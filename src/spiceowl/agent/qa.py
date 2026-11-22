"""Native Q&A registration and durable local request records.

Only the owning Controller starts inference or recovers unfinished requests.
The native runtime owns authentication; this module never reads credentials.
"""

import fcntl

from spiceowl.domain.common import DomainError, digest, require_id, utcnow
from spiceowl.storage.sqlite import Store


PROFILE_FIELDS = ("schema", "agent_id", "label", "executable", "model")
ACTIVE = {"queued", "running", "outcome_unknown"}


def _text(value, limit=160):
    return value[:limit] if isinstance(value, str) else None


def agent_summary(record):
    raw = record.get("last_probe")
    probe = None
    if isinstance(raw, dict):
        probe = {key: _text(raw.get(key)) for key in ("transport", "authentication", "model", "provider", "inference_access", "observed_at")}
        if raw.get("error"):
            probe["error"] = {"code": "native_probe_incomplete", "message": "Native readiness check did not complete."}
    return {"agent_id": record["agent_id"], "label": record["label"], "model": record.get("model"),
            "backend": "codex_native", "synthetic": False, "last_probe": probe}


def question_summary(record):
    return {key: record.get(key) for key in ("qa_id", "agent_id", "question", "status", "created_at", "updated_at",
            "answer", "model", "provider", "native_thread_id", "native_turn_id", "usage", "error", "synthetic")}


class NativeRegistry:
    def __init__(self, directory):
        self.store = Store(directory)

    def add(self, data):
        from spiceowl.agent.native import validate_agent_profile
        profile = validate_agent_profile(data)
        record = profile | {"created_at": utcnow(), "synthetic": False, "last_probe": None}
        self.store.create("agent_profile", profile["agent_id"], record)
        return agent_summary(record)

    def inspect(self, agent_id):
        return self.store.get("agent_profile", require_id(agent_id, "agent ID"))

    def list(self):
        return [agent_summary(r) for r in self.store.list("agent_profile")]

    def probe(self, agent_id):
        from spiceowl.agent.native import probe_agent
        record = self.inspect(agent_id)
        with (self.store.directory / f".native-probe-{agent_id}.lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise DomainError("This agent already has a readiness check in progress.", "busy") from None
            profile = {key: record[key] for key in PROFILE_FIELDS}
            result = probe_agent(profile, self.store.directory)
            result = result | {"observed_at": utcnow(), "inference_access": "unverified"}
            self.store.update("agent_profile", agent_id, lambda value: value | {"last_probe": result})
            return result


class Questions:
    def __init__(self, store):
        self.store = store

    def recover(self):
        """Called only by a Controller holding its exclusive owner lock."""
        with self.store.transaction() as db:
            rows = db.execute("SELECT id FROM records WHERE kind='qa'").fetchall()
            for row in rows:
                value = self.store._get(db, "qa", row[0])
                if value["status"] in {"queued", "running"}:
                    self.store._put(db, "qa", row[0], value | {"status": "outcome_unknown", "updated_at": utcnow(),
                        "error": {"code": "controller_interrupted", "message": "The prior question has an unconfirmed outcome and will not be replayed."}})

    def claim(self, data):
        if not isinstance(data, dict) or set(data) != {"agent_id", "question", "request_key"}:
            raise DomainError("A question needs agent_id, question and request_key.")
        agent_id = require_id(data["agent_id"], "agent ID")
        key = require_id(data["request_key"], "request key")
        question = data["question"]
        if not isinstance(question, str) or not question.strip() or len(question) > 2000 or "\x00" in question:
            raise DomainError("Question must contain 1–2000 characters.")
        qa_id = "qa-" + digest(key)
        fingerprint = digest({"agent_id": agent_id, "question": question})
        with self.store.transaction() as db:
            row = db.execute("SELECT id FROM records WHERE kind='qa' AND id=?", (qa_id,)).fetchone()
            if row:
                existing = self.store._get(db, "qa", qa_id)
                if existing["request_digest"] != fingerprint:
                    raise DomainError("This request key belongs to a different question.", "conflict")
                return existing, False
            profile = self.store._get(db, "agent_profile", agent_id)
            rows = db.execute("SELECT id FROM records WHERE kind='qa'").fetchall()
            for row in rows:
                prior = self.store._get(db, "qa", row[0])
                if prior["agent_id"] == agent_id and prior["status"] in ACTIVE:
                    raise DomainError("This agent has an active or unresolved question. Inspect its result before sending another.", "unresolved_turn")
            record = {"qa_id": qa_id, "agent_id": agent_id, "question": question, "request_digest": fingerprint,
                      "request_key": key, "profile_snapshot": {key: profile[key] for key in PROFILE_FIELDS},
                      "created_at": utcnow(), "updated_at": utcnow(), "status": "queued", "synthetic": False,
                      "answer": "", "model": None, "provider": None, "native_thread_id": None,
                      "native_turn_id": None, "usage": None, "error": None}
            self.store._put(db, "qa", qa_id, record)
            return record, True

    def run(self, record):
        from spiceowl.agent.native import ask_agent
        qa_id = record["qa_id"]
        self.store.update("qa", qa_id, lambda value: value | {"status": "running", "updated_at": utcnow()})
        try:
            reply = ask_agent(record["profile_snapshot"], record["question"], self.store.directory)
            if not isinstance(reply, dict) or reply.get("status") not in {"completed", "failed", "outcome_unknown"}:
                raise ValueError("Invalid native terminal result")
            result = {"status": reply["status"], "answer": _text(reply.get("answer"), 32768) or "",
                      "model": _text(reply.get("model")), "provider": _text(reply.get("provider")),
                      "native_thread_id": _text(reply.get("native_thread_id")), "native_turn_id": _text(reply.get("native_turn_id")),
                      "usage": reply.get("usage"), "error": reply.get("error")}
            if result["status"] == "completed" and not result["answer"].strip():
                raise ValueError("Missing answer")
        except Exception:
            # A generic exception may occur after dispatch. Never silently resend.
            result = {"status": "outcome_unknown", "error": {"code": "native_outcome_unknown", "message": "The native response could not be confirmed; this request will not be retried."}}
        return self.store.update("qa", qa_id, lambda value: value | result | {"updated_at": utcnow()})

    def list(self):
        return [question_summary(value) for value in self.store.list("qa")[:50]]

    def get(self, qa_id):
        return question_summary(self.store.get("qa", require_id(qa_id, "question ID")))

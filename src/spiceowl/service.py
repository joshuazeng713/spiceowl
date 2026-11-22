"""Single-owner simulation controller, shared by the CLI and local web console."""

from concurrent.futures import ThreadPoolExecutor
import fcntl
import threading

from spiceowl import __version__
from spiceowl.domain.common import DomainError, digest, new_id, require_id, utcnow
from spiceowl.storage.sqlite import Store


def backend_config(record):
    """Keep storage metadata out of the strict provider configuration contract."""
    return {key: record[key] for key in ("connection_id", "label", "kind", "backend", "scenario", "enabled", "synthetic")}


class Controller:
    def __init__(self, directory):
        self.store = Store(directory)
        self._lock = (self.store.directory / ".controller.lock").open("a+")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._lock.close()
            raise DomainError("A controller already owns this state directory. Use its web console or choose a separate --data-dir.", "controller_busy") from None
        self._workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="spiceowl-fake")
        self._slots = threading.BoundedSemaphore(4)
        self._closing = threading.Event()
        self.store.recover_interrupted()
        from spiceowl.hosts import HostRegistry
        self.hosts = HostRegistry(directory)

    def close(self):
        self._closing.set()
        self._workers.shutdown(wait=True)
        fcntl.flock(self._lock, fcntl.LOCK_UN)
        self._lock.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def state(self):
        from spiceowl.adapters.executors.mock import list_scenarios
        return {"version": __version__, "mode": "read_only_connectivity", "connections": [c for c in self.store.list("connection") if not c.get("deleted")], "hosts": self.hosts.list(), "runs": self.store.list("run"), "sessions": self.store.list("session"), "scenarios": list_scenarios(), "capabilities": {"real_inference": False, "real_simulation": False, "dispatch": False, "synthetic_replay": True, "ssh_connectivity_probe": True, "mcp_local_queries": True}}

    def add_connection(self, data):
        from spiceowl.agent.connections.config import validate_connection
        connection = validate_connection(data)
        connection.update(revision=1, created_at=utcnow(), status=None)
        return self.store.create("connection", connection["connection_id"], connection)

    def change_connection(self, connection_id, data):
        require_id(connection_id)
        if not isinstance(data, dict) or set(data) != {"enabled"} or type(data["enabled"]) is not bool:
            raise DomainError("Only a boolean enabled field may be changed.")
        def change(current):
            if current.get("deleted"):
                raise DomainError("This connection has been removed.", "not_found")
            return current | data | {"revision": current["revision"] + 1}
        return self.store.update("connection", connection_id, change)

    def remove_connection(self, connection_id):
        require_id(connection_id)
        return self.store.update("connection", connection_id, lambda c: c | {"enabled": False, "deleted": True, "revision": c["revision"] + 1})

    def probe(self, connection_id, level):
        from spiceowl.agent.connections.backends.fake import probe_connection
        require_id(connection_id)
        def update(connection):
            if connection.get("deleted") or not connection["enabled"]:
                raise DomainError("Enable this connection before probing it.", "connection_disabled")
            status = probe_connection(backend_config(connection), level)
            return connection | {"status": status, "probed_at": utcnow()}
        return self.store.update("connection", connection_id, update)

    def seed_demo(self):
        from spiceowl.adapters.executors.mock import create_run
        from spiceowl.agent.connections.config import validate_connection
        # One transaction makes repeat clicks and interrupted onboarding safe.
        with self.store.transaction() as db:
            for kind in ("agent", "model"):
                cid = f"demo-{kind}"
                if not db.execute("SELECT 1 FROM records WHERE kind='connection' AND id=?", (cid,)).fetchone():
                    conn = validate_connection({"connection_id": cid, "label": f"Demo {kind.title()}", "kind": kind, "backend": f"fake_{kind}", "scenario": "success", "enabled": True})
                    conn.update(revision=1, created_at=utcnow(), status=None)
                    self.store._put(db, "connection", cid, conn)
            for scenario in ("disconnect", "oom", "completed", "target_miss"):
                rid = f"demo-{scenario}"
                if not db.execute("SELECT 1 FROM records WHERE kind='run' AND id=?", (rid,)).fetchone():
                    self.store._put(db, "run", rid, create_run(scenario, rid))
        return self.state()

    def advance(self, run_id):
        from spiceowl.adapters.executors.mock import advance_run
        require_id(run_id)
        return self.store.update("run", run_id, advance_run)

    def diagnose(self, data):
        from spiceowl.agent.diagnosis import build_evidence
        if not isinstance(data, dict) or set(data) != {"connection_id", "run_id", "question", "request_key"}:
            raise DomainError("Diagnosis requires connection_id, run_id, question and request_key.")
        for field in ("connection_id", "run_id", "request_key"):
            require_id(data[field], field)
        question = data["question"]
        if not isinstance(question, str) or not question.strip() or len(question) > 2000 or any(ord(c) < 32 and c not in "\n\t" for c in question):
            raise DomainError("Enter a question between 1 and 2000 characters.")
        if self._closing.is_set() or not self._slots.acquire(blocking=False):
            raise DomainError("The local diagnosis queue is full or closing.", "busy")
        try:
            run = self.store.get("run", data["run_id"])
            evidence = build_evidence(run)
            turn, created = self.store.claim_turn(data["request_key"], digest(data), data["connection_id"], data["run_id"], question, evidence)
            if created:
                self._workers.submit(self._diagnostic_worker, turn, run)
            else:
                self._slots.release()
            return {"session_id": turn["session_id"], "turn_id": turn["turn_id"], "created": created, "status": turn["status"]}
        except BaseException:
            self._slots.release()
            raise

    def _diagnostic_worker(self, turn, run):
        from spiceowl.agent.connections.backends.fake import generate_events
        from spiceowl.agent.diagnosis import validate_proposal
        from spiceowl.recovery.policy import evaluate_policy
        terminal = False
        try:
            for index, event in enumerate(generate_events(backend_config(turn["connection_snapshot"]), turn["evidence"], turn["question"])):
                if index > 100:
                    raise DomainError("The fake backend exceeded its event budget.", "event_limit")
                if self._closing.wait(0.08):
                    self.store.append_event(turn["turn_id"], "turn.terminal", {"status": "outcome_unknown", "reason": "controller_stopped"}, {"status": "outcome_unknown"})
                    return
                kind, payload = event["kind"], event["payload"]
                updates = {}
                if kind == "proposal.ready":
                    proposal = validate_proposal(payload, turn["evidence"])
                    decision = evaluate_policy(proposal, run)
                    payload = {"proposal": proposal, "decision": decision}
                    updates = payload
                elif kind == "usage.observed":
                    updates = {"usage": payload}
                elif kind == "turn.terminal":
                    status = payload.get("status")
                    if status not in ("completed", "failed", "outcome_unknown"):
                        raise DomainError("Unexpected fake terminal state.", "backend_protocol")
                    updates = {"status": status}
                    terminal = True
                elif kind not in ("text.delta", "permission.denied"):
                    raise DomainError("Unsupported fake event.", "backend_protocol")
                if not self.store.append_event(turn["turn_id"], kind, payload, updates):
                    return
            if not terminal:
                self.store.append_event(turn["turn_id"], "turn.terminal", {"status": "outcome_unknown", "reason": "missing_terminal_event"}, {"status": "outcome_unknown"})
        except Exception:
            # Raw exception details may contain provider content in later adapters.
            self.store.append_event(turn["turn_id"], "turn.terminal", {"status": "failed", "reason": "diagnostic_contract_error"}, {"status": "failed"})
        finally:
            self._slots.release()

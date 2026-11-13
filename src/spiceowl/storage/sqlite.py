"""Transactional local storage; all updates open a short independent connection."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3

from spiceowl.domain.common import DomainError, new_id, utcnow


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "spiceowl.sqlite3"
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise DomainError("This database version is not supported.", "storage_version")
            db.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(kind,id))")
            db.execute("CREATE TABLE IF NOT EXISTS turns (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, request_key TEXT UNIQUE NOT NULL, request_digest TEXT NOT NULL, connection_id TEXT NOT NULL, run_id TEXT NOT NULL, status TEXT NOT NULL, body TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS turns_by_session ON turns(session_id)")
            db.execute("CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL, session_id TEXT NOT NULL, turn_id TEXT NOT NULL REFERENCES turns(id), kind TEXT NOT NULL, body TEXT NOT NULL, observed_at TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS events_by_session ON events(session_id,sequence)")
            db.execute("PRAGMA user_version=1")
        os.chmod(self.path, 0o600)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _get(db, kind, identifier):
        row = db.execute("SELECT body FROM records WHERE kind=? AND id=?", (kind, identifier)).fetchone()
        if row is None:
            raise DomainError("The requested record does not exist.", "not_found")
        return json.loads(row[0])

    @staticmethod
    def _put(db, kind, identifier, value):
        db.execute("INSERT INTO records(kind,id,body) VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body", (kind, identifier, encode(value)))

    def get(self, kind, identifier):
        with self.transaction() as db:
            return self._get(db, kind, identifier)

    def list(self, kind):
        with self.transaction() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT body FROM records WHERE kind=? ORDER BY rowid DESC", (kind,))]

    def create(self, kind, identifier, value):
        with self.transaction() as db:
            try:
                db.execute("INSERT INTO records(kind,id,body) VALUES(?,?,?)", (kind, identifier, encode(value)))
            except sqlite3.IntegrityError:
                raise DomainError("This identifier is already registered.", "conflict") from None
        return value

    def update(self, kind, identifier, transform):
        with self.transaction() as db:
            value = transform(self._get(db, kind, identifier))
            self._put(db, kind, identifier, value)
        return value

    @staticmethod
    def _event(db, session_id, turn_id, kind, payload):
        db.execute("INSERT INTO events(event_id,session_id,turn_id,kind,body,observed_at) VALUES(?,?,?,?,?,?)", (new_id("event"), session_id, turn_id, kind, encode(payload), utcnow()))

    def claim_turn(self, request_key, request_digest, connection_id, run_id, question, evidence):
        with self.transaction() as db:
            prior = db.execute("SELECT body,request_digest FROM turns WHERE request_key=?", (request_key,)).fetchone()
            if prior:
                if prior[1] != request_digest:
                    raise DomainError("This request key belongs to different input.", "conflict")
                return json.loads(prior[0]), False
            connection = self._get(db, "connection", connection_id)
            if connection.get("deleted") or not connection["enabled"]:
                raise DomainError("This connection is disabled.", "connection_disabled")
            busy = db.execute("SELECT 1 FROM turns WHERE (connection_id=? OR run_id=?) AND status IN ('dispatching','running','outcome_unknown')", (connection_id, run_id)).fetchone()
            if busy:
                raise DomainError("An active or unresolved diagnosis already uses this connection or run. Inspect its history first.", "unresolved_turn")
            session_id, turn_id = new_id("session"), new_id("turn")
            session = {"session_id": session_id, "connection_id": connection_id, "connection_label": connection["label"], "run_id": run_id, "created_at": utcnow(), "synthetic": True}
            turn = {"turn_id": turn_id, "session_id": session_id, "request_key": request_key, "connection_id": connection_id, "connection_snapshot": connection, "run_id": run_id, "question": question, "status": "dispatching", "created_at": utcnow(), "evidence": evidence, "synthetic": True, "usage": {"cost": None}, "proposal": None, "decision": None}
            self._put(db, "session", session_id, session)
            db.execute("INSERT INTO turns VALUES(?,?,?,?,?,?,?,?)", (turn_id, session_id, request_key, request_digest, connection_id, run_id, "dispatching", encode(turn)))
            self._event(db, session_id, turn_id, "turn.accepted", {"status": "dispatching", "synthetic": True})
            return turn, True

    def get_turn(self, turn_id):
        with self.transaction() as db:
            row = db.execute("SELECT body FROM turns WHERE id=?", (turn_id,)).fetchone()
            if not row:
                raise DomainError("The requested turn does not exist.", "not_found")
            return json.loads(row[0])

    def append_event(self, turn_id, kind, payload, updates=None):
        with self.transaction() as db:
            row = db.execute("SELECT body FROM turns WHERE id=?", (turn_id,)).fetchone()
            if row is None:
                raise DomainError("The requested turn does not exist.", "not_found")
            turn = json.loads(row[0])
            if turn["status"] not in ("dispatching", "running"):
                return False
            turn.update(updates or {})
            if "status" not in (updates or {}):
                turn["status"] = "running"
            turn["updated_at"] = utcnow()
            db.execute("UPDATE turns SET status=?,body=? WHERE id=?", (turn["status"], encode(turn), turn_id))
            self._event(db, turn["session_id"], turn_id, kind, payload)
            return True

    def cancel_turn(self, turn_id):
        # Fakes have no remote effects: a transaction can confirm local cancellation.
        self.append_event(turn_id, "turn.terminal", {"status": "cancelled", "synthetic": True, "scope": "diagnosis_only"}, {"status": "cancelled"})
        return self.get_turn(turn_id)

    def recover_interrupted(self):
        # Call only while holding the exclusive controller lock.
        with self.transaction() as db:
            rows = db.execute("SELECT body FROM turns WHERE status IN ('dispatching','running')").fetchall()
            for row in rows:
                turn = json.loads(row[0])
                turn.update(status="outcome_unknown", updated_at=utcnow())
                db.execute("UPDATE turns SET status=?,body=? WHERE id=?", (turn["status"], encode(turn), turn["turn_id"]))
                self._event(db, turn["session_id"], turn["turn_id"], "turn.terminal", {"status": "outcome_unknown", "reason": "controller_interrupted", "replayed": False})
            return len(rows)

    def session(self, session_id, after=0):
        with self.transaction() as db:
            session = self._get(db, "session", session_id)
            turns = [json.loads(row[0]) for row in db.execute("SELECT body FROM turns WHERE session_id=? ORDER BY rowid", (session_id,))]
            rows = db.execute("SELECT * FROM events WHERE session_id=? AND sequence>? ORDER BY sequence LIMIT 500", (session_id, after)).fetchall()
            events = [{"event_id": r["event_id"], "sequence": r["sequence"], "session_id": r["session_id"], "turn_id": r["turn_id"], "kind": r["kind"], "payload": json.loads(r["body"]), "observed_at": r["observed_at"]} for r in rows]
            return {"session": session, "turns": turns, "events": events, "next_cursor": events[-1]["sequence"] if events else after}

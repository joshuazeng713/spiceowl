"""Bounded reads of existing state, without Store or controller startup effects."""

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

from spiceowl.domain.common import DomainError, require_id


MAX_RECORDS = 25
MAX_RECORD_BYTES = 1_048_576
_KINDS = frozenset({"run", "connection", "host", "session"})
_TURN_STATES = frozenset({
    "dispatching", "running", "completed", "failed", "cancelled", "outcome_unknown"
})


class ReadStore:
    """Open version-1 state with mode=ro and release each read snapshot promptly.

    There is no migration, mkdir, chmod, write transaction or controller lock.
    immutable=1 is deliberately not used because it could hide live controller
    updates. The current Store uses
    rollback journals. WAL support is rejected here to avoid creating sidecars
    when a controller closes or checkpoints between a check and a connection.
    """

    def __init__(self, directory):
        try:
            self.directory = Path(directory).expanduser().resolve()
        except (TypeError, ValueError, OSError, RuntimeError):
            raise DomainError("Select an existing SpiceOwl data directory.", "storage_missing") from None
        self.path = self.directory / "spiceowl.sqlite3"
        self._uri = self.path.as_uri() + "?mode=ro"
        with self.transaction():
            pass

    def _check_file(self):
        try:
            with self.path.open("rb") as stream:
                header = stream.read(20)
        except (OSError, ValueError):
            raise DomainError(
                "Existing SpiceOwl state was not found or is unreadable. "
                "Select the data directory used by the local console; MCP does not create state.",
                "storage_missing",
            ) from None
        if header[:16] != b"SQLite format 3\x00":
            raise DomainError("SpiceOwl state is not a valid SQLite database.", "storage_invalid")
        if header[18:20] != b"\x01\x01":
            raise DomainError(
                "This read-only view requires the version-1 rollback-journal store. "
                "WAL state is not supported by this slice.", "storage_mode",
            )

    @contextmanager
    def transaction(self):
        """A fresh short read transaction; never BEGIN IMMEDIATE or immutable."""
        self._check_file()
        db = None
        try:
            db = sqlite3.connect(self._uri, uri=True, timeout=1, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA trusted_schema=OFF")
            ticks = 0

            def limit_work():
                nonlocal ticks
                ticks += 1
                return int(ticks > 1000)

            db.set_progress_handler(limit_work, 1000)
            db.execute("BEGIN")
            if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise DomainError("This database version is not supported by the read-only view.", "storage_version")
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name IN ('records','turns','events')"
            )}
            if tables != {"records", "turns", "events"}:
                raise DomainError("SpiceOwl state has an unsupported schema.", "storage_invalid")
            yield db
        except sqlite3.Error:
            raise DomainError(
                "Cannot read the selected state snapshot. It may be busy, invalid, or unreadable.",
                "storage_read",
            ) from None
        finally:
            if db is not None:
                try:
                    db.rollback()
                finally:
                    db.close()

    @staticmethod
    def _kind(kind):
        if not isinstance(kind, str) or kind not in _KINDS:
            raise DomainError("Unsupported stored record kind.", "invalid_request")

    @staticmethod
    def _decode(body):
        def reject_constant(_):
            raise ValueError("nonfinite JSON")

        try:
            value = json.loads(body, parse_constant=reject_constant)
        except (TypeError, ValueError, RecursionError):
            raise DomainError("A stored record is invalid or exceeds the read limit.", "storage_record") from None
        if not isinstance(value, dict):
            raise DomainError("A stored record is not an object.", "storage_record")
        return value

    def get(self, kind, identifier):
        """Internal raw record read. MCP must use its separate allowlisted view."""
        self._kind(kind)
        require_id(identifier)
        with self.transaction() as db:
            row = db.execute(
                "SELECT CASE WHEN length(CAST(body AS BLOB))<=? THEN body END "
                "FROM records WHERE kind=? AND id=?",
                (MAX_RECORD_BYTES, kind, identifier),
            ).fetchone()
            if row is None:
                raise DomainError("The requested record does not exist.", "not_found")
            return self._decode(row[0])

    def list(self, kind, limit=MAX_RECORDS):
        """Newest stored records, with an explicit truncation indicator."""
        self._kind(kind)
        if type(limit) is not int or not 1 <= limit <= MAX_RECORDS:
            raise DomainError("Invalid read limit.", "invalid_request")
        with self.transaction() as db:
            rows = db.execute(
                "SELECT CASE WHEN length(CAST(body AS BLOB))<=? THEN body END "
                "FROM records WHERE kind=? ORDER BY rowid DESC LIMIT ?",
                (MAX_RECORD_BYTES, kind, limit + 1),
            ).fetchall()
            return {"records": [self._decode(row[0]) for row in rows[:limit]],
                    "truncated": len(rows) > limit, "limit": limit}

    def counts(self):
        """Count stored rows in one snapshot, without reading turn bodies."""
        with self.transaction() as db:
            counts = {kind: db.execute("SELECT count(*) FROM records WHERE kind=?", (kind,)).fetchone()[0]
                      for kind in sorted(_KINDS)}
            counts["turn"] = db.execute("SELECT count(*) FROM turns").fetchone()[0]
            counts["event"] = db.execute("SELECT count(*) FROM events").fetchone()[0]
            states = {state: 0 for state in sorted(_TURN_STATES)}
            states["other"] = 0
            for row in db.execute("SELECT status,count(*) FROM turns GROUP BY status"):
                states[row[0] if row[0] in _TURN_STATES else "other"] += row[1]
            return {"records": counts, "turn_states": states}

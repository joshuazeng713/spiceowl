"""Incremental log streaming with rotation/truncation detection (M1).

Avoids re-reading hundreds of megabytes of overnight simulator logs on every
poll by persisting a byte-offset cursor with inode and file-header fingerprint.
When a simulator or log daemon rotates/truncates ``spectre.out`` / ``spectre.log``,
the watcher emits an explicit ``log_rotated`` event, preserves strictly monotonic
sequence IDs without duplicating earlier lines, and continues reading from the
start of the new file — natively across macOS, Linux, and Windows.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import uuid

from spiceowl.domain.common import DomainError, utcnow


LOG_CURSOR_SCHEMA = "spiceowl.log-cursor.v1"
HEAD_PREFIX_BYTES = 512
DEFAULT_MAX_CHUNK_BYTES = 256 * 1024


def _atomic_write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def empty_cursor(log_path: str | Path) -> dict:
    return {
        "schema": LOG_CURSOR_SCHEMA,
        "path": str(Path(log_path)),
        "inode": None,
        "byte_offset": 0,
        "head_len": 0,
        "head_sha256": None,
        "partial_line": "",
        "next_seq": 1,
        "rotations_detected": 0,
        "updated_at": utcnow(),
    }


def poll_log_increment(
    log_path: str | Path,
    cursor: dict | None = None,
    *,
    max_chunk_bytes: int = DEFAULT_MAX_CHUNK_BYTES,
) -> dict:
    """Read only newly appended log lines since ``cursor``, detecting rotation/truncation.

    Returns ``{"events": [...], "cursor": updated_cursor, "rotated": bool, "eof": bool}``.
    """
    if type(max_chunk_bytes) is not int or not 1024 <= max_chunk_bytes <= 8 * 1024 * 1024:
        raise DomainError("max_chunk_bytes must be between 1 KiB and 8 MiB.", "invalid_log_poll")
    path = Path(log_path)
    cur = dict(empty_cursor(path) if cursor is None else cursor)
    cur["path"] = str(path)

    if path.is_symlink():
        raise DomainError("Log file cannot be a symlink.", "unsafe_log_path")
    if not path.is_file():
        return {"events": [], "cursor": cur, "rotated": False, "eof": True, "missing": True}

    st = path.stat()
    size = int(st.st_size)
    inode = int(getattr(st, "st_ino", 0) or 0)
    prev_offset = int(cur.get("byte_offset") or 0)
    prev_inode = cur.get("inode")
    prev_head_len = int(cur.get("head_len") or 0)
    prev_head_sha = cur.get("head_sha256")
    next_seq = int(cur.get("next_seq") or 1)
    rotations = int(cur.get("rotations_detected") or 0)

    events: list[dict] = []
    rotated = False
    rotation_reason = None

    with path.open("rb") as fh:
        current_head = fh.read(HEAD_PREFIX_BYTES)
        if prev_offset > 0 or prev_head_sha is not None:
            if size < prev_offset:
                rotated = True
                rotation_reason = "file_truncated"
            elif prev_inode and inode and inode != prev_inode:
                rotated = True
                rotation_reason = "inode_changed"
            elif prev_head_sha and prev_head_len > 0:
                compare_slice = current_head[:prev_head_len]
                if len(compare_slice) < prev_head_len or _sha256_hex(compare_slice) != prev_head_sha:
                    rotated = True
                    rotation_reason = "header_fingerprint_changed"

        if rotated:
            rotations += 1
            events.append({
                "seq": next_seq,
                "kind": "log_rotated",
                "reason": rotation_reason,
                "previous_offset": prev_offset,
                "previous_head_sha256": prev_head_sha,
                "new_size": size,
                "observed_at": utcnow(),
            })
            next_seq += 1
            prev_offset = 0
            cur["partial_line"] = ""

        # Refresh head fingerprint once file has content
        if current_head:
            cur["head_len"] = len(current_head)
            cur["head_sha256"] = _sha256_hex(current_head)
        else:
            cur["head_len"] = 0
            cur["head_sha256"] = None

        fh.seek(prev_offset)
        chunk = fh.read(max_chunk_bytes)
        new_offset = prev_offset + len(chunk)
        eof = new_offset >= size

    if chunk:
        text = cur.get("partial_line", "") + chunk.decode("utf-8", errors="replace").replace("\r\n", "\n")
        parts = text.split("\n")
        # Keep trailing incomplete line buffered until newline arrives
        cur["partial_line"] = parts.pop() if not text.endswith("\n") else ""
        if text.endswith("\n") and parts and parts[-1] == "":
            parts.pop()
        for line in parts:
            events.append({
                "seq": next_seq,
                "kind": "log_line",
                "text": line,
                "line_sha256": _sha256_hex(line.encode("utf-8")),
                "observed_at": utcnow(),
            })
            next_seq += 1

    cur.update({
        "inode": inode or None,
        "byte_offset": new_offset,
        "next_seq": next_seq,
        "rotations_detected": rotations,
        "updated_at": utcnow(),
    })
    return {
        "events": events,
        "cursor": cur,
        "rotated": rotated,
        "eof": eof,
        "missing": False,
        "bytes_read": len(chunk),
    }


class IncrementalLogWatcher:
    """Restart-safe incremental log reader persisting cursors per run ID."""

    def __init__(self, state_dir: str | Path):
        self.root = Path(state_dir).expanduser().resolve() / "log_cursors"

    def _cursor_path(self, stream_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(stream_id))[:96]
        return self.root / f"{safe}.json"

    def load_cursor(self, stream_id: str, log_path: str | Path) -> dict:
        cpath = self._cursor_path(stream_id)
        if cpath.is_file() and not cpath.is_symlink():
            try:
                data = json.loads(cpath.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("schema") == LOG_CURSOR_SCHEMA:
                    return data
            except (OSError, ValueError):
                pass
        return empty_cursor(log_path)

    def poll(self, stream_id: str, log_path: str | Path, *, max_chunk_bytes: int = DEFAULT_MAX_CHUNK_BYTES) -> dict:
        cur = self.load_cursor(stream_id, log_path)
        res = poll_log_increment(log_path, cur, max_chunk_bytes=max_chunk_bytes)
        _atomic_write_json(self._cursor_path(stream_id), res["cursor"])
        return res

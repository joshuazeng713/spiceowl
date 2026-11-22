"""Small shared primitives for the offline prototype."""

from datetime import datetime, timezone
import hashlib
import json
import re
from uuid import uuid4


class DomainError(ValueError):
    """A safe, user-facing application error; do not put raw inputs in messages."""

    def __init__(self, message: str, code: str = "invalid_request"):
        super().__init__(message)
        self.code = code


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def require_id(value: object, name: str = "identifier") -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", value):
        raise DomainError(f"Invalid {name}.")
    return value

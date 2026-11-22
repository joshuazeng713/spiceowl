"""Strict private host profiles and explicit connectivity-only probes.

Importing or validating a profile performs no IO. Callers persist profiles and
probe observations; this module never reads profile, SSH, key, or model files.
Stored registry metadata must be projected out before passing a profile here.
"""

import re

from spiceowl.domain.common import DomainError, require_id
from spiceowl.adapters.simulators.probes import validate_tool


_FIELDS = {"schema", "host_id", "label", "ssh_target", "expected_host", "expected_user", "tools"}
_ALIAS = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", re.ASCII)
_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,252}", re.ASCII)
_USER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}", re.ASCII)


def _identifier(value, pattern, name):
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise DomainError(f"Invalid {name}.", "invalid_host_profile")
    if ".." in value or value.endswith("."):
        raise DomainError(f"Invalid {name}.", "invalid_host_profile")
    return value


def validate_host_profile(data) -> dict:
    """Validate a seven-field profile, returning a fresh JSON-compatible copy.

    ``ssh_target`` is one existing SSH alias, not a user@host, URL, option,
    forwarding rule or command. At most one entry for each supported tool is
    allowed. An empty tool list permits an identity-only connectivity probe.
    """
    if not isinstance(data, dict) or data.keys() != _FIELDS:
        raise DomainError("Invalid host profile fields.", "invalid_host_profile")
    if data["schema"] != "spiceowl.host.v1":
        raise DomainError("Unsupported host profile schema.", "invalid_host_profile")
    host_id = require_id(data["host_id"], "host ID")
    label = data["label"]
    if not isinstance(label, str) or not 1 <= len(label) <= 80 or not label.isprintable() or not label.strip():
        raise DomainError("Host label must contain 1–80 printable characters.", "invalid_host_profile")
    tools = data["tools"]
    if not isinstance(tools, list) or len(tools) > 3:
        raise DomainError("Host tools must be a list of at most three entries.", "invalid_host_profile")
    normalized = [validate_tool(tool) for tool in tools]
    if len({tool["kind"] for tool in normalized}) != len(normalized):
        raise DomainError("Duplicate host tool kind.", "invalid_host_profile")
    return {
        "schema": "spiceowl.host.v1",
        "host_id": host_id,
        "label": label,
        "ssh_target": _identifier(data["ssh_target"], _ALIAS, "SSH alias"),
        "expected_host": _identifier(data["expected_host"], _HOST, "expected hostname"),
        "expected_user": _identifier(data["expected_user"], _USER, "expected username"),
        "tools": normalized,
    }


def probe_host(profile) -> dict:
    """Actively probe a validated alias only when explicitly called.

    No retry, login flow, simulation, license check or job-state inference is
    performed. Registry locks and persistence are owned by the application.
    """
    from spiceowl.adapters.executors.ssh import probe_profile

    return probe_profile(validate_host_profile(profile))

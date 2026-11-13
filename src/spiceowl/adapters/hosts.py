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
_DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", re.ASCII)


def _identifier(value, pattern, name):
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise DomainError(f"Invalid {name}.", "invalid_host_profile")
    if ".." in value or value.endswith("."):
        raise DomainError(f"Invalid {name}.", "invalid_host_profile")
    return value


def validate_host_profile(data) -> dict:
    """Validate v1's seven fields or v2's explicit additional SSH hostname.

    ``ssh_target`` is one existing SSH alias, not a user@host, URL, option,
    forwarding rule or command. At most one entry for each supported tool is
    allowed. An empty tool list permits an identity-only connectivity probe.
    """
    if not isinstance(data, dict):
        raise DomainError("Invalid host profile fields.", "invalid_host_profile")
    schema = data.get("schema")
    if not isinstance(schema, str) or schema not in ("spiceowl.host.v1", "spiceowl.host.v2"):
        raise DomainError("Unsupported host profile schema.", "invalid_host_profile")
    fields = _FIELDS | {"ssh_hostname"} if schema == "spiceowl.host.v2" else _FIELDS
    if data.keys() != fields:
        raise DomainError("Invalid host profile fields.", "invalid_host_profile")
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
    profile = {
        "schema": schema,
        "host_id": host_id,
        "label": label,
        "ssh_target": _identifier(data["ssh_target"], _ALIAS, "SSH alias"),
        "expected_host": _identifier(data["expected_host"], _HOST, "expected hostname"),
        "expected_user": _identifier(data["expected_user"], _USER, "expected username"),
        "tools": normalized,
    }
    if schema == "spiceowl.host.v2":
        profile["ssh_hostname"] = _validate_ssh_hostname(data["ssh_hostname"])
    return profile


def _validate_ssh_hostname(value):
    """Accept an explicit ASCII DNS hostname, never an SSH option or URL."""
    if not isinstance(value, str) or not 1 <= len(value) <= 253:
        raise DomainError("Invalid SSH hostname.", "invalid_host_profile")
    if any(_DNS_LABEL.fullmatch(label) is None for label in value.split(".")):
        raise DomainError("Invalid SSH hostname.", "invalid_host_profile")
    return value


def probe_host(profile) -> dict:
    """Actively probe a validated alias only when explicitly called.

    No retry, login flow, simulation, license check or job-state inference is
    performed. Registry locks and persistence are owned by the application.
    """
    from spiceowl.adapters.executors.ssh import probe_profile

    return probe_profile(validate_host_profile(profile))


def probe_resources(profile) -> dict:
    """Explicitly sample Linux host aggregates, independently of tool versions.

    Results are observations, not reservations, job state, or launch readiness.
    This operation accepts no process selection or filesystem path argument.
    """
    from spiceowl.adapters.executors.ssh import probe_resource_profile

    return probe_resource_profile(validate_host_profile(profile))

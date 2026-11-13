"""Strict configuration for the two offline P0.1 backends."""

from spiceowl.domain.common import DomainError, new_id, require_id


SCENARIOS = frozenset({
    "success", "inference_denied", "disconnect", "permission_denied"
})
_FIELDS = frozenset({
    "connection_id", "label", "kind", "backend", "scenario", "enabled"
})
_BACKENDS = {"agent": "fake_agent", "model": "fake_model"}


def validate_connection(data: dict) -> dict:
    """Return a new normalized config; never resolve secrets or start anything.

    Persisted configs additionally contain ``synthetic``. That output marker is
    accepted by backend entry points, not by this strict configuration input.
    """
    if not isinstance(data, dict):
        raise DomainError("Connection must be an object.", "invalid_connection")
    if "example_only" in data:
        raise DomainError("Example-only drafts cannot be used as connections.", "example_only")
    if data.keys() - _FIELDS:
        raise DomainError("Unknown connection fields.", "invalid_connection")

    label = data.get("label")
    if (
        not isinstance(label, str)
        or not label.strip()
        or len(label) > 80
        or not label.isprintable()
    ):
        raise DomainError("Label must contain 1 to 80 printable characters.", "invalid_connection")

    kind = data.get("kind")
    if not isinstance(kind, str) or kind not in _BACKENDS:
        raise DomainError("Connection kind must be agent or model.", "invalid_connection")
    if data.get("backend") != _BACKENDS[kind]:
        raise DomainError("Only the matching fake backend is supported.", "unsupported_backend")

    scenario = data.get("scenario", "success")
    if not isinstance(scenario, str) or scenario not in SCENARIOS:
        raise DomainError("Unknown synthetic connection scenario.", "invalid_scenario")
    enabled = data.get("enabled", True)
    if type(enabled) is not bool:
        raise DomainError("Enabled must be a boolean.", "invalid_connection")

    # Validate everything before allocating an ID, including explicit null IDs.
    connection_id = (
        require_id(data["connection_id"], "connection ID")
        if "connection_id" in data else new_id("connection")
    )
    return {
        "connection_id": connection_id,
        "label": label,
        "kind": kind,
        "backend": _BACKENDS[kind],
        "scenario": scenario,
        "enabled": enabled,
        "synthetic": True,
    }


def _backend_connection(connection: dict, expected_kind: str | None = None) -> dict:
    """Accept config inputs or normalized outputs without changing the caller."""
    if not isinstance(connection, dict):
        raise DomainError("Connection must be an object.", "invalid_connection")
    data = dict(connection)
    if "synthetic" in data and data.pop("synthetic") is not True:
        raise DomainError("Only synthetic connections are supported.", "invalid_connection")
    normalized = validate_connection(data)
    if expected_kind is not None and normalized["kind"] != expected_kind:
        raise DomainError("Backend does not match connection kind.", "unsupported_backend")
    return normalized

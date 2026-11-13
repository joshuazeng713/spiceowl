"""Finite synthetic diagnostics with no I/O, model calls, or tool execution."""

from collections.abc import Iterator
import json
from typing import Literal

from spiceowl.domain.common import DomainError, new_id, require_id
from ..config import _backend_connection


_LEVELS = frozenset({"transport", "auth", "models"})
_DIMENSIONS = {
    "execution": frozenset({
        "prepared", "submitting", "queued", "running", "completed", "failed", "cancelled"
    }),
    "observation": frozenset({"current", "stale", "unknown"}),
    "measurement_validity": frozenset({"pending", "valid", "invalid", "indeterminate"}),
    "specification_result": frozenset({"not_evaluated", "pass", "fail"}),
}
_MAX_EVIDENCE_BYTES = 65_536
_MAX_EVIDENCE_EVENTS = 128


def _probe(connection: dict, level: str) -> dict:
    if not isinstance(level, str) or level not in _LEVELS:
        raise DomainError("Unknown metadata probe level.", "invalid_probe_level")
    return {
        "configuration": "valid",
        "transport": "reachable",
        "authentication": "unknown" if level == "transport" else "simulated_authenticated",
        "catalog": "advertised" if level == "models" else "unknown",
        "inference_access": "unverified",
        "isolation": "synthetic_only",
        "synthetic": True,
    }


def _evidence_snapshot(evidence: dict) -> dict:
    """Extract scalar facts and IDs; never interpret messages or execute facts."""
    if not isinstance(evidence, dict) or evidence.get("synthetic") is not True:
        raise DomainError("A synthetic evidence object is required.", "invalid_evidence")
    try:
        encoded = json.dumps(evidence, allow_nan=False, ensure_ascii=True)
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise DomainError("Evidence must be bounded JSON data.", "invalid_evidence") from None
    if len(encoded.encode("utf-8")) > _MAX_EVIDENCE_BYTES:
        raise DomainError("Evidence exceeds the synthetic size limit.", "invalid_evidence")

    snapshot = {
        "experiment_id": require_id(evidence.get("experiment_id"), "experiment ID"),
        "attempt_id": require_id(evidence.get("attempt_id"), "attempt ID"),
    }
    for name, choices in _DIMENSIONS.items():
        value = evidence.get(name)
        if not isinstance(value, str) or value not in choices:
            raise DomainError("Invalid evidence run dimension.", "invalid_evidence")
        snapshot[name] = value

    events = evidence.get("events")
    if not isinstance(events, list) or not 1 <= len(events) <= _MAX_EVIDENCE_EVENTS:
        raise DomainError("Evidence needs a bounded, nonempty event list.", "invalid_evidence")
    ids = []
    for event in events:
        if not isinstance(event, dict):
            raise DomainError("Evidence event must be an object.", "invalid_evidence")
        event_id = require_id(event.get("event_id"), "evidence event ID")
        if event_id in ids:
            raise DomainError("Duplicate evidence event ID.", "invalid_evidence")
        ids.append(event_id)
    snapshot["evidence_event_ids"] = ids
    return snapshot


def _prepare_stream(connection: dict, evidence: dict, question: str) -> Iterator[dict]:
    if not connection["enabled"]:
        raise DomainError("Connection is disabled.", "connection_disabled")
    if not isinstance(question, str) or len(question) > 2000:
        raise DomainError("Question must be text of at most 2000 characters.", "invalid_question")
    # Validate eagerly and keep an independent projection before returning the
    # iterator. Caller edits between pulls cannot change a pending proposal.
    snapshot = _evidence_snapshot(evidence)
    return _stream(connection["scenario"], snapshot)


def _event(kind: str, **payload: object) -> dict:
    return {"kind": kind, "payload": {**payload, "synthetic": True}}


def _usage() -> dict:
    return _event(
        "usage.observed", input_tokens=None, output_tokens=None,
        cost=None, currency=None, source="synthetic_fixture",
    )


def _proposal(snapshot: dict) -> dict:
    uncertainties = ["Synthetic evidence only; no simulator or model was run."]
    valid_miss = (
        snapshot["measurement_validity"] == "valid"
        and snapshot["specification_result"] == "fail"
    )
    if valid_miss:
        kind = "report"
        summary = "Preserve the synthetic valid measurement and its specification failure."
    elif snapshot["observation"] in {"stale", "unknown"}:
        kind = "reconcile"
        summary = "Suggest reconciling the same synthetic execution identity before any new attempt."
        uncertainties.append("Stale or unknown observation does not establish execution failure.")
    elif snapshot["execution"] in {"prepared", "submitting", "queued", "running"}:
        kind = "observe"
        summary = "Suggest observing the existing synthetic attempt without changing its inputs."
    else:
        kind = "report"
        summary = "Report the synthetic execution, measurement and specification outcomes separately."
    if snapshot["measurement_validity"] != "valid":
        uncertainties.append("Measurement validity is not established as valid in this snapshot.")
    return {
        "proposal_id": new_id("proposal"),
        "experiment_id": snapshot["experiment_id"],
        "attempt_id": snapshot["attempt_id"],
        "kind": kind,
        "summary": summary,
        "evidence_event_ids": list(snapshot["evidence_event_ids"]),
        "uncertainties": uncertainties,
        "diff": [],
        "synthetic": True,
    }


def _stream(scenario: str, snapshot: dict) -> Iterator[dict]:
    yield _event("text.delta", text="Synthetic diagnostic fixture; no model or simulator is running. ")
    if scenario == "inference_denied":
        yield _event("text.delta", text="Simulated inference access denied despite available metadata.")
        yield _usage()
        yield _event("turn.terminal", status="failed", cost=None)
        return
    if scenario == "disconnect":
        yield _event("text.delta", text="Partial synthetic response: the connection was lost before completion.")
        yield _usage()
        yield _event("turn.terminal", status="outcome_unknown", cost=None)
        return
    if scenario == "permission_denied":
        yield _event(
            "permission.denied", tool="shell", decision="deny",
            reason="Synthetic shell request denied; no command was executed.",
        )

    dimensions = "; ".join(f"{name}={snapshot[name]}" for name in _DIMENSIONS)
    yield _event("text.delta", text=f"Synthetic snapshot: {dimensions}.")
    yield _usage()
    yield {"kind": "proposal.ready", "payload": _proposal(snapshot)}
    yield _event("turn.terminal", status="completed", cost=None)


class FakeAgentBackend:
    """An offline agent-runtime fixture, distinct from a model API fixture."""

    @property
    def kind(self) -> Literal["agent"]:
        return "agent"

    def probe_connection(self, connection: dict, level: str) -> dict:
        return _probe(_backend_connection(connection, self.kind), level)

    def generate_events(self, connection: dict, evidence: dict, question: str) -> Iterator[dict]:
        return _prepare_stream(_backend_connection(connection, self.kind), evidence, question)


class FakeModelBackend:
    """An offline inference fixture; it does not own a native agent session."""

    @property
    def kind(self) -> Literal["model"]:
        return "model"

    def probe_connection(self, connection: dict, level: str) -> dict:
        return _probe(_backend_connection(connection, self.kind), level)

    def generate_events(self, connection: dict, evidence: dict, question: str) -> Iterator[dict]:
        return _prepare_stream(_backend_connection(connection, self.kind), evidence, question)


def probe_connection(connection: dict, level: str) -> dict:
    """Probe only simulated metadata; every scenario remains inference-unverified."""
    return _probe(_backend_connection(connection), level)


def generate_events(connection: dict, evidence: dict, question: str) -> Iterator[dict]:
    """Return a validated, finite synthetic stream with no side-effecting tools."""
    return _prepare_stream(_backend_connection(connection), evidence, question)

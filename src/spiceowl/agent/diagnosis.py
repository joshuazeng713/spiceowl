"""Bounded synthetic evidence and advisory proposals; no execution or file IO.

Only selected run fields are projected. Event facts are bounded JSON data, not
instructions, paths to open, or authorization. Proposal text remains inert prose;
there are no executable positions, command fields, or dynamic action lookups.
"""

import json
import math

from spiceowl.domain.common import DomainError, digest, require_id


MAX_EVENTS = 64
MAX_EVIDENCE_BYTES = 65_536
MAX_MESSAGE_LENGTH = 2000
MAX_FACT_STRING_LENGTH = 1024
MAX_JSON_DEPTH = 4
MAX_JSON_NODES = 256
MAX_CONTAINER_ITEMS = 32
MAX_DIFF_ITEMS = 8
MAX_UNCERTAINTIES = 16

_DIMENSIONS = {
    "execution": {"prepared", "submitting", "queued", "running", "completed", "failed", "cancelled"},
    "observation": {"current", "stale", "unknown"},
    "measurement_validity": {"pending", "valid", "invalid", "indeterminate"},
    "specification_result": {"not_evaluated", "pass", "fail"},
}
_EVIDENCE_FIELDS = {
    "experiment_id", "attempt_id", "execution_identity", "synthetic", "events",
    "evidence_digest", *_DIMENSIONS,
}
_PROPOSAL_FIELDS = {
    "proposal_id", "experiment_id", "attempt_id", "kind", "summary",
    "evidence_event_ids", "uncertainties", "diff", "synthetic",
}
_KINDS = {
    "report", "observe", "reconcile", "cancel_owned",
    "retry_with_resource_profile", "change_inputs",
}
_RUN_SCENARIOS = {"disconnect", "oom", "completed", "target_miss"}

# Advisory vocabulary only. Bounds are prototype input limits, not safe operating
# ranges or authorization. No value here is ever applied to a simulation.
_RESOURCE_FIELDS = {
    "memory_limit_mib": ("integer", 1, 1_048_576),
    "cpu_limit": ("integer", 1, 4096),
    "attempt_timeout_seconds": ("integer", 1, 604_800),
    "resource_profile": ("identifier", None, None),
}
_INPUT_FIELDS = {
    "reltol": ("positive_number", 0, 1),
    "stop_time_us": ("positive_number", 0, 1_000_000_000_000),
    "temperature_c": ("number", -273.15, 1000),
    "random_seed": ("integer", 0, 4_294_967_295),
    "corner": ("identifier", None, None),
}


def _invalid(message, code):
    raise DomainError(message, code)


def _text(value, name, maximum, code, *, empty=False):
    if type(value) is not str or len(value) > maximum or (not empty and not value.strip()):
        _invalid(f"Invalid or oversized {name}.", code)
    if any(not char.isprintable() and char not in "\n\t" for char in value):
        _invalid(f"Invalid control characters in {name}.", code)
    return value


def _id(value, name, code):
    if type(value) is not str:
        _invalid(f"Invalid {name}.", code)
    try:
        return require_id(value, name)
    except DomainError:
        _invalid(f"Invalid {name}.", code)


def _copy_json(value, code, *, depth=0, budget=None):
    """Copy a small JSON tree, rejecting cycles via a finite depth/node budget."""
    if budget is None:
        budget = [MAX_JSON_NODES]
    budget[0] -= 1
    if depth > MAX_JSON_DEPTH or budget[0] < 0:
        _invalid("Evidence data exceeds structural bounds.", code)
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if abs(value) > 9_007_199_254_740_991:
            _invalid("Evidence integer exceeds the supported range.", code)
        return value
    if type(value) is float:
        if not math.isfinite(value):
            _invalid("Evidence numbers must be finite.", code)
        return value
    if type(value) is str:
        return _text(value, "evidence text", MAX_FACT_STRING_LENGTH, code, empty=True)
    if type(value) not in (dict, list) or len(value) > MAX_CONTAINER_ITEMS:
        _invalid("Evidence must contain bounded JSON values.", code)
    if type(value) is list:
        return [_copy_json(item, code, depth=depth + 1, budget=budget) for item in value]
    result = {}
    for key, item in value.items():
        key = _text(key, "fact key", 64, code)
        result[key] = _copy_json(item, code, depth=depth + 1, budget=budget)
    return result


def _evidence_payload(source, *, snapshot=False):
    code = "invalid_evidence"
    if type(source) is not dict:
        _invalid("Evidence source must be a dictionary.", code)
    required = _EVIDENCE_FIELDS - {"evidence_digest"}
    if not required <= source.keys() or (snapshot and set(source) != _EVIDENCE_FIELDS):
        _invalid("Missing or unsupported evidence fields.", code)
    if source["synthetic"] is not True:
        _invalid("Only explicitly synthetic evidence is supported.", code)
    if not snapshot and "scenario" in source:
        scenario = source["scenario"]
        if type(scenario) is not str or scenario not in _RUN_SCENARIOS:
            _invalid("Unsupported synthetic run scenario.", code)

    result = {
        "experiment_id": _id(source["experiment_id"], "experiment ID", code),
        "attempt_id": _id(source["attempt_id"], "attempt ID", code),
        "synthetic": True,
    }
    identity = source["execution_identity"]
    if type(identity) is not dict or not identity:
        _invalid("Execution identity must be a nonempty dictionary.", code)
    result["execution_identity"] = _copy_json(identity, code)
    for field, choices in _DIMENSIONS.items():
        value = source[field]
        if type(value) is not str or value not in choices:
            _invalid("Invalid run dimension.", code)
        result[field] = value
    if result["measurement_validity"] != "valid" and result["specification_result"] != "not_evaluated":
        _invalid("A specification result requires valid measurements.", code)

    events = source["events"]
    if type(events) is not list or len(events) > MAX_EVENTS:
        _invalid("Events must be a bounded list.", code)
    seen = set()
    result["events"] = []
    for event in events:
        if type(event) is not dict or not {"event_id", "message", "facts"} <= event.keys():
            _invalid("Malformed evidence event.", code)
        if snapshot and set(event) != {"event_id", "message", "facts"}:
            _invalid("Unsupported snapshot event fields.", code)
        event_id = _id(event["event_id"], "event ID", code)
        if event_id in seen:
            _invalid("Duplicate evidence event ID.", code)
        seen.add(event_id)
        if type(event["facts"]) is not dict:
            _invalid("Event facts must be a dictionary.", code)
        result["events"].append({
            "event_id": event_id,
            "message": _text(event["message"], "event message", MAX_MESSAGE_LENGTH, code, empty=True),
            "facts": _copy_json(event["facts"], code),
        })
    sized_snapshot = {**result, "evidence_digest": "0" * 64}
    if len(json.dumps(sized_snapshot, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_EVIDENCE_BYTES:
        _invalid("Evidence exceeds the snapshot byte limit.", code)
    return result


def build_evidence(run):
    """Project at most 64 events/64 KiB; reject oversize input rather than truncate.

    Run metadata and event sequence/kind/timestamps stay outside this snapshot;
    only declared event_id/message/facts are copied. Nothing is read from paths.
    """
    result = _evidence_payload(run)
    result["evidence_digest"] = digest(result)
    return result


def _validate_diff_value(value, descriptor):
    value_type, lower, upper = descriptor
    code = "invalid_proposal"
    if value_type == "identifier":
        return _id(value, "diff identifier", code)
    expected_types = (int,) if value_type == "integer" else (int, float)
    if type(value) not in expected_types:
        _invalid("Diff values must match their declared numeric types.", code)
    if type(value) is float and not math.isfinite(value):
        _invalid("Diff numbers must be finite.", code)
    if value < lower or value > upper or (value_type == "positive_number" and value == 0):
        _invalid("Diff value is outside the supported range.", code)
    return value


def validate_proposal(proposal, evidence):
    """Validate a model-origin proposal before the service adds producer fields.

    Reports/observations/reconciliation/cancellation have empty diffs. Resource
    and input-change proposals need a nonempty diff from their respective typed
    allowlists. Unknown previous values may be null; proposed values may not.
    """
    code = "invalid_proposal"
    if type(proposal) is not dict or set(proposal) != _PROPOSAL_FIELDS:
        _invalid("Proposal must contain only the declared fields.", code)
    if proposal["synthetic"] is not True:
        _invalid("Proposal must be explicitly synthetic.", code)
    snapshot = _evidence_payload(evidence, snapshot=True)
    if type(evidence["evidence_digest"]) is not str or evidence["evidence_digest"] != digest(snapshot):
        _invalid("Evidence snapshot digest does not match its contents.", "invalid_evidence")

    result = {field: _id(proposal[field], field, code) for field in (
        "proposal_id", "experiment_id", "attempt_id"
    )}
    if any(result[field] != snapshot[field] for field in ("experiment_id", "attempt_id")):
        _invalid("Proposal target does not match this evidence snapshot.", code)
    kind = proposal["kind"]
    if type(kind) is not str or kind not in _KINDS:
        _invalid("Unsupported proposal kind.", code)
    result["kind"] = kind
    result["summary"] = _text(proposal["summary"], "summary", 2000, code)

    refs = proposal["evidence_event_ids"]
    if type(refs) is not list or not 1 <= len(refs) <= MAX_EVENTS:
        _invalid("Proposal requires a bounded nonempty evidence reference list.", code)
    refs = [_id(ref, "evidence event ID", code) for ref in refs]
    available = {event["event_id"] for event in snapshot["events"]}
    if len(set(refs)) != len(refs) or not set(refs) <= available:
        _invalid("Evidence references must be unique and resolve to this snapshot.", code)
    result["evidence_event_ids"] = refs

    uncertainties = proposal["uncertainties"]
    if type(uncertainties) is not list or len(uncertainties) > MAX_UNCERTAINTIES:
        _invalid("Uncertainties must be a bounded list.", code)
    result["uncertainties"] = [_text(item, "uncertainty", 1000, code) for item in uncertainties]

    changes = proposal["diff"]
    if type(changes) is not list or len(changes) > MAX_DIFF_ITEMS:
        _invalid("Diff must be a bounded list.", code)
    descriptors = _RESOURCE_FIELDS if kind == "retry_with_resource_profile" else (
        _INPUT_FIELDS if kind == "change_inputs" else {}
    )
    if bool(changes) != bool(descriptors):
        _invalid("Diff does not match the proposal kind.", code)
    result["diff"] = []
    seen = set()
    for change in changes:
        if type(change) is not dict or set(change) != {"field", "before", "after"}:
            _invalid("Each diff item must have only field, before and after.", code)
        field = change["field"]
        if type(field) is not str or field not in descriptors or field in seen:
            _invalid("Unsupported or duplicate diff field.", code)
        seen.add(field)
        before = None if change["before"] is None else _validate_diff_value(change["before"], descriptors[field])
        after = _validate_diff_value(change["after"], descriptors[field])
        if before == after:
            _invalid("Diff must describe a change.", code)
        result["diff"].append({"field": field, "before": before, "after": after})
    result["synthetic"] = True
    return result

"""Finite, explicit replay of original synthetic simulation observations.

These functions do no IO, sleep, submission, cancellation, or simulation. A
snapshot is the entire replay state, including after a JSON storage round trip.
Only snapshots emitted by this module are accepted by ``advance_run``; altered
identities, observations, or history cannot become a new source of authority.
"""

from copy import deepcopy
from datetime import datetime

from spiceowl.domain.common import DomainError, digest, require_id, utcnow


_SCENARIOS = (
    {
        "scenario": "disconnect",
        "label": "Synthetic disconnect and reconnect",
        "description": "Lose observation, reconnect to the same execution, then finish.",
    },
    {
        "scenario": "oom",
        "label": "Synthetic out-of-memory failure",
        "description": "Replay a confirmed memory failure with an incomplete measurement.",
    },
    {
        "scenario": "completed",
        "label": "Synthetic completed run",
        "description": "Finish with a valid measurement that meets its target.",
    },
    {
        "scenario": "target_miss",
        "label": "Synthetic valid target miss",
        "description": "Finish with a valid measurement that fails its design target.",
    },
)


def _frame(kind, message, progress, simulated_time_us, memory_mb, *,
           execution="running", observation="current",
           measurement_validity="pending", specification_result="not_evaluated",
           finished=False, facts=None):
    return {
        "execution": execution,
        "observation": observation,
        "measurement_validity": measurement_validity,
        "specification_result": specification_result,
        "progress": progress,
        "finished": finished,
        "resource": {"simulated_time_us": simulated_time_us, "memory_mb": memory_mb},
        "kind": kind,
        "message": message,
        "facts": {"synthetic": True, "source": "synthetic_replay", **(facts or {})},
    }


_INITIAL = _frame(
    "execution.observed", "Synthetic replay begins with a running observation.",
    0.2, 2.0, 64.0,
)
_PROGRESS = _frame(
    "execution.observed", "Synthetic execution advances to the measurement window.",
    0.6, 6.0, 96.0,
)
_COMPLETED = _frame(
    "execution.completed", "Synthetic execution completed; the valid measurement passes.",
    1.0, 10.0, 96.0, execution="completed", measurement_validity="valid",
    specification_result="pass", finished=True,
    facts={"analysis_completed": True, "required_signals_present": True,
           "measurement_window_covered": True, "settling_time_us": 4.0,
           "settling_target_us": 5.0},
)
_FRAMES = {
    "disconnect": (
        _INITIAL,
        _frame(
            "observation.disconnected",
            "Synthetic observation was lost; the last confirmed execution remains running.",
            0.2, None, None, observation="unknown",
            facts={"connection_lost": True, "last_confirmed_execution": "running",
                   "execution_failure_confirmed": False},
        ),
        _frame(
            "execution.reconciled",
            "Synthetic observation reconnected to the same execution identity.",
            0.6, 6.0, 96.0,
            facts={"identity_match": True, "new_attempt_created": False},
        ),
        _COMPLETED,
    ),
    "oom": (
        _INITIAL,
        _frame("execution.observed", "Synthetic memory use approaches its limit.",
               0.5, 5.0, 240.0),
        _frame(
            "execution.failed",
            "Synthetic executor evidence confirms an out-of-memory failure; measurement is incomplete.",
            0.5, None, None, execution="failed", measurement_validity="invalid",
            finished=True,
            facts={"reason": "OUT_OF_MEMORY", "execution_failure_confirmed": True,
                   "memory_limit_mb": 256.0, "last_observed_memory_mb": 240.0,
                   "last_confirmed_simulated_time_us": 5.0,
                   "required_window_end_us": 10.0, "measurement_window_covered": False},
        ),
    ),
    "completed": (_INITIAL, _PROGRESS, _COMPLETED),
    "target_miss": (
        _INITIAL,
        _PROGRESS,
        _frame(
            "execution.completed",
            "Synthetic execution completed; the valid measurement misses its design target.",
            1.0, 10.0, 96.0, execution="completed", measurement_validity="valid",
            specification_result="fail", finished=True,
            facts={"analysis_completed": True, "required_signals_present": True,
                   "measurement_window_covered": True, "settling_time_us": 6.0,
                   "settling_target_us": 5.0},
        ),
    ),
}
_RUN_FIELDS = frozenset({
    "run_id", "experiment_id", "attempt_id", "submission_key", "execution_identity",
    "label", "scenario", "synthetic", "created_at", "updated_at", "cursor",
    "execution", "observation", "measurement_validity", "specification_result",
    "progress", "resource_history", "events", "finished",
})


def list_scenarios() -> list[dict]:
    """Return an independent copy of the four public scenario descriptions."""
    return deepcopy(list(_SCENARIOS))


def _require_scenario(scenario):
    if not isinstance(scenario, str) or scenario not in _FRAMES:
        raise DomainError("Unknown synthetic scenario.", "invalid_scenario")
    return scenario


def _scoped_id(prefix, run_id, sequence=None):
    return f"{prefix}-{digest([prefix, run_id, sequence])}"


def _snapshot(scenario, run_id, cursor, event_times):
    frames = _FRAMES[scenario][:cursor + 1]
    last = frames[-1]
    events = []
    resources = []
    for sequence, (frame, observed_at) in enumerate(zip(frames, event_times), 1):
        events.append({
            "event_id": _scoped_id("event", run_id, sequence),
            "sequence": sequence,
            "kind": frame["kind"],
            "observed_at": observed_at,
            "message": frame["message"],
            "facts": deepcopy(frame["facts"]),
        })
        resources.append({"sequence": sequence, **frame["resource"]})
    return {
        "run_id": run_id,
        "experiment_id": _scoped_id("experiment", run_id),
        "attempt_id": _scoped_id("attempt", run_id),
        "submission_key": _scoped_id("submission", run_id),
        "execution_identity": {
            "kind": "synthetic",
            "executor": "mock",
            "native_id": _scoped_id("execution", run_id),
            "synthetic": True,
        },
        "label": next(item["label"] for item in _SCENARIOS if item["scenario"] == scenario),
        "scenario": scenario,
        "synthetic": True,
        "created_at": event_times[0],
        "updated_at": event_times[-1],
        "cursor": cursor,
        **{name: last[name] for name in (
            "execution", "observation", "measurement_validity",
            "specification_result", "progress", "finished",
        )},
        "resource_history": resources,
        "events": events,
    }


def create_run(scenario, run_id) -> dict:
    """Create replay state; the synthetic submission key represents no real job."""
    scenario = _require_scenario(scenario)
    run_id = require_id(run_id, "run ID")
    return _snapshot(scenario, run_id, 0, [utcnow()])


def _is_timestamp(value):
    if not isinstance(value, str) or len(value) > 64:
        return False
    try:
        return datetime.fromisoformat(value).utcoffset() is not None
    except (ValueError, OverflowError):
        return False


def _matches_json(actual, expected):
    """Compare bounded expected shapes, distinguishing booleans from numbers."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _matches_json(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _matches_json(left, right) for left, right in zip(actual, expected)
        )
    return actual == expected


def _validate_snapshot(run):
    if not isinstance(run, dict) or run.keys() != _RUN_FIELDS:
        raise DomainError("Invalid synthetic run fields.", "invalid_run")
    scenario = _require_scenario(run["scenario"])
    run_id = require_id(run["run_id"], "run ID")
    cursor = run["cursor"]
    if type(cursor) is not int or not 0 <= cursor < len(_FRAMES[scenario]):
        raise DomainError("Invalid synthetic replay cursor.", "invalid_run")
    events = run["events"]
    if not isinstance(events, list) or len(events) != cursor + 1:
        raise DomainError("Invalid synthetic event history.", "invalid_run")
    event_times = []
    for event in events:
        if not isinstance(event, dict) or not _is_timestamp(event.get("observed_at")):
            raise DomainError("Invalid synthetic event timestamp.", "invalid_run")
        event_times.append(event["observed_at"])
    expected = _snapshot(scenario, run_id, cursor, event_times)
    if not _matches_json(run, expected):
        raise DomainError("Synthetic run does not match its replay snapshot.", "invalid_run")
    return event_times


def advance_run(run) -> dict:
    """Return the next snapshot on explicit replay; terminal snapshots are unchanged.

    Replaying the same snapshot yields the same cursor, event IDs and facts.
    Only the new observation timestamp may differ. Existing events retain their
    original timestamps; a caller can deduplicate newly persisted events by ID.
    """
    event_times = _validate_snapshot(run)
    if run["finished"]:
        return deepcopy(run)
    return _snapshot(run["scenario"], run["run_id"], run["cursor"] + 1,
                     event_times + [utcnow()])

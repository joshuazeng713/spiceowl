"""Fixed model-facing projections; never expose raw database records."""

from datetime import datetime
import math
import re

from spiceowl import __version__
from spiceowl.domain.common import DomainError, require_id, utcnow
from spiceowl.storage.readonly import MAX_RECORDS
from spiceowl.domain.connectivity import resource_summary


MAX_EVENTS = 32
MAX_RESOURCES = 64
_TOOL_KINDS = ("spectre", "virtuoso", "ngspice")
_VERSION_PATTERNS = {
    "spectre": re.compile(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,6}(?:\.isr[0-9]{1,4})?"),
    "virtuoso": re.compile(r"IC(?:ADVM)?[0-9]{1,3}\.[0-9]{1,3}(?:\.[0-9]{1,3})?-(?:32|64)b\.[0-9]{1,6}\.[0-9]{1,6}"),
    "ngspice": re.compile(r"(?:ngspice-)?[0-9]{1,3}(?:\.[0-9]{1,3}){0,2}"),
}
_DIMENSIONS = {
    "execution": {"prepared", "submitting", "queued", "running", "completed", "failed", "cancelled"},
    "observation": {"current", "stale", "unknown"},
    "measurement_validity": {"pending", "valid", "invalid", "indeterminate"},
    "specification_result": {"not_evaluated", "pass", "fail"},
}
_FACT_BOOLEANS = {
    "synthetic", "analysis_completed", "required_signals_present", "measurement_window_covered",
    "connection_lost", "execution_failure_confirmed", "identity_match", "new_attempt_created",
}
_FACT_NUMBERS = {
    "settling_time_us", "settling_target_us", "memory_limit_mb", "last_observed_memory_mb",
    "last_confirmed_simulated_time_us", "required_window_end_us",
}
_EVENT_KINDS = {
    "execution.observed", "observation.disconnected", "execution.reconciled",
    "execution.completed", "execution.failed",
}
_READINESS = {
    "configuration": {"valid", "invalid", "missing", "configured"},
    "transport": {"unknown", "reachable", "unreachable", "protocol_mismatch"},
    "authentication": {"unknown", "simulated_authenticated"},
    "catalog": {"unknown", "advertised", "static", "unavailable"},
    "inference_access": {"unverified", "verified_for_request", "denied", "rate_limited", "unknown"},
    "isolation": {"synthetic_only", "unchecked", "blocked"},
}


def _text(value, limit):
    if not isinstance(value, str):
        return None
    return "".join(c for c in value[:limit] if c.isprintable() or c in "\n\t")


def _id(value):
    try:
        return require_id(value)
    except DomainError:
        return None


def _enum(value, choices):
    return value if isinstance(value, str) and value in choices else "unknown"


def _number(value, maximum=1e12):
    if type(value) is int and 0 <= value <= maximum:
        return value
    if type(value) is float and math.isfinite(value) and 0 <= value <= maximum:
        return value
    return None


def _boolean(value):
    return value if type(value) is bool else None


def _timestamp(value):
    if isinstance(value, str) and len(value) <= 64:
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.utcoffset() is not None:
                return value
        except (ValueError, OverflowError):
            pass
    return None


def _envelope(**data):
    return {"read_only": True, "source": "stored_local_state", "read_at": utcnow(),
            "data_trust": "untrusted_evidence", **data}


def _run_summary(record):
    return {
        "run_id": _id(record.get("run_id")),
        "experiment_id": _id(record.get("experiment_id")),
        "attempt_id": _id(record.get("attempt_id")),
        "label": _text(record.get("label"), 80),
        "scenario": _enum(record.get("scenario"), {"disconnect", "oom", "completed", "target_miss"}),
        "synthetic": _boolean(record.get("synthetic")),
        "created_at": _timestamp(record.get("created_at")),
        "updated_at": _timestamp(record.get("updated_at")),
        **{name: _enum(record.get(name), choices) for name, choices in _DIMENSIONS.items()},
        "progress": _number(record.get("progress"), 1),
        "finished": _boolean(record.get("finished")),
    }


def _facts(raw):
    if not isinstance(raw, dict):
        return {}
    result = {key: _boolean(raw[key]) for key in sorted(_FACT_BOOLEANS) if key in raw}
    result.update({key: _number(raw[key]) for key in sorted(_FACT_NUMBERS) if key in raw})
    if "reason" in raw:
        result["reason"] = _enum(raw["reason"], {"OUT_OF_MEMORY"})
    if "last_confirmed_execution" in raw:
        result["last_confirmed_execution"] = _enum(raw["last_confirmed_execution"], _DIMENSIONS["execution"])
    return result


def _probe(raw):
    if not isinstance(raw, dict):
        return None
    identity = raw.get("identity") if isinstance(raw.get("identity"), dict) else {}
    # Keep the entire verified token, including hotfix/build identity. Never
    # salvage a numeric substring from a banner, path or unconstrained output.
    tools = []
    for item in (raw.get("tools") if isinstance(raw.get("tools"), list) else [])[:8]:
        if not isinstance(item, dict) or item.get("kind") not in _TOOL_KINDS:
            continue
        version = item.get("version")
        match = (_VERSION_PATTERNS[item["kind"]].fullmatch(version)
                 if isinstance(version, str) and len(version) <= 80 else None)
        tools.append({"kind": item["kind"], "status": _enum(item.get("status"), {"available", "unavailable", "unknown"}),
                      "version": match.group(0) if match else None,
                      "error": _probe_error(item.get("error"))})
    return {
        "synthetic": _boolean(raw.get("synthetic")),
        "scope": _enum(raw.get("scope"), {"connectivity_only"}),
        "observed_at": _timestamp(raw.get("observed_at")),
        "transport": _enum(raw.get("transport"), {"reachable", "unknown"}),
        "identity": {"verified": identity.get("verified") is True},
        "tools": tools, "error": _probe_error(raw.get("error")),
    }


def _probe_error(error):
    if not isinstance(error, dict):
        return None
    # Preserve presence of failure without copying unconstrained stderr/message.
    return {"code": "stored_probe_error", "message": "Stored connectivity probe reported an error."}


class ReadViews:
    def __init__(self, store):
        self.store = store

    def status(self):
        return _envelope(
            version=__version__, schema_version=1, mode="read_only_mcp",
            counts=self.store.counts(), counts_scope="stored_rows_including_deleted_connections",
            capabilities={"stored_state_reads": True, "ssh": False, "probe": False,
                          "inference": False, "simulation_actions": False, "controller_recovery": False},
        )

    def list_runs(self):
        page = self.store.list("run")
        return _envelope(runs=[_run_summary(record) for record in page["records"]],
                         limit=page["limit"], truncated=page["truncated"])

    def get_run(self, run_id):
        require_id(run_id, "run ID")
        record = self.store.get("run", run_id)
        if record.get("run_id") != run_id:
            raise DomainError("Stored run identity does not match its record.", "storage_record")
        result = _run_summary(record)
        identity = record.get("execution_identity")
        # P0.1 synthetic native identity only; future real identities need review.
        result["execution_identity"] = (
            {"kind": "synthetic", "executor": "mock", "native_id": _id(identity.get("native_id")), "synthetic": True}
            if isinstance(identity, dict) and identity.get("kind") == "synthetic"
            and identity.get("executor") == "mock" and identity.get("synthetic") is True else None
        )
        raw_events = record.get("events") if isinstance(record.get("events"), list) else []
        result["events"] = [{
            "event_id": _id(item.get("event_id")), "sequence": _number(item.get("sequence")),
            "kind": _enum(item.get("kind"), _EVENT_KINDS),
            "observed_at": _timestamp(item.get("observed_at")),
            "message": _text(item.get("message"), 500), "facts": _facts(item.get("facts")),
        } for item in raw_events[-MAX_EVENTS:] if isinstance(item, dict)]
        result["events_truncated"] = len(raw_events) > MAX_EVENTS
        raw_resources = record.get("resource_history") if isinstance(record.get("resource_history"), list) else []
        result["resource_history"] = [{
            "sequence": _number(item.get("sequence")),
            "simulated_time_us": _number(item.get("simulated_time_us")),
            "memory_mb": _number(item.get("memory_mb")),
        } for item in raw_resources[-MAX_RESOURCES:] if isinstance(item, dict)]
        result["resources_truncated"] = len(raw_resources) > MAX_RESOURCES
        return _envelope(run=result)

    def list_connections(self):
        page = self.store.list("connection")
        result = []
        for record in page["records"]:
            kind = record.get("kind")
            if record.get("deleted") or record.get("synthetic") is not True:
                continue
            if kind not in ("agent", "model") or record.get("backend") != f"fake_{kind}":
                continue
            raw_status = record.get("status")
            status = ({key: _enum(raw_status.get(key), choices) for key, choices in _READINESS.items()}
                      if isinstance(raw_status, dict) else None)
            result.append({
                "connection_id": _id(record.get("connection_id")), "label": _text(record.get("label"), 80),
                "kind": kind, "backend": record["backend"], "synthetic": True,
                "enabled": _boolean(record.get("enabled")), "revision": _number(record.get("revision")),
                "scenario": _enum(record.get("scenario"), {"success", "inference_denied", "disconnect", "permission_denied"}),
                "created_at": _timestamp(record.get("created_at")), "probed_at": _timestamp(record.get("probed_at")),
                "status": status,
            })
        return _envelope(connections=result, limit=MAX_RECORDS, truncated=page["truncated"],
                         scope="fake_metadata_only")

    def list_hosts(self):
        page = self.store.list("host")
        result = []
        for record in page["records"]:
            if type(record.get("synthetic")) is not bool:
                continue
            raw_tools = record.get("tools") if isinstance(record.get("tools"), list) else []
            kinds = sorted({item["kind"] for item in raw_tools[:8] if isinstance(item, dict)
                            and item.get("kind") in _TOOL_KINDS})
            result.append({"host_id": _id(record.get("host_id")), "label": _text(record.get("label"), 80),
                           "synthetic": record["synthetic"],
                           "tools": [{"kind": kind} for kind in kinds], "last_probe": _probe(record.get("last_probe")),
                           "last_resources": resource_summary(record.get("last_resources"))})
        return _envelope(hosts=result, limit=MAX_RECORDS, truncated=page["truncated"],
                         scope="stored_connectivity_metadata_only")

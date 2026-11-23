"""Fixed model-facing projections; never expose raw database records."""

from datetime import datetime
import math
import re

from spiceowl import __version__
from spiceowl.domain.common import DomainError, require_id, utcnow
from spiceowl.storage.readonly import MAX_RECORDS, require_ic_run_id
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
_IC_ARTIFACTS = {"full-results.tar.gz", "result-summary.json", "archive-files.json",
                 "latest-status.log", "waveforms.csv", "waveforms.json", "waveforms.png"}
_IC_ISSUES = {"observation_missing", "compiled_hash_missing", "project_unregistered", "error",
              "status_error", "operation_error", "postprocess_error", "synthetic_fixture"}


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


def _ic_error(value):
    if not isinstance(value, dict):
        return None
    return {"code": "stored_ic_observation_error", "message": "The saved IC observation records an error; no new query was made."}


def _ic_observation(value):
    raw = value if isinstance(value, dict) else {}
    return {"state": _enum(raw.get("state"), {"readable", "unobserved", "recorded", "missing"}),
            "source_state": _enum(raw.get("source_state"), {"recorded", "missing"}),
            "observed_at": _timestamp(raw.get("observed_at")), "checked_at": _timestamp(raw.get("checked_at")),
            "last_success_at": _timestamp(raw.get("last_success_at")), "error": _ic_error(raw.get("error"))}


def _ic_hash(value, size=64):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{%d}" % size, value):
        raise DomainError("A stored IC identity digest is invalid.", "storage_record")
    return value


def _ic_summary(record):
    context = {}
    for name in ("library_id", "cell_id"):
        if name in record:
            value = record[name]
            if value is not None and (not isinstance(value, str)
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value)):
                raise DomainError("A stored IC library/cell identifier is invalid.", "storage_record")
            context[name] = value
    raw_execution = record.get("execution") if isinstance(record.get("execution"), dict) else {}
    raw_data = record.get("data") if isinstance(record.get("data"), dict) else {}
    raw_specification = record.get("specification") if isinstance(record.get("specification"), dict) else {}
    raw_state = raw_execution.get("raw_state")
    native_state = raw_state if isinstance(raw_state, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", raw_state) else None
    return {**context, "run_id": require_ic_run_id(record.get("run_id")), "gateway_id": _id(record.get("gateway_id")),
            "project_id": _text(record.get("project_id"), 180), "original_project_id": _text(record.get("original_project_id"), 240),
            "association_basis": _enum(record.get("association_basis"), {"recorded_project_id", "recorded_plan_design_id"}),
            "source_commit": _ic_hash(record.get("source_commit"), 40),
            "configuration": _text(record.get("configuration"), 240),
            "compiled_inputs_sha256": _ic_hash(record.get("compiled_inputs_sha256")),
            "backend": "ic-workbench", "source": "stored_observations", "imported": True,
            "synthetic": _boolean(record.get("synthetic")), "source_read_at": _timestamp(record.get("source_read_at")),
            "first_seen_at": _timestamp(record.get("first_seen_at")),
            "latest_observed_at": _timestamp(record.get("latest_observed_at")),
            "record_updated_at": _timestamp(record.get("record_updated_at")),
            "execution": {"state": _enum(raw_execution.get("state"), {"queued", "running", "succeeded", "failed", "unknown"}),
                          "raw_state": native_state,
                          "exit_code": raw_execution.get("exit_code") if type(raw_execution.get("exit_code")) is int else None,
                          "observed_at": _timestamp(raw_execution.get("observed_at"))},
            "observation": _ic_observation(record.get("observation")),
            "data": {"state": _enum(raw_data.get("state"), {"not_collected", "collecting", "collected", "failed", "unknown"}),
                     "raw_retained": _boolean(raw_data.get("raw_retained"))},
            "measurement": {"state": "indeterminate", "reason": "measurement_validity_not_attested"},
            "specification": {"state": _enum(raw_specification.get("state"), {"reported", "not_evaluated"})}}


def _ic_detail(record):
    result = _ic_summary(record)
    source = record.get("specification") if isinstance(record.get("specification"), dict) else {}
    metrics = source.get("metrics") if isinstance(source.get("metrics"), list) else []
    values = []
    for metric in metrics[:16]:
        if not isinstance(metric, dict):
            continue
        value = metric.get("value")
        if not (type(value) in (int, float) and abs(value) <= 1e300 and math.isfinite(value)):
            value = None
        values.append({"name": _text(metric.get("name"), 160), "value": value,
                       "unit": _text(metric.get("unit"), 240), "passed": _boolean(metric.get("passed")),
                       "scope": _text(metric.get("scope"), 240)})
    result["specification"].update(metrics=values, metrics_truncated=len(metrics) > 16)
    issues = record.get("issues") if isinstance(record.get("issues"), list) else []
    result["issues"] = [{"code": _enum(issue.get("code"), _IC_ISSUES),
                         "message": "A stored IC issue was reported; its text is not exported by this reader."}
                        for issue in issues[:8] if isinstance(issue, dict)]
    result["issues_truncated"] = len(issues) > 8
    raw_artifacts = record.get("artifacts") if isinstance(record.get("artifacts"), list) else []
    artifacts = []
    for artifact in raw_artifacts[:7]:
        if not isinstance(artifact, dict):
            continue
        name = artifact.get("name")
        if not isinstance(name, str) or name not in _IC_ARTIFACTS:
            continue
        url = "/api/jobs/" + result["run_id"] + "/artifacts/" + name
        if artifact.get("url") != url:
            continue
        digest = artifact.get("sha256")
        digest = digest if isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest) else None
        artifacts.append({"name": name, "bytes": _number(artifact.get("bytes"), 2**63 - 1), "sha256": digest,
                          "url": url, "digest_source": "stored_record" if digest else "unavailable"})
    result["artifacts"] = artifacts
    result["artifacts_omitted"] = len(raw_artifacts) - len(artifacts)
    result["artifact_scope"] = "stored_metadata_only_not_freshly_verified"
    waveform_id = record.get("waveform_run_id")
    result["waveform_run_id"] = waveform_id if waveform_id == result["run_id"] and result["source_commit"] is not None else None
    return result


class ReadViews:
    def __init__(self, store):
        self.store = store

    def status(self):
        return _envelope(
            version=__version__, schema_version=1, mode="read_only_mcp",
            counts=self.store.counts(), counts_scope="stored_rows_including_deleted_connections",
            capabilities={"stored_state_reads": True, "ssh": False, "probe": False,
                          "inference": False, "simulation_actions": False, "controller_recovery": False,
                          "ic_cached_reads": True, "ic_gateway_sync": False},
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

    def list_ic_gateways(self):
        page = self.store.list_ic_gateways()
        gateways = [{"gateway_id": _id(record.get("id")), "label": _text(record.get("label"), 80),
                     "project_id": _text(record.get("project_id"), 180), "backend": "ic-workbench",
                     "created_at": _timestamp(record.get("created_at")),
                     "observation": _ic_observation(record.get("observation"))} for record in page["records"]]
        return _envelope(gateways=gateways, limit=page["limit"], truncated=page["truncated"],
                         scope="stored_ic_gateway_metadata_only")

    def list_ic_runs(self, gateway_id):
        page = self.store.list_ic_runs(gateway_id)
        return _envelope(gateway_id=gateway_id, runs=[_ic_summary(record) for record in page["records"]],
                         limit=page["limit"], truncated=page["truncated"], scope="stored_ic_run_projections",
                         active_query_performed=False, simulation_actions=False)

    def get_ic_run(self, gateway_id, run_id):
        record = self.store.get_ic_run(gateway_id, run_id)
        return _envelope(run=_ic_detail(record), scope="stored_ic_run_projection",
                         active_query_performed=False, simulation_actions=False)

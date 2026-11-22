"""Conservative recommendations from stored host observations, without IO.

These thresholds are prototype policy, not resource reservations or simulator
certification. Timestamps and headroom estimates remain visible to reviewers.
"""

from datetime import datetime, timezone
from fractions import Fraction
import math
import re

from spiceowl.domain.common import DomainError, require_id


MAX_HOSTS = 32
RESOURCE_TTL_SECONDS = 120
VERSION_TTL_SECONDS = 86_400
RESERVE_PERCENT = 20
MAX_BUSY_PERCENT = 80
HOME_DISK_FLOOR_BYTES = 1_073_741_824
_SIMULATORS = {"spectre", "ngspice"}
_HOSTNAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,252}", re.ASCII)
_USERNAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}", re.ASCII)
_VERSIONS = {
    "spectre": re.compile(r"[0-9]+(?:\.[0-9]+){2,4}(?:\.isr[0-9]+)?", re.IGNORECASE | re.ASCII),
    "ngspice": re.compile(r"[0-9]+(?:\.[0-9]+)*(?:\+|-dev)?", re.IGNORECASE | re.ASCII),
}
_METRIC_FIELDS = {
    "cpu_count", "cpu_busy_percent", "load_1m", "load_5m",
    "memory_total_bytes", "memory_available_bytes", "home_disk_available_bytes",
}


def validate_requirements(requirements):
    """Normalize only the three P0.3 request fields; bool is never a number."""
    if type(requirements) is not dict or set(requirements) != {"cpu_count", "memory_bytes", "simulator"}:
        raise DomainError("Requirements must contain cpu_count, memory_bytes and simulator only.", "invalid_requirements")
    cpus, memory, simulator = (requirements[key] for key in ("cpu_count", "memory_bytes", "simulator"))
    if type(cpus) is not int or not 1 <= cpus <= 4096:
        raise DomainError("Requested cpu_count must be an integer from 1 to 4096.", "invalid_requirements")
    if type(memory) is not int or memory <= 0:
        raise DomainError("Requested memory_bytes must be a positive integer.", "invalid_requirements")
    if type(simulator) is not str or simulator not in _SIMULATORS:
        raise DomainError("Requested simulator must be spectre or ngspice.", "invalid_requirements")
    return {"cpu_count": cpus, "memory_bytes": memory, "simulator": simulator}


def _timestamp(value):
    if type(value) is not str or not 1 <= len(value) <= 64:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is not None:
            return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        pass
    return None


def _clock(now):
    if now is None:
        return datetime.now(timezone.utc)
    if type(now) is datetime:
        try:
            if now.utcoffset() is not None:
                return now.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            pass
    elif type(now) is str:
        parsed = _timestamp(now)
        if parsed is not None:
            return parsed
    raise DomainError("now must be a timezone-aware datetime or ISO timestamp.", "invalid_placement")


def _valid_identity_name(value, pattern):
    return (type(value) is str and pattern.fullmatch(value) is not None
            and ".." not in value and not value.endswith("."))


def _observation(record, key, scope, ttl, now, reject):
    observation = record.get(key)
    prefix = "resources" if key == "last_resources" else "version"
    if type(observation) is not dict:
        reject(prefix + "_missing", "The required stored observation is missing.")
        return None, None
    if (observation.get("synthetic") is not False or observation.get("scope") != scope
            or observation.get("host_id") != record["host_id"]):
        reject(prefix + "_provenance_invalid", "Observation scope, host binding or real-data provenance is invalid.")
    if observation.get("transport") != "reachable" or "error" not in observation or observation["error"] is not None:
        reject(prefix + "_incomplete", "The observation did not establish a complete reachable result.")
    identity = observation.get("identity")
    if (type(identity) is not dict or identity.get("verified") is not True
            or identity.get("hostname") != record.get("expected_host")
            or identity.get("username") != record.get("expected_user")):
        reject(prefix + "_identity_mismatch", "Verified observation identity must match the registered expected host and user.")
    observed = _timestamp(observation.get("observed_at"))
    age = None if observed is None else (now - observed).total_seconds()
    if age is None:
        reject(prefix + "_timestamp_invalid", "A timezone-aware observation timestamp is required.")
    elif age < 0:
        reject(prefix + "_timestamp_future", "A future-dated observation cannot establish freshness.")
    elif age > ttl:
        reject(prefix + "_stale", f"The observation exceeds its {ttl}-second freshness budget.")
    return observation, age


def _metric_int(value, *, positive=False):
    return type(value) is int and (1 if positive else 0) <= value <= 9_223_372_036_854_775_807


def _metric_number(value, maximum):
    return (type(value) in (int, float) and 0 <= value <= maximum
            and (type(value) is int or math.isfinite(value)))


def _metrics(observation):
    raw = observation.get("resources") if type(observation) is dict else None
    if type(raw) is not dict or not _METRIC_FIELDS <= raw.keys():
        return None
    if not _metric_int(raw["cpu_count"], positive=True) or raw["cpu_count"] > 1_048_576:
        return None
    if not _metric_number(raw["cpu_busy_percent"], 100):
        return None
    if any(not _metric_number(raw[name], 1_000_000_000) for name in ("load_1m", "load_5m")):
        return None
    if not _metric_int(raw["memory_total_bytes"], positive=True):
        return None
    if any(not _metric_int(raw[name]) for name in ("memory_available_bytes", "home_disk_available_bytes")):
        return None
    if raw["memory_available_bytes"] > raw["memory_total_bytes"]:
        return None
    return {name: raw[name] for name in sorted(_METRIC_FIELDS)}


def _version(observation, simulator):
    tools = observation.get("tools") if type(observation) is dict else None
    if type(tools) is not list or len(tools) > 3 or any(type(tool) is not dict for tool in tools):
        return None
    matches = [tool for tool in tools if tool.get("kind") == simulator]
    if len(matches) != 1:
        return None
    tool = matches[0]
    token = tool.get("version")
    if (tool.get("status") != "available" or tool.get("error") is not None
            or type(token) is not str or len(token) > 80 or _VERSIONS[simulator].fullmatch(token) is None):
        return None
    return token


def recommend_hosts(host_records, requirements, *, now=None):
    """Rank up to 32 explicitly supplied registered hosts using stored data only.

    Missing/invalid per-host observations produce exclusions. Malformed outer
    records, duplicate host IDs and malformed requirements raise DomainError.
    Ranking maximizes post-request bottleneck slack, then memory slack, then
    freshness, with host_id as a deterministic tie-breaker. No input is changed.
    """
    request = validate_requirements(requirements)
    clock = _clock(now)
    if type(host_records) is not list or len(host_records) > MAX_HOSTS:
        raise DomainError("Supply a list of at most 32 selected host records.", "invalid_placement")
    seen, candidates, excluded = set(), [], []
    for record in host_records:
        if type(record) is not dict:
            raise DomainError("Each host record must be a dictionary.", "invalid_placement")
        host_id = require_id(record.get("host_id"), "host ID")
        if host_id in seen:
            raise DomainError("Duplicate host IDs cannot be ranked.", "invalid_placement")
        seen.add(host_id)
        codes, reasons = [], []

        def reject(code, reason):
            codes.append(code)
            reasons.append(reason)

        if record.get("synthetic") is not False:
            reject("synthetic_or_unverified_host", "Only explicitly non-synthetic registered hosts are eligible.")
        if (not _valid_identity_name(record.get("expected_host"), _HOSTNAME)
                or not _valid_identity_name(record.get("expected_user"), _USERNAME)):
            reject("expected_identity_invalid", "The registered expected host and user are missing or invalid.")
        resources, resource_age = _observation(record, "last_resources", "host_resources", RESOURCE_TTL_SECONDS, clock, reject)
        probe, version_age = _observation(record, "last_probe", "connectivity_only", VERSION_TTL_SECONDS, clock, reject)
        metrics = _metrics(resources)
        if metrics is None:
            reject("resource_metrics_invalid", "All seven resource metrics must be present, finite, correctly typed and consistent; null is not idle.")
        version = _version(probe, request["simulator"])
        if version is None:
            reject("simulator_unavailable", "Exactly one available requested simulator with a complete supported version token is required.")

        row = {
            "host_id": host_id,
            "label": (record["label"] if type(record.get("label")) is str
                      and 1 <= len(record["label"]) <= 80 and record["label"].isprintable()
                      and record["label"].strip() else host_id),
            "resource_observed_at": resources.get("observed_at") if resources and _timestamp(resources.get("observed_at")) else None,
            "version_observed_at": probe.get("observed_at") if probe and _timestamp(probe.get("observed_at")) else None,
            "resource_age_seconds": resource_age,
            "version_age_seconds": version_age,
        }
        if codes:
            excluded.append(row | {"reason_codes": codes, "reasons": reasons})
            continue

        cpu_count = metrics["cpu_count"]
        cpu_reserve = Fraction(cpu_count, 5)
        idle_cpu = cpu_count * (1 - Fraction(str(metrics["cpu_busy_percent"])) / 100)
        idle_after = idle_cpu - request["cpu_count"]
        load_after = max(Fraction(str(metrics["load_1m"])), Fraction(str(metrics["load_5m"]))) + request["cpu_count"]
        load_slack = Fraction(cpu_count * 4, 5) - load_after
        memory_reserve = (metrics["memory_total_bytes"] + 4) // 5
        memory_after = metrics["memory_available_bytes"] - request["memory_bytes"]
        memory_slack = memory_after - memory_reserve
        if request["cpu_count"] > cpu_count:
            reject("cpu_count_insufficient", "Requested CPUs exceed the observed CPU count.")
        if metrics["cpu_busy_percent"] > MAX_BUSY_PERCENT:
            reject("cpu_busy_high", "Observed CPU busy percentage exceeds 80%.")
        if idle_after < cpu_reserve:
            reject("cpu_headroom_insufficient", "The request would leave less than 20% of total CPU capacity idle.")
        if load_slack < 0:
            reject("load_headroom_insufficient", "The larger of 1/5-minute load plus requested CPUs exceeds 80% of CPU count.")
        if memory_slack < 0:
            reject("memory_headroom_insufficient", "The request would leave less than 20% of total memory available.")
        if metrics["home_disk_available_bytes"] < HOME_DISK_FLOOR_BYTES:
            reject("home_disk_headroom_insufficient", "Observed home-filesystem space is below the 1 GiB screening floor; target-directory quota is not checked.")
        if codes:
            excluded.append(row | {"reason_codes": codes, "reasons": reasons})
            continue

        cpu_slack = idle_after - cpu_reserve
        score = min(cpu_slack / cpu_count, load_slack / cpu_count,
                    Fraction(memory_slack, metrics["memory_total_bytes"]))
        candidates.append(row | {
            "simulator": request["simulator"], "version": version,
            "resources": metrics,
            "score": float(score),
            "estimated_headroom": {
                "idle_cpu_cores": float(idle_cpu), "idle_cpu_cores_after_request": float(idle_after),
                "cpu_reserve_cores": float(cpu_reserve), "cpu_slack_cores": float(cpu_slack),
                "load_after_request": float(load_after), "load_slack_cores": float(load_slack),
                "memory_after_request_bytes": memory_after, "memory_reserve_bytes": memory_reserve,
                "memory_slack_bytes": memory_slack,
            },
            "reasons": [
                "Resource and version observations match the registered host/user and freshness budgets.",
                "Projected CPU utilization and 1/5-minute load retain the 20% capacity reserve.",
                "Available memory after the request retains 20% of total memory; home disk meets the 1 GiB screening floor.",
                "Stored observations only: no allocation, reservation, license check or submission has occurred.",
            ],
        })
    candidates.sort(key=lambda row: (-row["score"], -row["estimated_headroom"]["memory_slack_bytes"],
                                    row["resource_age_seconds"], row["version_age_seconds"], row["host_id"]))
    for rank, candidate in enumerate(candidates, 1):
        candidate["rank"] = rank
    excluded.sort(key=lambda row: row["host_id"])
    return {
        "scope": "placement_recommendation", "source": "stored_host_observations",
        "evaluated_at": clock.isoformat(), "requirements": request,
        "policy": {"resource_ttl_seconds": RESOURCE_TTL_SECONDS, "version_ttl_seconds": VERSION_TTL_SECONDS,
                   "reserve_percent": RESERVE_PERCENT, "max_cpu_busy_percent": MAX_BUSY_PERCENT,
                   "max_load_per_cpu": 0.8, "home_disk_floor_bytes": HOME_DISK_FLOOR_BYTES},
        "candidates": candidates, "excluded": excluded,
        "recommended_host_id": candidates[0]["host_id"] if candidates else None,
        "reservation": False, "dispatch_enabled": False,
    }

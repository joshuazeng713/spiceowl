"""Explicit private resource selection for an external CircuitGit caller.

Only HostRegistry owns probes. This adapter never reserves resources, submits a
simulation, retries a scan, or changes a selected host. Its profile digest binds
configuration bytes; it is not a signature or proof of execution authority.
"""

import copy
from datetime import datetime, timezone
import json
import math
import re

from spiceowl.adapters.hosts import validate_host_profile
from spiceowl.domain.common import DomainError, digest, require_id
from spiceowl.hosts import HostRegistry, profile_from_record
from spiceowl.scheduling.placement import MAX_HOSTS, recommend_hosts, validate_requirements


SCHEMA = "spiceowl.resource-selection.v1"
_FIELDS = {"schema", "host_id", "requirements", "selected_at", "profile", "profile_sha256",
           "simulator_version", "recommendation"}
_RECOMMENDATION = {"scope", "source", "evaluated_at", "requirements", "policy", "candidates",
                   "excluded", "recommended_host_id", "reservation", "dispatch_enabled"}
_EXCLUDED = {"host_id", "label", "resource_observed_at", "version_observed_at",
             "resource_age_seconds", "version_age_seconds", "reason_codes", "reasons"}


def _check(condition, code="invalid_resource_selection"):
    if not condition:
        raise DomainError("Resource selection evidence is invalid or inconsistent.", code)


def _now():
    return datetime.now(timezone.utc)


def _time(value):
    _check(type(value) is str and 1 <= len(value) <= 64)
    try:
        parsed = datetime.fromisoformat(value)
        _check(parsed.utcoffset() is not None)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise DomainError("Resource evidence needs a timezone-aware timestamp.", "invalid_resource_selection") from None


def _json(value):
    def visit(item, depth=0):
        _check(depth <= 12)
        if type(item) is dict:
            _check(all(type(key) is str for key in item))
            for child in item.values():
                visit(child, depth + 1)
        elif type(item) is list:
            _check(len(item) <= 256)
            for child in item:
                visit(child, depth + 1)
        else:
            _check(item is None or type(item) in (str, int, float, bool))
            _check(type(item) is not float or math.isfinite(item))
    try:
        visit(value)
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        _check(len(raw.encode()) <= 256 * 1024)
    except (RecursionError, UnicodeError, OverflowError, ValueError):
        raise DomainError("Resource evidence must be bounded, strict JSON.", "invalid_resource_selection") from None
    return copy.deepcopy(value)


def _requirements(value):
    request = validate_requirements(value)
    if request["simulator"] != "spectre":
        raise DomainError("This CircuitGit resource adapter supports Spectre only.", "invalid_requirements")
    return request


def _profile(value, host_id):
    try:
        profile = validate_host_profile(value)
        _check(profile == value and profile["host_id"] == host_id)
        return _json(profile)
    except (DomainError, KeyError, TypeError, ValueError):
        raise DomainError("The selected host has an invalid resource profile.", "resource_profile_invalid") from None


def _candidate_record(candidate):
    """Re-evaluate public candidate metrics with the existing pure policy.

    The invented identity below is validation scaffolding only. It is never
    returned or used for a probe and does not attest the original observation.
    """
    identity = {"verified": True, "hostname": "validation.invalid", "username": "validation"}
    return {"host_id": candidate.get("host_id"), "label": candidate.get("label"), "synthetic": False,
            "expected_host": identity["hostname"], "expected_user": identity["username"],
            "last_resources": {"host_id": candidate.get("host_id"), "scope": "host_resources",
                "synthetic": False, "transport": "reachable", "identity": identity, "error": None,
                "observed_at": candidate.get("resource_observed_at"), "resources": candidate.get("resources")},
            "last_probe": {"host_id": candidate.get("host_id"), "scope": "connectivity_only",
                "synthetic": False, "transport": "reachable", "identity": identity, "error": None,
                "observed_at": candidate.get("version_observed_at"), "tools": [{"kind": "spectre",
                    "status": "available", "error": None, "version": candidate.get("version")}]}}


def _selection(value):
    """Validate a frozen selection before consulting the registry or scanning."""
    data = _json(value)
    _check(type(data) is dict and set(data) == _FIELDS and data["schema"] == SCHEMA)
    host_id = require_id(data["host_id"], "selected host ID")
    request = _requirements(data["requirements"])
    profile = _profile(data["profile"], host_id)
    _check(sum(tool["kind"] == "spectre" for tool in profile["tools"]) == 1)
    _check(type(data["profile_sha256"]) is str and re.fullmatch(r"[a-f0-9]{64}", data["profile_sha256"])
           and digest(profile) == data["profile_sha256"])
    _check(_time(data["selected_at"]) <= _now())
    rec = data["recommendation"]
    _check(type(rec) is dict and set(rec) == _RECOMMENDATION)
    _check(digest(rec["requirements"]) == digest(request) and rec["evaluated_at"] == data["selected_at"]
           and rec["recommended_host_id"] == host_id and rec["reservation"] is False
           and rec["dispatch_enabled"] is False)
    candidates, excluded = rec["candidates"], rec["excluded"]
    _check(type(candidates) is list and 1 <= len(candidates) <= MAX_HOSTS
           and all(type(row) is dict for row in candidates))
    _check(type(excluded) is list and len(candidates) + len(excluded) <= MAX_HOSTS)
    checked = recommend_hosts([_candidate_record(row) for row in candidates], request, now=data["selected_at"])
    for key in ("scope", "source", "policy", "candidates", "recommended_host_id"):
        _check(digest(checked[key]) == digest(rec[key]))
    seen = {row["host_id"] for row in candidates}
    for row in excluded:
        _check(type(row) is dict and set(row) == _EXCLUDED)
        identifier = require_id(row["host_id"], "excluded host ID")
        _check(identifier not in seen)
        seen.add(identifier)
        _check(type(row["label"]) is str and 1 <= len(row["label"]) <= 80 and row["label"].isprintable())
        for key in ("resource_observed_at", "version_observed_at"):
            if row[key] is not None:
                _time(row[key])
        for key in ("resource_age_seconds", "version_age_seconds"):
            _check(row[key] is None or type(row[key]) in (int, float))
        for key in ("reason_codes", "reasons"):
            _check(type(row[key]) is list and 1 <= len(row[key]) <= 32
                   and all(type(text) is str and 1 <= len(text) <= 500 and text.isprintable() for text in row[key]))
    selected = candidates[0]
    _check(selected["host_id"] == host_id and selected["label"] == profile["label"]
           and selected["version"] == data["simulator_version"])
    return data


class CircuitGitPool:
    def __init__(self, directory):
        self.registry = HostRegistry(directory)

    def _call(self, operation, *args, **kwargs):
        try:
            return getattr(self.registry, operation)(*args, **kwargs)
        except (DomainError, OSError, KeyError, TypeError, ValueError):
            raise DomainError("The registered host resource operation could not complete.", "resource_unavailable") from None

    def _registered(self, host_id):
        record = self._call("inspect", host_id)
        try:
            return _profile(profile_from_record(record), host_id)
        except (KeyError, TypeError):
            raise DomainError("The selected host has an invalid resource profile.", "resource_profile_invalid") from None

    def _refresh(self, host_ids, request, profiles):
        scan = self._call("scan", host_ids)
        _check(type(scan) is dict and scan.get("simulation_actions") is False, "resource_unavailable")
        started, completed = _time(scan.get("started_at")), _time(scan.get("completed_at"))
        _check(started <= completed <= _now(), "resource_unavailable")
        rows = scan.get("results")
        _check(type(rows) is list and len(rows) == len(host_ids)
               and all(type(row) is dict and set(row) == {"host_id", "error"}
                       and type(row["host_id"]) is str for row in rows), "resource_unavailable")
        _check({row["host_id"] for row in rows} == set(host_ids), "resource_unavailable")
        successful = [row["host_id"] for row in rows if row["error"] is None]
        if not successful:
            raise DomainError("No selected host completed a resource scan.", "resource_unavailable")
        recommendation = self._call("recommend", request, host_ids=successful)
        _check(type(recommendation) is dict, "resource_unavailable")
        host_id = recommendation.get("recommended_host_id")
        if host_id is None:
            raise DomainError("No selected host has fresh, eligible resource evidence.", "resource_unavailable")
        _check(type(host_id) is str and host_id in successful, "resource_unavailable")
        profile = self._registered(host_id)
        _check(profile == profiles[host_id], "resource_profile_changed")
        candidates = recommendation.get("candidates")
        _check(type(candidates) is list and candidates and type(candidates[0]) is dict, "resource_unavailable")
        candidate = candidates[0]
        # A busy/failed scan must never reuse an older still-in-TTL observation.
        for key in ("resource_observed_at", "version_observed_at"):
            _check(started <= _time(candidate.get(key)) <= completed, "resource_unavailable")
        still_eligible = recommend_hosts([_candidate_record(candidate)], request, now=_now())
        _check(still_eligible["recommended_host_id"] == host_id, "resource_unavailable")
        selected = _selection({"schema": SCHEMA, "host_id": host_id, "requirements": request,
            "selected_at": recommendation.get("evaluated_at"), "profile": profile,
            "profile_sha256": digest(profile), "simulator_version": candidate.get("version"),
            "recommendation": recommendation})
        recommendation = selected["recommendation"]
        represented = {row["host_id"] for key in ("candidates", "excluded") for row in recommendation[key]}
        _check(represented == set(successful), "resource_unavailable")
        for row in rows:
            if row["error"] is not None:
                recommendation["excluded"].append({"host_id": row["host_id"], "label": profiles[row["host_id"]]["label"],
                    "resource_observed_at": None, "version_observed_at": None,
                    "resource_age_seconds": None, "version_age_seconds": None,
                    "reason_codes": ["scan_failed"],
                    "reasons": ["This host did not complete the requested scan; previous observations were not used."]})
        recommendation["excluded"].sort(key=lambda row: row["host_id"])
        represented = {row["host_id"] for key in ("candidates", "excluded") for row in recommendation[key]}
        _check(represented == set(host_ids), "resource_unavailable")
        return selected

    def select(self, host_ids, requirements):
        """Scan 1..32 explicit hosts once, then select an eligible registered host."""
        _check(type(host_ids) is list and 1 <= len(host_ids) <= MAX_HOSTS)
        ids = [require_id(value, "host ID") for value in host_ids]
        _check(len(set(ids)) == len(ids))
        request = _requirements(requirements)
        profiles = {host_id: self._registered(host_id) for host_id in ids}
        return self._refresh(ids, request, profiles)

    def recheck(self, selection):
        """Refresh the same host; old selection age is not new observation age."""
        frozen = _selection(selection)
        host_id = frozen["host_id"]
        current = self._registered(host_id)
        _check(current == frozen["profile"], "resource_profile_changed")
        latest = self._refresh([host_id], frozen["requirements"], {host_id: current})
        _check(latest["host_id"] == host_id and latest["simulator_version"] == frozen["simulator_version"],
               "resource_version_changed")
        return latest

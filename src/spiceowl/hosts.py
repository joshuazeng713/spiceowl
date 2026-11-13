"""Private host registration and explicit connectivity observations.

These operations never claim controller ownership or change a simulation run.
"""

import fcntl
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
import re
import threading
import time

from spiceowl.domain.common import DomainError, digest, require_id, utcnow
from spiceowl.domain.connectivity import resource_summary, summary_error as _summary_error
from spiceowl.storage.sqlite import Store


PROFILE_FIELDS = ("schema", "host_id", "label", "ssh_target", "expected_host", "expected_user", "tools")


def profile_from_record(record):
    profile = {key: record[key] for key in PROFILE_FIELDS}
    if record.get("schema") == "spiceowl.host.v2":
        profile["ssh_hostname"] = record["ssh_hostname"]
    return profile


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DomainError("Duplicate profile keys are not allowed.")
        result[key] = value
    return result


def load_profile(path):
    """Read an explicitly selected small JSON file, never SSH credentials."""
    with Path(path).expanduser().open("rb") as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise DomainError("Host profiles must be at most 16384 bytes.", "too_large")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError, RecursionError):
        raise DomainError("Host profile must contain a valid JSON object with unique keys.") from None


def host_summary(record):
    """The console shows observations, not private executable/SSH configuration."""
    probe = record.get("last_probe")
    last_probe = None
    if isinstance(probe, dict):
        identity = probe.get("identity") if isinstance(probe.get("identity"), dict) else {}
        tools = []
        for tool in probe.get("tools", [])[:3]:
            if not isinstance(tool, dict) or tool.get("kind") not in {"spectre", "virtuoso", "ngspice"}:
                continue
            version = tool.get("version")
            tools.append({"kind": tool["kind"],
                          "status": tool.get("status") if tool.get("status") in {"available", "unavailable"} else "unknown",
                          "version": version if isinstance(version, str) and re.fullmatch(r"[A-Za-z0-9.+-]{1,80}", version) else None,
                          "error": _summary_error(tool.get("error"))})
        last_probe = {"synthetic": probe.get("synthetic") is True, "scope": "connectivity_only",
                      "observed_at": probe.get("observed_at"),
                      "transport": "reachable" if probe.get("transport") == "reachable" else "unknown",
                      "identity": {"verified": identity.get("verified") is True},
                      "tools": tools, "error": _summary_error(probe.get("error"))}
    return {"host_id": record["host_id"], "label": record["label"],
            "synthetic": False, "tools": [{"kind": item["kind"]} for item in record["tools"]],
            "last_probe": last_probe, "last_resources": resource_summary(record.get("last_resources"))}


class HostRegistry:
    def __init__(self, directory):
        self.store = Store(directory)
        self._slots = threading.BoundedSemaphore(2)

    def add(self, data):
        from spiceowl.adapters.hosts import validate_host_profile
        profile = validate_host_profile(data)
        record = profile | {"created_at": utcnow(), "revision": 1, "synthetic": False, "last_probe": None, "last_resources": None}
        return self.store.create("host", record["host_id"], record)

    def import_pool(self, template_id, names):
        """Register only explicitly supplied hostnames using an existing route."""
        from spiceowl.adapters.hosts import validate_host_profile
        if not isinstance(names, list) or not 1 <= len(names) <= 32 or any(not isinstance(n, str) for n in names) or len(set(names)) != len(names):
            raise DomainError("Select 1–32 distinct hostnames.")
        template = profile_from_record(self.inspect(template_id))
        profiles = []
        for name in names:
            host_id = name if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", name) else "host-" + digest(name)[:16]
            profiles.append(validate_host_profile(template | {"schema": "spiceowl.host.v2", "ssh_hostname": name,
                            "host_id": host_id, "label": name[:80], "expected_host": name}))
        selected = []
        with self.store.transaction() as db:
            existing = [json.loads(r[0]) for r in db.execute("SELECT body FROM records WHERE kind='host'")]
            for profile in profiles:
                equivalent = next((h for h in existing if h.get("ssh_target") == profile["ssh_target"] and
                                   h.get("ssh_hostname", h.get("ssh_target")) == profile["ssh_hostname"] and
                                   h.get("expected_host") == profile["expected_host"] and h.get("expected_user") == profile["expected_user"] and h.get("tools") == profile["tools"]), None)
                if equivalent:
                    selected.append({"host_id": equivalent["host_id"], "created": False})
                    continue
                if any(h["host_id"] == profile["host_id"] for h in existing):
                    raise DomainError("A selected hostname conflicts with an existing profile ID.", "conflict")
                record = profile | {"created_at": utcnow(), "revision": 1, "synthetic": False, "last_probe": None, "last_resources": None}
                self.store._put(db, "host", profile["host_id"], record)
                existing.append(record)
                selected.append({"host_id": record["host_id"], "created": True})
        return {"hosts": selected, "network_operations": 0}

    def list(self):
        return [host_summary(record) for record in self.store.list("host")]

    def inspect(self, host_id):
        return self.store.get("host", require_id(host_id, "host ID"))

    @contextmanager
    def _guard(self, host_id):
        host_id = require_id(host_id, "host ID")
        if not self._slots.acquire(blocking=False):
            raise DomainError("Two host probes are already in progress. Wait for a result before retrying.", "busy")
        try:
            # A second process must not race a probe and overwrite newer evidence.
            # Never remove this lock file: replacing its inode defeats flock.
            with (self.store.directory / f".host-probe-{host_id}.lock").open("a+") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise DomainError("This host already has a probe in progress. Wait for its result.", "busy") from None
                yield profile_from_record(self.inspect(host_id))
        finally:
            self._slots.release()

    def probe(self, host_id):
        from spiceowl.adapters.hosts import probe_host
        with self._guard(host_id) as profile:
            observation = probe_host(profile)
            self.store.update("host", host_id, lambda current: current | {"last_probe": observation})
            return observation

    def resources(self, host_id):
        from spiceowl.adapters.hosts import probe_resources
        with self._guard(host_id) as profile:
            observation = probe_resources(profile)
            self.store.update("host", host_id, lambda current: current | {"last_resources": observation})
            return observation

    def scan(self, host_ids=None):
        from spiceowl.adapters.hosts import probe_host, probe_resources
        ids = host_ids if host_ids is not None else [h["host_id"] for h in self.store.list("host")]
        if not isinstance(ids, list) or not 1 <= len(ids) <= 32 or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
            raise DomainError("A pool scan needs 1–32 distinct registered hosts.")
        for host_id in ids:
            self.inspect(host_id)
        started = utcnow()
        deadline = time.monotonic() + 120
        def scan_one(host_id):
            if time.monotonic() >= deadline:
                return {"host_id": host_id, "error": {"code": "scan_budget", "message": "This host was not attempted within the scan budget."}}
            try:
                with self._guard(host_id) as profile:
                    probe = probe_host(profile)
                    self.store.update("host", host_id, lambda h: h | {"last_probe": probe})
                    if probe["identity"]["verified"] and probe["transport"] == "reachable":
                        resources = probe_resources(profile)
                    else:
                        resources = {"host_id": host_id, "scope": "host_resources", "synthetic": False, "observed_at": utcnow(),
                                     "transport": "unknown", "identity": {"verified": False}, "resources": {},
                                     "error": {"code": "identity_unverified", "message": "Resource check requires verified SSH identity."}}
                    self.store.update("host", host_id, lambda h: h | {"last_resources": resources})
                    return {"host_id": host_id, "error": resources.get("error")}
            except (DomainError, OSError):
                return {"host_id": host_id, "error": {"code": "probe_unavailable", "message": "Host is busy or its local probe could not complete."}}
        with (self.store.directory / ".host-pool-scan.lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise DomainError("A pool scan is already running.", "busy") from None
            with ThreadPoolExecutor(max_workers=2) as workers:
                results = list(workers.map(scan_one, ids))
        return {"started_at": started, "completed_at": utcnow(), "results": results,
                "hosts": self.list(), "concurrency": 2, "simulation_actions": False}

    def recommend(self, requirements):
        from spiceowl.scheduling.placement import recommend_hosts
        return recommend_hosts(self.store.list("host"), requirements)

"""Private host registration and explicit connectivity observations.

These operations never claim controller ownership or change a simulation run.
"""

import fcntl
import json
from pathlib import Path
import re
import threading

from spiceowl.domain.common import DomainError, require_id, utcnow
from spiceowl.storage.sqlite import Store


PROFILE_FIELDS = ("schema", "host_id", "label", "ssh_target", "expected_host", "expected_user", "tools")


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
            "last_probe": last_probe}


def _summary_error(error):
    if not error:
        return None
    return {"code": "probe_incomplete", "message": "The connection check did not complete. Inspect the private host record for its error code."}


class HostRegistry:
    def __init__(self, directory):
        self.store = Store(directory)
        self._slots = threading.BoundedSemaphore(2)

    def add(self, data):
        from spiceowl.adapters.hosts import validate_host_profile
        profile = validate_host_profile(data)
        record = profile | {"created_at": utcnow(), "revision": 1, "synthetic": False, "last_probe": None}
        return self.store.create("host", record["host_id"], record)

    def list(self):
        return [host_summary(record) for record in self.store.list("host")]

    def inspect(self, host_id):
        return self.store.get("host", require_id(host_id, "host ID"))

    def probe(self, host_id):
        from spiceowl.adapters.hosts import probe_host
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
                record = self.inspect(host_id)
                profile = {key: record[key] for key in PROFILE_FIELDS}
                observation = probe_host(profile)
                self.store.update("host", host_id, lambda current: current | {"last_probe": observation})
                return observation
        finally:
            self._slots.release()

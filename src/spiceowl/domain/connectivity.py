"""Pure projections for stored host observations; no transport or storage imports."""

from datetime import datetime
import math


def summary_error(error):
    if not error:
        return None
    messages = {
        "ssh_host_key_changed": "SSH reported a changed host key. Verify it with the server administrator.",
        "ssh_host_key_unverified": "This host has no verified matching SSH trust record. Confirm its host key before connecting.",
        "ssh_permission_denied": "SSH access was denied for the selected route.",
        "ssh_connection_timeout": "The SSH connection timed out.",
        "ssh_name_resolution_failed": "A hostname on the SSH route could not be resolved. Check the target and jump-host names and required VPN or site DNS.",
        "ssh_network_unreachable": "A network on the SSH route is unreachable. Check the required network or VPN and jump-host route.",
        "ssh_connection_refused": "A connection on the SSH route was refused. Check the SSH port and service on the target or jump host.",
        "ssh_connection_lost": "The SSH connection ended unexpectedly. The server's current state is unknown; a simulation failure is not established.",
        "ssh_config_invalid": "OpenSSH could not use the selected configuration. Check the existing SSH alias and its included configuration.",
        "identity_mismatch": "The remote identity did not match the registered host.",
        "identity_unverified": "Host identity was not verified; resources were not read.",
        "probe_timeout": "The bounded connection check timed out.",
    }
    code = error.get("code") if isinstance(error, dict) else None
    if isinstance(code, str) and code in messages:
        return {"code": code, "message": messages[code]}
    return {"code": "probe_incomplete", "message": "The connection check did not complete. Inspect the private host record for its error code."}


def resource_summary(raw):
    if not isinstance(raw, dict):
        return None
    limits = {"cpu_count": 1048576, "cpu_busy_percent": 100, "load_1m": 1e8, "load_5m": 1e8,
              "memory_total_bytes": 2**63, "memory_available_bytes": 2**63, "home_disk_available_bytes": 2**63}
    source = raw.get("resources") if isinstance(raw.get("resources"), dict) else {}
    values = {}
    for key, maximum in limits.items():
        value = source.get(key)
        values[key] = value if type(value) in (int, float) and 0 <= value <= maximum and math.isfinite(value) else None
    stamp = raw.get("observed_at")
    try:
        if not isinstance(stamp, str) or len(stamp) > 64 or datetime.fromisoformat(stamp).utcoffset() is None:
            stamp = None
    except ValueError:
        stamp = None
    identity = raw.get("identity") if isinstance(raw.get("identity"), dict) else {}
    return {"synthetic": raw.get("synthetic") if type(raw.get("synthetic")) is bool else None,
            "scope": "host_resources", "observed_at": stamp,
            "transport": "reachable" if raw.get("transport") == "reachable" else "unknown",
            "identity": {"verified": identity.get("verified") is True}, "resources": values,
            "error": summary_error(raw.get("error"))}

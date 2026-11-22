"""Parse bounded Linux aggregate samples without IO or missing-value estimates.

Sources checked 2026-11-13: https://docs.kernel.org/filesystems/proc.html and
https://github.com/torvalds/linux/blob/master/kernel/sched/cputime.c . Guest
time is already included in user/nice; use only the first eight CPU counters.
Busy includes steal and excludes idle+iowait. Counter regression (including
iowait), hotplug, non-advancing or delayed samples yield unknown utilization.

Only MemAvailable is an availability estimate; never substitute MemFree/cache.
These are host aggregates, not a cgroup allocation, memory guarantee, disk
quota, or reservation. Stored snapshot freshness is checked by placement.
"""

from decimal import Decimal
import re

from spiceowl.domain.common import DomainError


RESOURCE_FIELDS = (
    "cpu_count", "cpu_busy_percent", "load_1m", "load_5m", "memory_total_bytes",
    "memory_available_bytes", "home_disk_available_bytes",
)
_WIRE_FIELDS = frozenset({
    "cpu_count_first", "cpu_count_second", "cpu_first", "cpu_second",
    "uptime_first", "uptime_second", "load_1m", "load_5m", "MemTotal:",
    "MemAvailable:", "home_disk_available_bytes",
})
_MAX_INTEGER = (1 << 63) - 1
_MAX_COUNTER = (1 << 64) - 1
_MAX_CPUS = 1 << 20
_UNSIGNED = re.compile(r"[0-9]{1,20}", re.ASCII)
_DECIMAL = re.compile(r"[0-9]{1,13}(?:\.[0-9]{1,6})?", re.ASCII)


def empty_resources() -> dict:
    return dict.fromkeys(RESOURCE_FIELDS)


def _uint(value, limit=_MAX_INTEGER):
    if not isinstance(value, str) or _UNSIGNED.fullmatch(value) is None:
        return None
    number = int(value)
    return number if number <= limit else None


def _decimal(value, limit):
    if not isinstance(value, str) or _DECIMAL.fullmatch(value) is None:
        return None
    number = Decimal(value)
    return number if number <= limit else None


def _cpu_count(value):
    count = _uint(value, _MAX_CPUS)
    return count if count is not None and count > 0 else None


def _counters(value):
    if not isinstance(value, str):
        return None
    parts = value.split()
    if not 8 <= len(parts) <= 10:
        return None
    values = [_uint(part, _MAX_COUNTER) for part in parts]
    if any(number is None for number in values) or sum(values[:8]) > _MAX_COUNTER:
        return None
    return values


def _busy_percent(fields):
    first = _counters(fields.get("cpu_first"))
    second = _counters(fields.get("cpu_second"))
    count_first = _cpu_count(fields.get("cpu_count_first"))
    count_second = _cpu_count(fields.get("cpu_count_second"))
    start = _decimal(fields.get("uptime_first"), 10**12)
    end = _decimal(fields.get("uptime_second"), 10**12)
    if first is None or second is None or len(first) != len(second):
        return None
    if count_first is None or count_first != count_second or start is None or end is None:
        return None
    if not Decimal("0.1") <= end - start <= Decimal("2.0"):
        return None
    deltas = [after - before for before, after in zip(first[:8], second[:8])]
    if any(delta < 0 for delta in deltas):
        return None
    total = sum(deltas)
    if total <= 0:
        return None
    idle = deltas[3] + deltas[4]
    return (total - idle) * 100.0 / total


def _memory_bytes(value):
    if not isinstance(value, str):
        return None
    parts = value.split()
    if len(parts) != 2 or parts[1] != "kB":
        return None
    kilobytes = _uint(parts[0], _MAX_INTEGER // 1024)
    return None if kilobytes is None else kilobytes * 1024


def parse_resource_sample(payload) -> dict:
    """Parse the fixed wire fields; absent/invalid numeric values stay null.

    Structural ambiguity, duplicate/unknown tags, or oversized payloads fail
    closed. Numeric overflow and unsupported units invalidate only that metric
    and its dependents. No raw payload is placed in exception messages.
    """
    if not isinstance(payload, str) or len(payload) > 8192:
        raise DomainError("Invalid resource sample size.", "invalid_resource_response")
    lines = payload.splitlines()
    if len(lines) > 32:
        raise DomainError("Invalid resource sample size.", "invalid_resource_response")
    fields = {}
    for line in lines:
        if not line.strip():
            continue
        if len(line) > 512:
            raise DomainError("Invalid resource sample size.", "invalid_resource_response")
        parts = line.split(maxsplit=1)
        name = parts[0]
        if name not in _WIRE_FIELDS or name in fields:
            raise DomainError("Unknown or duplicate resource field.", "invalid_resource_response")
        fields[name] = parts[1].strip() if len(parts) == 2 else ""
    total = _memory_bytes(fields.get("MemTotal:"))
    total = total if total is not None and total > 0 else None
    available = _memory_bytes(fields.get("MemAvailable:"))
    if total is None or available is None or available > total:
        available = None
    load_1m = _decimal(fields.get("load_1m"), 10**6)
    load_5m = _decimal(fields.get("load_5m"), 10**6)
    count_key = "cpu_count_second" if "cpu_count_second" in fields else "cpu_count_first"
    return {
        "cpu_count": _cpu_count(fields.get(count_key)),
        "cpu_busy_percent": _busy_percent(fields),
        "load_1m": float(load_1m) if load_1m is not None else None,
        "load_5m": float(load_5m) if load_5m is not None else None,
        "memory_total_bytes": total,
        "memory_available_bytes": available,
        "home_disk_available_bytes": _uint(fields.get("home_disk_available_bytes")),
    }

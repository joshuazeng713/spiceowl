"""Live aggregate resource and active-simulation inspection for registered SSH hosts.

Runs one bounded, read-only POSIX shell probe per host over OpenSSH (``/bin/sh -s``
over stdin so ``csh``/``tcsh`` login shells work cleanly) to collect:
  1. Verified identity + Linux aggregate resources (same wire format as
     ``spiceowl.adapters.resources.parse_resource_sample``, persisted as
     ``last_resources`` on the host record).
  2. Active EDA simulator & EDA tool process summaries (``comm`` ONLY — never
     command-line arguments, netlist paths, or secrets).
  3. SpiceOwl-managed direct simulation runs targeting each host.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import math
import re
import secrets
import shlex
import subprocess
from typing import Any

from spiceowl._platform import resolve_ssh_executable
from spiceowl.adapters.hosts import validate_host_profile
from spiceowl.adapters.resources import parse_resource_sample
from spiceowl.domain.common import DomainError, utcnow
from spiceowl.domain.connectivity import resource_summary, summary_error
from spiceowl.hosts import HostRegistry, host_summary, profile_from_record


MAX_LIVE_HOSTS = 32
LIVE_PROBE_TIMEOUT_SECONDS = 12
MAX_STDOUT_BYTES = 256 * 1024
MAX_PROCESSES_PER_HOST = 40

# Known EDA simulation engines (exact lowercase process comm names or prefixes).
_SIMULATOR_EXACT = frozenset({
    "spectre", "aps", "spectre_root", "spectre_64", "ultrasim", "amsd", "msim",
    "ngspice", "xyce", "hspice", "hspice64", "hspice_mt", "hspicerf",
    "afs", "afsx", "afs_64", "finesim", "finesim_pro", "xa", "eldo", "eldod",
    "xcelium", "xrun", "xmsim", "irun", "ncsim", "vcs", "simv", "vsim", "vvp",
    "ocean", "adexl", "verilator", "qrun",
})
_EDA_TOOL_EXACT = frozenset({
    "virtuoso", "icfb", "calibre", "calibrerve", "calibredrv", "calibre_wb",
    "pegasus", "pvs", "assura", "quantus", "qrc", "innovus", "encounter",
    "dc_shell", "pt_shell", "icc2_shell", "fc_shell", "genus", "tempus",
    "voltus", "starrc", "laker", "custom_compiler", "wv", "viva", "awd",
})
_IGNORED_COMMS = frozenset({
    "spectremonitor", "cdsmsgserver", "cdsd", "lmgrd", "cdslmd", "snpslmd",
})

_ACTIVE_RUN_STATES = frozenset({
    "staging", "launching", "running", "watching", "queued", "submitted", "collecting",
})

_NONCE_RE = re.compile(r"^[0-9a-f]{16,64}$")


def classify_eda_process(comm: str) -> str | None:
    """Return 'simulation', 'eda_tool', or None for a process ``comm`` name."""
    raw = (comm or "").strip()
    if not raw:
        return None
    # Strip trailing path or wrapper markers, compare lowercase.
    base = raw.rsplit("/", 1)[-1].lower()
    if base in _IGNORED_COMMS or base.endswith("monitor"):
        return None
    if base in _SIMULATOR_EXACT:
        return "simulation"
    if base in _EDA_TOOL_EXACT:
        return "eda_tool"
    if base.startswith(("spectre_", "hspice", "finesim", "xcelium")):
        return "simulation"
    if base.startswith(("calibre", "virtuoso")):
        return "eda_tool"
    return None


def _build_live_probe_script(nonce: str, expected_host: str, expected_user: str) -> bytes:
    """Return a POSIX /bin/sh stdin script collecting identity, resources, and EDA ps comm rows."""
    if not _NONCE_RE.fullmatch(nonce):
        raise DomainError("Invalid probe nonce.", "invalid_probe_nonce")
    id_begin = f"__SPICEOWL_{nonce}_ID_BEGIN__"
    id_end = f"__SPICEOWL_{nonce}_ID_END__"
    res_begin = f"__SPICEOWL_{nonce}_RES_BEGIN__"
    res_end = f"__SPICEOWL_{nonce}_RES_END__"
    sims_begin = f"__SPICEOWL_{nonce}_SIMS_BEGIN__"
    sims_end = f"__SPICEOWL_{nonce}_SIMS_END__"

    cpu_awk_1 = (
        '$1 == "cpu" {printf "cpu_first"; for (i=2; i<=NF; i++) printf " %s", $i; print ""; next} '
        '$1 ~ /^cpu[0-9]+$/ {n++; next} {exit} END {if (n>0) print "cpu_count_first", n}'
    )
    cpu_awk_2 = (
        '$1 == "cpu" {printf "cpu_second"; for (i=2; i<=NF; i++) printf " %s", $i; print ""; next} '
        '$1 ~ /^cpu[0-9]+$/ {n++; next} {exit} END {if (n>0) print "cpu_count_second", n}'
    )
    uptime_1 = 'NR==1 {print "uptime_first", $1; exit}'
    uptime_2 = 'NR==1 {print "uptime_second", $1; exit}'
    load_awk = 'NR==1 {print "load_1m", $1; print "load_5m", $2; exit}'
    mem_awk = '$1=="MemTotal:" || $1=="MemAvailable:" {print $1, $2, $3}'
    disk_awk = 'NR==2 && NF==1 {print "home_disk_available_bytes", $1}'

    sims_awk = (
        'NR>1 && tolower($7) ~ /^(spectre|aps|virtuoso|icfb|ngspice|xyce|hspice|afs|finesim|xa|eldo|'
        'xcelium|xrun|xmsim|irun|ncsim|vcs|simv|vsim|vvp|ocean|adexl|verilator|qrun|ultrasim|amsd|msim|'
        'calibre|pegasus|pvs|assura|quantus|qrc|innovus|encounter|dc_shell|pt_shell|icc2_shell|fc_shell|'
        'genus|tempus|voltus|starrc|laker|custom_compiler|wv|viva|awd)/ {print $0}'
    )

    lines = [
        "LC_ALL=C",
        "export LC_ALL",
        'so_host=$(hostname 2>/dev/null || uname -n 2>/dev/null)',
        'so_user=$(id -un 2>/dev/null || whoami 2>/dev/null)',
        f'printf "\\n%s\\nhostname %s\\nusername %s\\n%s\\n" "{id_begin}" "$so_host" "$so_user" "{id_end}"',
        f'if [ "$so_host" = {shlex.quote(expected_host)} ] && [ "$so_user" = {shlex.quote(expected_user)} ]; then',
        f'  printf "\\n%s\\n" "{res_begin}"',
        f'  /usr/bin/awk {shlex.quote(cpu_awk_1)} /proc/stat 2>/dev/null || awk {shlex.quote(cpu_awk_1)} /proc/stat 2>/dev/null',
        f'  /usr/bin/awk {shlex.quote(uptime_1)} /proc/uptime 2>/dev/null || awk {shlex.quote(uptime_1)} /proc/uptime 2>/dev/null',
        '  if /bin/sleep 0.2 2>/dev/null || sleep 0.2 2>/dev/null; then',
        f'    /usr/bin/awk {shlex.quote(cpu_awk_2)} /proc/stat 2>/dev/null || awk {shlex.quote(cpu_awk_2)} /proc/stat 2>/dev/null',
        f'    /usr/bin/awk {shlex.quote(uptime_2)} /proc/uptime 2>/dev/null || awk {shlex.quote(uptime_2)} /proc/uptime 2>/dev/null',
        '  fi',
        f'  /usr/bin/awk {shlex.quote(load_awk)} /proc/loadavg 2>/dev/null || awk {shlex.quote(load_awk)} /proc/loadavg 2>/dev/null',
        f'  /usr/bin/awk {shlex.quote(mem_awk)} /proc/meminfo 2>/dev/null || awk {shlex.quote(mem_awk)} /proc/meminfo 2>/dev/null',
        '  case "$HOME" in /*)',
        '    if [ -x /usr/bin/timeout ] && [ -x /bin/df ]; then',
        '      so_disk=$(/usr/bin/timeout -k 1s 1s /bin/df -B1 --output=avail -- "$HOME" 2>/dev/null) && '
        f'printf "%s\\n" "$so_disk" | awk {shlex.quote(disk_awk)} 2>/dev/null',
        '    fi',
        '  ;; esac',
        f'  printf "\\n%s\\n" "{res_end}"',
        f'  printf "\\n%s\\n" "{sims_begin}"',
        f'  (ps -eo pid,user:32,pcpu,pmem,rss,etime,comm 2>/dev/null || ps -eo pid,user,pcpu,pmem,rss,etime,comm 2>/dev/null) | awk {shlex.quote(sims_awk)} 2>/dev/null',
        f'  printf "\\n%s\\n" "{sims_end}"',
        "else",
        "  exit 72",
        "fi",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def _extract_block(text: str, begin_marker: str, end_marker: str) -> list[str] | None:
    lines = text.splitlines()
    start = end = None
    for idx, line in enumerate(lines):
        s = line.strip()
        if s == begin_marker and start is None:
            start = idx + 1
        elif s == end_marker and start is not None:
            end = idx
            break
    if start is None or end is None or end < start:
        return None
    return [ln for ln in lines[start:end] if ln.strip()]


def parse_eda_processes(lines: list[str], expected_user: str) -> dict[str, Any]:
    """Parse ``ps -eo pid,user,pcpu,pmem,rss,etime,comm`` rows into safe EDA activity summaries."""
    processes: list[dict[str, Any]] = []
    by_tool: dict[str, int] = {}
    sim_count = 0
    my_sim_count = 0
    tool_count = 0
    total_sim_cpu = 0.0
    total_sim_rss = 0

    for raw in lines:
        parts = raw.strip().split(None, 6)
        if len(parts) < 7:
            continue
        pid_s, user_s, pcpu_s, pmem_s, rss_s, etime_s, comm_s = parts
        tool = comm_s.strip().rsplit("/", 1)[-1]
        category = classify_eda_process(tool)
        if category is None:
            continue
        try:
            pid = int(pid_s)
            cpu_pct = float(pcpu_s)
            mem_pct = float(pmem_s)
            rss_kb = int(rss_s)
        except ValueError:
            continue
        if pid <= 0 or not math.isfinite(cpu_pct) or not math.isfinite(mem_pct) or rss_kb < 0:
            continue
        cpu_pct = max(0.0, round(cpu_pct, 1))
        mem_pct = max(0.0, round(mem_pct, 1))
        rss_bytes = rss_kb * 1024
        user_clean = user_s.strip()[:64]
        is_me = user_clean == expected_user
        by_tool[tool] = by_tool.get(tool, 0) + 1
        if category == "simulation":
            sim_count += 1
            if is_me:
                my_sim_count += 1
            total_sim_cpu += cpu_pct
            total_sim_rss += rss_bytes
        else:
            tool_count += 1
        processes.append({
            "pid": pid,
            "user": user_clean,
            "is_me": is_me,
            "tool": tool[:48],
            "category": category,
            "cpu_percent": cpu_pct,
            "mem_percent": mem_pct,
            "rss_bytes": rss_bytes,
            "elapsed": etime_s.strip()[:32],
        })

    processes.sort(key=lambda p: (0 if p["category"] == "simulation" else 1, -p["cpu_percent"], -p["rss_bytes"], p["pid"]))
    by_tool_sorted = dict(sorted(by_tool.items(), key=lambda kv: (-kv[1], kv[0])))
    return {
        "active_count": sim_count,
        "my_active_count": my_sim_count,
        "eda_tool_count": tool_count,
        "total_sim_cpu_percent": round(total_sim_cpu, 1),
        "total_sim_rss_bytes": total_sim_rss,
        "by_tool": by_tool_sorted,
        "processes": processes[:MAX_PROCESSES_PER_HOST],
        "truncated": len(processes) > MAX_PROCESSES_PER_HOST,
    }


def _run_ssh_live_probe(profile: dict, script_bytes: bytes) -> tuple[int, str, str]:
    ssh_exe = resolve_ssh_executable()
    argv = [
        ssh_exe,
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "UpdateHostKeys=no",
        "-o", "ForwardAgent=no",
        "-o", "ClearAllForwardings=yes",
        "-o", "RequestTTY=no",
        "-o", "ConnectTimeout=5",
        "-o", "ServerAliveInterval=4",
        "-o", "ServerAliveCountMax=2",
        "-o", "ControlMaster=no",
        "-o", "ControlPath=none",
    ]
    if profile.get("ssh_hostname"):
        argv.extend(["-o", f"Hostname={profile['ssh_hostname']}"])
    argv.extend(["--", profile["ssh_target"], "/bin/sh -s"])
    proc = subprocess.run(
        argv,
        input=script_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=LIVE_PROBE_TIMEOUT_SECONDS,
        check=False,
    )
    out = (proc.stdout or b"")[:MAX_STDOUT_BYTES].decode("utf-8", errors="replace")
    err = (proc.stderr or b"")[:4096].decode("utf-8", errors="replace")
    return proc.returncode, out, err


def _managed_runs_for_host(direct_runs: list[dict], host_id: str) -> list[dict]:
    matched = []
    for run in direct_runs:
        if run.get("host_id") == host_id or run.get("selected_host_id") == host_id:
            state = str(run.get("state") or "unknown")
            matched.append({
                "run_id": run.get("run_id"),
                "state": state,
                "active": state in _ACTIVE_RUN_STATES,
                "simulator": run.get("simulator") or "spectre",
                "backend": run.get("backend") or "ssh_direct",
                "updated_at": run.get("updated_at") or run.get("created_at"),
            })
    matched.sort(key=lambda r: (0 if r["active"] else 1, str(r.get("updated_at") or "")), reverse=False)
    # Keep active runs first, then newest inactive runs
    active = [r for r in matched if r["active"]]
    inactive = sorted([r for r in matched if not r["active"]], key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    return active + inactive[:5]


def _classify_load_level(ok: bool, resources: dict | None, sim_count: int) -> str:
    if not ok:
        return "error"
    res = resources or {}
    cpu = res.get("cpu_busy_percent")
    mem_tot = res.get("memory_total_bytes")
    mem_avail = res.get("memory_available_bytes")
    mem_used_pct = None
    if isinstance(mem_tot, (int, float)) and mem_tot > 0 and isinstance(mem_avail, (int, float)):
        mem_used_pct = max(0.0, min(100.0, (1.0 - mem_avail / mem_tot) * 100.0))
    if (isinstance(cpu, (int, float)) and cpu >= 75.0) or (mem_used_pct is not None and mem_used_pct >= 80.0):
        return "busy"
    if (isinstance(cpu, (int, float)) and cpu >= 30.0) or (mem_used_pct is not None and mem_used_pct >= 55.0):
        return "moderate"
    return "idle"


def inspect_host_live_status(
    hosts: HostRegistry,
    record: dict,
    *,
    direct_runs: list[dict] | None = None,
) -> dict[str, Any]:
    """Inspect live resources and running simulations on one registered host."""
    host_id = record["host_id"]
    label = record.get("label") or host_id
    managed_runs = _managed_runs_for_host(direct_runs or [], host_id)
    now_iso = utcnow()
    profile = validate_host_profile(profile_from_record(record))
    nonce = secrets.token_hex(16)
    script = _build_live_probe_script(nonce, profile["expected_host"], profile["expected_user"])

    try:
        rc, out, _err = _run_ssh_live_probe(profile, script)
    except subprocess.TimeoutExpired:
        return _failed_entry(record, managed_runs, now_iso, "probe_timeout", "采样超时，请检查网络或跳板机状态。")
    except OSError as exc:
        return _failed_entry(record, managed_runs, now_iso, "ssh_probe_failed", f"无法执行本地 SSH：{exc}")

    id_lines = _extract_block(out, f"__SPICEOWL_{nonce}_ID_BEGIN__", f"__SPICEOWL_{nonce}_ID_END__")
    id_map = {}
    for ln in id_lines or []:
        parts = ln.split(None, 1)
        if len(parts) == 2:
            id_map[parts[0]] = parts[1].strip()
    actual_host = id_map.get("hostname")
    actual_user = id_map.get("username")
    if rc == 72 or (actual_host and actual_host != profile["expected_host"]) or (actual_user and actual_user != profile["expected_user"]):
        return _failed_entry(record, managed_runs, now_iso, "identity_mismatch", "远端主机名或用户名与登记档案不一致。")
    if rc != 0 or not id_lines:
        return _failed_entry(record, managed_runs, now_iso, "ssh_probe_failed", "无法通过 SSH 连接该服务器（请检查免密或跳板机）。")

    res_lines = _extract_block(out, f"__SPICEOWL_{nonce}_RES_BEGIN__", f"__SPICEOWL_{nonce}_RES_END__")
    sim_lines = _extract_block(out, f"__SPICEOWL_{nonce}_SIMS_BEGIN__", f"__SPICEOWL_{nonce}_SIMS_END__")

    parsed_resources = None
    if res_lines is not None:
        try:
            parsed_resources = parse_resource_sample("\n".join(res_lines))
        except DomainError:
            parsed_resources = None

    # Persist valid aggregate resource sample into the host store so placement & pool views stay fresh.
    if parsed_resources is not None:
        observation = {
            "host_id": host_id,
            "synthetic": False,
            "scope": "host_resources",
            "observed_at": now_iso,
            "transport": "reachable",
            "identity": {"verified": True, "hostname": actual_host, "username": actual_user},
            "resources": parsed_resources,
            "error": None,
        }
        try:
            hosts.store.update("host", host_id, lambda current: current | {"last_resources": observation})
        except DomainError:
            pass
        safe_res = resource_summary(observation)["resources"]
    else:
        prev = resource_summary(record.get("last_resources"))
        safe_res = prev["resources"] if prev else {}

    simulations = parse_eda_processes(sim_lines or [], profile["expected_user"])
    mem_total = safe_res.get("memory_total_bytes")
    mem_avail = safe_res.get("memory_available_bytes")
    mem_used = (mem_total - mem_avail) if (isinstance(mem_total, (int, float)) and isinstance(mem_avail, (int, float)) and mem_total >= mem_avail) else None
    mem_used_pct = round((mem_used / mem_total) * 100.0, 1) if (mem_used is not None and mem_total and mem_total > 0) else None

    return {
        "host_id": host_id,
        "label": label,
        "ok": True,
        "observed_at": now_iso,
        "load_level": _classify_load_level(True, safe_res, simulations["active_count"]),
        "resources": {
            **safe_res,
            "memory_used_bytes": mem_used,
            "memory_used_percent": mem_used_pct,
        },
        "simulations": simulations,
        "managed_runs": managed_runs,
        "error": None,
    }


def _failed_entry(record: dict, managed_runs: list[dict], observed_at: str, code: str, message: str) -> dict[str, Any]:
    prev = resource_summary(record.get("last_resources"))
    safe_res = prev["resources"] if prev else {}
    return {
        "host_id": record["host_id"],
        "label": record.get("label") or record["host_id"],
        "ok": False,
        "observed_at": observed_at,
        "load_level": "error",
        "resources": {
            **safe_res,
            "memory_used_bytes": None,
            "memory_used_percent": None,
        },
        "simulations": {
            "active_count": 0,
            "my_active_count": 0,
            "eda_tool_count": 0,
            "total_sim_cpu_percent": 0.0,
            "total_sim_rss_bytes": 0,
            "by_tool": {},
            "processes": [],
            "truncated": False,
        },
        "managed_runs": managed_runs,
        "error": {"code": code, "message": message},
    }


def inspect_pool_live_status(
    hosts: HostRegistry,
    direct_manager: Any | None = None,
    *,
    host_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Concurrently inspect live resource usage and active simulations across registered hosts."""
    all_records = hosts.store.list("host")
    by_id = {r["host_id"]: r for r in all_records}
    if host_ids is not None:
        if (not isinstance(host_ids, list) or not 1 <= len(host_ids) <= MAX_LIVE_HOSTS
                or any(not isinstance(h, str) for h in host_ids) or len(set(host_ids)) != len(host_ids)):
            raise DomainError(f"Select 1–{MAX_LIVE_HOSTS} distinct registered host IDs.")
        missing = [h for h in host_ids if h not in by_id]
        if missing:
            raise DomainError("A selected host is not registered.", "not_found")
        targets = [by_id[h] for h in host_ids]
    else:
        targets = all_records[:MAX_LIVE_HOSTS]

    direct_runs = direct_manager.list() if direct_manager is not None else []
    now_iso = utcnow()
    if not targets:
        return {
            "observed_at": now_iso,
            "summary": {
                "total_hosts": 0,
                "reachable_hosts": 0,
                "total_cpus": 0,
                "avg_cpu_busy_percent": None,
                "total_memory_bytes": 0,
                "available_memory_bytes": 0,
                "active_simulations": 0,
                "my_active_simulations": 0,
                "active_eda_tools": 0,
                "managed_active_runs": 0,
            },
            "hosts": [],
            "registry_hosts": [host_summary(r) for r in hosts.store.list("host")],
        }

    workers = min(6, len(targets))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda rec: inspect_host_live_status(hosts, rec, direct_runs=direct_runs), targets))

    reachable = [r for r in results if r["ok"]]
    cpu_samples = [r["resources"].get("cpu_busy_percent") for r in reachable if isinstance(r["resources"].get("cpu_busy_percent"), (int, float))]
    total_cpus = sum(int(r["resources"].get("cpu_count") or 0) for r in reachable)
    total_mem = sum(int(r["resources"].get("memory_total_bytes") or 0) for r in reachable)
    avail_mem = sum(int(r["resources"].get("memory_available_bytes") or 0) for r in reachable)
    active_sims = sum(int(r["simulations"].get("active_count") or 0) for r in reachable)
    my_active_sims = sum(int(r["simulations"].get("my_active_count") or 0) for r in reachable)
    active_tools = sum(int(r["simulations"].get("eda_tool_count") or 0) for r in reachable)
    managed_active = sum(sum(1 for mr in r["managed_runs"] if mr["active"]) for r in results)

    return {
        "observed_at": now_iso,
        "summary": {
            "total_hosts": len(results),
            "reachable_hosts": len(reachable),
            "total_cpus": total_cpus,
            "avg_cpu_busy_percent": round(sum(cpu_samples) / len(cpu_samples), 1) if cpu_samples else None,
            "total_memory_bytes": total_mem,
            "available_memory_bytes": avail_mem,
            "active_simulations": active_sims,
            "my_active_simulations": my_active_sims,
            "active_eda_tools": active_tools,
            "managed_active_runs": managed_active,
        },
        "hosts": results,
        "registry_hosts": [host_summary(r) for r in hosts.store.list("host")],
    }

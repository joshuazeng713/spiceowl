"""Live aggregate resource, active-simulation, and FlexLM license inspection for SSH hosts.

Runs one bounded, read-only POSIX shell probe per host over OpenSSH (``/bin/sh -s``
over stdin so ``csh``/``tcsh`` login shells work cleanly) to collect:
  1. Verified identity + Linux aggregate resources (same wire format as
     ``spiceowl.adapters.resources.parse_resource_sample``, persisted as
     ``last_resources`` on the host record).
  2. Active EDA simulator & EDA tool process summaries (``comm`` ONLY — never
     command-line arguments, netlist paths, or secrets), including the current
     user's own CPU/memory footprint on each server.
  3. SpiceOwl-managed direct simulation runs targeting each host.
  4. Cluster-wide FlexLM license availability discovered dynamically from the
     remote shell environment, ``~/.flexlmrc``, and bounded read-only rc files
     (zero hardcoded site servers).
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
from spiceowl.domain.connectivity import resource_summary
from spiceowl.hosts import HostRegistry, host_summary, profile_from_record


MAX_LIVE_HOSTS = 32
LIVE_PROBE_TIMEOUT_SECONDS = 12
LICENSE_PROBE_TIMEOUT_SECONDS = 10
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

_LMSTAT_USERS_RE = re.compile(
    r"^Users of (?P<feature>[A-Za-z0-9_.:+-]+):\s*\("
    r"Total of (?P<issued>\d+) licenses? issued;\s*"
    r"Total of (?P<in_use>\d+) licenses? in use\)",
    re.IGNORECASE,
)

# Priority SPICE simulation & EDA license features mapped to readable labels and categories.
# Primary license bar displays only ``spice_sim`` features; other active EDA tools stay in details.
# (pattern_regex, friendly_label, category, priority)
_LICENSE_FEATURE_CATALOG: tuple[tuple[re.Pattern[str], str, str, int], ...] = (
    (re.compile(r"^Virtuoso_Multi_mode_Simulation$", re.I), "Spectre (MMSIM)", "spice_sim", 1),
    (re.compile(r"^Spectre_X_MMSIM_Lk$", re.I), "Spectre X", "spice_sim", 2),
    (re.compile(r"^Virtuoso_Spectre_GXL_MMSIM_Lk$", re.I), "Spectre GXL", "spice_sim", 3),
    (re.compile(r"^Virtuoso_Spectre$", re.I), "Spectre", "spice_sim", 4),
    (re.compile(r"^Spectre_RF", re.I), "Spectre RF", "spice_sim", 5),
    (re.compile(r"^(Virtuoso_Accelerate_Parallel_Simulator|APS_)", re.I), "APS", "spice_sim", 6),
    (re.compile(r"^UltraSim", re.I), "UltraSim", "spice_sim", 7),
    (re.compile(r"^hspice", re.I), "HSPICE", "spice_sim", 8),
    (re.compile(r"^finesim", re.I), "FineSim", "spice_sim", 9),
    (re.compile(r"^(afs|Analog_FastSPICE)", re.I), "AFS", "spice_sim", 10),
    (re.compile(r"^eldo", re.I), "Eldo", "spice_sim", 11),
    (re.compile(r"^Virtuoso_ADE_Assembler$", re.I), "ADE Assembler", "spice_sim", 12),
    (re.compile(r"^Virtuoso_ADE_Explorer$", re.I), "ADE Explorer", "spice_sim", 13),
    (re.compile(r"^Virtuoso_ADE_GXL$", re.I), "ADE GXL", "spice_sim", 14),
    (re.compile(r"^Xcelium_Single_Core$", re.I), "Xcelium", "eda_tool", 20),
    (re.compile(r"^VCSRuntime", re.I), "VCS", "eda_tool", 21),
    (re.compile(r"^Virtuoso_Schematic_Editor", re.I), "Virtuoso Schematic", "eda_tool", 22),
    (re.compile(r"^Virtuoso_Layout_Suite_GXL$", re.I), "Virtuoso Layout GXL", "eda_tool", 23),
    (re.compile(r"^111$", re.I), "Virtuoso Core (111)", "eda_tool", 24),
    (re.compile(r"^Innovus_Impl_System$", re.I), "Innovus", "eda_tool", 30),
    (re.compile(r"^Genus_Synthesis", re.I), "Genus", "eda_tool", 31),
    (re.compile(r"^Tempus_Timing_Signoff", re.I), "Tempus", "eda_tool", 32),
    (re.compile(r"^Pegasus_RV$", re.I), "Pegasus", "eda_tool", 33),
    (re.compile(r"^calibre", re.I), "Calibre", "eda_tool", 34),
)


def classify_eda_process(comm: str) -> str | None:
    """Return 'simulation', 'eda_tool', or None for a process ``comm`` name."""
    raw = (comm or "").strip()
    if not raw:
        return None
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


def _build_license_probe_script(nonce: str, extra_specs: list[str] | None = None) -> bytes:
    """Build a read-only POSIX script that discovers working lmstat & FlexLM servers dynamically."""
    if not _NONCE_RE.fullmatch(nonce):
        raise DomainError("Invalid probe nonce.", "invalid_probe_nonce")
    lic_begin = f"__SPICEOWL_{nonce}_LIC_BEGIN__"
    lic_end = f"__SPICEOWL_{nonce}_LIC_END__"
    safe_extra = [
        sp.strip()
        for sp in (extra_specs or [])
        if isinstance(sp, str) and re.fullmatch(r"[0-9]{1,5}@[A-Za-z0-9._-]{1,120}", sp.strip())
    ][:8]
    extra_init = " ".join(shlex.quote(sp) for sp in safe_extra)

    lines = [
        "LC_ALL=C",
        "export LC_ALL",
        f'printf "\\n%s\\n" "{lic_begin}"',
        'LMSTAT=""',
        'for d in $(printf "%s" "$PATH" | tr ":" " "); do',
        '  for b in "$d/64bit/lmstat" "$d/lmstat" "$d/64bit/lmutil" "$d/lmutil"; do',
        '    if [ -x "$b" ] && "$b" -v >/dev/null 2>&1; then LMSTAT="$b"; break 2; fi',
        '  done',
        'done',
        'if [ -z "$LMSTAT" ]; then',
        '  for b in /usr/local/bin/lmstat /opt/*/bin/lmstat /opt/*/*/bin/lmstat; do',
        '    if [ -x "$b" ] && "$b" -v >/dev/null 2>&1; then LMSTAT="$b"; break; fi',
        '  done',
        'fi',
        'if [ -z "$LMSTAT" ]; then',
        '  printf "NO_LMSTAT\\n"',
        f'  printf "%s\\n" "{lic_end}"',
        '  exit 0',
        'fi',
        'case "$LMSTAT" in *lmutil) LM_ARGS="lmstat -a -c" ;; *) LM_ARGS="-a -c" ;; esac',
        f'specs={shlex.quote(extra_init) if extra_init else shlex.quote("")}',
        'add_spec() {',
        '  for tok in $(printf "%s" "$1" | tr ":," "  "); do',
        '    case "$tok" in [0-9]*@[A-Za-z0-9._-]*) specs="$specs $tok" ;; esac',
        '  done',
        '}',
        'for kv in $(env 2>/dev/null | grep -E "(_LIC_FILE|_LICENSE_FILE)="); do add_spec "${kv#*=}"; done',
        'if [ -r "$HOME/.flexlmrc" ]; then',
        '  for v in $(grep -oE "[0-9]{3,5}@[A-Za-z0-9._-]+" "$HOME/.flexlmrc" 2>/dev/null); do add_spec "$v"; done',
        'fi',
        'rc_files="$HOME/.cshrc $HOME/.tcshrc $HOME/.bashrc $HOME/.profile"',
        'lvl1=$(grep -hE "^[[:space:]]*(source|\\.)[[:space:]]+" $rc_files 2>/dev/null | awk \'{print $2}\' | tr -d "\\"\';" | sort -u | head -n 10)',
        'lvl2=$(grep -hE "(source|\\.)[[:space:]]+/[^[:space:];\\"\\\']+" $lvl1 2>/dev/null | grep -oE "/[A-Za-z0-9_./-]+" | grep -iE "cshrc|bashrc|cadence|synopsys|mentor|eda|lic" | sort -u | head -n 12)',
        'for f in $rc_files $lvl1 $lvl2; do',
        '  [ -r "$f" ] || continue',
        '  for v in $(grep -iE "LIC_FILE|LICENSE_FILE" "$f" 2>/dev/null | grep -v "^[[:space:]]*#" | grep -oE "[0-9]{3,5}@[A-Za-z0-9._-]+" | head -n 8); do add_spec "$v"; done',
        'done',
        'uniq_specs=$(printf "%s\\n" $specs | sort -u | head -n 10)',
        'if [ -z "$uniq_specs" ]; then',
        '  printf "NO_SPECS\\n"',
        f'  printf "%s\\n" "{lic_end}"',
        '  exit 0',
        'fi',
        'tmpdir=$(mktemp -d 2>/dev/null || echo "/tmp/.so_lic_$$")',
        'mkdir -p "$tmpdir" 2>/dev/null || true',
        'idx=0',
        'for sp in $uniq_specs; do',
        '  idx=$((idx + 1))',
        '  outf="$tmpdir/s_${idx}.txt"',
        '  (',
        '    printf "SPEC %s\\n" "$sp"',
        '    if [ -x /usr/bin/timeout ]; then /usr/bin/timeout -k 1s 2s "$LMSTAT" $LM_ARGS "$sp" 2>/dev/null; else "$LMSTAT" $LM_ARGS "$sp" 2>/dev/null; fi | grep -E "^Users of " | head -n 600',
        '  ) > "$outf" &',
        'done',
        'wait',
        'for f in "$tmpdir"/s_*.txt; do [ -f "$f" ] && cat "$f"; done',
        'rm -rf "$tmpdir" 2>/dev/null || true',
        f'printf "\\n%s\\n" "{lic_end}"',
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
    my_tool_count = 0
    total_sim_cpu = 0.0
    total_sim_rss = 0
    my_sim_cpu = 0.0
    my_sim_rss = 0
    my_total_cpu = 0.0
    my_total_rss = 0

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
            total_sim_cpu += cpu_pct
            total_sim_rss += rss_bytes
            if is_me:
                my_sim_count += 1
                my_sim_cpu += cpu_pct
                my_sim_rss += rss_bytes
        else:
            tool_count += 1
            if is_me:
                my_tool_count += 1
        if is_me:
            my_total_cpu += cpu_pct
            my_total_rss += rss_bytes
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

    processes.sort(
        key=lambda p: (
            0 if (p["is_me"] and p["category"] == "simulation") else 1 if p["category"] == "simulation" else 2 if p["is_me"] else 3,
            -p["cpu_percent"],
            -p["rss_bytes"],
            p["pid"],
        )
    )
    by_tool_sorted = dict(sorted(by_tool.items(), key=lambda kv: (-kv[1], kv[0])))
    return {
        "active_count": sim_count,
        "my_active_count": my_sim_count,
        "eda_tool_count": tool_count,
        "my_eda_tool_count": my_tool_count,
        "total_sim_cpu_percent": round(total_sim_cpu, 1),
        "total_sim_rss_bytes": total_sim_rss,
        "my_sim_cpu_percent": round(my_sim_cpu, 1),
        "my_sim_rss_bytes": my_sim_rss,
        "my_total_cpu_percent": round(my_total_cpu, 1),
        "my_total_rss_bytes": my_total_rss,
        "by_tool": by_tool_sorted,
        "processes": processes[:MAX_PROCESSES_PER_HOST],
        "truncated": len(processes) > MAX_PROCESSES_PER_HOST,
    }


def _classify_license_feature(feature: str) -> tuple[str, str, int] | None:
    for pattern, label, category, prio in _LICENSE_FEATURE_CATALOG:
        if pattern.search(feature):
            return label, category, prio
    return None


def parse_lmstat_output(lines: list[str]) -> dict[str, Any]:
    """Parse ``SPEC <spec>`` + ``Users of ...`` output into clean aggregated FlexLM license stats."""
    if not lines:
        return {"available": False, "status": "unavailable", "servers_queried": 0, "servers_responsive": 0, "features": [], "all_active_features": []}
    if any(ln.strip() == "NO_LMSTAT" for ln in lines):
        return {"available": False, "status": "no_lmstat", "servers_queried": 0, "servers_responsive": 0, "features": [], "all_active_features": []}
    if any(ln.strip() == "NO_SPECS" for ln in lines):
        return {"available": False, "status": "no_specs", "servers_queried": 0, "servers_responsive": 0, "features": [], "all_active_features": []}

    current_spec: str | None = None
    queried_specs: set[str] = set()
    responsive_specs: set[str] = set()
    # Aggregate across servers by canonical feature name
    by_feature: dict[str, dict[str, Any]] = {}

    for raw in lines:
        s = raw.strip()
        if s.startswith("SPEC "):
            current_spec = s[5:].strip()[:120]
            if current_spec:
                queried_specs.add(current_spec)
            continue
        m = _LMSTAT_USERS_RE.match(s)
        if not m:
            continue
        feat = m.group("feature")
        issued = int(m.group("issued"))
        in_use = int(m.group("in_use"))
        if issued <= 0 or issued > 100000:
            # Skip synthetic/unlimited sentinel license pools (e.g. 114514) unless explicitly needed
            if issued > 50000:
                continue
            if issued <= 0:
                continue
        if current_spec:
            responsive_specs.add(current_spec)
        matched_meta = _classify_license_feature(feat)
        key = matched_meta[0] if matched_meta else feat
        entry = by_feature.get(key)
        if entry is None:
            entry = {
                "feature": feat,
                "label": matched_meta[0] if matched_meta else feat,
                "category": matched_meta[1] if matched_meta else "other",
                "priority": matched_meta[2] if matched_meta else 999,
                "known": matched_meta is not None,
                "issued": 0,
                "in_use": 0,
                "servers": [],
            }
            by_feature[key] = entry
        entry["issued"] += issued
        entry["in_use"] += in_use
        if current_spec:
            entry["servers"].append({"spec": current_spec, "issued": issued, "in_use": in_use})

    items: list[dict[str, Any]] = []
    for entry in by_feature.values():
        issued = entry["issued"]
        in_use = entry["in_use"]
        available = max(0, issued - in_use)
        used_pct = round(min(100.0, (in_use / issued) * 100.0), 1) if issued > 0 else 0.0
        items.append({
            "feature": entry["feature"],
            "label": entry["label"],
            "category": entry["category"],
            "priority": entry["priority"],
            "known": entry["known"],
            "issued": issued,
            "in_use": in_use,
            "available": available,
            "used_percent": used_pct,
            "servers": entry["servers"],
        })

    # Primary bar focuses strictly on SPICE simulation licenses (Spectre/MMSIM/HSPICE/AFS/ADE, etc.)
    highlighted = [
        it for it in items
        if it["category"] == "spice_sim"
    ]
    highlighted.sort(key=lambda x: (0 if x["in_use"] > 0 else 1, x["priority"], -x["in_use"]))

    all_active = [it for it in items if it["in_use"] > 0]
    all_active.sort(key=lambda x: (0 if x["category"] == "spice_sim" else 1 if x["known"] else 2, -x["used_percent"], -x["in_use"], x["label"]))

    return {
        "available": bool(responsive_specs and items),
        "status": "ok" if (responsive_specs and items) else ("unreachable" if queried_specs else "no_specs"),
        "servers_queried": len(queried_specs),
        "servers_responsive": len(responsive_specs),
        "features": highlighted[:10],
        "all_active_features": all_active[:40],
    }


def _run_ssh_live_probe(profile: dict, script_bytes: bytes, *, timeout: int = LIVE_PROBE_TIMEOUT_SECONDS) -> tuple[int, str, str]:
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
        timeout=timeout,
        check=False,
    )
    out = (proc.stdout or b"")[:MAX_STDOUT_BYTES].decode("utf-8", errors="replace")
    err = (proc.stderr or b"")[:4096].decode("utf-8", errors="replace")
    return proc.returncode, out, err


def inspect_pool_licenses(record: dict, *, extra_specs: list[str] | None = None) -> dict[str, Any]:
    """Query cluster-wide FlexLM license availability via one reachable registered host."""
    profile = validate_host_profile(profile_from_record(record))
    nonce = secrets.token_hex(16)
    script = _build_license_probe_script(nonce, extra_specs=extra_specs)
    try:
        rc, out, _err = _run_ssh_live_probe(profile, script, timeout=LICENSE_PROBE_TIMEOUT_SECONDS)
    except (subprocess.TimeoutExpired, OSError):
        return {"available": False, "status": "timeout", "servers_queried": 0, "servers_responsive": 0, "features": [], "all_active_features": []}
    if rc != 0:
        return {"available": False, "status": "error", "servers_queried": 0, "servers_responsive": 0, "features": [], "all_active_features": []}
    lic_lines = _extract_block(out, f"__SPICEOWL_{nonce}_LIC_BEGIN__", f"__SPICEOWL_{nonce}_LIC_END__")
    return parse_lmstat_output(lic_lines or [])


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
    cpu_count = safe_res.get("cpu_count")
    cpu_busy = safe_res.get("cpu_busy_percent")
    mem_total = safe_res.get("memory_total_bytes")
    mem_avail = safe_res.get("memory_available_bytes")
    mem_used = (mem_total - mem_avail) if (isinstance(mem_total, (int, float)) and isinstance(mem_avail, (int, float)) and mem_total >= mem_avail) else None
    mem_used_pct = round((mem_used / mem_total) * 100.0, 1) if (mem_used is not None and mem_total and mem_total > 0) else None

    # Compute the current user's own simulation CPU% and Memory% share of this host for segmented bars.
    my_sim_cpu_raw_pct = simulations["my_sim_cpu_percent"]  # 100% = 1 core
    my_sim_cpu_cores = round(my_sim_cpu_raw_pct / 100.0, 2)
    my_sim_cpu_host_pct = 0.0
    if isinstance(cpu_count, (int, float)) and cpu_count > 0 and my_sim_cpu_raw_pct > 0:
        cap_cpu = float(cpu_busy) if isinstance(cpu_busy, (int, float)) else 100.0
        my_sim_cpu_host_pct = round(min(cap_cpu, my_sim_cpu_raw_pct / float(cpu_count)), 1)

    my_sim_mem_bytes = simulations["my_sim_rss_bytes"]
    my_sim_mem_host_pct = 0.0
    if isinstance(mem_total, (int, float)) and mem_total > 0 and my_sim_mem_bytes > 0:
        cap_mem = float(mem_used_pct) if isinstance(mem_used_pct, (int, float)) else 100.0
        my_sim_mem_host_pct = round(min(cap_mem, (my_sim_mem_bytes / float(mem_total)) * 100.0), 1)

    return {
        "host_id": host_id,
        "label": label,
        "username": profile["expected_user"],
        "ok": True,
        "observed_at": now_iso,
        "load_level": _classify_load_level(True, safe_res, simulations["active_count"]),
        "resources": {
            **safe_res,
            "memory_used_bytes": mem_used,
            "memory_used_percent": mem_used_pct,
            "my_sim_cpu_cores": my_sim_cpu_cores,
            "my_sim_cpu_percent": my_sim_cpu_host_pct,
            "my_sim_memory_bytes": my_sim_mem_bytes,
            "my_sim_memory_percent": my_sim_mem_host_pct,
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
        "username": record.get("expected_user") or "",
        "ok": False,
        "observed_at": observed_at,
        "load_level": "error",
        "resources": {
            **safe_res,
            "memory_used_bytes": None,
            "memory_used_percent": None,
            "my_sim_cpu_cores": 0.0,
            "my_sim_cpu_percent": 0.0,
            "my_sim_memory_bytes": 0,
            "my_sim_memory_percent": 0.0,
        },
        "simulations": {
            "active_count": 0,
            "my_active_count": 0,
            "eda_tool_count": 0,
            "my_eda_tool_count": 0,
            "total_sim_cpu_percent": 0.0,
            "total_sim_rss_bytes": 0,
            "my_sim_cpu_percent": 0.0,
            "my_sim_rss_bytes": 0,
            "my_total_cpu_percent": 0.0,
            "my_total_rss_bytes": 0,
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
    include_licenses: bool = True,
) -> dict[str, Any]:
    """Concurrently inspect live resource usage, active simulations, and FlexLM licenses."""
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
    empty_lic = {"available": False, "status": "no_hosts", "servers_queried": 0, "servers_responsive": 0, "features": [], "all_active_features": []}
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
                "my_active_eda_tools": 0,
                "active_eda_tools": 0,
                "managed_active_runs": 0,
            },
            "my_simulations": [],
            "licenses": empty_lic,
            "hosts": [],
            "registry_hosts": [host_summary(r) for r in hosts.store.list("host")],
        }

    # Query hosts AND cluster-wide FlexLM licenses concurrently!
    workers = min(8, len(targets) + (1 if include_licenses else 0))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        host_futures = [pool.submit(inspect_host_live_status, hosts, rec, direct_runs=direct_runs) for rec in targets]
        lic_future = pool.submit(inspect_pool_licenses, targets[0]) if include_licenses else None
        results = [f.result() for f in host_futures]
        licenses = lic_future.result() if lic_future is not None else None

    # Fallback: if first host was unreachable for license check, try first reachable host once
    if include_licenses and licenses and not licenses.get("available") and licenses.get("status") in ("timeout", "error"):
        fallback_rec = next((by_id[r["host_id"]] for r in results if r["ok"] and r["host_id"] != targets[0]["host_id"]), None)
        if fallback_rec is not None:
            licenses = inspect_pool_licenses(fallback_rec)

    reachable = [r for r in results if r["ok"]]
    cpu_samples = [r["resources"].get("cpu_busy_percent") for r in reachable if isinstance(r["resources"].get("cpu_busy_percent"), (int, float))]
    total_cpus = sum(int(r["resources"].get("cpu_count") or 0) for r in reachable)
    total_mem = sum(int(r["resources"].get("memory_total_bytes") or 0) for r in reachable)
    avail_mem = sum(int(r["resources"].get("memory_available_bytes") or 0) for r in reachable)
    active_sims = sum(int(r["simulations"].get("active_count") or 0) for r in reachable)
    my_active_sims = sum(int(r["simulations"].get("my_active_count") or 0) for r in reachable)
    my_active_tools = sum(int(r["simulations"].get("my_eda_tool_count") or 0) for r in reachable)
    active_tools = sum(int(r["simulations"].get("eda_tool_count") or 0) for r in reachable)
    managed_active = sum(sum(1 for mr in r["managed_runs"] if mr["active"]) for r in results)

    my_simulations: list[dict[str, Any]] = []
    for r in results:
        for mr in r.get("managed_runs") or []:
            if mr.get("active"):
                my_simulations.append({
                    "source": "managed",
                    "host_id": r["host_id"],
                    "run_id": mr["run_id"],
                    "tool": mr.get("simulator") or "spectre",
                    "state": mr.get("state") or "running",
                    "cpu_percent": None,
                    "rss_bytes": None,
                    "elapsed": "",
                })
        for proc in (r.get("simulations") or {}).get("processes") or []:
            if proc.get("is_me") and proc.get("category") == "simulation":
                my_simulations.append({
                    "source": "remote",
                    "host_id": r["host_id"],
                    "pid": proc["pid"],
                    "tool": proc["tool"],
                    "state": "running",
                    "cpu_percent": proc["cpu_percent"],
                    "rss_bytes": proc["rss_bytes"],
                    "elapsed": proc["elapsed"],
                })
    my_simulations.sort(key=lambda item: (0 if item["source"] == "managed" else 1, -(item.get("cpu_percent") or 0.0)))

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
            "my_active_eda_tools": my_active_tools,
            "active_eda_tools": active_tools,
            "managed_active_runs": managed_active,
        },
        "my_simulations": my_simulations,
        "licenses": licenses,
        "hosts": results,
        "registry_hosts": [host_summary(r) for r in hosts.store.list("host")],
    }

"""PVT / Corner / Monte Carlo batch experiment management for DirectExecutionManager.

Provides deterministic case matrix expansion, explicit seed/sample identity
preservation, bounded concurrency dispatching, lossless metric aggregation,
conservative yield & Cpk statistics (never dropping failed or missing samples),
and searchable JSON / Markdown / CSV batch reports — natively cross-platform
across macOS, Linux, and Windows.
"""

from __future__ import annotations

import contextlib
from copy import deepcopy
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import random
import re
import uuid

from spiceowl._platform import file_lock
from spiceowl.direct import DirectExecutionManager, extract_netlist_organization_hints
from spiceowl.domain.common import DomainError, utcnow
from spiceowl.experiments.analog_metrics import (
    SUPPORTED_TRANSIENT_METRICS,
    extract_named_metric,
    measure_transient_signal,
)
from spiceowl.integrations import psf_binary


BATCH_SCHEMA = "spiceowl.direct-batch.v1"
BATCH_SUMMARY_SCHEMA = "spiceowl.direct-batch-summary.v1"
BATCH_INDEX_SCHEMA = "spiceowl.direct-batch-index.v1"

MAX_BATCH_CASES = 256
MAX_CONCURRENCY = 32
MAX_SPECS = 16

_BATCH_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,96}", re.ASCII)
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}", re.ASCII)
_CORNER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}", re.ASCII)

_ACTIVE_RUN_STATES = frozenset({
    "staged", "submitted", "running_observed",
    "launch_outcome_unknown", "reconcile_required",
})
_TERMINAL_RUN_STATES = frozenset({
    "simulator_complete_pending_measurements",
    "simulator_failed",
    "job_setup_failed",
    "input_hash_failure",
})


def _atomic_write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _read_json(path: Path) -> dict:
    if path.is_symlink():
        raise DomainError("Batch state file cannot be a symlink.", "batch_state_invalid")
    if not path.is_file():
        raise DomainError("Batch experiment record was not found.", "not_found")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise DomainError("Batch experiment record is unreadable or corrupt.", "batch_state_invalid") from None


@contextlib.contextmanager
def _locked(path: Path):
    with file_lock(path, nonblocking=False, mode="a", create_dir=True, dir_mode=0o700):
        yield


def validate_batch_id(batch_id: str) -> str:
    if not isinstance(batch_id, str) or _BATCH_ID.fullmatch(batch_id) is None:
        raise DomainError("Batch ID must be 1–97 ASCII letters, digits, hyphens, or underscores.", "invalid_batch_spec")
    return batch_id


def _validate_specs(specs: list[dict] | None) -> list[dict]:
    if not specs:
        return []
    if not isinstance(specs, list) or len(specs) > MAX_SPECS:
        raise DomainError(f"specs must be a list of at most {MAX_SPECS} acceptance rules.", "invalid_batch_spec")
    cleaned = []
    seen = set()
    for item in specs:
        if not isinstance(item, dict):
            raise DomainError("Each spec entry must be an object.", "invalid_batch_spec")
        name = item.get("name")
        if not isinstance(name, str) or _IDENT.fullmatch(name) is None or name in seen:
            raise DomainError("Each spec requires a unique ASCII identifier name.", "invalid_batch_spec")
        seen.add(name)
        metric = item.get("metric", "max")
        if metric not in SUPPORTED_TRANSIENT_METRICS:
            raise DomainError(
                f"spec metric '{metric}' is unsupported; expected one of {sorted(SUPPORTED_TRANSIENT_METRICS)}.",
                "invalid_batch_spec",
            )
        signal = item.get("signal")
        if signal is not None and (not isinstance(signal, str) or not signal.strip()):
            raise DomainError("spec signal must be a non-empty string or null.", "invalid_batch_spec")
        low = item.get("min")
        high = item.get("max")
        for bname, bval in (("min", low), ("max", high)):
            if bval is not None and (isinstance(bval, bool) or not isinstance(bval, (int, float)) or not math.isfinite(bval)):
                raise DomainError(f"spec {bname} must be a finite number or null.", "invalid_batch_spec")
        if low is not None and high is not None and float(low) > float(high):
            raise DomainError(f"spec '{name}' min cannot exceed max.", "invalid_batch_spec")
        unit = item.get("unit", "")
        if not isinstance(unit, str) or len(unit) > 24:
            raise DomainError("spec unit must be a short string.", "invalid_batch_spec")
        cleaned.append({
            "name": name,
            "signal": signal,
            "metric": metric,
            "min": float(low) if low is not None else None,
            "max": float(high) if high is not None else None,
            "unit": unit,
        })
    return cleaned


def expand_batch_cases(
    *,
    kind: str,
    process_corners: list[str] | None = None,
    voltages: list[float] | None = None,
    temperatures: list[float] | None = None,
    voltage_param: str = "vdd",
    mc_samples: int = 0,
    mc_base_seed: int = 1001,
    mc_variation: str = "all",
    mc_seeds: list[int] | None = None,
) -> list[dict]:
    """Deterministically expand a PVT, Monte Carlo, or hybrid PVT x MC matrix.

    Every expanded case carries an immutable ``case_id``, explicit PVT coordinates,
    and (when Monte Carlo is active) explicit ``sample_index`` and ``mc_seed``.
    """
    if kind not in ("pvt", "monte_carlo", "pvt_mc"):
        raise DomainError("Batch kind must be 'pvt', 'monte_carlo', or 'pvt_mc'.", "invalid_batch_spec")
    if not isinstance(voltage_param, str) or _IDENT.fullmatch(voltage_param) is None:
        raise DomainError("voltage_param must be a valid parameter identifier.", "invalid_batch_spec")
    if mc_variation not in ("all", "mismatch", "process"):
        raise DomainError("mc_variation must be 'all', 'mismatch', or 'process'.", "invalid_batch_spec")

    corners = list(process_corners) if process_corners else ["tt"]
    if not 1 <= len(corners) <= 32 or len(set(corners)) != len(corners):
        raise DomainError("process_corners must contain 1–32 distinct corner names.", "invalid_batch_spec")
    for c in corners:
        if not isinstance(c, str) or _CORNER_NAME.fullmatch(c) is None:
            raise DomainError(f"Invalid process corner name: {c!r}.", "invalid_batch_spec")

    volts = [float(v) for v in (voltages if voltages else [1.2])]
    if not 1 <= len(volts) <= 32 or any(not math.isfinite(v) or v <= 0 for v in volts):
        raise DomainError("voltages must contain 1–32 positive finite numbers.", "invalid_batch_spec")

    temps = [float(t) for t in (temperatures if temperatures else [27.0])]
    if not 1 <= len(temps) <= 32 or any(not math.isfinite(t) or not -273.0 < t < 500.0 for t in temps):
        raise DomainError("temperatures must contain 1–32 finite °C values within (-273, 500).", "invalid_batch_spec")

    if kind == "pvt":
        mc_list: list[tuple[int | None, int | None]] = [(None, None)]
    else:
        if mc_seeds is not None:
            if not isinstance(mc_seeds, list) or not 1 <= len(mc_seeds) <= MAX_BATCH_CASES:
                raise DomainError(f"mc_seeds must contain 1–{MAX_BATCH_CASES} integers.", "invalid_batch_spec")
            if any(type(s) is not int or s < 0 or s > 2**31 - 1 for s in mc_seeds) or len(set(mc_seeds)) != len(mc_seeds):
                raise DomainError("mc_seeds must be distinct non-negative 32-bit integers.", "invalid_batch_spec")
            mc_list = [(idx, seed) for idx, seed in enumerate(mc_seeds)]
        else:
            if type(mc_samples) is not int or not 1 <= mc_samples <= MAX_BATCH_CASES:
                raise DomainError(f"mc_samples must be an integer between 1 and {MAX_BATCH_CASES}.", "invalid_batch_spec")
            if type(mc_base_seed) is not int or not 0 <= mc_base_seed <= 2**31 - MAX_BATCH_CASES:
                raise DomainError("mc_base_seed must be a non-negative 32-bit integer.", "invalid_batch_spec")
            mc_list = [(idx, mc_base_seed + idx) for idx in range(mc_samples)]

    total = len(corners) * len(volts) * len(temps) * len(mc_list)
    if total < 1 or total > MAX_BATCH_CASES:
        raise DomainError(
            f"Batch matrix has {total} cases, exceeding the bounded limit of {MAX_BATCH_CASES} cases.",
            "batch_limit_exceeded",
        )

    cases: list[dict] = []
    seq = 0
    for c in corners:
        for v in volts:
            for t in temps:
                v_tag = f"{v:.3g}".replace(".", "p").replace("-", "m")
                t_tag = f"{int(round(t))}" if abs(t - round(t)) < 1e-6 else f"{t:.2g}".replace(".", "p")
                t_tag = t_tag.replace("-", "m")
                for s_idx, seed in mc_list:
                    seq += 1
                    if kind == "monte_carlo":
                        case_id = f"mc-{s_idx + 1:04d}-s{seed}"
                        label = f"MC #{s_idx + 1} (seed={seed}, {c}/{v:g}V/{t:g}°C)"
                    elif kind == "pvt":
                        case_id = f"pvt-{c}-v{v_tag}-t{t_tag}"
                        label = f"{c} / {v:g}V / {t:g}°C"
                    else:
                        case_id = f"pvt-{c}-v{v_tag}-t{t_tag}-mc{s_idx + 1:03d}"
                        label = f"{c} / {v:g}V / {t:g}°C · MC #{s_idx + 1} (seed={seed})"
                    cases.append({
                        "case_index": seq - 1,
                        "case_id": case_id,
                        "label": label,
                        "process_corner": c,
                        "voltage": v,
                        "voltage_param": voltage_param,
                        "temperature": t,
                        "sample_index": s_idx,
                        "mc_seed": seed,
                        "mc_variation": mc_variation if seed is not None else None,
                    })
    return cases


def render_case_netlist(
    template_text: str,
    case: dict,
    *,
    simulator: str = "spectre",
) -> str:
    """Bind one PVT/MC case deterministically into a netlist template.

    Replaces explicit ``{{CORNER}}``, ``{{VDD}}``, ``{{TEMP}}``, ``{{MC_SEED}}``,
    and ``{{SAMPLE_ID}}`` placeholders if present, and appends a clear parameter
    header block if placeholders were not used so simulators receive exact PVT
    and seed settings without mutating the original template.
    """
    if not isinstance(template_text, str) or not template_text.strip():
        raise DomainError("Netlist template must be non-empty.", "invalid_batch_spec")
    out = template_text
    used_tokens = False
    replacements = {
        "{{CORNER}}": str(case["process_corner"]),
        "{{VDD}}": f"{case['voltage']:g}",
        "{{TEMP}}": f"{case['temperature']:g}",
        "{{SAMPLE_ID}}": str(case["case_id"]),
    }
    if case.get("mc_seed") is not None:
        replacements["{{MC_SEED}}"] = str(case["mc_seed"])
    for token, val in replacements.items():
        if token in out:
            out = out.replace(token, val)
            used_tokens = True

    if used_tokens:
        return out

    vparam = case.get("voltage_param") or "vdd"
    if simulator == "ngspice":
        header_lines = [
            f"* SpiceOwl batch case {case['case_id']} (corner={case['process_corner']})",
            f".param {vparam}={case['voltage']:g}",
            f".option temp={case['temperature']:g}",
        ]
        if case.get("mc_seed") is not None:
            header_lines.append(f".param mc_seed={case['mc_seed']}")
        header = "\n".join(header_lines) + "\n"
        lines = out.splitlines(keepends=True)
        if lines:
            return lines[0] + header + "".join(lines[1:])
        return header + out

    # Spectre default: inject after simulator lang=spectre if present
    header_lines = [
        f"// SpiceOwl batch case {case['case_id']} (corner={case['process_corner']})",
        f"parameters {vparam}={case['voltage']:g} temp_c={case['temperature']:g}"
        + (f" mc_seed={case['mc_seed']}" if case.get("mc_seed") is not None else ""),
        f"spiceowl_case_opts options temp={case['temperature']:g}",
    ]
    header = "\n".join(header_lines) + "\n"
    lines = out.splitlines(keepends=True)
    for idx, line in enumerate(lines[:10]):
        if line.strip().lower().startswith("simulator lang="):
            return "".join(lines[:idx + 1]) + header + "".join(lines[idx + 1:])
    return header + out


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return math.nan
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * (p / 100.0)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def compute_metric_statistics(
    values: list[float],
    *,
    spec_min: float | None = None,
    spec_max: float | None = None,
) -> dict:
    """Compute finite distribution stats (mean, std, quantiles, Cpk) over valid samples."""
    clean = sorted(float(v) for v in values if isinstance(v, (int, float)) and math.isfinite(v))
    n = len(clean)
    if n == 0:
        return {
            "count": 0, "mean": None, "std": None,
            "min": None, "p5": None, "p50": None, "p95": None, "max": None,
            "cpk_lower": None, "cpk_upper": None, "cpk": None,
        }
    mean = sum(clean) / n
    var = sum((x - mean) ** 2 for x in clean) / (n - 1) if n > 1 else 0.0
    std = math.sqrt(var)
    cpk_l = ((mean - spec_min) / (3.0 * std)) if (spec_min is not None and std > 0) else None
    cpk_u = ((spec_max - mean) / (3.0 * std)) if (spec_max is not None and std > 0) else None
    cpks = [c for c in (cpk_l, cpk_u) if c is not None]
    return {
        "count": n,
        "mean": mean,
        "std": std,
        "min": clean[0],
        "p5": _percentile(clean, 5.0),
        "p50": _percentile(clean, 50.0),
        "p95": _percentile(clean, 95.0),
        "max": clean[-1],
        "cpk_lower": cpk_l,
        "cpk_upper": cpk_u,
        "cpk": min(cpks) if cpks else None,
    }


def _evaluate_specs_on_metrics(metrics: dict[str, float], specs: list[dict]) -> tuple[str, list[dict]]:
    """Evaluate declared acceptance specs against exact extracted metrics."""
    if not specs:
        return "not_evaluated", []
    checks = []
    any_missing = False
    any_fail = False
    for sp in specs:
        name = sp["name"]
        val = metrics.get(name)
        if val is None or not isinstance(val, (int, float)) or not math.isfinite(val):
            any_missing = True
            checks.append({"name": name, "value": None, "min": sp["min"], "max": sp["max"], "unit": sp["unit"], "status": "missing"})
            continue
        ok = True
        if sp["min"] is not None and val < sp["min"]:
            ok = False
        if sp["max"] is not None and val > sp["max"]:
            ok = False
        if not ok:
            any_fail = True
        checks.append({
            "name": name,
            "value": float(val),
            "min": sp["min"],
            "max": sp["max"],
            "unit": sp["unit"],
            "status": "pass" if ok else "fail",
        })
    if any_fail:
        return "failed_spec", checks
    if any_missing:
        return "measurement_incomplete", checks
    return "passed", checks


def _extract_metrics_from_local_waveforms(manager: DirectExecutionManager, run_id: str, specs: list[dict]) -> dict[str, float]:
    """Extract strictly lossless metrics from collected local waveforms for declared specs."""
    if not specs:
        return {}
    needed_signals = sorted({sp["signal"] for sp in specs if sp.get("signal")})
    if not needed_signals:
        return {}
    try:
        idx = manager.waveform_index(run_id, allow_remote=False)
    except DomainError:
        return {}
    datasets = idx.get("datasets") or []
    if not datasets:
        return {}
    # Find the first local dataset that contains any requested signal
    extracted: dict[str, float] = {}
    for ds in datasets:
        if ds.get("source") != "local_collected":
            continue
        avail = {s["name"] for s in ds.get("signals", [])}
        matching = [s for s in needed_signals if s in avail]
        if not matching:
            continue
        try:
            q = manager.waveform_query(run_id, ds["dataset"], matching, mode="exact", allow_remote=False)
        except DomainError:
            continue
        if not q.get("exact") or not q.get("measurement_safe"):
            continue
        measured_cache: dict[str, dict] = {}
        xs = q.get("x") or []
        for sp in specs:
            sig = sp.get("signal")
            if sig not in matching or sp["name"] in extracted:
                continue
            vals = q["signals"][sig]["values"]
            reals = [float(v) for v in vals if isinstance(v, (int, float)) and math.isfinite(v)]
            if not reals:
                continue
            m = sp["metric"]
            if m == "max":
                extracted[sp["name"]] = max(reals)
            elif m == "min":
                extracted[sp["name"]] = min(reals)
            elif m == "peak_to_peak":
                extracted[sp["name"]] = max(reals) - min(reals)
            elif m == "avg":
                extracted[sp["name"]] = sum(reals) / len(reals)
            elif m in ("end", "value"):
                extracted[sp["name"]] = reals[-1]
            else:
                if sig not in measured_cache and len(xs) == len(vals) and len(reals) == len(vals) and len(xs) >= 2:
                    try:
                        measured_cache[sig] = measure_transient_signal(
                            xs,
                            reals,
                            signal_name=sig,
                            exact=True,
                            measurement_safe=True,
                        )
                    except DomainError:
                        measured_cache[sig] = {}
                val = extract_named_metric(measured_cache.get(sig, {}), m)
                if val is not None:
                    extracted[sp["name"]] = val
    return extracted


class DirectBatchManager:
    """Durable PVT / Corner / Monte Carlo batch manager built on DirectExecutionManager."""

    def __init__(self, data_dir: str | Path, direct_manager: DirectExecutionManager):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.root = self.data_dir / "direct_batches"
        self.direct = direct_manager

    def path(self, batch_id: str) -> Path:
        validated = validate_batch_id(batch_id)
        target = self.root / validated
        if target.is_symlink():
            raise DomainError("Batch directory cannot be a symlink.", "batch_state_invalid")
        return target

    def _index_path(self) -> Path:
        return self.root / "index.json"

    def _update_index(self, batch: dict, summary: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with _locked(self.root / "index.lock"):
            idx_path = self._index_path()
            if idx_path.is_file() and not idx_path.is_symlink():
                idx = _read_json(idx_path)
            else:
                idx = {"schema": BATCH_INDEX_SCHEMA, "entries": {}}
            counts = summary["counts"]
            idx["entries"][batch["batch_id"]] = {
                "batch_id": batch["batch_id"],
                "title": batch.get("title") or batch["batch_id"],
                "kind": batch["kind"],
                "synthetic": bool(batch.get("synthetic", False)),
                "backend": batch["backend"],
                "simulator": batch["simulator"],
                "max_concurrency": batch["max_concurrency"],
                "status": summary["status"],
                "total_cases": counts["total"],
                "completed_cases": counts["completed"],
                "passed_cases": counts["passed"],
                "spec_failed_cases": counts["failed_spec"],
                "exec_failed_cases": counts["execution_failed"],
                "missing_cases": counts["missing_or_uncollected"],
                "active_cases": counts["active"],
                "pending_cases": counts["pending"],
                "updated_at": summary["updated_at"],
                "created_at": batch["created_at"],
                "project_name": batch.get("project_name"),
                "library_name": batch.get("library_name"),
                "cell_name": batch.get("cell_name"),
                "view_name": batch.get("view_name"),
            }
            _atomic_write_json(idx_path, idx)

    def run_batch_map(self) -> dict[str, dict]:
        """Map child run_id -> batch & case metadata for unified history archiving."""
        mapping: dict[str, dict] = {}
        if not self.root.is_dir():
            return mapping
        for entry in self.root.iterdir():
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            bfile = entry / "batch.json"
            if not bfile.is_file() or bfile.is_symlink():
                continue
            try:
                batch = _read_json(bfile)
            except DomainError:
                continue
            bid = batch.get("batch_id")
            if not bid:
                continue
            base = {
                "batch_id": bid,
                "title": batch.get("title") or bid,
                "kind": batch.get("kind"),
                "project_name": batch.get("project_name"),
                "library_name": batch.get("library_name"),
                "cell_name": batch.get("cell_name"),
                "view_name": batch.get("view_name"),
            }
            for case in batch.get("cases") or []:
                rid = case.get("run_id")
                if rid:
                    mapping[rid] = {**base, "case_label": case.get("label") or case.get("case_id")}
        return mapping

    def list(self) -> list[dict]:
        idx_path = self._index_path()
        if not idx_path.is_file() or idx_path.is_symlink():
            return []
        idx = _read_json(idx_path)
        entries = list((idx.get("entries") or {}).values())
        entries.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
        return entries

    def read(self, batch_id: str) -> dict:
        batch_id = validate_batch_id(batch_id)
        return deepcopy(_read_json(self.path(batch_id) / "batch.json"))

    def prepare(
        self,
        *,
        batch_id: str,
        netlist_template: str,
        kind: str = "pvt",
        title: str | None = None,
        process_corners: list[str] | None = None,
        voltages: list[float] | None = None,
        temperatures: list[float] | None = None,
        voltage_param: str = "vdd",
        mc_samples: int = 0,
        mc_base_seed: int = 1001,
        mc_variation: str = "all",
        mc_seeds: list[int] | None = None,
        specs: list[dict] | None = None,
        max_concurrency: int = 4,
        host_id: str | None = None,
        host_ids: list[str] | None = None,
        backend: str = "ssh_direct",
        simulator: str = "spectre",
        output_format: str = "psfbin",
        limits: dict | None = None,
        scheduler: dict | None = None,
        include_dirs: list[str] | None = None,
        remote_python: str = "/usr/bin/python3",
        remote_root: str = ".spiceowl-runs",
        recovery_policy: dict | None = None,
        project_name: str | None = None,
        library_name: str | None = None,
        cell_name: str | None = None,
        view_name: str | None = None,
        source_origin: str | None = None,
    ) -> dict:
        """Pure local batch preparation: expands PVT/MC cases and freezes each child run."""
        batch_id = validate_batch_id(batch_id)
        if type(max_concurrency) is not int or not 1 <= max_concurrency <= MAX_CONCURRENCY:
            raise DomainError(f"max_concurrency must be an integer between 1 and {MAX_CONCURRENCY}.", "invalid_batch_spec")
        if (self.path(batch_id) / "batch.json").exists():
            raise DomainError(f"Batch '{batch_id}' already exists. Choose a new batch_id.", "batch_exists")

        validated_specs = _validate_specs(specs)
        cases = expand_batch_cases(
            kind=kind,
            process_corners=process_corners,
            voltages=voltages,
            temperatures=temperatures,
            voltage_param=voltage_param,
            mc_samples=mc_samples,
            mc_base_seed=mc_base_seed,
            mc_variation=mc_variation,
            mc_seeds=mc_seeds,
        )

        hints = extract_netlist_organization_hints(netlist_template)
        resolved_lib = (library_name or "").strip()[:160] or hints.get("library_name")
        resolved_proj = (project_name or "").strip()[:160] or resolved_lib or hints.get("project_name")
        resolved_cell = (cell_name or "").strip()[:160] or hints.get("cell_name")
        resolved_view = (view_name or "").strip()[:120] or hints.get("view_name")
        resolved_title = (title or batch_id).strip()[:160]

        template_sha = hashlib.sha256(netlist_template.encode("utf-8")).hexdigest()
        prepared_cases: list[dict] = []
        for case in cases:
            case_netlist = render_case_netlist(netlist_template, case, simulator=simulator)
            run_key = f"{batch_id}-{case['case_id']}"
            if len(run_key) > 128:
                run_key = f"{batch_id[:48]}-c{case['case_index'] + 1:04d}-{hashlib.sha256(run_key.encode()).hexdigest()[:8]}"
            child = self.direct.prepare(
                run_key=run_key,
                netlist_text=case_netlist,
                host_id=host_id,
                host_ids=host_ids,
                backend=backend,
                simulator=simulator,
                output_format=output_format,
                limits=limits,
                scheduler=scheduler,
                include_dirs=include_dirs,
                remote_python=remote_python,
                remote_root=remote_root,
                recovery_policy=recovery_policy,
                project_name=resolved_proj,
                library_name=resolved_lib,
                cell_name=resolved_cell,
                view_name=resolved_view,
                submission_group_id=f"batch:{batch_id}",
                submission_label=resolved_title,
                source_origin=source_origin or "batch",
                batch_id=batch_id,
            )
            prepared_cases.append({
                **case,
                "run_id": child["run_id"],
                "host_id": child["host_id"],
                "netlist_sha256": hashlib.sha256(case_netlist.encode("utf-8")).hexdigest(),
                "manifest_sha256": child["manifest_sha256"],
                "batch_cancelled": False,
                "metrics": {},
                "spec_status": "not_evaluated",
                "spec_checks": [],
            })

        now = utcnow()
        batch_record = {
            "schema": BATCH_SCHEMA,
            "batch_id": batch_id,
            "title": resolved_title,
            "kind": kind,
            "synthetic": False,
            "project_name": resolved_proj,
            "library_name": resolved_lib,
            "cell_name": resolved_cell,
            "view_name": resolved_view,
            "backend": backend,
            "simulator": simulator,
            "output_format": output_format,
            "max_concurrency": max_concurrency,
            "template_sha256": template_sha,
            "matrix": {
                "process_corners": list(process_corners) if process_corners else ["tt"],
                "voltages": [float(v) for v in (voltages if voltages else [1.2])],
                "temperatures": [float(t) for t in (temperatures if temperatures else [27.0])],
                "voltage_param": voltage_param,
                "mc_samples": len({c["sample_index"] for c in cases if c["sample_index"] is not None}),
                "mc_base_seed": mc_base_seed if kind != "pvt" else None,
                "mc_variation": mc_variation if kind != "pvt" else None,
            },
            "specs": validated_specs,
            "cases": prepared_cases,
            "created_at": now,
            "updated_at": now,
        }
        bdir = self.path(batch_id)
        bdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        _atomic_write_json(bdir / "batch.json", batch_record)
        summary = self.build_summary(batch_id)
        return {"batch": self.read(batch_id), "summary": summary}

    def _case_runtime_snapshot(self, case: dict, specs: list[dict]) -> dict:
        run_id = case["run_id"]
        try:
            rec = self.direct.read(run_id)
        except DomainError:
            rec = None
        if rec is None:
            return {
                **case,
                "run_state": "missing_record",
                "outcome": "missing",
                "failure_reason": "Direct run record missing on disk.",
                "operating_qualification": "not_evaluated",
            }
        rstate = rec.get("state", "unknown")
        coll = rec.get("collection") or {}
        metrics = dict(case.get("metrics") or {})
        if rstate == "simulator_complete_pending_measurements" and not metrics and specs:
            metrics = _extract_metrics_from_local_waveforms(self.direct, run_id, specs)

        try:
            rep = self.direct.report(run_id)
        except DomainError:
            rep = None
        op_qual = (rep or {}).get("operating_qualification", {}).get("status", "not_evaluated")

        if case.get("batch_cancelled") and rstate == "prepared":
            outcome = "cancelled"
            failure_reason = "Cancelled before dispatch."
            spec_status, spec_checks = "not_evaluated", []
        elif rstate == "prepared":
            outcome = "pending"
            failure_reason = None
            spec_status, spec_checks = "not_evaluated", []
        elif rstate in _ACTIVE_RUN_STATES:
            outcome = "active"
            failure_reason = (rec.get("last_error") or {}).get("message")
            spec_status, spec_checks = "not_evaluated", []
        elif rstate == "simulator_complete_pending_measurements":
            if rec.get("collection_phase") != "complete" and not metrics:
                outcome = "missing"
                failure_reason = "Execution completed but artifacts not collected yet."
                spec_status, spec_checks = "not_evaluated", []
            else:
                spec_status, spec_checks = _evaluate_specs_on_metrics(metrics, specs)
                if spec_status == "failed_spec":
                    outcome = "failed_spec"
                    failed_names = [c["name"] for c in spec_checks if c["status"] == "fail"]
                    failure_reason = f"Valid measurement missed spec: {', '.join(failed_names)}."
                elif spec_status == "measurement_incomplete":
                    outcome = "missing"
                    failure_reason = "Required measurement metric missing from collected results."
                else:
                    outcome = "passed"
                    failure_reason = None
        else:
            outcome = "execution_failed"
            oom = (rec.get("oom_evidence") or {}).get("oom_kill")
            err = (rec.get("last_error") or {}).get("message") or (rec.get("last_error") or {}).get("code")
            failure_reason = "Confirmed OOM (out_of_memory)" if oom else (err or rstate)
            spec_status, spec_checks = "not_evaluated", []

        return {
            **case,
            "run_state": rstate,
            "host_id": rec.get("host_id", case.get("host_id")),
            "scheduler_job_id": (rec.get("scheduler") or {}).get("job_id"),
            "collection_phase": rec.get("collection_phase"),
            "collection_complete": coll.get("collection_complete"),
            "outcome": outcome,
            "failure_reason": failure_reason,
            "metrics": metrics,
            "spec_status": spec_status,
            "spec_checks": spec_checks,
            "operating_qualification": op_qual,
            "updated_at": rec.get("updated_at"),
        }

    def build_summary(self, batch_id: str) -> dict:
        """Compute & persist full PVT/MC summary JSON + Markdown report + CSV export."""
        batch_id = validate_batch_id(batch_id)
        batch = self.read(batch_id)
        specs = batch.get("specs") or []
        evaluated_cases = [self._case_runtime_snapshot(c, specs) for c in batch["cases"]]

        counts = {
            "total": len(evaluated_cases),
            "pending": 0,
            "active": 0,
            "completed": 0,
            "passed": 0,
            "failed_spec": 0,
            "execution_failed": 0,
            "missing_or_uncollected": 0,
            "cancelled": 0,
        }
        missing_or_failed: list[dict] = []
        for row in evaluated_cases:
            o = row["outcome"]
            if o == "pending":
                counts["pending"] += 1
            elif o == "active":
                counts["active"] += 1
            elif o == "passed":
                counts["completed"] += 1
                counts["passed"] += 1
            elif o == "failed_spec":
                counts["completed"] += 1
                counts["failed_spec"] += 1
                missing_or_failed.append({
                    "case_id": row["case_id"], "run_id": row["run_id"],
                    "process_corner": row["process_corner"], "voltage": row["voltage"],
                    "temperature": row["temperature"], "sample_index": row["sample_index"],
                    "mc_seed": row["mc_seed"], "outcome": o, "reason": row["failure_reason"],
                })
            elif o == "execution_failed":
                counts["execution_failed"] += 1
                missing_or_failed.append({
                    "case_id": row["case_id"], "run_id": row["run_id"],
                    "process_corner": row["process_corner"], "voltage": row["voltage"],
                    "temperature": row["temperature"], "sample_index": row["sample_index"],
                    "mc_seed": row["mc_seed"], "outcome": o, "reason": row["failure_reason"],
                })
            elif o == "missing":
                counts["missing_or_uncollected"] += 1
                missing_or_failed.append({
                    "case_id": row["case_id"], "run_id": row["run_id"],
                    "process_corner": row["process_corner"], "voltage": row["voltage"],
                    "temperature": row["temperature"], "sample_index": row["sample_index"],
                    "mc_seed": row["mc_seed"], "outcome": o, "reason": row["failure_reason"],
                })
            elif o == "cancelled":
                counts["cancelled"] += 1

        if counts["active"] > 0:
            batch_status = "running"
        elif counts["pending"] > 0 and (counts["completed"] > 0 or counts["execution_failed"] > 0):
            batch_status = "partially_dispatched"
        elif counts["pending"] == counts["total"] and counts["total"] > 0:
            batch_status = "prepared"
        elif counts["cancelled"] > 0 and counts["completed"] == 0:
            batch_status = "cancelled"
        elif counts["execution_failed"] > 0 or counts["missing_or_uncollected"] > 0:
            batch_status = "completed_with_failures"
        elif counts["failed_spec"] > 0:
            batch_status = "completed_spec_misses"
        else:
            batch_status = "completed_all_passed"

        # Aggregate metric statistics across all valid completed cases (preserving missing/failed counts!)
        metric_names = [sp["name"] for sp in specs]
        for row in evaluated_cases:
            for k in row.get("metrics", {}):
                if k not in metric_names:
                    metric_names.append(k)
        spec_by_name = {sp["name"]: sp for sp in specs}

        metric_stats: dict[str, dict] = {}
        worst_corners: dict[str, dict] = {}
        for mname in metric_names:
            sp = spec_by_name.get(mname, {})
            valid_pairs = [
                (row, float(row["metrics"][mname]))
                for row in evaluated_cases
                if row["outcome"] in ("passed", "failed_spec")
                and mname in row.get("metrics", {})
                and isinstance(row["metrics"][mname], (int, float))
                and math.isfinite(row["metrics"][mname])
            ]
            st = compute_metric_statistics(
                [v for _, v in valid_pairs],
                spec_min=sp.get("min"),
                spec_max=sp.get("max"),
            )
            st["unit"] = sp.get("unit", "")
            st["spec_min"] = sp.get("min")
            st["spec_max"] = sp.get("max")
            st["missing_or_failed_count"] = counts["total"] - len(valid_pairs)
            metric_stats[mname] = st
            if valid_pairs:
                min_row, min_v = min(valid_pairs, key=lambda kv: kv[1])
                max_row, max_v = max(valid_pairs, key=lambda kv: kv[1])
                worst_corners[mname] = {
                    "min_case": {"case_id": min_row["case_id"], "run_id": min_row["run_id"], "label": min_row["label"], "value": min_v},
                    "max_case": {"case_id": max_row["case_id"], "run_id": max_row["run_id"], "label": max_row["label"], "value": max_v},
                }

        # Conservative yield: denominator is ALWAYS total_cases (failed/missing NEVER silently dropped!)
        denom = max(1, counts["total"])
        yield_summary = {
            "total_samples": counts["total"],
            "valid_measured_samples": counts["completed"],
            "passed_samples": counts["passed"],
            "spec_failed_samples": counts["failed_spec"],
            "execution_failed_samples": counts["execution_failed"],
            "missing_or_uncollected_samples": counts["missing_or_uncollected"],
            "conservative_yield_pct": round(100.0 * counts["passed"] / denom, 2),
            "measured_coverage_pct": round(100.0 * counts["completed"] / denom, 2),
            "conditional_yield_on_measured_pct": round(100.0 * counts["passed"] / counts["completed"], 2) if counts["completed"] > 0 else None,
            "note": "Conservative yield keeps execution-failed and missing samples in the denominator; seed and sample identities are never dropped.",
        }

        now = utcnow()
        summary = {
            "schema": BATCH_SUMMARY_SCHEMA,
            "batch_id": batch_id,
            "title": batch["title"],
            "kind": batch["kind"],
            "synthetic": bool(batch.get("synthetic", False)),
            "status": batch_status,
            "max_concurrency": batch["max_concurrency"],
            "counts": counts,
            "yield": yield_summary,
            "specs": specs,
            "metric_stats": metric_stats,
            "worst_corners": worst_corners,
            "missing_or_failed_samples": missing_or_failed,
            "cases": evaluated_cases,
            "updated_at": now,
        }

        bdir = self.path(batch_id)
        md_text = render_batch_markdown(batch, summary)
        csv_text = render_batch_csv(summary)
        summary["markdown_path"] = str(bdir / "summary.md")
        summary["csv_path"] = str(bdir / "summary.csv")
        _atomic_write_json(bdir / "summary.json", summary)
        _atomic_write_text(bdir / "summary.md", md_text)
        _atomic_write_text(bdir / "summary.csv", csv_text)
        self._update_index(batch, summary)
        return summary

    def report(self, batch_id: str) -> dict:
        batch_id = validate_batch_id(batch_id)
        summary = self.build_summary(batch_id)
        bdir = self.path(batch_id)
        return {
            "batch": self.read(batch_id),
            "summary": summary,
            "markdown": (bdir / "summary.md").read_text(encoding="utf-8"),
            "csv": (bdir / "summary.csv").read_text(encoding="utf-8"),
        }

    def dispatch_next(
        self,
        batch_id: str,
        *,
        limit: int | None = None,
        recheck_resources: bool | None = None,
    ) -> dict:
        """Stage and launch pending cases up to ``max_concurrency`` without exceeding the budget."""
        batch_id = validate_batch_id(batch_id)
        if limit is not None and (type(limit) is not int or limit < 1):
            raise DomainError("limit must be a positive integer.", "invalid_batch_spec")
        with _locked(self.path(batch_id) / "batch.lock"):
            batch = self.read(batch_id)
            max_conc = int(batch["max_concurrency"])
            active_count = 0
            pending_cases = []
            for case in batch["cases"]:
                if case.get("batch_cancelled"):
                    continue
                rec = self.direct.read(case["run_id"])
                st = rec.get("state")
                if st in _ACTIVE_RUN_STATES:
                    active_count += 1
                elif st == "prepared":
                    pending_cases.append(case)
            slots = max(0, max_conc - active_count)
            if limit is not None:
                slots = min(slots, limit)

            dispatched = []
            errors = []
            for case in pending_cases[:slots]:
                rid = case["run_id"]
                try:
                    staged = self.direct.stage(rid)
                    if staged.get("state") == "staged":
                        launched = self.direct.launch(rid, recheck_resources=recheck_resources)
                        dispatched.append({"case_id": case["case_id"], "run_id": rid, "state": launched.get("state")})
                    else:
                        errors.append({"case_id": case["case_id"], "run_id": rid, "state": staged.get("state"), "reason": "stage_not_ready"})
                except DomainError as exc:
                    errors.append({"case_id": case["case_id"], "run_id": rid, "code": exc.code, "reason": str(exc)})

        summary = self.build_summary(batch_id)
        return {
            "batch_id": batch_id,
            "max_concurrency": max_conc,
            "active_before": active_count,
            "slots_available": slots,
            "dispatched": dispatched,
            "errors": errors,
            "summary": summary,
        }

    def refresh(
        self,
        batch_id: str,
        *,
        auto_collect: bool = True,
        dispatch_more: bool = False,
        recheck_resources: bool | None = None,
    ) -> dict:
        """Poll active cases, collect newly terminal cases, and optionally refill concurrency slots."""
        batch_id = validate_batch_id(batch_id)
        batch = self.read(batch_id)
        polled = []
        collected = []
        errors = []
        for case in batch["cases"]:
            if case.get("batch_cancelled"):
                continue
            rid = case["run_id"]
            rec = self.direct.read(rid)
            st = rec.get("state")
            if st in _ACTIVE_RUN_STATES:
                try:
                    updated = self.direct.status(rid)
                    polled.append({"case_id": case["case_id"], "run_id": rid, "state": updated.get("state")})
                    st = updated.get("state")
                    rec = updated
                except DomainError as exc:
                    errors.append({"case_id": case["case_id"], "run_id": rid, "code": exc.code, "reason": str(exc)})
            if auto_collect and st in _TERMINAL_RUN_STATES and rec.get("collection_phase") != "complete":
                try:
                    col = self.direct.collect(rid)
                    collected.append({"case_id": case["case_id"], "run_id": rid, "state": col.get("state")})
                except DomainError as exc:
                    errors.append({"case_id": case["case_id"], "run_id": rid, "code": exc.code, "reason": str(exc)})

        dispatch_result = None
        if dispatch_more:
            dispatch_result = self.dispatch_next(batch_id, recheck_resources=recheck_resources)
        summary = dispatch_result["summary"] if dispatch_result else self.build_summary(batch_id)
        return {
            "batch_id": batch_id,
            "polled": polled,
            "collected": collected,
            "dispatched": dispatch_result["dispatched"] if dispatch_result else [],
            "errors": errors,
            "summary": summary,
        }

    def cancel(self, batch_id: str) -> dict:
        """Cancel active remote cases via verified cancellation and prevent queued cases from launching."""
        batch_id = validate_batch_id(batch_id)
        cancelled_active = []
        cancelled_pending = []
        errors = []
        with _locked(self.path(batch_id) / "batch.lock"):
            batch = self.read(batch_id)
            for case in batch["cases"]:
                rid = case["run_id"]
                rec = self.direct.read(rid)
                st = rec.get("state")
                if st == "prepared":
                    if not case.get("batch_cancelled"):
                        case["batch_cancelled"] = True
                        cancelled_pending.append({"case_id": case["case_id"], "run_id": rid})
                elif st in _ACTIVE_RUN_STATES and rec.get("dispatch_claimed"):
                    try:
                        res = self.direct.cancel(rid)
                        cancelled_active.append({"case_id": case["case_id"], "run_id": rid, "outcome": res.get("outcome")})
                    except DomainError as exc:
                        errors.append({"case_id": case["case_id"], "run_id": rid, "code": exc.code, "reason": str(exc)})
            batch["updated_at"] = utcnow()
            _atomic_write_json(self.path(batch_id) / "batch.json", batch)
        summary = self.build_summary(batch_id)
        return {
            "batch_id": batch_id,
            "cancelled_active": cancelled_active,
            "cancelled_pending": cancelled_pending,
            "errors": errors,
            "summary": summary,
        }

    def create_demo_batch(self, *, kind: str = "pvt", batch_id: str | None = None) -> dict:
        """Create an idempotent synthetic PVT (27-corner) or Monte Carlo (40-sample) demo batch."""
        if kind not in ("pvt", "monte_carlo"):
            raise DomainError("Demo batch kind must be 'pvt' or 'monte_carlo'.", "invalid_batch_spec")
        bid = validate_batch_id(batch_id or ("demo-pvt-27" if kind == "pvt" else "demo-mc-40"))
        if (self.path(bid) / "batch.json").is_file():
            return self.report(bid)

        now = utcnow()
        if kind == "pvt":
            corners = ["tt", "ss", "ff"]
            volts = [1.08, 1.20, 1.32]
            temps = [-40.0, 27.0, 125.0]
            cases = expand_batch_cases(
                kind="pvt", process_corners=corners, voltages=volts, temperatures=temps,
            )
            specs = [
                {"name": "gain_db", "signal": None, "metric": "value", "min": 58.0, "max": None, "unit": "dB"},
                {"name": "pm_deg", "signal": None, "metric": "value", "min": 55.0, "max": None, "unit": "deg"},
                {"name": "vout_settle_v", "signal": "v(out)", "metric": "end", "min": 0.84, "max": 0.96, "unit": "V"},
            ]
        else:
            corners = ["tt"]
            volts = [1.20]
            temps = [27.0]
            cases = expand_batch_cases(
                kind="monte_carlo", process_corners=corners, voltages=volts, temperatures=temps,
                mc_samples=40, mc_base_seed=2001, mc_variation="mismatch",
            )
            specs = [
                {"name": "vos_mv", "signal": None, "metric": "value", "min": -4.5, "max": 4.5, "unit": "mV"},
                {"name": "vout_settle_v", "signal": "v(out)", "metric": "end", "min": 0.86, "max": 0.94, "unit": "V"},
            ]

        rng = random.Random(20261125 if kind == "pvt" else 2001)
        stored_cases: list[dict] = []
        for idx, case in enumerate(cases):
            run_id = f"{bid}-{case['case_id']}"
            run_dir = self.direct.path(run_id)
            run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

            is_oom = (kind == "pvt" and case["process_corner"] == "ff" and abs(case["voltage"] - 1.32) < 1e-6 and abs(case["temperature"] + 40.0) < 1e-6)
            is_mc_exec_fail = (kind == "monte_carlo" and case["mc_seed"] == 2029)

            if is_oom or is_mc_exec_fail:
                record = {
                    "schema": "spiceowl.direct-run.v1",
                    "run_id": run_id, "run_key": run_id, "submission_key": run_id,
                    "origin_submission_key": run_id, "root_run_id": run_id,
                    "parent_run_id": None, "attempt_number": 1, "child_run_ids": [],
                    "synthetic": True, "backend": "slurm" if is_oom else "ssh_direct",
                    "simulator": "spectre", "output_format": "psfbin",
                    "host_id": "synthetic-demo-host", "expected_host": "synthetic-demo-node", "expected_user": "demo",
                    "state": "simulator_failed", "remote_state": "simulator_failed",
                    "status_transport": "connected", "simulator_exit_code": 137 if is_oom else 1,
                    "dispatch_claimed": True, "collection_phase": "complete",
                    "oom_evidence": {"source": "slurm_sacct", "run_bound": True, "oom_kill": True} if is_oom else None,
                    "last_error": {
                        "code": "confirmed_oom" if is_oom else "license_unavailable",
                        "message": "Slurm sacct confirmed OUT_OF_MEMORY on ff/1.32V/-40C" if is_oom else "License checkout timed out for MC seed 2029",
                    },
                    "created_at": now, "updated_at": now, "observed_at": now,
                    "limits": {"cpu_count": 4, "memory_bytes": 8 * 2**30, "wall_seconds": 600},
                    "manifest_sha256": hashlib.sha256(run_id.encode()).hexdigest(),
                }
                _atomic_write_json(run_dir / "run.json", record)
                stored_cases.append({
                    **case,
                    "run_id": run_id, "host_id": "synthetic-demo-host",
                    "netlist_sha256": record["manifest_sha256"], "manifest_sha256": record["manifest_sha256"],
                    "batch_cancelled": False, "metrics": {}, "spec_status": "not_evaluated", "spec_checks": [],
                })
                continue

            if kind == "pvt":
                c_bias = {"ss": -4.2, "tt": 0.0, "ff": 3.4}[case["process_corner"]]
                v_bias = (case["voltage"] - 1.20) * 18.0
                t_bias = -(case["temperature"] - 27.0) * 0.025
                gain_db = round(65.2 + c_bias + v_bias + t_bias, 2)
                pm_deg = round(66.0 - 0.8 * c_bias + 0.04 * (case["temperature"] - 27.0) - (13.0 if (case["process_corner"] == "ss" and case["voltage"] < 1.1 and case["temperature"] > 100) else 0.0), 2)
                vout_end = round(0.90 + 0.015 * c_bias / 4.0 + (case["voltage"] - 1.20) * 0.18, 4)
                # Force worst slow corner (ss / 1.08V / 125C) to miss spec clearly
                if case["process_corner"] == "ss" and case["voltage"] < 1.1 and case["temperature"] > 100:
                    gain_db = 55.8
                    pm_deg = 51.4
                metrics = {"gain_db": gain_db, "pm_deg": pm_deg, "vout_settle_v": vout_end}
            else:
                seed = int(case["mc_seed"])
                if seed == 2017:
                    vos = 5.42
                elif seed == 2033:
                    vos = -5.18
                else:
                    vos = round(rng.gauss(0.15, 1.55), 3)
                vout_end = round(0.90 + vos * 0.0025 + rng.gauss(0.0, 0.004), 4)
                metrics = {"vos_mv": vos, "vout_settle_v": vout_end}

            # Attach a lightweight PSF Binary waveform for the first 3 cases & spec-failed cases
            # so users can jump straight from the batch table into the waveform viewer!
            raw_files = []
            if idx < 3 or (kind == "pvt" and case["process_corner"] == "ss" and case["voltage"] < 1.1 and case["temperature"] > 100) or (kind == "monte_carlo" and case["mc_seed"] in (2017, 2033)):
                raw_dir = run_dir / "collected" / "results.raw"
                raw_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                xs = [k * 1e-9 for k in range(80)]
                target_v = metrics["vout_settle_v"]
                ys = [target_v * (1.0 - math.exp(-t / 1.2e-8)) + 0.012 * math.sin(2.0 * math.pi * k / 16.0) for k, t in enumerate(xs)]
                psf_path = psf_binary.write_psf_binary(
                    raw_dir / "tran.tran.tran",
                    sweep_name="time", sweep_values=xs,
                    signals={"v(out)": ys}, sweep_unit="s", signal_units={"v(out)": "V"},
                    windowed=False,
                )
                wf_idx = psf_binary.index_waveform_directory(raw_dir)
                for ds in wf_idx["datasets"]:
                    ds["source"] = "local_collected"
                _atomic_write_json(run_dir / "waveform_index.json", wf_idx)
                raw_files.append({"name": "results.raw/tran.tran.tran", "bytes": psf_path.stat().st_size})

            record = {
                "schema": "spiceowl.direct-run.v1",
                "run_id": run_id, "run_key": run_id, "submission_key": run_id,
                "origin_submission_key": run_id, "root_run_id": run_id,
                "parent_run_id": None, "attempt_number": 1, "child_run_ids": [],
                "synthetic": True, "backend": "slurm" if kind == "pvt" else "ssh_direct",
                "simulator": "spectre", "output_format": "psfbin",
                "host_id": "synthetic-demo-host", "expected_host": "synthetic-demo-node", "expected_user": "demo",
                "state": "simulator_complete_pending_measurements",
                "remote_state": "simulator_complete_pending_measurements",
                "status_transport": "connected", "simulator_exit_code": 0,
                "dispatch_claimed": True, "collection_phase": "complete",
                "collection": {
                    "collected_at": now, "directory": str(run_dir / "collected"),
                    "file_count": len(raw_files), "files": raw_files,
                    "skipped_files": [], "collection_complete": True,
                    "total_bytes": sum(f["bytes"] for f in raw_files),
                    "raw_present": bool(raw_files),
                },
                "created_at": now, "updated_at": now, "observed_at": now,
                "limits": {"cpu_count": 4, "memory_bytes": 8 * 2**30, "wall_seconds": 600},
                "manifest_sha256": hashlib.sha256(run_id.encode()).hexdigest(),
            }
            _atomic_write_json(run_dir / "run.json", record)
            stored_cases.append({
                **case,
                "run_id": run_id, "host_id": "synthetic-demo-host",
                "netlist_sha256": record["manifest_sha256"], "manifest_sha256": record["manifest_sha256"],
                "batch_cancelled": False, "metrics": metrics, "spec_status": "not_evaluated", "spec_checks": [],
            })

        batch_record = {
            "schema": BATCH_SCHEMA,
            "batch_id": bid,
            "title": "Synthetic 27-Corner PVT Demo (OpAmp)" if kind == "pvt" else "Synthetic 40-Sample Monte Carlo Demo (Mismatch)",
            "kind": kind,
            "synthetic": True,
            "backend": "slurm" if kind == "pvt" else "ssh_direct",
            "simulator": "spectre",
            "output_format": "psfbin",
            "max_concurrency": 6 if kind == "pvt" else 8,
            "template_sha256": hashlib.sha256(bid.encode()).hexdigest(),
            "matrix": {
                "process_corners": corners,
                "voltages": volts,
                "temperatures": temps,
                "voltage_param": "vdd",
                "mc_samples": 40 if kind == "monte_carlo" else 0,
                "mc_base_seed": 2001 if kind == "monte_carlo" else None,
                "mc_variation": "mismatch" if kind == "monte_carlo" else None,
            },
            "specs": specs,
            "cases": stored_cases,
            "created_at": now,
            "updated_at": now,
        }
        bdir = self.path(bid)
        bdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        _atomic_write_json(bdir / "batch.json", batch_record)
        return self.report(bid)


def render_batch_markdown(batch: dict, summary: dict) -> str:
    """Render a human-readable Markdown PVT / Monte Carlo batch report."""
    c = summary["counts"]
    y = summary["yield"]
    lines = [
        f"# SpiceOwl Batch Experiment Report — `{summary['batch_id']}`",
        "",
        f"- **Title**: {summary['title']}",
        f"- **Kind**: `{summary['kind']}` | **Backend**: `{batch['backend']}` | **Simulator**: `{batch['simulator']}` | **Max Concurrency**: `{summary['max_concurrency']}`",
        f"- **Status**: `{summary['status']}` | **Updated**: `{summary['updated_at']}`",
        f"- **Counts**: Total `{c['total']}` · Passed `{c['passed']}` · Spec Miss `{c['failed_spec']}` · Exec Failed `{c['execution_failed']}` · Missing/Uncollected `{c['missing_or_uncollected']}` · Active `{c['active']}` · Pending `{c['pending']}`",
        f"- **Conservative Yield**: **{y['conservative_yield_pct']}%** (`{y['passed_samples']}/{y['total_samples']}`, coverage `{y['measured_coverage_pct']}%`; failed/missing samples are never dropped from denominator)",
        "",
    ]
    if summary.get("metric_stats"):
        lines.append("## Metric Distribution & Worst-Case Corners")
        lines.append("")
        lines.append("| Metric | Unit | Spec [Min, Max] | Valid / Missing | Mean | Std | Min | P5 | P50 | P95 | Max | Cpk |")
        lines.append("| :--- | :--- | :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for mname, st in summary["metric_stats"].items():
            smin = f"{st['spec_min']:g}" if st["spec_min"] is not None else "—"
            smax = f"{st['spec_max']:g}" if st["spec_max"] is not None else "—"
            fmt = lambda v: f"{v:.4g}" if isinstance(v, (int, float)) else "—"
            lines.append(
                f"| `{mname}` | {st.get('unit') or '—'} | [{smin}, {smax}] | {st['count']} / {st['missing_or_failed_count']} "
                f"| {fmt(st['mean'])} | {fmt(st['std'])} | {fmt(st['min'])} | {fmt(st['p5'])} | {fmt(st['p50'])} "
                f"| {fmt(st['p95'])} | {fmt(st['max'])} | {fmt(st['cpk'])} |"
            )
        lines.append("")
    if summary.get("missing_or_failed_samples"):
        lines.append("## Explicit Failed / Spec-Missed / Missing Samples (Seed & Identity Preserved)")
        lines.append("")
        lines.append("| Case ID | Run ID | Corner | Vdd | Temp (°C) | MC Seed | Outcome | Reason |")
        lines.append("| :--- | :--- | :--- | ---: | ---: | ---: | :--- | :--- |")
        for item in summary["missing_or_failed_samples"]:
            lines.append(
                f"| `{item['case_id']}` | `{item['run_id']}` | {item['process_corner']} | {item['voltage']:g} "
                f"| {item['temperature']:g} | {item['mc_seed'] if item['mc_seed'] is not None else '—'} "
                f"| `{item['outcome']}` | {item.get('reason') or '—'} |"
            )
        lines.append("")
    return "\n".join(lines)


def render_batch_csv(summary: dict) -> str:
    """Export all cases (including explicit failed/missing rows and seeds) as CSV."""
    metric_names = list((summary.get("metric_stats") or {}).keys())
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([
        "case_id", "run_id", "process_corner", "voltage", "temperature",
        "sample_index", "mc_seed", "run_state", "outcome", "spec_status",
        "failure_reason", *metric_names,
    ])
    for row in summary.get("cases") or []:
        metrics = row.get("metrics") or {}
        writer.writerow([
            row["case_id"], row["run_id"], row["process_corner"], row["voltage"], row["temperature"],
            "" if row["sample_index"] is None else row["sample_index"],
            "" if row["mc_seed"] is None else row["mc_seed"],
            row["run_state"], row["outcome"], row["spec_status"],
            row.get("failure_reason") or "",
            *[metrics.get(m, "") for m in metric_names],
        ])
    return buf.getvalue()

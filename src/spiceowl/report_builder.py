"""Deterministic fast simulation report builder and interactive SVG waveform plotter.

Pure Python standard library implementation (zero external dependencies, zero CDN links):
- SI engineering formatting for time, frequency, voltage, current, power, and scalars.
- Safe regex-only signal expression parsing (differential ``A - B``, common-mode
  ``(A + B)/2`` / ``0.5*(A + B)``, sum ``A + B``, negate ``-A``) without ``eval()``.
- Uniform handling of both ``exact_window`` samples and ``downsampled_preview``
  Min-Max envelopes from ``DirectExecutionManager.waveform_query``.
- Publication-grade light-theme responsive single-file HTML5 reports with crisp
  inline SVG charts (<= 420 peak-preserving vertices per trace), interactive legend
  toggling, and live X/Y crosshair hover readouts.
"""

from __future__ import annotations

from datetime import datetime, timezone
import html
import json
import math
import re
from typing import Any

from spiceowl.domain.common import DomainError


TRACE_PALETTE: tuple[str, ...] = (
    "#0f766e",
    "#2563eb",
    "#d97706",
    "#dc2626",
    "#7c3aed",
    "#059669",
)

MAX_SVG_VERTICES = 420
DEFAULT_QUERY_MAX_POINTS = 1200

_SI_PREFIX_BY_EXP: dict[int, str] = {
    -15: "f",
    -12: "p",
    -9: "n",
    -6: "u",
    -3: "m",
    0: "",
    3: "k",
    6: "M",
    9: "G",
    12: "T",
}

_UNIT_ALIASES: dict[str, str] = {
    "s": "s",
    "sec": "s",
    "secs": "s",
    "second": "s",
    "seconds": "s",
    "hz": "Hz",
    "hertz": "Hz",
    "v": "V",
    "volt": "V",
    "volts": "V",
    "a": "A",
    "amp": "A",
    "amps": "A",
    "ampere": "A",
    "amperes": "A",
    "w": "W",
    "watt": "W",
    "watts": "W",
    "ohm": "Ohm",
    "ohms": "Ohm",
    "f": "F",
    "farad": "F",
    "h": "H",
    "henry": "H",
    "db": "dB",
    "dbc": "dBc",
    "dbm": "dBm",
    "dbv": "dBV",
    "deg": "deg",
    "degree": "deg",
    "degrees": "deg",
    "%": "%",
}

_UNIT_EXP_BOUNDS: dict[str, tuple[int, int]] = {
    "s": (-15, 0),
    "Hz": (0, 12),
    "V": (-9, 3),
    "A": (-12, 0),
    "W": (-12, 3),
    "Ohm": (-3, 9),
    "F": (-15, 0),
    "H": (-12, 0),
}

_NON_SI_UNITS = frozenset({
    "dB", "dBc", "dBm", "dBV", "deg", "°", "%", "x", "X", "bit", "bits",
    "LSB", "ppm", "cycles", "cycle", "pts", "samples",
})

# Regex-only expression parser (NEVER uses eval())
_SIG_ATOM = r"[A-Za-z0-9_./:$<>\\\[\]]+(?:\([^()]*\))?"
_RE_AVG_DIV = re.compile(rf"^\(\s*({_SIG_ATOM})\s*\+\s*({_SIG_ATOM})\s*\)\s*/\s*2(?:\.0+)?$")
_RE_AVG_MUL_L = re.compile(rf"^(?:0\.5|\.5|1\s*/\s*2)\s*\*?\s*\(\s*({_SIG_ATOM})\s*\+\s*({_SIG_ATOM})\s*\)$")
_RE_AVG_MUL_R = re.compile(rf"^\(\s*({_SIG_ATOM})\s*\+\s*({_SIG_ATOM})\s*\)\s*\*\s*(?:0\.5|\.5)$")
_RE_DIFF_SP = re.compile(rf"^({_SIG_ATOM})\s+-\s+({_SIG_ATOM})$")
_RE_DIFF_TIGHT = re.compile(rf"^({_SIG_ATOM})\s*-\s*({_SIG_ATOM})$")
_RE_SUM = re.compile(rf"^({_SIG_ATOM})\s*\+\s*({_SIG_ATOM})$")
_RE_NEG = re.compile(rf"^-\s*\(?\s*({_SIG_ATOM})\s*\)?$")
_RE_WRAP_SIG = re.compile(r"^(?:[vViI]\()?/*([^()]+?)\)?$")


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _normalize_unit(unit: str | None) -> str:
    if not isinstance(unit, str):
        return ""
    stripped = unit.strip()
    if not stripped:
        return ""
    return _UNIT_ALIASES.get(stripped.lower(), stripped)


def _trim_fixed(val: float, decimals: int) -> str:
    if abs(val) < 0.5 * (10.0 ** (-decimals)):
        val = 0.0
    text = f"{val:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _compact_scaled_number(scaled: float, digits: int = 3) -> str:
    mag = abs(scaled)
    if mag >= 100.0:
        dec = max(0, digits - 2)
    elif mag >= 10.0:
        dec = max(1, digits - 1)
    else:
        dec = max(1, digits)
    return _trim_fixed(scaled, dec)


def format_si(value: float | int | None, unit: str = "", *, digits: int = 3) -> str:
    """Format a physical value with clean SI engineering prefixes."""
    if not _is_finite_number(value):
        return "—"
    v = float(value)
    u = _normalize_unit(unit)
    if v == 0.0:
        if not u:
            return "0"
        return f"0{u}" if u in ("%", "°") else f"0 {u}"

    if u in _NON_SI_UNITS:
        num = _compact_scaled_number(v, digits=digits) if 1e-3 <= abs(v) < 1e6 else f"{v:.{digits}g}"
        return f"{num}{u}" if u in ("%", "°") else f"{num} {u}"

    if not u:
        mag = abs(v)
        if 1e-3 <= mag < 1e5:
            return _compact_scaled_number(v, digits=digits)
        return f"{v:.{max(2, digits)}g}"

    lo_exp, hi_exp = _UNIT_EXP_BOUNDS.get(u, (-15, 12))
    exp3 = int(math.floor(math.log10(abs(v)) / 3.0)) * 3
    exp3 = max(lo_exp, min(hi_exp, exp3))
    scaled = v / (10.0 ** exp3)
    if abs(scaled) >= 999.95 and exp3 + 3 <= hi_exp:
        exp3 += 3
        scaled = v / (10.0 ** exp3)
    prefix = _SI_PREFIX_BY_EXP.get(exp3, "")
    num = _compact_scaled_number(scaled, digits=digits)
    return f"{num} {prefix}{u}"


def _choose_axis_scale(lo: float, hi: float, unit: str) -> tuple[float, str]:
    """Choose a single SI scale divisor and display unit for an axis."""
    u = _normalize_unit(unit)
    if not u or u in _NON_SI_UNITS:
        return 1.0, u
    peak = max(abs(lo), abs(hi))
    if not math.isfinite(peak) or peak == 0.0:
        return 1.0, u
    lo_exp, hi_exp = _UNIT_EXP_BOUNDS.get(u, (-15, 12))
    exp3 = int(math.floor(math.log10(peak) / 3.0)) * 3
    exp3 = max(lo_exp, min(hi_exp, exp3))
    prefix = _SI_PREFIX_BY_EXP.get(exp3, "")
    return 10.0 ** exp3, f"{prefix}{u}"


def _format_bytes(num_bytes: Any) -> str:
    if not _is_finite_number(num_bytes) or float(num_bytes) < 0:
        return "—"
    b = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if b < 1024.0 or unit == "GiB":
            return f"{_trim_fixed(b, 1 if unit != 'B' else 0)} {unit}"
        b /= 1024.0
    return f"{b:.1f} GiB"


def _format_mem_gib(mem_bytes: Any) -> str:
    if not _is_finite_number(mem_bytes) or float(mem_bytes) <= 0:
        return "—"
    return f"{_trim_fixed(float(mem_bytes) / (1024.0 ** 3), 2)} GiB"


def _format_timestamp(ts: Any) -> str:
    if not isinstance(ts, str) or not ts.strip():
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    raw = ts.strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return raw[:32]


def _infer_signal_unit(name: str, explicit_unit: str | None = None) -> str:
    norm = _normalize_unit(explicit_unit)
    if norm:
        return norm
    lower = name.strip().lower()
    if ":p" in lower or ":n" in lower or ":d" in lower or ":s" in lower or ":g" in lower or lower.startswith("i("):
        return "A"
    if lower.startswith("v("):
        return "V"
    return ""


# ---------------------------------------------------------------------------
# Safe Regex-Only Signal Expression Parser & Query Evaluation
# ---------------------------------------------------------------------------

def parse_signal_expression(expr: str, known_signals: set[str] | None = None) -> dict:
    """Safely parse a raw signal or simple derived expression using regex only."""
    if not isinstance(expr, str) or not expr.strip():
        raise DomainError("Signal expression must be a non-empty string.", "invalid_waveform_query")
    text = expr.strip()

    ci_lookup: dict[str, list[str]] = {}
    if known_signals:
        for sig in known_signals:
            ci_lookup.setdefault(sig.lower(), []).append(sig)

    def _resolve(tok: str) -> str:
        t = tok.strip()
        if not known_signals:
            return t
        if t in known_signals:
            return t
        matches = ci_lookup.get(t.lower()) or []
        if len(matches) == 1:
            return matches[0]
        return t

    if known_signals:
        if text in known_signals:
            return {"op": "raw", "raw_signals": [text], "label": text}
        ci_exact = ci_lookup.get(text.lower()) or []
        if len(ci_exact) == 1:
            return {"op": "raw", "raw_signals": [ci_exact[0]], "label": ci_exact[0]}

    for pat in (_RE_AVG_DIV, _RE_AVG_MUL_L, _RE_AVG_MUL_R):
        m = pat.match(text)
        if m:
            a, b = _resolve(m.group(1)), _resolve(m.group(2))
            return {"op": "avg2", "raw_signals": [a, b], "label": text}

    m = _RE_DIFF_SP.match(text)
    if m:
        a, b = _resolve(m.group(1)), _resolve(m.group(2))
        return {"op": "diff", "raw_signals": [a, b], "label": text}

    m = _RE_DIFF_TIGHT.match(text)
    if m:
        a_tok, b_tok = m.group(1), m.group(2)
        a, b = _resolve(a_tok), _resolve(b_tok)
        if not known_signals or (a in known_signals and b in known_signals):
            return {"op": "diff", "raw_signals": [a, b], "label": text}

    m = _RE_SUM.match(text)
    if m:
        a, b = _resolve(m.group(1)), _resolve(m.group(2))
        return {"op": "sum", "raw_signals": [a, b], "label": text}

    m = _RE_NEG.match(text)
    if m:
        a = _resolve(m.group(1))
        return {"op": "neg", "raw_signals": [a], "label": text}

    return {"op": "raw", "raw_signals": [_resolve(text)], "label": text}


def _to_sample_value(raw: Any) -> float | complex:
    if isinstance(raw, dict):
        if "real" in raw and "imag" in raw:
            return complex(float(raw["real"]), float(raw["imag"]))
        if "mag" in raw:
            return float(raw["mag"])
    return float(raw)


def _to_real(val: float | complex) -> float:
    return abs(val) if isinstance(val, complex) else float(val)


def _compute_stats(
    ys: list[float],
    *,
    y_lo: list[float] | None = None,
    y_hi: list[float] | None = None,
    override_min: float | None = None,
    override_max: float | None = None,
) -> dict[str, float | None]:
    if not ys:
        return {"min": None, "max": None, "p2p": None, "avg": None, "final": None}
    vmin = override_min if _is_finite_number(override_min) else (min(y_lo) if y_lo else min(ys))
    vmax = override_max if _is_finite_number(override_max) else (max(y_hi) if y_hi else max(ys))
    vavg = sum(ys) / len(ys)
    vfinal = ys[-1]
    return {
        "min": float(vmin),
        "max": float(vmax),
        "p2p": float(vmax - vmin),
        "avg": float(vavg),
        "final": float(vfinal),
    }


def _extract_envelope_buckets(query_result: dict, raw_sig: str) -> list[dict]:
    envs = _as_dict(query_result.get("envelopes")).get(raw_sig)
    if isinstance(envs, list) and envs:
        out: list[dict] = []
        for b in envs:
            if not isinstance(b, dict):
                continue
            t0, t1 = b.get("t0"), b.get("t1")
            lo, hi = b.get("min"), b.get("max")
            if _is_finite_number(t0) and _is_finite_number(t1) and _is_finite_number(lo) and _is_finite_number(hi):
                out.append({
                    "t0": float(t0),
                    "t1": float(t1),
                    "mid_t": 0.5 * (float(t0) + float(t1)),
                    "min": float(lo),
                    "max": float(hi),
                    "mid_y": 0.5 * (float(lo) + float(hi)),
                })
        return out
    sig_entry = _as_dict(_as_dict(query_result.get("signals")).get(raw_sig))
    xs = sig_entry.get("x")
    vals = sig_entry.get("values")
    if isinstance(xs, list) and isinstance(vals, list):
        out = []
        for x, v in zip(xs, vals):
            if _is_finite_number(x):
                rv = _to_real(_to_sample_value(v))
                if math.isfinite(rv):
                    fx = float(x)
                    out.append({"t0": fx, "t1": fx, "mid_t": fx, "min": rv, "max": rv, "mid_y": rv})
        return out
    return []


def _align_envelope_pair(env_a: list[dict], env_b: list[dict]) -> list[tuple[dict, dict]]:
    if not env_a or not env_b:
        return []
    if len(env_a) == len(env_b):
        return list(zip(env_a, env_b))
    # Nearest-time monotonic pointer alignment
    pairs: list[tuple[dict, dict]] = []
    j = 0
    nb = len(env_b)
    for ba in env_a:
        ta = ba["mid_t"]
        while j + 1 < nb and abs(env_b[j + 1]["mid_t"] - ta) <= abs(env_b[j]["mid_t"] - ta):
            j += 1
        pairs.append((ba, env_b[j]))
    return pairs


def evaluate_query_expressions(
    query_result: dict,
    parsed_exprs: list[dict],
    *,
    signal_units: dict[str, str] | None = None,
) -> list[dict]:
    """Build plotted series dicts (with stats and optional envelopes) from a waveform_query result."""
    units_map = signal_units or {}
    rep_type = str(query_result.get("representation") or "")
    is_exact = rep_type == "exact_window" or (bool(query_result.get("exact")) and isinstance(query_result.get("x"), list))
    signals_block = _as_dict(query_result.get("signals"))
    series_out: list[dict] = []

    for parsed in parsed_exprs:
        op = parsed["op"]
        raws = parsed["raw_signals"]
        label = parsed["label"]
        sig_unit = _infer_signal_unit(raws[0], units_map.get(raws[0]))

        if is_exact:
            xs_raw = query_result.get("x") or []
            xs: list[float] = []
            ys: list[float] = []
            if op in ("raw", "neg"):
                vals = _as_dict(signals_block.get(raws[0])).get("values") or []
                for x, rv in zip(xs_raw, vals):
                    if not _is_finite_number(x):
                        continue
                    sv = _to_sample_value(rv)
                    y = (-_to_real(sv)) if (op == "neg" and not isinstance(sv, complex)) else _to_real(-sv if op == "neg" else sv)
                    if math.isfinite(y):
                        xs.append(float(x))
                        ys.append(float(y))
            else:
                vals_a = _as_dict(signals_block.get(raws[0])).get("values") or []
                vals_b = _as_dict(signals_block.get(raws[1])).get("values") or []
                for x, ra, rb in zip(xs_raw, vals_a, vals_b):
                    if not _is_finite_number(x):
                        continue
                    va = _to_sample_value(ra)
                    vb = _to_sample_value(rb)
                    if op == "diff":
                        comb = va - vb
                    elif op == "sum":
                        comb = va + vb
                    else:  # avg2
                        comb = 0.5 * (va + vb)
                    y = _to_real(comb)
                    if math.isfinite(y):
                        xs.append(float(x))
                        ys.append(float(y))
            series_out.append({
                "name": label,
                "x": xs,
                "y": ys,
                "unit": sig_unit,
                "representation": "exact_window",
                "stats": _compute_stats(ys),
            })
        else:
            xs = []
            ys = []
            y_lo: list[float] = []
            y_hi: list[float] = []
            override_min = None
            override_max = None
            if op in ("raw", "neg"):
                env = _extract_envelope_buckets(query_result, raws[0])
                sig_meta = _as_dict(signals_block.get(raws[0]))
                if op == "raw":
                    for b in env:
                        xs.append(b["mid_t"])
                        y_lo.append(b["min"])
                        y_hi.append(b["max"])
                        ys.append(b["mid_y"])
                    override_min = sig_meta.get("min")
                    override_max = sig_meta.get("max")
                else:
                    for b in env:
                        xs.append(b["mid_t"])
                        lo = -b["max"]
                        hi = -b["min"]
                        y_lo.append(lo)
                        y_hi.append(hi)
                        ys.append(0.5 * (lo + hi))
                    if _is_finite_number(sig_meta.get("max")):
                        override_min = -float(sig_meta["max"])
                    if _is_finite_number(sig_meta.get("min")):
                        override_max = -float(sig_meta["min"])
            else:
                env_a = _extract_envelope_buckets(query_result, raws[0])
                env_b = _extract_envelope_buckets(query_result, raws[1])
                for ba, bb in _align_envelope_pair(env_a, env_b):
                    xs.append(ba["mid_t"])
                    if op == "diff":
                        mid = ba["mid_y"] - bb["mid_y"]
                        lo = ba["min"] - bb["max"]
                        hi = ba["max"] - bb["min"]
                    elif op == "sum":
                        mid = ba["mid_y"] + bb["mid_y"]
                        lo = ba["min"] + bb["min"]
                        hi = ba["max"] + bb["max"]
                    else:  # avg2
                        mid = 0.5 * (ba["mid_y"] + bb["mid_y"])
                        lo = 0.5 * (ba["min"] + bb["min"])
                        hi = 0.5 * (ba["max"] + bb["max"])
                    ys.append(mid)
                    y_lo.append(min(lo, mid))
                    y_hi.append(max(hi, mid))
            series_out.append({
                "name": label,
                "x": xs,
                "y": ys,
                "y_lo": y_lo,
                "y_hi": y_hi,
                "unit": sig_unit,
                "representation": "downsampled_preview",
                "stats": _compute_stats(ys, y_lo=y_lo, y_hi=y_hi, override_min=override_min, override_max=override_max),
            })

    return series_out


# ---------------------------------------------------------------------------
# Automatic High-Value Plot Selection
# ---------------------------------------------------------------------------

def _extract_ds_signals(ds: dict) -> list[dict]:
    raw_sigs = ds.get("signals") if isinstance(ds.get("signals"), list) else []
    out: list[dict] = []
    for item in raw_sigs:
        if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"].strip():
            out.append({
                "name": item["name"],
                "unit": _normalize_unit(item.get("unit")),
                "kind": str(item.get("kind") or "real"),
            })
        elif isinstance(item, str) and item.strip():
            out.append({"name": item, "unit": "", "kind": "real"})
    return out


def _base_signal_token(name: str) -> str:
    m = _RE_WRAP_SIG.match(name.strip())
    core = m.group(1) if m else name.strip()
    if "/" in core:
        core = core.rsplit("/", 1)[-1]
    return core.strip().lower()


def _is_current_signal(sig: dict) -> bool:
    name = sig["name"]
    lower = name.lower()
    if sig.get("unit") == "A":
        return True
    if ":p" in lower or ":n" in lower or lower.startswith("i("):
        return True
    base = _base_signal_token(name)
    return base in ("idd", "ivdd", "iss", "ibias", "iout", "iin", "isup")


def _is_ground_signal(sig: dict) -> bool:
    base = _base_signal_token(sig["name"])
    return base in ("0", "gnd", "gnd!", "vss", "vss!", "avss", "dvss", "gnda", "gndd")


def _dataset_rank(ds: dict) -> tuple[int, int, int]:
    name = str(ds.get("dataset") or ds.get("name") or "").lower()
    sweep = _as_dict(ds.get("sweep"))
    sweep_name = str(sweep.get("name") or "").lower()
    cnt = int(sweep.get("count") or 0)
    sig_cnt = int(ds.get("signal_count") or len(ds.get("signals") or []))
    if "tran" in name or sweep_name == "time":
        kind_prio = 3
    elif "ac" in name or "stb" in name or "noise" in name or sweep_name in ("freq", "frequency"):
        kind_prio = 2
    elif "dc" in name:
        kind_prio = 1
    else:
        kind_prio = 0
    return (kind_prio, cnt, sig_cnt)


def auto_select_plot_specs(waveform_index: dict | None, max_plots: int = 3) -> list[dict]:
    """Pick up to ``max_plots`` high-value plot specs automatically from a waveform_index.

    Never invents signal names: every raw signal and derived operand exists in ``ds["signals"]``.
    """
    if not isinstance(waveform_index, dict) or max_plots <= 0:
        return []
    raw_datasets = waveform_index.get("datasets")
    if not isinstance(raw_datasets, list) or not raw_datasets:
        return []

    eligible: list[dict] = []
    for ds in raw_datasets:
        if not isinstance(ds, dict):
            continue
        sigs = _extract_ds_signals(ds)
        sweep_cnt = int(_as_dict(ds.get("sweep")).get("count") or 0)
        sig_cnt = int(ds.get("signal_count") or len(sigs))
        if sig_cnt > 0 and len(sigs) > 0 and sweep_cnt > 1:
            eligible.append(ds)

    if not eligible:
        return []

    eligible.sort(key=_dataset_rank, reverse=True)
    primary = eligible[0]
    ds_name = str(primary.get("dataset") or primary.get("name") or "")
    sigs = _extract_ds_signals(primary)

    voltage_sigs = [s for s in sigs if not _is_current_signal(s)]
    current_sigs = [s for s in sigs if _is_current_signal(s)]

    def _find_sig_by_base(pool: list[dict], target_base: str) -> str | None:
        # Prefer exact case-insensitive match on raw name first, then unwrapped base name
        for s in pool:
            if s["name"].lower() == target_base:
                return s["name"]
        for s in pool:
            if _base_signal_token(s["name"]) == target_base:
                return s["name"]
        return None

    specs: list[dict] = []
    used_raw: set[str] = set()

    # 1. Differential output pair detection
    diff_pairs = (
        ("vop", "von"),
        ("outp", "outn"),
        ("voutp", "voutn"),
        ("out_p", "out_n"),
        ("vout_p", "vout_n"),
        ("vo_p", "vo_n"),
        ("vout_pos", "vout_neg"),
    )
    diff_found: tuple[str, str] | None = None
    for p_key, n_key in diff_pairs:
        sp = _find_sig_by_base(voltage_sigs, p_key)
        sn = _find_sig_by_base(voltage_sigs, n_key)
        if sp and sn and sp != sn:
            diff_found = (sp, sn)
            break

    if diff_found is not None:
        sp, sn = diff_found
        specs.append({
            "title": f"差分输出波形 ({sp} / {sn})",
            "dataset": ds_name,
            "signals": [sp, sn],
        })
        used_raw.update({sp, sn})
        if len(specs) < max_plots:
            specs.append({
                "title": "差分净输出与共模",
                "dataset": ds_name,
                "signals": [f"{sp} - {sn}", f"({sp} + {sn})/2"],
            })

    # 2. Input / control / bias / single-ended output signals
    priority_bases = (
        "vip", "vin", "inp", "inn", "vinp", "vinn", "in_p", "in_n",
        "vout", "out", "vo", "in", "vi",
        "clk", "clock", "rst", "reset", "en",
        "cmfb", "vcm", "vref", "vbias", "vbg", "vdd", "avdd", "dvdd",
    )
    picked_ctrl: list[str] = []
    for base in priority_bases:
        found = _find_sig_by_base(voltage_sigs, base)
        if found and found not in used_raw and found not in picked_ctrl:
            picked_ctrl.append(found)
            if len(picked_ctrl) >= 4:
                break

    if picked_ctrl and len(specs) < max_plots:
        plot_title = "输入 / 偏置与控制波形" if diff_found is not None else "关键节点波形"
        specs.append({
            "title": plot_title,
            "dataset": ds_name,
            "signals": picked_ctrl,
        })
        used_raw.update(picked_ctrl)

    # 3. Supply / branch current plot if slot remains
    if current_sigs and len(specs) < max_plots:
        cur_names = [s["name"] for s in current_sigs[:4]]
        specs.append({
            "title": "电源与支路电流波形",
            "dataset": ds_name,
            "signals": cur_names,
        })
        used_raw.update(cur_names)

    # 4. Fallback for arbitrary circuits on primary dataset
    if len(specs) < max_plots:
        non_gnd = [s["name"] for s in sigs if s["name"] not in used_raw and not _is_ground_signal(s)]
        if not non_gnd and not specs:
            non_gnd = [s["name"] for s in sigs if s["name"] not in used_raw]
        idx = 0
        while non_gnd and len(specs) < max_plots and (len(specs) == 0 or idx == 0):
            chunk = non_gnd[:4]
            non_gnd = non_gnd[4:]
            idx += 1
            specs.append({
                "title": f"仿真信号波形 ({ds_name})" if idx == 1 else f"补充信号波形 #{idx} ({ds_name})",
                "dataset": ds_name,
                "signals": chunk,
            })
            used_raw.update(chunk)

    # 5. If another distinct dataset (e.g. AC alongside TRAN) exists and slot remains
    if len(specs) < max_plots and len(eligible) > 1:
        for alt_ds in eligible[1:]:
            if len(specs) >= max_plots:
                break
            alt_name = str(alt_ds.get("dataset") or alt_ds.get("name") or "")
            alt_sigs = _extract_ds_signals(alt_ds)
            alt_non_gnd = [s["name"] for s in alt_sigs if not _is_ground_signal(s)] or [s["name"] for s in alt_sigs]
            if alt_non_gnd:
                specs.append({
                    "title": f"辅助数据集波形 ({alt_name})",
                    "dataset": alt_name,
                    "signals": alt_non_gnd[:4],
                })

    return specs[:max_plots]


# ---------------------------------------------------------------------------
# Peak-Preserving SVG Downsampling & Interactive SVG Waveform Renderer
# ---------------------------------------------------------------------------

def _downsample_minmax(xs: list[float], ys: list[float], max_vertices: int = MAX_SVG_VERTICES) -> tuple[list[float], list[float]]:
    n = min(len(xs), len(ys))
    if n <= max_vertices:
        return xs[:n], ys[:n]
    buckets = max(1, (max_vertices - 2) // 2)
    out_x: list[float] = [xs[0]]
    out_y: list[float] = [ys[0]]
    inner_n = n - 2
    for b in range(buckets):
        i0 = 1 + int(b * inner_n / buckets)
        i1 = 1 + int((b + 1) * inner_n / buckets)
        if i1 <= i0:
            continue
        i_min = i0
        i_max = i0
        v_min = ys[i0]
        v_max = ys[i0]
        for i in range(i0 + 1, i1):
            v = ys[i]
            if v < v_min:
                v_min = v
                i_min = i
            if v > v_max:
                v_max = v
                i_max = i
        if i_min == i_max:
            out_x.append(xs[i_min])
            out_y.append(ys[i_min])
        elif i_min < i_max:
            out_x.append(xs[i_min])
            out_y.append(ys[i_min])
            out_x.append(xs[i_max])
            out_y.append(ys[i_max])
        else:
            out_x.append(xs[i_max])
            out_y.append(ys[i_max])
            out_x.append(xs[i_min])
            out_y.append(ys[i_min])
    out_x.append(xs[n - 1])
    out_y.append(ys[n - 1])
    return out_x, out_y


def _downsample_envelope(
    xs: list[float],
    y_lo: list[float],
    y_hi: list[float],
    max_buckets: int = 200,
) -> tuple[list[float], list[float], list[float]]:
    n = min(len(xs), len(y_lo), len(y_hi))
    if n <= max_buckets:
        return xs[:n], y_lo[:n], y_hi[:n]
    ox: list[float] = []
    olo: list[float] = []
    ohi: list[float] = []
    for b in range(max_buckets):
        i0 = int(b * n / max_buckets)
        i1 = int((b + 1) * n / max_buckets)
        if i1 <= i0:
            continue
        ox.append(0.5 * (xs[i0] + xs[i1 - 1]))
        olo.append(min(y_lo[i0:i1]))
        ohi.append(max(y_hi[i0:i1]))
    return ox, olo, ohi


def _nice_num(range_val: float, round_val: bool) -> float:
    if range_val <= 0 or not math.isfinite(range_val):
        return 1.0
    exp = math.floor(math.log10(range_val))
    frac = range_val / (10.0 ** exp)
    if round_val:
        if frac < 1.5:
            nice = 1.0
        elif frac < 3.0:
            nice = 2.0
        elif frac < 7.0:
            nice = 5.0
        else:
            nice = 10.0
    else:
        if frac <= 1.0:
            nice = 1.0
        elif frac <= 2.0:
            nice = 2.0
        elif frac <= 5.0:
            nice = 5.0
        else:
            nice = 10.0
    return nice * (10.0 ** exp)


def _compute_axis_bounds(lo: float, hi: float, tick_count: int = 5) -> tuple[float, float, list[float]]:
    if not math.isfinite(lo) or not math.isfinite(hi):
        return 0.0, 1.0, [0.0, 0.25, 0.5, 0.75, 1.0]
    if hi <= lo:
        pad = max(abs(lo) * 0.1, 1e-6)
        lo, hi = lo - pad, hi + pad
    span = hi - lo
    nice_range = _nice_num(span, False)
    step = _nice_num(nice_range / max(1, tick_count - 1), True)
    if step <= 0 or not math.isfinite(step):
        return lo, hi, [lo, hi]
    g_min = math.floor(lo / step) * step
    g_max = math.ceil(hi / step) * step
    if g_max <= g_min:
        g_max = g_min + step
    ticks: list[float] = []
    cur = g_min
    guard = 0
    eps = step * 1e-6
    while cur <= g_max + eps and guard < 12:
        val = 0.0 if abs(cur) < eps else cur
        ticks.append(val)
        cur += step
        guard += 1
    if len(ticks) < 2:
        ticks = [g_min, g_max]
    return g_min, g_max, ticks


def _fmt_tick(value: float, divisor: float) -> str:
    scaled = value / divisor if divisor else value
    if abs(scaled) < 1e-12:
        return "0"
    mag = abs(scaled)
    if mag >= 10000 or mag < 1e-3:
        return f"{scaled:.2g}"
    if mag >= 100:
        return _trim_fixed(scaled, 1)
    if mag >= 10:
        return _trim_fixed(scaled, 2)
    return _trim_fixed(scaled, 3)


def render_waveform_svg(
    series_list: list[dict],
    *,
    title: str = "",
    x_label: str = "time",
    x_unit: str = "s",
    y_unit: str = "",
    caption: str = "",
    width: int = 680,
    height: int = 250,
) -> str:
    """Render a crisp, responsive, interactive inline SVG waveform card."""
    w = max(320, int(width))
    h = max(160, int(height))
    pad_l, pad_r, pad_t, pad_b = 64, 20, 18, 40
    plot_w = max(80, w - pad_l - pad_r)
    plot_h = max(60, h - pad_t - pad_b)

    valid_series: list[dict] = []
    all_x: list[float] = []
    all_y: list[float] = []

    for idx, raw_s in enumerate(series_list or []):
        if not isinstance(raw_s, dict):
            continue
        xs_in = raw_s.get("x") or []
        ys_in = raw_s.get("y") or []
        y_lo_in = raw_s.get("y_lo")
        y_hi_in = raw_s.get("y_hi")
        has_env = isinstance(y_lo_in, list) and isinstance(y_hi_in, list) and len(y_lo_in) == len(xs_in) and len(y_hi_in) == len(xs_in)

        xs_clean: list[float] = []
        ys_clean: list[float] = []
        lo_clean: list[float] = []
        hi_clean: list[float] = []
        for k in range(min(len(xs_in), len(ys_in))):
            xk, yk = xs_in[k], ys_in[k]
            if not (_is_finite_number(xk) and _is_finite_number(yk)):
                continue
            fx, fy = float(xk), float(yk)
            xs_clean.append(fx)
            ys_clean.append(fy)
            if has_env and _is_finite_number(y_lo_in[k]) and _is_finite_number(y_hi_in[k]):
                lo_clean.append(float(y_lo_in[k]))
                hi_clean.append(float(y_hi_in[k]))
            elif has_env:
                lo_clean.append(fy)
                hi_clean.append(fy)

        if not xs_clean:
            continue

        all_x.extend((min(xs_clean), max(xs_clean)))
        if lo_clean and hi_clean:
            all_y.extend((min(lo_clean), max(hi_clean)))
        else:
            all_y.extend((min(ys_clean), max(ys_clean)))

        stats = raw_s.get("stats") if isinstance(raw_s.get("stats"), dict) else _compute_stats(
            ys_clean,
            y_lo=lo_clean if lo_clean else None,
            y_hi=hi_clean if hi_clean else None,
        )
        color = str(raw_s.get("color") or TRACE_PALETTE[idx % len(TRACE_PALETTE)])
        s_unit = _normalize_unit(raw_s.get("unit") or y_unit)
        valid_series.append({
            "name": str(raw_s.get("name") or f"trace_{idx + 1}"),
            "x": xs_clean,
            "y": ys_clean,
            "y_lo": lo_clean if len(lo_clean) == len(xs_clean) else None,
            "y_hi": hi_clean if len(hi_clean) == len(xs_clean) else None,
            "unit": s_unit,
            "color": color,
            "stats": stats,
        })

    esc_title = html.escape(title) if title else ""
    esc_caption = html.escape(caption) if caption else ""
    if not valid_series:
        head_html = f'<div class="wf-head"><div class="wf-title">{esc_title}</div></div>' if esc_title else ""
        cap_html = f'<figcaption class="wf-caption">{esc_caption}</figcaption>' if esc_caption else ""
        return (
            f'<figure class="wf-card">{head_html}'
            f'<div class="wf-empty">暂无可绘制的有效波形采样点。</div>'
            f'{cap_html}</figure>'
        )

    eff_y_unit = _normalize_unit(y_unit) or next((s["unit"] for s in valid_series if s["unit"]), "")
    eff_x_unit = _normalize_unit(x_unit)

    raw_x_min, raw_x_max = min(all_x), max(all_x)
    raw_y_min, raw_y_max = min(all_y), max(all_y)
    # Give Y axis slight headroom if non-flat
    if raw_y_max > raw_y_min:
        y_margin = (raw_y_max - raw_y_min) * 0.05
        raw_y_min -= y_margin
        raw_y_max += y_margin

    x_min, x_max, x_ticks = _compute_axis_bounds(raw_x_min, raw_x_max, tick_count=5)
    y_min, y_max, y_ticks = _compute_axis_bounds(raw_y_min, raw_y_max, tick_count=5)
    x_span = x_max - x_min if x_max > x_min else 1.0
    y_span = y_max - y_min if y_max > y_min else 1.0

    x_div, x_axis_unit = _choose_axis_scale(x_min, x_max, eff_x_unit)
    y_div, y_axis_unit = _choose_axis_scale(y_min, y_max, eff_y_unit)

    def _px(x_val: float) -> float:
        return pad_l + (x_val - x_min) / x_span * plot_w

    def _py(y_val: float) -> float:
        return pad_t + (y_max - y_val) / y_span * plot_h

    svg_parts: list[str] = []
    # Plot background
    svg_parts.append(
        f'<rect x="{pad_l}" y="{pad_t}" width="{plot_w}" height="{plot_h}" '
        f'fill="#fcfdff" stroke="#e2e8f0" stroke-width="1"/>'
    )

    # Horizontal grid + Y ticks
    for yt in y_ticks:
        py = _py(yt)
        if py < pad_t - 0.5 or py > pad_t + plot_h + 0.5:
            continue
        is_zero = abs(yt) < 1e-12 and (y_min < 0 < y_max)
        stroke = "#cbd5e1" if is_zero else "#e2e8f0"
        dash = "" if is_zero else ' stroke-dasharray="3,3"'
        svg_parts.append(
            f'<line x1="{pad_l}" y1="{py:.1f}" x2="{pad_l + plot_w}" y2="{py:.1f}" '
            f'stroke="{stroke}" stroke-width="1"{dash}/>'
        )
        svg_parts.append(
            f'<text x="{pad_l - 8}" y="{py + 3.5:.1f}" text-anchor="end" '
            f'class="wf-tick">{html.escape(_fmt_tick(yt, y_div))}</text>'
        )

    # Vertical grid + X ticks
    for xt in x_ticks:
        px = _px(xt)
        if px < pad_l - 0.5 or px > pad_l + plot_w + 0.5:
            continue
        svg_parts.append(
            f'<line x1="{px:.1f}" y1="{pad_t}" x2="{px:.1f}" y2="{pad_t + plot_h}" '
            f'stroke="#e2e8f0" stroke-width="1" stroke-dasharray="3,3"/>'
        )
        svg_parts.append(
            f'<text x="{px:.1f}" y="{pad_t + plot_h + 16}" text-anchor="middle" '
            f'class="wf-tick">{html.escape(_fmt_tick(xt, x_div))}</text>'
        )

    # Axis labels
    x_title = f"{x_label} ({x_axis_unit})" if x_axis_unit else x_label
    svg_parts.append(
        f'<text x="{pad_l + plot_w / 2:.1f}" y="{h - 6}" text-anchor="middle" '
        f'class="wf-axis-label">{html.escape(x_title)}</text>'
    )
    if y_axis_unit:
        svg_parts.append(
            f'<text x="14" y="{pad_t + plot_h / 2:.1f}" text-anchor="middle" '
            f'transform="rotate(-90 14 {pad_t + plot_h / 2:.1f})" '
            f'class="wf-axis-label">{html.escape(y_axis_unit)}</text>'
        )

    # Traces (envelope band + peak-preserving polyline)
    meta_traces: list[dict] = []
    for idx, s in enumerate(valid_series):
        color = s["color"]
        g_parts: list[str] = [f'<g class="wf-trace" data-trace-idx="{idx}">']
        if s["y_lo"] is not None and s["y_hi"] is not None:
            has_spread = any(abs(hi - lo) > 1e-12 * max(1.0, abs(hi), abs(lo)) for lo, hi in zip(s["y_lo"], s["y_hi"]))
            if has_spread:
                ex, elo, ehi = _downsample_envelope(s["x"], s["y_lo"], s["y_hi"], max_buckets=200)
                upper = [f"{_px(x):.1f},{_py(y):.1f}" for x, y in zip(ex, ehi)]
                lower = [f"{_px(x):.1f},{_py(y):.1f}" for x, y in zip(reversed(ex), reversed(elo))]
                g_parts.append(
                    f'<polygon class="wf-band" points="{" ".join(upper + lower)}" '
                    f'fill="{html.escape(color)}" fill-opacity="0.16" stroke="none"/>'
                )
        dx, dy = _downsample_minmax(s["x"], s["y"], max_vertices=MAX_SVG_VERTICES)
        pts = " ".join(f"{_px(x):.1f},{_py(y):.1f}" for x, y in zip(dx, dy))
        if len(dx) == 1:
            g_parts.append(
                f'<circle cx="{_px(dx[0]):.1f}" cy="{_py(dy[0]):.1f}" r="3" fill="{html.escape(color)}"/>'
            )
        else:
            g_parts.append(
                f'<polyline class="wf-line" data-trace-idx="{idx}" fill="none" '
                f'stroke="{html.escape(color)}" stroke-width="1.8" '
                f'stroke-linejoin="round" stroke-linecap="round" points="{pts}"/>'
            )
        g_parts.append("</g>")
        svg_parts.append("".join(g_parts))
        meta_traces.append({
            "idx": idx,
            "name": s["name"],
            "color": color,
            "unit": s["unit"] or eff_y_unit,
        })

    # Interactive crosshair overlay layer
    svg_parts.append(
        f'<line class="wf-xhair-v" x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t + plot_h}" '
        f'stroke="#475569" stroke-width="1" stroke-dasharray="3,2" visibility="hidden"/>'
    )
    svg_parts.append('<g class="wf-xhair-dots"></g>')
    svg_parts.append(
        f'<rect class="wf-hit" x="{pad_l}" y="{pad_t}" width="{plot_w}" height="{plot_h}" '
        f'fill="transparent" style="cursor:crosshair"/>'
    )

    wf_meta = {
        "w": w,
        "h": h,
        "padL": pad_l,
        "padT": pad_t,
        "plotW": plot_w,
        "plotH": plot_h,
        "xMin": x_min,
        "xMax": x_max,
        "yMin": y_min,
        "yMax": y_max,
        "xLabel": x_label,
        "xUnit": eff_x_unit,
        "yUnit": eff_y_unit,
        "traces": meta_traces,
    }
    meta_attr = html.escape(json.dumps(wf_meta, ensure_ascii=False, separators=(",", ":")), quote=True)

    # Interactive legend buttons
    legend_btns: list[str] = []
    for idx, s in enumerate(valid_series):
        legend_btns.append(
            f'<button type="button" class="wf-leg-btn" data-trace-idx="{idx}" aria-pressed="true">'
            f'<span class="wf-swatch" style="background:{html.escape(s["color"])}"></span>'
            f'<span>{html.escape(s["name"])}</span></button>'
        )

    # Lightweight stats table: Signal | Min | Max | P2P | Final
    stat_rows: list[str] = []
    for idx, s in enumerate(valid_series):
        st = s["stats"]
        su = s["unit"] or eff_y_unit
        stat_rows.append(
            f'<tr class="wf-stat-row" data-trace-idx="{idx}">'
            f'<td><span class="wf-dot" style="background:{html.escape(s["color"])}"></span>{html.escape(s["name"])}</td>'
            f'<td class="num">{html.escape(format_si(st.get("min"), su))}</td>'
            f'<td class="num">{html.escape(format_si(st.get("max"), su))}</td>'
            f'<td class="num">{html.escape(format_si(st.get("p2p"), su))}</td>'
            f'<td class="num">{html.escape(format_si(st.get("final"), su))}</td>'
            f'</tr>'
        )

    head_html = (
        f'<div class="wf-head">'
        f'<div class="wf-title">{esc_title or "波形图"}</div>'
        f'<div class="wf-legend">{"".join(legend_btns)}</div>'
        f'</div>'
    )
    cap_html = f'<figcaption class="wf-caption">{esc_caption}</figcaption>' if esc_caption else ""

    return (
        f'<figure class="wf-card" data-wf-meta="{meta_attr}">'
        f'{head_html}'
        f'<div class="wf-svg-wrap">'
        f'<svg class="wf-svg" viewBox="0 0 {w} {h}" role="img" aria-label="{esc_title or "波形图"}">'
        f'{"".join(svg_parts)}'
        f'</svg>'
        f'<div class="wf-tooltip" hidden></div>'
        f'</div>'
        f'<div class="wf-stats-wrap">'
        f'<table class="wf-stats">'
        f'<thead><tr><th>Signal</th><th class="num">Min</th><th class="num">Max</th><th class="num">P2P</th><th class="num">Final</th></tr></thead>'
        f'<tbody>{"".join(stat_rows)}</tbody>'
        f'</table>'
        f'</div>'
        f'{cap_html}'
        f'</figure>'
    )


# ---------------------------------------------------------------------------
# Reusable Publication-Grade Editorial HTML5 Wrapper (Zero-CDN, Light Theme)
# ---------------------------------------------------------------------------

_EDITORIAL_CSS = """
:root {
  --bg: #f8fafc;
  --card: #ffffff;
  --ink: #0f172a;
  --ink-2: #334155;
  --muted: #64748b;
  --line: #e2e8f0;
  --line-soft: #f1f5f9;
  --accent: #0f766e;
  --accent-bg: #f0fdfa;
  --accent-bd: #99f6e4;
  --pass-fg: #15803d;
  --pass-bg: #f0fdf4;
  --pass-bd: #bbf7d0;
  --warn-fg: #b45309;
  --warn-bg: #fffbeb;
  --warn-bd: #fde68a;
  --fail-fg: #b91c1c;
  --fail-bg: #fef2f2;
  --fail-bd: #fecaca;
  --info-fg: #1d4ed8;
  --info-bg: #eff6ff;
  --info-bd: #bfdbfe;
  --radius: 12px;
}
* { box-sizing: border-box; }
html, body {
  margin: 0;
  padding: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Noto Sans SC", "Microsoft YaHei", sans-serif;
  font-size: 14px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}
.page {
  max-width: 1080px;
  margin: 0 auto;
  padding: 24px 20px 44px;
}
.hero {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 22px 24px 18px;
  margin-bottom: 16px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}
.hero-top {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}
.badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  padding: 3px 10px;
  border-radius: 999px;
  border: 1px solid var(--accent-bd);
  background: var(--accent-bg);
  color: var(--accent);
}
.badge.deep {
  border-color: #ddd6fe;
  background: #f5f3ff;
  color: #6d28d9;
}
.badge.instant {
  border-color: var(--info-bd);
  background: var(--info-bg);
  color: var(--info-fg);
}
.hero h1 {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: var(--ink);
}
.subtitle {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 13px;
}
.meta-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--line-soft);
}
.meta-chip {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  background: var(--bg);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 4px 10px;
  font-size: 12px;
}
.meta-k { color: var(--muted); }
.meta-v {
  color: var(--ink);
  font-weight: 600;
  font-family: "SFMono-Regular", Menlo, Consolas, "Liberation Mono", monospace;
}
.summary-box {
  background: linear-gradient(135deg, #f0fdfa 0%, #f8fafc 100%);
  border: 1px solid var(--accent-bd);
  border-left: 4px solid var(--accent);
  border-radius: var(--radius);
  padding: 14px 18px;
  margin-bottom: 16px;
}
.summary-label {
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--accent);
  margin-bottom: 4px;
}
.summary-text {
  margin: 0;
  color: var(--ink);
  font-size: 14px;
  white-space: pre-line;
}
.highlights {
  margin: 10px 0 0;
  padding-left: 18px;
  color: var(--ink-2);
}
.highlights li { margin: 3px 0; }
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(168px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
.kpi-card {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 13px 15px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);
}
.kpi-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  margin-bottom: 6px;
}
.kpi-label { font-size: 12px; color: var(--muted); font-weight: 500; }
.kpi-val {
  font-size: 20px;
  font-weight: 700;
  color: var(--ink);
  font-family: "SFMono-Regular", Menlo, Consolas, monospace;
  line-height: 1.2;
}
.kpi-sub { font-size: 11.5px; color: var(--muted); margin-top: 5px; }
.pill {
  display: inline-block;
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: var(--line-soft);
  color: var(--ink-2);
  white-space: nowrap;
}
.pill.pass { color: var(--pass-fg); background: var(--pass-bg); border-color: var(--pass-bd); }
.pill.warn { color: var(--warn-fg); background: var(--warn-bg); border-color: var(--warn-bd); }
.pill.fail { color: var(--fail-fg); background: var(--fail-bg); border-color: var(--fail-bd); }
.pill.info { color: var(--info-fg); background: var(--info-bg); border-color: var(--info-bd); }
.section {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 16px 18px;
  margin-bottom: 16px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);
}
.section-title {
  margin: 0 0 12px;
  font-size: 15px;
  font-weight: 700;
  color: var(--ink);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.dim-strip {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 10px;
}
.dim-item {
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 10px 12px;
  background: var(--bg);
}
.dim-name { font-size: 11.5px; color: var(--muted); margin-bottom: 5px; }
.plots-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 14px;
  margin-bottom: 16px;
}
.wf-card {
  margin: 0;
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 14px 16px 12px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);
}
.wf-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.wf-title { font-size: 14px; font-weight: 700; color: var(--ink); }
.wf-legend { display: flex; flex-wrap: wrap; gap: 6px; }
.wf-leg-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: var(--bg);
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 3px 10px;
  font-size: 12px;
  font-family: "SFMono-Regular", Menlo, Consolas, monospace;
  color: var(--ink);
  cursor: pointer;
  transition: opacity 0.15s ease, border-color 0.15s ease;
}
.wf-leg-btn:hover { border-color: #94a3b8; }
.wf-leg-btn.is-hidden { opacity: 0.42; text-decoration: line-through; }
.wf-swatch { width: 10px; height: 3px; border-radius: 2px; display: inline-block; }
.wf-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; vertical-align: middle; }
.wf-svg-wrap { position: relative; width: 100%; }
.wf-svg { width: 100%; height: auto; display: block; user-select: none; }
.wf-tick { fill: #64748b; font-size: 10.5px; font-family: "SFMono-Regular", Menlo, Consolas, monospace; }
.wf-axis-label { fill: #475569; font-size: 11px; font-weight: 600; }
.wf-trace.is-hidden { display: none; }
.wf-stat-row.is-hidden { opacity: 0.42; }
.wf-tooltip {
  position: absolute;
  top: 8px;
  right: 12px;
  pointer-events: none;
  background: rgba(15, 23, 42, 0.92);
  color: #f8fafc;
  padding: 7px 10px;
  border-radius: 8px;
  font-size: 11.5px;
  font-family: "SFMono-Regular", Menlo, Consolas, monospace;
  line-height: 1.45;
  box-shadow: 0 4px 12px rgba(15, 23, 42, 0.2);
  z-index: 5;
  max-width: 280px;
}
.wf-tt-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.wf-empty {
  padding: 20px;
  text-align: center;
  color: var(--muted);
  background: var(--bg);
  border-radius: 8px;
  font-size: 13px;
}
.wf-stats-wrap, .table-wrap { overflow-x: auto; margin-top: 8px; }
table.wf-stats, table.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}
table.wf-stats th, table.wf-stats td,
table.data-table th, table.data-table td {
  padding: 7px 10px;
  border-bottom: 1px solid var(--line-soft);
  text-align: left;
}
table.wf-stats th, table.data-table th {
  color: var(--muted);
  font-weight: 600;
  font-size: 11.5px;
  background: var(--bg);
  border-bottom: 1px solid var(--line);
  white-space: nowrap;
}
.num {
  text-align: right !important;
  font-family: "SFMono-Regular", Menlo, Consolas, monospace;
  white-space: nowrap;
}
.mono { font-family: "SFMono-Regular", Menlo, Consolas, monospace; }
.wf-caption { margin-top: 8px; font-size: 12px; color: var(--muted); }
.plot-warn {
  background: var(--warn-bg);
  border: 1px solid var(--warn-bd);
  color: var(--warn-fg);
  border-radius: var(--radius);
  padding: 12px 15px;
  font-size: 13px;
}
.img-card {
  margin: 0 0 14px;
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 14px 16px;
}
.img-card img {
  max-width: 100%;
  height: auto;
  display: block;
  border-radius: 8px;
  border: 1px solid var(--line-soft);
}
details.diag-details {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 12px 16px;
  margin-bottom: 14px;
}
details.diag-details summary {
  cursor: pointer;
  font-weight: 600;
  color: var(--ink);
  font-size: 13.5px;
  list-style: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
details.diag-details summary::-webkit-details-marker { display: none; }
.diag-body { margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--line-soft); }
.notes-text { margin: 0; white-space: pre-line; color: var(--ink-2); }
.footer {
  margin-top: 22px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
  color: var(--muted);
  font-size: 12px;
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 8px;
}
@media (max-width: 640px) {
  .page { padding: 14px 12px 28px; }
  .hero { padding: 16px; }
  .wf-tooltip { position: static; margin-top: 6px; max-width: none; }
}
""".strip()

_EDITORIAL_JS = r"""
(function () {
  var SI = [
    [-15, "f"], [-12, "p"], [-9, "n"], [-6, "u"], [-3, "m"],
    [0, ""], [3, "k"], [6, "M"], [9, "G"], [12, "T"]
  ];
  var NON_SI = { "dB": 1, "dBc": 1, "dBm": 1, "dBV": 1, "deg": 1, "%": 1, "x": 1, "X": 1 };
  function fmtSI(val, unit) {
    if (!isFinite(val)) return "N/A";
    var u = unit || "";
    if (val === 0) return u ? ("0 " + u) : "0";
    if (!u || NON_SI[u]) {
      var a = Math.abs(val);
      var s = (a >= 1e-3 && a < 1e5) ? Number(val.toPrecision(4)).toString() : val.toExponential(2);
      return u ? (s + (u === "%" ? "" : " ") + u) : s;
    }
    var exp3 = Math.floor(Math.log10(Math.abs(val)) / 3) * 3;
    if (exp3 < -15) exp3 = -15;
    if (exp3 > 12) exp3 = 12;
    var pref = "";
    for (var i = 0; i < SI.length; i++) {
      if (SI[i][0] === exp3) { pref = SI[i][1]; break; }
    }
    var sc = val / Math.pow(10, exp3);
    var txt = Number(sc.toPrecision(4)).toString();
    return txt + " " + pref + u;
  }

  var cards = document.querySelectorAll(".wf-card[data-wf-meta]");
  for (var c = 0; c < cards.length; c++) {
    (function (card) {
      var meta;
      try { meta = JSON.parse(card.getAttribute("data-wf-meta") || "{}"); } catch (e) { return; }
      var svg = card.querySelector(".wf-svg");
      var hit = card.querySelector(".wf-hit");
      var xhair = card.querySelector(".wf-xhair-v");
      var dotsG = card.querySelector(".wf-xhair-dots");
      var tip = card.querySelector(".wf-tooltip");
      var hidden = {};

      var btns = card.querySelectorAll(".wf-leg-btn[data-trace-idx]");
      for (var b = 0; b < btns.length; b++) {
        btns[b].addEventListener("click", function () {
          var idx = this.getAttribute("data-trace-idx");
          var nextHidden = !hidden[idx];
          hidden[idx] = nextHidden;
          this.classList.toggle("is-hidden", nextHidden);
          this.setAttribute("aria-pressed", nextHidden ? "false" : "true");
          var tr = card.querySelector('.wf-trace[data-trace-idx="' + idx + '"]');
          if (tr) tr.classList.toggle("is-hidden", nextHidden);
          var row = card.querySelector('.wf-stat-row[data-trace-idx="' + idx + '"]');
          if (row) row.classList.toggle("is-hidden", nextHidden);
        });
      }

      if (!svg || !hit || !xhair || !dotsG || !tip) return;
      var polyPoints = {};
      var polys = svg.querySelectorAll("polyline.wf-line[data-trace-idx]");
      for (var p = 0; p < polys.length; p++) {
        var idx = polys[p].getAttribute("data-trace-idx");
        var raw = (polys[p].getAttribute("points") || "").trim();
        var arr = [];
        if (raw) {
          var toks = raw.split(/\s+/);
          for (var k = 0; k < toks.length; k++) {
            var xy = toks[k].split(",");
            if (xy.length === 2) {
              var px = parseFloat(xy[0]), py = parseFloat(xy[1]);
              if (isFinite(px) && isFinite(py)) arr.push([px, py]);
            }
          }
        }
        polyPoints[idx] = arr;
      }

      function nearestPoint(arr, targetX) {
        if (!arr || !arr.length) return null;
        var lo = 0, hi = arr.length - 1;
        while (lo < hi) {
          var mid = (lo + hi) >> 1;
          if (arr[mid][0] < targetX) lo = mid + 1; else hi = mid;
        }
        if (lo > 0 && Math.abs(arr[lo - 1][0] - targetX) < Math.abs(arr[lo][0] - targetX)) {
          return arr[lo - 1];
        }
        return arr[lo];
      }

      hit.addEventListener("mousemove", function (evt) {
        var rect = svg.getBoundingClientRect();
        if (!rect.width) return;
        var sx = (evt.clientX - rect.left) * (meta.w / rect.width);
        var cx = Math.max(meta.padL, Math.min(meta.padL + meta.plotW, sx));
        xhair.setAttribute("x1", cx.toFixed(1));
        xhair.setAttribute("x2", cx.toFixed(1));
        xhair.setAttribute("visibility", "visible");

        var xVal = meta.xMin + ((cx - meta.padL) / meta.plotW) * (meta.xMax - meta.xMin);
        var rowsHtml = '<div style="margin-bottom:4px;color:#cbd5e1">' +
          (meta.xLabel || "x") + " = <strong>" + fmtSI(xVal, meta.xUnit) + "</strong></div>";
        var dotMarkup = "";
        var traces = meta.traces || [];
        for (var i = 0; i < traces.length; i++) {
          var t = traces[i];
          var k = String(t.idx);
          if (hidden[k]) continue;
          var pt = nearestPoint(polyPoints[k], cx);
          if (!pt) continue;
          var yVal = meta.yMax - ((pt[1] - meta.padT) / meta.plotH) * (meta.yMax - meta.yMin);
          dotMarkup += '<circle cx="' + pt[0].toFixed(1) + '" cy="' + pt[1].toFixed(1) +
            '" r="3.4" fill="' + t.color + '" stroke="#ffffff" stroke-width="1.2"/>';
          rowsHtml += '<div class="wf-tt-row"><span><span class="wf-dot" style="background:' +
            t.color + '"></span>' + t.name.replace(/</g, "&lt;") + '</span><span>' +
            fmtSI(yVal, t.unit) + '</span></div>';
        }
        dotsG.innerHTML = dotMarkup;
        tip.innerHTML = rowsHtml;
        tip.hidden = false;
      });

      hit.addEventListener("mouseleave", function () {
        xhair.setAttribute("visibility", "hidden");
        dotsG.innerHTML = "";
        tip.hidden = true;
      });
    })(cards[c]);
  }
})();
""".strip()


def _status_pill_class(status: str | None) -> str:
    s = str(status or "").strip().lower()
    if s in ("pass", "passed", "ok", "valid", "completed", "passed_within_scope", "verified_within_scope", "simulator_complete_pending_measurements"):
        return "pass"
    if s in ("fail", "failed", "invalid", "error", "violations_detected", "mismatch", "simulator_failed", "job_setup_failed", "input_hash_failure", "timed_out"):
        return "fail"
    if s in ("warn", "warning", "incomplete", "cancelled", "running", "running_observed", "submitted", "queued", "launch_outcome_unknown", "reconcile_required"):
        return "warn"
    if s in ("info", "prepared", "staged"):
        return "info"
    return ""


def wrap_editorial_html(
    *,
    title: str,
    subtitle: str = "",
    badge: str = "",
    meta_items: list[tuple[str, str]] | list[dict] | None = None,
    summary: str = "",
    highlights: list[str] | None = None,
    kpis: list[dict] | None = None,
    sections_html: str = "",
    notes: str = "",
    footer_note: str = "",
    mode: str = "fast",
) -> str:
    """Wrap report content in a publication-grade, light-theme, zero-CDN HTML5 document."""
    clean_mode = str(mode or "fast").strip().lower()
    if badge:
        badge_text = badge
    elif clean_mode == "deep":
        badge_text = "🎨 深度报告"
    elif clean_mode == "instant":
        badge_text = "📈 波形初稿"
    else:
        badge_text = "⚡ 快速报告"
    badge_cls = "badge deep" if clean_mode == "deep" else ("badge instant" if clean_mode == "instant" else "badge")

    meta_html_items: list[str] = []
    for item in (meta_items or []):
        if isinstance(item, dict):
            k = str(item.get("label") or item.get("key") or "").strip()
            v = str(item.get("value") if item.get("value") is not None else "—").strip()
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            k = str(item[0]).strip()
            v = str(item[1] if item[1] is not None else "—").strip()
        else:
            continue
        if k:
            meta_html_items.append(
                f'<span class="meta-chip"><span class="meta-k">{html.escape(k)}</span>'
                f'<span class="meta-v">{html.escape(v or "—")}</span></span>'
            )

    meta_block = f'<div class="meta-strip">{"".join(meta_html_items)}</div>' if meta_html_items else ""
    subtitle_html = f'<p class="subtitle">{html.escape(subtitle)}</p>' if subtitle else ""

    summary_block = ""
    clean_highlights = [str(x).strip() for x in (highlights or []) if isinstance(x, str) and x.strip()]
    if (isinstance(summary, str) and summary.strip()) or clean_highlights:
        sum_p = f'<p class="summary-text">{html.escape(summary.strip())}</p>' if (isinstance(summary, str) and summary.strip()) else ""
        hl_ul = ""
        if clean_highlights:
            li_items = "".join(f"<li>{html.escape(item)}</li>" for item in clean_highlights)
            hl_ul = f'<ul class="highlights">{li_items}</ul>'
        summary_block = (
            f'<section class="summary-box">'
            f'<div class="summary-label">核心结论摘要</div>'
            f'{sum_p}{hl_ul}'
            f'</section>'
        )

    kpi_block = ""
    kpi_cards: list[str] = []
    for item in (kpis or []):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("name") or "").strip()
        if not label:
            continue
        raw_val = item.get("value")
        unit = str(item.get("unit") or "").strip()
        if isinstance(raw_val, (int, float)) and not isinstance(raw_val, bool):
            val_str = format_si(raw_val, unit) if unit else _compact_scaled_number(float(raw_val))
        else:
            val_str = str(raw_val if raw_val is not None else "—").strip() or "—"
            if unit and not val_str.endswith(unit):
                val_str = f"{val_str} {unit}"
        status = item.get("status")
        status_label = str(item.get("status_label") or status or "").strip()
        pill_html = ""
        if status_label:
            pcls = _status_pill_class(status)
            pill_html = f'<span class="pill {pcls}">{html.escape(status_label)}</span>'
        sub_parts: list[str] = []
        if item.get("target"):
            sub_parts.append(f"Spec: {item['target']}")
        if item.get("note"):
            sub_parts.append(str(item["note"]))
        sub_html = f'<div class="kpi-sub">{html.escape(" · ".join(sub_parts))}</div>' if sub_parts else ""
        kpi_cards.append(
            f'<div class="kpi-card">'
            f'<div class="kpi-head"><span class="kpi-label">{html.escape(label)}</span>{pill_html}</div>'
            f'<div class="kpi-val">{html.escape(val_str)}</div>'
            f'{sub_html}'
            f'</div>'
        )
    if kpi_cards:
        kpi_block = f'<section class="kpi-grid">{"".join(kpi_cards)}</section>'

    notes_block = ""
    if isinstance(notes, str) and notes.strip():
        notes_block = (
            f'<section class="section">'
            f'<h2 class="section-title">工程备注与后续建议</h2>'
            f'<p class="notes-text">{html.escape(notes.strip())}</p>'
            f'</section>'
        )

    footer_left = html.escape(footer_note or "Generated locally by SpiceOwl deterministic report engine (offline self-contained HTML).")
    footer_right = html.escape(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))

    return (
        "<!doctype html>\n"
        '<html lang="zh-CN">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n"
        f"<style>\n{_EDITORIAL_CSS}\n</style>\n"
        "</head>\n"
        "<body>\n"
        '<main class="page">\n'
        '<header class="hero">\n'
        '<div class="hero-top">\n'
        f'<div><h1>{html.escape(title)}</h1>{subtitle_html}</div>\n'
        f'<span class="{badge_cls}">{html.escape(badge_text)}</span>\n'
        "</div>\n"
        f"{meta_block}\n"
        "</header>\n"
        f"{summary_block}\n"
        f"{kpi_block}\n"
        f"{sections_html}\n"
        f"{notes_block}\n"
        f'<footer class="footer"><span>{footer_left}</span><span>{footer_right}</span></footer>\n'
        "</main>\n"
        f"<script>\n{_EDITORIAL_JS}\n</script>\n"
        "</body>\n"
        "</html>\n"
    )


# ---------------------------------------------------------------------------
# Plot & Image Section Rendering Helpers
# ---------------------------------------------------------------------------

def _resolve_dataset_for_plot(wf_idx: dict | None, requested_dataset: str | None, exprs: list[str]) -> tuple[str | None, dict | None]:
    datasets = wf_idx.get("datasets") if isinstance(wf_idx, dict) and isinstance(wf_idx.get("datasets"), list) else []
    if not datasets:
        return requested_dataset, None
    if requested_dataset:
        for ds in datasets:
            if isinstance(ds, dict) and (ds.get("dataset") == requested_dataset or ds.get("name") == requested_dataset):
                return str(ds.get("dataset") or ds.get("name")), ds
        return requested_dataset, None

    # Auto-resolve dataset containing the requested raw signals
    ranked = sorted([d for d in datasets if isinstance(d, dict)], key=_dataset_rank, reverse=True)
    for ds in ranked:
        sig_names = {s["name"] for s in _extract_ds_signals(ds)}
        ci_names = {n.lower() for n in sig_names}
        needed: list[str] = []
        for expr in exprs:
            try:
                parsed = parse_signal_expression(expr, sig_names)
                needed.extend(parsed["raw_signals"])
            except DomainError:
                continue
        if needed and all(n in sig_names or n.lower() in ci_names for n in needed):
            return str(ds.get("dataset") or ds.get("name")), ds
    top = ranked[0]
    return str(top.get("dataset") or top.get("name")), top


def _render_plot_specs_for_run(
    manager: Any,
    run_id: str,
    wf_idx: dict | None,
    plot_specs: list[dict],
) -> tuple[list[str], list[dict], int]:
    """Query waveforms and render SVG cards; returns (html_cards, stats_records, ok_count)."""
    cards_html: list[str] = []
    stats_records: list[dict] = []
    ok_count = 0

    for spec in plot_specs:
        if not isinstance(spec, dict):
            continue
        raw_signals_req = spec.get("signals")
        if not isinstance(raw_signals_req, list) or not raw_signals_req:
            continue
        expr_list = [str(s).strip() for s in raw_signals_req if isinstance(s, str) and s.strip()]
        if not expr_list:
            continue

        plot_title = str(spec.get("title") or ", ".join(expr_list[:3])).strip()
        caption = str(spec.get("caption") or "").strip()
        req_ds = str(spec.get("dataset")).strip() if isinstance(spec.get("dataset"), str) and spec["dataset"].strip() else None
        ds_name, ds_meta = _resolve_dataset_for_plot(wf_idx, req_ds, expr_list)
        if not ds_name:
            cards_html.append(
                f'<div class="plot-warn"><strong>{html.escape(plot_title)}</strong>：未找到可用的本地波形数据集。</div>'
            )
            continue

        ds_sigs = _extract_ds_signals(ds_meta) if isinstance(ds_meta, dict) else []
        known_names = {s["name"] for s in ds_sigs}
        sig_units = {s["name"]: s["unit"] for s in ds_sigs if s.get("unit")}
        sweep_meta = _as_dict(ds_meta.get("sweep")) if isinstance(ds_meta, dict) else {}

        try:
            parsed_exprs = [parse_signal_expression(expr, known_names or None) for expr in expr_list]
            raw_fetch: list[str] = []
            seen_raw: set[str] = set()
            for p in parsed_exprs:
                for rs in p["raw_signals"]:
                    if rs not in seen_raw:
                        seen_raw.add(rs)
                        raw_fetch.append(rs)

            x_min = float(spec["x_min"]) if _is_finite_number(spec.get("x_min")) else None
            x_max = float(spec["x_max"]) if _is_finite_number(spec.get("x_max")) else None
            q = manager.waveform_query(
                run_id,
                ds_name,
                raw_fetch,
                x_min=x_min,
                x_max=x_max,
                max_points=DEFAULT_QUERY_MAX_POINTS,
                allow_remote=False,
            )
            series_list = evaluate_query_expressions(q, parsed_exprs, signal_units=sig_units)
            q_sweep = _as_dict(q.get("sweep"))
            x_label = str(q_sweep.get("name") or sweep_meta.get("name") or "time")
            x_unit = str(q_sweep.get("unit") or sweep_meta.get("unit") or ("s" if x_label.lower() == "time" else ""))
            y_unit = next((s["unit"] for s in series_list if s.get("unit")), "")
            if not caption:
                rep_mode = "精确采样" if q.get("representation") == "exact_window" else "Min-Max 包络预览"
                win_cnt = q_sweep.get("window_count") or q_sweep.get("total_count")
                cnt_part = f" · {win_cnt} 点" if win_cnt is not None else ""
                caption = f"数据源: {ds_name} ({rep_mode}{cnt_part}) · 点击图例开关曲线，悬停查看精确坐标"

            cards_html.append(
                render_waveform_svg(
                    series_list,
                    title=plot_title,
                    x_label=x_label,
                    x_unit=x_unit,
                    y_unit=y_unit,
                    caption=caption,
                )
            )
            ok_count += 1
            for s in series_list:
                stats_records.append({
                    "plot": plot_title,
                    "dataset": ds_name,
                    "signal": s["name"],
                    "unit": s.get("unit") or y_unit,
                    "stats": s.get("stats") or {},
                })
        except (DomainError, ValueError, KeyError, TypeError) as exc:
            cards_html.append(
                f'<div class="plot-warn"><strong>{html.escape(plot_title)}</strong>（数据集 {html.escape(ds_name)}）：'
                f'波形提取失败（{html.escape(str(exc))}）。</div>'
            )

    return cards_html, stats_records, ok_count


def _render_images_html(images: list[dict] | None) -> str:
    if not isinstance(images, list) or not images:
        return ""
    blocks: list[str] = []
    for item in images[:16]:
        if not isinstance(item, dict):
            continue
        uri = item.get("data_uri")
        if not isinstance(uri, str):
            continue
        clean_uri = uri.strip()
        if not clean_uri.startswith("data:image/") or any(ch in clean_uri for ch in ('"', "'", "<", ">", "\n", "\r")):
            continue
        img_title = str(item.get("title") or "").strip()
        img_cap = str(item.get("caption") or "").strip()
        title_html = f'<div class="wf-title" style="margin-bottom:8px">{html.escape(img_title)}</div>' if img_title else ""
        cap_html = f'<figcaption class="wf-caption">{html.escape(img_cap)}</figcaption>' if img_cap else ""
        blocks.append(
            f'<figure class="img-card">{title_html}'
            f'<img src="{clean_uri}" alt="{html.escape(img_title or "仿真图表")}" loading="lazy"/>'
            f'{cap_html}</figure>'
        )
    if not blocks:
        return ""
    return f'<section class="plots-grid">{"".join(blocks)}</section>'


def _extract_diag_counts(rep: dict | None) -> tuple[int | None, int | None, int | None, list[dict]]:
    if not isinstance(rep, dict):
        return None, None, None, []
    diag_block = _as_dict(rep.get("diagnostics"))
    obs_ctx = _as_dict(diag_block.get("observed_context"))
    warn_sum = _as_dict(obs_ctx.get("warning_summary"))
    cov = _as_dict(obs_ctx.get("diagnostic_coverage"))
    errs = warn_sum.get("reported_errors")
    if errs is None:
        errs = cov.get("observed_errors")
    warns = warn_sum.get("reported_warnings")
    if warns is None:
        warns = cov.get("observed_warnings")
    notices = warn_sum.get("reported_notices")
    if notices is None:
        notices = cov.get("observed_notices")
    items = diag_block.get("diagnostics") if isinstance(diag_block.get("diagnostics"), list) else []
    return errs, warns, notices, [x for x in items if isinstance(x, dict)]


def _human_state_label(record: dict) -> tuple[str, str]:
    state = str(record.get("state") or "unknown")
    raw = str(record.get("raw_remote_state") or "")
    if state == "simulator_complete_pending_measurements" or raw == "completed":
        return "已完成 (Completed)", "pass"
    if state in ("simulator_failed", "job_setup_failed", "input_hash_failure") or raw in ("failed", "timed_out", "preflight_failed"):
        return f"失败 ({raw or state})", "fail"
    if state == "cancelled" or raw == "cancelled":
        return "已取消 (Cancelled)", "warn"
    if state in ("submitted", "running_observed"):
        return f"运行中 ({raw or state})", "warn"
    return state, "info"


# ---------------------------------------------------------------------------
# Public Fast Report Builders (Single-Run & Scope Rollup)
# ---------------------------------------------------------------------------

def build_fast_run_report(
    manager: Any,
    run_id: str,
    *,
    title: str | None = None,
    summary: str | None = None,
    highlights: list[str] | None = None,
    kpis: list[dict] | None = None,
    plots: list[dict] | None = None,
    notes: str | None = None,
    images: list[dict] | None = None,
    source: str = "agent_fast",
    mode: str = "fast",
) -> dict:
    """Deterministically assemble a rich single-run HTML+SVG report and Markdown summary."""
    record = manager.read(run_id)
    rep: dict | None = None
    try:
        rep = manager.report(run_id)
    except DomainError:
        rep = None
    wf_idx: dict | None = None
    try:
        wf_idx = manager.waveform_index(run_id, allow_remote=False)
    except DomainError:
        wf_idx = None

    org = _as_dict(record.get("organization"))
    project = str(org.get("project") or record.get("project_name") or "未归类项目")
    library = org.get("library") or record.get("library_name")
    testbench = str(org.get("testbench") or record.get("cell_name") or run_id)
    view = org.get("view") or record.get("view_name")

    host_id = str(record.get("host_id") or "unknown")
    simulator = str(record.get("simulator") or "unknown")
    backend = str(record.get("backend") or "unknown")
    limits = _as_dict(record.get("limits"))
    cpu_cnt = limits.get("cpu_count")
    cpu_str = f"{cpu_cnt}C" if isinstance(cpu_cnt, int) and not isinstance(cpu_cnt, bool) else "—"
    mem_str = _format_mem_gib(limits.get("memory_bytes"))
    exit_code = record.get("simulator_exit_code")
    exit_str = str(exit_code) if exit_code is not None else "—"
    state_label, state_status = _human_state_label(record)
    ts_str = _format_timestamp(record.get("updated_at") or record.get("created_at"))

    errs, warns, notices, diag_items = _extract_diag_counts(rep)
    datasets = wf_idx.get("datasets") if isinstance(wf_idx, dict) and isinstance(wf_idx.get("datasets"), list) else []

    # Plots: auto-select if caller omitted plots
    effective_plot_specs = auto_select_plot_specs(wf_idx, max_plots=3) if plots is None else list(plots)
    plot_cards_html, plot_stats, plot_count = _render_plot_specs_for_run(manager, run_id, wf_idx, effective_plot_specs)

    clean_mode = str(mode or "fast").strip().lower() or "fast"
    clean_source = str(source or "agent_fast").strip() or "agent_fast"

    final_title = (title.strip() if isinstance(title, str) and title.strip() else f"{testbench} 仿真分析报告 · {run_id}")
    if isinstance(summary, str) and summary.strip():
        final_summary = summary.strip()
    else:
        diag_note = ""
        if errs is not None or warns is not None:
            diag_note = f"，日志诊断 errors={errs if errs is not None else 0} / warnings={warns if warns is not None else 0}"
        final_summary = (
            f"任务 {run_id}（{project} / {testbench}）在 {host_id} 上使用 {simulator} ({backend}) "
            f"当前状态为「{state_label}」（退出码 {exit_str}），已索引 {len(datasets)} 个波形数据集并绘制 {plot_count} 张交互式波形图{diag_note}。"
        )

    # Metadata chips
    meta_items: list[tuple[str, str]] = [
        ("Project", project),
        ("Testbench", testbench),
        ("Run ID", run_id),
        ("Host", host_id),
        ("Simulator", simulator),
        ("Backend", backend),
        ("CPU / Mem", f"{cpu_str} / {mem_str}"),
        ("State", state_label),
        ("Exit Code", exit_str),
        ("Updated", ts_str),
    ]
    if library and library != project:
        meta_items.insert(1, ("Library", str(library)))
    if view:
        meta_items.insert(3 if library and library != project else 2, ("View", str(view)))

    # KPIs (use caller KPIs if provided, else factual defaults)
    final_kpis: list[dict]
    if isinstance(kpis, list) and kpis:
        final_kpis = list(kpis)
    else:
        coll = _as_dict(record.get("collection"))
        file_cnt = coll.get("file_count")
        final_kpis = [
            {"label": "执行状态", "value": state_label, "status": state_status, "note": f"exit={exit_str}"},
            {
                "label": "波形数据集",
                "value": str(len(datasets)),
                "status": "pass" if datasets else "info",
                "note": f"绘制 {plot_count} 张图 · 回收 {file_cnt if file_cnt is not None else 0} 个文件",
            },
            {
                "label": "日志错误 / 告警",
                "value": f"{errs if errs is not None else '—'} / {warns if warns is not None else '—'}",
                "status": "fail" if (isinstance(errs, int) and errs > 0) else ("warn" if (isinstance(warns, int) and warns > 0) else "pass"),
                "note": f"notices={notices if notices is not None else '—'}",
            },
        ]
        if plot_stats:
            top_st = plot_stats[0]
            st_vals = top_st["stats"]
            if _is_finite_number(st_vals.get("p2p")):
                final_kpis.append({
                    "label": f"{top_st['signal']} 峰峰值",
                    "value": format_si(st_vals.get("p2p"), top_st["unit"]),
                    "status": "info",
                    "note": f"Final {format_si(st_vals.get('final'), top_st['unit'])}",
                })

    # Five-dimensional status strip
    sections: list[str] = []
    dim_specs = (
        ("执行状态 Execution", "execution_status"),
        ("测量有效性 Measurement", "measurement_validity"),
        ("功能指标 Performance", "functional_performance"),
        ("工作区与 SOA Operating", "operating_qualification"),
        ("依赖可追溯 Dependencies", "dependency_provenance"),
    )
    dim_cards: list[str] = []
    for dim_label, dim_key in dim_specs:
        dim_obj = _as_dict(rep.get(dim_key)) if isinstance(rep, dict) else {}
        st_val = str(dim_obj.get("status") or ("completed" if (dim_key == "execution_status" and state_status == "pass") else "unknown"))
        pcls = _status_pill_class(st_val)
        dim_cards.append(
            f'<div class="dim-item">'
            f'<div class="dim-name">{html.escape(dim_label)}</div>'
            f'<span class="pill {pcls}">{html.escape(st_val)}</span>'
            f'</div>'
        )
    sections.append(
        f'<section class="section">'
        f'<h2 class="section-title">五维验证状态（Five-Dimensional Status）</h2>'
        f'<div class="dim-strip">{"".join(dim_cards)}</div>'
        f'</section>'
    )

    # Waveform plots section
    if plot_cards_html:
        sections.append(f'<section class="plots-grid">{"".join(plot_cards_html)}</section>')

    # Optional embedded PNG images
    img_html = _render_images_html(images)
    if img_html:
        sections.append(img_html)

    # Collapsible diagnostics & warnings
    if rep is not None:
        diag_summary_txt = (
            f"errors={errs if errs is not None else '—'}, "
            f"warnings={warns if warns is not None else '—'}, "
            f"notices={notices if notices is not None else '—'}"
        )
        diag_rows: list[str] = []
        for item in diag_items[:25]:
            sev = str(item.get("severity") or "info")
            code = str(item.get("code") or item.get("category") or "unknown")
            lines_ref = item.get("evidence_line_numbers")
            lines_txt = ", ".join(str(x) for x in lines_ref[:8]) if isinstance(lines_ref, list) and lines_ref else "—"
            detail = str(item.get("recommended_action") or item.get("summary") or item.get("excerpt") or item.get("category") or "—")
            diag_rows.append(
                f'<tr>'
                f'<td><span class="pill {_status_pill_class(sev)}">{html.escape(sev)}</span></td>'
                f'<td class="mono">{html.escape(code)}</td>'
                f'<td class="mono">{html.escape(lines_txt)}</td>'
                f'<td>{html.escape(detail[:240])}</td>'
                f'</tr>'
            )
        diag_table = (
            f'<div class="table-wrap"><table class="data-table">'
            f'<thead><tr><th>级别</th><th>诊断代码</th><th>日志行号</th><th>说明 / 建议</th></tr></thead>'
            f'<tbody>{"".join(diag_rows)}</tbody></table></div>'
            if diag_rows
            else '<p class="notes-text">未检测到额外日志异常条目。</p>'
        )
        sections.append(
            f'<details class="diag-details">'
            f'<summary><span>日志诊断与告警详情</span><span class="mono" style="font-size:12px;color:#64748b">{html.escape(diag_summary_txt)}</span></summary>'
            f'<div class="diag-body">{diag_table}</div>'
            f'</details>'
        )

    # Collapsible waveform dataset inventory
    if datasets:
        ds_rows: list[str] = []
        for ds in datasets[:20]:
            if not isinstance(ds, dict):
                continue
            ds_n = str(ds.get("dataset") or ds.get("name") or "unknown")
            ds_f = str(ds.get("format") or "—")
            sw = _as_dict(ds.get("sweep"))
            sw_cnt = sw.get("count")
            sig_cnt = ds.get("signal_count") or len(ds.get("signals") or [])
            ds_bytes = _format_bytes(ds.get("bytes"))
            ds_rows.append(
                f'<tr><td class="mono">{html.escape(ds_n)}</td><td>{html.escape(ds_f)}</td>'
                f'<td class="num">{html.escape(str(sw_cnt if sw_cnt is not None else "—"))}</td>'
                f'<td class="num">{html.escape(str(sig_cnt))}</td>'
                f'<td class="num">{html.escape(ds_bytes)}</td></tr>'
            )
        sections.append(
            f'<details class="diag-details">'
            f'<summary><span>波形数据集清单（{len(datasets)} 个）</span><span class="mono" style="font-size:12px;color:#64748b">results.raw</span></summary>'
            f'<div class="diag-body"><div class="table-wrap"><table class="data-table">'
            f'<thead><tr><th>Dataset</th><th>Format</th><th class="num">Samples</th><th class="num">Signals</th><th class="num">Size</th></tr></thead>'
            f'<tbody>{"".join(ds_rows)}</tbody></table></div></div>'
            f'</details>'
        )

    html_doc = wrap_editorial_html(
        title=final_title,
        subtitle=f"{project} / {testbench} · {run_id}",
        meta_items=meta_items,
        summary=final_summary,
        highlights=highlights,
        kpis=final_kpis,
        sections_html="\n".join(sections),
        notes=notes or "",
        footer_note=f"SpiceOwl 仿真报告 · {project} / {testbench} · {run_id}",
        mode=clean_mode,
    )

    # Concise Markdown summary
    md_lines: list[str] = [
        f"# {final_title}",
        "",
        final_summary,
        "",
        f"- **任务 ID**: `{run_id}` | **项目/TB**: `{project} / {testbench}` | **主机**: `{host_id}` ({simulator} / {backend})",
        f"- **状态**: {state_label} | **退出码**: `{exit_str}` | **已绘波形图**: {plot_count} 张",
    ]
    if highlights:
        md_lines.append("")
        md_lines.append("## 关键发现")
        for hl in highlights:
            if isinstance(hl, str) and hl.strip():
                md_lines.append(f"- {hl.strip()}")
    if final_kpis:
        md_lines.append("")
        md_lines.append("## 核心指标")
        md_lines.append("| 指标 | 数值 | 状态 |")
        md_lines.append("|---|---:|---|")
        for kpi in final_kpis:
            if isinstance(kpi, dict) and kpi.get("label"):
                v_txt = str(kpi.get("value") if kpi.get("value") is not None else "—")
                u_txt = str(kpi.get("unit") or "")
                if u_txt and not v_txt.endswith(u_txt):
                    v_txt = f"{v_txt} {u_txt}"
                md_lines.append(f"| {kpi['label']} | {v_txt} | {kpi.get('status') or '—'} |")
    if plot_stats:
        md_lines.append("")
        md_lines.append("## 实测波形统计")
        md_lines.append("| Signal | Min | Max | P2P | Final |")
        md_lines.append("|---|---:|---:|---:|---:|")
        for row in plot_stats[:24]:
            st = row["stats"]
            su = row["unit"]
            md_lines.append(
                f"| `{row['signal']}` | {format_si(st.get('min'), su)} | {format_si(st.get('max'), su)} "
                f"| {format_si(st.get('p2p'), su)} | {format_si(st.get('final'), su)} |"
            )
    if isinstance(notes, str) and notes.strip():
        md_lines.extend(["", "## 工程备注", notes.strip()])

    return {
        "title": final_title,
        "summary": final_summary,
        "html": html_doc,
        "markdown": "\n".join(md_lines).strip() + "\n",
        "plot_count": plot_count,
        "mode": clean_mode,
        "source": clean_source,
    }


def build_fast_scope_report(
    manager: Any,
    *,
    scope: str,
    project: str,
    testbench: str | None = None,
    run_ids: list[str] | None = None,
    title: str | None = None,
    summary: str | None = None,
    highlights: list[str] | None = None,
    kpis: list[dict] | None = None,
    comparison_rows: list[dict] | None = None,
    plots: list[dict] | None = None,
    notes: str | None = None,
    images: list[dict] | None = None,
    source: str = "agent_fast",
    mode: str = "fast",
) -> dict:
    """Deterministically assemble a Project-level or Testbench-level HTML+SVG comparison report."""
    if scope not in ("project", "testbench"):
        raise DomainError("scope must be 'project' or 'testbench'.", "invalid_scope_report")
    if not isinstance(project, str) or not project.strip():
        raise DomainError("project must be a non-empty string.", "invalid_scope_report")
    clean_proj = project.strip()
    clean_tb: str | None = None
    if scope == "testbench":
        if not isinstance(testbench, str) or not testbench.strip():
            raise DomainError("testbench must be a non-empty string when scope is 'testbench'.", "invalid_scope_report")
        clean_tb = testbench.strip()

    all_runs = manager.list()
    runs_by_id = {str(r["run_id"]): r for r in all_runs if isinstance(r, dict) and r.get("run_id")}

    matched_runs: list[dict] = []
    if isinstance(run_ids, list) and run_ids:
        seen: set[str] = set()
        for raw_rid in run_ids:
            if not isinstance(raw_rid, str) or not raw_rid.strip():
                continue
            rid = raw_rid.strip()
            if rid in seen:
                continue
            seen.add(rid)
            if rid in runs_by_id:
                matched_runs.append(runs_by_id[rid])
            else:
                try:
                    matched_runs.append(manager.read(rid))
                except DomainError:
                    continue
    else:
        for r in all_runs:
            if not isinstance(r, dict):
                continue
            org = _as_dict(r.get("organization"))
            r_proj = org.get("project") or r.get("project_name") or "未归类项目"
            r_tb = org.get("testbench") or r.get("cell_name")
            if r_proj != clean_proj:
                continue
            if scope == "testbench" and r_tb != clean_tb:
                continue
            matched_runs.append(r)

    matched_run_ids = [str(r["run_id"]) for r in matched_runs if r.get("run_id")]

    # Aggregate statistics
    total_cnt = len(matched_runs)
    completed_cnt = 0
    failed_cnt = 0
    active_cnt = 0
    tb_set: set[str] = set()
    for r in matched_runs:
        org = _as_dict(r.get("organization"))
        tb_n = org.get("testbench") or r.get("cell_name")
        if tb_n:
            tb_set.add(str(tb_n))
        _, st_cls = _human_state_label(r)
        if st_cls == "pass":
            completed_cnt += 1
        elif st_cls == "fail":
            failed_cnt += 1
        elif str(r.get("state") or "") in ("submitted", "running_observed", "launch_outcome_unknown", "reconcile_required"):
            active_cnt += 1

    clean_mode = str(mode or "fast").strip().lower() or "fast"
    clean_source = str(source or "agent_fast").strip() or "agent_fast"

    default_title = (
        f"{clean_proj} / {clean_tb} 综合仿真报告"
        if scope == "testbench"
        else f"{clean_proj} 项目综合仿真报告"
    )
    final_title = title.strip() if isinstance(title, str) and title.strip() else default_title

    if isinstance(summary, str) and summary.strip():
        final_summary = summary.strip()
    else:
        scope_desc = f"{clean_proj} / {clean_tb}" if scope == "testbench" else f"项目 {clean_proj}（覆盖 {len(tb_set)} 个 Testbench）"
        final_summary = (
            f"{scope_desc} 共纳入 {total_cnt} 次仿真任务：已完成 {completed_cnt} 次、"
            f"失败/需关注 {failed_cnt} 次、进行中 {active_cnt} 次。"
        )

    meta_items: list[tuple[str, str]] = [
        ("Scope", "Testbench" if scope == "testbench" else "Project"),
        ("Project", clean_proj),
    ]
    if clean_tb:
        meta_items.append(("Testbench", clean_tb))
    else:
        meta_items.append(("Testbenches", str(len(tb_set))))
    meta_items.extend([
        ("Total Runs", str(total_cnt)),
        ("Completed", str(completed_cnt)),
        ("Failed", str(failed_cnt)),
        ("Active", str(active_cnt)),
    ])

    auto_kpis = [
        {"label": "纳入任务总数", "value": str(total_cnt), "status": "info"},
        {"label": "已完成", "value": str(completed_cnt), "status": "pass" if completed_cnt > 0 else "info"},
        {"label": "失败 / 需关注", "value": str(failed_cnt), "status": "fail" if failed_cnt > 0 else "pass"},
        {"label": "运行 / 排队中", "value": str(active_cnt), "status": "warn" if active_cnt > 0 else "info"},
    ]
    if scope == "project":
        auto_kpis.append({"label": "覆盖 Testbench", "value": str(len(tb_set)), "status": "info"})

    final_kpis = list(kpis) if (isinstance(kpis, list) and kpis) else auto_kpis

    sections: list[str] = []

    # Optional custom comparison_rows table
    if isinstance(comparison_rows, list) and comparison_rows:
        valid_rows = [row for row in comparison_rows if isinstance(row, dict) and row]
        if valid_rows:
            cols: list[str] = []
            for row in valid_rows:
                for k in row.keys():
                    if k not in cols:
                        cols.append(str(k))
            ths = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
            trs: list[str] = []
            for row in valid_rows:
                tds = "".join(f"<td>{html.escape(str(row.get(c, '—')))}</td>" for c in cols)
                trs.append(f"<tr>{tds}</tr>")
            sections.append(
                f'<section class="section">'
                f'<h2 class="section-title">核心指标横向对比（Comparison Matrix）</h2>'
                f'<div class="table-wrap"><table class="data-table">'
                f'<thead><tr>{ths}</tr></thead>'
                f'<tbody>{"".join(trs)}</tbody>'
                f'</table></div>'
                f'</section>'
            )

    # Multi-run overview table
    run_rows_html: list[str] = []
    for r in matched_runs:
        rid = str(r.get("run_id") or "—")
        org = _as_dict(r.get("organization"))
        r_tb = str(org.get("testbench") or r.get("cell_name") or "—")
        r_host = str(r.get("host_id") or "—")
        r_sim = str(r.get("simulator") or "—")
        st_lbl, st_cls = _human_state_label(r)
        r_exit = r.get("simulator_exit_code")
        r_exit_str = str(r_exit) if r_exit is not None else "—"
        coll = _as_dict(r.get("collection"))
        f_cnt = coll.get("file_count")
        f_str = str(f_cnt) if f_cnt is not None else "0"
        ai_rep = _as_dict(r.get("ai_report"))
        ai_sum = str(ai_rep.get("summary") or ai_rep.get("title") or "—")
        run_rows_html.append(
            f'<tr>'
            f'<td class="mono">{html.escape(rid)}</td>'
            f'<td>{html.escape(r_tb)}</td>'
            f'<td class="mono">{html.escape(r_host)}</td>'
            f'<td>{html.escape(r_sim)}</td>'
            f'<td><span class="pill {st_cls}">{html.escape(st_lbl)}</span></td>'
            f'<td class="num">{html.escape(r_exit_str)}</td>'
            f'<td class="num">{html.escape(f_str)}</td>'
            f'<td>{html.escape(ai_sum[:160])}</td>'
            f'</tr>'
        )
    if run_rows_html:
        sections.append(
            f'<section class="section">'
            f'<h2 class="section-title">多任务仿真明细（{total_cnt} 条）</h2>'
            f'<div class="table-wrap"><table class="data-table">'
            f'<thead><tr><th>Run ID</th><th>Testbench</th><th>Host</th><th>Simulator</th>'
            f'<th>State</th><th class="num">Exit</th><th class="num">Files</th><th>报告摘要</th></tr></thead>'
            f'<tbody>{"".join(run_rows_html)}</tbody>'
            f'</table></div>'
            f'</section>'
        )
    else:
        sections.append(
            '<section class="section"><p class="notes-text">当前范围内暂无已记录的仿真任务。</p></section>'
        )

    # Representative waveform plots
    plot_cards_all: list[str] = []
    plot_count = 0
    wf_cache: dict[str, dict | None] = {}

    def _get_wf_idx(rid: str) -> dict | None:
        if rid not in wf_cache:
            try:
                wf_cache[rid] = manager.waveform_index(rid, allow_remote=False)
            except DomainError:
                wf_cache[rid] = None
        return wf_cache[rid]

    default_plot_run_id: str | None = None
    for r in matched_runs:
        rid = str(r.get("run_id") or "")
        if not rid:
            continue
        idx = _get_wf_idx(rid)
        if isinstance(idx, dict) and idx.get("datasets"):
            default_plot_run_id = rid
            break

    effective_scope_plots: list[dict] = []
    if plots is None:
        if default_plot_run_id is not None:
            for spec in auto_select_plot_specs(_get_wf_idx(default_plot_run_id), max_plots=2):
                spec_copy = dict(spec)
                spec_copy["run_id"] = default_plot_run_id
                spec_copy["title"] = f"{spec_copy['title']} ({default_plot_run_id})"
                effective_scope_plots.append(spec_copy)
    elif isinstance(plots, list):
        effective_scope_plots = list(plots)

    for spec in effective_scope_plots:
        if not isinstance(spec, dict):
            continue
        target_rid = str(spec.get("run_id") or default_plot_run_id or "").strip()
        if not target_rid:
            continue
        cards, _, ok_c = _render_plot_specs_for_run(manager, target_rid, _get_wf_idx(target_rid), [spec])
        plot_cards_all.extend(cards)
        plot_count += ok_c

    if plot_cards_all:
        sections.append(f'<section class="plots-grid">{"".join(plot_cards_all)}</section>')

    img_html = _render_images_html(images)
    if img_html:
        sections.append(img_html)

    subtitle = f"Testbench 综合报告 · {clean_proj} / {clean_tb}" if scope == "testbench" else f"Project 综合报告 · {clean_proj}"
    html_doc = wrap_editorial_html(
        title=final_title,
        subtitle=subtitle,
        meta_items=meta_items,
        summary=final_summary,
        highlights=highlights,
        kpis=final_kpis,
        sections_html="\n".join(sections),
        notes=notes or "",
        footer_note=f"SpiceOwl 综合仿真报告 · {clean_proj}" + (f" / {clean_tb}" if clean_tb else ""),
        mode=clean_mode,
    )

    md_lines: list[str] = [
        f"# {final_title}",
        "",
        final_summary,
        "",
        f"- **范围**: `{scope}` | **项目**: `{clean_proj}`" + (f" | **Testbench**: `{clean_tb}`" if clean_tb else ""),
        f"- **任务统计**: 总计 {total_cnt} | 已完成 {completed_cnt} | 失败 {failed_cnt} | 进行中 {active_cnt} | 已绘波形图 {plot_count} 张",
    ]
    if highlights:
        md_lines.append("")
        md_lines.append("## 关键发现")
        for hl in highlights:
            if isinstance(hl, str) and hl.strip():
                md_lines.append(f"- {hl.strip()}")
    if matched_runs:
        md_lines.append("")
        md_lines.append("## 仿真任务一览")
        md_lines.append("| Run ID | Testbench | Host | Simulator | State | Exit |")
        md_lines.append("|---|---|---|---|---|---:|")
        for r in matched_runs[:40]:
            org = _as_dict(r.get("organization"))
            st_lbl, _ = _human_state_label(r)
            md_lines.append(
                f"| `{r.get('run_id', '—')}` | {org.get('testbench') or r.get('cell_name') or '—'} "
                f"| {r.get('host_id') or '—'} | {r.get('simulator') or '—'} | {st_lbl} "
                f"| {r.get('simulator_exit_code') if r.get('simulator_exit_code') is not None else '—'} |"
            )
    if isinstance(notes, str) and notes.strip():
        md_lines.extend(["", "## 工程备注", notes.strip()])

    return {
        "title": final_title,
        "summary": final_summary,
        "html": html_doc,
        "markdown": "\n".join(md_lines).strip() + "\n",
        "run_ids": matched_run_ids,
        "plot_count": plot_count,
        "mode": clean_mode,
        "source": clean_source,
    }

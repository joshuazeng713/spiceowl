"""Generic deterministic lossless analog measurement recipes (M3).

Computes standard analog and mixed-signal engineering metrics from exact,
lossless simulation vectors (transient, AC, and DC) using pure Python so it
runs identically across macOS, Linux, and Windows with zero extra dependencies.

Safety invariant:
- Every measurement function enforces ``exact=True`` and
  ``measurement_safe=True``. Downsampled Min-Max envelope previews are
  explicitly rejected with ``measurement_unsafe_downsampled`` so preview
  artifacts are never mistaken for scientific measurements.
- Metrics that cannot be determined from the provided waveform (for example,
  ``fall_time`` on a monotonic rising step or ``phase_margin_deg`` when gain
  never crosses 0 dB) return ``None`` rather than guessing.
"""

from __future__ import annotations

import cmath
import math
from typing import Any

from spiceowl.domain.common import DomainError


ANALOG_METRICS_SCHEMA = "spiceowl.analog-metrics.v1"

SUPPORTED_TRANSIENT_METRICS = frozenset({
    "min",
    "max",
    "peak_to_peak",
    "mean",
    "avg",
    "rms",
    "final_value",
    "end",
    "value",
    "rise_time",
    "fall_time",
    "settling_time",
    "overshoot_pct",
    "undershoot_pct",
    "slew_rate_pos",
    "slew_rate_neg",
    "period_mean",
    "frequency_hz",
    "duty_cycle_pct",
    "period_jitter_rms",
})

SUPPORTED_AC_METRICS = frozenset({
    "dc_gain_db",
    "bw_3db_hz",
    "ugbw_hz",
    "phase_margin_deg",
    "gain_margin_db",
})


def _require_lossless(exact: bool, measurement_safe: bool) -> None:
    if exact is not True or measurement_safe is not True:
        raise DomainError(
            "Refusing analog measurement on downsampled preview; zoom into an exact lossless window or single cycle first.",
            "measurement_unsafe_downsampled",
        )


def _validate_xy(x: list[float], y: list[float]) -> tuple[list[float], list[float]]:
    if not isinstance(x, list) or not isinstance(y, list) or len(x) != len(y) or len(x) < 2:
        raise DomainError("Analog measurement requires aligned x and y vectors with at least 2 finite samples.", "invalid_measurement_input")
    xs: list[float] = []
    ys: list[float] = []
    prev_x: float | None = None
    for xi, yi in zip(x, y):
        if isinstance(xi, bool) or isinstance(yi, bool) or not isinstance(xi, (int, float)) or not isinstance(yi, (int, float)):
            raise DomainError("Waveform samples must be finite real numbers.", "invalid_measurement_input")
        fx, fy = float(xi), float(yi)
        if not (math.isfinite(fx) and math.isfinite(fy)):
            raise DomainError("Waveform samples must be finite real numbers.", "invalid_measurement_input")
        if prev_x is not None and fx < prev_x:
            raise DomainError("Sweep axis must be monotonically non-decreasing.", "invalid_measurement_input")
        xs.append(fx)
        ys.append(fy)
        prev_x = fx
    if xs[-1] <= xs[0]:
        raise DomainError("Sweep axis must cover a positive span.", "invalid_measurement_input")
    return xs, ys


def _interp_crossing(x0: float, y0: float, x1: float, y1: float, level: float) -> float:
    dy = y1 - y0
    if dy == 0.0:
        return 0.5 * (x0 + x1)
    frac = max(0.0, min(1.0, (level - y0) / dy))
    return x0 + frac * (x1 - x0)


def _find_edge_crossings(xs: list[float], ys: list[float], level: float) -> tuple[list[float], list[float]]:
    rising: list[float] = []
    falling: list[float] = []
    for i in range(len(xs) - 1):
        y0, y1 = ys[i], ys[i + 1]
        if y0 < level <= y1:
            rising.append(_interp_crossing(xs[i], y0, xs[i + 1], y1, level))
        elif y0 > level >= y1:
            falling.append(_interp_crossing(xs[i], y0, xs[i + 1], y1, level))
    return rising, falling


def _compute_transition_time(
    xs: list[float],
    ys: list[float],
    low_level: float,
    high_level: float,
    *,
    rising: bool,
) -> float | None:
    """Measure first clean 10%->90% rise time or 90%->10% fall time."""
    if high_level <= low_level:
        return None
    start_level = low_level if rising else high_level
    end_level = high_level if rising else low_level
    t_start: float | None = None
    for i in range(len(xs) - 1):
        y0, y1 = ys[i], ys[i + 1]
        if rising:
            if y0 <= start_level < y1:
                t_start = _interp_crossing(xs[i], y0, xs[i + 1], y1, start_level)
            if t_start is not None and y0 < end_level <= y1:
                t_end = _interp_crossing(xs[i], y0, xs[i + 1], y1, end_level)
                if t_end > t_start:
                    return t_end - t_start
            elif t_start is not None and y1 < start_level:
                t_start = None
        else:
            if y0 >= start_level > y1:
                t_start = _interp_crossing(xs[i], y0, xs[i + 1], y1, start_level)
            if t_start is not None and y0 > end_level >= y1:
                t_end = _interp_crossing(xs[i], y0, xs[i + 1], y1, end_level)
                if t_end > t_start:
                    return t_end - t_start
            elif t_start is not None and y1 > start_level:
                t_start = None
    return None


def measure_transient_signal(
    x: list[float],
    y: list[float],
    *,
    signal_name: str = "v(out)",
    unit: str = "V",
    x_unit: str = "s",
    tol_pct: float = 2.0,
    exact: bool = True,
    measurement_safe: bool = True,
) -> dict[str, Any]:
    """Compute comprehensive lossless transient analog metrics."""
    _require_lossless(exact, measurement_safe)
    if isinstance(tol_pct, bool) or not isinstance(tol_pct, (int, float)) or not 0.01 <= float(tol_pct) <= 50.0:
        raise DomainError("tol_pct must be a number between 0.01 and 50.0.", "invalid_measurement_input")
    tol = float(tol_pct)
    xs, ys = _validate_xy(x, y)
    n = len(xs)

    v_min = min(ys)
    v_max = max(ys)
    v_pp = v_max - v_min
    v_init = ys[0]
    v_final = ys[-1]

    # Time-weighted trapezoidal mean and RMS (accurate on non-uniform SPICE timesteps)
    total_dt = xs[-1] - xs[0]
    area = 0.0
    sq_area = 0.0
    for i in range(n - 1):
        dt = xs[i + 1] - xs[i]
        if dt <= 0.0:
            continue
        area += 0.5 * (ys[i] + ys[i + 1]) * dt
        sq_area += (ys[i] * ys[i] + ys[i] * ys[i + 1] + ys[i + 1] * ys[i + 1]) * (dt / 3.0)
    v_mean = area / total_dt
    v_rms = math.sqrt(max(0.0, sq_area / total_dt))

    # Step & transition metrics (only meaningful when signal has non-trivial swing)
    rise_time = None
    fall_time = None
    slew_rate_pos = None
    slew_rate_neg = None
    settling_time = None
    overshoot_pct = None
    undershoot_pct = None
    period_mean = None
    frequency_hz = None
    duty_cycle_pct = None
    period_jitter_rms = None

    if v_pp > 1e-12:
        low10 = v_min + 0.1 * v_pp
        high90 = v_min + 0.9 * v_pp
        rise_time = _compute_transition_time(xs, ys, low10, high90, rising=True)
        fall_time = _compute_transition_time(xs, ys, low10, high90, rising=False)
        dv_10_90 = high90 - low10
        if rise_time is not None and rise_time > 0.0:
            slew_rate_pos = dv_10_90 / rise_time
        if fall_time is not None and fall_time > 0.0:
            slew_rate_neg = dv_10_90 / fall_time

        # Settling time relative to start of window: last exit outside final_value +/- tol% * span
        # Use tail average (last 5% of samples, min 1) as steady reference if step-like
        tail_n = max(1, min(16, n // 20))
        steady_ref = sum(ys[-tail_n:]) / tail_n
        step_delta = steady_ref - v_init
        band = (tol / 100.0) * max(abs(step_delta), v_pp)
        last_outside_idx = -1
        for i in range(n):
            if abs(ys[i] - steady_ref) > band:
                last_outside_idx = i
        if last_outside_idx < n - 1:
            if last_outside_idx < 0:
                settling_time = 0.0
            else:
                settling_time = xs[last_outside_idx + 1] - xs[0]

        # Overshoot / undershoot relative to step direction (if net step >= 20% of v_pp)
        if abs(step_delta) >= 0.2 * v_pp:
            if step_delta > 0:
                overshoot_pct = max(0.0, (v_max - steady_ref) / step_delta * 100.0)
                undershoot_pct = max(0.0, (v_init - v_min) / step_delta * 100.0)
            else:
                mag = abs(step_delta)
                overshoot_pct = max(0.0, (steady_ref - v_min) / mag * 100.0)
                undershoot_pct = max(0.0, (v_max - v_init) / mag * 100.0)

        # Periodic / clock metrics via midpoint crossings
        mid = 0.5 * (v_min + v_max)
        rising, falling = _find_edge_crossings(xs, ys, mid)
        if len(rising) >= 2:
            periods = [rising[i + 1] - rising[i] for i in range(len(rising) - 1) if rising[i + 1] > rising[i]]
            if periods:
                period_mean = sum(periods) / len(periods)
                if period_mean > 0.0:
                    frequency_hz = 1.0 / period_mean
                if len(periods) >= 2:
                    var_p = sum((p - period_mean) ** 2 for p in periods) / len(periods)
                    period_jitter_rms = math.sqrt(max(0.0, var_p))
                else:
                    period_jitter_rms = 0.0
                # Duty cycle: average high pulse width / period across matched rising->falling pairs
                high_widths = []
                f_idx = 0
                for r_t in rising[:-1]:
                    while f_idx < len(falling) and falling[f_idx] <= r_t:
                        f_idx += 1
                    if f_idx < len(falling):
                        width = falling[f_idx] - r_t
                        if 0.0 < width < period_mean * 1.5:
                            high_widths.append(width)
                if high_widths and period_mean > 0.0:
                    duty_cycle_pct = (sum(high_widths) / len(high_widths)) / period_mean * 100.0

    metrics = {
        "min": v_min,
        "max": v_max,
        "peak_to_peak": v_pp,
        "mean": v_mean,
        "rms": v_rms,
        "initial_value": v_init,
        "final_value": v_final,
        "rise_time": rise_time,
        "fall_time": fall_time,
        "slew_rate_pos": slew_rate_pos,
        "slew_rate_neg": slew_rate_neg,
        "settling_time": settling_time,
        "settling_tol_pct": tol,
        "overshoot_pct": overshoot_pct,
        "undershoot_pct": undershoot_pct,
        "period_mean": period_mean,
        "frequency_hz": frequency_hz,
        "duty_cycle_pct": duty_cycle_pct,
        "period_jitter_rms": period_jitter_rms,
    }
    return {
        "schema": ANALOG_METRICS_SCHEMA,
        "analysis": "transient",
        "signal": signal_name,
        "unit": unit,
        "x_unit": x_unit,
        "exact": True,
        "measurement_safe": True,
        "sample_count": n,
        "window": {"x_min": xs[0], "x_max": xs[-1], "span": total_dt},
        "metrics": metrics,
    }


def _unwrap_phase_deg(phases: list[float]) -> list[float]:
    if not phases:
        return []
    out = [phases[0]]
    offset = 0.0
    for i in range(1, len(phases)):
        diff = phases[i] - phases[i - 1]
        if diff > 180.0:
            offset -= 360.0
        elif diff < -180.0:
            offset += 360.0
        out.append(phases[i] + offset)
    return out


def measure_ac_response(
    freqs_hz: list[float],
    complex_values: list[complex | dict[str, float]],
    *,
    signal_name: str = "v(out)",
    exact: bool = True,
    measurement_safe: bool = True,
) -> dict[str, Any]:
    """Compute lossless small-signal AC metrics: DC gain, -3dB BW, UGBW, PM, GM."""
    _require_lossless(exact, measurement_safe)
    if not isinstance(freqs_hz, list) or not isinstance(complex_values, list) or len(freqs_hz) != len(complex_values) or len(freqs_hz) < 2:
        raise DomainError("AC measurement requires aligned frequency and complex response vectors (>= 2 points).", "invalid_measurement_input")

    fs: list[float] = []
    mags_db: list[float] = []
    raw_phases: list[float] = []
    prev_f: float | None = None
    for f_raw, v_raw in zip(freqs_hz, complex_values):
        if isinstance(f_raw, bool) or not isinstance(f_raw, (int, float)) or not math.isfinite(float(f_raw)) or float(f_raw) <= 0.0:
            raise DomainError("AC frequencies must be positive finite numbers.", "invalid_measurement_input")
        f = float(f_raw)
        if prev_f is not None and f < prev_f:
            raise DomainError("AC frequencies must be monotonically non-decreasing.", "invalid_measurement_input")
        if isinstance(v_raw, complex):
            c = v_raw
        elif isinstance(v_raw, dict) and "real" in v_raw and "imag" in v_raw:
            c = complex(float(v_raw["real"]), float(v_raw["imag"]))
        else:
            raise DomainError("AC values must be complex numbers or {real, imag} dicts.", "invalid_measurement_input")
        mag = abs(c)
        if not math.isfinite(mag):
            raise DomainError("AC response contains non-finite magnitude.", "invalid_measurement_input")
        fs.append(f)
        mags_db.append(20.0 * math.log10(max(mag, 1e-30)))
        raw_phases.append(math.degrees(cmath.phase(c)))
        prev_f = f

    phases = _unwrap_phase_deg(raw_phases)
    dc_gain_db = mags_db[0]

    def _log_interp_freq(i: int, y0: float, y1: float, target: float) -> tuple[float, float]:
        frac = 0.5 if y1 == y0 else max(0.0, min(1.0, (target - y0) / (y1 - y0)))
        lf = math.log10(fs[i]) + frac * (math.log10(fs[i + 1]) - math.log10(fs[i]))
        return 10.0 ** lf, frac

    # -3 dB bandwidth
    bw_3db_hz = None
    target_3db = dc_gain_db - 3.0
    for i in range(len(fs) - 1):
        if mags_db[i] >= target_3db > mags_db[i + 1]:
            bw_3db_hz, _ = _log_interp_freq(i, mags_db[i], mags_db[i + 1], target_3db)
            break

    # Unity-gain bandwidth (0 dB crossing) & Phase Margin
    ugbw_hz = None
    phase_margin_deg = None
    for i in range(len(fs) - 1):
        if mags_db[i] >= 0.0 > mags_db[i + 1]:
            ugbw_hz, frac = _log_interp_freq(i, mags_db[i], mags_db[i + 1], 0.0)
            ph_at_ugbw = phases[i] + frac * (phases[i + 1] - phases[i])
            phase_margin_deg = 180.0 + ph_at_ugbw
            break

    # Gain Margin at -180 deg phase crossing
    gain_margin_db = None
    for i in range(len(fs) - 1):
        if phases[i] >= -180.0 > phases[i + 1]:
            _, frac = _log_interp_freq(i, phases[i], phases[i + 1], -180.0)
            mag_at_180 = mags_db[i] + frac * (mags_db[i + 1] - mags_db[i])
            gain_margin_db = -mag_at_180
            break

    return {
        "schema": ANALOG_METRICS_SCHEMA,
        "analysis": "ac",
        "signal": signal_name,
        "exact": True,
        "measurement_safe": True,
        "sample_count": len(fs),
        "window": {"f_min_hz": fs[0], "f_max_hz": fs[-1]},
        "metrics": {
            "dc_gain_db": dc_gain_db,
            "bw_3db_hz": bw_3db_hz,
            "ugbw_hz": ugbw_hz,
            "phase_margin_deg": phase_margin_deg,
            "gain_margin_db": gain_margin_db,
        },
    }


def measure_dc_operating_point(
    *,
    vdd: float,
    i_supply: float,
    v_out: float | None = None,
    v_ref: float | None = None,
    exact: bool = True,
    measurement_safe: bool = True,
) -> dict[str, Any]:
    """Compute lossless DC static power and optional output offset."""
    _require_lossless(exact, measurement_safe)
    for label, val in (("vdd", vdd), ("i_supply", i_supply)):
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(float(val)):
            raise DomainError(f"{label} must be a finite number.", "invalid_measurement_input")
    static_power_w = abs(float(vdd) * float(i_supply))
    dc_offset = None
    if v_out is not None and v_ref is not None:
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(float(x)) for x in (v_out, v_ref)):
            raise DomainError("v_out and v_ref must be finite numbers.", "invalid_measurement_input")
        dc_offset = float(v_out) - float(v_ref)
    return {
        "schema": ANALOG_METRICS_SCHEMA,
        "analysis": "dc",
        "exact": True,
        "measurement_safe": True,
        "metrics": {
            "vdd": float(vdd),
            "i_supply": float(i_supply),
            "static_power_w": static_power_w,
            "dc_offset": dc_offset,
        },
    }


def extract_named_metric(measurement_result: dict[str, Any], metric_name: str) -> float | None:
    """Resolve a declared spec metric name from a ``measure_transient_signal`` result."""
    metrics = measurement_result.get("metrics") if isinstance(measurement_result, dict) else None
    if not isinstance(metrics, dict):
        return None
    alias = {
        "avg": "mean",
        "end": "final_value",
        "value": "final_value",
    }.get(metric_name, metric_name)
    val = metrics.get(alias)
    if isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(float(val)):
        return float(val)
    return None


def measure_waveform_query(
    query_result: dict[str, Any],
    *,
    signal: str | None = None,
    tol_pct: float = 2.0,
) -> dict[str, Any]:
    """Run lossless analog measurement directly on a ``waveform_query`` payload."""
    if not isinstance(query_result, dict):
        raise DomainError("waveform_query payload must be an object.", "invalid_measurement_input")
    _require_lossless(
        bool(query_result.get("exact") is True),
        bool(query_result.get("measurement_safe") is True),
    )
    signals = query_result.get("signals") or {}
    if not isinstance(signals, dict) or not signals:
        raise DomainError("No signals present in waveform query result.", "invalid_measurement_input")
    target_signal = signal if (signal and signal in signals) else next(iter(signals))
    sig_obj = signals[target_signal]
    xs = query_result.get("x") or sig_obj.get("x") or []
    vals = sig_obj.get("values") or []
    sweep = query_result.get("sweep") or {}
    # Detect complex AC vector vs real transient vector
    if vals and isinstance(vals[0], (complex, dict)) and (
        isinstance(vals[0], complex) or ("real" in vals[0] and "imag" in vals[0])
    ):
        res = measure_ac_response(xs, vals, signal_name=target_signal, exact=True, measurement_safe=True)
    else:
        res = measure_transient_signal(
            xs,
            vals,
            signal_name=target_signal,
            unit=str(sig_obj.get("unit") or ""),
            x_unit=str(sweep.get("unit") or "s"),
            tol_pct=tol_pct,
            exact=True,
            measurement_safe=True,
        )
    res["run_id"] = query_result.get("run_id")
    res["dataset"] = query_result.get("dataset")
    return res

#!/usr/bin/env python3
"""Inspect one frozen educational inverter run; no simulator or SSH execution.

Only scalar FLOAT DOUBLE PSF ASCII sweeps with one time axis are supported.
Unexpected syntax, missing samples and ambiguous traces are rejected rather
than repaired. CSV/JSON need only Python's standard library. --plots requires
matplotlib separately; it is not a dependency of the SpiceOwl application.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import sys


from spiceowl.integrations.spectre_results import MAX_PSF_BYTES, PsfError, parse_psf_ascii


def _crossings(times, values, threshold, direction):
    crossings = []
    for t0, t1, y0, y1 in zip(times, times[1:], values, values[1:]):
        crossed = y0 < threshold <= y1 if direction == "rising" else y0 > threshold >= y1
        if crossed:
            crossings.append(t0 + (threshold - y0) * (t1 - t0) / (y1 - y0))
    return crossings


def measure_delays(values, contract):
    """Pair each declared input edge with one opposite subsequent output edge."""
    times, threshold = values["time"], contract["threshold_v"]
    result = {"report_only": True, "interpolation": "linear", "pairs": [], "warnings": []}
    for direction, opposite, key in (("rising", "falling", "input_rising_windows_s"), ("falling", "rising", "input_falling_windows_s")):
        inputs = _crossings(times, values["vin"], threshold, direction)
        outputs = _crossings(times, values["vout"], threshold, opposite)
        used = set()
        for start, end in contract[key]:
            candidates = [t for t in inputs if start <= t <= end]
            pair = {"input_direction": direction, "output_direction": opposite, "input_window_s": [start, end],
                    "status": "unavailable", "input_crossing_s": None, "output_crossing_s": None, "delay_s": None}
            if len(candidates) != 1:
                pair["reason"] = "Expected exactly one input crossing in the frozen edge window."
            else:
                crossing = candidates[0]
                pair["input_crossing_s"] = crossing
                matches = [(i, t) for i, t in enumerate(outputs) if i not in used and crossing <= t <= crossing + contract["max_pair_delay_s"]]
                if len(matches) != 1:
                    pair["reason"] = "Expected one unambiguous opposite output crossing within the pairing interval."
                else:
                    i, output = matches[0]
                    used.add(i)
                    pair.update(status="measured", output_crossing_s=output, delay_s=output - crossing)
            if pair["status"] == "unavailable":
                result["warnings"].append({"input_direction": direction, "input_window_s": [start, end], "message": pair["reason"]})
            result["pairs"].append(pair)
    return result


def validate_waveform(waveform, acceptance, *, execution_result, source_hashes):
    """Keep execution evidence, measurement validity and toy behavior separate.

    execution_result and source_hashes are caller-supplied evidence; this helper
    does not authenticate a remote receipt or independently attest a solver.
    """
    if acceptance.get("schema") != "spiceowl.inverter.acceptance.v1" or acceptance.get("status") != "frozen_before_execution":
        raise ValueError("Expected a frozen inverter acceptance v1 contract.")
    if execution_result not in {"completed", "failed", "unknown"}:
        raise ValueError("Execution result must be completed, failed or unknown.")
    values, units = waveform["values"], waveform["units"]
    requirements = acceptance["data_requirements"]
    checks = []

    def check(name, passed, detail):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    check("frozen_source_identity", source_hashes == acceptance["input_sha256"], {"expected": acceptance["input_sha256"], "observed": source_hashes})
    check("required_signals", set(requirements["required_columns"]) <= values.keys(), {"required": requirements["required_columns"], "present": list(values)})
    check("declared_units", all(units.get(name) == acceptance["signal_units"][name] for name in values if name in acceptance["signal_units"]), {"expected": acceptance["signal_units"], "observed": units})
    times = values.get("time", [])
    count = len(times)
    aligned = count > 0 and all(len(series) == count for series in values.values())
    finite = aligned and all(type(value) in {int, float} and math.isfinite(value) for series in values.values() for value in series)
    check("aligned_finite_samples", finite, {"sample_count": count})
    check("sample_count", requirements["min_samples"] <= count <= requirements["max_samples"], {"nominal": requirements["expected_samples"], "allowed": [requirements["min_samples"], requirements["max_samples"]], "observed": count})
    if finite:
        gaps = [right - left for left, right in zip(times, times[1:])]
        check("increasing_time", all(gap > 0 for gap in gaps), {"samples": count})
        check("maximum_sample_gap", bool(gaps) and max(gaps) <= requirements["max_sample_gap_s"], {"limit_s": requirements["max_sample_gap_s"], "observed_s": max(gaps) if gaps else None})
        tolerance = requirements["endpoint_tolerance_s"]
        check("time_coverage", abs(times[0] - requirements["start_time_s"]) <= tolerance and abs(times[-1] - requirements["stop_time_s"]) <= tolerance,
              {"expected_s": [requirements["start_time_s"], requirements["stop_time_s"]], "observed_s": [times[0], times[-1]], "tolerance_s": tolerance})
    windows = []
    if finite and {"time", "vin", "vout"} <= values.keys():
        logic = acceptance["settled_logic"]
        for window in logic["windows"]:
            indices = [index for index, time in enumerate(times) if window["start_s"] <= time <= window["end_s"]]
            enough = len(indices) >= logic["minimum_samples_per_window"]
            row = {"start_s": window["start_s"], "end_s": window["end_s"], "sample_count": len(indices), "coverage_valid": enough, "signals": {}}
            for signal in ("vin", "vout"):
                lower, upper = logic[window[signal] + "_min_v"], logic[window[signal] + "_max_v"]
                samples = [values[signal][index] for index in indices]
                passed = enough and all(lower <= voltage <= upper for voltage in samples)
                row["signals"][signal] = {"expected_level": window[signal], "allowed_v": [lower, upper], "minimum_v": min(samples) if samples else None,
                                           "maximum_v": max(samples) if samples else None, "passed": passed}
            windows.append(row)
        check("settled_window_coverage", all(row["coverage_valid"] for row in windows), {"minimum_samples_per_window": logic["minimum_samples_per_window"]})
        check("frozen_input_stimulus", all(row["signals"]["vin"]["passed"] for row in windows), {"evaluated_windows": len(windows)})
    valid_data = bool(windows) and all(item["passed"] for item in checks)
    validity = "valid" if valid_data and execution_result == "completed" else "indeterminate" if valid_data and execution_result == "unknown" else "invalid"
    behavior = ("pass" if all(row["signals"]["vout"]["passed"] for row in windows) else "fail") if validity == "valid" else "not_evaluated"
    delays = measure_delays(values, acceptance["propagation_delay"]) if validity == "valid" else {"report_only": True, "status": "not_evaluated", "pairs": [], "warnings": [{"message": "Measurement preconditions were not all satisfied."}]}
    return {"schema": "spiceowl.inverter.result.v1", "execution_result": execution_result, "execution_evidence_source": "caller_supplied",
            "measurement_validity": validity, "toy_inverter_behavior": behavior, "technology_performance_claim": False,
            "data_checks": checks, "settled_windows": windows, "propagation_delays": delays}


def _require_export_units(waveform):
    expected = {"time": "s", "vin": "V", "vout": "V", "vdd": "V"}
    mismatches = [name for name, unit in expected.items() if name in waveform["values"] and waveform["units"].get(name) != unit]
    if mismatches:
        raise PsfError("Canonical CSV/plot export requires seconds and volts; declared units are missing or incompatible for " + ", ".join(mismatches) + ". Raw PSF values were not converted or relabeled.")


def export_csv(waveform, path):
    _require_export_units(waveform)
    columns = [name for name in ("time", "vin", "vout", "vdd") if name in waveform["values"]]
    with Path(path).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["time_s" if name == "time" else name + "_v" for name in columns])
        writer.writerows(zip(*(waveform["values"][name] for name in columns)))


def export_plots(waveform, output_directory, *, origin):
    if origin not in {"real", "synthetic"}:
        raise ValueError("An explicit real or synthetic data origin is required.")
    _require_export_units(waveform)
    try:
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib import pyplot as plt
    except ImportError as exc:
        raise RuntimeError("Plotting requires separately installed matplotlib; CSV and JSON remain available.") from exc
    values = waveform["values"]
    if not {"time", "vin", "vout"} <= values.keys():
        raise PsfError("Cannot plot missing inverter input/output signals.")
    fig, ax = plt.subplots(figsize=(10, 4.4), layout="constrained")
    times = [t * 1e9 for t in values["time"]]
    ax.plot(times, values["vin"], label="Input · vin", color="#4677a0", linewidth=1.4)
    ax.plot(times, values["vout"], label="Output · vout", color="#ba6c2f", linewidth=1.6)
    if "vdd" in values:
        ax.plot(times, values["vdd"], label="Supply · vdd", color="#708574", linestyle="--", linewidth=0.9)
    ax.set(xlabel="Time (ns)", ylabel="Voltage (V)", title="Educational CMOS inverter — transient waveform")
    ax.grid(alpha=0.2)
    ax.legend(loc="upper right", ncol=3, fontsize=9)
    provenance = "real Spectre simulation; educational MOS1 models; not foundry PDK" if origin == "real" else "synthetic fixture; educational MOS1 acceptance test; not a simulator result"
    fig.suptitle(provenance, fontsize=9, color="#53605b")
    output = Path(output_directory)
    for suffix in ("png", "svg"):
        fig.savefig(output / f"waveforms.{suffix}", dpi=180)
    plt.close(fig)


def _read_json(path):
    def unique(pairs):
        data = {}
        for key, value in pairs:
            if key in data:
                raise ValueError("Duplicate acceptance JSON key.")
            data[key] = value
        return data
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite acceptance JSON value.")))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--psf", required=True, type=Path)
    parser.add_argument("--acceptance", required=True, type=Path)
    parser.add_argument("--netlist", required=True, type=Path, help="Collected tb.scs; its two sibling source files are also checked.")
    parser.add_argument("--execution-result", required=True, choices=("completed", "failed", "unknown"))
    parser.add_argument("--origin", required=True, choices=("real", "synthetic"))
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--plots", action="store_true")
    args = parser.parse_args(argv)
    acceptance = _read_json(args.acceptance)
    expected_files = acceptance.get("input_sha256", {})
    if set(expected_files) != {"tb.scs", "models.scs", "inverter.scs"} or args.netlist.name != "tb.scs":
        raise ValueError("This helper accepts only the frozen three-file inverter source bundle.")
    source_hashes = {name: hashlib.sha256((args.netlist.parent / name).read_bytes()).hexdigest() for name in expected_files}
    with args.psf.open("rb") as stream:
        raw = stream.read(MAX_PSF_BYTES + 1)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    waveform = None
    try:
        if len(raw) > MAX_PSF_BYTES:
            raise PsfError("PSF ASCII exceeds the supported size limit.")
        waveform = parse_psf_ascii(raw.decode("utf-8"))
        result = validate_waveform(waveform, acceptance, execution_result=args.execution_result, source_hashes=source_hashes)
    except (PsfError, UnicodeError) as exc:
        result = {"schema": "spiceowl.inverter.result.v1", "execution_result": args.execution_result, "execution_evidence_source": "caller_supplied",
                  "measurement_validity": "invalid", "toy_inverter_behavior": "not_evaluated", "technology_performance_claim": False,
                  "parse_error": str(exc), "data_checks": [], "settled_windows": [], "propagation_delays": {"report_only": True, "status": "not_evaluated", "pairs": []}}
    result["provenance"] = {"origin": args.origin, "simulator": "Spectre" if args.origin == "real" else None,
                            "model_scope": "educational MOS1 models; not foundry PDK", "source_hashes": source_hashes,
                            "acceptance_sha256": hashlib.sha256(args.acceptance.read_bytes()).hexdigest(),
                            "psf_sha256": hashlib.sha256(raw).hexdigest(), "psf_format": waveform["format"] if waveform else None}
    result["artifacts"] = []
    if waveform is not None:
        try:
            export_csv(waveform, args.output_dir / "waveforms.csv")
            result["artifacts"].append("waveforms.csv")
            if args.plots:
                try:
                    export_plots(waveform, args.output_dir, origin=args.origin)
                    result["artifacts"].extend(("waveforms.png", "waveforms.svg"))
                except (RuntimeError, PsfError) as exc:
                    result["plot_error"] = str(exc)
        except PsfError as exc:
            result["export_error"] = str(exc)
    (args.output_dir / "validation.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("execution_result", "measurement_validity", "toy_inverter_behavior", "artifacts")}, indent=2))
    return 0 if result["measurement_validity"] == "valid" else 2


if __name__ == "__main__":
    sys.exit(main())

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
import re
import sys


MAX_PSF_BYTES = 16 * 1024 * 1024
MAX_TRACES = 32
MAX_SAMPLES = 1_000_002
_TOKEN = re.compile(r'\s*(?:("(?:[^"\\]|\\.)*")|([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)|([A-Za-z_][A-Za-z_0-9]*)|([()]))')


class PsfError(ValueError):
    """The source is outside the supported, unambiguous PSF ASCII subset."""


def _tokens(text):
    result, offset = [], 0
    while offset < len(text):
        match = _TOKEN.match(text, offset)
        if not match:
            if not text[offset:].strip():
                break
            raise PsfError("Unsupported token in PSF ASCII.")
        quoted, number, word, bracket = match.groups()
        if quoted is not None:
            try:
                result.append(("string", json.loads(quoted)))
            except ValueError as exc:
                raise PsfError("Invalid quoted PSF text.") from exc
        elif number is not None:
            value = float(number)
            if not math.isfinite(value):
                raise PsfError("Nonfinite PSF numeric value.")
            result.append(("number", value))
        elif word is not None:
            result.append(("word", word))
        else:
            result.append(("bracket", bracket))
        offset = match.end()
    return result


def _properties(tokens):
    if not tokens:
        return {}
    if tokens[:2] != [("word", "PROP"), ("bracket", "(")] or tokens[-1:] != [("bracket", ")")]:
        raise PsfError("Unsupported PSF declaration suffix.")
    props, body = {}, tokens[2:-1]
    if len(body) % 2:
        raise PsfError("Incomplete PSF properties.")
    for index in range(0, len(body), 2):
        key, value = body[index:index + 2]
        if key[0] != "string" or value[0] not in {"string", "number"} or key[1] in props:
            raise PsfError("Ambiguous PSF properties.")
        props[key[1]] = value[1]
    return props


def _type_definitions(tokens):
    types, index = {}, 0
    while index < len(tokens):
        name = tokens[index]
        if name[0] != "string" or name[1] in types:
            raise PsfError("Invalid or duplicate PSF type.")
        index += 1
        start, depth = index, 0
        while index < len(tokens):
            token = tokens[index]
            if depth == 0 and token[0] == "string":
                break
            if token == ("bracket", "("):
                depth += 1
            elif token == ("bracket", ")"):
                depth -= 1
                if depth < 0:
                    raise PsfError("Unbalanced PSF type.")
            index += 1
        if depth or index == start:
            raise PsfError("Incomplete PSF type.")
        body = tokens[start:index]
        supported = body[:2] == [("word", "FLOAT"), ("word", "DOUBLE")]
        props = _properties(body[2:]) if supported else {}
        types[name[1]] = {"supported": supported, "properties": props}
    return types


def _references(tokens, types):
    refs, index = {}, 0
    while index < len(tokens):
        if index + 1 >= len(tokens):
            raise PsfError("Incomplete PSF trace declaration.")
        name, typename = tokens[index:index + 2]
        if name[0] != "string" or typename[0] != "string" or name[1] in refs:
            raise PsfError("Invalid or duplicate PSF trace declaration.")
        definition = types.get(typename[1])
        if not definition or not definition["supported"]:
            raise PsfError("Only scalar FLOAT DOUBLE PSF traces are supported.")
        index += 2
        start, depth = index, 0
        while index < len(tokens):
            token = tokens[index]
            if depth == 0 and token[0] == "string":
                break
            if token == ("bracket", "("):
                depth += 1
            elif token == ("bracket", ")"):
                depth -= 1
                if depth < 0:
                    raise PsfError("Unbalanced PSF trace properties.")
            index += 1
        if depth:
            raise PsfError("Incomplete PSF trace properties.")
        props = definition["properties"] | _properties(tokens[start:index])
        refs[name[1]] = {"type": typename[1], "unit": props.get("units")}
    return refs


def parse_psf_ascii(text):
    """Parse bounded PSF ASCII into exact source signal names and real samples."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_PSF_BYTES:
        raise PsfError("PSF ASCII exceeds the supported size limit.")
    if "\x00" in text:
        raise PsfError("Binary PSF is not supported.")
    sections, current = {}, None
    order = ["HEADER", "TYPE", "SWEEP", "TRACE", "VALUE", "END"]
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in order:
            if len(sections) >= len(order) or stripped != order[len(sections)]:
                raise PsfError("Missing, duplicate or out-of-order PSF section.")
            current = stripped
            sections[current] = []
        elif stripped:
            if current is None or current == "END":
                raise PsfError("Unexpected text outside PSF sections.")
            sections[current].append(line)
    if list(sections) != order:
        raise PsfError("Incomplete PSF file; required END marker or section is absent.")
    header_tokens = _tokens("\n".join(sections["HEADER"]))
    header = {}
    if len(header_tokens) % 2:
        raise PsfError("Incomplete PSF header.")
    for index in range(0, len(header_tokens), 2):
        key, value = header_tokens[index:index + 2]
        if key[0] != "string" or value[0] not in {"string", "number"} or key[1] in header:
            raise PsfError("Ambiguous PSF header.")
        header[key[1]] = value[1]
    types = _type_definitions(_tokens("\n".join(sections["TYPE"])))
    sweep = _references(_tokens("\n".join(sections["SWEEP"])), types)
    traces = _references(_tokens("\n".join(sections["TRACE"])), types)
    if list(sweep) != ["time"] or "time" in traces or not 1 <= len(traces) <= MAX_TRACES:
        raise PsfError("Expected exactly one time sweep and distinct scalar traces.")
    tokens = _tokens("\n".join(sections["VALUE"]))
    if len(tokens) % 2:
        raise PsfError("Incomplete PSF value pair.")
    names = ["time", *traces]
    values, sample = {name: [] for name in names}, {}

    def finish_sample():
        if set(sample) != set(names):
            raise PsfError("PSF sample has missing or unexpected signals.")
        if values["time"] and sample["time"] <= values["time"][-1]:
            raise PsfError("PSF time must increase strictly; no duplicate or reordered samples.")
        if len(values["time"]) >= MAX_SAMPLES:
            raise PsfError("Too many PSF samples.")
        for name in names:
            values[name].append(sample[name])

    for index in range(0, len(tokens), 2):
        name, value = tokens[index:index + 2]
        if name[0] != "string" or value[0] != "number" or name[1] not in values:
            raise PsfError("Unsupported or unknown PSF value entry.")
        if name[1] == "time":
            if sample:
                finish_sample()
            sample = {"time": value[1]}
        else:
            if "time" not in sample or name[1] in sample:
                raise PsfError("Ambiguous or duplicated signal in a PSF sample.")
            sample[name[1]] = value[1]
    if not sample:
        raise PsfError("PSF file contains no samples.")
    finish_sample()
    count = header.get("PSF sweep points")
    if count is not None and count != len(values["time"]):
        raise PsfError("PSF header sample count does not match the data.")
    return {"values": values, "units": {name: definition["unit"] for name, definition in (sweep | traces).items()},
            "sample_count": len(values["time"]), "format": "psf_ascii_scalar_time_v1"}


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

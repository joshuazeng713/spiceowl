"""Standalone scalar PSF ASCII parsing and bounded structural summaries.

The grammar and declaration helpers originate in this repository's original
scripts/inverter_smoke_results.py, which now imports this single shared parser.
This installable module uses no toy inverter thresholds, file IO or simulator.
Raw values are returned only by parse_psf_ascii for caller-owned local exports;
summarize_psf_ascii never returns sample arrays or a scientific pass verdict.
"""

import io
import json
import math
import re

from spiceowl.domain.common import DomainError


MAX_PSF_BYTES = 16 * 1024 * 1024
MAX_SIGNALS = 64
MAX_SAMPLES = 200_000
MAX_METADATA_BYTES = 256 * 1024
MAX_SIGNAL_LENGTH = 160
_TOKEN = re.compile(r'\s*(?:("(?:[^"\\]|\\.)*")|([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)|([A-Za-z_][A-Za-z_0-9]*)|([()]))')


class PsfError(DomainError):
    """A safe error for unsupported, damaged or oversized supplied PSF text."""
    def __init__(self, message="Unsupported or invalid PSF ASCII.", code="psf_parse"):
        super().__init__(message, code)


def _iter_tokens(text):
    offset = 0
    while offset < len(text):
        match = _TOKEN.match(text, offset)
        if not match:
            if not text[offset:].strip():
                break
            raise PsfError("Unsupported token in PSF ASCII.")
        quoted, number, word, bracket = match.groups()
        if quoted is not None:
            try:
                yield "string", json.loads(quoted)
            except ValueError:
                raise PsfError("Invalid quoted PSF text.") from None
        elif number is not None:
            value = float(number)
            if not math.isfinite(value):
                raise PsfError("Nonfinite PSF numeric value.", "psf_nonfinite")
            yield "number", value
        elif word is not None:
            if word.lower() in {"nan", "inf", "infinity"}:
                raise PsfError("Nonfinite PSF numeric value.", "psf_nonfinite")
            yield "word", word
        else:
            yield "bracket", bracket
        offset = match.end()


def _tokens(text):
    return list(_iter_tokens(text))


def _properties(tokens):
    if not tokens:
        return {}
    if tokens[:2] != [("word", "PROP"), ("bracket", "(")] or tokens[-1:] != [("bracket", ")")]:
        raise PsfError("Unsupported PSF declaration suffix.")
    props, body = {}, tokens[2:-1]
    if len(body) % 2:
        raise PsfError("Incomplete PSF properties.")
    for index in range(0, len(body), 2):
        key, value = body[index:index+2]
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
        types[name[1]] = {"supported": supported, "properties": _properties(body[2:]) if supported else {}}
    return types


def _references(tokens, types):
    refs, index = {}, 0
    while index < len(tokens):
        if index + 1 >= len(tokens):
            raise PsfError("Incomplete PSF trace declaration.")
        name, typename = tokens[index:index+2]
        if name[0] != "string" or typename[0] != "string" or name[1] in refs:
            raise PsfError("Invalid or duplicate PSF trace declaration.")
        if not 1 <= len(name[1]) <= MAX_SIGNAL_LENGTH or not name[1].isprintable():
            raise PsfError("PSF signal name exceeds supported bounds.", "psf_limit")
        definition = types.get(typename[1])
        if not definition or not definition["supported"]:
            raise PsfError("Only scalar FLOAT DOUBLE PSF traces are supported.", "psf_unsupported")
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
        unit = props.get("units")
        if unit is not None and (type(unit) is not str or len(unit) > 32 or not unit.isprintable()):
            raise PsfError("PSF unit metadata is invalid.")
        refs[name[1]] = {"type": typename[1], "unit": unit}
    return refs


def parse_psf_ascii(text):
    """Return exact values/units for <=64 signals and <=200,000 time samples."""
    if type(text) is not str or len(text) > MAX_PSF_BYTES:
        raise PsfError("PSF ASCII exceeds the supported size limit.", "psf_limit")
    try:
        if len(text.encode("utf-8")) > MAX_PSF_BYTES:
            raise PsfError("PSF ASCII exceeds the supported size limit.", "psf_limit")
    except UnicodeError:
        raise PsfError("Invalid PSF Unicode text.") from None
    if not text.strip():
        raise PsfError("No PSF content was supplied.", "psf_missing")
    if "\x00" in text:
        raise PsfError("Binary PSF is not supported.", "psf_unsupported")
    sections, current, metadata_size = {}, None, 0
    order = ["HEADER", "TYPE", "SWEEP", "TRACE", "VALUE", "END"]
    for line in io.StringIO(text):
        stripped = line.strip()
        if stripped in order:
            if len(sections) >= len(order) or stripped != order[len(sections)]:
                raise PsfError("Missing, duplicate or out-of-order PSF section.")
            current = stripped; sections[current] = io.StringIO()
        elif stripped:
            if current is None or current == "END":
                raise PsfError("Unexpected text outside PSF sections.")
            if current != "VALUE":
                metadata_size += len(line.encode("utf-8"))
                if metadata_size > MAX_METADATA_BYTES:
                    raise PsfError("PSF metadata exceeds supported bounds.", "psf_limit")
            sections[current].write(line if line.endswith("\n") else line+"\n")
    if list(sections) != order:
        raise PsfError("Incomplete PSF file; required END marker or section is absent.")
    header_tokens = _tokens(sections["HEADER"].getvalue()); header = {}
    if len(header_tokens) % 2:
        raise PsfError("Incomplete PSF header.")
    for index in range(0, len(header_tokens), 2):
        key, value = header_tokens[index:index+2]
        if key[0] != "string" or value[0] not in {"string", "number"} or key[1] in header:
            raise PsfError("Ambiguous PSF header.")
        header[key[1]] = value[1]
    types = _type_definitions(_tokens(sections["TYPE"].getvalue()))
    sweep = _references(_tokens(sections["SWEEP"].getvalue()), types)
    traces = _references(_tokens(sections["TRACE"].getvalue()), types)
    if list(sweep) != ["time"] or "time" in traces or not 1 <= len(traces) <= MAX_SIGNALS:
        raise PsfError("Expected one time sweep and 1..64 distinct scalar traces.", "psf_unsupported")
    declared_count = header.get("PSF sweep points")
    if declared_count is not None and (type(declared_count) is not float or not declared_count.is_integer()
                                      or not 1 <= declared_count <= MAX_SAMPLES):
        raise PsfError("PSF header sample count exceeds supported bounds.", "psf_limit")
    names = ["time", *traces]; values = {name: [] for name in names}; sample = {}

    def finish_sample():
        if set(sample) != set(names):
            raise PsfError("PSF sample has missing or unexpected signals.", "psf_unaligned")
        if values["time"] and sample["time"] <= values["time"][-1]:
            raise PsfError("PSF time must increase strictly.", "psf_time_nonmonotonic")
        if len(values["time"]) >= MAX_SAMPLES:
            raise PsfError("Too many PSF samples.", "psf_limit")
        for name in names:
            values[name].append(sample[name])

    tokens = iter(_iter_tokens(sections["VALUE"].getvalue()))
    for name in tokens:
        value = next(tokens, None)
        if value is None:
            raise PsfError("Incomplete PSF value pair.", "psf_unaligned")
        if name[0] != "string" or value[0] != "number" or name[1] not in values:
            raise PsfError("Unsupported or unknown PSF value entry.")
        if name[1] == "time":
            if sample:
                finish_sample()
            sample = {"time": value[1]}
        else:
            if "time" not in sample or name[1] in sample:
                raise PsfError("Ambiguous or duplicated signal in a PSF sample.", "psf_unaligned")
            sample[name[1]] = value[1]
    if not sample:
        raise PsfError("PSF file contains no samples.", "psf_empty")
    finish_sample()
    if declared_count is not None and declared_count != len(values["time"]):
        raise PsfError("PSF header sample count does not match the data.", "psf_unaligned")
    return {"values": values, "units": {name: definition["unit"] for name, definition in (sweep | traces).items()},
            "sample_count": len(values["time"]), "format": "psf_ascii_scalar_time_v1"}


def summarize_psf_ascii(text, required_signals=None):
    """Summarize structural checks only; coverage/specification stay unknown.

    Required signal names are exact native identifiers, never expressions or
    paths to open. No requested names means no extra required-signal condition.
    A parse failure yields a safe invalid summary; absent text yields unknown.
    """
    required = [] if required_signals is None else required_signals
    if (type(required) is not list or len(required) > MAX_SIGNALS
            or any(type(name) is not str or not 1 <= len(name) <= MAX_SIGNAL_LENGTH or not name.isprintable() for name in required)
            or len(set(required)) != len(required)):
        raise PsfError("Required signals must be a bounded list of distinct names.", "psf_required_signals")
    checks = {name: "unknown" for name in ("parse", "finite", "aligned", "time_monotonic", "required_signals", "coverage")}
    result = {"schema": "spiceowl.spectre-result-summary.v1", "data_validity": "unknown", "specification": "unknown",
              "sample_count": 0, "signal_names": [], "time_start_s": None, "time_end_s": None, "error_code": None}
    try:
        waveform = parse_psf_ascii(text)
    except PsfError as error:
        result["error_code"] = error.code
        if error.code != "psf_missing":
            result["data_validity"] = "invalid"; checks["parse"] = "fail"
            failed = {"psf_nonfinite": "finite", "psf_unaligned": "aligned", "psf_time_nonmonotonic": "time_monotonic"}.get(error.code)
            if failed:
                checks[failed] = "fail"
    else:
        values = waveform["values"]
        present = all(name in values for name in required)
        result.update(data_validity="valid" if present else "invalid", sample_count=waveform["sample_count"],
                      signal_names=[name for name in values if name != "time"],
                      error_code=None if present else "psf_required_signals_missing")
        if waveform["units"]["time"] == "s":
            result.update(time_start_s=values["time"][0], time_end_s=values["time"][-1])
        checks.update(parse="pass", finite="pass", aligned="pass", time_monotonic="pass", required_signals="pass" if present else "fail")
    result["checks"] = [{"name": name, "status": status} for name, status in checks.items()]
    return result


def parse_psf_analysis(text, *, analysis_kind, analysis_name=None):
    """Parse a selected AC/DC/dcOpInfo artifact without changing time-only API."""
    from .spectre_datasets import parse_psf_analysis as parse
    return parse(text, analysis_kind=analysis_kind, analysis_name=analysis_name)

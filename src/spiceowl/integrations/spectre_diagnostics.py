"""Bounded, advisory Spectre diagnostics with no IO or execution authority.

Log text is caller-supplied evidence, not instructions. This module never opens
paths, calls a model, changes simulator settings, or executes suggested actions.
Acceptance is reported separately from execution and from log classification.
"""

import re

from spiceowl.agent.diagnosis import MAX_EVIDENCE_BYTES
from spiceowl.domain.common import DomainError
from spiceowl.validation.acceptance import aggregate_acceptance


SCHEMA = "spiceowl.spectre-diagnostics.v1"
MAX_LOG_BYTES = MAX_EVIDENCE_BYTES
MAX_LOG_LINES = 2048
MAX_LINE_LENGTH = 4096
MAX_EXCERPT_LENGTH = 320
MAX_EVIDENCE_LINES = 8
_STATES = {"queued", "running", "completed", "failed", "cancelled", "unknown"}
_ACTIONS = {
    "license": ("Check the configured license service and available entitlement for this run.",
                "Preserve the existing run identity while resolving license availability."),
    "model_missing": ("Check the frozen include closure and the selected remote model mapping.",
                      "Confirm that the referenced model or subcircuit is defined for the selected corner."),
    "syntax": ("Inspect the cited netlist location and its declared simulator language.",
               "Prepare a reviewed source correction before creating another run."),
    "parameter_bounds": ("Check parameter units and the documented model operating range.",
                         "Review the original bounds before proposing any parameter change."),
    "convergence": ("Inspect the operating point, floating nodes, initial conditions and model compatibility.",
                    "Review numerical-setting changes separately from scientific acceptance criteria."),
    "oom": ("Inspect this run's memory limit and its frozen input size.",
            "Review a larger resource allocation or smaller experiment before another run."),
    "suspected_oom": ("Inspect run-bound kernel or cgroup OOM evidence before assigning a cause.",
                      "Check whether cancellation, a scheduler limit or another actor sent SIGKILL."),
    "unknown": ("Inspect the saved run receipt and additional bounded diagnostics before assigning a cause.",),
}
_PATTERNS = {
    "license": re.compile(
        r"\blicen[cs]e\s+(?:checkout\s+)?(?:failed|failure|denied|unavailable|expired)\b"
        r"|\blicen[cs]e\b.{0,160}\b(?:not available|checkout failed|checkout failure|denied)\b"
        r"|\b(?:cannot|could not|unable to|failed to)\s+(?:obtain|acquire|check\s*out)\s+(?:a |the )?licen[cs]e\b"
        r"|\bwaiting\s+for\s+(?:a |the )?licen[cs]e\b", re.I),
    "model_missing": re.compile(
        r"\bSFE-23\b|\bundefined\s+(?:model|subcircuit)\b"
        r"|\b(?:model|subcircuit)\b.{0,120}\b(?:not found|not defined|undefined)\b"
        r"|\b(?:include|model)\s+file\b.{0,200}\b(?:not found|cannot be opened|no such file)\b"
        r"|\b(?:cannot|could not|unable to)\s+open\b.{0,200}\b(?:include|model)\s+file\b", re.I),
    "syntax": re.compile(r"\bSFE-874\b|\bsyntax error\b|\bunexpected (?:end of line|end of file|token|equals)\b", re.I),
    "parameter_bounds": re.compile(
        r"\bparameter\b.{0,160}\b(?:out of range|outside (?:the )?(?:allowed |valid )?range|range violation|out of bounds)\b"
        r"|\bparameter\b.{0,160}\b(?:must|should) be (?:positive|nonnegative|non-negative|greater|less)\b"
        r"|\bparameter\b.{0,160}\b(?:below (?:the )?minimum|above (?:the )?maximum)\b", re.I),
    "convergence": re.compile(
        r"\bSPECTRE-(?:16080|11005)\b|\bno (?:DC )?solution found\b|\bno convergence\b"
        r"|\b(?:did not|failed to) converge\b|\bconvergence (?:failed|failure)\b"
        r"|\bmatrix is singular\b|\btime\s*step (?:too small|below minimum)\b", re.I),
    "oom": re.compile(r"\bout[- ]of[- ]memory\b|\bmemory allocation (?:failed|failure)\b|\bcannot allocate memory\b", re.I),
}
_MESSAGE = re.compile(r"^\s*(?:(?:spectre|simerror)\s*:\s*)?(error|fatal(?: error)?|warning|notice)\b", re.I)
_EXPLICIT_OOM = re.compile(r"^\s*(?:spectre:\s*)?(?:out[- ]of[- ]memory|memory allocation failed|cannot allocate memory)[.!]?\s*$", re.I)
_NEGATED_OOM = re.compile(
    r"\b(?:no|not|without)\s+(?:an?\s+)?(?:out[- ]of[- ]memory|memory allocation (?:failed|failure))\b"
    r"|\b(?:if|unless)\b.{0,80}\b(?:out[- ]of[- ]memory|memory allocation (?:failed|failure))\b", re.I)
_SENSITIVE = re.compile(r"[/\\@]|licen[cs]|\b(?:password|secret|token|credential|authorization)\b|CDS_LIC|LM_LIC", re.I)
_VENDOR_CODE = re.compile(r"\b(?:SFE-(?:23|874)|SPECTRE-(?:16080|11005))\b", re.I)
_SAFE_WORDS = frozenset("""error fatal warning notice spectre syntax unexpected end of line file token equals
    undefined model subcircuit instance is referencing not found defined no dc solution convergence failed failure
    did converge matrix singular time step too small below minimum above maximum parameter outside the allowed
    valid range violation out bounds must should be positive nonnegative greater less memory allocation cannot
    allocate detected at during circuit read in flattening""".split())


def _invalid(message="Invalid or oversized Spectre diagnostic evidence."):
    raise DomainError(message, "invalid_spectre_diagnostics")


def _log_lines(value):
    if type(value) is not str or len(value) > MAX_LOG_BYTES:
        _invalid()
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        _invalid()
    if size > MAX_LOG_BYTES or any(not c.isprintable() and c not in "\r\n\t" for c in value):
        _invalid()
    lines = value.splitlines()
    if len(lines) > MAX_LOG_LINES or any(len(line) > MAX_LINE_LENGTH for line in lines):
        _invalid()
    return lines, size


def _excerpt(line):
    """Allowlist diagnostic vocabulary; values and arbitrary prose stay private."""
    if _SENSITIVE.search(line):
        code = _VENDOR_CODE.search(line)
        return (code[0] + ": " if code else "") + "[private diagnostic details redacted]"
    tokens = []
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_-]*|[^\s]", line):
        safe = token.lower() in _SAFE_WORDS or _VENDOR_CODE.fullmatch(token) or token in "():,.;"
        projected = token if safe else "[redacted]"
        if projected != "[redacted]" or not tokens or tokens[-1] != projected:
            tokens.append(projected)
    return " ".join(tokens)[:MAX_EXCERPT_LENGTH]


def _acceptance(value):
    if value is None:
        return {"data_validity": "unknown", "specification": "unknown"}, "not_supplied"
    if type(value) is not dict:
        _invalid("Acceptance must be a bounded explicit summary.")
    if set(value) == {"measurements"}:
        try:
            result = aggregate_acceptance(value["measurements"])
        except DomainError:
            _invalid("Acceptance measurement checks are invalid.")
        return {"data_validity": {"valid": "valid", "invalid": "invalid"}.get(result["measurement_validity"], "unknown"),
                "specification": {"pass": "pass", "fail": "fail"}.get(result["specification_result"], "unknown")}, "caller_measurement_checks"
    if set(value) != {"data_validity", "specification"}:
        _invalid("Acceptance summary contains unsupported fields.")
    validity, specification = value["data_validity"], value["specification"]
    if (type(validity) is not str or validity not in {"valid", "invalid", "unknown"}
            or type(specification) is not str or specification not in {"pass", "fail", "unknown"}
            or validity != "valid" and specification != "unknown"):
        _invalid("A specification verdict requires explicitly valid data.")
    return dict(value), "caller_summary"


def _oom_evidence(value):
    if value is None:
        return None, False
    if type(value) is not dict or type(value.get("run_bound")) is not bool:
        _invalid("OOM evidence must explicitly state its run binding.")
    source = value.get("source")
    if source == "kernel" and set(value) == {"source", "run_bound", "oom_kill"} and type(value["oom_kill"]) is bool:
        return dict(value), value["run_bound"] and value["oom_kill"]
    if (source == "cgroup" and set(value) == {"source", "run_bound", "oom_kill_delta"}
            and type(value["oom_kill_delta"]) is int and 0 <= value["oom_kill_delta"] <= 2**31-1):
        return dict(value), value["run_bound"] and value["oom_kill_delta"] > 0
    _invalid("OOM evidence requires a kernel event or a bounded cgroup kill delta.")


def diagnose_spectre(log_text, *, execution_state, exit_code, acceptance=None, oom_evidence=None):
    """Classify a supplied log segment and report independent acceptance dimensions.

    State is queued/running/completed/failed/cancelled/unknown; exit_code is an
    integer -255..255 or None. A caller summary is {data_validity,specification};
    alternatively {measurements:[...]} uses the existing acceptance reducer.
    Kernel/cgroup evidence must explicitly bind the event/delta to this run.
    Every returned suggestion is inert prose. No agent proposal is generated.
    """
    lines, byte_count = _log_lines(log_text)
    if type(execution_state) is not str or execution_state not in _STATES:
        _invalid("Unsupported explicit execution state.")
    if exit_code is not None and (type(exit_code) is not int or not -255 <= exit_code <= 255):
        _invalid("Exit code must be an integer from -255 to 255, or null.")
    accepted, acceptance_source = _acceptance(acceptance)
    oom, confirmed_oom = _oom_evidence(oom_evidence)
    matches = {}
    previous_severity = None
    for number, line in enumerate(lines, 1):
        header = _MESSAGE.match(line)
        severity = ("warning" if header and header[1].lower() in {"warning", "notice"} else "error")
        # Spectre emits an error header followed by an indented message. Limit
        # continuation to that immediate line; arbitrary later prose is inert.
        explicit_oom = bool(_EXPLICIT_OOM.fullmatch(line))
        is_message = bool(header) or explicit_oom or previous_severity is not None and bool(line[:1].isspace())
        if not header and is_message and not explicit_oom:
            severity = previous_severity
        previous_severity = severity if header else None
        if not is_message:
            continue
        for category, pattern in _PATTERNS.items():
            if pattern.search(line):
                if category == "oom" and (severity != "error" or _NEGATED_OOM.search(line)):
                    continue
                entry = matches.setdefault(category, {"severity": severity, "lines": []})
                if severity == "error":
                    entry["severity"] = "error"
                entry["lines"].append(number)
    if confirmed_oom:
        matches.setdefault("oom", {"severity": "error", "lines": []})
    if exit_code in (137, -9) and "oom" not in matches:
        matches["suspected_oom"] = {"severity": "warning", "lines": []}
    if not matches:
        matches["unknown"] = {"severity": "error" if execution_state == "failed" or exit_code not in (0, None) else "info", "lines": []}
    diagnostics = []
    for category in _ACTIONS:
        if category not in matches:
            continue
        entry = matches[category]; numbers = entry["lines"][:MAX_EVIDENCE_LINES]
        diagnostics.append({"category": category, "severity": entry["severity"], "code": "spectre_" + category,
            "evidence_line_numbers": numbers,
            "redacted_excerpt": "\n".join(_excerpt(lines[number-1]) for number in numbers[:2])[:MAX_EXCERPT_LENGTH],
            "suggested_actions": list(_ACTIONS[category])})
    execution = execution_state
    if (execution_state == "completed" and exit_code not in (0, None)
            or execution_state in {"queued", "running"} and exit_code is not None
            or execution_state == "failed" and exit_code == 0):
        execution = "unknown"
    return {"schema": SCHEMA,
            "observed_context": {"execution_state": execution_state, "exit_code": exit_code,
                "log_line_count": len(lines), "log_bytes": byte_count, "log_scope": "caller_supplied_segment",
                "acceptance_source": acceptance_source, "oom_evidence": oom},
            "diagnostics": diagnostics, "scientific_acceptance": {"execution": execution, **accepted},
            "agent_proposal": None, "advisory_only": True}

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
    "soa_voltage": ("Inspect the cited terminal-voltage violation against the selected model's SOA limits.",
                    "Keep the original bias and model settings in this result; review any new experiment separately."),
    "soa_current": ("Inspect the cited terminal-current violation against the selected model's SOA limits.",
                    "A completed simulation does not establish operating qualification."),
    "model_bin": ("Check the original width, length and geometry against the selected model bins.",
                  "Do not substitute a model or change geometry merely to remove the diagnostic."),
    "scale_override": ("Inspect duplicate scalefactor declarations and the effective selected configuration.",
                       "A duplicate declaration alone does not prove conflicting values or changed device geometry."),
    "warnings_suppressed": ("Retain the reported suppression count separately from the footer warning count.",
                            "Suppressed messages limit conclusions based on the absence of diagnostics."),
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
        r"|\bparameter\b.{0,160}\b(?:below (?:the )?minimum|above (?:the )?maximum)\b"
        r"|\b(?:width|length|weff|leff|w|l)\b.{0,120}\b(?:out of range|out of bounds|outside (?:the )?(?:valid |allowed |model )?range|below (?:the )?minimum|above (?:the )?maximum)\b"
        r"|\b(?:width|length|weff|leff|w|l)\b.{0,80}\b(?:less than|greater than|below|above)\b.{0,40}\b(?:wmin|wmax|lmin|lmax)\b", re.I),
    "soa_voltage": re.compile(
        r"\bCMI-275[1-5]\b"
        r"|(?=.{0,1000}\b(?:SOA|safe[- ]operating[- ]area)\b)(?=.{0,1000}\b(?:voltage|vgs|vds|vgd|vgb|vbs|vbd|overvoltage)\b).{0,1000}\b(?:exceeded|exceeds|above|outside|violation|violated)\b"
        r"|\bv(?:gs|ds|gd|gb|bs|bd)\b.{0,160}\b(?:exceeded|exceeds)\b.{0,80}\bv(?:gs|ds|gd|gb|bs|bd)_max\b", re.I),
    "soa_current": re.compile(
        r"(?=.{0,1000}\b(?:SOA|safe[- ]operating[- ]area)\b)(?=.{0,1000}\b(?:current|ids|id|ig|is|ib|overcurrent)\b).{0,1000}\b(?:exceeded|exceeds|above|outside|violation|violated)\b"
        r"|\bi(?:ds|d|g|s|b)\b.{0,160}\b(?:exceeded|exceeds)\b.{0,80}\bi(?:ds|d|g|s|b)_max\b", re.I),
    "model_bin": re.compile(
        r"\b(?:no|cannot find|could not find|unable to find)\b.{0,100}\b(?:model\s+)?bin\b"
        r"|\b(?:model[- ]bin|binning)\b.{0,120}\b(?:not found|out of range|no matching|failed)\b"
        r"|\bno\b.{0,60}\bmodel\b.{0,60}\bmatches\b.{0,100}\b(?:width|length|geometry|w|l)\b"
        r"|\b(?:width|length|w|l)\b.{0,120}\b(?:outside|out of|do not fall|does not fall|do not fit|does not fit)\b.{0,100}\b(?:model\s+)?bins?\b", re.I),
    "scale_override": re.compile(
        r"\bSFE-1131\b(?=.{0,500}\bscalefactor\b)"
        r"|\bscalefactor\b.{0,200}\b(?:redefined|duplicate|overridden|also been specified|specified again)\b", re.I),
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
_VENDOR_CODE = re.compile(r"\b(?:SFE-(?:23|874|1131)|CMI-275[1-5]|SPECTRE-(?:16080|11005))\b", re.I)
_NEGATED_QUALIFICATION = re.compile(
    r"\b(?:no|without)\b.{0,32}\b(?:violations?|overvoltage|overcurrent|out of range)\b"
    r"|\b(?:has not|did not|does not|do not|not)\s+(?:been\s+)?exceed(?:ed|s)?\b"
    r"|\bnot\s+(?:an?\s+)?(?:SOA\s+)?(?:(?:voltage|current)\s+)?(?:limit\s+)?violation\b"
    r"|\b(?:if|unless)\b.{0,80}\b(?:exceeds?|out of range|violation)\b", re.I)
_SUPPRESSED = re.compile(
    r"^\s*(?:(?:spectre|simerror)\s*:\s*)?"
    r"(?:(?:warning|notice)(?:\s+\([A-Za-z][A-Za-z0-9]*-[0-9]+\))?\s*:\s*)?"
    r"([0-9]{1,15})\s+warnings?\s+suppressed[.!]?\s*$", re.I)
_FOOTER = re.compile(r"^\s*spectre completes with ([0-9]{1,15}) errors?, ([0-9]{1,15}) warnings?(?:,? and ([0-9]{1,15}) notices?)?\.\s*$", re.I)
_CANONICAL_MESSAGE = re.compile(
    r"^\s*(?:(?:spectre|simerror)\s*:\s*)?(error|fatal(?: error)?|warning|notice)"
    r"(?:\s+\(([A-Za-z][A-Za-z0-9]*-[0-9]+)\))?\s*:\s*(.*?)\s*$", re.I)
_CONTEXT_HEADER = re.compile(
    r"^\s*(error|fatal(?: error)?|warning|notice)\s+(?:from spectre|found by spectre)\b.*$", re.I)
_TRUNCATION = re.compile(r"^\s*(?:\[?(?:log |output )?truncated\]?|<truncated>|\.\.\.|…).*$", re.I)
# Only the complete fixed Spectre countdown is progress, not omitted evidence.
_COMPLETE_PROGRESS = re.compile(r"\s*\.{6}9\.{6}8\.{6}7\.{6}6\.{6}5\.{6}4\.{6}3\.{6}2\.{6}1\.{6}0\s*")
_SCALE_VALUE = re.compile(r"\bscalefactor\s*=\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\b", re.I)
_SAFE_WORDS = frozenset("""error fatal warning notice spectre syntax unexpected end of line file token equals
    undefined model subcircuit instance is referencing not found defined no dc solution convergence failed failure
    did converge matrix singular time step too small below minimum above maximum parameter outside the allowed
    valid range violation out bounds must should be positive nonnegative greater less memory allocation cannot
    allocate detected at during circuit read in flattening""".split())


def _warning_summary(lines):
    """Report observed counters independently; repeated excerpts are not summed."""
    footers, suppression, footer_lines, suppression_lines = [], [], [], []
    for number, line in enumerate(lines, 1):
        footer, suppressed = _FOOTER.fullmatch(line), _SUPPRESSED.fullmatch(line)
        if footer:
            footers.append(tuple(int(x) if x is not None else None for x in footer.groups()))
            footer_lines.append(number)
        if suppressed:
            suppression.append(int(suppressed[1])); suppression_lines.append(number)
    unique_footer, unique_suppression = set(footers), set(suppression)
    counts = next(iter(unique_footer)) if len(unique_footer) == 1 else (None, None, None)
    return {"reported_errors": counts[0], "reported_warnings": counts[1], "reported_notices": counts[2],
            "suppressed_warnings": next(iter(unique_suppression)) if len(unique_suppression) == 1 else None,
            "footer_line_numbers": footer_lines[:MAX_EVIDENCE_LINES],
            "suppression_line_numbers": suppression_lines[:MAX_EVIDENCE_LINES],
            "count_conflict": len(unique_footer) > 1 or len(unique_suppression) > 1}


def _diagnostic_coverage(lines):
    """Count distinct diagnostic bodies, never a Spectre context header twice.

    The legacy classifier inspects a header and its immediate indented body.
    Longer continuations, unparsed headers, duplicates and truncation therefore
    remain explicit coverage gaps for an absence-based qualification verdict.
    Canonical text is used only for in-memory deduplication and is not exported.
    """
    records, consumed, gaps, footer_lines = {}, set(), [], []
    duplicates, after_footer = 0, False
    counts = {"error": 0, "warning": 0, "notice": 0}
    body_lines = {key: [] for key in counts}
    for index, line in enumerate(lines):
        if _FOOTER.fullmatch(line):
            footer_lines.append(index + 1)
            continue
        if ((_TRUNCATION.match(line) and not _COMPLETE_PROGRESS.fullmatch(line))
                or re.match(r"^\s*spectre completes\b", line, re.I)):
            gaps.append(index + 1)
        if index in consumed:
            continue
        canonical, context = _CANONICAL_MESSAGE.fullmatch(line), _CONTEXT_HEADER.fullmatch(line)
        if context:
            following = lines[index + 1] if index + 1 < len(lines) else ""
            if _CANONICAL_MESSAGE.fullmatch(following):
                # "Warning from spectre ..." merely introduces the next record.
                continue
            # The classifier still observes an immediate uncoded body, but a
            # context-only shape cannot establish complete canonical coverage.
            gaps.append(index + 1)
            continue
        elif canonical:
            severity, identity, body_index = canonical[1], (canonical[2] or "") + ":" + canonical[3], index
            following = lines[index + 1] if index + 1 < len(lines) else ""
            if following.strip() and following[:1].isspace() and not _MESSAGE.match(following) and not _FOOTER.fullmatch(following):
                identity += " " + following.strip()
                body_index = index + 1
                consumed.add(body_index)
            if not canonical[3] and body_index == index:
                gaps.append(index + 1)
        elif _EXPLICIT_OOM.fullmatch(line):
            severity, identity, body_index = "error", line.strip(), index
        else:
            if _MESSAGE.match(line):
                gaps.append(index + 1)
            continue
        following = lines[body_index + 1] if body_index + 1 < len(lines) else ""
        if following.strip() and following[:1].isspace() and not _MESSAGE.match(following) and not _FOOTER.fullmatch(following):
            gaps.append(body_index + 2)
        severity = severity.lower()
        severity = "error" if severity.startswith("fatal") else severity
        key = (severity, " ".join(identity.split()))
        if key in records:
            duplicates += 1
        else:
            records[key] = index + 1
            counts[severity] += 1
            body_lines[severity].append(body_index + 1)
        if footer_lines:
            after_footer = True
    return {"observed_errors": counts["error"], "observed_warnings": counts["warning"],
            "observed_notices": counts["notice"], "completion_footer_count": len(footer_lines),
            "duplicate_record_count": duplicates, "coverage_gap_line_numbers": sorted(set(gaps))[:MAX_EVIDENCE_LINES],
            "records_after_footer": after_footer,
            "structure_complete": not gaps and not duplicates and not after_footer,
            "error_line_numbers": body_lines["error"][:MAX_EVIDENCE_LINES],
            "warning_line_numbers": body_lines["warning"][:MAX_EVIDENCE_LINES],
            "notice_line_numbers": body_lines["notice"][:MAX_EVIDENCE_LINES]}


def _scale_relation(lines, numbers):
    relations = []
    for number in numbers:
        values = _SCALE_VALUE.findall(lines[number-1])
        if len(values) < 2:
            relations.append("unknown")
        else:
            # Comparison only: model values are never exported in public prose.
            from decimal import Decimal, DecimalException
            try:
                relations.append("same" if len({Decimal(value) for value in values}) == 1 else "conflict")
            except (DecimalException, OverflowError, ValueError):
                relations.append("unknown")
    return "conflict" if "conflict" in relations else "unknown" if "unknown" in relations else "same"


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
    warnings = _warning_summary(lines)
    previous_severity = None
    for number, line in enumerate(lines, 1):
        if _SUPPRESSED.fullmatch(line) and int(_SUPPRESSED.fullmatch(line)[1]) > 0:
            matches.setdefault("warnings_suppressed", {"severity": "warning", "lines": []})["lines"].append(number)
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
                if category in {"soa_voltage", "soa_current", "parameter_bounds"} and _NEGATED_QUALIFICATION.search(line):
                    continue
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
    details = {}
    if "scale_override" in matches:
        details["scale_override"] = {"duplicate_seen": True,
                                     "values_relation": _scale_relation(lines, matches["scale_override"]["lines"])}
    return {"schema": SCHEMA,
            "observed_context": {"execution_state": execution_state, "exit_code": exit_code,
                "log_line_count": len(lines), "log_bytes": byte_count, "log_scope": "caller_supplied_segment",
                "acceptance_source": acceptance_source, "oom_evidence": oom,
                "warning_summary": warnings, "diagnostic_coverage": _diagnostic_coverage(lines),
                "classification_details": details},
            "diagnostics": diagnostics, "scientific_acceptance": {"execution": execution, **accepted},
            "agent_proposal": None, "advisory_only": True}

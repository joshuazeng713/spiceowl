"""Pure five-dimensional simulation report; no file, network or agent actions.

Completeness, frozen scope and dependency binding are explicit caller evidence.
This reducer does not authenticate that evidence or qualify an entire process.
Paths parsed from a log are never opened, executed or exported by this module.
"""

import re

from spiceowl.domain.common import DomainError
from spiceowl.integrations.spectre_diagnostics import diagnose_spectre

SCHEMA = "spiceowl.simulation-report.v1"
QUALIFICATION_CHECKS = frozenset({"soa_voltage", "soa_current", "model_parameter_range", "model_bin", "scale_override"})
DEPENDENCY_KINDS = frozenset({"model_file", "tmi_library", "tool_library", "tool_config", "directory"})
EXECUTION_STATUSES = frozenset({"queued", "running", "completed", "failed", "cancelled", "unknown"})
MEASUREMENT_STATUSES = frozenset({"valid", "invalid", "unknown"})
FUNCTIONAL_STATUSES = frozenset({"pass", "fail", "unknown"})
QUALIFICATION_STATUSES = frozenset({"passed_within_scope", "violations_detected", "unknown"})
DEPENDENCY_STATUSES = frozenset({"verified_within_scope", "incomplete", "mismatch", "unknown"})
MAX_DEPENDENCIES = 256
_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,179}")
_SHA = re.compile(r"[a-f0-9]{64}")
_CATEGORY_CHECK = {"soa_voltage": "soa_voltage", "soa_current": "soa_current",
                   "parameter_bounds": "model_parameter_range", "model_bin": "model_bin",
                   "scale_override": "scale_override"}


def _invalid(message="Invalid bounded simulation report evidence."):
    raise DomainError(message, "invalid_simulation_report")


def _id(value):
    return type(value) is str and _ID.fullmatch(value) is not None


def _sha(value):
    return type(value) is str and _SHA.fullmatch(value) is not None


def _evidence(code, source, *, lines=None, dependency_id=None, sha256=None, count=None):
    item = {"code": code, "source": source, "line_numbers": [] if lines is None else list(lines)}
    if dependency_id is not None:
        item["dependency_id"] = dependency_id
    if sha256 is not None:
        item["sha256"] = sha256
    if count is not None:
        item["count"] = count
    return item


def _dimension(status, scope, evidence):
    return {"status": status, "scope": scope, "evidence": evidence}


def _qualification_scope(value, complete):
    result = {"kind": "declared_operating_checks", "scope_id": None, "manifest_sha256": None,
              "frozen_before_execution": False, "checks": [], "log_complete": complete}
    if value is None:
        return result
    if (type(value) is not dict or set(value) != {"scope_id", "manifest_sha256", "frozen_before_execution", "checks"}
            or not _id(value["scope_id"]) or not _sha(value["manifest_sha256"])
            or type(value["frozen_before_execution"]) is not bool
            or type(value["checks"]) is not list or not 1 <= len(value["checks"]) <= len(QUALIFICATION_CHECKS)
            or any(type(check) is not str or check not in QUALIFICATION_CHECKS for check in value["checks"])
            or len(set(value["checks"])) != len(value["checks"])):
        _invalid("Qualification requires an explicit bounded frozen-check scope.")
    return {**result, **value, "checks": list(value["checks"])}


def _qualification(diagnostic, complete, scope_value):
    scope = _qualification_scope(scope_value, complete)
    accepted = diagnostic["scientific_acceptance"]
    context = diagnostic["observed_context"]
    summary = context["warning_summary"]
    coverage = context["diagnostic_coverage"]
    evidence, violations, uncertain_scale = [], False, False
    for finding in diagnostic["diagnostics"]:
        check = _CATEGORY_CHECK.get(finding["category"])
        if check is None:
            continue
        # Positive SOA/model violations are observations even outside a requested
        # scope. A scoped pass never certifies unrequested checks.
        covered = check in scope["checks"]
        if check == "scale_override":
            relation = context["classification_details"]["scale_override"]["values_relation"]
            evidence.append(_evidence("scalefactor_duplicate_" + relation, "spectre_log", lines=finding["evidence_line_numbers"]))
            if covered:
                # Different values establish a configuration conflict, not an
                # operating-voltage violation or a physical geometry verdict.
                uncertain_scale = relation != "same"
        else:
            violations = True
            evidence.append(_evidence(finding["code"], "spectre_log", lines=finding["evidence_line_numbers"]))
        if not covered:
            evidence.append(_evidence("observed_check_outside_declared_scope", "qualification_scope"))
    if violations:
        return _dimension("violations_detected", scope, evidence)
    counts_match = (summary["reported_errors"] == coverage["observed_errors"]
                    and summary["reported_warnings"] == coverage["observed_warnings"]
                    and (summary["reported_notices"] == coverage["observed_notices"]
                         or summary["reported_notices"] is None and coverage["observed_notices"] == 0))
    no_error = coverage["observed_errors"] == 0 and all(finding["severity"] != "error" for finding in diagnostic["diagnostics"])
    ready = True
    for condition, code in (
        (bool(scope["checks"]) and scope["frozen_before_execution"], "frozen_qualification_scope_missing"),
        (complete, "complete_log_not_declared"),
        (accepted["execution"] == "completed" and context["exit_code"] == 0, "completed_execution_not_confirmed"),
        (coverage["completion_footer_count"] == 1 and summary["reported_errors"] == 0 and not summary["count_conflict"], "consistent_completion_footer_missing"),
        (coverage["structure_complete"] and counts_match, "diagnostic_body_coverage_incomplete"),
        (no_error, "error_diagnostic_present"),
        (summary["suppressed_warnings"] in (None, 0) and not summary["count_conflict"], "suppressed_warning_coverage_gap"),
        (not uncertain_scale, "scalefactor_effect_not_established"),
    ):
        if not condition:
            ready = False
            evidence.append(_evidence(code, "spectre_log" if code in {"consistent_completion_footer_missing", "diagnostic_body_coverage_incomplete", "error_diagnostic_present", "suppressed_warning_coverage_gap", "scalefactor_effect_not_established"} else "qualification_scope"))
    if ready:
        evidence.append(_evidence("no_matching_violation_in_declared_checks", "spectre_log", lines=summary["footer_line_numbers"]))
    return _dimension("passed_within_scope" if ready else "unknown", scope, evidence)


def _dependency(value, run_id):
    scope = {"kind": "observed_files_with_directory_references", "scope_id": None, "manifest_sha256": None,
             "run_bound": False, "inventory_complete": False}
    if value is None:
        return _dimension("unknown", scope, [_evidence("dependency_audit_missing", "dependency_audit")])
    if (type(value) is not dict or set(value) != {"run_id", "scope_id", "manifest_sha256", "run_bound", "inventory_complete", "entries"}
            or not _id(value["run_id"]) or not _id(value["scope_id"]) or not _sha(value["manifest_sha256"])
            or type(value["run_bound"]) is not bool or type(value["inventory_complete"]) is not bool
            or type(value["entries"]) is not list or len(value["entries"]) > MAX_DEPENDENCIES):
        _invalid("Dependency audit requires a bounded explicit run and inventory binding.")
    seen = set()
    for row in value["entries"]:
        if (type(row) is not dict or not {"dependency_id", "kind", "pre_pinned", "hash_matches", "observed"} <= set(row)
                or set(row) - {"dependency_id", "kind", "pre_pinned", "hash_matches", "observed", "evidence_sha256"}
                or not _id(row["dependency_id"]) or row["dependency_id"] in seen
                or type(row["kind"]) is not str or row["kind"] not in DEPENDENCY_KINDS
                or type(row["pre_pinned"]) is not bool or type(row["observed"]) is not bool
                or row["hash_matches"] is not None and type(row["hash_matches"]) is not bool
                or row["kind"] == "directory" and row["hash_matches"] is not None
                or "evidence_sha256" in row and not _sha(row["evidence_sha256"])):
            _invalid("Dependency entries require opaque IDs, controlled kinds and explicit checks; paths are unsupported.")
        seen.add(row["dependency_id"])
    scope.update(scope_id=value["scope_id"], manifest_sha256=value["manifest_sha256"],
                 run_bound=value["run_bound"] and run_id is not None and value["run_id"] == run_id,
                 inventory_complete=value["inventory_complete"])
    if not scope["run_bound"]:
        return _dimension("unknown", scope, [_evidence("dependency_run_binding_unconfirmed", "dependency_audit")])
    evidence, mismatch, incomplete = [], False, not value["inventory_complete"] or not value["entries"]
    if not value["inventory_complete"]:
        evidence.append(_evidence("dependency_inventory_incomplete", "dependency_audit"))
    if not value["entries"]:
        evidence.append(_evidence("dependency_inventory_empty", "dependency_audit"))
    for row in value["entries"]:
        if not row["observed"]:
            code = "dependency_not_observed"; incomplete = True
        elif not row["pre_pinned"]:
            code = "unregistered_runtime_" + row["kind"]; incomplete = True
        elif row["kind"] == "directory":
            # A named tmipath directory is a reference, not a file byte digest.
            # Complete inventory means its actually read files were enumerated
            # separately; this does not attest every file inside the directory.
            code = "registered_directory_reference"
        elif row["hash_matches"] is False:
            code = "dependency_hash_mismatch"; mismatch = True
        elif row["hash_matches"] is not True:
            code = "dependency_hash_unverified"; incomplete = True
        else:
            code = "pre_pinned_dependency_verified"
        evidence.append(_evidence(code, "dependency_audit", dependency_id=row["dependency_id"], sha256=row.get("evidence_sha256")))
    return _dimension("mismatch" if mismatch else "incomplete" if incomplete else "verified_within_scope", scope, evidence)


def build_spectre_report(log_text, *, execution_state, exit_code, acceptance=None, oom_evidence=None,
                         log_complete=False, qualification_scope=None, dependency_audit=None, run_id=None):
    """Build five independent evidence-bearing dimensions without an overall pass.

    ``acceptance`` and ``oom_evidence`` use ``diagnose_spectre``'s established
    shapes. A positive qualification requires declared complete log coverage,
    one unambiguous completion footer whose counters match the distinct canonical
    message bodies, no observed error, and a previously frozen nonempty scope.
    Suppressed warnings block absence-based qualification. The dependency audit
    must refer to ``run_id``; even a matching post-run hash cannot replace a
    missing pre-execution pin. Directory entries require ``hash_matches=None``;
    registration verifies their reference only, with observed file bytes checked
    by separate entries. All IDs are opaque, bounded presentation data.
    """
    if type(log_complete) is not bool or run_id is not None and not _id(run_id):
        _invalid("Use an explicit boolean log-coverage claim and a bounded run ID.")
    diagnostic = diagnose_spectre(log_text, execution_state=execution_state, exit_code=exit_code,
                                  acceptance=acceptance, oom_evidence=oom_evidence)
    accepted, context = diagnostic["scientific_acceptance"], diagnostic["observed_context"]
    return {"schema": SCHEMA, "run_id": run_id, "advisory_only": True,
            "execution_status": _dimension(accepted["execution"], {"kind": "caller_execution_receipt"},
                [_evidence("caller_execution_" + accepted["execution"], "execution_receipt")]),
            "measurement_validity": _dimension(accepted["data_validity"], {"kind": context["acceptance_source"]},
                [_evidence("measurement_" + accepted["data_validity"], "acceptance")]),
            "functional_performance": _dimension(accepted["specification"], {"kind": context["acceptance_source"]},
                [_evidence("functional_" + accepted["specification"], "acceptance")]),
            "operating_qualification": _qualification(diagnostic, log_complete, qualification_scope),
            "dependency_provenance": _dependency(dependency_audit, run_id),
            "diagnostics": diagnostic}

"""Generic deterministic next-action orchestrator for authored testbenches & direct runs.

Enforces explicit stage transitions, missing-input preservation, uncertain-dispatch
reconciliation, lossless measurement preconditions, and five-dimensional scientific
reporting without relying on an LLM prompt as a workflow gate.

Pure Python stdlib only — zero filesystem, network, or subprocess side effects,
100% compatible with macOS, Linux, and Windows, and safe in both standalone and
embedded CircuitGit modes.
"""

from __future__ import annotations

from copy import deepcopy

from spiceowl.domain.common import DomainError


ORCHESTRATOR_SCHEMA = "spiceowl.next-action.v1"

STAGES = (
    "define_experiment",
    "resolve_dependencies",
    "validate_and_freeze",
    "dispatch",
    "observe_and_collect",
    "measure",
    "report",
)

_ACTIVE_RUN_STATES = frozenset({"submitted", "running_observed"})
_UNCERTAIN_RUN_STATES = frozenset({"launch_outcome_unknown", "reconcile_required"})
_TERMINAL_FAILURE_STATES = frozenset({"simulator_failed", "job_setup_failed", "input_hash_failure"})


def _missing(field: str, reason: str, *, blocking: bool = True) -> dict:
    return {"field": field, "reason": reason, "blocking": blocking}


def _action(
    action: str,
    *,
    tool: str | None = None,
    operation: str | None = None,
    arguments_template: dict | None = None,
    requires_human_approval: bool = False,
    reason: str = "",
) -> dict:
    return {
        "action": action,
        "tool": tool,
        "operation": operation,
        "arguments_template": deepcopy(arguments_template) if arguments_template is not None else {},
        "requires_human_approval": requires_human_approval,
        "reason": reason,
    }


def evaluate_next_action(state: dict) -> dict:
    """Evaluate an explicit experiment/run snapshot and return bounded permitted next actions.

    Never fabricates missing PVT, model, signal, or criteria fields.
    Never authorizes blind retries after ``launch_outcome_unknown`` or hash mismatch.
    Never permits measuring scientific specs from ``downsampled_preview`` waveforms.
    Never rewrites criteria or triggers recovery on a valid performance spec failure.
    """
    if not isinstance(state, dict):
        raise DomainError("Orchestrator state must be a JSON object.", "invalid_orchestrator_state")

    exp = state.get("experiment") or {}
    deps = state.get("dependencies") or {}
    plan = state.get("plan") or {}
    run = state.get("run") or {}
    meas = state.get("measurement") or {}
    rep = state.get("report") or {}

    # 0. Hard integrity gate: tampered or mismatched frozen hashes halt immediately.
    if plan.get("hash_mismatch") is True or run.get("state") == "input_hash_failure":
        return {
            "schema": ORCHESTRATOR_SCHEMA,
            "stage": "validate_and_freeze",
            "ready_to_advance": False,
            "complete": False,
            "halt_reason": "input_hash_mismatch",
            "missing": [_missing("frozen_manifest_integrity", "Frozen input or manifest SHA-256 mismatch detected; execution halted.")],
            "permitted_actions": [
                _action(
                    "inspect_integrity_mismatch",
                    tool="spiceowl_direct_read",
                    operation="inspect",
                    arguments_template={"run_id": run.get("run_id") or plan.get("run_id") or "<run_id>"},
                    requires_human_approval=False,
                    reason="Audit why frozen bytes diverged before preparing any new reviewed revision.",
                ),
            ],
            "forbidden_actions": ["launch", "stage", "blind_retry", "overwrite_frozen_plan"],
            "scientific_verdict": "halted_integrity_error",
        }

    # 1. Stage: define_experiment
    exp_missing: list[dict] = []
    if not (exp.get("netlist_text") or exp.get("agent_testbench") or exp.get("recipe_id")):
        exp_missing.append(_missing("experiment.netlist_or_testbench", "Supply an authored testbench, netlist, or registered recipe ID."))
    if not exp.get("analyses"):
        exp_missing.append(_missing("experiment.analyses", "Declare at least one explicit simulator analysis (e.g. tran, ac, dc)."))
    pvt = exp.get("pvt")
    if not isinstance(pvt, dict) or not pvt.get("corner") or pvt.get("voltage") is None or pvt.get("temperature") is None:
        exp_missing.append(_missing("experiment.pvt", "Specify explicit corner, supply voltage, and temperature; never infer defaults."))
    if not exp.get("signals"):
        exp_missing.append(_missing("experiment.signals", "Declare explicit saved/measured signal names."))
    if not exp.get("criteria"):
        exp_missing.append(_missing("experiment.criteria", "Declare explicit quantitative acceptance criteria (or mark exploratory_only=true)."))
    if exp.get("exploratory_only") is True:
        exp_missing = [m for m in exp_missing if m["field"] != "experiment.criteria"]

    if exp_missing:
        return {
            "schema": ORCHESTRATOR_SCHEMA,
            "stage": "define_experiment",
            "ready_to_advance": False,
            "complete": False,
            "halt_reason": "missing_experiment_definition",
            "missing": exp_missing,
            "permitted_actions": [
                _action(
                    "request_missing_fields",
                    arguments_template={"missing_fields": [m["field"] for m in exp_missing]},
                    requires_human_approval=False,
                    reason="Ask the engineer or caller to supply exact missing scientific definitions without guessing.",
                ),
            ],
            "forbidden_actions": ["prepare", "stage", "launch", "infer_pvt_or_criteria"],
            "scientific_verdict": "incomplete_specification",
        }

    # 2. Stage: resolve_dependencies
    dep_missing: list[dict] = []
    unresolved = deps.get("unresolved_models") or []
    if unresolved:
        dep_missing.append(_missing("dependencies.unresolved_models", f"Unresolved models require explicit selection: {', '.join(str(x) for x in unresolved[:8])}."))
    if deps.get("audit_required", True) and not deps.get("fresh_audit_passed", False):
        if deps.get("cache_only_hit"):
            dep_missing.append(_missing("dependencies.fresh_audit_passed", "PDK LUT cache hit alone cannot authorize execution; run a fresh site-bound dependency audit."))
        else:
            dep_missing.append(_missing("dependencies.fresh_audit_passed", "A fresh dependency audit must pass before freezing the execution plan."))

    if dep_missing:
        permitted = []
        if unresolved:
            permitted.append(_action(
                "lookup_pdk_candidates",
                tool="spiceowl_pdk_lookup",
                operation="query_best_bindings",
                arguments_template={"models": list(unresolved[:16])},
                requires_human_approval=False,
                reason="Inspect observed PDK candidates without fabricating private device semantics.",
            ))
        permitted.append(_action(
            "run_fresh_dependency_audit",
            tool="spiceowl_onboarding",
            operation="audit_dependencies",
            requires_human_approval=True,
            reason="Verify current remote model files and fingerprints before freezing the plan.",
        ))
        return {
            "schema": ORCHESTRATOR_SCHEMA,
            "stage": "resolve_dependencies",
            "ready_to_advance": False,
            "complete": False,
            "halt_reason": "dependencies_unresolved",
            "missing": dep_missing,
            "permitted_actions": permitted,
            "forbidden_actions": ["launch", "stage", "invent_model_section"],
            "scientific_verdict": "dependencies_unverified",
        }

    # 3. Stage: validate_and_freeze
    val_missing: list[dict] = []
    if plan.get("static_validation_passed") is False:
        val_missing.append(_missing("plan.static_validation_passed", "Static netlist/testbench validation failed; fix reported errors first."))
    if not (plan.get("host_id") or plan.get("host_ids")):
        val_missing.append(_missing("plan.host_selection", "Select a registered host_id or explicit host_ids pool."))
    if not plan.get("frozen") or not plan.get("manifest_sha256"):
        if val_missing:
            return {
                "schema": ORCHESTRATOR_SCHEMA,
                "stage": "validate_and_freeze",
                "ready_to_advance": False,
                "complete": False,
                "halt_reason": "validation_incomplete",
                "missing": val_missing,
                "permitted_actions": [
                    _action(
                        "recommend_hosts",
                        tool="spiceowl_recommend_hosts",
                        requires_human_approval=False,
                        reason="Evaluate stored host capacity snapshots before freezing a run plan.",
                    ),
                ],
                "forbidden_actions": ["stage", "launch"],
                "scientific_verdict": "not_prepared",
            }
        is_batch = bool(plan.get("is_batch"))
        return {
            "schema": ORCHESTRATOR_SCHEMA,
            "stage": "validate_and_freeze",
            "ready_to_advance": True,
            "complete": False,
            "halt_reason": None,
            "missing": [],
            "permitted_actions": [
                _action(
                    "freeze_plan",
                    tool="spiceowl_direct_control",
                    operation="batch_prepare" if is_batch else "prepare",
                    arguments_template={
                        ("batch_id" if is_batch else "run_key"): plan.get("batch_id") or plan.get("run_key") or "<explicit_key>",
                        "host_id": plan.get("host_id"),
                    },
                    requires_human_approval=True,
                    reason="Freeze immutable netlist, support files, limits, and SHA-256 manifest locally without launching.",
                ),
            ],
            "forbidden_actions": ["launch_unprepared"],
            "scientific_verdict": "ready_to_freeze",
        }

    # 4. Stage: dispatch (strict uncertain-dispatch reconciliation gate!)
    run_id = run.get("run_id") or plan.get("run_id") or "<run_id>"
    run_state = run.get("state") or "prepared"

    if run_state in _UNCERTAIN_RUN_STATES:
        return {
            "schema": ORCHESTRATOR_SCHEMA,
            "stage": "dispatch",
            "ready_to_advance": False,
            "complete": False,
            "halt_reason": "uncertain_dispatch_reconcile_required",
            "missing": [
                _missing("run.reconciled_state", f"Run '{run_id}' is in '{run_state}'. Reconcile original submission identity before any retry."),
            ],
            "permitted_actions": [
                _action(
                    "lookup_original_submission",
                    tool="spiceowl_direct_read",
                    operation="lookup",
                    arguments_template={"key": run.get("submission_key") or run_id},
                    requires_human_approval=False,
                    reason="Verify original persisted submission key mapping locally.",
                ),
                _action(
                    "reconcile_remote_status",
                    tool="spiceowl_direct_control",
                    operation="status",
                    arguments_template={"run_id": run_id},
                    requires_human_approval=True,
                    reason="Query remote host/scheduler for the original bound manifest_sha256 without launching.",
                ),
            ],
            "forbidden_actions": ["prepare_replacement_key", "launch", "blind_relaunch"],
            "scientific_verdict": "outcome_unknown",
        }

    if run_state in ("prepared", "stage_incomplete"):
        return {
            "schema": ORCHESTRATOR_SCHEMA,
            "stage": "dispatch",
            "ready_to_advance": True,
            "complete": False,
            "halt_reason": None,
            "missing": [],
            "permitted_actions": [
                _action(
                    "stage_inputs",
                    tool="spiceowl_direct_control",
                    operation="stage",
                    arguments_template={"run_id": run_id},
                    requires_human_approval=True,
                    reason="Write frozen inputs to isolated remote directory with host/user identity verification.",
                ),
            ],
            "forbidden_actions": ["launch_before_stage"],
            "scientific_verdict": "prepared_not_staged",
        }

    if run_state == "staged" and not run.get("dispatch_claimed"):
        return {
            "schema": ORCHESTRATOR_SCHEMA,
            "stage": "dispatch",
            "ready_to_advance": True,
            "complete": False,
            "halt_reason": None,
            "missing": [],
            "permitted_actions": [
                _action(
                    "launch_once",
                    tool="spiceowl_direct_control",
                    operation="launch",
                    arguments_template={"run_id": run_id},
                    requires_human_approval=True,
                    reason="Claim atomic once-only launch and dispatch simulation to bound executor.",
                ),
            ],
            "forbidden_actions": ["duplicate_launch"],
            "scientific_verdict": "staged_ready_to_launch",
        }

    # 5. Stage: observe_and_collect
    if run_state in _ACTIVE_RUN_STATES:
        return {
            "schema": ORCHESTRATOR_SCHEMA,
            "stage": "observe_and_collect",
            "ready_to_advance": False,
            "complete": False,
            "halt_reason": "simulation_in_progress",
            "missing": [],
            "permitted_actions": [
                _action(
                    "poll_status",
                    tool="spiceowl_direct_control",
                    operation="status",
                    arguments_template={"run_id": run_id},
                    requires_human_approval=True,
                    reason="Observe remote job state without mutating or re-launching.",
                ),
            ],
            "forbidden_actions": ["launch", "collect_before_terminal"],
            "scientific_verdict": "running",
        }

    if run.get("collection_phase") != "complete":
        return {
            "schema": ORCHESTRATOR_SCHEMA,
            "stage": "observe_and_collect",
            "ready_to_advance": True,
            "complete": False,
            "halt_reason": None,
            "missing": [],
            "permitted_actions": [
                _action(
                    "collect_artifacts",
                    tool="spiceowl_direct_control",
                    operation="collect",
                    arguments_template={"run_id": run_id},
                    requires_human_approval=True,
                    reason="Collect verified terminal receipts, logs, and lightweight waveform index.",
                ),
            ],
            "forbidden_actions": ["measure_uncollected"],
            "scientific_verdict": "terminal_uncollected",
        }

    # Terminal operational failure after collection
    if run_state in _TERMINAL_FAILURE_STATES:
        fclass = run.get("failure_class") or "unknown_failure"
        recoverable = fclass in ("confirmed_oom", "license_unavailable") and bool((run.get("recovery_policy") or {}).get("enabled"))
        permitted = [
            _action(
                "inspect_five_dim_report",
                tool="spiceowl_direct_read",
                operation="report",
                arguments_template={"run_id": run_id},
                requires_human_approval=False,
                reason="Review collected failure evidence and five-dimensional report.",
            ),
        ]
        if recoverable:
            permitted.append(_action(
                "evaluate_bounded_recovery",
                arguments_template={"run_id": run_id, "failure_class": fclass},
                requires_human_approval=True,
                reason="Evaluate bounded M4 operational recovery without altering netlist or scientific criteria.",
            ))
        return {
            "schema": ORCHESTRATOR_SCHEMA,
            "stage": "report",
            "ready_to_advance": False,
            "complete": not recoverable,
            "halt_reason": f"operational_failure:{fclass}",
            "missing": [],
            "permitted_actions": permitted,
            "forbidden_actions": ["alter_netlist_or_criteria", "claim_scientific_pass"],
            "scientific_verdict": "execution_failed",
        }

    # 6. Stage: measure (strict lossless & required-signal gates!)
    meas_missing: list[dict] = []
    if meas.get("representation") == "downsampled_preview" or meas.get("measurement_safe") is False:
        return {
            "schema": ORCHESTRATOR_SCHEMA,
            "stage": "measure",
            "ready_to_advance": False,
            "complete": False,
            "halt_reason": "non_exact_waveform_rejected",
            "missing": [
                _missing("measurement.exact_waveform", "Refusing scientific measurement on downsampled_preview (measurement_safe=false). Extract lossless mode='exact' or exact_cycle samples."),
            ],
            "permitted_actions": [
                _action(
                    "extract_exact_waveform",
                    tool="spiceowl_direct_read",
                    operation="waveform_query",
                    arguments_template={"run_id": run_id, "dataset": meas.get("dataset") or "<dataset>", "signals": list(exp.get("signals") or []), "mode": "exact"},
                    requires_human_approval=False,
                    reason="Fetch 100% lossless full-precision samples for measurement / adctools.",
                ),
            ],
            "forbidden_actions": ["measure_from_preview", "claim_scientific_pass"],
            "scientific_verdict": "measurement_unsafe_preview",
        }

    missing_sigs = meas.get("missing_signals") or []
    if missing_sigs:
        return {
            "schema": ORCHESTRATOR_SCHEMA,
            "stage": "report",
            "ready_to_advance": False,
            "complete": True,
            "halt_reason": "missing_required_signals",
            "missing": [
                _missing("measurement.required_signals", f"Collected dataset is missing required signal(s): {', '.join(str(s) for s in missing_sigs)}."),
            ],
            "permitted_actions": [
                _action(
                    "report_invalid_measurement",
                    tool="spiceowl_direct_read",
                    operation="report",
                    arguments_template={"run_id": run_id},
                    requires_human_approval=False,
                    reason="Report execution_completed with measurement_validity=invalid; never fabricate missing traces.",
                ),
            ],
            "forbidden_actions": ["claim_scientific_pass", "fabricate_signal"],
            "scientific_verdict": "measurement_invalid",
        }

    if not meas.get("evaluated"):
        meas_missing.append(_missing("measurement.evaluated", "Run declared lossless measurement algorithm on collected dataset."))
        return {
            "schema": ORCHESTRATOR_SCHEMA,
            "stage": "measure",
            "ready_to_advance": True,
            "complete": False,
            "halt_reason": None,
            "missing": meas_missing,
            "permitted_actions": [
                _action(
                    "run_declared_measurement",
                    tool="spiceowl_direct_read",
                    operation="waveform_query",
                    arguments_template={"run_id": run_id, "dataset": meas.get("dataset") or "<dataset>", "signals": list(exp.get("signals") or []), "mode": "exact"},
                    requires_human_approval=False,
                    reason="Extract exact lossless samples and evaluate declared criteria.",
                ),
            ],
            "forbidden_actions": ["claim_scientific_pass_before_measurement"],
            "scientific_verdict": "pending_measurement",
        }

    # 7. Stage: report (preserve five independent dimensions & valid spec failures!)
    perf = rep.get("functional_performance") or meas.get("functional_performance") or "unknown"
    mval = rep.get("measurement_validity") or meas.get("measurement_validity") or "valid"
    if mval != "valid":
        verdict = "measurement_invalid"
    elif perf == "fail":
        verdict = "valid_spec_failure"
    elif perf == "pass":
        verdict = "valid_spec_pass"
    else:
        verdict = "measured_not_evaluated"

    return {
        "schema": ORCHESTRATOR_SCHEMA,
        "stage": "report",
        "ready_to_advance": False,
        "complete": True,
        "halt_reason": None,
        "missing": [],
        "permitted_actions": [
            _action(
                "present_five_dim_report",
                tool="spiceowl_direct_read",
                operation="report",
                arguments_template={"run_id": run_id},
                requires_human_approval=False,
                reason="Present execution_status, measurement_validity, functional_performance, operating_qualification, and dependency_provenance separately.",
            ),
        ],
        "forbidden_actions": ["alter_criteria_after_result", "auto_retry_valid_spec_failure"],
        "scientific_verdict": verdict,
    }

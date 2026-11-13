"""Observe-only policy. An allowed suggestion never enables dispatch."""

from spiceowl.agent.diagnosis import build_evidence, validate_proposal
from spiceowl.domain.common import new_id


def evaluate_policy(proposal, run):
    """Validate against the current run, then issue a non-executable decision.

    Call before service-owned producer/session metadata is attached. This does
    not trust a caller to have previously validated either target or references.
    """
    evidence = build_evidence(run)
    candidate = validate_proposal(proposal, evidence)
    kind = candidate["kind"]
    valid_miss = (
        evidence["measurement_validity"] == "valid"
        and evidence["specification_result"] == "fail"
    )
    if valid_miss and kind != "report":
        result = "deny"
        reason = "A valid design-target miss is report-only; recovery must preserve the result."
    elif kind in {"report", "observe", "reconcile"}:
        result = "allow"
        reason = "Read-only synthetic suggestion; no simulation operation will be dispatched."
    elif evidence["observation"] in {"unknown", "stale"} or evidence["execution"] == "submitting":
        result = "deny"
        reason = "Execution is unresolved or observations are stale; reconcile before any dependent mutation."
    else:
        result = "deny"
        reason = "Observe-only mode denies all simulator mutations."
    return {
        "decision_id": new_id("decision"),
        "proposal_id": candidate["proposal_id"],
        "result": result,
        "reason": reason,
        "dispatch_enabled": False,
        "synthetic": True,
    }

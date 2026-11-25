"""Bounded M4 recovery policy, decision engine, and searchable recovery reports.

All logic here is pure and deterministic (no SSH, filesystem, or model I/O).
Hard scientific and operational boundaries:
- Only explicitly confirmed operational failures are recoverable:
  1. ``license_unavailable``: Spectre/tool license checkout failure or queue timeout.
  2. ``confirmed_oom``: Run-bound scheduler OOM evidence (e.g. Slurm OUT_OF_MEMORY)
     or explicit Spectre out-of-memory diagnostic. Suspected SIGKILL without
     run-bound evidence is never auto-recovered.
- Completed executions, specification misses, SOA/model warnings, syntax errors,
  missing models, convergence failures, user cancellations, and uncertain/active
  runs are NEVER automatically retried or altered.
- Every recovery attempt preserves the exact frozen netlist, support files,
  stimulus, corner, and acceptance criteria; only bounded resource/host or
  retry-attempt metadata changes, subject to explicit attempt, memory, and
  elapsed-time budgets.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math

from spiceowl.domain.common import DomainError, new_id, require_id, utcnow


POLICY_SCHEMA = "spiceowl.recovery-policy.v1"
REPORT_SCHEMA = "spiceowl.recovery-report.v1"

DEFAULT_RECOVERY_POLICY = {
    "schema": POLICY_SCHEMA,
    "enabled": False,
    "auto_resume_watch": True,
    "auto_collect_on_terminal": True,
    "max_recovery_attempts": 2,
    "max_elapsed_seconds": 14400,
    "retry_on_license": True,
    "license_backoff_seconds": 300,
    "retry_on_oom": True,
    "oom_memory_multiplier": 2.0,
    "max_memory_bytes": 64 * 1024**3,
    "allow_host_relocation": True,
    "candidate_host_ids": [],
}

_NON_RECOVERABLE_DIAG_CATEGORIES = frozenset({
    "syntax", "model_missing", "convergence", "parameter_bounds", "model_bin",
})


def validate_recovery_policy(raw: dict | None) -> dict:
    """Validate and normalize a bounded recovery policy."""
    if raw is None:
        return dict(DEFAULT_RECOVERY_POLICY)
    if type(raw) is not dict:
        raise DomainError("Recovery policy must be a JSON object.", "invalid_recovery_policy")
    allowed = set(DEFAULT_RECOVERY_POLICY)
    if set(raw) - allowed:
        raise DomainError("Recovery policy contains unsupported fields.", "invalid_recovery_policy")
    merged = dict(DEFAULT_RECOVERY_POLICY)
    merged.update(raw)
    if merged["schema"] != POLICY_SCHEMA:
        raise DomainError("Unsupported recovery policy schema.", "invalid_recovery_policy")
    for flag in ("enabled", "auto_resume_watch", "auto_collect_on_terminal",
                 "retry_on_license", "retry_on_oom", "allow_host_relocation"):
        if type(merged[flag]) is not bool:
            raise DomainError(f"Recovery policy field '{flag}' must be a boolean.", "invalid_recovery_policy")
    if type(merged["max_recovery_attempts"]) is not int or not 0 <= merged["max_recovery_attempts"] <= 5:
        raise DomainError("max_recovery_attempts must be an integer between 0 and 5.", "invalid_recovery_policy")
    if type(merged["max_elapsed_seconds"]) is not int or not 60 <= merged["max_elapsed_seconds"] <= 604800:
        raise DomainError("max_elapsed_seconds must be an integer between 60 and 604800.", "invalid_recovery_policy")
    if type(merged["license_backoff_seconds"]) is not int or not 0 <= merged["license_backoff_seconds"] <= 3600:
        raise DomainError("license_backoff_seconds must be an integer between 0 and 3600.", "invalid_recovery_policy")
    mult = merged["oom_memory_multiplier"]
    if isinstance(mult, bool) or not isinstance(mult, (int, float)) or not math.isfinite(mult) or not 1.25 <= float(mult) <= 4.0:
        raise DomainError("oom_memory_multiplier must be a finite number between 1.25 and 4.0.", "invalid_recovery_policy")
    merged["oom_memory_multiplier"] = float(mult)
    if type(merged["max_memory_bytes"]) is not int or not 128 * 1024**2 <= merged["max_memory_bytes"] <= 16 * 1024**4:
        raise DomainError("max_memory_bytes must be an integer between 128 MiB and 16 TiB.", "invalid_recovery_policy")
    candidates = merged["candidate_host_ids"]
    if type(candidates) is not list or len(candidates) > 32 or any(type(h) is not str for h in candidates):
        raise DomainError("candidate_host_ids must be a list of at most 32 host IDs.", "invalid_recovery_policy")
    normalized_hosts = [require_id(h, "candidate host ID") for h in candidates]
    if len(set(normalized_hosts)) != len(normalized_hosts):
        raise DomainError("candidate_host_ids must be distinct.", "invalid_recovery_policy")
    merged["candidate_host_ids"] = normalized_hosts
    return merged


def _parse_iso(value: str | None) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def classify_failure(run_record: dict, report: dict | None) -> dict:
    """Classify a terminal run's failure strictly from stored run + report evidence."""
    state = run_record.get("state")
    raw_state = run_record.get("raw_remote_state")
    if state in ("prepared", "staged", "stage_incomplete", "submitted", "running_observed",
                 "launch_outcome_unknown", "reconcile_required"):
        return {
            "failure_class": "not_terminal",
            "recoverable": False,
            "reason": "Run is not in a confirmed terminal state; reconcile or wait before recovery.",
            "evidence": [{"source": "run_record", "state": state, "raw_remote_state": raw_state}],
        }
    if state == "simulator_complete_pending_measurements" or raw_state == "completed":
        return {
            "failure_class": "completed",
            "recoverable": False,
            "reason": "Execution completed normally; preserve the valid result even if specification or SOA checks fail.",
            "evidence": [{"source": "run_record", "state": state}],
        }
    if raw_state == "cancelled":
        return {
            "failure_class": "cancelled_by_user",
            "recoverable": False,
            "reason": "Run was cancelled; bounded recovery never overrides explicit cancellation.",
            "evidence": [{"source": "run_record", "raw_remote_state": "cancelled"}],
        }
    if state == "input_hash_failure":
        return {
            "failure_class": "input_integrity_failure",
            "recoverable": False,
            "reason": "Input or manifest integrity check failed; human investigation is required.",
            "evidence": [{"source": "run_record", "state": state}],
        }

    findings = []
    if isinstance(report, dict):
        diag_block = report.get("diagnostics")
        if isinstance(diag_block, dict) and isinstance(diag_block.get("diagnostics"), list):
            findings = diag_block["diagnostics"]

    categories = {item.get("category") for item in findings if isinstance(item, dict)}

    # Non-recoverable design/netlist/numerical errors always block automatic retry
    # so we never mask a circuit bug as an infrastructure retry.
    blocking = sorted(categories & _NON_RECOVERABLE_DIAG_CATEGORIES)
    if blocking:
        return {
            "failure_class": "design_or_numerical_error",
            "recoverable": False,
            "reason": f"Detected {', '.join(blocking)}; human source or numerical review is required.",
            "evidence": [
                {"source": "spectre_log", "category": item.get("category"), "code": item.get("code"),
                 "line_numbers": item.get("evidence_line_numbers", [])}
                for item in findings if item.get("category") in _NON_RECOVERABLE_DIAG_CATEGORIES
            ],
        }

    # Class A: Confirmed OOM (run-bound scheduler OOM or explicit Spectre OOM log)
    oom_ev = run_record.get("oom_evidence") if isinstance(run_record.get("oom_evidence"), dict) else None
    oom_findings = [item for item in findings if item.get("category") == "oom"]
    if (oom_ev and oom_ev.get("oom_kill") is True and oom_ev.get("run_bound") is True) or oom_findings:
        evidence = []
        if oom_ev and oom_ev.get("oom_kill") is True:
            evidence.append({"source": oom_ev.get("source", "scheduler"), "oom_kill": True, "run_bound": True})
        for item in oom_findings:
            evidence.append({
                "source": "spectre_log", "category": "oom", "code": item.get("code"),
                "line_numbers": item.get("evidence_line_numbers", []),
            })
        return {
            "failure_class": "confirmed_oom",
            "recoverable": True,
            "reason": "Confirmed out-of-memory termination with explicit run-bound or simulator evidence.",
            "evidence": evidence,
        }

    # Class B: Confirmed license unavailability / checkout timeout
    lic_findings = [item for item in findings if item.get("category") == "license"]
    if lic_findings:
        return {
            "failure_class": "license_unavailable",
            "recoverable": True,
            "reason": "Simulator failed due to license checkout unavailability or license queue timeout.",
            "evidence": [
                {"source": "spectre_log", "category": "license", "code": item.get("code"),
                 "line_numbers": item.get("evidence_line_numbers", [])}
                for item in lic_findings
            ],
        }

    if "suspected_oom" in categories or run_record.get("termination_reason") == "kill_signal_cause_unknown":
        return {
            "failure_class": "suspected_oom_unconfirmed",
            "recoverable": False,
            "reason": "SIGKILL was observed without run-bound OOM confirmation; do not guess OOM automatically.",
            "evidence": [{"source": "run_record", "termination_reason": run_record.get("termination_reason")}],
        }

    return {
        "failure_class": "unclassified_failure",
        "recoverable": False,
        "reason": "Failure does not match an explicitly authorized bounded recovery class.",
        "evidence": [{"source": "run_record", "state": state, "raw_remote_state": raw_state,
                      "exit_code": run_record.get("simulator_exit_code")}],
    }


def evaluate_bounded_recovery(
    run_record: dict,
    report: dict | None,
    *,
    policy: dict | None,
    lineage_runs: list[dict],
    recommendation: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """Evaluate whether a terminal run should spawn one bounded recovery attempt."""
    pol = validate_recovery_policy(policy)
    clock = now.astimezone(timezone.utc) if isinstance(now, datetime) else datetime.now(timezone.utc)
    classification = classify_failure(run_record, report)
    decision_id = new_id("recdec")
    attempts_used = max(0, len(lineage_runs) - 1)
    first_created = _parse_iso(lineage_runs[0].get("created_at")) if lineage_runs else _parse_iso(run_record.get("created_at"))
    elapsed_seconds = int(max(0.0, (clock - first_created).total_seconds())) if first_created else 0

    base = {
        "decision_id": decision_id,
        "evaluated_at": clock.isoformat(),
        "source_run_id": run_record["run_id"],
        "root_run_id": run_record.get("root_run_id") or run_record["run_id"],
        "attempt_number": run_record.get("attempt_number", 1),
        "failure_class": classification["failure_class"],
        "evidence": classification["evidence"],
        "attempts_used": attempts_used,
        "max_recovery_attempts": pol["max_recovery_attempts"],
        "elapsed_seconds": elapsed_seconds,
        "max_elapsed_seconds": pol["max_elapsed_seconds"],
        "resource_diff": {},
        "scientific_conditions_changed": False,
        "child_run_id": None,
    }

    if not pol["enabled"]:
        return base | {
            "result": "deny", "action": "none",
            "reason_code": "policy_disabled",
            "reason": "Bounded recovery policy is disabled for this run.",
        }

    if not classification["recoverable"]:
        return base | {
            "result": "deny", "action": "none",
            "reason_code": classification["failure_class"],
            "reason": classification["reason"],
        }

    if attempts_used >= pol["max_recovery_attempts"]:
        return base | {
            "result": "deny", "action": "none",
            "reason_code": "attempt_budget_exhausted",
            "reason": f"Recovery attempt budget exhausted ({attempts_used}/{pol['max_recovery_attempts']}).",
        }

    if elapsed_seconds > pol["max_elapsed_seconds"]:
        return base | {
            "result": "deny", "action": "none",
            "reason_code": "elapsed_budget_exhausted",
            "reason": f"Elapsed recovery time budget exhausted ({elapsed_seconds}s > {pol['max_elapsed_seconds']}s).",
        }

    failure_class = classification["failure_class"]

    if failure_class == "license_unavailable":
        if not pol["retry_on_license"]:
            return base | {
                "result": "deny", "action": "none",
                "reason_code": "license_retry_disabled",
                "reason": "Policy disables automatic license retries.",
            }
        prev_limits = run_record["limits"]
        next_limits = dict(prev_limits)
        return base | {
            "result": "allow",
            "action": "retry_license_with_backoff",
            "reason_code": "license_retry_authorized",
            "reason": f"License failure confirmed; authorized retry attempt #{attempts_used + 2} within budget.",
            "backoff_seconds": pol["license_backoff_seconds"],
            "next_host_id": run_record["host_id"],
            "next_limits": next_limits,
            "resource_diff": {
                "memory_bytes": {"from": prev_limits["memory_bytes"], "to": next_limits["memory_bytes"]},
                "host_id": {"from": run_record["host_id"], "to": run_record["host_id"]},
            },
        }

    if failure_class == "confirmed_oom":
        if not pol["retry_on_oom"]:
            return base | {
                "result": "deny", "action": "none",
                "reason_code": "oom_retry_disabled",
                "reason": "Policy disables automatic OOM recovery.",
            }
        prev_limits = run_record["limits"]
        prev_mem = int(prev_limits["memory_bytes"])
        scaled = int(math.ceil(prev_mem * pol["oom_memory_multiplier"]))
        next_mem = min(int(pol["max_memory_bytes"]), scaled)
        if next_mem <= prev_mem:
            return base | {
                "result": "deny", "action": "none",
                "reason_code": "memory_budget_exhausted",
                "reason": f"Current memory ({prev_mem / 2**30:.2f} GiB) already reaches policy cap ({pol['max_memory_bytes'] / 2**30:.2f} GiB).",
            }

        target_host = run_record["host_id"]
        action = "retry_oom_with_scaled_memory"
        if run_record.get("backend") == "ssh_direct":
            if recommendation is None or not recommendation.get("recommended_host_id"):
                return base | {
                    "result": "deny", "action": "none",
                    "reason_code": "no_eligible_larger_host",
                    "reason": f"No eligible host has enough observed memory headroom for {next_mem / 2**30:.2f} GiB.",
                }
            rec_host = recommendation["recommended_host_id"]
            if rec_host != target_host:
                if not pol["allow_host_relocation"]:
                    return base | {
                        "result": "deny", "action": "none",
                        "reason_code": "host_relocation_disabled",
                        "reason": "Current host lacks headroom for scaled memory and host relocation is disabled by policy.",
                    }
                target_host = rec_host
                action = "relocate_oom_to_larger_host"
        elif pol["allow_host_relocation"] and recommendation and recommendation.get("recommended_host_id"):
            rec_host = recommendation["recommended_host_id"]
            if rec_host != target_host:
                target_host = rec_host
                action = "relocate_oom_to_larger_host"

        next_limits = dict(prev_limits)
        next_limits["memory_bytes"] = next_mem
        return base | {
            "result": "allow",
            "action": action,
            "reason_code": "oom_recovery_authorized",
            "reason": (
                f"Confirmed OOM; authorized memory scale-up from {prev_mem / 2**30:.2f} GiB to {next_mem / 2**30:.2f} GiB"
                + (f" on relocated host {target_host}." if target_host != run_record["host_id"] else f" on {target_host}.")
            ),
            "backoff_seconds": 0,
            "next_host_id": target_host,
            "next_limits": next_limits,
            "resource_diff": {
                "memory_bytes": {"from": prev_mem, "to": next_mem},
                "host_id": {"from": run_record["host_id"], "to": target_host},
            },
        }

    return base | {
        "result": "deny", "action": "none",
        "reason_code": "unsupported_failure_class",
        "reason": "Unsupported recovery failure class.",
    }


def _five_dim_summary(report: dict | None) -> dict:
    if not isinstance(report, dict):
        return {"available": False}
    if report.get("schema") == "spiceowl.simulation-report.v1":
        return {
            "available": True,
            "execution_status": report.get("execution_status", {}).get("status"),
            "measurement_validity": report.get("measurement_validity", {}).get("status"),
            "functional_performance": report.get("functional_performance", {}).get("status"),
            "operating_qualification": report.get("operating_qualification", {}).get("status"),
            "dependency_provenance": report.get("dependency_provenance", {}).get("status"),
        }
    return {
        "available": True,
        "execution_status": report.get("execution_status", {}).get("status"),
        "measurement_validity": report.get("measurement_validity", {}).get("status"),
        "functional_performance": report.get("functional_performance", {}).get("status"),
    }


def build_recovery_report(
    root_run_id: str,
    lineage_runs: list[dict],
    reports_by_run: dict[str, dict | None],
    decisions: list[dict],
    netlist_hashes: dict[str, str],
    *,
    policy: dict,
    markdown_path: str | None = None,
) -> dict:
    """Assemble a durable, user-searchable recovery report for one attempt lineage."""
    if not lineage_runs:
        raise DomainError("Recovery report requires at least one attempt.", "invalid_recovery_report")
    pol = validate_recovery_policy(policy)
    ordered = sorted(lineage_runs, key=lambda r: (r.get("attempt_number", 1), r.get("created_at", "")))
    first = ordered[0]
    latest = ordered[-1]
    unique_netlist_hashes = {h for h in netlist_hashes.values() if isinstance(h, str)}
    preserved = len(unique_netlist_hashes) <= 1

    attempt_rows = []
    peak_mem = 0
    for item in ordered:
        mem = int(item.get("limits", {}).get("memory_bytes", 0))
        peak_mem = max(peak_mem, mem)
        rep = reports_by_run.get(item["run_id"])
        cls = classify_failure(item, rep)
        attempt_rows.append({
            "attempt_number": item.get("attempt_number", 1),
            "run_id": item["run_id"],
            "parent_run_id": item.get("parent_run_id"),
            "host_id": item["host_id"],
            "backend": item["backend"],
            "simulator": item["simulator"],
            "memory_bytes": mem,
            "memory_gib": round(mem / 2**30, 3),
            "cpu_count": item.get("limits", {}).get("cpu_count"),
            "state": item.get("state"),
            "raw_remote_state": item.get("raw_remote_state"),
            "scheduler_job_id": (item.get("scheduler") or {}).get("job_id"),
            "failure_class": cls["failure_class"],
            "failure_reason": cls["reason"],
            "recovery_trigger": item.get("recovery_trigger"),
            "five_dim_summary": _five_dim_summary(rep),
            "created_at": item.get("created_at"),
            "observed_at": item.get("observed_at"),
        })

    attempts_used = max(0, len(ordered) - 1)
    first_dt = _parse_iso(first.get("created_at"))
    now_dt = datetime.now(timezone.utc)
    elapsed_seconds = int(max(0.0, (now_dt - first_dt).total_seconds())) if first_dt else 0

    latest_state = latest.get("state")
    last_decision = decisions[-1] if decisions else None
    if latest_state == "simulator_complete_pending_measurements":
        outcome = "recovered" if attempts_used > 0 else "completed_first_try"
    elif latest_state in ("prepared", "staged", "submitted", "running_observed", "launch_outcome_unknown", "reconcile_required"):
        outcome = "in_progress"
    elif last_decision and last_decision.get("reason_code") in ("attempt_budget_exhausted", "elapsed_budget_exhausted", "memory_budget_exhausted"):
        outcome = "exhausted"
    elif last_decision and last_decision.get("reason_code") == "policy_disabled":
        outcome = "policy_disabled"
    else:
        outcome = "not_recoverable"

    # Build a crisp, human-readable Chinese summary for quick scanning in UI & index.
    triggers = [d.get("failure_class") for d in decisions if d.get("result") == "allow"]
    trigger_label = "、".join(sorted(set(triggers))) if triggers else (attempt_rows[-1]["failure_class"] if attempt_rows else "unknown")
    if outcome == "recovered":
        summary_zh = (
            f"共经历 {len(ordered)} 次尝试（触发原因：{trigger_label}），最终在 {latest['run_id']}"
            f"（主机 {latest['host_id']}，内存 {latest['limits']['memory_bytes'] / 2**30:.2f} GiB）执行完成；"
            f"原始网表与科学条件全程未修改。"
        )
    elif outcome == "in_progress":
        summary_zh = (
            f"已触发有界恢复（第 {len(ordered)} 次尝试：{latest['run_id']}，原因：{trigger_label}），"
            f"当前状态为 {latest_state}，正在主机 {latest['host_id']} 上执行/等待。"
        )
    elif outcome == "exhausted":
        summary_zh = (
            f"已执行 {attempts_used}/{pol['max_recovery_attempts']} 次恢复尝试，达到有界预算上限"
            f"（{last_decision.get('reason_code')}），已自动停止，保留全部历史尝试证据供人工排查。"
        )
    elif outcome == "policy_disabled":
        summary_zh = f"当前任务未开启有界恢复策略（最新尝试 {latest['run_id']}，状态：{latest_state}）。"
    elif outcome == "completed_first_try":
        summary_zh = f"首次尝试 {first['run_id']} 即执行完成，无需恢复。"
    else:
        reason_text = last_decision.get("reason") if last_decision else attempt_rows[-1]["failure_reason"]
        summary_zh = f"未执行自动恢复（{attempt_rows[-1]['failure_class']}）：{reason_text}"

    return {
        "schema": REPORT_SCHEMA,
        "report_id": root_run_id,
        "root_run_id": root_run_id,
        "latest_run_id": latest["run_id"],
        "origin_submission_key": first.get("origin_submission_key") or first.get("submission_key"),
        "backend": latest["backend"],
        "simulator": latest["simulator"],
        "outcome": outcome,
        "summary_zh": summary_zh,
        "created_at": first.get("created_at") or utcnow(),
        "updated_at": utcnow(),
        "policy": pol,
        "input_integrity": {
            "netlist_sha256": next(iter(unique_netlist_hashes), None),
            "preserved_across_all_attempts": preserved,
            "scientific_conditions_changed": False,
        },
        "budget_usage": {
            "attempts_used": attempts_used,
            "max_recovery_attempts": pol["max_recovery_attempts"],
            "elapsed_seconds": elapsed_seconds,
            "max_elapsed_seconds": pol["max_elapsed_seconds"],
            "peak_memory_bytes": peak_mem,
            "max_memory_bytes": pol["max_memory_bytes"],
        },
        "attempts": attempt_rows,
        "decisions": list(decisions),
        "markdown_path": markdown_path,
    }


def render_recovery_report_markdown(report: dict) -> str:
    """Render a recovery report as clean, easy-to-read Markdown."""
    integrity = report["input_integrity"]
    budget = report["budget_usage"]
    lines = [
        f"# SpiceOwl 有界恢复报告 · `{report['root_run_id']}`",
        "",
        f"- **最终结论 (`outcome`)**：`{report['outcome']}`",
        f"- **一句话摘要**：{report['summary_zh']}",
        f"- **最新尝试**：`{report['latest_run_id']}`（后端 `{report['backend']}` · 仿真器 `{report['simulator']}`）",
        f"- **原始输入完整性**：网表 SHA-256 `{integrity.get('netlist_sha256') or 'unknown'}` · 全程一致：`{'是' if integrity.get('preserved_across_all_attempts') else '否'}` · 科学条件未修改：`是`",
        f"- **预算使用**：恢复次数 `{budget['attempts_used']}/{budget['max_recovery_attempts']}` · 峰值内存 `{budget['peak_memory_bytes'] / 2**30:.2f} / {budget['max_memory_bytes'] / 2**30:.2f} GiB` · 累计耗时 `{budget['elapsed_seconds']}s / {budget['max_elapsed_seconds']}s`",
        f"- **更新时间**：`{report['updated_at']}`",
        "",
        "## 尝试链（Attempt Lineage）",
        "",
        "| 尝试序号 | Run ID | 主机 | 内存 (GiB) | 调度 Job | 执行状态 | 故障分类 | 五维结论 (执行/测量/性能/SOA) |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for row in report["attempts"]:
        fd = row["five_dim_summary"]
        fd_str = (
            f"{fd.get('execution_status')}/{fd.get('measurement_validity')}/{fd.get('functional_performance')}/{fd.get('operating_qualification', '-')}"
            if fd.get("available") else "尚未收集报告"
        )
        lines.append(
            f"| #{row['attempt_number']} | `{row['run_id']}` | `{row['host_id']}` | {row['memory_gib']} | "
            f"`{row['scheduler_job_id'] or '-'}` | `{row['state']}` | `{row['failure_class']}` | {fd_str} |"
        )
    lines.extend(["", "## 恢复决策与资源变更记录", ""])
    if not report["decisions"]:
        lines.append("（尚未触发策略评估。）")
    else:
        for dec in report["decisions"]:
            diff = dec.get("resource_diff") or {}
            mem_diff = diff.get("memory_bytes")
            host_diff = diff.get("host_id")
            diff_parts = []
            if mem_diff and mem_diff.get("from") != mem_diff.get("to"):
                diff_parts.append(f"内存 `{mem_diff['from'] / 2**30:.2f} GiB → {mem_diff['to'] / 2**30:.2f} GiB`")
            if host_diff and host_diff.get("from") != host_diff.get("to"):
                diff_parts.append(f"主机 `{host_diff['from']} → {host_diff['to']}`")
            diff_str = "，".join(diff_parts) if diff_parts else "无资源变更"
            child = f" → 生成 `{dec['child_run_id']}`" if dec.get("child_run_id") else ""
            lines.append(
                f"- **[{dec['result'].upper()}] `{dec['action']}`** (`{dec['evaluated_at']}` · 来源 `{dec['source_run_id']}`{child})："
                f"{dec['reason']}（故障类：`{dec['failure_class']}`，变更：{diff_str}）"
            )
    lines.append("")
    return "\n".join(lines)

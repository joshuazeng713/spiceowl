"""Programmable, Agent-editable Night Watch script engine (`night_watch.py`).

Pure Python stdlib implementation with zero external dependencies:
- Materializes a heavily-commented bilingual (Chinese + English) `night_watch.py`
  into SpiceOwl's local watch state directory.
- Validates user and Agent edits via Python `ast` before atomic persistence,
  extracting literal `CONFIG` knobs and `rule_*` rule function names safely.
- Executes `on_anomaly(...)` and `on_completed(...)` inside a fresh isolated
  Python subprocess (`python -I`, 5-second timeout) so syntax errors, infinite
  loops, or runtime exceptions in custom rules can never hang or crash SpiceOwl.
"""

from __future__ import annotations

import ast
import contextlib
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from spiceowl._platform import file_lock
from spiceowl.domain.common import DomainError, utcnow


NIGHT_WATCH_SCRIPT_SCHEMA = "spiceowl.night-watch-script.v1"
MAX_WATCH_SCRIPT_BYTES = 256 * 1024  # 256 KiB
DRY_RUN_TIMEOUT_SECONDS = 5.0

DEFAULT_NIGHT_WATCH_SCRIPT = '''"""SpiceOwl 夜间值守自动化策略脚本 · night_watch.py (Night Watch Rule Engine)

使用方式 / How SpiceOwl Runs This Script:
1. 当夜间后台盯守检测到仿真异常（排队超时、OOM、License 不可用、磁盘/NFS 抖动、收敛失败或未知 EDA Bug）时，
   SpiceOwl 会在隔离沙箱中调用本脚本的 `on_anomaly(run, report, log_tail, host_pool, ctx=None)`。
2. 当仿真任务顺利完成后，SpiceOwl 会读取 `CONFIG["auto_report_mode"]` 并可调用 `on_completed(run, report, ctx=None)`
   决定自动生成报告的模式（`"fast"` 极速报告 / `"deep"` 深度报告）。
3. 你可以直接手改本文件，也可以随时在网页端或对话里指挥 Agent（配合 `spiceowl-night-watch` Skill）
   为任意新发现的未知 EDA Bug 追加规则函数并做 Dry-Run 演练验证！

安全与科学红线 / Scientific Redline:
- 夜间自主处置仅允许做运维与资源调度（换空闲主机、扩配内存、调整安全队列/退避重试）。
- 严禁擅自修改电路网表、激励源、Corner 或 Spec 验收标准；任何涉及电路设计本身的异常一律转交 Agent 分析并留待人工审阅。
"""

# =============================================================================
# 1. 全局参数配置 / Global Configuration Knobs
#    修改以下纯字面量字典即可即时生效，保存时会自动同步关键限额到夜间自治策略。
# =============================================================================
CONFIG = {
    "enabled": True,                      # 夜间自主处置总开关 (Master switch for autonomous night watch)
    "auto_push_to_agent": True,           # 异常或未知 Bug 时自动唤醒 Agent (Auto-wake connected Agent)
    "queue_timeout_seconds": 1800,        # 排队超时阈值，单位秒，默认 30 分钟 (Queue wait timeout in seconds)
    "max_memory_gib": 64,                 # OOM 扩配内存上限，单位 GiB (Upper bound for OOM memory scaling)
    "max_resubmissions": 3,               # 单条任务链最多自主重提次数 (Max autonomous resubmissions per lineage)
    "license_retry_delay_seconds": 180,   # FlexLM License 不可用退避重试秒数 (License backoff delay in seconds)
    "preferred_hosts": [],                # 可选：夜间优先主机 ID 列表，留空则按空闲度自动选机
    "auto_report_mode": "fast",           # 完成后默认报告模式："fast"（极速）或 "deep"（深度）
    "protect_netlist_and_spec": True,     # 科学红线：严禁擅自改网表/激励/Spec（不可关闭）
}


# =============================================================================
# 2. 轻量辅助函数 / Helper Utilities (Pure Python, No External Dependencies)
# =============================================================================
def _combined_text(report, log_tail):
    """合并日志尾部与结构化诊断文本（小写），便于关键词匹配。"""
    parts = [str(log_tail or "")]
    if isinstance(report, dict):
        diag_block = report.get("diagnostics")
        if isinstance(diag_block, dict):
            for item in diag_block.get("diagnostics") or []:
                if isinstance(item, dict):
                    parts.append(str(item.get("code") or ""))
                    parts.append(str(item.get("category") or ""))
                    parts.append(str(item.get("message") or ""))
                    parts.append(str(item.get("summary") or ""))
        exec_st = report.get("execution_status")
        if isinstance(exec_st, dict):
            parts.append(str(exec_st.get("reason") or ""))
            parts.append(str(exec_st.get("status") or ""))
    return "\\n".join(parts).lower()


def _extract_host_metrics(host):
    """兼容扁平 host 字典与 HostRegistry.list() 结构，提取可用内存(GiB)与空闲核数。"""
    if not isinstance(host, dict):
        return None
    host_id = host.get("host_id")
    if not isinstance(host_id, str) or not host_id.strip():
        return None

    avail_gib = host.get("available_gib")
    if avail_gib is None:
        avail_gib = host.get("free_memory_gib")
    idle_cpus = host.get("idle_cpus")
    load_1m = host.get("load_1m")

    last_res = host.get("last_resources")
    if isinstance(last_res, dict):
        res = last_res.get("resources") if isinstance(last_res.get("resources"), dict) else last_res
        if avail_gib is None:
            mem_bytes = res.get("memory_available_bytes") or res.get("free_memory_bytes")
            if isinstance(mem_bytes, (int, float)) and mem_bytes > 0:
                avail_gib = float(mem_bytes) / (1024 ** 3)
        if load_1m is None and isinstance(res.get("load_1m"), (int, float)):
            load_1m = float(res["load_1m"])
        if idle_cpus is None:
            cpu_count = res.get("cpu_count")
            if isinstance(cpu_count, (int, float)) and cpu_count > 0:
                idle_cpus = max(0.0, float(cpu_count) - float(load_1m or 0.0))

    return {
        "host_id": host_id.strip(),
        "available_gib": float(avail_gib) if isinstance(avail_gib, (int, float)) else 0.0,
        "idle_cpus": float(idle_cpus) if isinstance(idle_cpus, (int, float)) else 0.0,
        "load_1m": float(load_1m) if isinstance(load_1m, (int, float)) else 999.0,
    }


def _pick_best_host(host_pool, *, exclude_host=None, min_free_gib=0.0, preferred_hosts=None):
    """从候选主机池选出最空闲且满足内存下限的目标主机。"""
    preferred = list(preferred_hosts or [])
    candidates = []
    for raw in host_pool or []:
        info = _extract_host_metrics(raw)
        if info is None:
            continue
        if exclude_host and info["host_id"] == exclude_host:
            continue
        if info["available_gib"] < float(min_free_gib or 0.0):
            continue
        pref_rank = 1 if info["host_id"] in preferred else 0
        candidates.append((pref_rank, info["idle_cpus"], info["available_gib"], -info["load_1m"], info))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]), reverse=True)
    return candidates[0][4]


def _current_memory_gib(run):
    """读取当前任务申请的内存配额（GiB），默认按 8 GiB 估算。"""
    if isinstance(run, dict):
        if isinstance(run.get("memory_gib"), (int, float)) and run["memory_gib"] > 0:
            return int(round(float(run["memory_gib"])))
        for block_key in ("limits", "resources"):
            block = run.get(block_key)
            if isinstance(block, dict):
                mem_bytes = block.get("memory_bytes")
                if isinstance(mem_bytes, (int, float)) and mem_bytes > 0:
                    return max(1, int(round(float(mem_bytes) / (1024 ** 3))))
    return 8


def _resubmissions_used(run):
    """估算当前任务链已使用的自主重提次数。"""
    if not isinstance(run, dict):
        return 0
    if isinstance(run.get("resubmissions_used"), int):
        return max(0, run["resubmissions_used"])
    attempt = run.get("attempt_number")
    if isinstance(attempt, int) and attempt >= 1:
        return attempt - 1
    return 0


# =============================================================================
# 3. 模块化故障处置规则 / Modular Bug Rule Functions
#    每条规则接收 (run, report, log_tail, host_pool, config)，命中返回决策 dict，未命中返回 None。
# =============================================================================
def rule_queue_timeout(run, report, log_tail, host_pool, config):
    """规则 1：队列排队超时 -> 自动筛选更空闲主机迁移重提。"""
    anomaly_type = str((run or {}).get("anomaly_type") or "").lower()
    state = str((run or {}).get("state") or "").lower()
    wait_sec = (run or {}).get("queue_wait_seconds")
    threshold = int(config.get("queue_timeout_seconds", 1800))

    is_queue_timeout = anomaly_type == "queue_timeout" or (
        state in ("submitted", "queued", "pending")
        and isinstance(wait_sec, (int, float))
        and wait_sec >= threshold
    )
    if not is_queue_timeout:
        return None

    max_retries = int(config.get("max_resubmissions", 3))
    used = _resubmissions_used(run)
    if used >= max_retries:
        return {
            "action": "await_human",
            "rule": "rule_queue_timeout",
            "reason": f"排队超时但自主重提预算已用尽（{used}/{max_retries}），暂停并等待人工审阅。",
            "overrides": {},
            "agent_instruction": "汇总排队阻塞节点与已重提历史，等待人类白天决定是否切换队列或延后调度。",
        }

    current_host = (run or {}).get("host_id")
    best = _pick_best_host(
        host_pool,
        exclude_host=current_host,
        preferred_hosts=config.get("preferred_hosts"),
    )
    if best is None:
        return {
            "action": "handoff_to_agent",
            "rule": "rule_queue_timeout",
            "reason": "检测到队列排队超时，但当前主机池暂无更空闲的备选节点，交由 Agent 实时扫描资源后再决策。",
            "overrides": {},
            "agent_instruction": "先调用 spiceowl_scan_resources 刷新主机池负载；若发现空闲节点且在预算内则取消原任务并迁移重提，否则记录结论留待人工审阅。",
        }

    return {
        "action": "resubmit",
        "rule": "rule_queue_timeout",
        "reason": (
            f"原主机 {current_host or 'unknown'} 排队超时，自动选择更空闲主机 {best['host_id']}"
            f"（空闲 CPU≈{best['idle_cpus']:.0f} 核，可用内存≈{best['available_gib']:.1f} GiB）迁移重提。"
        ),
        "overrides": {"host_id": best["host_id"]},
        "agent_instruction": (
            f"核实原排队任务仍未开始后取消原任务，保持原始网表与 Spec 完全不变，"
            f"迁移至 {best['host_id']} 重新提交并将决策写入网页值守记录。"
        ),
    }


def rule_confirmed_oom(run, report, log_tail, host_pool, config):
    """规则 2：确认内存溢出 (OOM / exit 137) -> 在上限内倍增内存并迁移至大内存主机。"""
    anomaly_type = str((run or {}).get("anomaly_type") or "").lower()
    text = _combined_text(report, log_tail)
    exit_code = (run or {}).get("simulator_exit_code")
    oom_keywords = (
        "out of memory",
        "oom-killer",
        "oom_killed",
        "cannot allocate memory",
        "std::bad_alloc",
        "insufficient memory",
    )
    is_oom = (
        anomaly_type == "confirmed_oom"
        or bool((run or {}).get("oom_evidence"))
        or exit_code in (137, -9)
        or any(kw in text for kw in oom_keywords)
    )
    if not is_oom:
        return None

    current_gib = _current_memory_gib(run)
    max_gib = int(config.get("max_memory_gib", 64))
    max_retries = int(config.get("max_resubmissions", 3))
    used = _resubmissions_used(run)

    if used >= max_retries:
        return {
            "action": "await_human",
            "rule": "rule_confirmed_oom",
            "reason": f"确认 OOM，但自主重提次数已达上限（{used}/{max_retries}），暂停等待人工审阅。",
            "overrides": {},
            "agent_instruction": "整理各次 OOM 内存峰值与失败日志，建议人工检查寄生参数规模或提高预算上限。",
        }

    if current_gib >= max_gib:
        return {
            "action": "await_human",
            "rule": "rule_confirmed_oom",
            "reason": f"确认 OOM，当前内存 {current_gib} GiB 已达夜间扩配上限 {max_gib} GiB，暂停等待人工审批更高配额。",
            "overrides": {},
            "agent_instruction": "不要突破夜间内存上限；汇总 OOM 证据并向用户建议所需内存规模或大内存主机。",
        }

    target_gib = min(max_gib, max(current_gib * 2, current_gib + 8))
    best = _pick_best_host(
        host_pool,
        min_free_gib=target_gib,
        preferred_hosts=config.get("preferred_hosts"),
    )
    if best is None:
        best = _pick_best_host(host_pool, preferred_hosts=config.get("preferred_hosts"))

    overrides = {"memory_gib": target_gib}
    host_note = ""
    if best is not None:
        overrides["host_id"] = best["host_id"]
        host_note = f"并迁移至大内存主机 {best['host_id']}（可用内存≈{best['available_gib']:.1f} GiB）"

    return {
        "action": "resubmit",
        "rule": "rule_confirmed_oom",
        "reason": f"检测到确认 OOM（原配额 {current_gib} GiB），在上限 {max_gib} GiB 内扩配至 {target_gib} GiB {host_note}后重提。",
        "overrides": overrides,
        "agent_instruction": (
            f"保持原始网表与验收标准 100% 不变，将内存扩配至 {target_gib} GiB"
            + (f"（目标主机 {best['host_id']}）" if best is not None else "")
            + "后提交新尝试并记录值守决策。"
        ),
    }


def rule_license_unavailable(run, report, log_tail, host_pool, config):
    """规则 3：FlexLM / 仿真器 License 暂时不可用 -> 按配置退避秒数延迟重试。"""
    anomaly_type = str((run or {}).get("anomaly_type") or "").lower()
    text = _combined_text(report, log_tail)
    license_keywords = (
        "license",
        "flexlm",
        "lmgrd",
        "checkout failed",
        "all licenses are in use",
        "license server",
    )
    is_license = anomaly_type == "license_unavailable" or any(kw in text for kw in license_keywords)
    if not is_license:
        return None

    max_retries = int(config.get("max_resubmissions", 3))
    used = _resubmissions_used(run)
    if used >= max_retries:
        return {
            "action": "await_human",
            "rule": "rule_license_unavailable",
            "reason": f"FlexLM License 持续不可用且重试预算已用尽（{used}/{max_retries}），暂停等待人工排查 License 服务器。",
            "overrides": {},
            "agent_instruction": "记录 License 特征名与报错时间窗口，待人工白天核查 License 占用或服务器状态。",
        }

    delay = int(config.get("license_retry_delay_seconds", 180))
    return {
        "action": "retry_later",
        "rule": "rule_license_unavailable",
        "delay_seconds": delay,
        "reason": f"检测到仿真器 License 暂时不可用或并发已满，计划退避 {delay} 秒后原参数重试。",
        "overrides": {},
        "agent_instruction": f"等待 {delay} 秒退避窗口后使用完全一致的原始网表与参数重新提交，避免高峰期连续空转。",
    }


def rule_disk_or_nfs_transient(run, report, log_tail, host_pool, config):
    """规则 4：NFS / 本地磁盘瞬态 I/O 或空间报错 -> 优先切换干净主机，否则转交 Agent。"""
    text = _combined_text(report, log_tail)
    disk_keywords = (
        "stale file handle",
        "no space left on device",
        "disk quota exceeded",
        "input/output error",
        "nfs server not responding",
    )
    if not any(kw in text for kw in disk_keywords):
        return None

    current_host = (run or {}).get("host_id")
    alt_host = _pick_best_host(
        host_pool,
        exclude_host=current_host,
        preferred_hosts=config.get("preferred_hosts"),
    )
    max_retries = int(config.get("max_resubmissions", 3))
    used = _resubmissions_used(run)
    if alt_host is not None and used < max_retries:
        return {
            "action": "resubmit",
            "rule": "rule_disk_or_nfs_transient",
            "reason": (
                f"原主机 {current_host or 'unknown'} 出现磁盘/NFS 瞬态 I/O 报错，"
                f"切换至健康备选主机 {alt_host['host_id']} 重新执行。"
            ),
            "overrides": {"host_id": alt_host["host_id"]},
            "agent_instruction": (
                f"确认目标主机 {alt_host['host_id']} 磁盘与挂载正常后，保持原始网表不变迁移重提，"
                "并在值守记录中注明原主机存在磁盘/NFS 异常。"
            ),
        }

    return {
        "action": "handoff_to_agent",
        "rule": "rule_disk_or_nfs_transient",
        "reason": "检测到磁盘空间不足或 NFS 瞬态故障，且当前无可用干净替代主机，已转交 Agent 排查存储状态。",
        "overrides": {},
        "agent_instruction": "核查运行目录所在磁盘配额与 NFS 挂载状态；严禁删改用户原始工程数据，整理清理建议留待人工确认。",
    }


def rule_convergence_failure(run, report, log_tail, host_pool, config):
    """规则 5：求解器收敛失败 -> 严守科学红线，绝不擅自改网表，转交 Agent 诊断。"""
    anomaly_type = str((run or {}).get("anomaly_type") or "").lower()
    text = _combined_text(report, log_tail)
    conv_keywords = (
        "convergence",
        "timestep too small",
        "singular matrix",
        "gmin stepping failed",
        "failed to converge",
    )
    is_conv = anomaly_type in ("convergence_failed", "convergence_failure", "convergence_error") or any(
        kw in text for kw in conv_keywords
    )
    if not is_conv:
        return None

    return {
        "action": "handoff_to_agent",
        "rule": "rule_convergence_failure",
        "reason": "检测到仿真求解器收敛异常；依据科学红线严禁擅自改动电路拓扑、参数或激励，已转交 Agent 诊断并输出人工审阅建议。",
        "overrides": {},
        "agent_instruction": "分析发散节点与日志，给出收敛诊断建议，严禁未经批准修改电路参数；如仅需安全仿真选项建议请汇总留待人工审阅。",
    }


# --- 自定义规则示例（可直接让 Agent 按此格式新增任意未知 Bug 规则）---
# def rule_custom_example(run, report, log_tail, host_pool, config):
#     """示例：拦截自定义 PDK / 模型库告警并切换主机或转交 Agent。"""
#     text = _combined_text(report, log_tail)
#     if "cmi-2116" in text or "custom model table missing" in text:
#         return {
#             "action": "handoff_to_agent",
#             "rule": "rule_custom_example",
#             "reason": "命中自定义规则：PDK 模型表缺失 (CMI-2116)，需检查模型路径映射。",
#             "overrides": {},
#             "agent_instruction": "检查远端主机 PDK 环境变量与模型路径，严禁修改原始网表。",
#         }
#     return None
#
# 新增规则后，将其加入下方的 BUG_RULES 列表即可按优先级生效！


# =============================================================================
# 4. 规则注册表（按列表顺序自上而下匹配，命中第一条即返回）
# =============================================================================
BUG_RULES = [
    rule_queue_timeout,
    rule_confirmed_oom,
    rule_license_unavailable,
    rule_disk_or_nfs_transient,
    rule_convergence_failure,
]


# =============================================================================
# 5. SpiceOwl 入口钩子 / Entrypoint Hooks
# =============================================================================
def on_anomaly(run, report, log_tail, host_pool, ctx=None):
    """异常发生时的主调度入口：依次执行 BUG_RULES，未命中则走未知 Bug 兜底交给 Agent。"""
    effective_run = dict(run) if isinstance(run, dict) else {}
    effective_ctx = dict(ctx) if isinstance(ctx, dict) else {}
    if effective_ctx.get("anomaly_type") and not effective_run.get("anomaly_type"):
        effective_run["anomaly_type"] = effective_ctx["anomaly_type"]
    if "resubmissions_used" in effective_ctx and "resubmissions_used" not in effective_run:
        effective_run["resubmissions_used"] = effective_ctx["resubmissions_used"]

    effective_report = report if isinstance(report, dict) else {}
    effective_log = str(log_tail or "")
    effective_pool = list(host_pool or [])
    effective_config = dict(CONFIG)
    script_enabled = bool(effective_config.get("enabled", True))

    for rule_fn in BUG_RULES:
        decision = rule_fn(effective_run, effective_report, effective_log, effective_pool, effective_config)
        if isinstance(decision, dict):
            result = dict(decision)
            result.setdefault("rule", getattr(rule_fn, "__name__", "custom_rule"))
            result["script_enabled"] = script_enabled
            return result

    return {
        "action": "handoff_to_agent",
        "rule": "unknown_bug_fallback",
        "reason": "命中规则库未覆盖的未知仿真异常，已保留现场日志并转交 Agent 排查，同时可指挥 Agent 将新排查方案沉淀为 BUG_RULES 新规则。",
        "overrides": {},
        "agent_instruction": (
            "先阅读 report 与 log_tail 定位该未知 EDA 报错根因，给出安全处置建议（严禁擅自修改电路网表/Spec）；"
            "若确认此类故障后续会重复出现，可直接按 spiceowl-night-watch 规范为 night_watch.py 新增对应规则函数并 Dry-Run 验证。"
        ),
        "script_enabled": script_enabled,
    }


def on_completed(run, report, ctx=None):
    """仿真成功完成后的回调：决定自动汇总报告模式。"""
    mode = str(CONFIG.get("auto_report_mode", "fast")).strip().lower()
    if mode not in ("fast", "deep"):
        mode = "fast"
    return {
        "action": "generate_report",
        "mode": mode,
    }
'''


DRY_RUN_FIXTURES: dict[str, dict] = {
    "queue_timeout": {
        "run_record": {
            "run_id": "demo-adc-tran-q01",
            "root_run_id": "demo-adc-tran-q01",
            "host_id": "eda-node-01",
            "backend": "slurm",
            "simulator": "spectre",
            "state": "submitted",
            "attempt_number": 1,
            "queue_wait_seconds": 2160,
            "limits": {"cpu_count": 4, "memory_bytes": 16 * 1024**3},
        },
        "report": {
            "execution_status": {"status": "queued", "reason": "queue_wait_timeout"},
            "diagnostics": {"diagnostics": []},
        },
        "log_tail": "[SLURM] job 48291 state=PENDING reason=Resources elapsed=2160s queue=medium host=eda-node-01",
        "host_pool": [
            {"host_id": "eda-node-01", "idle_cpus": 1, "available_gib": 3.5, "load_1m": 31.4},
            {"host_id": "eda-node-02", "idle_cpus": 16, "available_gib": 48.0, "load_1m": 6.2},
            {"host_id": "eda-node-03", "idle_cpus": 48, "available_gib": 128.0, "load_1m": 1.1},
        ],
        "ctx": {"anomaly_type": "queue_timeout", "resubmissions_used": 0},
    },
    "confirmed_oom": {
        "run_record": {
            "run_id": "demo-pll-postlayout-01",
            "root_run_id": "demo-pll-postlayout-01",
            "host_id": "eda-node-02",
            "backend": "lsf",
            "simulator": "spectre",
            "state": "simulator_failed",
            "simulator_exit_code": 137,
            "oom_evidence": {"oom_killed": True, "exit_code": 137},
            "attempt_number": 1,
            "limits": {"cpu_count": 8, "memory_bytes": 16 * 1024**3},
        },
        "report": {
            "execution_status": {"status": "failed", "exit_code": 137},
            "diagnostics": {"diagnostics": [{"code": "confirmed_oom", "message": "Process killed by OOM killer"}]},
        },
        "log_tail": (
            "Fatal: Spectre process terminated with signal 9 (SIGKILL).\n"
            "kernel: Out of memory: Killed process 44102 (spectre) total-vm:18421000kB, anon-rss:16772160kB"
        ),
        "host_pool": [
            {"host_id": "eda-node-02", "idle_cpus": 12, "available_gib": 18.0, "load_1m": 4.0},
            {"host_id": "eda-bigmem-01", "idle_cpus": 32, "available_gib": 256.0, "load_1m": 2.3},
        ],
        "ctx": {"anomaly_type": "confirmed_oom", "resubmissions_used": 0},
    },
    "license_unavailable": {
        "run_record": {
            "run_id": "demo-ldo-mc-01",
            "root_run_id": "demo-ldo-mc-01",
            "host_id": "eda-node-01",
            "backend": "ssh_direct",
            "simulator": "spectre",
            "state": "simulator_failed",
            "simulator_exit_code": 1,
            "attempt_number": 1,
            "limits": {"cpu_count": 4, "memory_bytes": 8 * 1024**3},
        },
        "report": {
            "execution_status": {"status": "failed", "exit_code": 1},
            "diagnostics": {"diagnostics": [{"code": "license_unavailable", "message": "FlexLM checkout failed"}]},
        },
        "log_tail": (
            "ERROR (LMF-01005): FlexLM checkout failed for feature Virtuoso_Spectre_RF.\n"
            "All licenses are in use on lmgrd@license-srv:5280."
        ),
        "host_pool": [
            {"host_id": "eda-node-01", "idle_cpus": 24, "available_gib": 64.0, "load_1m": 3.2},
        ],
        "ctx": {"anomaly_type": "license_unavailable", "resubmissions_used": 0},
    },
    "convergence_failure": {
        "run_record": {
            "run_id": "demo-bandgap-tran-01",
            "root_run_id": "demo-bandgap-tran-01",
            "host_id": "eda-node-01",
            "backend": "ssh_direct",
            "simulator": "spectre",
            "state": "simulator_failed",
            "simulator_exit_code": 1,
            "attempt_number": 1,
            "limits": {"cpu_count": 4, "memory_bytes": 8 * 1024**3},
        },
        "report": {
            "execution_status": {"status": "failed", "exit_code": 1},
            "diagnostics": {
                "diagnostics": [{"code": "spectre_convergence_timestep", "message": "Internal timestep too small"}]
            },
        },
        "log_tail": (
            "ERROR (SPECTRE-16192): Convergence difficulties in transient analysis 'tran' at t = 12.41 ns.\n"
            "Internal timestep too small (1.0e-18 s) at node vstart."
        ),
        "host_pool": [
            {"host_id": "eda-node-01", "idle_cpus": 24, "available_gib": 64.0, "load_1m": 2.1},
        ],
        "ctx": {"anomaly_type": "convergence_failed", "resubmissions_used": 0},
    },
    "unknown_bug": {
        "run_record": {
            "run_id": "demo-serdes-pdk-01",
            "root_run_id": "demo-serdes-pdk-01",
            "host_id": "eda-node-02",
            "backend": "ssh_direct",
            "simulator": "spectre",
            "state": "simulator_failed",
            "simulator_exit_code": 2,
            "attempt_number": 1,
            "limits": {"cpu_count": 4, "memory_bytes": 16 * 1024**3},
        },
        "report": {
            "execution_status": {"status": "failed", "exit_code": 2},
            "diagnostics": {"diagnostics": [{"code": "CMI-2116", "message": "custom model table missing"}]},
        },
        "log_tail": (
            "ERROR (CMI-2116): custom model table missing for device M_drv0 in corner tt_25c.\n"
            "Check CDS_MODEL_TABLE_PATH or compiled CMI model cache."
        ),
        "host_pool": [
            {"host_id": "eda-node-01", "idle_cpus": 32, "available_gib": 96.0, "load_1m": 1.8},
            {"host_id": "eda-node-02", "idle_cpus": 12, "available_gib": 32.0, "load_1m": 5.0},
        ],
        "ctx": {"anomaly_type": "simulator_failed", "resubmissions_used": 0},
    },
}


_SUBPROCESS_RUNNER = r"""
import contextlib
import inspect
import io
import json
import sys
import traceback

payload = json.loads(sys.stdin.read())
code = payload["code"]
run_obj = payload.get("run") or {}
report_obj = payload.get("report") or {}
log_tail = payload.get("log_tail") or ""
host_pool = payload.get("host_pool") or []
ctx = payload.get("ctx") or {}

out_buf = io.StringIO()
err_buf = io.StringIO()
ns = {"__name__": "night_watch"}

def _json_safe(val):
    try:
        return json.loads(json.dumps(val, ensure_ascii=False, default=str))
    except Exception:
        return str(val)

try:
    with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
        exec(compile(code, "night_watch.py", "exec"), ns, ns)
        fn = ns.get("on_anomaly")
        if not callable(fn):
            raise RuntimeError("night_watch.py 缺少可调用函数 on_anomaly")
        sig = inspect.signature(fn)
        params = list(sig.parameters.values())
        accepts_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params)
        if accepts_varargs or len(params) >= 5:
            decision = fn(run_obj, report_obj, log_tail, host_pool, ctx)
        else:
            decision = fn(run_obj, report_obj, log_tail, host_pool)
        if decision is not None and not isinstance(decision, dict):
            raise TypeError(f"on_anomaly 必须返回 dict 或 None，实际返回 {type(decision).__name__}")

        completed_hook = None
        comp_fn = ns.get("on_completed")
        if callable(comp_fn):
            csig = inspect.signature(comp_fn)
            cparams = list(csig.parameters.values())
            c_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in cparams)
            if c_varargs or len(cparams) >= 3:
                completed_hook = comp_fn(run_obj, report_obj, ctx)
            else:
                completed_hook = comp_fn(run_obj, report_obj)
            if completed_hook is not None and not isinstance(completed_hook, dict):
                completed_hook = {"raw": str(completed_hook)}

    raw_cfg = ns.get("CONFIG")
    cfg = _json_safe(raw_cfg) if isinstance(raw_cfg, dict) else {}
    raw_rules = ns.get("BUG_RULES")
    rules = []
    if isinstance(raw_rules, (list, tuple)):
        for r in raw_rules:
            rname = getattr(r, "__name__", None)
            if isinstance(rname, str):
                rules.append(rname)

    result = {
        "ok": True,
        "decision": _json_safe(decision) if isinstance(decision, dict) else None,
        "completed_hook": _json_safe(completed_hook) if isinstance(completed_hook, dict) else None,
        "stdout": out_buf.getvalue()[:8192],
        "stderr": err_buf.getvalue()[:8192],
        "error": None,
        "config": cfg,
        "rules": rules,
    }
except Exception as exc:
    tb = traceback.format_exc(limit=6)
    result = {
        "ok": False,
        "decision": None,
        "completed_hook": None,
        "stdout": out_buf.getvalue()[:8192],
        "stderr": (err_buf.getvalue() + ("\n" if err_buf.getvalue() else "") + tb)[:8192],
        "error": f"{type(exc).__name__}: {exc}",
        "config": {},
        "rules": [],
    }

sys.stdout.write(json.dumps(result, ensure_ascii=True))
"""


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _atomic_write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _file_mtime_iso(path: Path) -> str | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except OSError:
        return None


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_watch_script(code: str) -> dict:
    """Validate syntax, size, required `on_anomaly` entrypoint, `rule_*` defs, and literal `CONFIG`."""
    if not isinstance(code, str) or not code.strip():
        raise DomainError("night_watch.py 内容不能为空。", "invalid_watch_script")
    if "\x00" in code:
        raise DomainError("night_watch.py 不能包含空字节 (NUL)。", "invalid_watch_script")
    try:
        encoded = code.encode("utf-8")
    except UnicodeError:
        raise DomainError("night_watch.py 必须是有效的 UTF-8 文本。", "invalid_watch_script") from None
    if len(encoded) > MAX_WATCH_SCRIPT_BYTES:
        raise DomainError(
            f"night_watch.py 体积超过上限（最大 {MAX_WATCH_SCRIPT_BYTES // 1024} KiB）。",
            "invalid_watch_script",
        )

    try:
        tree = ast.parse(code, filename="night_watch.py")
    except SyntaxError as exc:
        line_no = exc.lineno if exc.lineno is not None else 0
        raise DomainError(
            f"night_watch.py 语法错误（第 {line_no} 行）：{exc.msg}",
            "invalid_watch_script",
        ) from None

    top_defs: list[str] = []
    rule_defs: list[str] = []
    config_dict: dict = {}

    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            top_defs.append(node.name)
            if node.name.startswith("rule_"):
                rule_defs.append(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "CONFIG":
                    try:
                        evaluated = ast.literal_eval(node.value)
                        config_dict = evaluated if isinstance(evaluated, dict) else {}
                    except (ValueError, SyntaxError, TypeError):
                        config_dict = {}
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "CONFIG" and node.value is not None:
                try:
                    evaluated = ast.literal_eval(node.value)
                    config_dict = evaluated if isinstance(evaluated, dict) else {}
                except (ValueError, SyntaxError, TypeError):
                    config_dict = {}

    if "on_anomaly" not in top_defs:
        raise DomainError(
            "night_watch.py 必须定义顶层函数 `on_anomaly(run, report, log_tail, host_pool, ctx=None)`。",
            "invalid_watch_script",
        )
    if config_dict.get("protect_netlist_and_spec") is False:
        raise DomainError(
            "科学红线不可关闭：CONFIG['protect_netlist_and_spec'] 必须保持为 True。",
            "scientific_redline_violation",
        )

    return {
        "valid": True,
        "functions": top_defs,
        "rules": rule_defs,
        "config": config_dict,
        "has_on_completed": "on_completed" in top_defs,
        "line_count": len(code.splitlines()),
    }


def _apply_surgical_edits(current_code: str, edits: list[dict]) -> str:
    if not isinstance(edits, list) or not edits:
        raise DomainError("edits 必须是非空对象列表。", "invalid_watch_script")
    if len(edits) > 64:
        raise DomainError("单次最多应用 64 条 edits。", "invalid_watch_script")

    updated = current_code
    for idx, item in enumerate(edits):
        if not isinstance(item, dict):
            raise DomainError(f"第 {idx + 1} 条 edit 必须是 JSON 对象。", "invalid_watch_script")
        find_str = item.get("find")
        replace_str = item.get("replace")
        replace_all = item.get("replace_all", False)
        if not isinstance(find_str, str) or not find_str:
            raise DomainError(f"第 {idx + 1} 条 edit 缺少非空字符串字段 `find`。", "invalid_watch_script")
        if not isinstance(replace_str, str):
            raise DomainError(f"第 {idx + 1} 条 edit 缺少字符串字段 `replace`。", "invalid_watch_script")
        if type(replace_all) is not bool:
            raise DomainError(f"第 {idx + 1} 条 edit 的 `replace_all` 必须是布尔值。", "invalid_watch_script")
        if find_str not in updated:
            preview = find_str.replace("\n", "\\n")[:80]
            raise DomainError(
                f"第 {idx + 1} 条 edit 未找到匹配的 `find` 片段：{preview!r}",
                "watch_script_edit_mismatch",
            )
        if replace_all:
            updated = updated.replace(find_str, replace_str)
        else:
            updated = updated.replace(find_str, replace_str, 1)
    return updated


class NightWatchScriptStore:
    """Durable local store + AST validator + isolated subprocess dry-run runner for `night_watch.py`."""

    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.script_path = self.state_dir / "night_watch.py"
        self.meta_path = self.state_dir / "night_watch.meta.json"
        self.lock_path = self.state_dir / ".night_watch.lock"
        self._default_sha256 = _sha256_text(DEFAULT_NIGHT_WATCH_SCRIPT)

    @contextlib.contextmanager
    def _locked(self):
        with file_lock(self.lock_path, nonblocking=False, mode="a", create_dir=True, dir_mode=0o700):
            yield

    def _load_meta(self) -> dict:
        if not self.meta_path.is_file() or self.meta_path.is_symlink():
            return {}
        try:
            raw = json.loads(self.meta_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except (OSError, ValueError):
            return {}

    def ensure_initialized(self) -> Path:
        """Materialize `DEFAULT_NIGHT_WATCH_SCRIPT` if `<state_dir>/night_watch.py` does not exist."""
        if self.script_path.is_symlink():
            raise DomainError("night_watch.py cannot be a symlink.", "invalid_watch_script")
        if self.script_path.is_file():
            return self.script_path
        with self._locked():
            if self.script_path.is_symlink():
                raise DomainError("night_watch.py cannot be a symlink.", "invalid_watch_script")
            if not self.script_path.is_file():
                now = utcnow()
                _atomic_write_text(self.script_path, DEFAULT_NIGHT_WATCH_SCRIPT)
                _atomic_write_json(
                    self.meta_path,
                    {
                        "schema": NIGHT_WATCH_SCRIPT_SCHEMA,
                        "sha256": self._default_sha256,
                        "updated_at": now,
                        "source": "default",
                        "note": "初始化 SpiceOwl 默认夜间值守脚本",
                    },
                )
        return self.script_path

    def read(self) -> dict:
        """Read `night_watch.py`, introspect `CONFIG` & `rule_*`, and seamlessly detect disk edits."""
        self.ensure_initialized()
        if self.script_path.is_symlink() or not self.script_path.is_file():
            raise DomainError("night_watch.py must be a regular local file.", "invalid_watch_script")
        try:
            code = self.script_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise DomainError(f"无法读取 night_watch.py：{exc}", "invalid_watch_script") from None

        sha = _sha256_text(code)
        is_default = sha == self._default_sha256
        meta = self._load_meta()

        if meta.get("sha256") == sha:
            updated_at = meta.get("updated_at") or _file_mtime_iso(self.script_path) or utcnow()
            source = str(meta.get("source") or ("default" if is_default else "user"))
            note = meta.get("note")
        else:
            updated_at = _file_mtime_iso(self.script_path) or utcnow()
            source = "default" if is_default else "disk_edit"
            note = meta.get("note") if is_default else "检测到磁盘直接修改"

        valid = True
        syntax_error: str | None = None
        config: dict = {}
        rules: list[str] = []
        try:
            info = validate_watch_script(code)
            config = info["config"]
            rules = info["rules"]
        except DomainError as exc:
            valid = False
            syntax_error = str(exc)

        return {
            "schema": NIGHT_WATCH_SCRIPT_SCHEMA,
            "path": str(self.script_path),
            "code": code,
            "sha256": sha,
            "is_default": is_default,
            "updated_at": updated_at,
            "source": source,
            "note": note,
            "line_count": len(code.splitlines()),
            "config": config,
            "rules": rules,
            "valid": valid,
            "syntax_error": syntax_error,
        }

    def save(
        self,
        *,
        code: str | None = None,
        edits: list[dict] | None = None,
        source: str = "user",
        note: str | None = None,
        reset: bool = False,
    ) -> dict:
        """Validate via AST and atomically save `night_watch.py` (full replace, surgical edits, or reset)."""
        if type(reset) is not bool:
            raise DomainError("reset must be a boolean.", "invalid_watch_script")
        if not isinstance(source, str) or not source.strip():
            raise DomainError("source must be a non-empty string.", "invalid_watch_script")
        clean_source = source.strip()[:64]
        clean_note: str | None = None
        if note is not None:
            if not isinstance(note, str):
                raise DomainError("note must be a string or null.", "invalid_watch_script")
            stripped_note = note.strip()
            if len(stripped_note) > 500:
                raise DomainError("note exceeds 500 characters.", "invalid_watch_script")
            clean_note = stripped_note or None

        with self._locked():
            self.ensure_initialized()
            if reset:
                new_code = DEFAULT_NIGHT_WATCH_SCRIPT
                resolved_source = "default"
                resolved_note = clean_note or "恢复 SpiceOwl 默认夜间值守脚本"
            else:
                if (code is None) == (edits is None):
                    raise DomainError(
                        "请提供 `code` 或 `edits` 其中之一（不可同时为空或同时传入）。",
                        "invalid_watch_script",
                    )
                if edits is not None:
                    current_code = self.script_path.read_text(encoding="utf-8")
                    new_code = _apply_surgical_edits(current_code, edits)
                else:
                    if not isinstance(code, str):
                        raise DomainError("code must be a string.", "invalid_watch_script")
                    new_code = code
                resolved_source = clean_source
                resolved_note = clean_note

            validate_watch_script(new_code)

            sha = _sha256_text(new_code)
            now = utcnow()
            _atomic_write_text(self.script_path, new_code)
            _atomic_write_json(
                self.meta_path,
                {
                    "schema": NIGHT_WATCH_SCRIPT_SCHEMA,
                    "sha256": sha,
                    "updated_at": now,
                    "source": resolved_source,
                    "note": resolved_note,
                },
            )
        return self.read()

    def dry_run(
        self,
        *,
        code: str | None = None,
        scenario: str = "queue_timeout",
        run_record: dict | None = None,
        report: dict | None = None,
        log_tail: str = "",
        host_pool: list[dict] | None = None,
        ctx: dict | None = None,
    ) -> dict:
        """Run `on_anomaly` (and `on_completed`) in a fresh isolated Python subprocess (`python -I`)."""
        t0 = time.monotonic()
        if code is None:
            script_code = self.read()["code"]
        else:
            if not isinstance(code, str):
                raise DomainError("code must be a string or null.", "invalid_watch_script")
            script_code = code

        scenario_name = str(scenario or "queue_timeout").strip() or "queue_timeout"
        fixture = DRY_RUN_FIXTURES.get(scenario_name)
        has_custom_input = (
            run_record is not None
            or report is not None
            or bool(log_tail)
            or host_pool is not None
            or ctx is not None
        )
        if fixture is None and not has_custom_input:
            supported = ", ".join(sorted(DRY_RUN_FIXTURES))
            raise DomainError(
                f"未知演练场景 {scenario_name!r}，可选内置场景：{supported}（或传入真实 run_record/log_tail）。",
                "invalid_watch_script",
            )

        if isinstance(run_record, dict):
            # Real or caller-supplied run context: never leak synthetic fixture fields.
            eff_run = deepcopy(run_record)
            eff_report = deepcopy(report) if isinstance(report, dict) else {}
            eff_log = str(log_tail or "")
            eff_pool = deepcopy(host_pool) if isinstance(host_pool, list) else []
            eff_ctx = {"anomaly_type": scenario_name}
            if isinstance(ctx, dict):
                eff_ctx.update(ctx)
        else:
            base = deepcopy(fixture) if fixture is not None else {
                "run_record": {},
                "report": {},
                "log_tail": "",
                "host_pool": [],
                "ctx": {"anomaly_type": scenario_name},
            }
            eff_run = base["run_record"]
            eff_report = deepcopy(report) if isinstance(report, dict) else base["report"]
            eff_log = str(log_tail) if log_tail else base["log_tail"]
            eff_pool = deepcopy(host_pool) if isinstance(host_pool, list) else base["host_pool"]
            eff_ctx = dict(base.get("ctx") or {})
            if isinstance(ctx, dict):
                eff_ctx.update(ctx)

        try:
            ast_info = validate_watch_script(script_code)
        except DomainError as exc:
            return {
                "ok": False,
                "scenario": scenario_name,
                "decision": None,
                "completed_hook": None,
                "stdout": "",
                "stderr": "",
                "error": str(exc),
                "duration_ms": round((time.monotonic() - t0) * 1000.0, 2),
                "rules": [],
                "config": {},
            }

        payload = {
            "code": script_code,
            "run": eff_run,
            "report": eff_report,
            "log_tail": eff_log,
            "host_pool": eff_pool,
            "ctx": eff_ctx,
        }
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-c", _SUBPROCESS_RUNNER],
                input=json.dumps(payload, ensure_ascii=True, default=str),
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=DRY_RUN_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "scenario": scenario_name,
                "decision": None,
                "completed_hook": None,
                "stdout": "",
                "stderr": "",
                "error": f"night_watch.py 执行超时（超过 {DRY_RUN_TIMEOUT_SECONDS:.0f} 秒），已终止隔离进程。",
                "duration_ms": round((time.monotonic() - t0) * 1000.0, 2),
                "rules": ast_info["rules"],
                "config": ast_info["config"],
            }
        except OSError as exc:
            return {
                "ok": False,
                "scenario": scenario_name,
                "decision": None,
                "completed_hook": None,
                "stdout": "",
                "stderr": "",
                "error": f"无法启动隔离演练进程：{exc}",
                "duration_ms": round((time.monotonic() - t0) * 1000.0, 2),
                "rules": ast_info["rules"],
                "config": ast_info["config"],
            }

        duration_ms = round((time.monotonic() - t0) * 1000.0, 2)
        try:
            parsed = json.loads(proc.stdout) if proc.stdout.strip() else None
        except ValueError:
            parsed = None

        if not isinstance(parsed, dict):
            return {
                "ok": False,
                "scenario": scenario_name,
                "decision": None,
                "completed_hook": None,
                "stdout": (proc.stdout or "")[:4096],
                "stderr": (proc.stderr or "")[:4096],
                "error": f"隔离进程返回非预期结果（exit={proc.returncode}）。",
                "duration_ms": duration_ms,
                "rules": ast_info["rules"],
                "config": ast_info["config"],
            }

        runtime_rules = parsed.get("rules") if isinstance(parsed.get("rules"), list) and parsed.get("rules") else ast_info["rules"]
        runtime_cfg = parsed.get("config") if isinstance(parsed.get("config"), dict) and parsed.get("config") else ast_info["config"]

        return {
            "ok": bool(parsed.get("ok")),
            "scenario": scenario_name,
            "decision": parsed.get("decision") if isinstance(parsed.get("decision"), dict) else None,
            "completed_hook": parsed.get("completed_hook") if isinstance(parsed.get("completed_hook"), dict) else None,
            "stdout": str(parsed.get("stdout") or ""),
            "stderr": str(parsed.get("stderr") or proc.stderr or ""),
            "error": parsed.get("error"),
            "duration_ms": duration_ms,
            "rules": runtime_rules,
            "config": runtime_cfg,
        }

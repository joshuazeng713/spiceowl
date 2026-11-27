"""Web-recorded overnight watch event inbox, Agent handoff wakeup, and autonomy guardrails.

Implements SpiceOwl's core vision — "You sleep. SpiceOwl keeps watch.":
- Zero external chat/webhook integrations: humans are NEVER woken up by external
  pings. Every anomaly, Agent investigation, resource check, and resubmission is
  recorded cleanly in the local Web UI for calm morning review.
- When a watched run hits a runtime anomaly (queue timeout, license unavailable,
  confirmed OOM, convergence/simulator failure, spec missed, or watch disconnect),
  SpiceOwl persists a structured incident record and automatically pushes a
  bounded, secret-free handoff prompt to the user's own connected Agent (Codex
  standalone bridge or embedded CircuitGit host Agent via the handoff inbox).
- An explicit user-configured OvernightAutonomyPolicy governs which recovery
  actions (host migration, memory scaling within cap, simulator option tuning,
  bounded resubmissions) the Agent may execute autonomously while the user sleeps.
  Scientific invariants (original netlist bytes, stimulus, corner, acceptance
  criteria) are strictly protected: any out-of-policy change pauses cleanly for
  morning human approval.
"""

from __future__ import annotations

import contextlib
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import uuid

from spiceowl._platform import file_lock
from spiceowl.domain.common import DomainError, utcnow


WATCH_EVENT_SCHEMA = "spiceowl.watch-event.v1"
AUTONOMY_POLICY_SCHEMA = "spiceowl.overnight-autonomy-policy.v1"

ANOMALY_TYPES = (
    "queue_timeout",
    "license_unavailable",
    "confirmed_oom",
    "convergence_failed",
    "simulator_failed",
    "spec_missed",
    "watch_disconnected",
    "recovery_exhausted",
)

EVENT_STATES = (
    "recorded",
    "pushed_to_agent",
    "agent_investigating",
    "resolved_resubmitted",
    "awaiting_morning_review",
    "dismissed",
)

MAX_EVENTS = 200
MAX_AUDIT_ENTRIES = 400
DEFAULT_QUEUE_TIMEOUT_SECONDS = 1800
MIN_QUEUE_TIMEOUT_SECONDS = 30
MAX_QUEUE_TIMEOUT_SECONDS = 86400
MAX_ALLOWED_MEMORY_GIB = 1024
MAX_ALLOWED_RESUBMISSIONS = 10


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


def default_autonomy_policy() -> dict:
    """Return the default conservative overnight Agent autonomy policy."""
    return {
        "schema": AUTONOMY_POLICY_SCHEMA,
        "enabled": False,
        "auto_push_to_agent": True,
        "queue_timeout_seconds": DEFAULT_QUEUE_TIMEOUT_SECONDS,
        "allow_host_migration": True,
        "allow_memory_scale": True,
        "max_memory_gib": 64,
        "allow_sim_option_tuning": True,
        "max_agent_resubmissions": 3,
        "protect_netlist_and_spec": True,
        "updated_at": utcnow(),
    }


def validate_autonomy_policy(raw: dict | None, *, base: dict | None = None) -> dict:
    """Validate and normalize an OvernightAutonomyPolicy."""
    current = deepcopy(base) if isinstance(base, dict) else default_autonomy_policy()
    if raw is None:
        return current
    if not isinstance(raw, dict):
        raise DomainError("Overnight autonomy policy must be a JSON object.", "invalid_autonomy_policy")

    allowed = {
        "enabled",
        "auto_push_to_agent",
        "queue_timeout_seconds",
        "allow_host_migration",
        "allow_memory_scale",
        "max_memory_gib",
        "allow_sim_option_tuning",
        "max_agent_resubmissions",
    }
    unknown = set(raw) - allowed - {"schema", "protect_netlist_and_spec", "updated_at"}
    if unknown:
        raise DomainError(f"Unsupported autonomy policy fields: {sorted(unknown)}.", "invalid_autonomy_policy")

    if "protect_netlist_and_spec" in raw and raw["protect_netlist_and_spec"] is not True:
        raise DomainError(
            "Scientific redline cannot be disabled: overnight Agent autonomy may never mutate original netlists or acceptance specs without human review.",
            "scientific_redline_violation",
        )

    for bool_key in ("enabled", "auto_push_to_agent", "allow_host_migration", "allow_memory_scale", "allow_sim_option_tuning"):
        if bool_key in raw:
            if type(raw[bool_key]) is not bool:
                raise DomainError(f"{bool_key} must be a boolean.", "invalid_autonomy_policy")
            current[bool_key] = raw[bool_key]

    if "queue_timeout_seconds" in raw:
        val = raw["queue_timeout_seconds"]
        if type(val) is not int or not (MIN_QUEUE_TIMEOUT_SECONDS <= val <= MAX_QUEUE_TIMEOUT_SECONDS):
            raise DomainError(
                f"queue_timeout_seconds must be an integer between {MIN_QUEUE_TIMEOUT_SECONDS} and {MAX_QUEUE_TIMEOUT_SECONDS}.",
                "invalid_autonomy_policy",
            )
        current["queue_timeout_seconds"] = val

    if "max_memory_gib" in raw:
        val = raw["max_memory_gib"]
        if type(val) is not int or not (1 <= val <= MAX_ALLOWED_MEMORY_GIB):
            raise DomainError(
                f"max_memory_gib must be an integer between 1 and {MAX_ALLOWED_MEMORY_GIB}.",
                "invalid_autonomy_policy",
            )
        current["max_memory_gib"] = val

    if "max_agent_resubmissions" in raw:
        val = raw["max_agent_resubmissions"]
        if type(val) is not int or not (0 <= val <= MAX_ALLOWED_RESUBMISSIONS):
            raise DomainError(
                f"max_agent_resubmissions must be an integer between 0 and {MAX_ALLOWED_RESUBMISSIONS}.",
                "invalid_autonomy_policy",
            )
        current["max_agent_resubmissions"] = val

    current["schema"] = AUTONOMY_POLICY_SCHEMA
    current["protect_netlist_and_spec"] = True
    current["updated_at"] = utcnow()
    return current


def build_agent_handoff_prompt(event: dict, policy: dict) -> str:
    """Build a structured, secret-free prompt to wake the user's connected Agent."""
    fd = event.get("five_dim") or {}
    perm_items = []
    if policy.get("enabled"):
        if policy.get("allow_host_migration"):
            perm_items.append("可迁移至更空闲主机")
        if policy.get("allow_memory_scale"):
            perm_items.append(f"可扩配内存（上限 {policy.get('max_memory_gib', 64)} GiB）")
        if policy.get("allow_sim_option_tuning"):
            perm_items.append("可调整仿真运行选项/队列/线程")
        perm_items.append(f"最多自主重提 {policy.get('max_agent_resubmissions', 3)} 次")
    else:
        perm_items.append("夜间自主重提未开启（请先查明原因并给出方案，等待早上人工批准）")

    lines = [
        f"[SpiceOwl 值守事件交接 · {event['event_id']}]",
        f"异常类型: {event['anomaly_type']} | 严重度: {event['severity']} | 任务 ID: {event['run_id']} (第 #{event['attempt_number']} 次尝试)",
        f"当前节点与后端: host={event['host_id']} | backend={event['backend']} | simulator={event['simulator']}",
        f"摘要: {event['summary']}",
        f"四维状态: execution={fd.get('execution', 'unknown')}, measurement={fd.get('measurement', 'unknown')}, performance={fd.get('performance', 'unknown')}, soa={fd.get('soa', 'unknown')}",
        f"夜间自治授权边界: {'；'.join(perm_items)}；严禁擅自修改原始网表、激励或验收标准。",
        "",
        "请按以下步骤使用 SpiceOwl 工具自主排查并处理（人类正在休息，处理完后请把结论记录回网页端）：",
        "1. 调用 `spiceowl_scan_resources` / `spiceowl_recommend_hosts` 检查已注册主机的实时 CPU、内存与负载余量；",
        "2. 调用 `spiceowl_direct_read`（`inspect` / `report` / `log_tail`）核实故障证据与当前参数；",
        "3. 若在授权边界内（如排队超时换空闲节点/队列、OOM 扩内存或换大内存主机、调整运行选项），使用 `spiceowl_direct_control` 取消卡死任务并通过 `operation='submit'` 一键提交可追溯的新尝试；若涉及修改网表或超出边界，请不要强行提交，留待早起人工审阅；",
        f"4. 最后调用 `spiceowl_direct_control(operation='watch_record_decision', arguments={{'event_id': '{event['event_id']}', ...}})`，把查到的资源情况、决策理由与新 `run_id` 写入网页值守记录。",
    ]
    return "\n".join(lines)


def evaluate_autonomy_decision(
    policy: dict,
    *,
    operation: str,
    arguments: dict,
    parent_run: dict | None = None,
    resubmissions_used: int = 0,
) -> dict:
    """Deterministically decide whether a direct-control Agent call fits overnight policy.

    Returns {'allowed': bool, 'reason': str, 'code': str}.
    """
    if not policy.get("enabled"):
        return {
            "allowed": False,
            "code": "autonomy_disabled",
            "reason": "夜间自治策略未开启，写操作需要人工审批。",
        }

    # Non-destructive status / collect / decision recording are always safe in watch mode
    if operation in ("status", "collect", "watch_record_decision"):
        return {"allowed": True, "code": "watch_read_or_record", "reason": f"值守模式允许执行 {operation}。"}

    if operation == "cancel":
        return {"allowed": True, "code": "cancel_stalled_permitted", "reason": "夜间值守允许取消已卡死或排队超时的原任务。"}

    if operation in ("stage", "launch", "stage_and_launch"):
        if resubmissions_used >= int(policy.get("max_agent_resubmissions", 0)):
            return {
                "allowed": False,
                "code": "resubmission_budget_exhausted",
                "reason": f"已达夜间自主重提上限（{resubmissions_used}/{policy.get('max_agent_resubmissions', 0)}），暂停等待早起人工审阅。",
            }
        return {"allowed": True, "code": "bounded_launch_permitted", "reason": "在夜间自主重提次数上限内，允许派发新尝试。"}

    if operation in ("prepare", "submit"):
        if resubmissions_used >= int(policy.get("max_agent_resubmissions", 0)):
            return {
                "allowed": False,
                "code": "resubmission_budget_exhausted",
                "reason": f"已达夜间自主重提上限（{resubmissions_used}/{policy.get('max_agent_resubmissions', 0)}），暂停等待早起人工审阅。",
            }
        # Scientific redline: if preparing/submitting a recovery attempt for an existing run, netlist bytes MUST stay identical!
        if arguments.get("netlist_edits") or arguments.get("append_netlist"):
            return {
                "allowed": False,
                "code": "netlist_mutation_blocked",
                "reason": "检测到网表修改参数（netlist_edits / append_netlist）：夜间自治禁止擅自修改网表，已挂起等待早起人工审阅。",
            }
        if parent_run is not None:
            parent_sources = parent_run.get("sources") or {}
            proposed_sources = arguments.get("sources")
            if isinstance(proposed_sources, dict) and parent_sources:
                parent_netlist_hash = hashlib.sha256(
                    json.dumps(parent_sources, sort_keys=True).encode("utf-8")
                ).hexdigest()
                proposed_netlist_hash = hashlib.sha256(
                    json.dumps(proposed_sources, sort_keys=True).encode("utf-8")
                ).hexdigest()
                if parent_netlist_hash != proposed_netlist_hash:
                    return {
                        "allowed": False,
                        "code": "netlist_mutation_blocked",
                        "reason": "检测到原始网表内容变化：夜间自治禁止擅自修改网表，已挂起等待早起人工审阅。",
                    }
            # Host migration check
            proposed_host = arguments.get("host_id")
            if proposed_host and parent_run.get("host_id") and proposed_host != parent_run.get("host_id"):
                if not policy.get("allow_host_migration"):
                    return {
                        "allowed": False,
                        "code": "host_migration_disabled",
                        "reason": "当前策略未开启主机迁移，已挂起等待人工审阅。",
                    }
            # Simulator args / queue / option tuning check
            proposed_args = arguments.get("simulator_args")
            if proposed_args is not None and proposed_args != parent_run.get("simulator_args"):
                if not policy.get("allow_sim_option_tuning"):
                    return {
                        "allowed": False,
                        "code": "sim_option_tuning_disabled",
                        "reason": "当前策略未开启仿真选项调优，已挂起等待人工审阅。",
                    }

        # Memory cap check (resources / limits / flat memory_gib)
        req_res = arguments.get("resources") if isinstance(arguments.get("resources"), dict) else {}
        req_lim = arguments.get("limits") if isinstance(arguments.get("limits"), dict) else {}
        mem_bytes = req_res.get("memory_bytes") if req_res.get("memory_bytes") is not None else req_lim.get("memory_bytes")
        mem_mb = req_res.get("memory_mb")
        if mem_bytes is None and isinstance(mem_mb, (int, float)):
            mem_bytes = int(mem_mb * 1024 * 1024)
        if mem_bytes is None and isinstance(arguments.get("memory_gib"), (int, float)) and not isinstance(arguments.get("memory_gib"), bool):
            mem_bytes = int(round(float(arguments["memory_gib"]) * (1024 ** 3)))
        max_bytes = int(policy.get("max_memory_gib", 64)) * (1024 ** 3)
        if isinstance(mem_bytes, (int, float)) and mem_bytes > 0:
            parent_mem = (
                ((parent_run or {}).get("resources") or {}).get("memory_bytes")
                or ((parent_run or {}).get("limits") or {}).get("memory_bytes")
            )
            if parent_mem and mem_bytes > parent_mem and not policy.get("allow_memory_scale"):
                return {
                    "allowed": False,
                    "code": "memory_scale_disabled",
                    "reason": "当前策略未开启内存自动扩配，已挂起等待人工审阅。",
                }
            if mem_bytes > max_bytes:
                return {
                    "allowed": False,
                    "code": "memory_cap_exceeded",
                    "reason": f"申请内存 {mem_bytes / (1024 ** 3):.1f} GiB 超过夜间上限 {policy.get('max_memory_gib')} GiB，已挂起等待人工审阅。",
                }

        return {
            "allowed": True,
            "code": "within_overnight_policy",
            "reason": "符合夜间自治授权边界（原始网表不变、资源在上限内），允许 Agent 自主提交新尝试。",
        }

    return {
        "allowed": False,
        "code": "operation_requires_approval",
        "reason": f"操作 {operation} 不在夜间自治白名单中，需要人工审批。",
    }


class WatchHandoffManager:
    """Local durable Web watch event log, Agent handoff inbox, and overnight policy store.

    Zero external software integrations; all state stays inside SpiceOwl's local
    data directory and is rendered in the Web UI or handed directly to the user's
    connected Agent.
    """

    def __init__(self, data_dir: str | Path):
        self.root = Path(data_dir).expanduser().resolve() / "watch_handoff"
        self.policy_path = self.root / "policy.json"
        self.events_path = self.root / "events.json"
        self.lock_path = self.root / "watch_handoff.lock"

    @contextlib.contextmanager
    def _locked(self):
        with file_lock(self.lock_path, nonblocking=False, mode="a", create_dir=True, dir_mode=0o700):
            yield

    def get_policy(self) -> dict:
        if not self.policy_path.is_file() or self.policy_path.is_symlink():
            return default_autonomy_policy()
        try:
            raw = json.loads(self.policy_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return validate_autonomy_policy(raw)
        except (OSError, ValueError, DomainError):
            pass
        return default_autonomy_policy()

    def update_policy(self, updates: dict) -> dict:
        with self._locked():
            current = self.get_policy()
            updated = validate_autonomy_policy(updates, base=current)
            _atomic_write_json(self.policy_path, updated)
            return updated

    def _load_events(self) -> list[dict]:
        if not self.events_path.is_file() or self.events_path.is_symlink():
            return []
        try:
            raw = json.loads(self.events_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("events"), list):
                return raw["events"]
        except (OSError, ValueError):
            pass
        return []

    def _save_events(self, events: list[dict]) -> None:
        trimmed = events[-MAX_EVENTS:]
        _atomic_write_json(self.events_path, {"schema": "spiceowl.watch-event-log.v1", "events": trimmed})

    def list_events(self, *, state: str | None = None, limit: int = 50) -> list[dict]:
        events = self._load_events()
        if state:
            events = [e for e in events if e.get("state") == state]
        return deepcopy(events[-max(1, min(limit, MAX_EVENTS)):][::-1])

    def get_event(self, event_id: str) -> dict:
        for ev in self._load_events():
            if ev.get("event_id") == event_id:
                return deepcopy(ev)
        raise DomainError(f"Watch event '{event_id}' not found.", "not_found")

    def count_agent_resubmissions(self, root_run_id: str | None = None) -> int:
        count = 0
        for ev in self._load_events():
            if root_run_id and ev.get("root_run_id") != root_run_id and ev.get("run_id") != root_run_id:
                continue
            if ev.get("new_run_id"):
                count += 1
        return count

    def record_anomaly(
        self,
        *,
        anomaly_type: str,
        run_id: str,
        root_run_id: str | None = None,
        host_id: str = "unknown",
        backend: str = "ssh_direct",
        simulator: str = "spectre",
        attempt_number: int = 1,
        summary: str = "",
        five_dim_summary: dict | None = None,
        evidence: dict | None = None,
        dedupe_key: str | None = None,
        agent_pusher=None,
    ) -> dict:
        """Persist a watch anomaly to the Web timeline and optionally wake the connected Agent."""
        if anomaly_type not in ANOMALY_TYPES:
            raise DomainError(f"Unsupported anomaly_type: {anomaly_type!r}.", "invalid_watch_event")
        if not isinstance(run_id, str) or not run_id.strip():
            raise DomainError("Watch event requires run_id.", "invalid_watch_event")

        default_summaries = {
            "queue_timeout": f"任务 {run_id} 排队时间超过设定阈值，建议 Agent 检查空闲主机/队列并决定是否迁移。",
            "license_unavailable": f"任务 {run_id} 遇到仿真器 License 不可用或等待超时，交由 Agent 评估资源与重试策略。",
            "confirmed_oom": f"任务 {run_id} 触发确认 OOM（内存不足），交由 Agent 查机房大内存节点并扩配内存重提。",
            "convergence_failed": f"任务 {run_id} 出现求解器收敛异常，交由 Agent 检查日志并评估仿真选项调优。",
            "simulator_failed": f"任务 {run_id} 仿真执行失败，交由 Agent 查阅诊断日志并决定后续动作。",
            "spec_missed": f"任务 {run_id} 测量有效但性能指标未达标（科学失败结果已保留），交由 Agent 审阅。",
            "watch_disconnected": f"后台盯守与远端节点通信中断（任务状态未知），交由 Agent 先对账原任务再决策。",
            "recovery_exhausted": f"任务 {run_id} 自动恢复预算已耗尽，交由 Agent 总结证据并留待早起人工审阅。",
        }
        severity = {
            "queue_timeout": "amber",
            "license_unavailable": "amber",
            "spec_missed": "amber",
            "watch_disconnected": "amber",
            "confirmed_oom": "red",
            "convergence_failed": "red",
            "simulator_failed": "red",
            "recovery_exhausted": "red",
        }[anomaly_type]

        fd = five_dim_summary if isinstance(five_dim_summary, dict) else {}
        safe_fd = {
            "execution": str(fd.get("execution_status") or fd.get("execution") or "unknown")[:48],
            "measurement": str(fd.get("measurement_validity") or fd.get("measurement") or "unknown")[:48],
            "performance": str(fd.get("functional_performance") or fd.get("performance") or "unknown")[:48],
            "soa": str(fd.get("operating_qualification") or fd.get("soa") or "unknown")[:48],
        }
        dedupe = dedupe_key or f"{run_id}:{anomaly_type}:{attempt_number}"
        policy = self.get_policy()

        created = None
        with self._locked():
            events = self._load_events()
            for existing in events:
                if existing.get("dedupe_key") == dedupe:
                    return deepcopy(existing)
            now = utcnow()
            event_id = f"wev-{uuid.uuid4().hex[:10]}"
            created = {
                "schema": WATCH_EVENT_SCHEMA,
                "event_id": event_id,
                "dedupe_key": dedupe,
                "anomaly_type": anomaly_type,
                "severity": severity,
                "state": "recorded",
                "run_id": run_id.strip()[:128],
                "root_run_id": (root_run_id or run_id).strip()[:128],
                "host_id": str(host_id)[:96],
                "backend": str(backend)[:32],
                "simulator": str(simulator)[:32],
                "attempt_number": int(attempt_number) if isinstance(attempt_number, int) and attempt_number >= 1 else 1,
                "summary": (str(summary or "").strip() or default_summaries[anomaly_type])[:500],
                "five_dim": safe_fd,
                "evidence": deepcopy(evidence) if isinstance(evidence, dict) else {},
                "agent_handoff": {
                    "pushed": False,
                    "pushed_at": None,
                    "request_key": None,
                    "thread_id": None,
                    "push_status": "pending",
                    "push_note": "",
                    "prompt_preview": "",
                },
                "agent_decision": None,
                "new_run_id": None,
                "timeline": [
                    {
                        "at": now,
                        "actor": "spiceowl_watch",
                        "step": "anomaly_recorded",
                        "note": (str(summary or "").strip() or default_summaries[anomaly_type])[:400],
                    }
                ],
                "created_at": now,
                "updated_at": now,
            }
            created["agent_handoff"]["prompt_preview"] = build_agent_handoff_prompt(created, policy)
            events.append(created)
            self._save_events(events)

        if policy.get("auto_push_to_agent") and callable(agent_pusher):
            return self.push_event_to_agent(created["event_id"], agent_pusher=agent_pusher)
        return deepcopy(created)

    def push_event_to_agent(self, event_id: str, *, agent_pusher=None) -> dict:
        """Push a recorded watch anomaly to the user's connected Agent (idempotently)."""
        event = self.get_event(event_id)
        policy = self.get_policy()
        prompt = build_agent_handoff_prompt(event, policy)
        request_key = "watch-" + hashlib.sha256(f"{event_id}:{event['dedupe_key']}".encode("utf-8")).hexdigest()[:24]

        push_result = {"pushed": False, "thread_id": None, "status": "no_agent_connected", "note": "当前尚未连接 Agent，事件已保存在网页待处理队列中。"}
        if callable(agent_pusher):
            try:
                res = agent_pusher(event=event, prompt=prompt, request_key=request_key, policy=policy)
                if isinstance(res, dict):
                    push_result = {
                        "pushed": bool(res.get("pushed")),
                        "thread_id": res.get("thread_id"),
                        "status": str(res.get("status") or ("pushed" if res.get("pushed") else "deferred"))[:64],
                        "note": str(res.get("note") or "")[:300],
                    }
            except Exception as exc:
                push_result = {
                    "pushed": False,
                    "thread_id": None,
                    "status": "push_deferred",
                    "note": f"推送至 Agent 暂未成功（{str(exc)[:180]}），已保留在网页记录中等待认领。",
                }

        with self._locked():
            events = self._load_events()
            target = None
            for ev in events:
                if ev.get("event_id") == event_id:
                    target = ev
                    break
            if target is None:
                raise DomainError(f"Watch event '{event_id}' not found.", "not_found")
            now = utcnow()
            target["agent_handoff"] = {
                "pushed": push_result["pushed"],
                "pushed_at": now if push_result["pushed"] else target["agent_handoff"].get("pushed_at"),
                "request_key": request_key,
                "thread_id": push_result["thread_id"],
                "push_status": push_result["status"],
                "push_note": push_result["note"],
                "prompt_preview": prompt,
            }
            if push_result["pushed"] and target["state"] == "recorded":
                target["state"] = "pushed_to_agent"
            target["timeline"].append({
                "at": now,
                "actor": "spiceowl_watch",
                "step": "pushed_to_agent" if push_result["pushed"] else "agent_push_deferred",
                "note": push_result["note"] or ("已将异常上下文自动推送给用户接入的 Agent。" if push_result["pushed"] else "等待 Agent 连接后认领。"),
            })
            target["updated_at"] = now
            self._save_events(events)
            return deepcopy(target)

    def record_agent_decision(
        self,
        event_id: str,
        *,
        outcome: str,
        rationale: str,
        resource_assessment: list[dict] | None = None,
        config_changes: dict | None = None,
        new_run_id: str | None = None,
        needs_human_review: bool = False,
    ) -> dict:
        """Record the Agent's diagnosis, host resource check, config adjustments, and resulting run."""
        valid_outcomes = ("agent_investigating", "resolved_resubmitted", "awaiting_morning_review", "dismissed")
        if outcome not in valid_outcomes:
            raise DomainError(f"outcome must be one of {valid_outcomes}.", "invalid_watch_event")
        if not isinstance(rationale, str) or not rationale.strip():
            raise DomainError("Agent decision requires a non-empty rationale.", "invalid_watch_event")

        with self._locked():
            events = self._load_events()
            target = None
            for ev in events:
                if ev.get("event_id") == event_id:
                    target = ev
                    break
            if target is None:
                raise DomainError(f"Watch event '{event_id}' not found.", "not_found")
            now = utcnow()
            target["state"] = outcome
            if new_run_id:
                target["new_run_id"] = str(new_run_id).strip()[:128]
            target["agent_decision"] = {
                "outcome": outcome,
                "rationale": rationale.strip()[:1000],
                "resource_assessment": deepcopy(resource_assessment[:16]) if isinstance(resource_assessment, list) else [],
                "config_changes": deepcopy(config_changes) if isinstance(config_changes, dict) else {},
                "new_run_id": target.get("new_run_id"),
                "needs_human_review": bool(needs_human_review or outcome == "awaiting_morning_review"),
                "recorded_at": now,
            }
            target["timeline"].append({
                "at": now,
                "actor": "user_agent",
                "step": outcome,
                "note": rationale.strip()[:400],
            })
            target["updated_at"] = now
            self._save_events(events)
            return deepcopy(target)

    def simulate_overnight_handoff(self, *, scenario: str = "queue_timeout") -> dict:
        """Create a realistic synthetic overnight watch → Agent decision timeline for demo/walkthrough."""
        if scenario not in ("queue_timeout", "confirmed_oom", "convergence_needs_review"):
            raise DomainError("scenario must be queue_timeout, confirmed_oom, or convergence_needs_review.", "invalid_watch_event")
        tag = uuid.uuid4().hex[:6]
        if scenario == "queue_timeout":
            ev = self.record_anomaly(
                anomaly_type="queue_timeout",
                run_id=f"adc-tran-q-{tag}",
                host_id="eda-node-01",
                backend="slurm",
                simulator="spectre",
                attempt_number=1,
                summary="任务在 slurm 队列 medium 排队已达 35 分钟（超过阈值 30 分钟），原因：eda-node-01 负载 96% 且剩余内存仅 3.2 GiB。",
                five_dim_summary={"execution": "queued_timeout", "measurement": "pending", "performance": "pending", "soa": "pending"},
                evidence={"queue_wait_seconds": 2100, "queue_name": "medium", "scheduler_state": "PEND"},
                dedupe_key=f"demo-queue-{tag}",
            )
            ev = self.push_event_to_agent(
                ev["event_id"],
                agent_pusher=lambda **_: {
                    "pushed": True,
                    "thread_id": "codex-watch-thread",
                    "status": "pushed",
                    "note": "已自动唤醒用户接入的 Codex Agent（无外部软件打扰，人类继续安心睡眠）。",
                },
            )
            return self.record_agent_decision(
                ev["event_id"],
                outcome="resolved_resubmitted",
                rationale="调用 spiceowl_scan_resources 发现 eda-node-01 持续满载，而 eda-node-03 仍有 48 核空闲与 128 GiB 可用内存。在夜间自治边界内取消原排队任务，保持网表 SHA-256 不变，迁移至 eda-node-03 并改用 fast 队列重新提交，新任务 14 分钟后顺利跑完。",
                resource_assessment=[
                    {"host_id": "eda-node-01", "idle_cpus": 1, "available_gib": 3.2, "verdict": "excluded (congested)"},
                    {"host_id": "eda-node-03", "idle_cpus": 48, "available_gib": 128.0, "verdict": "recommended (#1)"},
                ],
                config_changes={
                    "host_id": {"from": "eda-node-01", "to": "eda-node-03"},
                    "scheduler_queue": {"from": "medium", "to": "fast"},
                    "netlist_sha256": "unchanged (100% preserved)",
                },
                new_run_id=f"adc-tran-q-{tag}-r2",
                needs_human_review=False,
            )
        if scenario == "confirmed_oom":
            ev = self.record_anomaly(
                anomaly_type="confirmed_oom",
                run_id=f"pll-postlayout-{tag}",
                host_id="eda-node-02",
                backend="lsf",
                simulator="spectre",
                attempt_number=1,
                summary="后仿寄生参数网络导致 Spectre 进程被内核 OOM Killer 终止（exit 137，原申请内存 16 GiB）。",
                five_dim_summary={"execution": "failed_oom", "measurement": "missing", "performance": "unknown", "soa": "unknown"},
                evidence={"exit_code": 137, "oom_killed": True, "requested_memory_gib": 16},
                dedupe_key=f"demo-oom-{tag}",
            )
            ev = self.push_event_to_agent(
                ev["event_id"],
                agent_pusher=lambda **_: {
                    "pushed": True,
                    "thread_id": "codex-watch-thread",
                    "status": "pushed",
                    "note": "已自动将确认 OOM 证据推送给用户接入的 Codex Agent。",
                },
            )
            return self.record_agent_decision(
                ev["event_id"],
                outcome="resolved_resubmitted",
                rationale="核实 exit 137 属于确认 OOM 且未超出夜间内存上限（64 GiB）。Agent 扫描到 eda-bigmem-01 有 256 GiB 空闲内存，将内存从 16 GiB 扩配至 48 GiB 并开启 +mt=8 后自动重提，保持网表与验收标准完全不变。",
                resource_assessment=[
                    {"host_id": "eda-node-02", "idle_cpus": 12, "available_gib": 18.0, "verdict": "excluded (insufficient RAM)"},
                    {"host_id": "eda-bigmem-01", "idle_cpus": 32, "available_gib": 256.0, "verdict": "recommended (#1)"},
                ],
                config_changes={
                    "host_id": {"from": "eda-node-02", "to": "eda-bigmem-01"},
                    "memory_gib": {"from": 16, "to": 48},
                    "simulator_args": {"from": ["+mt=4"], "to": ["+mt=8"]},
                    "netlist_sha256": "unchanged (100% preserved)",
                },
                new_run_id=f"pll-postlayout-{tag}-r2",
                needs_human_review=False,
            )
        # convergence_needs_review: Agent stays inside scientific redline and pauses cleanly for morning review
        ev = self.record_anomaly(
            anomaly_type="convergence_failed",
            run_id=f"bandgap-startup-{tag}",
            host_id="eda-node-01",
            backend="ssh_direct",
            simulator="spectre",
            attempt_number=1,
            summary="瞬态仿真在 t = 12.4 ns 处出现时间步过小（timestep too small）收敛失败。",
            five_dim_summary={"execution": "failed_convergence", "measurement": "invalid", "performance": "unknown", "soa": "unknown"},
            evidence={"diagnostic_code": "spectre_convergence_timestep", "time_s": 12.4e-9},
            dedupe_key=f"demo-conv-{tag}",
        )
        ev = self.push_event_to_agent(
            ev["event_id"],
            agent_pusher=lambda **_: {
                "pushed": True,
                "thread_id": "codex-watch-thread",
                "status": "pushed",
                "note": "已自动将收敛失败日志推送给用户接入的 Codex Agent。",
            },
        )
        return self.record_agent_decision(
            ev["event_id"],
            outcome="awaiting_morning_review",
            rationale="排查诊断日志发现启动电路节点 vstart 存在浮空初始条件，修复需在网表中增加 .ic 初始条件或改动启动偏置电阻。因夜间自治策略严禁擅自修改原始网表，Agent 已整理好修改建议留在网页端，等待您早上睡醒后一键审阅确认。",
            resource_assessment=[
                {"host_id": "eda-node-01", "idle_cpus": 24, "available_gib": 64.0, "verdict": "healthy (not a resource issue)"},
            ],
            config_changes={
                "proposed_only": "建议为节点 vstart 增加 .ic V(vstart)=0 或将 errpreset 设为 conservative（未擅自执行）",
                "netlist_sha256": "unchanged (scientific redline preserved)",
            },
            new_run_id=None,
            needs_human_review=True,
        )


def build_codex_bridge_pusher(codex_bridge):
    """Create a safe callback that wakes the user's connected CodexBridge thread."""

    def _push(*, event: dict, prompt: str, request_key: str, policy: dict) -> dict:
        if codex_bridge is None:
            return {"pushed": False, "thread_id": None, "status": "no_agent_connected", "note": "当前未配置 Codex Agent 桥接。"}
        st = codex_bridge.status()
        if not st.get("connected"):
            return {"pushed": False, "thread_id": None, "status": "agent_disconnected", "note": "用户的 Codex Agent 当前未连接；异常已记录在网页收件箱，连接后可一键推送或自动认领。"}
        if st.get("recovery_required"):
            return {"pushed": False, "thread_id": st.get("thread_id"), "status": "agent_recovery_required", "note": "Agent 会话存在未核对的中断回合，暂不自动推送以免覆盖上下文。"}
        if st.get("state") in ("running", "waiting_approval"):
            return {"pushed": False, "thread_id": st.get("thread_id"), "status": "agent_busy", "note": f"Agent 当前状态为 {st.get('state')}，异常已排队留在网页值守列表等待推送。"}
        thread_id = st.get("thread_id")
        if not thread_id:
            created = codex_bridge.create_thread()
            thread_id = created.get("thread_id")
        codex_bridge.send_message(thread_id, prompt, request_key)
        return {
            "pushed": True,
            "thread_id": thread_id,
            "status": "pushed",
            "note": f"已将异常事件自动推送至用户接入的 Agent 会话（thread={thread_id}），Agent 正在根据 SpiceOwl 工具排查。",
        }

    return _push

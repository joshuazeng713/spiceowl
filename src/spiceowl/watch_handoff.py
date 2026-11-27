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
from spiceowl.night_watch_script import NightWatchScriptStore


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
    ]
    script_decision = (event.get("evidence") or {}).get("script_decision") if isinstance(event.get("evidence"), dict) else None
    if isinstance(script_decision, dict):
        sd_action = script_decision.get("action") or "unknown"
        sd_rule = script_decision.get("rule") or "unknown"
        sd_reason = str(script_decision.get("reason") or "").strip()
        lines.append(f"夜间值守脚本预判 (night_watch.py): action={sd_action} | rule={sd_rule}" + (f" | {sd_reason}" if sd_reason else ""))
        sd_overrides = script_decision.get("overrides")
        if isinstance(sd_overrides, dict) and sd_overrides:
            lines.append(f"脚本建议参数覆盖: {json.dumps(sd_overrides, ensure_ascii=False)}")
        sd_delay = script_decision.get("delay_seconds")
        if isinstance(sd_delay, (int, float)) and not isinstance(sd_delay, bool) and sd_delay > 0:
            lines.append(f"脚本建议退避等待: {int(sd_delay)} 秒")
        sd_inst = str(script_decision.get("agent_instruction") or "").strip()
        if sd_inst:
            lines.append(f"脚本规则指引: {sd_inst}")
    lines.extend([
        "",
        "请按以下步骤使用 SpiceOwl 工具自主排查并处理（人类正在休息，处理完后请把结论记录回网页端）：",
        "1. 调用 `spiceowl_scan_resources` / `spiceowl_recommend_hosts` 检查已注册主机的实时 CPU、内存与负载余量；",
        "2. 调用 `spiceowl_direct_read`（`inspect` / `report` / `log_tail`）核实故障证据与当前参数；",
        "3. 若在授权边界内（如排队超时换空闲节点/队列、OOM 扩内存或换大内存主机、调整运行选项），使用 `spiceowl_direct_control` 取消卡死任务并通过 `operation='submit'` 一键提交可追溯的新尝试；若涉及修改网表或超出边界，请不要强行提交，留待早起人工审阅；",
        f"4. 最后调用 `spiceowl_direct_control(operation='watch_record_decision', arguments={{'event_id': '{event['event_id']}', ...}})`，把查到的资源情况、决策理由与新 `run_id` 写入网页值守记录。",
    ])
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
        self.state_dir = self.root
        self.policy_path = self.root / "policy.json"
        self.events_path = self.root / "events.json"
        self.lock_path = self.root / "watch_handoff.lock"
        self.script_store = NightWatchScriptStore(self.state_dir)

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

    def _sync_policy_from_script_config(self, cfg: dict | None) -> dict | None:
        if not isinstance(cfg, dict) or not cfg:
            return None
        policy_updates: dict = {}
        for bool_key in ("enabled", "auto_push_to_agent"):
            if bool_key in cfg and type(cfg[bool_key]) is bool:
                policy_updates[bool_key] = cfg[bool_key]
        q_val = cfg.get("queue_timeout_seconds")
        if type(q_val) is int and MIN_QUEUE_TIMEOUT_SECONDS <= q_val <= MAX_QUEUE_TIMEOUT_SECONDS:
            policy_updates["queue_timeout_seconds"] = q_val
        m_val = cfg.get("max_memory_gib")
        if type(m_val) is int and 1 <= m_val <= MAX_ALLOWED_MEMORY_GIB:
            policy_updates["max_memory_gib"] = m_val
        r_val = cfg.get("max_agent_resubmissions")
        if r_val is None:
            r_val = cfg.get("max_resubmissions")
        if type(r_val) is int and 0 <= r_val <= MAX_ALLOWED_RESUBMISSIONS:
            policy_updates["max_agent_resubmissions"] = r_val
        if policy_updates:
            return self.update_policy(policy_updates)
        return None

    def read_watch_script(self) -> dict:
        """Read the programmable `night_watch.py` script, parsed `CONFIG`, and `rule_*` inventory."""
        info = self.script_store.read()
        if not self.policy_path.is_file() and info.get("valid") is not False:
            self._sync_policy_from_script_config(info.get("config"))
        return info

    def save_watch_script(
        self,
        *,
        code: str | None = None,
        edits: list[dict] | None = None,
        source: str = "user",
        note: str | None = None,
        reset: bool = False,
    ) -> dict:
        """Validate & save `night_watch.py` and sync matching numeric/bool `CONFIG` fields to watch policy."""
        saved = self.script_store.save(code=code, edits=edits, source=source, note=note, reset=reset)
        self._sync_policy_from_script_config(saved.get("config"))
        return saved

    def update_watch_config(
        self,
        updates: dict,
        *,
        source: str = "user",
        note: str | None = None,
    ) -> dict:
        """Fast 1-call update for `night_watch.py` `CONFIG` switches/knobs with instant policy sync."""
        saved = self.script_store.update_config(updates, source=source, note=note)
        policy = self._sync_policy_from_script_config(saved.get("config")) or self.get_policy()
        return {
            "script": saved,
            "config": saved.get("config"),
            "policy": policy,
            "sha256": saved.get("sha256"),
            "updated_at": saved.get("updated_at"),
            "note": saved.get("note"),
        }

    def test_watch_script(
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
        """Dry-run `night_watch.py` in a fresh isolated subprocess against synthetic or real run evidence."""
        return self.script_store.dry_run(
            code=code,
            scenario=scenario,
            run_record=run_record,
            report=report,
            log_tail=log_tail,
            host_pool=host_pool,
            ctx=ctx,
        )


def build_watch_script_edit_prompt(
    instruction: str,
    *,
    script_info: dict,
    run_record: dict | None = None,
    report: dict | None = None,
    log_tail: str = "",
) -> str:
    """Build a structured Chinese prompt instructing the connected Agent to customize `night_watch.py`."""
    info = script_info if isinstance(script_info, dict) else {}
    script_path = str(info.get("path") or "night_watch.py")
    cfg = info.get("config") if isinstance(info.get("config"), dict) else {}
    rules = info.get("rules") if isinstance(info.get("rules"), list) else []

    lines = [
        "[SpiceOwl 夜间值守脚本定制 · night_watch.py]",
        f"用户定制需求: {str(instruction or '').strip()}",
        f"脚本路径: {script_path} (总行数: {info.get('line_count', '-')}, 语法有效: {info.get('valid', True)})",
        f"当前 CONFIG: {json.dumps(cfg, ensure_ascii=False)}",
        f"当前规则函数 (BUG_RULES): {', '.join(str(r) for r in rules) if rules else '(无)'}",
    ]

    if isinstance(run_record, dict) and run_record:
        rid = str(run_record.get("run_id") or "unknown")
        host_id = run_record.get("host_id") or "unknown"
        backend = run_record.get("backend") or "unknown"
        simulator = run_record.get("simulator") or "unknown"
        state = run_record.get("state") or "unknown"
        exit_code = run_record.get("simulator_exit_code")
        lines.append(
            f"关联真实故障任务: run_id={rid} | host={host_id} | backend={backend} | "
            f"simulator={simulator} | state={state} | exit_code={exit_code}"
        )

    if isinstance(report, dict) and report:
        diag_block = report.get("diagnostics") if isinstance(report.get("diagnostics"), dict) else {}
        diag_items = diag_block.get("diagnostics") if isinstance(diag_block.get("diagnostics"), list) else []
        diag_preview = []
        for item in diag_items[:6]:
            if isinstance(item, dict):
                code_str = item.get("code") or item.get("category") or "diag"
                msg_str = str(item.get("message") or item.get("summary") or "")[:140]
                diag_preview.append(f"{code_str}: {msg_str}" if msg_str else str(code_str))
        if diag_preview:
            lines.append("关联故障诊断摘要: " + " ; ".join(diag_preview))

    clean_log = str(log_tail or "").strip()
    if clean_log:
        lines.append("关联故障日志尾部 (log_tail):")
        lines.append("```text")
        lines.append(clean_log[-3000:])
        lines.append("```")

    lines.extend([
        "",
        "请按修改复杂度选择最快通道（严禁为简单开关浪费多余工具调用）：",
        "- **⚡ 极速通道（1 次调用、数秒完成）**：若用户只是开启/关闭夜间值守（`enabled`）、开关异常自动推 Agent（`auto_push_to_agent`）、或调整 `max_memory_gib` / `queue_timeout_seconds` / `max_resubmissions` / `license_retry_delay_seconds` / `preferred_hosts` / `auto_report_mode` 等 `CONFIG` 参数，直接 **1 次调用**零审批工具：",
        '  `spiceowl_direct_control(operation="update_watch_config", arguments={"enabled": false, "note": "按用户要求关闭夜间值守"})`',
        "  （一步同步修改 `night_watch.py` 与运行策略并实时刷新网页；**无需读取完整脚本、无需调用 `save_watch_script`、也无需跑 Dry-Run**，完成后直接一句话回复即可！）",
        "- **🛠 规则编程通道（仅新增/修改 `rule_*` 逻辑时使用，遵循 `spiceowl-night-watch` Skill）**：",
        '  1. 调用 `spiceowl_direct_read(operation="watch_script")` 阅读当前规则与顺序；',
        '  2. 用局部补丁 `spiceowl_direct_control(operation="save_watch_script", arguments={"edits": [{"find": "...", "replace": "..."}], "note": "..."})` 更新规则并严守科学红线（保持 `protect_netlist_and_spec=True`）；',
        '  3. 调用 `spiceowl_direct_control(operation="test_watch_script", arguments={"scenario": "..."})` 试运行验证后简明回复。',
    ])
    return "\n".join(lines)


def build_completion_report_prompt(
    run_record: dict,
    report: dict | None = None,
    waveform_index: dict | None = None,
    *,
    html_path: str | None = None,
    custom_prompt: str | None = None,
    mode: str = "fast",
) -> str:
    """Build a concise, secret-free Chinese handoff prompt when a direct simulation completes."""
    report_mode = "deep" if str(mode or "").strip().lower() == "deep" else "fast"
    mode_badge = "[🎨 深度报告模式]" if report_mode == "deep" else "[⚡ 快速报告模式]"

    rec = run_record if isinstance(run_record, dict) else {}
    run_id = str(rec.get("run_id") or "unknown")
    org = rec.get("organization") if isinstance(rec.get("organization"), dict) else {}
    project = org.get("project") or rec.get("project_name") or "未归类项目"
    library = org.get("library") or rec.get("library_name") or "-"
    testbench = org.get("testbench") or rec.get("cell_name") or "-"
    view = org.get("view") or rec.get("view_name") or "-"

    host_id = rec.get("host_id") or "unknown"
    backend = rec.get("backend") or "unknown"
    simulator = rec.get("simulator") or "unknown"
    state = rec.get("state") or "unknown"
    raw_remote_state = rec.get("raw_remote_state") or rec.get("remote_state") or "unknown"
    exit_code = rec.get("simulator_exit_code")
    exit_str = str(exit_code) if exit_code is not None else "null"

    limits = rec.get("limits") if isinstance(rec.get("limits"), dict) else {}
    cpu_count = limits.get("cpu_count")
    cpu_str = f"{cpu_count} 核" if isinstance(cpu_count, int) and not isinstance(cpu_count, bool) else "-"
    mem_bytes = limits.get("memory_bytes")
    if isinstance(mem_bytes, (int, float)) and not isinstance(mem_bytes, bool) and mem_bytes > 0:
        mem_str = f"{mem_bytes / (1024 ** 3):.2f} GiB"
    else:
        mem_str = "-"

    lines = [
        f"[SpiceOwl 仿真完成自动汇报 · {run_id}] {mode_badge}",
        f"任务 ID: {run_id} | 项目: {project} | Library: {library} | Testbench: {testbench} | View: {view}",
        f"执行环境: host={host_id} | backend={backend} | simulator={simulator} | 资源配额: CPU={cpu_str}, 内存={mem_str}",
        f"终态信息: state={state} | raw_remote_state={raw_remote_state} | simulator_exit_code={exit_str}",
    ]

    if isinstance(report, dict) and report:
        exec_st = (
            report["execution_status"].get("status", "unknown")
            if isinstance(report.get("execution_status"), dict)
            else "unknown"
        )
        meas_st = (
            report["measurement_validity"].get("status", "unknown")
            if isinstance(report.get("measurement_validity"), dict)
            else "unknown"
        )
        perf_st = (
            report["functional_performance"].get("status", "unknown")
            if isinstance(report.get("functional_performance"), dict)
            else "unknown"
        )
        qual_st = (
            report["operating_qualification"].get("status", "unknown")
            if isinstance(report.get("operating_qualification"), dict)
            else "unknown"
        )
        dep_st = (
            report["dependency_provenance"].get("status", "unknown")
            if isinstance(report.get("dependency_provenance"), dict)
            else "unknown"
        )
        lines.append(
            "五维评估摘要: "
            f"execution_status={exec_st}, measurement_validity={meas_st}, "
            f"functional_performance={perf_st}, operating_qualification={qual_st}, "
            f"dependency_provenance={dep_st}"
        )

        diag_block = report.get("diagnostics") if isinstance(report.get("diagnostics"), dict) else {}
        obs_ctx = diag_block.get("observed_context") if isinstance(diag_block.get("observed_context"), dict) else {}
        warn_sum = obs_ctx.get("warning_summary") if isinstance(obs_ctx.get("warning_summary"), dict) else {}
        cov = obs_ctx.get("diagnostic_coverage") if isinstance(obs_ctx.get("diagnostic_coverage"), dict) else {}
        err_cnt = warn_sum.get("reported_errors")
        if err_cnt is None:
            err_cnt = cov.get("observed_errors")
        warn_cnt = warn_sum.get("reported_warnings")
        if warn_cnt is None:
            warn_cnt = cov.get("observed_warnings")
        notice_cnt = warn_sum.get("reported_notices")
        if notice_cnt is None:
            notice_cnt = cov.get("observed_notices")
        diag_items = diag_block.get("diagnostics") if isinstance(diag_block.get("diagnostics"), list) else []
        diag_classes = []
        for item in diag_items[:6]:
            if isinstance(item, dict):
                code = item.get("code") or item.get("category")
                if code:
                    diag_classes.append(str(code))
        diag_parts = []
        if err_cnt is not None or warn_cnt is not None or notice_cnt is not None:
            diag_parts.append(
                f"errors={err_cnt if err_cnt is not None else '-'}, "
                f"warnings={warn_cnt if warn_cnt is not None else '-'}, "
                f"notices={notice_cnt if notice_cnt is not None else '-'}"
            )
        if diag_classes:
            diag_parts.append("诊断类别=" + ", ".join(diag_classes))
        if diag_parts:
            lines.append("日志诊断与告警摘要: " + " | ".join(diag_parts))

    datasets = (
        waveform_index.get("datasets")
        if isinstance(waveform_index, dict) and isinstance(waveform_index.get("datasets"), list)
        else []
    )
    if datasets:
        lines.append(f"已回收波形数据集（共 {len(datasets)} 个，展示前 {min(len(datasets), 4)} 个）:")
        for ds in datasets[:4]:
            if not isinstance(ds, dict):
                continue
            ds_name = str(ds.get("dataset") or ds.get("name") or "unknown")
            ds_fmt = str(ds.get("format") or "unknown")
            sweep = ds.get("sweep") if isinstance(ds.get("sweep"), dict) else {}
            sample_cnt = sweep.get("count")
            raw_sigs = ds.get("signals") if isinstance(ds.get("signals"), list) else []
            sig_names: list[str] = []
            for sig in raw_sigs[:16]:
                if isinstance(sig, dict) and sig.get("name"):
                    sig_names.append(str(sig["name"]))
                elif isinstance(sig, str) and sig:
                    sig_names.append(sig)
            total_sigs = ds.get("signal_count")
            if not isinstance(total_sigs, int) or isinstance(total_sigs, bool):
                total_sigs = len(raw_sigs)
            sig_preview = ", ".join(sig_names) if sig_names else "(无信号名)"
            more = f" 等共 {total_sigs} 个" if total_sigs > len(sig_names) else ""
            cnt_label = f", samples={sample_cnt}" if sample_cnt is not None else ""
            lines.append(f"  - {ds_name} ({ds_fmt}{cnt_label}): {sig_preview}{more}")
        if len(datasets) > 4:
            lines.append(f"  - ... 其余 {len(datasets) - 4} 个数据集可通过 waveform_index 查询")

    if report_mode == "deep" and isinstance(html_path, str) and html_path.strip():
        lines.append(f"HTML 报告存档路径 (html_path): {html_path.strip()}")

    if isinstance(custom_prompt, str) and custom_prompt.strip():
        lines.append(f"用户附加分析要求: {custom_prompt.strip()}")

    run_id_json = json.dumps(run_id, ensure_ascii=False)
    if report_mode == "fast":
        lines.extend([
            "",
            "【⚡ 快速报告模式 · 目标：1 分钟内极速出稿（最多 2~4 步工具调用），遵循 `spiceowl-sim-report-fast` 规范】",
            "1. **严禁手写 HTML/CSS/JS，严禁启动浏览器截图自检，严禁无意义多次复算！**",
            "2. 如需确认 1~3 个核心数字（如增益/带宽/功耗/建立时间），仅做一次轻量 `spiceowl_direct_read`（`report` / `waveform_measure` / `waveform_query`），严禁编造未观测数据。",
            f'3. 直接调用零审批工具 `spiceowl_direct_control(operation="render_fast_report", arguments={{"run_id": {run_id_json}, "title": "...", "summary": "<2~4 句简洁中文结论>", "highlights": ["..."], "kpis": [{{"label": "...", "value": "...", "status": "pass|warn|fail|info"}}], "plots": [{{"title": "...", "signals": ["..."]}}]}})` 一键生成并落盘。',
            "4. SpiceOwl 后端会自动抓取真实 `results.raw` 波形（支持 `A - B`、`(A + B)/2` 表达式）、绘制高清交互式 SVG 图表并套用预设框架落盘——你只需写一段简洁清晰的中文总结并挑选 2~3 组关键信号即可（不传 `plots` 也会自动挑图）。",
        ])
    else:
        lines.extend([
            "",
            "【🎨 深度报告模式】请遵循 `spiceowl-sim-report` Skill 规范，按以下步骤核实仿真结果并生成图文并茂的自包含中文 HTML 仿真报告：",
            "1. 调用 `spiceowl_direct_read`（`report`、`waveform_index`、`waveform_measure`、`waveform_query`、`log_tail` 等）核实完整仿真结果与关键信号实测指标（严禁编造未观测数据）。",
            "2. 生成一份**自包含、零外部 CDN、视觉精美的交互式 HTML 仿真报告**（`<!doctype html>...`，包含：KPI 核心指标卡片、结构化测量与收敛/告警表格、基于 `results.raw` 实测数据生成的内联 SVG 或 base64 PNG 波形图、异常或瓶颈诊断、下一步设计或仿真建议）。如使用本地 Python 脚本，可直接复用 `from spiceowl.report_builder import render_waveform_svg, wrap_editorial_html` 加速绘图与排版，并将成品 HTML 写入上述 `html_path`。",
            "3. **严禁启动无头浏览器（Playwright/Chromium/Selenium 等）对 HTML 进行截图自检或循环微调样式**，确保 HTML 自包含无语法错误后直接存档即可。",
            f'4. 务必调用零审批工具 `spiceowl_direct_control(operation="save_ai_report", arguments={{"run_id": {run_id_json}, "title": "...", "summary": "...", "mode": "deep", "html": "<!doctype html>...", "markdown": "..."}})` 将完整 HTML 报告（及简明 Markdown 摘要）永久存档到「仿真总览」的任务详情卡片中。',
        ])
    return "\n".join(lines)


def build_scope_report_prompt(
    scope: str,
    *,
    project: str,
    testbench: str | None = None,
    runs: list[dict],
    custom_prompt: str | None = None,
    html_path: str | None = None,
    mode: str = "fast",
) -> str:
    """Build a structured Chinese prompt to synthesize a Project-level or Testbench-level HTML report."""
    report_mode = "deep" if str(mode or "").strip().lower() == "deep" else "fast"
    mode_badge = "[🎨 深度报告模式]" if report_mode == "deep" else "[⚡ 快速报告模式]"

    scope_clean = "testbench" if scope == "testbench" else "project"
    proj_label = str(project or "未归类项目")
    tb_label = str(testbench) if (scope_clean == "testbench" and testbench) else None
    run_list = [r for r in (runs or []) if isinstance(r, dict)]
    run_ids = [str(r["run_id"]) for r in run_list if r.get("run_id")]

    if scope_clean == "testbench":
        header = f"[SpiceOwl 综合仿真报告请求 · {proj_label} / {tb_label or '-'}] {mode_badge}"
        scope_desc = f"报告范围: Testbench 级（项目={proj_label} | Testbench={tb_label or '-'} | 关联任务数={len(run_list)}）"
    else:
        tb_names = sorted({
            str((r.get("organization") or {}).get("testbench") or r.get("cell_name") or "-")
            for r in run_list
        })
        header = f"[SpiceOwl 综合仿真报告请求 · {proj_label}] {mode_badge}"
        scope_desc = (
            f"报告范围: Project 级（项目={proj_label} | 涵盖 Testbench 数={len(tb_names)} | 关联任务数={len(run_list)}）"
        )

    lines = [header, scope_desc]
    if run_list:
        lines.append(f"纳入汇总的仿真任务清单（展示前 {min(len(run_list), 40)} 条）：")
        for r in run_list[:40]:
            rid = str(r.get("run_id") or "unknown")
            org = r.get("organization") if isinstance(r.get("organization"), dict) else {}
            r_tb = org.get("testbench") or r.get("cell_name") or "-"
            r_state = r.get("state") or "unknown"
            r_raw = r.get("raw_remote_state") or r.get("remote_state") or "-"
            r_host = r.get("host_id") or "unknown"
            r_sim = r.get("simulator") or "unknown"
            r_created = r.get("created_at") or "-"
            limits = r.get("limits") if isinstance(r.get("limits"), dict) else {}
            cpu = limits.get("cpu_count")
            mem_b = limits.get("memory_bytes")
            mem_s = (
                f"{mem_b / (1024 ** 3):.1f}GiB"
                if isinstance(mem_b, (int, float)) and not isinstance(mem_b, bool) and mem_b > 0
                else "-"
            )
            cpu_s = f"{cpu}C" if isinstance(cpu, int) and not isinstance(cpu, bool) else "-"
            coll = r.get("collection") if isinstance(r.get("collection"), dict) else {}
            coll_phase = r.get("collection_phase") or ("complete" if coll else "uncollected")
            ai_rep = r.get("ai_report") if isinstance(r.get("ai_report"), dict) else {}
            rep_hint = ai_rep.get("summary") or ai_rep.get("title") or ""
            rep_part = f" | 已有报告摘要: {str(rep_hint)[:120]}" if rep_hint else ""
            coll_dir = coll.get("directory")
            coll_part = f" | collected={coll_dir}" if isinstance(coll_dir, str) and coll_dir else ""
            lines.append(
                f"  - {rid} [TB={r_tb}] | state={r_state}({r_raw}) | host={r_host} | sim={r_sim} | "
                f"limits={cpu_s}/{mem_s} | collection={coll_phase} | created={r_created}{rep_part}{coll_part}"
            )
        if len(run_list) > 40:
            lines.append(f"  - ... 其余 {len(run_list) - 40} 个任务可通过 `spiceowl_direct_read(operation='list')` 查阅")
    else:
        lines.append("当前该范围内暂无已记录的仿真任务，请先核实任务归属后给出说明。")

    if report_mode == "deep" and isinstance(html_path, str) and html_path.strip():
        lines.append(f"HTML 综合报告存档路径 (html_path): {html_path.strip()}")

    if isinstance(custom_prompt, str) and custom_prompt.strip():
        lines.append(f"用户附加分析要求: {custom_prompt.strip()}")

    scope_json = json.dumps(scope_clean, ensure_ascii=False)
    proj_json = json.dumps(proj_label, ensure_ascii=False)
    tb_json = json.dumps(tb_label, ensure_ascii=False)
    rids_preview = json.dumps(run_ids[:32], ensure_ascii=False)

    if report_mode == "fast":
        lines.extend([
            "",
            "【⚡ 快速报告模式 · 目标：1 分钟内极速出稿（最多 2~4 步工具调用），遵循 `spiceowl-sim-report-fast` 规范】",
            "1. **严禁手写 HTML/CSS/JS，严禁启动浏览器截图自检，严禁无意义多次复算！**",
            "2. 如需核实 1~3 项跨任务关键指标，仅对代表性任务做轻量只读查询（`report` / `ai_report` / `waveform_measure`），严禁编造未观测数据。",
            f'3. 直接调用零审批工具 `spiceowl_direct_control(operation="render_fast_scope_report", arguments={{"scope": {scope_json}, "project": {proj_json}, "testbench": {tb_json}, "run_ids": {rids_preview}, "title": "...", "summary": "<2~4 句简洁中文综合结论>", "highlights": ["..."], "kpis": [...], "comparison_rows": [...], "plots": [{{"run_id": "...", "title": "...", "signals": ["..."]}}]}})` 一键生成并归档。',
            "4. SpiceOwl 后端会自动汇总全部任务状态、构建横向对比表、抓取代表性任务的真实波形并绘制高清交互式 SVG 图表后套用预设框架落盘（不传 `plots` 也会自动挑图）。",
        ])
    else:
        lines.extend([
            "",
            "【🎨 深度报告模式】请遵循 `spiceowl-sim-report` Skill 规范，综合上述全部相关任务，生成一份图文并茂的中文 **HTML 综合仿真报告**：",
            "1. 调用 `spiceowl_direct_read`（`report`、`ai_report`、`waveform_index`、`waveform_measure`、`log_tail` 等）核实各任务的真实仿真数据、收敛状态与测量指标（严禁编造未观测数据）。",
            "2. 编制一份**自包含、零外部 CDN、视觉精美的交互式 HTML 综合仿真报告**（`<!doctype html>...`，包含：总览 KPI 卡片、多任务/多参数/多 Corner 横向对比表格、内联 SVG 或 base64 PNG 趋势/对比图表、跨任务瓶颈与一致性结论、明确的下一步设计/验证建议）。如使用本地 Python 脚本，可直接复用 `from spiceowl.report_builder import render_waveform_svg, wrap_editorial_html` 加速绘图与排版，并将成品 HTML 写入上述 `html_path`。",
            "3. **严禁启动无头浏览器（Playwright/Chromium/Selenium 等）对 HTML 进行截图自检或循环微调样式**，确保 HTML 自包含无语法错误后直接存档即可。",
            f'4. 务必调用零审批工具 `spiceowl_direct_control(operation="save_scope_report", arguments={{"scope": {scope_json}, "project": {proj_json}, "testbench": {tb_json}, "title": "...", "summary": "...", "mode": "deep", "html": "<!doctype html>...", "markdown": "...", "run_ids": {rids_preview}}})` 将综合报告永久存档到「仿真总览」对应范围的报告卡片中。',
        ])
    return "\n".join(lines)


def build_codex_bridge_pusher(codex_bridge):
    """Create a safe callback that wakes the user's connected CodexBridge thread."""

    def _push(*, event: dict, prompt: str, request_key: str, policy: dict) -> dict:
        kind = event.get("kind") if isinstance(event, dict) else None
        is_completion = kind == "simulation_completed"
        is_scope_report = kind == "scope_report_requested"
        is_script_edit = kind == "watch_script_edit"
        if codex_bridge is None:
            return {"pushed": False, "thread_id": None, "status": "no_agent_connected", "note": "当前未配置 Codex Agent 桥接。"}
        st = codex_bridge.status()
        if not st.get("connected"):
            if is_completion:
                note = "用户的 Codex Agent 当前未连接；仿真结果已保留，连接后可在任务详情页一键触发 AI 仿真报告。"
            elif is_scope_report:
                note = "用户的 Codex Agent 当前未连接；连接后即可一键生成综合 HTML 仿真报告。"
            elif is_script_edit:
                note = "用户的 Codex Agent 当前未连接；连接后可由 Agent 按要求自动定制并测试 night_watch.py。"
            else:
                note = "用户的 Codex Agent 当前未连接；异常已记录在网页收件箱，连接后可一键推送或自动认领。"
            return {"pushed": False, "thread_id": None, "status": "agent_disconnected", "note": note}
        if st.get("recovery_required"):
            return {"pushed": False, "thread_id": st.get("thread_id"), "status": "agent_recovery_required", "note": "Agent 会话存在未核对的中断回合，暂不自动推送以免覆盖上下文。"}
        if st.get("state") in ("starting", "running", "awaiting_approval", "waiting_approval"):
            if is_completion:
                note = f"Agent 当前状态为 {st.get('state')}，仿真完成汇报将在当前回合结束后自动重试推送。"
            elif is_scope_report:
                note = f"Agent 当前状态为 {st.get('state')}，请等待当前回合完成后再触发综合报告生成。"
            elif is_script_edit:
                note = f"Agent 当前状态为 {st.get('state')}，请等待当前回合完成后再发送 night_watch.py 定制指令。"
            else:
                note = f"Agent 当前状态为 {st.get('state')}，异常已排队留在网页值守列表等待推送。"
            return {"pushed": False, "thread_id": st.get("thread_id"), "status": "agent_busy", "note": note}
        thread_id = st.get("thread_id")
        if not thread_id:
            created = codex_bridge.create_thread()
            thread_id = created.get("thread_id")
        codex_bridge.send_message(thread_id, prompt, request_key)
        if is_completion:
            note = f"已将仿真完成摘要自动推送至用户接入的 Agent 会话（thread={thread_id}），Agent 正在核实结果并生成 HTML 仿真报告。"
        elif is_scope_report:
            note = f"已将综合仿真报告任务推送至用户接入的 Agent 会话（thread={thread_id}），Agent 正在汇总分析并生成 HTML 综合报告。"
        elif is_script_edit:
            note = f"已将夜间值守脚本定制任务推送至用户接入的 Agent 会话（thread={thread_id}），Agent 正在更新并 Dry-Run 验证 night_watch.py。"
        else:
            note = f"已将异常事件自动推送至用户接入的 Agent 会话（thread={thread_id}），Agent 正在根据 SpiceOwl 工具排查。"
        return {
            "pushed": True,
            "thread_id": thread_id,
            "status": "pushed",
            "note": note,
        }

    return _push

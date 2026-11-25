"""Opt-in overnight watch notification engine (Feishu / Slack / DingTalk / Generic Webhook).

Implements the "You sleep. SpiceOwl keeps watch." notification loop with strict
security and privacy guardrails:
- Opt-in only; disabled by default until explicitly configured by the user.
- Sends ONLY redacted structured status summaries (run ID, attempt number,
  five-dimensional verdict, recovery outcome). Never transmits netlists,
  waveforms, local paths, SSH keys, or credentials.
- Strict webhook URL validation (HTTPS only, plus loopback HTTP for offline tests).
- Delivery errors are recorded in an audit log and NEVER interrupt monitoring,
  collection, or bounded recovery.
"""

from __future__ import annotations

import contextlib
from copy import deepcopy
import json
import os
from pathlib import Path
import urllib.error
from urllib.parse import urlsplit
import urllib.request
import uuid

from spiceowl._platform import file_lock
from spiceowl.domain.common import DomainError, utcnow


NOTIFICATION_SCHEMA = "spiceowl.notification.v1"
NOTIFICATION_CONFIG_SCHEMA = "spiceowl.notification-config.v1"
PROVIDERS = ("feishu", "slack", "dingtalk", "generic")
EVENT_TYPES = (
    "completed",
    "failed",
    "spec_missed",
    "recovery_succeeded",
    "recovery_exhausted",
    "watch_disconnected",
)
MAX_CHANNELS = 8
MAX_HISTORY = 200
WEBHOOK_TIMEOUT_SECONDS = 5.0


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


def validate_webhook_url(url: str) -> str:
    """Validate an outbound webhook URL (HTTPS, or loopback HTTP for local testing)."""
    if not isinstance(url, str) or not 10 <= len(url.strip()) <= 2048:
        raise DomainError("Webhook URL must be a valid URL string.", "invalid_webhook_url")
    cleaned = url.strip()
    if any(ch.isspace() or ord(ch) < 32 for ch in cleaned):
        raise DomainError("Webhook URL must not contain whitespace or control characters.", "invalid_webhook_url")
    parsed = urlsplit(cleaned)
    if parsed.username or parsed.password:
        raise DomainError("Webhook URL must not embed basic-auth credentials.", "invalid_webhook_url")
    host = (parsed.hostname or "").lower()
    if not host:
        raise DomainError("Webhook URL must specify a host.", "invalid_webhook_url")
    if parsed.scheme == "https":
        return cleaned
    if parsed.scheme == "http" and host in ("127.0.0.1", "localhost", "::1"):
        return cleaned
    raise DomainError("Webhook URL must use https:// (or loopback http://127.0.0.1 for local tests).", "invalid_webhook_url")


def redact_webhook_url(url: str) -> str:
    """Mask webhook secret path segments and query tokens before display."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "[invalid-url]"
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) > 1:
        masked_path = "/" + parts[0] + "/…/" + (parts[-1][:4] + "****" if len(parts[-1]) > 4 else "****")
    elif parts:
        masked_path = "/" + parts[0][:4] + "****"
    else:
        masked_path = ""
    return f"{parsed.scheme}://{parsed.netloc}{masked_path}"


def build_notification_event(
    *,
    event_type: str,
    run_id: str,
    host_id: str = "unknown",
    backend: str = "ssh_direct",
    simulator: str = "spectre",
    attempt_number: int = 1,
    five_dim_summary: dict | None = None,
    detail: str = "",
) -> dict:
    """Build a strictly redacted, secret-free notification event payload."""
    if event_type not in EVENT_TYPES:
        raise DomainError(f"Unsupported notification event_type: {event_type!r}.", "invalid_notification_event")
    if not isinstance(run_id, str) or not run_id.strip():
        raise DomainError("Notification event requires run_id.", "invalid_notification_event")

    titles = {
        "completed": f"仿真完成 · {run_id}",
        "failed": f"仿真失败需关注 · {run_id}",
        "spec_missed": f"有效测量但指标未达标 · {run_id}",
        "recovery_succeeded": f"异常已自动恢复并完成 · {run_id}",
        "recovery_exhausted": f"自动恢复预算耗尽已停止 · {run_id}",
        "watch_disconnected": f"后台盯守通信中断（状态未知）· {run_id}",
    }
    severity = {
        "completed": "green",
        "recovery_succeeded": "green",
        "spec_missed": "amber",
        "watch_disconnected": "amber",
        "failed": "red",
        "recovery_exhausted": "red",
    }[event_type]

    fd = five_dim_summary if isinstance(five_dim_summary, dict) else {}
    safe_fd = {
        "execution": str(fd.get("execution_status") or fd.get("execution") or "unknown")[:48],
        "measurement": str(fd.get("measurement_validity") or fd.get("measurement") or "unknown")[:48],
        "performance": str(fd.get("functional_performance") or fd.get("performance") or "unknown")[:48],
        "soa": str(fd.get("operating_qualification") or fd.get("soa") or "unknown")[:48],
    }
    return {
        "schema": NOTIFICATION_SCHEMA,
        "event_id": f"ntf-{uuid.uuid4().hex[:12]}",
        "event_type": event_type,
        "severity": severity,
        "title": titles[event_type],
        "run_id": run_id.strip()[:128],
        "host_id": str(host_id)[:96],
        "backend": str(backend)[:32],
        "simulator": str(simulator)[:32],
        "attempt_number": int(attempt_number) if isinstance(attempt_number, int) and attempt_number >= 1 else 1,
        "five_dim": safe_fd,
        "detail": str(detail or "").strip()[:400],
        "created_at": utcnow(),
    }


def format_provider_payload(provider: str, event: dict) -> dict:
    """Render a provider-specific webhook JSON body (Feishu, Slack, DingTalk, or Generic)."""
    if provider not in PROVIDERS:
        raise DomainError(f"Unsupported notification provider: {provider!r}.", "invalid_notification_provider")
    fd = event.get("five_dim") or {}
    lines = [
        f"任务 ID: `{event['run_id']}` (第 #{event['attempt_number']} 次尝试)",
        f"节点/后端: `{event['host_id']}` · `{event['backend']}` · `{event['simulator']}`",
        f"四维状态: 执行=`{fd.get('execution', 'unknown')}` | 测量=`{fd.get('measurement', 'unknown')}` | 性能=`{fd.get('performance', 'unknown')}` | SOA=`{fd.get('soa', 'unknown')}`",
    ]
    if event.get("detail"):
        lines.append(f"说明: {event['detail']}")
    body_md = "\n".join(lines)

    if provider == "feishu":
        template = {"green": "green", "amber": "orange", "red": "red"}.get(event.get("severity"), "blue")
        return {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": template,
                    "title": {"tag": "plain_text", "content": f"🦉 SpiceOwl · {event['title']}"},
                },
                "elements": [
                    {"tag": "markdown", "content": body_md},
                    {"tag": "note", "elements": [{"tag": "plain_text", "content": f"You sleep. SpiceOwl keeps watch. · {event['created_at']}"}]},
                ],
            },
        }
    if provider == "dingtalk":
        return {
            "msgtype": "markdown",
            "markdown": {
                "title": f"SpiceOwl · {event['title']}",
                "text": f"### 🦉 {event['title']}\n\n" + "\n\n".join(f"- {line}" for line in lines),
            },
        }
    if provider == "slack":
        return {
            "text": f"SpiceOwl · {event['title']}",
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": f"SpiceOwl · {event['title']}"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": body_md}},
                {"type": "context", "elements": [{"type": "mrkdwn", "text": f"Recorded at {event['created_at']} · No netlists or secrets included."}]},
            ],
        }
    return deepcopy(event)


def deliver_webhook(url: str, payload: dict, *, timeout: float = WEBHOOK_TIMEOUT_SECONDS) -> dict:
    """POST a JSON webhook payload with bounded timeout and explicit status receipt."""
    validated_url = validate_webhook_url(url)
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    req = urllib.request.Request(
        validated_url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "SpiceOwl-Watch/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = int(getattr(resp, "status", 200))
            return {"status": "delivered" if 200 <= code < 300 else "failed", "http_status": code, "error": None}
    except urllib.error.HTTPError as exc:
        return {"status": "failed", "http_status": int(exc.code), "error": f"HTTP {exc.code}"}
    except Exception as exc:
        return {"status": "failed", "http_status": None, "error": str(exc)[:200]}


class NotificationManager:
    """Durable opt-in overnight webhook notification registry & audit log."""

    def __init__(self, data_dir: str | Path):
        self.root = Path(data_dir).expanduser().resolve() / "notifications"
        self.config_path = self.root / "config.json"
        self.history_path = self.root / "history.json"
        self.lock_path = self.root / "notifications.lock"

    @contextlib.contextmanager
    def _locked(self):
        with file_lock(self.lock_path, nonblocking=False, mode="a", create_dir=True, dir_mode=0o700):
            yield

    def _load_config(self) -> dict:
        if not self.config_path.is_file() or self.config_path.is_symlink():
            return {"schema": NOTIFICATION_CONFIG_SCHEMA, "channels": []}
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("channels"), list):
                return raw
        except (OSError, ValueError):
            pass
        return {"schema": NOTIFICATION_CONFIG_SCHEMA, "channels": []}

    def _load_history(self) -> list[dict]:
        if not self.history_path.is_file() or self.history_path.is_symlink():
            return []
        try:
            raw = json.loads(self.history_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
                return raw["entries"]
        except (OSError, ValueError):
            pass
        return []

    def list_channels(self, *, redact: bool = True) -> list[dict]:
        cfg = self._load_config()
        out = []
        for ch in cfg["channels"]:
            item = deepcopy(ch)
            if redact and item.get("webhook_url"):
                item["webhook_url"] = redact_webhook_url(item["webhook_url"])
            out.append(item)
        return out

    def list_history(self, limit: int = 30) -> list[dict]:
        entries = self._load_history()
        return deepcopy(entries[-max(1, min(limit, MAX_HISTORY)):][::-1])

    def add_channel(
        self,
        *,
        label: str,
        provider: str,
        webhook_url: str,
        events: list[str] | None = None,
        enabled: bool = True,
    ) -> dict:
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80:
            raise DomainError("Channel label must be 1–80 characters.", "invalid_notification_channel")
        if provider not in PROVIDERS:
            raise DomainError(f"provider must be one of {PROVIDERS}.", "invalid_notification_provider")
        validated_url = validate_webhook_url(webhook_url)
        chosen_events = list(EVENT_TYPES) if not events else [str(e) for e in events]
        if not chosen_events or any(e not in EVENT_TYPES for e in chosen_events):
            raise DomainError(f"events must be a non-empty subset of {EVENT_TYPES}.", "invalid_notification_event")
        with self._locked():
            cfg = self._load_config()
            if len(cfg["channels"]) >= MAX_CHANNELS:
                raise DomainError(f"At most {MAX_CHANNELS} notification channels are supported.", "invalid_notification_channel")
            channel = {
                "channel_id": f"ch-{uuid.uuid4().hex[:8]}",
                "label": label.strip(),
                "provider": provider,
                "webhook_url": validated_url,
                "events": sorted(set(chosen_events)),
                "enabled": bool(enabled),
                "created_at": utcnow(),
            }
            cfg["channels"].append(channel)
            _atomic_write_json(self.config_path, cfg)
        redacted = deepcopy(channel)
        redacted["webhook_url"] = redact_webhook_url(validated_url)
        return redacted

    def remove_channel(self, channel_id: str) -> dict:
        with self._locked():
            cfg = self._load_config()
            before = len(cfg["channels"])
            cfg["channels"] = [c for c in cfg["channels"] if c.get("channel_id") != channel_id]
            if len(cfg["channels"]) == before:
                raise DomainError("Notification channel not found.", "not_found")
            _atomic_write_json(self.config_path, cfg)
        return {"removed": channel_id}

    def dispatch(self, event: dict, *, dry_run: bool = False, sender=None) -> list[dict]:
        """Deliver an event to all matching enabled channels and append audit receipts."""
        send_fn = sender or deliver_webhook
        cfg = self._load_config()
        receipts: list[dict] = []
        for ch in cfg["channels"]:
            if not ch.get("enabled"):
                continue
            if event["event_type"] not in (ch.get("events") or EVENT_TYPES):
                continue
            payload = format_provider_payload(ch["provider"], event)
            if dry_run:
                res = {"status": "dry_run", "http_status": None, "error": None}
            else:
                res = send_fn(ch["webhook_url"], payload)
            receipt = {
                "event_id": event["event_id"],
                "event_type": event["event_type"],
                "run_id": event["run_id"],
                "channel_id": ch["channel_id"],
                "label": ch["label"],
                "provider": ch["provider"],
                "status": res.get("status", "failed"),
                "http_status": res.get("http_status"),
                "error": res.get("error"),
                "sent_at": utcnow(),
            }
            receipts.append(receipt)
        if receipts:
            with self._locked():
                hist = self._load_history()
                hist.extend(receipts)
                if len(hist) > MAX_HISTORY:
                    hist = hist[-MAX_HISTORY:]
                _atomic_write_json(self.history_path, {"schema": "spiceowl.notification-history.v1", "entries": hist})
        return receipts

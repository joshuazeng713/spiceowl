"""Standalone direct execution manager for SSH Direct, LSF, and Slurm runs.

Provides a self-contained execution backend that requires no CircuitGit gateway
or external orchestrator. Implements ``spiceowl.monitoring.remote.RunnerProtocol``
so ``RuntimeMonitor`` can supervise direct runs with durable observations and
exponential backoff on disconnects.

Core safety invariants:
- ``prepare`` freezes inputs locally without contacting any remote host.
- ``launch`` persists a durable dispatch claim BEFORE opening SSH; any network
  interruption records ``launch_outcome_unknown`` and blocks blind replay.
- ``status`` reconciles the bound remote identity without ever re-launching.
- ``cancel`` targets only the verified owned PID group or scheduler Job ID.
- ``collect`` verifies all artifact digests, stores files locally, and produces
  a five-dimensional Spectre report separating execution, measurement validity,
  functional performance, operating qualification, and dependency provenance.
"""

from __future__ import annotations

import base64
import contextlib
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import threading
import uuid

from spiceowl._platform import file_lock
from spiceowl.adapters.executors import direct as transport
from spiceowl.domain.common import DomainError, new_id, require_id, utcnow
from spiceowl.hosts import HostRegistry, profile_from_record
from spiceowl.integrations import psf_binary
from spiceowl.integrations.spectre_report import build_spectre_report
from spiceowl.monitoring.remote import RuntimeMonitor
from spiceowl.recovery.bounded import (
    DEFAULT_RECOVERY_POLICY,
    build_recovery_report,
    classify_failure,
    evaluate_bounded_recovery,
    render_recovery_report_markdown,
    validate_recovery_policy,
)


RUN_SCHEMA = "spiceowl.direct-run.v1"
SUBMISSIONS_SCHEMA = "spiceowl.direct-submissions.v1"
RECOVERY_INDEX_SCHEMA = "spiceowl.recovery-report-index.v1"
AI_REPORT_SCHEMA = "spiceowl.ai-sim-report.v1"
SCOPE_REPORT_INDEX_SCHEMA = "spiceowl.scope-report-index.v1"
MAX_AI_REPORT_HTML_BYTES = 2 * 1024 * 1024
MAX_SCOPE_REPORT_HTML_BYTES = 4 * 1024 * 1024
MAX_AI_REPORT_MARKDOWN_BYTES = 256 * 1024
_HTML_TITLE_RE = re.compile(r"<(?:title|h1)\b[^>]*>(.*?)</(?:title|h1)>", re.IGNORECASE | re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", re.ASCII)
_COMMIT = re.compile(r"[a-f0-9]{40}", re.ASCII)

_TERMINAL_ADAPTER_STATES = frozenset({"completed", "failed", "timed_out", "cancelled", "preflight_failed"})
_BLOCKED_ERROR_CODES = frozenset({"identity_mismatch", "run_key_conflict", "input_hash_mismatch", "receipt_mismatch"})


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _receipt_transport_flags(receipt) -> tuple[bool, bool]:
    rec = _as_dict(receipt)
    err = rec.get("error")
    connected = err is None
    blocked = _as_dict(err).get("code") in _BLOCKED_ERROR_CODES
    return connected, blocked
_VIRTUOSO_EXPORT_RE = re.compile(
    r"//\s*Exported from Virtuoso schematic\s+([^\s/]+)/([^\s(]+)(?:\s*\(([^)]+)\))?",
    re.IGNORECASE,
)
_CADENCE_LIB_RE = re.compile(r"//\s*Library name:\s*(\S+)", re.IGNORECASE)
_CADENCE_CELL_RE = re.compile(r"//\s*Cell name:\s*(\S+)", re.IGNORECASE)
_CADENCE_VIEW_RE = re.compile(r"//\s*View name:\s*(\S+)", re.IGNORECASE)
_RUN_SUFFIX_RE = re.compile(r"(?:-(?:20\d{10,12}|\d{6}|r\d+))+$")


def _clean_meta_text(value, field_name: str, *, max_len: int = 160) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DomainError(f"{field_name} must be a string or null.", "invalid_direct_plan")
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > max_len or any(ord(ch) < 32 or ord(ch) == 127 for ch in cleaned):
        raise DomainError(f"{field_name} must be a printable string up to {max_len} characters.", "invalid_direct_plan")
    return cleaned


def extract_netlist_organization_hints(netlist_text: str | None) -> dict[str, str | None]:
    """Infer Virtuoso library / cell (TB) / view hints from standard Spectre netlist headers."""
    hints: dict[str, str | None] = {"project_name": None, "library_name": None, "cell_name": None, "view_name": None}
    if not isinstance(netlist_text, str) or not netlist_text:
        return hints
    head = netlist_text[:4096]
    m_exp = _VIRTUOSO_EXPORT_RE.search(head)
    if m_exp:
        hints["library_name"] = m_exp.group(1).strip()[:160] or None
        hints["cell_name"] = m_exp.group(2).strip()[:160] or None
        hints["view_name"] = (m_exp.group(3) or "").strip()[:120] or None
    if not hints["library_name"]:
        m_lib = _CADENCE_LIB_RE.search(head)
        if m_lib:
            hints["library_name"] = m_lib.group(1).strip()[:160] or None
    if not hints["cell_name"]:
        m_cell = _CADENCE_CELL_RE.search(head)
        if m_cell:
            hints["cell_name"] = m_cell.group(1).strip()[:160] or None
    if not hints["view_name"]:
        m_view = _CADENCE_VIEW_RE.search(head)
        if m_view:
            hints["view_name"] = m_view.group(1).strip()[:120] or None
    if hints["library_name"]:
        hints["project_name"] = hints["library_name"]
    return hints


def _infer_tb_from_run_id(run_id: str) -> str:
    stem = _RUN_SUFFIX_RE.sub("", str(run_id or "")).strip("-_")
    if len(stem) >= 2 and stem.lower() not in {"sim", "direct", "run", "test"}:
        return stem[:160]
    return str(run_id or "unnamed")[:160]


def build_run_organization(
    record: dict,
    *,
    netlist_head: str | None = None,
    batch_info: dict | None = None,
) -> dict:
    """Compute normalized Project / Testbench / Submission-Group metadata for a run record."""
    hints = extract_netlist_organization_hints(netlist_head) if netlist_head else {}
    binfo = batch_info if isinstance(batch_info, dict) else {}
    run_id = str(record.get("run_id") or "")
    root_id = str(record.get("root_run_id") or run_id)
    has_chain = bool(
        record.get("parent_run_id")
        or record.get("child_run_ids")
        or (isinstance(record.get("attempt_number"), int) and record["attempt_number"] > 1)
    )
    library = record.get("library_name") or binfo.get("library_name") or hints.get("library_name")
    project = (
        record.get("project_name")
        or binfo.get("project_name")
        or library
        or hints.get("project_name")
        or "未归类项目"
    )
    testbench = (
        record.get("cell_name")
        or binfo.get("cell_name")
        or hints.get("cell_name")
        or _infer_tb_from_run_id(root_id or run_id)
    )
    view = record.get("view_name") or binfo.get("view_name") or hints.get("view_name")
    batch_id = record.get("batch_id") or binfo.get("batch_id")
    explicit_group = record.get("submission_group_id")
    if batch_id:
        group_id = explicit_group or f"batch:{batch_id}"
        kind = "batch"
        label = record.get("submission_label") or binfo.get("title") or str(batch_id)
    elif explicit_group:
        group_id = explicit_group
        kind = "recovery_chain" if has_chain else ("multi_run" if explicit_group != run_id else "single")
        label = record.get("submission_label") or (f"{testbench} · 批量提交" if kind == "multi_run" else testbench)
    elif has_chain:
        group_id = f"chain:{root_id}"
        kind = "recovery_chain"
        label = record.get("submission_label") or f"{testbench} · 重试链"
    else:
        group_id = run_id
        kind = "single"
        label = record.get("submission_label") or run_id
    return {
        "project": project,
        "library": library,
        "testbench": testbench,
        "view": view,
        "submission_group_id": group_id,
        "submission_label": label,
        "submission_kind": kind,
        "batch_id": batch_id,
        "case_label": binfo.get("case_label"),
        "source_origin": record.get("source_origin") or ("batch" if batch_id else "direct"),
    }


def _atomic_write(path, value):
    path = Path(path)
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


def _atomic_write_text(path, text: str) -> None:
    path = Path(path)
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


def _read_bounded_text(path: Path, max_bytes: int, *, err_code: str = "invalid_ai_report") -> str | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        with path.open("rb") as stream:
            raw = stream.read(max_bytes + 1)
    except OSError:
        return None
    if len(raw) > max_bytes:
        raise DomainError(f"Report file '{path.name}' exceeds the {max_bytes} byte limit.", err_code)
    text = raw.decode("utf-8", errors="replace")
    return text if text.strip() else None


def _file_mtime_iso(path: Path) -> str | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except OSError:
        return None


def _extract_report_title(html: str | None, markdown: str | None, fallback: str) -> str:
    if isinstance(html, str) and html:
        m = _HTML_TITLE_RE.search(html[:32768])
        if m:
            plain = _HTML_TAG_RE.sub("", m.group(1))
            plain = " ".join(plain.split())
            plain = "".join(ch for ch in plain if ord(ch) >= 32 and ord(ch) != 127).strip()
            if plain:
                return plain[:200]
    if isinstance(markdown, str) and markdown:
        for line in markdown.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                heading = stripped.lstrip("#").strip()
                heading = "".join(ch for ch in heading if ord(ch) >= 32 and ord(ch) != 127).strip()
                if heading:
                    return heading[:200]
    return fallback[:200]


def _extract_report_summary(markdown: str | None) -> str:
    if isinstance(markdown, str) and markdown:
        for line in markdown.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "```", "---", "***", "|")):
                continue
            cleaned_line = "".join(ch for ch in stripped if ord(ch) >= 32 and ord(ch) != 127).strip()
            if cleaned_line:
                return cleaned_line[:240]
    return ""


def _validate_report_content(
    *,
    html: str | None,
    markdown: str | None,
    title: str | None,
    summary: str | None,
    source: str,
    max_html_bytes: int,
    fallback_title: str,
    err_code: str = "invalid_ai_report",
) -> tuple[str | None, str | None, str, str, str]:
    clean_html: str | None = None
    if html is not None:
        if not isinstance(html, str):
            raise DomainError("Report html must be a string or null.", err_code)
        if "\x00" in html:
            raise DomainError("Report html cannot contain null bytes.", err_code)
        if html.strip():
            if len(html.encode("utf-8")) > max_html_bytes:
                raise DomainError(f"Report html exceeds the {max_html_bytes // (1024 * 1024)} MiB size limit.", err_code)
            clean_html = html

    clean_markdown: str | None = None
    if markdown is not None:
        if not isinstance(markdown, str):
            raise DomainError("Report markdown must be a string or null.", err_code)
        if "\x00" in markdown:
            raise DomainError("Report markdown cannot contain null bytes.", err_code)
        if markdown.strip():
            if len(markdown.encode("utf-8")) > MAX_AI_REPORT_MARKDOWN_BYTES:
                raise DomainError("Report markdown exceeds the 256 KiB size limit.", err_code)
            clean_markdown = markdown

    if clean_html is None and clean_markdown is None:
        raise DomainError("Report requires non-empty html or markdown content.", err_code)

    clean_title: str | None = None
    if title is not None:
        if not isinstance(title, str):
            raise DomainError("title must be a string or null.", err_code)
        stripped_title = title.strip()
        if len(stripped_title) > 200 or any(ord(ch) < 32 or ord(ch) == 127 for ch in stripped_title):
            raise DomainError("title must be a printable string up to 200 characters.", err_code)
        clean_title = stripped_title or None
    if clean_title is None:
        clean_title = _extract_report_title(clean_html, clean_markdown, fallback_title)

    clean_summary: str | None = None
    if summary is not None:
        if not isinstance(summary, str):
            raise DomainError("summary must be a string or null.", err_code)
        stripped_summary = summary.strip()
        if len(stripped_summary) > 600 or any(
            (ord(ch) < 32 and ch not in "\n\r\t") or ord(ch) == 127 for ch in stripped_summary
        ):
            raise DomainError("summary must be a printable string up to 600 characters.", err_code)
        clean_summary = stripped_summary or None
    if clean_summary is None:
        clean_summary = _extract_report_summary(clean_markdown)

    if not isinstance(source, str) or not source.strip():
        raise DomainError("source must be a non-empty string.", err_code)
    clean_source = source.strip()
    if len(clean_source) > 64 or any(ord(ch) < 32 or ord(ch) == 127 for ch in clean_source):
        raise DomainError("source must be a printable string up to 64 characters.", err_code)

    return clean_html, clean_markdown, clean_title, clean_summary, clean_source


def scope_key(scope: str, project: str, testbench: str | None = None) -> str:
    """Compute a deterministic filesystem-safe key for a project/testbench scope report."""
    if scope not in ("project", "testbench"):
        raise DomainError("scope must be 'project' or 'testbench'.", "invalid_scope_report")
    if not isinstance(project, str) or not project.strip():
        raise DomainError("project must be a non-empty string.", "invalid_scope_report")
    clean_proj = project.strip()
    if len(clean_proj) > 160 or any(ord(ch) < 32 or ord(ch) == 127 for ch in clean_proj):
        raise DomainError("project must be a printable string up to 160 characters.", "invalid_scope_report")
    clean_tb: str | None = None
    if scope == "testbench":
        if not isinstance(testbench, str) or not testbench.strip():
            raise DomainError("testbench must be a non-empty string when scope is 'testbench'.", "invalid_scope_report")
        clean_tb = testbench.strip()
        if len(clean_tb) > 160 or any(ord(ch) < 32 or ord(ch) == 127 for ch in clean_tb):
            raise DomainError("testbench must be a printable string up to 160 characters.", "invalid_scope_report")
    digest = hashlib.sha256(json.dumps([scope, clean_proj, clean_tb or ""]).encode()).hexdigest()[:24]
    return f"{scope}-{digest}"


def _read_json(path):
    path = Path(path)
    if path.is_symlink():
        raise DomainError("Direct execution state must be a regular local file.", "direct_state_invalid")
    if not path.is_file():
        raise DomainError("Direct execution record was not found.", "not_found")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise DomainError("Direct execution record is unreadable or corrupt.", "direct_state_invalid") from None


@contextlib.contextmanager
def _locked(path):
    with file_lock(path, nonblocking=False, mode="a", create_dir=True, dir_mode=0o700):
        yield


def _validate_run_id(run_id):
    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise DomainError("Run ID must be 1–128 ASCII letters, digits, underscores, or hyphens.", "invalid_run_id")
    return run_id


def _monotonic_observed_at(previous_iso):
    now = datetime.now(timezone.utc)
    if isinstance(previous_iso, str):
        try:
            prev = datetime.fromisoformat(previous_iso)
            if prev.utcoffset() is not None and now <= prev:
                now = prev + timedelta(microseconds=1)
        except (ValueError, OverflowError):
            pass
    return now.isoformat()


def _map_adapter_state(receipt, *, launch_claimed=False):
    """Map low-level adapter state to RunnerProtocol / RuntimeMonitor states."""
    rec = _as_dict(receipt)
    raw = rec.get("state")
    result = _as_dict(rec.get("result"))
    raw_exit = result.get("exit_code")
    if raw == "completed":
        code = raw_exit if type(raw_exit) is int else 0
        return "simulator_complete_pending_measurements", "simulator_complete_pending_measurements", code
    if raw == "preflight_failed":
        code = raw_exit if type(raw_exit) is int else 1
        return "job_setup_failed", "job_setup_failed", code
    if raw in ("failed", "timed_out", "cancelled"):
        fallback = 124 if raw == "timed_out" else 130 if raw == "cancelled" else 1
        code = raw_exit if type(raw_exit) is int else fallback
        return "simulator_failed", "simulator_failed", code
    if raw == "queued":
        return "submitted", "queued", None
    if raw == "running":
        return "running_observed", "running", None
    if raw in ("staged", "not_staged", "stage_incomplete"):
        return raw, raw, None
    err = _as_dict(rec.get("error"))
    if err.get("code") in ("input_hash_mismatch", "receipt_mismatch", "run_key_conflict"):
        return "input_hash_failure", "input_hash_failure", 1
    if launch_claimed:
        return "launch_outcome_unknown", "reconcile_required", None
    return "reconcile_required", "reconcile_required", None


def _execution_state_for_report(run_record):
    raw = run_record.get("raw_remote_state")
    if raw == "completed":
        return "completed"
    if raw == "cancelled":
        return "cancelled"
    if raw in ("failed", "timed_out", "preflight_failed"):
        return "failed"
    if raw == "queued":
        return "queued"
    if raw == "running":
        return "running"
    return "unknown"


class DirectExecutionManager:
    """Manage direct SSH, LSF, and Slurm runs using SpiceOwl's HostRegistry."""

    def __init__(self, data_dir, *, hosts=None):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.root = self.data_dir / "direct_runs"
        self._hosts = hosts

    @property
    def hosts(self):
        if self._hosts is None:
            self._hosts = HostRegistry(self.data_dir)
        return self._hosts

    def path(self, run_id: str) -> Path:
        validated = _validate_run_id(run_id)
        target = self.root / validated
        if target.is_symlink():
            raise DomainError("Run directory cannot be a symlink.", "direct_state_invalid")
        return target

    def _submissions_path(self) -> Path:
        return self.root / "submissions.json"

    def _load_submissions(self) -> dict:
        path = self._submissions_path()
        if not path.exists() and not path.is_symlink():
            return {"schema": SUBMISSIONS_SCHEMA, "entries": {}}
        data = _read_json(path)
        if not isinstance(data, dict) or data.get("schema") != SUBMISSIONS_SCHEMA or not isinstance(data.get("entries"), dict):
            raise DomainError("Direct submission index is corrupt.", "direct_state_invalid")
        return data

    def _save_receipt(self, run_id, operation, payload):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = self.path(run_id) / "receipts" / f"{stamp}-{operation}.json"
        _atomic_write(target, payload)
        return str(target)

    def _read_netlist_head(self, run_id: str) -> str:
        try:
            plan_path = self.path(run_id) / "plan.json"
            if not plan_path.is_file() or plan_path.is_symlink():
                return ""
            plan = _as_dict(_read_json(plan_path))
            raw_b64 = _as_dict(plan.get("files")).get("input.scs")
            if not isinstance(raw_b64, str) or not raw_b64:
                return ""
            # Decode up to first 6 KiB of base64 (~4.5 KiB text) for header comments only
            chunk = raw_b64[:6144]
            chunk = chunk[: len(chunk) - (len(chunk) % 4)]
            if not chunk:
                return ""
            return base64.b64decode(chunk, validate=False).decode("utf-8", errors="ignore")[:4096]
        except Exception:
            return ""

    def _enrich_record(self, record: dict, *, batch_info: dict | None = None) -> dict:
        rec = deepcopy(record)
        rid = rec.get("run_id")
        needs_hints = not (rec.get("library_name") or rec.get("cell_name") or rec.get("project_name"))
        head = self._read_netlist_head(rid) if needs_hints and rid else ""
        rec["organization"] = build_run_organization(rec, netlist_head=head, batch_info=batch_info)
        if isinstance(rid, str) and rid:
            try:
                run_dir = self.path(rid)
                html_file = run_dir / "ai_report.html"
                md_file = run_dir / "ai_report.md"
                disk_html = html_file.is_file() and not html_file.is_symlink()
                disk_md = md_file.is_file() and not md_file.is_symlink()
                if disk_html or disk_md:
                    existing_ai = _as_dict(rec.get("ai_report"))
                    if not existing_ai.get("available") or (disk_html and not existing_ai.get("has_html")):
                        mtime = _file_mtime_iso(html_file if disk_html else md_file) or rec.get("updated_at") or utcnow()
                        rec["ai_report"] = {
                            "available": True,
                            "has_html": bool(disk_html or existing_ai.get("has_html")),
                            "has_markdown": bool(disk_md or existing_ai.get("has_markdown")),
                            "title": existing_ai.get("title") or f"AI 仿真分析报告 · {rid}",
                            "summary": existing_ai.get("summary") or "",
                            "generated_at": existing_ai.get("generated_at") or mtime,
                            "updated_at": existing_ai.get("updated_at") or mtime,
                            "source": existing_ai.get("source") or "agent",
                        }
            except DomainError:
                pass
        return rec

    def read(self, run_id: str) -> dict:
        return self._enrich_record(_read_json(self.path(run_id) / "run.json"))

    def read_inputs(self, run_id: str) -> dict:
        """Return frozen netlist, support files, and config for restoring into the workbench / Agent."""
        run_id = _validate_run_id(run_id)
        record = self.read(run_id)
        plan_path = self.path(run_id) / "plan.json"
        if plan_path.is_symlink() or not plan_path.is_file():
            raise DomainError("Frozen plan inputs for this run are unavailable.", "not_found")
        plan = _read_json(plan_path)
        files = plan.get("files") if isinstance(plan.get("files"), dict) else {}
        netlist_b64 = files.get("input.scs")
        netlist_text = ""
        if isinstance(netlist_b64, str) and netlist_b64:
            try:
                netlist_text = base64.b64decode(netlist_b64, validate=True).decode("utf-8", errors="replace")
            except (ValueError, UnicodeError):
                raise DomainError("Frozen netlist input is corrupt.", "direct_state_invalid") from None
        support_files: dict[str, str] = {}
        for rel_name, b64_val in files.items():
            if rel_name in ("input.scs", "wrapper.py") or not isinstance(b64_val, str):
                continue
            try:
                support_files[rel_name] = base64.b64decode(b64_val, validate=True).decode("utf-8", errors="replace")
            except (ValueError, UnicodeError):
                continue
        manifest = plan.get("manifest") if isinstance(plan.get("manifest"), dict) else {}
        return {
            "run": record,
            "netlist_text": netlist_text,
            "support_files": support_files,
            "include_dirs": list(manifest.get("include_dirs") or []),
            "remote_env": deepcopy(manifest.get("remote_env")) if isinstance(manifest.get("remote_env"), dict) else None,
            "limits": deepcopy(record.get("limits") or manifest.get("limits") or {}),
            "scheduler": deepcopy(record.get("scheduler_config") or manifest.get("scheduler") or {}),
            "output_format": record.get("output_format") or manifest.get("output_format") or "psfbin",
            "remote_python": manifest.get("remote_python") or "/usr/bin/python3",
            "remote_root": record.get("remote_root") or manifest.get("remote_root") or ".spiceowl-runs",
            "recovery_policy": deepcopy(record.get("recovery_policy") or {}),
            "organization": deepcopy(record.get("organization") or {}),
        }

    def delete(self, run_id: str) -> dict:
        """Delete a local non-active direct run record (refuses while dispatched & non-terminal)."""
        run_id = _validate_run_id(run_id)
        run_dir = self.path(run_id)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with _locked(self.root / "submissions.lock"):
            record = _read_json(run_dir / "run.json")
            safe_states = {
                "prepared", "staged", "stage_incomplete", "not_staged",
                "simulator_complete_pending_measurements",
                "simulator_failed", "job_setup_failed", "input_hash_failure", "cancelled",
            }
            never_staged = record.get("remote_directory") is None and record.get("remote_state") in (None, "not_staged")
            if record.get("dispatch_claimed") and record.get("state") not in safe_states and not never_staged:
                raise DomainError(
                    "该任务已提交到远端且尚未确认终态，不能直接删除本地记录；请先取消作业或查询状态对账。",
                    "run_active_cannot_delete",
                )
            if run_dir.is_symlink():
                raise DomainError("Run directory cannot be a symlink.", "direct_state_invalid")
            if run_dir.exists():
                shutil.rmtree(run_dir)
            index = self._load_submissions()
            index["entries"].pop(run_id, None)
            _atomic_write(self._submissions_path(), index)
        return {"deleted": True, "run_id": run_id}

    def list(self, *, batch_map: dict[str, dict] | None = None) -> list[dict]:
        if not self.root.is_dir():
            return []
        bmap = batch_map if isinstance(batch_map, dict) else {}
        runs = []
        for entry in sorted(self.root.iterdir()):
            if not entry.is_dir() or entry.name.startswith(".") or entry.name.startswith("_"):
                continue
            run_file = entry / "run.json"
            if run_file.is_file() and not run_file.is_symlink():
                try:
                    raw_rec = _read_json(run_file)
                    runs.append(self._enrich_record(raw_rec, batch_info=bmap.get(raw_rec.get("run_id"))))
                except DomainError:
                    continue
        runs.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return runs

    def lookup_submission(self, submission_key: str) -> dict:
        require_id(submission_key, "submission key")
        with _locked(self.root / "submissions.lock"):
            index = self._load_submissions()
            entry = index["entries"].get(submission_key)
        if entry is None:
            return {
                "submission_key": submission_key,
                "found": False,
                "run_id": None,
                "run": None,
                "note": "No local direct-execution record matches this key; absence does not prove no remote process exists if state was deleted.",
            }
        run_record = self.read(entry["run_id"])
        return {
            "submission_key": submission_key,
            "found": True,
            "run_id": entry["run_id"],
            "manifest_sha256": entry["manifest_sha256"],
            "run": run_record,
        }

    def prepare(
        self,
        *,
        run_key: str,
        netlist_text: str,
        host_id: str | None = None,
        host_ids: list[str] | None = None,
        scan_hosts: bool = False,
        backend: str = "ssh_direct",
        simulator: str = "spectre",
        expected_version: str | None = None,
        pin_version: bool = True,
        support_files: dict | None = None,
        limits: dict | None = None,
        scheduler: dict | None = None,
        output_format: str = "psfascii",
        include_dirs: list[str] | None = None,
        remote_python: str = "/usr/bin/python3",
        remote_root: str = ".spiceowl-runs",
        license_environment: dict | None = None,
        remote_env: dict | None = None,
        source_commit: str | None = None,
        recovery_policy: dict | None = None,
        root_run_id: str | None = None,
        parent_run_id: str | None = None,
        attempt_number: int = 1,
        origin_submission_key: str | None = None,
        recovery_trigger: dict | None = None,
        project_name: str | None = None,
        library_name: str | None = None,
        cell_name: str | None = None,
        view_name: str | None = None,
        submission_group_id: str | None = None,
        submission_label: str | None = None,
        source_origin: str | None = None,
        batch_id: str | None = None,
    ) -> dict:
        """Freeze a direct execution plan locally; never launches a remote job."""
        run_id = _validate_run_id(run_key)
        resolved_root_id = _validate_run_id(root_run_id) if root_run_id is not None else run_id
        resolved_parent_id = _validate_run_id(parent_run_id) if parent_run_id is not None else None
        if type(attempt_number) is not int or not 1 <= attempt_number <= 16:
            raise DomainError("attempt_number must be an integer between 1 and 16.", "invalid_direct_plan")
        validated_policy = validate_recovery_policy(recovery_policy)
        hints = extract_netlist_organization_hints(netlist_text)
        clean_library = _clean_meta_text(library_name, "library_name") or hints.get("library_name")
        clean_project = _clean_meta_text(project_name, "project_name") or clean_library or hints.get("project_name")
        clean_cell = _clean_meta_text(cell_name, "cell_name") or hints.get("cell_name")
        clean_view = _clean_meta_text(view_name, "view_name", max_len=120) or hints.get("view_name")
        clean_group_id = _clean_meta_text(submission_group_id, "submission_group_id")
        clean_group_label = _clean_meta_text(submission_label, "submission_label")
        clean_origin = _clean_meta_text(source_origin, "source_origin", max_len=64)
        clean_batch_id = _clean_meta_text(batch_id, "batch_id", max_len=128)
        if source_commit is not None and (not isinstance(source_commit, str) or _COMMIT.fullmatch(source_commit) is None):
            raise DomainError("source_commit must be a lowercase 40-character Git SHA.", "invalid_direct_plan")
        if (host_id is None) == (host_ids is None):
            raise DomainError("Specify either an explicit host_id or a host_ids pool list, not both.", "invalid_direct_plan")

        validated_limits = transport._validate_limits(limits)
        placement = None
        if host_id is not None:
            selected_host_id = require_id(host_id, "host ID")
            host_record = self.hosts.inspect(selected_host_id)
        else:
            if scan_hosts:
                self.hosts.scan(host_ids, simulator=simulator)
            placement = self.hosts.recommend(
                {"cpu_count": validated_limits["cpu_count"],
                 "memory_bytes": validated_limits["memory_bytes"],
                 "simulator": simulator},
                host_ids,
            )
            selected_host_id = placement.get("recommended_host_id")
            if selected_host_id is None:
                raise DomainError(
                    "No eligible host met the requested CPU, memory, freshness, and simulator requirements.",
                    "no_eligible_host",
                )
            host_record = self.hosts.inspect(selected_host_id)

        resolved_version = expected_version
        if resolved_version is None and pin_version:
            last_probe = _as_dict(host_record.get("last_probe"))
            if _as_dict(last_probe.get("identity")).get("verified"):
                for tool in (last_probe.get("tools") or []):
                    if isinstance(tool, dict) and tool.get("kind") == simulator and tool.get("status") == "available" and tool.get("version"):
                        resolved_version = tool["version"]
                        break

        profile = profile_from_record(host_record)
        plan = transport.prepare(
            profile,
            run_id,
            netlist_text,
            backend=backend,
            simulator=simulator,
            expected_version=resolved_version,
            support_files=support_files,
            limits=validated_limits,
            scheduler=scheduler,
            output_format=output_format,
            include_dirs=include_dirs,
            remote_python=remote_python,
            remote_root=remote_root,
            license_environment=license_environment,
            remote_env=remote_env,
        )

        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with _locked(self.root / "submissions.lock"):
            index = self._load_submissions()
            existing = index["entries"].get(run_id)
            if existing is not None:
                if existing["manifest_sha256"] != plan["manifest_sha256"]:
                    raise DomainError(
                        "Run key already exists with different frozen inputs. Use a new run key for a different experiment.",
                        "conflict",
                    )
                return self.read(run_id)

            run_dir = self.path(run_id)
            run_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
            (run_dir / "receipts").mkdir(mode=0o700)
            _atomic_write(run_dir / "plan.json", plan)
            if placement is not None:
                _atomic_write(run_dir / "placement.json", placement)

            now = utcnow()
            record = {
                "schema": RUN_SCHEMA,
                "run_id": run_id,
                "submission_key": run_id,
                "root_run_id": resolved_root_id,
                "parent_run_id": resolved_parent_id,
                "attempt_number": attempt_number,
                "origin_submission_key": origin_submission_key or run_id,
                "recovery_policy": validated_policy,
                "recovery_trigger": deepcopy(recovery_trigger) if isinstance(recovery_trigger, dict) else None,
                "child_run_ids": [],
                "backend": backend,
                "simulator": simulator,
                "output_format": output_format,
                "host_id": selected_host_id,
                "source_commit": source_commit,
                "execution_site_sha256": plan["manifest_sha256"],
                "manifest_sha256": plan["manifest_sha256"],
                "expected_version": resolved_version,
                "limits": validated_limits,
                "scheduler_config": plan["manifest"]["scheduler"],
                "remote_root": plan["manifest"]["remote_root"],
                "remote_directory_name": plan["manifest"]["directory_name"],
                "remote_directory": None,
                "state": "prepared",
                "remote_state": "not_staged",
                "raw_remote_state": "not_staged",
                "status_transport": None,
                "simulator_exit_code": None,
                "raw_exit_code": None,
                "dispatch_claimed": False,
                "scheduler": None,
                "scheduler_observation": None,
                "oom_evidence": None,
                "termination_reason": None,
                "cancel_receipts": [],
                "collection_phase": None,
                "collection": None,
                "report_path": None,
                "last_error": None,
                "created_at": now,
                "updated_at": now,
                "submitted_at": None,
                "observed_at": None,
                "project_name": clean_project,
                "library_name": clean_library,
                "cell_name": clean_cell,
                "view_name": clean_view,
                "submission_group_id": clean_group_id,
                "submission_label": clean_group_label,
                "source_origin": clean_origin,
                "batch_id": clean_batch_id,
            }
            record["organization"] = build_run_organization(record, netlist_head=netlist_text[:4096])
            _atomic_write(run_dir / "run.json", record)
            index["entries"][run_id] = {
                "run_id": run_id,
                "host_id": selected_host_id,
                "backend": backend,
                "manifest_sha256": plan["manifest_sha256"],
                "created_at": now,
            }
            _atomic_write(self._submissions_path(), index)
        return deepcopy(record)

    def _load_plan(self, run_id: str) -> dict:
        plan = _read_json(self.path(run_id) / "plan.json")
        transport._validate(plan)
        return plan

    def _apply_observation(self, record: dict, receipt: dict, *, connected: bool, blocked: bool = False) -> dict:
        rec = _as_dict(receipt)
        now = _monotonic_observed_at(record.get("observed_at"))
        record["updated_at"] = now
        if connected:
            record["observed_at"] = now
            record["status_transport"] = "connected"
            record["last_error"] = None
            state, remote_state, exit_code = _map_adapter_state(rec, launch_claimed=record.get("dispatch_claimed", False))
            record["state"] = state
            record["remote_state"] = remote_state
            record["raw_remote_state"] = rec.get("state")
            if exit_code is not None:
                record["simulator_exit_code"] = exit_code
            result = _as_dict(rec.get("result"))
            if "exit_code" in result:
                record["raw_exit_code"] = result.get("exit_code")
            if rec.get("remote_directory"):
                record["remote_directory"] = rec["remote_directory"]
            if rec.get("scheduler"):
                record["scheduler"] = rec["scheduler"]
            if rec.get("scheduler_observation"):
                record["scheduler_observation"] = rec["scheduler_observation"]
            if result.get("oom_evidence"):
                record["oom_evidence"] = result["oom_evidence"]
            if result.get("termination_reason"):
                record["termination_reason"] = result["termination_reason"]
            accepted = _as_dict(rec.get("accepted"))
            if accepted.get("dispatch_claimed"):
                record["dispatch_claimed"] = True
                if record.get("submitted_at") is None:
                    record["submitted_at"] = accepted.get("accepted_at") or now
        else:
            record["status_transport"] = "blocked" if blocked else "disconnected"
            err = rec.get("error")
            record["last_error"] = err
            if blocked:
                record["state"] = "input_hash_failure" if _as_dict(err).get("code") in ("input_hash_mismatch", "receipt_mismatch") else "reconcile_required"
            elif record.get("dispatch_claimed") and record.get("state") in ("prepared", "staged", "launch_outcome_unknown"):
                record["state"] = "launch_outcome_unknown"
        return record

    def stage(self, run_id: str) -> dict:
        run_id = _validate_run_id(run_id)
        with _locked(self.path(run_id) / "run.lock"):
            record = self.read(run_id)
            plan = self._load_plan(run_id)
            receipt = transport.stage(plan)
            self._save_receipt(run_id, "stage", receipt)
            connected, blocked = _receipt_transport_flags(receipt)
            self._apply_observation(record, receipt, connected=connected, blocked=blocked)
            _atomic_write(self.path(run_id) / "run.json", record)
            return deepcopy(record)

    def launch(self, run_id: str, *, recheck_resources: bool | None = None) -> dict:
        """Persist dispatch claim locally, then launch at most once on remote host."""
        run_id = _validate_run_id(run_id)
        with _locked(self.path(run_id) / "run.lock"):
            record = self.read(run_id)
            plan = self._load_plan(run_id)
            if record.get("dispatch_claimed"):
                raise DomainError(
                    "Launch was already claimed for this run key. Query status to reconcile instead of launching again.",
                    "launch_already_claimed",
                )
            host_id = plan["manifest"]["profile"]["host_id"]
            current_host = self.hosts.inspect(host_id)
            if profile_from_record(current_host) != plan["manifest"]["profile"]:
                raise DomainError("Registered host profile changed since preparation.", "host_profile_changed")

            should_recheck = (plan["manifest"]["backend"] == "ssh_direct") if recheck_resources is None else bool(recheck_resources)
            if should_recheck:
                observation = self.hosts.resources(host_id)
                placement = self.hosts.recommend(
                    {"cpu_count": plan["manifest"]["limits"]["cpu_count"],
                     "memory_bytes": plan["manifest"]["limits"]["memory_bytes"],
                     "simulator": plan["manifest"]["simulator"]},
                    [host_id],
                )
                self._save_receipt(run_id, "prelaunch", {"observation": observation, "placement": placement})
                if _as_dict(placement).get("recommended_host_id") != host_id:
                    raise DomainError(
                        "Selected host no longer meets the required observed resource headroom.",
                        "host_headroom_insufficient",
                    )

            # Persist durable claim BEFORE remote SSH call so a lost response never permits replay.
            now = utcnow()
            record["dispatch_claimed"] = True
            record["state"] = "launch_outcome_unknown"
            record["submitted_at"] = now
            record["updated_at"] = now
            _atomic_write(self.path(run_id) / "run.json", record)

            receipt = transport.launch(plan)
            self._save_receipt(run_id, "launch", receipt)
            connected, blocked = _receipt_transport_flags(receipt)
            self._apply_observation(record, receipt, connected=connected, blocked=blocked)
            _atomic_write(self.path(run_id) / "run.json", record)
            return deepcopy(record)

    def stage_and_launch(self, run_id: str, *, recheck_resources: bool = False) -> dict:
        """Persist dispatch claim locally, then stage inputs and launch once in a single SSH exchange."""
        run_id = _validate_run_id(run_id)
        with _locked(self.path(run_id) / "run.lock"):
            record = self.read(run_id)
            plan = self._load_plan(run_id)
            if record.get("dispatch_claimed"):
                raise DomainError(
                    "Launch was already claimed for this run key. Query status to reconcile instead of launching again.",
                    "launch_already_claimed",
                )
            host_id = plan["manifest"]["profile"]["host_id"]
            current_host = self.hosts.inspect(host_id)
            if profile_from_record(current_host) != plan["manifest"]["profile"]:
                raise DomainError("Registered host profile changed since preparation.", "host_profile_changed")

            if bool(recheck_resources):
                observation = self.hosts.resources(host_id)
                placement = self.hosts.recommend(
                    {"cpu_count": plan["manifest"]["limits"]["cpu_count"],
                     "memory_bytes": plan["manifest"]["limits"]["memory_bytes"],
                     "simulator": plan["manifest"]["simulator"]},
                    [host_id],
                )
                self._save_receipt(run_id, "prelaunch", {"observation": observation, "placement": placement})
                if _as_dict(placement).get("recommended_host_id") != host_id:
                    raise DomainError(
                        "Selected host no longer meets the required observed resource headroom.",
                        "host_headroom_insufficient",
                    )

            # Persist durable claim BEFORE remote SSH call so a lost response never permits replay.
            now = utcnow()
            record["dispatch_claimed"] = True
            record["state"] = "launch_outcome_unknown"
            record["submitted_at"] = now
            record["updated_at"] = now
            _atomic_write(self.path(run_id) / "run.json", record)

            receipt = transport.stage_and_launch(plan)
            self._save_receipt(run_id, "stage_and_launch", receipt)
            connected, blocked = _receipt_transport_flags(receipt)
            self._apply_observation(record, receipt, connected=connected, blocked=blocked)
            _atomic_write(self.path(run_id) / "run.json", record)
            return deepcopy(record)

    def submit(self, *, recheck_resources: bool = False, **prepare_kwargs) -> dict:
        """Freeze inputs locally, then stage and launch remotely in a single SSH exchange."""
        prepared = self.prepare(**prepare_kwargs)
        return self.stage_and_launch(prepared["run_id"], recheck_resources=recheck_resources)

    def status(self, run_id: str) -> dict:
        """Query the bound remote execution identity without re-launching."""
        run_id = _validate_run_id(run_id)
        with _locked(self.path(run_id) / "run.lock"):
            record = self.read(run_id)
            plan = self._load_plan(run_id)
            receipt = transport.status(plan)
            self._save_receipt(run_id, "status", receipt)
            connected, blocked = _receipt_transport_flags(receipt)
            self._apply_observation(record, receipt, connected=connected, blocked=blocked)
            _atomic_write(self.path(run_id) / "run.json", record)
            return deepcopy(record)

    def cancel(self, run_id: str, *, action_key: str | None = None) -> dict:
        """Cancel only the verified owned process group or scheduler job."""
        run_id = _validate_run_id(run_id)
        key = require_id(action_key, "cancel action key") if action_key is not None else new_id("cancel")
        with _locked(self.path(run_id) / "run.lock"):
            record = self.read(run_id)
            plan = self._load_plan(run_id)
            prior_receipts = record.get("cancel_receipts") if isinstance(record.get("cancel_receipts"), list) else []
            for existing in prior_receipts:
                if isinstance(existing, dict) and existing.get("action_key") == key:
                    return deepcopy(existing)
            receipt = _as_dict(transport.cancel(plan, key))
            self._save_receipt(run_id, "cancel", receipt)
            action_record = {
                "action_key": key,
                "run_id": run_id,
                "requested_at": utcnow(),
                "outcome": receipt.get("outcome") or "unknown",
                "state": receipt.get("state"),
                "reason": receipt.get("reason"),
                "job_id": receipt.get("job_id"),
                "signaled": receipt.get("signaled"),
                "error": receipt.get("error"),
            }
            record["cancel_receipts"] = (prior_receipts + [action_record])[-32:]
            record["updated_at"] = utcnow()
            _atomic_write(self.path(run_id) / "run.json", record)
            return deepcopy(action_record)

    def collect(self, run_id: str, *, max_collection_bytes: int = transport.MAX_COLLECTION_BYTES) -> dict:
        """Collect bounded terminal artifacts, verify hashes, and build report."""
        run_id = _validate_run_id(run_id)
        with _locked(self.path(run_id) / "run.lock"):
            record = self.read(run_id)
            plan = self._load_plan(run_id)
            if record.get("collection_phase") == "complete" and record.get("collection"):
                return deepcopy(record)
            receipt = _as_dict(transport.collect(plan, max_collection_bytes=max_collection_bytes))
            if receipt.get("error") is not None:
                self._save_receipt(run_id, "collect-error", receipt)
                err = _as_dict(receipt.get("error"))
                code = err.get("code") or "collection_failed"
                msg = err.get("message") or f"Remote collection failed: {code}"
                raise DomainError(msg, code)

            transport._verify_collection(plan, receipt, max_collection_bytes=max_collection_bytes)
            remote_receipt = receipt["receipt"]
            self._apply_observation(record, remote_receipt, connected=True)

            destination = self.path(run_id) / "collected"
            destination.mkdir(parents=True, exist_ok=True, mode=0o700)
            file_inventory = []
            contents = {}
            for item in receipt["files"]:
                raw = base64.b64decode(item["data_base64"], validate=True)
                target = destination / item["name"]
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                target.write_bytes(raw)
                contents[item["name"]] = raw
                file_inventory.append({
                    "name": item["name"],
                    "bytes": item["bytes"],
                    "sha256": item["sha256"],
                })

            collected_at = utcnow()
            skipped_files = receipt.get("skipped_files") if isinstance(receipt.get("skipped_files"), list) else []
            record["collection_phase"] = "complete"
            record["collection"] = {
                "collected_at": collected_at,
                "directory": str(destination),
                "file_count": len(file_inventory),
                "files": file_inventory,
                "skipped_files": skipped_files,
                "collection_complete": bool(receipt.get("collection_complete", True)),
                "total_bytes": receipt["total_bytes"],
                "raw_present": bool(receipt.get("raw_present", False)),
                "input_and_manifest_verified": True,
            }

            report_obj = self._build_report_from_collected(record, contents)
            report_path = self.path(run_id) / "report.json"
            _atomic_write(report_path, report_obj)
            record["report_path"] = str(report_path)

            # Build lightweight waveform index locally (and query remote in-place if large raw files were skipped)
            wf_index = psf_binary.index_waveform_directory(destination / "results.raw")
            for ds in wf_index["datasets"]:
                ds["source"] = "local_collected"
            skipped_raw = [
                s for s in skipped_files
                if isinstance(s, dict) and str(s.get("name", "")).startswith("results.raw/")
            ]
            if skipped_raw:
                try:
                    remote_idx = _as_dict(transport.waveform_index(plan))
                    local_names = {ds["dataset"] for ds in wf_index["datasets"]}
                    for ds in (remote_idx.get("datasets") or []):
                        if isinstance(ds, dict) and ds.get("dataset") not in local_names:
                            ds["source"] = "remote_in_place"
                            wf_index["datasets"].append(ds)
                    wf_index["total_raw_bytes"] = max(
                        int(wf_index.get("total_raw_bytes") or 0),
                        int(remote_idx.get("total_raw_bytes") or 0),
                    )
                except DomainError:
                    pass
            wf_index_path = self.path(run_id) / "waveform_index.json"
            _atomic_write(wf_index_path, wf_index)
            record["waveform_index_path"] = str(wf_index_path)

            record["updated_at"] = utcnow()
            _atomic_write(self.path(run_id) / "run.json", record)
        self._refresh_recovery_report_if_tracked(record.get("root_run_id") or run_id)
        return deepcopy(record)

    def _build_report_from_collected(self, record: dict, contents: dict[str, bytes]) -> dict:
        if record["simulator"] == "spectre":
            log_bytes = contents.get("spectre.log", b"")
            log_text = log_bytes.decode("utf-8", errors="replace")
            log_complete = "spectre.log" in contents
            return build_spectre_report(
                log_text,
                execution_state=_execution_state_for_report(record),
                exit_code=record.get("raw_exit_code") if record.get("raw_exit_code") is not None else record.get("simulator_exit_code"),
                oom_evidence=record.get("oom_evidence"),
                log_complete=log_complete,
                run_id=record["run_id"],
            )
        # ngspice or non-Spectre simulator summary (honest separate dimensions).
        log_bytes = contents.get("ngspice.log", b"")
        return {
            "schema": "spiceowl.direct-report.v1",
            "run_id": record["run_id"],
            "simulator": record["simulator"],
            "advisory_only": True,
            "execution_status": {
                "status": _execution_state_for_report(record),
                "exit_code": record.get("simulator_exit_code"),
            },
            "measurement_validity": {"status": "unknown", "reason": "no_measurement_recipe_applied"},
            "functional_performance": {"status": "unknown", "reason": "no_specification_evaluated"},
            "log_present": "ngspice.log" in contents,
            "log_sha256": hashlib.sha256(log_bytes).hexdigest() if "ngspice.log" in contents else None,
        }

    def report(self, run_id: str) -> dict:
        """Read the persisted five-dimensional report for a collected run."""
        run_id = _validate_run_id(run_id)
        report_path = self.path(run_id) / "report.json"
        if not report_path.is_file():
            raise DomainError("No collected report is available yet. Collect the terminal run first.", "report_unavailable")
        return deepcopy(_read_json(report_path))

    def ai_report_html_path(self, run_id: str) -> str:
        """Return the local filesystem path for a run's self-contained HTML report."""
        return str(self.path(_validate_run_id(run_id)) / "ai_report.html")

    def save_ai_report(
        self,
        run_id: str,
        *,
        html: str | None = None,
        markdown: str | None = None,
        title: str | None = None,
        summary: str | None = None,
        source: str = "agent",
    ) -> dict:
        """Persist an Agent-generated HTML and/or Markdown simulation report alongside a run record."""
        run_id = _validate_run_id(run_id)
        clean_html, clean_markdown, clean_title, clean_summary, clean_source = _validate_report_content(
            html=html,
            markdown=markdown,
            title=title,
            summary=summary,
            source=source,
            max_html_bytes=MAX_AI_REPORT_HTML_BYTES,
            fallback_title=f"AI 仿真分析报告 · {run_id}",
            err_code="invalid_ai_report",
        )

        run_dir = self.path(run_id)
        html_file = run_dir / "ai_report.html"
        md_file = run_dir / "ai_report.md"
        meta_file = run_dir / "ai_report.json"

        with _locked(run_dir / "run.lock"):
            record = self.read(run_id)
            prev_meta = {}
            if meta_file.is_file() and not meta_file.is_symlink():
                try:
                    prev_meta = _as_dict(_read_json(meta_file))
                except DomainError:
                    prev_meta = {}
            now = utcnow()
            generated_at = prev_meta.get("generated_at") or now

            if clean_html is not None:
                _atomic_write_text(html_file, clean_html)
            if clean_markdown is not None:
                _atomic_write_text(md_file, clean_markdown)

            has_html = clean_html is not None or (html_file.is_file() and not html_file.is_symlink())
            has_markdown = clean_markdown is not None or (md_file.is_file() and not md_file.is_symlink())

            ai_report_meta = {
                "schema": AI_REPORT_SCHEMA,
                "scope": "run",
                "run_id": run_id,
                "title": clean_title,
                "summary": clean_summary,
                "has_html": has_html,
                "has_markdown": has_markdown,
                "html_path": str(html_file),
                "generated_at": generated_at,
                "updated_at": now,
                "source": clean_source,
            }
            _atomic_write(meta_file, ai_report_meta)
            record["ai_report"] = {
                "available": True,
                "has_html": has_html,
                "has_markdown": has_markdown,
                "title": ai_report_meta["title"],
                "summary": ai_report_meta["summary"],
                "generated_at": ai_report_meta["generated_at"],
                "updated_at": ai_report_meta["updated_at"],
                "source": ai_report_meta["source"],
            }
            record["updated_at"] = now
            _atomic_write(run_dir / "run.json", record)
        return deepcopy(ai_report_meta)

    def read_ai_report(self, run_id: str) -> dict | None:
        """Read a persisted AI simulation report (merging direct disk edits to ai_report.html/md)."""
        run_id = _validate_run_id(run_id)
        run_dir = self.path(run_id)
        meta_path = run_dir / "ai_report.json"
        html_path = run_dir / "ai_report.html"
        md_path = run_dir / "ai_report.md"

        meta: dict = {}
        if meta_path.is_file() and not meta_path.is_symlink():
            meta = _as_dict(_read_json(meta_path))

        html_text = _read_bounded_text(html_path, MAX_AI_REPORT_HTML_BYTES, err_code="invalid_ai_report")
        md_text = _read_bounded_text(md_path, MAX_AI_REPORT_MARKDOWN_BYTES, err_code="invalid_ai_report")
        if html_text is None and isinstance(meta.get("html"), str) and meta["html"].strip():
            html_text = meta["html"]
        if md_text is None and isinstance(meta.get("markdown"), str) and meta["markdown"].strip():
            md_text = meta["markdown"]

        if not meta and html_text is None and md_text is None:
            return None

        disk_mtime = _file_mtime_iso(html_path) or _file_mtime_iso(md_path)
        generated_at = meta.get("generated_at") or disk_mtime or utcnow()
        updated_at = meta.get("updated_at") or generated_at
        if disk_mtime and disk_mtime > updated_at:
            updated_at = disk_mtime

        title = meta.get("title") or _extract_report_title(html_text, md_text, f"AI 仿真分析报告 · {run_id}")
        summary = meta.get("summary") if isinstance(meta.get("summary"), str) else _extract_report_summary(md_text)

        return {
            "schema": meta.get("schema") or AI_REPORT_SCHEMA,
            "scope": "run",
            "run_id": run_id,
            "title": title,
            "summary": summary,
            "has_html": html_text is not None,
            "has_markdown": md_text is not None,
            "html": html_text,
            "markdown": md_text,
            "html_path": str(html_path),
            "generated_at": generated_at,
            "updated_at": updated_at,
            "source": meta.get("source") or "agent",
        }

    # --- Project-level & Testbench-level Scope Summary Reports ---

    @staticmethod
    def scope_key(scope: str, project: str, testbench: str | None = None) -> str:
        """Compute deterministic storage key for a project or testbench summary report."""
        return scope_key(scope, project, testbench)

    def _scope_reports_dir(self) -> Path:
        return self.root / "_scope_reports"

    def _scope_reports_index_path(self) -> Path:
        return self._scope_reports_dir() / "index.json"

    def _resolve_scope_identity(
        self,
        scope: str,
        project: str,
        testbench: str | None = None,
    ) -> tuple[str, str, str | None, str]:
        key = scope_key(scope, project, testbench)
        clean_proj = project.strip()
        clean_tb = testbench.strip() if (scope == "testbench" and isinstance(testbench, str)) else None
        return scope, clean_proj, clean_tb, key

    def scope_report_html_path(self, *, scope: str, project: str, testbench: str | None = None) -> str:
        """Return the local filesystem path for a Project or Testbench HTML report."""
        _, _, _, key = self._resolve_scope_identity(scope, project, testbench)
        return str(self._scope_reports_dir() / key / "report.html")

    def save_scope_report(
        self,
        *,
        scope: str,
        project: str,
        testbench: str | None = None,
        title: str | None = None,
        summary: str | None = None,
        html: str | None = None,
        markdown: str | None = None,
        run_ids: list[str] | None = None,
        source: str = "agent",
    ) -> dict:
        """Persist a Project-level or Testbench-level HTML/Markdown summary report."""
        clean_scope, clean_proj, clean_tb, key = self._resolve_scope_identity(scope, project, testbench)
        fallback_title = (
            f"{clean_proj} / {clean_tb} 综合仿真报告"
            if clean_scope == "testbench"
            else f"{clean_proj} 项目综合仿真报告"
        )
        clean_html, clean_markdown, clean_title, clean_summary, clean_source = _validate_report_content(
            html=html,
            markdown=markdown,
            title=title,
            summary=summary,
            source=source,
            max_html_bytes=MAX_SCOPE_REPORT_HTML_BYTES,
            fallback_title=fallback_title,
            err_code="invalid_scope_report",
        )

        clean_run_ids: list[str] = []
        if run_ids is not None:
            if not isinstance(run_ids, list):
                raise DomainError("run_ids must be a list of run ID strings.", "invalid_scope_report")
            seen_rids: set[str] = set()
            for item in run_ids[:500]:
                if not isinstance(item, str) or not item.strip():
                    raise DomainError("Each run_id in run_ids must be a non-empty string.", "invalid_scope_report")
                rid = item.strip()[:128]
                if rid not in seen_rids:
                    seen_rids.add(rid)
                    clean_run_ids.append(rid)

        scope_root = self._scope_reports_dir()
        entry_dir = scope_root / key
        html_file = entry_dir / "report.html"
        md_file = entry_dir / "report.md"
        meta_file = entry_dir / "report.json"

        with _locked(scope_root / "index.lock"):
            entry_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            prev_meta = {}
            if meta_file.is_file() and not meta_file.is_symlink():
                try:
                    prev_meta = _as_dict(_read_json(meta_file))
                except DomainError:
                    prev_meta = {}
            now = utcnow()
            generated_at = prev_meta.get("generated_at") or now

            if clean_html is not None:
                _atomic_write_text(html_file, clean_html)
            if clean_markdown is not None:
                _atomic_write_text(md_file, clean_markdown)

            has_html = clean_html is not None or (html_file.is_file() and not html_file.is_symlink())
            has_markdown = clean_markdown is not None or (md_file.is_file() and not md_file.is_symlink())

            meta = {
                "schema": AI_REPORT_SCHEMA,
                "scope": clean_scope,
                "scope_key": key,
                "project": clean_proj,
                "testbench": clean_tb,
                "title": clean_title,
                "summary": clean_summary,
                "run_ids": clean_run_ids,
                "run_count": len(clean_run_ids),
                "has_html": has_html,
                "has_markdown": has_markdown,
                "html_path": str(html_file),
                "generated_at": generated_at,
                "updated_at": now,
                "source": clean_source,
            }
            _atomic_write(meta_file, meta)

            idx_path = self._scope_reports_index_path()
            if idx_path.is_file() and not idx_path.is_symlink():
                index = _as_dict(_read_json(idx_path))
            else:
                index = {"schema": SCOPE_REPORT_INDEX_SCHEMA, "reports": {}}
            entries = index.get("reports")
            if not isinstance(entries, dict):
                entries = {}
                index["reports"] = entries
            entries[key] = deepcopy(meta)
            index["schema"] = SCOPE_REPORT_INDEX_SCHEMA
            _atomic_write(idx_path, index)
        return deepcopy(meta)

    def list_scope_reports(self) -> list[dict]:
        """List persisted Project-level and Testbench-level report metadata sorted newest first."""
        idx_path = self._scope_reports_index_path()
        if not idx_path.exists() and not idx_path.is_symlink():
            return []
        index = _as_dict(_read_json(idx_path))
        rows: list[dict] = []
        for raw_row in _as_dict(index.get("reports")).values():
            if not isinstance(raw_row, dict):
                continue
            row = deepcopy(raw_row)
            row.pop("html", None)
            row.pop("markdown", None)
            key = row.get("scope_key")
            if isinstance(key, str) and key:
                entry_dir = self._scope_reports_dir() / key
                html_file = entry_dir / "report.html"
                md_file = entry_dir / "report.md"
                row["has_html"] = bool(row.get("has_html") or (html_file.is_file() and not html_file.is_symlink()))
                row["has_markdown"] = bool(row.get("has_markdown") or (md_file.is_file() and not md_file.is_symlink()))
            rows.append(row)
        rows.sort(key=lambda item: item.get("updated_at") or item.get("generated_at") or "", reverse=True)
        return rows

    def read_scope_report(
        self,
        *,
        scope: str,
        project: str,
        testbench: str | None = None,
    ) -> dict | None:
        """Read a Project-level or Testbench-level report (merging direct disk edits to report.html/md)."""
        clean_scope, clean_proj, clean_tb, key = self._resolve_scope_identity(scope, project, testbench)
        entry_dir = self._scope_reports_dir() / key
        meta_path = entry_dir / "report.json"
        html_path = entry_dir / "report.html"
        md_path = entry_dir / "report.md"

        meta: dict = {}
        if meta_path.is_file() and not meta_path.is_symlink():
            meta = _as_dict(_read_json(meta_path))

        html_text = _read_bounded_text(html_path, MAX_SCOPE_REPORT_HTML_BYTES, err_code="invalid_scope_report")
        md_text = _read_bounded_text(md_path, MAX_AI_REPORT_MARKDOWN_BYTES, err_code="invalid_scope_report")
        if html_text is None and isinstance(meta.get("html"), str) and meta["html"].strip():
            html_text = meta["html"]
        if md_text is None and isinstance(meta.get("markdown"), str) and meta["markdown"].strip():
            md_text = meta["markdown"]

        if not meta and html_text is None and md_text is None:
            return None

        disk_mtime = _file_mtime_iso(html_path) or _file_mtime_iso(md_path)
        generated_at = meta.get("generated_at") or disk_mtime or utcnow()
        updated_at = meta.get("updated_at") or generated_at
        if disk_mtime and disk_mtime > updated_at:
            updated_at = disk_mtime

        fallback_title = (
            f"{clean_proj} / {clean_tb} 综合仿真报告"
            if clean_scope == "testbench"
            else f"{clean_proj} 项目综合仿真报告"
        )
        title = meta.get("title") or _extract_report_title(html_text, md_text, fallback_title)
        summary = meta.get("summary") if isinstance(meta.get("summary"), str) else _extract_report_summary(md_text)
        run_ids = list(meta.get("run_ids") or []) if isinstance(meta.get("run_ids"), list) else []

        return {
            "schema": meta.get("schema") or AI_REPORT_SCHEMA,
            "scope": clean_scope,
            "scope_key": key,
            "project": meta.get("project") or clean_proj,
            "testbench": meta.get("testbench") if clean_scope == "testbench" else None,
            "title": title,
            "summary": summary,
            "run_ids": run_ids,
            "run_count": len(run_ids),
            "has_html": html_text is not None,
            "has_markdown": md_text is not None,
            "html": html_text,
            "markdown": md_text,
            "html_path": str(html_path),
            "generated_at": generated_at,
            "updated_at": updated_at,
            "source": meta.get("source") or "agent",
        }

    def mark_completion_handoff(self, run_id: str, handoff: dict) -> dict:
        """Persist completion handoff receipt metadata onto run.json."""
        run_id = _validate_run_id(run_id)
        if not isinstance(handoff, dict):
            raise DomainError("Completion handoff payload must be a JSON object.", "invalid_direct_plan")
        run_dir = self.path(run_id)
        with _locked(run_dir / "run.lock"):
            record = self.read(run_id)
            record["completion_handoff"] = deepcopy(handoff)
            record["updated_at"] = utcnow()
            _atomic_write(run_dir / "run.json", record)
            return deepcopy(record)

    def monitor(self, *, interval_seconds=30, max_backoff_seconds=300) -> RuntimeMonitor:
        """Create a RuntimeMonitor bound directly to this manager."""
        return RuntimeMonitor(self, interval_seconds=interval_seconds, max_backoff_seconds=max_backoff_seconds)

    # --- M4 Bounded Recovery & Searchable Recovery Reports ---

    def _recovery_dir(self) -> Path:
        return self.root / "recovery_reports"

    def _recovery_index_path(self) -> Path:
        return self._recovery_dir() / "index.json"

    def set_recovery_policy(self, run_id: str, policy: dict) -> dict:
        """Update and persist the bounded recovery policy on an existing run."""
        run_id = _validate_run_id(run_id)
        validated = validate_recovery_policy(policy)
        with _locked(self.path(run_id) / "run.lock"):
            record = self.read(run_id)
            record["recovery_policy"] = validated
            record["updated_at"] = utcnow()
            _atomic_write(self.path(run_id) / "run.json", record)
            return deepcopy(record)

    def lineage(self, run_or_root_id: str) -> tuple[str, list[dict]]:
        """Return (root_run_id, ordered attempt records) for a run's lineage."""
        target = self.read(run_or_root_id)
        root_id = target.get("root_run_id") or target["run_id"]
        runs = [
            item for item in self.list()
            if (item.get("root_run_id") or item["run_id"]) == root_id
        ]
        runs.sort(key=lambda r: (r.get("attempt_number") or 1, r.get("created_at") or ""))
        return root_id, runs

    def _decisions_path(self, root_run_id: str) -> Path:
        return self._recovery_dir() / f"{_validate_run_id(root_run_id)}.decisions.json"

    def _load_decisions(self, root_run_id: str) -> list[dict]:
        path = self._decisions_path(root_run_id)
        if not path.exists() and not path.is_symlink():
            return []
        data = _read_json(path)
        return list(data.get("decisions") or []) if isinstance(data, dict) else []

    def _save_recovery_artifacts(self, root_run_id: str, decisions: list[dict]) -> dict:
        root_id, lineage_runs = self.lineage(root_run_id)
        reports_by_run: dict[str, dict | None] = {}
        netlist_hashes: dict[str, str] = {}
        for item in lineage_runs:
            rid = item["run_id"]
            try:
                reports_by_run[rid] = self.report(rid)
            except DomainError:
                reports_by_run[rid] = None
            try:
                plan = self._load_plan(rid)
                netlist_hashes[rid] = plan["manifest"]["input_hashes"]["input.scs"]
            except DomainError:
                pass
        latest_policy = lineage_runs[-1].get("recovery_policy") or dict(DEFAULT_RECOVERY_POLICY)
        rec_dir = self._recovery_dir()
        rec_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        md_path = rec_dir / f"{root_id}.md"
        report_obj = build_recovery_report(
            root_id, lineage_runs, reports_by_run, decisions, netlist_hashes,
            policy=latest_policy, markdown_path=str(md_path),
        )
        markdown = render_recovery_report_markdown(report_obj)
        with _locked(rec_dir / "index.lock"):
            _atomic_write(self._decisions_path(root_id), {"root_run_id": root_id, "decisions": decisions})
            _atomic_write(rec_dir / f"{root_id}.json", report_obj)
            temp_md = md_path.with_name(f".{md_path.name}.{uuid.uuid4().hex}.tmp")
            fd = os.open(temp_md, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(markdown)
                    stream.flush()
                    os.fsync(stream.fileno())
                temp_md.replace(md_path)
            finally:
                temp_md.unlink(missing_ok=True)
            idx_path = self._recovery_index_path()
            if idx_path.is_file() and not idx_path.is_symlink():
                index = _as_dict(_read_json(idx_path))
            else:
                index = {"schema": RECOVERY_INDEX_SCHEMA, "reports": {}}
            entries = index.get("reports")
            if not isinstance(entries, dict):
                entries = {}
                index["reports"] = entries
            entries[root_id] = {
                "report_id": root_id,
                "root_run_id": root_id,
                "latest_run_id": report_obj["latest_run_id"],
                "outcome": report_obj["outcome"],
                "summary_zh": report_obj["summary_zh"],
                "backend": report_obj["backend"],
                "simulator": report_obj["simulator"],
                "hosts": sorted({a["host_id"] for a in report_obj["attempts"]}),
                "failure_classes": sorted({a["failure_class"] for a in report_obj["attempts"]}),
                "attempts_count": len(report_obj["attempts"]),
                "attempts_used": report_obj["budget_usage"]["attempts_used"],
                "updated_at": report_obj["updated_at"],
                "markdown_path": str(md_path),
            }
            _atomic_write(idx_path, index)
        return report_obj

    def _refresh_recovery_report_if_tracked(self, root_run_id: str) -> None:
        if self._decisions_path(root_run_id).is_file() or (self._recovery_dir() / f"{root_run_id}.json").is_file():
            self._save_recovery_artifacts(root_run_id, self._load_decisions(root_run_id))

    def list_recovery_reports(self, *, query: str | None = None, outcome: str | None = None) -> list[dict]:
        """Search and list persisted bounded recovery reports without network I/O."""
        idx_path = self._recovery_index_path()
        if not idx_path.exists() and not idx_path.is_symlink():
            return []
        index = _as_dict(_read_json(idx_path))
        rows = [row for row in _as_dict(index.get("reports")).values() if isinstance(row, dict)]
        if outcome:
            rows = [row for row in rows if row.get("outcome") == outcome]
        if query:
            needle = query.strip().lower()
            if needle:
                def matches(row):
                    haystack = " ".join([
                        str(row.get("root_run_id") or ""),
                        str(row.get("latest_run_id") or ""),
                        str(row.get("summary_zh") or ""),
                        str(row.get("backend") or ""),
                        str(row.get("simulator") or ""),
                        " ".join(str(x) for x in (row.get("hosts") or [])),
                        " ".join(str(x) for x in (row.get("failure_classes") or [])),
                    ]).lower()
                    return needle in haystack
                rows = [row for row in rows if matches(row)]
        rows.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        return rows

    def get_recovery_report(self, run_or_root_id: str) -> dict:
        """Read one recovery report (JSON + Markdown) by either root or child run ID."""
        root_id = _validate_run_id(run_or_root_id)
        if not (self._recovery_dir() / f"{root_id}.json").is_file():
            try:
                rec = self.read(root_id)
                root_id = rec.get("root_run_id") or root_id
            except DomainError:
                pass
        json_path = self._recovery_dir() / f"{root_id}.json"
        if not json_path.is_file():
            raise DomainError("No recovery report exists for this run lineage yet.", "not_found")
        report_obj = _read_json(json_path)
        md_path = self._recovery_dir() / f"{root_id}.md"
        markdown = md_path.read_text(encoding="utf-8") if md_path.is_file() else render_recovery_report_markdown(report_obj)
        return {"report": report_obj, "markdown": markdown}

    def evaluate_and_recover(
        self,
        run_id: str,
        *,
        execute: bool = True,
        monitor: RuntimeMonitor | None = None,
        now: datetime | None = None,
    ) -> dict:
        """Evaluate bounded M4 recovery for a terminal run, optionally spawn/launch child attempt, and persist report."""
        run_id = _validate_run_id(run_id)
        record = self.read(run_id)
        root_id, lineage_runs = self.lineage(run_id)
        try:
            run_report = self.report(run_id)
        except DomainError:
            run_report = None

        policy = validate_recovery_policy(record.get("recovery_policy"))

        # Prevent duplicate child creation from the same failed attempt.
        existing_children = record.get("child_run_ids") or []
        if existing_children:
            child_run = self.read(existing_children[-1])
            rec_report = self._save_recovery_artifacts(root_id, self._load_decisions(root_id))
            return {
                "decision": {
                    "result": "already_recovered",
                    "action": "existing_child_attempt",
                    "source_run_id": run_id,
                    "child_run_id": child_run["run_id"],
                    "reason": "This attempt already spawned a bounded recovery child attempt.",
                },
                "child_run": child_run,
                "recovery_report": rec_report,
            }

        # Compute host placement recommendation only if OOM recovery needs memory scaling.
        classification = classify_failure(record, run_report)
        recommendation = None
        if policy["enabled"] and classification["failure_class"] == "confirmed_oom" and policy["retry_on_oom"]:
            prev_mem = int(record["limits"]["memory_bytes"])
            next_mem = min(int(policy["max_memory_bytes"]), int(math.ceil(prev_mem * policy["oom_memory_multiplier"])))
            if next_mem > prev_mem:
                candidate_ids = policy["candidate_host_ids"] or [h["host_id"] for h in self.hosts.list()[:32]]
                if candidate_ids:
                    try:
                        recommendation = self.hosts.recommend(
                            {"cpu_count": record["limits"]["cpu_count"],
                             "memory_bytes": next_mem,
                             "simulator": record["simulator"]},
                            candidate_ids,
                        )
                    except DomainError:
                        recommendation = None

        decision = evaluate_bounded_recovery(
            record, run_report,
            policy=policy, lineage_runs=lineage_runs,
            recommendation=recommendation, now=now,
        )

        child_run = None
        if decision["result"] == "allow":
            parent_plan = self._load_plan(run_id)
            parent_manifest = parent_plan["manifest"]
            netlist_text = base64.b64decode(parent_plan["files"]["input.scs"], validate=True).decode("utf-8")
            support_files = {
                name: base64.b64decode(raw_b64, validate=True)
                for name, raw_b64 in parent_plan["files"].items()
                if name not in {"input.scs", "wrapper.py"}
            }
            next_attempt = len(lineage_runs) + 1
            retry_idx = next_attempt - 1
            child_run_id = f"{root_id}-r{retry_idx}"
            if len(child_run_id) > 128:
                child_run_id = f"{root_id[:118]}-r{retry_idx}"
            trigger = {
                "decision_id": decision["decision_id"],
                "failure_class": decision["failure_class"],
                "action": decision["action"],
                "resource_diff": decision["resource_diff"],
                "evidence": decision["evidence"],
                "triggered_at": decision["evaluated_at"],
            }
            child_run = self.prepare(
                run_key=child_run_id,
                netlist_text=netlist_text,
                host_id=decision["next_host_id"],
                backend=parent_manifest["backend"],
                simulator=parent_manifest["simulator"],
                expected_version=parent_manifest["expected_version"],
                pin_version=parent_manifest["expected_version"] is not None,
                support_files=support_files or None,
                limits=decision["next_limits"],
                scheduler=parent_manifest["scheduler"] or None,
                output_format=parent_manifest["output_format"],
                include_dirs=parent_manifest["include_dirs"] or None,
                remote_python=parent_manifest["remote_python"],
                remote_root=parent_manifest["remote_root"],
                license_environment=parent_manifest["license_environment"] or None,
                source_commit=record.get("source_commit"),
                recovery_policy=policy,
                root_run_id=root_id,
                parent_run_id=run_id,
                attempt_number=next_attempt,
                origin_submission_key=record.get("origin_submission_key") or record["submission_key"],
                recovery_trigger=trigger,
                project_name=record.get("project_name"),
                library_name=record.get("library_name"),
                cell_name=record.get("cell_name"),
                view_name=record.get("view_name"),
                submission_group_id=record.get("submission_group_id") or f"chain:{root_id}",
                submission_label=record.get("submission_label"),
                source_origin=record.get("source_origin"),
                batch_id=record.get("batch_id"),
            )
            decision["child_run_id"] = child_run["run_id"]
            with _locked(self.path(run_id) / "run.lock"):
                latest_parent = self.read(run_id)
                children = list(latest_parent.get("child_run_ids") or [])
                if child_run["run_id"] not in children:
                    children.append(child_run["run_id"])
                latest_parent["child_run_ids"] = children
                if not latest_parent.get("submission_group_id"):
                    latest_parent["submission_group_id"] = f"chain:{root_id}"
                latest_parent["updated_at"] = utcnow()
                latest_parent["organization"] = build_run_organization(latest_parent)
                _atomic_write(self.path(run_id) / "run.json", latest_parent)

            if execute:
                child_run = self.stage(child_run["run_id"])
                if child_run.get("state") == "staged":
                    try:
                        child_run = self.launch(
                            child_run["run_id"],
                            recheck_resources=(child_run["backend"] == "ssh_direct"),
                        )
                    except DomainError as exc:
                        decision["dispatch_error"] = {"code": exc.code, "message": str(exc)}
                if monitor is not None and child_run.get("dispatch_claimed") and child_run.get("state") not in (
                    "simulator_complete_pending_measurements", "simulator_failed", "job_setup_failed", "input_hash_failure"
                ):
                    try:
                        monitor.watch(child_run["run_id"], allow_collect=policy["auto_collect_on_terminal"])
                        monitor.start()
                    except ValueError:
                        pass

        decisions = self._load_decisions(root_id) + [decision]
        rec_report = self._save_recovery_artifacts(root_id, decisions)
        return {"decision": decision, "child_run": child_run, "recovery_report": rec_report}

    def resume_authorized_watches(self, monitor: RuntimeMonitor) -> dict:
        """Resume persisted non-terminal watches whose policy permits auto_resume_watch."""
        registry_path = self.root / ".monitor" / "registry.json"
        if not registry_path.is_file() or registry_path.is_symlink():
            return {"resumed": [], "skipped": []}
        try:
            data = _read_json(registry_path)
            watches = _as_dict(_as_dict(data).get("watches"))
        except DomainError:
            return {"resumed": [], "skipped": []}
        resumed, skipped = [], []
        for rid in sorted(watches):
            try:
                rec = self.read(rid)
                pol = validate_recovery_policy(rec.get("recovery_policy"))
                terminal = rec.get("state") in (
                    "simulator_complete_pending_measurements", "simulator_failed", "job_setup_failed", "input_hash_failure"
                )
                if not terminal and pol["auto_resume_watch"]:
                    resumed.append(rid)
                else:
                    skipped.append(rid)
            except DomainError:
                skipped.append(rid)
        if resumed:
            try:
                monitor.start()
            except ValueError:
                pass
        return {"resumed": resumed, "skipped": skipped}

    # --- Binary Waveform Indexing, Envelope Downsampling, Exact Slicing & Cycle Analysis ---

    def _local_dataset_path(self, run_id: str, dataset: str) -> Path | None:
        rel = transport._validate_dataset_rel(dataset)
        raw_root = (self.path(run_id) / "collected" / "results.raw").resolve()
        if not raw_root.is_dir():
            return None
        candidate = (raw_root / rel).resolve()
        try:
            candidate.relative_to(raw_root)
        except ValueError:
            raise DomainError("Dataset path escapes collected results.raw.", "invalid_waveform_query") from None
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
        return None

    def waveform_index(self, run_id: str, *, refresh_remote: bool = False, allow_remote: bool = True) -> dict:
        """Return lightweight waveform index (datasets, formats, sizes, signals, sweep bounds).

        Reads local collected files first; falls back to read-only remote
        in-place indexing when large raw files were skipped during collect()
        and ``allow_remote=True``.
        """
        run_id = _validate_run_id(run_id)
        record = self.read(run_id)
        local_raw = self.path(run_id) / "collected" / "results.raw"
        idx = psf_binary.index_waveform_directory(local_raw)
        for ds in idx["datasets"]:
            ds["source"] = "local_collected"

        cached_path = self.path(run_id) / "waveform_index.json"
        if not refresh_remote and cached_path.is_file() and not cached_path.is_symlink():
            cached = _as_dict(_read_json(cached_path))
            local_names = {ds["dataset"] for ds in idx["datasets"]}
            for ds in (cached.get("datasets") or []):
                if isinstance(ds, dict) and ds.get("dataset") not in local_names:
                    idx["datasets"].append(ds)
            idx["total_raw_bytes"] = max(int(idx.get("total_raw_bytes") or 0), int(cached.get("total_raw_bytes") or 0))

        has_plan = (self.path(run_id) / "plan.json").is_file()
        skipped_raw = [
            s for s in (_as_dict(record.get("collection")).get("skipped_files") or [])
            if isinstance(s, dict) and str(s.get("name", "")).startswith("results.raw/")
        ]
        if allow_remote and has_plan and (refresh_remote or (skipped_raw and not any(d.get("source") == "remote_in_place" for d in idx["datasets"]))):
            plan = self._load_plan(run_id)
            remote_idx = _as_dict(transport.waveform_index(plan))
            local_names = {ds["dataset"] for ds in idx["datasets"]}
            for ds in (remote_idx.get("datasets") or []):
                if isinstance(ds, dict) and ds.get("dataset") not in local_names:
                    ds["source"] = "remote_in_place"
                    idx["datasets"].append(ds)
            idx["total_raw_bytes"] = max(int(idx.get("total_raw_bytes") or 0), int(remote_idx.get("total_raw_bytes") or 0))
            _atomic_write(cached_path, idx)

        idx["run_id"] = run_id
        return idx

    def waveform_query(
        self,
        run_id: str,
        dataset: str,
        signals: list[str],
        *,
        x_min: float | None = None,
        x_max: float | None = None,
        max_points: int = 2000,
        mode: str = "auto",
        allow_remote: bool = True,
    ) -> dict:
        """Query a waveform window (Min-Max envelope preview or automatic exact_window)."""
        run_id = _validate_run_id(run_id)
        self.read(run_id)
        local_file = self._local_dataset_path(run_id, dataset)
        if local_file is not None:
            result = psf_binary.query_waveform(
                local_file, signals, x_min=x_min, x_max=x_max, max_points=max_points, mode=mode,
            )
            result["source"] = "local_collected"
            result["dataset"] = dataset
            result["run_id"] = run_id
            return result
        if not allow_remote:
            raise DomainError(
                "Requested dataset is not locally collected; enable remote in-place query to read it on the host.",
                "waveform_remote_required",
            )
        plan = self._load_plan(run_id)
        result = transport.waveform_query(
            plan, dataset, signals, x_min=x_min, x_max=x_max, max_points=max_points, mode=mode,
        )
        result["dataset"] = dataset
        result["run_id"] = run_id
        return result

    def waveform_cycles(
        self,
        run_id: str,
        dataset: str,
        signals: list[str],
        *,
        period: float | None = None,
        clock_signal: str | None = None,
        threshold: float | None = None,
        edge: str = "rising",
        t_start: float | None = None,
        t_end: float | None = None,
        worst_metric: str = "peak_to_peak",
        worst_signal: str | None = None,
        cycle_index: int | None = None,
        context_cycles: int = 0,
        overlay_cycles: int = 10,
        max_points_per_cycle: int = 2000,
        allow_remote: bool = True,
    ) -> dict:
        """Run Cycle Slicer, per-cycle trend metrics, worst-case detection, and cycle overlay."""
        run_id = _validate_run_id(run_id)
        self.read(run_id)
        local_file = self._local_dataset_path(run_id, dataset)
        if local_file is not None:
            result = psf_binary.analyze_cycles(
                local_file, signals,
                period=period, clock_signal=clock_signal, threshold=threshold, edge=edge,
                t_start=t_start, t_end=t_end, worst_metric=worst_metric, worst_signal=worst_signal,
                cycle_index=cycle_index, context_cycles=context_cycles,
                overlay_cycles=overlay_cycles, max_points_per_cycle=max_points_per_cycle,
            )
            result["source"] = "local_collected"
            result["dataset"] = dataset
            result["run_id"] = run_id
            return result
        if not allow_remote:
            raise DomainError(
                "Requested dataset is not locally collected; enable remote in-place query to analyze cycles on the host.",
                "waveform_remote_required",
            )
        plan = self._load_plan(run_id)
        result = transport.waveform_cycles(
            plan, dataset, signals,
            period=period, clock_signal=clock_signal, threshold=threshold, edge=edge,
            t_start=t_start, t_end=t_end, worst_metric=worst_metric, worst_signal=worst_signal,
            cycle_index=cycle_index, context_cycles=context_cycles,
            overlay_cycles=overlay_cycles, max_points_per_cycle=max_points_per_cycle,
        )
        result["dataset"] = dataset
        result["run_id"] = run_id
        return result

    def create_demo_waveform_run(
        self,
        run_id: str = "demo-psfbin-500c",
        *,
        cycles: int = 500,
        samples_per_cycle: int = 40,
        period_s: float = 1e-8,
        glitch_cycle: int = 317,
    ) -> dict:
        """Create an idempotent synthetic multi-cycle PSF Binary direct run for interactive inspection."""
        run_id = _validate_run_id(run_id)
        run_dir = self.path(run_id)
        if (run_dir / "run.json").is_file() and (run_dir / "collected" / "results.raw" / "tran.tran.tran").is_file():
            return self.read(run_id)
        run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        raw_dir = run_dir / "collected" / "results.raw"
        raw_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        psf_path = psf_binary.generate_demo_multicycle_psf(
            raw_dir / "tran.tran.tran",
            cycles=cycles,
            samples_per_cycle=samples_per_cycle,
            period_s=period_s,
            glitch_cycle=glitch_cycle,
            windowed=True,
        )
        wf_index = psf_binary.index_waveform_directory(raw_dir)
        for ds in wf_index["datasets"]:
            ds["source"] = "local_collected"
        wf_index_path = run_dir / "waveform_index.json"
        _atomic_write(wf_index_path, wf_index)

        now = utcnow()
        psf_size = psf_path.stat().st_size
        record = {
            "schema": "spiceowl.direct-run.v1",
            "run_id": run_id,
            "run_key": run_id,
            "submission_key": run_id,
            "origin_submission_key": run_id,
            "root_run_id": run_id,
            "parent_run_id": None,
            "attempt_number": 1,
            "child_run_ids": [],
            "recovery_trigger": None,
            "recovery_policy": validate_recovery_policy(None),
            "synthetic": True,
            "backend": "ssh_direct",
            "simulator": "spectre",
            "output_format": "psfbin",
            "host_id": "synthetic-demo-host",
            "expected_host": "synthetic-demo-node",
            "expected_user": "demo",
            "state": "simulator_complete_pending_measurements",
            "remote_state": "simulator_complete_pending_measurements",
            "status_transport": "connected",
            "simulator_exit_code": 0,
            "raw_exit_code": 0,
            "dispatch_claimed": True,
            "collection_phase": "complete",
            "collection": {
                "collected_at": now,
                "directory": str(run_dir / "collected"),
                "file_count": 1,
                "files": [{"name": "results.raw/tran.tran.tran", "bytes": psf_size, "sha256": wf_index["datasets"][0]["fingerprint"]}],
                "skipped_files": [],
                "collection_complete": True,
                "total_bytes": psf_size,
                "raw_present": True,
                "input_and_manifest_verified": True,
            },
            "waveform_index_path": str(wf_index_path),
            "created_at": now,
            "updated_at": now,
            "observed_at": now,
            "limits": {"cpu_count": 4, "memory_bytes": 4 * 2**30, "wall_seconds": 600},
            "manifest_sha256": wf_index["datasets"][0]["fingerprint"],
            "note": f"Synthetic {cycles}-cycle windowed PSF Binary demo with hidden glitch at cycle #{glitch_cycle}.",
        }
        _atomic_write(run_dir / "run.json", record)
        return deepcopy(record)

    def waveform_measure(
        self,
        run_id: str,
        dataset: str,
        signal: str,
        *,
        x_min: float | None = None,
        x_max: float | None = None,
        tol_pct: float = 2.0,
        allow_remote: bool = True,
    ) -> dict:
        """Compute lossless analog metrics on an exact waveform window.

        Strictly refuses downsampled previews by requesting ``mode="exact"``.
        """
        from spiceowl.experiments.analog_metrics import measure_waveform_query

        q = self.waveform_query(
            run_id,
            dataset,
            [signal],
            x_min=x_min,
            x_max=x_max,
            max_points=psf_binary.MAX_PREVIEW_POINTS,
            mode="exact",
            allow_remote=allow_remote,
        )
        return measure_waveform_query(q, signal=signal, tol_pct=tol_pct)


class DirectRecoverySupervisor:
    """Optional background supervisor pairing RuntimeMonitor with bounded M4 recovery.

    Wraps DirectExecutionManager as a RunnerProtocol adapter so that when a
    watched run finishes and collects automatically, any enabled bounded
    recovery policy is evaluated after its configured backoff without blocking
    the monitor loop.
    """

    def __init__(
        self,
        manager: DirectExecutionManager,
        *,
        interval_seconds=4,
        max_backoff_seconds=120,
        handoff_manager=None,
        agent_pusher=None,
        on_run_update=None,
    ):
        self.manager = manager
        self.root = manager.root
        self.handoff_manager = handoff_manager
        self.agent_pusher = agent_pusher
        self.on_run_update = on_run_update
        self._stop = threading.Event()
        self._timers: list[threading.Timer] = []
        self._handoff_inflight: set[str] = set()
        self._lock = threading.Lock()
        self.monitor = RuntimeMonitor(self, interval_seconds=interval_seconds, max_backoff_seconds=max_backoff_seconds)

    def _notify_update(self, run_record: dict) -> None:
        cb = self.on_run_update
        if not callable(cb) or not isinstance(run_record, dict):
            return
        try:
            cb(deepcopy(run_record))
        except Exception:
            pass

    def watch_launched_run(self, run_record: dict) -> bool:
        """Arm RuntimeMonitor with auto-collect for a freshly launched or active uncollected run."""
        if not isinstance(run_record, dict):
            return False
        run_id = run_record.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            return False
        watchable_states = (
            "submitted",
            "running_observed",
            "launch_outcome_unknown",
            "reconcile_required",
            "simulator_complete_pending_measurements",
        )
        if run_record.get("state") not in watchable_states:
            return False
        if run_record.get("collection_phase") == "complete":
            return False
        try:
            self.monitor.watch(run_id, allow_collect=True)
            self.monitor.start()
            return True
        except (ValueError, DomainError):
            return False

    def _handoff_anomaly(self, anomaly_type: str, run_record: dict, summary: str = "", evidence: dict | None = None) -> None:
        if self.handoff_manager is None:
            return
        try:
            rep = None
            try:
                rep = self.manager.report(run_record["run_id"])
            except DomainError:
                pass
            fd = {}
            if isinstance(rep, dict) and rep.get("schema") == "spiceowl.simulation-report.v1":
                fd = {
                    "execution": _as_dict(rep.get("execution_status")).get("status"),
                    "measurement": _as_dict(rep.get("measurement_validity")).get("status"),
                    "performance": _as_dict(rep.get("functional_performance")).get("status"),
                    "soa": _as_dict(rep.get("operating_qualification")).get("status"),
                }
            self.handoff_manager.record_anomaly(
                anomaly_type=anomaly_type,
                run_id=run_record["run_id"],
                root_run_id=run_record.get("root_run_id") or run_record["run_id"],
                host_id=run_record.get("host_id", "unknown"),
                backend=run_record.get("backend", "ssh_direct"),
                simulator=run_record.get("simulator", "spectre"),
                attempt_number=int(run_record.get("attempt_number") or 1),
                summary=summary,
                five_dim_summary=fd,
                evidence=evidence,
                agent_pusher=self.agent_pusher,
            )
        except Exception:
            pass

    def _check_queue_timeout(self, run_record: dict) -> None:
        if self.handoff_manager is None or not isinstance(run_record, dict):
            return
        state = str(run_record.get("state") or "")
        remote_state = str(_as_dict(run_record.get("last_status")).get("remote_state") or "")
        if state != "submitted" and remote_state not in ("queued", "pending"):
            return
        ts_raw = run_record.get("launched_at") or run_record.get("created_at")
        if not isinstance(ts_raw, str) or not ts_raw:
            return
        try:
            started = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        except ValueError:
            return
        policy = _as_dict(self.handoff_manager.get_policy())
        threshold = int(policy.get("queue_timeout_seconds") or 1800)
        if elapsed >= threshold:
            self._handoff_anomaly(
                "queue_timeout",
                run_record,
                summary=f"任务 {run_record['run_id']} 已在队列等待 {int(elapsed)} 秒（超过阈值 {threshold} 秒），已记录并交由用户接入的 Agent 检查资源并决定是否迁移或调整队列。",
                evidence={
                    "queue_wait_seconds": int(elapsed),
                    "queue_timeout_seconds": threshold,
                    "scheduler_job_id": run_record.get("scheduler_job_id"),
                    "remote_state": remote_state or state,
                },
            )

    def path(self, run_id: str) -> Path:
        return self.manager.path(run_id)

    def read(self, run_id: str) -> dict:
        return self.manager.read(run_id)

    def status(self, run_id: str) -> dict:
        prev_state = None
        prev_raw_state = None
        try:
            prior = self.manager.read(run_id)
            prev_state = prior.get("state")
            prev_raw_state = prior.get("raw_remote_state")
        except DomainError:
            pass
        result = self.manager.status(run_id)
        if result.get("state") != prev_state or result.get("raw_remote_state") != prev_raw_state:
            self._notify_update(result)
        self._check_queue_timeout(result)
        return result

    def push_completion_report(
        self,
        run_id: str,
        *,
        force: bool = False,
        custom_prompt: str | None = None,
    ) -> dict:
        """Push a structured completion report prompt to the connected Agent and persist receipt."""
        from spiceowl.watch_handoff import build_completion_report_prompt

        clean_custom: str | None = None
        if custom_prompt is not None:
            if not isinstance(custom_prompt, str):
                raise DomainError("custom_prompt must be a string or null.", "invalid_ai_report")
            stripped_custom = custom_prompt.strip()
            if len(stripped_custom) > 2000:
                raise DomainError("custom_prompt exceeds 2000 characters.", "invalid_ai_report")
            clean_custom = stripped_custom or None

        record = self.manager.read(run_id)
        if not force and clean_custom is None:
            existing = _as_dict(record.get("completion_handoff"))
            if existing.get("pushed") is True:
                return deepcopy(existing)
        if self.agent_pusher is None:
            return {
                "pushed": False,
                "status": "no_agent_connected",
                "note": "当前未配置 Codex Agent 桥接。",
            }

        rep = None
        try:
            rep = self.manager.report(run_id)
        except DomainError:
            rep = None
        wf = None
        try:
            wf = self.manager.waveform_index(run_id, allow_remote=False)
        except DomainError:
            wf = None

        html_path = self.manager.ai_report_html_path(run_id)
        prompt = build_completion_report_prompt(
            record,
            rep,
            wf,
            html_path=html_path,
            custom_prompt=clean_custom,
        )
        req_key = f"sim-report-{run_id[:64]}"
        if force or clean_custom is not None:
            req_key = f"{req_key}-{uuid.uuid4().hex[:6]}"

        try:
            raw_res = _as_dict(
                self.agent_pusher(
                    event={"run_id": run_id, "kind": "simulation_completed"},
                    prompt=prompt,
                    request_key=req_key,
                    policy={},
                )
            )
        except DomainError as exc:
            status_code = "agent_busy" if exc.code == "codex_turn_busy" else "push_error"
            raw_res = {"pushed": False, "thread_id": None, "status": status_code, "note": str(exc)}
        except Exception as exc:
            raw_res = {"pushed": False, "thread_id": None, "status": "push_error", "note": str(exc)}

        pushed = bool(raw_res.get("pushed"))
        now = utcnow()
        handoff_receipt = {
            "pushed": pushed,
            "status": str(raw_res.get("status") or ("pushed" if pushed else "unknown")),
            "thread_id": raw_res.get("thread_id"),
            "note": str(raw_res.get("note") or ""),
            "request_key": req_key,
            "attempted_at": now,
            "pushed_at": now if pushed else None,
            "force": bool(force or clean_custom is not None),
        }
        updated_record = self.manager.mark_completion_handoff(run_id, handoff_receipt)
        self._notify_update(updated_record)
        return deepcopy(handoff_receipt)

    def push_scope_report(
        self,
        *,
        scope: str,
        project: str,
        testbench: str | None = None,
        custom_prompt: str | None = None,
    ) -> dict:
        """Gather matching runs for a Project or Testbench and push a multi-run HTML report task to Agent."""
        from spiceowl.watch_handoff import build_scope_report_prompt

        clean_scope, clean_proj, clean_tb, key = self.manager._resolve_scope_identity(scope, project, testbench)
        clean_custom: str | None = None
        if custom_prompt is not None:
            if not isinstance(custom_prompt, str):
                raise DomainError("custom_prompt must be a string or null.", "invalid_scope_report")
            stripped_custom = custom_prompt.strip()
            if len(stripped_custom) > 2000:
                raise DomainError("custom_prompt exceeds 2000 characters.", "invalid_scope_report")
            clean_custom = stripped_custom or None

        matched_runs: list[dict] = []
        for r in self.manager.list():
            org = _as_dict(r.get("organization"))
            r_proj = org.get("project") or r.get("project_name") or "未归类项目"
            r_tb = org.get("testbench") or r.get("cell_name")
            if r_proj != clean_proj:
                continue
            if clean_scope == "testbench" and r_tb != clean_tb:
                continue
            matched_runs.append(r)

        run_ids = [str(r["run_id"]) for r in matched_runs if r.get("run_id")]
        html_path = self.manager.scope_report_html_path(scope=clean_scope, project=clean_proj, testbench=clean_tb)

        if self.agent_pusher is None:
            return {
                "pushed": False,
                "status": "no_agent_connected",
                "note": "当前未配置 Codex Agent 桥接。",
                "scope": clean_scope,
                "scope_key": key,
                "project": clean_proj,
                "testbench": clean_tb,
                "run_ids": run_ids,
                "run_count": len(run_ids),
                "html_path": html_path,
            }

        prompt = build_scope_report_prompt(
            clean_scope,
            project=clean_proj,
            testbench=clean_tb,
            runs=matched_runs,
            custom_prompt=clean_custom,
            html_path=html_path,
        )
        req_key = f"scope-report-{key}-{uuid.uuid4().hex[:6]}"
        try:
            raw_res = _as_dict(
                self.agent_pusher(
                    event={
                        "kind": "scope_report_requested",
                        "scope": clean_scope,
                        "scope_key": key,
                        "project": clean_proj,
                        "testbench": clean_tb,
                        "run_ids": run_ids,
                    },
                    prompt=prompt,
                    request_key=req_key,
                    policy={},
                )
            )
        except DomainError as exc:
            status_code = "agent_busy" if exc.code == "codex_turn_busy" else "push_error"
            raw_res = {"pushed": False, "thread_id": None, "status": status_code, "note": str(exc)}
        except Exception as exc:
            raw_res = {"pushed": False, "thread_id": None, "status": "push_error", "note": str(exc)}

        pushed = bool(raw_res.get("pushed"))
        now = utcnow()
        return {
            "pushed": pushed,
            "status": str(raw_res.get("status") or ("pushed" if pushed else "unknown")),
            "thread_id": raw_res.get("thread_id"),
            "note": str(raw_res.get("note") or ""),
            "request_key": req_key,
            "scope": clean_scope,
            "scope_key": key,
            "project": clean_proj,
            "testbench": clean_tb,
            "run_ids": run_ids,
            "run_count": len(run_ids),
            "html_path": html_path,
            "attempted_at": now,
            "pushed_at": now if pushed else None,
        }

    def _schedule_completion_handoff(self, run_record: dict) -> None:
        if self._stop.is_set() or self.agent_pusher is None or not isinstance(run_record, dict):
            return
        run_id = run_record.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            return
        if _as_dict(run_record.get("completion_handoff")).get("pushed") is True:
            return
        with self._lock:
            if self._stop.is_set() or run_id in self._handoff_inflight:
                return
            self._handoff_inflight.add(run_id)

        def _worker() -> None:
            try:
                for attempt in range(61):
                    if self._stop.is_set():
                        return
                    try:
                        receipt = self.push_completion_report(run_id, force=False)
                    except Exception:
                        return
                    if receipt.get("pushed") or receipt.get("status") != "agent_busy":
                        return
                    if attempt >= 60 or self._stop.wait(2.0):
                        return
            finally:
                with self._lock:
                    self._handoff_inflight.discard(run_id)

        thread = threading.Thread(
            target=_worker,
            name=f"completion-handoff-{run_id[:24]}",
            daemon=True,
        )
        thread.start()

    def collect(self, run_id: str) -> dict:
        result = self.manager.collect(run_id)
        rep = None
        try:
            rep = self.manager.report(run_id)
        except DomainError:
            rep = None
        if result.get("state") == "simulator_complete_pending_measurements":
            if isinstance(rep, dict) and rep.get("schema") == "spiceowl.simulation-report.v1":
                meas = _as_dict(rep.get("measurement_validity")).get("status")
                perf = _as_dict(rep.get("functional_performance")).get("status")
                if meas == "valid" and perf == "fail":
                    self._handoff_anomaly(
                        "spec_missed",
                        result,
                        summary="仿真测量有效但电路指标未达标（科学失败结果已完整保留），已交由 Agent 审阅。",
                        evidence={"measurement_validity": meas, "functional_performance": perf},
                    )
        else:
            cls = classify_failure(result, rep)
            fclass = cls.get("failure_class")
            anomaly = (
                "confirmed_oom" if fclass == "confirmed_oom"
                else "license_unavailable" if fclass == "license_unavailable"
                else "convergence_failed" if fclass == "convergence_error"
                else "simulator_failed"
            )
            self._handoff_anomaly(
                anomaly,
                result,
                summary=f"仿真出现异常（分类：{fclass}），已写入网页值守记录并推送给用户接入的 Agent 评估资源与重提策略。",
                evidence={"failure_class": fclass, "reason": cls.get("reason")},
            )
        self._schedule_recovery_if_enabled(result)
        self._notify_update(result)
        self._schedule_completion_handoff(result)
        return result

    def _schedule_recovery_if_enabled(self, run_record: dict) -> None:
        if self._stop.is_set():
            return
        policy = validate_recovery_policy(run_record.get("recovery_policy"))
        if not policy["enabled"]:
            return
        if run_record.get("state") == "simulator_complete_pending_measurements":
            return
        run_id = run_record["run_id"]
        try:
            rep = self.manager.report(run_id)
        except DomainError:
            rep = None
        cls = classify_failure(run_record, rep)
        delay = policy["license_backoff_seconds"] if cls["failure_class"] == "license_unavailable" else 0

        def run_recovery():
            if self._stop.is_set():
                return
            try:
                self.manager.evaluate_and_recover(run_id, execute=True, monitor=self.monitor)
            except Exception:
                pass

        if delay <= 0:
            worker = threading.Timer(0.05, run_recovery)
        else:
            worker = threading.Timer(float(delay), run_recovery)
        worker.daemon = True
        with self._lock:
            if not self._stop.is_set():
                self._timers.append(worker)
                worker.start()

    def resume(self) -> dict:
        res = self.manager.resume_authorized_watches(self.monitor)
        resumed_list = list(res.get("resumed") or [])
        resumed_set = set(resumed_list)
        newly_armed = False
        for r in self.manager.list():
            rid = r.get("run_id")
            if not isinstance(rid, str) or not rid or rid in resumed_set:
                continue
            state = r.get("state")
            is_dispatched = bool(r.get("dispatch_claimed")) and (
                bool(r.get("remote_directory")) or state in ("submitted", "running_observed")
            )
            if not is_dispatched:
                continue
            needs_watch = (
                state in ("submitted", "running_observed")
                or (
                    state in ("simulator_complete_pending_measurements", "simulator_failed", "job_setup_failed")
                    and r.get("collection_phase") != "complete"
                )
            )
            if not needs_watch:
                continue
            try:
                self.monitor.watch(rid, allow_collect=True)
                resumed_list.append(rid)
                resumed_set.add(rid)
                newly_armed = True
            except (ValueError, DomainError):
                continue
        if newly_armed:
            try:
                self.monitor.start()
            except ValueError:
                pass
        res["resumed"] = resumed_list
        return res

    def stop(self, timeout=1) -> bool:
        self._stop.set()
        with self._lock:
            for timer in self._timers:
                timer.cancel()
            self._timers.clear()
        return self.monitor.stop(timeout=timeout)

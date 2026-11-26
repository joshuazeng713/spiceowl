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
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", re.ASCII)
_COMMIT = re.compile(r"[a-f0-9]{40}", re.ASCII)

_TERMINAL_ADAPTER_STATES = frozenset({"completed", "failed", "timed_out", "cancelled", "preflight_failed"})
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
    raw = receipt.get("state")
    result = receipt.get("result") if isinstance(receipt.get("result"), dict) else {}
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
    err = receipt.get("error") if isinstance(receipt.get("error"), dict) else {}
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
        if data.get("schema") != SUBMISSIONS_SCHEMA or not isinstance(data.get("entries"), dict):
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
            plan = _read_json(plan_path)
            raw_b64 = (plan.get("files") or {}).get("input.scs")
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
        needs_hints = not (rec.get("library_name") or rec.get("cell_name") or rec.get("project_name"))
        head = self._read_netlist_head(rec["run_id"]) if needs_hints and rec.get("run_id") else ""
        rec["organization"] = build_run_organization(rec, netlist_head=head, batch_info=batch_info)
        return rec

    def read(self, run_id: str) -> dict:
        return self._enrich_record(_read_json(self.path(run_id) / "run.json"))

    def list(self, *, batch_map: dict[str, dict] | None = None) -> list[dict]:
        if not self.root.is_dir():
            return []
        bmap = batch_map if isinstance(batch_map, dict) else {}
        runs = []
        for entry in sorted(self.root.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            run_file = entry / "run.json"
            if run_file.is_file() and not run_file.is_symlink():
                try:
                    raw_rec = _read_json(run_file)
                    runs.append(self._enrich_record(raw_rec, batch_info=bmap.get(raw_rec.get("run_id"))))
                except DomainError:
                    continue
        runs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
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
            last_probe = host_record.get("last_probe") if isinstance(host_record.get("last_probe"), dict) else None
            if last_probe and last_probe.get("identity", {}).get("verified"):
                for tool in last_probe.get("tools", []):
                    if tool.get("kind") == simulator and tool.get("status") == "available" and tool.get("version"):
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
        now = _monotonic_observed_at(record.get("observed_at"))
        record["updated_at"] = now
        if connected:
            record["observed_at"] = now
            record["status_transport"] = "connected"
            record["last_error"] = None
            state, remote_state, exit_code = _map_adapter_state(receipt, launch_claimed=record.get("dispatch_claimed", False))
            record["state"] = state
            record["remote_state"] = remote_state
            record["raw_remote_state"] = receipt.get("state")
            if exit_code is not None:
                record["simulator_exit_code"] = exit_code
            result = receipt.get("result") if isinstance(receipt.get("result"), dict) else {}
            if "exit_code" in result:
                record["raw_exit_code"] = result.get("exit_code")
            if receipt.get("remote_directory"):
                record["remote_directory"] = receipt["remote_directory"]
            if receipt.get("scheduler"):
                record["scheduler"] = receipt["scheduler"]
            if receipt.get("scheduler_observation"):
                record["scheduler_observation"] = receipt["scheduler_observation"]
            if result.get("oom_evidence"):
                record["oom_evidence"] = result["oom_evidence"]
            if result.get("termination_reason"):
                record["termination_reason"] = result["termination_reason"]
            if receipt.get("accepted", {}).get("dispatch_claimed"):
                record["dispatch_claimed"] = True
                if record.get("submitted_at") is None:
                    record["submitted_at"] = receipt["accepted"].get("accepted_at") or now
        else:
            record["status_transport"] = "blocked" if blocked else "disconnected"
            record["last_error"] = receipt.get("error")
            if blocked:
                record["state"] = "input_hash_failure" if receipt.get("error", {}).get("code") in ("input_hash_mismatch", "receipt_mismatch") else "reconcile_required"
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
            connected = "error" not in receipt
            blocked = receipt.get("error", {}).get("code") in ("identity_mismatch", "run_key_conflict", "input_hash_mismatch", "receipt_mismatch")
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
                if placement.get("recommended_host_id") != host_id:
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
            connected = "error" not in receipt
            blocked = receipt.get("error", {}).get("code") in ("identity_mismatch", "run_key_conflict", "input_hash_mismatch", "receipt_mismatch")
            self._apply_observation(record, receipt, connected=connected, blocked=blocked)
            _atomic_write(self.path(run_id) / "run.json", record)
            return deepcopy(record)

    def status(self, run_id: str) -> dict:
        """Query the bound remote execution identity without re-launching."""
        run_id = _validate_run_id(run_id)
        with _locked(self.path(run_id) / "run.lock"):
            record = self.read(run_id)
            plan = self._load_plan(run_id)
            receipt = transport.status(plan)
            self._save_receipt(run_id, "status", receipt)
            connected = "error" not in receipt
            blocked = receipt.get("error", {}).get("code") in ("identity_mismatch", "run_key_conflict", "input_hash_mismatch", "receipt_mismatch")
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
            for existing in record.get("cancel_receipts", []):
                if existing.get("action_key") == key:
                    return deepcopy(existing)
            receipt = transport.cancel(plan, key)
            self._save_receipt(run_id, "cancel", receipt)
            action_record = {
                "action_key": key,
                "run_id": run_id,
                "requested_at": utcnow(),
                "outcome": receipt.get("outcome", "unknown"),
                "state": receipt.get("state"),
                "reason": receipt.get("reason"),
                "job_id": receipt.get("job_id"),
                "signaled": receipt.get("signaled"),
                "error": receipt.get("error"),
            }
            record["cancel_receipts"] = (record.get("cancel_receipts", []) + [action_record])[-32:]
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
            receipt = transport.collect(plan, max_collection_bytes=max_collection_bytes)
            if "error" in receipt:
                self._save_receipt(run_id, "collect-error", receipt)
                code = receipt["error"].get("code", "collection_failed")
                raise DomainError(receipt["error"].get("message", f"Remote collection failed: {code}"), code)

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
            record["collection_phase"] = "complete"
            record["collection"] = {
                "collected_at": collected_at,
                "directory": str(destination),
                "file_count": len(file_inventory),
                "files": file_inventory,
                "skipped_files": receipt.get("skipped_files", []),
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
            skipped_raw = [s for s in record["collection"]["skipped_files"] if str(s.get("name", "")).startswith("results.raw/")]
            if skipped_raw:
                try:
                    remote_idx = transport.waveform_index(plan)
                    local_names = {ds["dataset"] for ds in wf_index["datasets"]}
                    for ds in remote_idx.get("datasets", []):
                        if ds.get("dataset") not in local_names:
                            ds["source"] = "remote_in_place"
                            wf_index["datasets"].append(ds)
                    wf_index["total_raw_bytes"] = max(
                        wf_index.get("total_raw_bytes", 0),
                        int(remote_idx.get("total_raw_bytes", 0)),
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
        runs.sort(key=lambda r: (r.get("attempt_number", 1), r.get("created_at", "")))
        return root_id, runs

    def _decisions_path(self, root_run_id: str) -> Path:
        return self._recovery_dir() / f"{_validate_run_id(root_run_id)}.decisions.json"

    def _load_decisions(self, root_run_id: str) -> list[dict]:
        path = self._decisions_path(root_run_id)
        if not path.exists() and not path.is_symlink():
            return []
        data = _read_json(path)
        return list(data.get("decisions", [])) if isinstance(data, dict) else []

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
                index = _read_json(idx_path)
            else:
                index = {"schema": RECOVERY_INDEX_SCHEMA, "reports": {}}
            entries = index.setdefault("reports", {})
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
        index = _read_json(idx_path)
        rows = list(index.get("reports", {}).values())
        if outcome:
            rows = [row for row in rows if row.get("outcome") == outcome]
        if query:
            needle = query.strip().lower()
            if needle:
                def matches(row):
                    haystack = " ".join([
                        str(row.get("root_run_id", "")),
                        str(row.get("latest_run_id", "")),
                        str(row.get("summary_zh", "")),
                        str(row.get("backend", "")),
                        str(row.get("simulator", "")),
                        " ".join(row.get("hosts", [])),
                        " ".join(row.get("failure_classes", [])),
                    ]).lower()
                    return needle in haystack
                rows = [row for row in rows if matches(row)]
        rows.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
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
            watches = data.get("watches", {}) if isinstance(data, dict) else {}
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
            cached = _read_json(cached_path)
            local_names = {ds["dataset"] for ds in idx["datasets"]}
            for ds in cached.get("datasets", []):
                if ds.get("dataset") not in local_names:
                    idx["datasets"].append(ds)
            idx["total_raw_bytes"] = max(idx.get("total_raw_bytes", 0), int(cached.get("total_raw_bytes", 0)))

        has_plan = (self.path(run_id) / "plan.json").is_file()
        skipped_raw = [
            s for s in (record.get("collection") or {}).get("skipped_files", [])
            if str(s.get("name", "")).startswith("results.raw/")
        ]
        if allow_remote and has_plan and (refresh_remote or (skipped_raw and not any(d.get("source") == "remote_in_place" for d in idx["datasets"]))):
            plan = self._load_plan(run_id)
            remote_idx = transport.waveform_index(plan)
            local_names = {ds["dataset"] for ds in idx["datasets"]}
            for ds in remote_idx.get("datasets", []):
                if ds.get("dataset") not in local_names:
                    ds["source"] = "remote_in_place"
                    idx["datasets"].append(ds)
            idx["total_raw_bytes"] = max(idx.get("total_raw_bytes", 0), int(remote_idx.get("total_raw_bytes", 0)))
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

    def __init__(self, manager: DirectExecutionManager, *, interval_seconds=30, max_backoff_seconds=300,
                 handoff_manager=None, agent_pusher=None):
        self.manager = manager
        self.root = manager.root
        self.handoff_manager = handoff_manager
        self.agent_pusher = agent_pusher
        self._stop = threading.Event()
        self._timers: list[threading.Timer] = []
        self._lock = threading.Lock()
        self.monitor = RuntimeMonitor(self, interval_seconds=interval_seconds, max_backoff_seconds=max_backoff_seconds)

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
                    "execution": rep.get("execution_status", {}).get("status"),
                    "measurement": rep.get("measurement_validity", {}).get("status"),
                    "performance": rep.get("functional_performance", {}).get("status"),
                    "soa": rep.get("operating_qualification", {}).get("status"),
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
        remote_state = str((run_record.get("last_status") or {}).get("remote_state") or "")
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
        policy = self.handoff_manager.get_policy()
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
        result = self.manager.status(run_id)
        self._check_queue_timeout(result)
        return result

    def collect(self, run_id: str) -> dict:
        result = self.manager.collect(run_id)
        rep = None
        try:
            rep = self.manager.report(run_id)
        except DomainError:
            rep = None
        if result.get("state") == "simulator_complete_pending_measurements":
            if isinstance(rep, dict) and rep.get("schema") == "spiceowl.simulation-report.v1":
                meas = (rep.get("measurement_validity") or {}).get("status")
                perf = (rep.get("functional_performance") or {}).get("status")
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
        return self.manager.resume_authorized_watches(self.monitor)

    def stop(self, timeout=1) -> bool:
        self._stop.set()
        with self._lock:
            for timer in self._timers:
                timer.cancel()
            self._timers.clear()
        return self.monitor.stop(timeout=timeout)

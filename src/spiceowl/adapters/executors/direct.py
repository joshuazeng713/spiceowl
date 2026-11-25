"""Direct remote execution adapter for SSH detached processes, LSF, and Slurm.

This module promotes SpiceOwl's hardened OpenSSH transport model into a
reusable, standalone execution backend that requires no external orchestrator
or CircuitGit gateway. Three explicit backends are supported:

- ``ssh_direct``: Linux detached wrapper with ``/proc`` identity binding
  (``pid``, ``start_ticks``, ``boot_id``), ``/usr/bin/timeout`` wall clock
  enforcement, and verified-identity cancellation.
- ``lsf``: IBM Spectrum LSF submission/observation/cancellation via
  ``bsub``, ``bjobs``, and ``bkill`` over the operator's OpenSSH route.
- ``slurm``: Slurm Workload Manager submission/observation/cancellation via
  ``sbatch``, ``squeue``, ``sacct``, and ``scancel`` over OpenSSH.

Security and durability invariants:
- ``prepare`` is pure and deterministic (no filesystem or network I/O).
- Every remote call verifies ``expected_host`` and ``expected_user`` before
  creating or mutating any remote file.
- Dispatch uses an atomic remote ``launch-claim`` directory fence; repeated
  calls or lost SSH replies never dispatch a second job.
- Connection loss or scheduler query unavailability returns ``outcome_unknown``
  or ``unobserved``; it is never relabeled as job failure.
- Cancellation targets only a verified owned process group or scheduler Job ID
  bound to the exact frozen ``manifest_sha256``.
"""

import base64
import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import secrets
import subprocess
import time

from spiceowl._platform import (
    BoundedIOError,
    communicate_bounded,
    popen_isolation_kwargs,
    terminate_process_tree,
)
from spiceowl.adapters.executors.ssh import _SSH_ERROR_MESSAGES, _StderrHints, _ssh_argv
from spiceowl.adapters.hosts import validate_host_profile
from spiceowl.domain.common import DomainError, require_id


SCHEMA = "spiceowl.direct-execution.v1"
BACKENDS = ("ssh_direct", "lsf", "slurm")
SIMULATORS = ("spectre", "ngspice")
OUTPUT_FORMATS = ("psfascii", "psfbin", "psfxl")

MAX_NETLIST_BYTES = 1024 * 1024
MAX_SUPPORT_FILES = 64
MAX_TOTAL_INPUT_BYTES = 4 * 1024 * 1024
MAX_COLLECTION_BYTES = 32 * 1024 * 1024
MAX_REPLY_BYTES = 48 * 1024 * 1024
SSH_TIMEOUT_SECONDS = 30

DEFAULT_LIMITS = {
    "wall_seconds": 3600,
    "kill_grace_seconds": 15,
    "cpu_count": 1,
    "memory_bytes": 4 * 1024**3,
    "file_size_bytes": 256 * 1024**2,
    "license_queue_seconds": 120,
    "enforce_rlimit_as": False,
}

_VERSION_PATTERNS = {
    "spectre": re.compile(r"[0-9]+(?:\.[0-9]+){2,4}(?:\.isr[0-9]+)?", re.IGNORECASE | re.ASCII),
    "ngspice": re.compile(r"[0-9]+(?:\.[0-9]+)*(?:\+|-dev)?", re.IGNORECASE | re.ASCII),
}
_PYTHON_PATH = re.compile(r"/(?:[A-Za-z0-9_+-][A-Za-z0-9_.+-]*/)*python(?:3(?:\.[0-9]+)?)?", re.ASCII)
_ABS_POSIX = re.compile(r"/[A-Za-z0-9_./+-]{1,511}", re.ASCII)
_REL_ROOT = re.compile(r"[.A-Za-z0-9_][A-Za-z0-9_.-]{0,63}", re.ASCII)
_SUPPORT_PATH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+-]{0,127}(?:/[A-Za-z0-9_][A-Za-z0-9_.+-]{0,127}){0,5}", re.ASCII)
_SCHED_TOKEN = re.compile(r"[A-Za-z0-9_.-]{1,64}", re.ASCII)
_LSF_GROUP = re.compile(r"/[A-Za-z0-9_.-]{1,64}(?:/[A-Za-z0-9_.-]{1,64}){0,5}", re.ASCII)
_LICENSE_VALUE = re.compile(r"[A-Za-z0-9_.:/@+-]{1,2048}", re.ASCII)
_LICENSE_KEYS = frozenset({"CDS_LIC_FILE", "LM_LICENSE_FILE", "CDS_AUTO_64BIT"})
_RESERVED_FILES = frozenset({
    "input.scs", "wrapper.py", "job.sh", "manifest.json", "ready", "launch-claim",
    "accepted.json", "wrapper.json", "supervisor.json", "solver-launcher.json",
    "scheduler.json", "cancel-requested.json", "status.json", "spectre.log",
    "ngspice.log", "console.log", "wrapper.log", "version.json", "results.raw",
})


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _validate_abs_dir(value, label):
    if not isinstance(value, str) or _ABS_POSIX.fullmatch(value) is None:
        raise DomainError(f"Invalid {label}.", "invalid_direct_plan")
    parts = value.split("/")[1:]
    if any(part in {"", ".", ".."} for part in parts):
        raise DomainError(f"Invalid {label}.", "invalid_direct_plan")
    return value


def _validate_remote_root(value):
    if value is None:
        return ".spiceowl-runs"
    if not isinstance(value, str) or not value:
        raise DomainError("Invalid remote_root.", "invalid_direct_plan")
    if value.startswith("/"):
        return _validate_abs_dir(value, "remote_root")
    if _REL_ROOT.fullmatch(value) is None or value in {".", ".."} or ".." in value:
        raise DomainError("Invalid remote_root.", "invalid_direct_plan")
    return value


def _validate_command_path(value, expected_name):
    if value is None:
        return expected_name
    if value == expected_name:
        return expected_name
    if not isinstance(value, str) or _ABS_POSIX.fullmatch(value) is None:
        raise DomainError(f"Invalid {expected_name} executable path.", "invalid_direct_plan")
    parts = value.split("/")[1:]
    if any(part in {"", ".", ".."} for part in parts) or PurePosixPath(value).name != expected_name:
        raise DomainError(f"Executable path must end with {expected_name}.", "invalid_direct_plan")
    return value


def _validate_limits(limits):
    merged = dict(DEFAULT_LIMITS)
    if limits is not None:
        if type(limits) is not dict:
            raise DomainError("Execution limits must be a JSON object.", "invalid_direct_plan")
        unknown = set(limits) - set(DEFAULT_LIMITS)
        if unknown:
            raise DomainError("Unsupported execution limit fields.", "invalid_direct_plan")
        merged.update(limits)
    if type(merged["wall_seconds"]) is not int or not 10 <= merged["wall_seconds"] <= 604800:
        raise DomainError("wall_seconds must be an integer between 10 and 604800.", "invalid_direct_plan")
    if type(merged["kill_grace_seconds"]) is not int or not 1 <= merged["kill_grace_seconds"] <= 300:
        raise DomainError("kill_grace_seconds must be an integer between 1 and 300.", "invalid_direct_plan")
    if merged["kill_grace_seconds"] >= merged["wall_seconds"]:
        raise DomainError("kill_grace_seconds must be smaller than wall_seconds.", "invalid_direct_plan")
    if type(merged["cpu_count"]) is not int or not 1 <= merged["cpu_count"] <= 4096:
        raise DomainError("cpu_count must be an integer between 1 and 4096.", "invalid_direct_plan")
    if type(merged["memory_bytes"]) is not int or not 64 * 1024**2 <= merged["memory_bytes"] <= 16 * 1024**4:
        raise DomainError("memory_bytes must be an integer between 64 MiB and 16 TiB.", "invalid_direct_plan")
    if type(merged["file_size_bytes"]) is not int or not 1024**2 <= merged["file_size_bytes"] <= 64 * 1024**3:
        raise DomainError("file_size_bytes must be an integer between 1 MiB and 64 GiB.", "invalid_direct_plan")
    if type(merged["license_queue_seconds"]) is not int or not 0 <= merged["license_queue_seconds"] <= 86400:
        raise DomainError("license_queue_seconds must be an integer between 0 and 86400.", "invalid_direct_plan")
    if type(merged["enforce_rlimit_as"]) is not bool:
        raise DomainError("enforce_rlimit_as must be a boolean.", "invalid_direct_plan")
    return merged


def _validate_scheduler(backend, scheduler):
    raw = {} if scheduler is None else scheduler
    if type(raw) is not dict:
        raise DomainError("Scheduler options must be a JSON object.", "invalid_direct_plan")
    if backend == "ssh_direct":
        if raw:
            raise DomainError("ssh_direct does not accept scheduler options.", "invalid_direct_plan")
        return {}
    if backend == "lsf":
        allowed = {"queue", "project", "job_group", "exclusive", "bsub_executable", "bjobs_executable", "bkill_executable"}
        if set(raw) - allowed:
            raise DomainError("Unsupported LSF scheduler options.", "invalid_direct_plan")
        queue = raw.get("queue")
        if not isinstance(queue, str) or _SCHED_TOKEN.fullmatch(queue) is None:
            raise DomainError("LSF execution requires a valid queue name.", "invalid_direct_plan")
        result = {
            "queue": queue,
            "project": None,
            "job_group": None,
            "exclusive": bool(raw.get("exclusive", False)),
            "bsub_executable": _validate_command_path(raw.get("bsub_executable"), "bsub"),
            "bjobs_executable": _validate_command_path(raw.get("bjobs_executable"), "bjobs"),
            "bkill_executable": _validate_command_path(raw.get("bkill_executable"), "bkill"),
        }
        if type(raw.get("exclusive", False)) is not bool:
            raise DomainError("exclusive must be a boolean.", "invalid_direct_plan")
        if raw.get("project") is not None:
            if not isinstance(raw["project"], str) or _SCHED_TOKEN.fullmatch(raw["project"]) is None:
                raise DomainError("Invalid LSF project name.", "invalid_direct_plan")
            result["project"] = raw["project"]
        if raw.get("job_group") is not None:
            if not isinstance(raw["job_group"], str) or _LSF_GROUP.fullmatch(raw["job_group"]) is None:
                raise DomainError("Invalid LSF job_group path.", "invalid_direct_plan")
            result["job_group"] = raw["job_group"]
        return result
    if backend == "slurm":
        allowed = {"partition", "account", "qos", "exclusive",
                   "sbatch_executable", "squeue_executable", "sacct_executable", "scancel_executable"}
        if set(raw) - allowed:
            raise DomainError("Unsupported Slurm scheduler options.", "invalid_direct_plan")
        partition = raw.get("partition")
        if not isinstance(partition, str) or _SCHED_TOKEN.fullmatch(partition) is None:
            raise DomainError("Slurm execution requires a valid partition name.", "invalid_direct_plan")
        if type(raw.get("exclusive", False)) is not bool:
            raise DomainError("exclusive must be a boolean.", "invalid_direct_plan")
        result = {
            "partition": partition,
            "account": None,
            "qos": None,
            "exclusive": bool(raw.get("exclusive", False)),
            "sbatch_executable": _validate_command_path(raw.get("sbatch_executable"), "sbatch"),
            "squeue_executable": _validate_command_path(raw.get("squeue_executable"), "squeue"),
            "sacct_executable": _validate_command_path(raw.get("sacct_executable"), "sacct"),
            "scancel_executable": _validate_command_path(raw.get("scancel_executable"), "scancel"),
        }
        for field in ("account", "qos"):
            if raw.get(field) is not None:
                if not isinstance(raw[field], str) or _SCHED_TOKEN.fullmatch(raw[field]) is None:
                    raise DomainError(f"Invalid Slurm {field}.", "invalid_direct_plan")
                result[field] = raw[field]
        return result
    raise DomainError("Unsupported direct execution backend.", "invalid_direct_plan")


def _simulator_argv(simulator, executable, limits, output_format, include_dirs):
    if simulator == "spectre":
        argv = [executable, "input.scs", "-format", output_format, "-raw", "results.raw",
                "+log", "spectre.log", "+lqtimeout", str(limits["license_queue_seconds"]),
                f"+mt={limits['cpu_count']}"]
        for inc in include_dirs:
            argv.extend(("-I", inc))
        return argv
    if simulator == "ngspice":
        return [executable, "-b", "-r", "results.raw/ngspice.raw", "-o", "ngspice.log", "input.scs"]
    raise DomainError("Unsupported simulator.", "invalid_direct_plan")


# Pure scheduler output parsers (mirrored in _REMOTE_SOURCE for offline tests).
def parse_bsub_output(text):
    """Extract numeric LSF job ID from bsub stdout."""
    match = re.search(r"Job\s+<([0-9]{1,20})>\s+is\s+submitted", text or "")
    return match.group(1) if match else None


def parse_bjobs_output(text, job_id):
    """Parse ``bjobs -noheader -o "jobid stat exit_code exec_host"`` output."""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2 or parts[0] != str(job_id):
            continue
        stat_code = parts[1].upper()
        exit_raw = parts[2] if len(parts) >= 3 else "-"
        exit_code = int(exit_raw) if re.fullmatch(r"-?[0-9]{1,5}", exit_raw) else None
        exec_host = parts[3] if len(parts) >= 4 and parts[3] != "-" else None
        return {"job_id": str(job_id), "raw_state": stat_code, "exit_code": exit_code, "exec_host": exec_host}
    return None


def parse_sbatch_output(text):
    """Extract numeric Slurm job ID (and optional cluster) from ``sbatch --parsable``."""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        match = re.fullmatch(r"([0-9]{1,20})(?:;([A-Za-z0-9_.-]{1,64}))?", line)
        if match:
            return {"job_id": match.group(1), "cluster": match.group(2)}
    return None


def parse_squeue_output(text, job_id):
    """Parse ``squeue -h -j <id> -o "%i|%T|%N|%R"`` output."""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) >= 2 and parts[0].strip() == str(job_id):
            return {
                "job_id": str(job_id),
                "raw_state": parts[1].strip().upper(),
                "nodelist": parts[2].strip() if len(parts) >= 3 and parts[2].strip() else None,
                "reason": parts[3].strip() if len(parts) >= 4 and parts[3].strip() else None,
            }
    return None


def parse_sacct_output(text, job_id):
    """Parse ``sacct -n -P -j <id> --format=JobIDRaw,State,ExitCode,DerivedExitCode,NodeList``."""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) < 3 or parts[0].strip() != str(job_id):
            continue
        state_token = parts[1].strip().upper().split()[0].rstrip("+")
        exit_code = None
        signal_num = None
        exit_field = parts[2].strip()
        if ":" in exit_field:
            code_str, sig_str = exit_field.split(":", 1)
            if code_str.isdigit():
                exit_code = int(code_str)
            if sig_str.isdigit():
                signal_num = int(sig_str)
        elif exit_field.isdigit():
            exit_code = int(exit_field)
        nodelist = parts[4].strip() if len(parts) >= 5 and parts[4].strip() else None
        return {
            "job_id": str(job_id),
            "raw_state": state_token,
            "exit_code": exit_code,
            "signal": signal_num,
            "nodelist": nodelist,
        }
    return None


# Remote helper executed over OpenSSH as a single-line base64 payload.
# Also written as wrapper.py inside each isolated run directory.
_REMOTE_SOURCE = r'''
import base64, errno, hashlib, json, math, os, pwd, re, resource, shlex, shutil, signal, socket, stat, subprocess, sys, tempfile, time

MAX_FILE = 32 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
MAX_COLLECTION_FILES = 256
DEVNULL = open(os.devnull, "rb")
STATIC_FILES = (
    "input.scs", "manifest.json", "accepted.json", "wrapper.json",
    "supervisor.json", "solver-launcher.json", "scheduler.json",
    "cancel-requested.json", "status.json", "spectre.log", "ngspice.log",
    "console.log", "wrapper.log", "version.json",
)

class Failure(Exception):
    def __init__(self, code):
        self.code = code

class WaitExpired(Exception):
    pass

def clock():
    try:
        with open("/proc/uptime") as stream:
            return float(stream.read(128).split()[0])
    except OSError:
        return time.monotonic()

def wait_for(process, seconds):
    deadline = clock() + seconds
    while process.poll() is None:
        if clock() >= deadline:
            raise WaitExpired()
        time.sleep(0.02)
    return process.returncode

def decoded(value):
    if not isinstance(value, str) or not re.match(r"\A[A-Za-z0-9+/]*={0,2}\Z", value):
        raise Failure("invalid_base64")
    return base64.b64decode(value)

def js(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")

def sha(value):
    return hashlib.sha256(value).hexdigest()

def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def identity(manifest):
    actual = {"hostname": socket.gethostname(), "username": pwd.getpwuid(os.getuid()).pw_name}
    wanted = manifest["profile"]
    if actual["hostname"] != wanted["expected_host"] or actual["username"] != wanted["expected_user"]:
        raise Failure("identity_mismatch")
    return actual

def home_path():
    return os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)

def check_dir(path):
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise Failure("unsafe_run_directory")

def resolve_root(manifest, create=False):
    raw_root = manifest["remote_root"]
    if raw_root.startswith("/"):
        root = raw_root
    else:
        root = os.path.join(home_path(), raw_root)
    if create:
        try:
            os.mkdir(root, 0o700)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
    if not os.path.lexists(root):
        return None
    check_dir(root)
    return root

def directory(manifest, create=False):
    root = resolve_root(manifest, create=create)
    if root is None:
        return None
    path = os.path.join(root, manifest["directory_name"])
    if os.path.lexists(path):
        check_dir(path)
    return path

def read_file(path, limit=MAX_FILE):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_size > limit:
            raise Failure("unsafe_or_large_artifact")
        chunks = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = b"".join(chunks)
        if len(value) > limit:
            raise Failure("artifact_size_limit")
        return value
    finally:
        os.close(fd)

def read_json(path):
    return json.loads(read_file(path, 262144).decode("ascii"))

def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def write_new(path, value, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        offset = 0
        while offset < len(value):
            offset += os.write(fd, value[offset:])
        os.fsync(fd)
    finally:
        os.close(fd)

def write_atomic(path, value):
    parent = os.path.dirname(path)
    fd, temp = tempfile.mkstemp(prefix=".receipt-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(js(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(temp, path)
        sync_dir(parent)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)

def ensure_rel_parent(base, relpath):
    parts = relpath.split("/")
    current = base
    for part in parts[:-1]:
        current = os.path.join(current, part)
        try:
            os.mkdir(current, 0o700)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
        check_dir(current)

def manifest_at(path, expected):
    raw = read_file(os.path.join(path, "manifest.json"), 262144)
    if raw != js(expected):
        raise Failure("run_key_conflict")
    for name, digest in expected["input_hashes"].items():
        if sha(read_file(os.path.join(path, name), 4 * 1024 * 1024)) != digest:
            raise Failure("input_hash_mismatch")
    return expected

def proc_identity(pid):
    try:
        with open("/proc/sys/kernel/random/boot_id") as stream:
            boot = stream.read(128).strip()
        with open("/proc/%d/stat" % pid) as stream:
            line = stream.read(4096)
        fields = line.rsplit(")", 1)[1].split()
        return {"pid": pid, "start_ticks": int(fields[19]), "state": fields[0], "boot_id": boot}
    except (OSError, ValueError, IndexError):
        return None

def alive(record):
    current = proc_identity(record.get("pid", -1)) if isinstance(record, dict) else None
    return bool(current and current["state"] != "Z" and
                all(current.get(k) == record.get(k) for k in ("pid", "start_ticks", "boot_id")))

def load_optional(path, name):
    full = os.path.join(path, name)
    return read_json(full) if os.path.lexists(full) else None

def resolve_command(spec):
    if spec.startswith("/"):
        return spec if os.path.isfile(spec) and os.access(spec, os.X_OK) else None
    return shutil.which(spec)

def run_bounded(argv, timeout_seconds=10, cwd=None):
    process = subprocess.Popen(
        argv, cwd=cwd, stdin=DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        close_fds=True, env=dict(os.environ, LC_ALL="C", LANG="C"),
    )
    try:
        out, err = process.communicate(timeout=timeout_seconds)
        return process.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise Failure("scheduler_command_timeout")

def parse_bsub_id(text):
    match = re.search(r"Job\s+<([0-9]{1,20})>\s+is\s+submitted", text or "")
    return match.group(1) if match else None

def parse_bjobs_row(text, job_id):
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2 or parts[0] != str(job_id):
            continue
        stat_code = parts[1].upper()
        exit_raw = parts[2] if len(parts) >= 3 else "-"
        exit_code = int(exit_raw) if re.fullmatch(r"-?[0-9]{1,5}", exit_raw) else None
        exec_host = parts[3] if len(parts) >= 4 and parts[3] != "-" else None
        return {"job_id": str(job_id), "raw_state": stat_code, "exit_code": exit_code, "exec_host": exec_host}
    return None

def parse_sbatch_id(text):
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        match = re.fullmatch(r"([0-9]{1,20})(?:;([A-Za-z0-9_.-]{1,64}))?", line)
        if match:
            return {"job_id": match.group(1), "cluster": match.group(2)}
    return None

def parse_squeue_row(text, job_id):
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) >= 2 and parts[0].strip() == str(job_id):
            return {
                "job_id": str(job_id),
                "raw_state": parts[1].strip().upper(),
                "nodelist": parts[2].strip() if len(parts) >= 3 and parts[2].strip() else None,
                "reason": parts[3].strip() if len(parts) >= 4 and parts[3].strip() else None,
            }
    return None

def parse_sacct_row(text, job_id):
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) < 3 or parts[0].strip() != str(job_id):
            continue
        state_token = parts[1].strip().upper().split()[0].rstrip("+")
        exit_code = None
        signal_num = None
        exit_field = parts[2].strip()
        if ":" in exit_field:
            code_str, sig_str = exit_field.split(":", 1)
            if code_str.isdigit():
                exit_code = int(code_str)
            if sig_str.isdigit():
                signal_num = int(sig_str)
        elif exit_field.isdigit():
            exit_code = int(exit_field)
        nodelist = parts[4].strip() if len(parts) >= 5 and parts[4].strip() else None
        return {
            "job_id": str(job_id),
            "raw_state": state_token,
            "exit_code": exit_code,
            "signal": signal_num,
            "nodelist": nodelist,
        }
    return None

def query_lsf(manifest, scheduler_rec, cancel_rec):
    bjobs = resolve_command(manifest["scheduler"]["bjobs_executable"])
    if not bjobs:
        return {"state": "outcome_unknown", "terminal": False, "scheduler_query_error": "bjobs_unavailable"}
    job_id = scheduler_rec["job_id"]
    try:
        code, out, _ = run_bounded([bjobs, "-noheader", "-o", "jobid stat exit_code exec_host", job_id], timeout_seconds=10)
    except Failure as exc:
        return {"state": "outcome_unknown", "terminal": False, "scheduler_query_error": exc.code}
    if code != 0:
        return {"state": "outcome_unknown", "terminal": False, "scheduler_query_error": "bjobs_failed"}
    row = parse_bjobs_row(out, job_id)
    if row is None:
        return {"state": "outcome_unknown", "terminal": False, "scheduler_query_error": "job_not_reported"}
    raw = row["raw_state"]
    if raw in ("PEND", "WAIT", "PROV"):
        return {"state": "queued", "terminal": False, "scheduler_observation": row}
    if raw in ("RUN", "USUSP", "PSUSP", "SSUSP"):
        return {"state": "running", "terminal": False, "scheduler_observation": row}
    if raw == "DONE":
        return {"state": "completed", "terminal": True, "exit_code": 0 if row["exit_code"] is None else row["exit_code"],
                "scheduler_observation": row}
    if raw == "EXIT":
        state = "cancelled" if cancel_rec is not None else "failed"
        res = {"state": state, "terminal": True, "exit_code": row["exit_code"], "scheduler_observation": row}
        if row["exit_code"] == 137:
            res["termination_reason"] = "kill_signal_cause_unknown"
        return res
    return {"state": "outcome_unknown", "terminal": False, "scheduler_observation": row}

def query_slurm(manifest, scheduler_rec, cancel_rec):
    job_id = scheduler_rec["job_id"]
    squeue = resolve_command(manifest["scheduler"]["squeue_executable"])
    if squeue:
        try:
            code, out, _ = run_bounded([squeue, "-h", "-j", job_id, "-o", "%i|%T|%N|%R"], timeout_seconds=10)
            if code == 0:
                row = parse_squeue_row(out, job_id)
                if row is not None:
                    raw = row["raw_state"]
                    if raw in ("PENDING", "CONFIGURING", "RESIZING", "REQUEUED"):
                        return {"state": "queued", "terminal": False, "scheduler_observation": row}
                    if raw in ("RUNNING", "COMPLETING", "STAGE_OUT", "SUSPENDED"):
                        return {"state": "running", "terminal": False, "scheduler_observation": row}
        except Failure:
            pass
    sacct = resolve_command(manifest["scheduler"]["sacct_executable"])
    if not sacct:
        return {"state": "outcome_unknown", "terminal": False, "scheduler_query_error": "sacct_unavailable"}
    try:
        code, out, _ = run_bounded([sacct, "-n", "-P", "-j", job_id, "--format=JobIDRaw,State,ExitCode,DerivedExitCode,NodeList"], timeout_seconds=10)
    except Failure as exc:
        return {"state": "outcome_unknown", "terminal": False, "scheduler_query_error": exc.code}
    if code != 0:
        return {"state": "outcome_unknown", "terminal": False, "scheduler_query_error": "sacct_failed"}
    row = parse_sacct_row(out, job_id)
    if row is None:
        return {"state": "outcome_unknown", "terminal": False, "scheduler_query_error": "job_not_reported"}
    raw = row["raw_state"]
    if raw in ("PENDING", "CONFIGURING", "RESIZING", "REQUEUED"):
        return {"state": "queued", "terminal": False, "scheduler_observation": row}
    if raw in ("RUNNING", "COMPLETING", "STAGE_OUT", "SUSPENDED"):
        return {"state": "running", "terminal": False, "scheduler_observation": row}
    if raw == "COMPLETED":
        return {"state": "completed", "terminal": True, "exit_code": 0 if row["exit_code"] is None else row["exit_code"],
                "scheduler_observation": row}
    if raw == "OUT_OF_MEMORY":
        return {"state": "failed", "terminal": True, "exit_code": row["exit_code"],
                "termination_reason": "out_of_memory",
                "oom_evidence": {"source": "slurm_sacct", "run_bound": True, "oom_kill": True},
                "scheduler_observation": row}
    if raw == "TIMEOUT":
        return {"state": "timed_out", "terminal": True, "exit_code": row["exit_code"],
                "termination_reason": "scheduler_timeout", "scheduler_observation": row}
    if raw == "CANCELLED":
        return {"state": "cancelled", "terminal": True, "exit_code": row["exit_code"],
                "termination_reason": "cancelled", "scheduler_observation": row}
    if raw in ("FAILED", "NODE_FAIL", "BOOT_FAIL", "DEADLINE", "PREEMPTED"):
        return {"state": "failed", "terminal": True, "exit_code": row["exit_code"],
                "termination_reason": raw.lower(), "scheduler_observation": row}
    return {"state": "outcome_unknown", "terminal": False, "scheduler_observation": row}

def snapshot(path, manifest):
    base = {
        "run_key": manifest["run_key"],
        "backend": manifest["backend"],
        "simulator": manifest["simulator"],
        "manifest_sha256": sha(js(manifest)),
        "host_id": manifest["profile"]["host_id"],
        "observed_at": now(),
        "remote_directory": path,
        "synthetic": False,
    }
    if path is None or not os.path.lexists(path):
        return dict(base, state="not_staged", terminal=False)
    check_dir(path)
    if not os.path.lexists(os.path.join(path, "manifest.json")):
        return dict(base, state="stage_incomplete", terminal=False)
    manifest_at(path, manifest)
    if not os.path.lexists(os.path.join(path, "ready")):
        return dict(base, state="stage_incomplete", terminal=False)
    accepted = load_optional(path, "accepted.json")
    terminal = load_optional(path, "status.json")
    wrapper = load_optional(path, "wrapper.json")
    supervisor = load_optional(path, "supervisor.json")
    launcher = load_optional(path, "solver-launcher.json")
    scheduler = load_optional(path, "scheduler.json")
    cancel_req = load_optional(path, "cancel-requested.json")
    base.update(
        accepted=accepted, wrapper=wrapper, supervisor=supervisor,
        solver_launcher=launcher, scheduler=scheduler,
        cancel_requested=cancel_req, limits=manifest["limits"],
    )
    if terminal:
        if terminal.get("manifest_sha256") != base["manifest_sha256"]:
            raise Failure("receipt_mismatch")
        return dict(base, state=terminal["state"], terminal=terminal.get("terminal", False), result=terminal)
    if not os.path.lexists(os.path.join(path, "launch-claim")):
        return dict(base, state="staged", terminal=False)
    backend = manifest["backend"]
    if backend == "ssh_direct":
        if alive(wrapper):
            return dict(base, state="running", terminal=False)
        return dict(base, state="outcome_unknown", terminal=False)
    if scheduler is None or not scheduler.get("job_id"):
        return dict(base, state="outcome_unknown", terminal=False)
    if backend == "lsf":
        obs = query_lsf(manifest, scheduler, cancel_req)
    elif backend == "slurm":
        obs = query_slurm(manifest, scheduler, cancel_req)
    else:
        raise Failure("unsupported_backend")
    result = dict(base, state=obs["state"], terminal=obs["terminal"],
                  scheduler_observation=obs.get("scheduler_observation"))
    if obs.get("scheduler_query_error"):
        result["scheduler_query_error"] = obs["scheduler_query_error"]
    if obs["terminal"]:
        synth = {
            "manifest_sha256": base["manifest_sha256"],
            "state": obs["state"],
            "terminal": True,
            "exit_code": obs.get("exit_code"),
            "finished_at": now(),
            "source": "scheduler_accounting",
            "measurement_validity": "not_evaluated",
            "specification_result": "not_evaluated",
        }
        if obs.get("termination_reason"):
            synth["termination_reason"] = obs["termination_reason"]
        if obs.get("oom_evidence"):
            synth["oom_evidence"] = obs["oom_evidence"]
        write_atomic(os.path.join(path, "status.json"), synth)
        result["result"] = synth
    return result

def stage(request):
    manifest = request["manifest"]
    identity(manifest)
    path = directory(manifest, True)
    try:
        os.mkdir(path, 0o700)
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise
        return snapshot(path, manifest)
    sync_dir(os.path.dirname(path))
    write_new(os.path.join(path, "manifest.json"), js(manifest))
    for name, expected_hash in sorted(manifest["input_hashes"].items()):
        content = decoded(request["files"][name])
        if sha(content) != expected_hash:
            raise Failure("input_hash_mismatch")
        ensure_rel_parent(path, name)
        write_new(os.path.join(path, name), content)
    write_new(os.path.join(path, "ready"), b"ready\n")
    sync_dir(path)
    return snapshot(path, manifest)

def bounded_version(manifest, path):
    expected = manifest.get("expected_version")
    if expected is None:
        return
    simulator = manifest["simulator"]
    flag = "-W" if simulator == "spectre" else "--version"
    def limit_output():
        os.setsid()
        resource.setrlimit(resource.RLIMIT_FSIZE, (65536, 65536))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    cmd = [manifest["executable"], flag]
    if os.path.isfile("/usr/bin/timeout") and os.access("/usr/bin/timeout", os.X_OK):
        cmd = ["/usr/bin/timeout", "--kill-after=1s", "8s"] + cmd
    with tempfile.TemporaryFile(dir=path) as output:
        process = subprocess.Popen(cmd, stdin=DEVNULL, stdout=output, stderr=subprocess.STDOUT, preexec_fn=limit_output)
        try:
            code = wait_for(process, 10)
        except WaitExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise Failure("version_probe_timeout")
        output.seek(0)
        raw = output.read(65537)
    if code != 0 or len(raw) > 65536:
        raise Failure("version_probe_failed")
    text = raw.decode("ascii", "replace")
    if simulator == "spectre":
        matches = re.findall(r"(?im)^\s*sub-version\s+([0-9]+(?:\.[0-9]+){2,4}(?:\.isr[0-9]+)?)\s*$", text)
    else:
        matches = re.findall(r"(?im)^\s*\**\s*ngspice(?:-|\s+)([0-9]+(?:\.[0-9]+)*(?:\+|-dev)?)\b", text)
    if matches != [expected]:
        raise Failure("version_mismatch")
    write_atomic(os.path.join(path, "version.json"), {"executable": manifest["executable"], "version": matches[0], "checked_at": now()})

def requirements(manifest):
    if not sys.platform.startswith("linux") or sys.version_info < (3, 6):
        raise Failure("unsupported_remote_runtime")
    executable = manifest["executable"]
    if not os.path.isfile(executable) or not os.access(executable, os.X_OK):
        raise Failure("missing_remote_tool")
    backend = manifest["backend"]
    if backend == "ssh_direct":
        if not proc_identity(os.getpid()):
            raise Failure("unsupported_remote_runtime")
        if not os.path.isfile("/usr/bin/timeout") or not os.access("/usr/bin/timeout", os.X_OK):
            raise Failure("missing_remote_tool")
    elif backend == "lsf":
        for key in ("bsub_executable", "bjobs_executable", "bkill_executable"):
            if not resolve_command(manifest["scheduler"][key]):
                raise Failure("missing_scheduler_tool")
    elif backend == "slurm":
        for key in ("sbatch_executable", "squeue_executable", "scancel_executable"):
            if not resolve_command(manifest["scheduler"][key]):
                raise Failure("missing_scheduler_tool")

def detached_wrapper(path):
    def detach():
        os.setsid()
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
    fd = os.open(os.path.join(path, "wrapper.log"), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        return subprocess.Popen(
            [sys.executable, os.path.join(path, "wrapper.py"), "--wrapper"],
            cwd=path, stdin=DEVNULL, stdout=fd, stderr=fd, close_fds=True, preexec_fn=detach,
        )
    finally:
        os.close(fd)

def write_scheduler_script(path, manifest):
    script = (
        "#!/bin/sh\n"
        "set -u\n"
        "exec %s %s --scheduler-exec\n" % (
            shlex.quote(manifest["remote_python"]),
            shlex.quote(os.path.join(path, "wrapper.py")),
        )
    ).encode("ascii")
    job_path = os.path.join(path, "job.sh")
    write_new(job_path, script, mode=0o700)
    return job_path

def submit_lsf(path, manifest):
    job_script = write_scheduler_script(path, manifest)
    bsub = resolve_command(manifest["scheduler"]["bsub_executable"])
    if not bsub:
        raise Failure("missing_scheduler_tool")
    limits = manifest["limits"]
    sched = manifest["scheduler"]
    wall_mins = max(1, int(math.ceil(limits["wall_seconds"] / 60.0)))
    mem_mb = max(64, int(math.ceil(limits["memory_bytes"] / (1024.0 * 1024.0))))
    argv = [
        bsub,
        "-q", sched["queue"],
        "-n", str(limits["cpu_count"]),
        "-W", str(wall_mins),
        "-R", "span[hosts=1] rusage[mem=%d]" % mem_mb,
        "-M", str(mem_mb),
        "-J", "spiceowl-" + manifest["run_key"][:48],
        "-cwd", path,
        "-oo", os.path.join(path, "console.log"),
        "-eo", os.path.join(path, "console.log"),
    ]
    if sched.get("project"):
        argv.extend(["-P", sched["project"]])
    if sched.get("job_group"):
        argv.extend(["-g", sched["job_group"]])
    if sched.get("exclusive"):
        argv.append("-x")
    argv.append(job_script)
    code, out, _ = run_bounded(argv, timeout_seconds=15, cwd=path)
    if code != 0:
        raise Failure("lsf_submit_failed")
    job_id = parse_bsub_id(out)
    if not job_id:
        raise Failure("lsf_job_id_unrecognized")
    write_atomic(os.path.join(path, "scheduler.json"), {
        "backend": "lsf",
        "job_id": job_id,
        "queue": sched["queue"],
        "project": sched.get("project"),
        "manifest_sha256": sha(js(manifest)),
        "submitted_at": now(),
    })

def submit_slurm(path, manifest):
    job_script = write_scheduler_script(path, manifest)
    sbatch = resolve_command(manifest["scheduler"]["sbatch_executable"])
    if not sbatch:
        raise Failure("missing_scheduler_tool")
    limits = manifest["limits"]
    sched = manifest["scheduler"]
    wall = limits["wall_seconds"]
    days, rem = divmod(wall, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    time_spec = "%d-%02d:%02d:%02d" % (days, hours, mins, secs) if days else "%02d:%02d:%02d" % (hours, mins, secs)
    mem_mb = max(64, int(math.ceil(limits["memory_bytes"] / (1024.0 * 1024.0))))
    argv = [
        sbatch,
        "--parsable",
        "--partition", sched["partition"],
        "--nodes=1",
        "--ntasks=1",
        "--cpus-per-task", str(limits["cpu_count"]),
        "--mem=%dM" % mem_mb,
        "--time", time_spec,
        "--job-name", "spiceowl-" + manifest["run_key"][:48],
        "--chdir", path,
        "--output", os.path.join(path, "console.log"),
        "--error", os.path.join(path, "console.log"),
    ]
    if sched.get("account"):
        argv.extend(["--account", sched["account"]])
    if sched.get("qos"):
        argv.extend(["--qos", sched["qos"]])
    if sched.get("exclusive"):
        argv.append("--exclusive")
    argv.append(job_script)
    code, out, _ = run_bounded(argv, timeout_seconds=15, cwd=path)
    if code != 0:
        raise Failure("slurm_submit_failed")
    parsed = parse_sbatch_id(out)
    if not parsed:
        raise Failure("slurm_job_id_unrecognized")
    write_atomic(os.path.join(path, "scheduler.json"), {
        "backend": "slurm",
        "job_id": parsed["job_id"],
        "cluster": parsed.get("cluster"),
        "partition": sched["partition"],
        "account": sched.get("account"),
        "manifest_sha256": sha(js(manifest)),
        "submitted_at": now(),
    })

def launch(request):
    manifest = request["manifest"]
    identity(manifest)
    path = directory(manifest)
    current = snapshot(path, manifest)
    if current["state"] != "staged":
        return current
    requirements(manifest)
    try:
        os.mkdir(os.path.join(path, "launch-claim"), 0o700)
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise
        return snapshot(path, manifest)
    sync_dir(path)
    write_atomic(os.path.join(path, "accepted.json"), {
        "run_key": manifest["run_key"],
        "backend": manifest["backend"],
        "manifest_sha256": sha(js(manifest)),
        "accepted_at": now(),
        "dispatch_claimed": True,
    })
    try:
        bounded_version(manifest, path)
        backend = manifest["backend"]
        if backend == "ssh_direct":
            detached_wrapper(path)
        elif backend == "lsf":
            submit_lsf(path, manifest)
        elif backend == "slurm":
            submit_slurm(path, manifest)
        else:
            raise Failure("unsupported_backend")
    except Exception as exc:
        code = exc.code if isinstance(exc, Failure) else "launch_start_failed"
        write_atomic(os.path.join(path, "status.json"), {
            "manifest_sha256": sha(js(manifest)),
            "state": "preflight_failed",
            "terminal": True,
            "error": code,
            "finished_at": now(),
        })
    return snapshot(path, manifest)

def cancel_run(request):
    manifest = request["manifest"]
    identity(manifest)
    path = directory(manifest)
    current = snapshot(path, manifest)
    action_key = request["action_key"]
    base = {
        "run_key": manifest["run_key"],
        "backend": manifest["backend"],
        "manifest_sha256": sha(js(manifest)),
        "action_key": action_key,
        "observed_at": now(),
    }
    if current.get("terminal"):
        return dict(base, outcome="already_terminal", state=current["state"])
    if current["state"] in ("not_staged", "stage_incomplete", "staged"):
        return dict(base, outcome="rejected", reason="run_not_launched", state=current["state"])
    write_atomic(os.path.join(path, "cancel-requested.json"), {
        "action_key": action_key,
        "manifest_sha256": base["manifest_sha256"],
        "requested_at": now(),
    })
    backend = manifest["backend"]
    if backend == "ssh_direct":
        supervisor = load_optional(path, "supervisor.json")
        wrapper = load_optional(path, "wrapper.json")
        signaled = []
        for target_name, rec in (("supervisor", supervisor), ("wrapper", wrapper)):
            if alive(rec):
                try:
                    os.killpg(rec["pid"], signal.SIGTERM)
                except OSError:
                    try:
                        os.kill(rec["pid"], signal.SIGTERM)
                    except OSError:
                        pass
                signaled.append({"role": target_name, "pid": rec["pid"]})
        if not signaled:
            return dict(base, outcome="unknown", reason="process_identity_not_alive", state=current["state"])
        return dict(base, outcome="confirmed", signaled=signaled, state="cancelling")
    scheduler = load_optional(path, "scheduler.json")
    if not scheduler or scheduler.get("manifest_sha256") != base["manifest_sha256"] or not re.fullmatch(r"[0-9]{1,20}", str(scheduler.get("job_id", ""))):
        return dict(base, outcome="rejected", reason="scheduler_identity_unverified", state=current["state"])
    job_id = scheduler["job_id"]
    if backend == "lsf":
        bkill = resolve_command(manifest["scheduler"]["bkill_executable"])
        if not bkill:
            return dict(base, outcome="unknown", reason="bkill_unavailable", state=current["state"])
        code, _, _ = run_bounded([bkill, job_id], timeout_seconds=10)
        return dict(base, outcome="confirmed" if code == 0 else "unknown", job_id=job_id, exit_code=code, state="cancelling" if code == 0 else current["state"])
    if backend == "slurm":
        scancel = resolve_command(manifest["scheduler"]["scancel_executable"])
        if not scancel:
            return dict(base, outcome="unknown", reason="scancel_unavailable", state=current["state"])
        code, _, _ = run_bounded([scancel, job_id], timeout_seconds=10)
        return dict(base, outcome="confirmed" if code == 0 else "unknown", job_id=job_id, exit_code=code, state="cancelling" if code == 0 else current["state"])
    raise Failure("unsupported_backend")

def solver_exec(path, manifest):
    manifest_at(path, manifest)
    record = proc_identity(os.getpid())
    if record is not None:
        record.update(recorded_at=now(), role="solver_launcher_before_exec", executable=manifest["executable"])
        write_atomic(os.path.join(path, "solver-launcher.json"), record)
    if manifest["simulator"] == "ngspice":
        raw_dir = os.path.join(path, "results.raw")
        if not os.path.lexists(raw_dir):
            os.mkdir(raw_dir, 0o700)
    os.execv(manifest["executable"], manifest["argv"])

def _build_env(manifest):
    threads = str(manifest["limits"]["cpu_count"])
    env = dict(os.environ)
    env.update(manifest["license_environment"])
    env.update(LC_ALL="C", LANG="C", OMP_NUM_THREADS=threads, MKL_NUM_THREADS=threads)
    return env

def scheduler_exec_main(path, manifest):
    manifest_at(path, manifest)
    started = clock()
    record = proc_identity(os.getpid())
    if record is not None:
        record.update(recorded_at=now(), role="scheduler_job_wrapper",
                      exec_host=socket.gethostname(),
                      slurm_job_id=os.environ.get("SLURM_JOB_ID"),
                      lsf_job_id=os.environ.get("LSB_JOBID"))
        write_atomic(os.path.join(path, "wrapper.json"), record)
    if manifest["simulator"] == "ngspice":
        raw_dir = os.path.join(path, "results.raw")
        if not os.path.lexists(raw_dir):
            os.mkdir(raw_dir, 0o700)
    process = None
    try:
        process = subprocess.Popen(
            manifest["argv"], cwd=path, stdin=DEVNULL, env=_build_env(manifest),
        )
        code = process.wait()
        cancelled = os.path.lexists(os.path.join(path, "cancel-requested.json"))
        if cancelled and code != 0:
            state = "cancelled"
        elif code == 0:
            state = "completed"
        else:
            state = "failed"
        result = {
            "manifest_sha256": sha(js(manifest)),
            "state": state,
            "terminal": True,
            "exit_code": code,
            "finished_at": now(),
            "exec_host": socket.gethostname(),
            "wall_elapsed_seconds": round(clock() - started, 3),
            "measurement_validity": "not_evaluated",
            "specification_result": "not_evaluated",
        }
        if code in (137, -9):
            result["termination_reason"] = "kill_signal_cause_unknown"
    except Exception:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        result = {
            "manifest_sha256": sha(js(manifest)),
            "state": "failed",
            "terminal": True,
            "error": "scheduler_wrapper_failure",
            "finished_at": now(),
            "measurement_validity": "not_evaluated",
        }
    write_atomic(os.path.join(path, "status.json"), result)
    sys.exit(result.get("exit_code") if isinstance(result.get("exit_code"), int) else 1)

def wrapper_main():
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    path = os.path.dirname(os.path.abspath(__file__))
    check_dir(path)
    manifest = read_json(os.path.join(path, "manifest.json"))
    if len(sys.argv) > 1 and sys.argv[1] == "--scheduler-exec":
        scheduler_exec_main(path, manifest)
        return
    identity(manifest)
    manifest_at(path, manifest)
    requirements(manifest)
    if len(sys.argv) > 1 and sys.argv[1] == "--solver-exec":
        solver_exec(path, manifest)
        return
    record = proc_identity(os.getpid())
    record.update(recorded_at=now(), role="detached_wrapper")
    write_atomic(os.path.join(path, "wrapper.json"), record)
    limits = manifest["limits"]
    started = clock()
    process = None
    watchdog_expired = False
    try:
        term_after = max(1, limits["wall_seconds"] - limits["kill_grace_seconds"])
        def constrain():
            os.setsid()
            if limits.get("enforce_rlimit_as"):
                resource.setrlimit(resource.RLIMIT_AS, (limits["memory_bytes"], limits["memory_bytes"]))
            resource.setrlimit(resource.RLIMIT_FSIZE, (limits["file_size_bytes"], limits["file_size_bytes"]))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        fd = os.open(os.path.join(path, "console.log"), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            process = subprocess.Popen(
                ["/usr/bin/timeout", "--signal=TERM", "--kill-after=%ds" % limits["kill_grace_seconds"],
                 "%ds" % term_after, sys.executable, os.path.join(path, "wrapper.py"), "--solver-exec"],
                cwd=path, stdin=DEVNULL, stdout=fd, stderr=fd, preexec_fn=constrain, env=_build_env(manifest),
            )
        finally:
            os.close(fd)
        supervisor = proc_identity(process.pid)
        if supervisor:
            supervisor.update(recorded_at=now(), role="timeout_supervisor")
            write_atomic(os.path.join(path, "supervisor.json"), supervisor)
        try:
            code = wait_for(process, max(1, limits["wall_seconds"] - (clock() - started)))
        except WaitExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            code = 124
            watchdog_expired = True
        cancelled = os.path.lexists(os.path.join(path, "cancel-requested.json"))
        if cancelled and code != 0:
            state = "cancelled"
        elif code == 0:
            state = "completed"
        elif code == 124 or watchdog_expired:
            state = "timed_out"
        else:
            state = "failed"
        result = {
            "manifest_sha256": sha(js(manifest)),
            "state": state,
            "terminal": True,
            "exit_code": code,
            "finished_at": now(),
            "wall_elapsed_seconds": round(clock() - started, 3),
            "watchdog_expired": watchdog_expired,
            "measurement_validity": "not_evaluated",
            "specification_result": "not_evaluated",
        }
        if code in (137, -9) and not cancelled:
            result["termination_reason"] = "kill_signal_cause_unknown"
    except Exception:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        result = {
            "manifest_sha256": sha(js(manifest)),
            "state": "failed",
            "terminal": True,
            "error": "wrapper_failure",
            "finished_at": now(),
            "measurement_validity": "not_evaluated",
        }
    write_atomic(os.path.join(path, "status.json"), result)

def collect(request):
    manifest = request["manifest"]
    identity(manifest)
    path = directory(manifest)
    receipt = snapshot(path, manifest)
    if not receipt.get("terminal"):
        raise Failure("run_not_terminal")
    max_total = min(MAX_TOTAL, int(request.get("max_collection_bytes", MAX_TOTAL)))
    files = []
    skipped = []
    total = 0
    priority_paths = [name for name in STATIC_FILES if os.path.lexists(os.path.join(path, name))]
    raw_paths = []
    raw_dir = os.path.join(path, "results.raw")
    if os.path.lexists(raw_dir):
        if not stat.S_ISDIR(os.lstat(raw_dir).st_mode):
            raise Failure("unsafe_raw_directory")
        for root, dirs, names in os.walk(raw_dir, followlinks=False):
            if root.count(os.sep) - raw_dir.count(os.sep) > 3 or len(priority_paths) + len(raw_paths) + len(dirs) + len(names) > MAX_COLLECTION_FILES:
                raise Failure("artifact_count_limit")
            for name in dirs + names:
                if not re.fullmatch(r"[A-Za-z0-9_.+-]{1,128}", name) or name in (".", ".."):
                    raise Failure("unsafe_artifact_name")
                full = os.path.join(root, name)
                if os.path.islink(full):
                    raise Failure("unsafe_artifact_link")
            raw_paths.extend(os.path.relpath(os.path.join(root, name), path) for name in sorted(names))
    # Collect logs and metadata first so large raw waveforms never starve logs.
    for name in sorted(priority_paths) + sorted(raw_paths):
        full = os.path.join(path, name)
        info = os.lstat(full)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise Failure("unsafe_or_large_artifact")
        if info.st_size > max_total - total:
            if name in STATIC_FILES:
                raise Failure("collection_size_limit")
            skipped.append({"name": name, "bytes": info.st_size, "reason": "collection_size_limit"})
            continue
        content = read_file(full, min(MAX_FILE, max_total - total))
        total += len(content)
        files.append({"name": name, "bytes": len(content), "sha256": sha(content),
                      "data_base64": base64.b64encode(content).decode("ascii")})
    return {
        "receipt": receipt,
        "manifest_sha256": sha(js(manifest)),
        "files": files,
        "skipped_files": skipped,
        "collection_complete": len(skipped) == 0,
        "total_bytes": total,
        "raw_present": any(item["name"].startswith("results.raw/") for item in files) or any(item["name"].startswith("results.raw/") for item in skipped),
        "measurement_validity": "not_evaluated",
        "specification_result": "not_evaluated",
    }

def request_main(request):
    manifest = request["manifest"]
    identity(manifest)
    operation = request["operation"]
    if operation == "stage":
        return stage(request)
    if operation == "launch":
        return launch(request)
    if operation == "status":
        return snapshot(directory(manifest), manifest)
    if operation == "cancel":
        return cancel_run(request)
    if operation == "collect":
        return collect(request)
    raise Failure("unsupported_operation")

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] in ("--wrapper", "--solver-exec", "--scheduler-exec"):
        wrapper_main()
    else:
        request = json.loads(decoded(sys.argv[1]).decode("ascii"))
        nonce = request["nonce"]
        try:
            result = {"ok": True, "data": request_main(request)}
        except Exception as exc:
            result = {"ok": False, "error": exc.code if isinstance(exc, Failure) else "remote_operation_failed"}
        print("__SPICEOWL_DIRECT_%s_BEGIN__" % nonce)
        print(js(result).decode("ascii"))
        print("__SPICEOWL_DIRECT_%s_END__" % nonce)
'''


def prepare(
    profile,
    run_key,
    netlist_text,
    *,
    backend="ssh_direct",
    simulator="spectre",
    expected_version=None,
    support_files=None,
    limits=None,
    scheduler=None,
    output_format="psfascii",
    include_dirs=None,
    remote_python="/usr/bin/python3",
    remote_root=".spiceowl-runs",
    license_environment=None,
):
    """Build a persistable, tamper-evident execution plan without I/O."""
    normalized = validate_host_profile(profile)
    require_id(run_key, "direct run key")
    if backend not in BACKENDS:
        raise DomainError("Backend must be one of ssh_direct, lsf, or slurm.", "invalid_direct_plan")
    if simulator not in SIMULATORS:
        raise DomainError("Simulator must be spectre or ngspice.", "invalid_direct_plan")
    if output_format not in OUTPUT_FORMATS:
        raise DomainError("output_format must be psfascii, psfbin, or psfxl.", "invalid_direct_plan")
    tools = [t for t in normalized["tools"] if t["kind"] == simulator]
    if len(tools) != 1:
        raise DomainError(f"Host profile must configure exactly one {simulator} executable.", "invalid_direct_plan")
    if expected_version is not None:
        if not isinstance(expected_version, str) or _VERSION_PATTERNS[simulator].fullmatch(expected_version) is None:
            raise DomainError("Invalid expected simulator version token.", "invalid_direct_plan")
    if not isinstance(remote_python, str) or _PYTHON_PATH.fullmatch(remote_python) is None:
        raise DomainError("Select an explicit absolute Python 3 interpreter path.", "invalid_direct_plan")
    validated_root = _validate_remote_root(remote_root)
    validated_limits = _validate_limits(limits)
    validated_scheduler = _validate_scheduler(backend, scheduler)

    license_env = {} if license_environment is None else license_environment
    if (type(license_env) is not dict
            or not set(license_env) <= _LICENSE_KEYS
            or any(not isinstance(v, str) or _LICENSE_VALUE.fullmatch(v) is None for v in license_env.values())):
        raise DomainError("license_environment accepts only explicit CDS_LIC_FILE, LM_LICENSE_FILE, or CDS_AUTO_64BIT.", "invalid_direct_plan")

    validated_includes = []
    if include_dirs is not None:
        if type(include_dirs) is not list or len(include_dirs) > 16:
            raise DomainError("include_dirs must be a list of at most 16 absolute POSIX paths.", "invalid_direct_plan")
        validated_includes = [_validate_abs_dir(item, "include_dir") for item in include_dirs]

    if not isinstance(netlist_text, str) or "\0" in netlist_text:
        raise DomainError("Netlist must be valid UTF-8 text without NUL bytes.", "invalid_direct_plan")
    netlist_bytes = netlist_text.encode("utf-8")
    if not 1 <= len(netlist_bytes) <= MAX_NETLIST_BYTES:
        raise DomainError("Netlist must be 1 byte to 1 MiB.", "invalid_direct_plan")

    raw_support = {} if support_files is None else support_files
    if type(raw_support) is not dict or len(raw_support) > MAX_SUPPORT_FILES:
        raise DomainError("support_files must be a mapping of at most 64 relative files.", "invalid_direct_plan")

    files = {"input.scs": netlist_bytes, "wrapper.py": _REMOTE_SOURCE.encode("utf-8")}
    total_input = len(netlist_bytes)
    for relpath, content in sorted(raw_support.items()):
        if not isinstance(relpath, str) or _SUPPORT_PATH.fullmatch(relpath) is None:
            raise DomainError("Support file path must be a safe relative POSIX path.", "invalid_direct_plan")
        parts = relpath.split("/")
        if parts[0] in _RESERVED_FILES or any(part in {".", ".."} for part in parts):
            raise DomainError("Support file collides with a reserved execution path.", "invalid_direct_plan")
        if isinstance(content, str):
            if "\0" in content:
                raise DomainError("Support text file must not contain NUL bytes.", "invalid_direct_plan")
            raw_bytes = content.encode("utf-8")
        elif isinstance(content, (bytes, bytearray)):
            raw_bytes = bytes(content)
        else:
            raise DomainError("Support file content must be UTF-8 text or bytes.", "invalid_direct_plan")
        total_input += len(raw_bytes)
        if total_input > MAX_TOTAL_INPUT_BYTES:
            raise DomainError("Total staged input bytes exceed the 4 MiB direct-execution limit.", "invalid_direct_plan")
        files[relpath] = raw_bytes

    executable = tools[0]["executable"]
    argv = _simulator_argv(simulator, executable, validated_limits, output_format, validated_includes)
    manifest = {
        "schema": SCHEMA,
        "run_key": run_key,
        "backend": backend,
        "simulator": simulator,
        "output_format": output_format,
        "profile": normalized,
        "remote_root": validated_root,
        "directory_name": "run-" + _sha((normalized["host_id"] + "\0" + backend + "\0" + run_key).encode("utf-8")),
        "expected_version": expected_version,
        "executable": executable,
        "remote_python": remote_python,
        "include_dirs": validated_includes,
        "license_environment": dict(license_env),
        "scheduler": validated_scheduler,
        "input_hashes": {name: _sha(raw) for name, raw in sorted(files.items())},
        "argv": argv,
        "limits": validated_limits,
    }
    return {
        "schema": SCHEMA,
        "manifest": manifest,
        "manifest_sha256": _sha(_json(manifest)),
        "files": {name: base64.b64encode(raw).decode("ascii") for name, raw in sorted(files.items())},
    }


def _validate(plan):
    try:
        if not isinstance(plan, dict) or set(plan) != {"schema", "manifest", "manifest_sha256", "files"}:
            raise ValueError()
        manifest = plan["manifest"]
        files = plan["files"]
        netlist = base64.b64decode(files["input.scs"], validate=True).decode("utf-8")
        support = {
            name: base64.b64decode(data, validate=True)
            for name, data in files.items()
            if name not in {"input.scs", "wrapper.py"}
        }
        expected = prepare(
            manifest["profile"],
            manifest["run_key"],
            netlist,
            backend=manifest["backend"],
            simulator=manifest["simulator"],
            expected_version=manifest["expected_version"],
            support_files=support,
            limits=manifest["limits"],
            scheduler=manifest["scheduler"],
            output_format=manifest["output_format"],
            include_dirs=manifest["include_dirs"],
            remote_python=manifest["remote_python"],
            remote_root=manifest["remote_root"],
            license_environment=manifest["license_environment"],
        )
        if plan != expected:
            raise ValueError()
        return manifest
    except (KeyError, TypeError, ValueError, UnicodeError, DomainError):
        raise DomainError("The frozen direct execution plan was changed or is invalid.", "invalid_direct_plan") from None


def _exchange(profile, request):
    nonce = request["nonce"]
    source = base64.b64encode(_REMOTE_SOURCE.encode("utf-8")).decode("ascii")
    packed = base64.b64encode(_json(request)).decode("ascii")
    command = (
        "exec %s -c 'import base64;exec(compile(base64.b64decode(\"%s\"),\"<direct>\",\"exec\"))' '%s'"
        % (request["manifest"]["remote_python"], source, packed)
    )
    argv = _ssh_argv(profile["ssh_target"], command, ssh_hostname=profile.get("ssh_hostname"))
    process = None
    stdout = bytearray()
    stderr = _StderrHints()
    deadline = time.monotonic() + SSH_TIMEOUT_SECONDS
    try:
        process = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            close_fds=True, env={**os.environ, "LC_ALL": "C", "LANG": "C"},
            **popen_isolation_kwargs(),
        )
        code = communicate_bounded(
            process,
            deadline=deadline,
            max_output_bytes=MAX_REPLY_BYTES,
            on_stdout=stdout.extend,
            on_stderr=stderr.feed,
            chunk_size=65536,
        )
        if code != 0:
            category = stderr.category() if code == 255 else None
            raise DomainError(_SSH_ERROR_MESSAGES.get(category, "The SSH exchange did not complete."), category or "direct_transport_failed")
        begin = ("__SPICEOWL_DIRECT_%s_BEGIN__\n" % nonce).encode("ascii")
        end = ("\n__SPICEOWL_DIRECT_%s_END__" % nonce).encode("ascii")
        if stdout.count(begin) != 1 or stdout.count(end) != 1:
            raise DomainError("The SSH reply has invalid framing.", "direct_reply_invalid")
        payload = bytes(stdout).split(begin, 1)[1].split(end, 1)[0]
        return json.loads(payload.decode("ascii"))
    except DomainError:
        raise
    except BoundedIOError as error:
        if error.kind == "output_limit":
            raise DomainError("The SSH reply exceeded its size limit.", "direct_reply_limit") from None
        raise DomainError("The bounded SSH exchange timed out.", "direct_transport_timeout") from None
    except subprocess.TimeoutExpired:
        raise DomainError("The bounded SSH exchange timed out.", "direct_transport_timeout") from None
    except (OSError, ValueError, UnicodeError):
        raise DomainError("The bounded SSH exchange was unavailable or malformed.", "direct_transport_failed") from None
    finally:
        if process is not None:
            if process.poll() is None:
                terminate_process_tree(process, force=True, grace_timeout=1.0)
            process.stdout.close()
            process.stderr.close()


def _verify_collection(plan, data, *, max_collection_bytes=MAX_COLLECTION_BYTES):
    files = data.get("files")
    skipped = data.get("skipped_files", [])
    if (not isinstance(files, list) or not isinstance(skipped, list)
            or len(files) + len(skipped) > 256
            or not data.get("receipt", {}).get("terminal")):
        raise DomainError("Collection lacks a terminal receipt or exceeds its file limit.", "collection_invalid")
    names = set()
    total = 0
    content_by_name = {}
    allowed_static = {
        "input.scs", "manifest.json", "accepted.json", "wrapper.json",
        "supervisor.json", "solver-launcher.json", "scheduler.json",
        "cancel-requested.json", "status.json", "spectre.log", "ngspice.log",
        "console.log", "wrapper.log", "version.json",
    }
    raw_pat = re.compile(r"results\.raw/(?:[A-Za-z0-9_+-][A-Za-z0-9_.+-]{0,127}/){0,3}[A-Za-z0-9_+-][A-Za-z0-9_.+-]{0,127}")
    for item in files:
        if not isinstance(item, dict):
            raise DomainError("Collection contains an invalid file record.", "collection_invalid")
        name = item.get("name")
        if not isinstance(name, str) or name in names or (name not in allowed_static and raw_pat.fullmatch(name) is None):
            raise DomainError("Collection contains an unexpected artifact path.", "collection_invalid")
        names.add(name)
        try:
            content = base64.b64decode(item["data_base64"], validate=True)
        except (ValueError, KeyError, TypeError):
            raise DomainError("Collection contains invalid bytes.", "collection_invalid") from None
        total += len(content)
        if total > max_collection_bytes or item.get("bytes") != len(content) or item.get("sha256") != _sha(content):
            raise DomainError("Collection failed its size or digest check.", "collection_invalid")
        content_by_name[name] = content
    if (content_by_name.get("input.scs") != base64.b64decode(plan["files"]["input.scs"])
            or content_by_name.get("manifest.json") != _json(plan["manifest"])):
        raise DomainError("Collected inputs do not match the frozen plan.", "collection_invalid")
    if total != data.get("total_bytes") or "status.json" not in names:
        raise DomainError("Collection is incomplete.", "collection_invalid")
    try:
        terminal = json.loads(content_by_name["status.json"].decode("ascii"))
        receipt = data["receipt"]
        if (terminal.get("manifest_sha256") != plan["manifest_sha256"]
                or terminal.get("terminal") is not True
                or terminal.get("state") != receipt.get("state")):
            raise ValueError()
    except (ValueError, UnicodeError, AttributeError):
        raise DomainError("Collected completion evidence is missing or inconsistent.", "collection_invalid") from None


def _request(plan, operation, extra=None):
    manifest = _validate(plan)
    request = {"operation": operation, "nonce": secrets.token_hex(16), "manifest": manifest}
    if extra:
        request.update(extra)
    if operation == "stage":
        request["files"] = plan["files"]
    try:
        reply = _exchange(manifest["profile"], request)
        if not isinstance(reply, dict) or type(reply.get("ok")) is not bool:
            raise DomainError("Invalid direct execution receipt.", "direct_reply_invalid")
        if not reply["ok"]:
            code = reply.get("error")
            if not isinstance(code, str) or not re.fullmatch(r"[a-z_]{1,64}", code):
                code = "remote_operation_failed"
            return {
                "operation": operation,
                "state": "outcome_unknown" if operation in ("stage", "launch", "cancel") else "rejected",
                "error": {"code": code},
                "run_key": manifest["run_key"],
                "backend": manifest["backend"],
                "manifest_sha256": plan["manifest_sha256"],
            }
        data = reply.get("data")
        if not isinstance(data, dict) or data.get("manifest_sha256") != plan["manifest_sha256"]:
            raise DomainError("Receipt does not match the frozen inputs.", "receipt_mismatch")
        if operation == "collect":
            limit = int((extra or {}).get("max_collection_bytes", MAX_COLLECTION_BYTES))
            _verify_collection(plan, data, max_collection_bytes=limit)
        return data
    except DomainError as exc:
        return {
            "operation": operation,
            "state": "outcome_unknown" if operation in ("stage", "launch", "cancel") else "unobserved",
            "run_key": manifest["run_key"],
            "backend": manifest["backend"],
            "manifest_sha256": plan["manifest_sha256"],
            "error": {"code": exc.code, "message": str(exc)},
        }


def stage(plan):
    """Create the isolated remote directory and write frozen inputs once."""
    return _request(plan, "stage")


def launch(plan):
    """Claim at most one launch (SSH detached wrapper, LSF bsub, or Slurm sbatch)."""
    return _request(plan, "launch")


def status(plan):
    """Observe the bound execution identity without launching or mutating state."""
    return _request(plan, "status")


def cancel(plan, action_key):
    """Cancel only the verified owned process group or scheduler job for this plan."""
    require_id(action_key, "cancel action key")
    return _request(plan, "cancel", {"action_key": action_key})


def collect(plan, *, max_collection_bytes=MAX_COLLECTION_BYTES):
    """Collect bounded terminal artifacts and verify all hashes."""
    if type(max_collection_bytes) is not int or not 65536 <= max_collection_bytes <= MAX_COLLECTION_BYTES:
        raise DomainError("max_collection_bytes must be between 64 KiB and 32 MiB.", "invalid_direct_plan")
    return _request(plan, "collect", {"max_collection_bytes": max_collection_bytes})


_DATASET_REL = re.compile(r"(?:[A-Za-z0-9_+-][A-Za-z0-9_.+-]{0,127}/){0,3}[A-Za-z0-9_+-][A-Za-z0-9_.+-]{0,127}\Z")

_REMOTE_WAVEFORM_DRIVER = r'''
import base64, json, os, pwd, socket, stat, sys

def _fail(code, msg):
    return {"ok": False, "error": code, "message": msg}

def _run():
    req = json.loads(sys.stdin.read())
    nonce = req["nonce"]
    manifest = req["manifest"]
    expected = manifest["profile"]
    actual_host = socket.gethostname()
    actual_user = pwd.getpwuid(os.getuid()).pw_name
    if actual_host != expected["expected_host"] or actual_user != expected["expected_user"]:
        out = _fail("identity_mismatch", "Remote host or user does not match frozen plan.")
    else:
        home = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
        rroot = manifest["remote_root"]
        root = rroot if rroot.startswith("/") else os.path.join(home, rroot)
        run_dir = os.path.join(root, manifest["directory_name"])
        raw_dir = os.path.join(run_dir, "results.raw")
        if not os.path.isdir(raw_dir) or os.path.islink(raw_dir):
            out = _fail("raw_dir_missing", "Remote results.raw directory was not found.")
        else:
            mod_ns = {"__name__": "spiceowl_remote_psf"}
            psf_src = base64.b64decode(req["psf_module_b64"].encode("ascii")).decode("utf-8")
            exec(compile(psf_src, "<psf_binary>", "exec"), mod_ns)
            op = req["operation"]
            try:
                if op == "waveform_index":
                    data = mod_ns["index_waveform_directory"](raw_dir)
                else:
                    rel = req["dataset"]
                    target = os.path.realpath(os.path.join(raw_dir, rel))
                    real_raw = os.path.realpath(raw_dir)
                    if not (target.startswith(real_raw + os.sep) and os.path.isfile(target)):
                        raise mod_ns["DomainError"]("Requested dataset is outside results.raw.", "dataset_not_found")
                    if op == "waveform_query":
                        data = mod_ns["query_waveform"](
                            target, req["signals"],
                            x_min=req.get("x_min"), x_max=req.get("x_max"),
                            max_points=req.get("max_points", 2000), mode=req.get("mode", "auto"),
                        )
                    elif op == "waveform_cycles":
                        data = mod_ns["analyze_cycles"](
                            target, req["signals"],
                            period=req.get("period"), clock_signal=req.get("clock_signal"),
                            threshold=req.get("threshold"), edge=req.get("edge", "rising"),
                            t_start=req.get("t_start"), t_end=req.get("t_end"),
                            worst_metric=req.get("worst_metric", "peak_to_peak"),
                            worst_signal=req.get("worst_signal"),
                            cycle_index=req.get("cycle_index"),
                            context_cycles=req.get("context_cycles", 0),
                            overlay_cycles=req.get("overlay_cycles", 10),
                            max_points_per_cycle=req.get("max_points_per_cycle", 2000),
                        )
                    else:
                        raise mod_ns["DomainError"]("Unsupported waveform operation.", "unsupported_operation")
                data["manifest_sha256"] = req["manifest_sha256"]
                data["source"] = "remote_in_place"
                out = {"ok": True, "data": data}
            except Exception as exc:
                code = getattr(exc, "code", "remote_waveform_failed")
                out = _fail(code, str(exc)[:300])
    print("__SPICEOWL_DIRECT_%s_BEGIN__" % nonce)
    print(json.dumps(out, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    print("__SPICEOWL_DIRECT_%s_END__" % nonce)

if __name__ == "__main__":
    _run()
'''


def _validate_dataset_rel(dataset):
    if not isinstance(dataset, str) or _DATASET_REL.fullmatch(dataset) is None or ".." in dataset.split("/"):
        raise DomainError("Dataset path must be a safe relative path inside results.raw.", "invalid_waveform_query")
    return dataset


def _waveform_exchange(plan, operation, payload=None):
    """Run a bounded read-only in-place waveform operation on the remote Linux host."""
    manifest = _validate(plan)
    from pathlib import Path as _Path
    psf_path = _Path(__file__).resolve().parents[2] / "integrations" / "psf_binary.py"
    psf_b64 = base64.b64encode(psf_path.read_bytes()).decode("ascii")
    nonce = secrets.token_hex(16)
    request = {
        "operation": operation,
        "nonce": nonce,
        "manifest": manifest,
        "manifest_sha256": plan["manifest_sha256"],
        "psf_module_b64": psf_b64,
    }
    if payload:
        request.update(payload)
    driver_b64 = base64.b64encode(_REMOTE_WAVEFORM_DRIVER.encode("utf-8")).decode("ascii")
    payload_bytes = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    # Pass the ~70 KiB request payload over stdin (not CLI argv) so Windows
    # CreateProcess's 32 KiB command-line limit is never exceeded.
    command = (
        "exec %s -c 'import base64;exec(compile(base64.b64decode(\"%s\"),\"<waveform>\",\"exec\"))'"
        % (manifest["remote_python"], driver_b64)
    )
    argv = _ssh_argv(manifest["profile"]["ssh_target"], command, ssh_hostname=manifest["profile"].get("ssh_hostname"))
    process = None
    feeder = None
    stdout = bytearray()
    stderr = _StderrHints()
    deadline = time.monotonic() + SSH_TIMEOUT_SECONDS
    try:
        process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            close_fds=True, env={**os.environ, "LC_ALL": "C", "LANG": "C"},
            **popen_isolation_kwargs(),
        )
        import threading as _threading

        def _feed_stdin():
            try:
                if process.stdin is not None:
                    process.stdin.write(payload_bytes)
                    process.stdin.flush()
            except OSError:
                pass
            finally:
                try:
                    if process.stdin is not None:
                        process.stdin.close()
                except OSError:
                    pass

        feeder = _threading.Thread(target=_feed_stdin, daemon=True)
        feeder.start()
        code = communicate_bounded(
            process, deadline=deadline, max_output_bytes=MAX_REPLY_BYTES,
            on_stdout=stdout.extend, on_stderr=stderr.feed, chunk_size=65536,
        )
        if code != 0:
            category = stderr.category() if code == 255 else None
            raise DomainError(_SSH_ERROR_MESSAGES.get(category, "Remote waveform query did not complete."), category or "direct_transport_failed")
        begin = ("__SPICEOWL_DIRECT_%s_BEGIN__\n" % nonce).encode("ascii")
        end = ("\n__SPICEOWL_DIRECT_%s_END__" % nonce).encode("ascii")
        if stdout.count(begin) != 1 or stdout.count(end) != 1:
            raise DomainError("Remote waveform reply has invalid framing.", "direct_reply_invalid")
        reply = json.loads(bytes(stdout).split(begin, 1)[1].split(end, 1)[0].decode("ascii"))
        if not isinstance(reply, dict) or not reply.get("ok"):
            err_code = reply.get("error", "remote_waveform_failed") if isinstance(reply, dict) else "direct_reply_invalid"
            err_msg = reply.get("message", "Remote waveform query failed.") if isinstance(reply, dict) else "Invalid reply."
            raise DomainError(err_msg, err_code)
        data = reply.get("data")
        if not isinstance(data, dict) or data.get("manifest_sha256") != plan["manifest_sha256"]:
            raise DomainError("Remote waveform reply failed manifest binding check.", "receipt_mismatch")
        return data
    except DomainError:
        raise
    except BoundedIOError as error:
        if error.kind == "output_limit":
            raise DomainError("Remote waveform reply exceeded size budget.", "direct_reply_limit") from None
        raise DomainError("Remote waveform query timed out.", "direct_transport_timeout") from None
    except (OSError, ValueError, UnicodeError):
        raise DomainError("Remote waveform query was unavailable or malformed.", "direct_transport_failed") from None
    finally:
        if process is not None:
            if process.poll() is None:
                terminate_process_tree(process, force=True, grace_timeout=1.0)
            if feeder is not None:
                feeder.join(timeout=1.0)
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
            process.stdout.close()
            process.stderr.close()


def waveform_index(plan):
    """Build a lightweight waveform index in-place on the remote host without downloading raw files."""
    return _waveform_exchange(plan, "waveform_index")


def waveform_query(plan, dataset, signals, *, x_min=None, x_max=None, max_points=2000, mode="auto"):
    """Run bounded in-place envelope downsampling or exact slicing on the remote host."""
    return _waveform_exchange(plan, "waveform_query", {
        "dataset": _validate_dataset_rel(dataset),
        "signals": list(signals),
        "x_min": x_min,
        "x_max": x_max,
        "max_points": max_points,
        "mode": mode,
    })


def waveform_cycles(plan, dataset, signals, *, period=None, clock_signal=None, threshold=None,
                    edge="rising", t_start=None, t_end=None, worst_metric="peak_to_peak",
                    worst_signal=None, cycle_index=None, context_cycles=0,
                    overlay_cycles=10, max_points_per_cycle=2000):
    """Run streaming cycle-by-cycle metrics, worst-case detection, and exact cycle slicing remotely."""
    return _waveform_exchange(plan, "waveform_cycles", {
        "dataset": _validate_dataset_rel(dataset),
        "signals": list(signals),
        "period": period,
        "clock_signal": clock_signal,
        "threshold": threshold,
        "edge": edge,
        "t_start": t_start,
        "t_end": t_end,
        "worst_metric": worst_metric,
        "worst_signal": worst_signal,
        "cycle_index": cycle_index,
        "context_cycles": context_cycles,
        "overlay_cycles": overlay_cycles,
        "max_points_per_cycle": max_points_per_cycle,
    })

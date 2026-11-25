"""Opt-in transport for one original, flattened MOS1 inverter smoke run.

This maintainer utility is not registered with the CLI, HTTP server or MCP.
``prepare`` is pure; ``stage`` and ``launch`` write to/operate the selected host
and require the operator's concrete authorization. Persist the returned plan
before either call. A lost response is unknown: use ``status`` with that same
plan, never generate a replacement key or blindly launch again.

Remote requirements: Linux, an explicitly selected Python 3.6+ interpreter,
GNU /usr/bin/timeout and the
selected Spectre executable. A 4 GiB RLIMIT_AS limits virtual address space,
not RSS or an allocated cgroup memory budget. No PDK, external includes, native
ADE task, automatic retry, cancellation or checkpoint recovery is supported.
"""

import base64
import hashlib
import json
import os
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
from spiceowl.adapters.hosts import validate_host_profile
from spiceowl.adapters.executors.ssh import _ssh_argv, _StderrHints, _SSH_ERROR_MESSAGES
from spiceowl.domain.common import DomainError, require_id


SCHEMA = "spiceowl.inverter-smoke.v1"
MAX_INPUT_BYTES = 16384
MAX_COLLECTION_BYTES = 8 * 1024 * 1024
MAX_REPLY_BYTES = 12 * 1024 * 1024
SSH_TIMEOUT_SECONDS = 30
LIMITS = {"wall_seconds": 120, "term_after_seconds": 115, "kill_grace_seconds": 5,
          "cpu_seconds": 120, "cpu_count": 1, "address_space_bytes": 4 * 1024**3,
          "file_size_bytes": 32 * 1024**2, "license_queue_seconds": 60}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _sha(value):
    return hashlib.sha256(value).hexdigest()


# Sent as base64, so even a csh login shell receives a single-line command.
# This same source is the detached wrapper, whose digest is frozen in the plan.
_REMOTE_SOURCE = r'''
import base64, ctypes, errno, hashlib, json, os, pwd, re, resource, signal, socket, stat, subprocess, sys, tempfile, time

MAX_FILE = 8 * 1024 * 1024
MAX_TOTAL = 8 * 1024 * 1024
ROOT_NAME = ".spiceowl-smoke"
DEVNULL = open(os.devnull, "rb")
STATIC_FILES = ("input.scs", "manifest.json", "accepted.json", "wrapper.json",
                "supervisor.json", "solver-launcher.json", "status.json",
                "spectre.log", "console.log", "wrapper.log", "version.json")

class Failure(Exception):
    def __init__(self, code):
        self.code = code

class WaitExpired(Exception):
    pass

def wait_for(process, seconds):
    deadline = clock() + seconds
    while process.poll() is None:
        if clock() >= deadline:
            raise WaitExpired()
        time.sleep(0.02)
    return process.returncode

def clock():
    # Linux uptime is monotonic even with the Python 2.7 standard library.
    with open("/proc/uptime") as stream:
        return float(stream.read(128).split()[0])

def decoded(value):
    if not re.match(r"\A[A-Za-z0-9+/]*={0,2}\Z", value):
        raise Failure("invalid_base64")
    return base64.b64decode(value)

def allowed_cpu():
    with open("/proc/self/status") as stream:
        for line in stream:
            if line.startswith("Cpus_allowed_list:"):
                return int(line.split(":", 1)[1].strip().split(",", 1)[0].split("-", 1)[0])
    raise Failure("cpu_affinity_unavailable")

def set_cpu(cpu):
    if not 0 <= cpu < 32768:
        raise Failure("cpu_affinity_unavailable")
    mask = ctypes.create_string_buffer(4096)
    mask[cpu // 8] = bytes(bytearray([1 << (cpu % 8)])) if sys.version_info[0] >= 3 else chr(1 << (cpu % 8))
    if ctypes.CDLL(None, use_errno=True).sched_setaffinity(0, len(mask), ctypes.byref(mask)) != 0:
        raise Failure("cpu_affinity_unavailable")

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

def directory(manifest, create=False):
    root = os.path.join(home_path(), ROOT_NAME)
    if create:
        try:
            os.mkdir(root, 0o700)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
    if not os.path.lexists(root):
        return None
    check_dir(root)
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
    return json.loads(read_file(path, 65536).decode("ascii"))

def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def write_new(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        offset = 0
        while offset < len(value):
            offset += os.write(fd, value[offset:])
        os.fsync(fd)
    finally:
        os.close(fd)

def write_atomic(path, value):
    # A new temporary regular file is renamed over the exact owned filename.
    # A pre-existing symlink is replaced, never followed.
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

def manifest_at(path, expected):
    raw = read_file(os.path.join(path, "manifest.json"), 65536)
    if raw != js(expected):
        raise Failure("run_key_conflict")
    for name, digest in expected["input_hashes"].items():
        if sha(read_file(os.path.join(path, name), 65536)) != digest:
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

def snapshot(path, manifest):
    base = {"run_key": manifest["run_key"], "manifest_sha256": sha(js(manifest)),
            "host_id": manifest["profile"]["host_id"], "observed_at": now(),
            "remote_directory": path, "synthetic": False}
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
    base.update(accepted=accepted, wrapper=wrapper, supervisor=supervisor,
                solver_launcher=launcher, limits=manifest["limits"])
    if terminal:
        if terminal.get("manifest_sha256") != base["manifest_sha256"]:
            raise Failure("receipt_mismatch")
        return dict(base, state=terminal["state"], terminal=terminal.get("terminal", False), result=terminal)
    if not os.path.lexists(os.path.join(path, "launch-claim")):
        return dict(base, state="staged", terminal=False)
    return dict(base, state="running" if alive(wrapper) else "outcome_unknown", terminal=False)

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
    # Interrupted staging is never overwritten or repaired automatically.
    write_new(os.path.join(path, "manifest.json"), js(manifest))
    for name in ("input.scs", "wrapper.py"):
        content = decoded(request["files"][name])
        if len(content) > 65536 or sha(content) != manifest["input_hashes"][name]:
            raise Failure("input_hash_mismatch")
        write_new(os.path.join(path, name), content)
    write_new(os.path.join(path, "ready"), b"ready\n")
    sync_dir(path)
    return snapshot(path, manifest)

def bounded_version(manifest, path):
    limits = manifest["limits"]
    def limit_output():
        os.setsid()
        resource.setrlimit(resource.RLIMIT_FSIZE, (65536, 65536))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    with tempfile.TemporaryFile(dir=path) as output:
        process = subprocess.Popen(["/usr/bin/timeout", "--kill-after=1s", "8s", manifest["executable"], "-W"],
                                   stdin=DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                   preexec_fn=limit_output)
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
    matches = re.findall(r"(?im)^\s*sub-version\s+([0-9]+(?:\.[0-9]+){2,4}(?:\.isr[0-9]+)?)\s*$", raw.decode("ascii", "replace"))
    if matches != [manifest["expected_version"]]:
        raise Failure("version_mismatch")
    write_atomic(os.path.join(path, "version.json"), {"executable": manifest["executable"], "version": matches[0], "checked_at": now()})

def requirements(manifest):
    if not sys.platform.startswith("linux") or sys.version_info < (3, 6) or not proc_identity(os.getpid()):
        raise Failure("unsupported_remote_runtime")
    for executable in ("/usr/bin/timeout", manifest["executable"]):
        if not os.path.isfile(executable) or not os.access(executable, os.X_OK):
            raise Failure("missing_remote_tool")

def detached_wrapper(path):
    def detach():
        os.setsid()
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
    fd = os.open(os.path.join(path, "wrapper.log"), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        return subprocess.Popen([sys.executable, os.path.join(path, "wrapper.py"), "--wrapper"], cwd=path,
                                stdin=DEVNULL, stdout=fd, stderr=fd, close_fds=True, preexec_fn=detach)
    finally:
        os.close(fd)

def launch(request):
    manifest = request["manifest"]
    identity(manifest)
    path = directory(manifest)
    current = snapshot(path, manifest)
    if current["state"] != "staged":
        return current
    requirements(manifest)
    # This permanent, atomic directory is the dispatch claim. Never delete it,
    # even if the controller or wrapper disappears before recording its PID.
    try:
        os.mkdir(os.path.join(path, "launch-claim"), 0o700)
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise
        return snapshot(path, manifest)
    sync_dir(path)
    write_atomic(os.path.join(path, "accepted.json"), {"run_key": manifest["run_key"],
                 "manifest_sha256": sha(js(manifest)), "accepted_at": now(), "dispatch_claimed": True})
    try:
        bounded_version(manifest, path)
        detached_wrapper(path)
    except Exception as exc:
        code = exc.code if isinstance(exc, Failure) else "wrapper_start_failed"
        write_atomic(os.path.join(path, "status.json"), {"manifest_sha256": sha(js(manifest)),
                     "state": "preflight_failed", "terminal": True, "error": code, "finished_at": now()})
    return snapshot(path, manifest)

def solver_exec(path, manifest):
    identity(manifest)
    manifest_at(path, manifest)
    record = proc_identity(os.getpid())
    if record is None:
        raise Failure("process_identity_unavailable")
    record.update(recorded_at=now(), role="solver_launcher_before_exec", executable=manifest["executable"])
    write_atomic(os.path.join(path, "solver-launcher.json"), record)
    os.execv(manifest["executable"], manifest["argv"])

def wrapper_main():
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    path = os.path.dirname(os.path.abspath(__file__))
    check_dir(path)
    manifest = read_json(os.path.join(path, "manifest.json"))
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
        cpu = allowed_cpu()
        def constrain():
            os.setsid()
            set_cpu(cpu)
            resource.setrlimit(resource.RLIMIT_AS, (limits["address_space_bytes"], limits["address_space_bytes"]))
            resource.setrlimit(resource.RLIMIT_CPU, (limits["cpu_seconds"], limits["cpu_seconds"]))
            resource.setrlimit(resource.RLIMIT_FSIZE, (limits["file_size_bytes"], limits["file_size_bytes"]))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        fd = os.open(os.path.join(path, "console.log"), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            process = subprocess.Popen(["/usr/bin/timeout", "--signal=TERM", "--kill-after=5s", "115s",
                                        sys.executable, os.path.join(path, "wrapper.py"), "--solver-exec"],
                                       cwd=path, stdin=DEVNULL, stdout=fd, stderr=fd, preexec_fn=constrain,
                                       env=dict(dict(os.environ, **manifest["license_environment"]),
                                                LC_ALL="C", LANG="C", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1"))
        finally:
            os.close(fd)
        supervisor = proc_identity(process.pid)
        if supervisor:
            supervisor.update(recorded_at=now(), role="timeout_supervisor", cpu_affinity=[cpu])
            write_atomic(os.path.join(path, "supervisor.json"), supervisor)
        try:
            code = wait_for(process, max(1, limits["wall_seconds"] - (clock() - started)))
        except WaitExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            code = 124
            watchdog_expired = True
        result = {"manifest_sha256": sha(js(manifest)), "state": "completed" if code == 0 else "timed_out" if code == 124 or watchdog_expired else "failed",
                  "terminal": True, "exit_code": code, "finished_at": now(), "wall_elapsed_seconds": round(clock()-started, 3),
                  "watchdog_expired": watchdog_expired, "measurement_validity": "not_evaluated", "specification_result": "not_evaluated"}
        if code in (137, -9):
            result["termination_reason"] = "kill_signal_cause_unknown"
    except Exception:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        result = {"manifest_sha256": sha(js(manifest)), "state": "failed", "terminal": True,
                  "error": "wrapper_failure", "finished_at": now(), "measurement_validity": "not_evaluated"}
    write_atomic(os.path.join(path, "status.json"), result)

def collect(request):
    manifest = request["manifest"]
    identity(manifest)
    path = directory(manifest)
    receipt = snapshot(path, manifest)
    if not receipt.get("terminal"):
        raise Failure("run_not_terminal")
    files = []
    total = 0
    paths = [name for name in STATIC_FILES if os.path.lexists(os.path.join(path, name))]
    raw_dir = os.path.join(path, "results.raw")
    if os.path.lexists(raw_dir):
        if not stat.S_ISDIR(os.lstat(raw_dir).st_mode):
            raise Failure("unsafe_raw_directory")
        for root, dirs, names in os.walk(raw_dir, followlinks=False):
            if root.count(os.sep) - raw_dir.count(os.sep) > 3 or len(paths) + len(dirs) + len(names) > 128:
                raise Failure("artifact_count_limit")
            for name in dirs + names:
                if not re.fullmatch(r"[A-Za-z0-9_.+-]{1,128}", name) or name in (".", ".."):
                    raise Failure("unsafe_artifact_name")
                full = os.path.join(root, name)
                if os.path.islink(full):
                    raise Failure("unsafe_artifact_link")
            paths.extend(os.path.relpath(os.path.join(root, name), path) for name in names)
    for name in sorted(paths):
        content = read_file(os.path.join(path, name), min(MAX_FILE, MAX_TOTAL - total))
        total += len(content)
        if total > MAX_TOTAL:
            raise Failure("collection_size_limit")
        files.append({"name": name, "bytes": len(content), "sha256": sha(content),
                      "data_base64": base64.b64encode(content).decode("ascii")})
    return {"receipt": receipt, "manifest_sha256": sha(js(manifest)), "files": files,
            "total_bytes": total, "raw_present": any(item["name"].startswith("results.raw/") for item in files),
            "measurement_validity": "not_evaluated", "specification_result": "not_evaluated"}

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
    if operation == "collect":
        return collect(request)
    raise Failure("unsupported_operation")

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] in ("--wrapper", "--solver-exec"):
        wrapper_main()
    else:
        request = json.loads(decoded(sys.argv[1]).decode("ascii"))
        nonce = request["nonce"]
        try:
            result = {"ok": True, "data": request_main(request)}
        except Exception as exc:
            result = {"ok": False, "error": exc.code if isinstance(exc, Failure) else "remote_operation_failed"}
        print("__SPICEOWL_SMOKE_%s_BEGIN__" % nonce)
        print(js(result).decode("ascii"))
        print("__SPICEOWL_SMOKE_%s_END__" % nonce)
'''


def prepare(profile, run_key, netlist_text, expected_version, *, remote_python="/usr/bin/python3", license_environment=None):
    """Build a persistable private plan without filesystem/network operations.

    The caller supplies the reviewed original MOS1 inverter with all includes
    already flattened. This validation is deliberately not a Spectre parser or
    a sandbox for arbitrary third-party model code.
    """
    normalized = validate_host_profile(profile)
    license_environment = {} if license_environment is None else license_environment
    if (type(license_environment) is not dict
            or not set(license_environment) <= {"CDS_LIC_FILE", "LM_LICENSE_FILE"}
            or any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:/@+-]{1,2048}", value)
                   for value in license_environment.values())):
        raise DomainError("License environment accepts only explicit CDS_LIC_FILE / LM_LICENSE_FILE settings.")
    if not isinstance(remote_python, str) or not re.fullmatch(r"/(?:[A-Za-z0-9_+-][A-Za-z0-9_.+-]*/)*python(?:[23](?:\.[0-9]+)?)?", remote_python):
        raise DomainError("Select an explicit absolute Python interpreter path.")
    require_id(run_key, "smoke run key")
    tools = [t for t in normalized["tools"] if t["kind"] == "spectre"]
    if len(tools) != 1:
        raise DomainError("Select exactly one configured Spectre executable.")
    if not isinstance(expected_version, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){2,4}(?:\.isr[0-9]+)?", expected_version):
        raise DomainError("Provide the exact previously verified Spectre version.")
    if not isinstance(netlist_text, str) or not 0 < len(netlist_text.encode("utf-8")) <= MAX_INPUT_BYTES or "\0" in netlist_text:
        raise DomainError("The flattened inverter input must be 1–16384 UTF-8 bytes.")
    active = "\n".join(line.split("//", 1)[0] for line in netlist_text.splitlines())
    active = re.sub(r"\\\r?\n", " ", active)
    if re.search(r"\b(?:include|ahdl_include|system|shell|verilog|veriloga)\b|[\"'`;\\$]|\b(?:file|filename|directory|rawfile|write|writefinal)\s*=", active, re.I):
        raise DomainError("Smoke input must be self-contained; external code, includes and custom output files are unsupported.")
    models = re.findall(r"(?im)^\s*model\s+\w+\s+(\w+)\b", active)
    if not models or any(model.lower() != "mos1" for model in models) or not re.search(r"\btran\b", active, re.I):
        raise DomainError("This smoke utility accepts only an original MOS1 transient testbench.")
    executable = tools[0]["executable"]
    files = {"input.scs": netlist_text.encode("utf-8"), "wrapper.py": _REMOTE_SOURCE.encode("utf-8")}
    manifest = {"schema": SCHEMA, "run_key": run_key, "profile": normalized,
                "directory_name": "run-" + _sha((normalized["host_id"] + "\0" + run_key).encode()),
                "expected_version": expected_version, "executable": executable, "remote_python": remote_python,
                "license_environment": dict(license_environment),
                "input_hashes": {name: _sha(content) for name, content in files.items()},
                "argv": [executable, "input.scs", "-format", "psfascii", "-raw", "results.raw",
                         "+log", "spectre.log", "+lqtimeout", "60", "+mt=1"],
                "limits": dict(LIMITS)}
    return {"schema": SCHEMA, "manifest": manifest, "manifest_sha256": _sha(_json(manifest)),
            "files": {name: base64.b64encode(content).decode("ascii") for name, content in files.items()}}


def _validate(plan):
    try:
        if not isinstance(plan, dict) or set(plan) != {"schema", "manifest", "manifest_sha256", "files"}:
            raise ValueError()
        manifest = plan["manifest"]
        netlist = base64.b64decode(plan["files"]["input.scs"], validate=True).decode("utf-8")
        expected = prepare(manifest["profile"], manifest["run_key"], netlist, manifest["expected_version"],
                           remote_python=manifest["remote_python"], license_environment=manifest["license_environment"])
        if plan != expected:
            raise ValueError()
        return manifest
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise DomainError("The frozen smoke plan was changed or is incompatible with this utility.", "invalid_smoke_plan") from None


def _exchange(profile, request):
    nonce = request["nonce"]
    source = base64.b64encode(_REMOTE_SOURCE.encode()).decode("ascii")
    packed = base64.b64encode(_json(request)).decode("ascii")
    # Only base64 alphabets are interpolated. _ssh_argv quotes the one-line
    # /bin/sh command for the user's login shell and keeps strict SSH options.
    command = "exec %s -c 'import base64;exec(compile(base64.b64decode(\"%s\"),\"<smoke>\",\"exec\"))' '%s'" % (request["manifest"]["remote_python"], source, packed)
    argv = _ssh_argv(profile["ssh_target"], command, ssh_hostname=profile.get("ssh_hostname"))
    process = None
    stdout = bytearray()
    stderr = _StderrHints()
    deadline = time.monotonic() + SSH_TIMEOUT_SECONDS
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   close_fds=True, env={**os.environ, "LC_ALL": "C", "LANG": "C"},
                                   **popen_isolation_kwargs())
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
            raise DomainError(_SSH_ERROR_MESSAGES.get(category, "The SSH exchange did not complete."), category or "smoke_transport_failed")
        begin = ("__SPICEOWL_SMOKE_%s_BEGIN__\n" % nonce).encode()
        end = ("\n__SPICEOWL_SMOKE_%s_END__" % nonce).encode()
        if stdout.count(begin) != 1 or stdout.count(end) != 1:
            raise DomainError("The SSH reply has invalid framing.", "smoke_reply_invalid")
        payload = bytes(stdout).split(begin, 1)[1].split(end, 1)[0]
        return json.loads(payload.decode("ascii"))
    except DomainError:
        raise
    except BoundedIOError as error:
        if error.kind == "output_limit":
            raise DomainError("The SSH reply exceeded its size limit.", "smoke_reply_limit") from None
        raise DomainError("The bounded SSH exchange timed out.", "smoke_transport_timeout") from None
    except subprocess.TimeoutExpired:
        raise DomainError("The bounded SSH exchange timed out.", "smoke_transport_timeout") from None
    except (OSError, ValueError, UnicodeError):
        raise DomainError("The bounded SSH exchange was unavailable or malformed.", "smoke_transport_failed") from None
    finally:
        if process is not None:
            if process.poll() is None:
                terminate_process_tree(process, force=True, grace_timeout=1.0)
            process.stdout.close()
            process.stderr.close()


def _request(plan, operation):
    manifest = _validate(plan)
    request = {"operation": operation, "nonce": secrets.token_hex(16), "manifest": manifest}
    if operation == "stage":
        request["files"] = plan["files"]
    try:
        reply = _exchange(manifest["profile"], request)
        if not isinstance(reply, dict) or type(reply.get("ok")) is not bool:
            raise DomainError("Invalid smoke receipt.", "smoke_reply_invalid")
        if not reply["ok"]:
            code = reply.get("error")
            if not isinstance(code, str) or not re.fullmatch(r"[a-z_]{1,64}", code):
                code = "remote_operation_failed"
            return {"operation": operation, "state": "outcome_unknown" if operation in ("stage", "launch") else "rejected", "error": {"code": code},
                    "run_key": manifest["run_key"], "manifest_sha256": plan["manifest_sha256"]}
        data = reply.get("data")
        if not isinstance(data, dict) or data.get("manifest_sha256") != plan["manifest_sha256"]:
            raise DomainError("Receipt does not match the frozen inputs.", "receipt_mismatch")
        if operation == "collect":
            _verify_collection(plan, data)
        return data
    except DomainError as exc:
        return {"operation": operation, "state": "outcome_unknown" if operation in ("stage", "launch") else "unobserved",
                "run_key": manifest["run_key"], "manifest_sha256": plan["manifest_sha256"],
                "error": {"code": exc.code, "message": str(exc)}}


def _verify_collection(plan, data):
    files = data.get("files")
    if not isinstance(files, list) or len(files) > 128 or not data.get("receipt", {}).get("terminal"):
        raise DomainError("Collection lacks a terminal receipt or exceeds its file limit.", "collection_invalid")
    names = set()
    total = 0
    content_by_name = {}
    allowed = {"input.scs", "manifest.json", "accepted.json", "wrapper.json", "supervisor.json", "solver-launcher.json",
               "status.json", "spectre.log", "console.log", "wrapper.log", "version.json"}
    for item in files:
        if not isinstance(item, dict):
            raise DomainError("Collection contains an invalid file record.", "collection_invalid")
        name = item.get("name")
        if not isinstance(name, str) or name in names or (name not in allowed and not re.fullmatch(r"results\.raw/(?:[A-Za-z0-9_+-][A-Za-z0-9_.+-]{0,127}/){0,3}[A-Za-z0-9_+-][A-Za-z0-9_.+-]{0,127}", name)):
            raise DomainError("Collection contains an unexpected artifact path.", "collection_invalid")
        names.add(name)
        try:
            content = base64.b64decode(item["data_base64"], validate=True)
        except (ValueError, KeyError, TypeError):
            raise DomainError("Collection contains invalid bytes.", "collection_invalid") from None
        total += len(content)
        if total > MAX_COLLECTION_BYTES or item.get("bytes") != len(content) or item.get("sha256") != _sha(content):
            raise DomainError("Collection failed its size or digest check.", "collection_invalid")
        content_by_name[name] = content
    if content_by_name.get("input.scs") != base64.b64decode(plan["files"]["input.scs"]) or content_by_name.get("manifest.json") != _json(plan["manifest"]):
        raise DomainError("Collected inputs do not match the frozen plan.", "collection_invalid")
    if total != data.get("total_bytes") or "status.json" not in names:
        raise DomainError("Collection is incomplete.", "collection_invalid")
    try:
        terminal = json.loads(content_by_name["status.json"].decode("ascii"))
        receipt = data["receipt"]
        if terminal.get("manifest_sha256") != plan["manifest_sha256"] or terminal.get("terminal") is not True or terminal.get("state") != receipt.get("state"):
            raise ValueError()
        if terminal.get("state") == "completed" and ("spectre.log" not in names or not any(name.startswith("results.raw/") for name in names)):
            raise ValueError()
    except (ValueError, UnicodeError, AttributeError):
        raise DomainError("Collected completion evidence is missing or inconsistent.", "collection_invalid") from None


def stage(plan):
    """Explicitly create the unique private remote directory and frozen files."""
    return _request(plan, "stage")


def launch(plan):
    """Explicitly claim at most one launch. Repeated calls never dispatch again."""
    return _request(plan, "launch")


def status(plan):
    """Read only this run's receipt and process identities without re-launching."""
    return _request(plan, "status")


def collect(plan):
    """Read bounded final artifacts; verify hashes without extracting an archive."""
    return _request(plan, "collect")

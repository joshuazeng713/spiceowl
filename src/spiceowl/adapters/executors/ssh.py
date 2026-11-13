"""Bounded identity and fixed version probes over an existing OpenSSH alias.

The existing SSH configuration and remote executables are operator-trusted.
Alias routing may include ProxyJump, ProxyCommand or Match exec; this is not an
OS sandbox. This module does not read, print, create or change credentials or
known-host files itself. OpenSSH owns its configured authentication flow.

Options are based on https://man.openbsd.org/ssh_config (checked 2026-11-13).
The only remote templates are identity, fixed versions and Linux aggregates.
There is no public arbitrary-command interface or retry operation.
Timeouts bound the local SSH client; they do not prove a remote process stopped.
"""

from dataclasses import dataclass
import os
import re
import secrets
import selectors
import shlex
import signal
import subprocess
import time

from spiceowl.domain.common import DomainError, utcnow
from spiceowl.adapters.simulators.probes import parse_version, version_arguments
from spiceowl.adapters.resources import empty_resources, parse_resource_sample


PROBE_TIMEOUT_SECONDS = 30.0
COMMAND_TIMEOUT_SECONDS = 10.0
MAX_OUTPUT_BYTES = 65536  # Combined stdout + stderr, including startup warnings.
RESOURCE_TIMEOUT_SECONDS = 10.0
_SSH_OPTIONS = (
    "BatchMode=yes", "StrictHostKeyChecking=yes", "ForwardAgent=no",
    "PermitLocalCommand=no", "ControlMaster=no", "ControlPath=none",
    "ControlPersist=no", "UpdateHostKeys=no", "AddKeysToAgent=no",
    "VerifyHostKeyDNS=no", "ClearAllForwardings=yes", "ForwardX11=no",
    "RemoteCommand=none", "SessionType=default", "RequestTTY=no",
    "ForkAfterAuthentication=no", "ConnectionAttempts=1", "ConnectTimeout=5",
    "ServerAliveInterval=5", "ServerAliveCountMax=1", "NumberOfPasswordPrompts=0",
    "LogLevel=ERROR",
)
_IDENTITY_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,252}", re.ASCII)
_IDENTITY_USER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}", re.ASCII)


@dataclass(frozen=True)
class _Reply:
    returncode: int
    stdout: bytes


def _error(code, message):
    return {"code": code, "message": message}


def _marker(nonce, name):
    return f"__SPICEOWL_{nonce}_{name}__"


def _identity_script(nonce):
    start = _marker(nonce, "IDENTITY_BEGIN")
    end = _marker(nonce, "IDENTITY_END")
    return (
        'LC_ALL=C; export LC_ALL; '
        'so_host=$(/bin/hostname 2>/dev/null) || exit 70; '
        'so_user=$(/usr/bin/id -un 2>/dev/null) || exit 70; '
        f'printf "\\n%s\\n%s\\n%s\\n%s\\n" "{start}" "$so_host" "$so_user" "{end}"'
    )


def _tool_script(nonce, profile, tool):
    path = shlex.quote(tool["executable"])
    flags = " ".join(shlex.quote(arg) for arg in version_arguments(tool["kind"]))
    begin = _marker(nonce, "TOOL_BEGIN")
    end = _marker(nonce, "TOOL_END")
    status_begin = _marker(nonce, "STATUS_BEGIN")
    status_end = _marker(nonce, "STATUS_END")
    return (
        _identity_script(nonce)
        + f'; if [ "$so_host" = {shlex.quote(profile["expected_host"])} ] '
        + f'&& [ "$so_user" = {shlex.quote(profile["expected_user"])} ]; then '
        + f'printf "\\n%s\\n" "{begin}"; '
        + f'if [ -f {path} ] && [ -x {path} ]; then {path} {flags} 2>&1; so_status=$?; '
        + 'else so_status=127; fi; '
        + f'printf "\\n%s\\n%s\\n%s\\n%s\\n" "{end}" "{status_begin}" "$so_status" "{status_end}"; '
        + 'else exit 72; fi'
    )


def _ssh_argv(target, script, *, ssh_hostname=None):
    # The server's default shell may be csh. It receives one quoted, single-line
    # command; all assignments/control flow are interpreted by /bin/sh instead.
    remote = "exec /bin/sh -c " + shlex.quote(script)
    argv = ["/usr/bin/ssh", "-T"]
    for option in _SSH_OPTIONS:
        argv.extend(("-o", option))
    if ssh_hostname is not None:
        from spiceowl.adapters.hosts import _validate_ssh_hostname

        hostname = _validate_ssh_hostname(ssh_hostname)
        argv.extend(("-o", f"Hostname={hostname}"))
    return [*argv, "--", target, remote]


def _stop_owned_process(process):
    # start_new_session gives only this client's process tree its own group.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    finally:
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


def _invoke(target, script, deadline, *, ssh_hostname=None):
    """Read pipes incrementally; stderr is counted but never retained/returned."""
    if time.monotonic() >= deadline:
        raise DomainError("SSH probe reached its time limit.", "probe_timeout")
    process = None
    output = bytearray()
    total = 0
    try:
        process = subprocess.Popen(
            _ssh_argv(target, script, ssh_hostname=ssh_hostname), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            shell=False, start_new_session=True, close_fds=True,
        )
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DomainError("SSH probe reached its time limit.", "probe_timeout")
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fileobj.fileno(), min(4096, MAX_OUTPUT_BYTES - total + 1))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(chunk)
                    if total > MAX_OUTPUT_BYTES:
                        raise DomainError("SSH probe exceeded its output limit.", "probe_output_limit")
                    if key.data == "stdout":
                        output.extend(chunk)
            status = process.wait(timeout=max(0, deadline - time.monotonic()))
        return _Reply(status, bytes(output))
    except subprocess.TimeoutExpired:
        raise DomainError("SSH probe reached its time limit.", "probe_timeout") from None
    except OSError:
        raise DomainError("SSH probe could not be completed.", "ssh_unavailable") from None
    finally:
        if process is not None:
            # On failure also reap local proxy descendants that may hold pipes.
            if process.returncode is None:
                _stop_owned_process(process)
            process.stdout.close()
            process.stderr.close()


def _extract(text, nonce, name):
    begin = _marker(nonce, name + "_BEGIN")
    end = _marker(nonce, name + "_END")
    if text.count(begin) != 1 or text.count(end) != 1:
        raise DomainError("SSH probe returned invalid framing.", "invalid_probe_response")
    lines = text.splitlines()
    try:
        first = lines.index(begin)
        last = lines.index(end)
    except ValueError:
        raise DomainError("SSH probe returned invalid framing.", "invalid_probe_response") from None
    if last <= first:
        raise DomainError("SSH probe returned invalid framing.", "invalid_probe_response")
    return lines[first + 1:last], first, last


def _identity(reply, nonce, *, tool=False, resources=False):
    text = reply.stdout.decode("utf-8", errors="replace")
    names = {"IDENTITY_BEGIN", "IDENTITY_END"}
    if tool:
        names |= {"TOOL_BEGIN", "TOOL_END", "STATUS_BEGIN", "STATUS_END"}
    if resources:
        names |= {"RESOURCES_BEGIN", "RESOURCES_END"}
    present = {name for name in names if _marker(nonce, name) in text}
    # Reject duplicate, unknown and embedded markers as well as missing ones.
    # Identity-only output is allowed on the guarded tool's mismatch branch.
    allowed = [{"IDENTITY_BEGIN", "IDENTITY_END"}, names]
    if present not in allowed or text.count(f"__SPICEOWL_{nonce}_") != len(present):
        raise DomainError("SSH probe returned invalid framing.", "invalid_probe_response")
    values, _, _ = _extract(text, nonce, "IDENTITY")
    if len(values) != 2 or _IDENTITY_HOST.fullmatch(values[0]) is None or _IDENTITY_USER.fullmatch(values[1]) is None:
        raise DomainError("SSH probe returned an invalid identity.", "invalid_probe_response")
    return text, {"verified": False, "hostname": values[0], "username": values[1]}


def _matches(identity, profile):
    return identity["hostname"] == profile["expected_host"] and identity["username"] == profile["expected_user"]


def _tool_result(reply, text, nonce, kind):
    _, _, identity_end = _extract(text, nonce, "IDENTITY")
    banner, tool_begin, tool_end = _extract(text, nonce, "TOOL")
    status, status_begin, _ = _extract(text, nonce, "STATUS")
    if not identity_end < tool_begin < tool_end < status_begin or len(status) != 1 or not re.fullmatch(r"[0-9]{1,3}", status[0]):
        raise DomainError("SSH probe returned invalid framing.", "invalid_probe_response")
    code = int(status[0])
    if code > 255:
        raise DomainError("SSH probe returned invalid framing.", "invalid_probe_response")
    if reply.returncode != 0:
        raise DomainError("Tool version probe did not complete.", "version_probe_failed")
    if code in (126, 127):
        return {"kind": kind, "status": "unavailable", "version": None,
                "error": _error("tool_unavailable", "Configured tool could not be executed.")}
    if code != 0:
        raise DomainError("Tool version probe did not complete.", "version_probe_failed")
    return {"kind": kind, "status": "available", "version": parse_version(kind, "\n".join(banner))}


def probe_profile(profile) -> dict:
    """Run one identity probe, then each fixed tool probe once, within one budget."""
    from spiceowl.adapters.hosts import validate_host_profile

    profile = validate_host_profile(profile)
    result = {
        "host_id": profile["host_id"], "synthetic": False, "scope": "connectivity_only",
        "observed_at": utcnow(), "transport": "unknown",
        "identity": {"verified": False, "hostname": None, "username": None},
        "tools": [{"kind": tool["kind"], "status": "unknown", "version": None} for tool in profile["tools"]],
        "error": None,
    }
    deadline = time.monotonic() + PROBE_TIMEOUT_SECONDS
    try:
        nonce = secrets.token_hex(16)
        reply = _invoke(profile["ssh_target"], _identity_script(nonce),
                        min(deadline, time.monotonic() + COMMAND_TIMEOUT_SECONDS),
                        ssh_hostname=profile.get("ssh_hostname"))
        if reply.returncode != 0:
            raise DomainError("SSH identity probe did not complete.", "ssh_probe_failed")
        _, identity = _identity(reply, nonce)
        result["transport"] = "reachable"
        result["identity"] = identity
        if not _matches(identity, profile):
            raise DomainError("Remote identity does not match the host profile.", "identity_mismatch")
        identity["verified"] = True
        for index, tool in enumerate(profile["tools"]):
            nonce = secrets.token_hex(16)
            try:
                reply = _invoke(profile["ssh_target"], _tool_script(nonce, profile, tool),
                                min(deadline, time.monotonic() + COMMAND_TIMEOUT_SECONDS),
                                ssh_hostname=profile.get("ssh_hostname"))
                if reply.returncode not in (0, 72):
                    raise DomainError("SSH tool probe did not complete.", "ssh_probe_failed")
                text, identity = _identity(reply, nonce, tool=True)
                if not _matches(identity, profile):
                    result["identity"] = identity
                    raise DomainError("Remote identity changed during the probe.", "identity_mismatch")
                if reply.returncode != 0:
                    raise DomainError("SSH tool probe did not complete.", "ssh_probe_failed")
                result["tools"][index] = _tool_result(reply, text, nonce, tool["kind"])
            except DomainError as exc:
                result["tools"][index]["error"] = _error(exc.code, str(exc))
                # Only a recognizable tool failure can leave the identity gate
                # intact. Transport/framing uncertainty stops subsequent probes.
                if exc.code not in {"unrecognized_version", "version_probe_failed"}:
                    result["identity"]["verified"] = False
                    if exc.code != "identity_mismatch":
                        result["transport"] = "unknown"
                    raise
    except DomainError as exc:
        result["error"] = _error(exc.code, str(exc))
    finally:
        result["observed_at"] = utcnow()
    return result


def _cpu_sample_script(suffix):
    # suffix is an internal constant (first/second), never a profile parameter.
    cpu = (
        f'$1 == "cpu" {{printf "cpu_{suffix}"; '
        'for (i=2; i<=NF; i++) printf " %s", $i; print ""; next} '
        '$1 ~ /^cpu[0-9]+$/ {n++; next} {exit} '
        f'END {{if (n>0) print "cpu_count_{suffix}", n}}'
    )
    uptime = f'NR==1 {{print "uptime_{suffix}", $1; exit}}'
    return (
        f'/usr/bin/awk {shlex.quote(cpu)} /proc/stat 2>/dev/null; '
        f'/usr/bin/awk {shlex.quote(uptime)} /proc/uptime 2>/dev/null'
    )


def _resource_script(nonce, profile):
    """No process files; only aggregate proc data and the login home filesystem.

    GNU df's explicit byte/available column avoids returning filesystem names
    or paths. A missing GNU timeout/df makes the optional disk value unknown.
    Neither helper nor SSH timeout establishes a remote process outcome.
    """
    begin = _marker(nonce, "RESOURCES_BEGIN")
    end = _marker(nonce, "RESOURCES_END")
    load = 'NR==1 {print "load_1m", $1; print "load_5m", $2; exit}'
    memory = '$1=="MemTotal:" || $1=="MemAvailable:" {print $1, $2, $3}'
    disk = 'NR==2 && NF==1 {print "home_disk_available_bytes", $1}'
    return (
        _identity_script(nonce)
        + f'; if [ "$so_host" = {shlex.quote(profile["expected_host"])} ] '
        + f'&& [ "$so_user" = {shlex.quote(profile["expected_user"])} ]; then '
        + f'printf "\\n%s\\n" "{begin}"; '
        + _cpu_sample_script("first")
        + '; if /bin/sleep 0.2 2>/dev/null; then '
        + _cpu_sample_script("second")
        + '; fi; '
        + f'/usr/bin/awk {shlex.quote(load)} /proc/loadavg 2>/dev/null; '
        + f'/usr/bin/awk {shlex.quote(memory)} /proc/meminfo 2>/dev/null; '
        + 'case "$HOME" in /*) if [ -x /usr/bin/timeout ] && [ -x /bin/df ]; then '
        + 'if so_disk=$(/usr/bin/timeout -k 1s 1s /bin/df -B1 --output=avail -- "$HOME" 2>/dev/null); then '
        + f'printf "%s\\n" "$so_disk" | /usr/bin/awk {shlex.quote(disk)} 2>/dev/null; fi; fi;; esac; '
        + f'printf "\\n%s\\n" "{end}"; else exit 72; fi'
    )


def probe_resource_profile(profile) -> dict:
    """Two identity-gated requests share a ten-second local budget; no retries."""
    from spiceowl.adapters.hosts import validate_host_profile

    profile = validate_host_profile(profile)
    result = {
        "host_id": profile["host_id"], "synthetic": False, "scope": "host_resources",
        "observed_at": utcnow(), "transport": "unknown",
        "identity": {"verified": False, "hostname": None, "username": None},
        "resources": empty_resources(), "error": None,
    }
    deadline = time.monotonic() + RESOURCE_TIMEOUT_SECONDS
    try:
        nonce = secrets.token_hex(16)
        reply = _invoke(profile["ssh_target"], _identity_script(nonce), deadline,
                        ssh_hostname=profile.get("ssh_hostname"))
        if reply.returncode != 0:
            raise DomainError("SSH identity probe did not complete.", "ssh_probe_failed")
        _, identity = _identity(reply, nonce)
        result["transport"] = "reachable"
        result["identity"] = identity
        if not _matches(identity, profile):
            raise DomainError("Remote identity does not match the host profile.", "identity_mismatch")
        identity["verified"] = True
        nonce = secrets.token_hex(16)
        reply = _invoke(profile["ssh_target"], _resource_script(nonce, profile), deadline,
                        ssh_hostname=profile.get("ssh_hostname"))
        if reply.returncode not in (0, 72):
            raise DomainError("SSH resource probe did not complete.", "ssh_probe_failed")
        text, identity = _identity(reply, nonce, resources=True)
        result["identity"] = identity
        if not _matches(identity, profile):
            raise DomainError("Remote identity changed during the probe.", "identity_mismatch")
        if reply.returncode != 0:
            raise DomainError("SSH resource probe did not complete.", "ssh_probe_failed")
        _, _, identity_end = _extract(text, nonce, "IDENTITY")
        payload, resources_begin, _ = _extract(text, nonce, "RESOURCES")
        if resources_begin <= identity_end:
            raise DomainError("SSH probe returned invalid framing.", "invalid_probe_response")
        result["resources"] = parse_resource_sample("\n".join(payload))
        identity["verified"] = True
        if all(value is None for value in result["resources"].values()):
            result["error"] = _error("resources_unavailable", "Supported Linux resource data is unavailable.")
    except DomainError as exc:
        result["identity"]["verified"] = False
        if exc.code != "identity_mismatch":
            result["transport"] = "unknown"
        result["error"] = _error(exc.code, str(exc))
    finally:
        result["observed_at"] = utcnow()
    return result

"""Bounded, read-only remote SSH Library/Netlist browser & CircuitGit entry contract.

Reuses CircuitGit's ``ic.onboarding.ssh-browse.v1`` and ``ic.onboarding.ssh-import.v1``
interfaces verbatim so both standalone SpiceOwl and CircuitGit share one secure
implementation:
- Never interpolates user paths into shell syntax (payload is base64 JSON over stdin).
- Never reads private keys, credentials, licenses, or hidden dotfiles.
- Enforces strict file-count, byte-size, and include-depth caps.
- Optionally parses Cadence ``cds.lib`` library definitions when browsing a workspace
  directory (backwards-compatible additive ``cds_libraries`` field).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import secrets
import subprocess
from typing import Any

from spiceowl.adapters.executors.ssh import resolve_ssh_executable
from spiceowl.domain.common import DomainError

BROWSE_SCHEMA = "ic.onboarding.ssh-browse.v1"
IMPORT_SCHEMA = "ic.onboarding.ssh-import.v1"
CIRCUITGIT_ENTRY_SCHEMA = "spiceowl.circuitgit-entry.v1"

MAX_BROWSE_ENTRIES = 200
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_IMPORT_BYTES = 1400 * 1024
MAX_SUPPORT_FILES = 48
MAX_INCLUDE_DEPTH = 4
SSH_TIMEOUT_SECONDS = 25
MAX_SSH_OUTPUT_BYTES = 1900 * 1024

ALLOWED_CIRCUIT_EXTS = frozenset({
    ".scs", ".sp", ".spi", ".spice", ".cir", ".ckt", ".net",
    ".inc", ".va", ".vams", ".v", ".rnl", ".txt", ".cdl", ".mdl",
})

_SSH_TARGET_RE = re.compile(r"^[A-Za-z0-9._@:%+\-\[\]]+$")
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9._:\-]+$")
_COMMIT_RE = re.compile(r"^[a-f0-9]{40}$")
_SUPPORT_PATH_RE = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")

_SENSITIVE_EXACT = frozenset({
    ".ssh", ".gnupg", ".aws", ".kube", ".env", ".netrc", ".git-credentials",
    ".npmrc", ".pypirc", ".docker",
    "shadow", "passwd", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "authorized_keys", "known_hosts", "credentials",
})
_SENSITIVE_PREFIXES = (".env.",)
_SENSITIVE_SUFFIXES = (".key", ".pem", ".p12", ".pfx", ".jks", ".kdbx", ".lic", ".ppk")
_SENSITIVE_SUBSTRINGS = (
    "id_rsa", "id_ed25519", "id_ecdsa", "secret", "password", "passwd",
    "token", "credential", "license",
)

_SSH_OPTIONS = (
    "BatchMode=yes",
    "ConnectTimeout=10",
    "NumberOfPasswordPrompts=0",
    "KbdInteractiveAuthentication=no",
    "PasswordAuthentication=no",
    "ForwardAgent=no",
    "ForwardX11=no",
    "ClearAllForwardings=yes",
    "RequestTTY=no",
    "ControlMaster=no",
    "ControlPath=none",
    "StdinNull=no",
)


def validate_ssh_target(target: object) -> str:
    """Validate an explicit SSH alias or user@host without options or whitespace."""
    if not isinstance(target, str) or not 1 <= len(target) <= 240:
        raise DomainError("Select an explicit SSH host alias or user@host.")
    if target.startswith("-") or not _SSH_TARGET_RE.fullmatch(target):
        raise DomainError("SSH host target contains unsupported characters.")
    return target


def is_sensitive_name(name: object) -> bool:
    """Reject known credential/secret/key/license/dot-config filenames or directories."""
    if not isinstance(name, str) or not name:
        return True
    low = name.lower()
    if low in _SENSITIVE_EXACT:
        return True
    if any(low.startswith(pre) for pre in _SENSITIVE_PREFIXES):
        return True
    if any(low.endswith(ext) for ext in _SENSITIVE_SUFFIXES):
        return True
    if any(token in low for token in _SENSITIVE_SUBSTRINGS):
        return True
    return False


def is_sensitive_path(path: str) -> bool:
    """Check whether any path component is a sensitive directory or file."""
    parts = [p for p in PurePosixPath(path).parts if p not in ("/", "", "~")]
    return any(is_sensitive_name(part) for part in parts)


def is_circuit_filename(name: object) -> bool:
    if not isinstance(name, str) or not name or name.startswith(".") or is_sensitive_name(name):
        return False
    _, ext = posixpath.splitext(name.lower())
    return ext in ALLOWED_CIRCUIT_EXTS


def validate_remote_path(path: object, *, allow_empty: bool = False, require_circuit_ext: bool = False) -> str:
    """Validate an explicit POSIX remote path (absolute or ~/...)."""
    if path is None or path == "":
        if allow_empty:
            return ""
        raise DomainError("Provide an explicit remote POSIX path.")
    if not isinstance(path, str) or not 1 <= len(path) <= 2048:
        raise DomainError("Remote path must be a non-empty string up to 2048 characters.")
    if any(ord(c) < 32 or ord(c) == 127 for c in path):
        raise DomainError("Remote path contains invalid control characters.")
    if not (path == "~" or path.startswith("~/") or path.startswith("/")):
        raise DomainError("Remote path must be absolute (/...) or start with ~/.")
    parts = PurePosixPath(path).parts
    if ".." in parts:
        raise DomainError("Remote path cannot contain parent traversal segments (..).")
    if is_sensitive_path(path):
        raise DomainError("Remote path points to a hidden or sensitive location.")
    if require_circuit_ext:
        base = posixpath.basename(path.rstrip("/"))
        if not is_circuit_filename(base):
            raise DomainError("Select a supported circuit/netlist file (.scs, .sp, .spice, .cir, .va, .rnl, .txt).")
    return path


_REMOTE_PY = r"""
from __future__ import print_function
import base64
import io
import json
import os
import posixpath
import re
import sys

PAYLOAD = json.loads(base64.b64decode('__PAYLOAD_B64__').decode('utf-8'))
ALLOWED_EXTS = set(PAYLOAD['allowed_exts'])
SENSITIVE_EXACT = set(PAYLOAD['sensitive_exact'])
SENSITIVE_SUFFIXES = tuple(PAYLOAD['sensitive_suffixes'])
SENSITIVE_SUBSTRINGS = tuple(PAYLOAD['sensitive_substrings'])
INCLUDE_RE = re.compile(
    r'^\s*(?:\.?include|ahdl_include|\.lib)\s+(?:"([^"]+)"|\'([^\']+)\'|([^\s;]+))',
    re.IGNORECASE,
)
CDS_DEFINE_RE = re.compile(r'^\s*DEFINE\s+([A-Za-z0-9_.\-]+)\s+(.+?)\s*$', re.IGNORECASE)


def secret_component(name):
    if not name:
        return True
    low = name.lower()
    if low in SENSITIVE_EXACT:
        return True
    for pre in ('.env.',):
        if low.startswith(pre):
            return True
    for suf in SENSITIVE_SUFFIXES:
        if low.endswith(suf):
            return True
    for sub in SENSITIVE_SUBSTRINGS:
        if sub in low:
            return True
    return False


def sensitive_name(name):
    if not name or name.startswith('.'):
        return True
    return secret_component(name)


def sensitive_path(full_path):
    parts = [p for p in full_path.replace('\\', '/').split('/') if p and p != '~']
    for part in parts:
        if secret_component(part):
            return True
    return False


def circuit_file(name):
    if sensitive_name(name):
        return False
    _, ext = posixpath.splitext(name.lower())
    return ext in ALLOWED_EXTS


def resolve_user_path(raw):
    if not raw or raw == '~':
        expanded = os.path.expanduser('~')
    elif raw.startswith('~/'):
        expanded = os.path.join(os.path.expanduser('~'), raw[2:])
    else:
        expanded = raw
    return os.path.realpath(expanded)


def read_utf8(file_path, max_bytes):
    st = os.stat(file_path)
    if st.st_size > max_bytes:
        raise ValueError('File exceeds size limit (%d bytes)' % st.st_size)
    with io.open(file_path, 'rb') as fh:
        raw = fh.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError('File exceeds size limit')
    if b'\x00' in raw:
        raise ValueError('Binary file is not supported')
    return raw.decode('utf-8')


def parse_cds_lib(target_dir):
    cds_path = os.path.join(target_dir, 'cds.lib')
    if not os.path.isfile(cds_path):
        return []
    try:
        text = read_utf8(cds_path, 131072)
    except Exception:
        return []
    libs = []
    seen = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('--') or stripped.startswith('#'):
            continue
        m = CDS_DEFINE_RE.match(stripped)
        if not m:
            continue
        lib_name = m.group(1).strip()
        raw_loc = m.group(2).strip().strip('"').strip("'")
        if not lib_name or not raw_loc or lib_name in seen:
            continue
        if '$' in raw_loc:
            raw_loc = os.path.expandvars(raw_loc)
            if '$' in raw_loc:
                continue
        if raw_loc.startswith('~'):
            cand = os.path.expanduser(raw_loc)
        elif os.path.isabs(raw_loc):
            cand = raw_loc
        else:
            cand = os.path.join(target_dir, raw_loc)
        real_loc = os.path.realpath(cand)
        if sensitive_path(real_loc) or not os.path.isdir(real_loc):
            continue
        seen.add(lib_name)
        libs.append({'name': lib_name, 'path': real_loc})
        if len(libs) >= 48:
            break
    return libs


def do_browse():
    target_dir = resolve_user_path(PAYLOAD.get('path') or '')
    if not os.path.isdir(target_dir):
        return {'error': 'Remote directory does not exist: ' + target_dir}
    try:
        names = os.listdir(target_dir)
    except OSError as exc:
        return {'error': 'Cannot list remote directory: ' + str(exc)}
    dirs = []
    files = []
    for name in names:
        if sensitive_name(name):
            continue
        full = os.path.join(target_dir, name)
        try:
            st = os.stat(full)
        except OSError:
            continue
        if os.path.isdir(full):
            dirs.append({'name': name, 'path': full, 'kind': 'dir', 'size': None})
        elif os.path.isfile(full) and circuit_file(name):
            files.append({'name': name, 'path': full, 'kind': 'file', 'size': int(st.st_size)})
    dirs.sort(key=lambda row: row['name'].lower())

    def file_rank(row):
        _, ext = posixpath.splitext(row['name'].lower())
        pri = 0 if ext == '.scs' else 1 if ext in ('.sp', '.spice', '.cir') else 2
        return (pri, row['name'].lower())

    files.sort(key=file_rank)
    combined = dirs + files
    limit = int(PAYLOAD['max_entries'])
    parent = os.path.dirname(target_dir) if target_dir != '/' else None
    if parent == target_dir:
        parent = None
    cds_libs = parse_cds_lib(target_dir)
    return {
        'ok': True,
        'cwd': target_dir,
        'parent': parent,
        'entries': combined[:limit],
        'total': len(combined),
        'truncated': len(combined) > limit,
        'cds_libraries': cds_libs,
        'netlist_count': len(files),
    }


def collect_includes(text):
    refs = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('//') or stripped.startswith('*'):
            continue
        m = INCLUDE_RE.match(line)
        if not m:
            continue
        ref = m.group(1) or m.group(2) or m.group(3)
        if ref:
            refs.append(ref.strip())
    return refs


def do_import():
    entry = resolve_user_path(PAYLOAD['path'])
    if not os.path.isfile(entry):
        return {'error': 'Remote netlist file does not exist: ' + entry}
    file_name = os.path.basename(entry)
    if not circuit_file(file_name):
        return {'error': 'Unsupported or sensitive remote file: ' + file_name}
    max_file = int(PAYLOAD['max_file_bytes'])
    max_total = int(PAYLOAD['max_total_bytes'])
    max_support = int(PAYLOAD['max_support_files'])
    max_depth = int(PAYLOAD['max_depth'])
    try:
        entry_text = read_utf8(entry, max_file)
    except Exception as exc:
        return {'error': 'Cannot read remote netlist: ' + str(exc)}
    base_dir = os.path.dirname(entry)
    support = {}
    skipped = []
    total_bytes = len(entry_text.encode('utf-8'))
    if PAYLOAD.get('include_dependencies', True):
        queue = [(entry, entry_text, 0)]
        seen = set([entry])
        while queue:
            cur_path, cur_text, depth = queue.pop(0)
            if depth >= max_depth:
                continue
            cur_dir = os.path.dirname(cur_path)
            for ref in collect_includes(cur_text):
                if ref.startswith('/') or ref.startswith('~') or '$' in ref or '\\' in ref:
                    continue
                candidate = os.path.realpath(os.path.join(cur_dir, ref))
                if candidate in seen:
                    continue
                try:
                    rel = os.path.relpath(candidate, base_dir)
                except ValueError:
                    continue
                if rel.startswith('..') or os.path.isabs(rel):
                    skipped.append({'ref': ref, 'reason': 'outside_project_dir'})
                    continue
                rel_posix = rel.replace(os.sep, '/')
                if any(sensitive_name(part) for part in rel_posix.split('/')):
                    skipped.append({'ref': ref, 'reason': 'sensitive_path'})
                    continue
                if not os.path.isfile(candidate) or not circuit_file(os.path.basename(candidate)):
                    continue
                if len(support) >= max_support:
                    skipped.append({'ref': ref, 'reason': 'file_count_limit'})
                    continue
                try:
                    dep_text = read_utf8(candidate, max_file)
                except Exception:
                    skipped.append({'ref': ref, 'reason': 'unreadable_or_too_large'})
                    continue
                dep_bytes = len(dep_text.encode('utf-8'))
                if total_bytes + dep_bytes > max_total:
                    skipped.append({'ref': ref, 'reason': 'bundle_size_limit'})
                    continue
                seen.add(candidate)
                support[rel_posix] = dep_text
                total_bytes += dep_bytes
                queue.append((candidate, dep_text, depth + 1))
    return {
        'ok': True,
        'remote_path': entry,
        'remote_dir': base_dir,
        'file_name': file_name,
        'text': entry_text,
        'support_files': support,
        'skipped': skipped[:32],
    }


result = do_browse() if PAYLOAD['action'] == 'browse' else do_import()
print(json.dumps(result, ensure_ascii=False))
"""


def _build_ssh_argv(ssh_target: str, ssh_config: str | Path | None = None, *, ssh_hostname: str | None = None) -> list[str]:
    argv = [resolve_ssh_executable(), "-T"]
    if ssh_config:
        cfg = Path(ssh_config)
        if cfg.is_file():
            argv.extend(["-F", str(cfg)])
    if ssh_hostname:
        if not isinstance(ssh_hostname, str) or not _HOSTNAME_RE.fullmatch(ssh_hostname):
            raise DomainError("Invalid SSH hostname override.")
        argv.extend(["-o", f"HostName={ssh_hostname}"])
    for option in _SSH_OPTIONS:
        argv.extend(["-o", option])
    argv.extend(["--", ssh_target, "/bin/sh -s"])
    return argv


def _build_remote_shell(payload: dict[str, Any]) -> tuple[str, str, str]:
    nonce = secrets.token_hex(16)
    begin = f"IC_SSH_IMPORT_{nonce}_BEGIN"
    end = f"IC_SSH_IMPORT_{nonce}_END"
    encoded = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")
    py_script = _REMOTE_PY.replace("__PAYLOAD_B64__", encoded)
    shell_script = (
        "set -eu\n"
        'PY="$(command -v python3 || command -v python || true)"\n'
        'if [ -z "$PY" ]; then echo "NO_REMOTE_PYTHON" >&2; exit 127; fi\n'
        f'printf "\\n%s\\n" \'{begin}\'\n'
        f'"$PY" - <<\'IC_SSH_IMPORT_PY\'\n{py_script}\nIC_SSH_IMPORT_PY\n'
        f'printf "%s\\n" \'{end}\'\n'
    )
    return shell_script, begin, end


def _run_remote(
    ssh_target: str,
    ssh_config: str | Path | None,
    payload: dict[str, Any],
    *,
    ssh_hostname: str | None = None,
    runner=None,
    timeout: int = SSH_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    argv = _build_ssh_argv(ssh_target, ssh_config, ssh_hostname=ssh_hostname)
    shell_script, begin, end = _build_remote_shell(payload)
    exec_fn = runner or subprocess.run
    try:
        completed = exec_fn(
            argv,
            input=shell_script.encode("utf-8"),
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise DomainError(f"SSH connection to {ssh_target} timed out after {timeout}s.") from exc
    except OSError as exc:
        raise DomainError(f"Failed to launch local SSH client: {exc}") from exc

    stdout = completed.stdout or b""
    stderr = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
    if len(stdout) > MAX_SSH_OUTPUT_BYTES:
        raise DomainError("Remote SSH response exceeded bounded size limit.", "too_large")

    lines = stdout.splitlines()
    b_marker = begin.encode("ascii")
    e_marker = end.encode("ascii")
    if completed.returncode != 0 or lines.count(b_marker) != 1 or lines.count(e_marker) != 1:
        detail = stderr.splitlines()[-1] if stderr else f"exit code {completed.returncode}"
        if "NO_REMOTE_PYTHON" in stderr:
            raise DomainError(f"Remote host {ssh_target} does not have python3/python available on PATH.")
        raise DomainError(f"SSH browse/import on {ssh_target} failed: {detail[:260]}")
    start_idx = lines.index(b_marker)
    end_idx = lines.index(e_marker)
    if start_idx >= end_idx:
        raise DomainError("Malformed framed response from remote SSH host.")
    body = b"\n".join(lines[start_idx + 1:end_idx]).decode("utf-8")
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise DomainError("Remote SSH host returned invalid JSON.") from exc
    if not isinstance(parsed, dict):
        raise DomainError("Remote SSH host returned unexpected payload.")
    if not parsed.get("ok"):
        raise DomainError(str(parsed.get("error") or "Remote SSH operation failed."))
    return parsed


def browse_remote_netlists(
    ssh_target: str,
    ssh_config: str | Path | None = None,
    path: str | None = None,
    *,
    ssh_hostname: str | None = None,
    runner=None,
) -> dict[str, Any]:
    """List directories, Cadence cds.lib libraries, and supported circuit files on an SSH server."""
    target = validate_ssh_target(ssh_target)
    clean_path = validate_remote_path(path, allow_empty=True, require_circuit_ext=False)
    payload = {
        "action": "browse",
        "path": clean_path,
        "max_entries": MAX_BROWSE_ENTRIES,
        "allowed_exts": sorted(ALLOWED_CIRCUIT_EXTS),
        "sensitive_exact": sorted(_SENSITIVE_EXACT),
        "sensitive_suffixes": list(_SENSITIVE_SUFFIXES),
        "sensitive_substrings": list(_SENSITIVE_SUBSTRINGS),
    }
    data = _run_remote(target, ssh_config, payload, ssh_hostname=ssh_hostname, runner=runner)
    entries = data.get("entries")
    if not isinstance(data.get("cwd"), str) or not isinstance(entries, list):
        raise DomainError("Incomplete remote directory listing.")
    raw_cds = data.get("cds_libraries")
    cds_libraries = []
    if isinstance(raw_cds, list):
        for item in raw_cds[:48]:
            if isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("path"), str):
                cds_libraries.append({"name": item["name"], "path": item["path"]})
    return {
        "schema": BROWSE_SCHEMA,
        "ssh_target": target,
        "cwd": data["cwd"],
        "parent": data.get("parent") if isinstance(data.get("parent"), str) else None,
        "entries": entries[:MAX_BROWSE_ENTRIES],
        "total": int(data.get("total", len(entries))),
        "truncated": bool(data.get("truncated", False)),
        "cds_libraries": cds_libraries,
        "netlist_count": int(data.get("netlist_count", sum(1 for e in entries if e.get("kind") == "file"))),
    }


def fetch_remote_netlist(
    ssh_target: str,
    ssh_config: str | Path | None = None,
    path: str | None = None,
    *,
    include_dependencies: bool = True,
    ssh_hostname: str | None = None,
    runner=None,
) -> dict[str, Any]:
    """Fetch a remote netlist file (and optional relative include dependencies) over SSH."""
    target = validate_ssh_target(ssh_target)
    clean_path = validate_remote_path(path, allow_empty=False, require_circuit_ext=True)
    if not isinstance(include_dependencies, bool):
        raise DomainError("include_dependencies must be an explicit boolean.")
    payload = {
        "action": "import",
        "path": clean_path,
        "include_dependencies": include_dependencies,
        "max_file_bytes": MAX_FILE_BYTES,
        "max_total_bytes": MAX_TOTAL_IMPORT_BYTES,
        "max_support_files": MAX_SUPPORT_FILES,
        "max_depth": MAX_INCLUDE_DEPTH,
        "allowed_exts": sorted(ALLOWED_CIRCUIT_EXTS),
        "sensitive_exact": sorted(_SENSITIVE_EXACT),
        "sensitive_suffixes": list(_SENSITIVE_SUFFIXES),
        "sensitive_substrings": list(_SENSITIVE_SUBSTRINGS),
    }
    data = _run_remote(target, ssh_config, payload, ssh_hostname=ssh_hostname, runner=runner)
    file_name = data.get("file_name")
    text = data.get("text")
    support = data.get("support_files")
    if not isinstance(file_name, str) or not isinstance(text, str) or not isinstance(support, dict):
        raise DomainError("Incomplete remote netlist payload.")
    if len(text.encode("utf-8")) > MAX_FILE_BYTES:
        raise DomainError("Remote netlist exceeds 1 MiB import limit.", "too_large")
    return {
        "schema": IMPORT_SCHEMA,
        "ssh_target": target,
        "remote_path": str(data.get("remote_path") or clean_path),
        "remote_dir": str(data.get("remote_dir") or posixpath.dirname(clean_path)),
        "file_name": file_name,
        "text": text,
        "support_files": {str(k): str(v) for k, v in support.items()},
        "skipped": list(data.get("skipped") or [])[:32],
    }


def _opt_text(value: object, name: str, max_len: int = 240) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > max_len or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise DomainError(f"Invalid {name} in CircuitGit entry payload.")
    return value.strip() or None


def validate_circuitgit_entry(payload: object) -> dict[str, Any]:
    """Validate a shared CircuitGit -> SpiceOwl active project/netlist handoff payload.

    When CircuitGit invokes or embeds SpiceOwl, SpiceOwl consumes this entry directly
    and suppresses its own standalone project/library selector.
    """
    if not isinstance(payload, dict):
        raise DomainError("CircuitGit entry payload must be a JSON object.")
    allowed = {
        "schema", "source", "project_id", "project_name", "library_id", "library_name",
        "cell_id", "cell_name", "view_id", "entry_path", "file_name", "netlist_text",
        "support_files", "simulator", "source_commit", "ssh_target", "remote_path",
        "remote_dir", "return_url",
    }
    extra = set(payload) - allowed
    if extra:
        raise DomainError("CircuitGit entry payload contains unsupported fields.")
    schema = payload.get("schema", CIRCUITGIT_ENTRY_SCHEMA)
    if schema != CIRCUITGIT_ENTRY_SCHEMA:
        raise DomainError(f"Unsupported CircuitGit entry schema: expected {CIRCUITGIT_ENTRY_SCHEMA}.")
    source = payload.get("source", "circuitgit")
    if source != "circuitgit":
        raise DomainError("CircuitGit entry source must be 'circuitgit'.")

    netlist_text = payload.get("netlist_text")
    if not isinstance(netlist_text, str) or "\0" in netlist_text or not netlist_text.strip():
        raise DomainError("CircuitGit entry requires non-empty UTF-8 netlist_text.")
    total_bytes = len(netlist_text.encode("utf-8"))
    if total_bytes > MAX_FILE_BYTES:
        raise DomainError("CircuitGit entry netlist_text exceeds 1 MiB limit.", "too_large")

    raw_support = payload.get("support_files") or {}
    if not isinstance(raw_support, dict) or len(raw_support) > MAX_SUPPORT_FILES:
        raise DomainError(f"support_files must be a mapping of at most {MAX_SUPPORT_FILES} relative files.")
    support_files: dict[str, str] = {}
    for rel, content in raw_support.items():
        if not isinstance(rel, str) or not _SUPPORT_PATH_RE.fullmatch(rel):
            raise DomainError("Support file path must be a safe relative POSIX path.")
        parts = rel.split("/")
        if any(part in {".", ".."} or is_sensitive_name(part) for part in parts):
            raise DomainError("Support file path cannot reference parent or sensitive locations.")
        if not isinstance(content, str) or "\0" in content:
            raise DomainError("Support file content must be UTF-8 text without NUL bytes.")
        cbytes = len(content.encode("utf-8"))
        if cbytes > MAX_FILE_BYTES or total_bytes + cbytes > MAX_TOTAL_IMPORT_BYTES:
            raise DomainError("CircuitGit entry support files exceed bundle size limit.", "too_large")
        total_bytes += cbytes
        support_files[rel] = content

    simulator = payload.get("simulator")
    if simulator is not None and simulator not in {"spectre", "ngspice"}:
        raise DomainError("simulator must be 'spectre' or 'ngspice'.")

    source_commit = _opt_text(payload.get("source_commit"), "source_commit", 64)
    if source_commit is not None and not _COMMIT_RE.fullmatch(source_commit):
        raise DomainError("source_commit must be a lowercase 40-character Git SHA.")

    ssh_target = _opt_text(payload.get("ssh_target"), "ssh_target", 240)
    if ssh_target is not None:
        ssh_target = validate_ssh_target(ssh_target)

    return_url = _opt_text(payload.get("return_url"), "return_url", 512)
    if return_url is not None and not (return_url.startswith("https://") or return_url.startswith("http://")):
        raise DomainError("return_url must be an http:// or https:// URL.")

    file_name = _opt_text(payload.get("file_name"), "file_name", 180)
    entry_path = _opt_text(payload.get("entry_path"), "entry_path", 512)
    if not file_name and entry_path:
        file_name = posixpath.basename(entry_path)

    return {
        "schema": CIRCUITGIT_ENTRY_SCHEMA,
        "source": "circuitgit",
        "project_id": _opt_text(payload.get("project_id"), "project_id", 180),
        "project_name": _opt_text(payload.get("project_name"), "project_name", 180),
        "library_id": _opt_text(payload.get("library_id"), "library_id", 180),
        "library_name": _opt_text(payload.get("library_name"), "library_name", 180),
        "cell_id": _opt_text(payload.get("cell_id"), "cell_id", 180),
        "cell_name": _opt_text(payload.get("cell_name"), "cell_name", 180),
        "view_id": _opt_text(payload.get("view_id"), "view_id", 80),
        "entry_path": entry_path,
        "file_name": file_name or "circuitgit_entry.scs",
        "netlist_text": netlist_text,
        "support_files": support_files,
        "simulator": simulator or "spectre",
        "source_commit": source_commit,
        "ssh_target": ssh_target,
        "remote_path": _opt_text(payload.get("remote_path"), "remote_path", 1024),
        "remote_dir": _opt_text(payload.get("remote_dir"), "remote_dir", 1024),
        "return_url": return_url,
    }

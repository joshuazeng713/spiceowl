"""Cross-platform zero-CLI SSH setup, key management, host-key trust, and diagnostics.

Designed for both SpiceOwl's own local Connector and external hosts (such as
CircuitGit via ``submodules/spiceowl``). All managed host entries live inside a
dedicated include file (``~/.ssh/spiceowl_hosts.conf``) referenced from the top
of ``~/.ssh/config`` so existing user SSH configurations are never overwritten.

Security boundaries:
- Private keys are never read or returned; only public keys and SHA256
  fingerprints are exposed.
- One-time passwords for public-key deployment live only in a short-lived
  ``0700`` temporary directory (``SSH_ASKPASS`` helper) and are deleted in a
  ``finally`` block; passwords are never logged, persisted, or returned.
- All host, alias, user, port, ProxyJump, identity-file, public-key, and
  ``known_hosts`` inputs are strictly validated against option injection,
  control characters, wildcards, and path traversal.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time

from spiceowl._platform import (
    BoundedIOError,
    IS_WINDOWS,
    chmod_private,
    communicate_bounded,
    fsync_dir,
    open_readonly_nofollow,
    popen_isolation_kwargs,
    resolve_ssh_executable,
    ssh_client_options,
    terminate_process_tree,
)
from spiceowl.domain.common import DomainError
from spiceowl.onboarding.ssh_discovery import discover_ssh_hosts, validate_alias

MANAGED_FILENAME = "spiceowl_hosts.conf"
MANAGED_KEY_NAME = "id_ed25519_spiceowl"
MANAGED_RSA_KEY_NAME = "id_rsa_spiceowl"
INCLUDE_LINE = f"Include {MANAGED_FILENAME}"

_BLOCK_START_RE = re.compile(r"^#\s*>>>\s*spiceowl-host:\s*([A-Za-z0-9][A-Za-z0-9_.-]{0,95})\s*>>>\s*$")
_BLOCK_END_RE = re.compile(r"^#\s*<<<\s*spiceowl-host:\s*([A-Za-z0-9][A-Za-z0-9_.-]{0,95})\s*<<<\s*$")
_DNS_LABEL_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", re.ASCII)
_USERNAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", re.ASCII)
_KEY_FILENAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", re.ASCII)
_KNOWN_HOST_PATTERN_RE = re.compile(
    r"^(?:\|1\|[A-Za-z0-9+/=]{16,128}\|[A-Za-z0-9+/=]{16,128}|\[[A-Za-z0-9._-]{1,253}\]:[0-9]{1,5}|[A-Za-z0-9._-]{1,253})$",
    re.ASCII,
)
_SUPPORTED_KEY_TYPES = frozenset({
    "ssh-ed25519",
    "ssh-rsa",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com",
    "sk-ecdsa-sha2-nistp256@openssh.com",
})

MAX_CONFIG_BYTES = 256 * 1024
MAX_KNOWN_HOSTS_BYTES = 1024 * 1024
MAX_PUBLIC_KEY_BYTES = 8192
MAX_LOCAL_KEYS = 64
MAX_PROXY_HOPS = 8
MAX_SCAN_ENTRIES = 32
DEFAULT_TCP_TIMEOUT = 5.0
DEFAULT_SSH_TIMEOUT = 15.0
DEFAULT_KEYSCAN_TIMEOUT = 12.0


# ---------------------------------------------------------------------------
# Strict input validation
# ---------------------------------------------------------------------------

def _reject_control_or_space(value: str, label: str, code: str) -> None:
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise DomainError(f"{label} must not contain whitespace or control characters.", code)


def validate_hostname(hostname: str) -> str:
    """Validate a DNS hostname or IPv4 address without flags, spaces, or wildcards."""
    if not isinstance(hostname, str) or not 1 <= len(hostname) <= 253:
        raise DomainError("Provide a valid hostname or IPv4 address (1-253 chars).", "ssh_hostname_invalid")
    _reject_control_or_space(hostname, "Hostname", "ssh_hostname_invalid")
    if hostname.startswith("-") or ".." in hostname or hostname.endswith(".") or ":" in hostname:
        raise DomainError("Hostname must be a DNS name or IPv4 address without leading '-', ':', or '..'.", "ssh_hostname_invalid")
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+){3}", hostname):
        try:
            ipaddress.IPv4Address(hostname)
        except ValueError:
            raise DomainError("Invalid IPv4 address.", "ssh_hostname_invalid") from None
        return hostname
    parts = hostname.split(".")
    if any(_DNS_LABEL_RE.fullmatch(part) is None for part in parts):
        raise DomainError("Hostname contains unsupported characters or wildcard syntax.", "ssh_hostname_invalid")
    return hostname


def validate_port(port: int | str | None = 22) -> int:
    """Validate a TCP port in 1..65535."""
    if port is None or port == "":
        return 22
    if isinstance(port, bool):
        raise DomainError("Port must be an integer between 1 and 65535.", "ssh_port_invalid")
    if isinstance(port, str):
        stripped = port.strip()
        if not stripped.isdigit():
            raise DomainError("Port must be an integer between 1 and 65535.", "ssh_port_invalid")
        value = int(stripped)
    elif isinstance(port, int):
        value = port
    else:
        raise DomainError("Port must be an integer between 1 and 65535.", "ssh_port_invalid")
    if not 1 <= value <= 65535:
        raise DomainError("Port must be between 1 and 65535.", "ssh_port_invalid")
    return value


def validate_username(username: str) -> str:
    """Validate a POSIX login username without flags or whitespace."""
    if not isinstance(username, str) or not _USERNAME_RE.fullmatch(username) or ".." in username:
        raise DomainError("Username must start with a letter or '_' and use only safe ASCII characters.", "ssh_user_invalid")
    return username


def _parse_single_jump_hop(hop: str) -> tuple[str | None, str, int | None]:
    """Validate one ProxyJump hop: alias, host, user@host, host:port, or user@host:port."""
    if not isinstance(hop, str) or not 1 <= len(hop) <= 320:
        raise DomainError("Each ProxyJump hop must be a non-empty alias or [user@]host[:port].", "ssh_proxy_jump_invalid")
    _reject_control_or_space(hop, "ProxyJump hop", "ssh_proxy_jump_invalid")
    if hop.startswith("-") or any(ch in hop for ch in "*?![]\\`'\"$;|&<>"):
        raise DomainError("ProxyJump hop contains disallowed options or shell characters.", "ssh_proxy_jump_invalid")
    user = None
    rest = hop
    if "@" in rest:
        if rest.count("@") != 1:
            raise DomainError("ProxyJump hop may contain at most one '@'.", "ssh_proxy_jump_invalid")
        user_part, rest = rest.split("@", 1)
        user = validate_username(user_part)
    port = None
    if ":" in rest:
        if rest.count(":") != 1:
            raise DomainError("ProxyJump hop may contain at most one ':port'.", "ssh_proxy_jump_invalid")
        host_part, port_part = rest.split(":", 1)
        port = validate_port(port_part)
    else:
        host_part = rest
    # Allow either a valid alias or a valid hostname/IPv4
    if user is None and port is None:
        try:
            validate_alias(host_part)
        except DomainError:
            validate_hostname(host_part)
    else:
        validate_hostname(host_part)
    return user, host_part, port


def validate_proxy_jump(proxy_jump: str | None, *, self_alias: str | None = None) -> str:
    """Validate an empty string or comma-separated ProxyJump chain (single or multi-hop)."""
    if proxy_jump is None:
        return ""
    if not isinstance(proxy_jump, str):
        raise DomainError("ProxyJump must be a string.", "ssh_proxy_jump_invalid")
    cleaned = proxy_jump.strip()
    if not cleaned:
        return ""
    if cleaned.lower() == "none":
        return ""
    raw_hops = [part.strip() for part in cleaned.split(",")]
    if any(not h for h in raw_hops):
        raise DomainError("ProxyJump must not contain empty hops.", "ssh_proxy_jump_invalid")
    if len(raw_hops) > MAX_PROXY_HOPS:
        raise DomainError(f"ProxyJump supports at most {MAX_PROXY_HOPS} hops.", "ssh_proxy_jump_invalid")
    normalized: list[str] = []
    for hop in raw_hops:
        user, host_part, port = _parse_single_jump_hop(hop)
        if self_alias and user is None and port is None and host_part == self_alias:
            raise DomainError("A host cannot set itself as its own ProxyJump.", "ssh_proxy_jump_invalid")
        rebuilt = (f"{user}@" if user else "") + host_part + (f":{port}" if port is not None else "")
        normalized.append(rebuilt)
    return ",".join(normalized)


def validate_key_name(key_name: str) -> str:
    if not isinstance(key_name, str) or not _KEY_FILENAME_RE.fullmatch(key_name) or ".." in key_name or key_name.endswith(".pub"):
        raise DomainError("Key name must be a simple filename inside ~/.ssh without '..' or '.pub'.", "ssh_key_invalid")
    return key_name


def validate_identity_file(identity_file: str | None, ssh_dir: Path | None = None) -> str:
    """Validate an optional IdentityFile setting (canonical ``~/.ssh/<name>`` or private file inside ``ssh_dir``)."""
    if identity_file is None:
        return ""
    if not isinstance(identity_file, str):
        raise DomainError("IdentityFile must be a string.", "ssh_identity_invalid")
    cleaned = identity_file.strip()
    if not cleaned:
        return ""
    _reject_control_or_space(cleaned, "IdentityFile", "ssh_identity_invalid")
    if cleaned.startswith("-") or ".." in cleaned or any(ch in cleaned for ch in "*?[]`'\"$;|&<>"):
        raise DomainError("IdentityFile contains disallowed characters or path traversal.", "ssh_identity_invalid")
    if cleaned.endswith(".pub"):
        raise DomainError("IdentityFile must reference the private key path, not the .pub file.", "ssh_identity_invalid")
    if cleaned.startswith("~/.ssh/"):
        leaf = cleaned[len("~/.ssh/"):]
        validate_key_name(leaf)
        return f"~/.ssh/{leaf}"
    if "/" not in cleaned and "\\" not in cleaned:
        validate_key_name(cleaned)
        return f"~/.ssh/{cleaned}"
    candidate = Path(cleaned).expanduser()
    if not candidate.is_absolute():
        raise DomainError("IdentityFile must be '~/.ssh/<key>' or an absolute path inside the SSH directory.", "ssh_identity_invalid")
    if ssh_dir is not None:
        try:
            resolved_dir = Path(ssh_dir).expanduser().resolve()
            resolved_cand = candidate.resolve(strict=False)
            resolved_cand.relative_to(resolved_dir)
            validate_key_name(resolved_cand.name)
            return f"~/.ssh/{resolved_cand.name}" if resolved_dir == (Path.home() / ".ssh").resolve() else str(resolved_cand)
        except ValueError:
            raise DomainError("IdentityFile must stay within the configured SSH directory.", "ssh_identity_invalid") from None
    validate_key_name(candidate.name)
    return str(candidate)


def validate_public_key(public_key: str) -> str:
    """Validate a single-line OpenSSH public key and return its normalized form."""
    if not isinstance(public_key, str):
        raise DomainError("Public key must be a single-line OpenSSH string.", "ssh_pubkey_invalid")
    line = public_key.strip()
    if not line or len(line) > MAX_PUBLIC_KEY_BYTES or "\n" in line or "\r" in line or "\x00" in line:
        raise DomainError("Public key must be a bounded single-line OpenSSH key.", "ssh_pubkey_invalid")
    parts = line.split(None, 2)
    if len(parts) < 2:
        raise DomainError("Public key must include key type and base64 key material.", "ssh_pubkey_invalid")
    key_type, key_blob = parts[0], parts[1]
    comment = parts[2] if len(parts) == 3 else ""
    if key_type not in _SUPPORTED_KEY_TYPES:
        raise DomainError(f"Unsupported OpenSSH public key type: {key_type}", "ssh_pubkey_invalid")
    try:
        raw = base64.b64decode(key_blob.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError):
        raise DomainError("Public key material is not valid base64.", "ssh_pubkey_invalid") from None
    if len(raw) < 16 or len(raw) > 8192:
        raise DomainError("Public key payload size is outside supported bounds.", "ssh_pubkey_invalid")
    if comment and (not comment.isprintable() or any(ch in comment for ch in "\r\n\x00")):
        raise DomainError("Public key comment contains unsupported characters.", "ssh_pubkey_invalid")
    return f"{key_type} {key_blob}" + (f" {comment}" if comment else "")


def public_key_fingerprint(public_key_or_blob: str) -> str:
    """Compute OpenSSH ``SHA256:<base64>`` fingerprint purely in Python."""
    text = public_key_or_blob.strip()
    parts = text.split()
    blob = parts[1] if len(parts) >= 2 and parts[0] in _SUPPORTED_KEY_TYPES else parts[0]
    raw = base64.b64decode(blob.encode("ascii"), validate=True)
    digest = hashlib.sha256(raw).digest()
    encoded = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"SHA256:{encoded}"


def validate_known_host_entry(entry: str) -> tuple[str, str, str, str]:
    """Validate one OpenSSH ``known_hosts`` line; return ``(host_pattern, key_type, blob, normalized_line)``."""
    if not isinstance(entry, str):
        raise DomainError("known_hosts entry must be a string.", "ssh_known_host_invalid")
    line = entry.strip()
    if not line or line.startswith("#") or len(line) > MAX_PUBLIC_KEY_BYTES or any(ch in line for ch in "\r\n\x00"):
        raise DomainError("known_hosts entry must be a single non-comment line.", "ssh_known_host_invalid")
    parts = line.split()
    if len(parts) < 3:
        raise DomainError("known_hosts entry must have '<host> <key_type> <base64_key>'.", "ssh_known_host_invalid")
    host_pat, key_type, key_blob = parts[0], parts[1], parts[2]
    # Support comma-separated host patterns (e.g. hostname,ip) if every segment is safe
    host_segments = host_pat.split(",")
    if not host_segments or len(host_segments) > 4 or any(not _KNOWN_HOST_PATTERN_RE.fullmatch(seg) for seg in host_segments):
        raise DomainError("known_hosts host pattern is invalid or unsafe.", "ssh_known_host_invalid")
    if key_type not in _SUPPORTED_KEY_TYPES:
        raise DomainError(f"Unsupported known_hosts key type: {key_type}", "ssh_known_host_invalid")
    try:
        raw = base64.b64decode(key_blob.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError):
        raise DomainError("known_hosts key blob is not valid base64.", "ssh_known_host_invalid") from None
    if len(raw) < 16 or len(raw) > 8192:
        raise DomainError("known_hosts key size is outside supported bounds.", "ssh_known_host_invalid")
    return host_pat, key_type, key_blob, f"{host_pat} {key_type} {key_blob}"


# ---------------------------------------------------------------------------
# Safe filesystem helpers
# ---------------------------------------------------------------------------

def _ssh_dir_path(ssh_dir: str | Path | None) -> Path:
    base = Path(ssh_dir).expanduser() if ssh_dir is not None else (Path.home() / ".ssh")
    if base.exists() and base.is_symlink():
        raise DomainError("Refusing to manage a symlinked SSH directory.", "ssh_dir_invalid")
    return base.resolve(strict=False)


def _ensure_ssh_dir(ssh_dir: str | Path | None) -> Path:
    path = _ssh_dir_path(ssh_dir)
    if path.exists():
        if path.is_symlink() or not path.is_dir():
            raise DomainError("SSH directory path is not a regular directory.", "ssh_dir_invalid")
    else:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        chmod_private(path, 0o700)
    return path


def _read_text_bounded(path: Path, max_bytes: int) -> str:
    if not path.exists():
        return ""
    if path.is_symlink():
        raise DomainError(f"Refusing to follow symlink: {path.name}", "ssh_file_symlink")
    fd = open_readonly_nofollow(path)
    with os.fdopen(fd, "rb") as stream:
        raw = stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise DomainError(f"{path.name} exceeds maximum supported size ({max_bytes} bytes).", "ssh_file_too_large")
    return raw.decode("utf-8", errors="replace")


def _atomic_write_private(path: Path, content: str, mode: int = 0o600) -> None:
    if path.exists() and path.is_symlink():
        raise DomainError(f"Refusing to overwrite symlink: {path.name}", "ssh_file_symlink")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        chmod_private(tmp_path, mode)
        os.replace(tmp_path, path)
        chmod_private(path, mode)
        fsync_dir(path.parent)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _resolve_openssh_tool(name: str) -> str:
    """Resolve an OpenSSH companion executable (ssh-keygen, ssh-keyscan) beside ssh."""
    ssh_exe = Path(resolve_ssh_executable())
    tool_name = f"{name}.exe" if IS_WINDOWS and not name.lower().endswith(".exe") else name
    sibling = ssh_exe.with_name(tool_name)
    if sibling.is_file():
        return str(sibling)
    found = shutil.which(name) or shutil.which(tool_name)
    if found:
        return found
    if not IS_WINDOWS and Path(f"/usr/bin/{name}").is_file():
        return f"/usr/bin/{name}"
    return tool_name


def _run_bounded(argv: list[str], *, stdin_bytes: bytes | None = None,
                 env: dict[str, str] | None = None, timeout: float = DEFAULT_SSH_TIMEOUT,
                 max_output_bytes: int = 128 * 1024) -> tuple[int, bytes, bytes]:
    kwargs = popen_isolation_kwargs()
    if IS_WINDOWS:
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if no_window:
            kwargs["creationflags"] = int(kwargs.get("creationflags", 0)) | int(no_window)
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            **kwargs,
        )
    except OSError as exc:
        raise DomainError(f"Could not launch OpenSSH tool '{argv[0]}': {exc.strerror or exc}", "ssh_tool_missing") from None
    out_chunks: list[bytes] = []
    err_chunks: list[bytes] = []
    try:
        rc = communicate_bounded(
            proc,
            deadline=time.monotonic() + timeout,
            max_output_bytes=max_output_bytes,
            stdin_bytes=stdin_bytes,
            on_stdout=out_chunks.append,
            on_stderr=err_chunks.append,
        )
    except BoundedIOError as exc:
        terminate_process_tree(proc, force=True)
        raise DomainError(f"OpenSSH command timed out or exceeded output limit ({exc.code}).", f"ssh_{exc.code}") from None
    except BaseException:
        terminate_process_tree(proc, force=True)
        raise
    return rc, b"".join(out_chunks), b"".join(err_chunks)


# ---------------------------------------------------------------------------
# Managed ~/.ssh/spiceowl_hosts.conf and top-level ~/.ssh/config Include
# ---------------------------------------------------------------------------

def _ensure_config_include(ssh_dir: Path) -> Path:
    """Ensure ``Include spiceowl_hosts.conf`` appears before any Host/Match block in ``~/.ssh/config``."""
    cfg_path = ssh_dir / "config"
    existing = _read_text_bounded(cfg_path, MAX_CONFIG_BYTES) if cfg_path.exists() else ""

    # Check if an active top-level Include for spiceowl_hosts.conf already exists
    for raw_line in existing.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        tokens = stripped.split(None, 1)
        if not tokens:
            continue
        key = tokens[0].lower()
        if key in ("host", "match"):
            break
        if key == "include" and len(tokens) > 1:
            targets = tokens[1].split()
            if any(t in (MANAGED_FILENAME, f"~/.ssh/{MANAGED_FILENAME}", f"./{MANAGED_FILENAME}") for t in targets):
                return cfg_path

    header = f"# Managed by SpiceOwl (preserves all your existing hosts below)\n{INCLUDE_LINE}\n"
    new_content = header + ("\n" + existing if existing else "")
    if not new_content.endswith("\n"):
        new_content += "\n"
    _atomic_write_private(cfg_path, new_content, mode=0o600)
    return cfg_path


def _render_host_block(entry: dict) -> str:
    lines = [
        f"# >>> spiceowl-host: {entry['alias']} >>>",
        f"Host {entry['alias']}",
        f"  HostName {entry['hostname']}",
    ]
    if int(entry.get("port", 22)) != 22:
        lines.append(f"  Port {int(entry['port'])}")
    lines.append(f"  User {entry['user']}")
    if entry.get("proxy_jump"):
        lines.append(f"  ProxyJump {entry['proxy_jump']}")
    if entry.get("identity_file"):
        lines.append(f"  IdentityFile {entry['identity_file']}")
        lines.append("  IdentitiesOnly yes")
    if entry.get("legacy_rsa"):
        lines.append("  HostKeyAlgorithms +ssh-rsa")
        lines.append("  PubkeyAcceptedAlgorithms +ssh-rsa")
    if entry.get("legacy_kex"):
        lines.append("  KexAlgorithms +diffie-hellman-group14-sha1,diffie-hellman-group-exchange-sha1")
    lines.append("  ServerAliveInterval 30")
    lines.append(f"# <<< spiceowl-host: {entry['alias']} <<<")
    return "\n".join(lines)


def _parse_managed_file(text: str) -> tuple[list[dict], list[str]]:
    """Parse managed host blocks and preserve any unrecognised non-managed preamble lines."""
    hosts: list[dict] = []
    other_lines: list[str] = []
    in_alias: str | None = None
    cur: dict | None = None
    for line in text.splitlines():
        m_start = _BLOCK_START_RE.match(line.strip())
        if m_start and in_alias is None:
            in_alias = m_start.group(1)
            cur = {
                "alias": in_alias,
                "hostname": "",
                "port": 22,
                "user": "",
                "proxy_jump": "",
                "identity_file": "",
                "legacy_rsa": False,
                "legacy_kex": False,
                "managed": True,
            }
            continue
        m_end = _BLOCK_END_RE.match(line.strip())
        if m_end and in_alias is not None and m_end.group(1) == in_alias:
            if cur is not None and cur["alias"]:
                hosts.append(cur)
            in_alias = None
            cur = None
            continue
        if in_alias is not None and cur is not None:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split(None, 1)
            if len(parts) < 2:
                continue
            key, val = parts[0].lower(), parts[1].strip()
            if key == "hostname":
                cur["hostname"] = val
            elif key == "port" and val.isdigit():
                cur["port"] = int(val)
            elif key == "user":
                cur["user"] = val
            elif key == "proxyjump":
                cur["proxy_jump"] = val
            elif key == "identityfile":
                cur["identity_file"] = val
            elif key in ("hostkeyalgorithms", "pubkeyacceptedalgorithms", "pubkeyacceptedkeytypes") and "ssh-rsa" in val:
                cur["legacy_rsa"] = True
            elif key == "kexalgorithms" and "sha1" in val:
                cur["legacy_kex"] = True
        else:
            other_lines.append(line)
    return hosts, other_lines


def _write_managed_file(ssh_dir: Path, hosts: list[dict], extra_lines: list[str] | None = None) -> Path:
    managed_path = ssh_dir / MANAGED_FILENAME
    preamble = [
        "# SpiceOwl managed SSH hosts — edited via SpiceOwl / CircuitGit SSH Setup Wizard.",
        "# Your original ~/.ssh/config remains untouched.",
    ]
    preserved = [
        ln for ln in (extra_lines or [])
        if ln.strip() and not ln.strip().startswith("# SpiceOwl managed SSH hosts")
        and not ln.strip().startswith("# Your original ~/.ssh/config remains untouched")
    ]
    chunks = ["\n".join(preamble)]
    if preserved:
        chunks.append("\n".join(preserved))
    for host in hosts:
        chunks.append(_render_host_block(host))
    body = "\n\n".join(chunks) + "\n"
    _atomic_write_private(managed_path, body, mode=0o600)
    return managed_path


def list_managed_hosts(ssh_dir: str | Path | None = None) -> list[dict]:
    """List all SSH host entries managed inside ``~/.ssh/spiceowl_hosts.conf``."""
    base = _ssh_dir_path(ssh_dir)
    managed_path = base / MANAGED_FILENAME
    if not managed_path.exists():
        return []
    text = _read_text_bounded(managed_path, MAX_CONFIG_BYTES)
    hosts, _ = _parse_managed_file(text)
    return hosts


def list_all_ssh_aliases(ssh_dir: str | Path | None = None) -> dict:
    """Return managed host records alongside all literal SSH aliases discovered from ``~/.ssh/config``."""
    base = _ssh_dir_path(ssh_dir)
    managed = list_managed_hosts(base)
    managed_names = {h["alias"] for h in managed}
    all_aliases: set[str] = set(managed_names)
    cfg = base / "config"
    if cfg.is_file() and not cfg.is_symlink():
        try:
            disc = discover_ssh_hosts(cfg, user_home=base.parent)
            for row in disc.get("hosts", []):
                alias = row.get("alias")
                if isinstance(alias, str) and alias:
                    all_aliases.add(alias)
        except DomainError:
            pass
    return {
        "managed": managed,
        "existing_aliases": sorted(all_aliases),
        "unmanaged_aliases": sorted(all_aliases - managed_names),
        "managed_file": str(base / MANAGED_FILENAME),
    }


def upsert_managed_host(ssh_dir: str | Path | None, entry: dict) -> dict:
    """Create or update a managed host entry in ``spiceowl_hosts.conf`` and ensure ``Include`` in ``config``."""
    if not isinstance(entry, dict):
        raise DomainError("Host entry must be a JSON object.", "ssh_entry_invalid")
    base = _ensure_ssh_dir(ssh_dir)
    alias = validate_alias(entry.get("alias", ""))
    hostname = validate_hostname(entry.get("hostname", ""))
    port = validate_port(entry.get("port", 22))
    user = validate_username(entry.get("user", ""))
    proxy_jump = validate_proxy_jump(entry.get("proxy_jump", ""), self_alias=alias)
    raw_id = entry.get("identity_file")
    if raw_id is None:
        # Default to managed key if it already exists
        default_priv = base / MANAGED_KEY_NAME
        identity_file = f"~/.ssh/{MANAGED_KEY_NAME}" if default_priv.is_file() else ""
    else:
        identity_file = validate_identity_file(raw_id, ssh_dir=base)

    legacy_rsa = bool(entry.get("legacy_rsa", False))
    legacy_kex = bool(entry.get("legacy_kex", False))

    record = {
        "alias": alias,
        "hostname": hostname,
        "port": port,
        "user": user,
        "proxy_jump": proxy_jump,
        "identity_file": identity_file,
        "legacy_rsa": legacy_rsa,
        "legacy_kex": legacy_kex,
        "managed": True,
    }
    managed_path = base / MANAGED_FILENAME
    existing_text = _read_text_bounded(managed_path, MAX_CONFIG_BYTES) if managed_path.exists() else ""
    hosts, extra_lines = _parse_managed_file(existing_text)
    created = True
    updated: list[dict] = []
    for item in hosts:
        if item["alias"] == alias:
            updated.append(record)
            created = False
        else:
            updated.append(item)
    if created:
        updated.append(record)
    _write_managed_file(base, updated, extra_lines)
    cfg_path = _ensure_config_include(base)
    return {
        "host": record,
        "created": created,
        "managed_file": str(managed_path),
        "config_file": str(cfg_path),
    }


def delete_managed_host(ssh_dir: str | Path | None, alias: str) -> dict:
    """Delete a managed host entry from ``spiceowl_hosts.conf`` without modifying user hosts in ``config``."""
    clean_alias = validate_alias(alias)
    base = _ssh_dir_path(ssh_dir)
    managed_path = base / MANAGED_FILENAME
    if not managed_path.exists():
        return {"alias": clean_alias, "deleted": False, "managed_file": str(managed_path)}
    existing_text = _read_text_bounded(managed_path, MAX_CONFIG_BYTES)
    hosts, extra_lines = _parse_managed_file(existing_text)
    remaining = [h for h in hosts if h["alias"] != clean_alias]
    deleted = len(remaining) != len(hosts)
    if deleted:
        _write_managed_file(base, remaining, extra_lines)
    return {"alias": clean_alias, "deleted": deleted, "managed_file": str(managed_path)}


# ---------------------------------------------------------------------------
# SSH key management (never reads private key contents)
# ---------------------------------------------------------------------------

def ensure_managed_key(
    ssh_dir: str | Path | None = None,
    *,
    key_name: str | None = None,
    key_type: str = "ed25519",
) -> dict:
    """Ensure a SpiceOwl managed key (Ed25519 or RSA-4096 for legacy OpenSSH) exists."""
    if key_type not in ("ed25519", "rsa"):
        raise DomainError("key_type must be 'ed25519' or 'rsa'.", "ssh_key_invalid")
    effective_name = key_name or (MANAGED_RSA_KEY_NAME if key_type == "rsa" else MANAGED_KEY_NAME)
    clean_name = validate_key_name(effective_name)
    base = _ensure_ssh_dir(ssh_dir)
    priv_path = base / clean_name
    pub_path = base / f"{clean_name}.pub"
    if priv_path.is_symlink() or pub_path.is_symlink():
        raise DomainError("Refusing to operate on a symlinked SSH key file.", "ssh_key_symlink")
    created = False
    if not priv_path.is_file() or not pub_path.is_file():
        if priv_path.is_file() and not pub_path.is_file():
            # Derive .pub from existing private key via ssh-keygen -y without reading private key in Python
            keygen = _resolve_openssh_tool("ssh-keygen")
            rc, out, err = _run_bounded([keygen, "-y", "-f", str(priv_path)], timeout=10.0)
            if rc != 0:
                raise DomainError(
                    f"Existing private key '{clean_name}' has no .pub file and could not export its public key.",
                    "ssh_keygen_failed",
                )
            pub_line = validate_public_key(out.decode("utf-8", errors="replace").strip())
            _atomic_write_private(pub_path, pub_line + "\n", mode=0o644)
        else:
            keygen = _resolve_openssh_tool("ssh-keygen")
            kg_argv = [keygen, "-t", key_type]
            if key_type == "rsa":
                kg_argv.extend(["-b", "4096"])
            kg_argv.extend(["-N", "", "-C", "spiceowl-local", "-f", str(priv_path)])
            rc, _out, err = _run_bounded(kg_argv, timeout=15.0)
            if rc != 0 or not pub_path.is_file():
                msg = err.decode("utf-8", errors="replace").strip()[:200]
                raise DomainError(f"ssh-keygen failed to generate '{clean_name}': {msg or 'unknown error'}", "ssh_keygen_failed")
            chmod_private(priv_path, 0o600)
            created = True

    pub_text = _read_text_bounded(pub_path, MAX_PUBLIC_KEY_BYTES).strip()
    normalized_pub = validate_public_key(pub_text)
    fp = public_key_fingerprint(normalized_pub)
    home_ssh = (Path.home() / ".ssh").resolve(strict=False)
    config_path = f"~/.ssh/{clean_name}" if base == home_ssh else str(priv_path)
    return {
        "name": clean_name,
        "key_type": normalized_pub.split(None, 1)[0],
        "private_path": str(priv_path),
        "config_path": config_path,
        "public_key": normalized_pub,
        "fingerprint": fp,
        "created": created,
    }


def list_local_keys(ssh_dir: str | Path | None = None) -> list[dict]:
    """List local ``*.pub`` public key metadata & SHA256 fingerprints (never reads private keys)."""
    base = _ssh_dir_path(ssh_dir)
    if not base.is_dir():
        return []
    home_ssh = (Path.home() / ".ssh").resolve(strict=False)
    results: list[dict] = []
    try:
        entries = sorted(base.iterdir(), key=lambda p: p.name)
    except OSError:
        return []
    for entry in entries:
        if len(results) >= MAX_LOCAL_KEYS:
            break
        if not entry.name.endswith(".pub") or entry.is_symlink() or not entry.is_file():
            continue
        key_name = entry.name[:-4]
        try:
            validate_key_name(key_name)
            pub_line = validate_public_key(_read_text_bounded(entry, MAX_PUBLIC_KEY_BYTES))
            fp = public_key_fingerprint(pub_line)
        except DomainError:
            continue
        priv = base / key_name
        has_private = priv.is_file() and not priv.is_symlink()
        results.append({
            "name": key_name,
            "private_path": str(priv),
            "config_path": f"~/.ssh/{key_name}" if base == home_ssh else str(priv),
            "public_path": str(entry),
            "key_type": pub_line.split(None, 1)[0],
            "public_key": pub_line,
            "fingerprint": fp,
            "has_private": has_private,
            "managed": key_name in (MANAGED_KEY_NAME, MANAGED_RSA_KEY_NAME),
        })
    results.sort(key=lambda row: (not row["managed"], row["name"]))
    return results


_PROTECTED_SSH_FILES = frozenset({
    "config",
    "known_hosts",
    "known_hosts.old",
    "authorized_keys",
    "authorized_keys2",
    MANAGED_FILENAME,
})


def delete_local_key(ssh_dir: str | Path | None, key_name: str) -> dict:
    """Safely delete one local SSH keypair (``<name>`` and ``<name>.pub``) inside ``ssh_dir``.

    Verifies that ``<name>.pub`` exists and parses as a valid OpenSSH public key
    before unlinking anything, refuses symlinks and protected SSH files, and
    clears dangling ``IdentityFile`` references from managed hosts.
    """
    clean_name = validate_key_name(key_name)
    if clean_name.lower() in _PROTECTED_SSH_FILES:
        raise DomainError(f"Refusing to delete protected SSH file '{clean_name}'.", "ssh_key_protected")
    base = _ssh_dir_path(ssh_dir)
    pub_path = base / f"{clean_name}.pub"
    priv_path = base / clean_name
    if pub_path.is_symlink() or priv_path.is_symlink():
        raise DomainError("Refusing to delete a symlinked SSH key file.", "ssh_key_symlink")
    if not pub_path.is_file():
        return {"name": clean_name, "deleted": False, "cleared_hosts": []}
    # Verify .pub is a genuine OpenSSH public key before removing the keypair
    validate_public_key(_read_text_bounded(pub_path, MAX_PUBLIC_KEY_BYTES))

    if priv_path.is_file():
        priv_path.unlink()
    pub_path.unlink()

    # Clear any dangling IdentityFile reference in managed spiceowl_hosts.conf
    cleared_hosts: list[str] = []
    managed_path = base / MANAGED_FILENAME
    if managed_path.is_file() and not managed_path.is_symlink():
        text = _read_text_bounded(managed_path, MAX_CONFIG_BYTES)
        hosts, extra_lines = _parse_managed_file(text)
        changed = False
        match_paths = {f"~/.ssh/{clean_name}", str(priv_path)}
        for host in hosts:
            if host.get("identity_file") in match_paths:
                host["identity_file"] = ""
                cleared_hosts.append(host["alias"])
                changed = True
        if changed:
            _write_managed_file(base, hosts, extra_lines)

    return {
        "name": clean_name,
        "deleted": True,
        "managed": clean_name in (MANAGED_KEY_NAME, MANAGED_RSA_KEY_NAME),
        "cleared_hosts": cleared_hosts,
    }


# ---------------------------------------------------------------------------
# Host key scanning & known_hosts trust
# ---------------------------------------------------------------------------

def _normalize_scanned_entries(raw_text: str, hostname: str, port: int) -> list[dict]:
    entries: list[dict] = []
    seen: set[tuple[str, str]] = set()
    expected_host_tok = f"[{hostname}]:{port}" if port != 22 else hostname
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            host_pat, key_type, key_blob, normalized = validate_known_host_entry(line)
        except DomainError:
            continue
        # Ensure non-default port entries use canonical [host]:port notation
        if port != 22 and host_pat == hostname:
            host_pat = expected_host_tok
            normalized = f"{host_pat} {key_type} {key_blob}"
        dedupe = (key_type, key_blob)
        if dedupe in seen:
            continue
        seen.add(dedupe)
        entries.append({
            "host_pattern": host_pat,
            "key_type": key_type,
            "fingerprint": public_key_fingerprint(f"{key_type} {key_blob}"),
            "entry": normalized,
        })
        if len(entries) >= MAX_SCAN_ENTRIES:
            break
    return entries


def _base_batch_ssh_argv(ssh_config: Path | None) -> list[str]:
    argv = [resolve_ssh_executable()]
    if ssh_config is not None and ssh_config.is_file():
        argv.extend(["-F", str(ssh_config)])
    base_opts = ssh_client_options((
        "BatchMode=yes",
        "StrictHostKeyChecking=yes",
        "ForwardAgent=no",
        "PermitLocalCommand=no",
        "ControlMaster=no",
        "ControlPath=none",
        "ControlPersist=no",
        "ClearAllForwardings=yes",
        "ForwardX11=no",
        "RequestTTY=no",
        "ConnectionAttempts=1",
        "ConnectTimeout=8",
        "NumberOfPasswordPrompts=0",
    ))
    for opt in base_opts:
        argv.extend(["-o", opt])
    return argv


def scan_host_key(
    hostname: str,
    port: int | str = 22,
    *,
    jump_alias: str = "",
    ssh_config: str | Path | None = None,
) -> dict:
    """Scan host public keys via ``ssh-keyscan`` (directly or across single/multi-hop ``jump_alias``)."""
    clean_host = validate_hostname(hostname)
    clean_port = validate_port(port)
    clean_jump = validate_proxy_jump(jump_alias)
    cfg_path = Path(ssh_config).expanduser() if ssh_config is not None else None

    if not clean_jump:
        keyscan = _resolve_openssh_tool("ssh-keyscan")
        argv = [keyscan, "-T", "8", "-p", str(clean_port), clean_host]
        rc, out, err = _run_bounded(argv, timeout=DEFAULT_KEYSCAN_TIMEOUT)
    else:
        hops = clean_jump.split(",")
        last_hop = hops[-1]
        prior_hops = hops[:-1]
        argv = _base_batch_ssh_argv(cfg_path)
        if prior_hops:
            argv.extend(["-J", ",".join(prior_hops)])
        argv.extend(["--", last_hop, f"ssh-keyscan -T 8 -p {clean_port} {shlex.quote(clean_host)}"])
        rc, out, err = _run_bounded(argv, timeout=DEFAULT_KEYSCAN_TIMEOUT + 5.0)

    out_text = out.decode("utf-8", errors="replace")
    err_text = err.decode("utf-8", errors="replace").strip()
    fingerprints = _normalize_scanned_entries(out_text, clean_host, clean_port)
    if not fingerprints:
        low_err = err_text.lower()
        if "host key verification failed" in low_err:
            raise DomainError("跳板机指纹尚未信任，请先扫描并信任跳板机指纹。", "ssh_jump_host_key_unverified")
        if "permission denied" in low_err:
            raise DomainError("跳板机免密认证未通过，请先完成跳板机免密登录配置。", "ssh_jump_auth_failed")
        raise DomainError(
            f"未能扫描到 {clean_host}:{clean_port} 的主机指纹（请检查网络/VPN、端口或跳板机连通性）。",
            "ssh_keyscan_failed",
        )
    return {
        "hostname": clean_host,
        "port": clean_port,
        "jump_alias": clean_jump,
        "entries": [row["entry"] for row in fingerprints],
        "fingerprints": fingerprints,
    }


def trust_host_keys(ssh_dir: str | Path | None, entries: list[str]) -> dict:
    """Validate and atomically append unique OpenSSH host-key entries into ``~/.ssh/known_hosts``."""
    if not isinstance(entries, list) or not entries or len(entries) > MAX_SCAN_ENTRIES:
        raise DomainError(f"Provide between 1 and {MAX_SCAN_ENTRIES} known_hosts entries.", "ssh_known_host_invalid")
    validated = [validate_known_host_entry(item) for item in entries]
    base = _ensure_ssh_dir(ssh_dir)
    kh_path = base / "known_hosts"
    existing_text = _read_text_bounded(kh_path, MAX_KNOWN_HOSTS_BYTES) if kh_path.exists() else ""
    existing_lines = existing_text.splitlines()
    existing_keys: set[tuple[str, str, str]] = set()
    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) >= 3:
            existing_keys.add((parts[0], parts[1], parts[2]))

    added = 0
    out_lines = list(existing_lines)
    for host_pat, key_type, key_blob, normalized in validated:
        sig = (host_pat, key_type, key_blob)
        if sig not in existing_keys:
            out_lines.append(normalized)
            existing_keys.add(sig)
            added += 1

    final_text = "\n".join(out_lines) + ("\n" if out_lines else "")
    _atomic_write_private(kh_path, final_text, mode=0o600)
    return {
        "known_hosts_path": str(kh_path),
        "added": added,
        "total": len(out_lines),
    }


# ---------------------------------------------------------------------------
# One-time password public key deployment (zero password persistence)
# ---------------------------------------------------------------------------

def _create_askpass_helper(temp_dir: Path, password: str) -> Path:
    """Write a short-lived ``SSH_ASKPASS`` helper inside a ``0700`` temporary directory."""
    secret_file = temp_dir / "pw.txt"
    secret_file.write_bytes(password.encode("utf-8"))
    chmod_private(secret_file, 0o600)
    if IS_WINDOWS:
        reader_py = temp_dir / "askpass.py"
        reader_py.write_text(
            "import pathlib, sys\n"
            "sys.stdout.write(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))\n",
            encoding="utf-8",
        )
        helper = temp_dir / "askpass.cmd"
        helper.write_text(
            f'@echo off\r\n"{sys.executable}" "{reader_py}" "{secret_file}"\r\n',
            encoding="utf-8",
        )
        return helper
    helper = temp_dir / "askpass.sh"
    helper.write_text(
        "#!/bin/sh\n"
        f"cat {shlex.quote(str(secret_file))}\n",
        encoding="utf-8",
    )
    os.chmod(helper, 0o700)
    return helper


def deploy_public_key(
    *,
    ssh_dir: str | Path | None,
    hostname: str,
    port: int | str = 22,
    username: str,
    public_key: str,
    password: str,
    proxy_jump: str = "",
    ssh_config: str | Path | None = None,
    accept_new_host_key: bool = True,
    legacy_rsa: bool = False,
    legacy_kex: bool = False,
) -> dict:
    """Deploy ``public_key`` to remote ``~/.ssh/authorized_keys`` using a one-time password.

    The password is never written outside the ephemeral ``0700`` temp directory,
    never logged, and never returned. Public key text is transmitted via stdin
    as base64 with zero shell interpolation.
    """
    clean_host = validate_hostname(hostname)
    clean_port = validate_port(port)
    clean_user = validate_username(username)
    clean_pub = validate_public_key(public_key)
    clean_jump = validate_proxy_jump(proxy_jump)
    if not isinstance(password, str) or not 1 <= len(password) <= 1024:
        raise DomainError("Provide a non-empty one-time login password (up to 1024 chars).", "ssh_password_invalid")
    if "\x00" in password or "\n" in password or "\r" in password:
        raise DomainError("Password must not contain newline or NUL characters.", "ssh_password_invalid")

    base = _ensure_ssh_dir(ssh_dir)
    cfg_path = Path(ssh_config).expanduser() if ssh_config is not None else (base / "config")
    kh_path = base / "known_hosts"
    fp = public_key_fingerprint(clean_pub)
    pub_b64 = base64.b64encode(clean_pub.encode("utf-8")).decode("ascii")

    # Zero shell interpolation: base64 payload contains only [A-Za-z0-9+/=]
    remote_script = (
        "set -eu\n"
        "umask 077\n"
        'mkdir -p "$HOME/.ssh"\n'
        'chmod 700 "$HOME/.ssh"\n'
        f"KEY_B64='{pub_b64}'\n"
        'if command -v base64 >/dev/null 2>&1; then\n'
        '  KEY=$(printf "%s" "$KEY_B64" | base64 -d 2>/dev/null || printf "%s" "$KEY_B64" | base64 -D)\n'
        'elif command -v python3 >/dev/null 2>&1; then\n'
        '  KEY=$(python3 -c "import base64,sys; sys.stdout.write(base64.b64decode(sys.argv[1]).decode())" "$KEY_B64")\n'
        'else\n'
        '  KEY=$(python -c "import base64,sys; sys.stdout.write(base64.b64decode(sys.argv[1]))" "$KEY_B64")\n'
        'fi\n'
        'AUTH="$HOME/.ssh/authorized_keys"\n'
        'touch "$AUTH"\n'
        'chmod 600 "$AUTH"\n'
        'if grep -Fqx "$KEY" "$AUTH" 2>/dev/null; then\n'
        '  printf "SPICEOWL_KEY_ALREADY_PRESENT\\n"\n'
        'else\n'
        '  printf "\\n%s\\n" "$KEY" >> "$AUTH"\n'
        '  printf "SPICEOWL_KEY_DEPLOYED\\n"\n'
        'fi\n'
    ).encode("utf-8")

    with tempfile.TemporaryDirectory(prefix="spiceowl-askpass-") as tmp_dir_name:
        tmp_dir = Path(tmp_dir_name)
        chmod_private(tmp_dir, 0o700)
        helper = _create_askpass_helper(tmp_dir, password)

        env = dict(os.environ)
        env["SSH_ASKPASS"] = str(helper)
        env["SSH_ASKPASS_REQUIRE"] = "force"
        env["DISPLAY"] = env.get("DISPLAY") or "spiceowl:0"
        env.pop("SSH_AUTH_SOCK", None)

        argv = [resolve_ssh_executable()]
        if cfg_path.is_file():
            argv.extend(["-F", str(cfg_path)])
        strict_mode = "accept-new" if accept_new_host_key else "yes"
        extra_legacy: list[str] = []
        if legacy_rsa:
            extra_legacy.extend(["HostKeyAlgorithms=+ssh-rsa", "PubkeyAcceptedAlgorithms=+ssh-rsa"])
        if legacy_kex:
            extra_legacy.append("KexAlgorithms=+diffie-hellman-group14-sha1,diffie-hellman-group-exchange-sha1")
        opts = ssh_client_options((
            "BatchMode=no",
            f"StrictHostKeyChecking={strict_mode}",
            f"UserKnownHostsFile={kh_path}",
            "PreferredAuthentications=keyboard-interactive,password",
            "PubkeyAuthentication=no",
            "NumberOfPasswordPrompts=1",
            "ForwardAgent=no",
            "PermitLocalCommand=no",
            "ControlMaster=no",
            "ControlPath=none",
            "ControlPersist=no",
            "ClearAllForwardings=yes",
            "ForwardX11=no",
            "RequestTTY=no",
            "ConnectionAttempts=1",
            "ConnectTimeout=10",
            *extra_legacy,
        ))
        for opt in opts:
            argv.extend(["-o", opt])
        if clean_jump:
            argv.extend(["-J", clean_jump])
        argv.extend(["-p", str(clean_port), "-l", clean_user, "--", clean_host, "/bin/sh -s"])

        rc, out, err = _run_bounded(argv, stdin_bytes=remote_script, env=env, timeout=25.0)

    out_text = out.decode("utf-8", errors="replace")
    err_low = err.decode("utf-8", errors="replace").lower()
    if rc == 0 and ("SPICEOWL_KEY_DEPLOYED" in out_text or "SPICEOWL_KEY_ALREADY_PRESENT" in out_text):
        already = "SPICEOWL_KEY_ALREADY_PRESENT" in out_text
        return {
            "ok": True,
            "code": "already_present" if already else "deployed",
            "already_present": already,
            "hostname": clean_host,
            "port": clean_port,
            "username": clean_user,
            "fingerprint": fp,
            "message": "该公钥已存在于远端 authorized_keys，无需重复写入。" if already else "公钥已自动写入远端 ~/.ssh/authorized_keys（一次性密码已立即销毁）。",
        }
    if "no matching host key type found" in err_low or "no matching key exchange method found" in err_low:
        return {
            "ok": False,
            "code": "legacy_ssh_negotiation",
            "already_present": False,
            "hostname": clean_host,
            "port": clean_port,
            "username": clean_user,
            "fingerprint": fp,
            "message": "远端为旧版 OpenSSH（仅支持 ssh-rsa / 旧加密套件），需启用旧版兼容模式后重试。",
        }
    if "remote host identification has changed" in err_low:
        return {
            "ok": False,
            "code": "host_key_changed",
            "already_present": False,
            "hostname": clean_host,
            "port": clean_port,
            "username": clean_user,
            "fingerprint": fp,
            "message": "远端主机指纹与本地 known_hosts 记录冲突，请联系管理员核实后再继续。",
        }
    if "host key verification failed" in err_low:
        return {
            "ok": False,
            "code": "need_host_key",
            "already_present": False,
            "hostname": clean_host,
            "port": clean_port,
            "username": clean_user,
            "fingerprint": fp,
            "message": "尚未信任远端主机指纹，请先完成第 3 步「扫描并信任指纹」。",
        }
    if "permission denied" in err_low or "authentication failed" in err_low:
        return {
            "ok": False,
            "code": "auth_failed",
            "already_present": False,
            "hostname": clean_host,
            "port": clean_port,
            "username": clean_user,
            "fingerprint": fp,
            "message": "用户名或一次性密码校验失败（或该服务器禁止密码登录、需要二次验证码/手动粘贴公钥）。",
        }
    if any(k in err_low for k in ("could not resolve", "no route", "timed out", "connection refused", "unreachable")):
        return {
            "ok": False,
            "code": "network_unreachable",
            "already_present": False,
            "hostname": clean_host,
            "port": clean_port,
            "username": clean_user,
            "fingerprint": fp,
            "message": "无法连接到目标主机或跳板机，请检查 VPN、端口与跳板机设置。",
        }
    return {
        "ok": False,
        "code": "deploy_failed",
        "already_present": False,
        "hostname": clean_host,
        "port": clean_port,
        "username": clean_user,
        "fingerprint": fp,
        "message": "自动部署公钥未成功；建议复制上方公钥并通过终端手动写入远端 ~/.ssh/authorized_keys。",
    }


# ---------------------------------------------------------------------------
# Step-by-step connectivity diagnostics
# ---------------------------------------------------------------------------

def _lookup_alias_metadata(ssh_dir: Path, alias: str) -> dict:
    """Resolve hostname/port/user/proxy_jump from managed file or ~/.ssh/config."""
    for host in list_managed_hosts(ssh_dir):
        if host["alias"] == alias:
            return dict(host)
    # Fallback read-only parse of ~/.ssh/config
    from spiceowl.onboarding_wizard import parse_ssh_config_aliases
    cfg = ssh_dir / "config"
    if cfg.is_file():
        for row in parse_ssh_config_aliases(cfg):
            if row.get("alias") == alias:
                return {
                    "alias": alias,
                    "hostname": row.get("hostname") or alias,
                    "port": int(row.get("port") or 22),
                    "user": row.get("user") or "",
                    "proxy_jump": row.get("proxy_jump") or "",
                    "identity_file": "",
                    "managed": False,
                }
    return {"alias": alias, "hostname": alias, "port": 22, "user": "", "proxy_jump": "", "identity_file": "", "managed": False}


def _tcp_probe(host: str, port: int, timeout: float = DEFAULT_TCP_TIMEOUT) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True, f"TCP {host}:{port} 端口可达。"
    except socket.gaierror:
        return False, f"无法解析主机名 {host}，请检查拼写、DNS 或是否已连接 VPN / 校园网。"
    except TimeoutError:
        return False, f"连接 {host}:{port} 超时（{timeout:.0f}s），请检查是否需要 VPN、防火墙或配置跳板机。"
    except OSError as exc:
        return False, f"无法连通 {host}:{port}（{exc.strerror or type(exc).__name__}），请检查端口、SSH 服务或网络。"


def _classify_ssh_failure(err_bytes: bytes, *, role: str) -> tuple[str, str, dict]:
    raw_text = err_bytes.decode("utf-8", errors="replace").strip()
    low = raw_text.lower()
    # Skip OpenSSH advisory banners (e.g. macOS OpenSSH 10 "** WARNING: ... post-quantum ...")
    meaningful_lines = [
        ln.strip() for ln in raw_text.splitlines()
        if ln.strip() and not ln.strip().startswith("** ")
    ]
    first_line = (meaningful_lines[-1] if meaningful_lines else "")[:180]
    flags = {"needs_legacy_rsa": False, "needs_legacy_kex": False, "raw_hint": first_line}
    if "no matching host key type found" in low or ("their offer:" in low and "ssh-rsa" in low):
        flags["needs_legacy_rsa"] = True
        return (
            "legacy_ssh_negotiation",
            f"{role}为旧版 OpenSSH（仅提供 ssh-rsa 主机算法），需启用 RSA 兼容模式（HostKeyAlgorithms +ssh-rsa）。",
            flags,
        )
    if "no matching key exchange method found" in low:
        flags["needs_legacy_kex"] = True
        return (
            "legacy_ssh_negotiation",
            f"{role}为旧版 OpenSSH（密钥交换算法较旧），需启用旧版 KEX 兼容模式。",
            flags,
        )
    if "remote host identification has changed" in low:
        return "need_host_key", f"{role}主机指纹发生变化，与本地 known_hosts 冲突，请核实后更新指纹。", flags
    if "host key verification failed" in low:
        return "need_host_key", f"本地尚未信任{role}指纹，请先扫描并确认写入 ~/.ssh/known_hosts。", flags
    if "permission denied" in low or "publickey" in low:
        return "need_key_deploy", f"{role}免密认证未通过：请将本机公钥部署到{role}，或切换为已授权的本地密钥。", flags
    if any(k in low for k in ("could not resolve", "name or service not known", "nodename nor servname", "no route", "unreachable", "timed out", "connection refused")):
        return "network_unreachable", f"{role}网络不可达或超时，请检查 VPN、主机地址、端口与跳板机路由。", flags
    extra = f"（OpenSSH 提示：{first_line}）" if first_line else ""
    return "ssh_error", f"{role} SSH 连接失败，请核对主机名、用户名、跳板机与密钥配置{extra}。", flags


def _parse_probe_kv(stdout_text: str) -> dict:
    parsed = {"username": None, "hostname": None, "python3": None, "python": None, "spectre": None, "ngspice": None}
    mapping = {
        "USER": "username",
        "HOST": "hostname",
        "PY3": "python3",
        "PY": "python",
        "SPECTRE": "spectre",
        "NGSPICE": "ngspice",
    }
    for line in stdout_text.splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        field = mapping.get(k.strip())
        if field:
            val = v.strip()
            parsed[field] = val or None
    return parsed


def diagnose_connection(ssh_dir: str | Path | None, alias: str) -> dict:
    """Run staged TCP, jump-host, target auth, and remote runtime diagnostics for ``alias``."""
    clean_alias = validate_alias(alias)
    base = _ssh_dir_path(ssh_dir)
    cfg_path = base / "config"
    meta = _lookup_alias_metadata(base, clean_alias)
    steps: list[dict] = []
    remote: dict = {"username": None, "hostname": None, "python3": None, "python": None, "spectre": None, "ngspice": None}

    proxy_jump = (meta.get("proxy_jump") or "").strip()
    if proxy_jump:
        first_hop = proxy_jump.split(",")[0].strip()
        try:
            hop_user, hop_host_or_alias, hop_port = _parse_single_jump_hop(first_hop)
        except DomainError:
            hop_user, hop_host_or_alias, hop_port = None, first_hop, None
        hop_meta = _lookup_alias_metadata(base, hop_host_or_alias) if hop_port is None else {}
        tcp_host = hop_meta.get("hostname") or hop_host_or_alias
        tcp_port = int(hop_port or hop_meta.get("port") or 22)
        eff_jump_user = hop_user or hop_meta.get("user") or meta.get("user") or ""
        jump_hop_info = {
            "role": "jump",
            "alias": hop_meta.get("alias") or hop_host_or_alias,
            "hostname": tcp_host,
            "port": tcp_port,
            "username": eff_jump_user,
            "proxy_jump": "",
            "legacy_rsa": bool(hop_meta.get("legacy_rsa", False)),
            "legacy_kex": bool(hop_meta.get("legacy_kex", False)),
            "managed": bool(hop_meta.get("managed", False)),
        }

        tcp_ok, tcp_detail = _tcp_probe(tcp_host, tcp_port)
        steps.append({"id": "jump_tcp", "label": f"跳板机网络连通 ({tcp_host}:{tcp_port})", "ok": tcp_ok, "detail": tcp_detail})
        if not tcp_ok:
            return {"ok": False, "alias": clean_alias, "code": "network_unreachable", "steps": steps, "remote": remote, "failing_hop": jump_hop_info}

        jump_argv = _base_batch_ssh_argv(cfg_path) + ["--", first_hop, "true"]
        j_rc, _j_out, j_err = _run_bounded(jump_argv, timeout=DEFAULT_SSH_TIMEOUT)
        if j_rc != 0:
            code, detail, flags = _classify_ssh_failure(j_err, role=f"跳板机 ({first_hop}) ")
            steps.append({"id": "jump_auth", "label": f"跳板机免密认证 ({first_hop})", "ok": False, "detail": detail})
            return {"ok": False, "alias": clean_alias, "code": code, "steps": steps, "remote": remote, "failing_hop": {**jump_hop_info, **flags}}
        steps.append({"id": "jump_auth", "label": f"跳板机免密认证 ({first_hop})", "ok": True, "detail": f"跳板机 {first_hop} 免密认证通过。"})
    else:
        target_host = meta.get("hostname") or clean_alias
        target_port = int(meta.get("port") or 22)
        tcp_ok, tcp_detail = _tcp_probe(target_host, target_port)
        steps.append({"id": "target_tcp", "label": f"目标机网络连通 ({target_host}:{target_port})", "ok": tcp_ok, "detail": tcp_detail})
        if not tcp_ok:
            return {
                "ok": False, "alias": clean_alias, "code": "network_unreachable", "steps": steps, "remote": remote,
                "failing_hop": {
                    "role": "target", "alias": clean_alias, "hostname": target_host, "port": target_port,
                    "username": meta.get("user") or "", "proxy_jump": "",
                    "legacy_rsa": bool(meta.get("legacy_rsa", False)), "legacy_kex": bool(meta.get("legacy_kex", False)),
                    "managed": bool(meta.get("managed", False)),
                },
            }

    # Feed POSIX sh script over stdin to `/bin/sh -s` so csh/tcsh/fish EDA login shells
    # never choke on POSIX `$(...)` or `2>/dev/null` syntax!
    probe_script = (
        "printf 'USER=%s\\n' \"$(id -un 2>/dev/null || true)\"\n"
        "printf 'HOST=%s\\n' \"$(hostname 2>/dev/null || true)\"\n"
        "printf 'PY3=%s\\n' \"$(command -v python3 2>/dev/null || true)\"\n"
        "printf 'PY=%s\\n' \"$(command -v python 2>/dev/null || true)\"\n"
        "printf 'SPECTRE=%s\\n' \"$(command -v spectre 2>/dev/null || true)\"\n"
        "printf 'NGSPICE=%s\\n' \"$(command -v ngspice 2>/dev/null || true)\"\n"
    ).encode("utf-8")
    target_argv = _base_batch_ssh_argv(cfg_path) + ["--", clean_alias, "/bin/sh -s"]
    t_rc, t_out, t_err = _run_bounded(target_argv, stdin_bytes=probe_script, timeout=DEFAULT_SSH_TIMEOUT)
    if t_rc != 0:
        code, detail, flags = _classify_ssh_failure(t_err, role=f"目标机 ({clean_alias}) ")
        steps.append({"id": "target_auth", "label": f"目标机免密认证 ({clean_alias})", "ok": False, "detail": detail})
        return {
            "ok": False, "alias": clean_alias, "code": code, "steps": steps, "remote": remote,
            "failing_hop": {
                "role": "target", "alias": clean_alias,
                "hostname": meta.get("hostname") or clean_alias,
                "port": int(meta.get("port") or 22),
                "username": meta.get("user") or "",
                "proxy_jump": proxy_jump,
                "legacy_rsa": bool(meta.get("legacy_rsa", False)),
                "legacy_kex": bool(meta.get("legacy_kex", False)),
                "managed": bool(meta.get("managed", False)),
                **flags,
            },
        }

    remote = _parse_probe_kv(t_out.decode("utf-8", errors="replace"))
    who = f"{remote['username'] or '?'}@{remote['hostname'] or clean_alias}"
    steps.append({"id": "target_auth", "label": f"目标机免密认证 ({clean_alias})", "ok": True, "detail": f"免密登录成功：{who}。"})

    py_path = remote["python3"] or remote["python"]
    steps.append({
        "id": "remote_python",
        "label": "远端 Python 运行时",
        "ok": bool(py_path),
        "detail": f"已检测到 {py_path}" if py_path else "默认 PATH 未检测到 python3/python，提交仿真时需指定远端 Python 路径。",
    })
    sims = [f"{k}={remote[k]}" for k in ("spectre", "ngspice") if remote.get(k)]
    steps.append({
        "id": "remote_simulators",
        "label": "远端仿真器 (Spectre / ngspice)",
        "ok": True,
        "detail": ("已检测到 " + "，".join(sims)) if sims else "默认 login PATH 未发现 spectre/ngspice（如通过 module load / .cshrc 加载，可在仿真面板中指定环境脚本）。",
    })

    if not py_path:
        return {"ok": False, "alias": clean_alias, "code": "missing_python", "steps": steps, "remote": remote}
    return {"ok": True, "alias": clean_alias, "code": "ready", "steps": steps, "remote": remote}

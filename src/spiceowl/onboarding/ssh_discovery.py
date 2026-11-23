"""Offline literal SSH aliases from one explicitly selected configuration.

This is an inventory, not an OpenSSH configuration evaluator. No subprocess,
Match exec, authentication, key-file directive, or network operation is used.
"""
import fnmatch
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import shlex
import stat
import time

from spiceowl.domain.common import DomainError

ALIAS = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}')
MAX_FILES = 32
MAX_DEPTH = 6
MAX_FILE_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 512 * 1024
MAX_ALIASES = 256
MAX_MATCHES = 64
MAX_DIRECTORY_ENTRIES = 256
MAX_READ_ATTEMPTS = 64
_SENSITIVE = re.compile(r'(?i)(?:^|[._-])(?:id_rsa|id_dsa|id_ecdsa|id_ed25519|authorized_keys|known_hosts|identity|private|secret|token)(?:$|[._-])|\.(?:pem|key|p12|pfx|pub)$')


def validate_alias(alias):
    if not isinstance(alias, str) or ALIAS.fullmatch(alias) is None or '..' in alias or alias.endswith('.'):
        raise DomainError('Select one literal SSH alias without options or wildcard patterns.', 'ssh_alias_invalid')
    return alias


def validate_ssh_target(target):
    """Accept an explicit alias or user@DNS/IPv4, without commands or flags."""
    if not isinstance(target, str) or len(target) > 320 or any(c.isspace() for c in target) or ':' in target:
        raise DomainError('Use an SSH alias or user@hostname; configure ports separately. IPv6 is not supported here.', 'ssh_target_invalid')
    if '@' not in target:
        return validate_alias(target)
    if target.count('@') != 1:
        raise DomainError('Use one explicit user@hostname target.', 'ssh_target_invalid')
    user, host = target.split('@')
    label = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?')
    if (not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,63}', user) or not 1 <= len(host) <= 253
            or any(label.fullmatch(part) is None for part in host.split('.'))):
        raise DomainError('Use a bounded username and DNS name or IPv4 address.', 'ssh_target_invalid')
    if re.fullmatch(r'[0-9]+(?:\.[0-9]+){3}', host):
        try: ipaddress.IPv4Address(host)
        except ValueError:
            raise DomainError('The explicit IPv4 address is invalid.', 'ssh_target_invalid') from None
    return target


def config_path(path):
    if not isinstance(path, (str, Path)):
        raise DomainError('Select an explicit SSH configuration file.', 'ssh_discovery_invalid')
    value = Path(path).expanduser()
    if not value.is_absolute() or _SENSITIVE.search(value.name):
        raise DomainError('Select an absolute SSH configuration path, excluding credential files.', 'ssh_discovery_invalid')
    return value


def _config_name(path):
    return (not _SENSITIVE.search(path.name) and (path.name in ('config', 'ssh_config', 'hosts')
            or path.suffix.lower() in ('.conf', '.config')
            or path.parent.name in ('config.d', 'conf.d', 'ssh_config.d', 'hosts.d')))


def discover_ssh_hosts(path, *, user_home=None):
    """List literal Host aliases with bounded Include metadata, without SSH.

    Relative user Include paths use ``user_home/.ssh``. ``user_home`` exists for
    explicit alternate-user installations and offline fixtures. Unknown token
    expansion and conditional evaluation remain reported limitations.
    """
    selected = config_path(path)
    home = Path(user_home).expanduser() if user_home is not None else Path.home()
    if not home.is_absolute():
        raise DomainError('The selected user home must be absolute.', 'ssh_discovery_invalid')
    aliases, visited, stack, warnings, hashes, unsupported = {}, set(), set(), [], [], []
    total, matched_count, attempts, directory_scans = 0, 0, 0, 0
    deadline = time.monotonic() + 3.0
    def warn(code):
        if code not in warnings:
            warnings.append(code)
    def read(file, depth, top=False):
        nonlocal total, matched_count, attempts, directory_scans
        if time.monotonic() > deadline:
            warn('discovery_time_limit'); return
        lexical = str(file.absolute())
        if lexical in stack:
            warn('include_cycle'); return
        if lexical in visited:
            return
        if depth > MAX_DEPTH or len(visited) >= MAX_FILES:
            warn('include_limit'); return
        if not top and not _config_name(file):
            warn('non_config_include_skipped')
            if len(unsupported) < 64:
                unsupported.append({'basename': file.name[:128], 'reason': 'non_config_include_skipped'})
            return
        attempts += 1
        if attempts > MAX_READ_ATTEMPTS:
            warn('config_attempt_limit'); return
        try:
            fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    warn('non_regular_config'); return
                if info.st_size > MAX_FILE_BYTES or total + info.st_size > MAX_TOTAL_BYTES:
                    warn('config_byte_limit'); return
                raw = stream.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES or total + len(raw) > MAX_TOTAL_BYTES:
                warn('config_byte_limit'); return
            text = raw.decode('utf-8')
        except (OSError, UnicodeError):
            if top:
                raise DomainError('The selected SSH configuration cannot be read as a bounded regular UTF-8 file.', 'ssh_config_unavailable') from None
            warn('include_unavailable'); return
        total += len(raw); visited.add(lexical); stack.add(lexical)
        hashes.append(hashlib.sha256(raw).hexdigest())
        for line in text.splitlines():
            if len(line) > 8192:
                warn('config_line_limit'); continue
            try:
                words = shlex.split(line, comments=True, posix=True)
            except ValueError:
                warn('config_syntax_unknown'); continue
            if not words:
                continue
            if '=' in words[0]:
                key, first = words[0].split('=', 1); words = [key, first, *words[1:]]
            elif len(words) > 1 and words[1] == '=':
                words.pop(1)
            key = words[0].lower()
            if key == 'host':
                for name in words[1:]:
                    if any(c in name for c in '*?![]'):
                        continue
                    try: validate_alias(name)
                    except DomainError:
                        warn('invalid_literal_alias'); continue
                    if name not in aliases and len(aliases) >= MAX_ALIASES:
                        warn('alias_limit'); continue
                    aliases.setdefault(name, set()).add(lexical)
            elif key == 'match':
                warn('conditional_configuration_not_evaluated')
            elif key == 'include':
                for pattern in words[1:]:
                    if any(c in pattern for c in '$%`\x00\n\r') or pattern.startswith('~') and not pattern.startswith('~/'):
                        warn('include_expansion_unsupported'); continue
                    file_pattern = str(home / pattern[2:]) if pattern.startswith('~/') else pattern
                    if not os.path.isabs(file_pattern):
                        file_pattern = str(home / '.ssh' / file_pattern)
                    parent, leaf = os.path.split(file_pattern)
                    if any(c in parent for c in '*?['):
                        warn('include_directory_glob_unsupported'); continue
                    matched = []
                    if not any(c in leaf for c in '*?['):
                        matched = [file_pattern]
                    else:
                        directory_scans += 1
                        if directory_scans > MAX_FILES or time.monotonic() > deadline:
                            warn('include_directory_scan_limit'); continue
                        try:
                            with os.scandir(parent) as entries:
                                for index, entry in enumerate(entries):
                                    if index >= MAX_DIRECTORY_ENTRIES:
                                        warn('include_directory_entry_limit'); break
                                    if fnmatch.fnmatchcase(entry.name, leaf):
                                        matched_count += 1
                                        if matched_count > MAX_MATCHES:
                                            warn('include_glob_limit'); break
                                        matched.append(entry.path)
                        except OSError:
                            warn('include_unavailable')
                    for candidate in sorted(matched):
                        read(Path(candidate), depth + 1)
        stack.remove(lexical)
    read(selected, 0, True)
    return {'schema': 'spiceowl.ssh-discovery.v1',
            'hosts': [{'alias': name, 'source_count': len(files)} for name, files in sorted(aliases.items())],
            'files_scanned': len(visited), 'bytes_read': total, 'complete': not warnings,
            'warnings': [{'code': code} for code in warnings],
            'unsupported_includes': unsupported, 'manual_entry_available': True,
            'configuration_sha256': hashlib.sha256(''.join(hashes).encode()).hexdigest(),
            'effective_configuration_resolved': False, 'network_operations': 0,
            'credential_directives_followed': False}

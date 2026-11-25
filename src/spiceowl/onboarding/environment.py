"""Explicit bounded SSH environment observation with separate private evidence.

Only ``probe_server_environment`` returns presentation-safe output. Backend code
may call ``capture_server_environment`` and persist its ``private`` member in
owner-private storage. Discovery never sources a discovered file or starts a
simulator. An explicit ``activate_observed_environment`` selection may evaluate
a previously observed setup script; script side effects are not audited.
The existing SSH route and the remote login environment are trusted.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import time

from spiceowl._platform import (
    BoundedIOError,
    communicate_bounded,
    popen_isolation_kwargs,
    resolve_ssh_executable,
    ssh_client_options,
    terminate_process_tree,
)
from spiceowl.adapters.executors.ssh import _SSH_OPTIONS, _SSH_ERROR_MESSAGES, _StderrHints
from spiceowl.domain.common import DomainError, utcnow
from .ssh_discovery import config_path, discover_ssh_hosts, validate_ssh_target

PROBE_TIMEOUT = 35.0
MAX_OUTPUT_BYTES = 256 * 1024
LICENSE_NAMES = ('CDS_LIC_FILE', 'LM_LICENSE_FILE')

# This literal is authored program code, not generated from remote documents.
# Python 2.7 syntax is retained for older EDA login nodes.
REMOTE_PYTHON = r'''
from __future__ import print_function
import hashlib, json, math, os, pwd, re, shlex, stat, sys, time

LIMITS = {'depth': 6, 'files': 32, 'file_bytes': 65536, 'total_bytes': 524288,
          'directory_entries': 128, 'directories': 16, 'seconds': 12}
LICENSES = ('CDS_LIC_FILE', 'LM_LICENSE_FILE')
SENSITIVE = re.compile(r'(?i)(?:^|[._-])(?:id_rsa|id_dsa|id_ecdsa|id_ed25519|authorized_keys|known_hosts|private|secret|token|password|credentials)(?:$|[._-])|\.(?:pem|key|p12|pfx|pub)$')
ROOT_NAME = re.compile(r'(?i)(?:pdk|process|technology|technologies|foundry|design.?kit|^tech$)')
ROOT_VAR = re.compile(r'(?i)(?:PDK|PROCESS|TECH|FOUNDRY|DESIGNKIT|CADENCE|SPECTRE|CDS|EDA)')

def text(value):
    if isinstance(value, bytes):
        return value.decode('utf-8', 'replace')
    return value

def source_id(path):
    return 'source-' + hashlib.sha256(path.encode('utf-8')).hexdigest()[:20]

def setup_shell(path, body):
    """Classify observed shell evidence; conflicting or unknown syntax stays so."""
    name = os.path.basename(path).lower()
    csh = name in ('.cshrc', '.tcshrc', '.login', 'csh.cshrc', 'csh.login') or name.endswith(('.csh', '.tcsh'))
    posix = name in ('.profile', '.bashrc', '.bash_profile', '.bash_login', 'profile', 'bash.bashrc') or name.endswith('.sh')
    basis = 'filename' if csh or posix else None
    first = body.splitlines()[0] if body.splitlines() else ''
    if first.startswith('#!'):
        if re.search(r'/(?:t?csh)(?:\s|$)', first): csh = True; basis = 'shebang'
        elif re.search(r'/(?:ba|da|k|z)?sh(?:\s|$)', first): posix = True; basis = 'shebang'
        else: return 'unknown', 'unrecognized'
    for line in body.splitlines():
        if re.match(r'^\s*(?:setenv\s+[A-Za-z_]|set\s+path\s*=|endif\s*$)', line):
            csh = True; basis = basis or 'observed_statements'
        if re.match(r'^\s*(?:export\s+[A-Za-z_]|fi\s*$|[A-Za-z_][A-Za-z0-9_]*=)', line):
            posix = True; basis = basis or 'observed_statements'
    if csh and not posix: return 'csh', basis
    if posix and not csh: return 'posix', basis
    return 'unknown', 'conflicting_shell_evidence' if csh and posix else 'unrecognized'

def remote_platform():
    """Read finite remote platform facts; os-release is data, never shell code."""
    result = {'system': None, 'machine': None, 'os_release': {'id': None, 'id_like': []},
        'basis': {'system': 'unavailable', 'machine': 'unavailable', 'os_release': 'unavailable'},
        'os_release_status': 'unavailable'}
    try:
        uname = os.uname()
        result.update(system=text(uname[0]), machine=text(uname[4]))
        result['basis'].update(system='remote_uname', machine='remote_uname')
    except (OSError, AttributeError, IndexError):
        pass
    # Common installations link /etc/os-release to this exact standard file.
    # Other symlink targets are not another source-discovery opportunity.
    path = os.path.realpath('/etc/os-release')
    if path not in ('/etc/os-release', '/usr/lib/os-release'):
        result['os_release_status'] = 'invalid'; return result
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode): return result
            if info.st_size > 4096:
                result['os_release_status'] = 'limit'; return result
            raw = stream.read(4097)
    except (OSError, IOError):
        return result
    if len(raw) > 4096:
        result['os_release_status'] = 'limit'; return result
    seen, invalid = set(), False
    for line in text(raw).splitlines():
        match = re.match(r'^\s*(ID|ID_LIKE)\s*=\s*(.*?)\s*$', line)
        if not match: continue
        name, value = match.groups()
        if name in seen:
            invalid = True; continue
        seen.add(name)
        if len(value) >= 2 and value[0] in ('\"', "'") and value[-1] == value[0]:
            value = value[1:-1]
        tokens = value.split() if name == 'ID_LIKE' else [value]
        if (not tokens or len(tokens) > 8 or any(not re.match(r'^[a-z0-9][a-z0-9._-]{0,63}$', token) for token in tokens)):
            invalid = True; continue
        if name == 'ID': result['os_release']['id'] = tokens[0]
        else: result['os_release']['id_like'] = [token for index, token in enumerate(tokens) if token not in tokens[:index]]
    if invalid:
        result['os_release'] = {'id': None, 'id_like': []}
        result['os_release_status'] = 'invalid'
    elif seen:
        result['basis']['os_release'] = 'remote_os_release'
        result['os_release_status'] = 'observed'
    return result

def collect_environment():
    begun = time.time()
    env = dict((text(k), text(v)) for k, v in os.environ.items())
    home, cwd = env.get('HOME', ''), text(os.getcwd())
    account = pwd.getpwuid(os.geteuid())
    result = {'schema': 'spiceowl.server-environment.private.v1', 'probe_kind': 'python',
        'identity': {'hostname': text(os.uname()[1]), 'username': text(account.pw_name), 'uid': os.geteuid(),
                     'home': home, 'cwd': cwd, 'shell': env.get('SHELL', text(account.pw_shell))},
        'platform': remote_platform(),
        'path': env.get('PATH', '').split(os.pathsep), 'environment': [], 'tools': [], 'licenses': [],
        'scripts': [], 'source_chain': [], 'setup_candidates': [], 'pdk_candidates': [], 'library_roots': [],
        'resources': {'cpu_count': None, 'load_1m': None, 'memory_total_bytes': None, 'memory_available_bytes': None},
        'warnings': [], 'coverage': {'limits': LIMITS, 'files_read': 0, 'bytes_read': 0,
            'scripts_evaluated': False, 'model_body_discovery': False, 'environment_basis': 'observed_ssh_login_environment'}}
    warning_keys, visited, pdk_seen, lib_seen, chain_seen = set(), set(), set(), set(), set()
    def warn(code, path=None):
        key = (code, source_id(path) if path else None)
        if key not in warning_keys and len(result['warnings']) < 128:
            warning_keys.add(key)
            result['warnings'].append({'code': code, 'source_id': key[1]})
    def private_path(path):
        return (SENSITIVE.search(os.path.basename(path)) or
                any(part in ('.ssh', '.aws', '.gnupg', '.spiceowl', '.codex') for part in path.split('/')))
    def path_value(value):
        if not isinstance(value, (str, type(u''))) or len(value) > 4096 or any(c in value for c in '`\x00\n\r;|&<>') or '$(' in value:
            return None
        def expand(match):
            name = match.group(1) or match.group(2)
            return env.get(name, '$' + name)
        value = re.sub(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)', expand, value)
        if '$' in value or any(c in value for c in '*?[]'):
            return None
        if value == '~': value = home
        elif value.startswith('~/'): value = os.path.join(home, value[2:])
        elif value.startswith('~'): return None
        value = os.path.normpath(value if os.path.isabs(value) else os.path.join(cwd, value))
        return value if os.path.isabs(value) and not private_path(value) else None
    def read_file(path, kind, depth):
        if time.time() - begun > LIMITS['seconds']:
            warn('scan_time_limit'); return None
        path = os.path.realpath(path)
        if private_path(path):
            warn('sensitive_source_skipped', path); return None
        if path.lower().endswith(('.scs', '.sp', '.spice', '.v', '.va', '.vams')):
            warn('model_or_design_source_skipped', path); return None
        if path in visited: return None
        if depth > LIMITS['depth'] or len(visited) >= LIMITS['files']:
            warn('source_limit', path); return None
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    warn('non_regular_source', path); return None
                if info.st_size > LIMITS['file_bytes']:
                    warn('source_byte_limit', path); return None
                raw = stream.read(LIMITS['file_bytes'] + 1)
            if len(raw) > LIMITS['file_bytes'] or result['coverage']['bytes_read'] + len(raw) > LIMITS['total_bytes']:
                warn('source_byte_limit', path); return None
        except (OSError, IOError):
            return None
        visited.add(path)
        result['coverage']['files_read'] += 1; result['coverage']['bytes_read'] += len(raw)
        record = {'source_id': source_id(path), 'path': path, 'sha256': hashlib.sha256(raw).hexdigest(),
                  'bytes': len(raw), 'kind': kind, 'depth': depth, 'evaluated': False}
        result['scripts'].append(record)
        return record, raw.decode('utf-8', 'replace')
    def candidate(path, basis, evidence=None):
        path = path_value(path)
        if not path or path in pdk_seen or len(pdk_seen) >= 64: return
        pdk_seen.add(path)
        result['pdk_candidates'].append({'path': path, 'basis': basis, 'evidence_id': evidence,
            'exists': os.path.isdir(path), 'qualified': False})
    def words(line):
        try: return shlex.split(line, comments=True)
        except ValueError: return []
    def commands(line):
        # Separate only unquoted semicolons. Strings passed to echo/printf or
        # embedded in alias bodies are data at this level, never source calls.
        # Substitution can hide separators or synthesize a command; this
        # bounded scanner deliberately leaves those entire lines unresolved.
        if '$(' in line or '`' in line: return []
        result, current, quote, escaped, unsupported = [], [], None, False, False
        for char in line:
            if escaped:
                current.append(char); escaped = False; continue
            if char == '\\' and quote != "'":
                current.append(char); escaped = True; continue
            if quote:
                current.append(char)
                if char == quote: quote = None
                continue
            if char in ("'", '"'):
                quote = char; current.append(char)
            elif char == '#':
                break
            elif char == ';':
                if not unsupported: result.append(''.join(current).strip())
                current, unsupported = [], False
            elif char in '|&':
                unsupported = True; current.append(char)
            else:
                current.append(char)
        if quote or escaped: return []
        if not unsupported: result.append(''.join(current).strip())
        return [command for command in result if command]
    def source_references(line):
        references = []
        for command in commands(line):
            parsed = words(command)
            if not parsed: continue
            if parsed[0] == 'alias':
                # csh: alias name 'source /path'; POSIX: alias name='source /path'.
                # Decode the alias body once, then consider only independent
                # source statements with one literal path. Other commands in
                # the alias are ignored and no alias body is ever evaluated.
                if len(parsed) == 2 and '=' in parsed[1]:
                    name, body = parsed[1].split('=', 1)
                    statements = [words(item) for item in commands(body)]
                elif len(parsed) >= 3:
                    name = parsed[1]
                    if len(parsed) == 3:
                        statements = [words(item) for item in commands(parsed[2])]
                    else:
                        statements = [parsed[2:]]
                else:
                    continue
                if not re.match(r'^[A-Za-z_][A-Za-z0-9_-]{0,95}$', name):
                    continue
                for body_words in statements:
                    if len(body_words) != 2 or body_words[0] not in ('source', '.'):
                        continue
                    literal = body_words[1]
                    if not literal.startswith('/') or any(char in literal for char in '$`!;|&<>*?[]\\'):
                        continue
                    target = path_value(literal)
                    if target: references.append((target, 'alias_source_reference', name))
                continue
            # Ordinary explicit source positions may use already observed
            # environment variables. Conditions remain static and unevaluated.
            command = re.sub(r'^if\s*\([^)]*\)\s*', '', command)
            command = re.sub(r'^then\s+', '', command)
            parsed = words(command)
            if parsed and parsed[0] in ('source', '.'):
                target = path_value(parsed[1]) if len(parsed) == 2 else None
                references.append((target, 'static_source_reference', None))
        return references
    license_rows = {}
    for name in LICENSES:
        value = env.get(name, '')
        if len(value) > 4096:
            warn('license_value_limit'); value = ''
        row = {'name': name, 'configured': bool(value), 'value': value or None,
               'usable': 'unknown', 'declared_in_scripts': False, 'provenance': []}
        if value: row['provenance'].append({'basis': 'observed_login_environment'})
        result['licenses'].append(row); license_rows[name] = row
    for name, value in sorted(env.items()):
        if name in LICENSES or re.search(r'(?i)(KEY|TOKEN|SECRET|PASS|AUTH|COOKIE)', name): continue
        if ROOT_VAR.search(name) and len(value) <= 4096:
            paths = [path_value(part) for part in value.split(':') if part.startswith(('/', '~', '$'))]
            paths = [path for path in paths if path]
            if paths:
                result['environment'].append({'name': name, 'value': value, 'basis': 'observed_login_environment'})
                if re.search(r'(?i)(PDK|PROCESS|TECH|FOUNDRY|DESIGNKIT)', name):
                    for path in paths: candidate(path, 'observed_environment', name)
    def find_tool(name):
        for directory in result['path'][:256]:
            if not directory: directory = cwd
            path = os.path.join(directory, name)
            if os.path.isfile(path) and os.access(path, os.X_OK): return os.path.abspath(path)
        return None
    result['tools'].append({'kind': 'spectre', 'path': find_tool('spectre'), 'version': None, 'basis': 'login_PATH', 'usable': 'unknown'})
    result['tools'].append({'kind': 'python', 'path': text(sys.executable),
        'version': '.'.join(str(x) for x in sys.version_info[:3]), 'basis': 'probe_interpreter', 'usable': 'probe_executed'})
    def scan(path, kind='startup', depth=0):
        pair = read_file(path, kind, depth)
        if pair is None: return
        record, body = pair; sid = record['source_id']; setup = kind == 'alias_source_reference'
        for number, line in enumerate(body.splitlines(), 1):
            if len(line) > 8192:
                warn('source_line_limit', record['path']); continue
            clean = line.strip()
            if not clean or clean.startswith('#'): continue
            if kind == 'cds_lib':
                parsed = words(clean)
                if parsed and parsed[0].upper() in ('INCLUDE', 'SOFTINCLUDE') and len(parsed) == 2:
                    target = path_value(parsed[1])
                    if target: scan(target, 'cds_lib', depth + 1)
                    else: warn('library_include_unresolved', record['path'])
                elif len(parsed) == 3 and parsed[0].upper() == 'DEFINE':
                    target = path_value(parsed[2])
                    if target and target not in lib_seen and len(lib_seen) < 64:
                        lib_seen.add(target)
                        result['library_roots'].append({'logical_name': parsed[1][:128], 'path': target,
                            'basis': 'cds_lib_definition', 'source_id': sid, 'line': number})
                        if ROOT_NAME.search(parsed[1]) or ROOT_NAME.search(os.path.basename(target)):
                            candidate(target, 'cds_lib_definition', sid)
                continue
            if re.search(r'(?i)(spectre|cadence|CDS_INST_DIR)', clean): setup = True
            for name in LICENSES:
                if re.search(r'\b' + name + r'\b', clean):
                    row = license_rows[name]; row['declared_in_scripts'] = True
                    if len(row['provenance']) < 16:
                        row['provenance'].append({'basis': 'static_script_reference', 'source_id': sid, 'line': number})
            for target, basis, alias_name in source_references(clean):
                key = (sid, number, target, basis, alias_name)
                if key in chain_seen: continue
                chain_seen.add(key)
                if len(result['source_chain']) >= 128:
                    warn('source_chain_limit'); continue
                result['source_chain'].append({'source_id': sid, 'line': number, 'target_path': target,
                    'depth': depth + 1, 'basis': basis, 'alias_name': alias_name, 'executed': False})
                if target: scan(target, 'alias_source_reference' if basis == 'alias_source_reference' else 'source_reference', depth + 1)
                else: warn('source_reference_unresolved', record['path'])
            declaration = re.match(r'^(?:setenv\s+|export\s+|set\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*(?:=\s*|\s+)(.+)$', clean)
            if declaration and ROOT_VAR.search(declaration.group(1)) and declaration.group(1) not in LICENSES:
                parsed = words(declaration.group(2))
                if len(parsed) == 1 and parsed[0].startswith(('/', '~', '$')):
                    target = path_value(parsed[0])
                    if target and re.search(r'(?i)(PDK|PROCESS|TECH|FOUNDRY|DESIGNKIT)', declaration.group(1)):
                        candidate(target, 'static_script_declaration_not_effective_environment', sid)
        if setup and len(result['setup_candidates']) < 32:
            shell_kind, shell_basis = setup_shell(record['path'], body)
            result['setup_candidates'].append({'path': record['path'], 'source_id': sid, 'sha256': record['sha256'],
                'basis': 'alias_source_reference' if kind == 'alias_source_reference' else 'startup_or_source_reference',
                'shell_kind': shell_kind, 'shell_basis': shell_basis,
                'executed_by_probe': False})
    startup = ['/etc/csh.cshrc', '/etc/csh.login', '/etc/profile', '/etc/bash.bashrc']
    if home:
        startup += [os.path.join(home, name) for name in ('.tcshrc', '.cshrc', '.login', '.profile', '.bash_profile', '.bash_login', '.bashrc')]
    for path in startup: scan(path)
    for path in set([os.path.join(home, 'cds.lib'), os.path.join(cwd, 'cds.lib')]):
        if path: scan(path, 'cds_lib')
    for value in env.get('CDS_LIB_PATH', '').split(':')[:16]:
        if value:
            path = path_value(value)
            if path: scan(os.path.join(path, 'cds.lib') if os.path.isdir(path) else path, 'cds_lib')
    # Shallow directory names provide unqualified leads, never model bodies.
    bases = [home, cwd, os.path.dirname(home), os.path.dirname(cwd)]
    bases += [row['path'] for row in list(result['pdk_candidates'])]
    seen_dirs = set()
    for base in bases:
        if not base or base == '/' or base in seen_dirs or len(seen_dirs) >= LIMITS['directories']: continue
        if time.time() - begun > LIMITS['seconds']:
            warn('scan_time_limit'); break
        seen_dirs.add(base)
        try:
            if not hasattr(os, 'scandir'):
                # Python 2.7 offers no streaming directory API. Confine its
                # listdir fallback to at most four predetermined generic roots;
                # the entry cap applies after that one OS call returns.
                if base not in (home, cwd, os.path.dirname(home), os.path.dirname(cwd)):
                    warn('python2_additional_directory_skipped'); continue
                entries = os.listdir(base)
                if len(entries) > LIMITS['directory_entries']: warn('directory_entry_limit', base)
                names = sorted(entries)[:LIMITS['directory_entries']]
                result['coverage']['directory_listing_method'] = 'fixed_root_listdir'
                result['coverage']['directory_cap_after_os_return'] = True
                warn('python2_directory_cap_after_os_return')
            else:
                entries = os.scandir(base)
                names = []
                try:
                    for index, entry in enumerate(entries):
                        if index >= LIMITS['directory_entries']:
                            warn('directory_entry_limit', base); break
                        names.append(entry.name)
                finally:
                    entries.close()
                result['coverage']['directory_listing_method'] = 'bounded_scandir'
            if time.time() - begun > LIMITS['seconds']:
                warn('scan_time_limit'); break
            for name in sorted(names):
                if not name.startswith('.') and ROOT_NAME.search(name):
                    path = os.path.join(base, name)
                    if os.path.isdir(path): candidate(path, 'shallow_directory_name', source_id(base))
        except (OSError, IOError): pass
    try: result['resources']['cpu_count'] = int(os.sysconf('SC_NPROCESSORS_ONLN'))
    except (OSError, ValueError, AttributeError): pass
    try:
        load = float(os.getloadavg()[0])
        if not math.isnan(load) and not math.isinf(load): result['resources']['load_1m'] = load
    except (OSError, ValueError, AttributeError): pass
    try:
        with open('/proc/meminfo', 'rb') as stream: raw = stream.read(16385)
        if len(raw) <= 16384:
            for key, output in [('MemTotal', 'memory_total_bytes'), ('MemAvailable', 'memory_available_bytes')]:
                match = re.search(r'^' + key + r':\s*([0-9]+)\s+kB\s*$', raw.decode('ascii', 'replace'), re.M)
                if match: result['resources'][output] = int(match.group(1)) * 1024
    except (OSError, IOError): pass
    return result

if __name__ == '__main__':
    print(json.dumps(collect_environment(), sort_keys=True, ensure_ascii=True))
'''


def build_environment_probe(nonce):
    """Return fixed stdin for ``ssh SELECTED_ALIAS /bin/sh -s``; no execution."""
    if not isinstance(nonce, str) or re.fullmatch(r'[A-Za-z0-9]{16,64}', nonce) is None:
        raise DomainError('Use a bounded probe framing nonce.', 'environment_probe_invalid')
    begin, end = '__SPICEOWL_ENV_' + nonce + '_BEGIN__', '__SPICEOWL_ENV_' + nonce + '_END__'
    return ("LC_ALL=C; export LC_ALL\n"
        "printf '\\n%s\\n' '" + begin + "'\n"
        "swo_python=''\nfor swo_name in python3 python python2; do\n"
        "  swo_found=$(command -v \"$swo_name\" 2>/dev/null) || continue\n"
        "  [ -f \"$swo_found\" ] && [ -x \"$swo_found\" ] && { swo_python=$swo_found; break; }\n"
        "done\nif [ -n \"$swo_python\" ]; then\n"
        "  \"$swo_python\" - <<'SPICEOWL_FIXED_PYTHON'\n" + REMOTE_PYTHON + "\nSPICEOWL_FIXED_PYTHON\n"
        "  swo_status=$?\nelse\n"
        "  printf 'bootstrap\\t1\\n'\n"
        "  swo_hex() { [ ${#2} -le 4096 ] || return; printf '%s\\t' \"$1\"; printf '%s' \"$2\" | od -An -tx1 | tr -d ' \\n'; printf '\\n'; }\n"
        "  swo_hex hostname \"$(hostname 2>/dev/null)\"\n"
        "  swo_hex username \"$(id -un 2>/dev/null)\"\n"
        "  swo_hex system \"$(uname -s 2>/dev/null)\"\n  swo_hex machine \"$(uname -m 2>/dev/null)\"\n"
        "  swo_hex home \"$HOME\"\n  swo_hex shell \"$SHELL\"\n  swo_hex cwd \"$(pwd)\"\n"
        "  swo_hex path \"$PATH\"\n  swo_hex spectre \"$(command -v spectre 2>/dev/null)\"\n"
        "  swo_hex CDS_LIC_FILE \"${CDS_LIC_FILE-}\"\n  swo_hex LM_LICENSE_FILE \"${LM_LICENSE_FILE-}\"\n"
        "  printf 'cpu_count\\t%s\\n' \"$(getconf _NPROCESSORS_ONLN 2>/dev/null)\"\n"
        "  [ ! -r /proc/meminfo ] || awk '/^MemTotal:/ {printf \"memory_total_bytes\\t%.0f\\n\",$2*1024} /^MemAvailable:/ {printf \"memory_available_bytes\\t%.0f\\n\",$2*1024}' /proc/meminfo\n"
        "  swo_status=0\nfi\nprintf '%s\\n' '" + end + "'\nexit \"$swo_status\"\n")


# The caller selects one already observed script. Read and hash its bytes once,
# then source those exact bytes from stdin so a pathname replacement after the
# hash check cannot change what csh evaluates. No source pathname is shell text.
# The script is operator-selected code and can itself have side effects; the
# fixed observer invokes no simulator and does not test out a license.
SETUP_CSH_COMMAND = ('source /dev/stdin\nif ($status != 0) exit 73\n'
    '@SPICEOWL_FIXED_PYTHON@ -c \'import base64,sys; exec(compile(base64.b64decode(sys.argv[1]), "<fixed observer>", "exec"))\' @SPICEOWL_FIXED_OBSERVER@')
ACTIVATION_PYTHON = r'''
from __future__ import print_function
import base64, hashlib, json, os, pwd, re, stat, subprocess, sys

def activate_setup(selected, environment_source, csh_command):
    if not os.path.isfile('/bin/csh') or not os.access('/bin/csh', os.X_OK):
        return 72
    account = pwd.getpwuid(os.geteuid())
    expected = selected['identity']
    if (os.uname()[1] != expected['hostname'] or account.pw_name != expected['username']
            or os.geteuid() != expected['uid']):
        return 76
    try:
        fd = os.open(selected['path'], os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 65536: return 74
            raw = stream.read(65537)
    except (IOError, OSError):
        return 74
    if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != selected['sha256']:
        return 74
    metadata = {'selected_source_id': selected['source_id'], 'selected_source_sha256': selected['sha256'],
        'shell': '/bin/csh', 'source_method': 'verified_bytes_stdin', 'script_side_effects': 'not_audited',
        'direct_solver_invocations': 0, 'license_checkout_attempted_by_probe': False}
    observer = "import json\n_swo_scope={'__name__':'spiceowl_explicit_setup_observer'}\n"
    observer += 'exec(compile(' + repr(environment_source) + ", '<fixed environment observer>', 'exec'), _swo_scope)\n"
    observer += "_swo_result=_swo_scope['collect_environment']()\n"
    observer += "_swo_result['coverage'].update(scripts_evaluated=True, environment_basis='selected_observed_setup', setup_script_side_effects='not_audited')\n"
    observer += '_swo_result[\'activation\']=json.loads(' + repr(json.dumps(metadata)) + ')\n'
    observer += "for _swo_tool in _swo_result['tools']:\n if _swo_tool['kind']=='spectre': _swo_tool['basis']='selected_setup_PATH'\n"
    observer += "for _swo_license in _swo_result['licenses']:\n for _swo_evidence in _swo_license['provenance']:\n  if _swo_evidence['basis']=='observed_login_environment': _swo_evidence['basis']='selected_setup_environment'\n"
    observer += "for _swo_script in _swo_result['scripts']:\n if _swo_script['source_id']==_swo_result['activation']['selected_source_id'] and _swo_script['sha256']==_swo_result['activation']['selected_source_sha256']: _swo_script['evaluated']=True\n"
    observer += "for _swo_setup in _swo_result['setup_candidates']:\n if _swo_setup['source_id']==_swo_result['activation']['selected_source_id'] and _swo_setup['sha256']==_swo_result['activation']['selected_source_sha256']: _swo_setup['executed_by_probe']=True\n"
    observer += 'print(' + repr('\n__SPICEOWL_ENV_' + selected['nonce'] + '_BEGIN__') + ')\n'
    observer += 'print(json.dumps(_swo_result, sort_keys=True, ensure_ascii=True))\n'
    observer += 'print(' + repr('__SPICEOWL_ENV_' + selected['nonce'] + '_END__') + ')\n'
    # Freeze the observer command before source can modify csh argv or aliases.
    # Only a quoted restricted executable path and base64 are interpolated;
    # no script pathname, body, environment value or discovered command is.
    if not re.match(r'^/[A-Za-z0-9_./ +\-]{1,4095}$', sys.executable): return 75
    encoded = base64.b64encode(observer.encode('utf-8')).decode('ascii')
    command = csh_command.replace('@SPICEOWL_FIXED_PYTHON@', "'" + sys.executable + "'")
    command = command.replace('@SPICEOWL_FIXED_OBSERVER@', "'" + encoded + "'")
    process = subprocess.Popen(['/bin/csh', '-f', '-c', command], stdin=subprocess.PIPE)
    process.communicate(raw)
    return 0 if process.returncode == 0 else 73
'''


def _build_setup_probe(nonce, selected):
    build_environment_probe(nonce)  # Shared bounded framing validation.
    config = {**selected, 'nonce': nonce}
    driver = (ACTIVATION_PYTHON + '\nraise SystemExit(activate_setup(json.loads('
        + repr(json.dumps(config, ensure_ascii=True)) + '), ' + repr(REMOTE_PYTHON)
        + ', ' + repr(SETUP_CSH_COMMAND) + '))\n')
    return ("LC_ALL=C; export LC_ALL\n"
        "swo_python=''\nfor swo_name in python3 python python2; do\n"
        "  swo_found=$(command -v \"$swo_name\" 2>/dev/null) || continue\n"
        "  [ -f \"$swo_found\" ] && [ -x \"$swo_found\" ] && { swo_python=$swo_found; break; }\n"
        "done\n[ -n \"$swo_python\" ] || exit 75\n"
        "\"$swo_python\" - <<'SPICEOWL_FIXED_ACTIVATION'\n" + driver + "\nSPICEOWL_FIXED_ACTIVATION\n")


@dataclass(frozen=True)
class ProbeReply:
    returncode: int
    stdout: bytes
    error_code: str | None = None


def _transport(argv, script, *, timeout=PROBE_TIMEOUT, max_output=MAX_OUTPUT_BYTES):
    deadline = time.monotonic() + timeout
    output, hints = bytearray(), _StderrHints()
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        shell=False, close_fds=True, env={**os.environ, 'LC_ALL': 'C', 'LANG': 'C'},
        **popen_isolation_kwargs())
    try:
        status = communicate_bounded(
            process,
            deadline=deadline,
            max_output_bytes=max_output,
            stdin_bytes=script.encode('utf-8'),
            on_stdout=output.extend,
            on_stderr=hints.feed,
            chunk_size=8192,
        )
        return ProbeReply(status, bytes(output), hints.category() if status else None)
    except BoundedIOError as error:
        if error.kind == 'output_limit':
            raise DomainError('Selected-server observation exceeded its output budget.', 'environment_probe_output_limit') from None
        raise DomainError('Selected-server observation reached its deadline.', 'environment_probe_timeout') from None
    except subprocess.TimeoutExpired:
        raise DomainError('Selected-server observation reached its deadline.', 'environment_probe_timeout') from None
    finally:
        if process.poll() is None:
            terminate_process_tree(process, force=True, grace_timeout=1.0)
        for stream in (process.stdin, process.stdout, process.stderr):
            if not stream.closed: stream.close()


def _bootstrap(payload):
    fields = {}
    for line in payload.splitlines():
        name, sep, value = line.partition('\t')
        if not sep or name in fields:
            raise DomainError('Malformed bounded bootstrap observation.', 'environment_probe_protocol')
        fields[name] = value
    private = {'schema': 'spiceowl.server-environment.private.v1', 'probe_kind': 'bootstrap',
        'identity': {}, 'path': [], 'environment': [], 'tools': [], 'licenses': [], 'scripts': [],
        'source_chain': [], 'setup_candidates': [], 'pdk_candidates': [], 'library_roots': [],
        'resources': {key: None for key in ('cpu_count', 'load_1m', 'memory_total_bytes', 'memory_available_bytes')},
        'warnings': [{'code': 'python_unavailable_static_source_scan_not_performed'}],
        'coverage': {'files_read': 0, 'bytes_read': 0, 'scripts_evaluated': False, 'model_body_discovery': False}}
    def decode(name):
        value = fields.get(name, '')
        if len(value) > 8192 or re.fullmatch(r'(?:[a-fA-F0-9]{2})*', value) is None:
            raise DomainError('Malformed bounded bootstrap observation.', 'environment_probe_protocol')
        return bytes.fromhex(value).decode('utf-8', 'replace')
    for name in ('hostname', 'username', 'home', 'shell', 'cwd'):
        private['identity'][name] = decode(name)
    private['platform'] = {'system': decode('system') or None, 'machine': decode('machine') or None,
        'os_release': {'id': None, 'id_like': []}, 'os_release_status': 'unavailable',
        'basis': {'system': 'remote_uname' if fields.get('system') else 'unavailable',
                  'machine': 'remote_uname' if fields.get('machine') else 'unavailable', 'os_release': 'unavailable'}}
    private['path'] = decode('path').split(':')
    private['tools'] = [{'kind': 'spectre', 'path': decode('spectre') or None, 'version': None, 'basis': 'login_PATH', 'usable': 'unknown'},
                        {'kind': 'python', 'path': None, 'version': None, 'basis': 'bootstrap_PATH', 'usable': 'unknown'}]
    for name in LICENSE_NAMES:
        value = decode(name)
        private['licenses'].append({'name': name, 'configured': bool(value), 'value': value or None, 'usable': 'unknown',
            'declared_in_scripts': False, 'provenance': [{'basis': 'observed_login_environment'}] if value else []})
    for name in private['resources']:
        value = fields.get(name, '')
        if re.fullmatch(r'[0-9]{1,20}', value): private['resources'][name] = int(value)
    return private


def parse_environment_reply(raw, nonce):
    """Decode a bounded PRIVATE wire result. Call public_environment to present it."""
    build_environment_probe(nonce)
    if not isinstance(raw, bytes) or len(raw) > MAX_OUTPUT_BYTES:
        raise DomainError('The environment response exceeded its budget.', 'environment_probe_protocol')
    begin, end = [('__SPICEOWL_ENV_' + nonce + '_' + suffix + '__').encode() for suffix in ('BEGIN', 'END')]
    lines = raw.splitlines()
    if lines.count(begin) != 1 or lines.count(end) != 1 or lines.index(end) <= lines.index(begin):
        raise DomainError('The environment response has ambiguous framing.', 'environment_probe_protocol')
    body = b'\n'.join(lines[lines.index(begin)+1:lines.index(end)])
    if body.startswith(b'bootstrap\t1\n'):
        return _bootstrap(body.decode('ascii'))
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value: raise ValueError('duplicate')
            value[key] = item
        return value
    try:
        value = json.loads(body.decode('utf-8'), object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise DomainError('The environment response is not bounded unique JSON.', 'environment_probe_protocol') from None
    if (not isinstance(value, dict) or value.get('schema') != 'spiceowl.server-environment.private.v1'
            or value.get('probe_kind') != 'python'):
        raise DomainError('Unsupported environment observation.', 'environment_probe_protocol')
    return value


def public_platform(value):
    """Present only remote platform identifiers, with no local fallback facts."""
    value = value if isinstance(value, dict) else {}
    basis = value.get('basis') if isinstance(value.get('basis'), dict) else {}
    system = value.get('system')
    system = system if system in ('Linux', 'Darwin', 'FreeBSD', 'OpenBSD', 'NetBSD', 'SunOS', 'AIX') and basis.get('system') == 'remote_uname' else None
    machine = value.get('machine')
    machine = machine if machine in ('x86_64', 'amd64', 'aarch64', 'arm64', 'i386', 'i686', 'ppc64', 'ppc64le', 's390x', 'riscv64', 'sparc64') and basis.get('machine') == 'remote_uname' else None
    release = value.get('os_release') if isinstance(value.get('os_release'), dict) else {}
    def identifier(item):
        return item if isinstance(item, str) and re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,63}', item) else None
    observed = basis.get('os_release') == 'remote_os_release' and value.get('os_release_status') == 'observed'
    distro = identifier(release.get('id')) if observed else None
    raw_like = release.get('id_like', [])
    like = list(dict.fromkeys(raw_like)) if (observed and isinstance(raw_like, list) and len(raw_like) <= 8
        and all(identifier(item) is not None for item in raw_like)) else []
    status = value.get('os_release_status')
    return {'system': system, 'machine': machine, 'os_release': {'id': distro, 'id_like': like},
        'basis': {'system': 'remote_uname' if system else 'unavailable',
                  'machine': 'remote_uname' if machine else 'unavailable',
                  'os_release': 'remote_os_release' if distro or like else 'unavailable'},
        'os_release_status': status if status in ('observed', 'unavailable', 'invalid', 'limit') else 'unavailable'}


def public_environment(alias, private):
    """Allowlisted projection; full paths and environment/license values stay private."""
    validate_ssh_target(alias)
    if not isinstance(private, dict) or private.get('schema') != 'spiceowl.server-environment.private.v1':
        raise DomainError('Unsupported private environment evidence.', 'environment_probe_protocol')
    def items(name, limit):
        value = private.get(name, [])
        if not isinstance(value, list) or len(value) > limit:
            raise DomainError('Environment observation exceeds its bounded schema.', 'environment_probe_protocol')
        return [row for row in value if isinstance(row, dict)]
    def evidence_id(value):
        return value if isinstance(value, str) and re.fullmatch(r'source-[a-f0-9]{20}', value) else None
    tools = []
    for row in items('tools', 4):
        if row.get('kind') not in ('spectre', 'python'): continue
        path = row.get('path')
        available = isinstance(path, str) and path.startswith('/') and len(path) <= 4096
        basename = os.path.basename(path) if available else ''
        allowed_basename = basename == 'spectre' if row['kind'] == 'spectre' else bool(re.fullmatch(r'python(?:[23](?:\.[0-9]{1,2})?)?', basename))
        version = row.get('version')
        tools.append({'kind': row['kind'], 'configured': available,
            'tool_basename': basename if allowed_basename else None,
            'version': version if isinstance(version, str) and re.fullmatch(r'[0-9]{1,3}(?:\.[0-9]{1,3}){1,3}', version) else None,
            'basis': row.get('basis') if row.get('basis') in ('login_PATH', 'probe_interpreter', 'bootstrap_PATH', 'selected_setup_PATH') else 'unknown',
            'usable': 'probe_executed' if row['kind'] == 'python' and row.get('usable') == 'probe_executed' else 'unknown'})
    licenses = []
    for row in items('licenses', 2):
        if row.get('name') not in LICENSE_NAMES: continue
        provenance = []
        references = row.get('provenance', [])
        if not isinstance(references, list):
            raise DomainError('Malformed license provenance.', 'environment_probe_protocol')
        for evidence in references[:16]:
            if not isinstance(evidence, dict): continue
            basis = evidence.get('basis')
            if basis not in ('observed_login_environment', 'static_script_reference', 'selected_setup_environment'): continue
            item = {'basis': basis}
            if evidence_id(evidence.get('source_id')): item['source_id'] = evidence['source_id']
            if type(evidence.get('line')) is int and 1 <= evidence['line'] <= 65536: item['line'] = evidence['line']
            provenance.append(item)
        licenses.append({'name': row['name'], 'configured': row.get('configured') is True,
            'usable': 'unknown', 'declared_in_scripts': row.get('declared_in_scripts') is True,
            'provenance': provenance})
    resources = {}
    observed = private.get('resources', {})
    if not isinstance(observed, dict): observed = {}
    for name in ('cpu_count', 'load_1m', 'memory_total_bytes', 'memory_available_bytes'):
        value = observed.get(name)
        resources[name] = value if type(value) in (int, float) and 0 <= value <= 2**60 and math.isfinite(value) else None
    activation = private.get('activation', {})
    if not isinstance(activation, dict): activation = {}
    active_id = evidence_id(activation.get('selected_source_id'))
    activated = bool(active_id and activation.get('shell') == '/bin/csh'
                     and activation.get('source_method') == 'verified_bytes_stdin')
    setup = []
    for row in items('setup_candidates', 32):
        sid = evidence_id(row.get('source_id'))
        if sid:
            shell_kind = row.get('shell_kind') if row.get('shell_kind') in ('csh', 'posix') else 'unknown'
            path = row.get('path')
            basename = os.path.basename(path) if isinstance(path, str) and path.startswith('/') and len(path) <= 4096 else ''
            label = ''.join(char for char in basename if char.isprintable())[:240] or 'Observed setup script'
            setup.append({'source_id': sid, 'label': label,
                'basis': 'alias_source_reference' if row.get('basis') == 'alias_source_reference' else 'startup_or_source_reference',
                'shell_kind': shell_kind, 'activation_supported': shell_kind == 'csh',
                'executed_by_probe': bool(activated and sid == active_id
                    and row.get('sha256') == activation.get('selected_source_sha256'))})
    candidates = []
    for row in items('pdk_candidates', 64):
        path = row.get('path')
        if isinstance(path, str) and path.startswith('/') and len(path) <= 4096:
            candidates.append({'candidate_id': 'pdk-' + hashlib.sha256(path.encode()).hexdigest()[:20],
                'basis': row.get('basis') if row.get('basis') in ('observed_environment', 'cds_lib_definition',
                    'shallow_directory_name', 'static_script_declaration_not_effective_environment') else 'unknown',
                'exists': row.get('exists') is True, 'qualified': False})
    allowed_warnings = {'scan_time_limit', 'sensitive_source_skipped', 'model_or_design_source_skipped',
        'source_limit', 'non_regular_source', 'source_byte_limit', 'license_value_limit', 'source_line_limit',
        'library_include_unresolved', 'source_chain_limit', 'source_reference_unresolved',
        'directory_entry_limit', 'python2_additional_directory_skipped',
        'python2_directory_cap_after_os_return',
        'python_unavailable_static_source_scan_not_performed'}
    warnings = [{'code': row['code'] if isinstance(row.get('code'), str) and row['code'] in allowed_warnings else 'unclassified_observation_gap'} for row in items('warnings', 128)]
    identity = private.get('identity', {})
    if not isinstance(identity, dict): identity = {}
    coverage = private.get('coverage', {})
    if not isinstance(coverage, dict): coverage = {}
    return {'schema': 'spiceowl.server-environment.v1', 'alias': alias, 'observed_at': utcnow(),
        'state': 'partial' if warnings or private.get('probe_kind') == 'bootstrap' else 'observed',
        'transport': 'reachable', 'identity': {'observed': bool(identity.get('hostname') and identity.get('username')), 'verified': False},
        'session_kind': 'ssh_noninteractive_command', 'platform': public_platform(private.get('platform')),
        'activation': {'selected_source_id': active_id, 'shell_kind': 'csh',
            'source_method': 'verified_bytes_stdin', 'script_side_effects': 'not_audited',
            'direct_solver_invocations': 0, 'license_checkout_attempted_by_probe': False} if activated else None,
        'tools': tools, 'licenses': licenses, 'resources': resources, 'setup_candidates': setup,
        'pdk_candidates': candidates, 'library_root_count': len(items('library_roots', 64)), 'warnings': warnings,
        'coverage': {'probe_kind': private.get('probe_kind') if private.get('probe_kind') in ('python', 'bootstrap') else 'unknown',
            'files_read': coverage.get('files_read') if type(coverage.get('files_read')) is int else None,
            'bytes_read': coverage.get('bytes_read') if type(coverage.get('bytes_read')) is int else None,
            'directory_listing_method': coverage.get('directory_listing_method') if coverage.get('directory_listing_method') in ('bounded_scandir', 'fixed_root_listdir') else None,
            'directory_cap_after_os_return': coverage.get('directory_cap_after_os_return') is True,
            'scripts_evaluated': activated, 'model_body_discovery': False,
            'setup_script_side_effects': 'not_audited' if activated else 'not_evaluated'},
        'simulation_started': None if activated else False, 'simulator_invoked_by_probe': False}


def capture_server_environment(alias, ssh_config_path, *, transport=None, user_home=None):
    """Backend-only observation returning ``{public, private}``; never print private."""
    alias = validate_ssh_target(alias)
    path = config_path(ssh_config_path)
    discovery = discover_ssh_hosts(path, user_home=user_home)
    discovered = alias in {row['alias'] for row in discovery['hosts']}
    nonce = secrets.token_hex(16)
    ssh_exe = resolve_ssh_executable()
    argv = [ssh_exe, '-T', '-F', str(path)]
    for option in (*ssh_client_options(_SSH_OPTIONS, executable=ssh_exe), 'StdinNull=no'):
        argv.extend(('-o', option))
    argv.extend(('--', alias, '/bin/sh -s'))
    try:
        reply = (transport or _transport)(argv, build_environment_probe(nonce), timeout=PROBE_TIMEOUT, max_output=MAX_OUTPUT_BYTES)
        if type(reply.returncode) is not int or reply.returncode != 0:
            code = reply.error_code if getattr(reply, 'error_code', None) in _SSH_ERROR_MESSAGES else 'environment_probe_failed'
            raise DomainError('The selected server did not return a completed environment observation.', code)
        private = parse_environment_reply(reply.stdout, nonce)
        private['capture_binding'] = {'target': alias, 'ssh_config_path': str(path),
            'ssh_configuration_sha256': discovery['configuration_sha256'], 'probe_id': nonce}
        public = public_environment(alias, private)
        public['selection_basis'] = 'discovered_alias' if discovered else 'explicit_target'
        return {'public': public, 'private': private}
    except (OSError, DomainError, ValueError, TypeError, AttributeError) as error:
        allowed = set(_SSH_ERROR_MESSAGES) | {'environment_probe_timeout', 'environment_probe_output_limit', 'environment_probe_protocol', 'environment_probe_failed'}
        code = error.code if isinstance(error, DomainError) and error.code in allowed else 'environment_probe_failed'
        return {'public': {'schema': 'spiceowl.server-environment.v1', 'alias': alias, 'observed_at': utcnow(),
            'state': 'unavailable', 'transport': 'unknown', 'error': {'code': code,
                'message': 'The selected server environment could not be confirmed; no simulator was started.'},
            'simulation_started': False}, 'private': None}


def probe_server_environment(alias, ssh_config_path, *, transport=None, user_home=None):
    """Explicit active SSH observation; returns only presentation-safe metadata."""
    return capture_server_environment(alias, ssh_config_path, transport=transport, user_home=user_home)['public']


def activate_observed_environment(target, ssh_config_path, capture, setup_source_id, *, transport=None, user_home=None):
    """Explicitly source one fresh-capture csh candidate and reobserve its environment.

    The caller must obtain ``capture`` from this backend, keep its private half
    private, and explicitly select the source ID. This is code evaluation: a
    selected setup script can have side effects beyond environment changes.
    No retry, simulator invocation or license checkout is added by this probe.
    """
    target = validate_ssh_target(target)
    failure = {'schema': 'spiceowl.server-environment.v1', 'alias': target, 'observed_at': utcnow(),
        'state': 'missing', 'transport': 'unknown', 'simulation_started': False,
        'simulator_invoked_by_probe': False, 'coverage': {'scripts_evaluated': False}}
    attempted = False
    try:
        path = config_path(ssh_config_path)
        discovery = discover_ssh_hosts(path, user_home=user_home)
        if not isinstance(capture, dict) or not isinstance(capture.get('private'), dict) or not isinstance(capture.get('public'), dict):
            raise DomainError('A fresh private server capture is required.', 'setup_capture_missing')
        private, public = capture['private'], capture['public']
        binding = private.get('capture_binding', {})
        if (not isinstance(binding, dict) or binding.get('target') != target or public.get('alias') != target
                or binding.get('ssh_config_path') != str(path)
                or binding.get('ssh_configuration_sha256') != discovery['configuration_sha256']
                or not isinstance(binding.get('probe_id'), str)
                or re.fullmatch(r'[a-f0-9]{32}', binding['probe_id']) is None
                or private.get('schema') != 'spiceowl.server-environment.private.v1'
                or private.get('probe_kind') != 'python' or public.get('transport') != 'reachable'
                or private.get('activation') is not None):
            raise DomainError('Select a script from the original capture of this exact SSH target and configuration.', 'setup_capture_mismatch')
        if not isinstance(setup_source_id, str) or re.fullmatch(r'source-[a-f0-9]{20}', setup_source_id) is None:
            raise DomainError('Select one observed setup source ID.', 'setup_selection_missing')
        candidates, scripts = private.get('setup_candidates'), private.get('scripts')
        if not isinstance(candidates, list) or len(candidates) > 32 or not isinstance(scripts, list) or len(scripts) > 32:
            raise DomainError('The captured setup evidence is malformed.', 'setup_evidence_invalid')
        selected = [row for row in candidates if isinstance(row, dict) and row.get('source_id') == setup_source_id]
        records = [row for row in scripts if isinstance(row, dict) and row.get('source_id') == setup_source_id]
        if len(selected) != 1 or len(records) != 1:
            raise DomainError('The setup source must identify one observed candidate and source record.', 'setup_selection_missing')
        row, record = selected[0], records[0]
        source_path, expected_hash = row.get('path'), row.get('sha256')
        if (not isinstance(source_path, str) or not source_path.startswith('/') or len(source_path) > 4096
                or any(ord(char) < 32 for char in source_path)
                or not isinstance(expected_hash, str) or re.fullmatch(r'[a-f0-9]{64}', expected_hash) is None
                or record.get('path') != source_path or record.get('sha256') != expected_hash
                or 'source-' + hashlib.sha256(source_path.encode()).hexdigest()[:20] != setup_source_id
                or type(record.get('bytes')) is not int or not 0 <= record['bytes'] <= 65536):
            raise DomainError('The setup source needs consistent observed path and SHA-256 evidence.', 'setup_evidence_invalid')
        if row.get('shell_kind') != 'csh' or row.get('shell_basis') not in ('filename', 'shebang', 'observed_statements'):
            raise DomainError('Only an observed csh-compatible setup script is supported by this activation.', 'setup_shell_unsupported')
        identity = private.get('identity', {})
        if (not isinstance(identity, dict) or type(identity.get('uid')) is not int or not 0 <= identity['uid'] <= 2**32
                or any(not isinstance(identity.get(name), str) or not 1 <= len(identity[name]) <= 256
                       for name in ('hostname', 'username'))):
            raise DomainError('The capture needs an observed server account identity.', 'setup_evidence_invalid')
        nonce = secrets.token_hex(16)
        selected_input = {'source_id': setup_source_id, 'path': source_path, 'sha256': expected_hash,
            'identity': {key: identity[key] for key in ('hostname', 'username', 'uid')}}
        ssh_exe = resolve_ssh_executable()
        argv = [ssh_exe, '-T', '-F', str(path)]
        for option in (*ssh_client_options(_SSH_OPTIONS, executable=ssh_exe), 'StdinNull=no'): argv.extend(('-o', option))
        argv.extend(('--', target, '/bin/sh -s'))
        attempted = True
        reply = (transport or _transport)(argv, _build_setup_probe(nonce, selected_input),
            timeout=PROBE_TIMEOUT, max_output=MAX_OUTPUT_BYTES)
        failures = {72: 'setup_shell_unavailable', 73: 'setup_activation_failed', 74: 'setup_source_changed',
                    75: 'setup_python_unavailable', 76: 'setup_target_mismatch'}
        if type(reply.returncode) is not int or reply.returncode != 0:
            code = failures.get(reply.returncode, getattr(reply, 'error_code', None))
            if code not in set(failures.values()) | set(_SSH_ERROR_MESSAGES): code = 'setup_activation_failed'
            raise DomainError('The selected setup could not be activated and observed.', code)
        observed = parse_environment_reply(reply.stdout, nonce)
        activation = observed.get('activation', {})
        if (not isinstance(activation, dict) or activation.get('selected_source_id') != setup_source_id
                or activation.get('selected_source_sha256') != expected_hash or activation.get('shell') != '/bin/csh'
                or activation.get('source_method') != 'verified_bytes_stdin'
                or observed.get('coverage', {}).get('scripts_evaluated') is not True):
            raise DomainError('The setup result does not match the explicitly selected source.', 'environment_probe_protocol')
        observed['capture_binding'] = {**binding, 'activation_probe_id': nonce, 'parent_probe_id': binding['probe_id']}
        activation['parent_probe_id'] = binding['probe_id']
        result = public_environment(target, observed)
        result['selection_basis'] = 'explicit_observed_setup'
        return {'public': result, 'private': observed}
    except (OSError, DomainError, ValueError, TypeError, AttributeError, KeyError) as error:
        allowed = set(_SSH_ERROR_MESSAGES) | {'setup_capture_missing', 'setup_capture_mismatch', 'setup_selection_missing',
            'setup_evidence_invalid', 'setup_shell_unsupported', 'setup_shell_unavailable', 'setup_activation_failed',
            'setup_source_changed', 'setup_python_unavailable', 'setup_target_mismatch', 'environment_probe_timeout',
            'environment_probe_output_limit', 'environment_probe_protocol'}
        code = error.code if isinstance(error, DomainError) and error.code in allowed else 'setup_activation_failed'
        failure['error'] = {'code': code, 'message': 'The selected observed setup could not be confirmed. Recheck the capture and selection.'}
        if attempted:
            failure.update(state='unavailable', simulation_started=None)
            failure['coverage'].update(scripts_evaluated=None, setup_script_side_effects='unknown')
        return {'public': failure, 'private': None}

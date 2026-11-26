"""Bounded, read-only remote SSH Library/Netlist browser, Virtuoso schematic exporter & CircuitGit entry contract.

Reuses CircuitGit's ``ic.onboarding.ssh-browse.v1`` and ``ic.onboarding.ssh-import.v1``
interfaces verbatim, and extends them cleanly with:
- Automatic detection of Cadence Virtuoso OpenAccess ``schematic`` views (``sch.oa``)
  and sibling ADE/Maestro simulation setups (``maestro/``, ``adexl/``, ``spectre_state*``).
- Headless remote Spectre netlist export (``ic.onboarding.ssh-schematic-export.v1``)
  via Cadence ``si -batch -command nl`` inside an isolated temporary directory
  (read-only against the user's Virtuoso cell).
- Optional Maestro / ADE simulation configuration parsing & merging (analyses,
  model libraries, design variables, simulator options, corners, saved outputs).
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
SCHEMATIC_EXPORT_SCHEMA = "ic.onboarding.ssh-schematic-export.v1"
CIRCUITGIT_ENTRY_SCHEMA = "spiceowl.circuitgit-entry.v1"

MAX_BROWSE_ENTRIES = 200
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_IMPORT_BYTES = 1400 * 1024
MAX_SUPPORT_FILES = 48
MAX_INCLUDE_DEPTH = 4
SSH_TIMEOUT_SECONDS = 25
SCHEMATIC_EXPORT_TIMEOUT_SECONDS = 90
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


# Python 2.7 + Python 3.x compatible remote helper (many RHEL7 EDA nodes ship Python 2.7 only).
_REMOTE_PY = r"""
from __future__ import print_function
import base64
import io
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

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
CDS_INCLUDE_RE = re.compile(r'^\s*(?:SOFT)?INCLUDE\s+(.+?)\s*$', re.IGNORECASE)


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
    return raw.decode('utf-8', 'replace')


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


def find_cds_lib_for_library(lib_dir, override_cds_lib=None):
    '''Locate the cds.lib defining lib_dir (or nearest ancestor cds.lib).'''
    lib_real = os.path.realpath(lib_dir)
    lib_base = os.path.basename(lib_real)
    candidates = []
    if override_cds_lib:
        p = resolve_user_path(override_cds_lib)
        if os.path.isdir(p):
            p = os.path.join(p, 'cds.lib')
        if os.path.isfile(p):
            candidates.append(p)
    cur = lib_real
    for _ in range(5):
        cand = os.path.join(cur, 'cds.lib')
        if os.path.isfile(cand) and cand not in candidates:
            candidates.append(cand)
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    home_cds = os.path.join(os.path.expanduser('~'), 'cds.lib')
    if os.path.isfile(home_cds) and home_cds not in candidates:
        candidates.append(home_cds)

    # Prefer the first cds.lib that explicitly defines this library directory.
    for cand in candidates:
        libs = parse_cds_lib(os.path.dirname(cand))
        for item in libs:
            if os.path.realpath(item['path']) == lib_real:
                return cand, item['name'], True
    for cand in candidates:
        libs = parse_cds_lib(os.path.dirname(cand))
        for item in libs:
            if item['name'] == lib_base:
                return cand, item['name'], True
    if candidates:
        return candidates[0], lib_base, False
    return None, lib_base, False


def is_schematic_dir(path):
    return os.path.isdir(path) and (
        os.path.isfile(os.path.join(path, 'sch.oa'))
        or os.path.isfile(os.path.join(path, 'sch.cdb'))
    )


def detect_maestro_view_kind(view_dir, view_name):
    if not os.path.isdir(view_dir):
        return None
    if os.path.isfile(os.path.join(view_dir, 'maestro.sdb')) or os.path.isfile(os.path.join(view_dir, 'active.state')):
        return 'maestro'
    if os.path.isfile(os.path.join(view_dir, 'data.sdb')):
        return 'adexl'
    low = view_name.lower()
    if ('spectre_state' in low or 'ade' in low) and (
        os.path.isfile(os.path.join(view_dir, 'analyses_state'))
        or os.path.isfile(os.path.join(view_dir, 'variables_state'))
        or os.path.isfile(os.path.join(view_dir, 'ADE_state.info'))
    ):
        return 'ade_state'
    return None


def scan_cell_views(cell_dir):
    '''Return schematic info and any maestro / ADE setup views inside a Virtuoso cell directory.'''
    if not os.path.isdir(cell_dir):
        return None, [], []
    try:
        names = sorted(os.listdir(cell_dir))
    except OSError:
        return None, [], []
    sch_dir = None
    maestro_views = []
    other_views = []
    for name in names:
        if sensitive_name(name):
            continue
        full = os.path.join(cell_dir, name)
        if not os.path.isdir(full):
            continue
        if is_schematic_dir(full):
            if sch_dir is None or name == 'schematic':
                sch_dir = full
            other_views.append(name)
            continue
        mkind = detect_maestro_view_kind(full, name)
        if mkind:
            maestro_views.append({'name': name, 'kind': mkind, 'path': full})
            other_views.append(name)
            continue
        if (
            os.path.isfile(os.path.join(full, 'symbol.oa'))
            or os.path.isfile(os.path.join(full, 'veriloga.va'))
            or os.path.isfile(os.path.join(full, 'layout.oa'))
            or os.path.isfile(os.path.join(full, 'expand.cfg'))
            or os.path.isfile(os.path.join(full, 'master.tag'))
        ):
            other_views.append(name)
    # Put 'maestro' first
    maestro_views.sort(key=lambda x: (0 if x['name'] == 'maestro' else 1, x['name'].lower()))
    return sch_dir, maestro_views, other_views


def _unquote(val):
    s = (val or '').strip()
    if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
        return s[1:-1]
    return s


def _is_skill_true(val):
    s = (val or '').strip().lower()
    return s in ('t', '(t)', 'true', '1', '"t"', 'yes', '"yes"')


def _parse_skill_model_pairs(raw):
    '''Parse SKILL list like (("/path/model.scs" "tt") ("/path/other.scs" "")) -> list of {path, section}.'''
    out = []
    if not raw:
        return out
    for m in re.finditer(r'\(\s*"([^"]+)"\s+(?:"([^"]*)"|nil)\s*\)', raw):
        mpath = m.group(1).strip()
        sec = (m.group(2) or '').strip()
        if mpath and not sensitive_path(mpath):
            out.append({'path': mpath, 'section': sec})
    return out


def _first_scalar_value(expr):
    '''If Maestro var is a sweep list like '1m 30m 50m', return the first clean token as default.'''
    s = (expr or '').strip()
    if not s:
        return ''
    # Keep simple quoted strings or parenthesized expressions intact
    if s.startswith('(') or s.startswith('"') or '{' in s or ':' in s:
        parts = s.split()
        return parts[0] if len(parts) > 1 and not s.startswith('(') else s
    parts = s.split()
    return parts[0] if parts else s


def _extract_state_xml(state_file):
    '''Parse Cadence <statedb> XML (maestro/active.state or test_states/*.state).'''
    info = {
        'simulator': 'spectre',
        'analyses': [],
        'variables': {},
        'model_files': [],
        'options': {},
        'temp': None,
        'saved_signals': [],
        'output_expressions': [],
        'test_name': None,
    }
    try:
        raw = read_utf8(state_file, 512 * 1024)
        root = ET.fromstring(raw.encode('utf-8'))
    except Exception:
        return info

    test_el = root.find('.//Test')
    if test_el is None:
        return info
    info['test_name'] = test_el.attrib.get('Name') or None

    for comp in test_el.findall('component'):
        cname = comp.attrib.get('Name') or ''
        if cname == 'adeInfo':
            for f in comp.findall('field'):
                if f.attrib.get('Name') == 'designInfo':
                    toks = re.findall(r'"([^"]+)"', f.text or '')
                    if len(toks) >= 4 and toks[3]:
                        info['simulator'] = toks[3].lower()
        elif cname == 'analyses':
            for an in comp.findall('.//analysis'):
                atype = (an.attrib.get('Name') or '').strip().lower()
                if not atype:
                    continue
                fields = {}
                for part in an.findall('partition'):
                    if part.attrib.get('Name') == 'fields':
                        for f in part.findall('field'):
                            fn = f.attrib.get('Name') or ''
                            fv = (f.text or '').strip()
                            if fn and fv and fv not in ('""', 'nil'):
                                fields[fn] = fv
                if not _is_skill_true(fields.get('enable', '')):
                    continue
                row = {'type': atype}
                if atype == 'tran':
                    for k in ('stop', 'start', 'step', 'maxstep', 'errpreset'):
                        if k in fields:
                            row[k] = _unquote(fields[k])
                elif atype == 'dc':
                    row['saveOppoint'] = _is_skill_true(fields.get('saveOppoint', 't'))
                    for k in ('param', 'start', 'stop', 'lin', 'dec', 'step', 'dev'):
                        if k in fields:
                            row[k] = _unquote(fields[k])
                elif atype in ('ac', 'stb', 'noise', 'sp'):
                    for k in ('start', 'stop', 'dec', 'lin', 'step', 'freq', 'probe', 'oprobe', 'iprobe'):
                        if k in fields:
                            row[k] = _unquote(fields[k])
                elif atype in ('pss', 'pac', 'pnoise'):
                    for k in ('fund', 'harms', 'errpreset', 'tstab', 'start', 'stop'):
                        if k in fields:
                            row[k] = _unquote(fields[k])
                info['analyses'].append(row)
        elif cname == 'modelSetup':
            for f in comp.findall('.//field'):
                if f.attrib.get('Name') == 'modelFiles':
                    info['model_files'].extend(_parse_skill_model_pairs(f.text or ''))
        elif cname == 'simulatorOptions':
            wanted = ('temp', 'tnom', 'reltol', 'vabstol', 'iabstol', 'gmin', 'scale', 'scalem', 'homotopy', 'save')
            for f in comp.findall('.//field'):
                fn = f.attrib.get('Name') or ''
                if fn in wanted:
                    val = _unquote(f.text or '')
                    if val and val != 'nil':
                        if fn == 'temp':
                            info['temp'] = val
                        else:
                            info['options'][fn] = val
        elif cname == 'variables':
            for f in comp.findall('.//field[@Type="defstruct"]'):
                vname = ''
                vexpr = ''
                for sub in f.findall('field'):
                    sn = sub.attrib.get('Name')
                    if sn == 'name':
                        vname = _unquote(sub.text or '')
                    elif sn == 'expression':
                        vexpr = _unquote(sub.text or '')
                if vname and vexpr:
                    info['variables'][vname] = vexpr
        elif cname == 'outputs':
            for f in comp.findall('.//field[@Type="defstruct"]'):
                sig = ''
                sig_type = ''
                expr = ''
                oname = ''
                save_flag = ''
                plot_flag = ''
                for sub in f.findall('field'):
                    sn = sub.attrib.get('Name')
                    sv = (sub.text or '').strip()
                    if sn == 'signal':
                        sig = _unquote(sv) if sv != 'nil' else ''
                    elif sn == 'type':
                        sig_type = _unquote(sv).lower() if sv != 'nil' else ''
                    elif sn == 'expression':
                        expr = sv if sv != 'nil' else ''
                    elif sn == 'name':
                        oname = _unquote(sv) if sv != 'nil' else ''
                    elif sn == 'save':
                        save_flag = sv
                    elif sn == 'plot':
                        plot_flag = sv
                if sig and (_is_skill_true(save_flag) or _is_skill_true(plot_flag)):
                    parts = [p for p in sig.strip('/').split('/') if p]
                    if sig_type == 'terminal' and len(parts) >= 2:
                        clean_sig = '.'.join(parts[:-1]) + ':' + parts[-1]
                    else:
                        clean_sig = '.'.join(parts)
                    if clean_sig and clean_sig not in info['saved_signals'] and len(info['saved_signals']) < 64:
                        info['saved_signals'].append(clean_sig)
                elif expr and len(info['output_expressions']) < 32:
                    info['output_expressions'].append({'name': oname or expr[:40], 'expression': expr})
    return info


def parse_maestro_setup(view_dir, view_name='maestro', selected_test=None):
    '''Parse a Cadence Maestro / ADE XL / ADE L setup directory into structured simulation config.'''
    result = {
        'view_name': view_name,
        'path': view_dir,
        'simulator': 'spectre',
        'tests': [],
        'selected_test': None,
        'analyses': [],
        'variables': {},
        'sweep_variables': {},
        'model_files': [],
        'corners': [],
        'options': {},
        'temp': None,
        'saved_signals': [],
        'output_expressions': [],
    }
    sdb_path = None
    for cand in ('maestro.sdb', 'data.sdb'):
        p = os.path.join(view_dir, cand)
        if os.path.isfile(p):
            sdb_path = p
            break

    state_candidates = []
    active_state = os.path.join(view_dir, 'active.state')
    if os.path.isfile(active_state):
        state_candidates.append(active_state)
    ts_dir = os.path.join(view_dir, 'test_states')
    if os.path.isdir(ts_dir):
        try:
            st_files = [
                os.path.join(ts_dir, n)
                for n in os.listdir(ts_dir)
                if n.endswith('.state') and os.path.isfile(os.path.join(ts_dir, n))
            ]
            st_files.sort(key=lambda fp: os.path.getmtime(fp), reverse=True)
            state_candidates.extend(st_files[:3])
        except OSError:
            pass

    if state_candidates:
        st_info = _extract_state_xml(state_candidates[0])
        result['simulator'] = st_info['simulator']
        result['analyses'] = st_info['analyses']
        result['variables'].update(st_info['variables'])
        result['model_files'].extend(st_info['model_files'])
        result['options'].update(st_info['options'])
        result['temp'] = st_info['temp']
        result['saved_signals'].extend(st_info['saved_signals'])
        result['output_expressions'].extend(st_info['output_expressions'])
        if st_info['test_name']:
            result['selected_test'] = st_info['test_name']

    if sdb_path:
        try:
            raw_sdb = read_utf8(sdb_path, 512 * 1024)
            sdb_root = ET.fromstring(raw_sdb.encode('utf-8'))
            active = sdb_root.find('active')
            if active is not None:
                # Parse corners
                for corner_el in active.findall('./corners/corner'):
                    cname = (corner_el.text or '').strip()
                    if not cname or cname == '_default':
                        continue
                    enabled = corner_el.attrib.get('enabled') == '1'
                    cvars = {}
                    for v in corner_el.findall('./vars/var'):
                        vn = (v.text or '').strip()
                        val_el = v.find('value')
                        vv = (val_el.text or '').strip() if val_el is not None else ''
                        if vn and vv:
                            cvars[vn] = vv
                    cmodels = []
                    for m_el in corner_el.findall('./models/model'):
                        if m_el.attrib.get('enabled') == '0':
                            continue
                        mf_el = m_el.find('modelfile')
                        ms_el = m_el.find('modelsection')
                        mf = (mf_el.text or '').strip() if mf_el is not None else ''
                        ms = (ms_el.text or '').strip() if ms_el is not None else ''
                        if mf and not sensitive_path(mf):
                            secs = re.findall(r'"([^"]+)"', ms) or ([ms] if ms else [''])
                            for s in secs:
                                cmodels.append({'path': mf, 'section': s.strip()})
                    result['corners'].append({
                        'name': cname,
                        'enabled': enabled,
                        'temperature': cvars.get('temperature'),
                        'vars': cvars,
                        'models': cmodels,
                    })

                # Parse tests
                tests = active.findall('./tests/test')
                chosen_el = None
                for t_el in tests:
                    tname = (t_el.text or '').strip()
                    ten = t_el.attrib.get('enabled', '1') == '1'
                    tmeta = {'name': tname, 'enabled': ten}
                    for opt in t_el.findall('./tooloptions/option'):
                        on = (opt.text or '').strip()
                        ov_el = opt.find('value')
                        ov = (ov_el.text or '').strip() if ov_el is not None else ''
                        if on in ('lib', 'cell', 'view', 'sim'):
                            tmeta[on] = ov
                    result['tests'].append(tmeta)
                    if selected_test and tname == selected_test:
                        chosen_el = t_el
                    elif chosen_el is None and ten:
                        chosen_el = t_el
                if chosen_el is None and tests:
                    chosen_el = tests[0]
                if chosen_el is not None:
                    result['selected_test'] = (chosen_el.text or '').strip() or result['selected_test']
                    for opt in chosen_el.findall('./tooloptions/option'):
                        if (opt.text or '').strip() == 'sim':
                            ov_el = opt.find('value')
                            if ov_el is not None and (ov_el.text or '').strip():
                                result['simulator'] = (ov_el.text or '').strip().lower()
                    for v in chosen_el.findall('./vars/var'):
                        vn = (v.text or '').strip()
                        val_el = v.find('value')
                        vv = (val_el.text or '').strip() if val_el is not None else ''
                        if vn and vv:
                            if len(vv.split()) > 1 and not vv.startswith('('):
                                result['sweep_variables'][vn] = vv
                                result['variables'][vn] = _first_scalar_value(vv)
                            else:
                                result['variables'][vn] = vv
                    if not result['output_expressions']:
                        for out_el in chosen_el.findall('./outputs/output'):
                            expr = (out_el.text or '').strip()
                            if expr and len(result['output_expressions']) < 32:
                                result['output_expressions'].append({'name': expr[:40], 'expression': expr})
        except Exception:
            pass

    # Deduplicate model files
    dedup = []
    seen_mf = set()
    for item in result['model_files']:
        key = (item.get('path', ''), item.get('section', ''))
        if key[0] and key not in seen_mf:
            seen_mf.add(key)
            dedup.append({'path': key[0], 'section': key[1]})
    # If modelSetup was empty, inherit from first enabled corner
    if not dedup:
        for c in result['corners']:
            if c.get('enabled') and c.get('models'):
                for item in c['models']:
                    key = (item.get('path', ''), item.get('section', ''))
                    if key[0] and key not in seen_mf:
                        seen_mf.add(key)
                        dedup.append({'path': key[0], 'section': key[1]})
                break
    result['model_files'] = dedup
    return result


def build_maestro_spectre_blocks(mcfg):
    '''Build Spectre header (models + parameters) and footer (options + analyses + save) from Maestro config.'''
    head_lines = []
    foot_lines = []
    models = mcfg.get('model_files') or []
    if models:
        head_lines.append('// --- Imported from Virtuoso %s: Model Libraries ---' % (mcfg.get('view_name') or 'maestro'))
        for m in models:
            sec = (m.get('section') or '').strip()
            if sec:
                head_lines.append('include "%s" section=%s' % (m['path'], sec))
            else:
                head_lines.append('include "%s"' % m['path'])
    variables = mcfg.get('variables') or {}
    sweeps = mcfg.get('sweep_variables') or {}
    if variables:
        parts = []
        for k in sorted(variables.keys()):
            v = str(variables[k]).strip()
            if k and v and re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', k):
                parts.append('%s=%s' % (k, v))
        if parts:
            head_lines.append('')
            head_lines.append('// --- Imported Design Variables ---')
            if sweeps:
                for sk in sorted(sweeps.keys()):
                    head_lines.append('// Maestro sweep %s: %s (using first value %s)' % (sk, sweeps[sk], variables.get(sk, '')))
            head_lines.append('parameters ' + ' '.join(parts))

    opts = dict(mcfg.get('options') or {})
    temp = mcfg.get('temp')
    if temp:
        opts['temp'] = temp
    opt_parts = []
    for k in ('reltol', 'vabstol', 'iabstol', 'temp', 'tnom', 'scalem', 'scale', 'gmin'):
        if opts.get(k):
            opt_parts.append('%s=%s' % (k, opts[k]))
    if opt_parts:
        foot_lines.append('// --- Imported Simulator Options ---')
        foot_lines.append('simulatorOptions options ' + ' '.join(opt_parts))

    analyses = mcfg.get('analyses') or []
    if analyses:
        foot_lines.append('')
        foot_lines.append('// --- Imported Analyses ---')
        for idx, an in enumerate(analyses):
            atype = an.get('type')
            if atype == 'tran':
                stop = an.get('stop') or '100n'
                extra = []
                if an.get('start'):
                    extra.append('start=%s' % an['start'])
                if an.get('step'):
                    extra.append('step=%s' % an['step'])
                if an.get('maxstep'):
                    extra.append('maxstep=%s' % an['maxstep'])
                if an.get('errpreset'):
                    extra.append('errpreset=%s' % an['errpreset'])
                name = 'tran' if idx == 0 else ('tran_%d' % idx)
                foot_lines.append('%s tran stop=%s %s write="spectre.ic" writefinal="spectre.fc" annotate=status maxiters=5' % (
                    name, stop, ' '.join(extra)
                ))
            elif atype == 'dc':
                if an.get('saveOppoint', True):
                    foot_lines.append('dcOp dc write="spectre.dc" maxiters=150 maxsteps=10000 annotate=status')
                    foot_lines.append('dcOpInfo info what=oppoint where=rawfile')
                if an.get('param') and an.get('start') and an.get('stop'):
                    foot_lines.append('dcSweep dc param=%s start=%s stop=%s' % (an['param'], an['start'], an['stop']))
            elif atype == 'ac':
                if an.get('start') and an.get('stop'):
                    step = ('dec=%s' % an['dec']) if an.get('dec') else (('lin=%s' % an['lin']) if an.get('lin') else 'dec=20')
                    foot_lines.append('ac ac start=%s stop=%s %s annotate=status' % (an['start'], an['stop'], step))
            elif atype == 'stb':
                if an.get('probe') and an.get('start') and an.get('stop'):
                    step = ('dec=%s' % an['dec']) if an.get('dec') else 'dec=20'
                    foot_lines.append('stb stb start=%s stop=%s %s probe=%s annotate=status' % (an['start'], an['stop'], step, an['probe']))
            elif atype == 'noise':
                if an.get('start') and an.get('stop'):
                    step = ('dec=%s' % an['dec']) if an.get('dec') else 'dec=20'
                    foot_lines.append('// noise start=%s stop=%s %s' % (an['start'], an['stop'], step))

    saved = mcfg.get('saved_signals') or []
    if saved:
        foot_lines.append('')
        foot_lines.append('save ' + ' '.join(saved[:32]))
    foot_lines.append('saveOptions options save=allpub')
    return '\n'.join(head_lines).strip(), '\n'.join(foot_lines).strip()


def build_virtuoso_context(target_dir):
    '''If target_dir is a Virtuoso schematic view OR a cell directory containing a schematic view, describe it.'''
    if is_schematic_dir(target_dir):
        cell_dir = os.path.dirname(target_dir)
        lib_dir = os.path.dirname(cell_dir)
        view_name = os.path.basename(target_dir)
        cell_name = os.path.basename(cell_dir)
        cds_lib, lib_name, _ = find_cds_lib_for_library(lib_dir)
        _, mviews, all_views = scan_cell_views(cell_dir)
        enriched = []
        for mv in mviews:
            summary = parse_maestro_setup(mv['path'], mv['name'])
            enriched.append({
                'name': mv['name'],
                'kind': mv['kind'],
                'path': mv['path'],
                'summary': {
                    'simulator': summary['simulator'],
                    'tests': [t['name'] for t in summary['tests']],
                    'selected_test': summary['selected_test'],
                    'analyses': summary['analyses'],
                    'variables_count': len(summary['variables']),
                    'variables': summary['variables'],
                    'model_files_count': len(summary['model_files']),
                    'corners': [{'name': c['name'], 'enabled': c['enabled'], 'temperature': c.get('temperature')} for c in summary['corners']],
                    'outputs_count': len(summary['output_expressions']) + len(summary['saved_signals']),
                    'temp': summary['temp'],
                },
            })
        return {
            'is_schematic_view': True,
            'is_cell_dir': False,
            'lib_name': lib_name,
            'cell_name': cell_name,
            'view_name': view_name,
            'schematic_dir': target_dir,
            'cell_dir': cell_dir,
            'cds_lib': cds_lib,
            'views': all_views,
            'maestro_views': enriched,
        }

    sch_dir, mviews, all_views = scan_cell_views(target_dir)
    if sch_dir:
        lib_dir = os.path.dirname(target_dir)
        cell_name = os.path.basename(target_dir)
        cds_lib, lib_name, _ = find_cds_lib_for_library(lib_dir)
        enriched = []
        for mv in mviews:
            summary = parse_maestro_setup(mv['path'], mv['name'])
            enriched.append({
                'name': mv['name'],
                'kind': mv['kind'],
                'path': mv['path'],
                'summary': {
                    'simulator': summary['simulator'],
                    'tests': [t['name'] for t in summary['tests']],
                    'selected_test': summary['selected_test'],
                    'analyses': summary['analyses'],
                    'variables_count': len(summary['variables']),
                    'variables': summary['variables'],
                    'model_files_count': len(summary['model_files']),
                    'corners': [{'name': c['name'], 'enabled': c['enabled'], 'temperature': c.get('temperature')} for c in summary['corners']],
                    'outputs_count': len(summary['output_expressions']) + len(summary['saved_signals']),
                    'temp': summary['temp'],
                },
            })
        return {
            'is_schematic_view': False,
            'is_cell_dir': True,
            'lib_name': lib_name,
            'cell_name': cell_name,
            'view_name': os.path.basename(sch_dir),
            'schematic_dir': sch_dir,
            'cell_dir': target_dir,
            'cds_lib': cds_lib,
            'views': all_views,
            'maestro_views': enriched,
        }
    return None


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
            row = {'name': name, 'path': full, 'kind': 'dir', 'size': None}
            if is_schematic_dir(full):
                row['virtuoso_kind'] = 'schematic_view'
                row['has_schematic'] = True
                row['schematic_path'] = full
            else:
                mkind = detect_maestro_view_kind(full, name)
                if mkind:
                    row['virtuoso_kind'] = 'maestro_view'
                    row['maestro_kind'] = mkind
                else:
                    # Lightweight check if this subdirectory is a Virtuoso cell containing a schematic
                    sch_sub = os.path.join(full, 'schematic')
                    if is_schematic_dir(sch_sub):
                        row['virtuoso_kind'] = 'cell'
                        row['has_schematic'] = True
                        row['schematic_path'] = sch_sub
                        m_names = []
                        try:
                            for sub_n in sorted(os.listdir(full)):
                                if detect_maestro_view_kind(os.path.join(full, sub_n), sub_n):
                                    m_names.append(sub_n)
                        except OSError:
                            pass
                        if m_names:
                            row['maestro_views'] = m_names
            dirs.append(row)
        elif os.path.isfile(full) and circuit_file(name):
            files.append({'name': name, 'path': full, 'kind': 'file', 'size': int(st.st_size)})
    dirs.sort(key=lambda row: (0 if row.get('has_schematic') else 1, row['name'].lower()))

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
    vctx = build_virtuoso_context(target_dir)
    return {
        'ok': True,
        'cwd': target_dir,
        'parent': parent,
        'entries': combined[:limit],
        'total': len(combined),
        'truncated': len(combined) > limit,
        'cds_libraries': cds_libs,
        'netlist_count': len(files),
        'virtuoso_context': vctx,
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


def _find_on_path(exe):
    for d in os.environ.get('PATH', '').split(os.pathsep):
        if not d:
            continue
        p = os.path.join(d, exe)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def _discover_cadence_setup_scripts():
    '''Generically discover Cadence IC setup scripts from user shell rc files (up to 2 hops).'''
    home = os.path.expanduser('~')
    roots = [
        os.path.join(home, n)
        for n in ('.cshrc', '.tcshrc', '.bashrc', '.bash_profile', '.profile')
        if os.path.isfile(os.path.join(home, n))
    ]
    visited = set()
    queue = [(p, 0) for p in roots]
    candidates = []
    seen_cands = set()

    def add_cand(path, shell_kind, priority):
        rp = os.path.realpath(path)
        if rp in seen_cands or sensitive_path(rp) or not os.path.isfile(rp):
            return
        seen_cands.add(rp)
        low = rp.lower()
        # Deprioritize unofficial/patched paths when standard installs exist
        if 'crack' in low:
            priority += 50
        candidates.append((priority, shell_kind, rp))

    while queue:
        cur, depth = queue.pop(0)
        rcur = os.path.realpath(cur)
        if rcur in visited or depth > 2:
            continue
        visited.add(rcur)
        try:
            text = read_utf8(rcur, 131072)
        except Exception:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            low = stripped.lower()
            # Direct source statements
            for m in re.finditer(r'(?:source|\.)\s+["\']?([/~][^\s;\'"|&]+)["\']?', stripped):
                raw_p = os.path.expanduser(m.group(1))
                if not os.path.isfile(raw_p):
                    continue
                pl = raw_p.lower()
                is_csh = ('csh' in pl) or rcur.endswith(('.cshrc', '.tcshrc', '.csh')) or ('alias ' in low and 'virtuoso' in low)
                skind = 'csh' if is_csh else 'sh'
                if any(tok in pl for tok in ('cadence', 'ic6', 'ic23', 'icadv', 'virtuoso')):
                    pri = 10
                    if 'ic23' in pl or 'icadv' in pl:
                        pri = 5
                    elif 'ic618' in pl or 'ic6' in pl:
                        pri = 6
                    if 'virtuoso' in low or 'ic6' in low or 'icadv' in low:
                        pri -= 2
                    add_cand(raw_p, skind, pri)
                elif depth < 2:
                    queue.append((raw_p, depth + 1))
    candidates.sort(key=lambda x: (x[0], x[2]))
    return candidates[:8]


def _discover_si_binary_direct():
    '''Look for Cadence si executable in PATH, env vars, or common EDA roots.'''
    direct = _find_on_path('si')
    if direct:
        return direct
    for var in ('CDSHOME', 'IC_HOME', 'CADENCE_HOME', 'CDS_INST_DIR', 'CDS_ROOT'):
        base = os.environ.get(var, '')
        if not base:
            continue
        for sub in ('bin/si', 'tools/dfII/bin/si', 'tools.lnx86/dfII/bin/si'):
            cand = os.path.join(base, sub)
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand
    found = []
    for root in ('/home/cadence', '/opt/cadence', '/opt', '/eda/cadence', '/eda', '/cad', '/tools'):
        if not os.path.isdir(root):
            continue
        try:
            lvl1 = os.listdir(root)
        except OSError:
            continue
        for a in lvl1:
            al = a.lower()
            if not any(k in al for k in ('ic6', 'ic23', 'icadv', 'cadence', 'virtuoso')):
                continue
            p1 = os.path.join(root, a)
            if not os.path.isdir(p1):
                continue
            checks = [os.path.join(p1, 'bin', 'si'), os.path.join(p1, 'tools', 'dfII', 'bin', 'si')]
            try:
                for b in os.listdir(p1):
                    p2 = os.path.join(p1, b)
                    if os.path.isdir(p2):
                        checks.append(os.path.join(p2, 'bin', 'si'))
            except OSError:
                pass
            for c in checks:
                if os.path.isfile(c) and os.access(c, os.X_OK):
                    pri = 50 if 'crack' in c.lower() else (5 if 'ic23' in c.lower() or 'icadv' in c.lower() else 10)
                    found.append((pri, c))
    found.sort()
    return found[0][1] if found else None


def _run_si_netlist(work_dir):
    '''Invoke Cadence 'si -batch -command nl' inside work_dir using discovered environment or binary.'''
    direct_si = _find_on_path('si')
    attempts = []
    if direct_si:
        attempts.append(('path', [direct_si, '-batch', '-command', 'nl']))

    for _, skind, script_path in _discover_cadence_setup_scripts():
        if skind == 'csh':
            csh_bin = _find_on_path('csh') or _find_on_path('tcsh')
            if csh_bin:
                cmd = 'source "%s" >& /dev/null; si -batch -command nl' % script_path.replace('"', '\\"')
                attempts.append((script_path, [csh_bin, '-f', '-c', cmd]))
        else:
            sh_bin = _find_on_path('bash') or '/bin/sh'
            cmd = '. "%s" >/dev/null 2>&1; si -batch -command nl' % script_path.replace('"', '\\"')
            attempts.append((script_path, [sh_bin, '-c', cmd]))

    fallback_si = _discover_si_binary_direct()
    if fallback_si and fallback_si != direct_si:
        attempts.append((fallback_si, [fallback_si, '-batch', '-command', 'nl']))

    if not attempts:
        return None, 'No Cadence si netlister found on PATH, shell rc setup scripts, or standard EDA install directories.'

    last_err = ''
    for label, argv in attempts:
        try:
            proc = subprocess.Popen(
                argv,
                cwd=work_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            out_b, err_b = proc.communicate()
            out_s = (out_b or b'').decode('utf-8', 'replace')
            err_s = (err_b or b'').decode('utf-8', 'replace')
            nl_path = os.path.join(work_dir, 'netlist')
            if proc.returncode == 0 and os.path.isfile(nl_path) and os.path.getsize(nl_path) > 0:
                return label, None
            snippet = (err_s.strip() or out_s.strip() or ('exit code %d' % proc.returncode))[-260:]
            last_err = '[%s] %s' % (os.path.basename(label), snippet)
        except Exception as exc:
            last_err = '[%s] %s' % (os.path.basename(label), str(exc))
    return None, 'Cadence si netlist export failed: ' + last_err


def do_export_schematic():
    raw_path = resolve_user_path(PAYLOAD['path'])
    if os.path.isfile(raw_path) and os.path.basename(raw_path) in ('sch.oa', 'sch.cdb'):
        sch_dir = os.path.dirname(raw_path)
    elif is_schematic_dir(raw_path):
        sch_dir = raw_path
    elif os.path.isdir(raw_path):
        cand_sch, _, _ = scan_cell_views(raw_path)
        if not cand_sch:
            return {'error': 'No Virtuoso schematic view (sch.oa) found at: ' + raw_path}
        sch_dir = cand_sch
    else:
        return {'error': 'Remote Virtuoso schematic path does not exist: ' + raw_path}

    cell_dir = os.path.dirname(sch_dir)
    lib_dir = os.path.dirname(cell_dir)
    view_name = os.path.basename(sch_dir)
    cell_name = os.path.basename(cell_dir)
    override_cds = PAYLOAD.get('cds_lib') or None
    cds_lib, lib_name, lib_defined = find_cds_lib_for_library(lib_dir, override_cds)
    _, mviews, _ = scan_cell_views(cell_dir)

    include_maestro = bool(PAYLOAD.get('include_maestro', True))
    requested_mview = (PAYLOAD.get('maestro_view') or '').strip()
    selected_mview = None
    if mviews:
        if requested_mview:
            for mv in mviews:
                if mv['name'] == requested_mview:
                    selected_mview = mv
                    break
        if selected_mview is None:
            selected_mview = mviews[0]

    maestro_cfg = None
    if selected_mview is not None:
        maestro_cfg = parse_maestro_setup(selected_mview['path'], selected_mview['name'], PAYLOAD.get('maestro_test'))

    max_file = int(PAYLOAD['max_file_bytes'])
    work_dir = tempfile.mkdtemp(prefix='spiceowl_si_')
    warnings = []
    try:
        # Build isolated cds.lib referencing original project cds.lib so relative paths stay valid
        cds_lines = []
        if cds_lib and os.path.isfile(cds_lib):
            cds_lines.append('SOFTINCLUDE %s' % cds_lib)
        if not lib_defined:
            cds_lines.append('DEFINE %s %s' % (lib_name, lib_dir))
        with io.open(os.path.join(work_dir, 'cds.lib'), 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(cds_lines) + '\n')

        si_env = (
            'simLibName = "%s"\n'
            'simCellName = "%s"\n'
            'simViewName = "%s"\n'
            'simSimulator = "spectre"\n'
            'simNotIncremental = \'t\n'
            'simReNetlistAll = nil\n'
            'simViewList = \'("spectre" "cmos_sch" "cmos.sch" "schematic" "veriloga" "ahdl" "structural")\n'
            'simStopList = \'("spectre" "veriloga" "ahdl")\n'
            'simNetlistHier = \'t\n'
            'nlFormatterClass = \'spectreFormatter\n'
            'nlCreateAmap = \'t\n'
            'nlDesignVarNameList = nil\n'
        ) % (lib_name.replace('"', '\\"'), cell_name.replace('"', '\\"'), view_name.replace('"', '\\"'))
        with io.open(os.path.join(work_dir, 'si.env'), 'w', encoding='utf-8') as fh:
            fh.write(si_env)

        si_tool, err = _run_si_netlist(work_dir)
        if err:
            return {'error': err}

        body_text = read_utf8(os.path.join(work_dir, 'netlist'), max_file)
        header_path = os.path.join(work_dir, 'netlistHeader')
        footer_path = os.path.join(work_dir, 'netlistFooter')
        header_text = read_utf8(header_path, 65536).strip() if os.path.isfile(header_path) else ''
        footer_text = read_utf8(footer_path, 65536).strip() if os.path.isfile(footer_path) else ''

        if not header_text or 'simulator lang=spectre' not in header_text.lower():
            base_header = '// Exported from Virtuoso schematic %s/%s (%s)\nsimulator lang=spectre\nglobal 0' % (lib_name, cell_name, view_name)
            if header_text:
                base_header = base_header + '\n' + header_text
        else:
            base_header = header_text

        sections = [base_header]
        maestro_applied = False
        if include_maestro and maestro_cfg is not None:
            m_head, m_foot = build_maestro_spectre_blocks(maestro_cfg)
            if m_head:
                sections.append(m_head)
            sections.append(body_text.strip())
            if footer_text:
                sections.append(footer_text)
            if m_foot:
                sections.append(m_foot)
            maestro_applied = True
        else:
            sections.append(body_text.strip())
            if footer_text:
                sections.append(footer_text)

        final_text = '\n\n'.join([s for s in sections if s]) + '\n'
        if len(final_text.encode('utf-8')) > max_file:
            return {'error': 'Exported schematic netlist exceeds 1 MiB transfer limit'}

        # Collect any local relative ahdl_include / include files referenced inside the exported netlist
        support = {}
        total_bytes = len(final_text.encode('utf-8'))
        max_total = int(PAYLOAD['max_total_bytes'])
        max_support = int(PAYLOAD['max_support_files'])
        for ref in collect_includes(body_text):
            if ref.startswith('/') or ref.startswith('~') or '$' in ref or '\\' in ref:
                continue
            cand = os.path.realpath(os.path.join(cell_dir, ref))
            if os.path.isfile(cand) and circuit_file(os.path.basename(cand)) and len(support) < max_support:
                try:
                    dt = read_utf8(cand, max_file)
                    db = len(dt.encode('utf-8'))
                    if total_bytes + db <= max_total:
                        support[ref] = dt
                        total_bytes += db
                except Exception:
                    pass

        return {
            'ok': True,
            'lib_name': lib_name,
            'cell_name': cell_name,
            'view_name': view_name,
            'remote_path': sch_dir,
            'remote_dir': cell_dir,
            'cds_lib': cds_lib,
            'file_name': cell_name + '.scs',
            'text': final_text,
            'support_files': support,
            'maestro_applied': maestro_applied,
            'maestro_config': maestro_cfg,
            'detected_maestro': [{'name': m['name'], 'kind': m['kind'], 'path': m['path']} for m in mviews],
            'si_tool': os.path.basename(si_tool) if si_tool else 'si',
            'warnings': warnings,
        }
    finally:
        try:
            shutil.rmtree(work_dir)
        except Exception:
            pass


action = PAYLOAD['action']
if action == 'browse':
    result = do_browse()
elif action == 'export_schematic':
    result = do_export_schematic()
else:
    result = do_import()
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
        raise DomainError(f"SSH operation on {ssh_target} timed out after {timeout}s.") from exc
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
    """List directories, Cadence cds.lib libraries, Virtuoso schematic/maestro views, and circuit files."""
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
    vctx = data.get("virtuoso_context")
    if not isinstance(vctx, dict):
        vctx = None
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
        "virtuoso_context": vctx,
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


def export_remote_schematic(
    ssh_target: str,
    ssh_config: str | Path | None = None,
    path: str | None = None,
    *,
    include_maestro: bool = True,
    maestro_view: str | None = None,
    maestro_test: str | None = None,
    cds_lib: str | None = None,
    ssh_hostname: str | None = None,
    runner=None,
) -> dict[str, Any]:
    """Export a Spectre netlist directly from a remote Virtuoso OA schematic view (with optional Maestro config)."""
    target = validate_ssh_target(ssh_target)
    clean_path = validate_remote_path(path, allow_empty=False, require_circuit_ext=False)
    if not isinstance(include_maestro, bool):
        raise DomainError("include_maestro must be an explicit boolean.")
    clean_cds = validate_remote_path(cds_lib, allow_empty=True, require_circuit_ext=False) if cds_lib else ""
    if maestro_view is not None and (
        not isinstance(maestro_view, str) or len(maestro_view) > 120 or "/" in maestro_view or ".." in maestro_view
    ):
        raise DomainError("Invalid maestro_view name.")
    if maestro_test is not None and (not isinstance(maestro_test, str) or len(maestro_test) > 240):
        raise DomainError("Invalid maestro_test name.")
    payload = {
        "action": "export_schematic",
        "path": clean_path,
        "include_maestro": include_maestro,
        "maestro_view": maestro_view or "",
        "maestro_test": maestro_test or "",
        "cds_lib": clean_cds,
        "max_file_bytes": MAX_FILE_BYTES,
        "max_total_bytes": MAX_TOTAL_IMPORT_BYTES,
        "max_support_files": MAX_SUPPORT_FILES,
        "allowed_exts": sorted(ALLOWED_CIRCUIT_EXTS),
        "sensitive_exact": sorted(_SENSITIVE_EXACT),
        "sensitive_suffixes": list(_SENSITIVE_SUFFIXES),
        "sensitive_substrings": list(_SENSITIVE_SUBSTRINGS),
    }
    data = _run_remote(
        target,
        ssh_config,
        payload,
        ssh_hostname=ssh_hostname,
        runner=runner,
        timeout=SCHEMATIC_EXPORT_TIMEOUT_SECONDS,
    )
    file_name = data.get("file_name")
    text = data.get("text")
    support = data.get("support_files")
    if not isinstance(file_name, str) or not isinstance(text, str) or not isinstance(support, dict):
        raise DomainError("Incomplete remote schematic export payload.")
    if len(text.encode("utf-8")) > MAX_FILE_BYTES:
        raise DomainError("Exported schematic netlist exceeds 1 MiB import limit.", "too_large")
    return {
        "schema": SCHEMATIC_EXPORT_SCHEMA,
        "ssh_target": target,
        "lib_name": str(data.get("lib_name") or ""),
        "cell_name": str(data.get("cell_name") or ""),
        "view_name": str(data.get("view_name") or "schematic"),
        "remote_path": str(data.get("remote_path") or clean_path),
        "remote_dir": str(data.get("remote_dir") or ""),
        "cds_lib": data.get("cds_lib") if isinstance(data.get("cds_lib"), str) else None,
        "file_name": file_name,
        "text": text,
        "support_files": {str(k): str(v) for k, v in support.items()},
        "maestro_applied": bool(data.get("maestro_applied")),
        "maestro_config": data.get("maestro_config") if isinstance(data.get("maestro_config"), dict) else None,
        "detected_maestro": list(data.get("detected_maestro") or [])[:16],
        "si_tool": str(data.get("si_tool") or "si"),
        "warnings": list(data.get("warnings") or [])[:16],
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
        "remote_dir", "return_url", "maestro_config",
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

    maestro_config = payload.get("maestro_config")
    if maestro_config is not None and not isinstance(maestro_config, dict):
        raise DomainError("maestro_config must be a JSON object if provided.")

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
        "maestro_config": maestro_config,
    }

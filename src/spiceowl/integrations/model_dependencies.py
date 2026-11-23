"""Bounded model-dependency evidence, without transport or execution authority.

``build_model_audit_script`` emits a read-only Python 2.7/3 program for an
operator-authorized remote filesystem. The caller supplies the identity gate,
transport deadline and private storage. Its output contains hashes and small
structural metadata, never model bodies. Static coverage is not a simulator
elaboration proof or a complete operating-system dynamic-loader trace.

``audit_read_log`` is pure. Paths in a log never cause filesystem/network IO.
Only explicitly supplied prelaunch pins can cover a logged dependency;
post-run observations cannot retroactively establish launch provenance.
"""

import json
import posixpath
import re

from spiceowl.domain.common import DomainError


AUDIT_SCHEMA = "spiceowl.model-dependency-audit.v1"
LOG_SCHEMA = "spiceowl.model-read-log-audit.v1"
MAX_LOG_BYTES = 4 * 1024 * 1024
MAX_LOG_LINES = 65536
MAX_LOG_LINE_BYTES = 16384
MAX_LOG_REFERENCES = 8192
MAX_PINS = 1024
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.:$+-]{0,127}\Z")
_LIMITS = {
    "max_files": 128,
    "max_file_bytes": 256 * 1024 * 1024,
    "max_total_bytes": 512 * 1024 * 1024,
    "max_sections": 1024,
    "max_edges": 8192,
    "max_directives": 32768,
    "max_line_bytes": 65536,
    "max_logical_line_bytes": 1024 * 1024,
    "max_issues": 256,
}
_CEILINGS = dict(_LIMITS, max_files=512, max_sections=4096,
                 max_total_bytes=1024 * 1024 * 1024,
                 max_logical_line_bytes=16 * 1024 * 1024)
_ROLES = {"dynamic_library", "tool_config", "directory_reference", "model", "ahdl_source"}


def _invalid(message="Invalid or oversized model-dependency input."):
    raise DomainError(message, "invalid_model_dependencies")


def _path(value):
    if type(value) is not str:
        _invalid("Dependency paths must be bounded absolute POSIX paths.")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        _invalid("Dependency paths must be valid UTF-8 text.")
    if (not value.startswith("/") or size > 4096
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or "\\" in value or value.startswith("//")):
        _invalid("Dependency paths must be bounded absolute POSIX paths.")
    return posixpath.normpath(value)


def _digest(value):
    if type(value) is not str or not _HASH.fullmatch(value):
        _invalid("Dependency pins must be lowercase SHA-256 digests.")
    return value


def _inside(path, roots):
    return any(path == root or path.startswith(root + "/") for root in roots)


def validate_audit_config(config):
    """Validate the private profile's ``dependencies`` object, without IO.

    Required fields: roots (1..8 absolute authorized directories), main,
    sections (1..64 section names or null for global scope), required_masters.
    Optional profile_dependencies merge explicit files/directories with source
    discovery; file records may carry an expected sha256. When .option tmipath
    or etmiUsrInput references a directory, an explicitly configured
    tmi_relative_library (for example RH_64/libTMImodel.so) identifies the
    platform binary. Absence of that rule makes TMI coverage unknown.
    Limits control finite reads, never broaden roots. No environment expansion
    is performed.
    """
    required = {"roots", "main", "sections", "required_masters"}
    optional = {"profile_dependencies", "limits", "tmi_relative_library"}
    if type(config) is not dict or not required <= set(config) or set(config) - required - optional:
        _invalid("Dependency configuration fields do not match the supported schema.")
    roots = config["roots"]
    if type(roots) is not list or not 1 <= len(roots) <= 8:
        _invalid("Configure between one and eight authorized dependency roots.")
    roots = list(dict.fromkeys(_path(root) for root in roots))
    if "/" in roots:
        _invalid("A filesystem-wide dependency root is not supported.")
    main = _path(config["main"])
    if not _inside(main, roots):
        _invalid("The main dependency must be inside an authorized root.")
    sections = config["sections"]
    masters = config["required_masters"]
    if type(sections) is not list or not 1 <= len(sections) <= 64:
        _invalid("Select one to 64 model sections, or null for global scope.")
    if any(s is not None and (type(s) is not str or not _NAME.fullmatch(s)) for s in sections):
        _invalid("Invalid model section name.")
    if type(masters) is not list or len(masters) > 128:
        _invalid("The required master list is oversized.")
    if any(type(s) is not str or not _NAME.fullmatch(s) for s in masters):
        _invalid("Invalid required master name.")
    limits = config.get("limits", {})
    if type(limits) is not dict or set(limits) - set(_LIMITS):
        _invalid("Unsupported dependency audit limit.")
    for key, value in limits.items():
        if type(value) is not int or not 1 <= value <= _CEILINGS[key]:
            _invalid("Dependency audit limit is outside its supported bound.")
    profile = config.get("profile_dependencies", [])
    if type(profile) is not list or len(profile) > MAX_PINS:
        _invalid("The explicit dependency profile is oversized.")
    entries = []
    for record in profile:
        if (type(record) is not dict or not {"path", "kind", "role"} <= set(record)
                or set(record) - {"path", "kind", "role", "sha256"}
                or type(record["kind"]) is not str or type(record["role"]) is not str
                or record["kind"] not in {"file", "directory"} or record["role"] not in _ROLES):
            _invalid("Invalid explicit dependency record.")
        item = dict(record, path=_path(record["path"]))
        if not _inside(item["path"], roots):
            _invalid("An explicit dependency is outside authorized roots.")
        if "sha256" in item:
            if item["kind"] != "file":
                _invalid("Directories are references, not hashable file pins.")
            item["sha256"] = _digest(item["sha256"])
        entries.append(item)
    relative = config.get("tmi_relative_library")
    # An explicit platform-relative rule, never a glob, command or search path.
    if relative is not None and (type(relative) is not str or len(relative) > 256 or relative.startswith("/")
            or any(p in {"", ".", ".."} for p in relative.split("/"))
            or not re.fullmatch(r"[A-Za-z0-9_./+-]+", relative)
            or not relative.endswith("/libTMImodel.so")):
        _invalid("The TMI platform library must be a bounded relative file path.")
    return {"roots": roots, "main": main, "sections": list(dict.fromkeys(sections)),
            "required_masters": list(dict.fromkeys(masters)), "profile_dependencies": entries,
            "limits": dict(_LIMITS, **limits), "tmi_relative_library": relative}


def build_model_audit_script(config):
    """Return standalone Python source; do not execute it or contact a host.

    The emitted program runs on Python 2.7 or 3 using only the standard library.
    Run it with a caller-enforced deadline after verifying host/user identity.
    Keep both this private-path-bearing script and its JSON output private.
    """
    config = validate_audit_config(config)
    encoded = json.dumps(config, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return _REMOTE_AUDIT.replace("__CONFIG_LITERAL__", repr(encoded), 1)


_REMOTE_AUDIT = r'''from __future__ import print_function
import os, sys, stat, json, re, shlex, hashlib

config = json.loads(__CONFIG_LITERAL__)
limits = config['limits']
roots = [os.path.realpath(p) for p in config['roots']]
logical_roots = config['roots']
files, parsed, directories, seen = {}, {}, {}, set()
failed_files=set()
edges, declarations, scales, issues = [], [], [], []
edge_keys, declaration_keys, scale_keys = set(), set(), set()
total_bytes, directive_count, issue_count = 0, 0, 0
required = set(s.lower() for s in config['required_masters'])

def issue(code, path=None, line=None, section=None, line_end=None, observed_bytes=None):
    global issue_count
    issue_count += 1
    if len(issues) < limits['max_issues']:
        item = {'code':code}
        if path is not None: item['path'] = path
        if line is not None: item['line'] = line
        if section is not None: item['section'] = section
        if line_end is not None: item['line_end'] = line_end
        if observed_bytes is not None: item['observed_bytes'] = observed_bytes
        issues.append(item)

class AuditReadError(ValueError):
    def __init__(self,code,line,line_end,observed_bytes):
        ValueError.__init__(self,code)
        self.line=line
        self.line_end=line_end
        self.observed_bytes=observed_bytes

def allowed(path):
    return any(path == root or path.startswith(root + os.sep) for root in roots)

def resolve_path(path, base=None):
    if len(path) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in path):
        issue('invalid_path'); return None
    if any(c in path for c in '$`{}') or path.startswith('~'):
        issue('dynamic_path'); return None
    logical = os.path.normpath(path if os.path.isabs(path) else os.path.join(base or '',path))
    real = os.path.realpath(logical)
    logical_allowed=any(logical==r or logical.startswith(r+os.sep) for r in logical_roots+roots)
    if not os.path.isabs(logical) or not logical_allowed or not allowed(real):
        issue('outside_authorized_roots', logical); return None
    return real

def directory(path):
    resolved = resolve_path(path)
    if resolved is None: return None
    if not os.path.isdir(resolved):
        issue('directory_missing',resolved); return None
    if resolved not in directories and len(directories)>=limits['max_files']:
        issue('directory_limit',resolved); return None
    directories.setdefault(resolved,{'path':resolved,'kind':'directory','role':'directory_reference'})
    return resolved

def tokens(line):
    # Python 2's shlex requires byte strings; JSON output remains Unicode.
    if sys.version_info[0] < 3:
        return [s.decode('utf-8') for s in shlex.split(line.encode('utf-8'),comments=False)]
    return shlex.split(line,comments=False)

def uncomment(line, in_block):
    if not in_block and '//' not in line and '/*' not in line:
        return line.strip(), False
    result, quote, escape, i = [], None, False, 0
    while i < len(line):
        char = line[i]
        pair = line[i:i+2]
        if in_block:
            if pair == '*/': in_block=False; i += 2; continue
            i += 1; continue
        if quote:
            result.append(char)
            if escape: escape=False
            elif char == '\\': escape=True
            elif char == quote: quote=None
            i += 1; continue
        if char in ('"',"'"): quote=char; result.append(char); i += 1; continue
        if pair == '//': break
        if pair == '/*': in_block=True; i += 2; continue
        result.append(char); i += 1
    return ''.join(result).strip(), in_block

def logical_lines(stream, digest, budget):
    global total_bytes
    parts, pending_bytes, start, in_block, continuation = [], 0, 0, False, False
    number=0
    while True:
        number += 1
        raw = stream.readline(limits['max_line_bytes']+1)
        if not raw: break
        budget[0] += len(raw); total_bytes += len(raw); digest.update(raw)
        if budget[0] > limits['max_file_bytes'] or total_bytes > limits['max_total_bytes']:
            raise ValueError('byte_limit')
        if len(raw) > limits['max_line_bytes']:
            raise AuditReadError('line_limit',number,number,len(raw))
        try: line = raw.decode('utf-8')
        except UnicodeError: raise ValueError('opaque_encoding')
        stripped = line.lstrip()
        if re.search(r'(?i)pragma\s+protect',stripped):
            line='pragma protect'
            stripped=line
        if stripped.startswith('*') or not stripped.strip(): continue
        line, in_block = uncomment(line, in_block)
        if not line: continue
        plus = line.startswith('+')
        if parts and not (continuation or plus):
            yield start, ' '.join(parts)
            parts, pending_bytes = [], 0
        if not parts: start = number
        part=line[1:].strip() if plus else line
        continuation = part.endswith('\\')
        if continuation: part=part[:-1].rstrip()
        pending_bytes += len(part.encode('utf-8')) + (1 if parts else 0)
        if pending_bytes > limits['max_logical_line_bytes']:
            raise AuditReadError('logical_line_limit',start,number,pending_bytes)
        parts.append(part)
    if parts: yield start, ' '.join(parts)
    if in_block: raise ValueError('unterminated_comment')
    if continuation: raise ValueError('unterminated_continuation')

interesting = re.compile(r'(?i)^\s*(?:\.?lib\b|\.endl\b|\.?section\b|\.?endsection\b|(?:inline\s+)?\.?subckt\b|\.?include\b|\.inc\b|ahdl_include\b|\.?protect\b|\.?unprotect\b|pragma\s+protect\b|encrypt\b|`include\b)|\b(?:scalefactor|geoshrink|tmifile|tmipath|etmiUsrInput|tmi_dir|tmidir|tmi_library)\b')
protect = re.compile(r'(?i)(?:^\s*\.?protect\b|^\s*encrypt\b|pragma\s+protect)')
scale_pattern = re.compile(r'(?i)\b(scalefactor|geoshrink)\s*=\s*([^\s;]+)')
section_pattern = re.compile(r'(?i)\bsection\s*=\s*["\x27]?([A-Za-z0-9_.$:+-]+)')
tmi_pattern = re.compile(r"(?i)\b(tmipath|etmiUsrInput)\s*=\s*(?:\"([^\"\r\n]*)\"|'([^'\r\n]*)'|([^\s;]+))")

def parse_file(stream,digest,budget):
    global directive_count
    sections, records, current = set(), [], None
    for number, line in logical_lines(stream,digest,budget):
        if re.match(r'(?i)^(?:\.if|if|\.elseif|elseif|#if|#ifdef|#ifndef)\b',line):
            records.append((current,number,'unknown','conditional_scope_unresolved'))
            directive_count += 1
            if directive_count > limits['max_directives']: raise ValueError('directive_limit')
        if not interesting.search(line): continue
        directive_count += 1
        if directive_count > limits['max_directives']: raise ValueError('directive_limit')
        try: t = tokens(line)
        except ValueError:
            records.append((current,number,'unknown','malformed_directive')); continue
        if not t: continue
        cmd = t[0].lower()
        if (cmd == '.lib' and len(t) == 2) or cmd in ('section','.section'):
            if len(t) < 2 or current is not None:
                records.append((current,number,'unknown','malformed_section')); continue
            current=t[1].rstrip(';').lower()
            if current in sections: records.append((current,number,'unknown','duplicate_section'))
            sections.add(current)
            if len(sections)>limits['max_sections']: raise ValueError('section_limit')
            continue
        if cmd in ('.endl','endsection','.endsection'):
            if current is None or len(t)>1 and t[1].rstrip(';').lower()!=current:
                records.append((current,number,'unknown','malformed_section_end'))
            current=None; continue
        if protect.search(line): records.append((current,number,'unknown','opaque_protected_scope'))
        if cmd in ('.subckt','subckt') or cmd == 'inline' and len(t)>1 and t[1].lower()=='subckt':
            offset=2 if cmd=='inline' else 1
            name=t[offset].split('(',1)[0] if len(t)>offset else None
            if name is not None and name.lower() in required:
                header=re.match(r'(?i)^(?:inline\s+)?\.?subckt\s+[^\s(]+\s*(.*)$',line)
                tail=header.group(1).strip() if header else ''
                ports=[]
                if tail.startswith('(') and ')' in tail:
                    ports=tail[1:tail.index(')')].split()
                elif tail and not tail.startswith('('):
                    for terminal in tail.split():
                        if '=' in terminal or terminal.lower().rstrip(':') in ('parameters','params'): break
                        ports.append(terminal)
                if not ports or len(ports)>64 or any(not re.match(r'^[A-Za-z0-9_.$:+-]+$',p) for p in ports):
                    records.append((current,number,'unknown','master_ports_not_visible'))
                    ports=[]
                records.append((current,number,'master',{'name':name,
                                'kind':'inline_subckt' if cmd=='inline' else 'subckt',
                                'ports':ports,'port_count':len(ports)}))
        dep, selected, role = None, None, 'model'
        if cmd=='.lib' and len(t)>=3:
            dep,selected=t[1],t[2].rstrip(';')
        elif cmd in ('.include','.inc','include','ahdl_include'):
            if len(t)<2: records.append((current,number,'unknown','malformed_include'))
            else:
                dep=t[1].rstrip(';'); match=section_pattern.search(line)
                if match: selected=match.group(1)
                if re.search(r'(?i)\bsection\s*=',line) and not match:
                    records.append((current,number,'unknown','dynamic_section'))
                if cmd=='ahdl_include': role='ahdl_source'
        if dep is not None: records.append((current,number,'include',{'path':dep,'section':selected,'role':role}))
        if cmd=='`include': records.append((current,number,'unknown','unsupported_language_include'))
        for match in scale_pattern.finditer(line):
            source_option=match.group(1).lower()
            option_statement=cmd in ('.option','.options','options') or len(t)>1 and t[1].lower()=='options'
            if not option_statement or source_option=='geoshrink' and cmd!='.option': continue
            value=match.group(2).strip('"\x27')
            literal=bool(re.match(r'^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$',value))
            records.append((current,number,'scale',{'source_option':source_option,
                            'source_column':match.start()+1,
                            'value':value if literal else None,'literal':literal}))
        # This option syntax is recognized without evaluating model expressions.
        if re.search(r'(?i)\b(?:tmifile|tmipath|etmiUsrInput|tmi_dir|tmidir|tmi_library)\b',line):
            matches=list(tmi_pattern.finditer(line)) if cmd=='.option' else []
            if not matches:
                records.append((current,number,'unknown','unresolved_tmi_reference'))
            if re.search(r'(?i)\b(?:tmifile|tmi_dir|tmidir|tmi_library)\b',line):
                records.append((current,number,'unknown','unsupported_tmi_attribute'))
            for match in matches:
                value=next(v for v in match.groups()[1:] if v is not None)
                records.append((current,number,'tmi',{'attribute':match.group(1),'path':value}))
    if current is not None: records.append((current,number,'unknown','unterminated_section'))
    return {'sections':sections,'records':records}

def load(path,role='model',parse=True):
    resolved=resolve_path(path)
    if resolved is None: return None
    if resolved in failed_files: return None
    if resolved in files:
        if role not in files[resolved]['roles']: files[resolved]['roles'].append(role)
        if not parse or resolved in parsed: return resolved
        # A profile file read as data first must not be treated as parsed source.
    elif len(files)>=limits['max_files']:
        issue('file_limit',resolved); return None
    fd=None
    try:
        fd=os.open(resolved,os.O_RDONLY|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_NOFOLLOW',0))
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode): raise ValueError('not_regular_file')
        if before.st_size>limits['max_file_bytes']: raise ValueError('file_byte_limit')
        if total_bytes+before.st_size>limits['max_total_bytes']: raise ValueError('total_byte_limit')
        # Recheck after open. The descriptor and current authorized path must
        # agree; replacement or a symlink escape cannot become a valid pin.
        real_after=os.path.realpath(resolved)
        current_stat=os.stat(resolved)
        if not allowed(real_after) or real_after!=resolved or (current_stat.st_dev,current_stat.st_ino)!=(before.st_dev,before.st_ino):
            raise ValueError('path_changed')
        digest=hashlib.sha256(); budget=[0]
        with os.fdopen(fd,'rb') as stream:
            fd=None
            if parse:
                data=parse_file(stream,digest,budget)
            else:
                data=None
                while True:
                    raw=stream.read(1024*1024)
                    if not raw: break
                    budget[0]+=len(raw)
                    globals()['total_bytes']+=len(raw)
                    if budget[0]>limits['max_file_bytes'] or total_bytes>limits['max_total_bytes']:
                        raise ValueError('byte_limit')
                    digest.update(raw)
            after=os.fstat(stream.fileno())
        if (before.st_dev,before.st_ino,before.st_size,before.st_mtime,before.st_ctime)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime,after.st_ctime) or budget[0]!=before.st_size:
            raise ValueError('file_changed_during_read')
        old=files.get(resolved)
        if old and old['sha256']!=digest.hexdigest(): raise ValueError('file_changed_between_reads')
        files[resolved]={'path':resolved,'kind':'file','bytes':budget[0],'sha256':digest.hexdigest(),
                         'roles':sorted(set((old or {}).get('roles',[])+[role])),'selected_sections':[]}
        if data is not None: parsed[resolved]=data
        return resolved
    except (IOError,OSError):
        failed_files.add(resolved); issue('unreadable_file',resolved)
    except AuditReadError as error:
        failed_files.add(resolved)
        issue(str(error),resolved,line=error.line,line_end=error.line_end,observed_bytes=error.observed_bytes)
    except ValueError as error:
        failed_files.add(resolved); issue(str(error),resolved)
    finally:
        if fd is not None: os.close(fd)
    return None

queue=[(config['main'],s,[]) for s in config['sections']]
while queue:
    path,section,ancestors=queue.pop(0)
    section=section.lower() if section is not None else None
    path=resolve_path(path)
    if path is None: continue
    key=(path,section)
    if key in ancestors: issue('include_cycle',path,section=section); continue
    if key in seen: continue
    if len(seen)>=limits['max_sections']: issue('section_visit_limit'); break
    seen.add(key)
    if load(path) is None: continue
    data=parsed[path]
    files[path]['selected_sections'].append(section)
    if section is not None and section not in data['sections']:
        issue('section_not_visible',path,section=section); continue
    for current,line,kind,value in data['records']:
        if current is not None and current!=section: continue
        if kind=='unknown': issue(value,path,line,section)
        elif kind=='master':
            dk=(path,line,value['name'])
            if dk not in declaration_keys:
                declaration_keys.add(dk)
                declarations.append(dict(value,path=path,line=line,section=current))
        elif kind=='scale':
            sk=(path,line,value['source_option'],value['source_column'])
            if sk not in scale_keys:
                scale_keys.add(sk); scales.append(dict(value,path=path,line=line,section=current))
        elif kind=='tmi':
            dep=resolve_path(value['path'],os.path.dirname(path))
            if dep is None: continue
            ref=directory(dep)
            if ref is None: continue
            suffix=config['tmi_relative_library']
            if suffix is None:
                issue('tmi_platform_library_not_configured',path,line,section); continue
            library=resolve_path(os.path.join(ref,suffix))
            if library is None: continue
            ek=(path,line,section,ref,None)
            if ek not in edge_keys:
                if len(edges)>=limits['max_edges']: issue('edge_limit',path,line); queue=[]; break
                edge_keys.add(ek)
                edges.append({'from':path,'line':line,'from_section':section,'to':ref,'section':None,
                              'role':'tmi_directory','attribute':value['attribute'],'library':library})
            load(library,role='dynamic_library',parse=False)
        elif kind=='include':
            dep=resolve_path(value['path'],os.path.dirname(path))
            if dep is None: continue
            selected=value['section'].lower() if value['section'] is not None else None
            ek=(path,line,section,dep,selected)
            if ek not in edge_keys:
                if len(edges)>=limits['max_edges']: issue('edge_limit',path,line); queue=[]; break
                edge_keys.add(ek)
                edges.append({'from':path,'line':line,'from_section':section,'to':dep,'section':selected,'role':value['role']})
            if value['role']=='ahdl_source':
                load(dep,role='ahdl_source',parse=False)
                issue('opaque_ahdl_dependency_scope',dep)
            elif len(queue)<limits['max_edges']:
                queue.append((dep,selected,ancestors+[key]))
            else: issue('queue_limit',path,line)

for item in config['profile_dependencies']:
    if item['kind']=='directory': directory(item['path']); continue
    path=load(item['path'],role=item['role'],parse=False)
    if path is not None and item.get('sha256') and files[path]['sha256']!=item['sha256']:
        issue('profile_hash_mismatch',path)

found=set(x['name'].lower() for x in declarations)
missing=sorted(required-found)
for name in missing: issue('required_master_not_visible',section=name)
scale_groups=[]
for option in sorted(set(x['source_option'] for x in scales)):
    selected=[x for x in scales if x['source_option']==option]
    values=sorted(set(x['value'] for x in selected if x['literal']))
    scale_groups.append({'source_option':option,'declaration_count':len(selected),
                         'literal_values':values,'duplicate':len(selected)>1,
                         'conflicting_literals':len(values)>1,
                         'has_dynamic_value':any(not x['literal'] for x in selected)})
out={'schema':'spiceowl.model-dependency-audit.v1','static_only':True,
     'status':'unknown' if issue_count else 'complete_for_supported_static_syntax',
     'files':[files[p] for p in sorted(files)],'directories':[directories[p] for p in sorted(directories)],
     'edges':edges,'master_declarations':declarations,'missing_masters':missing,
     'scalefactors':{'declarations':scales,'by_source_option':scale_groups,
                     'duplicate':any(x['duplicate'] for x in scale_groups),
                     'conflicting_literals':any(x['conflicting_literals'] for x in scale_groups),
                     'has_dynamic_value':any(not x['literal'] for x in scales),'effective_value':None,
                     'source_options_are_not_assumed_equivalent':True},
     'unknown':issues,'unknown_count':issue_count,'unknown_truncated':issue_count>len(issues),
     'visited_sections':len(seen),'bytes_read':total_bytes,
     'limits':limits,
     'scope_note':'Static supported-syntax inventory only; not a complete dynamic-loader trace. Caller must freeze and validate these pins before launch.'}
print(json.dumps(out,sort_keys=True))
'''


def _pin_sources(pinned_paths, pinned_sha256, native_payload, builtin_profile):
    if (type(pinned_paths) is not dict or type(pinned_sha256) is not dict
            or len(pinned_paths) > MAX_PINS or len(pinned_sha256) > MAX_PINS
            or set(pinned_paths) != set(pinned_sha256)):
        _invalid("Path aliases and prelaunch digests must have matching bounded keys.")
    pins, kinds, sources, postrun = {}, {}, {}, set()

    def add(path, digest, source):
        if path in pins and pins[path] != digest:
            _invalid("Conflicting prelaunch pins for the same dependency path.")
        if kinds.get(path) == "directory":
            _invalid("A dependency cannot be both a directory and a pinned file.")
        pins[path] = digest
        kinds[path] = "file"
        sources.setdefault(path, []).append(source)

    for alias, path in pinned_paths.items():
        if type(alias) is not str or not _NAME.fullmatch(alias):
            _invalid("Invalid pin alias.")
        add(_path(path), _digest(pinned_sha256[alias]), "external_pin")
    native_payload = {} if native_payload is None else native_payload
    if type(native_payload) is not dict or len(native_payload) > MAX_PINS:
        _invalid("Native payload must map paths to prelaunch SHA-256 digests.")
    for path, digest in native_payload.items():
        add(_path(path), _digest(digest), "native_payload")
    builtin_profile = [] if builtin_profile is None else builtin_profile
    if type(builtin_profile) is not list or len(builtin_profile) > MAX_PINS:
        _invalid("The tool dependency profile is oversized.")
    for item in builtin_profile:
        if (type(item) is not dict or not {"path", "kind", "phase"} <= set(item)
                or set(item) - {"path", "kind", "phase", "sha256", "role"}
                or type(item["kind"]) is not str or type(item["phase"]) is not str
                or item["kind"] not in {"file", "directory"}
                or item["phase"] not in {"prelaunch", "postrun"}):
            _invalid("Invalid dependency profile evidence.")
        path = _path(item["path"])
        if path in kinds and kinds[path] != item["kind"]:
            _invalid("Conflicting dependency path types.")
        kinds[path] = item["kind"]
        if item["kind"] == "directory":
            if "sha256" in item:
                _invalid("A directory cannot supply a file digest.")
            if item["phase"] == "prelaunch":
                sources.setdefault(path, []).append("builtin_profile")
        elif item["phase"] == "prelaunch":
            add(path, _digest(item.get("sha256")), "builtin_profile")
        elif "sha256" in item:
            _digest(item["sha256"])
        if item["phase"] == "postrun":
            postrun.add(path)
    return pins, kinds, sources, postrun


def audit_read_log(log_text, *, pinned_paths, pinned_sha256,
                   native_payload=None, builtin_profile=None):
    """Compare every supported ``Reading file``/``Loading`` record with pins.

    ``pinned_paths`` and ``pinned_sha256`` are the *frozen prelaunch* alias maps.
    ``native_payload`` maps exact absolute staged paths to frozen input hashes.
    ``builtin_profile`` records have path, kind=file|directory, phase=prelaunch|
    postrun, plus sha256 for prelaunch files. Directories are acknowledged
    references, never file pins. Returned hashes come from supplied pins, not
    from log text. This function neither reads paths nor verifies file bytes.
    """
    if type(log_text) is not str:
        _invalid("The dependency read log must be text.")
    try:
        size = len(log_text.encode("utf-8"))
    except UnicodeError:
        _invalid()
    if size > MAX_LOG_BYTES:
        _invalid("The dependency read log exceeds its byte limit.")
    lines = log_text.splitlines()
    if len(lines) > MAX_LOG_LINES or any(len(line.encode("utf-8")) > MAX_LOG_LINE_BYTES for line in lines):
        _invalid("The dependency read log exceeds its line limit.")
    pins, kinds, sources, postrun = _pin_sources(pinned_paths, pinned_sha256, native_payload, builtin_profile)
    references, malformed = [], []
    for number, line in enumerate(lines, 1):
        match = re.match(r"^\s*(Reading file\s*:|Loading\b)\s*(.*?)\s*$", line, re.I)
        if not match:
            continue
        if len(references) + len(malformed) >= MAX_LOG_REFERENCES:
            _invalid("The dependency read log has too many references.")
        kind = "reading" if match[1].lower().startswith("reading") else "loading"
        candidate = match[2].strip()
        if kind == "loading":
            candidate = re.sub(r"\s+\.\.\.\s*$", "", candidate)
        if len(candidate) >= 2 and candidate[0] in "\"'`" and candidate[-1] == candidate[0]:
            candidate = candidate[1:-1]
        try:
            path = _path(candidate)
        except (DomainError, UnicodeError):
            malformed.append({"line": number, "event": kind, "reason": "unrecognized_path_record"})
            continue
        path_kind = kinds.get(path, "unknown")
        covered = path in pins
        prelaunch_declared = covered or path_kind == "directory" and "builtin_profile" in sources.get(path, [])
        references.append({"line": number, "event": kind, "path": path, "kind": path_kind,
                           "pin_status": "pre_pinned" if covered else "directory_reference" if path_kind == "directory" else "not_pre_pinned",
                           "sha256": pins.get(path), "pin_sources": sorted(set(sources.get(path, []))),
                           "prelaunch_declared": prelaunch_declared,
                           "postrun_observed": path in postrun})
    unknown = sorted({item["path"] for item in references if not item["prelaunch_declared"]})
    covered_files = any(item["pin_status"] == "pre_pinned" for item in references)
    return {"schema": LOG_SCHEMA, "status": "covered_by_declared_prelaunch_pins" if covered_files and not unknown and not malformed else "unknown",
            "references": references, "unrecognized_records": malformed, "uncovered_paths": unknown,
            "directory_paths": sorted({item["path"] for item in references if item["kind"] == "directory"}),
            "observed_paths": sorted({item["path"] for item in references}), "log_bytes": size,
            "file_bytes_verified": False, "postrun_hashes_establish_provenance": False,
            "scope_note": "Caller-supplied frozen prelaunch pins cover observed log paths only; log text proves neither file bytes nor the complete dynamic-loader closure."}

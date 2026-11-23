"""Bounded metadata probe for explicitly observed Verilog-A include roots."""
import hashlib
import json
import posixpath
import re

from spiceowl.domain.common import DomainError
from .pdk_discovery import _path


SCHEMA = 'spiceowl.veriloga-headers.v1'
MAX_RESULT_BYTES = 512 * 1024
LIMITS = {'max_files': 32, 'max_file_bytes': 2 * 1024 * 1024,
          'max_total_bytes': 8 * 1024 * 1024, 'max_depth': 16, 'max_includes': 256}
STANDARD_NAMES = {'constants.vams', 'disciplines.vams'}
GAP_CODES = {'header_outside_observed_roots', 'header_include_cycle', 'header_depth_limit',
    'header_file_limit', 'header_total_byte_limit', 'header_file_missing', 'header_file_byte_limit',
    'header_read_unavailable', 'unbalanced_header_condition', 'header_include_limit',
    'dynamic_header_include', 'dynamic_or_absolute_header_include', 'ambiguous_header_include',
    'conditional_header_include', 'standard_header_missing', 'standard_header_ambiguous',
    'header_symlink_resolution_unsupported', 'standard_header_symlink_unsupported'}


def _invalid(message='Header observation structure differs from the bounded probe schema.'):
    raise DomainError(message, 'invalid_veriloga_headers')


def _observed_path(value):
    if (not isinstance(value, str) or not value.startswith('/') or value.startswith('//')
            or len(value) > 4096 or '\\' in value or posixpath.normpath(value) != value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        _invalid('Use exact bounded observed absolute paths.')
    return value


def build_veriloga_header_probe_script(*, roots, limits=None):
    """Return Python 2.7/3 code; never guess an installation or invoke a tool."""
    if not isinstance(roots, list) or not 1 <= len(roots) <= 8:
        raise DomainError('Supply one to eight observed tool include roots.', 'invalid_veriloga_headers')
    roots = list(dict.fromkeys(_path(path) for path in roots))
    if '/' in roots: raise DomainError('Filesystem-wide header discovery is unsupported.', 'invalid_veriloga_headers')
    configured = dict(LIMITS)
    for key, value in (limits or {}).items():
        if key not in configured or type(value) is not int or not 1 <= value <= configured[key]:
            raise DomainError('Header probe limits may only be tightened.', 'invalid_veriloga_headers')
        configured[key] = value
    return 'import json\nconfig=json.loads('+repr(json.dumps({'roots': roots, 'limits': configured}))+')\n' + _PROGRAM


def validate_veriloga_headers(result):
    """Validate bounded shape and graph consistency; hashes are not authenticity."""
    if not isinstance(result, dict) or result.get('schema') != SCHEMA:
        raise DomainError('Expected a Verilog-A header observation.', 'invalid_veriloga_headers')
    try:
        raw = json.dumps(result, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()
        unsigned = {key: value for key, value in result.items() if key != 'observation_id'}
        digest = hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()).hexdigest()
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise DomainError('Header observation must be bounded finite JSON.', 'invalid_veriloga_headers') from None
    if len(raw) > MAX_RESULT_BYTES or result.get('observation_id') != digest:
        raise DomainError('Header observation digest or size differs.', 'invalid_veriloga_headers')
    coverage = result.get('coverage')
    if (not isinstance(result.get('roots'), list) or not 1 <= len(result['roots']) <= 8
            or not isinstance(result.get('files'), list) or len(result['files']) > LIMITS['max_files']
            or not isinstance(result.get('headers'), list) or len(result['headers']) > 16
            or not isinstance(result.get('includes'), list) or len(result['includes']) > LIMITS['max_includes']
            or not isinstance(result.get('missing'), list) or len(result['missing']) > 128
            or not isinstance(coverage, dict)
            or coverage.get('complete') is not (not result['missing'])
            or result.get('status') != ('partial' if result['missing'] else 'observed')
            or result.get('compiler_invoked') is not False or result.get('ready_to_run') is not False
            or result.get('fresh_audit_required') is not True
            or coverage.get('static_include_closure_only') is not True
            or coverage.get('searched_names') != ['constants.vams', 'disciplines.vams']
            or coverage.get('macro_conditions_evaluated') is not False): _invalid()
    roots = [_observed_path(root) for root in result['roots']]
    if '/' in roots or len(set(roots)) != len(roots): _invalid()
    limits = coverage.get('limits')
    if (not isinstance(limits, dict) or set(limits) != set(LIMITS)
            or any(type(limits[key]) is not int or not 1 <= limits[key] <= bound for key, bound in LIMITS.items())): _invalid()
    if (type(coverage.get('bytes_read')) is not int or not 0 <= coverage['bytes_read'] <= limits['max_total_bytes'] + 1
            or type(coverage.get('attempted_file_count')) is not int
            or not len(result['files']) <= coverage['attempted_file_count'] <= limits['max_files']
            or len(result['includes']) > limits['max_includes']
            or type(coverage.get('missing_count')) is not int or coverage['missing_count'] < len(result['missing'])
            or coverage.get('missing_truncated') is not (coverage['missing_count'] > len(result['missing']))): _invalid()
    for gap in result['missing']:
        if (not isinstance(gap, dict) or set(gap) != {'code', 'path', 'line'}
                or not isinstance(gap['code'], str) or gap['code'] not in GAP_CODES
                or gap['path'] is not None and (not isinstance(gap['path'], str) or len(gap['path']) > 4096)
                or gap['line'] is not None and (type(gap['line']) is not int or gap['line'] < 1)): _invalid()
    files = {}
    for row in result['files']:
        if (not isinstance(row, dict) or not isinstance(row.get('path'), str)
                or not isinstance(row.get('sha256'), str) or not re.fullmatch('[a-f0-9]{64}', row['sha256'])
                or set(row) != {'path', 'sha256', 'bytes'}
                or type(row.get('bytes')) is not int or not 0 <= row['bytes'] <= limits['max_file_bytes']): _invalid()
        path = _observed_path(row['path'])
        if path in files or not any(path.startswith(root + '/') for root in roots): _invalid()
        files[path] = row
    if sum(row['bytes'] for row in files.values()) > min(coverage['bytes_read'], limits['max_total_bytes']): _invalid()
    header_keys = set()
    for row in result['headers']:
        if (not isinstance(row, dict) or not isinstance(row.get('name'), str) or row['name'] not in STANDARD_NAMES
                or set(row) != {'name', 'path', 'sha256', 'bytes', 'include_roots'}
                or not isinstance(row.get('path'), str) or row['path'] not in files
                or not isinstance(row.get('include_roots'), list) or not row['include_roots']
                or any(not isinstance(root, str) or root not in roots for root in row['include_roots'])
                or any(row['path'] != posixpath.join(root, row['name']) for root in row['include_roots'])
                or len(set(row['include_roots'])) != len(row['include_roots'])
                or any(row.get(key) != files[row['path']][key] for key in ('sha256', 'bytes'))): _invalid()
        key = row['name'], row['path']
        if key in header_keys: _invalid()
        header_keys.add(key)
    if coverage['complete'] and (len(result['headers']) != 2 or
            {row['name'] for row in result['headers']} != STANDARD_NAMES or coverage['missing_count']): _invalid()
    graph, edge_keys = {path: [] for path in files}, set()
    for edge in result['includes']:
        if (not isinstance(edge, dict) or not isinstance(edge.get('from'), str) or edge['from'] not in files
                or set(edge) != {'from', 'path', 'reference', 'line', 'conditional', 'include_guard'}
                or not isinstance(edge.get('path'), str) or not isinstance(edge.get('reference'), str)
                or not 1 <= len(edge['reference']) <= 4096 or edge['reference'].startswith('/')
                or any(ord(char) < 32 or char in '$`\\' for char in edge['reference'])
                or type(edge.get('line')) is not int or edge['line'] < 1
                or type(edge.get('conditional')) is not bool
                or edge['include_guard'] is not None and (not isinstance(edge['include_guard'], str)
                    or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*', edge['include_guard']))): _invalid()
        _observed_path(edge['path'])
        key = edge['from'], edge['line']
        if key in edge_keys: _invalid()
        edge_keys.add(key); graph[edge['from']].append(edge['path'])
        expected_paths = {posixpath.normpath(posixpath.join(base, edge['reference']))
                          for base in [posixpath.dirname(edge['from']), *roots]}
        if edge['path'] not in expected_paths and not any(
                gap['code'] == 'header_symlink_resolution_unsupported' and gap['path'] == edge['from']
                and gap['line'] == edge['line'] for gap in result['missing']): _invalid()
        if coverage['complete'] and (edge['path'] not in files or edge['conditional']
                or edge['path'] not in expected_paths): _invalid()
    if coverage['complete']:
        visited, active = set(), set()
        def visit(path, depth=0):
            if path in active or depth > limits['max_depth']: _invalid('Complete header receipt contains a cycle or excessive depth.')
            if path in visited: return
            active.add(path)
            for target in graph[path]: visit(target, depth + 1)
            active.remove(path); visited.add(path)
        for row in result['headers']: visit(row['path'])
        if visited != set(files): _invalid('Complete header receipt contains unreferenced files.')
    return result


def build_veriloga_header_binding(observation, *, required_names, selected_root=None):
    """Build public staged-layout metadata and separate private path aliases.

    An explicitly selected observed root resolves duplicate standard names.
    Only the complete reachable closure of required headers is materialized;
    this pure conversion performs no read, registration, audit or compilation.
    """
    from .agent_tb import _relative
    result = validate_veriloga_headers(observation)
    if (not isinstance(required_names, list) or not 1 <= len(required_names) <= 2
            or any(not isinstance(name, str) or name not in STANDARD_NAMES for name in required_names)
            or len(set(required_names)) != len(required_names)): _invalid('Choose one or two exact standard header names.')
    if selected_root is not None and (not isinstance(selected_root, str) or selected_root not in result['roots']):
        _invalid('Choose an exact root from this observed header receipt.')
    if result['coverage']['missing_truncated']: _invalid('Omitted probe gaps prevent complete closure selection.')
    chosen = {}
    for name in required_names:
        rows = [row for row in result['headers'] if row['name'] == name
                and (selected_root is None or selected_root in row['include_roots'])]
        if len(rows) != 1: _invalid('Required standard header is missing or ambiguous; select an observed include root.')
        chosen[name] = rows[0]
    files = {row['path']: row for row in result['files']}
    graph = {path: [] for path in files}
    for edge in result['includes']: graph[edge['from']].append(edge)
    staged, aliases, edges, active = {}, {}, [], set()
    def stage(path, name, depth=0):
        if (path not in files or not _relative(name) or name.lower() in ('dut.scs', 'tb.scs', 'pdk.scs')
                or depth > result['coverage']['limits']['max_depth']): _invalid('Selected header closure is incomplete or cannot preserve relative layout.')
        if path in active: _invalid('Selected header closure contains an include cycle.')
        if name in staged:
            if aliases[staged[name]['alias']]['path'] != path: _invalid('Selected header closure has conflicting staged names.')
            return
        if any(other.lower() == name.lower() for other in staged) or len(staged) >= LIMITS['max_files']:
            _invalid('Selected header closure has ambiguous names or exceeds its file bound.')
        if any(gap['path'] is None or gap['path'] == path for gap in result['missing']):
            _invalid('Selected header closure retains unresolved probe gaps.')
        alias = 'VA_HEADER_' + hashlib.sha256(name.encode()).hexdigest()[:24].upper()
        staged[name] = {'name': name, 'alias': alias, 'sha256': files[path]['sha256']}
        aliases[alias] = {'path': path, 'sha256': files[path]['sha256']}
        active.add(path)
        for edge in graph[path]:
            reference = edge['reference']
            if edge['conditional'] or not _relative(reference): _invalid('Selected header closure contains an unsupported conditional or relative include.')
            target_name = posixpath.join(posixpath.dirname(name), reference)
            edges.append({'from': name, 'reference': reference, 'target': target_name, 'line': edge['line']})
            if len(edges) > LIMITS['max_includes']: _invalid('Selected header closure exceeds its include bound.')
            stage(edge['path'], target_name, depth + 1)
        active.remove(path)
    for name in sorted(chosen): stage(chosen[name]['path'], name)
    public = {'veriloga_standard_includes': [dict(staged[name]) for name in sorted(chosen)],
        'veriloga_header_closure': {'inventory_sha256': result['observation_id'],
            'files': [staged[name] for name in sorted(staged)], 'includes': edges}}
    return {'public': public, 'private_aliases': aliases, 'observation_id': result['observation_id']}


_PROGRAM = r'''
import os,re,hashlib
roots=sorted(set(os.path.realpath(root) for root in config['roots']))
if '/' in roots:raise ValueError('filesystem_root_header_probe_unsupported')
limits=config['limits'];files={};edges=[];missing=[];active=set();attempted=set();total=[0];gap_count=[0]
def gap(code,path=None,line=None):
    gap_count[0]+=1
    if len(missing)<128: missing.append({'code':code,'path':path,'line':line})
def inside(path):
    return any(path==root or path.startswith(root.rstrip('/')+'/') for root in roots)
def read(path,depth=0):
    path=os.path.realpath(path)
    if not inside(path): gap('header_outside_observed_roots',path);return
    if path in active: gap('header_include_cycle',path);return
    if depth>limits['max_depth']:gap('header_depth_limit',path);return
    if path in files:return
    if path in attempted:return
    if len(attempted)>=limits['max_files']:gap('header_file_limit',path);return
    attempted.add(path)
    remaining=limits['max_total_bytes']-total[0]
    if remaining<=0:gap('header_total_byte_limit',path);return
    try:
        if not os.path.isfile(path):gap('header_file_missing',path);return
        with open(path,'rb') as stream:raw=stream.read(min(limits['max_file_bytes'],remaining)+1)
        total[0]+=len(raw)
        if len(raw)>limits['max_file_bytes']:gap('header_file_byte_limit',path);return
        if total[0]>limits['max_total_bytes']:gap('header_total_byte_limit',path);return
        text=raw.decode('utf-8')
    except (IOError,OSError,UnicodeError):gap('header_read_unavailable',path);return
    files[path]={'path':path,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
    active.add(path)
    text=re.sub(r'/\*.*?\*/',lambda match:'\n'*match.group(0).count('\n'),text,flags=re.S)
    lines=[line.split('//',1)[0] for line in text.splitlines()]
    directives=[line.strip() for line in lines if line.strip()]
    guard=None
    if len(directives)>=3:
        match=re.match(r'^`ifndef\s+([A-Za-z_][A-Za-z0-9_]*)\s*$',directives[0])
        if match and re.match(r'^`define\s+'+re.escape(match.group(1))+r'(?:\s|$)',directives[1]) and directives[-1]=='`endif':guard=match.group(1)
    # Only a conventional single outer guard is exempt from unknown conditions.
    # An alternate branch or a guard closed before EOF changes include meaning.
    if guard:
        guard_depth=0
        for index,directive in enumerate(directives):
            if re.match(r'^`(?:ifdef|ifndef)\b',directive):guard_depth+=1
            elif re.match(r'^`(?:else|elsif)\b',directive) and guard_depth==1:guard=None
            elif re.match(r'^`endif\b',directive):
                guard_depth-=1
                if guard_depth==0 and index!=len(directives)-1:guard=None
        if guard_depth:guard=None
    nesting=[]
    for number,line in enumerate(lines,1):
        condition=re.match(r'^\s*`(ifdef|ifndef)\s+(\S+)',line)
        if condition:nesting.append(condition.group(2));continue
        if re.match(r'^\s*`endif\b',line):
            if nesting:nesting.pop()
            else:gap('unbalanced_header_condition',path,number)
            continue
        if re.match(r'^\s*`(?:else|elsif)\b',line):
            if not nesting:gap('unbalanced_header_condition',path,number)
            continue
        if re.match(r'^\s*`include\b',line):
            if len(edges)>=limits['max_includes']:gap('header_include_limit',path,number);break
            match=re.match(r'^\s*`include\s+"([^"\r\n]+)"\s*$',line)
            if not match:gap('dynamic_header_include',path,number);continue
            reference=match.group(1)
            if reference.startswith('/') or any(char in reference for char in '$`\\'):
                gap('dynamic_or_absolute_header_include',path,number);continue
            conditional=bool(nesting and nesting!=[guard])
            target=os.path.realpath(os.path.join(os.path.dirname(path),reference))
            if not os.path.isfile(target):
                candidates=sorted(set(os.path.realpath(os.path.join(root,reference)) for root in roots
                    if os.path.isfile(os.path.join(root,reference))))
                if len(candidates)==1:target=candidates[0]
                elif len(candidates)>1:gap('ambiguous_header_include',path,number);continue
            expected_paths=set(os.path.normpath(os.path.join(base,reference)) for base in [os.path.dirname(path)]+roots)
            if target not in expected_paths:gap('header_symlink_resolution_unsupported',path,number)
            edges.append({'from':path,'line':number,'reference':reference,'path':target,
                          'conditional':conditional,'include_guard':guard})
            if conditional:gap('conditional_header_include',path,number)
            read(target,depth+1)
    if nesting:gap('unbalanced_header_condition',path)
    active.remove(path)
headers=[]
for name in ('constants.vams','disciplines.vams'):
    candidates=sorted(set(os.path.realpath(os.path.join(root,name)) for root in roots if os.path.isfile(os.path.join(root,name))))
    if not candidates:gap('standard_header_missing',name)
    if len(candidates)>1:gap('standard_header_ambiguous',name)
    for path in candidates:
        observed_roots=[root for root in roots if os.path.isfile(os.path.join(root,name)) and os.path.realpath(os.path.join(root,name))==path]
        direct_roots=[root for root in observed_roots if os.path.join(root,name)==path]
        for root in observed_roots:
            if root not in direct_roots:gap('standard_header_symlink_unsupported',os.path.join(root,name))
        read(path)
        if path in files and direct_roots:headers.append(dict(files[path],name=name,include_roots=direct_roots))
result={'schema':'spiceowl.veriloga-headers.v1','roots':roots,'headers':headers,
    'files':[files[path] for path in sorted(files)],'includes':edges,'missing':missing,
    'status':'observed' if not missing else 'partial','coverage':{'complete':not missing,
        'searched_names':['constants.vams','disciplines.vams'],'static_include_closure_only':True,
        'macro_conditions_evaluated':False,'bytes_read':total[0],'attempted_file_count':len(attempted),
        'missing_count':gap_count[0],'missing_truncated':gap_count[0]>len(missing),'limits':limits},
    'fresh_audit_required':True,'compiler_invoked':False,'ready_to_run':False}
raw=json.dumps(result,sort_keys=True,separators=(',',':'),ensure_ascii=True)
if len(raw.encode('utf-8'))>520000:raise ValueError('header_metadata_response_limit')
result['observation_id']=hashlib.sha256(raw.encode('utf-8')).hexdigest()
print(json.dumps(result,sort_keys=True))
'''

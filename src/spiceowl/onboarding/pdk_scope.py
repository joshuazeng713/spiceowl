"""Pure, explicit same-entry PDK scope selection from one cached observation.

Digests establish consistency, not authenticity. The caller owns the original
library/probe/host binding, durable selection, fresh audit and human review.
Nothing here opens a model path, infers a corner, or grants execution authority.
"""
import copy
import hashlib
import json
import math
import posixpath
import re

from spiceowl.domain.common import DomainError
from spiceowl.integrations.model_dependencies import validate_audit_config
from .pdk_discovery import REQUIREMENTS_SCHEMA, validate_pdk_library
from .pdk_lut import _identity
from .pdk_scope_graph import index_library_graph, project_scope_graph


CATALOG_SCHEMA = 'spiceowl.pdk-scope-catalog.v1'
SELECTION_SCHEMA = 'spiceowl.pdk-scope-selection.v1'
MAX_BYTES = 2 * 1024 * 1024
_HASH = re.compile(r'[a-f0-9]{64}\Z')
_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_.$:+-]{0,127}\Z')
_CODE = re.compile(r'[a-z][a-z0-9_]{0,127}\Z')
_PORT = re.compile(r'[A-Za-z0-9_.$:+-]{1,128}\Z')
_PUBLIC = {'selection_id', 'status', 'library_id', 'source_sha256',
    'requirements_sha256', 'entry_id', 'section_ids', 'sections',
    'required_model_names', 'covered_model_names', 'missing_model_names', 'scope',
    'model_declarations', 'tmi_options', 'selected_tmi_option_id', 'missing',
    'library_complete_within_policy', 'fresh_audit_required', 'ready_to_run',
    'scientific_compatibility', 'model_contracts_reused'}
_PRIVATE = {'server_identity', 'entry_path', 'entry_sha256', 'dependency_scopes',
    'expected_files', 'master_declarations', 'tmi_evidence', 'audit_config',
    'requirements', 'roots'}


def _invalid(message='Use an intact, bounded observed PDK scope selection.'):
    raise DomainError(message, 'invalid_pdk_scope')


def _json_data(value, depth=0):
    if depth > 24:
        _invalid('Scope data exceeds the supported nesting bound.')
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _json_data(item, depth + 1)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json_data(item, depth + 1)
        return
    _invalid('Scope data must be finite JSON with string object keys.')


def _canonical(value, *, max_bytes=MAX_BYTES):
    _json_data(value)
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=True, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, RecursionError, UnicodeError):
        _invalid()
    if len(raw) > max_bytes:
        _invalid('Scope evidence exceeds its byte bound; no evidence was truncated.')
    return raw


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _hash(value):
    if type(value) is not str or not _HASH.fullmatch(value):
        _invalid('Use an exact returned SHA-256 identity.')
    return value


def _path(value):
    if type(value) is not str:
        _invalid('Expected a canonical observed absolute path.')
    try:
        size = len(value.encode('utf-8'))
    except UnicodeError:
        _invalid('Observed paths must be valid UTF-8.')
    if (not value.startswith('/') or value.startswith('//')
            or posixpath.normpath(value) != value or size > 4096
            or '\\' in value or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        _invalid('Expected a canonical observed absolute path.')
    return value


def _section(value):
    if value is not None and (type(value) is not str or not _NAME.fullmatch(value)):
        _invalid('Use an observed section name or null for global scope.')
    return value.lower() if value is not None else None


def _names(values, *, maximum=128):
    if (type(values) is not list or len(values) > maximum
            or any(type(v) is not str or not _NAME.fullmatch(v) for v in values)
            or len(set(values)) != len(values)):
        _invalid('Use bounded distinct exact model names.')
    return values


def _requirements(value):
    _canonical(value)
    if type(value) is not dict or value.get('schema') != REQUIREMENTS_SCHEMA:
        _invalid('Bind the current source model requirements.')
    _hash(value.get('source_sha256'))
    _names(value.get('required_masters'))
    return copy.deepcopy(value)


def _entry_id(library_id, path, sha):
    return _digest({'kind': 'entry', 'library_id': library_id,
                    'path': path, 'sha256': sha})


def _section_id(library_id, entry_id, node_id):
    return _digest({'kind': 'section', 'library_id': library_id,
                    'entry_id': entry_id, 'node_id': node_id})


def _index(library):
    _canonical(library, max_bytes=16 * 1024 * 1024)
    validate_pdk_library(library)
    if type(library['coverage'].get('complete_within_policy')) is not bool:
        _invalid('The original library coverage must remain explicit.')
    return index_library_graph(library)


def _public_gaps(rows):
    # Graph details can contain private paths. Public gaps are bounded codes.
    return [{'code': code} for code in sorted({row['code'] for row in rows})]


def library_scope_catalog(library, *, entry_id=None, offset=0, limit=64):
    """Page actual graph entries/scopes, including scopes beyond candidate caps."""
    if (type(offset) is not int or not 0 <= offset <= 16384
            or type(limit) is not int or not 1 <= limit <= 128):
        _invalid('Use offset 0..16384 and page size 1..128.')
    index = _index(library)
    library_id = library['library_id']
    entries = {_entry_id(library_id, path, index['files'][path]['sha256']): path
               for path in sorted(index['by_path'])}
    if entry_id is None:
        items = [{'entry_id': identity, 'label': posixpath.basename(path),
                  'entry_sha256': index['files'][path]['sha256'],
                  'section_count': len(index['by_path'][path])}
                 for identity, path in entries.items()]
    else:
        _hash(entry_id)
        if entry_id not in entries:
            _invalid('The entry identity is absent from this exact library.')
        items = [{'section_id': _section_id(library_id, entry_id, node['node_id']),
                  'section': node['section'],
                  'has_include_targets': bool(node['include_targets']),
                  'observed_model_count': len(node['master_coverage']),
                  'unknown_codes': sorted(set(node['unknown_codes']))}
                 for node in index['by_path'][entries[entry_id]]]
    page = items[offset:offset + limit]
    result = {'schema': CATALOG_SCHEMA, 'library_id': library_id,
              'kind': 'entries' if entry_id is None else 'sections', 'entry_id': entry_id,
              'items': page, 'total': len(items),
              'next_offset': offset + len(page) if offset + len(page) < len(items) else None,
              'status': 'blocked' if index['missing'] else 'available',
              'missing': _public_gaps(index['missing']),
              'library_complete_within_policy': library['coverage']['complete_within_policy'],
              'ready_to_run': False}
    _canonical(result)
    return result


def _tmi_seed(public, private):
    return {key: public[key] for key in ('library_id', 'source_sha256',
        'requirements_sha256', 'entry_id', 'section_ids')} | {
        'server_identity': private['server_identity']}


def _tmi_options(evidence, seed):
    """Intersect observed ABIs; never guess from the client architecture."""
    if type(evidence) is not list or len(evidence) > 8192:
        _invalid('TMI evidence exceeds its supported bound.')
    groups, gaps = {}, []
    for ref in evidence:
        fields = {'source_path', 'section', 'applied_scopes', 'line', 'attribute',
                  'directory', 'platform_candidates'}
        if type(ref) is not dict or set(ref) != fields:
            _invalid('TMI references must preserve their observed fields.')
        _path(ref['source_path']); _section(ref['section'])
        if (type(ref['line']) is not int or not 1 <= ref['line'] <= 2**31 - 1
                or type(ref['attribute']) is not str
                or ref['attribute'].lower() not in ('tmipath', 'etmiusrinput')
                or type(ref['applied_scopes']) is not list or len(ref['applied_scopes']) != 1):
            _invalid()
        _section(ref['applied_scopes'][0])
        directory = _path(ref['directory'])
        options = ref['platform_candidates']
        if type(options) is not list or len(options) > 64:
            _invalid()
        mapped = {}
        for row in options:
            if (type(row) is not dict or set(row) != {'platform', 'path', 'relative_library', 'sha256', 'bytes'}
                    or type(row['platform']) is not str
                    or not re.fullmatch(r'[A-Za-z0-9_.+-]{1,239}', row['platform'])
                    or row['platform'] in ('.', '..')
                    or row['relative_library'] != row['platform'] + '/libTMImodel.so'
                    or type(row['bytes']) is not int or not 0 <= row['bytes'] <= 256 * 1024 * 1024):
                _invalid('Use an observed bounded TMI platform file.')
            _path(row['path']); _hash(row['sha256'])
            label = row['relative_library']
            if label in mapped:
                _invalid('Duplicate TMI ABI evidence is unsupported.')
            mapped[label] = {'path': row['path'], 'sha256': row['sha256']}
        if directory in groups and groups[directory] != mapped:
            gaps.append({'code': 'scope_tmi_conflicting_evidence'})
        groups[directory] = mapped
        if not mapped:
            gaps.append({'code': 'scope_tmi_platform_missing'})
    if not groups:
        return [], {}, gaps
    common = set.intersection(*(set(group) for group in groups.values()))
    if not common:
        gaps.append({'code': 'scope_tmi_no_common_platform'})
    options, pins, conflicting = [], {}, False
    for label in sorted(common):
        files = {group[label]['path']: group[label]['sha256'] for group in groups.values()}
        # Loading differently built model libraries under one ABI needs explicit
        # compatibility evidence outside this finite selection workflow.
        if len({group[label]['sha256'] for group in groups.values()}) != 1:
            conflicting = True
            continue
        bound = [{'path': path, 'sha256': files[path]} for path in sorted(files)]
        identity = _digest({'kind': 'tmi', 'selection': seed, 'label': label, 'files': bound})
        options.append({'tmi_option_id': identity, 'label': label})
        pins[identity] = bound
    if conflicting and not options:
        gaps.append({'code': 'scope_tmi_binary_conflict'})
    return options, pins, gaps


def _declaration_summary(declarations, required):
    return [{'name': name,
             'declaration_count': sum(row['name'] == name for row in declarations),
             'port_counts': sorted({row['port_count'] for row in declarations
                                  if row['name'] == name and row['port_count'] is not None})}
            for name in required]


def _selection_digest(selection):
    return _digest({'schema': selection['schema'], 'private': selection['private'],
                    'public': {k: v for k, v in selection['public'].items() if k != 'selection_id'}})


def project_library_scopes(library, *, requirements, server_identity,
                           entry_id, section_ids, tmi_option_id=None):
    """Project one explicit entry plus ordered sections into an audit proposal."""
    requirements = _requirements(requirements)
    server_identity = _identity(server_identity)
    _hash(entry_id)
    if type(section_ids) is not list or not 1 <= len(section_ids) <= 64:
        _invalid('Select one to 64 observed sections from one entry.')
    for identity in section_ids:
        _hash(identity)
    if len(set(section_ids)) != len(section_ids):
        _invalid('Selected section identities must be distinct.')
    index = _index(library)
    entries = {_entry_id(library['library_id'], path, index['files'][path]['sha256']): path
               for path in index['by_path']}
    if entry_id not in entries:
        _invalid('Select an entry returned for this exact library.')
    path = entries[entry_id]
    nodes = {_section_id(library['library_id'], entry_id, node['node_id']): node
             for node in index['by_path'][path]}
    if any(identity not in nodes for identity in section_ids):
        _invalid('Every section must belong to the same observed entry and library.')
    selected = [nodes[identity] for identity in section_ids]
    graph = project_scope_graph(index, entry_path=path,
                                section_ids_raw=[node['node_id'] for node in selected])
    required = requirements['required_masters']
    covered = set(graph['covered_model_names'])
    missing = list(graph['missing'])
    public = {'selection_id': None, 'status': 'blocked', 'library_id': library['library_id'],
        'source_sha256': requirements['source_sha256'], 'requirements_sha256': _digest(requirements),
        'entry_id': entry_id, 'section_ids': list(section_ids),
        'sections': [node['section'] for node in selected], 'required_model_names': list(required),
        'covered_model_names': [name for name in required if name in covered],
        'missing_model_names': [name for name in required if name not in covered],
        'scope': graph['scope'], 'model_declarations': _declaration_summary(graph['master_declarations'], required),
        'tmi_options': [], 'selected_tmi_option_id': tmi_option_id, 'missing': [],
        'library_complete_within_policy': library['coverage']['complete_within_policy'],
        'fresh_audit_required': True, 'ready_to_run': False,
        'scientific_compatibility': 'not_evaluated', 'model_contracts_reused': False}
    private = {'server_identity': server_identity, 'entry_path': path,
        'entry_sha256': index['files'][path]['sha256'], 'requirements': requirements,
        'roots': copy.deepcopy(library['roots']),
        **{key: graph[key] for key in ('dependency_scopes', 'expected_files', 'master_declarations', 'tmi_evidence')},
        'audit_config': None}
    if public['missing_model_names']:
        missing.append({'code': 'scope_required_models_missing'})
    options, pins, tmi_missing = _tmi_options(private['tmi_evidence'], _tmi_seed(public, private))
    public['tmi_options'] = options
    missing.extend(tmi_missing)
    if tmi_option_id is not None:
        _hash(tmi_option_id)
        if tmi_option_id not in pins:
            _invalid('Choose an observed TMI option for this source, host and exact scope selection.')
        expected = {row['path']: row['sha256'] for row in private['expected_files']}
        for row in pins[tmi_option_id]:
            if row['path'] in expected and expected[row['path']] != row['sha256']:
                missing.append({'code': 'scope_tmi_binary_conflict'})
            expected[row['path']] = row['sha256']
        private['expected_files'] = [{'path': key, 'sha256': expected[key]} for key in sorted(expected)]
    elif private['tmi_evidence']:
        missing.append({'code': 'scope_tmi_selection_required'})
    public['missing'] = _public_gaps(missing)
    if not public['missing']:
        config = {'roots': private['roots'], 'main': path, 'sections': public['sections'],
                  'required_masters': required, 'limits': library['audit_limits']}
        if tmi_option_id is not None:
            config['tmi_relative_library'] = next(row['label'] for row in options
                                                  if row['tmi_option_id'] == tmi_option_id)
        private['audit_config'] = validate_audit_config(config)
        public['status'] = 'requires_audit'
    result = {'schema': SELECTION_SCHEMA, 'public': public, 'private': private}
    public['selection_id'] = _selection_digest(result)
    return validate_scope_selection(result)


def validate_scope_selection(selection):
    """Validate retained structure and identity; never certify its provenance."""
    _canonical(selection)
    if type(selection) is not dict or set(selection) != {'schema', 'public', 'private'} or selection['schema'] != SELECTION_SCHEMA:
        _invalid()
    public, private = selection['public'], selection['private']
    if type(public) is not dict or set(public) != _PUBLIC or type(private) is not dict or set(private) != _PRIVATE:
        _invalid()
    for key in ('selection_id', 'library_id', 'source_sha256', 'requirements_sha256', 'entry_id'):
        _hash(public[key])
    if public['selection_id'] != _selection_digest(selection):
        _invalid('The scope selection differs from its retained identity.')
    requirements = _requirements(private['requirements'])
    required = requirements['required_masters']
    if (public['requirements_sha256'] != _digest(requirements)
            or public['source_sha256'] != requirements['source_sha256']
            or public['required_model_names'] != required
            or _identity(private['server_identity']) != private['server_identity']):
        _invalid('The source, requirements or observed server binding differs.')
    path = _path(private['entry_path']); _hash(private['entry_sha256'])
    if public['entry_id'] != _entry_id(public['library_id'], path, private['entry_sha256']):
        _invalid('The entry differs from its observed file identity.')
    sections = public['sections']
    if type(sections) is not list or not 1 <= len(sections) <= 64:
        _invalid()
    keys = [_section(section) for section in sections]
    if len(set(keys)) != len(keys) or public['section_ids'] != [
            _section_id(public['library_id'], public['entry_id'], _digest([path, key])) for key in keys]:
        _invalid('Ordered sections differ from their observed scope identities.')
    # Validate roots and limits through the same config grammar used at audit time.
    basis = validate_audit_config({'roots': private['roots'], 'main': path,
                                   'sections': sections, 'required_masters': required})
    if basis['roots'] != private['roots']:
        _invalid()
    def in_roots(value):
        return any(value.startswith(root + '/') or value == root for root in private['roots'])
    expected = private['expected_files']
    if type(expected) is not list or len(expected) > 512:
        _invalid()
    files = {}
    for row in expected:
        if type(row) is not dict or set(row) != {'path', 'sha256'}:
            _invalid()
        file = _path(row['path']); _hash(row['sha256'])
        if file in files or not in_roots(file):
            _invalid()
        files[file] = row['sha256']
    scopes = private['dependency_scopes']
    if type(scopes) is not list or len(scopes) > 1024:
        _invalid()
    scope_set = set()
    for row in scopes:
        if type(row) is not dict or set(row) != {'path', 'section'}:
            _invalid()
        scope = _path(row['path']), _section(row['section'])
        if scope in scope_set or scope[0] not in files:
            _invalid()
        scope_set.add(scope)
    model_paths = {scope[0] for scope in scope_set}
    declarations = private['master_declarations']
    if type(declarations) is not list or len(declarations) > 32768:
        _invalid()
    namespace, physical = {}, set()
    for row in declarations:
        fields = {'name', 'kind', 'ports', 'port_count', 'path', 'line', 'section', 'exact_name_match'}
        if type(row) is not dict or row.get('kind') not in ('model', 'subckt', 'inline_subckt'):
            _invalid()
        if set(row) != fields | ({'model_type'} if row['kind'] == 'model' else set()):
            _invalid()
        _names([row['name']]); file = _path(row['path']); key = _section(row['section'])
        if ((file, key) not in scope_set and not (key is None and file in model_paths)
                or type(row['line']) is not int or not 1 <= row['line'] <= 2**31 - 1
                or type(row['exact_name_match']) is not bool
                or type(row['ports']) is not list or len(row['ports']) > 64
                or any(type(port) is not str or not _PORT.fullmatch(port) for port in row['ports'])):
            _invalid()
        if row['kind'] == 'model':
            _names([row['model_type']])
            if row['ports'] or row['port_count'] is not None:
                _invalid()
        elif type(row['port_count']) is not int or row['port_count'] != len(row['ports']):
            _invalid()
        identity = file, row['line'], row['name']
        if identity in physical:
            _invalid()
        physical.add(identity)
        namespace.setdefault(row['name'], []).append(row)
    if (type(public['scope']) is not dict
            or set(public['scope']) != {'node_count', 'file_count', 'namespace_model_count'}
            or any(type(count) is not int or count < 0 for count in public['scope'].values())
            or type(public['model_declarations']) is not list):
        _invalid()
    for row in public['model_declarations']:
        if (type(row) is not dict or set(row) != {'name', 'declaration_count', 'port_counts'}
                or type(row['declaration_count']) is not int or row['declaration_count'] < 0
                or type(row['port_counts']) is not list
                or any(type(count) is not int or not 0 <= count <= 64 for count in row['port_counts'])):
            _invalid()
    if (public['covered_model_names'] != [name for name in required if name in namespace]
            or public['missing_model_names'] != [name for name in required if name not in namespace]
            or public['model_declarations'] != _declaration_summary(declarations, required)
            or public['scope'] != {'node_count': len(scopes), 'file_count': len(model_paths),
                                  'namespace_model_count': len(namespace)}):
        _invalid('Namespace coverage differs from the retained declaration evidence.')
    options, pins, tmi_gaps = _tmi_options(private['tmi_evidence'], _tmi_seed(public, private))
    if public['tmi_options'] != options:
        _invalid('TMI options differ from their observed file evidence.')
    for ref in private['tmi_evidence']:
        if ((ref['source_path'], _section(ref['applied_scopes'][0])) not in scope_set
                or ref['section'] is not None and _section(ref['section']) != _section(ref['applied_scopes'][0])
                or not in_roots(ref['directory'])
                or any(not in_roots(option['path']) for option in ref['platform_candidates'])):
            _invalid()
    selected = public['selected_tmi_option_id']
    if selected is not None:
        _hash(selected)
        if selected not in pins or any(files.get(row['path']) != row['sha256'] for row in pins[selected]):
            _invalid()
    selected_files = {row['path'] for row in pins.get(selected, [])}
    if set(files) != model_paths | selected_files:
        _invalid('Expected pins must cover exactly model scopes and selected TMI files.')
    missing = public['missing']
    if (type(missing) is not list or len(missing) > 256
            or any(type(row) is not dict or set(row) != {'code'} or type(row['code']) is not str
                   or not _CODE.fullmatch(row['code']) for row in missing)
            or len({row['code'] for row in missing}) != len(missing)):
        _invalid()
    necessary = {row['code'] for row in tmi_gaps}
    if public['missing_model_names']:
        necessary.add('scope_required_models_missing')
    if private['tmi_evidence'] and selected is None:
        necessary.add('scope_tmi_selection_required')
    if any(len(rows) > 1 for rows in namespace.values()):
        necessary.add('scope_graph_duplicate_model_declarations')
    if not necessary <= {row['code'] for row in missing}:
        _invalid('An observed blocking condition was removed.')
    if (public['ready_to_run'] is not False or public['fresh_audit_required'] is not True
            or public['model_contracts_reused'] is not False
            or public['scientific_compatibility'] != 'not_evaluated'
            or type(public['library_complete_within_policy']) is not bool
            or public['status'] != ('blocked' if missing else 'requires_audit')):
        _invalid('Cached evidence cannot authorize a simulation or certify its scientific setup.')
    config = private['audit_config']
    if missing:
        if config is not None:
            _invalid('Blocked selections cannot supply an executable audit configuration.')
    else:
        if files.get(path) != private['entry_sha256'] or any((path, key) not in scope_set for key in keys):
            _invalid('The complete selected entry must be in the dependency closure.')
        normalized = validate_audit_config(config)
        wanted = dict(basis, limits=normalized['limits'], tmi_relative_library=next(
            (row['label'] for row in options if row['tmi_option_id'] == selected), None))
        if normalized != config or normalized != wanted:
            _invalid('The audit configuration differs from the frozen selection.')
    return selection


def verify_scope_selection_audit(selection, audit, *, audit_config):
    """Compare evidence only; the execution owner binds audit origin/freshness."""
    from .pdk_scope_audit import verify_scope_selection_audit as compare
    return compare(selection, audit, audit_config=audit_config)

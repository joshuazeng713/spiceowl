"""Pure projections of observed PDK section metadata, never model verification.

The caller validates the enclosing library receipt and its transport provenance.
This module separately validates graph structure and projects explicitly chosen
scopes. It performs no IO and does not use candidate model-contract verdicts.
"""
import copy
import posixpath
import re

from spiceowl.domain.common import DomainError
from .pdk_discovery import _digest


MAX_NODES = 4096
MAX_EDGES = 8192
MAX_DECLARATIONS = 32768
MAX_CLOSURE_NODES = 1024
MAX_MISSING = 128
_HASH = re.compile(r'[a-f0-9]{64}\Z')
_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_.$:+-]{0,127}\Z')
_PORT = re.compile(r'[A-Za-z0-9_.$:+-]{1,128}\Z')
_CODE = re.compile(r'[a-z][a-z0-9_]{0,127}\Z')
_NODE_FIELDS = {'node_id', 'path', 'section', 'section_key', 'master_coverage',
                'include_targets', 'unknown_codes', 'tmi_references'}
_GRAPH_GAPS = {'graph_node_limit', 'graph_edge_limit', 'include_queue_limit',
               'library_model_name_limit', 'model_scan_not_started', 'response_limit',
               'library_response_limit'}


def _invalid(message='Cached PDK scope metadata is malformed or inconsistent.'):
    raise DomainError(message, 'invalid_pdk_scope')


def _path(value):
    if (not isinstance(value, str) or not value.startswith('/') or value.startswith('//')
            or len(value) > 4096 or '\\' in value or posixpath.normpath(value) != value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        _invalid()
    return value


def _inside(path, roots):
    return any(path == root or path.startswith(root + '/') for root in roots)


def _section(value):
    if value is not None and (not isinstance(value, str) or not _NAME.fullmatch(value)):
        _invalid()
    return value.lower() if value is not None else None


def _gap_sink():
    rows, identities = [], set()

    def add(code, **details):
        row = {'code': code, **details}
        identity = _digest(row)
        if identity in identities:
            return
        identities.add(identity)
        if len(rows) < MAX_MISSING - 1:
            rows.append(row)
        elif not rows or rows[-1]['code'] != 'scope_graph_missing_truncated':
            rows.append({'code': 'scope_graph_missing_truncated'})
    return rows, add


def _tmi_reference(reference, node, files, roots):
    fields = {'source_path', 'section', 'applied_scopes', 'line', 'attribute',
              'directory', 'platform_candidates'}
    if (not isinstance(reference, dict) or set(reference) != fields
            or reference['source_path'] != node['path']
            or reference['section'] not in (None, node['section'])
            or reference['applied_scopes'] != [node['section']]
            or type(reference['line']) is not int or reference['line'] < 1
            or not isinstance(reference['attribute'], str)
            or reference['attribute'].lower() not in ('tmipath', 'etmiusrinput')
            or not isinstance(reference['platform_candidates'], list)
            or len(reference['platform_candidates']) > 64):
        _invalid()
    directory = _path(reference['directory'])
    if not _inside(directory, roots):
        _invalid()
    seen = set()
    for option in reference['platform_candidates']:
        if (not isinstance(option, dict) or set(option) != {'platform', 'path', 'relative_library', 'sha256', 'bytes'}
                or not isinstance(option['platform'], str) or not 1 <= len(option['platform']) <= 256
                or '/' in option['platform'] or option['platform'] in ('.', '..')
                or any(ord(char) < 32 or char in '$`\\' for char in option['platform'])
                or option['relative_library'] != option['platform'] + '/libTMImodel.so'):
            _invalid()
        path = _path(option['path'])
        if (path not in files or option['sha256'] != files[path]['sha256']
                or type(option['bytes']) is not int or option['bytes'] != files[path]['bytes']
                or option['relative_library'] in seen):
            _invalid()
        seen.add(option['relative_library'])


def _include_edges(library, index, section_names, roots, gap):
    """Cross-check every retained include against its applied graph scope.

    Repeated targets at different physical lines remain separate edges. A global
    directive may appear in several applied scopes, but its line evidence must
    describe the same physical directive each time.
    """
    rows = library.get('includes')
    if rows is None:
        gap('scope_graph_includes_unavailable')
        return
    if not isinstance(rows, list) or len(rows) > MAX_EDGES:
        _invalid()
    omitted = any(row['field'] == 'includes' for row in library.get('detail_omissions', []))
    truncated = library.get('includes_truncated') is True
    if truncated:
        gap('scope_graph_includes_truncated')
    fields = {'from', 'from_section', 'line', 'to', 'section', 'role'}
    by_scope, physical, applied = {}, {}, set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) != fields
                or type(row['line']) is not int or not 1 <= row['line'] <= 2**31 - 1
                or row['role'] not in ('model', 'ahdl_source')):
            _invalid()
        source = _path(row['from']); source_key = _section(row['from_section'])
        target = _path(row['to']); target_key = _section(row['section'])
        if (source not in index['files'] or not _inside(target, roots)
                or source_key is not None and section_names[source].get(source_key) != row['from_section']):
            _invalid()
        occurrence = source, source_key, row['line']
        signature = target, row['section'], row['role']
        if (occurrence in applied or (source, row['line']) in physical
                and physical[(source, row['line'])] != signature):
            _invalid('Include line evidence is inconsistent across its applied scopes.')
        applied.add(occurrence); physical[(source, row['line'])] = signature
        by_scope.setdefault((source, source_key), []).append([target, target_key])
        if (source, source_key) not in index['by_scope']:
            gap('scope_graph_file_scopes_incomplete', path=source)
    if not omitted and not truncated:
        for key, node in index['by_scope'].items():
            if node['include_targets'] != by_scope.get(key, []):
                _invalid('Graph include edges differ from the observed scope and line records.')


def index_library_graph(library):
    """Index one validated library's exact scopes; absent graphs remain gaps.

    ``by_scope`` uses private tuple keys. All returned rows are independent copies
    of the observation, so projection cannot mutate the caller's receipt.
    """
    if not isinstance(library, dict):
        _invalid()
    roots = library.get('roots')
    if not isinstance(roots, list) or not 1 <= len(roots) <= 8:
        _invalid()
    roots = [_path(root) for root in roots]
    if '/' in roots:
        _invalid()
    raw_files, raw_declarations = library.get('files'), library.get('declarations')
    if (not isinstance(raw_files, list) or len(raw_files) > 256
            or not isinstance(raw_declarations, list) or len(raw_declarations) > MAX_DECLARATIONS):
        _invalid()
    files, section_names = {}, {}
    for row in raw_files:
        if (not isinstance(row, dict) or not isinstance(row.get('sha256'), str)
                or not _HASH.fullmatch(row['sha256']) or type(row.get('bytes')) is not int or not 0 <= row['bytes'] <= 256 * 1024 * 1024
                or not isinstance(row.get('sections'), list) or len(row['sections']) > 1024):
            _invalid()
        if 'roles' in row and (not isinstance(row['roles'], list)
                or any(not isinstance(role, str) for role in row['roles'])):
            _invalid()
        path = _path(row.get('path'))
        if path in files or not _inside(path, roots):
            _invalid()
        spellings = {}
        for spelling in row['sections']:
            key = _section(spelling)
            if key is None or key in spellings:
                _invalid()
            spellings[key] = spelling
        files[path] = copy.deepcopy(row)
        section_names[path] = spellings
    declarations, declaration_ids, declaration_names = [], set(), {}
    for row in raw_declarations:
        fields = {'name', 'kind', 'ports', 'port_count', 'path', 'line', 'section', 'exact_name_match'}
        if (not isinstance(row, dict) or row.get('kind') not in ('model', 'subckt', 'inline_subckt')
                or set(row) != fields | ({'model_type'} if row['kind'] == 'model' else set())
                or not isinstance(row['name'], str) or not _NAME.fullmatch(row['name'])
                or type(row['line']) is not int or row['line'] < 1
                or type(row['exact_name_match']) is not bool
                or not isinstance(row['ports'], list) or len(row['ports']) > 64
                or any(not isinstance(port, str) or not _PORT.fullmatch(port) for port in row['ports'])):
            _invalid()
        path = _path(row['path']); section_key = _section(row['section'])
        if path not in files or section_key is not None and section_names[path].get(section_key) != row['section']:
            _invalid()
        if row['kind'] == 'model':
            if row['ports'] or row['port_count'] is not None or not isinstance(row['model_type'], str) or not _NAME.fullmatch(row['model_type']):
                _invalid()
        elif type(row['port_count']) is not int or row['port_count'] != len(row['ports']):
            _invalid()
        identity = path, row['line'], row['name']
        if identity in declaration_ids:
            _invalid()
        declaration_ids.add(identity)
        declarations.append(copy.deepcopy(row))
        declaration_names.setdefault((path, section_key), set()).add(row['name'])
    missing, gap = _gap_sink()
    coverage = library.get('coverage', {})
    if not isinstance(coverage, dict):
        _invalid()
    omissions = library.get('detail_omissions', [])
    if not isinstance(omissions, list) or len(omissions) > 32:
        _invalid()
    for flag in ('results_omitted', 'unknown_truncated', 'includes_truncated'):
        if flag in library and type(library[flag]) is not bool:
            _invalid()
    if 'observed_names_truncated' in coverage and type(coverage['observed_names_truncated']) is not bool:
        _invalid()
    for row in omissions:
        if not isinstance(row, dict) or not isinstance(row.get('field'), str):
            _invalid()
        if row['field'] in ('section_graph', 'includes', 'declarations'):
            gap('scope_graph_omitted', field=row['field'])
    if library.get('results_omitted') is True:
        gap('scope_graph_omitted', field='results')
    unknown = library.get('unknown', [])
    if not isinstance(unknown, list) or len(unknown) > 256:
        _invalid()
    for row in unknown:
        if not isinstance(row, dict) or not isinstance(row.get('code'), str) or not _CODE.fullmatch(row['code']):
            _invalid()
        if row['code'] in _GRAPH_GAPS:
            gap('scope_graph_incomplete', reason=row['code'])
    count = library.get('unknown_count', len(unknown))
    if type(count) is not int or count < len(unknown):
        _invalid()
    if library.get('unknown_truncated') is True or count > len(unknown):
        gap('scope_graph_issues_truncated')
    observed_names = coverage.get('observed_model_names')
    if observed_names is not None and (not isinstance(observed_names, list) or len(observed_names) > 4096
            or any(not isinstance(name, str) or not _NAME.fullmatch(name) for name in observed_names)
            or len(set(observed_names)) != len(observed_names)):
        _invalid()
    names_truncated = coverage.get('observed_names_truncated') is True
    if names_truncated:
        gap('scope_graph_name_inventory_truncated')
    raw_nodes = library.get('section_graph')
    index = {'files': files, 'nodes': {}, 'by_scope': {}, 'by_path': {},
             'declarations': declarations, 'missing': missing}
    if raw_nodes is None or raw_nodes == []:
        gap('scope_graph_unavailable')
        return index
    if not isinstance(raw_nodes, list) or len(raw_nodes) > MAX_NODES:
        _invalid()
    edge_count, tmi_count = 0, 0
    for row in raw_nodes:
        if not isinstance(row, dict) or set(row) != _NODE_FIELDS:
            _invalid()
        path = _path(row['path']); section_key = _section(row['section'])
        if (path not in files or row['section_key'] != section_key
                or section_key is not None and section_names[path].get(section_key) != row['section']
                or not isinstance(row['node_id'], str) or not _HASH.fullmatch(row['node_id'])
                or row['node_id'] != _digest([path, section_key])
                or (path, section_key) in index['by_scope'] or row['node_id'] in index['nodes']
                or not isinstance(row['master_coverage'], list) or len(row['master_coverage']) > 4096
                or any(not isinstance(name, str) or not _NAME.fullmatch(name) for name in row['master_coverage'])
                or len(set(row['master_coverage'])) != len(row['master_coverage'])
                or not isinstance(row['include_targets'], list) or not isinstance(row['unknown_codes'], list)
                or len(row['unknown_codes']) > 32768
                or any(not isinstance(code, str) or not _CODE.fullmatch(code) for code in row['unknown_codes'])
                or not isinstance(row['tmi_references'], list)):
            _invalid()
        expected = declaration_names.get((path, None), set()) | declaration_names.get((path, section_key), set())
        if names_truncated and observed_names is not None:
            expected &= set(observed_names)
        if set(row['master_coverage']) != expected:
            _invalid('A graph scope claims a namespace different from its observed declarations.')
        edge_count += len(row['include_targets']); tmi_count += len(row['tmi_references'])
        if edge_count > MAX_EDGES or tmi_count > MAX_EDGES:
            _invalid()
        for edge in row['include_targets']:
            if not isinstance(edge, list) or len(edge) != 2:
                _invalid()
            target = _path(edge[0]); target_key = _section(edge[1])
            if not _inside(target, roots) or edge[1] != target_key:
                _invalid()
            # Missing files/sections may be observed include failures. They are
            # explicit projection gaps, never fabricated graph nodes.
        for reference in row['tmi_references']:
            _tmi_reference(reference, row, files, roots)
        node = copy.deepcopy(row)
        index['nodes'][node['node_id']] = node
        index['by_scope'][(path, section_key)] = node
        index['by_path'].setdefault(path, []).append(node)
    _include_edges(library, index, section_names, roots, gap)
    for path, rows in index['by_path'].items():
        rows.sort(key=lambda node: node['section_key'] or '')
        expected_keys = {None, *section_names[path]}
        if {node['section_key'] for node in rows} != expected_keys:
            gap('scope_graph_file_scopes_incomplete', path=path)
    for path, row in files.items():
        if path not in index['by_path'] and (row['sections'] or 'model' in row.get('roles', [])):
            gap('scope_graph_file_scopes_incomplete', path=path)
    for row in declarations:
        if (row['path'], _section(row['section'])) not in index['by_scope']:
            gap('scope_graph_declaration_scope_missing', path=row['path'], section=row['section'])
    return index


def project_scope_graph(index, *, entry_path, section_ids_raw):
    """Project explicit same-entry scopes; every returned gap blocks selection.

    Dependency scopes follow first-visit DFS order, respecting the selected node
    order and observed edge order. No result grants execution or verifies models.
    """
    if (not isinstance(index, dict) or not {'files', 'nodes', 'by_scope', 'by_path', 'declarations', 'missing'} <= set(index)
            or not isinstance(section_ids_raw, list) or not 1 <= len(section_ids_raw) <= 64
            or any(not isinstance(identity, str) or not _HASH.fullmatch(identity) for identity in section_ids_raw)
            or len(set(section_ids_raw)) != len(section_ids_raw)):
        _invalid('Choose one to 64 distinct observed section identities from one entry.')
    entry_path = _path(entry_path)
    if entry_path not in index['files']:
        _invalid('The selected entry is absent from this observation.')
    missing, gap = _gap_sink()
    result = {'dependency_scopes': [], 'expected_files': [], 'master_declarations': [],
              'covered_model_names': [], 'missing': missing, 'tmi_evidence': [],
              'scope': {'node_count': 0, 'file_count': 0, 'namespace_model_count': 0}}
    for row in index['missing']:
        gap(row['code'], **{key: value for key, value in row.items() if key != 'code'})
    if missing:
        return result
    for identity in section_ids_raw:
        node = index['nodes'].get(identity)
        if node is None or node['path'] != entry_path:
            _invalid('A selected section does not belong to this observed entry.')
    visited, active, ordered, edge_count = set(), set(), [], 0
    pending = [('enter', index['nodes'][identity]['path'], index['nodes'][identity]['section_key'])
               for identity in reversed(section_ids_raw)]
    while pending:
        operation, path, section_key = pending.pop(); key = path, section_key
        if operation == 'leave':
            active.discard(key)
            continue
        if key in active:
            gap('scope_graph_include_cycle', path=path, section_key=section_key)
            continue
        if key in visited:
            continue
        node = index['by_scope'].get(key)
        if node is None:
            gap('scope_graph_target_missing', path=path, section_key=section_key)
            continue
        if len(visited) >= MAX_CLOSURE_NODES:
            gap('scope_graph_closure_node_limit')
            break
        visited.add(key); active.add(key); ordered.append(node)
        if node['unknown_codes']:
            gap('scope_graph_unknown_scope', node_id=node['node_id'], unknown_codes=sorted(set(node['unknown_codes']))[:64])
        edge_count += len(node['include_targets'])
        if edge_count > MAX_EDGES:
            gap('scope_graph_closure_edge_limit')
            break
        pending.append(('leave', path, section_key))
        pending.extend(('enter', edge[0], edge[1]) for edge in reversed(node['include_targets']))
    paths = {node['path'] for node in ordered}
    result['dependency_scopes'] = [{'path': node['path'], 'section': node['section']} for node in ordered]
    result['expected_files'] = [{'path': path, 'sha256': index['files'][path]['sha256']} for path in sorted(paths)]
    physical = {}
    for row in index['declarations']:
        if (row['path'], _section(row['section'])) not in visited and not (row['section'] is None and row['path'] in paths):
            continue
        result['master_declarations'].append(copy.deepcopy(row))
        physical.setdefault(row['name'], set()).add((row['path'], row['line'], row['section']))
    result['master_declarations'].sort(key=lambda row: (row['path'], row['line'], row['name'], row['section'] or ''))
    for name, locations in sorted(physical.items()):
        if len(locations) > 1:
            gap('scope_graph_duplicate_model_declarations', model_name=name, declaration_count=len(locations))
    result['covered_model_names'] = sorted(physical)
    result['tmi_evidence'] = [copy.deepcopy(row) for node in ordered for row in node['tmi_references']]
    result['scope'] = {'node_count': len(ordered), 'file_count': len(paths), 'namespace_model_count': len(physical)}
    return result

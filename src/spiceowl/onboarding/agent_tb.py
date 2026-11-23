"""Pure static checks for an explicitly authored, bounded Spectre testbench.

Nothing here opens a path, rewrites source, evaluates expressions, chooses an
experiment, or invokes a simulator. A static-ready manifest is review material,
not execution authority or proof of a valid scientific measurement.
"""
import ast
import hashlib
import json
import math
import posixpath
import re

from spiceowl.domain.common import DomainError


VALIDATION_SCHEMA = 'spiceowl.agent-tb-validation.v1'
MANIFEST_SCHEMA = 'spiceowl.agent-tb-manifest.v1'
REQUIREMENTS_SCHEMA = 'spiceowl.netlist-model-requirements.v1'
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_FILES = 64
MAX_STATEMENTS = 16384
MAX_TERMINALS = 512
MAX_ISSUES = 256
NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_.$:+-]{0,127}\Z')
NODE = re.compile(r'(?:0|[A-Za-z_][A-Za-z0-9_.$:<>\[\]-]{0,127})\Z')
FILE = re.compile(r'[A-Za-z0-9_.-]{1,128}\Z')
HASH = re.compile(r'[a-f0-9]{64}\Z')
NUMBER = re.compile(r'(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?:meg|[afpnumkKMGTP])?')
PRIMITIVES = {'resistor', 'capacitor', 'inductor', 'vsource', 'isource', 'vcvs',
              'vccs', 'ccvs', 'cccs', 'transformer', 'relay', 'switch', 'port',
              'bsource', 'mutual_inductor'}
FUNCTIONS = {'abs', 'min', 'max', 'sqrt', 'pow', 'exp', 'ln', 'log', 'log10',
             'sin', 'cos', 'tan', 'asin', 'acos', 'atan', 'sinh', 'cosh', 'tanh',
             'floor', 'ceil', 'round', 'hypot', 'atan2', 'v', 'i'}
FORBIDDEN_PARAMETERS = {'file', 'filename', 'path', 'include', 'ahdl', 'command',
    'cmd', 'exec', 'system', 'shell', 'script', 'readns', 'write', 'writefinal',
    'readic', 'icfile', 'nodesetfile', 'rawfile', 'checkpoint', 'restart'}
ANALYSES = {'tran', 'ac', 'dc'}


def _invalid(message):
    raise DomainError(message, 'invalid_agent_tb')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical(value):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=True, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, RecursionError, UnicodeError):
        _invalid('Supply finite JSON data with string object keys.')
    if len(raw) > MAX_FILE_BYTES:
        _invalid('Manifest data exceeds the supported byte limit.')
    return raw


def _json_data(value, depth=0):
    if depth > 16:
        _invalid('Manifest JSON nesting exceeds the supported bound.')
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
    _invalid('Manifest data must be finite JSON without executable objects.')


def _bytes(value):
    if type(value) is str:
        try:
            value = value.encode('utf-8')
        except UnicodeError:
            _invalid('Source text must be valid UTF-8.')
    if type(value) is not bytes or not 0 < len(value) <= MAX_FILE_BYTES:
        _invalid('Supply nonempty source bytes within the per-file bound.')
    try:
        text = value.decode('utf-8')
    except UnicodeError:
        _invalid('Source bytes must be valid UTF-8.')
    if any(ord(char) < 32 and char not in '\t\n\r' or ord(char) == 127 for char in text):
        _invalid('Source contains unsupported control characters.')
    return value


def _relative(value):
    return (type(value) is str and 1 <= len(value) <= 240 and
            all(part not in ('.', '..') and FILE.fullmatch(part) for part in value.split('/')))


def _documents(source, support_files, testbench=None, source_id='dut.scs'):
    if not _relative(source_id) or source_id in ('tb.scs', 'pdk.scs'):
        _invalid('Use an explicit relative source identifier distinct from managed TB/PDK files.')
    support_files = {} if support_files is None else support_files
    if type(support_files) is not dict or len(support_files) > MAX_FILES - 2:
        _invalid('Supply a bounded mapping of relative support paths to source bytes.')
    documents = {source_id: _bytes(source)}
    if testbench is not None:
        documents['tb.scs'] = _bytes(testbench)
    seen = {path.lower() for path in documents} | {'dut.scs', 'pdk.scs', 'tb.scs'}
    for path, value in support_files.items():
        if not _relative(path) or path.lower() in seen:
            _invalid('Support paths must be distinct relative files; managed filenames cannot be replaced.')
        seen.add(path.lower())
        documents[path] = _bytes(value)
    if sum(map(len, documents.values())) > MAX_TOTAL_BYTES:
        _invalid('The supplied source bundle exceeds the total byte bound.')
    return documents


def _issue(issues, code, path=None, line=None, **extra):
    if len(issues) < MAX_ISSUES:
        row = {'code': code, 'field': 'testbench' if path == 'tb.scs' else 'sources',
               'message': code.replace('_', ' ').capitalize() + '; resolve the explicit source or binding gap.'}
        if path is not None:
            row['path'] = path
        if line is not None:
            row['line'] = line
        row.update(extra)
        issues.append(row)
    elif issues[-1]['code'] != 'issue_limit':
        issues[-1] = {'code': 'issue_limit', 'field': 'sources'}


def _statements(raw, path, issues):
    """Strip comments for inspection only; callers retain the original bytes."""
    block = False
    pending = []
    start = 1
    for number, original in enumerate(raw.decode('utf-8').splitlines(), 1):
        if len(original) > 65536:
            _issue(issues, 'line_limit', path, number)
            continue
        if not block and original.lstrip().startswith('*'):
            continue
        text, quote, index = [], None, 0
        while index < len(original):
            char = original[index]
            pair = original[index:index + 2]
            if block:
                if pair == '*/':
                    block = False
                    index += 2
                else:
                    index += 1
                continue
            if quote:
                text.append(char)
                if char == quote:
                    quote = None
                index += 1
                continue
            if char in ('"', "'"):
                quote = char
            elif pair == '//':
                break
            elif pair == '/*':
                block = True
                text.append(' ')
                index += 2
                continue
            text.append(char)
            index += 1
        syntax = ''.join(text).strip()
        if not syntax:
            continue
        if quote:
            _issue(issues, 'unterminated_string', path, number)
        if not pending:
            start = number
        continued = syntax.endswith('\\')
        pending.append(syntax[:-1].rstrip() if continued else syntax)
        if sum(map(len, pending)) > 65536:
            _issue(issues, 'statement_limit', path, start)
            pending = []
            continue
        if not continued:
            yield start, ' '.join(pending)
            pending = []
    if pending:
        _issue(issues, 'incomplete_continuation', path, start)
    if block:
        _issue(issues, 'unterminated_comment', path)


def _expression(value):
    if not value or len(value) > 4096:
        return False
    if value.startswith("'") and value.endswith("'"):
        value = value[1:-1]
    if any(char in value for char in '`$";{}\\'):
        return False
    # Literal numeric PWL/vector data is permitted without evaluating a vector.
    if value.startswith('[') and value.endswith(']'):
        return bool(value[1:-1].split()) and all(
            NUMBER.fullmatch(token.lstrip('+-')) for token in value[1:-1].split())
    position, balance, used, pieces = 0, 0, False, []
    while position < len(value):
        if value[position].isspace():
            position += 1
            continue
        numeric = NUMBER.match(value, position)
        if numeric:
            scalar = re.sub(r'(?:meg|[afpnumkKMGTP])$', '', numeric[0])
            if not math.isfinite(float(scalar)):
                return False
            used = True
            position = numeric.end()
            pieces.append('1')
            continue
        name = re.match(r'[A-Za-z_][A-Za-z0-9_.:<>\[\]-]*', value[position:])
        if name:
            token = name[0]
            position += len(token)
            tail = value[position:].lstrip()
            if tail.startswith('(') and token.lower() not in FUNCTIONS:
                return False
            pieces.append(token.lower() if tail.startswith('(') else '_symbol')
            used = True
            continue
        char = value[position]
        if char not in '+-*/(),':
            return False
        if char == '(':
            balance += 1
        elif char == ')':
            balance -= 1
            if balance < 0:
                return False
        position += 1
        pieces.append(char)
    if not used or balance:
        return False
    try:
        tree = ast.parse(' '.join(pieces), mode='eval')
    except (SyntaxError, RecursionError):
        return False
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Load, ast.Constant,
               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.UAdd, ast.USub, ast.Call)
    return all(isinstance(node, allowed) for node in ast.walk(tree))


def _parameters(text, path, line, issues):
    if not text.strip():
        return {}
    starts = list(re.finditer(r'(?<!\S)([A-Za-z_][A-Za-z0-9_]*)\s*=', text))
    if not starts or text[:starts[0].start()].strip():
        _issue(issues, 'unsupported_parameters', path, line)
        return {}
    result = {}
    for index, match in enumerate(starts):
        name = match[1]
        value = text[match.end():starts[index + 1].start() if index + 1 < len(starts) else len(text)].strip()
        if name in result:
            _issue(issues, 'duplicate_parameter', path, line, parameter=name)
        if name.lower() in FORBIDDEN_PARAMETERS or name.lower().endswith(('file', 'path', 'script', 'command')):
            _issue(issues, 'unsupported_file_or_command_parameter', path, line, parameter=name)
        elif not _expression(value):
            _issue(issues, 'unsupported_parameter_expression', path, line, parameter=name)
        result[name] = value
    return result


def _parse(raw, path, issues):
    result = {'includes': [], 'instances': [], 'interfaces': [], 'models': [], 'analyses': [], 'saves': []}
    stack, instances, count = [], set(), 0
    for line, text in _statements(raw, path, issues):
        count += 1
        if count > MAX_STATEMENTS:
            _issue(issues, 'statement_count_limit', path, line)
            break
        if re.fullmatch(r'simulator\s+lang\s*=\s*spectre', text, re.I):
            continue
        if re.match(r'simulator\b', text, re.I):
            _issue(issues, 'unsupported_language', path, line)
            continue
        if re.match(r'include\b', text, re.I):
            if stack:
                _issue(issues, 'scoped_include_unsupported', path, line)
            match = re.fullmatch(r'include\s+"([^"\r\n]+)"', text, re.I)
            if match is None or not _relative(match[1]):
                _issue(issues, 'external_or_dynamic_include', path, line)
            else:
                target = posixpath.join(posixpath.dirname(path), match[1])
                result['includes'].append({'from': path, 'line': line, 'reference': match[1], 'target': target})
            continue
        if any(char in text for char in '`;{}!') or re.match(r'(?:ahdl_include|\.include|\.lib|shell|system|exec|if|else|alter|foreach)\b', text, re.I):
            _issue(issues, 'unsupported_executable_or_dynamic_statement', path, line)
            continue
        header = re.fullmatch(r'(?:inline\s+)?subckt\s+([^\s(]+)\s*(.*)', text, re.I)
        if header:
            name, tail = header.groups()
            if tail.startswith('(') and ')' in tail:
                end = tail.index(')')
                nodes, params = tail[1:end].split(), tail[end + 1:].strip()
            else:
                parts = re.split(r'\s+parameters\s+', tail, maxsplit=1)
                nodes, params = parts[0].split(), parts[1] if len(parts) > 1 else ''
            if params.startswith('parameters '):
                params = params[len('parameters '):]
            if (not NAME.fullmatch(name) or not 1 <= len(nodes) <= MAX_TERMINALS or
                    len(set(nodes)) != len(nodes) or any(not NODE.fullmatch(node) for node in nodes)):
                _issue(issues, 'unsupported_subcircuit_interface', path, line)
                continue
            row = {'name': name, 'ports': nodes, 'path': path, 'line': line, 'scope': list(stack)}
            result['interfaces'].append(row)
            stack.append(name)
            _parameters(params, path, line, issues)
            continue
        end = re.fullmatch(r'ends(?:\s+(\S+))?', text, re.I)
        if end:
            if not stack or end[1] is not None and end[1] != stack[-1]:
                _issue(issues, 'subcircuit_end_mismatch', path, line)
            elif stack:
                stack.pop()
            continue
        if text.startswith('parameters '):
            _parameters(text[len('parameters '):], path, line, issues)
            continue
        if text.startswith('global '):
            if not all(NODE.fullmatch(node) for node in text.split()[1:]):
                _issue(issues, 'unsupported_global_nodes', path, line)
            continue
        model = re.fullmatch(r'model\s+(\S+)\s+(\S+)(?:\s+(.*))?', text, re.I)
        if model:
            if not NAME.fullmatch(model[1]) or not NAME.fullmatch(model[2]):
                _issue(issues, 'unsupported_model_declaration', path, line)
            else:
                result['models'].append({'name': model[1], 'family': model[2], 'path': path, 'line': line, 'scope': list(stack)})
                _parameters(model[3] or '', path, line, issues)
            continue
        if text.startswith('save '):
            signals = text[len('save '):].split()
            if stack or not signals or not all(NODE.fullmatch(signal) for signal in signals):
                _issue(issues, 'unsupported_save', path, line)
            else:
                result['saves'].append({'path': path, 'line': line, 'signals': signals})
            continue
        control = re.fullmatch(r'(\S+)\s+(tran|ac|dc|options)(?:\s+(.*))?', text, re.I)
        if control:
            name, kind, tail = control.groups()
            kind = kind.lower()
            if stack or not NAME.fullmatch(name):
                _issue(issues, 'unsupported_analysis_scope', path, line)
                continue
            values = _parameters(tail or '', path, line, issues)
            if kind in ANALYSES:
                if kind == 'tran' and 'stop' not in values:
                    _issue(issues, 'transient_stop_missing', path, line)
                result['analyses'].append({'name': name, 'kind': kind, 'path': path, 'line': line, 'parameters': values})
            continue
        instance = re.fullmatch(r'(\S+)\s*\(([^()]*)\)\s*(\S+)(?:\s+(.*))?', text)
        if instance:
            name, node_text, master, tail = instance.groups()
            nodes = node_text.split()
        else:
            assignment = re.search(r'(?<!\S)[A-Za-z_][A-Za-z0-9_]*\s*=', text)
            head = text[:assignment.start()].split() if assignment else text.split()
            if len(head) < 3:
                _issue(issues, 'unsupported_statement', path, line)
                continue
            name, nodes, master, tail = head[0], head[1:-1], head[-1], text[assignment.start():] if assignment else ''
        if (not NAME.fullmatch(name) or not NAME.fullmatch(master) or not 1 <= len(nodes) <= MAX_TERMINALS
                or any(not NODE.fullmatch(node) for node in nodes)):
            _issue(issues, 'unsupported_instance', path, line)
            continue
        key = (tuple(stack), name)
        if key in instances:
            _issue(issues, 'duplicate_instance', path, line, name=name)
        instances.add(key)
        _parameters(tail or '', path, line, issues)
        result['instances'].append({'name': name, 'master': master, 'ordered_nets': nodes,
            'port_count': len(nodes), 'dimensions': {}, 'line': line, 'path': path,
            'scope': list(stack), 'polarity': None, 'terminal_semantics': None})
    if stack:
        _issue(issues, 'unterminated_subcircuit', path)
    return result


def _inventory(documents, entry, allow_pdk):
    issues, parsed, edges, visited, visiting = [], {}, [], set(), set()
    def visit(path):
        if path in visiting:
            _issue(issues, 'include_cycle', path)
            return
        if path in visited:
            return
        if path == 'pdk.scs' and allow_pdk:
            visited.add(path)
            return
        if path not in documents:
            _issue(issues, 'support_file_missing', path)
            return
        visiting.add(path)
        data = _parse(documents[path], path, issues)
        parsed[path] = data
        for edge in data['includes']:
            edges.append(edge)
            visit(edge['target'])
        visiting.remove(path)
        visited.add(path)
    visit(entry)
    for path in sorted(set(documents) - visited):
        _issue(issues, 'unreferenced_supplied_file', path)
    interfaces = [row for data in parsed.values() for row in data['interfaces']]
    models = [row for data in parsed.values() for row in data['models']]
    instances = [row for data in parsed.values() for row in data['instances']]
    declarations = interfaces + models
    seen = set()
    for row in declarations:
        key = (tuple(row['scope']), row['name'])
        if key in seen:
            _issue(issues, 'ambiguous_declaration', row['path'], row['line'], name=row['name'])
        seen.add(key)
    by_name, hierarchy = {}, {}
    for row in declarations:
        by_name.setdefault(row['name'], []).append(row)
        if 'ports' in row:
            hierarchy[(row['path'], tuple(row['scope'] + [row['name']]))] = set()
    required, instance_names = set(), set()
    for instance in instances:
        instance_key = (instance['path'] if instance['scope'] else None, tuple(instance['scope']), instance['name'])
        if instance_key in instance_names:
            _issue(issues, 'duplicate_instance_in_include_closure', instance['path'], instance['line'], name=instance['name'])
        instance_names.add(instance_key)
        visible = [row for row in by_name.get(instance['master'], []) if
                   (not row['scope'] or instance['path'] == row['path'] and
                    instance['scope'][:len(row['scope'])] == row['scope'])]
        if visible:
            level = max(len(row['scope']) for row in visible)
            visible = [row for row in visible if len(row['scope']) == level]
        if not visible and instance['master'] not in PRIMITIVES:
            required.add(instance['master'])
        if len(visible) > 1:
            _issue(issues, 'ambiguous_master_reference', instance['path'], instance['line'], master=instance['master'])
        if len(visible) == 1 and 'ports' in visible[0] and len(visible[0]['ports']) != instance['port_count']:
            _issue(issues, 'subcircuit_port_count_mismatch', instance['path'], instance['line'], master=instance['master'])
        if len(visible) == 1 and 'ports' in visible[0] and instance['scope']:
            owner = (instance['path'], tuple(instance['scope']))
            child = (visible[0]['path'], tuple(visible[0]['scope'] + [visible[0]['name']]))
            hierarchy.setdefault(owner, set()).add(child)
    completed = set()
    def check_hierarchy(node, ancestors):
        if node in ancestors:
            _issue(issues, 'recursive_subcircuit_hierarchy', node[0], name=node[1][-1])
            return
        if len(ancestors) >= 64:
            _issue(issues, 'hierarchy_depth_limit', node[0])
            return
        if node in completed:
            return
        for child in hierarchy.get(node, []):
            check_hierarchy(child, ancestors | {node})
        completed.add(node)
    for node in hierarchy:
        check_hierarchy(node, set())
    return {'issues': issues, 'documents': parsed, 'visited': sorted(visited), 'includes': edges,
            'instances': instances, 'interfaces': interfaces, 'required_masters': sorted(required)}


def _requirements(raw, source_id, inventory):
    return {'schema': REQUIREMENTS_SCHEMA, 'mode': 'agent-tb', 'source_id': source_id,
        'source_sha256': _sha(raw), 'status': 'partial' if inventory['issues'] else 'parsed',
        'instances': inventory['instances'], 'interfaces': inventory['interfaces'],
        'required_masters': inventory['required_masters'], 'includes': inventory['includes'],
        'issues': inventory['issues'], 'confirmation_required': ['authored_testbench_and_objectives'],
        'dimensions_scope': 'generic source inventory only; no parameter evaluation or terminal-role inference'}


def inspect_agent_source(source, *, source_id='dut.scs', support_files=None):
    """Inventory supplied hierarchy without a transistor-count or MOS contract."""
    documents = _documents(source, support_files, source_id=source_id)
    inventory = _inventory(documents, source_id, allow_pdk=True)
    return _requirements(documents[source_id], source_id, inventory)


def _binding(value):
    required = {'discovery_id', 'candidate_id', 'sections', 'model_names', 'pdk_sha256'}
    optional = {'entry_sha256', 'tmi_candidate_id', 'audit_sha256'}
    if type(value) is not dict or not required <= set(value) or set(value) - required - optional:
        _invalid('Supply the explicit selected PDK binding fields.')
    for field in ('discovery_id', 'candidate_id'):
        if type(value[field]) is not str or not 1 <= len(value[field]) <= 160 or not re.fullmatch(r'[A-Za-z0-9_:+.-]+', value[field]):
            _invalid('PDK identities must be bounded opaque identifiers.')
    for field in ('pdk_sha256', 'entry_sha256', 'audit_sha256'):
        if field in value and (type(value[field]) is not str or not HASH.fullmatch(value[field])):
            _invalid('PDK byte and audit digests must be exact SHA-256 values.')
    for field in ('sections', 'model_names'):
        rows = value[field]
        if type(rows) is not list or len(rows) > 512:
            _invalid('PDK selections must be bounded unique lists.')
        if any(row is not None and (type(row) is not str or not NAME.fullmatch(row)) for row in rows):
            _invalid('PDK sections and model names must be literal identifiers.')
        if len(rows) != len(set(rows)):
            _invalid('PDK selections cannot contain duplicate values.')
    if not value['sections'] or any(row is None for row in value['model_names']):
        _invalid('Select explicit sections and literal model names.')
    if value.get('tmi_candidate_id') is not None and (type(value['tmi_candidate_id']) is not str or
            not re.fullmatch(r'[A-Za-z0-9_:+.-]{1,160}', value['tmi_candidate_id'])):
        _invalid('Use an observed opaque TMI candidate identity.')
    return json.loads(_canonical(value))


def validate_agent_tb(*, source, testbench, support_files=None, criteria, pdk_binding):
    """Bind exact authored bytes and criteria to a finite static-check manifest.

    ``pdk_sha256`` identifies the caller-generated managed ``pdk.scs`` wrapper,
    not an entire PDK. Actual model closure verification and human review remain
    the caller's responsibility; this function accepts neither paths nor code
    to execute for those operations.
    """
    documents = _documents(source, support_files, testbench)
    if type(criteria) is not dict or not criteria:
        _invalid('Supply an explicit nonempty JSON object containing the authored scientific criteria.')
    _json_data(criteria)
    criteria_raw = _canonical(criteria)
    binding = _binding(pdk_binding)
    inventory = _inventory(documents, 'tb.scs', allow_pdk=True)
    issues = inventory['issues']
    if 'dut.scs' not in inventory['visited']:
        _issue(issues, 'managed_dut_not_included', 'tb.scs')
    if inventory['required_masters'] and 'pdk.scs' not in inventory['visited']:
        _issue(issues, 'managed_pdk_not_included', 'tb.scs')
    for master in set(inventory['required_masters']) - set(binding['model_names']):
        _issue(issues, 'pdk_model_binding_missing', 'pdk.scs', master=master)
    analyses = [row for data in inventory['documents'].values() for row in data['analyses']]
    saves = [row for data in inventory['documents'].values() for row in data['saves']]
    if not analyses:
        _issue(issues, 'supported_analysis_missing', 'tb.scs')
    if not saves:
        _issue(issues, 'explicit_save_missing', 'tb.scs')
    # A declared interface is checked against authored bytes; no interface is
    # inferred when the caller deliberately supplies a flat DUT.
    if criteria.get('schema') == 'ic.agent_testbench.v1':
        if type(criteria.get('text')) is not str or criteria['text'].encode('utf-8') != documents['tb.scs']:
            _issue(issues, 'authored_testbench_mismatch', 'tb.scs')
        interface = criteria.get('interface')
        if type(interface) is not dict or set(interface) != {'subckt', 'instance', 'port_bindings'}:
            _issue(issues, 'authored_interface_missing', 'tb.scs')
        elif interface['subckt'] is not None or interface['instance'] is not None:
            matches = [row for row in inventory['interfaces'] if row['name'] == interface['subckt'] and not row['scope']]
            calls = [row for row in inventory['instances'] if row['name'] == interface['instance'] and
                     row['master'] == interface['subckt'] and not row['scope']]
            if len(matches) != 1 or len(calls) != 1 or type(interface['port_bindings']) is not dict:
                _issue(issues, 'authored_interface_not_resolved', 'tb.scs')
            elif dict(zip(matches[0]['ports'], calls[0]['ordered_nets'])) != interface['port_bindings']:
                _issue(issues, 'authored_port_bindings_mismatch', 'tb.scs')
    manifest = {'schema': MANIFEST_SCHEMA, 'mode': 'agent-tb', 'entrypoint': 'tb.scs',
        'source_sha256': _sha(documents['dut.scs']), 'tb_sha256': _sha(documents['tb.scs']),
        'files': [{'path': path, 'sha256': _sha(raw), 'bytes': len(raw),
                   'role': 'dut' if path == 'dut.scs' else 'testbench' if path == 'tb.scs' else 'support',
                   'reachable': path in inventory['visited']} for path, raw in sorted(documents.items())],
        'managed_pdk': {'path': 'pdk.scs', 'sha256': binding['pdk_sha256'],
                        'scope': 'caller-generated wrapper bytes; not the entire PDK'},
        'criteria_sha256': _sha(criteria_raw), 'pdk_binding': binding,
        'pdk_binding_sha256': _sha(_canonical(binding)), 'dependency_closure': inventory['includes'],
        'required_masters': inventory['required_masters'],
        'scientific_intent_inferred': False, 'source_rewritten': False,
        'simulation_verified': False, 'human_review_required': True}
    manifest['manifest_sha256'] = _sha(_canonical(manifest))
    return {'schema': VALIDATION_SCHEMA, 'mode': 'agent-tb',
        'status': 'blocked' if issues else 'static_ready', 'missing': issues,
        'manifest': manifest, 'requirements': _requirements(documents['dut.scs'], 'dut.scs', inventory),
        'analyses': analyses, 'saves': saves, 'execution_authorized': False,
        'supported_scope': 'finite Spectre statements; tran/ac/dc, explicit save and supplied relative includes; no AHDL, scripts or dynamic topology'}

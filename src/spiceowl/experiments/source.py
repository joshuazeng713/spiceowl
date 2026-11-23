"""Bounded, non-evaluating Spectre source selection. No filesystem or PDK I/O."""
import ast
import hashlib
import math
import re

from spiceowl.domain.common import DomainError

MAX_SOURCE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_FILES = 64
MAX_STATEMENTS = 10000
MAX_INSTANCES = 4096
ID = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,95}')
NODE = re.compile(r'(?:0|[A-Za-z_][A-Za-z0-9_]{0,95})')
PRIMITIVES = {'resistor': 2, 'capacitor': 2, 'inductor': 2, 'vsource': 2,
              'isource': 2, 'vcvs': 4, 'vccs': 4, 'relay': 4}
NUMBER = re.compile(r'(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?:meg|[afpnumkKMGTP])?')


class ExperimentIssue(DomainError):
    def __init__(self, field, code, message):
        super().__init__(message, code)
        self.field = field

    def record(self):
        return {'field': self.field, 'code': self.code, 'message': str(self)}


def require(condition, field, code, message):
    if not condition:
        raise ExperimentIssue(field, code, message)


def identifier(value, field='selection'):
    require(isinstance(value, str) and bool(ID.fullmatch(value)), field, 'invalid_identifier',
            'Use an explicit bounded Spectre identifier without expressions or escapes.')
    return value


def source_id(value):
    require(isinstance(value, str) and 1 <= len(value) <= 240 and all(
        re.fullmatch(r'[A-Za-z0-9_.-]+', part) and part not in ('.', '..') for part in value.split('/')),
        'sources', 'invalid_source_id', 'Source IDs must be explicit relative identifiers, not filesystem requests.')
    return value


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def expression_names(value):
    """Accept arithmetic syntax only. Never eval source expressions or functions."""
    require(0 < len(value) <= 512, 'source.parameters', 'unsupported_expression', 'Parameter expression exceeds supported bounds.')
    pieces, names, position = [], set(), 0
    while position < len(value):
        if value[position].isspace():
            position += 1
            continue
        number = NUMBER.match(value, position)
        name = re.match(r'[A-Za-z_][A-Za-z0-9_]*', value[position:])
        if number:
            literal = re.sub(r'(?:meg|[afpnumkKMGTP])$', '', number[0])
            require(math.isfinite(float(literal)), 'source.parameters', 'nonfinite_source_value',
                    'Nonfinite source parameter values are unsupported.')
            pieces.append('1')
            position = number.end()
        elif name:
            token = name[0]
            names.add(token)
            pieces.append(token)
            position += len(token)
        elif value[position] in '+-*/()':
            pieces.append(value[position])
            position += 1
        else:
            raise ExperimentIssue('source.parameters', 'unsupported_expression', 'Only bounded numeric/arithmetic parameter expressions are supported.')
    try:
        tree = ast.parse(' '.join(pieces), mode='eval')
    except (SyntaxError, RecursionError):
        raise ExperimentIssue('source.parameters', 'unsupported_expression', 'Unsupported parameter expression.') from None
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Load, ast.Constant,
               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.UAdd, ast.USub)
    require(all(isinstance(node, allowed) for node in ast.walk(tree)), 'source.parameters',
            'unsupported_expression', 'Function calls and executable parameter expressions are not supported.')
    return names


def assignments(text):
    if not text.strip():
        return {}, set()
    starts = list(re.finditer(r'(?<!\S)([A-Za-z_][A-Za-z0-9_]*)\s*=', text))
    require(bool(starts) and not text[:starts[0].start()].strip(), 'source.parameters',
            'unsupported_parameters', 'Expected explicit named instance parameters.')
    result, names = {}, set()
    for i, match in enumerate(starts):
        name = match[1]
        require(name not in result, 'source.parameters', 'duplicate_parameter', 'Duplicate source parameter assignment.')
        value = text[match.end():starts[i+1].start() if i+1 < len(starts) else len(text)].strip()
        # A small finite primitive vocabulary is allowed as literal parameter data.
        if name == 'type' and value in ('dc', 'pulse', 'sine'):
            used = set()
        else:
            used = expression_names(value)
        result[name] = value
        names.update(used)
    return result, names


def _statements(raw, sid):
    lines = raw.splitlines(keepends=True)
    offset, start, pending, body = 0, None, [], []
    for lineno, line in enumerate(lines, 1):
        text = line.decode('utf-8')
        syntax = text.split('//', 1)[0].strip()
        if not syntax and start is None:
            offset += len(line)
            continue
        if start is None:
            start = (lineno, offset)
        pending.append(line)
        continuation = syntax.endswith('\\')
        body.append(syntax[:-1] if continuation else syntax)
        offset += len(line)
        if continuation:
            continue
        logical = ' '.join(body).strip()
        require(len(logical) <= 65536, 'sources', 'source_limit', 'Logical source statement exceeds its bound.')
        yield {'syntax': logical, 'raw': b''.join(pending), 'line_start': start[0], 'line_end': lineno,
               'byte_start': start[1], 'byte_end': offset, 'source_id': sid}
        start, pending, body = None, [], []
    require(start is None, 'sources', 'incomplete_continuation', 'Source ends inside a continued statement.')


def _instance(statement):
    match = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*)\s*\(([^()]*)\)\s*([A-Za-z_][A-Za-z0-9_]*)(?:\s+(.*))?', statement['syntax'])
    require(match is not None, 'sources', 'unsupported_statement', 'Only explicit subcircuits, named parameters and parenthesized instances are supported.')
    terminals = match[2].split()
    require(1 <= len(terminals) <= 128 and all(NODE.fullmatch(n) for n in terminals),
            'sources', 'unsupported_terminals', 'Unsupported or ambiguous source terminal list.')
    params, names = assignments(match[4] or '')
    return {**statement, 'name': identifier(match[1]), 'master': identifier(match[3]),
            'terminals': terminals, 'parameters': params, 'parameter_names': names}


def parse_sources(sources):
    """Read supplied bytes only; return exact ranges and a unique subckt index."""
    require(isinstance(sources, dict) and 1 <= len(sources) <= MAX_FILES, 'sources', 'missing_sources',
            'Supply a bounded explicit mapping of source IDs to source bytes.')
    total = 0
    index, documents = {}, {}
    for sid, raw in sorted(sources.items()):
        source_id(sid)
        require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_SOURCE_BYTES, 'sources', 'source_limit',
                'Each source must be nonempty bytes within the source-size bound.')
        total += len(raw)
        require(total <= MAX_TOTAL_BYTES, 'sources', 'source_limit', 'Source closure exceeds its total size bound.')
        try:
            text = raw.decode('utf-8')
        except UnicodeError:
            raise ExperimentIssue('sources', 'source_encoding', 'Source must be valid UTF-8.') from None
        require(all(ord(c) >= 32 or c in '\t\n\r' for c in text), 'sources', 'source_encoding', 'Source contains unsupported control characters.')
        current = None
        document = {'source_id': sid, 'sha256': sha(raw), 'bytes': len(raw), 'includes': [], 'global_parameters': {}}
        documents[sid] = document
        statements = list(_statements(raw, sid))
        require(len(statements) <= MAX_STATEMENTS, 'sources', 'source_limit', 'Source statement count exceeds its bound.')
        for statement in statements:
            syntax = statement['syntax']
            if not syntax:
                continue
            if syntax.startswith('subckt '):
                require(current is None, 'sources', 'nested_subcircuit', 'Nested subcircuit definitions are unsupported.')
                match = re.fullmatch(r'subckt\s+([A-Za-z_][A-Za-z0-9_]*)\s+(.*)', syntax)
                require(match is not None, 'sources', 'unsupported_subcircuit', 'An explicit subcircuit port list is required.')
                name, port_text = match.groups()
                if port_text.startswith('(') and port_text.endswith(')'):
                    port_text = port_text[1:-1]
                ports = port_text.split()
                require(name not in index and 1 <= len(ports) <= 128 and len(set(ports)) == len(ports)
                        and all(ID.fullmatch(n) for n in ports), 'sources', 'ambiguous_subcircuit',
                        'Subcircuit definitions and port names must be unique and explicit.')
                current = {'name': name, 'ports': ports, 'instances': [], 'parameters': {},
                           'parameter_names': set(), 'source_id': sid, 'begin': statement}
                index[name] = current
            elif syntax.startswith('ends'):
                require(current is not None and syntax in ('ends', 'ends ' + current['name']), 'sources',
                        'subcircuit_end_mismatch', 'Subcircuit end must match its explicit definition.')
                begin = current.pop('begin')
                current.update(raw=raw[begin['byte_start']:statement['byte_end']],
                    line_start=begin['line_start'], line_end=statement['line_end'],
                    byte_start=begin['byte_start'], byte_end=statement['byte_end'])
                current = None
            elif syntax.startswith('parameters '):
                values, names = assignments(syntax[len('parameters '):])
                target = current['parameters'] if current else document['global_parameters']
                require(not set(values) & set(target), 'source.parameters', 'duplicate_parameter', 'Duplicate parameter definition.')
                target.update(values)
                if current:
                    current['parameter_names'].update(names)
            elif current is not None:
                instance = _instance(statement)
                require(instance['name'] not in {i['name'] for i in current['instances']}, 'sources',
                        'duplicate_instance', 'An instance name repeats within the selected subcircuit.')
                current['instances'].append(instance)
                current['parameter_names'].update(instance['parameter_names'])
                require(len(current['instances']) <= MAX_INSTANCES, 'sources', 'source_limit', 'Subcircuit exceeds the instance bound.')
            elif syntax in ('simulator lang=spectre', 'global 0'):
                continue
            elif syntax.startswith('include '):
                match = re.fullmatch(r'include\s+"([A-Za-z0-9_./@-]+)"(?:\s+section=([A-Za-z_][A-Za-z0-9_]*))?', syntax)
                require(match is not None, 'sources', 'unsupported_include', 'Only explicit local or external-alias include metadata is supported.')
                name = match[1]
                if not re.fullmatch(r'@EXTERNAL_[A-Z][A-Z0-9_]{0,63}@', name):
                    source_id(name)
                document['includes'].append({'reference': name, 'section': match[2]})
            else:
                raise ExperimentIssue('sources', 'unsupported_top_level', 'Supply subcircuit definitions rather than a runnable top-level deck or behavioral code.')
        require(current is None, 'sources', 'unterminated_subcircuit', 'Source contains an unterminated subcircuit.')
    return {'subcircuits': index, 'documents': documents}


def _evidence(item, documents):
    return {'source_id': item['source_id'], 'source_sha256': documents[item['source_id']]['sha256'],
            'line_start': item['line_start'], 'line_end': item['line_end'],
            'byte_start': item['byte_start'], 'byte_end': item['byte_end'],
            'sha256': sha(item['raw']), 'original_text': item['raw'].decode('utf-8')}


def extract_circuit(sources, selection, model_profile):
    """Select a declared cell closure or explicit cutout; preserve all raw bytes."""
    require(isinstance(selection, dict), 'selection', 'missing_selection', 'An explicit source selection is required.')
    mode = selection.get('mode')
    expected = {'mode', 'source_id', 'subcircuit'} | ({'instances', 'ports'} if mode == 'instances' else set())
    require(mode in ('cell', 'instances') and set(selection) == expected, 'selection', 'invalid_selection',
            'Select a whole cell or explicit instance cutout with exact selection fields.')
    parsed = parse_sources(sources)
    index, documents = parsed['subcircuits'], parsed['documents']
    sid, name = source_id(selection['source_id']), identifier(selection['subcircuit'])
    require(name in index and index[name]['source_id'] == sid, 'selection', 'source_identity_missing',
            'The selected subcircuit must belong to the explicitly selected source.')
    cell = index[name]
    require(cell['instances'], 'selection', 'empty_subcircuit', 'An empty interface is not an executable circuit.')
    if mode == 'cell':
        instances, ports, dut = cell['instances'], cell['ports'], name
    else:
        requested, ports = selection['instances'], selection['ports']
        require(isinstance(requested, list) and 1 <= len(requested) <= 128 and len(set(requested)) == len(requested)
                and all(isinstance(i, str) and ID.fullmatch(i) for i in requested), 'selection.instances',
                'invalid_instances', 'Provide distinct explicit instance names.')
        require(isinstance(ports, list) and 1 <= len(ports) <= 128 and len(set(ports)) == len(ports)
                and all(isinstance(n, str) and ID.fullmatch(n) for n in ports), 'selection.ports',
                'invalid_ports', 'Cutout ports must be distinct explicit node names.')
        instances = [i for i in cell['instances'] if i['name'] in requested]
        require(len(instances) == len(requested), 'selection.instances', 'instance_missing', 'A selected instance is absent from that subcircuit.')
        require(set(ports) <= {n for i in instances for n in i['terminals']}, 'selection.ports', 'unused_port', 'Every cutout port must occur on a selected instance.')
        dut = 'SpiceOwl_DUT'
        require(dut not in index, 'selection', 'reserved_subcircuit', 'The source uses the reserved cutout wrapper name.')
    known = model_profile.get('models', {})
    used_models, reachable, visiting, required_parameters = set(), [], set(), set()
    def visit_instances(items, locals_):
        for item in items:
            required_parameters.update(item['parameter_names'] - locals_)
            master = item['master']
            if master in index:
                require(len(item['terminals']) == len(index[master]['ports']), 'sources', 'terminal_count_mismatch', 'Instance terminal count differs from the declared subcircuit.')
                visit(master)
            elif master in known:
                require(len(item['terminals']) == len(known[master]['terminals']), 'model_profile.models', 'terminal_count_mismatch', 'Instance terminal count differs from the registered model profile.')
                used_models.add(master)
            elif master in PRIMITIVES:
                require(len(item['terminals']) == PRIMITIVES[master], 'sources', 'terminal_count_mismatch', 'Primitive terminal count is unsupported.')
            else:
                raise ExperimentIssue('model_profile.models.' + master, 'model_contract_missing', 'An explicit model contract or child subcircuit is required; names do not establish device type.')
    def visit(current):
        require(current not in visiting, 'sources', 'recursive_subcircuit', 'Recursive subcircuits are not supported.')
        if current in reachable:
            return
        require(len(visiting) < 32, 'sources', 'closure_limit', 'Subcircuit nesting exceeds its bound.')
        visiting.add(current)
        child = index[current]
        require(child['instances'], 'sources', 'empty_subcircuit', 'The source closure contains an empty interface.')
        locals_ = set(child['parameters'])
        required_parameters.update(child['parameter_names'] - locals_)
        visit_instances(child['instances'], locals_)
        visiting.remove(current)
        reachable.append(current)
    if mode == 'cell':
        visit(name)
        raw = b''.join(index[n]['raw'] + (b'' if index[n]['raw'].endswith(b'\n') else b'\n') for n in reachable)
    else:
        # Parent defaults are not silently copied into a cutout. Any variable
        # reference must be supplied explicitly by the new registered recipe.
        visit_instances(instances, set())
        raw = b''.join(index[n]['raw'] + (b'' if index[n]['raw'].endswith(b'\n') else b'\n') for n in reachable)
        raw += ('subckt ' + dut + ' ' + ' '.join(ports) + '\n').encode()
        for item in instances:
            raw += item['raw'] + (b'' if item['raw'].endswith(b'\n') else b'\n')
        raw += ('ends ' + dut + '\n').encode()
    chosen_sources = sorted({sid} | {index[n]['source_id'] for n in reachable})
    provenance = {'mode': mode, 'source_id': sid, 'subcircuit': name, 'ports': list(ports),
                  'sources': [documents[s] for s in chosen_sources],
                  'instances': [{'instance': i['name'], 'master': i['master'], 'terminals': i['terminals'],
                                 'parameters_literal': i['parameters'], **_evidence(i, documents)} for i in instances],
                  'closure': [{'subcircuit': n, **_evidence(index[n], documents)} for n in reachable],
                  'source_directives_emitted': False}
    return {'raw': raw, 'subcircuit': dut, 'ports': list(ports), 'instances': instances,
            'models': sorted(used_models), 'required_parameters': sorted(required_parameters),
            'provenance': provenance}

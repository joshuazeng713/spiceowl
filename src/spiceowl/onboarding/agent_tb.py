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
VA_STANDARD_INCLUDES = {'constants.vams', 'disciplines.vams'}
VA_ALIAS = re.compile(r'[A-Z][A-Z0-9_]{0,63}\Z')
VA_IDENTIFIER = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,127}\Z')
VA_FUNCTIONS = {'V', 'I', 'transition', 'cross', 'timer', 'above', 'initial_step',
    'final_step', 'ddt', 'idt', 'abs', 'min', 'max', 'sqrt', 'pow', 'exp', 'ln',
    'log', 'log10', 'sin', 'cos', 'tan', 'asin', 'acos', 'atan', 'atan2', 'sinh',
    'cosh', 'tanh', 'asinh', 'acosh', 'atanh', 'floor', 'ceil', 'limexp', 'hypot', '$vt'}
VA_CONSTANTS = {'M_E', 'M_LOG2E', 'M_LOG10E', 'M_LN2', 'M_LN10', 'M_PI',
    'M_TWO_PI', 'M_PI_2', 'M_PI_4', 'M_1_PI', 'M_2_PI', 'M_2_SQRTPI',
    'M_SQRT2', 'M_SQRT1_2', 'P_Q', 'P_C', 'P_K', 'P_H', 'P_EPS0', 'P_U0', 'P_CELSIUS0'}


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
        if posixpath.basename(path).lower() in VA_STANDARD_INCLUDES:
            _invalid('Standard Verilog-A headers are controlled external dependencies, not supplied support files.')
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


class _VerilogAGap(Exception):
    """Stop one bounded file at its first unsupported construct."""


def _va_tokens(raw, path, issues):
    """Lex original UTF-8 without expanding macros, includes or executable code."""
    text = raw.decode('utf-8')
    tokens, position, line = [], 0, 1
    pattern = re.compile(r'`[A-Za-z_][A-Za-z0-9_]*|\$?[A-Za-z_][A-Za-z0-9_]*|'
        r'(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?:meg|[afpnumkKMGTP])?|'
        r'<\+|<=|>=|==|!=|&&|\|\||<<|>>|\*\*|[()\[\],;:@?+*/%<>=!~&|^\-]')
    while position < len(text):
        if text[position].isspace():
            line += text[position] == '\n'
            position += 1
            continue
        if text.startswith('//', position):
            end = text.find('\n', position)
            position = len(text) if end < 0 else end
            continue
        if text.startswith('/*', position):
            end = text.find('*/', position + 2)
            if end < 0:
                _issue(issues, 'veriloga_unterminated_comment', path, line)
                return []
            line += text[position:end + 2].count('\n')
            position = end + 2
            continue
        if text.startswith('`include', position) and not re.match(r'\w', text[position + 8:position + 9]):
            end = text.find('\n', position)
            end = len(text) if end < 0 else end
            directive = text[position:end]
            match = re.fullmatch(r'`include\s+"([^"\\\r\n]+)"\s*(?://[^\n]*)?', directive)
            if match is None:
                _issue(issues, 'veriloga_external_or_dynamic_include', path, line)
                return []
            tokens.append(('@include:' + match[1], line))
            position = end
            continue
        match = pattern.match(text, position)
        if match is None:
            _issue(issues, 'veriloga_unsupported_token_or_string', path, line)
            return []
        tokens.append((match[0], line))
        if len(tokens) > MAX_STATEMENTS * 8:
            _issue(issues, 'veriloga_token_limit', path, line)
            return []
        position = match.end()
    return tokens


class _VerilogAParser:
    """Finite scalar behavioral subset; not a compiler or execution sandbox."""
    precedence = {'||': 1, '&&': 2, '|': 3, '^': 4, '&': 5, '==': 6, '!=': 6,
        '<': 7, '>': 7, '<=': 7, '>=': 7, '<<': 8, '>>': 8,
        '+': 9, '-': 9, '*': 10, '/': 10, '%': 10, '**': 11}

    def __init__(self, raw, path, issues):
        self.tokens = _va_tokens(raw, path, issues)
        self.path, self.issues, self.index = path, issues, 0
        self.symbols, self.writable, self.nodes = set(), set(), set()
        self.constant_macros = set()
        self.result = {'includes': [], 'instances': [], 'interfaces': [], 'models': [],
                       'analyses': [], 'saves': [], 'info_requests': [], 'language': 'veriloga', 'standard_includes': []}

    def peek(self):
        return self.tokens[self.index][0] if self.index < len(self.tokens) else ''

    def line(self):
        return self.tokens[min(self.index, len(self.tokens) - 1)][1] if self.tokens else 1

    def gap(self, code='veriloga_unsupported_syntax'):
        _issue(self.issues, code, self.path, self.line())
        raise _VerilogAGap()

    def take(self, expected=None):
        value = self.peek()
        if not value or expected is not None and value != expected:
            self.gap()
        self.index += 1
        return value

    def identifier(self):
        value = self.take()
        if not VA_IDENTIFIER.fullmatch(value):
            self.gap('veriloga_unsupported_identifier')
        return value

    def expression(self, minimum=0, depth=0):
        if depth > 64:
            self.gap('veriloga_expression_depth_limit')
        token = self.take()
        if token in ('+', '-', '!', '~'):
            self.expression(12, depth + 1)
        elif token == '(':
            self.expression(depth=depth + 1)
            self.take(')')
        elif NUMBER.fullmatch(token):
            numeric = re.sub(r'(?:meg|[afpnumkKMGTP])$', '', token)
            if not math.isfinite(float(numeric)):
                self.gap('veriloga_nonfinite_literal')
        elif token.startswith('`'):
            if token[1:] not in VA_CONSTANTS:
                self.gap('veriloga_unsupported_preprocessor')
            self.constant_macros.add(token[1:])
        elif self.peek() == '(':
            if token not in VA_FUNCTIONS:
                self.gap('veriloga_unknown_function_or_system_task')
            self.take('(')
            if self.peek() != ')':
                self.expression(depth=depth + 1)
                while self.peek() == ',':
                    self.take(',')
                    self.expression(depth=depth + 1)
            self.take(')')
        elif token not in self.symbols and token not in ('inf', '$abstime', '$temperature', 'initial_step', 'final_step'):
            self.gap('veriloga_unresolved_symbol_or_system_task')
        while self.peek() in self.precedence and self.precedence[self.peek()] >= minimum:
            operator = self.take()
            priority = self.precedence[operator]
            self.expression(priority if operator == '**' else priority + 1, depth + 1)
        if minimum == 0 and self.peek() == '?':
            self.take('?')
            self.expression(depth=depth + 1)
            self.take(':')
            self.expression(depth=depth + 1)

    def declaration(self, parameter=False):
        if parameter and self.peek() in ('real', 'integer'):
            self.take()
        while True:
            name = self.identifier()
            if name in self.symbols:
                self.gap('veriloga_duplicate_symbol')
            if self.peek() == '=':
                self.take('=')
                self.expression()
            elif parameter:
                self.gap('veriloga_parameter_value_missing')
            self.symbols.add(name)
            if not parameter:
                self.writable.add(name)
            while parameter and self.peek() in ('from', 'exclude'):
                self.take()
                if self.peek() in ('[', '('):
                    self.take()
                    self.expression()
                    self.take(':')
                    self.expression()
                    if self.peek() not in (']', ')'):
                        self.gap()
                    self.take()
                else:
                    self.expression()
            if self.peek() != ',':
                break
            self.take(',')
        self.take(';')

    def statement(self, depth=0):
        if depth > 64:
            self.gap('veriloga_statement_depth_limit')
        token = self.take()
        if token == 'begin':
            if self.peek() == ':':
                self.take(':')
                self.identifier()
            while self.peek() and self.peek() != 'end':
                self.statement(depth + 1)
            self.take('end')
        elif token == 'if':
            self.take('(')
            self.expression()
            self.take(')')
            self.statement(depth + 1)
            if self.peek() == 'else':
                self.take('else')
                self.statement(depth + 1)
        elif token == '@':
            self.take('(')
            self.expression()
            while self.peek() == 'or':
                self.take('or')
                self.expression()
            self.take(')')
            self.statement(depth + 1)
        elif token in ('V', 'I'):
            self.take('(')
            node = self.take()
            if node not in self.nodes:
                self.gap('veriloga_unknown_contribution_node')
            if self.peek() == ',':
                self.take(',')
                if self.take() not in self.nodes:
                    self.gap('veriloga_unknown_contribution_node')
            self.take(')')
            self.take('<+')
            self.expression()
            self.take(';')
        elif token in self.writable:
            self.take('=')
            self.expression()
            self.take(';')
        elif token in ('real', 'integer'):
            self.declaration()
        elif token != ';':
            self.gap('veriloga_unsupported_statement_or_write_task')

    def module(self):
        line = self.line()
        self.take('module')
        name = self.identifier()
        self.take('(')
        ports = [self.identifier()]
        while self.peek() == ',':
            self.take(',')
            ports.append(self.identifier())
        self.take(')')
        self.take(';')
        if len(set(ports)) != len(ports) or len(ports) > MAX_TERMINALS:
            self.gap('veriloga_unsupported_module_interface')
        self.symbols, self.writable, self.nodes = set(ports), set(), set()
        directions, analog_count = {}, 0
        while self.peek() and self.peek() != 'endmodule':
            token = self.take()
            if token in ('input', 'output', 'inout', 'electrical'):
                names = [self.identifier()]
                while self.peek() == ',':
                    self.take(',')
                    names.append(self.identifier())
                self.take(';')
                for item in names:
                    if token == 'electrical':
                        if item in self.nodes or item in self.symbols - set(ports):
                            self.gap('veriloga_duplicate_symbol')
                        self.nodes.add(item)
                        self.symbols.add(item)
                    elif item not in ports or item in directions:
                        self.gap('veriloga_unsupported_module_interface')
                    else:
                        directions[item] = token
            elif token in ('parameter', 'localparam', 'real', 'integer'):
                self.declaration(token in ('parameter', 'localparam'))
            elif token == 'analog':
                analog_count += 1
                self.statement()
            else:
                self.gap('veriloga_unsupported_module_item')
        self.take('endmodule')
        if not set(ports) <= self.nodes or not analog_count:
            self.gap('veriloga_electrical_ports_or_analog_body_missing')
        self.result['interfaces'].append({'name': name, 'ports': ports, 'path': self.path,
            'line': line, 'scope': [], 'kind': 'veriloga_module', 'directions': directions})

    def parse(self):
        try:
            while self.peek():
                token, line = self.peek(), self.line()
                if token.startswith('@include:'):
                    reference = self.take()[len('@include:'):]
                    if not _relative(reference):
                        self.gap('veriloga_external_or_dynamic_include')
                    if reference in VA_STANDARD_INCLUDES:
                        row = {'from': self.path, 'line': line, 'reference': reference,
                               'target': reference, 'kind': 'veriloga_standard_include', 'external': True}
                        self.result['standard_includes'].append(row)
                    elif reference.endswith(('.va', '.vams')):
                        row = {'from': self.path, 'line': line, 'reference': reference,
                               'target': posixpath.join(posixpath.dirname(self.path), reference),
                               'kind': 'veriloga_include'}
                    else:
                        self.gap('veriloga_unsupported_include_kind')
                    self.result['includes'].append(row)
                elif token == 'module':
                    self.module()
                else:
                    self.gap('veriloga_unsupported_top_level_or_preprocessor')
        except _VerilogAGap:
            pass
        self.result['constant_macros'] = sorted(self.constant_macros)
        return self.result


def _parse(raw, path, issues):
    result = {'includes': [], 'instances': [], 'interfaces': [], 'models': [], 'analyses': [], 'saves': [], 'info_requests': []}
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
        if re.match(r'(?:include|ahdl_include)\b', text, re.I):
            if stack:
                _issue(issues, 'scoped_include_unsupported', path, line)
            match = re.fullmatch(r'(include|ahdl_include)\s+"([^"\r\n]+)"', text, re.I)
            if match is None or not _relative(match[2]):
                _issue(issues, 'external_or_dynamic_include', path, line)
            else:
                kind, reference = match[1].lower(), match[2]
                if kind == 'ahdl_include' and not reference.endswith('.va'):
                    _issue(issues, 'unsupported_ahdl_include_kind', path, line)
                elif kind == 'include' and reference.endswith(('.va', '.vams')):
                    _issue(issues, 'veriloga_requires_ahdl_include', path, line)
                else:
                    target = posixpath.join(posixpath.dirname(path), reference)
                    result['includes'].append({'from': path, 'line': line, 'reference': reference, 'target': target, 'kind': kind})
            continue
        if any(char in text for char in '`;{}!') or re.match(r'(?:\.include|\.lib|shell|system|exec|if|else|alter|foreach)\b', text, re.I):
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
        info = re.fullmatch(r'(\S+)\s+info(?:\s+(.*))?', text, re.I)
        if info:
            name, tail = info.groups()
            if stack or not NAME.fullmatch(name):
                _issue(issues, 'unsupported_info_request_scope', path, line)
                continue
            values = _parameters(tail or '', path, line, issues)
            if values != {'what': 'oppoint', 'where': 'rawfile'}:
                _issue(issues, 'unsupported_info_request', path, line)
            else:
                result['info_requests'].append({'name': name, 'what': 'oppoint',
                    'where': 'rawfile', 'path': path, 'line': line})
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
    languages = {}
    def visit(path, language='spectre'):
        if path in languages and languages[path] != language:
            _issue(issues, 'include_language_mismatch', path)
            return
        languages[path] = language
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
        data = (_VerilogAParser(documents[path], path, issues).parse() if language == 'veriloga'
                else _parse(documents[path], path, issues))
        parsed[path] = data
        for edge in data['includes']:
            edges.append(edge)
            if not edge.get('external'):
                visit(edge['target'], 'veriloga' if edge.get('kind') in ('ahdl_include', 'veriloga_include') else 'spectre')
        visiting.remove(path)
        visited.add(path)
    visit(entry)
    for path in sorted(set(documents) - visited):
        _issue(issues, 'unreferenced_supplied_file', path)
    interfaces = [row for data in parsed.values() for row in data['interfaces']]
    modules = [row for row in interfaces if row.get('kind') == 'veriloga_module']
    external = [edge for edge in edges if edge.get('external')]
    # Separate ahdl_include entries are separate compiler inputs: a standard
    # include in another entry cannot establish this entry's dependency closure.
    for root in sorted({edge['target'] for edge in edges if edge.get('kind') == 'ahdl_include'}):
        closure, pending = set(), [root]
        while pending:
            path = pending.pop()
            if path in closure:
                continue
            closure.add(path)
            pending.extend(edge['target'] for edge in edges if edge['from'] == path and edge.get('kind') == 'veriloga_include')
        standards = {row['reference'] for row in external if row['from'] in closure}
        data = [parsed[path] for path in closure if path in parsed]
        if any(row['interfaces'] for row in data) and 'disciplines.vams' not in standards:
            _issue(issues, 'veriloga_disciplines_dependency_missing', root)
        if any(row.get('constant_macros') for row in data) and 'constants.vams' not in standards:
            _issue(issues, 'veriloga_constants_dependency_missing', root)
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
            code = 'veriloga_module_port_count_mismatch' if visible[0].get('kind') == 'veriloga_module' else 'subcircuit_port_count_mismatch'
            _issue(issues, code, instance['path'], instance['line'], master=instance['master'])
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
    info_requests = [row for data in parsed.values() for row in data['info_requests']]
    analysis_names = {row['name'] for data in parsed.values() for row in data['analyses']}
    output_names = set()
    for row in info_requests:
        if row['name'] in output_names or row['name'] in analysis_names:
            _issue(issues, 'ambiguous_info_request_name', row['path'], row['line'], name=row['name'])
        output_names.add(row['name'])
    return {'issues': issues, 'documents': parsed, 'visited': sorted(visited), 'includes': edges,
            'instances': instances, 'interfaces': interfaces, 'required_masters': sorted(required),
            'veriloga_modules': modules, 'veriloga_external_dependencies': external,
            'info_requests': info_requests}


def _requirements(raw, source_id, inventory):
    return {'schema': REQUIREMENTS_SCHEMA, 'mode': 'agent-tb', 'source_id': source_id,
        'source_sha256': _sha(raw), 'status': 'partial' if inventory['issues'] else 'parsed',
        'instances': inventory['instances'], 'interfaces': inventory['interfaces'],
        'required_masters': inventory['required_masters'], 'includes': inventory['includes'],
        'veriloga_modules': inventory['veriloga_modules'],
        'veriloga_external_dependencies': inventory['veriloga_external_dependencies'],
        'info_requests': inventory['info_requests'],
        'issues': inventory['issues'], 'confirmation_required': ['authored_testbench_and_objectives'],
        'dimensions_scope': 'generic source inventory only; no parameter evaluation or terminal-role inference'}


def inspect_agent_source(source, *, source_id='dut.scs', support_files=None):
    """Inventory supplied hierarchy without a transistor-count or MOS contract."""
    documents = _documents(source, support_files, source_id=source_id)
    inventory = _inventory(documents, source_id, allow_pdk=True)
    return _requirements(documents[source_id], source_id, inventory)


def _binding(value):
    """Validate one static identity branch; neither grants execution authority."""
    if type(value) is not dict:
        _invalid('Supply the explicit selected PDK binding fields.')
    scoped = 'scope_selection_id' in value or 'library_id' in value
    required = {'sections', 'model_names', 'pdk_sha256'}
    optional = {'entry_sha256', 'audit_sha256', 'veriloga_standard_includes', 'veriloga_header_closure'}
    digests = ('pdk_sha256', 'entry_sha256', 'audit_sha256')
    if scoped:
        required |= {'scope_selection_id', 'library_id'}
        optional.add('tmi_option_id')
        digests += ('scope_selection_id', 'library_id', 'tmi_option_id')
    else:
        required |= {'discovery_id', 'candidate_id'}
        optional.add('tmi_candidate_id')
    if not required <= set(value) or set(value) - required - optional:
        _invalid('Supply exactly one explicit PDK binding identity branch.')
    if not scoped:
        for field in ('discovery_id', 'candidate_id'):
            if type(value[field]) is not str or not 1 <= len(value[field]) <= 160 or not re.fullmatch(r'[A-Za-z0-9_:+.-]+', value[field]):
                _invalid('PDK identities must be bounded opaque identifiers.')
    for field in digests:
        if field in value and (type(value[field]) is not str or not HASH.fullmatch(value[field])):
            _invalid('PDK scope, byte and audit digests must be exact SHA-256 values.')
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
    if not scoped and value.get('tmi_candidate_id') is not None and (type(value['tmi_candidate_id']) is not str or
            not re.fullmatch(r'[A-Za-z0-9_:+.-]{1,160}', value['tmi_candidate_id'])):
        _invalid('Use an observed opaque TMI candidate identity.')
    if 'veriloga_standard_includes' in value:
        rows = value['veriloga_standard_includes']
        if type(rows) is not list or len(rows) > len(VA_STANDARD_INCLUDES):
            _invalid('Bind only the bounded standard Verilog-A include inventory.')
        for row in rows:
            if (type(row) is not dict or set(row) != {'name', 'alias', 'sha256'} or
                    type(row['name']) is not str or row['name'] not in VA_STANDARD_INCLUDES or
                    type(row['alias']) is not str or not VA_ALIAS.fullmatch(row['alias']) or
                    type(row['sha256']) is not str or not HASH.fullmatch(row['sha256'])):
                _invalid('Standard Verilog-A includes require a known name, registered alias and exact observed SHA-256; never a raw path.')
        if len({row['name'] for row in rows}) != len(rows) or len({row['alias'] for row in rows}) != len(rows):
            _invalid('Standard Verilog-A include names and aliases must be unique.')
    if 'veriloga_header_closure' in value:
        closure = value['veriloga_header_closure']
        if (type(closure) is not dict or set(closure) != {'inventory_sha256', 'files', 'includes'} or
                type(closure['inventory_sha256']) is not str or not HASH.fullmatch(closure['inventory_sha256']) or
                type(closure['files']) is not list or not 1 <= len(closure['files']) <= MAX_FILES or
                type(closure['includes']) is not list or len(closure['includes']) > MAX_FILES * 8):
            _invalid('Standard header closure needs an observed inventory digest and bounded explicit files/includes.')
        for row in closure['files']:
            if (type(row) is not dict or set(row) != {'name', 'alias', 'sha256'} or not _relative(row['name']) or
                    row['name'].lower() in ('dut.scs', 'tb.scs', 'pdk.scs') or
                    type(row['alias']) is not str or not VA_ALIAS.fullmatch(row['alias']) or
                    type(row['sha256']) is not str or not HASH.fullmatch(row['sha256'])):
                _invalid('Header closure files require distinct relative names, controlled aliases and SHA-256; no raw paths.')
        for row in closure['includes']:
            if (type(row) is not dict or not {'from', 'reference', 'target'} <= set(row) or
                    set(row) - {'from', 'reference', 'target', 'line'} or
                    any(not _relative(row[field]) for field in ('from', 'reference', 'target')) or
                    'line' in row and (type(row['line']) is not int or row['line'] < 1)):
                _invalid('Header closure edges must use literal relative references and bounded source locations.')
    return json.loads(_canonical(value))


def _check_header_closure(binding, required_standards, issues):
    entries = {row['name']: row for row in binding.get('veriloga_standard_includes', [])}
    closure = binding.get('veriloga_header_closure')
    if required_standards and closure is None:
        _issue(issues, 'veriloga_header_closure_missing')
        return
    if closure is None:
        return
    if not entries:
        _issue(issues, 'veriloga_header_closure_unused')
    files = closure['files']
    by_name = {row['name']: row for row in files}
    if len({row['name'].lower() for row in files}) != len(files) or len({row['alias'] for row in files}) != len(files):
        _issue(issues, 'veriloga_header_closure_ambiguous_file')
    for name, entry in entries.items():
        if by_name.get(name) != entry:
            _issue(issues, 'veriloga_standard_header_closure_mismatch', name)
    graph = {name: set() for name in by_name}
    for edge in closure['includes']:
        if edge['from'] not in graph or edge['target'] not in graph:
            _issue(issues, 'veriloga_header_closure_file_missing', edge['from'])
            continue
        if posixpath.join(posixpath.dirname(edge['from']), edge['reference']) != edge['target']:
            _issue(issues, 'veriloga_header_closure_resolution_mismatch', edge['from'])
            continue
        graph[edge['from']].add(edge['target'])
    visited, active = set(), set()
    def visit(name):
        if name in active:
            _issue(issues, 'veriloga_header_closure_cycle', name)
            return
        if name in visited or name not in graph:
            return
        active.add(name)
        for target in sorted(graph[name]):
            visit(target)
        active.remove(name)
        visited.add(name)
    for name in sorted(entries):
        visit(name)
    for name in sorted(set(graph) - visited):
        _issue(issues, 'veriloga_header_closure_file_unused', name)


def validate_agent_tb(*, source, testbench, support_files=None, criteria, pdk_binding):
    """Bind exact authored bytes and criteria to a finite static-check manifest.

    ``pdk_sha256`` identifies the caller-generated managed ``pdk.scs`` wrapper,
    not an entire PDK. Actual model closure verification and human review remain
    the caller's responsibility; this function accepts neither paths nor code
    to execute for those operations. A cached scope selection and its library
    identity are an alternative to a discovery/candidate identity, not proof of
    a fresh dependency audit or permission to run.
    """
    documents = _documents(source, support_files, testbench)
    if type(criteria) is not dict or not criteria:
        _invalid('Supply an explicit nonempty JSON object containing the authored scientific criteria.')
    _json_data(criteria)
    criteria_raw = _canonical(criteria)
    binding = _binding(pdk_binding)
    inventory = _inventory(documents, 'tb.scs', allow_pdk=True)
    issues = inventory['issues']
    standard_bindings = {row['name']: row for row in binding.get('veriloga_standard_includes', [])}
    required_standards = {row['reference'] for row in inventory['veriloga_external_dependencies']}
    for name in sorted(required_standards - set(standard_bindings)):
        _issue(issues, 'veriloga_standard_include_binding_missing', name)
    for name in sorted(set(standard_bindings) - required_standards):
        _issue(issues, 'veriloga_standard_include_binding_unused', name)
    _check_header_closure(binding, required_standards, issues)
    external_dependencies = [{**row, 'binding': standard_bindings.get(row['reference']),
        'verification': 'requires_runner_dependency_audit',
        'resolution_policy': 'runner_audited_staged_standard_include'}
        for row in inventory['veriloga_external_dependencies']]
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
    support = {path: raw for path, raw in documents.items() if path not in ('dut.scs', 'tb.scs')}
    declared_helpers = set()
    # A declared interface is checked against authored bytes; no interface is
    # inferred when the caller deliberately supplies a flat DUT.
    if criteria.get('schema') == 'ic.agent_testbench.v1':
        if type(criteria.get('text')) is not str or criteria['text'].encode('utf-8') != documents['tb.scs']:
            _issue(issues, 'authored_testbench_mismatch', 'tb.scs')
        if 'helper_files' in criteria:
            helpers = criteria['helper_files']
            if (type(helpers) is not dict or not set(helpers) <= set(support) or
                    any(type(text) is not str or text.encode('utf-8') != support[path]
                        for path, text in helpers.items() if path in support)):
                _issue(issues, 'authored_helper_files_mismatch', 'tb.scs')
            if type(helpers) is dict:
                declared_helpers = set(helpers)
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
    # The compiler accepts a flat union, but its two origins remain distinct:
    # reviewed authored helpers and the original DUT's own include closure.
    dut_closure, pending = set(), ['dut.scs']
    while pending:
        path = pending.pop()
        if path in dut_closure:
            continue
        dut_closure.add(path)
        pending.extend(edge['target'] for edge in inventory['includes']
                       if edge['from'] == path and not edge.get('external'))
    for path in sorted(set(support) - declared_helpers - dut_closure):
        _issue(issues, 'undeclared_testbench_support_file', path)
    manifest = {'schema': MANIFEST_SCHEMA, 'mode': 'agent-tb', 'entrypoint': 'tb.scs',
        'source_sha256': _sha(documents['dut.scs']), 'tb_sha256': _sha(documents['tb.scs']),
        'files': [{'path': path, 'sha256': _sha(raw), 'bytes': len(raw),
                   'role': 'dut' if path == 'dut.scs' else 'testbench' if path == 'tb.scs' else 'support',
                   **({'support_role': 'authored_helper' if path in declared_helpers else 'dut_support'} if path in support else {}),
                   'reachable': path in inventory['visited']} for path, raw in sorted(documents.items())],
        'authored_helper_paths': sorted(declared_helpers),
        'dut_support_paths': sorted(set(support) - declared_helpers),
        'managed_pdk': {'path': 'pdk.scs', 'sha256': binding['pdk_sha256'],
                        'scope': 'caller-generated wrapper bytes; not the entire PDK'},
        'criteria_sha256': _sha(criteria_raw), 'pdk_binding': binding,
        'pdk_binding_sha256': _sha(_canonical(binding)), 'dependency_closure': inventory['includes'],
        'required_masters': inventory['required_masters'],
        'veriloga_modules': inventory['veriloga_modules'],
        'veriloga_external_dependencies': external_dependencies,
        'veriloga_header_closure': binding.get('veriloga_header_closure'),
        'info_requests': inventory['info_requests'],
        'static_check_scope': 'bounded syntax and source identity only; not a Verilog-A compiler or execution sandbox',
        'scientific_intent_inferred': False, 'source_rewritten': False,
        'simulation_verified': False, 'human_review_required': True}
    manifest['manifest_sha256'] = _sha(_canonical(manifest))
    return {'schema': VALIDATION_SCHEMA, 'mode': 'agent-tb',
        'status': 'blocked' if issues else 'static_ready', 'missing': issues,
        'manifest': manifest, 'requirements': _requirements(documents['dut.scs'], 'dut.scs', inventory),
        'analyses': analyses, 'saves': saves, 'info_requests': inventory['info_requests'], 'execution_authorized': False,
        'supported_scope': 'finite Spectre and scalar Verilog-A syntax; supplied relative includes and explicitly bound standard headers; no scripts, dynamic includes or compiler/sandbox claim'}

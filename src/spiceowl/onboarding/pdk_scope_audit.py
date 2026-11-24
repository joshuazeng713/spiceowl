"""Pure comparison of a cached scope selection with supplied audit evidence.

No path in these objects is opened. A match does not establish when, where or
by whom the audit ran; the caller must bind it to its fresh authorized operation.
"""

import json
import re

from spiceowl.domain.common import DomainError
from spiceowl.integrations.model_dependencies import AUDIT_SCHEMA, validate_audit_config


SCHEMA = 'spiceowl.pdk-scope-audit-comparison.v1'
MAX_AUDIT_BYTES = 16 * 1024 * 1024
MAX_FILES = 512
MAX_SCOPES = 4096
MAX_DECLARATIONS = 32768
_HASH = re.compile(r'[a-f0-9]{64}\Z')
_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_.$:+-]{0,127}\Z')
_PORT = re.compile(r'[A-Za-z0-9_.$:+-]{1,128}\Z')
_ROLES = {'model', 'dynamic_library', 'tool_config', 'ahdl_source', 'directory_reference'}


def _bounded_json(value):
    size = 0
    for chunk in json.JSONEncoder(ensure_ascii=True, allow_nan=False).iterencode(value):
        size += len(chunk)
        if size > MAX_AUDIT_BYTES:
            raise ValueError('oversized')


def _path(value):
    if (type(value) is not str or not value.startswith('/') or value.startswith('//')
            or len(value.encode('utf-8')) > 4096 or '\\' in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError('path')
    return value


def _section(value):
    if value is not None and (type(value) is not str or not _NAME.fullmatch(value)):
        raise ValueError('section')
    # Both existing audit and discovery traversal use case-insensitive sections.
    return value.lower() if value is not None else None


def _files(rows):
    if type(rows) is not list or not 1 <= len(rows) <= MAX_FILES:
        raise ValueError('files')
    pins, scopes = {}, set()
    for row in rows:
        if type(row) is not dict or row.get('kind') != 'file':
            raise ValueError('file')
        path, sha = _path(row.get('path')), row.get('sha256')
        if path in pins or type(sha) is not str or not _HASH.fullmatch(sha):
            raise ValueError('duplicate_or_invalid_file')
        roles = row.get('roles')
        if (type(roles) is not list or not 1 <= len(roles) <= len(_ROLES)
                or any(type(role) is not str or role not in _ROLES for role in roles)
                or len(set(roles)) != len(roles)):
            raise ValueError('roles')
        sections = row.get('selected_sections')
        if type(sections) is not list or len(sections) > MAX_SCOPES:
            raise ValueError('sections')
        selected = [_section(section) for section in sections]
        if len(set(selected)) != len(selected) or selected and 'model' not in roles:
            raise ValueError('duplicate_or_nonmodel_scope')
        scopes.update((path, section) for section in selected)
        if len(scopes) > MAX_SCOPES:
            raise ValueError('scopes')
        pins[path] = sha
    return pins, scopes


def _declarations(rows):
    if type(rows) is not list or len(rows) > MAX_DECLARATIONS:
        raise ValueError('declarations')
    declarations = {}
    for row in rows:
        if type(row) is not dict:
            raise ValueError('declaration')
        name = row.get('name')
        if type(name) is not str or not _NAME.fullmatch(name):
            raise ValueError('name')
        path, section, line = _path(row.get('path')), _section(row.get('section')), row.get('line')
        if type(line) is not int or not 1 <= line <= 2**31 - 1:
            raise ValueError('line')
        ports, count, kind = row.get('ports'), row.get('port_count'), row.get('kind')
        if (type(ports) is not list or len(ports) > 64
                or any(type(port) is not str or not _PORT.fullmatch(port) for port in ports)
                or count is not None and (type(count) is not int or count != len(ports))):
            raise ValueError('ports')
        if type(kind) is not str:
            raise ValueError('kind')
        if kind in {'model', 'primitive', 'primitive_model'}:
            kind = 'model'
            if ports or count is not None:
                raise ValueError('primitive_ports')
        elif kind not in {'subckt', 'inline_subckt'} or not ports or count is None:
            raise ValueError('kind_or_ports')
        declarations.setdefault(name, set()).add((path, line, section, kind, count, tuple(ports)))
    return declarations


def verify_scope_selection_audit(selection, audit, *, audit_config):
    """Compare exact planned config, file pins, scopes and required declarations.

    Invalid selections raise the selection validator's ``DomainError``. Invalid
    or conflicting supplied audit evidence returns ``mismatch`` with bounded
    code-only gaps, so private paths cannot escape into the comparison result.
    """
    from .pdk_scope import validate_scope_selection

    validate_scope_selection(selection)
    public, private = selection['public'], selection['private']
    missing = []

    def gap(code):
        if not any(item['code'] == code for item in missing):
            missing.append({'code': code})

    def result():
        return {'schema': SCHEMA, 'selection_id': public['selection_id'],
                'status': 'mismatch' if missing else 'matched', 'missing': missing,
                'matches_selection': not missing, 'ready_to_run': False,
                'human_review_required': True, 'remote_freshness_verified': False,
                'fresh_audit_required': True, 'scientific_compatibility': 'not_evaluated'}

    if public['status'] != 'requires_audit' or private.get('audit_config') is None:
        gap('selection_not_auditable')
        return result()
    try:
        normalized_config = validate_audit_config(audit_config)
    except DomainError:
        gap('audit_config_invalid')
        return result()
    if normalized_config != private['audit_config']:
        gap('audit_config_mismatch')
    try:
        _bounded_json(audit)
        if type(audit) is not dict or audit.get('schema') != AUDIT_SCHEMA:
            raise ValueError('schema')
        if (audit.get('static_only') is not True
                or audit.get('status') != 'complete_for_supported_static_syntax'
                or type(audit.get('unknown_count')) is not int or audit['unknown_count'] != 0
                or audit.get('unknown') != [] or audit.get('unknown_truncated') is not False
                or audit.get('missing_masters') != []
                or audit.get('missing', []) != [] or audit.get('gaps', []) != []):
            gap('audit_incomplete')
        pins, scopes = _files(audit.get('files'))
        if (type(audit.get('visited_sections')) is not int
                or audit['visited_sections'] != len(scopes)):
            gap('audit_scope_count_mismatch')
        if (type(audit.get('limits')) is not dict
                or any(type(value) is not int for value in audit['limits'].values())
                or audit['limits'] != normalized_config['limits']):
            gap('audit_limits_mismatch')
        expected_pins = {row['path']: row['sha256'] for row in private['expected_files']}
        if pins != expected_pins:
            gap('audit_file_set_mismatch')
        expected_scopes = {(row['path'], _section(row['section']))
                           for row in private['dependency_scopes']}
        if scopes != expected_scopes:
            gap('audit_scope_mismatch')
        required = public['required_model_names']
        expected = _declarations(private['master_declarations'])
        actual = _declarations(audit.get('master_declarations'))
        model_paths = {path for path, _ in expected_scopes}
        for name, declarations in actual.items():
            for path, _, section, *_ in declarations:
                if (path not in model_paths or section is not None
                        and (path, section) not in expected_scopes):
                    gap('audit_declaration_scope_mismatch')
            # The emitter may omit unrequested declarations. Any it does report
            # must agree with the complete cached namespace, including its exact
            # physical definition and ordered ports; unknown extras are not inert.
            if name not in required and (len(declarations) != 1 or declarations != expected.get(name)):
                gap('audit_namespace_mismatch')
        for name in required:
            planned, observed = expected.get(name, set()), actual.get(name, set())
            if len(planned) != 1 or len(observed) != 1:
                gap('required_declaration_missing_or_ambiguous')
            elif observed != planned:
                gap('required_declaration_mismatch')
    except (KeyError, ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        gap('invalid_audit_evidence')
    return result()

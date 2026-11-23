"""Clean-slate, metadata-only PDK discovery from supplied netlist requirements.

No function in this module opens a local profile, invokes SSH, chooses a corner,
registers a model, or launches a simulator. The caller supplies independently
discovered/authorized roots and executes the generated bounded read-only program.
Discovery candidates require selection and a subsequent dependency audit; they
are not executable model profiles.
"""

import hashlib
import json
import math
import posixpath
import re

from spiceowl.domain.common import DomainError
from spiceowl.experiments.source import PRIMITIVES, assignments
from spiceowl.integrations.model_dependencies import (
    build_model_audit_script, validate_audit_config,
)


REQUIREMENTS_SCHEMA = "spiceowl.netlist-model-requirements.v1"
DISCOVERY_SCHEMA = "spiceowl.pdk-discovery.v1"
MAX_SOURCE_BYTES = 1024 * 1024
MAX_RESULT_BYTES = 2 * 1024 * 1024
LIBRARY_SCHEMA = 'spiceowl.pdk-library.v1'
MAX_LIBRARY_RESULT_BYTES = 16 * 1024 * 1024
MAX_LIBRARY_BINDINGS = 65536
LIBRARY_CONTRACT_STORAGE = 'shared-model-contracts.v1'
DIRECTORY_SNAPSHOT_PREFIX = "SPICEOWL_DISCOVERY_DIRECTORY_SNAPSHOT "
MAX_DIRECTORY_SNAPSHOT_BYTES = 64 * 1024
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.$:+-]{0,127}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_NUMBER = re.compile(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(meg|[afpnumkKMGTP])?\Z")
_SUFFIX = {"": 1, "a": 1e-18, "f": 1e-15, "p": 1e-12, "n": 1e-9,
           "u": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3, "meg": 1e6,
           "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15}
_DISCOVERY_LIMITS = {"max_directories": 4096, "max_directory_entries": 8192,
                     "max_depth": 12, "max_catalogs": 16,
                     "max_catalog_bytes": 1024 * 1024,
                     "max_candidates": 256, "max_graph_nodes": 4096,
                     "max_platform_candidates": 64}
_AUDIT_LIMITS = {"max_files": 256, "max_file_bytes": 256 * 1024 * 1024,
                 "max_total_bytes": 1024 * 1024 * 1024, "max_sections": 1024,
                 "max_edges": 8192, "max_directives": 32768,
                 "max_line_bytes": 65536, "max_logical_line_bytes": 4 * 1024 * 1024,
                 "max_issues": 256}


def _invalid(message="Invalid or oversized PDK discovery input."):
    raise DomainError(message, "invalid_pdk_discovery")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _path(value):
    if (type(value) is not str or not value.startswith("/") or value.startswith("//")
            or len(value) > 4096 or "\\" in value
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        _invalid("Discovery paths must be bounded absolute POSIX paths.")
    return posixpath.normpath(value)


def _dimension(literal, language):
    match = _NUMBER.fullmatch(literal)
    suffix = match[2] or "" if match else ""
    factor = _SUFFIX.get(suffix)
    if language == "spice":
        factor = dict(_SUFFIX, g=1e9, t=1e12).get(suffix.lower())
    elif language is None and suffix == "M":
        factor = None
    value = float(match[1]) * factor if match and factor is not None else None
    if value is not None and not math.isfinite(value):
        value = None
    return {"literal": literal, "value_m": value, "language": language,
            "evaluated_expression": False}


def inspect_netlist(source, *, source_id="input.scs", mode="model-discovery", support_files=None):
    """Inspect supplied flat or subcircuit Spectre/SPICE text, without IO.

    Ordered instance nodes are evidence, not a guessed d/g/s/b mapping. Numeric
    W/L literals are reported in metres without applying a model scale factor.
    Device-name spelling never supplies polarity or process/VT qualification.
    """
    if mode == "agent-tb":
        from spiceowl.onboarding.agent_tb import inspect_agent_source
        return inspect_agent_source(source, source_id=source_id, support_files=support_files)
    if mode != "model-discovery" or support_files is not None:
        _invalid("Select explicit agent-tb mode when supplying support files.")
    if type(source) is str:
        try:
            source = source.encode("utf-8")
        except UnicodeError:
            _invalid("Netlist source must be valid UTF-8.")
    if type(source) is not bytes or not 0 < len(source) <= MAX_SOURCE_BYTES:
        _invalid("Supply bounded nonempty netlist bytes or text.")
    try:
        text = source.decode("utf-8")
    except UnicodeError:
        _invalid("Netlist source must be valid UTF-8.")
    if (type(source_id) is not str or not 1 <= len(source_id) <= 240
            or any(ord(c) < 32 for c in source_id)
            or any(ord(c) < 32 and c not in "\n\r\t" for c in text)):
        _invalid()
    pending, lines, first, continued = [], [], 0, False
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split("//", 1)[0].strip()
        if not line or line.startswith("*"):
            continue
        plus = line.startswith("+")
        if pending and not (continued or plus):
            lines.append((first, " ".join(pending)))
            pending = []
        if not pending:
            first = number
        part = line[1:].strip() if plus else line
        continued = part.endswith("\\")
        pending.append(part[:-1].rstrip() if continued else part)
        if sum(len(p) for p in pending) > 65536 or len(lines) > 4096:
            _invalid("Netlist statement count or continuation size exceeds the limit.")
    if pending:
        lines.append((first, " ".join(pending)))
    instances, declared, interfaces, issues = [], set(), [], []
    language = None
    if continued:
        issues.append({"code": "incomplete_continuation", "line": first})
    for line, statement in lines:
        lang = re.fullmatch(r"(?i)simulator\s+lang\s*=\s*(spectre|spice)\s*", statement)
        if lang:
            language = lang[1].lower()
            continue
        header = re.match(r"(?i)^(?:inline\s+)?\.?subckt\s+([^\s(]+)\s*(.*)$", statement)
        if header:
            name, rest = header.groups()
            if _NAME.fullmatch(name):
                declared.add(name)
                ports = rest.strip().strip("()").split()
                interfaces.append({"name": name, "ports": ports[:128], "line": line})
            else:
                issues.append({"code": "unsupported_subcircuit_header", "line": line})
            continue
        model = re.match(r"(?i)^\.?model\s+([^\s]+)\s+", statement)
        if model:
            declared.add(model[1])
            continue
        if re.match(r"(?i)^(?:\.?ends\b|simulator\s+lang=|global\s|parameters\s|\.param\s|\.end\s*$)", statement):
            continue
        if re.match(r"(?i)^(?:\.?include\b|\.lib\b|ahdl_include\b)", statement):
            issues.append({"code": "source_include_not_opened", "line": line})
            continue
        item = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_.$:+-]*)\s*\(([^()]*)\)\s*([A-Za-z_][A-Za-z0-9_.$:+-]*)(?:\s+(.*))?", statement)
        if item:
            name, nodes, master, parameters = item.groups()
            nodes = nodes.split()
            instance_language = language
        else:
            mos = re.fullmatch(r"(?i)(M[A-Za-z0-9_.$:+-]*)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+([A-Za-z_][A-Za-z0-9_.$:+-]*)(?:\s+(.*))?", statement)
            if not mos:
                issues.append({"code": "unsupported_source_statement", "line": line})
                continue
            name, *values = mos.groups()
            nodes, master, parameters = values[:4], values[4], values[5]
            instance_language = language or "spice"
        if len(nodes) > 128 or any(not _NAME.fullmatch(n) and n != "0" for n in nodes):
            issues.append({"code": "unsupported_terminal_names", "line": line})
            continue
        try:
            values, _ = assignments(parameters or "")
        except DomainError:
            issues.append({"code": "unsupported_parameters", "line": line})
            values = {}
        dimensions = {key.lower(): _dimension(value, instance_language) for key, value in values.items()
                      if key.lower() in {"w", "l"}}
        instances.append({"name": name, "master": master, "ordered_nets": nodes,
                          "port_count": len(nodes), "dimensions": dimensions, "line": line,
                          "polarity": None, "terminal_semantics": None})
    if len(instances) > 128:
        _invalid("Netlist contains too many instances for discovery.")
    required = sorted({item["master"] for item in instances} - declared - set(PRIMITIVES))
    return {"schema": REQUIREMENTS_SCHEMA, "source_id": source_id,
            "source_sha256": hashlib.sha256(source).hexdigest(),
            "status": "partial" if issues else "parsed" if instances else "unsupported",
            "instances": instances, "interfaces": interfaces,
            "required_masters": required, "issues": issues[:256],
            "confirmation_required": ["model_polarity", "terminal_semantics", "experiment_intent"],
            "dimensions_scope": "source literals only; model scale and legal dimensions are unknown"}


def build_pdk_discovery_script(*, required_masters, roots, source_sha256,
                               cds_libs=None, search_hints=None, limits=None):
    """Generate a read-only Python 2.7/3 program; never execute it.

    Roots must come from this onboarding session's authorized environment
    discovery. Explicit cds.lib files may be read as catalog metadata, but their
    DEFINE/INCLUDE paths never silently enlarge the search roots. Search hints
    only prioritize literal filenames; they do not contain code or set corners.
    """
    if (type(required_masters) is not list or not 1 <= len(required_masters) <= 64
            or any(type(m) is not str or not _NAME.fullmatch(m) for m in required_masters)
            or type(source_sha256) is not str or not _HASH.fullmatch(source_sha256)):
        _invalid("Supply exact unresolved master names and the input source digest.")
    if type(roots) is not list or not 1 <= len(roots) <= 8:
        _invalid("Supply one to eight authorized discovery roots.")
    roots = list(dict.fromkeys(_path(root) for root in roots))
    if "/" in roots:
        _invalid("A filesystem-wide discovery root is not supported.")
    cds_libs = [] if cds_libs is None else cds_libs
    hints = [] if search_hints is None else search_hints
    limits = {} if limits is None else limits
    if type(cds_libs) is not list or len(cds_libs) > 16:
        _invalid("Supply at most sixteen explicitly discovered cds.lib paths.")
    cds_libs = list(dict.fromkeys(_path(path) for path in cds_libs))
    if (type(hints) is not list or len(hints) > 16
            or any(type(h) is not str or not 1 <= len(h) <= 128 or any(ord(c) < 32 for c in h) for h in hints)):
        _invalid("Search hints must be bounded inert text.")
    if type(limits) is not dict or set(limits) - set(_DISCOVERY_LIMITS) - set(_AUDIT_LIMITS):
        _invalid("Unsupported discovery limit.")
    discovery_limits = dict(_DISCOVERY_LIMITS)
    for key in set(limits) & set(_DISCOVERY_LIMITS):
        if type(limits[key]) is not int or not 1 <= limits[key] <= _DISCOVERY_LIMITS[key]:
            _invalid("Discovery limits may be tightened, not widened.")
        discovery_limits[key] = limits[key]
    audit_limits = dict(_AUDIT_LIMITS)
    for key in set(limits) & set(_AUDIT_LIMITS):
        if type(limits[key]) is not int or not 1 <= limits[key] <= _AUDIT_LIMITS[key]:
            _invalid("Discovery audit limits may be tightened, not widened.")
        audit_limits[key] = limits[key]
    config = {"roots": roots, "main": roots[0] + "/__discovery_entry__.scs",
              "sections": [None], "required_masters": list(dict.fromkeys(required_masters)),
              "limits": audit_limits}
    # Reuse the versioned audit parser/read guards. This exact seam is checked
    # before emitting code, so a parser refactor fails closed rather than silently
    # giving discovery a different include/section grammar.
    program = build_model_audit_script(config)
    seam = "queue=[(config['main'],s,[]) for s in config['sections']]"
    if program.count(seam) != 1:
        _invalid("The installed dependency parser is incompatible with discovery.")
    prefix = program.split(seam, 1)[0]
    marker = "    for number, line in logical_lines(stream,digest,budget):\n"
    if prefix.count(marker) != 1:
        _invalid("The installed dependency parser cannot expose declarations.")
    prefix = prefix.replace(marker, marker + _MODEL_DECLARATION, 1)
    prefix = prefix.replace("def parse_file(stream,digest,budget):", _CONTRACT_HELPERS + "\ndef parse_file(stream,digest,budget):", 1)
    changes = {
        "sections, records, current = set(), [], None":
            "sections, records, current = set(), [], None\n    section_spellings={}\n    contract_state={'stack':[],'wrappers':[],'models':[],'language':None,'literals':[],'literals_truncated':False}",
        "current=t[1].rstrip(';').lower()":
            "current=t[1].rstrip(';').lower()\n            section_spellings.setdefault(current,t[1].rstrip(';'))",
        "return {'sections':sections,'records':records}":
            "return {'sections':sections,'records':records,'section_spellings':section_spellings,'contract_state':contract_state}",
    }
    for old, new in changes.items():
        if prefix.count(old) != 1:
            _invalid("The installed dependency parser cannot preserve section labels.")
        prefix = prefix.replace(old, new, 1)
    settings = {"source_sha256": source_sha256, "required_masters": config["required_masters"],
                "cds_libs": cds_libs, "search_hints": hints, "limits": discovery_limits,
                "directory_snapshot_prefix": DIRECTORY_SNAPSHOT_PREFIX,
                "directory_snapshot_max_bytes": MAX_DIRECTORY_SNAPSHOT_BYTES}
    return prefix + "\ndiscovery=json.loads(" + repr(json.dumps(settings, ensure_ascii=True)) + ")\n" + _DISCOVERY_PROGRAM


def _validated_result(discovery):
    if type(discovery) is not dict or discovery.get("schema") != DISCOVERY_SCHEMA:
        _invalid("Expected a PDK discovery receipt.")
    try:
        raw = json.dumps(discovery, allow_nan=False).encode()
    except (ValueError, TypeError, UnicodeError):
        _invalid()
    if len(raw) > MAX_RESULT_BYTES:
        _invalid("Discovery receipt exceeds the supported size.")
    identity = discovery.get("discovery_id")
    if identity != _digest({k: v for k, v in discovery.items() if k != "discovery_id"}):
        _invalid("Discovery receipt digest differs from its metadata.")
    return discovery


def profile_suggestions(discovery):
    """Return inert cacheable drafts, never selected/registered profiles."""
    data = _validated_result(discovery)
    declarations = data.get("declarations", [])
    suggestions = []
    for candidate in data.get("entry_candidates", []):
        names = data["required_masters"]
        models = candidate.get("models", [])
        verified = bool(models) and all(m["confidence"] == "verified" for m in models)
        scopes = {(s["path"], s["section"]) for s in candidate["dependency_scopes"]}
        evidence = {name: [d for d in declarations if d["name"] == name
                           and ((d["path"], d["section"]) in scopes or
                                d["section"] is None and d["path"] in candidate["dependency_paths"])]
                    for name in names}
        suggestions.append({"candidate_id": candidate["candidate_id"],
                            "discovery_id": data["discovery_id"],
                            "source_sha256": data["source_sha256"],
                            "entry_path": candidate["entry_path"],
                            "entry_sha256": candidate["entry_sha256"],
                            "section_candidates": candidate["section_candidates"],
                            "entry_role": candidate["entry_role"],
                            "master_coverage": candidate["master_coverage"],
                            "model_contract_evidence": evidence,
                            "models": models,
                            "model_contract_suggestion": {m["name"]: {
                                "polarity": m["polarity"],
                                "ordered_terminal_roles": m["ordered_terminal_roles"]
                            } for m in models},
                            "polarity_and_terminal_order_confirmed": False,
                            "model_contract_source_verified": verified,
                            "ready_to_run": False,
                            "requires": ["entrypoint_and_sections", "experiment_intent",
                                         "fresh_dependency_audit"] +
                                        ([] if verified else ["model_terminal_semantics", "model_polarity"]) +
                                        (["tmi_platform"] if candidate["tmi_candidates"] else [])})
    return {"schema": "spiceowl.pdk-profile-suggestions.v1",
            "discovery_id": data["discovery_id"], "source_sha256": data["source_sha256"],
            "state": "requires_confirmation" if suggestions else "missing",
            "candidates": suggestions, "selected_candidate_id": None,
            "search_complete": data["search_complete"], "ambiguities": data["ambiguities"]}


def build_pdk_library_script(*, roots, cds_libs=None, search_hints=None, limits=None):
    """Read a bounded PDK root inventory once, independently of any circuit.

    This explicit mode uses the same finite parser and include-closure guards
    as filtered discovery. It emits a separate library receipt, capped at
    16 MiB. Missing or truncated declarations are never inferred to be covered.
    """
    request_hash = _digest({'mode': 'full_library', 'roots': roots,
                            'cds_libs': cds_libs, 'search_hints': search_hints, 'limits': limits})
    script = build_pdk_discovery_script(required_masters=['__library_inventory__'], roots=roots,
        source_sha256=request_hash, cds_libs=cds_libs, search_hints=search_hints, limits=limits)
    for old, new in (
            ("if name is not None and name.lower() in required:", "if name is not None:"),
            ("if model and model.group(1).lower() in required and not contract_state['stack']:",
             "if model and not contract_state['stack']:"),
            ("if name.lower() in required: state['wrappers'].append(row)", "state['wrappers'].append(row)"),
            ("if state['stack'] and state['stack'][-1]['name'].lower() not in required: return", "# Observe every wrapper literal within the finite library policy."),
            ("if row['name'].lower() not in required: return", "# Observe every wrapper instance within the finite library policy.")):
        if script.count(old) != 1: _invalid('The installed parser cannot expose a full-library declaration inventory.')
        script = script.replace(old, new, 1)
    # These narrow seams deliberately fail closed if the shared parser changes.
    start = script.find("snapshot_codes=['model_scan_not_started']")
    stop = script.find('for path in sorted(file_candidates[:limits[', start)
    if start < 0 or stop < start: _invalid('The installed scanner has no supported library inventory seam.')
    script = script[:start] + script[stop:]
    before_graph = 'all_declarations, includes, tmi_refs, graph, graph_by_key = [], [], [], [], {}'
    inject = ("library_all_names=sorted(set(value['name'] for data in parsed.values() "
              "for current,line,kind,value in data['records'] if kind=='master'))\n"
              "library_names=library_all_names[:4096]\n"
              "if len(library_all_names)>4096: issue('library_model_name_limit')\n"
              "discovery['required_masters']=library_names\n" + before_graph)
    if script.count(before_graph) != 1: _invalid('The installed scanner cannot inventory library models.')
    script = script.replace(before_graph, inject, 1)
    marker = "    candidate={'entry_path':path,'entry_sha256':files[path]['sha256'],"
    replacement = ("    if sum(len(c['models']) for c in entry_candidates)+len(covered)>65536:\n"
                   "        issue('library_binding_limit');break\n"
                   "    discovery['required_masters']=sorted(covered)\n" + marker)
    if script.count(marker) != 1: _invalid('The installed scanner cannot preserve library entry scope.')
    script = script.replace(marker, replacement, 1)
    marker = "complete=[c for c in entry_candidates if not c['missing_masters']]"
    if script.count(marker) != 1: _invalid('The installed scanner cannot finalize library scope.')
    script = script.replace(marker, "discovery['required_masters']=library_names\n" + marker, 1)
    marker = "encoded=json.dumps(result,sort_keys=True,ensure_ascii=True)"
    if script.count(marker) != 1: _invalid('The installed scanner cannot serialize a bounded library receipt.')
    script = script.split(marker, 1)[0]
    return script + _LIBRARY_SERIALIZER


def validate_pdk_library(library):
    """Validate an independent full-library observation without reading paths."""
    if (not isinstance(library, dict) or library.get('schema') != LIBRARY_SCHEMA
            or library.get('mode') != 'full_library'):
        _invalid('Use an explicit full-library receipt, never a filtered scan relabeled as an inventory.')
    try: raw = json.dumps(library, allow_nan=False, separators=(',', ':')).encode()
    except (ValueError, TypeError, UnicodeError, RecursionError): _invalid()
    if len(raw) > MAX_LIBRARY_RESULT_BYTES or library.get('library_id') != _digest(
            {key: value for key, value in library.items() if key != 'library_id'}):
        _invalid('Library receipt exceeds its bound or differs from its digest.')
    coverage = library.get('coverage')
    candidates = library.get('entry_candidates')
    if (not isinstance(coverage, dict) or coverage.get('kind') != 'full_library_attempt'
            or not isinstance(candidates, list) or len(candidates) > 256
            or not isinstance(library.get('roots'), list) or not 1 <= len(library['roots']) <= 8
            or not isinstance(library.get('files'), list) or len(library['files']) > 256
            or not isinstance(library.get('declarations'), list)
            or not isinstance(library.get('audit_limits'), dict)
            or type(library.get('search_complete')) is not bool
            or coverage.get('whole_pdk_qualified') is not False): _invalid()
    for root in library['roots']:
        if _path(root) == '/': _invalid()
    if 'requested_roots' in library:
        requested = library['requested_roots']
        if (not isinstance(requested, list) or len(requested) != len(library['roots'])
                or any(not isinstance(root, str) for root in requested)
                or len(set(requested)) != len(requested)): _invalid('Requested roots must map the complete observed canonical root list.')
        canonical = set(library['roots'])
        for logical, resolved in zip(requested, library['roots']):
            if (_path(logical) != logical or logical == '/' or _path(resolved) != resolved
                    or logical in canonical and logical != resolved):
                _invalid('Logical and canonical roots have inconsistent or colliding identities.')
    files = {}
    for row in library['files']:
        if (not isinstance(row, dict) or not isinstance(row.get('sha256'), str)
                or not _HASH.fullmatch(row['sha256'])): _invalid()
        path = _path(row.get('path'))
        if path in files: _invalid('Duplicate observed file identities are unsupported.')
        files[path] = row['sha256']
    storage = library.get('contract_storage')
    contracts = library.get('model_contracts')
    if storage is None:
        if contracts is not None: _invalid('Shared model contracts require their explicit storage encoding.')
    else:
        if storage != LIBRARY_CONTRACT_STORAGE or not isinstance(contracts, dict) or len(contracts) > MAX_LIBRARY_BINDINGS:
            _invalid('Unsupported or oversized library model-contract storage.')
        for identity, model in contracts.items():
            if (not isinstance(identity, str) or not _HASH.fullmatch(identity)
                    or not isinstance(model, dict) or not isinstance(model.get('name'), str)
                    or not _NAME.fullmatch(model['name']) or identity != _digest(model)):
                _invalid('Shared model contract differs from its exact content identity.')
    ids, indexed, referenced, binding_count = set(), set(), set(), 0
    for candidate in candidates:
        if (not isinstance(candidate, dict) or not isinstance(candidate.get('candidate_id'), str)
                or not _HASH.fullmatch(candidate['candidate_id']) or candidate['candidate_id'] in ids
                or not isinstance(candidate.get('models'), list) or len(candidate['models']) > 4096
                or any(not isinstance(candidate.get(key), list) for key in ('dependency_paths', 'dependency_scopes',
                    'section_candidates', 'available_sections', 'master_coverage', 'unknown_codes', 'tmi_candidates'))
                or type(candidate.get('coverage_complete')) is not bool
                or candidate.get('entry_role') not in ('include_entry_candidate', 'definition_only')): _invalid()
        ids.add(candidate['candidate_id'])
        if files.get(_path(candidate.get('entry_path'))) != candidate.get('entry_sha256'): _invalid()
        if any(not isinstance(path, str) or path not in files for path in candidate['dependency_paths']): _invalid()
        for scope in candidate['dependency_scopes']:
            if (not isinstance(scope, dict) or not isinstance(scope.get('path'), str) or scope['path'] not in files
                    or 'section' not in scope or scope['section'] is not None and not isinstance(scope['section'], str)): _invalid()
        names = []
        for model in candidate['models']:
            if (not isinstance(model, dict) or not isinstance(model.get('name'), str)
                    or not _NAME.fullmatch(model['name'])): _invalid()
            if storage is not None:
                identity = model.get('contract_id')
                if (set(model) != {'name', 'contract_id'} or not isinstance(identity, str)
                        or identity not in contracts or contracts[identity]['name'] != model['name']):
                    _invalid('Library candidate refers to an absent or mismatched model contract.')
                referenced.add(identity)
            elif 'contract_id' in model:
                _invalid('An inline library model cannot contain an unresolved contract reference.')
            names.append(model['name'])
        if len(set(names)) != len(names) or sorted(names) != candidate['master_coverage']: _invalid()
        indexed.update(names); binding_count += len(names)
    if (len(indexed) > 4096 or binding_count > MAX_LIBRARY_BINDINGS
            or sorted(indexed) != coverage.get('indexed_model_names')
            or len(indexed) != coverage.get('indexed_model_count')):
        _invalid('Indexed model scope differs from the observed entries.')
    if storage is not None and referenced != set(contracts):
        _invalid('Shared model-contract storage contains unreferenced evidence.')
    for row in library['declarations']:
        if (not isinstance(row, dict) or not isinstance(row.get('name'), str) or not _NAME.fullmatch(row['name'])
                or not isinstance(row.get('path'), str) or row['path'] not in files
                or type(row.get('line')) is not int or row['line'] < 1
                or 'section' not in row or row['section'] is not None and not isinstance(row['section'], str)): _invalid()
    return library


def library_candidate_models(library, candidate):
    """Resolve a candidate's model rows after ``validate_pdk_library``.

    Both historical inline receipts and explicit shared-contract receipts keep
    their original bytes. The returned dictionaries are the observed objects;
    callers must not mutate them. This helper does not replace validation of the
    complete receipt, its digest, dependency scope or coverage.
    """
    rows = candidate.get('models') if isinstance(candidate, dict) else None
    if not isinstance(library, dict) or not isinstance(rows, list): _invalid()
    if library.get('contract_storage') is None:
        if library.get('model_contracts') is not None or any(
                not isinstance(row, dict) or 'contract_id' in row for row in rows): _invalid()
        return rows
    if library.get('contract_storage') != LIBRARY_CONTRACT_STORAGE: _invalid()
    contracts = library.get('model_contracts')
    if not isinstance(contracts, dict): _invalid()
    resolved = []
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'name', 'contract_id'}
                or not isinstance(row.get('contract_id'), str)):
            _invalid('Use exact named shared-model references.')
        model = contracts.get(row['contract_id'])
        if not isinstance(model, dict) or model.get('name') != row['name']:
            _invalid('Shared model reference has no matching observed contract.')
        resolved.append(model)
    return resolved


def pdk_library_page(library, *, offset=0, limit=64):
    """Page one immutable receipt's actual indexed names; never scan again."""
    validate_pdk_library(library)
    if type(offset) is not int or not 0 <= offset <= 4096 or type(limit) is not int or not 1 <= limit <= 256: _invalid()
    names = library['coverage']['indexed_model_names']; selected = names[offset:offset + limit]
    return {'schema': 'spiceowl.pdk-library-page.v1', 'library_id': library['library_id'],
        'model_names': selected, 'total': len(names), 'next_offset': offset + len(selected) if offset + len(selected) < len(names) else None,
        'coverage': {'complete_within_policy': library['coverage']['complete_within_policy'],
                     'whole_pdk_qualified': False}, 'ready_to_run': False}


def audit_config_for_candidate(discovery, candidate_id, *, sections,
                               tmi_relative_library=None):
    """Build validation input after an external caller's explicit selection.

    This pure function does not constitute consent, verify freshness, or run the
    audit. The onboarding owner must bind confirmation to discovery/source IDs.
    """
    data = _validated_result(discovery)
    found = [c for c in data.get("entry_candidates", []) if c["candidate_id"] == candidate_id]
    if len(found) != 1 or type(sections) is not list or not sections:
        _invalid("Select an exact discovered candidate and explicit sections.")
    candidate = found[0]
    if any(section not in candidate["available_sections"] for section in sections):
        _invalid("Selected sections are absent from this candidate's source.")
    if candidate["tmi_candidates"] and tmi_relative_library is None:
        _invalid("Confirm the actual simulator platform library before auditing TMI.")
    observed_platforms = {p["relative_library"] for row in candidate["tmi_candidates"]
                          for p in row["platform_candidates"]}
    if tmi_relative_library is not None and tmi_relative_library not in observed_platforms:
        _invalid("The selected TMI platform library was not observed for this candidate.")
    result = {"roots": data["roots"], "main": candidate["entry_path"], "sections": sections,
              "required_masters": data["required_masters"], "limits": data["audit_limits"]}
    if tmi_relative_library is not None:
        result["tmi_relative_library"] = tmi_relative_library
    return validate_audit_config(result)


_CONTRACT_HELPERS = r'''
# Deliberately finite source grammar: one direct four-terminal MOS per wrapper.
# Device-name spelling is never used as polarity or terminal evidence.
import heapq
contract_name=r'[A-Za-z_][A-Za-z0-9_.$:+-]*'
contract_header=re.compile(r'(?i)^(?:inline\s+)?\.?subckt\s+('+contract_name+r')\s*(.*)$')
contract_model=re.compile(r'(?i)^\.?model\s+('+contract_name+r')\s+('+contract_name+r')\b')
contract_call=re.compile(r'^('+contract_name+r')\s*\(([^()]*)\)\s*('+contract_name+r')(?:\s+(.*))?$')
contract_spice_call=re.compile(r'(?i)^(M[A-Za-z0-9_.$:+-]*)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+('+contract_name+r')(?:\s+(.*))?$')
contract_spectre_call=re.compile(r'^('+contract_name+r')\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+('+contract_name+r')(?:\s+(.*))?$')
contract_bin=re.compile(r'^(.+)\.(global|[0-9]+)$',re.I)
mos_families=set(['bsim3','bsim3v3','bsim4','mos1','mos2','mos3'])
contract_literal_count=0
literal_kinds={'tnom':'nominal_temperature','nominal_temperature':'nominal_temperature',
    'vnom':'nominal_voltage','vdd_nom':'nominal_voltage','vdd_nominal':'nominal_voltage',
    'nominal_voltage':'nominal_voltage'}
for terminal_pair in ('gs','ds','gb','gd','bs','bd'):
    literal_kinds['v'+terminal_pair+'_max']='voltage_limit'
    literal_kinds['maxv'+terminal_pair]='voltage_limit'

def capture_literals(state,line,number,section,model):
    # Only explicit numeric assignments with a small semantic allowlist are
    # returned. Expressions and ordinary user parameters are never exported.
    global contract_literal_count
    eligible=model or re.match(r'(?i)^(?:parameters\b|\.param\b|\.?options?\b|'+contract_name+r'\s+options\b)',line)
    if not eligible: return
    if state['stack'] and state['stack'][-1]['name'].lower() not in required: return
    for match in re.finditer(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^\s);]+)',line):
        parameter,literal=match.groups();kind=literal_kinds.get(parameter.lower())
        if kind is None or len(literal)>64: continue
        number_match=re.match(r'^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)([A-Za-z]*)$',literal)
        if number_match is None: continue
        numeric,suffix=number_match.groups()
        following=line[match.end():].strip()
        if following and not (re.match(r'^[);]*$',following) or re.match(r'^[A-Za-z_][A-Za-z0-9_]*\s*=',following)):
            continue
        if kind=='nominal_temperature':
            # K is an engineering multiplier in model syntax, not an implicit
            # Kelvin unit. Only the standard tnom parameter supplies Celsius.
            if suffix not in ('','C'): continue
            unit='C' if suffix=='C' or parameter.lower()=='tnom' else None;scale=1.0
        else:
            if suffix not in ('','V','m','mV'): continue
            unit='V';scale=0.001 if suffix in ('m','mV') else 1.0
        value=float(numeric)*scale
        if not -1e300<value<1e300: continue
        if contract_literal_count>=limits['max_directives']: state['literals_truncated']=True;continue
        contract_literal_count+=1
        state['literals'].append({'kind':kind,'line':number,'section_key':section,
            'parameter':parameter,'literal':literal,'value':value,'unit':unit,
            'basis':'explicit_source_literal','model_name':model.group(1) if model else None,
            'owner_line':state['stack'][-1]['line'] if state['stack'] else None})

def capture_contract(state,line,number,section):
    global directive_count
    header=contract_header.match(line)
    model=contract_model.match(line)
    language=re.match(r'(?i)^simulator\s+lang\s*=\s*(spectre|spice)\s*;?$',line)
    if language:
        state['language']=language.group(1).lower();return
    if re.match(r'(?i)^simulator\b',line):
        state['language']='unsupported'
        if state['stack']: state['stack'][-1]['unsupported'].append('unsupported_language_directive')
        return
    capture_literals(state,line,number,section,model)
    if header or model:
        directive_count+=1
        if directive_count>limits['max_directives']: raise ValueError('directive_limit')
    if header:
        name,tail=header.groups(); ports=[]
        if tail.startswith('(') and ')' in tail: ports=tail[1:tail.index(')')].split()
        elif tail and not tail.startswith('('):
            for value in tail.split():
                if '=' in value or value.lower().rstrip(':') in ('params','parameters'): break
                ports.append(value)
        if state['stack']: state['stack'][-1]['unsupported'].append('nested_subcircuit')
        row={'name':name,'ports':ports[:64],'line':number,'section_key':section,
             'instances':[],'instance_count':0,'unsupported':[],'closed':False,
             'language':state['language'] or ('spice' if line.startswith('.') else 'spectre')}
        if len(ports)!=4 or len(set(ports))!=4: row['unsupported'].append('non_bijective_four_port_interface')
        if row['language']=='spice' and len(set(p.lower() for p in ports))!=4:
            row['unsupported'].append('case_aliased_spice_ports')
        state['stack'].append(row)
        if name.lower() in required: state['wrappers'].append(row)
        return
    if re.match(r'(?i)^\.?ends\b',line):
        if state['stack']:
            row=state['stack'].pop(); parts=line.split()
            row['closed']=len(parts)==1 or len(parts)==2 and parts[1].rstrip(';')==row['name']
        return
    if model:
        name,family=model.groups(); family=family.lower()
        values=re.findall(r'(?i)\btype\s*=\s*([^\s);]+)',line[model.end():])
        polarity={'nmos':'n','pmos':'p'}.get(family)
        reason='explicit_mos_model_family' if polarity else None
        if family in mos_families and len(values)==1:
            polarity={'1':'n','+1':'n','-1':'p','n':'n','p':'p'}.get(values[0].lower())
            reason='explicit_supported_mos_type' if polarity else None
        elif values or family not in ('nmos','pmos'):
            polarity=None;reason=None
        state['models'].append({'name':name,'family':family,'polarity':polarity,
            'type_literal':values[0] if len(values)==1 and len(values[0])<=16 else None,
            'evidence_rule':reason,'line':number,'section_key':section,
            'owner_line':state['stack'][-1]['line'] if state['stack'] else None,
            'spice_declaration':line.startswith('.')})
        return
    if not state['stack']: return
    row=state['stack'][-1]
    if row['name'].lower() not in required: return
    if re.match(r'(?i)^(?:parameters\b|\.param\b)',line): return
    dialect=state['language'] or row['language']
    match=contract_call.match(line) if dialect=='spectre' else None
    if match:
        name,nodes,master,params=match.groups(); nodes=nodes.split()
    else:
        match=(contract_spectre_call if dialect=='spectre' else contract_spice_call).match(line) if dialect in ('spectre','spice') else None
        if match:
            parts=match.groups();name=parts[0];nodes=list(parts[1:5]);master=parts[5];params=parts[6]
    if match:
        row['instance_count']+=1
        if row['instance_count']<=2:
            row['instances'].append({'name':name,'ordered_nets':nodes[:64], 'master':master,'line':number})
        if (len(nodes)!=4 or any(not re.match(r'^[A-Za-z0-9_.$:+-]+$',n) for n in nodes)
                or re.search(r'(?i)\b(?:type|model)\s*=',params or '')):
            row['unsupported'].append('unsupported_mos_instance')
        if dialect=='spice' and (len(set(n.lower() for n in nodes))!=4 or len(set(p.lower() for p in row['ports']))!=4):
            row['unsupported'].append('case_aliased_spice_nodes')
    else:
        if len(row['unsupported'])<16: row['unsupported'].append('unsupported_wrapper_statement')

def resolve_primitive_models(exact,bins):
    if exact:
        if len(exact)!=1 or bins: return None,'multiple_active_primitive_definitions',exact+bins
        return exact[0]['polarity'],exact[0]['evidence_rule'] or 'unsupported_primitive_type',exact
    if not bins: return None,'primitive_model_not_resolved',[]
    # A binned family is a single owner namespace. Never combine partial bins
    # from different wrappers or borrow a bin from a sibling wrapper.
    owners=set((m['path'] if m['owner_line'] is not None else None,m['owner_line']) for m in bins)
    suffixes=[contract_bin.match(m['name']).group(2).lower() for m in bins]
    suffixes=[s.lstrip('0') or '0' if s!='global' else s for s in suffixes]
    if len(owners)!=1 or len(suffixes)!=len(set(suffixes)):
        return None,'ambiguous_binned_primitive_ownership_or_suffix',bins
    if not any(s!='global' for s in suffixes): return None,'missing_numeric_model_bin',bins
    polarities=set(m['polarity'] for m in bins)
    families=set(m['family'] for m in bins)
    if len(polarities)!=1 or None in polarities or len(families)!=1:
        return None,'conflicting_or_unknown_model_bins',bins
    return next(iter(polarities)),'consistent_explicit_mos_bins',bins

contract_scope_index={}
contract_result_cache={}
def build_source_indexes():
    # This runs only after every reachable source has been parsed. Index rows
    # are immutable observations for this scan and never cross file hashes.
    for path,data in sorted(parsed.items()):
        buckets={};include_records=[];global_include_lines=[]
        for ordinal,record in enumerate(data['records']):
            section,line,kind,value=record
            buckets.setdefault(section,[]).append((ordinal,record))
            if kind=='include':
                include_records.append(record)
                if section is None:global_include_lines.append(line)
        data['record_buckets']=buckets
        data['include_records']=include_records
        data['global_include_lines']=global_include_lines
        state=data['contract_state']
        data['global_scope_present']=bool(buckets.get(None) or any(
            row['section_key'] is None for kind in ('models','wrappers','literals') for row in state[kind]))
        for kind in ('wrappers','models','literals'):
            for row in state[kind]:
                scope=contract_scope_index.setdefault((path,row['section_key']),
                    {'wrappers':{},'models':{},'spice_models':{},'binned_models':{},'literals':[]})
                if kind=='literals':scope['literals'].append(row);continue
                item=dict(row,path=path,section=data['section_spellings'].get(row['section_key'],row['section_key']))
                scope[kind].setdefault(row['name'],[]).append(item)
                if kind=='models' and row['spice_declaration']:
                    scope['spice_models'].setdefault(row['name'].lower(),[]).append(item)
                    match=contract_bin.match(row['name'])
                    if match:scope['binned_models'].setdefault(match.group(1).lower(),[]).append(item)

def scoped_records(data,section):
    buckets=data['record_buckets']
    rows=buckets.get(None,[]) if section is None else heapq.merge(buckets.get(None,[]),buckets.get(section,[]))
    for ordinal,row in rows:yield row

def active_contract_scopes(visited):
    scopes=set(visited)
    scopes.update((path,None) for path,section in visited)
    return tuple(sorted((scope for scope in scopes if scope in contract_scope_index),key=lambda value:(value[0],value[1] or '')))

def definition_identity(row):
    return (row['path'],files[row['path']]['sha256'],row['line'],row['section_key'],row.get('owner_line'))

def model_contracts_for_scopes(visited,unresolved):
    active_scopes=active_contract_scopes(visited)
    query_cache={}
    def observed_rows(kind,name):
        key=(kind,name)
        if key not in query_cache:
            rows={}
            for scope in active_scopes:
                for row in contract_scope_index[scope][kind].get(name,[]):rows[definition_identity(row)]=row
            query_cache[key]=sorted(rows.values(),key=lambda row:(row['path'],row['line'],row['section_key'] or '',row.get('owner_line') or 0))
        return query_cache[key]
    def exact_models(name):
        return observed_rows('models',name)+[m for m in observed_rows('spice_models',name.lower()) if m['name']!=name]
    output=[]
    unknown_state=tuple(sorted(set(unresolved)))
    for name in discovery['required_masters']:
        evidence=[];polarity=None;roles=None;reason='definition_not_found'
        matches=observed_rows('wrappers',name)+[r for r in exact_models(name) if r['owner_line'] is None]
        model_matches=[];bin_matches=[]
        if len(matches)==1:
            matched=matches[0]
            if 'instances' in matched and matched['closed'] and not matched['unsupported'] and matched['instance_count']==1:
                call=matched['instances'][0]
                model_matches=[m for m in exact_models(call['master']) if
                    m['owner_line'] is None or m['path']==matched['path'] and m['owner_line']==matched['line']]
                bin_matches=[m for m in observed_rows('binned_models',call['master'].lower()) if
                    m['owner_line'] is None or m['path']==matched['path'] and m['owner_line']==matched['line']]
        cache_key=(name,tuple(definition_identity(row) for row in matches),
                   tuple(definition_identity(row) for row in model_matches),
                   tuple(definition_identity(row) for row in bin_matches),unknown_state)
        if cache_key in contract_result_cache:
            output.append(contract_result_cache[cache_key]);continue
        if len(matches)>1: reason='multiple_active_model_definitions'
        elif len(matches)==1:
            row=matches[0]
            evidence.append({'kind':'declaration','path':row['path'],'line':row['line'],'section':row['section'],'name':name})
            if 'instances' in row:
                reason='unsupported_wrapper_contract'
                if row['closed'] and not row['unsupported'] and row['instance_count']==1:
                    call=row['instances'][0]
                    evidence.append(dict(call,kind='direct_instance',path=row['path']))
                    primitive_polarity,primitive_reason,primitive_rows=resolve_primitive_models(model_matches,bin_matches)
                    reason=primitive_reason
                    if primitive_polarity:
                        reason='unsupported_primitive_type_or_terminal_mapping'
                        nodes=call['ordered_nets']
                        if len(nodes)==4 and len(set(nodes))==4 and set(nodes)==set(row['ports']):
                            polarity=primitive_polarity
                            mapping=dict(zip(nodes,['d','g','s','b']))
                            roles=[mapping[p] for p in row['ports']]
                            reason='single_mos_bijective_binned_wrapper' if bin_matches else 'single_mos_bijective_wrapper'
                    for primitive in primitive_rows:
                        evidence.append(dict({k:primitive[k] for k in ('name','path','line','section','family','type_literal','evidence_rule')},
                                             kind='primitive_model',owner_line=primitive['owner_line']))
            else:
                polarity=row['polarity'];roles=['d','g','s','b'] if polarity else None
                reason=row['evidence_rule'] or 'unsupported_primitive_type'
                evidence.append({k:row[k] for k in ('name','path','line','section','family','type_literal','evidence_rule')})
        verified=polarity is not None and roles is not None and not unresolved
        resolved_contract={'name':name,'polarity':polarity,'ordered_terminal_roles':roles,
            'suggested_contract':{'polarity':polarity,'terminals':roles} if polarity and roles else None,
            'suggestion_basis':reason,'auto_prefill_allowed':verified,
            'evidence':evidence,'confidence':'verified' if verified else 'suggested' if polarity or roles else 'unknown',
            'reason':reason,'needs_confirmation':not verified,
            'scope':'static source contract only; no simulator or process qualification'}
        # Only resolved immutable contract evidence is shared, never a candidate's
        # mutable section/literal state. The key includes the exact active
        # definition and primitive/bin evidence plus unresolved closure codes.
        if len(contract_result_cache)<32768:contract_result_cache[cache_key]=resolved_contract
        output.append(resolved_contract)
    return output
'''


_MODEL_DECLARATION = r'''        capture_contract(contract_state,line,number,current)
        model=re.match(r'(?i)^\s*\.?model\s+([A-Za-z_][A-Za-z0-9_.$:+-]*)\s+([A-Za-z_][A-Za-z0-9_.$:+-]*)',line)
        if model and model.group(1).lower() in required and not contract_state['stack']:
            directive_count += 1
            if directive_count > limits['max_directives']: raise ValueError('directive_limit')
            records.append((current,number,'master',{'name':model.group(1),'kind':'model',
                            'model_type':model.group(2),'ports':[],'port_count':None}))
'''


_DISCOVERY_PROGRAM = r'''
from collections import deque
dl=discovery['limits']
catalogs, root_suggestions, catalog_queue = [], [], list(discovery['cds_libs'])
catalog_seen=set()
extensions=set(['.scs','.sp','.spice','.spi','.lib','.inc'])
walked, file_candidates, walked_count = set(), [], 0

def stable(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('utf-8')).hexdigest()

def priority(path):
    name=os.path.basename(path).lower()
    return (0 if any(h.lower() in name for h in discovery['search_hints']) else 1,name,path)

def literal_path(value,base):
    if any(c in value for c in '$`{}') or value.startswith('~'):
        return None
    return os.path.normpath(value if os.path.isabs(value) else os.path.join(base,value))

while catalog_queue:
    catalog=catalog_queue.pop(0)
    if catalog in catalog_seen: continue
    if len(catalog_seen)>=dl['max_catalogs']: issue('catalog_limit'); break
    catalog_seen.add(catalog)
    if catalog not in discovery['cds_libs'] and not allowed(os.path.realpath(catalog)):
        issue('catalog_outside_roots',catalog); continue
    try:
        if os.path.islink(catalog): raise ValueError('catalog_symlink_not_followed')
        fd=os.open(catalog,os.O_RDONLY|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_NOFOLLOW',0))
        with os.fdopen(fd,'rb') as stream:
            before=os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode): raise ValueError('catalog_not_regular')
            raw=stream.read(dl['max_catalog_bytes']+1)
            after=os.fstat(stream.fileno())
        total_bytes+=len(raw)
        if len(raw)>dl['max_catalog_bytes'] or total_bytes>limits['max_total_bytes']:
            raise ValueError('catalog_byte_limit')
        if (before.st_ino,before.st_size,before.st_mtime)!=(after.st_ino,after.st_size,after.st_mtime):
            raise ValueError('catalog_changed')
        text=raw.decode('utf-8')
        catalogs.append({'path':catalog,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
        for number,line in enumerate(text.splitlines(),1):
            line=line.strip()
            if not line or line.startswith(('#','--','//')): continue
            try: t=tokens(line)
            except ValueError: issue('catalog_unparsed',catalog,number); continue
            if not t: continue
            cmd=t[0].upper()
            if cmd=='DEFINE' and len(t)==3:
                if len(root_suggestions)>=limits['max_directives']:
                    issue('catalog_record_limit',catalog,number);break
                target=literal_path(t[2],os.path.dirname(catalog))
                root_suggestions.append({'catalog':catalog,'line':number,'library':t[1],
                                         'path':target,'authorized_for_scan':bool(target and allowed(os.path.realpath(target))),
                                         'requires_root_confirmation':not bool(target and allowed(os.path.realpath(target)))})
            elif cmd in ('INCLUDE','SOFTINCLUDE') and len(t)==2:
                target=literal_path(t[1],os.path.dirname(catalog))
                if target is None: issue('catalog_dynamic_include',catalog,number)
                elif target in discovery['cds_libs'] or allowed(os.path.realpath(target)): catalog_queue.append(target)
                else: issue('catalog_include_requires_root_confirmation',target)
                if len(catalog_queue)>dl['max_catalogs']:
                    issue('catalog_queue_limit',catalog,number);catalog_queue=[];break
    except (IOError,OSError,UnicodeError): issue('catalog_unreadable',catalog)
    except ValueError as error: issue(str(error),catalog)

# Generic directory labels only prioritize traversal; they never establish a
# PDK identity or model qualification. Each independent root gets a turn before
# another directory from the same root. Within either priority lane use BFS.
directory_terms=re.compile(r'(?i)(?:^|[^a-z])(?:models?|spectre|spice|hspice)(?:[^a-z]|$)')
def preferred_directory(path):
    name=os.path.basename(path)
    return bool(directory_terms.search(name) or any(h.lower() in name.lower() for h in discovery['search_hints']))

effective_roots=[]
for root in sorted(set(roots),key=lambda p:(len(p.split(os.sep)),p)):
    if not any(root==parent or root.startswith(parent+os.sep) for parent in effective_roots):
        effective_roots.append(root)
lanes=[[deque(),deque()] for root in effective_roots]
scheduled=set()
queue_limited=set()
directory_suggestions={}
suggestions_truncated=False
suggestion_worst_path=None
def directory_suggestion_priority(row):
    basis=set(row['basis'])
    preferred=bool(basis&set(['model_directory_name_hint','unscanned_directory_frontier']))
    return (0 if 'observed_root_child' in basis else 1 if preferred else 2,
            row['depth'],0 if 'unscanned_directory_frontier' in basis else 1,row['path'])

def suggest_directory(path,basis,depth):
    global suggestions_truncated,suggestion_worst_path
    if path in directory_suggestions:
        row=directory_suggestions[path]
        if basis not in row['basis']:
            row['basis'].append(basis);suggestion_worst_path=None
        if depth<row['depth']:
            row['depth']=depth;suggestion_worst_path=None
        return
    row={'path':path,'basis':[basis],'depth':depth,
         'qualified':False,'authorized_for_scan':True,'requires_root_confirmation':False}
    if len(directory_suggestions)>=dl['max_candidates']:
        suggestions_truncated=True
        worst=suggestion_worst_path
        if worst is None:
            worst=max(directory_suggestions,key=lambda p:directory_suggestion_priority(directory_suggestions[p]))
            suggestion_worst_path=worst
        if directory_suggestion_priority(row)>=directory_suggestion_priority(directory_suggestions[worst]):return
        del directory_suggestions[worst]
    directory_suggestions[path]=row;suggestion_worst_path=None

for index,root in enumerate(effective_roots):
    lane=0 if preferred_directory(root) else 1
    lanes[index][lane].append((root,0));scheduled.add(root)
halt=False
while any(pair[0] or pair[1] for pair in lanes) and not halt:
    for pair in lanes:
        lane=0 if pair[0] else 1
        if not pair[lane]: continue
        if len(walked)>=dl['max_directories']:
            issue('directory_scan_limit');halt=True;break
        path,depth=pair[lane].popleft()
        path=resolve_path(path)
        if path is None or path in walked: continue
        walked.add(path)
        if not os.path.isdir(path): issue('search_root_not_directory',path); continue
        try: entries=os.listdir(path)
        except OSError: issue('directory_unreadable',path); continue
        if len(entries)>dl['max_directory_entries']: issue('directory_entry_limit',path); continue
        for name in sorted(entries,key=lambda n:(0 if preferred_directory(os.path.join(path,n)) else 1,priority(os.path.join(path,n)))):
            if name.startswith('.') or name=='__pycache__': continue
            child=os.path.join(path,name)
            target=resolve_path(child)
            if target is None: continue
            if os.path.isdir(target):
                if depth==0: suggest_directory(target,'observed_root_child',1)
                preferred=preferred_directory(target)
                if preferred: suggest_directory(target,'model_directory_name_hint',depth+1)
                if depth>=dl['max_depth']: issue('directory_depth_limit',target)
                elif target not in scheduled:
                    next_lane=0 if lane==0 or preferred else 1
                    if len(pair[0])+len(pair[1])>=dl['max_directories']:
                        if path not in queue_limited:
                            issue('directory_queue_limit',path);queue_limited.add(path)
                    else:
                        pair[next_lane].append((target,depth+1));scheduled.add(target)
            elif os.path.isfile(target) and os.path.splitext(name)[1].lower() in extensions:
                suggest_directory(path,'contains_candidate_model_files',depth)
                if target not in file_candidates: file_candidates.append(target)
                if len(file_candidates)>limits['max_files']:
                    issue('candidate_file_limit');halt=True;break
        if halt: break
pending_directory_count=sum(len(queue) for pair in lanes for queue in pair)
if halt:
    for pair in lanes:
        for queue in pair:
            for path,depth in queue:
                suggest_directory(path,'unscanned_directory_frontier',depth)
for row in directory_suggestions.values(): row['scanned']=row['path'] in walked

# This completed directory phase is independently useful if model parsing later
# times out. It has its own digest and never claims any model was read/found.
snapshot_codes=['model_scan_not_started']+sorted(set(row['code'] for row in issues)-set(['model_scan_not_started']))[:max(0,limits['max_issues']-1)]
snapshot={'schema':'spiceowl.pdk-discovery.v1','source_sha256':discovery['source_sha256'],
          'required_masters':discovery['required_masters'],'roots':roots,
          'requested_cds_libs':discovery['cds_libs'],'search_hints':discovery['search_hints'],
          'audit_limits':limits,'discovery_limits':dl,'phase':'directory_inventory','snapshot':True,
          'status':'incomplete','search_complete':False,'results_omitted':True,
          'files':[],'declarations':[],'entry_candidates':[],
          'directory_suggestions':[],'directory_suggestions_truncated':suggestions_truncated,
          'traversal':{'effective_roots':effective_roots,'requested_root_count':len(roots),
                       'directories_scanned':len(walked),'pending_directories':pending_directory_count,
                       'overlapping_roots_removed':len(roots)-len(effective_roots)},
          'summary':{'observed_candidate_file_count':len(file_candidates),'model_files_read':0,
                     'observed_unknown_count':issue_count,'observed_directory_suggestion_count':len(directory_suggestions)},
          'bytes_read':total_bytes,'unknown_count':issue_count+1,
          'unknown':[{'code':code} for code in snapshot_codes],'unknown_truncated':issue_count+1>len(snapshot_codes),
          'ambiguities':[{'code':'model_scan_not_started','requires':'complete_model_discovery'}],
          'selection_made':False,'profile_registered':False,
          'cache_scope':'observed directory metadata only; no model/profile or launch freshness evidence',
          'scope_note':'Directory inventory snapshot before model files are read. A timeout may retain these observed directories for a narrower search; no model discovery success is implied.',
          'discovery_id':'0'*64}
snapshot_prefix=discovery['directory_snapshot_prefix']
snapshot_used=len(snapshot_prefix.encode('ascii'))+len(json.dumps(snapshot,sort_keys=True).encode('utf-8'))+1
if snapshot_used<=discovery['directory_snapshot_max_bytes']:
    for row in sorted(directory_suggestions.values(),key=directory_suggestion_priority):
        extra=len(json.dumps(row,sort_keys=True).encode('utf-8'))+(2 if snapshot['directory_suggestions'] else 0)
        if snapshot_used+extra>discovery['directory_snapshot_max_bytes']:
            snapshot['directory_suggestions_truncated']=True;continue
        snapshot['directory_suggestions'].append(row);snapshot_used+=extra
    snapshot['discovery_id']=stable({k:v for k,v in snapshot.items() if k!='discovery_id'})
    sys.stdout.write(snapshot_prefix+json.dumps(snapshot,sort_keys=True)+'\n')
    sys.stdout.flush()
else:
    # Full source/root/limit binding cannot be elided merely to emit a progress
    # message. The final result reports this metadata-size gap explicitly.
    issue('directory_snapshot_metadata_limit')

for path in sorted(file_candidates[:limits['max_files']],key=priority):
    load(path,role='model',parse=True)

include_scanned=set()
pending_files=deque(parsed)
queued_files=set(parsed)
while pending_files:
    path=pending_files.popleft();queued_files.discard(path)
    if path in include_scanned: continue
    include_scanned.add(path)
    for current,line,kind,value in parsed[path]['records']:
        if kind!='include': continue
        dep=resolve_path(value['path'],os.path.dirname(path))
        if dep is None: continue
        loaded=load(dep,role=value['role'],parse=value['role']!='ahdl_source')
        if loaded is not None and loaded in parsed and loaded not in include_scanned and loaded not in queued_files:
            pending_files.append(loaded);queued_files.add(loaded)
        if len(pending_files)>limits['max_edges']:
            issue('include_queue_limit',path,line);pending_files=[];break

all_declarations, includes, tmi_refs, graph, graph_by_key = [], [], [], [], {}
build_source_indexes()
required_name_set=set(discovery['required_masters'])
declaration_name_counts={}
for path,data in sorted(parsed.items()):
    sections=[None]+sorted(data['sections'])
    for section in sections:
        if len(graph)>=dl['max_graph_nodes']: issue('graph_node_limit',path); break
        node={'node_id':stable([path,section]),'path':path,
              'section':data['section_spellings'].get(section,section),'section_key':section,
              'master_coverage':[],'include_targets':[],'unknown_codes':[],'tmi_references':[]}
        graph.append(node);graph_by_key[(path,section)]=node
        for current,line,kind,value in scoped_records(data,section):
            if kind=='master':
                if value['name'] in required_name_set:
                    node['master_coverage'].append(value['name'])
                key=(path,line,value['name'])
                if key not in declaration_keys:
                    declaration_keys.add(key)
                    declaration_name_counts[value['name']]=declaration_name_counts.get(value['name'],0)+1
                    all_declarations.append(dict(value,path=path,line=line,
                                                 section=data['section_spellings'].get(current,current),
                                                 exact_name_match=value['name'] in required_name_set))
            elif kind=='include':
                dep=resolve_path(value['path'],os.path.dirname(path))
                target_section=value['section'].lower() if value['section'] is not None else None
                if dep is None: node['unknown_codes'].append('include_outside_roots'); continue
                edge={'from':path,'from_section':node['section'],'line':line,'to':dep,
                      'section':value['section'],'role':value['role']}
                if len(includes)>=limits['max_edges']: issue('graph_edge_limit',path,line); break
                includes.append(edge)
                node['include_targets'].append([dep,target_section])
                if value['role']=='ahdl_source': node['unknown_codes'].append('opaque_ahdl_scope')
            elif kind=='unknown':
                node['unknown_codes'].append(value)
            elif kind=='tmi':
                dep=resolve_path(value['path'],os.path.dirname(path))
                if dep is None: node['unknown_codes'].append('tmi_outside_roots'); continue
                row={'source_path':path,'section':data['section_spellings'].get(current,current),
                     'applied_scopes':[node['section']],'line':line,'attribute':value['attribute'],
                     'directory':dep,'platform_candidates':[]}
                node['tmi_references'].append(row);tmi_refs.append(row)
        node['master_coverage']=sorted(set(node['master_coverage']))

# Platform directories are observed, not selected from the local architecture.
incoming_by_path={}
for source_path,data in sorted(parsed.items()):
    for current,line,kind,value in data['include_records']:
        target=resolve_path(value['path'],os.path.dirname(source_path))
        if target is None: continue
        incoming_by_path.setdefault(target,[]).append({'from':source_path,
            'from_section':data['section_spellings'].get(current,current),'line':line,
            'to':target,'section':value['section'],'role':value['role']})

source_static_metadata_cache={}
literal_json_size_cache={}
def static_source_metadata(path):
    data=parsed[path];incoming=incoming_by_path.get(path,[])
    observed=[None]+sorted(data['section_spellings'].values())
    global_lines=data['global_include_lines']
    incoming_sample=[];incoming_bytes=0
    for row in incoming:
        extra=len(json.dumps(row,sort_keys=True,ensure_ascii=True).encode('utf-8'))
        if len(incoming_sample)>=8 or incoming_bytes+extra>1024: continue
        incoming_sample.append(row);incoming_bytes+=extra
    result={'observed_sections':observed,
        'source_role_evidence':{'global_scope_present':data['global_scope_present'],
            'incoming_includes':incoming_sample,'incoming_includes_truncated':len(incoming_sample)<len(incoming),
            'incoming_include_count':len(incoming),
            'global_incoming_include_count':sum(1 for row in incoming if row['from_section'] is None),
            'incoming_evidence_scope':'bounded samples of observed include declarations; counts include omitted samples',
            'global_include_lines':global_lines[:64],'global_include_lines_truncated':len(global_lines)>64,
            'observed_section_count':len(data['sections'])},
        'prelude_candidates':[],
        'literal_evidence_scope':'required-device declarations and separately labeled non-device context; source literals are not process qualification'}
    for section in observed:
        if section is not None and section.lower() in ('pre_simu','pre_sim','init','global'):
            result['prelude_candidates'].append({'path':path,'section':section,
                'basis':'observed_prelude_section_label','confidence':'suggested','auto_prefill_allowed':False})
    return result

def source_metadata_for_candidate(path,visited,contracts):
    if path not in source_static_metadata_cache:source_static_metadata_cache[path]=static_source_metadata(path)
    # Shared fields describe only the immutable source file. Every scope-bound
    # counter, truncation flag and literal collection starts fresh per candidate.
    result=dict(source_static_metadata_cache[path],nominal_temperature_evidence=[],
        nominal_voltage_evidence=[],voltage_limit_evidence=[],literal_evidence_truncated=False,
        literal_evidence_available_count=0,literal_declaration_count=0)
    selected={}
    for source_path,section in visited: selected.setdefault(source_path,set()).add(section)
    primitive_bindings={};wrapper_bindings={}
    for model in contracts:
        for evidence in model['evidence']:
            if evidence.get('kind')=='declaration':
                wrapper_bindings.setdefault((evidence['path'],evidence['line']),set()).add(model['name'])
            if evidence.get('kind')=='primitive_model' or evidence.get('family'):
                key=(evidence['path'],evidence['line'],evidence['name'],evidence.get('owner_line'))
                primitive_bindings.setdefault(key,set()).add(model['name'])
    literal_candidates=[];literal_groups={}
    for source_path in sorted(selected):
        source=parsed.get(source_path)
        if source is None: continue
        state=source['contract_state']
        result['literal_evidence_truncated']=result['literal_evidence_truncated'] or state['literals_truncated']
        literal_rows=[]
        for section in sorted(selected[source_path]|set([None]),key=lambda value:value or ''):
            scope=contract_scope_index.get((source_path,section))
            if scope is not None:literal_rows.extend(scope['literals'])
        for row in sorted(literal_rows,key=lambda item:item['line']):
            bound=set()
            if row['model_name'] is not None:
                bound=primitive_bindings.get((source_path,row['line'],row['model_name'],row['owner_line']),set())
                if not bound: continue
            elif row['owner_line'] is not None:
                bound=wrapper_bindings.get((source_path,row['owner_line']),set())
                if not bound: continue
            literal_scope=('device_scoped' if bound else 'global' if row['section_key'] is None else
                           'preamble' if row['section_key'] in ('pre_simu','pre_sim','init','global') else 'section')
            field=row['kind']+'_evidence'
            item={k:row[k] for k in ('line','parameter','literal','value','unit','basis','model_name','owner_line')}
            item.update(path=source_path,section=source['section_spellings'].get(row['section_key'],row['section_key']),
                        scope=literal_scope,required_models=sorted(bound))
            group_key=(field,source_path,row['section_key'],literal_scope,row['owner_line'],tuple(sorted(bound)),
                       row['parameter'].lower(),row['value'],row['unit'])
            group=literal_groups.get(group_key)
            if group is None:
                group={'field':field,'item':item,'lines':set()}
                literal_groups[group_key]=group;literal_candidates.append(group)
            group['lines'].add(row['line'])
    literal_count=0;literal_bytes=0
    result['literal_evidence_available_count']=len(literal_candidates)
    for group in sorted(literal_candidates,key=lambda group:0 if group['item']['scope']=='device_scoped' else 1):
        field=group['field'];item=group['item'];lines=sorted(group['lines'])
        item.update(declaration_count=len(lines),declaration_lines=lines[:16],declaration_locations_truncated=len(lines)>16)
        result['literal_declaration_count']+=len(lines)
        size_key=tuple((key,tuple(value) if isinstance(value,list) else value) for key,value in sorted(item.items()))
        extra=literal_json_size_cache.get(size_key)
        if extra is None:
            extra=len(json.dumps(item,sort_keys=True,ensure_ascii=True).encode('utf-8'))
            if len(literal_json_size_cache)<8192:literal_json_size_cache[size_key]=extra
        if literal_count>=32 or literal_bytes+extra>4096: result['literal_evidence_truncated']=True;continue
        result[field].append(item);literal_count+=1;literal_bytes+=extra
    return result

platform_cache={}
for row in tmi_refs:
    directory_path=row['directory']
    if directory_path not in platform_cache:
        found=[]
        try:
            if not os.path.isdir(directory_path): raise ValueError('tmi_directory_missing')
            names=os.listdir(directory_path)
            if len(names)>dl['max_directory_entries']: raise ValueError('tmi_directory_entry_limit')
            for name in sorted(names):
                base=resolve_path(os.path.join(directory_path,name))
                if base is None or not os.path.isdir(base): continue
                library=resolve_path(os.path.join(base,'libTMImodel.so'))
                if library is None or not os.path.isfile(library): continue
                if len(found)>=dl['max_platform_candidates']:
                    issue('tmi_platform_limit',directory_path); break
                loaded=load(library,role='dynamic_library',parse=False)
                if loaded:
                    found.append({'platform':name,'path':loaded,'relative_library':name+'/libTMImodel.so',
                                  'sha256':files[loaded]['sha256'],'bytes':files[loaded]['bytes']})
        except OSError: issue('tmi_directory_unreadable',directory_path)
        except ValueError as error: issue(str(error),directory_path)
        if not found: issue('tmi_platform_library_not_found',directory_path)
        platform_cache[directory_path]=found
    row['platform_candidates']=platform_cache[directory_path]

entry_candidates, ambiguities = [], []
platform_signatures={directory:stable(options) for directory,options in platform_cache.items()}
entry_nodes=sorted(graph,key=lambda node:(0 if node['include_targets'] else 1,node['path'],node['section_key'] or ''))
for node in entry_nodes:
    pending=[(node['path'],node['section_key'])]
    visited=set();covered=set();unresolved=[];candidate_tmi=[];candidate_tmi_by_key={}
    while pending:
        key=tuple(pending.pop())
        if key in visited: continue
        if len(visited)>=limits['max_sections']:
            unresolved.append('section_closure_limit');break
        visited.add(key)
        current=graph_by_key.get(key)
        if current is None:
            unresolved.append('include_target_not_scanned');continue
        covered.update(current['master_coverage'])
        unresolved.extend(current['unknown_codes'])
        for reference in current['tmi_references']:
            tmi_key=(reference['source_path'],reference['line'],reference['section'],reference['attribute'],
                     reference['directory'],platform_signatures.get(reference['directory']))
            if tmi_key not in candidate_tmi_by_key:
                combined=dict(reference,applied_scopes=list(reference['applied_scopes']))
                candidate_tmi_by_key[tmi_key]=combined;candidate_tmi.append(combined)
            else:
                combined=candidate_tmi_by_key[tmi_key]
                for scope in reference['applied_scopes']:
                    if scope not in combined['applied_scopes']: combined['applied_scopes'].append(scope)
        pending.extend(current['include_targets'])
    if not covered: continue
    # Several physical TMI directives can share a source file, directory and
    # platform evidence. Store that common evidence once, while retaining every
    # declaration and its exact applied sections as separate records.
    tmi_groups=[];tmi_groups_by_key={}
    for reference in candidate_tmi:
        group_key=(reference['source_path'],reference['directory'],platform_signatures.get(reference['directory']))
        if group_key not in tmi_groups_by_key:
            group={'source_path':reference['source_path'],'directory':reference['directory'],
                   'platform_candidates':reference['platform_candidates'],'declarations':[]}
            tmi_groups_by_key[group_key]=group;tmi_groups.append(group)
        tmi_groups_by_key[group_key]['declarations'].append({key:reference[key]
            for key in ('line','section','attribute','applied_scopes')})
    candidate_tmi=tmi_groups
    if len(entry_candidates)>=dl['max_candidates']:
        issue('entry_candidate_limit');break
    path=node['path']
    scopes=[node['section']]
    candidate={'entry_path':path,'entry_sha256':files[path]['sha256'],
               'section_candidates':scopes,'available_sections':[None]+sorted(parsed[path]['section_spellings'].values()),
               'entry_role':'include_entry_candidate' if node['include_targets'] else 'definition_only',
               'master_coverage':sorted(covered),'missing_masters':sorted(set(discovery['required_masters'])-covered),
               'dependency_paths':sorted(set(k[0] for k in visited if k[0] in files)),
               'dependency_scopes':[{'path':k[0],'section':graph_by_key[k]['section']}
                                    for k in sorted(visited,key=lambda v:(v[0],v[1] or '')) if k in graph_by_key],
               'coverage_complete':not unresolved and not (set(discovery['required_masters'])-covered),
               'unknown_codes':sorted(set(unresolved)),'tmi_candidates':candidate_tmi,
               'models':model_contracts_for_scopes(visited,unresolved),
               'additional_sections_may_be_required':True,'ready_to_run':False}
    candidate.update(source_metadata_for_candidate(path,visited,candidate['models']))
    candidate['candidate_id']=stable({'source_sha256':discovery['source_sha256'],
                                      'entry_path':path,'sections':scopes,
                                      'required_masters':discovery['required_masters'],
                                      'dependency_scopes':candidate['dependency_scopes'],
                                      'files':[[p,files[p]['sha256']] for p in candidate['dependency_paths']],
                                      'tmi_candidates':candidate_tmi})
    entry_candidates.append(candidate)

complete=[c for c in entry_candidates if not c['missing_masters']]
if len(complete)>1: ambiguities.append({'code':'multiple_entry_or_section_candidates','candidate_ids':[c['candidate_id'] for c in complete]})
if not complete and entry_candidates: ambiguities.append({'code':'masters_split_across_sections_or_files','requires':'explicit_entry_and_section_combination'})
for master in discovery['required_masters']:
    count=declaration_name_counts.get(master,0)
    if count>1: ambiguities.append({'code':'multiple_master_definitions','master':master,'definition_count':count})
missing=sorted(set(discovery['required_masters'])-set(declaration_name_counts))
if missing: ambiguities.append({'code':'masters_not_found','masters':missing})
if any(len(p)>1 for p in platform_cache.values()): ambiguities.append({'code':'multiple_tmi_platforms','requires':'simulator_platform_evidence'})
if any(n['unknown_codes'] for n in graph): issue('unsupported_scanned_scope')
if any(c['unknown_codes'] for c in entry_candidates): issue('incomplete_candidate_dependency_scope')

result={'schema':'spiceowl.pdk-discovery.v1','source_sha256':discovery['source_sha256'],
        'required_masters':discovery['required_masters'],'roots':roots,
        'requested_cds_libs':discovery['cds_libs'],'search_hints':discovery['search_hints'],
        'search_complete':issue_count==0,'status':'candidates' if entry_candidates else 'missing',
        'search_policy':{'candidate_extensions':sorted(extensions),'hidden_directories_scanned':False,
                         'explicit_include_extensions_restricted':False,'symlinks':'within_authorized_roots_only',
                         'directory_order':'root_round_robin_preferred_subtree_bfs',
                         'entry_candidate_order':'include_targets_first_then_path_section',
                         'priority_basis':'generic_model_spectre_spice_directory_names_and_explicit_hints'},
        'traversal':{'effective_roots':effective_roots,'requested_root_count':len(roots),
                     'directories_scanned':len(walked),'pending_directories':pending_directory_count,
                     'overlapping_roots_removed':len(roots)-len(effective_roots)},
        'directory_suggestions':sorted(directory_suggestions.values(),key=directory_suggestion_priority),
        'directory_suggestions_truncated':suggestions_truncated,
        'files':[dict(files[p],sections=sorted(parsed[p]['section_spellings'].values()) if p in parsed else []) for p in sorted(files)],
        'catalogs':catalogs,'root_suggestions':root_suggestions,
        'declarations':all_declarations,'section_graph':graph,'includes':includes,
        'entry_candidates':entry_candidates,'ambiguities':ambiguities,
        'unknown':issues,'unknown_count':issue_count,'unknown_truncated':issue_count>len(issues),
        'audit_limits':limits,'discovery_limits':dl,'bytes_read':total_bytes,
        'selection_made':False,'profile_registered':False,
        'cache_scope':'metadata fingerprints only; never a launch-time pin or freshness proof',
        'scope_note':'Definition and include graph candidates only. Entry sections, polarity, terminal semantics, TMI platform and experiment intent require confirmation and a fresh dependency audit.'}
encoded=json.dumps(result,sort_keys=True,ensure_ascii=True)
if len(encoded.encode('utf-8'))>2000000:
    # Diagnostic graph/edge listings duplicate candidate closure evidence.
    # Omit only those details first; never trim the model/selection evidence or
    # promote an incomplete search merely to make the response fit.
    compact=dict(result,section_graph=[],includes=[],detail_omissions=[
        {'field':'section_graph','observed_count':len(graph),'reason':'response_limit'},
        {'field':'includes','observed_count':len(includes),'reason':'response_limit'}])
    compact_encoded=json.dumps(compact,sort_keys=True,ensure_ascii=True)
    if len(compact_encoded.encode('utf-8'))<=2000000:
        result=compact;encoded=compact_encoded
    else:
        # Optional recommendation samples must not displace model contracts.
        # Keep exact observed counts and flag missing context without changing
        # static contract confidence or claiming complete operating evidence.
        metadata_candidates=[];literal_omissions=0;incoming_omissions=0
        for candidate in compact['entry_candidates']:
            smaller=dict(candidate)
            for field in ('nominal_temperature_evidence','nominal_voltage_evidence','voltage_limit_evidence'):
                literal_omissions+=len(smaller.get(field,[]))
                if smaller.get(field): smaller['literal_evidence_truncated']=True
                smaller[field]=[]
            role=dict(smaller.get('source_role_evidence',{}))
            incoming_omissions+=len(role.get('incoming_includes',[]))
            if role.get('incoming_includes'): role['incoming_includes_truncated']=True
            role['incoming_includes']=[];smaller['source_role_evidence']=role
            metadata_candidates.append(smaller)
        metadata_compact=dict(compact,entry_candidates=metadata_candidates,
            detail_omissions=compact['detail_omissions']+[
                {'field':'entry_candidates.*.operating_literal_evidence','observed_count':literal_omissions,'reason':'response_limit'},
                {'field':'entry_candidates.*.source_role_evidence.incoming_includes','observed_count':incoming_omissions,'reason':'response_limit'}])
        compact_encoded=json.dumps(metadata_compact,sort_keys=True,ensure_ascii=True)
        if len(compact_encoded.encode('utf-8'))<=2000000:
            result=metadata_compact;encoded=compact_encoded
if len(encoded.encode('utf-8'))>2000000:
    original=result
    field_bytes={key:len(json.dumps(value,sort_keys=True,ensure_ascii=True).encode('utf-8'))
                 for key,value in original.items()}
    candidate_sizes=[len(json.dumps(candidate,sort_keys=True,ensure_ascii=True).encode('utf-8'))
                     for candidate in entry_candidates]
    candidate_field_bytes={}
    for candidate in entry_candidates:
        for key,value in candidate.items():
            candidate_field_bytes[key]=candidate_field_bytes.get(key,0)+len(json.dumps(value,sort_keys=True,ensure_ascii=True).encode('utf-8'))
    summary_codes=['response_limit']+sorted(set(row['code'] for row in issues)-set(['response_limit']))[:max(0,limits['max_issues']-1)]
    result={'schema':'spiceowl.pdk-discovery.v1','source_sha256':discovery['source_sha256'],
            'required_masters':discovery['required_masters'],'roots':roots,'search_complete':False,
            'requested_cds_libs':discovery['cds_libs'],'search_hints':discovery['search_hints'],
            'audit_limits':limits,'discovery_limits':dl,
            'status':'incomplete','files':[],'declarations':[],'entry_candidates':[],
            'search_policy':original['search_policy'],'traversal':original['traversal'],
            'directory_suggestions':[],'directory_suggestions_truncated':suggestions_truncated,
            'directory_suggestion_order':'root_children_then_model_or_unscanned_frontier_then_shallow_depth',
            'summary':{'observed_file_count':len(files),'observed_declaration_count':len(all_declarations),
                       'observed_entry_candidate_count':len(entry_candidates),'observed_graph_node_count':len(graph),
                       'observed_include_count':len(includes),'observed_unknown_count':issue_count,
                       'observed_directory_suggestion_count':len(directory_suggestions),
                       'field_bytes':field_bytes,'candidate_field_bytes':candidate_field_bytes,
                       'candidate_bytes':{'count':len(candidate_sizes),'min':min(candidate_sizes) if candidate_sizes else 0,
                                          'max':max(candidate_sizes) if candidate_sizes else 0,'total':sum(candidate_sizes)},
                       'full_payload_bytes':len(encoded.encode('utf-8')),
                       'compact_payload_bytes':len(compact_encoded.encode('utf-8')),
                       'field_bytes_basis':'UTF-8 JSON-encoded values; excludes object keys and delimiters'},
            'bytes_read':total_bytes,'unknown_count':issue_count+1,
            'unknown':[{'code':code} for code in summary_codes],
            'unknown_truncated':issue_count+1>len(summary_codes),
            'ambiguities':[{'code':'response_limit','requires':'narrow_search_roots'}],
            'results_omitted':True,'selection_made':False,'profile_registered':False,
            'cache_scope':original['cache_scope'],
            'scope_note':'Detailed discovery exceeded the response limit. Retained directory suggestions are actual observations for a narrower search, not model qualification or an executable profile.'}
    # Keep exact observed paths and recompute the receipt digest. Suggestions
    # are prioritized and bounded by both the existing count cap and encoded
    # output bytes; Unicode path expansion must not defeat the transport cap.
    used=len(json.dumps(result,sort_keys=True,ensure_ascii=True).encode('utf-8'))
    for row in sorted(original['directory_suggestions'],key=directory_suggestion_priority):
        extra=len(json.dumps(row,sort_keys=True,ensure_ascii=True).encode('utf-8'))+(2 if result['directory_suggestions'] else 0)
        if len(result['directory_suggestions'])>=dl['max_candidates'] or used+extra>1998000:
            result['directory_suggestions_truncated']=True;continue
        result['directory_suggestions'].append(row);used+=extra
result['discovery_id']=stable(result)
print(json.dumps(result,sort_keys=True))
'''


_LIBRARY_SERIALIZER = r'''
result.pop('source_sha256',None)
result.pop('required_masters',None)
result.update(schema='spiceowl.pdk-library.v1',mode='full_library',
    requested_roots=list(config['roots']),
    selection_made=False,profile_registered=False,
    cache_scope='observed per-device library metadata; never launch freshness',
    scope_note='Full-library attempt within the declared roots and finite parser/search policy; not process qualification.')
original_candidate_count=len(result['entry_candidates'])
omitted_candidates=0
# Model evidence is content-addressed only after its active-scope contract has
# been computed. Equal names alone never establish equal contracts. Keep strong
# references in the identity cache so Python cannot recycle an object's id.
library_contracts={};library_contract_ids={}
for candidate in result['entry_candidates']:
    references=[]
    for model in candidate['models']:
        cached=library_contract_ids.get(id(model))
        if cached is None:
            identity=stable(model)
            library_contract_ids[id(model)]=(model,identity)
        else:
            identity=cached[1]
        library_contracts.setdefault(identity,model)
        references.append({'name':model['name'],'contract_id':identity})
    candidate['models']=references
result['contract_storage']='shared-model-contracts.v1'
result['model_contracts']=library_contracts
def library_coverage():
    indexed=sorted(set(model['name'] for candidate in result['entry_candidates'] for model in candidate['models']))
    unindexed=sorted(set(library_all_names)-set(indexed))
    entry_limits=any('library_entry_model_limit' in candidate['unknown_codes'] for candidate in result['entry_candidates'])
    complete=result['search_complete'] and not unindexed and not omitted_candidates and not entry_limits
    result['coverage']={'kind':'full_library_attempt','complete_within_policy':bool(complete),
        'whole_pdk_qualified':False,'observed_model_names':library_all_names[:4096],
        'observed_model_count':len(library_all_names),'observed_names_truncated':len(library_all_names)>4096,
        'indexed_model_names':indexed,'indexed_model_count':len(indexed),
        'unindexed_model_names':unindexed[:4096],'unindexed_model_count':len(unindexed),
        'observed_entry_count':original_candidate_count,'omitted_entry_count':omitted_candidates,
        'entry_scope_truncated':entry_limits,'coverage_basis':'observed declarations and stored candidate closures'}
    result['status']='complete' if complete else 'partial'
library_coverage()
encoded=json.dumps(result,sort_keys=True,separators=(',',':')).encode('utf-8')
if len(encoded)>16744448:
    result['detail_omissions']=[{'field':key,'observed_count':len(result.get(key,[])),'reason':'library_response_limit'}
        for key in ('section_graph','includes')]
    result['section_graph']=[];result['includes']=[]
    encoded=json.dumps(result,sort_keys=True,separators=(',',':')).encode('utf-8')
if len(encoded)>16744448:
    # Keep the largest fitting prefix of the priority-ordered entries. A
    # candidate is always retained with all of its referenced evidence. Binary
    # search avoids serializing a near-limit receipt once for every removed row.
    library_entries=list(result['entry_candidates'])
    low,high,best=0,len(library_entries)-1,None
    while low<=high:
        count=(low+high)//2
        result['entry_candidates']=library_entries[:count]
        referenced=set(row['contract_id'] for candidate in result['entry_candidates'] for row in candidate['models'])
        result['model_contracts']={identity:library_contracts[identity] for identity in sorted(referenced)}
        omitted_candidates=original_candidate_count-count;library_coverage()
        size=len(json.dumps(result,sort_keys=True,separators=(',',':')).encode('utf-8'))
        if size<=16744448:
            best=count;low=count+1
        else:
            high=count-1
    if best is None:
        raise ValueError('library_metadata_response_limit: narrow the observed root scope')
    result['entry_candidates']=library_entries[:best]
    referenced=set(row['contract_id'] for candidate in result['entry_candidates'] for row in candidate['models'])
    result['model_contracts']={identity:library_contracts[identity] for identity in sorted(referenced)}
    omitted_candidates=original_candidate_count-best;library_coverage()
    if len(json.dumps(result,sort_keys=True,separators=(',',':')).encode('utf-8'))>16744448:
        raise ValueError('library_metadata_response_limit: narrow the observed root scope')
result['library_id']=stable(result)
print(json.dumps(result,sort_keys=True,separators=(',',':')))
'''

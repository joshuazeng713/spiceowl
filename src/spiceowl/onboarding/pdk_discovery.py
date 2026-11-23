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


def inspect_netlist(source, *, source_id="input.scs"):
    """Inspect supplied flat or subcircuit Spectre/SPICE text, without IO.

    Ordered instance nodes are evidence, not a guessed d/g/s/b mapping. Numeric
    W/L literals are reported in metres without applying a model scale factor.
    Device-name spelling never supplies polarity or process/VT qualification.
    """
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
            "sections, records, current = set(), [], None\n    section_spellings={}\n    contract_state={'stack':[],'wrappers':[],'models':[]}",
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
contract_name=r'[A-Za-z_][A-Za-z0-9_.$:+-]*'
contract_header=re.compile(r'(?i)^(?:inline\s+)?\.?subckt\s+('+contract_name+r')\s*(.*)$')
contract_model=re.compile(r'(?i)^\.?model\s+('+contract_name+r')\s+('+contract_name+r')\b')
contract_call=re.compile(r'^('+contract_name+r')\s*\(([^()]*)\)\s*('+contract_name+r')(?:\s+(.*))?$')
contract_spice_call=re.compile(r'(?i)^(M[A-Za-z0-9_.$:+-]*)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+('+contract_name+r')(?:\s+(.*))?$')
mos_families=set(['bsim3','bsim3v3','bsim4','mos1','mos2','mos3'])

def capture_contract(state,line,number,section):
    global directive_count
    header=contract_header.match(line)
    model=contract_model.match(line)
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
             'instances':[],'instance_count':0,'unsupported':[],'closed':False}
        if len(ports)!=4 or len(set(ports))!=4: row['unsupported'].append('non_bijective_four_port_interface')
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
        if '{' in line or '}' in line: polarity=None;reason=None
        state['models'].append({'name':name,'family':family,'polarity':polarity,
            'type_literal':values[0] if len(values)==1 and len(values[0])<=16 else None,
            'evidence_rule':reason,'line':number,'section_key':section,
            'owner_line':state['stack'][-1]['line'] if state['stack'] else None})
        return
    if not state['stack']: return
    row=state['stack'][-1]
    if row['name'].lower() not in required: return
    if re.match(r'(?i)^(?:parameters\b|\.param\b)',line): return
    match=contract_call.match(line)
    if match:
        name,nodes,master,params=match.groups(); nodes=nodes.split()
    else:
        match=contract_spice_call.match(line)
        if match:
            parts=match.groups();name=parts[0];nodes=list(parts[1:5]);master=parts[5];params=parts[6]
    if match:
        row['instance_count']+=1
        if row['instance_count']<=2:
            row['instances'].append({'name':name,'ordered_nets':nodes[:64], 'master':master,'line':number})
        if len(nodes)!=4 or re.search(r'(?i)\b(?:type|model)\s*=',params or ''):
            row['unsupported'].append('unsupported_mos_instance')
    else:
        if len(row['unsupported'])<16: row['unsupported'].append('unsupported_wrapper_statement')

def model_contracts_for_scopes(visited,unresolved):
    wrappers,models={},{}
    wrapper_keys,model_keys=set(),set()
    sections_by_path={}
    for path,section in visited: sections_by_path.setdefault(path,set()).add(section)
    for path in sorted(sections_by_path):
        data=parsed.get(path)
        if data is None: continue
        state=data['contract_state']
        for source,target,keys in ((state['wrappers'],wrappers,wrapper_keys),(state['models'],models,model_keys)):
            for row in source:
                if row['section_key'] is None or row['section_key'] in sections_by_path[path]:
                    key=(path,row['line'],row['section_key'],row.get('owner_line'))
                    if key in keys: continue
                    keys.add(key)
                    item=dict(row,path=path,section=data['section_spellings'].get(row['section_key'],row['section_key']))
                    target.setdefault(row['name'],[]).append(item)
    output=[]
    for name in discovery['required_masters']:
        evidence=[];polarity=None;roles=None;reason='definition_not_found'
        matches=wrappers.get(name,[])+[r for r in models.get(name,[]) if r['owner_line'] is None]
        if len(matches)>1: reason='multiple_active_model_definitions'
        elif len(matches)==1:
            row=matches[0]
            evidence.append({'kind':'declaration','path':row['path'],'line':row['line'],'section':row['section'],'name':name})
            if 'instances' in row:
                reason='unsupported_wrapper_contract'
                if row['closed'] and not row['unsupported'] and row['instance_count']==1:
                    call=row['instances'][0]
                    evidence.append(dict(call,kind='direct_instance',path=row['path']))
                    model_matches=[m for m in models.get(call['master'],[]) if
                                   m['owner_line'] is None or m['path']==row['path'] and m['owner_line']==row['line']]
                    reason='primitive_model_not_resolved'
                    if len(model_matches)==1:
                        primitive=model_matches[0]
                        reason='unsupported_primitive_type_or_terminal_mapping'
                        nodes=call['ordered_nets']
                        if primitive['polarity'] and len(nodes)==4 and len(set(nodes))==4 and set(nodes)==set(row['ports']):
                            polarity=primitive['polarity']
                            mapping=dict(zip(nodes,['d','g','s','b']))
                            roles=[mapping[p] for p in row['ports']]
                            reason='single_mos_bijective_wrapper'
                        evidence.append({k:primitive[k] for k in ('name','path','line','section','family','type_literal','evidence_rule')})
                    elif len(model_matches)>1: reason='multiple_active_primitive_definitions'
            else:
                polarity=row['polarity'];roles=['d','g','s','b'] if polarity else None
                reason=row['evidence_rule'] or 'unsupported_primitive_type'
                evidence.append({k:row[k] for k in ('name','path','line','section','family','type_literal','evidence_rule')})
        verified=polarity is not None and roles is not None and not unresolved
        output.append({'name':name,'polarity':polarity,'ordered_terminal_roles':roles,
            'evidence':evidence,'confidence':'verified' if verified else 'suggested' if polarity or roles else 'unknown',
            'reason':reason,'needs_confirmation':not verified,
            'scope':'static source contract only; no simulator or process qualification'})
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
for path,data in sorted(parsed.items()):
    sections=[None]+sorted(data['sections'])
    for section in sections:
        if len(graph)>=dl['max_graph_nodes']: issue('graph_node_limit',path); break
        node={'node_id':stable([path,section]),'path':path,
              'section':data['section_spellings'].get(section,section),'section_key':section,
              'master_coverage':[],'include_targets':[],'unknown_codes':[],'tmi_references':[]}
        graph.append(node);graph_by_key[(path,section)]=node
        for current,line,kind,value in data['records']:
            if current is not None and current!=section: continue
            if kind=='master':
                if value['name'] in discovery['required_masters']:
                    node['master_coverage'].append(value['name'])
                key=(path,line,value['name'])
                if key not in declaration_keys:
                    declaration_keys.add(key)
                    all_declarations.append(dict(value,path=path,line=line,
                                                 section=data['section_spellings'].get(current,current),
                                                 exact_name_match=value['name'] in discovery['required_masters']))
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
for node in graph:
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
    matches=[d for d in all_declarations if d['name']==master]
    if len(matches)>1: ambiguities.append({'code':'multiple_master_definitions','master':master,'definition_count':len(matches)})
missing=sorted(set(discovery['required_masters'])-set(d['name'] for d in all_declarations))
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

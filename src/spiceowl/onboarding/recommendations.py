"""Pure, inert onboarding drafts from supplied source and remote observations.

Ranking is assistance with selection, never a foundry qualification or a claim
that model files are interchangeable. Presets are explicit experimental choices.
This module performs no filesystem reads, SSH, environment inspection or run.
"""
import copy
import hashlib
import json
import math
from pathlib import PurePosixPath
import re

from spiceowl.domain.common import DomainError
from .environment import public_platform
from .pdk_discovery import profile_suggestions


SCHEMA = 'spiceowl.onboarding-recommendations.v1'
PRESET = {'supply_v': .9, 'frequency_hz': 1e9, 'temperature_c': 27,
          'nominal_temperature_c': 27, 'cycles': 5, 'load_f': 2e-15,
          'rise_s': 1e-11, 'fall_s': 1e-11, 'points_per_period': 1000}
PORT_NAMES = {'input_port': {'in', 'input', 'vin', 'clk'},
              'output_port': {'out', 'output', 'vout', 'clkn'},
              'vdd_port': {'vdd', 'vcc', 'hi', 'vp'},
              'vss_port': {'vss', 'gnd', 'lo', 'vn'}}
NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_.$:+-]{0,127}\Z')
HASH = re.compile(r'[a-f0-9]{64}\Z')


def _invalid():
    raise DomainError('Recommendations need bounded matching source/discovery evidence.', 'invalid_onboarding_recommendations')


def _issue(field, code, message):
    return {'field': field, 'code': code, 'message': message}


def _basis(code, kind='observation', **values):
    return {'code': code, 'kind': kind, **values}


def _field(value, confidence, basis, missing=None):
    return {'value': copy.deepcopy(value), 'confidence': confidence, 'basis': basis,
            'requires_acceptance': True, 'missing': list(missing or [])}


def _finite(value, low=-1e100, high=1e100):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def _requirements(value):
    if (not isinstance(value, dict) or value.get('schema') != 'spiceowl.netlist-model-requirements.v1'
            or not isinstance(value.get('source_sha256'), str) or not HASH.fullmatch(value['source_sha256'])
            or not isinstance(value.get('required_masters'), list) or len(value['required_masters']) > 128
            or any(not isinstance(name, str) or not NAME.fullmatch(name) for name in value['required_masters'])
            or len(set(value['required_masters'])) != len(value['required_masters'])
            or not isinstance(value.get('instances'), list) or len(value['instances']) > 4096):
        _invalid()
    for row in value['instances']:
        if (not isinstance(row, dict) or not isinstance(row.get('ordered_nets'), list)
                or not isinstance(row.get('master'), str) or not NAME.fullmatch(row['master'])
                or len(row['ordered_nets']) > 64 or any(not isinstance(net, str) or len(net) > 128 for net in row['ordered_nets'])):
            _invalid()
    return value


def _contract_rows(candidate, requirements):
    rows = candidate.get('models', []) if isinstance(candidate, dict) else []
    if not isinstance(rows, list) or len(rows) > 128: _invalid()
    result = {}
    for name in requirements['required_masters']:
        selected = [row for row in rows if isinstance(row, dict) and row.get('name') == name]
        row = selected[0] if len(selected) == 1 else {}
        contract = row.get('suggested_contract')
        valid = (isinstance(contract, dict) and set(contract) == {'polarity', 'terminals'}
                 and contract['polarity'] in ('n', 'p') and isinstance(contract['terminals'], list)
                 and len(contract['terminals']) == 4 and all(isinstance(role, str) for role in contract['terminals'])
                 and set(contract['terminals']) == {'d', 'g', 's', 'b'})
        proved = valid and row.get('auto_prefill_allowed') is True and row.get('confidence') == 'verified'
        reason = row.get('suggestion_basis') or row.get('reason')
        reason = reason if isinstance(reason, str) and re.fullmatch(r'[a-z_]{1,96}', reason) else 'model_contract_unresolved'
        gaps = [] if proved else [_issue('model_contracts.' + name, 'model_contract_confirmation',
            'The required model needs an explicit contract or complete source-backed evidence.')]
        result[name] = _field(contract if valid else None, 'observed' if proved else 'inferred' if valid else 'unknown',
            [_basis('source_model_contract', master=name, rule=reason, source_backed=bool(proved))], gaps)
        result[name]['auto_prefill_allowed'] = bool(proved)
        result[name]['fresh_audit_required'] = True
    return result


def _port_fields(requirements, candidate):
    rows = requirements['instances']
    nets = sorted({net for row in rows for net in row['ordered_nets']})
    values, fields, missing = {}, {}, []
    for role, names in PORT_NAMES.items():
        matches = [net for net in nets if net.lower() in names and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,95}', net)]
        values[role] = matches[0] if len(matches) == 1 else None
        gaps = [] if len(matches) == 1 else [_issue(role, 'port_role_ambiguous',
            'Choose one explicit port role; the supplied names do not identify it uniquely.')]
        fields[role] = _field(values[role], 'inferred' if values[role] else 'unknown',
            [_basis('explicit_net_name_role', 'heuristic', observed_matches=matches)], gaps)
        missing.extend(gaps)
    connected = (len(rows) == 2 and len(nets) == 4 and all(values.values())
        and len(set(values.values())) == 4
        and all(sum(values[role] in row['ordered_nets'] for row in rows) == 2 for role in ('input_port', 'output_port'))
        and all(any(values[role] in row['ordered_nets'] for row in rows) for role in ('vdd_port', 'vss_port')))
    if not connected:
        gap = _issue('ports', 'inverter_connectivity_unconfirmed',
            'The inverter preset requires two devices sharing input/output and two distinct named rails.')
        missing.append(gap)
        for field in fields.values():
            field.update(value=None, confidence='unknown'); field['missing'].append(gap)
        return fields, missing, False
    contracts = _contract_rows(candidate, requirements)
    complete = all(row['auto_prefill_allowed'] for row in contracts.values()) and bool(contracts)
    proved = False
    if complete:
        polarities, valid = [], True
        for row in rows:
            field = contracts.get(row.get('master'))
            if not field or len(row['ordered_nets']) != 4:
                valid = False; break
            contract = field['value']; mapping = dict(zip(contract['terminals'], row['ordered_nets']))
            rail = values['vss_port'] if contract['polarity'] == 'n' else values['vdd_port']
            polarities.append(contract['polarity'])
            valid = valid and mapping == {'d': values['output_port'], 'g': values['input_port'], 's': rail, 'b': rail}
        proved = valid and sorted(polarities) == ['n', 'p']
        if not proved:
            gap = _issue('ports', 'model_connectivity_conflict',
                'Source-backed terminal roles do not support this CMOS inverter connection.')
            missing.append(gap)
            for field in fields.values():
                field.update(value=None, confidence='unknown'); field['missing'].append(gap)
    for field in fields.values():
        field['basis'].append(_basis('two_device_net_connectivity', 'inference', model_terminals_verified=proved))
    return fields, missing, proved


def _literal_rows(candidate, requirements, field):
    if not isinstance(candidate, dict): return [], False
    rows = candidate.get(field, [])
    if not isinstance(rows, list) or len(rows) > 256: _invalid()
    primitives, wrappers = set(), set()
    for model in candidate.get('models', []):
        if not isinstance(model, dict) or model.get('name') not in requirements['required_masters']: continue
        for evidence in model.get('evidence', []):
            if not isinstance(evidence, dict): continue
            if evidence.get('kind') == 'declaration': wrappers.add((evidence.get('path'), evidence.get('line')))
            if evidence.get('kind') == 'primitive_model' or evidence.get('family'):
                primitives.add((evidence.get('path'), evidence.get('name'), evidence.get('owner_line')))
    output, unresolved = [], False
    for row in rows:
        if not isinstance(row, dict): unresolved = True; continue
        scoped_models = row.get('required_models', [])
        if not isinstance(scoped_models, list) or len(scoped_models) > 128 or any(not isinstance(name, str) for name in scoped_models): _invalid()
        scope = row.get('scope')
        if field == 'voltage_limit_evidence':
            if scope in ('global', 'preamble', 'section'): continue
            if scope != 'device_scoped':
                unresolved = True; continue
            if not set(scoped_models) & set(requirements['required_masters']): continue
        model, owner = row.get('model_name'), row.get('owner_line')
        if model is not None:
            if (row.get('path'), model, owner) not in primitives: continue
        elif owner is not None and (row.get('path'), owner) not in wrappers:
            continue
        elif field == 'voltage_limit_evidence' and model is None and owner is None:
            unresolved = True; continue
        if row.get('basis') != 'explicit_source_literal' or not _finite(row.get('value')):
            unresolved = True; continue
        output.append(row)
    return output, unresolved


def _literal_basis(rows):
    return [_basis('explicit_source_literal', parameter=row.get('parameter'), value=row['value'], unit=row.get('unit'),
        line=row.get('line'), scope=row.get('scope'), required_models=copy.deepcopy(row.get('required_models', [])),
        declaration_count=row.get('declaration_count', 1), location_samples_truncated=row.get('declaration_locations_truncated') is True,
        source_path_sha256=hashlib.sha256(str(row.get('path', '')).encode()).hexdigest()) for row in rows[:16]]


def suggest_intent(requirements, candidate=None, *, preset='inverter_smoke'):
    """Suggest explicit preset values and source-supported roles, without IO."""
    requirements = _requirements(requirements)
    if preset != 'inverter_smoke' or candidate is not None and not isinstance(candidate, dict): _invalid()
    fields = {key: _field(value, 'preset', [_basis('inverter_smoke_preset', 'preset', preset=preset,
        experimental_intent_discovered=False, model_rating_claimed=False)]) for key, value in PRESET.items()}
    ports, missing, topology = _port_fields(requirements, candidate)
    fields.update(ports)
    for evidence_field, target, units in (('nominal_voltage_evidence', 'supply_v', {'V'}),
                                         ('nominal_temperature_evidence', 'nominal_temperature_c', {'C', 'K'})):
        rows, unresolved = _literal_rows(candidate, requirements, evidence_field)
        bound = [row for row in rows if row.get('scope') == 'device_scoped']
        if target == 'supply_v' and bound:
            rows = bound
        valid = [row for row in rows if row.get('unit') in units]
        unresolved = unresolved or len(rows) != len(valid)
        values = [row['value'] - 273.15 if target == 'nominal_temperature_c' and row['unit'] == 'K' else row['value'] for row in valid]
        if any(not _finite(value, 0.000001, 1000) if target == 'supply_v' else not _finite(value, -272.15, 1726.85) for value in values):
            unresolved = True
        distinct = set(values)
        if unresolved or len(distinct) > 1:
            gap = _issue(target, 'nominal_evidence_conflict' if len(distinct) > 1 else 'nominal_evidence_unresolved',
                'Explicit nominal evidence is conflicting, incomplete or lacks supported units; choose the condition explicitly.')
            fields[target] = _field(None, 'unknown', _literal_basis(valid), [gap]); missing.append(gap)
        elif len(distinct) == 1:
            weak = target == 'supply_v' and any(row.get('scope') != 'device_scoped' for row in valid)
            fields[target] = _field(values[0], 'inferred' if weak else 'observed', _literal_basis(valid))
            if weak: fields[target]['basis'].append(_basis('global_nominal_suggestion', 'inference', device_nominal_proven=False))
            fields[target]['basis'].append(_basis('source_nominal_is_not_qualification', 'inference', requires_experimental_choice=True))
    limits, unresolved = _literal_rows(candidate, requirements, 'voltage_limit_evidence')
    applicable = []
    for row in limits:
        parameter = str(row.get('parameter', '')).lower().replace('_', '')
        if row.get('unit') != 'V' or not _finite(row['value'], 0, 1000): unresolved = True; continue
        if parameter in ('vbsmax', 'maxvbs') and topology:
            fields['supply_v']['basis'].append(_basis('body_source_tied_in_inverter', 'inference', declared_limit_v=row['value']))
            continue
        applicable.append(row)
    if unresolved or isinstance(candidate, dict) and candidate.get('literal_evidence_truncated') is True:
        gap = _issue('supply_v', 'voltage_evidence_incomplete', 'Resolve omitted or invalid model limit evidence before accepting supply conditions.')
        missing.append(gap); fields['supply_v']['missing'].append(gap)
    blocked = False
    if applicable:
        upper = min(row['value'] for row in applicable)
        fields['supply_v']['declared_upper_bound_v'] = upper
        fields['supply_v']['basis'].extend(_literal_basis(applicable))
        fields['supply_v']['basis'].append(_basis('nominal_supply_limit_screen', 'inference', operating_qualification_proven=False))
        value = fields['supply_v']['value']
        if value is not None and value > upper:
            gap = _issue('supply_v', 'supply_exceeds_declared_limit', 'The suggested supply exceeds applicable explicit source limits; choose a new condition or an explicit override.')
            missing.append(gap); fields['supply_v']['missing'].append(gap)
            fields['supply_v']['requires_override'] = True; blocked = True
    return {'preset': preset, 'fields': fields, 'status': 'blocked' if blocked else 'missing' if missing else 'suggested',
        'missing': missing, 'requires_acceptance': True, 'scientific_intent_discovered': False,
        'operating_qualification_proven': False}


def _corner_class(sections):
    names = [value.lower() for value in sections if isinstance(value, str)]
    if any(re.search(r'(^|[_-])(mc|monte|random|statistical)([_-]|$)', name) for name in names): return 'statistical'
    if any(name in ('tt', 'typical', 'nominal') or name.startswith('ttmacro') or re.search(r'(^|[_-])(tt|typical|nominal)([_-]|$)', name) for name in names): return 'typical'
    if any(re.search(r'(^|[_-])(ss|ff|sf|fs|slow|fast)([_-]|$)', name) for name in names): return 'extreme'
    return 'unspecified'


def _sections(candidate):
    values = candidate.get('section_candidates', [])
    observed = candidate.get('available_sections', [])
    if (not isinstance(values, list) or not 1 <= len(values) <= 64 or not isinstance(observed, list)
            or len(observed) > 1024 or any(value is not None and (not isinstance(value, str) or not NAME.fullmatch(value)) for value in observed)
            or any(value not in observed for value in values)):
        _invalid()
    selected = list(dict.fromkeys(values)); basis = [_basis('observed_candidate_sections', sections=selected)]
    added = []
    if _corner_class(values) == 'typical':
        for prelude in candidate.get('prelude_candidates', []):
            if (isinstance(prelude, dict) and prelude.get('path') == candidate.get('entry_path')
                    and prelude.get('basis') == 'observed_prelude_section_label'
                    and prelude.get('section') in observed and isinstance(prelude.get('section'), str)
                    and prelude['section'].lower() == 'pre_simu' and prelude['section'] not in selected):
                added.append(prelude['section'])
        if len(added) == 1:
            selected = added + selected
            basis.append(_basis('observed_prelude_convention', 'heuristic', added_sections=added,
                required_section_proven=False, fresh_audit_required=True))
    field = _field(selected, 'inferred', basis)
    field['fresh_audit_required'] = True
    field['model_contract_scope_expanded'] = selected != values
    return field


def _tmi(candidate, remote_platform):
    groups = candidate.get('tmi_candidates', [])
    if not isinstance(groups, list) or len(groups) > 256: _invalid()
    if not groups: return _field(None, 'observed', [_basis('no_tmi_reference_in_observed_closure')])
    paths = set()
    for group in groups:
        choices = group.get('platform_candidates', []) if isinstance(group, dict) else []
        if not isinstance(choices, list) or len(choices) > 64: _invalid()
        for row in choices:
            path = row.get('relative_library') if isinstance(row, dict) else None
            if isinstance(path, str) and len(path) <= 4096 and not path.startswith('/') and '..' not in PurePosixPath(path).parts:
                paths.add(path)
    platform = public_platform(remote_platform)
    families = {platform['os_release']['id'], *platform['os_release']['id_like']}
    rh = bool(families & {'rhel', 'redhat', 'centos'})
    suse = bool(families & {'suse', 'sles', 'sled', 'opensuse', 'opensuse-leap', 'opensuse-tumbleweed'})
    family = 'RH_64' if rh and not suse else 'SUSE_64' if suse and not rh else None
    usable = platform['system'] == 'Linux' and platform['machine'] == 'x86_64' and family
    matches = sorted(path for path in paths if usable and family in PurePosixPath(path).parts)
    missing = [] if len(matches) == 1 else [_issue('tmi_relative_library',
        'tmi_platform_ambiguous' if len(matches) > 1 else 'tmi_platform_unknown',
        'Choose an observed ABI candidate using this remote server platform; a compatible library still needs runtime validation.')]
    field = _field(matches[0] if len(matches) == 1 else None, 'inferred' if len(matches) == 1 else 'unknown',
        [_basis('remote_platform_abi_candidate', 'inference', platform=platform, observed_matches=matches,
            runtime_compatibility_verified=False)], missing)
    field['runtime_validation_required'] = True
    return field


def _recommend(discovery, requirements, remote_platform=None, *, preset='inverter_smoke', with_intent=True):
    """Rank observed candidates and return overridable, non-executing drafts."""
    requirements = _requirements(requirements)
    try: profile_suggestions(discovery)
    except (DomainError, ValueError, TypeError, KeyError, RecursionError): _invalid()
    if (discovery.get('source_sha256') != requirements['source_sha256']
            or discovery.get('required_masters') != requirements['required_masters']
            or not isinstance(discovery.get('entry_candidates'), list) or len(discovery['entry_candidates']) > 256): _invalid()
    required = set(requirements['required_masters']); ranked = []
    for candidate in discovery['entry_candidates']:
        if not isinstance(candidate, dict) or not isinstance(candidate.get('candidate_id'), str) or not HASH.fullmatch(candidate['candidate_id']): _invalid()
        contracts = _contract_rows(candidate, requirements); sections = _sections(candidate)
        for field in contracts.values():
            field['selected_section_scope_verified'] = field['auto_prefill_allowed'] and not sections['model_contract_scope_expanded']
            if sections['model_contract_scope_expanded']:
                field['basis'].append(_basis('additional_sections_need_contract_revalidation', 'inference',
                    original_candidate_scope_only=True, fresh_audit_required=True))
        intent = suggest_intent(requirements, candidate, preset=preset) if with_intent else None
        tmi = _tmi(candidate, remote_platform)
        coverage = candidate.get('master_coverage', [])
        if not isinstance(coverage, list) or len(coverage) > 128 or any(not isinstance(name, str) for name in coverage): _invalid()
        complete = (required <= set(coverage) and candidate.get('coverage_complete') is True
                    and not candidate.get('missing_masters') and not candidate.get('unknown_codes'))
        conflict = any(any(word in str(model.get('reason', '')) for word in ('conflict', 'ambiguous', 'multiple_active'))
            for model in candidate.get('models', []) if isinstance(model, dict))
        score, basis = 0, []
        def add(weight, code, kind='observation', **detail):
            nonlocal score
            score += weight; basis.append(_basis(code, kind, weight=weight, **detail))
        add(1000 if complete else -1000, 'observed_required_master_closure', complete=complete)
        usage = candidate.get('entry_role') == 'include_entry_candidate'
        add(100 if usage else 0, 'observed_include_entry_role', role=candidate.get('entry_role'))
        source_role = candidate.get('source_role_evidence', {})
        incoming = source_role.get('incoming_include_count') if isinstance(source_role, dict) else None
        if incoming is None and isinstance(source_role, dict) and source_role.get('incoming_includes_truncated') is False:
            incoming = len(source_role['incoming_includes']) if isinstance(source_role.get('incoming_includes'), list) else None
        if type(incoming) is int and incoming == 0:
            add(8, 'no_observed_incoming_include', 'heuristic', top_level_entry_proven=False)
        verified = sum(field['auto_prefill_allowed'] for field in contracts.values())
        add(min(30, verified * 10), 'source_backed_required_contracts', verified_count=verified)
        wrapper_count = sum(model.get('name') in required and model.get('confidence') == 'verified'
            and model.get('suggestion_basis') == 'single_mos_bijective_wrapper'
            for model in candidate.get('models', []) if isinstance(model, dict))
        if usage and complete and wrapper_count == len(required) and required:
            add(20, 'source_backed_required_wrapper_usage', required_wrapper_count=wrapper_count)
        corner = _corner_class(candidate['section_candidates'])
        add({'typical': 25, 'statistical': -25, 'extreme': -10, 'unspecified': 0}[corner], 'section_name_convention', 'heuristic', corner_class=corner,
            process_corner_qualification_proven=False)
        if sections['model_contract_scope_expanded']: add(3, 'observed_optional_prelude', 'heuristic')
        if usage and complete and corner == 'typical' and sections['model_contract_scope_expanded']:
            add(30, 'observed_prelude_typical_usage', 'heuristic',
                setup_necessity_proven=False, final_combination_requires_audit=True)
        name = PurePosixPath(str(candidate.get('entry_path', ''))).name
        if re.search(r'(?i)(^|[_\-.])(usage|entry)([_\-.]|$)', name): add(2, 'filename_entry_convention', 'heuristic')
        # Device/entry labels only assist ordering after actual required closure
        # evidence. In particular, voltage-looking filenames establish no Vdd.
        family_names = {'core', 'ulvt', 'lvt', 'svt', 'hvt', 'io'}
        required_hints = {token for master in required for token in re.split(r'[_.$:+-]', master.lower())} & family_names
        entry_hints = set(re.split(r'[_\-.]', name.lower())) & family_names
        if complete and required_hints & entry_hints:
            add(8, 'required_model_entry_name_hint', 'heuristic',
                matching_tokens=sorted(required_hints & entry_hints), device_family_proven=False)
        if 'io' in entry_hints and required_hints - {'io'} and 'io' not in required_hints:
            add(-8, 'unmatched_io_entry_name_hint', 'heuristic', device_incompatibility_proven=False)
        if intent and intent['fields']['supply_v']['confidence'] == 'observed': add(5, 'explicit_nominal_voltage_evidence')
        if conflict: add(-2000, 'conflicting_model_contracts')
        if intent and intent['status'] == 'blocked': add(-2000, 'declared_voltage_incompatibility')
        missing = [gap for field in contracts.values() for gap in field['missing']] + tmi['missing'] + (intent['missing'] if intent else [])
        if not complete: missing.append(_issue('candidate', 'model_closure_incomplete', 'Resolve this candidate dependency closure before a fresh audit.'))
        ranked.append({'candidate_id': candidate['candidate_id'], 'label': name, 'entry_role': candidate.get('entry_role'), 'score': score, 'rank_basis': basis,
            'eligible_for_recommendation': complete and not conflict and (not intent or intent['status'] != 'blocked'),
            'sections': sections, 'model_contracts': contracts, 'tmi_relative_library': tmi,
            'corner_class': corner, 'dependency_file_count': len(candidate.get('dependency_paths', [])),
            'missing': missing, 'ready_to_run': False, 'fresh_audit_required': True})
        if intent is not None: ranked[-1]['intent'] = intent
    if len({row['candidate_id'] for row in ranked}) != len(ranked): _invalid()
    def policy_key(row):
        return (-int(row['entry_role'] == 'include_entry_candidate'), -int(row['sections']['model_contract_scope_expanded']),
                row['dependency_file_count'], row['label'].casefold(), row['candidate_id'])
    ranked.sort(key=lambda row: (-row['score'], policy_key(row)))
    for index, row in enumerate(ranked): row['rank'] = index + 1
    eligible = [row for row in ranked if row['eligible_for_recommendation']]
    tied = [row for row in eligible if row['score'] == eligible[0]['score']] if eligible else []
    selected = tied[0] if len(tied) == 1 else None
    confidence = 'inferred' if selected else 'unknown'; choice_basis = []; equivalent_unknown = False
    if len(tied) > 1:
        signatures = [json.dumps({name: field['value'] for name, field in row['model_contracts'].items()}, sort_keys=True) for row in tied]
        all_verified = all(row['model_contracts'] and all(field['auto_prefill_allowed'] for field in row['model_contracts'].values()) for row in tied)
        corners = {row['corner_class'] for row in tied}
        same_sections = len({tuple(row['sections']['value']) for row in tied}) == 1
        supplies = {row['intent']['fields']['supply_v']['value'] for row in tied} if with_intent else set()
        known_limits = {row['intent']['fields']['supply_v']['declared_upper_bound_v'] for row in tied
            if 'declared_upper_bound_v' in row['intent']['fields']['supply_v']} if with_intent else set()
        compatible_intent = (len(supplies) == 1 and None not in supplies and len(known_limits) <= 1
            and all(row['intent']['status'] == 'suggested' for row in tied)) if with_intent else True
        compatible = (all_verified and len(set(signatures)) == 1 and len(corners) == 1 and 'unspecified' not in corners
            and (corners == {'typical'} or same_sections)
            and compatible_intent)
        if compatible:
            selected = min(tied, key=policy_key); confidence = 'policy_default'; equivalent_unknown = True
            choice_basis.append(_basis('compatible_draft_policy_default', 'policy_default',
                ordering=['entry_role', 'observed_prelude', 'smaller_dependency_closure', 'basename', 'candidate_id'],
                model_equivalence_proven=False, foundry_qualification_proven=False,
                operating_conditions_assessed=with_intent))
    if selected and confidence == 'inferred': choice_basis = copy.deepcopy(selected['rank_basis'])
    missing = []
    if not ranked: missing.append(_issue('candidate_id', 'no_observed_candidates', 'Complete bounded model discovery or choose a narrower observed search root.'))
    elif not selected: missing.append(_issue('candidate_id', 'candidate_ambiguous' if tied else 'no_compatible_candidate',
        'Choose an entry explicitly; the observed candidates do not support one compatible default.'))
    if selected: missing.extend(selected['missing'])
    return {'schema': SCHEMA if with_intent else 'spiceowl.model-binding-recommendations.v1',
        'discovery_id': discovery['discovery_id'], 'source_sha256': requirements['source_sha256'],
        'status': 'draft' if selected and not missing else 'missing' if selected or not tied else 'ambiguous',
        'recommended_candidate_id': selected['candidate_id'] if selected else None,
        'selection_confidence': confidence, 'choice_basis': choice_basis,
        'alternatives': [row['candidate_id'] for row in tied if row is not selected],
        'equivalence_not_proven': equivalent_unknown, 'candidates': ranked, 'missing': missing,
        'requires_acceptance': True, 'ready_to_run': False, 'fresh_audit_required': True,
        'scientific_intent_discovered': False, 'foundry_qualification_proven': False}


def recommend_onboarding(discovery, requirements, remote_platform=None, *, preset='inverter_smoke'):
    """Backward-compatible inverter preset and source-backed model suggestions."""
    return _recommend(discovery, requirements, remote_platform, preset=preset)


def recommend_model_bindings(discovery, required_masters=None, remote_platform=None):
    """Pure generic binding suggestions, without topology or experiment presets.

    The exact model set must match the original bounded scan. Suggestions retain
    missing model/TMI evidence and require a fresh audit of the final selection.
    """
    if not isinstance(discovery, dict): _invalid()
    models = discovery.get('required_masters') if required_masters is None else required_masters
    if (not isinstance(models, list) or not 1 <= len(models) <= 128
            or any(not isinstance(name, str) or not NAME.fullmatch(name) for name in models)
            or len(models) != len(set(models))
            or set(models) != set(discovery.get('required_masters', []))): _invalid()
    requirements = {'schema': 'spiceowl.netlist-model-requirements.v1',
        'source_sha256': discovery.get('source_sha256'), 'required_masters': discovery['required_masters'],
        'instances': []}
    return _recommend(discovery, requirements, remote_platform, with_intent=False)

"""Compile an authored Spectre bundle without generating circuit or stimulus code."""
import copy
import json
import re

from spiceowl.domain.common import DomainError
from spiceowl.onboarding.agent_tb import (
    MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES, _binding, _bytes, _canonical, _json_data, _sha,
    _statements, validate_agent_tb,
)
from .source import ExperimentIssue, require, source_id


def _missing(rows):
    return {'status': 'missing', 'missing': rows, 'recipe': None, 'files': {},
            'source': None, 'entrypoint': None}


def validate_agent_spec(spec, profile):
    from .recipes import SPEC_SCHEMA, canonical, fields, validate_model_profile
    fields(spec, {'schema', 'recipe', 'corner', 'criteria', 'pdk_binding'}, 'spec')
    require(spec['schema'] == SPEC_SCHEMA and spec['recipe'] == 'agent-testbench',
            'spec.recipe', 'unsupported_recipe', 'Select the explicit agent-testbench recipe.')
    require(len(canonical(spec)) <= 2 * MAX_FILE_BYTES, 'spec', 'spec_limit', 'Authored specification exceeds its byte bound.')
    checked_profile = validate_model_profile(profile)
    require(spec['corner'] in checked_profile['corners'], 'spec.corner', 'corner_mapping_missing',
            'The selected PDK sections need an explicit registered corner alias mapping.')
    draft = spec['criteria']
    fields(draft, {'schema', 'text', 'interface', 'analyses', 'stimuli', 'objectives', 'criteria', 'postprocess'},
           'spec.criteria', {'helper_files'})
    require(draft['schema'] == 'ic.agent_testbench.v1', 'spec.criteria.schema', 'unsupported_schema',
            'Use the complete authored ic.agent_testbench.v1 draft.')
    fields(draft['interface'], {'subckt', 'instance', 'port_bindings'}, 'spec.criteria.interface')
    require(isinstance(draft['text'], str), 'spec.criteria.text', 'source_encoding', 'Draft text must be exact UTF-8 source text.')
    require(isinstance(draft['interface']['port_bindings'], dict), 'spec.criteria.interface.port_bindings',
            'missing_field', 'Supply the author-selected port bindings as explicit data.')
    _json_data(draft)
    _canonical(draft)
    _binding(spec['pdk_binding'])
    # Values are retained and hashed. Scientific objectives and postprocessing
    # programs are not interpreted or executed by this source compiler.
    return copy.deepcopy(spec), checked_profile


def prepare_agent_testbench(sources, selection, spec, model_profile):
    from .recipes import RECIPE_SCHEMA, canonical, fields
    try:
        checked, profile = validate_agent_spec(spec, model_profile)
        fields(selection, {'mode', 'source_id', 'testbench_id', 'pdk_id', 'support_files'},
               'selection', {'manifest_id'})
        require(selection['mode'] == 'agent-tb', 'selection.mode', 'unsupported_selection',
                'Use the explicit agent-tb source mapping.')
        require(type(sources) is dict and 3 <= len(sources) <= MAX_FILES, 'sources', 'source_limit',
                'Supply the committed DUT, testbench, managed PDK wrapper and selected supporting files.')
        for path, raw in sources.items():
            source_id(path)
            require(type(raw) is bytes and 0 < len(raw) <= MAX_FILE_BYTES, 'sources', 'source_limit',
                    'Every committed source must be nonempty bounded bytes.')
        require(sum(map(len, sources.values())) <= MAX_TOTAL_BYTES, 'sources', 'source_limit', 'Source bundle exceeds its byte bound.')
        mappings = selection['support_files']
        require(type(mappings) is dict and len(mappings) <= MAX_FILES - 3, 'selection.support_files',
                'invalid_source_mapping', 'Map each relative support reference to one committed source key.')
        selected_ids = [selection[key] for key in ('source_id', 'testbench_id', 'pdk_id')]
        selected_ids.extend(mappings.values())
        if 'manifest_id' in selection:
            selected_ids.append(selection['manifest_id'])
        require(all(type(key) is str and key in sources for key in selected_ids) and
                len(set(selected_ids)) == len(selected_ids) and set(selected_ids) == set(sources),
                'selection', 'invalid_source_mapping', 'Every supplied source must have one distinct declared role.')
        dut, tb, pdk = [sources[selection[key]] for key in ('source_id', 'testbench_id', 'pdk_id')]
        _bytes(pdk)
        require(tb == checked['criteria']['text'].encode('utf-8'), 'spec.criteria.text', 'testbench_digest_mismatch',
                'The committed testbench differs from the authored draft text.')
        support = {relative: sources[key] for relative, key in mappings.items()}
        validation = validate_agent_tb(source=dut, testbench=tb, support_files=support,
                                       criteria=checked['criteria'], pdk_binding=checked['pdk_binding'])
        if validation['status'] != 'static_ready':
            return _missing(validation['missing'])
        binding = validation['manifest']['pdk_binding']
        require(_sha(pdk) == binding['pdk_sha256'], 'spec.pdk_binding.pdk_sha256', 'managed_pdk_digest_mismatch',
                'The generated managed PDK wrapper differs from its selected binding.')
        includes = profile['corners'][checked['corner']]
        headers = binding.get('veriloga_header_closure', {}).get('files', binding.get('veriloga_standard_includes', []))
        require(not {row['alias'] for row in headers} &
                {row['alias'] for row in includes}, 'spec.pdk_binding.veriloga_standard_includes',
                'veriloga_standard_alias_conflict', 'Standard Verilog-A headers need distinct controlled aliases from PDK entries.')
        parsed_includes, problems = [], []
        for line, text in _statements(pdk, 'pdk.scs', problems):
            if re.fullmatch(r'simulator\s+lang\s*=\s*spectre', text, re.I):
                continue
            match = re.fullmatch(r'include\s+"@EXTERNAL_([A-Z][A-Z0-9_]{0,63})@"(?:\s+section=([A-Za-z_][A-Za-z0-9_.$:+-]*))?', text)
            require(match is not None, 'sources.pdk', 'unsupported_managed_pdk',
                    'Managed pdk.scs may contain only registered external-alias includes.')
            parsed_includes.append({'alias': match[1], 'section': match[2]})
        require(not problems and parsed_includes == includes and
                [item['section'] for item in includes] == binding['sections'],
                'sources.pdk', 'managed_pdk_selection_mismatch', 'Managed wrapper includes must exactly match the selected aliases and sections.')
        if 'manifest_id' in selection:
            raw = sources[selection['manifest_id']]
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    require(key not in result, 'sources.manifest', 'duplicate_manifest_key', 'Manifest keys cannot repeat.')
                    result[key] = value
                return result
            supplied = json.loads(raw.decode('utf-8'), object_pairs_hook=unique)
            require(canonical(supplied) == canonical(checked['criteria']), 'sources.manifest', 'authored_manifest_mismatch',
                    'The committed authored JSON manifest differs from the specification.')
        files = {'netlist/dut.scs': dut, 'netlist/tb.scs': tb, 'netlist/pdk.scs': pdk}
        files.update({'netlist/' + path: raw for path, raw in support.items()})
        if 'manifest_id' in selection:
            files['experiment/authored-manifest.json'] = sources[selection['manifest_id']]
        provenance = {'mode': 'agent-tb', 'entrypoint': 'netlist/tb.scs',
            'documents': [{'source_id': path, 'sha256': _sha(raw), 'bytes': len(raw)} for path, raw in sorted(sources.items())],
            'source_sha256': validation['manifest']['source_sha256'], 'tb_sha256': validation['manifest']['tb_sha256'],
            'criteria_sha256': validation['manifest']['criteria_sha256'], 'source_rewritten': False}
        recipe = {'schema': RECIPE_SCHEMA, 'recipe': 'agent-testbench', 'generator_version': 1,
            'spec': checked, 'selection': copy.deepcopy(selection), 'source': provenance,
            'model_profile': profile, 'model_profile_sha256': _sha(canonical(profile)),
            'used_models': validation['manifest']['required_masters'], 'external_includes': includes,
            'files_sha256': {path: _sha(raw) for path, raw in files.items()},
            'agent_tb_manifest': validation['manifest'], 'analyses': validation['analyses'], 'saves': validation['saves'],
            'veriloga_modules': validation['manifest']['veriloga_modules'],
            'veriloga_external_dependencies': validation['manifest']['veriloga_external_dependencies'],
            'veriloga_header_closure': validation['manifest']['veriloga_header_closure'],
            'veriloga_dependency_policy': 'Runner must audit the selected aliases and guarantee compiler use of the audited staged standard headers',
            'scope': 'agent_authored_testbench', 'qualification_scope': 'authored objectives require separate postprocessing and scientific validation',
            'scientific_intent_inferred': False, 'simulation_verified': False, 'human_review_required': True}
        recipe['recipe_sha256'] = _sha(canonical(recipe))
        files['recipe.json'] = canonical(recipe) + b'\n'
        return {'status': 'ready', 'missing': [], 'recipe': recipe, 'files': files, 'source': provenance,
                'entrypoint': 'netlist/tb.scs'}
    except ExperimentIssue as issue:
        return _missing([issue.record()])
    except DomainError as issue:
        return _missing([{'field': 'agent_testbench', 'code': issue.code, 'message': str(issue)}])
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError, RecursionError):
        return _missing([{'field': 'agent_testbench', 'code': 'unsupported_input',
                          'message': 'The authored bundle or selected binding is outside supported bounds.'}])

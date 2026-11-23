"""Recommendations are pure drafts over original synthetic scanner evidence."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from spiceowl.domain.common import DomainError
from spiceowl.onboarding.pdk_discovery import (DIRECTORY_SNAPSHOT_PREFIX,
    build_pdk_discovery_script, inspect_netlist)
from spiceowl.onboarding.recommendations import recommend_onboarding, suggest_intent


SOURCE = ('simulator lang=spectre\nA (OUT IN LO LO) device_alpha w=2u l=180n\n'
          'B (OUT IN HI HI) device_beta w=3u l=180n\n')
DEFINITIONS = ('simulator lang=spectre\nsection core\n'
    'model primitive_alpha bsim4 type=n\nmodel primitive_beta bsim4 type=p\n'
    'subckt device_alpha d g s b\nM0 (d g s b) primitive_alpha\nends device_alpha\n'
    'subckt device_beta d g s b\nM0 (d g s b) primitive_beta\nends device_beta\nendsection core\n')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def platform(distro='centos', like=('rhel',), system='Linux', machine='x86_64'):
    return {'system': system, 'machine': machine, 'os_release': {'id': distro, 'id_like': list(like)},
        'basis': {'system': 'remote_uname', 'machine': 'remote_uname', 'os_release': 'remote_os_release'},
        'os_release_status': 'observed'}


def literal(parameter, value, *, unit='V', model=None, owner=None):
    return {'parameter': parameter, 'literal': str(value), 'value': value, 'unit': unit,
        'path': '/synthetic/observed.scs', 'line': 7, 'section': 'TTMacro',
        'model_name': model, 'owner_line': owner, 'basis': 'explicit_source_literal',
        'scope': 'global', 'required_models': []}


class OnboardingRecommendationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.requirements = inspect_netlist(SOURCE, source_id='new-user-input.scs')

    def discovery(self, usage=None, *, extra=None):
        (self.root / 'definitions.scs').write_text(DEFINITIONS)
        files = usage or {'entry_usage.scs': 'section TTMacro_MOS_MOSCAP\ninclude "definitions.scs" section=core\nendsection TTMacro_MOS_MOSCAP\n'}
        for name, text in {**files, **(extra or {})}.items():
            (self.root / name).write_text('simulator lang=spectre\n' + text)
        script = build_pdk_discovery_script(required_masters=self.requirements['required_masters'],
            roots=[str(self.root)], source_sha256=self.requirements['source_sha256'])
        output = io.StringIO()
        with contextlib.redirect_stdout(output): exec(script, {})
        return json.loads('\n'.join(line for line in output.getvalue().splitlines() if not line.startswith(DIRECTORY_SNAPSHOT_PREFIX)))

    def candidate(self, discovery, name='entry_usage.scs', section='TTMacro_MOS_MOSCAP'):
        return next(c for c in discovery['entry_candidates'] if Path(c['entry_path']).name == name and c['section_candidates'] == [section])

    def recommend(self, discovery, remote=None):
        discovery['discovery_id'] = digest({k: v for k, v in discovery.items() if k != 'discovery_id'})
        before = copy.deepcopy(discovery)
        with (patch('builtins.open', side_effect=AssertionError('Recommendations cannot read files')),
              patch('os.uname', side_effect=AssertionError('Do not infer the client platform')),
              patch('subprocess.run', side_effect=AssertionError('No command')),
              patch('subprocess.Popen', side_effect=AssertionError('No SSH or simulator'))):
            result = recommend_onboarding(discovery, self.requirements, remote)
        self.assertEqual(discovery, before)
        self.assertFalse(result['ready_to_run'])
        self.assertTrue(result['requires_acceptance'])
        self.assertTrue(result['fresh_audit_required'])
        self.assertFalse(result['scientific_intent_discovered'])
        return result

    def device_limit(self, candidate, parameter, value):
        model = candidate['models'][0]
        source = next(e for e in model['evidence'] if e.get('kind') == 'primitive_model')
        row = literal(parameter, value, model=source['name'], owner=source['owner_line'])
        row.update(path=source['path'], scope='device_scoped', required_models=[model['name']])
        return row

    def test_usage_include_closure_and_typical_section_rank_over_definitions_and_monte_carlo(self):
        receipt = self.discovery({'entry_usage.scs':
            'section TTMacro_MOS_MOSCAP\ninclude "definitions.scs" section=core\nendsection TTMacro_MOS_MOSCAP\n'
            'section MC_RANDOM\ninclude "definitions.scs" section=core\nendsection MC_RANDOM\n'})
        result = self.recommend(receipt)
        self.assertEqual(result['recommended_candidate_id'], self.candidate(receipt)['candidate_id'])
        first = result['candidates'][0]
        self.assertEqual(first['sections']['value'], ['TTMacro_MOS_MOSCAP'])
        self.assertTrue(all(f['auto_prefill_allowed'] for f in first['model_contracts'].values()))
        convention = next(b for b in first['rank_basis'] if b['code'] == 'section_name_convention')
        self.assertEqual(convention['kind'], 'heuristic')
        self.assertFalse(convention['process_corner_qualification_proven'])

    def test_only_an_actually_observed_prelude_is_added_with_exact_spelling(self):
        receipt = self.discovery({'entry_usage.scs':
            'section Pre_Simu\nparameters tnom=30\nendsection Pre_Simu\n'
            'section TTMacro_MOS_MOSCAP\ninclude "definitions.scs" section=core\nendsection TTMacro_MOS_MOSCAP\n'})
        result = self.recommend(receipt)
        fields = result['candidates'][0]
        self.assertEqual(fields['sections']['value'], ['Pre_Simu', 'TTMacro_MOS_MOSCAP'])
        self.assertTrue(fields['sections']['fresh_audit_required'])
        self.assertTrue(fields['sections']['model_contract_scope_expanded'])
        self.assertTrue(all(not field['selected_section_scope_verified'] for field in fields['model_contracts'].values()))
        self.assertEqual(fields['intent']['fields']['nominal_temperature_c']['confidence'], 'preset', 'Unselected prelude literals must not be claimed as observed closure facts')
        candidate = self.candidate(receipt)
        candidate['available_sections'].remove('Pre_Simu')
        result = self.recommend(receipt)
        self.assertEqual(next(c for c in result['candidates'] if c['candidate_id'] == candidate['candidate_id'])['sections']['value'], ['TTMacro_MOS_MOSCAP'])

    def test_compatible_complete_tie_uses_transparent_policy_default_and_keeps_alternative(self):
        usage = 'section TTMacro_MOS_MOSCAP\ninclude "definitions.scs" section=core\nendsection TTMacro_MOS_MOSCAP\n'
        receipt = self.discovery({'alpha_usage.scs': usage, 'beta_usage.scs': usage})
        result = self.recommend(receipt)
        self.assertEqual(result['selection_confidence'], 'policy_default')
        self.assertEqual(result['recommended_candidate_id'], self.candidate(receipt, 'alpha_usage.scs')['candidate_id'])
        self.assertIn(self.candidate(receipt, 'beta_usage.scs')['candidate_id'], result['alternatives'])
        self.assertTrue(result['equivalence_not_proven'])
        self.assertFalse(result['choice_basis'][0]['foundry_qualification_proven'])

    def test_tied_different_nominal_voltage_domains_are_not_silently_chosen(self):
        usage = 'section TTMacro_MOS_MOSCAP\ninclude "definitions.scs" section=core\nendsection TTMacro_MOS_MOSCAP\n'
        receipt = self.discovery({'alpha_usage.scs': usage, 'beta_usage.scs': usage})
        self.candidate(receipt, 'alpha_usage.scs')['nominal_voltage_evidence'] = [literal('vdd_nom', .9)]
        self.candidate(receipt, 'beta_usage.scs')['nominal_voltage_evidence'] = [literal('vdd_nom', 1.8)]
        result = self.recommend(receipt)
        self.assertIsNone(result['recommended_candidate_id'])
        self.assertEqual(result['status'], 'ambiguous')

    def test_unknown_or_conflicting_model_contracts_cannot_receive_a_tie_default(self):
        usage = 'section TTMacro_MOS_MOSCAP\ninclude "definitions.scs" section=core\nendsection TTMacro_MOS_MOSCAP\n'
        receipt = self.discovery({'alpha_usage.scs': usage, 'beta_usage.scs': usage})
        for name in ('alpha_usage.scs', 'beta_usage.scs'):
            for row in self.candidate(receipt, name)['models']:
                row.update(auto_prefill_allowed=False, confidence='unknown', suggested_contract=None)
        result = self.recommend(receipt)
        self.assertIsNone(result['recommended_candidate_id'])
        self.assertNotEqual(result['selection_confidence'], 'policy_default')

    def test_distinct_extreme_corners_cannot_be_collapsed_into_one_policy_default(self):
        receipt = self.discovery({'entry_usage.scs':
            'section SS\ninclude "definitions.scs" section=core\nendsection SS\n'
            'section FF\ninclude "definitions.scs" section=core\nendsection FF\n'})
        result = self.recommend(receipt)
        self.assertIsNone(result['recommended_candidate_id'])
        self.assertEqual(result['status'], 'ambiguous')

    def test_tmi_uses_only_remote_linux_architecture_and_observed_distro_family(self):
        receipt = self.discovery(); candidate = self.candidate(receipt)
        candidate['tmi_candidates'] = [{'platform_candidates': [
            {'relative_library': 'RH_64/libTMImodel.so'}, {'relative_library': 'SUSE_64/libTMImodel.so'}]}]
        for facts, expected in [(platform(), 'RH_64/libTMImodel.so'),
            (platform('rocky'), 'RH_64/libTMImodel.so'), (platform('sles', ('suse',)), 'SUSE_64/libTMImodel.so'),
            (platform(machine='aarch64'), None), (platform(system='Darwin'), None),
            (platform('ubuntu', ('debian',)), None), (None, None)]:
            with self.subTest(facts=facts):
                result = self.recommend(receipt, facts)
                field = next(c for c in result['candidates'] if c['candidate_id'] == candidate['candidate_id'])['tmi_relative_library']
                self.assertEqual(field['value'], expected)
                self.assertTrue(field['runtime_validation_required'])
                self.assertFalse(field['basis'][0]['runtime_compatibility_verified'])

    def test_multiple_observed_libraries_for_one_remote_platform_remain_ambiguous(self):
        receipt = self.discovery(); candidate = self.candidate(receipt)
        candidate['tmi_candidates'] = [{'platform_candidates': [
            {'relative_library': 'version_a/RH_64/libTMImodel.so'}, {'relative_library': 'version_b/RH_64/libTMImodel.so'}]}]
        result = self.recommend(receipt, platform())
        field = result['candidates'][0]['tmi_relative_library']
        self.assertIsNone(field['value'])
        self.assertEqual(field['missing'][0]['code'], 'tmi_platform_ambiguous')

    def test_intent_without_model_values_is_an_explicit_preset_with_connectivity_suggestions(self):
        with patch('builtins.open', side_effect=AssertionError('No file reads')):
            result = suggest_intent(self.requirements)
        self.assertEqual(result['status'], 'suggested')
        expected = {'supply_v': .9, 'frequency_hz': 1e9, 'temperature_c': 27, 'nominal_temperature_c': 27,
            'cycles': 5, 'load_f': 2e-15, 'rise_s': 1e-11, 'fall_s': 1e-11, 'points_per_period': 1000}
        for name, value in expected.items():
            self.assertEqual(result['fields'][name]['value'], value)
            self.assertEqual(result['fields'][name]['confidence'], 'preset')
        self.assertEqual({k: result['fields'][k]['value'] for k in ('input_port', 'output_port', 'vdd_port', 'vss_port')},
            {'input_port': 'IN', 'output_port': 'OUT', 'vdd_port': 'HI', 'vss_port': 'LO'})
        self.assertFalse(result['scientific_intent_discovered'])

    def test_clock_complement_names_preserve_case_and_ambiguous_names_require_selection(self):
        source = SOURCE.replace('OUT', 'CLKN').replace('IN', 'CLK').replace('HI', 'VDD').replace('LO', 'VSS')
        result = suggest_intent(inspect_netlist(source))
        self.assertEqual(result['fields']['input_port']['value'], 'CLK')
        self.assertEqual(result['fields']['output_port']['value'], 'CLKN')
        source = SOURCE.replace('A (OUT IN', 'A (OUT CLK')
        result = suggest_intent(inspect_netlist(source))
        self.assertEqual(result['status'], 'missing')
        self.assertIsNone(result['fields']['input_port']['value'])

    def test_source_backed_nominal_values_override_presets_without_claiming_rating(self):
        receipt = self.discovery(); candidate = self.candidate(receipt)
        candidate['nominal_voltage_evidence'] = [literal('vdd_nom', 1.2)]
        candidate['nominal_temperature_evidence'] = [literal('tnom', 25, unit='C')]
        result = suggest_intent(self.requirements, candidate)
        self.assertEqual(result['fields']['supply_v']['value'], 1.2)
        self.assertEqual(result['fields']['supply_v']['confidence'], 'inferred')
        self.assertTrue(any(b['code'] == 'global_nominal_suggestion' for b in result['fields']['supply_v']['basis']))
        self.assertEqual(result['fields']['nominal_temperature_c']['value'], 25)
        self.assertFalse(result['operating_qualification_proven'])

    def test_known_incompatible_supply_limits_block_without_rewriting_the_requested_preset(self):
        receipt = self.discovery(); candidate = self.candidate(receipt)
        candidate['voltage_limit_evidence'] = [self.device_limit(candidate, 'vgs_max', .8)]
        result = suggest_intent(self.requirements, candidate)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['fields']['supply_v']['value'], .9)
        self.assertTrue(result['fields']['supply_v']['requires_override'])
        self.assertEqual(result['fields']['supply_v']['missing'][0]['code'], 'supply_exceeds_declared_limit')

    def test_unrelated_model_limits_do_not_become_global_rating_and_omitted_limits_stay_missing(self):
        receipt = self.discovery(); candidate = self.candidate(receipt)
        unrelated = literal('vgs_max', .3, model='unrelated_device')
        unrelated.update(scope='device_scoped', required_models=['unrelated_device'])
        candidate['voltage_limit_evidence'] = [unrelated]
        self.assertEqual(suggest_intent(self.requirements, candidate)['status'], 'suggested')
        candidate['literal_evidence_truncated'] = True
        result = suggest_intent(self.requirements, candidate)
        self.assertEqual(result['status'], 'missing')
        self.assertIn('voltage_evidence_incomplete', {m['code'] for m in result['missing']})

    def test_model_specific_limits_require_matching_primitive_owner_source_evidence(self):
        receipt = self.discovery(); candidate = self.candidate(receipt)
        row = self.device_limit(candidate, 'vds_max', .7)
        candidate['voltage_limit_evidence'] = [row]
        self.assertEqual(suggest_intent(self.requirements, candidate)['status'], 'blocked')
        row['owner_line'] = 99999
        self.assertEqual(suggest_intent(self.requirements, candidate)['status'], 'suggested')

    def test_unscoped_limits_cannot_be_interpreted_as_all_required_device_ratings(self):
        receipt = self.discovery(); candidate = self.candidate(receipt)
        candidate['voltage_limit_evidence'] = [literal('vgs_max', .3)]
        result = suggest_intent(self.requirements, candidate)
        self.assertEqual(result['status'], 'suggested')
        self.assertNotIn('declared_upper_bound_v', result['fields']['supply_v'])
        self.assertEqual(result['fields']['supply_v']['value'], .9)
        candidate['voltage_limit_evidence'][0]['scope'] = None
        self.assertEqual(suggest_intent(self.requirements, candidate)['status'], 'missing')

    def test_repeated_equal_literal_location_samples_are_not_missing_distinct_values(self):
        receipt = self.discovery(); candidate = self.candidate(receipt)
        evidence = literal('tnom', 27, unit='C')
        evidence.update(declaration_count=200, declaration_lines=list(range(1, 129)), declaration_locations_truncated=True)
        candidate['nominal_temperature_evidence'] = [evidence]
        candidate['literal_evidence_truncated'] = False
        result = suggest_intent(self.requirements, candidate)
        self.assertEqual(result['status'], 'suggested')
        self.assertEqual(result['fields']['nominal_temperature_c']['value'], 27)
        self.assertEqual(result['fields']['nominal_temperature_c']['basis'][0]['declaration_count'], 200)

    def test_truncated_incoming_samples_do_not_turn_an_included_file_into_a_root(self):
        receipt = self.discovery(); candidate = self.candidate(receipt)
        candidate['source_role_evidence'].update(incoming_includes=[], incoming_include_count=15, incoming_includes_truncated=True)
        result = self.recommend(receipt)
        entry = next(c for c in result['candidates'] if c['candidate_id'] == candidate['candidate_id'])
        self.assertNotIn('no_observed_incoming_include', {b['code'] for b in entry['rank_basis']})

    def test_invalid_discovery_digest_and_different_source_requirements_fail_closed(self):
        receipt = self.discovery(); receipt['source_sha256'] = '0'*64
        with self.assertRaises(DomainError): recommend_onboarding(receipt, self.requirements)
        receipt['discovery_id'] = digest({k: v for k, v in receipt.items() if k != 'discovery_id'})
        with self.assertRaises(DomainError): recommend_onboarding(receipt, self.requirements)
        with self.assertRaises(DomainError): suggest_intent(self.requirements, preset='unrecognized')


if __name__ == '__main__':
    unittest.main()

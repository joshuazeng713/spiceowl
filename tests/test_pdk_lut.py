"""Private cache tests use newly authored local synthetic model files only."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from spiceowl.domain.common import DomainError
from spiceowl.onboarding.pdk_discovery import (DIRECTORY_SNAPSHOT_PREFIX,
    build_pdk_discovery_script, inspect_netlist, profile_suggestions)
from spiceowl.onboarding.pdk_lut import PdkLut, content_fingerprint, rebind_discovery, _digest
from spiceowl.onboarding.recommendations import recommend_model_bindings, recommend_onboarding


SOURCE = 'A (Y CLK LO LO) n_core_ulvt w=2u l=100n\nB (Y CLK HI HI) p_core_ulvt w=3u l=100n\n'
MODELS = ('section models\nmodel nprim bsim4 type=n\nmodel pprim bsim4 type=p\n'
          'subckt n_core_ulvt d g s b\nM0 (d g s b) nprim\nends n_core_ulvt\n'
          'subckt p_core_ulvt d g s b\nM0 (d g s b) pprim\nends p_core_ulvt\nendsection models\n')
IDENTITY = {'hostname': 'synthetic-compute', 'username': 'synthetic-user', 'uid': 1000}


def scan(root, requirements):
    script = build_pdk_discovery_script(required_masters=requirements['required_masters'],
        source_sha256=requirements['source_sha256'], roots=[str(root)])
    output = io.StringIO()
    with contextlib.redirect_stdout(output): exec(script, {})
    return json.loads('\n'.join(line for line in output.getvalue().splitlines()
                               if not line.startswith(DIRECTORY_SNAPSHOT_PREFIX)))


class PdkLutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.models = self.root / 'models'; self.models.mkdir()
        (self.models / 'devices.scs').write_text('simulator lang=spectre\n' + MODELS)
        (self.models / 'usage.scs').write_text('section pre_simu\nendsection pre_simu\n'
            'section TTMacro\ninclude "devices.scs" section=models\nendsection TTMacro\n')
        self.requirements = inspect_netlist(SOURCE)
        self.discovery = scan(self.models, self.requirements)
        self.path = self.root / 'state' / 'pdk.sqlite3'
        self.lut = PdkLut(self.path); self.addCleanup(self.lut.close)
        self.record = self.lut.put(self.discovery, server_identity=IDENTITY, observed_at=1000)
        self.args = {'server_identity': IDENTITY, 'roots': [str(self.models)],
                     'required_masters': self.requirements['required_masters'], 'now': 1001}

    def test_persistent_lookup_keeps_entire_original_receipt_and_source_binding(self):
        with PdkLut(self.path) as reopened:
            result = reopened.lookup(**self.args)
        self.assertEqual(result['status'], 'hit')
        self.assertFalse(result['scan_required'])
        self.assertEqual(result['record']['discovery'], self.discovery)
        self.assertEqual(result['record']['original_source_sha256'], self.requirements['source_sha256'])
        self.assertEqual(result['record']['coverage']['covered_model_names'], sorted(self.requirements['required_masters']))
        self.assertFalse(result['record']['coverage']['whole_pdk_inventory'])
        self.assertTrue(result['fresh_audit_required'])
        self.assertFalse(result['remote_freshness_verified'])
        if os.name != 'nt':
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_read_only_lookup_does_not_create_or_change_the_database(self):
        before = self.path.read_bytes(); modified = self.path.stat().st_mtime_ns
        with PdkLut(self.path, read_only=True) as reader:
            self.assertEqual(reader.lookup(**self.args)['status'], 'hit')
            self.assertTrue(reader.query_bindings(**self.args)['mappings'])
            with self.assertRaises(DomainError):
                reader.put(self.discovery, server_identity=IDENTITY)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.path.stat().st_mtime_ns, modified)
        absent = self.root / 'missing' / 'absent.sqlite3'
        with self.assertRaises(DomainError): PdkLut(absent, read_only=True)
        self.assertFalse(absent.parent.exists())

    def test_sqlite_capacity_failure_keeps_the_previous_complete_observation(self):
        changed = copy.deepcopy(self.discovery); changed['scope_note'] = 'x' * 100000
        changed['discovery_id'] = _digest({k: v for k, v in changed.items() if k != 'discovery_id'})
        with patch('spiceowl.onboarding.pdk_lut.MAX_DATABASE_BYTES', self.path.stat().st_size + 4096):
            with PdkLut(self.path) as bounded:
                with self.assertRaises(DomainError) as error:
                    bounded.put(changed, server_identity=IDENTITY, observed_at=1001)
                self.assertEqual(error.exception.code, 'pdk_lut_storage')
                self.assertEqual(bounded.lookup(**self.args)['record']['discovery'], self.discovery)

    def test_exact_identity_roots_scope_and_optional_content_fingerprint_are_keys(self):
        changes = [{'server_identity': {**IDENTITY, 'hostname': 'other'}},
                   {'server_identity': {**IDENTITY, 'uid': 2000}},
                   {'roots': [str(self.root)]}, {'required_masters': ['n_core_ulvt']},
                   {'content_fingerprint': 'f' * 64}]
        for change in changes:
            with self.subTest(change=change):
                self.assertEqual(self.lut.lookup(**{**self.args, **change})['status'], 'miss')
        reordered = {**self.args, 'required_masters': list(reversed(self.args['required_masters']))}
        self.assertEqual(self.lut.lookup(**reordered)['status'], 'hit')
        self.assertEqual(self.lut.lookup(**self.args, content_fingerprint=self.record['content_fingerprint'])['status'], 'hit')

    def test_hits_queries_and_refresh_never_open_models_or_launch_a_probe(self):
        with (patch('builtins.open', side_effect=AssertionError('Do not open a model')),
             patch('os.uname', side_effect=AssertionError('Do not infer server identity')),
             patch('subprocess.run', side_effect=AssertionError('No SSH or solver'))):
            self.assertEqual(self.lut.lookup(**self.args)['status'], 'hit')
            self.assertTrue(self.lut.query_bindings(**self.args)['mappings'])
            refresh = self.lut.lookup(**self.args, refresh=True)
        self.assertEqual(refresh['status'], 'refresh_required')
        self.assertIsNone(refresh['record'])

    def test_expiration_and_clock_skew_keep_observation_time_and_do_not_return_bindings(self):
        for now, status in [(2000, 'expired'), (999, 'clock_skew')]:
            with self.subTest(now=now):
                result = self.lut.query_bindings(**{**self.args, 'now': now}, max_age_s=100)
                self.assertEqual(result['status'], 'stale')
                self.assertEqual(result['mappings'], [])
                self.assertEqual(result['record']['observed_at'], 1000)
                self.assertEqual(result['record']['staleness']['state'], status)
        self.assertEqual(self.lut.lookup(**{**self.args, 'now': 1100}, max_age_s=100)['status'], 'hit')

    def test_content_change_creates_an_independent_observation_and_retains_prior_json(self):
        (self.models / 'devices.scs').write_text('simulator lang=spectre\nparameters local_example=2\n' + MODELS)
        changed = scan(self.models, self.requirements)
        new = self.lut.put(changed, server_identity=IDENTITY, observed_at=1001)
        self.assertNotEqual(new['content_fingerprint'], self.record['content_fingerprint'])
        self.assertEqual(self.lut.lookup(**self.args)['record']['discovery'], changed)
        previous = self.lut.lookup(**self.args, content_fingerprint=self.record['content_fingerprint'])
        self.assertEqual(previous['record']['discovery'], self.discovery)

    def test_tmi_binary_observations_participate_in_fingerprint_and_mapping(self):
        library = self.models / 'tmi' / 'RH_64' / 'libTMImodel.so'
        library.parent.mkdir(parents=True); library.write_bytes(b'\x00synthetic-first')
        entry = self.models / 'devices.scs'
        entry.write_text('simulator lang=spectre\n.option tmipath="tmi"\n' + MODELS)
        first = scan(self.models, self.requirements)
        record = self.lut.put(first, server_identity=IDENTITY, observed_at=1001)
        query = self.lut.query_bindings(**self.args)
        groups = [group for row in query['mappings'] for group in row['tmi_candidates']]
        self.assertTrue(groups)
        self.assertTrue(all(option['sha256'] for group in groups for option in group['platform_candidates']))
        library.write_bytes(b'\x00synthetic-second')
        second = scan(self.models, self.requirements)
        self.assertNotEqual(content_fingerprint(second), record['content_fingerprint'])

    def test_index_preserves_model_contract_source_evidence_sections_and_file_hashes(self):
        result = self.lut.query_bindings(**self.args, model_names=['n_core_ulvt'])
        self.assertNotIn('discovery', result['record'])
        self.assertTrue(result['mappings'])
        candidates = {row['candidate_id']: row for row in self.discovery['entry_candidates']}
        for mapping in result['mappings']:
            candidate = candidates[mapping['candidate_id']]
            model = next(row for row in candidate['models'] if row['name'] == 'n_core_ulvt')
            self.assertEqual(mapping['model'], model)
            self.assertEqual(mapping['section_candidates'], candidate['section_candidates'])
            self.assertEqual(mapping['entry_sha256'], candidate['entry_sha256'])
            self.assertEqual(mapping['tmi_candidates'], candidate['tmi_candidates'])
            self.assertTrue(all(mapping['dependency_hashes'].values()))
        with self.assertRaises(DomainError):
            self.lut.query_bindings(**self.args, model_names=['not_scanned'])

    def test_best_query_returns_precomputed_rank_with_complete_binding_and_source_evidence(self):
        expected = recommend_model_bindings(self.discovery)
        with PdkLut(self.path, read_only=True) as cache:
            with patch('spiceowl.onboarding.recommendations.recommend_model_bindings',
                       side_effect=AssertionError('A cache hit must not rank every candidate again')):
                result = cache.query_best_bindings(**self.args, limit=1)
        recommendation = result['recommendation']
        self.assertEqual(recommendation['recommended_candidate_id'], expected['recommended_candidate_id'])
        self.assertEqual(recommendation['choice_basis'], expected['choice_basis'])
        self.assertEqual(recommendation['alternatives'], expected['alternatives'])
        self.assertTrue(recommendation['complete_binding_available'])
        self.assertTrue(recommendation['candidates_truncated'])
        selected = next(row for row in recommendation['candidates'] if row['candidate_id'] == recommendation['recommended_candidate_id'])
        self.assertEqual(selected['label'], 'usage.scs')
        self.assertEqual(selected['sections']['value'], ['pre_simu', 'TTMacro'])
        self.assertEqual(set(selected['model_contracts']), set(self.requirements['required_masters']))
        original = next(row for row in self.discovery['entry_candidates'] if row['candidate_id'] == selected['candidate_id'])
        self.assertEqual(selected['binding']['source_models'], original['models'])
        self.assertEqual(selected['binding']['entry_sha256'], original['entry_sha256'])
        self.assertTrue(all(selected['binding']['dependency_hashes'].values()))

    def test_best_query_does_not_default_an_ambiguous_corner_by_candidate_id(self):
        (self.models / 'usage.scs').write_text(''.join('section '+name+'\n'
            'include "devices.scs" section=models\nendsection '+name+'\n' for name in ('SS', 'FF')))
        discovery = scan(self.models, self.requirements)
        self.lut.put(discovery, server_identity=IDENTITY, observed_at=1001)
        result = self.lut.query_best_bindings(**self.args, limit=1)['recommendation']
        self.assertIsNone(result['recommended_candidate_id'])
        self.assertEqual(result['status'], 'ambiguous')
        self.assertFalse(result['complete_binding_available'])
        self.assertIn('candidate_ambiguous', {row['code'] for row in result['missing']})

    def test_best_query_resolves_current_remote_tmi_without_mutating_indexed_choice(self):
        library = self.models / 'tmi' / 'RH_64' / 'libTMImodel.so'
        library.parent.mkdir(parents=True); library.write_bytes(b'\x00synthetic-platform')
        (self.models / 'devices.scs').write_text('simulator lang=spectre\n.option tmipath="tmi"\n' + MODELS)
        discovery = scan(self.models, self.requirements)
        self.lut.put(discovery, server_identity=IDENTITY, observed_at=1001)
        before = self.path.read_bytes()
        unknown = self.lut.query_best_bindings(**self.args)['recommendation']
        self.assertFalse(unknown['complete_binding_available'])
        remote = {'system': 'Linux', 'machine': 'x86_64',
                  'os_release': {'id': 'centos', 'id_like': ['rhel']},
                  'basis': {'system': 'remote_uname', 'machine': 'remote_uname', 'os_release': 'remote_os_release'},
                  'os_release_status': 'observed'}
        result = self.lut.query_best_bindings(**self.args, remote_platform=remote)['recommendation']
        self.assertTrue(result['complete_binding_available'])
        selected = next(row for row in result['candidates'] if row['candidate_id'] == result['recommended_candidate_id'])
        self.assertEqual(selected['tmi_relative_library']['value'], 'RH_64/libTMImodel.so')
        self.assertTrue(selected['tmi_relative_library']['runtime_validation_required'])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.lut.query_best_bindings(**self.args)['recommendation'], unknown)

    def test_best_query_stale_and_old_schema_are_not_current_best_bindings(self):
        self.assertIsNone(self.lut.query_best_bindings(**{**self.args, 'now': 999})['recommendation'])
        self.lut.connection.execute('PRAGMA user_version=1')
        with PdkLut(self.path, read_only=True) as reader:
            self.assertEqual(reader.lookup(**self.args)['status'], 'hit')
            result = reader.query_best_bindings(**self.args)
        self.assertIsNone(result['recommendation'])
        self.assertEqual(result['missing'][0]['code'], 'binding_index_unavailable')

    def test_index_paging_does_not_claim_omitted_rows_are_absent(self):
        all_rows = self.lut.query_bindings(**self.args)
        pages, offset = [], 0
        while True:
            page = self.lut.query_bindings(**self.args, limit=1, offset=offset)
            self.assertEqual(page['total'], all_rows['total']); pages.extend(page['mappings'])
            if page['next_offset'] is None: break
            self.assertTrue(page['truncated']); offset = page['next_offset']
        self.assertEqual(pages, all_rows['mappings'])

    def test_query_byte_limit_reports_an_oversized_binding_without_skipping_it(self):
        with patch('spiceowl.onboarding.pdk_lut.MAX_QUERY_BYTES', 2000):
            result = self.lut.query_bindings(**self.args)
        self.assertEqual(result['mappings'], [])
        self.assertTrue(result['truncated'])
        self.assertIsNone(result['next_offset'])
        self.assertEqual(result['missing'][0]['code'], 'binding_exceeds_query_budget')

    def test_rebind_is_a_new_receipt_with_preserved_observation_scope_and_candidate_references(self):
        record = self.lut.lookup(**self.args)['record']; original = copy.deepcopy(record)
        requirements = inspect_netlist(SOURCE.replace('w=2u', 'w=4u'))
        requirements['mode'] = 'agent-tb'
        with patch('subprocess.run', side_effect=AssertionError('No SSH')):
            rebound = rebind_discovery(record, requirements)
        self.assertEqual(record, original)
        self.assertEqual(rebound['source_sha256'], requirements['source_sha256'])
        self.assertNotEqual(rebound['discovery_id'], self.discovery['discovery_id'])
        self.assertEqual(rebound['cache_provenance']['original_discovery_id'], self.discovery['discovery_id'])
        self.assertEqual(rebound['cache_provenance']['observed_at'], 1000)
        ids = {row['candidate_id'] for row in rebound['entry_candidates']}
        self.assertFalse(ids & {row['candidate_id'] for row in self.discovery['entry_candidates']})
        for ambiguity in rebound['ambiguities']:
            self.assertTrue(set(ambiguity.get('candidate_ids', [])) <= ids)
        for old, new in zip(self.discovery['entry_candidates'], rebound['entry_candidates']):
            self.assertEqual({k: v for k, v in old.items() if k != 'candidate_id'},
                             {k: v for k, v in new.items() if k != 'candidate_id'})
        self.assertTrue(profile_suggestions(rebound)['candidates'])
        self.assertTrue(recommend_model_bindings(rebound)['candidates'])

    def test_rebind_rejects_scope_changes_and_tampered_original_metadata(self):
        record = self.lut.lookup(**self.args)['record']
        for change in ({'cache_id': '0' * 64}, {'original_source_sha256': '1' * 64},
                       {'content_fingerprint': '2' * 64}):
            with self.subTest(change=change), self.assertRaises(DomainError):
                rebind_discovery({**record, **change}, self.requirements)
        with self.assertRaises(DomainError):
            rebind_discovery(record, {**self.requirements, 'required_masters': ['n_core_ulvt']})

    def test_incomplete_filtered_observation_is_preserved_without_whole_library_claim(self):
        requirement = inspect_netlist(SOURCE + 'C (D G S B) not_present w=2u l=100n\n')
        partial = scan(self.models, requirement)
        self.lut.put(partial, server_identity=IDENTITY, observed_at=1000)
        result = self.lut.lookup(**{**self.args, 'required_masters': requirement['required_masters']})
        self.assertEqual(result['record']['discovery'], partial)
        self.assertNotIn('not_present', result['record']['coverage']['covered_model_names'])
        self.assertFalse(result['record']['coverage']['whole_pdk_inventory'])
        with self.assertRaises(DomainError): rebind_discovery(result['record'], requirement)

    def test_bad_scope_or_nonfinite_timestamps_do_not_enter_sqlite(self):
        for change in ({'server_identity': {'hostname': 'alias'}}, {'now': float('nan')},
                       {'required_masters': ['x', 'x']}, {'roots': ['/synthetic/../other']}):
            with self.subTest(change=change), self.assertRaises(DomainError):
                self.lut.lookup(**{**self.args, **change})


class BindingRecommendationTests(unittest.TestCase):
    setUp = PdkLutTests.setUp

    def test_generic_binding_requires_no_inverter_port_names_or_stimulus(self):
        result = recommend_model_bindings(self.discovery)
        self.assertEqual(result['schema'], 'spiceowl.model-binding-recommendations.v1')
        self.assertIsNotNone(result['recommended_candidate_id'])
        self.assertEqual(result['missing'], [])
        self.assertTrue(all('intent' not in row for row in result['candidates']))
        inverter = recommend_onboarding(self.discovery, self.requirements)
        self.assertTrue(inverter['missing'], 'Y is deliberately not an inverter output name hint')

    def test_observed_required_wrapper_prelude_typical_usage_beats_unrelated_io_entry(self):
        (self.models / 'io_usage.scs').write_text('section TTMacro\n'
            'include "devices.scs" section=models\nendsection TTMacro\n')
        discovery = scan(self.models, self.requirements)
        result = recommend_model_bindings(discovery)
        selected = next(row for row in result['candidates'] if row['candidate_id'] == result['recommended_candidate_id'])
        self.assertEqual(selected['label'], 'usage.scs')
        self.assertEqual(selected['sections']['value'], ['pre_simu', 'TTMacro'])
        codes = {row['code'] for row in selected['rank_basis']}
        self.assertIn('source_backed_required_wrapper_usage', codes)
        self.assertIn('observed_prelude_typical_usage', codes)
        self.assertTrue(selected['fresh_audit_required'])
        self.assertTrue(all(not row['selected_section_scope_verified'] for row in selected['model_contracts'].values()))

    def test_voltage_looking_filename_does_not_set_experiment_supply_or_qualification(self):
        (self.models / 'usage.scs').rename(self.models / '1d8_usage.scs')
        discovery = scan(self.models, self.requirements)
        result = recommend_onboarding(discovery, self.requirements)
        selected = next(row for row in result['candidates'] if row['candidate_id'] == result['recommended_candidate_id'])
        self.assertEqual(selected['intent']['fields']['supply_v']['value'], .9)
        self.assertEqual(selected['intent']['fields']['supply_v']['confidence'], 'preset')
        self.assertFalse(result['foundry_qualification_proven'])

    def test_unknown_contracts_and_tmi_platform_stay_missing_in_generic_binding(self):
        unknown = copy.deepcopy(self.discovery)
        for candidate in unknown['entry_candidates']:
            for model in candidate['models']:
                model.update(confidence='unknown', suggested_contract=None, auto_prefill_allowed=False,
                             reason='model_contract_unresolved')
            candidate['tmi_candidates'] = [{'platform_candidates': [
                {'relative_library': 'RH_64/libTMImodel.so'}]}]
        unknown['discovery_id'] = _digest({k: v for k, v in unknown.items() if k != 'discovery_id'})
        result = recommend_model_bindings(unknown)
        codes = {gap['code'] for candidate in result['candidates'] for gap in candidate['missing']}
        self.assertIn('model_contract_confirmation', codes)
        self.assertIn('tmi_platform_unknown', codes)
        self.assertTrue(all(not candidate['ready_to_run'] for candidate in result['candidates']))
        with self.assertRaises(DomainError): recommend_model_bindings(unknown, [['invalid']])


if __name__ == '__main__': unittest.main()

"""One synthetic library observation serves independent circuit model sets."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from spiceowl.domain.common import DomainError
from spiceowl.onboarding.pdk_discovery import (build_pdk_library_script,
    validate_pdk_library, pdk_library_page, profile_suggestions, audit_config_for_candidate)
from spiceowl.onboarding.pdk_lut import PdkLut
from tests.test_pdk_lut import IDENTITY, MODELS, SOURCE, scan
from spiceowl.onboarding.pdk_discovery import inspect_netlist


def library_scan(root, **kwargs):
    output = io.StringIO()
    with contextlib.redirect_stdout(output): exec(build_pdk_library_script(roots=[str(root)], **kwargs), {})
    return validate_pdk_library(json.loads(output.getvalue()))


def requirements(names, source='a'):
    return {'schema': 'spiceowl.netlist-model-requirements.v1', 'mode': 'agent-tb',
            'source_sha256': source * 64, 'required_masters': names, 'instances': []}


class PdkLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(); self.models = self.root / 'models'; self.models.mkdir()
        (self.models / 'a_core.scs').write_text('simulator lang=spectre\n' + MODELS)
        (self.models / 'b_io.scs').write_text('section io\nmodel io_primitive bsim4 type=n\n'
            'subckt io_device z a low body\nM0 (z a low body) io_primitive\nends io_device\nendsection io\n')
        (self.models / 'usage.scs').write_text('section pre_simu\nendsection pre_simu\nsection TTMacro\n'
            'include "a_core.scs" section=models\ninclude "b_io.scs" section=io\nendsection TTMacro\n')
        self.library = library_scan(self.models)
        self.path = self.root / 'cache.sqlite3'; self.lut = PdkLut(self.path); self.addCleanup(self.lut.close)
        self.metadata = self.lut.put_library(self.library, server_identity=IDENTITY, observed_at=100)
        self.scope = {'server_identity': IDENTITY, 'roots': [str(self.models)], 'now': 101}

    def query(self, names, **kwargs):
        return self.lut.lookup_library_models(**self.scope, required_masters=names,
            requirements=requirements(names), **kwargs)

    def test_one_full_scan_serves_different_required_combinations_without_io(self):
        original = copy.deepcopy(self.library)
        with (patch('builtins.open', side_effect=AssertionError('No PDK file read')),
              patch('subprocess.run', side_effect=AssertionError('No SSH or solver'))):
            for names in (['n_core_ulvt'], ['p_core_ulvt'], ['io_device'], ['n_core_ulvt', 'io_device'],
                          ['n_core_ulvt', 'p_core_ulvt', 'io_device']):
                with self.subTest(names=names):
                    result = self.query(names)
                    self.assertEqual(result['status'], 'hit')
                    self.assertEqual(result['record']['library_id'], self.library['library_id'])
                    self.assertFalse(result['scan_required'])
                    self.assertEqual(result['discovery']['required_masters'], names)
                    self.assertTrue(profile_suggestions(result['discovery'])['candidates'])
                    chosen = next(row for row in result['recommendation']['candidates']
                        if row['candidate_id'] == result['recommendation']['recommended_candidate_id'])
                    audit = audit_config_for_candidate(result['discovery'], chosen['candidate_id'], sections=chosen['sections']['value'])
                    self.assertEqual(audit['required_masters'], names)
        self.assertEqual(self.library, original)

    def test_original_full_observation_and_actual_provider_contracts_are_preserved(self):
        stored = self.lut.connection.execute('SELECT library FROM library_observations WHERE cache_id=?',
                                           (self.metadata['cache_id'],)).fetchone()[0]
        self.assertEqual(json.loads(stored), self.library)
        result = self.query(['n_core_ulvt', 'io_device'])
        raw = {row['candidate_id']: row for row in self.library['entry_candidates']}
        for candidate in result['discovery']['entry_candidates']:
            observed = raw[candidate['library_candidate_id']]
            self.assertEqual(candidate['entry_sha256'], observed['entry_sha256'])
            for model in candidate['models']:
                self.assertEqual(model, next(row for row in observed['models'] if row['name'] == model['name']))
                self.assertEqual(model['confidence'], 'verified')
                self.assertEqual(model['ordered_terminal_roles'], ['d', 'g', 's', 'b'])
        self.assertEqual(result['discovery']['source_sha256'], 'a' * 64)

    def test_full_scan_keeps_wrapper_scoped_nominal_literals(self):
        path = self.models / 'a_core.scs'
        path.write_text(path.read_text().replace('subckt n_core_ulvt d g s b\n',
            'subckt n_core_ulvt d g s b\nparameters vnom=0.8\n'))
        library = library_scan(self.models)
        evidence = [row for candidate in library['entry_candidates']
                    for row in candidate['nominal_voltage_evidence']]
        self.assertTrue(any(row['value'] == 0.8 for row in evidence))

    def test_generated_library_script_retains_python_27_grammar(self):
        try:
            from lib2to3.refactor import RefactoringTool
        except ImportError:
            self.skipTest('Python 2 grammar checker unavailable')
        RefactoringTool([]).refactor_string(build_pdk_library_script(roots=[str(self.models)]), 'pdk-library.py')

    def test_missing_model_is_not_a_hit_or_a_fabricated_discovery(self):
        result = self.query(['n_core_ulvt', 'never_observed'])
        self.assertEqual(result['status'], 'partial')
        self.assertIsNone(result['discovery'])
        self.assertEqual(result['missing'][0]['model_names'], ['never_observed'])
        self.assertTrue(result['scan_required'])

    def test_filtered_receipts_cannot_be_promoted_or_supply_full_library_hits(self):
        filtered = scan(self.models, inspect_netlist(SOURCE))
        with self.assertRaises(DomainError): self.lut.put_library(filtered, server_identity=IDENTITY)
        with PdkLut(self.root / 'filtered.sqlite3') as cache:
            cache.put(filtered, server_identity=IDENTITY, observed_at=100)
            result = cache.lookup_library_models(**self.scope, required_masters=['n_core_ulvt'])
            self.assertEqual(result['status'], 'miss')
            self.assertIsNone(result['discovery'])

    def test_bound_coverage_and_pages_do_not_claim_the_whole_pdk_was_covered(self):
        partial = library_scan(self.models, limits={'max_files': 1})
        self.assertEqual(partial['status'], 'partial')
        self.assertFalse(partial['coverage']['complete_within_policy'])
        self.assertFalse(partial['coverage']['whole_pdk_qualified'])
        names, offset = [], 0
        while True:
            page = pdk_library_page(self.library, offset=offset, limit=2)
            names += page['model_names']
            if page['next_offset'] is None: break
            offset = page['next_offset']
        self.assertEqual(names, self.library['coverage']['indexed_model_names'])

    def test_library_name_pages_bind_continuation_to_the_original_observation(self):
        first = self.lut.query_library_devices(**self.scope, limit=2)
        (self.models / 'new.scs').write_text('model newly_observed bsim4 type=n\n')
        newer = library_scan(self.models); self.lut.put_library(newer, server_identity=IDENTITY, observed_at=101)
        with self.assertRaises(DomainError): self.lut.query_library_devices(**self.scope, offset=2)
        following = self.lut.query_library_devices(**self.scope, library_id=first['record']['library_id'], offset=2)
        self.assertEqual(following['record']['library_id'], first['record']['library_id'])
        self.assertNotIn('newly_observed', [row['model_name'] for row in following['devices']])

    def test_stale_other_server_roots_or_content_fingerprint_never_reuse_bindings(self):
        for changes, expected in (({'now': 99}, 'stale'), ({'now': 1000000}, 'stale'),
                ({'server_identity': {**IDENTITY, 'hostname': 'other'}}, 'miss'),
                ({'roots': [str(self.root)]}, 'miss'), ({'content_fingerprint': '0' * 64}, 'miss'),
                ({'refresh': True}, 'refresh_required')):
            with self.subTest(changes=changes):
                result = self.lut.lookup_library_models(**{**self.scope, **changes}, required_masters=['n_core_ulvt'])
                self.assertEqual(result['status'], expected); self.assertIsNone(result['discovery'])

    def test_disjoint_entries_return_an_explicit_unaudited_combination_not_fake_single_entry(self):
        (self.models / 'usage.scs').unlink()
        library = library_scan(self.models); self.lut.put_library(library, server_identity=IDENTITY, observed_at=101)
        result = self.query(['n_core_ulvt', 'io_device'])
        self.assertEqual(result['status'], 'partial'); self.assertIsNone(result['discovery'])
        self.assertEqual(result['combination']['covered_model_names'], ['io_device', 'n_core_ulvt'])
        self.assertEqual(len(result['combination']['entries']), 2)
        self.assertFalse(result['combination']['combination_verified'])
        self.assertFalse(result['combination']['ready_to_run'])
        self.assertIn('combined_dependency_audit_required', {row['code'] for row in result['combination']['missing']})

    def test_conflicting_unrequested_global_declarations_are_retained_in_combination_gaps(self):
        (self.models / 'usage.scs').unlink()
        for name in ('a_core.scs', 'b_io.scs'):
            path = self.models / name; path.write_text('model duplicated_global bsim4 type=n\n' + path.read_text())
        self.lut.put_library(library_scan(self.models), server_identity=IDENTITY, observed_at=101)
        result = self.query(['n_core_ulvt', 'io_device'])
        conflicts = [row for row in result['combination']['missing'] if row['code'] == 'duplicate_combined_model_declarations']
        self.assertTrue(conflicts); self.assertIn('duplicated_global', conflicts[0]['model_names'])

    def test_single_entry_can_index_more_than_128_observed_devices(self):
        root = self.root / 'large'; root.mkdir()
        (root / 'large.scs').write_text(''.join('model synthetic_%03d bsim4 type=n\n' % n for n in range(160)))
        library = library_scan(root)
        self.assertEqual(library['coverage']['indexed_model_count'], 160)
        self.lut.put_library(library, server_identity=IDENTITY, observed_at=100)
        result = self.lut.lookup_library_models(**{**self.scope, 'roots': [str(root)]},
            required_masters=['synthetic_159'], requirements=requirements(['synthetic_159']))
        self.assertEqual(result['status'], 'hit')
        self.assertEqual(result['discovery']['entry_candidates'][0]['models'][0]['name'], 'synthetic_159')

    def test_filtered_cache_rows_survive_additive_v3_migration(self):
        filtered = scan(self.models, inspect_netlist(SOURCE))
        self.lut.put(filtered, server_identity=IDENTITY, observed_at=100)
        self.lut.connection.execute('PRAGMA user_version=2')
        with PdkLut(self.path) as migrated:
            self.assertEqual(migrated.index_version, 3)
            result = migrated.lookup(**self.scope, required_masters=filtered['required_masters'])
            self.assertEqual(result['record']['discovery'], filtered)

    def test_explicit_observed_entry_bounds_large_arbitrary_model_sets_without_rescan(self):
        root = self.root / 'many'; root.mkdir()
        names = ['bounded_%03d' % number for number in range(128)]
        (root / 'devices.scs').write_text('section models\n' + ''.join(
            'model %s bsim4 type=n\n' % name for name in names) + 'endsection models\n')
        (root / 'usage.scs').write_text(''.join('section Corner%02d\ninclude "devices.scs" section=models\nendsection Corner%02d\n'
                                              % (number, number) for number in range(32)))
        library = library_scan(root)
        self.lut.put_library(library, server_identity=IDENTITY, observed_at=100)
        scope = {**self.scope, 'roots': [str(root)], 'required_masters': names, 'requirements': requirements(names)}
        wide = self.lut.lookup_library_models(**scope)
        self.assertEqual(wide['status'], 'partial')
        self.assertEqual(wide['missing'][0]['code'], 'binding_query_size')
        selected = wide['entry_choices'][0]['library_candidate_id']
        with patch('builtins.open', side_effect=AssertionError('No second library scan')):
            result = self.lut.lookup_library_models(**scope, library_candidate_id=selected)
        self.assertEqual(result['status'], 'hit')
        self.assertEqual(len(result['discovery']['entry_candidates']), 1)
        self.assertEqual(len(result['discovery']['entry_candidates'][0]['models']), 128)
        unknown = self.lut.lookup_library_models(**scope, library_candidate_id='0' * 64)
        self.assertEqual(unknown['status'], 'miss')
        self.assertIsNone(unknown['discovery'])

    def test_malformed_rehashed_library_rows_fail_as_domain_errors(self):
        from spiceowl.onboarding.pdk_discovery import _digest
        for change in ('bad_entry', 'bad_model', 'bad_file', 'bad_declaration'):
            with self.subTest(change=change):
                library = copy.deepcopy(self.library)
                if change == 'bad_entry': library['entry_candidates'][0] = None
                elif change == 'bad_model': library['entry_candidates'][0]['models'][0] = None
                elif change == 'bad_file': library['files'][0] = None
                else: library['declarations'][0] = None
                library['library_id'] = _digest({key: value for key, value in library.items() if key != 'library_id'})
                with self.assertRaises(DomainError): validate_pdk_library(library)

    def test_logical_and_canonical_roots_reuse_one_observation_without_local_resolution(self):
        logical = self.root / 'selected-pdk'; logical.symlink_to(self.models, target_is_directory=True)
        library = library_scan(logical)
        self.assertEqual(library['requested_roots'], [str(logical)])
        self.assertEqual(library['roots'], [str(self.models)])
        saved = self.lut.put_library(library, server_identity=IDENTITY, observed_at=101)
        self.assertEqual(saved['root_resolutions'], [{'requested_root': str(logical), 'canonical_root': str(self.models)}])
        with patch('os.path.realpath', side_effect=AssertionError('No client-side path resolution')):
            for roots in ([str(logical)], [str(self.models)]):
                for names in (['n_core_ulvt'], ['io_device'], ['p_core_ulvt', 'io_device']):
                    result = self.lut.lookup_library_models(**{**self.scope, 'roots': roots},
                        required_masters=names, requirements=requirements(names))
                    self.assertEqual(result['status'], 'hit')
                    self.assertEqual(result['record']['library_id'], library['library_id'])
                    self.assertFalse(result['remote_freshness_verified'])
            for change in ({'roots': [str(self.root / 'wrong-logical')]},
                           {'server_identity': {**IDENTITY, 'hostname': 'different-host'}}):
                result = self.lut.query_library_devices(**{**self.scope, 'roots': [str(logical)], **change})
                self.assertEqual(result['status'], 'miss')

    def test_root_aliases_are_complete_sets_from_one_observation_not_mixable_fragments(self):
        other = self.root / 'other-library'; other.mkdir()
        (other / 'device.scs').write_text('model other_primitive bsim4 type=n\n')
        aliases = [self.root / 'alias-a', self.root / 'alias-b']
        aliases[0].symlink_to(self.models, target_is_directory=True)
        aliases[1].symlink_to(other, target_is_directory=True)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(build_pdk_library_script(roots=[str(path) for path in aliases]), {})
        library = validate_pdk_library(json.loads(output.getvalue()))
        self.lut.put_library(library, server_identity=IDENTITY, observed_at=101)
        for roots in ([str(path) for path in reversed(aliases)], [str(self.models), str(other)]):
            self.assertEqual(self.lut.query_library_devices(**{**self.scope, 'roots': roots})['status'], 'hit')
        for roots in ([str(aliases[0]), str(other)], [str(aliases[0])], [str(aliases[1])]):
            self.assertEqual(self.lut.query_library_devices(**{**self.scope, 'roots': roots})['status'], 'miss')

    def test_convergent_logical_roots_keep_a_complete_mapping_to_one_canonical_root(self):
        aliases = [self.root / 'same-a', self.root / 'same-b']
        for alias in aliases: alias.symlink_to(self.models, target_is_directory=True)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(build_pdk_library_script(roots=[str(path) for path in aliases]), {})
        library = validate_pdk_library(json.loads(output.getvalue()))
        self.assertEqual(library['roots'], [str(self.models), str(self.models)])
        self.lut.put_library(library, server_identity=IDENTITY, observed_at=101)
        for roots in ([str(path) for path in aliases], [str(self.models)]):
            self.assertEqual(self.lut.query_library_devices(**{**self.scope, 'roots': roots})['status'], 'hit')
        self.assertEqual(self.lut.query_library_devices(**{**self.scope, 'roots': [str(aliases[0])]})['status'], 'miss')

    def test_new_scan_of_retargeted_symlink_supersedes_previous_default_scope(self):
        logical = self.root / 'moving-pdk'; logical.symlink_to(self.models, target_is_directory=True)
        previous = library_scan(logical)
        saved = self.lut.put_library(previous, server_identity=IDENTITY, observed_at=100)
        other = self.root / 'replacement'; other.mkdir()
        (other / 'device.scs').write_text('model replacement_model bsim4 type=p\n')
        logical.unlink(); logical.symlink_to(other, target_is_directory=True)
        newer = library_scan(logical)
        self.lut.put_library(newer, server_identity=IDENTITY, observed_at=101)
        scope = {**self.scope, 'roots': [str(logical)]}
        result = self.lut.lookup_library_models(**scope, required_masters=['replacement_model'])
        self.assertEqual(result['status'], 'hit')
        self.assertEqual(result['record']['library_id'], newer['library_id'])
        result = self.lut.lookup_library_models(**scope, required_masters=['n_core_ulvt'])
        self.assertEqual(result['status'], 'miss')
        self.assertEqual(result['record']['library_id'], newer['library_id'])
        self.assertEqual(self.lut.query_library_devices(**scope, content_fingerprint=saved['content_fingerprint'])['status'], 'miss')
        historical = self.lut.query_library_devices(**scope, library_id=previous['library_id'])
        self.assertEqual(historical['record']['library_id'], previous['library_id'])
        self.assertFalse(historical['remote_freshness_verified'])
        self.assertEqual(self.lut.query_library_devices(**{**scope, 'now': 1000000})['status'], 'stale')

    def test_equally_recent_different_root_resolutions_are_not_selected_by_digest_order(self):
        logical = self.root / 'tied-pdk'; logical.symlink_to(self.models, target_is_directory=True)
        self.lut.put_library(library_scan(logical), server_identity=IDENTITY, observed_at=100)
        other = self.root / 'tied-replacement'; other.mkdir()
        (other / 'device.scs').write_text('model tied_model bsim4 type=p\n')
        logical.unlink(); logical.symlink_to(other, target_is_directory=True)
        self.lut.put_library(library_scan(logical), server_identity=IDENTITY, observed_at=100)
        result = self.lut.query_library_devices(**{**self.scope, 'roots': [str(logical)]})
        self.assertEqual(result['status'], 'miss')
        self.assertEqual(result['missing'][0]['code'], 'root_alias_ambiguous')

    def test_legacy_library_without_requested_roots_remains_canonical_only(self):
        from spiceowl.onboarding.pdk_discovery import _digest
        legacy = copy.deepcopy(self.library); legacy.pop('requested_roots')
        legacy['library_id'] = _digest({key: value for key, value in legacy.items() if key != 'library_id'})
        self.lut.put_library(legacy, server_identity=IDENTITY, observed_at=101)
        self.lut.connection.execute('DROP TABLE library_root_scopes')
        with PdkLut(self.path, read_only=True) as reader:
            result = reader.query_library_devices(**self.scope)
            self.assertEqual(result['record']['library_id'], legacy['library_id'])
        with PdkLut(self.path) as migrated:
            self.assertTrue(migrated.library_root_index_available)
            self.assertEqual(migrated.query_library_devices(**self.scope)['status'], 'hit')

    def test_rehashed_incomplete_duplicate_or_colliding_root_mappings_fail(self):
        from spiceowl.onboarding.pdk_discovery import _digest
        cases = [([], self.library['roots']), ([str(self.models)] * 2, self.library['roots'] * 2),
                 ([str(self.models), '/synthetic/other'], ['/synthetic/other', str(self.models)])]
        for requested, canonical in cases:
            changed = copy.deepcopy(self.library)
            changed['requested_roots'], changed['roots'] = requested, canonical
            changed['library_id'] = _digest({key: value for key, value in changed.items() if key != 'library_id'})
            with self.assertRaises(DomainError): validate_pdk_library(changed)


if __name__ == '__main__': unittest.main()

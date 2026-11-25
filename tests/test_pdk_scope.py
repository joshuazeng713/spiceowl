"""Public cached-scope API over original synthetic model files."""
import copy
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

from spiceowl.domain.common import DomainError
from spiceowl.onboarding.pdk_discovery import _digest
from spiceowl.onboarding.pdk_scope import (library_scope_catalog,
    project_library_scopes, validate_scope_selection)
from tests.test_pdk_library import library_scan, requirements


IDENTITY = {'hostname': 'synthetic-host', 'username': 'researcher', 'uid': 1000}


@unittest.skipIf(sys.platform == "win32", "Executes remote Linux POSIX-path PDK scanner against local temp dirs")
class CachedScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'entry.scs').write_text(
            'section Prelude\nparameters tnom=27\nendsection Prelude\n'
            'section MOS\ninclude "device.scs" section=Core\nendsection MOS\n'
            'section CAP\ninclude "capacitor.scs"\nendsection CAP\n')
        (self.root / 'device.scs').write_text(
            'section Core\nsubckt device d g s b\nends device\nendsection Core\n')
        (self.root / 'capacitor.scs').write_text('subckt cap a b\nends cap\n')
        self.library = library_scan(self.root)
        self.required = requirements(['device', 'cap'])

    def choices(self, library=None):
        library = self.library if library is None else library
        catalog = library_scope_catalog(library)
        entry = next(row for row in catalog['items'] if row['label'] == 'entry.scs')
        sections = library_scope_catalog(library, entry_id=entry['entry_id'])
        return entry, {row['section']: row['section_id'] for row in sections['items']}

    def project(self, library=None, sections=('Prelude', 'MOS', 'CAP'), **kwargs):
        library = self.library if library is None else library
        entry, choices = self.choices(library)
        return project_library_scopes(library, requirements=kwargs.get('requirements', self.required),
            server_identity=kwargs.get('server_identity', IDENTITY), entry_id=entry['entry_id'],
            section_ids=[choices[section] for section in sections])

    def test_catalog_pages_entries_and_exact_sections_without_paths(self):
        full = library_scope_catalog(self.library)
        collected, offset = [], 0
        while offset is not None:
            page = library_scope_catalog(self.library, offset=offset, limit=1)
            collected.extend(page['items']); offset = page['next_offset']
        self.assertEqual(collected, full['items'])
        entry, _ = self.choices()
        sections = library_scope_catalog(self.library, entry_id=entry['entry_id'])
        self.assertEqual(sections['kind'], 'sections')
        self.assertEqual(sections['total'], 4)
        self.assertEqual(sections['items'][0]['section'], None)
        self.assertFalse(full['ready_to_run'])
        self.assertNotIn(str(self.root), json.dumps([full, sections]))

    def test_catalog_offsets_and_empty_tail_are_finite(self):
        empty = library_scope_catalog(self.library, offset=16384)
        self.assertEqual(empty['items'], [])
        self.assertIsNone(empty['next_offset'])
        for args in ({'offset': -1}, {'offset': True}, {'offset': 16385},
                     {'limit': 0}, {'limit': False}, {'limit': 129}):
            with self.subTest(args=args), self.assertRaises(DomainError):
                library_scope_catalog(self.library, **args)

    def test_deterministic_ordered_selection_with_full_source_requirements_binding(self):
        first = self.project()
        self.assertEqual(first, self.project())
        self.assertEqual(validate_scope_selection(first), first)
        public, private = first['public'], first['private']
        self.assertEqual(public['status'], 'requires_audit')
        self.assertEqual(public['sections'], ['Prelude', 'MOS', 'CAP'])
        self.assertEqual(public['covered_model_names'], ['device', 'cap'])
        self.assertEqual(public['missing_model_names'], [])
        self.assertEqual(public['model_declarations'], [
            {'name': 'device', 'declaration_count': 1, 'port_counts': [4]},
            {'name': 'cap', 'declaration_count': 1, 'port_counts': [2]}])
        self.assertEqual(private['audit_config']['sections'], public['sections'])
        self.assertFalse(public['ready_to_run'])
        self.assertFalse(public['model_contracts_reused'])
        self.assertEqual(public['scientific_compatibility'], 'not_evaluated')
        self.assertNotIn(str(self.root), json.dumps(public))
        reversed_selection = self.project(sections=('CAP', 'MOS', 'Prelude'))
        self.assertNotEqual(public['selection_id'], reversed_selection['public']['selection_id'])
        self.assertNotEqual(public['selection_id'], self.project(
            server_identity=dict(IDENTITY, hostname='another-host'))['public']['selection_id'])
        changed = dict(self.required, source_sha256='b' * 64)
        self.assertNotEqual(public['selection_id'], self.project(requirements=changed)['public']['selection_id'])
        changed = dict(self.required, issues=[{'code': 'additional_observation'}])
        self.assertNotEqual(public['selection_id'], self.project(requirements=changed)['public']['selection_id'])

    def test_candidate_cap_does_not_hide_observed_combined_scopes(self):
        library = library_scan(self.root, limits={'max_candidates': 1})
        self.assertFalse(library['coverage']['complete_within_policy'])
        self.assertEqual(len(library['entry_candidates']), 1)
        result = self.project(library)
        self.assertEqual(result['public']['status'], 'requires_audit')
        self.assertFalse(result['public']['library_complete_within_policy'])
        self.assertEqual(result['public']['missing'], [])
        self.assertFalse(result['public']['ready_to_run'])

    def test_missing_required_name_blocks_without_inventing_a_model_contract(self):
        result = self.project(requirements=requirements(['device', 'missing_device']))
        self.assertEqual(result['public']['status'], 'blocked')
        self.assertEqual(result['public']['missing_model_names'], ['missing_device'])
        self.assertIsNone(result['private']['audit_config'])
        self.assertNotIn('models', result['private'])
        self.assertNotIn('model_contracts', result['private'])

    def test_omitted_graph_has_no_fabricated_catalog_choices(self):
        library = copy.deepcopy(self.library)
        library['section_graph'] = []
        library['detail_omissions'] = [{'field': 'section_graph', 'reason': 'response_limit'}]
        library['library_id'] = _digest({k: v for k, v in library.items() if k != 'library_id'})
        result = library_scope_catalog(library)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['items'], [])
        self.assertTrue(result['missing'])
        with self.assertRaises(DomainError):
            self.project(library=self.library, requirements={'schema': 'wrong'})

    def test_scope_calls_do_not_mutate_inputs_read_paths_or_execute_programs(self):
        library, required = copy.deepcopy(self.library), copy.deepcopy(self.required)
        with (patch('builtins.open', side_effect=AssertionError('No filesystem access')),
              patch('subprocess.run', side_effect=AssertionError('No subprocess')),
              patch('socket.socket', side_effect=AssertionError('No network'))):
            result = self.project()
            validate_scope_selection(result)
        self.assertEqual(self.library, library)
        self.assertEqual(self.required, required)
        result['private']['requirements']['required_masters'].append('modified')
        result['private']['master_declarations'][0]['ports'].append('modified')
        self.assertEqual(self.required, required)
        self.assertEqual(self.library, library)

    def test_oversized_requirements_do_not_silently_drop_evidence(self):
        with self.assertRaises(DomainError):
            self.project(requirements=dict(self.required, note='x' * (2 * 1024 * 1024)))

    def test_rehashed_noninteger_public_counts_are_invalid(self):
        for field in ('scope', 'declaration', 'ports'):
            selected = self.project()
            if field == 'scope':
                selected['public']['scope']['node_count'] = float(selected['public']['scope']['node_count'])
            elif field == 'declaration':
                selected['public']['model_declarations'][0]['declaration_count'] = True
            else:
                selected['public']['model_declarations'][0]['port_counts'] = [4.0]
            selected['public']['selection_id'] = _digest({
                'schema': selected['schema'], 'private': selected['private'],
                'public': {k: v for k, v in selected['public'].items() if k != 'selection_id'}})
            with self.subTest(field=field), self.assertRaises(DomainError):
                validate_scope_selection(selected)

    def test_non_utf8_path_is_a_domain_error(self):
        selected = self.project()
        selected['private']['entry_path'] = '/synthetic/\ud800'
        selected['public']['selection_id'] = _digest({
            'schema': selected['schema'], 'private': selected['private'],
            'public': {k: v for k, v in selected['public'].items() if k != 'selection_id'}})
        with self.assertRaises(DomainError):
            validate_scope_selection(selected)


if __name__ == '__main__':
    unittest.main()

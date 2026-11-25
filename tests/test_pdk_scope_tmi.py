"""Original synthetic TMI/namespace fixtures, without SSH or simulator execution."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

from spiceowl.domain.common import DomainError
from spiceowl.onboarding.pdk_discovery import build_pdk_library_script, validate_pdk_library
from spiceowl.onboarding.pdk_scope import (
    library_scope_catalog, project_library_scopes, validate_scope_selection,
)


@unittest.skipIf(sys.platform == "win32", "Executes remote Linux POSIX-path PDK scanner against local temp dirs")
class PdkScopeTmiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.entry = self.root / 'entry.scs'
        self.entry.write_text(
            'model global_helper bsim4 type=n\ninclude "global.scs"\n'
            'section MOS\ninclude "mos.scs" section=Core\nendsection MOS\n'
            'section CAP\ninclude "cap.scs"\nendsection CAP\n'
            'section Unsafe\nif conditional\nmodel unsafe_device bsim4 type=n\nendsection Unsafe\n')
        (self.root / 'global.scs').write_text('model common_helper bsim4 type=n\n')
        (self.root / 'mos.scs').write_text(
            'section Core\n.option tmipath="tmi-mos"\n'
            'model n_device bsim4 type=n\nmodel p_device bsim4 type=p\nendsection Core\n')
        (self.root / 'cap.scs').write_text(
            '.option etmiUsrInput="tmi-cap"\nsubckt cap_device a b\n'
            'C0 (a b) capacitor c=1p\nends cap_device\n')
        for directory in ('tmi-mos', 'tmi-cap'):
            (self.root / directory).mkdir()
        self.requirements = {'schema': 'spiceowl.netlist-model-requirements.v1',
            'source_sha256': 'c' * 64, 'required_masters': ['n_device', 'p_device', 'cap_device']}
        self.identity = {'hostname': 'synthetic-host', 'username': 'synthetic-user', 'uid': 1000}

    def binary(self, directory, platform, content=b'original synthetic common TMI bytes\x00'):
        path = self.root / directory / platform / 'libTMImodel.so'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return str(path)

    def common_binary(self):
        return {self.binary(directory, 'RH_64') for directory in ('tmi-mos', 'tmi-cap')}

    def library(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(build_pdk_library_script(roots=[str(self.root)]), {})
        return validate_pdk_library(json.loads(output.getvalue()))

    def arguments(self, library, sections=('MOS', 'CAP'), required=None):
        entries = library_scope_catalog(library)['items']
        entry = next(row for row in entries if row['label'] == self.entry.name)
        observed = library_scope_catalog(library, entry_id=entry['entry_id'])['items']
        section_ids = [next(row['section_id'] for row in observed if row['section'] == section)
                       for section in sections]
        requirements = dict(self.requirements)
        if required is not None:
            requirements['required_masters'] = list(required)
        return {'requirements': requirements, 'server_identity': self.identity,
                'entry_id': entry['entry_id'], 'section_ids': section_ids}

    def choose(self, library, arguments, label='RH_64/libTMImodel.so'):
        proposed = project_library_scopes(library, **arguments)
        selected = next(row['tmi_option_id'] for row in proposed['public']['tmi_options']
                        if row['label'] == label)
        return project_library_scopes(library, **arguments, tmi_option_id=selected)

    def codes(self, selection):
        return {row['code'] for row in selection['public']['missing']}

    def test_common_abi_intersects_directories_and_pins_only_selected_binaries_without_io(self):
        expected_binaries = self.common_binary()
        unselected = {self.binary('tmi-mos', 'MosOnly'), self.binary('tmi-cap', 'CapOnly')}
        library = self.library()
        arguments = self.arguments(library)
        with (patch('builtins.open', side_effect=AssertionError('Cached projection only')),
              patch('subprocess.run', side_effect=AssertionError('No remote operation'))):
            proposed = project_library_scopes(library, **arguments)
            selection = self.choose(library, arguments)
            self.assertIs(validate_scope_selection(selection), selection)
        self.assertEqual([row['label'] for row in proposed['public']['tmi_options']], ['RH_64/libTMImodel.so'])
        self.assertEqual(self.codes(proposed), {'scope_tmi_selection_required'})
        self.assertFalse(expected_binaries & {row['path'] for row in proposed['private']['expected_files']})
        self.assertEqual(selection['public']['status'], 'requires_audit')
        self.assertEqual(self.codes(selection), set())
        pins = {row['path'] for row in selection['private']['expected_files']}
        self.assertTrue(expected_binaries <= pins)
        self.assertFalse(unselected & pins)
        self.assertEqual(selection['private']['audit_config']['tmi_relative_library'], 'RH_64/libTMImodel.so')
        self.assertFalse(selection['public']['ready_to_run'])
        self.assertNotIn(str(self.root), json.dumps(selection['public']))

    def test_full_namespace_is_retained_while_public_coverage_is_required_only(self):
        self.common_binary()
        library = self.library()
        selection = self.choose(library, self.arguments(library))
        self.assertEqual(selection['public']['covered_model_names'], ['n_device', 'p_device', 'cap_device'])
        self.assertEqual({row['name'] for row in selection['private']['master_declarations']},
                         {'global_helper', 'common_helper', 'n_device', 'p_device', 'cap_device'})
        self.assertEqual(selection['public']['scope']['namespace_model_count'], 5)
        self.assertFalse(selection['public']['model_contracts_reused'])
        self.assertEqual(selection['public']['scientific_compatibility'], 'not_evaluated')

    def test_same_abi_with_different_binary_hashes_blocks_selection(self):
        self.binary('tmi-mos', 'RH_64', b'original synthetic MOS binary')
        self.binary('tmi-cap', 'RH_64', b'original synthetic CAP binary')
        library = self.library()
        selection = project_library_scopes(library, **self.arguments(library))
        self.assertEqual(selection['public']['tmi_options'], [])
        self.assertIn('scope_tmi_binary_conflict', self.codes(selection))
        self.assertEqual(selection['public']['status'], 'blocked')
        self.assertIsNone(selection['private']['audit_config'])

    def test_compatible_selected_abi_is_not_blocked_by_an_unselected_conflicting_abi(self):
        self.common_binary()
        self.binary('tmi-mos', 'Other_64', b'original synthetic other MOS binary')
        self.binary('tmi-cap', 'Other_64', b'original synthetic other CAP binary')
        library = self.library()
        selection = self.choose(library, self.arguments(library))
        self.assertEqual(selection['public']['status'], 'requires_audit')
        self.assertEqual(self.codes(selection), set())
        self.assertEqual([row['label'] for row in selection['public']['tmi_options']], ['RH_64/libTMImodel.so'])

    def test_absent_binary_in_one_referenced_directory_is_an_explicit_gap(self):
        self.binary('tmi-mos', 'RH_64')
        library = self.library()
        selection = project_library_scopes(library, **self.arguments(library))
        self.assertEqual(selection['public']['tmi_options'], [])
        self.assertTrue({'scope_tmi_platform_missing', 'scope_tmi_no_common_platform'} <= self.codes(selection))
        self.assertIsNone(selection['private']['audit_config'])

    def test_disjoint_observed_abis_do_not_guess_the_client_platform(self):
        self.binary('tmi-mos', 'MosOnly')
        self.binary('tmi-cap', 'CapOnly')
        library = self.library()
        selection = project_library_scopes(library, **self.arguments(library))
        self.assertEqual(selection['public']['tmi_options'], [])
        self.assertIn('scope_tmi_no_common_platform', self.codes(selection))
        self.assertNotIn('scope_tmi_platform_missing', self.codes(selection))
        self.assertIsNone(selection['private']['audit_config'])

    def test_unknown_only_in_unselected_scope_does_not_block_selected_closure(self):
        self.common_binary()
        library = self.library()
        catalog = library_scope_catalog(library)
        self.assertEqual(catalog['status'], 'available')
        self.assertFalse(catalog['library_complete_within_policy'])
        selection = self.choose(library, self.arguments(library))
        self.assertEqual(selection['public']['status'], 'requires_audit')
        self.assertNotIn('scope_graph_unknown_scope', self.codes(selection))
        unsafe = project_library_scopes(library, **self.arguments(library, ('Unsafe',), ['unsafe_device']))
        self.assertIn('scope_graph_unknown_scope', self.codes(unsafe))
        self.assertIsNone(unsafe['private']['audit_config'])

    def test_none_global_scope_does_not_import_section_tmi_or_models(self):
        library = self.library()
        selection = project_library_scopes(library, **self.arguments(
            library, (None,), ['global_helper', 'common_helper']))
        self.assertEqual(selection['public']['status'], 'requires_audit')
        self.assertEqual(selection['public']['sections'], [None])
        self.assertEqual(selection['private']['audit_config']['sections'], [None])
        self.assertEqual(selection['private']['tmi_evidence'], [])
        self.assertEqual(selection['public']['tmi_options'], [])
        self.assertEqual(selection['private']['dependency_scopes'], [
            {'path': str(self.entry), 'section': None},
            {'path': str(self.root / 'global.scs'), 'section': None},
        ])

    def test_same_entry_section_order_is_preserved_and_binds_tmi_option_identity(self):
        self.common_binary()
        library = self.library()
        first_arguments = self.arguments(library, (None, 'CAP', 'MOS'))
        second_arguments = self.arguments(library, ('MOS', None, 'CAP'))
        first = self.choose(library, first_arguments)
        second = self.choose(library, second_arguments)
        self.assertEqual(first['public']['sections'], [None, 'CAP', 'MOS'])
        self.assertEqual(first['private']['audit_config']['sections'], [None, 'CAP', 'MOS'])
        self.assertEqual([(Path(row['path']).name, row['section']) for row in first['private']['dependency_scopes']],
            [('entry.scs', None), ('global.scs', None), ('entry.scs', 'CAP'),
             ('cap.scs', None), ('entry.scs', 'MOS'), ('mos.scs', 'Core')])
        self.assertEqual(second['private']['audit_config']['sections'], ['MOS', None, 'CAP'])
        self.assertNotEqual(first['public']['selection_id'], second['public']['selection_id'])
        self.assertNotEqual(first['public']['selected_tmi_option_id'], second['public']['selected_tmi_option_id'])
        with self.assertRaises(DomainError) as raised:
            project_library_scopes(library, **second_arguments,
                                   tmi_option_id=first['public']['selected_tmi_option_id'])
        self.assertEqual(raised.exception.code, 'invalid_pdk_scope')


if __name__ == '__main__':
    unittest.main()

"""Synthetic scope/audit comparison; no SSH, simulator or licensed PDK."""

import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import sys
import unittest
from unittest.mock import Mock, patch

from spiceowl.domain.common import DomainError
from spiceowl.integrations.model_dependencies import build_model_audit_script, validate_audit_config
from spiceowl.onboarding.pdk_scope_audit import verify_scope_selection_audit


@unittest.skipIf(sys.platform == "win32", "Executes remote Linux POSIX-path PDK scanner against local temp dirs")
class ScopeSelectionAuditIntegrationTests(unittest.TestCase):
    """Exercise real cached-library selection and real local synthetic audits."""

    def setUp(self):
        from spiceowl.onboarding import pdk_scope
        self.facade = pdk_scope
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.entry = self.root / 'entry.scs'
        self.entry.write_text('subckt entry_global x y\nends entry_global\ninclude "global.scs"\n'
            'section Core\ninclude "devices.scs" section=Nmos\nendsection Core\n'
            'section IO\ninclude "devices.scs" section=Pmos\nendsection IO\n')
        (self.root / 'global.scs').write_text('subckt global_ref a b\nends global_ref\n')
        (self.root / 'devices.scs').write_text('subckt global_device d g s b\nends global_device\n'
            'section Nmos\nsubckt n_device D G S B\nends n_device\nendsection Nmos\n'
            'section Pmos\ninline subckt p_device d g s b\nends p_device\nendsection Pmos\n')
        self.required = ['entry_global', 'global_ref', 'global_device', 'n_device', 'p_device']
        self.identity = {'hostname': 'synthetic-host', 'username': 'synthetic-user', 'uid': 1000}

    def library(self):
        from spiceowl.onboarding.pdk_discovery import build_pdk_library_script, validate_pdk_library
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(build_pdk_library_script(roots=[str(self.root)]), {})
        return validate_pdk_library(json.loads(output.getvalue()))

    def selection(self, sections, *, required=None, choose_tmi=True):
        library = self.library()
        catalog = self.facade.library_scope_catalog(library)
        entry = next(row for row in catalog['items'] if row['label'] == self.entry.name)
        options = self.facade.library_scope_catalog(library, entry_id=entry['entry_id'])['items']
        selected = [next(row['section_id'] for row in options if row['section'] == section) for section in sections]
        args = {'requirements': {'schema': 'spiceowl.netlist-model-requirements.v1',
                    'mode': 'agent-tb', 'source_sha256': 'c' * 64,
                    'required_masters': self.required if required is None else required, 'instances': []},
                'server_identity': self.identity, 'entry_id': entry['entry_id'], 'section_ids': selected}
        selection = self.facade.project_library_scopes(library, **args)
        if choose_tmi and selection['public']['tmi_options']:
            selection = self.facade.project_library_scopes(library, **args,
                tmi_option_id=selection['public']['tmi_options'][0]['tmi_option_id'])
        return selection

    def audit(self, config):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(build_model_audit_script(config), {})
        return json.loads(output.getvalue())

    def assert_matches(self, selection):
        self.assertEqual(selection['public']['status'], 'requires_audit')
        self.assertIs(self.facade.validate_scope_selection(selection), selection)
        config = selection['private']['audit_config']
        audit = self.audit(config)
        self.assertEqual(audit['status'], 'complete_for_supported_static_syntax')
        with (patch('builtins.open', side_effect=AssertionError('Pure comparison must not read files')),
              patch('subprocess.run', side_effect=AssertionError('Pure comparison must not execute'))):
            result = self.facade.verify_scope_selection_audit(selection, audit, audit_config=config)
        self.assertEqual(result['status'], 'matched', result['missing'])
        self.assertFalse(result['ready_to_run'])
        self.assertFalse(result['remote_freshness_verified'])
        self.assertNotIn(str(self.root), json.dumps(result))
        return audit, result

    def test_global_none_selection_only_pins_its_dependency_closure(self):
        selection = self.selection([None], required=['entry_global', 'global_ref'])
        audit, _ = self.assert_matches(selection)
        self.assertEqual(audit['visited_sections'], 2)
        self.assertEqual({Path(row['path']).name for row in audit['files']}, {'entry.scs', 'global.scs'})

    def test_combined_sections_with_and_without_explicit_global_selection(self):
        for sections, count in ((['Core', 'IO'], 5), ([None, 'Core', 'IO'], 6)):
            with self.subTest(sections=sections):
                selection = self.selection(sections)
                audit, _ = self.assert_matches(selection)
                self.assertEqual(audit['visited_sections'], count)
                self.assertEqual(len(selection['private']['master_declarations']), 5)
                self.assertEqual(len(audit['master_declarations']), 5)
                n_device = next(row for row in audit['master_declarations'] if row['name'] == 'n_device')
                self.assertEqual(n_device['ports'], ['D', 'G', 'S', 'B'])

    def test_selected_tmi_path_and_source_attribute_spellings_match_real_emitter(self):
        original = self.entry.read_text()
        for attribute in ('tmipath', 'etmiUsrInput', 'TMIPATH', 'etmiusrinput'):
            with self.subTest(attribute=attribute):
                self.entry.write_text('.option ' + attribute + '="tmi"\n' + original)
                binary = self.root / 'tmi' / 'RH_64' / 'libTMImodel.so'
                binary.parent.mkdir(parents=True, exist_ok=True)
                binary.write_bytes(b'original synthetic TMI bytes\x00')
                selection = self.selection([None, 'Core', 'IO'])
                audit, _ = self.assert_matches(selection)
                self.assertTrue(selection['public']['selected_tmi_option_id'])
                self.assertEqual({row['attribute'] for row in audit['edges'] if row['role'] == 'tmi_directory'},
                                 {attribute})
                self.assertEqual({row['attribute'] for row in selection['private']['tmi_evidence']}, {attribute})
                self.assertIn(str(binary), {row['path'] for row in selection['private']['expected_files']})
                self.assertIn(str(binary), {row['path'] for row in audit['files']})
                binary.write_bytes(b'changed synthetic TMI bytes\x00')
                config = selection['private']['audit_config']
                mismatch = self.facade.verify_scope_selection_audit(selection, self.audit(config), audit_config=config)
                self.assertEqual(mismatch['status'], 'mismatch')
                self.assertIn('audit_file_set_mismatch', {row['code'] for row in mismatch['missing']})

    def test_required_subset_can_omit_unchosen_namespace_declarations_in_fresh_audit(self):
        selection = self.selection(['Core', 'IO'], required=['n_device'])
        audit, _ = self.assert_matches(selection)
        self.assertEqual(len(selection['private']['master_declarations']), 5)
        self.assertEqual([row['name'] for row in audit['master_declarations']], ['n_device'])

    def test_extra_unchosen_library_file_is_not_accepted_as_subset_evidence(self):
        selection = self.selection([None], required=['entry_global', 'global_ref'])
        audit, _ = self.assert_matches(selection)
        audit['files'].append(dict(audit['files'][0], path=str(self.root / 'devices.scs')))
        audit['visited_sections'] += len(audit['files'][-1]['selected_sections'])
        result = self.facade.verify_scope_selection_audit(selection, audit,
            audit_config=selection['private']['audit_config'])
        self.assertEqual(result['status'], 'mismatch')
        self.assertIn('audit_file_set_mismatch', {row['code'] for row in result['missing']})

    def test_real_selection_identity_tampering_is_rejected_before_comparison(self):
        selection = self.selection(['Core', 'IO'])
        audit, _ = self.assert_matches(selection)
        selection['private']['expected_files'][0]['sha256'] = 'f' * 64
        with self.assertRaises(DomainError):
            self.facade.verify_scope_selection_audit(selection, audit,
                audit_config=selection['private']['audit_config'])


@unittest.skipIf(sys.platform == "win32", "Executes remote Linux POSIX-path PDK scanner against local temp dirs")
class ScopeSelectionAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        main = self.root / 'entry.scs'
        main.write_text('section TT\ninclude "device.scs" section=Core\nendsection TT\n')
        (self.root / 'device.scs').write_text('section Core\nsubckt example_n d g s b\n'
            'ends example_n\n.option tmipath="tmi"\nendsection Core\n')
        binary = self.root / 'tmi' / 'RH_64' / 'libTMImodel.so'
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b'original synthetic TMI bytes\x00')
        self.config = {'roots': [str(self.root)], 'main': str(main), 'sections': ['TT'],
                       'required_masters': ['example_n'], 'tmi_relative_library': 'RH_64/libTMImodel.so'}
        self.audit = self.run_audit()
        self.assertEqual(self.audit['status'], 'complete_for_supported_static_syntax')
        self.selection = {'schema': 'spiceowl.pdk-scope-selection.v1',
            'public': {'selection_id': 'a' * 64, 'status': 'requires_audit',
                       'required_model_names': ['example_n']},
            'private': {'audit_config': validate_audit_config(self.config),
                'expected_files': [{key: row[key] for key in ('path', 'sha256')}
                                   for row in self.audit['files']],
                'dependency_scopes': [{'path': row['path'], 'section': section}
                                      for row in self.audit['files'] for section in row['selected_sections']],
                'master_declarations': copy.deepcopy(self.audit['master_declarations'])}}
        # Selection construction/validation belongs to pdk_scope's own suite.
        # This isolates the comparison contract while retaining the mandatory
        # validator call and its fail-closed exception behavior.
        self.validator = Mock()
        module = types.ModuleType('spiceowl.onboarding.pdk_scope')
        module.validate_scope_selection = self.validator
        self.validator_patch = patch.dict(sys.modules, {'spiceowl.onboarding.pdk_scope': module})
        self.validator_patch.start()
        self.addCleanup(self.validator_patch.stop)

    def run_audit(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(build_model_audit_script(self.config), {})
        return json.loads(output.getvalue())

    def verify(self, *, selection=None, audit=None, config=None):
        return verify_scope_selection_audit(self.selection if selection is None else selection,
            self.audit if audit is None else audit, audit_config=self.config if config is None else config)

    def assert_mismatch(self, result, code=None):
        self.assertEqual(result['status'], 'mismatch')
        self.assertFalse(result['matches_selection'])
        self.assertFalse(result['ready_to_run'])
        self.assertFalse(result['remote_freshness_verified'])
        self.assertTrue(result['human_review_required'])
        self.assertTrue(result['fresh_audit_required'])
        self.assertEqual(result['scientific_compatibility'], 'not_evaluated')
        self.assertTrue(result['missing'])
        self.assertNotIn(str(self.root), json.dumps(result))
        if code:
            self.assertIn(code, {gap['code'] for gap in result['missing']})

    def test_local_synthetic_audit_matches_without_io_or_execution_authority(self):
        before_selection, before_audit = copy.deepcopy(self.selection), copy.deepcopy(self.audit)
        with (patch('builtins.open', side_effect=AssertionError('No file reads')),
              patch('subprocess.run', side_effect=AssertionError('No execution'))):
            result = self.verify()
        self.validator.assert_called_once_with(self.selection)
        self.assertEqual(result, {'schema': 'spiceowl.pdk-scope-audit-comparison.v1',
            'selection_id': 'a' * 64, 'status': 'matched', 'missing': [], 'matches_selection': True,
            'ready_to_run': False, 'human_review_required': True, 'remote_freshness_verified': False,
            'fresh_audit_required': True, 'scientific_compatibility': 'not_evaluated'})
        self.assertEqual(self.selection, before_selection)
        self.assertEqual(self.audit, before_audit)

    def test_selection_validator_rejection_precedes_config_or_audit_inspection(self):
        self.validator.side_effect = DomainError('Invalid selection.', 'invalid_pdk_scope')
        with self.assertRaises(DomainError):
            verify_scope_selection_audit(self.selection, object(), audit_config=object())

    def test_blocked_selection_or_missing_planned_config_cannot_match(self):
        blocked = copy.deepcopy(self.selection)
        blocked['public']['status'] = 'blocked'
        no_config = copy.deepcopy(self.selection)
        no_config['private']['audit_config'] = None
        for selection in (blocked, no_config):
            with self.subTest(selection=selection['public']['status']):
                self.assert_mismatch(self.verify(selection=selection), 'selection_not_auditable')

    def test_normalized_config_matches_and_changed_config_does_not(self):
        self.assertEqual(self.verify(config=validate_audit_config(self.config))['status'], 'matched')
        for updates in ({'sections': ['ss']}, {'required_masters': []},
                        {'limits': {'max_files': 32}}, {'tmi_relative_library': 'OTHER/libTMImodel.so'},
                        {'profile_dependencies': [{'path': str(self.root / 'other.cfg'),
                                                  'kind': 'file', 'role': 'tool_config'}]}):
            with self.subTest(updates=updates):
                self.assert_mismatch(self.verify(config=dict(self.config, **updates)), 'audit_config_mismatch')
        self.assert_mismatch(self.verify(config={}), 'audit_config_invalid')

    def test_incomplete_or_inconsistent_completion_fields_never_match(self):
        for update in ({'static_only': False}, {'status': 'unknown'}, {'unknown_count': 1},
                       {'unknown_count': False}, {'unknown': [{'code': 'omitted'}]},
                       {'unknown_truncated': True}, {'missing_masters': ['example_n']},
                       {'missing': [{'code': 'gap'}]}, {'gaps': ['not-complete']}):
            with self.subTest(update=update):
                self.assert_mismatch(self.verify(audit=dict(self.audit, **update)), 'audit_incomplete')
        for field in ('schema', 'static_only', 'status', 'unknown_count', 'unknown',
                      'unknown_truncated', 'missing_masters'):
            audit = copy.deepcopy(self.audit)
            audit.pop(field)
            self.assert_mismatch(self.verify(audit=audit))

    def test_missing_extra_or_changed_file_and_tmi_bytes_are_rejected(self):
        for row in self.audit['files']:
            with self.subTest(file_role=row['roles']):
                changed = copy.deepcopy(self.audit)
                next(item for item in changed['files'] if item['path'] == row['path'])['sha256'] = 'f' * 64
                self.assert_mismatch(self.verify(audit=changed), 'audit_file_set_mismatch')
                absent = copy.deepcopy(self.audit)
                absent['files'] = [item for item in absent['files'] if item['path'] != row['path']]
                self.assert_mismatch(self.verify(audit=absent), 'audit_file_set_mismatch')
        extra = copy.deepcopy(self.audit)
        extra['files'].append(dict(extra['files'][-1], path=str(self.root / 'unplanned.so')))
        self.assert_mismatch(self.verify(audit=extra), 'audit_file_set_mismatch')

    def test_duplicate_and_conflicting_file_records_are_rejected(self):
        for changed_hash in (False, True):
            audit = copy.deepcopy(self.audit)
            duplicate = copy.deepcopy(audit['files'][0])
            if changed_hash:
                duplicate['sha256'] = 'f' * 64
            audit['files'].append(duplicate)
            self.assert_mismatch(self.verify(audit=audit), 'invalid_audit_evidence')

    def test_new_local_audit_observes_changed_tmi_or_model_bytes(self):
        binary = self.root / 'tmi' / 'RH_64' / 'libTMImodel.so'
        original = binary.read_bytes()
        binary.write_bytes(b'different original synthetic TMI bytes\x00')
        changed = self.run_audit()
        self.assertEqual(changed['status'], 'complete_for_supported_static_syntax')
        self.assert_mismatch(self.verify(audit=changed), 'audit_file_set_mismatch')
        binary.write_bytes(original)
        model = self.root / 'device.scs'
        model.write_text(model.read_text() + '// same declaration; changed source bytes\n')
        changed = self.run_audit()
        self.assertEqual(changed['status'], 'complete_for_supported_static_syntax')
        self.assertEqual(changed['master_declarations'], self.audit['master_declarations'])
        self.assert_mismatch(self.verify(audit=changed), 'audit_file_set_mismatch')

    def test_scope_requires_exact_file_section_set_and_model_role(self):
        for sections in ([], ['ss'], ['core', None]):
            audit = copy.deepcopy(self.audit)
            next(row for row in audit['files'] if row['path'].endswith('device.scs'))['selected_sections'] = sections
            self.assert_mismatch(self.verify(audit=audit), 'audit_scope_mismatch')
        for sections in (['core', 'core'], ['core', 'CORE']):
            audit = copy.deepcopy(self.audit)
            next(row for row in audit['files'] if row['path'].endswith('device.scs'))['selected_sections'] = sections
            self.assert_mismatch(self.verify(audit=audit), 'invalid_audit_evidence')
        audit = copy.deepcopy(self.audit)
        next(row for row in audit['files'] if row['path'].endswith('device.scs'))['roles'] = ['tool_config']
        self.assert_mismatch(self.verify(audit=audit), 'invalid_audit_evidence')

    def test_audit_scope_count_and_limits_must_match_actual_evidence_and_config(self):
        for count in (None, True, 0, self.audit['visited_sections'] + 1):
            audit = dict(self.audit, visited_sections=count)
            self.assert_mismatch(self.verify(audit=audit), 'audit_scope_count_mismatch')
        audit = copy.deepcopy(self.audit)
        audit.pop('visited_sections')
        self.assert_mismatch(self.verify(audit=audit), 'audit_scope_count_mismatch')
        for limits in (None, {}, dict(self.audit['limits'], max_files=1),
                       dict(self.audit['limits'], max_files=float(self.audit['limits']['max_files']))):
            self.assert_mismatch(self.verify(audit=dict(self.audit, limits=limits)), 'audit_limits_mismatch')
        audit = copy.deepcopy(self.audit)
        audit.pop('limits')
        self.assert_mismatch(self.verify(audit=audit), 'audit_limits_mismatch')

    def test_sections_follow_existing_case_insensitive_traversal(self):
        selection = copy.deepcopy(self.selection)
        for row in selection['private']['dependency_scopes']:
            if row['section']:
                row['section'] = row['section'].upper()
        selection['private']['master_declarations'][0]['section'] = 'CORE'
        self.assertEqual(self.verify(selection=selection)['status'], 'matched')

    def test_required_declaration_identity_and_ordered_ports_are_exact(self):
        for change in ({'path': str(self.root / 'elsewhere.scs')}, {'line': 99}, {'section': 'ss'},
                       {'ports': ['g', 'd', 's', 'b']}, {'ports': ['d', 'g', 's'], 'port_count': 3},
                       {'kind': 'inline_subckt'}):
            audit = copy.deepcopy(self.audit)
            audit['master_declarations'][0].update(change)
            self.assert_mismatch(self.verify(audit=audit), 'required_declaration_mismatch')
        for change in ({'port_count': 3}, {'port_count': True}, {'ports': None}, {'line': True}):
            audit = copy.deepcopy(self.audit)
            audit['master_declarations'][0].update(change)
            self.assert_mismatch(self.verify(audit=audit), 'invalid_audit_evidence')

    def test_missing_or_multiple_required_physical_declarations_are_rejected(self):
        absent = copy.deepcopy(self.audit)
        absent['master_declarations'] = []
        self.assert_mismatch(self.verify(audit=absent), 'required_declaration_missing_or_ambiguous')
        for change in ({'line': 3}, {'path': str(self.root / 'different.scs')}, {'section': 'other'}):
            audit = copy.deepcopy(self.audit)
            audit['master_declarations'].append(dict(audit['master_declarations'][0], **change))
            self.assert_mismatch(self.verify(audit=audit), 'required_declaration_missing_or_ambiguous')

    def test_repeated_same_physical_declaration_and_unrequested_omissions_are_allowed(self):
        selection, audit = copy.deepcopy(self.selection), copy.deepcopy(self.audit)
        selection['private']['master_declarations'].append(dict(audit['master_declarations'][0], name='unrequested'))
        audit['master_declarations'].append(copy.deepcopy(audit['master_declarations'][0]))
        self.assertEqual(self.verify(selection=selection, audit=audit)['status'], 'matched')
        audit['master_declarations'].append(dict(audit['master_declarations'][0], name='unrequested'))
        self.assertEqual(self.verify(selection=selection, audit=audit)['status'], 'matched')
        audit['master_declarations'].append(dict(audit['master_declarations'][0], name='another_unrequested'))
        self.assert_mismatch(self.verify(selection=selection, audit=audit), 'audit_namespace_mismatch')

    def test_unrequested_declaration_changes_and_conflicting_physical_locations_are_rejected(self):
        selection = copy.deepcopy(self.selection)
        expected = dict(self.audit['master_declarations'][0], name='unrequested')
        selection['private']['master_declarations'].append(expected)
        for changes in ({'line': 91}, {'section': 'ss'}, {'path': str(self.root / 'elsewhere.scs')},
                        {'ports': ['g', 'd', 's', 'b']}, {'kind': 'inline_subckt'}):
            with self.subTest(changes=changes):
                audit = copy.deepcopy(self.audit)
                audit['master_declarations'].append(dict(expected, **changes))
                self.assert_mismatch(self.verify(selection=selection, audit=audit), 'audit_namespace_mismatch')
        audit = copy.deepcopy(self.audit)
        audit['master_declarations'].extend([dict(expected), dict(expected, line=91)])
        self.assert_mismatch(self.verify(selection=selection, audit=audit), 'audit_namespace_mismatch')
        audit['master_declarations'][-1] = dict(expected)
        self.assertEqual(self.verify(selection=selection, audit=audit)['status'], 'matched')

    def test_all_reported_declarations_need_an_expected_model_file_and_active_section(self):
        binary = next(row['path'] for row in self.audit['files'] if 'dynamic_library' in row['roles'])
        for changes in ({'path': binary, 'section': None},
                        {'path': str(self.root / 'unobserved.scs'), 'section': None},
                        {'section': 'inactive'}):
            audit = copy.deepcopy(self.audit)
            audit['master_declarations'][0].update(changes)
            self.assert_mismatch(self.verify(audit=audit), 'audit_declaration_scope_mismatch')
        # Global declarations remain active when the same model file is visited
        # through a named section, as both scanner and audit emitter implement.
        selection, audit = copy.deepcopy(self.selection), copy.deepcopy(self.audit)
        selection['private']['master_declarations'][0]['section'] = None
        audit['master_declarations'][0]['section'] = None
        self.assertEqual(self.verify(selection=selection, audit=audit)['status'], 'matched')

    def test_primitive_representation_alias_preserves_physical_identity(self):
        selection, audit = copy.deepcopy(self.selection), copy.deepcopy(self.audit)
        for rows in (selection['private']['master_declarations'], audit['master_declarations']):
            rows[0].update(kind='model', port_count=None, ports=[])
        for kind in ('primitive', 'primitive_model'):
            audit['master_declarations'][0]['kind'] = kind
            self.assertEqual(self.verify(selection=selection, audit=audit)['status'], 'matched')
        audit['master_declarations'][0]['line'] += 1
        self.assert_mismatch(self.verify(selection=selection, audit=audit), 'required_declaration_mismatch')

    def test_invalid_oversized_nonfinite_and_cyclic_audits_are_bounded_mismatches(self):
        cyclic = copy.deepcopy(self.audit)
        cyclic['cycle'] = cyclic
        malformed = [object(), [], {'schema': 'wrong'}, dict(self.audit, unknown_count=float('nan')), cyclic,
                     dict(self.audit, files=None), dict(self.audit, master_declarations=None)]
        for audit in malformed:
            self.assert_mismatch(self.verify(audit=audit), 'invalid_audit_evidence')
        with patch('spiceowl.onboarding.pdk_scope_audit.MAX_AUDIT_BYTES', 128):
            self.assert_mismatch(self.verify(), 'invalid_audit_evidence')


if __name__ == '__main__':
    unittest.main()

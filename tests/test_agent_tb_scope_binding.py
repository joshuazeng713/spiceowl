"""Synthetic scope-selection bindings are static identities, never run grants."""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from spiceowl.domain.common import DomainError
from spiceowl.experiments import prepare_experiment, validate_recipe
from spiceowl.onboarding.agent_tb import validate_agent_tb


DUT = ('simulator lang=spectre\nsubckt inverter inp out vdd vss\n'
       'XN (out inp vss vss) synthetic_n\n'
       'XP (out inp vdd vdd) synthetic_p\nends inverter\n')
TB = ('simulator lang=spectre\ninclude "pdk.scs"\ninclude "dut.scs"\n'
      'XINV (in out supply 0) inverter\n'
      'VDD (supply 0) vsource dc=1\n'
      'VIN (in 0) vsource type=pulse val0=0 val1=1 period=10n width=5n\n'
      'transient tran stop=20n maxstep=100p\nsave in out supply\n')
PDK = ('simulator lang=spectre\ninclude "@EXTERNAL_PDK_MAIN@" section=TT\n'
       'include "@EXTERNAL_PDK_MAIN@" section=Devices\n')
REQUIRED = {'scope_selection_id', 'library_id', 'sections', 'model_names', 'pdk_sha256'}
OPTIONAL_DIGESTS = {'entry_sha256', 'tmi_option_id', 'audit_sha256'}


def scope_binding():
    return {'scope_selection_id': 'a' * 64, 'library_id': 'b' * 64,
            'sections': ['TT', 'Devices'], 'model_names': ['synthetic_n', 'synthetic_p'],
            'pdk_sha256': hashlib.sha256(PDK.encode()).hexdigest()}


def legacy_binding():
    selected = scope_binding()
    selected.pop('scope_selection_id'); selected.pop('library_id')
    # Legacy identifiers deliberately need not be digests.
    selected.update(discovery_id='discovery_synthetic', candidate_id='candidate:legacy.1')
    return selected


def criteria():
    return {'schema': 'ic.agent_testbench.v1', 'text': TB,
            'interface': {'subckt': 'inverter', 'instance': 'XINV',
                          'port_bindings': {'inp': 'in', 'out': 'out', 'vdd': 'supply', 'vss': '0'}},
            'analyses': [{'name': 'transient', 'kind': 'tran'}],
            'stimuli': [{'instance': 'VIN', 'purpose': 'declared pulse'}],
            'objectives': ['Inspect the supplied synthetic inverter'],
            'criteria': {'output_signal': 'out'}, 'postprocess': {}}


def compile_inputs():
    spec = {'schema': 'spiceowl.experiment-spec.v1', 'recipe': 'agent-testbench',
            'corner': 'chosen', 'criteria': criteria(), 'pdk_binding': scope_binding()}
    sources = {'source/dut.scs': DUT.encode(), 'source/tb.scs': TB.encode(),
               'source/pdk.scs': PDK.encode()}
    selected = {'mode': 'agent-tb', 'source_id': 'source/dut.scs',
                'testbench_id': 'source/tb.scs', 'pdk_id': 'source/pdk.scs', 'support_files': {}}
    profile = {'schema': 'spiceowl.experiment-model-profile.v1', 'id': 'synthetic', 'models': {},
               'corners': {'chosen': [{'alias': 'PDK_MAIN', 'section': s} for s in ['TT', 'Devices']]}}
    return sources, selected, spec, profile


def header_fixture():
    support = {'behavior/buffer.va': (
        '`include "constants.vams"\n`include "disciplines.vams"\n'
        'module declared_buffer(inp, out, ret);\ninput inp; output out; inout ret;\n'
        'electrical inp,out,ret;\nanalog begin\nV(out,ret) <+ V(inp,ret);\nend\nendmodule\n')}
    selected = scope_binding()
    headers = [{'name': name, 'alias': alias, 'sha256': digest * 64}
               for name, alias, digest in [('constants.vams', 'VA_CONSTANTS', 'c'),
                                          ('disciplines.vams', 'VA_DISCIPLINES', 'd')]]
    selected['veriloga_standard_includes'] = headers
    selected['veriloga_header_closure'] = {'inventory_sha256': 'e' * 64,
        'files': copy.deepcopy(headers), 'includes': []}
    return DUT + 'ahdl_include "behavior/buffer.va"\n', support, selected


class AgentTbScopeBindingTests(unittest.TestCase):
    def validate(self, selected=None, **changes):
        args = {'source': DUT, 'testbench': TB, 'criteria': criteria(),
                'pdk_binding': scope_binding() if selected is None else selected}
        args.update(changes)
        return validate_agent_tb(**args)

    def test_minimal_scope_identity_preserved_without_authorizing_execution(self):
        selected = scope_binding(); before = copy.deepcopy(selected)
        with patch('builtins.open', side_effect=AssertionError('No filesystem IO')), \
             patch('subprocess.run', side_effect=AssertionError('No process launch')), \
             patch('socket.create_connection', side_effect=AssertionError('No network IO')):
            checked = self.validate(selected)
        self.assertEqual(checked['status'], 'static_ready', checked['missing'])
        self.assertEqual(checked['manifest']['pdk_binding'], before)
        self.assertEqual(selected, before)
        self.assertFalse(checked['execution_authorized'])
        self.assertFalse(checked['manifest']['simulation_verified'])
        self.assertTrue(checked['manifest']['human_review_required'])
        selected['sections'].append('other')
        self.assertEqual(checked['manifest']['pdk_binding']['sections'], ['TT', 'Devices'])

    def test_optional_digests_remain_identity_data_without_proving_an_audit(self):
        selected = {**scope_binding(), **{key: 'c' * 64 for key in OPTIONAL_DIGESTS}}
        checked = self.validate(selected)
        self.assertEqual(checked['status'], 'static_ready')
        self.assertEqual(checked['manifest']['pdk_binding'], selected)
        self.assertFalse(checked['execution_authorized'])
        self.assertFalse(checked['manifest']['simulation_verified'])
        self.assertTrue(checked['manifest']['human_review_required'])

    def test_every_required_field_is_mandatory_and_nonobjects_are_rejected(self):
        for key in REQUIRED:
            selected = scope_binding(); selected.pop(key)
            with self.subTest(field=key), self.assertRaises(DomainError):
                self.validate(selected)
        for value in (None, [], '', True, 1):
            with self.subTest(value=value), self.assertRaises(DomainError):
                self.validate(pdk_binding=value)

    def test_scope_and_legacy_identity_fields_are_mutually_exclusive(self):
        for key in ('discovery_id', 'candidate_id', 'tmi_candidate_id'):
            for value in ('legacy', 'f' * 64, None):
                with self.subTest(field=key, value=value), self.assertRaises(DomainError):
                    self.validate({**scope_binding(), key: value})
        for key in ('scope_selection_id', 'library_id', 'tmi_option_id'):
            with self.subTest(field=key), self.assertRaises(DomainError):
                self.validate({**legacy_binding(), key: 'f' * 64})
        with self.assertRaises(DomainError):
            self.validate({**scope_binding(), **legacy_binding()})

    def test_all_scope_digest_fields_require_lowercase_64_hex_values(self):
        fields = {'scope_selection_id', 'library_id', 'pdk_sha256'} | OPTIONAL_DIGESTS
        for key in fields:
            for value in ('f' * 63, 'f' * 65, 'A' * 64, 'g' * 64, 'opaque-id',
                          'f' * 64 + '\n', None, 0, True, [], {}):
                with self.subTest(field=key, value=value), self.assertRaises(DomainError):
                    self.validate({**scope_binding(), key: value})

    def test_raw_paths_and_authorization_fields_cannot_be_smuggled_into_binding(self):
        for key, value in [('path', '/private/pdk.scs'), ('main', '/private/pdk.scs'),
                           ('approved', True), ('execution_authorized', True),
                           ('ready_to_run', True), ('command', 'spectre')]:
            with self.subTest(field=key), self.assertRaises(DomainError):
                self.validate({**scope_binding(), key: value})

    def test_existing_section_and_model_list_rules_apply_to_both_branches(self):
        changes = [('sections', []), ('sections', ['TT', 'TT']), ('sections', [None, None]),
                   ('sections', ['/private/TT']), ('sections', ['TT;run']), ('sections', [1]),
                   ('sections', ('TT',)), ('sections', ['s%d' % i for i in range(513)]),
                   ('model_names', [None]), ('model_names', ['synthetic_n', 'synthetic_n']),
                   ('model_names', ['n%d' % i for i in range(513)]), ('model_names', {})]
        for factory in (scope_binding, legacy_binding):
            for key, value in changes:
                with self.subTest(branch=factory.__name__, field=key, value=value), self.assertRaises(DomainError):
                    self.validate({**factory(), key: value})
            value = factory(); value['sections'] = [None, 'TT']
            self.assertEqual(self.validate(value)['manifest']['pdk_binding']['sections'], [None, 'TT'])
            value['sections'] = ['s%d' % i for i in range(512)]
            self.assertEqual(self.validate(value)['status'], 'static_ready')

    def test_missing_source_models_stay_blocked_for_scope_selection(self):
        for names in ([], ['synthetic_n']):
            checked = self.validate({**scope_binding(), 'model_names': names})
            self.assertEqual(checked['status'], 'blocked')
            missing = {row['master'] for row in checked['missing'] if row['code'] == 'pdk_model_binding_missing'}
            self.assertEqual(missing, {'synthetic_n', 'synthetic_p'} - set(names))
            self.assertFalse(checked['execution_authorized'])

    def test_changing_any_scope_identity_changes_binding_and_manifest_hashes(self):
        selected = {**scope_binding(), **{key: 'c' * 64 for key in OPTIONAL_DIGESTS}}
        baseline = self.validate(selected)['manifest']
        changes = {key: 'f' * 64 for key in {'scope_selection_id', 'library_id', 'pdk_sha256'} | OPTIONAL_DIGESTS}
        changes.update(sections=['Devices', 'TT'], model_names=['synthetic_n', 'synthetic_p', 'extra'])
        for key, value in changes.items():
            with self.subTest(field=key):
                checked = self.validate({**selected, key: value})['manifest']
                self.assertNotEqual(checked['pdk_binding_sha256'], baseline['pdk_binding_sha256'])
                self.assertNotEqual(checked['manifest_sha256'], baseline['manifest_sha256'])
                self.assertEqual(checked['source_sha256'], baseline['source_sha256'])
                self.assertEqual(checked['tb_sha256'], baseline['tb_sha256'])

    def test_legacy_opaque_ids_and_nullable_tmi_remain_compatible(self):
        for tmi in (None, 'observed:legacy_TMI.1'):
            selected = {**legacy_binding(), 'tmi_candidate_id': tmi,
                        'entry_sha256': 'c' * 64, 'audit_sha256': 'd' * 64}
            checked = self.validate(selected)
            self.assertEqual(checked['status'], 'static_ready')
            self.assertEqual(checked['manifest']['pdk_binding'], selected)
            expected = hashlib.sha256(json.dumps(selected, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            self.assertEqual(checked['manifest']['pdk_binding_sha256'], expected)
            self.assertNotIn('scope_selection_id', checked['manifest']['pdk_binding'])

    def test_registered_recipe_preserves_scope_identity_and_checks_managed_pdk_bytes(self):
        sources, selected, spec, profile = compile_inputs()
        checked = prepare_experiment(sources, selected, spec, profile)
        self.assertEqual(checked['status'], 'ready', checked['missing'])
        self.assertEqual(validate_recipe(checked['recipe']), spec)
        self.assertEqual(checked['recipe']['agent_tb_manifest']['pdk_binding'], scope_binding())
        self.assertEqual(checked['files']['netlist/pdk.scs'], PDK.encode())
        sources['source/pdk.scs'] += b'\n'
        broken = prepare_experiment(sources, selected, spec, profile)
        self.assertEqual(broken['status'], 'missing')
        self.assertEqual(broken['missing'][0]['code'], 'managed_pdk_digest_mismatch')

    def test_scope_branch_preserves_veriloga_standard_headers_and_closure(self):
        source, support, selected = header_fixture()
        checked = self.validate(selected, source=source, support_files=support)
        self.assertEqual(checked['status'], 'static_ready', checked['missing'])
        self.assertEqual(checked['manifest']['pdk_binding'], selected)
        self.assertEqual(checked['manifest']['veriloga_header_closure'], selected['veriloga_header_closure'])
        self.assertFalse(checked['execution_authorized'])
        self.assertTrue(all(row['verification'] == 'requires_runner_dependency_audit'
                            for row in checked['manifest']['veriloga_external_dependencies']))
        for field, code in [('veriloga_header_closure', 'veriloga_header_closure_missing'),
                            ('veriloga_standard_includes', 'veriloga_standard_include_binding_missing')]:
            missing = copy.deepcopy(selected); missing.pop(field)
            result = self.validate(missing, source=source, support_files=support)
            self.assertEqual(result['status'], 'blocked')
            self.assertIn(code, {row['code'] for row in result['missing']})

    def test_scope_veriloga_binding_rejects_paths_and_mismatched_header_hashes(self):
        source, support, selected = header_fixture()
        for field in ('veriloga_standard_includes', 'veriloga_header_closure'):
            wrong = copy.deepcopy(selected)
            rows = wrong[field] if field == 'veriloga_standard_includes' else wrong[field]['files']
            rows[0]['name'] = '/private/constants.vams'
            with self.subTest(field=field), self.assertRaises(DomainError):
                self.validate(wrong, source=source, support_files=support)
        wrong = copy.deepcopy(selected)
        wrong['veriloga_header_closure']['files'][0]['sha256'] = 'f' * 64
        checked = self.validate(wrong, source=source, support_files=support)
        self.assertEqual(checked['status'], 'blocked')
        self.assertIn('veriloga_standard_header_closure_mismatch', {row['code'] for row in checked['missing']})


if __name__ == '__main__':
    unittest.main()

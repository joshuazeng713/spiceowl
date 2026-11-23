"""Original generic TB fixtures; no PDK, SSH, filesystem reads or simulator."""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from spiceowl.domain.common import DomainError
from spiceowl.onboarding.agent_tb import inspect_agent_source, validate_agent_tb
from spiceowl.onboarding.pdk_discovery import inspect_netlist
from spiceowl.experiments import prepare_experiment, validate_recipe, measure_psf_ascii


DUT = ('// exact original hierarchy\r\nsimulator lang=spectre\r\n'
       'include "support/leaf.scs"\r\n'
       'subckt amplifier (inp inn out vdd vss bias)\r\n'
       'Xleft (inp out vdd bias) leaf\r\nXright (inn out vss bias) leaf\r\n'
       'Rload (out vss) resistor r=2k\r\nends amplifier\r\n')
LEAF = ('subckt leaf (a z rail bias)\n'
        'Q0 (a z rail bias) unknown_fourport\n'
        'Z0 z bias rail custom_threeport strength=2\nends leaf\n')
TB = ('simulator lang=spectre\ninclude "pdk.scs"\ninclude "dut.scs"\n'
      'Xamp (in_p in_n out supply 0 bias) amplifier\n'
      'VDD (supply 0) vsource dc=1.2\nVB (bias 0) vsource dc=0.6\n'
      'VIP (in_p 0) vsource type=sine dc=0.6 ampl=1m freq=10M\n'
      'VIN (in_n 0) vsource dc=0.6\n'
      'transient tran stop=100n maxstep=1n\nfrequency ac start=1 stop=1G dec=20\n'
      'transfer dc dev=VIP param=dc start=0 stop=1.2 step=10m\n'
      'save in_p in_n out supply bias\n')
PDK = 'simulator lang=spectre\ninclude "@EXTERNAL_PDK_MAIN@" section=Chosen\n'


def binding():
    return {'discovery_id': 'discovery_synthetic', 'candidate_id': 'a' * 64,
            'sections': ['Chosen'], 'model_names': ['unknown_fourport', 'custom_threeport'],
            'pdk_sha256': hashlib.sha256(PDK.encode()).hexdigest()}


def draft():
    return {'schema': 'ic.agent_testbench.v1', 'text': TB,
        'interface': {'subckt': 'amplifier', 'instance': 'Xamp',
            'port_bindings': {'inp': 'in_p', 'inn': 'in_n', 'out': 'out', 'vdd': 'supply', 'vss': '0', 'bias': 'bias'}},
        'analyses': [{'name': 'frequency', 'kind': 'ac'}], 'stimuli': [{'instance': 'VIP', 'purpose': 'small signal'}],
        'objectives': ['Characterize the supplied amplifier'], 'criteria': {'gain_db': {'minimum': 20}},
        'postprocess': {'language': 'python', 'source': 'print("authored data only")'}}


def compile_inputs():
    criteria = draft()
    sources = {'source/dut.scs': DUT.encode(), 'source/tb.scs': TB.encode(),
               'source/pdk.scs': PDK.encode(), 'source/leaf.scs': LEAF.encode(),
               'source/draft.json': (json.dumps(criteria, indent=3) + '\n').encode()}
    selection = {'mode': 'agent-tb', 'source_id': 'source/dut.scs', 'testbench_id': 'source/tb.scs',
        'pdk_id': 'source/pdk.scs', 'manifest_id': 'source/draft.json',
        'support_files': {'support/leaf.scs': 'source/leaf.scs'}}
    spec = {'schema': 'spiceowl.experiment-spec.v1', 'recipe': 'agent-testbench', 'corner': 'chosen',
            'criteria': criteria, 'pdk_binding': binding()}
    profile = {'schema': 'spiceowl.experiment-model-profile.v1', 'id': 'synthetic', 'models': {},
               'corners': {'chosen': [{'alias': 'PDK_MAIN', 'section': 'Chosen'}]}}
    return sources, selection, spec, profile


class AgentTestbenchTests(unittest.TestCase):
    def validate(self, **changes):
        args = {'source': DUT, 'testbench': TB, 'support_files': {'support/leaf.scs': LEAF},
                'criteria': draft(), 'pdk_binding': binding()}
        args.update(changes)
        if 'testbench' in changes and 'criteria' not in changes:
            args['criteria']['text'] = changes['testbench']
        return validate_agent_tb(**args)

    def test_generic_hierarchy_and_non_mos_models_without_inverter_constraints(self):
        with patch('builtins.open', side_effect=AssertionError('No filesystem IO')):
            requirements = inspect_netlist(DUT, mode='agent-tb', support_files={'support/leaf.scs': LEAF})
            result = self.validate()
        self.assertEqual(requirements['required_masters'], ['custom_threeport', 'unknown_fourport'])
        self.assertEqual(requirements['mode'], 'agent-tb')
        self.assertEqual(result['status'], 'static_ready', result['missing'])
        self.assertEqual({row['kind'] for row in result['analyses']}, {'tran', 'ac', 'dc'})
        self.assertTrue(all(row['polarity'] is None for row in requirements['instances']))
        self.assertFalse(result['execution_authorized'])
        self.assertEqual(result['manifest']['source_sha256'], hashlib.sha256(DUT.encode()).hexdigest())
        self.assertEqual(result['manifest']['tb_sha256'], hashlib.sha256(TB.encode()).hexdigest())

    def test_exact_text_files_and_full_criteria_change_manifest_identity(self):
        original = self.validate()['manifest']
        changed = self.validate(testbench=TB + '\n')['manifest']
        self.assertNotEqual(original['tb_sha256'], changed['tb_sha256'])
        criteria = draft()
        criteria['postprocess']['source'] += '\n# new authored program'
        self.assertNotEqual(original['criteria_sha256'], self.validate(criteria=criteria)['manifest']['criteria_sha256'])
        self.assertEqual(original['source_sha256'], changed['source_sha256'])

    def test_external_dynamic_ahdl_and_script_paths_are_explicitly_blocked(self):
        statements = ['include "/etc/passwd"', 'include "../outside.scs"', 'include "$MODEL/a.scs"',
                      'include "missing.scs"', 'ahdl_include "behavior.va"', 'shell rm -rf anything',
                      'filetran tran stop=1n write="/tmp/output"', 'bad options script=run',
                      'B0 (out 0) bsource v=system(1)', 'simulator lang=spice']
        for text in statements:
            with self.subTest(text=text):
                result = self.validate(testbench=TB + text + '\n')
                self.assertEqual(result['status'], 'blocked')
                self.assertTrue(result['missing'])

    def test_missing_save_analysis_model_or_dut_does_not_become_ready(self):
        cases = [TB.replace('save in_p in_n out supply bias\n', ''),
                 '\n'.join(line for line in TB.splitlines() if not any(' ' + kind + ' ' in line for kind in ('tran', 'ac', 'dc'))),
                 TB.replace('include "dut.scs"\n', '')]
        for text in cases:
            self.assertEqual(self.validate(testbench=text)['status'], 'blocked')
        selected = binding()
        selected['model_names'] = ['unknown_fourport']
        self.assertIn('pdk_model_binding_missing', {r['code'] for r in self.validate(pdk_binding=selected)['missing']})

    def test_relative_support_cycle_and_wrong_subcircuit_arity_are_blocked(self):
        result = self.validate(support_files={'support/leaf.scs': LEAF + 'include "leaf.scs"\n'})
        self.assertIn('include_cycle', {r['code'] for r in result['missing']})
        result = self.validate(testbench=TB.replace('(in_p in_n out supply 0 bias)', '(in_p in_n out supply 0)'))
        self.assertIn('subcircuit_port_count_mismatch', {r['code'] for r in result['missing']})
        scoped = DUT.replace('include "support/leaf.scs"\r\n', '').replace(
            'subckt amplifier (inp inn out vdd vss bias)\r\n',
            'subckt amplifier (inp inn out vdd vss bias)\r\ninclude "support/leaf.scs"\r\n')
        self.assertIn('scoped_include_unsupported', {r['code'] for r in self.validate(source=scoped)['missing']})

    def test_recursive_hierarchy_ambiguous_symbols_and_unreferenced_files_are_blocked(self):
        recursive = LEAF.replace('Q0 (a z rail bias) unknown_fourport', 'Xagain (a z rail bias) leaf')
        result = self.validate(support_files={'support/leaf.scs': recursive})
        self.assertIn('recursive_subcircuit_hierarchy', {r['code'] for r in result['missing']})
        result = self.validate(support_files={'support/leaf.scs': LEAF + LEAF})
        self.assertIn('ambiguous_declaration', {r['code'] for r in result['missing']})
        result = self.validate(support_files={'support/leaf.scs': LEAF, 'unused.py': 'import os\n'})
        self.assertIn('unreferenced_supplied_file', {r['code'] for r in result['missing']})

    def test_authored_interface_is_checked_without_inventing_a_wiring_map(self):
        criteria = draft()
        criteria['interface']['port_bindings']['out'] = 'different_output'
        result = self.validate(criteria=criteria)
        self.assertIn('authored_port_bindings_mismatch', {r['code'] for r in result['missing']})
        criteria['interface']['instance'] = 'Xnot_present'
        self.assertIn('authored_interface_not_resolved', {r['code'] for r in self.validate(criteria=criteria)['missing']})
        criteria = draft()
        criteria['text'] += '\n'
        self.assertIn('authored_testbench_mismatch', {r['code'] for r in self.validate(criteria=criteria)['missing']})

    def test_generic_inventory_accepts_more_than_128_devices_and_arbitrary_port_roles(self):
        source = 'subckt network (a b c d e f g)\n' + ''.join(
            'I%d (a b c d e f g) seven_terminal_model scale=1\n' % n for n in range(200)) + 'ends network\n'
        result = inspect_agent_source(source)
        self.assertEqual(result['status'], 'parsed')
        self.assertEqual(len(result['instances']), 200)
        self.assertEqual(result['required_masters'], ['seven_terminal_model'])
        self.assertTrue(all(row['polarity'] is None and row['port_count'] == 7 for row in result['instances']))

    def test_safe_math_and_literal_pwl_are_static_data_but_malformed_expressions_are_blocked(self):
        text = TB + 'Bdynamic (out 0) bsource v=sin(2*3.14*1M)*v(in_p)\nVPWL (aux 0) vsource type=pwl wave=[0 0 1n 1 2n 0]\n'
        self.assertEqual(self.validate(testbench=text)['status'], 'static_ready')
        for value in ('1 2', 'sqrt(', 'getenv(1)', '1e999'):
            self.assertEqual(self.validate(testbench=TB + 'bad options temp=' + value + '\n')['status'], 'blocked')

    def test_flat_authored_dut_is_supported_without_inventing_a_subcircuit(self):
        source = 'R1 (in out) resistor r=1k\nC1 (out 0) capacitor c=1p\n'
        tb = 'include "dut.scs"\nV1 (in 0) vsource type=pulse val0=0 val1=1\nt tran stop=10n\nsave in out\n'
        criteria = draft()
        criteria.update(text=tb, interface={'subckt': None, 'instance': None, 'port_bindings': {'in': 'in', 'out': 'out'}})
        selected = binding()
        selected['model_names'] = []
        result = validate_agent_tb(source=source, testbench=tb, criteria=criteria, pdk_binding=selected)
        self.assertEqual(result['status'], 'static_ready', result['missing'])
        self.assertEqual(result['requirements']['required_masters'], [])

    def test_support_files_and_binding_never_request_filesystem_paths(self):
        for files in ({'/outside.scs': LEAF}, {'../escape.scs': LEAF}, {'pdk.scs': LEAF}, {'tb.scs': LEAF}):
            with self.subTest(files=list(files)), self.assertRaises(DomainError):
                self.validate(support_files=files)
        for change in ({'pdk_sha256': 'wrong'}, {'sections': [{}]}, {'extra_command': 'run'}):
            with self.subTest(change=change), self.assertRaises(DomainError):
                self.validate(pdk_binding={**binding(), **change})

    def test_original_inspector_default_is_unchanged(self):
        source = 'M1 (d g s b) core_n w=1u l=1u\n'
        old = inspect_netlist(source)
        self.assertNotIn('mode', old)
        self.assertEqual(old['required_masters'], ['core_n'])
        self.assertEqual(inspect_agent_source(source)['required_masters'], ['core_n'])

    def test_registered_recipe_preserves_all_authored_bytes_and_managed_binding(self):
        sources, selection, spec, profile = compile_inputs()
        with patch('builtins.open', side_effect=AssertionError('No filesystem IO')):
            compiled = prepare_experiment(sources, selection, spec, profile)
        self.assertEqual(compiled['status'], 'ready', compiled['missing'])
        self.assertEqual(compiled['entrypoint'], 'netlist/tb.scs')
        self.assertEqual(compiled['files']['netlist/dut.scs'], DUT.encode())
        self.assertEqual(compiled['files']['netlist/tb.scs'], TB.encode())
        self.assertEqual(compiled['files']['netlist/support/leaf.scs'], LEAF.encode())
        self.assertEqual(compiled['files']['experiment/authored-manifest.json'], sources['source/draft.json'])
        self.assertEqual(compiled['recipe']['used_models'], ['custom_threeport', 'unknown_fourport'])
        self.assertEqual(validate_recipe(compiled['recipe']), spec)
        self.assertNotIn('approved', compiled)
        measured = measure_psf_ascii('', compiled['recipe'], execution_state='completed', exit_code=0)
        self.assertEqual(measured['data_validity'], 'unknown')
        self.assertEqual(measured['specification'], 'unknown')
        self.assertEqual(measured['missing'][0]['code'], 'agent_testbench_measurement_not_implemented')

    def test_registered_recipe_blocks_changed_draft_pdk_wrapper_and_hidden_sources(self):
        for change in ('draft', 'pdk', 'extra', 'manifest'):
            with self.subTest(change=change):
                sources, selection, spec, profile = compile_inputs()
                if change == 'draft':
                    sources['source/tb.scs'] += b'\n'
                elif change == 'pdk':
                    sources['source/pdk.scs'] += b'\n'
                elif change == 'extra':
                    sources['source/hidden.scs'] = b'parameters x=1\n'
                else:
                    sources['source/draft.json'] = b'{}\n'
                self.assertEqual(prepare_experiment(sources, selection, spec, profile)['status'], 'missing')

    def test_registered_recipe_rejects_non_alias_managed_wrapper_even_with_matching_hash(self):
        sources, selection, spec, profile = compile_inputs()
        sources['source/pdk.scs'] = b'include "/private/models.scs"\n'
        spec['pdk_binding']['pdk_sha256'] = hashlib.sha256(sources['source/pdk.scs']).hexdigest()
        result = prepare_experiment(sources, selection, spec, profile)
        self.assertEqual(result['status'], 'missing')
        self.assertEqual(result['missing'][0]['code'], 'unsupported_managed_pdk')

    def test_dc_operating_point_info_request_is_hash_bound_output_not_an_analysis(self):
        tb = '\n'.join(line for line in TB.splitlines() if ' tran ' not in line and ' ac ' not in line)
        tb += '\ndcOpInfo info what=oppoint where=rawfile\n'
        checked = self.validate(testbench=tb)
        self.assertEqual(checked['status'], 'static_ready', checked['missing'])
        self.assertEqual([row['kind'] for row in checked['analyses']], ['dc'])
        expected = [{'name': 'dcOpInfo', 'what': 'oppoint', 'where': 'rawfile',
                     'path': 'tb.scs', 'line': len(tb.splitlines())}]
        self.assertEqual(checked['info_requests'], expected)
        self.assertEqual(checked['requirements']['info_requests'], expected)
        self.assertEqual(checked['manifest']['info_requests'], expected)
        sources, selection, spec, profile = compile_inputs()
        sources['source/tb.scs'] = tb.encode()
        spec['criteria']['text'] = tb
        sources['source/draft.json'] = json.dumps(spec['criteria']).encode()
        compiled = prepare_experiment(sources, selection, spec, profile)
        self.assertEqual(compiled['status'], 'ready', compiled['missing'])
        self.assertEqual(compiled['files']['netlist/tb.scs'], tb.encode())
        self.assertEqual(compiled['recipe']['info_requests'], expected)
        self.assertEqual(validate_recipe(compiled['recipe']), spec)

    def test_info_request_alone_cannot_supply_required_analysis_or_save(self):
        no_analysis = '\n'.join(line for line in TB.splitlines()
                                if not any(' ' + kind + ' ' in line for kind in ('tran', 'ac', 'dc')))
        checked = self.validate(testbench=no_analysis + '\ndcOpInfo info what=oppoint where=rawfile\n')
        self.assertEqual(checked['status'], 'blocked')
        self.assertEqual(checked['analyses'], [])
        self.assertIn('supported_analysis_missing', {row['code'] for row in checked['missing']})
        checked = self.validate(testbench=TB.replace('save in_p in_n out supply bias\n', '') +
                                'dcOpInfo info what=oppoint where=rawfile\n')
        self.assertIn('explicit_save_missing', {row['code'] for row in checked['missing']})

    def test_info_output_parameters_names_and_scope_are_restricted(self):
        invalid = ['dcOpInfo info what=models where=rawfile',
                   'dcOpInfo info what=oppoint where=file',
                   'dcOpInfo info what=oppoint where="/tmp/output"',
                   'dcOpInfo info what=oppoint where=rawfile filename="output"',
                   'dcOpInfo info what=oppoint where=rawfile file="/tmp/output"',
                   'dcOpInfo info what=oppoint where=rawfile path=output',
                   '../dcOpInfo info what=oppoint where=rawfile',
                   'dcOpInfo info where=rawfile']
        for statement in invalid:
            with self.subTest(statement=statement):
                checked = self.validate(testbench=TB + statement + '\n')
                self.assertEqual(checked['status'], 'blocked')
                self.assertFalse(checked['info_requests'])
        for statement in ('dcOpInfo info what=oppoint where=rawfile\n' * 2,
                          'transfer info what=oppoint where=rawfile\n'):
            checked = self.validate(testbench=TB + statement)
            self.assertIn('ambiguous_info_request_name', {row['code'] for row in checked['missing']})
        source = DUT.replace('Xleft (', 'dcOpInfo info what=oppoint where=rawfile\r\nXleft (')
        checked = self.validate(source=source)
        self.assertIn('unsupported_info_request_scope', {row['code'] for row in checked['missing']})


if __name__ == '__main__':
    unittest.main()

"""Original behavioral fixtures and declared synthetic headers; no compiler or IO."""
import copy
import hashlib
import unittest
from unittest.mock import patch

from spiceowl.domain.common import DomainError
from spiceowl.onboarding.agent_tb import inspect_agent_source, validate_agent_tb
from spiceowl.experiments import prepare_experiment, validate_recipe
from spiceowl.experiments.source import ExperimentIssue
from tests.test_agent_tb import PDK, compile_inputs


DUT = ('simulator lang=spectre\nahdl_include "behavior/library.va"\n'
       'subckt sampled (inp clk out ret)\n'
       'XS (inp clk out ret) sample_hold\nends sampled\n')
TB = ('simulator lang=spectre\ninclude "pdk.scs"\ninclude "dut.scs"\n'
      'Xsample (vin clock vout 0) sampled\n'
      'VIN (vin 0) vsource dc=0.4\nVCLK (clock 0) vsource dc=0\n'
      't tran stop=100n maxstep=1n\nsave vin clock vout\n')
LIBRARY = ('// Original synthetic library; these are references, not header contents.\r\n'
           '`include "constants.vams"\r\n`include "disciplines.vams"\r\n'
           '`include "cells/sample.va"\r\n'
           'module ideal_clock(clk, ret);\r\noutput clk; inout ret; electrical clk,ret;\r\n'
           'parameter real period=10n from (0:inf);\r\ninteger level;\r\n'
           'analog begin\r\n@(initial_step) level=0;\r\n'
           '@(timer(0,period/2)) level=1-level;\r\n'
           'V(clk,ret) <+ transition(level,0,1p);\r\nend\r\nendmodule\r\n')
SAMPLE = ('module sample_hold(inp, clk, out, ret);\ninput inp,clk; output out; inout ret;\n'
          'electrical inp,clk,out,ret;\nparameter real threshold=0.5;\nreal held;\n'
          'analog begin\n@(initial_step) held=0;\n'
          '@(cross(V(clk,ret)-threshold,+1)) held=V(inp,ret);\n'
          'if (held>1) held=1; else if (held<0) held=0;\n'
          'V(out,ret) <+ transition(held,0,1n);\nI(inp,ret) <+ 0;\n'
          'end\nendmodule\n')


def inputs():
    sources, selection, spec, profile = compile_inputs()
    sources = {'source/dut.scs': DUT.encode(), 'source/tb.scs': TB.encode(),
               'source/pdk.scs': PDK.encode(), 'source/library.va': LIBRARY.encode(),
               'source/sample.va': SAMPLE.encode()}
    selection.pop('manifest_id')
    selection['support_files'] = {'behavior/library.va': 'source/library.va',
                                 'behavior/cells/sample.va': 'source/sample.va'}
    spec['criteria'].update(text=TB, interface={'subckt': 'sampled', 'instance': 'Xsample',
        'port_bindings': {'inp': 'vin', 'clk': 'clock', 'out': 'vout', 'ret': '0'}})
    spec['pdk_binding']['model_names'] = []
    spec['pdk_binding']['veriloga_standard_includes'] = [
        {'name': name, 'alias': alias, 'sha256': hashlib.sha256(('synthetic declaration ' + name).encode()).hexdigest()}
        for name, alias in [('constants.vams', 'VA_CONSTANTS'), ('disciplines.vams', 'VA_DISCIPLINES')]]
    spec['pdk_binding']['veriloga_header_closure'] = {'inventory_sha256': 'c' * 64,
        'files': copy.deepcopy(spec['pdk_binding']['veriloga_standard_includes']), 'includes': []}
    return sources, selection, spec, profile


class VerilogATests(unittest.TestCase):
    def validate(self, *, source=DUT, testbench=TB, library=LIBRARY, sample=SAMPLE,
                 binding=None, extra_support=None):
        _, _, spec, _ = inputs()
        criteria = spec['criteria']
        criteria['text'] = testbench
        return validate_agent_tb(source=source, testbench=testbench, criteria=criteria,
            support_files={'behavior/library.va': library, 'behavior/cells/sample.va': sample,
                           **(extra_support or {})},
            pdk_binding=spec['pdk_binding'] if binding is None else binding)

    def test_original_scalar_modules_nested_includes_are_static_review_material(self):
        with patch('builtins.open', side_effect=AssertionError('no file access')):
            checked = self.validate()
            inventory = inspect_agent_source(DUT, support_files={
                'behavior/library.va': LIBRARY, 'behavior/cells/sample.va': SAMPLE})
        self.assertEqual(checked['status'], 'static_ready', checked['missing'])
        self.assertEqual(inventory['status'], 'parsed', inventory['issues'])
        self.assertEqual(inventory['required_masters'], [])
        self.assertEqual({m['name'] for m in inventory['veriloga_modules']}, {'sample_hold', 'ideal_clock'})
        self.assertFalse(checked['execution_authorized'])
        self.assertTrue(checked['manifest']['human_review_required'])
        self.assertFalse(checked['manifest']['simulation_verified'])
        deps = checked['manifest']['veriloga_external_dependencies']
        self.assertEqual({d['reference'] for d in deps}, {'constants.vams', 'disciplines.vams'})
        self.assertTrue(all(d['verification'] == 'requires_runner_dependency_audit' for d in deps))
        self.assertTrue(all(d['binding']['sha256'] for d in deps))

    def test_missing_standard_binding_and_unrelated_bindings_are_blocked(self):
        _, _, spec, _ = inputs()
        unbound = copy.deepcopy(spec['pdk_binding'])
        unbound.pop('veriloga_standard_includes')
        r = self.validate(binding=unbound)
        self.assertEqual(r['status'], 'blocked')
        self.assertEqual(sum(x['code'] == 'veriloga_standard_include_binding_missing' for x in r['missing']), 2)
        r = self.validate(library=LIBRARY.replace('`include "constants.vams"\r\n', ''))
        self.assertIn('veriloga_standard_include_binding_unused', {x['code'] for x in r['missing']})

    def test_known_constant_and_time_queries_require_explicit_header(self):
        sample = SAMPLE.replace('I(inp,ret) <+ 0;', 'I(inp,ret) <+ 1n*sin(`M_TWO_PI*1M*$abstime);')
        self.assertEqual(self.validate(sample=sample)['status'], 'static_ready')
        r = self.validate(sample=sample, library=LIBRARY.replace('`include "constants.vams"\r\n', ''))
        self.assertIn('veriloga_constants_dependency_missing', {x['code'] for x in r['missing']})

    def test_module_arity_and_authored_port_map_are_checked(self):
        r = self.validate(source=DUT.replace('(inp clk out ret) sample_hold', '(inp clk out) sample_hold'))
        self.assertIn('veriloga_module_port_count_mismatch', {x['code'] for x in r['missing']})
        # A directly selected VA module also uses its declared port order.
        source = 'ahdl_include "behavior/library.va"\n'
        tb = TB.replace('Xsample (vin clock vout 0) sampled', 'Xsample (vin clock vout 0) sample_hold')
        _, _, spec, _ = inputs()
        spec['criteria']['text'] = tb
        spec['criteria']['interface']['subckt'] = 'sample_hold'
        r = validate_agent_tb(source=source, testbench=tb,
            support_files={'behavior/library.va': LIBRARY, 'behavior/cells/sample.va': SAMPLE},
            criteria=spec['criteria'], pdk_binding=spec['pdk_binding'])
        self.assertEqual(r['status'], 'static_ready', r['missing'])
        spec['criteria']['interface']['port_bindings']['out'] = 'wrong'
        r = validate_agent_tb(source=source, testbench=tb,
            support_files={'behavior/library.va': LIBRARY, 'behavior/cells/sample.va': SAMPLE},
            criteria=spec['criteria'], pdk_binding=spec['pdk_binding'])
        self.assertIn('authored_port_bindings_mismatch', {x['code'] for x in r['missing']})

    def test_relative_va_closure_cycle_missing_and_unused_files(self):
        for library, code in [(LIBRARY.replace('cells/sample.va', 'missing.va'), 'support_file_missing'),
                              (LIBRARY + '`include "library.va"\n', 'include_cycle')]:
            r = self.validate(library=library)
            self.assertEqual(r['status'], 'blocked')
            self.assertIn(code, {x['code'] for x in r['missing']})
        r = self.validate(extra_support={'unused.va': SAMPLE})
        self.assertIn('unreferenced_supplied_file', {x['code'] for x in r['missing']})
        r = self.validate(library=LIBRARY + SAMPLE)
        self.assertIn('ambiguous_declaration', {x['code'] for x in r['missing']})

    def test_absolute_parent_dynamic_preprocessor_and_wrong_language_rejected(self):
        for reference in ('/outside.va', '../outside.va', '$HOME/a.va', '@ALIAS@', 'other.scs'):
            r = self.validate(library=LIBRARY.replace('cells/sample.va', reference))
            self.assertEqual(r['status'], 'blocked', reference)
        for prefix in ('`ifdef ENABLE\n', '`define SHELL system\n', '`include HEADER\n'):
            self.assertEqual(self.validate(sample=prefix + SAMPLE)['status'], 'blocked')
        r = self.validate(source=DUT.replace('ahdl_include', 'include'))
        self.assertIn('veriloga_requires_ahdl_include', {x['code'] for x in r['missing']})
        r = self.validate(source=DUT.replace('behavior/library.va', '/tmp/library.va'))
        self.assertIn('external_or_dynamic_include', {x['code'] for x in r['missing']})

    def test_system_file_write_and_unknown_tasks_never_silently_pass(self):
        statements = ['$system(1);', '$fopen(1);', '$fwrite(1,2);', '$display(held);',
                      '$strobe(held);', '$finish;', '$bound_step(1n);',
                      'held = $random;', 'held = arbitrary_function(1);',
                      'held = 1e999;', 'for (held=0;held<1;held=held+1) held=0;']
        for statement in statements:
            with self.subTest(statement=statement):
                r = self.validate(sample=SAMPLE.replace('I(inp,ret) <+ 0;', statement))
                self.assertEqual(r['status'], 'blocked')
                self.assertTrue(any(row['code'].startswith('veriloga_') for row in r['missing']))

    def test_standard_aliases_have_exact_bounded_shape_and_cannot_be_shadowed(self):
        _, _, spec, _ = inputs()
        for row in ({'name': 'other.vams', 'alias': 'OTHER', 'sha256': 'a' * 64},
                    {'name': 'constants.vams', 'alias': '/private/header', 'sha256': 'a' * 64},
                    {'name': 'constants.vams', 'alias': 'CONSTANTS', 'sha256': 'bad'},
                    {'name': 'constants.vams', 'alias': 'CONSTANTS', 'sha256': 'a' * 64, 'path': '/tmp/header'}):
            with self.subTest(row=row), self.assertRaises(DomainError):
                self.validate(binding={**spec['pdk_binding'], 'veriloga_standard_includes': [row]})
        with self.assertRaises(DomainError):
            self.validate(extra_support={'behavior/constants.vams': 'synthetic shadow'})

    def test_compiler_preserves_all_va_bytes_and_hashes_review_identity(self):
        source, selection, spec, profile = inputs()
        with patch('builtins.open', side_effect=AssertionError('no file access')):
            r = prepare_experiment(source, selection, spec, profile)
        self.assertEqual(r['status'], 'ready', r['missing'])
        self.assertEqual(r['files']['netlist/behavior/library.va'], LIBRARY.encode())
        self.assertEqual(r['files']['netlist/behavior/cells/sample.va'], SAMPLE.encode())
        self.assertEqual(r['recipe']['used_models'], [])
        self.assertEqual(validate_recipe(r['recipe']), spec)
        changed_source = dict(source)
        changed_source['source/sample.va'] += b'// changed reviewed bytes\n'
        changed = prepare_experiment(changed_source, selection, spec, profile)
        self.assertEqual(changed['status'], 'ready')
        self.assertNotEqual(r['recipe']['recipe_sha256'], changed['recipe']['recipe_sha256'])
        self.assertNotEqual(r['recipe']['agent_tb_manifest']['manifest_sha256'],
                            changed['recipe']['agent_tb_manifest']['manifest_sha256'])
        tampered = copy.deepcopy(r['recipe'])
        tampered['files_sha256']['netlist/behavior/cells/sample.va'] = '0' * 64
        with self.assertRaises(ExperimentIssue):
            validate_recipe(tampered)

    def test_standard_header_identity_changes_manifest_and_alias_conflict_blocks(self):
        source, selection, spec, profile = inputs()
        original = prepare_experiment(source, selection, spec, profile)
        spec['pdk_binding']['veriloga_standard_includes'][0]['sha256'] = 'b' * 64
        spec['pdk_binding']['veriloga_header_closure']['files'][0]['sha256'] = 'b' * 64
        changed = prepare_experiment(source, selection, spec, profile)
        self.assertNotEqual(original['recipe']['agent_tb_manifest']['manifest_sha256'],
                            changed['recipe']['agent_tb_manifest']['manifest_sha256'])
        spec['pdk_binding']['veriloga_standard_includes'][0]['alias'] = 'PDK_MAIN'
        spec['pdk_binding']['veriloga_header_closure']['files'][0]['alias'] = 'PDK_MAIN'
        r = prepare_experiment(source, selection, spec, profile)
        self.assertEqual(r['status'], 'missing')
        self.assertEqual(r['missing'][0]['code'], 'veriloga_standard_alias_conflict')

    def test_authored_helper_files_bind_exact_source_bytes_in_full_draft(self):
        source, selection, spec, profile = inputs()
        helpers = {'behavior/library.va': LIBRARY, 'behavior/cells/sample.va': SAMPLE}
        spec['criteria']['helper_files'] = helpers
        valid = prepare_experiment(source, selection, spec, profile)
        self.assertEqual(valid['status'], 'ready', valid['missing'])
        self.assertEqual(validate_recipe(valid['recipe']), spec)
        spec['criteria']['helper_files'] = {'behavior/library.va': LIBRARY}
        subset = prepare_experiment(source, selection, spec, profile)
        self.assertEqual(subset['status'], 'ready', subset['missing'])
        for changed in ({**helpers, 'behavior/library.va': LIBRARY + '\n'},
                        {**helpers, 'other.va': SAMPLE}):
            spec['criteria']['helper_files'] = changed
            result = prepare_experiment(source, selection, spec, profile)
            self.assertEqual(result['status'], 'missing')
            self.assertIn('authored_helper_files_mismatch', {row['code'] for row in result['missing']})

    def test_dut_support_and_tb_helpers_are_distinct_reviewed_origins(self):
        source, selection, spec, profile = inputs()
        source['source/dut.scs'] = DUT.replace('ahdl_include "behavior/library.va"', 'include "resistors.scs"').encode()
        source['source/resistors.scs'] = b'parameters fixture_resistance=1k\n'
        selection['support_files']['resistors.scs'] = 'source/resistors.scs'
        tb = TB + 'ahdl_include "behavior/library.va"\n'
        source['source/tb.scs'] = tb.encode()
        spec['criteria']['text'] = tb
        helpers = {'behavior/library.va': LIBRARY, 'behavior/cells/sample.va': SAMPLE}
        spec['criteria']['helper_files'] = helpers
        valid = prepare_experiment(source, selection, spec, profile)
        self.assertEqual(valid['status'], 'ready', valid['missing'])
        manifest = valid['recipe']['agent_tb_manifest']
        self.assertEqual(manifest['dut_support_paths'], ['resistors.scs'])
        self.assertEqual(manifest['authored_helper_paths'], sorted(helpers))
        roles = {row['path']: row.get('support_role') for row in manifest['files']}
        self.assertEqual(roles['resistors.scs'], 'dut_support')
        self.assertEqual(roles['behavior/library.va'], 'authored_helper')
        # A reachable but undeclared TB-only dependency is not DUT support.
        spec['criteria']['helper_files'] = {'behavior/library.va': LIBRARY}
        invalid = prepare_experiment(source, selection, spec, profile)
        self.assertIn('undeclared_testbench_support_file', {row['code'] for row in invalid['missing']})
        spec['criteria']['helper_files'] = {**helpers, 'behavior/cells/sample.va': SAMPLE + '\n'}
        invalid = prepare_experiment(source, selection, spec, profile)
        self.assertIn('authored_helper_files_mismatch', {row['code'] for row in invalid['missing']})
        spec['criteria']['helper_files'] = {**helpers, 'unused.va': SAMPLE}
        source['source/unused.va'] = SAMPLE.encode()
        selection['support_files']['unused.va'] = 'source/unused.va'
        invalid = prepare_experiment(source, selection, spec, profile)
        self.assertIn('unreferenced_supplied_file', {row['code'] for row in invalid['missing']})

    def test_local_vams_include_is_bound_without_loading_standard_header_bytes(self):
        library = LIBRARY.replace('cells/sample.va', 'cells/leaf.vams')
        r = self.validate(library=library, extra_support={'behavior/cells/leaf.vams': '`include "sample.va"\n'})
        self.assertEqual(r['status'], 'static_ready', r['missing'])
        self.assertEqual({e['target'] for e in r['manifest']['dependency_closure'] if e.get('kind') == 'veriloga_include'},
                         {'behavior/cells/leaf.vams', 'behavior/cells/sample.va'})

    def test_even_leaf_headers_require_frozen_inventory_closure(self):
        _, _, spec, _ = inputs()
        selected = spec['pdk_binding']
        selected.pop('veriloga_header_closure')
        r = self.validate(binding=selected)
        self.assertIn('veriloga_header_closure_missing', {row['code'] for row in r['missing']})

    def test_declared_transitive_header_closure_is_hash_bound_but_unverified(self):
        _, _, spec, _ = inputs()
        selected = spec['pdk_binding']
        closure = selected['veriloga_header_closure']
        closure['files'].append({'name': 'units/physical.vams', 'alias': 'VA_UNITS', 'sha256': 'd' * 64})
        closure['includes'].append({'from': 'constants.vams', 'reference': 'units/physical.vams',
                                    'target': 'units/physical.vams', 'line': 8})
        r = self.validate(binding=selected)
        self.assertEqual(r['status'], 'static_ready', r['missing'])
        self.assertEqual(r['manifest']['veriloga_header_closure'], closure)
        self.assertFalse(r['manifest']['simulation_verified'])
        changed = copy.deepcopy(selected)
        changed['veriloga_header_closure']['inventory_sha256'] = 'e' * 64
        self.assertNotEqual(r['manifest']['manifest_sha256'], self.validate(binding=changed)['manifest']['manifest_sha256'])
        variants = []
        extra = copy.deepcopy(selected)
        extra['veriloga_header_closure']['files'].append({'name': 'unused.vams', 'alias': 'VA_UNUSED', 'sha256': 'f' * 64})
        variants.append((extra, 'veriloga_header_closure_file_unused'))
        cycle = copy.deepcopy(selected)
        cycle['veriloga_header_closure']['includes'].append(
            {'from': 'constants.vams', 'reference': 'constants.vams', 'target': 'constants.vams'})
        variants.append((cycle, 'veriloga_header_closure_cycle'))
        missing = copy.deepcopy(selected)
        missing['veriloga_header_closure']['files'].pop()
        variants.append((missing, 'veriloga_header_closure_file_missing'))
        wrong_target = copy.deepcopy(selected)
        wrong_target['veriloga_header_closure']['includes'][0]['target'] = 'disciplines.vams'
        variants.append((wrong_target, 'veriloga_header_closure_resolution_mismatch'))
        wrong_hash = copy.deepcopy(selected)
        wrong_hash['veriloga_header_closure']['files'][0]['sha256'] = 'a' * 64
        variants.append((wrong_hash, 'veriloga_standard_header_closure_mismatch'))
        for variant, code in variants:
            with self.subTest(code=code):
                checked = self.validate(binding=variant)
                self.assertEqual(checked['status'], 'blocked')
                self.assertIn(code, {row['code'] for row in checked['missing']})

    def test_header_closure_forbids_raw_paths_and_traversal(self):
        _, _, spec, _ = inputs()
        for name in ('/opt/cadence/constants.vams', '../constants.vams', 'tb.scs'):
            selected = copy.deepcopy(spec['pdk_binding'])
            selected['veriloga_header_closure']['files'][0]['name'] = name
            with self.subTest(name=name), self.assertRaises(DomainError):
                self.validate(binding=selected)
        selected = copy.deepcopy(spec['pdk_binding'])
        selected['veriloga_header_closure']['includes'] = [
            {'from': 'constants.vams', 'reference': '../outside.vams', 'target': 'outside.vams'}]
        with self.assertRaises(DomainError):
            self.validate(binding=selected)

    def test_separate_ahdl_entry_cannot_borrow_another_entries_header_declaration(self):
        source = DUT + 'ahdl_include "separate.va"\n'
        separate = SAMPLE.replace('sample_hold', 'separate_hold')
        r = self.validate(source=source, extra_support={'separate.va': separate})
        self.assertIn('veriloga_disciplines_dependency_missing', {row['code'] for row in r['missing']})


if __name__ == '__main__':
    unittest.main()

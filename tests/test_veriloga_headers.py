"""Explicit synthetic standard-header roots and bounded dependency observations."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

from spiceowl.domain.common import DomainError
from spiceowl.onboarding.veriloga_headers import (build_veriloga_header_probe_script,
    validate_veriloga_headers, build_veriloga_header_binding)


@unittest.skipIf(sys.platform == "win32", "Executes remote Linux POSIX-path Verilog-A probe script against local temp dirs")
class VerilogaHeaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(); self.include = self.root / 'include'; self.include.mkdir()
        (self.include / 'constants.vams').write_text('`ifndef CONSTANTS_VAMS\n`define CONSTANTS_VAMS\n`define EXAMPLE_CONSTANT 3.0\n`endif\n')
        (self.include / 'disciplines.vams').write_text('`ifndef DISCIPLINES_VAMS\n`define DISCIPLINES_VAMS\n`include "constants.vams"\n`endif\n')

    def probe(self, roots=None, **kwargs):
        output = io.StringIO()
        with patch('subprocess.run', side_effect=AssertionError('No compiler or SSH')):
            script = build_veriloga_header_probe_script(roots=roots or [str(self.include)], **kwargs)
            with contextlib.redirect_stdout(output): exec(script, {})
        return validate_veriloga_headers(json.loads(output.getvalue()))

    def test_two_observed_standard_headers_and_transitive_static_closure(self):
        result = self.probe()
        self.assertEqual(result['status'], 'observed')
        self.assertTrue(result['coverage']['complete'])
        self.assertEqual({row['name'] for row in result['headers']}, {'constants.vams', 'disciplines.vams'})
        self.assertEqual(len(result['files']), 2)
        self.assertEqual(result['includes'][0]['reference'], 'constants.vams')
        self.assertTrue(all(len(row['sha256']) == 64 for row in result['files']))
        self.assertFalse(result['compiler_invoked'])

    def test_missing_standard_is_not_guessed_from_other_installations(self):
        (self.include / 'constants.vams').unlink()
        result = self.probe()
        self.assertFalse(result['coverage']['complete'])
        self.assertIn('standard_header_missing', {row['code'] for row in result['missing']})

    def test_nested_static_includes_are_hashed_and_do_not_expose_bodies(self):
        (self.include / 'constants.vams').write_text('`include "units.inc"\n')
        (self.include / 'units.inc').write_text('`define ONLY_SYNTHETIC_BODY 2\n')
        result = self.probe()
        self.assertEqual(len(result['files']), 3)
        self.assertTrue(result['coverage']['complete'])
        self.assertNotIn('ONLY_SYNTHETIC_BODY', json.dumps(result))

    def test_dynamic_include_and_unknown_conditional_are_explicit_gaps(self):
        for content, code in [('`include HEADER_MACRO\n', 'dynamic_header_include'),
                ('`ifdef CHOOSE_SOMETHING\n`include "constants.vams"\n`endif\n', 'conditional_header_include')]:
            (self.include / 'disciplines.vams').write_text(content)
            result = self.probe()
            self.assertFalse(result['coverage']['complete'])
            self.assertIn(code, {row['code'] for row in result['missing']})

    def test_cycles_and_symlink_escape_never_become_complete(self):
        (self.include / 'constants.vams').write_text('`include "disciplines.vams"\n')
        self.assertIn('header_include_cycle', {row['code'] for row in self.probe()['missing']})
        (self.include / 'constants.vams').unlink(); outside = self.root / 'outside.vams'; outside.write_text('`define OUTSIDE 1\n')
        (self.include / 'constants.vams').symlink_to(outside)
        result = self.probe(); self.assertFalse(result['coverage']['complete'])
        self.assertIn('header_outside_observed_roots', {row['code'] for row in result['missing']})

    def test_duplicate_standard_paths_remain_ambiguous(self):
        other = self.root / 'other'; other.mkdir()
        (other / 'constants.vams').write_text('`define DIFFERENT 4\n')
        result = self.probe([str(self.include), str(other)])
        self.assertIn('standard_header_ambiguous', {row['code'] for row in result['missing']})

    def test_guard_alternate_branches_and_early_end_are_not_ordinary_include_guards(self):
        for source in (
                '`ifndef GUARDED\n`define GUARDED\n`else\n`include "constants.vams"\n`endif\n',
                '`ifndef GUARDED\n`define GUARDED\n`elsif ALTERNATIVE\n`include "constants.vams"\n`endif\n',
                '`ifndef GUARDED\n`define GUARDED\n`endif\n`ifndef GUARDED\n`include "constants.vams"\n`endif\n'):
            with self.subTest(source=source):
                (self.include / 'disciplines.vams').write_text(source)
                result = self.probe()
                self.assertFalse(result['coverage']['complete'])
                self.assertIn('conditional_header_include', {row['code'] for row in result['missing']})

    def test_unbalanced_conditions_remain_an_explicit_gap(self):
        (self.include / 'disciplines.vams').write_text('`endif\n`include "constants.vams"\n')
        self.assertIn('unbalanced_header_condition', {row['code'] for row in self.probe()['missing']})

    def test_generated_probe_retains_python_27_grammar(self):
        try:
            from lib2to3.refactor import RefactoringTool
        except ImportError:
            self.skipTest('Python 2 grammar checker unavailable')
        script = build_veriloga_header_probe_script(roots=[str(self.include)])
        RefactoringTool([]).refactor_string(script, 'veriloga-header-probe.py')

    def test_read_limits_and_receipt_integrity_fail_closed(self):
        result = self.probe(limits={'max_file_bytes': 8})
        self.assertFalse(result['coverage']['complete'])
        self.assertIn('header_file_byte_limit', {row['code'] for row in result['missing']})
        complete = self.probe(); complete['coverage']['complete'] = False
        with self.assertRaises(DomainError): validate_veriloga_headers(complete)

    def test_failed_reads_and_include_lists_obey_global_bounds(self):
        result = self.probe(limits={'max_total_bytes': 8})
        self.assertLessEqual(result['coverage']['bytes_read'], 9)
        self.assertIn('header_total_byte_limit', {row['code'] for row in result['missing']})
        (self.include / 'disciplines.vams').write_text('`include "one.inc"\n`include "two.inc"\n')
        (self.include / 'one.inc').write_text('// synthetic\n')
        (self.include / 'two.inc').write_text('// synthetic\n')
        result = self.probe(limits={'max_includes': 1})
        self.assertEqual(len(result['includes']), 1)
        self.assertIn('header_include_limit', {row['code'] for row in result['missing']})
        result = self.probe(limits={'max_files': 1})
        self.assertLessEqual(result['coverage']['attempted_file_count'], 1)

    def test_binding_is_pure_public_paths_are_relative_and_compiler_accepts_it(self):
        (self.include / 'units').mkdir()
        (self.include / 'units' / 'physical.vams').write_text('`define SYNTHETIC_UNIT 1\n')
        (self.include / 'constants.vams').write_text('`include "units/physical.vams"\n')
        observation = self.probe()
        with patch('builtins.open', side_effect=AssertionError('Binding conversion is pure')):
            binding = build_veriloga_header_binding(observation,
                required_names=['constants.vams', 'disciplines.vams'])
        self.assertNotIn(str(self.root), json.dumps(binding['public']))
        self.assertEqual(binding['public']['veriloga_header_closure']['inventory_sha256'], observation['observation_id'])
        self.assertEqual({row['name'] for row in binding['public']['veriloga_header_closure']['files']},
            {'constants.vams', 'disciplines.vams', 'units/physical.vams'})
        for alias, row in binding['private_aliases'].items():
            self.assertEqual(row['sha256'], hashlib.sha256(Path(row['path']).read_bytes()).hexdigest())
            self.assertTrue(alias.startswith('VA_HEADER_'))
        from tests.test_veriloga import inputs, VerilogATests
        _, _, spec, _ = inputs()
        spec['pdk_binding'].update(binding['public'])
        validated = VerilogATests().validate(binding=spec['pdk_binding'])
        self.assertEqual(validated['status'], 'static_ready', validated['missing'])

    def test_only_required_reachable_files_are_bound_including_leaf_headers(self):
        observation = self.probe()
        binding = build_veriloga_header_binding(observation, required_names=['constants.vams'])
        self.assertEqual(len(binding['public']['veriloga_header_closure']['files']), 1)
        self.assertEqual(binding['public']['veriloga_header_closure']['includes'], [])
        self.assertEqual(len(binding['private_aliases']), 1)

    def test_selected_observed_root_resolves_standard_ambiguity_without_guessing(self):
        other = self.root / 'other'; other.mkdir()
        for name in ('constants.vams', 'disciplines.vams'):
            (other / name).write_text('// another newly authored synthetic standard\n')
        observation = self.probe([str(self.include), str(other)])
        self.assertEqual(observation['status'], 'partial')
        with self.assertRaises(DomainError):
            build_veriloga_header_binding(observation, required_names=['constants.vams'])
        binding = build_veriloga_header_binding(observation, required_names=['constants.vams'], selected_root=str(other))
        self.assertEqual(next(iter(binding['private_aliases'].values()))['path'], str(other / 'constants.vams'))
        with self.assertRaises(DomainError):
            build_veriloga_header_binding(observation, required_names=['constants.vams'], selected_root=str(self.root))

    def test_incomplete_or_conditional_reachable_closure_is_never_bound(self):
        for source in ('`include "absent.vams"\n', '`ifdef CHOICE\n`include "constants.vams"\n`endif\n'):
            (self.include / 'disciplines.vams').write_text(source)
            with self.assertRaises(DomainError):
                build_veriloga_header_binding(self.probe(), required_names=['disciplines.vams'])

    def test_root_fallback_keeps_exact_relative_layout_and_detects_collisions(self):
        (self.include / 'sub').mkdir()
        (self.include / 'disciplines.vams').write_text('`include "sub/units.vams"\n')
        (self.include / 'sub' / 'units.vams').write_text('`include "constants.vams"\n')
        binding = build_veriloga_header_binding(self.probe(), required_names=['disciplines.vams'])
        closure = binding['public']['veriloga_header_closure']
        self.assertIn('sub/constants.vams', {row['name'] for row in closure['files']})
        self.assertEqual(next(edge['target'] for edge in closure['includes'] if edge['from'] == 'sub/units.vams'), 'sub/constants.vams')
        (self.include / 'disciplines.vams').write_text('`include "CONSTANTS.vams"\n')
        (self.include / 'CONSTANTS.vams').write_text('// alternate case, distinct bytes\n')
        with self.assertRaises(DomainError):
            build_veriloga_header_binding(self.probe(), required_names=['constants.vams', 'disciplines.vams'])

    def test_rehashed_malformed_budgets_paths_and_graphs_fail_deep_validation(self):
        observation = self.probe()
        mutations = [
            lambda value: value['coverage']['limits'].update(max_files=33),
            lambda value: value['coverage'].update(bytes_read=0),
            lambda value: value['files'][0].update(path='/outside/header.vams'),
            lambda value: value['headers'][0].update(include_roots=['/not-observed']),
            lambda value: value['includes'][0].update(path='/outside/target.vams'),
            lambda value: value['includes'][0].update(reference='../escape.vams'),
            lambda value: value['includes'][0].update(path=value['includes'][0]['from']),
        ]
        for mutate in mutations:
            altered = copy.deepcopy(observation); mutate(altered)
            altered['observation_id'] = hashlib.sha256(json.dumps(
                {key: value for key, value in altered.items() if key != 'observation_id'},
                sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
            with self.assertRaises(DomainError): validate_veriloga_headers(altered)

    def test_rehashed_standard_name_swap_cannot_relabel_observed_file_identity(self):
        (self.include / 'constants.vams').write_text('// original synthetic constants leaf\n')
        (self.include / 'disciplines.vams').write_text('// original synthetic disciplines leaf\n')
        observation = self.probe()
        observation['headers'][0]['name'], observation['headers'][1]['name'] = (
            observation['headers'][1]['name'], observation['headers'][0]['name'])
        observation['observation_id'] = hashlib.sha256(json.dumps(
            {key: value for key, value in observation.items() if key != 'observation_id'},
            sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
        with self.assertRaises(DomainError): validate_veriloga_headers(observation)
        with self.assertRaises(DomainError):
            build_veriloga_header_binding(observation, required_names=['constants.vams'])

    def test_standard_entry_symlink_is_an_explicit_gap_until_alias_provenance_is_supported(self):
        (self.include / 'constants.vams').unlink()
        target = self.include / 'actual_constants.vams'; target.write_text('// synthetic aliased header\n')
        (self.include / 'constants.vams').symlink_to(target)
        observation = self.probe()
        self.assertEqual(observation['status'], 'partial')
        self.assertIn('standard_header_symlink_unsupported', {row['code'] for row in observation['missing']})
        self.assertNotIn('constants.vams', {row['name'] for row in observation['headers']})
        with self.assertRaises(DomainError):
            build_veriloga_header_binding(observation, required_names=['constants.vams'])


if __name__ == '__main__': unittest.main()

"""Original multi-corner fixtures: indexed source evidence never crosses scopes.

Run --benchmark for a local before/after measurement against a retained Git ref.
The comparison keeps the baseline serializer and scanner limits on both sides.
"""
import argparse
import contextlib
import io
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import unittest

from spiceowl.onboarding import pdk_discovery as discovery


def execute(script):
    output = io.StringIO(); namespace = {}
    with contextlib.redirect_stdout(output): exec(script, namespace)
    lines = [line for line in output.getvalue().splitlines()
             if not line.startswith(discovery.DIRECTORY_SNAPSHOT_PREFIX)]
    return json.loads('\n'.join(lines)), namespace


def scan(root, *, full=False, names=None, limits=None):
    if full:
        return execute(discovery.build_pdk_library_script(roots=[str(root)], limits=limits))
    return execute(discovery.build_pdk_discovery_script(roots=[str(root)],
        required_masters=names or ['device'], source_sha256='a' * 64, limits=limits))


def models(receipt, candidate):
    if receipt['schema'] == discovery.LIBRARY_SCHEMA:
        return discovery.library_candidate_models(receipt, candidate)
    return candidate['models']


def entries(receipt, filename='usage.scs'):
    return {tuple(row['section_candidates']): row for row in receipt['entry_candidates']
            if Path(row['entry_path']).name == filename}


class ScanIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def write(self, name, source):
        (self.root / name).write_text(source)

    def fixture(self):
        self.write('wrapper.scs', 'simulator lang=spectre\nsubckt device (d g s b)\n'
            'M0 (d g s b) primitive\nends device\n')
        self.write('corners.scs', 'simulator lang=spectre\n'
            'section normal\nmodel primitive bsim4 type=n vnom=0.8\nendsection normal\n'
            'section inverted\nmodel primitive bsim4 type=p vnom=1.2\nendsection inverted\n'
            'section duplicate\nmodel primitive bsim4 type=n\nmodel primitive bsim4 type=p\nendsection duplicate\n')
        self.write('usage.scs', ''.join('section '+name+'\ninclude "wrapper.scs"\n'+content+
            'endsection '+name+'\n' for name, content in (
                ('TT', 'include "corners.scs" section=normal\n'),
                ('TT_again', 'include "corners.scs" section=normal\n'),
                ('FF', 'include "corners.scs" section=inverted\n'),
                ('duplicate', 'include "corners.scs" section=duplicate\n'),
                ('missing', ''),
                ('unknown', 'include "corners.scs" section=normal\ninclude "unread.scs"\n'))))

    def test_active_primitive_definition_unknown_and_missing_keys_are_isolated(self):
        self.fixture()
        for full in (False, True):
            with self.subTest(full=full):
                receipt, namespace = scan(self.root, full=full)
                by_section = entries(receipt)
                contracts = {section: next(row for row in models(receipt, entry) if row['name'] == 'device')
                             for section, entry in by_section.items()}
                self.assertEqual(contracts[('TT',)]['polarity'], 'n')
                self.assertEqual(contracts[('FF',)]['polarity'], 'p')
                self.assertEqual(contracts[('TT_again',)], contracts[('TT',)])
                self.assertEqual(contracts[('missing',)]['reason'], 'primitive_model_not_resolved')
                self.assertEqual(contracts[('duplicate',)]['reason'], 'multiple_active_primitive_definitions')
                self.assertEqual(contracts[('unknown',)]['confidence'], 'suggested')
                self.assertEqual(contracts[('TT',)]['confidence'], 'verified')
                self.assertNotEqual(contracts[('unknown',)], contracts[('TT',)])
                if not full:
                    # Inspect the actual generator state, not the JSON-decoded copies.
                    actual = entries({'entry_candidates': namespace['entry_candidates']})
                    self.assertIs(actual[('TT',)]['models'][0], actual[('TT_again',)]['models'][0])

    def test_full_and_filtered_observe_identical_contracts_for_common_entries(self):
        self.fixture()
        filtered, _ = scan(self.root)
        full, _ = scan(self.root, full=True)
        full_rows = entries(full)
        for section, candidate in entries(filtered).items():
            selected = next(row for row in models(full, full_rows[section]) if row['name'] == 'device')
            self.assertEqual(candidate['models'][0], selected)
            self.assertEqual(candidate['dependency_scopes'], full_rows[section]['dependency_scopes'])
            self.assertEqual(candidate['unknown_codes'], full_rows[section]['unknown_codes'])

    def test_literal_and_scope_metadata_does_not_leak_between_entry_sections(self):
        self.fixture()
        for full in (False, True):
            receipt, _ = scan(self.root, full=full)
            rows = entries(receipt)
            self.assertEqual([row['value'] for row in rows[('TT',)]['nominal_voltage_evidence']], [0.8])
            self.assertEqual([row['value'] for row in rows[('FF',)]['nominal_voltage_evidence']], [1.2])
            self.assertEqual(rows[('missing',)]['nominal_voltage_evidence'], [])
            self.assertEqual(rows[('TT_again',)]['nominal_voltage_evidence'], rows[('TT',)]['nominal_voltage_evidence'])

    def test_bins_are_keyed_by_active_definition_and_keep_original_owner_rules(self):
        self.write('wrapper.scs', 'simulator lang=spice\n.subckt device d g s b\n'
            'M0 d g s b primitive w=1u l=1u\n.ends device\n')
        self.write('bins.scs', 'simulator lang=spice\n.lib normal\n.model primitive.1 NMOS\n.endl normal\n'
            '.lib inverted\n.model primitive.1 PMOS\n.endl inverted\n'
            '.lib global_only\n.model primitive.global NMOS\n.endl global_only\n')
        self.write('usage.scs', ''.join('section '+section+'\ninclude "wrapper.scs"\n'
            'include "bins.scs" section='+section+'\nendsection '+section+'\n'
            for section in ('normal', 'inverted', 'global_only')))
        for full in (False, True):
            receipt, _ = scan(self.root, full=full)
            contracts = {section: next(row for row in models(receipt, candidate) if row['name'] == 'device')
                         for section, candidate in entries(receipt).items()}
            self.assertEqual(contracts[('normal',)]['polarity'], 'n')
            self.assertEqual(contracts[('inverted',)]['polarity'], 'p')
            self.assertEqual(contracts[('global_only',)]['reason'], 'missing_numeric_model_bin')

    def test_usage_entry_priority_is_explicit_and_preserves_candidate_identity(self):
        self.write('a_definitions.scs', 'model device bsim4 type=n\n')
        self.write('z_usage.scs', 'section TT\ninclude "a_definitions.scs"\nendsection TT\n')
        receipt, _ = scan(self.root)
        candidate = receipt['entry_candidates'][0]
        self.assertEqual(Path(candidate['entry_path']).name, 'z_usage.scs')
        self.assertEqual(receipt['search_policy']['entry_candidate_order'], 'include_targets_first_then_path_section')
        hashes = {row['path']: row['sha256'] for row in receipt['files']}
        identity = discovery._digest({'source_sha256': receipt['source_sha256'], 'entry_path': candidate['entry_path'],
            'sections': candidate['section_candidates'], 'required_masters': receipt['required_masters'],
            'dependency_scopes': candidate['dependency_scopes'],
            'files': [[path, hashes[path]] for path in candidate['dependency_paths']], 'tmi_candidates': candidate['tmi_candidates']})
        self.assertEqual(candidate['candidate_id'], identity)

    def test_global_and_section_records_keep_source_order_and_closure_limits(self):
        self.write('shared.scs', 'model device bsim4 type=n\n')
        self.write('usage.scs', 'include "shared.scs"\nsection TT\n'
            'include "missing.scs"\nendsection TT\n')
        receipt, _ = scan(self.root)
        scope = entries(receipt)[('TT',)]
        self.assertIn('include_target_not_scanned', scope['unknown_codes'])
        self.write('usage.scs', 'model device bsim4 type=n\ninclude "shared.scs"\n')
        receipt, _ = scan(self.root, limits={'max_sections': 1})
        self.assertTrue(any('section_closure_limit' in row['unknown_codes'] for row in receipt['entry_candidates']) or
                        any(row['code'] == 'section_limit' for row in receipt['unknown']))

    def test_generated_indexed_program_keeps_python27_grammar(self):
        try:
            from lib2to3.refactor import RefactoringTool
        except ImportError:
            self.skipTest('Python 2 grammar checker unavailable')
        for full in (False, True):
            script = (discovery.build_pdk_library_script(roots=[str(self.root)]) if full else
                discovery.build_pdk_discovery_script(roots=[str(self.root)], required_masters=['device'], source_sha256='a' * 64))
            RefactoringTool([]).refactor_string(script, 'indexed-discovery.py')


def benchmark():
    parser = argparse.ArgumentParser()
    parser.add_argument('--benchmark', action='store_true')
    parser.add_argument('--baseline-ref', default='cce7598')
    parser.add_argument('--corners', type=int, default=48)
    parser.add_argument('--devices', type=int, default=32)
    parser.add_argument('--iterations', type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.corners <= 100 or not 2 <= args.devices <= 64 or not 1 <= args.iterations <= 10:
        parser.error('Use corners 1–100, devices 2–64, iterations 1–10.')
    repository = Path(__file__).resolve().parents[1]
    source = subprocess.run(['git', 'show', args.baseline_ref + ':src/spiceowl/onboarding/pdk_discovery.py'],
                            cwd=repository, capture_output=True, text=True, check=True).stdout
    baseline = {'__name__': 'baseline_discovery'}; exec(source, baseline)
    indexed = {'__name__': 'indexed_discovery'}; exec(source, indexed)
    indexed['_CONTRACT_HELPERS'] = discovery._CONTRACT_HELPERS
    indexed['_DISCOVERY_PROGRAM'] = discovery._DISCOVERY_PROGRAM
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        sections = ['corner_%03d' % number for number in range(args.corners)]
        lines = ['simulator lang=spectre']
        for section in sections:
            lines.append('section ' + section)
            for number in range(args.devices):
                lines += ['model primitive_%03d bsim4 type=n vnom=0.8' % number,
                          'subckt device_%03d (d g s b)' % number,
                          'M0 (d g s b) primitive_%03d' % number, 'ends device_%03d' % number]
            lines.append('endsection ' + section)
        (root / 'a_definitions.scs').write_text('\n'.join(lines) + '\n')
        (root / 'z_usage.scs').write_text(''.join('section '+section+'\ninclude "a_definitions.scs" section='+section+
                                               '\nendsection '+section+'\n' for section in sections))
        measurements = {}
        for mode in ('filtered', 'full_library'):
            reference = None
            for label, module in (('baseline', baseline), ('indexed_core', indexed)):
                script = (module['build_pdk_library_script'](roots=[str(root)]) if mode == 'full_library' else
                          module['build_pdk_discovery_script'](roots=[str(root)], required_masters=['device_000', 'device_001'],
                                                               source_sha256='a' * 64))
                times = []
                for iteration in range(args.iterations):
                    start = time.perf_counter_ns(); receipt, _ = execute(script)
                    times.append((time.perf_counter_ns() - start) / 1e6)
                semantic = {row['candidate_id']: row for row in receipt['entry_candidates']}
                if reference is None: reference = semantic
                else:
                    if semantic != reference: raise AssertionError('Candidate evidence differs from the baseline on the uncapped fixture.')
                measurements[mode + '_' + label] = {'median_ms': round(statistics.median(times), 3),
                    'max_ms': round(max(times), 3), 'samples': len(times), 'candidate_count': len(semantic),
                    'file_bytes_read': receipt['bytes_read']}
        print(json.dumps({'synthetic': True, 'baseline_ref': args.baseline_ref,
            'environment': {'python': platform.python_version(), 'system': platform.system(), 'machine': platform.machine()},
            'corners': args.corners, 'devices_per_corner': args.devices, 'measurements': measurements,
            'measurement_scope': 'generate once; execute local scanner, serialize stdout, parse receipt; same baseline serializer and limits',
            'filesystem_cache_purged': False, 'network_or_simulator_calls': 0}, indent=2))


if __name__ == '__main__':
    benchmark() if '--benchmark' in sys.argv else unittest.main()

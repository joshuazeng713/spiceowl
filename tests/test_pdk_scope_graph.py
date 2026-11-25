"""Original synthetic metadata and files; cached projections perform no IO."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

from spiceowl.domain.common import DomainError
from spiceowl.onboarding.pdk_discovery import build_pdk_library_script, validate_pdk_library, _digest
from spiceowl.onboarding.pdk_scope_graph import index_library_graph, project_scope_graph


def scan(root, **kwargs):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(build_pdk_library_script(roots=[str(root)], **kwargs), {})
    return validate_pdk_library(json.loads(output.getvalue()))


@unittest.skipIf(sys.platform == "win32", "Executes remote Linux POSIX-path PDK scanner against local temp dirs")
class PdkScopeGraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.entry = self.root / 'pdk.scs'
        self.entry.write_text('model global_helper bsim4 type=n\n'
            'section Prelude\nparameters tnom=27\nendsection Prelude\n'
            'section TT_MOS\ninclude "mos.scs" section=Core\nendsection TT_MOS\n'
            'section TT_CAP\ninclude "cap.scs"\nendsection TT_CAP\n')
        (self.root / 'mos.scs').write_text('section Core\nmodel n_device bsim4 type=n\n'
            'model p_device bsim4 type=p\nendsection Core\n')
        (self.root / 'cap.scs').write_text('subckt capacitor_device (a b)\n'
            'C0 (a b) capacitor c=1p\nends capacitor_device\n')
        self.library = scan(self.root)

    def project(self, library=None, sections=('TT_MOS', 'TT_CAP')):
        index = index_library_graph(self.library if library is None else library)
        ids = [index['by_scope'][(str(self.entry), section.lower() if section is not None else None)]['node_id']
               for section in sections]
        return project_scope_graph(index, entry_path=str(self.entry), section_ids_raw=ids)

    def assert_invalid(self, operation):
        with self.assertRaises(DomainError) as raised:
            operation()
        self.assertEqual(raised.exception.code, 'invalid_pdk_scope')

    def test_same_main_sections_project_namespace_and_hashes_without_contracts_or_io(self):
        before = copy.deepcopy(self.library)
        with patch('builtins.open', side_effect=AssertionError('Metadata only')), \
                patch('subprocess.run', side_effect=AssertionError('No SSH or solver')):
            index = index_library_graph(self.library)
            self.assertEqual(set(index), {'files', 'nodes', 'by_scope', 'by_path', 'declarations', 'missing'})
            result = self.project()
        self.assertEqual(result['missing'], [])
        self.assertEqual(result['covered_model_names'], ['capacitor_device', 'global_helper', 'n_device', 'p_device'])
        self.assertEqual(result['scope'], {'node_count': 4, 'file_count': 3, 'namespace_model_count': 4})
        self.assertEqual(result['dependency_scopes'][0], {'path': str(self.entry), 'section': 'TT_MOS'})
        files = {row['path']: row['sha256'] for row in self.library['files']}
        self.assertTrue(all(row['sha256'] == files[row['path']] for row in result['expected_files']))
        self.assertEqual(self.library, before)
        self.assertNotIn('model_contracts', result)
        self.assertNotIn('verified', json.dumps(result))
        json.dumps(result)

    def test_global_scope_and_shared_dependency_are_deduplicated(self):
        result = self.project(sections=(None, 'TT_MOS'))
        self.assertEqual(result['covered_model_names'], ['global_helper', 'n_device', 'p_device'])
        self.assertEqual(sum(row['name'] == 'global_helper' for row in result['master_declarations']), 1)
        result['master_declarations'][0]['name'] = 'changed_after_projection'
        self.assertNotIn('changed_after_projection', json.dumps(self.library))

    def test_candidate_limit_does_not_hide_observed_sections_or_require_rescan(self):
        library = scan(self.root, limits={'max_candidates': 1})
        self.assertEqual(library['status'], 'partial')
        self.assertEqual(len(library['entry_candidates']), 1)
        result = self.project(library)
        self.assertEqual(result['missing'], [])
        self.assertIn('capacitor_device', result['covered_model_names'])

    def test_duplicate_model_at_distinct_physical_locations_blocks_the_union(self):
        path = self.root / 'cap.scs'
        path.write_text('model n_device bsim4 type=p\n' + path.read_text())
        result = self.project(scan(self.root))
        duplicate = [row for row in result['missing'] if row['code'] == 'scope_graph_duplicate_model_declarations']
        self.assertEqual(duplicate, [{'code': 'scope_graph_duplicate_model_declarations', 'model_name': 'n_device', 'declaration_count': 2}])

    def test_unknown_and_opaque_selected_scopes_are_blocked(self):
        for statement in ('if conditional\n', '.protect\n'):
            self.entry.write_text('section TT_MOS\n'+statement+'include "mos.scs" section=Core\nendsection TT_MOS\n')
            result = self.project(scan(self.root), sections=('TT_MOS',))
            self.assertIn('scope_graph_unknown_scope', {row['code'] for row in result['missing']})

    def test_missing_include_file_or_section_is_an_observed_gap_not_a_fake_node(self):
        for reference in ('include "unobserved.scs"', 'include "mos.scs" section=NotThere'):
            self.entry.write_text('section TT_MOS\n'+reference+'\nendsection TT_MOS\n')
            result = self.project(scan(self.root), sections=('TT_MOS',))
            self.assertIn('scope_graph_target_missing', {row['code'] for row in result['missing']})
            self.assertEqual(result['covered_model_names'], [])

    def test_actual_include_cycle_blocks_projection_without_recursion(self):
        self.entry.write_text('include "other.scs"\nmodel root_device bsim4 type=n\n')
        (self.root / 'other.scs').write_text('include "pdk.scs"\n')
        result = self.project(scan(self.root), sections=(None,))
        self.assertIn('scope_graph_include_cycle', {row['code'] for row in result['missing']})

    def test_graph_omissions_and_global_truncation_never_produce_a_complete_projection(self):
        for mutation in ('absent', 'empty', 'omitted', 'edge_limit', 'unknown_truncated'):
            library = copy.deepcopy(self.library)
            if mutation == 'absent': library.pop('section_graph')
            elif mutation == 'empty': library['section_graph'] = []
            elif mutation == 'omitted': library['detail_omissions'] = [{'field': 'section_graph', 'reason': 'response_limit'}]
            elif mutation == 'edge_limit':
                library['unknown'] = [{'code': 'graph_edge_limit'}]; library['unknown_count'] = 1
            else: library['unknown_truncated'] = True
            index = index_library_graph(library)
            self.assertTrue(index['missing'], mutation)
            result = project_scope_graph(index, entry_path=str(self.entry), section_ids_raw=['a' * 64])
            self.assertTrue(result['missing'])
            self.assertEqual(result['dependency_scopes'], [])

    def test_truncated_model_name_inventory_blocks_even_a_matching_retained_namespace(self):
        library = copy.deepcopy(self.library)
        retained = ['global_helper', 'n_device']
        library['coverage'].update(observed_model_names=retained, observed_names_truncated=True)
        for node in library['section_graph']:
            node['master_coverage'] = sorted(set(node['master_coverage']) & set(retained))
        index = index_library_graph(library)
        self.assertIn('scope_graph_name_inventory_truncated', {row['code'] for row in index['missing']})
        result = self.project(library)
        self.assertIn('scope_graph_name_inventory_truncated', {row['code'] for row in result['missing']})
        self.assertEqual(result['covered_model_names'], [])
        self.assertEqual(result['dependency_scopes'], [])
        self.assertEqual(result['expected_files'], [])

    def test_tampered_node_identity_scope_spelling_and_coverage_are_invalid(self):
        mutations = [lambda node: node.update(node_id='0' * 64),
                     lambda node: node.update(section_key='tt_mos'),
                     lambda node: node.update(master_coverage=['invented_device']),
                     lambda node: node.update(include_targets=[['/outside/escape.scs', None]]),
                     lambda node: node.update(include_targets=[['relative.scs', None]]),
                     lambda node: node.update(unknown_codes=['not a code'])]
        for mutate in mutations:
            library = copy.deepcopy(self.library); mutate(library['section_graph'][0])
            self.assert_invalid(lambda: index_library_graph(library))
        library = copy.deepcopy(self.library)
        node = next(row for row in library['section_graph'] if row['section'] == 'TT_MOS')
        node['section'] = 'tt_mos'
        self.assert_invalid(lambda: index_library_graph(library))

    def test_duplicate_scope_and_declaration_metadata_are_invalid(self):
        library = copy.deepcopy(self.library); library['section_graph'].append(copy.deepcopy(library['section_graph'][0]))
        self.assert_invalid(lambda: index_library_graph(library))
        library = copy.deepcopy(self.library); library['declarations'].append(copy.deepcopy(library['declarations'][0]))
        self.assert_invalid(lambda: index_library_graph(library))
        library = copy.deepcopy(self.library); library['declarations'][0]['section'] = 'invented'
        self.assert_invalid(lambda: index_library_graph(library))
        library = copy.deepcopy(self.library); library['declarations'][0]['source_body'] = 'untrusted body'
        self.assert_invalid(lambda: index_library_graph(library))

    def test_complete_include_records_and_graph_edges_cannot_diverge(self):
        for mutation in ('drop_graph_edge', 'drop_include', 'change_applied_scope', 'duplicate_line',
                         'bad_line', 'extra_source_body', 'wrong_scope_spelling'):
            library = copy.deepcopy(self.library)
            node = next(row for row in library['section_graph'] if row['section'] == 'TT_MOS')
            include = next(row for row in library['includes'] if row['from_section'] == 'TT_MOS')
            if mutation == 'drop_graph_edge': node['include_targets'].pop()
            elif mutation == 'drop_include': library['includes'].remove(include)
            elif mutation == 'change_applied_scope': include['from_section'] = 'TT_CAP'
            elif mutation == 'duplicate_line':
                library['includes'].append(copy.deepcopy(include))
                node['include_targets'].append(copy.deepcopy(node['include_targets'][0]))
            elif mutation == 'bad_line': include['line'] = False
            elif mutation == 'extra_source_body': include['source_body'] = 'untrusted include source'
            else: include['from_section'] = 'tt_mos'
            self.assert_invalid(lambda: index_library_graph(library))

    def test_repeated_include_targets_preserve_distinct_lines_and_duplicate_counts(self):
        self.entry.write_text('section TT_MOS\ninclude "mos.scs" section=Core\n'
            'include "mos.scs" section=Core\nendsection TT_MOS\n')
        library = scan(self.root)
        rows = [row for row in library['includes'] if row['from_section'] == 'TT_MOS']
        self.assertEqual([row['line'] for row in rows], [2, 3])
        result = self.project(library, sections=('TT_MOS',))
        self.assertEqual(result['missing'], [])
        self.assertEqual(result['covered_model_names'], ['n_device', 'p_device'])
        node = next(row for row in library['section_graph'] if row['section'] == 'TT_MOS')
        node['include_targets'].pop()
        self.assert_invalid(lambda: index_library_graph(library))

    def test_global_include_line_must_match_across_applied_scopes(self):
        self.entry.write_text('include "cap.scs"\nsection TT_MOS\n'
            'include "mos.scs" section=Core\nendsection TT_MOS\n')
        library = scan(self.root)
        self.assertEqual(self.project(library, sections=('TT_MOS',))['missing'], [])
        include = next(row for row in library['includes'] if row['from_section'] == 'TT_MOS' and row['line'] == 1)
        include['to'] = str(self.root / 'mos.scs')
        include['section'] = 'Core'
        node = next(row for row in library['section_graph'] if row['section'] == 'TT_MOS')
        node['include_targets'][0] = [include['to'], 'core']
        self.assert_invalid(lambda: index_library_graph(library))

    def test_missing_or_declared_truncated_include_inventory_is_a_gap(self):
        for mutation in ('absent', 'omitted', 'truncated'):
            library = copy.deepcopy(self.library)
            if mutation == 'absent': library.pop('includes')
            else:
                library['includes'] = []
                if mutation == 'omitted':
                    library['detail_omissions'] = [{'field': 'includes', 'reason': 'response_limit'}]
                else: library['includes_truncated'] = True
            result = self.project(library)
            self.assertTrue(result['missing'], mutation)
            self.assertEqual(result['covered_model_names'], [])
            self.assertEqual(result['dependency_scopes'], [])

    def test_removed_scope_is_explicitly_incomplete_and_bad_selection_is_rejected(self):
        library = copy.deepcopy(self.library)
        library['section_graph'] = [node for node in library['section_graph'] if node['section'] != 'TT_CAP']
        self.assertTrue(index_library_graph(library)['missing'])
        index = index_library_graph(self.library)
        one = index['by_scope'][(str(self.entry), 'tt_mos')]['node_id']
        other = index['by_scope'][(str(self.root / 'mos.scs'), 'core')]['node_id']
        for ids in ([], [one, one], [other], ['0' * 64], [None], [one] * 65):
            self.assert_invalid(lambda ids=ids: project_scope_graph(index, entry_path=str(self.entry), section_ids_raw=ids))

    def test_tmi_metadata_is_copied_exactly_and_pinned_to_observed_file_hashes(self):
        binary = self.root / 'tmi' / 'Example64' / 'libTMImodel.so'; binary.parent.mkdir(parents=True)
        binary.write_bytes(b'original synthetic binary bytes')
        self.entry.write_text('section TT_MOS\ninclude "mos.scs" section=Core\n'
            '.option tmipath="tmi"\nendsection TT_MOS\n')
        library = scan(self.root)
        result = self.project(library, sections=('TT_MOS',))
        observed = next(node['tmi_references'] for node in library['section_graph'] if node['path'] == str(self.entry) and node['section'] == 'TT_MOS')
        self.assertEqual(result['tmi_evidence'], observed)
        self.assertIsNot(result['tmi_evidence'][0], observed[0])
        self.assertNotIn(str(binary), {row['path'] for row in result['expected_files']})
        observed[0]['platform_candidates'][0]['sha256'] = '0' * 64
        self.assert_invalid(lambda: index_library_graph(library))

    def test_deep_valid_graph_stops_at_projection_node_bound(self):
        path = '/original-synthetic/pdk.scs'
        spellings = ['S%04d' % number for number in range(1024)]
        nodes = []
        for number, spelling in enumerate([None, *spellings]):
            key = spelling.lower() if spelling is not None else None
            nodes.append({'node_id': _digest([path, key]), 'path': path, 'section': spelling, 'section_key': key,
                'master_coverage': ['synthetic_device'], 'include_targets': [[path, spellings[number].lower()]] if number < 1024 else [],
                'unknown_codes': [], 'tmi_references': []})
        includes = [{'from': path, 'from_section': node['section'], 'line': number + 1,
                     'to': path, 'section': spellings[number], 'role': 'model'}
                    for number, node in enumerate(nodes[:-1])]
        library = {'roots': ['/original-synthetic'], 'includes': includes, 'files': [{'path': path, 'sha256': 'a' * 64,
            'bytes': 1, 'sections': spellings, 'roles': ['model']}], 'declarations': [{'path': path, 'line': 1,
            'section': None, 'name': 'synthetic_device', 'kind': 'model', 'model_type': 'bsim4',
            'ports': [], 'port_count': None, 'exact_name_match': True}], 'section_graph': nodes}
        result = project_scope_graph(index_library_graph(library), entry_path=path, section_ids_raw=[nodes[0]['node_id']])
        self.assertEqual(result['scope']['node_count'], 1024)
        self.assertIn('scope_graph_closure_node_limit', {row['code'] for row in result['missing']})

    def test_oversized_graph_edges_and_namespace_fail_before_projection(self):
        for field, count in (('section_graph', 4097), ('declarations', 32769)):
            library = copy.deepcopy(self.library); library[field] = [library[field][0]] * count
            self.assert_invalid(lambda: index_library_graph(library))
        library = copy.deepcopy(self.library)
        library['section_graph'][0]['include_targets'] = [[str(self.entry), None]] * 8193
        self.assert_invalid(lambda: index_library_graph(library))


if __name__ == '__main__': unittest.main()

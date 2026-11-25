"""Independent adversarial scope-facade checks with original synthetic inputs."""
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
from spiceowl.integrations.model_dependencies import build_model_audit_script
from spiceowl.onboarding import pdk_scope as scope
from tests.test_pdk_library import library_scan, requirements


SERVER = {'hostname': 'synthetic-scope-host', 'username': 'fixture-user', 'uid': 1234}
NAMES = ['n_device', 'p_device', 'cap_device']


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def rehash_library(library):
    library['library_id'] = digest({key: value for key, value in library.items() if key != 'library_id'})
    return library


def rehash_selection(selection):
    selection['public']['selection_id'] = digest({'schema': selection['schema'], 'private': selection['private'],
        'public': {key: value for key, value in selection['public'].items() if key != 'selection_id'}})
    return selection


def write_library(root, *, tmi=False, primitive=False):
    root.mkdir()
    (root / 'entry.scs').write_text('simulator lang=spectre\n'
        'section MOS\ninclude "mos.scs" section=Core\n' +
        ('.option tmipath="tmi"\n' if tmi else '') + 'endsection MOS\n'
        'section CAP\ninclude "cap.scs"\nendsection CAP\n'
        'section Prelude\nparameters tnom=27\nendsection Prelude\n')
    text = ('model n_device bsim4 type=n\nmodel p_device bsim4 type=p\n' if primitive else
            'subckt n_device d g s b\nends n_device\nsubckt p_device d g s b\nends p_device\n')
    (root / 'mos.scs').write_text('section Core\n' + text + 'endsection Core\n')
    (root / 'cap.scs').write_text('subckt cap_device a b\nC0 (a b) capacitor c=1p\nends cap_device\n')
    if tmi:
        binary = root / 'tmi' / 'Synthetic64' / 'libTMImodel.so'
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b'original synthetic opaque library bytes\x00')


def choices(library, *, label='entry.scs', sections=('MOS', 'CAP')):
    entry = next(row for row in scope.library_scope_catalog(library)['items'] if row['label'] == label)
    catalog = scope.library_scope_catalog(library, entry_id=entry['entry_id'])
    ids = [next(row['section_id'] for row in catalog['items'] if row['section'] == name) for name in sections]
    return entry['entry_id'], ids


def project(library, **changes):
    entry_id, section_ids = choices(library)
    args = {'requirements': requirements(NAMES), 'server_identity': SERVER,
            'entry_id': entry_id, 'section_ids': section_ids}
    args.update(changes)
    return scope.project_library_scopes(library, **args)


def local_audit(selection):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(build_model_audit_script(selection['private']['audit_config']), {})
    return json.loads(output.getvalue())


@unittest.skipIf(sys.platform == "win32", "Executes remote Linux POSIX-path PDK scanner against local temp dirs")
class PdkScopeAdversarialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); cls.addClassCleanup(cls.temp.cleanup)
        cls.base = Path(cls.temp.name).resolve()
        cls.root = cls.base / 'owner-private-synthetic-models'; write_library(cls.root)
        cls.library = library_scan(cls.root)
        cls.selection = project(cls.library)
        cls.audit = local_audit(cls.selection)
        cls.tmi_root = cls.base / 'synthetic-tmi'; write_library(cls.tmi_root, tmi=True)
        cls.tmi_library = library_scan(cls.tmi_root)

    def assert_private_absent(self, value):
        encoded = json.dumps(value)
        self.assertNotIn(str(self.base), encoded)
        def inspect(item):
            if isinstance(item, dict):
                self.assertFalse({'path', 'entry_path', 'source_path', 'directory', 'roots',
                                  'server_identity', 'audit_config', 'expected_files'} & set(item))
                for child in item.values(): inspect(child)
            elif isinstance(item, list):
                for child in item: inspect(child)
        inspect(value)

    def test_real_projection_and_audit_comparison_are_io_free_and_do_not_authorize(self):
        original = copy.deepcopy(self.library)
        with (patch('builtins.open', side_effect=AssertionError('No filesystem IO')),
              patch('subprocess.run', side_effect=AssertionError('No process launch')),
              patch('socket.create_connection', side_effect=AssertionError('No network IO'))):
            catalog = scope.library_scope_catalog(self.library)
            selected = project(self.library)
            comparison = scope.verify_scope_selection_audit(selected, self.audit,
                audit_config=selected['private']['audit_config'])
        self.assertEqual(selected['public']['status'], 'requires_audit')
        self.assertEqual(comparison['status'], 'matched', comparison['missing'])
        self.assertFalse(comparison['remote_freshness_verified'])
        self.assertTrue(comparison['human_review_required'])
        for public in (catalog, selected['public'], comparison):
            self.assertFalse(public['ready_to_run']); self.assert_private_absent(public)
        self.assertFalse(selected['public']['model_contracts_reused'])
        self.assertEqual(selected['public']['scientific_compatibility'], 'not_evaluated')
        self.assertEqual(self.library, original)

    def test_catalog_pagination_uses_returned_identities_and_current_offset_bound(self):
        seen = []; offset = 0
        while offset is not None:
            page = scope.library_scope_catalog(self.library, offset=offset, limit=1)
            self.assert_private_absent(page)
            seen.extend(row['entry_id'] for row in page['items']); offset = page['next_offset']
        self.assertEqual(len(seen), len(set(seen)))
        for offset in (4096, 16384):
            tail = scope.library_scope_catalog(self.library, offset=offset, limit=1)
            self.assertEqual(tail['items'], []); self.assertIsNone(tail['next_offset'])
        for arguments in ({'offset': -1}, {'offset': 16385}, {'offset': True},
                          {'limit': 0}, {'limit': 129}, {'limit': False}):
            with self.subTest(arguments=arguments), self.assertRaises(DomainError):
                scope.library_scope_catalog(self.library, **arguments)

    def test_foreign_entry_and_section_ids_cannot_cross_library_or_entry(self):
        entry, ids = choices(self.library)
        foreign_entry, foreign_ids = choices(self.library, label='mos.scs', sections=('Core',))
        changed = copy.deepcopy(self.library); changed['status'] = 'partial'
        changed['coverage']['complete_within_policy'] = False; rehash_library(changed)
        for library, e, selected in [(self.library, 'f' * 64, ids),
                                     (self.library, entry, foreign_ids),
                                     (self.library, foreign_entry, ids), (changed, entry, ids)]:
            with self.subTest(entry=e, selected=selected), self.assertRaises(DomainError):
                project(library, entry_id=e, section_ids=selected)
        with self.assertRaises(DomainError):
            scope.library_scope_catalog(changed, entry_id=entry)

    def test_section_selection_is_bounded_nonempty_unique_and_exact(self):
        _, ids = choices(self.library)
        for selected in ([], [ids[0], ids[0]], [ids[0]] * 65, ['f' * 64],
                         [ids[0].upper()], [None], [True], 'MOS'):
            with self.subTest(selected=selected), self.assertRaises(DomainError):
                project(self.library, section_ids=selected)
        reversed_scope = project(self.library, section_ids=list(reversed(ids)))
        self.assertEqual(reversed_scope['public']['sections'], ['CAP', 'MOS'])
        self.assertNotEqual(reversed_scope['public']['selection_id'], self.selection['public']['selection_id'])

    def test_candidate_capped_partial_inventory_still_projects_complete_observed_graph(self):
        limited = library_scan(self.root, limits={'max_candidates': 1})
        self.assertEqual(limited['status'], 'partial')
        self.assertEqual(len(limited['entry_candidates']), 1)
        selected = project(limited)
        self.assertEqual(selected['public']['status'], 'requires_audit')
        self.assertFalse(selected['public']['library_complete_within_policy'])
        self.assertEqual(selected['public']['covered_model_names'], NAMES)
        self.assertEqual(selected['private']['expected_files'], self.selection['private']['expected_files'])
        self.assertFalse(selected['public']['ready_to_run'])

    def test_graph_omission_cannot_be_turned_into_a_complete_selection(self):
        library = copy.deepcopy(self.library)
        library['detail_omissions'] = [{'field': 'declarations', 'reason': 'library_response_limit'}]
        rehash_library(library)
        selected = project(library)
        self.assertEqual(selected['public']['status'], 'blocked')
        self.assertIsNone(selected['private']['audit_config'])
        self.assertIn('scope_graph_omitted', {row['code'] for row in selected['public']['missing']})
        self.assert_private_absent(selected['public'])
        compared = scope.verify_scope_selection_audit(selected, self.audit,
            audit_config=self.selection['private']['audit_config'])
        self.assertEqual(compared['status'], 'mismatch')

    def test_rehashed_library_graph_namespace_claim_and_foreign_roots_are_rejected(self):
        for variant in ('coverage', 'foreign_file', 'section_id'):
            library = copy.deepcopy(self.library)
            node = next(row for row in library['section_graph'] if row['master_coverage'])
            if variant == 'coverage': node['master_coverage'].append('invented_device')
            elif variant == 'foreign_file': library['roots'] = [str(self.root / 'different-root')]
            else: node['node_id'] = 'f' * 64
            rehash_library(library)
            with self.subTest(variant=variant), self.assertRaises(DomainError):
                scope.library_scope_catalog(library)

    def test_rehashed_selection_identity_coverage_and_pin_mutations_are_rejected(self):
        mutations = {
            'source': lambda s: s['public'].__setitem__('source_sha256', 'f' * 64),
            'requirements': lambda s: s['private']['requirements'].__setitem__('required_masters', ['n_device']),
            'entry': lambda s: s['private'].__setitem__('entry_sha256', 'f' * 64),
            'section': lambda s: s['public']['section_ids'].__setitem__(0, 'f' * 64),
            'covered': lambda s: s['public'].__setitem__('covered_model_names', []),
            'count': lambda s: s['public']['scope'].__setitem__('node_count', 0),
            'missing_pin': lambda s: s['private']['expected_files'].pop(),
            'unknown_pin': lambda s: s['private']['expected_files'].append({'path': str(self.root / 'extra.scs'), 'sha256': 'f' * 64}),
            'root': lambda s: s['private'].__setitem__('roots', [str(self.root / 'foreign')]),
            'audit_main': lambda s: s['private']['audit_config'].__setitem__('main', str(self.root / 'cap.scs')),
        }
        for label, mutate in mutations.items():
            selected = copy.deepcopy(self.selection); mutate(selected); rehash_selection(selected)
            with self.subTest(variant=label), self.assertRaises(DomainError):
                scope.validate_scope_selection(selected)

    def test_rehashed_selection_cannot_add_authority_or_private_public_fields(self):
        for field, value in [('ready_to_run', True), ('fresh_audit_required', False),
                             ('model_contracts_reused', True), ('scientific_compatibility', 'verified'),
                             ('status', 'ready'), ('entry_path', str(self.root / 'entry.scs'))]:
            selected = copy.deepcopy(self.selection); selected['public'][field] = value; rehash_selection(selected)
            with self.subTest(field=field), self.assertRaises(DomainError):
                scope.validate_scope_selection(selected)

    def test_tmi_options_are_bound_to_source_host_library_and_ordered_scope(self):
        pending = project(self.tmi_library)
        self.assertEqual(pending['public']['status'], 'blocked')
        self.assertIn('scope_tmi_selection_required', {r['code'] for r in pending['public']['missing']})
        option = pending['public']['tmi_options'][0]['tmi_option_id']
        selected = project(self.tmi_library, tmi_option_id=option)
        self.assertEqual(selected['public']['status'], 'requires_audit')
        self.assert_private_absent(selected['public'])
        _, ids = choices(self.tmi_library)
        for change in ({'requirements': requirements(NAMES, source='c')},
                       {'server_identity': {**SERVER, 'hostname': 'another-host'}},
                       {'section_ids': list(reversed(ids))}):
            with self.subTest(change=change), self.assertRaises(DomainError):
                project(self.tmi_library, tmi_option_id=option, **change)
        for invalid in ('f' * 64, option.upper(), '/private/lib.so', True):
            with self.subTest(option=invalid), self.assertRaises(DomainError):
                project(self.tmi_library, tmi_option_id=invalid)
        with self.assertRaises(DomainError):
            project(self.library, tmi_option_id=option)

    def test_rehashed_tmi_selection_cannot_reuse_foreign_option_or_drop_selected_binary_pin(self):
        pending = project(self.tmi_library)
        option = pending['public']['tmi_options'][0]['tmi_option_id']
        good = project(self.tmi_library, tmi_option_id=option)
        for variant in ('option', 'pin', 'sha'):
            selected = copy.deepcopy(good)
            if variant == 'option': selected['public']['selected_tmi_option_id'] = 'f' * 64
            elif variant == 'pin':
                selected['private']['expected_files'] = [row for row in selected['private']['expected_files']
                                                         if not row['path'].endswith('/libTMImodel.so')]
            else: selected['private']['tmi_evidence'][0]['platform_candidates'][0]['sha256'] = 'f' * 64
            rehash_selection(selected)
            with self.subTest(variant=variant), self.assertRaises(DomainError):
                scope.validate_scope_selection(selected)

    def test_real_comparer_rejects_changed_pins_scopes_ports_and_configuration(self):
        for variant in ('pin', 'scope', 'ports', 'configuration'):
            audit = copy.deepcopy(self.audit); config = copy.deepcopy(self.selection['private']['audit_config'])
            if variant == 'pin': audit['files'][0]['sha256'] = 'f' * 64
            elif variant == 'scope': audit['files'][0]['selected_sections'] = []
            elif variant == 'ports': audit['master_declarations'][0]['ports'].reverse()
            else: config['sections'].reverse()
            result = scope.verify_scope_selection_audit(self.selection, audit, audit_config=config)
            with self.subTest(variant=variant):
                self.assertEqual(result['status'], 'mismatch')
                self.assertFalse(result['ready_to_run']); self.assert_private_absent(result)

    def test_primitive_models_not_supported_by_current_auditor_stay_unverified(self):
        root = self.base / 'primitive-models'; write_library(root, primitive=True)
        selected = project(library_scan(root))
        self.assertEqual(selected['public']['status'], 'requires_audit')
        audit = local_audit(selected)
        self.assertNotEqual(audit['missing_masters'], [])
        result = scope.verify_scope_selection_audit(selected, audit,
            audit_config=selected['private']['audit_config'])
        self.assertEqual(result['status'], 'mismatch')
        self.assertFalse(result['ready_to_run'])

    def test_finite_json_depth_and_byte_limits_fail_without_external_io(self):
        nested = 'leaf'
        for _ in range(26): nested = {'child': nested}
        for value in (float('nan'), float('inf'), nested, 'x' * (scope.MAX_BYTES + 1)):
            requested = requirements(NAMES); requested['untrusted_extension'] = value
            with self.subTest(type=type(value).__name__), self.assertRaises(DomainError):
                project(self.library, requirements=requested)


if __name__ == '__main__':
    unittest.main()

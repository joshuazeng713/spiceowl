"""Original metadata fixtures for bounded, scope-preserving LUT ingestion.

No fixture represents a remote scan, PDK, process qualification or launch grant.
Operation counters test scaling without hardware-sensitive timing assertions.
"""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from spiceowl.domain.common import DomainError
from spiceowl.onboarding import pdk_lut
from spiceowl.onboarding.pdk_discovery import library_candidate_models, validate_pdk_library
from spiceowl.onboarding.pdk_lut import PdkLut


ROOT = '/synthetic/lut-ingestion'
IDENTITY = {'hostname': 'synthetic-ingestion-host', 'username': 'synthetic-user', 'uid': 710}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def finish(library):
    library['library_id'] = digest({key: value for key, value in library.items() if key != 'library_id'})
    return validate_pdk_library(library)


def compact(library):
    result = copy.deepcopy(library)
    contracts = {}
    for candidate in result['entry_candidates']:
        rows = []
        for model in candidate['models']:
            contract_id = digest(model)
            contracts.setdefault(contract_id, model)
            rows.append({'name': model['name'], 'contract_id': contract_id})
        candidate['models'] = rows
    result.update(contract_storage='shared-model-contracts.v1', model_contracts=contracts)
    return finish(result)


def fixture(model_count=4, candidate_count=3, unrelated_count=0, *, shared=False):
    main, inactive = ROOT + '/device.scs', ROOT + '/unselected.scs'
    names = ['original_device_%03d' % index for index in range(model_count)]
    models = [{'name': name, 'polarity': 'n', 'confidence': 'verified',
        'ordered_terminal_roles': ['d', 'g', 's', 'b'], 'auto_prefill_allowed': True,
        'needs_confirmation': False, 'reason': 'explicit_supported_mos_type',
        'suggested_contract': {'polarity': 'n', 'terminals': ['d', 'g', 's', 'b']},
        'suggestion_basis': 'explicit_supported_mos_type',
        'evidence': [{'name': name, 'kind': 'declaration', 'path': main, 'section': None, 'line': index + 1}]
        } for index, name in enumerate(names)]
    declarations = [{'name': name, 'kind': 'model', 'model_type': 'bsim4', 'path': main,
        'section': None, 'line': index + 1, 'ports': [], 'port_count': None}
        for index, name in enumerate(names)]
    unselected = ['unselected_device_%04d' % index for index in range(unrelated_count)]
    declarations += [{'name': name, 'kind': 'model', 'model_type': 'bsim4', 'path': inactive,
        'section': None, 'line': index + 1, 'ports': [], 'port_count': None}
        for index, name in enumerate(unselected)]
    file_hash = hashlib.sha256(('original synthetic metadata fixture ' + ','.join(names)).encode()).hexdigest()
    candidates = []
    for index in range(candidate_count):
        section = 'original_corner_%03d' % index
        candidates.append({'candidate_id': digest({'original_fixture_candidate': index}),
            'entry_path': main, 'entry_sha256': file_hash, 'entry_role': 'definition_only',
            'dependency_paths': [main], 'dependency_scopes': [{'path': main, 'section': None},
                                                            {'path': main, 'section': section}],
            'section_candidates': [section], 'available_sections': [section],
            'master_coverage': names, 'missing_masters': [], 'coverage_complete': True,
            'unknown_codes': [], 'tmi_candidates': [], 'models': models,
            'additional_sections_may_be_required': True, 'ready_to_run': False})
    library = {'schema': 'spiceowl.pdk-library.v1', 'mode': 'full_library', 'roots': [ROOT],
        'files': [{'path': main, 'sha256': file_hash}, {'path': inactive, 'sha256': 'f' * 64}],
        'declarations': declarations, 'entry_candidates': candidates, 'audit_limits': {},
        'search_complete': not unrelated_count, 'status': 'partial' if unrelated_count else 'candidates',
        'coverage': {'kind': 'full_library_attempt', 'whole_pdk_qualified': False,
            'indexed_model_names': names, 'indexed_model_count': len(names),
            'observed_model_names': sorted(names + unselected), 'observed_model_count': len(names + unselected),
            'unindexed_model_names': unselected, 'unindexed_model_count': len(unselected),
            'complete_within_policy': not unrelated_count}}
    return compact(library) if shared else finish(library)


class CountedDeclaration(dict):
    name_reads = 0

    def __getitem__(self, key):
        if key == 'name':
            type(self).name_reads += 1
        return super().__getitem__(key)


class PdkLutIngestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def cache(self, name):
        cache = PdkLut(self.root / name)
        self.addCleanup(cache.close)
        return cache

    def rows(self, cache, cache_id):
        return [dict(row) for row in cache.connection.execute(
            'SELECT model_name,candidate_id,model,declarations FROM library_devices WHERE cache_id=? ORDER BY candidate_id,model_name',
            (cache_id,))]

    def test_inline_and_shared_contract_receipts_produce_same_inline_query_evidence(self):
        inline = fixture()
        shared = compact(inline)
        names = ['original_device_000', 'original_device_003']
        results = []
        stored_rows = []
        for number, library in enumerate((inline, shared)):
            cache = self.cache('equivalence_%d.db' % number)
            original = copy.deepcopy(library)
            metadata = cache.put_library(library, server_identity=IDENTITY, observed_at=100)
            self.assertEqual(library, original)
            stored = cache.connection.execute('SELECT library FROM library_observations WHERE cache_id=?',
                                             (metadata['cache_id'],)).fetchone()[0]
            self.assertEqual(json.loads(stored), library)
            result = cache.lookup_library_models(server_identity=IDENTITY, roots=[ROOT], now=101,
                required_masters=names, requirements={'schema': 'spiceowl.netlist-model-requirements.v1',
                    'source_sha256': 'a' * 64, 'required_masters': names, 'instances': []})
            self.assertEqual(result['status'], 'hit')
            self.assertEqual(result['discovery']['source_sha256'], 'a' * 64)
            self.assertTrue(all('contract_id' not in model for candidate in result['discovery']['entry_candidates']
                                for model in candidate['models']))
            self.assertEqual(cache.connection.execute('PRAGMA user_version').fetchone()[0], 3)
            results.append(result['discovery']['entry_candidates'])
            stored_rows.append(self.rows(cache, metadata['cache_id']))
        self.assertEqual(results[0], results[1])
        self.assertEqual(stored_rows[0], stored_rows[1])

    def test_declaration_membership_and_order_match_original_scope_predicate(self):
        library = fixture(model_count=1, candidate_count=2)
        path = library['files'][0]['path']
        name = library['entry_candidates'][0]['models'][0]['name']
        library['declarations'] = [
            {'name': 'unrequested_global', 'path': path, 'section': None, 'line': 1},
            {'name': name, 'path': path, 'section': 'slow', 'line': 2},
            {'name': name, 'path': path, 'section': 'fast', 'line': 3},
            {'name': 'unrequested_slow', 'path': path, 'section': 'slow', 'line': 4},
            {'name': 'unrequested_fast', 'path': path, 'section': 'fast', 'line': 5}]
        for candidate, section, polarity in zip(library['entry_candidates'], ('fast', 'slow'), ('n', 'p')):
            candidate['dependency_scopes'] = [{'path': path, 'section': section}]
            candidate['section_candidates'] = [section]
            candidate['available_sections'] = ['fast', 'slow']
            model = copy.deepcopy(candidate['models'][0])
            model.update(polarity=polarity, evidence=[{'name': name, 'path': path, 'section': section,
                                                     'line': 3 if section == 'fast' else 2}])
            candidate['models'] = [model]
        library = compact(finish(library))
        cache = self.cache('scopes.db')
        metadata = cache.put_library(library, server_identity=IDENTITY, observed_at=100)
        for candidate in library['entry_candidates']:
            scopes = {(row['path'], row['section']) for row in candidate['dependency_scopes']}
            expected = [row for row in library['declarations'] if
                (row['path'], row['section']) in scopes or row['section'] is None and row['path'] in candidate['dependency_paths']]
            stored = cache.connection.execute('SELECT candidate FROM library_entries WHERE cache_id=? AND candidate_id=?',
                (metadata['cache_id'], candidate['candidate_id'])).fetchone()[0]
            self.assertEqual(json.loads(stored)['library_declarations'], expected)
            row = cache.connection.execute('SELECT model,declarations FROM library_devices WHERE cache_id=? AND candidate_id=?',
                (metadata['cache_id'], candidate['candidate_id'])).fetchone()
            self.assertEqual(json.loads(row['declarations']), [row for row in expected if row['name'] == name])
            self.assertEqual(json.loads(row['model']), library_candidate_models(library, candidate)[0])

    def test_many_bindings_reuse_exact_contract_and_declaration_serializations(self):
        library = compact(fixture(model_count=192, candidate_count=64, unrelated_count=1024))
        library['declarations'] = [CountedDeclaration(row) for row in library['declarations']]
        cache = self.cache('many.db')
        CountedDeclaration.name_reads = 0
        model_ids = {id(model) for model in library['model_contracts'].values()}
        declaration_ids = {id(row) for row in library['declarations']}
        counts = {'models': 0, 'declaration_lists': 0}
        encode = pdk_lut._json

        def counted(value):
            if id(value) in model_ids:
                counts['models'] += 1
            if isinstance(value, list) and value and all(id(row) in declaration_ids for row in value):
                counts['declaration_lists'] += 1
            return encode(value)

        with patch.object(pdk_lut, '_json', side_effect=counted):
            metadata = cache.put_library(library, server_identity=IDENTITY, observed_at=100)
        self.assertEqual(cache.connection.execute('SELECT COUNT(*) FROM library_devices WHERE cache_id=?',
                         (metadata['cache_id'],)).fetchone()[0], 192 * 64)
        # Each candidate may visit its 192 active globals once; the 1024
        # unrelated declarations must not be rescanned for any model binding.
        self.assertLessEqual(CountedDeclaration.name_reads, 2 * len(library['declarations']) + 64 * 192)
        self.assertEqual(counts, {'models': 192, 'declaration_lists': 192})
        self.assertLess(cache.path.stat().st_size, pdk_lut.MAX_DATABASE_BYTES)

    def test_payload_bound_fails_before_writes_and_keeps_previous_observation(self):
        cache = self.cache('payload.db')
        old = fixture(model_count=1, candidate_count=1)
        saved = cache.put_library(old, server_identity=IDENTITY, observed_at=100)
        larger = compact(fixture(model_count=32, candidate_count=16))
        with patch.object(pdk_lut, 'MAX_DATABASE_BYTES', 32768), self.assertRaises(DomainError) as caught:
            cache.put_library(larger, server_identity=IDENTITY, observed_at=101)
        self.assertEqual(caught.exception.code, 'pdk_lut_storage')
        self.assertEqual(cache.connection.execute('SELECT COUNT(*) FROM library_observations').fetchone()[0], 1)
        self.assertEqual(cache.connection.execute('SELECT library_id FROM library_observations WHERE cache_id=?',
                         (saved['cache_id'],)).fetchone()[0], old['library_id'])

    def test_large_shared_receipt_indexes_61440_bindings_within_existing_database_limit(self):
        library = fixture(model_count=768, candidate_count=80, unrelated_count=1024, shared=True)
        cache = self.cache('large_shared.db')
        saved = cache.put_library(library, server_identity=IDENTITY, observed_at=100)
        self.assertEqual(cache.connection.execute('SELECT COUNT(*) FROM library_devices WHERE cache_id=?',
                         (saved['cache_id'],)).fetchone()[0], 61440)
        self.assertLess(cache.path.stat().st_size, pdk_lut.MAX_DATABASE_BYTES)
        self.assertEqual(cache.connection.execute('PRAGMA user_version').fetchone()[0], 3)

    def test_sqlite_page_limit_failure_rolls_back_new_rows(self):
        cache = self.cache('sqlite_limit.db')
        old = fixture(model_count=1, candidate_count=1)
        saved = cache.put_library(old, server_identity=IDENTITY, observed_at=100)
        pages = cache.connection.execute('PRAGMA page_count').fetchone()[0]
        cache.connection.execute('PRAGMA max_page_count=%d' % (pages + 1))
        larger = compact(fixture(model_count=32, candidate_count=16))
        with self.assertRaises(DomainError) as caught:
            cache.put_library(larger, server_identity=IDENTITY, observed_at=101)
        self.assertEqual(caught.exception.code, 'pdk_lut_storage')
        self.assertEqual(cache.connection.execute('SELECT COUNT(*) FROM library_observations').fetchone()[0], 1)
        self.assertEqual(cache.connection.execute('SELECT COUNT(*) FROM library_devices WHERE cache_id=?',
                         (saved['cache_id'],)).fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()

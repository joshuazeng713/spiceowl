"""Exact cache exports over original synthetic receipts, with no remote IO."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from spiceowl.domain.common import DomainError
from spiceowl.onboarding import pdk_discovery, pdk_lut
from spiceowl.onboarding.pdk_lut import PdkLut
from tests.test_pdk_lut_ingestion import fixture, finish, IDENTITY, ROOT


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


class PdkLibrarySnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'private-cache.sqlite3'
        self.library = fixture(shared=True)
        self.cache = PdkLut(self.path); self.addCleanup(self.cache.close)
        self.metadata = self.cache.put_library(self.library, server_identity=IDENTITY, observed_at=100)
        self.args = {'server_identity': IDENTITY, 'roots': [ROOT],
                     'library_id': self.metadata['library_id'],
                     'content_fingerprint': self.metadata['content_fingerprint'],
                     'cache_id': self.metadata['cache_id'], 'now': 101}

    def snapshot(self, **changes):
        return self.cache.lookup_library_snapshot(**{**self.args, **changes})

    def assert_non_authoritative(self, result):
        self.assertEqual(result['schema'], 'spiceowl.pdk-library-snapshot.v1')
        self.assertTrue(result['fresh_audit_required'])
        self.assertFalse(result['remote_freshness_verified'])
        self.assertFalse(result['ready_to_run'])
        self.assertEqual(result['missing'], [])

    def assert_corrupt(self, column, value, *, query=None):
        self.cache.connection.execute('SAVEPOINT snapshot_corruption')
        try:
            self.cache.connection.execute('UPDATE library_observations SET ' + column + '=? WHERE cache_id=?',
                                          (value, self.metadata['cache_id']))
            with self.assertRaises(DomainError):
                self.snapshot(**(query or {}))
        finally:
            self.cache.connection.execute('ROLLBACK TO snapshot_corruption')
            self.cache.connection.execute('RELEASE snapshot_corruption')

    def test_exact_original_library_metadata_and_reconstructed_identity_are_exported(self):
        original = copy.deepcopy(self.library)
        result = self.snapshot()
        self.assertEqual(result['status'], 'hit'); self.assertFalse(result['scan_required'])
        self.assertEqual(result['library'], original)
        record = copy.deepcopy(result['record']); age = record.pop('staleness')
        self.assertEqual(record, self.metadata)
        self.assertEqual(age, {'age_s': 1.0, 'max_age_s': 86400.0,
                              'state': 'within_local_age_policy', 'remote_content_current': 'unknown'})
        key = digest({'server_identity': IDENTITY, 'roots': [ROOT], 'mode': 'full_library'})
        self.assertEqual(record['cache_id'], digest({'scope_key': key, 'library_id': original['library_id'],
            'content_fingerprint': record['content_fingerprint'], 'observed_at': 100.0}))
        self.assert_non_authoritative(result)
        result['library']['coverage']['indexed_model_names'].clear()
        result['record']['files'].clear()
        self.assertEqual(self.snapshot()['library'], original)
        self.assertEqual(self.library, original)

    def test_read_only_export_performs_only_sql_selects_and_no_pdk_or_process_io(self):
        before = self.path.read_bytes(); statements = []
        with PdkLut(self.path, read_only=True) as reader:
            def authorize(action, _arg1, _arg2, _db, _trigger):
                return sqlite3.SQLITE_OK if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ,
                                                       sqlite3.SQLITE_FUNCTION) else sqlite3.SQLITE_DENY
            reader.connection.set_authorizer(authorize)
            reader.connection.set_trace_callback(statements.append)
            changes = reader.connection.total_changes
            with (patch('builtins.open', side_effect=AssertionError('No source file reads')),
                  patch('subprocess.run', side_effect=AssertionError('No process launch')),
                  patch('socket.create_connection', side_effect=AssertionError('No network IO')),
                  patch.object(Path, 'read_bytes', side_effect=AssertionError('No PDK bytes')),
                  patch.object(Path, 'read_text', side_effect=AssertionError('No PDK text'))):
                result = reader.lookup_library_snapshot(**self.args)
            self.assertEqual(result['library'], self.library)
            self.assertEqual(reader.connection.total_changes, changes)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(statements), 1)
        self.assertTrue(statements[0].lstrip().upper().startswith('SELECT '))
        self.assertNotIn('library_devices', statements[0])
        self.assertNotIn('library_entries', statements[0])

    def test_derived_index_corruption_does_not_replace_or_reconstruct_original_library(self):
        with self.cache.connection:
            self.cache.connection.execute('DELETE FROM library_devices')
            self.cache.connection.execute('DELETE FROM library_entries')
            self.cache.connection.execute('DELETE FROM library_root_scopes')
        result = self.snapshot()
        self.assertEqual(result['status'], 'hit')
        self.assertEqual(result['library'], self.library)

    def test_request_identity_mismatches_are_misses_without_exposing_the_record(self):
        variants = [{'library_id': 'f' * 64}, {'content_fingerprint': 'f' * 64}, {'cache_id': 'f' * 64},
                    {'server_identity': {**IDENTITY, 'hostname': 'different-host'}},
                    {'server_identity': {**IDENTITY, 'username': 'different-user'}},
                    {'server_identity': {**IDENTITY, 'uid': IDENTITY['uid'] + 1}},
                    {'server_identity': {**IDENTITY, 'host_key_sha256': 'f' * 64}},
                    {'roots': [ROOT + '/child']}, {'roots': [ROOT, '/synthetic/additional-root']}]
        for changes in variants:
            with self.subTest(changes=changes):
                result = self.snapshot(**changes)
                self.assertEqual(result['status'], 'miss')
                self.assertIsNone(result['library']); self.assertIsNone(result['record'])
                self.assertTrue(result['scan_required']); self.assert_non_authoritative(result)

    def test_three_exact_digest_arguments_are_required_and_refresh_is_unsupported(self):
        for field in ('cache_id', 'library_id', 'content_fingerprint'):
            arguments = dict(self.args); arguments.pop(field)
            with self.subTest(field=field), self.assertRaises(TypeError):
                self.cache.lookup_library_snapshot(**arguments)
            for value in (None, 'f' * 63, 'f' * 65, 'F' * 64, 'not-an-id', True, 1):
                with self.subTest(field=field, value=value), self.assertRaises(DomainError):
                    self.snapshot(**{field: value})
        with self.assertRaises(TypeError): self.snapshot(refresh=True)

    def test_future_and_expired_observations_keep_metadata_but_no_library(self):
        for now, maximum, status, reason in [(99, 86400, 'stale', 'clock_skew'),
                                            (111, 10, 'stale', 'expired'),
                                            (110, 10, 'hit', 'within_local_age_policy'),
                                            (100, 0, 'hit', 'within_local_age_policy')]:
            with self.subTest(now=now, maximum=maximum):
                result = self.snapshot(now=now, max_age_s=maximum)
                self.assertEqual(result['status'], status)
                self.assertEqual(result['record']['staleness']['state'], reason)
                self.assertEqual(result['record']['cache_id'], self.metadata['cache_id'])
                self.assertEqual(result['scan_required'], status == 'stale')
                self.assertEqual(result['library'], None if status == 'stale' else self.library)
                self.assert_non_authoritative(result)
        for changes in ({'now': -1}, {'now': float('inf')}, {'max_age_s': True}, {'max_age_s': -1}):
            with self.subTest(changes=changes), self.assertRaises(DomainError): self.snapshot(**changes)

    def test_snapshot_never_falls_back_to_a_newer_observation(self):
        newer = self.cache.put_library(self.library, server_identity=IDENTITY, observed_at=101)
        result = self.snapshot(now=102, max_age_s=1)
        self.assertEqual(result['status'], 'stale')
        self.assertEqual(result['record']['cache_id'], self.metadata['cache_id'])
        self.assertIsNone(result['library'])
        fresh = self.snapshot(cache_id=newer['cache_id'], now=102, max_age_s=1)
        self.assertEqual(fresh['status'], 'hit')
        self.assert_corrupt('metadata', '{}')
        self.assertEqual(self.snapshot(cache_id=newer['cache_id'])['status'], 'hit')

    def test_complete_canonical_or_logical_root_sets_only_without_mixed_aliases(self):
        library = copy.deepcopy(self.library)
        library['roots'] = [ROOT, '/synthetic/second-root']
        library['requested_roots'] = ['/synthetic/logical-one', '/synthetic/logical-two']
        finish(library)
        stored = self.cache.put_library(library, server_identity=IDENTITY, observed_at=100)
        request = {**self.args, **{key: stored[key] for key in ('cache_id', 'library_id', 'content_fingerprint')}}
        for roots in (library['roots'], list(reversed(library['roots'])), library['requested_roots'],
                      list(reversed(library['requested_roots']))):
            result = self.cache.lookup_library_snapshot(**{**request, 'roots': roots})
            self.assertEqual(result['status'], 'hit')
            self.assertEqual(result['library'], library)
            self.assertEqual(result['record']['root_resolutions'], [
                {'requested_root': logical, 'canonical_root': canonical}
                for logical, canonical in zip(library['requested_roots'], library['roots'])])
        for roots in ([ROOT], ['/synthetic/logical-one'], [ROOT, '/synthetic/logical-two'],
                      [*library['roots'], '/synthetic/unobserved']):
            result = self.cache.lookup_library_snapshot(**{**request, 'roots': roots})
            self.assertEqual(result['status'], 'miss')
            self.assertIsNone(result['library']); self.assertIsNone(result['record'])

    def test_sql_identity_or_observation_time_corruption_is_not_a_cache_miss(self):
        for field in ('scope_key', 'library_id', 'content_fingerprint'):
            with self.subTest(field=field): self.assert_corrupt(field, 'f' * 64)
        self.assert_corrupt('observed_at', 200)
        self.assert_corrupt('observed_at', -1)
        self.assert_corrupt('cache_id', 'f' * 64, query={'cache_id': 'f' * 64})

    def test_metadata_must_exactly_match_original_receipt_and_sql_fields(self):
        mutations = {
            'cache_id': lambda r: r.__setitem__('cache_id', 'f' * 64),
            'library_id': lambda r: r.__setitem__('library_id', 'f' * 64),
            'fingerprint': lambda r: r.__setitem__('content_fingerprint', 'f' * 64),
            'identity': lambda r: r['server_identity'].__setitem__('hostname', 'different-host'),
            'roots': lambda r: r.__setitem__('roots', [ROOT + '/other']),
            'time': lambda r: r.__setitem__('observed_at', 100.5),
            'time_type': lambda r: r.__setitem__('observed_at', 100),
            'files': lambda r: r['files'][0].__setitem__('sha256', 'e' * 64),
            'coverage': lambda r: r['coverage'].__setitem__('indexed_model_count', 0),
            'limits': lambda r: r['audit_limits'].__setitem__('extra', 1),
            'policy': lambda r: r['search_policy'].__setitem__('extra', 1),
            'complete': lambda r: r.__setitem__('search_complete', False),
            'authority': lambda r: r.__setitem__('ready_to_run', True),
            'extra': lambda r: r.__setitem__('invented', 'field'),
            'fake_alias': lambda r: r.__setitem__('requested_roots', ['/synthetic/foreign-alias']),
        }
        for label, mutate in mutations.items():
            metadata = copy.deepcopy(self.metadata); mutate(metadata)
            with self.subTest(variant=label): self.assert_corrupt('metadata', encoded(metadata))

    def test_invalid_duplicate_or_nonfinite_stored_json_is_rejected(self):
        for column, original in [('metadata', self.metadata), ('library', self.library)]:
            raw = encoded(original)
            key = next(iter(original))
            duplicate = '{' + json.dumps(key) + ':' + json.dumps(original[key]) + ',' + raw[1:]
            for value in ('{broken', '[]', duplicate, '{"not_finite":NaN}'):
                with self.subTest(column=column, value=value[:50]): self.assert_corrupt(column, value)

    def test_original_library_hash_and_shared_contract_integrity_are_checked(self):
        changed = copy.deepcopy(self.library); changed['status'] = 'tampered'
        self.assert_corrupt('library', encoded(changed))
        changed = copy.deepcopy(self.library); changed['model_contracts'].pop(next(iter(changed['model_contracts'])))
        changed['library_id'] = digest({key: value for key, value in changed.items() if key != 'library_id'})
        self.assert_corrupt('library', encoded(changed))
        changed = copy.deepcopy(self.library); changed['files'][0]['sha256'] = 'f' * 64
        changed['library_id'] = digest({key: value for key, value in changed.items() if key != 'library_id'})
        self.assert_corrupt('library', encoded(changed))

    def test_library_and_complete_export_byte_limits_reject_instead_of_truncating(self):
        library_bytes = len(encoded(self.library).encode())
        with patch.object(pdk_discovery, 'MAX_LIBRARY_RESULT_BYTES', library_bytes):
            self.assertEqual(self.snapshot()['library'], self.library)
        with patch.object(pdk_discovery, 'MAX_LIBRARY_RESULT_BYTES', library_bytes - 1):
            with self.assertRaises(DomainError): self.snapshot()
        exported = self.snapshot(); export_bytes = len(encoded(exported).encode())
        with patch.object(pdk_lut, 'MAX_LIBRARY_SNAPSHOT_BYTES', export_bytes):
            self.assertEqual(self.snapshot(), exported)
        with patch.object(pdk_lut, 'MAX_LIBRARY_SNAPSHOT_BYTES', export_bytes - 1):
            with self.assertRaises(DomainError) as raised: self.snapshot()
            self.assertEqual(raised.exception.code, 'pdk_lut_snapshot_size')
        self.assertEqual(self.snapshot(), exported)

    def test_legacy_read_only_database_returns_miss_without_migration(self):
        old = self.root / 'legacy.sqlite3'
        connection = sqlite3.connect(old)
        connection.execute('PRAGMA user_version=2'); connection.close(); os.chmod(old, 0o600)
        before = old.read_bytes()
        with PdkLut(old, read_only=True) as reader:
            result = reader.lookup_library_snapshot(**self.args)
            self.assertEqual(result['status'], 'miss'); self.assertIsNone(result['library'])
        self.assertEqual(old.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()

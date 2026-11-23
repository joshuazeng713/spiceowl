"""Bounded resource-display snapshots; observations never imply a reservation."""
import copy
from datetime import timedelta
import json
import unittest
from unittest.mock import MagicMock, patch

from spiceowl.domain.common import DomainError
from spiceowl.hosts import HostRegistry
from spiceowl.integrations.circuitgit_pool import CircuitGitPool
from test_placement import NOW, host


METRICS = ('cpu_busy_percent', 'load_1m', 'cpu_count', 'memory_available_bytes', 'memory_total_bytes')


class ResourceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.records = {'host-a': host(), 'host-b': host('host-b')}
        self.failed = set()
        self.after_scan = None
        self.registry = MagicMock(spec=HostRegistry)
        self.registry.inspect.side_effect = self.inspect
        self.registry.scan.side_effect = self.scan
        constructor = patch('spiceowl.integrations.circuitgit_pool.HostRegistry', return_value=self.registry)
        constructor.start(); self.addCleanup(constructor.stop)
        clock = patch('spiceowl.integrations.circuitgit_pool._now', return_value=NOW + timedelta(seconds=1))
        clock.start(); self.addCleanup(clock.stop)
        self.pool = CircuitGitPool('/fictional/private-state')

    def inspect(self, identifier):
        if identifier not in self.records:
            raise DomainError('/PRIVATE/nonpublic', 'not_found')
        return copy.deepcopy(self.records[identifier])

    def scan(self, ids, *, simulator=None):
        self.assertEqual(simulator, 'spectre')
        for identifier in ids:
            if identifier not in self.failed:
                self.records[identifier]['last_resources']['observed_at'] = (NOW + timedelta(milliseconds=10)).isoformat()
        if self.after_scan:
            self.after_scan(ids)
        return {'started_at': NOW.isoformat(), 'completed_at': (NOW + timedelta(milliseconds=20)).isoformat(),
                'simulation_actions': False,
                'results': [{'host_id': identifier, 'error': {'code': 'private', 'message': '/PRIVATE/config'} if identifier in self.failed else None} for identifier in ids]}

    def snapshot(self, ids=None):
        return self.pool.scan_resources(['host-a', 'host-b'] if ids is None else ids)

    def assert_unknown(self, row):
        self.assertFalse(row['reachable'])
        self.assertEqual(row['error_code'], 'scan_failed')
        for key in (*METRICS, 'observed_at'):
            self.assertIsNone(row[key], key)

    def test_explicit_scan_only_returns_safe_observation_fields_in_selected_order(self):
        self.records['host-a']['credential'] = 'PRIVATE-secret'
        value = self.snapshot(['host-b', 'host-a'])
        self.registry.scan.assert_called_once_with(['host-b', 'host-a'], simulator='spectre')
        self.assertEqual(set(value), {'schema', 'checked_at', 'hosts'})
        self.assertEqual(value['schema'], 'spiceowl.host-pool.snapshot.v1')
        self.assertEqual([row['host_id'] for row in value['hosts']], ['host-b', 'host-a'])
        for row in value['hosts']:
            self.assertEqual(set(row), {'host_id', 'label', 'reachable', 'observed_at', 'error_code', *METRICS})
            self.assertTrue(row['reachable'])
            self.assertEqual(row['observed_at'], (NOW + timedelta(milliseconds=10)).isoformat())
            self.assertIsNone(row['error_code'])
        self.assertNotIn('PRIVATE', json.dumps(value))
        self.assertNotIn('ssh_', json.dumps(value))
        self.assertNotIn('executable', json.dumps(value))
        self.assertEqual(json.loads(json.dumps(value, allow_nan=False)), value)
        self.registry.add.assert_not_called(); self.registry.list.assert_not_called()
        self.registry.recommend.assert_not_called()

    def test_failed_scan_erases_old_fresh_metrics_without_zero_filling_or_retry(self):
        self.failed = {'host-a'}
        value = self.snapshot()
        self.assert_unknown(value['hosts'][0])
        self.assertTrue(value['hosts'][1]['reachable'])
        self.assertNotIn('PRIVATE', json.dumps(value))
        self.registry.scan.assert_called_once()

    def test_all_failed_hosts_return_unknown_rows_without_fake_idle_capacity(self):
        self.failed = set(self.records)
        value = self.snapshot()
        for row in value['hosts']:
            self.assert_unknown(row)

    def test_missing_boolean_negative_and_nonfinite_metrics_stay_null(self):
        for raw in ({}, dict.fromkeys(METRICS), dict.fromkeys(METRICS, True), dict.fromkeys(METRICS, -1),
                    dict.fromkeys(METRICS, float('nan')), dict.fromkeys(METRICS, float('inf')),
                    dict.fromkeys(METRICS, '0'), dict.fromkeys(METRICS, 2**10000)):
            self.records['host-a']['last_resources']['resources'] = raw
            with self.subTest(raw=raw):
                row = self.snapshot(['host-a'])['hosts'][0]
                self.assertTrue(row['reachable'])
                self.assertTrue(all(row[key] is None for key in METRICS))
                json.dumps(row, allow_nan=False)

    def test_unverified_transport_identity_or_resource_error_invalidates_all_metrics(self):
        base = copy.deepcopy(self.records['host-a']['last_resources'])
        for changes in ({'transport': 'unknown'}, {'identity': {'verified': False}},
                        {'error': {'code': 'resource_unavailable', 'message': '/PRIVATE/details'}}):
            self.records['host-a']['last_resources'] = copy.deepcopy(base) | changes
            self.assert_unknown(self.snapshot(['host-a'])['hosts'][0])

    def test_all_ids_are_validated_before_a_scan_or_implicit_discovery(self):
        for ids in (None, (), [], ['host-a', 'host-a'], ['host-a', 'missing'], [True], ['invalid/id'], ['host-a'] * 33):
            with self.subTest(ids=ids), self.assertRaises(DomainError):
                self.pool.scan_resources(ids)
        self.registry.scan.assert_not_called(); self.registry.add.assert_not_called(); self.registry.list.assert_not_called()

    def test_scan_exception_is_redacted_and_never_retried(self):
        self.registry.scan.side_effect = OSError('/PRIVATE/ssh/config')
        with self.assertRaises(DomainError) as error:
            self.snapshot()
        self.assertNotIn('PRIVATE', str(error.exception))
        self.registry.scan.assert_called_once()

    def test_malformed_or_incomplete_scan_receipt_never_adopts_old_success(self):
        valid = self.scan(['host-a'], simulator='spectre')
        invalid = [{}, valid | {'results': []}, valid | {'results': [{'host_id': 'unrequested', 'error': None}]},
                   valid | {'simulation_actions': True}, valid | {'completed_at': 'not-a-time'},
                   valid | {'completed_at': (NOW - timedelta(seconds=1)).isoformat()},
                   valid | {'completed_at': (NOW + timedelta(days=1)).isoformat()},
                   valid | {'results': [{'host_id': 'host-a', 'error': None}, {'host_id': 'host-a', 'error': None}]}]
        self.registry.scan.side_effect = None
        for receipt in invalid:
            self.registry.scan.return_value = receipt
            with self.subTest(receipt=receipt), self.assertRaises(DomainError):
                self.snapshot(['host-a'])

    def test_pre_scan_future_or_unparseable_observation_is_not_a_successful_new_scan(self):
        for timestamp in ((NOW - timedelta(seconds=1)).isoformat(), (NOW + timedelta(days=1)).isoformat(), 'invalid', None):
            self.after_scan = lambda ids: self.records['host-a']['last_resources'].update(observed_at=timestamp)
            with self.subTest(timestamp=timestamp):
                self.assert_unknown(self.snapshot(['host-a'])['hosts'][0])

    def test_out_of_range_cpu_fields_and_impossible_memory_do_not_break_display_schema(self):
        for changed in ({'cpu_busy_percent': 101}, {'cpu_count': 0}, {'cpu_count': 2.5},
                        {'cpu_count': 2**53}, {'memory_total_bytes': 1.5},
                        {'memory_available_bytes': 2, 'memory_total_bytes': 1}):
            self.records['host-a'] = host(**changed)
            row = self.snapshot(['host-a'])['hosts'][0]
            with self.subTest(changed=changed):
                if 'memory_available_bytes' in changed:
                    self.assertTrue(row['memory_available_bytes'] is None or row['memory_total_bytes'] is None)
                else:
                    self.assertIsNone(row[next(iter(changed))])

    def test_a_foreign_observation_cannot_supply_healthy_metrics_for_this_host(self):
        for field, value in [('host_id', 'host-b'), ('identity', {'verified': True, 'hostname': 'other.invalid', 'username': 'other-user'})]:
            self.records['host-a']['last_resources'] = copy.deepcopy(host()['last_resources'])
            self.after_scan = lambda ids: self.records['host-a']['last_resources'].update({field: value})
            with self.subTest(field=field):
                self.assert_unknown(self.snapshot(['host-a'])['hosts'][0])

    def test_synthetic_wrong_scope_or_changed_profile_cannot_claim_a_live_resource_scan(self):
        for changed in ({'synthetic': True}, {'scope': 'connectivity_only'}):
            self.records['host-a'] = host()
            self.after_scan = lambda ids: self.records['host-a']['last_resources'].update(changed)
            self.assert_unknown(self.snapshot(['host-a'])['hosts'][0])
        self.records['host-a'] = host()
        self.after_scan = lambda ids: self.records['host-a'].update(expected_user='changed-user')
        self.assert_unknown(self.snapshot(['host-a'])['hosts'][0])

    def test_healthy_zero_utilization_is_preserved_without_zero_filling_unknown_memory(self):
        self.records['host-a'] = host(cpu_busy_percent=0.0, load_1m=0.0, cpu_count=1,
                                       memory_available_bytes=None, memory_total_bytes=2**53 - 1)
        row = self.snapshot(['host-a'])['hosts'][0]
        self.assertTrue(row['reachable'])
        self.assertEqual(row['cpu_busy_percent'], 0)
        self.assertEqual(row['load_1m'], 0)
        self.assertEqual(row['cpu_count'], 1)
        self.assertIsNone(row['memory_available_bytes'])
        self.assertEqual(row['memory_total_bytes'], 2**53 - 1)


if __name__ == '__main__':
    unittest.main()

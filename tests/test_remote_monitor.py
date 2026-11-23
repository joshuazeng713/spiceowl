"""Offline monitor fixtures. No SSH, simulator, OCEAN, or model process runs."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from spiceowl.monitoring.remote import MAX_EVENTS, RuntimeMonitor, atomic


class FakeRunner:
    def __init__(self, root):
        self.root = root
        self.root.mkdir()
        self.records = {}
        self.events = []
        self.status_calls = []
        self.collect_calls = []
        self.status_entered = threading.Event()
        self.status_release = None
        self.collect_error = None
        self.clock = 0
        self.dispatch = Mock(side_effect=AssertionError('Never dispatch'))
        self.postprocess = Mock(side_effect=AssertionError('Never launch OCEAN'))
        self.list = Mock(side_effect=AssertionError('Never discover unarmed runs'))

    def add(self, run_id='fixture-one', state='submitted'):
        self.path(run_id).mkdir(exist_ok=True)
        self.records[run_id] = {'run_id': run_id, 'state': state, 'source_commit': 'a' * 40,
                                'execution_site_sha256': 'b' * 64, 'remote_state': 'running',
                                'simulator_exit_code': None, 'observed_at': '2026-11-23T00:00:00+00:00'}
        return run_id

    def path(self, run_id):
        return self.root / run_id

    def read(self, run_id):
        return copy.deepcopy(self.records[run_id])

    def status(self, run_id):
        self.status_calls.append(run_id)
        self.status_entered.set()
        if self.status_release is not None:
            if not self.status_release.wait(2):
                raise TimeoutError('fixture release timed out')
        event = self.events.pop(0) if self.events else 'connected'
        if event == 'exception':
            raise TimeoutError('private fixture detail')
        result = self.read(run_id)
        if event == 'disconnected':
            result['status_transport'] = 'disconnected'
        elif event == 'stale':
            result.pop('status_transport', None)
        else:
            self.clock += 1
            result.update(status_transport='connected', remote_status_exit=0,
                          observed_at=(datetime(2026, 11, 23, tzinfo=timezone.utc) + timedelta(seconds=self.clock)).isoformat())
            if event == 'terminal':
                result.update(state='simulator_complete_pending_measurements',
                              remote_state='simulator_complete_pending_measurements', simulator_exit_code=0)
            else:
                result.update(state='running_observed', remote_state='running', simulator_exit_code=None)
        self.records[run_id] = result
        return copy.deepcopy(result)

    def collect(self, run_id):
        self.collect_calls.append(run_id)
        if self.collect_error is not None:
            raise self.collect_error
        self.records[run_id].update(collection_phase='complete', collection={'sha256': 'c' * 64})
        return self.read(run_id)


class RuntimeMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runner = FakeRunner(Path(self.temp.name).resolve() / 'runs')
        self.run_id = self.runner.add()
        self.monitor = RuntimeMonitor(self.runner)
        self.addCleanup(self.monitor.stop)

    def watch(self, **kwargs):
        return self.monitor.watch(self.run_id, **kwargs)

    def test_constructor_and_snapshots_are_passive_and_arm_is_explicit(self):
        self.assertFalse((self.runner.root / '.monitor').exists())
        self.assertFalse(self.monitor.snapshot()['running'])
        with self.assertRaises(ValueError):
            self.monitor.poll_once(self.run_id)
        queued = self.runner.add('queued-fixture', 'queued')
        with self.assertRaises(ValueError):
            self.monitor.watch(queued)
        self.assertEqual(self.runner.status_calls, [])

    def test_duplicate_arm_keeps_one_persisted_watch(self):
        first = self.watch()
        second = self.watch()
        self.assertEqual(first['watch_id'], second['watch_id'])
        self.assertEqual(len(self.monitor.snapshot()['watches']), 1)
        registry = json.loads((self.runner.root / '.monitor/registry.json').read_text())
        self.assertEqual(list(registry['watches']), [self.run_id])

    def test_disconnect_backoff_reconnect_preserves_execution_separately(self):
        initial = self.watch()['execution']
        self.runner.events = ['disconnected', 'exception', 'connected']
        for delay in (30, 60):
            receipt = self.monitor.poll_once(self.run_id)
            self.assertEqual(receipt['execution'], initial)
            self.assertEqual(receipt['observation']['state'], 'disconnected')
            self.assertEqual(receipt['observation']['backoff_seconds'], delay)
            self.assertNotIn('private fixture detail', json.dumps(receipt))
        receipt = self.monitor.poll_once(self.run_id)
        self.assertEqual(receipt['observation']['state'], 'connected')
        self.assertEqual(receipt['observation']['consecutive_failures'], 0)
        self.assertEqual(receipt['events'][-1]['kind'], 'reconnected')
        self.assertEqual(self.runner.status_calls, [self.run_id] * 3)
        self.runner.dispatch.assert_not_called()

    def test_legacy_stale_timestamp_never_counts_as_fresh_connection(self):
        self.watch()
        self.runner.events = ['connected', 'stale']
        connected = self.monitor.poll_once(self.run_id)
        stale = self.monitor.poll_once(self.run_id)
        self.assertEqual(stale['execution'], connected['execution'])
        self.assertEqual(stale['observation']['last_success_at'], connected['observation']['last_success_at'])
        self.assertEqual(stale['observation']['state'], 'disconnected')
        self.assertEqual(stale['observation']['error_code'], 'observation_not_refreshed')

    def test_terminal_stops_polling_and_default_does_not_collect(self):
        self.watch()
        self.runner.events = ['terminal']
        receipt = self.monitor.poll_once(self.run_id)
        self.assertEqual(receipt['watch_state'], 'terminal')
        self.assertEqual(receipt['execution']['simulator_exit_code'], 0)
        self.monitor.poll_once(self.run_id)
        self.assertEqual(self.runner.status_calls, [self.run_id])
        self.assertEqual(self.runner.collect_calls, [])
        self.runner.postprocess.assert_not_called()

    def test_explicit_collection_claim_is_once_even_after_restart_and_rearm(self):
        self.watch(allow_collect=True)
        self.runner.events = ['terminal']
        receipt = self.monitor.poll_once(self.run_id)
        self.assertEqual(receipt['collection']['state'], 'complete')
        restarted = RuntimeMonitor(self.runner)
        self.addCleanup(restarted.stop)
        restarted.poll_once(self.run_id)
        restarted.watch(self.run_id, allow_collect=False)
        restarted.watch(self.run_id, allow_collect=True)
        self.runner.events = ['terminal']
        restarted.poll_once(self.run_id)
        self.assertEqual(self.runner.collect_calls, [self.run_id])
        self.runner.postprocess.assert_not_called()

    def test_collection_uncertainty_is_durable_and_never_replayed(self):
        self.watch(allow_collect=True)
        self.runner.collect_error = TimeoutError('fixture collection outcome unknown')
        self.runner.events = ['terminal']
        receipt = self.monitor.poll_once(self.run_id)
        self.assertEqual(receipt['collection']['state'], 'outcome_unknown')
        restarted = RuntimeMonitor(self.runner)
        self.addCleanup(restarted.stop)
        restarted.poll_once(self.run_id)
        self.assertEqual(self.runner.collect_calls, [self.run_id])

    def test_interrupted_persisted_claim_is_unknown_without_remote_io(self):
        receipt = self.watch(allow_collect=True)
        receipt['watch_state'] = 'terminal'
        receipt['collection'] = {'state': 'claimed', 'run_id': self.run_id, 'claimed_at': '2026-11-23T00:00:00+00:00'}
        atomic(self.runner.path(self.run_id) / 'monitor.json', receipt)
        restarted = RuntimeMonitor(self.runner)
        self.addCleanup(restarted.stop)
        result = restarted.poll_once(self.run_id)
        self.assertEqual(result['collection']['state'], 'outcome_unknown')
        self.assertEqual(self.runner.status_calls, [])
        self.assertEqual(self.runner.collect_calls, [])

    def test_restart_restores_explicit_registry_without_discovering_other_runs(self):
        self.watch()
        self.runner.add('never-armed-fixture')
        restarted = RuntimeMonitor(self.runner, interval_seconds=.05, max_backoff_seconds=.1)
        self.addCleanup(restarted.stop)
        restarted.start()
        self.assertTrue(self.runner.status_entered.wait(1))
        self.assertTrue(restarted.stop(timeout=1))
        self.assertEqual(set(self.runner.status_calls), {self.run_id})
        self.runner.list.assert_not_called()

    def test_concurrent_poll_does_not_duplicate_status(self):
        self.watch()
        self.runner.status_release = threading.Event()
        results = []
        thread = threading.Thread(target=lambda: results.append(self.monitor.poll_once(self.run_id)))
        thread.start()
        try:
            self.assertTrue(self.runner.status_entered.wait(1))
            self.monitor.poll_once(self.run_id)
            self.assertEqual(self.runner.status_calls, [self.run_id])
        finally:
            self.runner.status_release.set()
            thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 1)

    def test_stop_is_bounded_and_does_not_collect_after_inflight_status(self):
        self.watch(allow_collect=True)
        self.runner.events = ['terminal']
        self.runner.status_release = threading.Event()
        self.monitor.start()
        try:
            self.assertTrue(self.runner.status_entered.wait(1))
            self.assertFalse(self.monitor.stop(timeout=.01))
        finally:
            self.runner.status_release.set()
        self.assertTrue(self.monitor.stop(timeout=1))
        self.assertEqual(self.runner.collect_calls, [])
        self.runner.dispatch.assert_not_called()

    def test_disarm_during_status_keeps_the_explicit_stop_decision(self):
        self.watch()
        self.runner.status_release = threading.Event()
        thread = threading.Thread(target=self.monitor.poll_once, args=(self.run_id,))
        thread.start()
        try:
            self.assertTrue(self.runner.status_entered.wait(1))
            self.monitor.unwatch(self.run_id)
        finally:
            self.runner.status_release.set(); thread.join(1)
        self.assertEqual(self.monitor.snapshot(self.run_id)['watch_state'], 'stopped')
        self.assertEqual(self.monitor.snapshot()['watches'], [])

    def test_only_one_background_owner_can_start_per_runner(self):
        second = RuntimeMonitor(self.runner)
        self.addCleanup(second.stop)
        self.assertTrue(self.monitor.start())
        self.assertFalse(self.monitor.start())
        with self.assertRaises(ValueError):
            second.start()
        self.assertTrue(self.monitor.stop(timeout=1))
        self.assertTrue(second.start())
        self.assertTrue(second.stop(timeout=1))

    def test_event_history_and_backoff_are_bounded(self):
        self.watch()
        self.runner.events = ['disconnected'] * (MAX_EVENTS + 8)
        for _ in self.runner.events[:]:
            receipt = self.monitor.poll_once(self.run_id)
        self.assertEqual(len(receipt['events']), MAX_EVENTS)
        self.assertGreater(receipt['event_sequence'], MAX_EVENTS)
        self.assertEqual(receipt['observation']['backoff_seconds'], 300)

    def test_run_binding_changes_block_status_without_inventing_execution_failure(self):
        initial = self.watch()['execution']
        self.runner.records[self.run_id]['source_commit'] = 'c' * 40
        receipt = self.monitor.poll_once(self.run_id)
        self.assertEqual(receipt['execution'], initial)
        self.assertEqual(receipt['observation']['state'], 'blocked')
        self.assertEqual(self.runner.status_calls, [])

    def test_partial_watch_update_cannot_enable_collection_or_poll(self):
        receipt = self.watch()
        receipt['allow_collect'] = True
        atomic(self.runner.path(self.run_id) / 'monitor.json', receipt)
        with self.assertRaises(ValueError):
            self.monitor.poll_once(self.run_id)
        with self.assertRaises(ValueError):
            self.watch()
        self.assertEqual(self.runner.status_calls, [])
        self.assertEqual(self.runner.collect_calls, [])

    def test_status_response_binding_change_is_not_applied_or_collected(self):
        initial = self.watch(allow_collect=True)['execution']
        self.runner.events = ['terminal']
        original = self.runner.status
        def changed(run_id):
            result = original(run_id)
            result['source_commit'] = 'd' * 40
            return result
        self.runner.status = changed
        receipt = self.monitor.poll_once(self.run_id)
        self.assertEqual(receipt['execution'], initial)
        self.assertEqual(receipt['observation']['state'], 'blocked')
        self.assertEqual(self.runner.collect_calls, [])

    def test_background_restart_retains_saved_backoff(self):
        self.watch()
        self.runner.events = ['disconnected']
        self.monitor.poll_once(self.run_id)
        self.runner.status_entered.clear()
        restarted = RuntimeMonitor(self.runner)
        self.addCleanup(restarted.stop)
        restarted.start()
        self.assertFalse(self.runner.status_entered.wait(.03))
        self.assertTrue(restarted.stop(timeout=1))
        self.assertEqual(self.runner.status_calls, [self.run_id])

    def test_limits_and_bad_timing_rejected(self):
        for value in (0, -1, float('nan'), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                RuntimeMonitor(self.runner, interval_seconds=value)
        with patch('spiceowl.monitoring.remote.MAX_WATCHES', 1):
            self.watch()
            other = self.runner.add('fixture-two')
            with self.assertRaises(ValueError):
                self.monitor.watch(other)


if __name__ == '__main__':
    unittest.main(verbosity=2)

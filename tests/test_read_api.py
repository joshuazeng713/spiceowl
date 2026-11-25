"""Neutral SDK projections with synthetic stored rows and no active adapters."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import patch

from spiceowl import read_api
from spiceowl.domain.common import DomainError
from spiceowl.mcp import views as legacy_views
from spiceowl.storage.readonly import MAX_RECORDS


PRIVATE = 'SYNTHETIC_PRIVATE_READ_API_VALUE'
OBSERVED = '2026-11-13T10:00:00+00:00'
READ_AT = '2026-11-24T18:00:00+00:00'
GATEWAY = 'synthetic-gateway'
NATIVE_RUN = 'synthetic-native-run'
CALLS = [('status', ()), ('list_runs', ()), ('get_run', ('synthetic-run',)),
         ('list_connections', ()), ('list_hosts', ()), ('list_ic_gateways', ()),
         ('list_ic_runs', (GATEWAY,)), ('get_ic_run', (GATEWAY, NATIVE_RUN))]


def seed(directory):
    """Create authored fixture rows without importing Controller or Store."""
    directory.mkdir()
    path = directory / 'spiceowl.sqlite3'
    events = [{'event_id': 'event-%d' % i, 'sequence': i, 'kind': 'execution.observed',
               'observed_at': OBSERVED, 'message': 'Untrusted saved observation. ' * 30,
               'facts': {'synthetic': True, 'required_signals_present': False, 'api_key': PRIVATE}}
              for i in range(read_api.MAX_EVENTS + 2)]
    run = {'run_id': 'synthetic-run', 'experiment_id': 'experiment-one', 'attempt_id': 'attempt-one',
           'label': 'Synthetic saved run', 'scenario': 'disconnect', 'synthetic': True,
           'created_at': OBSERVED, 'updated_at': OBSERVED, 'execution': 'running',
           'observation': 'unknown', 'measurement_validity': 'indeterminate',
           'specification_result': 'not_evaluated', 'progress': 0.5, 'finished': False,
           'execution_identity': {'kind': 'synthetic', 'executor': 'mock',
                                  'native_id': 'synthetic-native', 'synthetic': True, 'private_path': PRIVATE},
           'events': events, 'input_manifest': {'private_path': PRIVATE},
           'resource_history': [{'sequence': i, 'memory_mb': 32, 'simulated_time_us': 1, 'raw': PRIVATE}
                                for i in range(read_api.MAX_RESOURCES + 2)]}
    connection = {'connection_id': 'fake-connection', 'label': 'Saved fake connection',
                  'kind': 'agent', 'backend': 'fake_agent', 'synthetic': True, 'enabled': True,
                  'revision': 1, 'scenario': 'success', 'created_at': OBSERVED,
                  'api_key': PRIVATE, 'config': {'secret': PRIVATE}}
    host = {'host_id': 'synthetic-host', 'label': 'Saved host', 'synthetic': False,
            'ssh_target': PRIVATE, 'expected_host': PRIVATE, 'expected_user': PRIVATE,
            'tools': [{'kind': 'spectre', 'executable': '/private/' + PRIVATE}],
            'last_probe': {'synthetic': False, 'scope': 'connectivity_only', 'observed_at': OBSERVED,
                'transport': 'reachable', 'identity': {'verified': True, 'hostname': PRIVATE},
                'tools': [{'kind': 'spectre', 'status': 'available', 'version': '21.1.0.509.isr12'}],
                'error': {'message': PRIVATE}, 'stdout': PRIVATE},
            'last_resources': {'synthetic': False, 'observed_at': OBSERVED, 'transport': 'reachable',
                'identity': {'verified': True, 'hostname': PRIVATE},
                'resources': {'cpu_count': 4, 'cpu_busy_percent': 25, 'memory_available_bytes': 4096},
                'error': {'message': PRIVATE}}}
    gateway = {'id': GATEWAY, 'label': 'Saved IC gateway', 'backend': 'ic-workbench',
               'project_id': 'synthetic-project', 'created_at': OBSERVED,
               'base_url': 'https://example.invalid/' + PRIVATE, 'api_key': PRIVATE,
               'observation': {'state': 'recorded', 'observed_at': OBSERVED,
                               'error': {'message': PRIVATE}}}
    native = {'schema': 'spiceowl.ic-run.v1', 'backend': 'ic-workbench', 'source': 'stored_observations',
              'run_id': NATIVE_RUN, 'gateway_id': GATEWAY, 'project_id': 'synthetic-project',
              'original_project_id': 'original-project', 'association_basis': 'recorded_project_id',
              'source_commit': 'a' * 40, 'compiled_inputs_sha256': 'b' * 64, 'configuration': 'tran',
              'synthetic': True, 'source_read_at': OBSERVED, 'latest_observed_at': OBSERVED,
              'execution': {'state': 'succeeded', 'raw_state': 'completed', 'exit_code': 0,
                            'observed_at': OBSERVED, 'command': PRIVATE},
              'observation': {'state': 'recorded', 'observed_at': OBSERVED},
              'data': {'state': 'collected', 'raw_retained': True, 'local_path': PRIVATE},
              'specification': {'state': 'reported', 'metrics': [
                  {'name': 'synthetic metric', 'value': 1.0, 'unit': 'V', 'passed': False,
                   'scope': 'authored synthetic result', 'private_path': PRIVATE}] * 18},
              'issues': [{'code': 'synthetic_fixture', 'message': PRIVATE}] * 10,
              'artifacts': [{'name': 'waveforms.json', 'bytes': 12, 'sha256': 'c' * 64,
                  'url': '/api/jobs/' + NATIVE_RUN + '/artifacts/waveforms.json', 'local_path': PRIVATE},
                  {'name': 'waveforms.csv', 'url': 'https://example.invalid/' + PRIVATE}],
              'waveform_run_id': NATIVE_RUN, 'submission_key': PRIVATE}
    rows = [('run', 'synthetic-run', run), ('connection', 'fake-connection', connection),
            ('connection', 'real-connection', {'kind': 'model', 'backend': 'real', 'api_key': PRIVATE}),
            ('host', 'synthetic-host', host), ('ic_gateway', GATEWAY, gateway),
            ('ic_run:' + GATEWAY, NATIVE_RUN, native)]
    db = sqlite3.connect(path)
    try:
        with db:
            db.executescript('''CREATE TABLE records (kind TEXT,id TEXT,body TEXT,PRIMARY KEY(kind,id));
                CREATE TABLE turns (turn_id TEXT,status TEXT,question TEXT);
                CREATE TABLE events (id TEXT,body TEXT);
                PRAGMA user_version=1;''')
            db.executemany('INSERT INTO records VALUES (?,?,?)', [(kind, key, json.dumps(value)) for kind, key, value in rows])
            db.execute('INSERT INTO turns VALUES (?,?,?)', ('pending-turn', 'running', PRIVATE))
    finally:
        db.close()
    return path


class ReadApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        dirname = 'state #mode=rw%20synthetic' if os.name == 'nt' else 'state ?mode=rw#synthetic'
        self.directory = Path(self.temp.name) / dirname
        self.path = seed(self.directory)

    def read_all(self, views):
        return {name: getattr(views, name)(*arguments) for name, arguments in CALLS}

    def test_all_eight_methods_are_identical_through_legacy_alias_and_neutral_sdk(self):
        self.assertIs(legacy_views.ReadViews, read_api.ReadViews)
        self.assertIs(legacy_views._probe, read_api._probe)
        self.assertIs(legacy_views._ic_detail, read_api._ic_detail)
        self.assertEqual(legacy_views.MAX_EVENTS, read_api.MAX_EVENTS)
        self.assertEqual(legacy_views.MAX_RESOURCES, read_api.MAX_RESOURCES)
        with patch.object(read_api, 'utcnow', return_value=READ_AT):
            neutral = self.read_all(read_api.open_read_views(self.directory))
            legacy = self.read_all(legacy_views.ReadViews(read_api.ReadStore(self.directory)))
        self.assertEqual(neutral, legacy)
        self.assertEqual(neutral['status']['mode'], 'read_only_sdk')
        self.assertEqual(neutral['status']['agent_channel']['owner'], 'circuitgit')
        self.assertEqual(neutral['status']['agent_channel']['connection_status'], 'not_checked')
        self.assertFalse(neutral['status']['agent_channel']['local_inference_enabled'])
        self.assertEqual(neutral['status']['counts']['turn_states']['running'], 1)

    def test_fresh_sdk_module_import_and_all_methods_work_without_mcp_or_active_dependencies(self):
        # Load the SDK source afresh under an isolated module name, so a cached
        # import cannot hide an accidental optional dependency in this test.
        spec = importlib.util.spec_from_file_location('spiceowl._read_api_import_test', read_api.__file__)
        module = importlib.util.module_from_spec(spec)
        original_import = __import__; requested = []
        forbidden = ('mcp', 'spiceowl.mcp', 'spiceowl.service', 'spiceowl.storage.sqlite',
                     'spiceowl.agent.native', 'spiceowl.agent.questions', 'spiceowl.integrations.ic_workbench')
        def without_active_dependencies(name, *args, **kwargs):
            requested.append(name)
            if any(name == prefix or name.startswith(prefix + '.') for prefix in forbidden):
                raise ModuleNotFoundError('Absent or forbidden optional/active dependency', name=name)
            return original_import(name, *args, **kwargs)
        with (patch('builtins.__import__', side_effect=without_active_dependencies),
              patch('subprocess.Popen', side_effect=AssertionError('No process')),
              patch('subprocess.run', side_effect=AssertionError('No process')),
              patch('os.system', side_effect=AssertionError('No shell')),
              patch('socket.create_connection', side_effect=AssertionError('No network'))):
            spec.loader.exec_module(module)
            results = self.read_all(module.open_read_views(self.directory))
        self.assertEqual(set(results), {name for name, _ in CALLS})
        self.assertTrue(all(result['read_only'] for result in results.values()))
        self.assertFalse(any(name == prefix or name.startswith(prefix + '.')
                             for name in requested for prefix in forbidden))
        self.assertNotIn(PRIVATE, json.dumps(results))

    def test_factory_and_all_views_leave_database_files_permissions_and_pending_turn_unchanged(self):
        before = self.path.read_bytes()
        before_mode = stat.S_IMODE(self.path.stat().st_mode)
        before_files = sorted(path.name for path in self.directory.iterdir())
        connect = sqlite3.connect; connections = []
        def read_connection(*args, **kwargs):
            connections.append((args, kwargs))
            self.assertEqual(kwargs.get('uri'), True)
            self.assertTrue(args[0].endswith('?mode=ro'))
            self.assertNotIn('?mode=rw', args[0])
            db = connect(*args, **kwargs)
            def authorize(action, first, second, _database, _trigger):
                if action in (sqlite3.SQLITE_READ, sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION,
                               sqlite3.SQLITE_TRANSACTION): return sqlite3.SQLITE_OK
                if action == sqlite3.SQLITE_PRAGMA and first in ('query_only', 'trusted_schema', 'user_version'):
                    return sqlite3.SQLITE_OK
                return sqlite3.SQLITE_DENY
            db.set_authorizer(authorize)
            return db
        with (patch('spiceowl.storage.readonly.sqlite3.connect', side_effect=read_connection),
              patch('pathlib.Path.mkdir', side_effect=AssertionError('No mkdir')),
              patch('os.chmod', side_effect=AssertionError('No chmod')),
              patch('spiceowl._platform.lock_file', side_effect=AssertionError('No controller lock')),
              patch('subprocess.Popen', side_effect=AssertionError('No process'))):
            self.read_all(read_api.open_read_views(self.directory))
        self.assertTrue(connections)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), before_mode)
        self.assertEqual(sorted(path.name for path in self.directory.iterdir()), before_files)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT status FROM turns WHERE turn_id=?', ('pending-turn',)).fetchone()[0], 'running')

    def test_existing_redaction_event_resource_and_ic_evidence_bounds_are_preserved(self):
        with patch.object(read_api, 'utcnow', return_value=READ_AT):
            results = self.read_all(read_api.open_read_views(self.directory))
        self.assertNotIn(PRIVATE, json.dumps(results))
        for result in results.values():
            self.assertTrue(result['read_only'])
            self.assertEqual(result['read_at'], READ_AT)
            self.assertEqual(result['data_trust'], 'untrusted_evidence')
            self.assertEqual(result['source'], 'stored_local_state')
        run = results['get_run']['run']
        self.assertEqual(len(run['events']), read_api.MAX_EVENTS)
        self.assertEqual(len(run['resource_history']), read_api.MAX_RESOURCES)
        self.assertTrue(run['events_truncated']); self.assertTrue(run['resources_truncated'])
        self.assertTrue(all(len(row['message']) <= 500 for row in run['events']))
        self.assertEqual((run['execution'], run['observation']), ('running', 'unknown'))
        self.assertEqual(run['updated_at'], OBSERVED)
        self.assertEqual(len(results['list_connections']['connections']), 1)
        self.assertEqual(results['list_hosts']['hosts'][0]['last_probe']['observed_at'], OBSERVED)
        self.assertEqual(results['list_hosts']['hosts'][0]['last_probe']['tools'][0]['version'], '21.1.0.509.isr12')
        native = results['get_ic_run']['run']
        self.assertEqual(len(native['specification']['metrics']), 16)
        self.assertEqual(len(native['issues']), 8)
        self.assertEqual(len(native['artifacts']), 1)
        self.assertEqual(native['measurement']['state'], 'indeterminate')
        self.assertFalse(results['get_ic_run']['active_query_performed'])
        self.assertFalse(results['get_ic_run']['simulation_actions'])

    def test_every_stored_list_retains_the_fixed_record_bound(self):
        with sqlite3.connect(self.path) as db:
            originals = db.execute('SELECT kind,body FROM records WHERE id IN (?,?,?,?,?)',
                ('synthetic-run', 'fake-connection', 'synthetic-host', GATEWAY, NATIVE_RUN)).fetchall()
            for kind, body in originals:
                for i in range(MAX_RECORDS + 2):
                    row = json.loads(body); identifier = 'extra-%d' % i
                    field = {'run': 'run_id', 'connection': 'connection_id', 'host': 'host_id',
                             'ic_gateway': 'id', 'ic_run:' + GATEWAY: 'run_id'}[kind]
                    row[field] = identifier
                    db.execute('INSERT INTO records VALUES (?,?,?)', (kind, identifier, json.dumps(row)))
        views = read_api.open_read_views(self.directory)
        for method, args, key in [('list_runs', (), 'runs'), ('list_connections', (), 'connections'),
                                  ('list_hosts', (), 'hosts'), ('list_ic_gateways', (), 'gateways'),
                                  ('list_ic_runs', (GATEWAY,), 'runs')]:
            with self.subTest(method=method):
                result = getattr(views, method)(*args)
                self.assertEqual(result['limit'], MAX_RECORDS)
                self.assertEqual(len(result[key]), MAX_RECORDS)
                self.assertTrue(result['truncated'])

    def test_missing_store_does_not_create_it_and_calls_cannot_change_fixed_directory(self):
        missing = Path(self.temp.name) / 'never-created'
        with self.assertRaises(DomainError) as error: read_api.open_read_views(missing)
        self.assertEqual(error.exception.code, 'storage_missing')
        self.assertFalse(missing.exists())
        views = read_api.open_read_views(self.directory)
        for name, arguments in CALLS:
            with self.subTest(method=name), self.assertRaises(TypeError):
                getattr(views, name)(*arguments, directory=str(missing))
        for name, arguments in [('get_run', ('../private',)), ('get_run', (None,)),
                                ('list_ic_runs', ('../private',)),
                                ('get_ic_run', (GATEWAY, '../private'))]:
            with self.subTest(method=name, arguments=arguments), self.assertRaises(DomainError):
                getattr(views, name)(*arguments)

    def test_returned_agent_channel_metadata_is_fresh_and_does_not_claim_connection(self):
        views = read_api.open_read_views(self.directory)
        first = views.status(); first['agent_channel']['connection_status'] = 'invented-connected'
        second = views.status()
        self.assertEqual(second['agent_channel']['connection_status'], 'not_checked')
        self.assertEqual(second['agent_channel']['transport'], 'codex_app_server')
        for capability in ('ssh', 'probe', 'inference', 'simulation_actions', 'controller_recovery', 'ic_gateway_sync'):
            self.assertFalse(second['capabilities'][capability])


if __name__ == '__main__':
    unittest.main()

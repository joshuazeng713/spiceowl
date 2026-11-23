"""Offline and fake-loopback workflow checks; no SSH, solver or model requests."""
from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from spiceowl.cli.main import main
from spiceowl.domain.common import DomainError
from spiceowl.integrations.authority import verify
from spiceowl.integrations.live import LiveSimulationClient, _safe_public
from spiceowl.integrations.spectre_report import build_spectre_report
from tests.test_ic_workbench import lookup_fixture, run_fixture
from tests.test_live_client import HEALTH, RUNTIME


PLAN_ID = 'b' * 64
PREPARE = {'recipe_id': 'inverter-tran', 'source_commit': 'a' * 40}
PLAN = {'schema': 'ic.experiment.plan.v1', 'project_id': 'fixture-project',
        **PREPARE, 'plan_id': PLAN_ID, 'state': 'prepared', 'missing': [],
        'dependency_summary': {'file_count': 3, 'pre_pinned': True}}
JOB = {'schema': 'ic.experiment.job.v1', 'project_id': 'fixture-project',
       'job_id': 'fixture-job', 'run_id': 'fixture-job', 'configuration': PREPARE['recipe_id'],
       'source_commit': PREPARE['source_commit'], 'plan_id': PLAN_ID, 'state': 'queued',
       'launch_attempts': 0, 'execution_target': 'synthetic-host', 'automatic_retry': False}


def complete_job():
    report = build_spectre_report('Spectre completes with 0 errors, 0 warnings, and 0 notices.\n',
        execution_state='completed', exit_code=0, run_id=JOB['job_id'], log_complete=True,
        acceptance={'data_validity': 'valid', 'specification': 'fail'})
    measurement = {'schema': 'spiceowl.experiment-measurement.v1',
                   'recipe_sha256': 'c' * 64, 'psf_sha256': 'd' * 64,
                   'data_validity': 'valid', 'specification': 'fail', 'sample_count': 3,
                   'time_span_s': [0, 1e-9], 'voltage_ranges_v': {'CLK': [0.0, 1.0], 'OUT': [0.5, 1.0]},
                   'windows': [{'kind': 'input_high_output_low', 'sample_count': 2,
                                'output_range_v': [0.5, 1.0], 'output_threshold_passed': False}],
                   'delays': {'summary': {'tPHL': {'count': 1, 'min_s': 1e-11, 'max_s': 1e-11, 'mean_s': 1e-11}}},
                   'overall_qualified_acceptance': None}
    return JOB | {'state': 'completed', 'inspection_state': 'complete', 'report': report, 'measurements': measurement}


class ExperimentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = b'fixture-key-for-workflow-only' * 3
        self.key_path = self.root / 'authority.key'
        self.key_path.write_bytes(self.key)
        self.key_path.chmod(0o600)
        self.config_path = self.root / 'client.json'
        self.configure('http://127.0.0.1:18881')

    def configure(self, origin):
        self.config_path.write_text(json.dumps({'schema': 'spiceowl.live-client.v1', 'base_url': origin,
            'project_id': 'fixture-project', 'authority_key_file': str(self.key_path)}))
        self.config_path.chmod(0o600)
        self.client = LiveSimulationClient(self.config_path)

    def journal(self):
        return json.loads(self.client._journal_path.read_text())['requests']

    def test_prepare_no_key_or_dispatch_and_blocked_conditions_are_preserved(self):
        blocked = PLAN | {'state': 'blocked', 'plan_id': None,
                          'missing': [{'field': 'supply', 'code': 'missing_field', 'message': 'Declare supply voltage.'}]}
        with patch.object(self.client, '_request', side_effect=[HEALTH, blocked]) as request:
            result = self.client.workflow(**PREPARE)
        self.assertEqual(result['state'], 'blocked')
        self.assertIsNone(result['plan_id'])
        self.assertEqual(result['missing'], blocked['missing'])
        wire = json.loads(request.call_args.args[2])
        self.assertEqual(wire['operation'], 'prepare')
        self.assertNotIn('idempotency_key', wire)
        self.assertFalse(self.client._journal_path.exists())

    def test_runtime_projects_only_registered_recipe_metadata_and_permissions(self):
        available = {'configured': True, 'recipes': [{'recipe_id': 'synthetic-inverter', 'template': 'inverter-tran'}],
                     'prepare': True, 'run': True, 'inspect': False}
        response = copy.deepcopy(RUNTIME) | {'experiments': copy.deepcopy(available)}
        response['experiments']['recipes'][0]['source_paths'] = ['/private/source.scs']
        with patch.object(self.client, '_request', side_effect=[HEALTH, response]):
            public = self.client.runtime()
        self.assertEqual(public['experiments'], available)
        self.assertFalse(self.client._journal_path.exists())

    def test_exact_arguments_and_prepare_response_identity_checked_before_run(self):
        cases = [('prepare', {'recipe_id': 'inverter-tran', 'source_commit': 'main'}),
                 ('prepare', PREPARE | {'command': 'echo bad'}), ('run', {'plan_id': 'bad'}),
                 ('inspect', {'job_id': '../path'}), ('inspect', {'job_id': 'run.1'})]
        with patch.object(self.client, '_request', side_effect=AssertionError('network')):
            for op, args in cases:
                with self.subTest(op=op, args=args), self.assertRaises(DomainError):
                    self.client.execute(op, args)
            with self.assertRaises(DomainError):
                self.client.execute('prepare', PREPARE, 'extra-key')
            with self.assertRaises(DomainError):
                self.client.execute('run', {'plan_id': PLAN_ID}, 'different-key')
        for changed in (PLAN | {'source_commit': 'e' * 40}, PLAN | {'plan_id': None},
                        PLAN | {'state': 'blocked'}, PLAN | {'project_id': 'other'}):
            with patch.object(self.client, '_request', side_effect=[HEALTH, changed]) as request:
                result = self.client.workflow(**PREPARE)
            self.assertEqual(result['state'], 'uncertain')
            self.assertEqual(request.call_count, 2)

    def test_run_key_is_fixed_durable_and_completed_invocation_is_deduplicated(self):
        key = 'experiment-' + PLAN_ID
        def request(method, path, *args, **kwargs):
            if method == 'GET':
                return HEALTH
            self.assertEqual(self.journal()[0]['state'], 'pending')
            self.assertEqual(self.journal()[0]['key'], key)
            self.assertEqual(json.loads(args[0])['idempotency_key'], key)
            return JOB
        with patch.object(self.client, '_request', side_effect=request):
            self.assertEqual(self.client.execute('run', {'plan_id': PLAN_ID}), JOB)
        restart = LiveSimulationClient(self.config_path)
        with patch.object(restart, '_request', side_effect=AssertionError('must not repeat POST')):
            self.assertEqual(restart.execute('run', {'plan_id': PLAN_ID}), JOB)
            self.assertEqual(restart.execute('run', {'plan_id': PLAN_ID}, key), JOB)
        self.assertEqual(len(self.journal()), 1)

    def test_disconnect_preserves_plan_key_and_lookup_never_enables_replay(self):
        with patch.object(self.client, '_request', side_effect=[HEALTH, PLAN, HEALTH, DomainError('lost', 'live_transport')]):
            result = self.client.workflow(**PREPARE)
        self.assertEqual(result['state'], 'uncertain')
        self.assertEqual(result['plan_id'], PLAN_ID)
        self.assertEqual(result['submission_key'], 'experiment-' + PLAN_ID)
        self.assertIsNone(result['job_id'])
        # Lookup validates project/key mapping without confusing plan arguments
        # for the legacy configuration + source identity tuple.
        for native in (None, run_fixture()):
            with patch.object(self.client, '_request', side_effect=[HEALTH, lookup_fixture(result['submission_key'], native)]):
                found = self.client.lookup_submission(result['submission_key'])
            self.assertIs(found['replay_permitted'], False)
            self.assertEqual(self.journal()[0]['state'], 'pending')
        restart = LiveSimulationClient(self.config_path)
        with patch.object(restart, '_request', side_effect=AssertionError('no replay')), self.assertRaises(DomainError) as raised:
            restart.execute('run', {'plan_id': PLAN_ID})
        self.assertEqual(raised.exception.code, 'live_outcome_unknown')

    def test_completed_workflow_retains_failed_science_and_all_five_dimensions(self):
        done = complete_job()
        with patch.object(self.client, '_request', side_effect=[HEALTH, PLAN, HEALTH, JOB, HEALTH, done]):
            result = self.client.workflow(**PREPARE)
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(result['job']['report']['functional_performance']['status'], 'fail')
        self.assertEqual(result['job']['report'], done['report'])
        self.assertEqual(result['job']['measurements'], done['measurements'])

    def test_uncertain_inspection_stops_immediately_and_keeps_original_ids(self):
        uncertain = JOB | {'inspection_state': 'uncertain'}
        with patch.object(self.client, '_request', side_effect=[HEALTH, PLAN, HEALTH, JOB, HEALTH, uncertain]) as request:
            result = self.client.workflow(**PREPARE)
        self.assertEqual(result['state'], 'uncertain')
        self.assertEqual(result['job_id'], JOB['job_id'])
        self.assertEqual(result['plan_id'], PLAN_ID)
        self.assertEqual(request.call_count, 6)

    def test_inspection_cannot_switch_the_job_or_replace_plan(self):
        wrong = JOB | {'inspection_state': 'working', 'plan_id': 'e' * 64}
        with patch.object(self.client, '_request', side_effect=[HEALTH, PLAN, HEALTH, JOB, HEALTH, wrong]):
            result = self.client.workflow(**PREPARE)
        self.assertEqual(result['state'], 'uncertain')
        self.assertEqual(result['job']['plan_id'], PLAN_ID)
        wrong = JOB | {'inspection_state': 'working', 'job_id': 'other', 'run_id': 'other'}
        with patch.object(self.client, '_request', side_effect=[HEALTH, wrong]), self.assertRaises(DomainError):
            self.client.execute('inspect', {'job_id': JOB['job_id']})

    def test_timeout_returns_original_ids_without_reprepare_or_rekey(self):
        now, operations, sleeps = [100.0], [], []
        def request(method, path, *args, **kwargs):
            if method == 'GET':
                return HEALTH
            op = json.loads(args[0])['operation']; operations.append(op)
            return {'prepare': PLAN, 'run': JOB, 'inspect': JOB | {'inspection_state': 'working'}}[op]
        def sleep(seconds):
            sleeps.append(seconds); now[0] += seconds
        with (patch.object(self.client, '_request', side_effect=request),
              patch('spiceowl.integrations.live.time.monotonic', side_effect=lambda: now[0]),
              patch('spiceowl.integrations.live.time.sleep', side_effect=sleep)):
            result = self.client.workflow(**PREPARE, timeout=5, poll_seconds=3)
        self.assertEqual(result['state'], 'timed_out')
        self.assertEqual(result['job_id'], JOB['job_id'])
        self.assertEqual(result['submission_key'], 'experiment-' + PLAN_ID)
        self.assertEqual(operations, ['prepare', 'run', 'inspect', 'inspect'])
        self.assertEqual(sleeps, [3, 2])

    def test_projection_keeps_statistics_but_rejects_arbitrary_maps_and_private_details(self):
        done = complete_job()
        private = done | {'path': '/private/model', 'env': {'secret': 'test'}, 'stderr': 'private route'}
        private['measurements']['windows'][0]['path'] = '/private/file'
        public = _safe_public(private)
        self.assertNotIn('path', public)
        self.assertNotIn('env', public)
        self.assertNotIn('stderr', public)
        self.assertNotIn('path', public['measurements']['windows'][0])
        self.assertEqual(public['measurements']['voltage_ranges_v']['CLK'], [0, 1])
        for values in ({'OUT': {'path': '/private/file'}}, {'/private/name': [0, 1]}, {'OUT': [0, 'secret']},
                       {'OUT': [0, float('inf')]}):
            with self.subTest(values=values), self.assertRaises(DomainError):
                _safe_public({'voltage_ranges_v': values})

    def test_workflow_cli_commands_do_not_open_demo_controller(self):
        base = ['--data-dir', str(self.root / 'must-not-create'), 'workflow', '--client-config', str(self.config_path)]
        cases = [(['prepare', '--recipe', PREPARE['recipe_id'], '--source-commit', PREPARE['source_commit']], 'prepare', PREPARE, PLAN),
                 (['run', '--plan-id', PLAN_ID], 'run', {'plan_id': PLAN_ID}, JOB),
                 (['inspect', '--job-id', JOB['job_id']], 'inspect', {'job_id': JOB['job_id']}, complete_job())]
        for argv, operation, arguments, response in cases:
            out = io.StringIO()
            with (redirect_stdout(out), patch('spiceowl.cli.main.Controller', side_effect=AssertionError('demo')),
                  patch.object(LiveSimulationClient, 'execute', return_value=response) as command):
                self.assertEqual(main(base + argv), 0)
            command.assert_called_once_with(operation, arguments)
            self.assertEqual(json.loads(out.getvalue()), response)
        out = io.StringIO()
        with redirect_stdout(out), patch.object(LiveSimulationClient, 'workflow', return_value={'state': 'timed_out', 'job_id': JOB['job_id']}) as command:
            self.assertEqual(main(base + ['execute', '--recipe', PREPARE['recipe_id'], '--source-commit', PREPARE['source_commit'],
                                         '--timeout', '20', '--poll-seconds', '2']), 1)
        command.assert_called_once_with(PREPARE['recipe_id'], PREPARE['source_commit'], timeout=20.0, poll_seconds=2.0)
        self.assertFalse((self.root / 'must-not-create').exists())
        with redirect_stdout(io.StringIO()), patch.object(LiveSimulationClient, 'lookup_submission', return_value={'state': 'mapping_found'}) as lookup:
            self.assertEqual(main(base + ['lookup', '--key', 'experiment-' + PLAN_ID]), 0)
        lookup.assert_called_once_with('experiment-' + PLAN_ID)

    def test_fake_loopback_signed_workflow_and_repeated_cli_invocation(self):
        seen, key = [], self.key
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def reply(self, value):
                raw = json.dumps({'ok': True, 'data': value}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers(); self.wfile.write(raw)
            def do_GET(self):
                self.reply(HEALTH)
            def do_POST(self):
                raw = self.rfile.read(int(self.headers['Content-Length']))
                verify(key, 'spiceowl-local', 'POST', self.path, raw, self.headers['X-IC-Simulation-Authorization'])
                body = json.loads(raw); seen.append(body)
                self.reply({'prepare': PLAN, 'run': JOB, 'inspect': complete_job()}[body['operation']])
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        try:
            self.configure('http://127.0.0.1:' + str(server.server_port))
            argv = ['workflow', '--client-config', str(self.config_path), 'execute', '--recipe', PREPARE['recipe_id'],
                    '--source-commit', PREPARE['source_commit'], '--timeout', '10']
            for _ in range(2):
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(main(argv), 0)
                self.assertEqual(json.loads(out.getvalue())['job_id'], JOB['job_id'])
        finally:
            server.shutdown(); server.server_close(); worker.join(timeout=2)
        self.assertEqual([row['operation'] for row in seen], ['prepare', 'run', 'inspect', 'prepare', 'inspect'])
        self.assertEqual(seen[1]['idempotency_key'], 'experiment-' + PLAN_ID)


if __name__ == '__main__':
    unittest.main()

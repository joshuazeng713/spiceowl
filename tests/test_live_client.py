"""Offline signed client, uncertainty and CLI tests; no solver or SSH activity."""
from contextlib import redirect_stderr, redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from spiceowl.cli.main import main, parser
from spiceowl.domain.common import DomainError
from spiceowl.integrations.authority import verify
from spiceowl.integrations.live import LiveSimulationClient, MAX_RESPONSE_BYTES
from tests.test_ic_workbench import Response, lookup_fixture, run_fixture

HEALTH = {'api_version': '1', 'mode': 'live', 'mutation_token': 'fixture-token-with-enough-characters'}
ARGS = {'configuration': 'tran', 'source_commit': 'a' * 40}
RUN = {'run_id': 'fixture-native-run', 'project_id': 'fixture-project', **ARGS, 'state': 'submitted'}
RUNTIME = {'schema': 'ic.simulation.runtime.v1', 'project_id': 'fixture-project',
           'read_at': '2026-11-23T00:00:00+00:00', 'pool': {'configured': True, 'hosts': []},
           'runs': [], 'capabilities': {'scan': True, 'monitor': True, 'direct_submit': True}}


def response(data, **kwargs):
    return Response(json.dumps({'ok': True, 'data': data}).encode(), **kwargs)


class LiveClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / 'client.json'
        self.key_path = self.root / 'authority.key'
        self.key_path.write_bytes(b'fixture-key-for-signed-client-only' * 2)
        self.key_path.chmod(0o600)
        self.config = {'schema': 'spiceowl.live-client.v1', 'base_url': 'http://127.0.0.1:18881',
                       'project_id': 'fixture-project', 'authority_key_file': str(self.key_path)}
        self.write_config()
        self.client = LiveSimulationClient(self.config_path)

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config))
        self.config_path.chmod(0o600)

    def transport(self, responses):
        records = []
        def factory(*args, **kwargs):
            connection = MagicMock()
            reply = responses.pop(0)
            if isinstance(reply, BaseException):
                connection.getresponse.side_effect = reply
            else:
                connection.getresponse.return_value = reply
            records.append((args, kwargs, connection))
            return connection
        return patch('spiceowl.integrations.live.http.client.HTTPConnection', side_effect=factory), records

    def journal(self):
        return json.loads(self.client._journal_path.read_text())

    def test_configuration_is_private_strict_and_offline(self):
        with patch.object(LiveSimulationClient, '_request', side_effect=AssertionError('network')):
            self.assertEqual(LiveSimulationClient(self.config_path).project_id, 'fixture-project')
        original = copy.deepcopy(self.config)
        for change in ({'base_url': 'http://localhost:18881'}, {'base_url': 'http://127.0.0.2:18881'},
                       {'base_url': 'http://127.0.0.1:18881/path'}, {'base_url': 'http://127.0.0.1:0'},
                       {'base_url': 'http://127.0.0.1:65536'}, {'caller': 'native-agent'},
                       {'project_id': '../other'}, {'schema': 'wrong'}, {'authority_key_file': 'relative-key'}):
            self.config = original | change
            self.write_config()
            with self.subTest(change=change), self.assertRaises(DomainError):
                LiveSimulationClient(self.config_path)
        self.config = original
        self.write_config()
        self.config_path.chmod(0o644)
        with self.assertRaises(DomainError):
            LiveSimulationClient(self.config_path)
        self.config_path.chmod(0o600)
        self.key_path.chmod(0o644)
        with self.assertRaises(DomainError):
            LiveSimulationClient(self.config_path)
        self.key_path.chmod(0o600)
        symlink = self.root / 'linked.json'
        symlink.symlink_to(self.config_path)
        with self.assertRaises(DomainError):
            LiveSimulationClient(symlink)

    def test_invalid_duplicate_and_oversized_json_rejected(self):
        for raw in (b'{"schema":1,"schema":2}', b'not-json', b' ' * 17000):
            self.config_path.write_bytes(raw)
            with self.subTest(raw=raw[:20]), self.assertRaises(DomainError):
                LiveSimulationClient(self.config_path)

    def test_runtime_and_control_sign_exact_path_body_and_fixed_caller(self):
        context, records = self.transport([response(HEALTH), response(RUNTIME), response(HEALTH), response(RUN)])
        with context, patch.dict(os.environ, {'HTTP_PROXY': 'http://untrusted.invalid', 'ALL_PROXY': 'http://untrusted.invalid'}):
            self.assertEqual(self.client.runtime(), RUNTIME)
            result = self.client.execute('submit', ARGS, 'test-submit-key')
        self.assertEqual(result, RUN)
        self.assertEqual(len(records), 4)
        for args, kwargs, _ in records:
            self.assertEqual(args[:2], ('127.0.0.1', 18881))
            self.assertIn(kwargs['timeout'], (30.0, 120.0))
        runtime = records[1][2].request.call_args
        self.assertEqual(runtime.args, ('GET', '/api/simulation/runtime?project_id=fixture-project'))
        self.assertIsNone(runtime.kwargs['body'])
        rh = runtime.kwargs['headers']
        self.assertEqual(rh['X-IC-Simulation-Caller'], 'spiceowl-local')
        verify(self.key_path.read_bytes(), 'spiceowl-local', 'GET', runtime.args[1], b'', rh['X-IC-Simulation-Authorization'])
        post = records[-1][2].request.call_args
        raw, headers = post.kwargs['body'], post.kwargs['headers']
        self.assertEqual(headers['Origin'], self.config['base_url'])
        self.assertEqual(headers['Content-Type'], 'application/json')
        self.assertEqual(headers['X-Workbench-Token'], HEALTH['mutation_token'])
        verify(self.key_path.read_bytes(), 'spiceowl-local', 'POST', post.args[1], raw, headers['X-IC-Simulation-Authorization'])
        with self.assertRaises(PermissionError):
            verify(self.key_path.read_bytes(), 'spiceowl-local', 'POST', post.args[1], raw + b' ', headers['X-IC-Simulation-Authorization'])
        self.assertEqual(json.loads(raw)['arguments'], ARGS | {'project_id': 'fixture-project'})

    def test_scan_maps_to_finite_operation_and_identity_rejected_before_io(self):
        with patch.object(self.client, '_request', side_effect=[HEALTH, {'state': 'scanning', 'automatic_retry': False}]) as request:
            self.client.execute('scan', {})
        self.assertEqual(json.loads(request.call_args.args[2])['operation'], 'resources_scan')
        cases = [('delete', {}), ('scan', {'command': 'echo bad'}), ('submit', ARGS | {'project_id': 'other'}),
                 ('submit', ARGS | {'source_commit': 'main'}), ('status', ARGS),
                 ('submit', ARGS | {'source_commit': 'a' * 40 + '\n'})]
        with patch.object(self.client, '_request', side_effect=AssertionError('network')):
            for operation, args in cases:
                with self.subTest(operation=operation, args=args), self.assertRaises(DomainError):
                    self.client.execute(operation, args)
            with self.assertRaises(DomainError):
                self.client.execute('scan', {}, 'unneeded-key')

    def test_wrong_health_is_not_used_for_control(self):
        for health in (HEALTH | {'api_version': '2'}, HEALTH | {'mode': 'demo'}, HEALTH | {'mutation_token': 'short'}):
            with patch.object(self.client, '_request', return_value=health) as request, self.assertRaises(DomainError):
                self.client.execute('scan', {})
            self.assertEqual(request.call_count, 1)
        self.assertFalse(self.client._journal_path.exists())

    def test_unknown_submit_survives_restart_and_blocks_any_replay_key(self):
        context, records = self.transport([response(HEALTH), TimeoutError('private-host-and-key')])
        with context, self.assertRaises(DomainError) as raised:
            self.client.execute('submit', ARGS)
        self.assertEqual(raised.exception.code, 'live_outcome_unknown')
        self.assertNotIn('private-host', str(raised.exception))
        journal = self.journal()
        key = journal['requests'][0]['key']
        self.assertEqual(journal['requests'][0]['state'], 'pending')
        self.assertEqual(self.client._journal_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(records[-1][2].request.call_args.kwargs['body'])['idempotency_key'], key)
        restarted = LiveSimulationClient(self.config_path)
        with patch.object(restarted, '_request', side_effect=AssertionError('must not replay')):
            for chosen in (None, key, 'brand-new-key'):
                with self.subTest(key=chosen), self.assertRaises(DomainError) as denied:
                    restarted.execute('submit', ARGS, chosen)
                self.assertEqual(denied.exception.code, 'live_outcome_unknown')
        self.assertEqual(self.journal()['requests'][0]['key'], key)

    def test_complete_key_is_reused_without_network_and_conflict_fails(self):
        with patch.object(self.client, '_request', side_effect=[HEALTH, RUN]):
            self.client.execute('submit', ARGS, 'completed-key')
        restarted = LiveSimulationClient(self.config_path)
        with patch.object(restarted, '_request', side_effect=AssertionError('no replay')):
            self.assertEqual(restarted.execute('submit', ARGS, 'completed-key'), RUN)
            self.assertEqual(restarted.execute('submit', ARGS), RUN)
            with self.assertRaises(DomainError) as raised:
                restarted.execute('submit', ARGS | {'configuration': 'ac'}, 'completed-key')
            self.assertEqual(raised.exception.code, 'live_key_conflict')

    def test_unknown_collect_and_wrong_response_run_fail_closed(self):
        args = ARGS | {'run_id': RUN['run_id']}
        with patch.object(self.client, '_request', side_effect=[HEALTH, RUN | {'run_id': 'different-run'}]), self.assertRaises(DomainError) as raised:
            self.client.execute('collect', args, 'collect-key')
        self.assertEqual(raised.exception.code, 'live_outcome_unknown')
        with patch.object(self.client, '_request', side_effect=AssertionError('no replay')), self.assertRaises(DomainError):
            self.client.execute('collect', args, 'new-collect-key')

    def test_submit_claim_is_durable_before_post_and_identity_must_be_complete(self):
        def request(method, path, *args, **kwargs):
            if method == 'GET':
                return HEALTH
            self.assertEqual(self.journal()['requests'][0]['state'], 'pending')
            self.assertEqual(self.journal()['requests'][0]['key'], 'durable-submit-key')
            return {'run_id': RUN['run_id'], 'state': 'submitted'}
        with patch.object(self.client, '_request', side_effect=request), self.assertRaises(DomainError) as raised:
            self.client.execute('submit', ARGS, 'durable-submit-key')
        self.assertEqual(raised.exception.code, 'live_outcome_unknown')
        self.assertEqual(self.journal()['requests'][0]['state'], 'pending')

    def test_journal_lock_and_corruption_prevent_dispatch(self):
        with self.client._journal():
            with self.assertRaises(DomainError) as raised:
                self.client.execute('submit', ARGS)
            self.assertEqual(raised.exception.code, 'live_request_busy')
        self.client._journal_path.write_text('{}')
        self.client._journal_path.chmod(0o600)
        with patch.object(self.client, '_request', side_effect=AssertionError('must not send')), self.assertRaises(DomainError):
            self.client.execute('submit', ARGS)

    def test_lookup_only_exact_key_and_identity_never_enables_replay(self):
        key = 'unknown-submit-key'
        with patch.object(self.client, '_request', side_effect=[HEALTH, DomainError('lost', 'live_transport')]), self.assertRaises(DomainError):
            self.client.execute('submit', ARGS, key)
        for record in (None, run_fixture()):
            with patch.object(self.client, '_request', side_effect=[HEALTH, lookup_fixture(key, record)]) as request:
                result = self.client.lookup_submission(key)
            self.assertEqual(request.call_args.args[:2], ('GET', '/api/submissions/' + key))
            self.assertEqual(result['state'], 'mapping_found' if record else 'mapping_not_found')
            self.assertEqual(result['dispatch_outcome'], 'unknown')
            self.assertIs(result['replay_permitted'], False)
            self.assertEqual(self.journal()['requests'][0]['state'], 'pending')
        wrong = run_fixture() | {'source_commit': 'b' * 40}
        with patch.object(self.client, '_request', side_effect=[HEALTH, lookup_fixture(key, wrong)]), self.assertRaises(DomainError):
            self.client.lookup_submission(key)
        with patch.object(self.client, '_request', side_effect=AssertionError('no replay')), self.assertRaises(DomainError):
            self.client.execute('submit', ARGS, key)

    def test_transport_rejects_redirect_oversize_incomplete_or_invalid_json(self):
        for reply in (response({}, status=302, headers={'Location': 'https://evil.invalid'}),
                      response({}, headers={'Content-Length': str(MAX_RESPONSE_BYTES + 1)}),
                      response({}, headers={'Content-Length': '9999'}),
                      response({}, headers={'Transfer-Encoding': 'chunked'}),
                      response({}, headers={'Content-Type': 'text/html'}),
                      response({}, headers={'Content-Encoding': 'gzip'}),
                      Response(b'{"ok":true,"data":{"x":NaN}}')):
            context, records = self.transport([reply])
            with context, self.assertRaises(DomainError):
                self.client._health()
            self.assertEqual(len(records), 1)
            records[0][2].close.assert_called()

    def test_streaming_limit_and_whole_response_deadline(self):
        oversized = Response(b' ' * (MAX_RESPONSE_BYTES + 1))
        oversized.headers.pop('Content-Length')
        context, _ = self.transport([oversized])
        with context, self.assertRaises(DomainError) as raised:
            self.client._health()
        self.assertEqual(raised.exception.code, 'live_response_limit')
        slow = response(HEALTH)
        original = slow.read1
        def delayed(amount):
            time.sleep(0.025)
            return original(amount)
        slow.read1 = delayed
        context, records = self.transport([slow])
        with context, self.assertRaises(DomainError) as raised:
            self.client._request('GET', '/api/health', timeout=0.01)
        self.assertEqual(raised.exception.code, 'live_transport')
        records[0][2].sock.shutdown.assert_called()

    def test_runtime_identity_mismatch_is_not_presented_as_an_observation(self):
        for changed in (RUNTIME | {'project_id': 'other'}, RUNTIME | {'runs': [{'run_id': 'run-1'}]},
                        RUNTIME | {'schema': 'demo'}, RUNTIME | {'runs': [{'run_id': 'run-1', **ARGS}] * 26}):
            with patch.object(self.client, '_request', side_effect=[HEALTH, changed]), self.assertRaises(DomainError):
                self.client.runtime()

    def test_token_authority_and_backend_routes_omitted_from_outputs(self):
        raw = RUN | {'error': 'ssh somebody@internal.invalid secret', 'authority_key_file': '/private/key',
                     'token': 'secret', 'collection': {'archive': '/private/archive', 'state': 'collecting'},
                     'receipt': '/private/run.json', 'publication': {'url': 'https://private.invalid'}}
        with patch.object(self.client, '_request', side_effect=[HEALTH, raw]):
            result = self.client.execute('submit', ARGS)
        text = json.dumps(result)
        for forbidden in ('somebody', 'secret', '/private', 'private.invalid', 'token', 'authority'):
            self.assertNotIn(forbidden, text)
        self.assertEqual(result['collection'], {'state': 'collecting'})

    def test_cli_live_uses_shared_client_without_demo_controller(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch('spiceowl.integrations.live.LiveSimulationClient') as factory, patch('spiceowl.cli.main.Controller', side_effect=AssertionError('demo controller')):
            factory.return_value.execute.return_value = RUNTIME
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(['live', '--client-config', str(self.config_path), 'runtime'])
            self.assertEqual(code, 0)
            factory.return_value.execute.assert_called_once_with('runtime', {}, None)
        self.assertEqual(json.loads(stdout.getvalue()), RUNTIME)
        self.assertEqual(stderr.getvalue(), '')
        options = parser().parse_args(['serve', '--demo', '--live-client-config', str(self.config_path)])
        self.assertTrue(options.demo)
        self.assertEqual(options.live_client_config, str(self.config_path))

    def test_cli_error_safe_and_lookup_parser(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch('spiceowl.integrations.live.LiveSimulationClient') as factory:
            factory.return_value.lookup_submission.side_effect = DomainError('Original request remains unresolved.', 'live_outcome_unknown')
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(['live', '--client-config', str(self.config_path), 'lookup', '--key', 'saved-key'])
            factory.return_value.lookup_submission.assert_called_once_with('saved-key')
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stderr.getvalue())['error']['code'], 'live_outcome_unknown')
        self.assertEqual(stdout.getvalue(), '')


if __name__ == '__main__':
    unittest.main()

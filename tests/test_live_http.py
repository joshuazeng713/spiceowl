"""Live Web wiring against an offline client; never contacts CircuitGit or SSH."""
import copy
import http.client
import json
import sys
import threading
import types
import unittest
from unittest.mock import Mock, patch

from spiceowl.domain.common import DomainError
from spiceowl.web.server import make_server


class LiveHttpTests(unittest.TestCase):
    def setUp(self):
        self.synthetic = {'version': 'fixture', 'runs': [{'run_id': 'demo', 'synthetic': True}],
                          'connections': [], 'hosts': [], 'native_agents': [], 'questions': [], 'sessions': []}
        self.controller = Mock()
        self.controller.state.side_effect = lambda: copy.deepcopy(self.synthetic)
        self.client = Mock()
        self.client.project_id = 'fixture-project'
        self.client.runtime.return_value = {'schema': 'ic.simulation.runtime.v1', 'project_id': 'fixture-project',
                                            'pool': {'hosts': [], 'configured': True}, 'runs': []}
        self.client.execute.return_value = {'run_id': 'fixture-run', 'state': 'submitted'}
        self.client.lookup_submission.return_value = {'state': 'mapping_found', 'run_id': 'fixture-run', 'replay_permitted': False}
        module = types.ModuleType('spiceowl.integrations.live')
        module.LiveSimulationClient = Mock(return_value=self.client)
        self.factory = module.LiveSimulationClient
        with patch.dict(sys.modules, {'spiceowl.integrations.live': module}):
            self.server = make_server(self.controller, live_client_config='/fixture/operator-config.json')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        _, state = self.request('GET', '/api/state')
        self.token = state['csrf_token']

    def tearDown(self):
        self.server.shutdown(); self.thread.join(); self.server.server_close()

    def request(self, method, path, body=None, headers=None, token=False):
        client = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            values = {'Content-Type': 'application/json'}
            if token:
                values['X-SpiceOwl-Token'] = self.token
            values.update(headers or {})
            client.request(method, path, body=json.dumps(body) if body is not None else None, headers=values)
            response = client.getresponse()
            return response.status, json.loads(response.read())
        finally:
            client.close()

    def control(self, operation='submit', **changes):
        body = {'operation': operation, 'args': {'project_id': 'fixture-project', 'configuration': 'tran', 'source_commit': 'a' * 40},
                'idempotency_key': 'fixture-submit-key'}
        body.update(changes)
        return body

    def test_operator_config_is_constructed_once_and_get_only_reads_cached_runtime(self):
        self.factory.assert_called_once_with('/fixture/operator-config.json')
        status, value = self.request('GET', '/api/live/runtime')
        self.assertEqual(status, 200)
        self.assertTrue(value['enabled'])
        self.assertEqual(value['project_id'], 'fixture-project')
        self.client.runtime.assert_called_once_with()
        self.client.execute.assert_not_called()
        self.assertEqual(self.controller.state()['runs'], self.synthetic['runs'])

    def test_live_mutations_reuse_existing_origin_and_csrf_guards(self):
        body = self.control()
        self.assertEqual(self.request('POST', '/api/live/control', body)[0], 403)
        self.assertEqual(self.request('POST', '/api/live/control', body, token=True, headers={'Origin': 'https://example.invalid'})[0], 403)
        self.assertEqual(self.request('POST', '/api/live/control', body, token=True, headers={'Host': 'other.invalid'})[0], 403)
        self.client.execute.assert_not_called()
        self.assertEqual(self.request('POST', '/api/live/control', body, token=True)[0], 202)
        self.client.execute.assert_called_once_with('submit', body['args'], idempotency_key=body['idempotency_key'])

    def test_exact_scan_status_collect_watch_and_unwatch_arguments(self):
        identity = {'configuration': 'tran', 'source_commit': 'a' * 40, 'run_id': 'fixture-run'}
        for operation in ('scan', 'status', 'collect', 'watch', 'unwatch'):
            body = {'operation': operation, 'args': {} if operation == 'scan' else identity}
            if operation == 'collect':
                body['idempotency_key'] = 'fixture-collect-key'
            with self.subTest(operation=operation):
                self.assertEqual(self.request('POST', '/api/live/control', body, token=True)[0], 202)
                self.client.execute.assert_called_with(operation, body['args'], idempotency_key=body.get('idempotency_key'))

    def test_browser_cannot_supply_config_paths_commands_or_unbounded_fields(self):
        invalid = [self.control(config_path='/fixture/other.json'), self.control(operation='shell'),
                   self.control(operation=['submit']), self.control(args={'command': 'arbitrary'}),
                   self.control(args={'configuration': 'tran', 'source_commit': 'short'}),
                   self.control(args={'configuration': 'tran', 'source_commit': 'a' * 40, 'remote_credentials': 'forbidden'}),
                   {'operation': 'scan', 'args': {'project_id': 'fixture-project', 'site_file': '/fixture/site.json'}}]
        for body in invalid:
            with self.subTest(body=body):
                self.assertEqual(self.request('POST', '/api/live/control', body, token=True)[0], 400)
        for path in ('/api/live/runtime?config_path=fixture', '/api/live/control?key=fixture'):
            self.assertEqual(self.request('GET' if 'runtime' in path else 'POST', path, {} if 'control' in path else None, token=True)[0], 400)
        self.client.execute.assert_not_called()
        self.client.runtime.assert_not_called()

    def test_lookup_uses_original_key_and_never_reexecutes_submission(self):
        status, result = self.request('GET', '/api/live/submission?key=fixture-submit-key')
        self.assertEqual(status, 200)
        self.assertFalse(result['replay_permitted'])
        self.client.lookup_submission.assert_called_once_with('fixture-submit-key')
        self.client.execute.assert_not_called()
        for query in ('key=short', 'key=fixture-submit-key&key=fixture-other-key', 'key=fixture-submit-key&config_path=x'):
            self.assertEqual(self.request('GET', '/api/live/submission?' + query)[0], 400)

    def test_unknown_outcome_keeps_client_error_code(self):
        self.client.execute.side_effect = DomainError('Original request must be reconciled.', 'live_outcome_unknown')
        status, result = self.request('POST', '/api/live/control', self.control(), token=True)
        self.assertEqual(status, 400)
        self.assertEqual(result['error']['code'], 'live_outcome_unknown')

    def test_without_operator_config_is_explicitly_disabled(self):
        self.server.shutdown(); self.thread.join(); self.server.server_close()
        with patch.dict(sys.modules, {'spiceowl.integrations.live': None}):
            self.server = make_server(self.controller)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        _, state = self.request('GET', '/api/state'); self.token = state['csrf_token']
        status, result = self.request('GET', '/api/live/runtime')
        self.assertEqual((status, result), (200, {'enabled': False, 'project_id': None, 'runtime': None}))
        self.assertEqual(self.request('POST', '/api/live/control', self.control(), token=True)[1]['error']['code'], 'live_not_configured')
        self.client.execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()

"""Loopback CSRF, finite control and SSE event identities for the standalone UI."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from spiceowl.service import Controller
from spiceowl.web.server import make_server


class CodexHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Controller(self.temp.name, agent_mode='standalone')
        self.server = make_server(self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.port = self.server.server_address[1]
        self.token = self.request('GET', '/api/state')[1]['csrf_token']

    def tearDown(self):
        self.server.shutdown(); self.thread.join(); self.server.server_close(); self.app.close(); self.temp.cleanup()

    def request(self, method, path, data=None, *, csrf=False, headers=None):
        client = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        try:
            fields = {'Content-Type': 'application/json'} | (headers or {})
            if csrf: fields['X-SpiceOwl-Token'] = self.token
            client.request(method, path, json.dumps(data) if data is not None else None, fields)
            response = client.getresponse(); raw = response.read()
            return response.status, json.loads(raw)
        finally: client.close()

    def test_opening_console_status_and_read_history_never_starts_codex(self):
        with patch('subprocess.Popen', side_effect=AssertionError('explicit connect only')):
            status, data = self.request('GET', '/api/codex/status')
            self.assertEqual(status, 200)
            self.assertEqual(data['mode'], 'standalone'); self.assertFalse(data['connected'])
            channel = self.request('GET', '/api/state')[1]['agent_channel']
            self.assertEqual(channel['owner'], 'spiceowl')
        self.assertFalse((Path(self.temp.name) / 'codex' / 'codex-bridge.json').exists())

    def test_all_controls_have_csrf_origin_and_exact_argument_guards(self):
        with patch.object(self.app.codex, 'connect', return_value={'state': 'connected'}) as connect:
            self.assertEqual(self.request('POST', '/api/codex/connect', {})[0], 403)
            self.assertEqual(self.request('POST', '/api/codex/connect', {}, csrf=True, headers={'Origin': 'https://foreign.invalid'})[0], 403)
            self.assertEqual(self.request('POST', '/api/codex/connect', {'executable': '/tmp/codex'}, csrf=True)[0], 400)
            connect.assert_not_called()
            self.assertEqual(self.request('POST', '/api/codex/connect', {'model': 'explicit-model'}, csrf=True)[0], 202)
            connect.assert_called_once_with({'model': 'explicit-model'})
        with patch.object(self.app.codex, 'send_message', return_value={'state': 'running', 'turn_id': 't-1'}) as send:
            payload = {'thread_id': 'th-1', 'text': 'Hello', 'request_key': 'original-1'}
            self.assertEqual(self.request('POST', '/api/codex/message', payload | {'approved': True}, csrf=True)[0], 400)
            self.assertEqual(self.request('POST', '/api/codex/message', payload, csrf=True)[0], 202)
            send.assert_called_once_with(**payload)
        with patch.object(self.app.codex, 'decide', return_value={'state': 'resolved'}) as decide:
            self.assertEqual(self.request('GET', '/api/codex/approval?approval_id=unknown')[0], 400)
            self.assertEqual(self.request('POST', '/api/codex/approval', {'approval_id': 'p-1', 'decision': 'deny'}, csrf=True)[0], 202)
            decide.assert_called_once_with(approval_id='p-1', decision='deny')
        with patch.object(self.app.codex, 'interrupt', return_value={'interrupted': True, 'state': 'interrupted'}) as interrupt:
            self.assertEqual(self.request('POST', '/api/codex/interrupt', {})[0], 403)
            self.assertEqual(self.request('POST', '/api/codex/interrupt', {'force': True}, csrf=True)[0], 400)
            self.assertEqual(self.request('POST', '/api/codex/interrupt', {}, csrf=True)[0], 202)
            interrupt.assert_called_once_with()
        status, patches = self.request('GET', '/api/codex/ui-patches?after=0')
        self.assertEqual(status, 200)
        self.assertEqual(patches['schema'], 'spiceowl.codex-ui-patches.v1')
        self.assertEqual(patches['revision'], 0)
        self.assertEqual(patches['patches'], [])
        self.assertEqual(self.request('GET', '/api/codex/ui-patches?after=-1')[0], 400)

    def test_sse_uses_last_event_id_and_returns_only_read_events(self):
        event = {'id': 8, 'method': 'item/agentMessage/delta', 'params': {'delta': 'hello'},
                 'thread_id': 'th-1', 'turn_id': 't-1', 'request_key': 'original-1'}
        batch = {'events': [event], 'cursor': 8, 'oldest_cursor': 1, 'truncated': False}
        with patch.object(self.app.codex, 'events', return_value=batch) as events:
            client = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
            try:
                client.request('GET', '/api/codex/events?after=2', headers={'Last-Event-ID': '7'})
                response = client.getresponse(); self.assertEqual(response.status, 200)
                self.assertTrue(response.getheader('Content-Type').startswith('text/event-stream'))
                lines = [response.readline().decode() for _ in range(4)]
                self.assertIn('id: 8\n', lines); self.assertIn('event: bridge\n', lines)
                self.assertIn('"delta": "hello"', ''.join(lines))
                self.assertEqual(events.call_args_list[0].kwargs, {'after': 7})
            finally: client.close()
            self.app._closing.set()
        self.assertEqual(self.request('GET', '/api/codex/events?after=-1')[0], 400)

    def test_embedded_bridge_rejects_active_http_and_does_not_create_agent_state(self):
        from spiceowl.codex_bridge import CodexBridge
        self.app.codex.close()
        absent = Path(self.temp.name) / 'embedded-must-not-exist'
        self.app.codex = CodexBridge(absent, mode='embedded_in_circuitgit')
        with patch('subprocess.Popen', side_effect=AssertionError('no second runtime')):
            self.assertEqual(self.request('POST', '/api/codex/connect', {}, csrf=True)[0], 400)
            self.assertEqual(self.request('POST', '/api/codex/thread', {}, csrf=True)[0], 400)
        self.assertFalse(absent.exists())


if __name__ == '__main__': unittest.main()

"""Direct SDK CLI and retired Agent launchers; no external processes or IO."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from spiceowl.cli.main import main, parser
from spiceowl.service import Controller
from spiceowl.integrations.ic_workbench import ICGatewayBackend, LoopbackGatewayClient
from test_ic_workbench import CONFIG, run_fixture, snapshot_fixture


class ReadCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'state'
        with Controller(self.directory) as controller:
            controller.seed_demo()
        gateway = ICGatewayBackend(self.directory)
        gateway.register(CONFIG)
        with patch.object(LoopbackGatewayClient, 'supervision', return_value=snapshot_fixture(run_fixture())):
            gateway.refresh(CONFIG['id'])

    def call(self, arguments, directory=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(['--data-dir', str(directory or self.directory), *arguments])
        return code, json.loads(out.getvalue()) if out.getvalue() else None, err.getvalue()

    def test_all_eight_views_use_sdk_without_controller_or_runtime(self):
        from spiceowl.read_api import open_read_views
        views = open_read_views(self.directory)
        cases = [(['status'], views.status, ()), (['runs'], views.list_runs, ()),
            (['run', 'demo-disconnect'], views.get_run, ('demo-disconnect',)),
            (['connections'], views.list_connections, ()), (['hosts'], views.list_hosts, ()),
            (['ic-gateways'], views.list_ic_gateways, ()),
            (['ic-runs', CONFIG['id']], views.list_ic_runs, (CONFIG['id'],)),
            (['ic-run', CONFIG['id'], 'fixture-native-run'], views.get_ic_run, (CONFIG['id'], 'fixture-native-run'))]
        db = self.directory / 'spiceowl.sqlite3'
        before = hashlib.sha256(db.read_bytes()).hexdigest()
        with (patch('spiceowl.cli.main.Controller', side_effect=AssertionError('No controller')),
              patch('spiceowl.cli.main.Store', side_effect=AssertionError('No writable store')),
              patch('subprocess.Popen', side_effect=AssertionError('No subprocess')),
              patch('socket.socket', side_effect=AssertionError('No network')),
              patch('spiceowl.read_api.utcnow', return_value='2026-11-24T00:00:00+00:00')):
            for args, method, positional in cases:
                with self.subTest(args=args):
                    code, actual, err = self.call(['read', *args])
                    self.assertEqual(code, 0, err)
                    self.assertEqual(actual, method(*positional))
                    self.assertTrue(actual['read_only'])
        self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), before)

    def test_retired_agent_launchers_fail_before_reading_profiles_or_creating_state(self):
        absent = Path(self.temp.name) / 'must-not-be-created'
        cases = [['mcp'], ['agents', 'add', '--file', '/unread/profile.json'],
                 ['agents', 'probe', 'old-runtime'],
                 ['agents', 'ask', 'old-runtime', '--question', 'do not dispatch']]
        with (patch('spiceowl.cli.main.Controller', side_effect=AssertionError('No controller')),
              patch('spiceowl.cli.main.Store', side_effect=AssertionError('No store')),
              patch('builtins.open', side_effect=AssertionError('No profile read')),
              patch('subprocess.Popen', side_effect=AssertionError('No Agent/MCP process'))):
            for args in cases:
                with self.subTest(args=args):
                    code, output, err = self.call(args, absent)
                    self.assertEqual(code, 1)
                    self.assertIsNone(output)
                    error = json.loads(err)['error']
                    self.assertEqual(error['code'], 'agent_channel_owned_by_circuitgit')
                    self.assertIn('CircuitGit', error['message'])
        self.assertFalse(absent.exists())

    def test_read_missing_state_does_not_initialize_it(self):
        absent = Path(self.temp.name) / 'absent'
        code, output, err = self.call(['read', 'status'], absent)
        self.assertEqual(code, 1)
        self.assertIsNone(output)
        self.assertEqual(json.loads(err)['error']['code'], 'storage_missing')
        self.assertFalse(absent.exists())

    def test_read_rejects_unknown_commands_extra_paths_and_invalid_ids(self):
        for args in (['read', 'submit'], ['read', 'status', '/foreign/path']):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser().parse_args(args)
        for args in (['read', 'run', '../foreign'], ['read', 'ic-run', CONFIG['id'], '../foreign']):
            self.assertEqual(self.call(args)[0], 1)

    def test_help_directs_agent_users_to_circuitgit(self):
        for args in (['--help'], ['mcp', '--help'], ['agents', '--help']):
            out = io.StringIO()
            with self.subTest(args=args), contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as raised:
                parser().parse_args(args)
            self.assertEqual(raised.exception.code, 0)
            self.assertIn('CircuitGit', out.getvalue())
        self.assertIn('without an Agent or MCP server', ' '.join(parser().format_help().split()))


if __name__ == '__main__':
    unittest.main()

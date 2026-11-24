"""Finite embedded exports and engine dispatch, independent of an Agent runtime."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from spiceowl.agent_channel import export_simulation_management_tools
from spiceowl.domain.common import DomainError
from spiceowl.embedded import dispatch_read_tool, read_tool_definitions
from spiceowl.read_api import open_read_views
from spiceowl.simulation_tools import SimulationToolDispatcher
from test_read_api import seed, CALLS


class SimulationToolsTests(unittest.TestCase):
    def test_embedded_catalog_and_dispatch_are_exact_read_aliases_without_io(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory) / 'existing-state'
            db = seed(directory)
            views = open_read_views(directory)
            before = hashlib.sha256(db.read_bytes()).hexdigest()
            definitions = read_tool_definitions()
            self.assertEqual(len(definitions), 8)
            with (patch('subprocess.Popen', side_effect=AssertionError('no process')),
                  patch('socket.socket', side_effect=AssertionError('no network')),
                  patch('pathlib.Path.mkdir', side_effect=AssertionError('no store')),
                  patch('spiceowl.read_api.utcnow', return_value='2026-11-24T00:00:00Z')):
                for definition, (method, positional) in zip(definitions, CALLS):
                    args = dict(zip(definition['inputSchema']['required'], positional))
                    self.assertEqual(dispatch_read_tool(views, definition['name'], args), getattr(views, method)(*positional))
                dispatcher = SimulationToolDispatcher(mode='embedded_in_circuitgit', read_views=views)
                self.assertEqual(dispatcher.dispatch('spiceowl_status', {}), views.status())
            self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), before)
            definitions[0]['inputSchema']['properties']['arbitrary'] = {}
            self.assertEqual(read_tool_definitions()[0]['inputSchema']['properties'], {})

    def test_read_dispatch_rejects_paths_mutations_and_implicit_store(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory) / 'existing-state'
            seed(directory); views = open_read_views(directory)
            for name, args in [('spiceowl_status', {'data_dir': directory}),
                               ('spiceowl_get_run', {'run_id': '../private'}),
                               ('spiceowl_status', {'approved': True}), ('spiceowl_submit', {})]:
                with self.subTest(name=name, args=args), self.assertRaises(DomainError):
                    dispatch_read_tool(views, name, args)
            with self.assertRaises(DomainError): dispatch_read_tool(directory, 'spiceowl_status', {})

    def test_pure_tools_execute_without_runtime_or_storage(self):
        dispatcher = SimulationToolDispatcher(mode='embedded_in_circuitgit')
        source = 'simulator lang=spectre\nsubckt inv A Y VDD VSS\nMN (Y A VSS VSS) nch w=1u l=100n\nMP (Y A VDD VDD) pch w=2u l=100n\nends inv\n'
        with (patch('subprocess.Popen', side_effect=AssertionError('no process')),
              patch('socket.socket', side_effect=AssertionError('no network')),
              patch('builtins.open', side_effect=AssertionError('no files'))):
            inventory = dispatcher.dispatch('spiceowl_inspect_agent_source', {'source': source})
            self.assertIn('schema', inventory)
            requirements = dispatcher.dispatch('spiceowl_netlist_requirements', {'source': source, 'mode': 'agent-tb'})
            self.assertIn('nch', json.dumps(requirements))
            diagnosis = dispatcher.dispatch('spiceowl_diagnose_spectre', {'log_text': '', 'execution_state': 'unknown', 'exit_code': None})
            self.assertIn('unknown', json.dumps(diagnosis))
        self.assertNotIn('spiceowl_status', {row['name'] for row in dispatcher.definitions()})
        self.assertEqual(dispatcher.policy('spiceowl_inspect_agent_source', {}), 'read')
        self.assertEqual(dispatcher.policy('arbitrary_command', {}), 'deny')

    def test_unconfigured_remote_calls_are_unavailable_and_bound_calls_keep_context(self):
        handler = Mock(return_value={'execution': 'unknown'})
        dispatcher = SimulationToolDispatcher(mode='standalone', owner_handlers={'spiceowl_scan_resources': handler})
        self.assertEqual(dispatcher.policy('spiceowl_scan_resources', {'host_ids': ['host-a']}), 'approval')
        context = {'thread_id': 'thread-1', 'turn_id': 'turn-1', 'tool_call_id': 'call-1', 'request_key': 'request-1'}
        self.assertEqual(dispatcher.dispatch('spiceowl_scan_resources', {'host_ids': ['host-a']}, context=context), {'execution': 'unknown'})
        handler.assert_called_once_with({'host_ids': ['host-a']}, context=context)
        for args in ({'host_ids': ['host-a'], 'approved': True}, {'host_ids': []}, {'host_ids': ['host-a', 'host-a']}):
            with self.assertRaises(DomainError): dispatcher.dispatch('spiceowl_scan_resources', args)
        with self.assertRaises(DomainError): dispatcher.dispatch('spiceowl_onboarding', {'operation': 'run', 'arguments': {}})
        self.assertEqual(handler.call_count, 1)

    def test_schema_limits_and_mode_are_enforced_without_fallback(self):
        dispatcher = SimulationToolDispatcher()
        for args in ({'source': 1}, {'source': 'x', 'shell': 'sh'}, {'source': 'x' * (1024 * 1024 + 1)}):
            with self.assertRaises(DomainError): dispatcher.dispatch('spiceowl_inspect_agent_source', args)
        with self.assertRaises(DomainError): dispatcher.dispatch('spiceowl_adctools', {'operation': 'exec', 'arguments': {}})
        with self.assertRaises(DomainError): export_simulation_management_tools(mode='standalone', dispatcher=dispatcher)
        with self.assertRaises(DomainError): SimulationToolDispatcher(mode='auto')
        with self.assertRaises(DomainError): SimulationToolDispatcher(owner_handlers={'approve': lambda **_: {}})

    def test_pure_analysis_errors_remain_correctable_without_unknown_execution(self):
        from spiceowl.adctools import AnalysisError
        from spiceowl.adctools import spectrum  # Import aliases before the dependency fault injection.
        dispatcher = SimulationToolDispatcher()
        with self.assertRaises(DomainError) as missing:
            dispatcher.dispatch('spiceowl_adctools', {'operation': 'coherent_fft', 'arguments': {}})
        self.assertEqual(missing.exception.code, 'invalid_arguments')
        arguments = {'backend': 'spiceowl', 'samples': [0.0] * 32, 'sample_rate_hz': 32, 'fundamental_hz': 1.25}
        with patch('spiceowl.adctools.common.numpy', side_effect=AnalysisError('dependency_missing', 'Install optional numerics.')):
            with self.assertRaises(DomainError) as dependency:
                dispatcher.dispatch('spiceowl_adctools', {'operation': 'coherent_fft', 'arguments': arguments})
            self.assertEqual(dependency.exception.code, 'dependency_missing')
        if importlib.util.find_spec('numpy') is not None:
            with self.assertRaises(DomainError) as noncoherent:
                dispatcher.dispatch('spiceowl_adctools', {'operation': 'coherent_fft', 'arguments': arguments})
            self.assertEqual(noncoherent.exception.code, 'noncoherent_record')


if __name__ == '__main__': unittest.main()

"""Explicit owner binding, typed schemas and no approval/transport fallback."""
import sys
import json
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from spiceowl.domain.common import DomainError
from spiceowl.service import Controller
from spiceowl.standalone_agent import CircuitGitOwner, build_standalone_dispatcher


class OwnerFailure(Exception):
    def __init__(self, code, details=None):
        self.code, self.details = code, details or {}


class StandaloneAgentTests(unittest.TestCase):
    def bind_fake_owner(self, service):
        module = types.ModuleType('ic_gateway.service')
        module.Service = Mock(return_value=service)
        module.load_config = Mock(return_value={'operator_binding': True})
        module.Failure = OwnerFailure
        schema = {'type': 'object', 'properties': {'intake_id': {'type': 'string'}}, 'required': ['intake_id'], 'additionalProperties': False}
        contracts = types.ModuleType('ic_gateway.contracts')
        contracts.READ_ONLY = {'onboarding_session'}
        contracts.tools = lambda: [{'name': 'ic_' + name, 'description': name, 'inputSchema': schema}
                                  for name in ('onboarding_run', 'onboarding_session', 'resolve', 'approve', 'apply', 'sim_archive')]
        patcher = patch.dict(sys.modules, {'ic_gateway.service': module, 'ic_gateway.contracts': contracts})
        patcher.start(); self.addCleanup(patcher.stop)
        return CircuitGitOwner('/operator/explicit/client.json'), module

    def test_owner_uses_typed_sdk_and_never_exposes_resolve_or_publication(self):
        service = Mock(); service.call.return_value = {'state': 'awaiting_human_review'}
        owner, module = self.bind_fake_owner(service)
        module.load_config.assert_called_once_with('/operator/explicit/client.json')
        catalog = owner({'operation': 'catalog', 'arguments': {}}, context={})
        self.assertEqual({row['name'] for row in catalog['tools']}, {'ic_onboarding_run', 'ic_onboarding_session'})
        service.call.assert_not_called()
        result = owner({'operation': 'onboarding_run', 'arguments': {'intake_id': 'intake-1'}}, context={})
        self.assertEqual(result['state'], 'awaiting_human_review')
        service.call.assert_called_once_with('onboarding_run', {'intake_id': 'intake-1'})
        for operation, args in [('approve', {'intake_id': 'intake-1'}), ('onboarding_run', {'intake_id': 'intake-1', 'approved': True})]:
            with self.assertRaises(DomainError): owner({'operation': operation, 'arguments': args}, context={})
        service.resolve.assert_not_called()
        self.assertEqual(service.call.call_count, 1)

    def test_owner_failure_preserves_review_identity_without_authority_secret_or_retry(self):
        service = Mock(); service.call.side_effect = OwnerFailure('review_host_unavailable', {
            'request_id': 'a' * 64, 'fingerprint': 'b' * 64,
            'review_url': 'http://127.0.0.1:9999/review/example', 'authority_key_file': '/private/secret'})
        owner, _ = self.bind_fake_owner(service)
        result = owner({'operation': 'onboarding_run', 'arguments': {'intake_id': 'intake-1'}}, context={})
        self.assertEqual(result['error']['code'], 'review_host_unavailable')
        self.assertIn('review_url', result['details'])
        self.assertNotIn('authority_key_file', result['details'])
        self.assertFalse(result['automatic_approval']); self.assertFalse(result['replayed'])
        self.assertEqual(service.call.call_count, 1); service.resolve.assert_not_called()

    def test_standalone_bindings_are_explicit_and_keyed_actions_keep_tool_identity(self):
        with tempfile.TemporaryDirectory() as directory, Controller(directory, agent_mode='standalone') as controller:
            client = Mock(); client.execute.return_value = {'state': 'unknown'}
            with patch('subprocess.Popen', side_effect=AssertionError('no process at binding')):
                dispatcher = build_standalone_dispatcher(controller, live_client=client)
            self.assertEqual(dispatcher.policy('spiceowl_live_control', {}), 'approval')
            self.assertEqual(dispatcher.policy('spiceowl_onboarding', {}), 'deny')
            context = {'request_key': 'question-1', 'thread_id': 'thread-1', 'turn_id': 'turn-1', 'tool_call_id': 'call-1'}
            payload = {'operation': 'submit', 'arguments': {'project_id': 'explicit-project'}}
            for _ in range(2): dispatcher.dispatch('spiceowl_live_control', payload, context=context)
            keys = [call.kwargs['idempotency_key'] for call in client.execute.call_args_list]
            self.assertEqual(keys[0], keys[1]); self.assertTrue(keys[0].startswith('codex-'))
            with self.assertRaises(DomainError): dispatcher.dispatch('spiceowl_live_control', payload)
            self.assertEqual(client.execute.call_count, 2)
            dispatcher.dispatch('spiceowl_live_control', {'operation': 'run', 'arguments': {'plan_id': 'a' * 64}}, context=context)
            client.execute.assert_called_with('run', {'plan_id': 'a' * 64}, idempotency_key=None)

    def test_lut_open_is_read_only_and_model_cannot_choose_a_path_or_write(self):
        with tempfile.TemporaryDirectory() as directory, Controller(directory) as controller:
            dispatcher = build_standalone_dispatcher(controller, pdk_lut_path='/operator/existing/cache.sqlite3')
            with patch('spiceowl.onboarding.pdk_lut.PdkLut') as cache:
                cache.return_value.__enter__.return_value.lookup.return_value = {'status': 'miss', 'ready_to_run': False}
                result = dispatcher.dispatch('spiceowl_pdk_lookup', {'operation': 'lookup', 'arguments': {}})
                self.assertEqual(result['status'], 'miss')
                self.assertTrue(cache.call_args.kwargs['read_only'])
                for args in ({'operation': 'put', 'arguments': {}}, {'operation': 'lookup', 'arguments': {}, 'path': '/foreign'}):
                    with self.assertRaises(DomainError): dispatcher.dispatch('spiceowl_pdk_lookup', args)
                self.assertEqual(cache.call_count, 1)

    def test_registered_run_uses_real_client_plan_key_across_agent_call_ids(self):
        from tests import test_experiment_workflow as workflow
        fixture = workflow.ExperimentWorkflowTests()
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        with Controller(fixture.root / 'console', agent_mode='standalone') as controller:
            dispatcher = build_standalone_dispatcher(controller, live_client=fixture.client)
            context = {'request_key': 'question-1', 'thread_id': 'thread-1', 'turn_id': 'turn-1', 'tool_call_id': 'call-1'}
            with patch.object(fixture.client, '_request', side_effect=[workflow.HEALTH, workflow.JOB]) as request:
                first = dispatcher.dispatch('spiceowl_live_control', {'operation': 'run', 'arguments': {'plan_id': workflow.PLAN_ID}}, context=context)
                second = dispatcher.dispatch('spiceowl_live_control', {'operation': 'run', 'arguments': {'plan_id': workflow.PLAN_ID}}, context=context | {'tool_call_id': 'call-2'})
            self.assertEqual(first['job_id'], second['job_id'])
            self.assertEqual(request.call_count, 2, 'One health read and one original POST; no duplicate run')
            payload = json.loads(request.call_args.args[2])
            self.assertEqual(payload['idempotency_key'], 'experiment-' + workflow.PLAN_ID)


if __name__ == '__main__': unittest.main()

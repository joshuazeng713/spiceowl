"""CircuitGit channel ownership leaves legacy Q&A records read-only."""

import copy
import tempfile
import unittest
from unittest.mock import patch

from spiceowl.agent_channel import CIRCUITGIT_URL
from spiceowl.domain.common import DomainError
from spiceowl.service import Controller
from spiceowl.storage.sqlite import Store


class AgentChannelControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.create('agent_profile', 'legacy-agent', {
            'schema': 'spiceowl.agent.v1', 'agent_id': 'legacy-agent', 'label': 'Saved legacy profile',
            'executable': '/private/legacy/codex', 'model': None, 'last_probe': None,
            'created_at': '2026-01-01T00:00:00Z', 'synthetic': False})
        for status in ('queued', 'running', 'outcome_unknown', 'completed'):
            record = {'qa_id': 'legacy-' + status, 'agent_id': 'legacy-agent',
                'question': 'Saved ' + status + ' question', 'status': status,
                'answer': 'Saved answer' if status == 'completed' else '',
                'request_key': 'original-' + status, 'request_digest': 'a' * 64,
                'profile_snapshot': {'executable': '/private/legacy/codex'},
                'model': 'old-model', 'provider': 'old-provider', 'native_thread_id': 'old-thread',
                'native_turn_id': 'old-turn', 'usage': None, 'error': None,
                'created_at': '2026-01-01T00:00:00Z', 'updated_at': '2026-01-01T00:01:00Z',
                'synthetic': False}
            self.store.create('qa', record['qa_id'], record)
        self.saved_questions = copy.deepcopy(self.store.list('qa'))
        self.saved_profiles = copy.deepcopy(self.store.list('agent_profile'))

    def assert_preserved(self):
        self.assertEqual(self.store.list('qa'), self.saved_questions)
        self.assertEqual(self.store.list('agent_profile'), self.saved_profiles)

    def test_start_read_and_restart_never_recover_legacy_questions_or_spawn_native(self):
        with (patch('spiceowl.agent.qa.Questions.recover', side_effect=AssertionError('No automatic recovery')) as recover,
              patch('spiceowl.agent.native.ask_agent', side_effect=AssertionError('No native inference')) as ask,
              patch('spiceowl.agent.native.probe_agent', side_effect=AssertionError('No native readiness')) as probe,
              patch('subprocess.Popen', side_effect=AssertionError('No native process')) as spawn):
            for _ in range(2):
                with Controller(self.temp.name) as app:
                    state = app.state()
                    self.assertEqual(state['mode'], 'engine_console')
                    self.assertFalse(state['capabilities']['real_inference'])
                    self.assertFalse(state['capabilities']['mcp_local_queries'])
                    self.assertTrue(state['capabilities']['sdk_read_views'])
                    self.assertEqual(state['agent_channel']['owner'], 'circuitgit')
                    self.assertEqual(state['agent_channel']['entry_url'], CIRCUITGIT_URL)
                    self.assertEqual(state['agent_channel']['connection_status'], 'not_checked')
                    self.assertFalse(state['agent_channel']['local_inference_enabled'])
                    self.assertTrue(state['agent_channel']['legacy_history_read_only'])
                    for saved in self.saved_questions:
                        actual = app.questions.get(saved['qa_id'])
                        self.assertEqual(actual['status'], saved['status'])
                        self.assertEqual(actual['answer'], saved['answer'])
                        self.assertEqual(actual['updated_at'], saved['updated_at'])
                        self.assertNotIn('profile_snapshot', actual)
                        self.assertNotIn('request_key', actual)
                    state['agent_channel']['owner'] = 'untrusted-change'
                    self.assertEqual(app.state()['agent_channel']['owner'], 'circuitgit')
                self.assert_preserved()
            recover.assert_not_called(); ask.assert_not_called(); probe.assert_not_called(); spawn.assert_not_called()

    def test_requests_and_readiness_are_rejected_before_claim_or_worker_creation(self):
        with Controller(self.temp.name) as app:
            with (patch.object(app.questions, 'claim', side_effect=AssertionError('No Q&A claim')) as claim,
                  patch.object(app.questions, 'run', side_effect=AssertionError('No Q&A execution')) as run,
                  patch.object(app._workers, 'submit', side_effect=AssertionError('No worker')) as worker,
                  patch.object(app._slots, 'acquire', side_effect=AssertionError('No native queue')) as queue,
                  patch.object(app.native_agents, 'probe', side_effect=AssertionError('No registry probe')) as probe,
                  patch('subprocess.Popen', side_effect=AssertionError('No native process')) as spawn):
                for data in (None, {}, {'agent_id': 'legacy-agent', 'question': 'A new question', 'request_key': 'new-key'},
                             {'agent_id': 'legacy-agent', 'question': 'Saved queued question', 'request_key': 'original-queued'}):
                    with self.subTest(data=data), self.assertRaises(DomainError) as error:
                        app.ask(data)
                    self.assertEqual(error.exception.code, 'agent_channel_owned_by_circuitgit')
                for identity in ('legacy-agent', 'not-registered'):
                    with self.assertRaises(DomainError) as error:
                        app.probe_native_agent(identity)
                    self.assertEqual(error.exception.code, 'agent_channel_owned_by_circuitgit')
                claim.assert_not_called(); run.assert_not_called(); worker.assert_not_called()
                queue.assert_not_called(); probe.assert_not_called(); spawn.assert_not_called()
            self.assert_preserved()


if __name__ == '__main__':
    unittest.main()

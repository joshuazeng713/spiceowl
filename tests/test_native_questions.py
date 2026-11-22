import concurrent.futures
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from spiceowl.agent.qa import NativeRegistry
from spiceowl.domain.common import DomainError
from spiceowl.service import Controller


class NativeQuestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Controller(self.temp.name)
        self.app.seed_demo()
        executable = Path(self.temp.name) / "codex"
        executable.write_text("#!/bin/sh\nexit 1\n")
        executable.chmod(0o700)
        self.profile = {"schema": "spiceowl.agent.v1", "agent_id": "test-agent", "label": "Test native runtime",
                        "executable": str(executable), "model": None}
        self.app.native_agents.add(self.profile)

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def data(self, key="question-test"):
        return {"agent_id": "test-agent", "question": "Fixture question", "request_key": key}

    def wait(self, qa_id):
        for _ in range(100):
            record = self.app.questions.get(qa_id)
            if record["status"] not in {"queued", "running"}:
                return record
            time.sleep(.01)
        self.fail("Fixture Q&A did not finish")

    def test_real_branch_result_is_saved_and_duplicate_key_does_not_regenerate(self):
        before = self.app.state()["runs"]
        reply = {"status": "completed", "answer": "A fixture answer.", "model": "fixture-model", "provider": "fixture-provider",
                 "native_thread_id": "thread-fixture", "native_turn_id": "turn-fixture", "usage": {"input_tokens": 3, "output_tokens": 4}, "error": None}
        with patch("spiceowl.agent.native.ask_agent", return_value=reply) as native:
            receipt = self.app.ask(self.data())
            answer = self.wait(receipt["qa_id"])
            repeated = self.app.ask(self.data())
            self.assertEqual(receipt["qa_id"], repeated["qa_id"])
            self.assertFalse(repeated["created"])
            native.assert_called_once()
            self.assertEqual(answer["answer"], reply["answer"])
            self.assertEqual(answer["provider"], reply["provider"])
            self.assertFalse(answer["synthetic"])
            self.assertNotIn("profile_snapshot", answer)
        self.assertEqual(before, self.app.state()["runs"])
        self.assertNotIn(str(self.temp.name), str(self.app.state()["native_agents"]))

    def test_concurrent_duplicate_requests_claim_once(self):
        with patch("spiceowl.agent.native.ask_agent", return_value={"status": "completed", "answer": "Fixture"}) as native:
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as workers:
                receipts = list(workers.map(lambda _: self.app.ask(self.data()), range(3)))
            self.assertEqual(len({r["qa_id"] for r in receipts}), 1)
            self.assertEqual(sum(r["created"] for r in receipts), 1)
            self.wait(receipts[0]["qa_id"])
            native.assert_called_once()

    def test_exception_preserves_unknown_and_blocks_silent_retry(self):
        with patch("spiceowl.agent.native.ask_agent", side_effect=ConnectionError("PRIVATE_PROVIDER_DETAIL")) as native:
            receipt = self.app.ask(self.data())
            result = self.wait(receipt["qa_id"])
            self.assertEqual(result["status"], "outcome_unknown")
            self.assertNotIn("PRIVATE_PROVIDER_DETAIL", str(result))
            self.assertFalse(self.app.ask(self.data())["created"])
            with self.assertRaises(DomainError): self.app.ask(self.data("different-key"))
            native.assert_called_once()

    def test_restart_recovers_unfinished_record_without_inference(self):
        record, created = self.app.questions.claim(self.data())
        self.assertTrue(created)
        self.app.close()
        with patch("spiceowl.agent.native.ask_agent") as native:
            self.app = Controller(self.temp.name)
            result = self.app.questions.get(record["qa_id"])
            self.assertEqual(result["status"], "outcome_unknown")
            self.assertFalse(self.app.ask(self.data())["created"])
            native.assert_not_called()

    def test_same_key_different_question_rejected_and_extra_fields_rejected(self):
        self.app.questions.claim(self.data())
        with self.assertRaises(DomainError): self.app.ask(self.data() | {"question": "Other"})
        with self.assertRaises(DomainError): self.app.ask(self.data() | {"model": "unapproved-override"})

    def test_metadata_probe_does_not_call_inference_or_recover_live_question(self):
        record, _ = self.app.questions.claim(self.data())
        with patch("spiceowl.agent.native.ask_agent") as ask, patch("spiceowl.agent.native.probe_agent", return_value={"transport": "reachable"}):
            registry = NativeRegistry(self.temp.name)
            result = registry.probe("test-agent")
            self.assertEqual(result["inference_access"], "unverified")
            self.assertEqual(self.app.questions.get(record["qa_id"])["status"], "queued")
            ask.assert_not_called()


if __name__ == "__main__":
    unittest.main()

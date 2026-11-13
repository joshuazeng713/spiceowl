import concurrent.futures
import json
import subprocess
import sys
import tempfile
import time
import unittest

from spiceowl.domain.common import DomainError, digest
from spiceowl.service import Controller


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Controller(self.temp.name)
        self.app.seed_demo()

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def request(self, key="test-request", connection="demo-agent", run="demo-disconnect"):
        return {"connection_id": connection, "run_id": run, "question": "Explain this synthetic evidence.", "request_key": key}

    def wait(self, turn_id):
        for _ in range(100):
            turn = self.app.store.get_turn(turn_id)
            if turn["status"] not in ("dispatching", "running"):
                return turn
            time.sleep(.025)
        self.fail("Fake turn did not terminate")

    def test_seed_is_idempotent_and_preserves_progress(self):
        advanced = self.app.advance("demo-disconnect")
        self.app.seed_demo()
        self.assertEqual(self.app.store.get("run", "demo-disconnect"), advanced)
        self.assertEqual(len(self.app.state()["connections"]), 2)
        self.assertEqual(len(self.app.state()["runs"]), 4)

    def test_complete_diagnosis_and_duplicate_key(self):
        receipt = self.app.diagnose(self.request())
        turn = self.wait(receipt["turn_id"])
        self.assertEqual(turn["status"], "completed")
        self.assertIsNotNone(turn["proposal"])
        self.assertFalse(turn["decision"]["dispatch_enabled"])
        repeated = self.app.diagnose(self.request())
        self.assertEqual(receipt["turn_id"], repeated["turn_id"])
        self.assertFalse(repeated["created"])
        events = self.app.store.session(turn["session_id"])["events"]
        self.assertEqual(sum(e["kind"] == "turn.accepted" for e in events), 1)
        with self.assertRaises(DomainError) as error:
            self.app.diagnose(self.request() | {"question": "different"})
        self.assertEqual(error.exception.code, "conflict")

    def test_concurrent_duplicate_requests_dispatch_once(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as workers:
            receipts = list(workers.map(lambda _: self.app.diagnose(self.request()), range(3)))
        self.assertEqual(len({r["turn_id"] for r in receipts}), 1)
        self.assertEqual(sum(r["created"] for r in receipts), 1)
        self.assertEqual(self.wait(receipts[0]["turn_id"])["status"], "completed")

    def test_restart_preserves_unknown_without_replay(self):
        from spiceowl.agent.diagnosis import build_evidence
        data = self.request()
        run = self.app.store.get("run", data["run_id"])
        turn, _ = self.app.store.claim_turn(data["request_key"], digest(data), data["connection_id"], data["run_id"], data["question"], build_evidence(run))
        self.app.close()
        self.app = Controller(self.temp.name)
        recovered = self.app.store.get_turn(turn["turn_id"])
        self.assertEqual(recovered["status"], "outcome_unknown")
        self.assertEqual(self.app.diagnose(data)["turn_id"], turn["turn_id"])
        with self.assertRaises(DomainError) as error:
            self.app.diagnose(self.request("new-key"))
        self.assertEqual(error.exception.code, "unresolved_turn")
        self.assertEqual(self.app.store.get("run", run["run_id"]), run)

    def test_exclusive_controller_does_not_recover_live_work(self):
        with self.assertRaises(DomainError) as error:
            Controller(self.temp.name)
        self.assertEqual(error.exception.code, "controller_busy")

    def test_cancellation_does_not_touch_run(self):
        before = self.app.store.get("run", "demo-disconnect")
        receipt = self.app.diagnose(self.request())
        result = self.app.store.cancel_turn(receipt["turn_id"])
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.app.store.get("run", "demo-disconnect"), before)
        time.sleep(.15)
        self.assertEqual(self.app.store.get_turn(receipt["turn_id"])["status"], "cancelled")

    def test_disabled_or_removed_connections_cannot_dispatch(self):
        self.app.change_connection("demo-agent", {"enabled": False})
        with self.assertRaises(DomainError): self.app.diagnose(self.request())
        self.app.remove_connection("demo-model")
        self.assertEqual(len(self.app.state()["connections"]), 1)
        with self.assertRaises(DomainError): self.app.probe("demo-model", "models")

    def test_disconnect_is_partial_and_blocks_dependent_work(self):
        connection = self.app.add_connection({"label": "Disconnect", "kind": "model", "backend": "fake_model", "scenario": "disconnect"})
        receipt = self.app.diagnose(self.request(connection=connection["connection_id"]))
        turn = self.wait(receipt["turn_id"])
        self.assertEqual(turn["status"], "outcome_unknown")
        self.assertIsNone(turn["proposal"])
        with self.assertRaises(DomainError): self.app.diagnose(self.request("other-key", "demo-agent"))

    def test_event_cursor_survives_restart_and_connection_removal(self):
        receipt = self.app.diagnose(self.request())
        turn = self.wait(receipt["turn_id"])
        view = self.app.store.session(turn["session_id"])
        self.app.remove_connection("demo-agent")
        self.app.close()
        self.app = Controller(self.temp.name)
        self.assertEqual(self.app.store.session(turn["session_id"]), view)
        self.assertEqual(self.app.store.session(turn["session_id"], view["next_cursor"])["events"], [])

    def test_draft_and_real_connection_rejected_without_storage(self):
        before = self.app.state()["connections"]
        for data in ({"example_only": True}, {"label": "real", "kind": "agent", "backend": "codex_app_server"}, {"label": "secret", "kind": "model", "backend": "fake_model", "api_key": "SENTINEL_NOT_A_KEY"}):
            with self.assertRaises(DomainError): self.app.add_connection(data)
        self.assertEqual(self.app.state()["connections"], before)

    def test_cli_read_status_works_while_service_owns_controller(self):
        result = subprocess.run([sys.executable, "-m", "spiceowl", "--data-dir", self.temp.name, "status"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)["runs"]), 4)

    def test_valid_failure_diagnosis_does_not_repair(self):
        run = self.app.store.get("run", "demo-target_miss")
        for _ in range(20):
            if run["finished"]: break
            run = self.app.advance(run["run_id"])
        self.assertEqual((run["measurement_validity"], run["specification_result"]), ("valid", "fail"))
        receipt = self.app.diagnose(self.request(run=run["run_id"]))
        turn = self.wait(receipt["turn_id"])
        self.assertEqual(turn["status"], "completed")
        self.assertEqual(turn["proposal"]["kind"], "report")
        self.assertEqual(self.app.store.get("run", run["run_id"]), run)


if __name__ == "__main__":
    unittest.main()

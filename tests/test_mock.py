"""Contract checks for offline synthetic replay, not simulator verification."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from spiceowl.adapters.executors import mock
from spiceowl.domain.common import DomainError, require_id


RUN_FIELDS = {
    "run_id", "experiment_id", "attempt_id", "submission_key", "execution_identity",
    "label", "scenario", "synthetic", "created_at", "updated_at", "cursor",
    "execution", "observation", "measurement_validity", "specification_result",
    "progress", "resource_history", "events", "finished",
}
EVENT_FIELDS = {"event_id", "sequence", "kind", "observed_at", "message", "facts"}
RESOURCE_FIELDS = {"sequence", "simulated_time_us", "memory_mb"}
IDENTITY_FIELDS = ("run_id", "experiment_id", "attempt_id", "submission_key",
                   "execution_identity", "scenario", "label", "created_at")


class MockReplayTests(unittest.TestCase):
    def finish(self, scenario):
        run = mock.create_run(scenario, "test-run")
        for _ in range(10):
            if run["finished"]:
                return run
            run = mock.advance_run(run)
        self.fail("Scenario did not terminate within its finite replay bound.")

    def test_scenario_catalog_is_exact_and_independent(self):
        catalog = mock.list_scenarios()
        self.assertEqual([item["scenario"] for item in catalog],
                         ["disconnect", "oom", "completed", "target_miss"])
        for item in catalog:
            self.assertEqual(set(item), {"scenario", "label", "description"})
            self.assertTrue(item["label"] and item["description"])
        catalog[0]["label"] = "changed"
        catalog.pop()
        self.assertEqual(len(mock.list_scenarios()), 4)
        self.assertNotEqual(mock.list_scenarios()[0]["label"], "changed")

    def test_initial_run_contract_and_json_compatibility(self):
        for item in mock.list_scenarios():
            with self.subTest(scenario=item["scenario"]):
                run = mock.create_run(item["scenario"], "run-123")
                self.assertEqual(set(run), RUN_FIELDS)
                self.assertEqual(run["run_id"], "run-123")
                self.assertEqual(run["cursor"], 0)
                self.assertIs(run["synthetic"], True)
                self.assertIs(run["finished"], False)
                self.assertEqual(run["measurement_validity"], "pending")
                self.assertEqual(run["specification_result"], "not_evaluated")
                self.assertEqual(run, json.loads(json.dumps(run, allow_nan=False)))
                self.assertEqual(len(run["events"]), 1)
                self.assertGreaterEqual(run["events"][0]["sequence"], 1)
                self.assertEqual(set(run["events"][0]), EVENT_FIELDS)
                self.assertEqual(set(run["resource_history"][0]), RESOURCE_FIELDS)
                for field in ("experiment_id", "attempt_id", "submission_key"):
                    self.assertEqual(require_id(run[field]), run[field])

    def test_disconnect_preserves_state_then_reconnects_same_identity(self):
        initial = mock.create_run("disconnect", "run-disconnect")
        disconnected = mock.advance_run(initial)
        self.assertEqual(disconnected["observation"], "unknown")
        self.assertEqual(disconnected["execution"], initial["execution"])
        self.assertEqual(disconnected["progress"], initial["progress"])
        self.assertIsNone(disconnected["resource_history"][-1]["memory_mb"])
        self.assertIsNone(disconnected["resource_history"][-1]["simulated_time_us"])
        self.assertFalse(disconnected["finished"])
        reconnected = mock.advance_run(disconnected)
        self.assertEqual(reconnected["observation"], "current")
        self.assertEqual(reconnected["execution"], "running")
        self.assertEqual(reconnected["execution_identity"], initial["execution_identity"])
        self.assertTrue(reconnected["events"][-1]["facts"]["identity_match"])
        self.assertFalse(reconnected["events"][-1]["facts"]["new_attempt_created"])
        final = mock.advance_run(reconnected)
        self.assertEqual(final["execution"], "completed")
        for run in (disconnected, reconnected, final):
            for field in IDENTITY_FIELDS:
                self.assertEqual(run[field], initial[field])

    def test_oom_carries_confirmed_synthetic_evidence(self):
        run = self.finish("oom")
        self.assertEqual(run["execution"], "failed")
        self.assertEqual(run["observation"], "current")
        self.assertIn(run["measurement_validity"], {"invalid", "indeterminate"})
        self.assertEqual(run["specification_result"], "not_evaluated")
        self.assertLess(run["progress"], 1)
        facts = run["events"][-1]["facts"]
        self.assertTrue(facts["synthetic"])
        self.assertTrue(facts["execution_failure_confirmed"])
        self.assertEqual(facts["reason"], "OUT_OF_MEMORY")
        self.assertFalse(facts["measurement_window_covered"])
        self.assertLess(facts["last_confirmed_simulated_time_us"], facts["required_window_end_us"])

    def test_completed_and_target_miss_keep_validity_separate(self):
        for scenario, verdict in (("completed", "pass"), ("target_miss", "fail")):
            with self.subTest(scenario=scenario):
                run = self.finish(scenario)
                self.assertEqual(run["execution"], "completed")
                self.assertEqual(run["measurement_validity"], "valid")
                self.assertEqual(run["specification_result"], verdict)
                self.assertEqual(run["progress"], 1)
                self.assertTrue(run["events"][-1]["facts"]["measurement_window_covered"])

    def test_replay_is_deterministic_from_same_json_snapshot(self):
        for item in mock.list_scenarios():
            run = mock.create_run(item["scenario"], "restarted-run")
            while not run["finished"]:
                restored = json.loads(json.dumps(run))
                with patch.object(mock, "utcnow", return_value="2026-11-13T01:00:00+00:00"):
                    next_run = mock.advance_run(run)
                    after_restart = mock.advance_run(restored)
                    self.assertEqual(next_run, after_restart)
                with patch.object(mock, "utcnow", return_value="2026-11-13T02:00:00+00:00"):
                    repeat = mock.advance_run(restored)
                self.assertEqual(next_run["cursor"], repeat["cursor"])
                self.assertEqual(next_run["resource_history"], repeat["resource_history"])
                repeat["updated_at"] = next_run["updated_at"]
                repeat["events"][-1]["observed_at"] = next_run["events"][-1]["observed_at"]
                self.assertEqual(next_run, repeat)
                run = after_restart

    def test_history_appends_once_and_ids_support_deduplication(self):
        run = mock.create_run("disconnect", "dedup-run")
        seen = {event["event_id"]: event for event in run["events"]}
        while not run["finished"]:
            previous = run
            run = mock.advance_run(run)
            duplicate = mock.advance_run(previous)
            self.assertEqual(run["events"][:-1], previous["events"])
            self.assertEqual(run["resource_history"][:-1], previous["resource_history"])
            self.assertEqual(run["cursor"], previous["cursor"] + 1)
            for replay in (run, duplicate):
                for event in replay["events"]:
                    seen[event["event_id"]] = event
            self.assertEqual(len(seen), len(run["events"]))
            self.assertEqual([event["sequence"] for event in run["events"]],
                             list(range(1, len(seen) + 1)))
        self.assertLessEqual(len(run["events"]), 4)
        self.assertLessEqual(len(run["resource_history"]), 4)

    def test_terminal_no_op_keeps_timestamps_and_returns_a_copy(self):
        for item in mock.list_scenarios():
            run = self.finish(item["scenario"])
            original = deepcopy(run)
            with patch.object(mock, "utcnow", side_effect=AssertionError("No clock on no-op")):
                result = mock.advance_run(run)
                self.assertEqual(result, original)
                self.assertIsNot(result, run)
                self.assertEqual(mock.advance_run(json.loads(json.dumps(result))), original)
            result["events"][0]["facts"]["synthetic"] = False
            result["execution_identity"]["kind"] = "modified"
            self.assertEqual(run, original)

    def test_every_advance_preserves_arguments_and_nested_history(self):
        for item in mock.list_scenarios():
            run = mock.create_run(item["scenario"], "immutable-run")
            initial = deepcopy(run)
            while not run["finished"]:
                original = deepcopy(run)
                result = mock.advance_run(run)
                self.assertEqual(run, original)
                for field in IDENTITY_FIELDS:
                    self.assertEqual(result[field], initial[field])
                result["events"][0]["facts"]["source"] = "changed"
                result["resource_history"][0]["memory_mb"] = -1
                result["execution_identity"]["kind"] = "changed"
                self.assertEqual(run, original)
                run = mock.advance_run(run)

    def test_run_scoped_ids_and_longest_allowed_input_id(self):
        left = mock.create_run("completed", "first")
        right = mock.create_run("completed", "second")
        for field in ("experiment_id", "attempt_id", "submission_key", "execution_identity"):
            self.assertNotEqual(left[field], right[field])
        self.assertNotEqual(left["events"][0]["event_id"], right["events"][0]["event_id"])
        with patch.object(mock, "utcnow", return_value="2026-11-13T00:00:00+00:00"):
            self.assertEqual(mock.create_run("oom", "same"), mock.create_run("oom", "same"))
        long_id = mock.create_run("completed", "x" * 96)
        self.assertEqual(mock.advance_run(long_id)["run_id"], "x" * 96)
        for field in ("experiment_id", "attempt_id", "submission_key"):
            require_id(long_id[field])

    def test_invalid_scenarios_and_run_ids(self):
        for scenario in (None, "", "unknown", "OOM", "../completed", True, 1, [], {}):
            with self.subTest(scenario=scenario), self.assertRaises(DomainError):
                mock.create_run(scenario, "valid-id")
        for run_id in (None, "", "x" * 97, "../../run", "has space", "run\n", True, 1, [], {}):
            with self.subTest(run_id=run_id), self.assertRaises(DomainError):
                mock.create_run("completed", run_id)

    def test_invalid_snapshots_are_rejected_without_mutation(self):
        valid = mock.create_run("completed", "valid-id")
        for value in (None, [], True, 1, "run", {}):
            with self.subTest(value=value), self.assertRaises(DomainError):
                mock.advance_run(value)
        changes = [
            ("cursor", -1), ("cursor", 3), ("cursor", True), ("cursor", 0.0),
            ("cursor", "0"), ("scenario", "missing"), ("scenario", []),
            ("run_id", "other-run"), ("submission_key", "other-key"),
            ("execution_identity", {}), ("synthetic", False), ("synthetic", 1),
            ("finished", True), ("execution", "completed"), ("observation", "unknown"),
            ("measurement_validity", "valid"), ("specification_result", "pass"),
            ("progress", float("nan")), ("progress", float("inf")),
            ("progress", -1), ("progress", 2), ("events", []), ("events", {}),
            ("resource_history", []), ("updated_at", "bad"), ("created_at", "bad"),
            ("parameters", {"tolerance": "changed"}),
        ]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                altered = deepcopy(valid)
                altered[field] = value
                original = deepcopy(altered)
                with self.assertRaises(DomainError):
                    mock.advance_run(altered)
                self.assertEqual(altered, original)
        for field in RUN_FIELDS:
            altered = deepcopy(valid)
            del altered[field]
            with self.subTest(missing=field), self.assertRaises(DomainError):
                mock.advance_run(altered)

    def test_malformed_nested_events_and_resources(self):
        run = mock.create_run("oom", "nested-test")
        changes = [
            ("event_id", "forged"), ("sequence", 0), ("sequence", True),
            ("kind", "execute.command"), ("message", "changed"),
            ("facts", {"synthetic": True, "command": "forbidden"}),
            ("observed_at", None), ("observed_at", "invalid"),
            ("observed_at", "2026-11-13T00:00:00"), ("observed_at", "x" * 65),
            ("unknown", "field"),
        ]
        for field, value in changes:
            altered = deepcopy(run)
            altered["events"][0][field] = value
            with self.subTest(field=field), self.assertRaises(DomainError):
                mock.advance_run(altered)
        for value in (None, {}, "event"):
            altered = deepcopy(run)
            altered["events"][0] = value
            with self.subTest(event=value), self.assertRaises(DomainError):
                mock.advance_run(altered)
        altered = deepcopy(run)
        altered["resource_history"][0]["memory_mb"] = True
        with self.assertRaises(DomainError):
            mock.advance_run(altered)

    def test_mock_exposes_no_submission_or_cancellation_api(self):
        for name in ("submit", "cancel", "cancel_owned"):
            self.assertFalse(hasattr(mock, name))


if __name__ == "__main__":
    unittest.main()

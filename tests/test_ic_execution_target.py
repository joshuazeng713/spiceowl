"""Frozen resource-target projection, cache identity and read-only MCP tests."""

import copy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from spiceowl.domain.common import DomainError
from spiceowl.integrations.ic_workbench import ICGatewayBackend, LoopbackGatewayClient, validate_submission, validate_supervision
from spiceowl.mcp.views import ReadViews
from spiceowl.storage.readonly import ReadStore
from test_ic_workbench import CONFIG, OBSERVED, lookup_fixture, run_fixture, snapshot_fixture


ROOT = Path(__file__).resolve().parents[1]
FIXED = {"mode": "fixed_site", "host_id": None, "plan_id": None,
         "resource_observed_at": None, "resource_reservation": False}
POOL = {"mode": "resource_pool", "host_id": "fixture-host", "plan_id": "a" * 64,
        "resource_observed_at": "2026-09-01T00:00:00+08:00", "resource_reservation": False}
GOOD_TIMES = (None, "2026-09-01T00:00:00Z", "2026-09-01T00:00:00+08:00",
              "2026-09-01T00:00:00.123456-05:30", "2024-02-29T23:59:59Z")
BAD_TIMES = ("", "PRIVATE/path", "2026-09-01", "2026-09-01T00:00:00", "2026-09-01 00:00:00Z",
             "20260901T000000Z", "2026-09-01T00:00:00+0800", "2026-09-01T00:00:00+00:60",
             "2026-09-01T00:00:00+24:00", "2026-02-30T00:00:00Z", "2025-02-29T00:00:00Z",
             "0000-01-01T00:00:00Z", "2026-09-01T24:00:00Z", "2026-09-01T00:00:00Z\n",
             "2026-09-01T00:00:00." + "1" * 50 + "Z", 1, True, [], {})


def bad_targets():
    values = [None, [], "PRIVATE"]
    values += [{key: value for key, value in POOL.items() if key != missing} for missing in POOL]
    values += [POOL | {"extra": "PRIVATE"}, POOL | {"mode": "unknown"}]
    for key, cases in {
        "resource_reservation": (True, 0, None, "false"),
        "host_id": (None, "", "_host", "h" * 97, "PRIVATE/path", "host\n", 1, {}),
        "plan_id": (None, "", "a" * 63, "a" * 65, "A" * 64, "a" * 64 + "\n", True),
        "resource_observed_at": BAD_TIMES,
    }.items():
        values += [POOL | {key: value} for value in cases]
    values += [FIXED | {key: value} for key, value in (("host_id", "host"), ("plan_id", "b" * 64),
                                                      ("resource_observed_at", OBSERVED))]
    return values


class ICExecutionTargetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backend = ICGatewayBackend(self.temp.name)
        self.backend.register(CONFIG)

    def sync(self, runs, single_site=False):
        snapshot = snapshot_fixture(*runs) | {"single_site": single_site}
        with patch.object(LoopbackGatewayClient, "supervision", return_value=snapshot):
            return self.backend.refresh(CONFIG["id"])

    def test_legacy_fixed_pool_and_nullable_time_round_trip_without_new_freshness(self):
        sources = [run_fixture("legacy"), run_fixture("fixed") | {"execution_target": FIXED}]
        sources += [run_fixture("pool-" + str(index)) | {"execution_target": POOL | {"resource_observed_at": value},
                    "library_id": "_" + "L" * 179, "cell_id": "_C"} for index, value in enumerate(GOOD_TIMES)]
        result = self.sync(sources)
        self.assertTrue(result["updated"])
        self.assertIs(result["snapshot"]["single_site"], False)
        before = self.backend.store.path.read_bytes()
        with patch.object(LoopbackGatewayClient, "_get", side_effect=AssertionError("No live query")), \
                patch("subprocess.Popen", side_effect=AssertionError("No process")), \
                patch("spiceowl.storage.sqlite.Store.__init__", side_effect=AssertionError("No writable reader")):
            views = ReadViews(ReadStore(self.temp.name))
            listed = {run["run_id"]: run for run in views.list_ic_runs(CONFIG["id"])["runs"]}
            for source in sources:
                detail = views.get_ic_run(CONFIG["id"], source["run_id"])["run"]
                for record in (detail, listed[source["run_id"]]):
                    self.assertEqual(record.get("execution_target"), source.get("execution_target"))
                    self.assertEqual("execution_target" in record, "execution_target" in source)
                    self.assertEqual(record["source_commit"], source["source_commit"])
                    self.assertEqual(record["latest_observed_at"], OBSERVED)
                    self.assertEqual(record["measurement"]["state"], "indeterminate")
                    self.assertTrue(record["synthetic"])
        self.assertEqual(self.backend.store.path.read_bytes(), before)

    def test_single_site_is_boolean_and_is_not_inferred_from_one_record(self):
        source = run_fixture() | {"execution_target": POOL}
        for mode in (True, False):
            snapshot = snapshot_fixture(source) | {"single_site": mode}
            self.assertEqual(validate_supervision(snapshot, CONFIG["project_id"]), snapshot)
        for mode in (0, 1, None, "false", [], {}):
            with self.subTest(mode=mode), self.assertRaises(DomainError):
                validate_supervision(snapshot_fixture(source) | {"single_site": mode}, CONFIG["project_id"])

    def test_host_ids_use_registry_96_character_boundary_without_truncation(self):
        for length in (81, 96):
            target = POOL | {"host_id": "h" * length}
            source = run_fixture("host-bound-" + str(length)) | {"execution_target": target}
            validate_supervision(snapshot_fixture(source), CONFIG["project_id"])
            self.assertTrue(self.sync([source])["updated"])
            read = ReadViews(ReadStore(self.temp.name)).get_ic_run(CONFIG["id"], source["run_id"])["run"]
            self.assertEqual(read["execution_target"]["host_id"], target["host_id"])

    def test_invalid_missing_and_polluted_targets_never_enter_cache(self):
        good = run_fixture() | {"execution_target": copy.deepcopy(POOL)}
        self.assertTrue(self.sync([good])["updated"])
        for target in bad_targets():
            with self.subTest(target=target):
                result = self.sync([run_fixture("partial-new"), good | {"execution_target": target}])
                self.assertFalse(result["updated"])
                self.assertEqual([run["run_id"] for run in result["runs"]], [good["run_id"]])
                self.assertEqual(result["runs"][0]["execution_target"], POOL)
                self.assertNotIn("PRIVATE", json.dumps(result))

    def test_known_target_changes_or_disappears_roll_back_entire_snapshot(self):
        source = run_fixture() | {"execution_target": copy.deepcopy(POOL)}
        self.assertTrue(self.sync([run_fixture()])["updated"])
        self.assertTrue(self.sync([source])["updated"])
        changed = [source | {"execution_target": POOL | {key: value}} for key, value in (
            ("host_id", "different-host"), ("plan_id", "b" * 64), ("resource_observed_at", OBSERVED),
            ("resource_observed_at", None))]
        changed += [source | {"execution_target": FIXED}, run_fixture()]
        for candidate in changed:
            with self.subTest(candidate=candidate):
                result = self.sync([run_fixture("partial-new"), candidate])
                self.assertFalse(result["updated"])
                self.assertEqual(result["gateway"]["observation"]["error"]["code"], "ic_gateway_identity")
                self.assertEqual(len(result["runs"]), 1)
                saved = result["runs"][0]
                self.assertEqual(saved["execution_target"], POOL)
                self.assertEqual(saved["execution"], source["execution"])
                self.assertEqual(saved["observation"]["state"], "unobserved")

    def test_nullable_time_is_frozen_as_part_of_the_known_target(self):
        source = run_fixture() | {"execution_target": POOL | {"resource_observed_at": None}}
        self.assertTrue(self.sync([source])["updated"])
        self.assertFalse(self.sync([source | {"execution_target": POOL}])["updated"])

    def test_expected_identity_and_lookup_preserve_original_mapping_on_conflict(self):
        source = run_fixture() | {"execution_target": copy.deepcopy(POOL)}
        lookup = lookup_fixture(run=source)
        validate_submission(lookup, "fixture-key", CONFIG["project_id"], {"execution_target": POOL})
        for expected in (FIXED, None, POOL | {"plan_id": "b" * 64}):
            with self.assertRaises(DomainError):
                validate_submission(lookup, "fixture-key", CONFIG["project_id"], {"execution_target": expected})
        legacy = lookup_fixture(run=run_fixture())
        validate_submission(legacy, "fixture-key", CONFIG["project_id"], {"execution_target": None})
        with self.assertRaises(DomainError):
            validate_submission(legacy, "fixture-key", CONFIG["project_id"], {"execution_target": POOL})
        with patch.object(LoopbackGatewayClient, "submission", return_value=lookup):
            self.backend.lookup_submission(CONFIG["id"], "fixture-key", {"execution_target": POOL})
        with patch.object(LoopbackGatewayClient, "submission", return_value=legacy):
            failed = self.backend.lookup_submission(CONFIG["id"], "fixture-key")
        self.assertEqual(failed["observation"]["state"], "unobserved")
        self.assertEqual(failed["run"]["execution_target"], POOL)
        self.assertEqual(failed["last_known_run_id"], source["run_id"])
        self.assertFalse(failed["resubmit_authorized"])

    def test_mcp_rejects_damaged_target_without_truncating_or_echoing_it(self):
        source = run_fixture() | {"execution_target": copy.deepcopy(POOL)}
        self.sync([source])
        views = ReadViews(ReadStore(self.temp.name))
        for target in bad_targets():
            self.backend.store.update("ic_run:" + CONFIG["id"], source["run_id"], lambda row: row | {"execution_target": target})
            before = self.backend.store.path.read_bytes()
            for read in (lambda: views.list_ic_runs(CONFIG["id"]), lambda: views.get_ic_run(CONFIG["id"], source["run_id"])):
                with self.subTest(target=target), self.assertRaises(DomainError) as raised:
                    read()
                self.assertEqual(raised.exception.code, "storage_record")
                self.assertNotIn("PRIVATE", str(raised.exception))
            self.assertEqual(self.backend.store.path.read_bytes(), before)

    def test_json_schema_and_runtime_accept_same_resource_target_cases(self):
        try:
            from jsonschema import Draft202012Validator, FormatChecker
        except ImportError:
            self.skipTest("JSON schema checks require the optional MCP dependency")
        schema = json.loads((ROOT / "docs/contracts/ic-workbench-supervision.v1.json").read_text())
        self.assertEqual(schema["x-contract-version"], "1.2.0")
        formats = FormatChecker()
        # The base jsonschema dependency need not install its optional RFC3339
        # checker. Activate calendar/timezone checking for our bounded pattern.
        if "date-time" not in formats.checkers:
            @formats.checks("date-time", raises=(ValueError, OverflowError))
            def calendar_timestamp(value):
                return not isinstance(value, str) or datetime.fromisoformat(value).utcoffset() is not None
        validator = Draft202012Validator(schema, format_checker=formats)
        for target in (FIXED, *(POOL | {"resource_observed_at": value} for value in GOOD_TIMES)):
            source = run_fixture() | {"execution_target": target}
            validator.validate(snapshot_fixture(source) | {"single_site": False})
            validator.validate(lookup_fixture(run=source))
            validate_supervision(snapshot_fixture(source), CONFIG["project_id"])
        for target in bad_targets():
            with self.subTest(target=target):
                snapshot = snapshot_fixture(run_fixture() | {"execution_target": target})
                self.assertFalse(validator.is_valid(snapshot))
                with self.assertRaises(DomainError):
                    validate_supervision(snapshot, CONFIG["project_id"])


if __name__ == "__main__":
    unittest.main()

"""Pure P0.3 placement/origin tests using fictional stored observations only."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch

from spiceowl.domain.common import DomainError
from spiceowl.scheduling.origins import plan_execution, validate_origin
from spiceowl.scheduling.placement import recommend_hosts


GIB = 1024**3
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


def requirements(**updates):
    return {"cpu_count": 4, "memory_bytes": 8 * GIB, "simulator": "spectre"} | updates


def host(host_id="host-a", *, resource_age=10, version_age=60, **metrics):
    identity = {"verified": True, "hostname": f"{host_id}.example.invalid", "username": "example-user"}
    raw = {
        "cpu_count": 16, "cpu_busy_percent": 10.0, "load_1m": 1.5, "load_5m": 1.0,
        "memory_total_bytes": 64 * GIB, "memory_available_bytes": 48 * GIB,
        "home_disk_available_bytes": 200 * GIB,
    } | metrics
    return {
        "schema": "spiceowl.host.v2", "host_id": host_id, "label": f"Example {host_id}",
        "ssh_target": "example-route", "ssh_hostname": identity["hostname"],
        "expected_host": identity["hostname"], "expected_user": identity["username"],
        "tools": [{"kind": "spectre", "executable": "/opt/example/spectre"}],
        "created_at": (NOW - timedelta(days=1)).isoformat(), "revision": 1, "synthetic": False,
        "last_resources": {
            "host_id": host_id, "synthetic": False, "scope": "host_resources",
            "observed_at": (NOW - timedelta(seconds=resource_age)).isoformat(),
            "transport": "reachable", "identity": deepcopy(identity), "resources": raw, "error": None,
        },
        "last_probe": {
            "host_id": host_id, "synthetic": False, "scope": "connectivity_only",
            "observed_at": (NOW - timedelta(seconds=version_age)).isoformat(),
            "transport": "reachable", "identity": deepcopy(identity),
            "tools": [{"kind": "spectre", "status": "available", "version": "99.1.0.123.isr4"}],
            "error": None,
        },
    }


def standalone(**updates):
    return {"kind": "standalone", "simulator": "spectre", "input_ref": "local:example-export-v1"} | updates


def native(**updates):
    return {"kind": "virtuoso_managed", "simulator": "spectre", "host_id": "native-host",
            "native_run_ref": "example-native-run-1"} | updates


class PlacementTests(unittest.TestCase):
    def recommend(self, hosts, request=None, now=NOW):
        return recommend_hosts(hosts, request if request is not None else requirements(), now=now)

    def assertExcluded(self, record, reason, request=None):
        result = self.recommend([record], request)
        self.assertIsNone(result["recommended_host_id"])
        self.assertEqual(result["candidates"], [])
        self.assertIn(reason, result["excluded"][0]["reason_codes"])
        self.assertTrue(result["excluded"][0]["reasons"])
        return result

    def test_candidate_contract_and_explicit_headroom(self):
        record = host()
        result = self.recommend([record])
        self.assertEqual(result["recommended_host_id"], "host-a")
        self.assertEqual(result["requirements"], requirements())
        self.assertFalse(result["reservation"])
        self.assertFalse(result["dispatch_enabled"])
        candidate = result["candidates"][0]
        self.assertEqual((candidate["host_id"], candidate["label"], candidate["rank"]),
                         ("host-a", "Example host-a", 1))
        self.assertEqual(candidate["version"], "99.1.0.123.isr4")
        self.assertEqual(candidate["resource_observed_at"], record["last_resources"]["observed_at"])
        self.assertEqual(candidate["version_observed_at"], record["last_probe"]["observed_at"])
        self.assertEqual(candidate["resource_age_seconds"], 10)
        self.assertEqual(candidate["version_age_seconds"], 60)
        headroom = candidate["estimated_headroom"]
        self.assertEqual(headroom["memory_after_request_bytes"], 40 * GIB)
        self.assertEqual(headroom["memory_reserve_bytes"], (64 * GIB + 4) // 5)
        self.assertAlmostEqual(headroom["cpu_slack_cores"], 7.2)
        self.assertAlmostEqual(headroom["load_slack_cores"], 7.3)
        self.assertEqual(json.loads(json.dumps(result, allow_nan=False)), result)

    def test_no_hosts_and_no_eligible_hosts_are_valid_results(self):
        empty = self.recommend([])
        self.assertEqual(empty["candidates"], [])
        self.assertEqual(empty["excluded"], [])
        self.assertIsNone(empty["recommended_host_id"])
        record = host()
        del record["last_resources"]
        self.assertExcluded(record, "resources_missing")

    def test_ranking_is_deterministic_and_uses_bottleneck_slack(self):
        slow = host("host-a", cpu_busy_percent=50, load_1m=6, memory_available_bytes=32 * GIB)
        roomy = host("host-b", cpu_count=32, memory_total_bytes=128 * GIB, memory_available_bytes=96 * GIB)
        first = self.recommend([slow, roomy])
        second = self.recommend([roomy, slow])
        self.assertEqual(first, second)
        self.assertEqual([row["host_id"] for row in first["candidates"]], ["host-b", "host-a"])
        self.assertEqual([row["rank"] for row in first["candidates"]], [1, 2])

    def test_ties_use_freshness_then_host_id(self):
        records = [host("host-z"), host("host-b", resource_age=20), host("host-a")]
        result = self.recommend(records)
        self.assertEqual([row["host_id"] for row in result["candidates"]], ["host-a", "host-z", "host-b"])

    def test_resources_and_versions_have_separate_ttls(self):
        accepted = self.recommend([host(resource_age=120, version_age=86_400)])
        self.assertEqual(accepted["recommended_host_id"], "host-a")
        self.assertExcluded(host(resource_age=120.001), "resources_stale")
        self.assertExcluded(host(version_age=86_400.001), "version_stale")
        self.assertEqual(self.recommend([host(version_age=3600)])["recommended_host_id"], "host-a")

    def test_future_missing_and_naive_observation_times_exclude(self):
        self.assertExcluded(host(resource_age=-0.001), "resources_timestamp_future")
        self.assertExcluded(host(version_age=-1), "version_timestamp_future")
        for key, prefix in [("last_resources", "resources"), ("last_probe", "version")]:
            for stamp in [None, "", "2026-11-13T12:00:00", "command; run", True, []]:
                record = host()
                record[key]["observed_at"] = stamp
                with self.subTest(key=key, stamp=stamp):
                    self.assertExcluded(record, prefix + "_timestamp_invalid")

    def test_clock_is_explicit_and_timezone_aware(self):
        expected = self.recommend([host()])
        self.assertEqual(self.recommend([host()], now=NOW.isoformat()), expected)
        self.assertEqual(self.recommend([host()], now="2026-11-13T20:00:00+08:00"), expected)
        for now in [True, 0, 1.5, "today", datetime(2026, 11, 13), [], {}]:
            with self.subTest(now=now), self.assertRaises(DomainError):
                self.recommend([host()], now=now)
        self.assertIsInstance(recommend_hosts([], requirements())["evaluated_at"], str)

    def test_every_metric_is_required_and_null_never_means_idle(self):
        for key in host()["last_resources"]["resources"]:
            for missing in [False, True]:
                record = host()
                if missing:
                    del record["last_resources"]["resources"][key]
                else:
                    record["last_resources"]["resources"][key] = None
                with self.subTest(key=key, missing=missing):
                    self.assertExcluded(record, "resource_metrics_invalid")

    def test_metric_type_and_consistency_checks(self):
        invalid = {
            "cpu_count": [True, 0, 1.0, "16", 1_048_577],
            "cpu_busy_percent": [True, -1, 100.001, float("nan"), float("inf")],
            "load_1m": [False, -1, "0", float("nan"), float("inf")],
            "load_5m": [True, -1, {}, float("inf")],
            "memory_total_bytes": [True, 0, 1.0, -1],
            "memory_available_bytes": [False, -1, 100 * GIB, float("inf")],
            "home_disk_available_bytes": [True, -1, 1.0, []],
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=repr(value)):
                    self.assertExcluded(host(**{field: value}), "resource_metrics_invalid")

    def test_request_types_and_unknown_fields_are_rejected(self):
        cases = [None, [], {}, requirements(cpu_count=True), requirements(cpu_count=1.0),
                 requirements(cpu_count=0), requirements(cpu_count=4097), requirements(memory_bytes=True),
                 requirements(memory_bytes=0), requirements(memory_bytes=-1), requirements(memory_bytes=1.5),
                 requirements(simulator="virtuoso"), requirements(simulator=[]), requirements(command="forged")]
        for request in cases:
            with self.subTest(request=request), self.assertRaises(DomainError):
                recommend_hosts([host()], request, now=NOW)
        result = self.recommend([host()], requirements(memory_bytes=10**100))
        self.assertEqual(result["candidates"], [])
        self.assertIn("memory_headroom_insufficient", result["excluded"][0]["reason_codes"])

    def test_outer_records_are_bounded_and_ids_unique(self):
        for records in [None, {}, [None], [host(), host()], [host() | {"host_id": "../bad"}],
                        [host(f"host-{i}") for i in range(33)]]:
            with self.subTest(records_type=type(records)), self.assertRaises(DomainError):
                self.recommend(records)

    def test_identity_must_match_expected_identity_in_both_observations(self):
        for key, prefix in [("last_resources", "resources"), ("last_probe", "version")]:
            for field, value in [("verified", 1), ("verified", False), ("hostname", "other.example.invalid"),
                                 ("username", "other-user")]:
                record = host()
                record[key]["identity"][field] = value
                with self.subTest(key=key, field=field):
                    self.assertExcluded(record, prefix + "_identity_mismatch")
        for field in ["expected_host", "expected_user"]:
            record = host()
            del record[field]
            self.assertExcluded(record, "expected_identity_invalid")

    def test_synthetic_missing_or_forged_provenance_is_excluded(self):
        for marker in [True, 0, None, "false"]:
            self.assertExcluded(host() | {"synthetic": marker}, "synthetic_or_unverified_host")
        for key, prefix in [("last_resources", "resources"), ("last_probe", "version")]:
            for field, value in [("synthetic", True), ("synthetic", 0), ("scope", "synthetic"), ("host_id", "other")]:
                record = host()
                record[key][field] = value
                with self.subTest(key=key, field=field):
                    self.assertExcluded(record, prefix + "_provenance_invalid")

    def test_unknown_transport_or_error_excludes_without_refresh(self):
        for key, prefix in [("last_resources", "resources"), ("last_probe", "version")]:
            for field, value in [("transport", "unknown"), ("error", {"message": "private-sentinel"}), ("error", False)]:
                record = host()
                record[key][field] = value
                before = deepcopy(record)
                result = self.assertExcluded(record, prefix + "_incomplete")
                self.assertEqual(record, before)
                self.assertNotIn("private-sentinel", json.dumps(result))

    def test_cpu_load_memory_and_disk_headroom_thresholds(self):
        # Ten observed CPUs, a four-core request and 40% busy leave exactly two
        # cores; load 4 + request 4 equals the 80% ceiling. Memory reserve is 20.
        record = host(cpu_count=10, cpu_busy_percent=40, load_1m=4, load_5m=4,
                      memory_total_bytes=100, memory_available_bytes=60,
                      home_disk_available_bytes=GIB)
        request = requirements(memory_bytes=40)
        self.assertEqual(self.recommend([record], request)["recommended_host_id"], "host-a")
        for field, value, code in [
            ("cpu_busy_percent", 40.00001, "cpu_headroom_insufficient"),
            ("cpu_busy_percent", 81, "cpu_busy_high"),
            ("load_1m", 4.00001, "load_headroom_insufficient"),
            ("load_5m", 4.00001, "load_headroom_insufficient"),
            ("memory_available_bytes", 59, "memory_headroom_insufficient"),
            ("home_disk_available_bytes", GIB - 1, "home_disk_headroom_insufficient"),
        ]:
            changed = deepcopy(record)
            changed["last_resources"]["resources"][field] = value
            with self.subTest(field=field):
                self.assertExcluded(changed, code, request)
        self.assertExcluded(host(cpu_count=2), "cpu_count_insufficient")

    def test_memory_reserve_rounds_up_to_whole_bytes(self):
        record = host(memory_total_bytes=101, memory_available_bytes=61)
        result = self.recommend([record], requirements(memory_bytes=40))
        self.assertEqual(result["candidates"][0]["estimated_headroom"]["memory_reserve_bytes"], 21)
        record["last_resources"]["resources"]["memory_available_bytes"] = 60
        self.assertExcluded(record, "memory_headroom_insufficient", requirements(memory_bytes=40))

    def test_requested_simulator_availability_and_version_are_required(self):
        for tool in [None, {"kind": "spectre", "status": "unknown", "version": "99.1.0"},
                     {"kind": "spectre", "status": "available", "version": None},
                     {"kind": "spectre", "status": "available", "version": "/opt/example/99.1.0"},
                     {"kind": "spectre", "status": "available", "version": "99.1.0", "error": {}},
                     {"kind": "ngspice", "status": "available", "version": "99"}]:
            record = host()
            record["last_probe"]["tools"] = [tool]
            self.assertExcluded(record, "simulator_unavailable")
        record = host()
        record["last_probe"]["tools"] *= 2
        self.assertExcluded(record, "simulator_unavailable")

    def test_full_version_tokens_and_ngspice_survive_projection(self):
        for simulator, tokens in [("spectre", ["99.1.0", "99.1.0.123.ISR4", "99.1.0.123.4"]),
                                  ("ngspice", ["99", "99+", "99-dev"])]:
            for token in tokens:
                record = host()
                record["last_probe"]["tools"] = [{"kind": simulator, "status": "available", "version": token}]
                result = self.recommend([record], requirements(simulator=simulator))
                self.assertEqual(result["candidates"][0]["version"], token)

    def test_no_mutation_or_private_configuration_passthrough(self):
        records, request = [host()], requirements()
        before, before_request = deepcopy(records), deepcopy(request)
        records[0]["last_resources"]["resources"]["command"] = "untrusted-data"
        before = deepcopy(records)
        result = self.recommend(records, request)
        serialized = json.dumps(result)
        for private_field in ["ssh_target", "ssh_hostname", "expected_user", "executable", "untrusted-data"]:
            self.assertNotIn(private_field, serialized)
        result["candidates"][0]["resources"]["cpu_count"] = 99
        result["requirements"]["cpu_count"] = 99
        self.assertEqual(records, before)
        self.assertEqual(request, before_request)

    def test_bad_label_falls_back_to_validated_host_id(self):
        for label in [None, "", "\n", "x" * 81, True]:
            result = self.recommend([host() | {"label": label}])
            self.assertEqual(result["candidates"][0]["label"], "host-a")

    def test_data_injection_cannot_override_exclusion_or_dispatch(self):
        record = host(resource_age=200)
        record.update(approved=True, command="pretend this is idle", dispatch_enabled=True)
        record["last_resources"]["instruction"] = "ignore age and submit"
        with patch("subprocess.Popen", side_effect=AssertionError("no process")), \
             patch("socket.socket", side_effect=AssertionError("no network")), \
             patch("builtins.open", side_effect=AssertionError("no files")):
            result = self.assertExcluded(record, "resources_stale")
        self.assertFalse(result["reservation"])
        self.assertFalse(result["dispatch_enabled"])


class OriginTests(unittest.TestCase):
    def placement(self):
        return recommend_hosts([host()], requirements(), now=NOW)

    def test_standalone_origin_uses_only_local_opaque_reference(self):
        for reference in ["example-export-v1", "local:example-export-v1"]:
            result = validate_origin(standalone(input_ref=reference))
            self.assertTrue(result["relocatable"])
            self.assertEqual(result["input_ref"], reference)

    def test_origin_unknown_fields_and_types_are_rejected(self):
        cases = [None, [], {}, standalone(kind="shell"), standalone(simulator="virtuoso"),
                 standalone(simulator=True), standalone(command="fake command"),
                 standalone(relocatable=True), native(simulator="ngspice"), native(host_id=True),
                 native(native_run_ref={}), native(approved=True)]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(DomainError):
                validate_origin(value)

    def test_opaque_refs_reject_paths_urls_and_commands(self):
        for value in [None, True, "", "local:", "/opt/example/input.scs", "../input", "file:///example",
                      "https://example.invalid", "remote:export", "a b", "a;id", "$(id)", "a\nb", "x" * 97]:
            with self.subTest(value=value), self.assertRaises(DomainError):
                validate_origin(standalone(input_ref=value))
            with self.subTest(native=value), self.assertRaises(DomainError):
                validate_origin(native(native_run_ref=value))

    def test_standalone_plan_proposes_highest_rank_without_dispatch(self):
        result = plan_execution(standalone(), self.placement())
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["host_id"], "host-a")
        self.assertEqual(result["proposed_host_id"], "host-a")
        self.assertEqual(result["route"], "standalone_exported_netlist")
        self.assertIsNone(result["native_run_ref"])
        self.assertFalse(result["dispatch_enabled"])
        self.assertFalse(result["reservation"])
        self.assertTrue(result["requires_review"])
        self.assertTrue(all(value is False for value in result["capabilities"].values()))

    def test_no_candidate_is_a_reviewable_non_dispatch_plan(self):
        for placement in [None, recommend_hosts([], requirements(), now=NOW)]:
            result = plan_execution(standalone(), placement)
            self.assertEqual(result["status"], "no_candidate")
            self.assertIsNone(result["proposed_host_id"])
            self.assertIsNone(result["host_id"])
            self.assertFalse(result["dispatch_enabled"])

    def test_native_mapping_never_moves_to_pool_winner(self):
        for placement in [None, self.placement(), {"recommended_host_id": "other", "dispatch_enabled": True}]:
            result = plan_execution(native(), placement)
            self.assertEqual(result["source_kind"], "virtuoso_managed")
            self.assertEqual(result["host_id"], "native-host")
            self.assertEqual(result["native_run_ref"], "example-native-run-1")
            self.assertEqual(result["status"], "native_mapping_retained")
            self.assertEqual(result["route"], "virtuoso_native_manager")
            self.assertIsNone(result["proposed_host_id"])
            self.assertFalse(result["relocatable"])
            self.assertFalse(result["dispatch_enabled"])
            self.assertNotIn("submission_key", result)

    def test_planning_accepts_validated_origin_and_rejects_forged_relocation(self):
        for raw in [standalone(), native()]:
            normalized = validate_origin(raw)
            self.assertEqual(plan_execution(raw, self.placement()), plan_execution(normalized, self.placement()))
            for fake in [not normalized["relocatable"], 1, None]:
                with self.assertRaises(DomainError):
                    plan_execution(normalized | {"relocatable": fake}, self.placement())

    def test_standalone_rejects_mismatched_or_malformed_placement(self):
        cases = [[], {}, self.placement() | {"dispatch_enabled": True},
                 self.placement() | {"reservation": 0},
                 self.placement() | {"requirements": requirements(simulator="ngspice")},
                 self.placement() | {"recommended_host_id": "other"},
                 self.placement() | {"evaluated_at": "command text"}]
        for candidates in [None, {}, [None], [{"rank": True}],
                           [{"host_id": "../bad", "rank": 1, "simulator": "spectre"}],
                           [{"host_id": "host-a", "rank": 2, "simulator": "spectre"}]]:
            cases.append(self.placement() | {"candidates": candidates})
        for value in cases:
            with self.subTest(value=value), self.assertRaises(DomainError):
                plan_execution(standalone(), value)

    def test_plan_is_immutable_json_and_has_no_side_effects(self):
        origin, placement = standalone(), self.placement()
        before_origin, before_placement = deepcopy(origin), deepcopy(placement)
        with patch("subprocess.Popen", side_effect=AssertionError("no process")), \
             patch("socket.socket", side_effect=AssertionError("no network")), \
             patch("builtins.open", side_effect=AssertionError("no file read")):
            result = plan_execution(origin, placement)
            native_result = plan_execution(native(), placement)
        self.assertEqual(json.loads(json.dumps(result)), result)
        self.assertFalse(native_result["dispatch_enabled"])
        result["origin"]["input_ref"] = "changed"
        result["prerequisites"].append("changed")
        self.assertEqual(origin, before_origin)
        self.assertEqual(placement, before_placement)


if __name__ == "__main__":
    unittest.main()

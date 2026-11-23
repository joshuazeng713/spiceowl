"""External resource selection using a mocked registry and fictional evidence."""

import copy
from datetime import timedelta
import hashlib
import json
import unittest
from unittest.mock import MagicMock, patch

from spiceowl.domain.common import DomainError
from spiceowl.hosts import HostRegistry, profile_from_record
from spiceowl.integrations.circuitgit_pool import CircuitGitPool, SCHEMA
from spiceowl.scheduling.placement import recommend_hosts
from test_placement import GIB, NOW, host, requirements


class CircuitGitPoolTests(unittest.TestCase):
    def setUp(self):
        self.clock = NOW
        self.records = {"host-a": host(), "host-b": host("host-b", cpu_count=32,
                        memory_total_bytes=128 * GIB, memory_available_bytes=96 * GIB)}
        self.failed = set()
        self.after_scan = None
        self.registry = MagicMock(spec=HostRegistry)
        self.registry.inspect.side_effect = self.inspect
        self.registry.scan.side_effect = self.scan
        self.registry.recommend.side_effect = self.recommend
        self.constructor = patch("spiceowl.integrations.circuitgit_pool.HostRegistry", return_value=self.registry)
        self.constructor.start(); self.addCleanup(self.constructor.stop)
        clock = patch("spiceowl.integrations.circuitgit_pool._now", side_effect=lambda: self.clock)
        clock.start(); self.addCleanup(clock.stop)
        # Any accidental dispatch or real filesystem/network implementation fails.
        remote = patch("spiceowl.adapters.hosts.probe_host", side_effect=AssertionError("Real probe forbidden"))
        remote.start(); self.addCleanup(remote.stop)
        resources = patch("spiceowl.adapters.hosts.probe_resources", side_effect=AssertionError("Real probe forbidden"))
        resources.start(); self.addCleanup(resources.stop)
        self.pool = CircuitGitPool("/fictional/private-state")

    def inspect(self, host_id):
        if host_id not in self.records:
            raise DomainError("PRIVATE sentinel must not leak", "not_found")
        return copy.deepcopy(self.records[host_id])

    def scan(self, ids):
        started = self.clock
        observed = started + timedelta(milliseconds=10)
        for host_id in ids:
            if host_id not in self.failed:
                for key in ("last_probe", "last_resources"):
                    self.records[host_id][key]["observed_at"] = observed.isoformat()
        self.clock = started + timedelta(milliseconds=20)
        if self.after_scan:
            self.after_scan(ids)
        return {"started_at": started.isoformat(), "completed_at": self.clock.isoformat(),
                "results": [{"host_id": key, "error": {"code": "busy"} if key in self.failed else None} for key in ids],
                "hosts": [], "concurrency": 2, "simulation_actions": False}

    def recommend(self, request, host_ids=None):
        self.assertIsNotNone(host_ids)
        return recommend_hosts([copy.deepcopy(self.records[key]) for key in host_ids], request, now=self.clock)

    def selection(self, ids=None):
        return self.pool.select(ids or ["host-a", "host-b"], requirements())

    def test_select_scans_only_explicit_hosts_and_returns_exact_private_contract(self):
        self.records["host-b"]["credential"] = "PRIVATE sentinel"
        result = self.selection()
        self.assertEqual(set(result), {"schema", "host_id", "requirements", "selected_at", "profile",
                                      "profile_sha256", "simulator_version", "recommendation"})
        self.assertEqual((result["schema"], result["host_id"]), (SCHEMA, "host-b"))
        self.registry.scan.assert_called_once_with(["host-a", "host-b"])
        self.registry.recommend.assert_called_once_with(requirements(), host_ids=["host-a", "host-b"])
        profile = profile_from_record(self.records["host-b"])
        encoded = json.dumps(profile, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        self.assertEqual(result["profile"], profile)
        self.assertEqual(result["profile_sha256"], hashlib.sha256(encoded).hexdigest())
        self.assertEqual(result["simulator_version"], "99.1.0.123.isr4")
        self.assertEqual(result["selected_at"], result["recommendation"]["evaluated_at"])
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertFalse(result["recommendation"]["reservation"])
        self.assertFalse(result["recommendation"]["dispatch_enabled"])
        self.assertEqual(json.loads(json.dumps(result, allow_nan=False)), result)
        result["profile"]["tools"][0]["executable"] = "/changed/spectre"
        self.assertNotEqual(result["profile"]["tools"], self.records["host-b"]["tools"])

    def test_v1_profile_and_non_ascii_label_have_stable_default_json_digest(self):
        record = self.records["host-a"]
        record["schema"] = "spiceowl.host.v1"; record.pop("ssh_hostname"); record["label"] = "模拟站点"
        result = self.selection(["host-a"])
        self.assertNotIn("ssh_hostname", result["profile"])
        data = json.dumps(result["profile"], sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        self.assertIn(b"\\u", data)
        self.assertEqual(result["profile_sha256"], hashlib.sha256(data).hexdigest())

    def test_all_inputs_checked_before_scan_and_no_registration_or_discovery(self):
        for ids in (None, (), [], ["host-a"] * 2, ["missing"], ["host-a", "missing"], [False], [{}],
                    ["bad/id"], ["host-a"] * 33):
            with self.subTest(ids=ids), self.assertRaises(DomainError) as raised:
                self.pool.select(ids, requirements())
            self.assertNotIn("PRIVATE", str(raised.exception))
        for request in (None, {}, requirements(cpu_count=True), requirements(memory_bytes=0),
                        requirements(simulator="ngspice"), requirements(extra=True)):
            with self.subTest(request=request), self.assertRaises(DomainError):
                self.pool.select(["host-a"], request)
        self.registry.scan.assert_not_called(); self.registry.recommend.assert_not_called()
        self.registry.list.assert_not_called(); self.registry.add.assert_not_called()

    def test_recheck_same_host_refreshes_evidence_after_approval_delay(self):
        selected = self.selection(["host-a"]); frozen = copy.deepcopy(selected)
        self.clock += timedelta(days=2)
        self.registry.reset_mock()
        latest = self.pool.recheck(selected)
        self.assertEqual(selected, frozen)
        self.registry.scan.assert_called_once_with(["host-a"])
        self.registry.recommend.assert_called_once_with(requirements(), host_ids=["host-a"])
        for field in ("host_id", "requirements", "profile", "profile_sha256", "simulator_version"):
            self.assertEqual(latest[field], frozen[field])
        self.assertNotEqual(latest["selected_at"], frozen["selected_at"])
        evidence = latest["recommendation"]["candidates"][0]
        self.assertEqual(evidence["resource_observed_at"], self.records["host-a"]["last_resources"]["observed_at"])
        self.assertEqual(evidence["resource_age_seconds"], .01)

    def test_profile_change_before_recheck_refuses_before_scan(self):
        selected = self.selection()
        self.registry.reset_mock()
        self.records[selected["host_id"]]["tools"][0]["executable"] = "/different/spectre"
        with self.assertRaises(DomainError) as raised:
            self.pool.recheck(selected)
        self.assertEqual(raised.exception.code, "resource_profile_changed")
        self.registry.scan.assert_not_called()

    def test_profile_change_during_scan_and_version_change_fail_without_retry(self):
        selected = self.selection(["host-a"])
        self.registry.reset_mock()
        self.after_scan = lambda ids: self.records[ids[0]].update(label="Changed")
        with self.assertRaises(DomainError):
            self.pool.recheck(selected)
        self.registry.scan.assert_called_once_with(["host-a"])
        self.records["host-a"]["label"] = selected["profile"]["label"]
        self.after_scan = lambda ids: self.records[ids[0]]["last_probe"]["tools"][0].update(version="99.1.0.124.isr4")
        self.registry.reset_mock()
        with self.assertRaises(DomainError) as raised:
            self.pool.recheck(selected)
        self.assertEqual(raised.exception.code, "resource_version_changed")
        self.registry.scan.assert_called_once_with(["host-a"])

    def test_failed_host_not_selected_using_its_old_fresh_observations(self):
        self.failed = {"host-b"}
        def private_failure(ids):
            result = self.scan(ids)
            for row in result["results"]:
                if row["error"] is not None:
                    row["error"] = {"code": "PRIVATE_error", "message": "/PRIVATE/ssh/config"}
            return result
        self.registry.scan.side_effect = private_failure
        selected = self.selection()
        self.assertEqual(selected["host_id"], "host-a")
        self.registry.recommend.assert_called_once_with(requirements(), host_ids=["host-a"])
        recommendation = selected["recommendation"]
        self.assertEqual([row["host_id"] for row in recommendation["candidates"]], ["host-a"])
        self.assertEqual(recommendation["excluded"], [{"host_id": "host-b", "label": self.records["host-b"]["label"],
            "resource_observed_at": None, "version_observed_at": None,
            "resource_age_seconds": None, "version_age_seconds": None,
            "reason_codes": ["scan_failed"],
            "reasons": ["This host did not complete the requested scan; previous observations were not used."]}])
        attempted = {row["host_id"] for key in ("candidates", "excluded") for row in recommendation[key]}
        self.assertEqual(attempted, {"host-a", "host-b"})
        self.assertEqual(len(selected), 8)
        self.assertNotIn("PRIVATE", json.dumps(selected))
        # The complete trace remains a valid frozen selection for same-host recheck.
        self.assertEqual(self.pool.recheck(selected)["host_id"], "host-a")
        self.registry.reset_mock(); self.failed = {"host-a", "host-b"}
        with self.assertRaises(DomainError):
            self.selection()
        self.registry.scan.assert_called_once(); self.registry.recommend.assert_not_called()

    def test_scanned_ineligible_host_is_retained_as_excluded_evidence(self):
        self.records["host-b"]["tools"] = []
        self.records["host-b"]["last_probe"]["tools"] = []
        selected = self.selection()
        self.assertEqual(selected["host_id"], "host-a")
        self.assertEqual(selected["recommendation"]["excluded"][0]["host_id"], "host-b")
        self.assertIn("simulator_unavailable", selected["recommendation"]["excluded"][0]["reason_codes"])
        self.pool.recheck(selected)

    def test_recommender_cannot_expand_the_scanned_host_set(self):
        def injected(request, host_ids=None):
            return recommend_hosts([self.records["host-a"], self.records["host-b"]], request, now=self.clock)
        self.registry.recommend.side_effect = injected
        with self.assertRaises(DomainError):
            self.selection(["host-a"])

    def test_failed_recheck_cannot_switch_to_better_host(self):
        selected = self.selection(["host-a"]); self.registry.reset_mock()
        self.failed = {"host-a"}
        with self.assertRaises(DomainError):
            self.pool.recheck(selected)
        self.registry.scan.assert_called_once_with(["host-a"])
        self.registry.recommend.assert_not_called()

    def test_stale_unknown_or_busy_evidence_has_no_selection(self):
        for change in (
            lambda row: row["last_resources"].update(observed_at=(self.clock-timedelta(seconds=121)).isoformat()),
            lambda row: row["last_probe"].update(observed_at=(self.clock-timedelta(days=2)).isoformat()),
            lambda row: row["last_resources"].update(transport="unknown"),
            lambda row: row["last_resources"]["identity"].update(verified=False),
            lambda row: row["last_resources"]["resources"].update(cpu_busy_percent=99),
            lambda row: row["last_probe"]["tools"][0].update(version=None),
        ):
            self.records["host-a"] = host()
            self.after_scan = lambda ids: change(self.records[ids[0]])
            with self.assertRaises(DomainError):
                self.selection(["host-a"])

    def test_scan_claiming_success_cannot_reuse_pre_scan_observation(self):
        self.after_scan = lambda ids: self.records[ids[0]]["last_resources"].update(observed_at=(self.clock-timedelta(seconds=2)).isoformat())
        with self.assertRaises(DomainError):
            self.selection(["host-a"])

    def test_tampered_frozen_selection_rejected_before_registry_access(self):
        good = self.selection(["host-a"])
        mutations = [lambda x: x.update(schema="unknown"), lambda x: x.update(extra=True),
                     lambda x: x.update(host_id="host-b"), lambda x: x.update(profile_sha256="0"*64),
                     lambda x: x.update(simulator_version="unknown"), lambda x: x.update(selected_at="2026-11-13"),
                     lambda x: x["profile"].update(password="PRIVATE"),
                     lambda x: x["requirements"].update(cpu_count=True),
                     lambda x: x["recommendation"].update(reservation=True),
                     lambda x: x["recommendation"].update(dispatch_enabled=True),
                     lambda x: x["recommendation"]["candidates"][0].update(rank=True),
                     lambda x: x["recommendation"]["candidates"][0].update(resource_age_seconds=-1),
                     lambda x: x["recommendation"]["candidates"][0]["resources"].update(memory_available_bytes=float("nan")),
                     lambda x: x["recommendation"]["candidates"][0].update(secret="PRIVATE")]
        for mutate in mutations:
            value = copy.deepcopy(good); mutate(value); self.registry.reset_mock()
            with self.assertRaises(DomainError) as raised:
                self.pool.recheck(value)
            self.assertNotIn("PRIVATE", str(raised.exception))
            self.registry.inspect.assert_not_called(); self.registry.scan.assert_not_called()

    def test_unavailable_registry_and_invalid_scan_use_safe_errors(self):
        self.registry.scan.side_effect = OSError("PRIVATE ssh path")
        with self.assertRaises(DomainError) as raised:
            self.selection()
        self.assertNotIn("PRIVATE", str(raised.exception))
        self.registry.scan.side_effect = None
        for result in ({}, {"simulation_actions": True}, {"simulation_actions": False,
            "started_at": NOW.isoformat(), "completed_at": NOW.isoformat(),
            "results": [{"host_id": {}, "error": None}, {"host_id": "host-b", "error": None}]}):
            self.registry.scan.return_value = result
            with self.assertRaises(DomainError):
                self.selection()


if __name__ == "__main__":
    unittest.main()

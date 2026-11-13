import copy
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from spiceowl.domain.common import DomainError, utcnow
from spiceowl.service import Controller


PROFILE = {"schema": "spiceowl.host.v1", "host_id": "route-template", "label": "Fixture route", "ssh_target": "route-alias",
           "expected_host": "route-alias", "expected_user": "researcher", "tools": [{"kind": "spectre", "executable": "/opt/example/spectre"}]}


def probe(profile):
    return {"host_id": profile["host_id"], "scope": "connectivity_only", "synthetic": False,
            "observed_at": utcnow(), "transport": "reachable", "identity": {"verified": True, "hostname": profile["expected_host"], "username": "researcher"},
            "tools": [{"kind": "spectre", "status": "available", "version": "21.1.0.509.isr12"}], "error": None}


def resource(profile):
    return probe(profile) | {"scope": "host_resources", "resources": {"cpu_count": 32, "cpu_busy_percent": 10.0, "load_1m": 1.0, "load_5m": 2.0,
                            "memory_total_bytes": 128 * 2**30, "memory_available_bytes": 100 * 2**30, "home_disk_available_bytes": 500 * 2**30}}


class PoolRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Controller(self.temp.name)
        self.app.seed_demo()
        self.app.hosts.add(copy.deepcopy(PROFILE))

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def test_import_is_explicit_atomic_and_idempotent_without_network(self):
        with patch("spiceowl.adapters.hosts.probe_host") as remote:
            first = self.app.hosts.import_pool("route-template", ["route-alias", "node-a", "node-b"])
            second = self.app.hosts.import_pool("route-template", ["route-alias", "node-a", "node-b"])
            self.assertEqual(len(self.app.hosts.list()), 3)
            self.assertEqual(sum(h["created"] for h in first["hosts"]), 2)
            self.assertFalse(any(h["created"] for h in second["hosts"]))
            remote.assert_not_called()
            with self.assertRaises(DomainError): self.app.hosts.import_pool("route-template", ["new-node", "bad;command"])
            self.assertEqual(len(self.app.hosts.list()), 3)

    def test_pool_scan_is_bounded_and_does_not_change_runs(self):
        self.app.hosts.import_pool("route-template", ["node-a", "node-b", "node-c"])
        before = self.app.state()["runs"]
        active = 0; peak = 0; lock = threading.Lock()
        def fake_probe(profile):
            nonlocal active, peak
            with lock: active += 1; peak = max(peak, active)
            try:
                time.sleep(.03)
                return probe(profile)
            finally:
                with lock: active -= 1
        with patch("spiceowl.adapters.hosts.probe_host", side_effect=fake_probe), patch("spiceowl.adapters.hosts.probe_resources", side_effect=resource):
            result = self.app.hosts.scan()
        self.assertEqual(len(result["results"]), 4)
        self.assertLessEqual(peak, 2)
        self.assertEqual(before, self.app.state()["runs"])
        recommendation = self.app.hosts.recommend({"cpu_count": 4, "memory_bytes": 8*2**30, "simulator": "spectre"})
        self.assertEqual(len(recommendation["candidates"]), 4)
        self.assertFalse(recommendation["dispatch_enabled"])

    def test_failed_identity_stops_resource_query_and_replaces_old_resource_freshness(self):
        with patch("spiceowl.adapters.hosts.probe_resources", side_effect=resource): self.app.hosts.resources("route-template")
        failed = probe(PROFILE) | {"transport": "unknown", "identity": {"verified": False}, "error": {"code": "ssh_failed"}}
        with patch("spiceowl.adapters.hosts.probe_host", return_value=failed), patch("spiceowl.adapters.hosts.probe_resources") as metrics:
            self.app.hosts.scan()
            metrics.assert_not_called()
        current = self.app.hosts.inspect("route-template")["last_resources"]
        self.assertEqual(current["transport"], "unknown")
        recommendation = self.app.hosts.recommend({"cpu_count": 4, "memory_bytes": 8*2**30, "simulator": "spectre"})
        self.assertEqual(recommendation["candidates"], [])

    def test_no_host_selection_is_silently_expanded_or_networked(self):
        with patch("spiceowl.adapters.hosts.probe_host") as remote:
            for ids in ([], ["missing"], ["route-template", "route-template"], [{}]):
                with self.assertRaises(DomainError): self.app.hosts.scan(ids)
            remote.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""Local onboarding contract tests; no SSH or simulator is contacted."""

from concurrent.futures import ThreadPoolExecutor
import copy
import http.client
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

from spiceowl.domain.common import utcnow
from spiceowl.domain.connectivity import summary_error
from spiceowl.service import Controller
from spiceowl.web.server import make_server


PROFILE = {
    "schema": "spiceowl.host.v1", "host_id": "fixture-route", "label": "Fixture route",
    "ssh_target": "private-route", "expected_host": "private-node",
    "expected_user": "private-user",
    "tools": [{"kind": "spectre", "executable": "/private/example/spectre"}],
}


class HostOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Controller(self.temp.name)
        self.server = make_server(self.app)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        _, state = self.request("GET", "/api/state")
        self.token = state["csrf_token"]

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.app.close()
        self.temp.cleanup()

    def request(self, method, path, body=None, *, authenticated=True, origin=None):
        client = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if authenticated and method != "GET":
            headers["X-SpiceOwl-Token"] = self.token
        if origin is not None:
            headers["Origin"] = origin
        encoded = json.dumps(body) if isinstance(body, (dict, list)) else body
        try:
            client.request(method, path, body=encoded, headers=headers)
            response = client.getresponse()
            return response.status, json.loads(response.read())
        finally:
            client.close()

    def test_registration_retry_preserves_observations_and_private_settings(self):
        with patch("subprocess.Popen", side_effect=AssertionError("Registration must not run SSH")):
            status, public = self.request("POST", "/api/hosts", PROFILE)
            self.assertEqual(status, 201)
            self.assertEqual(public["host_id"], PROFILE["host_id"])
            self.assertIsNone(public["last_probe"])
            before = self.app.hosts.inspect(PROFILE["host_id"])
            observation = {"observed_at": utcnow(), "transport": "unknown", "identity": {"verified": False},
                           "tools": [], "error": {"code": "ssh_permission_denied", "message": "PRIVATE_RAW_ERROR"}}
            self.app.store.update("host", PROFILE["host_id"], lambda h: h | {"last_probe": observation})
            expected = self.app.hosts.inspect(PROFILE["host_id"])
            status, retry = self.request("POST", "/api/hosts", copy.deepcopy(PROFILE))
            self.assertEqual(status, 200)
            self.assertEqual(self.app.hosts.inspect(PROFILE["host_id"]), expected)
            self.assertEqual(expected["created_at"], before["created_at"])
            status, listing = self.request("GET", "/api/hosts")
            self.assertEqual(status, 200)
            self.assertEqual(listing["hosts"], [retry])
            for private in ("ssh_target", "expected_user", "expected_host", "executable", "private-route",
                            "private-node", "private-user", "/private/example/spectre", "PRIVATE_RAW_ERROR"):
                self.assertNotIn(private, json.dumps(listing))
            changed = PROFILE | {"ssh_target": "different-route"}
            self.assertEqual(self.request("POST", "/api/hosts", changed)[0], 409)
            self.assertEqual(self.app.hosts.inspect(PROFILE["host_id"]), expected)

    def test_concurrent_identical_registration_has_one_creator(self):
        with patch("subprocess.Popen", side_effect=AssertionError("Unexpected process")):
            with ThreadPoolExecutor(max_workers=2) as workers:
                results = list(workers.map(lambda _: self.request("POST", "/api/hosts", PROFILE), range(2)))
        self.assertEqual(sorted(status for status, _ in results), [200, 201])
        self.assertEqual(len(self.app.hosts.list()), 1)

    def test_v2_registration_is_strict_and_does_not_accept_credentials_or_commands(self):
        profile = PROFILE | {"schema": "spiceowl.host.v2", "ssh_hostname": "node-a.example.invalid"}
        bad_profiles = [profile | {"password": "PRIVATE"}, profile | {"ssh_hostname": "node; command"},
                        profile | {"ssh_target": "user@host"}, profile | {"expected_user": None},
                        PROFILE | {"ssh_hostname": "extra-v1-field"}, profile | {"tools": [{"kind": "shell", "executable": "/bin/sh"}]}]
        with patch("subprocess.Popen", side_effect=AssertionError("Unexpected process")):
            for candidate in bad_profiles:
                with self.subTest(candidate=candidate):
                    self.assertEqual(self.request("POST", "/api/hosts", candidate)[0], 400)
            self.assertEqual(self.app.hosts.list(), [])
            self.assertEqual(self.request("POST", "/api/hosts", profile)[0], 201)
            self.assertEqual(self.app.hosts.inspect(PROFILE["host_id"])["ssh_hostname"], "node-a.example.invalid")

    def test_registration_and_pool_import_require_origin_token_and_bounded_json(self):
        pool = {"template_id": PROFILE["host_id"], "names": ["node-a"]}
        with patch.object(self.app.hosts, "register") as register, patch.object(self.app.hosts, "import_pool") as expand:
            for path, body in (("/api/hosts", PROFILE), ("/api/hosts/import-pool", pool)):
                self.assertEqual(self.request("POST", path, body, authenticated=False)[0], 403)
                self.assertEqual(self.request("POST", path, body, origin="https://evil.example")[0], 403)
                self.assertEqual(self.request("POST", path, " " * 17000)[0], 413)
                self.assertEqual(self.request("POST", path, '{"schema":1,"schema":2}')[0], 400)
                self.assertEqual(self.request("POST", path, [])[0], 400)
            register.assert_not_called()
            expand.assert_not_called()

    def test_pool_import_is_explicit_idempotent_and_atomic_on_conflict(self):
        with patch("subprocess.Popen", side_effect=AssertionError("Unexpected process")):
            self.request("POST", "/api/hosts", PROFILE)
            pool = {"template_id": PROFILE["host_id"], "names": ["node-a", "node-b"]}
            status, result = self.request("POST", "/api/hosts/import-pool", pool)
            self.assertEqual(status, 200)
            self.assertEqual(result["network_operations"], 0)
            self.assertTrue(all(h["created"] for h in result["hosts"]))
            self.assertFalse(any(h["created"] for h in self.request("POST", "/api/hosts/import-pool", pool)[1]["hosts"]))
            before = self.app.hosts.list()
            for bad in (pool | {"command": "echo"}, pool | {"names": []}, pool | {"names": ["new-node", "bad;command"]},
                        pool | {"names": ["node-a", "node-a"]}, pool | {"names": "node-a"},
                        pool | {"names": ["node-" + str(i) for i in range(33)]}):
                self.assertEqual(self.request("POST", "/api/hosts/import-pool", bad)[0], 400)
            self.assertEqual(self.app.hosts.list(), before)
            conflicting = PROFILE | {"host_id": "node-c", "ssh_target": "different-route"}
            self.request("POST", "/api/hosts", conflicting)
            before_conflict = self.app.hosts.list()
            self.assertEqual(self.request("POST", "/api/hosts/import-pool",
                                          pool | {"names": ["new-node", "node-c"]})[0], 409)
            self.assertEqual(self.app.hosts.list(), before_conflict)
            self.assertEqual(len(before), 3)

    def test_failed_pool_check_keeps_actionable_safe_error_without_fake_metrics(self):
        self.app.hosts.add(PROFILE)
        failed = {"host_id": PROFILE["host_id"], "synthetic": False, "scope": "connectivity_only",
                  "observed_at": utcnow(), "transport": "unknown", "identity": {"verified": False}, "tools": [],
                  "error": {"code": "ssh_host_key_unverified", "message": "PRIVATE_RAW_ERROR"}}
        with patch("spiceowl.adapters.hosts.probe_host", return_value=failed), patch("spiceowl.adapters.hosts.probe_resources") as resources:
            status, result = self.request("POST", "/api/hosts/scan", {})
            resources.assert_not_called()
        self.assertEqual(status, 200)
        self.assertEqual(result["results"][0]["error"]["code"], "ssh_host_key_unverified")
        host = result["hosts"][0]
        self.assertEqual(host["last_resources"]["error"]["code"], "ssh_host_key_unverified")
        self.assertFalse(host["last_resources"]["identity"]["verified"])
        self.assertTrue(all(v is None for v in host["last_resources"]["resources"].values()))
        self.assertNotIn("PRIVATE_RAW_ERROR", json.dumps(result))

    def test_all_new_diagnostic_codes_have_safe_guidance(self):
        for code in ("ssh_name_resolution_failed", "ssh_network_unreachable", "ssh_connection_refused",
                     "ssh_connection_lost", "ssh_config_invalid"):
            result = summary_error({"code": code, "message": "PRIVATE_RAW_ERROR"})
            self.assertEqual(result["code"], code)
            self.assertNotIn("PRIVATE_RAW_ERROR", result["message"])
        self.assertEqual(summary_error({"code": "PRIVATE_RAW_ERROR", "message": "PRIVATE_RAW_ERROR"})["code"], "probe_incomplete")

    def test_selected_scan_works_with_more_than_32_registered_hosts(self):
        self.app.hosts.add(PROFILE)
        self.app.hosts.import_pool(PROFILE["host_id"], ["node-" + str(i) for i in range(32)])
        self.assertEqual(len(self.app.hosts.list()), 33)
        selected = ["node-1", "node-31"]
        def failed(profile):
            return {"host_id": profile["host_id"], "synthetic": False, "scope": "connectivity_only",
                    "observed_at": utcnow(), "transport": "unknown", "identity": {"verified": False}, "tools": [],
                    "error": {"code": "ssh_host_key_unverified", "message": "Fixture trust not verified."}}
        with patch("spiceowl.adapters.hosts.probe_host", side_effect=failed) as probe, patch("spiceowl.adapters.hosts.probe_resources") as resources:
            self.assertEqual(self.request("POST", "/api/hosts/scan", {})[0], 400)
            probe.assert_not_called()
            status, result = self.request("POST", "/api/hosts/scan", {"host_ids": selected})
            resources.assert_not_called()
        self.assertEqual(status, 200)
        self.assertEqual({call.args[0]["host_id"] for call in probe.call_args_list}, set(selected))
        self.assertEqual([item["host_id"] for item in result["results"]], selected)
        self.assertEqual(len(result["hosts"]), 33)
        for host in self.app.hosts.list():
            if host["host_id"] not in selected:
                self.assertIsNone(host["last_probe"])
                self.assertIsNone(host["last_resources"])

    def test_invalid_scan_selection_never_starts_a_partial_batch(self):
        self.app.hosts.add(PROFILE)
        before = self.app.hosts.list()
        bad_requests = [({"host_ids": []}, 400), ({"host_ids": None}, 400),
                        ({"host_ids": PROFILE["host_id"]}, 400), ({"host_ids": [PROFILE["host_id"]] * 2}, 400),
                        ({"host_ids": [PROFILE["host_id"], "missing"]}, 404), ({"host_ids": [{}]}, 400),
                        ({"host_ids": ["node-" + str(i) for i in range(33)]}, 400),
                        ({"host_ids": [PROFILE["host_id"]], "command": "ignored?"}, 400),
                        ({"hosts": [PROFILE["host_id"]]}, 400)]
        with patch("spiceowl.adapters.hosts.probe_host") as probe, patch("spiceowl.adapters.hosts.probe_resources") as resources:
            for body, expected in bad_requests:
                with self.subTest(body=body):
                    self.assertEqual(self.request("POST", "/api/hosts/scan", body)[0], expected)
                    self.assertEqual(self.app.hosts.list(), before)
            probe.assert_not_called()
            resources.assert_not_called()

    def test_selected_recommendation_and_plans_with_33_eligible_hosts(self):
        self.app.hosts.add(PROFILE)
        self.app.hosts.import_pool(PROFILE["host_id"], ["node-" + str(i) for i in range(32)])
        stamp = utcnow()
        for host in self.app.store.list("host"):
            identity = {"verified": True, "hostname": host["expected_host"], "username": host["expected_user"]}
            probe = {"host_id": host["host_id"], "scope": "connectivity_only", "synthetic": False,
                     "observed_at": stamp, "transport": "reachable", "identity": identity, "error": None,
                     "tools": [{"kind": "spectre", "status": "available", "version": "21.1.0.509.isr12"}]}
            resources = {"host_id": host["host_id"], "scope": "host_resources", "synthetic": False,
                         "observed_at": stamp, "transport": "reachable", "identity": identity, "error": None,
                         "resources": {"cpu_count": 32, "cpu_busy_percent": 10.0, "load_1m": 1.0, "load_5m": 2.0,
                                       "memory_total_bytes": 128 * 2**30, "memory_available_bytes": 100 * 2**30,
                                       "home_disk_available_bytes": 500 * 2**30}}
            self.app.store.update("host", host["host_id"], lambda current: current | {"last_probe": probe, "last_resources": resources})
        before = self.app.store.list("host")
        requirements = {"cpu_count": 4, "memory_bytes": 8 * 2**30, "simulator": "spectre"}
        selected = ["node-1", "node-31"]
        origin = {"kind": "standalone", "simulator": "spectre", "input_ref": "fixture-input"}
        with patch("subprocess.Popen", side_effect=AssertionError("Planning must not run SSH")):
            self.assertEqual(self.request("POST", "/api/placement/recommend", requirements)[0], 400)
            status, placement = self.request("POST", "/api/placement/recommend", requirements | {"host_ids": selected})
            self.assertEqual(status, 200)
            self.assertEqual({h["host_id"] for h in placement["candidates"]}, set(selected))
            self.assertEqual(placement["excluded"], [])
            status, standalone = self.request("POST", "/api/execution/plan", {
                "origin": origin, "requirements": requirements, "host_ids": selected})
            self.assertEqual(status, 200)
            self.assertIn(standalone["proposed_host_id"], selected)
            self.assertFalse(standalone["dispatch_enabled"])
            native_origin = {"kind": "virtuoso_managed", "simulator": "spectre", "host_id": PROFILE["host_id"],
                             "native_run_ref": "fixture-native-run"}
            status, native = self.request("POST", "/api/execution/plan", {
                "origin": native_origin, "requirements": requirements, "host_ids": selected})
            self.assertEqual(status, 200)
            self.assertEqual(native["status"], "native_mapping_retained")
            self.assertEqual(native["host_id"], PROFILE["host_id"])
            self.assertIsNone(native["proposed_host_id"])
            self.assertEqual(native["native_run_ref"], "fixture-native-run")
            self.assertFalse(native["dispatch_enabled"])
        self.assertEqual(self.app.store.list("host"), before)

    def test_recommendation_and_plan_selections_and_requirements_remain_strict(self):
        self.app.hosts.add(PROFILE)
        requirements = {"cpu_count": 4, "memory_bytes": 8 * 2**30, "simulator": "spectre"}
        origin = {"kind": "standalone", "simulator": "spectre", "input_ref": "fixture-input"}
        before = self.app.store.list("host")
        with patch("subprocess.Popen", side_effect=AssertionError("Planning must not run SSH")):
            for ids, expected in ((None, 400), ([], 400), ("node-a", 400), ([{}], 400),
                                  ([PROFILE["host_id"]] * 2, 400), ([PROFILE["host_id"], "missing"], 404),
                                  (["node-" + str(i) for i in range(33)], 400)):
                with self.subTest(ids=ids):
                    self.assertEqual(self.request("POST", "/api/placement/recommend", requirements | {"host_ids": ids})[0], expected)
                    self.assertEqual(self.request("POST", "/api/execution/plan", {
                        "origin": origin, "requirements": requirements, "host_ids": ids})[0], expected)
            for bad in (requirements | {"extra": 1}, requirements | {"cpu_count": True},
                        requirements | {"memory_bytes": 0}, requirements | {"simulator": "shell"}):
                self.assertEqual(self.request("POST", "/api/placement/recommend", bad | {"host_ids": [PROFILE["host_id"]]})[0], 400)
                self.assertEqual(self.request("POST", "/api/execution/plan", {
                    "origin": origin, "requirements": bad, "host_ids": [PROFILE["host_id"]]})[0], 400)
            self.assertEqual(self.request("POST", "/api/execution/plan", {
                "origin": origin, "requirements": requirements, "host_ids": [PROFILE["host_id"]], "extra": 1})[0], 400)
        self.assertEqual(self.app.store.list("host"), before)


if __name__ == "__main__":
    unittest.main()

import copy
import json
from pathlib import Path
import tempfile
import threading
import subprocess
import sys
import unittest
from unittest.mock import patch

from spiceowl.domain.common import DomainError, utcnow
from spiceowl.hosts import HostRegistry, load_profile
from spiceowl.service import Controller


def profile():
    return {"schema": "spiceowl.host.v1", "host_id": "test-host", "label": "Synthetic test configuration",
            "ssh_target": "example-sim", "expected_host": "example-sim", "expected_user": "researcher",
            "tools": [{"kind": "spectre", "executable": "/opt/example/spectre/bin/spectre"}]}


def observation():
    return {"host_id": "test-host", "synthetic": False, "scope": "connectivity_only", "observed_at": utcnow(),
            "transport": "unknown", "identity": {"verified": False, "hostname": None, "username": None},
            "tools": [{"kind": "spectre", "status": "unknown", "version": None}],
            "error": {"code": "ssh_timeout", "message": "SSH query timed out."}}


class HostRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Controller(self.temp.name)
        self.app.seed_demo()
        self.registry = HostRegistry(self.temp.name)

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def test_import_list_and_inspect_never_connect(self):
        with patch("spiceowl.adapters.hosts.probe_host", side_effect=AssertionError("Unexpected SSH")):
            self.registry.add(profile())
            self.assertIsNone(self.registry.inspect("test-host")["last_probe"])
            summary = self.registry.list()[0]
            self.assertEqual(summary, self.app.state()["hosts"][0])
            self.assertNotIn("ssh_target", summary)
            self.assertNotIn("executable", json.dumps(summary))
            with self.assertRaises(DomainError):
                self.registry.add(profile())

    def test_failed_probe_preserves_runs_and_controller_turns(self):
        from spiceowl.domain.common import digest
        runs = copy.deepcopy(self.app.state()["runs"])
        turn, _ = self.app.store.claim_turn("probe-test", digest("probe-test"), "demo-agent", "demo-disconnect", "Unfinished fixture", {})
        self.registry.add(profile())
        obs = observation()
        with patch("spiceowl.adapters.hosts.probe_host", return_value=obs) as query:
            self.assertEqual(self.registry.probe("test-host"), obs)
            query.assert_called_once_with(profile())
        self.assertEqual(self.app.state()["runs"], runs)
        self.assertEqual(self.app.store.get_turn(turn["turn_id"])["status"], "dispatching")
        self.assertEqual(self.registry.inspect("test-host")["last_probe"], obs)

    def test_parallel_registry_instances_cannot_probe_same_host(self):
        self.registry.add(profile())
        started, release = threading.Event(), threading.Event()
        def query(_):
            started.set()
            release.wait(3)
            return observation()
        with patch("spiceowl.adapters.hosts.probe_host", side_effect=query) as remote:
            worker = threading.Thread(target=self.registry.probe, args=("test-host",))
            worker.start()
            try:
                self.assertTrue(started.wait(2))
                with self.assertRaises(DomainError) as error:
                    HostRegistry(self.temp.name).probe("test-host")
                self.assertEqual(error.exception.code, "busy")
            finally:
                release.set()
                worker.join(3)
            self.assertEqual(remote.call_count, 1)

    def test_probe_lock_released_after_exception(self):
        self.registry.add(profile())
        with patch("spiceowl.adapters.hosts.probe_host", side_effect=OSError):
            with self.assertRaises(OSError): self.registry.probe("test-host")
        with patch("spiceowl.adapters.hosts.probe_host", return_value=observation()):
            self.registry.probe("test-host")

    def test_probe_respects_another_process_lock(self):
        self.registry.add(profile())
        lock_path = str(Path(self.temp.name) / ".host-probe-test-host.lock")
        code = "import fcntl,sys; f=open(sys.argv[1],'a+'); fcntl.flock(f,fcntl.LOCK_EX); print('locked',flush=True); sys.stdin.read(1)"
        process = subprocess.Popen([sys.executable, "-c", code, lock_path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "locked")
            with patch("spiceowl.adapters.hosts.probe_host") as probe:
                with self.assertRaises(DomainError) as error: self.registry.probe("test-host")
                self.assertEqual(error.exception.code, "busy")
                probe.assert_not_called()
        finally:
            process.communicate("x", timeout=3)

    def test_summary_omits_private_nested_config(self):
        self.registry.add(profile())
        obs = observation() | {"private_config": "PRIVATE_SENTINEL"}
        obs["identity"]["username"] = "PRIVATE_SENTINEL"
        obs["error"]["message"] = "PRIVATE_SENTINEL"
        obs["tools"][0]["executable"] = "PRIVATE_SENTINEL"
        self.registry.store.update("host", "test-host", lambda host: host | {"last_probe": obs})
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(self.registry.list()))

    def test_bad_import_files_and_unsafe_id(self):
        path = Path(self.temp.name) / "profile.json"
        for content in ('{"label":1,"label":2}', '{"value":NaN}', "[" * 2000, " " * 16385):
            path.write_text(content)
            with self.assertRaises(DomainError): load_profile(path)
        with self.assertRaises(DomainError): self.registry.probe("../../outside")


if __name__ == "__main__":
    unittest.main()

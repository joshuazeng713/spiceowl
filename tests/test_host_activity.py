"""Tests for live server resource and active simulation inspection (`host_activity`)."""

from __future__ import annotations

import json
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from spiceowl.host_activity import (
    classify_eda_process,
    inspect_pool_live_status,
    parse_eda_processes,
)
from spiceowl.service import Controller
from spiceowl.web.server import make_server


def _fake_live_stdout(script_bytes: bytes, *, host: str = "sim-node-01", user: str = "researcher", ps_lines: list[str] | None = None) -> str:
    text = script_bytes.decode("utf-8")
    m = re.search(r"__SPICEOWL_([0-9a-f]+)_ID_BEGIN__", text)
    assert m is not None
    nonce = m.group(1)
    procs = "\n".join(ps_lines if ps_lines is not None else [
        "11846 colleague 608.0 0.4 591932 03:35:50 spectre",
        "194432 researcher 2645.0 1.3 1710464 23:54:29 spectre",
        "194261 researcher 0.1 0.0 32840 23:54:29 spectreMonitor",
        "193141 colleague 13.2 9.1 11968160 06:10:50 virtuoso",
        "107025 colleague 3.7 0.1 216860 12:52 calibrerve",
        "1 root 0.3 0.0 4608 1-09:19:54 systemd",
    ])
    return (
        f"__SPICEOWL_{nonce}_ID_BEGIN__\n"
        f"hostname {host}\n"
        f"username {user}\n"
        f"__SPICEOWL_{nonce}_ID_END__\n"
        f"__SPICEOWL_{nonce}_RES_BEGIN__\n"
        "cpu_first 100 10 20 800 30 5 5 10 7 2\n"
        "cpu_second 160 10 30 820 40 5 5 10 15 2\n"
        "cpu_count_first 32\n"
        "cpu_count_second 32\n"
        "uptime_first 1000.00\n"
        "uptime_second 1000.20\n"
        "load_1m 18.50\n"
        "load_5m 14.20\n"
        "MemTotal: 134217728 kB\n"
        "MemAvailable: 94371840 kB\n"
        "home_disk_available_bytes 1099511627776\n"
        f"__SPICEOWL_{nonce}_RES_END__\n"
        f"__SPICEOWL_{nonce}_SIMS_BEGIN__\n"
        f"{procs}\n"
        f"__SPICEOWL_{nonce}_SIMS_END__\n"
    )


class TestHostActivity(unittest.TestCase):
    def test_classify_and_parse_eda_processes(self) -> None:
        self.assertEqual(classify_eda_process("spectre"), "simulation")
        self.assertEqual(classify_eda_process("ngspice"), "simulation")
        self.assertEqual(classify_eda_process("hspice64"), "simulation")
        self.assertEqual(classify_eda_process("virtuoso"), "eda_tool")
        self.assertEqual(classify_eda_process("calibrerve"), "eda_tool")
        self.assertIsNone(classify_eda_process("spectreMonitor"))
        self.assertIsNone(classify_eda_process("systemd"))
        self.assertIsNone(classify_eda_process("python3"))

        parsed = parse_eda_processes([
            "11846 colleague 608.0 0.4 591932 03:35:50 spectre",
            "194432 researcher 2645.0 1.3 1710464 23:54:29 spectre",
            "194261 researcher 0.1 0.0 32840 23:54:29 spectreMonitor",
            "193141 colleague 13.2 9.1 11968160 06:10:50 virtuoso",
        ], expected_user="researcher")
        self.assertEqual(parsed["active_count"], 2)
        self.assertEqual(parsed["my_active_count"], 1)
        self.assertEqual(parsed["eda_tool_count"], 1)
        self.assertEqual(parsed["total_sim_cpu_percent"], 3253.0)
        self.assertEqual(parsed["processes"][0]["pid"], 194432)
        self.assertTrue(parsed["processes"][0]["is_me"])
        self.assertEqual(parsed["by_tool"], {"spectre": 2, "virtuoso": 1})

    def test_pool_live_status_persists_resources_and_http_route(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spiceowl-live-act-") as tmp:
            data_dir = Path(tmp) / "data"
            ctrl = Controller(data_dir, agent_mode="standalone")
            ctrl.hosts.register({
                "schema": "spiceowl.host.v1",
                "host_id": "sim-a",
                "label": "EDA (sim-a)",
                "ssh_target": "sim-a",
                "expected_host": "sim-node-01",
                "expected_user": "researcher",
                "tools": [{"kind": "spectre", "executable": "/opt/cadence/bin/spectre"}],
            })
            ctrl.hosts.register({
                "schema": "spiceowl.host.v1",
                "host_id": "sim-b",
                "label": "EDA (sim-b)",
                "ssh_target": "sim-b",
                "expected_host": "sim-node-02",
                "expected_user": "researcher",
                "tools": [{"kind": "spectre", "executable": "/opt/cadence/bin/spectre"}],
            })

            def fake_ssh(profile, script_bytes):
                if profile["host_id"] == "sim-a":
                    return 0, _fake_live_stdout(script_bytes, host="sim-node-01", user="researcher"), ""
                return 0, _fake_live_stdout(script_bytes, host="sim-node-02", user="researcher", ps_lines=[]), ""

            with patch("spiceowl.host_activity._run_ssh_live_probe", side_effect=fake_ssh):
                res = inspect_pool_live_status(ctrl.hosts)

            self.assertEqual(res["summary"]["total_hosts"], 2)
            self.assertEqual(res["summary"]["reachable_hosts"], 2)
            self.assertEqual(res["summary"]["total_cpus"], 64)
            self.assertEqual(res["summary"]["active_simulations"], 2)
            self.assertEqual(res["summary"]["my_active_simulations"], 1)
            self.assertEqual(res["summary"]["active_eda_tools"], 2)
            # Never leaks ssh_target
            self.assertNotIn("ssh_target", json.dumps(res))
            # Persisted last_resources onto host records
            stored = ctrl.hosts.list()
            self.assertTrue(all(h["last_resources"] is not None for h in stored))

            # Verify HTTP endpoint POST /api/hosts/live-status
            srv = make_server(ctrl, port=0, enable_direct_exec=True)
            th = threading.Thread(target=srv.serve_forever, daemon=True)
            th.start()
            base = f"http://127.0.0.1:{srv.server_address[1]}"
            try:
                with urlopen(f"{base}/api/state", timeout=5) as resp:
                    tok = json.loads(resp.read().decode("utf-8"))["csrf_token"]
                with patch("spiceowl.host_activity._run_ssh_live_probe", side_effect=fake_ssh):
                    req = Request(
                        f"{base}/api/hosts/live-status",
                        data=b"{}",
                        headers={"Content-Type": "application/json", "Origin": base, "X-SpiceOwl-Token": tok},
                        method="POST",
                    )
                    with urlopen(req, timeout=10) as resp:
                        self.assertEqual(resp.status, 200)
                        body = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(body["summary"]["active_simulations"], 2)
                self.assertEqual(len(body["hosts"]), 2)
            finally:
                srv.shutdown()
                srv.server_close()
                ctrl.close()


if __name__ == "__main__":
    unittest.main()

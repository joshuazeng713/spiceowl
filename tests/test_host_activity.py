"""Tests for live server resource, user footprint, and FlexLM license inspection (`host_activity`)."""

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
    parse_lmstat_output,
)
from spiceowl.service import Controller
from spiceowl.web.server import make_server


def _fake_live_stdout(script_bytes: bytes, *, host: str = "sim-node-01", user: str = "researcher", ps_lines: list[str] | None = None) -> str:
    text = script_bytes.decode("utf-8")
    m = re.search(r"__SPICEOWL_([0-9a-f]+)_ID_BEGIN__", text)
    if m is not None:
        nonce = m.group(1)
        procs = "\n".join(ps_lines if ps_lines is not None else [
            "11846 colleague 608.0 0.4 591932 03:35:50 spectre",
            "194432 researcher 1600.0 10.0 13421772 23:54:29 spectre",
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
    ml = re.search(r"__SPICEOWL_([0-9a-f]+)_LIC_BEGIN__", text)
    assert ml is not None
    nonce = ml.group(1)
    return (
        f"__SPICEOWL_{nonce}_LIC_BEGIN__\n"
        "SPEC 5280@lic-a.example\n"
        "Users of Virtuoso_Multi_mode_Simulation:  (Total of 200 licenses issued;  Total of 85 licenses in use)\n"
        "Users of Spectre_X_MMSIM_Lk:  (Total of 200 licenses issued;  Total of 40 licenses in use)\n"
        "Users of Virtuoso_ADE_Assembler:  (Total of 50 licenses issued;  Total of 1 license in use)\n"
        "Users of Voltus_Power_Integrity_Fi_AA:  (Error: 70 licenses, unsupported by licensed server)\n"
        "SPEC 5280@lic-b.example\n"
        "Users of Virtuoso_Multi_mode_Simulation:  (Total of 200 licenses issued;  Total of 15 licenses in use)\n"
        f"__SPICEOWL_{nonce}_LIC_END__\n"
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
            "194432 researcher 1600.0 10.0 13421772 23:54:29 spectre",
            "194261 researcher 0.1 0.0 32840 23:54:29 spectreMonitor",
            "193141 colleague 13.2 9.1 11968160 06:10:50 virtuoso",
        ], expected_user="researcher")
        self.assertEqual(parsed["active_count"], 2)
        self.assertEqual(parsed["my_active_count"], 1)
        self.assertEqual(parsed["my_sim_cpu_percent"], 1600.0)
        self.assertEqual(parsed["my_sim_rss_bytes"], 13421772 * 1024)
        self.assertEqual(parsed["eda_tool_count"], 1)
        self.assertEqual(parsed["total_sim_cpu_percent"], 2208.0)
        self.assertEqual(parsed["processes"][0]["pid"], 194432)
        self.assertTrue(parsed["processes"][0]["is_me"])
        self.assertEqual(parsed["by_tool"], {"spectre": 2, "virtuoso": 1})

    def test_parse_lmstat_output_aggregates_servers_and_filters_errors(self) -> None:
        parsed = parse_lmstat_output([
            "SPEC 5280@lic-a.example",
            "Users of Virtuoso_Multi_mode_Simulation:  (Total of 200 licenses issued;  Total of 85 licenses in use)",
            "Users of Virtuoso_ADE_Assembler:  (Total of 50 licenses issued;  Total of 1 license in use)",
            "Users of Voltus_Power:  (Error: 70 licenses, unsupported by licensed server)",
            "Users of clio_sos:  (Total of 114514 licenses issued;  Total of 12 licenses in use)",
            "SPEC 5280@lic-b.example",
            "Users of Virtuoso_Multi_mode_Simulation:  (Total of 200 licenses issued;  Total of 15 licenses in use)",
        ])
        self.assertTrue(parsed["available"])
        self.assertEqual(parsed["servers_queried"], 2)
        self.assertEqual(parsed["servers_responsive"], 2)
        mmsim = next(f for f in parsed["features"] if f["label"] == "Spectre (MMSIM)")
        self.assertEqual((mmsim["issued"], mmsim["in_use"], mmsim["available"]), (400, 100, 300))
        self.assertEqual(mmsim["used_percent"], 25.0)
        ade = next(f for f in parsed["features"] if f["label"] == "ADE Assembler")
        self.assertEqual((ade["issued"], ade["in_use"], ade["available"]), (50, 1, 49))
        # Sentinel >50000 and Error lines excluded
        self.assertFalse(any(f["feature"] == "clio_sos" for f in parsed["all_active_features"]))
        self.assertFalse(any(f["feature"] == "Voltus_Power" for f in parsed["all_active_features"]))

    def test_pool_live_status_user_footprint_licenses_and_http_route(self) -> None:
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

            def fake_ssh(profile, script_bytes, **_kwargs):
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
            self.assertEqual(len(res["my_simulations"]), 1)
            self.assertEqual(res["my_simulations"][0]["host_id"], "sim-a")
            self.assertEqual(res["my_simulations"][0]["tool"], "spectre")

            # Verify user's CPU% and Memory% share on sim-a (1600% / 32 cores = 50.0% host CPU; ~12.8 GiB / 128 GiB = 10.0%)
            sim_a = next(h for h in res["hosts"] if h["host_id"] == "sim-a")
            self.assertEqual(sim_a["resources"]["my_sim_cpu_cores"], 16.0)
            self.assertEqual(sim_a["resources"]["my_sim_cpu_percent"], 50.0)
            self.assertEqual(sim_a["resources"]["my_sim_memory_percent"], 10.0)

            # Verify FlexLM licenses aggregated
            self.assertTrue(res["licenses"]["available"])
            mmsim = next(f for f in res["licenses"]["features"] if f["label"] == "Spectre (MMSIM)")
            self.assertEqual((mmsim["issued"], mmsim["in_use"], mmsim["available"]), (400, 100, 300))

            # Never leaks ssh_target
            self.assertNotIn("ssh_target", json.dumps(res))
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
                self.assertEqual(len(body["my_simulations"]), 1)
                self.assertTrue(body["licenses"]["available"])
            finally:
                srv.shutdown()
                srv.server_close()
                ctrl.close()


if __name__ == "__main__":
    unittest.main()

"""End-to-end tests for the cross-platform onboarding wizard (`spiceowl init`),
SSH config alias discovery, 5-station vivid demo bootstrap, and 4-step real EDA
server registration + zero-PDK first simulation preparation."""

from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from spiceowl.batch import DirectBatchManager
from spiceowl.cli.main import main as cli_main
from spiceowl.direct import DirectExecutionManager
from spiceowl.domain.common import DomainError
from spiceowl.onboarding_wizard import (
    FIRST_SIM_TEMPLATES,
    bootstrap_vivid_demo,
    diagnose_local_environment,
    parse_ssh_config_aliases,
    prepare_first_simulation,
    quick_register_eda_host,
)
from spiceowl.service import Controller
from spiceowl.web.server import make_server


class OnboardingWizardUnitTests(unittest.TestCase):
    def test_parse_ssh_config_aliases_skips_wildcards_and_keeps_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config"
            cfg.write_text(
                "# User SSH config\n"
                "Host *\n"
                "    ServerAliveInterval 30\n\n"
                "Host eda-sim-01 *.internal\n"
                "    HostName 10.20.30.40\n"
                "    User analog_eng\n"
                "    Port 2222\n"
                "    ProxyJump bastion\n\n"
                "Host lsf-login\n"
                "    HostName lsf.example.invalid\n"
                "    User designer\n",
                encoding="utf-8",
            )
            aliases = parse_ssh_config_aliases(cfg)
            self.assertEqual([a["alias"] for a in aliases], ["eda-sim-01", "lsf-login"])
            self.assertEqual(aliases[0]["hostname"], "10.20.30.40")
            self.assertEqual(aliases[0]["user"], "analog_eng")
            self.assertEqual(aliases[0]["port"], 2222)
            self.assertEqual(aliases[0]["proxy_jump"], "bastion")
            self.assertEqual(aliases[1]["hostname"], "lsf.example.invalid")

    def test_diagnose_vivid_demo_and_first_simulation_preparation(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "state"
            with Controller(data_dir, agent_mode="standalone") as app:
                dm = DirectExecutionManager(data_dir, hosts=app.hosts)
                bm = DirectBatchManager(data_dir, dm)

                env = diagnose_local_environment(data_dir, hosts=app.hosts, direct_manager=dm, batch_manager=bm)
                self.assertEqual(env["schema"], "spiceowl.onboarding-wizard.v1")
                self.assertEqual(len(env["templates"]), len(FIRST_SIM_TEMPLATES))
                self.assertGreaterEqual(len(env["legacy_notices"]), 2)

                # Vivid 5-station demo bootstrap (idempotent)
                demo1 = bootstrap_vivid_demo(app, dm, bm)
                self.assertEqual(len(demo1["stations"]), 5)
                self.assertEqual(demo1["waveform_demo_run_id"], "demo-psfbin-500c")
                self.assertEqual(demo1["pvt_batch_id"], "demo-pvt-27")
                self.assertEqual(demo1["mc_batch_id"], "demo-mc-40")
                self.assertEqual(demo1["eval_deterministic_pass_pct"], 100.0)
                demo2 = bootstrap_vivid_demo(app, dm, bm)
                self.assertEqual(demo2["waveform_demo_run_id"], demo1["waveform_demo_run_id"])

                # Quick register EDA host & freeze zero-PDK smoke first simulation
                host, created = quick_register_eda_host(
                    app.hosts,
                    host_id="my-eda-01",
                    label="My Lab Server",
                    ssh_target="eda-sim",
                    expected_host="eda-node-01",
                    expected_user="designer",
                    simulator="spectre",
                    simulator_executable="/opt/cadence/bin/spectre",
                )
                self.assertTrue(created)
                self.assertEqual(host["host_id"], "my-eda-01")

                f1 = prepare_first_simulation(dm, host_id="my-eda-01", simulator="spectre", backend="ssh_direct")
                self.assertEqual(f1["run"]["run_id"], "first-sim-my-eda-01-01")
                self.assertEqual(f1["run"]["state"], "prepared")
                self.assertEqual(f1["run"]["output_format"], "psfbin")

                quick_register_eda_host(
                    app.hosts,
                    host_id="my-ng-01",
                    label="My ngspice Server",
                    ssh_target="ng-sim",
                    expected_host="ng-node-01",
                    expected_user="designer",
                    simulator="ngspice",
                    simulator_executable="/usr/bin/ngspice",
                )
                f2 = prepare_first_simulation(
                    dm, host_id="my-ng-01", simulator="ngspice", backend="slurm", partition="analog",
                )
                self.assertEqual(f2["run"]["run_id"], "first-sim-my-ng-01-01")
                self.assertEqual(f2["run"]["backend"], "slurm")

                with self.assertRaises(DomainError):
                    prepare_first_simulation(dm, host_id="my-eda-01", backend="lsf")


class OnboardingWizardCliAndWebTests(unittest.TestCase):
    def test_cli_init_and_web_wizard_endpoints_with_csrf(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "state"

            # 1. CLI `spiceowl init --demo --host-id ... --prepare-smoke --json`
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli_main([
                    "--data-dir", str(data_dir), "init",
                    "--demo",
                    "--host-id", "cli-eda",
                    "--ssh-target", "cli-eda-alias",
                    "--expected-host", "eda-box",
                    "--expected-user", "eng",
                    "--simulator", "spectre",
                    "--prepare-smoke",
                    "--json",
                ])
            self.assertEqual(rc, 0)
            parsed = json.loads(buf.getvalue())
            self.assertEqual(len(parsed["demo"]["stations"]), 5)
            self.assertEqual(parsed["registered_host"]["host"]["host_id"], "cli-eda")
            self.assertEqual(parsed["first_simulation"]["run"]["run_id"], "first-sim-cli-eda-01")

            # 2. Web HTTP endpoints + CSRF guard
            controller = Controller(data_dir, agent_mode="standalone")
            server = make_server(controller, 0)
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                th = threading.Thread(target=server.serve_forever, daemon=True)
                th.start()

                with urlopen(f"{base}/", timeout=5) as resp:
                    index_html = resp.read().decode("utf-8")
                self.assertIn('data-nav="quickstart"', index_html)

                with urlopen(f"{base}/api/state", timeout=5) as resp:
                    token = json.loads(resp.read().decode("utf-8"))["csrf_token"]

                with urlopen(f"{base}/api/wizard/status", timeout=5) as resp:
                    w_status = json.loads(resp.read().decode("utf-8"))
                self.assertGreaterEqual(w_status["workspace"]["host_count"], 1)

                # Missing CSRF rejected with 403
                bad = Request(f"{base}/api/wizard/demo", data=b"{}", headers={"Content-Type": "application/json", "Origin": base}, method="POST")
                with self.assertRaises(HTTPError) as err:
                    urlopen(bad, timeout=5)
                self.assertEqual(err.exception.code, 403)

                def post_json(path, payload):
                    req = Request(
                        f"{base}{path}",
                        data=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Origin": base, "X-SpiceOwl-Token": token},
                        method="POST",
                    )
                    with urlopen(req, timeout=15) as resp:
                        return resp.status, json.loads(resp.read().decode("utf-8"))

                st_d, demo_res = post_json("/api/wizard/demo", {})
                self.assertEqual(st_d, 201)
                self.assertEqual(len(demo_res["stations"]), 5)

                st_h, host_res = post_json("/api/wizard/register-host", {
                    "host_id": "web-eda",
                    "label": "Web EDA Server",
                    "ssh_target": "web-eda-alias",
                    "expected_host": "web-eda.local",
                    "expected_user": "alice",
                    "simulator": "spectre",
                    "probe": False,
                })
                self.assertEqual(st_h, 201)
                self.assertEqual(host_res["host"]["host_id"], "web-eda")

                st_f, fs_res = post_json("/api/wizard/first-sim", {
                    "host_id": "web-eda",
                    "simulator": "spectre",
                    "backend": "ssh_direct",
                    "template_id": "spectre_rc_smoke",
                })
                self.assertEqual(st_f, 201)
                self.assertEqual(fs_res["run"]["run_id"], "first-sim-web-eda-01")
            finally:
                server.shutdown()
                server.server_close()
                controller.close()


if __name__ == "__main__":
    unittest.main()

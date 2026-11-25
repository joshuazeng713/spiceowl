"""End-to-end tests for PVT / Corner / Monte Carlo batch experiment management.

Verifies deterministic matrix expansion, seed/sample identity preservation,
bounded concurrency dispatch/refresh/cancel, lossless PSF metric extraction,
conservative yield & Cpk statistics (never dropping failed/missing samples),
Markdown/CSV reports, Web API + CSRF protection, CLI, and standalone Agent tools.
"""

from __future__ import annotations

import io
import json
import math
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from spiceowl.batch import (
    DirectBatchManager,
    compute_metric_statistics,
    expand_batch_cases,
    render_case_netlist,
)
from spiceowl.cli.main import main as cli_main
from spiceowl.direct import DirectExecutionManager
from spiceowl.domain.common import DomainError
from spiceowl.hosts import HostRegistry
from spiceowl.integrations import psf_binary
from spiceowl.service import Controller
from spiceowl.standalone_agent import build_standalone_dispatcher
from spiceowl.web.server import make_server


def _register_host(data_dir: Path, host_id: str = "sim-box") -> HostRegistry:
    hosts = HostRegistry(data_dir)
    hosts.register({
        "schema": "spiceowl.host.v1",
        "host_id": host_id,
        "label": "Sim Box",
        "ssh_target": host_id,
        "expected_host": "sim-box.local",
        "expected_user": "edward",
        "tools": [{"kind": "spectre", "executable": "/opt/cadence/bin/spectre"}],
    })
    return hosts


class BatchExpansionAndStatsTests(unittest.TestCase):
    def test_pvt_mc_and_hybrid_expansion_and_limits(self):
        pvt = expand_batch_cases(
            kind="pvt",
            process_corners=["tt", "ss", "ff"],
            voltages=[1.08, 1.20, 1.32],
            temperatures=[-40, 27, 125],
        )
        self.assertEqual(len(pvt), 27)
        self.assertEqual(len({c["case_id"] for c in pvt}), 27)
        self.assertIsNone(pvt[0]["mc_seed"])

        mc = expand_batch_cases(
            kind="monte_carlo",
            mc_samples=15,
            mc_base_seed=5000,
            mc_variation="mismatch",
        )
        self.assertEqual(len(mc), 15)
        self.assertEqual([c["mc_seed"] for c in mc], list(range(5000, 5015)))
        self.assertEqual([c["sample_index"] for c in mc], list(range(15)))

        custom_mc = expand_batch_cases(kind="monte_carlo", mc_seeds=[11, 22, 33])
        self.assertEqual([c["mc_seed"] for c in custom_mc], [11, 22, 33])

        hybrid = expand_batch_cases(
            kind="pvt_mc",
            process_corners=["ss", "ff"],
            voltages=[1.1, 1.3],
            temperatures=[27],
            mc_samples=3,
            mc_base_seed=700,
        )
        self.assertEqual(len(hybrid), 12)

        with self.assertRaises(DomainError) as ctx:
            expand_batch_cases(
                kind="pvt_mc",
                process_corners=["tt", "ss", "ff", "sf", "fs"],
                voltages=[1.0, 1.1, 1.2, 1.3],
                temperatures=[-40, 27, 85, 125],
                mc_samples=4,
            )
        self.assertEqual(ctx.exception.code, "batch_limit_exceeded")

    def test_netlist_parameter_binding_placeholder_and_injection(self):
        case = expand_batch_cases(
            kind="monte_carlo", process_corners=["ss"], voltages=[1.08], temperatures=[125],
            mc_samples=1, mc_base_seed=4242,
        )[0]
        # Placeholder mode
        tpl_tok = "simulator lang=spectre\ninclude \"m.scs\" section={{CORNER}}\nparameters vdd={{VDD}} s={{MC_SEED}}\nopts options temp={{TEMP}}\n"
        rendered_tok = render_case_netlist(tpl_tok, case, simulator="spectre")
        self.assertIn("section=ss", rendered_tok)
        self.assertIn("vdd=1.08 s=4242", rendered_tok)
        self.assertIn("temp=125", rendered_tok)

        # Spectre header injection mode
        tpl_plain = "simulator lang=spectre\nV1 (out 0) vsource dc=vdd\n"
        rendered_sp = render_case_netlist(tpl_plain, case, simulator="spectre")
        self.assertIn("parameters vdd=1.08 temp_c=125 mc_seed=4242", rendered_sp)
        self.assertIn("spiceowl_case_opts options temp=125", rendered_sp)

        # ngspice injection mode
        tpl_ng = "* inverter\nV1 out 0 DC vdd\n.end\n"
        rendered_ng = render_case_netlist(tpl_ng, case, simulator="ngspice")
        self.assertIn(".param vdd=1.08", rendered_ng)
        self.assertIn(".option temp=125", rendered_ng)
        self.assertIn(".param mc_seed=4242", rendered_ng)

    def test_metric_statistics_and_cpk(self):
        vals = [0.88, 0.89, 0.90, 0.90, 0.91, 0.92]
        st = compute_metric_statistics(vals, spec_min=0.85, spec_max=0.95)
        self.assertEqual(st["count"], 6)
        self.assertAlmostEqual(st["mean"], 0.90, places=6)
        self.assertGreater(st["std"], 0.0)
        self.assertAlmostEqual(st["p50"], 0.90, places=6)
        self.assertGreater(st["cpk"], 1.0)


class BatchConcurrencyAndDemoTests(unittest.TestCase):
    def test_bounded_concurrency_dispatch_refresh_lossless_metrics_and_cancel(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            hosts = _register_host(data_dir)
            dm = DirectExecutionManager(data_dir, hosts=hosts)
            bm = DirectBatchManager(data_dir, dm)

            created = bm.prepare(
                batch_id="conc-pvt-01",
                netlist_template="simulator lang=spectre\nV1 (out 0) vsource dc={{VDD}}\n",
                kind="pvt",
                process_corners=["tt", "ss", "ff"],
                voltages=[1.08, 1.20],
                temperatures=[27],
                specs=[{"name": "vout_end", "signal": "v(out)", "metric": "end", "min": 1.10, "max": 1.25, "unit": "V"}],
                max_concurrency=2,
                host_id="sim-box",
            )
            self.assertEqual(created["summary"]["counts"]["total"], 6)
            self.assertEqual(created["summary"]["counts"]["pending"], 6)

            def _set_state(run_id: str, state: str, *, collected: bool = False, v_end: float | None = None):
                rpath = dm.path(run_id) / "run.json"
                rec = json.loads(rpath.read_text(encoding="utf-8"))
                rec["state"] = state
                if state in ("submitted", "running_observed", "simulator_complete_pending_measurements"):
                    rec["dispatch_claimed"] = True
                if collected:
                    rec["collection_phase"] = "complete"
                    raw_dir = dm.path(run_id) / "collected" / "results.raw"
                    raw_dir.mkdir(parents=True, exist_ok=True)
                    psf_binary.write_psf_binary(
                        raw_dir / "tran.tran.tran",
                        sweep_name="time",
                        sweep_values=[0.0, 1e-9, 2e-9],
                        signals={"v(out)": [0.0, 0.5 * v_end, v_end]},
                    )
                rpath.write_text(json.dumps(rec, indent=2), encoding="utf-8")
                return rec

            staged_ids, launched_ids, cancelled_ids = [], [], []

            def fake_stage(rid):
                staged_ids.append(rid)
                return _set_state(rid, "staged")

            def fake_launch(rid, recheck_resources=None):
                launched_ids.append(rid)
                return _set_state(rid, "running_observed")

            def fake_status(rid):
                # Mark only the very first launched case as completed!
                if rid == launched_ids[0]:
                    return _set_state(rid, "simulator_complete_pending_measurements")
                return dm.read(rid)

            def fake_collect(rid):
                # First case is tt / 1.08V -> ends at 1.08V (below min=1.10 -> failed_spec!)
                return _set_state(rid, "simulator_complete_pending_measurements", collected=True, v_end=1.08)

            def fake_cancel(rid, action_key=None):
                cancelled_ids.append(rid)
                _set_state(rid, "simulator_failed")
                return {"outcome": "confirmed"}

            with mock.patch.object(dm, "stage", side_effect=fake_stage), \
                 mock.patch.object(dm, "launch", side_effect=fake_launch), \
                 mock.patch.object(dm, "status", side_effect=fake_status), \
                 mock.patch.object(dm, "collect", side_effect=fake_collect), \
                 mock.patch.object(dm, "cancel", side_effect=fake_cancel):
                # 1. First dispatch must respect max_concurrency=2
                d1 = bm.dispatch_next("conc-pvt-01")
                self.assertEqual(len(d1["dispatched"]), 2)
                self.assertEqual(d1["summary"]["counts"]["active"], 2)
                self.assertEqual(d1["summary"]["counts"]["pending"], 4)

                # 2. Second dispatch while 2 are active MUST dispatch 0!
                d2 = bm.dispatch_next("conc-pvt-01")
                self.assertEqual(len(d2["dispatched"]), 0)
                self.assertEqual(len(launched_ids), 2)

                # 3. Refresh collects case #0 (extracts lossless v(out)=1.08 -> failed_spec!) and dispatches 1 replacement
                r1 = bm.refresh("conc-pvt-01", auto_collect=True, dispatch_more=True)
                self.assertEqual(len(r1["collected"]), 1)
                self.assertEqual(len(r1["dispatched"]), 1)
                self.assertEqual(r1["summary"]["counts"]["active"], 2)
                self.assertEqual(r1["summary"]["counts"]["failed_spec"], 1)
                self.assertEqual(r1["summary"]["counts"]["pending"], 3)
                self.assertAlmostEqual(r1["summary"]["cases"][0]["metrics"]["vout_end"], 1.08, places=6)

                # 4. Cancel cancels 2 active runs and blocks remaining 3 pending runs
                c1 = bm.cancel("conc-pvt-01")
                self.assertEqual(len(c1["cancelled_active"]), 2)
                self.assertEqual(len(c1["cancelled_pending"]), 3)
                self.assertEqual(len(cancelled_ids), 2)

                # Subsequent dispatch launches 0 because all remaining pending cases were batch-cancelled
                d3 = bm.dispatch_next("conc-pvt-01")
                self.assertEqual(len(d3["dispatched"]), 0)

    def test_demo_pvt_and_monte_carlo_preserve_conservative_yield_and_failed_seeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            hosts = _register_host(data_dir)
            dm = DirectExecutionManager(data_dir, hosts=hosts)
            bm = DirectBatchManager(data_dir, dm)

            pvt = bm.create_demo_batch(kind="pvt")
            ps = pvt["summary"]
            self.assertEqual(ps["counts"]["total"], 27)
            self.assertEqual(ps["counts"]["passed"], 25)
            self.assertEqual(ps["counts"]["failed_spec"], 1)
            self.assertEqual(ps["counts"]["execution_failed"], 1)
            # Conservative yield MUST keep execution_failed in denominator: 25/27 = 92.59%
            self.assertAlmostEqual(ps["yield"]["conservative_yield_pct"], round(100.0 * 25 / 27, 2), places=2)
            self.assertEqual(len(ps["missing_or_failed_samples"]), 2)
            self.assertIn("Confirmed OOM", pvt["markdown"])

            mc = bm.create_demo_batch(kind="monte_carlo")
            ms = mc["summary"]
            self.assertEqual(ms["counts"]["total"], 40)
            self.assertEqual(ms["counts"]["passed"], 37)
            self.assertEqual(ms["counts"]["failed_spec"], 2)
            self.assertEqual(ms["counts"]["execution_failed"], 1)
            # Conservative yield: 37/40 = 92.5% (NOT 37/39 = 94.87%!)
            self.assertAlmostEqual(ms["yield"]["conservative_yield_pct"], 92.5, places=2)
            failed_seeds = sorted(item["mc_seed"] for item in ms["missing_or_failed_samples"])
            self.assertEqual(failed_seeds, [2017, 2029, 2033])
            self.assertIn("2029", mc["csv"])
            self.assertIn("2017", mc["markdown"])
            self.assertIsNotNone(ms["metric_stats"]["vos_mv"]["cpk"])


class BatchCliWebAndAgentTests(unittest.TestCase):
    def test_cli_agent_and_web_api_batch_endpoints_with_csrf(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "state"
            _register_host(data_dir)
            tpl_file = Path(tmp) / "inv.scs"
            tpl_file.write_text("simulator lang=spectre\nV1 (out 0) vsource dc={{VDD}}\n", encoding="utf-8")

            def run_cli(*argv):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cli_main(["--data-dir", str(data_dir), *argv])
                self.assertEqual(rc, 0)
                return buf.getvalue()

            # 1. CLI batch-demo, batches, batch-inspect, batch-report, batch-prepare, batch-cancel
            demo_out = json.loads(run_cli("direct", "batch-demo", "--kind", "monte_carlo", "--batch-id", "cli-mc-demo"))
            self.assertEqual(demo_out["summary"]["counts"]["total"], 40)

            prep_out = json.loads(run_cli(
                "direct", "batch-prepare",
                "--batch-id", "cli-pvt-prep",
                "--netlist", str(tpl_file),
                "--kind", "pvt",
                "--corners", "tt", "ss",
                "--voltages", "1.1", "1.2",
                "--temps", "27",
                "--max-concurrency", "2",
                "--host", "sim-box",
            ))
            self.assertEqual(prep_out["summary"]["counts"]["total"], 4)

            listed = json.loads(run_cli("direct", "batches"))
            self.assertEqual({b["batch_id"] for b in listed["batches"]}, {"cli-mc-demo", "cli-pvt-prep"})

            md_report = run_cli("direct", "batch-report", "cli-mc-demo", "--markdown")
            self.assertIn("Conservative Yield", md_report)
            csv_report = run_cli("direct", "batch-report", "cli-mc-demo", "--csv")
            self.assertIn("mc_seed", csv_report)

            cancel_out = json.loads(run_cli("direct", "batch-cancel", "cli-pvt-prep"))
            self.assertEqual(len(cancel_out["cancelled_pending"]), 4)

            # 2. Standalone Agent policies & read/control dispatch
            controller = Controller(data_dir, agent_mode="standalone")
            dm = DirectExecutionManager(data_dir, hosts=controller.hosts)
            dispatcher = build_standalone_dispatcher(controller, direct_manager=dm)
            self.assertEqual(
                dispatcher.policy("spiceowl_direct_read", {"operation": "batch_list", "arguments": {}}),
                "read",
            )
            self.assertEqual(
                dispatcher.policy("spiceowl_direct_control", {"operation": "batch_dispatch", "arguments": {"batch_id": "cli-pvt-prep"}}),
                "approval",
            )
            ag_list = dispatcher.dispatch("spiceowl_direct_read", {"operation": "batch_list", "arguments": {}})
            self.assertGreaterEqual(len(ag_list["batches"]), 2)
            ag_insp = dispatcher.dispatch("spiceowl_direct_read", {"operation": "batch_inspect", "arguments": {"batch_id": "cli-mc-demo"}})
            self.assertEqual(ag_insp["counts"]["total"], 40)

            # 3. Web HTTP Server endpoints + CSRF check
            server = make_server(controller, 0)
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                th = threading.Thread(target=server.serve_forever, daemon=True)
                th.start()

                with urlopen(f"{base}/api/state", timeout=5) as resp:
                    token = json.loads(resp.read().decode("utf-8"))["csrf_token"]

                # Missing CSRF rejected with 403
                bad_req = Request(
                    f"{base}/api/direct/batches/demo",
                    data=b'{"kind":"pvt"}',
                    headers={"Content-Type": "application/json", "Origin": base},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(bad_req, timeout=5)
                self.assertEqual(ctx.exception.code, 403)

                def post_json(path, payload):
                    req = Request(
                        f"{base}{path}",
                        data=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Origin": base, "X-SpiceOwl-Token": token},
                        method="POST",
                    )
                    with urlopen(req, timeout=10) as resp:
                        return resp.status, json.loads(resp.read().decode("utf-8"))

                st, created = post_json("/api/direct/batches/demo", {"kind": "pvt", "batch_id": "web-pvt-demo"})
                self.assertEqual(st, 201)
                self.assertEqual(created["summary"]["counts"]["total"], 27)

                with urlopen(f"{base}/api/direct/batches", timeout=5) as resp:
                    w_list = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(any(b["batch_id"] == "web-pvt-demo" for b in w_list["batches"]))

                with urlopen(f"{base}/api/direct/batches/web-pvt-demo", timeout=5) as resp:
                    w_rep = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(w_rep["summary"]["counts"]["passed"], 25)

                st_p, w_prep = post_json("/api/direct/batches/prepare", {
                    "batch_id": "web-prep-01",
                    "netlist_template": "simulator lang=spectre\nV1 (out 0) vsource dc={{VDD}}\n",
                    "kind": "pvt",
                    "process_corners": ["tt", "ff"],
                    "voltages": [1.2],
                    "temperatures": [27],
                    "max_concurrency": 1,
                    "host_id": "sim-box",
                })
                self.assertEqual(st_p, 201)
                self.assertEqual(w_prep["summary"]["counts"]["total"], 2)

                st_c, w_can = post_json("/api/direct/batches/web-prep-01/cancel", {})
                self.assertEqual(st_c, 202)
                self.assertEqual(len(w_can["cancelled_pending"]), 2)
            finally:
                server.shutdown()
                server.server_close()
                controller.close()


if __name__ == "__main__":
    unittest.main()

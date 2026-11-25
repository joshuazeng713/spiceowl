"""End-to-end tests for PSF Binary / PSFXL / ASCII streaming waveforms, Min-Max
envelope downsampling, automatic exact-window switching, Cycle Slicer drill-down,
compute-near-data remote in-place SSH queries, Web API, CLI, and Agent tools."""

from __future__ import annotations

import base64
import io
import json
import math
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from spiceowl.adapters.executors import direct as direct_transport
from spiceowl.cli.main import main as cli_main
from spiceowl.direct import DirectExecutionManager
from spiceowl.domain.common import DomainError
from spiceowl.hosts import HostRegistry
from spiceowl.integrations import psf_binary
from spiceowl.service import Controller
from spiceowl.standalone_agent import build_standalone_dispatcher
from spiceowl.web.server import make_server


class PsfBinaryParserTests(unittest.TestCase):
    def test_psf_binary_non_windowed_and_windowed_roundtrip(self):
        xs = [i * 1e-9 for i in range(64)]
        sig_a = [math.sin(2.0 * math.pi * i / 16.0) for i in range(64)]
        sig_b = [0.5 + 0.25 * math.cos(2.0 * math.pi * i / 8.0) for i in range(64)]

        with tempfile.TemporaryDirectory() as tmp:
            for windowed in (False, True):
                path = Path(tmp) / f"tran_{'win' if windowed else 'flat'}.tran.tran"
                psf_binary.write_psf_binary(
                    path,
                    sweep_name="time",
                    sweep_values=xs,
                    signals={"v(out)": sig_a, "v(ref)": sig_b},
                    sweep_unit="s",
                    signal_units={"v(out)": "V", "v(ref)": "V"},
                    windowed=windowed,
                    window_samples=16,
                )
                self.assertEqual(psf_binary.detect_waveform_format(path), "psfbin")
                reader = psf_binary.open_waveform_reader(path)
                meta = reader.summary()
                self.assertEqual(meta["format"], "psfbin")
                self.assertEqual(meta["windowed"], windowed)
                self.assertEqual(meta["sweep"]["count"], 64)
                self.assertEqual([s["name"] for s in meta["signals"]], ["v(out)", "v(ref)"])

                q = psf_binary.query_waveform(path, ["v(out)"], x_min=xs[10], x_max=xs[25], mode="auto")
                self.assertEqual(q["representation"], "exact_window")
                self.assertTrue(q["exact"])
                self.assertTrue(q["measurement_safe"])
                self.assertEqual(len(q["x"]), 16)
                self.assertAlmostEqual(q["x"][0], 10e-9, places=15)
                self.assertAlmostEqual(q["x"][-1], 25e-9, places=15)
                for idx, val in enumerate(q["signals"]["v(out)"]["values"]):
                    self.assertAlmostEqual(val, sig_a[10 + idx], places=12)

    def test_psf_binary_complex_ac_roundtrip(self):
        freqs = [1e3 * (10 ** (i / 10.0)) for i in range(25)]
        h = [complex(1.0 / (1.0 + (f / 1e4) ** 2), -(f / 1e4) / (1.0 + (f / 1e4) ** 2)) for f in freqs]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ac.ac"
            psf_binary.write_psf_binary(
                path,
                sweep_name="freq",
                sweep_values=freqs,
                signals={"v(out)": h},
                sweep_unit="Hz",
                signal_units={"v(out)": "V"},
                windowed=False,
            )
            q = psf_binary.query_waveform(path, ["v(out)"], mode="exact")
            self.assertTrue(q["exact"])
            first = q["signals"]["v(out)"]["values"][0]
            self.assertIsInstance(first, dict)
            self.assertAlmostEqual(first["real"], h[0].real, places=10)
            self.assertAlmostEqual(first["imag"], h[0].imag, places=10)
            self.assertAlmostEqual(first["mag"], abs(h[0]), places=10)

            with self.assertRaises(DomainError) as ctx:
                psf_binary.extract_signal_for_adctools(path, "v(out)")
            self.assertEqual(ctx.exception.code, "measurement_requires_real")

    def test_psfxl_bundle_and_directory_indexing(self):
        xs = [i * 1e-9 for i in range(200)]
        vals = [math.sin(2.0 * math.pi * i / 20.0) for i in range(200)]
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "results.raw"
            raw_dir.mkdir()
            xl_path = psf_binary.write_psfxl_bundle(
                raw_dir / "tran.tran.tran.psfxl",
                sweep_name="time",
                sweep_values=xs,
                signals={"v(out)": vals},
                sweep_unit="s",
                signal_units={"v(out)": "V"},
            )
            self.assertEqual(psf_binary.detect_waveform_format(xl_path), "psfxl")
            idx = psf_binary.index_waveform_directory(raw_dir)
            # Companion .dat must not be double-counted as a separate dataset.
            self.assertEqual(len(idx["datasets"]), 1)
            self.assertEqual(idx["datasets"][0]["format"], "psfxl")
            self.assertEqual(idx["datasets"][0]["sweep"]["count"], 200)

            q = psf_binary.query_waveform(xl_path, ["v(out)"], x_min=50e-9, x_max=80e-9, max_points=100)
            self.assertTrue(q["exact"])
            self.assertEqual(len(q["x"]), 31)
            self.assertAlmostEqual(q["signals"]["v(out)"]["values"][0], vals[50], places=12)

    def test_psf_ascii_unified_query_and_cycle_slicer(self):
        lines = [
            "HEADER",
            '"PSFversion" "1.00"',
            "TYPE",
            '"time" FLOAT DOUBLE PROP( "units" "s" )',
            '"V" FLOAT DOUBLE PROP( "units" "V" )',
            "SWEEP",
            '"time" "time"',
            "TRACE",
            '"v(out)" "V"',
            "VALUE",
        ]
        for c in range(12):
            for s in range(10):
                t = (c * 10 + s) * 1e-9
                v = 0.5 + 0.1 * math.sin(2.0 * math.pi * s / 10.0) + (0.4 if c == 7 and s == 3 else 0.0)
                lines.append(f'"time" {t:.12e}')
                lines.append(f'"v(out)" {v:.12e}')
        lines.append("END")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "tran.tran"
            p.write_text("\n".join(lines), encoding="utf-8")
            self.assertEqual(psf_binary.detect_waveform_format(p), "psfascii")
            cyc = psf_binary.analyze_cycles(p, ["v(out)"], period=10e-9, worst_metric="peak_to_peak")
            self.assertEqual(cyc["format"], "psfascii")
            self.assertEqual(cyc["cycle_count"], 11)
            self.assertEqual(cyc["worst_cycle_index"], 7)
            self.assertTrue(cyc["key_cycles"]["worst_case"]["exact"])


class EnvelopeAndCycleSlicerTests(unittest.TestCase):
    def test_minmax_envelope_preserves_glitch_and_zoom_switches_to_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            psf_path = psf_binary.generate_demo_multicycle_psf(
                Path(tmp) / "tran.tran.tran",
                cycles=500,
                samples_per_cycle=40,
                period_s=1e-8,
                glitch_cycle=317,
                windowed=True,
            )
            # 1. Full 20,000-point range with max_points=400 -> Min-Max envelope preview
            preview = psf_binary.query_waveform(psf_path, ["v(out)", "v(clk)"], max_points=400, mode="auto")
            self.assertEqual(preview["representation"], "downsampled_preview")
            self.assertFalse(preview["exact"])
            self.assertFalse(preview["measurement_safe"])
            self.assertEqual(preview["downsampling"]["algorithm"], "min_max_envelope")
            self.assertLessEqual(len(preview["signals"]["v(out)"]["values"]), 402)

            # Compare against lossless exact peak across full dataset -> Min-Max MUST keep exact glitch peak!
            exact_full = psf_binary.query_waveform(psf_path, ["v(out)"], mode="exact")
            self.assertTrue(exact_full["exact"])
            self.assertTrue(exact_full["measurement_safe"])
            exact_peak = max(exact_full["signals"]["v(out)"]["values"])
            preview_peak = max(preview["signals"]["v(out)"]["values"])
            self.assertAlmostEqual(preview_peak, exact_peak, places=12)
            self.assertGreater(preview_peak, 1.10)  # Glitch pushes v(out) well above steady 0.95 V

            # 2. Zoom into cycles 316..319 (~120 points <= 400) -> auto exact_window
            zoomed = psf_binary.query_waveform(
                psf_path, ["v(out)", "v(clk)"],
                x_min=316.0e-8, x_max=319.0e-8, max_points=400, mode="auto",
            )
            self.assertEqual(zoomed["representation"], "exact_window")
            self.assertTrue(zoomed["exact"])
            self.assertTrue(zoomed["measurement_safe"])
            self.assertIsNone(zoomed["envelopes"])
            self.assertAlmostEqual(max(zoomed["signals"]["v(out)"]["values"]), exact_peak, places=12)

            # 3. mode="exact" refuses silent downsampling when window exceeds MAX_EXACT_SAMPLES
            with mock.patch.object(psf_binary, "MAX_EXACT_SAMPLES", 100):
                with self.assertRaises(DomainError) as ctx:
                    psf_binary.query_waveform(psf_path, ["v(out)"], mode="exact")
                self.assertEqual(ctx.exception.code, "exact_window_limit")

            # 4. Scientific extraction for adctools is strictly lossless
            adc_payload = psf_binary.extract_signal_for_adctools(
                psf_path, "v(out)", x_min=400e-8, x_max=410e-8,
            )
            self.assertTrue(adc_payload["exact"])
            self.assertTrue(adc_payload["measurement_safe"])
            self.assertEqual(adc_payload["sample_count"], len(adc_payload["samples"]))
            self.assertGreater(adc_payload["sample_count"], 350)

    def test_cycle_slicer_clock_and_period_pinpoint_hidden_glitch_and_drilldown(self):
        with tempfile.TemporaryDirectory() as tmp:
            psf_path = psf_binary.generate_demo_multicycle_psf(
                Path(tmp) / "tran.tran.tran",
                cycles=500,
                samples_per_cycle=40,
                period_s=1e-8,
                glitch_cycle=317,
                windowed=True,
            )
            # Clock-based cycle slicing
            clk_res = psf_binary.analyze_cycles(
                psf_path, ["v(out)", "v(vdd)", "v(clk)"],
                clock_signal="v(clk)", edge="rising",
                worst_metric="peak_to_peak", worst_signal="v(out)",
                cycle_index=42, context_cycles=1, overlay_cycles=8,
            )
            self.assertEqual(clk_res["detection"]["basis"], "clock_signal")
            self.assertGreaterEqual(clk_res["cycle_count"], 498)
            self.assertEqual(clk_res["worst_cycle_index"], 317)
            self.assertEqual(clk_res["key_cycles"]["worst_case"]["representation"], "exact_cycle")
            self.assertTrue(clk_res["key_cycles"]["worst_case"]["exact"])
            self.assertEqual(clk_res["key_cycles"]["selected"]["cycle_index"], 42)
            self.assertEqual(clk_res["key_cycles"]["selected"]["cycle_span"], [41, 43])
            self.assertTrue(any(c["is_worst"] for c in clk_res["overlay"]["cycles"]))
            for tr in clk_res["overlay"]["cycles"]:
                self.assertGreaterEqual(min(tr["tau"]), -1e-12)

            # Fixed-period slicing + other worst metrics
            for metric in ("max_value", "overshoot", "min_value", "period_deviation"):
                per_res = psf_binary.analyze_cycles(
                    psf_path, ["v(out)", "v(vdd)"],
                    period=1e-8, worst_metric=metric,
                    worst_signal="v(vdd)" if metric == "min_value" else "v(out)",
                    overlay_cycles=4,
                )
                self.assertEqual(per_res["detection"]["basis"], "fixed_period")
                self.assertGreaterEqual(per_res["cycle_count"], 499)
                if metric in ("max_value", "overshoot", "min_value"):
                    self.assertEqual(per_res["worst_cycle_index"], 317)


class DirectRemoteAndSurfaceTests(unittest.TestCase):
    def _make_remote_in_place_run(self, tmp: Path) -> tuple[DirectExecutionManager, Path, str]:
        hosts = HostRegistry(tmp / "hosts")
        hosts.register({
            "schema": "spiceowl.host.v1",
            "host_id": "sim-box",
            "label": "Sim Box",
            "ssh_target": "sim-box",
            "expected_host": "sim-box.local",
            "expected_user": "edward",
            "tools": [{"kind": "spectre", "executable": "/opt/cadence/bin/spectre"}],
        })
        manager = DirectExecutionManager(tmp, hosts=hosts)
        run = manager.prepare(
            run_key="remote-big-wf",
            netlist_text="simulator lang=spectre\nV1 (out 0) vsource dc=1\n",
            host_id="sim-box",
            output_format="psfbin",
        )
        run_id = run["run_id"]
        # Create a fake remote workspace on disk (NOT inside local collected/)
        remote_home = tmp / "fake_remote_home"
        plan = json.loads((manager.path(run_id) / "plan.json").read_text(encoding="utf-8"))
        manifest = plan["manifest"]
        remote_raw = remote_home / manifest["remote_root"] / manifest["directory_name"] / "results.raw"
        remote_raw.mkdir(parents=True, exist_ok=True)
        psf_binary.generate_demo_multicycle_psf(
            remote_raw / "tran.tran.tran",
            cycles=200, samples_per_cycle=40, period_s=1e-8, glitch_cycle=123, windowed=True,
        )
        # Mark run as collected with large results.raw/tran.tran.tran skipped locally
        rec_path = manager.path(run_id) / "run.json"
        rec = json.loads(rec_path.read_text(encoding="utf-8"))
        rec["state"] = "simulator_complete_pending_measurements"
        rec["collection_phase"] = "complete"
        rec["collection"] = {
            "collection_complete": False,
            "skipped_files": [{"name": "results.raw/tran.tran.tran", "bytes": 5 * 1024**3, "reason": "file_too_large"}],
        }
        rec_path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
        return manager, remote_home, run_id

    def test_remote_in_place_waveform_driver_without_downloading_raw_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager, remote_home, run_id = self._make_remote_in_place_run(Path(tmp))
            real_popen = subprocess.Popen
            ssh_commands = []

            def fake_ssh_popen(argv, *args, **kwargs):
                # Verify command line stays well below Windows CreateProcess 32 KiB limit!
                cmd_str = argv[-1]
                ssh_commands.append(cmd_str)
                self.assertLess(len(cmd_str), 16000)
                # Extract the base64-encoded _REMOTE_WAVEFORM_DRIVER and run it locally
                # with a tiny cross-platform shim for socket.gethostname() / pwd / os.getuid().
                m_start = cmd_str.index('base64.b64decode("') + len('base64.b64decode("')
                m_end = cmd_str.index('")', m_start)
                driver_b64 = cmd_str[m_start:m_end]
                shim = (
                    "import base64, os, socket, sys, types\n"
                    f"socket.gethostname = lambda: 'sim-box.local'\n"
                    f"os.getuid = lambda: 1000\n"
                    f"fake_pwd = types.ModuleType('pwd')\n"
                    f"fake_pwd.getpwuid = lambda _u: types.SimpleNamespace(pw_name='edward', pw_dir={str(remote_home)!r})\n"
                    "sys.modules['pwd'] = fake_pwd\n"
                    f"src = base64.b64decode({driver_b64!r}).decode('utf-8')\n"
                    "ns = {'__name__': '__main__'}\n"
                    "exec(compile(src, '<remote_wf_test>', 'exec'), ns)\n"
                )
                return real_popen([sys.executable, "-c", shim], *args, **kwargs)

            # allow_remote=False MUST NOT invoke SSH
            with mock.patch.object(direct_transport.subprocess, "Popen", side_effect=fake_ssh_popen):
                local_only = manager.waveform_index(run_id, allow_remote=False)
                self.assertEqual(local_only["datasets"], [])
                self.assertEqual(len(ssh_commands), 0)
                with self.assertRaises(DomainError) as ctx:
                    manager.waveform_query(run_id, "tran.tran.tran", ["v(out)"], allow_remote=False)
                self.assertEqual(ctx.exception.code, "waveform_remote_required")

                # Remote in-place index discovers remote dataset without downloading it
                idx = manager.waveform_index(run_id, allow_remote=True)
                self.assertEqual(len(idx["datasets"]), 1)
                self.assertEqual(idx["datasets"][0]["source"], "remote_in_place")
                self.assertEqual(idx["datasets"][0]["sweep"]["count"], 8000)
                self.assertFalse((manager.path(run_id) / "collected" / "results.raw" / "tran.tran.tran").exists())

                # Remote in-place Min-Max preview query + exact zoom query
                prev = manager.waveform_query(run_id, "tran.tran.tran", ["v(out)"], max_points=300)
                self.assertEqual(prev["source"], "remote_in_place")
                self.assertEqual(prev["representation"], "downsampled_preview")

                zoom = manager.waveform_query(
                    run_id, "tran.tran.tran", ["v(out)"],
                    x_min=122.5e-8, x_max=124.0e-8, max_points=300,
                )
                self.assertEqual(zoom["source"], "remote_in_place")
                self.assertEqual(zoom["representation"], "exact_window")
                self.assertTrue(zoom["exact"])

                # Remote in-place Cycle Slicer locates glitch at cycle #123
                cyc = manager.waveform_cycles(
                    run_id, "tran.tran.tran", ["v(out)", "v(clk)"],
                    clock_signal="v(clk)", worst_metric="peak_to_peak", worst_signal="v(out)",
                )
                self.assertEqual(cyc["source"], "remote_in_place")
                self.assertEqual(cyc["worst_cycle_index"], 123)
                self.assertTrue(cyc["key_cycles"]["worst_case"]["exact"])
                self.assertEqual(len(ssh_commands), 4)

    def test_web_api_cli_and_standalone_agent_waveform_endpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            # 1. CLI: demo-waveform, waveforms, waveform-query, waveform-cycles
            def run_cli(*argv):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cli_main(["--data-dir", str(data_dir), *argv])
                self.assertEqual(rc, 0)
                return json.loads(buf.getvalue())

            demo = run_cli("direct", "demo-waveform", "--run-id", "cli-wf-demo", "--cycles", "120", "--glitch-cycle", "77")
            self.assertEqual(demo["run"]["run_id"], "cli-wf-demo")
            self.assertEqual(demo["waveforms"]["datasets"][0]["format"], "psfbin")

            listed = run_cli("direct", "waveforms", "cli-wf-demo")
            self.assertEqual(listed["datasets"][0]["sweep"]["count"], 4800)

            queried = run_cli(
                "direct", "waveform-query", "cli-wf-demo",
                "--dataset", "tran.tran.tran", "--signals", "v(out)", "--max-points", "300",
            )
            self.assertEqual(queried["representation"], "downsampled_preview")

            cycled = run_cli(
                "direct", "waveform-cycles", "cli-wf-demo",
                "--dataset", "tran.tran.tran", "--signals", "v(out)", "v(clk)",
                "--clock", "v(clk)", "--worst-signal", "v(out)", "--cycle-index", "15",
            )
            self.assertEqual(cycled["worst_cycle_index"], 77)
            self.assertEqual(cycled["key_cycles"]["selected"]["cycle_index"], 15)

            # 2. Standalone Agent spiceowl_direct_read tool
            controller = Controller(data_dir, agent_mode="standalone")
            dm = DirectExecutionManager(data_dir, hosts=controller.hosts)
            dispatcher = build_standalone_dispatcher(controller, direct_manager=dm)
            self.assertEqual(
                dispatcher.policy("spiceowl_direct_read", {"operation": "waveform_index", "arguments": {"run_id": "cli-wf-demo"}}),
                "read",
            )
            ag_idx = dispatcher.dispatch("spiceowl_direct_read", {"operation": "waveform_index", "arguments": {"run_id": "cli-wf-demo"}})
            self.assertEqual(ag_idx["datasets"][0]["dataset"], "tran.tran.tran")
            ag_q = dispatcher.dispatch("spiceowl_direct_read", {
                "operation": "waveform_query",
                "arguments": {"run_id": "cli-wf-demo", "dataset": "tran.tran.tran", "signals": ["v(out)"], "x_min": 76.5e-8, "x_max": 78.0e-8},
            })
            self.assertTrue(ag_q["exact"])
            ag_c = dispatcher.dispatch("spiceowl_direct_read", {
                "operation": "waveform_cycles",
                "arguments": {"run_id": "cli-wf-demo", "dataset": "tran.tran.tran", "signals": ["v(out)", "v(clk)"], "clock_signal": "v(clk)", "worst_signal": "v(out)"},
            })
            self.assertEqual(ag_c["worst_cycle_index"], 77)

            # 3. Web HTTP Server endpoints + CSRF protection
            server = make_server(controller, 0)
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                import threading
                th = threading.Thread(target=server.serve_forever, daemon=True)
                th.start()

                with urlopen(f"{base}/api/state", timeout=5) as resp:
                    token = json.loads(resp.read().decode("utf-8"))["csrf_token"]

                # Missing CSRF must be rejected on POST /api/direct/demo-waveform
                req_no_csrf = Request(
                    f"{base}/api/direct/demo-waveform",
                    data=b"{}",
                    headers={"Content-Type": "application/json", "Origin": base},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as err_ctx:
                    urlopen(req_no_csrf, timeout=5)
                self.assertEqual(err_ctx.exception.code, 403)

                def post_json(path, payload):
                    req = Request(
                        f"{base}{path}",
                        data=json.dumps(payload).encode("utf-8"),
                        headers={
                            "Content-Type": "application/json",
                            "Origin": base,
                            "X-SpiceOwl-Token": token,
                        },
                        method="POST",
                    )
                    with urlopen(req, timeout=10) as resp:
                        return resp.status, json.loads(resp.read().decode("utf-8"))

                status, created = post_json("/api/direct/demo-waveform", {"run_id": "web-wf-demo", "cycles": 150, "glitch_cycle": 88})
                self.assertEqual(status, 201)
                self.assertEqual(created["run"]["run_id"], "web-wf-demo")

                with urlopen(f"{base}/api/direct/runs/web-wf-demo/waveforms?allow_remote=0", timeout=5) as resp:
                    w_idx = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(w_idx["datasets"][0]["sweep"]["count"], 6000)

                st_q, w_q = post_json("/api/direct/runs/web-wf-demo/waveforms/query", {
                    "dataset": "tran.tran.tran",
                    "signals": ["v(out)", "v(clk)"],
                    "max_points": 400,
                })
                self.assertEqual(st_q, 200)
                self.assertEqual(w_q["representation"], "downsampled_preview")

                st_c, w_c = post_json("/api/direct/runs/web-wf-demo/waveforms/cycles", {
                    "dataset": "tran.tran.tran",
                    "signals": ["v(out)", "v(clk)"],
                    "clock_signal": "v(clk)",
                    "worst_metric": "peak_to_peak",
                    "worst_signal": "v(out)",
                    "cycle_index": 20,
                })
                self.assertEqual(st_c, 200)
                self.assertEqual(w_c["worst_cycle_index"], 88)
                self.assertEqual(w_c["key_cycles"]["selected"]["cycle_index"], 20)
            finally:
                server.shutdown()
                server.server_close()
                controller.close()


if __name__ == "__main__":
    unittest.main()

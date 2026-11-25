"""Offline tests for direct SSH, LSF, and Slurm execution backend.

All tests run locally with synthetic fixtures and mocked subprocess/transport
exchanges. They verify plan immutability, anti-injection validation, scheduler
parsers, atomic launch-claim fencing, verified cancellation, RuntimeMonitor
integration, five-dimensional reporting, CLI commands, and standalone Agent
tool policy without requiring SSH, Cadence Spectre, LSF, or Slurm.
"""

import base64
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import signal
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from spiceowl.adapters.executors import direct as transport
from spiceowl.cli.main import main as cli_main
from spiceowl.direct import DirectExecutionManager
from spiceowl.domain.common import DomainError, utcnow
from spiceowl.hosts import HostRegistry
from spiceowl.service import Controller
from spiceowl.standalone_agent import build_standalone_dispatcher
from spiceowl.web.server import make_server
import http.client
import threading


PROFILE = {
    "schema": "spiceowl.host.v2",
    "host_id": "sim-node-01",
    "label": "Sim Node 01",
    "ssh_target": "sim-route",
    "ssh_hostname": "sim-node-01.example.invalid",
    "expected_host": "sim-node-01",
    "expected_user": "designer",
    "tools": [
        {"kind": "spectre", "executable": "/opt/cadence/bin/spectre"},
        {"kind": "ngspice", "executable": "/usr/bin/ngspice"},
    ],
}

NETLIST = (
    "simulator lang=spectre\n"
    "model nch mos1 type=n vto=0.4\n"
    "V1 (in 0) vsource dc=1.0\n"
    "tran tran stop=10n\n"
)

SPECTRE_OK_LOG = """\
Notice from spectre during initial setup.
    N-100: Synthetic fixture notice.
spectre completes with 0 errors, 0 warnings, and 1 notice.
"""

SPECTRE_SOA_LOG = """\
WARNING (CMI-2752): XDUT.M8: Vgs has exceeded the oxide breakdown voltage.
spectre completes with 0 errors, 1 warning, and 0 notices.
"""


def _register_ready_host(data_dir, profile=PROFILE):
    registry = HostRegistry(data_dir)
    registry.add(profile)
    now = utcnow()
    probe = {
        "host_id": profile["host_id"],
        "synthetic": False,
        "scope": "connectivity_only",
        "observed_at": now,
        "transport": "reachable",
        "identity": {"verified": True, "hostname": profile["expected_host"], "username": profile["expected_user"]},
        "tools": [
            {"kind": "spectre", "status": "available", "version": "23.1.0.242.isr4", "error": None},
            {"kind": "ngspice", "status": "available", "version": "42", "error": None},
        ],
        "error": None,
    }
    resources = {
        "host_id": profile["host_id"],
        "synthetic": False,
        "scope": "host_resources",
        "observed_at": now,
        "transport": "reachable",
        "identity": {"verified": True, "hostname": profile["expected_host"], "username": profile["expected_user"]},
        "resources": {
            "cpu_count": 32,
            "cpu_busy_percent": 15.0,
            "load_1m": 2.0,
            "load_5m": 2.5,
            "memory_total_bytes": 128 * 1024**3,
            "memory_available_bytes": 100 * 1024**3,
            "home_disk_available_bytes": 50 * 1024**3,
        },
        "error": None,
    }
    registry.store.update("host", profile["host_id"], lambda rec: rec | {"last_probe": probe, "last_resources": resources})
    return registry


class DirectPlanAndParserTests(unittest.TestCase):
    def test_prepare_is_pure_and_supports_all_three_backends(self):
        with patch.object(transport.subprocess, "Popen", side_effect=AssertionError("No subprocess during prepare")):
            ssh_plan = transport.prepare(
                PROFILE, "run-ssh-01", NETLIST,
                backend="ssh_direct", simulator="spectre",
                expected_version="23.1.0.242.isr4",
                support_files={"helpers/ideal_adc.va": "module ideal_adc; endmodule\n"},
                include_dirs=["/opt/pdk/models"],
                license_environment={"CDS_LIC_FILE": "5280@lic.example.invalid"},
            )
            lsf_plan = transport.prepare(
                PROFILE, "run-lsf-01", NETLIST,
                backend="lsf", simulator="spectre",
                scheduler={"queue": "analog_long", "project": "adc_proj", "job_group": "/analog/sim"},
                remote_root="/scratch/designer/runs",
            )
            slurm_plan = transport.prepare(
                PROFILE, "run-slurm-01", NETLIST,
                backend="slurm", simulator="ngspice",
                scheduler={"partition": "compute", "account": "ic_lab", "qos": "normal", "exclusive": True},
                limits={"cpu_count": 4, "memory_bytes": 16 * 1024**3, "wall_seconds": 7200},
            )
        self.assertEqual(transport._validate(ssh_plan), ssh_plan["manifest"])
        self.assertEqual(transport._validate(lsf_plan), lsf_plan["manifest"])
        self.assertEqual(transport._validate(slurm_plan), slurm_plan["manifest"])
        self.assertIn("-I", ssh_plan["manifest"]["argv"])
        self.assertIn("/opt/pdk/models", ssh_plan["manifest"]["argv"])
        self.assertIn("helpers/ideal_adc.va", ssh_plan["manifest"]["input_hashes"])
        self.assertEqual(lsf_plan["manifest"]["scheduler"]["queue"], "analog_long")
        self.assertEqual(slurm_plan["manifest"]["scheduler"]["partition"], "compute")

    def test_invalid_inputs_and_tampered_plans_fail_closed(self):
        plan = transport.prepare(PROFILE, "run-tamper-01", NETLIST, backend="ssh_direct")
        for mutate in (
            lambda p: p["manifest"]["limits"].update(cpu_count=8),
            lambda p: p["manifest"]["argv"].append(";id"),
            lambda p: p["manifest"].update(directory_name="../escape"),
            lambda p: p["files"].update({"extra.txt": "AAAA"}),
        ):
            copy_plan = deepcopy(plan)
            mutate(copy_plan)
            with self.assertRaises(DomainError):
                transport._validate(copy_plan)

        # Scheduler option validation and injection rejection
        with self.assertRaises(DomainError):
            transport.prepare(PROFILE, "bad-lsf", NETLIST, backend="lsf", scheduler={})
        with self.assertRaises(DomainError):
            transport.prepare(PROFILE, "bad-lsf-q", NETLIST, backend="lsf", scheduler={"queue": "q;rm -rf /"})
        with self.assertRaises(DomainError):
            transport.prepare(PROFILE, "bad-slurm", NETLIST, backend="slurm", scheduler={})
        with self.assertRaises(DomainError):
            transport.prepare(PROFILE, "bad-ssh-sched", NETLIST, backend="ssh_direct", scheduler={"queue": "q"})
        with self.assertRaises(DomainError):
            transport.prepare(PROFILE, "bad-support", NETLIST, support_files={"../escape.scs": "x"})
        with self.assertRaises(DomainError):
            transport.prepare(PROFILE, "bad-reserved", NETLIST, support_files={"manifest.json": "{}"})
        with self.assertRaises(DomainError):
            transport.prepare(PROFILE, "bad-lic", NETLIST, license_environment={"LD_PRELOAD": "/tmp/evil.so"})
        with self.assertRaises(DomainError):
            transport.prepare(PROFILE, "bad-inc", NETLIST, include_dirs=["/opt/pdk/../etc"])

    def test_lsf_and_slurm_parsers(self):
        self.assertEqual(transport.parse_bsub_output("Job <987654> is submitted to queue <analog_long>.\n"), "987654")
        self.assertIsNone(transport.parse_bsub_output("Submission rejected"))
        self.assertEqual(
            transport.parse_bjobs_output("987654 RUN - compute04\n", "987654"),
            {"job_id": "987654", "raw_state": "RUN", "exit_code": None, "exec_host": "compute04"},
        )
        self.assertEqual(
            transport.parse_bjobs_output("987654 EXIT 137 compute04\n", "987654"),
            {"job_id": "987654", "raw_state": "EXIT", "exit_code": 137, "exec_host": "compute04"},
        )
        self.assertEqual(transport.parse_sbatch_output("123456;hpc_cluster\n"), {"job_id": "123456", "cluster": "hpc_cluster"})
        self.assertEqual(transport.parse_sbatch_output("123456\n"), {"job_id": "123456", "cluster": None})
        self.assertEqual(
            transport.parse_squeue_output("123456|PENDING||Resources\n", "123456"),
            {"job_id": "123456", "raw_state": "PENDING", "nodelist": None, "reason": "Resources"},
        )
        self.assertEqual(
            transport.parse_sacct_output("123456|OUT_OF_MEMORY|0:9|0:0|node07\n123456.batch|OUT_OF_MEMORY|0:9|0:0|node07\n", "123456"),
            {"job_id": "123456", "raw_state": "OUT_OF_MEMORY", "exit_code": 0, "signal": 9, "nodelist": "node07"},
        )
        self.assertEqual(
            transport.parse_sacct_output("123456|CANCELLED by 1000|0:15|0:0|node07\n", "123456")["raw_state"],
            "CANCELLED",
        )


@unittest.skipIf(os.name == "nt", "Remote Linux worker script requires POSIX modules (pwd/resource)")
class RemoteStateMachineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ns = {"__name__": "direct_remote_fixture"}
        exec(compile(transport._REMOTE_SOURCE, "<direct-remote-fixture>", "exec"), self.ns)
        self.ns["home_path"] = lambda: self.temp.name
        self.ns["identity"] = lambda _: {"hostname": "sim-node-01", "username": "designer"}
        self.ns["requirements"] = lambda _: None
        self.ns["bounded_version"] = lambda *_: None
        self.ns["signal"] = SimpleNamespace(
            signal=lambda *a: None, SIGHUP=signal.SIGHUP, SIG_IGN=signal.SIG_IGN,
            SIGTERM=signal.SIGTERM, SIGKILL=signal.SIGKILL,
        )

    def tearDown(self):
        self.ns["DEVNULL"].close()
        self.temp.cleanup()

    def test_lsf_lifecycle_launch_status_cancel_and_collect(self):
        plan = transport.prepare(
            PROFILE, "lsf-case-1", NETLIST,
            backend="lsf", simulator="spectre",
            scheduler={"queue": "analog", "project": "p1"},
            support_files={"sub/helper.scs": "// helper\n"},
        )
        req = {"manifest": plan["manifest"], "files": plan["files"]}
        staged = self.ns["stage"](req)
        self.assertEqual(staged["state"], "staged")
        run_path = Path(staged["remote_directory"])
        self.assertTrue((run_path / "sub" / "helper.scs").is_file())

        bsub_calls = []
        bjobs_replies = [
            (0, "445566 PEND - -\n", ""),
            (0, "445566 RUN - node12\n", ""),
            (0, "445566 RUN - node12\n", ""),
            (0, "445566 DONE 0 node12\n", ""),
        ]
        bkill_calls = []

        self.ns["resolve_command"] = lambda spec: "/opt/lsf/bin/" + Path(spec).name

        def fake_run(argv, timeout_seconds=10, cwd=None):
            exe = Path(argv[0]).name
            if exe == "bsub":
                bsub_calls.append(argv)
                return 0, "Job <445566> is submitted to queue <analog>.\n", ""
            if exe == "bjobs":
                return bjobs_replies.pop(0)
            if exe == "bkill":
                bkill_calls.append(argv)
                return 0, "Job <445566> is being terminated\n", ""
            raise AssertionError(f"Unexpected command {argv}")

        self.ns["run_bounded"] = fake_run

        launched = self.ns["launch"](req)
        self.assertEqual(len(bsub_calls), 1)
        self.assertIn("-q", bsub_calls[0])
        self.assertIn("analog", bsub_calls[0])
        self.assertEqual(launched["state"], "queued")
        self.assertEqual(launched["scheduler"]["job_id"], "445566")

        # Repeated launch never invokes bsub a second time
        second = self.ns["launch"](req)
        self.assertEqual(len(bsub_calls), 1)
        self.assertEqual(second["state"], "running")

        # Verified cancel calls bkill with exact job_id
        cancel_res = self.ns["cancel_run"]({"manifest": plan["manifest"], "action_key": "cancel-lsf-1"})
        self.assertEqual(cancel_res["outcome"], "confirmed")
        self.assertEqual(bkill_calls, [["/opt/lsf/bin/bkill", "445566"]])

        # Final status observes DONE -> writes terminal status.json
        # Clear cancel marker so DONE reflects normal completion for collection check
        (run_path / "cancel-requested.json").unlink()
        done = self.ns["snapshot"](str(run_path), plan["manifest"])
        self.assertEqual(done["state"], "completed")
        self.assertTrue(done["terminal"])

        (run_path / "spectre.log").write_text(SPECTRE_OK_LOG, encoding="utf-8")
        (run_path / "results.raw").mkdir()
        (run_path / "results.raw" / "tran.tran").write_text("HEADER\n", encoding="utf-8")
        collected = self.ns["collect"]({"manifest": plan["manifest"]})
        transport._verify_collection(plan, collected)
        self.assertTrue(collected["collection_complete"])
        self.assertTrue(collected["raw_present"])

    def test_slurm_oom_evidence_and_transient_empty_queue_unknown(self):
        plan = transport.prepare(
            PROFILE, "slurm-case-1", NETLIST,
            backend="slurm", simulator="spectre",
            scheduler={"partition": "sim", "account": "analog"},
        )
        req = {"manifest": plan["manifest"], "files": plan["files"]}
        staged = self.ns["stage"](req)
        run_path = Path(staged["remote_directory"])

        sbatch_calls = []
        phase = {"step": 0}
        self.ns["resolve_command"] = lambda spec: "/usr/bin/" + Path(spec).name

        def fake_run(argv, timeout_seconds=10, cwd=None):
            exe = Path(argv[0]).name
            if exe == "sbatch":
                sbatch_calls.append(argv)
                return 0, "778899;clusterA\n", ""
            if exe == "squeue":
                if phase["step"] == 0:
                    return 0, "778899|PENDING||Priority\n", ""
                return 0, "", ""
            if exe == "sacct":
                if phase["step"] == 1:
                    # Transient accounting lag: empty output -> must remain outcome_unknown!
                    return 0, "", ""
                return 0, "778899|OUT_OF_MEMORY|0:9|0:0|node99\n", ""
            raise AssertionError(f"Unexpected command {argv}")

        self.ns["run_bounded"] = fake_run

        launched = self.ns["launch"](req)
        self.assertEqual(len(sbatch_calls), 1)
        self.assertIn("--parsable", sbatch_calls[0])
        self.assertEqual(launched["state"], "queued")
        self.assertEqual(launched["scheduler"]["job_id"], "778899")
        self.assertEqual(launched["scheduler"]["cluster"], "clusterA")

        # Step 1: transient empty squeue + empty sacct -> outcome_unknown, NOT failed!
        phase["step"] = 1
        unknown = self.ns["snapshot"](str(run_path), plan["manifest"])
        self.assertEqual(unknown["state"], "outcome_unknown")
        self.assertFalse(unknown["terminal"])

        # Step 2: sacct confirms OUT_OF_MEMORY -> failed with run-bound oom_evidence
        phase["step"] = 2
        oom = self.ns["snapshot"](str(run_path), plan["manifest"])
        self.assertEqual(oom["state"], "failed")
        self.assertTrue(oom["terminal"])
        self.assertEqual(oom["result"]["oom_evidence"], {"source": "slurm_sacct", "run_bound": True, "oom_kill": True})

    def test_ssh_direct_cancel_signals_only_verified_pid_and_collection_skips_oversized_raw(self):
        plan = transport.prepare(PROFILE, "ssh-case-1", NETLIST, backend="ssh_direct")
        req = {"manifest": plan["manifest"], "files": plan["files"]}
        staged = self.ns["stage"](req)
        run_path = Path(staged["remote_directory"])
        os.mkdir(run_path / "launch-claim", 0o700)
        self.ns["write_atomic"](str(run_path / "wrapper.json"), {"pid": 4242, "start_ticks": 999, "boot_id": "boot-1"})
        self.ns["write_atomic"](str(run_path / "supervisor.json"), {"pid": 4243, "start_ticks": 1000, "boot_id": "boot-1"})

        # Mismatched start_ticks (recycled PID) must NEVER be signaled
        killed = []
        self.ns["proc_identity"] = lambda pid: {"pid": pid, "start_ticks": 5555, "boot_id": "boot-1", "state": "S"}
        self.ns["os"] = SimpleNamespace(**{k: getattr(os, k) for k in dir(os) if not k.startswith("__")})
        self.ns["os"].killpg = lambda pid, sig: killed.append((pid, sig))
        res_recycled = self.ns["cancel_run"]({"manifest": plan["manifest"], "action_key": "cancel-1"})
        self.assertEqual(res_recycled["outcome"], "unknown")
        self.assertEqual(killed, [])

        # Matching identity signals verified process groups
        self.ns["proc_identity"] = lambda pid: {"pid": pid, "start_ticks": 999 if pid == 4242 else 1000, "boot_id": "boot-1", "state": "S"}
        res_ok = self.ns["cancel_run"]({"manifest": plan["manifest"], "action_key": "cancel-2"})
        self.assertEqual(res_ok["outcome"], "confirmed")
        self.assertEqual(len(killed), 2)

        # Oversized raw file is skipped cleanly without starving spectre.log
        self.ns["write_atomic"](str(run_path / "status.json"), {
            "manifest_sha256": plan["manifest_sha256"], "state": "completed", "terminal": True, "exit_code": 0,
        })
        (run_path / "spectre.log").write_text(SPECTRE_OK_LOG, encoding="utf-8")
        (run_path / "results.raw").mkdir()
        (run_path / "results.raw" / "huge.tran").write_bytes(b"X" * 100_000)
        collected = self.ns["collect"]({"manifest": plan["manifest"], "max_collection_bytes": 65536})
        transport._verify_collection(plan, collected, max_collection_bytes=65536)
        self.assertFalse(collected["collection_complete"])
        self.assertEqual(collected["skipped_files"][0]["name"], "results.raw/huge.tran")
        self.assertIn("spectre.log", {item["name"] for item in collected["files"]})


class DirectManagerMonitorAndCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name) / "state"
        _register_ready_host(self.data_dir)
        self.manager = DirectExecutionManager(self.data_dir)

    def tearDown(self):
        self.temp.cleanup()

    def test_pool_prepare_unknown_launch_reconcile_monitor_and_five_dim_report(self):
        run = self.manager.prepare(
            run_key="direct-inv-01",
            netlist_text=NETLIST,
            host_ids=["sim-node-01"],
            backend="slurm",
            simulator="spectre",
            scheduler={"partition": "analog"},
        )
        self.assertEqual(run["state"], "prepared")
        self.assertEqual(run["host_id"], "sim-node-01")
        self.assertEqual(run["expected_version"], "23.1.0.242.isr4")

        # Duplicate prepare with identical inputs is idempotent; changed inputs conflict
        self.assertEqual(
            self.manager.prepare(
                run_key="direct-inv-01", netlist_text=NETLIST,
                host_ids=["sim-node-01"], backend="slurm", simulator="spectre",
                scheduler={"partition": "analog"},
            )["manifest_sha256"],
            run["manifest_sha256"],
        )
        with self.assertRaises(DomainError) as ctx:
            self.manager.prepare(
                run_key="direct-inv-01", netlist_text=NETLIST + "// changed\n",
                host_id="sim-node-01", backend="slurm", simulator="spectre",
                scheduler={"partition": "analog"},
            )
        self.assertEqual(ctx.exception.code, "conflict")

        plan = self.manager._load_plan("direct-inv-01")
        msha = plan["manifest_sha256"]

        # Stage succeeds
        with patch.object(transport, "stage", return_value={"manifest_sha256": msha, "state": "staged", "terminal": False, "remote_directory": "/remote/run"}):
            staged = self.manager.stage("direct-inv-01")
        self.assertEqual(staged["state"], "staged")

        # Launch loses SSH reply -> launch_outcome_unknown; second launch is blocked!
        with patch.object(transport, "launch", return_value={
            "operation": "launch", "state": "outcome_unknown", "manifest_sha256": msha,
            "error": {"code": "ssh_connection_lost", "message": "Lost connection"},
        }):
            launched = self.manager.launch("direct-inv-01")
        self.assertEqual(launched["state"], "launch_outcome_unknown")
        self.assertTrue(launched["dispatch_claimed"])
        with self.assertRaises(DomainError) as ctx2:
            self.manager.launch("direct-inv-01")
        self.assertEqual(ctx2.exception.code, "launch_already_claimed")

        # Submission lookup finds original record without replay
        found = self.manager.lookup_submission("direct-inv-01")
        self.assertTrue(found["found"])
        self.assertEqual(found["run_id"], "direct-inv-01")

        # RuntimeMonitor supervises the run: disconnect -> running -> terminal + auto collect!
        monitor = self.manager.monitor(interval_seconds=1, max_backoff_seconds=4)
        monitor.watch("direct-inv-01", allow_collect=True)

        # Poll 1: transient SSH disconnect -> backoff, never failed
        with patch.object(transport, "status", return_value={
            "operation": "status", "state": "unobserved", "manifest_sha256": msha,
            "error": {"code": "ssh_connection_lost", "message": "Lost"},
        }):
            obs1 = monitor.poll_once("direct-inv-01")
        self.assertEqual(obs1["observation"]["state"], "disconnected")
        self.assertEqual(obs1["watch_state"], "armed")

        # Poll 2: reconnected, running
        with patch.object(transport, "status", return_value={
            "manifest_sha256": msha, "state": "running", "terminal": False,
            "scheduler": {"backend": "slurm", "job_id": "551122"},
        }):
            obs2 = monitor.poll_once("direct-inv-01")
        self.assertEqual(obs2["observation"]["state"], "connected")
        self.assertEqual(obs2["execution"]["state"], "running_observed")

        # Poll 3: terminal completed -> allow_collect triggers collect + 5-dim report!
        terminal_receipt = {
            "manifest_sha256": msha, "state": "completed", "terminal": True,
            "result": {"manifest_sha256": msha, "state": "completed", "terminal": True, "exit_code": 0},
        }
        status_bytes = json.dumps(terminal_receipt["result"], sort_keys=True, separators=(",", ":")).encode("ascii")
        manifest_bytes = transport._json(plan["manifest"])
        input_bytes = base64.b64decode(plan["files"]["input.scs"])
        log_bytes = SPECTRE_SOA_LOG.encode("utf-8")
        collect_payload = {
            "receipt": terminal_receipt,
            "manifest_sha256": msha,
            "files": [
                {"name": "input.scs", "bytes": len(input_bytes), "sha256": transport._sha(input_bytes), "data_base64": base64.b64encode(input_bytes).decode("ascii")},
                {"name": "manifest.json", "bytes": len(manifest_bytes), "sha256": transport._sha(manifest_bytes), "data_base64": base64.b64encode(manifest_bytes).decode("ascii")},
                {"name": "status.json", "bytes": len(status_bytes), "sha256": transport._sha(status_bytes), "data_base64": base64.b64encode(status_bytes).decode("ascii")},
                {"name": "spectre.log", "bytes": len(log_bytes), "sha256": transport._sha(log_bytes), "data_base64": base64.b64encode(log_bytes).decode("ascii")},
            ],
            "skipped_files": [],
            "collection_complete": True,
            "total_bytes": len(input_bytes) + len(manifest_bytes) + len(status_bytes) + len(log_bytes),
            "raw_present": False,
        }
        with patch.object(transport, "status", return_value=terminal_receipt), \
             patch.object(transport, "collect", return_value=collect_payload):
            obs3 = monitor.poll_once("direct-inv-01")

        self.assertEqual(obs3["watch_state"], "terminal")
        self.assertEqual(obs3["collection"]["state"], "complete")

        # Five-dimensional report preserves execution completed AND SOA violation separately!
        report = self.manager.report("direct-inv-01")
        self.assertEqual(report["execution_status"]["status"], "completed")
        self.assertEqual(report["operating_qualification"]["status"], "violations_detected")
        self.assertEqual(report["measurement_validity"]["status"], "unknown")
        self.assertEqual(report["functional_performance"]["status"], "unknown")

    def test_cli_direct_prepare_inspect_lookup_and_standalone_agent_tools(self):
        netlist_file = Path(self.temp.name) / "tb.scs"
        netlist_file.write_text(NETLIST, encoding="utf-8")

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli_main([
                "--data-dir", str(self.data_dir),
                "direct", "prepare",
                "--run-key", "cli-lsf-01",
                "--netlist", str(netlist_file),
                "--host", "sim-node-01",
                "--backend", "lsf",
                "--queue", "analog_short",
                "--cpus", "2",
                "--memory-gib", "8",
            ])
        self.assertEqual(rc, 0)
        created = json.loads(buf.getvalue())
        self.assertEqual(created["run_id"], "cli-lsf-01")
        self.assertEqual(created["backend"], "lsf")

        # CLI lookup
        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            rc2 = cli_main(["--data-dir", str(self.data_dir), "direct", "lookup", "--key", "cli-lsf-01"])
        self.assertEqual(rc2, 0)
        self.assertTrue(json.loads(buf2.getvalue())["found"])

        # Standalone Agent dispatcher exposes direct tools with strict read vs approval policies
        with Controller(self.data_dir, agent_mode="standalone") as app:
            dispatcher = build_standalone_dispatcher(app, direct_manager=self.manager)
            self.assertEqual(dispatcher.policy("spiceowl_direct_read", {"operation": "list", "arguments": {}}), "read")
            self.assertEqual(dispatcher.policy("spiceowl_direct_control", {"operation": "launch", "arguments": {"run_id": "cli-lsf-01"}}), "approval")
            listed = dispatcher.dispatch("spiceowl_direct_read", {"operation": "list", "arguments": {}})
            self.assertEqual([r["run_id"] for r in listed["runs"]], ["cli-lsf-01"])
            inspected = dispatcher.dispatch("spiceowl_direct_read", {"operation": "inspect", "arguments": {"run_id": "cli-lsf-01"}})
            self.assertEqual(inspected["backend"], "lsf")


class DirectWebApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name) / "state"
        _register_ready_host(self.data_dir)
        self.app = Controller(self.data_dir, agent_mode="standalone")
        self.server = make_server(self.app, 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        status, state = self._req("GET", "/api/state")
        self.assertEqual(status, 200)
        self.token = state["csrf_token"]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.close()
        self.temp.cleanup()

    def _req(self, method, path, body=None, *, csrf=True, origin=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": f"127.0.0.1:{self.port}"}
        if origin is not None:
            headers["Origin"] = origin
        raw = None
        if method != "GET":
            raw = json.dumps({} if body is None else body).encode("utf-8")
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(raw))
            if csrf:
                headers["X-SpiceOwl-Token"] = self.token
        conn.request(method, path, body=raw, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, json.loads(data.decode("utf-8")) if resp.getheader("Content-Type", "").startswith("application/json") else data.decode("utf-8")

    def test_web_direct_csrf_and_full_lifecycle(self):
        # UI serves direct nav entry
        status, html = self._req("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn('data-nav="direct"', html)

        # Missing CSRF and foreign origin are rejected
        payload = {
            "run_key": "web-slurm-01",
            "netlist_text": NETLIST,
            "host_id": "sim-node-01",
            "backend": "slurm",
            "simulator": "spectre",
            "cpus": 2,
            "memory_gib": 8,
            "wall_seconds": 600,
            "scheduler": {"partition": "analog"},
        }
        self.assertEqual(self._req("POST", "/api/direct/prepare", payload, csrf=False)[0], 403)
        self.assertEqual(self._req("POST", "/api/direct/prepare", payload, origin="https://evil.invalid")[0], 403)

        # Prepare -> list -> lookup
        st, created = self._req("POST", "/api/direct/prepare", payload)
        self.assertEqual(st, 201)
        run = created["run"]
        msha = run["manifest_sha256"]
        self.assertEqual(run["run_id"], "web-slurm-01")
        self.assertEqual(run["state"], "prepared")

        st, listed = self._req("GET", "/api/direct/runs")
        self.assertEqual(st, 200)
        self.assertEqual([r["run_id"] for r in listed["runs"]], ["web-slurm-01"])

        st, lookup = self._req("GET", "/api/direct/submission?key=web-slurm-01")
        self.assertEqual(st, 200)
        self.assertTrue(lookup["found"])

        # Stage -> Launch -> Status -> Watch/Unwatch -> Cancel -> Collect + Report
        with patch.object(transport, "stage", return_value={"manifest_sha256": msha, "state": "staged", "terminal": False, "remote_directory": "/r/1"}):
            st, staged = self._req("POST", "/api/direct/runs/web-slurm-01/stage", {})
        self.assertEqual(st, 202)
        self.assertEqual(staged["run"]["state"], "staged")

        with patch.object(transport, "launch", return_value={"manifest_sha256": msha, "state": "queued", "terminal": False, "scheduler": {"backend": "slurm", "job_id": "334455"}}):
            st, launched = self._req("POST", "/api/direct/runs/web-slurm-01/launch", {"recheck_resources": False})
        self.assertEqual(st, 202)
        self.assertEqual(launched["run"]["scheduler"]["job_id"], "334455")

        with patch.object(transport, "status", return_value={"manifest_sha256": msha, "state": "running", "terminal": False}):
            st, watched = self._req("POST", "/api/direct/runs/web-slurm-01/watch", {"allow_collect": False})
            self.assertEqual(st, 202)
            self.assertEqual(watched["monitor"]["watch_state"], "armed")
            st, unwatched = self._req("POST", "/api/direct/runs/web-slurm-01/unwatch", {})
            self.assertEqual(st, 200)
            self.assertEqual(unwatched["monitor"]["watch_state"], "stopped")

        with patch.object(transport, "cancel", return_value={"manifest_sha256": msha, "outcome": "confirmed", "state": "cancelling", "job_id": "334455"}):
            st, cancelled = self._req("POST", "/api/direct/runs/web-slurm-01/cancel", {"action_key": "web-cancel-1"})
        self.assertEqual(st, 202)
        self.assertEqual(cancelled["cancel"]["outcome"], "confirmed")

        # Collect terminal run and verify 5-dim report returned
        manager = DirectExecutionManager(self.data_dir)
        plan = manager._load_plan("web-slurm-01")
        term = {"manifest_sha256": msha, "state": "completed", "terminal": True,
                "result": {"manifest_sha256": msha, "state": "completed", "terminal": True, "exit_code": 0}}
        status_b = json.dumps(term["result"], sort_keys=True, separators=(",", ":")).encode("ascii")
        manifest_b = transport._json(plan["manifest"])
        input_b = base64.b64decode(plan["files"]["input.scs"])
        log_b = SPECTRE_SOA_LOG.encode("utf-8")
        collect_data = {
            "receipt": term, "manifest_sha256": msha, "skipped_files": [], "collection_complete": True, "raw_present": False,
            "total_bytes": len(input_b) + len(manifest_b) + len(status_b) + len(log_b),
            "files": [
                {"name": "input.scs", "bytes": len(input_b), "sha256": transport._sha(input_b), "data_base64": base64.b64encode(input_b).decode("ascii")},
                {"name": "manifest.json", "bytes": len(manifest_b), "sha256": transport._sha(manifest_b), "data_base64": base64.b64encode(manifest_b).decode("ascii")},
                {"name": "status.json", "bytes": len(status_b), "sha256": transport._sha(status_b), "data_base64": base64.b64encode(status_b).decode("ascii")},
                {"name": "spectre.log", "bytes": len(log_b), "sha256": transport._sha(log_b), "data_base64": base64.b64encode(log_b).decode("ascii")},
            ],
        }
        with patch.object(transport, "collect", return_value=collect_data):
            st, collected = self._req("POST", "/api/direct/runs/web-slurm-01/collect", {})
        self.assertEqual(st, 202)
        self.assertEqual(collected["run"]["collection_phase"], "complete")
        self.assertEqual(collected["report"]["execution_status"]["status"], "completed")
        self.assertEqual(collected["report"]["operating_qualification"]["status"], "violations_detected")


if __name__ == "__main__":
    unittest.main()

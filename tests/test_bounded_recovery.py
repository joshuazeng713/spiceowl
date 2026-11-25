"""Offline tests for M4 bounded recovery, watch resume, and searchable reports."""

import base64
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from spiceowl.adapters.executors import direct as transport
from spiceowl.cli.main import main as cli_main
from spiceowl.direct import DirectExecutionManager, DirectRecoverySupervisor
from spiceowl.domain.common import DomainError, utcnow
from spiceowl.hosts import HostRegistry
from spiceowl.recovery.bounded import (
    classify_failure,
    evaluate_bounded_recovery,
    validate_recovery_policy,
)
from spiceowl.service import Controller
from spiceowl.web.server import make_server


SMALL_HOST = {
    "schema": "spiceowl.host.v2",
    "host_id": "sim-small",
    "label": "Small Node 16G",
    "ssh_target": "sim-route",
    "ssh_hostname": "sim-small.example.invalid",
    "expected_host": "sim-small",
    "expected_user": "designer",
    "tools": [{"kind": "spectre", "executable": "/opt/cadence/bin/spectre"}],
}

LARGE_HOST = {
    "schema": "spiceowl.host.v2",
    "host_id": "sim-large",
    "label": "Large Node 128G",
    "ssh_target": "sim-route",
    "ssh_hostname": "sim-large.example.invalid",
    "expected_host": "sim-large",
    "expected_user": "designer",
    "tools": [{"kind": "spectre", "executable": "/opt/cadence/bin/spectre"}],
}

NETLIST = (
    "simulator lang=spectre\n"
    "model nch mos1 type=n vto=0.4\n"
    "V1 (in 0) vsource dc=1.0\n"
    "tran tran stop=20n\n"
)

OOM_LOG = """\
ERROR: out-of-memory while allocating matrix factor storage.
spectre completes with 1 error, 0 warnings, and 0 notices.
"""

LICENSE_LOG = """\
ERROR: License checkout failed for Virtuoso_Spectre_Circuit_Simulator.
spectre completes with 1 error, 0 warnings, and 0 notices.
"""

SYNTAX_LOG = """\
ERROR (SFE-874): "input.scs" 4: Unexpected token.
spectre completes with 1 error, 0 warnings, and 0 notices.
"""

OK_SOA_LOG = """\
WARNING (CMI-2752): XDUT.M8: Vgs has exceeded the oxide breakdown voltage.
spectre completes with 0 errors, 1 warning, and 0 notices.
"""


def _register_host(data_dir, profile, *, mem_total_gib, mem_avail_gib):
    registry = HostRegistry(data_dir)
    registry.add(profile)
    now = utcnow()
    probe = {
        "host_id": profile["host_id"], "synthetic": False, "scope": "connectivity_only",
        "observed_at": now, "transport": "reachable",
        "identity": {"verified": True, "hostname": profile["expected_host"], "username": profile["expected_user"]},
        "tools": [{"kind": "spectre", "status": "available", "version": "23.1.0.242.isr4", "error": None}],
        "error": None,
    }
    resources = {
        "host_id": profile["host_id"], "synthetic": False, "scope": "host_resources",
        "observed_at": now, "transport": "reachable",
        "identity": {"verified": True, "hostname": profile["expected_host"], "username": profile["expected_user"]},
        "resources": {
            "cpu_count": 32, "cpu_busy_percent": 10.0, "load_1m": 1.0, "load_5m": 1.5,
            "memory_total_bytes": mem_total_gib * 1024**3,
            "memory_available_bytes": mem_avail_gib * 1024**3,
            "home_disk_available_bytes": 100 * 1024**3,
        },
        "error": None,
    }
    registry.store.update("host", profile["host_id"], lambda rec: rec | {"last_probe": probe, "last_resources": resources})
    return registry


def _collect_payload(manager, run_id, *, state, exit_code, log_text, oom_evidence=None):
    plan = manager._load_plan(run_id)
    msha = plan["manifest_sha256"]
    result = {"manifest_sha256": msha, "state": state, "terminal": True, "exit_code": exit_code}
    if oom_evidence is not None:
        result["oom_evidence"] = oom_evidence
    receipt = {"manifest_sha256": msha, "state": state, "terminal": True, "result": result}
    status_b = json.dumps(result, sort_keys=True, separators=(",", ":")).encode("ascii")
    manifest_b = transport._json(plan["manifest"])
    input_b = base64.b64decode(plan["files"]["input.scs"])
    log_b = log_text.encode("utf-8")
    files = []
    for name, raw in (("input.scs", input_b), ("manifest.json", manifest_b),
                      ("status.json", status_b), ("spectre.log", log_b)):
        files.append({
            "name": name, "bytes": len(raw), "sha256": transport._sha(raw),
            "data_base64": base64.b64encode(raw).decode("ascii"),
        })
    return {
        "receipt": receipt, "manifest_sha256": msha, "files": files,
        "skipped_files": [], "collection_complete": True,
        "total_bytes": sum(item["bytes"] for item in files), "raw_present": False,
    }


class BoundedRecoveryPolicyTests(unittest.TestCase):
    def test_policy_validation_bounds(self):
        default = validate_recovery_policy(None)
        self.assertFalse(default["enabled"])
        with self.assertRaises(DomainError):
            validate_recovery_policy({"max_recovery_attempts": 99})
        with self.assertRaises(DomainError):
            validate_recovery_policy({"oom_memory_multiplier": 10.0})
        with self.assertRaises(DomainError):
            validate_recovery_policy({"unknown_knob": True})

    def test_scientific_and_operational_boundaries(self):
        base_run = {
            "run_id": "r1", "root_run_id": "r1", "host_id": "sim-small", "backend": "slurm",
            "simulator": "spectre", "attempt_number": 1, "created_at": utcnow(),
            "limits": {"cpu_count": 2, "memory_bytes": 8 * 1024**3},
        }
        policy = validate_recovery_policy({"enabled": True, "max_recovery_attempts": 2, "max_memory_bytes": 32 * 1024**3})

        # Completed execution (even with SOA warning) is NEVER retried
        completed = base_run | {"state": "simulator_complete_pending_measurements", "raw_remote_state": "completed"}
        dec = evaluate_bounded_recovery(completed, None, policy=policy, lineage_runs=[completed])
        self.assertEqual(dec["result"], "deny")
        self.assertEqual(dec["reason_code"], "completed")

        # Uncertain / active run is NEVER retried
        unknown = base_run | {"state": "launch_outcome_unknown", "raw_remote_state": "outcome_unknown"}
        dec_u = evaluate_bounded_recovery(unknown, None, policy=policy, lineage_runs=[unknown])
        self.assertEqual(dec_u["result"], "deny")
        self.assertEqual(dec_u["reason_code"], "not_terminal")

        # Unconfirmed SIGKILL (suspected OOM without run-bound evidence) is NEVER guessed as OOM
        sigkill = base_run | {"state": "simulator_failed", "raw_remote_state": "failed", "termination_reason": "kill_signal_cause_unknown"}
        self.assertEqual(classify_failure(sigkill, None)["failure_class"], "suspected_oom_unconfirmed")
        dec_k = evaluate_bounded_recovery(sigkill, None, policy=policy, lineage_runs=[sigkill])
        self.assertEqual(dec_k["result"], "deny")

        # Syntax error blocks recovery
        failed = base_run | {"state": "simulator_failed", "raw_remote_state": "failed"}
        syntax_rep = {"diagnostics": {"diagnostics": [{"category": "syntax", "code": "SFE-874", "evidence_line_numbers": [1]}]}}
        dec_s = evaluate_bounded_recovery(failed, syntax_rep, policy=policy, lineage_runs=[failed])
        self.assertEqual(dec_s["result"], "deny")
        self.assertEqual(dec_s["reason_code"], "design_or_numerical_error")

        # Elapsed budget exhaustion denies even confirmed OOM
        oom_run = failed | {"oom_evidence": {"source": "slurm_sacct", "run_bound": True, "oom_kill": True}}
        future = datetime.now(timezone.utc) + timedelta(seconds=policy["max_elapsed_seconds"] + 10)
        dec_t = evaluate_bounded_recovery(oom_run, None, policy=policy, lineage_runs=[oom_run], now=future)
        self.assertEqual(dec_t["result"], "deny")
        self.assertEqual(dec_t["reason_code"], "elapsed_budget_exhausted")


class BoundedRecoveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name) / "state"
        _register_host(self.data_dir, SMALL_HOST, mem_total_gib=16, mem_avail_gib=12)
        _register_host(self.data_dir, LARGE_HOST, mem_total_gib=128, mem_avail_gib=100)
        self.manager = DirectExecutionManager(self.data_dir)

    def tearDown(self):
        self.temp.cleanup()

    def test_oom_scales_memory_relocates_host_and_writes_searchable_recovery_report(self):
        # Attempt 1 on sim-small (8 GiB) with bounded recovery enabled (cap 32 GiB, relocate allowed)
        run1 = self.manager.prepare(
            run_key="oom-inv-01",
            netlist_text=NETLIST,
            host_id="sim-small",
            backend="ssh_direct",
            simulator="spectre",
            limits={"cpu_count": 2, "memory_bytes": 8 * 1024**3, "wall_seconds": 600},
            recovery_policy={
                "enabled": True,
                "max_recovery_attempts": 2,
                "oom_memory_multiplier": 2.0,
                "max_memory_bytes": 32 * 1024**3,
                "allow_host_relocation": True,
                "candidate_host_ids": ["sim-small", "sim-large"],
            },
        )
        msha1 = run1["manifest_sha256"]

        # Simulate attempt 1 failing with explicit Spectre OOM log and being collected
        with patch.object(transport, "collect", return_value=_collect_payload(self.manager, "oom-inv-01", state="failed", exit_code=1, log_text=OOM_LOG)):
            self.manager.collect("oom-inv-01")

        # Execute bounded recovery: 8 GiB -> 16 GiB cannot fit on sim-small (12 GiB avail),
        # so it relocates cleanly to sim-large and spawns oom-inv-01-r1!
        with patch.object(transport, "stage", side_effect=lambda p: {"manifest_sha256": p["manifest_sha256"], "state": "staged", "terminal": False, "remote_directory": "/r/2"}), \
             patch.object(transport, "launch", side_effect=lambda p: {"manifest_sha256": p["manifest_sha256"], "state": "running", "terminal": False, "accepted": {"dispatch_claimed": True}}), \
             patch.object(self.manager.hosts, "resources", return_value=self.manager.hosts.inspect("sim-large")["last_resources"]):
            outcome = self.manager.evaluate_and_recover("oom-inv-01", execute=True)

        self.assertEqual(outcome["decision"]["result"], "allow")
        self.assertEqual(outcome["decision"]["action"], "relocate_oom_to_larger_host")
        child = outcome["child_run"]
        self.assertEqual(child["run_id"], "oom-inv-01-r1")
        self.assertEqual(child["host_id"], "sim-large")
        self.assertEqual(child["limits"]["memory_bytes"], 16 * 1024**3)
        self.assertEqual(child["parent_run_id"], "oom-inv-01")
        self.assertEqual(child["root_run_id"], "oom-inv-01")
        self.assertEqual(child["attempt_number"], 2)
        self.assertEqual(outcome["recovery_report"]["outcome"], "in_progress")

        # Attempt 2 completes and collects -> recovery report automatically transitions to 'recovered'!
        with patch.object(transport, "collect", return_value=_collect_payload(self.manager, "oom-inv-01-r1", state="completed", exit_code=0, log_text=OK_SOA_LOG)):
            self.manager.collect("oom-inv-01-r1")

        # User can search by host, outcome, or child run_id
        by_host = self.manager.list_recovery_reports(query="sim-large", outcome="recovered")
        self.assertEqual(len(by_host), 1)
        self.assertEqual(by_host[0]["root_run_id"], "oom-inv-01")
        self.assertEqual(by_host[0]["attempts_count"], 2)

        fetched = self.manager.get_recovery_report("oom-inv-01-r1")
        report = fetched["report"]
        self.assertEqual(report["outcome"], "recovered")
        self.assertTrue(report["input_integrity"]["preserved_across_all_attempts"])
        self.assertFalse(report["input_integrity"]["scientific_conditions_changed"])
        self.assertEqual(report["attempts"][1]["five_dim_summary"]["operating_qualification"], "violations_detected")
        self.assertTrue(Path(report["markdown_path"]).is_file())
        self.assertIn("oom-inv-01-r1", fetched["markdown"])
        self.assertIn("16.00 GiB", fetched["markdown"])

    def test_license_retry_budget_exhaustion_and_watch_resume_on_restart(self):
        self.manager.prepare(
            run_key="lic-slurm-01",
            netlist_text=NETLIST,
            host_id="sim-large",
            backend="slurm",
            simulator="spectre",
            scheduler={"partition": "analog"},
            limits={"cpu_count": 2, "memory_bytes": 8 * 1024**3, "wall_seconds": 600},
            recovery_policy={"enabled": True, "max_recovery_attempts": 1, "license_backoff_seconds": 60},
        )
        with patch.object(transport, "collect", return_value=_collect_payload(self.manager, "lic-slurm-01", state="failed", exit_code=1, log_text=LICENSE_LOG)):
            self.manager.collect("lic-slurm-01")

        with patch.object(transport, "stage", side_effect=lambda p: {"manifest_sha256": p["manifest_sha256"], "state": "staged", "terminal": False, "remote_directory": "/r/l2"}), \
             patch.object(transport, "launch", side_effect=lambda p: {"manifest_sha256": p["manifest_sha256"], "state": "queued", "terminal": False, "scheduler": {"backend": "slurm", "job_id": "88001"}, "accepted": {"dispatch_claimed": True}}):
            first_rec = self.manager.evaluate_and_recover("lic-slurm-01", execute=True)
        self.assertEqual(first_rec["decision"]["action"], "retry_license_with_backoff")
        self.assertEqual(first_rec["child_run"]["run_id"], "lic-slurm-01-r1")

        # Arm watch on active child, then simulate server restart with a new supervisor instance!
        sup1 = DirectRecoverySupervisor(self.manager, interval_seconds=60, max_backoff_seconds=300)
        sup1.monitor.watch("lic-slurm-01-r1", allow_collect=True)
        sup1.stop(timeout=1)

        sup2 = DirectRecoverySupervisor(self.manager, interval_seconds=60, max_backoff_seconds=300)
        try:
            resumed = sup2.resume()
            self.assertEqual(resumed["resumed"], ["lic-slurm-01-r1"])
            self.assertTrue(sup2.monitor.snapshot()["running"])
        finally:
            sup2.stop(timeout=1)

        # Second license failure hits max_recovery_attempts=1 -> exhausted!
        with patch.object(transport, "collect", return_value=_collect_payload(self.manager, "lic-slurm-01-r1", state="failed", exit_code=1, log_text=LICENSE_LOG)):
            self.manager.collect("lic-slurm-01-r1")
        second_rec = self.manager.evaluate_and_recover("lic-slurm-01-r1", execute=True)
        self.assertEqual(second_rec["decision"]["result"], "deny")
        self.assertEqual(second_rec["decision"]["reason_code"], "attempt_budget_exhausted")
        self.assertEqual(second_rec["recovery_report"]["outcome"], "exhausted")

        # CLI recovery-reports & recovery-report --markdown work cleanly
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli_main(["--data-dir", str(self.data_dir), "direct", "recovery-reports", "--outcome", "exhausted"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue())["reports"][0]["root_run_id"], "lic-slurm-01")

        md_out = io.StringIO()
        with redirect_stdout(md_out):
            rc_md = cli_main(["--data-dir", str(self.data_dir), "direct", "recovery-report", "lic-slurm-01", "--markdown"])
        self.assertEqual(rc_md, 0)
        self.assertIn("# SpiceOwl 有界恢复报告", md_out.getvalue())


class BoundedRecoveryWebApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name) / "state"
        _register_host(self.data_dir, LARGE_HOST, mem_total_gib=128, mem_avail_gib=100)
        self.app = Controller(self.data_dir, agent_mode="standalone")
        self.server = make_server(self.app, 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        _, state = self._req("GET", "/api/state")
        self.token = state["csrf_token"]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.close()
        self.temp.cleanup()

    def _req(self, method, path, body=None, *, csrf=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": f"127.0.0.1:{self.port}"}
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
        return resp.status, json.loads(data.decode("utf-8"))

    def test_web_policy_recover_and_searchable_recovery_report_endpoints(self):
        st, created = self._req("POST", "/api/direct/prepare", {
            "run_key": "web-rec-01",
            "netlist_text": NETLIST,
            "host_id": "sim-large",
            "backend": "slurm",
            "simulator": "spectre",
            "cpus": 2,
            "memory_gib": 8,
            "scheduler": {"partition": "sim"},
            "recovery_policy": {"enabled": False, "max_recovery_attempts": 2, "max_memory_bytes": 32 * 1024**3},
        })
        self.assertEqual(st, 201)

        # Toggle policy on via CSRF-protected endpoint
        self.assertEqual(self._req("POST", "/api/direct/runs/web-rec-01/policy", {"enabled": True}, csrf=False)[0], 403)
        st, updated = self._req("POST", "/api/direct/runs/web-rec-01/policy", {"enabled": True, "max_memory_bytes": 32 * 1024**3})
        self.assertEqual(st, 200)
        self.assertTrue(updated["run"]["recovery_policy"]["enabled"])

        # Collect syntax failure -> recover endpoint denies and archives a searchable not_recoverable report
        manager = DirectExecutionManager(self.data_dir)
        with patch.object(transport, "collect", return_value=_collect_payload(manager, "web-rec-01", state="failed", exit_code=1, log_text=SYNTAX_LOG)):
            self.assertEqual(self._req("POST", "/api/direct/runs/web-rec-01/collect", {})[0], 202)

        st, rec = self._req("POST", "/api/direct/runs/web-rec-01/recover", {"execute": True})
        self.assertEqual(st, 200)
        self.assertEqual(rec["decision"]["result"], "deny")
        self.assertEqual(rec["decision"]["reason_code"], "design_or_numerical_error")

        # Search recovery reports via HTTP API
        st, listed = self._req("GET", "/api/direct/recovery-reports?q=web-rec-01&outcome=not_recoverable")
        self.assertEqual(st, 200)
        self.assertEqual(len(listed["reports"]), 1)

        st, detail = self._req("GET", "/api/direct/recovery-reports/web-rec-01")
        self.assertEqual(st, 200)
        self.assertEqual(detail["report"]["outcome"], "not_recoverable")
        self.assertIn("web-rec-01", detail["markdown"])


if __name__ == "__main__":
    unittest.main()

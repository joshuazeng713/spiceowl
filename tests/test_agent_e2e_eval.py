"""End-to-end tests for generic next-action orchestration, interrupted CodexBridge
turn recovery, and the frozen 10-case Agent E2E evaluation benchmark harness."""

from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from spiceowl.agent_eval import (
    FROZEN_BENCHMARK_CASES,
    compare_baselines,
    render_eval_markdown,
    run_agent_eval,
)
from spiceowl.cli.main import main as cli_main
from spiceowl.codex_bridge.bridge import CodexBridge
from spiceowl.codex_bridge.transport import BridgeError
from spiceowl.domain.common import utcnow
from spiceowl.orchestrator import evaluate_next_action
from spiceowl.service import Controller
from spiceowl.simulation_tools import SimulationToolDispatcher
from spiceowl.web.server import make_server


class NextActionOrchestratorTests(unittest.TestCase):
    def test_all_seven_stages_and_hard_gates(self):
        # 1. Incomplete specification preserves missing fields
        r1 = evaluate_next_action({"experiment": {"netlist_text": "simulator lang=spectre\n", "analyses": ["tran"]}})
        self.assertEqual(r1["stage"], "define_experiment")
        self.assertFalse(r1["ready_to_advance"])
        missing_fields = {m["field"] for m in r1["missing"]}
        self.assertIn("experiment.pvt", missing_fields)
        self.assertIn("experiment.signals", missing_fields)
        self.assertIn("experiment.criteria", missing_fields)

        base_exp = {
            "netlist_text": "simulator lang=spectre\nV1 (out 0) vsource dc=1.2\n",
            "analyses": ["tran"],
            "pvt": {"corner": "tt", "voltage": 1.2, "temperature": 27},
            "signals": ["v(out)"],
            "criteria": [{"name": "vout", "min": 1.1}],
        }

        # 2. Unresolved dependencies & cache-only hit
        r2 = evaluate_next_action({"experiment": base_exp, "dependencies": {"cache_only_hit": True, "unresolved_models": ["nmos1"]}})
        self.assertEqual(r2["stage"], "resolve_dependencies")
        self.assertEqual(r2["scientific_verdict"], "dependencies_unverified")

        # 3. Validate & freeze
        r3 = evaluate_next_action({
            "experiment": base_exp,
            "dependencies": {"audit_required": False},
            "plan": {"host_id": "sim-box", "frozen": False},
        })
        self.assertEqual(r3["stage"], "validate_and_freeze")
        self.assertEqual(r3["permitted_actions"][0]["action"], "freeze_plan")

        # 4. Uncertain dispatch hard gate
        r4 = evaluate_next_action({
            "experiment": base_exp,
            "dependencies": {"audit_required": False},
            "plan": {"host_id": "sim-box", "frozen": True, "manifest_sha256": "a" * 64, "run_id": "r-unc"},
            "run": {"run_id": "r-unc", "state": "launch_outcome_unknown", "dispatch_claimed": True},
        })
        self.assertEqual(r4["stage"], "dispatch")
        self.assertIn("launch", r4["forbidden_actions"])
        self.assertIn("prepare_replacement_key", r4["forbidden_actions"])
        self.assertEqual(r4["permitted_actions"][0]["action"], "lookup_original_submission")

        # 5. Downsampled preview trap
        r5 = evaluate_next_action({
            "experiment": base_exp,
            "dependencies": {"audit_required": False},
            "plan": {"host_id": "sim-box", "frozen": True, "manifest_sha256": "a" * 64, "run_id": "r-prev"},
            "run": {"run_id": "r-prev", "state": "simulator_complete_pending_measurements", "collection_phase": "complete"},
            "measurement": {"dataset": "tran.tran.tran", "representation": "downsampled_preview", "exact": False, "measurement_safe": False},
        })
        self.assertEqual(r5["stage"], "measure")
        self.assertEqual(r5["scientific_verdict"], "measurement_unsafe_preview")
        self.assertEqual(r5["permitted_actions"][0]["arguments_template"]["mode"], "exact")

        # 6. Valid spec failure is preserved, never retried
        r6 = evaluate_next_action({
            "experiment": base_exp,
            "dependencies": {"audit_required": False},
            "plan": {"host_id": "sim-box", "frozen": True, "manifest_sha256": "a" * 64, "run_id": "r-fail"},
            "run": {"run_id": "r-fail", "state": "simulator_complete_pending_measurements", "collection_phase": "complete"},
            "measurement": {"evaluated": True, "exact": True, "measurement_safe": True, "measurement_validity": "valid", "functional_performance": "fail"},
        })
        self.assertEqual(r6["stage"], "report")
        self.assertTrue(r6["complete"])
        self.assertEqual(r6["scientific_verdict"], "valid_spec_failure")
        self.assertIn("auto_retry_valid_spec_failure", r6["forbidden_actions"])

        # 7. Hash mismatch halts immediately
        r7 = evaluate_next_action({"experiment": base_exp, "plan": {"hash_mismatch": True}})
        self.assertEqual(r7["scientific_verdict"], "halted_integrity_error")
        self.assertIn("launch", r7["forbidden_actions"])

    def test_pure_tool_spiceowl_next_action_runs_in_embedded_mode_with_zero_io(self):
        dispatcher = SimulationToolDispatcher(mode="embedded_in_circuitgit")
        self.assertEqual(dispatcher.policy("spiceowl_next_action", {"state": {}}), "read")
        with mock.patch("subprocess.Popen", side_effect=AssertionError("no process")), \
             mock.patch("socket.socket", side_effect=AssertionError("no network")), \
             mock.patch("builtins.open", side_effect=AssertionError("no file")):
            res = dispatcher.dispatch("spiceowl_next_action", {"state": {"experiment": {}}})
        self.assertEqual(res["stage"], "define_experiment")
        self.assertEqual(res["scientific_verdict"], "incomplete_specification")


class InterruptedTurnRecoveryTests(unittest.TestCase):
    def _write_interrupted_journal(self, bdir: Path, *, with_side_effect: bool) -> None:
        bdir.mkdir(parents=True, exist_ok=True)
        calls = {}
        approvals = {}
        if with_side_effect:
            calls["ck-1"] = {
                "fingerprint": "a" * 64, "state": "executing", "call_id": "call-launch-1",
                "request_key": "req-crash-1", "policy": "approval", "rpc_ids": [1],
            }
            approvals["approval-1"] = {
                "approval_id": "approval-1", "kind": "dynamic_tool", "method": "item/tool/call",
                "summary": "Run launch", "params": {"tool": "spiceowl_direct_control"},
                "status": "resolving", "can_approve": False, "can_deny": False,
                "payload_sha256": "b" * 64, "rpc_id": 1, "call_key": "ck-1",
                "thread_id": "th-1", "turn_id": "tn-1", "request_key": "req-crash-1",
            }
        else:
            calls["ck-read"] = {
                "fingerprint": "c" * 64, "state": "executing", "call_id": "call-read-1",
                "request_key": "req-crash-1", "policy": "read", "rpc_ids": [1],
            }
        journal = {
            "schema": "spiceowl.codex-journal.v1",
            "state": "running",
            "connection_id": "conn-1",
            "connection_profile": {},
            "model": "test-model",
            "thread_id": "th-1",
            "turn_id": "tn-1",
            "request_key": "req-crash-1",
            "cursor": 1,
            "requests": {
                "req-crash-1": {
                    "schema": "spiceowl.codex-turn.v1",
                    "request_key": "req-crash-1",
                    "fingerprint": "d" * 64,
                    "thread_id": "th-1",
                    "turn_id": "tn-1",
                    "state": "running",
                    "created_at": utcnow(),
                },
            },
            "calls": calls,
            "approvals": approvals,
            "events": [{"id": 1, "method": "bridge/user_message", "params": {"text": "run"}}],
            "recovery_required": False,
        }
        (bdir / "codex-bridge.json").write_text(json.dumps(journal), encoding="utf-8")

    def test_read_only_interrupted_turn_reconciles_and_persists_without_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            bdir = Path(tmp) / "codex"
            self._write_interrupted_journal(bdir, with_side_effect=False)
            bridge = CodexBridge(bdir, mode="standalone")
            try:
                self.assertTrue(bridge.status()["recovery_required"])
                snap = bridge.recovery_report()
                self.assertFalse(snap["has_possible_side_effects"])
                self.assertTrue(snap["safe_to_retry_read_only"])
                self.assertEqual(len(snap["interrupted_requests"]), 1)

                receipt = bridge.reconcile_interrupted(
                    resolution="acknowledge_and_archive",
                    note="Read-only turn interrupted; archiving.",
                )
                self.assertFalse(receipt["status"]["recovery_required"])
                self.assertEqual(receipt["status"]["state"], "disconnected")
                self.assertEqual(receipt["recovery"]["archived_requests"], ["req-crash-1"])
                self.assertFalse(receipt["recovery"]["automatic_replay"])
                # Archived request stays in journal as terminal 'interrupted' (replay guard!)
                self.assertEqual(bridge.data["requests"]["req-crash-1"]["state"], "interrupted")
            finally:
                bridge.close()

            # Restarting bridge after reconciliation MUST stay unlocked!
            restarted = CodexBridge(bdir, mode="standalone")
            try:
                self.assertFalse(restarted.status()["recovery_required"])
                self.assertEqual(restarted.status()["recovery_history_count"], 1)
            finally:
                restarted.close()

    def test_side_effect_interrupted_turn_requires_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            bdir = Path(tmp) / "codex"
            self._write_interrupted_journal(bdir, with_side_effect=True)
            bridge = CodexBridge(bdir, mode="standalone")
            try:
                snap = bridge.recovery_report()
                self.assertTrue(snap["has_possible_side_effects"])
                self.assertFalse(snap["safe_to_retry_read_only"])

                with self.assertRaises(BridgeError) as ctx:
                    bridge.reconcile_interrupted(confirmed_side_effects_checked=False)
                self.assertEqual(ctx.exception.code, "codex_side_effects_unconfirmed")
                self.assertTrue(bridge.status()["recovery_required"])

                receipt = bridge.reconcile_interrupted(
                    resolution="abort_and_reset_thread",
                    note="Checked remote job status.",
                    confirmed_side_effects_checked=True,
                )
                self.assertFalse(receipt["status"]["recovery_required"])
                self.assertIsNone(receipt["status"]["thread_id"])
                self.assertEqual(bridge.data["calls"]["ck-1"]["state"], "interrupted_reconciled")
            finally:
                bridge.close()

        # Embedded mode rejects recovery mutation
        embedded = CodexBridge(Path(tmp) / "emb", mode="embedded_in_circuitgit")
        with self.assertRaises(BridgeError) as ctx:
            embedded.reconcile_interrupted()
        self.assertEqual(ctx.exception.code, "codex_embedded_mode")


class AgentEvalHarnessAndSurfacesTests(unittest.TestCase):
    def test_frozen_ten_case_benchmark_deterministic_vs_weak_naive(self):
        self.assertEqual(len(FROZEN_BENCHMARK_CASES), 10)
        det = run_agent_eval(baseline="deterministic", repeats=2)
        self.assertEqual(det["total_trials"], 20)
        self.assertEqual(det["passed_trials"], 20)
        self.assertTrue(det["strict_qualification_passed"])
        self.assertEqual(det["safety_counters"]["unauthorized_dispatches"], 0)
        self.assertEqual(det["safety_counters"]["duplicate_dispatches"], 0)
        self.assertEqual(det["safety_counters"]["false_scientific_passes"], 0)

        weak = run_agent_eval(baseline="weak_naive", repeats=1)
        self.assertFalse(weak["strict_qualification_passed"])
        self.assertGreater(weak["safety_counters"]["unauthorized_dispatches"], 0)
        self.assertGreater(weak["safety_counters"]["duplicate_dispatches"], 0)
        self.assertGreater(weak["safety_counters"]["false_scientific_passes"], 0)

        comp = compare_baselines(repeats=1)
        self.assertIn("Deterministic Orchestrator", comp["markdown"])
        self.assertIn("Weak Naive Baseline", comp["markdown"])

    def test_cli_and_web_api_endpoints_for_next_action_eval_and_codex_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "state"
            state_file = Path(tmp) / "state.json"
            state_file.write_text(json.dumps({"experiment": {"analyses": ["tran"]}}), encoding="utf-8")

            # 1. CLI next-action & eval-agent
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli_main(["next-action", "--state-json", str(state_file)])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(buf.getvalue())["stage"], "define_experiment")

            buf2 = io.StringIO()
            with redirect_stdout(buf2):
                rc2 = cli_main(["eval-agent", "--baseline", "compare", "--markdown"])
            self.assertEqual(rc2, 0)
            self.assertIn("100.0%", buf2.getvalue())

            # 2. Web HTTP endpoints (with CSRF check!)
            controller = Controller(data_dir, agent_mode="standalone")
            server = make_server(controller, 0)
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                th = threading.Thread(target=server.serve_forever, daemon=True)
                th.start()

                with urlopen(f"{base}/api/state", timeout=5) as resp:
                    token = json.loads(resp.read().decode("utf-8"))["csrf_token"]

                # Missing CSRF rejected with 403
                bad = Request(f"{base}/api/agent/eval", data=b"{}", headers={"Content-Type": "application/json", "Origin": base}, method="POST")
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

                st_na, na = post_json("/api/agent/next-action", {"experiment": {}})
                self.assertEqual(st_na, 200)
                self.assertEqual(na["stage"], "define_experiment")

                st_ev, ev = post_json("/api/agent/eval", {"baseline": "compare", "repeats": 1})
                self.assertEqual(st_ev, 200)
                self.assertTrue(ev["deterministic"]["strict_qualification_passed"])
                self.assertFalse(ev["weak_naive"]["strict_qualification_passed"])

                # Simulate interrupted turn on server's codex bridge, inspect via GET /api/codex/recovery, unlock via POST /api/codex/recover
                controller.codex._unknown("simulated_test_disconnect")
                with urlopen(f"{base}/api/codex/recovery", timeout=5) as resp:
                    rec_snap = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(rec_snap["recovery_required"])

                st_rec, rec_res = post_json("/api/codex/recover", {
                    "resolution": "acknowledge_and_archive",
                    "note": "Web API test reconciliation.",
                    "confirmed_side_effects_checked": False,
                })
                self.assertEqual(st_rec, 202)
                self.assertFalse(rec_res["status"]["recovery_required"])
            finally:
                server.shutdown()
                server.server_close()
                controller.close()


if __name__ == "__main__":
    unittest.main()

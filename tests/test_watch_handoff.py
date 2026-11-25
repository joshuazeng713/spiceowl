"""Tests for overnight web watch event inbox, queue-timeout detection, Agent handoff wakeup, and autonomy guardrails."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from spiceowl.direct import DirectExecutionManager, DirectRecoverySupervisor
from spiceowl.domain.common import DomainError
from spiceowl.service import Controller
from spiceowl.standalone_agent import build_standalone_dispatcher
from spiceowl.watch_handoff import (
    WatchHandoffManager,
    build_codex_bridge_pusher,
    evaluate_autonomy_decision,
    validate_autonomy_policy,
)
from spiceowl.web.server import make_server


class FakeCodexBridge:
    """Minimal stand-in for CodexBridge verifying active Agent wakeup without external processes."""

    def __init__(self, *, connected: bool = True, thread_id: str | None = "thread-watch-1", state: str = "idle"):
        self._connected = connected
        self._thread_id = thread_id
        self._state = state
        self.messages: list[tuple[str, str, str]] = []
        self.created_threads = 0

    def status(self) -> dict:
        return {
            "connected": self._connected,
            "thread_id": self._thread_id,
            "state": self._state,
            "recovery_required": False,
        }

    def create_thread(self) -> dict:
        self.created_threads += 1
        self._thread_id = f"thread-created-{self.created_threads}"
        return {"thread_id": self._thread_id}

    def send_message(self, thread_id: str, text: str, request_key: str) -> dict:
        self.messages.append((thread_id, text, request_key))
        return {"thread_id": thread_id, "request_key": request_key, "status": "sent"}


class TestWatchHandoffAndAutonomy(unittest.TestCase):
    def test_autonomy_policy_validation_and_scientific_redline(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spiceowl-watch-") as tmp:
            mgr = WatchHandoffManager(tmp)
            p0 = mgr.get_policy()
            self.assertFalse(p0["enabled"])
            self.assertTrue(p0["protect_netlist_and_spec"])
            self.assertEqual(p0["queue_timeout_seconds"], 1800)

            with self.assertRaises(DomainError):
                validate_autonomy_policy({"protect_netlist_and_spec": False})

            updated = mgr.update_policy({
                "enabled": True,
                "queue_timeout_seconds": 600,
                "max_memory_gib": 32,
                "max_agent_resubmissions": 2,
            })
            self.assertTrue(updated["enabled"])
            self.assertEqual(updated["queue_timeout_seconds"], 600)
            self.assertEqual(updated["max_memory_gib"], 32)
            self.assertTrue(updated["protect_netlist_and_spec"])

    def test_autonomy_decision_bounds_and_netlist_protection(self) -> None:
        policy = validate_autonomy_policy({
            "enabled": True,
            "allow_host_migration": True,
            "allow_memory_scale": True,
            "max_memory_gib": 32,
            "allow_sim_option_tuning": True,
            "max_agent_resubmissions": 2,
        })
        parent = {
            "run_id": "run-1",
            "host_id": "node-a",
            "sources": {"top.scs": "simulator lang=spectre\nR1 (in out) resistor r=1k\n"},
            "resources": {"memory_bytes": 8 * 1024**3},
            "simulator_args": ["+mt=2"],
        }

        # Within bounds: migrate host, double RAM to 16 GiB, keep netlist byte-identical
        ok = evaluate_autonomy_decision(
            policy,
            operation="prepare",
            arguments={
                "host_id": "node-b",
                "sources": dict(parent["sources"]),
                "resources": {"memory_bytes": 16 * 1024**3},
                "simulator_args": ["+mt=4"],
            },
            parent_run=parent,
            resubmissions_used=0,
        )
        self.assertTrue(ok["allowed"])

        # Violates scientific redline: netlist altered -> blocked for morning review!
        mutated = evaluate_autonomy_decision(
            policy,
            operation="prepare",
            arguments={
                "host_id": "node-b",
                "sources": {"top.scs": "simulator lang=spectre\nR1 (in out) resistor r=2k\n"},
                "resources": {"memory_bytes": 16 * 1024**3},
            },
            parent_run=parent,
            resubmissions_used=0,
        )
        self.assertFalse(mutated["allowed"])
        self.assertEqual(mutated["code"], "netlist_mutation_blocked")

        # Exceeds memory cap (64 GiB > 32 GiB) -> blocked
        over_mem = evaluate_autonomy_decision(
            policy,
            operation="prepare",
            arguments={"host_id": "node-b", "resources": {"memory_bytes": 64 * 1024**3}},
            parent_run=parent,
            resubmissions_used=0,
        )
        self.assertFalse(over_mem["allowed"])
        self.assertEqual(over_mem["code"], "memory_cap_exceeded")

        # Resubmission budget exhausted -> blocked
        exhausted = evaluate_autonomy_decision(
            policy, operation="launch", arguments={"run_id": "run-3"}, parent_run=parent, resubmissions_used=2,
        )
        self.assertFalse(exhausted["allowed"])
        self.assertEqual(exhausted["code"], "resubmission_budget_exhausted")

    def test_queue_timeout_wakes_connected_agent_idempotently(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spiceowl-qtimeout-") as tmp:
            dm = DirectExecutionManager(tmp)
            hm = WatchHandoffManager(tmp)
            hm.update_policy({"enabled": True, "auto_push_to_agent": True, "queue_timeout_seconds": 60})
            bridge = FakeCodexBridge(connected=True, thread_id=None)
            sup = DirectRecoverySupervisor(dm, handoff_manager=hm, agent_pusher=build_codex_bridge_pusher(bridge))

            old_ts = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat().replace("+00:00", "Z")
            fake_run = {
                "run_id": "slurm-stuck-1",
                "root_run_id": "slurm-stuck-1",
                "host_id": "eda-node-01",
                "backend": "slurm",
                "simulator": "spectre",
                "state": "submitted",
                "attempt_number": 1,
                "launched_at": old_ts,
                "scheduler_job_id": "99123",
                "last_status": {"remote_state": "queued"},
            }
            sup._check_queue_timeout(fake_run)
            # Second check must dedupe and NEVER spam the Agent twice!
            sup._check_queue_timeout(fake_run)

            self.assertEqual(len(bridge.messages), 1)
            self.assertEqual(bridge.created_threads, 1)
            tid, prompt, rkey = bridge.messages[0]
            self.assertEqual(tid, "thread-created-1")
            self.assertIn("queue_timeout", prompt)
            self.assertIn("spiceowl_scan_resources", prompt)
            self.assertIn("watch_record_decision", prompt)
            self.assertTrue(rkey.startswith("watch-"))

            events = hm.list_events()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["state"], "pushed_to_agent")
            self.assertTrue(events[0]["agent_handoff"]["pushed"])

            # Agent records its investigation & resubmission back to the Web UI timeline
            updated = hm.record_agent_decision(
                events[0]["event_id"],
                outcome="resolved_resubmitted",
                rationale="Migrated to idle eda-node-03 with 128 GiB free RAM.",
                resource_assessment=[{"host_id": "eda-node-03", "idle_cpus": 48, "available_gib": 128.0, "verdict": "recommended"}],
                config_changes={"host_id": {"from": "eda-node-01", "to": "eda-node-03"}},
                new_run_id="slurm-stuck-1-r2",
            )
            self.assertEqual(updated["state"], "resolved_resubmitted")
            self.assertEqual(updated["new_run_id"], "slurm-stuck-1-r2")
            self.assertEqual(hm.count_agent_resubmissions("slurm-stuck-1"), 1)

    def test_standalone_agent_dispatcher_watch_tools_and_dynamic_policy(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spiceowl-agent-watch-") as tmp:
            ctrl = Controller(tmp, agent_mode="standalone")
            try:
                dm = DirectExecutionManager(tmp, hosts=ctrl.hosts)
                hm = WatchHandoffManager(tmp)
                disp = build_standalone_dispatcher(ctrl, direct_manager=dm, handoff_manager=hm)

                # Default: autonomy disabled -> write/scan operations require human approval
                self.assertEqual(disp.policy("spiceowl_scan_resources", {"host_ids": ["h1"]}), "approval")
                self.assertEqual(disp.policy("spiceowl_direct_control", {"operation": "cancel", "arguments": {"run_id": "r1"}}), "approval")

                # Enable overnight autonomy -> scan_resources & bounded control calls become auto-permitted ('read')
                hm.update_policy({"enabled": True, "max_memory_gib": 32, "max_agent_resubmissions": 2})
                self.assertEqual(disp.policy("spiceowl_scan_resources", {"host_ids": ["h1"]}), "read")
                self.assertEqual(disp.policy("spiceowl_direct_control", {"operation": "cancel", "arguments": {"run_id": "r1"}}), "read")
                self.assertEqual(
                    disp.policy("spiceowl_direct_control", {"operation": "prepare", "arguments": {"resources": {"memory_bytes": 64 * 1024**3}}}),
                    "approval",
                )

                # Agent can read watch events & record decisions via standard tools
                ev = hm.record_anomaly(anomaly_type="confirmed_oom", run_id="amp-r1", host_id="node-a")
                listed = disp.dispatch("spiceowl_direct_read", {"operation": "watch_events", "arguments": {}})
                self.assertEqual(len(listed["events"]), 1)
                rec = disp.dispatch(
                    "spiceowl_direct_control",
                    {
                        "operation": "watch_record_decision",
                        "arguments": {
                            "event_id": ev["event_id"],
                            "outcome": "awaiting_morning_review",
                            "rationale": "Memory required exceeds overnight 32 GiB cap; paused for morning review.",
                            "needs_human_review": True,
                        },
                    },
                )
                self.assertEqual(rec["state"], "awaiting_morning_review")
            finally:
                ctrl.close()

    def test_web_server_watch_handoff_api_and_no_external_notifications_route(self) -> None:
        import re

        with tempfile.TemporaryDirectory(prefix="spiceowl-srv-watch-") as tmp:
            ctrl = Controller(tmp, agent_mode="standalone")
            srv = make_server(ctrl, port=0, enable_direct_exec=True)
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            try:
                port = srv.server_address[1]
                conn0 = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn0.request("GET", "/api/state")
                state_data = json.loads(conn0.getresponse().read().decode("utf-8"))
                conn0.close()
                token = state_data["csrf_token"]

                def req(method: str, path: str, body: dict | None = None):
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                    headers = {"X-SpiceOwl-Token": token}
                    raw = None
                    if body is not None:
                        headers["Content-Type"] = "application/json"
                        raw = json.dumps(body).encode("utf-8")
                    conn.request(method, path, body=raw, headers=headers)
                    resp = conn.getresponse()
                    data = json.loads(resp.read().decode("utf-8"))
                    conn.close()
                    return resp.status, data

                # Legacy external notifications endpoint must be completely gone (404)
                st_old, _ = req("GET", "/api/direct/notifications")
                self.assertEqual(st_old, 404)

                # Update overnight policy via Web API
                st, pol = req("POST", "/api/direct/watch-policy", {"enabled": True, "queue_timeout_seconds": 900, "max_memory_gib": 48})
                self.assertEqual(st, 200)
                self.assertTrue(pol["policy"]["enabled"])
                self.assertEqual(pol["policy"]["queue_timeout_seconds"], 900)

                # Simulate overnight queue_timeout handoff
                st, sim = req("POST", "/api/direct/watch-events/simulate", {"scenario": "queue_timeout"})
                self.assertEqual(st, 201)
                self.assertEqual(sim["event"]["state"], "resolved_resubmitted")
                self.assertTrue(sim["event"]["agent_handoff"]["pushed"])

                # Fetch Web timeline
                st, listing = req("GET", "/api/direct/watch-events")
                self.assertEqual(st, 200)
                self.assertEqual(len(listing["events"]), 1)
                self.assertEqual(listing["policy"]["max_memory_gib"], 48)
            finally:
                srv.shutdown()
                srv.server_close()
                ctrl.close()


if __name__ == "__main__":
    unittest.main()

"""Model-facing projections and real optional SDK stdio integration."""

import asyncio
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from spiceowl.domain.common import DomainError, digest, utcnow
from spiceowl.mcp.server import create_server, serve
from spiceowl.mcp.views import MAX_EVENTS, MAX_RESOURCES, ReadViews
from spiceowl.service import Controller
from spiceowl.storage.readonly import MAX_RECORDS, ReadStore
from spiceowl.integrations.ic_workbench import ICGatewayBackend, LoopbackGatewayClient
from test_ic_workbench import CONFIG as IC_CONFIG, run_fixture as ic_run_fixture, snapshot_fixture as ic_snapshot_fixture


try:
    SDK_AVAILABLE = importlib.util.find_spec("mcp.server.fastmcp") is not None
except ModuleNotFoundError:
    SDK_AVAILABLE = False

TOOLS = {
    "spiceowl_status", "spiceowl_list_runs", "spiceowl_get_run",
    "spiceowl_list_connections", "spiceowl_list_hosts",
    "spiceowl_list_ic_gateways", "spiceowl_list_ic_runs", "spiceowl_get_ic_run",
}
TOOL_ARGUMENTS = {
    "spiceowl_get_run": {"run_id": "demo-disconnect"},
    "spiceowl_list_ic_runs": {"gateway_id": IC_CONFIG["id"]},
    "spiceowl_get_ic_run": {"gateway_id": IC_CONFIG["id"], "run_id": "fixture-native-run"},
}
SENTINEL = "SYNTHETIC_PRIVATE_SENTINEL"


def add_host(app):
    app.store.create("host", "lab-fixture", {
        "schema": "spiceowl.host.v1", "host_id": "lab-fixture", "label": "Fixture lab",
        "ssh_target": SENTINEL, "expected_host": SENTINEL, "expected_user": SENTINEL,
        "tools": [{"kind": "spectre", "executable": "/private/" + SENTINEL}],
        "created_at": utcnow(), "revision": 1, "synthetic": False,
        "last_probe": {
            "host_id": "lab-fixture", "synthetic": False, "scope": "connectivity_only",
            "observed_at": "2026-11-13T10:00:00+00:00", "transport": "reachable",
            "identity": {"verified": True, "hostname": SENTINEL, "username": SENTINEL},
            "tools": [{"kind": "spectre", "status": "available", "version": "21.1.0.509.isr12",
                       "error": {"code": SENTINEL, "message": SENTINEL}, "executable": SENTINEL}],
            "error": {"code": SENTINEL, "message": SENTINEL}, "stdout": SENTINEL,
        },
    })


def add_ic(app):
    backend = ICGatewayBackend(app.store.directory)
    backend.register(IC_CONFIG)
    with patch.object(LoopbackGatewayClient, "supervision", return_value=ic_snapshot_fixture(ic_run_fixture())):
        backend.refresh(IC_CONFIG["id"])
    return backend


class ReadViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Controller(self.temp.name)
        self.app.seed_demo()
        self.views = ReadViews(ReadStore(self.temp.name))

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def test_connection_projection_drops_configs_and_nonfake_records(self):
        self.app.store.update("connection", "demo-agent", lambda c: c | {
            "api_key": SENTINEL, "config": {"key": SENTINEL},
            "status": {"authentication": SENTINEL, "token": SENTINEL},
        })
        self.app.store.create("connection", "private", {"kind": "model", "backend": "real", "api_key": SENTINEL})
        result = self.views.list_connections()
        self.assertEqual(len(result["connections"]), 2)
        self.assertNotIn(SENTINEL, json.dumps(result))
        self.assertTrue(result["read_only"])
        self.assertTrue(all(c["synthetic"] for c in result["connections"]))
        self.app.remove_connection("demo-agent")
        self.assertEqual(len(self.views.list_connections()["connections"]), 1)

    def test_host_projection_preserves_probe_provenance_but_not_paths_or_aliases(self):
        add_host(self.app)
        result = self.views.list_hosts()
        host = result["hosts"][0]
        self.assertEqual(set(host), {"host_id", "label", "synthetic", "tools", "last_probe", "last_resources"})
        self.assertIs(host["synthetic"], False)
        self.assertEqual(host["tools"], [{"kind": "spectre"}])
        probe = host["last_probe"]
        self.assertEqual(probe["tools"][0]["version"], "21.1.0.509.isr12")
        self.assertFalse(probe["synthetic"])
        self.assertEqual(probe["scope"], "connectivity_only")
        self.assertEqual(probe["observed_at"], "2026-11-13T10:00:00+00:00")
        encoded = json.dumps(result)
        for forbidden in [SENTINEL, "ssh_target", "executable", "stdout", "expected_host", "expected_user"]:
            self.assertNotIn(forbidden, encoded)

    def test_run_projection_is_bounded_and_drops_arbitrary_facts(self):
        raw = self.app.store.get("run", "demo-disconnect")
        item = raw["events"][0]
        raw["events"] = [deepcopy(item) for _ in range(MAX_EVENTS + 2)]
        for index, event in enumerate(raw["events"]):
            event.update(event_id=f"event-{index}", sequence=index, message="Evidence " * 1000)
            event["facts"].update(api_key=SENTINEL, shell={"command": SENTINEL}, memory_limit_mb=10**100)
        raw["resource_history"] = [{"sequence": i, "memory_mb": 12, "simulated_time_us": None, "raw": SENTINEL}
                                   for i in range(MAX_RESOURCES + 1)]
        raw["execution_identity"]["private_path"] = SENTINEL
        raw["input_manifest"] = {"credentials": SENTINEL}
        self.app.store.update("run", raw["run_id"], lambda _: raw)
        result = self.views.get_run(raw["run_id"])
        run = result["run"]
        self.assertTrue(run["synthetic"])
        self.assertTrue(run["events_truncated"])
        self.assertEqual(len(run["events"]), MAX_EVENTS)
        self.assertEqual(len(run["resource_history"]), MAX_RESOURCES)
        self.assertTrue(run["resources_truncated"])
        self.assertLessEqual(len(run["events"][0]["message"]), 500)
        self.assertIsNone(run["events"][0]["facts"]["memory_limit_mb"])
        self.assertNotIn(SENTINEL, json.dumps(result))
        self.assertEqual(self.app.store.get("run", raw["run_id"]), raw)

    def test_independent_run_dimensions_and_stored_time_are_preserved(self):
        self.app.advance("demo-disconnect")
        raw = self.app.store.get("run", "demo-disconnect")
        result = self.views.get_run("demo-disconnect")
        self.assertEqual(result["run"]["execution"], "running")
        self.assertEqual(result["run"]["observation"], "unknown")
        self.assertEqual(result["run"]["updated_at"], raw["updated_at"])
        for _ in range(2):
            self.app.advance("demo-target_miss")
        miss = self.views.get_run("demo-target_miss")["run"]
        self.assertEqual((miss["measurement_validity"], miss["specification_result"]), ("valid", "fail"))
        self.assertEqual(result["source"], "stored_local_state")
        self.assertEqual(result["data_trust"], "untrusted_evidence")

    def test_all_lists_and_schema_projection_are_bounded(self):
        for i in range(MAX_RECORDS + 2):
            for kind, body in [("host", {"host_id": f"host-{i}", "tools": [], "synthetic": False}),
                               ("run", {"run_id": f"run-{i}", "synthetic": True})]:
                self.app.store.create(kind, f"fixture-{i}", body)
        for key, result in [("hosts", self.views.list_hosts()), ("runs", self.views.list_runs())]:
            self.assertEqual(len(result[key]), MAX_RECORDS)
            self.assertTrue(result["truncated"])

    def test_status_has_no_turn_content_or_mutation_capability(self):
        turn, _ = self.app.store.claim_turn("pending-test", digest("test"), "demo-agent", "demo-disconnect", SENTINEL, {})
        result = self.views.status()
        self.assertEqual(result["counts"]["records"]["run"], 4)
        self.assertEqual(result["counts"]["turn_states"]["dispatching"], 1)
        self.assertEqual(self.app.store.get_turn(turn["turn_id"])["status"], "dispatching")
        self.assertNotIn(SENTINEL, json.dumps(result))
        for key in ["ssh", "probe", "inference", "simulation_actions", "controller_recovery"]:
            self.assertFalse(result["capabilities"][key])

    def test_complete_tool_version_tokens_are_preserved_without_salvaging(self):
        add_host(self.app)
        examples = [
            ("spectre", "21.1.0.509.isr12"), ("spectre", "23.1.0.123"),
            ("virtuoso", "IC6.1.8-64b.500.26"), ("virtuoso", "ICADVM20.1-64b.500.27"),
            ("ngspice", "45.2"), ("ngspice", "ngspice-44"),
        ]
        for kind, version in examples:
            for value, expected in [(version, version), ("/opt/" + version, None),
                                    ("banner " + version, None), (version + "\n", None),
                                    (version + " " + SENTINEL, None)]:
                with self.subTest(kind=kind, value=value):
                    def update(record):
                        return record | {
                            "tools": [{"kind": kind, "executable": SENTINEL}],
                            "last_probe": record["last_probe"] | {
                                "tools": [{"kind": kind, "status": "available", "version": value}]
                            },
                        }
                    self.app.store.update("host", "lab-fixture", update)
                    host = self.views.list_hosts()["hosts"][0]
                    self.assertEqual(host["tools"], [{"kind": kind}])
                    self.assertEqual(host["last_probe"]["tools"][0]["version"], expected)
                    self.assertNotIn(SENTINEL, json.dumps(host))

    def test_missing_sdk_is_safe_from_serve_and_factory(self):
        real_import = __import__

        def missing(name, *args, **kwargs):
            if name == "mcp" or name.startswith("mcp."):
                raise ModuleNotFoundError("absent", name="mcp")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=missing):
            for call in (serve, create_server):
                with self.subTest(call=call.__name__), self.assertRaises(DomainError) as error:
                    call(self.temp.name)
                self.assertEqual(error.exception.code, "missing_dependency")
                self.assertIn("mcp==1.30.0", str(error.exception))

    def test_ic_cached_queries_preserve_identity_dimensions_and_stored_times(self):
        backend = add_ic(self.app)
        stored = backend.run(IC_CONFIG["id"], "fixture-native-run")
        with patch("spiceowl.storage.sqlite.Store.__init__", side_effect=AssertionError("Writable store")), \
                patch.object(LoopbackGatewayClient, "_get", side_effect=AssertionError("Network")), \
                patch("subprocess.Popen", side_effect=AssertionError("External process")):
            gateways = self.views.list_ic_gateways()["gateways"]
            runs = self.views.list_ic_runs(IC_CONFIG["id"])["runs"]
            run = self.views.get_ic_run(IC_CONFIG["id"], "fixture-native-run")["run"]
        self.assertEqual(gateways[0]["gateway_id"], IC_CONFIG["id"])
        self.assertEqual(runs[0]["run_id"], "fixture-native-run")
        self.assertEqual(run["original_project_id"], "old-project")
        self.assertEqual(run["source_commit"], "a" * 40)
        self.assertEqual(run["execution"]["state"], "succeeded")
        self.assertEqual(run["data"]["state"], "collected")
        self.assertEqual(run["measurement"]["state"], "indeterminate")
        self.assertEqual(run["specification"]["state"], "reported")
        self.assertTrue(run["specification"]["metrics"][0]["passed"])
        self.assertEqual(run["observation"]["observed_at"], stored["observation"]["observed_at"])
        self.assertEqual(run["observation"]["checked_at"], stored["observation"]["checked_at"])
        self.assertTrue(run["synthetic"])

    def test_ic_allowlist_drops_paths_keys_raw_messages_and_foreign_artifact_urls(self):
        add_ic(self.app)
        self.app.store.update("ic_gateway", IC_CONFIG["id"], lambda record: record | {
            "base_url": "http://127.0.0.1:9999/" + SENTINEL, "api_key": SENTINEL,
            "observation": record["observation"] | {"error": {"code": SENTINEL, "message": SENTINEL}}})
        def corrupt(record):
            record.update(submission_key=SENTINEL, private_path=SENTINEL, ssh_target=SENTINEL,
                          measurement={"state": "valid", "raw": SENTINEL})
            record["execution"]["command"] = SENTINEL
            record["data"]["local_path"] = SENTINEL
            record["issues"].append({"code": SENTINEL, "message": SENTINEL})
            record["specification"]["metrics"][0]["private_path"] = SENTINEL
            record["artifacts"][0]["local_path"] = SENTINEL
            record["artifacts"].append({"name": "waveforms.csv", "url": "https://example.invalid/" + SENTINEL})
            record["artifacts"].append({"name": "waveforms.png", "url": "/api/jobs/other-run/artifacts/waveforms.png"})
            record["observation"]["state"] = "unobserved"
            record["observation"]["error"] = {"code": SENTINEL, "message": SENTINEL}
            return record
        self.app.store.update("ic_run:" + IC_CONFIG["id"], "fixture-native-run", corrupt)
        results = [self.views.list_ic_gateways(), self.views.list_ic_runs(IC_CONFIG["id"]),
                   self.views.get_ic_run(IC_CONFIG["id"], "fixture-native-run")]
        for forbidden in (SENTINEL, "base_url", "submission_key", "private_path", "ssh_target", "local_path", "other-run"):
            self.assertNotIn(forbidden, json.dumps(results))
        run = results[-1]["run"]
        self.assertEqual(run["measurement"]["state"], "indeterminate")
        self.assertEqual(run["observation"]["state"], "unobserved")
        self.assertEqual(run["artifacts_omitted"], 2)
        self.assertEqual(run["artifacts"][0]["url"], "/api/jobs/fixture-native-run/artifacts/waveforms.json")

    def test_ic_lists_and_evidence_have_independent_bounds(self):
        add_ic(self.app)
        record = self.app.store.get("ic_run:" + IC_CONFIG["id"], "fixture-native-run")
        for i in range(MAX_RECORDS + 1):
            self.app.store.create("ic_run:" + IC_CONFIG["id"], f"native-{i}", record | {"run_id": f"native-{i}"})
            self.app.store.create("ic_gateway", f"gw-{i}", {"id": f"gw-{i}", "backend": "ic-workbench", "project_id": "fixture"})
        listed = self.views.list_ic_runs(IC_CONFIG["id"])
        self.assertEqual(len(listed["runs"]), MAX_RECORDS)
        self.assertTrue(listed["truncated"])
        gateways = self.views.list_ic_gateways()
        self.assertEqual(len(gateways["gateways"]), MAX_RECORDS)
        self.assertTrue(gateways["truncated"])
        def extend(raw):
            raw["specification"]["metrics"] *= 20
            raw["issues"] *= 10
            raw["artifacts"] *= 10
            return raw
        self.app.store.update("ic_run:" + IC_CONFIG["id"], "fixture-native-run", extend)
        run = self.views.get_ic_run(IC_CONFIG["id"], "fixture-native-run")["run"]
        self.assertEqual(len(run["specification"]["metrics"]), 16)
        self.assertTrue(run["specification"]["metrics_truncated"])
        self.assertEqual(len(run["issues"]), 8)
        self.assertTrue(run["issues_truncated"])
        self.assertEqual(len(run["artifacts"]), 7)
        self.assertEqual(run["artifacts_omitted"], 3)


@unittest.skipUnless(SDK_AVAILABLE, "optional official MCP SDK is not installed")
class MCPIntegrationTests(unittest.TestCase):
    def test_real_stdio_initialize_list_and_call_while_controller_owns_state(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        with tempfile.TemporaryDirectory() as directory, Controller(directory) as app:
            app.seed_demo()
            add_host(app)
            add_ic(app)
            # An accepted but unexecuted turn must not be recovered by a reader.
            turn, _ = app.store.claim_turn("live-pending", digest("live"), "demo-agent", "demo-disconnect", SENTINEL, {})
            app.store.append_event(turn["turn_id"], "turn.fixture", {}, {"status": "running"})
            before = app.store.path.read_bytes()
            entries = sorted(p.name for p in Path(directory).iterdir())
            source = str(Path(__file__).resolve().parents[1] / "src")
            params = StdioServerParameters(
                command=sys.executable,
                args=["-c", "from spiceowl.mcp.server import serve; import sys; serve(sys.argv[1])", directory],
                env={"PYTHONPATH": source},
            )

            async def exercise():
                async with asyncio.timeout(20):
                    async with stdio_client(params) as (reader, writer):
                        async with ClientSession(reader, writer) as client:
                            initialized = await client.initialize()
                            self.assertEqual(initialized.serverInfo.name, "SpiceOwl")
                            self.assertIn("untrusted evidence", initialized.instructions)
                            listed = await client.list_tools()
                            self.assertEqual({tool.name for tool in listed.tools}, TOOLS)
                            for tool in listed.tools:
                                self.assertTrue(tool.annotations.readOnlyHint)
                                self.assertFalse(tool.annotations.destructiveHint)
                                self.assertTrue(tool.annotations.idempotentHint)
                                self.assertFalse(tool.annotations.openWorldHint)
                                self.assertFalse(tool.inputSchema["additionalProperties"])
                                self.assertEqual(set(tool.inputSchema.get("properties", {})),
                                                 set(TOOL_ARGUMENTS.get(tool.name, {})))
                            for name in sorted(TOOLS):
                                result = await client.call_tool(name, TOOL_ARGUMENTS.get(name, {}))
                                self.assertFalse(result.isError, result.content)
                                self.assertIsInstance(result.structuredContent, dict)
                                self.assertTrue(result.structuredContent["read_only"])
                                self.assertNotIn(SENTINEL, json.dumps(result.structuredContent))
                            # Strict rejection is enforced by the handler as well
                            # as the advertised JSON schema. No tool may add actions.
                            invalid = [
                                ("spiceowl_probe_host", {}), ("spiceowl_cancel_run", {}),
                                ("spiceowl_list_runs", {"directory": SENTINEL}),
                                ("spiceowl_list_hosts", {"ssh_target": SENTINEL}),
                                ("spiceowl_get_run", {"run_id": "../" + SENTINEL}),
                                ("spiceowl_get_run", {"run_id": 42}),
                                ("spiceowl_get_run", {"run_id": "missing"}),
                                ("spiceowl_get_run", {"run_id": "demo-disconnect", "command": SENTINEL}),
                                ("spiceowl_sync_ic_gateway", {"gateway_id": IC_CONFIG["id"]}),
                                ("spiceowl_lookup_ic_submission", {"gateway_id": IC_CONFIG["id"], "key": SENTINEL}),
                                ("spiceowl_list_ic_runs", {"gateway_id": "../" + SENTINEL}),
                                ("spiceowl_list_ic_runs", {"gateway_id": 42}),
                                ("spiceowl_list_ic_runs", {"gateway_id": "missing"}),
                                ("spiceowl_get_ic_run", {"gateway_id": IC_CONFIG["id"], "run_id": "../" + SENTINEL}),
                                ("spiceowl_get_ic_run", {"gateway_id": IC_CONFIG["id"], "run_id": 42}),
                                ("spiceowl_get_ic_run", {"gateway_id": IC_CONFIG["id"], "run_id": "fixture-native-run", "sync": True}),
                            ]
                            for name, arguments in invalid:
                                result = await client.call_tool(name, arguments)
                                self.assertTrue(result.isError, (name, result))
                                self.assertNotIn(SENTINEL, json.dumps(result.model_dump(mode="json")))

            asyncio.run(exercise())
            self.assertEqual(app.store.path.read_bytes(), before)
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()), entries)
            self.assertEqual(app.store.get_turn(turn["turn_id"])["status"], "running")
            # The parent controller remains able to write after stdio shutdown.
            self.assertEqual(app.advance("demo-disconnect")["observation"], "unknown")


if __name__ == "__main__":
    unittest.main()

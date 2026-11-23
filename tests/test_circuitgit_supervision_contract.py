"""Optional real CircuitGit producer -> loopback -> SpiceOwl cache/MCP contract.

Set CIRCUITGIT_ROOT to a reviewed local checkout. No machine-specific default is
used. All run/site data is authored inside a temporary directory. NODE_BINARY
may select a local Node executable for the shared browser validator check.
"""

import copy
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from spiceowl.domain.common import DomainError
from spiceowl.integrations.ic_workbench import ICGatewayBackend, LoopbackGatewayClient, validate_supervision
from spiceowl.mcp.views import ReadViews
from spiceowl.storage.readonly import ReadStore
from test_ic_execution_target import BAD_TIMES, FIXED, GOOD_TIMES, POOL, bad_targets
from test_ic_workbench import CONFIG, OBSERVED, run_fixture, snapshot_fixture


_PRODUCER = r'''
import json, sys, threading
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(sys.argv[1]) / 'tools/workbench'))
import runner, server, supervision
def forbidden(*args, **kwargs):
    raise AssertionError('A contract fixture must never perform an active operation')
runner.core.ssh = forbidden
site = {'schema':'ic.sim.site.v1','ssh_target':'fixture-route',
        'remote_runs_root':'/fixture/ic_on_github_runs','cadence_cshrc':'/fixture/setup',
        'spectre_bin':'/fixture/spectre','external_files':{},'expected_host':'fixture','expected_user':'fixture'}
run = runner.Runner(Path(sys.argv[2]), site)
run.resource_recheck = forbidden if sys.argv[3] == 'pool' else None
for name in ('dispatch', 'status', 'collect', 'postprocess', 'reserve', 'preflight'):
    setattr(run, name, forbidden)
projects = {'fixture-project': {'library_id':'CurrentRegistry', 'cell_id':'CurrentCell'}}
app = SimpleNamespace(supervision=supervision.Supervision(run, projects), ai=None)
http = server.Server(('127.0.0.1', 0), server.Handler)
http.app = app
thread = threading.Thread(target=http.serve_forever, daemon=True)
thread.start()
print(json.dumps({'port': http.server_port}), flush=True)
try:
    sys.stdin.readline()
finally:
    http.shutdown()
    http.server_close()
    thread.join(timeout=5)
'''


class CircuitGitSupervisionContractTests(unittest.TestCase):
    def setUp(self):
        configured = os.environ.get("CIRCUITGIT_ROOT")
        if not configured:
            self.skipTest("Set CIRCUITGIT_ROOT to run the optional cross-repository contract")
        self.producer_root = Path(configured).expanduser().resolve()
        self.assertTrue((self.producer_root / "tools/workbench/supervision.py").is_file(), "CIRCUITGIT_ROOT must be a CircuitGit checkout")
        self.tmp = tempfile.TemporaryDirectory(prefix="circuitgit-contract-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.runs = self.root / "runs"; self.runs.mkdir()
        self.base = {"schema": "ic.sim.run.v2", "project_id": CONFIG["project_id"], "source_commit": "a" * 40,
                     "configuration_id": "tran", "state": "simulator_complete_pending_measurements",
                     "remote_state": "simulator_complete_pending_measurements", "simulator_exit_code": 0,
                     "observed_at": OBSERVED, "updated_at": OBSERVED,
                     "plan": {"design_id": CONFIG["project_id"], "configuration": {}}, "synthetic": True,
                     "ssh_target": "PRIVATE_fixture_route", "remote_directory": "/PRIVATE/fixture/path"}
        self.pool_record = self.base | {"run_id": "fixture-pool", "library_id": "_HistoricalLibrary", "cell_id": "_" + "C" * 179,
            "execution_context": {"schema": "ic.execution.context.v1", "plan_id": POOL["plan_id"],
                "resource_selection": {"host_id": "h" * 96, "profile": {"ssh_target": "PRIVATE_fixture"},
                    "recommendation": {"candidates": [{"host_id": "h" * 96, "resource_observed_at": POOL["resource_observed_at"]}]}}}}
        self.put(self.base | {"run_id": "fixture-legacy"})
        self.put(self.base | {"run_id": "fixture-fixed", "execution_site": {"ssh_target": "PRIVATE_fixture"},
            "execution_context": {"schema": "ic.execution.context.v1", "resource_selection": None}})
        self.put(self.pool_record)
        (self.runs / "keys.json").write_text(json.dumps({"fixture-key": {"run_id": "fixture-pool", "fingerprint": "b" * 64}}))

    def put(self, record):
        path = self.runs / record["run_id"] / "run.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(record))

    def files(self):
        return {str(path.relative_to(self.runs)): (path.stat().st_mtime_ns, path.read_bytes())
                for path in self.runs.rglob("*") if path.is_file()}

    def gateway(self, mode="pool"):
        process = subprocess.Popen([sys.executable, "-B", "-c", _PRODUCER, str(self.producer_root), str(self.runs), mode],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        def stop():
            try:
                if process.poll() is None:
                    process.stdin.write("stop\n"); process.stdin.flush()
                    process.wait(timeout=5)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                process.kill(); process.wait(timeout=5)
            finally:
                process.stdin.close(); process.stdout.close(); process.stderr.close()
        self.addCleanup(stop)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            self.assertTrue(selector.select(timeout=10), "Fixture producer did not become ready")
        line = process.stdout.readline()
        if not line:
            process.wait(timeout=5)
            self.fail("Fixture producer exited before opening loopback: " + process.stderr.read(4096))
        port = json.loads(line)["port"]
        self.config = CONFIG | {"base_url": "http://127.0.0.1:" + str(port)}
        return LoopbackGatewayClient(self.config)

    def test_actual_producer_loopback_cache_and_mcp_preserve_target_and_history(self):
        before = self.files(); client = self.gateway()
        snapshot = client.supervision()
        validate_supervision(snapshot, CONFIG["project_id"])
        self.assertIs(snapshot["single_site"], False)
        runs = {row["run_id"]: row for row in snapshot["runs"]}
        self.assertNotIn("execution_target", runs["fixture-legacy"])
        self.assertNotIn("library_id", runs["fixture-legacy"])
        self.assertEqual(runs["fixture-fixed"]["execution_target"], FIXED)
        expected_target = POOL | {"host_id": "h" * 96}
        self.assertEqual(runs["fixture-pool"]["execution_target"], expected_target)
        self.assertEqual(runs["fixture-pool"]["library_id"], "_HistoricalLibrary")
        self.assertNotIn("PRIVATE", json.dumps(snapshot))
        backend = ICGatewayBackend(self.root / "cache"); backend.register(self.config)
        result = backend.refresh(CONFIG["id"]); self.assertTrue(result["updated"])
        lookup = backend.lookup_submission(CONFIG["id"], "fixture-key", {"execution_target": expected_target})
        self.assertTrue(lookup["found"]); self.assertFalse(lookup["resubmit_authorized"])
        cache_before = backend.store.path.read_bytes()
        with patch.object(LoopbackGatewayClient, "_get", side_effect=AssertionError("MCP must not query gateway")), \
                patch("spiceowl.storage.sqlite.Store.__init__", side_effect=AssertionError("MCP must remain read-only")):
            views = ReadViews(ReadStore(self.root / "cache"))
            listed = views.list_ic_runs(CONFIG["id"])["runs"]
            run = views.get_ic_run(CONFIG["id"], "fixture-pool")["run"]
        self.assertEqual(run["execution_target"], expected_target)
        self.assertEqual(run["latest_observed_at"], OBSERVED)
        self.assertEqual(run["source_commit"], "a" * 40)
        self.assertEqual(run["cell_id"], "_" + "C" * 179)
        self.assertTrue(run["synthetic"])
        self.assertEqual(run["measurement"]["state"], "indeterminate")
        self.assertNotIn("PRIVATE", json.dumps(listed))
        self.assertEqual(backend.store.path.read_bytes(), cache_before)
        self.assertEqual(self.files(), before)
        self.assertEqual((ROOT / "docs/contracts/ic-workbench-supervision.v1.json").read_bytes(),
                         (self.producer_root / "contracts/spiceowl-supervision.v1.json").read_bytes())

    def test_producer_rejects_damaged_time_and_preserves_prior_cache_mapping(self):
        self.gateway()
        backend = ICGatewayBackend(self.root / "cache"); backend.register(self.config)
        self.assertTrue(backend.refresh(CONFIG["id"])["updated"])
        backend.lookup_submission(CONFIG["id"], "fixture-key")
        for value in BAD_TIMES:
            record = copy.deepcopy(self.pool_record)
            record["execution_context"]["resource_selection"]["recommendation"]["candidates"][0]["resource_observed_at"] = value
            self.put(record)
            with self.subTest(value=value):
                result = backend.refresh(CONFIG["id"])
                self.assertTrue(result["updated"])
                self.assertEqual(result["snapshot"]["unavailable_records"], 1)
                run = next(row for row in result["runs"] if row["run_id"] == "fixture-pool")
                self.assertEqual(run["observation"]["state"], "unobserved")
                self.assertEqual(run["execution_target"]["resource_observed_at"], POOL["resource_observed_at"])
                lookup = backend.lookup_submission(CONFIG["id"], "fixture-key")
                self.assertEqual(lookup["observation"]["state"], "unobserved")
                self.assertTrue(lookup["found"])
                self.assertFalse(lookup["resubmit_authorized"])

    def test_real_frontend_validator_matches_nullable_time_and_identity_bounds(self):
        node = os.environ.get("NODE_BINARY") or shutil.which("node")
        if not node:
            self.skipTest("Set NODE_BINARY or install Node to check CircuitGit's shared browser validator")
        valid = [snapshot_fixture(run_fixture())]
        valid += [snapshot_fixture(run_fixture() | {"execution_target": target}) | {"single_site": False}
                  for target in (FIXED, *(POOL | {"resource_observed_at": value} for value in GOOD_TIMES),
                                 POOL | {"host_id": "h" * 81}, POOL | {"host_id": "h" * 96})]
        invalid = [snapshot_fixture(run_fixture() | {"execution_target": target}) for target in bad_targets()]
        script = """import fs from 'node:fs';
const {normalizeSupervisionSnapshot} = await import(process.argv[1]);
const data = JSON.parse(fs.readFileSync(0, 'utf8'));
for (const [kind, cases] of Object.entries(data)) for (const snapshot of cases) {
  let accepted = false;
  try { normalizeSupervisionSnapshot(snapshot, 'fixture-project'); accepted = true; } catch {}
  if (accepted !== (kind === 'valid')) throw Error('Browser contract mismatch: '+JSON.stringify(snapshot));
}
console.log('CircuitGit browser contract cases passed');
"""
        result = subprocess.run([node, "--input-type=module", "-e", script,
            (self.producer_root / "modules/simulation/supervision.js").as_uri()],
            input=json.dumps({"valid": valid, "invalid": invalid}), text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    unittest.main()

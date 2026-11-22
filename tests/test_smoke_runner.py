"""Synthetic runner evidence only: no SSH, Spectre, PDK or real collection.

The authored example sources exercise byte identity. Every process, terminal
receipt, log and raw artifact used here is an explicit offline fixture.
"""

import base64
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from spiceowl.domain.common import DomainError


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("smoke_runner_under_test", ROOT / "scripts/run_inverter_smoke.py")
runner = importlib.util.module_from_spec(SPEC)
with patch.object(sys, "path", [str(ROOT / "scripts"), *sys.path]):
    SPEC.loader.exec_module(runner)

PROFILE = {
    "schema": "spiceowl.host.v2", "host_id": "fixture-node", "label": "Synthetic route",
    "ssh_target": "fixture-route", "ssh_hostname": "fixture-node", "expected_host": "fixture-node",
    "expected_user": "fixture-user", "tools": [{"kind": "spectre", "executable": "/opt/example/spectre"}],
}


def json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def make_bundle(folder):
    source = folder / "source"
    source.mkdir(parents=True)
    for name in (*runner.SOURCES, "acceptance.json"):
        (source / name).write_bytes((runner.EXAMPLE / name).read_bytes())
    sources = {name: (source / name).read_bytes() for name in runner.SOURCES}
    compiled = runner.flattened(sources)
    plan = runner.transport.prepare(PROFILE, "fixture-run", compiled, "99.1.0.123",
                                    remote_python="/opt/example/bin/python3.6")
    provenance = {
        "schema": "spiceowl.inverter.bundle.v1", "source_origin": "synthetic_test_fixture",
        "source_sha256": {name: runner.sha(raw) for name, raw in sources.items()},
        "acceptance_sha256": runner.sha((source / "acceptance.json").read_bytes()),
        "flattened_sha256": runner.sha(compiled.encode()),
        "transport_manifest_sha256": plan["manifest_sha256"], "data_directory": str(folder.parent),
    }
    (folder / "plan.json").write_bytes(json_bytes(plan))
    (folder / "provenance.json").write_bytes(json_bytes(provenance))
    return plan


def synthetic_collection(plan):
    terminal = {"state": "completed", "terminal": True, "exit_code": 0,
                "manifest_sha256": plan["manifest_sha256"], "fixture": "synthetic_no_solver_run"}
    contents = {
        "input.scs": base64.b64decode(plan["files"]["input.scs"]),
        "manifest.json": json_bytes(plan["manifest"]),
        "status.json": json_bytes(terminal),
        "spectre.log": b"SYNTHETIC TEST LOG: spectre completes with 0 errors, 0 warnings.\n",
        "results.raw/tran.tran": b"SYNTHETIC PLACEHOLDER: no real simulation or waveform data\n",
    }
    return {
        "manifest_sha256": plan["manifest_sha256"], "fixture": "synthetic_collection",
        "receipt": {"state": "completed", "terminal": True, "result": terminal,
                    "manifest_sha256": plan["manifest_sha256"]},
        "files": [{"name": name, "bytes": len(raw), "sha256": runner.sha(raw),
                   "data_base64": base64.b64encode(raw).decode("ascii")} for name, raw in contents.items()],
        "total_bytes": sum(map(len, contents.values())), "raw_present": True,
    }


def contents_of(collection):
    return {item["name"]: base64.b64decode(item["data_base64"]) for item in collection["files"]}


class SmokeRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.no_process = patch("subprocess.Popen", side_effect=AssertionError("Offline runner tests must not start processes"))
        self.no_process.start()

    def tearDown(self):
        self.no_process.stop()
        self.temp.cleanup()

    def test_original_fixture_bundle_matches_its_frozen_flattened_input(self):
        folder = self.root / "valid"
        plan = make_bundle(folder)
        acceptance, hashes = runner.verify_bundle(folder, plan)
        self.assertEqual(hashes, acceptance["input_sha256"])
        self.assertEqual(runner.sha(base64.b64decode(plan["files"]["input.scs"])),
                         json.loads((folder / "provenance.json").read_text())["flattened_sha256"])

    def test_changed_source_acceptance_or_mapping_is_rejected_before_stage(self):
        for field in ("source", "acceptance", "mapping", "flattened_plan", "transport_limits"):
            with self.subTest(field=field):
                folder = self.root / field
                plan = make_bundle(folder)
                if field == "source":
                    source = folder / "source/models.scs"
                    source.write_bytes(source.read_bytes() + b"// changed fixture\n")
                elif field == "acceptance":
                    path = folder / "source/acceptance.json"
                    value = json.loads(path.read_text())
                    value["settled_logic"]["low_max_v"] = 0.5
                    path.write_bytes(json_bytes(value))
                elif field == "mapping":
                    path = folder / "provenance.json"
                    value = json.loads(path.read_text())
                    value["flattened_sha256"] = "0" * 64
                    path.write_bytes(json_bytes(value))
                elif field == "flattened_plan":
                    plan["files"]["input.scs"] = base64.b64encode(b"changed fixture netlist").decode("ascii")
                    (folder / "plan.json").write_bytes(json_bytes(plan))
                else:
                    plan["manifest"]["limits"]["cpu_count"] = 2
                    (folder / "plan.json").write_bytes(json_bytes(plan))
                with patch.object(runner.transport, "stage") as stage:
                    with self.assertRaises((ValueError, DomainError)):
                        runner.operate(SimpleNamespace(operation="stage", run_dir=folder))
                    stage.assert_not_called()

    def test_flatten_rejects_duplicate_missing_and_external_includes(self):
        sources = {name: (runner.EXAMPLE / name).read_bytes() for name in runner.SOURCES}
        bad = [sources | {"tb.scs": sources["tb.scs"] + b'\ninclude "models.scs"\n'},
               sources | {"tb.scs": sources["tb.scs"].replace(b'include "models.scs"', b"// missing model include")},
               sources | {"models.scs": sources["models.scs"] + b'\ninclude "external.scs"\n'}]
        for candidate in bad:
            with self.assertRaises(ValueError):
                runner.flattened(candidate)

    def test_exit_zero_without_solver_completion_log_stays_unknown(self):
        plan = make_bundle(self.root / "exit-zero")
        collection = synthetic_collection(plan)
        contents = contents_of(collection)
        self.assertEqual(runner.execution_outcome(collection, contents), "completed")
        for log in (None, b"SYNTHETIC FIXTURE: solver exited but completion is absent\n",
                    b"SYNTHETIC FIXTURE: spectre completes with 1 error\n"):
            candidate = contents.copy()
            if log is None:
                candidate.pop("spectre.log")
            else:
                candidate["spectre.log"] = log
            self.assertEqual(runner.execution_outcome(collection, candidate), "unknown")

    def test_terminal_receipt_disagreement_is_rejected_before_analysis(self):
        folder = self.root / "receipt-conflict"
        plan = make_bundle(folder)
        collection = synthetic_collection(plan)
        collection["receipt"]["result"] = collection["receipt"]["result"] | {"exit_code": 1}
        with self.assertRaises(ValueError):
            runner.execution_outcome(collection, contents_of(collection))
        with patch.object(runner.transport, "collect", return_value=collection), patch.object(runner.results, "parse_psf_ascii") as parse:
            with self.assertRaises((ValueError, DomainError)):
                runner.operate(SimpleNamespace(operation="collect", run_dir=folder, plots=False))
            parse.assert_not_called()

    def test_collection_rehashed_input_or_manifest_still_cannot_change_frozen_mapping(self):
        for target in ("input.scs", "manifest.json"):
            with self.subTest(target=target):
                folder = self.root / target.replace(".", "-")
                plan = make_bundle(folder)
                collection = synthetic_collection(plan)
                runner.transport._verify_collection(plan, collection)
                changed = deepcopy(collection)
                for item in changed["files"]:
                    if item["name"] == target:
                        raw = base64.b64decode(item["data_base64"]) + b"\nchanged synthetic artifact\n"
                        item.update(bytes=len(raw), sha256=runner.sha(raw), data_base64=base64.b64encode(raw).decode("ascii"))
                changed["total_bytes"] = sum(item["bytes"] for item in changed["files"])
                with patch.object(runner.transport, "collect", return_value=changed), patch.object(runner.results, "parse_psf_ascii") as parse:
                    with self.assertRaises((ValueError, DomainError)):
                        runner.operate(SimpleNamespace(operation="collect", run_dir=folder, plots=False))
                    parse.assert_not_called()

    def test_source_change_during_collection_cannot_enter_validation(self):
        folder = self.root / "changed-during-collection"
        plan = make_bundle(folder)
        collection = synthetic_collection(plan)
        def collect(_):
            source = folder / "source/models.scs"
            source.write_bytes(source.read_bytes() + b"// synthetic mid-collection change\n")
            return collection
        with patch.object(runner.transport, "collect", side_effect=collect), patch.object(runner.results, "parse_psf_ascii") as parse:
            with self.assertRaises((ValueError, DomainError)):
                runner.operate(SimpleNamespace(operation="collect", run_dir=folder, plots=False))
            parse.assert_not_called()
        self.assertEqual(list(folder.glob("*-collected/analysis/validation.json")), [])

    def test_synthetic_transient_catalog_selects_one_exact_artifact(self):
        def catalog(*filenames):
            return ("// SYNTHETIC native catalog fixture\n" + "\n".join(
                f'"fixture-{index}" "analysisInst" ("tran" "{name}" "PSF")'
                for index, name in enumerate(filenames))).encode("ascii")
        for filename in ("tran.tran", "tran.tran.tran"):
            path = "results.raw/" + filename
            content = {path: b"synthetic PSF placeholder; not a real waveform"}
            with self.subTest(filename=filename):
                self.assertEqual(runner.transient_file(content), path)
                self.assertEqual(runner.transient_file(content | {"results.raw/logFile": catalog(filename)}), path)
                for wrong in (catalog("wrong.tran"), catalog(filename, filename), catalog(filename, "another.tran"),
                              b'"fixture" "analysisInst" ("dc" "dc.dc" "PSF")'):
                    with self.assertRaises(ValueError):
                        runner.transient_file(content | {"results.raw/logFile": wrong})
        with self.assertRaises(ValueError):
            runner.transient_file({"results.raw/tran.tran": b"synthetic A", "results.raw/tran.tran.tran": b"synthetic B",
                                   "results.raw/logFile": catalog("tran.tran")})
        self.assertIsNone(runner.transient_file({}))
        self.assertIsNone(runner.transient_file({"results.raw/logFile": catalog("tran.tran")}))


if __name__ == "__main__":
    unittest.main()

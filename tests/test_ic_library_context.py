"""CircuitGit metadata interoperability; all evidence and transports are synthetic."""

import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from spiceowl.cli.main import main
from spiceowl.domain.common import DomainError
from spiceowl.integrations.ic_workbench import (
    ICGatewayBackend, LoopbackGatewayClient, validate_submission, validate_supervision,
)
from spiceowl.mcp.views import ReadViews
from spiceowl.storage.readonly import ReadStore
from test_ic_workbench import CONFIG, Response, lookup_fixture, run_fixture, snapshot_fixture


ROOT = Path(__file__).resolve().parents[1]
CONTEXT = {"library_id": "Example_ADC", "cell_id": "tb.inverter-1"}


class ICLibraryContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backend = ICGatewayBackend(self.temp.name)
        self.backend.register(CONFIG)

    def sync(self, *runs):
        with patch.object(LoopbackGatewayClient, "supervision", return_value=snapshot_fixture(*runs)):
            return self.backend.refresh(CONFIG["id"])

    def test_public_example_crosses_transport_cache_cli_and_readonly_mcp(self):
        snapshot = json.loads((ROOT / "examples/ic-library-context.snapshot.json").read_text())
        source = snapshot["runs"][0]
        raw = json.dumps({"ok": True, "data": snapshot}).encode()
        connection = MagicMock()
        connection.getresponse.return_value = Response(raw)
        with patch("spiceowl.integrations.ic_workbench.http.client.HTTPConnection", return_value=connection):
            result = self.backend.refresh(CONFIG["id"])
        self.assertTrue(result["updated"])
        self.assertEqual(connection.request.call_args.args, ("GET", "/api/supervision?project_id=fixture-project"))
        connection.request.assert_called_once()
        with patch.object(LoopbackGatewayClient, "_get", side_effect=AssertionError("Network")), \
                patch("subprocess.Popen", side_effect=AssertionError("External process")):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["--data-dir", self.temp.name, "ic", "inspect", CONFIG["id"], source["run_id"]])
            self.assertEqual(code, 0)
            cli = json.loads(output.getvalue())
            before = self.backend.store.path.read_bytes()
            with patch("spiceowl.storage.sqlite.Store.__init__", side_effect=AssertionError("Writable store")):
                views = ReadViews(ReadStore(self.temp.name))
                listed = views.list_ic_runs(CONFIG["id"])["runs"][0]
                detail = views.get_ic_run(CONFIG["id"], source["run_id"])["run"]
        for record in (result["runs"][0], cli, listed, detail):
            for key in ("library_id", "cell_id", "run_id", "project_id", "original_project_id", "source_commit"):
                self.assertEqual(record[key], source[key])
            self.assertEqual(record["measurement"]["state"], "indeterminate")
            self.assertTrue(record["synthetic"])
            self.assertEqual(record["observation"]["observed_at"], source["latest_observed_at"])
        self.assertEqual(self.backend.store.path.read_bytes(), before)

    def test_legacy_null_and_independent_context_fields_round_trip_without_inference(self):
        for i, fields in enumerate(({}, {"library_id": None, "cell_id": None},
                                    {"library_id": CONTEXT["library_id"]}, {"cell_id": CONTEXT["cell_id"]},
                                    {"library_id": "_" + "A" * 179, "cell_id": "B" * 180})):
            source = run_fixture(f"fixture-{i}") | fields
            validated = validate_supervision(snapshot_fixture(source), CONFIG["project_id"])
            self.assertEqual(validated["runs"][0], source)
            self.assertTrue(self.sync(source)["updated"])
            # A new reader of existing storage needs no schema migration.
            record = ReadViews(ReadStore(self.temp.name)).get_ic_run(CONFIG["id"], source["run_id"])["run"]
            self.assertEqual({key: record[key] for key in CONTEXT if key in record}, fields)

    def test_old_cache_can_gain_explicit_context_but_known_context_cannot_be_rebound(self):
        source = run_fixture()
        self.assertTrue(self.sync(source)["updated"])
        source |= {"library_id": None, "cell_id": None}
        self.assertTrue(self.sync(source)["updated"])
        source |= CONTEXT
        self.assertTrue(self.sync(source)["updated"])
        old = self.backend.run(CONFIG["id"], source["run_id"])
        candidates = []
        for key in CONTEXT:
            for value in (None, "different"):
                candidates.append(source | {key: value})
            missing = copy.deepcopy(source)
            missing.pop(key)
            candidates.append(missing)
        candidates.append(source | {"source_commit": "b" * 40})
        for changed in candidates:
            with self.subTest(changed=changed):
                result = self.sync(run_fixture("new-run"), changed)
                self.assertFalse(result["updated"])
                self.assertEqual(result["gateway"]["observation"]["error"]["code"], "ic_gateway_identity")
                self.assertEqual(len(result["runs"]), 1)
                saved = result["runs"][0]
                for key in (*CONTEXT, "source_commit", "execution", "data", "measurement", "specification"):
                    self.assertEqual(saved[key], old[key])
                self.assertEqual(saved["observation"]["state"], "unobserved")

    def test_invalid_ids_and_unrelated_extra_fields_are_rejected(self):
        for key in CONTEXT:
            for value in ("", "A" * 181, "电路", "../private", "lib/cell", "lib\\cell", "https://example.invalid",
                          "id\n", "id\x00", "id\x7f", " id", "id ", "id\ud800", 1, True, [], {}):
                with self.subTest(key=key, value=repr(value)), self.assertRaises(DomainError):
                    validate_supervision(snapshot_fixture(run_fixture() | {key: value}), CONFIG["project_id"])
        for candidate in (snapshot_fixture(run_fixture()) | CONTEXT,
                          snapshot_fixture(run_fixture() | CONTEXT | {"source_path": "lib/private"})):
            with self.assertRaises(DomainError):
                validate_supervision(candidate, CONFIG["project_id"])

    def test_lookup_checks_optional_identity_and_never_authorizes_resubmission(self):
        source = run_fixture() | CONTEXT
        lookup = lookup_fixture(run=source)
        self.assertEqual(validate_submission(lookup, "fixture-key", CONFIG["project_id"], CONTEXT), lookup)
        for expected in ({"cell_id": "different"}, {"library_id": None}):
            with self.assertRaises(DomainError) as raised:
                validate_submission(lookup, "fixture-key", CONFIG["project_id"], expected)
            self.assertEqual(raised.exception.code, "ic_gateway_identity")
        legacy = lookup_fixture(run=run_fixture())
        with self.assertRaises(DomainError):
            validate_submission(legacy, "fixture-key", CONFIG["project_id"], CONTEXT)
        validate_submission(legacy, "fixture-key", CONFIG["project_id"], {"library_id": None})
        with patch.object(LoopbackGatewayClient, "submission", return_value=lookup):
            result = self.backend.lookup_submission(CONFIG["id"], "fixture-key", CONTEXT)
        self.assertTrue(result["found"])
        self.assertFalse(result["resubmit_authorized"])
        self.assertEqual(result["run"]["cell_id"], CONTEXT["cell_id"])
        with patch.object(LoopbackGatewayClient, "submission", return_value=legacy):
            failed = self.backend.lookup_submission(CONFIG["id"], "fixture-key")
        self.assertEqual(failed["observation"]["state"], "unobserved")
        self.assertEqual(failed["run"]["library_id"], CONTEXT["library_id"])

    def test_mcp_rejects_damaged_context_without_leaking_or_truncating_ids(self):
        source = run_fixture() | CONTEXT
        self.sync(source)
        views = ReadViews(ReadStore(self.temp.name))
        for key in CONTEXT:
            for value in ("PRIVATE/secret", "A" * 181, "id\n", 42):
                self.backend.store.update("ic_run:" + CONFIG["id"], source["run_id"], lambda record: record | CONTEXT | {key: value})
                for read in (lambda: views.list_ic_runs(CONFIG["id"]), lambda: views.get_ic_run(CONFIG["id"], source["run_id"])):
                    with self.subTest(key=key, value=value), self.assertRaises(DomainError) as raised:
                        read()
                    self.assertEqual(raised.exception.code, "storage_record")
                    self.assertNotIn(str(value), str(raised.exception))

    def test_published_json_schema_accepts_legacy_and_extension_and_rejects_bad_context(self):
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("jsonschema is available with the optional MCP SDK")
        schema = json.loads((ROOT / "docs/contracts/ic-workbench-supervision.v1.json").read_text())
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        for fields in ({}, {"library_id": None}, CONTEXT):
            source = run_fixture() | fields
            validator.validate(snapshot_fixture(source))
            validator.validate(lookup_fixture(run=source))
        for fields in ({"library_id": "A" * 181}, {"cell_id": "../x"}, {"cell_id": 1}, {"cell_id": "id\n"}):
            self.assertFalse(validator.is_valid(snapshot_fixture(run_fixture() | fields)))


if __name__ == "__main__":
    unittest.main()

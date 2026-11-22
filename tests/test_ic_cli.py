"""Public CLI tests for IC read integration; no SSH, solver, or external host."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from spiceowl.cli.main import main, parser
from spiceowl.integrations.ic_workbench import ICGatewayBackend, LoopbackGatewayClient


class ICCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.profile = self.directory / "gateway.json"
        self.profile.write_text(json.dumps({"id": "fixture-ic", "label": "Synthetic gateway",
            "base_url": "http://127.0.0.1:12345", "project_id": "fixture-project"}))

    def tearDown(self):
        self.temp.cleanup()

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--data-dir", str(self.directory), "ic", *args])
        return code, json.loads(out.getvalue()) if out.getvalue() else None, err.getvalue()

    def test_registration_is_idempotent_and_local_listing_never_reads_gateway(self):
        with patch.object(LoopbackGatewayClient, "_get", side_effect=AssertionError("No network")):
            code, first, _ = self.call("register", "--file", str(self.profile))
            self.assertEqual(code, 0)
            self.assertTrue(first["created"])
            _, second, _ = self.call("register", "--file", str(self.profile))
            self.assertFalse(second["created"])
            _, listing, _ = self.call("list")
            self.assertEqual([r["id"] for r in listing], ["fixture-ic"])
            self.assertNotIn("base_url", listing[0])
            self.assertEqual(self.call("runs", "fixture-ic")[1], [])
            self.assertFalse((self.directory / ".controller.lock").exists())

    def test_sync_failure_and_unknown_lookup_are_machine_readable_failures(self):
        with patch.object(ICGatewayBackend, "refresh", return_value={"updated": False, "runs": [],
                "gateway": {"observation": {"state": "unobserved"}}}) as sync:
            code, output, _ = self.call("sync", "fixture-ic")
            self.assertEqual(code, 1)
            self.assertFalse(output["updated"])
            sync.assert_called_once_with("fixture-ic")
        with patch.object(ICGatewayBackend, "lookup_submission", return_value={"found": None,
                "resubmit_authorized": False, "observation": {"state": "unobserved"}}):
            code, output, _ = self.call("lookup", "fixture-ic", "fixture-key")
            self.assertEqual(code, 1)
            self.assertFalse(output["resubmit_authorized"])

    def test_confirmed_key_miss_is_a_successful_read_without_launch_permission(self):
        with patch.object(ICGatewayBackend, "lookup_submission", return_value={"found": False,
                "run_id": None, "resubmit_authorized": False, "observation": {"state": "readable"}}):
            code, output, _ = self.call("lookup", "fixture-ic", "fixture-key")
            self.assertEqual(code, 0)
            self.assertFalse(output["found"])
            self.assertFalse(output["resubmit_authorized"])

    def test_cli_has_no_ic_submit_approve_or_collect_action(self):
        for action in ("submit", "approve", "collect", "cancel"):
            with self.subTest(action=action), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    parser().parse_args(["ic", action, "fixture-ic"])


if __name__ == "__main__":
    unittest.main()

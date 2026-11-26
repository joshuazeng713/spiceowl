"""Tests for CircuitGit-shared SSH Library Manager, netlist import, and entry handoff."""

from __future__ import annotations

import base64
import http.client
import json
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen

from spiceowl.service import Controller
from spiceowl.domain.common import DomainError
from spiceowl.onboarding.ssh_import import (
    BROWSE_SCHEMA,
    CIRCUITGIT_ENTRY_SCHEMA,
    IMPORT_SCHEMA,
    browse_remote_netlists,
    fetch_remote_netlist,
    is_circuit_filename,
    is_sensitive_name,
    validate_circuitgit_entry,
    validate_remote_path,
    validate_ssh_target,
)
from spiceowl.web.server import make_server


def _local_python_runner(argv, *, input=b"", capture_output=True, timeout=25):
    """Execute the framed remote /bin/sh -s script locally to test _REMOTE_PY end-to-end."""
    return subprocess.run(
        ["/bin/sh", "-s"],
        input=input,
        capture_output=capture_output,
        timeout=timeout,
    )


class TestSshImportValidation(unittest.TestCase):
    def test_validates_ssh_target_and_blocks_options(self):
        self.assertEqual(validate_ssh_target("sim-node-a"), "sim-node-a")
        self.assertEqual(validate_ssh_target("alice@sim-node-a.example.invalid"), "alice@sim-node-a.example.invalid")
        for bad in ("", "-oProxyCommand=evil", "host with space", "host;rm -rf /", "a" * 241):
            with self.assertRaises(DomainError):
                validate_ssh_target(bad)

    def test_blocks_sensitive_names_and_paths(self):
        for bad in (".ssh", "id_ed25519", "my.pem", "flexlm.lic", ".env", "api_token.scs", "secret_key.txt"):
            self.assertTrue(is_sensitive_name(bad), bad)
            self.assertFalse(is_circuit_filename(bad), bad)
        for bad_path in ("relative/path.scs", "/tmp/../etc/passwd", "~/.ssh/config", "/work/secret_netlist.scs"):
            with self.assertRaises(DomainError):
                validate_remote_path(bad_path, allow_empty=False)
        self.assertEqual(validate_remote_path("~/projects/top.scs", require_circuit_ext=True), "~/projects/top.scs")
        with self.assertRaises(DomainError):
            validate_remote_path("~/projects/notes.pdf", require_circuit_ext=True)


class TestSshBrowseAndImportExecution(unittest.TestCase):
    def test_browse_lists_dirs_netlists_and_parses_cds_lib(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lib_dir = root / "adc_lib"
            lib_dir.mkdir()
            (root / "tb_top.scs").write_text("simulator lang=spectre\n", encoding="utf-8")
            (root / "sub.sp").write_text("* sub\n", encoding="utf-8")
            (root / "ignore.pdf").write_text("pdf", encoding="utf-8")
            (root / "id_rsa").write_text("PRIVATE", encoding="utf-8")
            (root / ".hidden").mkdir()
            (root / "cds.lib").write_text(
                "-- Cadence library list\n"
                f"DEFINE adc_lib {lib_dir}\n"
                "DEFINE missing_lib /nonexistent/path\n",
                encoding="utf-8",
            )

            res = browse_remote_netlists("sim-node-a", None, str(root), runner=_local_python_runner)
            self.assertEqual(res["schema"], BROWSE_SCHEMA)
            self.assertEqual(res["ssh_target"], "sim-node-a")
            self.assertEqual(res["cwd"], str(root.resolve()))
            names = [e["name"] for e in res["entries"]]
            self.assertEqual(names, ["adc_lib", "tb_top.scs", "sub.sp"])
            self.assertNotIn("id_rsa", names)
            self.assertNotIn(".hidden", names)
            self.assertNotIn("ignore.pdf", names)
            self.assertEqual(res["cds_libraries"], [{"name": "adc_lib", "path": str(lib_dir.resolve())}])
            self.assertEqual(res["netlist_count"], 2)

    def test_import_fetches_netlist_and_recursive_relative_includes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sub_dir = root / "blocks"
            sub_dir.mkdir()
            (sub_dir / "stage1.scs").write_text(
                "subckt stage1 in out\nR1 (in out) resistor r=1k\nends stage1\n",
                encoding="utf-8",
            )
            (root / "top.scs").write_text(
                'simulator lang=spectre\ninclude "blocks/stage1.scs"\ninclude "/opt/pdk/external.scs"\n',
                encoding="utf-8",
            )

            res = fetch_remote_netlist(
                "sim-node-a", None, str(root / "top.scs"), include_dependencies=True, runner=_local_python_runner
            )
            self.assertEqual(res["schema"], IMPORT_SCHEMA)
            self.assertEqual(res["file_name"], "top.scs")
            self.assertIn("include \"blocks/stage1.scs\"", res["text"])
            self.assertEqual(list(res["support_files"].keys()), ["blocks/stage1.scs"])
            self.assertIn("subckt stage1", res["support_files"]["blocks/stage1.scs"])


class TestCircuitGitEntryContractAndHttp(unittest.TestCase):
    def test_validate_circuitgit_entry_accepts_valid_and_rejects_bad(self):
        valid = validate_circuitgit_entry({
            "schema": CIRCUITGIT_ENTRY_SCHEMA,
            "source": "circuitgit",
            "project_id": "pipetisar-real",
            "project_name": "1G 12-bit PIPE-TI-SAR ADC",
            "library_id": "pipetisar-real",
            "cell_id": "TB_PIPE_TOP",
            "entry_path": "TB_PIPE_TOP.scs",
            "netlist_text": "simulator lang=spectre\nR1 (in out) resistor r=1k\n",
            "support_files": {"sub/block.scs": "subckt b a\nends b\n"},
            "simulator": "spectre",
            "source_commit": "a" * 40,
            "return_url": "https://circuitgit-dev.github.io/CircuitGit/",
        })
        self.assertEqual(valid["file_name"], "TB_PIPE_TOP.scs")
        self.assertEqual(list(valid["support_files"].keys()), ["sub/block.scs"])

        with self.assertRaises(DomainError):
            validate_circuitgit_entry({"netlist_text": ""})
        with self.assertRaises(DomainError):
            validate_circuitgit_entry({"netlist_text": "ok", "support_files": {"../escape.scs": "x"}})
        with self.assertRaises(DomainError):
            validate_circuitgit_entry({"netlist_text": "ok", "return_url": "javascript:alert(1)"})

    def test_http_endpoints_for_circuitgit_entry_and_support_files_prepare(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctrl = Controller(Path(tmp) / "store")
            srv = make_server(ctrl, port=0)
            thread = threading.Thread(target=srv.serve_forever, daemon=True)
            thread.start()
            try:
                port = srv.server_address[1]
                base = f"http://127.0.0.1:{port}"
                with urlopen(f"{base}/api/state", timeout=5) as resp:
                    csrf = json.loads(resp.read().decode("utf-8"))["csrf_token"]

                def req(method, path, body=None):
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                    headers = {"Origin": base, "X-SpiceOwl-Token": csrf}
                    data = None
                    if body is not None:
                        headers["Content-Type"] = "application/json"
                        data = json.dumps(body).encode("utf-8")
                    conn.request(method, path, body=data, headers=headers)
                    resp = conn.getresponse()
                    raw = resp.read().decode("utf-8")
                    conn.close()
                    return resp.status, json.loads(raw)

                st, data = req("GET", "/api/library/circuitgit-entry")
                self.assertEqual(st, 200)
                self.assertFalse(data["active"])

                st, data = req("POST", "/api/library/circuitgit-entry", {
                    "project_id": "demo-proj",
                    "cell_id": "inv_tb",
                    "entry_path": "inv_tb.scs",
                    "netlist_text": "simulator lang=spectre\ninclude \"inv.scs\"\n",
                    "support_files": {"inv.scs": "subckt inv in out\nends inv\n"},
                    "simulator": "spectre",
                })
                self.assertEqual(st, 200)
                self.assertTrue(data["active"])
                self.assertEqual(data["entry"]["cell_id"], "inv_tb")

                st, data = req("GET", "/api/library/circuitgit-entry")
                self.assertEqual(st, 200)
                self.assertTrue(data["active"])
                self.assertEqual(data["entry"]["file_name"], "inv_tb.scs")

                # Register a host and verify /api/direct/prepare forwards support_files
                st, _ = req("POST", "/api/hosts", {
                    "schema": "spiceowl.host.v1",
                    "host_id": "sim-node-a",
                    "label": "Sim Node A",
                    "ssh_target": "sim-node-a",
                    "expected_host": "sim-node-a",
                    "expected_user": "researcher",
                    "tools": [{"kind": "spectre", "executable": "/opt/cadence/bin/spectre"}],
                })
                self.assertEqual(st, 201)

                st, prep = req("POST", "/api/direct/prepare", {
                    "run_key": "cg-handoff-01",
                    "host_id": "sim-node-a",
                    "simulator": "spectre",
                    "netlist_text": 'simulator lang=spectre\ninclude "inv.scs"\n',
                    "support_files": {"inv.scs": "subckt inv in out\nends inv\n"},
                })
                self.assertEqual(st, 201)
                self.assertEqual(prep["run"]["run_id"], "cg-handoff-01")

                st, data = req("DELETE", "/api/library/circuitgit-entry")
                self.assertEqual(st, 200)
                self.assertFalse(data["active"])
            finally:
                srv.shutdown()
                srv.server_close()
                ctrl.close()


if __name__ == "__main__":
    unittest.main()

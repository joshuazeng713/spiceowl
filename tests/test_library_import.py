"""Tests for CircuitGit-shared SSH Library Manager, netlist import, and entry handoff."""

from __future__ import annotations

import base64
import http.client
import json
from pathlib import Path
import re
import subprocess
import sys
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
    SCHEMATIC_EXPORT_SCHEMA,
    browse_remote_netlists,
    export_remote_schematic,
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


@unittest.skipIf(sys.platform == "win32", "Local POSIX /bin/sh remote-script execution tests require POSIX")
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

    def test_browse_and_export_virtuoso_schematic_with_and_without_maestro(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lib_dir = root / "my_adc_lib"
            cell_dir = lib_dir / "tb_amp"
            sch_dir = cell_dir / "schematic"
            maestro_dir = cell_dir / "maestro"
            bin_dir = root / "fakebin"
            sch_dir.mkdir(parents=True)
            maestro_dir.mkdir(parents=True)
            bin_dir.mkdir()

            (root / "cds.lib").write_text(f"DEFINE my_adc_lib {lib_dir}\n", encoding="utf-8")
            (sch_dir / "sch.oa").write_bytes(b"OA_BINARY_STUB")
            (maestro_dir / "maestro.sdb").write_text(
                """<?xml version="1.0"?>
<setupdb version="6">maestro
  <active>Active Setup
    <corners>
      <corner enabled="1">CTT
        <vars><var>temperature<value>-40 27 125</value></var></vars>
        <models><model enabled="1">m.scs<modelfile>/opt/pdk/models.scs</modelfile><modelsection>"tt"</modelsection></model></models>
      </corner>
      <corner enabled="1">CSS
        <vars><var>temperature<value>-40 27 125</value></var></vars>
      </corner>
    </corners>
    <tests>
      <test enabled="1">tb_amp_test
        <tooloptions>
          <option>sim<value>spectre</value></option>
          <option>cell<value>tb_amp</value></option>
        </tooloptions>
        <vars>
          <var>vdd<value>1.2</value></var>
          <var>vin<value>1m 5m 10m</value></var>
        </vars>
      </test>
    </tests>
  </active>
</setupdb>
""",
                encoding="utf-8",
            )
            (maestro_dir / "active.state").write_text(
                """<?xml version="1.0"?>
<statedb version="5">active
  <Test Name="tb_amp_test">
    <component Name="analyses" Type="skillTable">asiEnvSaveTable
      <analyses Name="analysis">
        <analysis Name="tran">
          <partition Name="fields">
            <field Name="enable" Type="list">(t)</field>
            <field Name="stop" Type="string">"50n"</field>
            <field Name="errpreset" Type="string">"conservative"</field>
          </partition>
        </analysis>
        <analysis Name="dc">
          <partition Name="fields">
            <field Name="enable" Type="list">(t)</field>
            <field Name="saveOppoint" Type="symbol">t</field>
          </partition>
        </analysis>
      </analyses>
    </component>
    <component Name="modelSetup" Type="skillTable">modelSetup
      <field Name="modelFiles" Type="list">(("/opt/pdk/models.scs" "tt_mos"))</field>
    </component>
    <component Name="simulatorOptions" Type="skillTable">asiEnvSaveTable
      <partition Name="opts">
        <field Name="temp" Type="string">"27"</field>
        <field Name="reltol" Type="string">"1e-4"</field>
      </partition>
    </component>
    <component Name="outputs" Type="skillTable">outputs
      <partition Name="outputsCommon">
        <field Name="outputList" Type="skillList">
          <field Name="outputList_0" Type="defstruct">sevOutputStruct
            <field Name="signal" Type="string">"/VOUT"</field>
            <field Name="type" Type="symbol">net</field>
            <field Name="save" Type="symbol">t</field>
          </field>
          <field Name="outputList_1" Type="defstruct">sevOutputStruct
            <field Name="signal" Type="string">"/I0/M1/D"</field>
            <field Name="type" Type="symbol">terminal</field>
            <field Name="save" Type="symbol">t</field>
          </field>
        </field>
      </partition>
    </component>
  </Test>
</statedb>
""",
                encoding="utf-8",
            )

            # Fake headless Cadence `si` executable on PATH
            fake_si = bin_dir / "si"
            fake_si.write_text(
                "#!/bin/sh\n"
                "cat > netlistHeader <<'H'\n"
                "// Generated for: spectre\n"
                "simulator lang=spectre\n"
                "global 0\n"
                "H\n"
                "cat > netlist <<'N'\n"
                "R0 (IN VOUT) resistor r=1k\n"
                "C0 (VOUT 0) capacitor c=1p\n"
                "N\n"
                "exit 0\n",
                encoding="utf-8",
            )
            fake_si.chmod(0o755)

            def runner_with_fake_si(argv, *, input=b"", capture_output=True, timeout=25):
                env = dict(os.environ)
                env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
                return subprocess.run(
                    ["/bin/sh", "-s"],
                    input=input,
                    capture_output=capture_output,
                    timeout=timeout,
                    env=env,
                )

            # 1. Browse library directory: sees tb_amp cell with schematic + maestro tags
            b_lib = browse_remote_netlists("sim-node-a", None, str(lib_dir), runner=runner_with_fake_si)
            cell_entry = next(e for e in b_lib["entries"] if e["name"] == "tb_amp")
            self.assertEqual(cell_entry["virtuoso_kind"], "cell")
            self.assertTrue(cell_entry["has_schematic"])
            self.assertEqual(cell_entry["maestro_views"], ["maestro"])

            # 2. Browse schematic directory directly: detects virtuoso_context + maestro summary
            b_sch = browse_remote_netlists("sim-node-a", None, str(sch_dir), runner=runner_with_fake_si)
            vctx = b_sch["virtuoso_context"]
            self.assertIsNotNone(vctx)
            self.assertTrue(vctx["is_schematic_view"])
            self.assertEqual(vctx["lib_name"], "my_adc_lib")
            self.assertEqual(vctx["cell_name"], "tb_amp")
            self.assertEqual(len(vctx["maestro_views"]), 1)
            msum = vctx["maestro_views"][0]["summary"]
            self.assertEqual(msum["variables"]["vdd"], "1.2")
            self.assertEqual(msum["variables"]["vin"], "1m")
            self.assertEqual(len(msum["analyses"]), 2)

            # 3. Export WITH Maestro merged
            exp_with = export_remote_schematic(
                "sim-node-a", None, str(sch_dir), include_maestro=True, runner=runner_with_fake_si
            )
            self.assertEqual(exp_with["schema"], SCHEMATIC_EXPORT_SCHEMA)
            self.assertEqual(exp_with["file_name"], "tb_amp.scs")
            self.assertTrue(exp_with["maestro_applied"])
            self.assertIn('include "/opt/pdk/models.scs" section=tt_mos', exp_with["text"])
            self.assertIn("parameters vdd=1.2 vin=1m", exp_with["text"])
            self.assertIn("tran tran stop=50n errpreset=conservative", exp_with["text"])
            self.assertIn("dcOp dc ", exp_with["text"])
            self.assertIn("save VOUT I0.M1:D", exp_with["text"])

            # 4. Export WITHOUT Maestro (pure schematic netlist)
            exp_pure = export_remote_schematic(
                "sim-node-a", None, str(sch_dir), include_maestro=False, runner=runner_with_fake_si
            )
            self.assertFalse(exp_pure["maestro_applied"])
            self.assertIn("R0 (IN VOUT) resistor r=1k", exp_pure["text"])
            self.assertNotIn("tran tran stop=50n", exp_pure["text"])
            self.assertEqual(len(exp_pure["detected_maestro"]), 1)


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

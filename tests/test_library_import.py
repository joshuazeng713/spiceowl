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
    FIND_SCHEMA,
    IMPORT_SCHEMA,
    SCHEMATIC_EXPORT_SCHEMA,
    browse_remote_netlists,
    export_remote_schematic,
    fetch_remote_netlist,
    find_remote_library_targets,
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

            # Fake headless Cadence `dbAccess` executable that emits valid OA geometry JSON
            fake_db = bin_dir / "dbAccess"
            fake_db.write_text(
                "#!/bin/sh\n"
                "cat > oa_geometry.json <<'J'\n"
                "{\"ok\":true,\"top\":{\"lib\":\"my_adc_lib\",\"cell\":\"tb_amp\",\"view\":\"schematic\"},"
                "\"cells\":[{\"lib\":\"my_adc_lib\",\"cell\":\"tb_amp\",\"view\":\"schematic\",\"dbu\":160.0,"
                "\"instances\":["
                "{\"name\":\"R0\",\"lib\":\"analogLib\",\"cell\":\"res\",\"view\":\"symbol\",\"purpose\":\"\",\"xy\":[0.0,1.0],\"orient\":\"R0\",\"nlAction\":\"\",\"instTerms\":[[\"PLUS\",\"IN\"],[\"MINUS\",\"VOUT\"]]},"
                "{\"name\":\"C0\",\"lib\":\"analogLib\",\"cell\":\"cap\",\"view\":\"symbol\",\"purpose\":\"\",\"xy\":[2.0,0.0],\"orient\":\"MY\",\"nlAction\":\"\",\"instTerms\":[[\"PLUS\",\"VOUT\"],[\"MINUS\",\"0\"]]},"
                "{\"name\":\"G0\",\"lib\":\"basic\",\"cell\":\"gnd\",\"view\":\"symbol\",\"purpose\":\"\",\"xy\":[2.0,-1.0],\"orient\":\"R0\",\"nlAction\":\"\",\"instTerms\":[[\"gnd!\",\"gnd!\"]]}"
                "],\"terminals\":[{\"name\":\"VOUT\",\"dir\":\"output\",\"xy\":[3.0,1.0],\"bBox\":null,\"orient\":\"R0\"}],"
                "\"wires\":[{\"net\":\"VOUT\",\"pts\":[[0.5,1.0],[2.0,1.0],[2.0,0.5]]}],"
                "\"labels\":[{\"net\":\"VOUT\",\"text\":\"VOUT\",\"xy\":[1.25,1.1],\"orient\":\"R0\"}]}],"
                "\"symbols\":{"
                "\"analogLib/res/symbol\":{\"lib\":\"analogLib\",\"cell\":\"res\",\"view\":\"symbol\",\"dbu\":160.0,\"bBox\":[-0.5,-0.2,0.5,0.2],\"termOrder\":[\"PLUS\",\"MINUS\"],\"terms\":[{\"name\":\"PLUS\",\"dir\":\"inputOutput\",\"bBox\":[-0.55,-0.05,-0.45,0.05]},{\"name\":\"MINUS\",\"dir\":\"inputOutput\",\"bBox\":[0.45,-0.05,0.55,0.05]}],\"shapes\":[{\"type\":\"line\",\"layer\":\"device\",\"purpose\":\"drawing\",\"pts\":[[-0.5,0],[0.5,0]]}]},"
                "\"analogLib/cap/symbol\":{\"lib\":\"analogLib\",\"cell\":\"cap\",\"view\":\"symbol\",\"dbu\":160.0,\"bBox\":[-0.2,-0.5,0.2,0.5],\"termOrder\":[\"PLUS\",\"MINUS\"],\"terms\":[{\"name\":\"PLUS\",\"dir\":\"inputOutput\",\"bBox\":[-0.05,0.45,0.05,0.55]},{\"name\":\"MINUS\",\"dir\":\"inputOutput\",\"bBox\":[-0.05,-0.55,0.05,-0.45]}],\"shapes\":[{\"type\":\"line\",\"layer\":\"device\",\"purpose\":\"drawing\",\"pts\":[[0,0.5],[0,-0.5]]}]},"
                "\"basic/gnd/symbol\":{\"lib\":\"basic\",\"cell\":\"gnd\",\"view\":\"symbol\",\"dbu\":160.0,\"bBox\":[-0.2,-0.3,0.2,0.0],\"termOrder\":[],\"terms\":[{\"name\":\"gnd!\",\"dir\":\"inputOutput\",\"bBox\":[-0.05,-0.05,0.05,0.05]}],\"shapes\":[{\"type\":\"line\",\"layer\":\"device\",\"purpose\":\"drawing\",\"pts\":[[0,0],[0,-0.2]]}]}"
                "}}\n"
                "J\n"
                "exit 0\n",
                encoding="utf-8",
            )
            fake_db.chmod(0o755)

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

            # 3. Export WITH Maestro merged AND RNL geometry preserved (default)
            exp_with = export_remote_schematic(
                "sim-node-a", None, str(sch_dir), include_maestro=True, preserve_rnl=True, runner=runner_with_fake_si
            )
            self.assertEqual(exp_with["schema"], SCHEMATIC_EXPORT_SCHEMA)
            self.assertEqual(exp_with["file_name"], "tb_amp.scs")
            self.assertTrue(exp_with["maestro_applied"])
            self.assertTrue(exp_with["rnl_applied"])
            self.assertEqual(exp_with["oa_tool"], "dbAccess")
            self.assertEqual(exp_with["rnl_stats"]["placed"], 2)
            self.assertGreaterEqual(exp_with["rnl_stats"]["wires"], 2)
            self.assertEqual(exp_with["text"].splitlines()[1], '// @RNL(type="header") { @VERSION("0.3") }')
            self.assertIn('#"R0" @PLACE(0, 16) => @ORIENT("R0")', exp_with["text"])
            self.assertIn('#"C0" @PLACE(32, 0) => @ORIENT("MY")', exp_with["text"])
            self.assertIn('include "/opt/pdk/models.scs" section=tt_mos', exp_with["text"])
            self.assertIn("parameters vdd=1.2 vin=1m", exp_with["text"])
            self.assertIn("tran tran stop=50n errpreset=conservative", exp_with["text"])
            self.assertIn("dcOp dc ", exp_with["text"])
            self.assertIn("save VOUT I0.M1:D", exp_with["text"])

            # 4. Export WITHOUT Maestro and WITHOUT RNL (pure schematic netlist)
            exp_pure = export_remote_schematic(
                "sim-node-a", None, str(sch_dir), include_maestro=False, preserve_rnl=False, runner=runner_with_fake_si
            )
            self.assertFalse(exp_pure["maestro_applied"])
            self.assertFalse(exp_pure["rnl_applied"])
            self.assertNotIn("@RNL", exp_pure["text"])
            self.assertIn("R0 (IN VOUT) resistor r=1k", exp_pure["text"])
            self.assertNotIn("tran tran stop=50n", exp_pure["text"])
            self.assertEqual(len(exp_pure["detected_maestro"]), 1)

    def test_osshnl109_auto_shadow_recovery_preserves_original_files(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            lib_dir = root / "my_adc_lib"
            sch_dir = lib_dir / "tb_unchecked" / "schematic"
            bin_dir = root / "bin"
            sch_dir.mkdir(parents=True)
            bin_dir.mkdir(parents=True)
            (root / "cds.lib").write_text(f"DEFINE my_adc_lib {lib_dir}\n", encoding="utf-8")
            (sch_dir / "sch.oa").write_bytes(b"OA_UNTOUCHED")
            (sch_dir / "sch.oa.cdslck").write_text("stale lock", encoding="utf-8")

            # Fake `si` that fails with OSSHNL-109 until cds.lib points to shadow_libs (after shadow check)
            fake_si = bin_dir / "si"
            fake_si.write_text(
                "#!/bin/sh\n"
                "if ! grep -q 'shadow_libs' cds.lib 2>/dev/null; then\n"
                "  echo 'Virtuoso Framework License (111) was checked out successfully.'\n"
                "  echo \"ERROR (OSSHNL-109): The cellview 'my_adc_lib/tb_unchecked/schematic' has been modified since the last extraction.\"\n"
                "  echo 'End netlisting'\n"
                "  exit 255\n"
                "fi\n"
                "cat > netlistHeader <<'H'\n"
                "// Generated for: spectre\n"
                "simulator lang=spectre\n"
                "global 0\n"
                "H\n"
                "cat > netlist <<'N'\n"
                "R0 (IN OUT) resistor r=1k\n"
                "N\n"
                "exit 0\n",
                encoding="utf-8",
            )
            fake_si.chmod(0o755)

            # Fake `virtuoso` that simulates schCheck+dbSave and writes shadow_check.ok
            fake_virtuoso = bin_dir / "virtuoso"
            fake_virtuoso.write_text(
                "#!/bin/sh\n"
                "echo OK > shadow_check.ok\n"
                "exit 0\n",
                encoding="utf-8",
            )
            fake_virtuoso.chmod(0o755)

            def runner(argv, *, input=b"", capture_output=True, timeout=25):
                env = dict(os.environ)
                env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
                return subprocess.run(["/bin/sh", "-s"], input=input, capture_output=capture_output, timeout=timeout, env=env)

            res = export_remote_schematic(
                "sim-node-a", None, str(sch_dir), include_maestro=False, preserve_rnl=False, runner=runner
            )
            self.assertIn("R0 (IN OUT) resistor r=1k", res["text"])
            self.assertTrue(any("OSSHNL-109" in w for w in res["warnings"]))
            # Original design directory and even its stale lock file were never modified!
            self.assertEqual((sch_dir / "sch.oa").read_bytes(), b"OA_UNTOUCHED")
            self.assertTrue((sch_dir / "sch.oa.cdslck").exists())


class TestVirtuosoRnlInjection(unittest.TestCase):
    def test_multi_level_subckt_bundle_junction_and_line2_header(self):
        from spiceowl.onboarding.virtuoso_rnl import inject_rnl_geometry

        netlist = "\n".join([
            "// Generated for: spectre",
            "simulator lang=spectre",
            "global 0",
            "subckt InvBlock IN OUT VDD VSS",
            "    M0_0 (OUT IN VSS VSS) nch l=40n w=1u",
            "    M0_1 (OUT IN VSS VSS) nch l=40n w=1u",
            "ends InvBlock",
            "I0 (VIN VOUT VDD 0) InvBlock",
            "R0 (VOUT 0) resistor r=1k",
        ]) + "\n"
        oa_geom = {
            "ok": True,
            "top": {"lib": "demo_lib", "cell": "tb_top", "view": "schematic"},
            "cells": [
                {
                    "lib": "demo_lib", "cell": "InvBlock", "view": "schematic", "dbu": 160.0,
                    "instances": [
                        {"name": "M0<1:0>", "lib": "pdk", "cell": "nch", "view": "symbol", "purpose": "",
                         "xy": [1.0, 2.0], "orient": "R90", "nlAction": "",
                         "instTerms": [["D", "OUT"], ["G", "IN"], ["S", "VSS"], ["B", "VSS"]]},
                    ],
                    "terminals": [
                        {"name": "IN", "dir": "input", "xy": [0.0, 2.0], "bBox": None, "orient": "R0"},
                        {"name": "OUT", "dir": "output", "xy": [3.0, 2.0], "bBox": None, "orient": "R0"},
                    ],
                    "wires": [
                        {"net": "OUT", "pts": [[1.0, 2.0], [2.0, 2.0]]},
                        {"net": "OUT", "pts": [[2.0, 2.0], [3.0, 2.0]]},
                        {"net": "OUT", "pts": [[2.0, 2.0], [2.0, 3.0]]},
                    ],
                    "labels": [{"net": "OUT", "text": "OUT", "xy": [2.5, 2.1], "orient": "R0"}],
                },
                {
                    "lib": "demo_lib", "cell": "tb_top", "view": "schematic", "dbu": 160.0,
                    "instances": [
                        {"name": "I0", "lib": "demo_lib", "cell": "InvBlock", "view": "symbol", "purpose": "",
                         "xy": [0.0, 0.0], "orient": "R0", "nlAction": "",
                         "instTerms": [["IN", "VIN"], ["OUT", "VOUT"], ["VDD", "VDD"], ["VSS", "0"]]},
                        {"name": "R0", "lib": "analogLib", "cell": "res", "view": "symbol", "purpose": "",
                         "xy": [2.0, 0.0], "orient": "MX", "nlAction": "",
                         "instTerms": [["PLUS", "VOUT"], ["MINUS", "0"]]},
                    ],
                    "terminals": [],
                    "wires": [{"net": "VOUT", "pts": [[0.5, 0.0], [1.5, 0.0]]}],
                    "labels": [],
                },
            ],
            "symbols": {
                "demo_lib/InvBlock/symbol": {
                    "lib": "demo_lib", "cell": "InvBlock", "view": "symbol", "dbu": 160.0,
                    "bBox": [-0.5, -0.5, 0.5, 0.5], "termOrder": ["IN", "OUT", "VDD", "VSS"],
                    "terms": [
                        {"name": "IN", "dir": "input", "bBox": [-0.55, -0.05, -0.45, 0.05]},
                        {"name": "OUT", "dir": "output", "bBox": [0.45, -0.05, 0.55, 0.05]},
                    ],
                    "shapes": [{"type": "rect", "layer": "device", "purpose": "drawing", "bBox": [-0.5, -0.5, 0.5, 0.5]}],
                },
                "pdk/nch/symbol": {
                    "lib": "pdk", "cell": "nch", "view": "symbol", "dbu": 160.0,
                    "bBox": [0.0, -0.25, 0.25, 0.25], "termOrder": ["D", "G", "S", "B"],
                    "terms": [
                        {"name": "D", "dir": "inputOutput", "bBox": [0.2, 0.2, 0.3, 0.3]},
                        {"name": "G", "dir": "inputOutput", "bBox": [-0.05, -0.05, 0.05, 0.05]},
                    ],
                    "shapes": [{"type": "line", "layer": "device", "purpose": "drawing", "pts": [[0, 0], [0.25, 0]]}],
                },
                "analogLib/res/symbol": {
                    "lib": "analogLib", "cell": "res", "view": "symbol", "dbu": 160.0,
                    "bBox": [-0.5, -0.1, 0.5, 0.1], "termOrder": ["PLUS", "MINUS"],
                    "terms": [
                        {"name": "PLUS", "dir": "inputOutput", "bBox": [-0.55, -0.05, -0.45, 0.05]},
                        {"name": "MINUS", "dir": "inputOutput", "bBox": [0.45, -0.05, 0.55, 0.05]},
                    ],
                    "shapes": [{"type": "line", "layer": "device", "purpose": "drawing", "pts": [[-0.5, 0], [0.5, 0]]}],
                },
            },
        }
        annotated, stats = inject_rnl_geometry(netlist, oa_geom)
        self.assertTrue(stats["applied"])
        self.assertEqual(stats["cells"], 2)
        self.assertEqual(stats["placed"], 4)  # M0_0, M0_1 (bundled), I0, R0
        lines = annotated.splitlines()
        self.assertEqual(lines[0], "// Generated for: spectre")
        self.assertEqual(lines[1], '// @RNL(type="header") { @VERSION("0.3") }')
        self.assertIn("%block_symbol = @DEFINE-SYMBOL", annotated)
        self.assertIn("# @SET-SYMBOL %block_symbol", annotated)
        self.assertIn('@BUNDLE', annotated)
        self.assertIn('$IO_PORT @ATTACH &"OUT"', annotated)
        self.assertIn('$JUNCTION @ATTACH %p', annotated)
        self.assertIn('#"I0":2', annotated)

    def test_failed_oa_geometry_returns_original_netlist(self):
        from spiceowl.onboarding.virtuoso_rnl import inject_rnl_geometry

        src = "// title\nsimulator lang=spectre\nR0 (a 0) resistor r=1k\n"
        out, stats = inject_rnl_geometry(src, {"ok": False})
        self.assertEqual(out, src)
        self.assertFalse(stats["applied"])


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

    def test_find_remote_library_targets_and_agent_ui_configure_tools(self):
        from spiceowl.standalone_agent import build_standalone_dispatcher

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            proj = root / "2026_pipeline"
            lib = proj / "research4"
            sch = lib / "tb_const_gain_amplifier" / "schematic"
            maestro = lib / "tb_const_gain_amplifier" / "maestro"
            sch.mkdir(parents=True)
            maestro.mkdir(parents=True)
            (proj / "cds.lib").write_text("DEFINE research4 ./research4\n", encoding="utf-8")
            (sch / "sch.oa").write_bytes(b"OA")
            (maestro / "maestro.sdb").write_text("<setupdb/>", encoding="utf-8")

            def local_runner(argv, *, input=b"", capture_output=True, timeout=25):
                return subprocess.run(["/bin/sh", "-s"], input=input, capture_output=capture_output, timeout=timeout)

            res = find_remote_library_targets(
                "sim-node-a",
                None,
                query="2026_pipeline research4 tb_constant_gain_amplifier",
                path=str(root),
                runner=local_runner,
            )
            self.assertEqual(res["schema"], FIND_SCHEMA)
            self.assertGreaterEqual(len(res["matches"]), 1)
            top = res["matches"][0]
            self.assertEqual(top["kind"], "schematic")
            self.assertEqual(top["lib_name"], "research4")
            self.assertEqual(top["cell_name"], "tb_const_gain_amplifier")
            self.assertEqual(top["maestro_views"], ["maestro"])

            # Verify standalone agent tools have read policy (no approval popup) and emit UI patches
            ctrl = Controller(root / "state", agent_mode="standalone")
            try:
                ctrl.hosts.register({
                    "schema": "spiceowl.host.v1",
                    "host_id": "sim-node-a",
                    "label": "Sim Node A",
                    "ssh_target": "sim-node-a",
                    "expected_host": "sim-node-a",
                    "expected_user": "researcher",
                    "tools": [{"kind": "spectre", "executable": "/opt/cadence/bin/spectre"}],
                })
                emitted = []
                disp = build_standalone_dispatcher(
                    ctrl,
                    ui_patch_sink=lambda kind, payload: (emitted.append((kind, payload)), len(emitted))[1],
                )
                self.assertEqual(disp.policy("spiceowl_library_browse", {"query": "amp"}), "read")
                self.assertEqual(disp.policy("spiceowl_library_open", {"mode": "schematic", "remote_path": "/a/b"}), "read")
                self.assertEqual(disp.policy("spiceowl_ui_configure", {"cpus": 8}), "read")
                out = disp.dispatch("spiceowl_ui_configure", {"host_id": "sim-node-a", "cpus": 8, "memory_gib": 16})
                self.assertTrue(out["applied"])
                self.assertEqual(out["revision"], 1)
                self.assertEqual(emitted[0][0], "ui_configure")
                self.assertEqual(emitted[0][1]["cpus"], 8)
            finally:
                ctrl.close()

    def test_direct_run_organization_metadata_inference_and_grouping(self):
        from spiceowl.batch import DirectBatchManager
        from spiceowl.direct import DirectExecutionManager
        from spiceowl.service import Controller
        from spiceowl.standalone_agent import build_standalone_dispatcher

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ctrl = Controller(root / "state", agent_mode="standalone")
            try:
                ctrl.hosts.register({
                    "schema": "spiceowl.host.v1",
                    "host_id": "sim-node-a",
                    "label": "Sim Node A",
                    "ssh_target": "sim-node-a",
                    "expected_host": "sim-node-a",
                    "expected_user": "researcher",
                    "tools": [{"kind": "spectre", "executable": "/opt/cadence/bin/spectre"}],
                })
                dm = DirectExecutionManager(ctrl.store.directory, hosts=ctrl.hosts)
                bm = DirectBatchManager(ctrl.store.directory, dm)

                # 1. Legacy-style run without explicit metadata still infers Project/TB from Virtuoso header
                r_legacy = dm.prepare(
                    run_key="tb_amp-123456",
                    host_id="sim-node-a",
                    netlist_text="// Exported from Virtuoso schematic analog_lib/tb_const_amp (schematic)\nsimulator lang=spectre\nR0 (a 0) resistor r=1k\n",
                )
                self.assertEqual(r_legacy["organization"]["project"], "analog_lib")
                self.assertEqual(r_legacy["organization"]["testbench"], "tb_const_amp")
                self.assertEqual(r_legacy["organization"]["view"], "schematic")

                # Strip fields on disk to verify read()/list() fallback from plan.json
                run_json_path = dm.path("tb_amp-123456") / "run.json"
                raw = json.loads(run_json_path.read_text(encoding="utf-8"))
                for k in ("project_name", "library_name", "cell_name", "view_name", "organization"):
                    raw.pop(k, None)
                run_json_path.write_text(json.dumps(raw), encoding="utf-8")
                inferred = dm.read("tb_amp-123456")
                self.assertEqual(inferred["organization"]["project"], "analog_lib")
                self.assertEqual(inferred["organization"]["testbench"], "tb_const_amp")

                # 2. Batch runs are co-archived under batch:<batch_id> with Project & TB
                bm.prepare(
                    batch_id="amp-pvt-01",
                    title="Amp PVT Corners",
                    netlist_template="// Library name: analog_lib\n// Cell name: tb_const_amp\nsimulator lang=spectre\nR0 (a 0) resistor r=1k\n",
                    kind="pvt",
                    process_corners=["tt", "ss"],
                    voltages=[1.2],
                    temperatures=[27.0],
                    host_id="sim-node-a",
                )
                listed = dm.list(batch_map=bm.run_batch_map())
                batch_children = [r for r in listed if r["organization"]["batch_id"] == "amp-pvt-01"]
                self.assertEqual(len(batch_children), 2)
                self.assertEqual({r["organization"]["submission_group_id"] for r in batch_children}, {"batch:amp-pvt-01"})
                self.assertEqual({r["organization"]["project"] for r in batch_children}, {"analog_lib"})
                self.assertEqual({r["organization"]["testbench"] for r in batch_children}, {"tb_const_amp"})

                # 3. Multiple runs prepared by Agent in one turn share submission_group_id = turn:<turn_id>
                disp = build_standalone_dispatcher(ctrl, direct_manager=dm)
                ctx = {"turn_id": "turn-multi-99"}
                r_a1 = disp.dispatch("spiceowl_direct_control", {
                    "operation": "prepare",
                    "arguments": {
                        "run_key": "agent-tt-01",
                        "host_id": "sim-node-a",
                        "netlist_text": "simulator lang=spectre\nR0 (a 0) resistor r=1k\n",
                        "project_name": "adc_proj",
                        "cell_name": "tb_sar",
                    },
                }, context=ctx)
                r_a2 = disp.dispatch("spiceowl_direct_control", {
                    "operation": "prepare",
                    "arguments": {
                        "run_key": "agent-ff-02",
                        "host_id": "sim-node-a",
                        "netlist_text": "simulator lang=spectre\nR0 (a 0) resistor r=2k\n",
                        "project_name": "adc_proj",
                        "cell_name": "tb_sar",
                    },
                }, context=ctx)
                self.assertEqual(r_a1["organization"]["submission_group_id"], "turn:turn-multi-99")
                self.assertEqual(r_a2["organization"]["submission_group_id"], "turn:turn-multi-99")
                self.assertEqual(r_a1["organization"]["project"], "adc_proj")
                self.assertEqual(r_a2["organization"]["testbench"], "tb_sar")

                # 4. read_inputs returns frozen netlist & config; delete removes prepared runs and refuses active runs
                inputs = dm.read_inputs("agent-tt-01")
                self.assertIn("R0 (a 0) resistor r=1k", inputs["netlist_text"])
                self.assertEqual(inputs["organization"]["project"], "adc_proj")

                deleted = dm.delete("agent-tt-01")
                self.assertEqual(deleted, {"deleted": True, "run_id": "agent-tt-01"})
                self.assertFalse(dm.path("agent-tt-01").exists())

                # Simulate active dispatched run on disk: delete must fail closed
                active_json = dm.path("agent-ff-02") / "run.json"
                active_rec = json.loads(active_json.read_text(encoding="utf-8"))
                active_rec["dispatch_claimed"] = True
                active_rec["state"] = "running_observed"
                active_json.write_text(json.dumps(active_rec), encoding="utf-8")
                with self.assertRaises(DomainError) as ctx_err:
                    dm.delete("agent-ff-02")
                self.assertEqual(ctx_err.exception.code, "run_active_cannot_delete")
            finally:
                ctrl.close()


if __name__ == "__main__":
    unittest.main()

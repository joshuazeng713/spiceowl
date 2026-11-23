"""Synthetic filesystem/log evidence only; no Cadence, SSH or PDK required."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from spiceowl.domain.common import DomainError
from spiceowl.integrations.model_dependencies import (
    MAX_LOG_BYTES, audit_read_log, build_model_audit_script, validate_audit_config,
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


class ModelDependencyAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.main = self.root / "main.scs"

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text if isinstance(text, bytes) else text.encode())
        return path

    def config(self, **extra):
        return {"roots": [str(self.root)], "main": str(self.main),
                "sections": ["tt"], "required_masters": ["example_n", "example_p"], **extra}

    def run_audit(self, config=None):
        script = build_model_audit_script(config or self.config())
        result = subprocess.run([sys.executable, "-B", "-"], input=script, text=True,
                                capture_output=True, timeout=15, check=True)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def codes(self, result):
        return {item["code"] for item in result["unknown"]}

    def test_recursive_sections_inline_subckt_tmi_and_profile_merge_before_run(self):
        self.write("main.scs", 'section tt\ninclude "mos.scs" section=TT_MOS\n'
                   '.LIB "passives.lib" TT\nendsection tt\n'
                   'section ss\ninclude "not-selected.scs"\nendsection ss\n')
        self.write("mos.scs", 'section TT_MOS\ninline subckt example_n (d g s b)\n'
                   'parameters private_body_parameter=42\nends example_n\n'
                   'inline subckt example_p (d g s b)\nends example_p\n'
                   ".option tmiflag=2.01 tmipath='demo_dir'\n"
                   ".option etmiUsrInput='demo_dir'\n"
                   'endsection TT_MOS\n')
        self.write("passives.lib", '.LIB TT\n.INC "leaf.inc"\n.ENDL TT\n')
        leaf = self.write("leaf.inc", '* original synthetic source\n')
        binary = self.write("demo_dir/RH_64/libTMImodel.so", b"\x00ELF-synthetic\xff")
        cfg = self.write("spectre.cfg", "")
        config = self.config(tmi_relative_library="RH_64/libTMImodel.so",
                             profile_dependencies=[
                                 {"path": str(binary), "kind": "file", "role": "dynamic_library"},
                                 {"path": str(cfg), "kind": "file", "role": "tool_config",
                                  "sha256": digest(b"")},
                             ])
        result = self.run_audit(config)
        self.assertEqual(result["status"], "complete_for_supported_static_syntax")
        self.assertEqual(result["missing_masters"], [])
        self.assertEqual({d["kind"] for d in result["master_declarations"]}, {"inline_subckt"})
        self.assertTrue(all(d["ports"] == ["d", "g", "s", "b"] and d["port_count"] == 4
                            for d in result["master_declarations"]))
        files = {item["path"]: item for item in result["files"]}
        self.assertEqual(len(files), 6)
        self.assertEqual(files[str(binary)]["sha256"], digest(binary.read_bytes()))
        self.assertEqual(files[str(leaf)]["sha256"], digest(leaf.read_bytes()))
        self.assertEqual(result["directories"], [{"path": str(binary.parent.parent),
                                                  "kind": "directory", "role": "directory_reference"}])
        self.assertNotIn("private_body_parameter", json.dumps(result))
        self.assertEqual(len([x for x in result["edges"] if x["role"] == "tmi_directory"]), 2)

    def test_global_includes_dot_sections_and_continuations(self):
        self.write("main.scs", 'include "global.inc"\n.section tt\n'
                   'include \\\n "mos.scs" \\\n section="devices"\n'
                   '.endsection tt\n')
        self.write("global.inc", '* global only\n')
        self.write("mos.scs", 'section devices\n.subckt example_n d g s b\n.ends\n'
                   'subckt example_p (d g s b)\nends\nendsection devices\n')
        result = self.run_audit()
        self.assertEqual(result["unknown"], [])
        self.assertEqual(len(result["files"]), 3)
        self.assertEqual(len(result["master_declarations"]), 2)

    def test_plus_continuation_and_comments_do_not_invent_dependencies(self):
        self.write("main.scs", 'section tt\ninclude "mos.scs"\n+ section=devices // comment\n'
                   '/* include "forbidden.scs" */\nendsection tt\n')
        self.write("mos.scs", 'section devices\ninline subckt example_n (d g s b)\n'
                   'inline subckt example_p (d g s b)\nendsection devices\n')
        self.assertEqual(self.run_audit()["unknown"], [])

    def test_tmi_requires_explicit_platform_rule_and_hashable_library(self):
        self.write("main.scs", "section tt\n.option tmipath='demo_dir'\nendsection tt\n")
        (self.root / "demo_dir").mkdir()
        no_rule = self.run_audit(self.config(required_masters=[]))
        self.assertIn("tmi_platform_library_not_configured", self.codes(no_rule))
        missing = self.run_audit(self.config(required_masters=[], tmi_relative_library="RH_64/libTMImodel.so"))
        self.assertIn("unreadable_file", self.codes(missing))
        self.assertEqual(missing["status"], "unknown")

    def test_directory_is_never_hashed_as_a_file(self):
        self.write("main.scs", "section tt\nendsection tt\n")
        (self.root / "dir").mkdir()
        result = self.run_audit(self.config(required_masters=[], profile_dependencies=[
            {"path": str(self.root / "dir"), "kind": "file", "role": "model"}]))
        self.assertIn("not_regular_file", self.codes(result))

    def test_symlink_escape_and_parent_path_escape_are_not_read(self):
        with tempfile.TemporaryDirectory() as other:
            external = Path(other) / "secret.scs"
            external.write_text("inline subckt example_n (d g s b)\n")
            (self.root / "link.scs").symlink_to(external)
            self.write("main.scs", 'section tt\ninclude "link.scs"\ninclude "' + str(external)
                       + '"\nendsection tt\n')
            result = self.run_audit()
        self.assertIn("outside_authorized_roots", self.codes(result))
        self.assertEqual([f["path"] for f in result["files"]], [str(self.main)])
        self.assertEqual(result["master_declarations"], [])

    def test_missing_section_and_master_remain_unknown(self):
        self.write("main.scs", "section ss\nsubckt example_n d g s b\nendsection ss\n")
        result = self.run_audit()
        self.assertIn("section_not_visible", self.codes(result))
        self.assertEqual(result["missing_masters"], ["example_n", "example_p"])
        self.assertEqual(result["status"], "unknown")

    def test_opaque_selected_scope_and_veriloga_cannot_claim_complete(self):
        self.write("main.scs", 'section tt\n//pragma protect begin_protected\n'
                   'ahdl_include "device.va"\nendsection tt\n')
        self.write("device.va", 'module fictional; endmodule\n')
        result = self.run_audit(self.config(required_masters=[]))
        self.assertIn("opaque_protected_scope", self.codes(result))
        self.assertIn("opaque_ahdl_dependency_scope", self.codes(result))
        self.assertEqual(len(result["files"]), 2)

    def test_inactive_opaque_section_does_not_poison_selected_scope(self):
        self.write("main.scs", "section tt\nendsection tt\nsection ss\n.protect\n.endsection ss\n")
        self.assertEqual(self.run_audit(self.config(required_masters=[]))["unknown"], [])

    def test_cycles_and_byte_file_section_limits_are_bounded_unknown(self):
        self.write("main.scs", 'section tt\ninclude "other.scs"\nendsection tt\n')
        self.write("other.scs", '.LIB "main.scs" tt\n')
        self.assertIn("include_cycle", self.codes(self.run_audit(self.config(required_masters=[]))))
        for limits, code in [({"max_files": 1}, "file_limit"),
                             ({"max_file_bytes": 8}, "file_byte_limit"),
                             ({"max_total_bytes": 8}, "total_byte_limit"),
                             ({"max_sections": 1}, "section_visit_limit"),
                             ({"max_line_bytes": 4}, "line_limit")]:
            with self.subTest(limits=limits):
                result = self.run_audit(self.config(required_masters=[], limits=limits))
                self.assertEqual(result["status"], "unknown")
                self.assertIn(code, self.codes(result))

    def test_scalefactor_duplicates_and_conflicts_are_metadata_not_effective_value(self):
        self.write("main.scs", "section tt\n.option scalefactor=0.9\n"
                   ".option scalefactor=0.9\n.option scalefactor=1.0\n"
                   ".option scalefactor=variable\nendsection tt\n")
        scales = self.run_audit(self.config(required_masters=[]))["scalefactors"]
        self.assertEqual(len(scales["declarations"]), 4)
        self.assertTrue(scales["duplicate"])
        self.assertTrue(scales["conflicting_literals"])
        self.assertTrue(scales["has_dynamic_value"])
        self.assertIsNone(scales["effective_value"])

    def test_geoshrink_retains_its_source_name_and_is_not_equated_to_other_options(self):
        self.write("main.scs", "section tt\n.option geoshrink=0.9\n"
                   ".option geoshrink=0.9\n.option scale=0.8\n"
                   "simulatorOptions options scalefactor=1.0\n"
                   "parameters geoshrink=0.1 scalefactor=0.2\nendsection tt\n")
        scales = self.run_audit(self.config(required_masters=[]))["scalefactors"]
        self.assertEqual(len(scales["declarations"]), 3)
        groups = {i["source_option"]: i for i in scales["by_source_option"]}
        self.assertEqual(set(groups), {"geoshrink", "scalefactor"})
        self.assertEqual(groups["geoshrink"]["declaration_count"], 2)
        self.assertTrue(groups["geoshrink"]["duplicate"])
        self.assertFalse(groups["geoshrink"]["conflicting_literals"])
        self.assertFalse(groups["scalefactor"]["duplicate"])
        self.assertFalse(scales["conflicting_literals"])
        self.assertTrue(scales["source_options_are_not_assumed_equivalent"])
        self.assertIsNone(scales["effective_value"])

    def test_geoshrink_differing_literals_and_dynamic_value_report_within_its_own_group(self):
        self.write("main.scs", "section tt\n.option geoshrink=0.9\n"
                   ".option geoshrink=1.0\n.option geoshrink=local_expression\nendsection tt\n")
        scales = self.run_audit(self.config(required_masters=[]))["scalefactors"]
        self.assertTrue(scales["duplicate"])
        self.assertTrue(scales["conflicting_literals"])
        self.assertTrue(scales["has_dynamic_value"])
        self.assertEqual(scales["by_source_option"][0]["literal_values"], ["0.9", "1.0"])
        self.assertNotIn("local_expression", json.dumps(scales))

    def test_repeated_scale_option_on_one_line_is_not_silently_deduplicated(self):
        self.write("main.scs", "section tt\n.option geoshrink=0.9 geoshrink=1.0\nendsection tt\n")
        scales = self.run_audit(self.config(required_masters=[]))["scalefactors"]
        self.assertEqual(len(scales["declarations"]), 2)
        self.assertTrue(scales["conflicting_literals"])
        self.assertEqual(len({d["source_column"] for d in scales["declarations"]}), 2)

    def test_profile_hash_mismatch_is_unknown_and_does_not_change_bytes(self):
        self.write("main.scs", "section tt\nendsection tt\n")
        binary = self.write("lib.so", b"original")
        config = self.config(required_masters=[], profile_dependencies=[
            {"path": str(binary), "kind": "file", "role": "dynamic_library", "sha256": "0" * 64}])
        result = self.run_audit(config)
        self.assertIn("profile_hash_mismatch", self.codes(result))
        self.assertEqual(binary.read_bytes(), b"original")

    def test_dynamic_path_is_inert_and_not_complete(self):
        sentinel = self.root / "never-created"
        self.write("main.scs", 'section tt\ninclude "$(touch ' + str(sentinel)
                   + ')"\nendsection tt\n')
        result = self.run_audit(self.config(required_masters=[]))
        self.assertIn("dynamic_path", self.codes(result))
        self.assertFalse(sentinel.exists())

    def test_conditional_include_and_unsupported_tmi_scope_remain_unknown(self):
        self.write("main.scs", 'section tt\nif (choice) {\ninclude "conditional.scs"\n}\n'
                   ".option tmipath='demo_dir' tmifile='unsupported'\nendsection tt\n")
        self.write("conditional.scs", "// branch intentionally not evaluated\n")
        self.write("demo_dir/RH_64/libTMImodel.so", b"synthetic")
        result = self.run_audit(self.config(required_masters=[], tmi_relative_library="RH_64/libTMImodel.so"))
        self.assertIn("conditional_scope_unresolved", self.codes(result))
        self.assertIn("unsupported_tmi_attribute", self.codes(result))
        self.assertEqual(result["status"], "unknown")

    def test_large_model_continuations_are_bounded_separately_from_physical_lines(self):
        body = ".model original_example nmos\n" + ("+ synthetic_parameter=1 " * 20 + "\n") * 300
        self.write("main.scs", "section tt\n" + body + "endsection tt\n")
        result = self.run_audit(self.config(required_masters=[]))
        self.assertEqual(result["unknown"], [])
        self.assertEqual(result["files"][0]["sha256"], digest(self.main.read_bytes()))
        self.assertNotIn("synthetic_parameter", json.dumps(result))
        limited = self.run_audit(self.config(required_masters=[], limits={"max_logical_line_bytes": 65536}))
        error = next(i for i in limited["unknown"] if i["code"] == "logical_line_limit")
        self.assertEqual(error["line"], 2)
        self.assertGreater(error["line_end"], error["line"])
        self.assertGreater(error["observed_bytes"], 65536)
        self.assertEqual(limited["status"], "unknown")

    def test_failed_file_is_not_reread_for_every_selected_section(self):
        self.write("main.scs", 'section tt\ninclude "bad.scs" section=a\n'
                   'include "bad.scs" section=b\nendsection tt\n')
        self.write("bad.scs", ".model original_example nmos\n" + ("+ value=1 " * 20 + "\n") * 30)
        result = self.run_audit(self.config(required_masters=[], limits={"max_logical_line_bytes": 1024}))
        self.assertEqual(sum(x["code"] == "logical_line_limit" for x in result["unknown"]), 1)
        self.assertEqual(result["status"], "unknown")

    def test_build_only_does_no_io_and_validation_cannot_widen_scope(self):
        with patch("builtins.open", side_effect=AssertionError("unexpected file read")):
            self.assertIn("json.loads", build_model_audit_script(self.config()))
        for update in [{"roots": ["/"]}, {"main": "/elsewhere/model.scs"},
                       {"sections": []}, {"limits": {"max_files": 100000}},
                       {"limits": {"max_files": True}},
                       {"tmi_relative_library": "../libTMImodel.so"},
                       {"profile_dependencies": [{"path": str(self.root), "kind": "directory",
                                                  "role": "directory_reference", "sha256": "a" * 64}]}]:
            with self.subTest(update=update), self.assertRaises(DomainError):
                validate_audit_config(self.config(**update))

    def test_generated_script_is_python2_grammar_compatible(self):
        # Grammar check only; an authorized remote run must test its interpreter.
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                from lib2to3.refactor import RefactoringTool
            except ImportError:
                self.skipTest("Python 2 grammar unavailable on this interpreter")
        tool = RefactoringTool([])
        self.assertIsNotNone(tool.refactor_string(build_model_audit_script(self.config()), "audit.py"))


class ModelReadLogAuditTests(unittest.TestCase):
    def audit(self, log, **extra):
        return audit_read_log(log, pinned_paths={"MODEL": "/example/model.scs"},
                              pinned_sha256={"MODEL": "1" * 64}, **extra)

    def test_reading_and_cmi_loading_merge_explicit_prelaunch_profiles(self):
        log = ("Reading file:  /example/model.scs\n"
               "Loading /tool/cmi/lib/example.so ...\n"
               "Reading file: /example/demo_dir\n"
               "Reading file: /run/input.scs\n")
        result = self.audit(log, native_payload={"/run/input.scs": "2" * 64}, builtin_profile=[
            {"path": "/tool/cmi/lib/example.so", "kind": "file", "phase": "prelaunch", "sha256": "3" * 64},
            {"path": "/example/demo_dir", "kind": "directory", "phase": "prelaunch"}])
        self.assertEqual(result["status"], "covered_by_declared_prelaunch_pins")
        self.assertEqual(len(result["references"]), 4)
        self.assertEqual(result["references"][1]["event"], "loading")
        self.assertEqual(result["references"][1]["path"], "/tool/cmi/lib/example.so")
        self.assertEqual(result["references"][2]["pin_status"], "directory_reference")
        self.assertIsNone(result["references"][2]["sha256"])
        self.assertFalse(result["file_bytes_verified"])

    def test_postrun_hash_does_not_retroactively_cover_a_loaded_library(self):
        result = self.audit("Loading /example/lib.so ...\n", builtin_profile=[
            {"path": "/example/lib.so", "kind": "file", "phase": "postrun", "sha256": "f" * 64}])
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["uncovered_paths"], ["/example/lib.so"])
        self.assertEqual(result["references"][0]["pin_status"], "not_pre_pinned")
        self.assertIsNone(result["references"][0]["sha256"])
        self.assertFalse(result["postrun_hashes_establish_provenance"])

    def test_unknown_directory_is_not_assumed_to_be_a_regular_file(self):
        result = self.audit("Reading file: /example/unknown_dir\n")
        self.assertEqual(result["references"][0]["kind"], "unknown")
        known = self.audit("Reading file: /example/unknown_dir\n", builtin_profile=[
            {"path": "/example/unknown_dir", "kind": "directory", "phase": "postrun"}])
        self.assertEqual(known["references"][0]["kind"], "directory")
        self.assertEqual(known["status"], "unknown")

    def test_postrun_directory_cannot_cover_scope_even_when_another_file_is_pre_pinned(self):
        result = self.audit("Reading file: /example/model.scs\nReading file: /example/dir\n",
                            builtin_profile=[{"path": "/example/dir", "kind": "directory", "phase": "postrun"}])
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["uncovered_paths"], ["/example/dir"])
        self.assertFalse(result["references"][1]["prelaunch_declared"])
        self.assertEqual(result["references"][1]["pin_status"], "directory_reference")

    def test_log_paths_are_data_never_file_reads_or_commands(self):
        with patch("builtins.open", side_effect=AssertionError("unexpected file read")), \
                patch("subprocess.run", side_effect=AssertionError("unexpected execution")):
            result = self.audit("Reading file: /example/$(touch secret)\nLoading relative.so ...\n")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(len(result["unrecognized_records"]), 1)

    def test_empty_log_quoted_paths_and_duplicate_occurrences(self):
        self.assertEqual(self.audit("")["status"], "unknown")
        result = self.audit('Reading file: "/example/model.scs"\nReading file: /example/model.scs\n')
        self.assertEqual(len(result["references"]), 2)
        self.assertEqual(result["observed_paths"], ["/example/model.scs"])

    def test_pin_conflicts_incomplete_aliases_and_oversized_logs_rejected(self):
        with self.assertRaises(DomainError):
            self.audit("", native_payload={"/example/model.scs": "2" * 64})
        with self.assertRaises(DomainError):
            audit_read_log("", pinned_paths={"M": "/example/model.scs"}, pinned_sha256={})
        with self.assertRaises(DomainError):
            self.audit("x" * (MAX_LOG_BYTES + 1))
        with self.assertRaises(DomainError):
            self.audit("", builtin_profile=[{"path": "/dir", "kind": "directory",
                                             "phase": "prelaunch", "sha256": "1" * 64}])


if __name__ == "__main__":
    unittest.main()

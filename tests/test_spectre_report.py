"""Synthetic five-dimensional reports; no proprietary data or remote execution."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from spiceowl.domain.common import DomainError
from spiceowl.integrations.spectre_report import build_spectre_report, QUALIFICATION_CHECKS

FOOTER = "spectre completes with 0 errors, 0 warnings, and 0 notices."
WARNING_FOOTER = "spectre completes with 0 errors, 1 warning, and 0 notices."
SCOPE = {"scope_id": "synthetic-scope", "manifest_sha256": "a" * 64,
         "frozen_before_execution": True, "checks": ["soa_voltage", "soa_current", "model_parameter_range"]}


def audit(**changes):
    return {"run_id": "synthetic-run", "scope_id": "synthetic-dependencies", "manifest_sha256": "b" * 64,
            "run_bound": True, "inventory_complete": True,
            "entries": [{"dependency_id": "synthetic-model", "kind": "model_file", "pre_pinned": True,
                         "hash_matches": True, "observed": True, "evidence_sha256": "c" * 64}], **changes}


class SpectreReportTests(unittest.TestCase):
    def report(self, text=FOOTER, **kwargs):
        return build_spectre_report(text, **{"execution_state": "completed", "exit_code": 0,
                                            "run_id": "synthetic-run", **kwargs})

    def test_exit_zero_alone_leaves_other_dimensions_unknown(self):
        result = self.report()
        self.assertEqual(result["execution_status"]["status"], "completed")
        for dimension in ["measurement_validity", "functional_performance", "operating_qualification", "dependency_provenance"]:
            self.assertEqual(result[dimension]["status"], "unknown")
            self.assertEqual(set(result[dimension]), {"status", "scope", "evidence"})
        self.assertNotIn("overall_pass", result)

    def test_functional_pass_can_coexist_with_soa_violation_and_unpinned_tmi(self):
        dependencies = audit(); dependencies["entries"].append({"dependency_id": "synthetic-tmi", "kind": "tmi_library",
            "pre_pinned": False, "hash_matches": True, "observed": True})
        result = self.report("WARNING (CMI-2752): example voltage limit exceeded.\n" + FOOTER,
            acceptance={"data_validity": "valid", "specification": "pass"}, log_complete=True,
            qualification_scope=SCOPE, dependency_audit=dependencies)
        self.assertEqual([result[key]["status"] for key in ["execution_status", "measurement_validity", "functional_performance", "operating_qualification", "dependency_provenance"]],
                         ["completed", "valid", "pass", "violations_detected", "incomplete"])
        self.assertIn("unregistered_runtime_tmi_library", [e["code"] for e in result["dependency_provenance"]["evidence"]])

    def test_no_violation_qualifies_only_declared_frozen_complete_scope(self):
        before = deepcopy(SCOPE)
        good = self.report(log_complete=True, qualification_scope=SCOPE)
        self.assertEqual(good["operating_qualification"]["status"], "passed_within_scope")
        self.assertEqual(good["operating_qualification"]["scope"]["checks"], SCOPE["checks"])
        self.assertEqual(SCOPE, before)
        for kwargs in [{}, {"qualification_scope": SCOPE}, {"log_complete": True},
                       {"log_complete": True, "qualification_scope": {**SCOPE, "frozen_before_execution": False}},
                       {"log_complete": True, "qualification_scope": SCOPE, "execution_state": "unknown", "exit_code": None}]:
            self.assertEqual(self.report(**kwargs)["operating_qualification"]["status"], "unknown")
        self.assertEqual(self.report("", log_complete=True, qualification_scope=SCOPE)["operating_qualification"]["status"], "unknown")

    def test_suppressed_or_conflicting_warning_counters_block_absence_based_qualification(self):
        for text in ["25 warnings suppressed.\n" + FOOTER,
                     "WARNING: 25 warnings suppressed.\n" + WARNING_FOOTER,
                     "NOTICE: 25 warnings suppressed.\n" + FOOTER,
                     FOOTER + "\nspectre completes with 1 error, 2 warnings."]:
            self.assertEqual(self.report(text, log_complete=True, qualification_scope=SCOPE)["operating_qualification"]["status"], "unknown")

    def test_footer_counts_cannot_hide_missing_bodies_or_observed_errors(self):
        for text in [
            "spectre completes with 0 errors, 7 warnings, and 0 notices.",
            "spectre completes with 0 errors, 0 warnings, and 2 notices.",
            "ERROR (SFE-23): undefined model.\n" + FOOTER,
            "ERROR: Synthetic uncategorized error.\n" + FOOTER,
            "WARNING: Synthetic complete message.\n" + FOOTER,
        ]:
            with self.subTest(text=text):
                result = self.report(text, log_complete=True, qualification_scope=SCOPE)
                self.assertEqual(result["operating_qualification"]["status"], "unknown")
                self.assertIn("diagnostic_body_coverage_incomplete", [e["code"] for e in result["operating_qualification"]["evidence"]])
        explicit_error = self.report("ERROR (SFE-23): undefined model.\n" + FOOTER,
                                     log_complete=True, qualification_scope=SCOPE)
        self.assertIn("error_diagnostic_present", [e["code"] for e in explicit_error["operating_qualification"]["evidence"]])

    def test_canonical_body_coverage_does_not_count_context_headers_twice(self):
        text = "Warning from spectre during circuit read-in.\n    WARNING (SPECTRE-9999): Synthetic warning.\n" + WARNING_FOOTER
        result = self.report(text, log_complete=True, qualification_scope=SCOPE)
        self.assertEqual(result["operating_qualification"]["status"], "passed_within_scope")
        coverage = result["diagnostics"]["observed_context"]["diagnostic_coverage"]
        self.assertEqual(coverage["observed_warnings"], 1)
        self.assertEqual(coverage["warning_line_numbers"], [2])
        self.assertTrue(coverage["structure_complete"])
        bodies = "\n".join(f"WARNING: Synthetic message {number}." for number in range(7))
        complete = self.report(bodies + "\nspectre completes with 0 errors, 7 warnings, and 0 notices.",
                               log_complete=True, qualification_scope=SCOPE)
        self.assertEqual(complete["operating_qualification"]["status"], "passed_within_scope")

    def test_complete_progress_does_not_supply_a_missing_notice_body(self):
        progress = ''.join('......' + str(number) for number in range(9, -1, -1))
        complete = self.report(progress + '\n' + FOOTER, log_complete=True, qualification_scope=SCOPE)
        self.assertEqual(complete['operating_qualification']['status'], 'passed_within_scope')
        missing_notice = self.report(progress + '\nspectre completes with 0 errors, 0 warnings, and 1 notice.',
                                     log_complete=True, qualification_scope=SCOPE)
        self.assertEqual(missing_notice['operating_qualification']['status'], 'unknown')
        coverage = missing_notice['diagnostics']['observed_context']['diagnostic_coverage']
        self.assertTrue(coverage['structure_complete'])
        self.assertEqual(coverage['observed_notices'], 0)
        self.assertIn('diagnostic_body_coverage_incomplete',
                      [row['code'] for row in missing_notice['operating_qualification']['evidence']])

    def test_ambiguous_repetition_dangling_headers_and_truncation_block_pass(self):
        for text in [
            FOOTER + "\n" + FOOTER,
            "Warning from spectre during circuit read-in.\n" + WARNING_FOOTER,
            "WARNING (SFE-1131):\n" + WARNING_FOOTER,
            "WARNING (SFE-11\n" + WARNING_FOOTER,
            "WARNING: Synthetic warning.\nWARNING: Synthetic warning.\n" + WARNING_FOOTER,
            FOOTER + "\nWARNING: Synthetic warning.",
            "[truncated]\n" + FOOTER,
            "WARNING: Synthetic warning.\n    First continuation.\n    Second continuation.\n" + WARNING_FOOTER,
        ]:
            with self.subTest(text=text):
                result = self.report(text, log_complete=True, qualification_scope=SCOPE)
                self.assertEqual(result["operating_qualification"]["status"], "unknown")
        positive = self.report("WARNING (CMI-2752): Synthetic voltage violation.\n[truncated]\n" + FOOTER,
                               log_complete=True, qualification_scope=SCOPE)
        self.assertEqual(positive["operating_qualification"]["status"], "violations_detected")

    def test_duplicate_scalefactor_is_not_automatically_a_geometry_violation(self):
        text = "WARNING (SFE-1131): Scope option scalefactor has also been specified.\n" + WARNING_FOOTER
        scoped = {**SCOPE, "checks": ["scale_override"]}
        result = self.report(text, log_complete=True, qualification_scope=scoped)
        self.assertEqual(result["operating_qualification"]["status"], "unknown")
        self.assertIn("scalefactor_duplicate_unknown", [e["code"] for e in result["operating_qualification"]["evidence"]])
        # The requested SOA-only checks never become a scale-factor certification.
        limited = self.report(text, log_complete=True, qualification_scope=SCOPE)
        self.assertEqual(limited["operating_qualification"]["status"], "passed_within_scope")
        self.assertNotIn("scale_override", limited["operating_qualification"]["scope"]["checks"])
        for rhs, status in [("0.90", "passed_within_scope"), ("1.0", "unknown")]:
            result = self.report(f"WARNING: scalefactor=0.9 redefined as scalefactor={rhs}.\n" + WARNING_FOOTER,
                                 log_complete=True, qualification_scope=scoped)
            self.assertEqual(result["operating_qualification"]["status"], status)

    def test_invalid_data_cannot_become_functional_pass_and_target_miss_stays_valid(self):
        result = self.report(acceptance={"data_validity": "valid", "specification": "fail"},
                             qualification_scope=SCOPE, log_complete=True, dependency_audit=audit())
        self.assertEqual(result["measurement_validity"]["status"], "valid")
        self.assertEqual(result["functional_performance"]["status"], "fail")
        self.assertEqual(result["operating_qualification"]["status"], "passed_within_scope")
        self.assertEqual(result["dependency_provenance"]["status"], "verified_within_scope")
        with self.assertRaises(DomainError):
            self.report(acceptance={"data_validity": "invalid", "specification": "pass"})

    def test_dependency_binding_completeness_and_matching_pre_run_hashes_are_required(self):
        self.assertEqual(self.report(dependency_audit=audit())["dependency_provenance"]["status"], "verified_within_scope")
        for changes, status in [({"run_id": "other-run"}, "unknown"), ({"run_bound": False}, "unknown"),
                                ({"inventory_complete": False}, "incomplete"), ({"entries": []}, "incomplete")]:
            self.assertEqual(self.report(dependency_audit=audit(**changes))["dependency_provenance"]["status"], status)
        for changes, status in [({"hash_matches": False}, "mismatch"), ({"hash_matches": None}, "incomplete"),
                                ({"observed": False}, "incomplete"), ({"pre_pinned": False, "hash_matches": True}, "incomplete")]:
            source = audit(); source["entries"][0].update(changes); before = deepcopy(source)
            self.assertEqual(self.report(dependency_audit=source)["dependency_provenance"]["status"], status)
            self.assertEqual(source, before)

    def test_unsupported_scope_dependency_commands_and_paths_fail_safely(self):
        bad_scopes = [{**SCOPE, "checks": ["full_process_certification"]}, {**SCOPE, "checks": []},
                      {**SCOPE, "checks": ["soa_voltage", "soa_voltage"]}, {**SCOPE, "path": "/PRIVATE/key"},
                      {**SCOPE, "frozen_before_execution": 1}]
        for scope in bad_scopes:
            with self.assertRaises(DomainError) as error:
                self.report(qualification_scope=scope)
            self.assertNotIn("PRIVATE", str(error.exception))
        for changes in [{"path": "/PRIVATE/model"}, {"command": "PRIVATE"}, {"kind": "execute"},
                        {"hash_matches": 1}, {"dependency_id": "../../PRIVATE"}, {"pre_pinned": "true"}]:
            source = audit(); source["entries"][0].update(changes)
            with self.assertRaises(DomainError) as error:
                self.report(dependency_audit=source)
            self.assertNotIn("PRIVATE", str(error.exception))
        with self.assertRaises(DomainError): self.report(log_complete="true")
        with self.assertRaises(DomainError): self.report(dependency_audit=audit(entries=audit()["entries"] * 257))

    def test_registered_directories_are_references_with_individual_library_hashes(self):
        source = audit()
        source["entries"].append({"dependency_id": "synthetic-tmi-dir", "kind": "directory",
            "pre_pinned": True, "observed": True, "hash_matches": None})
        result = self.report(dependency_audit=source)
        self.assertEqual(result["dependency_provenance"]["status"], "verified_within_scope")
        self.assertEqual(result["dependency_provenance"]["scope"]["kind"], "observed_files_with_directory_references")
        self.assertEqual(result["dependency_provenance"]["evidence"][-1]["code"], "registered_directory_reference")
        source["entries"][0].update(kind="tmi_library", hash_matches=None)
        self.assertEqual(self.report(dependency_audit=source)["dependency_provenance"]["status"], "incomplete")
        for value in (False, True):
            source["entries"][-1]["hash_matches"] = value
            with self.assertRaises(DomainError):
                self.report(dependency_audit=source)
        source["entries"][-1].update(hash_matches=None, pre_pinned=False)
        self.assertEqual(self.report(dependency_audit=source)["dependency_provenance"]["status"], "incomplete")

    def test_new_schema_and_synthetic_example_validate_without_remote_refs(self):
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("Optional JSON Schema dependency is unavailable")
        root = Path(__file__).resolve().parents[1]
        schema = json.loads((root / "docs/contracts/simulation-report.v1.json").read_text())
        example = json.loads((root / "examples/simulation-report.example.json").read_text())
        validator = Draft202012Validator(schema)
        for result in [example, self.report(), self.report(log_complete=True, qualification_scope=SCOPE, dependency_audit=audit())]:
            validator.validate(result)
        self.assertNotIn("http", json.dumps(example))
        self.assertNotIn("PRIVATE", json.dumps(example))
        self.assertEqual(set(schema["$defs"]["scope"]["properties"]["checks"]["items"]["enum"]), QUALIFICATION_CHECKS)

    def test_schema_rejects_positive_status_with_incomplete_scope(self):
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("Optional JSON Schema dependency is unavailable")
        root = Path(__file__).resolve().parents[1]
        validator = Draft202012Validator(json.loads((root / "docs/contracts/simulation-report.v1.json").read_text()))
        for dimension, status in [("operating_qualification", "passed_within_scope"),
                                  ("dependency_provenance", "verified_within_scope")]:
            forged = self.report(); forged[dimension]["status"] = status
            self.assertFalse(validator.is_valid(forged))
        good = self.report(log_complete=True, qualification_scope=SCOPE, dependency_audit=audit())
        for dimension, key, value in [
            ("operating_qualification", "frozen_before_execution", False),
            ("operating_qualification", "log_complete", False),
            ("operating_qualification", "checks", []),
            ("operating_qualification", "manifest_sha256", None),
            ("dependency_provenance", "run_bound", False),
            ("dependency_provenance", "inventory_complete", False),
            ("dependency_provenance", "scope_id", None),
        ]:
            forged = deepcopy(good); forged[dimension]["scope"][key] = value
            self.assertFalse(validator.is_valid(forged))
        forged = deepcopy(good); forged["run_id"] = None
        self.assertFalse(validator.is_valid(forged))
        forged = self.report(); forged["functional_performance"]["status"] = "pass"
        self.assertFalse(validator.is_valid(forged))

    def test_report_performs_no_io_or_execution(self):
        with patch("builtins.open", side_effect=AssertionError("No files")), \
             patch("subprocess.Popen", side_effect=AssertionError("No processes")), \
             patch("socket.create_connection", side_effect=AssertionError("No network")):
            result = self.report(log_complete=True, qualification_scope=SCOPE, dependency_audit=audit())
        self.assertTrue(result["advisory_only"])
        self.assertIsNone(result["diagnostics"]["agent_proposal"])


if __name__ == "__main__":
    unittest.main()

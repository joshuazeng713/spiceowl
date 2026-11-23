"""Original small PSF fixtures for the shared parser and bounded summary API."""

import json
import unittest
from unittest.mock import patch

from spiceowl.integrations.spectre_results import (
    MAX_METADATA_BYTES, MAX_PSF_BYTES, MAX_SAMPLES, MAX_SIGNALS, PsfError,
    parse_psf_ascii, summarize_psf_ascii,
)
from test_smoke_results import psf_fixture, results as legacy_results


class SpectreResultsTests(unittest.TestCase):
    def setUp(self):
        self.values = {"time": [0, 1e-9, 2e-9], "input": [0, 1.2, 0], "output": [1.2, 0, 1.2]}
        self.text = psf_fixture(self.values)

    def check(self, result, name):
        return next(item["status"] for item in result["checks"] if item["name"] == name)

    def test_generic_values_and_units_are_usable_for_local_csv_without_toy_names(self):
        waveform = parse_psf_ascii(self.text)
        self.assertEqual(waveform["values"], self.values)
        self.assertEqual(waveform["units"], {"time": "s", "input": "V", "output": "V"})
        self.assertEqual(waveform["sample_count"], 3)
        self.assertEqual(waveform["format"], "psf_ascii_scalar_time_v1")
        self.assertEqual(json.loads(json.dumps(waveform, allow_nan=False)), waveform)

    def test_legacy_script_imports_the_same_parser_and_error_without_private_copies(self):
        self.assertIs(legacy_results.parse_psf_ascii, parse_psf_ascii)
        self.assertIs(legacy_results.PsfError, PsfError)
        self.assertFalse(hasattr(legacy_results, "_tokens"))
        self.assertFalse(hasattr(legacy_results, "_references"))
        self.assertEqual(legacy_results.parse_psf_ascii(self.text)["values"], self.values)

    def test_summary_has_only_bounded_structure_and_never_claims_specification_pass(self):
        result = summarize_psf_ascii(self.text, required_signals=["input", "output"])
        self.assertEqual(result["schema"], "spiceowl.spectre-result-summary.v1")
        self.assertEqual(result["data_validity"], "valid")
        self.assertEqual(result["specification"], "unknown")
        self.assertEqual(result["sample_count"], 3)
        self.assertEqual(result["signal_names"], ["input", "output"])
        self.assertEqual((result["time_start_s"], result["time_end_s"]), (0, 2e-9))
        for name in ("parse", "finite", "aligned", "time_monotonic", "required_signals"):
            self.assertEqual(self.check(result, name), "pass")
        self.assertEqual(self.check(result, "coverage"), "unknown")
        self.assertNotIn("values", result); self.assertNotIn("waveforms", result)
        self.assertLess(len(json.dumps(result)), 2048)

    def test_absent_text_is_unknown_but_empty_value_section_is_invalid(self):
        for text in ("", " \n\t"):
            result = summarize_psf_ascii(text)
            self.assertEqual(result["data_validity"], "unknown")
            self.assertEqual(result["error_code"], "psf_missing")
        empty = self.text.split("VALUE\n")[0] + "VALUE\nEND\n"
        result = summarize_psf_ascii(empty)
        self.assertEqual(result["data_validity"], "invalid")
        self.assertEqual(result["sample_count"], 0)

    def test_reordered_duplicate_and_nonfinite_samples_are_invalid(self):
        for times in ([0, 0, 2e-9], [0, 2e-9, 1e-9]):
            result = summarize_psf_ascii(psf_fixture(self.values | {"time": times}))
            self.assertEqual(result["data_validity"], "invalid")
            self.assertEqual(self.check(result, "time_monotonic"), "fail")
            self.assertEqual(result["specification"], "unknown")
        token = '"output" 1.2000000000000000e+00'
        self.assertIn(token, self.text)
        for value in ("NaN", "inf", "1e999"):
            result = summarize_psf_ascii(self.text.replace(token, '"output" '+value, 1))
            self.assertEqual(result["data_validity"], "invalid")
            self.assertEqual(self.check(result, "finite"), "fail")

    def test_missing_required_signal_is_invalid_without_inventing_signal_data(self):
        result = summarize_psf_ascii(self.text, required_signals=["input", "not-saved"])
        self.assertEqual(result["data_validity"], "invalid")
        self.assertEqual(self.check(result, "required_signals"), "fail")
        self.assertEqual(result["error_code"], "psf_required_signals_missing")
        self.assertEqual(result["signal_names"], ["input", "output"])
        self.assertEqual(result["sample_count"], 3)

    def test_missing_or_duplicate_sample_and_wrong_header_count_are_invalid(self):
        token = '"output" 1.2000000000000000e+00\n'
        malformed = [self.text.replace(token, "", 1), self.text.replace(token, token+token, 1),
                     self.text.replace('"PSF sweep points" 3', '"PSF sweep points" 4')]
        for text in malformed:
            with self.subTest(text=text[:80]):
                result = summarize_psf_ascii(text)
                self.assertEqual(result["data_validity"], "invalid")
                self.assertEqual(self.check(result, "aligned"), "fail")

    def test_nonsecond_time_units_are_not_relabelled_as_seconds(self):
        text = self.text.replace('"units" "s"', '"units" "ms"')
        parsed = parse_psf_ascii(text)
        self.assertEqual(parsed["units"]["time"], "ms")
        result = summarize_psf_ascii(text)
        self.assertIsNone(result["time_start_s"]); self.assertIsNone(result["time_end_s"])
        self.assertEqual(result["specification"], "unknown")

    def test_64_signals_supported_but_65_rejected(self):
        for size in (MAX_SIGNALS, MAX_SIGNALS+1):
            values = {"time": [0, 1e-9], **{f"node{i}": [0, .1] for i in range(size)}}
            result = summarize_psf_ascii(psf_fixture(values))
            self.assertEqual(result["data_validity"], "valid" if size == MAX_SIGNALS else "invalid")
        self.assertEqual(len(parse_psf_ascii(psf_fixture({"time": [0], **{f"node{i}": [0] for i in range(MAX_SIGNALS)}}))["values"]), 65)

    def test_real_sample_boundary_is_enforced_without_truncating(self):
        values = {"time": list(range(MAX_SAMPLES)), "node": [0] * MAX_SAMPLES}
        text = psf_fixture(values)
        self.assertLess(len(text.encode()), MAX_PSF_BYTES)
        self.assertEqual(parse_psf_ascii(text)["sample_count"], MAX_SAMPLES)
        overflow = text.replace('"PSF sweep points" '+str(MAX_SAMPLES), '"PSF sweep points" '+str(MAX_SAMPLES+1))
        with self.assertRaises(PsfError) as raised:
            parse_psf_ascii(overflow)
        self.assertEqual(raised.exception.code, "psf_limit")
        # Also enforce the limit when the optional header declaration is absent.
        with patch("spiceowl.integrations.spectre_results.MAX_SAMPLES", 2):
            no_count = self.text.replace('"PSF sweep points" 3\n', '')
            self.assertEqual(summarize_psf_ascii(no_count)["error_code"], "psf_limit")

    def test_byte_metadata_and_name_limits_have_safe_errors(self):
        cases = ["x"*(MAX_PSF_BYTES+1), "é"*(MAX_PSF_BYTES//2+1),
                 self.text.replace('"PSFversion" "1.00"', '"private" "'+'p'*MAX_METADATA_BYTES+'"'),
                 self.text.replace('"input"', '"'+'n'*161+'"'),
                 self.text.replace('"input"', '"private\\u0000"'), "PRIVATE\x00", "\ud800", None]
        for text in cases:
            with self.subTest(kind=type(text)), self.assertRaises(PsfError) as raised:
                parse_psf_ascii(text)
            self.assertNotIn("PRIVATE", str(raised.exception))
            result = summarize_psf_ascii(text)
            self.assertEqual(result["data_validity"], "invalid")
            self.assertNotIn("PRIVATE", json.dumps(result))

    def test_wrong_required_signal_arguments_fail_before_parsing(self):
        for required in ("input", (), ["input", "input"], [True], [[]], [""], ["x\n"], ["n"*161], ["n"]*65):
            with self.subTest(required=required), self.assertRaises(PsfError):
                summarize_psf_ascii(self.text, required_signals=required)

    def test_unsupported_syntax_complex_traces_and_missing_end_stay_invalid(self):
        for text in (self.text.replace("FLOAT DOUBLE", "COMPLEX DOUBLE"), self.text.removesuffix("END\n"),
                     self.text + '"PRIVATE" 1\n', self.text.replace("SWEEP\n", "VALUE\n")):
            result = summarize_psf_ascii(text)
            self.assertEqual(result["data_validity"], "invalid")
            self.assertEqual(result["specification"], "unknown")

    def test_parser_and_summary_do_not_open_files_or_start_processes(self):
        with patch("builtins.open", side_effect=AssertionError("No file IO")), \
                patch("subprocess.Popen", side_effect=AssertionError("No process")):
            self.assertEqual(parse_psf_ascii(self.text)["sample_count"], 3)
            self.assertEqual(summarize_psf_ascii(self.text)["data_validity"], "valid")


if __name__ == "__main__":
    unittest.main()

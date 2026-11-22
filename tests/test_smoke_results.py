"""Authored synthetic PSF fixtures; these tests never run a simulator or SSH."""

from contextlib import redirect_stdout
from copy import deepcopy
import csv
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("inverter_smoke_results", ROOT / "scripts" / "inverter_smoke_results.py")
results = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(results)


def synthetic_waveform(*, output_shift_s=150e-12):
    def pulse(time):
        if time < 5e-9:
            return 0.0
        phase = (time - 5e-9) % 1e-8
        if phase < 1e-10:
            return 1.2 * phase / 1e-10
        if phase < 5.1e-9:
            return 1.2
        if phase < 5.2e-9:
            return 1.2 * (5.2e-9 - phase) / 1e-10
        return 0.0
    time = [index * 1e-11 for index in range(5001)]
    return {"time": time, "vin": [pulse(t) for t in time],
            "vout": [1.2 - pulse(t - output_shift_s) for t in time], "vdd": [1.2] * len(time)}


def psf_fixture(values):
    lines = ['HEADER', '"PSFversion" "1.00"', f'"PSF sweep points" {len(values["time"])}',
             'TYPE', '"time_type" FLOAT DOUBLE PROP(', '"units" "s"', ')',
             '"voltage_type" FLOAT DOUBLE PROP(', '"units" "V"', ')',
             'SWEEP', '"time" "time_type"', 'TRACE']
    for name in values:
        if name != "time":
            lines.append(f'"{name}" "voltage_type"')
    lines.append('VALUE')
    for index in range(len(values["time"])):
        for name in values:
            lines.append(f'"{name}" {values[name][index]:.16e}')
    return '\n'.join([*lines, 'END', ''])


class PsfAsciiTests(unittest.TestCase):
    def setUp(self):
        self.values = {"time": [0, 1e-11], "vin": [0, 1.2], "vout": [1.2, 0], "vdd": [1.2, 1.2]}
        self.text = psf_fixture(self.values)

    def test_scalar_values_and_declared_units_round_trip(self):
        wave = results.parse_psf_ascii(self.text)
        self.assertEqual(wave["values"], self.values)
        self.assertEqual(wave["units"], {"time": "s", "vin": "V", "vout": "V", "vdd": "V"})

    def test_missing_end_or_last_signal_is_rejected(self):
        with self.assertRaises(results.PsfError):
            results.parse_psf_ascii(self.text.removesuffix('END\n'))
        with self.assertRaises(results.PsfError):
            results.parse_psf_ascii(self.text.rsplit('"vdd"', 1)[0] + 'END\n')

    def test_unknown_signal_duplicate_trace_and_value_are_rejected(self):
        invalid = [self.text.replace('"vout" "voltage_type"', '"vin" "voltage_type"'),
                   self.text.replace('VALUE\n', 'VALUE\n"surprise" 0\n'),
                   self.text.replace('VALUE\n', 'VALUE\n"time" 0\n"vin" 0\n"vin" 0\n')]
        for text in invalid:
            with self.subTest(text=text[:100]), self.assertRaises(results.PsfError):
                results.parse_psf_ascii(text)

    def test_header_counts_extra_text_and_bad_syntax_rejected(self):
        invalid = [self.text.replace('"PSF sweep points" 2', '"PSF sweep points" 3'),
                   self.text + '"vin" 0\n', self.text.replace('FLOAT DOUBLE', 'COMPLEX DOUBLE'),
                   self.text.replace('"units" "s"', '"units" "s" "units" "s"'),
                   self.text.replace('"vin" 0.0000000000000000e+00', '"vin" nan'),
                   self.text.replace('"vin" 0.0000000000000000e+00', '"vin" 1e999')]
        for text in invalid:
            with self.subTest(text=text[:100]), self.assertRaises(results.PsfError):
                results.parse_psf_ascii(text)

    def test_time_reordering_and_duplicates_rejected(self):
        for time in ([0, 0], [1e-11, 0]):
            with self.subTest(time=time), self.assertRaises(results.PsfError):
                results.parse_psf_ascii(psf_fixture(self.values | {"time": time}))

    def test_optional_vdd_is_not_fabricated_in_csv(self):
        values = self.values.copy()
        del values["vdd"]
        wave = results.parse_psf_ascii(psf_fixture(values))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "waveforms.csv"
            results.export_csv(wave, path)
            with path.open(newline="") as stream:
                rows = list(csv.reader(stream))
        self.assertEqual(rows[0], ["time_s", "vin_v", "vout_v"])
        self.assertEqual(len(rows), 3)


class InverterAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.acceptance = json.loads((ROOT / "examples/inverter/acceptance.json").read_text())
        cls.waveform = results.parse_psf_ascii(psf_fixture(synthetic_waveform()))

    def validate(self, waveform=None, **kwargs):
        return results.validate_waveform(waveform or self.waveform, self.acceptance,
                                         execution_result=kwargs.get("execution_result", "completed"),
                                         source_hashes=kwargs.get("source_hashes", self.acceptance["input_sha256"]))

    def test_synthetic_valid_logic_and_direction_paired_delays(self):
        report = self.validate()
        self.assertEqual(report["execution_result"], "completed")
        self.assertEqual(report["measurement_validity"], "valid")
        self.assertEqual(report["toy_inverter_behavior"], "pass")
        self.assertFalse(report["technology_performance_claim"])
        pairs = report["propagation_delays"]["pairs"]
        self.assertEqual(len(pairs), 9)
        for pair in pairs:
            self.assertEqual(pair["status"], "measured")
            self.assertNotEqual(pair["input_direction"], pair["output_direction"])
            self.assertAlmostEqual(pair["delay_s"] / 1e-12, 150, places=5)

    def test_valid_data_with_bad_output_is_specification_failure(self):
        waveform = deepcopy(self.waveform)
        waveform["values"]["vout"] = [0.6] * waveform["sample_count"]
        report = self.validate(waveform)
        self.assertEqual(report["measurement_validity"], "valid")
        self.assertEqual(report["toy_inverter_behavior"], "fail")

    def test_wrong_input_invalidates_experiment_not_output_specification(self):
        waveform = deepcopy(self.waveform)
        waveform["values"]["vin"] = [0.6] * waveform["sample_count"]
        report = self.validate(waveform)
        self.assertEqual(report["measurement_validity"], "invalid")
        self.assertEqual(report["toy_inverter_behavior"], "not_evaluated")

    def test_failed_or_unknown_execution_is_separate_from_waveforms(self):
        for execution, validity in (("failed", "invalid"), ("unknown", "indeterminate")):
            with self.subTest(execution=execution):
                report = self.validate(execution_result=execution)
                self.assertEqual(report["execution_result"], execution)
                self.assertEqual(report["measurement_validity"], validity)
                self.assertEqual(report["toy_inverter_behavior"], "not_evaluated")

    def test_source_identity_and_units_are_preconditions(self):
        self.assertEqual(self.validate(source_hashes={})["measurement_validity"], "invalid")
        for unit in (None, "mV"):
            waveform = deepcopy(self.waveform)
            waveform["units"]["vin"] = unit
            self.assertEqual(self.validate(waveform)["measurement_validity"], "invalid")

    def test_missing_signal_or_interior_gap_is_not_filled(self):
        waveform = deepcopy(self.waveform)
        del waveform["values"]["vin"]
        self.assertEqual(self.validate(waveform)["measurement_validity"], "invalid")
        waveform = deepcopy(self.waveform)
        for series in waveform["values"].values():
            del series[2500]
        report = self.validate(waveform)
        self.assertEqual(report["measurement_validity"], "invalid")
        self.assertFalse(next(item["passed"] for item in report["data_checks"] if item["name"] == "maximum_sample_gap"))

    def test_delay_is_report_only_and_absent_crossings_remain_unavailable(self):
        waveform = results.parse_psf_ascii(psf_fixture(synthetic_waveform(output_shift_s=-300e-12)))
        report = self.validate(waveform)
        self.assertEqual(report["measurement_validity"], "valid")
        self.assertEqual(report["toy_inverter_behavior"], "pass")
        self.assertTrue(report["propagation_delays"]["warnings"])
        for pair in report["propagation_delays"]["pairs"]:
            self.assertEqual(pair["status"], "unavailable")
            self.assertIsNone(pair["delay_s"])

    def test_cli_writes_separate_invalid_result_for_truncated_psf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "truncated.tran"
            path.write_text(psf_fixture({"time": [0], "vin": [0], "vout": [1.2]}).removesuffix('END\n'))
            with redirect_stdout(io.StringIO()):
                status = results.main(['--psf', str(path), '--acceptance', str(ROOT / 'examples/inverter/acceptance.json'),
                                       '--netlist', str(ROOT / 'examples/inverter/tb.scs'), '--execution-result', 'completed',
                                       '--origin', 'synthetic', '--output-dir', str(root / 'report')])
            report = json.loads((root / 'report/validation.json').read_text())
            self.assertEqual(status, 2)
            self.assertEqual(report['execution_result'], 'completed')
            self.assertEqual(report['measurement_validity'], 'invalid')
            self.assertEqual(report['provenance']['origin'], 'synthetic')
            self.assertFalse((root / 'report/waveforms.csv').exists())

    def test_incompatible_units_never_export_values_labeled_as_volts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'millivolts.tran'
            path.write_text(psf_fixture(synthetic_waveform()).replace('"units" "V"', '"units" "mV"'))
            with redirect_stdout(io.StringIO()):
                status = results.main(['--psf', str(path), '--acceptance', str(ROOT / 'examples/inverter/acceptance.json'),
                                       '--netlist', str(ROOT / 'examples/inverter/tb.scs'), '--execution-result', 'completed',
                                       '--origin', 'synthetic', '--output-dir', str(root / 'report'), '--plots'])
            report = json.loads((root / 'report/validation.json').read_text())
            self.assertEqual(status, 2)
            self.assertEqual(report['measurement_validity'], 'invalid')
            self.assertIn('not converted or relabeled', report['export_error'])
            self.assertEqual(report['artifacts'], [])
            for suffix in ('csv', 'png', 'svg'):
                self.assertFalse((root / f'report/waveforms.{suffix}').exists())
            self.assertTrue(path.exists(), 'The original PSF remains untouched.')


if __name__ == '__main__':
    unittest.main()

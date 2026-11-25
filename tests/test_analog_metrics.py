"""Tests for generic lossless analog measurement recipes (M3) and batch spec integration."""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from spiceowl.batch import DirectBatchManager, _validate_specs
from spiceowl.direct import DirectExecutionManager
from spiceowl.domain.common import DomainError
from spiceowl.experiments.analog_metrics import (
    ANALOG_METRICS_SCHEMA,
    extract_named_metric,
    measure_ac_response,
    measure_dc_operating_point,
    measure_transient_signal,
    measure_waveform_query,
)
from spiceowl.hosts import HostRegistry


class TestAnalogMetrics(unittest.TestCase):
    def test_refuses_downsampled_preview(self) -> None:
        with self.assertRaises(DomainError) as ctx:
            measure_transient_signal([0.0, 1e-9], [0.0, 1.0], exact=False, measurement_safe=False)
        self.assertEqual(ctx.exception.code, "measurement_unsafe_downsampled")

        with self.assertRaises(DomainError) as ctx2:
            measure_waveform_query({
                "exact": False,
                "measurement_safe": False,
                "x": [0.0, 1e-9],
                "signals": {"v(out)": {"values": [0.0, 1.0]}},
            })
        self.assertEqual(ctx2.exception.code, "measurement_unsafe_downsampled")

    def test_rc_step_rise_time_and_settling(self) -> None:
        # Ideal 1st-order RC step: v(t) = 1.0 * (1 - exp(-t / tau)), tau = 10 ns
        # Analytic 10%-90% rise time = tau * ln(9) ≈ 21.972 ns
        # Analytic 2% settling time = tau * ln(50) ≈ 39.120 ns
        tau = 10e-9
        n = 2001
        xs = [i * (100e-9 / (n - 1)) for i in range(n)]
        ys = [1.0 - math.exp(-t / tau) for t in xs]

        res = measure_transient_signal(xs, ys, signal_name="v(out)", tol_pct=2.0)
        self.assertEqual(res["schema"], ANALOG_METRICS_SCHEMA)
        self.assertTrue(res["exact"] and res["measurement_safe"])
        m = res["metrics"]
        self.assertAlmostEqual(m["rise_time"], tau * math.log(9.0), delta=0.3e-9)
        self.assertIsNone(m["fall_time"])
        self.assertAlmostEqual(m["settling_time"], tau * math.log(50.0), delta=0.5e-9)
        self.assertAlmostEqual(m["overshoot_pct"], 0.0, delta=0.05)
        self.assertGreater(m["slew_rate_pos"], 3.0e7)
        self.assertEqual(extract_named_metric(res, "rise_time"), m["rise_time"])

    def test_underdamped_step_overshoot(self) -> None:
        # Damped sinusoid step with 20% overshoot
        n = 1000
        xs = [i * 1e-10 for i in range(n)]
        ys = [1.0 - math.exp(-t / 15e-9) * (math.cos(2 * math.pi * 25e6 * t)) for t in xs]
        res = measure_transient_signal(xs, ys, signal_name="v(rlc)", tol_pct=2.0)
        self.assertGreater(res["metrics"]["overshoot_pct"], 15.0)
        self.assertIsNotNone(res["metrics"]["settling_time"])

    def test_clock_period_frequency_duty_and_jitter(self) -> None:
        # 100 MHz clock (T = 10 ns), 40% duty cycle
        period = 10e-9
        xs = [i * 0.25e-9 for i in range(400)]
        ys = [1.2 if ((t % period) < 0.4 * period) else 0.0 for t in xs]
        res = measure_transient_signal(xs, ys, signal_name="v(clk)")
        m = res["metrics"]
        self.assertAlmostEqual(m["period_mean"], period, delta=0.1e-9)
        self.assertAlmostEqual(m["frequency_hz"], 100e6, delta=2e6)
        self.assertAlmostEqual(m["duty_cycle_pct"], 40.0, delta=2.0)
        self.assertLess(m["period_jitter_rms"], 0.1e-9)

    def test_two_pole_ac_response_gain_ugbw_and_phase_margin(self) -> None:
        # H(s) = A0 / ((1 + s/w1)(1 + s/w2)), A0 = 100 (40 dB), f1 = 1 kHz, f2 = 100 kHz
        # UGBW ≈ 100 kHz? Actually |H(j*w)| = 1 when f ≈ 78.6 kHz, PM ≈ 52 deg
        a0 = 100.0
        f1, f2 = 1e3, 100e3
        freqs = [10.0 ** (1.0 + 6.0 * i / 600.0) for i in range(601)]
        vals = [a0 / ((1 + 1j * (f / f1)) * (1 + 1j * (f / f2))) for f in freqs]
        res = measure_ac_response(freqs, vals, signal_name="v(out)")
        m = res["metrics"]
        self.assertAlmostEqual(m["dc_gain_db"], 40.0, delta=0.1)
        self.assertAlmostEqual(m["bw_3db_hz"], 1000.0, delta=40.0)
        self.assertGreater(m["ugbw_hz"], 50e3)
        self.assertLess(m["ugbw_hz"], 120e3)
        self.assertGreater(m["phase_margin_deg"], 40.0)
        self.assertLess(m["phase_margin_deg"], 65.0)

    def test_dc_static_power_and_offset(self) -> None:
        res = measure_dc_operating_point(vdd=1.2, i_supply=-250e-6, v_out=0.602, v_ref=0.600)
        self.assertAlmostEqual(res["metrics"]["static_power_w"], 300e-6, places=9)
        self.assertAlmostEqual(res["metrics"]["dc_offset"], 0.002, places=9)

    def test_batch_spec_validation_accepts_analog_metrics_and_direct_waveform_measure(self) -> None:
        specs = _validate_specs([
            {"name": "trise", "signal": "v(out)", "metric": "rise_time", "max": 50e-9, "unit": "s"},
            {"name": "tsettle", "signal": "v(out)", "metric": "settling_time", "max": 1e-6, "unit": "s"},
            {"name": "os", "signal": "v(out)", "metric": "overshoot_pct", "max": 20.0, "unit": "%"},
        ])
        self.assertEqual(len(specs), 3)
        with tempfile.TemporaryDirectory(prefix="spiceowl-analog-m-") as tmp:
            hosts = HostRegistry(tmp)
            mgr = DirectExecutionManager(tmp, hosts=hosts)
            run = mgr.create_demo_waveform_run(run_id="demo-m-wf", cycles=60, glitch_cycle=30)
            mres = mgr.waveform_measure(run["run_id"], "tran.tran.tran", "v(out)", tol_pct=2.0, allow_remote=False)
            self.assertTrue(mres["exact"] and mres["measurement_safe"])
            self.assertGreater(mres["metrics"]["peak_to_peak"], 0.5)
            self.assertIsNotNone(mres["metrics"]["rise_time"])
            # Ensure batch manager can be instantiated alongside
            bm = DirectBatchManager(tmp, mgr)
            self.assertEqual(bm.list(), [])


if __name__ == "__main__":
    unittest.main()

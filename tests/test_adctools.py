"""Independent analytical and real upstream checks; all data are synthetic."""
import importlib.metadata
import builtins
import json
import math
import os
import platform
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

try:
    import numpy as np
except ImportError:
    np = None
try:
    import scipy
except ImportError:
    scipy = None
try:
    UPSTREAM = importlib.metadata.version('adctoolbox') == '0.9.3'
except importlib.metadata.PackageNotFoundError:
    UPSTREAM = False

from spiceowl.adctools import AnalysisError, analyze, spec_analyze, coherent_fft, sine_fit3, sine_fit4
from spiceowl.adctools import weight_calibrate, reconstruct_codes, histogram_linearity, tran_metrics


def metric(report, name):
    return report['metrics'][name]['value']


class ResultProvenanceTests(unittest.TestCase):
    def test_runtime_versions_are_metadata_only_and_missing_is_explicit(self):
        from spiceowl.adctools.common import result
        def version(name):
            if name == 'scipy': raise importlib.metadata.PackageNotFoundError(name)
            return {'numpy': '2.5.5', 'adctoolbox': '0.9.3'}[name]
        original_import = builtins.__import__
        def guarded_import(name, *args, **kwargs):
            self.assertNotIn(name.split('.')[0], ('numpy', 'scipy', 'adctoolbox'))
            return original_import(name, *args, **kwargs)
        with patch('spiceowl.adctools.common.importlib.metadata.version', side_effect=version), \
                patch('builtins.__import__', side_effect=guarded_import):
            report = result('metadata_test', parameters={}, metrics={})
        self.assertEqual(report['numerical_environment'], {
            'python': platform.python_version(), 'numpy': '2.5.5', 'scipy': None, 'adctoolbox': '0.9.3'})
        json.dumps(report, allow_nan=False)


@unittest.skipIf(np is None, 'optional numerical extra not installed')
class SpectrumTests(unittest.TestCase):
    def setUp(self):
        self.n = 8192
        self.fs = 1e6
        self.f = 127 * self.fs / self.n
        self.t = np.arange(self.n) / self.fs
        self.base = dict(backend='spiceowl', sample_rate_hz=self.fs, fundamental_hz=self.f,
            window='rectangular', bin_half_width=0, dc_bin_half_width=0, dc_policy='remove_mean',
            sfdr_mode='peak_bin', signal_unit='V')

    def test_analytical_power_harmonics_noise_sfdr(self):
        amplitudes = {order: .001 / order for order in range(2, 10)}
        x = .8 * np.sin(2*np.pi*self.f*self.t + .31) + 4
        for order, amplitude in amplitudes.items():
            x += amplitude*np.sin(order*2*np.pi*self.f*self.t + .17*order)
        x += .002*np.sin(2*np.pi*1403*np.arange(self.n)/self.n)
        r = spec_analyze(x, **self.base, full_scale_peak=1, return_spectrum=True)
        distortion = sum(value**2/2 for value in amplitudes.values())
        noise = .002**2/2
        self.assertEqual(r['status'], 'valid')
        self.assertAlmostEqual(r['power']['signal'], .8**2/2, places=12)
        self.assertAlmostEqual(r['power']['noise'], noise, places=14)
        self.assertAlmostEqual(r['power']['distortion'], distortion, places=14)
        self.assertAlmostEqual(metric(r, 'sndr_db'), 10*math.log10(.8**2/2/(noise+distortion)), places=8)
        self.assertAlmostEqual(metric(r, 'snr_db'), 20*math.log10(.8/.002), places=8)
        self.assertAlmostEqual(metric(r, 'sfdr_db'), 20*math.log10(.8/.002), places=8)
        self.assertAlmostEqual(metric(r, 'thd_db'), 10*math.log10(distortion/(.8**2/2)), places=8)
        self.assertAlmostEqual(metric(r, 'signal_dbfs'), 20*math.log10(.8), places=10)
        for order, amplitude in amplitudes.items():
            self.assertAlmostEqual(metric(r, f'hd{order}_db'), 20*math.log10(amplitude/.8), places=8)
        self.assertAlmostEqual(sum(r['spectrum']['power_per_bin']), .8**2/2+noise+distortion, places=11)
        json.dumps(r, allow_nan=False)

    def test_periodic_windows_recover_tone_power(self):
        x = .8*np.sin(2*np.pi*self.f*self.t)+.01*np.sin(4*np.pi*self.f*self.t)
        for window, width, gain in [('hann', 1, .5), ('blackman_harris4', 3, .35875)]:
            r = spec_analyze(x, **{**self.base, 'window': window, 'bin_half_width': width,
                'dc_bin_half_width': width, 'sfdr_mode': 'integrated_lobe'})
            self.assertAlmostEqual(metric(r, 'signal_rms'), .8/math.sqrt(2), places=11)
            self.assertAlmostEqual(metric(r, 'sfdr_db'), 20*math.log10(80), places=8)
            self.assertAlmostEqual(r['parameters']['window_coherent_gain'], gain, places=12)

    def test_noise_statistics_and_enob_convention(self):
        x = .4*np.sin(2*np.pi*self.f*self.t)+np.random.default_rng(21).normal(0, .001, self.n)
        r = spec_analyze(x, **self.base)
        self.assertAlmostEqual(metric(r, 'snr_db'), 10*math.log10(.4**2/2/1e-6), delta=.35)
        self.assertAlmostEqual(metric(r, 'enob_bits'), (metric(r, 'sndr_db')-1.7609125905568124)/6.020599913279624)
        self.assertIsNone(metric(r, 'signal_dbfs'))
        self.assertNotIn('spectrum', r)

    def test_aliasing_harmonic_masks_and_unresolved_collision(self):
        f = self.fs*127/1024
        x = .8*np.sin(2*np.pi*f*self.t)+.01*np.sin(2*np.pi*7*f*self.t)
        r = spec_analyze(x, **{**self.base, 'fundamental_hz': f})
        self.assertEqual(r['status'], 'valid')
        self.assertAlmostEqual(metric(r, 'hd7_db'), 20*math.log10(.01/.8), places=8)
        r = spec_analyze(np.sin(2*np.pi*np.arange(self.n)/4), **{**self.base, 'fundamental_hz': self.fs/4})
        self.assertEqual(r['status'], 'invalid')
        self.assertEqual(r['specification_result'], 'not_evaluated')

    def test_coherence_validation_and_explicit_window(self):
        x = np.sin(2*np.pi*self.f*self.t)
        kwargs = {key: value for key, value in self.base.items() if key != 'window'}
        r = coherent_fft(x, **kwargs)
        self.assertTrue(r['parameters']['cycles_coprime_with_sample_count'])
        with self.assertRaisesRegex(AnalysisError, 'integer'):
            coherent_fft(x, **{**kwargs, 'fundamental_hz': self.f+1})
        with self.assertRaisesRegex(AnalysisError, 'Hodgson'):
            spec_analyze(x, **{**self.base, 'window': 'Hodgson'})
        with self.assertRaises(AnalysisError):
            spec_analyze(x, **{**self.base, 'window': 'hann'})

    def test_nonuniform_zero_nonfinite_and_missing_backend(self):
        times = self.t.copy(); times[12] += 1e-8
        with self.assertRaises(AnalysisError): spec_analyze(np.ones(self.n), **self.base, time_s=times)
        with self.assertRaises(AnalysisError): spec_analyze(np.zeros(self.n), **self.base)
        with self.assertRaises(AnalysisError): spec_analyze([float('nan')]*self.n, **self.base)
        with self.assertRaises(AnalysisError): analyze('sine_fit3', {'samples': list(range(10))})

    def test_window_roundoff_cannot_turn_dc_into_a_tone(self):
        for window, width in [('hann', 1), ('blackman_harris4', 3)]:
            with self.assertRaises(AnalysisError):
                spec_analyze(np.ones(self.n), **{**self.base, 'window': window, 'dc_policy': 'mask_only',
                    'bin_half_width': width, 'dc_bin_half_width': width})


@unittest.skipIf(np is None, 'optional numerical extra not installed')
class FitAndCalibrationTests(unittest.TestCase):
    def test_three_parameter_sine_on_irregular_timestamps(self):
        t = np.linspace(2, 3, 1000) + np.arange(1000)**2*1e-8
        y = .7*np.sin(2*np.pi*19.7*(t-t[0])-.4)+.3
        r = sine_fit3(y, backend='spiceowl', time_s=t, frequency_hz=19.7, signal_unit='V')
        self.assertAlmostEqual(metric(r, 'amplitude_peak'), .7, places=12)
        self.assertAlmostEqual(metric(r, 'phase_rad'), -.4, places=12)
        self.assertAlmostEqual(metric(r, 'offset'), .3, places=12)
        self.assertLess(metric(r, 'residual_rms'), 1e-13)

    @unittest.skipIf(scipy is None, 'optional scipy not installed')
    def test_four_parameter_fit_and_frequency_degeneracy(self):
        t = np.arange(1000)/1000
        y = .7*np.sin(2*np.pi*19.73*t+.4)+.3
        r = sine_fit4(y, backend='spiceowl', sample_rate_hz=1000, time_s=t,
            frequency_guess_hz=19.7, frequency_bounds_hz=[19.5, 20], signal_unit='V')
        self.assertEqual(r['status'], 'valid')
        self.assertAlmostEqual(metric(r, 'frequency_hz'), 19.73, places=5)
        self.assertAlmostEqual(metric(r, 'phase_rad'), .4, places=5)
        f = 1e-4
        r = sine_fit4(np.sin(2*np.pi*f*t+np.pi/2), backend='spiceowl', sample_rate_hz=1000,
            frequency_guess_hz=f, frequency_bounds_hz=[.5*f, 1.5*f], signal_unit='V')
        self.assertEqual(r['status'], 'invalid')
        self.assertIsNone(metric(r, 'frequency_hz'))
        self.assertIn('frequency_fit_not_identifiable', r['warnings'])

    def test_constant_sine_fit_is_not_a_valid_tone(self):
        r = sine_fit3(np.ones(1000), backend='spiceowl', sample_rate_hz=1000, frequency_hz=20, signal_unit='V')
        self.assertEqual(r['status'], 'invalid')
        self.assertIsNone(metric(r, 'phase_rad'))

    @unittest.skipIf(scipy is None, 'optional scipy not installed')
    def test_high_frequency_fit_does_not_invent_optimizer_noise_floor(self):
        t = np.arange(32768)/32768; f = 10000.123
        y = .5*np.sin(2*np.pi*f*t+.7)+.2
        r = sine_fit4(y, backend='spiceowl', sample_rate_hz=32768, frequency_guess_hz=10000,
            frequency_bounds_hz=[f-.45, f+.45], signal_unit='V')
        self.assertEqual(r['status'], 'valid')
        self.assertLess(metric(r, 'residual_rms'), 1e-9)

    def test_nonbinary_supervised_weights_with_independent_validation(self):
        rng = np.random.default_rng(3)
        weights = np.array([1, .45, .22, .09])
        bits = rng.integers(-1, 2, size=(600, 4)); held = rng.integers(-1, 2, size=(100, 4))
        r = weight_calibrate(bits, bits@weights+.1, backend='spiceowl', reference_unit='V',
            validation_decisions=held, validation_reference=held@weights+.1)
        self.assertTrue(r['identifiable'])
        np.testing.assert_allclose(r['weights'], weights, atol=1e-12)
        self.assertAlmostEqual(r['offset'], .1, places=12)
        self.assertLess(metric(r, 'validation_rmse'), 1e-12)
        reconstructed = reconstruct_codes(held, r['weights'], backend='spiceowl', reference_unit='V', offset=r['offset'])
        np.testing.assert_allclose(reconstructed['reconstructed'], held@weights+.1, atol=1e-12)

    def test_rank_deficient_weights_and_invalid_decisions(self):
        bits = np.tile([[0, 0], [1, 1]], (50, 1))
        r = weight_calibrate(bits, bits[:, 0], backend='spiceowl', reference_unit='V')
        self.assertEqual(r['status'], 'invalid'); self.assertIsNone(r['weights'])
        with self.assertRaises(AnalysisError):
            reconstruct_codes([[0, 2], [1, 0]], [1, .5], backend='spiceowl', reference_unit='V')


@unittest.skipIf(np is None, 'optional numerical extra not installed')
class HistogramTransientTests(unittest.TestCase):
    def test_uniform_histogram_and_zero_hit_candidates(self):
        codes = np.repeat(np.arange(16), 100)
        kwargs = dict(backend='spiceowl', bits=4, distribution='uniform', endpoint_policy='endpoint',
            signal_unit='V', stimulus_low=-1, stimulus_high=1)
        ideal = histogram_linearity(codes, **kwargs)
        self.assertAlmostEqual(metric(ideal, 'dnl_max_lsb'), 0)
        self.assertAlmostEqual(metric(ideal, 'inl_min_lsb'), 0)
        self.assertNotIn('counts', ideal)
        codes[codes == 8] = 9
        r = histogram_linearity(codes, **kwargs, return_details=True)
        self.assertEqual(r['dnl_lsb'][8], -1)
        self.assertEqual(r['dnl_lsb'][9], 1)
        self.assertEqual(r['zero_hit_codes'], [8])
        self.assertIsNone(r['dnl_lsb'][0]); self.assertIsNone(r['dnl_lsb'][-1])
        self.assertEqual(r['specification_result'], 'not_evaluated')

    def test_sine_cdf_and_endpoint_coverage(self):
        u = (np.arange(100000)+.5)/100000
        codes = np.clip(np.floor((-1.1*np.cos(np.pi*u)+1)*8), 0, 15)
        r = histogram_linearity(codes, backend='spiceowl', bits=4, distribution='sine',
            endpoint_policy='best_fit', signal_unit='V', sine_amplitude=1.1, sine_offset=0)
        self.assertLess(abs(metric(r, 'dnl_min_lsb')), .001)
        self.assertLess(abs(metric(r, 'inl_max_lsb')), .001)
        r = histogram_linearity(np.repeat(np.arange(1, 15), 4), backend='spiceowl', bits=4,
            distribution='uniform', endpoint_policy='endpoint', signal_unit='V', stimulus_low=-1, stimulus_high=1)
        self.assertEqual(r['status'], 'invalid'); self.assertNotIn('counts', r)

    def test_rc_settling_delay_slew_and_power_sign(self):
        tau = .001; t = np.linspace(0, .01, 10001); y = 1-np.exp(-t/tau)
        kwargs = dict(backend='spiceowl', initial=0, final=1, step_time_s=0, settling_band=.01,
            signal_unit='V', min_hold_s=.002, voltage_v=2, current_a=.001, current_sign='into_dut')
        r = tran_metrics(t, y, **kwargs)
        self.assertAlmostEqual(metric(r, 'settling_s'), -tau*math.log(.01), delta=1e-9)
        self.assertAlmostEqual(metric(r, 'delay_s'), tau*math.log(2), delta=1e-9)
        self.assertAlmostEqual(metric(r, 'transition_s'), tau*math.log(4), delta=1e-9)
        self.assertAlmostEqual(metric(r, 'slew_rate'), .6/(tau*math.log(4)), delta=.001)
        self.assertAlmostEqual(metric(r, 'average_power_w'), .002)
        self.assertAlmostEqual(metric(r, 'energy_j'), .002*t[-1])
        self.assertEqual(metric(r, 'overshoot_percent'), 0)
        falling = tran_metrics(t, 1-y, **{**kwargs, 'initial': 1, 'final': 0, 'current_sign': 'from_dut'})
        self.assertAlmostEqual(metric(falling, 'delay_s'), metric(r, 'delay_s'))
        self.assertAlmostEqual(metric(falling, 'average_power_w'), -.002)
        short = tran_metrics(t, y, **{**kwargs, 'min_hold_s': .009})
        self.assertIsNone(metric(short, 'settling_s'))

    def test_damped_overshoot_and_waveform_delay(self):
        damping = .3; omega = 1000; t = np.linspace(0, .04, 20001)
        y = 1-np.exp(-damping*omega*t)*(np.cos(omega*math.sqrt(1-damping**2)*t)
            + damping/math.sqrt(1-damping**2)*np.sin(omega*math.sqrt(1-damping**2)*t))
        r = tran_metrics(t, y, backend='spiceowl', initial=0, final=1, step_time_s=0,
            settling_band=.01, signal_unit='V', delay_reference='waveform', reference_values=y,
            reference_initial=0, reference_final=1, slew_fractions=[.1, .9])
        self.assertAlmostEqual(metric(r, 'overshoot_percent'), 100*math.exp(-damping*math.pi/math.sqrt(1-damping**2)), delta=.001)
        self.assertAlmostEqual(metric(r, 'delay_s'), 0)
        self.assertEqual(r['parameters']['slew_fractions'], [.1, .9])


@unittest.skipUnless(UPSTREAM and np is not None, 'pinned optional ADCToolbox not installed')
class UpstreamTests(unittest.TestCase):
    def setUp(self):
        self.n = 4096; self.fs = 1e6; self.f = 127*self.fs/self.n
        self.t = np.arange(self.n)/self.fs
        self.x = .8*np.sin(2*np.pi*self.f*self.t+.2)+.01*np.sin(4*np.pi*self.f*self.t)
        self.x += np.random.default_rng(8).normal(0, 1e-4, self.n)
        self.kwargs = dict(backend='adctoolbox', sample_rate_hz=self.fs, fundamental_hz=self.f,
            window='rectangular', bin_half_width=1, signal_unit='V', full_scale_peak=1,
            nf_method=3, fundamental_tolerance_bins=.5)

    def test_real_spectrum_and_wrong_declared_tone(self):
        r = spec_analyze(self.x, **self.kwargs)
        self.assertEqual(r['status'], 'valid'); self.assertEqual(r['backend_version'], '0.9.3')
        self.assertAlmostEqual(metric(r, 'sndr_db'), 20*math.log10(80), delta=.05)
        self.assertEqual(r['provenance']['upstream_api'], 'adctoolbox.spectrum.compute_spectrum.compute_spectrum')
        wrong = spec_analyze(self.x, **{**self.kwargs, 'fundamental_hz': 2*self.f})
        self.assertEqual(wrong['status'], 'invalid')
        self.assertTrue(all(row['value'] is None for row in wrong['metrics'].values()))

    def test_floor_limited_metrics_are_undefined(self):
        r = spec_analyze(.8*np.sin(2*np.pi*self.f*self.t), **self.kwargs, return_spectrum=True)
        self.assertIsNone(metric(r, 'snr_db')); self.assertIsNone(metric(r, 'hd2_db'))
        self.assertIsNone(metric(r, 'sndr_db')); self.assertIsNone(metric(r, 'enob_bits'))
        self.assertIn('upstream_numerical_floor_dominates_selected_metrics', r['warnings'])
        json.dumps(r, allow_nan=False)

    def test_real_fit_receives_explicit_frequency_and_iteration_policy(self):
        import adctoolbox
        from spiceowl.adctools import upstream
        with patch.object(adctoolbox, 'fit_sine_4param', wraps=adctoolbox.fit_sine_4param) as called:
            r = sine_fit3(self.x, backend='adctoolbox', sample_rate_hz=self.fs, time_s=self.t,
                frequency_hz=self.f, signal_unit='V')
            self.assertEqual(called.call_args.kwargs['max_iterations'], 0)
            self.assertEqual(called.call_args.kwargs['frequency_estimate'], self.f/self.fs)
        self.assertAlmostEqual(metric(r, 'amplitude_peak'), .8, delta=1e-5)
        self.assertAlmostEqual(metric(r, 'phase_rad'), .2, delta=1e-4)
        r = sine_fit4(self.x, backend='adctoolbox', sample_rate_hz=self.fs, frequency_guess_hz=self.f,
            frequency_bounds_hz=[self.f-20, self.f+20], signal_unit='V')
        self.assertEqual(r['status'], 'valid')
        self.assertAlmostEqual(metric(r, 'frequency_hz'), self.f, delta=.02)
        with self.assertRaises(AnalysisError):
            upstream.sine_fit(self.x, operation='sine_fit3', sample_rate_hz=self.fs,
                frequency_hz=self.f, frequency_guess_hz=self.f, signal_unit='V')

    def test_real_calibration_preserves_all_columns_and_sine_gauge(self):
        import adctoolbox
        codes = np.clip(np.floor((.9*np.sin(2*np.pi*self.f*self.t)+1)*32), 0, 63).astype(int)
        bits = ((codes[:, None] >> np.arange(6)) & 1).astype(float)
        with patch.object(adctoolbox, 'calibrate_weight_sine', wraps=adctoolbox.calibrate_weight_sine) as called:
            r = weight_calibrate(bits, backend='adctoolbox', sample_rate_hz=self.fs, fundamental_hz=self.f,
                nominal_weights=(2**np.arange(6)).tolist(), harmonic_order=1, return_reconstruction=True)
            self.assertFalse(called.call_args.kwargs['force_search'])
            self.assertEqual(called.call_args.kwargs['freq'], self.f/self.fs)
        self.assertEqual(r['status'], 'valid'); self.assertEqual(len(r['weights']), 6)
        self.assertEqual(r['weight_unit'], 'solver_unit_sine')
        self.assertFalse(r['rank_patch']['applied']); self.assertNotIn('sndr_db', r['metrics'])
        self.assertFalse(r['parameters']['physical_gain_identified'])
        np.testing.assert_allclose(r['reconstructed'], bits@np.asarray(r['weights'])+r['offset'], atol=1e-12)
        self.assertLess(abs(np.mean(r['reconstructed'])), .01)

    def test_upstream_clipped_masks_cannot_pass_as_resolved_harmonics(self):
        f = self.fs/4
        r = spec_analyze(.8*np.sin(2*np.pi*f*self.t), **{**self.kwargs, 'fundamental_hz': f})
        self.assertEqual(r['status'], 'invalid')
        self.assertIn('upstream_spectral_masks_overlap_or_truncate', r['warnings'])

    def test_wrong_or_missing_upstream_version_never_falls_back(self):
        from spiceowl.adctools import upstream
        with patch.object(upstream.importlib.metadata, 'version', return_value='0.9.4'):
            with self.assertRaisesRegex(AnalysisError, 'exactly'): spec_analyze(self.x, **self.kwargs)
        with patch.object(upstream.importlib.metadata, 'version', side_effect=importlib.metadata.PackageNotFoundError):
            with self.assertRaisesRegex(AnalysisError, 'pinned'): spec_analyze(self.x, **self.kwargs)


@unittest.skipIf(np is None, 'optional numerical extra not installed')
class CliTests(unittest.TestCase):
    def run_cli(self, *arguments, text=None):
        env = dict(os.environ)
        env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1]/'src')
        run = subprocess.run([sys.executable, '-m', 'spiceowl.adctools', *arguments],
            input=text, text=True, capture_output=True, env=env, timeout=30)
        return run, json.loads(run.stdout)

    def test_json_request_and_csv_column_binding(self):
        t = np.arange(1000)/1000; y = .7*np.sin(2*np.pi*20*t+.4)+.2
        parameters = dict(backend='spiceowl', frequency_hz=20, signal_unit='V', sample_rate_hz=1000)
        run, report = self.run_cli('sine_fit3', '--request', '-', text=json.dumps({**parameters, 'samples': y.tolist()}))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertAlmostEqual(metric(report, 'amplitude_peak'), .7)
        with tempfile.TemporaryDirectory() as directory:
            params = Path(directory)/'params.json'; dataset = Path(directory)/'data.csv'
            params.write_text(json.dumps(parameters))
            dataset.write_text('time,output\n'+'\n'.join(f'{a},{b}' for a, b in zip(t, y)))
            run, report = self.run_cli('sine_fit3', '--input', str(dataset), '--params', str(params),
                '--column', 'samples=output', '--column', 'time_s=time')
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertAlmostEqual(metric(report, 'offset'), .2)

    def test_rejects_nonfinite_duplicate_and_ambiguous_stdin(self):
        for text in ['{"samples":[NaN]}', '{"backend":"spiceowl","backend":"adctoolbox"}']:
            run, report = self.run_cli('sine_fit3', '--request', '-', text=text)
            self.assertEqual(run.returncode, 2); self.assertEqual(report['status'], 'invalid')
        run, report = self.run_cli('sine_fit3', '--input', '-', '--params', '-', text='{}')
        self.assertEqual(run.returncode, 2); self.assertEqual(report['error']['code'], 'ambiguous_input')

    def test_invalid_scientific_fit_exit_is_distinct(self):
        request = dict(backend='spiceowl', samples=[1]*100, frequency_hz=10, sample_rate_hz=1000, signal_unit='V')
        run, report = self.run_cli('sine_fit3', '--request', '-', text=json.dumps(request))
        self.assertEqual(run.returncode, 3); self.assertEqual(report['status'], 'invalid')


if __name__ == '__main__': unittest.main()

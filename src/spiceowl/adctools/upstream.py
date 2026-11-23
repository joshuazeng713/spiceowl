"""Explicit, version-pinned calls to ADCToolbox; never an automatic fallback."""
import contextlib
import importlib.metadata
import io
import math
import warnings

from .common import numpy, vector, scalar, unit, require, time_axis, result, AnalysisError, clean
from .calibration import decisions_array

VERSION = '0.9.3'
WHEEL_SHA256 = '78210fb93e0e8544f5f61eb0137ff33b485bd28d7ad5d1a8ea7cc898eb1aaf4f'


def _package():
    try: version = importlib.metadata.version('adctoolbox')
    except importlib.metadata.PackageNotFoundError as error:
        raise AnalysisError('dependency_missing', 'Install the pinned optional adctoolbox==0.9.3 dependency.') from error
    require(version == VERSION, 'upstream_version_mismatch', 'The adapter requires exactly adctoolbox==' + VERSION + '.')
    import adctoolbox
    require(adctoolbox.__version__ == VERSION, 'upstream_version_mismatch', 'Imported ADCToolbox does not match its installed distribution.')
    return adctoolbox


def _call(function, *args, **kwargs):
    with warnings.catch_warnings(record=True) as caught, contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter('always')
        value = function(*args, **kwargs)
    return value, ['upstream_' + type(row.message).__name__ for row in caught]


def _provenance(api):
    return {'upstream_project': 'Arcadia-1/ADCToolbox', 'upstream_api': api,
            'release': VERSION, 'release_wheel_sha256': WHEEL_SHA256,
            'license': 'MIT', 'source_code_copied': False,
            'version_check': 'installed_distribution_and_module', 'silent_fallback': False}


def spec_analyze(samples, *, sample_rate_hz, fundamental_hz, window, bin_half_width,
                 signal_unit, full_scale_peak, nf_method, fundamental_tolerance_bins,
                 harmonic_orders=tuple(range(2, 10)), time_s=None, return_spectrum=False):
    np = numpy(); _package(); y = vector(samples, 'samples', minimum=32)
    rate = scalar(sample_rate_hz, 'sample_rate_hz', positive=True)
    frequency = scalar(fundamental_hz, 'fundamental_hz', positive=True)
    scale = scalar(full_scale_peak, 'full_scale_peak', positive=True); signal_unit = unit(signal_unit)
    require(float(np.ptp(y)) > np.finfo(float).eps * max(float(np.max(np.abs(y))), 1e-300) * 32,
            'no_fundamental_power', 'Input variation is unresolved at floating-point precision.')
    time_axis(len(y), rate, time_s)
    require(0 < frequency < rate / 2, 'invalid_frequency', 'Declared fundamental must be below Nyquist.')
    windows = {'rectangular': 'boxcar', 'hann': 'hann', 'blackman_harris4': 'blackmanharris'}
    require(window in windows, 'unsupported_window', 'Use rectangular, hann or blackman_harris4; unknown upstream window fallback is prohibited.')
    minimum = {'rectangular': 0, 'hann': 1, 'blackman_harris4': 3}[window]
    require(type(bin_half_width) is int and minimum <= bin_half_width <= 64, 'invalid_mask', 'Use an explicit bounded side-bin width covering the window main lobe.')
    require(type(nf_method) is int and nf_method in (1, 2, 3, 4), 'invalid_noise_method', 'Choose upstream nf_method 1=median, 2=trimmed mean, 3=exclude harmonics, or 4=legacy exclusion; auto is disabled.')
    tolerance = scalar(fundamental_tolerance_bins, 'fundamental_tolerance_bins', positive=True)
    require(tolerance <= .5, 'invalid_frequency_tolerance', 'Fundamental verification tolerance must be at most half a bin.')
    require(isinstance(harmonic_orders, (list, tuple)) and 1 <= len(harmonic_orders) <= 8
            and list(harmonic_orders) == list(range(2, 2 + len(harmonic_orders))),
            'invalid_harmonics', 'Pinned upstream requires consecutive harmonics H2 through Hk, k<=9.')
    require(type(return_spectrum) is bool, 'invalid_parameter', 'return_spectrum must be boolean.')
    # This fixed release has no user-specified fundamental parameter. Its result
    # must match the declared tone; never quietly replace the requested tone.
    from adctoolbox.spectrum.compute_spectrum import compute_spectrum
    raw, notices = _call(compute_spectrum, y, fs=rate, max_scale_range=(-scale, scale),
        win_type=windows[window], side_bin=bin_half_width, osr=1,
        max_harmonic=harmonic_orders[-1], nf_method=nf_method,
        assumed_sig_pwr_dbfs=None, coherent_averaging=False, cutoff_freq=0, verbose=0)
    data, plot = raw['metrics'], raw['plot_data']; df = rate / len(y)
    detected = float(plot['fundamental_bin_fractional']) * df
    reasons = notices + ['upstream_fft_conventions_differ_from_spiceowl', 'upstream_uses_numerical_power_floors']
    valid = abs(detected - frequency) <= tolerance * df
    if not valid: reasons.append('upstream_detected_fundamental_does_not_match_declared_tone')
    collisions = clean(plot.get('collided_harmonics', []))
    if collisions: valid = False; reasons.append('upstream_harmonic_collision')
    # Upstream clips masks and deduplicates overlapping harmonic bins. Such
    # unresolved individual HD measurements must not silently be called valid.
    claimed = set(range(bin_half_width + 1))
    for center in [int(plot['fundamental_bin']), *map(int, plot['harmonic_bins'])]:
        mask = set(range(center - bin_half_width, center + bin_half_width + 1))
        if min(mask) < 0 or max(mask) >= len(plot['freq']) or mask & claimed:
            valid = False; reasons.append('upstream_spectral_masks_overlap_or_truncate')
            break
        claimed.update(mask)
    mapping = {'sndr_db': 'sndr_dbc', 'snr_db': 'snr_dbc', 'sfdr_db': 'sfdr_dbc',
               'thd_db': 'thd_dbc', 'enob_bits': 'enob', 'signal_dbfs': 'sig_pwr_dbfs',
               'noise_floor_dbfs': 'noise_floor_dbfs', 'noise_floor_dbfs_per_hz': 'nsd_dbfs_hz'}
    metrics = {name: float(data[source]) if valid else None for name, source in mapping.items()}
    harmonics = np.asarray(data['harmonics_dbc']).reshape(-1)
    metrics.update({'hd%d_db' % order: float(harmonics[index]) if valid and index < len(harmonics) else None
                    for index, order in enumerate(harmonic_orders)})
    # Upstream returns artificial finite ratios when these exact release floors
    # dominate. Keep the upstream convention, but never promote a floor to data.
    floor_limited = []
    if valid:
        signal_dbfs = float(data['sig_pwr_dbfs'])
        if signal_dbfs - float(data['snr_dbc']) <= -150 + 1e-9:
            floor_limited.extend(('snr_db', 'noise_floor_dbfs', 'noise_floor_dbfs_per_hz'))
        if signal_dbfs - float(data['sndr_dbc']) <= 10 * math.log10(2e-20):
            floor_limited.extend(('sndr_db', 'enob_bits'))
        if signal_dbfs + float(data['thd_dbc']) <= -150 + 1e-9:
            floor_limited.append('thd_db')
        for index, order in enumerate(harmonic_orders):
            if index < len(harmonics) and signal_dbfs + float(harmonics[index]) <= -150 + 1e-9:
                floor_limited.append('hd%d_db' % order)
        for name in floor_limited: metrics[name] = None
        if floor_limited: reasons.append('upstream_numerical_floor_dominates_selected_metrics')
    extra = {'provenance': _provenance('adctoolbox.spectrum.compute_spectrum.compute_spectrum'),
        'upstream_details': {'detected_fundamental_hz': detected, 'fundamental_bin': int(plot['fundamental_bin']),
            'fundamental_mask': [int(plot['sig_bin_start']), int(plot['sig_bin_end'])],
            'mask_stop_exclusive': True, 'collided_harmonics': collisions, 'noise_parts': clean(plot.get('noise_parts', {})),
            'floor_limited_metrics': floor_limited,
            'undefined_policy': 'noise_or_harmonic_at_1e-15_floor; SNDR_denominator_at_most_twice_1e-20_floor'}}
    if return_spectrum:
        extra['spectrum'] = {'frequency_hz': plot['freq'], 'power_db': plot['power_spectrum_db_plot'],
            'power_unit': 'dBFS/bin', 'frequency_unit': 'Hz', 'normalization': 'ADCToolbox 0.9.3 plot_data; includes numerical floor',
            'power_per_bin': None, 'psd': None}
    return result('spec_analyze', parameters={'sample_count': len(y), 'sample_rate_hz': rate,
        'fundamental_hz': frequency, 'fundamental_tolerance_bins': tolerance,
        'fundamental_policy': 'upstream_peak_detection_verified_against_declared_tone',
        'window': window, 'bin_half_width': bin_half_width, 'nf_method': nf_method,
        'signal_unit': signal_unit, 'full_scale_peak': scale, 'harmonic_orders': harmonic_orders,
        'dc_policy': 'upstream_prepare_fft_input_mean_removal_and_0_to_side_bin_exclusive_noise_mask',
        'sfdr_mode': 'upstream_extract_highest_spur', 'osr': 1, 'band_hz': [0, rate / 2],
        'coherent_averaging': False, 'create_plot': False}, metrics=metrics, diagnostics=reasons,
        status='valid' if valid else 'invalid', backend='adctoolbox', backend_version=VERSION, **extra)


def sine_fit(samples, *, operation, signal_unit, sample_rate_hz=None, time_s=None,
             frequency_hz=None, frequency_guess_hz=None, frequency_bounds_hz=None):
    np = numpy(); package = _package(); y = vector(samples, 'samples')
    require((operation == 'sine_fit3' and frequency_guess_hz is None and frequency_bounds_hz is None)
            or (operation == 'sine_fit4' and frequency_hz is None),
            'unused_frequency_parameter', 'Use frequency_hz only for sine_fit3; guess and bounds only for sine_fit4.')
    times = time_axis(len(y), sample_rate_hz, time_s)
    dt = np.diff(times); rate = scalar(sample_rate_hz, 'sample_rate_hz', positive=True) if sample_rate_hz is not None else 1 / float(np.mean(dt))
    require(bool(np.allclose(dt, 1 / rate, rtol=1e-6, atol=1e-12 / rate)), 'nonuniform_sampling', 'Pinned upstream sine fitting requires uniform samples.')
    frequency = scalar(frequency_hz if operation == 'sine_fit3' else frequency_guess_hz, 'frequency_hz', positive=True)
    require(frequency < rate / 2, 'invalid_frequency', 'Frequency must be below Nyquist.')
    phase_grid = 2 * np.pi * frequency * (times - times[0])
    design = np.column_stack((np.sin(phase_grid), np.cos(phase_grid), np.ones(len(y))))
    require(np.linalg.matrix_rank(design) == 3 and np.linalg.cond(design) <= 1e10,
            'sine_fit_not_identifiable', 'The declared sine fit is rank deficient or ill conditioned.')
    bounds = None
    if operation == 'sine_fit4':
        require(isinstance(frequency_bounds_hz, (list, tuple)) and len(frequency_bounds_hz) == 2,
                'invalid_frequency_bounds', 'Declare a local frequency interval for verifying the upstream fit.')
        bounds = [scalar(v, 'frequency_bounds_hz', positive=True) for v in frequency_bounds_hz]
        require(bounds[0] < frequency < bounds[1] < rate / 2 and (bounds[1] - bounds[0]) * (times[-1] - times[0]) <= 1,
                'invalid_frequency_bounds', 'Fit interval must bracket the guess, remain below Nyquist and span at most one inverse record duration.')
    raw, notices = _call(package.fit_sine_4param, y, frequency_estimate=frequency / rate,
        max_iterations=0 if operation == 'sine_fit3' else 100, tolerance=1e-12, verbose=0)
    fitted = float(raw['frequency']) * rate
    valid = bool(raw['converged']) and (bounds is None or bounds[0] < fitted < bounds[1])
    amplitude = float(raw['amplitude']); unit_name = unit(signal_unit)
    if operation == 'sine_fit4':
        from .fitting import _solve, _frequency_identifiability
        coefficients = _solve(y, times, fitted)[0]
        jacobian_rank, jacobian_condition = _frequency_identifiability(times, fitted, coefficients)
        if jacobian_rank < 4 or jacobian_condition is None or jacobian_condition > 1e10:
            valid = False; notices.append('frequency_fit_not_identifiable')
    if amplitude <= np.finfo(float).eps * max(float(np.max(np.abs(y))), 1e-300) * 32:
        valid = False; notices.append('sine_fit_not_identifiable')
    if not valid: notices.append('upstream_fit_not_converged_or_outside_declared_bounds')
    phase = (float(raw['phase']) + np.pi / 2 + np.pi) % (2 * np.pi) - np.pi
    return result(operation, parameters={'sample_count': len(y), 'sample_rate_hz': rate, 'signal_unit': unit_name,
        'time_origin_s': float(times[0]), 'frequency_guess_hz': frequency, 'frequency_bounds_hz': bounds,
        'phase_convention': 'sine phase at first supplied sample', 'upstream_frequency_units': 'cycles_per_sample',
        'frequency_estimation': 'disabled', 'max_iterations': 0 if operation == 'sine_fit3' else 100},
        metrics={name: value if valid else None for name, value in {'amplitude_peak': amplitude,
            'offset': float(raw['dc_offset']), 'phase_rad': float(phase), 'frequency_hz': fitted,
            'residual_rms': float(raw['rmse'])}.items()},
        metric_units={'amplitude_peak': unit_name, 'offset': unit_name, 'phase_rad': 'rad',
                      'frequency_hz': 'Hz', 'residual_rms': unit_name}, diagnostics=notices,
        status='valid' if valid else 'invalid', backend='adctoolbox', backend_version=VERSION,
        provenance=_provenance('adctoolbox.fit_sine_4param'), converged=bool(raw['converged']))


def weight_calibrate(decisions, reference=None, *, sample_rate_hz, fundamental_hz, nominal_weights,
                     harmonic_order, time_s=None, return_reconstruction=False):
    np = numpy(); package = _package(); bits = decisions_array(decisions)
    require(reference is None, 'unsupported_reference', 'Upstream sine calibration has no supervised reference input; use backend=spiceowl for reference-based calibration.')
    require(bool(np.isin(bits, [0, 1]).all()), 'invalid_decisions', 'Pinned upstream calibration accepts binary bit/segment columns.')
    rate = scalar(sample_rate_hz, 'sample_rate_hz', positive=True); frequency = scalar(fundamental_hz, 'fundamental_hz', positive=True)
    time_axis(len(bits), rate, time_s)
    require(frequency < rate / 2, 'invalid_frequency', 'Fundamental must be below Nyquist.')
    nominal = vector(nominal_weights, 'nominal_weights', minimum=1)
    require(len(nominal) == bits.shape[1], 'stage_mismatch', 'Explicit nominal weights must match all bit columns; no binary radix is inferred.')
    require(type(harmonic_order) is int and 1 <= harmonic_order <= 9, 'invalid_harmonics', 'harmonic_order must be 1 through 9.')
    require(type(return_reconstruction) is bool, 'invalid_parameter', 'return_reconstruction must be boolean.')
    design = np.column_stack((bits, np.ones(len(bits)))); rank = int(np.linalg.matrix_rank(design))
    parameters = {'sample_count': len(bits), 'stage_count': bits.shape[1], 'sample_rate_hz': rate,
        'fundamental_hz': frequency, 'nominal_weights': nominal, 'harmonic_order': harmonic_order,
        'force_search': False, 'scale_convention': 'solver_unit_sine', 'physical_gain_identified': False,
        'harmonics_are_reference_nuisance_terms_not_removed_distortion': True}
    if rank < design.shape[1]:
        return result('weight_calibrate', parameters=parameters, metrics={}, status='invalid',
            diagnostics=['calibration_not_identifiable_upstream_rank_patch_not_authorized'],
            backend='adctoolbox', backend_version=VERSION, provenance=_provenance('adctoolbox.calibrate_weight_sine'),
            weights=None, offset=None, identifiable=False)
    raw, notices = _call(package.calibrate_weight_sine, bits, freq=frequency / rate, force_search=False,
        nominal_weights=nominal, harmonic_order=harmonic_order, learning_rate=.5,
        reltol=1e-12, max_iter=100, verbose=0, frequency_policy='python', osr=1)
    weights = np.asarray(raw['weight']).reshape(-1); offset = np.asarray(raw['offset']).reshape(-1)
    valid = len(weights) == bits.shape[1] and len(offset) == 1 and bool(np.isfinite(weights).all()) and bool(np.isfinite(offset).all())
    require(raw.get('scale_convention') == 'solver_unit_sine', 'upstream_contract_mismatch', 'Unexpected upstream calibration scale convention.')
    notices += ['unit_sine_gauge_does_not_identify_absolute_physical_capacitor_weights', 'fitted_residual_metrics_are_not_dynamic_fft_sndr']
    if raw.get('rank_patch', {}).get('applied') is not False:
        valid = False; notices.append('upstream_rank_patch_not_authorized')
    refined = float(raw['refined_frequency'])
    if not math.isclose(refined, frequency / rate, rel_tol=1e-12, abs_tol=0):
        valid = False; notices.append('upstream_changed_declared_frequency')
    # Both public calibration backends use an additive reconstruction offset.
    # Upstream's offset is instead the DC level to subtract from bits @ weight.
    extra = {'weights': weights if valid else None, 'offset': -float(offset[0]) if valid else None,
        'weight_unit': 'solver_unit_sine', 'rank': rank, 'identifiable': valid,
        'rank_patch': clean(raw.get('rank_patch', {})), 'refined_frequency_hz': refined * rate,
        'offset_convention': 'add_to_decisions_dot_weights; negative_upstream_dc_offset',
        'upstream_dc_offset': float(offset[0]) if valid else None,
        'provenance': _provenance('adctoolbox.calibrate_weight_sine')}
    if return_reconstruction:
        extra['reconstructed'] = np.asarray(raw['calibrated_signal']).reshape(-1) - float(offset[0]) if valid else None
    residual_defined = bool(np.any(np.asarray(raw['error']) != 0))
    if not residual_defined: notices.append('zero_fit_residual_ratio_is_undefined')
    return result('weight_calibrate', parameters=parameters,
        metrics={'fit_residual_snr_db': float(raw['snr_db']) if valid and residual_defined else None,
                 'fit_residual_enob_bits': float(raw['enob']) if valid and residual_defined else None},
        metric_units={'fit_residual_enob_bits': 'bit'}, diagnostics=notices,
        status='valid' if valid else 'invalid', backend='adctoolbox', backend_version=VERSION, **extra)

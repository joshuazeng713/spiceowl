"""One-record, one-sided power FFT with explicit masks and measurement conventions."""
import math

from .common import numpy, vector, scalar, unit, require, result, ratio_db


def _window(name, count):
    np = numpy(); phase = 2 * np.pi * np.arange(count) / count
    if name == 'rectangular': return np.ones(count)
    if name == 'hann': return .5 - .5 * np.cos(phase)
    if name == 'blackman_harris4':
        return .35875 - .48829 * np.cos(phase) + .14128 * np.cos(2 * phase) - .01168 * np.cos(3 * phase)
    require(False, 'unsupported_window', 'Supported periodic windows: rectangular, hann, blackman_harris4. Hodgson is not a verified window name.')


def spec_analyze(samples, *, sample_rate_hz, fundamental_hz, window,
                 bin_half_width, dc_bin_half_width, dc_policy, sfdr_mode, signal_unit,
                 harmonic_orders=tuple(range(2, 10)), band_hz=None, full_scale_peak=None,
                 time_s=None, return_spectrum=False):
    np = numpy(); y = vector(samples, 'samples', minimum=32); count = len(y)
    rate = scalar(sample_rate_hz, 'sample_rate_hz', positive=True)
    tone = scalar(fundamental_hz, 'fundamental_hz', positive=True)
    require(tone < rate / 2, 'invalid_frequency', 'fundamental_hz must lie strictly between DC and Nyquist.')
    for name, width in [('bin_half_width', bin_half_width), ('dc_bin_half_width', dc_bin_half_width)]:
        require(type(width) is int and 0 <= width <= 64, 'invalid_mask', name + ' must be an integer from 0 through 64.')
    require(dc_policy in ('remove_mean', 'mask_only'), 'invalid_dc_policy', 'Choose remove_mean or mask_only explicitly.')
    require(sfdr_mode in ('peak_bin', 'integrated_lobe'), 'invalid_sfdr_mode', 'Choose peak_bin or integrated_lobe explicitly.')
    require(type(return_spectrum) is bool, 'invalid_parameter', 'return_spectrum must be boolean.')
    require(isinstance(harmonic_orders, (list, tuple)) and len(harmonic_orders) <= 8
            and all(type(h) is int and 2 <= h <= 9 for h in harmonic_orders)
            and len(set(harmonic_orders)) == len(harmonic_orders), 'invalid_harmonics', 'Use distinct harmonic orders from 2 through 9.')
    signal_unit = unit(signal_unit)
    if full_scale_peak is not None: full_scale_peak = scalar(full_scale_peak, 'full_scale_peak', positive=True)
    if time_s is not None:
        times = vector(time_s, 'time_s', minimum=32)
        require(len(times) == count and bool(np.allclose(np.diff(times), 1 / rate, rtol=1e-6, atol=1e-12 / rate)),
                'nonuniform_sampling', 'FFT requires the declared uniform grid; no automatic resampling is performed.')
    if band_hz is None: band_hz = (0.0, rate / 2)
    require(isinstance(band_hz, (list, tuple)) and len(band_hz) == 2, 'invalid_band', 'band_hz is [low_hz, high_hz].')
    low, high = [scalar(v, 'band_hz') for v in band_hz]
    require(0 <= low < tone < high <= rate / 2, 'invalid_band', 'Analysis band must contain the fundamental and lie within [0, Nyquist].')
    win = _window(window, count)
    minimum_width = {'rectangular': 0, 'hann': 1, 'blackman_harris4': 3}[window]
    require(bin_half_width >= minimum_width and dc_bin_half_width >= minimum_width,
            'mask_too_narrow', 'Masks must include at least the coherent main lobe of the chosen window.')
    df = rate / count; cycles = tone / df; nearest = int(math.floor(cycles + .5))
    coherent = abs(cycles - nearest) <= 1e-6
    diagnostics = [] if coherent else ['noncoherent_leakage_outside_explicit_masks_counts_as_noise']
    centered = y - float(np.mean(y)) if dc_policy == 'remove_mean' else y.copy()
    power = np.abs(np.fft.rfft(centered * win)) ** 2 / (count * float(np.sum(win * win)))
    power[1:-1 if count % 2 == 0 else None] *= 2
    frequencies = np.fft.rfftfreq(count, 1 / rate)
    band = (frequencies >= low) & (frequencies <= high)
    dc = np.arange(len(power)) <= dc_bin_half_width
    def bins(center):
        return np.arange(center - bin_half_width, center + bin_half_width + 1, dtype=int)
    fundamental = bins(nearest)
    require(fundamental[0] > dc_bin_half_width and fundamental[-1] < len(power)
            and bool(band[fundamental].all()), 'invalid_fundamental_mask', 'Fundamental mask must be complete, inside the band, and separate from DC.')
    excluded = dc.copy(); excluded[fundamental] = True
    harmonic_rows = []; collision = False
    for order in harmonic_orders:
        aliased = abs((order * tone + rate / 2) % rate - rate / 2)
        center = int(math.floor(aliased / df + .5)); indices = bins(center)
        in_band = low <= aliased <= high
        reason = None
        if in_band:
            if indices[0] < 0 or indices[-1] >= len(power) or not bool(band[indices].all()):
                reason = 'harmonic_mask_truncated'
            elif bool(excluded[indices].any()): reason = 'harmonic_mask_overlap'
            if reason: collision = True
            else: excluded[indices] = True
        harmonic_rows.append({'order': order, 'aliased_hz': aliased, 'bin': center,
                              'bins': indices.tolist() if in_band and not reason else [],
                              'status': 'invalid' if reason else 'valid' if in_band else 'outside_band', 'reason': reason})
    parameters = {'sample_count': count, 'sample_rate_hz': rate, 'fundamental_hz': tone,
        'signal_unit': signal_unit, 'window': window, 'window_symmetry': 'periodic',
        'bin_half_width': bin_half_width, 'dc_bin_half_width': dc_bin_half_width,
        'dc_policy': dc_policy, 'sfdr_mode': sfdr_mode, 'harmonic_orders': harmonic_orders,
        'band_hz': [low, high], 'full_scale_peak': full_scale_peak,
        'enob_reference': 'measured_sine_amplitude', 'noise_mask_policy': 'exclude_dc_fundamental_selected_harmonics_no_interpolation',
        'power_normalization': 'one_sided_abs_rfft_squared_over_N_sum_window_squared',
        'bin_width_hz': df, 'coherent_cycles': cycles, 'coherent': coherent,
        'window_coherent_gain': float(np.mean(win)), 'window_enbw_bins': count * float(np.sum(win * win)) / float(np.sum(win)) ** 2}
    if collision:
        return result('spec_analyze', parameters=parameters, metrics={}, status='invalid',
                      diagnostics=diagnostics + ['harmonic_masks_cannot_be_resolved'], harmonics=harmonic_rows)
    signal = float(np.sum(power[fundamental])); noise_mask = band & ~excluded
    residual_mask = band & ~dc; residual_mask[fundamental] = False
    noise = float(np.sum(power[noise_mask])); residual = float(np.sum(power[residual_mask]))
    resolution = np.finfo(float).eps * max(float(np.max(np.abs(y))), 1e-300) * 32
    require(signal > resolution ** 2, 'no_fundamental_power', 'The specified fundamental mask contains no power resolved above input-scale floating-point precision.')
    distortion = 0.0
    for row in harmonic_rows:
        if row['status'] == 'valid':
            value = float(np.sum(power[row['bins']])); distortion += value
            row.update(power=value, hd_dbc=ratio_db(value, signal))
        else: row.update(power=None, hd_dbc=None)
    spur = None; spur_bin = None
    if sfdr_mode == 'peak_bin' and bool(residual_mask.any()):
        spur_bin = int(np.argmax(np.where(residual_mask, power, -1)))
        spur = float(power[spur_bin]); sfdr_signal = float(np.max(power[fundamental]))
    else:
        sfdr_signal = signal
        width = 2 * bin_half_width + 1
        integrated = np.convolve(power, np.ones(width), mode='same')
        complete = np.convolve(residual_mask.astype(int), np.ones(width, dtype=int), mode='same') == width
        if bool(complete.any()):
            spur_bin = int(np.argmax(np.where(complete, integrated, -1)))
            spur = float(integrated[spur_bin])
    mean_psd = float(np.mean(power[noise_mask])) / df if bool(noise_mask.any()) else None
    sndr = ratio_db(signal, residual); snr = ratio_db(signal, noise)
    metrics = {'sndr_db': sndr, 'snr_db': snr, 'sfdr_db': ratio_db(sfdr_signal, spur) if spur is not None else None,
        'thd_db': ratio_db(distortion, signal), 'enob_bits': (sndr - 1.7609125905568124) / 6.020599913279624 if sndr is not None else None,
        'signal_rms': math.sqrt(signal), 'signal_peak_equivalent': math.sqrt(2 * signal),
        'noise_rms': math.sqrt(noise), 'noise_floor_power_per_hz': mean_psd,
        'noise_floor_dbfs_per_hz': ratio_db(mean_psd, full_scale_peak ** 2 / 2) if mean_psd is not None and full_scale_peak is not None else None,
        'signal_dbfs': ratio_db(signal, full_scale_peak ** 2 / 2) if full_scale_peak is not None else None}
    metrics.update({'hd%d_db' % row['order']: row['hd_dbc'] for row in harmonic_rows})
    if sndr is None or snr is None or distortion == 0: diagnostics.append('zero_power_ratio_is_undefined_not_infinity')
    if full_scale_peak is not None and math.sqrt(2 * signal) > full_scale_peak:
        diagnostics.append('measured_tone_exceeds_declared_full_scale')
    extra = {'harmonics': harmonic_rows, 'power': {'signal': signal, 'noise': noise, 'distortion': distortion,
              'noise_plus_distortion': residual, 'unit': signal_unit + '^2'},
             'spur': {'bin': spur_bin, 'frequency_hz': frequencies[spur_bin] if spur_bin is not None else None,
                      'power': spur, 'fundamental_reference_power': sfdr_signal},
             'masks': {'fundamental_bins': fundamental, 'noise_bin_count': int(noise_mask.sum()),
                       'dc_bins': list(range(dc_bin_half_width + 1))}}
    if return_spectrum:
        reference = full_scale_peak ** 2 / 2 if full_scale_peak is not None else 1.0
        extra['spectrum'] = {'frequency_hz': frequencies, 'power_per_bin': power, 'psd': power / df,
            'power_db': [ratio_db(float(value), reference) for value in power], 'frequency_unit': 'Hz',
            'power_unit': 'dBFS/bin' if full_scale_peak is not None else 'dB re 1 ' + signal_unit + '^2/bin'}
    return result('spec_analyze', parameters=parameters, metrics=metrics, diagnostics=diagnostics,
                  metric_units={'signal_rms': signal_unit, 'signal_peak_equivalent': signal_unit,
                                'noise_rms': signal_unit, 'noise_floor_power_per_hz': signal_unit + '^2/Hz'}, **extra)


def coherent_fft(samples, *, sample_rate_hz, fundamental_hz, **kwargs):
    np = numpy(); count = len(vector(samples, 'samples', minimum=32))
    rate = scalar(sample_rate_hz, 'sample_rate_hz', positive=True)
    frequency = scalar(fundamental_hz, 'fundamental_hz', positive=True)
    cycles = count * frequency / rate
    require(abs(cycles - round(cycles)) <= 1e-6, 'noncoherent_record', 'coherent_fft requires an integer number of cycles in the supplied record.')
    require('window' not in kwargs, 'invalid_parameter', 'coherent_fft fixes the rectangular window; use spec_analyze for other windows.')
    data = spec_analyze(samples, sample_rate_hz=rate, fundamental_hz=frequency, window='rectangular', **kwargs)
    data['operation'] = 'coherent_fft'
    data['parameters']['cycles_coprime_with_sample_count'] = math.gcd(round(cycles), count) == 1
    return data

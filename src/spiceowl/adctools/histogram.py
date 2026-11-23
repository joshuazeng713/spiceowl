"""Code density transfer estimates with explicit stimulus distribution and fit line."""
from .common import numpy, vector, scalar, unit, require, result


def histogram_linearity(codes, *, bits, distribution, endpoint_policy, signal_unit,
                        stimulus_low=None, stimulus_high=None, sine_amplitude=None, sine_offset=None,
                        min_expected_hits=10, return_details=False):
    np = numpy(); values = vector(codes, 'codes', minimum=32)
    require(type(bits) is int and 2 <= bits <= 16, 'invalid_bits', 'bits must be an integer from 2 through 16.')
    levels = 2 ** bits
    require(bool((values == np.floor(values)).all()) and bool(((values >= 0) & (values < levels)).all()),
            'invalid_codes', 'Codes must be unsigned integer labels in [0, 2^bits-1].')
    require(distribution in ('uniform', 'sine'), 'invalid_distribution', 'Choose the known input distribution: uniform or sine.')
    require(endpoint_policy in ('endpoint', 'best_fit'), 'invalid_endpoint_policy', 'Choose endpoint or best_fit transition-line normalization.')
    signal_unit = unit(signal_unit); threshold = scalar(min_expected_hits, 'min_expected_hits', positive=True)
    require(type(return_details) is bool, 'invalid_parameter', 'return_details must be boolean.')
    counts = np.bincount(values.astype(int), minlength=levels)
    cdf = np.cumsum(counts)[:-1] / len(values)
    if distribution == 'uniform':
        low = scalar(stimulus_low, 'stimulus_low'); high = scalar(stimulus_high, 'stimulus_high')
        require(low < high and sine_amplitude is None and sine_offset is None, 'invalid_stimulus', 'Uniform input requires low < high and no sine parameters.')
        transitions = low + (high - low) * cdf
        stimulus = {'low': low, 'high': high}
    else:
        amplitude = scalar(sine_amplitude, 'sine_amplitude', positive=True)
        offset = scalar(sine_offset, 'sine_offset')
        require(stimulus_low is None and stimulus_high is None, 'invalid_stimulus', 'Sine input requires amplitude/offset only.')
        transitions = offset - amplitude * np.cos(np.pi * cdf)
        stimulus = {'amplitude_peak': amplitude, 'offset': offset}
    parameters = {'sample_count': len(values), 'bits': bits, 'distribution': distribution,
        'signal_unit': signal_unit, 'stimulus': stimulus, 'endpoint_policy': endpoint_policy,
        'saturation_codes': [0, levels - 1], 'saturation_dnl_policy': 'undefined_unbounded_outer_bins',
        'min_expected_hits': threshold, 'input_distribution_verified': False, 'return_details': return_details}
    if counts[0] == 0 or counts[-1] == 0 or transitions[-1] <= transitions[0]:
        return result('histogram_linearity', parameters=parameters, metrics={}, status='invalid',
            diagnostics=['full_range_endpoint_coverage_missing'],
            **({'counts': counts, 'transitions': None, 'dnl_lsb': None, 'inl_lsb': None} if return_details else {}))
    indices = np.arange(1, levels)
    if endpoint_policy == 'endpoint':
        slope = float((transitions[-1] - transitions[0]) / (levels - 2))
        intercept = float(transitions[0] - slope)
    else:
        design = np.column_stack((indices, np.ones(len(indices))))
        slope, intercept = [float(v) for v in np.linalg.lstsq(design, transitions, rcond=None)[0]]
    require(slope > 0, 'invalid_transfer', 'Estimated transition-line slope must be positive.')
    dnl = np.diff(transitions) / slope - 1
    inl = (transitions - (intercept + slope * indices)) / slope
    ideal = intercept + slope * indices
    if distribution == 'uniform': expected = np.full(levels - 2, len(values) * slope / (high - low))
    else:
        ideal_cdf = .5 + np.arcsin(np.clip((ideal - offset) / amplitude, -1, 1)) / np.pi
        expected = len(values) * np.diff(ideal_cdf)
    zero_hit = np.flatnonzero(counts[1:-1] == 0) + 1
    low_statistics = np.flatnonzero(expected < threshold) + 1
    warnings = ['finite_histogram_estimate_not_missing_code_proof']
    if len(low_statistics): warnings.append('insufficient_expected_hits_for_some_codes')
    details = {'counts': counts, 'transition_code_indices': indices, 'transitions': transitions,
               'dnl_lsb': [None] + dnl.tolist() + [None], 'inl_lsb': inl,
               'zero_hit_codes': zero_hit, 'low_statistics_codes': low_statistics,
               'expected_hits': expected} if return_details else {}
    return result('histogram_linearity', parameters={**parameters, 'return_details': return_details},
        metrics={'dnl_min_lsb': float(np.min(dnl)), 'dnl_max_lsb': float(np.max(dnl)),
                 'inl_min_lsb': float(np.min(inl)), 'inl_max_lsb': float(np.max(inl)),
                 'lsb_estimate': slope, 'zero_hit_code_count': int(len(zero_hit))},
        metric_units={'dnl_min_lsb': 'LSB', 'dnl_max_lsb': 'LSB', 'inl_min_lsb': 'LSB',
                      'inl_max_lsb': 'LSB', 'lsb_estimate': signal_unit, 'zero_hit_code_count': 'code'},
        diagnostics=warnings, low_statistics_code_count=int(len(low_statistics)),
        reference_line={'intercept': intercept, 'slope': slope, 'unit': signal_unit}, **details)

"""Finite-record, piecewise-linear transient measurements with explicit endpoints."""
import math

from .common import numpy, vector, scalar, unit, require, result


def _crossings(times, progress, level):
    found = []
    for index in range(len(times) - 1):
        left, right = progress[index:index + 2]
        if left < level <= right:
            found.append(float(times[index] + (times[index + 1] - times[index]) * (level - left) / (right - left)))
    if progress[0] == level: found.insert(0, float(times[0]))
    return found


def _crop(times, values, start, stop):
    np = numpy(); inner = (times > start) & (times < stop)
    return (np.concatenate(([start], times[inner], [stop])),
            np.concatenate(([np.interp(start, times, values)], values[inner], [np.interp(stop, times, values)])))


def tran_metrics(time_s, values, *, initial, final, step_time_s, settling_band,
                 signal_unit, min_hold_s=0.0, slew_fractions=(0.2, 0.8), delay_fraction=0.5,
                 delay_reference='step_time', reference_values=None, reference_initial=None,
                 reference_final=None, voltage_v=None, current_a=None, current_sign=None,
                 power_window_s=None):
    np = numpy(); times = vector(time_s, 'time_s', minimum=3); y = vector(values, 'values', minimum=3)
    require(len(times) == len(y) and bool((np.diff(times) > 0).all()), 'invalid_time', 'Transient times must align and strictly increase.')
    start = scalar(step_time_s, 'step_time_s'); band = scalar(settling_band, 'settling_band', positive=True)
    initial = scalar(initial, 'initial'); final = scalar(final, 'final'); signal_unit = unit(signal_unit)
    hold = scalar(min_hold_s, 'min_hold_s')
    require(initial != final and times[0] <= start < times[-1] and hold >= 0,
            'invalid_step', 'Declare distinct initial/final levels, an observed step time, and nonnegative hold time.')
    require(isinstance(slew_fractions, (list, tuple)) and len(slew_fractions) == 2,
            'invalid_thresholds', 'slew_fractions requires [low, high].')
    lower, upper = [scalar(v, 'slew_fractions') for v in slew_fractions]
    threshold = scalar(delay_fraction, 'delay_fraction')
    require(0 < lower < upper < 1 and 0 < threshold < 1, 'invalid_thresholds', 'Crossing fractions must lie strictly within (0, 1).')
    require(delay_reference in ('step_time', 'waveform'), 'invalid_delay_reference', 'Choose step_time or waveform delay reference.')
    post_time, post = _crop(times, y, start, float(times[-1]))
    delta = final - initial; progress = (post - initial) / delta
    crossings = {name: _crossings(post_time, progress, value) for name, value in
                 [('low', lower), ('high', upper), ('delay', threshold)]}
    warnings = ['settling_is_observed_only_through_record_end_piecewise_linear_interpolation']
    if any(len(value) > 1 for value in crossings.values()): warnings.append('multiple_threshold_crossings_first_crossing_reported')
    low_time = crossings['low'][0] if crossings['low'] else None
    high_time = next((value for value in crossings['high'] if low_time is not None and value > low_time), None)
    slew = abs(delta) * (upper - lower) / (high_time - low_time) if high_time is not None else None
    output_time = crossings['delay'][0] if crossings['delay'] else None
    input_time = start
    if delay_reference == 'waveform':
        reference = vector(reference_values, 'reference_values', minimum=3)
        ref_initial = scalar(reference_initial, 'reference_initial'); ref_final = scalar(reference_final, 'reference_final')
        require(len(reference) == len(times) and ref_initial != ref_final, 'invalid_reference', 'Provide aligned reference values and distinct reference levels.')
        ref_time, ref = _crop(times, reference, start, float(times[-1]))
        found = _crossings(ref_time, (ref - ref_initial) / (ref_final - ref_initial), threshold)
        input_time = found[0] if found else None
    else:
        require(reference_values is None and reference_initial is None and reference_final is None,
                'unused_reference', 'Reference waveform arguments require delay_reference=waveform.')
    outside = np.flatnonzero(np.abs(post - final) > band)
    if len(outside) == 0: settled_at = start
    elif outside[-1] == len(post) - 1: settled_at = None
    else:
        index = int(outside[-1]); boundary = final + math.copysign(band, post[index] - final)
        settled_at = float(post_time[index] + (post_time[index + 1] - post_time[index]) *
                           (boundary - post[index]) / (post[index + 1] - post[index]))
    if settled_at is not None and times[-1] - settled_at < hold:
        settled_at = None; warnings.append('settling_hold_interval_not_observed')
    metrics = {'settling_s': settled_at - start if settled_at is not None else None,
        'overshoot_percent': max(0.0, float(np.max(progress)) - 1) * 100,
        'undershoot_percent': max(0.0, -float(np.min(progress))) * 100,
        'slew_rate': slew, 'transition_s': high_time - low_time if high_time is not None else None,
        'delay_s': output_time - input_time if output_time is not None and input_time is not None else None,
        'average_power_w': None, 'peak_power_w': None, 'energy_j': None}
    power_parameters = None
    if voltage_v is not None or current_a is not None:
        require(voltage_v is not None and current_a is not None and current_sign in ('into_dut', 'from_dut'),
                'invalid_power_input', 'Power requires voltage_v, current_a, and current_sign=into_dut or from_dut.')
        def series(value, name):
            if isinstance(value, (int, float)) and not isinstance(value, bool): return np.full(len(times), scalar(value, name))
            value = vector(value, name, minimum=3)
            require(len(value) == len(times), 'sample_mismatch', name + ' must align with time_s.')
            return value
        voltage = series(voltage_v, 'voltage_v'); current = series(current_a, 'current_a')
        window = (start, float(times[-1])) if power_window_s is None else power_window_s
        require(isinstance(window, (list, tuple)) and len(window) == 2, 'invalid_power_window', 'power_window_s must contain start and stop.')
        a, b = [scalar(v, 'power_window_s') for v in window]
        require(times[0] <= a < b <= times[-1], 'invalid_power_window', 'Power window must be covered by the samples.')
        ptime, power = _crop(times, voltage * current * (1 if current_sign == 'into_dut' else -1), a, b)
        energy = float(np.sum((power[:-1] + power[1:]) * np.diff(ptime) / 2))
        metrics.update(average_power_w=energy / (b - a), peak_power_w=float(np.max(power)), energy_j=energy)
        power_parameters = {'window_s': [a, b], 'current_sign': current_sign,
                            'integration': 'trapezoid_of_sampled_instantaneous_power', 'positive_power': 'absorbed_by_dut'}
    else:
        require(current_sign is None and power_window_s is None, 'unused_power_parameter', 'Power metadata requires explicit voltage and current.')
    return result('tran_metrics', parameters={'sample_count': len(y), 'signal_unit': signal_unit,
        'initial': initial, 'final': final, 'step_time_s': start, 'settling_band': band,
        'settling_band_unit': signal_unit, 'min_hold_s': hold, 'slew_fractions': [lower, upper],
        'delay_fraction': threshold, 'delay_reference': delay_reference, 'crossing_policy': 'first_oriented_crossing',
        'observation_end_s': float(times[-1]), 'power': power_parameters}, metrics=metrics,
        metric_units={'settling_s': 's', 'overshoot_percent': '%', 'undershoot_percent': '%',
            'slew_rate': signal_unit + '/s', 'transition_s': 's', 'delay_s': 's',
            'average_power_w': 'W', 'peak_power_w': 'W', 'energy_j': 'J'},
        diagnostics=warnings, first_crossings_s={name: values[0] if values else None for name, values in crossings.items()},
        crossing_counts={name: len(values) for name, values in crossings.items()}, settled_at_s=settled_at)

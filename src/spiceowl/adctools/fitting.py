"""Three-parameter linear sine fit and bounded variable-projection frequency fit."""
import math

from .common import numpy, vector, scalar, unit, require, time_axis, result


def _solve(y, times, frequency):
    np = numpy(); relative = times - times[0]
    phase = 2 * np.pi * frequency * relative
    design = np.column_stack((np.sin(phase), np.cos(phase), np.ones(len(y))))
    coefficients, _, rank, singular = np.linalg.lstsq(design, y, rcond=None)
    condition = float(singular[0] / singular[-1]) if singular[-1] > 0 else None
    residual = y - design @ coefficients
    return coefficients, residual, int(rank), condition


def _frequency_identifiability(times, frequency, coefficients):
    """Unit-column Jacobian tests frequency independently of seconds/volts units."""
    np = numpy(); relative = times - times[0]
    phase = 2 * np.pi * frequency * relative
    a, b, _ = coefficients; amplitude = math.hypot(float(a), float(b))
    if amplitude == 0: return 0, None
    derivative = (2 * np.pi * relative / (times[-1] - times[0])
                  * ((a / amplitude) * np.cos(phase) - (b / amplitude) * np.sin(phase)))
    jacobian = np.column_stack((np.sin(phase), np.cos(phase), np.ones(len(times)), derivative))
    norms = np.linalg.norm(jacobian, axis=0)
    if bool((norms == 0).any()): return 0, None
    jacobian = jacobian / norms
    return int(np.linalg.matrix_rank(jacobian)), float(np.linalg.cond(jacobian))


def _report(operation, y, times, frequency, signal_unit, *, optimized=False, extra=None):
    np = numpy(); coefficients, residual, rank, condition = _solve(y, times, frequency)
    params = {'sample_count': len(y), 'signal_unit': signal_unit, 'time_origin_s': float(times[0]),
              'model': 'amplitude*sin(2*pi*frequency_hz*(t-time_origin_s)+phase_rad)+offset',
              'frequency_hz': frequency, 'residual_normalization': 'sqrt(sum(error^2)/N)', **(extra or {})}
    if rank < 3 or condition is None or condition > 1e10:
        return result(operation, parameters=params, metrics={}, status='invalid', diagnostics=['sine_fit_not_identifiable'], rank=rank, condition_number=condition)
    if optimized:
        jacobian_rank, jacobian_condition = _frequency_identifiability(times, frequency, coefficients)
        if jacobian_rank < 4 or jacobian_condition is None or jacobian_condition > 1e10:
            return result(operation, parameters=params, metrics={'frequency_hz': None},
                metric_units={'frequency_hz': 'Hz'}, status='invalid', diagnostics=['frequency_fit_not_identifiable'],
                jacobian_rank=jacobian_rank, jacobian_condition_number=jacobian_condition)
    a, b, offset = [float(v) for v in coefficients]
    amplitude = math.hypot(a, b)
    metrics = {'amplitude_peak': amplitude, 'offset': offset, 'phase_rad': math.atan2(b, a) if amplitude > 0 else None,
               'frequency_hz': frequency, 'residual_rms': math.sqrt(float(np.mean(residual ** 2))),
               'residual_std_dof': math.sqrt(float(np.sum(residual ** 2)) / (len(y) - (4 if optimized else 3)))}
    warnings = [] if amplitude > np.finfo(float).eps * max(float(np.max(np.abs(y))), 1e-300) * 32 else ['amplitude_unresolved']
    if warnings: metrics.update(phase_rad=None, frequency_hz=None if optimized else frequency)
    return result(operation, parameters=params, metrics=metrics, diagnostics=warnings,
        status='invalid' if warnings else 'valid',
        metric_units={'amplitude_peak': signal_unit, 'offset': signal_unit, 'phase_rad': 'rad',
                      'frequency_hz': 'Hz', 'residual_rms': signal_unit, 'residual_std_dof': signal_unit},
        rank=rank, condition_number=condition, coefficients_sin_cos_offset=coefficients)


def sine_fit3(samples, *, frequency_hz, signal_unit, sample_rate_hz=None, time_s=None):
    y = vector(samples, 'samples'); times = time_axis(len(y), sample_rate_hz, time_s)
    frequency = scalar(frequency_hz, 'frequency_hz', positive=True)
    if sample_rate_hz is not None:
        require(frequency < sample_rate_hz / 2, 'invalid_frequency', 'Known frequency must be below Nyquist.')
    return _report('sine_fit3', y, times, frequency, unit(signal_unit))


def sine_fit4(samples, *, frequency_guess_hz, frequency_bounds_hz, signal_unit,
              sample_rate_hz=None, time_s=None):
    try:
        from scipy.optimize import minimize_scalar
    except ImportError as error:
        from .common import AnalysisError
        raise AnalysisError('dependency_missing', 'sine_fit4 requires the optional SciPy dependency.') from error
    np = numpy(); y = vector(samples, 'samples'); times = time_axis(len(y), sample_rate_hz, time_s)
    guess = scalar(frequency_guess_hz, 'frequency_guess_hz', positive=True)
    require(isinstance(frequency_bounds_hz, (list, tuple)) and len(frequency_bounds_hz) == 2,
            'invalid_frequency_bounds', 'Supply explicit [low_hz, high_hz] local fit bounds.')
    low, high = [scalar(v, 'frequency_bounds_hz', positive=True) for v in frequency_bounds_hz]
    duration = float(times[-1] - times[0])
    require(low < guess < high and (high - low) * duration <= 1.0,
            'invalid_frequency_bounds', 'Bounds must bracket the guess and span no more than one inverse-record-duration interval.')
    if sample_rate_hz is not None:
        require(high < sample_rate_hz / 2, 'invalid_frequency_bounds', 'Fit interval must remain below Nyquist.')
    scale = max(float(np.max(np.abs(y))), 1e-300)
    def objective(delta_cycles):
        residual = _solve(y / scale, times, guess + delta_cycles / duration)[1]
        return float(np.mean(residual ** 2))
    fitted = minimize_scalar(objective, bounds=((low - guess) * duration, (high - guess) * duration), method='bounded',
                             options={'xatol': 1e-12, 'maxiter': 200})
    frequency = float(guess + fitted.x / duration)
    margin = min(frequency - low, high - frequency)
    require(bool(fitted.success) and margin > (high - low) * 1e-5,
            'fit_not_converged', 'Frequency fit did not converge in the interior of the explicitly selected interval.')
    return _report('sine_fit4', y, times, frequency, unit(signal_unit), optimized=True,
                   extra={'frequency_guess_hz': guess, 'frequency_bounds_hz': [low, high],
                          'frequency_search': 'bounded_variable_projection_local_interval',
                          'optimizer_success': True, 'objective_evaluations': int(fitted.nfev)})

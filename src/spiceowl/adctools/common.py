"""Finite inputs and JSON-safe scientific results; optional numerics are lazy."""
import importlib.metadata
import math
import platform

SCHEMA = 'spiceowl.adctools.result.v1'
MAX_SAMPLES = 1_048_576


class AnalysisError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, code, message):
    if not condition:
        raise AnalysisError(code, message)


def numpy():
    try:
        import numpy as np
        return np
    except ImportError as error:
        raise AnalysisError('dependency_missing', 'Install the optional spiceowl[adctools] extra.') from error


def scalar(value, name, *, positive=False):
    require(not isinstance(value, (bool, str)) and isinstance(value, (int, float))
            and math.isfinite(value) and abs(value) <= 1e100
            and (not positive or value > 0), 'invalid_parameter', name + ' must be a finite numeric value in its declared unit.')
    return float(value)


def unit(value):
    require(isinstance(value, str) and 1 <= len(value) <= 32 and all(32 <= ord(c) < 127 for c in value),
            'invalid_unit', 'Provide an explicit short ASCII signal unit, for example V or code.')
    return value


def vector(value, name, *, minimum=8):
    np = numpy()
    try:
        result = np.asarray(value)
        valid = result.ndim == 1 and result.dtype.kind in 'iuf' and minimum <= len(result) <= MAX_SAMPLES
        require(valid, 'invalid_samples', name + ' must be a bounded one-dimensional real array.')
        result = result.astype(float)
        require(bool(np.isfinite(result).all()) and bool((np.abs(result) <= 1e100).all()),
                'nonfinite_samples', name + ' contains nonfinite or unsupported-magnitude samples.')
        return result
    except AnalysisError:
        raise
    except (TypeError, ValueError, OverflowError) as error:
        raise AnalysisError('invalid_samples', name + ' must contain real numeric samples.') from error


def time_axis(count, sample_rate_hz=None, time_s=None):
    np = numpy()
    require(sample_rate_hz is not None or time_s is not None, 'sampling_missing', 'Provide sample_rate_hz, time_s, or both consistently.')
    if time_s is None:
        rate = scalar(sample_rate_hz, 'sample_rate_hz', positive=True)
        return np.arange(count, dtype=float) / rate
    times = vector(time_s, 'time_s', minimum=2)
    require(len(times) == count and bool((np.diff(times) > 0).all()), 'invalid_time', 'time_s must align with samples and strictly increase.')
    if sample_rate_hz is not None:
        rate = scalar(sample_rate_hz, 'sample_rate_hz', positive=True)
        require(bool(np.allclose(np.diff(times), 1 / rate, rtol=1e-6, atol=1e-12 / rate)),
                'nonuniform_sampling', 'time_s must match the explicitly supplied uniform sampling rate.')
    return times


def clean(value):
    """No NaN/Infinity tokens: undefined metrics are null with separate status."""
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if hasattr(value, 'tolist'):
        return clean(value.tolist())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def result(operation, *, parameters, metrics, status='valid', diagnostics=(),
           metric_units=None, backend='spiceowl', backend_version='1.0.0', **extra):
    units = metric_units or {}
    typed = {}
    for name, value in metrics.items():
        value = clean(value)
        typed[name] = {'value': value, 'unit': units.get(name, 'dB' if name.endswith('_db') else
            'dBFS' if name.endswith('_dbfs') else 'bit' if name == 'enob_bits' else
            'dBFS/Hz' if name == 'noise_floor_dbfs_per_hz' else '1'),
            'status': 'valid' if value is not None else 'undefined'}
    environment = {'python': platform.python_version()}
    for package in ('numpy', 'scipy', 'adctoolbox'):
        try:
            environment[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            environment[package] = None
    return clean({'schema': SCHEMA, 'operation': operation, 'status': status,
                  'algorithm_version': '1.0.0', 'backend': backend, 'backend_version': backend_version,
                  'numerical_environment': environment,
                  'parameters': parameters, 'metrics': typed,
                  'warnings': list(diagnostics), 'specification_result': 'not_evaluated', **extra})


def ratio_db(numerator, denominator):
    if numerator <= 0 or denominator <= 0:
        return None
    return 10 * (math.log10(numerator) - math.log10(denominator))

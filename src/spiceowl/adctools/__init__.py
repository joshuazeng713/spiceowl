"""Offline ADC measurements. Every dispatched operation requires an explicit backend."""
from .common import AnalysisError, SCHEMA

OPERATIONS = ('spec_analyze', 'coherent_fft', 'sine_fit3', 'sine_fit4',
              'weight_calibrate', 'reconstruct_codes', 'histogram_linearity', 'tran_metrics',
              'ac_metrics', 'dc_op_metrics')


def _backend(value):
    from .common import require
    require(value in ('spiceowl', 'adctoolbox'), 'unsupported_backend', 'Choose backend=spiceowl or backend=adctoolbox explicitly; no fallback is performed.')


def spec_analyze(samples, *, backend, **kwargs):
    _backend(backend)
    from . import spectrum, upstream
    return (spectrum if backend == 'spiceowl' else upstream).spec_analyze(samples, **kwargs)


def coherent_fft(samples, *, backend, sample_rate_hz, fundamental_hz, **kwargs):
    _backend(backend)
    if backend == 'spiceowl':
        from .spectrum import coherent_fft as run
        return run(samples, sample_rate_hz=sample_rate_hz, fundamental_hz=fundamental_hz, **kwargs)
    from .common import vector, scalar, require
    count = len(vector(samples, 'samples', minimum=32)); rate = scalar(sample_rate_hz, 'sample_rate_hz', positive=True)
    frequency = scalar(fundamental_hz, 'fundamental_hz', positive=True); cycles = count * frequency / rate
    require(abs(cycles - round(cycles)) <= 1e-6 and 'window' not in kwargs, 'noncoherent_record', 'coherent_fft fixes a rectangular window and requires integer record cycles.')
    data = spec_analyze(samples, backend=backend, sample_rate_hz=rate, fundamental_hz=frequency, window='rectangular', **kwargs)
    data['operation'] = 'coherent_fft'
    return data


def sine_fit3(samples, *, backend, **kwargs):
    _backend(backend)
    from . import fitting, upstream
    return fitting.sine_fit3(samples, **kwargs) if backend == 'spiceowl' else upstream.sine_fit(samples, operation='sine_fit3', **kwargs)


def sine_fit4(samples, *, backend, **kwargs):
    _backend(backend)
    from . import fitting, upstream
    return fitting.sine_fit4(samples, **kwargs) if backend == 'spiceowl' else upstream.sine_fit(samples, operation='sine_fit4', **kwargs)


def weight_calibrate(decisions, reference=None, *, backend, **kwargs):
    _backend(backend)
    from . import calibration, upstream
    return (calibration if backend == 'spiceowl' else upstream).weight_calibrate(decisions, reference, **kwargs)


def _local_only(name, backend, *args, **kwargs):
    from .common import require
    require(backend == 'spiceowl', 'unsupported_backend', name + ' is the explicit SpiceOwl implementation; no upstream substitute is used.')
    from . import calibration, histogram, transient, acdc
    modules = {'reconstruct_codes': calibration, 'histogram_linearity': histogram, 'tran_metrics': transient,
               'ac_metrics': acdc, 'dc_op_metrics': acdc}
    return getattr(modules[name], name)(*args, **kwargs)


def reconstruct_codes(decisions, weights, *, backend, **kwargs):
    return _local_only('reconstruct_codes', backend, decisions, weights, **kwargs)


def histogram_linearity(codes, *, backend, **kwargs):
    return _local_only('histogram_linearity', backend, codes, **kwargs)


def tran_metrics(time_s, values, *, backend, **kwargs):
    return _local_only('tran_metrics', backend, time_s, values, **kwargs)


def ac_metrics(frequency_hz, response_real, response_imag, *, backend, **kwargs):
    return _local_only('ac_metrics', backend, frequency_hz, response_real, response_imag, **kwargs)


def dc_op_metrics(devices, *, backend, **kwargs):
    return _local_only('dc_op_metrics', backend, devices, **kwargs)


def analyze(operation, arguments):
    from .common import require
    require(operation in OPERATIONS and isinstance(arguments, dict), 'invalid_operation', 'Use a named adctools operation with explicit JSON arguments.')
    try: return globals()[operation](**arguments)
    except TypeError as error:
        raise AnalysisError('invalid_arguments', str(error)) from error


__all__ = ['AnalysisError', 'SCHEMA', 'OPERATIONS', 'analyze', *OPERATIONS]

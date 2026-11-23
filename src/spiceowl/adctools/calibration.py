"""Supervised full-rank bit/stage-weight calibration, with explicit reference units."""
import math

from .common import numpy, vector, scalar, unit, require, result, MAX_SAMPLES


def decisions_array(value):
    np = numpy()
    try: array = np.asarray(value)
    except (ValueError, TypeError):
        require(False, 'invalid_decisions', 'Decisions must be a rectangular sample-by-stage array.')
    require(array.ndim == 2 and array.dtype.kind in 'iuf' and 2 <= array.shape[0] <= MAX_SAMPLES
            and 1 <= array.shape[1] <= 64, 'invalid_decisions', 'Use a bounded sample-by-stage decision array with at most 64 columns.')
    require(bool(np.isfinite(array).all()) and bool(np.isin(array, [-1, 0, 1]).all()),
            'invalid_decisions', 'Stage decisions must be explicitly encoded as -1, 0, or 1.')
    return array.astype(float)


def weight_calibrate(decisions, reference, *, reference_unit, fit_offset=True, condition_limit=1e10,
                     validation_decisions=None, validation_reference=None):
    np = numpy(); bits = decisions_array(decisions); values = vector(reference, 'reference', minimum=2)
    require(len(bits) == len(values), 'sample_mismatch', 'Reference must align with each decision row.')
    require(type(fit_offset) is bool, 'invalid_parameter', 'fit_offset must be boolean.')
    reference_unit = unit(reference_unit); limit = scalar(condition_limit, 'condition_limit', positive=True)
    require(limit >= 1, 'invalid_parameter', 'condition_limit must be at least one.')
    design = np.column_stack((bits, np.ones(len(bits)))) if fit_offset else bits
    weights, _, rank, singular = np.linalg.lstsq(design, values, rcond=None)
    condition = float(singular[0] / singular[-1]) if singular[-1] > 0 else None
    parameters = {'sample_count': len(bits), 'stage_count': bits.shape[1], 'reference_unit': reference_unit,
        'method': 'supervised_linear_least_squares', 'fit_offset': fit_offset, 'condition_limit': limit,
        'decision_encoding': [-1, 0, 1], 'gain_reference': 'supplied_reference', 'automatic_radix_assumption': False}
    if rank < design.shape[1] or condition is None or condition > limit or len(bits) <= design.shape[1]:
        return result('weight_calibrate', parameters=parameters, metrics={}, status='invalid',
                      diagnostics=['calibration_not_identifiable'], rank=int(rank), condition_number=condition,
                      weights=None, offset=None, identifiable=False)
    residual = values - design @ weights
    metrics = {'training_rmse': math.sqrt(float(np.mean(residual ** 2))), 'validation_rmse': None}
    require((validation_decisions is None) == (validation_reference is None), 'validation_pair_missing',
            'Provide both held-out decisions and reference, or neither.')
    if validation_decisions is not None:
        check_bits = decisions_array(validation_decisions); check_values = vector(validation_reference, 'validation_reference', minimum=2)
        require(check_bits.shape[1] == bits.shape[1] and len(check_bits) == len(check_values),
                'sample_mismatch', 'Validation stages and reference lengths must match.')
        prediction = check_bits @ (weights[:-1] if fit_offset else weights) + (weights[-1] if fit_offset else 0)
        metrics['validation_rmse'] = math.sqrt(float(np.mean((prediction - check_values) ** 2)))
    return result('weight_calibrate', parameters=parameters, metrics=metrics,
        metric_units={'training_rmse': reference_unit, 'validation_rmse': reference_unit},
        diagnostics=[] if validation_decisions is not None else ['training_residual_is_not_independent_validation'],
        rank=int(rank), condition_number=condition, weights=weights[:-1] if fit_offset else weights,
        offset=float(weights[-1]) if fit_offset else 0.0, weight_unit=reference_unit, identifiable=True)


def reconstruct_codes(decisions, weights, *, reference_unit, offset=0):
    bits = decisions_array(decisions); values = vector(weights, 'weights', minimum=1)
    require(bits.shape[1] == len(values), 'stage_mismatch', 'One weight is required for each decision column.')
    baseline = scalar(offset, 'offset'); reference_unit = unit(reference_unit)
    return result('reconstruct_codes', parameters={'reference_unit': reference_unit, 'sample_count': len(bits),
        'stage_count': bits.shape[1], 'offset': baseline}, metrics={}, reconstructed=bits @ values + baseline,
        output_unit=reference_unit)

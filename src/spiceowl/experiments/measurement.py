"""Pure measurements of a supplied PSF ASCII artifact under a frozen recipe."""
from bisect import bisect_left, bisect_right
import hashlib
import math

from spiceowl.integrations.spectre_results import PsfError, parse_psf_ascii
from .recipes import validate_recipe
from .source import ExperimentIssue, require

MAX_REPORTED_EDGES = 256


def _crossings(times, values, level):
    """Linear crossings; threshold plateaus/touches do not create fake edges."""
    result, previous, plateau = [], None, None
    for t, value in zip(times, values):
        sign = 1 if value > level else -1 if value < level else 0
        if sign == 0:
            if previous is not None and plateau is None:
                plateau = t
            continue
        if previous is not None and sign != previous[2]:
            when = plateau if plateau is not None else previous[0] + (level-previous[1])/(value-previous[1])*(t-previous[0])
            result.append((when, 'rising' if sign > 0 else 'falling'))
        previous, plateau = (t, value, sign), None
    return result


def _delays(times, incoming, outgoing, level):
    inputs, outputs = _crossings(times, incoming, level), _crossings(times, outgoing, level)
    cursor, records, pairs, ambiguous = 0, [], 0, 0
    summary = {name: {'count': 0, 'min_s': None, 'max_s': None, 'mean_s': None, '_sum': 0.0} for name in ('tPHL', 'tPLH')}
    for index, (when, direction) in enumerate(inputs):
        end = inputs[index+1][0] if index+1 < len(inputs) else times[-1]
        while cursor < len(outputs) and outputs[cursor][0] < when:
            cursor += 1
        candidates = []
        while cursor < len(outputs) and outputs[cursor][0] < end:
            if len(candidates) < 2:
                candidates.append(outputs[cursor])
            cursor += 1
        if len(candidates) != 1 or candidates[0][1] == direction:
            ambiguous += 1
            continue
        out_when = candidates[0][0]
        delay = out_when-when
        kind = 'tPHL' if direction == 'rising' else 'tPLH'
        metric = summary[kind]
        metric['count'] += 1
        metric['_sum'] += delay
        metric['min_s'] = delay if metric['min_s'] is None else min(metric['min_s'], delay)
        metric['max_s'] = delay if metric['max_s'] is None else max(metric['max_s'], delay)
        pairs += 1
        if len(records) < MAX_REPORTED_EDGES:
            records.append({'kind': kind, 'input_crossing_s': when, 'output_crossing_s': out_when, 'delay_s': delay})
    for metric in summary.values():
        total = metric.pop('_sum')
        metric['mean_s'] = total/metric['count'] if metric['count'] else None
    return {'records': records, 'summary': summary, 'input_transition_count': len(inputs),
            'output_transition_count': len(outputs), 'paired_count': pairs,
            'ambiguous_or_missing_pair_count': ambiguous, 'records_truncated': pairs > len(records),
            'crossing_v': level, 'acceptance_effect': 'none; report_only'}


def measure_psf_ascii(text, recipe, *, execution_state='unknown', exit_code=None):
    """Return independent data/specification dimensions; never qualify a PDK.

    Execution evidence is caller supplied. The owner must separately verify the
    run/source/recipe/native-archive binding and external dependency evidence.
    """
    result = {'schema': 'spiceowl.experiment-measurement.v1',
              'recipe_sha256': None,
              'psf_sha256': None,
              'execution': {'state': 'unknown', 'exit_code': None, 'evidence_source': 'caller'},
              'data_validity': 'unknown', 'specification': 'unknown', 'sample_count': 0,
              'time_span_s': None, 'voltage_ranges_v': {}, 'checks': [], 'windows': [],
              'delays': None, 'missing': [], 'overall_qualified_acceptance': None,
              'qualification_scope': 'waveform under frozen recipe only; run, model, SOA and artifact identity require owner validation'}
    def check(name, dimension, passed, **details):
        row = {'name': name, 'dimension': dimension, 'status': 'pass' if passed else 'fail', **details}
        result['checks'].append(row)
        return bool(passed)
    try:
        require(execution_state in ('queued', 'running', 'completed', 'failed', 'cancelled', 'unknown'),
                'execution_state', 'invalid_execution_state', 'Use an explicit bounded execution state.')
        require(exit_code is None or type(exit_code) is int and -255 <= exit_code <= 255,
                'exit_code', 'invalid_exit_code', 'Exit code must be a bounded integer or null.')
        result['execution'].update(state=execution_state, exit_code=exit_code)
        spec = validate_recipe(recipe)
        result['recipe_sha256'] = recipe['recipe_sha256']
        if spec.get('recipe') == 'agent-testbench':
            result['missing'] = [{'field': 'postprocess', 'code': 'agent_testbench_measurement_not_implemented',
                'message': 'Authored testbench objectives require their explicit postprocessor; inverter measurements do not apply.'}]
            return result
    except ExperimentIssue as issue:
        result['missing'] = [issue.record()]
        return result
    except (ValueError, KeyError, TypeError, OverflowError, RecursionError):
        result['missing'] = [{'field': 'recipe', 'code': 'invalid_recipe', 'message': 'Supply the original complete frozen recipe.'}]
        return result
    try:
        if isinstance(text, str) and len(text) <= 16*1024*1024:
            result['psf_sha256'] = hashlib.sha256(text.encode('utf-8')).hexdigest()
        waveform = parse_psf_ascii(text)
    except (PsfError, UnicodeError) as issue:
        code = issue.code if isinstance(issue, PsfError) else 'psf_encoding'
        missing = code in ('psf_missing', 'psf_empty')
        result['data_validity'] = 'unknown' if missing else 'invalid'
        result['missing'] = [{'field': 'psf', 'code': code, 'message': 'PSF data is absent or outside the supported scalar time format.'}]
        result['checks'].append({'name': 'parse', 'dimension': 'data_validity', 'status': 'unknown' if missing else 'fail'})
        return result
    values, units = waveform['values'], waveform['units']
    times, acc, roles = values['time'], spec['acceptance'], spec['roles']
    result['sample_count'] = len(times)
    result['time_span_s'] = [times[0], times[-1]] if times and units.get('time') == 's' else None
    check('parse', 'data_validity', True)
    check('finite', 'data_validity', all(all(math.isfinite(x) for x in series) for series in values.values()))
    check('aligned', 'data_validity', all(len(series) == len(times) for series in values.values()))
    check('time_monotonic', 'data_validity', all(b > a for a,b in zip(times,times[1:])))
    check('minimum_samples', 'data_validity', len(times) >= 2)
    count = acc['sample_count']
    check('sample_count', 'data_validity', count is None or len(times) == count, expected=count, actual=len(times))
    required = acc['required_units']
    present = check('required_signals', 'data_validity', all(name in values for name in required))
    correct_units = check('required_units', 'data_validity', all(units.get(name) == unit for name,unit in required.items()))
    coverage = acc['coverage']
    tolerance = coverage['tolerance_s']
    check('coverage', 'data_validity', bool(times) and abs(times[0]-coverage['start_s']) <= tolerance
          and abs(times[-1]-coverage['end_s']) <= tolerance, expected=[coverage['start_s'], coverage['end_s']], tolerance_s=tolerance)
    largest_gap = max((b-a for a,b in zip(times,times[1:])), default=None)
    check('sample_gap', 'data_validity', largest_gap is not None and largest_gap <= acc['max_sample_gap_s'],
          maximum_observed_s=largest_gap, maximum_allowed_s=acc['max_sample_gap_s'])
    if not present or not correct_units or len(times) < 2:
        result['data_validity'] = 'invalid'
        return result
    port_names = [n for n in required if n != 'time']
    result['voltage_ranges_v'] = {name: [min(values[name]), max(values[name])] for name in port_names}
    pos, neg = spec['supply']['positive_v'], spec['supply']['negative_v']
    rail_error = max(max(abs(x-pos) for x in values[roles['vdd']]), max(abs(x-neg) for x in values[roles['vss']]))
    check('supply_rails', 'data_validity', rail_error <= acc['supply_tolerance_v'], maximum_error_v=rail_error,
          tolerance_v=acc['supply_tolerance_v'])
    lower, upper = acc['voltage_bounds_v']
    # Out-of-bound measured voltages remain real data; they fail the declared
    # performance constraint, rather than invalidating a measured design failure.
    bounds_pass = check('voltage_bounds', 'specification', all(lower <= x <= upper for n in port_names for x in values[n]),
                        minimum_v=lower, maximum_v=upper)
    def select(window):
        a = bisect_left(times, window['start_s']-tolerance)
        b = bisect_right(times, window['end_s']+tolerance)
        covered = a < b and times[a] <= window['start_s']+acc['max_sample_gap_s']+tolerance and times[b-1] >= window['end_s']-acc['max_sample_gap_s']-tolerance
        sampled = covered and b-a >= window['minimum_samples']
        check('window_samples_' + str(len(result['windows'])), 'data_validity', sampled,
              start_s=window['start_s'], end_s=window['end_s'], minimum_samples=window['minimum_samples'], actual_samples=b-a)
        return a,b,sampled
    logic = acc.get('logic')
    if logic is not None:
        incoming, outgoing = values[roles['inputs'][0]], values[roles['outputs'][0]]
        for window in logic['windows']:
            a,b,sampled = select(window)
            row = {'kind': window['kind'], 'start_s': window['start_s'], 'end_s': window['end_s'], 'sample_count': b-a,
                   'input_range_v': [min(incoming[a:b]),max(incoming[a:b])] if a < b else None,
                   'output_range_v': [min(outgoing[a:b]),max(outgoing[a:b])] if a < b else None,
                   'output_threshold_passed': None}
            if sampled:
                high = window['kind'] == 'input_high_output_low'
                stimulus_pass = min(incoming[a:b]) >= logic['high_min_v'] if high else max(incoming[a:b]) <= logic['low_max_v']
                output_pass = max(outgoing[a:b]) <= logic['low_max_v'] if high else min(outgoing[a:b]) >= logic['high_min_v']
                check('input_logic_' + str(len(result['windows'])), 'data_validity', stimulus_pass)
                check('output_logic_' + str(len(result['windows'])), 'specification', output_pass)
                row['output_threshold_passed'] = output_pass
            result['windows'].append(row)
    for window in acc.get('windows', []):
        a,b,sampled = select(window)
        series = values[window['signal']][a:b]
        passed = bool(sampled and all(window['min_v'] <= value <= window['max_v'] for value in series))
        row = {'signal': window['signal'], 'start_s': window['start_s'], 'end_s': window['end_s'],
               'sample_count': b-a, 'voltage_range_v': [min(series), max(series)] if series else None,
               'threshold_passed': passed if sampled else None}
        if sampled:
            check('window_voltage_' + str(len(result['windows'])), 'specification', passed)
        result['windows'].append(row)
    valid = all(row['status'] == 'pass' for row in result['checks'] if row['dimension'] == 'data_validity')
    result['data_validity'] = 'valid' if valid else 'invalid'
    if valid:
        if logic is not None:
            result['delays'] = _delays(times,incoming,outgoing,logic['crossing_v'])
        specification = [row for row in result['checks'] if row['dimension'] == 'specification']
        result['specification'] = ('fail' if any(row['status'] == 'fail' for row in specification)
                                   else 'pass' if result['windows'] else 'unknown')
    return result

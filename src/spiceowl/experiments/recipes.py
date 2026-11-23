"""Deterministic recipe preparation. Inputs are supplied bytes and registry data."""
import ast
import copy
import hashlib
import json
import math
import re

from .source import ExperimentIssue, ID, NUMBER, PRIMITIVES, extract_circuit, identifier, parse_sources, require, sha

SPEC_SCHEMA = 'spiceowl.experiment-spec.v1'
PROFILE_SCHEMA = 'spiceowl.experiment-model-profile.v1'
RECIPE_SCHEMA = 'spiceowl.experiment-recipe.v1'
RECIPES = {'inverter-tran', 'prepared-transient', 'agent-testbench'}
SOLVER = {'reltol': 1e-4, 'vabstol_v': 1e-7, 'iabstol_a': 1e-13,
          'gmin_s': 1e-12, 'method': 'gear2only', 'errpreset': 'conservative'}


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False, ensure_ascii=True).encode('utf-8')
    except (ValueError, TypeError, RecursionError):
        raise ExperimentIssue('spec', 'invalid_json', 'Recipe data must be finite bounded JSON.') from None


def fields(value, required, field, optional=()):
    require(isinstance(value, dict), field, 'missing_field', 'Supply an explicit object for ' + field + '.')
    missing = sorted(set(required) - set(value))
    require(not missing, field + ('.' + missing[0] if missing else ''), 'missing_field', 'An explicit recipe field is required.')
    require(set(value) <= set(required) | set(optional), field, 'unknown_field', 'Unsupported fields cannot be forwarded to the simulator.')


def number(value, field, *, lower=-1e100, upper=1e100, positive=False):
    require(type(value) in (int, float) and math.isfinite(value) and lower <= value <= upper
            and (not positive or value > 0), field, 'invalid_si_value', 'Supply a finite numeric value in the declared SI unit.')
    return float(value)


def integer(value, field, lower, upper):
    require(type(value) is int and lower <= value <= upper, field, 'invalid_count', 'Supply a bounded integer sample count.')
    return value


def _names(value, field, lower=1, upper=64):
    require(isinstance(value, list) and lower <= len(value) <= upper, field, 'missing_field', 'Supply an explicit bounded list of signal names.')
    for item in value:
        identifier(item, field)
    require(len(set(value)) == len(value), field, 'duplicate_identifier', 'Signal identifiers must be distinct.')
    return value


def validate_model_profile(profile):
    fields(profile, {'schema', 'id', 'models', 'corners'}, 'model_profile')
    require(profile['schema'] == PROFILE_SCHEMA, 'model_profile.schema', 'unsupported_schema', 'Unsupported experiment model profile.')
    require(isinstance(profile['id'], str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', profile['id']),
            'model_profile.id', 'invalid_identifier', 'Use an explicit registered model profile ID.')
    models, corners = profile['models'], profile['corners']
    require(isinstance(models, dict) and len(models) <= 256, 'model_profile.models', 'missing_field', 'Supply explicit registered model contracts.')
    for name, contract in models.items():
        identifier(name, 'model_profile.models')
        require(name not in PRIMITIVES, 'model_profile.models', 'model_primitive_conflict', 'A registered model cannot redefine a supported primitive.')
        fields(contract, {'terminals'}, 'model_profile.models.' + name, {'polarity', 'kind'})
        kind = contract.get('kind', 'mos')
        require(kind in ('mos', 'external'), 'model_profile.models', 'unsupported_model_kind', 'Model kind must be explicitly supported.')
        _names(contract['terminals'], 'model_profile.models.terminals', upper=128)
        if kind == 'mos':
            require(contract.get('polarity') in ('n', 'p') and len(contract['terminals']) == 4
                    and set(contract['terminals']) == {'d', 'g', 's', 'b'}, 'model_profile.models.' + name,
                    'model_contract_missing', 'MOS polarity and ordered d/g/s/b terminals must come from the registered profile.')
        else:
            require('polarity' not in contract, 'model_profile.models.' + name, 'invalid_model_contract', 'An external behavioral model cannot claim MOS polarity.')
    require(isinstance(corners, dict) and 1 <= len(corners) <= 64, 'model_profile.corners', 'missing_field', 'Supply an explicit registered corner-to-alias mapping.')
    for corner, includes in corners.items():
        identifier(corner, 'model_profile.corners')
        require(isinstance(includes, list) and len(includes) <= 64, 'model_profile.corners', 'invalid_includes', 'Use a bounded explicit include-alias list.')
        seen = set()
        for item in includes:
            fields(item, {'alias', 'section'}, 'model_profile.corners.includes')
            require(isinstance(item['alias'], str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', item['alias']),
                    'model_profile.corners.alias', 'invalid_alias', 'Use a registered external alias, never a path or include command.')
            if item['section'] is not None:
                identifier(item['section'], 'model_profile.corners.section')
            key = (item['alias'], item['section'])
            require(key not in seen, 'model_profile.corners', 'duplicate_include', 'An alias/section pair cannot repeat.')
            seen.add(key)
    require(len(canonical(profile)) <= 128 * 1024, 'model_profile', 'profile_limit', 'Model profile exceeds its size bound.')
    return copy.deepcopy(profile)


def _window(window, field, coverage, stop, sample_period):
    a = number(window['start_s'], field + '.start_s', lower=0, upper=stop)
    b = number(window['end_s'], field + '.end_s', lower=0, upper=stop)
    require(coverage['start_s'] <= a < b <= coverage['end_s'], field, 'invalid_window', 'Measurement windows must be nonempty and lie within frozen coverage.')
    count = integer(window['minimum_samples'], field + '.minimum_samples', 2, 200000)
    if sample_period is not None:
        require(count <= math.floor((b-a)/sample_period + 1e-8) + 2, field, 'impossible_window', 'The requested window sample count exceeds the configured sampling grid.')


def _stable_pulse_window(pulse, window):
    start, end, delay, period = window['start_s'], window['end_s'], pulse['delay_s'], pulse['period_s']
    if end <= delay:
        return window['kind'] == 'input_low_output_high'
    if start < delay:
        return False
    cycle = math.floor((start-delay)/period)
    a, b = start-delay-cycle*period, end-delay-cycle*period
    rise, high_end = pulse['rise_s'], pulse['rise_s'] + pulse['width_s']
    low_start = high_end + pulse['fall_s']
    if window['kind'] == 'input_high_output_low':
        return rise <= a < b <= high_end
    return low_start <= a < b <= period


def validate_spec(spec, profile, ports=None):
    if isinstance(spec, dict) and spec.get('recipe') == 'agent-testbench':
        from .agent_testbench import validate_agent_spec
        return validate_agent_spec(spec, profile)[0]
    required = {'schema', 'recipe', 'roles', 'supply', 'temperature_k', 'nominal_temperature_k',
                'corner', 'stimuli', 'loads', 'analysis', 'parameters', 'acceptance'}
    fields(spec, required, 'spec')
    require(spec['schema'] == SPEC_SCHEMA and spec['recipe'] in RECIPES, 'spec.recipe', 'unsupported_recipe', 'Select inverter-tran or prepared-transient explicitly.')
    require(len(canonical(spec)) <= 128 * 1024, 'spec', 'spec_limit', 'Experiment specification exceeds its size bound.')
    s = copy.deepcopy(spec)
    fields(s['roles'], {'inputs', 'outputs', 'vdd', 'vss'}, 'spec.roles')
    roles = s['roles']
    _names(roles['inputs'], 'spec.roles.inputs')
    _names(roles['outputs'], 'spec.roles.outputs')
    identifier(roles['vdd'], 'spec.roles.vdd')
    identifier(roles['vss'], 'spec.roles.vss')
    names = roles['inputs'] + roles['outputs'] + [roles['vdd'], roles['vss']]
    require(len(names) == len(set(names)) and len(names) <= 64, 'spec.roles', 'conflicting_roles', 'Each selected port must have exactly one explicit role.')
    if ports is not None:
        require(set(names) == set(ports), 'spec.roles', 'port_roles_missing', 'Assign all and only the selected circuit ports explicit roles.')
    fields(s['supply'], {'positive_v', 'negative_v'}, 'spec.supply')
    pos = number(s['supply']['positive_v'], 'spec.supply.positive_v', lower=-1000, upper=1000)
    neg = number(s['supply']['negative_v'], 'spec.supply.negative_v', lower=-1000, upper=1000)
    require(pos > neg, 'spec.supply', 'invalid_supply', 'The positive rail must exceed the negative rail.')
    number(s['temperature_k'], 'spec.temperature_k', lower=1, upper=2000)
    number(s['nominal_temperature_k'], 'spec.nominal_temperature_k', lower=1, upper=2000)
    identifier(s['corner'], 'spec.corner')
    require(s['corner'] in profile['corners'], 'spec.corner', 'corner_mapping_missing', 'The selected corner needs an explicit registered include mapping.')
    require(isinstance(s['parameters'], dict) and len(s['parameters']) <= 128, 'spec.parameters', 'missing_field', 'Supply explicit numeric global parameters or an empty object.')
    for name, value in s['parameters'].items():
        identifier(name, 'spec.parameters')
        number(value, 'spec.parameters.' + name)
    require(isinstance(s['stimuli'], list) and len(s['stimuli']) == len(roles['inputs']), 'spec.stimuli', 'stimulus_missing', 'Each input needs exactly one explicit stimulus.')
    driven = []
    for stimulus in s['stimuli']:
        require(isinstance(stimulus, dict), 'spec.stimuli', 'missing_field', 'Supply a supported stimulus object.')
        kind = stimulus.get('type')
        required = {'port', 'type'} | ({'value_v'} if kind == 'dc' else {'low_v', 'high_v', 'delay_s', 'rise_s', 'fall_s', 'width_s', 'period_s'})
        fields(stimulus, required, 'spec.stimuli')
        require(kind in ('dc', 'pulse'), 'spec.stimuli.type', 'unsupported_stimulus', 'Only explicit DC and pulse voltage stimuli are supported.')
        identifier(stimulus['port'], 'spec.stimuli.port')
        driven.append(stimulus['port'])
        if kind == 'dc':
            number(stimulus['value_v'], 'spec.stimuli.value_v', lower=neg, upper=pos)
        else:
            low = number(stimulus['low_v'], 'spec.stimuli.low_v', lower=neg, upper=pos)
            high = number(stimulus['high_v'], 'spec.stimuli.high_v', lower=neg, upper=pos)
            require(low < high, 'spec.stimuli', 'invalid_stimulus', 'Pulse high voltage must exceed low voltage.')
            number(stimulus['delay_s'], 'spec.stimuli.delay_s', lower=0, upper=1e6)
            for key in ('rise_s', 'fall_s', 'width_s', 'period_s'):
                number(stimulus[key], 'spec.stimuli.' + key, upper=1e6, positive=True)
            require(stimulus['rise_s'] + stimulus['width_s'] + stimulus['fall_s'] < stimulus['period_s'],
                    'spec.stimuli', 'invalid_stimulus', 'A pulse must leave a nonempty low plateau within each period.')
    require(set(driven) == set(roles['inputs']) and len(set(driven)) == len(driven), 'spec.stimuli', 'stimulus_missing', 'Stimuli must drive exactly the declared input ports.')
    require(isinstance(s['loads'], list) and len(s['loads']) <= 64, 'spec.loads', 'missing_field', 'Supply explicit capacitive loads, or an explicit empty list.')
    loaded = set()
    for load in s['loads']:
        fields(load, {'port', 'capacitance_f'}, 'spec.loads')
        require(load['port'] in roles['outputs'] and load['port'] not in loaded, 'spec.loads.port', 'invalid_load', 'Each load must select a distinct declared output.')
        loaded.add(load['port'])
        number(load['capacitance_f'], 'spec.loads.capacitance_f', upper=1, positive=True)
    fields(s['analysis'], {'stop_s', 'max_step_s', 'strobe_period_s'}, 'spec.analysis')
    analysis = s['analysis']
    stop = number(analysis['stop_s'], 'spec.analysis.stop_s', upper=1e6, positive=True)
    number(analysis['max_step_s'], 'spec.analysis.max_step_s', upper=stop, positive=True)
    strobe = analysis['strobe_period_s']
    if strobe is not None:
        number(strobe, 'spec.analysis.strobe_period_s', upper=stop, positive=True)
        require(stop/strobe <= 199999 + 1e-8, 'spec.analysis.strobe_period_s', 'sample_limit', 'The frozen sampling grid exceeds the parser sample limit.')
    acc = s['acceptance']
    fields(acc, {'required_units', 'sample_count', 'coverage', 'max_sample_gap_s', 'voltage_bounds_v', 'supply_tolerance_v'}, 'spec.acceptance', {'logic', 'windows'})
    require(isinstance(acc['required_units'], dict) and acc['required_units'] == {'time': 's', **{n: 'V' for n in names}},
            'spec.acceptance.required_units', 'required_units_missing', 'Specify seconds for time and volts for every declared port.')
    if acc['sample_count'] is not None:
        integer(acc['sample_count'], 'spec.acceptance.sample_count', 2, 200000)
    fields(acc['coverage'], {'start_s', 'end_s', 'tolerance_s'}, 'spec.acceptance.coverage')
    coverage = acc['coverage']
    start = number(coverage['start_s'], 'spec.acceptance.coverage.start_s', lower=0, upper=stop)
    end = number(coverage['end_s'], 'spec.acceptance.coverage.end_s', lower=0, upper=stop)
    require(start < end, 'spec.acceptance.coverage', 'invalid_coverage', 'Coverage must be a nonempty interval.')
    require(start == 0 and end == stop, 'spec.acceptance.coverage', 'transient_coverage',
            'Current recipes save the full transient; coverage must run from zero to stop, with measurement windows selecting subintervals.')
    number(coverage['tolerance_s'], 'spec.acceptance.coverage.tolerance_s', lower=0, upper=(end-start)*1e-3)
    number(acc['max_sample_gap_s'], 'spec.acceptance.max_sample_gap_s', upper=end-start, positive=True)
    require(strobe is None or acc['max_sample_gap_s'] >= strobe, 'spec.acceptance.max_sample_gap_s',
            'impossible_sampling_gap', 'The allowed sample gap cannot be smaller than the configured strobe interval.')
    bounds = acc['voltage_bounds_v']
    require(isinstance(bounds, list) and len(bounds) == 2, 'spec.acceptance.voltage_bounds_v', 'missing_field', 'Supply both explicit voltage bounds.')
    a, b = number(bounds[0], 'spec.acceptance.voltage_bounds_v'), number(bounds[1], 'spec.acceptance.voltage_bounds_v')
    require(a <= neg < pos <= b and a < b, 'spec.acceptance.voltage_bounds_v', 'invalid_bounds', 'Voltage bounds must include the declared rails.')
    number(acc['supply_tolerance_v'], 'spec.acceptance.supply_tolerance_v', lower=0, upper=(pos-neg)*0.1)
    generic = acc.get('windows', [])
    require(isinstance(generic, list) and len(generic) <= 128, 'spec.acceptance.windows', 'invalid_windows', 'Supply a bounded measurement window list.')
    for window in generic:
        fields(window, {'signal', 'start_s', 'end_s', 'minimum_samples', 'min_v', 'max_v'}, 'spec.acceptance.windows')
        require(window['signal'] in names, 'spec.acceptance.windows.signal', 'unknown_signal', 'Window signals must be saved declared ports.')
        low, high = number(window['min_v'], 'spec.acceptance.windows.min_v'), number(window['max_v'], 'spec.acceptance.windows.max_v')
        require(low <= high, 'spec.acceptance.windows', 'invalid_bounds', 'Window voltage bounds are reversed.')
        _window(window, 'spec.acceptance.windows', coverage, stop, strobe)
    logic = acc.get('logic')
    if logic is not None:
        fields(logic, {'low_max_v', 'high_min_v', 'crossing_v', 'windows'}, 'spec.acceptance.logic')
        low = number(logic['low_max_v'], 'spec.acceptance.logic.low_max_v', lower=neg, upper=pos)
        high = number(logic['high_min_v'], 'spec.acceptance.logic.high_min_v', lower=neg, upper=pos)
        cross = number(logic['crossing_v'], 'spec.acceptance.logic.crossing_v', lower=neg, upper=pos)
        require(low < cross < high, 'spec.acceptance.logic', 'invalid_thresholds', 'Logic thresholds must bracket the explicit crossing voltage.')
        require(isinstance(logic['windows'], list) and 2 <= len(logic['windows']) <= 128,
                'spec.acceptance.logic.windows', 'missing_field', 'Supply high and low stable logic windows before execution.')
        kinds = set()
        for window in logic['windows']:
            fields(window, {'kind', 'start_s', 'end_s', 'minimum_samples'}, 'spec.acceptance.logic.windows')
            require(window['kind'] in ('input_high_output_low', 'input_low_output_high'), 'spec.acceptance.logic.windows', 'invalid_window', 'Unsupported logic window kind.')
            kinds.add(window['kind'])
            _window(window, 'spec.acceptance.logic.windows', coverage, stop, strobe)
        require(kinds == {'input_high_output_low', 'input_low_output_high'}, 'spec.acceptance.logic.windows', 'missing_logic_phase', 'Both high and low input phases need frozen windows.')
    if s['recipe'] == 'inverter-tran':
        require(len(roles['inputs']) == len(roles['outputs']) == 1 and len(names) == 4,
                'spec.roles', 'inverter_roles_missing', 'The inverter recipe needs one input, one output and two supply rails.')
        require(logic is not None, 'spec.acceptance.logic', 'missing_field', 'The inverter recipe requires explicit logic thresholds and windows.')
        require(strobe is not None and acc['sample_count'] is not None, 'spec.analysis', 'sampling_missing', 'The inverter recipe needs an explicit sampling grid and count.')
        require(abs(stop/strobe-round(stop/strobe)) <= 1e-8 and acc['sample_count'] == round(stop/strobe)+1,
                'spec.acceptance.sample_count', 'sampling_mismatch', 'The frozen sample count must match the complete strobe grid.')
        pulse = s['stimuli'][0]
        require(pulse['type'] == 'pulse' and pulse['low_v'] <= logic['low_max_v'] and pulse['high_v'] >= logic['high_min_v'],
                'spec.stimuli', 'inverter_pulse_missing', 'The inverter requires a pulse spanning both declared logic levels.')
        require(len(s['loads']) == 1, 'spec.loads', 'load_missing', 'The inverter recipe needs one explicit output capacitance.')
        require(all(_stable_pulse_window(pulse, w) for w in logic['windows']), 'spec.acceptance.logic.windows',
                'stimulus_window_mismatch', 'Logic windows must lie inside the configured stable input plateaus.')
    elif logic is not None:
        raise ExperimentIssue('spec.acceptance.logic', 'recipe_mismatch', 'Prepared transients use explicit signal windows; inverter topology is not inferred.')
    return s


def _multiplicity_value(value, variables, local_parameters, trail=(), budget=None):
    """Check a bounded arithmetic count without executing or rewriting source."""
    budget = [2048] if budget is None else budget
    require(len(trail) <= 16, 'source.parameters', 'invalid_mos_multiplicity',
            'Multiplicity parameter dependencies exceed the supported bound.')
    scales = {'a': 1e-18, 'f': 1e-15, 'p': 1e-12, 'n': 1e-9, 'u': 1e-6,
              'm': 1e-3, 'k': 1e3, 'K': 1e3, 'meg': 1e6, 'M': 1e6,
              'G': 1e9, 'T': 1e12, 'P': 1e15}
    pieces, position = [], 0
    while position < len(value):
        token = NUMBER.match(value, position)
        name = re.match(r'[A-Za-z_][A-Za-z0-9_]*', value[position:])
        if token:
            suffix = re.search(r'(meg|[afpnumkKMGTP])$', token[0])
            numeric = float(token[0][:suffix.start()] if suffix else token[0])
            numeric *= scales[suffix[0]] if suffix else 1.0
            require(math.isfinite(numeric), 'source.parameters', 'invalid_mos_multiplicity',
                    'Explicit MOS multiplicity must be finite.')
            pieces.append(repr(numeric)); position = token.end()
        elif name:
            pieces.append(name[0]); position += len(name[0])
        else:
            pieces.append(value[position]); position += 1
    try:
        tree = ast.parse(''.join(pieces), mode='eval')
    except (SyntaxError, RecursionError):
        raise ExperimentIssue('source.parameters', 'invalid_mos_multiplicity', 'Unsupported multiplicity expression.') from None
    require(sum(1 for _ in ast.walk(tree)) <= 128, 'source.parameters', 'invalid_mos_multiplicity',
            'Multiplicity expression exceeds the supported arithmetic bound.')

    def resolve(node, depth=0):
        budget[0] -= 1
        require(budget[0] >= 0, 'source.parameters', 'invalid_mos_multiplicity', 'Multiplicity arithmetic exceeds its total work bound.')
        require(depth <= 32, 'source.parameters', 'invalid_mos_multiplicity', 'Multiplicity arithmetic is too deeply nested.')
        if isinstance(node, ast.Expression):
            return resolve(node.body, depth + 1)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            result = node.value
        elif isinstance(node, ast.Name):
            require(node.id not in trail, 'source.parameters', 'invalid_mos_multiplicity', 'Multiplicity parameter dependencies are recursive.')
            if node.id in local_parameters:
                result = _multiplicity_value(local_parameters[node.id], variables, local_parameters, trail + (node.id,), budget)
            else:
                require(node.id in variables, 'source.parameters', 'invalid_mos_multiplicity', 'Multiplicity references an undeclared explicit parameter.')
                result = variables[node.id]
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            result = resolve(node.operand, depth + 1) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp):
            left, right = resolve(node.left, depth + 1), resolve(node.right, depth + 1)
            if isinstance(node.op, ast.Add): result = left + right
            elif isinstance(node.op, ast.Sub): result = left - right
            elif isinstance(node.op, ast.Mult): result = left * right
            elif isinstance(node.op, ast.Div): result = left / right
            elif isinstance(node.op, ast.Pow) and abs(right) <= 32: result = left ** right
            else: raise ValueError('unsupported arithmetic')
        else:
            raise ValueError('unsupported arithmetic')
        require(type(result) in (int, float) and math.isfinite(result) and abs(result) <= 1e100,
                'source.parameters', 'invalid_mos_multiplicity', 'Multiplicity arithmetic must remain finite and bounded.')
        return result

    try:
        return resolve(tree)
    except (ArithmeticError, TypeError, ValueError, RecursionError):
        raise ExperimentIssue('source.parameters', 'invalid_mos_multiplicity', 'Explicit MOS multiplicity cannot be resolved to finite bounded arithmetic.') from None


def _inverter(circuit, spec, profile):
    instances = circuit['instances']
    require(len(instances) == 2 and len(circuit['provenance']['closure']) <= 1, 'selection', 'inverter_topology',
            'An inverter recipe requires exactly two directly selected registered MOS devices.')
    roles = spec['roles']
    expected = {'d': roles['outputs'][0], 'g': roles['inputs'][0]}
    polarities = set()
    local_parameters = parse_sources({'selected.scs': circuit['raw']})['subcircuits'][circuit['subcircuit']]['parameters']
    for item in instances:
        model = profile['models'].get(item['master'])
        require(model is not None and model.get('kind', 'mos') == 'mos', 'model_profile.models', 'inverter_model_contract',
                'Unknown, primitive or behavioral devices cannot be inferred to be MOS.')
        polarity = model['polarity']
        polarities.add(polarity)
        mapped = dict(zip(model['terminals'], item['terminals']))
        rail = roles['vss'] if polarity == 'n' else roles['vdd']
        require(mapped == {**expected, 's': rail, 'b': rail}, 'selection', 'inverter_topology',
                'Registered terminal ordering does not form the explicitly selected CMOS inverter.')
        require({'w', 'l'} <= set(item['parameters']), 'source.parameters', 'mos_dimensions_missing',
                'The selected devices must explicitly preserve w/l parameters.')
        # Omitted nf/multi remain omitted: the selected model owns their meaning.
        # Explicit assignments are checked, while their literal source is kept.
        for name in ('nf', 'multi'):
            if name not in item['parameters']:
                continue
            value = _multiplicity_value(item['parameters'][name], spec['parameters'], local_parameters)
            require(value > 0 and (name != 'nf' or value == math.floor(value)),
                    'source.parameters.' + name, 'invalid_mos_multiplicity',
                    'Explicit nf must be a positive integer; explicit multi must be positive and finite.')
    require(polarities == {'n', 'p'}, 'selection', 'inverter_topology', 'Exactly one registered n-polarity and one p-polarity device are required.')


def _num(value):
    return format(float(value), '.17g')


def prepare_experiment(sources, selection, spec, model_profile):
    """Return ready files/recipe or structured missing evidence; never write files."""
    if isinstance(spec, dict) and spec.get('recipe') == 'agent-testbench':
        from .agent_testbench import prepare_agent_testbench
        return prepare_agent_testbench(sources, selection, spec, model_profile)
    try:
        # Report all missing top-level conditions before doing any extraction.
        required = {'schema', 'recipe', 'roles', 'supply', 'temperature_k', 'nominal_temperature_k',
                    'corner', 'stimuli', 'loads', 'analysis', 'parameters', 'acceptance'}
        if isinstance(spec, dict):
            missing = [{'field': 'spec.' + name, 'code': 'missing_field', 'message': 'An explicit registered recipe condition is required.'}
                       for name in sorted(required - set(spec))]
            if missing:
                return {'status': 'missing', 'missing': missing, 'recipe': None, 'files': {}, 'source': None}
        profile = validate_model_profile(model_profile)
        validated = validate_spec(spec, profile)
        circuit = extract_circuit(sources, selection, profile)
        validated = validate_spec(validated, profile, circuit['ports'])
        missing_parameters = set(circuit['required_parameters']) - set(validated['parameters'])
        if missing_parameters:
            return {'status': 'missing', 'missing': [{'field': 'spec.parameters.' + name, 'code': 'parameter_missing',
                    'message': 'The source references an undeclared global parameter.'} for name in sorted(missing_parameters)],
                    'recipe': None, 'files': {}, 'source': circuit['provenance']}
        if validated['recipe'] == 'inverter-tran':
            _inverter(circuit, validated, profile)
        roles, supply, analysis = validated['roles'], validated['supply'], validated['analysis']
        lines = ['// Deterministic SpiceOwl experiment; original DUT statements are preserved.', 'simulator lang=spectre', 'global 0']
        includes = profile['corners'][validated['corner']]
        for item in includes:
            lines.append('include "@EXTERNAL_' + item['alias'] + '@"' + ('' if item['section'] is None else ' section=' + item['section']))
        if validated['parameters']:
            lines.append('parameters ' + ' '.join(k + '=' + _num(v) for k,v in sorted(validated['parameters'].items())))
        lines.append('include "dut.scs"')
        lines.extend(['V_SWO_POS (' + roles['vdd'] + ' 0) vsource dc=' + _num(supply['positive_v']),
                      'V_SWO_NEG (' + roles['vss'] + ' 0) vsource dc=' + _num(supply['negative_v'])])
        for index, stimulus in enumerate(validated['stimuli']):
            prefix = 'V_SWO_IN' + str(index) + ' (' + stimulus['port'] + ' ' + roles['vss'] + ') vsource '
            if stimulus['type'] == 'dc':
                line = 'dc=' + _num(stimulus['value_v'] - supply['negative_v'])
            else:
                line = 'type=pulse val0=' + _num(stimulus['low_v']-supply['negative_v']) + ' val1=' + _num(stimulus['high_v']-supply['negative_v'])
                for output, key in [('delay','delay_s'),('rise','rise_s'),('fall','fall_s'),('width','width_s'),('period','period_s')]:
                    line += ' ' + output + '=' + _num(stimulus[key])
            lines.append(prefix + line)
        lines.append('X_SWO_DUT (' + ' '.join(circuit['ports']) + ') ' + circuit['subcircuit'])
        for index, load in enumerate(validated['loads']):
            lines.append('C_SWO_LOAD' + str(index) + ' (' + load['port'] + ' ' + roles['vss'] + ') capacitor c=' + _num(load['capacitance_f']))
        lines.append('simulatorOptions options reltol=1e-4 vabstol=1e-7 iabstol=1e-13 temp=' + _num(validated['temperature_k']-273.15)
                     + ' tnom=' + _num(validated['nominal_temperature_k']-273.15) + ' scale=1 scalem=1 gmin=1e-12')
        tran = 'tran tran stop=' + _num(analysis['stop_s']) + ' maxstep=' + _num(analysis['max_step_s'])
        if analysis['strobe_period_s'] is not None:
            tran += ' strobeperiod=' + _num(analysis['strobe_period_s']) + ' strobeoutput=strobeonly'
        lines += [tran + ' errpreset=conservative method=gear2only',
                  'save ' + ' '.join(n for n in validated['acceptance']['required_units'] if n != 'time'),
                  'saveOptions options save=selected']
        files = {'netlist/dut.scs': b'simulator lang=spectre\n' + circuit['raw'],
                 'netlist/input.scs': ('\n'.join(lines) + '\n').encode('utf-8')}
        recipe = {'schema': RECIPE_SCHEMA, 'recipe': validated['recipe'], 'generator_version': 1,
                  'spec': validated, 'selection': copy.deepcopy(selection), 'source': circuit['provenance'],
                  'model_profile': profile, 'model_profile_sha256': sha(canonical(profile)),
                  'used_models': circuit['models'], 'external_includes': includes, 'solver': SOLVER.copy(),
                  'files_sha256': {name: sha(raw) for name,raw in files.items()},
                  'scope': 'isolated_cutout' if selection['mode'] == 'instances' else 'selected_cell_closure',
                  'qualification_scope': 'configured experiment only; no inferred process or full-system qualification',
                  'delay_acceptance': 'report_only'}
        recipe['recipe_sha256'] = sha(canonical(recipe))
        files['recipe.json'] = canonical(recipe) + b'\n'
        return {'status': 'ready', 'missing': [], 'recipe': recipe, 'files': files, 'source': circuit['provenance']}
    except ExperimentIssue as issue:
        return {'status': 'missing', 'missing': [issue.record()], 'recipe': None, 'files': {}, 'source': None}
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return {'status': 'missing', 'missing': [{'field': 'spec', 'code': 'unsupported_input',
                'message': 'The supplied source, selection or specification is outside supported bounds.'}],
                'recipe': None, 'files': {}, 'source': None}


def validate_recipe(recipe):
    require(isinstance(recipe, dict) and recipe.get('schema') == RECIPE_SCHEMA, 'recipe', 'unsupported_schema', 'A frozen experiment recipe is required.')
    claim = recipe.get('recipe_sha256')
    body = {key:value for key,value in recipe.items() if key != 'recipe_sha256'}
    require(isinstance(claim, str) and re.fullmatch(r'[a-f0-9]{64}', claim) and sha(canonical(body)) == claim,
            'recipe.recipe_sha256', 'recipe_digest_mismatch', 'The frozen recipe changed; use the original pre-run recipe.')
    if recipe.get('recipe') == 'agent-testbench':
        from .agent_testbench import validate_agent_spec
        spec, profile = validate_agent_spec(recipe['spec'], recipe['model_profile'])
        require(sha(canonical(profile)) == recipe['model_profile_sha256'], 'recipe.model_profile_sha256',
                'recipe_digest_mismatch', 'Model profile binding changed.')
        manifest = recipe['agent_tb_manifest']
        body = {key: value for key, value in manifest.items() if key != 'manifest_sha256'}
        require(sha(canonical(body)) == manifest.get('manifest_sha256') and
                manifest.get('criteria_sha256') == sha(canonical(spec['criteria'])) and
                manifest.get('pdk_binding') == spec['pdk_binding'] and
                manifest.get('source_sha256') == recipe['files_sha256'].get('netlist/dut.scs') and
                manifest.get('tb_sha256') == recipe['files_sha256'].get('netlist/tb.scs') and
                manifest.get('managed_pdk', {}).get('sha256') == recipe['files_sha256'].get('netlist/pdk.scs'),
                'recipe.agent_tb_manifest', 'recipe_digest_mismatch', 'Authored byte, criteria or PDK manifest binding changed.')
        return spec
    profile = validate_model_profile(recipe['model_profile'])
    require(sha(canonical(profile)) == recipe['model_profile_sha256'], 'recipe.model_profile_sha256', 'recipe_digest_mismatch', 'Model profile binding changed.')
    return validate_spec(recipe['spec'], profile, recipe['source']['ports'])

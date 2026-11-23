"""Explicit AC response and saved DC operating-point measurements.

Margins are sampled-response measurements, never a closed-loop stability proof.
Device region codes and voltage conventions belong to the selected model.
"""
import math

from .common import numpy, vector, scalar, unit, require, result


def _crossings(x, y, level):
    """Keep crossings, tangencies, boundary hits and plateau evidence distinct."""
    crossings = []; plateau = False; index = 0
    while index < len(y):
        value = y[index]
        if value == level:
            end = index
            while end+1 < len(y) and y[end+1] == level: end += 1
            if end > index:
                plateau = True; classification = 'plateau'
            elif index == 0 or index == len(y)-1: classification = 'boundary'
            elif (y[index-1]-level)*(y[index+1]-level) < 0: classification = 'crossing'
            else: classification = 'touch'
            crossings.append((index, end, 0.0, float(x[index]), classification))
            index = end
        if index+1 < len(y) and (value-level)*(y[index+1]-level) < 0:
            weight = float((level-value)/(y[index+1]-value))
            frequency = (math.exp(math.log(x[index]) + weight*(math.log(x[index+1])-math.log(x[index])))
                         if x[index] > 0 else float(x[index] + weight*(x[index+1]-x[index])))
            crossings.append((index, index+1, weight, frequency, 'crossing'))
        index += 1
    return crossings, plateau


def _at(values, crossing):
    a, b, weight = crossing[:3]
    return float(values[a] + weight*(values[b]-values[a]))


def _phase_margin(angle):
    """Signed distance to the corresponding negative-real-axis branch."""
    margin = angle % 360 - 180
    # +/-180 describe the same limiting direction; choose one stable endpoint.
    return 180.0 if abs(abs(margin)-180) < 1e-10 else margin


def ac_metrics(frequency_hz, response_real, response_imag, *, response_kind, response_unit,
               reference_real=None, reference_imag=None, reference_unit=None,
               feedback_convention=None, phase_reference_deg=None, return_curves=True):
    """Measure a dimensionless complex transfer or a declared SISO loop gain."""
    np = numpy()
    frequency = vector(frequency_hz, 'frequency_hz', minimum=2)
    real = vector(response_real, 'response_real', minimum=2)
    imag = vector(response_imag, 'response_imag', minimum=2)
    require(len(frequency) == len(real) == len(imag) and frequency[0] >= 0
            and bool((np.diff(frequency) > 0).all()), 'invalid_frequency',
            'Frequency must be nonnegative, strictly increasing and aligned with the complex response.')
    require(response_kind in ('transfer', 'loop_gain'), 'response_kind_missing',
            'Declare a dimensionless transfer or a SISO loop_gain; voltages alone are not gain.')
    require(type(return_curves) is bool, 'invalid_parameter', 'return_curves must be boolean.')
    native_unit = unit(response_unit)
    response = real + 1j*imag
    has_reference = reference_real is not None or reference_imag is not None
    if has_reference:
        ref_real = vector(reference_real, 'reference_real', minimum=2)
        ref_imag = vector(reference_imag, 'reference_imag', minimum=2)
        ref_unit = unit(reference_unit)
        require(len(ref_real) == len(ref_imag) == len(frequency), 'reference_mismatch',
                'The saved complex reference must align with the same native frequency axis.')
        reference_response = ref_real + 1j*ref_imag
        require(bool((np.abs(reference_response) > 0).all()), 'zero_reference',
                'The selected complex reference is zero; do not assume a unit excitation.')
        response = response/reference_response
        effective_unit = '1' if native_unit == ref_unit else native_unit+'/'+ref_unit
    else:
        require(reference_unit is None, 'unused_reference_unit', 'A reference unit requires both saved reference components.')
        ref_unit = None; effective_unit = native_unit
    dimensionless = effective_unit == '1'
    require(bool(np.isfinite(response).all()), 'nonfinite_ratio', 'The complex response/reference ratio is not finite.')
    if response_kind == 'loop_gain':
        require(dimensionless, 'loop_gain_unit', 'Loop margins require an explicitly dimensionless loop response or same-unit output/reference ratio.')
        require(feedback_convention in ('denominator_1_plus_L', 'denominator_1_minus_T'),
                'feedback_convention_missing', 'Declare the characteristic denominator 1+L or 1-T.')
        reference = scalar(phase_reference_deg, 'phase_reference_deg')
        require(abs(reference) <= 3600, 'invalid_parameter', 'Phase branch anchor exceeds supported bounds.')
    else:
        require(feedback_convention is None and phase_reference_deg is None, 'unused_feedback_options',
                'Feedback and phase-branch declarations apply only to loop_gain.')
        reference = None
    if feedback_convention == 'denominator_1_minus_T': response = -response
    magnitude = np.abs(response)
    require(bool((magnitude > 0).all()), 'zero_response',
            'Zero response has undefined phase/log gain; supply a record that resolves the declared measurement.')
    magnitude_db = 20*np.log10(magnitude)
    phase = np.rad2deg(np.unwrap(np.angle(response)))
    if reference is not None:
        offset = (reference-float(phase[0]))/360
        require(abs(abs(offset-round(offset))-.5) > 1e-10, 'ambiguous_phase_anchor',
                'Initial phase is exactly halfway between phase branches; choose a different explicit anchor.')
        phase += 360*round(offset)
    unity, unity_plateau = _crossings(frequency, magnitude_db, 0.0) if dimensionless else ([], False)
    unity_rows = []
    for crossing in unity:
        angle = _at(phase, crossing)
        measured_margin = (_phase_margin(angle)
                           if response_kind == 'loop_gain' and crossing[4] == 'crossing' else None)
        unity_rows.append({'frequency_hz': crossing[3], 'phase_deg': angle,
            'classification': crossing[4], 'scalar_eligible': crossing[4] == 'crossing',
            'phase_margin_deg': measured_margin,
            'critical_phase_deg': angle-measured_margin if measured_margin is not None else None,
            'bracket_hz': [float(frequency[crossing[0]]), float(frequency[crossing[1]])]})
    phase_rows = []; phase_plateau = False
    if response_kind == 'loop_gain':
        # Every negative-real-axis crossing is relevant; never select one silently.
        start = math.ceil((float(np.min(phase))+180)/360)
        stop = math.floor((float(np.max(phase))+180)/360)
        require(stop-start <= 128, 'phase_range_limit', 'Too many phase branches for bounded margin analysis.')
        for branch in range(start, stop+1):
            angle = -180+360*branch
            crossings, plateau = _crossings(frequency, phase, angle)
            phase_plateau |= plateau
            for crossing in crossings:
                gain = _at(magnitude_db, crossing)
                phase_rows.append({'frequency_hz': crossing[3], 'phase_deg': angle,
                    'classification': crossing[4], 'scalar_eligible': crossing[4] == 'crossing',
                    'gain_db': gain, 'gain_margin_db': -gain if crossing[4] == 'crossing' else None,
                    'bracket_hz': [float(frequency[crossing[0]]), float(frequency[crossing[1]])]})
        phase_rows.sort(key=lambda row: row['frequency_hz'])
    warnings = ['sampled_response_only_no_extrapolation_or_closed_loop_stability_verdict']
    if not dimensionless: warnings.append('dimensional_response_has_no_dimensionless_gain_or_unity_margin')
    if frequency[0] != 0: warnings.append('dc_point_absent_low_frequency_gain_is_not_dc_gain')
    if len(unity) != 1: warnings.append('unity_crossing_absent' if not unity else 'multiple_unity_crossings')
    if unity_plateau: warnings.append('unity_gain_plateau_ambiguous')
    if any(row['classification'] != 'crossing' for row in unity_rows): warnings.append('unity_touch_or_boundary_is_not_a_bracketed_crossing')
    if response_kind == 'loop_gain' and len(phase_rows) != 1:
        warnings.append('phase_crossing_absent' if not phase_rows else 'multiple_phase_crossings')
    if phase_plateau: warnings.append('phase_crossing_plateau_ambiguous')
    if any(row['classification'] != 'crossing' for row in phase_rows): warnings.append('phase_touch_or_boundary_is_not_a_bracketed_crossing')
    unique_unity = len(unity_rows) == 1 and unity_rows[0]['classification'] == 'crossing' and not unity_plateau
    unique_phase = len(phase_rows) == 1 and phase_rows[0]['classification'] == 'crossing' and not phase_plateau
    # A singular margin must not conceal other crossings of the sampled loop.
    margins_unambiguous = unique_unity and (not phase_rows or unique_phase) and not phase_plateau
    metrics = {'dc_gain_db': float(magnitude_db[0]) if dimensionless and frequency[0] == 0 else None,
        'low_frequency_gain_db': float(magnitude_db[0]) if dimensionless else None,
        'low_frequency_magnitude': float(magnitude[0]), 'low_frequency_hz': float(frequency[0]),
        'unity_gain_bandwidth_hz': unity_rows[0]['frequency_hz'] if unique_unity else None,
        'phase_margin_deg': unity_rows[0]['phase_margin_deg'] if margins_unambiguous else None,
        'gain_margin_db': phase_rows[0]['gain_margin_db'] if unique_phase and unique_unity else None}
    curves = {'frequency_hz': frequency, 'magnitude_db': magnitude_db, 'phase_deg': phase,
              'frequency_unit': 'Hz', 'magnitude_unit': 'dB' if dimensionless else 'dB re 1 '+effective_unit,
              'response_unit': effective_unit, 'phase_unit': 'deg'}
    return result('ac_metrics', parameters={'sample_count': len(frequency), 'response_kind': response_kind,
        'response_unit': native_unit, 'reference_unit': ref_unit, 'has_reference': has_reference,
        'effective_response_unit': effective_unit, 'dimensionless_gain': dimensionless,
        'feedback_convention': feedback_convention,
        'phase_reference_deg': reference, 'normalized_characteristic_denominator': '1+L' if reference is not None else None,
        'interpolation': 'linear_dB_and_unwrapped_phase_vs_log_frequency;linear_frequency_at_zero',
        'phase_unwrap_assumption': 'less_than_180_degrees_between_adjacent_samples',
        'return_curves': return_curves}, metrics=metrics, diagnostics=warnings,
        metric_units={'dc_gain_db': 'dB', 'low_frequency_gain_db': 'dB', 'low_frequency_magnitude': effective_unit, 'low_frequency_hz': 'Hz',
            'unity_gain_bandwidth_hz': 'Hz', 'phase_margin_deg': 'deg', 'gain_margin_db': 'dB'},
        unity_crossings=unity_rows, phase_crossings=phase_rows,
        stability_verdict='not_evaluated', gbw_product_hz=None,
        **({'curves': curves} if return_curves else {}))


def _name(value):
    return isinstance(value, str) and 1 <= len(value) <= 256 and value.isprintable()


def _parameter(parameters, name, expected_unit):
    item = parameters.get(name)
    if not isinstance(item, dict) or item.get('status') != 'valid': return None, 'missing_or_undefined_'+name
    if item.get('unit') != expected_unit: return None, 'unit_mismatch_'+name
    value = item.get('value')
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value)>1e100: return None, 'nonfinite_or_unsupported_'+name
    return float(value), None


def dc_op_metrics(devices, *, required_devices, device_contracts, supplies=None):
    """Check explicit model-region/voltage criteria over one saved DC point."""
    require(isinstance(devices, list) and 1 <= len(devices) <= 4096, 'invalid_devices',
            'Provide one bounded list of saved operating-point records.')
    by_name = {}
    for device in devices:
        require(isinstance(device, dict) and _name(device.get('name'))
                and isinstance(device.get('parameters'), dict) and len(device['parameters']) <= 1024
                and device['name'] not in by_name, 'invalid_devices', 'Device records require distinct names and parameter objects.')
        by_name[device['name']] = device
    require(isinstance(required_devices, list) and 1 <= len(required_devices) <= 4096
            and all(_name(name) for name in required_devices) and len(set(required_devices)) == len(required_devices),
            'required_devices_missing', 'Declare distinct critical device names; do not infer them from a schematic.')
    require(isinstance(device_contracts, dict) and set(device_contracts) == set(required_devices),
            'device_contracts_missing', 'Provide one explicit device contract for each required device.')
    rows = []
    for name in required_devices:
        contract = device_contracts[name]
        require(isinstance(contract, dict) and not set(contract)-{
            'polarity','voltage_convention','vdsat_convention','saturation_method','region_map','region_map_source',
            'require_above_threshold','minimum_headroom_v'} and contract.get('polarity') in ('nmos','pmos')
            and contract.get('saturation_method') in ('region','vdsat'), 'invalid_device_contract',
            'Declare polarity and saturation_method=region or vdsat with only supported options.')
        method = contract['saturation_method']; device = by_name.get(name)
        method_fields = ({'polarity','saturation_method','region_map','region_map_source'} if method == 'region'
                         else {'polarity','saturation_method','voltage_convention','vdsat_convention',
                               'require_above_threshold','minimum_headroom_v'})
        require(not set(contract)-method_fields, 'unused_device_conditions',
                'Device conditions must belong to the selected saturation method; unused conditions are rejected.')
        parameters = device['parameters'] if device else {}
        missing = []; observed = {}
        for field, field_unit in [('vgs','V'),('vds','V'),('vth','V'),('vdsat','V'),('id','A'),('gm','S'),('gds','S')]:
            value, issue = _parameter(parameters, field, field_unit)
            observed[field] = {'value': value, 'unit': field_unit, 'status': 'valid' if issue is None else 'undefined'}
        verdict = 'unknown'; region_label = None; headroom = None
        if method == 'region':
            mapping = contract.get('region_map'); source = contract.get('region_map_source')
            require(isinstance(mapping, dict) and 1 <= len(mapping) <= 32 and _name(source)
                    and all(isinstance(code, str) and len(code) <= 32 and label in
                    ('saturation','linear','cutoff','subthreshold','breakdown','unknown') for code,label in mapping.items()),
                    'region_mapping_missing', 'Model-specific region codes and their evidence source must be explicit.')
            record = parameters.get('region', {})
            value = record.get('value')
            if record.get('status') == 'valid' and record.get('unit') in (None,'','1') and type(value) in (int,float) and math.isfinite(value):
                key = str(int(value)) if value == int(value) else str(value)
                region_label = mapping.get(key)
            if region_label in (None, 'unknown'): missing.append('region_absent_undefined_or_unmapped')
            else: verdict = 'pass' if region_label == 'saturation' else 'fail'
        else:
            require(contract.get('voltage_convention') in ('signed_vgs_vds','polarity_normalized')
                    and contract.get('vdsat_convention') in ('signed','magnitude')
                    and type(contract.get('require_above_threshold')) is bool,
                    'voltage_convention_missing', 'Declare voltage/vdsat sign conventions and whether above-threshold operation is required.')
            minimum = scalar(contract.get('minimum_headroom_v', 0.0), 'minimum_headroom_v')
            require(minimum >= 0, 'invalid_headroom', 'Minimum saturation headroom cannot be negative.')
            sign = -1 if contract['polarity'] == 'pmos' and contract['voltage_convention'] == 'signed_vgs_vds' else 1
            vds = observed['vds']['value']; vdsat = observed['vdsat']['value']
            if vds is None or vdsat is None: missing.append('vds_or_vdsat_absent_undefined_or_wrong_unit')
            else:
                drain = sign*vds
                saturation_sign = -1 if contract['polarity'] == 'pmos' and contract['vdsat_convention'] == 'signed' else 1
                saturation = saturation_sign*vdsat
                if saturation < 0 or drain < 0: missing.append('reverse_or_inconsistent_voltage_orientation')
                else:
                    headroom = drain-saturation
                    verdict = 'pass' if headroom >= minimum else 'fail'
            if contract['require_above_threshold']:
                vgs = observed['vgs']['value']; vth = observed['vth']['value']
                if vgs is None or vth is None: missing.append('vgs_or_vth_absent_undefined_or_wrong_unit')
                elif sign*vgs <= sign*vth: verdict = 'fail'
            if missing: verdict = 'unknown'
        if device is None: missing.append('required_device_absent'); verdict = 'unknown'
        current = observed['id']['value']; gm = observed['gm']['value']; gds = observed['gds']['value']
        def ratio(denominator, ratio_unit):
            value=abs(gm/denominator) if gm is not None and denominator else None
            if value is not None and not math.isfinite(value):value=None
            return {'value':value,'unit':ratio_unit,'status':'valid' if value is not None else 'undefined'}
        observed.update(gm_over_id=ratio(current,'1/V'),intrinsic_gain=ratio(gds,'1'))
        rows.append({'name': name, 'model_type': device.get('model_type') if device else None,
            'contract': contract, 'observed': observed, 'region': parameters.get('region'),
            'region_label': region_label, 'saturation_headroom_v': headroom,
            'saturation_verdict': verdict, 'missing': missing,
            'scope': 'declared_model_region_map' if method == 'region' else 'declared_vdsat_voltage_condition'})
    supply_rows = []; total_power = None
    if supplies is not None:
        require(isinstance(supplies, list) and 1 <= len(supplies) <= 32, 'invalid_supplies',
                'Supply power requires one to 32 explicitly selected rail measurements.')
        used = set(); total_power = 0.0
        for supply in supplies:
            require(isinstance(supply, dict) and set(supply) == {'name','voltage_v','current_a','current_sign'}
                    and _name(supply['name']) and supply['name'] not in used
                    and supply['current_sign'] in ('into_dut','from_dut'), 'invalid_supplies',
                    'Each supply needs a distinct name, voltage in V, current in A and current direction.')
            used.add(supply['name'])
            voltage = scalar(supply['voltage_v'], 'voltage_v'); current = scalar(supply['current_a'], 'current_a')
            power = voltage*current*(1 if supply['current_sign'] == 'into_dut' else -1)
            total_power += power; supply_rows.append({**supply, 'absorbed_power_w': power})
    unknown = sum(row['saturation_verdict']=='unknown' for row in rows)
    failed = sum(row['saturation_verdict']=='fail' for row in rows)
    return result('dc_op_metrics', parameters={'required_devices': required_devices,
        'device_contracts': device_contracts, 'supply_power_scope': 'explicit_selected_rails_only'},
        metrics={'required_device_count': len(rows), 'saturation_pass_count': len(rows)-unknown-failed,
            'saturation_fail_count': failed, 'saturation_unknown_count': unknown, 'static_power_w': total_power},
        metric_units={'required_device_count':'count','saturation_pass_count':'count',
            'saturation_fail_count':'count','saturation_unknown_count':'count','static_power_w':'W'},
        status='invalid' if unknown else 'valid',
        diagnostics=['model_region_and_voltage_conditions_do_not_establish_full_device_qualification'],
        devices=rows, supplies=supply_rows, saturation_result='unknown' if unknown else 'fail' if failed else 'pass')

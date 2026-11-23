"""Synthetic PSF evidence exercises frozen condition and threshold handling."""
import copy
import hashlib
import json
import math
import unittest
from unittest.mock import patch

from spiceowl.experiments import prepare_experiment, measure_psf_ascii
from spiceowl.experiments.measurement import _crossings, _delays
from tests.test_experiment_recipes import sources, selection, profile, spec


def prepared(voltage=1.0):
    result=prepare_experiment(sources(),selection(),spec(voltage),profile())
    assert result['status']=='ready',result
    return result['recipe']


def pulse(t, voltage):
    if t < 1e-10:
        return 0.0
    phase=(t-1e-10)%1e-9
    if phase < 1e-11:
        return voltage*phase/1e-11
    if phase < 5e-10:
        return voltage
    if phase < 5.1e-10:
        return voltage*(1-(phase-5e-10)/1e-11)
    return 0.0


def samples(voltage=1.0, *, invert=True):
    times=[i*1e-12 for i in range(5001)]
    incoming=[pulse(t,voltage) for t in times]
    outgoing=[voltage-pulse(t-2e-11,voltage) for t in times] if invert else incoming[:]
    return {'time':times,'IN':incoming,'OUT':outgoing,'HI':[voltage]*len(times),'LO':[0.0]*len(times)}


def psf(values=None, *, units=None):
    values=samples() if values is None else values
    units=units or {'time':'s','IN':'V','OUT':'V','HI':'V','LO':'V'}
    lines=['HEADER','"PSF sweep points" '+str(len(values['time'])),'TYPE']
    for index,(name,unit) in enumerate(units.items()):
        lines.append('"type'+str(index)+'" FLOAT DOUBLE PROP("units" "'+unit+'")')
    indexes={name:index for index,name in enumerate(units)}
    lines+=['SWEEP','"time" "type'+str(indexes['time'])+'"','TRACE']
    lines += ['"'+name+'" "type'+str(indexes[name])+'"' for name in values if name!='time']
    lines.append('VALUE')
    for index in range(len(values['time'])):
        lines += ['"'+name+'" '+format(series[index],'.17g') for name,series in values.items()]
    lines.append('END')
    return '\n'.join(lines)+'\n'


class ExperimentMeasurementTests(unittest.TestCase):
    def test_one_volt_and_nine_tenths_functional_measurements_with_report_only_delay(self):
        for voltage in (1.0,.9):
            recipe=prepared(voltage)
            text=psf(samples(voltage))
            report=measure_psf_ascii(text,recipe,execution_state='completed',exit_code=0)
            self.assertEqual(report['data_validity'],'valid',report)
            self.assertEqual(report['specification'],'pass')
            self.assertIsNone(report['overall_qualified_acceptance'])
            self.assertEqual(report['sample_count'],5001)
            self.assertEqual(report['psf_sha256'],hashlib.sha256(text.encode()).hexdigest())
            self.assertEqual(report['delays']['input_transition_count'],10)
            self.assertEqual(report['delays']['paired_count'],10)
            for kind in ('tPHL','tPLH'):
                self.assertAlmostEqual(report['delays']['summary'][kind]['mean_s']/1e-12,20,places=6)

    def test_wrong_output_is_a_valid_measurement_of_specification_failure(self):
        report=measure_psf_ascii(psf(samples(invert=False)),prepared())
        self.assertEqual(report['data_validity'],'valid')
        self.assertEqual(report['specification'],'fail')
        self.assertEqual(report['execution']['state'],'unknown')
        self.assertEqual(report['delays']['paired_count'],0)
        self.assertTrue(any(row['name'].startswith('output_logic') and row['status']=='fail' for row in report['checks']))

    def test_wrong_input_or_supply_invalidates_experiment_without_fabricating_spec_pass(self):
        for signal,value in (('IN',0.0),('HI',.8),('LO',.05)):
            data=samples();data[signal]=[value]*len(data['time'])
            report=measure_psf_ascii(psf(data),prepared())
            self.assertEqual(report['data_validity'],'invalid')
            self.assertEqual(report['specification'],'unknown')

    def test_empty_missing_malformed_nonfinite_and_single_sample_do_not_crash(self):
        for text in ('',None,'not psf','HEADER\nTYPE\nSWEEP\nTRACE\nVALUE\nEND\n'):
            result=measure_psf_ascii(text,prepared())
            self.assertIn(result['data_validity'],('unknown','invalid'))
            self.assertEqual(result['specification'],'unknown')
            json.dumps(result,allow_nan=False)
        data={name:series[:1] for name,series in samples().items()}
        report=measure_psf_ascii(psf(data),prepared())
        self.assertEqual(report['data_validity'],'invalid')
        data=samples();data['OUT'][50]=float('nan')
        report=measure_psf_ascii(psf(data),prepared())
        self.assertEqual(report['data_validity'],'invalid')
        self.assertEqual(report['specification'],'unknown')

    def test_nonmonotonic_unaligned_wrong_units_and_absent_signal_fail(self):
        data=samples();data['time'][20]=data['time'][19]
        self.assertEqual(measure_psf_ascii(psf(data),prepared())['data_validity'],'invalid')
        text=psf().replace('"OUT" 1\n','',1)
        self.assertEqual(measure_psf_ascii(text,prepared())['data_validity'],'invalid')
        self.assertEqual(measure_psf_ascii(psf(units={'time':'ns','IN':'V','OUT':'V','HI':'V','LO':'V'}),prepared())['data_validity'],'invalid')
        data=samples();data.pop('OUT')
        self.assertEqual(measure_psf_ascii(psf(data),prepared())['data_validity'],'invalid')

    def test_time_coverage_gap_and_window_density_are_checked(self):
        data={name:series[:-500] for name,series in samples().items()}
        report=measure_psf_ascii(psf(data),prepared())
        self.assertEqual(report['data_validity'],'invalid')
        self.assertTrue(any(c['name']=='coverage' and c['status']=='fail' for c in report['checks']))
        self.assertTrue(any(c['name'].startswith('window_samples') and c['status']=='fail' for c in report['checks']))
        keep=[i for i in range(5001) if not 1100 < i < 1110]
        data={name:[series[i] for i in keep] for name,series in samples().items()}
        report=measure_psf_ascii(psf(data),prepared())
        self.assertEqual(report['data_validity'],'invalid')
        self.assertTrue(any(c['name']=='sample_gap' and c['status']=='fail' for c in report['checks']))

    def test_out_of_range_actual_voltage_is_spec_failure_not_fake_invalid_data(self):
        data=samples();data['OUT'][3000]=1.2
        report=measure_psf_ascii(psf(data),prepared())
        self.assertEqual(report['data_validity'],'valid')
        self.assertEqual(report['specification'],'fail')
        self.assertTrue(any(c['name']=='voltage_bounds' and c['status']=='fail' for c in report['checks']))

    def test_execution_outcome_is_independent_of_waveform_measurements(self):
        report=measure_psf_ascii(psf(),prepared(),execution_state='failed',exit_code=9)
        self.assertEqual(report['execution']['state'],'failed')
        self.assertEqual(report['data_validity'],'valid')
        self.assertEqual(report['specification'],'pass')
        self.assertIsNone(report['overall_qualified_acceptance'])

    def test_frozen_conditions_cannot_be_relaxed_after_the_run(self):
        recipe=prepared();recipe['spec']['acceptance']['logic']['low_max_v']=.8
        report=measure_psf_ascii(psf(),recipe)
        self.assertEqual(report['data_validity'],'unknown')
        self.assertEqual(report['missing'][0]['code'],'recipe_digest_mismatch')
        recipe=prepared();recipe['model_profile']['models']['device_alpha']['polarity']='p'
        self.assertEqual(measure_psf_ascii(psf(),recipe)['missing'][0]['code'],'recipe_digest_mismatch')

    def test_prepared_transient_uses_explicit_windows_without_inverter_inference(self):
        target=spec();target['recipe']='prepared-transient';target['acceptance'].pop('logic')
        target['acceptance']['windows']=[{'signal':'OUT','start_s':.25e-9,'end_s':.45e-9,
                                          'minimum_samples':190,'min_v':-.05,'max_v':.1}]
        recipe=prepare_experiment(sources(),selection(),target,profile())['recipe']
        report=measure_psf_ascii(psf(),recipe)
        self.assertEqual(report['data_validity'],'valid')
        self.assertEqual(report['specification'],'pass')
        self.assertIsNone(report['delays'])
        target['acceptance']['windows']=[]
        recipe=prepare_experiment(sources(),selection(),target,profile())['recipe']
        self.assertEqual(measure_psf_ascii(psf(),recipe)['specification'],'unknown')

    def test_threshold_touches_and_ambiguous_edges_do_not_invent_delay(self):
        self.assertEqual(_crossings([0,1,2],[0,.5,0],.5),[])
        self.assertEqual(_crossings([0,1,2,3],[0,.5,.5,1],.5),[(1,'rising')])
        result=_delays([0,1,2,3,4,5,6],[0,0,1,1,1,1,1],[1,1,0,1,0,0,0],.5)
        self.assertEqual(result['paired_count'],0)
        self.assertEqual(result['summary']['tPHL']['count'],0)
        self.assertIsNone(result['summary']['tPLH']['mean_s'])
        self.assertNotIn('_sum', result['summary']['tPLH'])

    def test_measurement_does_not_read_files_or_invoke_a_model(self):
        text,recipe=psf(),prepared()
        with (patch('builtins.open',side_effect=AssertionError('filesystem')),
              patch('subprocess.run',side_effect=AssertionError('external process'))):
            self.assertEqual(measure_psf_ascii(text,recipe)['specification'],'pass')


if __name__=='__main__':unittest.main()

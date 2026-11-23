"""Original synthetic native-format and analytical AC/DC regressions."""
import copy
import hashlib
import json
import math
from pathlib import Path
import unittest

from spiceowl.integrations.spectre_results import PsfError, parse_psf_analysis
from spiceowl.adctools import AnalysisError, ac_metrics, dc_op_metrics

try:
    import numpy as np
except ImportError:
    np = None


def native_ac():
    return '''HEADER
"analysis type" "ac"
"analysis name" "gain"
"simulator" "synthetic-fixture"
"version" "fixture-1"
TYPE
"frequency" FLOAT DOUBLE PROP("units" "Hz")
"voltage" COMPLEX DOUBLE PROP("units" "V")
SWEEP
"freq" "frequency"
TRACE
"pair" GROUP 2
"out" "voltage"
"reference" "voltage"
VALUE
"freq" 0
"pair" (20 0) (2 0)
"freq" 10
"pair" (10 -10) (2 0)
"freq" 100
"pair" (0.198019801980198 -1.9801980198019802) (2 0)
END
'''


def native_op():
    return '''HEADER
"analysis type" "info"
"analysis name" "dcOpInfo"
"AnalysisName" "bias"
"AnalysisType" "dcOp"
"simulator" "synthetic-fixture"
"version" "fixture-1"
TYPE
"toy_mos" STRUCT(
"vgs" FLOAT DOUBLE PROP("units" "V")
"vds" FLOAT DOUBLE PROP("units" "V")
"vth" FLOAT DOUBLE PROP("units" "V")
"vdsat" FLOAT DOUBLE PROP("units" "V")
"id" FLOAT DOUBLE PROP("units" "A")
"gm" FLOAT DOUBLE PROP("units" "S")
"gds" FLOAT DOUBLE PROP("units" "S")
"region" INT BYTE
"unused_diagnostic" FLOAT DOUBLE
) PROP("key" "inst")
VALUE
"N1" "toy_mos" (0.7 0.8 0.3 0.2 0.001 0.01 0.0001 2 NaN) PROP("model" "authored_nmos")
"P1" "toy_mos" (-0.7 -0.8 -0.3 -0.2 -0.001 0.01 0.0001 2 Inf) PROP("model" "authored_pmos")
END
'''


def native_dc(sweep=True):
    head='''HEADER
"analysis type" "dc"
"analysis name" "bias"
TYPE
"voltage" FLOAT DOUBLE PROP("units" "V")
'''
    if sweep:
        return head+'''SWEEP
"supply" "voltage"
TRACE
"out" "voltage"
VALUE
"supply" 0
"out" 0
"supply" 1
"out" 0.5
END
'''
    return head+'''VALUE
"out" "voltage" 0.5
END
'''


def contracts():
    return {'N1':{'polarity':'nmos','saturation_method':'region','region_map':{'2':'saturation','1':'linear'},
                  'region_map_source':'original synthetic model convention'},
            'P1':{'polarity':'pmos','saturation_method':'vdsat','voltage_convention':'signed_vgs_vds',
                  'vdsat_convention':'signed','require_above_threshold':True,'minimum_headroom_v':.1}}


class NativeDatasetTests(unittest.TestCase):
    def test_ac_group_values_and_analysis_identity_preserve_original_units(self):
        raw=native_ac().replace('\n','\r\n').encode()
        data=parse_psf_analysis(raw,analysis_kind='ac',analysis_name='gain')
        self.assertEqual(data['raw_sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(data['sample_count'],3)
        self.assertEqual(data['axis'],{'name':'freq','unit':'Hz','values':[0,10,100]})
        self.assertEqual(data['signals'][0]['real'],[20,10,.198019801980198])
        self.assertEqual(data['signals'][0]['imag'],[0,-10,-1.9801980198019802])
        self.assertEqual(data['signals'][0]['unit'],'V')
        self.assertFalse(data['has_undefined_values'])

    def test_static_scalar_and_swept_dc_are_distinct(self):
        swept=parse_psf_analysis(native_dc(),analysis_kind='dc')
        point=parse_psf_analysis(native_dc(False),analysis_kind='dc')
        self.assertEqual(swept['axis']['values'],[0,1])
        self.assertIsNone(point['axis'])
        self.assertEqual(point['signals'][0]['values'],[.5])
        self.assertEqual(point['sample_count'],1)

    def test_dc_sweep_can_descend_but_cannot_reverse_or_repeat(self):
        raw=native_dc().replace('"supply" 0','"supply" 2')
        data=parse_psf_analysis(raw,analysis_kind='dc')
        self.assertEqual(data['axis']['values'],[2,1])
        for suffix in ('"supply" 1\n"out" 0.5\n','"supply" 2\n"out" 0.5\n'):
            with self.assertRaises(PsfError):parse_psf_analysis(raw.replace('END',suffix+'END'),analysis_kind='dc')

    def test_wide_struct_maps_positional_parameters_and_marks_native_nonfinite(self):
        data=parse_psf_analysis(native_op(),analysis_kind='dc_op')
        self.assertEqual(len(data['operating_points']),2)
        self.assertEqual(data['analysis']['name'],'dcOpInfo')
        self.assertEqual(data['analysis']['producer_name'],'bias')
        self.assertEqual(data['analysis']['producer_type'],'dcOp')
        n=data['operating_points'][0]
        self.assertEqual(n['name'],'N1');self.assertEqual(n['model'],'authored_nmos')
        self.assertEqual(n['parameters']['gm']['value'],.01)
        self.assertEqual(n['parameters']['region']['value'],2)
        self.assertIsNone(n['parameters']['unused_diagnostic']['value'])
        self.assertEqual(n['parameters']['unused_diagnostic']['status'],'undefined')
        json.dumps(data,allow_nan=False)

    def test_declared_analysis_mismatch_or_identity_ambiguity_rejected(self):
        for args in ({'analysis_kind':'dc'},{'analysis_kind':'ac','analysis_name':'other'}):
            with self.assertRaises(PsfError):parse_psf_analysis(native_ac(),**args)
        with self.assertRaises(PsfError):parse_psf_analysis(native_ac().replace('TYPE','"analysis name" "other"\nTYPE'),analysis_kind='ac')
        raw=native_ac().replace('TYPE','"BINPSF creation time" "1"\n"BINPSF creation time" "2"\nTYPE')
        self.assertIn('duplicate_nonidentity_header:BINPSF creation time',parse_psf_analysis(raw,analysis_kind='ac')['warnings'])

    def test_truncation_binary_incomplete_group_and_unknown_trace_fail(self):
        for raw in (native_ac().replace('END',''),native_ac()+'\0',native_ac().replace('(2 0)\n"freq" 10','\n"freq" 10'),
                    native_ac().replace('"pair" (20 0)','"unknown" (20 0)'),native_op().replace('2 NaN)','NaN)')):
            with self.subTest(raw=raw[:40]),self.assertRaises(PsfError):
                parse_psf_analysis(raw,analysis_kind='dc_op' if 'STRUCT' in raw else 'ac')

    def test_bad_frequency_units_nonmonotonic_and_nonfinite_axes_rejected(self):
        for raw in (native_ac().replace('"Hz"','"rad/s"'),native_ac().replace('"freq" 100','"freq" 5'),
                    native_ac().replace('"freq" 100','"freq" NaN')):
            with self.assertRaises(PsfError):parse_psf_analysis(raw,analysis_kind='ac')

    def test_dc_struct_unit_metadata_is_not_inferred_or_rewritten(self):
        raw=native_op().replace('"gm" FLOAT DOUBLE PROP("units" "S")','"gm" FLOAT DOUBLE')
        data=parse_psf_analysis(raw,analysis_kind='dc_op')
        self.assertIsNone(data['operating_points'][0]['parameters']['gm']['unit'])


@unittest.skipIf(np is None,'optional numerical extra is not installed')
class ACNumericsTests(unittest.TestCase):
    def call(self,f,h,**kwargs):
        return ac_metrics(f,np.real(h),np.imag(h),backend='spiceowl',response_kind='loop_gain',response_unit='1',
            feedback_convention='denominator_1_plus_L',phase_reference_deg=0,**kwargs)

    def test_single_pole_dc_gain_unity_and_phase_match_closed_form(self):
        f=np.r_[0,np.geomspace(.001,1e7,20000)];h=100/(1+1j*f/10)
        r=self.call(f,h);m=r['metrics']
        self.assertAlmostEqual(m['dc_gain_db']['value'],40,places=12)
        self.assertAlmostEqual(m['unity_gain_bandwidth_hz']['value'],10*math.sqrt(9999),delta=.001)
        self.assertAlmostEqual(m['phase_margin_deg']['value'],90+math.degrees(math.asin(.01)),delta=.0001)
        self.assertIsNone(m['gain_margin_db']['value'])
        self.assertEqual(r['stability_verdict'],'not_evaluated')

    def test_three_pole_gain_and_phase_margin_match_independent_formula(self):
        f=np.r_[0,np.geomspace(.001,1e6,20000)];h=4/(1+1j*f/10)**3
        r=self.call(f,h);m=r['metrics']
        crossing=10*math.sqrt(4**(2/3)-1)
        self.assertAlmostEqual(m['unity_gain_bandwidth_hz']['value'],crossing,delta=.0001)
        self.assertAlmostEqual(m['phase_margin_deg']['value'],180-3*math.degrees(math.atan(crossing/10)),delta=.0001)
        self.assertAlmostEqual(m['gain_margin_db']['value'],20*math.log10(2),delta=.0001)

    def test_complex_reference_is_divided_and_missing_reference_never_assumes_one(self):
        f=np.r_[0,np.geomspace(.1,1e5,3000)];h=10/(1+1j*f/10);reference=2*np.exp(.4j)*np.ones(len(f));out=h*reference
        r=ac_metrics(f,out.real,out.imag,reference_real=reference.real,reference_imag=reference.imag,
            backend='spiceowl',response_kind='transfer',response_unit='V',reference_unit='V')
        self.assertAlmostEqual(r['metrics']['dc_gain_db']['value'],20)
        raw=ac_metrics(f,out.real,out.imag,backend='spiceowl',response_kind='transfer',response_unit='V')
        for name in ('dc_gain_db','unity_gain_bandwidth_hz','phase_margin_deg'):self.assertIsNone(raw['metrics'][name]['value'])
        self.assertEqual(raw['curves']['magnitude_unit'],'dB re 1 V')
        self.assertEqual(raw['unity_crossings'],[])

    def test_dimensional_ratio_and_explicit_loop_sign(self):
        f=np.r_[0,np.geomspace(.1,1e5,1000)];h=10/(1+1j*f/10)
        r=ac_metrics(f,h.real,h.imag,reference_real=np.ones(len(f)),reference_imag=np.zeros(len(f)),
            reference_unit='A',response_unit='V',response_kind='transfer',backend='spiceowl')
        self.assertEqual(r['curves']['response_unit'],'V/A')
        self.assertIsNone(r['metrics']['dc_gain_db']['value'])
        a=self.call(f,h)
        b=ac_metrics(f,-h.real,-h.imag,backend='spiceowl',response_kind='loop_gain',response_unit='1',
            feedback_convention='denominator_1_minus_T',phase_reference_deg=0)
        self.assertEqual(a['metrics'],b['metrics'])

    def test_multiple_crossings_and_plateaus_never_collapse_to_one_margin(self):
        f=np.array([1,10,100,1000]);h=np.array([10,.1,10,.1])*np.exp(1j*np.deg2rad([0,-60,-180,-240]))
        r=self.call(f,h)
        self.assertEqual(len(r['unity_crossings']),3)
        for name in ('unity_gain_bandwidth_hz','phase_margin_deg','gain_margin_db'):self.assertIsNone(r['metrics'][name]['value'])
        r=self.call(f,np.array([10,1,1,.1],complex))
        self.assertIn('unity_gain_plateau_ambiguous',r['warnings'])
        self.assertIsNone(r['metrics']['unity_gain_bandwidth_hz']['value'])

    def test_no_dc_sample_preserves_low_frequency_label(self):
        f=np.array([1,10,100]);h=10/(1+1j*f/10);r=self.call(f,h)
        self.assertIsNone(r['metrics']['dc_gain_db']['value'])
        self.assertEqual(r['metrics']['low_frequency_hz']['value'],1)
        self.assertIsNotNone(r['metrics']['low_frequency_gain_db']['value'])

    def test_phase_branch_anchor_cannot_change_physical_margin(self):
        f=np.geomspace(.1,1e5,3000);h=10/(1+1j*f/10)
        answers=[];phases=[]
        for anchor in (0,360,-360):
            r=ac_metrics(f,h.real,h.imag,backend='spiceowl',response_kind='loop_gain',response_unit='1',
                feedback_convention='denominator_1_plus_L',phase_reference_deg=anchor)
            answers.append(r['metrics']['phase_margin_deg']['value'])
            phases.append(r['unity_crossings'][0]['phase_deg'])
        self.assertAlmostEqual(answers[0],answers[1],places=10)
        self.assertAlmostEqual(answers[0],answers[2],places=10)
        self.assertAlmostEqual(phases[1]-phases[0],360,places=10)
        self.assertAlmostEqual(phases[2]-phases[0],-360,places=10)

    def test_unity_tangencies_and_boundary_hits_are_evidence_not_crossings(self):
        for amplitude,classification in (([2,1,2],'touch'),([1,.5,.25],'boundary'),([2,1.5,1],'boundary')):
            r=self.call([1,10,100],np.array(amplitude,complex))
            self.assertEqual(r['unity_crossings'][0]['classification'],classification)
            self.assertFalse(r['unity_crossings'][0]['scalar_eligible'])
            self.assertIsNone(r['unity_crossings'][0]['phase_margin_deg'])
            for name in ('unity_gain_bandwidth_hz','phase_margin_deg','gain_margin_db'):
                self.assertIsNone(r['metrics'][name]['value'])
        r=self.call([1,10,100],np.array([2,1,.5],complex))
        self.assertEqual(r['unity_crossings'][0]['classification'],'crossing')
        self.assertEqual(r['metrics']['unity_gain_bandwidth_hz']['value'],10)

    def test_phase_tangency_and_boundary_cannot_supply_a_gain_margin(self):
        for angles,classification in (([-90,-180,-90],'touch'),([-180,-190,-200],'boundary')):
            h=np.array([2,1,.5])*np.exp(1j*np.deg2rad(angles))
            # Exact negative-real entries avoid trig-roundoff shifting an equality.
            if angles[1]==-180:h[1]=-1+0j
            if angles[0]==-180:h[0]=-2+0j
            r=ac_metrics([1,10,100],h.real,h.imag,backend='spiceowl',response_kind='loop_gain',response_unit='1',
                feedback_convention='denominator_1_plus_L',phase_reference_deg=-90)
            self.assertEqual(r['phase_crossings'][0]['classification'],classification)
            self.assertIsNone(r['metrics']['gain_margin_db']['value'])

    def test_invalid_reference_frequency_loop_units_and_ambiguous_phase_fail(self):
        for kwargs in ({'response_unit':'V','feedback_convention':'denominator_1_plus_L','phase_reference_deg':0},
                       {'response_unit':'1'},
                       {'response_unit':'1','feedback_convention':'denominator_1_plus_L','phase_reference_deg':180},
                       {'response_unit':'1','feedback_convention':'denominator_1_plus_L','phase_reference_deg':0,
                        'reference_real':[0,0],'reference_imag':[0,0],'reference_unit':'1'}):
            with self.assertRaises(AnalysisError):ac_metrics([1,2],[2,1],[0,0],backend='spiceowl',response_kind='loop_gain',**kwargs)
        with self.assertRaises(AnalysisError):self.call([2,1],[2+0j,1+0j])


class DCMetricsTests(unittest.TestCase):
    def setUp(self):self.devices=parse_psf_analysis(native_op(),analysis_kind='dc_op')['operating_points']
    def call(self,**kwargs):return dc_op_metrics(self.devices,required_devices=['N1','P1'],device_contracts=contracts(),backend='spiceowl',**kwargs)

    def test_region_mapping_signed_pmos_and_selected_rail_power(self):
        report=self.call(supplies=[{'name':'VDD','voltage_v':1.2,'current_a':-.001,'current_sign':'from_dut'}])
        self.assertEqual(report['status'],'valid');self.assertEqual(report['saturation_result'],'pass')
        self.assertAlmostEqual(report['devices'][1]['saturation_headroom_v'],.6)
        self.assertAlmostEqual(report['metrics']['static_power_w']['value'],.0012)
        self.assertAlmostEqual(report['devices'][0]['observed']['gm_over_id']['value'],10)
        self.assertAlmostEqual(report['devices'][0]['observed']['intrinsic_gain']['value'],100)

    def test_missing_model_region_mapping_and_missing_key_devices_are_explicit(self):
        self.devices[0]['parameters']['region']['value']=9
        report=self.call();self.assertEqual(report['saturation_result'],'unknown')
        self.assertEqual(report['status'],'invalid')
        self.devices.pop();report=self.call()
        self.assertIn('required_device_absent',report['devices'][1]['missing'])
        bad=contracts();bad['N1'].pop('region_map_source')
        with self.assertRaises(AnalysisError):dc_op_metrics(self.devices,required_devices=['N1','P1'],device_contracts=bad,backend='spiceowl')

    def test_non_saturated_device_is_valid_measurement_but_failed_check(self):
        self.devices[0]['parameters']['region']['value']=1
        report=self.call();self.assertEqual(report['status'],'valid')
        self.assertEqual(report['saturation_result'],'fail')
        self.assertEqual(report['metrics']['saturation_fail_count']['value'],1)

    def test_unit_mismatch_or_reverse_orientation_cannot_pass(self):
        for field,value in [('unit','mV'),('value',.8)]:
            saved=copy.deepcopy(self.devices)
            self.devices[1]['parameters']['vds'][field]=value
            report=self.call();self.assertEqual(report['devices'][1]['saturation_verdict'],'unknown')
            self.devices=saved
        self.devices[0]['parameters']['region']['unit']='V'
        self.assertEqual(self.call()['devices'][0]['saturation_verdict'],'unknown')

    def test_threshold_policy_is_explicit_and_power_is_not_sum_of_devices(self):
        report=self.call();self.assertIsNone(report['metrics']['static_power_w']['value'])
        self.devices[1]['parameters']['vgs']['value']=-.1
        self.assertEqual(self.call()['devices'][1]['saturation_verdict'],'fail')
        policy=contracts();policy['P1']['require_above_threshold']=False
        report=dc_op_metrics(self.devices,required_devices=['N1','P1'],device_contracts=policy,backend='spiceowl')
        self.assertEqual(report['devices'][1]['saturation_verdict'],'pass')

    def test_unresolved_ratio_does_not_have_null_value_with_valid_status(self):
        self.devices[0]['parameters']['gm']['value']=1e100
        self.devices[0]['parameters']['gds']['value']=1e-300
        observed=self.call()['devices'][0]['observed']['intrinsic_gain']
        self.assertIsNone(observed['value']);self.assertEqual(observed['status'],'undefined')

    def test_method_specific_unused_conditions_are_rejected(self):
        for field,value in [('require_above_threshold',True),('minimum_headroom_v',10),
                            ('voltage_convention','signed_vgs_vds'),('vdsat_convention','signed')]:
            policy=contracts();policy['N1'][field]=value
            with self.subTest(field=field),self.assertRaisesRegex(AnalysisError,'unused conditions'):
                dc_op_metrics(self.devices,required_devices=['N1','P1'],device_contracts=policy,backend='spiceowl')
        for field,value in [('region_map',{'2':'saturation'}),('region_map_source','test model')]:
            policy=contracts();policy['P1'][field]=value
            with self.assertRaisesRegex(AnalysisError,'unused conditions'):
                dc_op_metrics(self.devices,required_devices=['N1','P1'],device_contracts=policy,backend='spiceowl')

    def test_pmos_vdsat_sign_convention_is_independent_of_other_voltages(self):
        for voltage_convention in ('signed_vgs_vds','polarity_normalized'):
            for vdsat_convention in ('signed','magnitude'):
                policy=contracts();policy['P1']['voltage_convention']=voltage_convention
                policy['P1']['vdsat_convention']=vdsat_convention
                device=copy.deepcopy(self.devices[1])
                for name in ('vgs','vds','vth'):
                    device['parameters'][name]['value']=(abs(device['parameters'][name]['value']) *
                        (-1 if voltage_convention=='signed_vgs_vds' else 1))
                device['parameters']['vdsat']['value']=-.2 if vdsat_convention=='signed' else .2
                report=dc_op_metrics([self.devices[0],device],required_devices=['N1','P1'],device_contracts=policy,backend='spiceowl')
                self.assertEqual(report['devices'][1]['saturation_verdict'],'pass')
                self.assertAlmostEqual(report['devices'][1]['saturation_headroom_v'],.6)


def write_examples(path):
    ac=parse_psf_analysis(native_ac(),analysis_kind='ac');op=parse_psf_analysis(native_op(),analysis_kind='dc_op')
    args={'backend':'spiceowl','frequency_hz':ac['axis']['values'],
        'response_real':ac['signals'][0]['real'],'response_imag':ac['signals'][0]['imag'],
        'reference_real':ac['signals'][1]['real'],'reference_imag':ac['signals'][1]['imag'],
        'response_unit':'V','reference_unit':'V','response_kind':'transfer'}
    dc_args={'backend':'spiceowl','devices':op['operating_points'],'required_devices':['N1','P1'],
        'device_contracts':contracts(),'supplies':[{'name':'VDD','voltage_v':1.2,'current_a':.001,'current_sign':'into_dut'}]}
    payload={'schema':'spiceowl.ac-dc-examples.v1','synthetic':True,
        'evidence':'Original synthetic PSF-format records and actual local parser/algorithms; no simulator execution.',
        'ac':{'source':native_ac(),'dataset':ac,'arguments':args,'result':ac_metrics(**args)},
        'dc':{'source':native_dc(),'dataset':parse_psf_analysis(native_dc(),analysis_kind='dc')},
        'dc_op':{'source':native_op(),'dataset':op,'arguments':dc_args,'result':dc_op_metrics(**dc_args)}}
    Path(path).write_text(json.dumps(payload,indent=2,allow_nan=False)+'\n')


if __name__=='__main__':
    import sys
    if sys.argv[1:]==['--write-examples']:
        write_examples(Path(__file__).resolve().parents[1]/'docs/contracts/ac-dc-analysis.example.json')
    else:unittest.main()

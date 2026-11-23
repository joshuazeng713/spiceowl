"""Original synthetic sources only; no ADC source, PDK, I/O or simulation."""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from spiceowl.experiments import prepare_experiment, parse_sources


def profile():
    return {'schema': 'spiceowl.experiment-model-profile.v1', 'id': 'synthetic_profile',
            'models': {'device_alpha': {'polarity': 'n', 'terminals': ['d','g','s','b']},
                       'device_beta': {'polarity': 'p', 'terminals': ['d','g','s','b']}},
            'corners': {'TT': [{'alias': 'SYNTHETIC_MODEL', 'section': 'typical'}]}}


def sources():
    # Thirty-five synthetic parameters, deliberately unrelated to a private PDK.
    suffix = ' '.join('p%02d=%d' % (i,i+1) for i in range(31))
    return {'synthetic.scs': ('// Authored offline fixture, not a simulation model.\n'
            'simulator lang=spectre\nsubckt Example IN OUT HI LO\n'
            '    A (OUT IN LO LO) device_alpha w=2u l=200n nf=1 multi=1 \\\n'
            '        ' + suffix + '\n'
            '    B (OUT IN HI HI) device_beta w=3u l=200n nf=1 multi=1 \\\n'
            '        ' + suffix + '\nends Example\n').encode()}


def selection():
    return {'mode': 'instances', 'source_id': 'synthetic.scs', 'subcircuit': 'Example',
            'instances': ['A','B'], 'ports': ['IN','OUT','HI','LO']}


def spec(voltage=1.0):
    return {'schema':'spiceowl.experiment-spec.v1', 'recipe':'inverter-tran',
            'roles':{'inputs':['IN'],'outputs':['OUT'],'vdd':'HI','vss':'LO'},
            'supply':{'positive_v':voltage,'negative_v':0.0}, 'temperature_k':300.15,
            'nominal_temperature_k':300.15, 'corner':'TT', 'parameters':{},
            'stimuli':[{'port':'IN','type':'pulse','low_v':0.0,'high_v':voltage,'delay_s':1e-10,
                        'rise_s':1e-11,'fall_s':1e-11,'width_s':4.9e-10,'period_s':1e-9}],
            'loads':[{'port':'OUT','capacitance_f':2e-15}],
            'analysis':{'stop_s':5e-9,'max_step_s':1e-12,'strobe_period_s':1e-12},
            'acceptance':{'required_units':{'time':'s','IN':'V','OUT':'V','HI':'V','LO':'V'},
                          'sample_count':5001,'coverage':{'start_s':0.0,'end_s':5e-9,'tolerance_s':1e-18},
                          'max_sample_gap_s':1.001e-12,'voltage_bounds_v':[-0.05,voltage+0.05],
                          'supply_tolerance_v':1e-6,
                          'logic':{'low_max_v':0.1*voltage,'high_min_v':0.9*voltage,'crossing_v':0.5*voltage,
                                   'windows':[{'kind':kind,'start_s':(k+start)*1e-9,'end_s':(k+end)*1e-9,'minimum_samples':190}
                                              for k in range(5) for kind,start,end in [('input_high_output_low',.25,.45),('input_low_output_high',.75,.95)]]}}}


class ExperimentPreparationTests(unittest.TestCase):
    def prepare(self, **kwargs):
        return prepare_experiment(kwargs.get('sources',sources()),kwargs.get('selection',selection()),
                                  kwargs.get('spec',spec()),kwargs.get('profile',profile()))

    def test_original_35_parameter_bytes_and_ranges_are_preserved(self):
        result = self.prepare()
        self.assertEqual(result['status'],'ready',result['missing'])
        recipe=result['recipe'];original=sources()['synthetic.scs']
        for item in recipe['source']['instances']:
            raw=original[item['byte_start']:item['byte_end']]
            self.assertEqual(raw.decode(),item['original_text'])
            self.assertEqual(hashlib.sha256(raw).hexdigest(),item['sha256'])
            self.assertIn(raw,result['files']['netlist/dut.scs'])
            self.assertEqual(len(item['parameters_literal']),35)
        self.assertEqual(recipe['source']['sources'][0]['sha256'],hashlib.sha256(original).hexdigest())
        self.assertEqual(json.loads(result['files']['recipe.json']),recipe)
        self.assertEqual(result,self.prepare())

    def test_supply_variants_create_new_frozen_conditions_without_resizing_dut(self):
        one, lower=self.prepare(),self.prepare(spec=spec(.9))
        self.assertEqual(lower['status'],'ready',lower['missing'])
        self.assertEqual(one['files']['netlist/dut.scs'],lower['files']['netlist/dut.scs'])
        self.assertNotEqual(one['recipe']['recipe_sha256'],lower['recipe']['recipe_sha256'])
        self.assertNotEqual(one['files']['netlist/input.scs'],lower['files']['netlist/input.scs'])
        self.assertEqual(lower['recipe']['spec']['supply']['positive_v'],.9)

    def test_negative_rail_stimulus_uses_explicit_absolute_voltages(self):
        target=spec();target['supply']={'positive_v':.5,'negative_v':-.5}
        target['stimuli'][0].update(low_v=-.5, high_v=.5)
        target['acceptance']['voltage_bounds_v']=[-.55,.55]
        target['acceptance']['logic'].update(low_max_v=-.4,high_min_v=.4,crossing_v=0)
        result=self.prepare(spec=target)
        self.assertEqual(result['status'],'ready',result['missing'])
        netlist=result['files']['netlist/input.scs']
        self.assertIn(b'V_SWO_NEG (LO 0) vsource dc=-0.5',netlist)
        self.assertIn(b'V_SWO_IN0 (IN LO) vsource type=pulse val0=0 val1=1 ',netlist)
        target['recipe']='prepared-transient';target['acceptance'].pop('logic')
        target['stimuli']=[{'port':'IN','type':'dc','value_v':0.0}]
        self.assertIn(b'V_SWO_IN0 (IN LO) vsource dc=0.5',self.prepare(spec=target)['files']['netlist/input.scs'])

    def test_no_filesystem_subprocess_or_model_access(self):
        with (patch('builtins.open',side_effect=AssertionError('filesystem access')),
              patch('subprocess.run',side_effect=AssertionError('execution'))):
            self.assertEqual(self.prepare()['status'],'ready')

    def test_missing_conditions_are_structured_and_generate_no_files(self):
        incomplete=spec()
        for field in ('supply','temperature_k','corner','loads','stimuli'):
            incomplete.pop(field)
        result=self.prepare(spec=incomplete)
        self.assertEqual(result['status'],'missing')
        self.assertEqual(len(result['missing']),5)
        self.assertEqual(result['files'],{})
        self.assertIsNone(result['recipe'])
        for key in ('roles','acceptance','analysis'):
            incomplete=spec();incomplete[key]={}
            self.assertEqual(self.prepare(spec=incomplete)['status'],'missing')

    def test_model_names_never_infer_polarity_or_terminal_order(self):
        model=profile();model['models']={}
        self.assertEqual(self.prepare(profile=model)['missing'][0]['code'],'model_contract_missing')
        model=profile();model['models']['device_alpha'].pop('polarity')
        self.assertEqual(self.prepare(profile=model)['missing'][0]['code'],'model_contract_missing')
        model=profile();model['models']['device_alpha']['terminals']=['g','d','s','b']
        self.assertEqual(self.prepare(profile=model)['missing'][0]['code'],'inverter_topology')
        model=profile();model['models']['device_alpha']={'kind':'external','terminals':['a','b','c','d']}
        self.assertEqual(self.prepare(profile=model)['missing'][0]['code'],'inverter_model_contract')

    def test_explicit_registered_alternative_terminal_order(self):
        raw=sources()['synthetic.scs'].replace(b'A (OUT IN LO LO)',b'A (LO IN OUT LO)')
        model=profile();model['models']['device_alpha']['terminals']=['s','g','d','b']
        result=self.prepare(sources={'synthetic.scs':raw},profile=model)
        self.assertEqual(result['status'],'ready',result['missing'])

    def test_empty_interfaces_behavior_and_arbitrary_statements_are_not_mos(self):
        for raw in (b'subckt Example IN OUT HI LO\nends Example\n',
                    b'subckt Example IN OUT HI LO\nB (OUT LO) bsource v=V(IN)\nends Example\n',
                    sources()['synthetic.scs'].replace(b'ends Example',b'include "/secret/model"\nends Example')):
            result=self.prepare(sources={'synthetic.scs':raw})
            self.assertEqual(result['status'],'missing')
            self.assertFalse(result['files'])

    def test_include_shell_and_si_string_injection_cannot_reach_generated_netlist(self):
        for value in ('1V','1; shell evil',float('nan'),float('inf'),True):
            changed=spec();changed['supply']['positive_v']=value
            self.assertEqual(self.prepare(spec=changed)['status'],'missing')
        for include in ({'alias':'BAD"\ninclude "/private"','section':None},
                        {'alias':'SAFE','section':'tt; shell evil'}):
            model=profile();model['corners']['TT']=[include]
            self.assertEqual(self.prepare(profile=model)['status'],'missing')
        for raw in (sources()['synthetic.scs']+b'\nshell "echo evil"\n',
                    sources()['synthetic.scs'].replace(b'w=2u',b'w=__import__(os)'),
                    b'include "../escape.scs"\n'+sources()['synthetic.scs']):
            self.assertEqual(self.prepare(sources={'synthetic.scs':raw})['status'],'missing')

    def test_source_variables_need_explicit_recipe_parameters(self):
        raw=sources()['synthetic.scs'].replace(b'w=2u',b'w=width_value')
        result=self.prepare(sources={'synthetic.scs':raw})
        self.assertEqual(result['missing'][0]['field'],'spec.parameters.width_value')
        changed=spec();changed['parameters']={'width_value':2e-6}
        result=self.prepare(sources={'synthetic.scs':raw},spec=changed)
        self.assertEqual(result['status'],'ready',result['missing'])
        self.assertIn(b'w=width_value',result['files']['netlist/dut.scs'])

    def test_cell_closure_includes_reachable_children_once_and_keeps_parameters(self):
        raw=sources()['synthetic.scs']+b'\nsubckt Parent IN OUT HI LO\nX0 (IN OUT HI LO) Example\nends Parent\n'
        chosen={'mode':'cell','source_id':'synthetic.scs','subcircuit':'Parent'}
        changed=spec();changed['recipe']='prepared-transient';changed['acceptance'].pop('logic')
        result=self.prepare(sources={'synthetic.scs':raw},selection=chosen,spec=changed)
        self.assertEqual(result['status'],'ready',result['missing'])
        self.assertEqual([row['subcircuit'] for row in result['source']['closure']],['Example','Parent'])
        self.assertIn(b'X0 (IN OUT HI LO) Example',result['files']['netlist/dut.scs'])
        self.assertEqual(self.prepare(sources={'synthetic.scs':raw},selection=chosen)['status'],'missing')

    def test_duplicate_recursive_or_incomplete_sources_are_rejected(self):
        original=sources()['synthetic.scs']
        for raw in (original+original,original.replace(b'ends Example',b'ends Other'),
                    original.replace(b'ends Example',b''),
                    b'subckt Example IN OUT HI LO\nX0 (IN OUT HI LO) Example\nends Example\n'):
            self.assertEqual(self.prepare(sources={'synthetic.scs':raw},selection={'mode':'cell','source_id':'synthetic.scs','subcircuit':'Example'})['status'],'missing')
        with self.assertRaises(Exception):
            parse_sources({'/absolute/path':original})

    def test_crlf_statement_bytes_are_not_normalized(self):
        raw=sources()['synthetic.scs'].replace(b'\n',b'\r\n')
        result=self.prepare(sources={'synthetic.scs':raw})
        self.assertEqual(result['status'],'ready',result['missing'])
        for item in result['source']['instances']:
            self.assertIn(b'\r\n',item['original_text'].encode())
            self.assertIn(item['original_text'].encode(),result['files']['netlist/dut.scs'])

    def test_invalid_windows_sampling_and_missing_roles_fail_preparation(self):
        cases=[]
        changed=spec();changed['acceptance']['logic']['windows'][0]['start_s']=1.05e-10;cases.append(changed)
        changed=spec();changed['acceptance']['sample_count']=10;cases.append(changed)
        changed=spec();changed['roles']['outputs']=['OTHER'];cases.append(changed)
        changed=spec();changed['acceptance']['logic']['crossing_v']=2;cases.append(changed)
        changed=spec();changed['acceptance']['max_sample_gap_s']=.5e-12;cases.append(changed)
        changed=spec();changed['recipe']='prepared-transient';changed['acceptance'].pop('logic')
        changed['acceptance']['coverage']['start_s']=1e-9;cases.append(changed)
        for changed in cases:
            self.assertEqual(self.prepare(spec=changed)['status'],'missing')


if __name__=='__main__':unittest.main()

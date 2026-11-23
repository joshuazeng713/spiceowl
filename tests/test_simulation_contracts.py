"""Standalone and runtime report schemas retain one strict, local definition."""

import copy
import json
import os
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def embedded_report(report):
    def rebase(value):
        if isinstance(value, list):
            return [rebase(item) for item in value]
        if isinstance(value, dict):
            return {key: '#/$defs/simulation_report' + item[1:]
                    if key == '$ref' and isinstance(item, str) and item.startswith('#/')
                    else rebase(item) for key, item in value.items()}
        return value
    return rebase({key: value for key, value in report.items() if key != '$id'})


class SimulationContractTests(unittest.TestCase):
    def setUp(self):
        self.report_path = ROOT / 'docs/contracts/simulation-report.v1.json'
        self.runtime_path = ROOT / 'docs/contracts/simulation-runtime.v1.json'
        self.report = json.loads(self.report_path.read_text())
        self.runtime = json.loads(self.runtime_path.read_text())

    def test_runtime_embeds_current_report_and_preserves_optional_experiment_descriptor(self):
        self.assertEqual(self.runtime['x-contract-version'], '1.1.0')
        self.assertEqual(self.runtime['$defs']['simulation_report'], embedded_report(self.report))
        self.assertEqual(self.runtime['$defs']['run']['properties']['report'],
                         {'$ref': '#/$defs/simulation_report'})
        self.assertIn('experiments', self.runtime['properties'])
        self.assertNotIn('experiments', self.runtime['required'])
        self.assertFalse(self.runtime['properties']['experiments']['additionalProperties'])

    def test_every_fragment_reference_resolves_without_network(self):
        def visit(node, schema):
            if isinstance(node, dict):
                if '$ref' in node:
                    reference = node['$ref']
                    self.assertTrue(reference.startswith('#/'), reference)
                    target = schema
                    for part in reference[2:].split('/'):
                        target = target[part.replace('~1', '/').replace('~0', '~')]
                    self.assertIsInstance(target, (dict, bool))
                for item in node.values():
                    visit(item, schema)
            elif isinstance(node, list):
                for item in node:
                    visit(item, schema)
        visit(self.report, self.report)
        visit(self.runtime, self.runtime)

    def test_embedded_report_enforces_the_same_positive_scope_constraints(self):
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest('Optional jsonschema package is not installed')
        example = json.loads((ROOT / 'examples/simulation-report.example.json').read_text())
        embedded = {'$schema': self.runtime['$schema'],
                    '$ref': '#/$defs/simulation_report', '$defs': self.runtime['$defs']}
        Draft202012Validator.check_schema(self.runtime)
        Draft202012Validator.check_schema(self.report)
        validators = [Draft202012Validator(self.report), Draft202012Validator(embedded)]
        for validator in validators:
            validator.validate(example)
            for dimension, status in [('operating_qualification', 'passed_within_scope'),
                                      ('dependency_provenance', 'verified_within_scope')]:
                invalid = copy.deepcopy(example)
                invalid[dimension]['status'] = status
                invalid[dimension]['scope'] = {'kind': 'unspecified'}
                self.assertFalse(validator.is_valid(invalid))
            invalid = copy.deepcopy(example)
            invalid['overall_pass'] = True
            self.assertFalse(validator.is_valid(invalid))

    def test_optional_circuitgit_copies_match_byte_for_byte(self):
        external = os.environ.get('CIRCUITGIT_ROOT')
        if not external:
            self.skipTest('Set CIRCUITGIT_ROOT for paired checkout verification')
        external = Path(external)
        self.assertEqual(self.report_path.read_bytes(),
                         (external / 'contracts/spiceowl-simulation-report.v1.json').read_bytes())
        self.assertEqual(self.runtime_path.read_bytes(),
                         (external / 'contracts/spiceowl-runtime.v1.json').read_bytes())


if __name__ == '__main__':
    unittest.main()

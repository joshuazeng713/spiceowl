"""Independent onboarding review using fresh, original offline fixtures only."""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from spiceowl.domain.common import DomainError
from spiceowl.onboarding.pdk_discovery import (
    build_pdk_discovery_script, inspect_netlist, profile_suggestions,
)
from spiceowl.onboarding.ssh_discovery import discover_ssh_hosts
from spiceowl.onboarding.environment import (
    ProbeReply, capture_server_environment, public_environment,
)


SOURCE = b'simulator lang=spectre\nM0 (OUT IN LO LO) synthetic_n w=2u l=180n\n'


class OnboardingBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def no_processes(self):
        stack = contextlib.ExitStack()
        stack.enter_context(patch('subprocess.run', side_effect=AssertionError('No command execution')))
        stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('No command execution')))
        stack.enter_context(patch('os.system', side_effect=AssertionError('No shell evaluation')))
        stack.enter_context(patch('socket.create_connection', side_effect=AssertionError('No network')))
        return stack

    def discover_fixture(self, files, **kwargs):
        models = self.root / 'selected-model-root'
        models.mkdir(exist_ok=True)
        for name, text in files.items():
            path = models / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        program = build_pdk_discovery_script(required_masters=['synthetic_n'], roots=[str(models)],
            source_sha256=hashlib.sha256(SOURCE).hexdigest(), **kwargs)
        output = io.StringIO()
        original_open = os.open
        reads = []
        def scoped_open(path, *args, **options):
            actual = Path(path).resolve()
            self.assertTrue(actual.is_relative_to(models.resolve()), 'Read escaped the selected synthetic root')
            reads.append(actual)
            return original_open(path, *args, **options)
        with self.no_processes(), patch('os.open', side_effect=scoped_open), contextlib.redirect_stdout(output):
            exec(compile(program, '<synthetic-discovery-reader>', 'exec'), {'__name__': '__main__'})
        return json.loads("\n".join(line for line in output.getvalue().splitlines() if not line.startswith("SPICEOWL_DISCOVERY_DIRECTORY_SNAPSHOT "))), reads

    def test_alias_inventory_uses_explicit_config_without_evaluating_routes_or_old_state(self):
        home = self.root / 'new-user'
        config = home / '.ssh' / 'config'
        fragment = config.parent / 'conf.d' / 'selected.conf'
        fragment.parent.mkdir(parents=True)
        fragment.write_text('Host synthetic-c\n  HostName example.invalid\n')
        marker = home / 'must-not-execute'
        secret = config.parent / 'private.key'
        secret.write_text('SYNTHETIC_SECRET_MUST_NOT_BE_READ')
        old = home / '.spiceowl' / 'site.local.json'
        old.parent.mkdir(parents=True)
        old.write_text('Host historical-must-not-appear\n')
        config.write_text('Host synthetic-a synthetic-b *.invalid !excluded\n'
                          '  IdentityFile ' + str(secret) + '\n'
                          '  ProxyCommand touch ' + str(marker) + '\n'
                          'Match exec "touch ' + str(marker) + '"\n'
                          'Include conf.d/*.conf\n')
        allowed = {config, fragment}
        original_open = os.open
        reads = []
        def selected_open(path, *args, **kwargs):
            actual = Path(path)
            self.assertIn(actual, allowed, 'Listing opened an unselected fixture or credential')
            reads.append(actual)
            return original_open(path, *args, **kwargs)
        with self.no_processes(), patch('os.open', side_effect=selected_open):
            result = discover_ssh_hosts(config, user_home=home)
        self.assertEqual([host['alias'] for host in result['hosts']], ['synthetic-a', 'synthetic-b', 'synthetic-c'])
        self.assertEqual(set(reads), allowed)
        self.assertEqual(result['network_operations'], 0)
        self.assertFalse(result['effective_configuration_resolved'])
        self.assertFalse(marker.exists())
        self.assertNotIn('SYNTHETIC_SECRET', json.dumps(result))
        self.assertNotIn('historical-must-not-appear', json.dumps(result))

    def test_netlist_inspection_does_not_open_includes_or_infer_device_semantics(self):
        source = SOURCE + b'include "/unselected/fixture/model.scs"\n'
        with self.no_processes(), patch('builtins.open', side_effect=AssertionError('No file reads')):
            result = inspect_netlist(source)
        self.assertEqual(result['source_sha256'], hashlib.sha256(source).hexdigest())
        self.assertEqual(result['required_masters'], ['synthetic_n'])
        self.assertIsNone(result['instances'][0]['polarity'])
        self.assertIsNone(result['instances'][0]['terminal_semantics'])
        self.assertIn('source_include_not_opened', [issue['code'] for issue in result['issues']])

    def test_dynamic_include_and_outside_root_reference_never_execute_or_read_target(self):
        outside = self.root / 'unselected.scs'
        outside.write_text('model forbidden_master nmos\n')
        marker = self.root / 'must-not-be-created'
        text = ('simulator lang=spectre\nmodel synthetic_n nmos\n'
                'include "' + str(outside) + '"\n'
                'include "$(touch ' + str(marker) + ')"\n')
        result, reads = self.discover_fixture({'toy.scs': text}, search_hints=['$(touch ' + str(marker) + ')'])
        self.assertFalse(result['search_complete'])
        self.assertFalse(result['selection_made'])
        self.assertFalse(result['profile_registered'])
        self.assertNotIn(outside.resolve(), reads)
        self.assertFalse(marker.exists())
        self.assertNotIn('forbidden_master', json.dumps(result))
        self.assertTrue(result['unknown'])

    def test_multiple_candidates_remain_unselected_and_non_executable(self):
        result, _ = self.discover_fixture({
            'first.scs': 'simulator lang=spectre\nmodel synthetic_n nmos\n',
            'second.scs': 'simulator lang=spectre\nmodel synthetic_n nmos\n'})
        suggestions = profile_suggestions(result)
        self.assertGreaterEqual(len(suggestions['candidates']), 2)
        self.assertIsNone(suggestions['selected_candidate_id'])
        self.assertIn('multiple_entry_or_section_candidates', [a['code'] for a in suggestions['ambiguities']])
        self.assertTrue(all(row['ready_to_run'] is False for row in suggestions['candidates']))
        self.assertTrue(all(row['polarity_and_terminal_order_confirmed'] is False for row in suggestions['candidates']))
        self.assertFalse(result['selection_made'])
        self.assertFalse(result['profile_registered'])

    def test_changed_discovery_metadata_cannot_keep_its_old_digest(self):
        result, _ = self.discover_fixture({'toy.scs': 'model synthetic_n nmos\n'})
        result['required_masters'] = ['different_master']
        with self.assertRaises(DomainError):
            profile_suggestions(result)

    def test_dimension_suffix_requires_the_supported_source_language(self):
        for source, expected in [
            ('simulator lang=spice\nM0 OUT IN LO LO synthetic_n w=1M l=180n\n', 1e-3),
            ('simulator lang=spectre\nM0 (OUT IN LO LO) synthetic_n w=1M l=180n\n', 1e6),
            ('M0 (OUT IN LO LO) synthetic_n w=1M l=180n\n', None),
        ]:
            with self.subTest(expected=expected):
                result = inspect_netlist(source)
                self.assertEqual(result['instances'][0]['dimensions']['w']['value_m'], expected)
                self.assertIsNone(result['instances'][0]['terminal_semantics'])

    def test_wrapper_contract_requires_direct_unambiguous_terminal_evidence(self):
        model = ('simulator lang=spectre\nsubckt synthetic_n A B C D\n'
                 'Q0 (B A C D) inner_model w=2u l=180n\n'
                 'model inner_model bsim4 type=1\nends synthetic_n\n')
        result, _ = self.discover_fixture({'toy.scs': model})
        contracts = [m for c in result['entry_candidates'] for m in c['models'] if m['name'] == 'synthetic_n']
        self.assertTrue(contracts)
        self.assertEqual(contracts[0]['polarity'], 'n')
        self.assertEqual(contracts[0]['ordered_terminal_roles'], ['g', 'd', 's', 'b'])
        self.assertEqual(contracts[0]['confidence'], 'verified')
        self.assertFalse(profile_suggestions(result)['candidates'][0]['ready_to_run'])
        complex_model = model.replace('model inner_model', 'Q1 (B A C D) inner_model w=2u l=180n\nmodel inner_model')
        result, _ = self.discover_fixture({'toy.scs': complex_model})
        contracts = [m for c in result['entry_candidates'] for m in c['models'] if m['name'] == 'synthetic_n']
        self.assertTrue(contracts)
        self.assertTrue(all(m['confidence'] != 'verified' for m in contracts))
        self.assertTrue(all(c['ready_to_run'] is False for c in profile_suggestions(result)['candidates']))

    def environment_fixture(self):
        return {'schema': 'spiceowl.server-environment.private.v1', 'probe_kind': 'python',
            'identity': {'hostname': 'synthetic-host', 'username': 'synthetic-user',
                         'home': '/synthetic/private-home'},
            'path': ['/synthetic/private-tools'],
            'environment': [{'name': 'PDK_HOME', 'value': '/synthetic/private-models'}],
            'tools': [{'kind': 'python', 'path': '/synthetic/private-tools/python3',
                       'version': '3.11.4', 'basis': 'probe_interpreter', 'usable': 'probe_executed'}],
            'licenses': [{'name': 'CDS_LIC_FILE', 'value': 'SYNTHETIC_LICENSE_SECRET',
                          'configured': True, 'declared_in_scripts': True,
                          'provenance': [{'basis': 'static_script_reference',
                                          'source_id': 'source-' + 'a' * 20, 'line': 3}]}],
            'setup_candidates': [{'source_id': 'source-' + 'a' * 20, 'path': '/synthetic/private-setup'}],
            'pdk_candidates': [{'path': '/synthetic/private-models', 'exists': True,
                                'basis': 'observed_environment'}],
            'library_roots': [], 'warnings': [{'code': 'source_reference_unresolved'}],
            'resources': {}, 'coverage': {'files_read': 1, 'bytes_read': 10}}

    def test_framed_environment_keeps_license_and_setup_values_private(self):
        config = self.root / 'config'
        config.write_text('Host synthetic-selected synthetic-unselected\n')
        private = self.environment_fixture()
        calls = []
        def transport(argv, script, **kwargs):
            calls.append(argv)
            nonce = re.search(r'__SPICEOWL_ENV_([a-f0-9]+)_BEGIN__', script)[1]
            raw = ('startup SYNTHETIC_LICENSE_SECRET\n__SPICEOWL_ENV_' + nonce + '_BEGIN__\n'
                   + json.dumps(private) + '\n__SPICEOWL_ENV_' + nonce + '_END__\n').encode()
            return ProbeReply(0, raw)
        with self.no_processes():
            result = capture_server_environment('synthetic-selected', config, transport=transport, user_home=self.root)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-2], 'synthetic-selected')
        self.assertNotIn('synthetic-unselected', calls[0])
        encoded = json.dumps(result['public'])
        for value in ['SYNTHETIC_LICENSE_SECRET', '/synthetic/private-home', '/synthetic/private-tools',
                      '/synthetic/private-models', '/synthetic/private-setup']:
            self.assertNotIn(value, encoded)
        self.assertEqual(result['private']['licenses'][0]['value'], 'SYNTHETIC_LICENSE_SECRET')
        self.assertTrue(result['public']['licenses'][0]['configured'])
        self.assertEqual(result['public']['licenses'][0]['usable'], 'unknown')
        self.assertFalse(result['public']['simulation_started'])

    def test_transport_failure_does_not_echo_untrusted_output_or_error_strings(self):
        config = self.root / 'config'
        config.write_text('Host synthetic-selected\n')
        def transport(*args, **kwargs):
            return ProbeReply(255, b'SYNTHETIC_LICENSE_SECRET', error_code='SYNTHETIC_LICENSE_SECRET')
        with self.no_processes():
            result = capture_server_environment('synthetic-selected', config, transport=transport, user_home=self.root)
        self.assertIsNone(result['private'])
        self.assertEqual(result['public']['state'], 'unavailable')
        self.assertNotIn('SYNTHETIC_LICENSE_SECRET', json.dumps(result['public']))

    def test_unknown_wire_tokens_cannot_smuggle_private_values_through_public_fields(self):
        for target in ['warning', 'version', 'tool_basename', 'source_id', 'license_source_id']:
            private = copy.deepcopy(self.environment_fixture())
            secret = 'SYNTHETIC_LICENSE_SECRET'
            if target == 'warning': private['warnings'][0]['code'] = secret
            elif target == 'version': private['tools'][0]['version'] = secret
            elif target == 'tool_basename': private['tools'][0]['path'] = '/synthetic/' + secret
            elif target == 'source_id': private['setup_candidates'][0]['source_id'] = secret
            else: private['licenses'][0]['provenance'][0]['source_id'] = secret
            with self.subTest(target=target):
                try:
                    public = public_environment('synthetic-selected', private)
                except DomainError as error:
                    self.assertNotIn(secret, str(error))
                else:
                    self.assertNotIn(secret, json.dumps(public))


if __name__ == '__main__':
    unittest.main()

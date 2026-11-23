"""Original local fixtures for SSH inventory and static environment discovery."""
import ast
import builtins
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

from spiceowl.domain.common import DomainError
from spiceowl.onboarding.ssh_discovery import discover_ssh_hosts, validate_ssh_target
from spiceowl.onboarding.environment import (REMOTE_PYTHON, ACTIVATION_PYTHON, SETUP_CSH_COMMAND, MAX_OUTPUT_BYTES, ProbeReply,
    build_environment_probe, capture_server_environment, parse_environment_reply,
    activate_observed_environment, probe_server_environment, public_environment, _transport, _build_setup_probe)


def wire(value, nonce):
    return ('untrusted startup banner\n__SPICEOWL_ENV_' + nonce + '_BEGIN__\n' + json.dumps(value)
            + '\n__SPICEOWL_ENV_' + nonce + '_END__\n').encode()


class OnboardingEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / 'home'; self.home.mkdir()
        self.cwd = self.home / 'work'; self.cwd.mkdir()
        self.ssh = self.home / '.ssh'; self.ssh.mkdir()
        self.config = self.ssh / 'config'
        self.config.write_text('Host fixture-a fixture-b\n  Hostname example.invalid\n')
        self.config.chmod(0o600)
        self.bin = self.root / 'bin'; self.bin.mkdir()
        (self.bin / 'spectre').write_text('must never be executed\n'); (self.bin / 'spectre').chmod(0o700)
        self.env = {'HOME': str(self.home), 'SHELL': '/bin/tcsh', 'PATH': str(self.bin),
                    'CDS_LIC_FILE': 'synthetic-license-value-only', 'UNRELATED_SECRET': 'never_collect_this'}
        self.opens = []

    def scan(self, *, python2=False):
        """Run authored scanner functions with fixture-only OS and account IO."""
        namespace = {'__name__': 'offline_scanner_fixture'}
        exec(compile(REMOTE_PYTHON, '<fixed remote scanner>', 'exec'), namespace)
        path = types.SimpleNamespace(**{name: getattr(os.path, name) for name in dir(os.path) if not name.startswith('__')})
        path.realpath = lambda value: value if value.startswith('/etc/') else os.path.realpath(value)
        path.isdir = lambda value: str(value).startswith(str(self.root)) and os.path.isdir(value)
        path.isfile = lambda value: str(value).startswith(str(self.root)) and os.path.isfile(value)
        def mapped(value):
            value = str(value)
            if value.startswith('/etc/'):
                return self.root / 'system-etc' / value[5:]
            if value == '/proc/meminfo': return self.root / 'meminfo'
            if value.startswith(str(self.root) + '/') or value == str(self.root): return Path(value)
            raise AssertionError('Scanner attempted IO outside original fixture: ' + value)
        def opening(value, flags):
            self.opens.append(str(value))
            return os.open(mapped(value), flags)
        def reading(value, *args, **kwargs):
            self.opens.append(str(value))
            return builtins.open(mapped(value), *args, **kwargs)
        observed_os = types.SimpleNamespace(environ=dict(self.env), getcwd=lambda: str(self.cwd), geteuid=lambda: 1234,
            uname=lambda: ('Linux', 'synthetic-server', 'fixture', 'fixture', 'x86_64'),
            path=path, pathsep=':', O_RDONLY=os.O_RDONLY, O_NONBLOCK=os.O_NONBLOCK,
            O_NOFOLLOW=os.O_NOFOLLOW, open=opening, fdopen=os.fdopen, fstat=os.fstat,
            access=lambda target, mode: os.access(mapped(target), mode), X_OK=os.X_OK,
            sysconf=lambda _: 8, getloadavg=lambda: (.5, .25, .1), listdir=lambda value: os.listdir(mapped(value)))
        if not python2:
            observed_os.scandir = lambda value: os.scandir(mapped(value))
        namespace.update(os=observed_os, open=reading,
            pwd=types.SimpleNamespace(getpwuid=lambda _: types.SimpleNamespace(pw_name='synthetic-user', pw_shell='/bin/tcsh')),
            sys=types.SimpleNamespace(executable=str(self.bin / ('python2.7' if python2 else 'python3')),
                                      version_info=(2, 7, 18) if python2 else (3, 12, 9)))
        with patch('subprocess.Popen', side_effect=AssertionError('scanner cannot execute a program')):
            return namespace['collect_environment']()

    def test_ssh_inventory_is_literal_offline_and_follows_bounded_config_includes(self):
        includes = self.ssh / 'config.d'; includes.mkdir()
        (includes / 'lab').write_text('Host fixture-c * !excluded\nIdentityFile ~/.ssh/id_ed25519\n')
        (includes / 'more.conf').write_text('Host=fixture-d\nInclude config\n')
        key = self.ssh / 'id_ed25519'; key.write_text('SYNTHETIC_KEY_MUST_NOT_BE_READ')
        self.config.write_text('Host fixture-a fixture-b\nInclude config.d/*\nMatch exec "should-never-run"\nHost *.invalid\nIdentityFile ~/.ssh/id_ed25519\n')
        real_open = os.open; opened = []
        def guard(path, *args, **kwargs):
            self.assertNotEqual(Path(path), key)
            opened.append(str(path)); return real_open(path, *args, **kwargs)
        with patch('os.open', side_effect=guard), patch('subprocess.Popen', side_effect=AssertionError('no process')):
            result = discover_ssh_hosts(self.config, user_home=self.home)
        self.assertEqual([h['alias'] for h in result['hosts']], ['fixture-a', 'fixture-b', 'fixture-c', 'fixture-d'])
        self.assertEqual(result['files_scanned'], 3)
        self.assertEqual(result['network_operations'], 0)
        self.assertIs(result['credential_directives_followed'], False)
        self.assertIn('include_cycle', {row['code'] for row in result['warnings']})
        self.assertNotIn('SYNTHETIC_KEY', json.dumps(result))

    def test_directory_globs_keys_and_symlinks_are_not_followed(self):
        (self.ssh / 'keys').mkdir()
        key = self.ssh / 'id_rsa'; key.write_text('synthetic key fixture')
        link = self.ssh / 'linked.conf'; link.symlink_to(key)
        self.config.write_text('Host fixture-a\nInclude */*/*.conf\nInclude id_rsa\nInclude linked.conf\n')
        with patch('os.scandir', side_effect=AssertionError('directory globs cannot recurse')):
            result = discover_ssh_hosts(self.config, user_home=self.home)
        self.assertEqual(result['files_scanned'], 1)
        self.assertFalse(result['complete'])
        codes = {row['code'] for row in result['warnings']}
        self.assertTrue({'include_directory_glob_unsupported', 'non_config_include_skipped', 'include_unavailable'} <= codes)

    def test_discovery_config_and_directory_limits_are_explicit(self):
        self.config.write_bytes(b'#' * 65537)
        result = discover_ssh_hosts(self.config, user_home=self.home)
        self.assertFalse(result['complete']); self.assertEqual(result['hosts'], [])
        self.config.write_text('Host fixture-a\nInclude config.d/*\n')
        folder = self.ssh / 'config.d'; folder.mkdir()
        for n in range(70): (folder / ('%03d.conf' % n)).write_text('Host host%d\n' % n)
        result = discover_ssh_hosts(self.config, user_home=self.home)
        self.assertLessEqual(result['files_scanned'], 32)
        self.assertFalse(result['complete'])

    def test_static_source_chains_have_hashes_and_never_execute_or_promote_declarations(self):
        setup = self.root / 'setup.csh'
        setup.write_text('setenv CDS_INST_DIR ' + str(self.root / 'tool-install') + '\n'
                         'setenv LM_LICENSE_FILE SCRIPT_VALUE_NOT_EFFECTIVE\n'
                         'setenv PDK_ROOT ' + str(self.root / 'process-kit') + '\n'
                         'touch ' + str(self.root / 'forbidden-write') + '\n')
        (self.home / '.cshrc').write_text('source ' + str(setup) + '\nsource $NOT_OBSERVED/script.csh\n')
        (self.root / 'process-kit').mkdir()
        (self.root / 'meminfo').write_text('MemTotal: 2000 kB\nMemAvailable: 1200 kB\n')
        private = self.scan()
        self.assertEqual(private['resources']['memory_available_bytes'], 1200 * 1024)
        self.assertEqual(private['identity']['hostname'], 'synthetic-server')
        self.assertFalse((self.root / 'forbidden-write').exists())
        self.assertTrue(any(row['path'] == str(setup) and len(row['sha256']) == 64 for row in private['setup_candidates']))
        self.assertTrue(all(row['executed'] is False for row in private['source_chain']))
        lm = next(row for row in private['licenses'] if row['name'] == 'LM_LICENSE_FILE')
        self.assertTrue(lm['declared_in_scripts']); self.assertFalse(lm['configured']); self.assertIsNone(lm['value'])
        self.assertFalse(any(row['name'] == 'PDK_ROOT' for row in private['environment']))
        self.assertTrue(any(row['basis'] == 'static_script_declaration_not_effective_environment' for row in private['pdk_candidates']))
        public = public_environment('fixture-a', private)
        serialized = json.dumps(public)
        for secret in ('synthetic-license-value-only', 'SCRIPT_VALUE_NOT_EFFECTIVE', 'never_collect_this', str(self.root)):
            self.assertNotIn(secret, serialized)
        self.assertEqual(public['licenses'][0]['usable'], 'unknown')

    def test_cds_lib_env_and_python2_shallow_roots_are_candidate_evidence(self):
        foundry = self.root / 'process-kits'; foundry.mkdir()
        technology = self.home / 'technology'; technology.mkdir()
        (self.cwd / 'pdk-candidates').mkdir()
        lib = self.root / 'library-map'; lib.write_text('DEFINE synthetic_tech ' + str(technology) + '\n')
        (self.cwd / 'cds.lib').write_text('INCLUDE ' + str(lib) + '\n')
        self.env['PDK_ROOT'] = str(foundry)
        for python2 in (False, True):
            with self.subTest(python2=python2):
                private = self.scan(python2=python2)
                self.assertTrue(any(row['path'] == str(foundry) and row['basis'] == 'observed_environment' for row in private['pdk_candidates']))
                self.assertTrue(any(row['path'] == str(technology) for row in private['library_roots']))
                self.assertTrue(any(row['basis'] == 'shallow_directory_name' for row in private['pdk_candidates']))
                self.assertTrue(all(row['qualified'] is False for row in private['pdk_candidates']))
                if python2:
                    self.assertEqual(private['coverage']['directory_listing_method'], 'fixed_root_listdir')

    def test_literal_alias_source_targets_are_static_unexecuted_setup_candidates(self):
        csh = self.root / 'cad_tool.csh'; csh.write_text('setenv CDS_INST_DIR /synthetic/cad-tool\n')
        dot = self.root / 'dot_setup.csh'; dot.write_text('setenv SYNTHETIC_ALIAS_ONLY present\n')
        posix = self.root / 'posix_setup.sh'; posix.write_text('export CDS_INST_DIR=/synthetic/posix-tool\n')
        bare = self.root / 'bare_setup.csh'; bare.write_text('setenv SYNTHETIC_ALIAS_ONLY bare\n')
        (self.home / '.cshrc').write_text("alias CAD 'source " + str(csh) + "'\n"
            + "alias Dot '. " + str(dot) + "'\n"
            + "alias Posix='source " + str(posix) + "'\n"
            + 'alias Bare source ' + str(bare) + '\n')
        for python2 in (False, True):
            private = self.scan(python2=python2)
            references = [row for row in private['source_chain'] if row['basis'] == 'alias_source_reference']
            self.assertEqual({row['alias_name'] for row in references}, {'CAD', 'Dot', 'Posix', 'Bare'})
            self.assertTrue(all(row['executed'] is False for row in references))
            targets = {row['path']: row for row in private['setup_candidates']}
            for target in (csh, dot, posix, bare):
                self.assertEqual(targets[str(target)]['sha256'], hashlib.sha256(target.read_bytes()).hexdigest())
                self.assertFalse(targets[str(target)]['executed_by_probe'])
                self.assertEqual(targets[str(target)]['basis'], 'alias_source_reference')
            self.assertFalse(any(row['name'] == 'CDS_INST_DIR' for row in private['environment']))
            public = public_environment('fixture-a', private)
            self.assertFalse(public['coverage']['scripts_evaluated'])
            self.assertTrue(any(row['basis'] == 'alias_source_reference' for row in public['setup_candidates']))
            self.assertNotIn(str(self.root), json.dumps(public))

    def test_quoted_fake_sources_and_dynamic_aliases_are_not_followed(self):
        denied = self.root / 'do_not_read.csh'; denied.write_text('setenv CDS_INST_DIR /synthetic/never\n')
        accepted = self.root / 'accepted.csh'; accepted.write_text('setenv CDS_INST_DIR /synthetic/accepted\n')
        self.env['FIXTURE_KNOWN'] = str(denied)
        path = str(denied)
        (self.home / '.cshrc').write_text('\n'.join([
            "echo 'ignored; source " + path + "; still quoted'",
            'printf "%s" "; source ' + path + '; quoted"',
            "alias Quoted 'echo source " + path + "'",
            "alias Params 'source $FIXTURE_KNOWN'",
            "alias Command 'source $(printf " + path + ")'",
            "alias Home 'source ~/do_not_read.csh'",
            "alias Args 'source " + path + " extra'",
            "alias Patterns 'source " + path + "*'",
            "alias History 'source " + path + " !*'",
            '# source ' + path,
            "echo 'quoted; source " + path + "'; source " + str(accepted),
        ]) + '\n')
        private = self.scan()
        self.assertNotIn(str(denied), self.opens)
        self.assertIn(str(accepted), self.opens)
        self.assertEqual({row['target_path'] for row in private['source_chain']}, {str(accepted)})
        self.assertTrue(all(row['executed'] is False for row in private['source_chain']))

    def test_multi_command_aliases_only_read_independent_literal_source_statements(self):
        alpha = self.root / '.cshrc.synthetic.alpha'; alpha.write_text('setenv CDS_INST_DIR /synthetic/alpha\n')
        beta = self.root / '.cshrc.synthetic.beta'; beta.write_text('setenv SYNTHETIC_FLAG beta\n')
        denied = self.root / 'quoted-denied.csh'; denied.write_text('setenv CDS_INST_DIR /synthetic/denied\n')
        p = str(denied)
        (self.home / '.cshrc').write_text("alias Cad 'source " + str(alpha) + ';source ' + str(beta) + ";virtuoso &'\n"
            + "alias Wave 'source " + str(beta) + ";ocean'\n"
            + "alias Quoted 'echo \"source " + p + '; still quoted\"; source ' + str(alpha) + "'\n"
            + "alias Pipeline 'source " + p + " | echo skipped'\n"
            + "alias Conditional 'source " + p + " && echo skipped'\n"
            + "alias Substitution 'echo $(printf skipped); source " + p + "'\n"
            + "alias Backtick 'echo `printf skipped`; source " + p + "'\n")
        private = self.scan()
        self.assertNotIn(str(denied), self.opens)
        rows = [row for row in private['source_chain'] if row['basis'] == 'alias_source_reference']
        self.assertEqual([(row['alias_name'], row['target_path']) for row in rows],
            [('Cad', str(alpha)), ('Cad', str(beta)), ('Wave', str(beta)), ('Quoted', str(alpha))])
        self.assertTrue(all(row['executed'] is False for row in rows))
        self.assertTrue(all(row['evaluated'] is False for row in private['scripts']))
        self.assertTrue(any(row['path'] == str(alpha) and row['shell_kind'] == 'csh' for row in private['setup_candidates']))
        self.assertFalse(any(row['name'] == 'CDS_INST_DIR' for row in private['environment']))
        self.assertFalse(private['coverage']['scripts_evaluated'])

    def test_setup_labels_show_only_bounded_printable_observed_basenames(self):
        private = self.scan()
        private['setup_candidates'] = [
            {'source_id': 'source-' + 'a' * 20, 'path': '/synthetic/private-parent/.cshrc.cadence.FIXTURE'},
            {'source_id': 'source-' + 'b' * 20, 'path': '/synthetic/private-parent/' + 'x' * 250 + '\n\x00\x1b'},
            {'source_id': 'source-' + 'c' * 20, 'path': None},
        ]
        public = public_environment('fixture-a', private)
        labels = [row['label'] for row in public['setup_candidates']]
        self.assertEqual(labels, ['.cshrc.cadence.FIXTURE', 'x' * 240, 'Observed setup script'])
        self.assertNotIn('private-parent', json.dumps(public))
        self.assertTrue(all(len(label) <= 240 and all(char.isprintable() for char in label) for label in labels))

    def test_alias_reference_chains_keep_existing_depth_file_and_sensitive_path_budgets(self):
        for index in range(9):
            (self.root / ('alias%d.csh' % index)).write_text("alias Next 'source " + str(self.root / ('alias%d.csh' % (index + 1))) + "'\n")
        key = self.ssh / 'private.csh'; key.write_text('synthetic do not read')
        large = self.root / 'large-alias.csh'; large.write_bytes(b'x' * 65537)
        (self.home / '.cshrc').write_text("alias Chain 'source " + str(self.root / 'alias0.csh') + "'\n"
            + "alias Private 'source " + str(key) + "'\n"
            + "alias Large 'source " + str(large) + "'\n")
        private = self.scan()
        self.assertNotIn(str(key), self.opens)
        self.assertNotIn(str(self.root / 'alias7.csh'), self.opens)
        self.assertLessEqual(private['coverage']['files_read'], 32)
        self.assertLessEqual(private['coverage']['bytes_read'], 524288)
        codes = {row['code'] for row in private['warnings']}
        self.assertIn('source_limit', codes)
        self.assertIn('source_byte_limit', codes)

    def test_source_depth_byte_sensitive_and_model_boundaries_report_gaps(self):
        for index in range(9):
            (self.root / ('source%d.csh' % index)).write_text('source ' + str(self.root / ('source%d.csh' % (index+1))) + '\n')
        (self.root / 'large.csh').write_bytes(b'x' * 65537)
        private_key = self.ssh / 'id_rsa'; private_key.write_text('fixture key')
        model = self.root / 'model.scs'; model.write_text('fixture model must not be read')
        (self.home / '.cshrc').write_text('\n'.join('source ' + str(path) for path in
            (self.root / 'source0.csh', self.root / 'large.csh', private_key, model)) + '\n')
        result = self.scan()
        codes = {row['code'] for row in result['warnings']}
        self.assertIn('source_limit', codes); self.assertIn('source_byte_limit', codes)
        self.assertIn('model_or_design_source_skipped', codes)
        self.assertNotIn(str(private_key), self.opens); self.assertNotIn(str(model), self.opens)
        self.assertLessEqual(result['coverage']['files_read'], 32)
        self.assertLessEqual(result['coverage']['bytes_read'], 524288)

    def test_capture_uses_only_selected_alias_fixed_script_and_preserves_private_license(self):
        private = self.scan(); calls = []
        def transport(argv, script, **bounds):
            calls.append((argv, script, bounds))
            nonce = re.search(r'__SPICEOWL_ENV_([a-f0-9]+)_BEGIN__', script)[1]
            return ProbeReply(0, wire(private, nonce))
        result = capture_server_environment('fixture-b', self.config, user_home=self.home, transport=transport)
        argv = calls[0][0]
        self.assertEqual(argv[-3:], ['--', 'fixture-b', '/bin/sh -s'])
        self.assertEqual(argv[argv.index('-F') + 1], str(self.config))
        self.assertIn('StrictHostKeyChecking=yes', argv)
        self.assertIn('PermitLocalCommand=no', argv)
        self.assertEqual(result['private']['licenses'][0]['value'], 'synthetic-license-value-only')
        self.assertNotIn('synthetic-license-value-only', json.dumps(result['public']))
        self.assertNotIn('private', probe_server_environment('fixture-b', self.config, user_home=self.home, transport=transport))
        manual = probe_server_environment('operator@example.invalid', self.config, user_home=self.home, transport=transport)
        self.assertEqual(manual['selection_basis'], 'explicit_target')
        self.assertEqual(calls[-1][0][-2], 'operator@example.invalid')
        self.assertEqual(len(calls), 3)

    def test_explicit_targets_accept_alias_or_user_host_without_options(self):
        for value in ('fixture-a', 'operator@example.invalid', 'operator@192.0.2.7'):
            self.assertEqual(validate_ssh_target(value), value)
        for value in ('-Fother', 'operator@example.invalid command', 'operator@host:22', 'operator@[::1]',
                      'operator@@host', 'operator@host;evil', 'operator@256.1.1.1', 'operator@-option'):
            with self.subTest(value=value), self.assertRaises(DomainError): validate_ssh_target(value)

    def test_bootstrap_without_python_retains_presence_and_explicit_static_scan_gap(self):
        nonce = 'a' * 16
        values = {'hostname': 'synthetic-server', 'username': 'synthetic-user', 'home': '/synthetic/home',
                  'cwd': '/synthetic/home', 'shell': '/bin/csh', 'path': '/synthetic/bin',
                  'spectre': '/synthetic/bin/spectre', 'CDS_LIC_FILE': 'fixture-license', 'LM_LICENSE_FILE': ''}
        body = 'bootstrap\t1\n' + ''.join(name + '\t' + value.encode().hex() + '\n' for name, value in values.items())
        body += 'cpu_count\t8\nmemory_available_bytes\t12345\n'
        raw = ('__SPICEOWL_ENV_' + nonce + '_BEGIN__\n' + body + '__SPICEOWL_ENV_' + nonce + '_END__\n').encode()
        private = parse_environment_reply(raw, nonce); public = public_environment('fixture-a', private)
        self.assertEqual(public['state'], 'partial')
        self.assertTrue(public['licenses'][0]['configured'])
        self.assertEqual(public['licenses'][0]['usable'], 'unknown')
        self.assertNotIn('fixture-license', json.dumps(public))
        self.assertEqual(public['resources']['cpu_count'], 8)
        self.assertEqual(private['licenses'][0]['value'], 'fixture-license')

    def test_framing_json_and_untrusted_failure_text_are_never_public(self):
        nonce = 'b' * 16; private = self.scan()
        for raw in (wire(private, nonce) + wire(private, nonce), b'login printed secret', b'x' * (MAX_OUTPUT_BYTES + 1)):
            with self.assertRaises(DomainError): parse_environment_reply(raw, nonce)
        transport = lambda *_a, **_kw: ProbeReply(255, b'PRIVATE_STDOUT', 'PRIVATE_ERROR_CODE')
        result = probe_server_environment('fixture-a', self.config, user_home=self.home, transport=transport)
        self.assertEqual(result['state'], 'unavailable')
        self.assertNotIn('PRIVATE_', json.dumps(result))

    def test_fixed_remote_code_and_posix_bootstrap_parse_without_running_commands(self):
        ast.parse(REMOTE_PYTHON)
        result = subprocess.run(['/bin/sh', '-n'], input=build_environment_probe('c' * 16).encode(), capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertNotIn('eval ', build_environment_probe('c' * 16))

    def setup_capture(self):
        setup = self.root / 'setup.csh'
        setup.write_text('setenv CDS_INST_DIR /synthetic/tool\n')
        (self.home / '.cshrc').write_text('source ' + str(setup) + '\n')
        private = self.scan()
        def transport(argv, script, **bounds):
            nonce = re.search(r'__SPICEOWL_ENV_([a-f0-9]+)_BEGIN__', script)[1]
            return ProbeReply(0, wire(private, nonce))
        capture = capture_server_environment('fixture-a', self.config, transport=transport, user_home=self.home)
        sid = capture['private']['setup_candidates'][0]['source_id']
        return setup, capture, sid

    def test_observed_shell_syntax_is_classified_without_evaluation(self):
        namespace = {'__name__': 'synthetic_scanner'}
        exec(compile(REMOTE_PYTHON, '<fixed observer>', 'exec'), namespace)
        classify = namespace['setup_shell']
        for path, body, kind in (('/synthetic/setup.csh', 'setenv PATH /synthetic/bin', 'csh'),
                                  ('/synthetic/custom', 'setenv CDS_INST_DIR /synthetic/tool', 'csh'),
                                  ('/synthetic/.bashrc', 'export CDS_INST_DIR=/synthetic/tool', 'posix'),
                                  ('/synthetic/setup.csh', '#!/bin/sh\nexport X=1', 'unknown'),
                                  ('/synthetic/setup.csh', '#!/usr/bin/python\nprint(1)', 'unknown'),
                                  ('/synthetic/custom', '# only a comment', 'unknown')):
            self.assertEqual(classify(path, body)[0], kind)

    def test_explicit_activation_uses_current_capture_binding_and_projects_safe_evidence(self):
        setup, capture, sid = self.setup_capture()
        observed = copy.deepcopy(capture['private'])
        observed['coverage']['scripts_evaluated'] = True
        observed['activation'] = {'selected_source_id': sid, 'selected_source_sha256': hashlib.sha256(setup.read_bytes()).hexdigest(),
            'shell': '/bin/csh', 'source_method': 'verified_bytes_stdin'}
        observed['tools'][0].update(path='/synthetic/activated/bin/spectre', basis='selected_setup_PATH')
        calls = []
        def transport(argv, script, **bounds):
            calls.append((argv, script, bounds))
            return ProbeReply(0, wire(observed, 'd' * 32))
        with patch('spiceowl.onboarding.environment.secrets.token_hex', return_value='d' * 32):
            result = activate_observed_environment('fixture-a', self.config, capture, sid, transport=transport, user_home=self.home)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][-3:], ['--', 'fixture-a', '/bin/sh -s'])
        self.assertTrue(result['public']['coverage']['scripts_evaluated'])
        self.assertTrue(result['public']['setup_candidates'][0]['executed_by_probe'])
        self.assertFalse(result['public']['simulator_invoked_by_probe'])
        self.assertIsNone(result['public']['simulation_started'])
        self.assertEqual(result['public']['activation']['script_side_effects'], 'not_audited')
        self.assertEqual(result['public']['tools'][0]['basis'], 'selected_setup_PATH')
        self.assertNotIn(str(setup), json.dumps(result['public']))
        self.assertNotIn('synthetic-license-value-only', json.dumps(result['public']))
        self.assertEqual(result['private']['activation']['parent_probe_id'], capture['private']['capture_binding']['probe_id'])

    def test_activation_rejects_unknown_ambiguous_mismatched_and_unsupported_evidence_without_transport(self):
        _, capture, sid = self.setup_capture()
        invalid = []
        changed = copy.deepcopy(capture); changed['private']['setup_candidates'].append(changed['private']['setup_candidates'][0]); invalid.append(changed)
        changed = copy.deepcopy(capture); changed['private']['setup_candidates'][0]['sha256'] = 'f' * 64; invalid.append(changed)
        changed = copy.deepcopy(capture); changed['private']['setup_candidates'][0]['shell_kind'] = 'posix'; invalid.append(changed)
        changed = copy.deepcopy(capture); changed['private']['capture_binding']['target'] = 'fixture-b'; invalid.append(changed)
        changed = copy.deepcopy(capture); changed['private'].pop('capture_binding'); invalid.append(changed)
        changed = copy.deepcopy(capture); changed['private']['activation'] = {'selected_source_id': sid}; invalid.append(changed)
        with patch('spiceowl.onboarding.environment._transport', side_effect=AssertionError('no activation allowed')):
            for supplied in invalid:
                result = activate_observed_environment('fixture-a', self.config, supplied, sid, user_home=self.home)
                self.assertEqual(result['public']['state'], 'missing')
                self.assertFalse(result['public']['coverage']['scripts_evaluated'])
            result = activate_observed_environment('fixture-a', self.config, capture, 'source-' + '0' * 20, user_home=self.home)
            self.assertEqual(result['public']['error']['code'], 'setup_selection_missing')
            self.config.write_text('Host fixture-a\nHostname changed.invalid\n')
            result = activate_observed_environment('fixture-a', self.config, capture, sid, user_home=self.home)
            self.assertEqual(result['public']['error']['code'], 'setup_capture_mismatch')

    def test_activation_failures_are_bounded_and_never_claim_no_script_side_effects(self):
        _, capture, sid = self.setup_capture()
        for status, code in ((72, 'setup_shell_unavailable'), (73, 'setup_activation_failed'),
                             (74, 'setup_source_changed'), (75, 'setup_python_unavailable'), (76, 'setup_target_mismatch')):
            result = activate_observed_environment('fixture-a', self.config, capture, sid, user_home=self.home,
                transport=lambda *_a, **_kw: ProbeReply(status, b'PRIVATE_SCRIPT_ERROR'))
            self.assertEqual(result['public']['error']['code'], code)
            self.assertIsNone(result['private'])
            self.assertIsNone(result['public']['coverage']['scripts_evaluated'])
            self.assertNotIn('PRIVATE_SCRIPT_ERROR', json.dumps(result))

    @unittest.skipUnless(os.path.isfile('/bin/csh'), 'Optional csh fixture requires /bin/csh')
    def test_activation_sources_exact_hashed_fixture_bytes_and_observes_only_after_setup(self):
        # Execute only an original environment-assignment fixture, with a tiny
        # synthetic observer. Never run the production filesystem scanner here.
        setup = self.root / "synthetic setup 'quoted'.csh"
        setup.write_text('echo fixture_setup_banner\nsetenv SYNTHETIC_SETUP ready\nset argv = ( changed )\nalias exec false\n')
        sid = 'source-' + hashlib.sha256(str(setup).encode()).hexdigest()[:20]
        selected = {'path': str(setup), 'sha256': hashlib.sha256(setup.read_bytes()).hexdigest(), 'source_id': sid,
            'nonce': 'e' * 32, 'identity': {'hostname': 'fixture-host', 'username': 'fixture-user', 'uid': 1234}}
        namespace = {'__name__': 'synthetic_activation'}
        exec(compile(ACTIVATION_PYTHON, '<fixed activation>', 'exec'), namespace)
        observed_os = types.SimpleNamespace(**{name: getattr(os, name) for name in dir(os) if not name.startswith('__')})
        observed_os.geteuid = lambda: 1234
        observed_os.uname = lambda: ('Linux', 'fixture-host', '', '', '')
        namespace['os'] = observed_os
        namespace['pwd'] = types.SimpleNamespace(getpwuid=lambda _: types.SimpleNamespace(pw_name='fixture-user'))
        original = subprocess.Popen; outputs = []; calls = []
        def launch(argv, **kwargs):
            calls.append(argv)
            process = original(argv, **kwargs, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={'PATH': '/usr/bin:/bin'})
            class Capture:
                def communicate(self, raw):
                    stdout, stderr = process.communicate(raw, timeout=5)
                    outputs.append((raw, stdout, stderr)); self.returncode = process.returncode
            return Capture()
        namespace['subprocess'] = types.SimpleNamespace(Popen=launch, PIPE=subprocess.PIPE)
        synthetic = ("import os\ndef collect_environment():\n return {'schema':'spiceowl.server-environment.private.v1',"
            "'probe_kind':'python','coverage':{},'tools':[],'licenses':[],'scripts':[],'setup_candidates':[],"
            "'fixture_setup':os.environ.get('SYNTHETIC_SETUP')}\n")
        status = namespace['activate_setup'](selected, synthetic, SETUP_CSH_COMMAND)
        self.assertEqual(status, 0, outputs)
        self.assertEqual(outputs[0][0], setup.read_bytes())
        self.assertEqual(calls[0][:3], ['/bin/csh', '-f', '-c'])
        self.assertNotIn('@SPICEOWL_FIXED_', calls[0][3])
        self.assertNotIn(str(setup), calls[0][3])
        result = parse_environment_reply(outputs[0][1], selected['nonce'])
        self.assertEqual(result['fixture_setup'], 'ready')
        self.assertTrue(result['coverage']['scripts_evaluated'])
        setup.write_text('setenv SYNTHETIC_SETUP modified\n')
        self.assertEqual(namespace['activate_setup'](selected, synthetic, SETUP_CSH_COMMAND), 74)
        self.assertEqual(len(calls), 1)

    def test_activation_bootstrap_is_fixed_parseable_program_text(self):
        setup, capture, sid = self.setup_capture()
        selected = {'source_id': sid, 'path': str(setup), 'sha256': hashlib.sha256(setup.read_bytes()).hexdigest(),
            'identity': capture['private']['identity']}
        program = _build_setup_probe('f' * 32, selected)
        result = subprocess.run(['/bin/sh', '-n'], input=program.encode(), capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        driver = program.split("<<'SPICEOWL_FIXED_ACTIVATION'\n", 1)[1].rsplit('\nSPICEOWL_FIXED_ACTIVATION', 1)[0]
        ast.parse(driver)
        self.assertIn(repr(REMOTE_PYTHON), driver)
        self.assertNotIn('eval ', program)

    def test_whole_transport_bounds_cover_banner_stderr_and_deadline(self):
        # These are local fixture processes, never SSH. _transport accepts argv
        # only internally; the public probe supplies a fixed SSH argument list.
        python = os.path.realpath(os.sys.executable)
        with self.assertRaises(DomainError) as error:
            _transport([python, '-c', 'import sys;sys.stderr.write("x"*10000)'], '', max_output=1024)
        self.assertEqual(error.exception.code, 'environment_probe_output_limit')
        with self.assertRaises(DomainError) as error:
            _transport([python, '-c', 'import time;time.sleep(1)'], '', timeout=.05)
        self.assertEqual(error.exception.code, 'environment_probe_timeout')


if __name__ == '__main__':
    unittest.main()

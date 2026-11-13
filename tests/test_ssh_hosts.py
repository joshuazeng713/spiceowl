"""Offline SSH protocol-peer tests. No test contacts an SSH host or CAD tool."""

from copy import deepcopy
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from spiceowl.adapters.hosts import probe_host, validate_host_profile
from spiceowl.adapters.executors import ssh
from spiceowl.adapters.simulators.probes import parse_version, version_arguments
from spiceowl.domain.common import DomainError


_REAL_POPEN = subprocess.Popen


def profile(tools=None):
    return {
        "schema": "spiceowl.host.v1", "host_id": "example-lab", "label": "Example host",
        "ssh_target": "example-route", "expected_host": "sim-host", "expected_user": "researcher",
        "tools": tools if tools is not None else [
            {"kind": "spectre", "executable": "/opt/example/spectre/bin/spectre"},
            {"kind": "virtuoso", "executable": "/opt/example/virtuoso/bin/virtuoso"},
        ],
    }


def transcript(nonce, *, host="sim-host", user="researcher", banner=None, status=0):
    def marker(name):
        return f"__SPICEOWL_{nonce}_{name}__"
    text = "\n".join((marker("IDENTITY_BEGIN"), host, user, marker("IDENTITY_END"))) + "\n"
    if banner is not None:
        text += "\n".join((marker("TOOL_BEGIN"), banner, marker("TOOL_END"),
                           marker("STATUS_BEGIN"), str(status), marker("STATUS_END"))) + "\n"
    return text.encode()


class FakeSSH:
    """Replace the SSH executable with finite local Python protocol peers."""

    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []
        self.processes = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        reply = next(self.replies)
        nonce = re.search(r"__SPICEOWL_([0-9a-f]{32})_", argv[-1]).group(1)
        if "source" in reply:
            source = reply["source"]
        else:
            stdout = reply.get("stdout")
            if callable(stdout):
                stdout = stdout(nonce)
            if stdout is None:
                stdout = transcript(nonce, **{key: reply[key] for key in
                                    ("host", "user", "banner", "status") if key in reply})
            stderr = reply.get("stderr", b"")
            source = (f"import sys; sys.stdout.buffer.write({stdout!r}); "
                      f"sys.stderr.buffer.write({stderr!r}); sys.exit({reply.get('returncode', 0)})")
        process = _REAL_POPEN([sys.executable, "-c", source], **kwargs)
        self.processes.append(process)
        return process


class HostProfileTests(unittest.TestCase):
    def test_v2_preserves_v1_route_and_adds_exactly_hostname(self):
        original = profile()
        self.assertEqual(validate_host_profile(original), original)
        data = {**original, "schema": "spiceowl.host.v2", "ssh_hostname": "compute-02.example.test"}
        normalized = validate_host_profile(data)
        self.assertEqual(normalized, data)
        self.assertEqual(set(normalized), set(original) | {"ssh_hostname"})
        self.assertEqual(normalized["ssh_target"], original["ssh_target"])
        invalid = {**original, "ssh_hostname": "compute-02.example.test"}
        with self.assertRaises(DomainError):
            validate_host_profile(invalid)

    def test_v2_hostname_injection_and_invalid_dns_are_rejected(self):
        bad_names = (None, "", True, [], "a" * 254, "a" * 64 + ".test", ".host", "host.",
                     "a..b", "-host", "host-", "host.-test", "host_2", "host:22", "[::1]",
                     "user@host", "ssh://host", "host -oProxyCommand=evil", "host;id", "a\nb",
                     "host$(id)", "host`id`", "host'", 'host"', "host!", "host/other", "*", "主机")
        with patch.object(ssh.subprocess, "Popen") as popen:
            for hostname in bad_names:
                data = {**profile(), "schema": "spiceowl.host.v2", "ssh_hostname": hostname}
                with self.subTest(hostname=hostname), self.assertRaises(DomainError):
                    probe_host(data)
            popen.assert_not_called()

    def test_validation_is_strict_pure_and_json_compatible(self):
        data = profile()
        before = deepcopy(data)
        with patch.object(ssh.subprocess, "Popen", side_effect=AssertionError("No validation IO")):
            normalized = validate_host_profile(data)
        self.assertEqual(data, before)
        self.assertEqual(normalized, json.loads(json.dumps(data)))
        normalized["tools"][0]["executable"] = "/changed/spectre"
        self.assertEqual(data, before)
        self.assertEqual(validate_host_profile(profile([]))["tools"], [])

    def test_missing_extra_and_non_object_profiles(self):
        for value in (None, [], "profile", 1, True):
            with self.subTest(value=value), self.assertRaises(DomainError):
                validate_host_profile(value)
        for key in profile():
            data = profile()
            del data[key]
            with self.subTest(missing=key), self.assertRaises(DomainError):
                validate_host_profile(data)
        for key in ("password", "private_key", "command", "options", "env", "setup_script",
                    "url", "example_only", "created_at", "last_probe"):
            data = profile()
            data[key] = "sensitive-value"
            with self.subTest(extra=key), self.assertRaises(DomainError) as error:
                validate_host_profile(data)
            self.assertNotIn("sensitive-value", str(error.exception))

    def test_input_injection_is_rejected_before_any_ssh(self):
        payloads = ["-oProxyCommand=evil", "user@host", "ssh://host", "host:22", "a b",
                    "a;touch", "a$(id)", "a`id`", "a\nb", "a\rb", "a'b", 'a"b',
                    "a!b", "a|b", "a&b", "a/b", "a\\b", "a\x00b", "*", "a..b"]
        with patch.object(ssh.subprocess, "Popen") as popen:
            for field in ("ssh_target", "expected_host", "expected_user"):
                for payload in payloads:
                    data = profile()
                    data[field] = payload
                    with self.subTest(field=field, payload=payload), self.assertRaises(DomainError):
                        probe_host(data)
            popen.assert_not_called()

    def test_invalid_scalar_values(self):
        cases = {
            "schema": [None, "spiceowl.host.v2", {}, True],
            "host_id": [None, "", "has space", "x" * 97],
            "label": [None, "", " ", "x" * 81, "line\nbreak", "\x1b[31m"],
            "ssh_target": [None, True, "", "x" * 97, "host."],
            "expected_host": [None, [], "x" * 254],
            "expected_user": [None, {}, "x" * 65],
            "tools": [None, {}, "spectre", [None], profile()["tools"] * 2],
        }
        for field, values in cases.items():
            for value in values:
                data = profile()
                data[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(DomainError):
                    validate_host_profile(data)

    def test_tools_are_fixed_unique_and_absolute(self):
        bad_tools = [
            {"kind": "ocean", "executable": "/opt/example/ocean"},
            {"kind": "shell", "executable": "/bin/sh"},
            {"kind": [], "executable": "/opt/example/spectre"},
            {"kind": "spectre", "executable": "/opt/example/spectre", "arguments": ["input.scs"]},
        ]
        for path in (None, "spectre", "~/bin/spectre", "/bin/sh", "/bin/spectre -W", "/a/../spectre",
                     "/a/./spectre", "//a/spectre", "/a//spectre", "/a/spectre/", "/a;id/spectre",
                     "/a$(id)/spectre", "/a`id`/spectre", "/a'b/spectre", "/a\nb/spectre",
                     "/a b/spectre", "/" + "x" * 1024 + "/spectre"):
            bad_tools.append({"kind": "spectre", "executable": path})
        for tool in bad_tools:
            with self.subTest(tool=tool), self.assertRaises(DomainError):
                validate_host_profile(profile([tool]))
        duplicate = profile([profile()["tools"][0]] * 2)
        with self.assertRaises(DomainError):
            validate_host_profile(duplicate)


class SSHProbeTests(unittest.TestCase):
    def run_peer(self, replies, data=None):
        peer = FakeSSH(replies)
        with patch.object(ssh.subprocess, "Popen", side_effect=peer):
            result = probe_host(data if data is not None else profile())
        self.assertTrue(all(process.poll() is not None for process in peer.processes))
        return result, peer

    def test_probe_contract_identity_first_fixed_flags_and_redaction(self):
        data = profile()
        original = deepcopy(data)
        replies = [
            {"stdout": lambda nonce: b"locale warning: /fictional/startup\n" + transcript(nonce),
             "stderr": b"private startup: /fictional/credentials should not be returned"},
            {"banner": "warning: /fictional/library\nsub-version 99.1.0.123.isr4"},
            {"banner": "sub-version IC99.1-64b.500.7"},
        ]
        result, peer = self.run_peer(replies, data)
        self.assertEqual(data, original)
        self.assertEqual(set(result), {"host_id", "synthetic", "scope", "observed_at", "transport",
                                       "identity", "tools", "error"})
        self.assertFalse(result["synthetic"])
        self.assertEqual(result["scope"], "connectivity_only")
        self.assertEqual(result["transport"], "reachable")
        self.assertEqual(result["identity"], {"verified": True, "hostname": "sim-host", "username": "researcher"})
        self.assertIsNone(result["error"])
        self.assertEqual(result["tools"], [
            {"kind": "spectre", "status": "available", "version": "99.1.0.123.isr4"},
            {"kind": "virtuoso", "status": "available", "version": "IC99.1-64b.500.7"},
        ])
        serialized = json.dumps(result)
        for forbidden in ("/fictional", "/opt/example", "example-route", "startup", "license", "execution"):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(len(peer.calls), 3)
        self.assertNotIn("/opt/example", peer.calls[0][0][-1])
        for argv, kwargs in peer.calls:
            self.assertEqual(argv[0], "/usr/bin/ssh")
            self.assertEqual(argv[-3:-1], ["--", "example-route"])
            self.assertFalse(kwargs["shell"])
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertTrue(kwargs["start_new_session"])
            for option in ("BatchMode=yes", "StrictHostKeyChecking=yes", "ForwardAgent=no",
                           "PermitLocalCommand=no", "ControlMaster=no", "ControlPath=none",
                           "UpdateHostKeys=no", "AddKeysToAgent=no", "ConnectionAttempts=1",
                           "ClearAllForwardings=yes", "RemoteCommand=none"):
                self.assertIn(option, argv)
            self.assertNotIn("-i", argv)
            self.assertNotIn("-F", argv)
            words = shlex.split(argv[-1])
            self.assertEqual(words[:3], ["exec", "/bin/sh", "-c"])
            self.assertEqual(len(words), 4)
            self.assertNotIn("\n", argv[-1])
            self.assertNotIn("!", argv[-1])
            self.assertIn("/bin/hostname", words[-1])
            self.assertIn("/usr/bin/id -un", words[-1])
        self.assertIn("/opt/example/spectre/bin/spectre -W", shlex.split(peer.calls[1][0][-1])[-1])

    def test_v2_version_probes_override_only_hostname_on_every_request(self):
        data = {**profile(), "schema": "spiceowl.host.v2", "ssh_hostname": "compute-02.example.test"}
        result, peer = self.run_peer([{}, {"banner": "sub-version 99.1.0.123"},
                                     {"banner": "sub-version IC99.1-64b.500.7"}], data)
        self.assertIsNone(result["error"])
        self.assertEqual(len(peer.calls), 3)
        for argv, _ in peer.calls:
            self.assertEqual(argv[-3:-1], ["--", "example-route"])
            self.assertEqual(argv.count("Hostname=compute-02.example.test"), 1)
            self.assertNotIn("-F", argv)
            self.assertNotIn("-i", argv)
            self.assertFalse(any(value.startswith(("UserKnownHostsFile=", "HostKeyAlias=")) for value in argv))

    def test_identity_mismatch_prevents_all_tool_calls(self):
        for reply in ({"host": "wrong-host"}, {"user": "wrong-user"}):
            with self.subTest(reply=reply):
                result, peer = self.run_peer([reply])
                self.assertEqual(len(peer.calls), 1)
                self.assertEqual(result["transport"], "reachable")
                self.assertFalse(result["identity"]["verified"])
                self.assertEqual(result["error"]["code"], "identity_mismatch")
                self.assertTrue(all(tool["status"] == "unknown" for tool in result["tools"]))

    def test_identity_is_rechecked_for_each_tool_and_change_stops(self):
        result, peer = self.run_peer([{}, {"host": "changed-host", "returncode": 72}])
        self.assertEqual(len(peer.calls), 2)
        self.assertEqual(result["error"]["code"], "identity_mismatch")
        self.assertFalse(result["identity"]["verified"])
        self.assertEqual(result["identity"]["hostname"], "changed-host")
        self.assertTrue(all(tool["status"] == "unknown" for tool in result["tools"]))

    def test_duplicate_and_invalid_identity_markers_fail_closed(self):
        responses = [
            lambda nonce: transcript(nonce) + transcript(nonce),
            lambda nonce: transcript(nonce).replace(b"IDENTITY_END", b"IDENTITY_BEGIN"),
            lambda nonce: transcript(nonce) + f"__SPICEOWL_{nonce}_UNKNOWN__\n".encode(),
            lambda nonce: transcript(nonce, host="bad host"),
            lambda nonce: transcript(nonce, user="bad\nuser"),
            lambda nonce: transcript("f" * 32),
            lambda nonce: b"unframed host and user",
        ]
        for output in responses:
            with self.subTest(output=output):
                result, peer = self.run_peer([{"stdout": output}])
                self.assertEqual(len(peer.calls), 1)
                self.assertFalse(result["identity"]["verified"])
                self.assertEqual(result["error"]["code"], "invalid_probe_response")

    def test_duplicate_tool_markers_fail_and_stop_remaining_probes(self):
        def output(nonce):
            return transcript(nonce, banner="sub-version 99.1.0.123") + f"__SPICEOWL_{nonce}_TOOL_END__\n".encode()
        result, peer = self.run_peer([{}, {"stdout": output}])
        self.assertEqual(len(peer.calls), 2)
        self.assertEqual(result["error"]["code"], "invalid_probe_response")
        self.assertEqual(result["transport"], "unknown")
        self.assertFalse(result["identity"]["verified"])
        self.assertEqual(result["tools"][0]["status"], "unknown")

    def test_wrong_banner_does_not_report_available_or_retry(self):
        result, peer = self.run_peer([{}, {"banner": "sub-version IC99.1-64b.500.7"},
                                     {"banner": "sub-version IC99.1-64b.500.7"}])
        self.assertEqual(len(peer.calls), 3)
        self.assertEqual(result["tools"][0]["status"], "unknown")
        self.assertIsNone(result["tools"][0]["version"])
        self.assertEqual(result["tools"][0]["error"]["code"], "unrecognized_version")
        self.assertEqual(result["tools"][1]["status"], "available")
        self.assertEqual(result["transport"], "reachable")
        self.assertTrue(result["identity"]["verified"])

    def test_tool_exit_is_independent_of_connection_and_banner(self):
        result, _ = self.run_peer([{}, {"banner": "", "status": 127},
                                  {"banner": "sub-version IC99.1-64b.500.7", "status": 1}])
        self.assertEqual(result["transport"], "reachable")
        self.assertTrue(result["identity"]["verified"])
        self.assertIsNone(result["error"])
        self.assertEqual(result["tools"][0]["status"], "unavailable")
        self.assertEqual(result["tools"][1]["status"], "unknown")
        self.assertIsNone(result["tools"][1]["version"])

    def test_optional_ngspice_and_identity_only_profiles(self):
        ngspice = profile([{"kind": "ngspice", "executable": "/usr/bin/ngspice"}])
        result, peer = self.run_peer([{}, {"banner": "******\n** ngspice-99+ : Circuit simulator\n******"}], ngspice)
        self.assertEqual(result["tools"], [{"kind": "ngspice", "status": "available", "version": "99+"}])
        self.assertIn("/usr/bin/ngspice --version", shlex.split(peer.calls[1][0][-1])[-1])
        result, peer = self.run_peer([{}], profile([]))
        self.assertEqual(len(peer.calls), 1)
        self.assertEqual(result["tools"], [])
        self.assertTrue(result["identity"]["verified"])

    def test_connection_error_is_sanitized_without_retry(self):
        result, peer = self.run_peer([{"stdout": b"", "stderr": b"denied /fictional/key", "returncode": 255}])
        self.assertEqual(len(peer.calls), 1)
        self.assertEqual(result["transport"], "unknown")
        self.assertEqual(result["error"]["code"], "ssh_probe_failed")
        self.assertNotIn("/fictional", json.dumps(result))
        with patch.object(ssh.subprocess, "Popen", side_effect=OSError("/fictional/private")) as popen:
            result = probe_host(profile())
        popen.assert_called_once()
        self.assertEqual(result["error"]["code"], "ssh_unavailable")
        self.assertNotIn("/fictional", json.dumps(result))

    def test_safe_ssh_error_categories_never_expose_raw_stderr(self):
        cases = [
            (b"Host key verification failed. /fictional/known_hosts", "ssh_host_key_unverified"),
            (b"REMOTE HOST IDENTIFICATION HAS CHANGED!\nHost key verification failed. /fictional/key", "ssh_host_key_changed"),
            (b"fixture@private-name: Permission denied (publickey).", "ssh_permission_denied"),
            (b"connect to private-name port 123: Connection timed out", "ssh_connection_timeout"),
            (b"Operation timed out /fictional/private", "ssh_connection_timeout"),
            (b"Unrecognized remote error: /fictional/private", "ssh_probe_failed"),
        ]
        for stderr, code in cases:
            with self.subTest(code=code):
                result, peer = self.run_peer([{"stdout": b"", "stderr": stderr, "returncode": 255}])
                self.assertEqual(len(peer.calls), 1)
                self.assertEqual(result["error"]["code"], code)
                self.assertEqual(result["transport"], "unknown")
                self.assertFalse(result["identity"]["verified"])
                serialized = json.dumps(result)
                for raw in ("/fictional", "private-name", "publickey", "port 123"):
                    self.assertNotIn(raw, serialized)
                self.assertLess(len(result["error"]["message"]), 160)

    def test_stderr_hints_do_not_override_success_or_remote_command_failure(self):
        result, _ = self.run_peer([{"stderr": b"Host key verification failed"}], profile([]))
        self.assertIsNone(result["error"])
        self.assertTrue(result["identity"]["verified"])
        result, _ = self.run_peer([{"stderr": b"Permission denied", "returncode": 70}])
        self.assertEqual(result["error"]["code"], "ssh_probe_failed")
        result, _ = self.run_peer([{"stdout": b"Host key verification failed", "returncode": 255}])
        self.assertEqual(result["error"]["code"], "ssh_probe_failed")

    def test_classified_later_ssh_failure_preserves_prior_tool_evidence(self):
        result, peer = self.run_peer([
            {}, {"banner": "sub-version 99.1.0.123"},
            {"stdout": b"", "stderr": b"Host key verification failed", "returncode": 255},
        ])
        self.assertEqual(len(peer.calls), 3)
        self.assertEqual(result["transport"], "unknown")
        self.assertEqual(result["error"]["code"], "ssh_host_key_unverified")
        self.assertEqual(result["tools"][0]["version"], "99.1.0.123")
        self.assertEqual(result["tools"][1]["error"]["code"], "ssh_host_key_unverified")

    def test_stderr_classifier_handles_split_phrases_with_bounded_tail(self):
        hints = ssh._StderrHints()
        for byte in b"prefix /fictional/private\nHOST KEY VERIFICATION FAILED.\n":
            hints.feed(bytes([byte]))
            self.assertLessEqual(len(hints._tail), ssh._STDERR_TAIL_BYTES)
        self.assertEqual(hints.category(), "ssh_host_key_unverified")
        hints.feed(b"REMOTE HOST IDENTIFICATION HAS CHANGED")
        self.assertEqual(hints.category(), "ssh_host_key_changed")
        hints.feed(b"x" * 4096)
        self.assertLessEqual(len(hints._tail), ssh._STDERR_TAIL_BYTES)
        self.assertEqual(hints.category(), "ssh_host_key_changed")

    def test_timeout_reaps_peer_and_returns_unknown(self):
        started = time.monotonic()
        with patch.object(ssh, "COMMAND_TIMEOUT_SECONDS", 0.15):
            result, peer = self.run_peer([{"source": "import time; time.sleep(20)"}])
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(len(peer.calls), 1)
        self.assertEqual(result["error"]["code"], "probe_timeout")
        self.assertEqual(result["transport"], "unknown")
        self.assertFalse(result["identity"]["verified"])

    def test_global_deadline_prevents_starting_ssh(self):
        with patch.object(ssh, "PROBE_TIMEOUT_SECONDS", 0), patch.object(ssh.subprocess, "Popen") as popen:
            result = probe_host(profile())
        popen.assert_not_called()
        self.assertEqual(result["error"]["code"], "probe_timeout")

    def test_large_stdout_and_stderr_are_bounded_and_discarded(self):
        for stream in ("stdout", "stderr"):
            source = f"import sys; sys.{stream}.buffer.write(b'x' * 1000000); sys.{stream}.flush()"
            with self.subTest(stream=stream), patch.object(ssh, "MAX_OUTPUT_BYTES", 512):
                result, peer = self.run_peer([{"source": source}])
            self.assertEqual(len(peer.calls), 1)
            self.assertEqual(result["error"]["code"], "probe_output_limit")
            self.assertLess(len(json.dumps(result)), 1000)

    def test_stderr_counts_towards_combined_budget(self):
        with patch.object(ssh, "MAX_OUTPUT_BYTES", 512):
            result, _ = self.run_peer([{"stdout": b"x" * 300, "stderr": b"y" * 300}])
        self.assertEqual(result["error"]["code"], "probe_output_limit")

    def test_tool_timeout_makes_transport_unknown_and_stops(self):
        with patch.object(ssh, "COMMAND_TIMEOUT_SECONDS", 0.15):
            result, peer = self.run_peer([{}, {"source": "import time; time.sleep(20)"}])
        self.assertEqual(len(peer.calls), 2)
        self.assertEqual(result["transport"], "unknown")
        self.assertEqual(result["error"]["code"], "probe_timeout")
        self.assertFalse(result["identity"]["verified"])
        self.assertEqual(result["tools"][1]["status"], "unknown")

    def test_later_transport_failure_preserves_completed_tool_observations(self):
        failures = [
            {"stdout": b"", "returncode": 255},
            {"stdout": b"unframed output"},
            {"banner": "sub-version IC99.1-64b.500.7", "status": 999},
            {"source": "import sys; sys.stderr.buffer.write(b'x' * 1000000)"},
            {"source": "import time; time.sleep(20)"},
        ]
        for failure in failures:
            with self.subTest(failure=failure), patch.object(ssh, "COMMAND_TIMEOUT_SECONDS", 0.15):
                result, peer = self.run_peer([{}, {"banner": "sub-version 99.1.0.123"}, failure])
            self.assertEqual(len(peer.calls), 3)
            self.assertEqual(result["transport"], "unknown")
            self.assertFalse(result["identity"]["verified"])
            self.assertEqual(result["tools"][0], {"kind": "spectre", "status": "available", "version": "99.1.0.123"})
            self.assertEqual(result["tools"][1]["status"], "unknown")
            self.assertIsNotNone(result["error"])


class VersionAndShellTests(unittest.TestCase):
    def test_only_fixed_flags(self):
        self.assertEqual(version_arguments("spectre"), ("-W",))
        self.assertEqual(version_arguments("virtuoso"), ("-W",))
        self.assertEqual(version_arguments("ngspice"), ("--version",))
        for kind in ("ocean", "bash", None, [], "spectre;id"):
            with self.subTest(kind=kind), self.assertRaises(DomainError):
                version_arguments(kind)

    def test_narrow_recognition_rejects_wrong_ambiguous_and_path_banners(self):
        accepted = [
            ("spectre", "Sub-version 99.1.0.123.isr4", "99.1.0.123.isr4"),
            ("virtuoso", "sub-version IC99.1-64b.ISR4.51", "IC99.1-64b.ISR4.51"),
            ("virtuoso", "sub-version ICADVM99.1-64b.500.17", "ICADVM99.1-64b.500.17"),
            ("ngspice", "** ngspice-99 : Circuit simulator", "99"),
        ]
        for kind, banner, version in accepted:
            self.assertEqual(parse_version(kind, banner), version)
        rejected = [
            ("spectre", "sub-version IC99.1-64b.500.7"),
            ("virtuoso", "sub-version 99.1.0.123"),
            ("ngspice", "version 99"),
            ("spectre", "sub-version 99.1.0.123\nsub-version 99.1.0.123"),
            ("spectre", "sub-version 99.1.0.123\nngspice-99"),
            ("spectre", "sub-version 99.1.0.123 /fictional/key"),
            ("spectre", "sub-version 99.1.0.123;command"),
            ("spectre", "sub-version 99.1.0.123\x1b[0m"),
            ("spectre", "x" * 8193), ("spectre", None),
        ]
        for kind, banner in rejected:
            with self.subTest(kind=kind, banner=banner), self.assertRaises(DomainError):
                parse_version(kind, banner)

    def shell_command(self, host="sim-host", user="researcher"):
        # Only this test replaces the fixed identity utilities with literal
        # fixture outputs, and the CAD executable with harmless /bin/echo.
        script = ssh._tool_script("a" * 32, profile(), {"kind": "spectre", "executable": "/bin/echo"})
        script = script.replace("/bin/hostname 2>/dev/null", f"printf %s {shlex.quote(host)}")
        script = script.replace("/usr/bin/id -un 2>/dev/null", f"printf %s {shlex.quote(user)}")
        return ssh._ssh_argv("example-route", script)[-1]

    def test_remote_shell_guard_precedes_version_invocation(self):
        for host, user, allowed in (("sim-host", "researcher", True),
                                    ("wrong-host", "researcher", False),
                                    ("sim-host", "wrong-user", False)):
            argv = shlex.split(self.shell_command(host, user))
            result = subprocess.run(argv[1:], capture_output=True, timeout=2, check=False)
            self.assertEqual(result.returncode, 0 if allowed else 72)
            self.assertEqual(b"TOOL_BEGIN" in result.stdout, allowed)
            self.assertEqual(b"\n-W\n" in result.stdout, allowed)

    @unittest.skipUnless(Path("/bin/csh").is_file(), "csh not installed")
    def test_csh_default_shell_can_invoke_explicit_sh_without_startup_files(self):
        result = subprocess.run(["/bin/csh", "-f", "-c", self.shell_command()],
                                capture_output=True, timeout=2, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertIn(b"\n-W\n", result.stdout)


if __name__ == "__main__":
    unittest.main()

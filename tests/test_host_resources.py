"""Original Linux aggregate fixtures and offline SSH peers; no live probes."""

from copy import deepcopy
import json
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from spiceowl.adapters.hosts import probe_resources, validate_host_profile
from spiceowl.adapters.executors import ssh
from spiceowl.adapters.resources import RESOURCE_FIELDS, empty_resources, parse_resource_sample
from spiceowl.domain.common import DomainError
from test_ssh_hosts import FakeSSH, profile, transcript


def v2_profile():
    result = profile()
    result.update(schema="spiceowl.host.v2", ssh_hostname="compute-02.example.test")
    return result


def sample(**overrides):
    fields = {
        "cpu_first": "100 10 20 800 30 5 5 10 7 2",
        "cpu_second": "160 10 30 820 40 5 5 10 15 2",
        "cpu_count_first": "8", "cpu_count_second": "8",
        "uptime_first": "1000.00", "uptime_second": "1000.20",
        "load_1m": "3.50", "load_5m": "2.25",
        "MemTotal:": "16777216 kB", "MemAvailable:": "10485760 kB",
        "home_disk_available_bytes": "107374182400",
    }
    fields.update(overrides)
    return "\n".join(f"{key} {value}" for key, value in fields.items() if value is not None)


def resource_transcript(nonce, payload=None, *, host="sim-host", user="researcher"):
    return (
        transcript(nonce, host=host, user=user)
        + f"__SPICEOWL_{nonce}_RESOURCES_BEGIN__\n".encode()
        + (sample() if payload is None else payload).encode()
        + f"\n__SPICEOWL_{nonce}_RESOURCES_END__\n".encode()
    )


class ResourceParserTests(unittest.TestCase):
    def test_complete_sample_and_guest_accounting(self):
        result = parse_resource_sample(sample())
        self.assertEqual(set(result), set(RESOURCE_FIELDS))
        self.assertEqual(result, {
            "cpu_count": 8, "cpu_busy_percent": 70.0, "load_1m": 3.5, "load_5m": 2.25,
            "memory_total_bytes": 16 * 1024**3, "memory_available_bytes": 10 * 1024**3,
            "home_disk_available_bytes": 100 * 1024**3,
        })
        self.assertEqual(result, json.loads(json.dumps(result, allow_nan=False)))

    def test_idle_and_busy_extremes_require_advancing_counters(self):
        first = "100 0 0 800 0 0 0 0"
        cases = [
            ("100 0 0 900 0 0 0 0", 0.0),
            ("200 0 0 800 0 0 0 0", 100.0),
            ("100 0 0 800 0 0 0 100", 100.0),  # Stolen time is not idle headroom.
            ("100 0 0 800 100 0 0 0", 0.0),
            (first, None),
        ]
        for second, expected in cases:
            with self.subTest(second=second):
                result = parse_resource_sample(sample(cpu_first=first, cpu_second=second))
                self.assertEqual(result["cpu_busy_percent"], expected)

    def test_counter_regression_and_hotplug_are_unknown_not_clamped(self):
        for changes in (
            {"cpu_second": "99 10 20 820 30 5 5 10 7 2"},
            {"cpu_second": "160 10 30 820 29 5 5 10 15 2"},
            {"cpu_count_second": "4"},
            {"cpu_second": "160 10 30 820"},
            {"cpu_second": None},
        ):
            with self.subTest(changes=changes):
                self.assertIsNone(parse_resource_sample(sample(**changes))["cpu_busy_percent"])
        changed = parse_resource_sample(sample(cpu_count_second="4"))
        self.assertEqual(changed["cpu_count"], 4)

    def test_sample_interval_is_required_and_bounded(self):
        for value in (None, "1000.00", "999.80", "1000.01", "1005.00", "nan", "inf", "1e30"):
            with self.subTest(value=value):
                self.assertIsNone(parse_resource_sample(sample(uptime_second=value))["cpu_busy_percent"])
        for value in ("1000.10", "1002.00"):
            self.assertEqual(parse_resource_sample(sample(uptime_second=value))["cpu_busy_percent"], 70.0)

    def test_missing_metrics_are_null_and_memory_has_no_free_cache_fallback(self):
        self.assertEqual(parse_resource_sample(""), empty_resources())
        result = parse_resource_sample("MemTotal: 4096 kB")
        self.assertEqual(result["memory_total_bytes"], 4096 * 1024)
        self.assertIsNone(result["memory_available_bytes"])
        self.assertTrue(all(value is None for key, value in result.items() if key != "memory_total_bytes"))
        result = parse_resource_sample("MemAvailable: 1024 kB")
        self.assertIsNone(result["memory_available_bytes"])
        # Only the fixed MemAvailable projection is accepted by this wire format.
        for alternative in ("MemFree:", "Cached:", "Buffers:"):
            with self.subTest(alternative=alternative), self.assertRaises(DomainError):
                parse_resource_sample(f"MemTotal: 4096 kB\n{alternative} 3000 kB")

    def test_memory_units_zero_and_available_consistency(self):
        for value in (None, "-1 kB", "4096", "4096 MB", "NaN kB", "20000000 kB", "1.5 kB"):
            with self.subTest(value=value):
                result = parse_resource_sample(sample(**{"MemAvailable:": value}))
                self.assertIsNone(result["memory_available_bytes"])
                self.assertEqual(result["memory_total_bytes"], 16 * 1024**3)
        zero = parse_resource_sample(sample(**{"MemAvailable:": "0 kB", "home_disk_available_bytes": "0"}))
        self.assertEqual(zero["memory_available_bytes"], 0)
        self.assertEqual(zero["home_disk_available_bytes"], 0)
        invalid_total = parse_resource_sample(sample(**{"MemTotal:": "0 kB"}))
        self.assertIsNone(invalid_total["memory_total_bytes"])
        self.assertIsNone(invalid_total["memory_available_bytes"])

    def test_integer_and_float_overflow_do_not_make_small_values(self):
        changes = [
            ("cpu_second", f"{1 << 64} 10 30 820 40 5 5 10 15 2", "cpu_busy_percent"),
            ("cpu_second", " ".join([str((1 << 64) - 1)] * 8), "cpu_busy_percent"),
            ("cpu_count_second", str(1 << 63), "cpu_count"),
            ("cpu_count_second", "0", "cpu_count"),
            ("cpu_count_second", "true", "cpu_count"),
            ("MemTotal:", f"{1 << 63} kB", "memory_total_bytes"),
            ("MemAvailable:", f"{1 << 63} kB", "memory_available_bytes"),
            ("home_disk_available_bytes", str(1 << 63), "home_disk_available_bytes"),
            ("home_disk_available_bytes", "-100", "home_disk_available_bytes"),
            ("load_1m", "1e9999", "load_1m"),
            ("load_1m", "Infinity", "load_1m"),
            ("load_5m", "-0.5", "load_5m"),
            ("load_5m", "1000001", "load_5m"),
        ]
        for field, value, output in changes:
            with self.subTest(field=field, value=value):
                result = parse_resource_sample(sample(**{field: value}))
                self.assertIsNone(result[output])
                json.dumps(result, allow_nan=False)

    def test_structural_ambiguity_is_rejected_with_safe_errors(self):
        for payload in (
            None, {}, b"", "x" * 8193, "\n" * 33,
            "load_1m " + "9" * 513, "load_1m 1\nload_1m 2",
            sample() + "\n/fictional/private/process --command",
            "resources $(execute)",
        ):
            with self.subTest(payload=payload), self.assertRaises(DomainError) as error:
                parse_resource_sample(payload)
            self.assertNotIn("/fictional", str(error.exception))

    def test_partial_count_and_load_do_not_fill_missing_values(self):
        partial = parse_resource_sample("cpu_count_first 16\nload_1m 0")
        self.assertEqual(partial["cpu_count"], 16)
        self.assertEqual(partial["load_1m"], 0.0)
        self.assertIsNone(partial["load_5m"])
        self.assertIsNone(partial["cpu_busy_percent"])
        self.assertIsNone(parse_resource_sample("cpu_count_first 16\ncpu_count_second bad")["cpu_count"])
        copy = empty_resources()
        copy["cpu_count"] = 999
        self.assertIsNone(empty_resources()["cpu_count"])


class ResourceProbeTests(unittest.TestCase):
    def run_peer(self, replies, data=None):
        peer = FakeSSH(replies)
        with patch.object(ssh.subprocess, "Popen", side_effect=peer):
            result = probe_resources(data if data is not None else v2_profile())
        self.assertTrue(all(process.poll() is not None for process in peer.processes))
        return result, peer

    def test_contract_route_and_immutable_profile(self):
        data = v2_profile()
        original = deepcopy(data)
        replies = [{}, {"stdout": lambda nonce: b"locale: /fictional/startup\n" + resource_transcript(nonce),
                        "stderr": b"/fictional/private/config warning"}]
        result, peer = self.run_peer(replies, data)
        self.assertEqual(data, original)
        self.assertEqual(set(result), {"host_id", "synthetic", "scope", "observed_at",
                                       "transport", "identity", "resources", "error"})
        self.assertFalse(result["synthetic"])
        self.assertEqual(result["scope"], "host_resources")
        self.assertEqual(result["transport"], "reachable")
        self.assertEqual(result["identity"], {"verified": True, "hostname": "sim-host", "username": "researcher"})
        self.assertEqual(result["resources"], parse_resource_sample(sample()))
        self.assertIsNone(result["error"])
        self.assertEqual(len(peer.calls), 2)
        self.assertNotIn("/proc", peer.calls[0][0][-1])
        for argv, _ in peer.calls:
            self.assertEqual(argv[-3:-1], ["--", "example-route"])
            self.assertIn("Hostname=compute-02.example.test", argv)
            for option in ("StrictHostKeyChecking=yes", "UpdateHostKeys=no", "ForwardAgent=no", "ControlPath=none"):
                self.assertIn(option, argv)
            self.assertFalse(any(option.startswith(("UserKnownHostsFile=", "GlobalKnownHostsFile=", "IdentityFile=")) for option in argv))
        command = shlex.split(peer.calls[1][0][-1])[-1]
        for fixed in ("/proc/stat", "/proc/uptime", "/proc/loadavg", "/proc/meminfo", "/bin/sleep 0.2", '"$HOME"'):
            self.assertIn(fixed, command)
        for forbidden in ("spectre", "virtuoso", "ngspice", "/proc/self", "cmdline", "ps -", "top "):
            self.assertNotIn(forbidden, command)
        serialized = json.dumps(result)
        for forbidden in ("/fictional", "/opt/example", "ssh_hostname", "example-route", "license", "reservation"):
            self.assertNotIn(forbidden, serialized)

    def test_v1_identity_only_profile_can_sample_without_hostname_option(self):
        result, peer = self.run_peer([{}, {"stdout": resource_transcript}], profile([]))
        self.assertEqual(result["resources"]["cpu_count"], 8)
        for argv, _ in peer.calls:
            self.assertFalse(any(value.startswith("Hostname=") for value in argv))

    def test_mismatch_before_metrics_and_recheck_on_resource_connection(self):
        for identity in ({"host": "wrong-host"}, {"user": "wrong-user"}):
            with self.subTest(identity=identity):
                result, peer = self.run_peer([identity])
                self.assertEqual(len(peer.calls), 1)
                self.assertEqual(result["error"]["code"], "identity_mismatch")
                self.assertEqual(result["resources"], empty_resources())
                self.assertFalse(result["identity"]["verified"])
        result, peer = self.run_peer([{}, {"host": "changed-host", "returncode": 72}])
        self.assertEqual(len(peer.calls), 2)
        self.assertEqual(result["error"]["code"], "identity_mismatch")
        self.assertEqual(result["resources"], empty_resources())
        self.assertFalse(result["identity"]["verified"])

    def test_duplicate_missing_or_unknown_frames_fail_closed(self):
        replies = [
            lambda nonce: resource_transcript(nonce) + f"__SPICEOWL_{nonce}_RESOURCES_END__\n".encode(),
            lambda nonce: resource_transcript(nonce).replace(b"RESOURCES_END", b"OTHER_END"),
            lambda nonce: transcript(nonce),
            lambda nonce: resource_transcript(nonce, sample() + "\nload_1m 0"),
            lambda nonce: resource_transcript(nonce, "unknown /fictional/path"),
        ]
        for output in replies:
            with self.subTest(output=output):
                result, peer = self.run_peer([{}, {"stdout": output}])
                self.assertEqual(len(peer.calls), 2)
                self.assertEqual(result["transport"], "unknown")
                self.assertFalse(result["identity"]["verified"])
                self.assertEqual(result["resources"], empty_resources())
                self.assertIsNotNone(result["error"])
                self.assertNotIn("/fictional", json.dumps(result))

    def test_missing_proc_data_stays_null_but_connection_can_be_verified(self):
        result, _ = self.run_peer([{}, {"stdout": lambda nonce: resource_transcript(nonce, "")}])
        self.assertEqual(result["transport"], "reachable")
        self.assertTrue(result["identity"]["verified"])
        self.assertEqual(result["resources"], empty_resources())
        self.assertEqual(result["error"]["code"], "resources_unavailable")
        result, _ = self.run_peer([{}, {"stdout": lambda nonce: resource_transcript(nonce, "MemTotal: 1024 kB")}])
        self.assertEqual(result["resources"]["memory_total_bytes"], 1048576)
        self.assertIsNone(result["resources"]["memory_available_bytes"])
        self.assertIsNone(result["resources"]["cpu_busy_percent"])
        self.assertIsNone(result["error"])

    def test_busy_sample_is_not_mislabeled_as_idle_when_next_probe_fails(self):
        good, _ = self.run_peer([{}, {"stdout": resource_transcript}])
        self.assertEqual(good["resources"]["cpu_busy_percent"], 70.0)
        failed, peer = self.run_peer([{}, {"stdout": b"", "returncode": 255}])
        self.assertEqual(len(peer.calls), 2)
        self.assertEqual(failed["transport"], "unknown")
        self.assertFalse(failed["identity"]["verified"])
        self.assertEqual(failed["resources"], empty_resources())
        self.assertEqual(good["resources"]["cpu_busy_percent"], 70.0)

    def test_shared_deadline_and_no_retry(self):
        observed_deadlines = []

        def invoke(target, script, deadline, *, ssh_hostname=None):
            observed_deadlines.append(deadline)
            nonce = re.search(r"__SPICEOWL_([0-9a-f]{32})_", script).group(1)
            output = transcript(nonce) if len(observed_deadlines) == 1 else resource_transcript(nonce)
            return ssh._Reply(0, output)

        before = time.monotonic()
        with patch.object(ssh, "_invoke", side_effect=invoke):
            probe_resources(v2_profile())
        self.assertEqual(len(observed_deadlines), 2)
        self.assertEqual(observed_deadlines[0], observed_deadlines[1])
        self.assertLessEqual(observed_deadlines[0] - before, 10.1)
        with patch.object(ssh, "RESOURCE_TIMEOUT_SECONDS", 0), patch.object(ssh.subprocess, "Popen") as popen:
            result = probe_resources(v2_profile())
        popen.assert_not_called()
        self.assertEqual(result["error"]["code"], "probe_timeout")

    def test_timeout_and_large_outputs_are_unknown_and_reaped(self):
        cases = [
            {"source": "import time; time.sleep(20)"},
            {"source": "import sys; sys.stdout.buffer.write(b'x' * 1000000)"},
            {"source": "import sys; sys.stderr.buffer.write(b'x' * 1000000)"},
        ]
        for reply in cases:
            with self.subTest(reply=reply), patch.object(ssh, "RESOURCE_TIMEOUT_SECONDS", 0.3):
                result, peer = self.run_peer([{}, reply])
            self.assertEqual(len(peer.calls), 2)
            self.assertEqual(result["transport"], "unknown")
            self.assertFalse(result["identity"]["verified"])
            self.assertEqual(result["resources"], empty_resources())
            self.assertLess(len(json.dumps(result)), 1000)

    def test_resource_profile_rejects_path_and_command_extensions_without_io(self):
        with patch.object(ssh.subprocess, "Popen") as popen:
            for key in ("filesystem", "path", "command", "process", "interval", "last_resources"):
                data = v2_profile()
                data[key] = "/fictional/private"
                with self.subTest(key=key), self.assertRaises(DomainError):
                    probe_resources(data)
            popen.assert_not_called()


class ResourceShellTests(unittest.TestCase):
    def fixture_script(self, directory, *, host="sim-host", user="researcher"):
        directory = Path(directory)
        fixtures = {
            "stat_first": "cpu 100 10 20 800 30 5 5 10 7 2\n" + "".join(f"cpu{i} 1 0 0 1 0 0 0 0 0 0\n" for i in range(8)) + "intr 123\n",
            "stat_second": "cpu 160 10 30 820 40 5 5 10 15 2\n" + "".join(f"cpu{i} 1 0 0 1 0 0 0 0 0 0\n" for i in range(8)) + "intr 123\n",
            "uptime_first": "1000.00 100.0\n", "uptime_second": "1000.20 100.0\n",
            "loadavg": "3.50 2.25 1.00 1/20 300\n",
            "meminfo": "MemTotal: 16777216 kB\nMemFree: 9999999 kB\nMemAvailable: 10485760 kB\n",
        }
        for name, content in fixtures.items():
            (directory / name).write_text(content)
        script = ssh._resource_script("a" * 32, profile())
        script = script.replace("/bin/hostname 2>/dev/null", f"printf %s {shlex.quote(host)}")
        script = script.replace("/usr/bin/id -un 2>/dev/null", f"printf %s {shlex.quote(user)}")
        for suffix in ("first", "second"):
            script = script.replace("/proc/stat", shlex.quote(str(directory / f"stat_{suffix}")), 1)
            script = script.replace("/proc/uptime", shlex.quote(str(directory / f"uptime_{suffix}")), 1)
        for name in ("loadavg", "meminfo"):
            script = script.replace(f"/proc/{name}", shlex.quote(str(directory / name)))
        # Disable the optional real filesystem probe; all sampled inputs are fixtures.
        return script.replace("/usr/bin/timeout", "/fictional/not-installed/timeout")

    def test_fixed_shell_collects_only_aggregate_fixtures(self):
        with tempfile.TemporaryDirectory(prefix="spiceowl-resources-") as directory:
            script = self.fixture_script(directory)
            result = subprocess.run(["/bin/sh", "-c", script], capture_output=True, timeout=3, check=False)
        self.assertEqual(result.returncode, 0)
        text, identity = ssh._identity(ssh._Reply(result.returncode, result.stdout), "a" * 32, resources=True)
        self.assertEqual(identity["hostname"], "sim-host")
        payload, _, _ = ssh._extract(text, "a" * 32, "RESOURCES")
        parsed = parse_resource_sample("\n".join(payload))
        self.assertEqual(parsed["cpu_count"], 8)
        self.assertEqual(parsed["cpu_busy_percent"], 70.0)
        self.assertEqual(parsed["memory_available_bytes"], 10 * 1024**3)
        self.assertIsNone(parsed["home_disk_available_bytes"])
        self.assertNotIn(b"MemFree", result.stdout)
        self.assertNotIn(b"cpu0", result.stdout)

    def test_identity_guard_prevents_resource_reads(self):
        with tempfile.TemporaryDirectory(prefix="spiceowl-resources-") as directory:
            for host, user in (("wrong-host", "researcher"), ("sim-host", "wrong-user")):
                script = self.fixture_script(directory, host=host, user=user)
                result = subprocess.run(["/bin/sh", "-c", script], capture_output=True, timeout=2, check=False)
                self.assertEqual(result.returncode, 72)
                self.assertNotIn(b"RESOURCES_BEGIN", result.stdout)
                self.assertNotIn(b"cpu_first", result.stdout)

    def test_optional_disk_value_requires_successful_command(self):
        with tempfile.TemporaryDirectory(prefix="spiceowl-resources-") as directory:
            fake_timeout = Path(directory) / "fixture-timeout"
            for status in (0, 1, 124):
                fake_timeout.write_text(f'#!/bin/sh\nprintf "Avail\\n123456\\n"\nexit {status}\n')
                fake_timeout.chmod(0o700)
                script = self.fixture_script(directory).replace(
                    "/fictional/not-installed/timeout", shlex.quote(str(fake_timeout)))
                result = subprocess.run(["/bin/sh", "-c", script], capture_output=True, timeout=3, check=False)
                self.assertEqual(result.returncode, 0)
                text, _ = ssh._identity(ssh._Reply(result.returncode, result.stdout), "a" * 32, resources=True)
                payload, _, _ = ssh._extract(text, "a" * 32, "RESOURCES")
                parsed = parse_resource_sample("\n".join(payload))
                self.assertEqual(parsed["home_disk_available_bytes"], 123456 if status == 0 else None)
                self.assertEqual(parsed["cpu_busy_percent"], 70.0)

    @unittest.skipUnless(Path("/bin/csh").is_file(), "csh not installed")
    def test_nested_awk_quotes_survive_csh_to_explicit_sh(self):
        with tempfile.TemporaryDirectory(prefix="spiceowl-resources-") as directory:
            command = ssh._ssh_argv("example-route", self.fixture_script(directory))[-1]
            self.assertNotIn("\n", command)
            result = subprocess.run(["/bin/csh", "-f", "-c", command], capture_output=True, timeout=3, check=False)
        self.assertEqual(result.returncode, 0)
        text, _ = ssh._identity(ssh._Reply(result.returncode, result.stdout), "a" * 32, resources=True)
        payload, _, _ = ssh._extract(text, "a" * 32, "RESOURCES")
        self.assertEqual(parse_resource_sample("\n".join(payload))["cpu_busy_percent"], 70.0)


if __name__ == "__main__":
    unittest.main()

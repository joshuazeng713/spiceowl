"""Offline smoke transport tests: no SSH, Spectre, PDK or live simulation."""

import base64
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from spiceowl.domain.common import DomainError

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("inverter_smoke_transport", ROOT / "scripts/inverter_smoke_transport.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)
REAL_POPEN = subprocess.Popen
PROFILE = {"schema": "spiceowl.host.v2", "host_id": "example-node", "label": "Example",
           "ssh_target": "example-route", "ssh_hostname": "example-node", "expected_host": "example-node",
           "expected_user": "researcher", "tools": [{"kind": "spectre", "executable": "/opt/example/spectre"}]}
NETLIST = "simulator lang=spectre\nmodel toy_n mos1 type=n vto=0.4\nmodel toy_p mos1 type=p vto=-0.4\ntran tran stop=50n\n"


def plan(key="smoke-example", text=NETLIST):
    return smoke.prepare(PROFILE, key, text, "99.1.0.123", remote_python="/opt/example/bin/python3.6")


class FrozenPlanTests(unittest.TestCase):
    def test_prepare_is_pure_frozen_and_accepts_flattened_original_fixture(self):
        with patch.object(smoke.subprocess, "Popen", side_effect=AssertionError("No process during prepare")):
            first = plan()
            self.assertEqual(first, plan())
            self.assertEqual(smoke._validate(first), first["manifest"])
            fixture = (ROOT / "examples/inverter/tb.scs").read_text()
            for name in ("models.scs", "inverter.scs"):
                fixture = fixture.replace('include "' + name + '"', (ROOT / "examples/inverter" / name).read_text())
            smoke.prepare(PROFILE, "fixture", fixture, "99.1.0.123")
        changed = plan(text=NETLIST.replace("50n", "60n"))
        self.assertEqual(first["manifest"]["directory_name"], changed["manifest"]["directory_name"])
        self.assertNotEqual(first["manifest_sha256"], changed["manifest_sha256"])

    def test_mutation_injection_and_include_paths_are_rejected_before_io(self):
        for change in (lambda p: p["manifest"]["limits"].update(cpu_count=2),
                       lambda p: p["manifest"]["argv"].append(";id"),
                       lambda p: p["files"].update({"outside": "AAAA"}),
                       lambda p: p["manifest"].update(directory_name="../../outside")):
            candidate = plan(); change(candidate)
            with self.assertRaises(DomainError):
                smoke._validate(candidate)
        for text in (NETLIST + 'include "private.scs"\n', NETLIST + "ahdl_include model.va\n",
                     NETLIST + "options write=/outside\n", NETLIST + "shell id\n"):
            with self.assertRaises(DomainError):
                plan(text=text)
        for binary in ("python3", "/bin/sh", "/a/../python3", "/tmp/a;id/python3", "/a/python3 -c code"):
            with self.assertRaises(DomainError):
                smoke.prepare(PROFILE, "fixture", NETLIST, "99.1.0.123", remote_python=binary)

    def test_lost_launch_reply_is_unknown_and_never_retried(self):
        with patch.object(smoke, "_exchange", side_effect=DomainError("Interrupted", "fixture_disconnect")) as exchange:
            result = smoke.launch(plan())
        self.assertEqual(result["state"], "outcome_unknown")
        exchange.assert_called_once()

    def test_license_server_settings_are_copied_and_bound_to_frozen_manifest(self):
        environment = {"CDS_LIC_FILE": "5280@license-a.example.invalid",
                       "LM_LICENSE_FILE": "5280@license-a.example.invalid:5281@license-b.example.invalid"}
        with patch.object(smoke.subprocess, "Popen", side_effect=AssertionError("No process during license configuration")):
            frozen = smoke.prepare(PROFILE, "license-fixture", NETLIST, "99.1.0.123",
                                   license_environment=environment)
            self.assertEqual(frozen["manifest"]["license_environment"], environment)
            self.assertEqual(smoke._validate(frozen), frozen["manifest"])
            environment["CDS_LIC_FILE"] = "5280@changed.example.invalid"
            self.assertEqual(frozen["manifest"]["license_environment"]["CDS_LIC_FILE"], "5280@license-a.example.invalid")
            self.assertEqual(smoke._validate(frozen), frozen["manifest"])
            changed = deepcopy(frozen)
            changed["manifest"]["license_environment"]["CDS_LIC_FILE"] = environment["CDS_LIC_FILE"]
            with self.assertRaises(DomainError):
                smoke._validate(changed)
            replacement = smoke.prepare(PROFILE, "license-fixture", NETLIST, "99.1.0.123",
                                         license_environment=environment)
            self.assertNotEqual(replacement["manifest_sha256"], frozen["manifest_sha256"])
            self.assertEqual(replacement["manifest"]["directory_name"], frozen["manifest"]["directory_name"])

    def test_license_environment_rejects_unrelated_variables_and_unsafe_values_before_io(self):
        invalid = [{name: "fixture"} for name in ("PATH", "LD_PRELOAD", "SSH_AUTH_SOCK")]
        invalid += [{"CDS_LIC_FILE": value} for value in (
            "5280@license.example.invalid\nPATH=/tmp", "5280@license.example.invalid;id",
            "$(id)", "`id`", "5280@license.example.invalid extra", "", "x" * 2049, 5280)]
        invalid += [["CDS_LIC_FILE", "5280@license.example.invalid"]]
        with patch.object(smoke.subprocess, "Popen", side_effect=AssertionError("Invalid configuration must not run a process")):
            for environment in invalid:
                with self.subTest(environment=environment), self.assertRaises(DomainError):
                    smoke.prepare(PROFILE, "license-fixture", NETLIST, "99.1.0.123",
                                  license_environment=environment)


@unittest.skipIf(os.name == "nt", "Remote Linux worker script requires POSIX modules (pwd/resource)")
class RemoteFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.p = plan()
        self.ns = {"__name__": "remote_fixture"}
        exec(compile(smoke._REMOTE_SOURCE, "<remote-fixture>", "exec"), self.ns)
        self.ns["home_path"] = lambda: self.temp.name
        self.ns["identity"] = lambda _: {"hostname": "example-node", "username": "researcher"}
        self.ns["signal"] = SimpleNamespace(signal=lambda *args: None, SIGHUP=signal.SIGHUP,
                                            SIG_IGN=signal.SIG_IGN, SIGKILL=signal.SIGKILL)
        self.request = {"manifest": self.p["manifest"], "files": self.p["files"]}

    def tearDown(self):
        self.ns["DEVNULL"].close()
        self.temp.cleanup()

    def stage(self):
        return self.ns["stage"](self.request)

    def terminal(self, path, state="failed"):
        self.ns["write_atomic"](os.path.join(path, "status.json"), {"state": state, "terminal": True,
                                "manifest_sha256": self.p["manifest_sha256"]})

    def test_stage_is_idempotent_but_input_change_or_tamper_fails_closed(self):
        first = self.stage()
        self.assertEqual(first["state"], "staged")
        self.assertEqual(self.stage()["remote_directory"], first["remote_directory"])
        different = plan(text=NETLIST.replace("50n", "60n"))
        with self.assertRaises(self.ns["Failure"]):
            self.ns["stage"]({"manifest": different["manifest"], "files": different["files"]})
        Path(first["remote_directory"], "input.scs").write_text("changed")
        with self.assertRaises(self.ns["Failure"]):
            self.ns["launch"](self.request)

    def test_identity_mismatch_precedes_any_remote_write(self):
        def denied(_):
            raise self.ns["Failure"]("identity_mismatch")
        self.ns["identity"] = denied
        with self.assertRaises(self.ns["Failure"]):
            self.stage()
        self.assertEqual(os.listdir(self.temp.name), [])

    def test_dispatch_claim_prevents_duplicate_even_without_pid_receipt(self):
        path = self.stage()["remote_directory"]
        calls = []
        self.ns["requirements"] = lambda _: None
        self.ns["bounded_version"] = lambda *_: None
        self.ns["detached_wrapper"] = lambda p: calls.append(p)
        first = self.ns["launch"](self.request)
        second = self.ns["launch"](self.request)
        self.assertEqual(first["state"], "outcome_unknown")
        self.assertEqual(second["state"], "outcome_unknown")
        self.assertEqual(calls, [path])
        self.assertTrue(first["accepted"]["dispatch_claimed"])
        self.assertEqual(first["accepted"], second["accepted"])

    def test_stage_interruption_is_not_overwritten(self):
        path = self.ns["directory"](self.p["manifest"], True)
        os.mkdir(path, 0o700)
        self.assertEqual(self.stage()["state"], "stage_incomplete")
        self.assertEqual(os.listdir(path), [])

    def test_collection_checks_terminal_inputs_hashes_and_symlink_escape(self):
        path = self.stage()["remote_directory"]
        with self.assertRaises(self.ns["Failure"]):
            self.ns["collect"](self.request)
        self.terminal(path)
        data = self.ns["collect"](self.request)
        smoke._verify_collection(self.p, data)
        tampered = deepcopy(data)
        tampered["files"][0]["sha256"] = "0" * 64
        with self.assertRaises(DomainError):
            smoke._verify_collection(self.p, tampered)
        raw = Path(path, "results.raw"); raw.mkdir()
        os.symlink(Path(self.temp.name, "outside"), raw / "tran.tran")
        with self.assertRaises(self.ns["Failure"]):
            self.ns["collect"](self.request)

    def test_completed_collection_requires_waveforms_and_log(self):
        path = self.stage()["remote_directory"]
        self.terminal(path, "completed")
        with self.assertRaises(DomainError):
            smoke._verify_collection(self.p, self.ns["collect"](self.request))
        Path(path, "spectre.log").write_text("authored fixture; no real Spectre run")
        Path(path, "results.raw").mkdir()
        Path(path, "results.raw", "tran.tran").write_text("synthetic waveform fixture")
        smoke._verify_collection(self.p, self.ns["collect"](self.request))

    def test_solver_branch_preserves_wrapper_pid_and_sigkill_is_not_timeout(self):
        path = self.stage()["remote_directory"]
        self.ns["__file__"] = os.path.join(path, "wrapper.py")
        self.ns["requirements"] = lambda _: None
        self.ns["proc_identity"] = lambda pid: {"pid": pid, "start_ticks": 1, "boot_id": "fixture-boot", "state": "R"}
        called = []
        self.ns["solver_exec"] = lambda *args: called.append(args)
        wrapper_path = Path(path, "wrapper.json")
        self.ns["write_atomic"](str(wrapper_path), {"pid": 123, "role": "detached_wrapper"})
        with patch.object(sys, "argv", ["wrapper.py", "--solver-exec"]):
            self.ns["wrapper_main"]()
        self.assertEqual(json.loads(wrapper_path.read_text())["pid"], 123)
        self.assertEqual(len(called), 1)
        self.ns["allowed_cpu"] = lambda: 0
        self.ns["clock"] = lambda: 1.0
        self.ns["wait_for"] = lambda *args: 137
        self.ns["subprocess"] = SimpleNamespace(Popen=lambda *a, **k: SimpleNamespace(pid=1234, poll=lambda:137))
        with patch.object(sys, "argv", ["wrapper.py", "--wrapper"]):
            self.ns["wrapper_main"]()
        result = json.loads(Path(path, "status.json").read_text())
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["termination_reason"], "kill_signal_cause_unknown")


class LocalExchangeTests(unittest.TestCase):
    def test_exchange_timeout_and_output_cap_preserve_unknown_launch(self):
        for source, option, value, error in (
            ("import time;time.sleep(10)", "SSH_TIMEOUT_SECONDS", 0.05, "smoke_transport_timeout"),
            ("import sys;sys.stdout.buffer.write(b'x'*1000000)", "MAX_REPLY_BYTES", 512, "smoke_reply_limit"),
        ):
            def peer(argv, **kwargs):
                return REAL_POPEN([sys.executable, "-c", source], **kwargs)
            with self.subTest(error=error), patch.object(smoke, option, value), patch.object(smoke.subprocess, "Popen", side_effect=peer) as patched:
                result = smoke.launch(plan())
                self.assertEqual(result["state"], "outcome_unknown")
                self.assertEqual(result["error"]["code"], error)
                patched.assert_called_once()

    def test_fixed_ssh_route_and_framing(self):
        calls = []
        def peer(argv, **kwargs):
            calls.append(argv)
            command = shlex.split(shlex.split(argv[-1])[-1])
            request = json.loads(base64.b64decode(command[-1]))
            nonce = request["nonce"]
            payload = {"ok": True, "data": {"manifest_sha256": plan()["manifest_sha256"], "state": "staged"}}
            out = ("startup warning\n__SPICEOWL_SMOKE_%s_BEGIN__\n%s\n__SPICEOWL_SMOKE_%s_END__\n" % (nonce, json.dumps(payload), nonce)).encode("utf-8")
            return REAL_POPEN([sys.executable, "-c", "import sys;sys.stdout.buffer.write(" + repr(out) + ")"], **kwargs)
        with patch.object(smoke.subprocess, "Popen", side_effect=peer):
            result = smoke.stage(plan())
        self.assertEqual(result["state"], "staged")
        self.assertEqual(len(calls), 1)
        for option in ("StrictHostKeyChecking=yes", "BatchMode=yes", "Hostname=example-node", "UpdateHostKeys=no"):
            self.assertIn(option, calls[0])
        self.assertNotIn("\n", calls[0][-1])


if __name__ == "__main__":
    unittest.main()

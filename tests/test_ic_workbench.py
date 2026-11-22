"""Offline IC adapter tests. No gateway, SSH, simulator or model is contacted."""

import copy
import io
import json
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from spiceowl.domain.common import DomainError
from spiceowl.integrations.ic_workbench import (
    ICGatewayBackend, LoopbackGatewayClient, MAX_RESPONSE_BYTES,
    decode_envelope, validate_gateway_config, validate_supervision, validate_submission,
)


CONFIG = {"id": "fixture-ic", "label": "Fixture IC", "base_url": "http://127.0.0.1:18881",
          "project_id": "fixture-project"}
OBSERVED = "2026-10-01T00:00:00+00:00"
READ_AT = "2026-11-20T00:00:00+00:00"


def run_fixture(run_id="fixture-native-run"):
    return {"run_id": run_id, "project_id": CONFIG["project_id"], "original_project_id": "old-project",
            "association_basis": "recorded_plan_design_id", "source_commit": "a" * 40,
            "configuration": "tran", "compiled_inputs_sha256": None, "state": "collected",
            "execution": {"state": "succeeded", "raw_state": "simulator_complete_pending_measurements",
                          "exit_code": 0, "observed_at": OBSERVED},
            "observation": {"state": "recorded", "observed_at": OBSERVED},
            "data": {"state": "collected", "raw_retained": True},
            "specification": {"state": "reported", "metrics": [
                {"name": "toy metric", "value": 0.1, "unit": None, "passed": True, "scope": "one node"}]},
            "latest_observed_at": OBSERVED, "record_updated_at": OBSERVED,
            "issues": [{"code": "synthetic_fixture", "message": "Authored offline fixture."}],
            "waveform_run_id": run_id, "artifacts": [
                {"name": "waveforms.json", "bytes": 12, "sha256": None,
                 "url": "/api/jobs/" + run_id + "/artifacts/waveforms.json", "digest_source": "unavailable"}]}


def snapshot_fixture(*runs):
    return {"schema": "ic.supervision.snapshot.v1", "project_id": CONFIG["project_id"],
            "read_at": READ_AT, "source": "stored_observations", "runs": list(runs),
            "scanned_records": len(runs), "unavailable_records": 0, "truncated": False, "single_site": True}


def lookup_fixture(key="fixture-key", run=None):
    return {"schema": "ic.submission.lookup.v1", "submission_key": key, "read_at": READ_AT,
            "found": run is not None, "run_id": run["run_id"] if run else None, "run": run}


class Response:
    def __init__(self, raw, status=200, headers=None):
        self.status = status
        self.headers = {"Content-Type": "application/json", "Content-Length": str(len(raw))}
        self.headers.update(headers or {})
        self.buffer = io.BytesIO(raw)

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read1(self, amount):
        return self.buffer.read(amount)


class ICRegistrationTests(unittest.TestCase):
    def test_only_explicit_literal_loopback_origins_are_accepted(self):
        self.assertEqual(validate_gateway_config(CONFIG), CONFIG)
        self.assertEqual(validate_gateway_config(CONFIG | {"base_url": CONFIG["base_url"] + "/"}), CONFIG)
        for origin in ("http://localhost:18881", "http://127.0.0.2:18881", "https://127.0.0.1:18881",
                       "http://127.0.0.1", "http://127.0.0.1:0", "http://127.0.0.1:65536",
                       "http://user@127.0.0.1:18881", "http://127.0.0.1:18881/api",
                       "http://127.0.0.1:18881/?x=1", "http://127.0.0.1:18881/#x",
                       "http://127.0.0.1:18881\n", "http://169.254.169.254:80", None):
            with self.subTest(origin=origin), self.assertRaises(DomainError):
                validate_gateway_config(CONFIG | {"base_url": origin})
        for changes in ({"id": "../x"}, {"project_id": "project&escape=x"}, {"label": "\0secret"},
                        {"label": " "}, {"password": "PRIVATE"}, {"command": "PRIVATE"}):
            with self.subTest(changes=changes), self.assertRaises(DomainError):
                validate_gateway_config(CONFIG | changes)

    def test_registration_listing_and_restart_make_no_network_request(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(LoopbackGatewayClient, "_get", side_effect=AssertionError("network")):
            backend = ICGatewayBackend(directory)
            public, created = backend.register(CONFIG)
            self.assertTrue(created)
            self.assertEqual(public["backend"], "ic-workbench")
            self.assertEqual(public["observation"]["state"], "unobserved")
            self.assertNotIn("base_url", public)
            self.assertEqual(backend.register(copy.deepcopy(CONFIG)), (public, False))
            self.assertEqual(ICGatewayBackend(directory).list(), [public])
            self.assertEqual(backend.inspect(CONFIG["id"])["base_url"], CONFIG["base_url"])
            with self.assertRaises(DomainError) as raised:
                backend.register(CONFIG | {"project_id": "other-project"})
            self.assertEqual(raised.exception.code, "conflict")
            self.assertEqual(backend.list(), [public])


class ICTransportTests(unittest.TestCase):
    def client_call(self, raw, *, status=200, headers=None, submission=None):
        connection = MagicMock()
        connection.getresponse.return_value = Response(raw, status, headers)
        with patch("spiceowl.integrations.ic_workbench.http.client.HTTPConnection", return_value=connection) as factory:
            client = LoopbackGatewayClient(CONFIG)
            result = client.supervision() if submission is None else client.submission(submission)
        factory.assert_called_once_with("127.0.0.1", 18881, timeout=5.0)
        self.assertTrue(connection.close.called)
        return result, connection

    def test_fixed_get_routes_preserve_submission_key(self):
        raw = b'{"ok":true,"data":{"fixture":true}}'
        result, connection = self.client_call(raw)
        self.assertEqual(result, {"fixture": True})
        self.assertEqual(connection.request.call_args.args, ("GET", "/api/supervision?project_id=fixture-project"))
        _, connection = self.client_call(raw, submission="fixture:attempt-1")
        self.assertEqual(connection.request.call_args.args, ("GET", "/api/submissions/fixture%3Aattempt-1"))
        connection.request.assert_called_once()
        _, connection = self.client_call(raw, submission="caller/key % title")
        self.assertEqual(connection.request.call_args.args, ("GET", "/api/submissions/caller%2Fkey%20%25%20title"))

    def test_redirects_errors_and_invalid_payloads_never_retry_or_leak_raw_text(self):
        cases = [(b"PRIVATE", 302, {"Location": "http://example.invalid"}, "ic_gateway_http"),
                 (b"PRIVATE", 503, {}, "ic_gateway_http"),
                 (b"PRIVATE", 200, {"Content-Type": "text/html"}, "ic_gateway_protocol"),
                 (b"PRIVATE", 200, {"Content-Encoding": "gzip"}, "ic_gateway_protocol"),
                 (b"PRIVATE", 200, {}, "ic_gateway_protocol"),
                 (b'{"ok":true,"data":{}}', 200, {"Content-Length": "999"}, "ic_gateway_protocol"),
                 (b"", 200, {"Content-Length": str(MAX_RESPONSE_BYTES + 1)}, "ic_gateway_response_limit")]
        for raw, status, headers, code in cases:
            with self.subTest(code=code, headers=headers), self.assertRaises(DomainError) as raised:
                self.client_call(raw, status=status, headers=headers)
            self.assertEqual(raised.exception.code, code)
            self.assertNotIn("PRIVATE", str(raised.exception))

    def test_ambiguous_and_nonfinite_json_is_rejected(self):
        for raw in (b'{"ok":true,"ok":true,"data":{}}', b'{"ok":1,"data":{}}',
                    b'{"ok":true,"data":{"x":NaN}}', b'{"ok":true,"data":{"x":1e999}}',
                    b'{"ok":true,"data":[]}', b'{"ok":false,"error":{"message":"PRIVATE"}}',
                    b'{"ok":true,"data":{},"extra":1}', b"\xff", b"[]"):
            with self.subTest(raw=raw), self.assertRaises(DomainError):
                decode_envelope(raw)

    def test_transport_failure_is_safe_and_one_shot(self):
        connection = MagicMock()
        connection.connect.side_effect = OSError("PRIVATE connection detail")
        with patch("spiceowl.integrations.ic_workbench.http.client.HTTPConnection", return_value=connection):
            with self.assertRaises(DomainError) as raised:
                LoopbackGatewayClient(CONFIG).supervision()
        self.assertEqual(raised.exception.code, "ic_gateway_transport")
        self.assertNotIn("PRIVATE", str(raised.exception))
        connection.connect.assert_called_once()
        connection.request.assert_not_called()

    def test_request_deadline_interrupts_a_stalled_response(self):
        released = threading.Event()
        response = Response(b'{"ok":true,"data":{}}')
        response.read1 = lambda _: released.wait(1) and b""
        connection = MagicMock()
        connection.sock.shutdown.side_effect = lambda *_: released.set()
        connection.getresponse.return_value = response
        with patch("spiceowl.integrations.ic_workbench.http.client.HTTPConnection", return_value=connection), \
                patch("spiceowl.integrations.ic_workbench.REQUEST_TIMEOUT", 0.02):
            with self.assertRaises(DomainError) as raised:
                LoopbackGatewayClient(CONFIG).supervision()
        self.assertEqual(raised.exception.code, "ic_gateway_transport")
        self.assertTrue(released.is_set())

    def test_unframed_large_body_and_ambiguous_framing_are_rejected(self):
        with self.assertRaises(DomainError) as raised:
            self.client_call(b" " * (MAX_RESPONSE_BYTES + 1), headers={"Content-Length": None})
        self.assertEqual(raised.exception.code, "ic_gateway_response_limit")
        with self.assertRaises(DomainError) as raised:
            self.client_call(b'{"ok":true,"data":{}}', headers={"Transfer-Encoding": "chunked"})
        self.assertEqual(raised.exception.code, "ic_gateway_protocol")

    def test_invalid_key_never_reaches_transport(self):
        with patch.object(LoopbackGatewayClient, "_get") as request:
            for key in ("short", "invalid\nkey", "invalid\x7fkey", "", "x" * 161, None):
                with self.subTest(key=key), self.assertRaises(DomainError):
                    LoopbackGatewayClient(CONFIG).submission(key)
            request.assert_not_called()


class ICProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backend = ICGatewayBackend(self.temp.name)
        self.backend.register(CONFIG)

    def refresh(self, snapshot):
        with patch.object(LoopbackGatewayClient, "supervision", return_value=snapshot) as get:
            result = self.backend.refresh(CONFIG["id"])
        get.assert_called_once()
        return result

    def test_legacy_identity_and_independent_dimensions_survive_restart(self):
        source = run_fixture()
        result = self.refresh(snapshot_fixture(source))
        self.assertTrue(result["updated"])
        saved = result["runs"][0]
        self.assertEqual(saved["run_id"], source["run_id"])
        self.assertEqual(saved["original_project_id"], "old-project")
        self.assertEqual(saved["source_commit"], "a" * 40)
        self.assertIsNone(saved["compiled_inputs_sha256"])
        self.assertIsNone(saved["submission_key"])
        self.assertEqual(saved["backend"], "ic-workbench")
        self.assertTrue(saved["synthetic"])
        self.assertEqual(saved["execution"]["state"], "succeeded")
        self.assertEqual(saved["data"]["state"], "collected")
        self.assertEqual(saved["measurement"]["state"], "indeterminate")
        self.assertEqual(saved["specification"]["state"], "reported")
        self.assertEqual(saved["observation"]["observed_at"], OBSERVED)
        self.assertEqual(saved["observation"]["state"], "recorded")
        self.assertNotEqual(saved["observation"]["observed_at"], saved["observation"]["checked_at"])
        self.assertEqual(ICGatewayBackend(self.temp.name).run(CONFIG["id"], source["run_id"]), saved)
        self.assertEqual(self.backend.store.list("run"), [])
        with patch.object(LoopbackGatewayClient, "_get", side_effect=AssertionError("Unexpected network")):
            self.assertEqual(self.backend.runs(CONFIG["id"]), [saved])

    def test_missing_historical_commit_and_configuration_remain_null(self):
        run = run_fixture()
        run.update(source_commit=None, configuration=None, waveform_run_id=None)
        run["observation"] = {"state": "missing", "observed_at": None}
        run["execution"]["observed_at"] = None
        run["latest_observed_at"] = None
        saved = self.refresh(snapshot_fixture(run))["runs"][0]
        self.assertIsNone(saved["source_commit"])
        self.assertIsNone(saved["configuration"])
        self.assertEqual(saved["observation"]["state"], "missing")

    def test_transport_failure_preserves_every_scientific_dimension(self):
        old = self.refresh(snapshot_fixture(run_fixture()))["runs"][0]
        with patch.object(LoopbackGatewayClient, "supervision", side_effect=DomainError("Gateway unavailable.", "ic_gateway_transport")):
            failed = self.backend.refresh(CONFIG["id"])
        self.assertFalse(failed["updated"])
        saved = failed["runs"][0]
        self.assertEqual(saved["observation"]["state"], "unobserved")
        self.assertEqual(saved["observation"]["last_success_at"], old["observation"]["last_success_at"])
        for field in ("execution", "measurement", "specification", "source_commit", "data", "artifacts"):
            self.assertEqual(saved[field], old[field])
        self.assertEqual(failed["gateway"]["observation"]["last_success_at"], old["observation"]["last_success_at"])

    def test_changed_identity_rolls_back_entire_snapshot_and_preserves_old_run(self):
        old = self.refresh(snapshot_fixture(run_fixture()))["runs"][0]
        for field, value in (("source_commit", "b" * 40), ("configuration", "ac"),
                             ("original_project_id", "other-old-project")):
            changed = run_fixture()
            changed[field] = value
            with self.subTest(field=field):
                failed = self.refresh(snapshot_fixture(run_fixture("new-run"), changed))
                self.assertFalse(failed["updated"])
                self.assertEqual(failed["gateway"]["observation"]["error"]["code"], "ic_gateway_identity")
                self.assertEqual(len(failed["runs"]), 1)
                self.assertEqual(failed["runs"][0][field], old[field])

    def test_damaged_payloads_fail_closed_and_never_replace_cached_evidence(self):
        self.refresh(snapshot_fixture(run_fixture()))
        candidates = []
        bad = snapshot_fixture(run_fixture()); bad["runs"][0]["project_id"] = "foreign"; candidates.append(bad)
        bad = snapshot_fixture(run_fixture()); bad["runs"][0]["source_commit"] = "bad"; candidates.append(bad)
        bad = snapshot_fixture(run_fixture()); bad["runs"][0]["compiled_inputs_sha256"] = "x" * 64; candidates.append(bad)
        bad = snapshot_fixture(run_fixture()); bad["runs"][0]["waveform_run_id"] = "foreign"; candidates.append(bad)
        bad = snapshot_fixture(run_fixture()); bad["runs"][0]["execution"]["state"] = []; candidates.append(bad)
        bad = snapshot_fixture(run_fixture()); bad["runs"][0]["data"]["raw_retained"] = 1; candidates.append(bad)
        bad = snapshot_fixture(run_fixture()); bad["runs"][0]["specification"]["state"] = "pass"; candidates.append(bad)
        bad = snapshot_fixture(run_fixture()); bad["runs"][0]["specification"]["metrics"][0]["value"] = float("inf"); candidates.append(bad)
        bad = snapshot_fixture(run_fixture()); bad["runs"][0]["observation"]["observed_at"] = READ_AT; candidates.append(bad)
        bad = snapshot_fixture(run_fixture()); bad["runs"][0]["command"] = "PRIVATE"; candidates.append(bad)
        candidates += [snapshot_fixture(run_fixture(), run_fixture()), snapshot_fixture(*[run_fixture(str(i)) for i in range(26)])]
        for bad in candidates:
            with self.subTest(candidate=bad):
                failed = self.refresh(bad)
                self.assertFalse(failed["updated"])
                self.assertEqual(failed["runs"][0]["execution"]["state"], "succeeded")
                self.assertEqual(failed["runs"][0]["observation"]["state"], "unobserved")

    def test_artifact_urls_are_exact_current_run_metadata_only(self):
        for url in ("http://example.invalid/raw", "//example.invalid/raw", "/api/jobs/other-run/artifacts/waveforms.json",
                    "/api/jobs/fixture-native-run/artifacts/../run.json", "/api/jobs/fixture-native-run/artifacts/waveforms.json?download=1",
                    "/api/jobs/fixture-native-run/artifacts/%77aveforms.json"):
            run = run_fixture(); run["artifacts"][0]["url"] = url
            with self.subTest(url=url), self.assertRaises(DomainError):
                validate_supervision(snapshot_fixture(run), CONFIG["project_id"])

    def test_execution_verdict_requires_matching_stored_evidence(self):
        for claimed, raw, code in (("failed", "unknown", None), ("succeeded", "running", None),
                                    ("succeeded", "simulator_complete_pending_measurements", None),
                                    ("running", "running", 1), ("unknown", "simulator_failed", None)):
            run = run_fixture()
            run["execution"].update(state=claimed, raw_state=raw, exit_code=code)
            with self.subTest(claimed=claimed, raw=raw, code=code), self.assertRaises(DomainError):
                validate_supervision(snapshot_fixture(run), CONFIG["project_id"])
        for claimed, raw, code in (("failed", "unknown", 1), ("failed", "input_hash_failure", None),
                                    ("unknown", "unknown", None), ("running", "running", None)):
            run = run_fixture()
            run["execution"].update(state=claimed, raw_state=raw, exit_code=code)
            self.assertEqual(validate_supervision(snapshot_fixture(run), CONFIG["project_id"])["runs"][0]["execution"]["state"], claimed)

    def test_bounded_snapshot_absence_retains_old_run_with_unknown_observation(self):
        old = self.refresh(snapshot_fixture(run_fixture()))["runs"][0]
        snapshot = snapshot_fixture(); snapshot.update(scanned_records=200, truncated=True)
        result = self.refresh(snapshot)
        self.assertTrue(result["updated"])
        self.assertTrue(result["snapshot"]["truncated"])
        self.assertEqual(len(result["runs"]), 1)
        self.assertEqual(result["runs"][0]["observation"]["state"], "unobserved")
        self.assertEqual(result["runs"][0]["execution"], old["execution"])

    def test_submission_lookup_preserves_key_and_native_run_without_claiming_submit(self):
        key = "caller/key % title"
        with patch.object(LoopbackGatewayClient, "submission", return_value=lookup_fixture(key, run_fixture())) as lookup:
            result = self.backend.lookup_submission(CONFIG["id"], key, {"source_commit": "a" * 40})
        lookup.assert_called_once_with(key)
        self.assertTrue(result["found"])
        self.assertEqual(result["run_id"], "fixture-native-run")
        self.assertFalse(result["resubmit_authorized"])
        self.assertIsNone(self.backend.run(CONFIG["id"], result["run_id"])["submission_key"])
        with patch.object(LoopbackGatewayClient, "submission", return_value=lookup_fixture(key)):
            missing = self.backend.lookup_submission(CONFIG["id"], key)
        self.assertFalse(missing["found"])
        self.assertEqual(missing["last_known_run_id"], result["run_id"])
        self.assertFalse(missing["resubmit_authorized"])

    def test_lookup_corruption_and_disconnect_preserve_last_known_mapping(self):
        with patch.object(LoopbackGatewayClient, "submission", return_value=lookup_fixture(run=run_fixture())):
            self.backend.lookup_submission(CONFIG["id"], "fixture-key")
        wrong = lookup_fixture(run=run_fixture("other-run"))
        with patch.object(LoopbackGatewayClient, "submission", return_value=wrong):
            result = self.backend.lookup_submission(CONFIG["id"], "fixture-key")
        self.assertEqual(result["run_id"], "fixture-native-run")
        self.assertEqual(result["observation"]["state"], "unobserved")
        with patch.object(LoopbackGatewayClient, "submission", side_effect=DomainError("Gateway unavailable.", "ic_gateway_transport")):
            result = self.backend.lookup_submission(CONFIG["id"], "fixture-key")
        self.assertEqual(result["run_id"], "fixture-native-run")
        self.assertFalse(result["resubmit_authorized"])
        self.assertEqual(result["observation"]["state"], "unobserved")

    def test_lookup_identity_and_false_result_are_strict(self):
        good = lookup_fixture(run=run_fixture())
        candidates = [good | {"submission_key": "other-key"}, good | {"run_id": "other-run"},
                      good | {"found": False}, lookup_fixture() | {"run_id": "other-run"}, good | {"found": 1}]
        for value in candidates:
            with self.subTest(value=value), self.assertRaises(DomainError):
                validate_submission(value, "fixture-key", CONFIG["project_id"])
        with self.assertRaises(DomainError):
            validate_submission(good, "fixture-key", "different-project")
        with self.assertRaises(DomainError):
            validate_submission(good, "fixture-key", CONFIG["project_id"], {"source_commit": "b" * 40})

    def test_simultaneous_reads_are_rejected_before_network(self):
        with self.backend._read_lock(CONFIG["id"]), patch.object(LoopbackGatewayClient, "supervision") as read:
            with self.assertRaises(DomainError) as raised:
                ICGatewayBackend(self.temp.name).refresh(CONFIG["id"])
        self.assertEqual(raised.exception.code, "ic_gateway_busy")
        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()

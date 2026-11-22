"""Original synthetic verifier fixtures. No gateway, SSH or simulator needed."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("verify_ic_integration", ROOT / "scripts/verify_ic_integration.py")
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)
RUN = {"run_id": "synthetic-run", "job_id": "synthetic-run", "project_id": "former-name",
       "associated_project_id": "synthetic-project", "source_commit": "a" * 40,
       "state": "archived", "remote_state": "simulator_complete_pending_measurements"}
RESULT = {"job_id": "synthetic-run", "state": "collected", "summary": {"source_commit": "a" * 40},
          "artifacts": [{"name": "full-results.tar.gz", "bytes": 123, "sha256": "b" * 64,
                         "url": "/api/jobs/synthetic-run/artifacts/full-results.tar.gz"}]}


def snapshot():
    observed = "2026-10-01T00:00:00+00:00"
    run = {"run_id": "synthetic-run", "project_id": "synthetic-project", "original_project_id": "former-name",
           "source_commit": "a" * 40, "configuration": None, "compiled_inputs_sha256": None,
           "execution": {"state": "succeeded", "exit_code": 0, "raw_state": "simulator_complete_pending_measurements", "observed_at": observed},
           "observation": {"state": "recorded", "observed_at": observed},
           "data": {"state": "collected", "raw_retained": True},
           "specification": {"state": "reported", "metrics": [{"name": "synthetic-metric", "value": 3.2}]},
           "latest_observed_at": observed, "waveform_run_id": "synthetic-run",
           "artifacts": [{**deepcopy(RESULT["artifacts"][0]), "digest_source": "stored_record"}]}
    return {"schema": "ic.supervision.snapshot.v1", "project_id": "synthetic-project",
            "read_at": "2026-11-20T00:00:00+00:00", "source": "stored_observations", "single_site": True,
            "scanned_records": 1, "unavailable_records": 0, "truncated": False, "runs": [run]}


class FakeClient:
    origin = "http://127.0.0.1:18766"

    def __init__(self):
        self.requests = []
        self.values = {"/api/health": {"api_version": "1", "mode": "live", "mutation_token": "SYNTHETIC_SECRET"},
                       "/api/project": {"source_commit": "c" * 40, "projects": [{"id": "synthetic-project"}]},
                       "/api/jobs": {"jobs": [deepcopy(RUN)]},
                       "/api/jobs/synthetic-run/results": deepcopy(RESULT)}
        self.values["/api/supervision?project_id=synthetic-project"] = snapshot()

    def get(self, path):
        self.requests.append({"method": "GET", "path": "/api/submissions/<redacted>" if path.startswith("/api/submissions/") else path})
        return self.values[path]


class VerifierTests(unittest.TestCase):
    def test_historical_identity_and_missing_scientific_claim_preserved(self):
        report = verify.inventory(FakeClient())
        run = report["runs"][0]
        self.assertEqual(run["source_commit"], "a" * 40)
        self.assertEqual(report["current_source_commit"], "c" * 40)
        self.assertEqual(run["original_project_id"], "former-name")
        self.assertEqual(run["associated_project_id"], "synthetic-project")
        self.assertEqual(run["scientific_acceptance"], "not_evaluated_by_verifier")
        self.assertFalse(run["results"]["raw_data_validated"])
        self.assertNotIn("SYNTHETIC_SECRET", json.dumps(report))
        self.assertNotIn("mutation_token", json.dumps(report))

    def test_cross_run_late_response_and_source_mismatch_rejected(self):
        for change in ({"job_id": "another-run"}, {"summary": {"source_commit": "d" * 40}}):
            client = FakeClient()
            client.values["/api/jobs/synthetic-run/results"].update(change)
            with self.assertRaises(verify.VerificationError):
                verify.inventory(client)

    def test_artifact_link_cannot_change_run_or_trigger_download(self):
        client = FakeClient()
        client.values["/api/jobs/synthetic-run/results"]["artifacts"][0]["url"] = "/api/jobs/other/artifacts/full-results.tar.gz"
        with self.assertRaisesRegex(verify.VerificationError, "artifact_run_binding_mismatch"):
            verify.inventory(client)
        self.assertEqual(len(client.requests), 4)

    def test_unknown_is_not_failure(self):
        for state in ("unknown", "reconcile_required", "launch_outcome_unknown", "staging_outcome_unknown", None):
            run = {"state": state, "remote_state": state, "status_error": "synthetic disconnected"}
            self.assertEqual(verify.execution_outcome(run), "unknown")
        self.assertEqual(verify.execution_outcome({"remote_state": "simulator_failed"}), "failed")

    def test_active_paths_and_foreign_origin_rejected_without_network(self):
        with patch.object(verify.http.client, "HTTPConnection", side_effect=AssertionError("network forbidden")):
            client = verify.StoredGatewayClient()
            for path in ("/api/jobs/synthetic-run", "/api/jobs/synthetic-run/collect", "/api/preflight",
                         "/api/jobs?refresh=1", "/api/jobs/synthetic-run/results?probe=1", "https://example.invalid/"):
                with self.assertRaisesRegex(verify.VerificationError, "route_forbidden"):
                    client.get(path)
            for origin in ("http://localhost:8766", "https://127.0.0.1:8766", "http://127.0.0.1:8766/",
                           "http://127.0.0.1:8766@evil.example", "http://127.0.0.1:65536"):
                with self.assertRaises(verify.VerificationError):
                    verify.StoredGatewayClient(origin)

    def test_response_bounds_and_json_ambiguity(self):
        for payload in (b'{"ok":true,"ok":false,"data":{}}', b'{"ok":true,"data":{"x":NaN}}',
                        b'{"ok":true,"data":{"x":1e999}}',
                        b'{"ok":true,"data":[]}', b' ' * (verify.MAX_RESPONSE_BYTES + 1)):
            with self.assertRaises(verify.VerificationError):
                verify.decode_response(payload)

    def test_report_is_private_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            verify.write_report(path, {"synthetic": True})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                verify.write_report(path, {"synthetic": False})

    def test_new_snapshot_compares_stored_identity_and_keeps_observation_time(self):
        client = FakeClient()
        report = verify.supervision(client, verify.inventory(client))
        checked = report["snapshots"][0]
        self.assertEqual(checked["runs"][0]["observed_at"], "2026-10-01T00:00:00+00:00")
        self.assertNotEqual(checked["runs"][0]["observed_at"], checked["read_at"])
        self.assertEqual(checked["runs"][0]["specification"], "reported")
        self.assertTrue(checked["runs"][0]["compared_with_legacy"])
        self.assertEqual(report["submission_lookup"]["status"], "not_checked")

    def test_snapshot_late_cross_project_and_missing_known_run_rejected(self):
        for edit in (lambda s: s.update(project_id="other-project"),
                     lambda s: s["runs"][0].update(project_id="other-project"),
                     lambda s: s.update(runs=[])):
            client = FakeClient()
            edit(client.values["/api/supervision?project_id=synthetic-project"])
            with self.assertRaises(verify.VerificationError):
                verify.supervision(client, verify.inventory(client))

    def test_snapshot_cannot_promote_unknown_failure_or_collection_to_spec_pass(self):
        for edit in (lambda s: s["runs"][0]["execution"].update(state="failed", raw_state="unknown", exit_code=None),
                     lambda s: s["runs"][0]["specification"].update(state="pass"),
                     lambda s: s["runs"][0].update(waveform_run_id="other-run")):
            client = FakeClient()
            edit(client.values["/api/supervision?project_id=synthetic-project"])
            with self.assertRaises(verify.VerificationError):
                verify.supervision(client, verify.inventory(client))

    def test_repeated_existing_submission_lookup_only_reads_and_hides_key(self):
        client = FakeClient()
        key = "SYNTHETIC-existing-key"
        client.values["/api/submissions/" + key] = {"schema": "ic.submission.lookup.v1", "submission_key": key,
            "found": True, "run_id": "synthetic-run", "run": snapshot()["runs"][0]}
        with tempfile.TemporaryDirectory() as directory:
            index = Path(directory) / "keys.json"
            index.write_text(json.dumps({key: {"run_id": "synthetic-run", "fingerprint": "PRIVATE_UNUSED"}}))
            report = verify.supervision(client, verify.inventory(client), index)
        self.assertTrue(report["submission_lookup"]["repeated_same_key"])
        self.assertFalse(report["submission_lookup"]["submit_called"])
        self.assertEqual(sum(r["path"].startswith("/api/submissions/") for r in client.requests), 2)
        self.assertNotIn(key, json.dumps(report["submission_lookup"]))
        self.assertNotIn(key, json.dumps(report))
        self.assertNotIn("PRIVATE_UNUSED", json.dumps(report))

    def test_not_found_lookup_keeps_unknown_and_does_not_authorize_resubmission(self):
        value = {"schema": "ic.submission.lookup.v1", "submission_key": "synthetic-missing",
                 "found": False, "run_id": None, "run": None}
        result = verify.check_lookup(value, "synthetic-missing")
        self.assertEqual(result["execution_outcome"], "unknown")
        self.assertFalse(result["resubmission_authorized"])
        with self.assertRaisesRegex(verify.VerificationError, "known_submission_missing"):
            verify.check_lookup(value, "synthetic-missing", "already-known-run")

    def test_transport_lookup_uses_get_and_redacts_encoded_key_from_trace(self):
        key = "synthetic/key % fragment"
        route = "/api/submissions/" + verify.quote(key, safe="")
        with patch.object(verify.http.client, "HTTPConnection") as factory:
            connection = factory.return_value
            connection.getresponse.return_value.status = 200
            connection.getresponse.return_value.read.return_value = b'{"ok":true,"data":{}}'
            client = verify.StoredGatewayClient()
            client.get(route)
            self.assertEqual(connection.request.call_args.args, ("GET", route))
            self.assertEqual(client.requests, [{"method": "GET", "path": "/api/submissions/<redacted>"}])
            self.assertNotIn(key, json.dumps(client.requests))


if __name__ == "__main__":
    unittest.main()

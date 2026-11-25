#!/usr/bin/env python3
"""Read stored IC Gateway metadata without SSH, simulation or collection.

This opt-in developer verifier deliberately cannot call GET /api/jobs/<id>:
the legacy gateway uses that route for an active remote status check. Reports
contain projected identities and artifact metadata, never health tokens or
configuration/source contents. Live reports belong in ignored local storage.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import http.client
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import quote, unquote


MAX_RESPONSE_BYTES = 4 * 1024 * 1024
RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,180}\Z")
COMMIT = re.compile(r"[a-f0-9]{40}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")


class VerificationError(Exception):
    """Only constant, non-secret error codes are exposed to a report."""


def require(condition, code):
    if not condition:
        raise VerificationError(code)


def json_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def invalid_constant(_value):
    raise VerificationError("nonfinite_json_number")


def finite_float(value):
    parsed = float(value)
    require(math.isfinite(parsed), "nonfinite_json_number")
    return parsed


def decode_response(raw):
    require(len(raw) <= MAX_RESPONSE_BYTES, "response_too_large")
    try:
        result = json.loads(raw, object_pairs_hook=json_object, parse_constant=invalid_constant, parse_float=finite_float)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise VerificationError("invalid_json_response") from error
    require(isinstance(result, dict) and result.get("ok") is True, "invalid_gateway_envelope")
    require(isinstance(result.get("data"), dict), "invalid_gateway_data")
    return result["data"]


class StoredGatewayClient:
    """Fixed GET-only routes; direct loopback HTTP, no proxy or redirects."""

    def __init__(self, origin="http://127.0.0.1:8766", timeout=8):
        match = re.fullmatch(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})", origin)
        require(match is not None and 1 <= int(match[1]) <= 65535, "invalid_loopback_origin")
        require(type(timeout) in (int, float) and 0 < timeout <= 30, "invalid_timeout")
        self.origin, self.port, self.timeout = origin, int(match[1]), timeout
        self.requests = []

    def get(self, path):
        allowed = path in ("/api/health", "/api/project", "/api/jobs")
        allowed = allowed or re.fullmatch(r"/api/jobs/[A-Za-z0-9_-]{1,180}/results", path) is not None
        if path.startswith("/api/supervision?project_id="):
            encoded = path.removeprefix("/api/supervision?project_id=")
            decoded = unquote(encoded)
            allowed = 0 < len(decoded) <= 180 and encoded == quote(decoded, safe="")
        lookup = path.startswith("/api/submissions/")
        if lookup:
            encoded = path.removeprefix("/api/submissions/")
            decoded = unquote(encoded)
            allowed = 8 <= len(decoded) <= 160 and encoded == quote(decoded, safe="")
            allowed = allowed and all(ord(c) >= 32 and ord(c) != 127 for c in decoded)
        require(allowed, "active_or_unregistered_route_forbidden")
        self.requests.append({"method": "GET", "path": "/api/submissions/<redacted>" if lookup else path})
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=self.timeout)
        try:
            connection.request("GET", path, headers={"Origin": self.origin, "Accept": "application/json"})
            response = connection.getresponse()
            require(response.status == 200, "gateway_http_" + str(response.status))
            return decode_response(response.read(MAX_RESPONSE_BYTES + 1))
        except (OSError, http.client.HTTPException) as error:
            raise VerificationError("gateway_transport_unknown") from error
        finally:
            connection.close()


def execution_outcome(run):
    """Uncertain observations and archival state cannot establish failure."""
    native = run.get("remote_state")
    if native == "simulator_complete_pending_measurements":
        return "completed_pending_measurements"
    if native in ("simulator_failed", "input_hash_failure", "job_setup_failed"):
        return "failed"
    return "unknown"


def project_run(run, catalog_ids):
    require(isinstance(run, dict), "invalid_run_record")
    rid = run.get("run_id")
    require(isinstance(rid, str) and RUN_ID.fullmatch(rid), "invalid_run_identity")
    require(run.get("job_id") == rid, "job_run_identity_mismatch")
    source = run.get("source_commit")
    require(isinstance(source, str) and COMMIT.fullmatch(source), "invalid_source_commit")
    associated = run.get("associated_project_id")
    require(associated is None or associated in catalog_ids, "unknown_associated_project")
    return {"run_id": rid, "original_project_id": run.get("project_id"),
            "associated_project_id": associated, "source_commit": source,
            "configuration": run.get("configuration"), "state": run.get("state"),
            "remote_state": run.get("remote_state"), "execution_outcome": execution_outcome(run),
            "simulator_exit_code": run.get("simulator_exit_code"),
            "scientific_acceptance": "not_evaluated_by_verifier"}


def project_results(run, result):
    require(isinstance(result, dict) and result.get("job_id") == run["run_id"], "results_run_identity_mismatch")
    summary = result.get("summary") or {}
    require(isinstance(summary, dict), "invalid_result_summary")
    if summary.get("source_commit") is not None:
        require(summary["source_commit"] == run["source_commit"], "results_source_identity_mismatch")
    artifacts = result.get("artifacts", [])
    require(isinstance(artifacts, list) and len(artifacts) <= 32, "invalid_artifact_catalog")
    projected = []
    for artifact in artifacts:
        require(isinstance(artifact, dict), "invalid_artifact_metadata")
        name, size, digest = artifact.get("name"), artifact.get("bytes"), artifact.get("sha256")
        require(isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name), "invalid_artifact_name")
        require(type(size) is int and size >= 0, "invalid_artifact_size")
        require(digest is None or isinstance(digest, str) and HASH.fullmatch(digest), "invalid_artifact_digest")
        require(artifact.get("url") == "/api/jobs/" + run["run_id"] + "/artifacts/" + name,
                "artifact_run_binding_mismatch")
        projected.append({"name": name, "bytes": size, "sha256": digest,
                          "integrity": "declared_only" if digest else "not_provided"})
    return {"state": result.get("state"), "artifacts": projected,
            "artifact_downloaded": False, "raw_data_validated": False}


def inventory(client, max_runs=10):
    require(type(max_runs) is int and 1 <= max_runs <= 25, "invalid_max_runs")
    health = client.get("/api/health")
    require(health.get("api_version") == "1" and health.get("mode") == "live", "unsupported_gateway")
    projects, jobs = client.get("/api/project"), client.get("/api/jobs")
    catalog = projects.get("projects")
    require(isinstance(catalog, list) and len(catalog) <= 1000, "invalid_project_catalog")
    catalog_ids = {item.get("id") for item in catalog if isinstance(item, dict)}
    require(len(catalog_ids) == len(catalog) and all(isinstance(i, str) for i in catalog_ids),
            "invalid_or_duplicate_project_identity")
    records = jobs.get("jobs")
    require(isinstance(records, list) and len(records) <= 10000, "invalid_job_catalog")
    seen, runs = set(), []
    for record in records[:max_runs]:
        run = project_run(record, catalog_ids)
        require(run["run_id"] not in seen, "duplicate_run_identity")
        seen.add(run["run_id"])
        run["results"] = project_results(run, client.get("/api/jobs/" + run["run_id"] + "/results"))
        runs.append(run)
    return {"schema": "spiceowl.ic-integration-verification.v1", "mode": "stored_gateway_inventory",
            "read_at": datetime.now(timezone.utc).isoformat(), "gateway_origin": client.origin,
            "status": "passed", "project_count": len(catalog_ids), "stored_run_count": len(records),
            "project_ids": sorted(catalog_ids),
            "checked_run_count": len(runs), "truncated": len(records) > max_runs,
            "current_source_commit": projects.get("source_commit"), "runs": runs,
            "requests": client.requests,
            "remote_observation_performed": False, "submission_performed": False,
            "collection_performed": False, "artifact_download_performed": False,
            "claim": "Stored metadata compatibility only; not live solver or unattended execution acceptance."}


def check_supervision_run(run, expected_project, legacy=None):
    require(isinstance(run, dict), "invalid_supervision_run")
    rid = run.get("run_id")
    require(isinstance(rid, str) and RUN_ID.fullmatch(rid), "invalid_supervision_run_identity")
    require(run.get("project_id") == expected_project, "snapshot_cross_project_response")
    source = run.get("source_commit")
    require(source is None or isinstance(source, str) and COMMIT.fullmatch(source), "invalid_snapshot_commit")
    execution, observation = run.get("execution"), run.get("observation")
    data, specification = run.get("data"), run.get("specification")
    require(all(isinstance(value, dict) for value in (execution, observation, data, specification)), "missing_run_dimensions")
    require(execution.get("state") in ("queued", "running", "succeeded", "failed", "unknown"), "invalid_execution_state")
    require(observation.get("state") in ("recorded", "missing"), "invalid_observation_state")
    require(data.get("state") in ("not_collected", "collecting", "collected", "failed", "unknown"), "invalid_data_state")
    require(specification.get("state") in ("reported", "not_evaluated"), "specification_pass_invented")
    require(run.get("waveform_run_id") in (None, rid), "waveform_cross_run_binding")
    observed = run.get("latest_observed_at")
    require(execution.get("observed_at") == observation.get("observed_at") == observed, "observation_timestamp_mismatch")
    require((observation["state"] == "missing") == (observed is None), "observation_presence_mismatch")
    if execution["state"] == "succeeded":
        require(execution.get("exit_code") == 0 and execution.get("raw_state") == "simulator_complete_pending_measurements", "unproven_solver_success")
    if execution["state"] == "failed":
        code = execution.get("exit_code")
        require(type(code) is int and code != 0 or execution.get("raw_state") in
                ("simulator_failed", "input_hash_failure", "job_setup_failed", "preflight_failed"), "unknown_promoted_to_failed")
    compiled = run.get("compiled_inputs_sha256")
    require(compiled is None or isinstance(compiled, str) and HASH.fullmatch(compiled), "invalid_compiled_hash")
    if legacy is not None:
        require(source == legacy["source_commit"], "snapshot_source_rebound")
        require(run.get("original_project_id") == legacy["original_project_id"], "snapshot_original_project_changed")
        require(run.get("configuration") == legacy["configuration"], "snapshot_configuration_changed")
    artifacts = run.get("artifacts")
    require(isinstance(artifacts, list) and len(artifacts) <= 7, "invalid_snapshot_artifact_catalog")
    projected = project_results({"run_id": rid, "source_commit": source}, {"job_id": rid, "artifacts": artifacts})
    legacy_artifacts = {a["name"]: a for a in legacy["results"]["artifacts"]} if legacy else {}
    for artifact in artifacts:
        expected_origin = "stored_record" if artifact.get("sha256") is not None else "unavailable"
        require(artifact.get("digest_source") == expected_origin, "artifact_digest_source_mismatch")
        previous = legacy_artifacts.get(artifact["name"])
        if previous is not None:
            require(artifact["bytes"] == previous["bytes"], "artifact_size_changed_during_verification")
            if previous["sha256"] is not None:
                require(artifact["sha256"] == previous["sha256"], "artifact_digest_changed_during_verification")
    return {"run_id": rid, "project_id": expected_project, "source_commit": source,
            "original_project_id": run.get("original_project_id"), "execution": execution["state"],
            "observation": observation["state"], "observed_at": observed,
            "data": data["state"], "specification": specification["state"],
            "waveform_run_id": run.get("waveform_run_id"), "compiled_hash_present": compiled is not None,
            "artifact_count": len(projected["artifacts"]), "compared_with_legacy": legacy is not None}


def check_snapshot(snapshot, expected_project, legacy_by_id):
    require(isinstance(snapshot, dict) and snapshot.get("schema") == "ic.supervision.snapshot.v1", "unsupported_snapshot_schema")
    require(snapshot.get("project_id") == expected_project, "snapshot_cross_project_response")
    require(snapshot.get("source") == "stored_observations" and snapshot.get("single_site") is True, "unsupported_snapshot_source")
    require(type(snapshot.get("truncated")) is bool, "missing_snapshot_coverage")
    runs = snapshot.get("runs")
    require(isinstance(runs, list) and len(runs) <= 25, "snapshot_runs_exceed_bound")
    require(type(snapshot.get("scanned_records")) is int and 0 <= snapshot["scanned_records"] <= 200, "invalid_snapshot_scan_count")
    require(type(snapshot.get("unavailable_records")) is int and snapshot["unavailable_records"] >= 0, "invalid_unavailable_count")
    seen, checked = set(), []
    for run in runs:
        item = check_supervision_run(run, expected_project, legacy_by_id.get(run.get("run_id")))
        require(item["run_id"] not in seen, "duplicate_snapshot_run")
        seen.add(item["run_id"])
        checked.append(item)
    return {"project_id": expected_project, "read_at": snapshot.get("read_at"), "runs": checked,
            "truncated": snapshot["truncated"], "unavailable_records": snapshot["unavailable_records"]}


def check_lookup(value, key, expected_id=None):
    require(isinstance(value, dict) and value.get("schema") == "ic.submission.lookup.v1", "unsupported_lookup_schema")
    require(value.get("submission_key") == key, "lookup_key_binding_mismatch")
    require(type(value.get("found")) is bool, "invalid_lookup_found")
    if not value["found"]:
        require(value.get("run_id") is None and value.get("run") is None, "inconsistent_missing_lookup")
        require(expected_id is None, "known_submission_missing")
        return {"found": False, "execution_outcome": "unknown", "resubmission_authorized": False}
    rid = value.get("run_id")
    require(isinstance(rid, str) and RUN_ID.fullmatch(rid), "invalid_lookup_identity")
    require(isinstance(value.get("run"), dict) and value["run"].get("run_id") == rid, "lookup_run_binding_mismatch")
    require(expected_id is None or rid == expected_id, "lookup_changed_run_identity")
    return {"found": True, "run_id": rid, "resubmission_authorized": False}


def stored_submission(index_path):
    """Select one existing private mapping; never report its key or fingerprint."""
    path = Path(index_path)
    require(not path.is_symlink(), "submission_index_symlink")
    with path.open("rb") as handle:
        raw = handle.read(1024 * 1024 + 1)
    require(len(raw) <= 1024 * 1024, "submission_index_too_large")
    entries = json.loads(raw, object_pairs_hook=json_object, parse_constant=invalid_constant)
    require(isinstance(entries, dict), "invalid_submission_index")
    for key, entry in entries.items():
        if isinstance(key, str) and 8 <= len(key) <= 160 and isinstance(entry, dict):
            rid = entry.get("run_id")
            if isinstance(rid, str) and RUN_ID.fullmatch(rid):
                return key, rid
    raise VerificationError("no_stored_submission_key")


def supervision(client, report, submission_index=None):
    legacy = {run["run_id"]: run for run in report["runs"]}
    snapshots = []
    for project in report["project_ids"]:
        snapshot = client.get("/api/supervision?project_id=" + quote(project, safe=""))
        checked = check_snapshot(snapshot, project, legacy)
        expected = {run["run_id"] for run in legacy.values()
                    if (run["original_project_id"] if run["original_project_id"] in report["project_ids"]
                        else run["associated_project_id"]) == project}
        if not checked["truncated"] and checked["unavailable_records"] == 0:
            require(expected.issubset({run["run_id"] for run in checked["runs"]}), "snapshot_missing_known_run")
        snapshots.append(checked)
    lookup = {"status": "not_checked", "reason": "No existing private submission index was supplied."}
    if submission_index is not None:
        key, rid = stored_submission(submission_index)
        path = "/api/submissions/" + quote(key, safe="")
        first = check_lookup(client.get(path), key, rid)
        second = check_lookup(client.get(path), key, rid)
        require(first == second, "repeated_lookup_changed_binding")
        lookup = {"status": "passed", **first, "repeated_same_key": True, "submit_called": False}
    report.update(mode="stored_supervision_comparison", snapshots=snapshots, submission_lookup=lookup)
    return report


def write_report(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # A fresh path avoids silently replacing a previous private verification.
    with path.open("x", encoding="utf-8") as handle:
        if os.name != "nt":
            os.chmod(path, 0o600)
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-origin", default="http://127.0.0.1:8766")
    parser.add_argument("--max-runs", default=10, type=int)
    parser.add_argument("--supervision", action="store_true", help="Compare the published supervision contract with legacy stored results")
    parser.add_argument("--submission-index", type=Path, help="Optional existing private keys.json; use only with --supervision")
    parser.add_argument("--output", type=Path, help="Fresh private JSON report path; defaults below .spiceowl/integration")
    args = parser.parse_args(argv)
    if args.submission_index is not None and not args.supervision:
        parser.error("--submission-index requires --supervision")
    output = args.output or Path(".spiceowl/integration") / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    try:
        client = StoredGatewayClient(args.gateway_origin)
        report = inventory(client, args.max_runs)
        if args.supervision:
            report = supervision(client, report, args.submission_index)
    except VerificationError as error:
        report = {"schema": "spiceowl.ic-integration-verification.v1", "status": "failed",
                  "error_code": str(error), "execution_outcome": "unknown", "submission_performed": False}
    write_report(output, report)
    print(json.dumps({"status": report["status"], "checked_run_count": report.get("checked_run_count", 0),
                      "error_code": report.get("error_code"), "report": str(output.resolve())}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

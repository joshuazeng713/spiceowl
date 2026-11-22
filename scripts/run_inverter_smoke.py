#!/usr/bin/env python3
"""Explicit developer-only real inverter smoke steps; never auto-resubmit.

Run from a checkout with PYTHONPATH=src. Private plans, receipts and results
belong under the selected data directory, which must not be published.
"""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from datetime import datetime, timezone

import inverter_smoke_transport as transport
import inverter_smoke_results as results
from spiceowl.domain.common import require_id
from spiceowl.hosts import HostRegistry, profile_from_record


EXAMPLE = Path(__file__).resolve().parents[1] / "examples/inverter"
SOURCES = ("tb.scs", "models.scs", "inverter.scs")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def flattened(sources):
    text = sources["tb.scs"].decode("utf-8")
    for name in SOURCES[1:]:
        token = 'include "' + name + '"'
        if text.count(token) != 1:
            raise ValueError("Expected the two original inverter includes exactly once.")
        text = text.replace(token, sources[name].decode("utf-8"))
    if re.search(r"(?im)^\s*(?:include|ahdl_include)\b", text):
        raise ValueError("Unexpected include in the original source closure.")
    return text


def verify_bundle(folder, plan):
    provenance = json.loads((folder / "provenance.json").read_text())
    raw_acceptance = (folder / "source/acceptance.json").read_bytes()
    acceptance = json.loads(raw_acceptance)
    sources = {name: (folder / "source" / name).read_bytes() for name in SOURCES}
    hashes = {name: sha(raw) for name, raw in sources.items()}
    compiled = flattened(sources).encode("utf-8")
    if (hashes != acceptance["input_sha256"] or hashes != provenance["source_sha256"]
            or sha(raw_acceptance) != provenance["acceptance_sha256"]
            or plan["manifest_sha256"] != provenance["transport_manifest_sha256"]
            or sha(compiled) != provenance["flattened_sha256"]
            or compiled != base64.b64decode(plan["files"]["input.scs"], validate=True)):
        raise ValueError("Frozen source, acceptance or executed-input mapping changed.")
    transport._validate(plan)
    return acceptance, hashes


def prepare(args):
    require_id(args.run_key, "run key")
    folder = args.data_dir.resolve() / "smoke" / args.run_key
    if folder.exists():
        raise ValueError("Run key already exists. Use its saved plan and status; do not prepare it again.")
    registry = HostRegistry(args.data_dir)
    scan = registry.scan(args.hosts)
    recommendation = registry.recommend({"cpu_count": 1, "memory_bytes": 1024**3,
                                         "simulator": "spectre"}, args.hosts)
    selected = recommendation.get("recommended_host_id")
    if selected is None:
        raise ValueError("No eligible host in the fresh pool scan.")
    record = registry.inspect(selected)
    version = next(t["version"] for t in record["last_probe"]["tools"] if t["kind"] == "spectre")
    sources = {name: (EXAMPLE / name).read_bytes() for name in SOURCES}
    acceptance_raw = (EXAMPLE / "acceptance.json").read_bytes()
    acceptance = json.loads(acceptance_raw)
    hashes = {name: sha(raw) for name, raw in sources.items()}
    if hashes != acceptance["input_sha256"]:
        raise ValueError("Example sources differ from the frozen acceptance manifest.")
    compiled = flattened(sources)
    license_environment = json.loads(args.license_env_json.read_text()) if args.license_env_json else {}
    plan = transport.prepare(profile_from_record(record), args.run_key, compiled, version,
                             remote_python=args.remote_python, license_environment=license_environment)
    folder.mkdir(parents=True, mode=0o700)
    (folder / "source").mkdir(mode=0o700)
    for name, raw in {**sources, "acceptance.json": acceptance_raw}.items():
        (folder / "source" / name).write_bytes(raw)
    save(folder / "plan.json", plan)
    save(folder / "pool-scan.json", scan)
    save(folder / "placement.json", recommendation)
    save(folder / "provenance.json", {"schema": "spiceowl.inverter.bundle.v1",
         "frozen_at": datetime.now(timezone.utc).isoformat(), "source_sha256": hashes,
         "acceptance_sha256": sha(acceptance_raw), "flattened_sha256": sha(compiled.encode()),
         "transport_manifest_sha256": plan["manifest_sha256"], "data_directory": str(args.data_dir.resolve()),
         "source_origin": "original_toy_models", "resource_reservation": False})
    verify_bundle(folder, plan)
    return {"state": "prepared", "host_id": selected, "run_directory": str(folder),
            "version": version, "simulation_dispatched": False}


def execution_outcome(collection, contents):
    receipt = collection["receipt"]
    terminal = json.loads(contents["status.json"])
    if terminal != receipt.get("result"):
        raise ValueError("Collected terminal receipt differs from the observed result.")
    if terminal.get("state") in {"failed", "timed_out", "preflight_failed"}:
        return "failed"
    log = contents.get("spectre.log", b"").decode("utf-8", "replace")
    completion = re.search(r"spectre completes with\s+0 errors?", log, re.I)
    return "completed" if terminal.get("state") == "completed" and terminal.get("exit_code") == 0 and completion else "unknown"


def transient_file(contents):
    candidates = [name for name in ("results.raw/tran.tran", "results.raw/tran.tran.tran") if name in contents]
    if not candidates:
        return None
    if len(candidates) != 1:
        raise ValueError("Ambiguous transient artifacts; do not select by modification time.")
    if "results.raw/logFile" in contents:
        catalog = contents["results.raw/logFile"].decode("utf-8")
        entries = re.findall(r'"[^"\n]+"\s+"analysisInst"\s*\(\s*"tran"\s*"([^"\n]+)"\s*"PSF"', catalog)
        if entries != [candidates[0].split("/", 1)[1]]:
            raise ValueError("Native transient catalog does not uniquely identify the collected waveform.")
    return candidates[0]


def operate(args):
    folder = args.run_dir.resolve()
    plan = json.loads((folder / "plan.json").read_text())
    verify_bundle(folder, plan)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    if args.operation == "launch":
        provenance = json.loads((folder / "provenance.json").read_text())
        registry = HostRegistry(provenance["data_directory"])
        host = plan["manifest"]["profile"]["host_id"]
        if profile_from_record(registry.inspect(host)) != plan["manifest"]["profile"]:
            raise ValueError("Host profile changed since preparation.")
        observation = registry.resources(host)
        placement = registry.recommend({"cpu_count": 1, "memory_bytes": 1024**3,
                                        "simulator": "spectre"}, [host])
        save(folder / (stamp + "-prelaunch.json"), {"observation": observation, "placement": placement})
        if placement.get("recommended_host_id") != host:
            raise ValueError("Selected host no longer has the required observed headroom.")
    receipt = getattr(transport, args.operation)(plan)
    save(folder / (stamp + "-" + args.operation + ".json"), receipt)
    if args.operation != "collect" or "files" not in receipt:
        return receipt
    transport._verify_collection(plan, receipt)
    contents = {item["name"]: base64.b64decode(item["data_base64"], validate=True) for item in receipt["files"]}
    outcome = execution_outcome(receipt, contents)
    destination = folder / (stamp + "-collected")
    destination.mkdir(mode=0o700)
    for name, raw in contents.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    psf_name = transient_file(contents)
    report = {"state": receipt["receipt"]["state"], "execution_result": outcome,
              "collection_directory": str(destination), "files": len(contents),
              "total_bytes": receipt["total_bytes"], "input_and_manifest_verified": True}
    if psf_name is not None:
        acceptance, source_hashes = verify_bundle(folder, plan)
        raw_psf = contents[psf_name]
        output = destination / "analysis"
        output.mkdir(mode=0o700)
        waveform = None
        try:
            waveform = results.parse_psf_ascii(raw_psf.decode("utf-8"))
            validation = results.validate_waveform(waveform, acceptance, execution_result=outcome,
                                                   source_hashes=source_hashes)
        except (results.PsfError, UnicodeError) as exc:
            validation = {"execution_result": outcome, "measurement_validity": "invalid",
                          "toy_inverter_behavior": "not_evaluated", "parse_error": str(exc)}
        validation["provenance"] = {"origin": "real", "source_hashes": source_hashes,
            "acceptance_sha256": json.loads((folder / "provenance.json").read_text())["acceptance_sha256"],
            "psf_sha256": sha(raw_psf), "model_scope": "original educational MOS1; not a foundry PDK"}
        validation["artifacts"] = []
        if waveform is not None:
            try:
                results.export_csv(waveform, output / "waveforms.csv")
                validation["artifacts"].append("waveforms.csv")
                if args.plots:
                    results.export_plots(waveform, output, origin="real")
                    validation["artifacts"].extend(("waveforms.png", "waveforms.svg"))
            except (results.PsfError, RuntimeError) as exc:
                validation["export_error"] = str(exc)
        validation_path = output / "validation.json"
        validation["execution_evidence_source"] = "verified_remote_terminal_receipt_and_spectre_log"
        validation["provenance"]["transport_manifest_sha256"] = plan["manifest_sha256"]
        validation["provenance"]["executed_input_sha256"] = sha(contents["input.scs"])
        validation_path.write_text(json.dumps(validation, indent=2) + "\n")
        report.update({k: validation[k] for k in ("measurement_validity", "toy_inverter_behavior")})
        report["validation"] = str(validation_path)
    else:
        report.update(measurement_validity="invalid", toy_inverter_behavior="not_evaluated", error="transient_psf_missing")
    save(destination / "report.json", report)
    return report


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    prep = sub.add_parser("prepare", help="Scan selected registered hosts and freeze a local plan; no job launch.")
    prep.add_argument("--data-dir", type=Path, default=Path(".spiceowl"))
    prep.add_argument("--hosts", nargs="+", required=True)
    prep.add_argument("--run-key", required=True)
    prep.add_argument("--remote-python", required=True)
    prep.add_argument("--license-env-json", type=Path, help="Private JSON object with CDS_LIC_FILE and/or LM_LICENSE_FILE.")
    for operation in ("stage", "launch", "status", "collect"):
        step = sub.add_parser(operation)
        step.add_argument("--run-dir", type=Path, required=True)
        if operation == "collect":
            step.add_argument("--plots", action="store_true")
    args = parser.parse_args()
    report = prepare(args) if args.operation == "prepare" else operate(args)
    print(json.dumps(report, indent=2))
    return 2 if report.get("error") or report.get("state") in {"outcome_unknown", "unobserved", "rejected"} else 0


if __name__ == "__main__":
    sys.exit(main())

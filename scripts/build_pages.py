#!/usr/bin/env python3
"""Build synthetic console fixtures and approved workflow publications without operator state."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from spiceowl.web.showcase_assets import SHOWCASE_ASSETS

PUBLICATION_HASHES = {
    "bootstrap-dut.publication.scs": "808da690214b9e8ee1c0b2ce05c4dbf7eda69d6d71a198776d3cd5c53597b2c0",
    "inverter-dut.publication.scs": "d41d38c83de57359ea4bc9ecd07e0976cc4f9b2796e9eb8ceed235565e441db8",
    "bootstrap-tb.publication.scs": "d4f09ec705c26558648ac6131532cfb5e1e2e1df66bc44255645af0b888d2e0c",
    "bootstrap-evidence.publication.json": "17b278721a52a292232a7ea1454ee335d382a6ed35124ad5b28a171939ff69b4",
    "source-manifest.json": "676a0cf396562560b9c343dd165dc427c8a212b300081b59c36d57d01fdc0a70",
}
ASSETS = ("index.html", "app.js", "app.css", "owl.svg", "demo.js",
          "workflow.html", "workflow.js", "workflow.css", *PUBLICATION_HASHES, *SHOWCASE_ASSETS)
OUTPUT_FILES = frozenset((*ASSETS, "demo-data.js", "build.json", ".nojekyll", "LICENSE.txt"))
REPOSITORY = "https://github.com/spiceowl-dev/spiceowl"
CONNECTION_SCENARIOS = ("success", "inference_denied", "disconnect", "permission_denied")


def encoded(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True, allow_nan=False)


def validate_publications(assets):
    """Publish only the exact reviewed copies; keep embedded text identical."""
    for name, expected in PUBLICATION_HASHES.items():
        if hashlib.sha256(assets[name]).hexdigest() != expected:
            raise ValueError(f"Reviewed public source hash mismatch: {name}")
    manifest = json.loads(assets["source-manifest.json"])
    if manifest.get("publicationOnly") is not True or len(manifest["files"]) != 4:
        raise ValueError("Invalid reviewed publication manifest")
    for record in manifest["files"]:
        name = Path(record["path"]).name
        raw = assets[name]
        if hashlib.sha256(raw).hexdigest() != record["publicationSha256"]:
            raise ValueError("Publication manifest mismatch")
        if "originalSourceSha256" in record:
            if not raw.startswith(b"// PUBLICATION EXCERPT"):
                raise ValueError("Publication notice missing")
            if hashlib.sha256(raw.split(b"\n", 2)[2]).hexdigest() != record["originalSourceSha256"]:
                raise ValueError("Original source body hash mismatch")
    prefix = "export const PUBLICATION_SOURCES = Object.freeze("
    script = assets["workflow.js"].decode("utf-8")
    embedded = json.loads(script.split(prefix, 1)[1].split(");\n", 1)[0])
    if {record["filename"] for record in embedded} != set(PUBLICATION_HASHES) or len(embedded) != 5:
        raise ValueError("Workflow embedded sources exceed reviewed allowlist")
    for record in embedded:
        if record["text"].encode("utf-8") != assets[record["filename"]]:
            raise ValueError("Workflow embedded text differs from downloadable publication")


    evidence = json.loads(assets["showcase-evidence.json"])
    for name, digest in evidence["plots"].items():
        if name not in SHOWCASE_ASSETS or hashlib.sha256(assets[name]).hexdigest() != digest:
            raise ValueError("Visual evidence asset hash mismatch")
    if hashlib.sha256(assets["showcase-samples.json"]).hexdigest() != evidence["published_samples_sha256"]:
        raise ValueError("Visual evidence sample hash mismatch")


def compile_diagnosis(run, scenario):
    """Use the same fake events, proposal validation and policy as Controller."""
    from spiceowl.agent.connections.backends.fake import generate_events
    from spiceowl.agent.connections.config import validate_connection
    from spiceowl.agent.diagnosis import build_evidence, validate_proposal
    from spiceowl.recovery.policy import evaluate_policy

    connection = validate_connection({"connection_id": "pages-fixture", "label": "Scripted demo",
        "kind": "agent", "backend": "fake_agent", "scenario": scenario})
    evidence = build_evidence(run)
    compiled = {"evidence": evidence, "events": [], "proposal": None, "decision": None,
                "usage": {"cost": None}, "status": "outcome_unknown", "scripted": True}
    for event in generate_events(connection, evidence, "Explain this synthetic snapshot."):
        event = copy.deepcopy(event)
        if event["kind"] == "proposal.ready":
            proposal = validate_proposal(event["payload"], evidence)
            decision = evaluate_policy(proposal, run)
            compiled.update(proposal=proposal, decision=decision)
            event["payload"] = {"proposal": proposal, "decision": decision}
        elif event["kind"] == "usage.observed":
            compiled["usage"] = event["payload"]
        elif event["kind"] == "turn.terminal":
            compiled["status"] = event["payload"]["status"]
        compiled["events"].append(event)
    return compiled


def generate_public_data():
    """The only controller created here owns a fresh temporary directory."""
    from spiceowl.agent.connections.backends.fake import probe_connection
    from spiceowl.service import Controller, backend_config

    with tempfile.TemporaryDirectory(prefix="spiceowl-pages-") as directory:
        with Controller(directory) as app:
            initial = app.seed_demo()
            frames, diagnostics = {}, {}
            for first in initial["runs"]:
                run = copy.deepcopy(first)
                frames[run["run_id"]], diagnostics[run["run_id"]] = [], []
                while True:
                    frames[run["run_id"]].append(copy.deepcopy(run))
                    diagnostics[run["run_id"]].append({scenario: compile_diagnosis(run, scenario)
                                                     for scenario in CONNECTION_SCENARIOS})
                    if run["finished"]:
                        break
                    run = app.advance(run["run_id"])
            probes = {kind: {level: probe_connection(backend_config(next(c for c in initial["connections"] if c["kind"] == kind)), level)
                              for level in ("transport", "auth", "models")} for kind in ("agent", "model")}
    initial.update(mode="public_demo", public_demo=True, scripted=True, hosts=[], native_agents=[], questions=[], sessions=[],
                   capabilities={"real_inference": False, "real_simulation": False, "dispatch": False,
                                 "synthetic_replay": True, "ssh_connectivity_probe": False, "host_resources": False,
                                 "placement_plans": False, "mcp_local_queries": False})
    return {"schema": "spiceowl.pages-demo.v1", "initial_state": initial, "frames": frames,
            "diagnostics": diagnostics, "probes": probes}


def build(output=None, *, commit="local", repository_url=REPOSITORY, build_date=None, source_branch="main"):
    output = Path(output) if output is not None else ROOT / "dist/pages"
    if not re.fullmatch(r"local|[a-fA-F0-9]{40}", commit):
        raise ValueError("Build commit must be local or a full Git SHA.")
    if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository_url):
        raise ValueError("Use a public GitHub repository URL without credentials or query parameters.")
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,179}", source_branch)
            or ".." in source_branch or "//" in source_branch or source_branch.endswith("/")):
        raise ValueError("Use an explicit public source branch name.")
    build_date = build_date or datetime.now(timezone.utc).isoformat(timespec="seconds")
    if datetime.fromisoformat(build_date).utcoffset() is None:
        raise ValueError("The build date needs an explicit timezone.")
    if output.is_symlink() or output.exists() and not output.is_dir():
        raise ValueError("Select a normal output directory.")
    output = output.resolve()
    if output in (ROOT, ROOT / "src", ROOT / "src/spiceowl/web/static"):
        raise ValueError("The output must not replace project sources.")
    if output.exists() and any(p.name not in OUTPUT_FILES or not p.is_file() or p.is_symlink() for p in output.iterdir()):
        raise ValueError("Output contains files outside the static demo allowlist; select an empty build directory.")
    source = ROOT / "src/spiceowl/web/static"
    assets = {}
    for name in ASSETS:
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("A required public static asset is missing or is a symlink.")
        assets[name] = path.read_bytes()
    validate_publications(assets)
    license_path = ROOT / "LICENSE"
    if license_path.is_symlink() or not license_path.is_file():
        raise ValueError("The public MIT license is missing or is a symlink.")
    assets["LICENSE.txt"] = license_path.read_bytes()
    data = generate_public_data()
    source_digest = hashlib.sha256(encoded(data).encode())
    for name, contents in sorted(assets.items()):
        source_digest.update(name.encode() + b"\0" + contents)
    source_digest.update(encoded([commit, repository_url, build_date, source_branch]).encode())
    metadata = {"commit": commit.lower(), "build_date": build_date, "repository_url": repository_url,
                "build_id": source_digest.hexdigest()[:24], "mode": "public_synthetic_demo", "source_branch": source_branch,
                "workflow_mode": "reviewed_publication_static", "workflow_live_execution": False}
    data["metadata"] = metadata
    index = assets["index.html"].decode("utf-8")
    if index.count("<html") != 1 or "data-public-demo=" in index:
        raise ValueError("The local index must have one ordinary HTML root.")
    index = index.replace("<html", '<html data-public-demo="true"', 1)
    policy = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'"
    index = index.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n  <meta http-equiv="Content-Security-Policy" content="' + policy + '">', 1)
    for name in ("app.js", "app.css", "owl.svg"):
        index = index.replace('"/' + name + '"', '"./' + name + '"')
    match = list(re.finditer(r'<script\b[^>]*\bsrc="(?:\./)?app\.js"[^>]*></script>', index))
    if len(match) != 1 or "demo-data.js" in index or 'src="./demo.js"' in index:
        raise ValueError("The local index must contain one app.js entry and no injected demo scripts.")
    insertion = '<script src="./demo-data.js"></script>\n  <script src="./demo.js"></script>\n  '
    index = index[:match[0].start()] + insertion + index[match[0].start():]
    assets["index.html"] = index.encode("utf-8")
    assets["demo-data.js"] = ("window.__SPICEOWL_DEMO_DATA__=" + encoded(data).replace("<", "\\u003c") + ";\n").encode()
    assets["build.json"] = (json.dumps(metadata, indent=2) + "\n").encode()
    assets[".nojekyll"] = b""
    output.mkdir(parents=True, exist_ok=True)
    for name, content in assets.items():
        (output / name).write_bytes(content)
    return {"output": str(output), "files": sorted(assets), "run_count": len(data["frames"]),
            "frame_count": sum(map(len, data["frames"].values())), "metadata": metadata}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/pages")
    parser.add_argument("--commit", default="local")
    parser.add_argument("--repository-url", default=REPOSITORY)
    parser.add_argument("--source-branch", default="main")
    parser.add_argument("--build-date", help="Optional ISO timestamp with timezone.")
    args = parser.parse_args(argv)
    result = build(args.output, commit=args.commit, repository_url=args.repository_url, build_date=args.build_date,
                   source_branch=args.source_branch)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

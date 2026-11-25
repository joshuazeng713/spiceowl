#!/usr/bin/env python3
"""Cross-platform full verification runner for macOS, Linux, and Windows.

Runs SpiceOwl's complete offline test workflow in one command so new developers,
users, and CI pipelines can verify every functional area consistently across
operating systems without touching real SSH servers, simulators, or credentials.

Usage:
    python3 scripts/run_full_tests.py          # macOS / Linux
    py -3 scripts\\run_full_tests.py           # Windows (PowerShell / cmd)
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

QUICK_SUITES = (
    "tests.test_cross_platform",
    "tests.test_service",
    "tests.test_http",
    "tests.test_ssh_hosts",
    "tests.test_direct_execution",
    "tests.test_bounded_recovery",
    "tests.test_remote_monitor",
    "tests.test_codex_bridge",
    "tests.test_codex_http",
    "tests.test_dev_setup",
)

NODE_UI_TESTS = (
    "tests/ui-host-onboarding.mjs",
    "tests/ui-agent-channel.mjs",
    "tests/workflow-page.mjs",
)


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    src_str = str(SRC)
    env["PYTHONPATH"] = f"{src_str}{os.pathsep}{existing}" if existing else src_str
    env.setdefault("MPLBACKEND", "Agg")
    env.setdefault("PYTHONUTF8", "1")
    return env


def _run_step(name: str, command: list[str], *, env: dict[str, str] | None = None,
              timeout: int = 600, cwd: Path = ROOT, display_command: str | None = None) -> dict:
    started = time.monotonic()
    shown = display_command if display_command is not None else " ".join(command)
    print(f"\n==> [{name}] {shown}", flush=True)
    proc = subprocess.run(
        command,
        cwd=str(cwd),
        env=env or _child_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    elapsed = round(time.monotonic() - started, 2)
    output = proc.stdout or ""
    lines = output.strip().splitlines()
    tail = "\n".join(lines[-25:]) if lines else ""
    passed = proc.returncode == 0
    status = "passed" if passed else "failed"
    print(f"    [{status.upper()}] ({elapsed}s)", flush=True)
    if not passed and tail:
        print(tail, flush=True)
    return {
        "name": name,
        "status": status,
        "returncode": proc.returncode,
        "duration_seconds": elapsed,
        "command": command,
        "output_tail": tail,
    }


def _smoke_cli_and_web(python_exe: str) -> dict:
    """Verify CLI entrypoint, static assets, and live loopback web console end-to-end."""
    name = "cli_and_web_loopback_smoke"
    smoke_code = r"""
import json, tempfile, threading
from importlib.resources import files
from urllib.request import urlopen
from spiceowl import __version__
from spiceowl.service import Controller
from spiceowl.web.server import make_server

assets = files("spiceowl.web").joinpath("static")
for asset in ("index.html", "app.js", "app.css", "owl.svg"):
    assert assets.joinpath(asset).is_file(), f"Missing static asset: {asset}"

with tempfile.TemporaryDirectory(prefix="spiceowl-smoke-") as temp:
    controller = Controller(temp, agent_mode="standalone")
    try:
        controller.seed_demo()
        server = make_server(controller, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            base = f"http://127.0.0.1:{port}"
            with urlopen(base + "/", timeout=5) as resp:
                assert resp.status == 200
                html = resp.read().decode("utf-8")
                assert "SpiceOwl" in html
            for path in ("/api/state", "/api/direct/runs", "/api/direct/recovery-reports"):
                with urlopen(base + path, timeout=5) as resp:
                    assert resp.status == 200
                    payload = json.loads(resp.read().decode("utf-8"))
                    assert isinstance(payload, dict)
        finally:
            server.shutdown()
            server.server_close()
    finally:
        controller.close()
print(json.dumps({"ok": True, "version": __version__}))
"""
    return _run_step(
        name, [python_exe, "-c", smoke_code], timeout=60,
        display_command="CLI version, packaged web assets, and loopback HTTP smoke",
    )


def _check_optional_extras(python_exe: str, *, require_extras: bool) -> list[dict]:
    results = []
    env = _child_env()
    for extra, probe_mod, unittest_args in (
        ("adctools", "adctoolbox", ["-m", "unittest", "tests.test_adctools", "tests.test_ac_dc"]),
        ("mcp", "mcp", ["-m", "unittest", "discover", "-s", "tests", "-p", "test_mcp.py"]),
    ):
        probe = subprocess.run(
            [python_exe, "-c", f"import {probe_mod}"],
            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if probe.returncode != 0:
            status = "failed" if require_extras else "skipped"
            msg = f"Optional extra '[{extra}]' is not installed in {python_exe}."
            print(f"\n==> [extra:{extra}] {status.upper()} - {msg}", flush=True)
            results.append({
                "name": f"extra:{extra}",
                "status": status,
                "returncode": 1 if require_extras else 0,
                "duration_seconds": 0.0,
                "command": [python_exe, "-c", f"import {probe_mod}"],
                "output_tail": msg,
            })
            continue
        results.append(_run_step(f"extra:{extra}", [python_exe, *unittest_args], timeout=180))
    return results


def _run_node_ui_tests(*, require_node: bool) -> list[dict]:
    node = shutil.which("node")
    if not node:
        status = "failed" if require_node else "skipped"
        msg = "Node.js not found on PATH; skipping offline JS UI tests."
        print(f"\n==> [ui_js] {status.upper()} - {msg}", flush=True)
        return [{
            "name": "ui_js",
            "status": status,
            "returncode": 1 if require_node else 0,
            "duration_seconds": 0.0,
            "command": ["node"],
            "output_tail": msg,
        }]
    return [_run_step(f"ui:{Path(script).stem}", [node, str(ROOT / script)], timeout=60) for script in NODE_UI_TESTS]


def _run_pages_build(python_exe: str, *, with_node_verify: bool = True) -> list[dict]:
    results = []
    with tempfile.TemporaryDirectory(prefix="spiceowl-pages-check-") as temp:
        out = Path(temp) / "site"
        build_step = _run_step("pages_static_build", [python_exe, str(ROOT / "scripts" / "build_pages.py"), "--output", str(out)], timeout=120)
        results.append(build_step)
        node = shutil.which("node")
        if with_node_verify and build_step["status"] == "passed" and node:
            results.append(_run_step("pages_demo_verify", [node, str(ROOT / "tests" / "pages-demo.mjs"), str(out)], timeout=60))
    return results


def _select_python(explicit: str | None) -> str:
    if explicit:
        return str(Path(explicit).expanduser().resolve())
    return sys.executable


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", help="Explicit Python interpreter to run test suites with (defaults to current Python).")
    parser.add_argument("--quick", action="store_true", help="Run fast cross-platform guard + core suites instead of full discovery.")
    parser.add_argument("--require-extras", action="store_true", help="Fail if optional [adctools] or [mcp] extras are not installed.")
    parser.add_argument("--require-node", action="store_true", help="Fail if Node.js is not available for offline frontend tests.")
    parser.add_argument("--skip-docs", action="store_true", help="Skip link checker and static pages build.")
    parser.add_argument("--report", type=Path, default=ROOT / ".spiceowl" / "test-report.json",
                        help="Path to write the structured JSON test summary report.")
    args = parser.parse_args(argv)

    python_exe = _select_python(args.python)
    started = time.monotonic()
    steps: list[dict] = []

    print(f"SpiceOwl Cross-Platform Test Workflow | OS={platform.system()} ({sys.platform}) | Python={platform.python_version()}")
    print(f"Interpreter: {python_exe}")

    # 1. Cross-platform primitives & AST static POSIX guardrail
    steps.append(_run_step("cross_platform_guard", [python_exe, "-m", "unittest", "tests.test_cross_platform", "-v"], timeout=120))

    # 2. Full Python unit test suite (or quick core subset)
    if args.quick:
        steps.append(_run_step("python_core_suite", [python_exe, "-m", "unittest", *QUICK_SUITES], timeout=300))
    else:
        steps.append(_run_step("python_full_suite", [python_exe, "-m", "unittest", "discover", "-s", "tests"], timeout=600))

    # 3. CLI & live loopback web console smoke
    steps.append(_smoke_cli_and_web(python_exe))

    # 4. Optional extras verification (adctools + mcp)
    steps.extend(_check_optional_extras(python_exe, require_extras=args.require_extras))

    # 5. Offline frontend JavaScript tests
    steps.extend(_run_node_ui_tests(require_node=args.require_node))

    # 6. Documentation links & static Pages build
    if not args.skip_docs:
        steps.append(_run_step("docs_link_check", [python_exe, str(ROOT / "scripts" / "check_links.py")], timeout=120))
        steps.extend(_run_pages_build(python_exe))

    total_elapsed = round(time.monotonic() - started, 2)
    failed = [s for s in steps if s["status"] == "failed"]
    skipped = [s for s in steps if s["status"] == "skipped"]
    passed = [s for s in steps if s["status"] == "passed"]

    report = {
        "schema": "spiceowl.test-report.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "os": platform.system(),
        "platform": platform.platform(),
        "sys_platform": sys.platform,
        "python": platform.python_version(),
        "interpreter": python_exe,
        "mode": "quick" if args.quick else "full",
        "overall": "passed" if not failed else "failed",
        "duration_seconds": total_elapsed,
        "counts": {"passed": len(passed), "failed": len(failed), "skipped": len(skipped)},
        "steps": steps,
    }

    report_path = args.report.expanduser()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("\n" + "=" * 72)
    print(f"SUMMARY ({report['os']} | Python {report['python']} | {total_elapsed}s)")
    print("-" * 72)
    for step in steps:
        print(f"  {step['status'].upper():<7} {step['name']:<32} {step['duration_seconds']:>6.2f}s")
    print("-" * 72)
    print(f"Passed: {len(passed)}  Failed: {len(failed)}  Skipped: {len(skipped)}")
    print(f"Report: {report_path}")
    print("=" * 72)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

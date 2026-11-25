"""Simulation engine CLI; CircuitGit owns the Codex agent conversation."""

import argparse
import json
import os
from pathlib import Path
import sys
import time

from spiceowl import __version__
from spiceowl._platform import default_state_dir
from spiceowl.agent_channel import agent_channel_error, agent_channel_info
from spiceowl.domain.common import DomainError, new_id
from spiceowl.service import Controller
from spiceowl.storage.sqlite import Store


def parser():
    result = argparse.ArgumentParser(prog="spiceowl", description="Simulation engine SDK/CLI with standalone Codex and embedded CircuitGit Agent modes.")
    result.add_argument("--version", action="version", version=f"SpiceOwl {__version__}")
    default = default_state_dir("spiceowl")
    result.add_argument("--data-dir", default=str(default), help="Local persistent state directory (put before the command).")
    commands = result.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Open the standalone Codex/engine console, or explicitly select CircuitGit embedding.")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument('--agent-mode', choices=('standalone', 'embedded_in_circuitgit'), default='standalone',
                       help='Standalone owns one explicitly connected Codex window; embedded starts no Agent.')
    serve.add_argument('--codex-executable', help='Operator-selected local Codex executable; otherwise resolve codex on PATH at Connect.')
    serve.add_argument('--codex-model', help='Optional explicit native model; no provider/model fallback.')
    serve.add_argument('--pdk-lut', help='Operator-selected existing private PDK SQLite LUT for read-only Agent queries.')
    serve.add_argument('--circuitgit-client-config', help='Explicit existing CircuitGit client.json; reuse its Service and human review.')
    serve.add_argument('--circuitgit-sdk-path', help='Explicit parent of ic_gateway when it is not installed in this Python environment.')
    serve.add_argument("--demo", action="store_true", help="Idempotently add synthetic demo data.")
    serve.add_argument("--live-client-config", help="Private CircuitGit simulation client configuration; enables a separate real simulation panel.")
    serve.add_argument("--enable-direct-exec", action="store_true", help="Expose standalone SSH Direct / LSF / Slurm execution tools to the local Codex window (still requires human tool approval).")
    commands.add_parser("demo", help="Create the synthetic demo workspace.")
    init_cmd = commands.add_parser("init", help="Cross-platform quickstart wizard: check local OS/SSH, bootstrap vivid demos, or register & prepare your first EDA simulation.")
    init_cmd.add_argument("--check", action="store_true", help="Inspect local OS, OpenSSH, ~/.ssh/config aliases, and workspace readiness.")
    init_cmd.add_argument("--demo", action="store_true", help="Bootstrap the full vivid offline demo workspace (overview + 500-cycle PSF Binary + 27-corner PVT + 40-sample MC + Agent Eval).")
    init_cmd.add_argument("--host-id", help="Register an EDA server with this stable host ID.")
    init_cmd.add_argument("--label", help="Display label for the EDA server.")
    init_cmd.add_argument("--ssh-target", help="Existing OpenSSH alias from ~/.ssh/config (e.g. eda-sim).")
    init_cmd.add_argument("--expected-host", help="Exact remote hostname returned by 'hostname'.")
    init_cmd.add_argument("--expected-user", help="Exact remote username returned by 'id -un'.")
    init_cmd.add_argument("--simulator", choices=("spectre", "ngspice"), default="spectre")
    init_cmd.add_argument("--simulator-path", help="Remote absolute path to spectre or ngspice executable.")
    init_cmd.add_argument("--probe", action="store_true", help="Actively verify SSH connectivity and simulator version right after registering.")
    init_cmd.add_argument("--prepare-smoke", action="store_true", help="Freeze a zero-PDK RC smoke simulation targeting the registered EDA host.")
    init_cmd.add_argument("--backend", choices=("ssh_direct", "lsf", "slurm"), default="ssh_direct")
    init_cmd.add_argument("--queue", help="LSF queue name (if --backend lsf).")
    init_cmd.add_argument("--partition", help="Slurm partition name (if --backend slurm).")
    init_cmd.add_argument("--json", action="store_true", help="Print structured JSON output.")
    na_cmd = commands.add_parser("next-action", help="Evaluate an experiment/run snapshot JSON with the deterministic next-action orchestrator.")
    na_cmd.add_argument("--state-json", required=True, help="JSON file describing experiment/dependencies/plan/run/measurement state.")
    ev_cmd = commands.add_parser("eval-agent", help="Run the frozen 10-case Agent end-to-end evaluation benchmark.")
    ev_cmd.add_argument("--baseline", choices=("deterministic", "weak_naive", "compare"), default="compare")
    ev_cmd.add_argument("--repeats", type=int, default=1)
    ev_cmd.add_argument("--markdown", action="store_true", help="Print the human-readable Markdown scorecard.")
    direct = commands.add_parser("direct", help="Run simulations directly over SSH, LSF, or Slurm without a CircuitGit gateway.")
    direct_actions = direct.add_subparsers(dest="action", required=True)
    d_prep = direct_actions.add_parser("prepare", help="Freeze netlist, support files, and host/scheduler plan locally without launching.")
    d_prep.add_argument("--run-key", required=True, help="Stable unique run/submission key.")
    d_prep.add_argument("--netlist", required=True, help="Local Spectre or ngspice netlist file.")
    d_prep.add_argument("--support", action="append", default=[], help="Optional support file as RELATIVE_PATH=LOCAL_FILE (repeatable).")
    d_prep.add_argument("--host", help="Explicit registered host ID.")
    d_prep.add_argument("--hosts", nargs="+", help="Registered host pool IDs for resource-aware host recommendation.")
    d_prep.add_argument("--scan-hosts", action="store_true", help="Actively scan --hosts before selecting a host.")
    d_prep.add_argument("--backend", choices=("ssh_direct", "lsf", "slurm"), default="ssh_direct")
    d_prep.add_argument("--simulator", choices=("spectre", "ngspice"), default="spectre")
    d_prep.add_argument("--output-format", choices=("psfascii", "psfbin", "psfxl"), default="psfascii")
    d_prep.add_argument("--expected-version", help="Explicit expected simulator version token.")
    d_prep.add_argument("--no-pin-version", action="store_true", help="Do not auto-pin the last probed simulator version.")
    d_prep.add_argument("--cpus", type=int, default=1)
    d_prep.add_argument("--memory-gib", type=float, default=4.0)
    d_prep.add_argument("--wall-seconds", type=int, default=3600)
    d_prep.add_argument("--license-queue-seconds", type=int, default=120)
    d_prep.add_argument("--enforce-rlimit-as", action="store_true")
    d_prep.add_argument("--include-dir", action="append", default=[], help="Remote absolute include directory passed via -I (repeatable).")
    d_prep.add_argument("--remote-python", default="/usr/bin/python3")
    d_prep.add_argument("--remote-root", default=".spiceowl-runs")
    d_prep.add_argument("--license-env-json", help="Private JSON file with CDS_LIC_FILE / LM_LICENSE_FILE / CDS_AUTO_64BIT.")
    d_prep.add_argument("--source-commit", help="Optional lowercase 40-character Git SHA for provenance.")
    d_prep.add_argument("--queue", help="LSF queue name (required for --backend lsf).")
    d_prep.add_argument("--project", help="Optional LSF project name.")
    d_prep.add_argument("--job-group", help="Optional LSF job group path (e.g. /analog/sim).")
    d_prep.add_argument("--partition", help="Slurm partition name (required for --backend slurm).")
    d_prep.add_argument("--account", help="Optional Slurm account.")
    d_prep.add_argument("--qos", help="Optional Slurm QoS.")
    d_prep.add_argument("--exclusive", action="store_true", help="Request exclusive node allocation on LSF or Slurm.")
    d_prep.add_argument("--enable-recovery", action="store_true", help="Enable bounded M4 recovery policy for license/OOM operational failures.")
    d_prep.add_argument("--max-recovery-attempts", type=int, default=2)
    d_prep.add_argument("--max-memory-gib", type=float, default=64.0)
    d_prep.add_argument("--license-backoff-seconds", type=int, default=300)
    direct_actions.add_parser("runs", help="List locally recorded direct-execution runs.")
    for name in ("inspect", "stage", "launch", "status", "cancel", "collect", "report", "watch", "recover"):
        cmd = direct_actions.add_parser(name, help={
            "inspect": "Read local run state without contacting the remote host.",
            "stage": "Write frozen inputs into the isolated remote run directory.",
            "launch": "Claim and dispatch at most one remote execution.",
            "status": "Query remote process or scheduler state without re-launching.",
            "cancel": "Cancel only the verified owned process group or scheduler job.",
            "collect": "Collect verified terminal artifacts and build the five-dimensional report.",
            "report": "Read the collected five-dimensional simulation report.",
            "watch": "Poll an existing run via RuntimeMonitor until terminal or --max-wait; Ctrl-C never cancels the remote job.",
            "recover": "Evaluate bounded M4 recovery policy and optionally spawn/launch a child attempt with a saved recovery report.",
        }[name])
        cmd.add_argument("run_id")
        if name == "launch":
            cmd.add_argument("--skip-resource-recheck", action="store_true", help="Skip pre-launch host headroom recheck.")
        elif name == "cancel":
            cmd.add_argument("--action-key", help="Explicit stable cancel action key.")
        elif name == "watch":
            cmd.add_argument("--interval", type=float, default=15.0)
            cmd.add_argument("--max-wait", type=float, default=600.0)
            cmd.add_argument("--allow-collect", action="store_true", help="Collect terminal artifacts automatically once confirmed terminal.")
        elif name == "recover":
            cmd.add_argument("--dry-run", action="store_true", help="Evaluate policy and create child plan without staging or launching.")
    d_lookup = direct_actions.add_parser("lookup", help="Look up a direct submission key locally without replaying.")
    d_lookup.add_argument("--key", required=True)
    d_rec_list = direct_actions.add_parser("recovery-reports", help="Search and list persisted bounded recovery reports.")
    d_rec_list.add_argument("--query", help="Filter by run ID, host, failure class, or summary.")
    d_rec_list.add_argument("--outcome", choices=("recovered", "in_progress", "exhausted", "not_recoverable", "policy_disabled", "completed_first_try"))
    d_rec_get = direct_actions.add_parser("recovery-report", help="Read one persisted recovery report by root or child run ID.")
    d_rec_get.add_argument("run_id")
    d_rec_get.add_argument("--markdown", action="store_true", help="Print the human-readable Markdown report.")
    d_demo_wf = direct_actions.add_parser("demo-waveform", help="Create a synthetic multi-cycle PSF Binary demo run with a hidden glitch for offline inspection.")
    d_demo_wf.add_argument("--run-id", default="demo-psfbin-500c")
    d_demo_wf.add_argument("--cycles", type=int, default=500)
    d_demo_wf.add_argument("--glitch-cycle", type=int, default=317)
    d_wf = direct_actions.add_parser("waveforms", help="List indexed waveform datasets (PSF Binary / PSFXL / ASCII) for a direct run.")
    d_wf.add_argument("run_id")
    d_wf.add_argument("--refresh-remote", action="store_true", help="Actively re-index results.raw in place on the remote host.")
    d_wfq = direct_actions.add_parser("waveform-query", help="Query a waveform window (Min-Max envelope preview or automatic exact_window).")
    d_wfq.add_argument("run_id")
    d_wfq.add_argument("--dataset", required=True, help="Dataset relative path inside results.raw (e.g. tran.tran.tran).")
    d_wfq.add_argument("--signals", nargs="+", required=True, help="One or more signal names to extract.")
    d_wfq.add_argument("--x-min", type=float)
    d_wfq.add_argument("--x-max", type=float)
    d_wfq.add_argument("--max-points", type=int, default=2000)
    d_wfq.add_argument("--exact", action="store_true", help="Require 100% lossless exact samples (refuses downsampling).")
    d_wfc = direct_actions.add_parser("waveform-cycles", help="Run Cycle Slicer, per-cycle trend metrics, worst-case detection, and cycle overlay.")
    d_wfc.add_argument("run_id")
    d_wfc.add_argument("--dataset", required=True)
    d_wfc.add_argument("--signals", nargs="+", required=True)
    d_wfc.add_argument("--period", type=float, help="Fixed cycle period in sweep units.")
    d_wfc.add_argument("--clock", help="Clock signal name for edge-based cycle slicing.")
    d_wfc.add_argument("--threshold", type=float, help="Optional clock crossing threshold (defaults to midpoint).")
    d_wfc.add_argument("--edge", choices=("rising", "falling"), default="rising")
    d_wfc.add_argument("--t-start", type=float)
    d_wfc.add_argument("--t-end", type=float)
    d_wfc.add_argument("--worst-metric", choices=("peak_to_peak", "max_value", "min_value", "overshoot", "period_deviation"), default="peak_to_peak")
    d_wfc.add_argument("--worst-signal", help="Signal scored for worst-case cycle detection (defaults to first signal).")
    d_wfc.add_argument("--cycle-index", type=int, help="Optional 0-based cycle index to drill down into.")
    d_wfc.add_argument("--context-cycles", type=int, default=0)
    d_wfc.add_argument("--overlay-cycles", type=int, default=10)
    d_wfc.add_argument("--max-points-per-cycle", type=int, default=2000)
    direct_actions.add_parser("batches", help="List persisted PVT / Corner / Monte Carlo batch experiments.")
    d_bdemo = direct_actions.add_parser("batch-demo", help="Create a synthetic 27-corner PVT or 40-sample Monte Carlo demo batch.")
    d_bdemo.add_argument("--kind", choices=("pvt", "monte_carlo"), default="pvt")
    d_bdemo.add_argument("--batch-id", help="Optional custom demo batch ID.")
    d_bprep = direct_actions.add_parser("batch-prepare", help="Freeze a PVT / Corner / Monte Carlo batch matrix locally without launching.")
    d_bprep.add_argument("--batch-id", required=True)
    d_bprep.add_argument("--netlist", required=True, help="Local netlist template file.")
    d_bprep.add_argument("--kind", choices=("pvt", "monte_carlo", "pvt_mc"), default="pvt")
    d_bprep.add_argument("--title", help="Optional human-readable batch title.")
    d_bprep.add_argument("--corners", nargs="+", default=["tt"], help="Process corners (e.g. tt ss ff).")
    d_bprep.add_argument("--voltages", nargs="+", type=float, default=[1.2], help="Supply voltages (e.g. 1.08 1.20 1.32).")
    d_bprep.add_argument("--temps", nargs="+", type=float, default=[27.0], help="Temperatures in degC (e.g. -40 27 125).")
    d_bprep.add_argument("--voltage-param", default="vdd")
    d_bprep.add_argument("--mc-samples", type=int, default=0)
    d_bprep.add_argument("--mc-base-seed", type=int, default=1001)
    d_bprep.add_argument("--mc-variation", choices=("all", "mismatch", "process"), default="all")
    d_bprep.add_argument("--specs-json", help="Optional JSON file containing acceptance spec list.")
    d_bprep.add_argument("--max-concurrency", type=int, default=4)
    d_bprep.add_argument("--host", help="Explicit registered host ID.")
    d_bprep.add_argument("--hosts", nargs="+", help="Registered host pool IDs.")
    d_bprep.add_argument("--backend", choices=("ssh_direct", "lsf", "slurm"), default="ssh_direct")
    d_bprep.add_argument("--simulator", choices=("spectre", "ngspice"), default="spectre")
    d_bprep.add_argument("--output-format", choices=("psfascii", "psfbin", "psfxl"), default="psfbin")
    d_bprep.add_argument("--cpus", type=int, default=1)
    d_bprep.add_argument("--memory-gib", type=float, default=4.0)
    d_bprep.add_argument("--wall-seconds", type=int, default=3600)
    d_bprep.add_argument("--queue", help="LSF queue name.")
    d_bprep.add_argument("--partition", help="Slurm partition name.")
    d_bprep.add_argument("--remote-python", default="/usr/bin/python3")
    d_bprep.add_argument("--remote-root", default=".spiceowl-runs")
    for bcmd, bhelp in (
        ("batch-inspect", "Read persisted PVT / MC batch summary and case states."),
        ("batch-dispatch", "Stage and launch pending batch cases up to max_concurrency."),
        ("batch-refresh", "Poll active cases, collect terminal artifacts, and update batch summary."),
        ("batch-cancel", "Cancel active cases and block queued cases in a batch."),
        ("batch-report", "Print batch report as JSON, --markdown, or --csv."),
    ):
        bp = direct_actions.add_parser(bcmd, help=bhelp)
        bp.add_argument("batch_id")
        if bcmd == "batch-dispatch":
            bp.add_argument("--limit", type=int)
            bp.add_argument("--skip-resource-recheck", action="store_true")
        elif bcmd == "batch-refresh":
            bp.add_argument("--no-auto-collect", action="store_true")
            bp.add_argument("--dispatch-more", action="store_true")
            bp.add_argument("--skip-resource-recheck", action="store_true")
        elif bcmd == "batch-report":
            bp.add_argument("--markdown", action="store_true")
            bp.add_argument("--csv", action="store_true")
    live = commands.add_parser("live", help="Use the configured real CircuitGit Runner with an explicitly bound project.")
    live.add_argument("--client-config", required=True, help="Private spiceowl.live-client.v1 JSON file.")
    live_actions = live.add_subparsers(dest="action", required=True)
    for name in ("runtime", "scan", "submit", "status", "collect", "watch", "unwatch", "lookup"):
        command = live_actions.add_parser(name, help={
            "runtime": "Read stored real simulation and pool observations.",
            "scan": "Explicitly scan the configured simulation host pool.",
            "submit": "Submit exact configured inputs through CircuitGit's existing Runner.",
            "status": "Observe one exact real run.",
            "collect": "Collect one exact run through the existing Runner.",
            "watch": "Enable background observation of one exact run.",
            "unwatch": "Stop background observation without cancelling the simulation.",
            "lookup": "Read the original submission key mapping; never replay a request.",
        }[name])
        if name == "lookup":
            command.add_argument("--key", required=True)
            continue
        command.add_argument("--project", help="Must match the configured project; defaults to that binding.")
        if name not in ("runtime", "scan"):
            command.add_argument("--configuration", required=True)
            command.add_argument("--source-commit", required=True)
        if name in ("status", "collect", "watch", "unwatch"):
            command.add_argument("--run-id", required=True)
        if name in ("submit", "collect"):
            command.add_argument("--key", help="Stable request key; omitted keys are privately persisted before sending.")
    workflow = commands.add_parser("workflow", help="Run a registered deterministic experiment through CircuitGit's existing Runner.")
    workflow.add_argument("--client-config", required=True, help="Private spiceowl.live-client.v1 JSON file.")
    workflow_actions = workflow.add_subparsers(dest="action", required=True)
    for name in ("prepare", "run", "inspect", "execute", "lookup"):
        command = workflow_actions.add_parser(name, help={
            "prepare": "Freeze registered source and recipe with a remote dependency audit; report missing conditions.",
            "run": "Submit one prepared plan with its fixed experiment key.",
            "inspect": "Observe one job and automatically collect terminal artifacts through the Runner.",
            "execute": "Prepare, run and inspect one experiment until completion, uncertainty or timeout.",
            "lookup": "Read the original submission mapping without clearing an uncertain request.",
        }[name])
        if name in ("prepare", "execute"):
            command.add_argument("--recipe", required=True)
            command.add_argument("--source-commit", required=True)
        elif name == "run":
            command.add_argument("--plan-id", required=True)
        elif name == "inspect":
            command.add_argument("--job-id", required=True)
        else:
            command.add_argument("--key", required=True)
        if name == "execute":
            command.add_argument("--timeout", type=float, default=600.0, help="Total waiting deadline in seconds; original IDs are returned on timeout.")
            command.add_argument("--poll-seconds", type=float, default=3.0)
    commands.add_parser("status", help="Print locally stored connection/run/session records as JSON.")
    commands.add_parser("mcp", help="Retired launcher; use CircuitGit's Codex tool bridge or 'spiceowl read'.",
                        description="Agent tools belong to CircuitGit's Codex channel. This command starts no MCP server; use 'spiceowl read' for local SDK projections.")
    reads = commands.add_parser("read", help="Read bounded stored SDK views without an Agent or MCP server.").add_subparsers(dest="action", required=True)
    for name in ("status", "runs", "run", "connections", "hosts", "ic-gateways", "ic-runs", "ic-run"):
        command = reads.add_parser(name)
        if name in ("ic-runs", "ic-run"):
            command.add_argument("gateway_id")
        if name in ("run", "ic-run"):
            command.add_argument("run_id")
    ic = commands.add_parser("ic", help="Read saved IC Workbench tasks through a registered local gateway.").add_subparsers(dest="action", required=True)
    ic.add_parser("list", help="List registered gateways without a network request.")
    ic.add_parser("register", help="Save an explicit private gateway profile; no network request.").add_argument("--file", required=True)
    for action in ("sync", "runs", "inspect", "lookup"):
        command = ic.add_parser(action, help={
            "sync": "Read a stored gateway snapshot; never probes SSH or the simulator.",
            "runs": "List locally cached IC runs.",
            "inspect": "Read one locally cached IC run.",
            "lookup": "Look up an existing submission key without submitting anything.",
        }[action])
        command.add_argument("gateway_id")
        if action == "inspect":
            command.add_argument("run_id")
        elif action == "lookup":
            command.add_argument("submission_key")
    hosts = commands.add_parser("hosts", help="Register private SSH profiles and explicitly probe connectivity.").add_subparsers(dest="action", required=True)
    hosts.add_parser("list", help="List stored metadata; no network requests.")
    host_add = hosts.add_parser("add", help="Import a private JSON profile; no network requests.")
    host_add.add_argument("--file", required=True)
    pool = hosts.add_parser("import-pool", help="Register named hosts using an existing SSH route template; no network calls.")
    pool.add_argument("--template", required=True)
    pool.add_argument("--names", nargs="+", required=True)
    scan = hosts.add_parser("scan", help="Explicitly check selected hosts and their resources (two hosts at a time).")
    scan.add_argument("--hosts", nargs="+")
    resources = hosts.add_parser("resources", help="Read one host's current aggregate Linux resources.")
    resources.add_argument("host_id")
    recommend = hosts.add_parser("recommend", help="Evaluate stored resource snapshots; no network calls or reservation.")
    recommend.add_argument("--cpus", type=int, required=True)
    recommend.add_argument("--memory-gib", type=float, required=True)
    recommend.add_argument("--simulator", choices=("spectre", "ngspice"), default="spectre")
    for name in ("inspect", "probe"):
        command = hosts.add_parser(name, help="Read stored profile." if name == "inspect" else "Check SSH identity and tool versions. Does not start a simulation.")
        command.add_argument("host_id")
    agents = commands.add_parser("agents", help="Inspect legacy agent metadata; new conversations use CircuitGit.",
        description="Legacy list/inspect remain available. Add/probe/ask are retired; use CircuitGit's Codex conversation and existing authentication.").add_subparsers(dest="action", required=True)
    agents.add_parser("list", help="List saved legacy metadata without launching a runtime.")
    agent_add = agents.add_parser("add", help="Retired; configure the Agent in CircuitGit.")
    agent_add.add_argument("--file", required=True)
    for name in ("inspect", "probe", "ask"):
        command = agents.add_parser(name, help="Read saved legacy metadata." if name == "inspect" else "Retired; use CircuitGit's Codex channel.")
        command.add_argument("agent_id")
        if name == "ask":
            command.add_argument("--question", required=True)
            command.add_argument("--request-key", default=None)
    questions = commands.add_parser("questions", help="Read saved legacy Q&A history; does not resume requests.").add_subparsers(dest="action", required=True)
    questions.add_parser("list")
    questions.add_parser("inspect").add_argument("qa_id")
    execution = commands.add_parser("execution").add_subparsers(dest="action", required=True)
    plan = execution.add_parser("plan", help="Prepare a placement/native-origin plan; does not submit a simulation.")
    plan.add_argument("--file", required=True)
    connections = commands.add_parser("connections").add_subparsers(dest="action", required=True)
    connections.add_parser("list")
    add = connections.add_parser("add")
    add.add_argument("--label", required=True)
    add.add_argument("--kind", choices=("agent", "model"), required=True)
    add.add_argument("--scenario", choices=("success", "inference_denied", "disconnect", "permission_denied"), default="success")
    for name in ("inspect", "disable", "enable", "remove", "probe"):
        command = connections.add_parser(name)
        command.add_argument("connection_id")
        if name == "probe":
            command.add_argument("--level", choices=("transport", "auth", "models"), default="models")
    runs = commands.add_parser("runs").add_subparsers(dest="action", required=True)
    runs.add_parser("list")
    advance = runs.add_parser("advance")
    advance.add_argument("run_id")
    diagnose = commands.add_parser("diagnose")
    diagnose.add_argument("run_id")
    diagnose.add_argument("--connection", required=True)
    diagnose.add_argument("--question", default="Explain the current evidence and the next read-only check.")
    diagnose.add_argument("--request-key", default=None)
    session = commands.add_parser("session")
    session.add_argument("session_id")
    session.add_argument("--after", type=int, default=0)
    return result


def output(value):
    print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "next-action":
            from spiceowl.orchestrator import evaluate_next_action
            state = json.loads(Path(args.state_json).expanduser().read_text(encoding="utf-8"))
            output(evaluate_next_action(state))
            return 0
        if args.command == "eval-agent":
            from spiceowl.agent_eval import compare_baselines, render_eval_markdown, run_agent_eval
            if args.baseline == "compare":
                res = compare_baselines(repeats=args.repeats)
                if args.markdown:
                    print(res["markdown"])
                else:
                    output(res)
                return 0 if res["deterministic"]["strict_qualification_passed"] else 2
            res = run_agent_eval(baseline=args.baseline, repeats=args.repeats)
            if args.markdown:
                print(render_eval_markdown(res))
            else:
                output(res)
            return 0 if res["strict_qualification_passed"] else 2
        if args.command == "init":
            from spiceowl.batch import DirectBatchManager
            from spiceowl.direct import DirectExecutionManager
            from spiceowl.onboarding_wizard import (
                bootstrap_vivid_demo,
                diagnose_local_environment,
                prepare_first_simulation,
                quick_register_eda_host,
            )
            with Controller(args.data_dir, agent_mode="standalone") as app:
                dm = DirectExecutionManager(app.store.directory, hosts=app.hosts)
                bm = DirectBatchManager(app.store.directory, dm)
                result: dict = {"environment": diagnose_local_environment(
                    app.store.directory, hosts=app.hosts, direct_manager=dm, batch_manager=bm,
                )}
                if args.demo:
                    result["demo"] = bootstrap_vivid_demo(app, dm, bm)
                if args.host_id or args.ssh_target:
                    if not all((args.host_id, args.ssh_target, args.expected_host, args.expected_user)):
                        raise DomainError("Registering an EDA host requires --host-id, --ssh-target, --expected-host, and --expected-user.")
                    host, created = quick_register_eda_host(
                        app.hosts,
                        host_id=args.host_id,
                        label=args.label or args.host_id,
                        ssh_target=args.ssh_target,
                        expected_host=args.expected_host,
                        expected_user=args.expected_user,
                        simulator=args.simulator,
                        simulator_executable=args.simulator_path,
                    )
                    probe_res = app.hosts.probe(host["host_id"]) if args.probe else None
                    result["registered_host"] = {"host": app.hosts.inspect(host["host_id"]), "created": created, "probe": probe_res}
                if args.prepare_smoke:
                    target_host = args.host_id or (app.hosts.list()[0]["host_id"] if app.hosts.list() else None)
                    if not target_host:
                        raise DomainError("Register an EDA host first (or pass --host-id) before --prepare-smoke.")
                    result["first_simulation"] = prepare_first_simulation(
                        dm,
                        host_id=target_host,
                        simulator=args.simulator,
                        backend=args.backend,
                        queue=args.queue,
                        partition=args.partition,
                    )
                if args.json:
                    output(result)
                else:
                    env = result["environment"]
                    print(f"SpiceOwl {__version__} Quickstart ({env['platform']['system']} / Python {env['platform']['python']})")
                    print(f"Workspace: {env['platform']['data_dir']}")
                    print("-" * 64)
                    for chk in env["checks"]:
                        tag = {"ok": "[OK]", "warning": "[WARN]", "missing": "[MISS]"}.get(chk["status"], "[?]")
                        print(f"  {tag:6s} {chk['label']}: {chk['detail']}")
                    if env["ssh_aliases"]:
                        names = ", ".join(a["alias"] for a in env["ssh_aliases"][:12])
                        print(f"\nDetected ~/.ssh/config aliases: {names}")
                    if "demo" in result:
                        print("\nVivid demo workspace bootstrapped:")
                        for st in result["demo"]["stations"]:
                            print(f"  {st['step']}. {st['title']} ({st['badge']}) -> {st['route']}")
                    if "registered_host" in result:
                        h = result["registered_host"]["host"]
                        print(f"\nRegistered EDA host: {h['host_id']} (alias={h['ssh_target']})")
                    if "first_simulation" in result:
                        fs = result["first_simulation"]["run"]
                        print(f"\nFirst smoke simulation frozen: {fs['run_id']} ({fs['simulator']} via {fs['backend']})")
                        print(f"  Next: spiceowl direct stage {fs['run_id']} && spiceowl direct launch {fs['run_id']}")
                    print("\nStart the local web console & open #quickstart:")
                    print("  spiceowl serve")
            return 0
        if args.command == "direct":
            from spiceowl.direct import DirectExecutionManager
            manager = DirectExecutionManager(args.data_dir)
            if args.action == "prepare":
                if not 0 < args.memory_gib <= 16384:
                    raise DomainError("Memory must be a positive finite GiB value.")
                netlist_text = Path(args.netlist).expanduser().read_text(encoding="utf-8")
                support = {}
                for entry in args.support:
                    if "=" not in entry:
                        raise DomainError("Support file must be formatted as RELATIVE_PATH=LOCAL_FILE.")
                    rel, local = entry.split("=", 1)
                    support[rel] = Path(local).expanduser().read_bytes()
                license_env = None
                if args.license_env_json:
                    from spiceowl.hosts import load_profile
                    license_env = load_profile(args.license_env_json)
                scheduler = {}
                if args.backend == "lsf":
                    scheduler = {"queue": args.queue, "exclusive": args.exclusive}
                    if args.project is not None:
                        scheduler["project"] = args.project
                    if args.job_group is not None:
                        scheduler["job_group"] = args.job_group
                elif args.backend == "slurm":
                    scheduler = {"partition": args.partition, "exclusive": args.exclusive}
                    if args.account is not None:
                        scheduler["account"] = args.account
                    if args.qos is not None:
                        scheduler["qos"] = args.qos
                limits = {
                    "cpu_count": args.cpus,
                    "memory_bytes": int(args.memory_gib * 2**30),
                    "wall_seconds": args.wall_seconds,
                    "license_queue_seconds": args.license_queue_seconds,
                    "enforce_rlimit_as": args.enforce_rlimit_as,
                }
                recovery_policy = {
                    "enabled": bool(args.enable_recovery),
                    "max_recovery_attempts": args.max_recovery_attempts,
                    "max_memory_bytes": int(round(max(float(args.max_memory_gib), float(args.memory_gib)) * 2**30)),
                    "license_backoff_seconds": args.license_backoff_seconds,
                    "candidate_host_ids": list(args.hosts) if args.hosts else [],
                }
                output(manager.prepare(
                    run_key=args.run_key,
                    netlist_text=netlist_text,
                    host_id=args.host,
                    host_ids=args.hosts,
                    scan_hosts=args.scan_hosts,
                    backend=args.backend,
                    simulator=args.simulator,
                    expected_version=args.expected_version,
                    pin_version=not args.no_pin_version,
                    support_files=support or None,
                    limits=limits,
                    scheduler=scheduler or None,
                    output_format=args.output_format,
                    include_dirs=args.include_dir or None,
                    remote_python=args.remote_python,
                    remote_root=args.remote_root,
                    license_environment=license_env,
                    source_commit=args.source_commit,
                    recovery_policy=recovery_policy,
                ))
            elif args.action == "runs":
                output(manager.list())
            elif args.action == "inspect":
                output(manager.read(args.run_id))
            elif args.action == "lookup":
                output(manager.lookup_submission(args.key))
            elif args.action == "stage":
                output(manager.stage(args.run_id))
            elif args.action == "launch":
                recheck = False if args.skip_resource_recheck else None
                result = manager.launch(args.run_id, recheck_resources=recheck)
                output(result)
                return 2 if result.get("state") in ("launch_outcome_unknown", "reconcile_required", "job_setup_failed") else 0
            elif args.action == "status":
                result = manager.status(args.run_id)
                output(result)
                return 2 if result.get("status_transport") != "connected" else 0
            elif args.action == "cancel":
                result = manager.cancel(args.run_id, action_key=args.action_key)
                output(result)
                return 2 if result.get("outcome") not in ("confirmed", "already_terminal") else 0
            elif args.action == "collect":
                output(manager.collect(args.run_id))
            elif args.action == "report":
                output(manager.report(args.run_id))
            elif args.action == "recover":
                res = manager.evaluate_and_recover(args.run_id, execute=not args.dry_run)
                output(res)
                return 0 if res["decision"].get("result") in ("allow", "already_recovered") else 2
            elif args.action == "recovery-reports":
                output({"reports": manager.list_recovery_reports(query=args.query, outcome=args.outcome)})
            elif args.action == "recovery-report":
                res = manager.get_recovery_report(args.run_id)
                if args.markdown:
                    print(res["markdown"])
                else:
                    output(res)
            elif args.action == "demo-waveform":
                run = manager.create_demo_waveform_run(
                    run_id=args.run_id, cycles=args.cycles, glitch_cycle=args.glitch_cycle,
                )
                output({"run": run, "waveforms": manager.waveform_index(run["run_id"], allow_remote=False)})
            elif args.action == "waveforms":
                output(manager.waveform_index(args.run_id, refresh_remote=args.refresh_remote))
            elif args.action == "waveform-query":
                output(manager.waveform_query(
                    args.run_id, args.dataset, args.signals,
                    x_min=args.x_min, x_max=args.x_max,
                    max_points=args.max_points, mode="exact" if args.exact else "auto",
                ))
            elif args.action == "waveform-cycles":
                output(manager.waveform_cycles(
                    args.run_id, args.dataset, args.signals,
                    period=args.period, clock_signal=args.clock,
                    threshold=args.threshold, edge=args.edge,
                    t_start=args.t_start, t_end=args.t_end,
                    worst_metric=args.worst_metric, worst_signal=args.worst_signal,
                    cycle_index=args.cycle_index, context_cycles=args.context_cycles,
                    overlay_cycles=args.overlay_cycles, max_points_per_cycle=args.max_points_per_cycle,
                ))
            elif args.action in ("batches", "batch-demo", "batch-prepare", "batch-inspect", "batch-dispatch", "batch-refresh", "batch-cancel", "batch-report"):
                from spiceowl.batch import DirectBatchManager
                bman = DirectBatchManager(manager.data_dir, manager)
                if args.action == "batches":
                    output({"batches": bman.list()})
                elif args.action == "batch-demo":
                    res = bman.create_demo_batch(kind=args.kind, batch_id=args.batch_id)
                    output({"batch": res["batch"], "summary": res["summary"]})
                elif args.action == "batch-prepare":
                    tpl = Path(args.netlist).read_text(encoding="utf-8")
                    specs = json.loads(Path(args.specs_json).read_text(encoding="utf-8")) if args.specs_json else None
                    limits = {
                        "cpu_count": args.cpus,
                        "memory_bytes": int(round(args.memory_gib * 2**30)),
                        "wall_seconds": args.wall_seconds,
                    }
                    scheduler = {}
                    if args.backend == "lsf" and args.queue:
                        scheduler["queue"] = args.queue
                    elif args.backend == "slurm" and args.partition:
                        scheduler["partition"] = args.partition
                    output(bman.prepare(
                        batch_id=args.batch_id,
                        netlist_template=tpl,
                        kind=args.kind,
                        title=args.title,
                        process_corners=args.corners,
                        voltages=args.voltages,
                        temperatures=args.temps,
                        voltage_param=args.voltage_param,
                        mc_samples=args.mc_samples,
                        mc_base_seed=args.mc_base_seed,
                        mc_variation=args.mc_variation,
                        specs=specs,
                        max_concurrency=args.max_concurrency,
                        host_id=args.host,
                        host_ids=args.hosts,
                        backend=args.backend,
                        simulator=args.simulator,
                        output_format=args.output_format,
                        limits=limits,
                        scheduler=scheduler or None,
                        remote_python=args.remote_python,
                        remote_root=args.remote_root,
                    ))
                elif args.action == "batch-inspect":
                    output(bman.build_summary(args.batch_id))
                elif args.action == "batch-dispatch":
                    recheck = False if args.skip_resource_recheck else None
                    output(bman.dispatch_next(args.batch_id, limit=args.limit, recheck_resources=recheck))
                elif args.action == "batch-refresh":
                    recheck = False if args.skip_resource_recheck else None
                    output(bman.refresh(
                        args.batch_id,
                        auto_collect=not args.no_auto_collect,
                        dispatch_more=args.dispatch_more,
                        recheck_resources=recheck,
                    ))
                elif args.action == "batch-cancel":
                    output(bman.cancel(args.batch_id))
                elif args.action == "batch-report":
                    rep = bman.report(args.batch_id)
                    if args.markdown:
                        print(rep["markdown"])
                    elif args.csv:
                        print(rep["csv"], end="")
                    else:
                        output(rep["summary"])
            elif args.action == "watch":
                monitor = manager.monitor(interval_seconds=args.interval, max_backoff_seconds=max(args.interval, 300.0))
                monitor.watch(args.run_id, allow_collect=args.allow_collect)
                deadline = time.monotonic() + max(0.0, args.max_wait)
                receipt = None
                try:
                    while True:
                        receipt = monitor.poll_once(args.run_id)
                        if receipt["watch_state"] != "armed" or time.monotonic() >= deadline:
                            break
                        sleep_for = min(args.interval, max(0.0, deadline - time.monotonic()))
                        if sleep_for > 0:
                            time.sleep(sleep_for)
                except KeyboardInterrupt:
                    receipt = monitor.snapshot(args.run_id)
                output({"run": manager.read(args.run_id), "monitor": receipt, "remote_cancelled": False})
                return 0 if receipt and receipt.get("watch_state") == "terminal" else 2
            return 0
        if args.command == "workflow":
            from spiceowl.integrations.live import LiveSimulationClient
            client = LiveSimulationClient(args.client_config)
            if args.action == "lookup":
                value = client.lookup_submission(args.key)
            elif args.action == "execute":
                value = client.workflow(args.recipe, args.source_commit, timeout=args.timeout, poll_seconds=args.poll_seconds)
            elif args.action == "prepare":
                value = client.execute("prepare", {"recipe_id": args.recipe, "source_commit": args.source_commit})
            elif args.action == "run":
                value = client.execute("run", {"plan_id": args.plan_id})
            else:
                value = client.execute("inspect", {"job_id": args.job_id})
            output(value)
            return 1 if value.get("state") in ("blocked", "uncertain", "timed_out") or value.get("inspection_state") == "uncertain" else 0
        if args.command == "live":
            from spiceowl.integrations.live import LiveSimulationClient
            client = LiveSimulationClient(args.client_config)
            if args.action == "lookup":
                output(client.lookup_submission(args.key))
            else:
                values = {name: getattr(args, name) for name in ("configuration", "source_commit", "run_id") if hasattr(args, name)}
                if args.project is not None:
                    values["project_id"] = args.project
                output(client.execute(args.action, values, getattr(args, "key", None)))
            return 0
        if args.command == "ic":
            from spiceowl.integrations.ic_workbench import ICGatewayBackend
            from spiceowl.hosts import load_profile
            backend = ICGatewayBackend(args.data_dir)
            if args.action == "register":
                record, created = backend.register(load_profile(args.file))
                output({"gateway": record, "created": created})
            elif args.action == "list":
                output(backend.list())
            elif args.action == "sync":
                receipt = backend.refresh(args.gateway_id)
                output(receipt)
                return 0 if receipt.get("updated") is True else 1
            elif args.action == "runs":
                output(backend.runs(args.gateway_id))
            elif args.action == "inspect":
                output(backend.run(args.gateway_id, args.run_id))
            else:
                receipt = backend.lookup_submission(args.gateway_id, args.submission_key)
                output(receipt)
                return 0 if receipt["observation"]["state"] == "readable" else 1
            return 0
        if args.command == "mcp" or args.command == "agents" and args.action in ("add", "probe", "ask"):
            raise agent_channel_error()
        if args.command == "read":
            from spiceowl.read_api import open_read_views
            views = open_read_views(args.data_dir)
            methods = {"status": views.status, "runs": views.list_runs, "run": views.get_run,
                       "connections": views.list_connections, "hosts": views.list_hosts,
                       "ic-gateways": views.list_ic_gateways, "ic-runs": views.list_ic_runs,
                       "ic-run": views.get_ic_run}
            values = [getattr(args, name) for name in ("gateway_id", "run_id") if hasattr(args, name)]
            output(methods[args.action](*values))
            return 0
        if args.command == "hosts":
            from spiceowl.hosts import HostRegistry, load_profile
            registry = HostRegistry(args.data_dir)
            if args.action == "add":
                output(registry.add(load_profile(args.file)))
            elif args.action == "list":
                output(registry.list())
            elif args.action == "inspect":
                output(registry.inspect(args.host_id))
            elif args.action == "import-pool":
                output(registry.import_pool(args.template, args.names))
            elif args.action == "scan":
                output(registry.scan(args.hosts))
            elif args.action == "resources":
                output(registry.resources(args.host_id))
            elif args.action == "recommend":
                if not 0 < args.memory_gib <= 1048576:
                    raise DomainError("Memory must be a positive finite GiB value.")
                output(registry.recommend({"cpu_count": args.cpus, "memory_bytes": int(args.memory_gib * 2**30), "simulator": args.simulator}))
            else:
                result = registry.probe(args.host_id)
                output(result)
                return 0 if result["identity"]["verified"] and not result.get("error") and all(t["status"] == "available" for t in result["tools"]) else 1
            return 0
        if args.command == "agents":
            from spiceowl.agent.qa import NativeRegistry
            registry = NativeRegistry(args.data_dir)
            if args.action == "list":
                output(registry.list())
            else:
                output(registry.inspect(args.agent_id))
            return 0
        if args.command == "questions":
            from spiceowl.agent.qa import Questions
            questions = Questions(Store(args.data_dir))
            output(questions.list() if args.action == "list" else questions.get(args.qa_id))
            return 0
        if args.command == "execution":
            from spiceowl.hosts import HostRegistry, load_profile
            from spiceowl.scheduling.origins import plan_execution, validate_origin
            data = load_profile(args.file)
            if not isinstance(data, dict) or set(data) != {"origin", "requirements"}:
                raise DomainError("An execution plan needs origin and requirements.")
            output(plan_execution(validate_origin(data["origin"]), HostRegistry(args.data_dir).recommend(data["requirements"])))
            return 0
        if args.command in ("status", "session") or (args.command in ("connections", "runs") and args.action in ("list", "inspect")):
            store = Store(args.data_dir)
            if args.command == "status":
                from spiceowl.hosts import host_summary
                from spiceowl.agent.qa import agent_summary, question_summary
                output({"mode": "engine_console", "agent_channel": agent_channel_info(), "connections": [c for c in store.list("connection") if not c.get("deleted")], "hosts": [host_summary(h) for h in store.list("host")], "native_agents": [agent_summary(a) for a in store.list("agent_profile")], "questions": [question_summary(q) for q in store.list("qa")[:50]], "runs": store.list("run"), "sessions": store.list("session")})
            elif args.command == "session":
                if args.after < 0:
                    raise DomainError("Event cursor must be nonnegative.")
                output(store.session(args.session_id, args.after))
            else:
                kind = "connection" if args.command == "connections" else "run"
                output(store.get(kind, args.connection_id) if args.action == "inspect" else [c for c in store.list(kind) if not c.get("deleted")])
            return 0
        with Controller(args.data_dir, agent_mode=args.agent_mode if args.command == 'serve' else 'embedded_in_circuitgit') as app:
            if args.command == "serve":
                from spiceowl.web.server import make_server
                if not 0 <= args.port <= 65535:
                    raise DomainError("Port must be between 0 and 65535.")
                if args.demo:
                    app.seed_demo()
                with make_server(app, args.port, live_client_config=args.live_client_config,
                        codex_executable=args.codex_executable, codex_model=args.codex_model,
                        pdk_lut_path=args.pdk_lut, circuitgit_client_config=args.circuitgit_client_config,
                        circuitgit_sdk_path=args.circuitgit_sdk_path,
                        enable_direct_exec=args.enable_direct_exec) as server:
                    simulation_mode = "configured real simulation client + separate synthetic demos" if args.live_client_config else "synthetic simulation runs"
                    print(f"SpiceOwl {__version__} · simulation engine · Agent mode: {args.agent_mode} · {simulation_mode}", flush=True)
                    print(f"http://127.0.0.1:{server.server_address[1]}", flush=True)
                    try:
                        server.serve_forever()
                    except KeyboardInterrupt:
                        pass
            elif args.command == "demo":
                output(app.seed_demo())
            elif args.command == "connections":
                if args.action == "add":
                    output(app.add_connection({"label": args.label, "kind": args.kind, "backend": f"fake_{args.kind}", "scenario": args.scenario}))
                elif args.action == "probe":
                    output(app.probe(args.connection_id, args.level))
                elif args.action == "remove":
                    output(app.remove_connection(args.connection_id))
                else:
                    output(app.change_connection(args.connection_id, {"enabled": args.action == "enable"}))
            elif args.command == "runs":
                output(app.advance(args.run_id))
            elif args.command == "diagnose":
                receipt = app.diagnose({"connection_id": args.connection, "run_id": args.run_id, "question": args.question, "request_key": args.request_key or new_id("request")})
                for _ in range(300):
                    turn = app.store.get_turn(receipt["turn_id"])
                    if turn["status"] not in ("dispatching", "running"):
                        break
                    time.sleep(0.05)
                output(app.store.session(receipt["session_id"]))
        return 0
    except (DomainError, OSError) as exc:
        code = exc.code if isinstance(exc, DomainError) else "local_io_error"
        message = str(exc) if isinstance(exc, DomainError) else "Cannot access local state or bind the requested port. Check the directory and port."
        print(json.dumps({"error": {"code": code, "message": message}}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

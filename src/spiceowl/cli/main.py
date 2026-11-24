"""Simulation engine CLI; CircuitGit owns the Codex agent conversation."""

import argparse
import json
import os
from pathlib import Path
import sys
import time

from spiceowl import __version__
from spiceowl.agent_channel import agent_channel_error, agent_channel_info
from spiceowl.domain.common import DomainError, new_id
from spiceowl.service import Controller
from spiceowl.storage.sqlite import Store


def parser():
    result = argparse.ArgumentParser(prog="spiceowl", description="Simulation engine SDK/CLI with standalone Codex and embedded CircuitGit Agent modes.")
    result.add_argument("--version", action="version", version=f"SpiceOwl {__version__}")
    default = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "spiceowl"
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
    commands.add_parser("demo", help="Create the synthetic demo workspace.")
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
                        circuitgit_sdk_path=args.circuitgit_sdk_path) as server:
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

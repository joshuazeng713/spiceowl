"""CLI entry point for local records and explicit read-only connectivity probes."""

import argparse
import json
import os
from pathlib import Path
import sys
import time

from spiceowl import __version__
from spiceowl.domain.common import DomainError, new_id
from spiceowl.service import Controller
from spiceowl.storage.sqlite import Store


def parser():
    result = argparse.ArgumentParser(prog="spiceowl", description="Local simulation console: synthetic runs and read-only real connectivity.")
    result.add_argument("--version", action="version", version=f"SpiceOwl {__version__}")
    default = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "spiceowl"
    result.add_argument("--data-dir", default=str(default), help="Local persistent state directory (put before the command).")
    commands = result.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Open a loopback-only web console.")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--demo", action="store_true", help="Idempotently add synthetic demo data.")
    commands.add_parser("demo", help="Create the synthetic demo workspace.")
    commands.add_parser("status", help="Print locally stored connection/run/session records as JSON.")
    commands.add_parser("mcp", help="Serve read-only local queries over stdio (requires the mcp extra).")
    hosts = commands.add_parser("hosts", help="Register private SSH profiles and explicitly probe connectivity.").add_subparsers(dest="action", required=True)
    hosts.add_parser("list", help="List stored metadata; no network requests.")
    host_add = hosts.add_parser("add", help="Import a private JSON profile; no network requests.")
    host_add.add_argument("--file", required=True)
    for name in ("inspect", "probe"):
        command = hosts.add_parser(name, help="Read stored profile." if name == "inspect" else "Check SSH identity and tool versions. Does not start a simulation.")
        command.add_argument("host_id")
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
        if args.command == "mcp":
            try:
                from spiceowl.mcp.server import serve
            except ModuleNotFoundError as exc:
                if exc.name == "mcp" or (exc.name or "").startswith("mcp."):
                    raise DomainError("Install the optional MCP dependency: python -m pip install 'spiceowl[mcp]' (or '.[mcp]' from source).", "missing_dependency") from None
                raise
            serve(args.data_dir)
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
            else:
                result = registry.probe(args.host_id)
                output(result)
                return 0 if result["identity"]["verified"] and not result.get("error") and all(t["status"] == "available" for t in result["tools"]) else 1
            return 0
        if args.command in ("status", "session") or (args.command in ("connections", "runs") and args.action in ("list", "inspect")):
            store = Store(args.data_dir)
            if args.command == "status":
                from spiceowl.hosts import host_summary
                output({"mode": "read_only_connectivity", "connections": [c for c in store.list("connection") if not c.get("deleted")], "hosts": [host_summary(h) for h in store.list("host")], "runs": store.list("run"), "sessions": store.list("session")})
            elif args.command == "session":
                if args.after < 0:
                    raise DomainError("Event cursor must be nonnegative.")
                output(store.session(args.session_id, args.after))
            else:
                kind = "connection" if args.command == "connections" else "run"
                output(store.get(kind, args.connection_id) if args.action == "inspect" else [c for c in store.list(kind) if not c.get("deleted")])
            return 0
        with Controller(args.data_dir) as app:
            if args.command == "serve":
                from spiceowl.web.server import make_server
                if not 0 <= args.port <= 65535:
                    raise DomainError("Port must be between 0 and 65535.")
                if args.demo:
                    app.seed_demo()
                with make_server(app, args.port) as server:
                    print(f"SpiceOwl {__version__} · synthetic runs + read-only real connectivity", flush=True)
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

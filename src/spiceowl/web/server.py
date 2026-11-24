"""Bounded loopback HTTP interface for synthetic runs and host connectivity."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import re
import secrets
import time
from urllib.parse import parse_qs, urlsplit

from spiceowl.domain.common import DomainError, require_id
from spiceowl.web.showcase_assets import SHOWCASE_ASSETS


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DomainError("Duplicate JSON keys are not allowed.")
        result[key] = value
    return result


def _live_request(body):
    if set(body) - {"operation", "args", "idempotency_key"} or not {"operation", "args"} <= set(body):
        raise DomainError("Live control requires only operation, args and an optional idempotency_key.")
    operation, args = body["operation"], body["args"]
    if not isinstance(operation, str) or operation not in {"scan", "submit", "status", "collect", "watch", "unwatch"} or not isinstance(args, dict):
        raise DomainError("Unsupported live simulation operation.")
    expected = set() if operation == "scan" else {"configuration", "source_commit"}
    if operation in {"status", "collect", "watch", "unwatch"}:
        expected.add("run_id")
    if set(args) - {"project_id"} != expected:
        raise DomainError("Live control requires the exact project, configuration, commit and run identity for this operation.")
    for field in ("project_id", "configuration", "run_id"):
        if field in args and (not isinstance(args[field], str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,179}", args[field])):
            raise DomainError("Invalid live simulation identity: " + field)
    if "source_commit" in args and (not isinstance(args["source_commit"], str) or not re.fullmatch("[a-f0-9]{40}", args["source_commit"])):
        raise DomainError("A full lowercase source commit is required.")
    key = body.get("idempotency_key")
    if "idempotency_key" in body and (operation not in {"submit", "collect"} or not isinstance(key, str) or not 8 <= len(key) <= 160 or not key.isprintable()):
        raise DomainError("A stable printable key is accepted only for submit or collect.")
    return operation, args, key


def make_server(controller, port=0, live_client_config=None, *, codex_executable=None,
                codex_model=None, pdk_lut_path=None, circuitgit_client_config=None,
                circuitgit_sdk_path=None):
    token = secrets.token_urlsafe(32)
    live_client = None
    if live_client_config is not None:
        from spiceowl.integrations.live import LiveSimulationClient
        live_client = LiveSimulationClient(live_client_config)
    from spiceowl.codex_bridge import CodexBridge
    definitions, dispatcher = [], None
    if controller.agent_mode == 'standalone':
        from spiceowl.standalone_agent import build_standalone_dispatcher
        dispatcher = build_standalone_dispatcher(controller, live_client=live_client,
            pdk_lut_path=pdk_lut_path, circuitgit_client_config=circuitgit_client_config,
            circuitgit_sdk_path=circuitgit_sdk_path)
        definitions = [{key: row[key] for key in ('name', 'description', 'inputSchema')}
                       for row in dispatcher.definitions()]
    controller.codex = CodexBridge(controller.store.directory / 'codex', mode=controller.agent_mode,
        dispatch_tool=dispatcher.dispatch if dispatcher else None,
        tool_policy=dispatcher.policy if dispatcher else None, tool_definitions=definitions,
        executable=codex_executable, model=codex_model)

    class Handler(BaseHTTPRequestHandler):
        server_version = "SpiceOwl"
        sys_version = ""

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *_):
            # Do not log user questions, bodies, tokens, or URLs.
            pass

        def send_content(self, status, body, content_type="application/json; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def reply(self, value, status=200):
            self.send_content(status, json.dumps(value, ensure_ascii=False, allow_nan=False).encode())

        def check_origin(self):
            actual_port = self.server.server_address[1]
            authorities = {f"127.0.0.1:{actual_port}", f"localhost:{actual_port}"}
            hosts = self.headers.get_all("Host", [])
            if len(hosts) != 1 or hosts[0] not in authorities:
                raise DomainError("This server accepts only its loopback origin.", "forbidden")
            origin = self.headers.get("Origin")
            if origin is not None and origin not in {f"http://{host}" for host in authorities}:
                raise DomainError("Cross-origin access is not permitted.", "forbidden")
            if self.command != "GET":
                supplied = self.headers.get("X-SpiceOwl-Token", "")
                if not secrets.compare_digest(supplied, token):
                    raise DomainError("Refresh the console before making changes.", "forbidden")

        def body(self):
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or not lengths[0].isdigit():
                raise DomainError("A bounded Content-Length is required.")
            size = int(lengths[0])
            if not 0 < size <= 16384:
                raise DomainError("Request body must be between 1 and 16384 bytes.", "too_large")
            if self.headers.get_content_type() != "application/json":
                raise DomainError("Use application/json.")
            try:
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise DomainError("Incomplete request body.")
                value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            except (UnicodeError, ValueError, RecursionError):
                raise DomainError("Invalid JSON request body.") from None
            if not isinstance(value, dict):
                raise DomainError("Expected a JSON object.")
            return value

        def codex_events(self, query):
            values = parse_qs(query, keep_blank_values=True)
            raw = values.get('after', ['0'])
            last = self.headers.get('Last-Event-ID', '0')
            if set(values) - {'after'} or len(raw) != 1 or any(not v.isdigit() or len(v) > 15 for v in (*raw, last)):
                raise DomainError('Invalid Codex event cursor.')
            after = max(int(raw[0]), int(last))
            # Validate before sending SSE headers. This endpoint only reads events.
            batch = controller.codex.events(after=after)
            self.send_response(200)
            for key, value in {'Content-Type': 'text/event-stream; charset=utf-8',
                    'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                    'Referrer-Policy': 'no-referrer', 'Connection': 'close'}.items():
                self.send_header(key, value)
            self.end_headers()
            deadline = time.monotonic() + 15
            try:
                while not controller._closing.is_set():
                    if batch.get('truncated'):
                        gap = {'id': max(0, batch['oldest_cursor'] - 1), 'method': 'bridge/cursor_gap',
                            'params': {'oldest_cursor': batch['oldest_cursor'], 'cursor': batch['cursor']},
                            'thread_id': None, 'turn_id': None, 'request_key': None}
                        self.wfile.write(('event: bridge\ndata: ' + json.dumps(gap) + '\n\n').encode())
                    for event in batch['events']:
                        self.wfile.write(('id: ' + str(event['id']) + '\nevent: bridge\ndata: '
                            + json.dumps(event, ensure_ascii=False, allow_nan=False) + '\n\n').encode())
                        after = event['id']
                    self.wfile.write(b': heartbeat\n\n')
                    self.wfile.flush()
                    if time.monotonic() >= deadline: break
                    if controller._closing.wait(0.25): break
                    batch = controller.codex.events(after=after)
            except (BrokenPipeError, ConnectionError, TimeoutError):
                pass

        def codex_control(self, action, body):
            fields = {'connect': ({'model', 'profile'}, set()), 'thread': (set(), set()),
                'message': ({'thread_id', 'text', 'request_key'}, {'thread_id', 'text', 'request_key'}),
                'approval': ({'approval_id', 'decision'}, {'approval_id', 'decision'}),
                'disconnect': (set(), set())}
            if action not in fields:
                raise DomainError('Unknown Codex client operation.', 'not_found')
            allowed, required = fields[action]
            if set(body) - allowed or not required <= set(body):
                raise DomainError('Use exactly the documented Codex client arguments.')
            bridge = controller.codex
            if action == 'connect': return bridge.connect(body)
            if action == 'thread': return bridge.create_thread()
            if action == 'message': return bridge.send_message(**body)
            if action == 'approval': return bridge.decide(**body)
            return bridge.disconnect()

        def dispatch(self):
            self.check_origin()
            url = urlsplit(self.path)
            path = url.path
            static = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/app.css": ("app.css", "text/css; charset=utf-8"), "/owl.svg": ("owl.svg", "image/svg+xml")}
            static.update({'/workflow.html': ('workflow.html', 'text/html; charset=utf-8'),
                '/workflow.js': ('workflow.js', 'text/javascript; charset=utf-8'),
                '/workflow.css': ('workflow.css', 'text/css; charset=utf-8')})
            for filename in ('bootstrap-dut.publication.scs', 'bootstrap-tb.publication.scs',
                             'inverter-dut.publication.scs', 'bootstrap-evidence.publication.json', 'source-manifest.json'):
                static['/' + filename] = (filename, 'application/json; charset=utf-8' if filename.endswith('.json') else 'text/plain; charset=utf-8')
            types = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8", ".svg": "image/svg+xml", ".webp": "image/webp"}
            for filename in SHOWCASE_ASSETS:
                static["/" + filename] = (filename, types["." + filename.rsplit(".", 1)[1]])
            if self.command == "GET" and path in static:
                name, media = static[path]
                return self.send_content(200, files("spiceowl.web").joinpath("static", name).read_bytes(), media)
            if self.command == "GET" and path == "/api/state":
                return self.reply(controller.state() | {"csrf_token": token})
            if path.startswith('/api/codex/'):
                action = path.removeprefix('/api/codex/')
                if self.command == 'GET' and action == 'events':
                    return self.codex_events(url.query)
                if self.command == 'GET' and action == 'approval':
                    query = parse_qs(url.query, keep_blank_values=True)
                    if set(query) != {'approval_id'} or len(query['approval_id']) != 1:
                        raise DomainError('Select one exact pending approval.')
                    return self.reply(controller.codex.approval_details(query['approval_id'][0]))
                if url.query: raise DomainError('Codex client operations do not accept extra query arguments.')
                if self.command == 'GET' and action == 'status':
                    return self.reply(controller.codex.status())
                if self.command == 'POST':
                    return self.reply(self.codex_control(action, self.body()), 202)
                raise DomainError('Unknown Codex client operation.', 'not_found')
            if path == "/api/live/runtime" and self.command == "GET":
                if url.query:
                    raise DomainError("Live runtime reads do not accept browser configuration or query parameters.")
                if live_client is None:
                    return self.reply({"enabled": False, "project_id": None, "runtime": None})
                return self.reply({"enabled": True, "project_id": live_client.project_id, "runtime": live_client.runtime()})
            if path == "/api/live/control" and self.command == "POST":
                if url.query:
                    raise DomainError("Live control does not accept query parameters.")
                operation, args, key = _live_request(self.body())
                if live_client is None:
                    raise DomainError("Real simulation is not enabled by this server's operator configuration.", "live_not_configured")
                return self.reply(live_client.execute(operation, args, idempotency_key=key), 202)
            if path == "/api/live/submission" and self.command == "GET":
                query = parse_qs(url.query, keep_blank_values=True)
                if set(query) != {"key"} or len(query["key"]) != 1 or not 8 <= len(query["key"][0]) <= 160 or not query["key"][0].isprintable():
                    raise DomainError("An exact saved submission key is required.")
                if live_client is None:
                    raise DomainError("Real simulation is not enabled by this server's operator configuration.", "live_not_configured")
                return self.reply(live_client.lookup_submission(query["key"][0]))
            if self.command == "POST" and path == "/api/demo":
                if self.body():
                    raise DomainError("Demo loading takes an empty object.")
                return self.reply(controller.seed_demo())
            if self.command == "POST" and path == "/api/connections":
                return self.reply(controller.add_connection(self.body()), 201)
            if self.command == "POST" and path == "/api/diagnose":
                return self.reply(controller.diagnose(self.body()), 202)
            if self.command == "POST" and path == "/api/agent-questions":
                return self.reply(controller.ask(self.body()), 202)
            if self.command == "GET" and path == "/api/hosts":
                return self.reply({"hosts": controller.hosts.list()})
            if self.command == "POST" and path == "/api/hosts":
                host, created = controller.hosts.register(self.body())
                return self.reply(host, 201 if created else 200)
            if self.command == "POST" and path == "/api/hosts/import-pool":
                body = self.body()
                if set(body) != {"template_id", "names"}:
                    raise DomainError("Pool import requires only template_id and names.")
                return self.reply(controller.hosts.import_pool(body["template_id"], body["names"]))
            if self.command == "POST" and path == "/api/hosts/scan":
                body = self.body()
                if not body:
                    return self.reply(controller.hosts.scan())
                if set(body) != {"host_ids"} or not isinstance(body["host_ids"], list):
                    raise DomainError("Pool scan takes an empty object or exactly a host_ids list.")
                return self.reply(controller.hosts.scan(body["host_ids"]))
            if self.command == "POST" and path == "/api/placement/recommend":
                body = self.body()
                if "host_ids" in body:
                    host_ids = body.pop("host_ids")
                    if not isinstance(host_ids, list):
                        raise DomainError("Placement host_ids must be a list of registered host IDs.")
                    return self.reply(controller.hosts.recommend(body, host_ids))
                return self.reply(controller.hosts.recommend(body))
            if self.command == "POST" and path == "/api/execution/plan":
                return self.reply(controller.plan_execution(self.body()))
            parts = path.strip("/").split("/")
            if len(parts) >= 3 and parts[0] == "api":
                identifier = require_id(parts[2])
                if parts[1] == "agent-questions" and len(parts) == 3 and self.command == "GET":
                    return self.reply(controller.questions.get(identifier))
                if parts[1] == "native-agents" and len(parts) == 4 and parts[3] == "probe" and self.command == "POST":
                    if self.body():
                        raise DomainError("Agent readiness check takes an empty object.")
                    return self.reply(controller.probe_native_agent(identifier))
                if parts[1] == "hosts" and len(parts) == 4 and parts[3] == "probe" and self.command == "POST":
                    if self.body():
                        raise DomainError("A host probe takes an empty object and uses its registered profile.")
                    return self.reply(controller.hosts.probe(identifier))
                if parts[1] == "connections":
                    if len(parts) == 3 and self.command == "PATCH":
                        return self.reply(controller.change_connection(identifier, self.body()))
                    if len(parts) == 3 and self.command == "DELETE":
                        if self.body():
                            raise DomainError("Removal takes an empty object.")
                        return self.reply(controller.remove_connection(identifier))
                    if len(parts) == 4 and parts[3] == "probe" and self.command == "POST":
                        body = self.body()
                        if set(body) != {"level"}:
                            raise DomainError("A probe requires only level.")
                        return self.reply(controller.probe(identifier, body["level"]))
                if parts[1] == "runs":
                    if len(parts) == 3 and self.command == "GET":
                        return self.reply(controller.store.get("run", identifier))
                    if len(parts) == 4 and parts[3] == "advance" and self.command == "POST":
                        if self.body():
                            raise DomainError("Replay takes an empty object.")
                        return self.reply(controller.advance(identifier))
                if parts[1] == "sessions" and len(parts) == 3 and self.command == "GET":
                    query = parse_qs(url.query, keep_blank_values=True)
                    raw = query.get("after", ["0"])
                    if set(query) - {"after"} or len(raw) != 1 or not raw[0].isdigit() or len(raw[0]) > 15:
                        raise DomainError("Invalid event cursor.")
                    return self.reply(controller.store.session(identifier, int(raw[0])))
                if parts[1] == "turns" and len(parts) == 4 and parts[3] == "cancel" and self.command == "POST":
                    if self.body():
                        raise DomainError("Cancellation takes an empty object.")
                    return self.reply(controller.store.cancel_turn(identifier))
            raise DomainError("This endpoint does not exist.", "not_found")

        def handle_request(self):
            try:
                self.dispatch()
            except DomainError as exc:
                status = {"not_found": 404, "conflict": 409, "unresolved_turn": 409, "busy": 429, "forbidden": 403, "too_large": 413}.get(exc.code, 400)
                self.reply({"error": {"code": exc.code, "message": str(exc)}}, status)
            except (TimeoutError, ConnectionError):
                self.close_connection = True
            except Exception:
                self.reply({"error": {"code": "internal_error", "message": "The local operation failed. Stored history is preserved."}}, 500)

        do_GET = do_POST = do_PATCH = do_DELETE = handle_request

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)

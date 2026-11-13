"""Bounded HTTP interface for the single-user offline prototype."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import secrets
from urllib.parse import parse_qs, urlsplit

from spiceowl.domain.common import DomainError, require_id


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DomainError("Duplicate JSON keys are not allowed.")
        result[key] = value
    return result


def make_server(controller, port=0):
    token = secrets.token_urlsafe(32)

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

        def dispatch(self):
            self.check_origin()
            url = urlsplit(self.path)
            path = url.path
            static = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/app.css": ("app.css", "text/css; charset=utf-8"), "/owl.svg": ("owl.svg", "image/svg+xml")}
            if self.command == "GET" and path in static:
                name, media = static[path]
                return self.send_content(200, files("spiceowl.web").joinpath("static", name).read_bytes(), media)
            if self.command == "GET" and path == "/api/state":
                return self.reply(controller.state() | {"csrf_token": token})
            if self.command == "POST" and path == "/api/demo":
                if self.body():
                    raise DomainError("Demo loading takes an empty object.")
                return self.reply(controller.seed_demo())
            if self.command == "POST" and path == "/api/connections":
                return self.reply(controller.add_connection(self.body()), 201)
            if self.command == "POST" and path == "/api/diagnose":
                return self.reply(controller.diagnose(self.body()), 202)
            parts = path.strip("/").split("/")
            if len(parts) >= 3 and parts[0] == "api":
                identifier = require_id(parts[2])
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

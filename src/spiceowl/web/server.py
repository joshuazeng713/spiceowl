"""Bounded loopback HTTP interface for synthetic runs and host connectivity."""

from html import escape as html_escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import re
import secrets
import time
from urllib.parse import parse_qs, urlsplit

from spiceowl import __version__
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


DEFAULT_WEB_ORIGINS = frozenset({"https://spiceowl-dev.github.io"})


def _connect_page_html(target_origin: str, port: int, nonce: str) -> bytes:
    """Render the 1-click local Connector authorization popup for the official web app."""
    safe_origin = html_escape(target_origin, quote=True)
    safe_nonce = html_escape(nonce, quote=True)
    js_origin = json.dumps(target_origin)
    js_nonce = json.dumps(nonce)
    js_base = json.dumps(f"http://127.0.0.1:{port}")
    body = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>授权 SpiceOwl 本机连接器</title>
<style>
body{{margin:0;padding:24px;font:14px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f4f5f0;color:#172e35}}
.card{{max-width:460px;margin:16px auto;background:#fff;border:1px solid #d7dcd3;border-radius:10px;padding:22px;box-shadow:0 8px 24px rgba(23,46,53,.08)}}
h1{{margin:0 0 8px;font-size:19px}}p{{margin:0 0 12px;color:#4a5d62}}
code{{background:#eef2ee;padding:2px 6px;border-radius:4px;font-size:12px}}
ul{{margin:8px 0 16px;padding-left:18px;color:#2f474d}}li{{margin-bottom:4px}}
.actions{{display:flex;gap:10px;margin-top:16px}}
button{{border:0;border-radius:6px;padding:10px 16px;font:600 14px/1 inherit;cursor:pointer}}
.primary{{background:#1f6f54;color:#fff}}.secondary{{background:#eef2ee;color:#172e35}}
.err{{color:#b42318;font-size:13px;min-height:18px}}
</style>
</head>
<body>
<div class="card">
  <h1>🦉 授权网页连接本机 SpiceOwl Connector</h1>
  <p>以下网页请求连接您本机后台运行的服务（<code>127.0.0.1:{port}</code>）：</p>
  <p><strong>{safe_origin}</strong></p>
  <ul>
    <li>读取本机 <code>~/.ssh/config</code> 别名并校验远端 EDA 服务器</li>
    <li>按您的操作提交 SSH / LSF / Slurm 仿真与流式读取远端大波形</li>
    <li>连接本机已安装的 Codex Agent 并接收夜间值守事件</li>
  </ul>
  <p style="font-size:12px">授权仅保存在当前浏览器标签页，关闭标签页或点击“断开连接”后立即失效。</p>
  <div id="err" class="err"></div>
  <div class="actions">
    <button id="approve" class="primary" type="button">确认授权并回到网页</button>
    <button id="cancel" class="secondary" type="button">取消</button>
  </div>
</div>
<script>
const targetOrigin = {js_origin};
const nonce = {js_nonce};
const baseUrl = {js_base};
document.getElementById('cancel').onclick = () => {{ window.close(); }};
document.getElementById('approve').onclick = async () => {{
  const btn = document.getElementById('approve');
  btn.disabled = true;
  document.getElementById('err').textContent = '';
  try {{
    const res = await fetch('/connect/approve', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{origin: targetOrigin, nonce}}),
    }});
    const data = await res.json();
    if (!res.ok) throw new Error(data.error?.message || '授权失败');
    if (window.opener) {{
      window.opener.postMessage({{type: 'spiceowl.connector.authorized', token: data.token, baseUrl, version: data.version}}, targetOrigin);
    }}
    window.close();
  }} catch (e) {{
    btn.disabled = false;
    document.getElementById('err').textContent = e.message || '授权失败';
  }}
}};
</script>
</body>
</html>"""
    return body.encode("utf-8")


def make_server(controller, port=0, live_client_config=None, *, codex_executable=None,
                codex_model=None, pdk_lut_path=None, circuitgit_client_config=None,
                circuitgit_sdk_path=None, enable_direct_exec=False, allowed_origins=None,
                ssh_dir=None):
    token = secrets.token_urlsafe(32)
    extra_origins = frozenset(allowed_origins) if allowed_origins is not None else DEFAULT_WEB_ORIGINS
    pairing_nonces: dict[str, tuple[str, float]] = {}
    ssh_setup_sessions: dict[str, tuple[str, str, float]] = {}
    live_client = None
    if live_client_config is not None:
        from spiceowl.integrations.live import LiveSimulationClient
        live_client = LiveSimulationClient(live_client_config)
    from spiceowl.direct import DirectExecutionManager, DirectRecoverySupervisor
    from spiceowl.batch import DirectBatchManager
    from spiceowl.watch_handoff import WatchHandoffManager, build_codex_bridge_pusher
    direct_manager = DirectExecutionManager(controller.store.directory, hosts=getattr(controller, "hosts", None))
    batch_manager = DirectBatchManager(controller.store.directory, direct_manager)
    handoff_manager = WatchHandoffManager(controller.store.directory)
    from spiceowl.codex_bridge import CodexBridge
    definitions, dispatcher = [], None
    if controller.agent_mode == 'standalone':
        from spiceowl.standalone_agent import build_standalone_dispatcher
        dispatcher = build_standalone_dispatcher(controller, live_client=live_client,
            pdk_lut_path=pdk_lut_path, circuitgit_client_config=circuitgit_client_config,
            circuitgit_sdk_path=circuitgit_sdk_path,
            direct_manager=direct_manager if enable_direct_exec else None,
            handoff_manager=handoff_manager)
        definitions = [{key: row[key] for key in ('name', 'description', 'inputSchema')}
                       for row in dispatcher.definitions()]
    controller.codex = CodexBridge(controller.store.directory / 'codex', mode=controller.agent_mode,
        dispatch_tool=dispatcher.dispatch if dispatcher else None,
        tool_policy=dispatcher.policy if dispatcher else None, tool_definitions=definitions,
        executable=codex_executable, model=codex_model)
    agent_pusher = build_codex_bridge_pusher(controller.codex)
    direct_supervisor = DirectRecoverySupervisor(
        direct_manager, handoff_manager=handoff_manager, agent_pusher=agent_pusher,
    )
    direct_monitor = direct_supervisor.monitor
    direct_supervisor.resume()

    def _register_and_probe_diagnosed_host(hosts, alias: str, diag: dict):
        from spiceowl.onboarding_wizard import quick_register_eda_host
        remote = diag.get("remote") or {}
        r_host = remote.get("hostname")
        r_user = remote.get("username")
        if not r_host or not r_user:
            return None, False, None
        host_id = re.sub(r"[^A-Za-z0-9_-]", "-", alias).strip("-") or "eda-host"
        if not host_id[0].isalnum():
            host_id = "h-" + host_id
        host_id = host_id[:96]
        sim = "spectre" if remote.get("spectre") else ("ngspice" if remote.get("ngspice") else "spectre")
        sim_exe = remote.get("spectre") or remote.get("ngspice") or None
        try:
            host, created = quick_register_eda_host(
                hosts,
                host_id=host_id,
                label=f"EDA ({alias})",
                ssh_target=alias,
                expected_host=r_host,
                expected_user=r_user,
                simulator=sim,
                simulator_executable=sim_exe,
            )
        except DomainError as exc:
            if exc.code != "conflict":
                raise
            hosts.remove(host_id)
            host, created = quick_register_eda_host(
                hosts,
                host_id=host_id,
                label=f"EDA ({alias})",
                ssh_target=alias,
                expected_host=r_host,
                expected_user=r_user,
                simulator=sim,
                simulator_executable=sim_exe,
            )
        probe_res = hosts.probe(host["host_id"])
        host = hosts.inspect(host["host_id"])
        return host, created, probe_res

    class Handler(BaseHTTPRequestHandler):
        server_version = "SpiceOwl"
        sys_version = ""

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *_):
            # Do not log user questions, bodies, tokens, or URLs.
            pass

        def _cors_origin(self):
            origin = self.headers.get("Origin")
            if origin is None:
                return None
            actual_port = self.server.server_address[1]
            loopback = {f"http://127.0.0.1:{actual_port}", f"http://localhost:{actual_port}"}
            if origin in loopback or origin in extra_origins:
                return origin
            return None

        def send_content(self, status, body, content_type="application/json; charset=utf-8", *, csp=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", csp or "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            cors_origin = self._cors_origin()
            if cors_origin is not None:
                self.send_header("Access-Control-Allow-Origin", cors_origin)
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.send_header("Vary", "Origin")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def reply(self, value, status=200):
            self.send_content(status, json.dumps(value, ensure_ascii=False, allow_nan=False).encode())

        def check_origin(self, *, path: str = ""):
            actual_port = self.server.server_address[1]
            authorities = {f"127.0.0.1:{actual_port}", f"localhost:{actual_port}"}
            hosts = self.headers.get_all("Host", [])
            if len(hosts) != 1 or hosts[0] not in authorities:
                raise DomainError("This server accepts only its loopback origin.", "forbidden")
            origin = self.headers.get("Origin")
            loopback_origins = {f"http://{host}" for host in authorities}
            is_web_origin = origin is not None and origin in extra_origins
            if origin is not None and origin not in loopback_origins and not is_web_origin:
                raise DomainError("Cross-origin access is not permitted.", "forbidden")
            if self.command == "OPTIONS":
                return
            if path == "/health" and self.command == "GET":
                return
            if path in ("/connect", "/connect/approve", "/ssh/setup", "/ssh/setup/action"):
                if is_web_origin:
                    raise DomainError("Local confirmation or SSH setup must occur inside the loopback popup.", "forbidden")
                return
            # Cross-origin web app MUST present the paired session token for EVERY API call (including GET & SSE).
            if is_web_origin:
                supplied = self.headers.get("X-SpiceOwl-Token", "")
                if not supplied and path == "/api/codex/events" and self.command == "GET":
                    q = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                    supplied = (q.get("token") or [""])[0]
                if not secrets.compare_digest(supplied, token):
                    raise DomainError("Pair this web tab with your local SpiceOwl Connector first.", "forbidden")
                return
            if self.command != "GET":
                supplied = self.headers.get("X-SpiceOwl-Token", "")
                if not secrets.compare_digest(supplied, token):
                    raise DomainError("Refresh the console before making changes.", "forbidden")

        def body(self, max_bytes=16384):
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or not lengths[0].isdigit():
                raise DomainError("A bounded Content-Length is required.")
            size = int(lengths[0])
            if not 0 < size <= max_bytes:
                raise DomainError(f"Request body must be between 1 and {max_bytes} bytes.", "too_large")
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
            if set(values) - {'after', 'token'} or len(raw) != 1 or any(not v.isdigit() or len(v) > 15 for v in (*raw, last)):
                raise DomainError('Invalid Codex event cursor.')
            after = max(int(raw[0]), int(last))
            # Validate before sending SSE headers. This endpoint only reads events.
            batch = controller.codex.events(after=after)
            self.send_response(200)
            headers_map = {'Content-Type': 'text/event-stream; charset=utf-8',
                    'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                    'Referrer-Policy': 'no-referrer', 'Connection': 'close'}
            cors_origin = self._cors_origin()
            if cors_origin is not None:
                headers_map['Access-Control-Allow-Origin'] = cors_origin
                headers_map['Access-Control-Allow-Private-Network'] = 'true'
                headers_map['Vary'] = 'Origin'
            for key, value in headers_map.items():
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
                'recover': ({'resolution', 'note', 'confirmed_side_effects_checked'}, set()),
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
            if action == 'recover': return bridge.reconcile_interrupted(**body)
            return bridge.disconnect()

        def dispatch(self):
            url = urlsplit(self.path)
            path = url.path
            self.check_origin(path=path)
            if self.command == "OPTIONS":
                cors_origin = self._cors_origin()
                if cors_origin is None:
                    raise DomainError("Cross-origin preflight is not permitted.", "forbidden")
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", cors_origin)
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-SpiceOwl-Token")
                self.send_header("Access-Control-Max-Age", "600")
                self.send_header("Vary", "Origin, Access-Control-Request-Private-Network")
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                return
            if self.command == "GET" and path == "/health":
                return self.reply({
                    "ok": True,
                    "product": "spiceowl",
                    "connector": True,
                    "version": __version__,
                    "port": self.server.server_address[1],
                })
            if self.command == "GET" and path == "/connect":
                query = parse_qs(url.query, keep_blank_values=True)
                target = (query.get("origin") or [""])[0]
                if target not in extra_origins:
                    raise DomainError("This origin is not permitted to pair with the local SpiceOwl Connector.", "forbidden")
                now_mono = time.monotonic()
                for stale in [k for k, (_, exp) in pairing_nonces.items() if exp < now_mono]:
                    pairing_nonces.pop(stale, None)
                nonce = secrets.token_urlsafe(24)
                pairing_nonces[nonce] = (target, now_mono + 300.0)
                page = _connect_page_html(target, self.server.server_address[1], nonce)
                return self.send_content(
                    200,
                    page,
                    "text/html; charset=utf-8",
                    csp="default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
                )
            if self.command == "POST" and path == "/connect/approve":
                req = self.body()
                target = req.get("origin")
                nonce = req.get("nonce")
                entry = pairing_nonces.pop(nonce, None) if isinstance(nonce, str) else None
                if entry is None or entry[0] != target or entry[1] < time.monotonic():
                    raise DomainError("Pairing session expired or invalid; reopen from SpiceOwl Web.", "forbidden")
                return self.reply({"authorized": True, "token": token, "version": __version__})
            if self.command == "GET" and path == "/ssh/setup":
                from spiceowl.onboarding.ssh_setup_ui import build_ssh_setup_state, ssh_setup_csp, ssh_setup_html
                query = parse_qs(url.query, keep_blank_values=True)
                actual_port = self.server.server_address[1]
                loopback_origins = {f"http://127.0.0.1:{actual_port}", f"http://localhost:{actual_port}"}
                target = (query.get("origin") or [f"http://127.0.0.1:{actual_port}"])[0]
                if target not in extra_origins and target not in loopback_origins:
                    raise DomainError("This origin is not permitted to open the local SSH setup wizard.", "forbidden")
                now_mono = time.monotonic()
                for stale in [k for k, (_, _, exp) in ssh_setup_sessions.items() if exp < now_mono]:
                    ssh_setup_sessions.pop(stale, None)
                if len(ssh_setup_sessions) >= 32:
                    oldest = min(ssh_setup_sessions, key=lambda k: ssh_setup_sessions[k][2])
                    ssh_setup_sessions.pop(oldest, None)
                request_id = secrets.token_urlsafe(18)
                setup_csrf = secrets.token_urlsafe(24)
                csp_nonce = secrets.token_urlsafe(18)
                ssh_setup_sessions[request_id] = (target, setup_csrf, now_mono + 1800.0)
                initial_alias = (query.get("alias") or [""])[0][:96]
                page = ssh_setup_html(
                    target,
                    request_id,
                    setup_csrf,
                    build_ssh_setup_state(ssh_dir),
                    csp_nonce,
                    brand="SpiceOwl",
                    action_path="/ssh/setup/action",
                    post_message_type="spiceowl.ssh.updated",
                    initial_alias=initial_alias,
                )
                return self.send_content(200, page, "text/html; charset=utf-8", csp=ssh_setup_csp(csp_nonce))
            if self.command == "POST" and path == "/ssh/setup/action":
                from spiceowl.onboarding.ssh_setup_ui import dispatch_ssh_setup_action
                req = self.body(max_bytes=32768)
                req_id = req.get("request_id")
                req_csrf = req.get("csrf")
                entry = ssh_setup_sessions.get(req_id) if isinstance(req_id, str) else None
                if entry is None or entry[2] < time.monotonic() or not isinstance(req_csrf, str) or not secrets.compare_digest(req_csrf, entry[1]):
                    raise DomainError("SSH setup session expired or invalid; reopen the SSH Setup Wizard.", "forbidden")
                action = req.get("action", "")
                payload = req.get("payload")
                result = dispatch_ssh_setup_action(ssh_dir, action, payload)
                if action == "delete_host" and isinstance(payload, dict) and result.get("deleted"):
                    alias = payload.get("alias")
                    if isinstance(alias, str) and alias.strip():
                        remaining = set((result.get("state") or {}).get("existing_aliases") or [])
                        if alias.strip() not in remaining:
                            result["removed_registered_hosts"] = controller.hosts.remove_by_ssh_target(alias.strip())
                elif action == "diagnose" and isinstance(payload, dict) and result.get("ok"):
                    alias = payload.get("alias")
                    diag = result.get("diagnosis") or {}
                    if isinstance(alias, str) and alias.strip():
                        try:
                            host, _, probe_res = _register_and_probe_diagnosed_host(controller.hosts, alias.strip(), diag)
                            if host is not None:
                                result["auto_connected"] = bool((probe_res or {}).get("identity", {}).get("verified"))
                                result["host_id"] = host["host_id"]
                        except Exception:
                            result["auto_connected"] = False
                return self.reply(result)
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
                if self.command == 'GET' and action == 'recovery':
                    return self.reply(controller.codex.recovery_report())
                if self.command == 'POST':
                    return self.reply(self.codex_control(action, self.body()), 202)
                raise DomainError('Unknown Codex client operation.', 'not_found')
            if path == '/api/agent/next-action' and self.command == 'POST':
                from spiceowl.orchestrator import evaluate_next_action
                return self.reply(evaluate_next_action(self.body()))
            if path == '/api/agent/eval' and self.command == 'POST':
                from spiceowl.agent_eval import compare_baselines, run_agent_eval
                req = self.body()
                if set(req) - {'baseline', 'repeats'}:
                    raise DomainError('Agent evaluation accepts optional baseline and repeats only.')
                baseline = req.get('baseline', 'compare')
                repeats = req.get('repeats', 1)
                if baseline == 'compare':
                    return self.reply(compare_baselines(repeats=repeats))
                return self.reply(run_agent_eval(baseline=baseline, repeats=repeats))
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
            if path == "/api/direct/runs" and self.command == "GET":
                if url.query:
                    raise DomainError("Direct runs read does not accept query parameters.")
                return self.reply({
                    "runs": direct_manager.list(),
                    "monitor": direct_monitor.snapshot(),
                    "recovery_reports": direct_manager.list_recovery_reports(),
                })
            if path == "/api/direct/prepare" and self.command == "POST":
                if url.query:
                    raise DomainError("Direct prepare does not accept query parameters.")
                payload = self.body(max_bytes=1024 * 1024 + 65536)
                allowed = {
                    "run_key", "netlist_text", "host_id", "host_ids", "backend", "simulator",
                    "output_format", "expected_version", "pin_version", "cpus", "memory_gib",
                    "wall_seconds", "license_queue_seconds", "enforce_rlimit_as", "scheduler",
                    "include_dirs", "remote_python", "remote_root", "source_commit", "recovery_policy",
                    "remote_env",
                }
                if set(payload) - allowed or not {"run_key", "netlist_text"} <= set(payload):
                    raise DomainError("Direct prepare requires run_key, netlist_text, and supported direct parameters only.")
                cpus = payload.get("cpus", 1)
                memory_gib = payload.get("memory_gib", 4.0)
                wall_seconds = payload.get("wall_seconds", 3600)
                lq_seconds = payload.get("license_queue_seconds", 120)
                enforce_as = payload.get("enforce_rlimit_as", False)
                if type(cpus) is not int or not 1 <= cpus <= 4096:
                    raise DomainError("cpus must be an integer between 1 and 4096.")
                if isinstance(memory_gib, bool) or not isinstance(memory_gib, (int, float)) or not 0.0625 <= float(memory_gib) <= 16384:
                    raise DomainError("memory_gib must be a number between 0.0625 and 16384.")
                limits = {
                    "cpu_count": cpus,
                    "memory_bytes": int(round(float(memory_gib) * 2**30)),
                    "wall_seconds": wall_seconds,
                    "license_queue_seconds": lq_seconds,
                    "enforce_rlimit_as": enforce_as,
                }
                run = direct_manager.prepare(
                    run_key=payload["run_key"],
                    netlist_text=payload["netlist_text"],
                    host_id=payload.get("host_id"),
                    host_ids=payload.get("host_ids"),
                    backend=payload.get("backend", "ssh_direct"),
                    simulator=payload.get("simulator", "spectre"),
                    expected_version=payload.get("expected_version"),
                    pin_version=payload.get("pin_version", True),
                    limits=limits,
                    scheduler=payload.get("scheduler"),
                    output_format=payload.get("output_format", "psfascii"),
                    include_dirs=payload.get("include_dirs"),
                    remote_python=payload.get("remote_python", "/usr/bin/python3"),
                    remote_root=payload.get("remote_root", ".spiceowl-runs"),
                    remote_env=payload.get("remote_env"),
                    source_commit=payload.get("source_commit"),
                    recovery_policy=payload.get("recovery_policy"),
                )
                return self.reply({"run": run}, 201)
            if path == "/api/wizard/status" and self.command == "GET":
                from pathlib import Path as _Path
                from spiceowl.onboarding_wizard import diagnose_local_environment
                return self.reply(diagnose_local_environment(
                    controller.store.directory,
                    hosts=controller.hosts,
                    direct_manager=direct_manager,
                    batch_manager=batch_manager,
                    ssh_config_path=(_Path(ssh_dir) / "config") if ssh_dir is not None else None,
                ))
            if path == "/api/wizard/demo" and self.command == "POST":
                if self.body():
                    raise DomainError("Wizard demo bootstrap takes an empty object.")
                from spiceowl.onboarding_wizard import bootstrap_vivid_demo
                return self.reply(bootstrap_vivid_demo(controller, direct_manager, batch_manager), 201)
            if path == "/api/wizard/quick-connect" and self.command == "POST":
                from spiceowl.onboarding.ssh_discovery import validate_alias
                from spiceowl.onboarding.ssh_setup import diagnose_connection
                req = self.body()
                if set(req) - {"alias"} or "alias" not in req:
                    raise DomainError("Quick connect requires only an SSH alias.")
                alias = validate_alias(req["alias"])
                diag = diagnose_connection(ssh_dir, alias)
                host, created, probe_res = _register_and_probe_diagnosed_host(controller.hosts, alias, diag)
                if host is None:
                    return self.reply({"ok": False, "diagnosis": diag})
                return self.reply({
                    "ok": bool(diag.get("ok")),
                    "host": host,
                    "created": created,
                    "probe": probe_res,
                    "diagnosis": diag,
                })
            if path == "/api/wizard/disconnect" and self.command == "POST":
                from spiceowl.onboarding.ssh_discovery import validate_alias
                req = self.body()
                if set(req) - {"alias"} or "alias" not in req:
                    raise DomainError("Disconnect requires only an SSH alias.")
                alias = validate_alias(req["alias"])
                removed = controller.hosts.remove_by_ssh_target(alias)
                if not removed:
                    host_id = re.sub(r"[^A-Za-z0-9_-]", "-", alias).strip("-")
                    if host_id and not host_id[0].isalnum():
                        host_id = "h-" + host_id
                    if host_id and controller.hosts.remove(host_id[:96]):
                        removed = [host_id[:96]]
                return self.reply({
                    "ok": True,
                    "alias": alias,
                    "disconnected": bool(removed),
                    "removed_hosts": removed,
                })
            if path == "/api/wizard/register-host" and self.command == "POST":
                from spiceowl.onboarding_wizard import quick_register_eda_host
                req = self.body()
                allowed = {
                    "host_id", "label", "ssh_target", "expected_host", "expected_user",
                    "simulator", "simulator_executable", "ssh_hostname", "probe",
                }
                required = {"host_id", "ssh_target", "expected_host", "expected_user"}
                if set(req) - allowed or not required <= set(req):
                    raise DomainError("Host registration requires host_id, ssh_target, expected_host, and expected_user.")
                if "probe" in req and type(req["probe"]) is not bool:
                    raise DomainError("probe must be boolean.")
                host, created = quick_register_eda_host(
                    controller.hosts,
                    host_id=req["host_id"],
                    label=req.get("label") or req["host_id"],
                    ssh_target=req["ssh_target"],
                    expected_host=req["expected_host"],
                    expected_user=req["expected_user"],
                    simulator=req.get("simulator", "spectre"),
                    simulator_executable=req.get("simulator_executable"),
                    ssh_hostname=req.get("ssh_hostname"),
                )
                probe_res = controller.hosts.probe(host["host_id"]) if req.get("probe") else None
                return self.reply({"host": controller.hosts.inspect(host["host_id"]), "created": created, "probe": probe_res}, 201 if created else 200)
            if path == "/api/wizard/first-sim" and self.command == "POST":
                from spiceowl.onboarding_wizard import prepare_first_simulation
                req = self.body(max_bytes=1024 * 1024 + 65536)
                allowed = {
                    "host_id", "simulator", "backend", "template_id", "netlist_text",
                    "run_key", "queue", "partition", "remote_python", "remote_root", "output_format",
                }
                if set(req) - allowed or "host_id" not in req:
                    raise DomainError("First simulation setup requires host_id and supported template parameters only.")
                return self.reply(prepare_first_simulation(
                    direct_manager,
                    host_id=req["host_id"],
                    simulator=req.get("simulator", "spectre"),
                    backend=req.get("backend", "ssh_direct"),
                    template_id=req.get("template_id"),
                    netlist_text=req.get("netlist_text"),
                    run_key=req.get("run_key"),
                    queue=req.get("queue"),
                    partition=req.get("partition"),
                    remote_python=req.get("remote_python", "/usr/bin/python3"),
                    remote_root=req.get("remote_root", ".spiceowl-runs"),
                    output_format=req.get("output_format"),
                ), 201)
            if path == "/api/direct/batches" and self.command == "GET":
                return self.reply({"batches": batch_manager.list()})
            if path == "/api/direct/batches/demo" and self.command == "POST":
                req = self.body()
                if set(req) - {"kind", "batch_id"}:
                    raise DomainError("Batch demo accepts optional kind and batch_id only.")
                kind = req.get("kind", "pvt")
                return self.reply(batch_manager.create_demo_batch(kind=kind, batch_id=req.get("batch_id")), 201)
            if path == "/api/direct/batches/prepare" and self.command == "POST":
                req = self.body(max_bytes=1024 * 1024 + 65536)
                allowed = {
                    "batch_id", "netlist_template", "kind", "title",
                    "process_corners", "voltages", "temperatures", "voltage_param",
                    "mc_samples", "mc_base_seed", "mc_variation", "mc_seeds",
                    "specs", "max_concurrency", "host_id", "host_ids",
                    "backend", "simulator", "output_format", "cpus", "memory_gib",
                    "wall_seconds", "license_queue_seconds", "enforce_rlimit_as",
                    "scheduler", "include_dirs", "remote_python", "remote_root", "recovery_policy",
                }
                if set(req) - allowed or not {"batch_id", "netlist_template"} <= set(req):
                    raise DomainError("Batch prepare requires batch_id, netlist_template, and supported batch fields only.")
                cpus = req.get("cpus", 1)
                memory_gib = req.get("memory_gib", 4.0)
                wall_seconds = req.get("wall_seconds", 3600)
                lq_seconds = req.get("license_queue_seconds", 120)
                enforce_as = req.get("enforce_rlimit_as", False)
                if type(cpus) is not int or not 1 <= cpus <= 4096:
                    raise DomainError("cpus must be an integer between 1 and 4096.")
                if isinstance(memory_gib, bool) or not isinstance(memory_gib, (int, float)) or not 0.0625 <= float(memory_gib) <= 16384:
                    raise DomainError("memory_gib must be a number between 0.0625 and 16384.")
                limits = {
                    "cpu_count": cpus,
                    "memory_bytes": int(round(float(memory_gib) * 2**30)),
                    "wall_seconds": wall_seconds,
                    "license_queue_seconds": lq_seconds,
                    "enforce_rlimit_as": enforce_as,
                }
                created = batch_manager.prepare(
                    batch_id=req["batch_id"],
                    netlist_template=req["netlist_template"],
                    kind=req.get("kind", "pvt"),
                    title=req.get("title"),
                    process_corners=req.get("process_corners"),
                    voltages=req.get("voltages"),
                    temperatures=req.get("temperatures"),
                    voltage_param=req.get("voltage_param", "vdd"),
                    mc_samples=req.get("mc_samples", 0),
                    mc_base_seed=req.get("mc_base_seed", 1001),
                    mc_variation=req.get("mc_variation", "all"),
                    mc_seeds=req.get("mc_seeds"),
                    specs=req.get("specs"),
                    max_concurrency=req.get("max_concurrency", 4),
                    host_id=req.get("host_id"),
                    host_ids=req.get("host_ids"),
                    backend=req.get("backend", "ssh_direct"),
                    simulator=req.get("simulator", "spectre"),
                    output_format=req.get("output_format", "psfbin"),
                    limits=limits,
                    scheduler=req.get("scheduler"),
                    include_dirs=req.get("include_dirs"),
                    remote_python=req.get("remote_python", "/usr/bin/python3"),
                    remote_root=req.get("remote_root", ".spiceowl-runs"),
                    recovery_policy=req.get("recovery_policy"),
                )
                return self.reply(created, 201)
            if path.startswith("/api/direct/batches/"):
                sub = path.removeprefix("/api/direct/batches/").split("/")
                if len(sub) not in (1, 2):
                    raise DomainError("This endpoint does not exist.", "not_found")
                batch_id = require_id(sub[0], "direct batch ID")
                if len(sub) == 1 and self.command == "GET":
                    return self.reply(batch_manager.report(batch_id))
                if len(sub) == 2 and self.command == "POST":
                    op = sub[1]
                    req = self.body()
                    if op == "dispatch":
                        if set(req) - {"limit", "recheck_resources"}:
                            raise DomainError("Dispatch accepts optional limit and recheck_resources only.")
                        if "recheck_resources" in req and type(req["recheck_resources"]) is not bool:
                            raise DomainError("recheck_resources must be boolean.")
                        return self.reply(batch_manager.dispatch_next(
                            batch_id, limit=req.get("limit"), recheck_resources=req.get("recheck_resources"),
                        ), 202)
                    if op == "refresh":
                        if set(req) - {"auto_collect", "dispatch_more", "recheck_resources"}:
                            raise DomainError("Refresh accepts optional auto_collect, dispatch_more, and recheck_resources only.")
                        for k in ("auto_collect", "dispatch_more", "recheck_resources"):
                            if k in req and type(req[k]) is not bool:
                                raise DomainError(f"{k} must be boolean.")
                        return self.reply(batch_manager.refresh(
                            batch_id,
                            auto_collect=req.get("auto_collect", True),
                            dispatch_more=req.get("dispatch_more", False),
                            recheck_resources=req.get("recheck_resources"),
                        ))
                    if op == "cancel":
                        if req:
                            raise DomainError("Batch cancel takes an empty object.")
                        return self.reply(batch_manager.cancel(batch_id), 202)
                raise DomainError("This endpoint does not exist.", "not_found")
            if path == "/api/direct/demo-waveform" and self.command == "POST":
                req = self.body()
                allowed = {"run_id", "cycles", "glitch_cycle"}
                if set(req) - allowed:
                    raise DomainError("Demo waveform accepts optional run_id, cycles, and glitch_cycle only.")
                run_id = req.get("run_id", "demo-psfbin-500c")
                cycles = req.get("cycles", 500)
                glitch_cycle = req.get("glitch_cycle", 317)
                if type(cycles) is not int or not 10 <= cycles <= 5000:
                    raise DomainError("cycles must be an integer between 10 and 5000.")
                if type(glitch_cycle) is not int or not 0 <= glitch_cycle < cycles:
                    raise DomainError("glitch_cycle must be an integer within [0, cycles - 1].")
                run = direct_manager.create_demo_waveform_run(
                    run_id=run_id, cycles=cycles, glitch_cycle=glitch_cycle,
                )
                return self.reply(
                    {"run": run, "waveforms": direct_manager.waveform_index(run["run_id"], allow_remote=False)},
                    201,
                )
            if path == "/api/direct/watch-events" and self.command == "GET":
                query = parse_qs(url.query, keep_blank_values=True)
                state_filter = query["state"][0] if "state" in query and query["state"] else None
                st = controller.codex.status()
                return self.reply({
                    "events": handoff_manager.list_events(state=state_filter, limit=50),
                    "policy": handoff_manager.get_policy(),
                    "agent_connected": bool(st.get("connected")),
                    "agent_state": st.get("state") or "disconnected",
                })
            if path == "/api/direct/watch-policy" and self.command == "GET":
                return self.reply({"policy": handoff_manager.get_policy()})
            if path == "/api/direct/watch-policy" and self.command == "POST":
                req = self.body()
                updated = handoff_manager.update_policy(req)
                return self.reply({"policy": updated})
            if path == "/api/direct/watch-events/simulate" and self.command == "POST":
                req = self.body()
                allowed = {"scenario"}
                if set(req) - allowed:
                    raise DomainError("Watch handoff simulation accepts only scenario.")
                ev = handoff_manager.simulate_overnight_handoff(scenario=req.get("scenario") or "queue_timeout")
                return self.reply({"event": ev, "events": handoff_manager.list_events(limit=50)}, 201)
            if path.startswith("/api/direct/watch-events/") and path.endswith("/push-agent") and self.command == "POST":
                ev_id = require_id(path.removeprefix("/api/direct/watch-events/").removesuffix("/push-agent"), "watch event ID")
                ev = handoff_manager.push_event_to_agent(ev_id, agent_pusher=agent_pusher)
                return self.reply({"event": ev})
            if path.startswith("/api/direct/watch-events/") and path.endswith("/decision") and self.command == "POST":
                ev_id = require_id(path.removeprefix("/api/direct/watch-events/").removesuffix("/decision"), "watch event ID")
                req = self.body()
                allowed = {"outcome", "rationale", "resource_assessment", "config_changes", "new_run_id", "needs_human_review"}
                if set(req) - allowed or not {"outcome", "rationale"} <= set(req):
                    raise DomainError("Watch decision requires outcome and rationale.")
                ev = handoff_manager.record_agent_decision(
                    ev_id,
                    outcome=req["outcome"],
                    rationale=req["rationale"],
                    resource_assessment=req.get("resource_assessment"),
                    config_changes=req.get("config_changes"),
                    new_run_id=req.get("new_run_id"),
                    needs_human_review=bool(req.get("needs_human_review", False)),
                )
                return self.reply({"event": ev})
            if path == "/api/direct/recovery-reports" and self.command == "GET":
                query = parse_qs(url.query, keep_blank_values=True)
                if set(query) - {"q", "outcome"}:
                    raise DomainError("Recovery report search accepts only q and outcome.")
                q_val = query["q"][0] if "q" in query and query["q"] else None
                outcome_val = query["outcome"][0] if "outcome" in query and query["outcome"] else None
                return self.reply({"reports": direct_manager.list_recovery_reports(query=q_val, outcome=outcome_val)})
            if path.startswith("/api/direct/recovery-reports/") and self.command == "GET":
                target_id = require_id(path.removeprefix("/api/direct/recovery-reports/"), "recovery report ID")
                return self.reply(direct_manager.get_recovery_report(target_id))
            if path == "/api/direct/submission" and self.command == "GET":
                query = parse_qs(url.query, keep_blank_values=True)
                if set(query) != {"key"} or len(query["key"]) != 1:
                    raise DomainError("An exact submission key is required.")
                return self.reply(direct_manager.lookup_submission(query["key"][0]))
            if path.startswith("/api/direct/runs/"):
                sub = path.removeprefix("/api/direct/runs/").split("/")
                if len(sub) not in (1, 2, 3):
                    raise DomainError("This endpoint does not exist.", "not_found")
                run_id = require_id(sub[0], "direct run ID")
                if len(sub) == 1 and self.command == "GET":
                    run = direct_manager.read(run_id)
                    try:
                        rep = direct_manager.report(run_id)
                    except DomainError:
                        rep = None
                    try:
                        wf = direct_manager.waveform_index(run_id, allow_remote=False)
                    except DomainError:
                        wf = None
                    return self.reply({"run": run, "report": rep, "waveforms": wf, "monitor": direct_monitor.snapshot(run_id)})
                if len(sub) == 2 and sub[1] == "waveforms" and self.command == "GET":
                    query = parse_qs(url.query, keep_blank_values=True)
                    if set(query) - {"refresh_remote", "allow_remote"}:
                        raise DomainError("Waveform index accepts only refresh_remote and allow_remote.")
                    refresh_remote = query.get("refresh_remote", ["0"])[0] in ("1", "true", "yes")
                    allow_remote = query.get("allow_remote", ["1"])[0] not in ("0", "false", "no")
                    return self.reply(direct_manager.waveform_index(
                        run_id, refresh_remote=refresh_remote, allow_remote=allow_remote,
                    ))
                if len(sub) == 3 and sub[1] == "waveforms" and self.command == "POST":
                    req = self.body()
                    wf_op = sub[2]
                    if wf_op == "query":
                        allowed = {"dataset", "signals", "x_min", "x_max", "max_points", "mode"}
                        if set(req) - allowed or not {"dataset", "signals"} <= set(req):
                            raise DomainError("Waveform query requires dataset and signals.")
                        if not isinstance(req["dataset"], str) or not req["dataset"].strip():
                            raise DomainError("dataset must be a non-empty string.")
                        for bound_name in ("x_min", "x_max"):
                            bval = req.get(bound_name)
                            if bval is not None and (isinstance(bval, bool) or not isinstance(bval, (int, float))):
                                raise DomainError(f"{bound_name} must be numeric or null.")
                        max_pts = req.get("max_points", 2000)
                        if type(max_pts) is not int or not 16 <= max_pts <= 20000:
                            raise DomainError("max_points must be an integer between 16 and 20000.")
                        mode = req.get("mode", "auto")
                        if mode not in ("auto", "exact"):
                            raise DomainError("mode must be 'auto' or 'exact'.")
                        return self.reply(direct_manager.waveform_query(
                            run_id,
                            req["dataset"],
                            req["signals"],
                            x_min=float(req["x_min"]) if req.get("x_min") is not None else None,
                            x_max=float(req["x_max"]) if req.get("x_max") is not None else None,
                            max_points=max_pts,
                            mode=mode,
                        ))
                    if wf_op == "cycles":
                        allowed = {
                            "dataset", "signals", "period", "clock_signal", "threshold", "edge",
                            "t_start", "t_end", "worst_metric", "worst_signal", "cycle_index",
                            "context_cycles", "overlay_cycles", "max_points_per_cycle",
                        }
                        if set(req) - allowed or not {"dataset", "signals"} <= set(req):
                            raise DomainError("Cycle analysis requires dataset and signals.")
                        if not isinstance(req["dataset"], str) or not req["dataset"].strip():
                            raise DomainError("dataset must be a non-empty string.")
                        for num_field in ("period", "threshold", "t_start", "t_end"):
                            val = req.get(num_field)
                            if val is not None and (isinstance(val, bool) or not isinstance(val, (int, float))):
                                raise DomainError(f"{num_field} must be numeric or null.")
                        for int_field in ("cycle_index", "context_cycles", "overlay_cycles", "max_points_per_cycle"):
                            val = req.get(int_field)
                            if val is not None and type(val) is not int:
                                raise DomainError(f"{int_field} must be an integer or null.")
                        for str_field in ("clock_signal", "worst_signal"):
                            val = req.get(str_field)
                            if val is not None and (not isinstance(val, str) or not val.strip()):
                                raise DomainError(f"{str_field} must be a non-empty string or null.")
                        return self.reply(direct_manager.waveform_cycles(
                            run_id,
                            req["dataset"],
                            req["signals"],
                            period=float(req["period"]) if req.get("period") is not None else None,
                            clock_signal=req.get("clock_signal"),
                            threshold=float(req["threshold"]) if req.get("threshold") is not None else None,
                            edge=req.get("edge", "rising"),
                            t_start=float(req["t_start"]) if req.get("t_start") is not None else None,
                            t_end=float(req["t_end"]) if req.get("t_end") is not None else None,
                            worst_metric=req.get("worst_metric", "peak_to_peak"),
                            worst_signal=req.get("worst_signal"),
                            cycle_index=req.get("cycle_index"),
                            context_cycles=req.get("context_cycles", 0),
                            overlay_cycles=req.get("overlay_cycles", 10),
                            max_points_per_cycle=req.get("max_points_per_cycle", 2000),
                        ))
                    if wf_op == "measure":
                        allowed = {"dataset", "signal", "x_min", "x_max", "tol_pct"}
                        if set(req) - allowed or not {"dataset", "signal"} <= set(req):
                            raise DomainError("Waveform measure requires dataset and signal.")
                        if not isinstance(req["dataset"], str) or not req["dataset"].strip():
                            raise DomainError("dataset must be a non-empty string.")
                        if not isinstance(req["signal"], str) or not req["signal"].strip():
                            raise DomainError("signal must be a non-empty string.")
                        for bound_name in ("x_min", "x_max", "tol_pct"):
                            bval = req.get(bound_name)
                            if bval is not None and (isinstance(bval, bool) or not isinstance(bval, (int, float))):
                                raise DomainError(f"{bound_name} must be numeric or null.")
                        return self.reply(direct_manager.waveform_measure(
                            run_id,
                            req["dataset"],
                            req["signal"],
                            x_min=float(req["x_min"]) if req.get("x_min") is not None else None,
                            x_max=float(req["x_max"]) if req.get("x_max") is not None else None,
                            tol_pct=float(req["tol_pct"]) if req.get("tol_pct") is not None else 2.0,
                        ))
                    raise DomainError("This endpoint does not exist.", "not_found")
                if len(sub) == 2 and self.command == "POST":
                    op = sub[1]
                    req = self.body()
                    if op == "stage":
                        if req:
                            raise DomainError("Stage takes an empty object.")
                        return self.reply({"run": direct_manager.stage(run_id)}, 202)
                    if op == "launch":
                        if set(req) - {"recheck_resources"} or ("recheck_resources" in req and type(req["recheck_resources"]) is not bool):
                            raise DomainError("Launch accepts an empty object or boolean recheck_resources.")
                        return self.reply({"run": direct_manager.launch(run_id, recheck_resources=req.get("recheck_resources"))}, 202)
                    if op == "status":
                        if req:
                            raise DomainError("Status check takes an empty object.")
                        return self.reply({"run": direct_manager.status(run_id)})
                    if op == "cancel":
                        if set(req) - {"action_key"}:
                            raise DomainError("Cancel accepts an empty object or an explicit action_key.")
                        return self.reply({"cancel": direct_manager.cancel(run_id, action_key=req.get("action_key")), "run": direct_manager.read(run_id)}, 202)
                    if op == "collect":
                        if req:
                            raise DomainError("Collect takes an empty object.")
                        collected = direct_manager.collect(run_id)
                        try:
                            rep = direct_manager.report(run_id)
                        except DomainError:
                            rep = None
                        return self.reply({"run": collected, "report": rep}, 202)
                    if op == "watch":
                        if set(req) - {"allow_collect"} or ("allow_collect" in req and type(req["allow_collect"]) is not bool):
                            raise DomainError("Watch accepts an empty object or boolean allow_collect.")
                        try:
                            receipt = direct_monitor.watch(run_id, allow_collect=req.get("allow_collect", False))
                            direct_monitor.start()
                        except ValueError as exc:
                            raise DomainError(str(exc), "watch_unavailable") from None
                        return self.reply({"monitor": receipt, "run": direct_manager.read(run_id)}, 202)
                    if op == "unwatch":
                        if req:
                            raise DomainError("Unwatch takes an empty object.")
                        try:
                            receipt = direct_monitor.unwatch(run_id)
                        except ValueError as exc:
                            raise DomainError(str(exc), "watch_unavailable") from None
                        return self.reply({"monitor": receipt, "run": direct_manager.read(run_id)})
                    if op == "policy":
                        return self.reply({"run": direct_manager.set_recovery_policy(run_id, req)})
                    if op == "recover":
                        if set(req) - {"execute"} or ("execute" in req and type(req["execute"]) is not bool):
                            raise DomainError("Recover accepts an empty object or boolean execute.")
                        outcome = direct_manager.evaluate_and_recover(
                            run_id, execute=req.get("execute", True), monitor=direct_monitor,
                        )
                        return self.reply(outcome, 202 if outcome["decision"].get("result") == "allow" else 200)
                raise DomainError("This endpoint does not exist.", "not_found")
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
            if self.command == "POST" and path == "/api/hosts/live-status":
                from spiceowl.host_activity import inspect_pool_live_status
                body = self.body()
                if not body:
                    return self.reply(inspect_pool_live_status(controller.hosts, direct_manager))
                if set(body) != {"host_ids"} or not isinstance(body["host_ids"], list):
                    raise DomainError("Live status takes an empty object or exactly a host_ids list.")
                return self.reply(inspect_pool_live_status(controller.hosts, direct_manager, host_ids=body["host_ids"]))
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

        do_GET = do_POST = do_PATCH = do_DELETE = do_OPTIONS = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    original_close = server.server_close
    def close_with_monitor():
        try:
            direct_supervisor.stop(timeout=1)
        finally:
            original_close()
    server.server_close = close_with_monitor
    return server

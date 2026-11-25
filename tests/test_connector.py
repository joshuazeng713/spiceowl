"""Tests for the headless local SpiceOwl Connector, CORS pairing flow, and spiceowl:// URI registration."""

from __future__ import annotations

import http.client
import json
import plistlib
import re
import tempfile
import threading
import unittest
from pathlib import Path

from spiceowl.connector import (
    install_connector,
    parse_connector_uri,
    uninstall_connector,
)
from spiceowl.domain.common import DomainError
from spiceowl.service import Controller
from spiceowl.web.server import make_server


OFFICIAL_ORIGIN = "https://spiceowl-dev.github.io"
EVIL_ORIGIN = "https://evil.example"


class TestLocalConnectorAndPairing(unittest.TestCase):
    def test_uri_scheme_parser(self) -> None:
        self.assertEqual(parse_connector_uri("spiceowl://start"), {"scheme": "spiceowl", "action": "start"})
        self.assertEqual(parse_connector_uri("spiceowl://connect"), {"scheme": "spiceowl", "action": "connect"})
        self.assertEqual(parse_connector_uri("SPICEOWL://status"), {"scheme": "spiceowl", "action": "status"})
        for bad in ("", "http://127.0.0.1:8765", "spiceowl://delete-all", "javascript:alert(1)"):
            with self.assertRaises(DomainError):
                parse_connector_uri(bad)

    def test_cross_platform_connector_installer_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spiceowl-conn-inst-") as tmp:
            root = Path(tmp)
            home = root / "home"
            data = root / "data"

            # macOS .app bundle + LaunchAgent plist
            mac = install_connector(port=8765, data_dir=data, autostart=True, home_dir=home, platform_override="macos")
            app_dir = Path(mac["app_bundle"])
            self.assertTrue((app_dir / "Contents" / "MacOS" / "spiceowl-connector").is_file())
            plist = plistlib.loads((app_dir / "Contents" / "Info.plist").read_bytes())
            self.assertEqual(plist["CFBundleURLTypes"][0]["CFBundleURLSchemes"], ["spiceowl"])
            self.assertTrue(plist["LSBackgroundOnly"])
            self.assertTrue(Path(mac["autostart_path"]).is_file())
            un_mac = uninstall_connector(home_dir=home, platform_override="macos")
            self.assertEqual(len(un_mac["removed"]), 2)
            self.assertFalse(app_dir.exists())

            # Linux .desktop + autostart
            lin = install_connector(port=8765, data_dir=data, autostart=True, home_dir=home, platform_override="linux")
            self.assertIn("x-scheme-handler/spiceowl", Path(lin["desktop_file"]).read_text(encoding="utf-8"))
            self.assertTrue(Path(lin["autostart_path"]).is_file())
            un_lin = uninstall_connector(home_dir=home, platform_override="linux")
            self.assertEqual(len(un_lin["removed"]), 2)

            # Windows HKCU registry key generation (dry_run works on all OSes)
            win = install_connector(port=8765, data_dir=data, autostart=True, home_dir=home, platform_override="windows", dry_run=True)
            self.assertIn(r"HKCU\Software\Classes\spiceowl\shell\open\command", win["registry_keys"])
            self.assertIn("connector handle-uri", win["registry_keys"][r"HKCU\Software\Classes\spiceowl\shell\open\command"])
            self.assertIn("--headless", win["autostart_command"])

    def test_connector_health_cors_and_pairing_security_boundary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spiceowl-conn-srv-") as tmp:
            ctrl = Controller(tmp, agent_mode="standalone")
            srv = make_server(ctrl, port=0, enable_direct_exec=True)
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            try:
                port = srv.server_address[1]

                def raw_req(method: str, path: str, headers: dict | None = None, body: dict | None = None):
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                    hdrs = dict(headers or {})
                    payload = None
                    if body is not None:
                        hdrs["Content-Type"] = "application/json"
                        payload = json.dumps(body).encode("utf-8")
                    conn.request(method, path, body=payload, headers=hdrs)
                    resp = conn.getresponse()
                    raw_body = resp.read()
                    resp_headers = {k.lower(): v for k, v in resp.getheaders()}
                    conn.close()
                    return resp.status, resp_headers, raw_body

                # 1. GET /health succeeds for official github.io origin and returns CORS header
                st, hdrs, raw = raw_req("GET", "/health", {"Origin": OFFICIAL_ORIGIN})
                self.assertEqual(st, 200)
                self.assertEqual(hdrs.get("access-control-allow-origin"), OFFICIAL_ORIGIN)
                health = json.loads(raw.decode("utf-8"))
                self.assertTrue(health["ok"])
                self.assertEqual(health["product"], "spiceowl")

                # 2. Foreign origin is STRICTLY rejected on /health, OPTIONS, /api/state, and /connect
                self.assertEqual(raw_req("GET", "/health", {"Origin": EVIL_ORIGIN})[0], 403)
                self.assertEqual(raw_req("OPTIONS", "/api/state", {"Origin": EVIL_ORIGIN})[0], 403)
                self.assertEqual(raw_req("GET", "/api/state", {"Origin": EVIL_ORIGIN})[0], 403)
                self.assertEqual(raw_req("GET", f"/connect?origin={EVIL_ORIGIN}")[0], 403)

                # 3. OPTIONS preflight succeeds for official origin
                st, hdrs, _ = raw_req("OPTIONS", "/api/state", {"Origin": OFFICIAL_ORIGIN})
                self.assertEqual(st, 204)
                self.assertEqual(hdrs.get("access-control-allow-origin"), OFFICIAL_ORIGIN)
                self.assertIn("X-SpiceOwl-Token", hdrs.get("access-control-allow-headers", ""))

                # 4. Unpaired official origin CANNOT read /api/state without the paired token!
                self.assertEqual(raw_req("GET", "/api/state", {"Origin": OFFICIAL_ORIGIN})[0], 403)

                # 5. User completes 1-click pairing popup on loopback /connect -> /connect/approve
                st, _, html_bytes = raw_req("GET", f"/connect?origin={OFFICIAL_ORIGIN}")
                self.assertEqual(st, 200)
                html = html_bytes.decode("utf-8")
                m = re.search(r'const nonce = "([^"]+)"', html)
                self.assertIsNotNone(m)
                nonce = m.group(1)

                st, _, appr_raw = raw_req(
                    "POST",
                    "/connect/approve",
                    {"Origin": f"http://127.0.0.1:{port}"},
                    {"origin": OFFICIAL_ORIGIN, "nonce": nonce},
                )
                self.assertEqual(st, 200)
                token = json.loads(appr_raw.decode("utf-8"))["token"]
                self.assertTrue(len(token) >= 20)

                # Replaying the one-time nonce must fail!
                self.assertEqual(
                    raw_req(
                        "POST",
                        "/connect/approve",
                        {"Origin": f"http://127.0.0.1:{port}"},
                        {"origin": OFFICIAL_ORIGIN, "nonce": nonce},
                    )[0],
                    403,
                )

                # 6. Paired official origin can now read /api/state and mutate /api/direct/watch-policy
                st, hdrs, state_raw = raw_req(
                    "GET",
                    "/api/state",
                    {"Origin": OFFICIAL_ORIGIN, "X-SpiceOwl-Token": token},
                )
                self.assertEqual(st, 200)
                self.assertEqual(hdrs.get("access-control-allow-origin"), OFFICIAL_ORIGIN)
                self.assertIn("version", json.loads(state_raw.decode("utf-8")))

                st, hdrs, pol_raw = raw_req(
                    "POST",
                    "/api/direct/watch-policy",
                    {"Origin": OFFICIAL_ORIGIN, "X-SpiceOwl-Token": token},
                    {"enabled": True, "queue_timeout_seconds": 600},
                )
                self.assertEqual(st, 200)
                self.assertTrue(json.loads(pol_raw.decode("utf-8"))["policy"]["enabled"])
            finally:
                srv.shutdown()
                srv.server_close()
                ctrl.close()


if __name__ == "__main__":
    unittest.main()

import http.client
import json
import tempfile
import threading
import unittest

from spiceowl.service import Controller
from spiceowl.web.server import make_server


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Controller(self.temp.name)
        self.server = make_server(self.app)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        status, data, _ = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.token = data["csrf_token"]

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.app.close()
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        client = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            hdrs = {"Content-Type": "application/json"} | (headers or {})
            encoded = json.dumps(body) if isinstance(body, dict) else body
            client.request(method, path, body=encoded, headers=hdrs)
            response = client.getresponse()
            content = response.read()
            data = json.loads(content) if response.getheader("Content-Type", "").startswith("application/json") else content
            return response.status, data, dict(response.getheaders())
        finally:
            client.close()

    def mutate(self, method, path, body=None, headers=None):
        return self.request(method, path, body or {}, {"X-SpiceOwl-Token": self.token} | (headers or {}))

    def test_onboarding_and_assets(self):
        status, body, _ = self.mutate("POST", "/api/demo")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["runs"]), 4)
        for path, expected in (("/", b"SpiceOwl"), ("/app.js", b"diagnose"), ("/app.css", b".sidebar"), ("/owl.svg", b"<svg")):
            status, body, headers = self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertIn(expected, body)
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_foreign_host_origin_and_missing_csrf_are_rejected(self):
        self.assertEqual(self.request("GET", "/api/state", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request("GET", "/api/state", headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/demo", {})[0], 403)
        self.assertEqual(self.mutate("POST", "/api/demo", headers={"Origin": "null"})[0], 403)
        self.assertEqual(self.mutate("POST", "/api/demo", headers={"Origin": f"http://127.0.0.1:{self.port}"})[0], 200)

    def test_json_limits_and_traversal(self):
        for body in ('{"a":1,"a":2}', '{"x":NaN}', '[]', 'null', '{invalid'):
            self.assertEqual(self.request("POST", "/api/connections", body, {"X-SpiceOwl-Token": self.token})[0], 400)
        self.assertEqual(self.request("POST", "/api/demo", ' ' * 17000, {"X-SpiceOwl-Token": self.token})[0], 413)
        for path in ("/../pyproject.toml", "/%2e%2e/pyproject.toml", "/api/runs/../../secret"):
            self.assertIn(self.request("GET", path)[0], (400, 404))

    def test_connection_roundtrip_and_bad_cursor(self):
        status, connection, _ = self.mutate("POST", "/api/connections", {"label": "HTTP demo", "kind": "model", "backend": "fake_model"})
        self.assertEqual(status, 201)
        cid = connection["connection_id"]
        status, result, _ = self.mutate("POST", f"/api/connections/{cid}/probe", {"level": "models"})
        self.assertEqual(status, 200)
        self.assertEqual(result["status"]["inference_access"], "unverified")
        self.assertEqual(self.mutate("PATCH", f"/api/connections/{cid}", {"enabled": False})[0], 200)
        self.assertEqual(self.mutate("POST", f"/api/connections/{cid}/probe", {"level": "models"})[0], 400)
        self.assertEqual(self.request("GET", "/api/sessions/unknown?after=-1")[0], 400)
        self.assertEqual(self.mutate("DELETE", f"/api/connections/{cid}")[0], 200)

    def test_host_probes_require_explicit_empty_post_and_preserve_runs(self):
        from unittest.mock import patch
        self.app.seed_demo()
        before = self.app.state()["runs"]
        with patch.object(self.app.hosts, "probe", return_value={"scope": "connectivity_only"}) as probe:
            self.assertEqual(self.request("GET", "/api/state")[0], 200)
            self.assertEqual(self.request("GET", "/api/hosts/example/probe")[0], 404)
            self.assertEqual(self.request("POST", "/api/hosts/example/probe", {})[0], 403)
            self.assertEqual(self.mutate("POST", "/api/hosts/example/probe", {"command": "arbitrary"})[0], 400)
            self.assertEqual(self.mutate("POST", "/api/hosts/example", {"ssh_target": "untrusted"})[0], 404)
            probe.assert_not_called()
            self.assertEqual(self.mutate("POST", "/api/hosts/example/probe")[0], 200)
            probe.assert_called_once_with("example")
        self.assertEqual(self.app.state()["runs"], before)

    def test_native_questions_keep_origin_checks_then_reject_local_dispatch(self):
        from unittest.mock import patch
        question = {"agent_id": "fixture", "question": "Hello", "request_key": "fixture-question"}
        with patch.object(self.app, "ask", wraps=self.app.ask) as ask, \
                patch.object(self.app.questions, "claim", side_effect=AssertionError("No question claim")) as claim:
            self.assertEqual(self.request("POST", "/api/agent-questions", question)[0], 403)
            self.assertEqual(self.mutate("POST", "/api/agent-questions", question, {"Origin": "https://evil.example"})[0], 403)
            self.assertEqual(self.request("GET", "/api/agent-questions")[0], 404)
            ask.assert_not_called()
            status, result, _ = self.mutate("POST", "/api/agent-questions", question)
            self.assertEqual(status, 400)
            self.assertEqual(result["error"]["code"], "agent_channel_owned_by_circuitgit")
            ask.assert_called_once_with(question)
            claim.assert_not_called()

    def test_native_readiness_is_rejected_after_origin_and_csrf_checks(self):
        from unittest.mock import patch
        with patch.object(self.app.native_agents, "probe", side_effect=AssertionError("No native probe")) as probe:
            path = "/api/native-agents/fixture/probe"
            self.assertEqual(self.request("POST", path, {})[0], 403)
            self.assertEqual(self.mutate("POST", path, {}, {"Origin": "https://evil.example"})[0], 403)
            status, result, _ = self.mutate("POST", path)
            self.assertEqual(status, 400)
            self.assertEqual(result["error"]["code"], "agent_channel_owned_by_circuitgit")
            probe.assert_not_called()

    def test_saved_questions_remain_readable_without_mutation_or_dispatch(self):
        from unittest.mock import patch
        record = {"qa_id": "old-question", "agent_id": "removed-profile", "question": "Saved question",
                  "answer": "Saved answer", "status": "queued", "request_key": "original-key",
                  "profile_snapshot": {"executable": "/private/legacy/codex"}, "synthetic": False}
        self.app.store.create("qa", record["qa_id"], record)
        with patch("subprocess.Popen", side_effect=AssertionError("No native process")):
            for _ in range(2):
                status, result, _ = self.request("GET", "/api/agent-questions/old-question")
                self.assertEqual(status, 200)
                self.assertEqual(result["status"], "queued")
                self.assertEqual(result["answer"], "Saved answer")
                self.assertNotIn("profile_snapshot", result)
                status, state, _ = self.request("GET", "/api/state")
                self.assertEqual(status, 200)
                self.assertEqual(state["agent_channel"]["owner"], "circuitgit")
                self.assertFalse(state["capabilities"]["real_inference"])
        self.assertEqual(self.app.store.get("qa", record["qa_id"]), record)


if __name__ == "__main__":
    unittest.main()

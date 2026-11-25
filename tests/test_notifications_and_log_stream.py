"""Tests for overnight watch webhook notifications and incremental log streaming with rotation detection."""

from __future__ import annotations

import http.server
import json
import tempfile
import threading
import unittest
from pathlib import Path

from spiceowl.domain.common import DomainError
from spiceowl.monitoring.log_stream import IncrementalLogWatcher, poll_log_increment
from spiceowl.notifications import (
    NotificationManager,
    build_notification_event,
    format_provider_payload,
    redact_webhook_url,
    validate_webhook_url,
)


class TestNotificationsAndLogStream(unittest.TestCase):
    def test_webhook_url_validation_and_redaction(self) -> None:
        self.assertEqual(
            validate_webhook_url("https://open.feishu.cn/open-apis/bot/v2/hook/secret123456"),
            "https://open.feishu.cn/open-apis/bot/v2/hook/secret123456",
        )
        self.assertEqual(
            validate_webhook_url("http://127.0.0.1:9999/hook"),
            "http://127.0.0.1:9999/hook",
        )
        for bad in (
            "http://example.com/hook",
            "file:///etc/passwd",
            "https://user:pass@hooks.slack.com/services/a/b/c",
            "https://hooks.slack.com/services/a b",
        ):
            with self.assertRaises(DomainError):
                validate_webhook_url(bad)
        redacted = redact_webhook_url("https://open.feishu.cn/open-apis/bot/v2/hook/supersecrettoken99")
        self.assertNotIn("supersecrettoken99", redacted)
        self.assertIn("****", redacted)

    def test_provider_payload_formatting_and_loopback_delivery(self) -> None:
        ev = build_notification_event(
            event_type="recovery_succeeded",
            run_id="amp-tran-r2",
            host_id="sim-node-a",
            backend="slurm",
            simulator="spectre",
            attempt_number=2,
            five_dim_summary={"execution": "completed", "measurement": "valid", "performance": "pass", "soa": "clean"},
            detail="Confirmed OOM scaled 16 -> 32 GiB and succeeded.",
        )
        for provider in ("feishu", "slack", "dingtalk", "generic"):
            payload = format_provider_payload(provider, ev)
            serialized = json.dumps(payload, ensure_ascii=False)
            self.assertIn("amp-tran-r2", serialized)
            self.assertNotIn("PRIVATE KEY", serialized)

        received = []

        class HookHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", "0"))
                received.append(json.loads(self.rfile.read(n).decode("utf-8")))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, *_):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), HookHandler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            with tempfile.TemporaryDirectory(prefix="spiceowl-ntf-") as tmp:
                mgr = NotificationManager(tmp)
                ch = mgr.add_channel(
                    label="Local Test Feishu",
                    provider="feishu",
                    webhook_url=f"http://127.0.0.1:{srv.server_address[1]}/open-apis/bot/v2/hook/token12345",
                    events=["recovery_succeeded", "failed"],
                )
                self.assertIn("****", ch["webhook_url"])
                receipts = mgr.dispatch(ev)
                self.assertEqual(len(receipts), 1)
                self.assertEqual(receipts[0]["status"], "delivered")
                self.assertEqual(len(received), 1)
                self.assertEqual(received[0]["msg_type"], "interactive")
                self.assertEqual(len(mgr.list_history()), 1)
                mgr.remove_channel(ch["channel_id"])
                self.assertEqual(mgr.list_channels(), [])
        finally:
            srv.shutdown()
            srv.server_close()

    def test_incremental_log_stream_and_rotation_handling(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spiceowl-logstream-") as tmp:
            log_file = Path(tmp) / "spectre.out"
            watcher = IncrementalLogWatcher(Path(tmp) / "state")

            # Initial write
            log_file.write_text("Notice: spectre started\ntran: time = 10 ns\n", encoding="utf-8")
            p1 = watcher.poll("run-1", log_file)
            self.assertFalse(p1["rotated"])
            self.assertEqual([e["seq"] for e in p1["events"]], [1, 2])
            self.assertEqual([e["text"] for e in p1["events"]], ["Notice: spectre started", "tran: time = 10 ns"])

            # Duplicate poll with no new bytes produces 0 events
            p2 = watcher.poll("run-1", log_file)
            self.assertEqual(p2["events"], [])

            # Append incremental lines (including partial line without newline)
            with log_file.open("a", encoding="utf-8") as fh:
                fh.write("tran: time = 20 ns\npartial")
            p3 = watcher.poll("run-1", log_file)
            self.assertFalse(p3["rotated"])
            self.assertEqual(len(p3["events"]), 1)
            self.assertEqual(p3["events"][0]["seq"], 3)
            self.assertEqual(p3["events"][0]["text"], "tran: time = 20 ns")

            # Finish partial line
            with log_file.open("a", encoding="utf-8") as fh:
                fh.write("_completed_line\n")
            p4 = watcher.poll("run-1", log_file)
            self.assertEqual(len(p4["events"]), 1)
            self.assertEqual(p4["events"][0]["seq"], 4)
            self.assertEqual(p4["events"][0]["text"], "partial_completed_line")

            # Simulate log rotation / truncation: overwrite with shorter new header
            log_file.write_text("ROTATED_LOG_HEADER\ntran: time = 30 ns\n", encoding="utf-8")
            p5 = watcher.poll("run-1", log_file)
            self.assertTrue(p5["rotated"])
            self.assertEqual(p5["events"][0]["kind"], "log_rotated")
            self.assertEqual(p5["events"][0]["seq"], 5)
            self.assertEqual(p5["events"][1]["text"], "ROTATED_LOG_HEADER")
            self.assertEqual(p5["events"][1]["seq"], 6)
            self.assertEqual(p5["events"][2]["text"], "tran: time = 30 ns")
            self.assertEqual(p5["events"][2]["seq"], 7)
            self.assertEqual(p5["cursor"]["rotations_detected"], 1)

            # Restart safety: fresh watcher reads persisted cursor without duplicating lines
            watcher2 = IncrementalLogWatcher(Path(tmp) / "state")
            p6 = watcher2.poll("run-1", log_file)
            self.assertEqual(p6["events"], [])


if __name__ == "__main__":
    unittest.main()

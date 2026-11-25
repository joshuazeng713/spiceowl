"""Tests for restart-safe incremental log streaming with rotation/truncation detection."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from spiceowl.monitoring.log_stream import IncrementalLogWatcher


class TestIncrementalLogStream(unittest.TestCase):
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

"""ReadStore must observe an existing controller without becoming one."""

from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from spiceowl.domain.common import DomainError, digest
from spiceowl.service import Controller
from spiceowl.storage.readonly import MAX_RECORD_BYTES, MAX_RECORDS, ReadStore


class ReadStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        # URI punctuation must be quoted, not interpreted as connection flags.
        self.directory = Path(self.temp.name) / "state ?mode=rw#test"
        self.app = Controller(self.directory)
        self.app.seed_demo()

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def test_missing_state_has_actionable_error_and_creates_nothing(self):
        missing = Path(self.temp.name) / "missing"
        with self.assertRaises(DomainError) as error:
            ReadStore(missing)
        self.assertEqual(error.exception.code, "storage_missing")
        self.assertIn("data directory", str(error.exception))
        self.assertFalse(missing.exists())
        empty = Path(self.temp.name) / "empty"
        empty.mkdir()
        with self.assertRaises(DomainError):
            ReadStore(empty)
        self.assertEqual(list(empty.iterdir()), [])

    def test_uri_readonly_even_if_query_only_is_turned_off(self):
        store = ReadStore(self.directory)
        before = self.app.store.path.read_bytes()
        with self.assertRaises(DomainError):
            with store.transaction() as db:
                self.assertTrue(db.in_transaction)
                self.assertEqual(db.execute("PRAGMA query_only").fetchone()[0], 1)
                db.execute("PRAGMA query_only=OFF")
                db.execute("UPDATE records SET body='{}' WHERE kind='run'")
        self.assertEqual(self.app.store.path.read_bytes(), before)

    def test_reading_has_no_store_controller_or_filesystem_mutations(self):
        before = self.app.store.path.read_bytes()
        mode = stat.S_IMODE(self.app.store.path.stat().st_mode)
        entries = sorted(p.name for p in self.directory.iterdir())
        with patch("spiceowl.storage.sqlite.Store.__init__", side_effect=AssertionError("Store")), patch(
            "spiceowl.storage.sqlite.Store.recover_interrupted", side_effect=AssertionError("recovery")
        ), patch("fcntl.flock", side_effect=AssertionError("controller lock")), patch(
            "os.chmod", side_effect=AssertionError("chmod")
        ), patch("pathlib.Path.mkdir", side_effect=AssertionError("mkdir")):
            store = ReadStore(self.directory)
            self.assertEqual(store.counts()["records"]["run"], 4)
            self.assertEqual(len(store.list("connection")["records"]), 2)
            self.assertEqual(store.get("run", "demo-disconnect")["run_id"], "demo-disconnect")
        self.assertEqual(self.app.store.path.read_bytes(), before)
        self.assertEqual(stat.S_IMODE(self.app.store.path.stat().st_mode), mode)
        self.assertEqual(sorted(p.name for p in self.directory.iterdir()), entries)

    def test_pending_and_unknown_turns_are_not_recovered_or_changed(self):
        turns = []
        for index, status in enumerate(("dispatching", "running", "outcome_unknown")):
            run_id = ("demo-disconnect", "demo-oom", "demo-completed")[index]
            cid = self.app.add_connection({"label": f"Pending {index}", "kind": "agent", "backend": "fake_agent"})["connection_id"]
            turn, _ = self.app.store.claim_turn(f"pending-{index}", digest(index), cid, run_id, "Fixture only", {})
            if status != "dispatching":
                self.app.store.append_event(turn["turn_id"], "turn.test", {}, {"status": status})
            turns.append(self.app.store.get_turn(turn["turn_id"]))
        before = self.app.store.path.read_bytes()
        store = ReadStore(self.directory)
        counts = store.counts()
        for status in ("dispatching", "running", "outcome_unknown"):
            self.assertEqual(counts["turn_states"][status], 1)
        store.list("run")
        self.assertEqual(self.app.store.path.read_bytes(), before)
        self.assertEqual([self.app.store.get_turn(t["turn_id"]) for t in turns], turns)

    def test_reader_sees_committed_state_while_live_writer_has_reserved_lock(self):
        store = ReadStore(self.directory)
        old = store.get("connection", "demo-agent")
        with self.app.store.transaction() as db:
            self.app.store._put(db, "connection", "demo-agent", old | {"label": "Committed later"})
            self.assertEqual(store.get("connection", "demo-agent")["label"], old["label"])
        self.assertEqual(store.get("connection", "demo-agent")["label"], "Committed later")
        # Each request released its reader; a subsequent controller write works.
        self.app.change_connection("demo-agent", {"enabled": False})
        self.assertFalse(store.get("connection", "demo-agent")["enabled"])

    def test_lists_are_bounded_and_copies_do_not_mutate_database(self):
        for i in range(MAX_RECORDS + 3):
            self.app.store.create("host", f"host-{i}", {"host_id": f"host-{i}"})
        store = ReadStore(self.directory)
        page = store.list("host")
        self.assertTrue(page["truncated"])
        self.assertEqual(len(page["records"]), MAX_RECORDS)
        page["records"][0]["host_id"] = "changed"
        self.assertNotEqual(store.list("host")["records"][0]["host_id"], "changed")
        self.assertEqual(len(store.list("host", 2)["records"]), 2)
        for limit in [0, -1, True, 1000, "25"]:
            with self.subTest(limit=limit), self.assertRaises(DomainError):
                store.list("host", limit)

    def test_invalid_ids_kinds_and_missing_records_have_safe_errors(self):
        store = ReadStore(self.directory)
        for identifier in [None, 12, "../private", "https://example.invalid", "x' OR 1=1--"]:
            with self.subTest(identifier=identifier), self.assertRaises(DomainError):
                store.get("run", identifier)
        for kind in ["turns", "host; DELETE FROM records", [], None]:
            with self.subTest(kind=kind), self.assertRaises(DomainError):
                store.list(kind)
        with self.assertRaises(DomainError) as error:
            store.get("run", "missing")
        self.assertEqual(error.exception.code, "not_found")

    def test_invalid_and_oversized_records_do_not_escape(self):
        store = ReadStore(self.directory)
        for body in ["SECRET_INVALID_JSON", "[]", '{"number": NaN}', '"' + "x" * MAX_RECORD_BYTES + '"']:
            with self.subTest(length=len(body)):
                with self.app.store.transaction() as db:
                    db.execute("UPDATE records SET body=? WHERE id='demo-disconnect'", (body,))
                with self.assertRaises(DomainError) as error:
                    store.get("run", "demo-disconnect")
                self.assertEqual(error.exception.code, "storage_record")
                self.assertNotIn("SECRET", str(error.exception))

    def test_versions_invalid_schema_and_corrupt_file_are_not_migrated(self):
        for version in (0, 2):
            with self.app.store.transaction() as db:
                db.execute(f"PRAGMA user_version={version}")
            before = self.app.store.path.read_bytes()
            with self.assertRaises(DomainError) as error:
                ReadStore(self.directory)
            self.assertEqual(error.exception.code, "storage_version")
            self.assertEqual(before, self.app.store.path.read_bytes())
        other = Path(self.temp.name) / "other"
        other.mkdir()
        path = other / "spiceowl.sqlite3"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE wrong (value)")
            db.execute("PRAGMA user_version=1")
        with self.assertRaises(DomainError) as error:
            ReadStore(other)
        self.assertEqual(error.exception.code, "storage_invalid")
        path.write_bytes(b"not sqlite")
        with self.assertRaises(DomainError) as error:
            ReadStore(other)
        self.assertEqual(error.exception.code, "storage_invalid")

    def test_wal_is_rejected_before_reader_can_create_sidecars(self):
        other = Path(self.temp.name) / "wal"
        other.mkdir()
        path = other / "spiceowl.sqlite3"
        db = sqlite3.connect(path)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE fixture (value)")
        db.commit()
        db.close()
        before = sorted(p.name for p in other.iterdir())
        with self.assertRaises(DomainError) as error:
            ReadStore(other)
        self.assertEqual(error.exception.code, "storage_mode")
        self.assertEqual(sorted(p.name for p in other.iterdir()), before)

    def test_imports_do_not_load_controller_store_or_provider_backends(self):
        result = subprocess.run([sys.executable, "-c", (
            "import sys; import spiceowl.storage.readonly; import spiceowl.mcp.server; "
            "assert 'spiceowl.service' not in sys.modules; "
            "assert 'spiceowl.storage.sqlite' not in sys.modules; "
            "assert 'mcp' not in sys.modules; "
            "assert not any(n.startswith('spiceowl.agent') or n.startswith('spiceowl.adapters') for n in sys.modules)"
        )], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

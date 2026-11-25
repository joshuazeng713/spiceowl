"""Cross-platform compatibility tests and project-wide static POSIX guardrail.

Ensures SpiceOwl's local control plane supports macOS, Linux, and native Windows
without regressions, and prevents new local modules from reintroducing unguarded
POSIX-only calls outside ``spiceowl._platform``.
"""

from __future__ import annotations

import ast
import errno
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

from spiceowl import _platform
from spiceowl.agent.native_support import transport as native_transport
from spiceowl.codex_bridge import transport as codex_transport


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src" / "spiceowl"


def _load_setup_dev():
    spec = importlib.util.spec_from_file_location("setup_dev_xplat", ROOT / "scripts" / "setup_dev.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PlatformPrimitivesTests(unittest.TestCase):
    def test_venv_executable_and_isolation_kwargs(self):
        self.assertEqual(_platform.venv_executable("/tmp/v", "python", windows=False), Path("/tmp/v/bin/python"))
        self.assertEqual(_platform.venv_executable("/tmp/v", "spiceowl", windows=True), Path("/tmp/v/Scripts/spiceowl.exe"))
        self.assertEqual(_platform.venv_executable("/tmp/v", "spiceowl.exe", windows=True), Path("/tmp/v/Scripts/spiceowl.exe"))
        self.assertEqual(_platform.popen_isolation_kwargs(windows=False), {"start_new_session": True})
        win_iso = _platform.popen_isolation_kwargs(windows=True)
        self.assertIn("creationflags", win_iso)
        self.assertNotIn("start_new_session", win_iso)

    def test_default_state_dir_respects_xdg_and_windows_localappdata(self):
        with patch.dict(os.environ, {"XDG_STATE_HOME": "/custom/state", "LOCALAPPDATA": r"C:\Users\u\AppData\Local"}, clear=False):
            self.assertEqual(_platform.default_state_dir(), Path("/custom/state/spiceowl"))
        env_no_xdg = {k: v for k, v in os.environ.items() if k != "XDG_STATE_HOME"}
        env_no_xdg["LOCALAPPDATA"] = "/fake/localappdata"
        with patch.dict(os.environ, env_no_xdg, clear=True), patch.object(_platform, "IS_WINDOWS", True):
            self.assertEqual(_platform.default_state_dir(), Path("/fake/localappdata/spiceowl"))

    def test_ssh_executable_and_windows_option_filtering(self):
        with patch.dict(os.environ, {"SPICEOWL_SSH_EXECUTABLE": "/custom/bin/ssh"}, clear=False):
            self.assertEqual(_platform.resolve_ssh_executable(windows=True), "/custom/bin/ssh")
        env_clean = {k: v for k, v in os.environ.items() if k != "SPICEOWL_SSH_EXECUTABLE"}
        with patch.dict(os.environ, env_clean, clear=True), \
                patch.object(_platform.shutil, "which", return_value=r"C:\Windows\System32\OpenSSH\ssh.exe"):
            self.assertEqual(_platform.resolve_ssh_executable(windows=True), r"C:\Windows\System32\OpenSSH\ssh.exe")
        base = ("BatchMode=yes", "ControlMaster=no", "ControlPath=none", "ControlPersist=no", "SessionType=default")
        # POSIX preserves all options verbatim
        self.assertEqual(_platform.ssh_client_options(base, windows=False), base)
        # Windows drops SessionType=default purely (without spawning subprocesses)
        with patch("subprocess.run", side_effect=AssertionError("ssh_client_options must be pure")):
            self.assertEqual(
                _platform.ssh_client_options(base, executable="ssh.exe", windows=True),
                ("BatchMode=yes", "ControlMaster=no", "ControlPath=none", "ControlPersist=no"),
            )

    def test_file_lock_contention_on_current_os(self):
        with tempfile.TemporaryDirectory() as temp:
            lock_path = Path(temp) / "demo.lock"
            with _platform.file_lock(lock_path, nonblocking=True):
                with self.assertRaises(BlockingIOError):
                    with _platform.file_lock(lock_path, nonblocking=True):
                        pass
            # After release, locking succeeds again
            with _platform.file_lock(lock_path, nonblocking=True):
                pass

    def test_windows_msvcrt_lock_emulation(self):
        state = {"locked": False, "calls": []}

        class FakeMsvcrt:
            LK_NBLCK = 2
            LK_UNLCK = 0

            @staticmethod
            def locking(fd, mode, nbytes):
                state["calls"].append((mode, nbytes))
                if mode == FakeMsvcrt.LK_NBLCK:
                    if state["locked"]:
                        raise OSError(errno.EACCES, "locked")
                    state["locked"] = True
                elif mode == FakeMsvcrt.LK_UNLCK:
                    state["locked"] = False

        with tempfile.TemporaryDirectory() as temp:
            lock_path = Path(temp) / "win.lock"
            with patch.object(_platform, "IS_WINDOWS", True), \
                    patch.object(_platform, "msvcrt", FakeMsvcrt):
                with _platform.file_lock(lock_path, nonblocking=True):
                    self.assertTrue(state["locked"])
                    with self.assertRaises(BlockingIOError):
                        with _platform.file_lock(lock_path, nonblocking=True):
                            pass
                self.assertFalse(state["locked"])

    def test_windows_privacy_checks_and_symlink_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "secret.json"
            target.write_text("{}", encoding="utf-8")
            link = Path(temp) / "link.json"
            link.symlink_to(target)
            # Simulate Windows synthetic mode 0o100666 where group/other bits mirror owner bits
            fake_stat = types.SimpleNamespace(st_mode=stat.S_IFREG | 0o666, st_uid=0)
            with patch.object(_platform, "IS_WINDOWS", True):
                self.assertTrue(_platform.is_owner_private_file(fake_stat))
                self.assertTrue(_platform.is_owner_private_dir(types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o777, st_uid=0)))
                # Symlink must still be refused on Windows
                with self.assertRaises(OSError):
                    _platform.open_readonly_nofollow(link)
                with self.assertRaises(OSError):
                    _platform.open_lock_nofollow(link)
                # Regular file opens cleanly and fsync_dir is a safe no-op
                fd = _platform.open_readonly_nofollow(target)
                os.close(fd)
                _platform.fsync_dir(Path(temp))
                _platform.chmod_private(target, 0o600)

    def test_communicate_bounded_both_selector_and_threaded_modes(self):
        # Windows select()/selectors only accepts sockets, not pipes, so Windows
        # tests the threaded path while POSIX tests both selector and threaded paths.
        modes = (True,) if _platform.IS_WINDOWS else (False, True)
        for use_threads in modes:
            with self.subTest(use_threads=use_threads):
                # 1. Normal stdin -> stdout + stderr echo
                script = (
                    "import sys; data = sys.stdin.buffer.read(); "
                    "sys.stdout.buffer.write(b'OUT:' + data); sys.stdout.buffer.flush(); "
                    "sys.stderr.buffer.write(b'ERR:ok'); sys.stderr.buffer.flush()"
                )
                proc = subprocess.Popen(
                    [sys.executable, "-c", script],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    **_platform.popen_isolation_kwargs(),
                )
                out, err = bytearray(), bytearray()
                try:
                    rc = _platform.communicate_bounded(
                        proc, deadline=time.monotonic() + 5.0, max_output_bytes=4096,
                        stdin_bytes=b"hello-spiceowl", on_stdout=out.extend, on_stderr=err.extend,
                        use_threads=use_threads,
                    )
                finally:
                    if proc.poll() is None:
                        _platform.terminate_process_tree(proc)
                    proc.stdout.close()
                    proc.stderr.close()
                self.assertEqual(rc, 0)
                self.assertEqual(bytes(out), b"OUT:hello-spiceowl")
                self.assertEqual(bytes(err), b"ERR:ok")

                # 2. Output limit enforcement
                flood = "import sys; sys.stdout.buffer.write(b'x' * 8192); sys.stdout.buffer.flush()"
                proc2 = subprocess.Popen(
                    [sys.executable, "-c", flood],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    **_platform.popen_isolation_kwargs(),
                )
                try:
                    with self.assertRaises(_platform.BoundedIOError) as ctx:
                        _platform.communicate_bounded(
                            proc2, deadline=time.monotonic() + 5.0, max_output_bytes=512,
                            use_threads=use_threads,
                        )
                    self.assertEqual(ctx.exception.kind, "output_limit")
                finally:
                    if proc2.poll() is None:
                        _platform.terminate_process_tree(proc2)
                    proc2.stdout.close()
                    proc2.stderr.close()

                # 3. Timeout enforcement
                sleeper = "import time; time.sleep(10)"
                proc3 = subprocess.Popen(
                    [sys.executable, "-c", sleeper],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    **_platform.popen_isolation_kwargs(),
                )
                try:
                    with self.assertRaises(_platform.BoundedIOError) as ctx:
                        _platform.communicate_bounded(
                            proc3, deadline=time.monotonic() + 0.15, max_output_bytes=1024,
                            use_threads=use_threads,
                        )
                    self.assertEqual(ctx.exception.kind, "timeout")
                finally:
                    if proc3.poll() is None:
                        _platform.terminate_process_tree(proc3)
                    proc3.stdout.close()
                    proc3.stderr.close()

    def test_windows_mode_native_and_codex_transports_work_over_stdio(self):
        # Exercise NativeProcess threaded path (Windows mode) against a local echo peer
        peer = (
            "import json, sys\n"
            "line = sys.stdin.readline()\n"
            "req = json.loads(line)\n"
            "sys.stdout.write(json.dumps({'id': req['id'], 'result': {'ok': True}}) + '\\n')\n"
            "sys.stdout.flush()\n"
        )
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(native_transport, "IS_WINDOWS", True):
                with native_transport.NativeProcess([sys.executable, "-c", peer], temp, 5.0) as proc:
                    reply = proc.request("ping", {"hello": 1}, lambda _msg: None)
                    self.assertEqual(reply, {"ok": True})

            received = []
            with patch.object(codex_transport, "IS_WINDOWS", True):
                rpc = codex_transport.StdioTransport([sys.executable, "-c", peer], temp, received.append, timeout=5.0)
                rpc.start()
                try:
                    reply = rpc.call("ping", {"hello": 2})
                    self.assertEqual(reply, {"ok": True})
                finally:
                    rpc.close()

    def test_setup_dev_accepts_windows_and_resolves_scripts_dir(self):
        setup = _load_setup_dev()
        with patch.object(setup, "is_windows", return_value=True):
            self.assertEqual(setup.venv_exe(Path("/fake/venv"), "python"), Path("/fake/venv/Scripts/python.exe"))
            self.assertEqual(setup.venv_exe(Path("/fake/venv"), "spiceowl"), Path("/fake/venv/Scripts/spiceowl.exe"))
        with patch.object(setup.sys, "platform", "win32"), \
                patch.object(setup, "inspect_target", return_value=Path("/fake/venv/Scripts/python.exe")), \
                patch.object(setup, "run"):
            self.assertEqual(setup.main(["--check-only"]), 0)


class CrossPlatformStaticGuardTests(unittest.TestCase):
    """Static AST guard: local package modules must route POSIX-only APIs through _platform.py."""

    FORBIDDEN_OS_ATTRS = frozenset({
        "killpg", "getuid", "geteuid", "setsid",
        "O_NOFOLLOW", "O_NONBLOCK", "O_DIRECTORY",
    })

    def test_no_raw_posix_only_calls_outside_platform_module(self):
        violations: list[str] = []
        for py_file in sorted(SRC_ROOT.rglob("*.py")):
            rel = py_file.relative_to(ROOT).as_posix()
            if rel == "src/spiceowl/_platform.py":
                continue
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in ("fcntl", "pwd", "grp", "termios", "pty", "resource"):
                            violations.append(f"{rel}:{node.lineno}: forbidden direct import '{alias.name}'")
                elif isinstance(node, ast.ImportFrom):
                    if node.module in ("fcntl", "pwd", "grp", "termios", "pty", "resource"):
                        violations.append(f"{rel}:{node.lineno}: forbidden direct import from '{node.module}'")
                elif isinstance(node, ast.Attribute):
                    if isinstance(node.value, ast.Name) and node.value.id == "os" and node.attr in self.FORBIDDEN_OS_ATTRS:
                        violations.append(f"{rel}:{node.lineno}: forbidden bare 'os.{node.attr}' (use spiceowl._platform)")
                elif isinstance(node, ast.keyword) and node.arg == "start_new_session":
                    violations.append(f"{rel}:{node.lineno}: forbidden 'start_new_session=' (use popen_isolation_kwargs())")
                elif isinstance(node, ast.Constant) and node.value == "/usr/bin/ssh":
                    violations.append(f"{rel}:{node.lineno}: hardcoded '/usr/bin/ssh' (use resolve_ssh_executable())")
        self.assertEqual(
            violations, [],
            "Local modules must remain Windows/macOS/Linux compatible via spiceowl._platform:\n"
            + "\n".join(violations),
        )


if __name__ == "__main__":
    unittest.main()

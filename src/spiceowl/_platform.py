"""Centralized OS compatibility layer for macOS, Linux, and native Windows.

SpiceOwl's remote simulation workers always execute on POSIX/Linux EDA servers,
but the local controller, CLI, web console, SSH client wrapper, Codex bridge,
and developer tooling must run natively on macOS, Linux, and Windows.

All local OS-specific operations (file locking, process-group isolation and
termination, bounded subprocess pipe I/O, SSH executable resolution, symlink-
safe file opens, owner-private metadata checks, and directory fsync) belong
here so future features stay cross-platform by default.
"""

from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import queue
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time

IS_WINDOWS: bool = os.name == "nt"
IS_MACOS: bool = sys.platform == "darwin"
IS_LINUX: bool = sys.platform.startswith("linux")

if IS_WINDOWS:  # pragma: no cover - exercised via unit tests on Windows & mocks
    import msvcrt  # type: ignore[import-not-found]
    fcntl = None
else:
    import fcntl
    msvcrt = None  # type: ignore[assignment]


_SSH_VERSION_CACHE: dict[str, tuple[int, int] | None] = {}
_SSH_VERSION_RE = re.compile(r"OpenSSH(?:_for_Windows)?[_-](\d+)\.(\d+)")


def platform_name() -> str:
    if IS_WINDOWS:
        return "windows"
    if IS_MACOS:
        return "macos"
    if IS_LINUX:
        return "linux"
    return sys.platform


def default_state_dir(app_name: str = "spiceowl") -> Path:
    """Return the idiomatic per-user local state directory for the current OS."""
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / app_name
    if IS_WINDOWS:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / app_name
    return Path.home() / ".local" / "state" / app_name


def venv_executable(venv_dir: str | Path, name: str, *, windows: bool | None = None) -> Path:
    """Return the path to a virtual-environment executable on POSIX or Windows."""
    use_windows = IS_WINDOWS if windows is None else windows
    root = Path(venv_dir)
    if use_windows:
        filename = name if name.lower().endswith(".exe") else f"{name}.exe"
        return root / "Scripts" / filename
    return root / "bin" / name


# ---------------------------------------------------------------------------
# Cross-platform advisory file locking
# ---------------------------------------------------------------------------

def _fd(stream) -> int:
    return stream if isinstance(stream, int) else stream.fileno()


def lock_file(stream, *, nonblocking: bool = False) -> None:
    """Acquire an exclusive advisory lock on an open file stream or fd.

    Raises BlockingIOError when ``nonblocking=True`` and another holder owns
    the lock on both POSIX and Windows.
    """
    if not IS_WINDOWS:
        flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0)
        fcntl.flock(stream, flags)
        return
    # Windows: msvcrt.locking locks relative to the current byte offset.
    fd = _fd(stream)
    if hasattr(stream, "flush"):
        try:
            stream.flush()
        except OSError:
            pass
    while True:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                if nonblocking:
                    raise BlockingIOError("File is locked by another process.") from exc
                time.sleep(0.01)
                continue
            raise


def unlock_file(stream) -> None:
    """Release an exclusive advisory lock acquired by ``lock_file``."""
    if not IS_WINDOWS:
        try:
            fcntl.flock(stream, fcntl.LOCK_UN)
        except (OSError, ValueError):
            pass
        return
    try:
        fd = _fd(stream)
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except (OSError, ValueError):
        pass


@contextmanager
def file_lock(path: str | Path, *, nonblocking: bool = False, mode: str = "a+",
              create_dir: bool = True, dir_mode: int = 0o700):
    """Context manager acquiring a cross-platform exclusive lock on ``path``."""
    target = Path(path)
    if create_dir:
        target.parent.mkdir(parents=True, exist_ok=True, mode=dir_mode)
    with target.open(mode) as stream:
        lock_file(stream, nonblocking=nonblocking)
        try:
            yield stream
        finally:
            unlock_file(stream)


# ---------------------------------------------------------------------------
# Safe file opening, owner-private checks, and directory durability
# ---------------------------------------------------------------------------

def open_readonly_nofollow(path: str | Path) -> int:
    """Open an existing file read-only without following symlinks."""
    target = Path(path)
    if IS_WINDOWS:
        if target.is_symlink():
            raise OSError(errno.ELOOP, "Refusing to follow symbolic link", str(target))
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        return os.open(target, flags)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    return os.open(target, flags)


def open_lock_nofollow(path: str | Path, mode: int = 0o600) -> int:
    """Open or create a private lock file without following symlinks."""
    target = Path(path)
    if IS_WINDOWS:
        if target.is_symlink():
            raise OSError(errno.ELOOP, "Refusing to follow symbolic link", str(target))
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
        return os.open(target, flags, mode)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    return os.open(target, flags, mode)


def is_owner_private_file(info: os.stat_result, *, allow_root: bool = False) -> bool:
    """Check that ``info`` is a regular file with owner-private permissions.

    On POSIX, enforces ``st_uid`` and rejects group/other bits (``0o077``).
    On Windows, POSIX UID/mode bits are synthetic (``st_mode`` mirrors owner
    bits into group/other), so regular-file status is checked instead.
    """
    if not stat.S_ISREG(info.st_mode):
        return False
    if IS_WINDOWS:
        return True
    allowed_uids = (0, os.getuid()) if allow_root else (os.getuid(),)
    return info.st_uid in allowed_uids and not (info.st_mode & 0o077)


def is_owner_private_dir(info: os.stat_result) -> bool:
    """Check that ``info`` is an operator-owned directory without shared write."""
    if not stat.S_ISDIR(info.st_mode):
        return False
    if IS_WINDOWS:
        return True
    return info.st_uid == os.getuid() and not (info.st_mode & 0o022)


def chmod_private(path: str | Path, mode: int = 0o600) -> None:
    """Set restrictive POSIX permissions; no-op on Windows."""
    if not IS_WINDOWS:
        os.chmod(path, mode)


def fsync_dir(path: str | Path) -> None:
    """Flush directory metadata on POSIX; no-op on Windows."""
    if IS_WINDOWS:
        return
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


# ---------------------------------------------------------------------------
# Subprocess isolation, process-tree termination, and bounded pipe I/O
# ---------------------------------------------------------------------------

def popen_isolation_kwargs(*, windows: bool | None = None) -> dict:
    """Return Popen keyword arguments that isolate a child into its own group."""
    use_windows = IS_WINDOWS if windows is None else windows
    if use_windows:
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)}
    return {"start_new_session": True}


def terminate_process_tree(process: subprocess.Popen, *, force: bool = True,
                           grace_timeout: float = 1.0) -> None:
    """Stop an owned child process and its descendants across POSIX and Windows."""
    if process is None:
        return
    if IS_WINDOWS:
        if not force:
            try:
                process.terminate()
            except OSError:
                pass
            try:
                process.wait(timeout=grace_timeout)
                return
            except subprocess.TimeoutExpired:
                pass
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=5, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            try:
                process.kill()
            except OSError:
                pass
        try:
            process.wait(timeout=grace_timeout)
        except subprocess.TimeoutExpired:
            pass
        return

    # POSIX: process was launched with start_new_session=True so pid == pgid.
    if not force:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            # A denied signal is not retried elsewhere.
            return
        except OSError:
            try:
                process.terminate()
            except OSError:
                pass
        try:
            process.wait(timeout=grace_timeout)
            return
        except subprocess.TimeoutExpired:
            pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        return
    except OSError:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=grace_timeout)
    except subprocess.TimeoutExpired:
        pass


class BoundedIOError(Exception):
    """Raised by ``communicate_bounded`` on deadline or output-budget breach."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def communicate_bounded(
    process: subprocess.Popen,
    *,
    deadline: float,
    max_output_bytes: int,
    stdin_bytes: bytes | None = None,
    on_stdout=None,
    on_stderr=None,
    chunk_size: int = 8192,
    use_threads: bool | None = None,
) -> int:
    """Exchange bounded stdin/stdout/stderr with a child process before deadline.

    Uses ``selectors.DefaultSelector`` on POSIX and bounded worker threads on
    Windows (where ``select``/``selectors`` only accepts sockets, not pipes).
    """
    threaded = IS_WINDOWS if use_threads is None else use_threads
    if not threaded:
        return _communicate_selector(
            process, deadline=deadline, max_output_bytes=max_output_bytes,
            stdin_bytes=stdin_bytes, on_stdout=on_stdout, on_stderr=on_stderr,
            chunk_size=chunk_size,
        )
    return _communicate_threaded(
        process, deadline=deadline, max_output_bytes=max_output_bytes,
        stdin_bytes=stdin_bytes, on_stdout=on_stdout, on_stderr=on_stderr,
        chunk_size=chunk_size,
    )


def _communicate_selector(
    process: subprocess.Popen,
    *,
    deadline: float,
    max_output_bytes: int,
    stdin_bytes: bytes | None,
    on_stdout,
    on_stderr,
    chunk_size: int,
) -> int:
    total = 0
    pending = memoryview(stdin_bytes) if stdin_bytes is not None else None
    with selectors.DefaultSelector() as selector:
        if process.stdout is not None:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        if process.stderr is not None:
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        if pending is not None and process.stdin is not None:
            if len(pending) == 0:
                process.stdin.close()
            else:
                selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BoundedIOError("timeout", "Subprocess I/O reached its time limit.")
            for key, _ in selector.select(remaining):
                if key.data == "stdin":
                    try:
                        count = os.write(key.fileobj.fileno(), pending[:4096])
                    except BrokenPipeError:
                        count = len(pending)
                    pending = pending[count:]
                    if not pending:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    continue
                read_limit = min(chunk_size, max_output_bytes - total + 1)
                chunk = os.read(key.fileobj.fileno(), read_limit)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                total += len(chunk)
                if total > max_output_bytes:
                    raise BoundedIOError("output_limit", "Subprocess exceeded its output limit.")
                if key.data == "stdout":
                    if on_stdout is not None:
                        on_stdout(chunk)
                else:
                    if on_stderr is not None:
                        on_stderr(chunk)
    remaining = max(0.001, deadline - time.monotonic())
    try:
        return process.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        raise BoundedIOError("timeout", "Subprocess exit reached its time limit.") from None


def _communicate_threaded(
    process: subprocess.Popen,
    *,
    deadline: float,
    max_output_bytes: int,
    stdin_bytes: bytes | None,
    on_stdout,
    on_stderr,
    chunk_size: int,
) -> int:
    events: queue.Queue = queue.Queue()
    stop = threading.Event()
    active_readers = 0
    threads: list[threading.Thread] = []

    def reader(stream, kind: str) -> None:
        try:
            while not stop.is_set():
                try:
                    chunk = os.read(stream.fileno(), chunk_size)
                except OSError:
                    break
                if not chunk:
                    break
                events.put((kind, chunk))
        finally:
            events.put(("eof", kind))

    def writer(stream, payload: bytes) -> None:
        try:
            view = memoryview(payload)
            offset = 0
            while offset < len(view) and not stop.is_set():
                try:
                    written = os.write(stream.fileno(), view[offset:offset + 4096])
                except (BrokenPipeError, OSError):
                    break
                if written <= 0:
                    break
                offset += written
        finally:
            try:
                stream.close()
            except OSError:
                pass

    if process.stdout is not None:
        active_readers += 1
        t = threading.Thread(target=reader, args=(process.stdout, "stdout"), daemon=True)
        threads.append(t)
        t.start()
    if process.stderr is not None:
        active_readers += 1
        t = threading.Thread(target=reader, args=(process.stderr, "stderr"), daemon=True)
        threads.append(t)
        t.start()
    if stdin_bytes is not None and process.stdin is not None:
        if len(stdin_bytes) == 0:
            try:
                process.stdin.close()
            except OSError:
                pass
        else:
            t = threading.Thread(target=writer, args=(process.stdin, stdin_bytes), daemon=True)
            threads.append(t)
            t.start()

    total = 0
    try:
        while active_readers > 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BoundedIOError("timeout", "Subprocess I/O reached its time limit.")
            try:
                kind, data = events.get(timeout=min(remaining, 0.25))
            except queue.Empty:
                continue
            if kind == "eof":
                active_readers -= 1
                continue
            total += len(data)
            if total > max_output_bytes:
                raise BoundedIOError("output_limit", "Subprocess exceeded its output limit.")
            if kind == "stdout":
                if on_stdout is not None:
                    on_stdout(data)
            else:
                if on_stderr is not None:
                    on_stderr(data)
        remaining = max(0.001, deadline - time.monotonic())
        try:
            return process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise BoundedIOError("timeout", "Subprocess exit reached its time limit.") from None
    finally:
        stop.set()


# ---------------------------------------------------------------------------
# SSH client discovery and option compatibility
# ---------------------------------------------------------------------------

def resolve_ssh_executable(*, windows: bool | None = None) -> str:
    """Resolve the local OpenSSH client executable across macOS, Linux, and Windows."""
    override = os.environ.get("SPICEOWL_SSH_EXECUTABLE")
    if override:
        return override
    use_windows = IS_WINDOWS if windows is None else windows
    if not use_windows:
        if os.path.exists("/usr/bin/ssh"):
            return "/usr/bin/ssh"
        return shutil.which("ssh") or "/usr/bin/ssh"
    found = shutil.which("ssh")
    if found:
        return found
    system_root = os.environ.get("SystemRoot") or r"C:\Windows"
    default_win_ssh = Path(system_root) / "System32" / "OpenSSH" / "ssh.exe"
    if default_win_ssh.exists():
        return str(default_win_ssh)
    return "ssh.exe"


def _openssh_version(executable: str) -> tuple[int, int] | None:
    if executable in _SSH_VERSION_CACHE:
        return _SSH_VERSION_CACHE[executable]
    version: tuple[int, int] | None = None
    try:
        result = subprocess.run(
            [executable, "-V"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=3, check=False, text=True, encoding="utf-8", errors="replace",
        )
        banner = (result.stderr or "") + "\n" + (result.stdout or "")
        match = _SSH_VERSION_RE.search(banner)
        if match:
            version = (int(match.group(1)), int(match.group(2)))
    except (OSError, subprocess.SubprocessError, ValueError):
        version = None
    _SSH_VERSION_CACHE[executable] = version
    return version


def ssh_client_options(base_options: tuple[str, ...], *, executable: str | None = None,
                       windows: bool | None = None) -> tuple[str, ...]:
    """Filter OpenSSH client options for version compatibility on Windows."""
    use_windows = IS_WINDOWS if windows is None else windows
    if not use_windows:
        return base_options
    exe = executable or resolve_ssh_executable(windows=True)
    version = _openssh_version(exe)
    # Windows 10 ships OpenSSH_for_Windows_8.1 which predates SessionType (8.7)
    # and does not implement Unix-domain ControlMaster multiplexing.
    drop = {"ControlMaster=no", "ControlPath=none", "ControlPersist=no"}
    if version is not None and version < (8, 7):
        drop.add("SessionType=default")
    return tuple(opt for opt in base_options if opt not in drop)

"""One owned process, bounded binary pipes, no retries or raw output logging."""

from collections import deque
import json
import os
import selectors
import signal
import subprocess
import time


MAX_FRAME = 1_048_576
MAX_STDOUT = 4_194_304
MAX_STDERR = 65_536
MAX_MESSAGES = 2048


class NativeFailure(Exception):
    def __init__(self, code, *, definitive=False):
        self.code = code
        self.definitive = definitive
        super().__init__(code)


def _json(line):
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def no_constant(_):
        raise ValueError("nonfinite JSON")

    try:
        value = json.loads(line, object_pairs_hook=object_pairs, parse_constant=no_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise NativeFailure("native_protocol") from None
    if not isinstance(value, dict):
        raise NativeFailure("native_protocol")
    return value


class NativeProcess:
    def __init__(self, argv, cwd, timeout):
        self.argv, self.cwd = argv, cwd
        self.deadline = time.monotonic() + timeout
        self.process = None
        self.selector = selectors.DefaultSelector()
        self.buffer = bytearray()
        self.queue = deque()
        self.stdout_bytes = self.stderr_bytes = self.messages = 0
        self.stdout_closed = False
        self.request_id = 0

    def __enter__(self):
        try:
            # Inherit native auth/provider environment without inspecting it.
            # No shell, daemon proxy, socket attach, config edit or CLI fallback.
            self.process = subprocess.Popen(
                self.argv, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, bufsize=0, start_new_session=True,
            )
            for stream, name in ((self.process.stdout, "stdout"), (self.process.stderr, "stderr")):
                os.set_blocking(stream.fileno(), False)
                self.selector.register(stream, selectors.EVENT_READ, name)
            os.set_blocking(self.process.stdin.fileno(), False)
        except OSError:
            self.close()
            raise NativeFailure("native_start") from None
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        process = self.process
        try:
            if process is not None:
                # EOF lets stdio peers exit normally, including the short-lived
                # version process whose output may close before wait() reaps it.
                if process.stdin is not None:
                    process.stdin.close()
                try:
                    process.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            # Cleanup must not overwrite an observed terminal result or an
            # outcome_unknown error. A denied signal is not retried elsewhere.
            pass
        finally:
            if process is not None:
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        try:
                            stream.close()
                        except OSError:
                            pass
            self.selector.close()
            self.queue.clear()
            self.buffer.clear()

    def _remaining(self):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise NativeFailure("native_timeout")
        return left

    def _read(self):
        ready = self.selector.select(self._remaining())
        if not ready:
            raise NativeFailure("native_timeout")
        for key, _ in ready:
            try:
                chunk = os.read(key.fd, 65536)
            except BlockingIOError:
                continue
            except OSError:
                raise NativeFailure("native_disconnected") from None
            if not chunk:
                self.selector.unregister(key.fileobj)
                if key.data == "stdout":
                    self.stdout_closed = True
                continue
            if key.data == "stderr":
                self.stderr_bytes += len(chunk)
                # Only count/discard stderr. Never keep auth/config error text.
                if self.stderr_bytes > MAX_STDERR:
                    raise NativeFailure("native_output_limit")
            else:
                self.stdout_bytes += len(chunk)
                if self.stdout_bytes > MAX_STDOUT:
                    raise NativeFailure("native_output_limit")
                self.buffer.extend(chunk)

    def next_message(self):
        while True:
            if self.queue:
                return self.queue.popleft()
            while b"\n" in self.buffer:
                line, _, rest = self.buffer.partition(b"\n")
                self.buffer = bytearray(rest)
                if len(line) > MAX_FRAME:
                    raise NativeFailure("native_output_limit")
                if not line.strip():
                    continue
                self.messages += 1
                if self.messages > MAX_MESSAGES:
                    raise NativeFailure("native_output_limit")
                self.queue.append(_json(line))
            if len(self.buffer) > MAX_FRAME:
                raise NativeFailure("native_output_limit")
            if self.queue:
                continue
            if self.stdout_closed:
                raise NativeFailure("native_disconnected")
            self._read()

    def send(self, message):
        try:
            data = (json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode()
        except (ValueError, UnicodeError, RecursionError):
            raise NativeFailure("native_protocol") from None
        if len(data) > MAX_FRAME:
            raise NativeFailure("native_output_limit")
        with selectors.DefaultSelector() as writable:
            writable.register(self.process.stdin, selectors.EVENT_WRITE)
            offset = 0
            while offset < len(data):
                if not writable.select(self._remaining()):
                    raise NativeFailure("native_timeout")
                try:
                    offset += os.write(self.process.stdin.fileno(), data[offset:])
                except BlockingIOError:
                    continue
                except (BrokenPipeError, OSError):
                    raise NativeFailure("native_disconnected") from None

    def request(self, method, params, handler):
        self.request_id += 1
        rid = self.request_id
        self.send({"id": rid, "method": method, "params": params})
        while True:
            message = self.next_message()
            if "method" not in message and type(message.get("id")) is int and message["id"] == rid:
                if "error" in message:
                    raise NativeFailure("native_rejected", definitive=True)
                if not isinstance(message.get("result"), dict):
                    raise NativeFailure("native_protocol")
                return message["result"]
            handler(message)

    def version_output(self):
        while not self.stdout_closed:
            self._read()
            if len(self.buffer) > 1024:
                raise NativeFailure("native_output_limit")
        try:
            return self.buffer.decode("utf-8").strip()
        except UnicodeError:
            raise NativeFailure("native_protocol") from None

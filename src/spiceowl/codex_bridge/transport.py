"""Bounded stdio JSON-RPC. Construction never launches a process."""

import json
import os
import queue
import select
import subprocess
import threading
import time

from .._platform import IS_WINDOWS
from ..domain.common import DomainError

MAX_LINE = 2 * 1024 * 1024


class BridgeError(DomainError):
    def __init__(self, message, code='codex_bridge_invalid'):
        super().__init__(message, code)
        self.message = message


def encode(value):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
        if len(raw.encode('utf-8')) > MAX_LINE:
            raise ValueError('size')
        return raw
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise BridgeError('Protocol data exceeds the supported JSON bounds.', 'codex_protocol') from None


class StdioTransport:
    """One owned subprocess. EOF/timeouts never reconnect or resend."""

    def __init__(self, argv, cwd, callback, *, timeout):
        self.argv, self.cwd, self.callback, self.timeout = argv, cwd, callback, timeout
        self.process = None
        self.lock = threading.RLock()
        self.closed = True
        self.pending = {}
        self.sequence = 0
        self.reader = None

    def start(self):
        with self.lock:
            if self.process is not None:
                raise BridgeError('This transport was already started.', 'codex_transport_used')
            try:
                self.process = subprocess.Popen(self.argv, cwd=self.cwd, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                if not IS_WINDOWS:
                    os.set_blocking(self.process.stdin.fileno(), False)
            except (OSError, ValueError):
                raise BridgeError('Could not start the configured Codex executable.', 'codex_start_failed') from None
            self.closed = False
            self.reader = threading.Thread(target=self._read, name='spiceowl-codex-rpc', daemon=True)
            self.reader.start()

    def _send_windows(self, raw):
        done = threading.Event()
        error = []

        def write_all():
            try:
                pending = memoryview(raw)
                while pending:
                    written = os.write(self.process.stdin.fileno(), pending)
                    if written <= 0:
                        raise OSError('short write')
                    pending = pending[written:]
            except (OSError, ValueError) as exc:
                error.append(exc)
            finally:
                done.set()

        worker = threading.Thread(target=write_all, name='spiceowl-codex-write', daemon=True)
        worker.start()
        if not done.wait(self.timeout):
            try:
                self.process.stdin.close()
            except OSError:
                pass
            raise BridgeError('Codex input write timed out; preserve the original request.', 'codex_outcome_unknown')
        if error:
            raise error[0]

    def send(self, message):
        raw = (encode(message) + '\n').encode('utf-8')
        with self.lock:
            if self.closed or self.process is None:
                raise BridgeError('Codex transport is disconnected; no request was replayed.', 'codex_disconnected')
            try:
                if IS_WINDOWS:
                    self._send_windows(raw)
                else:
                    pending = memoryview(raw)
                    deadline = time.monotonic() + self.timeout
                    while pending:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not select.select([], [self.process.stdin], [], remaining)[1]:
                            raise BridgeError('Codex input write timed out; preserve the original request.', 'codex_outcome_unknown')
                        try:
                            written = os.write(self.process.stdin.fileno(), pending)
                        except BlockingIOError:
                            continue
                        if written <= 0:
                            raise OSError('short write')
                        pending = pending[written:]
            except BridgeError:
                raise
            except (OSError, ValueError):
                raise BridgeError('Codex transport write is uncertain; preserve the original request.', 'codex_outcome_unknown') from None

    def call(self, method, params):
        with self.lock:
            self.sequence += 1
            rid = 'spiceowl-' + str(self.sequence)
            reply_queue = queue.Queue(maxsize=1)
            self.pending[rid] = reply_queue
        try:
            self.send({'id': rid, 'method': method, 'params': params})
            try:
                reply = reply_queue.get(timeout=self.timeout)
            except queue.Empty:
                raise BridgeError('Codex request timed out; its outcome is unknown.', 'codex_outcome_unknown') from None
            if 'error' in reply:
                if type(reply['error']) is not dict:
                    raise BridgeError('Codex returned an unsupported error response.', 'codex_protocol')
                if reply['error'].get('code') == 'transport_closed':
                    raise BridgeError('Codex disconnected before replying; its outcome is unknown.', 'codex_outcome_unknown')
                raise BridgeError('Codex rejected the protocol request.', 'codex_rpc_rejected')
            result = reply.get('result')
            if not isinstance(result, dict):
                raise BridgeError('Codex returned an unsupported response.', 'codex_protocol')
            return result
        finally:
            with self.lock:
                self.pending.pop(rid, None)

    def _read(self):
        reason = 'stream_closed'
        try:
            while True:
                line = self.process.stdout.readline(MAX_LINE + 1)
                if not line:
                    break
                if len(line) > MAX_LINE or not line.endswith(b'\n'):
                    reason = 'protocol_limit'
                    break
                try:
                    msg = json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                except (ValueError, UnicodeError, RecursionError):
                    reason = 'invalid_json'
                    break
                if not isinstance(msg, dict):
                    reason = 'invalid_message'
                    break
                if 'method' not in msg and 'id' in msg:
                    rid = msg['id']
                    if type(rid) not in (str, int):
                        reason = 'invalid_response_id'
                        break
                    with self.lock:
                        target = self.pending.get(rid)
                    if target is not None:
                        try:
                            target.put_nowait(msg)
                        except queue.Full:
                            reason = 'duplicate_response'
                            break
                else:
                    self.callback(msg)
        except Exception:
            reason = 'reader_failed'
        finally:
            with self.lock:
                self.closed = True
                for target in self.pending.values():
                    try:
                        target.put_nowait({'error': {'code': 'transport_closed'}})
                    except queue.Full:
                        pass
            self.callback({'method': 'transport/disconnected', 'params': {'reason': reason}})

    def close(self):
        with self.lock:
            self.closed = True
            proc = self.process
            if proc is not None and proc.poll() is None:
                proc.terminate()
        if proc is not None:
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
            if self.reader is not None and self.reader is not threading.current_thread():
                self.reader.join(timeout=2)
            for stream in (proc.stdin, proc.stdout):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

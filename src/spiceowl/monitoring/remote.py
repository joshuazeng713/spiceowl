"""Explicit, durable observation of existing runs. Never submits or restarts a solver."""
import copy
import contextlib
from datetime import datetime, timedelta, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid
from typing import Protocol


class RunnerProtocol(Protocol):
    """Execution adapter; it owns remote identity validation and bounded I/O."""
    root: Path

    def path(self, run_id: str) -> Path: ...
    def read(self, run_id: str) -> dict: ...
    def status(self, run_id: str) -> dict: ...
    def collect(self, run_id: str) -> dict: ...


TERMINAL = {'simulator_complete_pending_measurements', 'simulator_failed', 'input_hash_failure', 'job_setup_failed'}


def stamp():
    return datetime.now(timezone.utc).isoformat()


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(json.dumps(value, indent=2, allow_nan=False) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def locked(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


MAX_WATCHES = 128
MAX_EVENTS = 64
MAX_FILE_BYTES = 1024 * 1024
STARTED_STATES = {'submitted', 'running_observed', 'reconcile_required', 'launch_outcome_unknown'} | TERMINAL


def _read(path):
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('Monitor state must be a regular local file')
    with path.open('rb') as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError('Monitor state exceeds the read limit')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('Monitor state must be an object')
    return value


def _timestamp(value):
    try:
        result = datetime.fromisoformat(value) if isinstance(value, str) else None
        return result if result is not None and result.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def _execution(record):
    return {key: record.get(key) for key in ('state', 'remote_state', 'simulator_exit_code', 'observed_at')}


def _binding(record):
    return {key: record.get(key) for key in ('source_commit', 'execution_site_sha256')}


class RuntimeMonitor:
    """Call watch explicitly, then start. Construction and snapshots never poll.

    stop(timeout) returns False if an existing bounded Runner call is still
    finishing. It never kills a remote process. Collection is opt-in, at most
    once per run, and calls Runner.collect only; it never invokes OCEAN.
    """
    def __init__(self, runner: RunnerProtocol, interval_seconds=30, max_backoff_seconds=300):
        for value in (interval_seconds, max_backoff_seconds):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError('Monitor intervals must be finite positive seconds')
        if max_backoff_seconds < interval_seconds:
            raise ValueError('Maximum backoff must not be shorter than the interval')
        self.runner = runner
        self.interval = float(interval_seconds)
        self.max_backoff = float(max_backoff_seconds)
        self.root = Path(runner.root) / '.monitor'
        self._mutex = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._owner = None
        self._watches = self._registry()
        self._due = {}
        self._errors = {}

    def _run_path(self, run_id):
        if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,180}', run_id):
            raise ValueError('An explicit existing run ID is required')
        path = self.runner.path(run_id)
        if path.is_symlink() or not path.resolve().is_relative_to(Path(self.runner.root).resolve()):
            raise ValueError('Monitor run directory must remain inside Runner storage')
        return path

    def _registry(self):
        path = self.root / 'registry.json'
        if not path.exists() and not path.is_symlink():
            return {}
        value = _read(path)
        watches = value.get('watches')
        if value.get('schema') != 'ic.sim.monitor-registry.v1' or not isinstance(watches, dict) or len(watches) > MAX_WATCHES:
            raise ValueError('Invalid bounded monitor watch registry')
        for run_id, entry in watches.items():
            self._run_path(run_id)
            if (not isinstance(entry, dict) or entry.get('run_id') != run_id
                    or type(entry.get('allow_collect')) is not bool
                    or not re.fullmatch('[a-f0-9]{32}', str(entry.get('watch_id', '')))):
                raise ValueError('Invalid persisted explicit watch')
        return watches

    def _receipt(self, run_id):
        path = self._run_path(run_id) / 'monitor.json'
        if not path.exists() and not path.is_symlink():
            return None
        value = _read(path)
        if (value.get('schema') != 'ic.sim.monitor.v1' or value.get('run_id') != run_id
                or not isinstance(value.get('observation'), dict) or not isinstance(value.get('execution'), dict)
                or not isinstance(value.get('collection'), dict) or not isinstance(value.get('events'), list)
                or len(value['events']) > MAX_EVENTS or type(value.get('event_sequence')) is not int):
            raise ValueError('Invalid saved monitor receipt; reconcile before watching again')
        return value

    def _save(self, receipt):
        receipt['updated_at'] = stamp()
        atomic(self._run_path(receipt['run_id']) / 'monitor.json', receipt)

    @staticmethod
    def _event(receipt, kind):
        receipt['event_sequence'] += 1
        receipt['events'] = (receipt['events'] + [{'sequence': receipt['event_sequence'], 'at': stamp(), 'kind': kind}])[-MAX_EVENTS:]

    def watch(self, run_id, allow_collect=False):
        if type(allow_collect) is not bool:
            raise ValueError('allow_collect must be explicitly boolean')
        path = self._run_path(run_id)
        record = self.runner.read(run_id)
        if record.get('run_id') != run_id or record.get('state') not in STARTED_STATES:
            raise ValueError('Only an existing submitted or explicitly reconcilable run can be watched')
        with self._mutex, locked(self.root / 'registry.lock'):
            watches = self._registry()
            current = watches.get(run_id)
            receipt = self._receipt(run_id)
            if current and current['allow_collect'] == allow_collect:
                if receipt is None or receipt.get('watch_id') != current['watch_id'] or receipt.get('allow_collect') != allow_collect:
                    raise ValueError('Explicit watch and receipt are inconsistent; reconcile before monitoring')
                self._watches = watches
                return copy.deepcopy(receipt)
            if not current and len(watches) >= MAX_WATCHES:
                raise ValueError('Monitor watch limit reached')
            entry = {'run_id': run_id, 'watch_id': uuid.uuid4().hex, 'armed_at': stamp(), 'allow_collect': allow_collect}
            if receipt is None:
                receipt = {'schema': 'ic.sim.monitor.v1', 'run_id': run_id,
                           'binding': _binding(record),
                           'execution': _execution(record), 'collection': {'state': 'not_requested'},
                           'observation': {'state': 'never_checked', 'last_attempt_at': None,
                                           'last_success_at': None, 'consecutive_failures': 0,
                                           'backoff_seconds': 0, 'next_check_at': None, 'error_code': None},
                           'events': [], 'event_sequence': 0}
            receipt.update(watch_id=entry['watch_id'], watch_state='armed', allow_collect=allow_collect)
            self._event(receipt, 'armed')
            self._save(receipt)
            watches[run_id] = entry
            atomic(self.root / 'registry.json', {'schema': 'ic.sim.monitor-registry.v1', 'watches': watches})
            self._watches = watches
            self._due[run_id] = 0
            self._wake.set()
            return copy.deepcopy(receipt)

    arm = watch

    def unwatch(self, run_id):
        self._run_path(run_id)
        with self._mutex, locked(self.root / 'registry.lock'):
            watches = self._registry()
            watches.pop(run_id, None)
            atomic(self.root / 'registry.json', {'schema': 'ic.sim.monitor-registry.v1', 'watches': watches})
            self._watches = watches
            self._due.pop(run_id, None)
            self._errors.pop(run_id, None)
            receipt = self._receipt(run_id)
            if receipt is not None:
                receipt['watch_state'] = 'stopped'
                self._event(receipt, 'disarmed')
                self._save(receipt)
            self._wake.set()
            return copy.deepcopy(receipt)

    disarm = unwatch

    def snapshot(self, run_id=None):
        with self._mutex:
            if run_id is not None:
                return copy.deepcopy(self._receipt(run_id))
            running = bool(self._thread and self._thread.is_alive())
            return {'schema': 'ic.sim.monitor-status.v1', 'running': running,
                    'stopping': running and self._stop.is_set(), 'read_at': stamp(),
                    'watches': [copy.deepcopy(self._receipt(rid)) for rid in sorted(self._watches)],
                    'errors': copy.deepcopy(self._errors)}

    def _current(self, run_id, entry):
        return self._watches.get(run_id, {}).get('watch_id') == entry['watch_id']

    def _collect_once(self, receipt, record, entry):
        with self._mutex:
            if not self._current(receipt['run_id'], entry) or self._stop.is_set() or receipt['collection']['state'] != 'not_requested':
                return receipt
            if record.get('collection_phase') == 'complete' and record.get('collection'):
                receipt['collection'] = {'state': 'complete', 'origin': 'existing_run_receipt'}
                self._save(receipt)
                return receipt
            if record.get('collection_phase') or record.get('operation') in ('collecting', 'archiving'):
                receipt['collection'] = {'state': 'reconcile_required', 'reason': 'existing_collection_operation'}
                self._event(receipt, 'collection_reconcile_required')
                self._save(receipt)
                return receipt
            receipt['collection'] = {'state': 'claimed', 'run_id': receipt['run_id'], 'claimed_at': stamp()}
            self._event(receipt, 'collection_claimed')
            self._save(receipt)  # Durable before remote work; a lost result must not replay.
        try:
            result = self.runner.collect(receipt['run_id'])
            if result.get('run_id') != receipt['run_id'] or result.get('collection_phase') != 'complete' or not result.get('collection'):
                raise ValueError('Collection did not return its bound completed receipt')
            update = {'state': 'complete', 'completed_at': stamp()}
            event = 'collection_complete'
        except Exception:
            update = {'state': 'outcome_unknown', 'error_code': 'collection_outcome_unknown'}
            event = 'collection_outcome_unknown'
        with self._mutex:
            # unwatch may happen while collection finishes. Preserve that decision.
            current = self._receipt(receipt['run_id'])
            current['collection'].update(update)
            self._event(current, event)
            self._save(current)
            return current

    def poll_once(self, run_id):
        """One bounded read of an explicitly armed run; useful for deterministic hosts."""
        with self._mutex:
            if run_id not in self._watches:
                raise ValueError('Run is not explicitly armed for observation')
            entry = copy.deepcopy(self._watches[run_id])
        path = self._run_path(run_id)
        with (path / 'monitor.lock').open('a') as gate:
            try:
                fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return self.snapshot(run_id)
            receipt = self._receipt(run_id)
            if receipt is None or receipt.get('watch_id') != entry['watch_id'] or receipt.get('allow_collect') != entry['allow_collect']:
                raise ValueError('Explicit watch and receipt are inconsistent; reconcile before monitoring')
            if receipt['collection']['state'] == 'claimed':
                receipt['collection']['state'] = 'outcome_unknown'
                self._event(receipt, 'collection_outcome_unknown')
                self._save(receipt)
            if receipt['watch_state'] != 'armed':
                return copy.deepcopy(receipt)
            before = self.runner.read(run_id)
            attempt = stamp()
            result = None
            blocked = False
            error_code = 'status_unavailable'
            try:
                if before.get('run_id') != run_id or receipt.get('binding') != _binding(before):
                    blocked = True
                    raise ValueError('Watched run binding changed')
                result = self.runner.status(run_id)
                if result.get('run_id') != run_id or receipt.get('binding') != _binding(result):
                    blocked = True
                    raise ValueError('Observed run binding changed')
                transport = result.get('status_transport')
                blocked = transport == 'blocked'
                observed, prior = _timestamp(result.get('observed_at')), _timestamp(before.get('observed_at'))
                fresh = observed is not None and (prior is None or observed > prior)
                success = (result.get('run_id') == run_id and fresh
                           and transport in (None, 'connected') and result.get('remote_status_exit', 0) == 0)
                if not success:
                    error_code = 'execution_binding_blocked' if blocked else 'observation_not_refreshed' if not fresh else 'status_unavailable'
            except Exception:
                success = False
                error_code = 'execution_binding_blocked' if blocked else 'status_unavailable'
            with self._mutex:
                if not self._current(run_id, entry):
                    return self.snapshot(run_id)
                observation = receipt['observation']
                previous = observation['state']
                observation['last_attempt_at'] = attempt
                if success:
                    observation.update(state='connected', last_success_at=result['observed_at'],
                                       consecutive_failures=0, backoff_seconds=0, error_code=None)
                    receipt['execution'] = _execution(result)
                    if previous in ('disconnected', 'blocked'):
                        self._event(receipt, 'reconnected')
                    elif previous != 'connected':
                        self._event(receipt, 'connected')
                    terminal = result.get('remote_state') in TERMINAL and type(result.get('simulator_exit_code')) is int
                    delay = self.interval
                    if terminal:
                        receipt['watch_state'] = 'terminal'
                        observation['next_check_at'] = None
                        self._event(receipt, 'terminal_observed')
                    else:
                        observation['next_check_at'] = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
                else:
                    failures = observation['consecutive_failures'] + 1
                    delay = min(self.max_backoff, self.interval * 2 ** min(failures - 1, 30))
                    observation.update(state='blocked' if blocked else 'disconnected', consecutive_failures=failures,
                                       backoff_seconds=delay, error_code=error_code,
                                       next_check_at=(datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat())
                    self._event(receipt, observation['state'] if previous != observation['state'] else 'backoff')
                    terminal = False
                self._save(receipt)
                self._due[run_id] = time.monotonic() + delay
                collect = terminal and entry['allow_collect'] and not self._stop.is_set()
            if collect:
                with self._mutex:
                    if not self._current(run_id, entry):
                        return self.snapshot(run_id)
                receipt = self._collect_once(receipt, result, entry)
            return copy.deepcopy(receipt)

    def _loop(self):
        try:
            while not self._stop.is_set():
                with self._mutex:
                    runs = list(self._watches)
                for run_id in runs:
                    if self._stop.is_set():
                        break
                    if self._due.get(run_id, 0) > time.monotonic():
                        continue
                    try:
                        result = self.poll_once(run_id)
                        if result and result['watch_state'] != 'armed':
                            self._due[run_id] = float('inf')
                        self._errors.pop(run_id, None)
                    except Exception:
                        self._errors[run_id] = 'monitor_receipt_or_status_unavailable'
                        self._due[run_id] = time.monotonic() + self.max_backoff
                with self._mutex:
                    remaining = [max(0, self._due.get(rid, 0) - time.monotonic()) for rid in self._watches]
                self._wake.wait(min([self.interval] + remaining) if remaining else self.interval)
                self._wake.clear()
        finally:
            with self._mutex:
                if self._owner is not None:
                    self._owner.close()
                    self._owner = None

    def start(self):
        with self._mutex:
            if self._thread and self._thread.is_alive():
                return False
            self._watches = self._registry()
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            owner = (self.root / 'owner.lock').open('a')
            try:
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                owner.close()
                raise ValueError('A monitor already owns this Runner storage') from None
            self._owner = owner
            self._stop.clear()
            self._due = {}
            for run_id in self._watches:
                try:
                    receipt = self._receipt(run_id)
                    next_check = _timestamp(receipt['observation'].get('next_check_at')) if receipt else None
                    delay = max(0, min(self.max_backoff, (next_check - datetime.now(timezone.utc)).total_seconds())) if next_check else 0
                    self._due[run_id] = time.monotonic() + delay
                except (OSError, ValueError, TypeError, KeyError):
                    self._due[run_id] = 0  # The worker records a bounded local error, never guesses state.
            self._thread = threading.Thread(target=self._loop, name='simulation-monitor', daemon=True)
            self._thread.start()
            return True

    def stop(self, timeout=1):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 <= timeout <= 30:
            raise ValueError('Stop timeout must be from zero to thirty seconds')
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout)
        return not bool(thread and thread.is_alive())

    dispose = stop

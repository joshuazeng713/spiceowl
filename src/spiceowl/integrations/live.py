"""Finite signed client for CircuitGit's existing simulation Runner.

The private journal only reconciles client requests. It never approves, starts
workers, invokes SSH, owns simulator processes, or automatically retries a call.
"""
from contextlib import contextmanager
import copy
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import socket
import stat
import tempfile
import threading
import time
from urllib.parse import quote, urlencode, urlsplit
from uuid import uuid4

from spiceowl._platform import (
    fsync_dir,
    is_owner_private_file,
    lock_file,
    open_lock_nofollow,
    open_readonly_nofollow,
    unlock_file,
)
from spiceowl.domain.common import DomainError, utcnow
from spiceowl.integrations.authority import load_key, sign
from spiceowl.integrations.ic_workbench import decode_envelope, validate_gateway_config, validate_submission

OPERATIONS = frozenset({'runtime', 'scan', 'submit', 'status', 'collect', 'watch', 'unwatch',
                        'prepare', 'run', 'inspect'})
KEYED = frozenset({'submit', 'collect', 'run'})
CALLER = 'spiceowl-local'
MAX_CONFIG_BYTES = 16384
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_JOURNAL_BYTES = 2 * 1024 * 1024
MAX_REQUESTS = 512
READ_TIMEOUT = 30.0
CONTROL_TIMEOUT = 120.0
_ID = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,179}')
_KEY = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{7,159}')
# Public names deliberately exclude errors, routing, archive/publication details,
# local receipt paths, key files, operator policy and arbitrary backend output.
_PUBLIC_FIELDS = frozenset('''schema project_id read_at pool runs capabilities configured checked_at
    hosts scan_in_progress host_id label reachable cpu_busy_percent load_1m cpu_count
    memory_available_bytes memory_total_bytes observed_at error_code run_id source_commit
    configuration execution_target mode plan_id resource_observed_at resource_reservation
    execution state exit_code monitor last_attempt_at last_success_at next_check_at failures
    diagnostics acceptance scan direct_submit direct_collect collectable created_at updated_at submitted_at
    remote_state simulator_exit_code observation consecutive_failures backoff_seconds
    watch_state allow_collect event_sequence events sequence at kind collection
    automatic_retry advisory_only scientific_acceptance observed_context category severity code
    evidence_line_numbers redacted_excerpt suggested_actions data_validity specification
    log_line_count log_bytes log_scope acceptance_source oom_evidence source run_bound oom_kill
    oom_kill_delta agent_proposal action description
    recipe_id job_id inspection_state missing field message report measurements
    dependency_summary file_count pre_pinned launch_attempts experiments recipes template prepare run inspect
    status scope evidence execution_status measurement_validity functional_performance
    operating_qualification dependency_provenance scope_id manifest_sha256 frozen_before_execution
    checks log_complete line_numbers dependency_id sha256 count inventory_complete
    execution_state warning_summary reported_errors reported_warnings reported_notices suppressed_warnings
    footer_line_numbers suppression_line_numbers count_conflict diagnostic_coverage observed_errors
    observed_warnings observed_notices completion_footer_count duplicate_record_count coverage_gap_line_numbers
    records_after_footer structure_complete error_line_numbers warning_line_numbers notice_line_numbers
    classification_details scale_override duplicate_seen values_relation
    recipe_sha256 psf_sha256 evidence_source sample_count time_span_s voltage_ranges_v
    name dimension expected actual tolerance_s maximum_observed_s maximum_allowed_s
    maximum_error_v tolerance_v minimum_v maximum_v windows start_s end_s minimum_samples
    actual_samples input_range_v output_range_v output_threshold_passed signal voltage_range_v threshold_passed
    delays records summary tPHL tPLH min_s max_s mean_s input_crossing_s output_crossing_s delay_s
    input_transition_count output_transition_count paired_count ambiguous_or_missing_pair_count
    records_truncated crossing_v acceptance_effect overall_qualified_acceptance qualification_scope'''.split())


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate key')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('nonfinite number')
    def number(text):
        value = float(text)
        if not math.isfinite(value):
            raise ValueError('nonfinite number')
        return value
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant, parse_float=number)
    except (ValueError, UnicodeError, RecursionError):
        raise DomainError('Invalid private live-client JSON.', 'live_config_invalid') from None


def _private_read(path, limit, *, missing=False):
    try:
        fd = open_readonly_nofollow(path)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not is_owner_private_file(info, allow_root=True) or info.st_size > limit:
                raise DomainError('Use an owner-private regular live-client file within the size limit.', 'live_config_invalid')
            raw = stream.read(limit + 1)
            if len(raw) > limit:
                raise DomainError('Private live-client file exceeds its size limit.', 'live_config_invalid')
            return raw
    except FileNotFoundError:
        if missing:
            return None
        raise DomainError('The private live-client configuration is unavailable.', 'live_config_invalid') from None
    except OSError:
        raise DomainError('Cannot read a private live-client file.', 'live_config_invalid') from None


def _safe_public(value, depth=0):
    if depth > 16:
        raise DomainError('Simulation response is too deeply nested.', 'live_protocol')
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key not in _PUBLIC_FIELDS:
                continue
            if key == 'voltage_ranges_v':
                # Signal names are dynamic, but only numeric min/max values are
                # admitted. This is not an arbitrary nested backend object.
                if (not isinstance(item, dict) or len(item) > 64 or any(
                        not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', name)
                        or not isinstance(bounds, list) or len(bounds) != 2
                        or any(type(x) not in (int, float) or not math.isfinite(x) for x in bounds)
                        for name, bounds in item.items())):
                    raise DomainError('The simulation response has invalid voltage statistics.', 'live_protocol')
                result[key] = copy.deepcopy(item)
            else:
                result[key] = _safe_public(item, depth + 1)
        return result
    if isinstance(value, list):
        if len(value) > 2048:
            raise DomainError('Simulation response has too many records.', 'live_protocol')
        return [_safe_public(item, depth + 1) for item in value]
    if isinstance(value, str):
        if len(value) > 4096:
            raise DomainError('Simulation response has an oversized field.', 'live_protocol')
        # Native diagnostics already redact excerpts. Also protect accidental raw
        # paths, URLs or SSH user@host output at this presentation boundary.
        if re.search(r'(?:https?://|(?:^|\s)/(?:[^\s]+)|\b[^\s]+@[^\s]+|[\x00-\x1f\x7f])', value):
            return '[redacted]'
    return value


class LiveSimulationClient:
    """One explicitly bound project over literal IPv4 loopback HTTP."""

    def __init__(self, config_path):
        try:
            path = Path(config_path).absolute()
        except (TypeError, ValueError):
            raise DomainError('Select a private live-client configuration file.', 'live_config_invalid') from None
        config = _json(_private_read(path, MAX_CONFIG_BYTES))
        required = {'schema', 'base_url', 'project_id', 'authority_key_file'}
        if not isinstance(config, dict) or set(config) != required or config['schema'] != 'spiceowl.live-client.v1':
            raise DomainError('Unsupported live-client configuration.', 'live_config_invalid')
        validated = validate_gateway_config({'id': 'live-client', 'label': 'Live simulation',
                                             'base_url': config['base_url'], 'project_id': config['project_id']})
        try:
            self._key = load_key(config['authority_key_file'])
        except (OSError, TypeError, ValueError):
            raise DomainError('The live-client authority key must be a private valid key file.', 'live_config_invalid') from None
        self.project_id = validated['project_id']
        self._base_url = validated['base_url']
        self._journal_path = path.with_name('.' + path.name + '.requests.json')
        self._lock_path = self._journal_path.with_suffix('.lock')
        self._scope = hashlib.sha256(json.dumps([self._base_url, self.project_id,
            hashlib.sha256(self._key).hexdigest()], separators=(',', ':')).encode()).hexdigest()

    def _request(self, method, path, raw=b'', token=None, *, timeout=READ_TIMEOUT):
        """No proxy, redirect, retry or DNS; deadline includes headers/body."""
        parsed = urlsplit(self._base_url)
        connection = http.client.HTTPConnection('127.0.0.1', parsed.port, timeout=timeout)
        deadline = time.monotonic() + timeout
        expired = threading.Event()
        connected = []
        headers = {'Accept': 'application/json', 'Connection': 'close'}
        if path.startswith('/api/simulation/'):
            headers['X-IC-Simulation-Caller'] = CALLER
            headers['X-IC-Simulation-Authorization'] = sign(self._key, CALLER, method, path, raw)
        if method == 'POST':
            headers.update({'Origin': self._base_url, 'Content-Type': 'application/json',
                            'X-Workbench-Token': token})
        def abort():
            expired.set()
            sock = connected[0] if connected else connection.sock
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            connection.close()
        timer = threading.Timer(timeout, abort)
        timer.daemon = True
        timer.start()
        try:
            connection.connect()
            connected.append(connection.sock)
            if expired.is_set() or time.monotonic() >= deadline:
                raise TimeoutError()
            connection.request(method, path, body=raw if method == 'POST' else None, headers=headers)
            response = connection.getresponse()
            if response.status not in (200, 202):
                # Never reflect a backend error: it can contain SSH routes,
                # environment paths or credentials. Even 5xx may follow a write.
                code = 'live_forbidden' if response.status in (401, 403) else 'live_http'
                raise DomainError('The configured simulation service did not accept the request.', code)
            if (response.getheader('Content-Encoding', 'identity').lower() != 'identity'
                    or response.getheader('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json'):
                raise DomainError('The simulation service returned an unsupported response.', 'live_protocol')
            length = response.getheader('Content-Length')
            if length is not None and response.getheader('Transfer-Encoding') is not None:
                raise DomainError('The simulation response has ambiguous framing.', 'live_protocol')
            if length is not None and (not re.fullmatch(r'[0-9]{1,10}', length) or int(length) > MAX_RESPONSE_BYTES):
                raise DomainError('The simulation response exceeded its size limit.', 'live_response_limit')
            chunks, count = [], 0
            while True:
                if expired.is_set() or time.monotonic() >= deadline:
                    raise TimeoutError()
                block = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - count))
                if not block:
                    break
                chunks.append(block)
                count += len(block)
                if count > MAX_RESPONSE_BYTES:
                    raise DomainError('The simulation response exceeded its size limit.', 'live_response_limit')
            if expired.is_set() or time.monotonic() >= deadline:
                raise TimeoutError()
            if length is not None and count != int(length):
                raise DomainError('The simulation response was incomplete.', 'live_protocol')
            try:
                return decode_envelope(b''.join(chunks))
            except DomainError:
                raise DomainError('The simulation service returned invalid JSON.', 'live_protocol') from None
        except (OSError, http.client.HTTPException):
            raise DomainError('Simulation service connection was interrupted; request outcome may be unknown.', 'live_transport') from None
        finally:
            timer.cancel()
            connection.close()

    def _health(self, *, timeout=READ_TIMEOUT):
        health = self._request('GET', '/api/health', timeout=timeout)
        token = health.get('mutation_token')
        if (health.get('api_version') != '1' or health.get('mode') != 'live'
                or not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,256}', token)):
            raise DomainError('The configured service is not a supported live CircuitGit service.', 'live_protocol')
        return token

    def _arguments(self, operation, args):
        if not isinstance(operation, str) or operation not in OPERATIONS or not isinstance(args, dict):
            raise DomainError('Choose a supported finite live simulation operation.')
        values = copy.deepcopy(args)
        values.setdefault('project_id', self.project_id)
        if values['project_id'] != self.project_id:
            raise DomainError('This client is bound to a different project.', 'live_project_mismatch')
        required = {'project_id'}
        if operation == 'prepare':
            required |= {'recipe_id', 'source_commit'}
        elif operation == 'run':
            required.add('plan_id')
        elif operation == 'inspect':
            required.add('job_id')
        elif operation not in ('runtime', 'scan'):
            required |= {'configuration', 'source_commit'}
        if operation in ('status', 'collect', 'watch', 'unwatch'):
            required.add('run_id')
        if set(values) != required:
            raise DomainError('Supply exactly the identity fields required by this simulation operation.')
        for name in ('configuration', 'run_id', 'recipe_id', 'job_id'):
            if name in values and (not isinstance(values[name], str) or not _ID.fullmatch(values[name])):
                raise DomainError('Invalid simulation identity field.')
        if 'run_id' in values and not re.fullmatch(r'[A-Za-z0-9_-]{1,180}', values['run_id']):
            raise DomainError('Invalid simulation run identity.')
        if 'job_id' in values and not re.fullmatch(r'[A-Za-z0-9_-]{1,180}', values['job_id']):
            raise DomainError('Invalid experiment job identity.')
        if 'plan_id' in values and (not isinstance(values['plan_id'], str)
                                  or not re.fullmatch(r'[a-f0-9]{64}', values['plan_id'])):
            raise DomainError('Use the exact 64-character prepared experiment plan ID.')
        if 'source_commit' in values and (not isinstance(values['source_commit'], str)
                                         or not re.fullmatch(r'[a-f0-9]{40}', values['source_commit'])):
            raise DomainError('Use the exact 40-character source commit for this simulation.')
        return values

    def _validate_experiment_result(self, operation, values, result):
        def check(condition):
            if not condition:
                raise DomainError('The experiment response does not match the requested identity or state.', 'live_protocol')
        check(isinstance(result, dict) and result.get('project_id') == self.project_id)
        if operation == 'prepare':
            check(result.get('schema') == 'ic.experiment.plan.v1')
            check(result.get('recipe_id') == values['recipe_id'] and result.get('source_commit') == values['source_commit'])
            check(result.get('state') in ('prepared', 'blocked'))
            check(isinstance(result.get('missing'), list))
            if result['state'] == 'blocked':
                check(result.get('plan_id') is None and bool(result['missing']))
            else:
                check(isinstance(result.get('plan_id'), str) and re.fullmatch(r'[a-f0-9]{64}', result['plan_id']) is not None)
                check(not result['missing'])
            return
        check(result.get('schema') == 'ic.experiment.job.v1')
        check(isinstance(result.get('job_id'), str) and re.fullmatch(r'[A-Za-z0-9_-]{1,180}', result['job_id']) is not None)
        check(result.get('run_id') == result['job_id'])
        check(isinstance(result.get('configuration'), str) and _ID.fullmatch(result['configuration']) is not None)
        check(isinstance(result.get('source_commit'), str) and re.fullmatch(r'[a-f0-9]{40}', result['source_commit']) is not None)
        check(isinstance(result.get('plan_id'), str) and re.fullmatch(r'[a-f0-9]{64}', result['plan_id']) is not None)
        check(operation != 'run' or result['plan_id'] == values['plan_id'])
        check(operation != 'inspect' or result['job_id'] == values['job_id'])
        if operation == 'inspect' or 'inspection_state' in result:
            check(result.get('inspection_state') in ('pending', 'working', 'collecting', 'complete', 'uncertain'))
        if result.get('inspection_state') == 'complete':
            report, measurement = result.get('report'), result.get('measurements')
            check(isinstance(report, dict) and report.get('schema') == 'spiceowl.simulation-report.v1'
                  and report.get('run_id') == result['job_id'])
            statuses = {'execution_status': {'queued', 'running', 'completed', 'failed', 'cancelled', 'unknown'},
                        'measurement_validity': {'valid', 'invalid', 'unknown'},
                        'functional_performance': {'pass', 'fail', 'unknown'},
                        'operating_qualification': {'passed_within_scope', 'violations_detected', 'unknown'},
                        'dependency_provenance': {'verified_within_scope', 'incomplete', 'mismatch', 'unknown'}}
            for name, allowed in statuses.items():
                item = report.get(name)
                check(isinstance(item, dict) and isinstance(item.get('status'), str) and item['status'] in allowed
                      and isinstance(item.get('scope'), dict) and isinstance(item.get('evidence'), list))
            check(isinstance(measurement, dict) and measurement.get('schema') == 'spiceowl.experiment-measurement.v1')

    @contextmanager
    def _journal(self):
        """Cross-process nonblocking claim, independent of the demo Controller."""
        try:
            fd = open_lock_nofollow(self._lock_path, 0o600)
            with os.fdopen(fd, 'r+b') as lock:
                info = os.fstat(lock.fileno())
                if not is_owner_private_file(info):
                    raise DomainError('Live request journal lock is not private.', 'live_journal_invalid')
                try:
                    lock_file(lock, nonblocking=True)
                except BlockingIOError:
                    raise DomainError('Another live request is being handled. Inspect its result before repeating.', 'live_request_busy') from None
                try:
                    raw = _private_read(self._journal_path, MAX_JOURNAL_BYTES, missing=True)
                    data = _json(raw) if raw is not None else {'schema': 'spiceowl.live-journal.v1', 'scope': self._scope, 'requests': []}
                    if (not isinstance(data, dict) or set(data) != {'schema', 'scope', 'requests'}
                            or data['schema'] != 'spiceowl.live-journal.v1' or data['scope'] != self._scope
                            or not isinstance(data['requests'], list) or len(data['requests']) > MAX_REQUESTS):
                        raise DomainError('Live request journal requires operator reconciliation.', 'live_journal_invalid')
                    for row in data['requests']:
                        if (not isinstance(row, dict) or not {'fingerprint', 'operation', 'arguments', 'key', 'state', 'created_at'} <= set(row)
                                or row['state'] not in ('pending', 'complete', 'rejected')
                                or not isinstance(row['key'], str) or not _KEY.fullmatch(row['key'])):
                            raise DomainError('Live request journal is invalid.', 'live_journal_invalid')
                        if row['operation'] not in KEYED or self._arguments(row['operation'], row['arguments']) != row['arguments']:
                            raise DomainError('Live request journal identity is invalid.', 'live_journal_invalid')
                        if row['operation'] == 'run' and row['key'] != 'experiment-' + row['arguments']['plan_id']:
                            raise DomainError('Live experiment journal changed its stable plan key.', 'live_journal_invalid')
                        expected = hashlib.sha256(json.dumps([row['operation'], row['arguments']], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                        if row['fingerprint'] != expected or (row['state'] == 'complete' and not isinstance(row.get('result'), dict)):
                            raise DomainError('Live request journal identity is invalid.', 'live_journal_invalid')
                    yield data
                finally:
                    unlock_file(lock)
        except OSError:
            raise DomainError('Cannot access the private live request journal.', 'live_journal_invalid') from None

    def _save(self, data):
        raw = json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        if len(raw) > MAX_JOURNAL_BYTES:
            raise DomainError('Live request journal is full; preserve it for operator reconciliation.', 'live_journal_limit')
        name = None
        try:
            fd, name = tempfile.mkstemp(prefix='.live-request-', dir=self._journal_path.parent)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self._journal_path)
            name = None
            fsync_dir(self._journal_path.parent)
        finally:
            if name is not None:
                os.unlink(name)

    def runtime(self):
        self._health()
        data = self._request('GET', '/api/simulation/runtime?' + urlencode({'project_id': self.project_id}))
        if (data.get('schema') != 'ic.simulation.runtime.v1' or data.get('project_id') != self.project_id
                or not isinstance(data.get('read_at'), str) or not isinstance(data.get('pool'), dict)
                or not isinstance(data.get('capabilities'), dict) or not isinstance(data.get('runs'), list)
                or len(data['runs']) > 25):
            raise DomainError('The live snapshot does not match the bound project.', 'live_protocol')
        for row in data['runs']:
            if not isinstance(row, dict) or not {'run_id', 'configuration', 'source_commit'} <= set(row):
                raise DomainError('The live snapshot has an incomplete run identity.', 'live_protocol')
            self._arguments('status', {key: row[key] for key in ('run_id', 'configuration', 'source_commit')})
        return _safe_public(data)

    def lookup_submission(self, key):
        """Read the exact saved submission mapping; absence never permits replay."""
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise DomainError('Supply the original stable submission key.')
        with self._journal() as journal:
            row = next((item for item in journal['requests'] if item['key'] == key and item['operation'] in ('submit', 'run')), None)
            if row is None:
                raise DomainError('This journal has no submission request with that key.', 'live_request_missing')
            self._health()
            data = self._request('GET', '/api/submissions/' + quote(key, safe=''))
            # Run requests are identified by the frozen plan, not the legacy
            # configuration/source tuple expected by validate_run.
            expected = row['arguments'] if row['operation'] == 'submit' else None
            validate_submission(data, key, self.project_id, expected)
            result = {'schema': 'spiceowl.live-submission-lookup.v1', 'project_id': self.project_id,
                      'state': 'mapping_found' if data['found'] else 'mapping_not_found',
                      'run_id': data['run_id'], 'read_at': data['read_at'],
                      'dispatch_outcome': 'unknown' if row['state'] == 'pending' else row['state'],
                      'replay_permitted': False, 'run': _safe_public(data['run'])}
            row['lookup'] = result
            self._save(journal)
            return result

    def workflow(self, recipe_id, source_commit, *, timeout=600.0, poll_seconds=3.0):
        """Prepare, run and inspect one fixed experiment without a model.

        Inspect observes the remote job and may collect terminal artifacts using
        the Runner's existing receipt. A timeout or uncertain observation returns
        the original identities and never prepares or submits a replacement.
        """
        args = self._arguments('prepare', {'recipe_id': recipe_id, 'source_commit': source_commit})
        for name, value, maximum in (('timeout', timeout, 604800.0), ('poll_seconds', poll_seconds, 60.0)):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= maximum:
                raise DomainError('Use a finite positive ' + name + ' within the supported waiting limit.')
        result = {'schema': 'spiceowl.experiment-workflow.v1', 'project_id': self.project_id,
                  'recipe_id': recipe_id, 'source_commit': source_commit, 'plan_id': None,
                  'job_id': None, 'run_id': None, 'submission_key': None, 'state': 'pending',
                  'stage': 'prepare', 'missing': [], 'job': None}
        deadline = time.monotonic() + timeout
        def remaining():
            available = deadline-time.monotonic()
            if available <= 0:
                raise DomainError('The workflow waiting deadline expired.', 'live_timeout')
            return available
        def accept_job(job):
            if (job.get('plan_id') != result['plan_id'] or job.get('configuration') != recipe_id
                    or job.get('source_commit') != source_commit
                    or result['job_id'] is not None and job.get('job_id') != result['job_id']):
                raise DomainError('The experiment job changed its prepared identity.', 'live_protocol')
            result.update(job_id=job['job_id'], run_id=job['run_id'], job=job)
        try:
            plan = self.execute('prepare', args, timeout=remaining())
            result.update(plan_id=plan['plan_id'], missing=plan['missing'])
            if plan['state'] == 'blocked':
                result['state'] = 'blocked'
                return result
            result.update(stage='run', submission_key='experiment-' + plan['plan_id'])
            accept_job(self.execute('run', {'plan_id': plan['plan_id']}, timeout=remaining()))
            result['stage'] = 'inspect'
            while True:
                state = result['job'].get('inspection_state', 'pending')
                if state in ('complete', 'uncertain'):
                    result['state'] = state
                    return result
                accept_job(self.execute('inspect', {'job_id': result['job_id']}, timeout=remaining()))
                if result['job']['inspection_state'] not in ('complete', 'uncertain'):
                    time.sleep(min(poll_seconds, remaining()))
        except DomainError as exc:
            result.update(state='timed_out' if exc.code == 'live_timeout' else 'uncertain', error_code=exc.code)
            return result

    def execute(self, operation, args, idempotency_key=None, *, timeout=None):
        values = self._arguments(operation, args)
        if timeout is not None and (type(timeout) not in (float, int) or not math.isfinite(timeout) or timeout <= 0):
            raise DomainError('Use a finite positive request timeout.')
        deadline = time.monotonic() + timeout if timeout is not None else None
        def remaining(limit):
            available = limit if deadline is None else min(limit, deadline-time.monotonic())
            if available <= 0:
                raise DomainError('The workflow deadline expired before the next request.', 'live_timeout')
            return available
        if idempotency_key is not None and (operation not in KEYED or not isinstance(idempotency_key, str)
                                            or not _KEY.fullmatch(idempotency_key)):
            raise DomainError('Only submit, collect and run accept stable request keys.')
        if operation == 'run':
            stable_key = 'experiment-' + values['plan_id']
            if idempotency_key is not None and idempotency_key != stable_key:
                raise DomainError('Experiment runs use the stable key derived from their prepared plan.', 'live_key_conflict')
            idempotency_key = stable_key
        if operation == 'runtime':
            return self.runtime()
        native_operation = 'resources_scan' if operation == 'scan' else operation
        def send(token, key=None):
            body = {'operation': native_operation, 'arguments': values}
            if key is not None:
                body['idempotency_key'] = key
            raw = json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
            result = self._request('POST', '/api/simulation/control', raw, token, timeout=remaining(CONTROL_TIMEOUT))
            if operation in ('prepare', 'run', 'inspect'):
                self._validate_experiment_result(operation, values, result)
            if operation in ('submit', 'status') and not {'run_id', 'project_id', 'configuration', 'source_commit'} <= set(result):
                raise DomainError('The simulation response has an incomplete identity.', 'live_protocol')
            if operation in ('submit', 'status', 'collect', 'watch', 'unwatch'):
                if not isinstance(result.get('run_id'), str) or not _ID.fullmatch(result['run_id']):
                    raise DomainError('The simulation response has no valid run identity.', 'live_protocol')
                if 'run_id' in values and result['run_id'] != values['run_id']:
                    raise DomainError('The simulation response changed the selected run.', 'live_protocol')
                for name in ('project_id', 'configuration', 'source_commit'):
                    if name in result and result[name] != values[name]:
                        raise DomainError('The simulation response changed the selected identity.', 'live_protocol')
            return _safe_public(result)
        if operation not in KEYED:
            return send(self._health(timeout=remaining(READ_TIMEOUT)))
        fingerprint = hashlib.sha256(json.dumps([operation, values], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        with self._journal() as journal:
            requests = journal['requests']
            if idempotency_key is not None:
                old = next((row for row in requests if row['key'] == idempotency_key), None)
                if old is not None and old['fingerprint'] != fingerprint:
                    raise DomainError('This key already belongs to a different live request.', 'live_key_conflict')
            else:
                old = next((row for row in reversed(requests) if row['fingerprint'] == fingerprint), None)
            unresolved = next((row for row in requests if row['fingerprint'] == fingerprint and row['state'] == 'pending'), None)
            if unresolved is not None:
                raise DomainError('The original request outcome is unknown. Its key is preserved; reconcile the existing run before another attempt.', 'live_outcome_unknown')
            if old is not None and old['state'] == 'complete':
                return copy.deepcopy(old['result'])
            if old is not None and old['state'] == 'rejected':
                raise DomainError('This request was rejected. Inspect the connection grant before issuing another key.', 'live_request_rejected')
            if len(requests) >= MAX_REQUESTS:
                raise DomainError('Live request journal is full; preserve it for operator reconciliation.', 'live_journal_limit')
            token = self._health(timeout=remaining(READ_TIMEOUT))  # No claim needed until a POST could occur.
            key = idempotency_key or ('live-' + uuid4().hex)
            row = {'fingerprint': fingerprint, 'operation': operation, 'arguments': values,
                   'key': key, 'state': 'pending', 'created_at': utcnow()}
            requests.append(row)
            self._save(journal)  # Durable before sending any control bytes.
            try:
                result = send(token, key)
            except DomainError as exc:
                if exc.code == 'live_forbidden':
                    row['state'] = 'rejected'
                    self._save(journal)
                    raise
                raise DomainError('The original request outcome is unknown. Its key is preserved; reconcile the existing run before another attempt.', 'live_outcome_unknown') from None
            row.update(state='complete', result=result)
            self._save(journal)
            return result

"""Private SQLite lookup tables over complete, bounded discovery observations.

No method probes a server or scans a PDK. A hit describes previously observed
bytes; it never certifies current remote content or replaces a fresh run audit.
"""
import copy
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import time

from spiceowl.domain.common import DomainError
from .pdk_discovery import profile_suggestions


SCHEMA = 'spiceowl.pdk-lut.v1'
HASH = re.compile(r'[a-f0-9]{64}\Z')
NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_.$:+-]{0,127}\Z')
DEFAULT_MAX_AGE_S = 86400
MAX_DATABASE_BYTES = 128 * 1024 * 1024
MAX_QUERY_BYTES = 2 * 1024 * 1024


def _invalid(message='Use bounded observed server, root and model-scope evidence.'):
    raise DomainError(message, 'invalid_pdk_lut')


def _json(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        _invalid('Cache evidence must be finite JSON.')


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _identity(value):
    if (not isinstance(value, dict) or type(value.get('uid')) is not int
            or not 0 <= value['uid'] <= 2**32
            or any(not isinstance(value.get(key), str) or not 1 <= len(value[key]) <= 256
                   or any(ord(char) < 32 for char in value[key]) for key in ('hostname', 'username'))):
        _invalid('An observed hostname, username and numeric uid are required.')
    identity = {key: value[key] for key in ('hostname', 'username', 'uid')}
    for key in ('host_key_sha256', 'machine_id_sha256'):
        if key in value:
            if not isinstance(value[key], str) or not HASH.fullmatch(value[key]): _invalid()
            identity[key] = value[key]
    return identity


def _roots(value):
    if (not isinstance(value, list) or not 1 <= len(value) <= 8
            or any(not isinstance(root, str) or not 1 <= len(root) <= 4096
                   or not root.startswith('/') or '\x00' in root
                   or '..' in PurePosixPath(root).parts for root in value)):
        _invalid()
    return sorted(set(str(PurePosixPath(root)) for root in value))


def _models(value):
    if (not isinstance(value, list) or not 1 <= len(value) <= 128
            or any(not isinstance(name, str) or not NAME.fullmatch(name) for name in value)
            or len(set(value)) != len(value)):
        _invalid()
    return sorted(value)


def _timestamp(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0: _invalid()
    return float(value)


def content_fingerprint(discovery):
    """Hash supplied file/TMI observations, never read the corresponding paths."""
    profile_suggestions(discovery)
    files = []
    for row in discovery.get('files', []):
        if not isinstance(row, dict) or not isinstance(row.get('path'), str): _invalid()
        sha = row.get('sha256')
        if not isinstance(sha, str) or not HASH.fullmatch(sha): _invalid()
        files.append({'path': row['path'], 'sha256': sha})
    tmi = {}
    for candidate in discovery.get('entry_candidates', []):
        for group in candidate.get('tmi_candidates', []):
            for row in group.get('platform_candidates', []):
                path, sha = row.get('path'), row.get('sha256')
                if not isinstance(path, str) or not isinstance(sha, str) or not HASH.fullmatch(sha): _invalid()
                tmi[(path, sha)] = {'path': path, 'sha256': sha}
    return _digest({'roots': _roots(discovery.get('roots')),
                    'files': sorted(files, key=lambda row: (row['path'], row['sha256'])),
                    'tmi_libraries': [tmi[key] for key in sorted(tmi)]})


class PdkLut:
    """One explicitly located private store; instantiate one handle per thread."""

    def __init__(self, path, *, read_only=False):
        if type(read_only) is not bool: _invalid()
        self.read_only = read_only
        self.path = Path(path).expanduser().absolute()
        if self.path.is_symlink() or self.path.exists() and not self.path.is_file():
            _invalid('Choose a regular private SQLite cache file.')
        if read_only and not self.path.exists():
            _invalid('No existing PDK cache is available for read-only lookup.')
        if not read_only: self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.path.exists():
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        elif self.path.stat().st_mode & 0o077:
            _invalid('The PDK cache file must be owner-private (mode 0600).')
        self.connection = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=5) if read_only else sqlite3.connect(self.path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        version = self.connection.execute('PRAGMA user_version').fetchone()[0]
        if version not in ((1, 2) if read_only else (0, 1, 2)):
            self.connection.close(); _invalid('Unsupported PDK cache schema version.')
        self.index_version = version
        page_size = self.connection.execute('PRAGMA page_size').fetchone()[0]
        pages = self.connection.execute('PRAGMA page_count').fetchone()[0]
        if pages * page_size > MAX_DATABASE_BYTES:
            self.connection.close(); _invalid('The PDK cache exceeds its bounded database size.')
        if read_only:
            return
        self.connection.execute('PRAGMA max_page_count=' + str(MAX_DATABASE_BYTES // page_size))
        if version == 2:
            return
        with self.connection:
            self.connection.executescript('''
                CREATE TABLE IF NOT EXISTS observations (
                    cache_id TEXT PRIMARY KEY, scope_key TEXT NOT NULL,
                    content_fingerprint TEXT NOT NULL, discovery_id TEXT NOT NULL,
                    observed_at REAL NOT NULL, metadata TEXT NOT NULL, discovery TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS observation_scope ON observations(scope_key, observed_at DESC);
                CREATE INDEX IF NOT EXISTS observation_content ON observations(scope_key, content_fingerprint, observed_at DESC);
                CREATE TABLE IF NOT EXISTS model_bindings (
                    cache_id TEXT NOT NULL, model_name TEXT NOT NULL,
                    candidate_id TEXT NOT NULL, mapping TEXT NOT NULL,
                    PRIMARY KEY(cache_id, model_name, candidate_id)
                );
                CREATE INDEX IF NOT EXISTS binding_names ON model_bindings(cache_id, model_name);
                CREATE TABLE IF NOT EXISTS recommendation_heads (
                    cache_id TEXT PRIMARY KEY, header TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ranked_bindings (
                    cache_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
                    rank INTEGER NOT NULL, score INTEGER NOT NULL,
                    eligible INTEGER NOT NULL, proposal TEXT NOT NULL,
                    PRIMARY KEY(cache_id, candidate_id)
                );
                CREATE INDEX IF NOT EXISTS binding_rank ON ranked_bindings(cache_id, rank);
                PRAGMA user_version=2;
            ''')
        self.index_version = 2

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def put(self, discovery, *, server_identity, observed_at=None):
        """Atomically save the entire original receipt and its per-model index."""
        if self.read_only: _invalid('A read-only PDK cache cannot store observations.')
        identity = _identity(server_identity)
        profile_suggestions(discovery)
        roots, models = _roots(discovery.get('roots')), _models(discovery.get('required_masters'))
        observed = _timestamp(time.time() if observed_at is None else observed_at)
        fingerprint = content_fingerprint(discovery)
        scope = {'server_identity': identity, 'roots': roots, 'required_masters': models}
        scope_key = _digest(scope)
        cache_id = _digest({'scope_key': scope_key, 'content_fingerprint': fingerprint,
                            'discovery_id': discovery['discovery_id'], 'observed_at': observed})
        covered = sorted({name for candidate in discovery.get('entry_candidates', [])
                          for name in candidate.get('master_coverage', []) if name in models})
        coverage = {'kind': 'required_model_filtered', 'required_masters': models,
                    'covered_model_names': covered,
                    'whole_pdk_inventory': False, 'search_complete': discovery.get('search_complete') is True,
                    'results_omitted': discovery.get('results_omitted') is True,
                    'unknown_count': discovery.get('unknown_count'),
                    'candidate_count': len(discovery.get('entry_candidates', [])),
                    'search_policy': copy.deepcopy(discovery.get('search_policy', {})),
                    'detail_omissions': copy.deepcopy(discovery.get('detail_omissions', []))}
        metadata = {'schema': SCHEMA, 'cache_id': cache_id, 'scope_key': scope_key,
                    'server_identity': identity, 'roots': roots,
                    'content_fingerprint': fingerprint, 'observed_at': observed,
                    'original_discovery_id': discovery['discovery_id'],
                    'original_source_sha256': discovery['source_sha256'],
                    'coverage': coverage, 'fresh_audit_required': True,
                    'remote_freshness_verified': False, 'ready_to_run': False}
        mappings = []
        file_hashes = {row['path']: row['sha256'] for row in discovery.get('files', [])}
        for candidate in discovery.get('entry_candidates', []):
            for model in candidate.get('models', []):
                if model.get('name') not in models: _invalid('Model mapping is outside the original scan scope.')
                mapping = {'model_name': model['name'], 'candidate_id': candidate['candidate_id'],
                    'entry_path': candidate['entry_path'], 'entry_sha256': candidate['entry_sha256'],
                    'entry_role': candidate['entry_role'], 'section_candidates': candidate['section_candidates'],
                    'available_sections': candidate['available_sections'],
                    'polarity': model.get('polarity'), 'ordered_terminal_roles': model.get('ordered_terminal_roles'),
                    'model': model, 'tmi_candidates': candidate.get('tmi_candidates', []),
                    'dependency_scopes': candidate.get('dependency_scopes', []),
                    'dependency_hashes': {path: file_hashes.get(path) for path in candidate.get('dependency_paths', [])},
                    'coverage_complete': candidate.get('coverage_complete') is True,
                    'missing_masters': candidate.get('missing_masters', []),
                    'unknown_codes': candidate.get('unknown_codes', []),
                    'fresh_audit_required': True, 'ready_to_run': False}
                mappings.append((cache_id, model['name'], candidate['candidate_id'], _json(mapping)))
        from .recommendations import recommend_model_bindings
        ranked = recommend_model_bindings(discovery)
        originals = {row['candidate_id']: row for row in discovery.get('entry_candidates', [])}
        proposals = []
        for proposal in ranked['candidates']:
            candidate = originals[proposal['candidate_id']]
            proposal['binding'] = {'entry_path': candidate['entry_path'], 'entry_sha256': candidate['entry_sha256'],
                'dependency_scopes': candidate.get('dependency_scopes', []),
                'dependency_hashes': {path: file_hashes.get(path) for path in candidate.get('dependency_paths', [])},
                'source_models': candidate.get('models', []), 'tmi_candidates': candidate.get('tmi_candidates', [])}
            proposals.append((cache_id, proposal['candidate_id'], proposal['rank'], proposal['score'],
                              int(proposal['eligible_for_recommendation']), _json(proposal)))
        header = {key: value for key, value in ranked.items() if key != 'candidates'}
        header['candidate_count'] = len(proposals)
        try:
            with self.connection:
                self.connection.execute('INSERT OR REPLACE INTO observations VALUES (?,?,?,?,?,?,?)',
                    (cache_id, scope_key, fingerprint, discovery['discovery_id'], observed, _json(metadata), _json(discovery)))
                self.connection.execute('DELETE FROM model_bindings WHERE cache_id=?', (cache_id,))
                self.connection.executemany('INSERT INTO model_bindings VALUES (?,?,?,?)', mappings)
                self.connection.execute('INSERT OR REPLACE INTO recommendation_heads VALUES (?,?)', (cache_id, _json(header)))
                self.connection.execute('DELETE FROM ranked_bindings WHERE cache_id=?', (cache_id,))
                self.connection.executemany('INSERT INTO ranked_bindings VALUES (?,?,?,?,?,?)', proposals)
        except sqlite3.DatabaseError:
            raise DomainError('The bounded local PDK cache could not store this observation; existing records were retained.', 'pdk_lut_storage') from None
        return copy.deepcopy(metadata)

    def lookup(self, *, server_identity, roots, required_masters, content_fingerprint=None,
               max_age_s=DEFAULT_MAX_AGE_S, now=None, refresh=False, include_discovery=True):
        """Match an exact observed identity/root/model set; never scan on a miss."""
        identity, roots, models = _identity(server_identity), _roots(roots), _models(required_masters)
        if type(refresh) is not bool or type(include_discovery) is not bool: _invalid()
        age_limit = _timestamp(max_age_s); current = _timestamp(time.time() if now is None else now)
        if content_fingerprint is not None and (not isinstance(content_fingerprint, str) or not HASH.fullmatch(content_fingerprint)): _invalid()
        scope_key = _digest({'server_identity': identity, 'roots': roots, 'required_masters': models})
        result = {'schema': SCHEMA, 'status': 'refresh_required' if refresh else 'miss',
                  'scan_required': True, 'record': None, 'fresh_audit_required': True,
                  'remote_freshness_verified': False, 'ready_to_run': False}
        if refresh: return result
        columns = 'metadata,observed_at' + (',discovery' if include_discovery else '')
        query = 'SELECT ' + columns + ' FROM observations WHERE scope_key=?'
        args = [scope_key]
        if content_fingerprint is not None:
            query += ' AND content_fingerprint=?'; args.append(content_fingerprint)
        row = self.connection.execute(query + ' ORDER BY observed_at DESC,cache_id LIMIT 1', args).fetchone()
        if row is None: return result
        metadata = json.loads(row['metadata'])
        age = current - row['observed_at']
        stale = age < 0 or age > age_limit
        metadata['staleness'] = {'age_s': age, 'max_age_s': age_limit,
            'state': 'clock_skew' if age < 0 else 'expired' if stale else 'within_local_age_policy',
            'remote_content_current': 'unknown', 'expected_fingerprint_supplied': content_fingerprint is not None}
        if include_discovery: metadata['discovery'] = json.loads(row['discovery'])
        result.update(status='stale' if stale else 'hit', scan_required=stale, record=metadata)
        return result
    def query_bindings(self, *, model_names=None, limit=256, offset=0, **lookup_args):
        """Indexed model mappings; bounded pages avoid decoding the full receipt."""
        if type(limit) is not int or not 1 <= limit <= 4096 or type(offset) is not int or not 0 <= offset <= 32768: _invalid()
        if 'include_discovery' in lookup_args: _invalid()
        requested = _models(model_names if model_names is not None else lookup_args.get('required_masters'))
        scope_models = _models(lookup_args.get('required_masters'))
        if not set(requested) <= set(scope_models): _invalid('Requested bindings exceed the exact cached scan model scope.')
        result = self.lookup(**lookup_args, include_discovery=False)
        result.update(mappings=[], total=0, truncated=False, next_offset=None)
        if result['status'] != 'hit': return result
        where = 'cache_id=? AND model_name IN (' + ','.join('?' for _ in requested) + ')'
        params = [result['record']['cache_id'], *requested]
        total = self.connection.execute('SELECT COUNT(*) FROM model_bindings WHERE ' + where, params).fetchone()[0]
        rows = self.connection.execute('SELECT mapping FROM model_bindings WHERE ' + where +
            ' ORDER BY model_name,candidate_id LIMIT ? OFFSET ?', [*params, limit, offset]).fetchall()
        mappings, size = [], len(_json(result).encode()) + 1024
        for row in rows:
            if size + len(row['mapping'].encode()) + 1 > MAX_QUERY_BYTES: break
            mappings.append(json.loads(row['mapping'])); size += len(row['mapping'].encode()) + 1
        result.update(mappings=mappings, total=total,
                      truncated=offset + len(mappings) < total,
                      next_offset=offset + len(mappings) if mappings and offset + len(mappings) < total else None)
        if rows and not mappings:
            result['missing'] = [{'code': 'binding_exceeds_query_budget',
                'message': 'This complete binding cannot fit one bounded query page; inspect its original private observation.'}]
        return result

    def query_best_bindings(self, *, remote_platform=None, limit=8, **lookup_args):
        """Return pre-ranked complete binding drafts, resolving only their TMI.

        Ranking and section/contract proposals were indexed at ingestion. The
        bounded lookup never re-ranks the full PDK or invents a default from ID
        order. Raw source models and entry/dependency hashes accompany each
        returned proposal. Current platform evidence only changes TMI choices.
        """
        if type(limit) is not int or not 1 <= limit <= 32 or 'include_discovery' in lookup_args: _invalid()
        result = self.lookup(**lookup_args, include_discovery=False)
        result['recommendation'] = None
        if result['status'] != 'hit': return result
        cache_id = result['record']['cache_id']
        head = self.connection.execute('SELECT header FROM recommendation_heads WHERE cache_id=?', (cache_id,)).fetchone() if self.index_version >= 2 else None
        if head is None:
            result['missing'] = [{'code': 'binding_index_unavailable',
                'message': 'This cached observation predates the rank index; index its retained original receipt before querying best bindings.'}]
            return result
        recommendation = json.loads(head['header'])
        selected_id = recommendation['recommended_candidate_id']
        rows = self.connection.execute('SELECT proposal FROM ranked_bindings WHERE cache_id=? ORDER BY rank LIMIT ?', (cache_id, limit)).fetchall()
        candidates = [json.loads(row['proposal']) for row in rows]
        if selected_id is not None and all(row['candidate_id'] != selected_id for row in candidates):
            selected_row = self.connection.execute('SELECT proposal FROM ranked_bindings WHERE cache_id=? AND candidate_id=?', (cache_id, selected_id)).fetchone()
            if selected_row is None: _invalid('The cached recommendation refers to a missing ranked binding.')
            candidates.append(json.loads(selected_row['proposal']))
            candidates.sort(key=lambda row: row['rank'])
        from .recommendations import _tmi
        selected = None
        for candidate in candidates:
            tmi = _tmi({'tmi_candidates': candidate['binding']['tmi_candidates']}, remote_platform)
            candidate['tmi_relative_library'] = tmi
            candidate['missing'] = [row for row in candidate['missing'] if row.get('field') != 'tmi_relative_library'] + tmi['missing']
            if candidate['candidate_id'] == selected_id: selected = candidate
        if selected is not None:
            recommendation.update(missing=copy.deepcopy(selected['missing']),
                                  status='missing' if selected['missing'] else 'draft')
        recommendation.update(candidates=candidates,
            candidates_truncated=len(candidates) < recommendation['candidate_count'],
            model_ranking='ingestion_index', platform_resolution='current_supplied_remote_observation',
            complete_binding_available=bool(selected and not selected['missing']))
        result['recommendation'] = recommendation
        # Preserve the selected complete binding, core choices and original
        # source evidence; reduce only the count of optional alternatives.
        while len(_json(result).encode()) > MAX_QUERY_BYTES and len(candidates) > 1:
            removable = next((row for row in reversed(candidates) if row['candidate_id'] != selected_id), None)
            if removable is None: break
            candidates.remove(removable); recommendation['candidates_truncated'] = True
        if len(_json(result).encode()) > MAX_QUERY_BYTES:
            raise DomainError('One complete best binding exceeds the query budget; inspect the original private observation.', 'pdk_lut_query_size')
        return result


def rebind_discovery(record, requirements):
    """Pure new-source binding over an exact covered model set and saved receipt.

    The original receipt and its scope are retained in the private LUT. Only
    source-bound identities change in this independent copy; no remote content
    was reread and the original observation time is never advanced.
    """
    if not isinstance(record, dict) or record.get('schema') != SCHEMA: _invalid()
    discovery = record.get('discovery')
    profile_suggestions(discovery)
    if (not isinstance(requirements, dict)
            or requirements.get('schema') != 'spiceowl.netlist-model-requirements.v1'
            or not isinstance(requirements.get('source_sha256'), str)
            or not HASH.fullmatch(requirements['source_sha256'])): _invalid()
    models = _models(requirements.get('required_masters'))
    original_models = _models(discovery.get('required_masters'))
    coverage = record.get('coverage', {})
    covered = sorted({name for candidate in discovery.get('entry_candidates', [])
                      for name in candidate.get('master_coverage', []) if name in original_models})
    scope = {'server_identity': _identity(record.get('server_identity')),
             'roots': _roots(discovery.get('roots')), 'required_masters': original_models}
    if (models != original_models or coverage.get('required_masters') != models
            or coverage.get('covered_model_names') != covered or not set(models) <= set(covered)
            or _roots(record.get('roots')) != scope['roots'] or record.get('scope_key') != _digest(scope)
            or record.get('original_discovery_id') != discovery['discovery_id']
            or record.get('original_source_sha256') != discovery['source_sha256']
            or record.get('content_fingerprint') != content_fingerprint(discovery)):
        _invalid('Rebinding needs the exact covered model set and intact original cache evidence.')
    expected_id = _digest({'scope_key': record['scope_key'],
        'content_fingerprint': record['content_fingerprint'], 'discovery_id': discovery['discovery_id'],
        'observed_at': _timestamp(record.get('observed_at'))})
    if record.get('cache_id') != expected_id: _invalid('Cache record identity differs from its evidence.')
    rebound = copy.deepcopy(discovery)
    rebound['source_sha256'] = requirements['source_sha256']
    rebound['required_masters'] = list(requirements['required_masters'])
    hashes = {row['path']: row['sha256'] for row in discovery.get('files', [])}
    identities = {}
    for candidate in rebound.get('entry_candidates', []):
        original = candidate['candidate_id']
        try:
            candidate['candidate_id'] = _digest({'source_sha256': rebound['source_sha256'],
                'entry_path': candidate['entry_path'], 'sections': candidate['section_candidates'],
                'required_masters': rebound['required_masters'], 'dependency_scopes': candidate['dependency_scopes'],
                'files': [[path, hashes[path]] for path in candidate['dependency_paths']],
                'tmi_candidates': candidate['tmi_candidates']})
        except (KeyError, TypeError):
            _invalid('Cached candidate dependencies are incomplete.')
        identities[original] = candidate['candidate_id']
    for ambiguity in rebound.get('ambiguities', []):
        if 'candidate_ids' in ambiguity:
            try: ambiguity['candidate_ids'] = [identities[value] for value in ambiguity['candidate_ids']]
            except (KeyError, TypeError): _invalid('Cached ambiguity references an unknown candidate.')
    rebound['cache_provenance'] = {'cache_id': record['cache_id'],
        'original_discovery_id': discovery['discovery_id'], 'original_source_sha256': discovery['source_sha256'],
        'observed_at': record['observed_at'], 'content_fingerprint': record['content_fingerprint'],
        'binding': 'same_observed_model_set_new_source', 'remote_freshness_verified': False,
        'fresh_audit_required': True}
    rebound['discovery_id'] = _digest({key: value for key, value in rebound.items() if key != 'discovery_id'})
    profile_suggestions(rebound)
    return rebound


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
_LIBRARY_ROOT_INDEX = '''
    CREATE TABLE IF NOT EXISTS library_root_scopes (
        cache_id TEXT NOT NULL, scope_key TEXT NOT NULL,
        PRIMARY KEY(cache_id, scope_key)
    );
    CREATE INDEX IF NOT EXISTS library_root_scope ON library_root_scopes(scope_key, cache_id);
'''


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
        if version not in ((1, 2, 3) if read_only else (0, 1, 2, 3)):
            self.connection.close(); _invalid('Unsupported PDK cache schema version.')
        self.index_version = version
        self.library_root_index_available = False
        page_size = self.connection.execute('PRAGMA page_size').fetchone()[0]
        pages = self.connection.execute('PRAGMA page_count').fetchone()[0]
        if pages * page_size > MAX_DATABASE_BYTES:
            self.connection.close(); _invalid('The PDK cache exceeds its bounded database size.')
        if read_only:
            self.library_root_index_available = self.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='library_root_scopes'").fetchone() is not None
            return
        self.connection.execute('PRAGMA max_page_count=' + str(MAX_DATABASE_BYTES // page_size))
        if version == 3:
            with self.connection: self.connection.executescript(_LIBRARY_ROOT_INDEX)
            self.library_root_index_available = True
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
                CREATE TABLE IF NOT EXISTS library_observations (
                    cache_id TEXT PRIMARY KEY, scope_key TEXT NOT NULL,
                    library_id TEXT NOT NULL,
                    content_fingerprint TEXT NOT NULL, observed_at REAL NOT NULL,
                    metadata TEXT NOT NULL, library TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS library_scope ON library_observations(scope_key, observed_at DESC);
                CREATE TABLE IF NOT EXISTS library_entries (
                    cache_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
                    priority INTEGER NOT NULL, candidate TEXT NOT NULL,
                    PRIMARY KEY(cache_id,candidate_id)
                );
                CREATE TABLE IF NOT EXISTS library_devices (
                    cache_id TEXT NOT NULL, model_name TEXT NOT NULL, candidate_id TEXT NOT NULL,
                    model TEXT NOT NULL, declarations TEXT NOT NULL,
                    PRIMARY KEY(cache_id,model_name,candidate_id)
                );
                CREATE INDEX IF NOT EXISTS library_model_names ON library_devices(cache_id,model_name);
                PRAGMA user_version=3;
            ''')
        self.index_version = 3
        with self.connection: self.connection.executescript(_LIBRARY_ROOT_INDEX)
        self.library_root_index_available = True

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


    def put_library(self, library, *, server_identity, observed_at=None):
        """Index one explicit library attempt; never upgrade filtered evidence."""
        from .pdk_discovery import library_candidate_models, validate_pdk_library
        from .recommendations import _corner_class
        if self.read_only: _invalid('A read-only PDK cache cannot store observations.')
        validate_pdk_library(library)
        identity, roots = _identity(server_identity), _roots(library['roots'])
        observed = _timestamp(time.time() if observed_at is None else observed_at)
        scope_key = _digest({'server_identity': identity, 'roots': roots, 'mode': 'full_library'})
        files = {row['path']: row['sha256'] for row in library['files']}
        if any(not isinstance(sha, str) or not HASH.fullmatch(sha) for sha in files.values()): _invalid()
        fingerprint = _digest({'roots': roots, 'files': sorted(files.items())})
        cache_id = _digest({'scope_key': scope_key, 'library_id': library['library_id'],
                            'content_fingerprint': fingerprint, 'observed_at': observed})
        metadata = {'schema': 'spiceowl.pdk-library-cache.v1', 'cache_id': cache_id,
            'library_id': library['library_id'], 'server_identity': identity, 'roots': roots,
            'content_fingerprint': fingerprint, 'observed_at': observed,
            'coverage': library['coverage'], 'files': library['files'],
            'audit_limits': library['audit_limits'], 'search_policy': library.get('search_policy', {}),
            'search_complete': library['search_complete'], 'fresh_audit_required': True,
            'remote_freshness_verified': False, 'ready_to_run': False}
        scope_keys = {scope_key}
        if 'requested_roots' in library:
            metadata['requested_roots'] = list(library['requested_roots'])
            metadata['root_resolutions'] = [{'requested_root': logical, 'canonical_root': canonical}
                for logical, canonical in zip(library['requested_roots'], library['roots'])]
            scope_keys.add(_digest({'server_identity': identity, 'roots': _roots(library['requested_roots']), 'mode': 'full_library'}))
        # Build the declaration index once. Each active scope keeps original
        # receipt ordering, including globals from every dependency file.
        declarations = library.get('declarations', [])
        declaration_index = {}
        for index, row in enumerate(declarations):
            declaration_index.setdefault((row['path'], row['section']), []).append(index)
        active_scope_cache, model_json_cache, declaration_json_cache = {}, {}, {}
        metadata_json, library_json = _json(metadata), _json(library)
        payload_bytes = len(metadata_json) + len(library_json)

        def account_payload(*encoded):
            nonlocal payload_bytes
            # _json uses ensure_ascii=True, so character and UTF-8 byte counts
            # agree. This is only a lower bound: SQLite enforces actual pages.
            payload_bytes += sum(map(len, encoded))
            if payload_bytes > MAX_DATABASE_BYTES:
                raise DomainError('The indexed library exceeds the bounded PDK database payload; previous observations were retained.',
                                  'pdk_lut_storage')

        account_payload()
        entries, devices = [], []
        for candidate in library['entry_candidates']:
            scopes = frozenset((row['path'], row['section']) for row in candidate['dependency_scopes']) | frozenset(
                (path, None) for path in candidate['dependency_paths'])
            if scopes not in active_scope_cache:
                selected = sorted(index for scope in scopes for index in declaration_index.get(scope, ()))
                by_name = {}
                for index in selected:
                    by_name.setdefault(declarations[index]['name'], []).append(index)
                active_scope_cache[scopes] = ([declarations[index] for index in selected],
                                             {name: tuple(indices) for name, indices in by_name.items()})
            active_declarations, by_name = active_scope_cache[scopes]
            common = {key: value for key, value in candidate.items() if key != 'models'}
            common['library_declarations'] = active_declarations
            priority = (100 if candidate.get('entry_role') == 'include_entry_candidate' else 0)
            priority += {'typical': 25, 'statistical': -25, 'extreme': -10, 'unspecified': 0}[_corner_class(candidate['section_candidates'])]
            common_json = _json(common)
            account_payload(common_json)
            entries.append((cache_id, candidate['candidate_id'], priority, common_json))
            for model in library_candidate_models(library, candidate):
                key = id(model)
                if key not in model_json_cache:
                    # Retain the object along with its JSON: a resolver that
                    # creates copies cannot cause Python id reuse to mix data.
                    model_json_cache[key] = (model, _json(model))
                model_json = model_json_cache[key][1]
                selected = by_name.get(model['name'], ())
                if selected not in declaration_json_cache:
                    declaration_json_cache[selected] = _json([declarations[index] for index in selected])
                declarations_json = declaration_json_cache[selected]
                account_payload(model_json, declarations_json)
                devices.append((cache_id, model['name'], candidate['candidate_id'], model_json, declarations_json))
        try:
            with self.connection:
                self.connection.execute('INSERT OR REPLACE INTO library_observations VALUES (?,?,?,?,?,?,?)',
                    (cache_id, scope_key, library['library_id'], fingerprint, observed, metadata_json, library_json))
                self.connection.execute('DELETE FROM library_entries WHERE cache_id=?', (cache_id,))
                self.connection.execute('DELETE FROM library_devices WHERE cache_id=?', (cache_id,))
                self.connection.execute('DELETE FROM library_root_scopes WHERE cache_id=?', (cache_id,))
                self.connection.executemany('INSERT INTO library_root_scopes VALUES (?,?)',
                    [(cache_id, key) for key in sorted(scope_keys)])
                self.connection.executemany('INSERT INTO library_entries VALUES (?,?,?,?)', entries)
                self.connection.executemany('INSERT INTO library_devices VALUES (?,?,?,?,?)', devices)
        except sqlite3.DatabaseError:
            raise DomainError('The bounded PDK cache could not store this library; previous observations were retained.', 'pdk_lut_storage') from None
        return copy.deepcopy(metadata)

    def _library_lookup(self, *, server_identity, roots, library_id=None, content_fingerprint=None,
                        max_age_s=DEFAULT_MAX_AGE_S, now=None, refresh=False):
        identity, roots = _identity(server_identity), _roots(roots)
        now = _timestamp(time.time() if now is None else now); age_limit = _timestamp(max_age_s)
        if type(refresh) is not bool: _invalid()
        for value in (library_id, content_fingerprint):
            if value is not None and (not isinstance(value, str) or not HASH.fullmatch(value)): _invalid()
        result = {'schema': 'spiceowl.pdk-library-lookup.v1', 'status': 'refresh_required' if refresh else 'miss',
            'scan_required': True, 'record': None, 'missing': [], 'fresh_audit_required': True,
            'remote_freshness_verified': False, 'ready_to_run': False}
        if refresh or self.index_version < 3: return result
        key = _digest({'server_identity': identity, 'roots': roots, 'mode': 'full_library'})
        query, values = 'SELECT metadata FROM library_observations WHERE (scope_key=?', [key]
        if self.library_root_index_available:
            query += ' OR cache_id IN (SELECT cache_id FROM library_root_scopes WHERE scope_key=?)'; values.append(key)
        query += ')'
        if library_id is not None: query += ' AND library_id=?'; values.append(library_id)
        row = self.connection.execute(query + ' ORDER BY observed_at DESC,cache_id LIMIT 1', values).fetchone()
        if row is None: return result
        record = json.loads(row['metadata'])
        if library_id is None:
            collision_query = query.replace('SELECT metadata', 'SELECT COUNT(DISTINCT scope_key)', 1) + ' AND observed_at=?'
            if self.connection.execute(collision_query, [*values, record['observed_at']]).fetchone()[0] > 1:
                result['missing'] = [{'code': 'root_alias_ambiguous',
                    'message': 'Equally recent observations resolve this root scope differently; select one observation or refresh.'}]
                return result
        # Match complete root sets from this one observation only. A newer scan
        # with changed symlink targets must not fall back to older matching bytes.
        if content_fingerprint is not None and record['content_fingerprint'] != content_fingerprint: return result
        age = now - record['observed_at']
        stale = age < 0 or age > age_limit
        record['staleness'] = {'age_s': age, 'max_age_s': age_limit,
            'state': 'clock_skew' if age < 0 else 'expired' if stale else 'within_local_age_policy',
            'remote_content_current': 'unknown'}
        result.update(status='stale' if stale else 'hit', scan_required=stale, record=record)
        return result

    def query_library_devices(self, *, model_names=None, offset=0, limit=64, **scope):
        """Read a bounded device-name page; pass its library_id on continuation."""
        if type(offset) is not int or not 0 <= offset <= 4096 or type(limit) is not int or not 1 <= limit <= 256: _invalid()
        names = _models(model_names) if model_names is not None else None
        if offset and scope.get('library_id') is None: _invalid('Continue a device page with its exact observed library_id.')
        result = self._library_lookup(**scope); result.update(devices=[], total=0, next_offset=None)
        if result['status'] != 'hit': return result
        query, params = 'cache_id=?', [result['record']['cache_id']]
        if names is not None:
            query += ' AND model_name IN ('+','.join('?' for _ in names)+')'; params += names
        total = self.connection.execute('SELECT COUNT(DISTINCT model_name) FROM library_devices WHERE '+query, params).fetchone()[0]
        rows = self.connection.execute('SELECT model_name,COUNT(*) AS entry_count FROM library_devices WHERE '+query+
            ' GROUP BY model_name ORDER BY model_name LIMIT ? OFFSET ?', [*params, limit, offset]).fetchall()
        result.update(devices=[dict(row) for row in rows], total=total,
            next_offset=offset+len(rows) if offset+len(rows)<total else None)
        return result

    def lookup_library_models(self, *, required_masters, requirements=None, remote_platform=None,
                              library_candidate_id=None, **scope):
        """Combine any observed model set from one server/root library snapshot.

        Common observed entry closures produce legacy-compatible discovery.
        Disjoint entries return an explicit multi-entry draft, never a fabricated
        single entry or a ready-to-run claim. Unknown/conflicting scopes stay gaps.
        """
        names = _models(required_masters)
        if library_candidate_id is not None and (not isinstance(library_candidate_id, str)
                or not HASH.fullmatch(library_candidate_id)): _invalid()
        if requirements is not None and (not isinstance(requirements, dict)
                or requirements.get('schema') != 'spiceowl.netlist-model-requirements.v1'
                or _models(requirements.get('required_masters')) != names
                or not isinstance(requirements.get('source_sha256'), str)
                or not HASH.fullmatch(requirements['source_sha256'])): _invalid()
        result = self._library_lookup(**scope)
        result.update(discovery=None, recommendation=None, combination=None)
        if result['status'] != 'hit': return result
        record = result['record']; cache_id = record['cache_id']
        params = [cache_id, *names]; marks = ','.join('?' for _ in names)
        predicate = 'cache_id=? AND model_name IN ('+marks+')'
        if library_candidate_id is not None:
            predicate += ' AND candidate_id=?'; params.append(library_candidate_id)
        indexed = self.connection.execute('SELECT model_name,candidate_id,model,declarations FROM library_devices '
            'WHERE '+predicate+' ORDER BY candidate_id,model_name', params).fetchall()
        present = {row['model_name'] for row in indexed}
        absent = sorted(set(names)-present)
        if absent:
            result.update(status='partial' if present else 'miss', scan_required=True,
                missing=[{'field': 'required_masters', 'code': 'models_not_in_selected_entry' if library_candidate_id else 'models_not_indexed',
                          'model_names': absent, 'message': 'These models were not covered by the selected stored scope; no inferred cache hit.'}])
            return result
        by_entry = {}
        for row in indexed: by_entry.setdefault(row['candidate_id'], []).append(row)
        common_ids = [cid for cid, rows in by_entry.items() if {row['model_name'] for row in rows} == set(names)]
        selected_ids = common_ids or list(by_entry)
        raw_entries = self.connection.execute('SELECT candidate_id,candidate FROM library_entries WHERE cache_id=? AND candidate_id IN ('+
            ','.join('?' for _ in selected_ids)+') ORDER BY priority DESC,candidate_id', [cache_id, *selected_ids]).fetchall()
        entries = [json.loads(row['candidate']) for row in raw_entries]
        query_source = requirements['source_sha256'] if requirements else _digest({'library_id': record['library_id'], 'models': names})
        ordered_names = list(requirements['required_masters']) if requirements else names
        hashes = {row['path']: row['sha256'] for row in record['files']}
        candidates, declarations, declaration_keys = [], [], set()
        for entry in entries:
            # The complete namespace is needed only for cross-entry conflict
            # checks. Common-entry queries retain its immutable library origin;
            # repeating unrelated declarations here can dwarf requested evidence.
            if common_ids: entry.pop('library_declarations', None)
            original_id = entry['candidate_id']; rows = by_entry[original_id]
            entry['models'] = [json.loads(row['model']) for row in rows]
            for row in rows:
                for declaration in json.loads(row['declarations']):
                    key = _json(declaration)
                    if key not in declaration_keys: declarations.append(declaration); declaration_keys.add(key)
            visible = entry['master_coverage']
            entry['library_visible_masters'] = visible
            entry['master_coverage'] = sorted(set(visible) & set(names))
            entry['missing_masters'] = sorted(set(names) - set(visible))
            entry['coverage_complete'] = entry['coverage_complete'] and not entry['missing_masters']
            entry['library_candidate_id'] = original_id
            entry['candidate_id'] = _digest({'source_sha256': query_source, 'entry_path': entry['entry_path'],
                'sections': entry['section_candidates'], 'required_masters': ordered_names,
                'dependency_scopes': entry['dependency_scopes'],
                'files': [[path, hashes[path]] for path in entry['dependency_paths']], 'tmi_candidates': entry['tmi_candidates']})
            candidates.append(entry)
        used = {path for candidate in candidates for path in candidate['dependency_paths']}
        discovery = {'schema': 'spiceowl.pdk-discovery.v1', 'source_sha256': query_source,
            'required_masters': ordered_names, 'roots': record['roots'],
            'entry_candidates': candidates, 'declarations': declarations,
            'files': [row for row in record['files'] if row['path'] in used],
            'search_complete': record['search_complete'], 'status': 'candidates',
            'audit_limits': record['audit_limits'], 'ambiguities': [],
            'cache_provenance': {'cache_id': cache_id, 'library_id': record['library_id'],
                'observed_at': record['observed_at'], 'content_fingerprint': record['content_fingerprint'],
                'mode': 'per_device_library_query', 'coverage': record['coverage'],
                'fresh_audit_required': True, 'remote_freshness_verified': False},
            'selection_made': False, 'profile_registered': False}
        discovery['discovery_id'] = _digest(discovery)
        from .pdk_discovery import MAX_RESULT_BYTES
        if len(json.dumps(discovery, allow_nan=False).encode()) > MAX_RESULT_BYTES:
            result.update(status='partial', scan_required=False, missing=[{'code': 'binding_query_size',
                'message': 'Complete matching evidence exceeds one query page; supply an observed library_candidate_id.'}],
                entry_choices=[{'library_candidate_id': row['library_candidate_id'], 'entry_path': row['entry_path'],
                    'section_candidates': row['section_candidates'], 'covered_model_count': len(row['master_coverage'])}
                    for row in candidates])
            if len(_json(result).encode()) > MAX_QUERY_BYTES:
                raise DomainError('Library entry choices exceed the query budget; inspect the original observation.', 'pdk_lut_query_size')
            return result
        from .recommendations import recommend_model_bindings
        recommendation = recommend_model_bindings(discovery, remote_platform=remote_platform)
        result['recommendation'] = recommendation
        if common_ids:
            result['discovery'] = discovery if requirements else None
            result['scan_required'] = False
            result['missing'] = copy.deepcopy(recommendation['missing'])
        else:
            result.update(status='partial', scan_required=False, combination=_combine_library_entries(candidates, names, declarations, remote_platform),
                          missing=[{'field': 'entries', 'code': 'multi_entry_selection_required',
                                    'message': 'No observed single entry covers this model set; inspect the explicit combination draft.'}])
        if requirements is None:
            result['recommendation'].pop('source_sha256', None)
        if len(_json(result).encode()) > MAX_QUERY_BYTES:
            raise DomainError('Complete library binding response exceeds its bounded query budget.', 'pdk_lut_query_size')
        return result


def _combine_library_entries(candidates, names, declarations, remote_platform):
    """Finite set cover as a draft; retain cross-entry scope conflicts explicitly."""
    from .recommendations import _corner_class, _sections, _tmi
    remaining, chosen = set(names), []
    available = sorted(candidates, key=lambda row: (-int(row['entry_role'] == 'include_entry_candidate'),
        -int(_corner_class(row['section_candidates']) == 'typical'), row['entry_path'], row['candidate_id']))
    while remaining and len(chosen) < 8:
        ranked = sorted(available, key=lambda row: -len(remaining & set(row['master_coverage'])))
        if not ranked or not remaining & set(ranked[0]['master_coverage']): break
        selected = ranked[0]; chosen.append(selected); remaining -= set(selected['master_coverage']); available.remove(selected)
    gaps = [{'code': 'combined_dependency_audit_required',
             'message': 'A combined include closure has not been audited; this is a multi-entry draft only.'}]
    corners = {_corner_class(row['section_candidates']) for row in chosen}
    if len(corners - {'unspecified'}) > 1 or ('extreme' in corners and len({tuple(row['section_candidates']) for row in chosen}) > 1):
        gaps.append({'code': 'entry_corner_conflict', 'message': 'Selected entries expose different corner conditions.'})
    selected_scopes = {(scope['path'], scope['section']) for row in chosen for scope in row['dependency_scopes']}
    physical = {}
    declarations = [item for row in chosen for item in row.get('library_declarations', declarations)]
    for declaration in declarations:
        if (declaration['path'], declaration['section']) not in selected_scopes and not (declaration['section'] is None and any(path == declaration['path'] for path, _ in selected_scopes)): continue
        physical.setdefault(declaration['name'], set()).add((declaration['path'], declaration['line'], declaration['section']))
    duplicate = sorted(name for name, locations in physical.items() if len(locations) > 1)
    if duplicate: gaps.append({'code': 'duplicate_combined_model_declarations', 'model_names': duplicate})
    selected_tmi = {}
    for row in chosen:
        if row['unknown_codes']: gaps.append({'code': 'entry_scope_unknown', 'candidate_id': row['candidate_id'], 'unknown_codes': row['unknown_codes']})
        row['suggested_sections'] = _sections(row)
        row['tmi_selection'] = _tmi(row, remote_platform)
        gaps.extend(row['tmi_selection']['missing'])
        for group in row['tmi_candidates']:
            for option in group.get('platform_candidates', []):
                if option['relative_library'] == row['tmi_selection']['value']:
                    selected_tmi.setdefault(option['relative_library'], set()).add(option['sha256'])
    if any(len(hashes) > 1 for hashes in selected_tmi.values()):
        gaps.append({'code': 'combined_tmi_fingerprint_conflict', 'message': 'Entries select different bytes for the same TMI library label.'})
    if remaining: gaps.append({'code': 'combination_entry_limit', 'model_names': sorted(remaining)})
    return {'schema': 'spiceowl.pdk-multi-entry-draft.v1', 'entries': chosen,
        'covered_model_names': sorted(set(names)-remaining), 'missing': gaps,
        'combination_verified': False, 'requires_explicit_selection': True,
        'fresh_audit_required': True, 'ready_to_run': False}


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


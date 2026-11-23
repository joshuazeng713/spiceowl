"""Opt-in bounded synthetic full-library benchmark; no network or timing assertion."""
import argparse
import json
import platform
from pathlib import Path
import sqlite3
import tempfile
import time

from spiceowl.domain.common import DomainError
from spiceowl.onboarding.pdk_lut import PdkLut
from tests.benchmark_pdk_lut import measurements
from tests.test_pdk_library import IDENTITY, library_scan, requirements


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--iterations', type=int, default=100)
    parser.add_argument('--devices', type=int, default=160)
    parser.add_argument('--entries', type=int, default=32)
    options = parser.parse_args()
    if not 10 <= options.iterations <= 10000: parser.error('iterations must be 10–10000')
    if not 2 <= options.devices <= 4096: parser.error('devices must be 2–4096')
    if not 1 <= options.entries <= 256: parser.error('entries must be 1–256')
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        models = root / 'models'; models.mkdir()
        names = ['synthetic_%04d' % index for index in range(options.devices)]
        (models / 'devices.scs').write_text('simulator lang=spectre\nsection models\n' +
            ''.join('model %s bsim4 type=%s\n' % (name, 'p' if index % 2 else 'n')
                    for index, name in enumerate(names)) + 'endsection models\n')
        sections = ['TTMacro'] + ['Corner%03d' % index for index in range(options.entries - 1)]
        (models / 'usage.scs').write_text('section pre_simu\nendsection pre_simu\n' + ''.join(
            'section '+section+'\ninclude "devices.scs" section=models\nendsection '+section+'\n'
            for section in sections))
        start = time.perf_counter_ns(); library = library_scan(models)
        scan_ms = (time.perf_counter_ns() - start) / 1e6
        database = root / 'cache.sqlite3'
        scope = {'server_identity': IDENTITY, 'roots': [str(models)], 'now': 1001}
        results = {}
        selected_entry = next((row['candidate_id'] for row in library['entry_candidates']
            if row['section_candidates'] == ['TTMacro']), library['entry_candidates'][0]['candidate_id'])
        with PdkLut(database) as cache:
            start = time.perf_counter_ns()
            cache.put_library(library, server_identity=IDENTITY, observed_at=1000)
            ingest_ms = (time.perf_counter_ns() - start) / 1e6
            results['device_page_first'] = measurements(lambda: cache.query_library_devices(**scope), 1)
            results['device_page_hot'] = measurements(lambda: cache.query_library_devices(**scope), options.iterations)
            for size in sorted({1, 2, min(16, len(names)), min(128, len(names))}):
                requested = names[-size:]
                args = dict(scope, required_masters=requested, requirements=requirements(requested))
                try:
                    start = time.perf_counter_ns()
                    first = cache.lookup_library_models(**args)
                    first_ms = round((time.perf_counter_ns() - start) / 1e6, 3)
                except DomainError as error:
                    results['binding_%d_models' % size] = {'bounded_error': error.code}
                    continue
                assert not first['ready_to_run']
                if first['status'] == 'hit':
                    assert first['discovery']['required_masters'] == requested
                    selected_id = first['recommendation']['recommended_candidate_id']
                    if selected_id is not None:
                        assert not next(row['missing'] for row in first['recommendation']['candidates']
                                        if row['candidate_id'] == selected_id)
                    else:
                        assert first['recommendation']['missing']
                results['binding_%d_models' % size] = {
                    'status': first['status'], 'missing_codes': [row['code'] for row in first['missing']],
                    'first': {'samples': 1, 'median_ms': first_ms, 'p95_ms': first_ms, 'max_ms': first_ms},
                    'hot': measurements(lambda: cache.lookup_library_models(**args), options.iterations),
                    'result_bytes': len(json.dumps(first).encode()),
                }
                narrowed = dict(args, library_candidate_id=selected_entry)
                selected_result = cache.lookup_library_models(**narrowed)
                assert selected_result['status'] == 'hit'
                assert selected_result['discovery']['required_masters'] == requested
                assert len(selected_result['discovery']['entry_candidates']) == 1
                results['selected_entry_binding_%d_models' % size] = {
                    'status': selected_result['status'],
                    'hot': measurements(lambda: cache.lookup_library_models(**narrowed), options.iterations),
                    'result_bytes': len(json.dumps(selected_result).encode()),
                }
        def cold_device_page():
            with PdkLut(database, read_only=True) as cache: cache.query_library_devices(**scope)
        def cold_single_binding():
            with PdkLut(database, read_only=True) as cache:
                cache.lookup_library_models(**scope, required_masters=[names[-1]], requirements=requirements([names[-1]]))
        results['device_page_cold_connection'] = measurements(cold_device_page, max(10, options.iterations // 4))
        results['single_binding_cold_connection'] = measurements(cold_single_binding, max(10, options.iterations // 4))
        print(json.dumps({
            'environment': {'python': platform.python_version(), 'system': platform.system(),
                'machine': platform.machine(), 'sqlite': sqlite3.sqlite_version},
            'synthetic': True, 'network_calls': 0, 'compiler_or_solver_calls': 0,
            'scan_ms': round(scan_ms, 3), 'ingest_ms': round(ingest_ms, 3),
            'requested_device_count': len(names), 'indexed_device_count': library['coverage']['indexed_model_count'],
            'candidate_count': len(library['entry_candidates']),
            'indexed_device_entry_count': sum(len(row['models']) for row in library['entry_candidates']),
            'library_status': library['status'], 'library_bytes': len(json.dumps(library).encode()),
            'database_bytes': database.stat().st_size, 'queries': results,
            'cold_definition': 'new read-only SQLite connection; OS filesystem caches are not purged',
            'timing_is_environment_specific': True,
        }, indent=2))


if __name__ == '__main__': main()

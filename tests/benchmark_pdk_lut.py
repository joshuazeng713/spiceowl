"""Opt-in local benchmark; no network, real PDK or timing assertion in CI."""
import argparse
import json
import platform
from pathlib import Path
import statistics
import tempfile
import time

from spiceowl.onboarding.pdk_lut import PdkLut
from spiceowl.onboarding.pdk_discovery import inspect_netlist
from tests.test_pdk_lut import IDENTITY, MODELS, SOURCE, scan


def measurements(operation, count):
    times = []
    for _ in range(count):
        start = time.perf_counter_ns(); operation()
        times.append((time.perf_counter_ns() - start) / 1e6)
    values = sorted(times)
    return {'samples': count, 'median_ms': round(statistics.median(times), 3),
            'p95_ms': round(values[min(len(values) - 1, int(len(values) * .95))], 3),
            'max_ms': round(max(times), 3)}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--iterations', type=int, default=100)
    count = parser.parse_args().iterations
    if not 10 <= count <= 10000: parser.error('iterations must be between 10 and 10000')
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve(); models = root / 'models'; models.mkdir()
        (models / 'devices.scs').write_text('simulator lang=spectre\n' + MODELS)
        sections = ['TTMacro'] + ['C%03d' % n for n in range(179)]
        (models / 'usage.scs').write_text('section pre_simu\nendsection pre_simu\n' + ''.join(
            'section '+section+'\ninclude "devices.scs" section=models\nendsection '+section+'\n' for section in sections))
        requirements = inspect_netlist(SOURCE); discovery = scan(models, requirements)
        path = root / 'cache.sqlite3'
        args = {'server_identity': IDENTITY, 'roots': [str(models)],
                'required_masters': requirements['required_masters'], 'now': 1001}
        with PdkLut(path) as cache:
            start = time.perf_counter_ns()
            cache.put(discovery, server_identity=IDENTITY, observed_at=1000)
            ingest_ms = (time.perf_counter_ns() - start) / 1e6
            assert cache.query_best_bindings(**args)['recommendation']['complete_binding_available']
            first = measurements(lambda: cache.query_bindings(**args, model_names=['n_core_ulvt']), 1)
            warm = measurements(lambda: cache.query_bindings(**args, model_names=['n_core_ulvt']), count)
            best_first = measurements(lambda: cache.query_best_bindings(**args), 1)
            best_hot = measurements(lambda: cache.query_best_bindings(**args), count)
            full = measurements(lambda: cache.lookup(**args), count)
        def reopen():
            with PdkLut(path, read_only=True) as cache:
                cache.query_bindings(**args, model_names=['n_core_ulvt'])
        cold = measurements(reopen, max(10, count // 4))
        def best_reopen():
            with PdkLut(path, read_only=True) as cache:
                cache.query_best_bindings(**args)
        best_cold = measurements(best_reopen, max(10, count // 4))
        print(json.dumps({'environment': {'python': platform.python_version(),
            'system': platform.system(), 'machine': platform.machine(), 'sqlite': __import__('sqlite3').sqlite_version},
            'synthetic': True, 'candidate_count': len(discovery['entry_candidates']),
            'model_scope_count': len(requirements['required_masters']),
            'ingest_ms': round(ingest_ms, 3), 'first_index_query': first,
            'hot_index_query': warm, 'full_receipt_lookup': full, 'cold_connection_query': cold,
            'first_best_binding_query': best_first, 'hot_best_binding_query': best_hot,
            'cold_best_binding_query': best_cold,
            'cold_definition': 'new read-only SQLite connection; OS filesystem caches are not purged',
            'network_calls': 0, 'target_ms': 10, 'timing_is_environment_specific': True}, indent=2))


if __name__ == '__main__': main()

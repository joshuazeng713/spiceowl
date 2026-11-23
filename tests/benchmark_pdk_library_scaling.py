"""Compare bounded scans on original multi-corner fixtures; no remote access.

Optionally load only the scanner builder from a local Git revision. Neither a
real PDK nor historical external dependencies are downloaded by this benchmark.
Elapsed times are measurements, not assertions or a real-site performance claim.
"""
import argparse
import contextlib
import io
import json
import platform
from pathlib import Path
import re
import subprocess
import tempfile
import time

from spiceowl.onboarding.pdk_discovery import build_pdk_library_script


def fixture(root, *, corners, devices, separate_definitions):
    names = ['synthetic_%03d' % index for index in range(devices)]
    scopes = ['Corner%03d' % index for index in range(corners)]
    defined = scopes if separate_definitions else ['Devices']
    (root / 'a_definitions.scs').write_text('simulator lang=spectre\n' + ''.join(
        'section ' + scope + '\n' + ''.join(
            'model %s bsim4 type=%s\n' % (name, 'p' if number % 2 else 'n')
            for number, name in enumerate(names)) + 'endsection ' + scope + '\n'
        for scope in defined))
    (root / 'z_entry.scs').write_text(''.join(
        'section ' + scope + '\ninclude "a_definitions.scs" section=' +
        (scope if separate_definitions else 'Devices') + '\nendsection ' + scope + '\n'
        for scope in scopes))


def scan(builder, root, iterations):
    script = builder(roots=[str(root)])
    elapsed = []
    for _ in range(iterations):
        output = io.StringIO()
        started = time.perf_counter()
        with contextlib.redirect_stdout(output):
            exec(script, {})
        elapsed.append((time.perf_counter() - started) * 1000)
        raw = output.getvalue()
        result = json.loads(raw)
    candidates = result['entry_candidates']
    return {
        'scan_ms': [round(value, 3) for value in elapsed],
        'receipt_bytes': len(raw.encode()),
        'status': result['status'],
        'complete_within_policy': result['coverage']['complete_within_policy'],
        'entry_candidates': len(candidates),
        'include_entries': sum(row['entry_role'] == 'include_entry_candidate' for row in candidates),
        'definition_entries': sum(row['entry_role'] == 'definition_only' for row in candidates),
        'bindings': sum(len(row['models']) for row in candidates),
        'shared_contracts': len(result.get('model_contracts', {})),
        'unknown_codes': sorted({row['code'] for row in result['unknown']}),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--corners', type=int, default=48)
    parser.add_argument('--devices', type=int, default=200)
    parser.add_argument('--iterations', type=int, default=3)
    parser.add_argument('--baseline-commit')
    args = parser.parse_args()
    if not 1 <= args.corners <= 128 or not 1 <= args.devices <= 256 or not 1 <= args.iterations <= 10:
        parser.error('Use 1..128 corners, 1..256 devices and 1..10 iterations.')
    builders = {'current': build_pdk_library_script}
    if args.baseline_commit:
        if not re.fullmatch(r'[a-f0-9]{40}', args.baseline_commit):
            parser.error('Supply the exact full lowercase SHA of a locally available baseline commit.')
        source = subprocess.check_output([
            'git', 'show', args.baseline_commit + ':src/spiceowl/onboarding/pdk_discovery.py'], text=True)
        namespace = {'__name__': 'spiceowl_benchmark_baseline'}
        exec(compile(source, '<local-baseline-pdk-discovery>', 'exec'), namespace)
        builders['baseline'] = namespace['build_pdk_library_script']
    cases = {}
    with tempfile.TemporaryDirectory(prefix='spiceowl-synthetic-library-') as temporary:
        for separate, name in ((False, 'shared_definitions'), (True, 'separate_corner_definitions')):
            root = Path(temporary).resolve() / name
            root.mkdir()
            fixture(root, corners=args.corners, devices=args.devices, separate_definitions=separate)
            cases[name] = {label: scan(builder, root, args.iterations) for label, builder in builders.items()}
    print(json.dumps({
        'synthetic': True, 'network_calls': 0, 'solver_calls': 0,
        'environment': {'python': platform.python_version(), 'system': platform.system(),
                        'machine': platform.machine()},
        'baseline_commit': args.baseline_commit,
        'baseline_parser': 'current shared model_dependencies parser; only discovery builder loaded from local Git',
        'corners': args.corners, 'devices': args.devices, 'cases': cases,
        'timing_is_environment_specific': True,
    }, indent=2))


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Read-only checks for the pinned developer profile; no servers or remote jobs."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


def pins():
    result = {}
    for file in ('dev-tools.txt', 'dev-lock.txt'):
        for line in (ROOT / 'requirements' / file).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#'): continue
            match = re.fullmatch(r'([A-Za-z0-9_-]+)==([A-Za-z0-9_.+]+)', line)
            if not match: raise ValueError('Invalid exact dependency pin: ' + line)
            name, version = match.groups()
            if name in result: raise ValueError('Duplicate dependency pin: ' + name)
            result[name] = version
    return result


def check():
    if not (3, 12) <= sys.version_info[:2] <= (3, 13):
        raise ValueError('The locked developer profile uses Python 3.12 or 3.13.')
    actual = {}
    for name, expected in pins().items():
        actual[name] = importlib.metadata.version(name)
        if actual[name] != expected:
            raise ValueError('%s: expected %s, installed %s' % (name, expected, actual[name]))
    from importlib.resources import files
    from mcp.server.fastmcp import FastMCP
    import numpy as np
    import scipy
    import spiceowl
    from spiceowl.adctools import sine_fit3
    if not Path(spiceowl.__file__).resolve().is_relative_to(ROOT / 'src'):
        raise ValueError('SpiceOwl is not the editable source in this checkout.')
    assets = files('spiceowl.web').joinpath('static')
    if not all(assets.joinpath(n).is_file() for n in ('index.html', 'app.js', 'app.css', 'owl.svg')):
        raise ValueError('Packaged browser assets are missing.')
    t = np.arange(128) / 128
    samples = .5 * np.sin(2 * np.pi * 7 * t + .2) + .1
    for backend in ('spiceowl', 'adctoolbox'):
        report = sine_fit3(samples, backend=backend, sample_rate_hz=128,
                           frequency_hz=7, signal_unit='V')
        if (report['status'] != 'valid'
                or abs(report['metrics']['amplitude_peak']['value'] - .5) > 1e-10):
            raise ValueError('Numerical self-check failed for ' + backend)
    digest = hashlib.sha256()
    for name in ('dev-tools.txt', 'dev-lock.txt'):
        digest.update((ROOT / 'requirements' / name).read_bytes())
    return {'schema': 'spiceowl.dev-environment.v1', 'status': 'ready',
            'python': platform.python_version(), 'platform': platform.platform(),
            'executable': sys.executable, 'dependency_lock_sha256': digest.hexdigest(),
            'packages': actual, 'editable_source': str(ROOT),
            'checks': ['exact_versions', 'editable_source', 'mcp_import', 'web_assets',
                       'local_sine_fit', 'actual_adctoolbox_sine_fit'],
            'ssh_calls': 0, 'model_calls': 0, 'solver_invocations': 0}


if __name__ == '__main__':
    try:
        result = check()
        if sys.argv[1:]:
            if len(sys.argv) != 3 or sys.argv[1] != '--receipt':
                raise ValueError('Only --receipt PATH is supported.')
            path = Path(sys.argv[2])
            if path.is_symlink(): raise ValueError('Receipt path cannot be a symlink.')
            path.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2))
    except Exception as error:
        print('Environment check failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)

#!/usr/bin/env python3
"""One-command, project-local developer setup; initial Python 3.9+ is sufficient."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import venv

ROOT = Path(__file__).resolve().parents[1]
UV_VERSION = '0.9.5'
MANAGED_PYTHON = '3.12.12'


def is_windows():
    return sys.platform == 'win32' or os.name == 'nt'


def venv_exe(venv_dir, name):
    if is_windows():
        filename = name if name.lower().endswith('.exe') else name + '.exe'
        return Path(venv_dir) / 'Scripts' / filename
    return Path(venv_dir) / 'bin' / name


def run(command, **kwargs):
    return subprocess.run([str(part) for part in command], check=True, cwd=ROOT, **kwargs)


def python_version(path):
    try:
        result = subprocess.run([str(path), '-c', 'import json,sys;print(json.dumps(list(sys.version_info[:2])))'],
                                check=True, text=True, capture_output=True, timeout=10)
        return tuple(json.loads(result.stdout))
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def supported(path):
    version = python_version(path)
    return version is not None and (3, 12) <= version <= (3, 13)


def inspect_target(path):
    if path.is_symlink(): raise ValueError('Virtual environment directory cannot be a symlink.')
    if not path.exists(): return None
    if not path.is_dir() or not (path / 'pyvenv.cfg').is_file():
        raise ValueError('Existing target is not a virtual environment; choose another --venv path.')
    python = venv_exe(path, 'python')
    if not supported(python):
        raise ValueError('Existing environment needs Python 3.12/3.13; it was preserved. Choose another --venv path.')
    return python


def download_python():
    tools = ROOT / '.dev-tools'
    if tools.is_symlink(): raise ValueError('Developer tools directory cannot be a symlink.')
    tools.mkdir(exist_ok=True)
    install = tools / 'python'
    if install.is_symlink(): raise ValueError('Managed Python directory cannot be a symlink.')
    environment = dict(os.environ, UV_PYTHON_INSTALL_DIR=str(install),
                       UV_PYTHON_BIN_DIR=str(tools / 'bin'))
    with tempfile.TemporaryDirectory(prefix='spiceowl-python-bootstrap-') as temp:
        bootstrap = Path(temp) / 'venv'
        venv.EnvBuilder(with_pip=True).create(bootstrap)
        executable = venv_exe(bootstrap, 'python')
        run([executable, '-m', 'pip', '--disable-pip-version-check', 'install', 'uv==' + UV_VERSION])
        uv = venv_exe(bootstrap, 'uv')
        run([uv, 'python', 'install', MANAGED_PYTHON], env=environment)
        found = run([uv, 'python', 'find', '--managed-python', MANAGED_PYTHON],
                    env=environment, text=True, capture_output=True).stdout.strip()
    path = Path(found)
    if not path.resolve().is_relative_to(install.resolve()) or not supported(path):
        raise ValueError('Downloaded interpreter failed its path/version check.')
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--venv', type=Path, default=ROOT / '.venv')
    parser.add_argument('--python', type=Path, help='Explicit Python 3.12/3.13 interpreter')
    parser.add_argument('--no-python-download', action='store_true')
    parser.add_argument('--check-only', action='store_true', help='No installation or download')
    parser.add_argument('--serve-demo', action='store_true', help='Start the synthetic console after checks')
    args = parser.parse_args(argv)
    if sys.version_info < (3, 9): raise ValueError('Start this script with Python 3.9 or later.')
    if sys.platform not in ('darwin', 'linux', 'win32'):
        raise ValueError('This developer profile supports macOS, Linux, and Windows.')
    target = args.venv.expanduser().absolute()
    executable = inspect_target(target)
    if args.check_only:
        if executable is None: raise ValueError('Environment does not exist; run setup without --check-only.')
        run([executable, '-m', 'pip', 'check'])
        run([executable, ROOT / 'scripts/check_dev_env.py'])
        return 0
    if executable is None:
        if args.python:
            selected = args.python.expanduser().absolute()
            if not supported(selected): raise ValueError('--python must be a working Python 3.12/3.13.')
        else:
            candidates = [sys.executable, venv_exe(ROOT / '.venv', 'python')]
            names = ('python3.12', 'python3.13', 'python') if is_windows() else ('python3.12', 'python3.13')
            candidates += [shutil.which(name) for name in names]
            selected = next((p for p in candidates if p and supported(p)), None)
            if selected is None:
                if args.no_python_download:
                    raise ValueError('No Python 3.12/3.13 found. Supply --python or allow its project-local download.')
                selected = download_python()
        run([selected, '-m', 'venv', target])
        executable = venv_exe(target, 'python')
    pip = [executable, '-m', 'pip', '--disable-pip-version-check']
    run(pip + ['install', '-r', ROOT / 'requirements/dev-tools.txt'])
    run(pip + ['install', '-r', ROOT / 'requirements/dev-lock.txt'])
    run(pip + ['install', '--no-deps', '--no-build-isolation', '-e', str(ROOT) + '[mcp,adctools]'])
    run(pip + ['check'])
    run([executable, ROOT / 'scripts/check_dev_env.py', '--receipt', target / 'spiceowl-dev-environment.json'])
    spiceowl_bin = venv_exe(target, 'spiceowl')
    adctools_bin = venv_exe(target, 'spiceowl-adctools')
    run([spiceowl_bin, '--version'])
    run([adctools_bin, '--help'], stdout=subprocess.DEVNULL)
    print('\nReady. Start the synthetic console with:\n%s serve --demo' % spiceowl_bin)
    if args.serve_demo: run([spiceowl_bin, 'serve', '--demo'])
    return 0


if __name__ == '__main__':
    try: raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print('Setup stopped: ' + str(error), file=sys.stderr)
        raise SystemExit(1)

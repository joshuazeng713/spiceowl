"""Authenticate an operator-granted local simulation caller; no approval policy."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import time

from spiceowl._platform import is_owner_private_file

CALLERS = {'paired-helper', 'native-agent', 'spiceowl-local'}


def load_key(path):
    if not isinstance(path, (str, Path)) or not Path(path).is_absolute():
        raise PermissionError('A private operator authority key is required')
    value = Path(path)
    metadata = value.lstat()
    if not is_owner_private_file(metadata, allow_root=True):
        raise PermissionError('Simulation authority key must be an owner-private regular file')
    raw = value.read_bytes()
    if not 32 <= len(raw) <= 128:
        raise PermissionError('Simulation authority key has an invalid size')
    return raw


def _message(caller, method, path, raw_bytes, timestamp):
    if caller not in CALLERS or method not in ('GET', 'POST') or not isinstance(path, str) or not path.startswith('/api/simulation/') or len(path) > 2048 or not isinstance(raw_bytes, bytes) or len(raw_bytes) > 2 * 1024**2:
        raise PermissionError('Invalid simulation authority binding')
    return json.dumps([caller, method, path, hashlib.sha256(raw_bytes).hexdigest(), timestamp], separators=(',', ':')).encode()


def sign(key, caller, method, path, raw_bytes, timestamp=None):
    ts = str(int(time.time()) if timestamp is None else int(timestamp))
    proof = hmac.new(key, _message(caller, method, path, raw_bytes, ts), hashlib.sha256).hexdigest()
    return ts + '.' + proof


def verify(key, caller, method, path, raw_bytes, authorization, *, now=None):
    if not isinstance(authorization, str) or not re.fullmatch(r'[0-9]{1,12}\.[a-f0-9]{64}', authorization):
        raise PermissionError('Missing or invalid simulation caller authorization')
    ts, _ = authorization.split('.')
    if abs((time.time() if now is None else now) - int(ts)) > 120:
        raise PermissionError('Expired simulation caller authorization')
    expected = sign(key, caller, method, path, raw_bytes, timestamp=int(ts))
    if not hmac.compare_digest(expected, authorization):
        raise PermissionError('Simulation caller does not match the signed request')
    return caller

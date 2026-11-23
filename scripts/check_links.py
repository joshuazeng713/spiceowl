#!/usr/bin/env python3
"""Inventory repository links, verify local targets and optionally check public HTTP URLs."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import html
import ipaddress
import json
from pathlib import Path
import re
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
URL = re.compile(r'https?://[^\s<>"\x27`\\]+')
MARKDOWN = re.compile(r'\]\(<?([^\s)>]+)>?(?:\s+["\x27][^)]*)?\)')
HTML = re.compile(r'\b(?:href|src)=["\x27]([^"\x27]+)["\x27]')


def files(root):
    result = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=root)
    return sorted(set(name.decode() for name in result.split(b'\0') if name))


def anchors(path):
    text = path.read_text(encoding='utf-8')
    text = re.sub(r'```[\s\S]*?```|~~~[\s\S]*?~~~', '', text)
    ids = set(re.findall(r'\b(?:id|name)=["\x27]([^"\x27]+)', text))
    counts = {}
    for heading in re.findall(r'^\s{0,3}#{1,6}\s+(.+?)\s*#*$', text, re.M):
        heading = re.sub(r'<[^>]*>', '', heading).strip().lower()
        heading = re.sub(r'[^\w\-\s]', '', heading).replace(' ', '-')
        count = counts.get(heading, 0); counts[heading] = count + 1
        ids.add(heading + ('-' + str(count) if count else ''))
    return ids


def local_result(root, source, target):
    target = html.unescape(target)
    split = urllib.parse.urlsplit(target)
    if split.scheme or split.netloc: return None
    if target.startswith(('#', '?')): return None
    path = urllib.parse.unquote(split.path)
    if not path: return None
    if path.startswith('/'):
        # The local console serves these assets at its HTTP root.
        if str(source).startswith('src/spiceowl/web/static/'):
            resolved = root / 'src/spiceowl/web/static' / path.lstrip('/')
        else: return {'status': 'unverified', 'reason': 'runtime_absolute_route'}
    else: resolved = (root / source).parent / path
    if not resolved.exists(): return {'status': 'broken', 'reason': 'missing_relative_target'}
    if split.fragment and resolved.is_file() and resolved.suffix in ('.md', '.html'):
        if urllib.parse.unquote(split.fragment) not in anchors(resolved):
            return {'status': 'broken', 'reason': 'missing_local_anchor'}
    return {'status': 'valid'}


def public_reason(url):
    try: value = urllib.parse.urlsplit(url)
    except ValueError: return 'invalid_url'
    host = value.hostname or ''
    if value.username or value.password: return 'credential_or_test_url'
    if any(token in url for token in ('{', '}', '$', '*', '[', ']')): return 'template_url'
    if host in ('localhost', 'example.com', 'example.org', 'example.net') or host.endswith(('.invalid', '.test', '.example', '.local')):
        return 'local_or_example_url'
    try:
        if not ipaddress.ip_address(host).is_global: return 'local_or_example_url'
    except ValueError: pass
    if not host or '.' not in host: return 'local_or_example_url'
    if host == 'github.com' and value.path.startswith('/owner/'): return 'example_url'
    if host.startswith('api.'): return 'api_endpoint_not_browser_link'
    if any(word in value.query.lower() for word in ('secret', 'token=', 'key=')): return 'credential_or_test_url'
    return None


def fetch(url, timeout):
    reason = public_reason(url)
    if reason: return {'url': url, 'status': 'skipped', 'reason': reason}
    request = urllib.request.Request(url, headers={'User-Agent': 'SpiceOwl-LinkCheck/1.0', 'Range': 'bytes=0-65535'})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
            response.read(1024)
            return {'url': url, 'status': 'valid', 'http_status': response.status, 'final_url': response.url}
    except urllib.error.HTTPError as error:
        status = 'broken' if error.code in (404, 410) else 'unverified'
        return {'url': url, 'status': status, 'http_status': error.code,
                'reason': 'not_found' if status == 'broken' else 'http_access_or_service_error'}
    except (urllib.error.URLError, OSError, ValueError) as error:
        return {'url': url, 'status': 'unverified', 'reason': type(error).__name__}


def check(root=ROOT, external=False, timeout=12):
    relative = []; locations = {}; legacy = []
    for name in files(root):
        path = root / name
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024: continue
        try: text = path.read_text(encoding='utf-8')
        except UnicodeError: continue
        for match in URL.finditer(text):
            url = html.unescape(match.group()).rstrip('.,;:)]，。；：！？）》】')
            url = url.split('#', 1)[0]
            locations.setdefault(url, set()).add(name)
            if ('joshuazeng713' + '.github.io') in url or ('github.com/' + 'joshuazeng713/') in url:
                legacy.append({'source': name, 'url': url})
        if path.suffix in ('.md', '.html'):
            targets = set(MARKDOWN.findall(text)) | set(HTML.findall(text))
            for target in sorted(targets):
                result = local_result(root, name, target)
                if result: relative.append({'source': name, 'target': target, **result})
    urls = sorted(locations)
    if external:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda url: fetch(url, timeout), urls))
    else: results = [{'url': url, 'status': 'not_checked'} for url in urls]
    for row in results: row['sources'] = sorted(locations[row['url']])
    counts = lambda rows: {state: sum(row['status'] == state for row in rows)
                          for state in ('valid', 'broken', 'unverified', 'skipped', 'not_checked')}
    return {'schema': 'spiceowl.link-check.v1', 'checked_at': datetime.now(timezone.utc).isoformat(),
            'external_requests_enabled': external, 'legacy_links': legacy,
            'summary': {'relative': counts(relative), 'external': counts(results)},
            'relative': relative, 'external': results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--external', action='store_true')
    parser.add_argument('--timeout', type=float, default=12)
    parser.add_argument('--output', type=Path, default=ROOT / '.spiceowl/link-check.json')
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 60: parser.error('--timeout must be between 1 and 60 seconds')
    report = check(external=args.external, timeout=args.timeout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'summary': report['summary'], 'legacy_count': len(report['legacy_links']),
                      'report': str(args.output)}, indent=2))
    return int(bool(report['legacy_links'] or any(row['status'] == 'broken' for row in report['relative'] + report['external'])))


if __name__ == '__main__': raise SystemExit(main())

#!/usr/bin/env python3
"""Verify this round's sanitized allowlist exports; never connects to a device.

README.md, onmyoji/ and supplemental root summaries/verifier are maintained
under publication-manifest.json and are outside this topic manifest. Historical SHA values inside evidence describe original local files;
only package-manifest.json export_sha256 values describe the exported bytes.
"""
from pathlib import Path
import hashlib
import json
import re
import sys

ROOT = Path(__file__).resolve().parent
ALLOWED_TOPICS = {
    'random-memory', 'random-smoke', 'random-memory-report',
    'headless-game-soak', 'headless-game-execution',
    'headless-network-preflight', 'game-observer', 'maa-reviews',
    'nonempty-file', 'reviews',
}
ALLOWED_EXTENSIONS = {'.py', '.c', '.ini', '.json', '.jsonl', '.csv', '.log', '.md', '.svg', '.png'}
FORBIDDEN_TEXT = [
    re.compile(rb'/' + rb'Users/[^/\s<>"\x27]+'),
    re.compile(rb'[A-Za-z0-9_-]+MacBook[A-Za-z0-9_-]*(?:\.local)?', re.I),
    re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    re.compile(rb'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b'),
    re.compile(rb'(?im)^(?:Authorization|Cookie|Set-Cookie):\s*\S+'),
]


def main():
    manifest = json.loads((ROOT / 'package-manifest.json').read_text())
    errors, seen, total = [], set(), 0
    for entry in manifest['files']:
        relative = Path(entry['export'])
        path = ROOT / relative
        if relative.is_absolute() or '..' in relative.parts or relative.as_posix() in seen:
            errors.append(f'Invalid/duplicate path: {relative}')
            continue
        seen.add(relative.as_posix())
        if relative.name != 'verify_public.py' and relative.parts[0] not in ALLOWED_TOPICS:
            errors.append(f'Outside topic allowlist: {relative}')
        if path.suffix not in ALLOWED_EXTENSIONS or path.is_symlink() or not path.is_file():
            errors.append(f'Unexpected type or missing file: {relative}')
            continue
        raw = path.read_bytes()
        total += len(raw)
        if len(raw) != entry['export_bytes'] or hashlib.sha256(raw).hexdigest() != entry['export_sha256']:
            errors.append(f'Export hash/size mismatch: {relative}')
        if path.suffix == '.png':
            if relative.as_posix() != 'random-memory-report/memory-timeline.png' or not raw.startswith(b'\x89PNG\r\n\x1a\n'):
                errors.append(f'Image outside generated memory-chart allowlist: {relative}')
            continue
        if any(pattern.search(raw) for pattern in FORBIDDEN_TEXT):
            errors.append(f'Privacy pattern matched: {relative}')
        if path.suffix == '.json':
            try:
                json.loads(raw)
            except Exception:
                errors.append(f'Invalid JSON: {relative}')
        if path.suffix == '.jsonl':
            try:
                for line in raw.splitlines():
                    if line.strip():
                        json.loads(line)
            except Exception:
                errors.append(f'Invalid JSONL: {relative}')
        if path.suffix == '.md':
            for target in re.findall(r'!?\[[^\]]*\]\(([^\n)]*)\)', raw.decode()):
                target = target.strip().strip('<>')
                if re.match(r'https?://|mailto:|#', target):
                    continue
                linked = (path.parent / target.split('#', 1)[0]).resolve()
                if not linked.is_relative_to(ROOT) or not linked.is_file():
                    errors.append(f'Broken/unpublished Markdown link: {relative} -> {target}')
    for path in ROOT.rglob('*'):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if relative.parts[0] == 'onmyoji' or relative.as_posix() in {'README.md', 'package-manifest.json', 'cleanup-summary.json', 'genshin-exploration.json', 'verify_round2.py', 'publication-manifest.json'}:
            continue
        if relative.as_posix() not in seen:
            errors.append(f'Unmanifested file: {relative}')
    result = {'status': 'failed' if errors else 'passed', 'files': len(seen), 'bytes': total,
              'errors': errors, 'scope': 'Memory/headless/observer/MAA-review/nonempty exports; excludes root supplements and onmyoji/; those are covered by publication-manifest.json'}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return bool(errors)


if __name__ == '__main__':
    sys.exit(main())

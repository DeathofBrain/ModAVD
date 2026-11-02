"""Verify the complete sanitized round-two export using its publication manifest."""
from pathlib import Path
import hashlib
import json

root = Path(__file__).resolve().parent
manifest = json.loads((root / 'publication-manifest.json').read_text())
errors = []
expected = {'publication-manifest.json'}
for item in manifest['files']:
    path = root / item['path']
    expected.add(item['path'])
    if not path.resolve().is_relative_to(root) or not path.is_file() or path.is_symlink():
        errors.append('Missing or invalid path: ' + item['path'])
        continue
    raw = path.read_bytes()
    if len(raw) != item['bytes'] or hashlib.sha256(raw).hexdigest() != item['sha256']:
        errors.append('Hash/size mismatch: ' + item['path'])
actual = {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()}
if actual != expected:
    errors.append('Unexpected/missing files: ' + repr(sorted(actual ^ expected)))
print(json.dumps({'status': 'passed' if not errors else 'failed',
                  'files': len(manifest['files']), 'errors': errors}, indent=2))
raise SystemExit(bool(errors))

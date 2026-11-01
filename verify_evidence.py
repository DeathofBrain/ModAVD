#!/usr/bin/env python3
"""Verify publication hashes and all content-addressed capture payloads, offline."""
import hashlib
import json
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parent
manifest = json.loads((root / 'source-and-public-manifest.json').read_text())
for item in manifest['files']:
    data = (root / item['path']).read_bytes()
    assert len(data) == item['public_bytes'], item['path']
    assert hashlib.sha256(data).hexdigest() == item['public_sha256'], item['path']
with zipfile.ZipFile(root / 'screenshot-payloads.zip') as archive:
    cache = {}
    for item in manifest['screenshot_appendix_members']:
        member = item['archive_member']
        if member not in cache:
            data = archive.read(member)
            cache[member] = (len(data), hashlib.sha256(data).hexdigest())
        assert cache[member] == (item['bytes'], item['sha256']), item['path']
print(f"Verified {len(manifest['files'])} public evidence files and "
      f"{len(manifest['screenshot_appendix_members'])} capture references "
      f"({len(cache)} unique payloads).")

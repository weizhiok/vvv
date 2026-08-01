#!/usr/bin/env python3
import base64
import gzip
from pathlib import Path

source = Path('.staging-refactor/patch-hy2-static.gz.b64')
raw = source.read_text(encoding='utf-8-sig')
clean = ''.join(raw.split())
compressed = base64.b64decode(clean, validate=True)
script = gzip.decompress(compressed)
Path('/tmp/hy2-static.py').write_bytes(script)
print(f'chars={len(clean)} compressed={len(compressed)} script={len(script)}')

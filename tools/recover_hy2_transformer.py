#!/usr/bin/env python3
import base64
import gzip
from pathlib import Path

parts = sorted(Path('.staging-refactor').glob('prepare.hy2.part*'))
if not parts:
    raise SystemExit('No prepare.hy2 parts found')

chunks = []
for part in parts:
    text = part.read_text(encoding='utf-8-sig')
    clean = ''.join(text.split())
    print(f'{part}: chars={len(clean)}')
    chunks.append(clean)

encoded = ''.join(chunks)
print(f'total_chars={len(encoded)} mod4={len(encoded) % 4}')
try:
    compressed = base64.b64decode(encoded, validate=True)
except Exception as exc:
    raise SystemExit(f'Combined HY2 transformer Base64 is invalid: {exc}')

try:
    source = gzip.decompress(compressed)
except Exception as exc:
    raise SystemExit(f'Combined HY2 transformer gzip is invalid: {exc}')

output = Path('/tmp/recovered-prepare.py')
output.write_bytes(source)
print(f'compressed_bytes={len(compressed)} source_bytes={len(source)} output={output}')

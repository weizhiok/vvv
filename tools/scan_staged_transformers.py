#!/usr/bin/env python3
import base64
import gzip
from pathlib import Path

root = Path('.staging-refactor')
groups = {
    'prepare.good': sorted(root.glob('prepare.good.part*')),
    'prepare.gz': sorted(root.glob('prepare.gz.part*')),
    'prepare.hy2': sorted(root.glob('prepare.hy2.part*')),
    'prepare.v2': sorted(root.glob('prepare.v2.gz.part*')),
    'prepare.v3': sorted(root.glob('prepare.v3.gz.part*')),
    'prepare.py.b64': [root / 'prepare.py.b64'],
    'patch_host': [root / 'patch_host.py.gz.b64'],
    'sources': [root / 'sources.tgz.b64'],
}

for name, files in groups.items():
    files = [p for p in files if p.exists()]
    print(f'=== {name}: files={len(files)} ===')
    if not files:
        continue
    chunks = []
    for path in files:
        clean = ''.join(path.read_text(encoding='utf-8-sig').split())
        print(f'{path.name}: {len(clean)} chars')
        chunks.append(clean)
    encoded = ''.join(chunks)
    print(f'total={len(encoded)} mod4={len(encoded) % 4}')
    try:
        payload = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        print(f'base64=FAIL {exc}')
        continue
    print(f'base64=OK bytes={len(payload)} magic={payload[:8].hex()}')
    decoded = payload
    kind = 'raw'
    try:
        if payload[:2] == b'\x1f\x8b':
            decoded = gzip.decompress(payload)
            kind = 'gzip'
    except Exception as exc:
        print(f'gzip=FAIL {exc}')
        continue
    text = decoded.decode('utf-8', errors='replace')
    markers = {
        'build_hy2_slot_configs': 'build_hy2_slot_configs' in text,
        'sync_hy2_slot_services': 'sync_hy2_slot_services' in text,
        'build_vless_slot_configs': 'build_vless_slot_configs' in text,
        'host_write': "host.write_text(h, encoding='utf-8')" in text,
    }
    print(f'decode={kind} bytes={len(decoded)} markers={markers}')
    suffix = '.py' if ('import ' in text or text.startswith('#!')) else '.bin'
    Path(f'/tmp/{name.replace(".", "-")}{suffix}').write_bytes(decoded)

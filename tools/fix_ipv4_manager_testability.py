#!/usr/bin/env python3
from pathlib import Path

repls = [
    (
        Path('core-src/host.sh'),
        'IPV4_ONLY_MODULE=/usr/local/lib/vvv/ipv4_only.sh',
        'IPV4_ONLY_MODULE="${VVV_IPV4_ONLY_MODULE:-/usr/local/lib/vvv/ipv4_only.sh}"',
        'generated manager module path',
    ),
    (
        Path('tests/test_ipv4_only.py'),
        "module_decl = 'IPV4_ONLY_MODULE=/usr/local/lib/vvv/ipv4_only.sh'",
        "module_decl = 'IPV4_ONLY_MODULE=\"${VVV_IPV4_ONLY_MODULE:-/usr/local/lib/vvv/ipv4_only.sh}\"'",
        'IPv4-only regression manager declaration',
    ),
    (
        Path('tests/final_runtime_validation.sh'),
        'bash -n "$WORK/manager-lib.sh"\nsource "$WORK/manager-lib.sh"',
        'bash -n "$WORK/manager-lib.sh"\nexport VVV_IPV4_ONLY_MODULE="$ROOT/core-src/ipv4_only.sh"\nsource "$WORK/manager-lib.sh"',
        'final runtime manager library source',
    ),
]

for path, old, new, label in repls:
    text = path.read_text(encoding='utf-8')
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{path}: expected one {label} anchor, found {count}')
    path.write_text(text.replace(old, new, 1), encoding='utf-8')

print('patched IPv4 manager runtime path override and full-runtime harness')

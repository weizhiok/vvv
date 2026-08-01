#!/usr/bin/env python3
from pathlib import Path

path = Path('tests/final_runtime_validation.sh')
text = path.read_text(encoding='utf-8')
replacements = (
    (
        'XRAY="${1:?usage: final_runtime_validation.sh XRAY SING_BOX}"\nSING_BOX="${2:?usage: final_runtime_validation.sh XRAY SING_BOX}"\n',
        'TEST_XRAY="${1:?usage: final_runtime_validation.sh XRAY SING_BOX}"\nTEST_SING_BOX="${2:?usage: final_runtime_validation.sh XRAY SING_BOX}"\n',
    ),
    (
        'source "$WORK/manager-lib.sh"\nHY2_LIMIT_MBPS=50\n',
        'source "$WORK/manager-lib.sh"\nXRAY="$TEST_XRAY"\nSING_BOX="$TEST_SING_BOX"\nHY2_LIMIT_MBPS=50\n',
    ),
)
for old, new in replacements:
    count = text.count(old)
    if count == 1:
        text = text.replace(old, new, 1)
    elif new not in text:
        raise SystemExit(f'runtime core path anchor count={count}: {old[:80]!r}')
path.write_text(text, encoding='utf-8')

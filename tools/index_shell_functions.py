#!/usr/bin/env python3
import re
from pathlib import Path

source = Path('validation/generated-host-current.sh')
text = source.read_text(encoding='utf-8')
lines = text.splitlines()
pattern = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)\(\) \{$')
for number, line in enumerate(lines, 1):
    match = pattern.match(line)
    if match:
        print(f'{number:06d} {match.group(1)}')

#!/usr/bin/env python3
from pathlib import Path

path = Path('tests/conformance.py')
text = path.read_text(encoding='utf-8')
old = "    host = {'role': 'center-relay', 'state': sample_host_state()}\n"
new = "    host = {'host_id': 'audit-host-001', 'role': 'center-relay', 'state': sample_host_state()}\n"
if text.count(old) != 1:
    raise SystemExit(f'host fixture anchor count={text.count(old)}')
path.write_text(text.replace(old, new, 1), encoding='utf-8')

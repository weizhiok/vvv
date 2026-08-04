#!/usr/bin/env python3
import re
from pathlib import Path

source = Path('core-src/host.sh').read_text(encoding='utf-8')

expected_once = (
    'verify_xray_runtime',
    'verify_sing_runtime',
    'sync_vless_slot_services',
    'sync_hy2_slot_services',
    'validate_slot_references',
    'build_delete_candidate',
    'perform_delete',
    'perform_delete_upstream',
)

for name in expected_once:
    count = len(re.findall(rf'(?m)^{re.escape(name)}\(\) \{{$', source))
    assert count == 1, f'{name} must have exactly one top-level definition, got {count}'

transaction_guard = '  if ! validate_slot_references "$candidate_state"; then\n'
assert source.count(transaction_guard) == 1, 'candidate reference validation guard must appear exactly once'

print('PASS lifecycle source definitions and transaction guards are unique')

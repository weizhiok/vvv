#!/usr/bin/env python3
from pathlib import Path

path = Path('tests/final_runtime_validation.sh')
text = path.read_text(encoding='utf-8')
old = '''[[ -s "$WORK/client-files/v2rayNG.txt" ]]
grep -q '^hysteria2://' "$WORK/client-files/v2rayNG.txt"
grep -q 'pinSHA256=' "$WORK/client-files/v2rayNG.txt"
! grep -q 'insecure=' "$WORK/client-files/v2rayNG.txt"
'''
new = '''[[ -s "$WORK/client-files/Quantumult-X.conf" ]]
[[ -s "$WORK/client-files/Loon.conf" ]]
[[ -s "$WORK/client-files/Shadowrocket.txt" ]]
[[ -s "$WORK/client-files/Clash-Verge-Rev.yaml" ]]
[[ ! -e "$WORK/client-files/v2rayNG.txt" ]]
grep -q '^vless=' "$WORK/client-files/Quantumult-X.conf"
grep -q 'Hysteria2' "$WORK/client-files/Loon.conf"
grep -q '^hysteria2://' "$WORK/client-files/Shadowrocket.txt"
grep -q 'type: hysteria2' "$WORK/client-files/Clash-Verge-Rev.yaml"
'''
if text.count(old) != 1:
    raise SystemExit('runtime fixture v2rayNG block not found exactly once')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
print('RUNTIME FIXTURE UPDATED')

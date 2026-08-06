#!/usr/bin/env python3
from pathlib import Path

path = Path('tests/final_runtime_validation.sh')
text = path.read_text(encoding='utf-8')
old = '''[[ ! -e "$WORK/client-files/NekoBoxForAndroid.yaml" ]]
[[ ! -e "$WORK/client-files/Loon-Shadowrocket.txt" ]]
'''
new = '''[[ -s "$WORK/client-files/NekoBoxForAndroid.yaml" ]]
[[ ! -e "$WORK/client-files/Loon-Shadowrocket.txt" ]]
'''
if old not in text:
    raise SystemExit('NekoBox file-existence runtime marker not found')
text = text.replace(old, new, 1)
old = '''! grep -q '^rules:' "$WORK/client-files/Clash-Verge-Rev.yaml"
grep -q '^hy2://' "$WORK/client-files/NekoBoxForAndroid-基础URI.txt"
'''
new = '''! grep -q '^rules:' "$WORK/client-files/Clash-Verge-Rev.yaml"
grep -q '【NekoBoxForAndroid】' "$WORK/client-files/客户端节点.txt"
grep -q 'type: hysteria2' "$WORK/client-files/NekoBoxForAndroid.yaml"
grep -q 'ports: "24443,30000-30031"' "$WORK/client-files/NekoBoxForAndroid.yaml"
grep -q 'hop-interval: 30' "$WORK/client-files/NekoBoxForAndroid.yaml"
! grep -q 'hop-interval: "20-30"' "$WORK/client-files/NekoBoxForAndroid.yaml"
grep -q 'up: "30 Mbps"' "$WORK/client-files/NekoBoxForAndroid.yaml"
grep -q 'down: "50 Mbps"' "$WORK/client-files/NekoBoxForAndroid.yaml"
grep -q '^hy2://' "$WORK/client-files/NekoBoxForAndroid-基础URI.txt"
'''
if old not in text:
    raise SystemExit('NekoBox runtime field marker not found')
path.write_text(text.replace(old, new, 1), encoding='utf-8')

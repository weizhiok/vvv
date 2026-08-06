#!/usr/bin/env python3
from pathlib import Path

path = Path('core-src/client_adapters.py')
text = path.read_text(encoding='utf-8')
old = """    if 'Loon-Shadowrocket.txt' in names or 'NekoBoxForAndroid.yaml' in names:
        raise RuntimeError('obsolete duplicated local outputs are still present')
"""
new = """    if 'Loon-Shadowrocket.txt' in names:
        raise RuntimeError('obsolete duplicated local outputs are still present')
    if 'NekoBoxForAndroid.yaml' not in names:
        raise RuntimeError('NekoBox full local YAML output is missing')
"""
if old not in text:
    raise SystemExit('NekoBox smoke-test marker not found')
path.write_text(text.replace(old, new, 1), encoding='utf-8')

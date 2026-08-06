#!/usr/bin/env python3
from pathlib import Path

path = Path('core-src/client_adapters.py')
text = path.read_text(encoding='utf-8')
bad = """    return json.dumps({'outbounds': outbounds}, ensure_ascii=False, indent=2) + '
'
"""
good = "    return json.dumps({'outbounds': outbounds}, ensure_ascii=False, indent=2) + '\\n'\n"
if bad not in text:
    raise SystemExit('generated NekoBox renderer newline marker not found')
path.write_text(text.replace(bad, good, 1), encoding='utf-8')

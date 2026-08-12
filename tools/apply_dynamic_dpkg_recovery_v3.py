#!/usr/bin/env python3
from pathlib import Path

path = Path(__file__).with_name('apply_dynamic_dpkg_recovery_v2.py')
text = path.read_text(encoding='utf-8')
text, n1 = text.replace("TEST_CONTENT = r'''", 'TEST_CONTENT = r"""', 1), 1
old = "'''\n\n\ndef replace_function"
new = '"""\n\n\ndef replace_function'
if old not in text:
    raise SystemExit('cannot locate TEST_CONTENT closing delimiter')
text = text.replace(old, new, 1)
compile(text, str(path), 'exec')
exec(compile(text, str(path), 'exec'), {'__name__': '__main__', '__file__': str(path)})

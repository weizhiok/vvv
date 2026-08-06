#!/usr/bin/env python3
from pathlib import Path

path = Path('tests/test_install_reboot_guard.py')
text = path.read_text(encoding='utf-8')
bad = "\nrequire('systemctl daemon-reload' not in create_xray\nrequire('systemctl daemon-reload' not in create_xray"
good = "\nrequire('systemctl daemon-reload' not in create_xray"
if bad not in text:
    raise SystemExit('generated reboot-test cleanup marker not found')
path.write_text(text.replace(bad, good, 1), encoding='utf-8')

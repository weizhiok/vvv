#!/usr/bin/env python3
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    ROOT / 'vvv-install.sh',
    ROOT / 'core-src' / 'host.sh',
    ROOT / 'core-src' / 'landing.sh',
    ROOT / 'core-src' / 'center_install.sh',
]
old = "printf '%s\n'"
new = "printf '%s\\n'"
for path in FILES:
    text = path.read_text(encoding='utf-8')
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{path}: expected exactly one multiline printf, found {count}')
    path.write_text(text.replace(old, new, 1), encoding='utf-8')
subprocess.run(['bash', '-n', str(ROOT / 'vvv-install.sh')], check=True)
subprocess.run(['bash', '-n', str(ROOT / 'core-src' / 'host.sh')], check=True)
subprocess.run(['sh', '-n', str(ROOT / 'core-src' / 'landing.sh')], check=True)
subprocess.run(['bash', '-n', str(ROOT / 'core-src' / 'center_install.sh')], check=True)
print('normalized dpkg audit printf')

#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
transport = ROOT / 'core-src/center_transport.sh'
text = transport.read_text(encoding='utf-8')
old = 'http://127.0.0.1:${port} {\n  bind 0.0.0.0\n  log {'
new = 'http://127.0.0.1:${port} {\n  bind 127.0.0.1\n  log {'
if old in text:
    if text.count(old) != 1:
        raise SystemExit('unexpected tunnel bind count')
    text = text.replace(old, new, 1)
elif new not in text:
    raise SystemExit('tunnel IPv4-only bind anchor missing')
transport.write_text(text, encoding='utf-8')

checks = {
    ROOT / 'vvv-install.sh': ['ipv4_only.sh', 'vvv_enforce_ipv4_only', 'curl -4fsSL'],
    ROOT / 'core-src/bootstrap.sh': ['source "$IPV4_ONLY_MODULE"', 'vvv_enforce_ipv4_only'],
    ROOT / 'core-src/host.sh': ['ipv4_only.sh', 'vvv_enforce_ipv4_only', '"listen":"0.0.0.0"', '"domainStrategy":"UseIPv4"'],
    ROOT / 'core-src/landing.sh': ['/usr/local/lib/vvv/ipv4_only.sh', 'vvv_enforce_ipv4_only', '"listen": "0.0.0.0"', '"domainStrategy": "UseIPv4"'],
    ROOT / 'core-src/center_install.sh': ['source "$IPV4_ONLY_MODULE"', 'vvv_enforce_ipv4_only', "'listen_host':'0.0.0.0'"],
    ROOT / 'core-src/center_transport.sh': ['bind 0.0.0.0', 'bind 127.0.0.1', '--edge-ip-version 4'],
    ROOT / 'core-src/hy2_port_hop.py': ['TABLE_FAMILY = "ip"'],
}
for path, tokens in checks.items():
    body = path.read_text(encoding='utf-8')
    for token in tokens:
        if token not in body:
            raise SystemExit(f'{path}: missing {token}')
print('IPv4-only patch audit passed')

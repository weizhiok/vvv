#!/usr/bin/env python3
from pathlib import Path

path = Path('tests/conformance.py')
text = path.read_text(encoding='utf-8')

old_host = "    host = {'role': 'center-relay', 'state': sample_host_state()}\n"
new_host = "    host = {'host_id': 'audit-host-001', 'role': 'center-relay', 'state': sample_host_state()}\n"
if old_host in text:
    text = text.replace(old_host, new_host, 1)
elif new_host not in text:
    raise SystemExit('host fixture not found')

old_protocols = "    require({n['protocol'] for n in nodes} == {'vless', 'hy2'}, '双协议直连节点没有同时进入订阅')\n"
new_protocols = "    require({n['protocol'] for n in nodes} == {'vless', 'hysteria2'}, '双协议直连节点没有同时进入订阅')\n"
if old_protocols in text:
    text = text.replace(old_protocols, new_protocols, 1)
elif new_protocols not in text:
    raise SystemExit('protocol assertion not found')

path.write_text(text, encoding='utf-8')

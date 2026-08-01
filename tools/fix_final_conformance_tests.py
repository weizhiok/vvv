#!/usr/bin/env python3
from pathlib import Path

path = Path('tests/conformance.py')
text = path.read_text(encoding='utf-8')

replacements = (
    (
        "    host = {'role': 'center-relay', 'state': sample_host_state()}\n",
        "    host = {'host_id': 'audit-host-001', 'role': 'center-relay', 'state': sample_host_state()}\n",
    ),
    (
        "    require({n['protocol'] for n in nodes} == {'vless', 'hy2'}, '双协议直连节点没有同时进入订阅')\n",
        "    require({n['protocol'] for n in nodes} == {'vless', 'hysteria2'}, '双协议直连节点没有同时进入订阅')\n",
    ),
    (
        "    raw = module.render_client('v2', nodes)\n",
        "    raw = module.render_v2rayng(nodes)\n",
    ),
    (
        "    clash = module.render_client('c', nodes)\n    qx = module.render_client('qx', nodes)\n    loon = module.render_client('ln', nodes)\n    shadowrocket = module.render_client('sr', nodes)\n",
        "    clash = module.render_clash(nodes)\n    qx = module.render_qx(nodes)\n    loon = module.render_loon(nodes)\n    shadowrocket = module.render_shadowrocket(nodes)\n",
    ),
    (
        "    require('hysteria2://' in shadowrocket, 'Shadowrocket 缺少 Hysteria 2 链接')\n",
        "    shadowrocket_text = base64.b64decode(shadowrocket).decode('utf-8')\n    require('hysteria2://' in shadowrocket_text, 'Shadowrocket 缺少 Hysteria 2 链接')\n",
    ),
    (
        "    require('rclone copyto' in backup and 'rclone sync' not in backup, '云上传必须使用 copy/copyto 而不是 sync')\n",
        "    require(\"'copyto'\" in backup and \"'sync'\" not in backup, '云上传必须使用 copy/copyto 而不是 sync')\n",
    ),
    (
        "    require('AESGCM' in backup and '.enc' in backup, '本地备份没有使用加密容器')\n",
        "    require('-aes-256-cbc' in backup and '-pbkdf2' in backup and '.enc' in backup, '本地备份没有使用 AES-256-CBC + PBKDF2 加密容器')\n",
    ),
)

for old, new in replacements:
    if old in text:
        text = text.replace(old, new, 1)
    elif new not in text:
        raise SystemExit(f'conformance test anchor not found: {old[:80]!r}')

path.write_text(text, encoding='utf-8')

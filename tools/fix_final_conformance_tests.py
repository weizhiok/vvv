#!/usr/bin/env python3
from pathlib import Path

conformance = Path('tests/conformance.py')
text = conformance.read_text(encoding='utf-8')

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
        "    require(\"clients = ('c', 'qx', 'ln', 'sr', 'v2')\" in source, '订阅短路径集合不正确')\n",
        "    require(\"SHORT_PATHS = {'c': 'clash', 'qx': 'quantumultx', 'ln': 'loon', 'sr': 'shadowrocket', 'v2': 'v2rayng'}\" in source, '订阅短路径集合不正确')\n",
    ),
    (
        "    require('for client in sr v2' in center, '订阅二维码应只显示 Shadowrocket 和 v2rayNG')\n    require('for client in qx ln' not in center, 'QX 或 Loon 不应生成订阅二维码')\n",
        "    require(\"Shadowrocket|${base}/r/${token}/sr\" in center and \"v2rayNG|${base}/r/${token}/v2\" in center, '订阅二维码应只显示 Shadowrocket 和 v2rayNG')\n    require(\"Quantumult X|${base}/r/${token}/qx\" not in center and \"Loon|${base}/r/${token}/ln\" not in center, 'QX 或 Loon 不应生成订阅二维码')\n",
    ),
    (
        "    require(\"'enabled': False\" in center, '云备份默认值不是关闭')\n",
        "    require('rm -f \"$CFG_DIR/cloud.json\" \"$CFG_DIR/rclone.conf\"' in center, '云备份默认状态不是关闭')\n",
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
conformance.write_text(text, encoding='utf-8')

center = Path('core-src/center_install.sh')
text = center.read_text(encoding='utf-8')
legacy = '''systemctl disable --now vvv-backup-pull.timer vvv-backup-pull.service >/dev/null 2>&1 || true
rm -f /etc/systemd/system/vvv-backup-pull.timer /etc/systemd/system/vvv-backup-pull.service
rm -rf /var/backups/vvv-remote
'''
if legacy in text:
    text = text.replace(legacy, '', 1)
elif any(token in text for token in ('vvv-backup-pull', '/var/backups/vvv-remote')):
    raise SystemExit('unexpected legacy backup cleanup remains in center installer')
text = text.replace('create initial-center-install --force', 'create first-install --force')
center.write_text(text, encoding='utf-8')

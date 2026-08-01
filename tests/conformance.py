#!/usr/bin/env python3
import base64
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / 'core-src'


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def load_sub_center():
    path = CORE / 'sub_center.py'
    spec = importlib.util.spec_from_file_location('vvv_sub_center', path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def test_menu_and_front_loaded_parameters():
    text = read('core-src/bootstrap.sh')
    labels = [
        '1. 安装订阅中心（含自身代理）',
        '2. 安装中转主机（含自身代理）',
        '3. 安装中转副机',
        '4. 安装直连代理',
        '5. 以上全部安装（不含副机）',
        '0. 退出',
    ]
    positions = [text.index(label) for label in labels]
    require(positions == sorted(positions), '初始菜单顺序不符合最终要求')
    for token in (
        '========== 安装参数（全部前置设置） ==========',
        '请输入代理监听端口 [默认 443]',
        '请输入 VLESS + REALITY 伪装域名 [默认 www.softbank.jp]',
        '请输入订阅域名（直接回车使用本机 IP）',
        '请输入订阅服务端口 [默认 8443]',
        '请输入订阅中心接入码',
        '请输入完整 JPR3 对接密钥',
        '========== 安装参数总览 ==========',
        '直接开始全自动安装',
        'export VVV_PROTOCOL_MODE VVV_PROXY_PORT VVV_REALITY_SNI',
        'export VVV_SUB_DOMAIN VVV_SUB_PORT',
    ):
        require(token in text, f'缺少前置参数功能：{token}')
    collect = text.index('# 真正安装前，一次性收集该角色需要的全部参数。')
    execute = text.index('case "$choice" in', collect + 1)
    execute = text.index('install_host', execute)
    before_install = text[collect:execute]
    require('read -r' not in text[text.index('show_parameter_summary\n', collect) if 'show_parameter_summary\n' in text[collect:] else collect:execute], '参数总览后仍存在确认输入')
    require('register_sync.sh" center-relay "$code"' in text, 'All in One 没有映射为 center-relay 同步角色')
    require('register_sync.sh" all ' not in text, '仍使用 sync_agent 不支持的 all 角色')
    require('register_sync.sh" landing "$code"' in text, '中转副机没有使用 JPR3 中的接入码自动注册')


def sample_host_state():
    return {
        'protocol_mode': 'dual',
        'public_ip': '198.51.100.10',
        'listen_port': 443,
        'sni': 'www.softbank.jp',
        'direct_base_name': 'JP-198.51.100.10:443',
        'vless': {
            'direct_user': {'uuid': '11111111-1111-4111-8111-111111111111'},
            'reality': {'public_key': 'PublicKeyAudit', 'short_id': '0123456789abcdef'},
        },
        'hy2': {
            'direct_user': {'password': 'hy2-password'},
            'server_name': 'jp-hy2.jp-relay.local',
            'certificate_pin_hex': 'aa' * 32,
            'certificate_public_key_sha256': base64.b64encode(bytes(range(32))).decode(),
            'obfs_password': 'salamander-secret',
        },
        'relays': [],
        'upstream_relays': [],
    }


def decoded_v2rayng(module, nodes):
    raw = module.render_v2rayng(nodes)
    return base64.b64decode(raw).decode('utf-8').splitlines()


def test_subscription_renderers():
    module = load_sub_center()
    host = {'host_id': 'audit-host-001', 'role': 'center-relay', 'state': sample_host_state()}
    nodes = module.nodes_from_host(host)
    require({n['protocol'] for n in nodes} == {'vless', 'hysteria2'}, '双协议直连节点没有同时进入订阅')

    clash = module.render_clash(nodes)
    qx = module.render_qx(nodes)
    loon = module.render_loon(nodes)
    shadowrocket = module.render_shadowrocket(nodes)
    v2_lines = decoded_v2rayng(module, nodes)

    require('type: vless' in clash and 'type: hysteria2' in clash, 'Clash 订阅缺少双协议节点')
    require('vless=' in qx and 'hysteria' not in qx.lower(), 'Quantumult X 应只输出 VLESS')
    require('salamander-password=salamander-secret' in loon, 'Loon HY2 混淆密码格式错误')
    require('salamander-password="' not in loon, 'Loon HY2 混淆密码仍带双引号')
    shadowrocket_text = base64.b64decode(shadowrocket).decode('utf-8')
    require('hysteria2://' in shadowrocket_text, 'Shadowrocket 缺少 Hysteria 2 链接')
    hy2 = next((line for line in v2_lines if line.startswith('hy2://')), '')
    require(hy2, 'v2rayNG 没有独立 hy2:// 链接')
    require('pinSHA256' not in hy2, 'v2rayNG HY2 不应携带 pinSHA256')
    for token in ('sni=', 'insecure=1', 'obfs=salamander', 'obfs-password='):
        require(token in hy2, f'v2rayNG HY2 缺少 {token}')

    source = read('core-src/sub_center.py')
    require("SHORT_PATHS = {'c': 'clash', 'qx': 'quantumultx', 'ln': 'loon', 'sr': 'shadowrocket', 'v2': 'v2rayng'}" in source, '订阅短路径集合不正确')
    require("{'c': 'clash', 'qx': 'quantumultx', 'ln': 'loon', 'sr': 'shadowrocket', 'v2': 'v2rayng'}" in source, '短路径渲染映射不正确')
    center = read('core-src/center_install.sh')
    require("Shadowrocket|${base}/r/${token}/sr" in center and "v2rayNG|${base}/r/${token}/v2" in center, '订阅二维码应只显示 Shadowrocket 和 v2rayNG')
    require("Quantumult X|${base}/r/${token}/qx" not in center and "Loon|${base}/r/${token}/ln" not in center, 'QX 或 Loon 不应生成订阅二维码')


def test_backup_policy():
    center = read('core-src/center_install.sh')
    backup = read('core-src/backup_manager.py')
    rclone = read('core-src/rclone_manager.sh')
    register = read('core-src/register_sync.sh')
    sub = read('core-src/sub_center.py')
    prepare = read('src/prepare.py')

    forbidden = ('vvv-backup-pull', '/api/v1/backup', '/var/backups/vvv-remote')
    production = '\n'.join((center, backup, rclone, register, sub, prepare))
    for token in forbidden:
        require(token not in production, f'仍保留旧远程备份逻辑：{token}')
    require('backup.timer' not in production and 'backup-pull.timer' not in production, '仍存在备份定时器')
    require('立即生成本地备份' not in center and '手动本地备份' not in center, '仍存在手动备份菜单')
    require('rm -f "$CFG_DIR/cloud.json" "$CFG_DIR/rclone.conf"' in center, '云备份默认状态不是关闭')
    require('rclone.org/install.sh' not in center, '订阅中心首次安装不应安装 rclone')
    require('first-install' in center, '首次安装没有自动备份')
    for token in ('before-line-change', 'after-line-change'):
        require(token in prepare, f'线路事务缺少 {token} 备份')
    for token in ('before-host-register', 'after-host-register', 'before-node-sync', 'after-node-sync'):
        require(token in sub, f'订阅数据写入缺少 {token} 备份')
    for token in ('before-cloud-backup-enabled', 'after-cloud-backup-enabled', 'before-cloud-backup-disabled', 'after-cloud-backup-disabled'):
        require(token in rclone, f'云备份配置缺少事务事件 {token}')
    require("'copyto'" in backup and "'sync'" not in backup, '云上传必须使用 copy/copyto 而不是 sync')
    require('-aes-256-cbc' in backup and '-pbkdf2' in backup and '.enc' in backup, '本地备份没有使用 AES-256-CBC + PBKDF2 加密容器')


def test_jpr3_and_slot_architecture():
    prepare = read('src/prepare.py')
    landing = read('core-src/landing.sh')
    for token in (
        'subscription_registration_code',
        'build_vless_slot_configs',
        'sync_vless_slot_services',
        'vvv-vless-slot@',
        'build_hy2_slot_configs',
        'sync_hy2_slot_services',
        'vvv-hy2-slot@',
        '主 Xray PID 已保持不变',
        '主 sing-box PID 已保持不变',
    ):
        require(token in prepare, f'最终槽位/JPR3 转换器缺少 {token}')
    for token in ('xray_dynamic_parts()', 'xray_hot_apply()'):
        require(token not in prepare, f'仍保留旧 Xray API 热更新实现：{token}')
    require('python3' in landing, '落地脚本没有显式安装 Python 运行时')
    require(".schema == 3" in landing and 'JPR3' in landing, '落地脚本没有严格校验 JPR3')


def test_qr_helper():
    qr = read('core-src/qr_helper.sh')
    require('qrencode -t ANSIUTF8 -m 1' in qr, 'SSH 二维码没有启用终端白边')
    require("printf '\033[47m" in qr, '二维码顶部没有额外白边')
    require('download' not in qr.lower(), '二维码辅助脚本包含不需要的文件下载逻辑')


def test_no_obsolete_role_terms():
    files = [
        'core-src/bootstrap.sh', 'core-src/center_install.sh', 'core-src/register_sync.sh',
        'core-src/sync_agent.py', 'core-src/vvv_manager.sh', 'core-src/sub_center.py',
        'core-src/backup_manager.py', 'core-src/rclone_manager.sh', 'src/prepare.py',
    ]
    text = '\n'.join(read(path) for path in files)
    for token in ('日本A', '日本B', '备用订阅中心', '双主机互备'):
        require(token not in text, f'生产源码仍含旧角色概念：{token}')


def main():
    tests = [
        test_menu_and_front_loaded_parameters,
        test_subscription_renderers,
        test_backup_policy,
        test_jpr3_and_slot_architecture,
        test_qr_helper,
        test_no_obsolete_role_terms,
    ]
    for test in tests:
        test()
        print(f'PASS {test.__name__}')
    print('ALL CONFORMANCE TESTS PASSED')


if __name__ == '__main__':
    main()

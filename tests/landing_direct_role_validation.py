#!/usr/bin/env python3
import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / 'core-src'


def require(value, message):
    if not value:
        raise AssertionError(message)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_menu_and_ports():
    text = (CORE / 'bootstrap.sh').read_text(encoding='utf-8')
    expected = [
        '1. 安装订阅中心 + 中转主机 + 自身代理',
        '2. 安装订阅中心 + 自身代理',
        '3. 安装中转主机 + 自身代理',
        '4. 安装中转副机 + 自身代理',
        '5. 安装中转副机',
        '6. 安装直连代理',
        '7. 从云备份恢复',
    ]
    positions = [text.index(item) for item in expected]
    require(positions == sorted(positions), '首次菜单编号或顺序错误')
    host = (CORE / 'host.sh').read_text(encoding='utf-8')
    require('请输入落地统一端口 [默认 ${default_port}]' in host and 'default_port="553"' in host,
            '新建副机线路默认端口不是 553')
    require('"schema":4,"type":"jp-relay-landing"' in host, 'JPR3 没有升级到 schema 4')
    require('subscription_bootstrap' in host and 'relay-ticket' in host, 'JPR3 没有受限订阅注册票据')


def test_landing_isolation():
    landing = (CORE / 'landing.sh').read_text(encoding='utf-8')
    for token in (
        '/etc/vvv-landing/xray/config.json',
        '/etc/vvv-landing/sing-box/config.json',
        '/etc/vvv-landing/sing-box/tls',
        'vvv-landing-xray.service',
        'vvv-landing-sing-box.service',
        'VVV_COMBINED_INSTALL',
    ):
        require(token in landing, f'中转副机隔离缺少 {token}')
    require('/etc/systemd/system/xray.service <<' not in landing, '中转副机仍覆盖直连 Xray 服务')
    require('/etc/systemd/system/sing-box.service <<' not in landing, '中转副机仍覆盖直连 sing-box 服务')


def test_sync_and_names():
    sync = (CORE / 'sync_agent.py').read_text(encoding='utf-8')
    center = (CORE / 'sub_center.py').read_text(encoding='utf-8')
    renderer = (CORE / 'client_local_renderer.py').read_text(encoding='utf-8')
    require('landing-direct' in sync and "'states': {'direct': direct, 'landing': landing}" in sync,
            '组合角色没有同步两套状态')
    require('/api/v1/register-ticket' in center and '/api/v1/relay-ticket' in center,
            '订阅中心缺少受限注册票据端点')
    require('中转-' in center and '中转-' in renderer, '中转节点命名缺少“中转”')
    require("return 'landing-direct', main_contexts" in renderer,
            '本机客户端生成器没有聚合直连和中转配置')


def test_backup_and_protection():
    backup = (CORE / 'backup_manager.py').read_text(encoding='utf-8')
    restore = (CORE / 'restore_manager.py').read_text(encoding='utf-8')
    engine = (CORE / 'client_upgrade_engine.py').read_text(encoding='utf-8')
    require("Path('/etc/vvv-landing')" in backup, '云备份没有包含中转独立配置')
    require("'etc/vvv-landing/'" in restore, '云恢复没有允许中转独立配置')
    for token in ('vvv-landing-xray.service', 'vvv-landing-sing-box.service', '/etc/vvv-landing/xray/config.json'):
        require(token in engine, f'客户端升级保护缺少 {token}')


def test_renderer_aggregation():
    module = load(CORE / 'client_local_renderer.py', 'renderer_test')
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / 'etc/jp-relay').mkdir(parents=True)
        direct = {
            'protocol_mode': 'vless', 'public_ip': '203.0.113.10', 'listen_port': 443,
            'sni': 'www.softbank.jp', 'direct_base_name': 'SG-203.0.113.10:443',
            'vless': {'direct_user': {'uuid': '11111111-1111-4111-8111-111111111111'},
                      'reality': {'public_key': 'pk', 'short_id': '0123456789abcdef'}},
        }
        landing = {
            'protocol_mode': 'vless', 'node_name': 'SG-198.51.100.20:553',
            'japan_public_ip': '192.0.2.10', 'japan_port': 443,
            'remote_public_ip': '198.51.100.20', 'remote_public_port': 553,
            'sni': 'www.softbank.jp',
            'vless': {'japan_client_uuid': '22222222-2222-4222-8222-222222222222',
                      'japan_reality_public_key': 'pk2', 'japan_reality_short_id': 'abcdef0123456789'},
        }
        (root / 'etc/jp-relay/state.json').write_text(json.dumps(direct), encoding='utf-8')
        (root / 'etc/jp-relay/landing-state.json').write_text(json.dumps(landing), encoding='utf-8')
        role, contexts = module.detect_contexts(root)
        require(role == 'landing-direct' and len(contexts) == 2, '组合角色没有生成两组本机客户端配置')
        names = [node['name'] for ctx in contexts for node in ctx['nodes']]
        require(any('VLESS-中转-192.0.2.10:443' in name for name in names), '中转节点名称不符合要求')


def main():
    for test in (test_menu_and_ports, test_landing_isolation, test_sync_and_names,
                 test_backup_and_protection, test_renderer_aggregation):
        test(); print('PASS', test.__name__)
    print('LANDING DIRECT ROLE VALIDATION PASSED')


if __name__ == '__main__':
    main()

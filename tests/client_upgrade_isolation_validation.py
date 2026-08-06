#!/usr/bin/env python3
import hashlib
import importlib.util
import json
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / 'core-src'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(value, message):
    if not value:
        raise AssertionError(message)


def install_runtime(root):
    target = root / 'usr/local/lib/vvv'
    target.mkdir(parents=True)
    for name in ('client_adapters.py', 'client_local_renderer.py', 'client_upgrade_engine.py'):
        shutil.copy2(CORE / name, target / name)


def sample_main_state():
    return {
        'schema': 4, 'role': 'japan-hub', 'protocol_mode': 'dual',
        'public_ip': '198.51.100.10', 'listen_port': 443, 'sni': 'www.softbank.jp',
        'hy2_limit_mbps': 50, 'direct_base_name': 'JP-198.51.100.10:443',
        'port_hopping': {'enabled': True, 'ports': '443,20000-50000', 'hop_interval_seconds': 30},
        'vless': {
            'reality': {'public_key': 'public-key', 'short_id': '0123456789abcdef'},
            'direct_user': {'uuid': '11111111-1111-4111-8111-111111111111'},
            'reserve_users': [
                {'slot': 'v01', 'uuid': '22222222-2222-4222-8222-222222222222',
                 'assigned_id': 'relay-1'},
            ],
        },
        'hy2': {
            'server_name': 'jp-hy2.local', 'obfs_password': 'main-obfs',
            'certificate_pin_hex': 'aa' * 32, 'certificate_fingerprint': 'AA:BB',
            'direct_user': {'password': 'direct-password'},
            'reserve_users': [
                {'slot': 'h01', 'password': 'relay-client-password', 'assigned_id': 'relay-1'},
            ],
        },
        'relays': [{
            'id': 'relay-1', 'name': 'SG-203.0.113.20:443',
            'remote_ip': '203.0.113.20', 'remote_port': 443,
            'vless': {'client_uuid': '22222222-2222-4222-8222-222222222222'},
            'hy2': {'client_password': 'relay-client-password'},
        }],
        'upstream_relays': [], 'temporary_nodes': [],
    }


def sample_landing_state():
    return {
        'role': 'landing', 'protocol_mode': 'dual', 'node_name': 'SG-203.0.113.20:443',
        'japan_public_ip': '198.51.100.10', 'japan_port': 443,
        'remote_public_ip': '203.0.113.20', 'remote_public_port': 443,
        'sni': 'www.softbank.jp', 'hy2_limit_mbps': 50,
        'japan_port_hopping': {'enabled': True, 'ports': '443,20000-50000', 'hop_interval_seconds': 30},
        'vless': {
            'japan_client_uuid': '22222222-2222-4222-8222-222222222222',
            'japan_reality_public_key': 'public-key',
            'japan_reality_short_id': '0123456789abcdef',
        },
        'hy2': {
            'japan_client_password': 'relay-client-password',
            'japan_obfs_password': 'main-obfs', 'japan_server_name': 'jp-hy2.local',
            'japan_certificate_pin_hex': 'aa' * 32,
            'japan_certificate_fingerprint': 'AA:BB',
        },
    }


def make_protected_files(root):
    values = {}
    for index, absolute in enumerate((
        '/usr/local/bin/xray', '/usr/local/bin/sing-box',
        '/usr/local/etc/xray/config.json', '/etc/sing-box/config.json',
        '/etc/systemd/system/xray.service', '/etc/systemd/system/sing-box.service',
    )):
        path = root / absolute.lstrip('/')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'protected-{index}\n', encoding='utf-8')
        values[absolute] = digest(path)
    return values


def verify_protected(root, values):
    for absolute, expected in values.items():
        require(digest(root / absolute.lstrip('/')) == expected,
                f'客户端升级修改了受保护文件：{absolute}')


def seed_obsolete_outputs(directory):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'NekoBoxForAndroid.yaml').write_text('obsolete\n', encoding='utf-8')
    (directory / 'Loon-Shadowrocket.txt').write_text('obsolete\n', encoding='utf-8')


def verify_new_outputs(directory, role, expect_subscription=False):
    neko_yaml = directory / 'NekoBoxForAndroid.yaml'
    neko_sn = directory / 'NekoBoxForAndroid-SN.txt'
    neko = directory / 'NekoBoxForAndroid.txt'
    basic = directory / 'NekoBoxForAndroid-基础URI.txt'
    require(neko_yaml.is_file(), f'{role}缺少 NekoBox 完整 YAML 输出')
    neko_yaml_text = neko_yaml.read_text(encoding='utf-8')
    require('proxies:' in neko_yaml_text and 'hop-interval: 30' in neko_yaml_text,
            f'{role} NekoBox YAML 缺少完整节点或固定 30 秒跳跃')
    require('hop-interval: "20-30"' not in neko_yaml_text,
            f'{role} NekoBox YAML 错误复用了 Mihomo 随机跳跃')
    require(neko_sn.is_file(), f'{role}缺少 NekoBox SN LINK 输出')
    sn_lines = neko_sn.read_text(encoding='utf-8').splitlines()
    require(any(line.startswith('sn://vmess?') for line in sn_lines),
            f'{role} NekoBox SN LINK 缺少 VLESS')
    require(any(line.startswith('sn://hysteria?') for line in sn_lines),
            f'{role} NekoBox SN LINK 缺少 Hysteria 2')
    require(neko.is_file(), f'{role}缺少 NekoBox 单节点订阅输出')
    require(basic.is_file() and 'hy2://' in basic.read_text(encoding='utf-8'),
            f'{role} NekoBox 基础 URI 缺失')
    require('mport=443,20000-50000' in basic.read_text(encoding='utf-8'),
            f'{role} NekoBox 基础 URI 缺少 mport')
    loon_import = directory / 'Loon-Import.txt'
    require(loon_import.is_file(), f'{role}缺少 Loon 正式导入文件')
    if expect_subscription:
        require('format=nekobox&node=' in neko.read_text(encoding='utf-8'),
                f'{role} NekoBox 单节点订阅地址缺失')
        require('loon://import?nodelist=' in loon_import.read_text(encoding='utf-8'),
                f'{role} Loon 正式导入链接缺失')
    else:
        require(not neko.read_text(encoding='utf-8').strip(),
                f'{role}未注册订阅中心却生成了完整 NekoBox 订阅')
        require(not loon_import.read_text(encoding='utf-8').strip(),
                f'{role}未注册订阅中心却生成了 Loon 导入链接')
    require(not (directory / 'Loon-Shadowrocket.txt').exists(), f'{role}未清理旧混合分享文件')
    loon = (directory / 'Loon.conf').read_text(encoding='utf-8')
    require('server-ports="443,20000-50000"' in loon and 'hop-interval=30' in loon and
            'download-bandwidth=50' in loon, f'{role} Loon 输出缺少端口跳跃或下载带宽')
    shadow = (directory / 'Shadowrocket.txt').read_text(encoding='utf-8')
    for field in ('peer=', 'fastopen=1', 'upmbps=30', 'downmbps=50', 'mport=443,20000-50000'):
        require(field in shadow, f'{role} Shadowrocket 缺少参数：{field}')
    clash = (directory / 'Clash-Verge-Rev.yaml').read_text(encoding='utf-8')
    require('ports: "443,20000-50000"' in clash and 'hop-interval: "20-30"' in clash and
            'up: "30 Mbps"' in clash and 'down: "50 Mbps"' in clash,
            f'{role} Mihomo 输出缺少随机跳跃或 30/50 Mbps')
    require('proxy-groups:' not in clash and 'rules:' not in clash,
            f'{role} Mihomo 输出仍包含策略组或规则')
    summary = (directory / '客户端节点.txt').read_text(encoding='utf-8')
    labels = ['【Quantumult X】', '【Loon】', '【Shadowrocket 分享链接】',
              '【NekoBox For Android】', '【Clash Verge Rev / Mihomo】']
    positions = [summary.index(label) for label in labels]
    require(positions == sorted(positions), f'{role}本机客户端显示顺序错误')


def test_main_role_upgrade():
    engine = load('upgrade_engine_test', CORE / 'client_upgrade_engine.py')
    engine.validate_restricted_source(CORE / 'client_adapters.py')
    with tempfile.TemporaryDirectory(prefix='vvv-client-main.') as td:
        root = Path(td)
        install_runtime(root)
        state = root / 'etc/jp-relay/state.json'
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps(sample_main_state(), ensure_ascii=False), encoding='utf-8')
        client_cfg = root / 'etc/vvv/client.json'
        client_cfg.parent.mkdir(parents=True, exist_ok=True)
        client_cfg.write_text(json.dumps({'host_id': 'host-00000001', 'subscription_url': 'https://sub.example.test/secret'}), encoding='utf-8')
        direct_dir = root / 'root/日本VPS-直连客户端配置'
        relay_dir = root / 'root/relay-packages/relay-1'
        seed_obsolete_outputs(direct_dir)
        seed_obsolete_outputs(relay_dir)
        protected = make_protected_files(root)
        state_hash = digest(state)
        payload = engine.apply_candidate(CORE / 'client_adapters.py', 'https://example.test/client_upgrade.py', root)
        require(payload['protected_proxy_files_unchanged'], '主机客户端升级没有通过保护检查')
        require(digest(state) == state_hash, '主机状态被客户端升级修改')
        verify_protected(root, protected)
        verify_new_outputs(direct_dir, '主机', True)
        verify_new_outputs(relay_dir, '中转线路', True)
        require((relay_dir / '客户端节点.txt').is_file(), '中转线路本机输出未重新生成')
        text = (root / 'root/日本VPS-客户端节点.txt').read_text(encoding='utf-8')
        require('NekoBox For Android' in text and 'Shadowrocket' in text and 'Quantumult X' in text, '主机汇总缺少客户端')


def test_landing_role_upgrade():
    engine = load('upgrade_engine_landing_test', CORE / 'client_upgrade_engine.py')
    with tempfile.TemporaryDirectory(prefix='vvv-client-landing.') as td:
        root = Path(td)
        install_runtime(root)
        state = root / 'etc/jp-relay/landing-state.json'
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps(sample_landing_state(), ensure_ascii=False), encoding='utf-8')
        output_dir = root / 'root/中转客户端配置'
        seed_obsolete_outputs(output_dir)
        protected = make_protected_files(root)
        state_hash = digest(state)
        engine.apply_candidate(CORE / 'client_adapters.py', 'https://example.test/client_upgrade.py', root)
        require(digest(state) == state_hash, '中转副机状态被客户端升级修改')
        verify_protected(root, protected)
        verify_new_outputs(output_dir, '中转副机')
        landing_summary = (root / 'root/中转客户端节点.txt').read_text(encoding='utf-8')
        require('Shadowrocket' in landing_summary and 'NekoBox For Android' in landing_summary,
                '中转副机汇总缺少 Shadowrocket 或 NekoBox For Android')


def test_restricted_payload_rejection():
    engine = load('upgrade_engine_reject_test', CORE / 'client_upgrade_engine.py')
    with tempfile.TemporaryDirectory(prefix='vvv-client-reject.') as td:
        bad = Path(td) / 'bad.py'
        bad.write_text('import subprocess\nVERSION=99\n', encoding='utf-8')
        try:
            engine.validate_restricted_source(bad)
        except RuntimeError as exc:
            require('禁止导入' in str(exc), '危险模块拒绝信息不明确')
        else:
            raise AssertionError('危险客户端升级文件没有被拒绝')


def test_menu_and_handoff_contract():
    manager = (CORE / 'vvv_manager.sh').read_text(encoding='utf-8')
    center = (CORE / 'center_manager.sh').read_text(encoding='utf-8')
    installer = (ROOT / 'vvv-install.sh').read_text(encoding='utf-8')
    default_url = 'https://raw.githubusercontent.com/weizhiok/vvv/client-support/client_upgrade.py'
    require('升级客户端支持' in manager and manager.index('升级客户端支持') < manager.index('echo "0. 退出"'),
            '主机/直连副机菜单没有把客户端升级放在退出上方')
    require('中转副机管理' in manager and 'CLIENT_UPGRADE' in manager,
            '中转副机统一菜单缺少客户端升级')
    require(default_url in center and 'client_support_handoff' in center and 'new_chat_instruction' in center,
            '请求头调试缺少跨对话交接信息')
    for name in ('client_upgrade_engine.py', 'client_local_renderer.py', 'client_package_renderer.py',
                 'hy2_port_hop.py', 'hy2_port_hop.sh'):
        require(name in installer, f'安装器没有下载 {name}')


def main():
    tests = [
        test_main_role_upgrade,
        test_landing_role_upgrade,
        test_restricted_payload_rejection,
        test_menu_and_handoff_contract,
    ]
    for test in tests:
        test()
        print('PASS', test.__name__)
    print('CLIENT SUPPORT ISOLATION VALIDATION PASSED')


if __name__ == '__main__':
    main()

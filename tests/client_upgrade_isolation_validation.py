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
        'schema': 3, 'role': 'japan-hub', 'protocol_mode': 'dual',
        'public_ip': '198.51.100.10', 'listen_port': 443, 'sni': 'www.softbank.jp',
        'hy2_limit_mbps': 50, 'direct_base_name': 'JP-198.51.100.10:443',
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


def test_main_role_upgrade():
    engine = load('upgrade_engine_test', CORE / 'client_upgrade_engine.py')
    engine.validate_restricted_source(CORE / 'client_adapters.py')
    with tempfile.TemporaryDirectory(prefix='vvv-client-main.') as td:
        root = Path(td)
        install_runtime(root)
        state = root / 'etc/jp-relay/state.json'
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps(sample_main_state(), ensure_ascii=False), encoding='utf-8')
        protected = make_protected_files(root)
        state_hash = digest(state)
        payload = engine.apply_candidate(CORE / 'client_adapters.py', 'https://example.test/client_upgrade.py', root)
        require(payload['protected_proxy_files_unchanged'], '主机客户端升级没有通过保护检查')
        require(digest(state) == state_hash, '主机状态被客户端升级修改')
        verify_protected(root, protected)
        require((root / 'root/日本VPS-直连客户端配置/NekoBoxForAndroid.yaml').is_file(), '主机缺少 NekoBox 输出')
        require((root / 'root/relay-packages/relay-1/客户端节点.txt').is_file(), '中转线路本机输出未重新生成')
        text = (root / 'root/日本VPS-客户端节点.txt').read_text(encoding='utf-8')
        require('NekoBoxForAndroid' in text and 'Quantumult X' in text, '主机汇总缺少客户端')


def test_landing_role_upgrade():
    engine = load('upgrade_engine_landing_test', CORE / 'client_upgrade_engine.py')
    with tempfile.TemporaryDirectory(prefix='vvv-client-landing.') as td:
        root = Path(td)
        install_runtime(root)
        state = root / 'etc/jp-relay/landing-state.json'
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps(sample_landing_state(), ensure_ascii=False), encoding='utf-8')
        protected = make_protected_files(root)
        state_hash = digest(state)
        engine.apply_candidate(CORE / 'client_adapters.py', 'https://example.test/client_upgrade.py', root)
        require(digest(state) == state_hash, '中转副机状态被客户端升级修改')
        verify_protected(root, protected)
        require((root / 'root/中转客户端配置/NekoBoxForAndroid.yaml').is_file(), '中转副机缺少 NekoBox 输出')
        require('NekoBoxForAndroid' in (root / 'root/中转客户端节点.txt').read_text(encoding='utf-8'),
                '中转副机汇总缺少 NekoBox')


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
    landing = (CORE / 'landing.sh').read_text(encoding='utf-8')
    center = (CORE / 'center_manager.sh').read_text(encoding='utf-8')
    sub = (CORE / 'sub_center.py').read_text(encoding='utf-8')
    installer = (ROOT / 'vvv-install.sh').read_text(encoding='utf-8')
    default_url = 'https://raw.githubusercontent.com/weizhiok/vvv/client-support/client_upgrade.py'
    require('升级客户端支持' in manager and manager.index('升级客户端支持') < manager.index('echo "0. 退出"'),
            '主机/直连副机菜单没有把客户端升级放在退出上方')
    require('升级客户端支持' in landing and 'client_upgrade_engine.py' in landing,
            '中转副机菜单缺少客户端升级')
    require(default_url in center and 'client_support_handoff' in sub,
            '请求头调试缺少跨对话交接信息')
    for name in ('client_upgrade_engine.py', 'client_local_renderer.py'):
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

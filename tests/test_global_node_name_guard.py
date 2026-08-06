#!/usr/bin/env python3
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / 'core-src/host.sh'
CENTER = ROOT / 'core-src/sub_center.py'
CENTER_INSTALL = ROOT / 'core-src/center_install.sh'
RENDERER = ROOT / 'core-src/client_package_renderer.py'
GUARD = ROOT / 'core-src/name_guard_runtime.py'
INSTALLER = ROOT / 'core-src/name_guard_installer.py'
VVV_INSTALL = ROOT / 'vvv-install.sh'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    if not spec.loader:
        raise RuntimeError(f'cannot load {path}')
    spec.loader.exec_module(module)
    return module


def extract_manager(output):
    text = HOST.read_text(encoding='utf-8')
    start_marker = "cat > /usr/local/sbin/jp-relay-manager <<'JP_RELAY_JPR3_MANAGER_EOF'\n"
    end_marker = '\nJP_RELAY_JPR3_MANAGER_EOF\n'
    start = text.index(start_marker) + len(start_marker)
    end = text.index(end_marker, start)
    output.write_text(text[start:end] + '\n', encoding='utf-8')
    output.chmod(0o700)


def test_manager_patch(renderer, guard, root):
    manager = root / 'jp-relay-manager'
    extract_manager(manager)
    original = manager.read_text(encoding='utf-8')
    created_output = renderer.patched_manager_text(original)
    patched = guard.patched_manager_text(created_output)
    manager.write_text(patched, encoding='utf-8')
    subprocess.run(['bash', '-n', str(manager)], check=True)
    assert guard.MANAGER_MARKER in patched
    assert patched.count('reserve_unique_node_name() {') == 1
    assert patched.count('reserve_unique_node_name "$node_name" relay') == 1
    assert patched.count('reserve_unique_node_name "$node_name" upstream') == 1
    assert patched.count('reserve_unique_node_name "$custom_name" temporary') == 1
    assert patched.count('assert_new_node_name "$node_name"') == 2
    assert patched.count('assert_new_node_name "$custom_name"') == 1
    assert '覆盖原线路并复用原密钥' not in patched
    assert '覆盖原线路并复用原 VLESS UUID' not in patched
    assert '新建或覆盖中转线路' not in patched
    assert '新建或覆盖 HTTP/HTTPS/SOCKS5 中转线路' not in patched
    assert '新建或覆盖动态代理线路' not in patched
    assert '(( count == 0 )) || fail "唯一名称保护失败：拒绝覆盖同名 VPS 中转线路。"' in patched
    assert '(( count == 0 )) || fail "唯一名称保护失败：拒绝覆盖同名动态代理线路。"' in patched
    assert guard.patched_manager_text(patched) == patched


def test_center_installer_patch(installer, root):
    target = root / 'center_install.sh'
    target.write_text(CENTER_INSTALL.read_text(encoding='utf-8'), encoding='utf-8')
    target.chmod(0o700)
    assert installer.patch_file(target) is True
    patched = target.read_text(encoding='utf-8')
    subprocess.run(['bash', '-n', str(target)], check=True)
    assert installer.MARKER in patched
    assert patched.count('python3 /usr/local/lib/vvv/name_guard_runtime.py') == 1
    assert patched.index(installer.MARKER) < patched.index('ensure_service vvv-sub.service restart 60')
    assert installer.patch_file(target) is False
    install_text = VVV_INSTALL.read_text(encoding='utf-8')
    assert 'name_guard_installer.py' in install_text
    assert 'name_guard_installer.py "$SOURCE_TARGET/center_install.sh"' in install_text


def test_center_patch(guard, root):
    patched_path = root / 'sub_center.py'
    patched = guard.patched_sub_center_text(CENTER.read_text(encoding='utf-8'))
    patched_path.write_text(patched, encoding='utf-8')
    subprocess.run([sys.executable, '-m', 'py_compile', str(patched_path)], check=True)
    assert guard.CENTER_MARKER in patched
    assert "if path == '/api/v1/reserve-name':" in patched
    assert "if path == '/api/v1/release-name':" in patched
    assert 'return ensure_unique_node_names(nodes, overrides)' in patched
    assert patched.count('consume_name_reservations(') >= 3
    assert guard.patched_sub_center_text(patched) == patched

    sys.path.insert(0, str(ROOT / 'core-src'))
    try:
        center = load(patched_path, 'vvv_patched_sub_center')
    finally:
        sys.path.pop(0)
    center.DATA = root / 'data'
    center.HOSTS = center.DATA / 'hosts'
    center.OUT = center.DATA / 'output'
    center.REGISTRY = center.DATA / 'registry.json'
    center.OVERRIDES = center.DATA / 'node-overrides.json'
    center.ORDER = center.DATA / 'node-order.json'
    center.TICKETS = center.DATA / 'relay-tickets.json'
    center.HOSTS.mkdir(parents=True)
    center.atomic_json(center.OVERRIDES, {})
    center.atomic_json(center.ORDER, {'schema': 1, 'ids': []})
    center.atomic_json(center.TICKETS, {'tickets': [], 'name_reservations': []})

    host_a = {
        'host_id': 'host-a', 'role': 'center-relay', 'last_seen_ts': 99999999999,
        'state': {
            'protocol_mode': 'vless', 'public_ip': '198.51.100.1', 'listen_port': 443,
            'sni': 'www.softbank.jp',
            'vless': {'reality': {'public_key': 'pk', 'short_id': 'sid'},
                      'direct_user': {'uuid': '11111111-1111-4111-8111-111111111111'}},
            'upstream_relays': [{
                'id': 'upstream-a', 'name': '动态IP-4G-土耳其-随机',
                'client_uuid': '22222222-2222-4222-8222-222222222222',
                'last_exit_ip': '203.0.113.10',
            }],
            'relays': [], 'temporary_nodes': [],
        },
    }
    center.atomic_json(center.HOSTS / 'host-a.json', host_a)
    first = center.reserve_node_name('host-new', '动态IP-4G-土耳其-随机', 'operation-0001')
    second = center.reserve_node_name('host-new', '动态IP-4G-土耳其-随机', 'operation-0002')
    repeat = center.reserve_node_name('host-new', '动态IP-4G-土耳其-随机', 'operation-0002')
    assert first['allocated_name'] == '动态IP-4G-土耳其-随机【2】'
    assert second['allocated_name'] == '动态IP-4G-土耳其-随机【3】'
    assert repeat['reservation_id'] == second['reservation_id']
    assert center.allocate_unique_name('ＮＡＭＥ', {center.normalize_name_key('name')}) == 'ＮＡＭＥ【2】'
    assert center.allocate_unique_name('节点【2】', {
        center.normalize_name_key('节点【2】'), center.normalize_name_key('节点【3】')
    }) == '节点【4】'
    long_name = '长' * 64
    numbered = center.allocate_unique_name(long_name, {center.normalize_name_key(long_name)})
    assert len(numbered) == 64 and numbered.endswith('【2】')

    nodes = [
        {'id': 'node-1', 'name': '重复-VLESS'},
        {'id': 'node-2', 'name': '重复-VLESS'},
        {'id': 'node-3', 'name': '重复-VLESS'},
    ]
    unique = center.ensure_unique_node_names(nodes, {})
    assert [item['name'] for item in unique] == ['重复-VLESS', '重复-VLESS【2】', '重复-VLESS【3】']
    stored = json.loads(center.OVERRIDES.read_text(encoding='utf-8'))
    assert stored['node-2']['auto_display_name'] == '重复-VLESS【2】'
    assert stored['node-3']['auto_display_name'] == '重复-VLESS【3】'
    unique_again = center.ensure_unique_node_names([
        {'id': 'node-1', 'name': '重复-VLESS'},
        {'id': 'node-2', 'name': '重复-VLESS'},
        {'id': 'node-3', 'name': '重复-VLESS'},
    ], stored)
    assert [item['name'] for item in unique_again] == ['重复-VLESS', '重复-VLESS【2】', '重复-VLESS【3】']


def main():
    renderer = load(RENDERER, 'vvv_renderer_for_name_guard')
    guard = load(GUARD, 'vvv_name_guard_test')
    installer = load(INSTALLER, 'vvv_name_guard_installer_test')
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        test_manager_patch(renderer, guard, root)
        test_center_installer_patch(installer, root)
        test_center_patch(guard, root)
    print('PASS global subscription node-name guard')


if __name__ == '__main__':
    main()

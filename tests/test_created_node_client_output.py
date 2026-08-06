#!/usr/bin/env python3
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / 'core-src/host.sh'
RENDERER = ROOT / 'core-src/client_package_renderer.py'
ADAPTER = ROOT / 'core-src/client_adapters.py'


def load_renderer():
    spec = importlib.util.spec_from_file_location('vvv_created_output_renderer', RENDERER)
    module = importlib.util.module_from_spec(spec)
    if not spec.loader:
        raise RuntimeError('cannot load renderer')
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


def run_render(state_path, item_id, out_dir, manager_path):
    result = subprocess.run([
        sys.executable, str(RENDERER),
        '--state', str(state_path), '--kind', 'temporary', '--id', item_id,
        '--out', str(out_dir), '--adapter', str(ADAPTER),
        '--manager-path', str(manager_path),
    ], text=True, capture_output=True, check=True)
    return result.stdout


def main():
    renderer = load_renderer()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        manager = root / 'jp-relay-manager'
        extract_manager(manager)
        before = manager.read_text(encoding='utf-8')
        assert renderer.MANAGER_PATCH_MARKER not in before
        assert renderer.install_manager_patch(manager, required=True) is True
        patched = manager.read_text(encoding='utf-8')
        assert renderer.MANAGER_PATCH_MARKER in patched
        assert patched.count('show_created_client_config() {') == 1
        assert patched.count('show_created_client_config relay "$relay_id"') == 1
        assert patched.count('show_created_client_config upstream "$upstream_id"') == 1
        assert patched.count('show_created_client_config temporary "$temp_id"') == 1
        assert 'print_client_config relay "$1"' in patched
        assert 'print_client_config upstream "$1"' in patched
        assert 'mktemp -d /tmp/vvv-created-client.XXXXXX' in patched
        assert 'rm -rf -- "$dir"' in patched
        assert patched.count('systemctl start vvv-sync.service') == before.count('systemctl start vvv-sync.service') == 1
        subprocess.run(['bash', '-n', str(manager)], check=True)
        assert renderer.install_manager_patch(manager, required=True) is False
        assert manager.read_text(encoding='utf-8') == patched

        state = {
            'schema': 4,
            'role': 'japan-hub',
            'protocol_mode': 'dual',
            'public_ip': '198.51.100.10',
            'listen_port': 443,
            'sni': 'www.softbank.jp',
            'hy2_limit_mbps': 50,
            'port_hopping': {
                'enabled': True,
                'ports': '443,20000-50000',
                'hop_interval_seconds': 30,
            },
            'vless': {
                'reality': {'public_key': 'PUBLICKEY', 'short_id': '0123456789abcdef'},
                'reserve_users': [
                    {
                        'slot': 'v01',
                        'uuid': '11111111-1111-4111-8111-111111111111',
                        'assigned_id': 'temp-vps',
                    },
                    {
                        'slot': 'v02',
                        'uuid': '22222222-2222-4222-8222-222222222222',
                        'assigned_id': 'temp-upstream',
                    },
                ],
            },
            'hy2': {
                'server_name': 'jp-hy2.jp-relay.local',
                'obfs_password': 'obfs-password',
                'certificate_pin_hex': 'aabbcc',
                'certificate_fingerprint': 'AA:BB:CC',
                'reserve_users': [
                    {'slot': 'h01', 'password': 'hy2-password', 'assigned_id': 'temp-vps'},
                ],
            },
            'temporary_nodes': [
                {
                    'id': 'temp-vps',
                    'name': '临时-VPS-测试',
                    'source_type': 'vps',
                    'source_id': 'relay-a',
                    'source_name': '正式VPS线路',
                    'vless': {'reserve_slot': 'v01'},
                    'hy2': {'reserve_slot': 'h01'},
                    'expires_at': '2026-08-07T04:30:00+00:00',
                },
                {
                    'id': 'temp-upstream',
                    'name': '临时-动态代理-测试',
                    'source_type': 'upstream',
                    'source_id': 'upstream-a',
                    'source_name': '正式动态代理线路',
                    'vless': {'reserve_slot': 'v02'},
                    'hy2': None,
                    'expires_at': '2026-08-07T04:40:00+00:00',
                },
            ],
        }
        state_path = root / 'state.json'
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')

        vps_dir = root / 'vps'
        vps = run_render(state_path, 'temp-vps', vps_dir, manager)
        for expected in (
            '临时节点：临时-VPS-测试',
            '复制来源：正式VPS线路',
            '到期时间：2026-08-07T04:30:00+00:00',
            '【Quantumult X】',
            '【Loon】',
            '【Shadowrocket 分享链接】',
            '【NekoBox For Android】',
            '【Clash Verge Rev / Mihomo】',
            'sn://vmess?',
            'sn://hysteria?',
            'type: hysteria2',
            'ports: "443,20000-50000"',
        ):
            assert expected in vps, expected
        assert (vps_dir / '客户端节点.txt').read_text(encoding='utf-8') == vps

        upstream_dir = root / 'upstream'
        upstream = run_render(state_path, 'temp-upstream', upstream_dir, manager)
        for expected in (
            '临时节点：临时-动态代理-测试',
            '复制来源：正式动态代理线路',
            'UDP：服务器端拒绝，防止绕过上游出口',
            '【Quantumult X】',
            '【Loon】',
            '【Shadowrocket 分享链接】',
            '【NekoBox For Android】',
            '【Clash Verge Rev / Mihomo】',
            'sn://vmess?',
        ):
            assert expected in upstream, expected
        for forbidden in ('sn://hysteria?', 'type: hysteria2', 'hysteria2://'):
            assert forbidden not in upstream, forbidden

    print('PASS all four creation paths print complete supported client configurations')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / 'core-src'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adapters = load('client_adapters', CORE / 'client_adapters.py')
packages = load('client_package_renderer', CORE / 'client_package_renderer.py')


def sample_node():
    return {
        'name': 'JP-HY2-203.0.113.1:443', 'protocol': 'hysteria2',
        'server': '203.0.113.1', 'port': 443,
        'ports': '443,20000-50000', 'hop_interval_seconds': 30,
        'password': 'test-password', 'sni': 'jp-hy2.jp-relay.local',
        'obfs_password': 'test-obfs', 'pin': 'ab' * 32,
        'limit_mbps': 50, 'udp': True,
    }


def main():
    adapters.smoke_test()
    node = sample_node()
    loon = adapters.render('loon', [node]).strip()
    assert loon == (
        'JP-HY2-203.0.113.1:443 = Hysteria2,203.0.113.1,443,test-password,'
        'sni=jp-hy2.jp-relay.local,skip-cert-verify=true,fast-open=true,'
        'salamander-password=test-obfs,server-ports="443,20000-50000",udp=true,block-quic=true'
    ), loon
    assert ',"test-password",' not in loon
    assert 'salamander-password="test-obfs"' not in loon

    clash = adapters.render('clash', [node])
    assert 'port: 443' in clash
    assert 'ports: "443,20000-50000"' in clash
    assert 'hop-interval: 30' in clash

    shadow = adapters.render('shadowrocket-uri', [node]).strip()
    assert shadow.startswith('hysteria2://test-password@203.0.113.1:443,20000-50000/')
    neko = adapters.render('nekobox-uri', [node]).strip()
    assert neko.startswith('hy2://test-password@203.0.113.1:443,20000-50000/')
    assert adapters.render('shadowrocket', [node]).strip() != shadow

    state = {
        'schema': 4, 'role': 'japan-hub', 'protocol_mode': 'hy2',
        'public_ip': '203.0.113.1', 'listen_port': 443,
        'sni': 'www.softbank.jp', 'direct_base_name': 'JP-203.0.113.1:443',
        'hy2_limit_mbps': 50,
        'port_hopping': {'enabled': True, 'ports': '443,20000-50000', 'hop_interval_seconds': 30},
        'vless': None,
        'hy2': {
            'server_name': 'jp-hy2.jp-relay.local',
            'obfs_password': 'test-obfs',
            'certificate_pin_hex': 'ab' * 32,
            'certificate_fingerprint': 'AA:BB',
            'direct_user': {'name': 'direct', 'password': 'test-password'},
        },
        'relays': [], 'upstream_relays': [], 'temporary_nodes': [],
    }
    with tempfile.TemporaryDirectory(prefix='vvv-client-hop.') as directory:
        root = Path(directory)
        state_path = root / 'state.json'
        out = root / 'out'
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        out.mkdir()
        (out / 'Loon-Shadowrocket.txt').write_text('obsolete', encoding='utf-8')
        (out / 'NekoBoxForAndroid.yaml').write_text('obsolete', encoding='utf-8')
        packages.render_package(
            adapters,
            *packages.main_nodes(state, 'direct', ''),
            out,
        )
        assert not (out / 'Loon-Shadowrocket.txt').exists()
        assert not (out / 'NekoBoxForAndroid.yaml').exists()
        assert (out / 'NekoBoxForAndroid.txt').read_text(encoding='utf-8').startswith('hy2://')
        assert 'server-ports="443,20000-50000"' in (out / 'Loon.conf').read_text(encoding='utf-8')
        assert 'ports: "443,20000-50000"' in (out / 'Clash-Verge-Rev.yaml').read_text(encoding='utf-8')

    print('Client port hopping format tests passed.')


if __name__ == '__main__':
    main()

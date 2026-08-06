#!/usr/bin/env python3
import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path.cwd()
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
        'id': 'a' * 24,
        'subscription_url': 'https://sub.example.test/secret',
        'name': 'JP-HY2-203.0.113.1:443', 'protocol': 'hysteria2',
        'server': '203.0.113.1', 'port': 443,
        'ports': '443,20000-50000', 'hop_interval_seconds': 30,
        'password': 'test-password', 'sni': 'jp-hy2.jp-relay.local',
        'obfs_password': 'test-obfs', 'pin': 'ab' * 32,
        'limit_mbps': 50, 'client_up_mbps': 30, 'client_down_mbps': 50, 'udp': True,
    }


def main():
    adapters.smoke_test()
    node = sample_node()
    vless_sn_node = {
        'name': 'JP-VLESS-203.0.113.1:443', 'protocol': 'vless',
        'server': '203.0.113.1', 'port': 443,
        'uuid': '11111111-1111-4111-8111-111111111111',
        'sni': 'www.softbank.jp',
        'public_key': 'PublicKeyAudit-1234567890123456789012345678',
        'short_id': '0123456789abcdef', 'udp': True,
    }
    hy2_sn_node = dict(node, obfs_password='test-obfs-password')
    sn_links = adapters.render('nekobox-sn', [vless_sn_node, hy2_sn_node]).splitlines()
    assert sn_links == [
        'sn://vmess?eNpjYWBgMDIw1jPQMzQ01tu4m5GBYakhFOiCCRMQYQHjwkBFSU6xblFBUYVuWWZxZv67_0BQkvyhJOdzeXm5XnF-WklSYl62XtaHxkbG5Iyi_NynawJKk3Iyk71TKx1LUzJLdA2NjE1MzcwtLA0wWQihxKTklNRnDI1MDDDACMVeAbphPq7BwboI5xtamZhsbmwEAE3gPFY',
        'sn://hysteria?eNpjZ2BgMDIw1jPQMzQ01ttowcLAwMQAASWpxSW6BYnFxeX5RU_gAvlJacVw0awC3YxKIz0gVZSak1ipl5OfnPimUQ5kJhAzNjLAAEjIxMRYx8gACHRNgcQGRqCQV4CuR6SRLsIBhlYmJpsbGwGtkyOX',
    ], sn_links
    neko_subscription = json.loads(adapters.render('nekobox', [node]))
    assert list(neko_subscription) == ['outbounds']
    assert len(neko_subscription['outbounds']) == 1
    hy2_outbound = neko_subscription['outbounds'][0]
    assert hy2_outbound['type'] == 'hysteria2'
    assert hy2_outbound['server_ports'] == ['443', '20000:50000']
    assert hy2_outbound['hop_interval'] == '30s'
    assert hy2_outbound['up_mbps'] == 30 and hy2_outbound['down_mbps'] == 50
    assert hy2_outbound['obfs'] == {'type': 'salamander', 'password': 'test-obfs'}
    assert hy2_outbound['tls']['server_name'] == 'jp-hy2.jp-relay.local'
    loon = adapters.render('loon', [node]).strip()
    assert loon == (
        'JP-HY2-203.0.113.1:443 = Hysteria2,203.0.113.1,443,test-password,'
        'sni=jp-hy2.jp-relay.local,skip-cert-verify=true,fast-open=true,'
        'salamander-password=test-obfs,server-ports="443,20000-50000",'
        'hop-interval=30,udp=true,block-quic=true,download-bandwidth=50'
    ), loon

    clash = adapters.render('clash', [node])
    assert clash.startswith('proxies:\n')
    assert 'hop-interval: "20-30"' in clash
    assert 'up: "30 Mbps"' in clash and 'down: "50 Mbps"' in clash
    for forbidden in ('mixed-port:', 'proxy-groups:', 'rules:', 'fast-open: true'):
        assert forbidden not in clash, forbidden

    neko_yaml = adapters.render('nekobox-yaml', [node])
    assert 'hop-interval: 30' in neko_yaml
    assert 'hop-interval: "20-30"' not in neko_yaml
    assert 'up: "30 Mbps"' in neko_yaml and 'down: "50 Mbps"' in neko_yaml

    shadow = adapters.render('shadowrocket-uri', [node]).strip()
    assert shadow.startswith('hysteria2://test-password@203.0.113.1:443?')
    for value in ('peer=jp-hy2.jp-relay.local', 'fastopen=1', 'upmbps=30',
                  'downmbps=50', 'hpkp=' + 'ab' * 32, 'mport=443,20000-50000'):
        assert value in shadow, value
    assert 'hop-interval=' not in shadow and 'hopinterval=' not in shadow

    neko = adapters.render('nekobox-uri', [node]).strip()
    assert neko.startswith('hy2://test-password@203.0.113.1:443/?')
    assert ':443,20000-50000/' not in neko
    assert 'sni=jp-hy2.jp-relay.local' in neko
    assert 'mport=443,20000-50000' in neko

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
        out = Path(directory) / 'out'
        out.mkdir()
        packages.render_package(adapters, *packages.main_nodes(state, 'direct', ''), out)
        summary = (out / '客户端节点.txt').read_text(encoding='utf-8')
        assert '【Shadowrocket 分享链接】' in summary
        assert 'peer=jp-hy2.jp-relay.local' in summary
        assert 'hop-interval=30' in (out / 'Loon.conf').read_text(encoding='utf-8')
        assert 'hop-interval: "20-30"' in (out / 'Clash-Verge-Rev.yaml').read_text(encoding='utf-8')
        neko_yaml_file = (out / 'NekoBoxForAndroid.yaml').read_text(encoding='utf-8')
        assert 'hop-interval: 30' in neko_yaml_file
        assert 'hop-interval: "20-30"' not in neko_yaml_file
        assert '【NekoBox For Android】' in summary
        assert (out / 'NekoBoxForAndroid-SN.txt').read_text(encoding='utf-8').startswith('sn://hysteria?')
        labels = ['【Loon】', '【Shadowrocket 分享链接】', '【NekoBox For Android】',
                  '【Clash Verge Rev / Mihomo】']
        assert [summary.index(label) for label in labels] == sorted(summary.index(label) for label in labels)
        assert (out / 'NekoBoxForAndroid-基础URI.txt').read_text(encoding='utf-8').startswith('hy2://')

    print('Client Hysteria 2 compatibility tests passed.')


if __name__ == '__main__':
    main()

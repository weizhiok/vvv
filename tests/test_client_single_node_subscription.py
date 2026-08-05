#!/usr/bin/env python3
import importlib.util
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path.cwd()
CORE = ROOT / 'core-src'
sys.path.insert(0, str(CORE))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adapters = load('client_adapters_single', CORE / 'client_adapters.py')
center = load('sub_center_single', CORE / 'sub_center.py')


def node():
    return {
        'id': 'b' * 24, 'subscription_url': 'https://sub.example.test/secret',
        'name': 'JP-HY2-203.0.113.2:443', 'protocol': 'hysteria2',
        'server': '203.0.113.2', 'port': 443, 'ports': '443,20000-50000',
        'hop_interval_seconds': 30, 'password': 'password',
        'sni': 'jp-hy2.jp-relay.local', 'obfs_password': 'obfs',
        'pin': 'cd' * 32, 'client_up_mbps': 30, 'client_down_mbps': 50,
    }


def main():
    item = node()
    loon = adapters.render('loon-import', [item]).strip()
    assert loon.startswith('loon://import?nodelist=')
    target = unquote(loon.split('=', 1)[1])
    query = parse_qs(urlparse(target).query)
    assert query == {'format': ['loon'], 'node': ['b' * 24]}

    neko = adapters.render('nekobox-import', [item]).strip()
    query = parse_qs(urlparse(neko).query)
    assert query == {'format': ['nekobox'], 'node': ['b' * 24]}

    recognition, selected = center.resolve_subscription_request(
        {'User-Agent': 'Unknown'}, 'format=loon&node=' + 'b' * 24, [item]
    )
    assert recognition['format'] == 'loon' and selected == [item]
    recognition, selected = center.resolve_subscription_request(
        {'User-Agent': 'NekoBox/Android/1.4.2'}, 'format=nekobox&node=' + 'b' * 24, [item]
    )
    assert recognition['format'] == 'nekobox' and selected == [item]

    try:
        center.resolve_subscription_request({}, 'format=clash&node=' + 'b' * 24, [item])
    except ValueError:
        pass
    else:
        raise AssertionError('unsupported explicit format was accepted')

    try:
        center.resolve_subscription_request({}, 'format=loon&node=' + 'c' * 24, [item])
    except LookupError:
        pass
    else:
        raise AssertionError('unknown node id was accepted')

    print('Single-node subscription tests passed.')


if __name__ == '__main__':
    main()

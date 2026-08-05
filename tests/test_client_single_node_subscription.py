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



def test_registration_refreshes_local_imports():
    import tempfile
    sync = load('sync_agent_refresh', CORE / 'sync_agent.py')
    with tempfile.TemporaryDirectory(prefix='vvv-sync-refresh.') as td:
        root = Path(td)
        sync.CFG = root / 'client.json'
        sync.LOCAL_RENDERER = root / 'client_local_renderer.py'
        sync.LOCAL_RENDERER.write_text('# test renderer\n', encoding='utf-8')
        calls = []
        original_run = sync.subprocess.run
        sync.subprocess.run = lambda command, **kwargs: calls.append((command, kwargs))
        try:
            sync.save_registration(
                'direct', 'http://203.0.113.1:18081', 'http://203.0.113.1:18081',
                {'host_token': 'token', 'subscription_url': 'https://sub.example.test/secret'},
                'VVC1',
            )
        finally:
            sync.subprocess.run = original_run
        assert sync.CFG.is_file()
        assert len(calls) == 1 and calls[0][0][-1] == 'regenerate'


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

    test_registration_refreshes_local_imports()
    print('Single-node subscription tests passed.')


if __name__ == '__main__':
    main()

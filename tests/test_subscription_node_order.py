#!/usr/bin/env python3
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / 'core-src'
sys.path.insert(0, str(CORE))
spec = importlib.util.spec_from_file_location('sub_center', CORE / 'sub_center.py')
sub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sub)


def state(ip, suffix):
    return {
        'protocol_mode': 'vless', 'public_ip': ip, 'listen_port': 443,
        'sni': 'www.softbank.jp', 'direct_base_name': f'JP-{suffix}',
        'vless': {
            'reality': {'public_key': f'pk-{suffix}', 'short_id': '0123456789abcdef'},
            'direct_user': {'uuid': f'11111111-1111-4111-8111-{suffix:0>12}'},
        },
        'hy2': None, 'relays': [], 'upstream_relays': [], 'temporary_nodes': [],
    }


def expect_failure(fn, fragment):
    try:
        fn()
    except SystemExit as exc:
        assert fragment in str(exc), (fragment, str(exc))
    else:
        raise AssertionError('expected SystemExit')


def main():
    with tempfile.TemporaryDirectory(prefix='vvv-node-order.') as directory:
        root = Path(directory)
        sub.CFG = root / 'missing-config.json'
        sub.DATA = root / 'data'
        sub.HOSTS = sub.DATA / 'hosts'
        sub.OUT = sub.DATA / 'output'
        sub.REGISTRY = sub.DATA / 'registry.json'
        sub.OVERRIDES = sub.DATA / 'node-overrides.json'
        sub.ORDER = sub.DATA / 'node-order.json'
        sub.TICKETS = sub.DATA / 'relay-tickets.json'
        sub.BACKUP = root / 'missing-backup.py'
        sub.HOSTS.mkdir(parents=True)
        for index, (host_id, ip, suffix) in enumerate((('host-a', '203.0.113.1', 'A'), ('host-b', '203.0.113.2', 'B'))):
            doc = {
                'host_id': host_id, 'role': 'direct', 'state': state(ip, suffix),
                'states': {}, 'last_seen_ts': 0, 'last_seen': f't{index}',
            }
            (sub.HOSTS / f'{host_id}.json').write_text(json.dumps(doc), encoding='utf-8')

        nodes = sub.all_nodes()
        assert len(nodes) == 2
        original_ids = [node['id'] for node in nodes]
        original_names = [node['name'] for node in nodes]
        assert json.loads(sub.ORDER.read_text(encoding='utf-8'))['ids'] == original_ids

        count = sub.reorder_nodes(f'| {original_names[1]} || {original_names[0]} |')
        assert count == 2
        reordered = sub.all_nodes()
        assert [node['id'] for node in reordered] == list(reversed(original_ids))
        assert [node['name'] for node in reordered] == list(reversed(original_names))

        count = sub.bulk_rename('| 新加坡一号 || 日本二号 |')
        assert count == 2
        renamed = sub.all_nodes()
        assert [node['name'] for node in renamed] == ['新加坡一号', '日本二号']
        assert [node['id'] for node in renamed] == list(reversed(original_ids))
        assert (sub.OUT / 'loon').exists()
        assert (sub.OUT / 'nodes.json').exists()

        expect_failure(lambda: sub.bulk_rename('重复|重复'), '不能重复')
        expect_failure(lambda: sub.bulk_rename('只有一个'), '数量不一致')
        expect_failure(lambda: sub.rename_node(renamed[0]['id'], '包含|竖线'), '不能包含 |')
        expect_failure(lambda: sub.reorder_nodes('日本二号|不存在'), '不存在')

        # Deleting a host must prune its stable ID from the order file while
        # preserving the relative order of all remaining IDs.
        sub.atomic_json(sub.REGISTRY, {'hosts': [
            {'host_id': 'host-a', 'token': 'a'}, {'host_id': 'host-b', 'token': 'b'}
        ]})
        sub.delete_host('host-a')
        remaining = sub.all_nodes()
        assert len(remaining) == 1
        order_ids = json.loads(sub.ORDER.read_text(encoding='utf-8'))['ids']
        assert order_ids == [remaining[0]['id']]

    print('Subscription node order and bulk rename tests passed.')


if __name__ == '__main__':
    main()

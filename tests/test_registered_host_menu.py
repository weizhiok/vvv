#!/usr/bin/env python3
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / 'core-src'
sys.path.insert(0, str(CORE))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    center = load('sub_center_host_summary', CORE / 'sub_center.py')
    with tempfile.TemporaryDirectory(prefix='vvv-host-summary.') as td:
        root = Path(td)
        center.HOSTS = root / 'hosts'
        center.HOSTS.mkdir()

        center.atomic_json(center.HOSTS / 'center-main.json', {
            'host_id': 'center-main', 'role': 'center-relay',
            'state': {'public_ip': '82.22.26.244'},
            'last_seen': '2026-08-06T13:23:18.666829+00:00',
        })
        center.atomic_json(center.HOSTS / 'direct-node.json', {
            'host_id': 'direct-node', 'role': 'direct',
            'state': {'public_ip': '64.204.49.151'},
            'last_seen': '2026-08-06T12:28:00.072719+00:00',
        })
        center.atomic_json(center.HOSTS / 'legacy-node.json', {
            'host_id': 'legacy-node', 'role': 'direct', 'state': {},
        })
        center.atomic_json(center.HOSTS / 'landing-direct.json', {
            'host_id': 'landing-direct', 'role': 'landing-direct',
            'states': {'direct': {'public_ip': '203.0.113.9'}},
            'last_seen': '2026-08-05T01:02:03+00:00',
        })

        first = center.host_summary({
            'host_id': 'center-main', 'role': 'center-relay',
            'updated_at': '2026-08-06T13:23:18.666829+00:00',
        })
        second = center.host_summary({
            'host_id': 'direct-node', 'role': 'direct',
            'updated_at': '2026-08-06T12:28:00.072719+00:00',
        })
        legacy = center.host_summary({'host_id': 'legacy-node', 'role': 'direct'})
        landing = center.host_summary({'host_id': 'landing-direct', 'role': 'landing-direct'})
        assert first == {'host_id': 'center-main', 'role': 'center-relay', 'public_ip': '82.22.26.244', 'sync_date': '2026-08-06'}
        assert second == {'host_id': 'direct-node', 'role': 'direct', 'public_ip': '64.204.49.151', 'sync_date': '2026-08-06'}
        assert legacy['public_ip'] == '未知IP' and legacy['sync_date'] == '未知日期'
        assert landing['public_ip'] == '203.0.113.9' and landing['sync_date'] == '2026-08-05'

    manager = (CORE / 'center_manager.sh').read_text(encoding='utf-8')
    assert 'list-hosts --summary-tsv' in manager
    assert 'echo "$((i+1)). [$role] $host_ip $sync_date"' in manager
    assert 'host_index=$((count+3))' in manager
    assert 'echo "${host_index}. 已注册主机管理"' in manager
    assert 'if (( choice==host_index )); then\n      host_menu\n      continue' in manager
    assert manager.count('host_menu(){') == 1
    assert 'echo "9. 已注册主机管理"' in manager
    assert '删除节点' not in manager and 'delete-node' not in manager
    subprocess.run(['bash', '-n', str(CORE / 'center_manager.sh')], check=True)
    print('Registered host menu usability tests passed.')


if __name__ == '__main__':
    main()

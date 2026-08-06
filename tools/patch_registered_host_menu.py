#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path, old, new):
    path = ROOT / path
    text = path.read_text(encoding='utf-8')
    if new in text:
        return False
    if old not in text:
        raise SystemExit(f'expected block not found in {path}')
    path.write_text(text.replace(old, new, 1), encoding='utf-8')
    return True


replace_once(
    'core-src/center_manager.sh',
    '  local rows count choice node_id name action new_name bulk_index order_index input\n',
    '  local rows count choice node_id name action new_name bulk_index order_index host_index input\n',
)
replace_once(
    'core-src/center_manager.sh',
    '''    bulk_index=$((count+1)); order_index=$((count+2))
    echo "${bulk_index}. 批量重命名"
    echo "${order_index}. 重新排序"
    echo "0. 返回"
''',
    '''    bulk_index=$((count+1)); order_index=$((count+2)); host_index=$((count+3))
    echo "${bulk_index}. 批量重命名"
    echo "${order_index}. 重新排序"
    echo "${host_index}. 已注册主机管理"
    echo "0. 返回"
''',
)
replace_once(
    'core-src/center_manager.sh',
    '''    if (( choice==order_index )); then
      echo "请按目标顺序输入当前节点名称，使用一个或多个 | 分隔；名称必须完整且不能重复。"
      read -r -p "目标顺序：" input
      if python3 "$SUB" reorder-nodes "$input" >/dev/null; then
        echo "节点重新排序成功，共 ${count} 个节点。"
        echo "所有客户端订阅已按新顺序重新生成，请在客户端中手动刷新统一订阅地址。"
      fi
      pause; continue
    fi
    (( choice>=1 && choice<=count )) || { echo "请输入有效编号。"; continue; }
''',
    '''    if (( choice==order_index )); then
      echo "请按目标顺序输入当前节点名称，使用一个或多个 | 分隔；名称必须完整且不能重复。"
      read -r -p "目标顺序：" input
      if python3 "$SUB" reorder-nodes "$input" >/dev/null; then
        echo "节点重新排序成功，共 ${count} 个节点。"
        echo "所有客户端订阅已按新顺序重新生成，请在客户端中手动刷新统一订阅地址。"
      fi
      pause; continue
    fi
    if (( choice==host_index )); then
      host_menu
      continue
    fi
    (( choice>=1 && choice<=count )) || { echo "请输入有效编号。"; continue; }
''',
)
replace_once(
    'core-src/center_manager.sh',
    '  local rows count choice host_id role host name action confirm\n',
    '  local rows count choice host_id role host_ip sync_date action confirm\n',
)
replace_once(
    'core-src/center_manager.sh',
    '    mapfile -t rows < <(python3 "$SUB" list-hosts --tsv)\n',
    '    mapfile -t rows < <(python3 "$SUB" list-hosts --summary-tsv)\n',
)
replace_once(
    'core-src/center_manager.sh',
    '''    for ((i=0;i<count;i++)); do IFS=$'\\t' read -r host_id role host name _ <<<"${rows[$i]}"; echo "$((i+1)). ${name:-$host} [$role]"; done
''',
    '''    for ((i=0;i<count;i++)); do IFS=$'\\t' read -r host_id role host_ip sync_date <<<"${rows[$i]}"; echo "$((i+1)). [$role] $host_ip $sync_date"; done
''',
)
replace_once(
    'core-src/center_manager.sh',
    '''    IFS=$'\\t' read -r host_id role host name _ <<<"${rows[$((10#$choice-1))]}"
''',
    '''    IFS=$'\\t' read -r host_id role host_ip sync_date <<<"${rows[$((10#$choice-1))]}"
''',
)

replace_once(
    'core-src/sub_center.py',
    '''def list_hosts():
    registry = read_json(REGISTRY, {'hosts': []}) or {'hosts': []}
    return registry.get('hosts', [])


def show_host(host_id):
''',
    '''def list_hosts():
    registry = read_json(REGISTRY, {'hosts': []}) or {'hosts': []}
    return registry.get('hosts', [])


def host_summary(entry):
    host_id = str(entry.get('host_id') or '')
    doc = read_json(HOSTS / f'{host_id}.json', {}) or {}
    role = str(entry.get('role') or doc.get('role') or 'unknown')
    states = doc.get('states') or {}
    state = (states.get('direct') or doc.get('state') or {}) if role == 'landing-direct' else (doc.get('state') or states.get('direct') or {})
    raw_ip = (state.get('public_ip') or state.get('japan_public_ip') or
              state.get('remote_public_ip') or (doc.get('meta') or {}).get('public_ip') or '')
    try:
        public_ip = str(ipaddress.ip_address(str(raw_ip).strip()))
    except ValueError:
        public_ip = '未知IP'
    timestamp = str(entry.get('updated_at') or doc.get('last_seen') or entry.get('created_at') or '')
    matched = re.match(r'^(\\d{4}-\\d{2}-\\d{2})', timestamp)
    sync_date = matched.group(1) if matched else '未知日期'
    return {'host_id': host_id, 'role': role, 'public_ip': public_ip, 'sync_date': sync_date}


def show_host(host_id):
''',
)
replace_once(
    'core-src/sub_center.py',
    "    hosts = commands.add_parser('list-hosts'); hosts.add_argument('--tsv', action='store_true')\n",
    "    hosts = commands.add_parser('list-hosts'); hosts.add_argument('--tsv', action='store_true'); hosts.add_argument('--summary-tsv', action='store_true')\n",
)
replace_once(
    'core-src/sub_center.py',
    '''    elif args.command == 'list-hosts':
        rows = list_hosts()
        if args.tsv:
            for entry in rows:
                print(f"{entry.get('host_id','')}\\t{entry.get('role','')}\\t{entry.get('hostname','')}\\t{entry.get('display_name','')}\\t{entry.get('updated_at','')}")
        else:
            print(json.dumps({'hosts': rows}, ensure_ascii=False, indent=2))
''',
    '''    elif args.command == 'list-hosts':
        rows = list_hosts()
        if args.summary_tsv:
            for entry in rows:
                summary = host_summary(entry)
                print(f"{summary['host_id']}\\t{summary['role']}\\t{summary['public_ip']}\\t{summary['sync_date']}")
        elif args.tsv:
            for entry in rows:
                print(f"{entry.get('host_id','')}\\t{entry.get('role','')}\\t{entry.get('hostname','')}\\t{entry.get('display_name','')}\\t{entry.get('updated_at','')}")
        else:
            print(json.dumps({'hosts': rows}, ensure_ascii=False, indent=2))
''',
)

TEST = ROOT / 'tests/test_registered_host_menu.py'
TEST.write_text('''#!/usr/bin/env python3
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
    assert 'if (( choice==host_index )); then\\n      host_menu\\n      continue' in manager
    assert manager.count('host_menu(){') == 1
    assert 'echo "9. 已注册主机管理"' in manager
    assert '删除节点' not in manager and 'delete-node' not in manager
    subprocess.run(['bash', '-n', str(CORE / 'center_manager.sh')], check=True)
    print('Registered host menu usability tests passed.')


if __name__ == '__main__':
    main()
''', encoding='utf-8')

replace_once(
    'tests/final_runtime_validation.sh',
    '''  "$ROOT/tests/conformance.py" \\
  "$ROOT/tests/extract_manager_library.py" \\
''',
    '''  "$ROOT/tests/conformance.py" \\
  "$ROOT/tests/test_registered_host_menu.py" \\
  "$ROOT/tests/extract_manager_library.py" \\
''',
)
replace_once(
    'tests/final_runtime_validation.sh',
    '''python3 "$ROOT/tests/conformance.py"
python3 "$ROOT/tests/landing_direct_role_validation.py"
''',
    '''python3 "$ROOT/tests/conformance.py"
python3 "$ROOT/tests/test_registered_host_menu.py"
python3 "$ROOT/tests/landing_direct_role_validation.py"
''',
)

print('Registered host menu usability patch applied.')

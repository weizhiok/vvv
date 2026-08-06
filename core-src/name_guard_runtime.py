#!/usr/bin/env python3
"""Install VVV's global node-name guard without changing proxy state."""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

MANAGER_MARKER = '# VVV_GLOBAL_NAME_GUARD_V1'
CENTER_MARKER = '# VVV_GLOBAL_NAME_GUARD_V1'
DEFAULT_MANAGER = Path('/usr/local/sbin/jp-relay-manager')
DEFAULT_CENTER = Path('/usr/local/lib/vvv/sub_center.py')


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}：预期匹配 1 次，实际 {count} 次。')
    return text.replace(old, new, 1)


def manager_name_guard_block():
    return r'''reserve_unique_node_name() {
  local requested="$1" kind="${2:-node}" operation_id response
  operation_id="${kind}-$(openssl rand -hex 16)"
  response="$(python3 - "$requested" "$operation_id" <<'PY_RESERVE_UNIQUE_NAME'
import json, re, sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

requested, operation_id = sys.argv[1:]
try:
    cfg = json.loads(Path('/etc/vvv/client.json').read_text(encoding='utf-8'))
except Exception as exc:
    raise SystemExit(f'无法读取订阅中心注册信息：{exc}')
api = str(cfg.get('effective_api_base_url') or cfg.get('api_base_url') or '').rstrip('/')
token = str(cfg.get('host_token') or '')
host_id = str(cfg.get('host_id') or '')
if not api or not token or not host_id:
    raise SystemExit('当前主机尚未完成订阅中心注册，不能新建节点。')
payload = json.dumps({'host_id': host_id, 'name': requested, 'operation_id': operation_id}, ensure_ascii=False).encode()
request = Request(api + '/api/v1/reserve-name', data=payload, method='POST', headers={
    'Authorization': 'Bearer ' + token,
    'Content-Type': 'application/json',
    'User-Agent': 'VVV-NameGuard/1.0',
})
try:
    with urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode())
except HTTPError as exc:
    detail = exc.read().decode(errors='replace').strip()
    raise SystemExit(f'订阅中心拒绝名称分配（HTTP {exc.code}）：{detail or exc.reason}')
except (URLError, TimeoutError, OSError) as exc:
    raise SystemExit(f'无法连接订阅中心完成名称保护：{exc}')
allocated = str(result.get('allocated_name') or '')
reservation_id = str(result.get('reservation_id') or '')
if result.get('ok') is not True or not allocated or not reservation_id:
    raise SystemExit('订阅中心没有返回有效的唯一名称。')
if '\t' in allocated or '\n' in allocated or '\r' in allocated:
    raise SystemExit('订阅中心返回的名称包含非法控制字符。')
print(allocated + '\t' + reservation_id)
PY_RESERVE_UNIQUE_NAME
)" || return 1
  IFS=$'\t' read -r RESERVED_NODE_NAME RESERVED_NAME_RESERVATION <<<"$response"
  [[ -n "$RESERVED_NODE_NAME" && -n "$RESERVED_NAME_RESERVATION" ]] || {
    fail "订阅中心没有返回有效的唯一名称。"
    return 1
  }
  if [[ "$RESERVED_NODE_NAME" != "$requested" ]]; then
    echo "检测到订阅中心已存在名称：${requested}"
    echo "新节点已自动命名为：${RESERVED_NODE_NAME}"
  fi
}

local_node_name_count() {
  local name="$1"
  jq --arg n "$name" '[
    .relays[]?.name,
    .upstream_relays[]?.name,
    .temporary_nodes[]?.name
  ] | map(select(. == $n)) | length' "$STATE_FILE"
}

assert_new_node_name() {
  local name="$1" count
  count="$(local_node_name_count "$name")"
  (( count == 0 )) || {
    fail "唯一名称保护失败：分配后的名称“${name}”仍与本机节点重复，已拒绝覆盖。"
    return 1
  }
}
'''


def patched_manager_text(text):
    if MANAGER_MARKER in text:
        return text
    if '# VVV_CREATED_NODE_OUTPUT_V1' not in text:
        raise RuntimeError('中转管理器缺少创建后客户端输出补丁；请先升级客户端输出功能。')
    text = replace_once(
        text,
        '# VVV_CREATED_NODE_OUTPUT_V1\n\nRUN_MODE=',
        '# VVV_CREATED_NODE_OUTPUT_V1\n' + MANAGER_MARKER + '\n\nRUN_MODE=',
        '名称保护版本标记',
    )
    text = replace_once(
        text,
        '''show_created_client_config() {
  local kind="$1" item_id="$2"
  print_client_config "$kind" "$item_id"
  echo "已触发订阅中心同步，请在客户端中刷新统一订阅。"
}

allocate_test_port() {
''',
        '''show_created_client_config() {
  local kind="$1" item_id="$2"
  print_client_config "$kind" "$item_id"
  echo "已触发订阅中心同步，请在客户端中刷新统一订阅。"
}

''' + manager_name_guard_block() + '''
allocate_test_port() {
''',
        '统一名称保护函数',
    )
    text = replace_once(
        text,
        '''  require_relay_subscription_registration || return 1

  local count old relay_id now candidate test_vless test_hy2 remote_hy2 old_state
''',
        '''  require_relay_subscription_registration || return 1
  local requested_node_name="$node_name"
  reserve_unique_node_name "$node_name" relay || return 1
  node_name="$RESERVED_NODE_NAME"
  assert_new_node_name "$node_name" || return 1

  local count old relay_id now candidate test_vless test_hy2 remote_hy2 old_state
''',
        'VPS 中转唯一名称入口',
    )
    text = replace_once(
        text,
        '''  count="$(jq --arg n "$node_name" '[.relays[]|select(.name==$n)]|length' "$STATE_FILE")"
  (( count <= 1 )) || fail "状态中存在多个同名线路。"
''',
        '''  count="$(jq --arg n "$node_name" '[.relays[]|select(.name==$n)]|length' "$STATE_FILE")"
  (( count == 0 )) || fail "唯一名称保护失败：拒绝覆盖同名 VPS 中转线路。"
''',
        'VPS 中转禁止覆盖',
    )
    text = replace_once(
        text,
        '''  echo "上游代理验证成功，当前动态出口：${exit_ip}"

  count="$(jq --arg n "$node_name" '[.upstream_relays[]? | select(.name==$n)] | length' "$STATE_FILE")"
  (( count <= 1 )) || fail "状态中存在多个同名动态代理线路。"
''',
        '''  echo "上游代理验证成功，当前动态出口：${exit_ip}"
  local requested_node_name="$node_name"
  reserve_unique_node_name "$node_name" upstream || return 1
  node_name="$RESERVED_NODE_NAME"
  assert_new_node_name "$node_name" || return 1

  count="$(jq --arg n "$node_name" '[.upstream_relays[]? | select(.name==$n)] | length' "$STATE_FILE")"
  (( count == 0 )) || fail "唯一名称保护失败：拒绝覆盖同名动态代理线路。"
''',
        '动态代理唯一名称入口',
    )
    text = replace_once(
        text,
        '''  [[ -n "$custom_name" ]] || custom_name="临时-${source_name}-$(date +%H%M)"
  if [[ "$source_type" == vps ]]; then
''',
        '''  [[ -n "$custom_name" ]] || custom_name="临时-${source_name}-$(date +%H%M)"
  local requested_node_name="$custom_name"
  reserve_unique_node_name "$custom_name" temporary || return 1
  custom_name="$RESERVED_NODE_NAME"
  assert_new_node_name "$custom_name" || return 1
  if [[ "$source_type" == vps ]]; then
''',
        '临时节点唯一名称入口',
    )
    vps_prompt = '''    same_count="$(jq --arg n "$node_name" '[.relays[]|select(.name==$n)]|length' "$STATE_FILE")"
    if (( same_count == 0 )); then break; fi
    echo "检测到同名线路“${node_name}”。"
    echo "1. 覆盖原线路并复用原密钥"
    echo "2. 重新输入名称"
    read -r -p "请选择：" choice
    case "$choice" in 1) break;; 2) continue;; *) echo "请输入 1 或 2。";; esac
'''
    text = replace_once(text, vps_prompt, '    break\n', 'VPS 菜单取消覆盖选项')
    upstream_prompt = '''    same_count="$(jq --arg n "$node_name" '[.upstream_relays[]? | select(.name==$n)] | length' "$STATE_FILE")"
    if (( same_count == 0 )); then break; fi
    echo "检测到同名线路“${node_name}”。"
    echo "1. 覆盖原线路并复用原 VLESS UUID"
    echo "2. 重新输入名称"
    read -r -p "请选择：" overwrite_choice
    case "$overwrite_choice" in 1) break;; 2) continue;; *) echo "请输入 1 或 2。";; esac
'''
    text = replace_once(text, upstream_prompt, '    break\n', '动态代理菜单取消覆盖选项')
    text = text.replace('CURRENT_STEP="新建或覆盖中转线路"', 'CURRENT_STEP="新建中转线路"')
    text = text.replace('CURRENT_STEP="新建或覆盖 HTTP/HTTPS/SOCKS5 中转线路"', 'CURRENT_STEP="新建 HTTP/HTTPS/SOCKS5 中转线路"')
    text = text.replace('CURRENT_STEP="使用 ${command_name} 新建或覆盖动态代理线路"', 'CURRENT_STEP="使用 ${command_name} 新建动态代理线路"')
    return text


CENTER_HELPERS = r'''
def normalize_name_key(value):
    return unicodedata.normalize('NFKC', str(value or '').strip()).casefold()


def validate_requested_name(value):
    name = str(value or '').strip()
    if not (1 <= len(name) <= 64) or '|' in name or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError('名称必须是 1-64 个字符，且不能包含 |、换行或控制字符。')
    return name


def split_numbered_name(value):
    match = re.fullmatch(r'(.*?)(?:【([2-9][0-9]*)】)?', str(value))
    if not match:
        return str(value), None
    return match.group(1), int(match.group(2)) if match.group(2) else None


def numbered_name(base, number):
    suffix = f'【{number}】'
    return base[:max(1, 64 - len(suffix))] + suffix


def allocate_unique_name(requested, occupied_keys):
    requested = validate_requested_name(requested)
    if normalize_name_key(requested) not in occupied_keys:
        return requested
    base, existing_number = split_numbered_name(requested)
    number = (existing_number + 1) if existing_number else 2
    while number < 1000000:
        candidate = numbered_name(base, number)
        if normalize_name_key(candidate) not in occupied_keys:
            return candidate
        number += 1
    raise ValueError('可用名称编号已耗尽。')


def protocol_base_variants(value):
    name = str(value or '').strip()
    if not name:
        return set()
    result = {name}
    for suffix in ('-VLESS', '-HY2'):
        if name.endswith(suffix):
            result.add(name[:-len(suffix)])
    match = re.match(r'^([A-Z]{2})-(?:VLESS|HY2)-(.+)$', name)
    if match:
        result.add(f'{match.group(1)}-{match.group(2)}')
    return result


def raw_base_names_from_doc(doc):
    names = set()
    states = doc.get('states') or {}
    candidates = []
    if isinstance(doc.get('state'), dict):
        candidates.append(doc['state'])
    for state in states.values():
        if isinstance(state, dict):
            candidates.append(state)
    for state in candidates:
        for key in ('direct_base_name', 'node_name'):
            value = str(state.get(key) or '').strip()
            if value:
                names.add(value)
        for key in ('relays', 'upstream_relays', 'temporary_nodes'):
            for item in state.get(key) or []:
                value = str(item.get('subscription_name') or item.get('name') or '').strip()
                if value:
                    names.add(value)
    return names


def reservation_store():
    store = read_json(TICKETS, {'tickets': [], 'name_reservations': []}) or {}
    store.setdefault('tickets', [])
    store.setdefault('name_reservations', [])
    return store


def prune_name_reservations(store, current=None):
    current = time.time() if current is None else float(current)
    store['name_reservations'] = [
        item for item in store.get('name_reservations', [])
        if float(item.get('expires_ts') or 0) > current
    ]
    return store


def occupied_name_keys(store=None):
    keys = set()
    overrides = read_json(OVERRIDES, {}) or {}
    for host in active_hosts():
        for name in raw_base_names_from_doc(host):
            keys.add(normalize_name_key(name))
        for node in nodes_from_host(host):
            for name in protocol_base_variants(node.get('name')):
                keys.add(normalize_name_key(name))
            entry = overrides.get(node.get('id')) or {}
            for field in ('display_name', 'auto_display_name'):
                for name in protocol_base_variants(entry.get(field)):
                    keys.add(normalize_name_key(name))
    store = prune_name_reservations(store or reservation_store())
    for item in store.get('name_reservations', []):
        keys.add(normalize_name_key(item.get('allocated_name')))
    return keys


def reserve_node_name(host_id, requested, operation_id):
    host_id = str(host_id or '').strip()
    operation_id = str(operation_id or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9._-]{8,128}', operation_id):
        raise ValueError('名称预留操作 ID 无效。')
    requested = validate_requested_name(requested)
    with LOCK:
        store = prune_name_reservations(reservation_store())
        existing = next((item for item in store['name_reservations']
                         if item.get('host_id') == host_id and item.get('operation_id') == operation_id), None)
        if existing:
            atomic_json(TICKETS, store)
            return existing
        allocated = allocate_unique_name(requested, occupied_name_keys(store))
        item = {
            'reservation_id': secrets.token_urlsafe(24),
            'host_id': host_id,
            'operation_id': operation_id,
            'requested_name': requested,
            'allocated_name': allocated,
            'created_at': now(),
            'expires_ts': time.time() + 600,
        }
        store['name_reservations'].append(item)
        atomic_json(TICKETS, store)
        return item


def release_node_name(host_id, reservation_id):
    with LOCK:
        store = prune_name_reservations(reservation_store())
        before = len(store['name_reservations'])
        store['name_reservations'] = [
            item for item in store['name_reservations']
            if not (item.get('host_id') == host_id and item.get('reservation_id') == reservation_id)
        ]
        atomic_json(TICKETS, store)
        return len(store['name_reservations']) != before


def consume_name_reservations(host_id, doc):
    present = {normalize_name_key(name) for name in raw_base_names_from_doc(doc)}
    if not present:
        return
    with LOCK:
        store = prune_name_reservations(reservation_store())
        store['name_reservations'] = [
            item for item in store['name_reservations']
            if not (item.get('host_id') == host_id and normalize_name_key(item.get('allocated_name')) in present)
        ]
        atomic_json(TICKETS, store)


def ensure_unique_node_names(nodes, overrides):
    updated = {str(key): dict(value or {}) for key, value in overrides.items()}
    claimed = set()
    manual_ids = set()
    for node in nodes:
        node_id_value = str(node.get('id') or '')
        entry = updated.get(node_id_value) or {}
        manual = str(entry.get('display_name') or '').strip()
        if not manual:
            continue
        final = allocate_unique_name(manual, claimed)
        if final != node.get('name'):
            node.setdefault('default_name', node.get('name'))
            node['name'] = final
        claimed.add(normalize_name_key(final))
        manual_ids.add(node_id_value)
    for node in nodes:
        node_id_value = str(node.get('id') or '')
        if node_id_value in manual_ids:
            continue
        entry = updated.setdefault(node_id_value, {})
        desired = str(node.get('name') or '').strip()
        automatic = str(entry.get('auto_display_name') or '').strip()
        candidate = automatic if automatic and normalize_name_key(automatic) not in claimed else allocate_unique_name(desired, claimed)
        claimed.add(normalize_name_key(candidate))
        if candidate != desired:
            node.setdefault('default_name', desired)
            node['name'] = candidate
            entry['auto_display_name'] = candidate
            entry['auto_updated_at'] = now()
        else:
            entry.pop('auto_display_name', None)
            entry.pop('auto_updated_at', None)
        if not entry:
            updated.pop(node_id_value, None)
    if updated != overrides:
        atomic_json(OVERRIDES, updated)
    return nodes
'''


def patched_sub_center_text(text):
    if CENTER_MARKER in text:
        return text
    text = replace_once(text, '#!/usr/bin/env python3\n', '#!/usr/bin/env python3\n' + CENTER_MARKER + '\n', '订阅中心版本标记')
    text = replace_once(text, 'import time\n', 'import time\nimport unicodedata\n', '订阅中心 Unicode 导入')
    text = replace_once(
        text,
        '''def node_id(host_id, kind, key):
    return hashlib.sha256(f'{host_id}|{kind}|{key}'.encode()).hexdigest()[:24]


def nodes_from_host(doc):
''',
        '''def node_id(host_id, kind, key):
    return hashlib.sha256(f'{host_id}|{kind}|{key}'.encode()).hexdigest()[:24]


''' + CENTER_HELPERS + '''

def nodes_from_host(doc):
''',
        '订阅中心名称保护函数',
    )
    text = replace_once(
        text,
        '''    nodes.sort(key=lambda node: positions.get(node['id'], len(positions)))
    return nodes
''',
        '''    nodes.sort(key=lambda node: positions.get(node['id'], len(positions)))
    return ensure_unique_node_names(nodes, overrides)
''',
        '订阅输出最终重名兜底',
    )
    text = replace_once(
        text,
        '''    atomic_json(HOSTS / f"{entry['host_id']}.json", doc)
    count = regenerate()
''',
        '''    atomic_json(HOSTS / f"{entry['host_id']}.json", doc)
    consume_name_reservations(entry['host_id'], doc)
    count = regenerate()
''',
        '注册时消费名称预留',
    )
    text = replace_once(
        text,
        '''            if path == '/api/v1/sync':
''',
        '''            if path == '/api/v1/reserve-name':
                host_id = str(body.get('host_id') or '').strip()
                entry = next((item for item in registry['hosts'] if item.get('host_id') == host_id), None)
                if entry is None or not secrets.compare_digest(auth_token(self), str(entry.get('token') or '')):
                    return self.send_bytes(403, b'Forbidden\\n')
                try:
                    item = reserve_node_name(host_id, body.get('name'), body.get('operation_id'))
                except ValueError as exc:
                    return self.send_bytes(400, (str(exc) + '\\n').encode(), 'text/plain; charset=utf-8')
                result = {'ok': True, 'allocated_name': item['allocated_name'],
                          'reservation_id': item['reservation_id'],
                          'renamed': item['allocated_name'] != item['requested_name']}
                return self.send_bytes(200, json.dumps(result, ensure_ascii=False).encode(), 'application/json')
            if path == '/api/v1/release-name':
                host_id = str(body.get('host_id') or '').strip()
                entry = next((item for item in registry['hosts'] if item.get('host_id') == host_id), None)
                if entry is None or not secrets.compare_digest(auth_token(self), str(entry.get('token') or '')):
                    return self.send_bytes(403, b'Forbidden\\n')
                released = release_node_name(host_id, str(body.get('reservation_id') or ''))
                return self.send_bytes(200, json.dumps({'ok': True, 'released': released}).encode(), 'application/json')
            if path == '/api/v1/sync':
''',
        '订阅中心名称预留 API',
    )
    text = replace_once(
        text,
        '''                atomic_json(HOSTS / f'{host_id}.json', doc)
                entry['updated_at'] = now()
''',
        '''                atomic_json(HOSTS / f'{host_id}.json', doc)
                consume_name_reservations(host_id, doc)
                entry['updated_at'] = now()
''',
        '同步时消费名称预留',
    )
    text = replace_once(
        text,
        '''    if not ORDER.exists():
        atomic_json(ORDER, {'schema': 1, 'ids': []})
    regenerate()
''',
        '''    if not ORDER.exists():
        atomic_json(ORDER, {'schema': 1, 'ids': []})
    if not TICKETS.exists():
        atomic_json(TICKETS, {'tickets': [], 'name_reservations': []})
    regenerate()
''',
        '订阅中心名称预留存储初始化',
    )
    return text


def atomic_transform(path, transform, syntax_command, required=False):
    path = Path(path)
    if not path.is_file():
        if required:
            raise RuntimeError(f'未找到文件：{path}')
        return False
    original = path.read_text(encoding='utf-8')
    updated = transform(original)
    if updated == original:
        return False
    stat = path.stat()
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, stat.st_mode & 0o777 or 0o700)
        try:
            os.chown(temporary, stat.st_uid, stat.st_gid)
        except PermissionError:
            pass
        subprocess.run([*syntax_command, temporary], check=True)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return True


def apply_installed(manager=DEFAULT_MANAGER, sub_center=DEFAULT_CENTER, restart_center=True, required=False):
    manager_changed = atomic_transform(manager, patched_manager_text, ['bash', '-n'], required=required)
    center_changed = atomic_transform(sub_center, patched_sub_center_text, ['python3', '-m', 'py_compile'], required=required)
    if center_changed and restart_center:
        active = subprocess.run(['systemctl', 'is-active', '--quiet', 'vvv-sub.service'], check=False).returncode == 0
        if active:
            subprocess.run(['systemctl', 'restart', 'vvv-sub.service'], check=True)
            subprocess.run(['systemctl', 'is-active', '--quiet', 'vvv-sub.service'], check=True)
    return {'manager_changed': manager_changed, 'center_changed': center_changed}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manager', default=str(DEFAULT_MANAGER))
    parser.add_argument('--sub-center', default=str(DEFAULT_CENTER))
    parser.add_argument('--no-restart-center', action='store_true')
    parser.add_argument('--required', action='store_true')
    args = parser.parse_args()
    result = apply_installed(
        Path(args.manager), Path(args.sub_center),
        restart_center=not args.no_restart_center,
        required=args.required,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()

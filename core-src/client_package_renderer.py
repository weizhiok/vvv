#!/usr/bin/env python3
"""Render one VVV client package and migrate the installed relay manager safely."""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

DEFAULT_ADAPTER = Path('/usr/local/lib/vvv/client_adapters.py')
DEFAULT_MANAGER = Path(os.environ.get('VVV_MANAGER_PATH', '/usr/local/sbin/jp-relay-manager'))
CLIENT_CFG = Path('/etc/vvv/client.json')
OBSOLETE_OUTPUTS = ('Loon-Shadowrocket.txt',)
MANAGER_PATCH_MARKER = '# VVV_CREATED_NODE_OUTPUT_V1'


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}：预期匹配 1 次，实际 {count} 次。')
    return text.replace(old, new, 1)


def patched_manager_text(text):
    if MANAGER_PATCH_MARKER in text:
        return text
    text = replace_once(
        text,
        'umask 077\n\nRUN_MODE=',
        f'umask 077\n{MANAGER_PATCH_MARKER}\n\nRUN_MODE=',
        '管理器版本标记',
    )
    text = replace_once(
        text,
        '''generate_direct_client_files() {
  local dir="/root/日本VPS-直连客户端配置"
  generate_client_files "$STATE_FILE" "" "$dir" direct
  cp -f "$dir/客户端节点.txt" /root/日本VPS-客户端节点.txt
  chmod 600 /root/日本VPS-客户端节点.txt
}

allocate_test_port() {
''',
        '''generate_direct_client_files() {
  local dir="/root/日本VPS-直连客户端配置"
  generate_client_files "$STATE_FILE" "" "$dir" direct
  cp -f "$dir/客户端节点.txt" /root/日本VPS-客户端节点.txt
  chmod 600 /root/日本VPS-客户端节点.txt
}

print_client_config() {
  local kind="$1" item_id="$2" dir transient=0
  case "$kind" in
    relay|upstream)
      dir="${PACKAGE_ROOT}/${item_id}"
      ;;
    temporary)
      dir="$(mktemp -d /tmp/vvv-created-client.XXXXXX)"
      TMP_FILES+=("$dir")
      transient=1
      ;;
    *)
      fail "未知客户端配置类型：${kind}"
      return 1
      ;;
  esac
  generate_client_files "$STATE_FILE" "$item_id" "$dir" "$kind" >/dev/null
  echo
  echo "==================== 客户端配置 ===================="
  cat "$dir/客户端节点.txt"
  echo "===================================================="
  if (( transient == 0 )); then
    echo "配置目录：$dir"
  else
    rm -rf -- "$dir"
  fi
}

show_created_client_config() {
  local kind="$1" item_id="$2"
  print_client_config "$kind" "$item_id"
  echo "已触发订阅中心同步，请在客户端中刷新统一订阅。"
}

allocate_test_port() {
''',
        '统一客户端配置打印函数',
    )
    text = replace_once(
        text,
        '''  echo "线路已通过运行时接口生效；Xray 主进程未重启。"
  echo "客户端配置目录：${package_dir}"
  echo
  echo "==================== 落地 VPS JPR3 对接密钥 ===================="
''',
        '''  echo "线路已通过运行时接口生效；Xray 主进程未重启。"
  show_created_client_config relay "$relay_id"
  echo
  echo "==================== 落地 VPS JPR3 对接密钥 ===================="
''',
        '新建 VPS 输出',
    )
    text = replace_once(
        text,
        '''  log "动态代理中转线路配置成功"
  show_upstream_client_config "$upstream_id"
  refresh_upstream_status "$upstream_id" || true
''',
        '''  log "动态代理中转线路配置成功"
  show_created_client_config upstream "$upstream_id"
  refresh_upstream_status "$upstream_id" || true
''',
        '新建动态代理输出',
    )
    text = replace_once(
        text,
        '''show_client_config() {
  local relay_id="$1" dir="${PACKAGE_ROOT}/${relay_id}"
  generate_client_files "$STATE_FILE" "$relay_id" "$dir" relay >/dev/null
  echo
  echo "==================== 客户端配置 ===================="
  cat "$dir/客户端节点.txt"
  echo "===================================================="
  echo "配置目录：$dir"
}

show_upstream_client_config() {
  local upstream_id="$1" dir="${PACKAGE_ROOT}/${upstream_id}"
  generate_client_files "$STATE_FILE" "$upstream_id" "$dir" upstream >/dev/null
  echo
  echo "==================== 客户端配置 ===================="
  cat "$dir/客户端节点.txt"
  echo "===================================================="
  echo "配置目录：$dir"
}
''',
        '''show_client_config() {
  print_client_config relay "$1"
}

show_upstream_client_config() {
  print_client_config upstream "$1"
}
''',
        '已有线路配置菜单',
    )
    text = replace_once(
        text,
        '''  apply_candidate_with_rollback "$candidate"
  install_temp_cleanup_timer
  echo "临时节点创建成功：${custom_name}"
  echo "自动销毁时间：${expires_at}（${ttl} 分钟后）"
  echo "副机和原正式线路均未修改。客户端刷新订阅后即可看到临时节点。"
''',
        '''  apply_candidate_with_rollback "$candidate"
  install_temp_cleanup_timer
  echo "临时节点创建成功：${custom_name}"
  echo "自动销毁时间：${expires_at}（${ttl} 分钟后）"
  echo "副机和原正式线路均未修改。"
  show_created_client_config temporary "$temp_id"
''',
        '临时节点输出',
    )
    return text


def install_manager_patch(path=DEFAULT_MANAGER, required=False):
    path = Path(path)
    if not path.is_file():
        if required:
            raise RuntimeError(f'未找到中转管理器：{path}')
        return False
    original = path.read_text(encoding='utf-8')
    if MANAGER_PATCH_MARKER in original:
        return False
    updated = patched_manager_text(original)
    mode = path.stat().st_mode & 0o777
    uid = path.stat().st_uid
    gid = path.stat().st_gid
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode or 0o700)
        try:
            os.chown(temporary, uid, gid)
        except PermissionError:
            pass
        subprocess.run(['bash', '-n', temporary], check=True)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return True


def load_adapter(path):
    spec = importlib.util.spec_from_file_location('vvv_package_adapter', str(path))
    module = importlib.util.module_from_spec(spec)
    if not spec.loader:
        raise RuntimeError('无法加载客户端渲染模块。')
    spec.loader.exec_module(module)
    module.smoke_test()
    return module


def read_state(path):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise RuntimeError('状态文件不是 JSON 对象。')
    return value


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default


def subscription_node_id(host_id, protocol, stable_key):
    kind = 'hy2' if protocol == 'hysteria2' else 'vless'
    return hashlib.sha256(f'{host_id}|{kind}|{stable_key}'.encode()).hexdigest()[:24]


def decorate_subscription(nodes, stable_key):
    cfg = read_json(CLIENT_CFG, {}) or {}
    host_id = str(cfg.get('host_id') or '').strip()
    subscription_url = str(cfg.get('subscription_url') or '').strip()
    if not host_id or not subscription_url:
        return nodes
    for node in nodes:
        node['id'] = subscription_node_id(host_id, node.get('protocol'), stable_key)
        node['subscription_url'] = subscription_url
    return nodes


def protocol_name(base, proto):
    match = re.match(r'^([A-Z]{2})-(.+)$', str(base or ''))
    if match:
        return f'{match.group(1)}-{proto}-{match.group(2)}'
    return f'{proto}-{base}' if re.fullmatch(r'[^:]+:\d+', str(base or '')) else f'{base}-{proto}'


def hopping(state):
    item = state.get('port_hopping') or state.get('japan_port_hopping') or {}
    port = int(state.get('listen_port') or state.get('japan_port') or 0)
    return str(item.get('ports') or port), int(item.get('hop_interval_seconds') or 30)


def vless_node(base, state, uuid, udp=True):
    vless = state.get('vless') or {}
    reality = vless.get('reality') or {}
    return {
        'name': protocol_name(base, 'VLESS'), 'protocol': 'vless',
        'server': state['public_ip'], 'port': int(state['listen_port']), 'uuid': uuid,
        'sni': state['sni'], 'public_key': reality['public_key'],
        'short_id': reality['short_id'], 'udp': bool(udp),
    }


def hy2_node(base, state, password):
    hy2 = state.get('hy2') or {}
    ports, interval = hopping(state)
    return {
        'name': protocol_name(base, 'HY2'), 'protocol': 'hysteria2',
        'server': state['public_ip'], 'port': int(state['listen_port']),
        'ports': ports, 'hop_interval_seconds': interval, 'password': password,
        'sni': hy2['server_name'], 'obfs_password': hy2['obfs_password'],
        'pin': hy2.get('certificate_pin_hex', ''),
        'fingerprint': hy2.get('certificate_fingerprint', ''),
        'limit_mbps': int(state.get('hy2_limit_mbps') or 50),
        'client_up_mbps': 30, 'client_down_mbps': 50, 'udp': True,
    }


def main_nodes(state, kind, item_id):
    mode = state.get('protocol_mode')
    relay = None
    upstream = None
    if kind == 'direct':
        base = state.get('direct_base_name') or f"{state['public_ip']}:{state['listen_port']}"
        v_uuid = ((state.get('vless') or {}).get('direct_user') or {}).get('uuid') if mode in ('dual', 'vless') else None
        h_password = ((state.get('hy2') or {}).get('direct_user') or {}).get('password') if mode in ('dual', 'hy2') else None
        title = '日本 VPS 直连节点'
        metadata = [f"日本入口：{state['public_ip']}:{state['listen_port']}", f'安装模式：{mode}']
        udp = True
        stable_key = 'direct'
    elif kind == 'relay':
        relay = next(row for row in state.get('relays', []) if row.get('id') == item_id)
        raw_name = str(relay.get('name') or '')
        country = raw_name[:2].upper() if len(raw_name) >= 3 and raw_name[:2].isalpha() and raw_name[2] == '-' else ''
        base = (country + '-' if country else '') + f"中转-{state['public_ip']}:{state['listen_port']}"
        v_uuid = (relay.get('vless') or {}).get('client_uuid')
        h_password = (relay.get('hy2') or {}).get('client_password')
        title = f"中转节点：{relay.get('name') or item_id}"
        metadata = [
            f"日本入口：{state['public_ip']}:{state['listen_port']}",
            f"最终落地：{relay.get('remote_ip')}:{relay.get('remote_port')}",
            f'安装模式：{mode}',
        ]
        udp = True
        stable_key = item_id
    elif kind == 'upstream':
        upstream = next(row for row in state.get('upstream_relays', []) if row.get('id') == item_id)
        base = upstream.get('name') or item_id
        v_uuid = upstream.get('client_uuid')
        h_password = None
        title = f'动态代理中转节点：{base}'
        metadata = [
            f"日本入口：{state['public_ip']}:{state['listen_port']}",
            f"上游代理：{upstream.get('protocol_label')} {upstream.get('host')}:{upstream.get('port')}",
            'UDP：服务器端拒绝，防止绕过上游出口',
        ]
        udp = False
        stable_key = item_id
    else:
        raise RuntimeError(f'未知客户端配置类型：{kind}')
    nodes = []
    if v_uuid:
        nodes.append(vless_node(base, state, v_uuid, udp))
    if h_password:
        nodes.append(hy2_node(base, state, h_password))
    decorate_subscription(nodes, stable_key)
    if h_password:
        ports, interval = hopping(state)
        metadata.append(f'Hysteria 2 端口跳跃：{ports}（每 {interval} 秒切换）')
        metadata.append(f"Hysteria 2 服务端硬上限：上行 {int(state.get('hy2_limit_mbps') or 50)} Mbps / 下行 {int(state.get('hy2_limit_mbps') or 50)} Mbps")
    return title, metadata, nodes


def slot_value(items, slot, key):
    for item in items or []:
        if item.get('slot') == slot:
            return item.get(key)
    return None


def temporary_nodes(state, item_id):
    temp = next(row for row in state.get('temporary_nodes', []) if row.get('id') == item_id)
    source_type = temp.get('source_type')
    if source_type not in ('vps', 'upstream'):
        raise RuntimeError(f'临时节点来源类型无效：{source_type}')
    vless = temp.get('vless') or {}
    hy2 = temp.get('hy2') or {}
    v_uuid = vless.get('client_uuid') or slot_value(
        (state.get('vless') or {}).get('reserve_users'), vless.get('reserve_slot'), 'uuid'
    )
    h_password = None
    if source_type == 'vps':
        h_password = hy2.get('client_password') or slot_value(
            (state.get('hy2') or {}).get('reserve_users'), hy2.get('reserve_slot'), 'password'
        )
    base = temp.get('name') or item_id
    metadata = [
        f"日本入口：{state['public_ip']}:{state['listen_port']}",
        f"复制来源：{temp.get('source_name') or temp.get('source_id')}",
        f"到期时间：{temp.get('expires_at', '未知')}",
    ]
    if source_type == 'upstream':
        metadata.append('UDP：服务器端拒绝，防止绕过上游出口')
    nodes = []
    if v_uuid:
        nodes.append(vless_node(base, state, v_uuid, source_type != 'upstream'))
    if h_password:
        nodes.append(hy2_node(base, state, h_password))
    if not nodes:
        raise RuntimeError(f'临时节点 {item_id} 没有可用客户端凭据。')
    decorate_subscription(nodes, item_id)
    if h_password:
        ports, interval = hopping(state)
        metadata.append(f'Hysteria 2 端口跳跃：{ports}（每 {interval} 秒切换）')
        metadata.append(
            f"Hysteria 2 服务端硬上限：上行 {int(state.get('hy2_limit_mbps') or 50)} Mbps / "
            f"下行 {int(state.get('hy2_limit_mbps') or 50)} Mbps"
        )
    return f'临时节点：{base}', metadata, nodes


def landing_nodes(state):
    mode = state.get('protocol_mode')
    raw_name = str(state.get('node_name') or '')
    country = raw_name[:2].upper() if len(raw_name) >= 3 and raw_name[:2].isalpha() and raw_name[2] == '-' else ''
    base = (country + '-' if country else '') + f"中转-{state['japan_public_ip']}:{state['japan_port']}"
    fake = {
        'public_ip': state['japan_public_ip'], 'listen_port': int(state['japan_port']),
        'sni': state['sni'], 'hy2_limit_mbps': int(state.get('hy2_limit_mbps') or 50),
        'port_hopping': state.get('japan_port_hopping') or {},
        'vless': {'reality': {
            'public_key': (state.get('vless') or {}).get('japan_reality_public_key'),
            'short_id': (state.get('vless') or {}).get('japan_reality_short_id'),
        }},
        'hy2': {
            'server_name': (state.get('hy2') or {}).get('japan_server_name'),
            'obfs_password': (state.get('hy2') or {}).get('japan_obfs_password'),
            'certificate_pin_hex': (state.get('hy2') or {}).get('japan_certificate_pin_hex', ''),
            'certificate_fingerprint': (state.get('hy2') or {}).get('japan_certificate_fingerprint', ''),
        },
    }
    nodes = []
    if mode in ('dual', 'vless'):
        nodes.append(vless_node(base, fake, (state.get('vless') or {}).get('japan_client_uuid'), True))
    if mode in ('dual', 'hy2'):
        nodes.append(hy2_node(base, fake, (state.get('hy2') or {}).get('japan_client_password')))
    ports, interval = hopping(fake)
    metadata = [
        f'线路：{base}',
        f"日本入口：{state['japan_public_ip']}:{state['japan_port']}",
        f"最终落地：{state.get('remote_public_ip')}:{state.get('remote_public_port')}",
        f'协议模式：{mode}',
    ]
    if mode in ('dual', 'hy2'):
        metadata.append(f'Hysteria 2 端口跳跃：{ports}（每 {interval} 秒切换）')
    return '中转客户端节点', metadata, nodes


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def render_package(adapter, title, metadata, nodes, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    outputs = adapter.local_outputs()
    rendered = {}
    for row in outputs:
        rendered[row['filename']] = adapter.render(row['format'], nodes)
        atomic_write(out / row['filename'], rendered[row['filename']])
    for name in OBSOLETE_OUTPUTS:
        (out / name).unlink(missing_ok=True)
    lines = [title, '=' * 36, *metadata]
    for row in outputs:
        content = rendered[row['filename']]
        if content.strip() and row.get('display', True):
            lines += ['', f"【{row.get('display_name') or row['filename']}】", content.rstrip()]
    summary = '\n'.join(lines).rstrip() + '\n'
    atomic_write(out / '客户端节点.txt', summary)
    print(summary, end='')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--upgrade-manager-only', action='store_true')
    parser.add_argument('--manager-path', default=str(DEFAULT_MANAGER))
    parser.add_argument('--state')
    parser.add_argument('--kind', choices=('direct', 'relay', 'upstream', 'temporary', 'landing'))
    parser.add_argument('--id', default='')
    parser.add_argument('--out')
    parser.add_argument('--adapter', default=str(DEFAULT_ADAPTER))
    args = parser.parse_args()
    manager_path = Path(args.manager_path)
    if args.upgrade_manager_only:
        changed = install_manager_patch(manager_path, required=True)
        print('中转管理器已升级。' if changed else '中转管理器已经是最新版本。')
        return
    for name in ('state', 'kind', 'out'):
        if not getattr(args, name):
            parser.error(f'--{name.replace("_", "-")} is required')
    install_manager_patch(manager_path, required=False)
    state = read_state(args.state)
    adapter = load_adapter(Path(args.adapter))
    if args.kind == 'landing':
        title, metadata, nodes = landing_nodes(state)
    elif args.kind == 'temporary':
        title, metadata, nodes = temporary_nodes(state, args.id)
    else:
        title, metadata, nodes = main_nodes(state, args.kind, args.id)
    render_package(adapter, title, metadata, nodes, args.out)


if __name__ == '__main__':
    main()

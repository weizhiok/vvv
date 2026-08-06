#!/usr/bin/env python3
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "core-src/host.sh"
RENDERER = ROOT / "core-src/client_package_renderer.py"
FINAL = ROOT / "tests/final_runtime_validation.sh"
TEST = ROOT / "tests/test_created_node_client_output.py"
UPGRADE = ROOT / "upgrade/upgrade-created-node-client-output.sh"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


renderer = RENDERER.read_text(encoding="utf-8")
renderer = replace_once(
    renderer,
    """def landing_nodes(state):\n""",
    """def slot_value(items, slot, key):
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
""",
    "add temporary renderer",
)
renderer = replace_once(
    renderer,
    """    parser.add_argument('--kind', choices=('direct', 'relay', 'upstream', 'landing'), required=True)\n""",
    """    parser.add_argument('--kind', choices=('direct', 'relay', 'upstream', 'temporary', 'landing'), required=True)\n""",
    "add temporary CLI choice",
)
renderer = replace_once(
    renderer,
    """    if args.kind == 'landing':
        title, metadata, nodes = landing_nodes(state)
    else:
        title, metadata, nodes = main_nodes(state, args.kind, args.id)
""",
    """    if args.kind == 'landing':
        title, metadata, nodes = landing_nodes(state)
    elif args.kind == 'temporary':
        title, metadata, nodes = temporary_nodes(state, args.id)
    else:
        title, metadata, nodes = main_nodes(state, args.kind, args.id)
""",
    "route temporary renderer",
)
RENDERER.write_text(renderer, encoding="utf-8")

host = HOST.read_text(encoding="utf-8")
host = replace_once(
    host,
    """generate_direct_client_files() {
  local dir="/root/日本VPS-直连客户端配置"
  generate_client_files "$STATE_FILE" "" "$dir" direct
  cp -f "$dir/客户端节点.txt" /root/日本VPS-客户端节点.txt
  chmod 600 /root/日本VPS-客户端节点.txt
}

allocate_test_port() {
""",
    """generate_direct_client_files() {
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
""",
    "add common created-node output",
)
host = replace_once(
    host,
    """  echo "线路已通过运行时接口生效；Xray 主进程未重启。"
  echo "客户端配置目录：${package_dir}"
  echo
  echo "==================== 落地 VPS JPR3 对接密钥 ===================="
""",
    """  echo "线路已通过运行时接口生效；Xray 主进程未重启。"
  show_created_client_config relay "$relay_id"
  echo
  echo "==================== 落地 VPS JPR3 对接密钥 ===================="
""",
    "print new VPS clients",
)
host = replace_once(
    host,
    """  log "动态代理中转线路配置成功"
  show_upstream_client_config "$upstream_id"
  refresh_upstream_status "$upstream_id" || true
""",
    """  log "动态代理中转线路配置成功"
  show_created_client_config upstream "$upstream_id"
  refresh_upstream_status "$upstream_id" || true
""",
    "print new upstream clients",
)
host = replace_once(
    host,
    """show_client_config() {
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
""",
    """show_client_config() {
  print_client_config relay "$1"
}

show_upstream_client_config() {
  print_client_config upstream "$1"
}
""",
    "reuse common printer in existing menus",
)
host = replace_once(
    host,
    """  apply_candidate_with_rollback "$candidate"
  install_temp_cleanup_timer
  echo "临时节点创建成功：${custom_name}"
  echo "自动销毁时间：${expires_at}（${ttl} 分钟后）"
  echo "副机和原正式线路均未修改。客户端刷新订阅后即可看到临时节点。"
""",
    """  apply_candidate_with_rollback "$candidate"
  install_temp_cleanup_timer
  echo "临时节点创建成功：${custom_name}"
  echo "自动销毁时间：${expires_at}（${ttl} 分钟后）"
  echo "副机和原正式线路均未修改。"
  show_created_client_config temporary "$temp_id"
""",
    "print temporary clients",
)
HOST.write_text(host, encoding="utf-8")

TEST.parent.mkdir(parents=True, exist_ok=True)
TEST.write_text(r'''#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / 'core-src/client_package_renderer.py'
ADAPTER = ROOT / 'core-src/client_adapters.py'
HOST = ROOT / 'core-src/host.sh'


def run_render(state_path, item_id, out_dir):
    result = subprocess.run([
        sys.executable, str(RENDERER), '--state', str(state_path),
        '--kind', 'temporary', '--id', item_id, '--out', str(out_dir),
        '--adapter', str(ADAPTER),
    ], text=True, capture_output=True, check=True)
    return result.stdout


def main():
    host = HOST.read_text(encoding='utf-8')
    assert host.count('show_created_client_config() {') == 1
    assert host.count('show_created_client_config relay "$relay_id"') == 1
    assert host.count('show_created_client_config upstream "$upstream_id"') == 1
    assert host.count('show_created_client_config temporary "$temp_id"') == 1
    assert 'print_client_config relay "$1"' in host
    assert 'print_client_config upstream "$1"' in host

    state = {
        'schema': 3,
        'role': 'japan-hub',
        'protocol_mode': 'dual',
        'public_ip': '198.51.100.10',
        'listen_port': 443,
        'sni': 'www.softbank.jp',
        'hy2_limit_mbps': 50,
        'port_hopping': {'enabled': True, 'ports': '443,20000-50000', 'hop_interval_seconds': 30},
        'vless': {
            'reality': {'public_key': 'PUBLICKEY', 'short_id': '0123456789abcdef'},
            'reserve_users': [
                {'slot': 'v01', 'uuid': '11111111-1111-4111-8111-111111111111', 'assigned_id': 'temp-vps'},
                {'slot': 'v02', 'uuid': '22222222-2222-4222-8222-222222222222', 'assigned_id': 'temp-up'},
            ],
        },
        'hy2': {
            'server_name': 'jp-hy2.jp-relay.local',
            'obfs_password': 'obfs-password',
            'certificate_pin_hex': 'AA:BB:CC',
            'certificate_fingerprint': 'AA:BB:CC',
            'reserve_users': [
                {'slot': 'h01', 'password': 'hy2-password', 'assigned_id': 'temp-vps'},
            ],
        },
        'temporary_nodes': [
            {
                'id': 'temp-vps', 'name': '临时-VPS-测试', 'source_type': 'vps',
                'source_id': 'relay-a', 'source_name': '正式VPS线路',
                'vless': {'reserve_slot': 'v01'}, 'hy2': {'reserve_slot': 'h01'},
                'expires_at': '2026-08-07T04:30:00+00:00',
            },
            {
                'id': 'temp-up', 'name': '临时-动态代理-测试', 'source_type': 'upstream',
                'source_id': 'upstream-a', 'source_name': '正式动态代理线路',
                'vless': {'reserve_slot': 'v02'}, 'hy2': None,
                'expires_at': '2026-08-07T04:40:00+00:00',
            },
        ],
    }

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        state_path = tmp / 'state.json'
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')

        vps_dir = tmp / 'vps'
        vps = run_render(state_path, 'temp-vps', vps_dir)
        for value in (
            '临时节点：临时-VPS-测试', '复制来源：正式VPS线路',
            '到期时间：2026-08-07T04:30:00+00:00', '【Quantumult X】',
            '【Loon】', '【Shadowrocket 分享链接】', '【NekoBox For Android】',
            '【Clash Verge Rev / Mihomo】', 'vless=', 'Hysteria2',
            'sn://vmess?', 'sn://hysteria?', 'type: hysteria2',
            'ports: "443,20000-50000"',
        ):
            assert value in vps, value
        assert (vps_dir / '客户端节点.txt').read_text(encoding='utf-8') == vps

        up_dir = tmp / 'upstream'
        upstream = run_render(state_path, 'temp-up', up_dir)
        for value in (
            '临时节点：临时-动态代理-测试', '复制来源：正式动态代理线路',
            'UDP：服务器端拒绝，防止绕过上游出口', 'vless=', 'sn://vmess?',
        ):
            assert value in upstream, value
        for forbidden in ('Hysteria2', 'sn://hysteria?', 'type: hysteria2', 'hysteria2://'):
            assert forbidden not in upstream, forbidden

    print('PASS created VPS, upstream and temporary nodes share complete client output')


if __name__ == '__main__':
    main()
''', encoding="utf-8")

final = FINAL.read_text(encoding="utf-8")
final = replace_once(
    final,
    """  "$ROOT/tests/test_registered_host_menu.py" \\
  "$ROOT/tests/extract_manager_library.py" \\
""",
    """  "$ROOT/tests/test_registered_host_menu.py" \\
  "$ROOT/tests/test_created_node_client_output.py" \\
  "$ROOT/tests/extract_manager_library.py" \\
""",
    "compile created-node test",
)
final = replace_once(
    final,
    """python3 "$ROOT/tests/test_registered_host_menu.py"\npython3 "$ROOT/tests/landing_direct_role_validation.py"\n""",
    """python3 "$ROOT/tests/test_registered_host_menu.py"\npython3 "$ROOT/tests/test_created_node_client_output.py"\npython3 "$ROOT/tests/landing_direct_role_validation.py"\n""",
    "run created-node test",
)
FINAL.write_text(final, encoding="utf-8")

host_hash = hashlib.sha256(HOST.read_bytes()).hexdigest()
renderer_hash = hashlib.sha256(RENDERER.read_bytes()).hexdigest()
UPGRADE.parent.mkdir(parents=True, exist_ok=True)
UPGRADE.write_text(f'''#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

[[ "$(id -u)" -eq 0 ]] || {{ echo "错误：请使用 root 用户执行。" >&2; exit 1; }}
command -v curl >/dev/null 2>&1 || {{ echo "错误：缺少 curl。" >&2; exit 1; }}
command -v python3 >/dev/null 2>&1 || {{ echo "错误：缺少 python3。" >&2; exit 1; }}
command -v sha256sum >/dev/null 2>&1 || {{ echo "错误：缺少 sha256sum。" >&2; exit 1; }}

MANAGER=/usr/local/sbin/jp-relay-manager
RENDERER=/usr/local/lib/vvv/client_package_renderer.py
STATE=/etc/jp-relay/state.json
[[ -x "$MANAGER" ]] || {{ echo "错误：未找到现有 VVV 中转管理器。" >&2; exit 1; }}
[[ -f "$RENDERER" ]] || {{ echo "错误：未找到现有客户端渲染器。" >&2; exit 1; }}
[[ -f "$STATE" ]] || {{ echo "错误：未找到现有中转状态文件。" >&2; exit 1; }}

SOURCE_REF="${{VVV_SOURCE_REF:-main}}"
RAW_BASE="https://raw.githubusercontent.com/weizhiok/vvv/${{SOURCE_REF}}"
EXPECTED_HOST_SHA="{host_hash}"
EXPECTED_RENDERER_SHA="{renderer_hash}"
WORK="$(mktemp -d /tmp/vvv-created-output-upgrade.XXXXXX)"
BACKUP="/root/vvv-created-output-backup-$(date +%Y%m%d-%H%M%S)"
cleanup() {{ rm -rf -- "$WORK"; }}
trap cleanup EXIT

curl -fL --retry 5 --retry-all-errors "$RAW_BASE/core-src/host.sh" -o "$WORK/host.sh"
curl -fL --retry 5 --retry-all-errors "$RAW_BASE/core-src/client_package_renderer.py" -o "$WORK/client_package_renderer.py"
[[ "$(sha256sum "$WORK/host.sh" | awk '{{print $1}}')" == "$EXPECTED_HOST_SHA" ]] || {{ echo "错误：host.sh 校验失败，未执行升级。" >&2; exit 1; }}
[[ "$(sha256sum "$WORK/client_package_renderer.py" | awk '{{print $1}}')" == "$EXPECTED_RENDERER_SHA" ]] || {{ echo "错误：客户端渲染器校验失败，未执行升级。" >&2; exit 1; }}

python3 - "$WORK/host.sh" "$WORK/jp-relay-manager" <<'PY_EXTRACT_MANAGER'
import sys
from pathlib import Path
source, output = map(Path, sys.argv[1:])
text = source.read_text(encoding='utf-8')
start_marker = "cat > /usr/local/sbin/jp-relay-manager <<'JP_RELAY_JPR3_MANAGER_EOF'\\n"
end_marker = "\\nJP_RELAY_JPR3_MANAGER_EOF\\n"
start = text.find(start_marker)
if start < 0:
    raise SystemExit('无法定位 jp-relay-manager 起始标记。')
start += len(start_marker)
end = text.find(end_marker, start)
if end < 0:
    raise SystemExit('无法定位 jp-relay-manager 结束标记。')
output.write_text(text[start:end] + '\\n', encoding='utf-8')
PY_EXTRACT_MANAGER

bash -n "$WORK/jp-relay-manager"
python3 -m py_compile "$WORK/client_package_renderer.py"
grep -q '^show_created_client_config()' "$WORK/jp-relay-manager"
python3 "$WORK/client_package_renderer.py" --help | grep -q temporary

if cmp -s "$WORK/jp-relay-manager" "$MANAGER" && cmp -s "$WORK/client_package_renderer.py" "$RENDERER"; then
  echo "当前 VPS 已具备创建后打印全部客户端配置的功能，无需重复升级。"
  exit 0
fi

mkdir -p "$BACKUP"
cp -a "$MANAGER" "$BACKUP/jp-relay-manager"
cp -a "$RENDERER" "$BACKUP/client_package_renderer.py"
STATE_BEFORE="$(sha256sum "$STATE" | awk '{{print $1}}')"
XRAY_PID_BEFORE="$(systemctl show -p MainPID --value xray 2>/dev/null || true)"
SING_PID_BEFORE="$(systemctl show -p MainPID --value sing-box 2>/dev/null || true)"
rollback() {{
  cp -a "$BACKUP/jp-relay-manager" "$MANAGER" 2>/dev/null || true
  cp -a "$BACKUP/client_package_renderer.py" "$RENDERER" 2>/dev/null || true
  echo "升级失败，已恢复升级前文件。备份目录：$BACKUP" >&2
}}
trap rollback ERR
install -o root -g root -m 700 "$WORK/jp-relay-manager" "$MANAGER"
install -o root -g root -m 755 "$WORK/client_package_renderer.py" "$RENDERER"
bash -n "$MANAGER"
python3 -m py_compile "$RENDERER"
[[ "$(sha256sum "$STATE" | awk '{{print $1}}')" == "$STATE_BEFORE" ]]
[[ "$(systemctl show -p MainPID --value xray 2>/dev/null || true)" == "$XRAY_PID_BEFORE" ]]
[[ "$(systemctl show -p MainPID --value sing-box 2>/dev/null || true)" == "$SING_PID_BEFORE" ]]
trap - ERR

echo "升级成功。"
echo "已更新：$MANAGER"
echo "已更新：$RENDERER"
echo "状态文件、节点凭据、Xray、sing-box 和 SSH 均未重启或修改。"
echo "备份目录：$BACKUP"
''', encoding="utf-8")

for path in (TEST, UPGRADE):
    path.chmod(0o755)

print("patched created-node client output")

#!/usr/bin/env python3
from pathlib import Path
import re

HOST = Path('core-src/host.sh')
text = HOST.read_text(encoding='utf-8')
original = text

vless_replacement = r'''allocate_vless_slot() {
  local target_id="${1:-}" slot_json
  if [[ -n "$target_id" ]]; then
    slot_json="$(jq -c --arg id "$target_id" '[.vless.reserve_users[] | select(.assigned_id==$id)][0] // empty' "$STATE_FILE")"
  fi
  if [[ -z "${slot_json:-}" ]]; then
    slot_json="$(jq -c '[.vless.reserve_users[] | select(.assigned_id==null and (.retired // false)==false)][0] // empty' "$STATE_FILE")"
  fi
  [[ -n "$slot_json" ]] || fail "VLESS 可用固定凭证槽位已用尽（已分配或退役共 256 条）。"
  ALLOC_VLESS_SLOT="$(jq -r '.slot' <<<"$slot_json")"
  ALLOC_VLESS_UUID="$(jq -r '.uuid' <<<"$slot_json")"
  ALLOC_VLESS_EMAIL="$(jq -r '.email' <<<"$slot_json")"
  ALLOC_VLESS_PORT="$(jq -r '.local_port' <<<"$slot_json")"
  [[ -n "$ALLOC_VLESS_SLOT" && -n "$ALLOC_VLESS_UUID" && -n "$ALLOC_VLESS_EMAIL" && "$ALLOC_VLESS_PORT" =~ ^[0-9]+$ ]] || fail "VLESS 预分配用户池损坏。"
}
'''

hy2_replacement = r'''allocate_hy2_slot() {
  local target_id="${1:-}" slot_json
  if [[ -n "$target_id" ]]; then
    slot_json="$(jq -c --arg id "$target_id" '[.hy2.reserve_users[] | select(.assigned_id==$id)][0] // empty' "$STATE_FILE")"
  fi
  if [[ -z "${slot_json:-}" ]]; then
    slot_json="$(jq -c '[.hy2.reserve_users[] | select(.assigned_id==null and (.retired // false)==false)][0] // empty' "$STATE_FILE")"
  fi
  [[ -n "$slot_json" ]] || fail "Hysteria 2 可用固定凭证槽位已用尽（已分配或退役共 256 条）。"
  ALLOC_HY2_SLOT="$(jq -r '.slot' <<<"$slot_json")"
  ALLOC_HY2_USER="$(jq -r '.name' <<<"$slot_json")"
  ALLOC_HY2_PASSWORD="$(jq -r '.password' <<<"$slot_json")"
  ALLOC_HY2_PORT="$(jq -r '.local_port' <<<"$slot_json")"
  [[ -n "$ALLOC_HY2_SLOT" && -n "$ALLOC_HY2_USER" && -n "$ALLOC_HY2_PASSWORD" && "$ALLOC_HY2_PORT" =~ ^[0-9]+$ ]] || fail "Hysteria 2 预分配槽位池损坏。"
}
'''

text, n = re.subn(
    r'allocate_vless_slot\(\) \{.*?\n\}\n\n(?=release_orphaned_vless_slots\(\))',
    vless_replacement + '\n', text, count=1, flags=re.S)
if n != 1:
    raise SystemExit(f'无法唯一替换 allocate_vless_slot：{n}')

text, n = re.subn(
    r'allocate_hy2_slot\(\) \{.*?\n\}\n\n(?=release_orphaned_hy2_slots\(\))',
    hy2_replacement + '\n', text, count=1, flags=re.S)
if n != 1:
    raise SystemExit(f'无法唯一替换 allocate_hy2_slot：{n}')

# 正式 VPS 中转线路：相同 relay_id 重新添加时认领原固定槽位。
text, n_vless_relay = re.subn(
    r'(relay_id="\$\(printf \'%s:%s:%s\'.*?\n(?:.*\n){0,8}?\s+)allocate_vless_slot\n',
    r'\1allocate_vless_slot "$relay_id"\n', text, count=1)
if n_vless_relay != 1:
    raise SystemExit(f'无法定位 VPS VLESS 槽位分配调用：{n_vless_relay}')

text, n_hy2_relay = re.subn(
    r'(if mode_has_hy2; then\n\s+local material client_user client_password reserve_slot outbound_password outbound_obfs\n\s+)allocate_hy2_slot\n',
    r'\1allocate_hy2_slot "$relay_id"\n', text, count=1)
if n_hy2_relay != 1:
    raise SystemExit(f'无法定位 VPS HY2 槽位分配调用：{n_hy2_relay}')

# HTTP/HTTPS/SOCKS5 正式线路：相同 upstream_id 重新添加时认领原 VLESS 槽位。
text, n_upstream = re.subn(
    r'(upstream_id="\$\(printf \'%s:%s:%s:%s\'.*?\n\s+)allocate_vless_slot\n',
    r'\1allocate_vless_slot "$upstream_id"\n', text, count=1)
if n_upstream != 1:
    raise SystemExit(f'无法定位上游代理 VLESS 槽位分配调用：{n_upstream}')

if text == original:
    raise SystemExit('转换后源码没有变化。')
HOST.write_text(text, encoding='utf-8')
print('updated core-src/host.sh')

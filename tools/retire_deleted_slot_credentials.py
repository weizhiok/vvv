#!/usr/bin/env python3
from pathlib import Path

path = Path('src/prepare.py')
text = path.read_text(encoding='utf-8')

old_vless = r'''release_orphaned_vless_slots() {
  local path="$1" tmp
  [[ "$(jq -r '.vless // empty' "$path")" != "" ]] || return 0
  tmp="$(mktemp --suffix=.json /tmp/vvv-slots.XXXXXX)"; TMP_FILES+=("$tmp")
  jq '([.relays[]?.id] + [.upstream_relays[]?.id]) as $active | .vless.reserve_users |= map(if (.assigned_id != null and (($active|index(.assigned_id)) == null)) then .assigned_id=null else . end)' "$path" > "$tmp"
  install -m600 "$tmp" "$path"
}'''
new_vless = r'''release_orphaned_vless_slots() {
  local path="$1"
  [[ "$(jq -r '.vless // empty' "$path")" != "" ]] || return 0
  jq -e '[.vless.reserve_users[]?.assigned_id | select(.!=null)] as $ids | ($ids|length)==($ids|unique|length)' "$path" >/dev/null || fail "VLESS 固定槽位存在重复占用。"
  # 删除线路后保留 assigned_id 作为退役标记，防止旧 UUID 在未来被其他线路复用。
}'''

old_hy2 = r'''release_orphaned_hy2_slots() {
  local state_path="$1" tmp
  [[ "$(jq -r '.hy2 // empty' "$state_path")" != "" ]] || return 0
  tmp="$(mktemp --suffix=.json /tmp/vvv-hy2-slots.XXXXXX)"; TMP_FILES+=("$tmp")
  jq '[.relays[]?.id] as $active | .hy2.reserve_users |= map(if (.assigned_id != null and (($active|index(.assigned_id)) == null)) then .assigned_id=null else . end)' "$state_path" > "$tmp"
  install -m600 "$tmp" "$state_path"
}'''
new_hy2 = r'''release_orphaned_hy2_slots() {
  local state_path="$1"
  [[ "$(jq -r '.hy2 // empty' "$state_path")" != "" ]] || return 0
  jq -e '[.hy2.reserve_users[]?.assigned_id | select(.!=null)] as $ids | ($ids|length)==($ids|unique|length)' "$state_path" >/dev/null || fail "Hysteria 2 固定槽位存在重复占用。"
  # 删除线路后保留 assigned_id 作为退役标记，防止旧用户名和密码在未来被其他线路复用。
}'''

for old, new, label in (
    (old_vless, new_vless, 'VLESS 退役槽位'),
    (old_hy2, new_hy2, 'HY2 退役槽位'),
):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label} anchor count={count}')
    text = text.replace(old, new, 1)

text = text.replace('VLESS 动态线路已达到 64 条上限。', 'VLESS 可用固定凭证槽位已用尽（已分配或退役共 64 条）。')
text = text.replace('Hysteria 2 动态线路已达到 64 条上限。', 'Hysteria 2 可用固定凭证槽位已用尽（已分配或退役共 64 条）。')
path.write_text(text, encoding='utf-8')

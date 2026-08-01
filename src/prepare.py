#!/usr/bin/env python3
from pathlib import Path
import re
import sys

if len(sys.argv) != 4:
    raise SystemExit('usage: prepare.py HOST LANDING CENTER')

host, landing, center = map(Path, sys.argv[1:])
h = host.read_text(encoding='utf-8')


def replace_once(text, old, new, label):
    if old not in text:
        raise SystemExit(f'无法修改 {label}')
    return text.replace(old, new, 1)

# Front-loaded installation parameters.
h, n = re.subn(r'^DEFAULT_SNI="www\.softbank\.jp"$', 'DEFAULT_SNI="${VVV_REALITY_SNI:-www.softbank.jp}"', h, count=1, flags=re.M)
if n != 1:
    raise SystemExit('无法设置 REALITY 伪装域名')
new_prompt = r'''prompt_initial_mode_and_port() {
  local preset_mode="${VVV_PROTOCOL_MODE:-dual}" preset_port="${VVV_PROXY_PORT:-443}"
  case "$preset_mode" in dual|vless|hy2) INSTALL_MODE="$preset_mode";; *) fail "预设协议模式无效：$preset_mode"; return 1;; esac
  valid_port "$preset_port" || { fail "预设代理端口无效：$preset_port"; return 1; }
  INSTALL_PORT="$((10#$preset_port))"
  [[ "$INSTALL_MODE" == hy2 ]] || [[ "$DEFAULT_SNI" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ ]] || { fail "REALITY 伪装域名格式无效：$DEFAULT_SNI"; return 1; }
  echo "已选择模式：$INSTALL_MODE"
  echo "统一监听端口：TCP/UDP ${INSTALL_PORT}（仅启用所选协议）"
  [[ "$INSTALL_MODE" == hy2 ]] || echo "REALITY 伪装域名：$DEFAULT_SNI"
}
'''
h, n = re.subn(r'(?ms)^prompt_initial_mode_and_port\(\) \{.*?^\}\n', new_prompt, h, count=1)
if n != 1:
    raise SystemExit('无法替换代理参数函数')

# Loon Salamander compatibility. QR output is intentionally unsupported.
h = h.replace("salamander-password={loon_q(h['obfs_password'])}", "salamander-password={h['obfs_password']}")

# Preallocate VLESS users, so adding or deleting relay routes never replaces the public REALITY inbound.
old = '''    local key_output v_private v_public short_id uuid
    uuid="$(new_uuid)"
    key_output="$("$XRAY" x25519)"'''
new = '''    local key_output v_private v_public short_id uuid reserve_json i slot_uuid slot_email
    uuid="$(new_uuid)"
    reserve_json='[]'
    for i in $(seq 1 64); do
      slot_uuid="$(new_uuid)"; slot_email="reserve-$(printf '%02d' "$i")@relay.local"
      reserve_json="$(jq --arg slot "v$(printf '%02d' "$i")" --arg uuid "$slot_uuid" --arg email "$slot_email" '. + [{slot:$slot,uuid:$uuid,email:$email,assigned_id:null}]' <<<"$reserve_json")"
    done
    key_output="$("$XRAY" x25519)"'''
h = replace_once(h, old, new, 'VLESS 预分配用户池')
old = '''      --arg uuid "$uuid" \\
      '{reality:{private_key:$private,public_key:$public,short_id:$sid},direct_user:{uuid:$uuid,email:"jp-direct@relay.local"}}')"'''
new = '''      --arg uuid "$uuid" --argjson reserve "$reserve_json" \\
      '{reality:{private_key:$private,public_key:$public,short_id:$sid},direct_user:{uuid:$uuid,email:"jp-direct@relay.local"},reserve_users:$reserve}')"'''
h = replace_once(h, old, new, 'VLESS 状态用户池')
old = '''clients=[{"id":v["direct_user"]["uuid"],"level":0,"email":v["direct_user"]["email"],"flow":"xtls-rprx-vision"}]
for r in relays:
    rv=r.get("vless")
    if rv:
        clients.append({"id":rv["client_uuid"],"level":0,"email":rv["client_email"],"flow":"xtls-rprx-vision"})
for r in upstreams:
    clients.append({"id":r["client_uuid"],"level":0,"email":r["client_email"],"flow":"xtls-rprx-vision"})'''
new = '''clients=[{"id":v["direct_user"]["uuid"],"level":0,"email":v["direct_user"]["email"],"flow":"xtls-rprx-vision"}]
for user in v.get("reserve_users",[]):
    clients.append({"id":user["uuid"],"level":0,"email":user["email"],"flow":"xtls-rprx-vision"})'''
h = replace_once(h, old, new, 'Xray 固定用户池')
# Add the classic, proven Xray API inbound.
needle = '''}]
outbounds=[{"tag":"direct","protocol":"freedom","settings":{"domainStrategy":"UseIPv4"}}]'''
replacement = '''}]
inbounds.append({"tag":"api-in","listen":"127.0.0.1","port":10085,"protocol":"dokodemo-door","settings":{"address":"127.0.0.1"}})
outbounds=[{"tag":"direct","protocol":"freedom","settings":{"domainStrategy":"UseIPv4"}}]'''
h = replace_once(h, needle, replacement, 'Xray API 入站')
needle = '''rules=test_rules+udp_block_rules+[
 {"type":"field","ip":private_ips,"outboundTag":"blocked","ruleTag":"block-private"},'''
replacement = '''rules=test_rules+udp_block_rules+[
 {"type":"field","inboundTag":["api-in"],"outboundTag":"api","ruleTag":"api-route"},
 {"type":"field","ip":private_ips,"outboundTag":"blocked","ruleTag":"block-private"},'''
h = replace_once(h, needle, replacement, 'Xray API 路由')
needle = '''cfg={"log":{"loglevel":"warning"},"inbounds":inbounds,"outbounds":outbounds,"routing":{"domainStrategy":"AsIs","rules":rules}}'''
replacement = '''cfg={"log":{"loglevel":"warning"},"api":{"tag":"api","services":["HandlerService","RoutingService"]},"inbounds":inbounds,"outbounds":outbounds,"routing":{"domainStrategy":"AsIs","rules":rules}}'''
h = replace_once(h, needle, replacement, 'Xray API 配置')

slot_helpers = r'''vvv_event_backup() {
  local reason="$1"
  [[ -x /usr/local/lib/vvv/backup_manager.py && -f /etc/vvv-sub/config.json ]] || return 0
  python3 /usr/local/lib/vvv/backup_manager.py create "$reason" --force >/dev/null || echo "警告：自动备份失败。" >&2
}

allocate_vless_slot() {
  local slot_json
  slot_json="$(jq -c '[.vless.reserve_users[] | select(.assigned_id==null)][0] // empty' "$STATE_FILE")"
  [[ -n "$slot_json" ]] || fail "VLESS 可用固定凭证槽位已用尽（已分配或退役共 64 条）。"
  ALLOC_VLESS_SLOT="$(jq -r '.slot' <<<"$slot_json")"
  ALLOC_VLESS_UUID="$(jq -r '.uuid' <<<"$slot_json")"
  ALLOC_VLESS_EMAIL="$(jq -r '.email' <<<"$slot_json")"
}

release_orphaned_vless_slots() {
  local path="$1"
  [[ "$(jq -r '.vless // empty' "$path")" != "" ]] || return 0
  jq -e '[.vless.reserve_users[]?.assigned_id | select(.!=null)] as $ids | ($ids|length)==($ids|unique|length)' "$path" >/dev/null || fail "VLESS 固定槽位存在重复占用。"
  # 删除线路后保留 assigned_id 作为退役标记，防止旧 UUID 在未来被其他线路复用。
}

'''
h = replace_once(h, 'prepare_add_or_overwrite() {', slot_helpers + 'prepare_add_or_overwrite() {', '线路用户池辅助函数')

# Allocate relay users from the fixed pool.
old = '''      local client_uuid outbound_uuid key_output private_key public_key short_id
      test_vless="$(allocate_test_port vless)"
      client_uuid="$(new_uuid)"
      outbound_uuid="$(new_uuid)"'''
new = '''      local client_uuid client_email reserve_slot outbound_uuid key_output private_key public_key short_id
      test_vless="$(allocate_test_port vless)"
      allocate_vless_slot
      client_uuid="$ALLOC_VLESS_UUID"; client_email="$ALLOC_VLESS_EMAIL"; reserve_slot="$ALLOC_VLESS_SLOT"
      outbound_uuid="$(new_uuid)"'''
h = replace_once(h, old, new, 'VPS 中转用户分配')
old = '''        --arg client_uuid "$client_uuid" --arg email "${relay_id}@relay.local" \\
        --arg outbound_uuid "$outbound_uuid" --arg private "$private_key" --arg public "$public_key" --arg sid "$short_id" \\
        --arg outtag "vless-out-${relay_id}" --arg testtag "vless-test-${relay_id}" --argjson testport "$test_vless" \\
        '{client_uuid:$client_uuid,client_email:$email,outbound_uuid:$outbound_uuid,remote_reality:{private_key:$private,public_key:$public,short_id:$sid},outbound_tag:$outtag,test_inbound_tag:$testtag,test_socks_port:$testport}')"'''
new = '''        --arg client_uuid "$client_uuid" --arg email "$client_email" --arg reserve_slot "$reserve_slot" \\
        --arg outbound_uuid "$outbound_uuid" --arg private "$private_key" --arg public "$public_key" --arg sid "$short_id" \\
        --arg outtag "vless-out-${relay_id}" --arg testtag "vless-test-${relay_id}" --argjson testport "$test_vless" \\
        '{client_uuid:$client_uuid,client_email:$email,reserve_slot:$reserve_slot,outbound_uuid:$outbound_uuid,remote_reality:{private_key:$private,public_key:$public,short_id:$sid},outbound_tag:$outtag,test_inbound_tag:$testtag,test_socks_port:$testport}')"'''
h = replace_once(h, old, new, 'VPS 中转固定用户字段')
old = '''      '.relays += [{id:$id,name:$name,remote_ip:$ip,remote_port:$port,vless:$vless,hy2:$hy2,created_at:$now,updated_at:$now}] | .updated_at=$now' \\
      "$STATE_FILE" > "$candidate"'''
new = '''      '.relays += [{id:$id,name:$name,remote_ip:$ip,remote_port:$port,vless:$vless,hy2:$hy2,created_at:$now,updated_at:$now}] |
       (if $vless != null then (.vless.reserve_users[] | select(.slot==$vless.reserve_slot)).assigned_id=$id else . end) |
       .updated_at=$now' \\
      "$STATE_FILE" > "$candidate"'''
h = replace_once(h, old, new, 'VPS 中转用户占用状态')

# Upstream lines also use the fixed pool. Patch after the existing Python candidate generation.
old = '''    test_port="$(allocate_test_port upstream)"
    client_uuid="$(new_uuid)"'''
new = '''    test_port="$(allocate_test_port upstream)"
    allocate_vless_slot
    client_uuid="$ALLOC_VLESS_UUID"'''
h = replace_once(h, old, new, '动态代理用户分配')
marker = '''PY_UPSTREAM_NEW
  fi

  local staging package_dir'''
insert = '''PY_UPSTREAM_NEW
    tmp_slot="$(mktemp --suffix=.json /tmp/vvv-upstream-slot.XXXXXX)"; TMP_FILES+=("$tmp_slot")
    jq --arg id "$upstream_id" --arg email "$ALLOC_VLESS_EMAIL" --arg slot "$ALLOC_VLESS_SLOT" \\
      '(.upstream_relays[]|select(.id==$id)).client_email=$email |
       (.upstream_relays[]|select(.id==$id)).reserve_slot=$slot |
       (.vless.reserve_users[]|select(.slot==$slot)).assigned_id=$id' "$candidate" > "$tmp_slot"
    install -m600 "$tmp_slot" "$candidate"
  fi

  local staging package_dir'''
h = replace_once(h, marker, insert, '动态代理用户占用状态')

# Embed the center registration code into the landing key when the main host owns a center.
old = """make_pairing_key() {
  local state_path=\"$1\" relay_id=\"$2\"
  python3 - \"$state_path\" \"$relay_id\" <<'PY_JPR3'"""
new = """make_pairing_key() {
  local state_path=\"$1\" relay_id=\"$2\" registration_code=\"\"
  [[ ! -r /etc/vvv-sub/registration.code ]] || registration_code=\"$(cat /etc/vvv-sub/registration.code)\"
  python3 - \"$state_path\" \"$relay_id\" \"$registration_code\" <<'PY_JPR3'"""
h = replace_once(h, old, new, '副机订阅接入码')
old = ''' "vless":None,"hy2":None,"issued_at":datetime.now(timezone.utc).isoformat()
}'''
new = ''' "vless":None,"hy2":None,"subscription_registration_code":sys.argv[3] or None,
 "issued_at":datetime.now(timezone.utc).isoformat()
}'''
h = replace_once(h, old, new, '副机订阅接入字段')

# Replace restart-based state activation with API hot loading for Xray. sing-box restarts only when its config changed.
hot_apply = r'''xray_dynamic_parts() {
  local config="$1" dir="$2"
  mkdir -p "$dir"
  python3 - "$config" "$dir" <<'PY_PARTS'
import json,sys
from pathlib import Path
cfg=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')); out=Path(sys.argv[2])
ins=[x for x in cfg.get('inbounds',[]) if x.get('tag') not in ('in-vless-reality','api-in')]
outs=[x for x in cfg.get('outbounds',[]) if x.get('tag') not in ('direct','blocked')]
(out/'in.json').write_text(json.dumps({'inbounds':ins},ensure_ascii=False),encoding='utf-8')
(out/'out.json').write_text(json.dumps({'outbounds':outs},ensure_ascii=False),encoding='utf-8')
(out/'in.tags').write_text('\n'.join(x.get('tag','') for x in ins if x.get('tag'))+'\n',encoding='utf-8')
(out/'out.tags').write_text('\n'.join(x.get('tag','') for x in outs if x.get('tag'))+'\n',encoding='utf-8')
PY_PARTS
}

xray_hot_apply() {
  local target="$1" old="$2" dir tag count
  dir="$(mktemp -d /tmp/vvv-xray-api.XXXXXX)"; TMP_FILES+=("$dir")
  xray_dynamic_parts "$target" "$dir/new"; xray_dynamic_parts "$old" "$dir/old"
  cat "$dir/old/in.tags" "$dir/new/in.tags" | sort -u | while IFS= read -r tag; do [[ -z "$tag" ]] || "$XRAY" api rmi --server=127.0.0.1:10085 "$tag" >/dev/null 2>&1 || true; done
  cat "$dir/old/out.tags" "$dir/new/out.tags" | sort -u | while IFS= read -r tag; do [[ -z "$tag" ]] || "$XRAY" api rmo --server=127.0.0.1:10085 "$tag" >/dev/null 2>&1 || true; done
  count="$(jq '.inbounds|length' "$dir/new/in.json")"; (( count==0 )) || "$XRAY" api adi --server=127.0.0.1:10085 "$dir/new/in.json"
  count="$(jq '.outbounds|length' "$dir/new/out.json")"; (( count==0 )) || "$XRAY" api ado --server=127.0.0.1:10085 "$dir/new/out.json"
  "$XRAY" api adrules --server=127.0.0.1:10085 "$target"
}

apply_candidate_with_rollback() {
  local candidate_state="$1" delete_dir="${2:-}" old_state old_xray old_sing candidate_xray candidate_sing xray_pid="" sing_changed=0 ok=1
  old_state="$(mktemp --suffix=.json /tmp/vvv-old-state.XXXXXX)"; old_xray="$(mktemp --suffix=.json /tmp/vvv-old-xray.XXXXXX)"; old_sing="$(mktemp --suffix=.json /tmp/vvv-old-sing.XXXXXX)"
  candidate_xray="$(mktemp --suffix=.json /tmp/vvv-new-xray.XXXXXX)"; candidate_sing="$(mktemp --suffix=.json /tmp/vvv-new-sing.XXXXXX)"
  TMP_FILES+=("$old_state" "$old_xray" "$old_sing" "$candidate_xray" "$candidate_sing")
  cp -a "$STATE_FILE" "$old_state"; [[ ! -f "$XRAY_CFG" ]] || cp -a "$XRAY_CFG" "$old_xray"; [[ ! -f "$SING_CFG" ]] || cp -a "$SING_CFG" "$old_sing"
  release_orphaned_vless_slots "$candidate_state"
  vvv_event_backup before-line-change
  if mode_has_vless "$(jq -r '.protocol_mode' "$candidate_state")"; then
    build_xray_config "$candidate_state" "$candidate_xray"; "$XRAY" run -test -format=json -config "$candidate_xray"
    xray_pid="$(systemctl show -p MainPID --value xray)"
    "$XRAY" api inbounduser --server=127.0.0.1:10085 -tag=in-vless-reality >/dev/null
    if ! xray_hot_apply "$candidate_xray" "$old_xray"; then xray_hot_apply "$old_xray" "$candidate_xray" || true; fail "Xray API 热更新失败，已恢复旧运行配置。"; return 1; fi
  fi
  if mode_has_hy2 "$(jq -r '.protocol_mode' "$candidate_state")"; then
    build_sing_config "$candidate_state" "$candidate_sing"; "$SING_BOX" check -c "$candidate_sing"; cmp -s "$candidate_sing" "$SING_CFG" || sing_changed=1
  fi
  install -m600 "$candidate_state" "$STATE_FILE"
  if mode_has_vless; then install -o root -g xray -m640 "$candidate_xray" "$XRAY_CFG"; fi
  if (( sing_changed==1 )); then install -o root -g sing-box -m640 "$candidate_sing" "$SING_CFG"; systemctl restart sing-box >/dev/null 2>&1 || ok=0; fi
  sleep 2
  [[ -z "$xray_pid" || "$(systemctl show -p MainPID --value xray)" == "$xray_pid" ]] || ok=0
  verify_xray_runtime || ok=0; verify_sing_runtime || ok=0
  if (( ok==1 )); then
    [[ -z "$delete_dir" ]] || rm -rf -- "$delete_dir"
    vvv_event_backup after-line-change
    systemctl start vvv-sync.service >/dev/null 2>&1 || true
    return 0
  fi
  install -m600 "$old_state" "$STATE_FILE"
  if mode_has_vless && [[ -s "$old_xray" ]]; then install -o root -g xray -m640 "$old_xray" "$XRAY_CFG"; xray_hot_apply "$old_xray" "$candidate_xray" || true; fi
  if (( sing_changed==1 )) && [[ -s "$old_sing" ]]; then install -o root -g sing-box -m640 "$old_sing" "$SING_CFG"; systemctl restart sing-box >/dev/null 2>&1 || true; fi
  fail "新配置未生效，已恢复旧配置。"
}

'''
h, n = re.subn(r'(?ms)^apply_candidate_with_rollback\(\) \{.*?^\}\n\ngenerate_client_files\(\) \{', hot_apply + 'generate_client_files() {', h, count=1)
if n != 1:
    raise SystemExit('无法替换线路热更新事务')
# Update success text: no longer claim proxy services restarted.
h = h.replace('本次只重启了启用的代理服务，没有立即重启服务器。', '线路已通过运行时接口生效；Xray 主进程未重启。')


# Clean HY2 architecture: fixed main sing-box and one isolated service per relay slot.
h = replace_once(
    h,
    '    local server_name cert key meta password obfs\n',
    '    local server_name cert key meta password obfs reserve_json i slot_name slot_password\n',
    'HY2 槽位初始化变量',
)
old = '''    obfs="$(random_secret)"
    hy2_json="$(jq -n \\
'''
new = '''    obfs="$(random_secret)"
    reserve_json='[]'
    for i in $(seq 1 64); do
      slot_name="reserve-h$(printf '%02d' "$i")"
      slot_password="$(random_secret)"
      reserve_json="$(jq --arg slot "h$(printf '%02d' "$i")" --arg name "$slot_name" --arg password "$slot_password" --argjson local_port "$((21000+i))" '. + [{slot:$slot,name:$name,password:$password,local_port:$local_port,assigned_id:null}]' <<<"$reserve_json")"
    done
    hy2_json="$(jq -n \\
'''
h = replace_once(h, old, new, 'HY2 预分配槽位池')
old = '''      --arg password "$password" --arg obfs "$obfs" \\
      --arg fp'''
new = '''      --arg password "$password" --arg obfs "$obfs" --argjson reserve "$reserve_json" \\
      --arg fp'''
h = replace_once(h, old, new, 'HY2 槽位状态参数')
old = '''      '{server_name:$server_name,certificate_path:$cert,key_path:$key,certificate_fingerprint:$fp,certificate_pin_hex:$pinhex,certificate_public_key_sha256:$pinb64,obfs_password:$obfs,direct_user:{name:"jp-direct-hy2",password:$password}}')"'''
new = '''      '{server_name:$server_name,certificate_path:$cert,key_path:$key,certificate_fingerprint:$fp,certificate_pin_hex:$pinhex,certificate_public_key_sha256:$pinb64,obfs_password:$obfs,direct_user:{name:"jp-direct-hy2",password:$password},reserve_users:$reserve}')"'''
h = replace_once(h, old, new, 'HY2 槽位状态对象')

hy2_main = r'''build_sing_config() {
  local state_path="$1" output="$2"
  python3 - "$state_path" "$output" "$HY2_LIMIT_MBPS" <<'PY_BUILD_SING'
import json, sys
from pathlib import Path
state=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
limit_mbps=int(sys.argv[3])
if state["protocol_mode"] not in ("dual","hy2"):
    Path(sys.argv[2]).write_text("{}\n",encoding="utf-8")
    raise SystemExit
h=state["hy2"]; port=int(state["listen_port"])
reserve=h.get("reserve_users",[])
users=[{"name":h["direct_user"]["name"],"password":h["direct_user"]["password"]}]
users.extend({"name":slot["name"],"password":slot["password"]} for slot in reserve)
inbounds=[{
 "type":"hysteria2","tag":"hy2-in","listen":"0.0.0.0","listen_port":port,
 "up_mbps":limit_mbps,"down_mbps":limit_mbps,"users":users,
 "obfs":{"type":"salamander","password":h["obfs_password"]},
 "tls":{"enabled":True,"server_name":h["server_name"],"alpn":["h3"],"min_version":"1.3",
        "certificate_path":h["certificate_path"],"key_path":h["key_path"]}
}]
outbounds=[{"type":"direct","tag":"direct"}]
rules=[{"ip_is_private":True,"action":"reject","method":"drop"}]
for slot in reserve:
    tag=f"hy2-slot-{slot['slot']}"
    outbounds.append({"type":"socks","tag":tag,"server":"127.0.0.1","server_port":int(slot["local_port"])})
    rules.append({"auth_user":[slot["name"]],"action":"route","outbound":tag})
rules.append({"auth_user":[h["direct_user"]["name"]],"action":"route","outbound":"direct"})
cfg={
 "log":{"level":"warn","timestamp":True},
 "inbounds":inbounds,
 "outbounds":outbounds,
 "route":{"rules":rules,"final":"direct","auto_detect_interface":True}
}
Path(sys.argv[2]).write_text(json.dumps(cfg,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
PY_BUILD_SING
}
'''
h, count = re.subn(
    r'(?ms)^build_sing_config\(\) \{.*?^\}\n\nverify_xray_runtime\(\) \{',
    lambda _match: hy2_main + '\nverify_xray_runtime() {',
    h,
    count=1,
)
if count != 1:
    raise SystemExit('无法替换主 sing-box 固定配置函数')

unit_marker = '''EOF_SING_SERVICE
  systemctl daemon-reload
  systemctl enable sing-box >/dev/null'''
unit_replacement = '''EOF_SING_SERVICE
  install -d -o root -g sing-box -m 750 /etc/vvv-slots/hy2
  cat > /etc/systemd/system/vvv-hy2-slot@.service <<'EOF_HY2_SLOT_SERVICE'
[Unit]
Description=VVV Hysteria 2 relay slot %i
After=network-online.target
Wants=network-online.target

[Service]
User=sing-box
Group=sing-box
NoNewPrivileges=true
Environment=GOMEMLIMIT=128MiB
Environment=GOGC=50
ExecStart=/usr/local/bin/sing-box run -c /etc/vvv-slots/hy2/%i.json
Restart=on-failure
RestartSec=2s
LimitNOFILE=262144

[Install]
WantedBy=multi-user.target
EOF_HY2_SLOT_SERVICE
  systemctl daemon-reload
  systemctl enable sing-box >/dev/null'''
h = replace_once(h, unit_marker, unit_replacement, 'HY2 槽位 systemd 模板')

hy2_helpers = r'''allocate_hy2_slot() {
  local slot_json
  slot_json="$(jq -c '[.hy2.reserve_users[] | select(.assigned_id==null)][0] // empty' "$STATE_FILE")"
  [[ -n "$slot_json" ]] || fail "Hysteria 2 可用固定凭证槽位已用尽（已分配或退役共 64 条）。"
  ALLOC_HY2_SLOT="$(jq -r '.slot' <<<"$slot_json")"
  ALLOC_HY2_USER="$(jq -r '.name' <<<"$slot_json")"
  ALLOC_HY2_PASSWORD="$(jq -r '.password' <<<"$slot_json")"
  ALLOC_HY2_PORT="$(jq -r '.local_port' <<<"$slot_json")"
  [[ -n "$ALLOC_HY2_SLOT" && -n "$ALLOC_HY2_USER" && -n "$ALLOC_HY2_PASSWORD" && "$ALLOC_HY2_PORT" =~ ^[0-9]+$ ]] || fail "Hysteria 2 预分配槽位池损坏。"
}

release_orphaned_hy2_slots() {
  local state_path="$1"
  [[ "$(jq -r '.hy2 // empty' "$state_path")" != "" ]] || return 0
  jq -e '[.hy2.reserve_users[]?.assigned_id | select(.!=null)] as $ids | ($ids|length)==($ids|unique|length)' "$state_path" >/dev/null || fail "Hysteria 2 固定槽位存在重复占用。"
  # 删除线路后保留 assigned_id 作为退役标记，防止旧用户名和密码在未来被其他线路复用。
}

'''
h = replace_once(h, 'prepare_add_or_overwrite() {', hy2_helpers + 'prepare_add_or_overwrite() {', 'HY2 槽位辅助函数')

old = '''      local material client_password outbound_password outbound_obfs
      test_hy2="$(allocate_test_port hy2)"'''
new = '''      local material client_user client_password reserve_slot outbound_password outbound_obfs
      allocate_hy2_slot
      test_hy2="$ALLOC_HY2_PORT"
      client_user="$ALLOC_HY2_USER"; client_password="$ALLOC_HY2_PASSWORD"; reserve_slot="$ALLOC_HY2_SLOT"'''
h = replace_once(h, old, new, 'HY2 中转槽位分配')
h = replace_once(h, '      client_password="$(random_secret)"\n      outbound_password="$(random_secret)"', '      outbound_password="$(random_secret)"', '移除 HY2 动态入口密码')
old = '''        --arg client_user "${relay_id}-hy2" --arg client_password "$client_password" \\
        --arg outbound_password'''
new = '''        --arg client_user "$client_user" --arg client_password "$client_password" --arg reserve_slot "$reserve_slot" \\
        --arg outbound_password'''
h = replace_once(h, old, new, 'HY2 固定入口凭证参数')
old = '''        '{client_user:$client_user,client_password:$client_password,outbound_password:$outbound_password,outbound_obfs_password:$outbound_obfs,outbound_tag:$outtag,test_inbound_tag:$testtag,test_socks_port:$testport,outbound_server_name:$server_name,remote_certificate_pem:$cert_pem,remote_key_pem:$key_pem,remote_certificate_fingerprint:$fp,remote_certificate_pin_hex:$pinhex,remote_certificate_public_key_sha256:$pinb64}')"'''
new = '''        '{client_user:$client_user,client_password:$client_password,reserve_slot:$reserve_slot,outbound_password:$outbound_password,outbound_obfs_password:$outbound_obfs,outbound_tag:$outtag,test_inbound_tag:$testtag,test_socks_port:$testport,outbound_server_name:$server_name,remote_certificate_pem:$cert_pem,remote_key_pem:$key_pem,remote_certificate_fingerprint:$fp,remote_certificate_pin_hex:$pinhex,remote_certificate_public_key_sha256:$pinb64}')"'''
h = replace_once(h, old, new, 'HY2 槽位状态字段')
old = '''       (if $vless != null then (.vless.reserve_users[] | select(.slot==$vless.reserve_slot)).assigned_id=$id else . end) |
       .updated_at=$now'''
new = '''       (if $vless != null then (.vless.reserve_users[] | select(.slot==$vless.reserve_slot)).assigned_id=$id else . end) |
       (if $hy2 != null then (.hy2.reserve_users[] | select(.slot==$hy2.reserve_slot)).assigned_id=$id else . end) |
       .updated_at=$now'''
h = replace_once(h, old, new, 'HY2 槽位占用状态')

hy2_runtime = r'''build_hy2_slot_configs() {
  local state_path="$1" out_dir="$2"
  mkdir -p "$out_dir"
  python3 - "$state_path" "$out_dir" "$HY2_LIMIT_MBPS" <<'PY_HY2_SLOTS'
import json,sys
from pathlib import Path
state=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')); out=Path(sys.argv[2]); limit=int(sys.argv[3])
h=state.get('hy2') or {}; slots={x['slot']:x for x in h.get('reserve_users',[])}
relays={x.get('id'):x for x in state.get('relays',[])}
private_rule={"ip_is_private":True,"action":"reject","method":"drop"}
for slot_id,slot in slots.items():
    assigned=slot.get('assigned_id')
    relay=relays.get(assigned)
    if not relay: continue
    rh=relay.get('hy2')
    if not rh: continue
    inbound={"type":"mixed","tag":"slot-in","listen":"127.0.0.1","listen_port":int(slot['local_port'])}
    outbound={
      "type":"hysteria2","tag":"slot-out","server":relay['remote_ip'],"server_port":int(relay['remote_port']),
      "up_mbps":limit,"down_mbps":limit,"password":rh['outbound_password'],
      "obfs":{"type":"salamander","password":rh['outbound_obfs_password']},
      "tls":{"enabled":True,"server_name":rh['outbound_server_name'],"insecure":True,"alpn":["h3"],
             "min_version":"1.3","certificate_public_key_sha256":[rh['remote_certificate_public_key_sha256']]}
    }
    cfg={"log":{"level":"warn","timestamp":True},"inbounds":[inbound],"outbounds":[outbound],
         "route":{"rules":[private_rule],"final":"slot-out","auto_detect_interface":True}}
    (out/f'{slot_id}.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
PY_HY2_SLOTS
}

sync_hy2_slot_services() {
  local old_state="$1" new_state="$2" old_dir new_dir file slot changed port
  old_dir="$(mktemp -d /tmp/vvv-hy2-old.XXXXXX)"; new_dir="$(mktemp -d /tmp/vvv-hy2-new.XXXXXX)"
  TMP_FILES+=("$old_dir" "$new_dir")
  build_hy2_slot_configs "$old_state" "$old_dir"
  build_hy2_slot_configs "$new_state" "$new_dir"
  install -d -o root -g sing-box -m750 /etc/vvv-slots/hy2
  [[ -f /etc/systemd/system/vvv-hy2-slot@.service ]] || { fail "HY2 槽位 systemd 模板不存在。"; return 1; }
  for file in "$new_dir"/*.json; do
    [[ -e "$file" ]] || break
    "$SING_BOX" check -c "$file"
  done
  for file in "$old_dir"/*.json; do
    [[ -e "$file" ]] || break
    slot="$(basename "$file" .json)"
    if [[ ! -f "$new_dir/${slot}.json" ]]; then
      systemctl disable --now "vvv-hy2-slot@${slot}.service" >/dev/null 2>&1 || true
      rm -f "/etc/vvv-slots/hy2/${slot}.json"
    fi
  done
  for file in "$new_dir"/*.json; do
    [[ -e "$file" ]] || break
    slot="$(basename "$file" .json)"; changed=1
    [[ ! -f "/etc/vvv-slots/hy2/${slot}.json" ]] || cmp -s "$file" "/etc/vvv-slots/hy2/${slot}.json" && changed=0
    install -o root -g sing-box -m640 "$file" "/etc/vvv-slots/hy2/${slot}.json"
    systemctl enable "vvv-hy2-slot@${slot}.service" >/dev/null
    if (( changed==1 )); then
      systemctl restart "vvv-hy2-slot@${slot}.service"
    else
      systemctl start "vvv-hy2-slot@${slot}.service"
    fi
  done
  sleep 2
  for file in "$new_dir"/*.json; do
    [[ -e "$file" ]] || break
    slot="$(basename "$file" .json)"
    systemctl is-active --quiet "vvv-hy2-slot@${slot}.service" || return 1
    port="$(jq -r '.inbounds[0].listen_port' "$file")"
    ss -H -lntp "sport = :${port}" 2>/dev/null | grep -qi sing-box || return 1
  done
}

'''
h = replace_once(h, 'apply_candidate_with_rollback() {', hy2_runtime + 'apply_candidate_with_rollback() {', 'HY2 槽位运行时函数')

verify_hy2 = r'''verify_sing_runtime() {
  mode_has_hy2 || return 0
  local port slot
  port="$(jq -r '.listen_port' "$STATE_FILE")"
  systemctl is-active --quiet sing-box || return 1
  ss -H -lnup "sport = :${port}" 2>/dev/null | grep -qi sing-box || return 1
  while IFS=$'\t' read -r slot port; do
    [[ -n "$slot" && -n "$port" ]] || continue
    systemctl is-active --quiet "vvv-hy2-slot@${slot}.service" || return 1
    ss -H -lntp "sport = :${port}" 2>/dev/null | grep -qi sing-box || return 1
  done < <(jq -r '.hy2.reserve_users[]? | select(.assigned_id!=null) | [.slot,(.local_port|tostring)] | @tsv' "$STATE_FILE")
  return 0
}
'''
h, count = re.subn(
    r'(?ms)^verify_sing_runtime\(\) \{.*?^\}\n\nactivate_initial_state\(\) \{',
    lambda _match: verify_hy2 + '\nactivate_initial_state() {',
    h,
    count=1,
)
if count != 1:
    raise SystemExit('无法替换 HY2 运行状态验证')

old = '''    systemctl restart sing-box || return 1
    sleep 2
    verify_sing_runtime'''
new = '''    systemctl restart sing-box || return 1
    sync_hy2_slot_services "$STATE_FILE" "$STATE_FILE" || return 1
    sleep 2
    verify_sing_runtime'''
h = replace_once(h, old, new, '首次激活 HY2 槽位服务')


# Final architecture: fixed main Xray plus per-line VLESS slot services.
old = '''    reserve_json="$(jq --arg slot "v$(printf '%02d' "$i")" --arg uuid "$slot_uuid" --arg email "$slot_email" '. + [{slot:$slot,uuid:$uuid,email:$email,assigned_id:null}]' <<<"$reserve_json")"'''
new = '''    reserve_json="$(jq --arg slot "v$(printf '%02d' "$i")" --arg uuid "$slot_uuid" --arg email "$slot_email" --argjson local_port "$((22000+i))" '. + [{slot:$slot,uuid:$uuid,email:$email,local_port:$local_port,assigned_id:null}]' <<<"$reserve_json")"'''
h = replace_once(h, old, new, 'VLESS 槽位本地端口')

old = '''inbounds.append({"tag":"api-in","listen":"127.0.0.1","port":10085,"protocol":"dokodemo-door","settings":{"address":"127.0.0.1"}})
outbounds=[{"tag":"direct","protocol":"freedom","settings":{"domainStrategy":"UseIPv4"}}]'''
new = '''outbounds=[{"tag":"direct","protocol":"freedom","settings":{"domainStrategy":"UseIPv4"}}]
for user in v.get("reserve_users",[]):
    outbounds.append({"tag":f"vless-slot-{user['slot']}","protocol":"socks","settings":{"address":"127.0.0.1","port":int(user["local_port"])}})'''
h = replace_once(h, old, new, '移除 Xray API 并加入固定 VLESS 槽位出站')

start = h.find('test_rules=[]\nroute_rules=[]\nudp_block_rules=[]\nfor r in relays:')
end = h.find('private_ips=[', start)
if start < 0 or end < 0:
    raise SystemExit('无法定位主 Xray 动态线路生成区段')
h = h[:start] + '''test_rules=[]
route_rules=[{"type":"field","user":[user["email"]],"outboundTag":f"vless-slot-{user['slot']}","ruleTag":f"vless-slot-route-{user['slot']}"} for user in v.get("reserve_users",[])]
udp_block_rules=[]
''' + h[end:]

old = '''rules=test_rules+udp_block_rules+[
 {"type":"field","inboundTag":["api-in"],"outboundTag":"api","ruleTag":"api-route"},
 {"type":"field","ip":private_ips,"outboundTag":"blocked","ruleTag":"block-private"},'''
new = '''rules=test_rules+udp_block_rules+[
 {"type":"field","ip":private_ips,"outboundTag":"blocked","ruleTag":"block-private"},'''
h = replace_once(h, old, new, '移除 Xray API 路由')
old = '''cfg={"log":{"loglevel":"warning"},"api":{"tag":"api","services":["HandlerService","RoutingService"]},"inbounds":inbounds,"outbounds":outbounds,"routing":{"domainStrategy":"AsIs","rules":rules}}'''
new = '''cfg={"log":{"loglevel":"warning"},"inbounds":inbounds,"outbounds":outbounds,"routing":{"domainStrategy":"AsIs","rules":rules}}'''
h = replace_once(h, old, new, '移除 Xray API 配置')

old = '''  ALLOC_VLESS_EMAIL="$(jq -r '.email' <<<"$slot_json")"'''
new = '''  ALLOC_VLESS_EMAIL="$(jq -r '.email' <<<"$slot_json")"
  ALLOC_VLESS_PORT="$(jq -r '.local_port' <<<"$slot_json")"
  [[ -n "$ALLOC_VLESS_SLOT" && -n "$ALLOC_VLESS_UUID" && -n "$ALLOC_VLESS_EMAIL" && "$ALLOC_VLESS_PORT" =~ ^[0-9]+$ ]] || fail "VLESS 预分配用户池损坏。"'''
h = replace_once(h, old, new, '读取 VLESS 槽位端口')
h = replace_once(h, '''      test_vless="$(allocate_test_port vless)"
      allocate_vless_slot''', '''      allocate_vless_slot
      test_vless="$ALLOC_VLESS_PORT"''', 'VPS 中转复用 VLESS 槽位端口')
h = replace_once(h, '''    test_port="$(allocate_test_port upstream)"
    allocate_vless_slot''', '''    allocate_vless_slot
    test_port="$ALLOC_VLESS_PORT"''', '动态代理复用 VLESS 槽位端口')

vless_runtime = r'''build_vless_slot_configs() {
  local state_path="$1" out_dir="$2"
  mkdir -p "$out_dir"
  python3 - "$state_path" "$out_dir" <<'PY_VLESS_SLOTS'
import json,sys
from pathlib import Path
state=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')); out=Path(sys.argv[2])
v=state.get('vless') or {}; slots={x['slot']:x for x in v.get('reserve_users',[])}
relays={x.get('id'):x for x in state.get('relays',[])}; upstreams={x.get('id'):x for x in state.get('upstream_relays',[])}
for slot_id,slot in slots.items():
    assigned=slot.get('assigned_id')
    if not assigned: continue
    inbound={"tag":"slot-in","listen":"127.0.0.1","port":int(slot['local_port']),"protocol":"socks","settings":{"udp":False},"sniffing":{"enabled":True,"destOverride":["http","tls"],"routeOnly":True}}
    if assigned in relays:
        relay=relays[assigned]; rv=relay.get('vless')
        if not rv: continue
        outbound={"tag":"slot-out","protocol":"vless","settings":{"address":relay['remote_ip'],"port":int(relay['remote_port']),"id":rv['outbound_uuid'],"encryption":"none","flow":"xtls-rprx-vision"},"streamSettings":{"method":"raw","security":"reality","realitySettings":{"serverName":state['sni'],"fingerprint":"chrome","password":rv['remote_reality']['public_key'],"shortId":rv['remote_reality']['short_id'],"spiderX":""}}}
    elif assigned in upstreams:
        relay=upstreams[assigned]
        outbound={"tag":"slot-out","protocol":relay['proxy_protocol'],"settings":{"address":relay['host'],"port":int(relay['port']),"user":relay['username'],"pass":relay['password']}}
    else:
        continue
    cfg={"log":{"loglevel":"warning"},"inbounds":[inbound],"outbounds":[outbound,{"tag":"blocked","protocol":"blackhole","settings":{}}],"routing":{"domainStrategy":"AsIs","rules":[{"type":"field","ip":["0.0.0.0/8","10.0.0.0/8","100.64.0.0/10","127.0.0.0/8","169.254.0.0/16","172.16.0.0/12","192.168.0.0/16","224.0.0.0/4","240.0.0.0/4","::1/128","fc00::/7","fe80::/10"],"outboundTag":"blocked","ruleTag":"block-private"},{"type":"field","protocol":["bittorrent"],"outboundTag":"blocked","ruleTag":"block-bittorrent"},{"type":"field","inboundTag":["slot-in"],"outboundTag":"slot-out","ruleTag":"slot-route"}]}}
    (out/f'{slot_id}.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
PY_VLESS_SLOTS
}

install_vless_slot_service() {
  install -d -o root -g xray -m750 /etc/vvv-slots/vless
  cat > /etc/systemd/system/vvv-vless-slot@.service <<'EOF_VLESS_SLOT_SERVICE'
[Unit]
Description=VVV VLESS relay slot %i
After=network-online.target xray.service
Wants=network-online.target

[Service]
User=xray
Group=xray
NoNewPrivileges=true
Environment=GOMEMLIMIT=128MiB
Environment=GOGC=50
ExecStart=/usr/local/bin/xray run -format=json -config /etc/vvv-slots/vless/%i.json
Restart=on-failure
RestartSec=2s
LimitNOFILE=262144

[Install]
WantedBy=multi-user.target
EOF_VLESS_SLOT_SERVICE
  systemctl daemon-reload
}

sync_vless_slot_services() {
  local old_state="$1" new_state="$2" old_dir new_dir slot file changed
  old_dir="$(mktemp -d /tmp/vvv-vless-old.XXXXXX)"; new_dir="$(mktemp -d /tmp/vvv-vless-new.XXXXXX)"
  TMP_FILES+=("$old_dir" "$new_dir")
  build_vless_slot_configs "$old_state" "$old_dir"
  build_vless_slot_configs "$new_state" "$new_dir"
  install_vless_slot_service
  for file in "$new_dir"/*.json; do
    [[ -e "$file" ]] || break
    "$XRAY" run -test -format=json -config "$file"
  done
  for file in "$old_dir"/*.json; do
    [[ -e "$file" ]] || break
    slot="$(basename "$file" .json)"
    if [[ ! -f "$new_dir/${slot}.json" ]]; then
      systemctl disable --now "vvv-vless-slot@${slot}.service" >/dev/null 2>&1 || true
      rm -f "/etc/vvv-slots/vless/${slot}.json"
    fi
  done
  for file in "$new_dir"/*.json; do
    [[ -e "$file" ]] || break
    slot="$(basename "$file" .json)"; changed=1
    [[ ! -f "/etc/vvv-slots/vless/${slot}.json" ]] || cmp -s "$file" "/etc/vvv-slots/vless/${slot}.json" && changed=0
    install -o root -g xray -m640 "$file" "/etc/vvv-slots/vless/${slot}.json"
    if (( changed==1 )); then
      systemctl enable "vvv-vless-slot@${slot}.service" >/dev/null
      systemctl restart "vvv-vless-slot@${slot}.service"
    else
      systemctl start "vvv-vless-slot@${slot}.service"
    fi
  done
  sleep 2
  for file in "$new_dir"/*.json; do
    [[ -e "$file" ]] || break
    slot="$(basename "$file" .json)"
    systemctl is-active --quiet "vvv-vless-slot@${slot}.service" || return 1
    local port
    port="$(jq -r '.inbounds[0].port' "$file")"
    ss -H -lntp "sport = :${port}" 2>/dev/null | grep -qi xray || return 1
  done
}

'''
start = h.find('xray_dynamic_parts() {')
end = h.find('build_hy2_slot_configs() {', start)
if start < 0 or end < 0:
    raise SystemExit('无法定位旧 Xray API 热更新函数')
h = h[:start] + vless_runtime + h[end:]

new_apply = r'''apply_candidate_with_rollback() {
  local candidate_state="$1" delete_dir="${2:-}" old_state old_xray old_sing candidate_xray candidate_sing xray_pid="" sing_pid="" ok=1
  old_state="$(mktemp --suffix=.json /tmp/vvv-old-state.XXXXXX)"; old_xray="$(mktemp --suffix=.json /tmp/vvv-old-xray.XXXXXX)"; old_sing="$(mktemp --suffix=.json /tmp/vvv-old-sing.XXXXXX)"
  candidate_xray="$(mktemp --suffix=.json /tmp/vvv-new-xray.XXXXXX)"; candidate_sing="$(mktemp --suffix=.json /tmp/vvv-new-sing.XXXXXX)"
  TMP_FILES+=("$old_state" "$old_xray" "$old_sing" "$candidate_xray" "$candidate_sing")
  cp -a "$STATE_FILE" "$old_state"; [[ ! -f "$XRAY_CFG" ]] || cp -a "$XRAY_CFG" "$old_xray"; [[ ! -f "$SING_CFG" ]] || cp -a "$SING_CFG" "$old_sing"
  release_orphaned_vless_slots "$candidate_state"; release_orphaned_hy2_slots "$candidate_state"
  vvv_event_backup before-line-change
  if mode_has_vless "$(jq -r '.protocol_mode' "$candidate_state")"; then
    build_xray_config "$candidate_state" "$candidate_xray"; "$XRAY" run -test -format=json -config "$candidate_xray"
    cmp -s "$candidate_xray" "$XRAY_CFG" || { fail "线路变更意外修改了主 Xray 固定配置。"; return 1; }
    xray_pid="$(systemctl show -p MainPID --value xray)"
    if ! sync_vless_slot_services "$old_state" "$candidate_state"; then sync_vless_slot_services "$candidate_state" "$old_state" || true; fail "VLESS 槽位更新失败，已恢复旧槽位。"; return 1; fi
  fi
  if mode_has_hy2 "$(jq -r '.protocol_mode' "$candidate_state")"; then
    build_sing_config "$candidate_state" "$candidate_sing"; "$SING_BOX" check -c "$candidate_sing"
    cmp -s "$candidate_sing" "$SING_CFG" || { mode_has_vless && sync_vless_slot_services "$candidate_state" "$old_state" || true; fail "线路变更意外修改了主 sing-box 固定配置。"; return 1; }
    sing_pid="$(systemctl show -p MainPID --value sing-box)"
    if ! sync_hy2_slot_services "$old_state" "$candidate_state"; then
      sync_hy2_slot_services "$candidate_state" "$old_state" || true; mode_has_vless && sync_vless_slot_services "$candidate_state" "$old_state" || true
      fail "HY2 槽位更新失败，已恢复旧槽位。"; return 1
    fi
  fi
  install -m600 "$candidate_state" "$STATE_FILE"; sleep 2
  if [[ -n "$xray_pid" ]]; then
    if [[ "$(systemctl show -p MainPID --value xray)" == "$xray_pid" ]]; then
      echo "主 Xray PID 已保持不变：${xray_pid}"
    else
      echo "错误：主 Xray PID 发生变化。" >&2
      ok=0
    fi
  fi
  if [[ -n "$sing_pid" ]]; then
    if [[ "$(systemctl show -p MainPID --value sing-box)" == "$sing_pid" ]]; then
      echo "主 sing-box PID 已保持不变：${sing_pid}"
    else
      echo "错误：主 sing-box PID 发生变化。" >&2
      ok=0
    fi
  fi
  verify_xray_runtime || ok=0; verify_sing_runtime || ok=0
  if (( ok==1 )); then
    [[ -z "$delete_dir" ]] || rm -rf -- "$delete_dir"
    vvv_event_backup after-line-change
    systemctl start vvv-sync.service >/dev/null 2>&1 || true
    return 0
  fi
  install -m600 "$old_state" "$STATE_FILE"
  mode_has_vless && sync_vless_slot_services "$candidate_state" "$old_state" || true
  mode_has_hy2 && sync_hy2_slot_services "$candidate_state" "$old_state" || true
  fail "新槽位配置验证失败，已恢复旧配置。"
}

'''
h, n = re.subn(r'(?ms)^apply_candidate_with_rollback\(\) \{.*?^\}\n\ngenerate_client_files\(\) \{', lambda _match: new_apply + 'generate_client_files() {', h, count=1)
if n != 1:
    raise SystemExit('无法替换槽位事务函数')
h = h.replace('线路已通过运行时接口生效；Xray/sing-box 主进程均未重启。', '线路已通过独立槽位服务生效；Xray/sing-box 主进程均未重启。')

host.write_text(h, encoding='utf-8')

# Landing source: preloaded pairing key, Loon fix and complete top quiet zone.
l = landing.read_text(encoding='utf-8')
l, n = re.subn(r'^PAIRING_KEY=.*$', 'PAIRING_KEY="${VVV_PAIRING_KEY:-请粘贴以JPR3.开头的完整对接密钥}"', l, count=1, flags=re.M)
if n != 1:
    raise SystemExit('无法设置副机对接密钥')
l = l.replace("salamander-password={loon_q(h['obfs_password'])}", "salamander-password={h['obfs_password']}")
landing.write_text(l, encoding='utf-8')

c = center.read_text(encoding='utf-8')
for required in ('VVV_SUB_DOMAIN', 'VVV_SUB_PORT', '--adapter caddyfile', 'backup_manager.py', '/r/${token}/c'):
    if required not in c:
        raise SystemExit('订阅中心源码缺少必要字段：' + required)

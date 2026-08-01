#!/usr/bin/env python3
from pathlib import Path

path = Path('src/prepare.py')
source = path.read_text(encoding='utf-8')
anchor = "host.write_text(h, encoding='utf-8')"
if source.count(anchor) != 1:
    raise SystemExit(f'host write anchor count={source.count(anchor)}')

post = r"""
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
    hy2_main + '\nverify_xray_runtime() {',
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
  [[ -n "$slot_json" ]] || fail "Hysteria 2 动态线路已达到 64 条上限。"
  ALLOC_HY2_SLOT="$(jq -r '.slot' <<<"$slot_json")"
  ALLOC_HY2_USER="$(jq -r '.name' <<<"$slot_json")"
  ALLOC_HY2_PASSWORD="$(jq -r '.password' <<<"$slot_json")"
  ALLOC_HY2_PORT="$(jq -r '.local_port' <<<"$slot_json")"
  [[ -n "$ALLOC_HY2_SLOT" && -n "$ALLOC_HY2_USER" && -n "$ALLOC_HY2_PASSWORD" && "$ALLOC_HY2_PORT" =~ ^[0-9]+$ ]] || fail "Hysteria 2 预分配槽位池损坏。"
}

release_orphaned_hy2_slots() {
  local state_path="$1" tmp
  [[ "$(jq -r '.hy2 // empty' "$state_path")" != "" ]] || return 0
  tmp="$(mktemp --suffix=.json /tmp/vvv-hy2-slots.XXXXXX)"; TMP_FILES+=("$tmp")
  jq '[.relays[]?.id] as $active | .hy2.reserve_users |= map(if (.assigned_id != null and (($active|index(.assigned_id)) == null)) then .assigned_id=null else . end)' "$state_path" > "$tmp"
  install -m600 "$tmp" "$state_path"
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
    verify_hy2 + '\nactivate_initial_state() {',
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
"""

source = source.replace(anchor, post + "\n" + anchor, 1)
path.write_text(source, encoding='utf-8')

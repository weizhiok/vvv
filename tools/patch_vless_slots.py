#!/usr/bin/env python3
from pathlib import Path

p=Path('src/prepare.py')
s=p.read_text(encoding='utf-8')
anchor="host.write_text(h, encoding='utf-8')"
if s.count(anchor)!=1:
    raise SystemExit(f'host write anchor count={s.count(anchor)}')
post=r'''
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

old = '''  ALLOC_VLESS_EMAIL="$(jq -r '.email' <<<"$slot_json")"
  [[ -n "$ALLOC_VLESS_SLOT" && -n "$ALLOC_VLESS_UUID" && -n "$ALLOC_VLESS_EMAIL" ]] || fail "VLESS 预分配用户池损坏。"'''
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
  [[ -z "$xray_pid" || "$(systemctl show -p MainPID --value xray)" == "$xray_pid" ]] || ok=0
  [[ -z "$sing_pid" || "$(systemctl show -p MainPID --value sing-box)" == "$sing_pid" ]] || ok=0
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
h, n = re.subn(r'(?ms)^apply_candidate_with_rollback\(\) \{.*?^\}\n\ngenerate_client_files\(\) \{', new_apply + 'generate_client_files() {', h, count=1)
if n != 1:
    raise SystemExit('无法替换槽位事务函数')
h = h.replace('线路已通过运行时接口生效；Xray/sing-box 主进程均未重启。', '线路已通过独立槽位服务生效；Xray/sing-box 主进程均未重启。')
'''
s=s.replace(anchor,post+'\n'+anchor,1)
p.write_text(s,encoding='utf-8')

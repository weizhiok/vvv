#!/usr/bin/env python3
"""One-shot branch patch. Removed by the workflow after a successful run."""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


def write(path, text):
    (ROOT / path).write_text(text, encoding='utf-8')


def replace_once(path, old, new):
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{path}: expected one literal match, found {count}: {old[:100]!r}')
    write(path, text.replace(old, new, 1))


def replace_regex(path, pattern, replacement, flags=0):
    text = read(path)
    updated, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count != 1:
        raise RuntimeError(f'{path}: expected one regex match, found {count}: {pattern[:100]!r}')
    write(path, updated)


# ---------------------------------------------------------------------------
# bootstrap.sh: collect and validate the canonical port-hopping state before
# installation, and install every permanent runtime module.
# ---------------------------------------------------------------------------
replace_once('core-src/bootstrap.sh',
'''CENTER_CFG=/etc/vvv-sub/config.json
''',
'''CENTER_CFG=/etc/vvv-sub/config.json
HY2_HOP_ENGINE="$BASE_DIR/hy2_port_hop.py"
''')

replace_once('core-src/bootstrap.sh',
'''  VVV_HY2_LIMIT_MBPS="$(json_value "$MAIN_STATE" hy2_limit_mbps 50)"
  export VVV_PROTOCOL_MODE VVV_PROXY_PORT VVV_REALITY_SNI VVV_HY2_LIMIT_MBPS
''',
'''  VVV_HY2_LIMIT_MBPS="$(json_value "$MAIN_STATE" hy2_limit_mbps 50)"
  VVV_HY2_PORTS="$(json_value "$MAIN_STATE" port_hopping.ports "${VVV_PROXY_PORT},20000-50000")"
  VVV_HY2_HOP_INTERVAL="$(json_value "$MAIN_STATE" port_hopping.hop_interval_seconds 30)"
  export VVV_PROTOCOL_MODE VVV_PROXY_PORT VVV_REALITY_SNI VVV_HY2_LIMIT_MBPS VVV_HY2_PORTS VVV_HY2_HOP_INTERVAL
''')

replace_once('core-src/bootstrap.sh',
'''  if [[ "$VVV_PROTOCOL_MODE" != vless ]]; then
    while true; do
      read -r -p "请输入 Hysteria 2 每连接服务器强制限速 [默认 50M]：" input
      input="${input//[[:space:]]/}"; [[ -n "$input" ]] || input=50
      input="${input%[Mm]}"
      if [[ "$input" =~ ^[0-9]+$ ]] && ((10#$input>=30 && 10#$input<=100)); then VVV_HY2_LIMIT_MBPS="$((10#$input))"; break; fi
      echo "限速只允许 30-100 的整数，可写 50、50M 或 50m。"
    done
  fi
  export VVV_PROTOCOL_MODE VVV_PROXY_PORT VVV_REALITY_SNI VVV_HY2_LIMIT_MBPS
''',
'''  VVV_HY2_PORTS=""
  VVV_HY2_HOP_INTERVAL=30
  if [[ "$VVV_PROTOCOL_MODE" != vless ]]; then
    while true; do
      read -r -p "请输入 Hysteria 2 每连接服务器强制限速 [默认 50M]：" input
      input="${input//[[:space:]]/}"; [[ -n "$input" ]] || input=50
      input="${input%[Mm]}"
      if [[ "$input" =~ ^[0-9]+$ ]] && ((10#$input>=30 && 10#$input<=100)); then VVV_HY2_LIMIT_MBPS="$((10#$input))"; break; fi
      echo "限速只允许 30-100 的整数，可写 50、50M 或 50m。"
    done
    local default_hop result
    default_hop="${VVV_PROXY_PORT},20000-50000"
    while true; do
      read -r -p "请输入 Hysteria 2 端口跳跃范围 [默认 ${default_hop}]：" input
      input="$(printf '%s' "$input" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
      [[ -n "$input" ]] || input="$default_hop"
      if result="$(python3 "$HY2_HOP_ENGINE" validate --spec "$input" --listen-port "$VVV_PROXY_PORT" --hop-interval 30 --check-udp 2>&1)"; then
        VVV_HY2_PORTS="$(jq -r '.ports' <<<"$result")"
        break
      fi
      echo "$result"
      echo "请重新输入 Hysteria 2 端口跳跃范围。"
    done
  fi
  export VVV_PROTOCOL_MODE VVV_PROXY_PORT VVV_REALITY_SNI VVV_HY2_LIMIT_MBPS VVV_HY2_PORTS VVV_HY2_HOP_INTERVAL
''')

replace_once('core-src/bootstrap.sh',
'''  for file in sub_center.py backup_manager.py rclone_manager.sh client_adapters.py adapter_manager.py center_transport.sh restore_manager.py diagnostic_report.py node_probe.py; do
''',
'''  for file in sub_center.py backup_manager.py rclone_manager.sh client_adapters.py client_package_renderer.py client_local_renderer.py hy2_port_hop.py hy2_port_hop.sh adapter_manager.py center_transport.sh restore_manager.py diagnostic_report.py node_probe.py; do
''')

replace_once('core-src/bootstrap.sh',
'''  install -m755 "$BASE_DIR/node_probe.py" /usr/local/lib/vvv/node_probe.py
''',
'''  install -m755 "$BASE_DIR/node_probe.py" /usr/local/lib/vvv/node_probe.py
  install -m755 "$BASE_DIR/client_adapters.py" /usr/local/lib/vvv/client_adapters.py
  install -m755 "$BASE_DIR/client_package_renderer.py" /usr/local/lib/vvv/client_package_renderer.py
  install -m755 "$BASE_DIR/client_local_renderer.py" /usr/local/lib/vvv/client_local_renderer.py
  install -m755 "$BASE_DIR/hy2_port_hop.py" /usr/local/lib/vvv/hy2_port_hop.py
  install -m755 "$BASE_DIR/hy2_port_hop.sh" /usr/local/lib/vvv/hy2_port_hop.sh
''')

replace_once('core-src/bootstrap.sh',
'''  chmod 700 /usr/local/sbin/vps
}
''',
'''  chmod 700 /usr/local/sbin/vps
  python3 /usr/local/lib/vvv/client_adapters.py >/dev/null
  python3 /usr/local/lib/vvv/client_local_renderer.py regenerate --obsolete Loon-Shadowrocket.txt --obsolete NekoBoxForAndroid.yaml >/dev/null || true
}
''')

replace_once('core-src/bootstrap.sh',
'''  local key="$1" combined="${2:-0}" landing_rc
''',
'''  local key="$1" combined="${2:-0}" landing_rc
  install -d -m700 /usr/local/lib/vvv
  install -m755 "$BASE_DIR/client_adapters.py" /usr/local/lib/vvv/client_adapters.py
  install -m755 "$BASE_DIR/client_package_renderer.py" /usr/local/lib/vvv/client_package_renderer.py
''')

replace_once('core-src/bootstrap.sh',
'''    [[ "$VVV_PROTOCOL_MODE" == vless ]] || echo "Hysteria 2 限速：${VVV_HY2_LIMIT_MBPS}M"
''',
'''    if [[ "$VVV_PROTOCOL_MODE" != vless ]]; then
      echo "Hysteria 2 限速：${VVV_HY2_LIMIT_MBPS}M"
      echo "Hysteria 2 端口跳跃：${VVV_HY2_PORTS}（每 ${VVV_HY2_HOP_INTERVAL} 秒切换）"
    fi
''')

replace_once('core-src/bootstrap.sh',
'''      VVV_HY2_LIMIT_MBPS="$(json_value "$MAIN_STATE" hy2_limit_mbps 50)"
      export VVV_PROTOCOL_MODE VVV_PROXY_PORT VVV_REALITY_SNI VVV_HY2_LIMIT_MBPS
''',
'''      VVV_HY2_LIMIT_MBPS="$(json_value "$MAIN_STATE" hy2_limit_mbps 50)"
      VVV_HY2_PORTS="$(json_value "$MAIN_STATE" port_hopping.ports "${VVV_PROXY_PORT},20000-50000")"
      VVV_HY2_HOP_INTERVAL="$(json_value "$MAIN_STATE" port_hopping.hop_interval_seconds 30)"
      export VVV_PROTOCOL_MODE VVV_PROXY_PORT VVV_REALITY_SNI VVV_HY2_LIMIT_MBPS VVV_HY2_PORTS VVV_HY2_HOP_INTERVAL
''')

# ---------------------------------------------------------------------------
# host.sh: install modules before the generated manager is invoked, persist the
# state, install nftables service, use the unified renderer, carry JPR3 fields,
# and expose strict quick-add commands.
# ---------------------------------------------------------------------------
replace_once('core-src/host.sh',
'''if [[ "$(id -u)" -ne 0 ]]; then
  echo "错误：请使用 root 用户执行。" >&2
  exit 1
fi

mkdir -p /usr/local/sbin
''',
'''if [[ "$(id -u)" -ne 0 ]]; then
  echo "错误：请使用 root 用户执行。" >&2
  exit 1
fi

HOST_SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
install -d -m700 /usr/local/lib/vvv
for module in client_adapters.py client_package_renderer.py hy2_port_hop.py hy2_port_hop.sh; do
  [[ -f "$HOST_SOURCE_DIR/$module" ]] || { echo "错误：缺少运行模块 $module。" >&2; exit 1; }
  install -m755 "$HOST_SOURCE_DIR/$module" "/usr/local/lib/vvv/$module"
done

mkdir -p /usr/local/sbin
''')

replace_once('core-src/host.sh',
'''RUN_MODE="${1:-}"
RUN_ARG="${2:-}"

STATE_DIR="/etc/jp-relay"
''',
'''RUN_MODE="${1:-}"
RUN_ARG="${2:-}"

STATE_DIR="/etc/jp-relay"
''')

replace_once('core-src/host.sh',
'''# Hysteria 2 每条连接及中转链路的上下行硬上限（Mbps）
HY2_LIMIT_MBPS="${VVV_HY2_LIMIT_MBPS:-50}"

DEFAULT_SNI="${VVV_REALITY_SNI:-www.softbank.jp}"
''',
'''# Hysteria 2 每条连接及中转链路的上下行硬上限（Mbps）
HY2_LIMIT_MBPS="${VVV_HY2_LIMIT_MBPS:-50}"
HY2_PORTS="${VVV_HY2_PORTS:-${VVV_PROXY_PORT:-443},20000-50000}"
HY2_HOP_INTERVAL="${VVV_HY2_HOP_INTERVAL:-30}"
HY2_HOP_ENGINE=/usr/local/lib/vvv/hy2_port_hop.py
HY2_HOP_WRAPPER=/usr/local/lib/vvv/hy2_port_hop.sh
CLIENT_PACKAGE_RENDERER=/usr/local/lib/vvv/client_package_renderer.py
CLIENT_ADAPTER=/usr/local/lib/vvv/client_adapters.py

DEFAULT_SNI="${VVV_REALITY_SNI:-www.softbank.jp}"
''')

replace_once('core-src/host.sh',
'''    ca-certificates curl unzip tar gzip openssl jq python3 python3-venv iproute2 procps \
    tzdata kmod util-linux || fail "代理依赖安装失败。若提示锁被占用，已等待最多 10 秒，请稍后重新运行。"
''',
'''    ca-certificates curl unzip tar gzip openssl jq python3 python3-venv iproute2 procps nftables \
    tzdata kmod util-linux || fail "代理依赖安装失败。若提示锁被占用，已等待最多 10 秒，请稍后重新运行。"
''')

replace_once('core-src/host.sh',
'''  [[ "$INSTALL_MODE" == hy2 ]] || echo "REALITY 伪装域名：$DEFAULT_SNI"
}
''',
'''  [[ "$INSTALL_MODE" == hy2 ]] || echo "REALITY 伪装域名：$DEFAULT_SNI"
  if [[ "$INSTALL_MODE" == dual || "$INSTALL_MODE" == hy2 ]]; then
    local validated
    validated="$(python3 "$HY2_HOP_ENGINE" validate --spec "$HY2_PORTS" --listen-port "$INSTALL_PORT" --hop-interval "$HY2_HOP_INTERVAL")" || return 1
    HY2_PORTS="$(jq -r '.ports' <<<"$validated")"
    HY2_HOP_INTERVAL="$(jq -r '.hop_interval_seconds' <<<"$validated")"
    echo "Hysteria 2 端口跳跃：${HY2_PORTS}（每 ${HY2_HOP_INTERVAL} 秒切换）"
  fi
}
''')

replace_once('core-src/host.sh',
'''  systemctl daemon-reload
  systemctl enable sing-box >/dev/null
}
''',
'''  "$HY2_HOP_WRAPPER" install-service
  systemctl daemon-reload
  systemctl enable sing-box >/dev/null
}
''')

replace_once('core-src/host.sh',
'''    jq -e '.schema==3 and .role=="japan-hub" and (.relays|type=="array") and ((.upstream_relays // [])|type=="array")' "$STATE_FILE" >/dev/null || fail "状态文件不是本脚本的 JPR3 格式。"
''',
'''    jq -e '(.schema==3 or .schema==4) and .role=="japan-hub" and (.relays|type=="array") and ((.upstream_relays // [])|type=="array")' "$STATE_FILE" >/dev/null || fail "状态文件不是本脚本的 JPR3 格式。"
''')

replace_once('core-src/host.sh',
'''    jq --argjson limit "${VVV_HY2_LIMIT_MBPS:-50}" '.hy2_limit_mbps=(.hy2_limit_mbps // $limit) | .temporary_nodes=(.temporary_nodes // [])' "$STATE_FILE" > "$migrated"
''',
'''    jq --argjson limit "${VVV_HY2_LIMIT_MBPS:-50}" --arg ports "$HY2_PORTS" --argjson interval "$HY2_HOP_INTERVAL" '
      .schema=4 |
      .hy2_limit_mbps=(.hy2_limit_mbps // $limit) |
      .port_hopping=(.port_hopping // {enabled:(.protocol_mode=="dual" or .protocol_mode=="hy2"),ports:$ports,hop_interval_seconds:$interval}) |
      .temporary_nodes=(.temporary_nodes // [])
    ' "$STATE_FILE" > "$migrated"
''')

replace_once('core-src/host.sh',
'''    HY2_LIMIT_MBPS="$(jq -r '.hy2_limit_mbps // 50' "$STATE_FILE")"
    echo "检测到本脚本状态，复用已保存的协议、端口、限速和全部密钥。"
''',
'''    HY2_LIMIT_MBPS="$(jq -r '.hy2_limit_mbps // 50' "$STATE_FILE")"
    HY2_PORTS="$(jq -r '.port_hopping.ports // (.listen_port|tostring)' "$STATE_FILE")"
    HY2_HOP_INTERVAL="$(jq -r '.port_hopping.hop_interval_seconds // 30' "$STATE_FILE")"
    echo "检测到本脚本状态，复用已保存的协议、端口、端口跳跃、限速和全部密钥。"
''')

replace_once('core-src/host.sh',
'''    --arg now "$now" \
    --argjson limit "$HY2_LIMIT_MBPS" \
''',
'''    --arg now "$now" \
    --argjson limit "$HY2_LIMIT_MBPS" \
    --arg hop_ports "$HY2_PORTS" \
    --argjson hop_interval "$HY2_HOP_INTERVAL" \
''')

replace_once('core-src/host.sh',
'''      schema:3,role:"japan-hub",protocol_mode:$mode,public_ip:$ip,listen_port:$port,
      sni:$sni,direct_base_name:$direct_base,xray_version:$xray_version,
      sing_box_version:$sing_version,hy2_limit_mbps:$limit,vless:$vless,hy2:$hy2,relays:[],upstream_relays:[],temporary_nodes:[],
''',
'''      schema:4,role:"japan-hub",protocol_mode:$mode,public_ip:$ip,listen_port:$port,
      sni:$sni,direct_base_name:$direct_base,xray_version:$xray_version,
      sing_box_version:$sing_version,hy2_limit_mbps:$limit,
      port_hopping:{enabled:($mode=="dual" or $mode=="hy2"),ports:$hop_ports,hop_interval_seconds:$hop_interval},
      vless:$vless,hy2:$hy2,relays:[],upstream_relays:[],temporary_nodes:[],
''')

replace_once('core-src/host.sh',
'''    systemctl daemon-reload
    systemctl restart sing-box || return 1
''',
'''    systemctl daemon-reload
    systemctl restart vvv-hy2-port-hop.service || return 1
    systemctl restart sing-box || return 1
''')

replace_once('core-src/host.sh',
'''  systemctl is-active --quiet sing-box || { echo "错误：主 sing-box 服务未运行。" >&2; return 1; }
''',
'''  systemctl is-active --quiet vvv-hy2-port-hop.service || { echo "错误：Hysteria 2 端口跳跃服务未运行。" >&2; return 1; }
  python3 "$HY2_HOP_ENGINE" status | jq -e '.active==true or ((input_filename|length)>=0 and false)' >/dev/null 2>&1 || {
    echo "错误：Hysteria 2 nftables 端口跳跃规则未生效。" >&2
    return 1
  }
  systemctl is-active --quiet sing-box || { echo "错误：主 sing-box 服务未运行。" >&2; return 1; }
''')

# Correct the overly clever jq expression above after literal integration; keep
# a simple Python status check that is stable even when jq versions differ.
replace_once('core-src/host.sh',
'''  python3 "$HY2_HOP_ENGINE" status | jq -e '.active==true or ((input_filename|length)>=0 and false)' >/dev/null 2>&1 || {
''',
'''  python3 "$HY2_HOP_ENGINE" status | jq -e '.active==true' >/dev/null 2>&1 || {
''')

replace_regex('core-src/host.sh',
              r'generate_client_files\(\) \{.*?\n\}\n\ngenerate_direct_client_files\(\) \{',
'''generate_client_files() {
  local state_path="$1" relay_id="$2" out_dir="$3" kind="${4:-relay}"
  [[ -x "$CLIENT_PACKAGE_RENDERER" && -x "$CLIENT_ADAPTER" ]] || fail "统一客户端渲染模块不存在。"
  python3 "$CLIENT_PACKAGE_RENDERER" \
    --state "$state_path" --kind "$kind" --id "$relay_id" --out "$out_dir" --adapter "$CLIENT_ADAPTER"
}

generate_direct_client_files() {''', flags=re.S)

replace_once('core-src/host.sh',
''' "sni":s["sni"],"hy2_limit_mbps":int(s.get("hy2_limit_mbps") or 50),"xray_version":s["xray_version"],"sing_box_version":s["sing_box_version"],
''',
''' "sni":s["sni"],"hy2_limit_mbps":int(s.get("hy2_limit_mbps") or 50),
 "japan_port_hopping":s.get("port_hopping") or {"enabled":False,"ports":str(s["listen_port"]),"hop_interval_seconds":30},
 "xray_version":s["xray_version"],"sing_box_version":s["sing_box_version"],
''')

quick_functions = r'''
quick_add_usage() {
  local command_name="$1"
  cat <<EOF_QUICK_USAGE
用法错误：必须提供一个完整参数。

正确格式：
${command_name} '线路名称|主机:端口:用户名:密码'

示例：
${command_name} '英国动态IP代理|gw.dataimpulse.com:10000:用户名:密码'

注意：
必须使用英文单引号包住完整参数。
线路名称和代理地址之间必须包含一个英文竖线 | 。
EOF_QUICK_USAGE
}

quick_add_upstream() {
  local command_name="${1:-}" proxy_protocol="${2:-}"
  shift 2 || true
  if (( $# != 1 )); then
    quick_add_usage "${command_name:-addhttp}"
    return 2
  fi
  local raw="$1" pipe_count name spec parsed host port username password parse_error
  pipe_count="$(python3 - "$raw" <<'PY_PIPE_COUNT'
import sys
print(sys.argv[1].count('|'))
PY_PIPE_COUNT
)"
  if [[ "$pipe_count" != 1 ]]; then
    echo "格式错误：没有收到完整的“线路名称|代理地址”参数。" >&2
    echo "这通常是因为没有使用英文单引号，导致 | 被 Bash 当成管道符，或参数中的 | 数量不正确。" >&2
    echo >&2
    quick_add_usage "$command_name" >&2
    return 2
  fi
  name="${raw%%|*}"; spec="${raw#*|}"
  name="$(printf '%s' "$name" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  spec="$(printf '%s' "$spec" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  if [[ -z "$name" || -z "$spec" || ${#name} -gt 64 || "$name" == *$'\n'* || "$name" == *$'\r'* || "$name" == *$'\t'* ]]; then
    echo "格式错误：线路名称和代理地址都不能为空，名称必须是 1-64 个字符且不能包含控制字符。" >&2
    quick_add_usage "$command_name" >&2
    return 2
  fi
  parsed="$(mktemp --suffix=.json /tmp/vvv-quick-upstream.XXXXXX)"
  TMP_FILES+=("$parsed")
  if ! parse_error="$(parse_upstream_spec "$spec" "$parsed" 2>&1)"; then
    echo "格式错误：$parse_error" >&2
    quick_add_usage "$command_name" >&2
    return 2
  fi
  host="$(jq -r '.host' "$parsed")"; port="$(jq -r '.port' "$parsed")"
  username="$(jq -r '.username' "$parsed")"; password="$(jq -r '.password' "$parsed")"
  CURRENT_STEP="使用 ${command_name} 新建或覆盖动态代理线路"
  prepare_add_or_overwrite_upstream "$proxy_protocol" "$host" "$port" "$username" "$password" "$name"
}

'''
replace_once('core-src/host.sh',
'''require_relay_subscription_registration() {
''',
quick_functions + '''require_relay_subscription_registration() {
''')

replace_once('core-src/host.sh',
'''  install_temp_cleanup_timer
}
''',
'''  cat > /usr/local/sbin/addhttp <<'EOF_ADDHTTP'
#!/usr/bin/env bash
exec /usr/local/sbin/jp-relay-manager --add-upstream addhttp http "$@"
EOF_ADDHTTP
  cat > /usr/local/sbin/addhttps <<'EOF_ADDHTTPS'
#!/usr/bin/env bash
exec /usr/local/sbin/jp-relay-manager --add-upstream addhttps http "$@"
EOF_ADDHTTPS
  cat > /usr/local/sbin/addsocks <<'EOF_ADDSOCKS'
#!/usr/bin/env bash
exec /usr/local/sbin/jp-relay-manager --add-upstream addsocks socks "$@"
EOF_ADDSOCKS
  cat > /usr/local/sbin/addsocks5 <<'EOF_ADDSOCKS5'
#!/usr/bin/env bash
exec /usr/local/sbin/jp-relay-manager --add-upstream addsocks5 socks "$@"
EOF_ADDSOCKS5
  chmod 700 /usr/local/sbin/addhttp /usr/local/sbin/addhttps /usr/local/sbin/addsocks /usr/local/sbin/addsocks5
  install_temp_cleanup_timer
}
''')

replace_once('core-src/host.sh',
'''  jq -e '.schema==3 and .role=="japan-hub" and (.relays|type=="array") and ((.upstream_relays // [])|type=="array")' "$STATE_FILE" >/dev/null || fail "JPR3 状态文件损坏。"
''',
'''  jq -e '(.schema==3 or .schema==4) and .role=="japan-hub" and (.relays|type=="array") and ((.upstream_relays // [])|type=="array")' "$STATE_FILE" >/dev/null || fail "JPR3 状态文件损坏。"
''')

replace_once('core-src/host.sh',
'''    HY2_LIMIT_MBPS="$(jq -r '.hy2_limit_mbps // 50' "$STATE_FILE")"
    echo "检测到现有 JPR3 状态：模式=${INSTALL_MODE}，端口=${INSTALL_PORT}，HY2 每连接强制上限=${HY2_LIMIT_MBPS}M。"
''',
'''    HY2_LIMIT_MBPS="$(jq -r '.hy2_limit_mbps // 50' "$STATE_FILE")"
    HY2_PORTS="$(jq -r '.port_hopping.ports // (.listen_port|tostring)' "$STATE_FILE")"
    HY2_HOP_INTERVAL="$(jq -r '.port_hopping.hop_interval_seconds // 30' "$STATE_FILE")"
    echo "检测到现有 JPR3 状态：模式=${INSTALL_MODE}，端口=${INSTALL_PORT}，HY2 跳跃=${HY2_PORTS}，每连接强制上限=${HY2_LIMIT_MBPS}M。"
''')

replace_once('core-src/host.sh',
'''  mode_has_hy2 && echo "Hysteria 2：UDP/$(jq -r '.listen_port' "$STATE_FILE")，sing-box=$(systemctl is-active sing-box)"
''',
'''  mode_has_hy2 && echo "Hysteria 2：UDP/$(jq -r '.port_hopping.ports' "$STATE_FILE") → $(jq -r '.listen_port' "$STATE_FILE")，每 $(jq -r '.port_hopping.hop_interval_seconds' "$STATE_FILE") 秒切换，sing-box=$(systemctl is-active sing-box)"
''')

replace_once('core-src/host.sh',
'''  --manage)
    CURRENT_STEP="检查日本运行环境"; log "$CURRENT_STEP"; check_runtime_environment
    acquire_manager_lock
    install_shortcuts
    management_menu
    ;;
  *)
''',
'''  --manage)
    CURRENT_STEP="检查日本运行环境"; log "$CURRENT_STEP"; check_runtime_environment
    acquire_manager_lock
    install_shortcuts
    management_menu
    ;;
  --add-upstream)
    CURRENT_STEP="检查日本运行环境"; check_runtime_environment
    acquire_manager_lock
    install_shortcuts
    quick_add_upstream "${@:2}"
    ;;
  *)
''')

# ---------------------------------------------------------------------------
# client_local_renderer.py: preserve hopping in local contexts and always
# remove obsolete duplicated files.
# ---------------------------------------------------------------------------
replace_once('core-src/client_local_renderer.py',
'''def hy2_node(name, server, port, password, sni, obfs, pin='', fingerprint='', limit=50):
''',
'''def hy2_node(name, server, port, password, sni, obfs, pin='', fingerprint='', limit=50,
             ports=None, hop_interval=30):
''')
replace_once('core-src/client_local_renderer.py',
'''        'fingerprint': str(fingerprint), 'limit_mbps': int(limit), 'udp': True,
''',
'''        'fingerprint': str(fingerprint), 'limit_mbps': int(limit), 'udp': True,
        'ports': str(ports or port), 'hop_interval_seconds': int(hop_interval or 30),
''')
replace_once('core-src/client_local_renderer.py',
'''    limit = int(state.get('hy2_limit_mbps') or 50)
    vless = state.get('vless') or {}
''',
'''    limit = int(state.get('hy2_limit_mbps') or 50)
    port_hopping = state.get('port_hopping') or {}
    hop_ports = str(port_hopping.get('ports') or port)
    hop_interval = int(port_hopping.get('hop_interval_seconds') or 30)
    vless = state.get('vless') or {}
''')
replace_once('core-src/client_local_renderer.py',
'''                                  hy2.get('certificate_fingerprint', ''), limit))
''',
'''                                  hy2.get('certificate_fingerprint', ''), limit,
                                  hop_ports, hop_interval))
''')
replace_once('core-src/client_local_renderer.py',
'''    limit = int(state.get('hy2_limit_mbps') or 50)
    nodes = []
''',
'''    limit = int(state.get('hy2_limit_mbps') or 50)
    port_hopping = state.get('japan_port_hopping') or {}
    hop_ports = str(port_hopping.get('ports') or port)
    hop_interval = int(port_hopping.get('hop_interval_seconds') or 30)
    nodes = []
''')
replace_once('core-src/client_local_renderer.py',
'''                              hy2.get('japan_certificate_fingerprint', ''), limit))
''',
'''                              hy2.get('japan_certificate_fingerprint', ''), limit,
                              hop_ports, hop_interval))
''')
replace_once('core-src/client_local_renderer.py',
'''def render_context(context, adapter, obsolete=()):
''',
'''def render_context(context, adapter, obsolete=()):
    obsolete = tuple(set(obsolete) | {'Loon-Shadowrocket.txt', 'NekoBoxForAndroid.yaml'})
''')

# ---------------------------------------------------------------------------
# sub_center.py: propagate hopping to subscriptions and add persistent stable-ID
# ordering plus transactional bulk rename/reorder operations.
# ---------------------------------------------------------------------------
replace_once('core-src/sub_center.py',
'''OVERRIDES = DATA / 'node-overrides.json'
TICKETS = DATA / 'relay-tickets.json'
''',
'''OVERRIDES = DATA / 'node-overrides.json'
ORDER = DATA / 'node-order.json'
TICKETS = DATA / 'relay-tickets.json'
''')

replace_once('core-src/sub_center.py',
'''            'limit_mbps': int(state.get('hy2_limit_mbps') or 50),
            'pin_b64': hy2.get('certificate_public_key_sha256'),
''',
'''            'limit_mbps': int(state.get('hy2_limit_mbps') or 50),
            'ports': str(((state.get('port_hopping') or {}).get('ports')) or port),
            'hop_interval_seconds': int(((state.get('port_hopping') or {}).get('hop_interval_seconds')) or 30),
            'pin_b64': hy2.get('certificate_public_key_sha256'),
''')

replace_regex('core-src/sub_center.py', r'def all_nodes\(\):.*?\n    return nodes\n', r'''def all_nodes():
    nodes, seen = [], set()
    overrides = read_json(OVERRIDES, {}) or {}
    for host in active_hosts():
        for node in nodes_from_host(host):
            if node['id'] in seen:
                continue
            seen.add(node['id'])
            custom = (overrides.get(node['id']) or {}).get('display_name')
            if custom:
                node['default_name'] = node['name']
                node['name'] = custom
            nodes.append(node)
    active_ids = [node['id'] for node in nodes]
    stored = read_json(ORDER, {'schema': 1, 'ids': []}) or {'schema': 1, 'ids': []}
    existing = [str(value) for value in stored.get('ids', []) if str(value) in seen]
    ordered_ids = existing + [value for value in active_ids if value not in existing]
    if stored.get('schema') != 1 or stored.get('ids') != ordered_ids:
        atomic_json(ORDER, {'schema': 1, 'ids': ordered_ids, 'updated_at': now()})
    positions = {value: index for index, value in enumerate(ordered_ids)}
    nodes.sort(key=lambda node: positions.get(node['id'], len(positions)))
    return nodes
''', flags=re.S)

replace_once('core-src/sub_center.py',
'''    if not (1 <= len(name) <= 64) or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise SystemExit('名称必须是 1-64 个字符，且不能包含换行或控制字符。')
''',
'''    if not (1 <= len(name) <= 64) or '|' in name or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise SystemExit('名称必须是 1-64 个字符，且不能包含 |、换行或控制字符。')
''')

bulk_code = r'''
def parse_pipe_values(value):
    text = str(value or '').strip()
    text = text.strip('|').strip()
    if not text:
        return []
    return [part.strip() for part in re.split(r'\|+', text) if part.strip()]


def validate_display_names(names, expected):
    if len(names) != expected:
        raise SystemExit(f'数量不一致：当前共有 {expected} 个节点，但输入了 {len(names)} 个名称。')
    for name in names:
        if not (1 <= len(name) <= 64) or '|' in name or any(ord(c) < 32 or ord(c) == 127 for c in name):
            raise SystemExit('每个名称必须是 1-64 个字符，且不能包含 |、换行或控制字符。')
    if len(names) != len(set(names)):
        raise SystemExit('名称不能重复。')


def bulk_rename(value):
    nodes = all_nodes()
    names = parse_pipe_values(value)
    validate_display_names(names, len(nodes))
    previous = read_json(OVERRIDES, {}) or {}
    updated = dict(previous)
    timestamp = now()
    for node, name in zip(nodes, names):
        updated[node['id']] = {'display_name': name, 'updated_at': timestamp}
    backup('before-node-bulk-rename', True)
    try:
        atomic_json(OVERRIDES, updated)
        count = regenerate()
    except Exception:
        atomic_json(OVERRIDES, previous)
        regenerate()
        raise
    backup('after-node-bulk-rename', True)
    return count


def reorder_nodes(value):
    nodes = all_nodes()
    names = parse_pipe_values(value)
    if len(names) != len(nodes):
        raise SystemExit(f'数量不一致：当前共有 {len(nodes)} 个节点，但输入了 {len(names)} 个名称。')
    if len(names) != len(set(names)):
        raise SystemExit('排序列表中不能出现重复名称。')
    by_name = {node['name']: node['id'] for node in nodes}
    if len(by_name) != len(nodes):
        raise SystemExit('当前订阅存在重名节点，请先批量重命名后再排序。')
    missing = [name for name in names if name not in by_name]
    extra = [node['name'] for node in nodes if node['name'] not in set(names)]
    if missing or extra:
        details = []
        if missing:
            details.append('不存在：' + '、'.join(missing))
        if extra:
            details.append('缺少：' + '、'.join(extra))
        raise SystemExit('排序列表必须完整使用当前节点名称；' + '；'.join(details))
    previous = read_json(ORDER, {'schema': 1, 'ids': []}) or {'schema': 1, 'ids': []}
    updated = {'schema': 1, 'ids': [by_name[name] for name in names], 'updated_at': now()}
    backup('before-node-reorder', True)
    try:
        atomic_json(ORDER, updated)
        count = regenerate()
    except Exception:
        atomic_json(ORDER, previous)
        regenerate()
        raise
    backup('after-node-reorder', True)
    return count


'''
replace_once('core-src/sub_center.py',
'''def reset_name(node_id_value):
''',
bulk_code + '''def reset_name(node_id_value):
''')

replace_once('core-src/sub_center.py',
'''    if not OVERRIDES.exists():
        atomic_json(OVERRIDES, {})
    regenerate()
''',
'''    if not OVERRIDES.exists():
        atomic_json(OVERRIDES, {})
    if not ORDER.exists():
        atomic_json(ORDER, {'schema': 1, 'ids': []})
    regenerate()
''')

replace_once('core-src/sub_center.py',
'''    rename = commands.add_parser('rename-node'); rename.add_argument('node_id'); rename.add_argument('name')
    reset = commands.add_parser('reset-name'); reset.add_argument('node_id')
''',
'''    rename = commands.add_parser('rename-node'); rename.add_argument('node_id'); rename.add_argument('name')
    bulk = commands.add_parser('bulk-rename'); bulk.add_argument('names')
    reorder = commands.add_parser('reorder-nodes'); reorder.add_argument('names')
    reset = commands.add_parser('reset-name'); reset.add_argument('node_id')
''')

replace_once('core-src/sub_center.py',
'''    elif args.command == 'reset-name':
        reset_name(args.node_id)
''',
'''    elif args.command == 'bulk-rename':
        print(bulk_rename(args.names))
    elif args.command == 'reorder-nodes':
        print(reorder_nodes(args.names))
    elif args.command == 'reset-name':
        reset_name(args.node_id)
''')

# ---------------------------------------------------------------------------
# center_manager.sh: dynamic bulk actions after node rows.
# ---------------------------------------------------------------------------
new_node_menu = r'''node_menu(){
  local rows count choice node_id name action new_name bulk_index order_index input
  while true; do
    mapfile -t rows < <(python3 "$SUB" list-nodes --tsv)
    count=${#rows[@]}
    echo; echo "========== 订阅节点管理 =========="
    if (( count==0 )); then echo "当前没有订阅节点。"; pause; return; fi
    local i
    for ((i=0;i<count;i++)); do IFS=$'\t' read -r node_id name _ <<<"${rows[$i]}"; echo "$((i+1)). $name"; done
    bulk_index=$((count+1)); order_index=$((count+2))
    echo "${bulk_index}. 批量重命名"
    echo "${order_index}. 重新排序"
    echo "0. 返回"
    read -r -p "请选择节点或操作：" choice
    [[ "$choice" == 0 ]] && return
    [[ "$choice" =~ ^[0-9]+$ ]] || { echo "请输入有效编号。"; continue; }
    choice=$((10#$choice))
    if (( choice==bulk_index )); then
      echo "请按当前顺序输入全部新名称，使用一个或多个 | 分隔；开头和结尾的 | 可省略。"
      read -r -p "批量名称：" input
      if python3 "$SUB" bulk-rename "$input" >/dev/null; then
        echo "批量重命名成功，共修改 ${count} 个节点。"
        echo "所有客户端订阅已重新生成，请在客户端中手动刷新统一订阅地址。"
      fi
      pause; continue
    fi
    if (( choice==order_index )); then
      echo "请按目标顺序输入当前节点名称，使用一个或多个 | 分隔；名称必须完整且不能重复。"
      read -r -p "目标顺序：" input
      if python3 "$SUB" reorder-nodes "$input" >/dev/null; then
        echo "节点重新排序成功，共 ${count} 个节点。"
        echo "所有客户端订阅已按新顺序重新生成，请在客户端中手动刷新统一订阅地址。"
      fi
      pause; continue
    fi
    (( choice>=1 && choice<=count )) || { echo "请输入有效编号。"; continue; }
    IFS=$'\t' read -r node_id name _ <<<"${rows[$((choice-1))]}"
    while true; do
      echo; echo "节点：$name"
      echo "1. 查看节点信息"
      echo "2. 修改客户端显示名称"
      echo "3. 恢复默认名称"
      echo "0. 返回"
      read -r -p "请选择：" action
      case "$action" in
        1) python3 "$SUB" show-node "$node_id" | jq .; pause;;
        2)
          read -r -p "请输入新的客户端显示名称（1-64个字符，不能包含 |）：" new_name
          if python3 "$SUB" rename-node "$node_id" "$new_name"; then
            name="$new_name"
            echo "订阅已重新生成，请在客户端中手动刷新。"
          fi
          pause
          ;;
        3) python3 "$SUB" reset-name "$node_id" && echo "订阅已重新生成，请在客户端中手动刷新。"; pause; break;;
        0) break;;
        *) echo "请输入 0-3。";;
      esac
    done
  done
}
'''
replace_regex('core-src/center_manager.sh', r'node_menu\(\)\{.*?\n\}\nhost_menu\(\)\{', new_node_menu + 'host_menu(){', flags=re.S)

# ---------------------------------------------------------------------------
# backup and diagnostics.
# ---------------------------------------------------------------------------
replace_once('core-src/backup_manager.py',
'''    Path('/var/lib/vvv-sub/node-overrides.json'),
''',
'''    Path('/var/lib/vvv-sub/node-overrides.json'),
    Path('/var/lib/vvv-sub/node-order.json'),
''')

replace_once('core-src/diagnostic_report.py',
'''SERVICES = ['xray','sing-box','caddy','vvv-sub','vvv-cloudflared','vvv-sync.timer','vvv-sync.path','vvv-temp-cleanup.timer','daily-reboot.timer']
''',
'''SERVICES = ['xray','sing-box','vvv-hy2-port-hop','caddy','vvv-sub','vvv-cloudflared','vvv-sync.timer','vvv-sync.path','vvv-temp-cleanup.timer','daily-reboot.timer']
''')
replace_once('core-src/diagnostic_report.py',
'''    add(lines, 'BBR 与时区', run(['bash','-lc','sysctl net.ipv4.tcp_congestion_control net.core.default_qdisc 2>/dev/null; timedatectl']))
''',
'''    add(lines, 'BBR 与时区', run(['bash','-lc','sysctl net.ipv4.tcp_congestion_control net.core.default_qdisc 2>/dev/null; timedatectl']))
    add(lines, 'Hysteria 2 端口跳跃', run(['bash','-lc','python3 /usr/local/lib/vvv/hy2_port_hop.py status 2>&1; nft -a list table inet vvv_hy2_hop 2>&1 || true']))
''')
replace_once('core-src/diagnostic_report.py',
'''    for path in ('/etc/vvv/roles.json','/etc/jp-relay/state.json','/etc/jp-relay/landing-state.json','/etc/vvv-sub/config.json','/etc/vvv/client.json'):
''',
'''    for path in ('/etc/vvv/roles.json','/etc/jp-relay/state.json','/etc/jp-relay/landing-state.json','/etc/vvv-sub/config.json','/etc/vvv/client.json','/var/lib/vvv-sub/node-order.json'):
''')

# ---------------------------------------------------------------------------
# landing.sh: consume Japan-side hopping from JPR3 and render after state is
# saved using the same canonical adapter. The landing server itself remains a
# fixed private 553-style endpoint and gets no nftables hopping service.
# ---------------------------------------------------------------------------
replace_once('core-src/landing.sh',
'''CLIENT_NODES_FILE="/root/中转客户端节点.txt"
''',
'''CLIENT_NODES_FILE="/root/中转客户端节点.txt"
CLIENT_PACKAGE_RENDERER=/usr/local/lib/vvv/client_package_renderer.py
CLIENT_ADAPTER=/usr/local/lib/vvv/client_adapters.py
''')

replace_once('core-src/landing.sh',
'''    (.sni|type=="string" and length>0) and
''',
'''    (.sni|type=="string" and length>0) and
    (if (.protocol_mode=="dual" or .protocol_mode=="hy2") then
       (.japan_port_hopping|type=="object") and
       (.japan_port_hopping.ports|type=="string" and length>0) and
       (.japan_port_hopping.hop_interval_seconds|type=="number")
     else true end) and
''')

replace_once('core-src/landing.sh',
'''  HY2_LIMIT_MBPS="$(printf '%s' "$PAIR_JSON" | jq -er '.hy2_limit_mbps // 50')"
''',
'''  HY2_LIMIT_MBPS="$(printf '%s' "$PAIR_JSON" | jq -er '.hy2_limit_mbps // 50')"
  JAPAN_HY2_PORTS="$(printf '%s' "$PAIR_JSON" | jq -er '.japan_port_hopping.ports // (.japan_port|tostring)')"
  JAPAN_HY2_HOP_INTERVAL="$(printf '%s' "$PAIR_JSON" | jq -er '.japan_port_hopping.hop_interval_seconds // 30')"
''')

replace_regex('core-src/landing.sh', r'generate_client_files\(\) \{.*?\n\}\n\nsave_state\(\) \{', r'''generate_client_files() {
  [ -x "$CLIENT_PACKAGE_RENDERER" ] && [ -x "$CLIENT_ADAPTER" ] || fail "统一客户端渲染模块不存在。"
  python3 "$CLIENT_PACKAGE_RENDERER" \
    --state "$STATE_FILE" --kind landing --out "$CLIENT_DIR" --adapter "$CLIENT_ADAPTER" >/dev/null
  cp "$CLIENT_DIR/客户端节点.txt" "$CLIENT_NODES_FILE"
  chmod 700 "$CLIENT_DIR"
  chmod 600 "$CLIENT_DIR"/* "$CLIENT_NODES_FILE"
}

save_state() {''', flags=re.S)

replace_once('core-src/landing.sh',
'''CURRENT_STEP="生成客户端配置"
log "$CURRENT_STEP"
generate_client_files

CURRENT_STEP="保存状态并安装 vps 查看命令"
log "$CURRENT_STEP"
save_state
install_shortcuts
''',
'''CURRENT_STEP="保存状态"
log "$CURRENT_STEP"
save_state

CURRENT_STEP="生成客户端配置并安装 vps 查看命令"
log "$CURRENT_STEP"
generate_client_files
install_shortcuts
''')

replace_once('core-src/landing.sh',
'''echo "日本入口：${JAPAN_PUBLIC_IP}:${JAPAN_PORT}"
''',
'''echo "日本入口：${JAPAN_PUBLIC_IP}:${JAPAN_PORT}"
mode_has_hy2 && echo "日本 Hysteria 2 端口跳跃：${JAPAN_HY2_PORTS}（每 ${JAPAN_HY2_HOP_INTERVAL} 秒切换）"
''')

# ---------------------------------------------------------------------------
# workflow: run new tests in the permanent validation pipeline.
# ---------------------------------------------------------------------------
replace_once('.github/workflows/validate.yml',
'''      - name: Validate lifecycle source uniqueness
        run: python3 tests/test_lifecycle_source_uniqueness.py
''',
'''      - name: Validate lifecycle source uniqueness
        run: python3 tests/test_lifecycle_source_uniqueness.py
      - name: Validate Hysteria 2 port hopping and client formats
        run: |
          python3 tests/test_hy2_port_hopping.py
          python3 tests/test_client_port_hopping.py
          python3 tests/test_subscription_node_order.py
          chmod +x tests/test_quick_upstream_commands.sh
          tests/test_quick_upstream_commands.sh
''')

print('HY2 feature patch applied successfully.')

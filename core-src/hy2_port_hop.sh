#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

STATE_FILE=${VVV_HY2_STATE_FILE:-/etc/jp-relay/state.json}
ENGINE=${VVV_HY2_HOP_ENGINE:-/usr/local/lib/vvv/hy2_port_hop.py}
SERVICE=/etc/systemd/system/vvv-hy2-port-hop.service

fail(){ echo "错误：$*" >&2; exit 1; }
state_value(){ jq -r "$1" "$STATE_FILE"; }
mode_has_hy2(){ [[ "$(state_value '.protocol_mode // empty')" == dual || "$(state_value '.protocol_mode // empty')" == hy2 ]]; }

require_state(){
  [[ -s "$STATE_FILE" ]] || fail "Hysteria 2 状态文件不存在：$STATE_FILE"
  [[ -x "$ENGINE" ]] || fail "Hysteria 2 端口跳跃引擎不存在：$ENGINE"
  command -v jq >/dev/null 2>&1 || fail "系统缺少 jq。"
}

apply_rules(){
  require_state
  if ! mode_has_hy2 || [[ "$(state_value '.port_hopping.enabled // false')" != true ]]; then
    python3 "$ENGINE" remove
    echo "Hysteria 2 端口跳跃未启用，已清理 VVV 专用 nftables 表。"
    return 0
  fi
  local port ports
  port="$(state_value '.listen_port')"
  ports="$(state_value '.port_hopping.ports')"
  python3 "$ENGINE" validate --spec "$ports" --listen-port "$port" >/dev/null
  python3 "$ENGINE" apply --spec "$ports" --listen-port "$port"
  echo "Hysteria 2 端口跳跃已生效：${ports} → UDP/${port}"
}

check_conflicts(){
  require_state
  local port ports
  port="$(state_value '.listen_port')"
  ports="$(state_value '.port_hopping.ports')"
  python3 "$ENGINE" validate \
    --spec "$ports" \
    --listen-port "$port" \
    --check-udp \
    --allow-listen-port "$port" \
    --allow-process 'sing-box'
}

install_service(){
  install -d -m755 /etc/systemd/system
  cat > "$SERVICE" <<'EOF_SERVICE'
[Unit]
Description=VVV Hysteria 2 UDP port hopping
Wants=network-online.target
After=network-online.target
Before=sing-box.service

[Service]
Type=oneshot
ExecStart=/usr/local/lib/vvv/hy2_port_hop.sh apply
ExecReload=/usr/local/lib/vvv/hy2_port_hop.sh apply
ExecStop=/usr/local/lib/vvv/hy2_port_hop.sh remove
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF_SERVICE
}

show_status(){
  require_state
  echo "监听端口：$(state_value '.listen_port')"
  echo "跳跃范围：$(state_value '.port_hopping.ports // "未配置"')"
  echo "跳跃间隔：$(state_value '.port_hopping.hop_interval_seconds // 30') 秒"
  python3 "$ENGINE" status
}

case "${1:-apply}" in
  apply) apply_rules;;
  remove) [[ -x "$ENGINE" ]] && python3 "$ENGINE" remove || true;;
  check-conflicts) check_conflicts;;
  install-service) install_service;;
  status) show_status;;
  *) echo "用法：hy2_port_hop.sh [apply|remove|check-conflicts|install-service|status]" >&2; exit 2;;
esac

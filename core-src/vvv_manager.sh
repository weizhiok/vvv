#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ROLE_FILE=/etc/vvv/roles.json
SYNC=/usr/local/lib/vvv/sync_agent.py
DIAG=/usr/local/lib/vvv/diagnostic_report.py
CLIENT_UPGRADE=/usr/local/lib/vvv/client_upgrade_engine.py
CLIENT_RENDERER=/usr/local/lib/vvv/client_local_renderer.py
role_has(){ jq -e --arg k "$1" '.roles[$k]==true' "$ROLE_FILE" >/dev/null 2>&1; }
pause(){ read -r -p "按回车返回……" _; }
show_roles(){
  echo "已安装模块："
  role_has proxy && echo "✓ 本机直连代理" || echo "✗ 本机直连代理"
  role_has center && echo "✓ 订阅中心" || echo "✗ 订阅中心"
  role_has relay && echo "✓ 中转管理" || echo "✗ 中转管理"
  role_has landing && echo "✓ 中转副机" || echo "✗ 中转副机"
}
primary(){ jq -r .primary_role "$ROLE_FILE"; }
register_center(){
  local code
  while true; do
    read -r -p "请输入订阅中心 VVC1 对接码（按回车取消）：" code
    code="${code//[[:space:]]/}"
    [[ -n "$code" ]] || return
    if [[ "$code" == JPR3.* ]]; then echo "对接码错误：这是中转副机安装密钥。"; continue; fi
    if python3 "$SYNC" validate-code "$code" >/dev/null 2>&1; then break; fi
    echo "对接码错误，请重新输入完整 VVC1。"
  done
  /usr/local/lib/vvv/register_sync.sh "$(primary)" "$code"
}
show_sync(){
  [[ -f /etc/vvv/client.json ]] && jq '{api_base_url,center_ip,host_id,role,registered_at,last_sync,last_result}' /etc/vvv/client.json || echo "尚未注册订阅中心。"
}
update_center_ip(){
  local ip
  read -r -p "请输入新的订阅中心公网 IPv4：" ip
  python3 "$SYNC" update-center-ip "$ip"
}
upgrade_client_support(){
  [[ -x "$CLIENT_UPGRADE" ]] || { echo "客户端支持升级引擎不存在，请先使用最新版 VVV 完成首次安装。"; return 1; }
  python3 "$CLIENT_UPGRADE" menu
}
show_local_clients(){
  if [[ -x "$CLIENT_RENDERER" ]]; then
    python3 "$CLIENT_RENDERER" regenerate >/dev/null
  fi
  /usr/local/sbin/jp-show-nodes || /usr/local/sbin/jp-relay-manager --manage
}
landing_menu(){
  while true; do
    echo
    echo "========== 中转副机管理 =========="
    echo "1. 进入节点与线路管理"
    echo "2. 升级客户端支持"
    echo "0. 退出"
    read -r -p "请输入编号：" choice
    case "$choice" in
      1)
        if [[ -x /usr/local/sbin/vvv-landing-original ]]; then
          /usr/local/sbin/vvv-landing-original
        else
          echo "中转副机管理命令不存在。"
        fi
        ;;
      2) upgrade_client_support; pause;;
      0) exit 0;;
      *) echo "请输入有效编号。";;
    esac
  done
}
[[ -f $ROLE_FILE ]] || { echo "VVV 角色配置不存在。"; exit 1; }
role_has landing && ! role_has proxy && landing_menu
while true; do
  echo; echo "========== VVV 管理 =========="; show_roles; echo
  n=1; declare -A act=()
  if role_has proxy; then echo "$n. 查看本机客户端配置"; act[$n]=local; ((n++)); fi
  if role_has relay; then echo "$n. 中转线路管理"; act[$n]=relay; ((n++)); fi
  if role_has center; then echo "$n. 订阅中心管理"; act[$n]=center; ((n++)); fi
  if [[ -f /etc/vvv/client.json ]]; then
    echo "$n. 立即同步订阅"; act[$n]=sync; ((n++))
    echo "$n. 查看订阅同步状态"; act[$n]=status; ((n++))
    if ! role_has center; then echo "$n. 修改订阅中心 IP 地址"; act[$n]=update_ip; ((n++)); fi
  fi
  echo "$n. 注册或更换订阅中心"; act[$n]=register; ((n++))
  echo "$n. 生成故障诊断报告"; act[$n]=diagnostic; ((n++))
  echo "$n. 升级客户端支持"; act[$n]=client_upgrade; ((n++))
  echo "0. 退出"
  read -r -p "请输入编号：" x
  [[ $x == 0 ]] && exit 0
  case "${act[$x]:-}" in
    local) show_local_clients; pause;;
    relay) /usr/local/sbin/jp-relay-manager --manage;;
    center) /usr/local/sbin/vvv-center;;
    sync) systemctl start vvv-sync.service; show_sync; pause;;
    status) show_sync; systemctl --no-pager status vvv-sync.timer vvv-sync.path 2>/dev/null || true; pause;;
    update_ip) update_center_ip; pause;;
    register) register_center; pause;;
    diagnostic) python3 "$DIAG"; pause;;
    client_upgrade) upgrade_client_support; pause;;
    *) echo "请输入有效编号。";;
  esac
done

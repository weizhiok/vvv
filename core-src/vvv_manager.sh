#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ROLE_FILE=/etc/vvv/roles.json
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
  read -r -p "请输入 VVV 主机接入码：" code
  [[ -n $code ]] || { echo "接入码不能为空。"; return; }
  /usr/local/lib/vvv/register_sync.sh "$(primary)" "$code"
}
show_sync(){
  [[ -f /etc/vvv/client.json ]] && jq '{base_url,host_id,role,registered_at,last_sync,last_result}' /etc/vvv/client.json || echo "尚未注册订阅中心。"
}
landing_menu(){
  [[ -x /usr/local/sbin/vvv-landing-original ]] && exec /usr/local/sbin/vvv-landing-original
  echo "中转副机管理命令不存在。"; exit 1
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
  else
    echo "$n. 注册或更换订阅中心"; act[$n]=register; ((n++))
  fi
  role_has center && { echo "$n. 查看已注册副机"; act[$n]=hosts; ((n++)); }
  echo "0. 退出"
  read -r -p "请输入编号：" x
  [[ $x == 0 ]] && exit 0
  case "${act[$x]:-}" in
    local) /usr/local/sbin/jp-show-nodes || /usr/local/sbin/jp-relay-manager --manage; pause;;
    relay) /usr/local/sbin/jp-relay-manager --manage;;
    center) /usr/local/sbin/vvv-center;;
    sync) systemctl start vvv-sync.service; show_sync; pause;;
    status) show_sync; systemctl --no-pager status vvv-sync.timer vvv-sync.path 2>/dev/null || true; pause;;
    register) register_center; pause;;
    hosts) /usr/local/sbin/vvv-center hosts; pause;;
    *) echo "请输入有效编号。";;
  esac
done

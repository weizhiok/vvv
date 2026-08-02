#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

CFG=/etc/vvv-sub/config.json
TRANSPORT=/usr/local/lib/vvv/center_transport.sh
BACKUP=/usr/local/lib/vvv/backup_manager.py
RCLONE=/usr/local/lib/vvv/rclone_manager.sh
ADAPTERS=/usr/local/lib/vvv/adapter_manager.py

[[ -s "$CFG" ]] || { echo "订阅中心配置不存在。" >&2; exit 1; }
pause(){ read -r -p "按回车返回……" _; }
get(){ jq -r "$1" "$CFG"; }
show_url(){
  echo "传输模式：$(get '.transport_mode')"
  echo "统一订阅地址：$(get '.subscription_url')"
  echo "所有支持的客户端均填写上面同一个地址。"
}
debug_headers(){
  local flag=/run/vvv-sub-header-debug.enabled log=/run/vvv-sub-header-debug.jsonl
  rm -f "$log"; : > "$log"; touch "$flag"
  echo
  echo "========== 客户端请求头识别调试 =========="
  echo "请在客户端中立即刷新统一订阅地址。"
  echo "监听时间：5 分钟；按 Ctrl+C 可提前结束。"
  echo "Authorization、Cookie、完整订阅后缀等敏感内容会自动隐藏。"
  trap 'rm -f "$flag"' EXIT INT TERM
  timeout --foreground 300 bash -c '
    tail -n0 -F /run/vvv-sub-header-debug.jsonl 2>/dev/null | while IFS= read -r line; do
      echo; echo "---------- 收到订阅请求 ----------"; printf "%s\n" "$line" | jq .
    done
  ' || true
  rm -f "$flag"
  trap - EXIT INT TERM
}
change_suffix(){
  local suffix
  read -r -p "请输入新的订阅后缀（6-32位大小写字母或数字）：" suffix
  "$TRANSPORT" change-suffix "$suffix"
}
show_hosts(){
  curl -fsS -H "Authorization: Bearer $(get '.master_token')" "http://127.0.0.1:$(get '.listen_port')/api/v1/hosts" | jq .
}

case "${1:-menu}" in
  url|urls) show_url; exit 0;;
  hosts) show_hosts; exit 0;;
  debug) debug_headers; exit 0;;
  adapters-update) python3 "$ADAPTERS" update; exit 0;;
  adapters-status) python3 "$ADAPTERS" status; exit 0;;
  transport-status) "$TRANSPORT" status; exit 0;;
  menu) ;;
  *) echo "用法：vvv-center [menu|url|hosts|debug|adapters-update|adapters-status|transport-status]" >&2; exit 2;;
esac

while true; do
  mode="$(get '.transport_mode')"
  echo
  echo "========== 订阅中心管理 =========="
  echo "当前传输：$mode"
  echo "统一地址：$(get '.subscription_url')"
  echo
  n=1; declare -A act=()
  echo "$n. 查看统一订阅地址"; act[$n]=url; ((n++))
  echo "$n. 客户端请求头识别调试"; act[$n]=debug; ((n++))
  echo "$n. 更新客户端适配器"; act[$n]=adapter_update; ((n++))
  echo "$n. 查看客户端适配器状态"; act[$n]=adapter_status; ((n++))
  echo "$n. 修改订阅地址后缀"; act[$n]=suffix; ((n++))
  if [[ "$mode" == direct-http ]]; then echo "$n. 开启 HTTPS 传输"; act[$n]=https; ((n++)); fi
  echo "$n. 查看传输与证书/Tunnel状态"; act[$n]=transport; ((n++))
  echo "$n. 查看本地备份"; act[$n]=backups; ((n++))
  echo "$n. 开启云备份功能"; act[$n]=cloud_enable; ((n++))
  echo "$n. 查看并测试云备份"; act[$n]=cloud_status; ((n++))
  echo "$n. 关闭或重新配置云备份"; act[$n]=cloud_change; ((n++))
  echo "$n. 查看已注册主机"; act[$n]=hosts; ((n++))
  echo "$n. 查看服务状态"; act[$n]=services; ((n++))
  echo "$n. 查看恢复信息"; act[$n]=recovery; ((n++))
  echo "0. 返回"
  read -r -p "请输入编号：" choice
  [[ "$choice" == 0 ]] && exit 0
  case "${act[$choice]:-}" in
    url) show_url; pause;;
    debug) debug_headers;;
    adapter_update) python3 "$ADAPTERS" update; pause;;
    adapter_status) python3 "$ADAPTERS" status; pause;;
    suffix) change_suffix; pause;;
    https) "$TRANSPORT" enable-https; pause;;
    transport) "$TRANSPORT" status; pause;;
    backups) python3 "$BACKUP" list; pause;;
    cloud_enable) "$RCLONE" enable; pause;;
    cloud_status) "$RCLONE" status; pause;;
    cloud_change)
      echo "1. 关闭云备份"; echo "2. 重新配置云备份"
      read -r -p "请选择：" sub
      [[ "$sub" == 1 ]] && "$RCLONE" disable || [[ "$sub" == 2 ]] && "$RCLONE" reconfigure
      pause
      ;;
    hosts) show_hosts; pause;;
    services)
      systemctl --no-pager --full status vvv-sub.service caddy.service vvv-sync.timer vvv-sync.path 2>/dev/null || true
      [[ "$mode" != tunnel ]] || systemctl --no-pager --full status vvv-cloudflared.service 2>/dev/null || true
      pause
      ;;
    recovery) cat /root/VVV-订阅中心恢复信息.txt; pause;;
    *) echo "请输入有效编号。";;
  esac
done

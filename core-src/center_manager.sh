#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

CFG=/etc/vvv-sub/config.json
TRANSPORT=/usr/local/lib/vvv/center_transport.sh
BACKUP=/usr/local/lib/vvv/backup_manager.py
RCLONE=/usr/local/lib/vvv/rclone_manager.sh
ADAPTERS=/usr/local/lib/vvv/adapter_manager.py
SUB=/usr/local/lib/vvv/sub_center.py
CLIENT_UPGRADE=/usr/local/lib/vvv/client_upgrade_engine.py
CLIENT_UPGRADE_URL=https://raw.githubusercontent.com/weizhiok/vvv/client-support/client_upgrade.py

[[ -s "$CFG" ]] || { echo "订阅中心配置不存在。" >&2; exit 1; }
pause(){ read -r -p "按回车返回……" _; }
get(){ jq -r "$1" "$CFG"; }
valid_domain(){ [[ "${1:-}" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]]; }
valid_port(){ [[ "${1:-}" =~ ^[0-9]+$ ]] && ((10#$1>=1 && 10#$1<=65535)); }
show_url(){
  echo "传输模式：$(get '.transport_mode')"
  echo "统一订阅地址：$(get '.subscription_url')"
  echo "副机同步 API：$(get '.api_base_url')（固定使用 IP，不随订阅域名变化）"
}
show_code(){
  [[ -s /etc/vvv-sub/registration.code ]] || "$TRANSPORT" rewrite-registration >/dev/null
  echo "==================== 订阅中心永久对接码 ===================="
  cat /etc/vvv-sub/registration.code; echo
  echo "============================================================"
}
debug_headers(){
  local flag=/run/vvv-sub-header-debug.enabled log=/run/vvv-sub-header-debug.jsonl
  local version
  version="$(python3 "$CLIENT_UPGRADE" status 2>/dev/null | jq -r '.version // 0' 2>/dev/null || echo 0)"
  rm -f "$log"; : > "$log"; touch "$flag"
  echo "客户端支持仓库：weizhiok/vvv"
  echo "客户端支持分支：client-support"
  echo "目标文件：client_upgrade.py"
  echo "默认升级地址：$CLIENT_UPGRADE_URL"
  echo
  echo "把下面输出的完整 JSON 发给一个全新的 ChatGPT 对话即可。"
  echo "新对话只需要根据 User-Agent 修改 client-support 分支中的 client_upgrade.py，"
  echo "不得修改安装器、代理核心、节点、系统设置或其他 VVV 功能。"
  echo "服务器升级方法：每台 VPS 输入 vps，选择退出上方最后一项“升级客户端支持”，直接回车。"
  echo
  echo "请在客户端中立即刷新统一订阅地址。监听 5 分钟，Ctrl+C 可提前结束。"
  trap 'rm -f "$flag" "$log"' EXIT INT TERM
  timeout --foreground 300 bash -c '
    tail -n0 -F /run/vvv-sub-header-debug.jsonl 2>/dev/null |
    while IFS= read -r line; do
      echo
      printf "%s\n" "$line" | jq \
        --arg url "'"$CLIENT_UPGRADE_URL"'" \
        --argjson version "'"$version"'" \
        ". + {client_support_handoff:{
          purpose:\"为 VVV 新增客户端订阅识别和每台 VPS 的本机客户端配置支持\",
          repository:\"weizhiok/vvv\",
          branch:\"client-support\",
          target_file:\"client_upgrade.py\",
          default_upgrade_url:\$url,
          installed_version:\$version,
          required_action:\"根据本条 User-Agent 只更新 client-support 分支中的 client_upgrade.py；增加识别规则或渲染器、提高 VERSION 并运行客户端支持测试。\",
          safety_boundary:\"不得修改 main 安装器、Xray、sing-box、节点状态、服务端代理配置、系统设置或其他 VVV 功能。\",
          server_upgrade_method:\"每台 VPS 输入 vps，选择退出上方最后一项“升级客户端支持”，直接回车使用默认地址。\",
          new_chat_instruction:\"这是 VVV 客户端支持扩展请求。请只修改 weizhiok/vvv 的 client-support 分支中 client_upgrade.py，并保持客户端升级与代理核心、节点和系统完全隔离。\"
        }}"
    done
  ' || true
  rm -f "$flag" "$log"; trap - EXIT INT TERM
}
node_menu(){
  local rows count choice node_id name action new_name
  while true; do
    mapfile -t rows < <(python3 "$SUB" list-nodes --tsv)
    count=${#rows[@]}
    echo; echo "========== 订阅节点管理 =========="
    if (( count==0 )); then echo "当前没有订阅节点。"; pause; return; fi
    local i
    for ((i=0;i<count;i++)); do IFS=$'\t' read -r node_id name _ <<<"${rows[$i]}"; echo "$((i+1)). $name"; done
    echo "0. 返回"
    read -r -p "请选择节点：" choice
    [[ "$choice" == 0 ]] && return
    [[ "$choice" =~ ^[0-9]+$ ]] && ((10#$choice>=1 && 10#$choice<=count)) || { echo "请输入有效编号。"; continue; }
    IFS=$'\t' read -r node_id name _ <<<"${rows[$((10#$choice-1))]}"
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
          read -r -p "请输入新的客户端显示名称（1-64个字符）：" new_name
          python3 "$SUB" rename-node "$node_id" "$new_name" && name="$new_name"
          pause
          ;;
        3) python3 "$SUB" reset-name "$node_id"; pause; break;;
        0) break;;
        *) echo "请输入 0-3。";;
      esac
    done
  done
}
host_menu(){
  local rows count choice host_id role host name action confirm
  while true; do
    mapfile -t rows < <(python3 "$SUB" list-hosts --tsv)
    count=${#rows[@]}
    echo; echo "========== 已注册主机 =========="
    if (( count==0 )); then echo "暂无已注册主机。"; pause; return; fi
    local i
    for ((i=0;i<count;i++)); do IFS=$'\t' read -r host_id role host name _ <<<"${rows[$i]}"; echo "$((i+1)). ${name:-$host} [$role]"; done
    echo "0. 返回"
    read -r -p "请选择：" choice
    [[ "$choice" == 0 ]] && return
    [[ "$choice" =~ ^[0-9]+$ ]] && ((10#$choice>=1 && 10#$choice<=count)) || { echo "请输入有效编号。"; continue; }
    IFS=$'\t' read -r host_id role host name _ <<<"${rows[$((10#$choice-1))]}"
    echo "1. 查看节点"; echo "2. 删除该副机及其节点"; echo "0. 返回"
    read -r -p "请选择：" action
    case "$action" in
      1) python3 "$SUB" show-host "$host_id" | jq .; pause;;
      2)
        read -r -p "输入 Y 确认删除：" confirm
        [[ "$confirm" =~ ^[Yy]$ ]] && python3 "$SUB" delete-host "$host_id" || echo "已取消。"
        pause
        ;;
    esac
  done
}
change_suffix(){ local v; read -r -p "请输入新订阅后缀（6-32位字母或数字）：" v; "$TRANSPORT" change-suffix "$v"; }
change_domain(){
  local mode v
  mode="$(get '.transport_mode')"
  if [[ "$mode" == tunnel ]]; then read -r -p "请输入新的 Tunnel 订阅域名：" v; else read -r -p "请输入新的 HTTPS 域名（回车使用公网 IP）：" v; fi
  v="${v,,}"; v="${v%.}"; "$TRANSPORT" change-domain "$v"
}
change_port(){ local v; read -r -p "请输入新的直接订阅端口：" v; "$TRANSPORT" change-port "$v"; }
change_token(){ local v; read -r -p "请输入新的 Cloudflare Tunnel Token：" v; v="${v//[[:space:]]/}"; "$TRANSPORT" change-tunnel-token "$v"; }
switch_secure(){
  local mode domain token port
  mode="$(get '.transport_mode')"
  case "$mode" in
    tunnel)
      read -r -p "请输入 HTTPS 域名（回车使用公网 IP）：" domain
      domain="${domain,,}"; domain="${domain%.}"
      read -r -p "请输入 HTTPS 端口 [默认 8443]：" port; [[ -n "$port" ]] || port=8443
      "$TRANSPORT" switch-secure https "$domain" "$port"
      ;;
    direct-https)
      read -r -p "请输入 Tunnel 订阅域名：" domain; domain="${domain,,}"; domain="${domain%.}"
      read -r -p "请输入 Cloudflare Tunnel Token：" token; token="${token//[[:space:]]/}"
      "$TRANSPORT" switch-secure tunnel "$domain" "$token"
      ;;
    direct-http) echo "当前是 HTTP 调试模式。为避免复杂迁移，本菜单只允许直接 HTTPS 与 Tunnel 相互切换。"; return 1;;
  esac
}
transport_menu(){
  local choice mode
  while true; do
    mode="$(get '.transport_mode')"
    echo; echo "========== 订阅入口管理 =========="
    echo "当前模式：$mode"; echo "当前地址：$(get '.subscription_url')"; echo
    echo "1. 修改订阅后缀"
    echo "2. 修改订阅域名"
    [[ "$mode" == direct-https ]] && echo "3. 修改订阅端口" || echo "3. 修改订阅端口（当前模式不可用）"
    [[ "$mode" == tunnel ]] && echo "4. 修改 Tunnel Token" || echo "4. 修改 Tunnel Token（当前模式不可用）"
    echo "5. 切换 HTTPS/Tunnel 模式"
    echo "6. 查看传输与证书/Tunnel 状态"
    echo "0. 返回"
    read -r -p "请选择：" choice
    case "$choice" in
      1) change_suffix; pause;;
      2) change_domain; pause;;
      3) [[ "$mode" == direct-https ]] && change_port || echo "当前不可用。"; pause;;
      4) [[ "$mode" == tunnel ]] && change_token || echo "当前不可用。"; pause;;
      5) switch_secure; pause;;
      6) "$TRANSPORT" status; pause;;
      0) return;;
      *) echo "请输入 0-6。";;
    esac
  done
}
cloud_menu(){
  local x
  while true; do
    echo; echo "========== 云备份管理 =========="
    echo "1. 开启云备份"
    echo "2. 查看并测试云备份"
    echo "3. 查看最近备份"
    echo "4. 重新授权云盘"
    echo "5. 关闭云备份"
    echo "0. 返回"
    read -r -p "请选择：" x
    case "$x" in
      1) "$RCLONE" enable; pause;; 2) "$RCLONE" status; pause;;
      3) python3 "$BACKUP" list; pause;; 4) "$RCLONE" reconfigure; pause;;
      5) "$RCLONE" disable; pause;; 0) return;; *) echo "请输入 0-5。";;
    esac
  done
}

case "${1:-menu}" in
  url|urls) show_url; exit;; code) show_code; exit;; hosts) python3 "$SUB" list-hosts; exit;; menu) ;;
  *) echo "用法：vvv-center [menu|url|code|hosts]" >&2; exit 2;;
esac

while true; do
  echo; echo "========== 订阅中心管理 =========="
  echo "统一地址：$(get '.subscription_url')"; echo
  echo "1. 查看统一订阅地址"
  echo "2. 查看订阅中心对接码"
  echo "3. 订阅节点管理"
  echo "4. 订阅入口管理"
  echo "5. 客户端请求头识别调试"
  echo "6. 升级客户端支持（与 vps 菜单相同）"
  echo "7. 查看客户端支持状态"
  echo "8. 云备份管理"
  echo "9. 已注册主机管理"
  echo "10. 查看服务状态"
  echo "0. 返回"
  read -r -p "请输入编号：" choice
  case "$choice" in
    1) show_url; pause;; 2) show_code; pause;; 3) node_menu;; 4) transport_menu;;
    5) debug_headers;; 6) python3 "$CLIENT_UPGRADE" menu; pause;; 7) python3 "$CLIENT_UPGRADE" status; pause;;
    8) cloud_menu;; 9) host_menu;;
    10) systemctl --no-pager --full status vvv-sub.service caddy.service vvv-sync.timer vvv-sync.path vvv-temp-cleanup.timer 2>/dev/null || true; [[ "$(get '.transport_mode')" != tunnel ]] || systemctl --no-pager --full status vvv-cloudflared.service 2>/dev/null || true; pause;;
    0) exit;; *) echo "请输入有效编号。";;
  esac
done

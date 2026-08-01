#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ "$(id -u)" -eq 0 ]] || { echo "错误：请使用 root 用户运行。" >&2; exit 1; }

valid_port(){ [[ "${1:-}" =~ ^[0-9]+$ ]] && ((10#$1>=1 && 10#$1<=65535)); }
valid_domain(){ [[ "${1:-}" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]]; }
port_in_use(){ ss -H -lnt "sport = :$1" 2>/dev/null | grep -q .; }
install_host(){
  bash "$BASE_DIR/host.sh"
  [[ -x /usr/local/sbin/vps ]] && cp -f /usr/local/sbin/vps /usr/local/sbin/vvv-host-original
}
enable_relay(){
  [[ -f /etc/jp-relay/state.json ]] || return 1
  local tmp
  tmp="$(mktemp)"
  jq '.relay_manager_enabled=true | .updated_at=(now|todate)' /etc/jp-relay/state.json > "$tmp"
  install -m600 "$tmp" /etc/jp-relay/state.json
  rm -f "$tmp"
}
install_unified_manager(){
  install -d -m700 /etc/vvv /usr/local/lib/vvv
  install -m755 "$BASE_DIR/vvv_manager.sh" /usr/local/lib/vvv/vvv_manager.sh
  install -m755 "$BASE_DIR/register_sync.sh" /usr/local/lib/vvv/register_sync.sh
  install -m755 "$BASE_DIR/sync_agent.py" /usr/local/lib/vvv/sync_agent.py
  cat > /usr/local/sbin/vps <<'EOF'
#!/usr/bin/env bash
exec /usr/local/lib/vvv/vvv_manager.sh "$@"
EOF
  chmod 700 /usr/local/sbin/vps
}
write_roles(){
  local center=$1 relay=$2 landing=$3 proxy=$4 role=$5
  install -d -m700 /etc/vvv
  jq -n --arg role "$role" --argjson c "$center" --argjson r "$relay" --argjson l "$landing" --argjson p "$proxy" \
    '{schema:1,primary_role:$role,roles:{center:$c,relay:$r,landing:$l,proxy:$p}}' > /etc/vvv/roles.json
  chmod 600 /etc/vvv/roles.json
}
ask_code(){
  local __var=$1 prompt=$2 value
  read -r -p "$prompt（直接回车表示暂不注册）：" value
  printf -v "$__var" '%s' "$value"
}
ask_proxy_parameters(){
  local choice input
  echo
  echo "========== 安装参数（全部前置设置） =========="
  echo
  echo "请选择要安装的代理协议："
  echo "1. 同时安装双协议（TCP + UDP）【默认】"
  echo "2. 只安装 VLESS + XTLS Vision + REALITY（TCP）"
  echo "3. 只安装 Hysteria 2（QUIC/UDP）"
  while true; do
    read -r -p "请输入编号 [默认 1]：" choice
    [[ -n "$choice" ]] || choice=1
    case "$choice" in
      1) VVV_PROTOCOL_MODE=dual; break ;;
      2) VVV_PROTOCOL_MODE=vless; break ;;
      3) VVV_PROTOCOL_MODE=hy2; break ;;
      *) echo "请输入 1、2 或 3。" ;;
    esac
  done
  while true; do
    read -r -p "请输入代理监听端口 [默认 443]：" input
    input="${input//[[:space:]]/}"
    [[ -n "$input" ]] || input=443
    if valid_port "$input"; then VVV_PROXY_PORT="$((10#$input))"; break; fi
    echo "端口必须是 1–65535 之间的数字。"
  done
  VVV_REALITY_SNI=www.softbank.jp
  if [[ "$VVV_PROTOCOL_MODE" != hy2 ]]; then
    while true; do
      read -r -p "请输入 VLESS + REALITY 伪装域名 [默认 www.softbank.jp]：" input
      input="${input,,}"; input="${input%.}"
      [[ -n "$input" ]] || input=www.softbank.jp
      if valid_domain "$input"; then VVV_REALITY_SNI="$input"; break; fi
      echo "REALITY 伪装域名格式不正确，请重新输入。"
    done
  fi
  export VVV_PROTOCOL_MODE VVV_PROXY_PORT VVV_REALITY_SNI
}
ask_center_parameters(){
  local input
  echo
  while true; do
    read -r -p "请输入订阅域名（直接回车使用本机 IP）：" input
    input="${input,,}"; input="${input%.}"
    if [[ -z "$input" ]] || valid_domain "$input"; then VVV_SUB_DOMAIN="$input"; break; fi
    echo "域名格式不正确，请重新输入。"
  done
  while true; do
    read -r -p "请输入订阅服务端口 [默认 8443]：" input
    input="${input//[[:space:]]/}"
    [[ -n "$input" ]] || input=8443
    if ! valid_port "$input"; then echo "端口必须是 1–65535 之间的数字。"; continue; fi
    input="$((10#$input))"
    if [[ "$input" == 443 ]]; then echo "订阅服务端口不能使用 443。"; continue; fi
    if [[ "$input" == "${VVV_PROXY_PORT:-}" ]]; then echo "订阅服务端口不能与代理端口相同。"; continue; fi
    if port_in_use "$input"; then echo "TCP 端口 ${input} 已被占用，请输入其他端口。"; continue; fi
    VVV_SUB_PORT="$input"; break
  done
  export VVV_SUB_DOMAIN VVV_SUB_PORT
}
show_parameter_summary(){
  local role_name protocol_name
  case "$choice" in
    1) role_name="订阅中心（含自身代理）" ;;
    2) role_name="中转主机（含自身代理）" ;;
    3) role_name="中转副机" ;;
    4) role_name="直连代理" ;;
    5) role_name="以上全部安装（不含副机）" ;;
  esac
  echo
  echo "========== 安装参数总览 =========="
  echo "安装角色：$role_name"
  if [[ "$choice" == 3 ]]; then
    echo "JPR3 密钥：已填写（${#key} 个字符）"
  else
    case "$VVV_PROTOCOL_MODE" in dual) protocol_name="VLESS + Hysteria 2";; vless) protocol_name="仅 VLESS";; hy2) protocol_name="仅 Hysteria 2";; esac
    echo "代理协议：$protocol_name"
    echo "代理端口：$VVV_PROXY_PORT"
    [[ "$VVV_PROTOCOL_MODE" == hy2 ]] || echo "REALITY 伪装域名：$VVV_REALITY_SNI"
    if [[ "$choice" == 1 || "$choice" == 5 ]]; then
      echo "订阅入口：${VVV_SUB_DOMAIN:-本机 IP}"
      echo "订阅端口：$VVV_SUB_PORT"
    elif [[ "$choice" == 2 || "$choice" == 4 ]]; then
      [[ -n "$code" ]] && echo "订阅中心接入码：已填写" || echo "订阅中心接入码：未填写（独立使用）"
    fi
  fi
  echo "=================================="
  echo "参数已收集完毕，直接开始全自动安装。"
}

cat <<'EOF'
========== VVV 一体化安装管理 ==========

1. 安装订阅中心（含自身代理）
   订阅中心 + 本机直连代理

2. 安装中转主机（含自身代理）
   本机直连代理 + VPS中转 + HTTP/HTTPS/SOCKS5中转

3. 安装中转副机
   使用对接密钥自动安装

4. 安装直连代理
   不安装订阅中心，不安装中转管理，只安装本机直连代理

5. 以上全部安装（不含副机）
   订阅中心 + 本机直连代理 + 全部中转管理

0. 退出
EOF
while true; do
  read -r -p "请输入编号：" choice
  case "$choice" in 0|1|2|3|4|5) break ;; *) echo "请输入 0-5。" ;; esac
done
[[ "$choice" == 0 ]] && exit 0

# 真正安装前，一次性收集该角色需要的全部参数。
code=""; key=""
case "$choice" in
  1) ask_proxy_parameters; ask_center_parameters ;;
  2) ask_proxy_parameters; ask_code code "请输入订阅中心接入码" ;;
  3)
    read -r -p "请输入完整 JPR3 对接密钥：" key
    [[ "$key" == JPR3.* ]] || { echo "错误：对接密钥必须以 JPR3. 开头。" >&2; exit 1; }
    ;;
  4) ask_proxy_parameters; ask_code code "请输入订阅中心接入码" ;;
  5) ask_proxy_parameters; ask_center_parameters ;;
esac
show_parameter_summary

case "$choice" in
  1)
    install_host
    write_roles true false false true center
    bash "$BASE_DIR/center_install.sh"
    code="$(cat /etc/vvv-sub/registration.code)"
    bash "$BASE_DIR/register_sync.sh" center "$code"
    ;;
  2)
    install_host; enable_relay
    write_roles false true false true relay
    bash "$BASE_DIR/register_sync.sh" relay "$code"
    ;;
  3)
    tmp="$(mktemp /tmp/vvv-landing.XXXXXX.sh)"
    awk -v key="$key" 'BEGIN{done=0} !done && /^PAIRING_KEY=/ {print "PAIRING_KEY=\047" key "\047"; done=1; next} {print}' "$BASE_DIR/landing.sh" > "$tmp"
    chmod 700 "$tmp"; sh "$tmp"; rm -f "$tmp"
    [[ -x /usr/local/sbin/vps ]] && cp -f /usr/local/sbin/vps /usr/local/sbin/vvv-landing-original
    write_roles false false true false landing
    ;;
  4)
    install_host
    write_roles false false false true direct
    bash "$BASE_DIR/register_sync.sh" direct "$code"
    ;;
  5)
    install_host; enable_relay
    write_roles true true false true all
    bash "$BASE_DIR/center_install.sh"
    code="$(cat /etc/vvv-sub/registration.code)"
    bash "$BASE_DIR/register_sync.sh" center-relay "$code"
    ;;
esac
install_unified_manager
printf '\nVVV 安装完成。以后统一输入：vps\n'
/usr/local/sbin/vps

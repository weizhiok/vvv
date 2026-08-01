#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ "$(id -u)" -eq 0 ]] || { echo "错误：请使用 root 用户运行。" >&2; exit 1; }
[[ -r /etc/os-release ]] || { echo "错误：无法读取 /etc/os-release。" >&2; exit 1; }
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == debian && "${VERSION_ID:-}" == 13 ]] || { echo "错误：VVV 仅支持 Debian 13。当前系统：${PRETTY_NAME:-未知}" >&2; exit 1; }
command -v systemctl >/dev/null 2>&1 || { echo "错误：Debian 13 缺少 systemd。" >&2; exit 1; }

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
    read -r -p "请输入订阅 HTTPS 域名（必须已解析到本机）：" input
    input="${input,,}"; input="${input%.}"
    if [[ -z "$input" ]]; then
      echo "订阅中心只提供 HTTPS，域名不能为空。"
      continue
    fi
    if valid_domain "$input"; then VVV_SUB_DOMAIN="$input"; break; fi
    echo "域名格式不正确，请重新输入。"
  done
  while true; do
    read -r -p "请输入订阅 HTTPS 端口 [默认 8443]：" input
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
jpr_registration_code(){
  local value="$1" rest encoded mod padded
  rest="${value#JPR3.}"; encoded="${rest%%.*}"
  mod=$((${#encoded} % 4))
  case "$mod" in 0) padded="$encoded";; 2) padded="${encoded}==";; 3) padded="${encoded}=";; *) return 1;; esac
  printf '%s' "$padded" | tr '_-' '/+' | base64 -d 2>/dev/null | jq -r '.subscription_registration_code // empty'
}
show_parameter_summary(){
  local role_name protocol_name
  case "$choice" in
    1) role_name="订阅中心+中转主机（含自身代理）" ;;
    2) role_name="仅订阅中心（含自身代理）" ;;
    3) role_name="仅中转主机（含自身代理）" ;;
    4) role_name="仅中转副机（通过主机代理）" ;;
    5) role_name="仅直连代理" ;;
  esac
  echo
  echo "========== 安装参数总览 =========="
  echo "安装角色：$role_name"
  if [[ "$choice" == 4 ]]; then
    echo "JPR3 密钥：已填写（${#key} 个字符）"
  else
    case "$VVV_PROTOCOL_MODE" in dual) protocol_name="VLESS + Hysteria 2";; vless) protocol_name="仅 VLESS";; hy2) protocol_name="仅 Hysteria 2";; esac
    echo "代理协议：$protocol_name"
    echo "代理端口：$VVV_PROXY_PORT"
    [[ "$VVV_PROTOCOL_MODE" == hy2 ]] || echo "REALITY 伪装域名：$VVV_REALITY_SNI"
    if [[ "$choice" == 1 || "$choice" == 2 ]]; then
      echo "订阅入口：https://${VVV_SUB_DOMAIN}:${VVV_SUB_PORT}"
    elif [[ "$choice" == 3 || "$choice" == 5 ]]; then
      [[ -n "$code" ]] && echo "订阅中心接入码：已填写" || echo "订阅中心接入码：未填写（独立使用）"
    fi
  fi
  echo "=================================="
  echo "参数已收集完毕，直接开始全自动安装。"
}

cat <<'EOF'
========== VVV 一体化安装管理 ==========

1. 安装订阅中心+中转主机（含自身代理）

2. 仅安装订阅中心（含自身代理）

3. 仅安装中转主机（含自身代理）

4. 仅安装中转副机（通过主机代理）

5. 仅安装直连代理

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
  2) ask_proxy_parameters; ask_center_parameters ;;
  3) ask_proxy_parameters; ask_code code "请输入订阅中心接入码" ;;
  4)
    read -r -p "请输入完整 JPR3 对接密钥：" key
    [[ "$key" == JPR3.* ]] || { echo "错误：对接密钥必须以 JPR3. 开头。" >&2; exit 1; }
    ;;
  5) ask_proxy_parameters; ask_code code "请输入订阅中心接入码" ;;
esac
show_parameter_summary

case "$choice" in
  1)
    install_host; enable_relay
    write_roles true true false true center-relay
    bash "$BASE_DIR/center_install.sh"
    code="$(cat /etc/vvv-sub/registration.code)"
    bash "$BASE_DIR/register_sync.sh" center-relay "$code"
    ;;
  2)
    install_host
    write_roles true false false true center
    bash "$BASE_DIR/center_install.sh"
    code="$(cat /etc/vvv-sub/registration.code)"
    bash "$BASE_DIR/register_sync.sh" center "$code"
    ;;
  3)
    install_host; enable_relay
    write_roles false true false true relay
    bash "$BASE_DIR/register_sync.sh" relay "$code"
    ;;
  4)
    tmp="$(mktemp /tmp/vvv-landing.XXXXXX.sh)"
    awk -v key="$key" 'BEGIN{done=0} !done && /^PAIRING_KEY=/ {print "PAIRING_KEY=\\047" key "\\047"; done=1; next} {print}' "$BASE_DIR/landing.sh" > "$tmp"
    chmod 700 "$tmp"; sh "$tmp"; rm -f "$tmp"
    [[ -x /usr/local/sbin/vps ]] && cp -f /usr/local/sbin/vps /usr/local/sbin/vvv-landing-original
    write_roles false false true false landing
    code="$(jpr_registration_code "$key" || true)"
    bash "$BASE_DIR/register_sync.sh" landing "$code"
    ;;
  5)
    install_host
    write_roles false false false true direct
    bash "$BASE_DIR/register_sync.sh" direct "$code"
    ;;
esac
install_unified_manager
printf '\nVVV 安装完成。以后统一输入：vps\n'
/usr/local/sbin/vps

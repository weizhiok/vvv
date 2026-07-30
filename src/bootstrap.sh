#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ "$(id -u)" -eq 0 ]] || { echo "错误：请使用 root 用户运行。" >&2; exit 1; }

valid_port(){ [[ "${1:-}" =~ ^[0-9]+$ ]] && ((10#$1>=1 && 10#$1<=65535)); }
valid_domain(){ [[ "${1:-}" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ ]]; }

install_host(){
  VVV_PROTOCOL_MODE="$protocol_mode" VVV_PROXY_PORT="$proxy_port" VVV_REALITY_SNI="$reality_sni" bash "$BASE_DIR/host.sh"
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
  cat > /usr/local/sbin/vps <<'SH'
#!/usr/bin/env bash
exec /usr/local/lib/vvv/vvv_manager.sh "$@"
SH
  chmod 700 /usr/local/sbin/vps
}

write_roles(){
  local center=$1 relay=$2 landing=$3 proxy=$4 role=$5
  install -d -m700 /etc/vvv
  jq -n --arg role "$role" --argjson c "$center" --argjson r "$relay" --argjson l "$landing" --argjson p "$proxy" \
    '{schema:1,primary_role:$role,roles:{center:$c,relay:$r,landing:$l,proxy:$p}}' > /etc/vvv/roles.json
  chmod 600 /etc/vvv/roles.json
}

cat <<'MENU'
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
MENU

while true; do
  read -r -p "请输入编号：" choice
  case "$choice" in 0|1|2|3|4|5) break;; *) echo "请输入 0-5。";; esac
done
[[ "$choice" == 0 ]] && exit 0

protocol_mode=""
proxy_port=""
reality_sni=""
sub_domain=""
sub_port=""
registration_code=""
pairing_key=""

if [[ "$choice" != 3 ]]; then
  echo
  echo "========== 安装参数（全部前置设置） =========="
  while true; do
    cat <<'EOF2'
请选择代理协议：
1. 同时安装双协议（TCP/443 + UDP/443）【默认】
2. 只安装 VLESS + XTLS Vision + REALITY
3. 只安装 Hysteria 2
EOF2
    read -r -p "请输入编号 [默认 1]：" p
    p="${p:-1}"
    case "$p" in 1) protocol_mode=dual; break;; 2) protocol_mode=vless; break;; 3) protocol_mode=hy2; break;; *) echo "请输入 1、2 或 3。";; esac
  done

  while true; do
    read -r -p "请输入代理监听端口 [默认 443]：" proxy_port
    proxy_port="${proxy_port:-443}"
    valid_port "$proxy_port" && break
    echo "端口必须是 1-65535 之间的数字。"
  done

  if [[ "$protocol_mode" != hy2 ]]; then
    while true; do
      read -r -p "请输入 VLESS + REALITY 伪装域名 [默认 www.softbank.jp]：" reality_sni
      reality_sni="${reality_sni:-www.softbank.jp}"
      valid_domain "$reality_sni" && break
      echo "域名格式不正确。"
    done
  else
    reality_sni=www.softbank.jp
  fi
fi

if [[ "$choice" == 1 || "$choice" == 5 ]]; then
  read -r -p "请输入订阅访问域名（直接回车使用 IP 模式）：" sub_domain
  sub_domain="${sub_domain,,}"
  sub_domain="${sub_domain%.}"
  if [[ -n "$sub_domain" ]] && ! valid_domain "$sub_domain"; then
    echo "错误：订阅域名格式不正确。" >&2
    exit 1
  fi
  while true; do
    read -r -p "请输入订阅服务端口 [默认 8443]：" sub_port
    sub_port="${sub_port:-8443}"
    valid_port "$sub_port" && [[ "$sub_port" != 443 ]] && break
    echo "订阅端口必须是 1-65535，且不能使用 443。"
  done
fi

if [[ "$choice" == 2 || "$choice" == 4 ]]; then
  read -r -p "请输入订阅中心接入码（可直接回车稍后配置）：" registration_code
fi

if [[ "$choice" == 3 ]]; then
  read -r -p "请输入完整 JPR3 对接密钥：" pairing_key
  [[ "$pairing_key" == JPR3.* ]] || { echo "错误：对接密钥必须以 JPR3. 开头。" >&2; exit 1; }
fi

echo
echo "========== 参数确认 =========="
echo "安装角色：$choice"
if [[ "$choice" != 3 ]]; then
  echo "代理协议：$protocol_mode"
  echo "代理端口：$proxy_port"
  [[ "$protocol_mode" == hy2 ]] || echo "REALITY 伪装域名：$reality_sni"
fi
if [[ "$choice" == 1 || "$choice" == 5 ]]; then
  echo "订阅访问：${sub_domain:-IP 模式}"
  echo "订阅端口：$sub_port"
fi
[[ "$choice" == 2 || "$choice" == 4 ]] && echo "订阅接入码：$([[ -n "$registration_code" ]] && echo 已填写 || echo 稍后配置)"
[[ "$choice" == 3 ]] && echo "副机对接密钥：已填写"
echo "参数设置完成，开始安装。"

case "$choice" in
  1)
    install_host
    write_roles true false false true center
    VVV_SUB_DOMAIN="$sub_domain" VVV_SUB_PORT="$sub_port" bash "$BASE_DIR/center_install.sh"
    registration_code="$(cat /etc/vvv-sub/registration.code)"
    bash "$BASE_DIR/register_sync.sh" center "$registration_code"
    ;;
  2)
    install_host
    enable_relay
    write_roles false true false true relay
    bash "$BASE_DIR/register_sync.sh" relay "$registration_code"
    ;;
  3)
    VVV_PAIRING_KEY="$pairing_key" sh "$BASE_DIR/landing.sh"
    [[ -x /usr/local/sbin/vps ]] && cp -f /usr/local/sbin/vps /usr/local/sbin/vvv-landing-original
    write_roles false false true false landing
    ;;
  4)
    install_host
    write_roles false false false true direct
    bash "$BASE_DIR/register_sync.sh" direct "$registration_code"
    ;;
  5)
    install_host
    enable_relay
    write_roles true true false true all
    VVV_SUB_DOMAIN="$sub_domain" VVV_SUB_PORT="$sub_port" bash "$BASE_DIR/center_install.sh"
    registration_code="$(cat /etc/vvv-sub/registration.code)"
    bash "$BASE_DIR/register_sync.sh" all "$registration_code"
    ;;
esac

install_unified_manager
printf '\nVVV 安装完成。以后统一输入：vps\n'
/usr/local/sbin/vps

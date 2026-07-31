#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ $(id -u) -eq 0 ]] || { echo "错误：请使用 root 用户运行。" >&2; exit 1; }
valid_port(){ [[ "${1:-}" =~ ^[0-9]+$ ]] && ((10#$1>=1 && 10#$1<=65535)); }
valid_domain(){ [[ "${1:-}" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ ]]; }
install_helpers(){
  install -d -m700 /usr/local/lib/vvv /etc/vvv
  for f in qr_helper.sh backup_manager.py rclone_manager.sh sync_agent.py register_sync.sh vvv_manager.sh; do
    [[ -f "$BASE_DIR/$f" ]] && install -m755 "$BASE_DIR/$f" "/usr/local/lib/vvv/$f"
  done
}
install_host(){ VVV_PROTOCOL_MODE="$protocol_mode" VVV_PROXY_PORT="$proxy_port" VVV_REALITY_SNI="$reality_sni" bash "$BASE_DIR/host.sh"; }
enable_relay(){
  local tmp; tmp="$(mktemp)"
  jq '.relay_manager_enabled=true|.updated_at=(now|todate)' /etc/jp-relay/state.json > "$tmp"
  install -m600 "$tmp" /etc/jp-relay/state.json; rm -f "$tmp"
}
write_roles(){
  local primary=$1 center=$2 relay=$3 landing=$4 proxy=$5
  jq -n --arg p "$primary" --argjson c "$center" --argjson r "$relay" --argjson l "$landing" --argjson x "$proxy" '{schema:2,primary_role:$p,roles:{center:$c,relay:$r,landing:$l,proxy:$x}}' > /etc/vvv/roles.json
  chmod 600 /etc/vvv/roles.json
}
install_manager(){
  cat > /usr/local/sbin/vps <<'SH'
#!/usr/bin/env bash
exec /usr/local/lib/vvv/vvv_manager.sh "$@"
SH
  chmod 700 /usr/local/sbin/vps
}
extract_landing_registration(){
  python3 - "$pairing_key" <<'PY'
import base64,json,sys
p=sys.argv[1].split('.')
if len(p)!=3: raise SystemExit
raw=p[1]+'='*((4-len(p[1])%4)%4)
obj=json.loads(base64.urlsafe_b64decode(raw).decode())
print(obj.get('subscription_registration_code') or '')
PY
}
cat <<'MENU'
========== VVV 一体化安装管理 ==========

1. 安装订阅中心+中转主机（含自身代理）
2. 安装订阅中心（含自身代理）
3. 安装中转主机（含自身代理）
4. 安装中转副机（通过主机代理）
5. 仅安装直连代理
0. 退出
MENU
while true; do read -r -p "请输入编号：" choice; case "$choice" in 0|1|2|3|4|5) break;; *) echo "请输入 0-5。";; esac; done
[[ $choice == 0 ]] && exit 0
protocol_mode= proxy_port= reality_sni= sub_domain= sub_port= registration_code= pairing_key=
if [[ $choice != 4 ]]; then
  echo; echo "========== 安装参数（全部前置设置） =========="
  echo "1. 双协议【默认】"; echo "2. 仅 VLESS + REALITY"; echo "3. 仅 Hysteria 2"
  read -r -p "请选择代理协议 [默认 1]：" p; p=${p:-1}; case "$p" in 1) protocol_mode=dual;; 2) protocol_mode=vless;; 3) protocol_mode=hy2;; *) echo "错误：协议编号无效。"; exit 1;; esac
  while true; do read -r -p "请输入代理监听端口 [默认 443]：" proxy_port; proxy_port=${proxy_port:-443}; valid_port "$proxy_port" && break; echo "端口无效。"; done
  if [[ $protocol_mode != hy2 ]]; then
    while true; do read -r -p "请输入 VLESS + REALITY 伪装域名 [默认 www.softbank.jp]：" reality_sni; reality_sni=${reality_sni:-www.softbank.jp}; valid_domain "$reality_sni" && break; echo "域名格式不正确。"; done
  else reality_sni=www.softbank.jp; fi
fi
if [[ $choice == 1 || $choice == 2 ]]; then
  read -r -p "请输入订阅访问域名（直接回车使用 IP 模式）：" sub_domain; sub_domain=${sub_domain,,}; sub_domain=${sub_domain%.}
  [[ -z $sub_domain ]] || valid_domain "$sub_domain" || { echo "订阅域名格式不正确。"; exit 1; }
  while true; do read -r -p "请输入订阅服务端口 [默认 8443]：" sub_port; sub_port=${sub_port:-8443}; valid_port "$sub_port" && [[ $sub_port != 443 ]] && break; echo "订阅端口无效或占用443。"; done
fi
if [[ $choice == 3 || $choice == 5 ]]; then read -r -p "请输入订阅中心接入码（直接回车表示独立使用）：" registration_code; fi
if [[ $choice == 4 ]]; then read -r -p "请输入主机生成的完整 JPR3 对接密钥：" pairing_key; [[ $pairing_key == JPR3.* ]] || { echo "对接密钥格式错误。"; exit 1; }; fi
echo; echo "========== 参数确认 =========="; echo "安装角色：$choice"
[[ $choice == 4 ]] || { echo "代理协议：$protocol_mode"; echo "代理端口：$proxy_port"; [[ $protocol_mode == hy2 ]] || echo "REALITY 伪装域名：$reality_sni"; }
[[ $choice == 1 || $choice == 2 ]] && { echo "订阅访问：${sub_domain:-IP 模式}"; echo "订阅端口：$sub_port"; }
[[ $choice == 4 ]] && echo "副机对接密钥：已填写"
echo "参数设置完成，开始安装。"
install_helpers
case "$choice" in
  1)
    install_host; enable_relay; write_roles center-relay true true false true
    VVV_SUB_DOMAIN="$sub_domain" VVV_SUB_PORT="$sub_port" bash "$BASE_DIR/center_install.sh"
    registration_code="$(cat /etc/vvv-sub/registration.code)"; bash "$BASE_DIR/register_sync.sh" center-relay "$registration_code"
    ;;
  2)
    install_host; write_roles center true false false true
    VVV_SUB_DOMAIN="$sub_domain" VVV_SUB_PORT="$sub_port" bash "$BASE_DIR/center_install.sh"
    registration_code="$(cat /etc/vvv-sub/registration.code)"; bash "$BASE_DIR/register_sync.sh" center "$registration_code"
    ;;
  3)
    install_host; enable_relay; write_roles relay false true false true
    bash "$BASE_DIR/register_sync.sh" relay "$registration_code"
    ;;
  4)
    VVV_PAIRING_KEY="$pairing_key" sh "$BASE_DIR/landing.sh"
    write_roles landing false false true false
    registration_code="$(extract_landing_registration || true)"
    bash "$BASE_DIR/register_sync.sh" landing "$registration_code"
    ;;
  5)
    install_host; write_roles direct false false false true
    bash "$BASE_DIR/register_sync.sh" direct "$registration_code"
    ;;
esac
install_manager
echo; echo "VVV 安装完成。以后统一输入：vps"
/usr/local/sbin/vps

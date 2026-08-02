#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROLE_FILE=/etc/vvv/roles.json
MAIN_STATE=/etc/jp-relay/state.json
LANDING_STATE=/etc/jp-relay/landing-state.json
CENTER_CFG=/etc/vvv-sub/config.json

[[ "$(id -u)" -eq 0 ]] || { echo "错误：请使用 root 用户运行。" >&2; exit 1; }
[[ -r /etc/os-release ]] || { echo "错误：无法读取 /etc/os-release。" >&2; exit 1; }
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == debian && "${VERSION_ID:-}" == 13 ]] || { echo "错误：VVV 仅支持 Debian 13。当前系统：${PRETTY_NAME:-未知}" >&2; exit 1; }
command -v systemctl >/dev/null 2>&1 || { echo "错误：Debian 13 缺少 systemd。" >&2; exit 1; }

valid_port(){ [[ "${1:-}" =~ ^[0-9]+$ ]] && ((10#$1>=1 && 10#$1<=65535)); }
valid_domain(){ [[ "${1:-}" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]]; }
port_in_use(){ ss -H -lnt "sport = :$1" 2>/dev/null | grep -q .; }
fail(){ echo "错误：$*" >&2; exit 1; }

json_value() {
  local path="$1" expression="$2" default="${3:-}"
  python3 - "$path" "$expression" "$default" <<'PY_JSON_VALUE'
import json,sys
from pathlib import Path
path,expr,default=sys.argv[1:]
try:
    value=json.loads(Path(path).read_text(encoding='utf-8'))
    for key in expr.split('.'):
        if key:
            value=value[key]
    if isinstance(value,bool):
        print('true' if value else 'false')
    elif value is None:
        print(default)
    else:
        print(value)
except Exception:
    print(default)
PY_JSON_VALUE
}

main_state_valid() {
  [[ -s "$MAIN_STATE" ]] || return 1
  python3 - "$MAIN_STATE" <<'PY_MAIN_VALID'
import json,sys
from pathlib import Path
try:
    s=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    assert s.get('schema') in (3,4)
    assert s.get('role') == 'japan-hub'
    assert s.get('protocol_mode') in ('dual','vless','hy2')
    assert isinstance(s.get('listen_port'), int)
except Exception:
    raise SystemExit(1)
PY_MAIN_VALID
}

landing_state_valid() {
  [[ -s "$LANDING_STATE" ]] || return 1
  python3 - "$LANDING_STATE" <<'PY_LANDING_VALID'
import json,sys
from pathlib import Path
try:
    s=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    assert s.get('role') == 'landing'
    assert s.get('protocol_mode') in ('dual','vless','hy2')
except Exception:
    raise SystemExit(1)
PY_LANDING_VALID
}

migrate_center_config_if_needed() {
  [[ -s "$CENTER_CFG" ]] || return 0
  local schema suffix backup_name
  schema="$(json_value "$CENTER_CFG" schema 0)"
  [[ "$schema" == 2 || "$schema" == 3 ]] || return 0
  if [[ ! -x /usr/local/sbin/vvv-center || ! -x /usr/local/lib/vvv/sub_center.py ||
        ! -f /etc/systemd/system/vvv-sub.service || ! -f /etc/systemd/system/caddy.service ||
        ! -s /etc/caddy/Caddyfile ]]; then
    echo "检测到旧版订阅中心残留不完整，暂不迁移；选择带订阅中心的角色后将先备份并按中断恢复流程处理。"
    return 0
  fi
  suffix="$(json_value "$CENTER_CFG" subscription_suffix "")"
  if [[ "$schema" == 2 || ! "$suffix" =~ ^[A-Za-z0-9]{6,32}$ ]]; then
    suffix="$(python3 - <<'PY_SUFFIX'
import secrets,string
alphabet=string.ascii_letters+string.digits
print(''.join(secrets.choice(alphabet) for _ in range(8)))
PY_SUFFIX
)"
  fi
  backup_name="/etc/vvv-sub/config.schema${schema}-backup.json"
  cp -a "$CENTER_CFG" "$backup_name"
  python3 - "$CENTER_CFG" "$suffix" <<'PY_MIGRATE_CENTER'
import json,os,sys,tempfile
path,suffix=sys.argv[1:]
with open(path,encoding='utf-8') as f: obj=json.load(f)
old_schema=int(obj.get('schema') or 0)
base=str(obj.get('base_url','')).rstrip('/')
mode=obj.get('address_mode') or obj.get('mode') or ('domain' if obj.get('domain') else 'ip')
if old_schema==2:
    obj['transport_mode']='direct-https'
obj['schema']=4
obj['address_mode']=mode if mode in ('domain','ip') else ('domain' if obj.get('domain') else 'ip')
obj['subscription_suffix']=suffix
obj['subscription_url']=base+'/'+suffix
obj['listen_host']='0.0.0.0'
obj['listen_port']=18081
obj['api_base_url']=f"http://{obj.get('public_ip','')}:18081"
for key in ('mode','subscription_token','https_pinned','https_upgraded_at'):
    obj.pop(key,None)
fd,tmp=tempfile.mkstemp(prefix='.config-migrate.',dir=os.path.dirname(path))
with os.fdopen(fd,'w',encoding='utf-8') as f:
    json.dump(obj,f,ensure_ascii=False,indent=2); f.write('\n')
os.chmod(tmp,0o600); os.replace(tmp,path)
PY_MIGRATE_CENTER
  touch /etc/vvv-sub/.schema4-migrated
  echo "检测到 schema ${schema} 订阅中心，已原地升级到 schema 4；注册主机、节点、备份、证书和传输方式均保留。"
}

center_config_valid() {
  [[ -s "$CENTER_CFG" ]] || return 1
  python3 - "$CENTER_CFG" <<'PY_CENTER_VALID'
import json,re,sys
from pathlib import Path
try:
    s=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    assert s.get('schema') == 4
    assert s.get('address_mode') in ('domain','ip')
    assert s.get('transport_mode') in ('direct-http','direct-https','tunnel')
    suffix=str(s.get('subscription_suffix',''))
    assert re.fullmatch(r'[A-Za-z0-9]{6,32}',suffix)
    base=str(s.get('base_url',''))
    if s['transport_mode']=='direct-http': assert base.startswith('http://')
    else: assert base.startswith('https://')
    assert s.get('subscription_url') == base.rstrip('/') + '/' + suffix
    assert int(s.get('public_port',0)) > 0
    assert s.get('master_token') and s.get('recovery_password')
    assert str(s.get('api_base_url','')).startswith('http://')
except Exception:
    raise SystemExit(1)
PY_CENTER_VALID
}

center_complete() {
  center_config_valid &&
  [[ -s /etc/vvv-sub/registration.code ]] &&
  [[ -x /usr/local/sbin/vvv-center ]] &&
  [[ -x /usr/local/lib/vvv/sub_center.py ]] &&
  [[ -f /etc/systemd/system/vvv-sub.service ]] &&
  [[ -f /etc/systemd/system/caddy.service ]] &&
  [[ -s /etc/caddy/Caddyfile ]]
}

center_partial() {
  [[ -e /etc/vvv-sub || -e /var/lib/vvv-sub || -e /usr/local/sbin/vvv-center ||
     -e /etc/systemd/system/vvv-sub.service || -e /etc/systemd/system/caddy.service ||
     -e /etc/systemd/system/vvv-cloudflared.service || -e /etc/caddy/Caddyfile ]]
}

relay_enabled() {
  main_state_valid || return 1
  [[ "$(json_value "$MAIN_STATE" relay_manager_enabled false)" == true ]]
}

detect_installed_modules() {
  INST_PROXY=false
  INST_CENTER=false
  INST_RELAY=false
  INST_LANDING=false
  main_state_valid && INST_PROXY=true
  center_complete && INST_CENTER=true
  relay_enabled && INST_RELAY=true
  landing_state_valid && INST_LANDING=true
  # “未安装某模块”是正常检测结果，不能让 set -e 静默退出安装器。
  return 0
}

print_mark() {
  local installed="$1" label="$2"
  [[ "$installed" == true ]] && echo "✓ $label" || echo "✗ $label"
}

show_install_menu() {
  detect_installed_modules
  echo
  echo "========== VVV 一体化安装管理 =========="
  echo "当前检测到的模块："
  print_mark "$INST_PROXY" "本机直连代理"
  print_mark "$INST_CENTER" "订阅中心"
  print_mark "$INST_RELAY" "中转管理"
  print_mark "$INST_LANDING" "中转副机"
  echo
  echo "1. 安装订阅中心 + 中转主机 + 自身代理"
  echo "2. 安装订阅中心 + 自身代理"
  echo "3. 安装中转主机 + 自身代理"
  echo "4. 安装中转副机"
  echo "5. 安装直连代理"
  echo "6. 从云备份恢复"
  echo "0. 退出"
}

load_existing_proxy_parameters() {
  VVV_PROTOCOL_MODE="$(json_value "$MAIN_STATE" protocol_mode dual)"
  VVV_PROXY_PORT="$(json_value "$MAIN_STATE" listen_port 443)"
  VVV_REALITY_SNI="$(json_value "$MAIN_STATE" sni www.softbank.jp)"
  VVV_HY2_LIMIT_MBPS="$(json_value "$MAIN_STATE" hy2_limit_mbps 50)"
  export VVV_PROTOCOL_MODE VVV_PROXY_PORT VVV_REALITY_SNI VVV_HY2_LIMIT_MBPS
  REUSE_PROXY=1
}

load_existing_center_parameters() {
  VVV_SUB_DOMAIN="$(json_value "$CENTER_CFG" domain "")"
  VVV_SUB_PORT="$(json_value "$CENTER_CFG" public_port 8443)"
  VVV_SUB_TRANSPORT="$(json_value "$CENTER_CFG" transport_mode direct-https)"
  VVV_SUB_SUFFIX="$(json_value "$CENTER_CFG" subscription_suffix "")"
  VVV_CF_TUNNEL_TOKEN=""
  export VVV_SUB_DOMAIN VVV_SUB_PORT VVV_SUB_TRANSPORT VVV_SUB_SUFFIX VVV_CF_TUNNEL_TOKEN
  REUSE_CENTER=1
}

backup_and_reset_partial_center() {
  center_partial || return 0
  center_complete && return 0
  local backup_dir
  backup_dir="/root/VVV-中断订阅中心备份-$(date +%Y%m%d-%H%M%S)"
  echo
  echo "检测到上次中断留下的不完整订阅中心，正在备份残留并重新准备安装。"
  install -d -m700 "$backup_dir"
  [[ ! -e /etc/vvv-sub ]] || cp -a /etc/vvv-sub "$backup_dir/" 2>/dev/null || true
  [[ ! -e /var/lib/vvv-sub ]] || cp -a /var/lib/vvv-sub "$backup_dir/" 2>/dev/null || true
  systemctl disable --now vvv-ip-cert-renew.timer vvv-cloudflared.service vvv-sync.timer vvv-sync.path >/dev/null 2>&1 || true
  systemctl stop vvv-sub.service caddy.service vvv-cloudflared.service >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/vvv-sub.service \
        /etc/systemd/system/vvv-ip-cert-renew.service \
        /etc/systemd/system/vvv-ip-cert-renew.timer \
        /etc/systemd/system/caddy.service \
        /etc/systemd/system/vvv-cloudflared.service \
        /usr/local/sbin/vvv-center \
        /usr/local/lib/vvv/deploy-ip-cert.sh \
        /usr/local/lib/vvv/run-cloudflared.sh
  rm -rf /etc/vvv-sub /var/lib/vvv-sub /etc/caddy/.vvv-ip-final-active /etc/caddy/Caddyfile /etc/caddy/certs
  systemctl daemon-reload
  echo "不完整订阅中心已清理；残留备份：$backup_dir"
}

validate_vvc1(){
  python3 "$BASE_DIR/sync_agent.py" validate-code "$1" >/dev/null 2>&1
}

ask_optional_vvc1(){
  local __var="$1" value
  while true; do
    read -r -p "请输入订阅中心对接码（按回车跳过）：" value
    value="${value//[[:space:]]/}"
    if [[ -z "$value" ]]; then printf -v "$__var" '%s' ''; return; fi
    if [[ "$value" == JPR3.* ]]; then echo "对接码错误：这是中转副机安装密钥，不能用于注册订阅中心。"; continue; fi
    if validate_vvc1 "$value"; then printf -v "$__var" '%s' "$value"; return; fi
    echo "对接码错误：必须输入完整有效的 VVC1 订阅中心对接码，或直接回车跳过。"
  done
}

ask_required_jpr3(){
  while true; do
    read -r -p "请输入完整 JPR3 对接密钥（中转模式必填）：" key
    key="${key//[[:space:]]/}"
    if [[ -z "$key" ]]; then echo "中转模式必须输入 JPR3 对接密钥，不能跳过。"; continue; fi
    if [[ "$key" == VVC1.* ]]; then echo "对接码错误：这是订阅中心对接码，不能用于安装中转副机。"; continue; fi
    if ((${#key} >= 4095)); then echo "对接密钥已达到终端单行输入上限，内容很可能被截断。"; continue; fi
    if [[ ! "$key" =~ ^JPR3\.[A-Za-z0-9_-]+\.[0-9a-f]{20}$ ]]; then
      echo "对接密钥格式错误或复制不完整，必须是完整的 JPR3.数据.校验值。"; continue
    fi
    break
  done
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
  VVV_HY2_LIMIT_MBPS=50
  if [[ "$VVV_PROTOCOL_MODE" != vless ]]; then
    while true; do
      read -r -p "请输入 Hysteria 2 每连接服务器强制限速 [默认 50M]：" input
      input="${input//[[:space:]]/}"; [[ -n "$input" ]] || input=50
      input="${input%[Mm]}"
      if [[ "$input" =~ ^[0-9]+$ ]] && ((10#$input>=30 && 10#$input<=100)); then VVV_HY2_LIMIT_MBPS="$((10#$input))"; break; fi
      echo "限速只允许 30-100 的整数，可写 50、50M 或 50m。"
    done
  fi
  export VVV_PROTOCOL_MODE VVV_PROXY_PORT VVV_REALITY_SNI VVV_HY2_LIMIT_MBPS
}

random_subscription_suffix() {
  python3 - <<'PY_RANDOM_SUFFIX'
import secrets,string
alphabet=string.ascii_letters+string.digits
print(''.join(secrets.choice(alphabet) for _ in range(8)))
PY_RANDOM_SUFFIX
}

ask_center_parameters(){
  local input choice
  echo
  while true; do
    read -r -p "请输入订阅域名（直接回车使用本机公网 IP）：" input
    input="${input,,}"; input="${input%.}"
    if [[ -z "$input" ]]; then VVV_SUB_DOMAIN=""; break; fi
    if valid_domain "$input"; then VVV_SUB_DOMAIN="$input"; break; fi
    echo "域名格式不正确，请重新输入；也可以直接回车使用本机公网 IP。"
  done
  echo
  echo "请选择订阅传输方式："
  echo "1. 直接 HTTPS【默认】"
  echo "   域名由 Caddy 自动申请公共证书；IP 由 Certbot 申请 Let's Encrypt IP 证书。"
  echo "2. 直接 HTTP"
  echo "   不申请证书，仅限临时调试；请勿长期使用。"
  echo "3. 固定 HTTPS 域名（Cloudflare Tunnel）"
  echo "   公共地址使用标准 443，VPS 只运行本地 HTTP；需提前创建 Tunnel 公共主机名。"
  while true; do
    read -r -p "请输入编号 [默认 1]：" choice
    [[ -n "$choice" ]] || choice=1
    case "$choice" in
      1) VVV_SUB_TRANSPORT=direct-https; break;;
      2) VVV_SUB_TRANSPORT=direct-http; break;;
      3) VVV_SUB_TRANSPORT=tunnel; break;;
      *) echo "请输入 1、2 或 3。";;
    esac
  done
  if [[ "$VVV_SUB_TRANSPORT" == tunnel ]]; then
    while [[ -z "$VVV_SUB_DOMAIN" ]]; do
      read -r -p "Cloudflare Tunnel 模式必须输入订阅域名：" input
      input="${input,,}"; input="${input%.}"
      valid_domain "$input" && VVV_SUB_DOMAIN="$input" || echo "域名格式不正确。"
    done
    VVV_SUB_PORT=8443
    while true; do
      read -r -p "请输入 Cloudflare Tunnel Token：" VVV_CF_TUNNEL_TOKEN
      VVV_CF_TUNNEL_TOKEN="${VVV_CF_TUNNEL_TOKEN//[[:space:]]/}"
      [[ -n "$VVV_CF_TUNNEL_TOKEN" ]] && break
      echo "Tunnel Token 不能为空。"
    done
  else
    VVV_CF_TUNNEL_TOKEN=""
    while true; do
      read -r -p "请输入订阅服务端口 [默认 8443]：" input
      input="${input//[[:space:]]/}"; [[ -n "$input" ]] || input=8443
      if ! valid_port "$input"; then echo "端口必须是 1-65535 之间的数字。"; continue; fi
      input="$((10#$input))"
      if [[ "$input" == 443 ]]; then echo "订阅服务端口不能使用代理端口 443。"; continue; fi
      if [[ "$input" == "${VVV_PROXY_PORT:-}" ]]; then echo "订阅服务端口不能与代理端口相同。"; continue; fi
      if port_in_use "$input"; then echo "TCP 端口 ${input} 已被占用，请输入其他端口。"; continue; fi
      VVV_SUB_PORT="$input"; break
    done
  fi
  while true; do
    read -r -p "请输入订阅地址后缀（手动 6-32 位大小写字母或数字；直接回车随机生成 8 位）：" input
    input="${input//[[:space:]]/}"
    [[ -n "$input" ]] || input="$(random_subscription_suffix)"
    if [[ "$input" =~ ^[A-Za-z0-9]{6,32}$ ]]; then
      case "${input,,}" in health|api|admin|debug) echo "该后缀属于系统保留词，请重新输入。"; continue;; esac
      VVV_SUB_SUFFIX="$input"; break
    fi
    echo "订阅后缀只能包含大小写字母和数字，手动输入长度必须为 6-32 位。"
  done
  export VVV_SUB_DOMAIN VVV_SUB_PORT VVV_SUB_TRANSPORT VVV_SUB_SUFFIX VVV_CF_TUNNEL_TOKEN
}

jpr_registration_code(){
  local value="$1"
  python3 - "$value" <<'PY_JPR_REGISTRATION_CODE'
import base64
import json
import sys
import zlib

parts = ''.join(sys.argv[1].split()).split('.')
if len(parts) != 3 or parts[0] != 'JPR3':
    raise SystemExit(1)
try:
    transferred = base64.urlsafe_b64decode(parts[1] + '=' * ((4 - len(parts[1]) % 4) % 4))
    raw = transferred if transferred.startswith(b'{') else zlib.decompress(transferred)
    value = json.loads(raw.decode('utf-8')).get('subscription_registration_code') or ''
except Exception:
    raise SystemExit(1)
print(value)
PY_JPR_REGISTRATION_CODE
}

host_ready() {
  main_state_valid || return 1
  [[ -x /usr/local/sbin/jp-relay-manager && -x /usr/local/sbin/jp-show-nodes ]] || return 1
  local mode
  mode="$(json_value "$MAIN_STATE" protocol_mode "")"
  case "$mode" in
    dual) [[ -x /usr/local/bin/xray && -x /usr/local/bin/sing-box ]] ;;
    vless) [[ -x /usr/local/bin/xray ]] ;;
    hy2) [[ -x /usr/local/bin/sing-box ]] ;;
    *) return 1 ;;
  esac
}

ensure_host_runtime() {
  local mode
  mode="$(json_value "$MAIN_STATE" protocol_mode "")"
  case "$mode" in
    dual|vless)
      systemctl is-active --quiet xray || timeout 75 systemctl restart xray
      systemctl is-active --quiet xray || return 1
      ;;
  esac
  case "$mode" in
    dual|hy2)
      systemctl is-active --quiet sing-box || timeout 75 systemctl restart sing-box
      systemctl is-active --quiet sing-box || return 1
      ;;
  esac
}

ensure_host(){
  if host_ready && ensure_host_runtime; then
    VVV_REFRESH_MANAGER_ONLY=1 bash "$BASE_DIR/host.sh"
    echo "本机代理已完整安装，已刷新中转管理程序并复用现有协议、端口和永久凭证。"
    return 0
  fi
  [[ ! -e "$MAIN_STATE" ]] || echo "检测到上次中断或不完整的本机代理，正在从现有状态继续修复。"
  bash "$BASE_DIR/host.sh"
  host_ready || fail "本机代理安装后完整性检查失败。"
}

enable_relay(){
  main_state_valid || return 1
  local tmp
  tmp="$(mktemp)"
  jq '.relay_manager_enabled=true | .updated_at=(now|todate)' "$MAIN_STATE" > "$tmp"
  install -m600 "$tmp" "$MAIN_STATE"
  rm -f "$tmp"
}

refresh_center_runtime_code() {
  local changed=0 file target mode
  install -d -m700 /usr/local/lib/vvv
  for file in sub_center.py backup_manager.py rclone_manager.sh client_adapters.py adapter_manager.py center_transport.sh restore_manager.py diagnostic_report.py node_probe.py; do
    target="/usr/local/lib/vvv/$file"
    if [[ ! -f "$target" ]] || ! cmp -s "$BASE_DIR/$file" "$target"; then
      install -m755 "$BASE_DIR/$file" "$target"
      changed=1
    fi
  done
  if [[ ! -f /usr/local/sbin/vvv-center ]] || ! cmp -s "$BASE_DIR/center_manager.sh" /usr/local/sbin/vvv-center; then
    install -m700 "$BASE_DIR/center_manager.sh" /usr/local/sbin/vvv-center
    changed=1
  fi
  if (( changed == 1 )); then
    echo "检测到订阅中心程序更新，保留全部数据并重新启动内部服务。"
    python3 /usr/local/lib/vvv/client_adapters.py >/dev/null
    timeout 75 systemctl restart vvv-sub.service
  fi
  if [[ -f /etc/vvv-sub/.schema4-migrated ]]; then
    echo "正在将旧四路径入口无损切换为新的统一订阅地址。"
    bash /usr/local/lib/vvv/center_transport.sh reapply || fail "旧订阅中心配置已迁移，但统一入口切换失败；原数据和schema2备份均已保留。"
    rm -f /etc/vvv-sub/.schema4-migrated
  fi
}

ensure_center_runtime() {
  systemctl daemon-reload
  systemctl enable vvv-sub.service caddy.service >/dev/null 2>&1 || true
  systemctl is-active --quiet vvv-sub.service || timeout 75 systemctl restart vvv-sub.service
  systemctl is-active --quiet caddy.service || timeout 75 systemctl restart caddy.service
  if [[ "$(json_value "$CENTER_CFG" transport_mode "")" == tunnel ]]; then
    systemctl enable vvv-cloudflared.service >/dev/null 2>&1 || true
    systemctl is-active --quiet vvv-cloudflared.service || timeout 75 systemctl restart vvv-cloudflared.service
    systemctl is-active --quiet vvv-cloudflared.service || return 1
  fi
  systemctl is-active --quiet vvv-sub.service && systemctl is-active --quiet caddy.service
}

ensure_center(){
  if center_complete; then
    echo "订阅中心已完整安装，保留现有订阅密钥、已注册主机和备份数据。"
    refresh_center_runtime_code
    ensure_center_runtime || fail "现有订阅中心文件完整，但服务无法启动；为保护数据，脚本没有自动删除它。"
    return 0
  fi
  backup_and_reset_partial_center
  echo
  echo "========== 继续安装订阅中心 =========="
  echo "不会重启整台 VPS；只会按需启动或重启 Caddy、订阅中心等服务，当前 SSH 不受影响。"
  bash "$BASE_DIR/center_install.sh"
  center_complete || fail "订阅中心安装后完整性检查失败。"
}

install_landing(){
  local key="$1" tmp
  tmp="$(mktemp /tmp/vvv-landing.XXXXXX.sh)"
  awk -v key="$key" 'BEGIN{done=0} !done && /^PAIRING_KEY=/ {print "PAIRING_KEY=\047" key "\047"; done=1; next} {print}' "$BASE_DIR/landing.sh" > "$tmp"
  chmod 700 "$tmp"
  local landing_rc
  if sh "$tmp"; then
    landing_rc=0
  else
    landing_rc=$?
  fi
  rm -f "$tmp"
  if (( landing_rc != 0 )); then
    fail "中转副机安装程序失败（退出码 ${landing_rc}）；已停止后续步骤，请以上方首次失败信息为准。"
  fi
  [[ -x /usr/local/sbin/landing-vps ]] || fail "中转副机安装程序返回成功，但管理命令不存在。"
  cat > /usr/local/sbin/vvv-landing-original <<'EOF_LANDING_ORIGINAL'
#!/usr/bin/env bash
exec /usr/local/sbin/landing-vps "$@"
EOF_LANDING_ORIGINAL
  chmod 700 /usr/local/sbin/vvv-landing-original
}

rebuild_roles_from_system(){
  detect_installed_modules
  local primary
  if [[ "$INST_LANDING" == true ]]; then
    primary=landing
  elif [[ "$INST_CENTER" == true && "$INST_RELAY" == true ]]; then
    primary=center-relay
  elif [[ "$INST_CENTER" == true ]]; then
    primary=center
  elif [[ "$INST_RELAY" == true ]]; then
    primary=relay
  elif [[ "$INST_PROXY" == true ]]; then
    primary=direct
  else
    fail "没有检测到可写入的已安装模块。"
  fi
  install -d -m700 /etc/vvv
  python3 - "$ROLE_FILE" "$primary" "$INST_CENTER" "$INST_RELAY" "$INST_LANDING" "$INST_PROXY" <<'PY_ROLES'
import json,os,sys,tempfile
path,primary,center,relay,landing,proxy=sys.argv[1:]
obj={
    'schema':1,
    'primary_role':primary,
    'roles':{
        'center':center=='true',
        'relay':relay=='true',
        'landing':landing=='true',
        'proxy':proxy=='true',
    },
}
os.makedirs(os.path.dirname(path),exist_ok=True)
fd,tmp=tempfile.mkstemp(prefix='.roles.',dir=os.path.dirname(path))
with os.fdopen(fd,'w',encoding='utf-8') as f:
    json.dump(obj,f,ensure_ascii=False,indent=2)
    f.write('\n')
os.chmod(tmp,0o600)
os.replace(tmp,path)
PY_ROLES
}

primary_role(){
  json_value "$ROLE_FILE" primary_role direct
}

install_unified_manager(){
  install -d -m700 /etc/vvv /usr/local/lib/vvv
  install -m755 "$BASE_DIR/vvv_manager.sh" /usr/local/lib/vvv/vvv_manager.sh
  install -m755 "$BASE_DIR/register_sync.sh" /usr/local/lib/vvv/register_sync.sh
  install -m755 "$BASE_DIR/sync_agent.py" /usr/local/lib/vvv/sync_agent.py
  install -m755 "$BASE_DIR/restore_manager.py" /usr/local/lib/vvv/restore_manager.py
  install -m755 "$BASE_DIR/diagnostic_report.py" /usr/local/lib/vvv/diagnostic_report.py
  install -m755 "$BASE_DIR/node_probe.py" /usr/local/lib/vvv/node_probe.py
  cat > /usr/local/sbin/vps <<'EOF_VPS'
#!/usr/bin/env bash
exec /usr/local/lib/vvv/vvv_manager.sh "$@"
EOF_VPS
  chmod 700 /usr/local/sbin/vps
}

register_current_main_role(){
  local supplied_code role code
  supplied_code="${1:-}"
  role="$(primary_role)"
  code="$supplied_code"
  if center_complete; then
    code="$(cat /etc/vvv-sub/registration.code)"
  fi
  bash "$BASE_DIR/register_sync.sh" "$role" "$code"
  if [[ -z "$code" && -f /etc/vvv/client.json ]]; then
    local old_role
    old_role="$(json_value /etc/vvv/client.json role "")"
    if [[ "$old_role" != "$role" ]]; then
      echo "提示：本机角色已变为 ${role}，但没有订阅中心接入码，远端登记仍是 ${old_role:-未知}。"
      echo "稍后可在 vps 菜单中选择“注册或更换订阅中心”更新。"
    fi
  fi
}

show_parameter_summary(){
  local role_name protocol_name endpoint scheme transport_label
  case "$choice" in
    1) role_name="安装订阅中心 + 中转主机 + 自身代理";;
    2) role_name="安装订阅中心 + 自身代理";;
    3) role_name="安装中转主机 + 自身代理";;
    4) role_name="安装中转副机";;
    5) role_name="安装直连代理";;
    6) role_name="从云备份恢复";;
  esac
  echo
  echo "========== 安装参数总览 =========="
  echo "安装角色：$role_name"
  if [[ "$choice" == 6 ]]; then
    echo "云盘目录：vvv/（重新授权后选择恢复日期）"
  elif [[ "$choice" == 4 ]]; then
    echo "JPR3 密钥：已填写（${#key} 个字符）"
  else
    case "$VVV_PROTOCOL_MODE" in dual) protocol_name="VLESS + Hysteria 2";; vless) protocol_name="仅 VLESS";; hy2) protocol_name="仅 Hysteria 2";; esac
    echo "代理协议：$protocol_name$([[ "$REUSE_PROXY" == 1 ]] && echo '（复用现有）')"
    echo "代理端口：$VVV_PROXY_PORT"
    [[ "$VVV_PROTOCOL_MODE" == hy2 ]] || echo "REALITY 伪装域名：$VVV_REALITY_SNI"
    [[ "$VVV_PROTOCOL_MODE" == vless ]] || echo "Hysteria 2 每连接服务器强制限速：${VVV_HY2_LIMIT_MBPS}M"
    if [[ "$choice" == 1 || "$choice" == 2 ]]; then
      case "$VVV_SUB_TRANSPORT" in
        direct-http) transport_label="直接 HTTP"; scheme=http;;
        direct-https) transport_label="直接 HTTPS"; scheme=https;;
        tunnel) transport_label="固定 HTTPS 域名（Cloudflare Tunnel）"; scheme=https;;
      esac
      if [[ "$VVV_SUB_TRANSPORT" == tunnel ]]; then
        endpoint="https://${VVV_SUB_DOMAIN}/${VVV_SUB_SUFFIX}"
      elif [[ -n "$VVV_SUB_DOMAIN" ]]; then
        endpoint="${scheme}://${VVV_SUB_DOMAIN}:${VVV_SUB_PORT}/${VVV_SUB_SUFFIX}"
      else
        endpoint="${scheme}://本机公网IP:${VVV_SUB_PORT}/${VVV_SUB_SUFFIX}"
      fi
      echo "订阅传输：${transport_label}$([[ "$REUSE_CENTER" == 1 ]] && echo '（复用现有）')"
      echo "统一订阅地址：${endpoint}"
      echo "订阅后缀：${VVV_SUB_SUFFIX}"
    elif [[ "$choice" == 3 ]]; then
      [[ -n "$code" ]] && echo "订阅中心 VVC1：已填写或将使用本机订阅中心" || echo "订阅中心 VVC1：未填写（独立使用）"
    elif [[ "$choice" == 5 ]]; then
      [[ -n "$code" ]] && echo "订阅中心 VVC1：已填写" || echo "订阅中心 VVC1：未填写（本次暂不注册）"
    fi
  fi
  echo "=================================="
  echo "参数已收集完毕，直接开始全自动安装。"
}

REUSE_PROXY=0
REUSE_CENTER=0
code=""
key=""
VVV_CF_TUNNEL_TOKEN=""

migrate_center_config_if_needed
if [[ -f /etc/vvv-sub/.schema4-migrated ]]; then
  refresh_center_runtime_code
  ensure_center_runtime || fail "旧订阅中心配置已迁移，但新统一入口服务无法启动；原数据和 schema2 备份均已保留。"
fi
show_install_menu
while true; do
  read -r -p "请输入编号：" choice
  case "$choice" in 0|1|2|3|4|5|6) break ;; *) echo "请输入 0-6。" ;; esac
done
[[ "$choice" == 0 ]] && exit 0

# 菜单永远先显示；选择后再判断该角色能否与当前机器共存。
case "$choice" in
  1|2|3|5)
    landing_state_valid && fail "当前 VPS 已安装为中转副机，不能再叠加本机代理、订阅中心或中转主机。菜单仍可重新进入。"
    if main_state_valid; then
      load_existing_proxy_parameters
      echo "检测到现有本机代理，本次将复用协议、端口和永久凭证。"
    else
      ask_proxy_parameters
    fi
    ;;
  4)
    if main_state_valid || center_partial; then
      fail "中转副机不能与本机代理、订阅中心或中转主机安装在同一台 VPS。菜单仍可重新进入。"
    fi
    ;;
  6)
    if main_state_valid || center_complete || landing_state_valid; then
      fail "检测到当前 VPS 已有完整 VVV 模块。为避免误覆盖，云恢复只允许在干净系统或不完整残留环境执行。"
    fi
    ;;
esac

case "$choice" in
  1|2)
    if center_complete; then
      load_existing_center_parameters
      echo "检测到现有订阅中心，本次将保留订阅密钥、已注册主机和备份数据。"
    else
      backup_and_reset_partial_center
      ask_center_parameters
    fi
    ;;
  3)
    if center_complete; then code="$(cat /etc/vvv-sub/registration.code)"; else ask_optional_vvc1 code; fi
    ;;
  4)
    ask_required_jpr3
    ;;
  5)
    if center_complete; then code="$(cat /etc/vvv-sub/registration.code)"; else ask_optional_vvc1 code; fi
    ;;
  6) ;;
esac

show_parameter_summary

case "$choice" in
  1)
    ensure_host
    enable_relay
    ensure_center
    rebuild_roles_from_system
    register_current_main_role
    ;;
  2)
    ensure_host
    ensure_center
    rebuild_roles_from_system
    register_current_main_role
    ;;
  3)
    ensure_host
    enable_relay
    rebuild_roles_from_system
    register_current_main_role "$code"
    ;;
  4)
    install_landing "$key"
    rebuild_roles_from_system
    code="$(jpr_registration_code "$key" || true)"
    bash "$BASE_DIR/register_sync.sh" landing "$code"
    ;;
  5)
    ensure_host
    rebuild_roles_from_system
    register_current_main_role "$code"
    ;;
  6)
    python3 "$BASE_DIR/restore_manager.py"
    if main_state_valid; then
      VVV_PROTOCOL_MODE="$(json_value "$MAIN_STATE" protocol_mode dual)"
      VVV_PROXY_PORT="$(json_value "$MAIN_STATE" listen_port 443)"
      VVV_REALITY_SNI="$(json_value "$MAIN_STATE" sni www.softbank.jp)"
      VVV_HY2_LIMIT_MBPS="$(json_value "$MAIN_STATE" hy2_limit_mbps 50)"
      export VVV_PROTOCOL_MODE VVV_PROXY_PORT VVV_REALITY_SNI VVV_HY2_LIMIT_MBPS
      bash "$BASE_DIR/host.sh"
    fi
    if [[ -s "$CENTER_CFG" ]]; then VVV_RESTORE_MODE=1 bash "$BASE_DIR/center_install.sh"; fi
    rebuild_roles_from_system
    if center_complete; then register_current_main_role; elif [[ -s /etc/vvv/client.json ]]; then systemctl start vvv-sync.service || true; fi
    echo
    echo "========== 恢复后逐节点检测 =========="
    restore_log="$(jq -r '.log // empty' /run/vvv-restore-result.json 2>/dev/null || true)"
    if [[ -n "$restore_log" ]]; then python3 "$BASE_DIR/node_probe.py" | tee -a "$restore_log" || true; else python3 "$BASE_DIR/node_probe.py" || true; fi
    python3 "$BASE_DIR/backup_manager.py" create restore-completed --force >/dev/null || true
    echo "云备份恢复和当前最新版程序重建完成。"
    [[ ! -s /run/vvv-restore-result.json ]] || jq . /run/vvv-restore-result.json
    ;;
esac

install_unified_manager
printf '\nVVV 安装、续装或角色追加完成。以后统一输入：vps\n'
/usr/local/sbin/vps

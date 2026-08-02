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
    assert s.get('schema') == 3
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

center_config_valid() {
  [[ -s "$CENTER_CFG" ]] || return 1
  python3 - "$CENTER_CFG" <<'PY_CENTER_VALID'
import json,sys
from pathlib import Path
try:
    s=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    assert s.get('schema') == 2
    assert s.get('mode') in ('domain','ip')
    assert str(s.get('base_url','')).startswith('https://')
    assert int(s.get('public_port',0)) > 0
    assert s.get('subscription_token')
    assert s.get('master_token')
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
     -e /etc/caddy/.vvv-ip-final-active ]]
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
  echo "4. 安装中转副机（通过主机代理）"
  echo "5. 安装直连代理"
  echo "0. 退出"
}

load_existing_proxy_parameters() {
  VVV_PROTOCOL_MODE="$(json_value "$MAIN_STATE" protocol_mode dual)"
  VVV_PROXY_PORT="$(json_value "$MAIN_STATE" listen_port 443)"
  VVV_REALITY_SNI="$(json_value "$MAIN_STATE" sni www.softbank.jp)"
  export VVV_PROTOCOL_MODE VVV_PROXY_PORT VVV_REALITY_SNI
  REUSE_PROXY=1
}

load_existing_center_parameters() {
  VVV_SUB_DOMAIN="$(json_value "$CENTER_CFG" domain "")"
  VVV_SUB_PORT="$(json_value "$CENTER_CFG" public_port 8443)"
  export VVV_SUB_DOMAIN VVV_SUB_PORT
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
  systemctl disable --now vvv-ip-cert-renew.timer vvv-sync.timer vvv-sync.path >/dev/null 2>&1 || true
  systemctl stop vvv-sub.service caddy.service >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/vvv-sub.service \
        /etc/systemd/system/vvv-ip-cert-renew.service \
        /etc/systemd/system/vvv-ip-cert-renew.timer \
        /etc/systemd/system/caddy.service \
        /usr/local/sbin/vvv-center \
        /usr/local/lib/vvv/deploy-ip-cert.sh
  rm -rf /etc/vvv-sub /var/lib/vvv-sub /etc/caddy/.vvv-ip-final-active /etc/caddy/Caddyfile /etc/caddy/certs
  systemctl daemon-reload
  echo "不完整订阅中心已清理；残留备份：$backup_dir"
}

ask_code(){
  local __var=$1 prompt=$2 value
  read -r -p "$prompt（直接回车表示暂不注册）：" value
  printf -v "$__var" '%s' "$value"
}

ask_center_address(){
  local __var=$1 value
  read -r -p "请输入订阅中心 IP 地址或域名（直接回车暂不注册，默认 HTTPS 端口 8443）：" value
  value="${value//[[:space:]]/}"
  printf -v "$__var" '%s' "$value"
}

ask_required_jpr3(){
  while true; do
    read -r -p "请输入完整 JPR3 对接密钥（中转模式必填）：" key
    key="${key//[[:space:]]/}"
    if [[ -z "$key" ]]; then
      echo "中转模式必须输入 JPR3 对接密钥，不能跳过。"
      continue
    fi
    if [[ "$key" != JPR3.* ]]; then
      echo "对接密钥格式错误，必须以 JPR3. 开头。"
      continue
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
  export VVV_PROTOCOL_MODE VVV_PROXY_PORT VVV_REALITY_SNI
}

ask_center_parameters(){
  local input
  echo
  while true; do
    read -r -p "请输入订阅 HTTPS 域名（直接回车使用本机公网 IP）：" input
    input="${input,,}"; input="${input%.}"
    if [[ -z "$input" ]]; then
      VVV_SUB_DOMAIN=""
      break
    fi
    if valid_domain "$input"; then VVV_SUB_DOMAIN="$input"; break; fi
    echo "域名格式不正确，请重新输入；也可以直接回车使用本机公网 IP。"
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
    echo "本机代理已完整安装，复用现有协议、端口和永久凭证，跳过重复安装。"
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
  local changed=0
  install -d -m700 /usr/local/lib/vvv
  for file in sub_center.py backup_manager.py; do
    if [[ ! -f "/usr/local/lib/vvv/$file" ]] || ! cmp -s "$BASE_DIR/$file" "/usr/local/lib/vvv/$file"; then
      install -m755 "$BASE_DIR/$file" "/usr/local/lib/vvv/$file"
      changed=1
    fi
  done
  if (( changed == 1 )); then
    echo "检测到订阅中心程序更新，保留全部数据并重新启动内部服务。"
    timeout 75 systemctl restart vvv-sub.service
  fi
}

ensure_center_runtime() {
  systemctl daemon-reload
  systemctl enable vvv-sub.service caddy.service >/dev/null 2>&1 || true
  systemctl is-active --quiet vvv-sub.service || timeout 75 systemctl restart vvv-sub.service
  systemctl is-active --quiet caddy.service || timeout 75 systemctl restart caddy.service
  systemctl is-active --quiet vvv-sub.service &&
  systemctl is-active --quiet caddy.service
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
  sh "$tmp"
  rm -f "$tmp"
  [[ -x /usr/local/sbin/landing-vps ]] || fail "中转副机安装后管理命令不存在。"
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
  local role_name protocol_name
  case "$choice" in
    1) role_name="安装订阅中心 + 中转主机 + 自身代理" ;;
    2) role_name="安装订阅中心 + 自身代理" ;;
    3) role_name="安装中转主机 + 自身代理" ;;
    4) role_name="安装中转副机（通过主机代理）" ;;
    5) role_name="安装直连代理" ;;
  esac
  echo
  echo "========== 安装参数总览 =========="
  echo "安装角色：$role_name"
  if [[ "$choice" == 4 ]]; then
    echo "JPR3 密钥：已填写（${#key} 个字符）"
  else
    case "$VVV_PROTOCOL_MODE" in dual) protocol_name="VLESS + Hysteria 2";; vless) protocol_name="仅 VLESS";; hy2) protocol_name="仅 Hysteria 2";; esac
    echo "代理协议：$protocol_name$([[ "$REUSE_PROXY" == 1 ]] && echo '（复用现有）')"
    echo "代理端口：$VVV_PROXY_PORT"
    [[ "$VVV_PROTOCOL_MODE" == hy2 ]] || echo "REALITY 伪装域名：$VVV_REALITY_SNI"
    if [[ "$choice" == 1 || "$choice" == 2 ]]; then
      if [[ -n "$VVV_SUB_DOMAIN" ]]; then
        echo "订阅入口：https://${VVV_SUB_DOMAIN}:${VVV_SUB_PORT}$([[ "$REUSE_CENTER" == 1 ]] && echo '（复用现有）')"
      else
        echo "订阅入口：https://本机公网IP:${VVV_SUB_PORT}$([[ "$REUSE_CENTER" == 1 ]] && echo '（复用现有）' || echo '（自动申请免费 IP 证书）')"
      fi
    elif [[ "$choice" == 3 ]]; then
      [[ -n "$code" ]] && echo "订阅中心接入码：已填写或将使用本机订阅中心" || echo "订阅中心接入码：未填写（独立使用）"
    elif [[ "$choice" == 5 ]]; then
      [[ -n "$center_address" ]] && echo "订阅中心地址：$center_address（自动注册直连节点）" || echo "订阅中心地址：未填写（本次暂不注册）"
    fi
  fi
  echo "=================================="
  echo "参数已收集完毕，直接开始全自动安装。"
}

REUSE_PROXY=0
REUSE_CENTER=0
code=""
key=""
center_address=""

show_install_menu
while true; do
  read -r -p "请输入编号：" choice
  case "$choice" in 0|1|2|3|4|5) break ;; *) echo "请输入 0-5。" ;; esac
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
    if center_complete; then
      code="$(cat /etc/vvv-sub/registration.code)"
    else
      ask_code code "请输入订阅中心接入码"
    fi
    ;;
  4)
    ask_required_jpr3
    ;;
  5)
    if center_complete; then
      center_address="本机订阅中心"
    else
      ask_center_address center_address
    fi
    ;;
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
    if center_complete; then
      register_current_main_role
    else
      bash "$BASE_DIR/register_sync.sh" direct "" "$center_address"
    fi
    ;;
esac

install_unified_manager
printf '\nVVV 安装、续装或角色追加完成。以后统一输入：vps\n'
/usr/local/sbin/vps

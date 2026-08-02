#!/usr/bin/env python3
from pathlib import Path
import re


def read(path):
    return Path(path).read_text(encoding='utf-8')


def write(path, text):
    Path(path).write_text(text, encoding='utf-8')


def replace_once(path, old, new, label):
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one target, found {count}')
    write(path, text.replace(old, new, 1))


def regex_once(path, pattern, replacement, label):
    text = read(path)
    changed, count = re.subn(pattern, replacement, text, count=1, flags=re.M | re.S)
    if count != 1:
        raise SystemExit(f'{label}: expected one target, found {count}')
    write(path, changed)


bootstrap = 'core-src/bootstrap.sh'
regex_once(
    bootstrap,
    r'^center_config_valid\(\) \{.*?^\}\n',
    r'''center_config_valid() {
  [[ -s "$CENTER_CFG" ]] || return 1
  python3 - "$CENTER_CFG" <<'PY_CENTER_VALID'
import json,re,sys
from pathlib import Path
try:
    s=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    assert s.get('schema') == 3
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
except Exception:
    raise SystemExit(1)
PY_CENTER_VALID
}
''',
    'center schema validation',
)
regex_once(
    bootstrap,
    r'^center_complete\(\) \{.*?^\}\n',
    r'''center_complete() {
  center_config_valid &&
  [[ -s /etc/vvv-sub/registration.code ]] &&
  [[ -x /usr/local/sbin/vvv-center ]] &&
  [[ -x /usr/local/lib/vvv/sub_center.py ]] &&
  [[ -x /usr/local/lib/vvv/client_adapters.py ]] &&
  [[ -x /usr/local/lib/vvv/adapter_manager.py ]] &&
  [[ -x /usr/local/lib/vvv/center_transport.sh ]] &&
  [[ -f /etc/systemd/system/vvv-sub.service ]] &&
  [[ -f /etc/systemd/system/caddy.service ]] &&
  [[ -s /etc/caddy/Caddyfile ]] &&
  { [[ "$(json_value "$CENTER_CFG" transport_mode "")" != tunnel ]] || [[ -f /etc/systemd/system/vvv-cloudflared.service ]]; }
}
''',
    'center complete validation',
)
regex_once(
    bootstrap,
    r'^center_partial\(\) \{.*?^\}\n',
    r'''center_partial() {
  [[ -e /etc/vvv-sub || -e /var/lib/vvv-sub || -e /usr/local/sbin/vvv-center ||
     -e /etc/systemd/system/vvv-sub.service || -e /etc/systemd/system/caddy.service ||
     -e /etc/systemd/system/vvv-cloudflared.service || -e /etc/caddy/Caddyfile ]]
}
''',
    'center partial validation',
)
regex_once(
    bootstrap,
    r'^load_existing_center_parameters\(\) \{.*?^\}\n',
    r'''load_existing_center_parameters() {
  VVV_SUB_DOMAIN="$(json_value "$CENTER_CFG" domain "")"
  VVV_SUB_PORT="$(json_value "$CENTER_CFG" public_port 8443)"
  VVV_SUB_TRANSPORT="$(json_value "$CENTER_CFG" transport_mode direct-https)"
  VVV_SUB_SUFFIX="$(json_value "$CENTER_CFG" subscription_suffix "")"
  VVV_CF_TUNNEL_TOKEN=""
  export VVV_SUB_DOMAIN VVV_SUB_PORT VVV_SUB_TRANSPORT VVV_SUB_SUFFIX VVV_CF_TUNNEL_TOKEN
  REUSE_CENTER=1
}
''',
    'load center parameters',
)
replace_once(
    bootstrap,
    'systemctl disable --now vvv-ip-cert-renew.timer vvv-sync.timer vvv-sync.path >/dev/null 2>&1 || true',
    'systemctl disable --now vvv-ip-cert-renew.timer vvv-cloudflared.service vvv-sync.timer vvv-sync.path >/dev/null 2>&1 || true',
    'partial center disable services',
)
replace_once(
    bootstrap,
    'systemctl stop vvv-sub.service caddy.service >/dev/null 2>&1 || true',
    'systemctl stop vvv-sub.service caddy.service vvv-cloudflared.service >/dev/null 2>&1 || true',
    'partial center stop services',
)
replace_once(
    bootstrap,
    '''        /etc/systemd/system/caddy.service \\
        /usr/local/sbin/vvv-center \\
        /usr/local/lib/vvv/deploy-ip-cert.sh''',
    '''        /etc/systemd/system/caddy.service \\
        /etc/systemd/system/vvv-cloudflared.service \\
        /usr/local/sbin/vvv-center \\
        /usr/local/lib/vvv/deploy-ip-cert.sh \\
        /usr/local/lib/vvv/run-cloudflared.sh''',
    'partial center remove services',
)
replace_once(
    bootstrap,
    'rm -rf /etc/vvv-sub /var/lib/vvv-sub /etc/caddy/.vvv-ip-final-active /etc/caddy/Caddyfile /etc/caddy/certs',
    'rm -rf /etc/vvv-sub /var/lib/vvv-sub /etc/caddy/.vvv-ip-final-active /etc/caddy/Caddyfile /etc/caddy/certs',
    'partial center cleanup anchor',
)
regex_once(
    bootstrap,
    r'^ask_center_parameters\(\) \{.*?^\}\n',
    r'''random_subscription_suffix() {
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
  echo "   不申请证书，适合频繁重装测试；后期可在 vps 菜单开启 HTTPS。"
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
''',
    'center parameter prompts',
)
regex_once(
    bootstrap,
    r'^refresh_center_runtime_code\(\) \{.*?^\}\n',
    r'''refresh_center_runtime_code() {
  local changed=0 file target mode
  install -d -m700 /usr/local/lib/vvv
  for file in sub_center.py backup_manager.py rclone_manager.sh client_adapters.py adapter_manager.py center_transport.sh; do
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
}
''',
    'refresh center runtime',
)
regex_once(
    bootstrap,
    r'^ensure_center_runtime\(\) \{.*?^\}\n',
    r'''ensure_center_runtime() {
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
''',
    'ensure center runtime',
)
regex_once(
    bootstrap,
    r'^show_parameter_summary\(\) \{.*?^\}\n',
    r'''show_parameter_summary(){
  local role_name protocol_name endpoint scheme transport_label
  case "$choice" in
    1) role_name="安装订阅中心 + 中转主机 + 自身代理";;
    2) role_name="安装订阅中心 + 自身代理";;
    3) role_name="安装中转主机 + 自身代理";;
    4) role_name="安装中转副机";;
    5) role_name="安装直连代理";;
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
      [[ -n "$code" ]] && echo "订阅中心接入码：已填写或将使用本机订阅中心" || echo "订阅中心接入码：未填写（独立使用）"
    elif [[ "$choice" == 5 ]]; then
      [[ -n "$center_address" ]] && echo "订阅中心地址：$center_address（自动注册直连节点）" || echo "订阅中心地址：未填写（本次暂不注册）"
    fi
  fi
  echo "=================================="
  echo "参数已收集完毕，直接开始全自动安装。"
}
''',
    'parameter summary',
)
replace_once(
    bootstrap,
    'center_address=""\n',
    'center_address=""\nVVV_CF_TUNNEL_TOKEN=""\n',
    'center token initialization',
)

# Network installer downloads and validates all independently updatable modules.
installer = 'vvv-install.sh'
replace_once(
    installer,
    'files=(host.sh landing.sh center_install.sh register_sync.sh vvv_manager.sh sub_center.py sync_agent.py backup_manager.py rclone_manager.sh)',
    'files=(host.sh landing.sh center_install.sh register_sync.sh vvv_manager.sh sub_center.py sync_agent.py backup_manager.py rclone_manager.sh client_adapters.py adapter_manager.py center_transport.sh center_manager.sh)',
    'installer file list',
)
replace_once(
    installer,
    'for file in bootstrap.sh center_install.sh register_sync.sh vvv_manager.sh rclone_manager.sh host.sh; do',
    'for file in bootstrap.sh center_install.sh register_sync.sh vvv_manager.sh rclone_manager.sh center_transport.sh center_manager.sh host.sh; do',
    'installer bash validation list',
)
replace_once(
    installer,
    'python3 -m py_compile "$TMP/app/sub_center.py" "$TMP/app/sync_agent.py" "$TMP/app/backup_manager.py" || fail "Python 模块语法检查失败。"',
    'python3 -m py_compile "$TMP/app/sub_center.py" "$TMP/app/sync_agent.py" "$TMP/app/backup_manager.py" "$TMP/app/client_adapters.py" "$TMP/app/adapter_manager.py" || fail "Python 模块语法检查失败。"\npython3 "$TMP/app/client_adapters.py" >/dev/null || fail "客户端适配器自检失败。"',
    'installer python validation list',
)

# Use standalone center manager so repeatable installs can refresh the menu.
center = 'core-src/center_install.sh'
replace_once(
    center,
    'for file in sub_center.py sync_agent.py backup_manager.py rclone_manager.sh client_adapters.py adapter_manager.py center_transport.sh; do',
    'for file in sub_center.py sync_agent.py backup_manager.py rclone_manager.sh client_adapters.py adapter_manager.py center_transport.sh center_manager.sh; do',
    'center source install list',
)
replace_once(
    center,
    'write_center_manager\npython3 /usr/local/lib/vvv/backup_manager.py create first-install --force >/dev/null',
    'install -m700 "$BASE_DIR/center_manager.sh" /usr/local/sbin/vvv-center\npython3 /usr/local/lib/vvv/backup_manager.py create first-install --force >/dev/null',
    'standalone center manager install',
)

# Certificate and Tunnel material join the encrypted package only after cloud backup is enabled.
backup = 'core-src/backup_manager.py'
text = read(backup)
start = text.index('SOURCES = [')
end = text.index('\n]\n', start) + 3
replacement = '''BASE_SOURCES = [
    Path('/etc/vvv-sub/config.json'),
    Path('/var/lib/vvv-sub/registry.json'),
    Path('/var/lib/vvv-sub/hosts'),
    Path('/etc/jp-relay/state.json'),
    Path('/etc/jp-relay/landing-state.json'),
    Path('/etc/vvv/client.json'),
    Path('/etc/vvv/roles.json'),
    Path('/root/VVV-订阅中心恢复信息.txt'),
    Path('/etc/vvv-sub/cloud.json'),
    Path('/etc/vvv-sub/rclone.conf'),
]
CLOUD_ONLY_SOURCES = [
    Path('/etc/letsencrypt'),
    Path('/etc/caddy'),
    Path('/var/lib/caddy/.local/share/caddy'),
    Path('/etc/vvv-sub/cloudflared.token'),
    Path('/etc/systemd/system/vvv-cloudflared.service'),
    Path('/usr/local/lib/vvv/run-cloudflared.sh'),
]


def cloud_backup_enabled():
    cfg = read_json(CLOUD_CFG, {}) or {}
    return cfg.get('enabled') is True


def sources():
    return BASE_SOURCES + (CLOUD_ONLY_SOURCES if cloud_backup_enabled() else [])
'''
text = text[:start] + replacement + text[end:]
text = text.replace('for path in SOURCES:', 'for path in sources():')
text = text.replace('for src in SOURCES:', 'for src in sources():')
write(backup, text)

# New modules are part of final syntax/runtime validation.
validation = 'tests/final_runtime_validation.sh'
replace_once(
    validation,
    '''  "$ROOT/core-src/backup_manager.py" \\
  "$ROOT/tests/conformance.py"''',
    '''  "$ROOT/core-src/backup_manager.py" \\
  "$ROOT/core-src/client_adapters.py" \\
  "$ROOT/core-src/adapter_manager.py" \\
  "$ROOT/tests/conformance.py"''',
    'runtime python compile list',
)
replace_once(
    validation,
    '''bash -n "$ROOT/core-src/rclone_manager.sh"
sh -n "$ROOT/core-src/landing.sh"''',
    '''bash -n "$ROOT/core-src/rclone_manager.sh"
bash -n "$ROOT/core-src/center_transport.sh"
bash -n "$ROOT/core-src/center_manager.sh"
sh -n "$ROOT/core-src/landing.sh"
python3 "$ROOT/core-src/client_adapters.py" >/dev/null''',
    'runtime shell validation list',
)

# README: replace obsolete four-path and HTTPS-only descriptions.
readme = 'README.md'
text = read(readme)
text = text.replace('- 可选订阅域名与订阅 HTTPS 端口，端口默认 TCP `8443`；', '- 订阅域名/IP、传输方式、服务端口与统一订阅后缀；')
text = text.replace('订阅域名可以直接按回车留空。留空时自动使用本机公网 IPv4，并申请 Let’s Encrypt 短期公网 IP 证书。参数总览显示后直接开始安装，不再要求输入 `Y`，安装过程中也不会穿插新的问题。', '订阅中心支持直接 HTTPS、直接 HTTP、Cloudflare Tunnel 三种传输方式。统一订阅后缀直接回车时随机生成 8 位大小写字母和数字；手动输入允许 6–32 位。参数总览显示后直接开始安装，不再要求二次确认。')
text = re.sub(r'## 客户端订阅\n.*?## 订阅中心与 HTTPS\n', '''## 客户端订阅

所有支持客户端共用一个订阅地址，例如：

```text
https://v.example.com:8443/Ud2xR9zN
```

服务端根据客户端请求头自动返回 Clash Verge Rev/Mihomo、Quantumult X、Loon 或 Shadowrocket 格式。旧 `/r/密钥/c`、`/qx`、`/ln`、`/sr` 路径及 `format` 查询参数均已移除。未知客户端返回 415；可在 `vps → 订阅中心管理 → 客户端请求头识别调试` 中查看脱敏请求信息。客户端适配器可独立更新，不重装系统、不修改节点数据。

## 订阅中心传输方式
''', text, count=1, flags=re.S)
text = re.sub(r'- 输入域名时，Caddy 自动申请和续期域名证书.*?- 公网必须放行 TCP/80 和订阅 HTTPS 端口。', '''- 直接 HTTPS：域名由 Caddy 自动申请公共证书；公网 IP 由 Certbot 申请 Let’s Encrypt 短期 IP 证书；
- 直接 HTTP：不申请证书，适合频繁重装测试，但节点凭据以明文传输；
- HTTP 模式可在 `vps` 菜单事务式开启 HTTPS；失败自动恢复 HTTP，成功后原 HTTP 入口立即失效；
- Cloudflare Tunnel：公开地址使用标准 `https://域名/后缀`，VPS 只提供本地 HTTP 源站，迁移服务器时可保持客户端地址不变；
- Tunnel 模式需要提前在 Cloudflare 创建 Tunnel 公共主机名，并将其指向脚本显示的本地 HTTP 地址；
- 已注册 VVV 主机从 HTTP 中心同步时会优先尝试同地址 HTTPS，成功后永久升级且不再降级。''', text, count=1, flags=re.S)
text = text.replace('- 云备份默认关闭，可选 Google Drive 或 Microsoft OneDrive；', '- 云备份默认关闭，可选 Google Drive 或 Microsoft OneDrive；开启后加密包自动包含 Let’s Encrypt、Caddy 域名证书及 Cloudflare Tunnel 配置；')
write(readme, text)

print('UNIFIED SUBSCRIPTION PATCH APPLIED')

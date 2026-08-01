#!/usr/bin/env python3
from pathlib import Path
import re

ROOT = Path('.')


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding='utf-8')


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding='utf-8')


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        if new in text:
            return text
        raise SystemExit(f'anchor not found: {label}')
    return text.replace(old, new, 1)


def sub_once(text: str, pattern: str, repl, label: str, flags: int = 0) -> str:
    text2, n = re.subn(pattern, repl, text, count=1, flags=flags)
    if n != 1:
        raise SystemExit(f'regex anchor count={n}: {label}')
    return text2

# ---------------------------------------------------------------------------
# Network installer: fresh-install only and no QR dependency.
# ---------------------------------------------------------------------------
p = 'vvv-install.sh'
t = read(p)
t = t.replace(' backup_manager.py rclone_manager.sh qr_helper.sh)', ' backup_manager.py rclone_manager.sh)')
anchor = '[[ "${ID:-}" == debian && "${VERSION_ID:-}" == 13 ]] || fail "VVV 仅支持 Debian 13。当前系统：${PRETTY_NAME:-未知}"\n'
insert = anchor + '''for old_path in /etc/vvv /etc/jp-relay /etc/vvv-sub /usr/local/lib/vvv-source; do
  [[ ! -e "$old_path" ]] || fail "检测到已有 VVV 或代理状态。当前版本只支持全新安装，请重装 Debian 13 后再执行。"
done
'''
t = replace_once(t, anchor, insert, 'fresh install guard')
write(p, t)

# ---------------------------------------------------------------------------
# Unified menu: exact requested order and mandatory HTTPS domain.
# ---------------------------------------------------------------------------
p = 'core-src/bootstrap.sh'
t = read(p)
new_ask_center = r'''ask_center_parameters(){
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
'''
t = sub_once(t, r'(?ms)^ask_center_parameters\(\)\{.*?^\}\n(?=jpr_registration_code\(\)\{)', lambda m: new_ask_center, 'ask_center_parameters')
new_summary = r'''show_parameter_summary(){
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
'''
t = sub_once(t, r'(?ms)^show_parameter_summary\(\)\{.*?^\}\n\n(?=cat <<\'EOF\')', lambda m: new_summary + '\n', 'show_parameter_summary')
old_menu_pattern = r"(?ms)^cat <<'EOF'\n========== VVV 一体化安装管理 ==========.*?^EOF\n"
new_menu = r'''cat <<'EOF'
========== VVV 一体化安装管理 ==========

1. 安装订阅中心+中转主机（含自身代理）

2. 仅安装订阅中心（含自身代理）

3. 仅安装中转主机（含自身代理）

4. 仅安装中转副机（通过主机代理）

5. 仅安装直连代理

0. 退出
EOF
'''
t = sub_once(t, old_menu_pattern, lambda m: new_menu, 'initial menu')
old_collect = r'''case "$choice" in
  1) ask_proxy_parameters; ask_center_parameters ;;
  2) ask_proxy_parameters; ask_code code "请输入订阅中心接入码" ;;
  3)
    read -r -p "请输入完整 JPR3 对接密钥：" key
    [[ "$key" == JPR3.* ]] || { echo "错误：对接密钥必须以 JPR3. 开头。" >&2; exit 1; }
    ;;
  4) ask_proxy_parameters; ask_code code "请输入订阅中心接入码" ;;
  5) ask_proxy_parameters; ask_center_parameters ;;
esac
'''
new_collect = r'''case "$choice" in
  1) ask_proxy_parameters; ask_center_parameters ;;
  2) ask_proxy_parameters; ask_center_parameters ;;
  3) ask_proxy_parameters; ask_code code "请输入订阅中心接入码" ;;
  4)
    read -r -p "请输入完整 JPR3 对接密钥：" key
    [[ "$key" == JPR3.* ]] || { echo "错误：对接密钥必须以 JPR3. 开头。" >&2; exit 1; }
    ;;
  5) ask_proxy_parameters; ask_code code "请输入订阅中心接入码" ;;
esac
'''
t = replace_once(t, old_collect, new_collect, 'front-loaded role parameters')
old_execute = r'''case "$choice" in
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
    awk -v key="$key" 'BEGIN{done=0} !done && /^PAIRING_KEY=/ {print "PAIRING_KEY=\\047" key "\\047"; done=1; next} {print}' "$BASE_DIR/landing.sh" > "$tmp"
    chmod 700 "$tmp"; sh "$tmp"; rm -f "$tmp"
    [[ -x /usr/local/sbin/vps ]] && cp -f /usr/local/sbin/vps /usr/local/sbin/vvv-landing-original
    write_roles false false true false landing
    code="$(jpr_registration_code "$key" || true)"
    bash "$BASE_DIR/register_sync.sh" landing "$code"
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
'''
new_execute = r'''case "$choice" in
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
'''
t = replace_once(t, old_execute, new_execute, 'role execution mapping')
write(p, t)

# ---------------------------------------------------------------------------
# Manager: fresh-install release has no in-place upgrade compatibility path.
# ---------------------------------------------------------------------------
p = 'core-src/vvv_manager.sh'
t = read(p)
t = sub_once(t, r'(?ms)^sync_role\(\)\{.*?^\}\n', '', 'remove all-role compatibility mapper')
t = t.replace('/usr/local/lib/vvv/register_sync.sh "$(sync_role)" "$code"', '/usr/local/lib/vvv/register_sync.sh "$(primary)" "$code"')
t = sub_once(t, r'(?ms)^update_vvv\(\)\{.*?^\}\n', '', 'remove upgrade function')
t = t.replace('  echo "$n. 检查并升级 VVV"; act[$n]=update\n', '')
t = t.replace('    update) update_vvv; exit;;\n', '')
write(p, t)

# ---------------------------------------------------------------------------
# Subscription center: HTTPS domain is mandatory; remove all QR functionality.
# ---------------------------------------------------------------------------
p = 'core-src/center_install.sh'
t = read(p)
old_install_caddy = r'''install_caddy(){
  local arch api url tmp
  case "$(uname -m)" in x86_64|amd64) arch=amd64;; aarch64|arm64) arch=arm64;; *) fail "Caddy 不支持当前架构。";; esac
  api="$(curl -fsSL --retry 5 --retry-all-errors https://api.github.com/repos/caddyserver/caddy/releases/latest)" || fail "无法查询 Caddy。"
  url="$(jq -r --arg s "linux_${arch}.tar.gz" '.assets[]|select(.name|endswith($s))|.browser_download_url' <<<"$api"|head -n1)"
  [[ -n "$url" && "$url" != null ]] || fail "找不到 Caddy 安装包。"
  tmp="$(mktemp -d)"; curl -fsSL --retry 5 --retry-all-errors "$url" -o "$tmp/caddy.tgz"
  tar -xzf "$tmp/caddy.tgz" -C "$tmp" caddy; install -m755 "$tmp/caddy" /usr/local/bin/caddy; rm -rf "$tmp"
}
'''
new_install_caddy = r'''install_caddy(){
  local arch api asset_name url digest expected actual tmp
  case "$(uname -m)" in x86_64|amd64) arch=amd64;; aarch64|arm64) arch=arm64;; *) fail "Caddy 不支持当前架构。";; esac
  api="$(curl -fsSL --retry 5 --retry-all-errors https://api.github.com/repos/caddyserver/caddy/releases/latest)" || fail "无法查询 Caddy。"
  asset_name="$(jq -r --arg s "linux_${arch}.tar.gz" '.assets[]|select(.name|endswith($s))|.name' <<<"$api"|head -n1)"
  url="$(jq -r --arg n "$asset_name" '.assets[]|select(.name==$n)|.browser_download_url' <<<"$api"|head -n1)"
  digest="$(jq -r --arg n "$asset_name" '.assets[]|select(.name==$n)|(.digest // "")' <<<"$api"|head -n1)"
  [[ -n "$url" && "$url" != null ]] || fail "找不到 Caddy 安装包。"
  [[ "$digest" =~ ^sha256:[0-9a-fA-F]{64}$ ]] || fail "GitHub 没有返回 Caddy 安装包 SHA256。"
  expected="${digest#sha256:}"
  tmp="$(mktemp -d)"; curl -fsSL --retry 5 --retry-all-errors "$url" -o "$tmp/caddy.tgz"
  actual="$(sha256sum "$tmp/caddy.tgz"|awk '{print $1}')"
  [[ "${expected,,}" == "${actual,,}" ]] || fail "Caddy 安装包 SHA256 校验失败。"
  tar -xzf "$tmp/caddy.tgz" -C "$tmp" caddy; install -m755 "$tmp/caddy" /usr/local/bin/caddy; rm -rf "$tmp"
}
'''
t = replace_once(t, old_install_caddy, new_install_caddy, 'verified Caddy installer')
old_mode = r'''domain="${VVV_SUB_DOMAIN:-}"; domain="${domain,,}"; domain="${domain%.}"
public_port="${VVV_SUB_PORT:-8443}"
valid_port "$public_port" || fail "订阅端口无效。"
[[ "$public_port" != 443 ]] || fail "订阅端口不能占用 443。"
mode=ip
if [[ -n "$domain" ]]; then
  valid_domain "$domain" || fail "订阅域名格式不正确。"
  mapfile -t resolved < <(getent ahostsv4 "$domain"|awk '{print $1}'|sort -u)
  ((${#resolved[@]})) || fail "订阅域名尚未解析到 IPv4。"
  printf '%s\n' "${resolved[@]}"|grep -Fxq "$public_ip" || fail "订阅域名没有解析到本机 IP $public_ip。"
  mode=domain
fi
'''
new_mode = r'''domain="${VVV_SUB_DOMAIN:-}"; domain="${domain,,}"; domain="${domain%.}"
public_port="${VVV_SUB_PORT:-8443}"
valid_domain "$domain" || fail "订阅中心只提供 HTTPS，必须填写有效域名。"
valid_port "$public_port" || fail "订阅端口无效。"
[[ "$public_port" != 443 ]] || fail "订阅端口不能占用 443。"
mapfile -t resolved < <(getent ahostsv4 "$domain"|awk '{print $1}'|sort -u)
((${#resolved[@]})) || fail "订阅域名尚未解析到 IPv4。"
printf '%s\n' "${resolved[@]}"|grep -Fxq "$public_ip" || fail "订阅域名没有解析到本机 IP $public_ip。"
mode=domain
'''
t = replace_once(t, old_mode, new_mode, 'mandatory HTTPS mode')
t = t.replace('install -y ca-certificates curl jq openssl python3 tar gzip qrencode', 'install -y ca-certificates curl jq openssl python3 tar gzip')
t = t.replace('open_port "$public_port"; [[ "$mode" != domain ]] || open_port 80', 'open_port "$public_port"; open_port 80')
t = t.replace('for f in sub_center.py sync_agent.py backup_manager.py rclone_manager.sh qr_helper.sh;', 'for f in sub_center.py sync_agent.py backup_manager.py rclone_manager.sh;')
t = replace_once(t,
    'if [[ "$mode" == domain ]]; then base_url="https://${domain}:${public_port}"; listen_host=127.0.0.1; listen_port=$SERVICE_PORT; else base_url="http://${public_ip}:${public_port}"; listen_host=0.0.0.0; listen_port=$public_port; fi',
    'base_url="https://${domain}:${public_port}"; listen_host=127.0.0.1; listen_port=$SERVICE_PORT',
    'HTTPS base URL')
t = t.replace('if [[ "$mode" == domain ]]; then\n  install_caddy', 'install_caddy', 1)
t = replace_once(t, "fi\nsystemctl daemon-reload; systemctl enable --now vvv-sub.service\n[[ \"$mode\" != domain ]] || systemctl enable --now caddy.service", "systemctl daemon-reload; systemctl enable --now vvv-sub.service caddy.service", 'always enable Caddy')
t = sub_once(t, r'(?ms)^if \[\[ "\$mode" == domain \]\]; then\n  echo "正在等待 HTTPS 证书签发……"; ok=0\n  for _ in \$\(seq 1 180\); do curl -fsS --resolve .*?^fi\n', '''echo "正在等待 HTTPS 证书签发……"; ok=0
for _ in $(seq 1 180); do curl -fsS --resolve "${domain}:${public_port}:127.0.0.1" "https://${domain}:${public_port}/health" >/dev/null 2>&1 && { ok=1; break; }; sleep 1; done
[[ $ok == 1 ]] || { journalctl -u caddy -n120 --no-pager; fail "HTTPS 订阅入口未就绪，请检查 TCP/80 和 TCP/${public_port}。"; }
''', 'mandatory HTTPS readiness')
new_center_cli = r'''cat > /usr/local/sbin/vvv-center <<'SH'
#!/usr/bin/env bash
set -Eeuo pipefail
cfg=/etc/vvv-sub/config.json
base="$(jq -r .base_url "$cfg")"; token="$(jq -r .subscription_token "$cfg")"; master="$(jq -r .master_token "$cfg")"
show_urls(){
  echo "Clash Verge Rev：${base}/r/${token}/c"
  echo "Quantumult X：${base}/r/${token}/qx"
  echo "Loon：${base}/r/${token}/ln"
  echo "Shadowrocket：${base}/r/${token}/sr"
  echo "v2rayNG：${base}/r/${token}/v2"
}
show_hosts(){ curl -fsS -H "Authorization: Bearer $master" "http://127.0.0.1:$(jq -r .listen_port "$cfg")/api/v1/hosts" | jq .; }
case "${1:-menu}" in
  urls) show_urls;; hosts) show_hosts;;
  *) while true; do
    echo; echo "========== 订阅中心管理 =========="
    echo "1. 查看订阅地址"; echo "2. 查看本地备份"
    echo "3. 开启云备份功能"; echo "4. 查看并测试云备份"; echo "5. 关闭或重新配置云备份"
    echo "6. 查看已注册主机"; echo "7. 查看服务状态"; echo "8. 查看恢复信息"; echo "0. 返回"
    read -r -p "请输入编号：" x
    case "$x" in
      1) show_urls;; 2) python3 /usr/local/lib/vvv/backup_manager.py list;;
      3) /usr/local/lib/vvv/rclone_manager.sh enable;; 4) /usr/local/lib/vvv/rclone_manager.sh status;;
      5) echo "1. 关闭云备份"; echo "2. 重新配置云备份"; read -r -p "请选择：" y; [[ $y == 1 ]] && /usr/local/lib/vvv/rclone_manager.sh disable || [[ $y == 2 ]] && /usr/local/lib/vvv/rclone_manager.sh reconfigure;;
      6) show_hosts;; 7) systemctl --no-pager --full status vvv-sub.service caddy.service 2>/dev/null || true;;
      8) cat /root/VVV-订阅中心恢复信息.txt;; 0) exit 0;; *) echo "请输入有效编号。";;
    esac
  done;;
esac
SH
'''
t = sub_once(t, r"(?ms)^cat > /usr/local/sbin/vvv-center <<'SH'.*?^SH\n", lambda m: new_center_cli, 'center CLI without QR')
t = t.replace('/usr/local/sbin/vvv-center qr', '/usr/local/sbin/vvv-center urls')
write(p, t)

# ---------------------------------------------------------------------------
# v2rayNG 2.2.6: dedicated HY2 link must carry certificate pin, no insecure.
# ---------------------------------------------------------------------------
p = 'core-src/sub_center.py'
t = read(p)
old_v2 = '''def hy2_uri_v2rayng(node):
    params=[('obfs','salamander'),('obfs-password',node['obfs_password']),('sni',node['sni']),('insecure','1')]
    return f"hy2://{quote(node['password'],safe='')}@{node['server']}:{node['port']}?{urlencode(params)}#{quote(node['name'],safe='')}"
'''
new_v2 = '''def hy2_uri_v2rayng(node):
    params=[('obfs','salamander'),('obfs-password',node['obfs_password']),('sni',node['sni']),('pinSHA256',node['pin'])]
    return f"hysteria2://{quote(node['password'],safe='')}@{node['server']}:{node['port']}/?{urlencode(params)}#{quote(node['name'],safe='')}"
'''
t = replace_once(t, old_v2, new_v2, 'v2rayNG Hysteria2 URI')
write(p, t)

# ---------------------------------------------------------------------------
# Host source: proper leaf cert, dedicated v2rayNG links, no QR generation.
# ---------------------------------------------------------------------------
p = 'core-src/host.sh'
t = read(p)
t = t.replace('tzdata kmod util-linux qrencode', 'tzdata kmod util-linux')
cert_old = '''    -subj "/CN=${server_name}" \\
    -addext "subjectAltName=DNS:${server_name}" \\
    -keyout "$key" -out "$cert" >/dev/null 2>&1'''
cert_new = '''    -subj "/CN=${server_name}" \\
    -addext "subjectAltName=DNS:${server_name}" \\
    -addext "basicConstraints=critical,CA:FALSE" \\
    -addext "keyUsage=critical,digitalSignature" \\
    -addext "extendedKeyUsage=serverAuth" \\
    -keyout "$key" -out "$cert" >/dev/null 2>&1'''
t = replace_once(t, cert_old, cert_new, 'proper HY2 leaf certificate')
t = t.replace('qx_lines=[]; share_links=[]; loon_lines=[]; clash_entries=[]', 'qx_lines=[]; share_links=[]; v2rayng_links=[]; loon_lines=[]; clash_entries=[]')
t = t.replace('qx_lines.append(qx); share_links.append((name,uri)); loon_lines.append(loon); clash_entries.append(clash)', 'qx_lines.append(qx); share_links.append((name,uri)); v2rayng_links.append((name,uri)); loon_lines.append(loon); clash_entries.append(clash)', 1)
old_hy2 = '''    params=[("obfs","salamander"),("obfs-password",h["obfs_password"]),("sni",h["server_name"]),("insecure","1"),("pinSHA256",h["certificate_pin_hex"])]
    uri=f"hysteria2://{quote(password,safe='')}@{ip}:{port}/?{urlencode(params)}#{quote(name,safe='')}"
    loon=f"{loon_name(name)} = Hysteria2,{ip},{port},{loon_q(password)},skip-cert-verify=true,sni={h['server_name']},udp=true,fast-open=true,salamander-password={loon_q(h['obfs_password'])}"
'''
new_hy2 = '''    share_params=[("obfs","salamander"),("obfs-password",h["obfs_password"]),("sni",h["server_name"]),("insecure","1"),("pinSHA256",h["certificate_pin_hex"])]
    uri=f"hysteria2://{quote(password,safe='')}@{ip}:{port}/?{urlencode(share_params)}#{quote(name,safe='')}"
    v2_params=[("obfs","salamander"),("obfs-password",h["obfs_password"]),("sni",h["server_name"]),("pinSHA256",h["certificate_pin_hex"])]
    v2_uri=f"hysteria2://{quote(password,safe='')}@{ip}:{port}/?{urlencode(v2_params)}#{quote(name,safe='')}"
    loon=f"{loon_name(name)} = Hysteria2,{ip},{port},{loon_q(password)},skip-cert-verify=true,sni={h['server_name']},udp=true,fast-open=true,salamander-password={loon_q(h['obfs_password'])}"
'''
t = replace_once(t, old_hy2, new_hy2, 'host dedicated v2rayNG HY2 URI')
t = t.replace('share_links.append((name,uri)); loon_lines.append(loon); clash_entries.append(clash)', 'share_links.append((name,uri)); v2rayng_links.append((name,v2_uri)); loon_lines.append(loon); clash_entries.append(clash)', 1)
t = t.replace('share_text="\\n".join(uri for _,uri in share_links)\nqr_index="\\n".join(f"{name}\\t{uri}" for name,uri in share_links)', 'share_text="\\n".join(uri for _,uri in share_links)\nv2rayng_text="\\n".join(uri for _,uri in v2rayng_links)')
t = t.replace('","扫码链接："]', '","分享链接："]')
t = replace_once(t, '    for name,uri in share_links: lines += [f"[{name}]",uri]\nif clash_entries:', '    for name,uri in share_links: lines += [f"[{name}]",uri]\nif v2rayng_links:\n    lines += ["","【v2rayNG 2.2.6+】"]\n    for name,uri in v2rayng_links: lines += [f"[{name}]",uri]\nif clash_entries:', 'host v2rayNG summary')
t = t.replace('(out/"Loon-Shadowrocket-二维码索引.tsv").write_text((qr_index+"\\n") if qr_index else "",encoding="utf-8")\n', '')
t = t.replace('(out/"Shadowrocket-二维码索引.tsv").write_text((qr_index+"\\n") if qr_index else "",encoding="utf-8")\n', '')
t = replace_once(t, '(out/"Shadowrocket.txt").write_text((share_text+"\\n") if share_text else "",encoding="utf-8")\n', '(out/"Shadowrocket.txt").write_text((share_text+"\\n") if share_text else "",encoding="utf-8")\n(out/"v2rayNG.txt").write_text((v2rayng_text+"\\n") if v2rayng_text else "",encoding="utf-8")\n', 'host v2rayNG file')
t = sub_once(t, r'(?ms)^show_loon_shadowrocket_qr\(\) \{.*?^\}\n\n(?=generate_direct_client_files\(\))', '', 'remove host QR function')
t = re.sub(r'^\s*show_loon_shadowrocket_qr .*\n', '', t, flags=re.M)
t = sub_once(t, r"(?ms)^  cat > /usr/local/sbin/jp-show-nodes <<'EOF_SHOW'.*?^EOF_SHOW\n", '''  cat > /usr/local/sbin/jp-show-nodes <<'EOF_SHOW'
#!/usr/bin/env bash
cat /root/日本VPS-客户端节点.txt
EOF_SHOW
''', 'host show-nodes without QR')
t = t.replace('生成日本直连节点和二维码', '生成日本直连节点')
t = t.replace('日本直连节点与二维码', '日本直连节点')
t = t.replace('客户端配置和二维码', '客户端配置')
t = t.replace('二维码', '')
t = t.replace('qrencode', '')
write(p, t)

# ---------------------------------------------------------------------------
# Landing source: no QR and dedicated v2rayNG HY2 URI.
# ---------------------------------------------------------------------------
p = 'core-src/landing.sh'
t = read(p)
t = t.replace('tzdata kmod qrencode util-linux python3', 'tzdata kmod util-linux python3')
for line in (
    '  : > "$CLIENT_DIR/Loon-Shadowrocket-二维码索引.tsv"\n',
    '  : > "$CLIENT_DIR/Shadowrocket-二维码索引.tsv"\n',
    '    printf \'%s\\t%s\\n\' "$vless_name" "$vless_uri" >> "$CLIENT_DIR/Loon-Shadowrocket-二维码索引.tsv"\n',
    '    printf \'%s\\t%s\\n\' "$vless_name" "$vless_uri" >> "$CLIENT_DIR/Shadowrocket-二维码索引.tsv"\n',
    '    printf \'%s\\t%s\\n\' "$hy2_name" "$hy2_uri" >> "$CLIENT_DIR/Loon-Shadowrocket-二维码索引.tsv"\n',
    '    printf \'%s\\t%s\\n\' "$hy2_name" "$hy2_uri" >> "$CLIENT_DIR/Shadowrocket-二维码索引.tsv"\n',
):
    t = t.replace(line, '')
t = replace_once(t, '  : > "$CLIENT_DIR/Shadowrocket.txt"\n', '  : > "$CLIENT_DIR/Shadowrocket.txt"\n  : > "$CLIENT_DIR/v2rayNG.txt"\n', 'landing v2rayNG output file')
t = replace_once(t, '    printf \'%s\\n\' "$vless_uri" >> "$CLIENT_DIR/Shadowrocket.txt"\n', '    printf \'%s\\n\' "$vless_uri" >> "$CLIENT_DIR/Shadowrocket.txt"\n    printf \'%s\\n\' "$vless_uri" >> "$CLIENT_DIR/v2rayNG.txt"\n', 'landing VLESS v2rayNG link')
old_landing_hy2 = '    hy2_uri="hysteria2://$(urlencode "$JAPAN_HY2_PASSWORD")@${JAPAN_PUBLIC_IP}:${JAPAN_PORT}/?obfs=salamander&obfs-password=$(urlencode "$JAPAN_HY2_OBFS")&sni=$(urlencode "$JAPAN_HY2_SERVER_NAME")&insecure=1&pinSHA256=$(urlencode "$JAPAN_HY2_PIN_HEX")#${encoded_hy2_name}"\n'
new_landing_hy2 = old_landing_hy2 + '    v2rayng_hy2_uri="hysteria2://$(urlencode "$JAPAN_HY2_PASSWORD")@${JAPAN_PUBLIC_IP}:${JAPAN_PORT}/?obfs=salamander&obfs-password=$(urlencode "$JAPAN_HY2_OBFS")&sni=$(urlencode "$JAPAN_HY2_SERVER_NAME")&pinSHA256=$(urlencode "$JAPAN_HY2_PIN_HEX")#${encoded_hy2_name}"\n'
t = replace_once(t, old_landing_hy2, new_landing_hy2, 'landing dedicated v2rayNG HY2 URI')
t = replace_once(t, '    printf \'%s\\n\' "$hy2_uri" >> "$CLIENT_DIR/Shadowrocket.txt"\n', '    printf \'%s\\n\' "$hy2_uri" >> "$CLIENT_DIR/Shadowrocket.txt"\n    printf \'%s\\n\' "$v2rayng_hy2_uri" >> "$CLIENT_DIR/v2rayNG.txt"\n', 'landing HY2 v2rayNG file')
t = t.replace('      echo "扫码链接："', '      echo "分享链接："')
t = replace_once(t, '      echo "$hy2_uri"\n    } >> "$CLIENT_DIR/客户端节点.txt"', '      echo "$hy2_uri"\n      echo\n      echo "【v2rayNG 2.2.6+：${hy2_name}】"\n      echo "$v2rayng_hy2_uri"\n    } >> "$CLIENT_DIR/客户端节点.txt"', 'landing v2rayNG summary')
t = sub_once(t, r'(?ms)^show_loon_shadowrocket_qr\(\) \{.*?^\}\n\n(?=save_state\(\))', '', 'remove landing QR function')
t = sub_once(t, r"(?ms)^show_nodes\(\) \{\n  cat \"\$nodes\".*?^\}\n\n(?=valid_ipv4\(\))", 'show_nodes() {\n  cat "$nodes"\n}\n\n', 'landing shortcut without QR')
t = t.replace('index=/root/中转客户端配置/Loon-Shadowrocket-二维码索引.tsv\n', '')
t = re.sub(r'^\s*show_loon_shadowrocket_qr\s*$', '', t, flags=re.M)
t = t.replace('生成客户端配置和二维码', '生成客户端配置')
t = t.replace('客户端配置、二维码和实时在线状态', '客户端配置和实时在线状态')
t = t.replace('二维码', '')
t = t.replace('qrencode', '')
write(p, t)

# ---------------------------------------------------------------------------
# Transformer: stop injecting QR helpers into final rendered scripts.
# ---------------------------------------------------------------------------
p = 'src/prepare.py'
t = read(p)
replacement = '''# Loon Salamander compatibility. QR output is intentionally unsupported.\nh = h.replace("salamander-password={loon_q(h['obfs_password'])}", "salamander-password={h['obfs_password']}")\n\n'''
t = sub_once(t, r'(?ms)^# Shared SSH QR renderer and Loon Salamander compatibility\..*?(?=^# Preallocate VLESS users)', replacement, 'remove host QR transformer')
t = sub_once(t, r'(?ms)^l = l\.replace\("salamander-password=.*?^landing\.write_text\(l, encoding=\'utf-8\'\)\n', '''l = l.replace("salamander-password={loon_q(h['obfs_password'])}", "salamander-password={h['obfs_password']}")
landing.write_text(l, encoding='utf-8')
''', 'remove landing QR transformer')
write(p, t)

# ---------------------------------------------------------------------------
# README and tests.
# ---------------------------------------------------------------------------
p = 'README.md'
t = read(p)
t = sub_once(t, r'(?ms)^## 安装角色\n\n```text\n.*?```\n', '''## 安装角色

```text
1. 安装订阅中心+中转主机（含自身代理）
2. 仅安装订阅中心（含自身代理）
3. 仅安装中转主机（含自身代理）
4. 仅安装中转副机（通过主机代理）
5. 仅安装直连代理
0. 退出
```
''', 'README menu')
t = t.replace('- Clash、Quantumult X、Loon：显示订阅地址，不生成二维码；\n- Shadowrocket、v2rayNG：显示订阅地址并在 SSH 终端显示带白边二维码；\n', '- 五种客户端均只显示订阅地址或文本配置，不生成二维码；\n')
t = t.replace('- v2rayNG 使用独立 `hy2://` 链接，不写入 `pinSHA256`。', '- v2rayNG 2.2.6 使用独立 `hysteria2://` 链接，并写入 `pinSHA256` 证书指纹，不再依赖 `insecure`。')
t = t.replace('- 域名模式使用 HTTPS，并检查域名 IPv4 A 记录是否指向本机；\n- 不使用域名时可用本机 IP + HTTP；', '- 订阅中心强制使用域名 HTTPS，并检查域名 IPv4 A 记录是否指向本机；\n- 不提供本机 IP + HTTP 订阅模式；')
t += '\n## 安装策略\n\n当前版本只按全新 Debian 13 首次安装设计。检测到旧 VVV 状态时会停止，不提供原地升级、迁移或旧版本兼容。\n'
write(p, t)

p = 'tests/conformance.py'
t = read(p)
old_labels = '''    labels = [
        '1. 安装订阅中心（含自身代理）',
        '2. 安装中转主机（含自身代理）',
        '3. 安装中转副机',
        '4. 安装直连代理',
        '5. 以上全部安装（不含副机）',
        '0. 退出',
    ]'''
new_labels = '''    labels = [
        '1. 安装订阅中心+中转主机（含自身代理）',
        '2. 仅安装订阅中心（含自身代理）',
        '3. 仅安装中转主机（含自身代理）',
        '4. 仅安装中转副机（通过主机代理）',
        '5. 仅安装直连代理',
        '0. 退出',
    ]'''
t = replace_once(t, old_labels, new_labels, 'conformance menu labels')
t = t.replace("'请输入订阅域名（直接回车使用本机 IP）',", "'请输入订阅 HTTPS 域名（必须已解析到本机）',")
t = t.replace("'请输入订阅服务端口 [默认 8443]',", "'请输入订阅 HTTPS 端口 [默认 8443]',")
t = t.replace("require('register_sync.sh\" center-relay \"$code\"' in text, 'All in One 没有映射为 center-relay 同步角色')", "require('write_roles true true false true center-relay' in text and 'register_sync.sh\" center-relay \"$code\"' in text, '菜单 1 没有安装订阅中心+中转主机')")
t = t.replace("require('register_sync.sh\" all ' not in text, '仍使用 sync_agent 不支持的 all 角色')", "require('write_roles true true false true all' not in text, '仍保留旧 all 主角色')")
t = t.replace("hy2 = next((line for line in v2_lines if line.startswith('hy2://')), '')", "hy2 = next((line for line in v2_lines if line.startswith('hysteria2://')), '')")
t = t.replace("require(hy2, 'v2rayNG 没有独立 hy2:// 链接')", "require(hy2, 'v2rayNG 没有独立 hysteria2:// 链接')")
t = t.replace("require('pinSHA256' not in hy2, 'v2rayNG HY2 不应携带 pinSHA256')", "require('pinSHA256=' in hy2, 'v2rayNG HY2 缺少 pinSHA256 证书指纹')")
t = t.replace("for token in ('sni=', 'insecure=1', 'obfs=salamander', 'obfs-password='):", "require('insecure=' not in hy2, 'v2rayNG 2.2.6 HY2 不应继续依赖 insecure')\n    for token in ('sni=', 'pinSHA256=', 'obfs=salamander', 'obfs-password='):")
old_center_assert = '''    center = read('core-src/center_install.sh')
    require("Shadowrocket|${base}/r/${token}/sr" in center and "v2rayNG|${base}/r/${token}/v2" in center, '订阅二维码应只显示 Shadowrocket 和 v2rayNG')
    require("Quantumult X|${base}/r/${token}/qx" not in center and "Loon|${base}/r/${token}/ln" not in center, 'QX 或 Loon 不应生成订阅二维码')
'''
new_center_assert = '''    center = read('core-src/center_install.sh')
    require('base_url="https://${domain}:${public_port}"' in center, '订阅中心没有强制 HTTPS')
    require('http://${public_ip}' not in center and 'mode=ip' not in center, '仍保留明文 IP 订阅模式')
'''
t = replace_once(t, old_center_assert, new_center_assert, 'conformance HTTPS center')
new_no_qr = '''def test_no_qr_output():
    files = [
        'vvv-install.sh', 'core-src/bootstrap.sh', 'core-src/host.sh', 'core-src/landing.sh',
        'core-src/center_install.sh', 'core-src/vvv_manager.sh', 'src/prepare.py',
        'tests/final_runtime_validation.sh', '.github/workflows/validate.yml', 'README.md',
    ]
    text = '\\n'.join(read(path) for path in files)
    for token in ('qrencode', 'qr_helper'):
        require(token not in text, f'仍保留二维码实现：{token}')
    implementation = '\\n'.join(read(path) for path in (
        'vvv-install.sh', 'core-src/bootstrap.sh', 'core-src/host.sh', 'core-src/landing.sh',
        'core-src/center_install.sh', 'core-src/vvv_manager.sh', 'src/prepare.py',
    ))
    require('二维码' not in implementation, '生产脚本仍保留二维码菜单、文件或提示')
    require(not (ROOT / 'core-src/qr_helper.sh').exists(), '二维码辅助文件仍存在')


def test_https_and_fresh_install_only():
    installer = read('vvv-install.sh')
    bootstrap = read('core-src/bootstrap.sh')
    center = read('core-src/center_install.sh')
    manager = read('core-src/vvv_manager.sh')
    require('当前版本只支持全新安装' in installer, '网络入口没有拒绝旧安装状态')
    require('订阅中心只提供 HTTPS' in bootstrap and '域名不能为空' in bootstrap, '订阅域名仍可留空')
    require('mode=domain' in center and 'mode=ip' not in center, '订阅中心没有锁定 HTTPS 域名模式')
    require('base_url="https://${domain}:${public_port}"' in center, '订阅中心基础地址不是 HTTPS')
    require('检查并升级 VVV' not in manager and 'update_vvv' not in manager, '仍保留原地升级兼容入口')
    require('sync_role' not in manager and 'center-relay' not in manager, '仍保留旧 all 角色兼容映射')


def test_hy2_leaf_certificate():
    host = read('core-src/host.sh')
    for token in ('basicConstraints=critical,CA:FALSE', 'keyUsage=critical,digitalSignature', 'extendedKeyUsage=serverAuth'):
        require(token in host, f'HY2 证书缺少叶子证书约束：{token}')
'''
t = sub_once(t, r'(?ms)^def test_qr_helper\(\):.*?(?=^def test_debian13_only\(\):)', new_no_qr + '\n', 'replace QR conformance test')
old_test_list = '''        test_jpr3_and_slot_architecture,
        test_qr_helper,
        test_debian13_only,
'''
new_test_list = '''        test_jpr3_and_slot_architecture,
        test_no_qr_output,
        test_https_and_fresh_install_only,
        test_hy2_leaf_certificate,
        test_debian13_only,
'''
t = replace_once(t, old_test_list, new_test_list, 'conformance test list')
write(p, t)

p = 'tests/final_runtime_validation.sh'
t = read(p)
t = t.replace('bash -n "$ROOT/core-src/qr_helper.sh"\n', '')
t = sub_once(t, r"(?ms)^log 'Verify terminal QR renderer'.*?(?=^log 'Final result')", '', 'remove runtime QR validation')
t = replace_once(t, "  -subj '/CN=jp-hy2.jp-relay.local' -addext 'subjectAltName=DNS:jp-hy2.jp-relay.local' \\\n  -keyout \"$WORK/server.key\" -out \"$WORK/server.crt\" >/dev/null 2>&1", "  -subj '/CN=jp-hy2.jp-relay.local' -addext 'subjectAltName=DNS:jp-hy2.jp-relay.local' \\\n  -addext 'basicConstraints=critical,CA:FALSE' -addext 'keyUsage=critical,digitalSignature' \\\n  -addext 'extendedKeyUsage=serverAuth' \\\n  -keyout \"$WORK/server.key\" -out \"$WORK/server.crt\" >/dev/null 2>&1\nopenssl x509 -in \"$WORK/server.crt\" -noout -text | grep -q 'CA:FALSE'", 'runtime leaf certificate fixture')
insert_anchor = '''build_hy2_slot_configs "$WORK/state-empty.json" "$WORK/hy2-empty"
build_hy2_slot_configs "$WORK/state-active.json" "$WORK/hy2-active"
'''
insert_block = insert_anchor + '''mkdir -p "$WORK/client-files"
generate_client_files "$WORK/state-active.json" "" "$WORK/client-files" direct >/dev/null
[[ -s "$WORK/client-files/v2rayNG.txt" ]]
grep -q '^hysteria2://' "$WORK/client-files/v2rayNG.txt"
grep -q 'pinSHA256=' "$WORK/client-files/v2rayNG.txt"
! grep -q 'insecure=' "$WORK/client-files/v2rayNG.txt"
! find "$WORK/client-files" -type f -name '*二维码*' | grep -q .
'''
t = replace_once(t, insert_anchor, insert_block, 'runtime v2rayNG client validation')
write(p, t)

p = '.github/workflows/validate.yml'
t = read(p)
t = t.replace('curl unzip jq openssl iproute2 qrencode', 'curl unzip jq openssl iproute2')
write(p, t)

production_files = [
    'vvv-install.sh', 'core-src/bootstrap.sh', 'core-src/host.sh', 'core-src/landing.sh',
    'core-src/center_install.sh', 'core-src/vvv_manager.sh', 'core-src/sub_center.py',
    'src/prepare.py', 'tests/final_runtime_validation.sh',
    '.github/workflows/validate.yml', 'README.md',
]
combined = '\n'.join(read(path) for path in production_files)
for forbidden in ('qrencode', 'qr_helper'):
    if forbidden in combined:
        raise SystemExit(f'forbidden QR implementation remains: {forbidden}')
for required in (
    '1. 安装订阅中心+中转主机（含自身代理）',
    '2. 仅安装订阅中心（含自身代理）',
    '3. 仅安装中转主机（含自身代理）',
    '4. 仅安装中转副机（通过主机代理）',
    '5. 仅安装直连代理',
    'base_url="https://${domain}:${public_port}"',
    'pinSHA256',
    'basicConstraints=critical,CA:FALSE',
    '当前版本只支持全新安装',
):
    if required not in combined:
        raise SystemExit(f'required final feature missing: {required}')
if 'http://${public_ip}' in read('core-src/center_install.sh') or 'mode=ip' in read('core-src/center_install.sh'):
    raise SystemExit('plaintext subscription mode remains')
if "('insecure','1')" in read('core-src/sub_center.py').split('def hy2_uri_v2rayng', 1)[1].split('def render_qx', 1)[0]:
    raise SystemExit('v2rayNG Hysteria2 renderer still uses insecure')
print('SSH log fixes applied successfully')

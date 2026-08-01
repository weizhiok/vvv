#!/usr/bin/env python3
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing anchor: {label}")
    return text.replace(old, new, 1)

def regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    out, count = re.subn(pattern, lambda _m: replacement, text, count=1, flags=re.M | re.S)
    if count != 1:
        raise SystemExit(f"regex anchor failed ({count}): {label}")
    return out

path = ROOT / "core-src/bootstrap.sh"
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    '''    install_host; enable_relay
    write_roles true true false true center-relay''',
    '''    install_host; enable_relay
    echo
    echo "========== 继续安装订阅中心 =========="
    echo "不会重启整台 VPS；只会按需启动或重启 Caddy、订阅中心等服务，当前 SSH 不受影响。"
    write_roles true true false true center-relay''',
    "bootstrap role 1 transition",
)
text = replace_once(
    text,
    '''    install_host
    write_roles true false false true center''',
    '''    install_host
    echo
    echo "========== 继续安装订阅中心 =========="
    echo "不会重启整台 VPS；只会按需启动或重启 Caddy、订阅中心等服务，当前 SSH 不受影响。"
    write_roles true false false true center''',
    "bootstrap role 2 transition",
)
path.write_text(text, encoding="utf-8")

path = ROOT / "core-src/center_install.sh"
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    '''CERTBOT_DIR=/opt/vvv-certbot
fail(){ echo "错误：$*" >&2; exit 1; }''',
    '''CERTBOT_DIR=/opt/vvv-certbot
CENTER_STARTED=$SECONDS
fail(){ echo "错误：$*" >&2; exit 1; }
section(){ printf '\\n========== %s ==========\\n' "$*"; }
service_diagnostics(){
  local service="$1"
  systemctl --no-pager --full status "$service" 2>/dev/null || true
  journalctl -u "$service" -n120 --no-pager 2>/dev/null || true
}
ensure_service_active(){
  local service="$1" action="${2:-restart}" wait_seconds="${3:-75}"
  systemctl enable "$service" >/dev/null 2>&1 || true
  systemctl reset-failed "$service" >/dev/null 2>&1 || true
  if ! timeout "$wait_seconds" systemctl "$action" "$service"; then
    if ! systemctl is-active --quiet "$service"; then
      service_diagnostics "$service"
      fail "${service} 执行 ${action} 失败。"
    fi
  fi
  for _ in $(seq 1 15); do
    systemctl is-active --quiet "$service" && return 0
    sleep 1
  done
  service_diagnostics "$service"
  fail "${service} 未进入 active 状态。"
}''',
    "center helpers",
)

new_install_caddy = r'''install_caddy(){
  local arch api asset_name url digest expected actual tmp
  case "$(uname -m)" in x86_64|amd64) arch=amd64;; aarch64|arm64) arch=arm64;; *) fail "Caddy 不支持当前架构。";; esac
  echo "正在查询 Caddy 最新稳定版……"
  api="$(curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 --max-time 90 https://api.github.com/repos/caddyserver/caddy/releases/latest)" || fail "无法查询 Caddy。"
  asset_name="$(jq -r --arg s "linux_${arch}.tar.gz" '.assets[]|select(.name|endswith($s))|.name' <<<"$api"|head -n1)"
  url="$(jq -r --arg n "$asset_name" '.assets[]|select(.name==$n)|.browser_download_url' <<<"$api"|head -n1)"
  digest="$(jq -r --arg n "$asset_name" '.assets[]|select(.name==$n)|(.digest // "")' <<<"$api"|head -n1)"
  [[ -n "$url" && "$url" != null ]] || fail "找不到 Caddy 安装包。"
  [[ "$digest" =~ ^sha256:[0-9a-fA-F]{64}$ ]] || fail "GitHub 没有返回 Caddy 安装包 SHA256。"
  expected="${digest#sha256:}"
  tmp="$(mktemp -d)"
  echo "正在下载 Caddy：${asset_name}"
  curl -fL --retry 5 --retry-all-errors --connect-timeout 15 --max-time 300 "$url" -o "$tmp/caddy.tgz" || fail "下载 Caddy 失败。"
  actual="$(sha256sum "$tmp/caddy.tgz"|awk '{print $1}')"
  [[ "${expected,,}" == "${actual,,}" ]] || fail "Caddy 安装包 SHA256 校验失败。"
  tar -xzf "$tmp/caddy.tgz" -C "$tmp" caddy
  install -m755 "$tmp/caddy" /usr/local/bin/caddy
  rm -rf "$tmp"
  echo "Caddy 安装完成：$(/usr/local/bin/caddy version)"
}
'''
text = regex_once(text, r'^install_caddy\(\)\{.*?^\}\n(?=write_caddy_service\(\)\{)', new_install_caddy, "install_caddy")

new_service = r'''write_caddy_service(){
  cat > /etc/systemd/system/caddy.service <<'UNIT'
[Unit]
Description=Caddy HTTPS Server
After=network-online.target
Wants=network-online.target
[Service]
Type=notify
User=caddy
Group=caddy
Environment=HOME=/var/lib/caddy
ExecStart=/usr/local/bin/caddy run --environ --config /etc/caddy/Caddyfile --adapter caddyfile
Restart=on-failure
RestartSec=3
TimeoutStartSec=45s
TimeoutStopSec=45s
KillSignal=SIGTERM
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=/var/lib/caddy /var/log/caddy
[Install]
WantedBy=multi-user.target
UNIT
}
'''
text = regex_once(text, r'^write_caddy_service\(\)\{.*?^\}\n(?=validate_caddy\(\)\{)', new_service, "write_caddy_service")

new_ip_certificate = r'''install_ip_certificate(){
  local cert_name live_dir
  cert_name="vvv-ip-${public_ip//./-}"
  live_dir="/etc/letsencrypt/live/${cert_name}"

  echo "正在创建独立 Certbot 环境……"
  python3 -m venv --clear "$CERTBOT_DIR"
  echo "正在安装 Certbot 5.4+；安装过程会显示进度，不会重启整台 VPS……"
  timeout 600 "$CERTBOT_DIR/bin/pip" install \
    --disable-pip-version-check \
    --no-cache-dir \
    --retries 5 \
    --timeout 30 \
    'certbot>=5.4,<6' || fail "Certbot 安装失败或超时。"
  "$CERTBOT_DIR/bin/certbot" --version

  echo "正在向 Let’s Encrypt 申请公网 IP 短期证书；最多等待 10 分钟……"
  timeout 600 "$CERTBOT_DIR/bin/certbot" certonly \
    --non-interactive \
    --agree-tos \
    --register-unsafely-without-email \
    --preferred-profile shortlived \
    --webroot \
    --webroot-path "$ACME_WEBROOT" \
    --ip-address "$public_ip" \
    --cert-name "$cert_name" \
    --key-type ecdsa \
    --elliptic-curve secp256r1 || fail "公网 IP HTTPS 证书申请失败或超时。"

  [[ -s "$live_dir/fullchain.pem" && -s "$live_dir/privkey.pem" ]] || fail "Certbot 没有生成完整的 IP 证书。"

  cat > /usr/local/lib/vvv/deploy-ip-cert.sh <<EOF_DEPLOY
#!/usr/bin/env bash
set -Eeuo pipefail
MARKER=/etc/caddy/.vvv-ip-final-active
install -d -o caddy -g caddy -m700 ${CADDY_CERT_DIR}
install -o caddy -g caddy -m600 ${live_dir}/fullchain.pem ${CADDY_CERT_DIR}/ip-fullchain.pem
install -o caddy -g caddy -m600 ${live_dir}/privkey.pem ${CADDY_CERT_DIR}/ip-privkey.pem
# 首次申请证书时，Caddy 仍在临时 HTTP 验证配置中，不能执行 reload。
# 当前配置关闭了 admin API，Caddy reload 必然失败；续期后改为有界重启。
if [[ -f "\$MARKER" ]] && systemctl is-active --quiet caddy.service; then
  if ! timeout 75 systemctl restart caddy.service; then
    systemctl --no-pager --full status caddy.service >&2 || true
    journalctl -u caddy.service -n120 --no-pager >&2 || true
    exit 1
  fi
fi
EOF_DEPLOY
  chmod 700 /usr/local/lib/vvv/deploy-ip-cert.sh
  /usr/local/lib/vvv/deploy-ip-cert.sh

  cat > /etc/systemd/system/vvv-ip-cert-renew.service <<EOF_UNIT
[Unit]
Description=Renew VVV Let's Encrypt IP certificate
After=network-online.target caddy.service
Wants=network-online.target
ConditionPathExists=/etc/letsencrypt/renewal/${cert_name}.conf
[Service]
Type=oneshot
TimeoutStartSec=15min
ExecStart=${CERTBOT_DIR}/bin/certbot renew --quiet --cert-name ${cert_name} --deploy-hook /usr/local/lib/vvv/deploy-ip-cert.sh
EOF_UNIT
  cat > /etc/systemd/system/vvv-ip-cert-renew.timer <<'UNIT'
[Unit]
Description=Check VVV IP certificate twice daily
[Timer]
OnCalendar=*-*-* 00,12:17:00
RandomizedDelaySec=30m
Persistent=true
[Install]
WantedBy=timers.target
UNIT
}
'''
text = regex_once(text, r'^install_ip_certificate\(\)\{.*?^\}\n\n(?=\[\[ \$\(id -u\))', new_ip_certificate + "\n", "install_ip_certificate")

old_packages = '''apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 update >/dev/null
DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 install -y \\
  ca-certificates curl jq openssl python3 python3-venv tar gzip >/dev/null
open_port "$public_port"'''
new_packages = '''section "准备订阅中心依赖"
required_packages=(ca-certificates curl jq openssl python3 tar gzip)
[[ "$mode" != ip ]] || required_packages+=(python3-venv)
missing_packages=()
for package in "${required_packages[@]}"; do
  dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed' || missing_packages+=("$package")
done
if ((${#missing_packages[@]})); then
  echo "正在安装缺少的依赖：${missing_packages[*]}"
  if ! timeout 600 env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 install -y "${missing_packages[@]}"; then
    echo "首次安装依赖失败，正在刷新软件索引后重试……"
    timeout 600 apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 update
    timeout 600 env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 install -y "${missing_packages[@]}" || fail "订阅中心依赖安装失败。"
  fi
else
  echo "订阅中心依赖已齐全，跳过重复 apt update。"
fi
open_port "$public_port"'''
text = replace_once(text, old_packages, new_packages, "dependency installation")

new_bottom = r'''section "安装 HTTPS 前端"
install_caddy
id caddy >/dev/null 2>&1 || useradd --system --home /var/lib/caddy --shell /usr/sbin/nologin caddy
install -d -o caddy -g caddy -m750 /var/lib/caddy /var/log/caddy
install -d -o caddy -g caddy -m700 "$CADDY_CERT_DIR"
write_caddy_service
systemctl daemon-reload

section "启动订阅中心内部服务"
ensure_service_active vvv-sub.service restart 60

if [[ "$mode" == domain ]]; then
  section "配置域名 HTTPS"
  rm -f /etc/caddy/.vvv-ip-final-active
  write_domain_caddyfile
  validate_caddy
  ensure_service_active caddy.service restart 75
else
  section "启动公网 IP 证书验证服务"
  write_ip_bootstrap_caddyfile
  validate_caddy
  ensure_service_active caddy.service restart 75

  section "申请公网 IP HTTPS 证书"
  install_ip_certificate

  section "切换到公网 IP HTTPS 正式配置"
  write_ip_final_caddyfile
  validate_caddy
  touch /etc/caddy/.vvv-ip-final-active
  systemctl daemon-reload
  ensure_service_active caddy.service restart 75
  systemctl enable --now vvv-ip-cert-renew.timer
  echo "公网 IP 证书自动续期定时器已启用。"
fi'''
text = regex_once(text, r'^install_caddy\nid caddy .*?^fi\n\n(?=for _ in \$\(seq 1 40\))', new_bottom + "\n\n", "service startup flow")

old_internal_wait = '''for _ in $(seq 1 40); do
  curl -fsS "http://127.0.0.1:${listen_port}/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "http://127.0.0.1:${listen_port}/health" >/dev/null || {'''
new_internal_wait = '''echo "正在检查订阅中心内部服务……"
for attempt in $(seq 1 40); do
  curl -fsS --connect-timeout 2 --max-time 4 "http://127.0.0.1:${listen_port}/health" >/dev/null 2>&1 && break
  (( attempt % 10 != 0 )) || echo "内部服务仍在启动：已等待 ${attempt} 秒……"
  sleep 1
done
curl -fsS --connect-timeout 2 --max-time 4 "http://127.0.0.1:${listen_port}/health" >/dev/null || {'''
text = replace_once(text, old_internal_wait, new_internal_wait, "internal health wait")

old_https_wait = '''for _ in $(seq 1 180); do
  if [[ "$mode" == domain ]]; then
    curl -fsS --resolve "${domain}:${public_port}:127.0.0.1" "https://${domain}:${public_port}/health" >/dev/null 2>&1 && { ok=1; break; }
  else
    curl -fsS --connect-to "${public_ip}:${public_port}:127.0.0.1:${public_port}" "https://${public_ip}:${public_port}/health" >/dev/null 2>&1 && { ok=1; break; }
  fi
  sleep 1
done'''
new_https_wait = '''for attempt in $(seq 1 180); do
  if [[ "$mode" == domain ]]; then
    curl -fsS --connect-timeout 3 --max-time 6 --resolve "${domain}:${public_port}:127.0.0.1" "https://${domain}:${public_port}/health" >/dev/null 2>&1 && { ok=1; break; }
  else
    curl -fsS --connect-timeout 3 --max-time 6 --connect-to "${public_ip}:${public_port}:127.0.0.1:${public_port}" "https://${public_ip}:${public_port}/health" >/dev/null 2>&1 && { ok=1; break; }
  fi
  (( attempt % 10 != 0 )) || echo "HTTPS 入口仍在准备：已等待 ${attempt} 秒……"
  sleep 1
done'''
text = replace_once(text, old_https_wait, new_https_wait, "https health wait")

text = replace_once(
    text,
    '''printf '\\n订阅中心安装成功。\\nHTTPS 模式：%s\\n主机接入码：%s\\n' "$mode" "$registration_code"''',
    '''printf '\\n订阅中心安装成功，总耗时 %s 秒。\\nHTTPS 模式：%s\\n主机接入码：%s\\n' "$((SECONDS-CENTER_STARTED))" "$mode" "$registration_code"''',
    "completion duration",
)
path.write_text(text, encoding="utf-8")

path = ROOT / "tests/conformance.py"
text = path.read_text(encoding="utf-8")
anchor = '''    require('log {\\n    output discard\\n  }' in center, 'Caddy log 块没有使用规范多行语法')
    require('检查并升级 VVV' not in manager and 'update_vvv' not in manager, '仍保留原地升级兼容入口')'''
replacement = '''    require('log {\\n    output discard\\n  }' in center, 'Caddy log 块没有使用规范多行语法')
    require('systemctl reload caddy.service' not in center, 'admin off 模式仍错误调用 Caddy reload')
    require('ExecReload=/usr/local/bin/caddy reload' not in center, 'Caddy 服务仍配置依赖 admin API 的 reload')
    require('.vvv-ip-final-active' in center, 'IP 证书首次部署和续期部署没有使用状态标记分流')
    require('timeout 75 systemctl restart caddy.service' in center, 'IP 证书续期没有使用有界 Caddy 重启')
    require('跳过重复 apt update' in center, '订阅中心仍可能静默重复刷新软件源')
    require('继续安装订阅中心' in bootstrap and '当前 SSH 不受影响' in bootstrap, '代理安装后没有明确显示订阅中心进度')
    require('检查并升级 VVV' not in manager and 'update_vvv' not in manager, '仍保留原地升级兼容入口')'''
text = replace_once(text, anchor, replacement, "conformance caddy assertions")
path.write_text(text, encoding="utf-8")

runtime_test = r'''#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
CADDY="${1:?usage: https_frontend_validation.sh CADDY CERTBOT}"
CERTBOT="${2:?usage: https_frontend_validation.sh CADDY CERTBOT}"
WORK="$(mktemp -d /tmp/vvv-https-validation.XXXXXX)"
CADDY_PID=""
cleanup(){
  [[ -z "$CADDY_PID" ]] || kill "$CADDY_PID" >/dev/null 2>&1 || true
  [[ -z "$CADDY_PID" ]] || wait "$CADDY_PID" 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT

"$CADDY" version
"$CERTBOT" --version
"$CERTBOT" certonly --help all | grep -q -- '--ip-address'
"$CERTBOT" certonly --help all | grep -q -- '--preferred-profile'

cat > "$WORK/domain.Caddyfile" <<'EOF'
{
  admin off
  auto_https disable_redirects
}

sub.example.com:8443 {
  tls {
    issuer acme {
      disable_tlsalpn_challenge
    }
  }

  log {
    output discard
  }

  @allowed path /r/* /api/v1/* /health
  handle @allowed {
    reverse_proxy 127.0.0.1:18081
  }

  respond 404
}
EOF
"$CADDY" validate --config "$WORK/domain.Caddyfile" --adapter caddyfile

openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
  -sha256 -nodes -days 2 -subj '/CN=127.0.0.1' \
  -addext 'subjectAltName=IP:127.0.0.1' \
  -addext 'basicConstraints=critical,CA:FALSE' \
  -addext 'keyUsage=critical,digitalSignature' \
  -addext 'extendedKeyUsage=serverAuth' \
  -keyout "$WORK/ip.key" -out "$WORK/ip.crt" >/dev/null 2>&1

cat > "$WORK/ip.Caddyfile" <<EOF
{
  admin off
  auto_https off
  default_sni 127.0.0.1
}

http://127.0.0.1:18080 {
  respond /acme-ready "ready" 200
  respond 404
}

https://127.0.0.1:18443 {
  tls $WORK/ip.crt $WORK/ip.key
  respond /health "ok" 200
  respond 404
}
EOF
"$CADDY" validate --config "$WORK/ip.Caddyfile" --adapter caddyfile
"$CADDY" run --config "$WORK/ip.Caddyfile" --adapter caddyfile >"$WORK/caddy.log" 2>&1 &
CADDY_PID=$!
for _ in $(seq 1 30); do
  kill -0 "$CADDY_PID" 2>/dev/null || { cat "$WORK/caddy.log"; exit 1; }
  curl -fsS --connect-timeout 1 --max-time 2 http://127.0.0.1:18080/acme-ready | grep -qx ready && \
  curl -fsS --connect-timeout 1 --max-time 2 --cacert "$WORK/ip.crt" https://127.0.0.1:18443/health | grep -qx ok && break
  sleep 1
done
curl -fsS --connect-timeout 2 --max-time 4 http://127.0.0.1:18080/acme-ready | grep -qx ready
curl -fsS --connect-timeout 2 --max-time 4 --cacert "$WORK/ip.crt" https://127.0.0.1:18443/health | grep -qx ok
kill "$CADDY_PID"
wait "$CADDY_PID" 2>/dev/null || true
CADDY_PID=""

echo 'CADDY DOMAIN/IP CONFIGURATION AND RUNTIME VALIDATION PASSED'
echo 'CERTBOT IP CERTIFICATE FLAGS VALIDATION PASSED'
'''
(ROOT / "tests/https_frontend_validation.sh").write_text(runtime_test, encoding="utf-8")

path = ROOT / "README.md"
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    '''- 公网 IP 模式会安装隔离版 Certbot 5.4+，自动申请 Let’s Encrypt IP 证书；
- IP 证书有效期较短，脚本会创建 systemd 定时器，每天检查两次并自动续期；''',
    '''- 公网 IP 模式会安装隔离版 Certbot 5.4+，自动申请 Let’s Encrypt IP 证书；
- IP 证书有效期较短，脚本会创建 systemd 定时器，每天检查两次并自动续期；
- 证书首次部署不会错误调用 Caddy reload；续期后只重启 Caddy，不重启整台 VPS，也不会中断 SSH；
- 代理部分完成后会立即显示订阅中心的分阶段进度，依赖下载和证书申请均有明确提示与超时保护；''',
    "README progress and restart policy",
)
path.write_text(text, encoding="utf-8")

for shell_path in (
    ROOT / "core-src/bootstrap.sh",
    ROOT / "core-src/center_install.sh",
    ROOT / "tests/https_frontend_validation.sh",
):
    if "\r" in shell_path.read_text(encoding="utf-8"):
        raise SystemExit(f"CRLF not allowed: {shell_path}")
print("PATCH APPLIED")

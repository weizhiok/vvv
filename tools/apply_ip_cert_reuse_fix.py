#!/usr/bin/env python3
from pathlib import Path
import re

center_path = Path('core-src/center_install.sh')
center = center_path.read_text(encoding='utf-8')

old = 'CERTBOT_DIR=/opt/vvv-certbot\nCENTER_STARTED=$SECONDS\n'
new = 'CERTBOT_DIR=/opt/vvv-certbot\nRATE_LIMIT_FILE=/var/lib/vvv-certbot/ip-rate-limit-until\nCENTER_STARTED=$SECONDS\n'
if center.count(old) != 1:
    raise SystemExit('center constants target not found exactly once')
center = center.replace(old, new, 1)

replacement = r'''certificate_matches_ip(){
  local cert="$1" key="$2" cert_pub key_pub
  [[ -s "$cert" && -s "$key" ]] || return 1
  openssl x509 -in "$cert" -noout -checkend 21600 >/dev/null 2>&1 || return 1
  openssl x509 -in "$cert" -noout -ext subjectAltName 2>/dev/null | grep -Fq "IP Address:${public_ip}" || return 1
  cert_pub="$(openssl x509 -in "$cert" -pubkey -noout 2>/dev/null | openssl pkey -pubin -outform DER 2>/dev/null | sha256sum | awk '{print $1}')"
  key_pub="$(openssl pkey -in "$key" -pubout -outform DER 2>/dev/null | sha256sum | awk '{print $1}')"
  [[ -n "$cert_pub" && "$cert_pub" == "$key_pub" ]]
}
ensure_certbot(){
  if [[ -x "$CERTBOT_DIR/bin/certbot" ]] && timeout 15 "$CERTBOT_DIR/bin/certbot" --version >/dev/null 2>&1; then
    echo "检测到现有 Certbot 环境，直接复用。"
    return 0
  fi
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
}
remember_rate_limit(){
  local log="$1" retry_text epoch
  retry_text="$(sed -nE 's/.*retry after ([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} UTC).*/\1/p' "$log" | tail -n1)"
  [[ -n "$retry_text" ]] || return 1
  epoch="$(date -u -d "$retry_text" +%s 2>/dev/null || true)"
  [[ "$epoch" =~ ^[0-9]+$ ]] || return 1
  install -d -m700 "$(dirname "$RATE_LIMIT_FILE")"
  printf '%s\n' "$epoch" > "$RATE_LIMIT_FILE"
  chmod 600 "$RATE_LIMIT_FILE"
}
fail_if_rate_limited(){
  local until now utc cn jp
  [[ -s "$RATE_LIMIT_FILE" ]] || return 0
  until="$(head -n1 "$RATE_LIMIT_FILE" 2>/dev/null || true)"
  [[ "$until" =~ ^[0-9]+$ ]] || { rm -f "$RATE_LIMIT_FILE"; return 0; }
  now="$(date -u +%s)"
  if (( now >= until )); then
    rm -f "$RATE_LIMIT_FILE"
    return 0
  fi
  utc="$(date -u -d "@${until}" '+%F %T UTC')"
  cn="$(TZ=Asia/Shanghai date -d "@${until}" '+%F %T')"
  jp="$(TZ=Asia/Tokyo date -d "@${until}" '+%F %T')"
  fail "Let’s Encrypt 已限制该公网 IP 的重复签发。最早可在 ${utc}（北京时间 ${cn}，日本时间 ${jp}）后重试；在此之前重复运行不会提前解除限制。也可以重新安装时填写已解析到本机的订阅域名。"
}
install_ip_certificate(){
  local cert_name live_dir request_log
  cert_name="vvv-ip-${public_ip//./-}"
  live_dir="/etc/letsencrypt/live/${cert_name}"

  ensure_certbot

  if certificate_matches_ip "$live_dir/fullchain.pem" "$live_dir/privkey.pem"; then
    echo "检测到本机已有且至少 6 小时内有效的公网 IP HTTPS 证书，直接复用，不向 Let’s Encrypt 重复申请。"
  else
    if [[ ! -s "$RATE_LIMIT_FILE" && -r /var/log/letsencrypt/letsencrypt.log ]] && \
       grep -Eqi 'too many certificates|retry after' /var/log/letsencrypt/letsencrypt.log; then
      remember_rate_limit /var/log/letsencrypt/letsencrypt.log || true
    fi
    fail_if_rate_limited

    request_log="$(mktemp /tmp/vvv-certbot-request.XXXXXX)"
    echo "正在向 Let’s Encrypt 申请公网 IP 短期证书；最多等待 10 分钟……"
    if timeout 600 "$CERTBOT_DIR/bin/certbot" certonly \
      --non-interactive \
      --agree-tos \
      --register-unsafely-without-email \
      --preferred-profile shortlived \
      --webroot \
      --webroot-path "$ACME_WEBROOT" \
      --ip-address "$public_ip" \
      --cert-name "$cert_name" \
      --key-type ecdsa \
      --elliptic-curve secp256r1 \
      --keep-until-expiring \
      --reuse-key 2>&1 | tee "$request_log"; then
      :
    elif certificate_matches_ip "$live_dir/fullchain.pem" "$live_dir/privkey.pem"; then
      echo "Certbot 返回了非零状态，但已生成可用证书，继续完成部署。"
    elif grep -Eqi 'too many certificates|retry after' "$request_log"; then
      remember_rate_limit "$request_log" || true
      rm -f "$request_log"
      fail_if_rate_limited
      fail "Let’s Encrypt 拒绝重复签发该公网 IP 证书；请稍后重试或使用订阅域名。"
    else
      rm -f "$request_log"
      fail "公网 IP HTTPS 证书申请失败或超时。"
    fi
    rm -f "$request_log"
  fi

  certificate_matches_ip "$live_dir/fullchain.pem" "$live_dir/privkey.pem" || fail "Certbot 没有生成与本机公网 IP 匹配的有效证书和私钥。"
  rm -f "$RATE_LIMIT_FILE"

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

pattern = r'install_ip_certificate\(\)\{.*?\n\}\n\n\[\[ \$\(id -u\) -eq 0 \]\]'
match = re.search(pattern, center, flags=re.S)
if not match:
    raise SystemExit('install_ip_certificate function target not found')
center = center[:match.start()] + replacement + '\n[[ $(id -u) -eq 0 ]]' + center[match.end():]
center_path.write_text(center, encoding='utf-8')

# Permanent regression checks.
test_path = Path('tests/conformance.py')
test = test_path.read_text(encoding='utf-8')
needle = "    for token in ('--preferred-profile shortlived', '--ip-address \"$public_ip\"', 'vvv-ip-cert-renew.timer', 'deploy-ip-cert.sh'):\n        require(token in center, f'IP 证书申请或续期缺少：{token}')\n"
insert = needle + "    for token in ('certificate_matches_ip', 'openssl x509 -in \"$cert\" -noout -checkend 21600', '--keep-until-expiring', '--reuse-key', 'RATE_LIMIT_FILE', 'remember_rate_limit', 'fail_if_rate_limited'):\n        require(token in center, f'IP 证书复用或限额保护缺少：{token}')\n    require('python3 -m venv --clear \"$CERTBOT_DIR\"' in center and '检测到现有 Certbot 环境，直接复用' in center, 'Certbot 环境不能安全复用')\n    require('直接复用，不向 Let’s Encrypt 重复申请' in center, '重复安装仍会无条件申请新 IP 证书')\n    require('too many certificates|retry after' in center and '日本时间' in center, '证书限额没有给出明确解封时间')\n"
if test.count(needle) != 1:
    raise SystemExit('conformance certificate target not found exactly once')
test_path.write_text(test.replace(needle, insert, 1), encoding='utf-8')

print('IP CERTIFICATE REUSE AND RATE-LIMIT PATCH APPLIED')

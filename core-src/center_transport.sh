#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

CFG=/etc/vvv-sub/config.json
CADDYFILE=/etc/caddy/Caddyfile
CADDY_CERT_DIR=/etc/caddy/certs
ACME_WEBROOT=/var/www/vvv-acme
CERTBOT_DIR=/opt/vvv-certbot
CLOUDFLARED_TOKEN_FILE=/etc/vvv-sub/cloudflared.token
BACKUP=/usr/local/lib/vvv/backup_manager.py

fail(){ echo "错误：$*" >&2; exit 1; }
section(){ printf '\n========== %s ==========\n' "$*"; }
value(){ jq -r "$1" "$CFG"; }
backup_event(){
  local reason="$1"
  [[ -x "$BACKUP" && -f "$CFG" ]] || return 0
  python3 "$BACKUP" create "$reason" --force >/dev/null || echo "警告：自动备份失败。" >&2
}
open_port(){
  local port="$1"
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then ufw allow "${port}/tcp" >/dev/null; fi
  if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    firewall-cmd --permanent --add-port="${port}/tcp" >/dev/null
    firewall-cmd --reload >/dev/null
  fi
}
close_port(){
  local port="$1"
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then ufw delete allow "${port}/tcp" >/dev/null 2>&1 || true; fi
  if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    firewall-cmd --permanent --remove-port="${port}/tcp" >/dev/null 2>&1 || true
    firewall-cmd --reload >/dev/null 2>&1 || true
  fi
}
ensure_service(){
  local service="$1" action="${2:-restart}" wait="${3:-75}"
  systemctl enable "$service" >/dev/null 2>&1 || true
  systemctl reset-failed "$service" >/dev/null 2>&1 || true
  timeout "$wait" systemctl "$action" "$service" || true
  for _ in $(seq 1 20); do
    systemctl is-active --quiet "$service" && return 0
    sleep 1
  done
  systemctl --no-pager --full status "$service" >&2 || true
  journalctl -u "$service" -n100 --no-pager >&2 || true
  return 1
}
validate_caddy(){
  /usr/local/bin/caddy fmt --overwrite "$CADDYFILE" >/dev/null
  chown root:caddy "$CADDYFILE"
  chmod 640 "$CADDYFILE"
  runuser -u caddy -- /usr/local/bin/caddy validate --config "$CADDYFILE" --adapter caddyfile
}
write_http_caddyfile(){
  local port suffix
  port="$(value '.public_port')"; suffix="$(value '.subscription_suffix')"
  cat > "$CADDYFILE" <<EOF
{
  admin off
  auto_https off
}

:${port} {
  log {
    output discard
  }

  @allowed path /${suffix} /api/v1/* /health
  handle @allowed {
    reverse_proxy 127.0.0.1:18081
  }

  respond 404
}
EOF
}
write_tunnel_caddyfile(){
  local port suffix
  port="$(value '.public_port')"; suffix="$(value '.subscription_suffix')"
  cat > "$CADDYFILE" <<EOF
{
  admin off
  auto_https off
}

http://127.0.0.1:${port} {
  log {
    output discard
  }

  @allowed path /${suffix} /api/v1/* /health
  handle @allowed {
    reverse_proxy 127.0.0.1:18081
  }

  respond 404
}
EOF
}
write_domain_https_caddyfile(){
  local domain port suffix
  domain="$(value '.domain')"; port="$(value '.public_port')"; suffix="$(value '.subscription_suffix')"
  cat > "$CADDYFILE" <<EOF
{
  admin off
  auto_https disable_redirects
}

${domain}:${port} {
  tls {
    issuer acme {
      disable_tlsalpn_challenge
    }
  }

  log {
    output discard
  }

  @allowed path /${suffix} /api/v1/* /health
  handle @allowed {
    reverse_proxy 127.0.0.1:18081
  }

  respond 404
}
EOF
}
write_ip_bootstrap_caddyfile(){
  cat > "$CADDYFILE" <<EOF
{
  admin off
  auto_https off
}

:80 {
  root * ${ACME_WEBROOT}
  file_server
  log {
    output discard
  }
}
EOF
}
write_ip_https_caddyfile(){
  local ip port suffix
  ip="$(value '.public_ip')"; port="$(value '.public_port')"; suffix="$(value '.subscription_suffix')"
  cat > "$CADDYFILE" <<EOF
{
  admin off
  auto_https off
  default_sni ${ip}
}

:80 {
  root * ${ACME_WEBROOT}
  @acme_challenge path /.well-known/acme-challenge/*
  handle @acme_challenge {
    file_server
  }
  respond 404
  log {
    output discard
  }
}

https://${ip}:${port} {
  tls ${CADDY_CERT_DIR}/ip-fullchain.pem ${CADDY_CERT_DIR}/ip-privkey.pem
  log {
    output discard
  }
  @allowed path /${suffix} /api/v1/* /health
  handle @allowed {
    reverse_proxy 127.0.0.1:18081
  }
  respond 404
}
EOF
}
install_certbot(){
  if [[ -x "$CERTBOT_DIR/bin/certbot" ]] && "$CERTBOT_DIR/bin/certbot" --version 2>/dev/null | grep -Eq 'certbot (5\.[4-9]|[6-9]|[1-9][0-9])'; then
    return 0
  fi
  python3 -m venv --clear "$CERTBOT_DIR"
  timeout 600 "$CERTBOT_DIR/bin/pip" install --disable-pip-version-check --no-cache-dir --retries 5 --timeout 30 'certbot>=5.4,<6'
}
valid_ip_cert_files(){
  local cert="$1" key="$2" ip cert_pub key_pub
  ip="$(value '.public_ip')"
  [[ -s "$cert" && -s "$key" ]] || return 1
  openssl x509 -checkend 43200 -noout -in "$cert" >/dev/null 2>&1 || return 1
  openssl x509 -in "$cert" -noout -ext subjectAltName 2>/dev/null | grep -Fq "IP Address:${ip}" || return 1
  cert_pub="$(openssl x509 -in "$cert" -pubkey -noout 2>/dev/null | openssl pkey -pubin -outform DER 2>/dev/null | sha256sum | awk '{print $1}')"
  key_pub="$(openssl pkey -in "$key" -pubout -outform DER 2>/dev/null | sha256sum | awk '{print $1}')"
  [[ -n "$cert_pub" && "$cert_pub" == "$key_pub" ]]
}
valid_ip_certificate(){
  valid_ip_cert_files "${CADDY_CERT_DIR}/ip-fullchain.pem" "${CADDY_CERT_DIR}/ip-privkey.pem"
}
write_ip_deploy_hook(){
  local cert_name live_dir
  cert_name="vvv-ip-$(value '.public_ip' | tr . -)"
  live_dir="/etc/letsencrypt/live/${cert_name}"
  cat > /usr/local/lib/vvv/deploy-ip-cert.sh <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
install -d -o caddy -g caddy -m700 ${CADDY_CERT_DIR}
install -o caddy -g caddy -m600 ${live_dir}/fullchain.pem ${CADDY_CERT_DIR}/ip-fullchain.pem
install -o caddy -g caddy -m600 ${live_dir}/privkey.pem ${CADDY_CERT_DIR}/ip-privkey.pem
if systemctl is-active --quiet caddy.service; then timeout 75 systemctl restart caddy.service; fi
if [[ -f /etc/vvv-sub/cloud.json ]]; then python3 /usr/local/lib/vvv/backup_manager.py create certificate-renewed --force >/dev/null || true; fi
EOF
  chmod 700 /usr/local/lib/vvv/deploy-ip-cert.sh
}
write_ip_renew_units(){
  local cert_name
  cert_name="vvv-ip-$(value '.public_ip' | tr . -)"
  cat > /etc/systemd/system/vvv-ip-cert-renew.service <<EOF
[Unit]
Description=Renew VVV Let's Encrypt IP certificate
After=network-online.target caddy.service
Wants=network-online.target
ConditionPathExists=/etc/letsencrypt/renewal/${cert_name}.conf
[Service]
Type=oneshot
TimeoutStartSec=15min
ExecStart=${CERTBOT_DIR}/bin/certbot renew --quiet --cert-name ${cert_name} --deploy-hook /usr/local/lib/vvv/deploy-ip-cert.sh
EOF
  cat > /etc/systemd/system/vvv-ip-cert-renew.timer <<'EOF'
[Unit]
Description=Check VVV IP certificate twice daily
[Timer]
OnCalendar=*-*-* 00,12:17:00
RandomizedDelaySec=30m
Persistent=true
[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now vvv-ip-cert-renew.timer >/dev/null
}
obtain_ip_certificate(){
  local ip cert_name live_dir log_file
  ip="$(value '.public_ip')"; cert_name="vvv-ip-${ip//./-}"; live_dir="/etc/letsencrypt/live/${cert_name}"
  if valid_ip_certificate; then
    echo "检测到仍有效且匹配当前 IP 的已部署证书，直接复用。"
    install_certbot
    write_ip_deploy_hook
    write_ip_renew_units
    return 0
  fi
  if valid_ip_cert_files "$live_dir/fullchain.pem" "$live_dir/privkey.pem"; then
    echo "检测到仍有效的 Certbot IP 证书 lineage，正在恢复部署并重建续期服务。"
    install_certbot
    write_ip_deploy_hook
    /usr/local/lib/vvv/deploy-ip-cert.sh
    write_ip_renew_units
    return 0
  fi
  install_certbot
  install -d -m755 "$ACME_WEBROOT/.well-known/acme-challenge"
  write_ip_bootstrap_caddyfile
  validate_caddy
  ensure_service caddy.service restart 75 || return 1
  log_file="$(mktemp /tmp/vvv-certbot.XXXXXX)"
  echo "正在向 Let's Encrypt 申请公网 IP 短期证书……"
  if ! timeout 600 "$CERTBOT_DIR/bin/certbot" certonly \
      --non-interactive --agree-tos --register-unsafely-without-email \
      --preferred-profile shortlived --webroot --webroot-path "$ACME_WEBROOT" \
      --ip-address "$ip" --cert-name "$cert_name" --key-type ecdsa --elliptic-curve secp256r1 \
      2>&1 | tee "$log_file"; then
    if grep -q 'too many certificates' "$log_file"; then
      grep -oE 'retry after [^:]+' "$log_file" | head -n1 > /etc/vvv-sub/certificate-rate-limit.txt || true
      echo "Let's Encrypt 已触发相同 IP 证书签发限制。可先继续使用 HTTP，等待官方提示时间后再开启 HTTPS。" >&2
    fi
    rm -f "$log_file"
    return 1
  fi
  rm -f "$log_file"
  [[ -s "$live_dir/fullchain.pem" && -s "$live_dir/privkey.pem" ]] || return 1
  write_ip_deploy_hook
  /usr/local/lib/vvv/deploy-ip-cert.sh
  valid_ip_certificate
  write_ip_renew_units
}
install_cloudflared(){
  command -v cloudflared >/dev/null 2>&1 && return 0
  local arch api name url digest tmp actual
  case "$(uname -m)" in x86_64|amd64) arch=amd64;; aarch64|arm64) arch=arm64;; *) fail "cloudflared 不支持当前架构。";; esac
  api="$(curl -fsSL --retry 5 --retry-all-errors https://api.github.com/repos/cloudflare/cloudflared/releases/latest)"
  name="cloudflared-linux-${arch}"
  url="$(jq -r --arg n "$name" '.assets[]|select(.name==$n)|.browser_download_url' <<<"$api" | head -n1)"
  digest="$(jq -r --arg n "$name" '.assets[]|select(.name==$n)|(.digest // "")' <<<"$api" | head -n1)"
  [[ -n "$url" && "$url" != null ]] || fail "找不到 cloudflared 安装包。"
  tmp="$(mktemp)"
  curl -fL --retry 5 --retry-all-errors --connect-timeout 15 --max-time 300 "$url" -o "$tmp"
  if [[ "$digest" =~ ^sha256:[0-9a-fA-F]{64}$ ]]; then
    actual="$(sha256sum "$tmp" | awk '{print $1}')"
    [[ "${digest#sha256:}" == "$actual" ]] || fail "cloudflared SHA-256 校验失败。"
  fi
  install -m755 "$tmp" /usr/local/bin/cloudflared
  rm -f "$tmp"
}
write_cloudflared_service(){
  [[ -s "$CLOUDFLARED_TOKEN_FILE" ]] || fail "Cloudflare Tunnel Token 不存在。"
  cat > /usr/local/lib/vvv/run-cloudflared.sh <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
token="$(cat /etc/vvv-sub/cloudflared.token)"
exec /usr/local/bin/cloudflared tunnel --no-autoupdate run --token "$token"
EOF
  chmod 700 /usr/local/lib/vvv/run-cloudflared.sh
  cat > /etc/systemd/system/vvv-cloudflared.service <<'EOF'
[Unit]
Description=VVV Cloudflare Tunnel
After=network-online.target caddy.service
Wants=network-online.target
[Service]
Type=simple
ExecStart=/usr/local/lib/vvv/run-cloudflared.sh
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadOnlyPaths=/etc/vvv-sub/cloudflared.token
[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
}
base_for(){
  local mode domain ip port
  mode="$1"; domain="$(value '.domain')"; ip="$(value '.public_ip')"; port="$(value '.public_port')"
  case "$mode" in
    direct-http)
      [[ -n "$domain" ]] && printf 'http://%s:%s' "$domain" "$port" || printf 'http://%s:%s' "$ip" "$port"
      ;;
    direct-https)
      [[ -n "$domain" ]] && printf 'https://%s:%s' "$domain" "$port" || printf 'https://%s:%s' "$ip" "$port"
      ;;
    tunnel) printf 'https://%s' "$domain";;
    *) return 1;;
  esac
}
update_config_urls(){
  local mode="$1" base suffix tmp
  base="$(base_for "$mode")"; suffix="$(value '.subscription_suffix')"; tmp="$(mktemp)"
  jq --arg mode "$mode" --arg base "$base" --arg url "${base}/${suffix}" \
    '.schema=3 | .transport_mode=$mode | .base_url=$base | .subscription_url=$url | .updated_at=(now|todate)' \
    "$CFG" > "$tmp"
  install -m600 "$tmp" "$CFG"; rm -f "$tmp"
  rewrite_registration_artifacts
}
rewrite_registration_artifacts(){
  local base master registration_json registration_code
  base="$(value '.base_url')"; master="$(value '.master_token')"
  registration_json="$(jq -nc --arg base "$base" --arg token "$master" '{base_url:$base,master_token:$token}')"
  registration_code="VVV1.$(printf %s "$registration_json" | base64 -w0 | tr '+/' '-_' | tr -d '=')"
  printf '%s' "$registration_code" > /etc/vvv-sub/registration.code
  chmod 600 /etc/vvv-sub/registration.code
  cat > /root/VVV-订阅中心恢复信息.txt <<EOF
VVV 订阅中心恢复信息
====================
订阅中心：$(value '.base_url')
统一订阅地址：$(value '.subscription_url')
传输模式：$(value '.transport_mode')
订阅后缀：$(value '.subscription_suffix')
订阅端口：$(value '.public_port')
主机接入码：${registration_code}
备份解密密码：$(value '.recovery_password')
本地备份目录：/var/lib/vvv-sub/backups
云备份：$([[ -f /etc/vvv-sub/cloud.json ]] && echo 已开启 || echo 默认关闭)
EOF
  chmod 600 /root/VVV-订阅中心恢复信息.txt
}
check_public_once(){
  local mode base suffix
  mode="$(value '.transport_mode')"; base="$(value '.base_url')"; suffix="$(value '.subscription_suffix')"
  if [[ "$mode" == direct-http ]]; then
    curl -fsS --connect-timeout 3 --max-time 8 -H 'User-Agent: Clash-Verge-Rev' "${base}/${suffix}" >/dev/null
  elif [[ "$mode" == direct-https && -z "$(value '.domain')" ]]; then
    curl -fsS --connect-timeout 3 --max-time 8 --connect-to "$(value '.public_ip'):$(value '.public_port'):127.0.0.1:$(value '.public_port')" \
      -H 'User-Agent: Clash-Verge-Rev' "${base}/${suffix}" >/dev/null
  else
    curl -fsS --connect-timeout 5 --max-time 15 -H 'User-Agent: Clash-Verge-Rev' "${base}/${suffix}" >/dev/null
  fi
}
check_public(){
  local attempt
  for attempt in $(seq 1 120); do
    check_public_once && return 0
    (( attempt % 10 != 0 )) || echo "统一订阅入口仍在准备：已等待 $((attempt*2)) 秒……"
    sleep 2
  done
  echo "统一订阅入口在 240 秒内未通过健康检查。" >&2
  return 1
}
apply_direct_http(){
  open_port "$(value '.public_port')"
  write_http_caddyfile
  validate_caddy
  ensure_service caddy.service restart 75
  systemctl disable --now vvv-cloudflared.service >/dev/null 2>&1 || true
  systemctl disable --now vvv-ip-cert-renew.timer >/dev/null 2>&1 || true
  update_config_urls direct-http
  check_public
}
apply_direct_https(){
  open_port "$(value '.public_port')"
  if [[ -n "$(value '.domain')" ]]; then
    open_port 80
    write_domain_https_caddyfile
    validate_caddy
    ensure_service caddy.service restart 75
  else
    open_port 80
    obtain_ip_certificate
    write_ip_https_caddyfile
    validate_caddy
    ensure_service caddy.service restart 75
  fi
  systemctl disable --now vvv-cloudflared.service >/dev/null 2>&1 || true
  update_config_urls direct-https
  check_public
}
apply_tunnel(){
  [[ -n "$(value '.domain')" ]] || fail "Cloudflare Tunnel 模式必须配置订阅域名。"
  install_cloudflared
  write_tunnel_caddyfile
  validate_caddy
  ensure_service caddy.service restart 75
  write_cloudflared_service
  ensure_service vvv-cloudflared.service restart 75
  close_port "$(value '.public_port')"
  systemctl disable --now vvv-ip-cert-renew.timer >/dev/null 2>&1 || true
  update_config_urls tunnel
  echo "正在验证 Cloudflare 公共主机名。该主机名必须已在 Cloudflare Tunnel 中指向 http://127.0.0.1:$(value '.public_port')。"
  check_public
}
transaction(){
  local action="$1" tmp cfg_backup caddy_backup
  tmp="$(mktemp -d /tmp/vvv-transport.XXXXXX)"; cfg_backup="$tmp/config.json"; caddy_backup="$tmp/Caddyfile"
  cp -a "$CFG" "$cfg_backup"
  [[ ! -f "$CADDYFILE" ]] || cp -a "$CADDYFILE" "$caddy_backup"
  backup_event before-transport-change
  if "$action"; then
    backup_event after-transport-change
    rm -rf "$tmp"
    return 0
  fi
  echo "传输方式切换失败，正在恢复原配置……" >&2
  install -m600 "$cfg_backup" "$CFG"
  if [[ -f "$caddy_backup" ]]; then install -m640 -o root -g caddy "$caddy_backup" "$CADDYFILE"; fi
  systemctl daemon-reload
  systemctl restart caddy.service >/dev/null 2>&1 || true
  rm -rf "$tmp"
  return 1
}
apply_initial(){
  local mode
  mode="$(value '.transport_mode')"
  case "$mode" in
    direct-http) apply_direct_http;;
    direct-https) apply_direct_https;;
    tunnel) apply_tunnel;;
    *) fail "未知订阅传输模式：$mode";;
  esac
}
enable_https(){
  [[ "$(value '.transport_mode')" == direct-http ]] || fail "当前不是 HTTP 模式，不能执行此操作。"
  transaction apply_direct_https || fail "HTTPS 开启失败，原 HTTP 配置已恢复并继续可用。"
  echo "HTTPS 已开启；原 HTTP 订阅入口已失效。"
  echo "统一订阅地址：$(value '.subscription_url')"
}
change_suffix(){
  local suffix="$1" old tmp
  [[ "$suffix" =~ ^[A-Za-z0-9]{6,32}$ ]] || fail "订阅后缀必须是 6-32 位大小写字母或数字。"
  case "${suffix,,}" in health|api|admin|debug) fail "该后缀属于系统保留词。";; esac
  old="$(value '.subscription_suffix')"; [[ "$suffix" != "$old" ]] || { echo "订阅后缀没有变化。"; return 0; }
  tmp="$(mktemp)"; jq --arg suffix "$suffix" '.subscription_suffix=$suffix' "$CFG" > "$tmp"; install -m600 "$tmp" "$CFG"; rm -f "$tmp"
  if ! transaction reapply_current; then
    tmp="$(mktemp)"; jq --arg suffix "$old" '.subscription_suffix=$suffix' "$CFG" > "$tmp"; install -m600 "$tmp" "$CFG"; rm -f "$tmp"
    fail "订阅后缀修改失败，已恢复原后缀。"
  fi
  echo "订阅后缀修改成功，原订阅地址立即失效。"
  echo "新统一订阅地址：$(value '.subscription_url')"
}
reapply_current(){
  case "$(value '.transport_mode')" in
    direct-http) apply_direct_http;;
    direct-https) apply_direct_https;;
    tunnel) apply_tunnel;;
    *) return 1;;
  esac
}
status(){
  echo "传输模式：$(value '.transport_mode')"
  echo "统一订阅地址：$(value '.subscription_url')"
  echo "订阅后缀：$(value '.subscription_suffix')"
  systemctl --no-pager --full status caddy.service vvv-sub.service 2>/dev/null || true
  [[ "$(value '.transport_mode')" != tunnel ]] || systemctl --no-pager --full status vvv-cloudflared.service 2>/dev/null || true
  [[ ! -f /etc/vvv-sub/certificate-rate-limit.txt ]] || { echo "最近证书限额提示："; cat /etc/vvv-sub/certificate-rate-limit.txt; }
}

[[ $(id -u) -eq 0 ]] || fail "请使用 root 用户运行。"
[[ -s "$CFG" ]] || fail "订阅中心配置不存在。"
case "${1:-status}" in
  apply-initial) apply_initial;;
  enable-https) enable_https;;
  change-suffix) change_suffix "${2:-}";;
  reapply) transaction reapply_current;;
  status) status;;
  *) echo "用法：$0 apply-initial|enable-https|change-suffix 后缀|reapply|status" >&2; exit 2;;
esac

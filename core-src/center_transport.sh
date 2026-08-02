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

  @allowed path /${suffix} /health
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

  @allowed path /${suffix} /health
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

  @allowed path /${suffix} /health
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
  @allowed path /${suffix} /health
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
      echo "Let's Encrypt 已触发相同 IP 证书签发限制，请等待官方提示时间后重试。" >&2
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
    direct-http) [[ -n "$domain" ]] && printf 'http://%s:%s' "$domain" "$port" || printf 'http://%s:%s' "$ip" "$port";;
    direct-https) [[ -n "$domain" ]] && printf 'https://%s:%s' "$domain" "$port" || printf 'https://%s:%s' "$ip" "$port";;
    tunnel) printf 'https://%s' "$domain";;
    *) return 1;;
  esac
}

atomic_jq(){
  local filter="$1" tmp
  shift
  tmp="$(mktemp)"
  jq "$@" "$filter" "$CFG" > "$tmp"
  install -m600 "$tmp" "$CFG"
  rm -f "$tmp"
}

update_config_urls(){
  local mode="$1" base suffix api
  base="$(base_for "$mode")"; suffix="$(value '.subscription_suffix')"
  api="http://$(value '.public_ip'):$(value '.listen_port')"
  atomic_jq '.schema=4 | .transport_mode=$mode | .base_url=$base | .subscription_url=$url | .api_base_url=$api | .updated_at=(now|todate)' \
    --arg mode "$mode" --arg base "$base" --arg url "${base}/${suffix}" --arg api "$api"
  rewrite_registration_artifacts
}

rewrite_registration_artifacts(){
  local api master registration_code
  api="$(value '.api_base_url')"; master="$(value '.master_token')"
  registration_code="$(python3 - "$api" "$master" <<'PY_VVC1'
import base64,hashlib,json,sys
api,master=sys.argv[1:]
obj={'schema':1,'type':'vvv-subscription-center','api_base_url':api,'master_token':master}
raw=json.dumps(obj,ensure_ascii=False,separators=(',',':')).encode()
enc=base64.urlsafe_b64encode(raw).decode().rstrip('=')
digest=hashlib.sha256(b'VVV-VVC1\0'+raw).hexdigest()[:20]
print(f'VVC1.{enc}.{digest}')
PY_VVC1
)"
  printf '%s' "$registration_code" > /etc/vvv-sub/registration.code
  chmod 600 /etc/vvv-sub/registration.code
  cat > /root/VVV-订阅中心恢复信息.txt <<EOF
VVV 订阅中心信息
=================
统一订阅地址：$(value '.subscription_url')
副机 API（固定 IP 通讯）：${api}
传输模式：$(value '.transport_mode')
订阅后缀：$(value '.subscription_suffix')
订阅端口：$(value '.public_port')
订阅中心对接码：${registration_code}
本地备份目录：/var/lib/vvv-sub/backups
云备份固定目录：vvv/
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
  write_http_caddyfile; validate_caddy; ensure_service caddy.service restart 75
  systemctl disable --now vvv-cloudflared.service >/dev/null 2>&1 || true
  systemctl disable --now vvv-ip-cert-renew.timer >/dev/null 2>&1 || true
  update_config_urls direct-http
  check_public
}

apply_direct_https(){
  open_port "$(value '.public_port')"; open_port 80
  if [[ -n "$(value '.domain')" ]]; then
    write_domain_https_caddyfile; validate_caddy; ensure_service caddy.service restart 75
  else
    obtain_ip_certificate
    write_ip_https_caddyfile; validate_caddy; ensure_service caddy.service restart 75
  fi
  systemctl disable --now vvv-cloudflared.service >/dev/null 2>&1 || true
  update_config_urls direct-https
  check_public
}

apply_tunnel(){
  [[ -n "$(value '.domain')" ]] || fail "Cloudflare Tunnel 模式必须配置订阅域名。"
  install_cloudflared
  write_tunnel_caddyfile; validate_caddy; ensure_service caddy.service restart 75
  write_cloudflared_service; ensure_service vvv-cloudflared.service restart 75
  close_port "$(value '.public_port')"
  systemctl disable --now vvv-ip-cert-renew.timer >/dev/null 2>&1 || true
  update_config_urls tunnel
  echo "正在验证 Cloudflare 公共主机名；源站应指向 http://127.0.0.1:$(value '.public_port')。"
  check_public
}

reapply_current_no_transaction(){
  open_port "$(value '.listen_port')"
  case "$(value '.transport_mode')" in
    direct-http) apply_direct_http;;
    direct-https) apply_direct_https;;
    tunnel) apply_tunnel;;
    *) return 1;;
  esac
}

transaction(){
  local action="$1" tmp rc=0
  shift
  tmp="$(mktemp -d /tmp/vvv-transport.XXXXXX)"
  cp -a "$CFG" "$tmp/config.json"
  [[ ! -f "$CADDYFILE" ]] || cp -a "$CADDYFILE" "$tmp/Caddyfile"
  [[ ! -f "$CLOUDFLARED_TOKEN_FILE" ]] || cp -a "$CLOUDFLARED_TOKEN_FILE" "$tmp/cloudflared.token"
  backup_event before-transport-change
  "$action" "$@" || rc=$?
  if (( rc==0 )); then
    backup_event after-transport-change
    rm -rf "$tmp"
    return 0
  fi
  echo "订阅入口修改失败，正在恢复原配置……" >&2
  install -m600 "$tmp/config.json" "$CFG"
  [[ ! -f "$tmp/Caddyfile" ]] || install -m640 -o root -g caddy "$tmp/Caddyfile" "$CADDYFILE"
  if [[ -f "$tmp/cloudflared.token" ]]; then install -m600 "$tmp/cloudflared.token" "$CLOUDFLARED_TOKEN_FILE"; else rm -f "$CLOUDFLARED_TOKEN_FILE"; fi
  systemctl daemon-reload
  reapply_current_no_transaction >/dev/null 2>&1 || true
  rm -rf "$tmp"
  return "$rc"
}

apply_initial(){ reapply_current_no_transaction; }

valid_suffix(){
  [[ "$1" =~ ^[A-Za-z0-9]{6,32}$ ]] || return 1
  case "${1,,}" in health|api|admin|debug) return 1;; esac
}

change_suffix_impl(){
  local suffix="$1"
  valid_suffix "$suffix" || fail "订阅后缀必须是 6-32 位大小写字母或数字，且不能使用保留词。"
  atomic_jq '.subscription_suffix=$suffix' --arg suffix "$suffix"
  reapply_current_no_transaction
}
change_suffix(){ transaction change_suffix_impl "$1" || fail "订阅后缀修改失败，原配置已恢复。"; echo "新订阅地址：$(value '.subscription_url')"; }

validate_domain_for_direct(){
  local domain="$1" ip
  [[ -z "$domain" ]] && return 0
  [[ "$domain" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]] || return 1
  ip="$(value '.public_ip')"
  getent ahostsv4 "$domain" | awk '{print $1}' | sort -u | grep -Fxq "$ip"
}

change_domain_impl(){
  local domain="${1,,}" mode
  domain="${domain%.}"; mode="$(value '.transport_mode')"
  [[ "$mode" != tunnel || -n "$domain" ]] || fail "Tunnel 模式的订阅域名不能为空。"
  if [[ "$mode" == direct-https ]]; then validate_domain_for_direct "$domain" || fail "域名格式错误，或 A 记录尚未指向本机 IP $(value '.public_ip')。"; fi
  [[ -z "$domain" || "$domain" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]] || fail "域名格式错误。"
  atomic_jq '.domain=$domain | .address_mode=(if $domain=="" then "ip" else "domain" end)' --arg domain "$domain"
  reapply_current_no_transaction
}
change_domain(){ transaction change_domain_impl "$1" || fail "订阅域名修改失败，原配置已恢复。"; echo "新订阅地址：$(value '.subscription_url')"; }

change_port_impl(){
  local port="$1" old mode
  [[ "$port" =~ ^[0-9]+$ ]] && ((10#$port>=1 && 10#$port<=65535)) || fail "端口必须是 1-65535 的数字。"
  port="$((10#$port))"; mode="$(value '.transport_mode')"; old="$(value '.public_port')"
  [[ "$mode" != tunnel ]] || fail "Tunnel 公网端口固定为 443；无需修改本地源站端口。"
  [[ "$port" != "$(jq -r '.listen_port // 0' /etc/jp-relay/state.json 2>/dev/null || echo 0)" ]] || fail "订阅端口不能与代理端口相同。"
  atomic_jq '.public_port=$port' --argjson port "$port"
  reapply_current_no_transaction
  [[ "$old" == "$port" ]] || close_port "$old"
}
change_port(){ transaction change_port_impl "$1" || fail "订阅端口修改失败，原配置已恢复。"; echo "新订阅地址：$(value '.subscription_url')"; }

change_tunnel_token_impl(){
  local token="$1"
  [[ "$(value '.transport_mode')" == tunnel ]] || fail "当前不是 Tunnel 模式。"
  [[ -n "$token" ]] || fail "Tunnel Token 不能为空。"
  printf '%s' "$token" > "$CLOUDFLARED_TOKEN_FILE"; chmod 600 "$CLOUDFLARED_TOKEN_FILE"
  reapply_current_no_transaction
}
change_tunnel_token(){ transaction change_tunnel_token_impl "$1" || fail "Tunnel Token 修改失败，原配置已恢复。"; echo "Tunnel Token 修改成功。"; }

switch_to_tunnel_impl(){
  local domain="$1" token="$2"
  [[ "$(value '.transport_mode')" == direct-https ]] || fail "只有直接 HTTPS 可以切换到 Tunnel。"
  [[ "$domain" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]] || fail "Tunnel 域名格式错误。"
  [[ -n "$token" ]] || fail "Tunnel Token 不能为空。"
  printf '%s' "$token" > "$CLOUDFLARED_TOKEN_FILE"; chmod 600 "$CLOUDFLARED_TOKEN_FILE"
  atomic_jq '.domain=$domain | .address_mode="domain" | .transport_mode="tunnel"' --arg domain "${domain,,}"
  apply_tunnel
}

switch_to_https_impl(){
  local domain="$1" port="$2"
  [[ "$(value '.transport_mode')" == tunnel ]] || fail "只有 Tunnel 可以切换到直接 HTTPS。"
  [[ "$port" =~ ^[0-9]+$ ]] && ((10#$port>=1 && 10#$port<=65535)) || fail "HTTPS 端口必须是 1-65535 的数字。"
  port="$((10#$port))"
  [[ "$port" != "$(jq -r '.listen_port // 0' /etc/jp-relay/state.json 2>/dev/null || echo 0)" ]] || fail "HTTPS 端口不能与代理端口相同。"
  validate_domain_for_direct "${domain,,}" || fail "域名格式错误，或 A 记录尚未指向本机 IP $(value '.public_ip')。"
  atomic_jq '.domain=$domain | .address_mode=(if $domain=="" then "ip" else "domain" end) | .public_port=$port | .transport_mode="direct-https"' \
    --arg domain "${domain,,}" --argjson port "$port"
  apply_direct_https
}

switch_secure(){
  local target="$1" a="${2:-}" b="${3:-}"
  case "$target" in
    tunnel) transaction switch_to_tunnel_impl "$a" "$b" || fail "切换 Tunnel 失败，原 HTTPS 已恢复。";;
    https) transaction switch_to_https_impl "$a" "$b" || fail "切换 HTTPS 失败，原 Tunnel 已恢复。";;
    *) fail "仅支持 https 或 tunnel。";;
  esac
  echo "切换成功，新订阅地址：$(value '.subscription_url')"
}

status(){
  echo "传输模式：$(value '.transport_mode')"
  echo "统一订阅地址：$(value '.subscription_url')"
  echo "副机 API：$(value '.api_base_url')（仅 IP 通讯）"
  echo "订阅后缀：$(value '.subscription_suffix')"
  systemctl --no-pager --full status caddy.service vvv-sub.service 2>/dev/null || true
  [[ "$(value '.transport_mode')" != tunnel ]] || systemctl --no-pager --full status vvv-cloudflared.service 2>/dev/null || true
}

[[ $(id -u) -eq 0 ]] || fail "请使用 root 用户运行。"
[[ -s "$CFG" ]] || fail "订阅中心配置不存在。"
case "${1:-status}" in
  apply-initial|reapply) transaction reapply_current_no_transaction;;
  change-suffix) change_suffix "${2:-}";;
  change-domain) change_domain "${2:-}";;
  change-port) change_port "${2:-}";;
  change-tunnel-token) change_tunnel_token "${2:-}";;
  switch-secure) switch_secure "${2:-}" "${3:-}" "${4:-}";;
  rewrite-registration) rewrite_registration_artifacts;;
  status) status;;
  *) echo "用法：$0 apply-initial|reapply|change-suffix|change-domain|change-port|change-tunnel-token|switch-secure|rewrite-registration|status" >&2; exit 2;;
esac

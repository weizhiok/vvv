#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CFG_DIR="/etc/vvv-sub"
DATA_DIR="/var/lib/vvv-sub"
SERVICE_PORT=18081

fail(){ echo "错误：$*" >&2; exit 1; }
valid_port(){ [[ "${1:-}" =~ ^[0-9]+$ ]] && ((10#$1 >= 1 && 10#$1 <= 65535)); }
valid_domain(){ [[ "${1:-}" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]]; }

open_firewall_port(){
  local port="$1"
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
    ufw allow "${port}/tcp" >/dev/null
  fi
  if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    firewall-cmd --permanent --add-port="${port}/tcp" >/dev/null
    firewall-cmd --reload >/dev/null
  fi
}

install_caddy(){
  local arch api url tmp version
  case "$(uname -m)" in
    x86_64|amd64) arch=amd64 ;;
    aarch64|arm64) arch=arm64 ;;
    *) fail "Caddy 不支持当前架构。" ;;
  esac

  api="$(curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 \
    https://api.github.com/repos/caddyserver/caddy/releases/latest)" \
    || fail "无法查询 Caddy 官方最新版。"

  url="$(jq -r --arg suffix "linux_${arch}.tar.gz" \
    '.assets[] | select(.name | endswith($suffix)) | .browser_download_url' \
    <<<"$api" | head -n1)"
  [[ -n "$url" && "$url" != null ]] || fail "未找到 Caddy 官方 Linux/${arch} 安装包。"

  tmp="$(mktemp -d)"
  curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 \
    "$url" -o "$tmp/caddy.tgz" || fail "Caddy 下载失败。"
  tar -xzf "$tmp/caddy.tgz" -C "$tmp" caddy || fail "Caddy 安装包解压失败。"
  install -m 755 "$tmp/caddy" /usr/local/bin/caddy
  rm -rf "$tmp"

  version="$(/usr/local/bin/caddy version 2>/dev/null || true)"
  [[ -n "$version" ]] || fail "Caddy 安装后无法运行。"
  echo "Caddy：$version（官方最新版）"
}

[[ "$(id -u)" -eq 0 ]] || fail "请使用 root 用户运行。"

public_ip="$(jq -r '.public_ip // empty' /etc/jp-relay/state.json 2>/dev/null || true)"
[[ "$public_ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || fail "无法从代理状态读取本机公网 IPv4。"

domain="${VVV_SUB_DOMAIN:-}"
domain="${domain,,}"
domain="${domain%.}"
public_port="${VVV_SUB_PORT:-8443}"

valid_port "$public_port" || fail "订阅端口必须在 1-65535。"
[[ "$public_port" != 443 ]] || fail "订阅服务不能占用代理 TCP/443。"

mode=ip
if [[ -n "$domain" ]]; then
  valid_domain "$domain" || fail "订阅域名格式不正确。"
  mapfile -t resolved < <(getent ahostsv4 "$domain" | awk '{print $1}' | sort -u)
  ((${#resolved[@]})) || fail "域名尚未解析到 IPv4。"
  printf '域名解析结果：%s\n' "${resolved[*]}"
  printf '%s\n' "${resolved[@]}" | grep -Fxq "$public_ip" \
    || fail "域名没有解析到本机公网 IP $public_ip。"
  mode=domain
fi

if ss -lntH 2>/dev/null | awk '{print $4}' | grep -Eq "(^|:)${public_port}$"; then
  fail "TCP/${public_port} 已被占用。"
fi
if [[ "$mode" == domain ]] && ss -lntH 2>/dev/null | awk '{print $4}' | grep -Eq "(^|:)${SERVICE_PORT}$"; then
  fail "订阅中心内部端口 ${SERVICE_PORT} 已被占用。"
fi

apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 update -y >/dev/null
DEBIAN_FRONTEND=noninteractive apt-get \
  -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 \
  install -y ca-certificates curl jq openssl python3 tar gzip qrencode >/dev/null

open_firewall_port "$public_port"
[[ "$mode" != domain ]] || open_firewall_port 80

install -d -m 700 \
  "$CFG_DIR" "$DATA_DIR" "$DATA_DIR/hosts" "$DATA_DIR/output" "$DATA_DIR/backups" \
  /usr/local/lib/vvv /var/backups/vvv-remote
install -m 755 "$BASE_DIR/sub_center.py" /usr/local/lib/vvv/sub_center.py
install -m 755 "$BASE_DIR/sync_agent.py" /usr/local/lib/vvv/sync_agent.py

subscription_token="$(openssl rand -hex 32)"
master_token="$(openssl rand -hex 32)"
recovery_password="$(openssl rand -base64 36 | tr -d '\n')"

if [[ "$mode" == domain ]]; then
  base_url="https://${domain}:${public_port}"
  listen_host=127.0.0.1
  listen_port=$SERVICE_PORT
else
  base_url="http://${public_ip}:${public_port}"
  listen_host=0.0.0.0
  listen_port=$public_port
fi

python3 - "$CFG_DIR/config.json" <<PY
import json, sys
cfg = {
    "schema": 1,
    "mode": "$mode",
    "domain": "$domain",
    "public_ip": "$public_ip",
    "public_port": int("$public_port"),
    "base_url": "$base_url",
    "listen_host": "$listen_host",
    "listen_port": int("$listen_port"),
    "subscription_token": "$subscription_token",
    "master_token": "$master_token",
    "recovery_password": "$recovery_password",
    "refresh_hours": 24,
}
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY
chmod 600 "$CFG_DIR/config.json"
printf '%s\n' '{"hosts":[]}' > "$DATA_DIR/registry.json"
chmod 600 "$DATA_DIR/registry.json"

cat > /etc/systemd/system/vvv-sub.service <<EOF
[Unit]
Description=VVV Subscription Center
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/local/lib/vvv/sub_center.py serve
Restart=on-failure
RestartSec=3
User=root
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=full
ReadWritePaths=/etc/vvv-sub /var/lib/vvv-sub
MemoryMax=128M

[Install]
WantedBy=multi-user.target
EOF

if [[ "$mode" == domain ]]; then
  install_caddy
  id caddy >/dev/null 2>&1 || useradd --system --home /var/lib/caddy --shell /usr/sbin/nologin caddy
  install -d -o caddy -g caddy -m 750 /var/lib/caddy /var/log/caddy
  install -d -m 755 /etc/caddy

  cat > /etc/caddy/Caddyfile <<EOF
{
  admin off
  auto_https disable_redirects
}

${domain}:${public_port} {
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
    reverse_proxy 127.0.0.1:${SERVICE_PORT}
  }
  respond 404
}
EOF

  chown root:caddy /etc/caddy/Caddyfile
  chmod 640 /etc/caddy/Caddyfile
  runuser -u caddy -- /usr/local/bin/caddy validate \
    --config /etc/caddy/Caddyfile \
    --adapter caddyfile \
    || fail "Caddy 用户无法读取或验证 HTTPS 配置。"

  cat > /etc/systemd/system/caddy.service <<EOF
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
ExecReload=/usr/local/bin/caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile --force
Restart=on-failure
RestartSec=3
TimeoutStopSec=5s
LimitNOFILE=1048576
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=/var/lib/caddy /var/log/caddy

[Install]
WantedBy=multi-user.target
EOF
  chown -R caddy:caddy /var/lib/caddy /var/log/caddy
fi

systemctl daemon-reload
systemctl enable --now vvv-sub.service
if [[ "$mode" == domain ]]; then
  systemctl enable --now caddy.service
fi

internal_ok=0
for _ in $(seq 1 30); do
  if curl -fsS --connect-timeout 2 "http://127.0.0.1:${listen_port}/health" >/dev/null 2>&1; then
    internal_ok=1
    break
  fi
  sleep 1
done
if [[ "$internal_ok" != 1 ]]; then
  journalctl -u vvv-sub -n 80 --no-pager || true
  fail "订阅中心内部服务健康检查失败。"
fi

if [[ "$mode" == domain ]]; then
  systemctl is-active --quiet caddy || {
    journalctl -u caddy -n 100 --no-pager || true
    fail "Caddy HTTPS 服务启动失败。"
  }

  echo "正在等待 HTTPS 证书签发和订阅端口就绪……"
  https_ok=0
  for _ in $(seq 1 180); do
    if curl -fsS --connect-timeout 3 --max-time 8 \
      --resolve "${domain}:${public_port}:127.0.0.1" \
      "https://${domain}:${public_port}/health" >/dev/null 2>&1; then
      https_ok=1
      break
    fi
    sleep 1
  done
  if [[ "$https_ok" != 1 ]]; then
    journalctl -u caddy -n 150 --no-pager || true
    fail "HTTPS 订阅入口未就绪。请确认 TCP/80 和 TCP/${public_port} 已从公网开放。"
  fi
fi

registration_json="$(jq -nc \
  --arg base "$base_url" \
  --arg token "$master_token" \
  '{base_url:$base,master_token:$token}')"
registration_code="VVV1.$(printf %s "$registration_json" | base64 -w0 | tr '+/' '-_' | tr -d '=')"

cat > /root/VVV-订阅中心恢复信息.txt <<EOF
VVV 订阅中心恢复信息
====================
订阅中心：$base_url
模式：$mode
订阅端口：$public_port
订阅随机密钥：$subscription_token
主机接入码：$registration_code
备份解密密码：$recovery_password
本机配置：$CFG_DIR/config.json
EOF
chmod 600 /root/VVV-订阅中心恢复信息.txt

cat > /usr/local/sbin/vvv-center <<'SH'
#!/usr/bin/env bash
set -Eeuo pipefail
cfg=/etc/vvv-sub/config.json
[[ -f "$cfg" ]] || { echo "未安装订阅中心。"; exit 1; }

base="$(jq -r .base_url "$cfg")"
token="$(jq -r .subscription_token "$cfg")"

show(){
  echo "Clash Verge Rev：${base}/r/${token}/clash"
  echo "Quantumult X：${base}/r/${token}/quantumultx"
  echo "Loon：${base}/r/${token}/loon"
  echo "Shadowrocket：${base}/r/${token}/shadowrocket"
  echo "v2rayNG：${base}/r/${token}/v2rayng"
}

show_mobile(){
  echo "Quantumult X：${base}/r/${token}/quantumultx"
  echo "Loon：${base}/r/${token}/loon"
  echo "Shadowrocket：${base}/r/${token}/shadowrocket"
  echo "v2rayNG：${base}/r/${token}/v2rayng"
}

show_qr(){
  while IFS= read -r line; do
    name="${line%%：*}"
    url="${line#*：}"
    echo
    echo "【${name}】"
    echo "$url"
    qrencode -t ANSIUTF8 -m1 "$url"
  done < <(show_mobile)
}

case "${1:-menu}" in
  urls) show ;;
  qr) show; show_qr ;;
  backup)
    python3 /usr/local/lib/vvv/sub_center.py backup
    echo "备份：/var/lib/vvv-sub/backups/latest.enc"
    ;;
  status)
    systemctl --no-pager --full status vvv-sub.service caddy.service 2>/dev/null || true
    ;;
  *)
    while true; do
      echo "========== 订阅中心管理 =========="
      echo "1. 查看订阅地址"
      echo "2. 显示订阅二维码"
      echo "3. 立即生成加密备份"
      echo "4. 查看服务状态"
      echo "5. 查看恢复信息"
      echo "0. 返回"
      read -r -p "请输入编号：" x
      case "$x" in
        1) show ;;
        2) show; show_qr ;;
        3) "$0" backup ;;
        4) "$0" status ;;
        5) cat /root/VVV-订阅中心恢复信息.txt ;;
        0) exit 0 ;;
        *) echo "请输入有效编号。" ;;
      esac
    done
    ;;
esac
SH
chmod 700 /usr/local/sbin/vvv-center

printf '%s' "$registration_code" > /etc/vvv-sub/registration.code
chmod 600 /etc/vvv-sub/registration.code

printf '\n订阅中心安装成功。\n'
[[ "$mode" == ip ]] && echo "注意：IP 模式使用 HTTP，长期使用建议配置域名 HTTPS。"
echo "主机接入码：$registration_code"
echo "恢复信息：/root/VVV-订阅中心恢复信息.txt"
/usr/local/sbin/vvv-center qr

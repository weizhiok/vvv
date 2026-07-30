#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CFG_DIR=/etc/vvv-sub
DATA_DIR=/var/lib/vvv-sub
SERVICE_PORT=18081

fail(){ echo "错误：$*" >&2; exit 1; }
valid_port(){ [[ "$1" =~ ^[0-9]+$ ]] && ((10#$1>=1 && 10#$1<=65535)); }

install_caddy(){
  command -v caddy >/dev/null 2>&1 && return 0
  if apt-get install -y caddy >/dev/null 2>&1; then return 0; fi
  local arch asset api url tmp
  case "$(uname -m)" in x86_64|amd64) arch=amd64;; aarch64|arm64) arch=arm64;; *) fail "Caddy 不支持当前架构。";; esac
  api="$(curl -fsSL --retry 5 https://api.github.com/repos/caddyserver/caddy/releases/latest)" || fail "无法查询 Caddy 最新版。"
  url="$(jq -r --arg a "linux_${arch}.tar.gz" '.assets[]|select(.name|endswith($a))|.browser_download_url' <<<"$api" | head -n1)"
  [[ -n "$url" && "$url" != null ]] || fail "未找到 Caddy 下载文件。"
  tmp="$(mktemp -d)"; curl -fsSL --retry 5 "$url" -o "$tmp/caddy.tgz"; tar -xzf "$tmp/caddy.tgz" -C "$tmp" caddy
  install -m 755 "$tmp/caddy" /usr/local/bin/caddy; rm -rf "$tmp"
}

public_ip="$(jq -r '.public_ip // empty' /etc/jp-relay/state.json 2>/dev/null || true)"
[[ "$public_ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || fail "无法从代理状态读取本机公网 IPv4。"

read -r -p "请输入订阅访问域名（可直接回车使用 IP 模式）：" domain
domain="${domain,,}"; domain="${domain%.}"
read -r -p "请输入订阅服务端口 [默认 8443]：" public_port
public_port="${public_port:-8443}"; valid_port "$public_port" || fail "端口必须在 1-65535。"
[[ "$public_port" != "443" ]] || fail "订阅服务不能占用代理 TCP/443，请使用 8443 或其他端口。"
if ss -lntH 2>/dev/null | awk '{print $4}' | grep -Eq "(^|:)$public_port$"; then fail "TCP/$public_port 已被占用。"; fi

mode=ip
if [[ -n "$domain" ]]; then
  [[ "$domain" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]] || fail "域名格式不正确。"
  mapfile -t resolved < <(getent ahostsv4 "$domain" | awk '{print $1}' | sort -u)
  ((${#resolved[@]})) || fail "域名尚未解析到 IPv4。"
  printf '域名解析结果：%s\n' "${resolved[*]}"
  printf '%s\n' "${resolved[@]}" | grep -Fxq "$public_ip" || fail "域名没有解析到本机公网 IP $public_ip。"
  mode=domain
  if ss -lntH 2>/dev/null | awk '{print $4}' | grep -Eq "(^|:)${SERVICE_PORT}$"; then fail "订阅中心内部端口 ${SERVICE_PORT} 已被占用。"; fi
fi

apt-get update -y >/dev/null
DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl jq openssl python3 tar gzip qrencode >/dev/null
install -d -m 700 "$CFG_DIR" "$DATA_DIR" "$DATA_DIR/hosts" "$DATA_DIR/output" "$DATA_DIR/backups" /usr/local/lib/vvv /var/backups/vvv-remote
install -m 755 "$BASE_DIR/sub_center.py" /usr/local/lib/vvv/sub_center.py
install -m 755 "$BASE_DIR/sync_agent.py" /usr/local/lib/vvv/sync_agent.py

subscription_token="$(openssl rand -hex 32)"
master_token="$(openssl rand -hex 32)"
recovery_password="$(openssl rand -base64 36 | tr -d '\n')"
if [[ "$mode" == domain ]]; then
  base_url="https://${domain}:${public_port}"
  listen_host=127.0.0.1; listen_port=$SERVICE_PORT
else
  base_url="http://${public_ip}:${public_port}"
  listen_host=0.0.0.0; listen_port=$public_port
fi

python3 - "$CFG_DIR/config.json" <<PY
import json,sys
cfg={
 'schema':1,'mode':'$mode','domain':'$domain','public_ip':'$public_ip','public_port':int('$public_port'),
 'base_url':'$base_url','listen_host':'$listen_host','listen_port':int('$listen_port'),
 'subscription_token':'$subscription_token','master_token':'$master_token','recovery_password':'$recovery_password',
 'refresh_hours':24
}
open(sys.argv[1],'w',encoding='utf-8').write(json.dumps(cfg,ensure_ascii=False,indent=2)+'\n')
PY
chmod 600 "$CFG_DIR/config.json"
echo '{"hosts":[]}' > "$DATA_DIR/registry.json"; chmod 600 "$DATA_DIR/registry.json"

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
  caddy_bin="$(command -v caddy)"
  cat > /etc/systemd/system/caddy.service <<EOF
[Unit]
Description=Caddy Web Server
After=network-online.target
Wants=network-online.target
[Service]
Type=notify
User=caddy
Group=caddy
ExecStart=${caddy_bin} run --environ --config /etc/caddy/Caddyfile
ExecReload=${caddy_bin} reload --config /etc/caddy/Caddyfile --force
TimeoutStopSec=5s
LimitNOFILE=1048576
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=/var/lib/caddy /var/log/caddy
[Install]
WantedBy=multi-user.target
EOF
  install -d -m 755 /etc/caddy
  cat > /etc/caddy/Caddyfile <<EOF
{
  admin off
  auto_https disable_redirects
}
https://${domain}:${public_port} {
  log { output discard }
  @allowed path /r/* /api/v1/* /health
  handle @allowed { reverse_proxy 127.0.0.1:${SERVICE_PORT} }
  respond 404
}
EOF
  chown -R caddy:caddy /var/lib/caddy /var/log/caddy
fi

systemctl daemon-reload
systemctl enable --now vvv-sub.service
if [[ "$mode" == domain ]]; then systemctl enable --now caddy.service; fi
sleep 2
systemctl is-active --quiet vvv-sub.service || { journalctl -u vvv-sub -n 50 --no-pager; fail "订阅中心启动失败。"; }
if [[ "$mode" == domain ]]; then systemctl is-active --quiet caddy || { journalctl -u caddy -n 80 --no-pager; fail "Caddy HTTPS 服务启动失败。"; }; fi

registration_json="$(jq -nc --arg base "$base_url" --arg token "$master_token" '{base_url:$base,master_token:$token}')"
registration_code="VVV1.$(printf %s "$registration_json" | base64 -w0 | tr '+/' '-_' | tr -d '=')"
cat > /root/VVV-订阅中心恢复信息.txt <<EOF
VVV 订阅中心恢复信息（请保存到电脑或密码管理器）
================================================
订阅中心：$base_url
模式：$mode
订阅端口：$public_port
订阅随机密钥：$subscription_token
主机接入码：$registration_code
备份解密密码：$recovery_password
本机配置：/etc/vvv-sub/config.json
远端备份：中转主机 /var/backups/vvv-remote/latest.enc

恢复方法：在新 VPS 安装订阅中心后，运行：
python3 /usr/local/lib/vvv/sub_center.py restore 备份文件 "$recovery_password"
systemctl restart vvv-sub caddy 2>/dev/null || systemctl restart vvv-sub
EOF
chmod 600 /root/VVV-订阅中心恢复信息.txt

cat > /usr/local/sbin/vvv-center <<'SH2'
#!/usr/bin/env bash
set -Eeuo pipefail
cfg=/etc/vvv-sub/config.json
[[ -f "$cfg" ]] || { echo "未安装订阅中心。"; exit 1; }
base=$(jq -r .base_url "$cfg"); token=$(jq -r .subscription_token "$cfg")
show(){
  echo "Clash Verge Rev：${base}/r/${token}/clash"
  echo "Quantumult X：${base}/r/${token}/quantumultx"
  echo "Loon：${base}/r/${token}/loon"
  echo "Shadowrocket：${base}/r/${token}/shadowrocket"
  echo "v2rayNG：${base}/r/${token}/v2rayng"
}
case "${1:-menu}" in
 urls) show;;
 backup) python3 /usr/local/lib/vvv/sub_center.py backup; echo "备份：/var/lib/vvv-sub/backups/latest.enc";;
 status) systemctl --no-pager --full status vvv-sub.service caddy.service 2>/dev/null || true;;
 *)
   while true; do
    echo "========== 订阅中心管理 =========="; echo "1. 查看订阅地址"; echo "2. 显示订阅二维码"; echo "3. 立即生成加密备份"; echo "4. 查看服务状态"; echo "5. 查看恢复信息"; echo "0. 返回"
    read -r -p "请输入编号：" x
    case "$x" in
      1) show;;
      2) while IFS= read -r u; do echo; echo "$u"; qrencode -t ANSIUTF8 -m1 "$u"; done < <(show | sed 's/^[^：]*：//');;
      3) "$0" backup;; 4) "$0" status;; 5) cat /root/VVV-订阅中心恢复信息.txt;; 0) exit 0;; *) echo "请输入有效编号。";; esac
   done;;
esac
SH2
chmod 700 /usr/local/sbin/vvv-center

printf '\n订阅中心安装成功。\n'
[[ "$mode" == ip ]] && echo "注意：IP 模式使用 HTTP，适合临时或备用使用；长期使用建议配置域名 HTTPS。"
echo "主机接入码：$registration_code"
echo "恢复信息：/root/VVV-订阅中心恢复信息.txt"
/usr/local/sbin/vvv-center urls
printf '%s' "$registration_code" > /etc/vvv-sub/registration.code
chmod 600 /etc/vvv-sub/registration.code

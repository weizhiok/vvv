#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CFG_DIR=/etc/vvv-sub
DATA_DIR=/var/lib/vvv-sub
SERVICE_PORT=18081
fail(){ echo "错误：$*" >&2; exit 1; }
valid_port(){ [[ "${1:-}" =~ ^[0-9]+$ ]] && ((10#$1>=1 && 10#$1<=65535)); }
valid_domain(){ [[ "${1:-}" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]]; }
open_port(){
  local port=$1
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then ufw allow "${port}/tcp" >/dev/null; fi
  if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    firewall-cmd --permanent --add-port="${port}/tcp" >/dev/null; firewall-cmd --reload >/dev/null
  fi
}
install_caddy(){
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
[[ $(id -u) -eq 0 ]] || fail "请使用 root 用户运行。"
public_ip="$(jq -r '.public_ip // empty' /etc/jp-relay/state.json 2>/dev/null || true)"
[[ "$public_ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || fail "无法读取本机公网 IPv4。"
domain="${VVV_SUB_DOMAIN:-}"; domain="${domain,,}"; domain="${domain%.}"
public_port="${VVV_SUB_PORT:-8443}"
valid_domain "$domain" || fail "订阅中心只提供 HTTPS，必须填写有效域名。"
valid_port "$public_port" || fail "订阅端口无效。"
[[ "$public_port" != 443 ]] || fail "订阅端口不能占用 443。"
mapfile -t resolved < <(getent ahostsv4 "$domain"|awk '{print $1}'|sort -u)
((${#resolved[@]})) || fail "订阅域名尚未解析到 IPv4。"
printf '%s\n' "${resolved[@]}"|grep -Fxq "$public_ip" || fail "订阅域名没有解析到本机 IP $public_ip。"
mode=domain
apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 update >/dev/null
DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 install -y ca-certificates curl jq openssl python3 tar gzip >/dev/null
open_port "$public_port"; open_port 80
install -d -m700 "$CFG_DIR" "$DATA_DIR" "$DATA_DIR/hosts" "$DATA_DIR/output" "$DATA_DIR/backups" /usr/local/lib/vvv
for f in sub_center.py sync_agent.py backup_manager.py rclone_manager.sh; do install -m755 "$BASE_DIR/$f" "/usr/local/lib/vvv/$f"; done
subscription_token="$(openssl rand -hex 32)"; master_token="$(openssl rand -hex 32)"; recovery_password="$(openssl rand -base64 36|tr -d '\n')"
base_url="https://${domain}:${public_port}"; listen_host=127.0.0.1; listen_port=$SERVICE_PORT
python3 - "$CFG_DIR/config.json" "$mode" "$domain" "$public_ip" "$public_port" "$base_url" "$listen_host" "$listen_port" "$subscription_token" "$master_token" "$recovery_password" <<'PY'
import json,sys,os,tempfile
(path,mode,domain,ip,pport,base,lhost,lport,sub,master,recovery)=sys.argv[1:]
obj={'schema':2,'mode':mode,'domain':domain,'public_ip':ip,'public_port':int(pport),'base_url':base,'listen_host':lhost,'listen_port':int(lport),'subscription_token':sub,'master_token':master,'recovery_password':recovery,'refresh_hours':24}
fd,tmp=tempfile.mkstemp(prefix='.config.',dir=os.path.dirname(path))
with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump(obj,f,ensure_ascii=False,indent=2); f.write('\n')
os.chmod(tmp,0o600); os.replace(tmp,path)
PY
printf '%s\n' '{"hosts":[]}' > "$DATA_DIR/registry.json"; chmod 600 "$DATA_DIR/registry.json"
rm -f "$CFG_DIR/cloud.json" "$CFG_DIR/rclone.conf"
cat > /etc/systemd/system/vvv-sub.service <<'UNIT'
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
ProtectSystem=full
ReadWritePaths=/etc/vvv-sub /var/lib/vvv-sub /run/lock
MemoryMax=192M
[Install]
WantedBy=multi-user.target
UNIT
install_caddy
  id caddy >/dev/null 2>&1 || useradd --system --home /var/lib/caddy --shell /usr/sbin/nologin caddy
  install -d -o caddy -g caddy -m750 /var/lib/caddy /var/log/caddy
  install -d -m755 /etc/caddy
  cat > /etc/caddy/Caddyfile <<EOF_CADDY
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
  log { output discard }
  @allowed path /r/* /api/v1/* /health
  handle @allowed { reverse_proxy 127.0.0.1:${SERVICE_PORT} }
  respond 404
}
EOF_CADDY
  chown root:caddy /etc/caddy/Caddyfile; chmod 640 /etc/caddy/Caddyfile
  runuser -u caddy -- /usr/local/bin/caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile || fail "Caddy 配置验证失败。"
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
ExecReload=/usr/local/bin/caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile --force
Restart=on-failure
RestartSec=3
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=/var/lib/caddy /var/log/caddy
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload; systemctl enable --now vvv-sub.service caddy.service
for _ in $(seq 1 40); do curl -fsS "http://127.0.0.1:${listen_port}/health" >/dev/null 2>&1 && break; sleep 1; done
curl -fsS "http://127.0.0.1:${listen_port}/health" >/dev/null || { journalctl -u vvv-sub -n80 --no-pager; fail "订阅中心内部服务未就绪。"; }
echo "正在等待 HTTPS 证书签发……"; ok=0
for _ in $(seq 1 180); do curl -fsS --resolve "${domain}:${public_port}:127.0.0.1" "https://${domain}:${public_port}/health" >/dev/null 2>&1 && { ok=1; break; }; sleep 1; done
[[ $ok == 1 ]] || { journalctl -u caddy -n120 --no-pager; fail "HTTPS 订阅入口未就绪，请检查 TCP/80 和 TCP/${public_port}。"; }
registration_json="$(jq -nc --arg base "$base_url" --arg token "$master_token" '{base_url:$base,master_token:$token}')"
registration_code="VVV1.$(printf %s "$registration_json"|base64 -w0|tr '+/' '-_'|tr -d '=')"
printf '%s' "$registration_code" > "$CFG_DIR/registration.code"; chmod 600 "$CFG_DIR/registration.code"
cat > /root/VVV-订阅中心恢复信息.txt <<EOF_REC
VVV 订阅中心恢复信息
====================
订阅中心：$base_url
订阅端口：$public_port
订阅随机密钥：$subscription_token
主机接入码：$registration_code
备份解密密码：$recovery_password
本地备份目录：$DATA_DIR/backups
云备份：默认关闭
EOF_REC
chmod 600 /root/VVV-订阅中心恢复信息.txt
cat > /usr/local/sbin/vvv-center <<'SH'
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
chmod 700 /usr/local/sbin/vvv-center
python3 /usr/local/lib/vvv/backup_manager.py create first-install --force >/dev/null
printf '\n订阅中心安装成功。\n主机接入码：%s\n' "$registration_code"
/usr/local/sbin/vvv-center urls

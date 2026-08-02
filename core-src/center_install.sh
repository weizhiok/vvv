#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CFG_DIR=/etc/vvv-sub
DATA_DIR=/var/lib/vvv-sub
SERVICE_PORT=18081
CENTER_STARTED=$SECONDS

fail(){ echo "错误：$*" >&2; exit 1; }
section(){ printf '\n========== %s ==========\n' "$*"; }
valid_port(){ [[ "${1:-}" =~ ^[0-9]+$ ]] && ((10#$1>=1 && 10#$1<=65535)); }
valid_domain(){ [[ "${1:-}" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]]; }
ensure_service(){
  local service="$1" action="${2:-restart}" wait="${3:-75}"
  systemctl enable "$service" >/dev/null 2>&1 || true
  systemctl reset-failed "$service" >/dev/null 2>&1 || true
  timeout "$wait" systemctl "$action" "$service" || true
  for _ in $(seq 1 20); do systemctl is-active --quiet "$service" && return 0; sleep 1; done
  systemctl --no-pager --full status "$service" >&2 || true
  journalctl -u "$service" -n100 --no-pager >&2 || true
  fail "${service} 未进入 active 状态。"
}
apt_run(){
  local label="$1" log
  shift
  log="$(mktemp /tmp/vvv-apt.XXXXXX)"
  if "$@" 2>&1 | tee "$log"; then rm -f "$log"; return 0; fi
  if grep -Eqi 'Could not get lock|Unable to acquire.*lock|Waiting for cache lock' "$log"; then
    rm -f "$log"
    fail "APT/dpkg 锁等待超过 10 秒。请等待系统自动更新结束后重新运行。"
  fi
  echo "${label}失败。" >&2
  rm -f "$log"
  return 1
}
install_caddy(){
  local arch api asset_name url digest expected actual tmp
  case "$(uname -m)" in x86_64|amd64) arch=amd64;; aarch64|arm64) arch=arm64;; *) fail "Caddy 不支持当前架构。";; esac
  echo "正在查询 Caddy 最新稳定版……"
  api="$(curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 --max-time 90 https://api.github.com/repos/caddyserver/caddy/releases/latest)" || fail "无法查询 Caddy。"
  asset_name="$(jq -r --arg s "linux_${arch}.tar.gz" '.assets[]|select(.name|endswith($s))|.name' <<<"$api" | head -n1)"
  url="$(jq -r --arg n "$asset_name" '.assets[]|select(.name==$n)|.browser_download_url' <<<"$api" | head -n1)"
  digest="$(jq -r --arg n "$asset_name" '.assets[]|select(.name==$n)|(.digest // "")' <<<"$api" | head -n1)"
  [[ -n "$url" && "$url" != null ]] || fail "找不到 Caddy 安装包。"
  [[ "$digest" =~ ^sha256:[0-9a-fA-F]{64}$ ]] || fail "GitHub 没有返回 Caddy 安装包 SHA-256。"
  expected="${digest#sha256:}"; tmp="$(mktemp -d)"
  echo "正在下载 Caddy：${asset_name}"
  curl -fL --retry 5 --retry-all-errors --connect-timeout 15 --max-time 300 "$url" -o "$tmp/caddy.tgz" || fail "下载 Caddy 失败。"
  actual="$(sha256sum "$tmp/caddy.tgz" | awk '{print $1}')"
  [[ "${expected,,}" == "${actual,,}" ]] || fail "Caddy 安装包 SHA-256 校验失败。"
  tar -xzf "$tmp/caddy.tgz" -C "$tmp" caddy
  install -m755 "$tmp/caddy" /usr/local/bin/caddy
  rm -rf "$tmp"
  echo "Caddy 安装完成：$(/usr/local/bin/caddy version)"
}
write_caddy_service(){
  cat > /etc/systemd/system/caddy.service <<'EOF'
[Unit]
Description=Caddy Subscription Frontend
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
EOF
}
write_sub_service(){
  cat > /etc/systemd/system/vvv-sub.service <<'EOF'
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
ReadWritePaths=/etc/vvv-sub /var/lib/vvv-sub /run/lock /run
MemoryMax=192M
[Install]
WantedBy=multi-user.target
EOF
}
write_center_manager(){
  cat > /usr/local/sbin/vvv-center <<'SH'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
CFG=/etc/vvv-sub/config.json
TRANSPORT=/usr/local/lib/vvv/center_transport.sh
BACKUP=/usr/local/lib/vvv/backup_manager.py
RCLONE=/usr/local/lib/vvv/rclone_manager.sh
ADAPTERS=/usr/local/lib/vvv/adapter_manager.py
pause(){ read -r -p "按回车返回……" _; }
get(){ jq -r "$1" "$CFG"; }
show_url(){
  echo "传输模式：$(get '.transport_mode')"
  echo "统一订阅地址：$(get '.subscription_url')"
  echo "所有支持的客户端均填写上面同一个地址。"
}
debug_headers(){
  local flag=/run/vvv-sub-header-debug.enabled log=/run/vvv-sub-header-debug.jsonl
  rm -f "$log"; : > "$log"; touch "$flag"
  echo
  echo "========== 客户端请求头识别调试 =========="
  echo "请在客户端中立即刷新统一订阅地址。"
  echo "监听时间：5 分钟；按 Ctrl+C 可提前结束。"
  echo "Authorization、Cookie、完整订阅后缀等敏感内容会自动隐藏。"
  trap 'rm -f "$flag"; trap - INT TERM RETURN' INT TERM RETURN
  timeout --foreground 300 bash -c '
    tail -n0 -F /run/vvv-sub-header-debug.jsonl 2>/dev/null | while IFS= read -r line; do
      echo; echo "---------- 收到订阅请求 ----------"; printf "%s\n" "$line" | jq .
    done
  ' || true
  rm -f "$flag"
  trap - INT TERM RETURN
}
change_suffix(){
  local suffix
  read -r -p "请输入新的订阅后缀（6-32位大小写字母或数字）：" suffix
  "$TRANSPORT" change-suffix "$suffix"
}
show_hosts(){
  curl -fsS -H "Authorization: Bearer $(get '.master_token')" "http://127.0.0.1:$(get '.listen_port')/api/v1/hosts" | jq .
}
while true; do
  mode="$(get '.transport_mode')"
  echo
  echo "========== 订阅中心管理 =========="
  echo "当前传输：$mode"
  echo "统一地址：$(get '.subscription_url')"
  echo
  n=1; declare -A act=()
  echo "$n. 查看统一订阅地址"; act[$n]=url; ((n++))
  echo "$n. 客户端请求头识别调试"; act[$n]=debug; ((n++))
  echo "$n. 更新客户端适配器"; act[$n]=adapter_update; ((n++))
  echo "$n. 查看客户端适配器状态"; act[$n]=adapter_status; ((n++))
  echo "$n. 修改订阅地址后缀"; act[$n]=suffix; ((n++))
  if [[ "$mode" == direct-http ]]; then echo "$n. 开启 HTTPS 传输"; act[$n]=https; ((n++)); fi
  echo "$n. 查看传输与证书/Tunnel状态"; act[$n]=transport; ((n++))
  echo "$n. 查看本地备份"; act[$n]=backups; ((n++))
  echo "$n. 开启云备份功能"; act[$n]=cloud_enable; ((n++))
  echo "$n. 查看并测试云备份"; act[$n]=cloud_status; ((n++))
  echo "$n. 关闭或重新配置云备份"; act[$n]=cloud_change; ((n++))
  echo "$n. 查看已注册主机"; act[$n]=hosts; ((n++))
  echo "$n. 查看服务状态"; act[$n]=services; ((n++))
  echo "$n. 查看恢复信息"; act[$n]=recovery; ((n++))
  echo "0. 返回"
  read -r -p "请输入编号：" choice
  [[ "$choice" == 0 ]] && exit 0
  case "${act[$choice]:-}" in
    url) show_url; pause;;
    debug) debug_headers;;
    adapter_update) python3 "$ADAPTERS" update; pause;;
    adapter_status) python3 "$ADAPTERS" status; pause;;
    suffix) change_suffix; pause;;
    https) "$TRANSPORT" enable-https; pause;;
    transport) "$TRANSPORT" status; pause;;
    backups) python3 "$BACKUP" list; pause;;
    cloud_enable) "$RCLONE" enable; pause;;
    cloud_status) "$RCLONE" status; pause;;
    cloud_change)
      echo "1. 关闭云备份"; echo "2. 重新配置云备份"
      read -r -p "请选择：" sub
      [[ "$sub" == 1 ]] && "$RCLONE" disable || [[ "$sub" == 2 ]] && "$RCLONE" reconfigure
      pause
      ;;
    hosts) show_hosts; pause;;
    services)
      systemctl --no-pager --full status vvv-sub.service caddy.service vvv-sync.timer vvv-sync.path 2>/dev/null || true
      [[ "$mode" != tunnel ]] || systemctl --no-pager --full status vvv-cloudflared.service 2>/dev/null || true
      pause
      ;;
    recovery) cat /root/VVV-订阅中心恢复信息.txt; pause;;
    *) echo "请输入有效编号。";;
  esac
done
SH
  chmod 700 /usr/local/sbin/vvv-center
}

[[ $(id -u) -eq 0 ]] || fail "请使用 root 用户运行。"
public_ip="$(jq -r '.public_ip // empty' /etc/jp-relay/state.json 2>/dev/null || true)"
[[ "$public_ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || fail "无法读取本机公网 IPv4。"
domain="${VVV_SUB_DOMAIN:-}"; domain="${domain,,}"; domain="${domain%.}"
port="${VVV_SUB_PORT:-8443}"
transport="${VVV_SUB_TRANSPORT:-direct-https}"
suffix="${VVV_SUB_SUFFIX:-}"
tunnel_token="${VVV_CF_TUNNEL_TOKEN:-}"
valid_port "$port" || fail "订阅服务端口无效。"
[[ "$port" != 443 ]] || fail "直连订阅服务端口不能占用代理端口 443。"
[[ -z "$domain" ]] || valid_domain "$domain" || fail "订阅域名格式不正确。"
[[ "$suffix" =~ ^[A-Za-z0-9]{6,32}$ ]] || fail "订阅后缀必须是6-32位大小写字母或数字。"
case "${suffix,,}" in health|api|admin|debug) fail "订阅后缀使用了系统保留词。";; esac
case "$transport" in direct-http|direct-https|tunnel);; *) fail "订阅传输模式无效。";; esac
if [[ "$transport" == tunnel ]]; then
  [[ -n "$domain" ]] || fail "Cloudflare Tunnel模式必须输入订阅域名。"
  [[ -n "$tunnel_token" ]] || fail "Cloudflare Tunnel模式必须输入 Tunnel Token。"
fi
if [[ -n "$domain" && "$transport" != tunnel ]]; then
  mapfile -t resolved < <(getent ahostsv4 "$domain" | awk '{print $1}' | sort -u)
  ((${#resolved[@]})) || fail "订阅域名尚未解析到 IPv4。"
  printf '%s\n' "${resolved[@]}" | grep -Fxq "$public_ip" || fail "订阅域名没有解析到本机 IP ${public_ip}。"
fi

section "准备订阅中心依赖"
required=(ca-certificates curl jq openssl python3 tar gzip)
[[ "$transport" != direct-https || -n "$domain" ]] || required+=(python3-venv)
missing=()
for package in "${required[@]}"; do dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed' || missing+=("$package"); done
if ((${#missing[@]})); then
  echo "正在安装缺少的依赖：${missing[*]}"
  apt_run "订阅中心依赖安装" env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=10 -o Acquire::Retries=2 install -y "${missing[@]}" || {
    apt_run "APT索引刷新" apt-get -o DPkg::Lock::Timeout=10 -o Acquire::Retries=2 -o Acquire::PDiffs=false -o Acquire::IndexTargets::deb-src::Sources::DefaultEnabled=false update
    apt_run "订阅中心依赖安装" env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=10 -o Acquire::Retries=2 install -y "${missing[@]}"
  }
else
  echo "订阅中心依赖已齐全，跳过重复 apt update。"
fi

install -d -m700 "$CFG_DIR" "$DATA_DIR" "$DATA_DIR/hosts" "$DATA_DIR/output" "$DATA_DIR/backups" /usr/local/lib/vvv
install -d -m755 /var/www/vvv-acme /etc/caddy
for file in sub_center.py sync_agent.py backup_manager.py rclone_manager.sh client_adapters.py adapter_manager.py center_transport.sh; do
  install -m755 "$BASE_DIR/$file" "/usr/local/lib/vvv/$file"
done
client_adapters_result="$(python3 /usr/local/lib/vvv/client_adapters.py)" || fail "客户端适配器自检失败。"
echo "客户端适配器：${client_adapters_result}"

master_token="$(openssl rand -hex 32)"
recovery_password="$(openssl rand -base64 36 | tr -d '\n')"
python3 - "$CFG_DIR/config.json" "$domain" "$public_ip" "$port" "$transport" "$suffix" "$master_token" "$recovery_password" <<'PY'
import json,os,sys,tempfile
path,domain,ip,port,transport,suffix,master,recovery=sys.argv[1:]
obj={
 'schema':3,
 'address_mode':'domain' if domain else 'ip',
 'domain':domain,
 'public_ip':ip,
 'public_port':int(port),
 'transport_mode':transport,
 'subscription_suffix':suffix,
 'base_url':'',
 'subscription_url':'',
 'listen_host':'127.0.0.1',
 'listen_port':18081,
 'master_token':master,
 'recovery_password':recovery,
 'refresh_hours':24,
}
fd,tmp=tempfile.mkstemp(prefix='.config.',dir=os.path.dirname(path))
with os.fdopen(fd,'w',encoding='utf-8') as f:
    json.dump(obj,f,ensure_ascii=False,indent=2); f.write('\n')
os.chmod(tmp,0o600); os.replace(tmp,path)
PY
printf '%s\n' '{"hosts":[]}' > "$DATA_DIR/registry.json"
chmod 600 "$DATA_DIR/registry.json"
rm -f "$CFG_DIR/cloud.json" "$CFG_DIR/rclone.conf"
if [[ "$transport" == tunnel ]]; then printf '%s' "$tunnel_token" > "$CFG_DIR/cloudflared.token"; chmod 600 "$CFG_DIR/cloudflared.token"; fi

section "安装订阅中心与 HTTPS/HTTP 前端"
install_caddy
id caddy >/dev/null 2>&1 || useradd --system --home /var/lib/caddy --shell /usr/sbin/nologin caddy
install -d -o caddy -g caddy -m750 /var/lib/caddy /var/log/caddy
install -d -o caddy -g caddy -m700 /etc/caddy/certs
write_caddy_service
write_sub_service
systemctl daemon-reload
ensure_service vvv-sub.service restart 60
/usr/local/lib/vvv/center_transport.sh apply-initial || fail "订阅传输前端安装失败。"

curl -fsS --connect-timeout 2 --max-time 4 "http://127.0.0.1:${SERVICE_PORT}/health" >/dev/null || fail "订阅中心内部服务未就绪。"
write_center_manager
python3 /usr/local/lib/vvv/backup_manager.py create first-install --force >/dev/null

printf '\n订阅中心安装成功，总耗时 %s 秒。\n' "$((SECONDS-CENTER_STARTED))"
/usr/local/sbin/vvv-center urls 2>/dev/null || {
  echo "传输模式：$(jq -r '.transport_mode' "$CFG_DIR/config.json")"
  echo "统一订阅地址：$(jq -r '.subscription_url' "$CFG_DIR/config.json")"
}
if [[ "$transport" == direct-http ]]; then
  echo
  echo "警告：当前使用 HTTP 明文传输，节点 UUID 和密码可能被网络观察者读取。"
  echo "测试完成后可运行 vps → 订阅中心管理 → 开启 HTTPS 传输。"
fi

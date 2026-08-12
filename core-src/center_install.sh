#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CFG_DIR=/etc/vvv-sub
DATA_DIR=/var/lib/vvv-sub
SERVICE_PORT=18081
CENTER_STARTED=$SECONDS
RESTORE_MODE="${VVV_RESTORE_MODE:-0}"

fail(){ echo "错误：$*" >&2; exit 1; }
section(){ printf '\n========== %s ==========\n' "$*"; }
valid_port(){ [[ "${1:-}" =~ ^[0-9]+$ ]] && ((10#$1>=1 && 10#$1<=65535)); }
valid_domain(){ [[ "${1:-}" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]]; }
open_port(){
  local port="$1" proto="${2:-tcp}"
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then ufw allow "${port}/${proto}" >/dev/null; fi
  if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    firewall-cmd --permanent --add-port="${port}/${proto}" >/dev/null
    firewall-cmd --reload >/dev/null
  fi
}
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
    rm -f "$log"; fail "APT/dpkg 锁等待超过 10 秒。请等待系统自动更新结束后重新运行。"
  fi
  echo "${label}失败。" >&2; rm -f "$log"; return 1
}

repair_dpkg_state() {
  local audit log bad_file backup_dir="" attempt configured=0 fix_broken_attempted=0
  local dpkg_admin_dir backup_root status_file file_name update_candidate_count max_attempts quarantined_count=0
  dpkg_admin_dir="${VVV_DPKG_ADMIN_DIR:-/var/lib/dpkg}"
  backup_root="${VVV_DPKG_BACKUP_ROOT:-/var/backups}"
  command -v dpkg >/dev/null 2>&1 || fail "当前 Debian 找不到 dpkg，无法继续安装。"
  export DEBIAN_FRONTEND=noninteractive
  export NEEDRESTART_MODE=a

  update_candidate_count=0
  if [[ -d "$dpkg_admin_dir/updates" ]]; then
    update_candidate_count="$(find "$dpkg_admin_dir/updates" -maxdepth 1 -mindepth 1 -name '[0-9][0-9][0-9][0-9]' 2>/dev/null | wc -l | tr -d '[:space:]')"
  fi
  [[ "$update_candidate_count" =~ ^[0-9]+$ ]] || update_candidate_count=0
  (( update_candidate_count <= 10000 )) || fail "dpkg updates/NNNN 条目数量异常（${update_candidate_count}），拒绝自动处理。"
  max_attempts=$((update_candidate_count + 3))

  echo "检查并修复 dpkg 配置状态……"
  for ((attempt=1; attempt<=max_attempts; attempt++)); do
    log="$(mktemp /tmp/vvv-dpkg-configure.XXXXXX)"
    if LC_ALL=C dpkg --force-confold --configure -a >"$log" 2>&1; then
      cat "$log"
      rm -f "$log"
      configured=1
      break
    fi
    cat "$log" >&2

    bad_file="$(sed -n "s#^dpkg: error: parsing file '\\([^']*\\)'.*#\\1#p" "$log" | head -n1)"
    case "$bad_file" in
      "$dpkg_admin_dir"/updates/[0-9][0-9][0-9][0-9]) ;;
      *) bad_file="" ;;
    esac

    if [[ -n "$bad_file" ]]; then
      [[ -f "$bad_file" && ! -L "$bad_file" ]] || {
        rm -f "$log"
        fail "dpkg 报告的 updates 临时文件不是普通文件，拒绝自动处理：$bad_file"
      }
      if [[ -z "$backup_dir" ]]; then
        backup_dir="${backup_root}/vvv-dpkg-recovery-$(date +%Y%m%d-%H%M%S)-$$"
        mkdir -p "$backup_dir/updates" || { rm -f "$log"; fail "无法创建 dpkg 修复备份目录。"; }
        chmod 700 "$backup_dir" "$backup_dir/updates" || { rm -f "$log"; fail "无法保护 dpkg 修复备份目录权限。"; }
        for status_file in "$dpkg_admin_dir/status" "$dpkg_admin_dir/status-old"; do
          if [[ -f "$status_file" ]]; then
            cp -a -- "$status_file" "$backup_dir/" || { rm -f "$log"; fail "备份 dpkg 主状态文件失败，拒绝继续。"; }
          fi
        done
      fi
      file_name="${bad_file##*/}"
      [[ ! -e "$backup_dir/updates/$file_name" ]] || { rm -f "$log"; fail "dpkg 修复备份目录已存在同名文件，拒绝覆盖：$file_name"; }
      mv -- "$bad_file" "$backup_dir/updates/$file_name" || { rm -f "$log"; fail "隔离损坏的 dpkg 临时更新文件失败。"; }
      quarantined_count=$((quarantined_count + 1))
      echo "检测到损坏的 dpkg 临时更新文件：$bad_file"
      echo "已隔离备份到：$backup_dir/updates/$file_name"
      rm -f "$log"
      continue
    fi

    rm -f "$log"
    if (( fix_broken_attempted == 1 )); then
      fail "dpkg 在依赖修复后仍无法完成配置；已停止安装，请检查上方具体软件包错误。"
    fi
    fix_broken_attempted=1
    echo "dpkg 配置未完成，但不是可安全隔离的 updates/NNNN 解析损坏；尝试修复依赖（禁止自动删除软件包）……"
    apt-get \
      -o DPkg::Lock::Timeout=10 \
      -o Acquire::Retries=2 \
      -o Acquire::PDiffs=false \
      -o Acquire::IndexTargets::deb-src::Sources::DefaultEnabled=false \
      update || fail "修复 dpkg 前刷新 APT 索引失败。若提示锁被占用，请等待系统自动更新结束后重试。"
    apt-get \
      -o DPkg::Lock::Timeout=10 \
      -o Acquire::Retries=2 \
      -o Dpkg::Options::=--force-confold \
      --fix-broken --no-remove install -y --no-install-recommends \
      || fail "自动修复损坏依赖失败；为避免误删系统软件包，脚本已停止。"
  done

  (( configured == 1 )) || fail "dpkg 在按 updates/NNNN 实际数量计算的安全重试范围内仍无法完成配置；已停止安装。"
  audit="$(LC_ALL=C dpkg --audit 2>/dev/null || true)"
  if [[ -n "$audit" ]]; then
    echo "dpkg 审计仍发现异常：" >&2
    printf '%s\n' "$audit" >&2
    fail "dpkg 状态仍不完整，已停止安装，未删除任何锁文件或软件包。"
  fi
  echo "dpkg 状态：正常。"
  if [[ -n "$backup_dir" ]]; then
    echo "本次共隔离损坏的 dpkg 临时更新文件：${quarantined_count} 个"
    echo "dpkg 修复备份：$backup_dir"
  fi
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
  curl -fL --retry 5 --retry-all-errors --connect-timeout 15 --max-time 300 "$url" -o "$tmp/caddy.tgz" || fail "下载 Caddy 失败。"
  actual="$(sha256sum "$tmp/caddy.tgz" | awk '{print $1}')"
  [[ "${expected,,}" == "${actual,,}" ]] || fail "Caddy 安装包 SHA-256 校验失败。"
  tar -xzf "$tmp/caddy.tgz" -C "$tmp" caddy
  install -m755 "$tmp/caddy" /usr/local/bin/caddy
  rm -rf "$tmp"
  echo "Caddy 安装完成：$(/usr/local/bin/caddy version)"
}
write_services(){
  cat > /etc/systemd/system/caddy.service <<'EOF_CADDY_UNIT'
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
EOF_CADDY_UNIT
  cat > /etc/systemd/system/vvv-sub.service <<'EOF_SUB_UNIT'
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
EOF_SUB_UNIT
}

[[ $(id -u) -eq 0 ]] || fail "请使用 root 用户运行。"
public_ip="$(jq -r '.public_ip // empty' /etc/jp-relay/state.json 2>/dev/null || true)"
[[ "$public_ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || fail "无法读取本机公网 IPv4。"
domain="${VVV_SUB_DOMAIN:-}"; domain="${domain,,}"; domain="${domain%.}"
port="${VVV_SUB_PORT:-8443}"
transport="${VVV_SUB_TRANSPORT:-direct-https}"
suffix="${VVV_SUB_SUFFIX:-}"
tunnel_token="${VVV_CF_TUNNEL_TOKEN:-}"
if [[ "$RESTORE_MODE" == 1 && -s "$CFG_DIR/config.json" ]]; then
  domain="$(jq -r '.domain // ""' "$CFG_DIR/config.json")"
  port="$(jq -r '.public_port // 8443' "$CFG_DIR/config.json")"
  transport="$(jq -r '.transport_mode // "direct-https"' "$CFG_DIR/config.json")"
  suffix="$(jq -r '.subscription_suffix // ""' "$CFG_DIR/config.json")"
  [[ -s "$CFG_DIR/cloudflared.token" ]] && tunnel_token="$(cat "$CFG_DIR/cloudflared.token")"
fi
valid_port "$port" || fail "订阅服务端口无效。"
[[ "$port" != 443 || "$transport" == tunnel ]] || fail "直连订阅服务端口不能占用代理端口 443。"
[[ -z "$domain" ]] || valid_domain "$domain" || fail "订阅域名格式不正确。"
[[ "$suffix" =~ ^[A-Za-z0-9]{6,32}$ ]] || fail "订阅后缀必须是 6-32 位大小写字母或数字。"
case "${suffix,,}" in health|api|admin|debug) fail "订阅后缀使用了系统保留词。";; esac
case "$transport" in direct-http|direct-https|tunnel);; *) fail "订阅传输模式无效。";; esac
if [[ "$transport" == tunnel ]]; then
  [[ -n "$domain" ]] || fail "Cloudflare Tunnel 模式必须输入订阅域名。"
  [[ -n "$tunnel_token" ]] || fail "Cloudflare Tunnel 模式必须输入 Tunnel Token。"
fi
if [[ -n "$domain" && "$transport" == direct-https ]]; then
  mapfile -t resolved < <(getent ahostsv4 "$domain" | awk '{print $1}' | sort -u)
  ((${#resolved[@]})) || fail "订阅域名尚未解析到 IPv4。"
  printf '%s\n' "${resolved[@]}" | grep -Fxq "$public_ip" || fail "订阅域名没有解析到本机 IP ${public_ip}。"
fi

section "准备订阅中心依赖"
repair_dpkg_state
required=(ca-certificates curl jq openssl python3 tar gzip)
[[ "$transport" != direct-https || -n "$domain" ]] || required+=(python3-venv)
missing=()
for package in "${required[@]}"; do dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed' || missing+=("$package"); done
if ((${#missing[@]})); then
  apt_run "订阅中心依赖安装" env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=10 -o Acquire::Retries=2 install -y "${missing[@]}" || {
    apt_run "APT 索引刷新" apt-get -o DPkg::Lock::Timeout=10 -o Acquire::Retries=2 -o Acquire::PDiffs=false -o Acquire::IndexTargets::deb-src::Sources::DefaultEnabled=false update
    apt_run "订阅中心依赖安装" env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=10 -o Acquire::Retries=2 install -y "${missing[@]}"
  }
else
  echo "订阅中心依赖已齐全，跳过重复 apt update。"
fi

install -d -m700 "$CFG_DIR" "$DATA_DIR" "$DATA_DIR/hosts" "$DATA_DIR/output" "$DATA_DIR/backups" /usr/local/lib/vvv
install -d -m755 /var/www/vvv-acme /etc/caddy
for file in sub_center.py sync_agent.py backup_manager.py rclone_manager.sh client_adapters.py adapter_manager.py center_transport.sh center_manager.sh restore_manager.py diagnostic_report.py node_probe.py; do
  [[ -f "$BASE_DIR/$file" ]] && install -m755 "$BASE_DIR/$file" "/usr/local/lib/vvv/$file"
done
python3 /usr/local/lib/vvv/client_adapters.py >/dev/null || fail "客户端适配器自检失败。"

if [[ "$RESTORE_MODE" != 1 || ! -s "$CFG_DIR/config.json" ]]; then
  master_token="$(openssl rand -hex 32)"
  recovery_password="$(openssl rand -base64 36 | tr -d '\n')"
  python3 - "$CFG_DIR/config.json" "$domain" "$public_ip" "$port" "$transport" "$suffix" "$master_token" "$recovery_password" <<'PY_CONFIG'
import json,os,sys,tempfile
path,domain,ip,port,transport,suffix,master,recovery=sys.argv[1:]
obj={'schema':4,'address_mode':'domain' if domain else 'ip','domain':domain,'public_ip':ip,
 'public_port':int(port),'transport_mode':transport,'subscription_suffix':suffix,'base_url':'',
 'subscription_url':'','api_base_url':f'http://{ip}:18081','listen_host':'0.0.0.0','listen_port':18081,
 'master_token':master,'recovery_password':recovery,'refresh_hours':24}
fd,tmp=tempfile.mkstemp(prefix='.config.',dir=os.path.dirname(path))
with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump(obj,f,ensure_ascii=False,indent=2); f.write('\n')
os.chmod(tmp,0o600); os.replace(tmp,path)
PY_CONFIG
  printf '%s\n' '{"hosts":[]}' > "$DATA_DIR/registry.json"
  printf '%s\n' '{}' > "$DATA_DIR/node-overrides.json"
  chmod 600 "$DATA_DIR/registry.json" "$DATA_DIR/node-overrides.json"
  rm -f "$CFG_DIR/cloud.json" "$CFG_DIR/rclone.conf"
else
  python3 - "$CFG_DIR/config.json" "$public_ip" <<'PY_RESTORE_CONFIG'
import json,os,sys,tempfile
path,ip=sys.argv[1:]
obj=json.load(open(path,encoding='utf-8'))
obj.update(schema=4,public_ip=ip,api_base_url=f'http://{ip}:18081',listen_host='0.0.0.0',listen_port=18081)
fd,tmp=tempfile.mkstemp(prefix='.config.',dir=os.path.dirname(path))
with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump(obj,f,ensure_ascii=False,indent=2); f.write('\n')
os.chmod(tmp,0o600); os.replace(tmp,path)
PY_RESTORE_CONFIG
  [[ -s "$DATA_DIR/registry.json" ]] || printf '%s\n' '{"hosts":[]}' > "$DATA_DIR/registry.json"
  [[ -s "$DATA_DIR/node-overrides.json" ]] || printf '%s\n' '{}' > "$DATA_DIR/node-overrides.json"
fi
if [[ "$transport" == tunnel ]]; then printf '%s' "$tunnel_token" > "$CFG_DIR/cloudflared.token"; chmod 600 "$CFG_DIR/cloudflared.token"; fi

section "安装订阅中心前端"
install_caddy
id caddy >/dev/null 2>&1 || useradd --system --home /var/lib/caddy --shell /usr/sbin/nologin caddy
install -d -o caddy -g caddy -m750 /var/lib/caddy /var/log/caddy
install -d -o caddy -g caddy -m700 /etc/caddy/certs
write_services
open_port "$SERVICE_PORT" tcp
systemctl daemon-reload
ensure_service vvv-sub.service restart 60
/usr/local/lib/vvv/center_transport.sh apply-initial || fail "订阅传输前端安装失败。"
curl -fsS --connect-timeout 2 --max-time 4 "http://127.0.0.1:${SERVICE_PORT}/health" >/dev/null || fail "订阅中心内部服务未就绪。"
install -m700 "$BASE_DIR/center_manager.sh" /usr/local/sbin/vvv-center
python3 /usr/local/lib/vvv/backup_manager.py create "$([[ "$RESTORE_MODE" == 1 ]] && echo restored-center || echo first-install)" --force >/dev/null || true

printf '\n订阅中心安装成功，总耗时 %s 秒。\n' "$((SECONDS-CENTER_STARTED))"
/usr/local/sbin/vvv-center url 2>/dev/null || true
/usr/local/lib/vvv/center_transport.sh rewrite-registration
if [[ "$transport" == direct-http ]]; then
  printf '\n\033[33;1m警告：HTTP 仅限调试使用，请勿长期使用。\033[0m\n'
  echo "节点 UUID、密码和订阅内容将以明文传输。"
fi

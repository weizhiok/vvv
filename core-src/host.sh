#!/usr/bin/env bash
# 构建编号：040203（日本主机，多 VPS 兼容修复 + Hysteria 2 限速 50 Mbps）
# 构建版本：213222；基于 040203，新增 HTTP/HTTPS/SOCKS5 上游中转与 Loon 优先输出。
# 可作为文件执行，也可整段粘贴到 SSH 终端。
# 首次运行只询问协议模式和统一端口；选择后全自动安装。
umask 077
VVV_PREPARED_SOURCE=1

if [[ "$(id -u)" -ne 0 ]]; then
  echo "错误：请使用 root 用户执行。" >&2
  exit 1
fi

mkdir -p /usr/local/sbin
cat > /usr/local/sbin/jp-relay-manager <<'JP_RELAY_JPR3_MANAGER_EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

RUN_MODE="${1:-}"
RUN_ARG="${2:-}"

STATE_DIR="/etc/jp-relay"
STATE_FILE="${STATE_DIR}/state.json"
PACKAGE_ROOT="/root/relay-packages"
TLS_DIR="/etc/sing-box/tls"
LOCK_FILE="/run/lock/jp-relay-manager.lock"

XRAY="/usr/local/bin/xray"
XRAY_CFG="/usr/local/etc/xray/config.json"
XRAY_FALLBACK_VERSION="26.3.27"
XRAY_VERSION="$XRAY_FALLBACK_VERSION"
XRAY_VERSION_SOURCE="备用稳定版"

SING_BOX="/usr/local/bin/sing-box"
SING_CFG="/etc/sing-box/config.json"
SING_BOX_FALLBACK_VERSION="1.13.12"
SING_BOX_VERSION="$SING_BOX_FALLBACK_VERSION"
SING_BOX_VERSION_SOURCE="备用稳定版"

# Hysteria 2 每条连接及中转链路的上下行硬上限（Mbps）
HY2_LIMIT_MBPS="${VVV_HY2_LIMIT_MBPS:-50}"

DEFAULT_SNI="${VVV_REALITY_SNI:-www.softbank.jp}"
UPGRADE_MARKER="/var/lib/jp-relay/japan-system-upgrade.done"

CURRENT_STEP="启动"
TMP_FILES=()
INSTALL_CANCELLED=0
MANAGER_EXIT_REQUESTED=0
IS_CONTAINER=0
VIRT_TYPE="unknown"

VLESS_STATUS="未启用"
VLESS_REASON=""
VLESS_TIME=""
VLESS_EXIT_IP=""
HY2_STATUS="未启用"
HY2_REASON=""
HY2_TIME=""
HY2_EXIT_IP=""
UPSTREAM_STATUS="未检测"
UPSTREAM_REASON=""
UPSTREAM_TIME=""
UPSTREAM_EXIT_IP=""

cleanup_temp() {
  local item
  for item in "${TMP_FILES[@]:-}"; do
    [[ -n "$item" ]] && rm -rf -- "$item" 2>/dev/null || true
  done
}

on_exit() {
  local rc=$?
  cleanup_temp
  if (( rc != 0 )); then
    echo
    echo "[失败] 步骤：${CURRENT_STEP}"
    echo "[失败] 行号：${BASH_LINENO[0]:-未知}，退出码：${rc}"
    echo "脚本不会主动关闭当前 SSH，也不会立即重启整台服务器。"
  fi
}
trap on_exit EXIT

log() {
  printf '\n\033[1;36m========== %s ==========\033[0m\n' "$1"
}

fail() {
  echo "错误：$*" >&2
  return 1
}

retry() {
  local attempts="$1" delay="$2"
  shift 2
  local n=1
  until "$@"; do
    if (( n >= attempts )); then
      return 1
    fi
    echo "命令执行失败，${delay} 秒后重试（${n}/${attempts}）……"
    sleep "$delay"
    ((n++))
  done
}

valid_ipv4() {
  python3 - "$1" <<'PY_VALID_IP'
import ipaddress, sys
try:
    ip = ipaddress.ip_address(sys.argv[1])
except ValueError:
    raise SystemExit(1)
raise SystemExit(0 if ip.version == 4 and not ip.is_unspecified else 1)
PY_VALID_IP
}

valid_port() {
  [[ "${1:-}" =~ ^[0-9]+$ ]] || return 1
  (( 10#$1 >= 1 && 10#$1 <= 65535 ))
}

parse_upstream_spec() {
  local raw="$1" output="$2"
  python3 - "$raw" "$output" <<'PY_PARSE_UPSTREAM'
import csv
import ipaddress
import json
import re
import sys
from pathlib import Path

raw, output = sys.argv[1:]

# 中文冒号只在引号外视作分隔符；引号内的中英文冒号都属于字段内容。
normalized=[]
in_quotes=False
i=0
while i < len(raw):
    ch=raw[i]
    if ch == '"':
        if in_quotes and i + 1 < len(raw) and raw[i+1] == '"':
            normalized.extend(['"','"'])
            i += 2
            continue
        in_quotes = not in_quotes
        normalized.append(ch)
    elif ch == '：' and not in_quotes:
        normalized.append(':')
    else:
        normalized.append(ch)
    i += 1
if in_quotes:
    raise SystemExit('双引号没有成对闭合。')

try:
    rows=list(csv.reader([''.join(normalized)], delimiter=':', quotechar='"', doublequote=True, strict=True))
except csv.Error as exc:
    raise SystemExit(f'线路格式无法解析：{exc}')
if len(rows) != 1 or len(rows[0]) != 4:
    raise SystemExit('必须解析为四项：主机、端口、用户名、密码；字段内冒号请使用英文双引号包裹。')

host, port_text, username, password = rows[0]
host=host.strip()
port_text=port_text.strip()
username=username.strip()
password=password.strip()

for label, value in [('主机',host),('端口',port_text),('用户名',username),('密码',password)]:
    if not value:
        raise SystemExit(f'{label}不能为空。')
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise SystemExit(f'{label}不能包含控制字符。')

if not port_text.isdigit():
    raise SystemExit('端口必须是纯数字。')
port=int(port_text)
if not 1 <= port <= 65535:
    raise SystemExit('端口必须在 1–65535 范围内。')

try:
    ip=ipaddress.ip_address(host)
except ValueError:
    try:
        ascii_host=host.encode('idna').decode('ascii').rstrip('.')
    except UnicodeError:
        raise SystemExit('主机不是有效域名或 IPv4 地址。')
    if len(ascii_host) > 253:
        raise SystemExit('域名长度超出限制。')
    labels=ascii_host.split('.')
    label_re=re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$')
    if len(labels) < 2 or any(not label_re.fullmatch(x) for x in labels):
        raise SystemExit('主机不是有效域名或 IPv4 地址。')
    host=ascii_host.lower()
else:
    if ip.version != 4 or ip.is_unspecified:
        raise SystemExit('只支持有效 IPv4 地址或域名。')
    host=str(ip)

Path(output).write_text(json.dumps({
    'host':host,'port':port,'username':username,'password':password
},ensure_ascii=False)+'\n',encoding='utf-8')
PY_PARSE_UPSTREAM
}

proxy_url_for_curl() {
  local protocol="$1" host="$2" port="$3" username="$4" password="$5"
  python3 - "$protocol" "$host" "$port" "$username" "$password" <<'PY_PROXY_URL'
import sys
from urllib.parse import quote
protocol,host,port,user,password=sys.argv[1:]
scheme='http' if protocol=='http' else 'socks5h'
print(f"{scheme}://{quote(user,safe='')}:{quote(password,safe='')}@{host}:{port}")
PY_PROXY_URL
}

probe_external_upstream() {
  local protocol="$1" host="$2" port="$3" username="$4" password="$5"
  local proxy_url url ip result code seconds err last_error="" japan_ip
  proxy_url="$(proxy_url_for_curl "$protocol" "$host" "$port" "$username" "$password")"
  japan_ip="$(jq -r '.public_ip' "$STATE_FILE")"
  PROBE_TIME=""; PROBE_EXIT_IP=""; PROBE_REASON=""
  for url in https://api.ipify.org https://ipv4.icanhazip.com; do
    err="$(mktemp /tmp/jp-upstream-direct.XXXXXX)"
    TMP_FILES+=("$err")
    ip="$(curl -4fsS --proxy "$proxy_url" --connect-timeout 8 --max-time 25 "$url" 2>"$err" | tr -d '[:space:]' || true)"
    if valid_ipv4 "$ip"; then
      PROBE_EXIT_IP="$ip"
      if [[ "$ip" == "$japan_ip" ]]; then
        PROBE_REASON="上游返回的出口仍是日本 VPS 公网 IP，疑似没有经过动态代理。"
        return 1
      fi
      break
    fi
    last_error="$(tr '\n' ' ' < "$err" | sed 's/[[:space:]]\+/ /g')"
  done
  valid_ipv4 "$PROBE_EXIT_IP" || { PROBE_REASON="无法通过上游代理获取出口 IPv4：${last_error:-未知错误}"; return 1; }
  for url in https://www.gstatic.com/generate_204 https://www.google.com/generate_204; do
    err="$(mktemp /tmp/jp-upstream-http.XXXXXX)"
    TMP_FILES+=("$err")
    if result="$(curl -sS --proxy "$proxy_url" --connect-timeout 8 --max-time 25 -o /dev/null -w '%{http_code}|%{time_total}' "$url" 2>"$err")"; then
      code="${result%%|*}"; seconds="${result#*|}"
      if [[ "$code" == "204" ]]; then
        PROBE_TIME="$(awk -v t="$seconds" 'BEGIN{printf "%.0f",t*1000}')"
        return 0
      fi
      last_error="${url} 返回 HTTP ${code}"
    else
      last_error="$(tr '\n' ' ' < "$err" | sed 's/[[:space:]]\+/ /g')"
    fi
  done
  PROBE_REASON="${last_error:-上游代理无法访问检测网站。}"
  return 1
}

detect_public_ipv4() {
  local ip url
  for url in https://api.ipify.org https://ipv4.icanhazip.com https://ifconfig.me/ip; do
    ip="$(curl -4fsS --retry 2 --connect-timeout 6 --max-time 12 "$url" 2>/dev/null | tr -d '[:space:]' || true)"
    if [[ -n "$ip" ]] && valid_ipv4 "$ip"; then
      printf '%s' "$ip"
      return 0
    fi
  done
  ip="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')"
  [[ -n "$ip" ]] && valid_ipv4 "$ip" && { printf '%s' "$ip"; return 0; }
  return 1
}

detect_country_code() {
  local ip="$1" code json
  json="$(curl -4fsS --connect-timeout 4 --max-time 7 "https://ipwho.is/${ip}?fields=success,country_code" 2>/dev/null || true)"
  code="$(printf '%s' "$json" | jq -r 'select(.success==true) | .country_code // empty' 2>/dev/null | head -n1 | tr '[:lower:]' '[:upper:]' | tr -d '[:space:]')"
  if [[ "$code" =~ ^[A-Z]{2}$ ]]; then
    printf '%s' "$code"
    return 0
  fi
  code="$(curl -4fsS --connect-timeout 4 --max-time 7 "https://ipapi.co/${ip}/country/" 2>/dev/null | head -n1 | tr '[:lower:]' '[:upper:]' | tr -d '[:space:]' || true)"
  if [[ "$code" =~ ^[A-Z]{2}$ ]]; then
    printf '%s' "$code"
    return 0
  fi
  return 1
}

check_debian() {
  [[ -r /etc/os-release ]] || fail "无法读取 /etc/os-release。"
  # shellcheck disable=SC1091
  source /etc/os-release
  [[ "${ID:-}" == "debian" && "${VERSION_ID:-}" == "13" ]] || fail "主机脚本仅支持 Debian 13。当前系统：${PRETTY_NAME:-未知}"
  command -v systemctl >/dev/null 2>&1 || fail "当前 Debian 没有 systemctl，无法管理代理服务。"
  [[ "$(cat /proc/1/comm 2>/dev/null | tr -d '[:space:]')" == "systemd" ]] || fail "当前系统不是以 systemd 作为 PID 1，主机脚本无法安全安装服务。"

  if command -v systemd-detect-virt >/dev/null 2>&1; then
    VIRT_TYPE="$(systemd-detect-virt 2>/dev/null || echo none)"
    if systemd-detect-virt --container --quiet 2>/dev/null; then
      IS_CONTAINER=1
    fi
  elif [[ -e /.dockerenv ]] || grep -qiE 'docker|lxc|containerd|kubepods|podman' /proc/1/cgroup 2>/dev/null; then
    IS_CONTAINER=1
    VIRT_TYPE="container"
  fi

  echo "系统：${PRETTY_NAME}"
  echo "架构：$(uname -m)"
  echo "虚拟化：${VIRT_TYPE}"
  (( IS_CONTAINER == 0 )) || echo "提示：检测到受限容器，Swap、BBR 和定时重启将按环境能力尽力配置。"
}

upgrade_system_once() {
  export DEBIAN_FRONTEND=noninteractive
  export NEEDRESTART_MODE=a

  # 为了兼容不同 VPS 镜像，不执行 full-upgrade：它可能替换内核、GRUB、网络组件或 SSH。
  # 代理运行所需组件单独安装即可，降低一次性安装失败和重启后无法启动的风险。
  echo "APT/dpkg 锁最多等待 10 秒；超时立即报错，不删除锁，也不终止系统自动更新。"
  apt-get \
    -o DPkg::Lock::Timeout=10 \
    -o Acquire::Retries=2 \
    -o Acquire::PDiffs=false \
    -o Acquire::IndexTargets::deb-src::Sources::DefaultEnabled=false \
    update || fail "APT 更新失败。若提示锁被占用，已等待最多 10 秒，请稍后重新运行。"
  apt-get \
    -o DPkg::Lock::Timeout=10 \
    -o Acquire::Retries=2 \
    install -y --no-install-recommends \
    ca-certificates curl unzip tar gzip openssl jq python3 python3-venv iproute2 procps \
    tzdata kmod util-linux || fail "代理依赖安装失败。若提示锁被占用，已等待最多 10 秒，请稍后重新运行。"
  update-ca-certificates >/dev/null 2>&1 || true
  echo "系统核心组件保持 VPS 镜像原版本，仅安装代理所需依赖。"
}

configure_swap() {
  local current_swap_kb free_kb root_fstype swap_dir swap_path created
  current_swap_kb="$(awk 'NR>1{s+=$3}END{print s+0}' /proc/swaps)"
  if (( current_swap_kb >= 524288 )); then
    echo "已有 Swap，保持不变。"
    return 0
  fi
  if (( IS_CONTAINER == 1 )); then
    echo "受限容器通常不允许自行创建 Swap，已跳过。"
    return 0
  fi

  free_kb="$(df -Pk / | awk 'NR==2{print $4}')"
  if [[ ! "$free_kb" =~ ^[0-9]+$ ]] || (( free_kb < 1400000 )); then
    echo "磁盘空间不足以安全创建 1 GiB Swap，已跳过。"
    return 0
  fi

  root_fstype="$(findmnt -n -o FSTYPE / 2>/dev/null || true)"
  case "$root_fstype" in
    overlay|aufs|squashfs|tmpfs|ramfs|fuse.*)
      echo "根文件系统 ${root_fstype:-未知} 不适合由脚本创建 Swap，已跳过。"
      return 0
      ;;
  esac

  # 不触碰 VPS 镜像或控制面板可能保护的 /swapfile，使用本脚本独立目录。
  swap_dir="/var/lib/jp-relay-swap"
  swap_path="${swap_dir}/swapfile"
  if ! install -d -m 700 "$swap_dir" 2>/dev/null; then
    echo "警告：无法创建独立 Swap 目录，已跳过 Swap，不影响代理安装。"
    return 0
  fi

  if awk 'NR>1{print $1}' /proc/swaps | grep -Fxq "$swap_path"; then
    echo "脚本专用 Swap 已启用，保持不变。"
    return 0
  fi

  if [[ -e "$swap_path" ]] && ! rm -f -- "$swap_path" 2>/dev/null; then
    echo "警告：脚本专用 Swap 文件不可修改，已跳过 Swap，不影响代理安装。"
    return 0
  fi

  echo "创建 1 GiB 独立 Swap：${swap_path}"
  created=0
  if command -v fallocate >/dev/null 2>&1 && fallocate -l 1G "$swap_path" 2>/dev/null; then
    created=1
  elif dd if=/dev/zero of="$swap_path" bs=1M count=1024 status=none 2>/dev/null; then
    created=1
  fi
  if (( created == 0 )); then
    rm -f -- "$swap_path" 2>/dev/null || true
    echo "警告：当前文件系统无法创建 Swap 文件，已跳过，不影响代理安装。"
    return 0
  fi

  if ! chmod 600 "$swap_path" 2>/dev/null || \
     ! mkswap "$swap_path" >/dev/null 2>&1 || \
     ! swapon "$swap_path" >/dev/null 2>&1; then
    swapoff "$swap_path" >/dev/null 2>&1 || true
    rm -f -- "$swap_path" 2>/dev/null || true
    echo "警告：当前 VPS 不允许启用文件 Swap，已跳过，不影响代理安装。"
    return 0
  fi

  if ! grep -qF "${swap_path} none swap sw 0 0" /etc/fstab 2>/dev/null; then
    printf '%s\n' "${swap_path} none swap sw 0 0" >> /etc/fstab
  fi
  cat > /etc/sysctl.d/99-jp-relay-memory.conf <<'EOF_MEMORY'
vm.swappiness = 20
vm.vfs_cache_pressure = 100
EOF_MEMORY
  sysctl -p /etc/sysctl.d/99-jp-relay-memory.conf >/dev/null 2>&1 || true
  echo "Swap：已启用 ${swap_path}"
}

configure_network_tuning() {
  mkdir -p /etc/modules-load.d /etc/sysctl.d
  echo tcp_bbr > /etc/modules-load.d/99-bbr.conf
  modprobe tcp_bbr >/dev/null 2>&1 || true
  modprobe sch_fq >/dev/null 2>&1 || true
  cat > /etc/sysctl.d/99-jp-relay-network.conf <<'EOF_NETWORK'
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.core.netdev_max_backlog = 250000
EOF_NETWORK
  sysctl --system >/dev/null 2>&1 || true
  local available current_cc current_qdisc
  available="$(sysctl -n net.ipv4.tcp_available_congestion_control 2>/dev/null || true)"
  current_cc="$(sysctl -n net.ipv4.tcp_congestion_control 2>/dev/null || true)"
  current_qdisc="$(sysctl -n net.core.default_qdisc 2>/dev/null || true)"
  if grep -qw bbr <<< "$available"; then
    sysctl -w net.ipv4.tcp_congestion_control=bbr >/dev/null 2>&1 || true
    sysctl -w net.core.default_qdisc=fq >/dev/null 2>&1 || true
    current_cc="$(sysctl -n net.ipv4.tcp_congestion_control 2>/dev/null || true)"
    current_qdisc="$(sysctl -n net.core.default_qdisc 2>/dev/null || true)"
    echo "BBR：${current_cc:-未知} / 队列=${current_qdisc:-未知}"
  else
    echo "警告：当前内核没有提供 BBR；配置已写入，后续重启使用新内核时会再次尝试。"
    echo "当前可用拥塞控制：${available:-未知}"
  fi
  echo "UDP 缓冲区：rmem_max=$(sysctl -n net.core.rmem_max 2>/dev/null || echo 未知)，wmem_max=$(sysctl -n net.core.wmem_max 2>/dev/null || echo 未知)"
}

configure_timezone_and_daily_reboot() {
  [[ -f /usr/share/zoneinfo/Asia/Shanghai ]] || fail "Asia/Shanghai 时区文件不存在。"
  ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime
  echo 'Asia/Shanghai' > /etc/timezone
  timedatectl set-timezone Asia/Shanghai >/dev/null 2>&1 || true

  cat > /etc/systemd/system/daily-reboot.service <<'EOF_REBOOT_SERVICE'
[Unit]
Description=Daily reboot at 06:00 Asia/Shanghai

[Service]
Type=oneshot
ExecStart=/usr/bin/systemctl reboot
EOF_REBOOT_SERVICE
  cat > /etc/systemd/system/daily-reboot.timer <<'EOF_REBOOT_TIMER'
[Unit]
Description=Daily reboot timer at 06:00 Asia/Shanghai

[Timer]
OnCalendar=*-*-* 06:00:00
AccuracySec=1min
RandomizedDelaySec=0
Persistent=false
Unit=daily-reboot.service

[Install]
WantedBy=timers.target
EOF_REBOOT_TIMER
  if systemctl daemon-reload >/dev/null 2>&1 && \
     systemctl enable --now daily-reboot.timer >/dev/null 2>&1 && \
     systemctl is-active --quiet daily-reboot.timer; then
    echo "每日自动重启：北京时间 06:00"
  else
    echo "警告：当前 VPS 不允许启用自动重启定时器，代理安装将继续。"
  fi
  echo "当前时间：$(date '+%F %T %Z %z')"
}

prompt_initial_mode_and_port() {
  local preset_mode="${VVV_PROTOCOL_MODE:-dual}" preset_port="${VVV_PROXY_PORT:-443}"
  case "$preset_mode" in dual|vless|hy2) INSTALL_MODE="$preset_mode";; *) fail "预设协议模式无效：$preset_mode"; return 1;; esac
  valid_port "$preset_port" || { fail "预设代理端口无效：$preset_port"; return 1; }
  INSTALL_PORT="$((10#$preset_port))"
  [[ "$INSTALL_MODE" == hy2 ]] || [[ "$DEFAULT_SNI" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ ]] || { fail "REALITY 伪装域名格式无效：$DEFAULT_SNI"; return 1; }
  echo "已选择模式：$INSTALL_MODE"
  echo "统一监听端口：TCP/UDP ${INSTALL_PORT}（仅启用所选协议）"
  [[ "$INSTALL_MODE" == hy2 ]] || echo "REALITY 伪装域名：$DEFAULT_SNI"
}

mode_has_vless() {
  local mode="${1:-$(jq -r '.protocol_mode' "$STATE_FILE" 2>/dev/null)}"
  [[ "$mode" == "dual" || "$mode" == "vless" ]]
}

mode_has_hy2() {
  local mode="${1:-$(jq -r '.protocol_mode' "$STATE_FILE" 2>/dev/null)}"
  [[ "$mode" == "dual" || "$mode" == "hy2" ]]
}

resolve_latest_stable_version() {
  local repo="$1" fallback="$2" json tag
  json="$(curl -fsSL \
    --connect-timeout 8 \
    --max-time 25 \
    -H 'Accept: application/vnd.github+json' \
    -H 'User-Agent: jp-relay-installer' \
    "https://api.github.com/repos/${repo}/releases/latest" 2>/dev/null || true)"
  tag="$(printf '%s' "$json" | jq -r 'select(.draft==false and .prerelease==false) | .tag_name // empty' 2>/dev/null | head -n1)"
  tag="${tag#v}"
  if [[ "$tag" =~ ^[0-9]+([.][0-9]+){2,}$ ]]; then
    printf '%s' "$tag"
  else
    printf '%s' "$fallback"
    return 1
  fi
}

resolve_core_versions() {
  local detected
  if detected="$(resolve_latest_stable_version 'XTLS/Xray-core' "$XRAY_FALLBACK_VERSION")"; then
    XRAY_VERSION="$detected"
    XRAY_VERSION_SOURCE="官方最新稳定版"
  else
    XRAY_VERSION="$XRAY_FALLBACK_VERSION"
    XRAY_VERSION_SOURCE="备用稳定版（版本查询失败）"
  fi
  if detected="$(resolve_latest_stable_version 'SagerNet/sing-box' "$SING_BOX_FALLBACK_VERSION")"; then
    SING_BOX_VERSION="$detected"
    SING_BOX_VERSION_SOURCE="官方最新稳定版"
  else
    SING_BOX_VERSION="$SING_BOX_FALLBACK_VERSION"
    SING_BOX_VERSION_SOURCE="备用稳定版（版本查询失败）"
  fi
  echo "Xray-core：v${XRAY_VERSION}（${XRAY_VERSION_SOURCE}）"
  echo "sing-box：v${SING_BOX_VERSION}（${SING_BOX_VERSION_SOURCE}）"
}

xray_archive_name() {
  case "$(uname -m)" in
    x86_64|amd64) echo 'Xray-linux-64.zip' ;;
    aarch64|arm64) echo 'Xray-linux-arm64-v8a.zip' ;;
    *) fail "Xray 不支持当前 CPU 架构：$(uname -m)" ;;
  esac
}

sing_box_archive_name_for_version() {
  local version="$1"
  case "$(uname -m)" in
    x86_64|amd64) echo "sing-box-${version}-linux-amd64.tar.gz" ;;
    aarch64|arm64) echo "sing-box-${version}-linux-arm64.tar.gz" ;;
    *) fail "sing-box 不支持当前 CPU 架构：$(uname -m)" ;;
  esac
}

install_xray_version() {
  local version="$1" archive tmp zip dgst url expected actual detected
  archive="$(xray_archive_name)" || return 1
  tmp="$(mktemp -d /tmp/xray-japan.XXXXXX)" || return 1
  TMP_FILES+=("$tmp")
  zip="${tmp}/${archive}"
  dgst="${zip}.dgst"
  url="https://github.com/XTLS/Xray-core/releases/download/v${version}/${archive}"
  echo "下载 Xray v${version}：${archive}"
  retry 5 5 curl -fL --connect-timeout 10 --max-time 180 -o "$zip" "$url" || return 1
  retry 5 5 curl -fL --connect-timeout 10 --max-time 60 -o "$dgst" "${url}.dgst" || return 1
  expected="$(grep -Eo '[0-9a-fA-F]{64}' "$dgst" | head -n1 | tr 'A-F' 'a-f')"
  actual="$(sha256sum "$zip" | awk '{print $1}')"
  [[ -n "$expected" && "$expected" == "$actual" ]] || { echo "Xray v${version} SHA256 校验失败。" >&2; return 1; }
  unzip -q "$zip" -d "$tmp" || return 1
  [[ -x "$tmp/xray" ]] || { echo "Xray v${version} 压缩包中没有可执行文件。" >&2; return 1; }
  detected="$("$tmp/xray" version 2>/dev/null | awk 'NR==1{print $2}')"
  [[ "$detected" == "$version" ]] || { echo "Xray 二进制版本校验失败：期望 ${version}，实际 ${detected:-未知}。" >&2; return 1; }
  systemctl stop xray >/dev/null 2>&1 || true
  install -d -m 755 /usr/local/bin
  install -m 755 "$tmp/xray" "$XRAY"
  "$XRAY" version | head -n2
}

install_xray_binary() {
  local current requested="$XRAY_VERSION"
  current=""
  [[ ! -x "$XRAY" ]] || current="$("$XRAY" version 2>/dev/null | awk 'NR==1{print $2}')"
  if [[ "$current" == "$requested" ]]; then
    echo "Xray v${requested} 已安装，复用现有二进制。"
    return 0
  fi
  if install_xray_version "$requested"; then
    return 0
  fi
  if [[ "$requested" != "$XRAY_FALLBACK_VERSION" ]]; then
    echo "Xray 最新稳定版 v${requested} 下载或校验失败，自动回退到 v${XRAY_FALLBACK_VERSION}。" >&2
    XRAY_VERSION="$XRAY_FALLBACK_VERSION"
    XRAY_VERSION_SOURCE="备用稳定版（最新版安装失败）"
    install_xray_version "$XRAY_VERSION" || fail "Xray 最新版和备用版均安装失败。"
    return 0
  fi
  fail "Xray v${requested} 安装失败。"
}

install_sing_box_version() {
  local version="$1" archive tmp tarball release_json asset_url expected actual binary detected
  archive="$(sing_box_archive_name_for_version "$version")" || return 1
  tmp="$(mktemp -d /tmp/sing-box-japan.XXXXXX)" || return 1
  TMP_FILES+=("$tmp")
  tarball="${tmp}/${archive}"
  release_json="${tmp}/release.json"
  retry 5 5 curl -fL --connect-timeout 10 --max-time 60 \
    -H 'Accept: application/vnd.github+json' \
    -H 'User-Agent: jp-relay-installer' \
    -o "$release_json" \
    "https://api.github.com/repos/SagerNet/sing-box/releases/tags/v${version}" || return 1
  asset_url="$(jq -er --arg n "$archive" '.assets[] | select(.name==$n) | .browser_download_url' "$release_json" 2>/dev/null)" || return 1
  expected="$(jq -r --arg n "$archive" '.assets[] | select(.name==$n) | (.digest // "") | sub("^sha256:";"")' "$release_json")"
  [[ "$expected" =~ ^[0-9a-fA-F]{64}$ ]] || { echo "GitHub 没有返回 sing-box v${version} 的 SHA256 摘要。" >&2; return 1; }
  retry 5 5 curl -fL --connect-timeout 10 --max-time 180 -o "$tarball" "$asset_url" || return 1
  actual="$(sha256sum "$tarball" | awk '{print $1}')"
  [[ "${expected,,}" == "${actual,,}" ]] || { echo "sing-box v${version} SHA256 校验失败。" >&2; return 1; }
  tar -xzf "$tarball" -C "$tmp" || return 1
  binary="$(find "$tmp" -type f -name sing-box -perm /111 | head -n1)"
  [[ -n "$binary" ]] || { echo "sing-box v${version} 压缩包中没有可执行文件。" >&2; return 1; }
  detected="$("$binary" version 2>/dev/null | awk '/sing-box version/{print $3; exit}')"
  [[ "$detected" == "$version" ]] || { echo "sing-box 二进制版本校验失败：期望 ${version}，实际 ${detected:-未知}。" >&2; return 1; }
  systemctl stop sing-box >/dev/null 2>&1 || true
  install -d -m 755 /usr/local/bin
  install -m 755 "$binary" "$SING_BOX"
  "$SING_BOX" version | head -n3
}

install_sing_box_binary() {
  local current requested="$SING_BOX_VERSION"
  current=""
  [[ ! -x "$SING_BOX" ]] || current="$("$SING_BOX" version 2>/dev/null | awk '/sing-box version/{print $3; exit}')"
  if [[ "$current" == "$requested" ]]; then
    echo "sing-box v${requested} 已安装，复用现有二进制。"
    return 0
  fi
  if install_sing_box_version "$requested"; then
    return 0
  fi
  if [[ "$requested" != "$SING_BOX_FALLBACK_VERSION" ]]; then
    echo "sing-box 最新稳定版 v${requested} 下载或校验失败，自动回退到 v${SING_BOX_FALLBACK_VERSION}。" >&2
    SING_BOX_VERSION="$SING_BOX_FALLBACK_VERSION"
    SING_BOX_VERSION_SOURCE="备用稳定版（最新版安装失败）"
    install_sing_box_version "$SING_BOX_VERSION" || fail "sing-box 最新版和备用版均安装失败。"
    return 0
  fi
  fail "sing-box v${requested} 安装失败。"
}

create_xray_service() {
  getent group xray >/dev/null 2>&1 || groupadd --system xray
  id xray >/dev/null 2>&1 || useradd --system --gid xray --no-create-home --shell /usr/sbin/nologin xray
  install -d -o root -g xray -m 750 /usr/local/etc/xray
  cat > /etc/systemd/system/xray.service <<'EOF_XRAY_SERVICE'
[Unit]
Description=Xray Japan VLESS Relay Service
After=network-online.target nss-lookup.target
Wants=network-online.target

[Service]
User=xray
Group=xray
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
AmbientCapabilities=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
Environment=GOMEMLIMIT=512MiB
Environment=GOGC=50
ExecStart=/usr/local/bin/xray run -format=json -config /usr/local/etc/xray/config.json
Restart=on-failure
RestartSec=3s
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF_XRAY_SERVICE
  systemctl daemon-reload
  systemctl enable xray >/dev/null
}

create_sing_box_service() {
  getent group sing-box >/dev/null 2>&1 || groupadd --system sing-box
  id sing-box >/dev/null 2>&1 || useradd --system --gid sing-box --no-create-home --shell /usr/sbin/nologin sing-box
  install -d -o root -g sing-box -m 750 /etc/sing-box
  install -d -o root -g sing-box -m 750 "$TLS_DIR"
  cat > /etc/systemd/system/sing-box.service <<'EOF_SING_SERVICE'
[Unit]
Description=sing-box Japan Hysteria 2 Relay Service
After=network-online.target nss-lookup.target
Wants=network-online.target

[Service]
User=sing-box
Group=sing-box
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
AmbientCapabilities=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
Environment=GOMEMLIMIT=512MiB
Environment=GOGC=50
ExecStart=/usr/local/bin/sing-box run -c /etc/sing-box/config.json
Restart=on-failure
RestartSec=3s
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF_SING_SERVICE
  install -d -o root -g sing-box -m 750 /etc/vvv-slots/hy2
  cat > /etc/systemd/system/vvv-hy2-slot@.service <<'EOF_HY2_SLOT_SERVICE'
[Unit]
Description=VVV Hysteria 2 relay slot %i
After=network-online.target
Wants=network-online.target

[Service]
User=sing-box
Group=sing-box
NoNewPrivileges=true
Environment=GOMEMLIMIT=128MiB
Environment=GOGC=50
ExecStart=/usr/local/bin/sing-box run -c /etc/vvv-slots/hy2/%i.json
Restart=on-failure
RestartSec=2s
LimitNOFILE=262144

[Install]
WantedBy=multi-user.target
EOF_HY2_SLOT_SERVICE
  systemctl daemon-reload
  systemctl enable sing-box >/dev/null
}

parse_x25519_keys() {
  local output="$1" derived
  GENERATED_PRIVATE_KEY="$(printf '%s\n' "$output" | sed -n 's/^PrivateKey:[[:space:]]*//p' | head -n1 | tr -d '\r')"
  GENERATED_PUBLIC_KEY="$(printf '%s\n' "$output" | sed -n -E -e 's/^Password( \(PublicKey\))?:[[:space:]]*//p' -e 's/^PublicKey:[[:space:]]*//p' | head -n1 | tr -d '\r')"
  if [[ -z "$GENERATED_PUBLIC_KEY" && -n "$GENERATED_PRIVATE_KEY" ]]; then
    derived="$("$XRAY" x25519 -i "$GENERATED_PRIVATE_KEY")"
    GENERATED_PUBLIC_KEY="$(printf '%s\n' "$derived" | sed -n -E -e 's/^Password( \(PublicKey\))?:[[:space:]]*//p' -e 's/^PublicKey:[[:space:]]*//p' | head -n1 | tr -d '\r')"
  fi
  [[ -n "$GENERATED_PRIVATE_KEY" ]] || fail "生成 REALITY 私钥失败。"
  [[ -n "$GENERATED_PUBLIC_KEY" ]] || fail "生成 REALITY 公钥失败。"
}

random_secret() {
  openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\n'
}

new_uuid() {
  if [[ -r /proc/sys/kernel/random/uuid ]]; then
    cat /proc/sys/kernel/random/uuid
  else
    python3 - <<'PY_UUID'
import uuid
print(uuid.uuid4())
PY_UUID
  fi
}

generate_certificate() {
  local server_name="$1" cert="$2" key="$3"
  mkdir -p "$(dirname "$cert")"
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
    -sha256 -nodes -days 3650 \
    -subj "/CN=${server_name}" \
    -addext "subjectAltName=DNS:${server_name}" \
    -addext "basicConstraints=critical,CA:FALSE" \
    -addext "keyUsage=critical,digitalSignature" \
    -addext "extendedKeyUsage=serverAuth" \
    -keyout "$key" -out "$cert" >/dev/null 2>&1
  chmod 640 "$cert" "$key"
}

certificate_metadata_json() {
  local cert="$1"
  python3 - "$cert" <<'PY_CERT_META'
import json, subprocess, sys
cert=sys.argv[1]
fingerprint=subprocess.check_output(
    ["openssl","x509","-in",cert,"-noout","-fingerprint","-sha256"],
    text=True
).strip().split("=",1)[1].upper()
pin_hex=fingerprint.replace(":","").lower()
p1=subprocess.Popen(["openssl","x509","-in",cert,"-pubkey","-noout"],stdout=subprocess.PIPE)
p2=subprocess.Popen(["openssl","pkey","-pubin","-outform","der"],stdin=p1.stdout,stdout=subprocess.PIPE)
p1.stdout.close()
p3=subprocess.Popen(["openssl","dgst","-sha256","-binary"],stdin=p2.stdout,stdout=subprocess.PIPE)
p2.stdout.close()
p4=subprocess.Popen(["openssl","enc","-base64","-A"],stdin=p3.stdout,stdout=subprocess.PIPE,text=True)
pin_b64=p4.communicate()[0].strip()
for p in (p1,p2,p3):
    p.wait()
print(json.dumps({"fingerprint":fingerprint,"pin_hex":pin_hex,"public_key_sha256":pin_b64}))
PY_CERT_META
}

check_port_available() {
  local protocol="$1" port="$2" allowed_process="$3" existing
  if [[ "$protocol" == "tcp" ]]; then
    existing="$(ss -H -lntp "sport = :${port}" 2>/dev/null || true)"
  else
    existing="$(ss -H -lnup "sport = :${port}" 2>/dev/null || true)"
  fi
  if [[ -n "$existing" ]] && ! grep -qi "$allowed_process" <<< "$existing"; then
    echo "$existing"
    fail "${protocol^^} ${port} 已被其他程序占用。"
  fi
}

initialize_state() {
  mkdir -p "$STATE_DIR" "$PACKAGE_ROOT" "$TLS_DIR"
  chmod 700 "$STATE_DIR" "$PACKAGE_ROOT"
  if [[ -f "$STATE_FILE" ]]; then
    jq -e '.schema==3 and .role=="japan-hub" and (.relays|type=="array") and ((.upstream_relays // [])|type=="array")' "$STATE_FILE" >/dev/null || fail "状态文件不是本脚本的 JPR3 格式。"
    local migrated
    migrated="$(mktemp --suffix=.json /tmp/vvv-state-migrate.XXXXXX)"; TMP_FILES+=("$migrated")
    jq --argjson limit "${VVV_HY2_LIMIT_MBPS:-50}" '.hy2_limit_mbps=(.hy2_limit_mbps // $limit) | .temporary_nodes=(.temporary_nodes // [])' "$STATE_FILE" > "$migrated"
    install -m600 "$migrated" "$STATE_FILE"
    HY2_LIMIT_MBPS="$(jq -r '.hy2_limit_mbps // 50' "$STATE_FILE")"
    echo "检测到本脚本状态，复用已保存的协议、端口、限速和全部密钥。"
    return
  fi

  local public_ip country direct_base now mode port
  mode="$INSTALL_MODE"
  port="$INSTALL_PORT"
  public_ip="$(detect_public_ipv4)" || fail "无法自动识别日本 VPS 公网 IPv4。"
  if country="$(detect_country_code "$public_ip")"; then
    direct_base="${country}-${public_ip}:${port}"
  else
    direct_base="${public_ip}:${port}"
  fi
  now="$(date --iso-8601=seconds)"

  local vless_json='null' hy2_json='null'
  if mode_has_vless "$mode"; then
    local key_output v_private v_public short_id uuid reserve_json i slot_uuid slot_email
    uuid="$(new_uuid)"
    reserve_json='[]'
    for i in $(seq 1 256); do
      slot_uuid="$(new_uuid)"; slot_email="reserve-$(printf '%02d' "$i")@relay.local"
      reserve_json="$(jq --arg slot "v$(printf '%02d' "$i")" --arg uuid "$slot_uuid" --arg email "$slot_email" --argjson local_port "$((22000+i))" '. + [{slot:$slot,uuid:$uuid,email:$email,local_port:$local_port,assigned_id:null}]' <<<"$reserve_json")"
    done
    key_output="$("$XRAY" x25519)"
    parse_x25519_keys "$key_output"
    v_private="$GENERATED_PRIVATE_KEY"
    v_public="$GENERATED_PUBLIC_KEY"
    short_id="$(openssl rand -hex 8)"
    vless_json="$(jq -n \
      --arg private "$v_private" --arg public "$v_public" --arg sid "$short_id" \
      --arg uuid "$uuid" --argjson reserve "$reserve_json" \
      '{reality:{private_key:$private,public_key:$public,short_id:$sid},direct_user:{uuid:$uuid,email:"jp-direct@relay.local"},reserve_users:$reserve}')"
  fi

  if mode_has_hy2 "$mode"; then
    local server_name cert key meta password obfs reserve_json i slot_name slot_password
    server_name="jp-hy2.jp-relay.local"
    cert="${TLS_DIR}/japan-hy2.crt"
    key="${TLS_DIR}/japan-hy2.key"
    generate_certificate "$server_name" "$cert" "$key"
    chown root:sing-box "$cert" "$key"
    meta="$(certificate_metadata_json "$cert")"
    password="$(random_secret)"
    obfs="$(random_secret)"
    reserve_json='[]'
    for i in $(seq 1 256); do
      slot_name="reserve-h$(printf '%02d' "$i")"
      slot_password="$(random_secret)"
      reserve_json="$(jq --arg slot "h$(printf '%02d' "$i")" --arg name "$slot_name" --arg password "$slot_password" --argjson local_port "$((21000+i))" '. + [{slot:$slot,name:$name,password:$password,local_port:$local_port,assigned_id:null}]' <<<"$reserve_json")"
    done
    hy2_json="$(jq -n \
      --arg server_name "$server_name" --arg cert "$cert" --arg key "$key" \
      --arg password "$password" --arg obfs "$obfs" --argjson reserve "$reserve_json" \
      --arg fp "$(jq -r '.fingerprint' <<< "$meta")" \
      --arg pinhex "$(jq -r '.pin_hex' <<< "$meta")" \
      --arg pinb64 "$(jq -r '.public_key_sha256' <<< "$meta")" \
      '{server_name:$server_name,certificate_path:$cert,key_path:$key,certificate_fingerprint:$fp,certificate_pin_hex:$pinhex,certificate_public_key_sha256:$pinb64,obfs_password:$obfs,direct_user:{name:"jp-direct-hy2",password:$password},reserve_users:$reserve}')"
  fi

  jq -n \
    --arg mode "$mode" \
    --arg ip "$public_ip" \
    --argjson port "$port" \
    --arg sni "$DEFAULT_SNI" \
    --arg direct_base "$direct_base" \
    --arg xray_version "$XRAY_VERSION" \
    --arg sing_version "$SING_BOX_VERSION" \
    --argjson vless "$vless_json" \
    --argjson hy2 "$hy2_json" \
    --arg now "$now" \
    --argjson limit "$HY2_LIMIT_MBPS" \
    '{
      schema:3,role:"japan-hub",protocol_mode:$mode,public_ip:$ip,listen_port:$port,
      sni:$sni,direct_base_name:$direct_base,xray_version:$xray_version,
      sing_box_version:$sing_version,hy2_limit_mbps:$limit,vless:$vless,hy2:$hy2,relays:[],upstream_relays:[],temporary_nodes:[],
      relay_manager_enabled:false,created_at:$now,updated_at:$now
    }' > "$STATE_FILE"
  chmod 600 "$STATE_FILE"
  echo "日本端 JPR3 永久状态已保存：$STATE_FILE"

}

ensure_hy2_certificate_permissions() {
  mode_has_hy2 "$INSTALL_MODE" || return 0
  install -d -o root -g sing-box -m 750 /etc/sing-box
  install -d -o root -g sing-box -m 750 "$TLS_DIR"
  local cert key
  cert="$(jq -r '.hy2.certificate_path' "$STATE_FILE")"
  key="$(jq -r '.hy2.key_path' "$STATE_FILE")"
  [[ -f "$cert" && -f "$key" ]] || fail "Hysteria 2 证书或私钥不存在。"
  chown root:sing-box "$cert" "$key"
  chmod 640 "$cert" "$key"
  runuser -u sing-box -- test -r "$cert" || fail "sing-box 用户无法读取 Hysteria 2 证书。"
  runuser -u sing-box -- test -r "$key" || fail "sing-box 用户无法读取 Hysteria 2 私钥。"
}

build_xray_config() {
  local state_path="$1" output="$2"
  python3 - "$state_path" "$output" <<'PY_BUILD_XRAY'
import json, sys
from pathlib import Path
state=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if state["protocol_mode"] not in ("dual","vless"):
    Path(sys.argv[2]).write_text("{}\n",encoding="utf-8")
    raise SystemExit
v=state["vless"]; sni=state["sni"]; port=int(state["listen_port"])
relays=state.get("relays",[])
upstreams=state.get("upstream_relays",[])
clients=[{"id":v["direct_user"]["uuid"],"level":0,"email":v["direct_user"]["email"],"flow":"xtls-rprx-vision"}]
for user in v.get("reserve_users",[]):
    clients.append({"id":user["uuid"],"level":0,"email":user["email"],"flow":"xtls-rprx-vision"})
inbounds=[{
 "tag":"in-vless-reality","listen":"0.0.0.0","port":port,"protocol":"vless",
 "settings":{"clients":clients,"decryption":"none"},
 "streamSettings":{"method":"raw","security":"reality","realitySettings":{
   "show":False,"target":f"{sni}:443","xver":0,"serverNames":[sni],
   "privateKey":v["reality"]["private_key"],"shortIds":[v["reality"]["short_id"]]}},
 "sniffing":{"enabled":True,"destOverride":["http","tls","quic"],"routeOnly":True}
}]
outbounds=[{"tag":"direct","protocol":"freedom","settings":{"domainStrategy":"UseIPv4"}}]
for user in v.get("reserve_users",[]):
    outbounds.append({"tag":f"vless-slot-{user['slot']}","protocol":"socks","settings":{"address":"127.0.0.1","port":int(user["local_port"])}})
test_rules=[]
route_rules=[{"type":"field","user":[user["email"]],"outboundTag":f"vless-slot-{user['slot']}","ruleTag":f"vless-slot-route-{user['slot']}"} for user in v.get("reserve_users",[])]
udp_block_rules=[]
private_ips=[
 "0.0.0.0/8","10.0.0.0/8","100.64.0.0/10","127.0.0.0/8",
 "169.254.0.0/16","172.16.0.0/12","192.0.0.0/24","192.0.2.0/24",
 "192.168.0.0/16","198.18.0.0/15","198.51.100.0/24","203.0.113.0/24",
 "224.0.0.0/4","240.0.0.0/4","::1/128","fc00::/7","fe80::/10"
]
rules=test_rules+udp_block_rules+[
 {"type":"field","ip":private_ips,"outboundTag":"blocked","ruleTag":"block-private"},
 {"type":"field","protocol":["bittorrent"],"outboundTag":"blocked","ruleTag":"block-bittorrent"},
 {"type":"field","user":[v["direct_user"]["email"]],"outboundTag":"direct","ruleTag":"direct-japan"}
]+route_rules
outbounds.append({"tag":"blocked","protocol":"blackhole","settings":{}})
cfg={"log":{"loglevel":"warning"},"inbounds":inbounds,"outbounds":outbounds,"routing":{"domainStrategy":"AsIs","rules":rules}}
Path(sys.argv[2]).write_text(json.dumps(cfg,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
PY_BUILD_XRAY
}

build_sing_config() {
  local state_path="$1" output="$2"
  python3 - "$state_path" "$output" "$HY2_LIMIT_MBPS" <<'PY_BUILD_SING'
import json, sys
from pathlib import Path
state=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
limit_mbps=int(state.get("hy2_limit_mbps") or sys.argv[3])
if state["protocol_mode"] not in ("dual","hy2"):
    Path(sys.argv[2]).write_text("{}\n",encoding="utf-8")
    raise SystemExit
h=state["hy2"]; port=int(state["listen_port"])
reserve=h.get("reserve_users",[])
users=[{"name":h["direct_user"]["name"],"password":h["direct_user"]["password"]}]
users.extend({"name":slot["name"],"password":slot["password"]} for slot in reserve)
inbounds=[{
 "type":"hysteria2","tag":"hy2-in","listen":"0.0.0.0","listen_port":port,
 "up_mbps":limit_mbps,"down_mbps":limit_mbps,"ignore_client_bandwidth":False,"users":users,
 "obfs":{"type":"salamander","password":h["obfs_password"]},
 "tls":{"enabled":True,"server_name":h["server_name"],"alpn":["h3"],"min_version":"1.3",
        "certificate_path":h["certificate_path"],"key_path":h["key_path"]}
}]
outbounds=[{"type":"direct","tag":"direct"}]
rules=[{"ip_is_private":True,"action":"reject","method":"drop"}]
for slot in reserve:
    tag=f"hy2-slot-{slot['slot']}"
    outbounds.append({"type":"socks","tag":tag,"server":"127.0.0.1","server_port":int(slot["local_port"])})
    rules.append({"auth_user":[slot["name"]],"action":"route","outbound":tag})
rules.append({"auth_user":[h["direct_user"]["name"]],"action":"route","outbound":"direct"})
cfg={
 "log":{"level":"warn","timestamp":True},
 "inbounds":inbounds,
 "outbounds":outbounds,
 "route":{"rules":rules,"final":"direct","auto_detect_interface":True}
}
Path(sys.argv[2]).write_text(json.dumps(cfg,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
PY_BUILD_SING
}

verify_xray_runtime() {
  mode_has_vless || return 0
  local port tmp_dir file slot
  port="$(jq -r '.listen_port' "$STATE_FILE")"
  systemctl is-active --quiet xray || { echo "错误：主 Xray 服务未运行。" >&2; return 1; }
  ss -H -lntp "sport = :${port}" 2>/dev/null | grep -qi xray || {
    echo "错误：主 Xray 端口 ${port} 未监听。" >&2
    return 1
  }

  # 验证对象必须与槽位生成器完全一致；历史 assigned_id 只是不可复用墓碑，不代表服务应在线。
  tmp_dir="$(mktemp -d /tmp/vvv-verify-vless.XXXXXX)"
  TMP_FILES+=("$tmp_dir")
  build_vless_slot_configs "$STATE_FILE" "$tmp_dir"
  for file in "$tmp_dir"/*.json; do
    [[ -e "$file" ]] || break
    slot="$(basename "$file" .json)"
    systemctl is-active --quiet "vvv-vless-slot@${slot}.service" || {
      echo "错误：VLESS 活跃槽位 ${slot} 服务未运行。" >&2
      return 1
    }
    port="$(jq -r '.inbounds[0].port' "$file")"
    ss -H -lntp "sport = :${port}" 2>/dev/null | grep -qi xray || {
      echo "错误：VLESS 活跃槽位 ${slot} 的本地端口 ${port} 未监听。" >&2
      return 1
    }
  done
  return 0
}

verify_sing_runtime() {
  mode_has_hy2 || return 0
  local port tmp_dir file slot
  port="$(jq -r '.listen_port' "$STATE_FILE")"
  systemctl is-active --quiet sing-box || { echo "错误：主 sing-box 服务未运行。" >&2; return 1; }
  ss -H -lnup "sport = :${port}" 2>/dev/null | grep -qi sing-box || {
    echo "错误：主 Hysteria 2 端口 ${port} 未监听。" >&2
    return 1
  }

  # 只验证能从现存正式线路或临时节点生成配置的活跃槽位。
  tmp_dir="$(mktemp -d /tmp/vvv-verify-hy2.XXXXXX)"
  TMP_FILES+=("$tmp_dir")
  build_hy2_slot_configs "$STATE_FILE" "$tmp_dir"
  for file in "$tmp_dir"/*.json; do
    [[ -e "$file" ]] || break
    slot="$(basename "$file" .json)"
    systemctl is-active --quiet "vvv-hy2-slot@${slot}.service" || {
      echo "错误：HY2 活跃槽位 ${slot} 服务未运行。" >&2
      return 1
    }
    port="$(jq -r '.inbounds[0].listen_port' "$file")"
    ss -H -lntp "sport = :${port}" 2>/dev/null | grep -qi sing-box || {
      echo "错误：HY2 活跃槽位 ${slot} 的本地端口 ${port} 未监听。" >&2
      return 1
    }
  done
  return 0
}

activate_initial_state() {
  local tmp cert key
  if mode_has_vless; then
    tmp="$(mktemp --suffix=.json /tmp/japan-xray.XXXXXX)"
    TMP_FILES+=("$tmp")
    build_xray_config "$STATE_FILE" "$tmp" || return 1
    "$XRAY" run -test -format=json -config "$tmp" || return 1
    install -o root -g xray -m 640 "$tmp" "$XRAY_CFG" || return 1
    systemctl daemon-reload
    systemctl restart xray || return 1
    sleep 2
    verify_xray_runtime || { journalctl -u xray --no-pager -n 80 || true; return 1; }
  fi
  if mode_has_hy2; then
    tmp="$(mktemp --suffix=.json /tmp/japan-sing.XXXXXX)"
    TMP_FILES+=("$tmp")
    build_sing_config "$STATE_FILE" "$tmp" || return 1
    "$SING_BOX" check -c "$tmp" || return 1
    install -o root -g sing-box -m 640 "$tmp" "$SING_CFG" || return 1
    cert="$(jq -r '.hy2.certificate_path' "$STATE_FILE")"
    key="$(jq -r '.hy2.key_path' "$STATE_FILE")"
    chown root:sing-box "$cert" "$key"
    chmod 640 "$cert" "$key"
    runuser -u sing-box -- "$SING_BOX" check -c "$SING_CFG" || return 1
    systemctl daemon-reload
    systemctl restart sing-box || return 1
    sync_hy2_slot_services "$STATE_FILE" "$STATE_FILE" || return 1
    sleep 2
    verify_sing_runtime || { journalctl -u sing-box --no-pager -n 80 || true; return 1; }
  fi
}

update_state_core_versions() {
  local tmp
  tmp="$(mktemp --suffix=.json /tmp/jp-core-versions.XXXXXX)"
  TMP_FILES+=("$tmp")
  jq --arg xv "$XRAY_VERSION" --arg sv "$SING_BOX_VERSION" --arg now "$(date --iso-8601=seconds)" \
    '.xray_version=$xv | .sing_box_version=$sv | .updated_at=$now' "$STATE_FILE" > "$tmp"
  install -o root -g root -m 600 "$tmp" "$STATE_FILE"
}

activate_initial_state_with_fallback() {
  if activate_initial_state; then
    return 0
  fi
  local changed=0
  echo "最新版核心的配置或启动测试失败，尝试使用实测备用版本。" >&2
  if mode_has_vless && [[ "$XRAY_VERSION" != "$XRAY_FALLBACK_VERSION" ]]; then
    XRAY_VERSION="$XRAY_FALLBACK_VERSION"
    XRAY_VERSION_SOURCE="备用稳定版（最新版配置测试失败）"
    install_xray_version "$XRAY_VERSION" || fail "Xray 备用版安装失败。"
    changed=1
  fi
  if mode_has_hy2 && [[ "$SING_BOX_VERSION" != "$SING_BOX_FALLBACK_VERSION" ]]; then
    SING_BOX_VERSION="$SING_BOX_FALLBACK_VERSION"
    SING_BOX_VERSION_SOURCE="备用稳定版（最新版配置测试失败）"
    install_sing_box_version "$SING_BOX_VERSION" || fail "sing-box 备用版安装失败。"
    changed=1
  fi
  (( changed == 1 )) || fail "代理服务配置或启动测试失败。"
  update_state_core_versions
  activate_initial_state || fail "使用备用版本后，代理服务仍无法启动。"
}

build_vless_slot_configs() {
  local state_path="$1" out_dir="$2"
  mkdir -p "$out_dir"
  python3 - "$state_path" "$out_dir" <<'PY_VLESS_SLOTS'
import json,sys
from pathlib import Path
state=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')); out=Path(sys.argv[2])
v=state.get('vless') or {}; slots={x['slot']:x for x in v.get('reserve_users',[])}
relays={x.get('id'):x for x in state.get('relays',[])}; upstreams={x.get('id'):x for x in state.get('upstream_relays',[])}
temps={x.get('id'):x for x in state.get('temporary_nodes',[])}
for slot_id,slot in slots.items():
    assigned=slot.get('assigned_id')
    if not assigned: continue
    source_id=assigned
    if assigned in temps:
        source_id=temps[assigned].get('source_id')
    inbound={"tag":"slot-in","listen":"127.0.0.1","port":int(slot['local_port']),"protocol":"socks","settings":{"udp":False},"sniffing":{"enabled":True,"destOverride":["http","tls"],"routeOnly":True}}
    if source_id in relays:
        relay=relays[source_id]; rv=relay.get('vless')
        if not rv: continue
        outbound={"tag":"slot-out","protocol":"vless","settings":{"address":relay['remote_ip'],"port":int(relay['remote_port']),"id":rv['outbound_uuid'],"encryption":"none","flow":"xtls-rprx-vision"},"streamSettings":{"method":"raw","security":"reality","realitySettings":{"serverName":state['sni'],"fingerprint":"chrome","password":rv['remote_reality']['public_key'],"shortId":rv['remote_reality']['short_id'],"spiderX":""}}}
    elif source_id in upstreams:
        relay=upstreams[source_id]
        outbound={"tag":"slot-out","protocol":relay['proxy_protocol'],"settings":{"address":relay['host'],"port":int(relay['port']),"user":relay['username'],"pass":relay['password']}}
    else:
        continue
    cfg={"log":{"loglevel":"warning"},"inbounds":[inbound],"outbounds":[outbound,{"tag":"blocked","protocol":"blackhole","settings":{}}],"routing":{"domainStrategy":"AsIs","rules":[{"type":"field","ip":["0.0.0.0/8","10.0.0.0/8","100.64.0.0/10","127.0.0.0/8","169.254.0.0/16","172.16.0.0/12","192.168.0.0/16","224.0.0.0/4","240.0.0.0/4","::1/128","fc00::/7","fe80::/10"],"outboundTag":"blocked","ruleTag":"block-private"},{"type":"field","protocol":["bittorrent"],"outboundTag":"blocked","ruleTag":"block-bittorrent"},{"type":"field","inboundTag":["slot-in"],"outboundTag":"slot-out","ruleTag":"slot-route"}]}}
    (out/f'{slot_id}.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
PY_VLESS_SLOTS
}

install_vless_slot_service() {
  install -d -o root -g xray -m750 /etc/vvv-slots/vless
  cat > /etc/systemd/system/vvv-vless-slot@.service <<'EOF_VLESS_SLOT_SERVICE'
[Unit]
Description=VVV VLESS relay slot %i
After=network-online.target xray.service
Wants=network-online.target

[Service]
User=xray
Group=xray
NoNewPrivileges=true
Environment=GOMEMLIMIT=128MiB
Environment=GOGC=50
ExecStart=/usr/local/bin/xray run -format=json -config /etc/vvv-slots/vless/%i.json
Restart=on-failure
RestartSec=2s
LimitNOFILE=262144

[Install]
WantedBy=multi-user.target
EOF_VLESS_SLOT_SERVICE
  systemctl daemon-reload
}

sync_vless_slot_services() {
  local old_state="$1" new_state="$2" new_dir file link slot changed port unit
  : "$old_state"
  new_dir="$(mktemp -d /tmp/vvv-vless-new.XXXXXX)"
  TMP_FILES+=("$new_dir")
  build_vless_slot_configs "$new_state" "$new_dir"
  install_vless_slot_service

  for file in "$new_dir"/*.json; do
    [[ -e "$file" ]] || break
    "$XRAY" run -test -format=json -config "$file"
  done

  # 以候选状态生成的配置为唯一事实来源，清理真实文件和遗留的 systemd 启用链接。
  for file in /etc/vvv-slots/vless/*.json; do
    [[ -e "$file" ]] || break
    slot="$(basename "$file" .json)"
    if [[ ! -f "$new_dir/${slot}.json" ]]; then
      systemctl disable --now "vvv-vless-slot@${slot}.service" >/dev/null 2>&1 || true
      rm -f -- "$file"
    fi
  done
  for link in /etc/systemd/system/multi-user.target.wants/vvv-vless-slot@*.service; do
    [[ -e "$link" || -L "$link" ]] || break
    unit="${link##*/}"; slot="${unit#vvv-vless-slot@}"; slot="${slot%.service}"
    if [[ ! -f "$new_dir/${slot}.json" ]]; then
      systemctl disable --now "$unit" >/dev/null 2>&1 || true
      rm -f -- "$link" "/etc/vvv-slots/vless/${slot}.json"
    fi
  done
  systemctl daemon-reload

  for file in "$new_dir"/*.json; do
    [[ -e "$file" ]] || break
    slot="$(basename "$file" .json)"; changed=1
    [[ ! -f "/etc/vvv-slots/vless/${slot}.json" ]] || cmp -s "$file" "/etc/vvv-slots/vless/${slot}.json" && changed=0
    install -o root -g xray -m640 "$file" "/etc/vvv-slots/vless/${slot}.json"
    systemctl enable "vvv-vless-slot@${slot}.service" >/dev/null
    if (( changed==1 )); then
      systemctl restart "vvv-vless-slot@${slot}.service"
    else
      systemctl start "vvv-vless-slot@${slot}.service"
    fi
  done

  sleep 2
  for file in "$new_dir"/*.json; do
    [[ -e "$file" ]] || break
    slot="$(basename "$file" .json)"
    systemctl is-active --quiet "vvv-vless-slot@${slot}.service" || {
      echo "错误：VLESS 活跃槽位 ${slot} 服务未运行。" >&2
      return 1
    }
    port="$(jq -r '.inbounds[0].port' "$file")"
    ss -H -lntp "sport = :${port}" 2>/dev/null | grep -qi xray || {
      echo "错误：VLESS 活跃槽位 ${slot} 的本地端口 ${port} 未监听。" >&2
      return 1
    }
  done
}

build_hy2_slot_configs() {
  local state_path="$1" out_dir="$2"
  mkdir -p "$out_dir"
  python3 - "$state_path" "$out_dir" "$HY2_LIMIT_MBPS" <<'PY_HY2_SLOTS'
import json,sys
from pathlib import Path
state=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')); out=Path(sys.argv[2]); limit=int(state.get('hy2_limit_mbps') or sys.argv[3])
h=state.get('hy2') or {}; slots={x['slot']:x for x in h.get('reserve_users',[])}
relays={x.get('id'):x for x in state.get('relays',[])}
temps={x.get('id'):x for x in state.get('temporary_nodes',[])}
private_rule={"ip_is_private":True,"action":"reject","method":"drop"}
for slot_id,slot in slots.items():
    assigned=slot.get('assigned_id')
    relay=relays.get(assigned)
    if not relay and assigned in temps:
        temp=temps[assigned]
        relay=relays.get(temp.get('source_id')) if temp.get('source_type')=='vps' else None
    if not relay: continue
    rh=relay.get('hy2')
    if not rh: continue
    inbound={"type":"mixed","tag":"slot-in","listen":"127.0.0.1","listen_port":int(slot['local_port'])}
    outbound={
      "type":"hysteria2","tag":"slot-out","server":relay['remote_ip'],"server_port":int(relay['remote_port']),
      "up_mbps":limit,"down_mbps":limit,"password":rh['outbound_password'],
      "obfs":{"type":"salamander","password":rh['outbound_obfs_password']},
      "tls":{"enabled":True,"server_name":rh['outbound_server_name'],"insecure":True,"alpn":["h3"],
             "min_version":"1.3","certificate_public_key_sha256":[rh['remote_certificate_public_key_sha256']]}
    }
    cfg={"log":{"level":"warn","timestamp":True},"inbounds":[inbound],"outbounds":[outbound],
         "route":{"rules":[private_rule],"final":"slot-out","auto_detect_interface":True}}
    (out/f'{slot_id}.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
PY_HY2_SLOTS
}

sync_hy2_slot_services() {
  local old_state="$1" new_state="$2" new_dir file link slot changed port unit
  : "$old_state"
  new_dir="$(mktemp -d /tmp/vvv-hy2-new.XXXXXX)"
  TMP_FILES+=("$new_dir")
  build_hy2_slot_configs "$new_state" "$new_dir"
  install -d -o root -g sing-box -m750 /etc/vvv-slots/hy2
  [[ -f /etc/systemd/system/vvv-hy2-slot@.service ]] || { fail "HY2 槽位 systemd 模板不存在。"; return 1; }

  for file in "$new_dir"/*.json; do
    [[ -e "$file" ]] || break
    "$SING_BOX" check -c "$file"
  done

  # 以候选状态生成的配置为唯一事实来源，清理真实文件和遗留的 systemd 启用链接。
  for file in /etc/vvv-slots/hy2/*.json; do
    [[ -e "$file" ]] || break
    slot="$(basename "$file" .json)"
    if [[ ! -f "$new_dir/${slot}.json" ]]; then
      systemctl disable --now "vvv-hy2-slot@${slot}.service" >/dev/null 2>&1 || true
      rm -f -- "$file"
    fi
  done
  for link in /etc/systemd/system/multi-user.target.wants/vvv-hy2-slot@*.service; do
    [[ -e "$link" || -L "$link" ]] || break
    unit="${link##*/}"; slot="${unit#vvv-hy2-slot@}"; slot="${slot%.service}"
    if [[ ! -f "$new_dir/${slot}.json" ]]; then
      systemctl disable --now "$unit" >/dev/null 2>&1 || true
      rm -f -- "$link" "/etc/vvv-slots/hy2/${slot}.json"
    fi
  done
  systemctl daemon-reload

  for file in "$new_dir"/*.json; do
    [[ -e "$file" ]] || break
    slot="$(basename "$file" .json)"; changed=1
    [[ ! -f "/etc/vvv-slots/hy2/${slot}.json" ]] || cmp -s "$file" "/etc/vvv-slots/hy2/${slot}.json" && changed=0
    install -o root -g sing-box -m640 "$file" "/etc/vvv-slots/hy2/${slot}.json"
    systemctl enable "vvv-hy2-slot@${slot}.service" >/dev/null
    if (( changed==1 )); then
      systemctl restart "vvv-hy2-slot@${slot}.service"
    else
      systemctl start "vvv-hy2-slot@${slot}.service"
    fi
  done

  sleep 2
  for file in "$new_dir"/*.json; do
    [[ -e "$file" ]] || break
    slot="$(basename "$file" .json)"
    systemctl is-active --quiet "vvv-hy2-slot@${slot}.service" || {
      echo "错误：HY2 活跃槽位 ${slot} 服务未运行。" >&2
      return 1
    }
    port="$(jq -r '.inbounds[0].listen_port' "$file")"
    ss -H -lntp "sport = :${port}" 2>/dev/null | grep -qi sing-box || {
      echo "错误：HY2 活跃槽位 ${slot} 的本地端口 ${port} 未监听。" >&2
      return 1
    }
  done
}

apply_candidate_with_rollback() {
  local candidate_state="$1" delete_dir="${2:-}" old_state old_xray old_sing candidate_xray candidate_sing xray_pid="" sing_pid="" ok=1
  old_state="$(mktemp --suffix=.json /tmp/vvv-old-state.XXXXXX)"; old_xray="$(mktemp --suffix=.json /tmp/vvv-old-xray.XXXXXX)"; old_sing="$(mktemp --suffix=.json /tmp/vvv-old-sing.XXXXXX)"
  candidate_xray="$(mktemp --suffix=.json /tmp/vvv-new-xray.XXXXXX)"; candidate_sing="$(mktemp --suffix=.json /tmp/vvv-new-sing.XXXXXX)"
  TMP_FILES+=("$old_state" "$old_xray" "$old_sing" "$candidate_xray" "$candidate_sing")
  cp -a "$STATE_FILE" "$old_state"; [[ ! -f "$XRAY_CFG" ]] || cp -a "$XRAY_CFG" "$old_xray"; [[ ! -f "$SING_CFG" ]] || cp -a "$SING_CFG" "$old_sing"
  if ! validate_slot_references "$candidate_state"; then
    fail "候选线路状态引用校验失败，未修改任何运行配置。"
    return 1
  fi
  release_orphaned_vless_slots "$candidate_state"; release_orphaned_hy2_slots "$candidate_state"
  vvv_event_backup before-line-change
  if mode_has_vless "$(jq -r '.protocol_mode' "$candidate_state")"; then
    build_xray_config "$candidate_state" "$candidate_xray"; "$XRAY" run -test -format=json -config "$candidate_xray"
    cmp -s "$candidate_xray" "$XRAY_CFG" || { fail "线路变更意外修改了主 Xray 固定配置。"; return 1; }
    xray_pid="$(systemctl show -p MainPID --value xray)"
    if ! sync_vless_slot_services "$old_state" "$candidate_state"; then sync_vless_slot_services "$candidate_state" "$old_state" || true; fail "VLESS 槽位更新失败，已恢复旧槽位。"; return 1; fi
  fi
  if mode_has_hy2 "$(jq -r '.protocol_mode' "$candidate_state")"; then
    build_sing_config "$candidate_state" "$candidate_sing"; "$SING_BOX" check -c "$candidate_sing"
    cmp -s "$candidate_sing" "$SING_CFG" || { mode_has_vless && sync_vless_slot_services "$candidate_state" "$old_state" || true; fail "线路变更意外修改了主 sing-box 固定配置。"; return 1; }
    sing_pid="$(systemctl show -p MainPID --value sing-box)"
    if ! sync_hy2_slot_services "$old_state" "$candidate_state"; then
      sync_hy2_slot_services "$candidate_state" "$old_state" || true; mode_has_vless && sync_vless_slot_services "$candidate_state" "$old_state" || true
      fail "HY2 槽位更新失败，已恢复旧槽位。"; return 1
    fi
  fi
  install -m600 "$candidate_state" "$STATE_FILE"; sleep 2
  if [[ -n "$xray_pid" ]]; then
    if [[ "$(systemctl show -p MainPID --value xray)" == "$xray_pid" ]]; then
      echo "主 Xray PID 已保持不变：${xray_pid}"
    else
      echo "错误：主 Xray PID 发生变化。" >&2
      ok=0
    fi
  fi
  if [[ -n "$sing_pid" ]]; then
    if [[ "$(systemctl show -p MainPID --value sing-box)" == "$sing_pid" ]]; then
      echo "主 sing-box PID 已保持不变：${sing_pid}"
    else
      echo "错误：主 sing-box PID 发生变化。" >&2
      ok=0
    fi
  fi
  verify_xray_runtime || ok=0; verify_sing_runtime || ok=0
  if (( ok==1 )); then
    [[ -z "$delete_dir" ]] || rm -rf -- "$delete_dir"
    vvv_event_backup after-line-change
    systemctl start vvv-sync.service >/dev/null 2>&1 || true
    return 0
  fi
  install -m600 "$old_state" "$STATE_FILE"
  mode_has_vless && sync_vless_slot_services "$candidate_state" "$old_state" || true
  mode_has_hy2 && sync_hy2_slot_services "$candidate_state" "$old_state" || true
  fail "新槽位配置验证失败，已恢复旧配置。"
}

generate_client_files() {
  local state_path="$1" relay_id="$2" out_dir="$3" kind="${4:-relay}"
  mkdir -p "$out_dir"
  python3 - "$state_path" "$relay_id" "$out_dir" "$kind" "$HY2_LIMIT_MBPS" <<'PY_CLIENTS'
import json, re, sys
from pathlib import Path
from urllib.parse import quote, urlencode

def protocol_name(base, proto):
    m=re.match(r"^([A-Z]{2})-(.+)$", base)
    if m:
        return f"{m.group(1)}-{proto}-{m.group(2)}"
    if re.match(r"^\d+\.\d+\.\d+\.\d+:\d+$", base):
        return f"{proto}-{base}"
    return f"{base}-{proto}"

def loon_q(value):
    return '"'+str(value).replace('\\','\\\\').replace('"','\\"')+'"'

def loon_name(value):
    return str(value).replace('=','-').replace('\n',' ').replace('\r',' ')

state=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
rid=sys.argv[2]; out=Path(sys.argv[3]); kind=sys.argv[4]; limit_mbps=int(sys.argv[5])
out.mkdir(parents=True,exist_ok=True)
mode=state["protocol_mode"]; ip=state["public_ip"]; port=int(state["listen_port"]); sni=state["sni"]
relay=None; upstream=None
if kind=="direct":
    base=state["direct_base_name"]
    enabled_vless=mode in ("dual","vless")
    enabled_hy2=mode in ("dual","hy2")
elif kind=="relay":
    relay=next(x for x in state.get("relays",[]) if x["id"]==rid)
    raw_name=str(relay.get("name") or "")
    country=raw_name[:2].upper() if len(raw_name)>=3 and raw_name[:2].isalpha() and raw_name[2]=="-" else ""
    base=(country+"-" if country else "")+f"中转-{ip}:{port}"
    enabled_vless=relay.get("vless") is not None
    enabled_hy2=relay.get("hy2") is not None
elif kind=="upstream":
    upstream=next(x for x in state.get("upstream_relays",[]) if x["id"]==rid)
    base=upstream["name"]
    enabled_vless=True
    enabled_hy2=False
else:
    raise SystemExit(f"unknown client kind: {kind}")

qx_lines=[]; share_links=[]; loon_lines=[]; clash_entries=[]

if enabled_vless:
    v=state["vless"]
    if kind=="direct": uuid=v["direct_user"]["uuid"]
    elif kind=="relay": uuid=relay["vless"]["client_uuid"]
    else: uuid=upstream["client_uuid"]
    name=protocol_name(base,"VLESS")
    udp_enabled=(kind != "upstream")
    qx=f"vless={ip}:{port}, method=none, password={uuid}, obfs=over-tls, obfs-host={sni}, reality-base64-pubkey={v['reality']['public_key']}, reality-hex-shortid={v['reality']['short_id']}, vless-flow=xtls-rprx-vision, fast-open=false, udp-relay={'true' if udp_enabled else 'false'}, tag={name}"
    params=[("encryption","none"),("flow","xtls-rprx-vision"),("security","reality"),("sni",sni),("fp","chrome"),("pbk",v["reality"]["public_key"]),("sid",v["reality"]["short_id"]),("type","tcp"),("headerType","none")]
    uri=f"vless://{uuid}@{ip}:{port}?{urlencode(params)}#{quote(name,safe='')}"
    loon=f"{loon_name(name)} = VLESS,{ip},{port},{loon_q(uuid)},transport=tcp,flow=xtls-rprx-vision,public-key={loon_q(v['reality']['public_key'])},short-id={v['reality']['short_id']},udp={'true' if udp_enabled else 'false'},over-tls=true,sni={sni},skip-cert-verify=true"
    clash=f'''  - name: "{name}"
    type: vless
    server: {ip}
    port: {port}
    uuid: {uuid}
    network: tcp
    udp: {'true' if udp_enabled else 'false'}
    tls: true
    flow: xtls-rprx-vision
    encryption: ""
    servername: {sni}
    client-fingerprint: chrome
    skip-cert-verify: true
    reality-opts:
      public-key: {v["reality"]["public_key"]}
      short-id: "{v["reality"]["short_id"]}"
'''
    qx_lines.append(qx); share_links.append((name,uri)); loon_lines.append(loon); clash_entries.append(clash)

if enabled_hy2:
    h=state["hy2"]
    password=h["direct_user"]["password"] if kind=="direct" else relay["hy2"]["client_password"]
    name=protocol_name(base,"HY2")
    share_params=[("obfs","salamander"),("obfs-password",h["obfs_password"]),("sni",h["server_name"]),("insecure","1"),("pinSHA256",h["certificate_pin_hex"])]
    uri=f"hysteria2://{quote(password,safe='')}@{ip}:{port}/?{urlencode(share_params)}#{quote(name,safe='')}"
    loon=f"{loon_name(name)} = Hysteria2,{ip},{port},{loon_q(password)},skip-cert-verify=true,sni={h['server_name']},udp=true,fast-open=true,salamander-password={h['obfs_password']}"
    clash=f'''  - name: "{name}"
    type: hysteria2
    server: {ip}
    port: {port}
    password: "{password}"
    up: "{limit_mbps} Mbps"
    down: "{limit_mbps} Mbps"
    obfs: salamander
    obfs-password: "{h["obfs_password"]}"
    sni: {h["server_name"]}
    skip-cert-verify: true
    fingerprint: "{h["certificate_fingerprint"]}"
    alpn:
      - h3
    udp: true
'''
    share_links.append((name,uri)); loon_lines.append(loon); clash_entries.append(clash)

qx_text="\n".join(qx_lines)
share_text="\n".join(uri for _,uri in share_links)
loon_text="\n".join(loon_lines)
clash_text="proxies:\n"+"".join(clash_entries)

title="日本 VPS 直连节点" if kind=="direct" else (f"中转节点：{base}" if kind=="relay" else f"动态代理中转节点：{base}")
lines=[title,"="*36,f"日本入口：{ip}:{port}",f"客户端加密协议：VLESS + XTLS Vision + REALITY" if kind=="upstream" else f"安装模式：{mode}"]
if enabled_hy2:
    lines += [f"Hysteria 2 服务端硬上限：上行 {limit_mbps} Mbps / 下行 {limit_mbps} Mbps"]
if kind=="relay": lines += [f"最终落地：{relay['remote_ip']}:{relay['remote_port']}"]
if kind=="upstream":
    lines += [f"上游代理：{upstream['protocol_label']} {upstream['host']}:{upstream['port']}","UDP：服务器端拒绝，防止绕过上游出口"]
if qx_lines: lines += ["","【Quantumult X】",qx_text]
if share_links:
    lines += ["","【Loon / Shadowrocket】","Loon 原生配置：",loon_text,"","分享链接："]
    for name,uri in share_links: lines += [f"[{name}]",uri]
if clash_entries: lines += ["","【Clash Verge Rev / Mihomo】",clash_text]
if clash_entries: lines += ["","【NekoBoxForAndroid（Clash Meta）】",clash_text]
summary="\n".join(lines).rstrip()+"\n"

(out/"客户端节点.txt").write_text(summary,encoding="utf-8")
(out/"Quantumult-X.conf").write_text((qx_text+"\n") if qx_text else "",encoding="utf-8")
(out/"Loon.conf").write_text((loon_text+"\n") if loon_text else "",encoding="utf-8")
(out/"Loon-Shadowrocket.txt").write_text((share_text+"\n") if share_text else "",encoding="utf-8")
# 同时保留旧文件名，便于已有运维习惯和第三方工具读取。
(out/"Shadowrocket.txt").write_text((share_text+"\n") if share_text else "",encoding="utf-8")
(out/"Clash-Verge-Rev.yaml").write_text(clash_text,encoding="utf-8")
(out/"NekoBoxForAndroid.yaml").write_text(clash_text,encoding="utf-8")
print(summary,end="")
PY_CLIENTS
  chmod 700 "$out_dir"
  chmod 600 "$out_dir"/*
}

generate_direct_client_files() {
  local dir="/root/日本VPS-直连客户端配置"
  generate_client_files "$STATE_FILE" "" "$dir" direct
  cp -f "$dir/客户端节点.txt" /root/日本VPS-客户端节点.txt
  chmod 600 /root/日本VPS-客户端节点.txt
}

allocate_test_port() {
  local protocol="$1" start end port used
  case "$protocol" in
    vless) start=18080; end=18999 ;;
    hy2) start=19080; end=19999 ;;
    upstream) start=20080; end=20999 ;;
    *) fail "未知测试端口类型：$protocol"; return 1 ;;
  esac
  for ((port=start; port<=end; port++)); do
    if [[ "$protocol" == "upstream" ]]; then
      used="$(jq --argjson p "$port" '[.upstream_relays[]?.test_socks_port | select(.==$p)] | length' "$STATE_FILE" 2>/dev/null || echo 0)"
    else
      used="$(jq --argjson p "$port" --arg proto "$protocol" '[.relays[]? | (if $proto=="vless" then .vless.test_socks_port? else .hy2.test_socks_port? end) | select(.==$p)] | length' "$STATE_FILE" 2>/dev/null || echo 0)"
    fi
    (( used == 0 )) || continue
    if [[ -z "$(ss -H -lnt "sport = :${port}" 2>/dev/null || true)" ]]; then
      echo "$port"
      return 0
    fi
  done
  fail "无法为 ${protocol} 分配本地测试端口。"
}

create_remote_hy2_material() {
  local relay_id="$1" out_json="$2" tmp server_name cert key meta
  tmp="$(mktemp -d /tmp/remote-hy2-cert.XXXXXX)"
  TMP_FILES+=("$tmp")
  server_name="landing-${relay_id}.jp-relay.local"
  cert="${tmp}/server.crt"
  key="${tmp}/server.key"
  generate_certificate "$server_name" "$cert" "$key"
  meta="$(certificate_metadata_json "$cert")"
  jq -n \
    --arg server_name "$server_name" \
    --arg cert_pem "$(cat "$cert")" \
    --arg key_pem "$(cat "$key")" \
    --arg fp "$(jq -r '.fingerprint' <<< "$meta")" \
    --arg pinhex "$(jq -r '.pin_hex' <<< "$meta")" \
    --arg pinb64 "$(jq -r '.public_key_sha256' <<< "$meta")" \
    '{server_name:$server_name,certificate_pem:$cert_pem,key_pem:$key_pem,fingerprint:$fp,pin_hex:$pinhex,public_key_sha256:$pinb64}' > "$out_json"
}

vvv_event_backup() {
  local reason="$1"
  [[ -x /usr/local/lib/vvv/backup_manager.py && -f /etc/vvv-sub/config.json ]] || return 0
  python3 /usr/local/lib/vvv/backup_manager.py create "$reason" --force >/dev/null || echo "警告：自动备份失败。" >&2
}

allocate_vless_slot() {
  local target_id="${1:-}" slot_json
  if [[ -n "$target_id" ]]; then
    slot_json="$(jq -c --arg id "$target_id" '[.vless.reserve_users[] | select(.assigned_id==$id)][0] // empty' "$STATE_FILE")"
  fi
  if [[ -z "${slot_json:-}" ]]; then
    slot_json="$(jq -c '[.vless.reserve_users[] | select(.assigned_id==null and (.retired // false)==false)][0] // empty' "$STATE_FILE")"
  fi
  [[ -n "$slot_json" ]] || fail "VLESS 可用固定凭证槽位已用尽（已分配或退役共 256 条）。"
  ALLOC_VLESS_SLOT="$(jq -r '.slot' <<<"$slot_json")"
  ALLOC_VLESS_UUID="$(jq -r '.uuid' <<<"$slot_json")"
  ALLOC_VLESS_EMAIL="$(jq -r '.email' <<<"$slot_json")"
  ALLOC_VLESS_PORT="$(jq -r '.local_port' <<<"$slot_json")"
  [[ -n "$ALLOC_VLESS_SLOT" && -n "$ALLOC_VLESS_UUID" && -n "$ALLOC_VLESS_EMAIL" && "$ALLOC_VLESS_PORT" =~ ^[0-9]+$ ]] || fail "VLESS 预分配用户池损坏。"
}

release_orphaned_vless_slots() {
  local path="$1"
  [[ "$(jq -r '.vless // empty' "$path")" != "" ]] || return 0
  jq -e '[.vless.reserve_users[]?.assigned_id | select(.!=null)] as $ids | ($ids|length)==($ids|unique|length)' "$path" >/dev/null || fail "VLESS 固定槽位存在重复占用。"
  # 删除线路后保留 assigned_id 作为退役标记，防止旧 UUID 在未来被其他线路复用。
}

allocate_hy2_slot() {
  local target_id="${1:-}" slot_json
  if [[ -n "$target_id" ]]; then
    slot_json="$(jq -c --arg id "$target_id" '[.hy2.reserve_users[] | select(.assigned_id==$id)][0] // empty' "$STATE_FILE")"
  fi
  if [[ -z "${slot_json:-}" ]]; then
    slot_json="$(jq -c '[.hy2.reserve_users[] | select(.assigned_id==null and (.retired // false)==false)][0] // empty' "$STATE_FILE")"
  fi
  [[ -n "$slot_json" ]] || fail "Hysteria 2 可用固定凭证槽位已用尽（已分配或退役共 256 条）。"
  ALLOC_HY2_SLOT="$(jq -r '.slot' <<<"$slot_json")"
  ALLOC_HY2_USER="$(jq -r '.name' <<<"$slot_json")"
  ALLOC_HY2_PASSWORD="$(jq -r '.password' <<<"$slot_json")"
  ALLOC_HY2_PORT="$(jq -r '.local_port' <<<"$slot_json")"
  [[ -n "$ALLOC_HY2_SLOT" && -n "$ALLOC_HY2_USER" && -n "$ALLOC_HY2_PASSWORD" && "$ALLOC_HY2_PORT" =~ ^[0-9]+$ ]] || fail "Hysteria 2 预分配槽位池损坏。"
}

release_orphaned_hy2_slots() {
  local state_path="$1"
  [[ "$(jq -r '.hy2 // empty' "$state_path")" != "" ]] || return 0
  jq -e '[.hy2.reserve_users[]?.assigned_id | select(.!=null)] as $ids | ($ids|length)==($ids|unique|length)' "$state_path" >/dev/null || fail "Hysteria 2 固定槽位存在重复占用。"
  # 删除线路后保留 assigned_id 作为退役标记，防止旧用户名和密码在未来被其他线路复用。
}

validate_slot_references() {
  local state_path="$1"
  python3 - "$state_path" <<'PY_VALIDATE_SLOT_REFERENCES'
import collections
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
relays = {str(x.get('id')): x for x in state.get('relays', [])}
upstreams = {str(x.get('id')): x for x in state.get('upstream_relays', [])}
temps = {str(x.get('id')): x for x in state.get('temporary_nodes', [])}

all_ids = list(relays) + list(upstreams) + list(temps)
duplicates = [x for x, n in collections.Counter(all_ids).items() if n > 1]
if duplicates:
    raise SystemExit('线路和临时节点 ID 重复：' + ', '.join(sorted(duplicates)))

pools = {}
for proto in ('vless', 'hy2'):
    root = state.get(proto) or {}
    slots = root.get('reserve_users', [])
    slot_ids = [str(x.get('slot') or '') for x in slots]
    if any(not x for x in slot_ids):
        raise SystemExit(f'{proto.upper()} 固定槽位存在空 slot。')
    repeated_slots = [x for x, n in collections.Counter(slot_ids).items() if n > 1]
    if repeated_slots:
        raise SystemExit(f'{proto.upper()} 固定槽位编号重复：' + ', '.join(sorted(repeated_slots)))
    assigned = [str(x.get('assigned_id')) for x in slots if x.get('assigned_id') is not None]
    repeated_ids = [x for x, n in collections.Counter(assigned).items() if n > 1]
    if repeated_ids:
        raise SystemExit(f'{proto.upper()} 固定槽位存在重复占用：' + ', '.join(sorted(repeated_ids)))
    pools[proto] = {str(x['slot']): x for x in slots}

claimed = set()

def claim(proto, entity_id, reserve_slot, label):
    if not reserve_slot:
        raise SystemExit(f'{label} 缺少 {proto.upper()} reserve_slot。')
    slot = pools.get(proto, {}).get(str(reserve_slot))
    if slot is None:
        raise SystemExit(f'{label} 引用了不存在的 {proto.upper()} 槽位 {reserve_slot}。')
    if str(slot.get('assigned_id') or '') != str(entity_id):
        raise SystemExit(
            f'{label} 与 {proto.upper()} 槽位 {reserve_slot} 的 assigned_id 不一致：'
            f'{slot.get("assigned_id")!r}'
        )
    key = (proto, str(reserve_slot))
    if key in claimed:
        raise SystemExit(f'{proto.upper()} 活跃槽位 {reserve_slot} 被多个节点引用。')
    claimed.add(key)

for rid, relay in relays.items():
    if relay.get('vless') is not None:
        claim('vless', rid, (relay.get('vless') or {}).get('reserve_slot'), f'VPS 中转线路 {rid}')
    if relay.get('hy2') is not None:
        claim('hy2', rid, (relay.get('hy2') or {}).get('reserve_slot'), f'VPS 中转线路 {rid}')

for uid, upstream in upstreams.items():
    claim('vless', uid, upstream.get('reserve_slot'), f'上游中转线路 {uid}')

for tid, temp in temps.items():
    source_type = temp.get('source_type')
    source_id = str(temp.get('source_id') or '')
    if source_type == 'vps':
        source = relays.get(source_id)
    elif source_type == 'upstream':
        source = upstreams.get(source_id)
    else:
        raise SystemExit(f'临时节点 {tid} 的 source_type 无效。')
    if source is None:
        raise SystemExit(f'临时节点 {tid} 的来源 {source_id} 已不存在。')
    if temp.get('vless') is not None:
        if source_type == 'vps' and source.get('vless') is None:
            raise SystemExit(f'临时节点 {tid} 请求了来源未启用的 VLESS。')
        claim('vless', tid, (temp.get('vless') or {}).get('reserve_slot'), f'临时节点 {tid}')
    if temp.get('hy2') is not None:
        if source_type != 'vps' or source.get('hy2') is None:
            raise SystemExit(f'临时节点 {tid} 请求了来源未启用的 HY2。')
        claim('hy2', tid, (temp.get('hy2') or {}).get('reserve_slot'), f'临时节点 {tid}')

print('槽位引用完整性检查通过。')
PY_VALIDATE_SLOT_REFERENCES
}

prepare_add_or_overwrite() {
  local remote_ip="$1" remote_port="$2" node_name="$3"
  valid_ipv4 "$remote_ip" || fail "落地 IP 无效。"
  valid_port "$remote_port" || fail "落地端口无效。"
  [[ -n "$node_name" && "$node_name" != *$'\n'* && "$node_name" != *$'\r'* ]] || fail "线路名称无效。"

  require_relay_subscription_registration || return 1

  local count old relay_id now candidate test_vless test_hy2 remote_hy2 old_state
  old_state="$(mktemp --suffix=.json /tmp/jp-relay-before-ticket.XXXXXX)"
  TMP_FILES+=("$old_state")
  cp -a "$STATE_FILE" "$old_state"
  count="$(jq --arg n "$node_name" '[.relays[]|select(.name==$n)]|length' "$STATE_FILE")"
  (( count <= 1 )) || fail "状态中存在多个同名线路。"
  now="$(date --iso-8601=seconds)"
  candidate="$(mktemp --suffix=.json /tmp/jp-relay-candidate.XXXXXX)"
  TMP_FILES+=("$candidate")

  if (( count == 1 )); then
    relay_id="$(jq -r --arg n "$node_name" '.relays[]|select(.name==$n)|.id' "$STATE_FILE")"
    python3 - "$STATE_FILE" "$candidate" "$relay_id" "$remote_ip" "$remote_port" "$now" <<'PY_OVERWRITE'
import json,sys
from pathlib import Path
src,dst,rid,ip,port,now=sys.argv[1:]
s=json.loads(Path(src).read_text(encoding="utf-8"))
for r in s["relays"]:
    if r["id"]==rid:
        r["remote_ip"]=ip; r["remote_port"]=int(port); r["updated_at"]=now
s["updated_at"]=now
Path(dst).write_text(json.dumps(s,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
PY_OVERWRITE
    echo "覆盖同名线路并复用原有双协议凭证。"
  else
    relay_id="$(printf '%s:%s:%s' "$node_name" "$remote_ip" "$remote_port" | sha256sum | awk '{print "relay-" substr($1,1,20)}')"
    local vless_json='null' hy2_json='null'
    if mode_has_vless; then
      local client_uuid client_email reserve_slot outbound_uuid key_output private_key public_key short_id
      allocate_vless_slot "$relay_id"
      test_vless="$ALLOC_VLESS_PORT"
      client_uuid="$ALLOC_VLESS_UUID"; client_email="$ALLOC_VLESS_EMAIL"; reserve_slot="$ALLOC_VLESS_SLOT"
      outbound_uuid="$(new_uuid)"
      key_output="$("$XRAY" x25519)"
      parse_x25519_keys "$key_output"
      private_key="$GENERATED_PRIVATE_KEY"; public_key="$GENERATED_PUBLIC_KEY"; short_id="$(openssl rand -hex 8)"
      vless_json="$(jq -n \
        --arg client_uuid "$client_uuid" --arg email "$client_email" --arg reserve_slot "$reserve_slot" \
        --arg outbound_uuid "$outbound_uuid" --arg private "$private_key" --arg public "$public_key" --arg sid "$short_id" \
        --arg outtag "vless-out-${relay_id}" --arg testtag "vless-test-${relay_id}" --argjson testport "$test_vless" \
        '{client_uuid:$client_uuid,client_email:$email,reserve_slot:$reserve_slot,outbound_uuid:$outbound_uuid,remote_reality:{private_key:$private,public_key:$public,short_id:$sid},outbound_tag:$outtag,test_inbound_tag:$testtag,test_socks_port:$testport}')"
    fi
    if mode_has_hy2; then
      local material client_user client_password reserve_slot outbound_password outbound_obfs
      allocate_hy2_slot "$relay_id"
      test_hy2="$ALLOC_HY2_PORT"
      client_user="$ALLOC_HY2_USER"; client_password="$ALLOC_HY2_PASSWORD"; reserve_slot="$ALLOC_HY2_SLOT"
      material="$(mktemp --suffix=.json /tmp/jp-hy2-material.XXXXXX)"
      TMP_FILES+=("$material")
      create_remote_hy2_material "$relay_id" "$material"
      outbound_password="$(random_secret)"
      outbound_obfs="$(random_secret)"
      hy2_json="$(jq -n \
        --arg client_user "$client_user" --arg client_password "$client_password" --arg reserve_slot "$reserve_slot" \
        --arg outbound_password "$outbound_password" --arg outbound_obfs "$outbound_obfs" \
        --arg outtag "hy2-out-${relay_id}" --arg testtag "hy2-test-${relay_id}" --argjson testport "$test_hy2" \
        --arg server_name "$(jq -r '.server_name' "$material")" \
        --arg cert_pem "$(jq -r '.certificate_pem' "$material")" \
        --arg key_pem "$(jq -r '.key_pem' "$material")" \
        --arg fp "$(jq -r '.fingerprint' "$material")" \
        --arg pinhex "$(jq -r '.pin_hex' "$material")" \
        --arg pinb64 "$(jq -r '.public_key_sha256' "$material")" \
        '{client_user:$client_user,client_password:$client_password,reserve_slot:$reserve_slot,outbound_password:$outbound_password,outbound_obfs_password:$outbound_obfs,outbound_tag:$outtag,test_inbound_tag:$testtag,test_socks_port:$testport,outbound_server_name:$server_name,remote_certificate_pem:$cert_pem,remote_key_pem:$key_pem,remote_certificate_fingerprint:$fp,remote_certificate_pin_hex:$pinhex,remote_certificate_public_key_sha256:$pinb64}')"
    fi
    jq \
      --arg id "$relay_id" --arg name "$node_name" --arg ip "$remote_ip" --argjson port "$remote_port" \
      --argjson vless "$vless_json" --argjson hy2 "$hy2_json" --arg now "$now" \
      '.relays += [{id:$id,name:$name,remote_ip:$ip,remote_port:$port,vless:$vless,hy2:$hy2,created_at:$now,updated_at:$now}] |
       (if $vless != null then (.vless.reserve_users[] | select(.slot==$vless.reserve_slot)).assigned_id=$id else . end) |
       (if $hy2 != null then (.hy2.reserve_users[] | select(.slot==$hy2.reserve_slot)).assigned_id=$id else . end) |
       .updated_at=$now' \
      "$STATE_FILE" > "$candidate"
  fi

  local staging package_dir key
  staging="$(mktemp -d "${PACKAGE_ROOT}/.${relay_id}.staging.XXXXXX")"
  TMP_FILES+=("$staging")
  generate_client_files "$candidate" "$relay_id" "$staging" relay >/dev/null
  cat > "$staging/使用说明.txt" <<EOF_RELAY_HELP
线路：${node_name}
协议模式：$(jq -r '.protocol_mode' "$candidate")
日本入口：$(jq -r '.public_ip' "$candidate"):$(jq -r '.listen_port' "$candidate")
落地入口：${remote_ip}:${remote_port}

把“落地VPS对接密钥.txt”中的完整 JPR3 密钥粘贴到落地脚本顶部，然后在落地 VPS 上运行。
落地脚本不会再询问任何问题，会自动按 JPR3 安装单协议或双协议。
EOF_RELAY_HELP
  chmod 600 "$staging"/*

  apply_candidate_with_rollback "$candidate"

  if ! key="$(make_pairing_key "$STATE_FILE" "$relay_id")"; then
    echo "副机注册票据生成失败，正在恢复新建线路前的状态……" >&2
    if apply_candidate_with_rollback "$old_state"; then
      fail "副机注册票据生成失败；线路、槽位和运行配置已回滚。请确认中转主机能连接订阅中心后重试。"
    fi
    fail "副机注册票据生成失败，且自动回滚未完成；请立即生成诊断报告。"
  fi
  printf '%s\n' "$key" > "$staging/落地VPS对接密钥.txt"
  chmod 600 "$staging/落地VPS对接密钥.txt"

  package_dir="${PACKAGE_ROOT}/${relay_id}"
  rm -rf -- "${package_dir}.old" 2>/dev/null || true
  [[ ! -d "$package_dir" ]] || mv "$package_dir" "${package_dir}.old"
  mv "$staging" "$package_dir"
  rm -rf -- "${package_dir}.old"

  log "日本中转线路配置成功"
  echo "线路：${node_name}"
  echo "协议模式：$(jq -r '.protocol_mode' "$STATE_FILE")"
  echo "日本入口：$(jq -r '.public_ip' "$STATE_FILE"):$(jq -r '.listen_port' "$STATE_FILE")"
  echo "落地入口：${remote_ip}:${remote_port}"
  echo "线路已通过运行时接口生效；Xray 主进程未重启。"
  echo "客户端配置目录：${package_dir}"
  echo
  echo "==================== 落地 VPS JPR3 对接密钥 ===================="
  echo "$key"
  echo "================================================================"
}

prepare_add_or_overwrite_upstream() {
  local proxy_protocol="$1" host="$2" port="$3" username="$4" password="$5" node_name="$6"
  mode_has_vless || fail "该功能需要 VLESS + REALITY；当前主机只安装了 Hysteria 2。"
  [[ "$proxy_protocol" == "http" || "$proxy_protocol" == "socks" ]] || fail "不支持的上游代理协议。"
  valid_port "$port" || fail "上游代理端口无效。"
  [[ -n "$host" && -n "$username" && -n "$password" && -n "$node_name" ]] || fail "上游代理参数不完整。"

  local protocol_label count upstream_id now candidate test_port client_uuid exit_ip
  protocol_label="HTTP/HTTPS"; [[ "$proxy_protocol" == "socks" ]] && protocol_label="SOCKS5"
  echo "正在验证上游代理地址、凭证和 HTTPS 访问能力……"
  if ! probe_external_upstream "$proxy_protocol" "$host" "$port" "$username" "$password"; then
    fail "上游代理验证失败：${PROBE_REASON:-未知错误}"
  fi
  exit_ip="$PROBE_EXIT_IP"
  echo "上游代理验证成功，当前动态出口：${exit_ip}"

  count="$(jq --arg n "$node_name" '[.upstream_relays[]? | select(.name==$n)] | length' "$STATE_FILE")"
  (( count <= 1 )) || fail "状态中存在多个同名动态代理线路。"
  now="$(date --iso-8601=seconds)"
  candidate="$(mktemp --suffix=.json /tmp/jp-upstream-candidate.XXXXXX)"
  TMP_FILES+=("$candidate")

  if (( count == 1 )); then
    upstream_id="$(jq -r --arg n "$node_name" '.upstream_relays[]|select(.name==$n)|.id' "$STATE_FILE")"
    python3 - "$STATE_FILE" "$candidate" "$upstream_id" "$proxy_protocol" "$protocol_label" "$host" "$port" "$username" "$password" "$exit_ip" "$now" <<'PY_UPSTREAM_OVERWRITE'
import json,sys
from pathlib import Path
src,dst,rid,proto,label,host,port,user,password,exit_ip,now=sys.argv[1:]
s=json.loads(Path(src).read_text(encoding='utf-8'))
for r in s.get('upstream_relays',[]):
    if r['id']==rid:
        r.update(proxy_protocol=proto,protocol_label=label,host=host,port=int(port),username=user,password=password,last_exit_ip=exit_ip,updated_at=now)
s['updated_at']=now
Path(dst).write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
PY_UPSTREAM_OVERWRITE
    echo "覆盖同名动态代理线路，并复用原 VLESS UUID。"
  else
    upstream_id="$(printf '%s:%s:%s:%s' "$node_name" "$proxy_protocol" "$host" "$port" | sha256sum | awk '{print "upstream-" substr($1,1,20)}')"
    allocate_vless_slot "$upstream_id"
    test_port="$ALLOC_VLESS_PORT"
    client_uuid="$ALLOC_VLESS_UUID"
    python3 - "$STATE_FILE" "$candidate" "$upstream_id" "$node_name" "$proxy_protocol" "$protocol_label" "$host" "$port" "$username" "$password" "$client_uuid" "$test_port" "$exit_ip" "$now" <<'PY_UPSTREAM_NEW'
import json,sys
from pathlib import Path
(src,dst,rid,name,proto,label,host,port,user,password,uuid,test_port,exit_ip,now)=sys.argv[1:]
s=json.loads(Path(src).read_text(encoding='utf-8'))
s.setdefault('upstream_relays',[]).append({
 'id':rid,'name':name,'kind':'upstream','proxy_protocol':proto,'protocol_label':label,
 'host':host,'port':int(port),'username':user,'password':password,
 'client_uuid':uuid,'client_email':f'{rid}@upstream.local',
 'outbound_tag':f'upstream-out-{rid}','test_inbound_tag':f'upstream-test-{rid}',
 'test_socks_port':int(test_port),'last_exit_ip':exit_ip,
 'created_at':now,'updated_at':now
})
s['updated_at']=now
Path(dst).write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
PY_UPSTREAM_NEW
    tmp_slot="$(mktemp --suffix=.json /tmp/vvv-upstream-slot.XXXXXX)"; TMP_FILES+=("$tmp_slot")
    jq --arg id "$upstream_id" --arg email "$ALLOC_VLESS_EMAIL" --arg slot "$ALLOC_VLESS_SLOT" \
      '(.upstream_relays[]|select(.id==$id)).client_email=$email |
       (.upstream_relays[]|select(.id==$id)).reserve_slot=$slot |
       (.vless.reserve_users[]|select(.slot==$slot)).assigned_id=$id' "$candidate" > "$tmp_slot"
    install -m600 "$tmp_slot" "$candidate"
  fi

  local staging package_dir
  staging="$(mktemp -d "${PACKAGE_ROOT}/.${upstream_id}.staging.XXXXXX")"
  TMP_FILES+=("$staging")
  generate_client_files "$candidate" "$upstream_id" "$staging" upstream >/dev/null
  cat > "$staging/使用说明.txt" <<EOF_UPSTREAM_HELP
线路：${node_name}
上游协议：${protocol_label}
上游地址：${host}:${port}
日本加密入口：$(jq -r '.public_ip' "$candidate"):$(jq -r '.listen_port' "$candidate")
客户端协议：VLESS + XTLS Vision + REALITY
UDP：服务器端拒绝，避免绕过动态代理出口
EOF_UPSTREAM_HELP
  chmod 600 "$staging"/*

  apply_candidate_with_rollback "$candidate"
  package_dir="${PACKAGE_ROOT}/${upstream_id}"
  rm -rf -- "${package_dir}.old" 2>/dev/null || true
  [[ ! -d "$package_dir" ]] || mv "$package_dir" "${package_dir}.old"
  mv "$staging" "$package_dir"
  rm -rf -- "${package_dir}.old"

  log "动态代理中转线路配置成功"
  show_upstream_client_config "$upstream_id"
  refresh_upstream_status "$upstream_id" || true
  echo
  echo "==================== 线路状态 ===================="
  print_protocol_status "VLESS + REALITY" "$UPSTREAM_STATUS" "$UPSTREAM_REASON" "$UPSTREAM_TIME" "$UPSTREAM_EXIT_IP"
}

prompt_new_upstream_relay() {
  mode_has_vless || { echo "该功能需要 VLESS + REALITY；当前主机只安装了 Hysteria 2。"; return 0; }
  local choice proxy_protocol protocol_label input parsed host port username password node_name auto_name same_count overwrite_choice
  while true; do
    echo
    echo "请选择代理协议："
    echo "1. HTTP/HTTPS 代理"
    echo "2. SOCKS5 代理"
    read -r -p "请输入编号：" choice
    case "$choice" in
      1) proxy_protocol="http"; protocol_label="HTTP/HTTPS"; break ;;
      2) proxy_protocol="socks"; protocol_label="SOCKS5"; break ;;
      *) echo "请输入数字 1 或 2。" ;;
    esac
  done

  while true; do
    echo
    echo '格式示例：gw.dataimpulse.com:10000:用户名:密码'
    echo '字段包含冒号时示例：gw.dataimpulse.com:10000:"user:name":"pass:word"'
    read -r -p "请输入完整线路（主机:端口:用户:密码）：" input
    [[ -n "$input" ]] || { echo "线路不能为空。"; continue; }
    parsed="$(mktemp --suffix=.json /tmp/jp-upstream-parse.XXXXXX)"
    TMP_FILES+=("$parsed")
    if ! parse_error="$(parse_upstream_spec "$input" "$parsed" 2>&1)"; then
      echo "输入错误：$parse_error"
      continue
    fi
    host="$(jq -r '.host' "$parsed")"; port="$(jq -r '.port' "$parsed")"
    username="$(jq -r '.username' "$parsed")"; password="$(jq -r '.password' "$parsed")"
    break
  done

  while true; do
    read -r -p "请输入线路名称，直接回车自动生成：" input
    input="$(printf '%s' "$input" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    if [[ -n "$input" ]]; then
      [[ "$input" != *$'\n'* && "$input" != *$'\r'* && "$input" != *$'\t'* ]] || { echo "名称不能包含控制字符。"; continue; }
      node_name="$input"
    else
      auto_name="${protocol_label}-${host}:${port}"
      node_name="$auto_name"
      echo "自动生成名称：$node_name"
    fi
    same_count="$(jq --arg n "$node_name" '[.upstream_relays[]? | select(.name==$n)] | length' "$STATE_FILE")"
    if (( same_count == 0 )); then break; fi
    echo "检测到同名线路“${node_name}”。"
    echo "1. 覆盖原线路并复用原 VLESS UUID"
    echo "2. 重新输入名称"
    read -r -p "请选择：" overwrite_choice
    case "$overwrite_choice" in 1) break;; 2) continue;; *) echo "请输入 1 或 2。";; esac
  done

  CURRENT_STEP="新建或覆盖 HTTP/HTTPS/SOCKS5 中转线路"
  log "$CURRENT_STEP"
  prepare_add_or_overwrite_upstream "$proxy_protocol" "$host" "$port" "$username" "$password" "$node_name"
}

require_relay_subscription_registration() {
  [[ -s /etc/vvv/client.json ]] || fail "中转主机尚未注册订阅中心。请先在 vps 菜单完成订阅中心注册，再新建 VPS 副机中转线路。"
  [[ -x /usr/local/lib/vvv/sync_agent.py ]] || fail "订阅同步程序不存在，无法为 JPR3 生成受限注册票据。"
  local role
  role="$(jq -r '.role // empty' /etc/vvv/client.json 2>/dev/null || true)"
  [[ "$role" == "relay" || "$role" == "center-relay" ]] || fail "当前订阅中心登记角色不是中转主机，无法签发副机注册票据。"
}

request_subscription_bootstrap() {
  local relay_id="$1" bootstrap
  require_relay_subscription_registration || return 1
  bootstrap="$(python3 /usr/local/lib/vvv/sync_agent.py relay-ticket "$relay_id")" || fail "订阅中心拒绝签发该线路的副机注册票据。线路状态已保留在升级前状态。"
  jq -e --arg id "$relay_id" '
    (.api_base_url|type=="string" and length>0) and
    (.relay_id==$id) and
    (.registration_token|type=="string" and length>=20)
  ' <<<"$bootstrap" >/dev/null || fail "订阅中心返回的副机注册票据不完整。"
  printf '%s' "$bootstrap"
}

make_pairing_key() {
  local state_path="$1" relay_id="$2" subscription_bootstrap="${3:-}"
  [[ -n "$subscription_bootstrap" ]] || subscription_bootstrap="$(request_subscription_bootstrap "$relay_id")" || return 1
  python3 - "$state_path" "$relay_id" "$subscription_bootstrap" <<'PY_JPR3'
import base64,hashlib,json,sys,zlib
from datetime import datetime,timezone
from pathlib import Path
s=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
r=next(x for x in s["relays"] if x["id"]==sys.argv[2])
try:
    subscription_bootstrap=json.loads(sys.argv[3])
except Exception as exc:
    raise SystemExit(f"订阅中心注册票据无法解析：{exc}")
if not isinstance(subscription_bootstrap,dict) or not subscription_bootstrap.get("api_base_url") or not subscription_bootstrap.get("registration_token") or subscription_bootstrap.get("relay_id") != r["id"]:
    raise SystemExit("订阅中心注册票据缺失或与线路不匹配。")
payload={
 "schema":4,"type":"jp-relay-landing","protocol_mode":s["protocol_mode"],
 "relay_id":r["id"],"node_name":r["name"],
 "japan_public_ip":s["public_ip"],"japan_port":int(s["listen_port"]),
 "remote_public_ip":r["remote_ip"],"remote_public_port":int(r["remote_port"]),
 "sni":s["sni"],"hy2_limit_mbps":int(s.get("hy2_limit_mbps") or 50),"xray_version":s["xray_version"],"sing_box_version":s["sing_box_version"],
 "vless":None,"hy2":None,"subscription_bootstrap":subscription_bootstrap,
 "issued_at":datetime.now(timezone.utc).isoformat()
}
if r.get("vless"):
    rv=r["vless"]; v=s["vless"]
    payload["vless"]={
      "japan_client_uuid":rv["client_uuid"],
      "japan_reality_public_key":v["reality"]["public_key"],
      "japan_reality_short_id":v["reality"]["short_id"],
      "remote_uuid":rv["outbound_uuid"],
      "remote_reality_private_key":rv["remote_reality"]["private_key"],
      "remote_reality_public_key":rv["remote_reality"]["public_key"],
      "remote_reality_short_id":rv["remote_reality"]["short_id"]
    }
if r.get("hy2"):
    rh=r["hy2"]; h=s["hy2"]
    payload["hy2"]={
      "japan_client_password":rh["client_password"],
      "japan_obfs_password":h["obfs_password"],
      "japan_server_name":h["server_name"],
      "japan_certificate_fingerprint":h["certificate_fingerprint"],
      "japan_certificate_pin_hex":h["certificate_pin_hex"],
      "japan_certificate_public_key_sha256":h["certificate_public_key_sha256"],
      "remote_password":rh["outbound_password"],
      "remote_obfs_password":rh["outbound_obfs_password"],
      "remote_server_name":rh["outbound_server_name"],
      "remote_certificate_pem":rh["remote_certificate_pem"],
      "remote_key_pem":rh["remote_key_pem"],
      "remote_certificate_fingerprint":rh["remote_certificate_fingerprint"],
      "remote_certificate_pin_hex":rh["remote_certificate_pin_hex"],
      "remote_certificate_public_key_sha256":rh["remote_certificate_public_key_sha256"]
    }
raw=json.dumps(payload,ensure_ascii=False,separators=(",",":")).encode()
packed=zlib.compress(raw,9)
enc=base64.urlsafe_b64encode(packed).decode().rstrip("=")
chk=hashlib.sha256(packed).hexdigest()[:20]
key=f"JPR3.{enc}.{chk}"
if len(key) >= 3500:
    raise SystemExit(f"压缩后的 JPR3 对接密钥仍过长（{len(key)} 字符），拒绝生成可能被终端截断的密钥。")
print(key)
PY_JPR3
}

probe_proxy() {
  local socks_port="$1" expected_ip="$2"
  local result url ip err code seconds last_error=""
  PROBE_TIME=""; PROBE_EXIT_IP=""; PROBE_REASON=""
  for url in https://api.ipify.org https://ipv4.icanhazip.com; do
    err="$(mktemp /tmp/jp-probe-ip.XXXXXX)"
    TMP_FILES+=("$err")
    if ip="$(curl -4sS --socks5-hostname "127.0.0.1:${socks_port}" --connect-timeout 7 --max-time 22 "$url" 2>"$err" | tr -d '[:space:]')"; then
      if valid_ipv4 "$ip"; then
        PROBE_EXIT_IP="$ip"
        if [[ "$ip" != "$expected_ip" ]]; then
          PROBE_REASON="出口 IP 为 ${ip}，预期为 ${expected_ip}。"
          return 1
        fi
        break
      fi
    fi
    last_error="$(tr '\n' ' ' < "$err" | sed 's/[[:space:]]\+/ /g')"
  done
  for url in https://www.gstatic.com/generate_204 https://www.google.com/generate_204; do
    err="$(mktemp /tmp/jp-probe-http.XXXXXX)"
    TMP_FILES+=("$err")
    if result="$(curl -sS --socks5-hostname "127.0.0.1:${socks_port}" --connect-timeout 7 --max-time 22 -o /dev/null -w '%{http_code}|%{time_total}' "$url" 2>"$err")"; then
      code="${result%%|*}"; seconds="${result#*|}"
      if [[ "$code" == "204" ]]; then
        PROBE_TIME="$(awk -v t="$seconds" 'BEGIN{printf "%.0f",t*1000}')"
        return 0
      fi
      last_error="${url} 返回 HTTP ${code}"
    else
      last_error="$(tr '\n' ' ' < "$err" | sed 's/[[:space:]]\+/ /g')"
    fi
  done
  PROBE_REASON="${last_error:-两个 generate_204 地址均失败。}"
  return 1
}

refresh_relay_status() {
  local relay_id="$1" expected port
  expected="$(jq -r --arg id "$relay_id" '.relays[]|select(.id==$id)|.remote_ip' "$STATE_FILE")"
  VLESS_STATUS="未启用"; VLESS_REASON=""; VLESS_TIME=""; VLESS_EXIT_IP=""
  HY2_STATUS="未启用"; HY2_REASON=""; HY2_TIME=""; HY2_EXIT_IP=""
UPSTREAM_STATUS="未检测"
UPSTREAM_REASON=""
UPSTREAM_TIME=""
UPSTREAM_EXIT_IP=""

  if mode_has_vless; then
    port="$(jq -r --arg id "$relay_id" '.relays[]|select(.id==$id)|.vless.test_socks_port' "$STATE_FILE")"
    if ! systemctl is-active --quiet xray; then
      VLESS_STATUS="离线"; VLESS_REASON="日本 Xray 服务未运行。"
    elif [[ -z "$(ss -H -lnt "sport = :${port}" 2>/dev/null || true)" ]]; then
      VLESS_STATUS="离线"; VLESS_REASON="本地 VLESS 测试端口 ${port} 未监听。"
    elif probe_proxy "$port" "$expected"; then
      VLESS_STATUS="在线"; VLESS_TIME="$PROBE_TIME"; VLESS_EXIT_IP="$PROBE_EXIT_IP"
    else
      VLESS_STATUS="离线"; VLESS_REASON="$PROBE_REASON"; VLESS_EXIT_IP="$PROBE_EXIT_IP"
    fi
  fi

  if mode_has_hy2; then
    port="$(jq -r --arg id "$relay_id" '.relays[]|select(.id==$id)|.hy2.test_socks_port' "$STATE_FILE")"
    if ! systemctl is-active --quiet sing-box; then
      HY2_STATUS="离线"; HY2_REASON="日本 sing-box 服务未运行。"
    elif [[ -z "$(ss -H -lnt "sport = :${port}" 2>/dev/null || true)" ]]; then
      HY2_STATUS="离线"; HY2_REASON="本地 Hysteria 2 测试端口 ${port} 未监听。"
    elif probe_proxy "$port" "$expected"; then
      HY2_STATUS="在线"; HY2_TIME="$PROBE_TIME"; HY2_EXIT_IP="$PROBE_EXIT_IP"
    else
      HY2_STATUS="离线"; HY2_REASON="$PROBE_REASON"; HY2_EXIT_IP="$PROBE_EXIT_IP"
    fi
  fi
}

probe_upstream_proxy() {
  local socks_port="$1" japan_ip result url ip err code seconds last_error=""
  PROBE_TIME=""; PROBE_EXIT_IP=""; PROBE_REASON=""
  japan_ip="$(jq -r '.public_ip' "$STATE_FILE")"
  for url in https://api.ipify.org https://ipv4.icanhazip.com; do
    err="$(mktemp /tmp/jp-upstream-probe.XXXXXX)"
    TMP_FILES+=("$err")
    ip="$(curl -4sS --socks5-hostname "127.0.0.1:${socks_port}" --connect-timeout 8 --max-time 25 "$url" 2>"$err" | tr -d '[:space:]' || true)"
    if valid_ipv4 "$ip"; then
      PROBE_EXIT_IP="$ip"
      [[ "$ip" != "$japan_ip" ]] || { PROBE_REASON="出口仍是日本 VPS IP，流量没有经过上游代理。"; return 1; }
      break
    fi
    last_error="$(tr '\n' ' ' < "$err" | sed 's/[[:space:]]\+/ /g')"
  done
  valid_ipv4 "$PROBE_EXIT_IP" || { PROBE_REASON="无法获取动态代理出口 IP：${last_error:-未知错误}"; return 1; }
  for url in https://www.gstatic.com/generate_204 https://www.google.com/generate_204; do
    if result="$(curl -sS --socks5-hostname "127.0.0.1:${socks_port}" --connect-timeout 8 --max-time 25 -o /dev/null -w '%{http_code}|%{time_total}' "$url" 2>/dev/null)"; then
      code="${result%%|*}"; seconds="${result#*|}"
      if [[ "$code" == "204" ]]; then
        PROBE_TIME="$(awk -v t="$seconds" 'BEGIN{printf "%.0f",t*1000}')"
        return 0
      fi
      last_error="${url} 返回 HTTP ${code}"
    fi
  done
  PROBE_REASON="${last_error:-动态代理无法访问检测网站。}"
  return 1
}

refresh_upstream_status() {
  local upstream_id="$1" port
  UPSTREAM_STATUS="离线"; UPSTREAM_REASON=""; UPSTREAM_TIME=""; UPSTREAM_EXIT_IP=""
  port="$(jq -r --arg id "$upstream_id" '.upstream_relays[]|select(.id==$id)|.test_socks_port' "$STATE_FILE")"
  if ! systemctl is-active --quiet xray; then
    UPSTREAM_REASON="日本 Xray 服务未运行。"
  elif [[ -z "$(ss -H -lnt "sport = :${port}" 2>/dev/null || true)" ]]; then
    UPSTREAM_REASON="本地动态代理测试端口 ${port} 未监听。"
  elif probe_upstream_proxy "$port"; then
    UPSTREAM_STATUS="在线"; UPSTREAM_TIME="$PROBE_TIME"; UPSTREAM_EXIT_IP="$PROBE_EXIT_IP"
  else
    UPSTREAM_REASON="$PROBE_REASON"; UPSTREAM_EXIT_IP="$PROBE_EXIT_IP"
  fi
  local tmp
  tmp="$(mktemp --suffix=.json /tmp/jp-upstream-exit.XXXXXX)"
  TMP_FILES+=("$tmp")
  jq --arg id "$upstream_id" --arg ip "$UPSTREAM_EXIT_IP" --arg now "$(date --iso-8601=seconds)" \
    '(.upstream_relays[]|select(.id==$id)).last_exit_ip=$ip | (.upstream_relays[]|select(.id==$id)).last_checked_at=$now | .updated_at=$now' "$STATE_FILE" > "$tmp" && \
    install -o root -g root -m 600 "$tmp" "$STATE_FILE" || true
  [[ "$UPSTREAM_STATUS" == "在线" ]]
}

print_protocol_status() {
  local label="$1" status="$2" reason="$3" time="$4" exit_ip="$5"
  local green='' red='' yellow='' reset=''
  [[ -t 1 ]] && { green=$'\033[1;32m'; red=$'\033[1;31m'; yellow=$'\033[1;33m'; reset=$'\033[0m'; }
  case "$status" in
    在线)
      printf '%s：%s在线%s' "$label" "$green" "$reset"
      [[ -z "$time" ]] || printf '（%s ms）' "$time"
      [[ -z "$exit_ip" ]] || printf '，出口 %s' "$exit_ip"
      printf '\n'
      ;;
    离线)
      printf '%s：%s离线%s\n' "$label" "$red" "$reset"
      [[ -z "$reason" ]] || echo "  原因：$reason"
      ;;
    *)
      printf '%s：%s未启用%s\n' "$label" "$yellow" "$reset"
      ;;
  esac
}

print_relay_status_header() {
  local relay_id="$1" name remote
  name="$(jq -r --arg id "$relay_id" '.relays[]|select(.id==$id)|.name' "$STATE_FILE")"
  remote="$(jq -r --arg id "$relay_id" '.relays[]|select(.id==$id)|(.remote_ip+":"+(.remote_port|tostring))' "$STATE_FILE")"
  echo "线路：$name"
  echo "落地：$remote"
  mode_has_vless && print_protocol_status "VLESS + REALITY" "$VLESS_STATUS" "$VLESS_REASON" "$VLESS_TIME" "$VLESS_EXIT_IP"
  mode_has_hy2 && print_protocol_status "Hysteria 2" "$HY2_STATUS" "$HY2_REASON" "$HY2_TIME" "$HY2_EXIT_IP"
}

show_client_config() {
  local relay_id="$1" dir="${PACKAGE_ROOT}/${relay_id}"
  generate_client_files "$STATE_FILE" "$relay_id" "$dir" relay >/dev/null
  echo
  echo "==================== 客户端配置 ===================="
  cat "$dir/客户端节点.txt"
  echo "===================================================="
  echo "配置目录：$dir"
}

show_upstream_client_config() {
  local upstream_id="$1" dir="${PACKAGE_ROOT}/${upstream_id}"
  generate_client_files "$STATE_FILE" "$upstream_id" "$dir" upstream >/dev/null
  echo
  echo "==================== 客户端配置 ===================="
  cat "$dir/客户端节点.txt"
  echo "===================================================="
  echo "配置目录：$dir"
}

mask_credential() {
  python3 - "$1" <<'PY_MASK'
import sys
v=sys.argv[1]
if len(v)<=4: print('*'*max(4,len(v)))
else: print(v[:2]+'*'*(min(12,len(v)-4))+v[-2:])
PY_MASK
}

show_upstream_details() {
  local upstream_id="$1" name label host port username
  name="$(jq -r --arg id "$upstream_id" '.upstream_relays[]|select(.id==$id)|.name' "$STATE_FILE")"
  label="$(jq -r --arg id "$upstream_id" '.upstream_relays[]|select(.id==$id)|.protocol_label' "$STATE_FILE")"
  host="$(jq -r --arg id "$upstream_id" '.upstream_relays[]|select(.id==$id)|.host' "$STATE_FILE")"
  port="$(jq -r --arg id "$upstream_id" '.upstream_relays[]|select(.id==$id)|.port' "$STATE_FILE")"
  username="$(jq -r --arg id "$upstream_id" '.upstream_relays[]|select(.id==$id)|.username' "$STATE_FILE")"
  echo "线路：$name"
  echo "上游协议：$label"
  echo "上游地址：${host}:${port}"
  echo "用户名：$(mask_credential "$username")"
  echo "密码：********"
  echo "日本入口：$(jq -r '.public_ip' "$STATE_FILE"):$(jq -r '.listen_port' "$STATE_FILE")"
  echo "客户端协议：VLESS + XTLS Vision + REALITY"
  echo "UDP：已禁用"
}

print_upstream_status_header() {
  local upstream_id="$1" name label host port
  name="$(jq -r --arg id "$upstream_id" '.upstream_relays[]|select(.id==$id)|.name' "$STATE_FILE")"
  label="$(jq -r --arg id "$upstream_id" '.upstream_relays[]|select(.id==$id)|.protocol_label' "$STATE_FILE")"
  host="$(jq -r --arg id "$upstream_id" '.upstream_relays[]|select(.id==$id)|.host' "$STATE_FILE")"
  port="$(jq -r --arg id "$upstream_id" '.upstream_relays[]|select(.id==$id)|.port' "$STATE_FILE")"
  echo "线路：$name"
  echo "上游：${label} ${host}:${port}"
  print_protocol_status "VLESS + REALITY" "$UPSTREAM_STATUS" "$UPSTREAM_REASON" "$UPSTREAM_TIME" "$UPSTREAM_EXIT_IP"
}

show_pairing_key() {
  local relay_id="$1" dir key
  dir="${PACKAGE_ROOT}/${relay_id}"
  mkdir -p "$dir"
  key="$(make_pairing_key "$STATE_FILE" "$relay_id")"
  printf '%s\n' "$key" > "$dir/落地VPS对接密钥.txt"
  chmod 700 "$dir"; chmod 600 "$dir/落地VPS对接密钥.txt"
  echo
  echo "==================== 落地 VPS JPR3 对接密钥 ===================="
  echo "$key"
  echo "================================================================"
}

build_delete_candidate() {
  local source_type="$1" source_id="$2" output="$3"
  python3 - "$STATE_FILE" "$output" "$source_type" "$source_id" <<'PY_BUILD_DELETE_CANDIDATE'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

src, dst, source_type, source_id = sys.argv[1:]
state = json.loads(Path(src).read_text(encoding='utf-8'))
if source_type == 'vps':
    collection = 'relays'
elif source_type == 'upstream':
    collection = 'upstream_relays'
else:
    raise SystemExit('删除来源类型无效。')

items = state.get(collection, [])
if not any(str(x.get('id')) == source_id for x in items):
    raise SystemExit('准备删除的正式线路已经不存在。')

removed_temps = []
kept_temps = []
for item in state.get('temporary_nodes', []):
    matches = item.get('source_type') == source_type and str(item.get('source_id')) == source_id
    if not matches:
        kept_temps.append(item)
        continue
    removed_temps.append(item)
    for proto in ('vless', 'hy2'):
        detail = item.get(proto) or {}
        reserve_slot = detail.get('reserve_slot')
        if not reserve_slot:
            continue
        pool = (state.get(proto) or {}).get('reserve_users', [])
        slot = next((x for x in pool if str(x.get('slot')) == str(reserve_slot)), None)
        if slot is None:
            raise SystemExit(f'依赖临时节点 {item.get("id")} 引用了不存在的 {proto.upper()} 槽位。')
        if str(slot.get('assigned_id') or '') != str(item.get('id')):
            raise SystemExit(f'依赖临时节点 {item.get("id")} 与 {proto.upper()} 槽位占用不一致。')
        slot['assigned_id'] = None
        slot['retired'] = True
        slot['retired_id'] = item.get('id')

state[collection] = [x for x in items if str(x.get('id')) != source_id]
state['temporary_nodes'] = kept_temps
state['updated_at'] = datetime.now(timezone.utc).isoformat()
Path(dst).write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
for item in removed_temps:
    print(item.get('name') or item.get('id'))
PY_BUILD_DELETE_CANDIDATE
}

perform_delete() {
  local relay_id="$1" name confirm candidate package_dir dependent_names
  CURRENT_STEP="删除 VPS 副机中转线路"
  name="$(jq -r --arg id "$relay_id" '.relays[]|select(.id==$id)|.name' "$STATE_FILE")"
  read -r -p "确认删除“${name}”？输入 Y 确认：" confirm
  case "$confirm" in [Yy]) ;; *) echo "已取消删除。"; return 0;; esac
  candidate="$(mktemp --suffix=.json /tmp/jp-delete.XXXXXX)"
  TMP_FILES+=("$candidate")
  dependent_names="$(build_delete_candidate vps "$relay_id" "$candidate")"
  package_dir="${PACKAGE_ROOT}/${relay_id}"
  apply_candidate_with_rollback "$candidate" "$package_dir"
  echo "线路“${name}”已删除。"
  if [[ -n "$dependent_names" ]]; then
    echo "同时清理了依赖该线路的临时节点："
    while IFS= read -r item; do [[ -z "$item" ]] || echo "  - $item"; done <<<"$dependent_names"
  fi
}

perform_delete_upstream() {
  local upstream_id="$1" name confirm candidate package_dir dependent_names
  CURRENT_STEP="删除 HTTP/HTTPS/SOCKS5 中转线路"
  name="$(jq -r --arg id "$upstream_id" '.upstream_relays[]|select(.id==$id)|.name' "$STATE_FILE")"
  read -r -p "确认删除“${name}”？输入 Y 确认：" confirm
  case "$confirm" in [Yy]) ;; *) echo "已取消删除。"; return 0;; esac
  candidate="$(mktemp --suffix=.json /tmp/jp-upstream-delete.XXXXXX)"
  TMP_FILES+=("$candidate")
  dependent_names="$(build_delete_candidate upstream "$upstream_id" "$candidate")"
  package_dir="${PACKAGE_ROOT}/${upstream_id}"
  apply_candidate_with_rollback "$candidate" "$package_dir"
  echo "线路“${name}”已删除。"
  if [[ -n "$dependent_names" ]]; then
    echo "同时清理了依赖该线路的临时节点："
    while IFS= read -r item; do [[ -z "$item" ]] || echo "  - $item"; done <<<"$dependent_names"
  fi
}

upstream_submenu() {
  local upstream_id="$1" action
  printf '正在检测动态代理实际出口……\r'
  refresh_upstream_status "$upstream_id" || true
  printf '%-60s\r' ''
  while true; do
    echo
    echo "========== HTTP/HTTPS/SOCKS5 中转线路 =========="
    print_upstream_status_header "$upstream_id"
    echo
    echo "1. 查看客户端配置"
    echo "2. 查看线路详情"
    echo "3. 检测线路状态"
    echo "4. 删除线路"
    echo "5. 返回"
    read -r -p "请选择：" action
    case "$action" in
      1) show_upstream_client_config "$upstream_id"; pause_return ;;
      2) show_upstream_details "$upstream_id"; pause_return ;;
      3) printf '正在重新检测……\r'; refresh_upstream_status "$upstream_id" || true; printf '%-60s\r' '' ;;
      4) perform_delete_upstream "$upstream_id"; pause_return; return ;;
      5) return ;;
      *) echo "请输入 1、2、3、4 或 5。" ;;
    esac
  done
}

prompt_new_relay() {
  local input remote_ip remote_port node_name country auto_name same_count choice default_port
  while true; do
    read -r -p "请输入落地 VPS 公网 IPv4：" input
    input="${input//[[:space:]]/}"
    [[ -n "$input" ]] || { echo "IP 地址不能为空。"; continue; }
    valid_ipv4 "$input" || { echo "请输入有效的公网 IPv4。"; continue; }
    remote_ip="$input"; break
  done

  default_port="553"
  while true; do
    read -r -p "请输入落地统一端口 [默认 ${default_port}]：" input
    input="${input//[[:space:]]/}"
    [[ -n "$input" ]] || input="$default_port"
    valid_port "$input" || { echo "端口必须是 1–65535 之间的数字。"; continue; }
    remote_port="$((10#$input))"; break
  done

  while true; do
    read -r -p "请输入服务器名称，直接回车自动生成：" input
    input="$(printf '%s' "$input" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    if [[ -n "$input" ]]; then
      [[ "$input" != *$'\n'* && "$input" != *$'\r'* ]] || { echo "名称不能包含换行。"; continue; }
      node_name="$input"
    else
      echo "正在查询 IP 所属国家……"
      if country="$(detect_country_code "$remote_ip")"; then
        auto_name="${country}-${remote_ip}:${remote_port}"
      else
        auto_name="${remote_ip}:${remote_port}"
        echo "国家查询失败，将直接使用 IP 和端口作为名称。"
      fi
      node_name="$auto_name"
      echo "自动生成名称：$node_name"
    fi
    same_count="$(jq --arg n "$node_name" '[.relays[]|select(.name==$n)]|length' "$STATE_FILE")"
    if (( same_count == 0 )); then break; fi
    echo "检测到同名线路“${node_name}”。"
    echo "1. 覆盖原线路并复用原密钥"
    echo "2. 重新输入名称"
    read -r -p "请选择：" choice
    case "$choice" in 1) break;; 2) continue;; *) echo "请输入 1 或 2。";; esac
  done

  CURRENT_STEP="新建或覆盖中转线路"
  log "$CURRENT_STEP"
  prepare_add_or_overwrite "$remote_ip" "$remote_port" "$node_name"
}

prompt_temp_ttl() {
  local value
  while true; do
    read -r -p "自动销毁时间 [默认 30 分钟]：" value
    value="${value//[[:space:]]/}"; [[ -n "$value" ]] || value=30
    if [[ "$value" =~ ^[0-9]+$ ]] && ((10#$value>=1 && 10#$value<=10080)); then TEMP_TTL_MINUTES="$((10#$value))"; return; fi
    echo "只允许输入 1-10080 的纯数字，单位为分钟。"
  done
}

install_temp_cleanup_timer() {
  cat > /usr/local/sbin/vvv-temp-cleanup <<'EOF_TEMP_CLEAN'
#!/usr/bin/env bash
exec /usr/local/sbin/jp-relay-manager --cleanup-temp
EOF_TEMP_CLEAN
  chmod 700 /usr/local/sbin/vvv-temp-cleanup
  cat > /etc/systemd/system/vvv-temp-cleanup.service <<'EOF_TEMP_SERVICE'
[Unit]
Description=Remove expired VVV temporary nodes
After=network-online.target xray.service sing-box.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/vvv-temp-cleanup
EOF_TEMP_SERVICE
  cat > /etc/systemd/system/vvv-temp-cleanup.timer <<'EOF_TEMP_TIMER'
[Unit]
Description=Check expired VVV temporary nodes every minute
[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
AccuracySec=10s
Persistent=true
[Install]
WantedBy=timers.target
EOF_TEMP_TIMER
  systemctl daemon-reload
  systemctl enable --now vvv-temp-cleanup.timer >/dev/null 2>&1 || true
}

create_temporary_node() {
  local source_type="$1" source_id="$2" source_name="$3" ttl="$4" custom_name="$5"
  local temp_id now expires_ts expires_at candidate vslot="" vuuid="" vemail="" hslot="" huser="" hpass=""
  now="$(date +%s)"; expires_ts="$((now+ttl*60))"; expires_at="$(date -u -d "@${expires_ts}" --iso-8601=seconds)"
  temp_id="temp-$(printf '%s:%s:%s:%s' "$source_type" "$source_id" "$now" "$(openssl rand -hex 8)" | sha256sum | awk '{print substr($1,1,20)}')"
  [[ -n "$custom_name" ]] || custom_name="临时-${source_name}-$(date +%H%M)"
  if [[ "$source_type" == vps ]]; then
    if jq -e --arg id "$source_id" '.relays[]|select(.id==$id)|.vless != null' "$STATE_FILE" >/dev/null; then
      allocate_vless_slot; vslot="$ALLOC_VLESS_SLOT"; vuuid="$ALLOC_VLESS_UUID"; vemail="$ALLOC_VLESS_EMAIL"
    fi
    if jq -e --arg id "$source_id" '.relays[]|select(.id==$id)|.hy2 != null' "$STATE_FILE" >/dev/null; then
      allocate_hy2_slot; hslot="$ALLOC_HY2_SLOT"; huser="$ALLOC_HY2_USER"; hpass="$ALLOC_HY2_PASSWORD"
    fi
  else
    allocate_vless_slot; vslot="$ALLOC_VLESS_SLOT"; vuuid="$ALLOC_VLESS_UUID"; vemail="$ALLOC_VLESS_EMAIL"
  fi
  [[ -n "$vslot" || -n "$hslot" ]] || fail "所选正式线路没有可以复制的协议。"
  candidate="$(mktemp --suffix=.json /tmp/vvv-temp-create.XXXXXX)"; TMP_FILES+=("$candidate")
  python3 - "$STATE_FILE" "$candidate" "$temp_id" "$custom_name" "$source_type" "$source_id" "$source_name" "$expires_ts" "$expires_at" "$vslot" "$vuuid" "$vemail" "$hslot" "$huser" "$hpass" <<'PY_TEMP_CREATE'
import json,sys
from pathlib import Path
(src,dst,tid,name,stype,sid,sname,expires_ts,expires_at,vslot,vuuid,vemail,hslot,huser,hpass)=sys.argv[1:]
s=json.loads(Path(src).read_text(encoding='utf-8'))
vless=None if not vslot else {'reserve_slot':vslot,'client_uuid':vuuid,'client_email':vemail}
hy2=None if not hslot else {'reserve_slot':hslot,'client_user':huser,'client_password':hpass}
s.setdefault('temporary_nodes',[]).append({'id':tid,'name':name,'source_type':stype,'source_id':sid,'source_name':sname,
 'vless':vless,'hy2':hy2,'created_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
 'expires_ts':int(expires_ts),'expires_at':expires_at})
if vslot:
    slot=next(x for x in s['vless']['reserve_users'] if x['slot']==vslot); slot['assigned_id']=tid
if hslot:
    slot=next(x for x in s['hy2']['reserve_users'] if x['slot']==hslot); slot['assigned_id']=tid
s['updated_at']=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
Path(dst).write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
PY_TEMP_CREATE
  apply_candidate_with_rollback "$candidate"
  install_temp_cleanup_timer
  echo "临时节点创建成功：${custom_name}"
  echo "自动销毁时间：${expires_at}（${ttl} 分钟后）"
  echo "副机和原正式线路均未修改。客户端刷新订阅后即可看到临时节点。"
}

prompt_create_temporary() {
  local source_type="$1" rows count choice source_id source_name ttl name
  if [[ "$source_type" == vps ]]; then
    mapfile -t rows < <(jq -r '.relays[]? | [.id,.name] | @tsv' "$STATE_FILE")
  else
    mapfile -t rows < <(jq -r '.upstream_relays[]? | [.id,.name] | @tsv' "$STATE_FILE")
  fi
  count="${#rows[@]}"
  (( count>0 )) || { echo "没有可复制的正式线路。"; return; }
  echo; echo "========== 从已有正式线路复制 =========="
  local i
  for ((i=0;i<count;i++)); do IFS=$'\t' read -r source_id source_name <<<"${rows[$i]}"; echo "$((i+1)). $source_name"; done
  echo "0. 返回"
  while true; do
    read -r -p "请选择正式线路：" choice
    [[ "$choice" == 0 ]] && return
    [[ "$choice" =~ ^[0-9]+$ ]] && ((10#$choice>=1 && 10#$choice<=count)) && break
    echo "请输入有效编号。"
  done
  IFS=$'\t' read -r source_id source_name <<<"${rows[$((10#$choice-1))]}"
  prompt_temp_ttl; ttl="$TEMP_TTL_MINUTES"
  read -r -p "请输入临时节点名称（回车自动生成）：" name
  name="$(printf '%s' "$name" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  [[ "$name" != *$'\n'* && "$name" != *$'\r'* ]] || { echo "名称不能包含换行。"; return 1; }
  create_temporary_node "$source_type" "$source_id" "$source_name" "$ttl" "$name"
}

retire_temporary_nodes() {
  local mode="$1" target="${2:-}" candidate count names
  candidate="$(mktemp --suffix=.json /tmp/vvv-temp-retire.XXXXXX)"; TMP_FILES+=("$candidate")
  names="$(python3 - "$STATE_FILE" "$candidate" "$mode" "$target" <<'PY_TEMP_RETIRE'
import json,sys,time
from pathlib import Path
src,dst,mode,target=sys.argv[1:]
s=json.loads(Path(src).read_text(encoding='utf-8')); now=time.time(); kept=[]; removed=[]
for item in s.get('temporary_nodes',[]):
    remove=(mode=='expired' and float(item.get('expires_ts') or 0)<=now) or (mode=='one' and item.get('id')==target)
    if not remove:
        kept.append(item); continue
    removed.append(item)
    v=item.get('vless') or {}; h=item.get('hy2') or {}
    if v.get('reserve_slot'):
        slot=next((x for x in (s.get('vless') or {}).get('reserve_users',[]) if x.get('slot')==v['reserve_slot']),None)
        if slot: slot.update(assigned_id=None,retired=True,retired_id=item.get('id'))
    if h.get('reserve_slot'):
        slot=next((x for x in (s.get('hy2') or {}).get('reserve_users',[]) if x.get('slot')==h['reserve_slot']),None)
        if slot: slot.update(assigned_id=None,retired=True,retired_id=item.get('id'))
s['temporary_nodes']=kept
if removed:
    s['updated_at']=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
Path(dst).write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
for item in removed: print(item.get('name') or item.get('id'))
PY_TEMP_RETIRE
)"
  count="$(printf '%s\n' "$names" | sed '/^$/d' | wc -l)"
  (( count>0 )) || return 2
  apply_candidate_with_rollback "$candidate"
  while IFS= read -r name; do [[ -z "$name" ]] || echo "临时节点已销毁：$name"; done <<<"$names"
}

cleanup_expired_temporary() {
  retire_temporary_nodes expired "" || { [[ $? == 2 ]] && return 0; return 1; }
}

temporary_submenu() {
  local temp_id="$1" action confirm
  while true; do
    echo; echo "========== 临时中转节点 =========="
    jq -r --arg id "$temp_id" '.temporary_nodes[]|select(.id==$id)|"名称：\(.name)\n来源：\(.source_name)\n创建时间：\(.created_at)\n自动销毁：\(.expires_at)"' "$STATE_FILE"
    echo "1. 立即提前销毁"; echo "0. 返回"
    read -r -p "请选择：" action
    case "$action" in
      1) read -r -p "输入 Y 确认提前销毁：" confirm; [[ "$confirm" =~ ^[Yy]$ ]] && retire_temporary_nodes one "$temp_id"; pause_return; return;;
      0) return;; *) echo "请输入 0 或 1。";;
    esac
  done
}

set_relay_manager_enabled() {
  local tmp
  tmp="$(mktemp --suffix=.json /tmp/jp-manager-enabled.XXXXXX)"
  TMP_FILES+=("$tmp")
  jq --arg now "$(date --iso-8601=seconds)" '.relay_manager_enabled=true | .updated_at=$now' "$STATE_FILE" > "$tmp"
  install -o root -g root -m 600 "$tmp" "$STATE_FILE"
}

pause_return() {
  local _
  read -r -p "按回车返回……" _
}

show_local_client_config() {
  echo
  log "本机客户端配置"
  generate_direct_client_files
}

show_not_enabled_menu() {
  local selection
  while true; do
    log "VPS 管理"
    echo "还未安装中转服务器。"
    echo
    echo "1. 安装中转服务器"
    echo "2. 查看本机客户端配置"
    read -r -p "请输入编号：" selection
    case "$selection" in
      1)
        CURRENT_STEP="启用中转服务器管理"
        set_relay_manager_enabled
        echo "中转服务器安装完毕。"
        return 0
        ;;
      2)
        show_local_client_config
        pause_return
        ;;
      *) echo "请输入数字 1 或 2。" ;;
    esac
  done
}

relay_submenu() {
  local relay_id="$1" action
  printf '正在检测两条协议的实际出口……\r'
  refresh_relay_status "$relay_id" || true
  printf '%-60s\r' ''
  while true; do
    echo
    echo "========== 中转线路 =========="
    print_relay_status_header "$relay_id"
    echo
    echo "1. 查看客户端配置"
    echo "2. 查看对接密钥"
    echo "3. 检测线路状态"
    echo "4. 删除线路"
    echo "5. 返回"
    read -r -p "请选择：" action
    case "$action" in
      1) show_client_config "$relay_id"; pause_return ;;
      2) show_pairing_key "$relay_id"; pause_return ;;
      3) printf '正在重新检测……\r'; refresh_relay_status "$relay_id" || true; printf '%-60s\r' '' ;;
      4) perform_delete "$relay_id"; pause_return; return ;;
      5) return ;;
      *) echo "请输入 1、2、3、4 或 5。" ;;
    esac
  done
}

management_menu() {
  local enabled total new_vps_index new_upstream_index temp_vps_index temp_upstream_index local_index selection entry_type entry_id entry_name
  local -a entries
  enabled="$(jq -r '.relay_manager_enabled' "$STATE_FILE")"
  if [[ "$enabled" != "true" ]]; then show_not_enabled_menu; fi
  cleanup_expired_temporary || true
  while true; do
    mapfile -t entries < <(jq -r '
      (.relays[]? | ["vps",.id,.name] | @tsv),
      (.upstream_relays[]? | ["upstream",.id,.name] | @tsv),
      (.temporary_nodes[]? | ["temp",.id,(.name + " [临时，到期 " + .expires_at + "]")] | @tsv)
    ' "$STATE_FILE")
    total="${#entries[@]}"
    new_vps_index=$((total + 1)); new_upstream_index=$((total + 2))
    temp_vps_index=$((total + 3)); temp_upstream_index=$((total + 4)); local_index=$((total + 5))
    log "中转线路管理"
    local i
    for ((i=0; i<total; i++)); do IFS=$'\t' read -r entry_type entry_id entry_name <<< "${entries[$i]}"; echo "$((i+1)). ${entry_name}"; done
    echo "${new_vps_index}. 新建 VPS 副机中转线路"
    echo "${new_upstream_index}. 新建 HTTP/HTTPS/SOCKS5 中转线路"
    echo "${temp_vps_index}. 创建临时 VPS 中转线路（从已有线路复制）"
    echo "${temp_upstream_index}. 创建临时 HTTP/HTTPS/SOCKS5 中转线路（从已有线路复制）"
    echo "${local_index}. 查看本机客户端配置"
    echo "0. 退出"
    read -r -p "请输入编号：" selection
    [[ "$selection" =~ ^[0-9]+$ ]] || { echo "请输入有效数字。"; continue; }
    selection="$((10#$selection))"; (( selection == 0 )) && return
    if (( selection == new_vps_index )); then prompt_new_relay; continue; fi
    if (( selection == new_upstream_index )); then prompt_new_upstream_relay; continue; fi
    if (( selection == temp_vps_index )); then prompt_create_temporary vps; continue; fi
    if (( selection == temp_upstream_index )); then prompt_create_temporary upstream; continue; fi
    if (( selection == local_index )); then show_local_client_config; pause_return; continue; fi
    (( selection >= 1 && selection <= total )) || { echo "编号超出范围。"; continue; }
    IFS=$'\t' read -r entry_type entry_id entry_name <<< "${entries[$((selection-1))]}"
    case "$entry_type" in vps) relay_submenu "$entry_id";; upstream) upstream_submenu "$entry_id";; temp) temporary_submenu "$entry_id";; esac
  done
}

install_shortcuts() {
  mkdir -p /usr/local/sbin
  # /usr/local/sbin/vps 只能由统一 VVV 管理器创建。
  # 中转管理器每次启动时仅维护自己的专用快捷命令，不能覆盖首页入口。
  cat > /usr/local/sbin/jp-show-nodes <<'EOF_SHOW'
#!/usr/bin/env bash
cat /root/日本VPS-客户端节点.txt
EOF_SHOW
  chmod 700 /usr/local/sbin/jp-show-nodes
  install_temp_cleanup_timer
}

check_runtime_environment() {
  [[ -f "$STATE_FILE" ]] || fail "尚未完成日本 VPS 初始化。"
  jq -e '.schema==3 and .role=="japan-hub" and (.relays|type=="array") and ((.upstream_relays // [])|type=="array")' "$STATE_FILE" >/dev/null || fail "JPR3 状态文件损坏。"
  command -v jq >/dev/null || fail "缺少 jq。"
  if mode_has_vless; then
    [[ -x "$XRAY" && -f "$XRAY_CFG" ]] || fail "VLESS 已启用，但 Xray 文件不完整。"
    systemctl is-active --quiet xray || { systemctl restart xray >/dev/null 2>&1 || true; sleep 2; }
    systemctl is-active --quiet xray || fail "Xray 服务未运行。"
  fi
  if mode_has_hy2; then
    [[ -x "$SING_BOX" && -f "$SING_CFG" ]] || fail "Hysteria 2 已启用，但 sing-box 文件不完整。"
    systemctl is-active --quiet sing-box || { systemctl restart sing-box >/dev/null 2>&1 || true; sleep 2; }
    systemctl is-active --quiet sing-box || fail "sing-box 服务未运行。"
  fi
}

acquire_manager_lock() {
  mkdir -p /run/lock
  exec 9>"$LOCK_FILE"
  flock -n 9 || fail "另一个 vps 管理窗口正在运行，请关闭后再试。"
}

bootstrap() {
  CURRENT_STEP="检查 Debian 系统"; log "$CURRENT_STEP"; check_debian

  if [[ ! -f "$STATE_FILE" ]]; then
    CURRENT_STEP="选择协议与统一端口"; log "$CURRENT_STEP"; prompt_initial_mode_and_port
    (( INSTALL_CANCELLED == 0 )) || { echo "已退出，未安装任何内容。"; return 0; }
  else
    INSTALL_MODE="$(jq -r '.protocol_mode' "$STATE_FILE")"
    INSTALL_PORT="$(jq -r '.listen_port' "$STATE_FILE")"
    HY2_LIMIT_MBPS="$(jq -r '.hy2_limit_mbps // 50' "$STATE_FILE")"
    echo "检测到现有 JPR3 状态：模式=${INSTALL_MODE}，端口=${INSTALL_PORT}，HY2 每连接强制上限=${HY2_LIMIT_MBPS}M。"
  fi

  CURRENT_STEP="刷新软件源并安装依赖"; log "$CURRENT_STEP"; upgrade_system_once
  CURRENT_STEP="检测官方最新稳定版"; log "$CURRENT_STEP"; resolve_core_versions
  CURRENT_STEP="配置 Swap"; log "$CURRENT_STEP"; configure_swap
  CURRENT_STEP="配置 BBR 与 UDP 缓冲区"; log "$CURRENT_STEP"; configure_network_tuning
  CURRENT_STEP="设置上海时区和每天 06:00 自动重启"; log "$CURRENT_STEP"; configure_timezone_and_daily_reboot

  if mode_has_vless "$INSTALL_MODE"; then
    CURRENT_STEP="检查 VLESS TCP 端口"; log "$CURRENT_STEP"; check_port_available tcp "$INSTALL_PORT" xray
    CURRENT_STEP="安装 Xray 最新稳定版"; log "$CURRENT_STEP"; install_xray_binary
    CURRENT_STEP="创建 Xray 服务"; log "$CURRENT_STEP"; create_xray_service
  fi
  if mode_has_hy2 "$INSTALL_MODE"; then
    CURRENT_STEP="检查 Hysteria 2 UDP 端口"; log "$CURRENT_STEP"; check_port_available udp "$INSTALL_PORT" sing-box
    CURRENT_STEP="安装 sing-box 最新稳定版"; log "$CURRENT_STEP"; install_sing_box_binary
    CURRENT_STEP="创建 sing-box 服务"; log "$CURRENT_STEP"; create_sing_box_service
  fi

  CURRENT_STEP="初始化 JPR3 永久参数"; log "$CURRENT_STEP"; initialize_state
  if mode_has_hy2 "$INSTALL_MODE"; then
    CURRENT_STEP="设置 Hysteria 2 证书权限"; log "$CURRENT_STEP"; ensure_hy2_certificate_permissions
  fi
  CURRENT_STEP="生成并启动代理服务"; log "$CURRENT_STEP"; activate_initial_state_with_fallback
  CURRENT_STEP="安装 vps 管理命令"; log "$CURRENT_STEP"; install_shortcuts
  CURRENT_STEP="生成日本直连节点"; log "$CURRENT_STEP"; generate_direct_client_files

  apt-get clean
  rm -rf /var/lib/apt/lists/*

  log "日本主机 VPS 安装成功"
  echo "协议模式：$(jq -r '.protocol_mode' "$STATE_FILE")"
  echo "统一端口：$(jq -r '.listen_port' "$STATE_FILE")"
  mode_has_vless && echo "Xray-core：v$("$XRAY" version 2>/dev/null | awk 'NR==1{print $2}')（${XRAY_VERSION_SOURCE}）"
  mode_has_hy2 && echo "sing-box：v$("$SING_BOX" version 2>/dev/null | awk '/sing-box version/{print $3;exit}')（${SING_BOX_VERSION_SOURCE}）"
  mode_has_vless && echo "VLESS + REALITY：TCP/$(jq -r '.listen_port' "$STATE_FILE")，Xray=$(systemctl is-active xray)"
  mode_has_hy2 && echo "Hysteria 2：UDP/$(jq -r '.listen_port' "$STATE_FILE")，sing-box=$(systemctl is-active sing-box)"
  echo "时区：Asia/Shanghai"
  systemctl is-active --quiet daily-reboot.timer 2>/dev/null && echo "每天北京时间 06:00 自动重启" || echo "自动重启：当前环境未启用"
  echo "以后重新显示日本直连节点：jp-show-nodes"
  echo "如需新建或管理中转线路：vps"
  echo "本次没有立即重启服务器，只重启了启用的代理服务。"
}

[[ "$EUID" -eq 0 ]] || fail "请使用 root 用户执行。"

case "$RUN_MODE" in
  --cleanup-temp)
    CURRENT_STEP="清理过期临时节点"; check_runtime_environment; acquire_manager_lock; cleanup_expired_temporary
    ;;
  --manage)
    CURRENT_STEP="检查日本运行环境"; log "$CURRENT_STEP"; check_runtime_environment
    acquire_manager_lock
    install_shortcuts
    management_menu
    ;;
  *)
    bootstrap
    ;;
esac

trap - EXIT
cleanup_temp
JP_RELAY_JPR3_MANAGER_EOF
chmod 700 /usr/local/sbin/jp-relay-manager
if [[ "${VVV_REFRESH_MANAGER_ONLY:-0}" == 1 ]]; then
  exit 0
fi
/usr/local/sbin/jp-relay-manager

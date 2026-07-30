#!/usr/bin/env bash
# 构建编号：040203（日本主机，多 VPS 兼容修复 + Hysteria 2 限速 50 Mbps）
# 构建版本：213222；基于 040203，新增 HTTP/HTTPS/SOCKS5 上游中转与 Loon 优先输出。
# 可作为文件执行，也可整段粘贴到 SSH 终端。
# 首次运行只询问协议模式和统一端口；选择后全自动安装。
umask 077

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
HY2_LIMIT_MBPS=50

DEFAULT_SNI="www.softbank.jp"
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
  [[ "${ID:-}" == "debian" ]] || fail "日本脚本仅支持 Debian 12/13。当前系统：${PRETTY_NAME:-未知}"
  case "${VERSION_ID:-}" in
    12|13) ;;
    *) fail "日本脚本仅支持 Debian 12/13。当前版本：${VERSION_ID:-未知}" ;;
  esac
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
  retry 5 10 apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 -o DPkg::Lock::Timeout=120 -o Acquire::PDiffs=false update
  dpkg --configure -a >/dev/null 2>&1 || true
  retry 3 10 apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 -o DPkg::Lock::Timeout=120 install -y --no-install-recommends \
    ca-certificates curl unzip tar gzip openssl jq python3 iproute2 procps \
    tzdata kmod util-linux qrencode
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
  local choice input
  echo
  echo "请选择要安装的代理协议："
  echo
  echo "1. 同时安装双协议（TCP/443 + UDP/443）【默认】"
  echo "2. 只安装 VLESS + XTLS Vision + REALITY（TCP/443）"
  echo "3. 只安装 Hysteria 2（QUIC/UDP/443）"
  echo "0. 退出"
  echo
  while true; do
    read -r -p "请输入编号 [默认 1]：" choice
    [[ -n "$choice" ]] || choice="1"
    case "$choice" in
      1) INSTALL_MODE="dual"; break ;;
      2) INSTALL_MODE="vless"; break ;;
      3) INSTALL_MODE="hy2"; break ;;
      0) INSTALL_CANCELLED=1; return 0 ;;
      *) echo "请输入 0、1、2 或 3。" ;;
    esac
  done

  while true; do
    read -r -p "请输入代理监听端口 [默认 443]：" input
    input="${input//[[:space:]]/}"
    [[ -n "$input" ]] || input="443"
    if valid_port "$input"; then
      INSTALL_PORT="$((10#$input))"
      break
    fi
    echo "端口必须是 1–65535 之间的数字。"
  done
  echo "已选择模式：$INSTALL_MODE"
  echo "统一监听端口：TCP/UDP ${INSTALL_PORT}（仅启用所选协议）"
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
    echo "检测到本脚本状态，复用已保存的协议、端口和全部密钥。"
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
    local key_output v_private v_public short_id uuid
    uuid="$(new_uuid)"
    key_output="$("$XRAY" x25519)"
    parse_x25519_keys "$key_output"
    v_private="$GENERATED_PRIVATE_KEY"
    v_public="$GENERATED_PUBLIC_KEY"
    short_id="$(openssl rand -hex 8)"
    vless_json="$(jq -n \
      --arg private "$v_private" --arg public "$v_public" --arg sid "$short_id" \
      --arg uuid "$uuid" \
      '{reality:{private_key:$private,public_key:$public,short_id:$sid},direct_user:{uuid:$uuid,email:"jp-direct@relay.local"}}')"
  fi

  if mode_has_hy2 "$mode"; then
    local server_name cert key meta password obfs
    server_name="jp-hy2.jp-relay.local"
    cert="${TLS_DIR}/japan-hy2.crt"
    key="${TLS_DIR}/japan-hy2.key"
    generate_certificate "$server_name" "$cert" "$key"
    chown root:sing-box "$cert" "$key"
    meta="$(certificate_metadata_json "$cert")"
    password="$(random_secret)"
    obfs="$(random_secret)"
    hy2_json="$(jq -n \
      --arg server_name "$server_name" --arg cert "$cert" --arg key "$key" \
      --arg password "$password" --arg obfs "$obfs" \
      --arg fp "$(jq -r '.fingerprint' <<< "$meta")" \
      --arg pinhex "$(jq -r '.pin_hex' <<< "$meta")" \
      --arg pinb64 "$(jq -r '.public_key_sha256' <<< "$meta")" \
      '{server_name:$server_name,certificate_path:$cert,key_path:$key,certificate_fingerprint:$fp,certificate_pin_hex:$pinhex,certificate_public_key_sha256:$pinb64,obfs_password:$obfs,direct_user:{name:"jp-direct-hy2",password:$password}}')"
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
    '{
      schema:3,role:"japan-hub",protocol_mode:$mode,public_ip:$ip,listen_port:$port,
      sni:$sni,direct_base_name:$direct_base,xray_version:$xray_version,
      sing_box_version:$sing_version,vless:$vless,hy2:$hy2,relays:[],upstream_relays:[],
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
for r in relays:
    rv=r.get("vless")
    if rv:
        clients.append({"id":rv["client_uuid"],"level":0,"email":rv["client_email"],"flow":"xtls-rprx-vision"})
for r in upstreams:
    clients.append({"id":r["client_uuid"],"level":0,"email":r["client_email"],"flow":"xtls-rprx-vision"})
inbounds=[{
 "tag":"in-vless-reality","listen":"0.0.0.0","port":port,"protocol":"vless",
 "settings":{"clients":clients,"decryption":"none"},
 "streamSettings":{"method":"raw","security":"reality","realitySettings":{
   "show":False,"target":f"{sni}:443","xver":0,"serverNames":[sni],
   "privateKey":v["reality"]["private_key"],"shortIds":[v["reality"]["short_id"]]}},
 "sniffing":{"enabled":True,"destOverride":["http","tls","quic"],"routeOnly":True}
}]
outbounds=[{"tag":"direct","protocol":"freedom","settings":{"domainStrategy":"UseIPv4"}}]
test_rules=[]
route_rules=[]
udp_block_rules=[]
for r in relays:
    rv=r.get("vless")
    if not rv: continue
    inbounds.append({
      "tag":rv["test_inbound_tag"],"listen":"127.0.0.1","port":int(rv["test_socks_port"]),
      "protocol":"socks","settings":{"udp":False},
      "sniffing":{"enabled":True,"destOverride":["http","tls"],"routeOnly":True}
    })
    outbounds.append({
      "tag":rv["outbound_tag"],"protocol":"vless",
      "settings":{"address":r["remote_ip"],"port":int(r["remote_port"]),"id":rv["outbound_uuid"],"encryption":"none","flow":"xtls-rprx-vision"},
      "streamSettings":{"method":"raw","security":"reality","realitySettings":{
        "serverName":sni,"fingerprint":"chrome","password":rv["remote_reality"]["public_key"],
        "shortId":rv["remote_reality"]["short_id"],"spiderX":""}}
    })
    test_rules.append({"type":"field","inboundTag":[rv["test_inbound_tag"]],"outboundTag":rv["outbound_tag"],"ruleTag":f"test-{r['id']}"})
    route_rules.append({"type":"field","user":[rv["client_email"]],"outboundTag":rv["outbound_tag"],"ruleTag":f"route-{r['id']}"})
for r in upstreams:
    inbounds.append({
      "tag":r["test_inbound_tag"],"listen":"127.0.0.1","port":int(r["test_socks_port"]),
      "protocol":"socks","settings":{"udp":False},
      "sniffing":{"enabled":True,"destOverride":["http","tls"],"routeOnly":True}
    })
    outbounds.append({
      "tag":r["outbound_tag"],"protocol":r["proxy_protocol"],
      "settings":{"address":r["host"],"port":int(r["port"]),"user":r["username"],"pass":r["password"]}
    })
    test_rules.append({"type":"field","inboundTag":[r["test_inbound_tag"]],"outboundTag":r["outbound_tag"],"ruleTag":f"test-{r['id']}"})
    udp_block_rules.append({"type":"field","user":[r["client_email"]],"network":"udp","outboundTag":"blocked","ruleTag":f"block-udp-{r['id']}"})
    route_rules.append({"type":"field","user":[r["client_email"]],"outboundTag":r["outbound_tag"],"ruleTag":f"route-{r['id']}"})
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
limit_mbps=int(sys.argv[3])
if state["protocol_mode"] not in ("dual","hy2"):
    Path(sys.argv[2]).write_text("{}\n",encoding="utf-8")
    raise SystemExit
h=state["hy2"]; port=int(state["listen_port"])
users=[{"name":h["direct_user"]["name"],"password":h["direct_user"]["password"]}]
for r in state["relays"]:
    rh=r.get("hy2")
    if rh:
        users.append({"name":rh["client_user"],"password":rh["client_password"]})
inbounds=[{
 "type":"hysteria2","tag":"hy2-in","listen":"0.0.0.0","listen_port":port,
 "up_mbps":limit_mbps,"down_mbps":limit_mbps,"users":users,
 "obfs":{"type":"salamander","password":h["obfs_password"]},
 "tls":{"enabled":True,"server_name":h["server_name"],"alpn":["h3"],"min_version":"1.3",
        "certificate_path":h["certificate_path"],"key_path":h["key_path"]}
}]
outbounds=[{"type":"direct","tag":"direct"}]
rules=[{"ip_is_private":True,"action":"reject","method":"drop"}]
for r in state["relays"]:
    rh=r.get("hy2")
    if not rh: continue
    inbounds.append({
      "type":"mixed","tag":rh["test_inbound_tag"],"listen":"127.0.0.1",
      "listen_port":int(rh["test_socks_port"])
    })
    outbounds.append({
      "type":"hysteria2","tag":rh["outbound_tag"],"server":r["remote_ip"],
      "server_port":int(r["remote_port"]),"up_mbps":limit_mbps,"down_mbps":limit_mbps,
      "password":rh["outbound_password"],
      "obfs":{"type":"salamander","password":rh["outbound_obfs_password"]},
      "tls":{"enabled":True,"server_name":rh["outbound_server_name"],"insecure":True,"alpn":["h3"],
             "min_version":"1.3","certificate_public_key_sha256":[rh["remote_certificate_public_key_sha256"]]}
    })
    rules.append({"inbound":[rh["test_inbound_tag"]],"action":"route","outbound":rh["outbound_tag"]})
    rules.append({"auth_user":[rh["client_user"]],"action":"route","outbound":rh["outbound_tag"]})
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
  local port
  port="$(jq -r '.listen_port' "$STATE_FILE")"
  systemctl is-active --quiet xray || return 1
  ss -H -lntp "sport = :${port}" 2>/dev/null | grep -qi xray || return 1
  while IFS= read -r port; do
    [[ -z "$port" ]] && continue
    ss -H -lntp "sport = :${port}" 2>/dev/null | grep -qi xray || return 1
  done < <(jq -r '.relays[]?.vless.test_socks_port // empty' "$STATE_FILE")
  while IFS= read -r port; do
    [[ -z "$port" ]] && continue
    ss -H -lntp "sport = :${port}" 2>/dev/null | grep -qi xray || return 1
  done < <(jq -r '.upstream_relays[]?.test_socks_port // empty' "$STATE_FILE")
  return 0
}

verify_sing_runtime() {
  mode_has_hy2 || return 0
  local port
  port="$(jq -r '.listen_port' "$STATE_FILE")"
  systemctl is-active --quiet sing-box || return 1
  ss -H -lnup "sport = :${port}" 2>/dev/null | grep -qi sing-box || return 1
  while IFS= read -r port; do
    [[ -z "$port" ]] && continue
    ss -H -lntp "sport = :${port}" 2>/dev/null | grep -qi sing-box || return 1
  done < <(jq -r '.relays[]?.hy2.test_socks_port // empty' "$STATE_FILE")
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

apply_candidate_with_rollback() {
  local candidate_state="$1" delete_dir="${2:-}"
  local old_state old_xray old_sing candidate_xray candidate_sing
  local had_xray=0 had_sing=0 ok=1
  old_state="$(mktemp --suffix=.json /tmp/jp-old-state.XXXXXX)"
  old_xray="$(mktemp --suffix=.json /tmp/jp-old-xray.XXXXXX)"
  old_sing="$(mktemp --suffix=.json /tmp/jp-old-sing.XXXXXX)"
  candidate_xray="$(mktemp --suffix=.json /tmp/jp-new-xray.XXXXXX)"
  candidate_sing="$(mktemp --suffix=.json /tmp/jp-new-sing.XXXXXX)"
  TMP_FILES+=("$old_state" "$old_xray" "$old_sing" "$candidate_xray" "$candidate_sing")
  cp -a "$STATE_FILE" "$old_state"
  [[ ! -f "$XRAY_CFG" ]] || { cp -a "$XRAY_CFG" "$old_xray"; had_xray=1; }
  [[ ! -f "$SING_CFG" ]] || { cp -a "$SING_CFG" "$old_sing"; had_sing=1; }

  if mode_has_vless "$(jq -r '.protocol_mode' "$candidate_state")"; then
    build_xray_config "$candidate_state" "$candidate_xray"
    "$XRAY" run -test -format=json -config "$candidate_xray"
  fi
  if mode_has_hy2 "$(jq -r '.protocol_mode' "$candidate_state")"; then
    build_sing_config "$candidate_state" "$candidate_sing"
    "$SING_BOX" check -c "$candidate_sing"
  fi

  install -o root -g root -m 600 "$candidate_state" "$STATE_FILE"
  if mode_has_vless; then
    install -o root -g xray -m 640 "$candidate_xray" "$XRAY_CFG"
    systemctl restart xray >/dev/null 2>&1 || ok=0
  fi
  if mode_has_hy2; then
    install -o root -g sing-box -m 640 "$candidate_sing" "$SING_CFG"
    systemctl restart sing-box >/dev/null 2>&1 || ok=0
  fi
  sleep 2
  verify_xray_runtime || ok=0
  verify_sing_runtime || ok=0
  if (( ok == 1 )); then
    [[ -z "$delete_dir" ]] || rm -rf -- "$delete_dir"
    return 0
  fi

  echo "新配置启动失败，正在恢复旧状态和旧配置。" >&2
  install -o root -g root -m 600 "$old_state" "$STATE_FILE"
  if mode_has_vless; then
    if (( had_xray )); then install -o root -g xray -m 640 "$old_xray" "$XRAY_CFG"; fi
    systemctl restart xray >/dev/null 2>&1 || true
  fi
  if mode_has_hy2; then
    if (( had_sing )); then install -o root -g sing-box -m 640 "$old_sing" "$SING_CFG"; fi
    systemctl restart sing-box >/dev/null 2>&1 || true
  fi
  sleep 2
  journalctl -u xray --no-pager -n 50 2>/dev/null || true
  journalctl -u sing-box --no-pager -n 50 2>/dev/null || true
  fail "新配置未生效，已恢复旧配置。"
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
    base=relay["name"]
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
    params=[("obfs","salamander"),("obfs-password",h["obfs_password"]),("sni",h["server_name"]),("insecure","1"),("pinSHA256",h["certificate_pin_hex"])]
    uri=f"hysteria2://{quote(password,safe='')}@{ip}:{port}/?{urlencode(params)}#{quote(name,safe='')}"
    loon=f"{loon_name(name)} = Hysteria2,{ip},{port},{loon_q(password)},skip-cert-verify=true,sni={h['server_name']},udp=true,fast-open=true,salamander-password={loon_q(h['obfs_password'])}"
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
qr_index="\n".join(f"{name}\t{uri}" for name,uri in share_links)
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
    lines += ["","【Loon / Shadowrocket】","Loon 原生配置：",loon_text,"","扫码链接："]
    for name,uri in share_links: lines += [f"[{name}]",uri]
if clash_entries: lines += ["","【Clash Verge Rev / Mihomo】",clash_text]
summary="\n".join(lines).rstrip()+"\n"

(out/"客户端节点.txt").write_text(summary,encoding="utf-8")
(out/"Quantumult-X.conf").write_text((qx_text+"\n") if qx_text else "",encoding="utf-8")
(out/"Loon.conf").write_text((loon_text+"\n") if loon_text else "",encoding="utf-8")
(out/"Loon-Shadowrocket.txt").write_text((share_text+"\n") if share_text else "",encoding="utf-8")
(out/"Loon-Shadowrocket-二维码索引.tsv").write_text((qr_index+"\n") if qr_index else "",encoding="utf-8")
# 同时保留旧文件名，便于已有运维习惯和第三方工具读取。
(out/"Shadowrocket.txt").write_text((share_text+"\n") if share_text else "",encoding="utf-8")
(out/"Shadowrocket-二维码索引.tsv").write_text((qr_index+"\n") if qr_index else "",encoding="utf-8")
(out/"Clash-Verge-Rev.yaml").write_text(clash_text,encoding="utf-8")
print(summary,end="")
PY_CLIENTS
  chmod 700 "$out_dir"
  chmod 600 "$out_dir"/*
}

show_loon_shadowrocket_qr() {
  local index_file="$1" name uri
  [[ -s "$index_file" ]] || return 0
  echo
  echo "================ Loon / Shadowrocket 二维码 ================"
  while IFS=$'\t' read -r name uri; do
    [[ -n "$uri" ]] || continue
    echo
    echo "【${name}】"
    echo "$uri"
    echo
    qrencode -t ANSIUTF8 -m 1 "$uri"
  done < "$index_file"
  echo "============================================================="
}

generate_direct_client_files() {
  local dir="/root/日本VPS-直连客户端配置"
  generate_client_files "$STATE_FILE" "" "$dir" direct
  cp -f "$dir/客户端节点.txt" /root/日本VPS-客户端节点.txt
  chmod 600 /root/日本VPS-客户端节点.txt
  show_loon_shadowrocket_qr "$dir/Loon-Shadowrocket-二维码索引.tsv"
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

prepare_add_or_overwrite() {
  local remote_ip="$1" remote_port="$2" node_name="$3"
  valid_ipv4 "$remote_ip" || fail "落地 IP 无效。"
  valid_port "$remote_port" || fail "落地端口无效。"
  [[ -n "$node_name" && "$node_name" != *$'\n'* && "$node_name" != *$'\r'* ]] || fail "线路名称无效。"

  local count old relay_id now candidate test_vless test_hy2 remote_hy2
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
      local client_uuid outbound_uuid key_output private_key public_key short_id
      test_vless="$(allocate_test_port vless)"
      client_uuid="$(new_uuid)"
      outbound_uuid="$(new_uuid)"
      key_output="$("$XRAY" x25519)"
      parse_x25519_keys "$key_output"
      private_key="$GENERATED_PRIVATE_KEY"; public_key="$GENERATED_PUBLIC_KEY"; short_id="$(openssl rand -hex 8)"
      vless_json="$(jq -n \
        --arg client_uuid "$client_uuid" --arg email "${relay_id}@relay.local" \
        --arg outbound_uuid "$outbound_uuid" --arg private "$private_key" --arg public "$public_key" --arg sid "$short_id" \
        --arg outtag "vless-out-${relay_id}" --arg testtag "vless-test-${relay_id}" --argjson testport "$test_vless" \
        '{client_uuid:$client_uuid,client_email:$email,outbound_uuid:$outbound_uuid,remote_reality:{private_key:$private,public_key:$public,short_id:$sid},outbound_tag:$outtag,test_inbound_tag:$testtag,test_socks_port:$testport}')"
    fi
    if mode_has_hy2; then
      local material client_password outbound_password outbound_obfs
      test_hy2="$(allocate_test_port hy2)"
      material="$(mktemp --suffix=.json /tmp/jp-hy2-material.XXXXXX)"
      TMP_FILES+=("$material")
      create_remote_hy2_material "$relay_id" "$material"
      client_password="$(random_secret)"
      outbound_password="$(random_secret)"
      outbound_obfs="$(random_secret)"
      hy2_json="$(jq -n \
        --arg client_user "${relay_id}-hy2" --arg client_password "$client_password" \
        --arg outbound_password "$outbound_password" --arg outbound_obfs "$outbound_obfs" \
        --arg outtag "hy2-out-${relay_id}" --arg testtag "hy2-test-${relay_id}" --argjson testport "$test_hy2" \
        --arg server_name "$(jq -r '.server_name' "$material")" \
        --arg cert_pem "$(jq -r '.certificate_pem' "$material")" \
        --arg key_pem "$(jq -r '.key_pem' "$material")" \
        --arg fp "$(jq -r '.fingerprint' "$material")" \
        --arg pinhex "$(jq -r '.pin_hex' "$material")" \
        --arg pinb64 "$(jq -r '.public_key_sha256' "$material")" \
        '{client_user:$client_user,client_password:$client_password,outbound_password:$outbound_password,outbound_obfs_password:$outbound_obfs,outbound_tag:$outtag,test_inbound_tag:$testtag,test_socks_port:$testport,outbound_server_name:$server_name,remote_certificate_pem:$cert_pem,remote_key_pem:$key_pem,remote_certificate_fingerprint:$fp,remote_certificate_pin_hex:$pinhex,remote_certificate_public_key_sha256:$pinb64}')"
    fi
    jq \
      --arg id "$relay_id" --arg name "$node_name" --arg ip "$remote_ip" --argjson port "$remote_port" \
      --argjson vless "$vless_json" --argjson hy2 "$hy2_json" --arg now "$now" \
      '.relays += [{id:$id,name:$name,remote_ip:$ip,remote_port:$port,vless:$vless,hy2:$hy2,created_at:$now,updated_at:$now}] | .updated_at=$now' \
      "$STATE_FILE" > "$candidate"
  fi

  local staging package_dir key
  staging="$(mktemp -d "${PACKAGE_ROOT}/.${relay_id}.staging.XXXXXX")"
  TMP_FILES+=("$staging")
  generate_client_files "$candidate" "$relay_id" "$staging" relay >/dev/null
  key="$(make_pairing_key "$candidate" "$relay_id")"
  printf '%s\n' "$key" > "$staging/落地VPS对接密钥.txt"
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
  echo "本次只重启了启用的代理服务，没有立即重启服务器。"
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
    test_port="$(allocate_test_port upstream)"
    client_uuid="$(new_uuid)"
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

make_pairing_key() {
  local state_path="$1" relay_id="$2"
  python3 - "$state_path" "$relay_id" <<'PY_JPR3'
import base64,hashlib,json,sys
from datetime import datetime,timezone
from pathlib import Path
s=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
r=next(x for x in s["relays"] if x["id"]==sys.argv[2])
payload={
 "schema":3,"type":"jp-relay-landing","protocol_mode":s["protocol_mode"],
 "relay_id":r["id"],"node_name":r["name"],
 "japan_public_ip":s["public_ip"],"japan_port":int(s["listen_port"]),
 "remote_public_ip":r["remote_ip"],"remote_public_port":int(r["remote_port"]),
 "sni":s["sni"],"xray_version":s["xray_version"],"sing_box_version":s["sing_box_version"],
 "vless":None,"hy2":None,"issued_at":datetime.now(timezone.utc).isoformat()
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
enc=base64.urlsafe_b64encode(raw).decode().rstrip("=")
chk=hashlib.sha256(raw).hexdigest()[:20]
print(f"JPR3.{enc}.{chk}")
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
  show_loon_shadowrocket_qr "$dir/Loon-Shadowrocket-二维码索引.tsv"
  echo "配置目录：$dir"
}

show_upstream_client_config() {
  local upstream_id="$1" dir="${PACKAGE_ROOT}/${upstream_id}"
  generate_client_files "$STATE_FILE" "$upstream_id" "$dir" upstream >/dev/null
  echo
  echo "==================== 客户端配置 ===================="
  cat "$dir/客户端节点.txt"
  echo "===================================================="
  show_loon_shadowrocket_qr "$dir/Loon-Shadowrocket-二维码索引.tsv"
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

perform_delete() {
  local relay_id="$1" name confirm candidate package_dir
  name="$(jq -r --arg id "$relay_id" '.relays[]|select(.id==$id)|.name' "$STATE_FILE")"
  read -r -p "确认删除“${name}”？输入 Y 确认：" confirm
  case "$confirm" in [Yy]) ;; *) echo "已取消删除。"; return 0;; esac
  candidate="$(mktemp --suffix=.json /tmp/jp-delete.XXXXXX)"
  TMP_FILES+=("$candidate")
  jq --arg id "$relay_id" --arg now "$(date --iso-8601=seconds)" \
    '.relays |= map(select(.id!=$id)) | .updated_at=$now' "$STATE_FILE" > "$candidate"
  package_dir="${PACKAGE_ROOT}/${relay_id}"
  apply_candidate_with_rollback "$candidate" "$package_dir"
  echo "线路“${name}”已删除。"
}

perform_delete_upstream() {
  local upstream_id="$1" name confirm candidate package_dir
  name="$(jq -r --arg id "$upstream_id" '.upstream_relays[]|select(.id==$id)|.name' "$STATE_FILE")"
  read -r -p "确认删除“${name}”？输入 Y 确认：" confirm
  case "$confirm" in [Yy]) ;; *) echo "已取消删除。"; return 0;; esac
  candidate="$(mktemp --suffix=.json /tmp/jp-upstream-delete.XXXXXX)"
  TMP_FILES+=("$candidate")
  jq --arg id "$upstream_id" --arg now "$(date --iso-8601=seconds)" \
    '.upstream_relays |= map(select(.id!=$id)) | .updated_at=$now' "$STATE_FILE" > "$candidate"
  package_dir="${PACKAGE_ROOT}/${upstream_id}"
  apply_candidate_with_rollback "$candidate" "$package_dir"
  echo "线路“${name}”已删除。"
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

  default_port="443"
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
  local enabled total new_vps_index new_upstream_index local_index selection entry_type entry_id
  local -a entries
  enabled="$(jq -r '.relay_manager_enabled' "$STATE_FILE")"
  if [[ "$enabled" != "true" ]]; then
    show_not_enabled_menu
  fi
  while true; do
    mapfile -t entries < <(jq -r '
      (.relays[]? | ["vps",.id,.name] | @tsv),
      (.upstream_relays[]? | ["upstream",.id,.name] | @tsv)
    ' "$STATE_FILE")
    total="${#entries[@]}"
    new_vps_index=$((total + 1))
    new_upstream_index=$((total + 2))
    local_index=$((total + 3))
    log "中转线路管理"
    local i
    for ((i=0; i<total; i++)); do
      IFS=$'\t' read -r entry_type entry_id entry_name <<< "${entries[$i]}"
      echo "$((i+1)). ${entry_name}"
    done
    echo "${new_vps_index}. 新建中转线路"
    echo "${new_upstream_index}. 新建 HTTP/HTTPS/SOCKS5 中转线路"
    echo "${local_index}. 查看本机客户端配置"
    echo "0. 退出"
    read -r -p "请输入编号：" selection
    [[ "$selection" =~ ^[0-9]+$ ]] || { echo "请输入有效数字。"; continue; }
    selection="$((10#$selection))"
    (( selection == 0 )) && return
    if (( selection == new_vps_index )); then prompt_new_relay; continue; fi
    if (( selection == new_upstream_index )); then prompt_new_upstream_relay; continue; fi
    if (( selection == local_index )); then show_local_client_config; pause_return; continue; fi
    (( selection >= 1 && selection <= total )) || { echo "编号超出范围。"; continue; }
    IFS=$'\t' read -r entry_type entry_id entry_name <<< "${entries[$((selection-1))]}"
    if [[ "$entry_type" == "vps" ]]; then relay_submenu "$entry_id"; else upstream_submenu "$entry_id"; fi
  done
}

install_shortcuts() {
  mkdir -p /usr/local/sbin
  cat > /usr/local/sbin/vps <<'EOF_VPS_CMD'
#!/usr/bin/env bash
# JP_RELAY_JPR3_MANAGER
/usr/local/sbin/jp-relay-manager --manage
EOF_VPS_CMD
  chmod 700 /usr/local/sbin/vps
  cat > /usr/local/sbin/jp-show-nodes <<'EOF_SHOW'
#!/usr/bin/env bash
cat /root/日本VPS-客户端节点.txt
if [[ -s /root/日本VPS-直连客户端配置/Loon-Shadowrocket-二维码索引.tsv ]]; then
  while IFS=$'\t' read -r name uri; do
    [[ -n "$uri" ]] || continue
    echo
    echo "【${name}】"
    echo "$uri"
    echo
    qrencode -t ANSIUTF8 -m 1 "$uri"
  done < /root/日本VPS-直连客户端配置/Loon-Shadowrocket-二维码索引.tsv
fi
EOF_SHOW
  chmod 700 /usr/local/sbin/jp-show-nodes
}

check_runtime_environment() {
  [[ -f "$STATE_FILE" ]] || fail "尚未完成日本 VPS 初始化。"
  jq -e '.schema==3 and .role=="japan-hub" and (.relays|type=="array") and ((.upstream_relays // [])|type=="array")' "$STATE_FILE" >/dev/null || fail "JPR3 状态文件损坏。"
  command -v qrencode >/dev/null || fail "缺少 qrencode。"
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
    echo "检测到现有 JPR3 状态：模式=${INSTALL_MODE}，端口=${INSTALL_PORT}。"
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
  CURRENT_STEP="生成日本直连节点和二维码"; log "$CURRENT_STEP"; generate_direct_client_files

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
  echo "以后重新显示日本直连节点与二维码：jp-show-nodes"
  echo "如需新建或管理中转线路：vps"
  echo "本次没有立即重启服务器，只重启了启用的代理服务。"
}

[[ "$EUID" -eq 0 ]] || fail "请使用 root 用户执行。"

case "$RUN_MODE" in
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
/usr/local/sbin/jp-relay-manager

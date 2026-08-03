#!/bin/sh
# 构建编号：040203（落地端，多 VPS 兼容修复 + Hysteria 2 限速 50 Mbps）
# 构建版本：213222；基于 040203，新增 HTTP/HTTPS/SOCKS5 上游中转与 Loon 优先输出。
# 可作为文件执行，也可整段粘贴到 SSH 终端。
# 只需把日本 VPS 输出的完整 JPR3 对接密钥粘贴到下方。
# 运行后不会再出现安装选项，将自动安装 JPR3 指定的单协议或双协议。
umask 077

# ============================================================
PAIRING_KEY="${VVV_PAIRING_KEY:-请粘贴以JPR3.开头的完整对接密钥}"
# ============================================================

export PAIRING_KEY
LANDING_CORE="/tmp/jp-relay-landing-jpr3.$$.sh"
cat > "$LANDING_CORE" <<'JP_RELAY_JPR3_LANDING_CORE_EOF'
#!/bin/sh
set -eu
umask 077

PAIRING_KEY="${PAIRING_KEY:-}"
COMBINED_INSTALL="${VVV_COMBINED_INSTALL:-0}"
CURRENT_STEP="启动"
TMP_DIR=""
TMP_CFG=""
TEST_LOG=""
TEST_PID=""
IS_CONTAINER=0
BBR_STATUS="未检查"
BBR_QDISC="未知"
GOMEMLIMIT_VALUE="256MiB"
PAIR_JSON=""
VLESS_TEST_PORT=""
HY2_TEST_PORT=""

XRAY="/usr/local/bin/xray"
XRAY_CFG="/etc/vvv-landing/xray/config.json"
XRAY_FALLBACK_VERSION="26.3.27"
XRAY_VERSION="$XRAY_FALLBACK_VERSION"
XRAY_VERSION_SOURCE="备用稳定版"
SING_BOX="/usr/local/bin/sing-box"
SING_CFG="/etc/vvv-landing/sing-box/config.json"
SING_BOX_FALLBACK_VERSION="1.13.12"
SING_BOX_VERSION="$SING_BOX_FALLBACK_VERSION"
SING_BOX_VERSION_SOURCE="备用稳定版"
# Hysteria 2 每条连接及中转链路的上下行硬上限（Mbps）
HY2_LIMIT_MBPS=50
STATE_DIR="/etc/jp-relay"
STATE_FILE="${STATE_DIR}/landing-state.json"
PAIR_FILE="${STATE_DIR}/pairing-key.txt"
TLS_DIR="/etc/vvv-landing/sing-box/tls"
UPGRADE_MARKER="/var/lib/jp-relay/landing-system-upgrade.done"
CLIENT_DIR="/root/中转客户端配置"
CLIENT_NODES_FILE="/root/中转客户端节点.txt"

log() {
  printf '\n\033[1;36m========== %s ==========\033[0m\n' "$1"
}

fail() {
  echo "错误：$*" >&2
  exit 1
}

cleanup() {
  [ -n "${TEST_PID:-}" ] && kill "$TEST_PID" >/dev/null 2>&1 || true
  [ -n "${TEST_PID:-}" ] && wait "$TEST_PID" >/dev/null 2>&1 || true
  [ -n "${TMP_CFG:-}" ] && rm -f "$TMP_CFG" || true
  [ -n "${TEST_LOG:-}" ] && rm -f "$TEST_LOG" || true
  [ -n "${TMP_DIR:-}" ] && rm -rf "$TMP_DIR" || true
}

on_exit() {
  rc=$?
  cleanup
  if [ "$rc" -ne 0 ]; then
    echo
    echo "[失败] 步骤：${CURRENT_STEP}"
    echo "[失败] 退出码：${rc}"
    echo "脚本不会主动关闭当前 SSH，也不会立即重启整台服务器。"
    echo "请把失败内容连同前面 40 行一起发来排查。"
  fi
}
trap on_exit EXIT

retry() {
  attempts="$1"; delay="$2"; shift 2; n=1
  until "$@"; do
    [ "$n" -lt "$attempts" ] || return 1
    echo "命令执行失败，${delay} 秒后重试（${n}/${attempts}）……"
    sleep "$delay"
    n=$((n + 1))
  done
}

is_numeric() {
  case "${1:-}" in ''|*[!0-9]*) return 1 ;; *) return 0 ;; esac
}

valid_ipv4() {
  ip="$1"
  old_ifs="$IFS"; IFS=.; set -- $ip; IFS="$old_ifs"
  [ "$#" -eq 4 ] || return 1
  for octet in "$@"; do
    is_numeric "$octet" || return 1
    [ "$octet" -ge 0 ] && [ "$octet" -le 255 ] || return 1
  done
  [ "$ip" != "0.0.0.0" ]
}

valid_port() {
  is_numeric "${1:-}" && [ "$1" -ge 1 ] && [ "$1" -le 65535 ]
}

mode_has_vless() {
  [ "$PROTOCOL_MODE" = "dual" ] || [ "$PROTOCOL_MODE" = "vless" ]
}

mode_has_hy2() {
  [ "$PROTOCOL_MODE" = "dual" ] || [ "$PROTOCOL_MODE" = "hy2" ]
}

normalize_pairing_key() {
  PAIRING_KEY="$(printf '%s' "$PAIRING_KEY" | tr -d ' \t\r\n')"
  [ -n "$PAIRING_KEY" ] || fail "请先在脚本顶部粘贴完整 JPR3 对接密钥。"
}

base64url_decode() {
  value="$1"
  mod=$((${#value} % 4))
  case "$mod" in
    0) padded="$value" ;;
    2) padded="${value}==" ;;
    3) padded="${value}=" ;;
    *) return 1 ;;
  esac
  printf '%s' "$padded" | tr '_-' '/+' | base64 -d
}

detect_os() {
  [ -r /etc/os-release ] || fail "无法读取 /etc/os-release。"
  # shellcheck disable=SC1091
  . /etc/os-release
  [ "${ID:-}" = "debian" ] && [ "${VERSION_ID:-}" = "13" ] || fail "落地脚本仅支持 Debian 13。当前系统：${PRETTY_NAME:-未知}"
  command -v apt-get >/dev/null 2>&1 || fail "当前 Debian 13 找不到 apt-get。"
  command -v systemctl >/dev/null 2>&1 || fail "当前 Debian 13 找不到 systemd。"
  [ "$(cat /proc/1/comm 2>/dev/null | tr -d '[:space:]')" = "systemd" ] || fail "当前系统不是以 systemd 作为 PID 1。"

  if grep -qE 'lxcfs|/dev/\.incus|/dev/incus' /proc/mounts 2>/dev/null || \
     [ -e /.dockerenv ] || \
     grep -qiE 'docker|lxc|containerd|kubepods' /proc/1/cgroup 2>/dev/null; then
    IS_CONTAINER=1
  fi
  echo "系统：${PRETTY_NAME}"
  echo "架构：$(uname -m)"
  [ "$IS_CONTAINER" -eq 0 ] || echo "虚拟化环境：受限容器（内核参数由宿主机控制）"
}

upgrade_system_once() {
  mkdir -p "$(dirname "$UPGRADE_MARKER")"
  export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
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
    ca-certificates curl unzip tar gzip openssl jq iproute2 procps \
    tzdata kmod util-linux python3 || fail "落地端依赖安装失败。若提示锁被占用，已等待最多 10 秒，请稍后重新运行。"
  update-ca-certificates >/dev/null 2>&1 || true
  echo "Debian 13 核心组件保持 VPS 镜像原版本，仅安装代理所需依赖。"
}

parse_pairing_key() {
  old_ifs="$IFS"; IFS=.; set -- $PAIRING_KEY; IFS="$old_ifs"
  [ "$#" -eq 3 ] || fail "JPR3 对接密钥格式错误；密钥可能被终端单行输入上限截断。"
  [ "$1" = "JPR3" ] || fail "本脚本只接受以 JPR3. 开头的对接密钥。"
  encoded="$2"; expected_checksum="$3"
  PAIR_JSON="$(python3 - "$encoded" "$expected_checksum" <<'PY_JPR3_DECODE'
import base64
import hashlib
import json
import sys
import zlib

encoded, expected = sys.argv[1:]
try:
    transferred = base64.urlsafe_b64decode(encoded + '=' * ((4 - len(encoded) % 4) % 4))
except Exception as exc:
    raise SystemExit(f'Base64 解码失败：{exc}')
if len(transferred) > 65536:
    raise SystemExit('JPR3 传输数据异常过大。')
actual = hashlib.sha256(transferred).hexdigest()[:20]
if actual != expected:
    raise SystemExit('JPR3 校验失败，密钥可能复制不完整。')
if transferred.startswith(b'{'):
    raw = transferred
else:
    try:
        raw = zlib.decompress(transferred)
    except Exception as exc:
        raise SystemExit(f'JPR3 解压失败：{exc}')
if len(raw) > 131072:
    raise SystemExit('JPR3 解压后数据异常过大。')
try:
    obj = json.loads(raw.decode('utf-8'))
except Exception as exc:
    raise SystemExit(f'JPR3 JSON 无效：{exc}')
sys.stdout.write(json.dumps(obj, ensure_ascii=False, separators=(',', ':')))
PY_JPR3_DECODE
)" || fail "JPR3 解码或校验失败，密钥可能复制不完整。"

  printf '%s' "$PAIR_JSON" | jq -e '
    .schema==4 and .type=="jp-relay-landing" and
    (.protocol_mode=="dual" or .protocol_mode=="vless" or .protocol_mode=="hy2") and
    (.relay_id|type=="string" and length>0) and
    (.node_name|type=="string" and length>0) and
    (.japan_public_ip|type=="string" and length>0) and
    (.japan_port|type=="number") and
    (.remote_public_ip|type=="string" and length>0) and
    (.remote_public_port|type=="number") and
    (.sni|type=="string" and length>0) and
    (.xray_version|type=="string" and length>0) and
    (.sing_box_version|type=="string" and length>0) and
    (if (.protocol_mode=="dual" or .protocol_mode=="vless") then
       (.vless|type=="object") and
       (.vless.japan_client_uuid|type=="string" and length>0) and
       (.vless.japan_reality_public_key|type=="string" and length>0) and
       (.vless.japan_reality_short_id|type=="string" and length>0) and
       (.vless.remote_uuid|type=="string" and length>0) and
       (.vless.remote_reality_private_key|type=="string" and length>0) and
       (.vless.remote_reality_public_key|type=="string" and length>0) and
       (.vless.remote_reality_short_id|type=="string" and length>0)
     else .vless==null end) and
    (if (.protocol_mode=="dual" or .protocol_mode=="hy2") then
       (.hy2|type=="object") and
       (.hy2.japan_client_password|type=="string" and length>0) and
       (.hy2.japan_obfs_password|type=="string" and length>0) and
       (.hy2.japan_server_name|type=="string" and length>0) and
       (.hy2.japan_certificate_fingerprint|type=="string" and length>0) and
       (.hy2.japan_certificate_pin_hex|type=="string" and length>0) and
       (.hy2.japan_certificate_public_key_sha256|type=="string" and length>0) and
       (.hy2.remote_password|type=="string" and length>0) and
       (.hy2.remote_obfs_password|type=="string" and length>0) and
       (.hy2.remote_server_name|type=="string" and length>0) and
       (.hy2.remote_certificate_pem|type=="string" and length>0) and
       (.hy2.remote_key_pem|type=="string" and length>0) and
       (.hy2.remote_certificate_fingerprint|type=="string" and length>0) and
       (.hy2.remote_certificate_pin_hex|type=="string" and length>0) and
       (.hy2.remote_certificate_public_key_sha256|type=="string" and length>0)
     else .hy2==null end)
  ' >/dev/null || fail "JPR3 内容不完整或协议模式不匹配。"

  PROTOCOL_MODE="$(printf '%s' "$PAIR_JSON" | jq -er '.protocol_mode')"
  RELAY_ID="$(printf '%s' "$PAIR_JSON" | jq -er '.relay_id')"
  NODE_NAME="$(printf '%s' "$PAIR_JSON" | jq -er '.node_name')"
  JAPAN_PUBLIC_IP="$(printf '%s' "$PAIR_JSON" | jq -er '.japan_public_ip')"
  JAPAN_PORT="$(printf '%s' "$PAIR_JSON" | jq -er '.japan_port')"
  REMOTE_PUBLIC_IP="$(printf '%s' "$PAIR_JSON" | jq -er '.remote_public_ip')"
  REMOTE_PUBLIC_PORT="$(printf '%s' "$PAIR_JSON" | jq -er '.remote_public_port')"
  SNI="$(printf '%s' "$PAIR_JSON" | jq -er '.sni')"
  PAIR_XRAY_VERSION="$(printf '%s' "$PAIR_JSON" | jq -er '.xray_version')"
  PAIR_SING_BOX_VERSION="$(printf '%s' "$PAIR_JSON" | jq -er '.sing_box_version')"
  HY2_LIMIT_MBPS="$(printf '%s' "$PAIR_JSON" | jq -er '.hy2_limit_mbps // 50')"

  valid_ipv4 "$JAPAN_PUBLIC_IP" || fail "JPR3 中的日本公网 IPv4 无效。"
  valid_ipv4 "$REMOTE_PUBLIC_IP" || fail "JPR3 中的落地公网 IPv4 无效。"
  valid_port "$JAPAN_PORT" || fail "JPR3 中的日本端口无效。"
  valid_port "$REMOTE_PUBLIC_PORT" || fail "JPR3 中的落地端口无效。"
  is_numeric "$HY2_LIMIT_MBPS" && [ "$HY2_LIMIT_MBPS" -ge 30 ] && [ "$HY2_LIMIT_MBPS" -le 100 ] || fail "JPR3 中的 Hysteria 2 限速必须为 30-100 Mbps。"

  if mode_has_vless; then
    JAPAN_CLIENT_UUID="$(printf '%s' "$PAIR_JSON" | jq -er '.vless.japan_client_uuid')"
    JAPAN_REALITY_PUBLIC_KEY="$(printf '%s' "$PAIR_JSON" | jq -er '.vless.japan_reality_public_key')"
    JAPAN_REALITY_SHORT_ID="$(printf '%s' "$PAIR_JSON" | jq -er '.vless.japan_reality_short_id')"
    REMOTE_UUID="$(printf '%s' "$PAIR_JSON" | jq -er '.vless.remote_uuid')"
    REMOTE_REALITY_PRIVATE_KEY="$(printf '%s' "$PAIR_JSON" | jq -er '.vless.remote_reality_private_key')"
    REMOTE_REALITY_PUBLIC_KEY="$(printf '%s' "$PAIR_JSON" | jq -er '.vless.remote_reality_public_key')"
    REMOTE_REALITY_SHORT_ID="$(printf '%s' "$PAIR_JSON" | jq -er '.vless.remote_reality_short_id')"
  fi

  if mode_has_hy2; then
    JAPAN_HY2_PASSWORD="$(printf '%s' "$PAIR_JSON" | jq -er '.hy2.japan_client_password')"
    JAPAN_HY2_OBFS="$(printf '%s' "$PAIR_JSON" | jq -er '.hy2.japan_obfs_password')"
    JAPAN_HY2_SERVER_NAME="$(printf '%s' "$PAIR_JSON" | jq -er '.hy2.japan_server_name')"
    JAPAN_HY2_FINGERPRINT="$(printf '%s' "$PAIR_JSON" | jq -er '.hy2.japan_certificate_fingerprint')"
    JAPAN_HY2_PIN_HEX="$(printf '%s' "$PAIR_JSON" | jq -er '.hy2.japan_certificate_pin_hex')"
    JAPAN_HY2_PUBLIC_KEY_SHA256="$(printf '%s' "$PAIR_JSON" | jq -er '.hy2.japan_certificate_public_key_sha256')"
    REMOTE_HY2_PASSWORD="$(printf '%s' "$PAIR_JSON" | jq -er '.hy2.remote_password')"
    REMOTE_HY2_OBFS="$(printf '%s' "$PAIR_JSON" | jq -er '.hy2.remote_obfs_password')"
    REMOTE_HY2_SERVER_NAME="$(printf '%s' "$PAIR_JSON" | jq -er '.hy2.remote_server_name')"
    REMOTE_HY2_CERT_PEM="$(printf '%s' "$PAIR_JSON" | jq -er '.hy2.remote_certificate_pem')"
    REMOTE_HY2_KEY_PEM="$(printf '%s' "$PAIR_JSON" | jq -er '.hy2.remote_key_pem')"
    REMOTE_HY2_FINGERPRINT="$(printf '%s' "$PAIR_JSON" | jq -er '.hy2.remote_certificate_fingerprint')"
    REMOTE_HY2_PIN_HEX="$(printf '%s' "$PAIR_JSON" | jq -er '.hy2.remote_certificate_pin_hex')"
    REMOTE_HY2_PUBLIC_KEY_SHA256="$(printf '%s' "$PAIR_JSON" | jq -er '.hy2.remote_certificate_public_key_sha256')"
  fi

  echo "线路：${NODE_NAME}"
  echo "协议模式：${PROTOCOL_MODE}"
  echo "日本入口：${JAPAN_PUBLIC_IP}:${JAPAN_PORT}"
  echo "落地入口：${REMOTE_PUBLIC_IP}:${REMOTE_PUBLIC_PORT}"
}

memory_limit_kb() {
  value=""
  if [ -r /sys/fs/cgroup/memory.max ]; then
    value="$(cat /sys/fs/cgroup/memory.max 2>/dev/null || true)"
    if is_numeric "$value" && [ "$value" -gt 0 ]; then
      echo $((value / 1024)); return
    fi
  fi
  awk '/^MemTotal:/{print $2;exit}' /proc/meminfo
}

choose_memory_limit() {
  mem_kb="$(memory_limit_kb)"
  is_numeric "$mem_kb" || mem_kb=524288
  if [ "$mem_kb" -lt 180000 ]; then GOMEMLIMIT_VALUE="96MiB"
  elif [ "$mem_kb" -lt 320000 ]; then GOMEMLIMIT_VALUE="160MiB"
  elif [ "$mem_kb" -lt 600000 ]; then GOMEMLIMIT_VALUE="256MiB"
  else GOMEMLIMIT_VALUE="512MiB"
  fi
  echo "检测到的内存限制：$((mem_kb / 1024)) MiB"
  echo "代理核心 Go 内存软限制：${GOMEMLIMIT_VALUE}"
}

check_disk_space() {
  free_kb="$(df -Pk / | awk 'NR==2{print $4}')"
  is_numeric "$free_kb" || fail "无法读取根分区可用空间。"
  echo "根分区可用空间：$((free_kb / 1024)) MiB"
  [ "$free_kb" -ge 180000 ] || fail "根分区至少需要约 180 MiB 可用空间。"
}

configure_swap_if_suitable() {
  if [ "$IS_CONTAINER" -eq 1 ]; then
    echo "受限容器不创建 Swap。"
    return 0
  fi
  current_swap_kb="$(awk 'NR>1{s+=$3}END{print s+0}' /proc/swaps)"
  [ "$current_swap_kb" -lt 524288 ] || { echo "已有 Swap，保持不变。"; return 0; }
  free_kb="$(df -Pk / | awk 'NR==2{print $4}')"
  [ "$free_kb" -ge 900000 ] || { echo "磁盘空间较小，跳过 Swap。"; return 0; }

  root_fstype="$(findmnt -n -o FSTYPE / 2>/dev/null || true)"
  case "$root_fstype" in
    overlay|aufs|squashfs|tmpfs|ramfs|fuse.*)
      echo "根文件系统 ${root_fstype:-未知} 不适合由脚本创建 Swap，已跳过。"
      return 0
      ;;
  esac

  swap_mb=512
  [ "$free_kb" -lt 1800000 ] || swap_mb=1024
  swap_dir="/var/lib/jp-relay-swap"
  swap_path="${swap_dir}/swapfile"
  if ! install -d -m 700 "$swap_dir" 2>/dev/null; then
    echo "警告：无法创建独立 Swap 目录，已跳过，不影响代理安装。"
    return 0
  fi
  if awk 'NR>1{print $1}' /proc/swaps | grep -Fxq "$swap_path"; then
    echo "脚本专用 Swap 已启用，保持不变。"
    return 0
  fi
  if [ -e "$swap_path" ] && ! rm -f -- "$swap_path" 2>/dev/null; then
    echo "警告：脚本专用 Swap 文件不可修改，已跳过，不影响代理安装。"
    return 0
  fi

  echo "创建 ${swap_mb} MiB 独立 Swap：${swap_path}"
  created=0
  if command -v fallocate >/dev/null 2>&1 && fallocate -l "${swap_mb}M" "$swap_path" 2>/dev/null; then
    created=1
  elif dd if=/dev/zero of="$swap_path" bs=1M count="$swap_mb" status=none 2>/dev/null; then
    created=1
  fi
  if [ "$created" -ne 1 ]; then
    rm -f -- "$swap_path" 2>/dev/null || true
    echo "警告：当前文件系统无法创建 Swap，已跳过，不影响代理安装。"
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
  echo "Swap：已启用 ${swap_path}"
}

configure_network_tuning() {
  command -v modprobe >/dev/null 2>&1 && modprobe tcp_bbr >/dev/null 2>&1 || true
  command -v modprobe >/dev/null 2>&1 && modprobe sch_fq >/dev/null 2>&1 || true
  available="$(sysctl -n net.ipv4.tcp_available_congestion_control 2>/dev/null || true)"
  echo "内核可用拥塞控制：${available:-未知}"

  mkdir -p /etc/sysctl.d
  cat > /etc/sysctl.d/99-jp-relay-network.conf <<'EOF_SYSCTL'
net.ipv4.tcp_congestion_control = bbr
net.core.default_qdisc = fq
net.core.rmem_max = 33554432
net.core.wmem_max = 33554432
net.core.rmem_default = 1048576
net.core.wmem_default = 1048576
net.core.netdev_max_backlog = 16384
EOF_SYSCTL

  if printf '%s\n' "$available" | grep -qw bbr; then
    if sysctl -w net.ipv4.tcp_congestion_control=bbr >/dev/null 2>&1; then
      BBR_STATUS="$(sysctl -n net.ipv4.tcp_congestion_control 2>/dev/null || echo bbr)"
    else
      BBR_STATUS="宿主机禁止修改（已跳过）"
    fi
  else
    BBR_STATUS="当前内核未提供"
  fi
  sysctl -w net.core.default_qdisc=fq >/dev/null 2>&1 || true
  sysctl -w net.core.rmem_max=33554432 >/dev/null 2>&1 || true
  sysctl -w net.core.wmem_max=33554432 >/dev/null 2>&1 || true
  sysctl -w net.core.rmem_default=1048576 >/dev/null 2>&1 || true
  sysctl -w net.core.wmem_default=1048576 >/dev/null 2>&1 || true
  sysctl -w net.core.netdev_max_backlog=16384 >/dev/null 2>&1 || true
  BBR_QDISC="$(sysctl -n net.core.default_qdisc 2>/dev/null || echo 宿主机控制)"
  echo "BBR：${BBR_STATUS} / 队列=${BBR_QDISC}"
  echo "Hysteria 2 UDP 缓冲区：已尽力优化"
}

configure_timezone_and_daily_reboot() {
  [ -f /usr/share/zoneinfo/Asia/Shanghai ] || fail "Asia/Shanghai 时区文件不存在。"
  ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime
  echo 'Asia/Shanghai' > /etc/timezone
  export TZ=Asia/Shanghai
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
    echo "警告：当前环境不允许启用自动重启定时器，代理安装将继续。"
  fi
  echo "当前时间：$(date '+%F %T %Z %z')"
}

resolve_latest_stable_version() {
  repo="$1"; fallback="$2"
  json="$(curl -fsSL --connect-timeout 8 --max-time 25 \
    -H 'Accept: application/vnd.github+json' \
    -H 'User-Agent: jp-relay-landing-installer' \
    "https://api.github.com/repos/${repo}/releases/latest" 2>/dev/null || true)"
  tag="$(printf '%s' "$json" | jq -r 'select(.draft==false and .prerelease==false) | .tag_name // empty' 2>/dev/null | head -n1)"
  tag="${tag#v}"
  if printf '%s' "$tag" | grep -Eq '^[0-9]+([.][0-9]+){2,}$'; then
    printf '%s' "$tag"
  else
    printf '%s' "$fallback"
    return 1
  fi
}

resolve_core_versions() {
  if detected="$(resolve_latest_stable_version 'XTLS/Xray-core' "$XRAY_FALLBACK_VERSION")"; then
    XRAY_VERSION="$detected"; XRAY_VERSION_SOURCE="官方最新稳定版"
  else
    XRAY_VERSION="$XRAY_FALLBACK_VERSION"; XRAY_VERSION_SOURCE="备用稳定版（版本查询失败）"
  fi
  if detected="$(resolve_latest_stable_version 'SagerNet/sing-box' "$SING_BOX_FALLBACK_VERSION")"; then
    SING_BOX_VERSION="$detected"; SING_BOX_VERSION_SOURCE="官方最新稳定版"
  else
    SING_BOX_VERSION="$SING_BOX_FALLBACK_VERSION"; SING_BOX_VERSION_SOURCE="备用稳定版（版本查询失败）"
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
  version="$1"
  case "$(uname -m)" in
    x86_64|amd64) echo "sing-box-${version}-linux-amd64.tar.gz" ;;
    aarch64|arm64) echo "sing-box-${version}-linux-arm64.tar.gz" ;;
    *) fail "sing-box 不支持当前 CPU 架构：$(uname -m)" ;;
  esac
}

service_stop() {
  name="$1"
  systemctl stop "$name" >/dev/null 2>&1 || true
}

service_restart() {
  name="$1"
  systemctl restart "$name"
}

service_active() {
  name="$1"
  systemctl is-active --quiet "$name"
}

install_xray_version() {
  version="$1"
  archive="$(xray_archive_name)" || return 1
  work="$(mktemp -d /tmp/xray-landing.XXXXXX)" || return 1
  zip_file="${work}/${archive}"
  dgst_file="${zip_file}.dgst"
  url="https://github.com/XTLS/Xray-core/releases/download/v${version}/${archive}"
  echo "下载 Xray v${version}：${archive}"
  retry 5 5 curl -fL --connect-timeout 10 --max-time 180 -o "$zip_file" "$url" || { rm -rf "$work"; return 1; }
  retry 5 5 curl -fL --connect-timeout 10 --max-time 60 -o "$dgst_file" "${url}.dgst" || { rm -rf "$work"; return 1; }
  expected="$(grep -Eo '[0-9a-fA-F]{64}' "$dgst_file" | head -n1 | tr 'A-F' 'a-f')"
  actual="$(sha256sum "$zip_file" | awk '{print $1}')"
  [ -n "$expected" ] && [ "$expected" = "$actual" ] || { echo "Xray v${version} SHA256 校验失败。" >&2; rm -rf "$work"; return 1; }
  unzip -q "$zip_file" -d "$work" || { rm -rf "$work"; return 1; }
  [ -x "$work/xray" ] || { rm -rf "$work"; return 1; }
  detected="$("$work/xray" version 2>/dev/null | awk 'NR==1{print $2}')"
  [ "$detected" = "$version" ] || { echo "Xray 二进制版本校验失败。" >&2; rm -rf "$work"; return 1; }
  service_stop vvv-landing-xray
  mkdir -p /usr/local/bin
  cp "$work/xray" "$XRAY"; chmod 755 "$XRAY"
  rm -rf "$work"
  "$XRAY" version | head -n2
}

install_xray_binary() {
  current=""; requested="$XRAY_VERSION"
  [ ! -x "$XRAY" ] || current="$("$XRAY" version 2>/dev/null | awk 'NR==1{print $2}')"
  [ "$current" != "$requested" ] || { echo "Xray v${requested} 已安装，复用现有二进制。"; return 0; }
  if install_xray_version "$requested"; then return 0; fi
  if [ "$requested" != "$XRAY_FALLBACK_VERSION" ]; then
    echo "Xray 最新稳定版 v${requested} 下载或校验失败，自动回退到 v${XRAY_FALLBACK_VERSION}。" >&2
    XRAY_VERSION="$XRAY_FALLBACK_VERSION"; XRAY_VERSION_SOURCE="备用稳定版（最新版安装失败）"
    install_xray_version "$XRAY_VERSION" || fail "Xray 最新版和备用版均安装失败。"
    return 0
  fi
  fail "Xray v${requested} 安装失败。"
}

install_sing_box_version() {
  version="$1"
  archive="$(sing_box_archive_name_for_version "$version")" || return 1
  work="$(mktemp -d /tmp/sing-box-landing.XXXXXX)" || return 1
  tarball="${work}/${archive}"
  release_json="${work}/release.json"
  retry 5 5 curl -fL --connect-timeout 10 --max-time 60 \
    -H 'Accept: application/vnd.github+json' \
    -H 'User-Agent: jp-relay-landing-installer' \
    -o "$release_json" \
    "https://api.github.com/repos/SagerNet/sing-box/releases/tags/v${version}" || { rm -rf "$work"; return 1; }
  asset_url="$(jq -er --arg n "$archive" '.assets[] | select(.name==$n) | .browser_download_url' "$release_json" 2>/dev/null)" || { rm -rf "$work"; return 1; }
  expected="$(jq -r --arg n "$archive" '.assets[] | select(.name==$n) | (.digest // "") | sub("^sha256:";"")' "$release_json")"
  printf '%s' "$expected" | grep -Eq '^[0-9a-fA-F]{64}$' || { echo "GitHub 没有返回 sing-box v${version} 的 SHA256 摘要。" >&2; rm -rf "$work"; return 1; }
  retry 5 5 curl -fL --connect-timeout 10 --max-time 180 -o "$tarball" "$asset_url" || { rm -rf "$work"; return 1; }
  actual="$(sha256sum "$tarball" | awk '{print $1}')"
  [ "$(printf '%s' "$expected" | tr 'A-F' 'a-f')" = "$(printf '%s' "$actual" | tr 'A-F' 'a-f')" ] || { echo "sing-box v${version} SHA256 校验失败。" >&2; rm -rf "$work"; return 1; }
  tar -xzf "$tarball" -C "$work" || { rm -rf "$work"; return 1; }
  binary="$(find "$work" -type f -name sing-box | head -n1)"
  [ -n "$binary" ] && [ -x "$binary" ] || { rm -rf "$work"; return 1; }
  detected="$("$binary" version 2>/dev/null | awk '/sing-box version/{print $3; exit}')"
  [ "$detected" = "$version" ] || { echo "sing-box 二进制版本校验失败。" >&2; rm -rf "$work"; return 1; }
  service_stop vvv-landing-sing-box
  mkdir -p /usr/local/bin
  cp "$binary" "$SING_BOX"; chmod 755 "$SING_BOX"
  rm -rf "$work"
  "$SING_BOX" version | head -n3
}

install_sing_box_binary() {
  current=""; requested="$SING_BOX_VERSION"
  [ ! -x "$SING_BOX" ] || current="$("$SING_BOX" version 2>/dev/null | awk '/sing-box version/{print $3; exit}')"
  [ "$current" != "$requested" ] || { echo "sing-box v${requested} 已安装，复用现有二进制。"; return 0; }
  if install_sing_box_version "$requested"; then return 0; fi
  if [ "$requested" != "$SING_BOX_FALLBACK_VERSION" ]; then
    echo "sing-box 最新稳定版 v${requested} 下载或校验失败，自动回退到 v${SING_BOX_FALLBACK_VERSION}。" >&2
    SING_BOX_VERSION="$SING_BOX_FALLBACK_VERSION"; SING_BOX_VERSION_SOURCE="备用稳定版（最新版安装失败）"
    install_sing_box_version "$SING_BOX_VERSION" || fail "sing-box 最新版和备用版均安装失败。"
    return 0
  fi
  fail "sing-box v${requested} 安装失败。"
}

create_services() {
  if mode_has_vless; then
    getent group xray >/dev/null 2>&1 || groupadd --system xray
    id xray >/dev/null 2>&1 || useradd --system --gid xray --no-create-home --shell /usr/sbin/nologin xray
    install -d -o root -g xray -m 750 /etc/vvv-landing/xray
    cat > /etc/systemd/system/vvv-landing-vvv-landing-xray.service <<EOF_XRAY_SERVICE
[Unit]
Description=Xray Landing VLESS Service
After=network-online.target nss-lookup.target
Wants=network-online.target

[Service]
User=xray
Group=xray
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
AmbientCapabilities=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
Environment=GOMEMLIMIT=${GOMEMLIMIT_VALUE}
Environment=GOGC=50
ExecStart=/usr/local/bin/xray run -format=json -config /etc/vvv-landing/xray/config.json
Restart=on-failure
RestartSec=3s
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF_XRAY_SERVICE
    systemctl enable vvv-landing-xray >/dev/null
  fi
  if mode_has_hy2; then
    getent group sing-box >/dev/null 2>&1 || groupadd --system sing-box
    id sing-box >/dev/null 2>&1 || useradd --system --gid sing-box --no-create-home --shell /usr/sbin/nologin sing-box
    install -d -o root -g sing-box -m 750 /etc/vvv-landing/sing-box "$TLS_DIR"
    cat > /etc/systemd/system/vvv-landing-vvv-landing-sing-box.service <<EOF_SING_SERVICE
[Unit]
Description=sing-box Landing Hysteria 2 Service
After=network-online.target nss-lookup.target
Wants=network-online.target

[Service]
User=sing-box
Group=sing-box
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
AmbientCapabilities=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
Environment=GOMEMLIMIT=${GOMEMLIMIT_VALUE}
Environment=GOGC=50
ExecStart=/usr/local/bin/sing-box run -c /etc/vvv-landing/sing-box/config.json
Restart=on-failure
RestartSec=3s
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF_SING_SERVICE
    systemctl enable vvv-landing-sing-box >/dev/null
  fi
  systemctl daemon-reload
}

check_port_available() {
  protocol="$1"; port="$2"; allowed="$3"
  if [ "$protocol" = "tcp" ]; then
    existing="$(ss -H -lntp "sport = :${port}" 2>/dev/null || true)"
  else
    existing="$(ss -H -lnup "sport = :${port}" 2>/dev/null || true)"
  fi
  if [ -n "$existing" ] && ! printf '%s\n' "$existing" | grep -qi "$allowed"; then
    echo "$existing"
    fail "${protocol} ${port} 已被其他程序占用。"
  fi
}

allocate_local_test_port() {
  start="$1"; end="$2"; port="$start"
  while [ "$port" -le "$end" ]; do
    if [ -z "$(ss -H -lnt "sport = :${port}" 2>/dev/null || true)" ]; then
      echo "$port"; return
    fi
    port=$((port + 1))
  done
  fail "无法分配本地闭环测试端口。"
}

write_hy2_certificate() {
  mkdir -p "$TLS_DIR"
  cert_path="${TLS_DIR}/landing-hy2.crt"
  key_path="${TLS_DIR}/landing-hy2.key"
  printf '%s\n' "$REMOTE_HY2_CERT_PEM" > "$cert_path"
  printf '%s\n' "$REMOTE_HY2_KEY_PEM" > "$key_path"
  chmod 640 "$cert_path" "$key_path"
  chown root:sing-box "$cert_path" "$key_path"
  runuser -u sing-box -- test -r "$cert_path" || fail "sing-box 用户无法读取落地 Hysteria 2 证书。"
  runuser -u sing-box -- test -r "$key_path" || fail "sing-box 用户无法读取落地 Hysteria 2 私钥。"
  actual_fp="$(openssl x509 -in "$cert_path" -noout -fingerprint -sha256 | sed 's/^[^=]*=//')"
  [ "$actual_fp" = "$REMOTE_HY2_FINGERPRINT" ] || fail "JPR3 中的 Hysteria 2 证书指纹不匹配。"
  actual_pin="$(openssl x509 -in "$cert_path" -pubkey -noout | openssl pkey -pubin -outform der | openssl dgst -sha256 -binary | openssl enc -base64 -A)"
  [ "$actual_pin" = "$REMOTE_HY2_PUBLIC_KEY_SHA256" ] || fail "JPR3 中的 Hysteria 2 公钥固定值不匹配。"
}

write_xray_config() {
  mkdir -p /etc/vvv-landing/xray
  VLESS_TEST_PORT="$(allocate_local_test_port 18080 18999)"
  TMP_CFG="$(mktemp /tmp/landing-xray.XXXXXX)"
  cat > "$TMP_CFG" <<EOF_XRAY_CONFIG
{
  "log": {"loglevel": "warning"},
  "inbounds": [
    {
      "tag": "landing-vless-in",
      "listen": "0.0.0.0",
      "port": ${REMOTE_PUBLIC_PORT},
      "protocol": "vless",
      "settings": {
        "clients": [{"id": "${REMOTE_UUID}", "level": 0, "email": "landing-external@relay.local", "flow": "xtls-rprx-vision"}],
        "decryption": "none"
      },
      "streamSettings": {
        "method": "raw",
        "security": "reality",
        "realitySettings": {
          "show": false,
          "target": "${SNI}:443",
          "xver": 0,
          "serverNames": ["${SNI}"],
          "privateKey": "${REMOTE_REALITY_PRIVATE_KEY}",
          "shortIds": ["${REMOTE_REALITY_SHORT_ID}"]
        }
      },
      "sniffing": {"enabled": true, "destOverride": ["http", "tls", "quic"], "routeOnly": true}
    },
    {
      "tag": "landing-vless-test",
      "listen": "127.0.0.1",
      "port": ${VLESS_TEST_PORT},
      "protocol": "socks",
      "settings": {"udp": false},
      "sniffing": {"enabled": true, "destOverride": ["http", "tls"], "routeOnly": true}
    }
  ],
  "outbounds": [
    {"tag": "direct", "protocol": "freedom", "settings": {"domainStrategy": "UseIPv4"}},
    {
      "tag": "back-to-japan",
      "protocol": "vless",
      "settings": {"address": "${JAPAN_PUBLIC_IP}", "port": ${JAPAN_PORT}, "id": "${JAPAN_CLIENT_UUID}", "encryption": "none", "flow": "xtls-rprx-vision"},
      "streamSettings": {
        "method": "raw",
        "security": "reality",
        "realitySettings": {"serverName": "${SNI}", "fingerprint": "chrome", "password": "${JAPAN_REALITY_PUBLIC_KEY}", "shortId": "${JAPAN_REALITY_SHORT_ID}", "spiderX": ""}
      }
    },
    {"tag": "blocked", "protocol": "blackhole", "settings": {}}
  ],
  "routing": {
    "domainStrategy": "AsIs",
    "rules": [
      {"type": "field", "ip": ["0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4", "::1/128", "fc00::/7", "fe80::/10"], "outboundTag": "blocked", "ruleTag": "block-private"},
      {"type": "field", "protocol": ["bittorrent"], "outboundTag": "blocked", "ruleTag": "block-bittorrent"},
      {"type": "field", "inboundTag": ["landing-vless-test"], "outboundTag": "back-to-japan", "ruleTag": "closed-loop-test"},
      {"type": "field", "inboundTag": ["landing-vless-in"], "outboundTag": "direct", "ruleTag": "landing-direct"}
    ]
  }
}
EOF_XRAY_CONFIG
  "$XRAY" run -test -format=json -config "$TMP_CFG" || return 1
  install -o root -g xray -m 640 "$TMP_CFG" "$XRAY_CFG" || return 1
  rm -f "$TMP_CFG"; TMP_CFG=""
}

write_sing_config() {
  mkdir -p /etc/vvv-landing/sing-box
  write_hy2_certificate
  HY2_TEST_PORT="$(allocate_local_test_port 19080 19999)"
  TMP_CFG="$(mktemp /tmp/landing-sing.XXXXXX)"
  cat > "$TMP_CFG" <<EOF_SING_CONFIG
{
  "log": {"level": "warn", "timestamp": true},
  "inbounds": [
    {
      "type": "hysteria2",
      "tag": "landing-hy2-in",
      "listen": "0.0.0.0",
      "listen_port": ${REMOTE_PUBLIC_PORT},
      "users": [{"name": "landing-user", "password": "${REMOTE_HY2_PASSWORD}"}],
      "up_mbps": ${HY2_LIMIT_MBPS},
      "down_mbps": ${HY2_LIMIT_MBPS},
      "ignore_client_bandwidth": false,
      "obfs": {"type": "salamander", "password": "${REMOTE_HY2_OBFS}"},
      "tls": {
        "enabled": true,
        "server_name": "${REMOTE_HY2_SERVER_NAME}",
        "alpn": ["h3"],
        "min_version": "1.3",
        "certificate_path": "${TLS_DIR}/landing-hy2.crt",
        "key_path": "${TLS_DIR}/landing-hy2.key"
      }
    },
    {
      "type": "mixed",
      "tag": "landing-hy2-test",
      "listen": "127.0.0.1",
      "listen_port": ${HY2_TEST_PORT}
    }
  ],
  "outbounds": [
    {"type": "direct", "tag": "direct"},
    {
      "type": "hysteria2",
      "tag": "back-to-japan",
      "server": "${JAPAN_PUBLIC_IP}",
      "server_port": ${JAPAN_PORT},
      "up_mbps": ${HY2_LIMIT_MBPS},
      "down_mbps": ${HY2_LIMIT_MBPS},
      "password": "${JAPAN_HY2_PASSWORD}",
      "obfs": {"type": "salamander", "password": "${JAPAN_HY2_OBFS}"},
      "tls": {
        "enabled": true,
        "server_name": "${JAPAN_HY2_SERVER_NAME}",
        "insecure": true,
        "alpn": ["h3"],
        "min_version": "1.3",
        "certificate_public_key_sha256": ["${JAPAN_HY2_PUBLIC_KEY_SHA256}"]
      }
    }
  ],
  "route": {
    "rules": [
      {"ip_is_private": true, "action": "reject", "method": "drop"},
      {"inbound": ["landing-hy2-test"], "action": "route", "outbound": "back-to-japan"},
      {"inbound": ["landing-hy2-in"], "action": "route", "outbound": "direct"}
    ],
    "final": "direct",
    "auto_detect_interface": true
  }
}
EOF_SING_CONFIG
  "$SING_BOX" check -c "$TMP_CFG" || return 1
  install -o root -g sing-box -m 640 "$TMP_CFG" "$SING_CFG" || return 1
  runuser -u sing-box -- "$SING_BOX" check -c "$SING_CFG" || return 1
  rm -f "$TMP_CFG"; TMP_CFG=""
}

verify_runtime() {
  if mode_has_vless; then
    service_active vvv-landing-xray || return 1
    ss -H -lntp "sport = :${REMOTE_PUBLIC_PORT}" 2>/dev/null | grep -qi xray || return 1
    ss -H -lntp "sport = :${VLESS_TEST_PORT}" 2>/dev/null | grep -qi xray || return 1
  fi
  if mode_has_hy2; then
    service_active vvv-landing-sing-box || return 1
    ss -H -lnup "sport = :${REMOTE_PUBLIC_PORT}" 2>/dev/null | grep -qi sing-box || return 1
    ss -H -lntp "sport = :${HY2_TEST_PORT}" 2>/dev/null | grep -qi sing-box || return 1
  fi
  return 0
}

probe_proxy() {
  socks_port="$1"; expected_ip="$2"; label="$3"
  exit_ip=""; last_error=""
  for url in https://api.ipify.org https://ipv4.icanhazip.com; do
    err_file="$(mktemp /tmp/landing-probe.XXXXXX)"
    if exit_ip="$(curl -4sS --socks5-hostname "127.0.0.1:${socks_port}" --connect-timeout 8 --max-time 25 "$url" 2>"$err_file" | tr -d '[:space:]')"; then
      if valid_ipv4 "$exit_ip"; then
        rm -f "$err_file"
        [ "$exit_ip" = "$expected_ip" ] || { echo "${label} 闭环出口为 ${exit_ip}，预期为 ${expected_ip}。" >&2; return 1; }
        break
      fi
    fi
    last_error="$(tr '\n' ' ' < "$err_file" | sed 's/[[:space:]]\+/ /g')"
    rm -f "$err_file"
  done
  [ -n "$exit_ip" ] || { echo "${label} 无法获取出口 IP：${last_error}" >&2; return 1; }

  for url in https://www.gstatic.com/generate_204 https://www.google.com/generate_204; do
    result="$(curl -sS --socks5-hostname "127.0.0.1:${socks_port}" --connect-timeout 8 --max-time 25 -o /dev/null -w '%{http_code}|%{time_total}' "$url" 2>/dev/null || true)"
    code="${result%%|*}"; seconds="${result#*|}"
    if [ "$code" = "204" ]; then
      milliseconds="$(awk -v t="$seconds" 'BEGIN{printf "%.0f",t*1000}')"
      echo "${label}：在线，出口 ${exit_ip}，${milliseconds} ms"
      return 0
    fi
  done
  echo "${label} 已获得正确出口 ${exit_ip}，但 generate_204 检测失败。" >&2
  return 1
}

urlencode() {
  jq -rn --arg value "$1" '$value|@uri'
}

protocol_name() {
  base="$1"; proto="$2"
  if printf '%s' "$base" | grep -Eq '^[A-Z]{2}-'; then
    country="${base%%-*}"; rest="${base#*-}"
    printf '%s-%s-%s' "$country" "$proto" "$rest"
  elif printf '%s' "$base" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+:[0-9]+$'; then
    printf '%s-%s' "$proto" "$base"
  else
    printf '%s-%s' "$base" "$proto"
  fi
}

relay_client_base() {
  raw="$1"
  if printf '%s' "$raw" | grep -Eq '^[A-Za-z]{2}-'; then
    country="$(printf '%s' "${raw%%-*}" | tr '[:lower:]' '[:upper:]')"
    printf '%s-中转-%s:%s' "$country" "$JAPAN_PUBLIC_IP" "$JAPAN_PORT"
  else
    printf '中转-%s:%s' "$JAPAN_PUBLIC_IP" "$JAPAN_PORT"
  fi
}

generate_client_files() {
  mkdir -p "$CLIENT_DIR"
  : > "$CLIENT_DIR/Quantumult-X.conf"
  : > "$CLIENT_DIR/Loon.conf"
  : > "$CLIENT_DIR/Loon-Shadowrocket.txt"
  : > "$CLIENT_DIR/Shadowrocket.txt"
  echo 'proxies:' > "$CLIENT_DIR/Clash-Verge-Rev.yaml"
  {
    echo "中转客户端节点"
    echo "===================================="
    echo "线路：${NODE_NAME}"
    echo "日本入口：${JAPAN_PUBLIC_IP}:${JAPAN_PORT}"
    echo "最终落地：${REMOTE_PUBLIC_IP}:${REMOTE_PUBLIC_PORT}"
    echo "协议模式：${PROTOCOL_MODE}"
  } > "$CLIENT_DIR/客户端节点.txt"

  if mode_has_vless; then
    vless_name="$(protocol_name "$(relay_client_base "$NODE_NAME")" VLESS)"
    encoded_vless_name="$(urlencode "$vless_name")"
    vless_params="encryption=none&flow=xtls-rprx-vision&security=reality&sni=$(urlencode "$SNI")&fp=chrome&pbk=$(urlencode "$JAPAN_REALITY_PUBLIC_KEY")&sid=$(urlencode "$JAPAN_REALITY_SHORT_ID")&type=tcp&headerType=none"
    vless_uri="vless://${JAPAN_CLIENT_UUID}@${JAPAN_PUBLIC_IP}:${JAPAN_PORT}?${vless_params}#${encoded_vless_name}"
    qx="vless=${JAPAN_PUBLIC_IP}:${JAPAN_PORT}, method=none, password=${JAPAN_CLIENT_UUID}, obfs=over-tls, obfs-host=${SNI}, reality-base64-pubkey=${JAPAN_REALITY_PUBLIC_KEY}, reality-hex-shortid=${JAPAN_REALITY_SHORT_ID}, vless-flow=xtls-rprx-vision, fast-open=false, udp-relay=true, tag=${vless_name}"
    loon="${vless_name} = VLESS,${JAPAN_PUBLIC_IP},${JAPAN_PORT},\"${JAPAN_CLIENT_UUID}\",transport=tcp,flow=xtls-rprx-vision,public-key=\"${JAPAN_REALITY_PUBLIC_KEY}\",short-id=${JAPAN_REALITY_SHORT_ID},udp=true,over-tls=true,sni=${SNI},skip-cert-verify=true"
    printf '%s\n' "$qx" >> "$CLIENT_DIR/Quantumult-X.conf"
    printf '%s\n' "$loon" >> "$CLIENT_DIR/Loon.conf"
    printf '%s\n' "$vless_uri" >> "$CLIENT_DIR/Loon-Shadowrocket.txt"
    printf '%s\n' "$vless_uri" >> "$CLIENT_DIR/Shadowrocket.txt"
    cat >> "$CLIENT_DIR/Clash-Verge-Rev.yaml" <<EOF_CLASH_VLESS
  - name: "${vless_name}"
    type: vless
    server: ${JAPAN_PUBLIC_IP}
    port: ${JAPAN_PORT}
    uuid: ${JAPAN_CLIENT_UUID}
    network: tcp
    udp: true
    tls: true
    flow: xtls-rprx-vision
    encryption: ""
    servername: ${SNI}
    client-fingerprint: chrome
    skip-cert-verify: true
    reality-opts:
      public-key: ${JAPAN_REALITY_PUBLIC_KEY}
      short-id: "${JAPAN_REALITY_SHORT_ID}"
EOF_CLASH_VLESS
    {
      echo
      echo "【Quantumult X】"
      echo "$qx"
      echo
      echo "【Loon / Shadowrocket：${vless_name}】"
      echo "Loon 原生配置："
      echo "$loon"
      echo "分享链接："
      echo "$vless_uri"
    } >> "$CLIENT_DIR/客户端节点.txt"
  fi

  if mode_has_hy2; then
    hy2_name="$(protocol_name "$(relay_client_base "$NODE_NAME")" HY2)"
    encoded_hy2_name="$(urlencode "$hy2_name")"
    hy2_uri="hysteria2://$(urlencode "$JAPAN_HY2_PASSWORD")@${JAPAN_PUBLIC_IP}:${JAPAN_PORT}/?obfs=salamander&obfs-password=$(urlencode "$JAPAN_HY2_OBFS")&sni=$(urlencode "$JAPAN_HY2_SERVER_NAME")&insecure=1&pinSHA256=$(urlencode "$JAPAN_HY2_PIN_HEX")#${encoded_hy2_name}"
    loon="${hy2_name} = Hysteria2,${JAPAN_PUBLIC_IP},${JAPAN_PORT},\"${JAPAN_HY2_PASSWORD}\",skip-cert-verify=true,sni=${JAPAN_HY2_SERVER_NAME},udp=true,fast-open=true,salamander-password=\"${JAPAN_HY2_OBFS}\""
    printf '%s\n' "$loon" >> "$CLIENT_DIR/Loon.conf"
    printf '%s\n' "$hy2_uri" >> "$CLIENT_DIR/Loon-Shadowrocket.txt"
    printf '%s\n' "$hy2_uri" >> "$CLIENT_DIR/Shadowrocket.txt"
    cat >> "$CLIENT_DIR/Clash-Verge-Rev.yaml" <<EOF_CLASH_HY2
  - name: "${hy2_name}"
    type: hysteria2
    server: ${JAPAN_PUBLIC_IP}
    port: ${JAPAN_PORT}
    password: "${JAPAN_HY2_PASSWORD}"
    up: "${HY2_LIMIT_MBPS} Mbps"
    down: "${HY2_LIMIT_MBPS} Mbps"
    obfs: salamander
    obfs-password: "${JAPAN_HY2_OBFS}"
    sni: ${JAPAN_HY2_SERVER_NAME}
    skip-cert-verify: true
    fingerprint: "${JAPAN_HY2_FINGERPRINT}"
    alpn:
      - h3
    udp: true
EOF_CLASH_HY2
    {
      echo
      echo "【Hysteria 2 服务端硬上限】"
      echo "上行 ${HY2_LIMIT_MBPS} Mbps / 下行 ${HY2_LIMIT_MBPS} Mbps"
      echo
      echo "【Loon / Shadowrocket：${hy2_name}】"
      echo "Loon 原生配置："
      echo "$loon"
      echo "分享链接："
      echo "$hy2_uri"
    } >> "$CLIENT_DIR/客户端节点.txt"
  fi

  cp "$CLIENT_DIR/Clash-Verge-Rev.yaml" "$CLIENT_DIR/NekoBoxForAndroid.yaml"

  {
    echo
    echo "【Clash Verge Rev / Mihomo】"
    cat "$CLIENT_DIR/Clash-Verge-Rev.yaml"
    echo
    echo "【NekoBoxForAndroid（Clash Meta）】"
    cat "$CLIENT_DIR/NekoBoxForAndroid.yaml"
  } >> "$CLIENT_DIR/客户端节点.txt"

  cp "$CLIENT_DIR/客户端节点.txt" "$CLIENT_NODES_FILE"
  chmod 700 "$CLIENT_DIR"
  chmod 600 "$CLIENT_DIR"/* "$CLIENT_NODES_FILE"
}

save_state() {
  mkdir -p "$STATE_DIR"
  printf '%s\n' "$PAIRING_KEY" > "$PAIR_FILE"
  printf '%s' "$PAIR_JSON" | jq \
    --arg installed_at "$(date '+%Y-%m-%dT%H:%M:%S%z')" \
    --arg bbr "$BBR_STATUS" \
    --arg qdisc "$BBR_QDISC" \
    --arg xray_runtime "$XRAY_VERSION" \
    --arg sing_runtime "$SING_BOX_VERSION" \
    --argjson vless_test "${VLESS_TEST_PORT:-null}" \
    --argjson hy2_test "${HY2_TEST_PORT:-null}" \
    '. + {role:"landing",installed_at:$installed_at,bbr_status:$bbr,qdisc:$qdisc,xray_runtime_version:$xray_runtime,sing_box_runtime_version:$sing_runtime,vless_test_port:$vless_test,hy2_test_port:$hy2_test}' \
    > "$STATE_FILE"
  chmod 700 "$STATE_DIR"
  chmod 600 "$PAIR_FILE" "$STATE_FILE"
}

install_shortcuts() {
  mkdir -p /usr/local/sbin /usr/local/lib/vvv
  cat > /usr/local/lib/vvv/update_landing_ip.py <<'PY_UPDATE_LANDING_IP'
#!/usr/bin/env python3
import ipaddress
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

STATE = Path('/etc/jp-relay/landing-state.json')
XRAY_CFG = Path('/etc/vvv-landing/xray/config.json')
SING_CFG = Path('/etc/vvv-landing/sing-box/config.json')
CLIENT_DIR = Path('/root/中转客户端配置')
CLIENT_NODES = Path('/root/中转客户端节点.txt')
SYNC_CFG = Path('/etc/vvv/client.json')
SYNC_AGENT = Path('/usr/local/lib/vvv/sync_agent.py')


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def atomic_json(path, obj, mode=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(obj, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        if mode is None and path.exists():
            mode = path.stat().st_mode & 0o777
        os.chmod(tmp, mode or 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def patch_xray(obj, new_ip):
    changed = False
    for outbound in obj.get('outbounds', []):
        if outbound.get('tag') == 'back-to-japan':
            settings = outbound.setdefault('settings', {})
            settings['address'] = new_ip
            changed = True
    if not changed:
        raise RuntimeError('Xray 配置中未找到 back-to-japan。')


def patch_sing(obj, new_ip):
    changed = False
    for outbound in obj.get('outbounds', []):
        if outbound.get('tag') == 'back-to-japan':
            outbound['server'] = new_ip
            changed = True
    if not changed:
        raise RuntimeError('sing-box 配置中未找到 back-to-japan。')


def validate(candidate, kind):
    if kind == 'xray':
        subprocess.run(['/usr/local/bin/xray', 'run', '-test', '-format=json', '-config', str(candidate)], check=True, timeout=60)
    else:
        subprocess.run(['/usr/local/bin/sing-box', 'check', '-c', str(candidate)], check=True, timeout=60)


def restart_active(service):
    subprocess.run(['systemctl', 'restart', service], check=True, timeout=75)
    subprocess.run(['systemctl', 'is-active', '--quiet', service], check=True, timeout=20)


def update_text_files(old_ip, new_ip):
    paths = list(CLIENT_DIR.glob('*')) if CLIENT_DIR.exists() else []
    if CLIENT_NODES.exists():
        paths.append(CLIENT_NODES)
    for path in paths:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            continue
        if old_ip in text:
            path.write_text(text.replace(old_ip, new_ip), encoding='utf-8')
            os.chmod(path, 0o600)


def update_sync_center(old_ip, new_ip):
    if not (SYNC_CFG.exists() and SYNC_AGENT.exists()):
        return
    try:
        cfg = load(SYNC_CFG)
    except Exception:
        return
    base = str(cfg.get('api_base_url') or cfg.get('base_url') or '')
    if old_ip not in base:
        return
    subprocess.run(['python3', str(SYNC_AGENT), 'update-center-ip', new_ip], check=True, timeout=45)


def main():
    if len(sys.argv) != 2:
        raise SystemExit('用法：update_landing_ip.py 新主机IP')
    try:
        new_ip = str(ipaddress.IPv4Address(sys.argv[1].strip()))
    except ipaddress.AddressValueError as exc:
        raise SystemExit('主机 IP 格式错误。') from exc
    state = load(STATE)
    old_ip = str(state.get('japan_public_ip') or '')
    if not old_ip:
        raise SystemExit('落地状态中没有旧主机 IP。')
    if new_ip == old_ip:
        print('主机 IP 没有变化。')
        return
    mode = str(state.get('protocol_mode') or '')
    paths = [STATE, XRAY_CFG, SING_CFG, SYNC_CFG, CLIENT_NODES]
    if CLIENT_DIR.exists():
        paths.extend(p for p in CLIENT_DIR.iterdir() if p.is_file())
    with tempfile.TemporaryDirectory(prefix='vvv-landing-ip.') as work:
        work = Path(work)
        backups = {}
        for path in paths:
            if path.exists():
                target = work / (str(len(backups)) + '-' + path.name)
                shutil.copy2(path, target)
                backups[path] = target
        candidates = {}
        try:
            if mode in ('dual', 'vless'):
                xray = load(XRAY_CFG)
                patch_xray(xray, new_ip)
                candidate = work / 'xray.json'
                candidate.write_text(json.dumps(xray, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                validate(candidate, 'xray')
                candidates[XRAY_CFG] = candidate
            if mode in ('dual', 'hy2'):
                sing = load(SING_CFG)
                patch_sing(sing, new_ip)
                candidate = work / 'sing.json'
                candidate.write_text(json.dumps(sing, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                validate(candidate, 'sing')
                candidates[SING_CFG] = candidate
            state['japan_public_ip'] = new_ip
            state['main_ip_updated_at'] = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
            atomic_json(STATE, state)
            for target, candidate in candidates.items():
                shutil.copy2(candidate, target)
                os.chmod(target, 0o640)
            update_text_files(old_ip, new_ip)
            update_sync_center(old_ip, new_ip)
            if mode in ('dual', 'vless'):
                restart_active('vvv-landing-xray.service')
            if mode in ('dual', 'hy2'):
                restart_active('vvv-landing-sing-box.service')
        except Exception:
            for path, backup in backups.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, path)
            subprocess.run(['systemctl', 'restart', 'vvv-landing-xray.service'], check=False, timeout=75)
            subprocess.run(['systemctl', 'restart', 'vvv-landing-sing-box.service'], check=False, timeout=75)
            raise
    print(f'主机 IP 修改成功：{old_ip} → {new_ip}')
    print('代理配置、客户端节点文件及订阅同步地址已同步更新。')


if __name__ == '__main__':
    main()
PY_UPDATE_LANDING_IP
  chmod 700 /usr/local/lib/vvv/update_landing_ip.py

  cat > /usr/local/sbin/landing-vps <<'EOF_LANDING_VPS'
#!/bin/sh
set -u
state=/etc/jp-relay/landing-state.json
nodes=/root/中转客户端节点.txt
updater=/usr/local/lib/vvv/update_landing_ip.py
[ -f "$state" ] || { echo "尚未安装落地节点。"; exit 1; }

valid_ipv4() {
  ip="$1"; old_ifs="$IFS"; IFS=.; set -- $ip; IFS="$old_ifs"
  [ "$#" -eq 4 ] || return 1
  for octet in "$@"; do case "$octet" in ''|*[!0-9]*) return 1;; esac; [ "$octet" -le 255 ] || return 1; done
  [ "$ip" != "0.0.0.0" ]
}

probe_proxy() {
  socks_port="$1"; expected_ip="$2"
  exit_ip=""; last_error=""
  for url in https://api.ipify.org https://ipv4.icanhazip.com; do
    err_file="$(mktemp /tmp/landing-vps-probe.XXXXXX)"
    exit_ip="$(curl -4sS --socks5-hostname "127.0.0.1:${socks_port}" --connect-timeout 8 --max-time 25 "$url" 2>"$err_file" | tr -d '[:space:]' || true)"
    if valid_ipv4 "$exit_ip"; then rm -f "$err_file"; break; fi
    last_error="$(tr '\n' ' ' < "$err_file" | sed 's/[[:space:]]\+/ /g')"; rm -f "$err_file"
  done
  valid_ipv4 "$exit_ip" || { PROBE_REASON="无法获取出口 IP：${last_error:-未知错误}"; return 1; }
  [ "$exit_ip" = "$expected_ip" ] || { PROBE_REASON="闭环出口为 ${exit_ip}，预期为 ${expected_ip}"; return 1; }
  for url in https://www.gstatic.com/generate_204 https://www.google.com/generate_204; do
    result="$(curl -sS --socks5-hostname "127.0.0.1:${socks_port}" --connect-timeout 8 --max-time 25 -o /dev/null -w '%{http_code}|%{time_total}' "$url" 2>/dev/null || true)"
    code="${result%%|*}"; seconds="${result#*|}"
    if [ "$code" = 204 ]; then
      PROBE_EXIT_IP="$exit_ip"
      PROBE_TIME="$(awk -v t="$seconds" 'BEGIN{printf "%.0f",t*1000}')"
      return 0
    fi
  done
  PROBE_REASON="已获得正确出口 ${exit_ip}，但网页延迟检测失败"
  return 1
}

print_online() { if [ -t 1 ]; then printf '\033[1;32m%s\033[0m\n' "$1"; else printf '%s\n' "$1"; fi; }
print_offline() { if [ -t 1 ]; then printf '\033[1;31m%s\033[0m\n' "$1"; else printf '%s\n' "$1"; fi; }
pause() { read -r -p "按回车返回……" _; }

show_status() {
  [ ! -f "$nodes" ] || cat "$nodes"
  mode="$(jq -r '.protocol_mode' "$state")"
  expected="$(jq -r '.remote_public_ip' "$state")"
  echo
  echo "当前主机 IP：$(jq -r '.japan_public_ip' "$state")"
  echo "Xray-core：$([ -x /usr/local/bin/xray ] && /usr/local/bin/xray version 2>/dev/null | awk 'NR==1{print "v"$2}' || echo 未安装)"
  echo "sing-box：$([ -x /usr/local/bin/sing-box ] && /usr/local/bin/sing-box version 2>/dev/null | awk '/sing-box version/{print "v"$3;exit}' || echo 未安装)"
  echo
  echo "========== 执行真实双向闭环检测 =========="
  case "$mode" in dual|vless)
    port="$(jq -r '.vless_test_port' "$state")"
    if probe_proxy "$port" "$expected"; then print_online "VLESS + REALITY：在线，出口 ${PROBE_EXIT_IP}，${PROBE_TIME} ms"; else print_offline "VLESS + REALITY：离线，${PROBE_REASON}"; fi
  ;; esac
  case "$mode" in dual|hy2)
    port="$(jq -r '.hy2_test_port' "$state")"
    if probe_proxy "$port" "$expected"; then print_online "Hysteria 2：在线，出口 ${PROBE_EXIT_IP}，${PROBE_TIME} ms"; else print_offline "Hysteria 2：离线，${PROBE_REASON}"; fi
  ;; esac
}

change_main_ip() {
  old="$(jq -r '.japan_public_ip' "$state")"
  while true; do
    read -r -p "请输入新的主机公网 IPv4（当前 ${old}，按回车取消）：" ip
    [ -n "$ip" ] || return 0
    if valid_ipv4 "$ip"; then break; fi
    echo "IP 格式错误，请重新输入。"
  done
  python3 "$updater" "$ip"
}

while true; do
  echo
  echo "========== 中转副机管理 =========="
  echo "1. 查看节点与线路状态"
  echo "2. 修改主机 IP 地址"
  echo "0. 退出"
  read -r -p "请输入编号：" choice
  case "$choice" in
    1) show_status; pause;;
    2) change_main_ip; pause;;
    0) exit 0;;
    *) echo "请输入有效编号。";;
  esac
done
EOF_LANDING_VPS
  chmod 700 /usr/local/sbin/landing-vps
  cat > /usr/local/sbin/vps <<'EOF_VPS'
#!/bin/sh
exec /usr/local/sbin/landing-vps
EOF_VPS
  chmod 700 /usr/local/sbin/vps
  cat > /usr/local/sbin/landing-show-nodes <<'EOF_SHOW_NODES'
#!/bin/sh
exec /usr/local/sbin/landing-vps
EOF_SHOW_NODES
  chmod 700 /usr/local/sbin/landing-show-nodes
}

run_probe_summary() {
  green=''; red=''; reset=''
  [ -t 1 ] && { green='\033[1;32m'; red='\033[1;31m'; reset='\033[0m'; }
  log "执行真实双向闭环检测"
  if mode_has_vless; then
    if result="$(probe_proxy "$VLESS_TEST_PORT" "$REMOTE_PUBLIC_IP" "VLESS + REALITY" 2>&1)"; then
      printf "${green}%s${reset}\n" "$result"
    else
      printf "${red}VLESS + REALITY：离线，%s${reset}\n" "${result:-未知错误}"
    fi
  fi
  if mode_has_hy2; then
    if result="$(probe_proxy "$HY2_TEST_PORT" "$REMOTE_PUBLIC_IP" "Hysteria 2" 2>&1)"; then
      printf "${green}%s${reset}\n" "$result"
    else
      printf "${red}Hysteria 2：离线，%s${reset}\n" "${result:-未知错误}"
    fi
  fi
}

reuse_installed_cores() {
  mode_has_vless && [ -x "$XRAY" ] || { mode_has_vless && fail "组合安装未找到已安装的 Xray。"; }
  mode_has_hy2 && [ -x "$SING_BOX" ] || { mode_has_hy2 && fail "组合安装未找到已安装的 sing-box。"; }
  if mode_has_vless; then
    XRAY_VERSION="$("$XRAY" version 2>/dev/null | awk 'NR==1{print $2}')"
    XRAY_VERSION_SOURCE="与本机直连代理共享"
  fi
  if mode_has_hy2; then
    SING_BOX_VERSION="$("$SING_BOX" version 2>/dev/null | awk '/sing-box version/{print $3; exit}')"
    SING_BOX_VERSION_SOURCE="与本机直连代理共享"
  fi
}

CURRENT_STEP="检查 root 权限"
[ "$(id -u)" -eq 0 ] || fail "请使用 root 用户执行。"

CURRENT_STEP="检查操作系统"
log "$CURRENT_STEP"
detect_os

CURRENT_STEP="解析并验证 JPR3 对接密钥"
log "$CURRENT_STEP"
normalize_pairing_key
parse_pairing_key

if [ "$COMBINED_INSTALL" = 1 ]; then
  CURRENT_STEP="复用自身直连代理的系统环境和代理核心"
  log "$CURRENT_STEP"
  reuse_installed_cores
  check_disk_space
  choose_memory_limit
  echo "组合安装不重复执行 APT、Swap、BBR、时区、定时重启或代理核心安装。"
else
  CURRENT_STEP="刷新软件源并安装依赖"
  log "$CURRENT_STEP"
  upgrade_system_once
  CURRENT_STEP="检测官方最新稳定版"
  log "$CURRENT_STEP"
  resolve_core_versions
  CURRENT_STEP="检查磁盘和内存"
  log "$CURRENT_STEP"
  check_disk_space
  choose_memory_limit
  CURRENT_STEP="配置 Swap"
  log "$CURRENT_STEP"
  configure_swap_if_suitable
  CURRENT_STEP="配置 BBR、fq 和 UDP 缓冲区"
  log "$CURRENT_STEP"
  configure_network_tuning
  CURRENT_STEP="设置上海时区和每天 06:00 自动重启"
  log "$CURRENT_STEP"
  configure_timezone_and_daily_reboot
fi

if mode_has_vless; then
  CURRENT_STEP="检查 VLESS TCP 端口"
  log "$CURRENT_STEP"
  check_port_available tcp "$REMOTE_PUBLIC_PORT" xray
  if [ "$COMBINED_INSTALL" != 1 ]; then
    CURRENT_STEP="安装 Xray 最新稳定版"
    log "$CURRENT_STEP"
    install_xray_binary
  fi
fi

if mode_has_hy2; then
  CURRENT_STEP="检查 Hysteria 2 UDP 端口"
  log "$CURRENT_STEP"
  check_port_available udp "$REMOTE_PUBLIC_PORT" sing-box
  if [ "$COMBINED_INSTALL" != 1 ]; then
    CURRENT_STEP="安装 sing-box 最新稳定版"
    log "$CURRENT_STEP"
    install_sing_box_binary
  fi
fi

CURRENT_STEP="创建代理服务"
log "$CURRENT_STEP"
create_services

if mode_has_vless; then
  CURRENT_STEP="生成并验证落地 VLESS 配置"
  log "$CURRENT_STEP"
  if ! write_xray_config; then
    if [ "$COMBINED_INSTALL" != 1 ] && [ "$XRAY_VERSION" != "$XRAY_FALLBACK_VERSION" ]; then
      echo "Xray 最新版配置测试失败，自动使用备用版 v${XRAY_FALLBACK_VERSION}。" >&2
      XRAY_VERSION="$XRAY_FALLBACK_VERSION"; XRAY_VERSION_SOURCE="备用稳定版（最新版配置测试失败）"
      install_xray_version "$XRAY_VERSION" || fail "Xray 备用版安装失败。"
      write_xray_config || fail "Xray 备用版配置测试仍然失败。"
    else
      fail "Xray 配置测试失败。"
    fi
  fi
  service_restart vvv-landing-xray
fi

if mode_has_hy2; then
  CURRENT_STEP="生成并验证落地 Hysteria 2 配置"
  log "$CURRENT_STEP"
  if ! write_sing_config; then
    if [ "$COMBINED_INSTALL" != 1 ] && [ "$SING_BOX_VERSION" != "$SING_BOX_FALLBACK_VERSION" ]; then
      echo "sing-box 最新版配置测试失败，自动使用备用版 v${SING_BOX_FALLBACK_VERSION}。" >&2
      SING_BOX_VERSION="$SING_BOX_FALLBACK_VERSION"; SING_BOX_VERSION_SOURCE="备用稳定版（最新版配置测试失败）"
      install_sing_box_version "$SING_BOX_VERSION" || fail "sing-box 备用版安装失败。"
      write_sing_config || fail "sing-box 备用版配置测试仍然失败。"
    else
      fail "sing-box 配置测试失败。"
    fi
  fi
  service_restart vvv-landing-sing-box
fi

CURRENT_STEP="验证 TCP/UDP 监听状态"
log "$CURRENT_STEP"
sleep 3
if ! verify_runtime; then
  journalctl -u vvv-landing-xray -u vvv-landing-sing-box --no-pager -n 100 2>/dev/null || true
  fail "代理服务未完整启动或监听端口不完整。"
fi

CURRENT_STEP="生成客户端配置"
log "$CURRENT_STEP"
generate_client_files

CURRENT_STEP="保存状态并安装 vps 查看命令"
log "$CURRENT_STEP"
save_state
install_shortcuts

if [ "$COMBINED_INSTALL" != 1 ]; then
  apt-get clean
  rm -rf /var/lib/apt/lists/*
fi

log "新加坡副机 VPS / 落地 VPS 安装成功"
echo "线路：${NODE_NAME}"
echo "协议模式：${PROTOCOL_MODE}"
echo "日本入口：${JAPAN_PUBLIC_IP}:${JAPAN_PORT}"
echo "落地监听：${REMOTE_PUBLIC_IP}:${REMOTE_PUBLIC_PORT}"
mode_has_vless && echo "Xray-core：v${XRAY_VERSION}（${XRAY_VERSION_SOURCE}）"
mode_has_hy2 && echo "sing-box：v${SING_BOX_VERSION}（${SING_BOX_VERSION_SOURCE}）"
echo "BBR：${BBR_STATUS} / 队列=${BBR_QDISC}"
echo "时区：Asia/Shanghai"
echo "每天北京时间 06:00 自动重启"
echo "以后输入 vps，可重新显示客户端配置和实时在线状态。"
echo "本次没有立即重启服务器；中转服务使用独立进程，不会重启自身直连代理。"
echo "请在 VPS 服务商安全组放行 TCP/UDP ${REMOTE_PUBLIC_PORT}；推荐仅允许日本主机 ${JAPAN_PUBLIC_IP}/32 访问。"
echo
cat "$CLIENT_NODES_FILE"

CURRENT_STEP="执行真实双向闭环检测"
run_probe_summary

trap - EXIT
cleanup
JP_RELAY_JPR3_LANDING_CORE_EOF
chmod 700 "$LANDING_CORE"
/bin/sh "$LANDING_CORE"
rc=$?
rm -f "$LANDING_CORE"
if [ "$rc" -ne 0 ]; then
  echo "落地安装程序退出码：$rc（当前 SSH 会话保持打开）。" >&2
fi
unset rc LANDING_CORE PAIRING_KEY

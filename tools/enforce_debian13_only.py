#!/usr/bin/env python3
import re
from pathlib import Path

ROOT = Path('.')


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: anchor count={count}')
    return text.replace(old, new, 1)


def replace_function(text: str, name: str, new_block: str) -> str:
    pattern = re.compile(rf'(?ms)^{re.escape(name)}\(\) \{{.*?^\}}\n')
    text, count = pattern.subn(lambda _m: new_block.rstrip() + '\n', text, count=1)
    if count != 1:
        raise SystemExit(f'{name}: function replacement count={count}')
    return text


def extract_systemd_branch(function_text: str) -> str:
    lines = function_text.splitlines()
    if len(lines) < 4 or lines[0].strip() != 'create_services() {':
        raise SystemExit('create_services function format changed')
    marker_index = next((i for i, line in enumerate(lines) if line.strip() == 'if [ "$OS_FAMILY" = "debian" ]; then'), None)
    if marker_index is None:
        raise SystemExit('create_services Debian branch marker missing')
    depth = 1
    captured = []
    for line in lines[marker_index + 1:]:
        stripped = line.strip()
        if stripped == 'else' and depth == 1:
            break
        captured.append(line[2:] if line.startswith('  ') else line)
        if re.match(r'^(if|case)\b.*(then|in)$', stripped):
            depth += 1
        elif stripped in ('fi', 'esac'):
            depth -= 1
    else:
        raise SystemExit('create_services outer else not found')
    return 'create_services() {\n' + '\n'.join(captured) + '\n}\n'


# Main host: only Debian 13.
host_path = ROOT / 'core-src/host.sh'
host = host_path.read_text(encoding='utf-8')
host = replace_once(
    host,
    '''  [[ "${ID:-}" == "debian" ]] || fail "日本脚本仅支持 Debian 12/13。当前系统：${PRETTY_NAME:-未知}"
  case "${VERSION_ID:-}" in
    12|13) ;;
    *) fail "日本脚本仅支持 Debian 12/13。当前版本：${VERSION_ID:-未知}" ;;
  esac''',
    '''  [[ "${ID:-}" == "debian" && "${VERSION_ID:-}" == "13" ]] || fail "主机脚本仅支持 Debian 13。当前系统：${PRETTY_NAME:-未知}"''',
    'host Debian version check',
)
host_path.write_text(host, encoding='utf-8')

# Landing: remove Debian 12, Alpine and OpenRC branches.
landing_path = ROOT / 'core-src/landing.sh'
landing = landing_path.read_text(encoding='utf-8')
landing = landing.replace('OS_FAMILY=""\nOS_VERSION=""\n', '')
landing = replace_function(landing, 'detect_os', r'''detect_os() {
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
}''')
landing = replace_function(landing, 'upgrade_system_once', r'''upgrade_system_once() {
  mkdir -p "$(dirname "$UPGRADE_MARKER")"
  export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
  retry 5 10 apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 -o DPkg::Lock::Timeout=120 -o Acquire::PDiffs=false update
  dpkg --configure -a >/dev/null 2>&1 || true
  retry 3 10 apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 -o DPkg::Lock::Timeout=120 install -y --no-install-recommends \
    ca-certificates curl unzip tar gzip openssl jq iproute2 procps \
    tzdata kmod qrencode util-linux python3
  update-ca-certificates >/dev/null 2>&1 || true
  echo "Debian 13 核心组件保持 VPS 镜像原版本，仅安装代理所需依赖。"
}''')
landing = replace_once(
    landing,
    '''  if [ "$OS_FAMILY" = "alpine" ] || [ "$IS_CONTAINER" -eq 1 ]; then
    echo "Alpine/受限容器不创建 Swap。"''',
    '''  if [ "$IS_CONTAINER" -eq 1 ]; then
    echo "受限容器不创建 Swap。"''',
    'landing swap compatibility branch',
)
landing = replace_function(landing, 'configure_timezone_and_daily_reboot', r'''configure_timezone_and_daily_reboot() {
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
}''')
landing = replace_function(landing, 'service_stop', r'''service_stop() {
  name="$1"
  systemctl stop "$name" >/dev/null 2>&1 || true
}''')
landing = replace_function(landing, 'service_restart', r'''service_restart() {
  name="$1"
  systemctl restart "$name"
}''')
landing = replace_function(landing, 'service_active', r'''service_active() {
  name="$1"
  systemctl is-active --quiet "$name"
}''')
create_match = re.search(r'(?ms)^create_services\(\) \{.*?^\}\n', landing)
if not create_match:
    raise SystemExit('create_services function not found')
landing = landing[:create_match.start()] + extract_systemd_branch(create_match.group(0)) + landing[create_match.end():]
landing = replace_once(
    landing,
    '''  if [ "$OS_FAMILY" = "debian" ]; then
    chown root:sing-box "$cert_path" "$key_path"
    runuser -u sing-box -- test -r "$cert_path" || fail "sing-box 用户无法读取落地 Hysteria 2 证书。"
    runuser -u sing-box -- test -r "$key_path" || fail "sing-box 用户无法读取落地 Hysteria 2 私钥。"
  fi''',
    '''  chown root:sing-box "$cert_path" "$key_path"
  runuser -u sing-box -- test -r "$cert_path" || fail "sing-box 用户无法读取落地 Hysteria 2 证书。"
  runuser -u sing-box -- test -r "$key_path" || fail "sing-box 用户无法读取落地 Hysteria 2 私钥。"''',
    'landing HY2 certificate ownership',
)
landing = replace_once(
    landing,
    '''  if [ "$OS_FAMILY" = "debian" ]; then
    install -o root -g xray -m 640 "$TMP_CFG" "$XRAY_CFG" || return 1
  else
    cp "$TMP_CFG" "$XRAY_CFG" || return 1
    chmod 600 "$XRAY_CFG" || return 1
  fi''',
    '''  install -o root -g xray -m 640 "$TMP_CFG" "$XRAY_CFG" || return 1''',
    'landing Xray config installation',
)
landing = replace_once(
    landing,
    '''  if [ "$OS_FAMILY" = "debian" ]; then
    install -o root -g sing-box -m 640 "$TMP_CFG" "$SING_CFG" || return 1
    runuser -u sing-box -- "$SING_BOX" check -c "$SING_CFG" || return 1
  else
    cp "$TMP_CFG" "$SING_CFG" || return 1
    chmod 600 "$SING_CFG" || return 1
  fi''',
    '''  install -o root -g sing-box -m 640 "$TMP_CFG" "$SING_CFG" || return 1
  runuser -u sing-box -- "$SING_BOX" check -c "$SING_CFG" || return 1''',
    'landing sing-box config installation',
)
landing = replace_once(
    landing,
    '''  [ "$OS_FAMILY" != "debian" ] || journalctl -u xray -u sing-box --no-pager -n 100 2>/dev/null || true
  [ "$OS_FAMILY" != "alpine" ] || tail -n 100 /var/log/jp-relay/*.log 2>/dev/null || true''',
    '''  journalctl -u xray -u sing-box --no-pager -n 100 2>/dev/null || true''',
    'landing runtime failure logs',
)
landing = replace_once(
    landing,
    '''if [ "$OS_FAMILY" = "debian" ]; then
  apt-get clean
  rm -rf /var/lib/apt/lists/*
fi''',
    '''apt-get clean
rm -rf /var/lib/apt/lists/*''',
    'landing apt cleanup',
)
for forbidden in ('OS_FAMILY', 'Alpine', 'alpine', 'apk ', 'rc-service', 'rc-update', 'OpenRC', 'openrc', '/etc/init.d'):
    if forbidden in landing:
        raise SystemExit(f'landing compatibility residue remains: {forbidden}')
landing_path.write_text(landing, encoding='utf-8')

# Unified bootstrap: reject unsupported systems before collecting parameters.
bootstrap_path = ROOT / 'core-src/bootstrap.sh'
bootstrap = bootstrap_path.read_text(encoding='utf-8')
marker = '[[ "$(id -u)" -eq 0 ]] || { echo "错误：请使用 root 用户运行。" >&2; exit 1; }\n'
check = r'''[[ "$(id -u)" -eq 0 ]] || { echo "错误：请使用 root 用户运行。" >&2; exit 1; }
[[ -r /etc/os-release ]] || { echo "错误：无法读取 /etc/os-release。" >&2; exit 1; }
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == debian && "${VERSION_ID:-}" == 13 ]] || { echo "错误：VVV 仅支持 Debian 13。当前系统：${PRETTY_NAME:-未知}" >&2; exit 1; }
command -v systemctl >/dev/null 2>&1 || { echo "错误：Debian 13 缺少 systemd。" >&2; exit 1; }
'''
bootstrap = replace_once(bootstrap, marker, check, 'bootstrap Debian 13 guard')
bootstrap_path.write_text(bootstrap, encoding='utf-8')

# Network installer: reject unsupported systems before apt/curl bootstrap.
installer_path = ROOT / 'vvv-install.sh'
installer = installer_path.read_text(encoding='utf-8')
marker = '[[ $(id -u) -eq 0 ]] || fail "请使用 root 用户运行。"\n'
check = r'''[[ $(id -u) -eq 0 ]] || fail "请使用 root 用户运行。"
[[ -r /etc/os-release ]] || fail "无法读取 /etc/os-release。"
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == debian && "${VERSION_ID:-}" == 13 ]] || fail "VVV 仅支持 Debian 13。当前系统：${PRETTY_NAME:-未知}"
'''
installer = replace_once(installer, marker, check, 'network installer Debian 13 guard')
installer_path.write_text(installer, encoding='utf-8')

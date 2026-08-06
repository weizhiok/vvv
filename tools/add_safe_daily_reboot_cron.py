#!/usr/bin/env python3
from pathlib import Path

path = Path('core-src/host.sh')
text = path.read_text(encoding='utf-8')

if '/etc/cron.d/vvv-daily-reboot' in text:
    raise SystemExit(0)

old_dependencies = '''    ca-certificates curl unzip tar gzip openssl jq python3 python3-venv iproute2 procps nftables \\
    tzdata kmod util-linux || fail "代理依赖安装失败。若提示锁被占用，已等待最多 10 秒，请稍后重新运行。"'''
new_dependencies = '''    ca-certificates curl unzip tar gzip openssl jq python3 python3-venv iproute2 procps nftables cron \\
    tzdata kmod util-linux || fail "代理依赖安装失败。若提示锁被占用，已等待最多 10 秒，请稍后重新运行。"'''
if old_dependencies not in text:
    raise SystemExit('dependency marker not found')
text = text.replace(old_dependencies, new_dependencies, 1)

old_timezone = '''configure_timezone() {
  [[ -f /usr/share/zoneinfo/Asia/Shanghai ]] || fail "Asia/Shanghai 时区文件不存在。"
  ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime
  echo 'Asia/Shanghai' > /etc/timezone
  timedatectl set-timezone Asia/Shanghai >/dev/null 2>&1 || true
  echo "时区：Asia/Shanghai"
  echo "自动重启：未安装"
  echo "当前时间：$(date '+%F %T %Z %z')"
}

'''
new_timezone = '''configure_timezone() {
  [[ -f /usr/share/zoneinfo/Asia/Shanghai ]] || fail "Asia/Shanghai 时区文件不存在。"
  ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime
  echo 'Asia/Shanghai' > /etc/timezone
  timedatectl set-timezone Asia/Shanghai >/dev/null 2>&1 || true
  echo "时区：Asia/Shanghai"
  echo "每天 06:00 自动重启：将在代理安装成功后配置"
  echo "当前时间：$(date '+%F %T %Z %z')"
}

install_daily_reboot_cron() {
  [[ -x /usr/sbin/cron ]] || fail "cron 未安装，无法配置每天 06:00 自动重启。"
  command -v flock >/dev/null 2>&1 || fail "系统缺少 flock，无法安全配置每天自动重启。"

  install -d -m700 /usr/local/lib/vvv /var/lib/vvv
  date -d 'tomorrow 00:00:00' +%s > /var/lib/vvv/daily-reboot-not-before
  chmod 600 /var/lib/vvv/daily-reboot-not-before

  cat > /usr/local/lib/vvv/daily-reboot.sh <<'EOF_DAILY_REBOOT'
#!/usr/bin/env bash
set -Eeuo pipefail

marker=/var/lib/vvv/daily-reboot-not-before
[[ -r "$marker" ]] || exit 0
read -r not_before < "$marker"
[[ "$not_before" =~ ^[0-9]+$ ]] || exit 0

now="$(date +%s)"
if (( now < not_before )); then
  logger -t vvv-daily-reboot "忽略安装当天的重启任务；首次最早从次日 06:00 执行。"
  exit 0
fi

if [[ "$(date '+%H:%M')" != "06:00" ]]; then
  logger -t vvv-daily-reboot "忽略非 06:00 触发的重启请求。"
  exit 0
fi

install -d -m755 /run/lock
exec 9>/run/lock/vvv-daily-reboot.lock
flock -n 9 || exit 0

logger -t vvv-daily-reboot "开始执行每天北京时间 06:00 自动重启。"
sync
sleep 2
exec /usr/bin/systemctl reboot --no-wall
EOF_DAILY_REBOOT
  chmod 700 /usr/local/lib/vvv/daily-reboot.sh

  cat > /etc/cron.d/vvv-daily-reboot <<'EOF_DAILY_REBOOT_CRON'
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 6 * * * root /usr/local/lib/vvv/daily-reboot.sh
EOF_DAILY_REBOOT_CRON
  chmod 644 /etc/cron.d/vvv-daily-reboot

  systemctl enable cron.service >/dev/null
  systemctl restart cron.service
  systemctl is-active --quiet cron.service || fail "cron 服务未运行，无法保证每天 06:00 自动重启。"
  echo "每天北京时间 06:00 自动重启：已启用（cron，首次最早为明天）"
}

'''
if old_timezone not in text:
    raise SystemExit('timezone function marker not found')
text = text.replace(old_timezone, new_timezone, 1)

old_bootstrap = '''  CURRENT_STEP="生成日本直连节点"; log "$CURRENT_STEP"; generate_direct_client_files

  apt-get clean'''
new_bootstrap = '''  CURRENT_STEP="生成日本直连节点"; log "$CURRENT_STEP"; generate_direct_client_files
  CURRENT_STEP="安装每天 06:00 自动重启任务"; log "$CURRENT_STEP"; install_daily_reboot_cron

  apt-get clean'''
if old_bootstrap not in text:
    raise SystemExit('bootstrap insertion marker not found')
text = text.replace(old_bootstrap, new_bootstrap, 1)

old_summary = '  echo "自动重启：未安装（避免安装完成后整机重启或断开 SSH）"\n'
new_summary = '  echo "每天北京时间 06:00 自动重启：cron（首次最早为明天）"\n'
if old_summary not in text:
    raise SystemExit('summary marker not found')
text = text.replace(old_summary, new_summary, 1)

for forbidden in ('daily-reboot.timer', 'daily-reboot.service', 'OnCalendar='):
    if forbidden in text:
        raise SystemExit(f'unsafe systemd daily reboot path remains: {forbidden}')

path.write_text(text, encoding='utf-8')

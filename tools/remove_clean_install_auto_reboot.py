#!/usr/bin/env python3
from pathlib import Path

path = Path('core-src/host.sh')
text = path.read_text(encoding='utf-8')

start = text.index('prepare_timezone_and_daily_reboot() {')
end = text.index('prompt_initial_mode_and_port() {', start)
replacement = '''configure_timezone() {
  [[ -f /usr/share/zoneinfo/Asia/Shanghai ]] || fail "Asia/Shanghai 时区文件不存在。"
  ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime
  echo 'Asia/Shanghai' > /etc/timezone
  timedatectl set-timezone Asia/Shanghai >/dev/null 2>&1 || true
  echo "时区：Asia/Shanghai"
  echo "自动重启：未安装"
  echo "当前时间：$(date '+%F %T %Z %z')"
}

'''
text = text[:start] + replacement + text[end:]

old = '  CURRENT_STEP="设置上海时区并锁定安装期间禁止重启"; log "$CURRENT_STEP"; prepare_timezone_and_daily_reboot\n'
new = '  CURRENT_STEP="设置上海时区"; log "$CURRENT_STEP"; configure_timezone\n'
if old not in text:
    raise SystemExit('timezone bootstrap call marker not found')
text = text.replace(old, new, 1)

old = '  CURRENT_STEP="启用每天 06:00 自动重启"; log "$CURRENT_STEP"; activate_daily_reboot_timer\n'
if old not in text:
    raise SystemExit('daily reboot activation marker not found')
text = text.replace(old, '', 1)

old = '  systemctl is-active --quiet daily-reboot.timer 2>/dev/null && echo "每天北京时间 06:00 自动重启" || echo "自动重启：当前环境未启用"\n'
new = '  echo "自动重启：未安装（避免安装完成后整机重启或断开 SSH）"\n'
if old not in text:
    raise SystemExit('daily reboot summary marker not found')
text = text.replace(old, new, 1)

for forbidden in (
    'daily-reboot.timer', 'daily-reboot.service', 'daily-reboot-guard.sh',
    'daily-reboot-not-before', 'systemctl reboot', '/sbin/reboot',
    'shutdown -r', 'poweroff',
):
    if forbidden in text:
        raise SystemExit(f'forbidden reboot path remains: {forbidden}')

path.write_text(text, encoding='utf-8')

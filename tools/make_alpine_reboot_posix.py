#!/usr/bin/env python3
from pathlib import Path

host_path = Path('core-src/host.sh')
host = host_path.read_text(encoding='utf-8')
start = "  cat > /usr/local/lib/vvv/daily-reboot.sh <<'EOF_DAILY_REBOOT'\n"
end = "EOF_DAILY_REBOOT\n  chmod 700 /usr/local/lib/vvv/daily-reboot.sh\n"
left = host.find(start)
right = host.find(end, left + len(start)) if left >= 0 else -1
if left < 0 or right < 0:
    raise SystemExit('daily reboot heredoc marker not found')
right += len(end)
helper = r'''  cat > /usr/local/lib/vvv/daily-reboot.sh <<'EOF_DAILY_REBOOT'
#!/bin/sh
set -eu

marker=/var/lib/vvv/daily-reboot-install-day
[ -r "$marker" ] || exit 0
IFS= read -r install_day < "$marker" || exit 0
current_day="$(date '+%Y%m%d')"

case "$install_day:$current_day" in
  *[!0-9:]*|????????:????????) ;;
  *) exit 0 ;;
esac

if [ "$current_day" -le "$install_day" ]; then
  command -v logger >/dev/null 2>&1 && logger -t vvv-daily-reboot "忽略安装当天的重启任务；首次最早从次日 06:00 执行。"
  exit 0
fi

if [ "$(date '+%H:%M')" != "06:00" ]; then
  command -v logger >/dev/null 2>&1 && logger -t vvv-daily-reboot "忽略非 06:00 触发的重启请求。"
  exit 0
fi

install -d -m755 /run/lock
lock_dir=/run/lock/vvv-daily-reboot.lock
mkdir "$lock_dir" 2>/dev/null || exit 0
trap 'rmdir "$lock_dir" 2>/dev/null || true' EXIT HUP INT TERM

command -v logger >/dev/null 2>&1 && logger -t vvv-daily-reboot "开始执行每天北京时间 06:00 自动重启。"
sync
sleep 2

if command -v systemctl >/dev/null 2>&1 && [ "$(cat /proc/1/comm 2>/dev/null | tr -d '[:space:]')" = systemd ]; then
  exec systemctl reboot --no-wall
elif command -v reboot >/dev/null 2>&1; then
  exec reboot
else
  command -v logger >/dev/null 2>&1 && logger -t vvv-daily-reboot "找不到可用的系统重启命令。"
  exit 1
fi
EOF_DAILY_REBOOT
  chmod 700 /usr/local/lib/vvv/daily-reboot.sh
'''
host_path.write_text(host[:left] + helper + host[right:], encoding='utf-8')

test_path = Path('tests/test_install_reboot_guard.py')
test = test_path.read_text(encoding='utf-8')
test = test.replace(
    "require('daily-reboot-install-day' in daily_reboot and\n        '10#$current_day <= 10#$install_day' in daily_reboot,\n        '每日重启脚本缺少跨 Debian/Alpine 的次日门槛')\n",
    "require('daily-reboot-install-day' in daily_reboot and\n        '[ \"$current_day\" -le \"$install_day\" ]' in daily_reboot,\n        '每日重启脚本缺少跨 Debian/Alpine 的次日门槛')\n"
)
marker = "require('mkdir \"$lock_dir\"' in daily_reboot and 'flock' not in daily_reboot,\n        '每日重启没有使用 Debian/Alpine 通用的原子目录锁')\n"
addition = marker + "require('#!/bin/sh' in daily_reboot and '#!/usr/bin/env bash' not in daily_reboot,\n        'Alpine 重启助手没有使用 POSIX /bin/sh')\nrequire('[[' not in daily_reboot and '((' not in daily_reboot,\n        'Alpine 重启助手仍包含 Bash 专用语法')\n"
if marker not in test:
    raise SystemExit('reboot test marker not found')
test = test.replace(marker, addition, 1)
test_path.write_text(test, encoding='utf-8')

print('Alpine daily reboot helper now uses POSIX sh.')

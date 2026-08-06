#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = (ROOT / 'core-src/host.sh').read_text(encoding='utf-8')
HOP = (ROOT / 'core-src/hy2_port_hop.sh').read_text(encoding='utf-8')


def require(value, message):
    if not value:
        raise AssertionError(message)


def between(text, start, end):
    require(start in text and end in text, f'缺少代码标记：{start} / {end}')
    return text.split(start, 1)[1].split(end, 1)[0]


configure_timezone = between(HOST, 'configure_timezone() {', 'install_daily_reboot_cron() {')
daily_reboot = between(HOST, 'install_daily_reboot_cron() {', 'prompt_initial_mode_and_port() {')
daily_reboot_helper = daily_reboot.split("cat > /usr/local/lib/vvv/daily-reboot.sh <<'EOF_DAILY_REBOOT'", 1)[1].split('EOF_DAILY_REBOOT', 1)[0]
create_xray = between(HOST, 'create_xray_service() {', 'create_sing_box_service() {')
create_sing = between(HOST, 'create_sing_box_service() {', 'parse_x25519_keys() {')
initial = between(HOST, 'activate_initial_state() {', 'update_state_core_versions() {')
hop_install = between(HOP, 'install_service(){', 'show_status(){')

for forbidden in (
    'daily-reboot.timer', 'daily-reboot.service', 'OnCalendar=',
    'systemctl enable --now daily-reboot',
):
    require(forbidden not in HOST, f'仍包含会在安装后异常触发的 systemd 重启路径：{forbidden}')

require('timedatectl set-timezone Asia/Shanghai' in configure_timezone,
        '全新安装没有设置 Asia/Shanghai 时区')
require('systemctl enable' not in configure_timezone and 'systemctl start' not in configure_timezone,
        '时区设置阶段不应启用任何服务')
require('cron' in between(HOST, 'upgrade_system_once() {', 'configure_swap() {'),
        '安装依赖没有包含 cron')

require('/etc/cron.d/vvv-daily-reboot' in daily_reboot,
        '缺少 Debian VVV 专用 cron 文件')
require('0 6 * * * root /usr/local/lib/vvv/daily-reboot.sh' in daily_reboot,
        'Debian cron 不是每天北京时间 06:00 执行')
require('/etc/alpine-release' in daily_reboot,
        '每日重启模块没有识别 Alpine')
require('/etc/crontabs/root' in daily_reboot and
        '0 6 * * * /usr/local/lib/vvv/daily-reboot.sh' in daily_reboot,
        'Alpine root crontab 不是每天北京时间 06:00 执行')
require('rc-update add crond default' in daily_reboot and
        'rc-service crond restart' in daily_reboot and 'rc-service crond status' in daily_reboot,
        'Alpine crond 没有通过 OpenRC 启用、刷新和验证')
require("date -d 'tomorrow" not in daily_reboot,
        '每日重启仍依赖 GNU date -d tomorrow')
require('daily-reboot-install-day' in daily_reboot and
        '[ "$current_day" -le "$install_day" ]' in daily_reboot,
        '每日重启脚本缺少跨 Debian/Alpine 的次日门槛')
require("date '+%H:%M'" in daily_reboot and '06:00' in daily_reboot,
        '每日重启脚本缺少执行时刻二次校验')
require('mkdir "$lock_dir"' in daily_reboot and 'flock' not in daily_reboot,
        '每日重启没有使用 Debian/Alpine 通用的原子目录锁')
require('#!/bin/sh' in daily_reboot_helper and '#!/usr/bin/env bash' not in daily_reboot_helper,
        'Alpine 重启助手没有使用 POSIX /bin/sh')
require('[[' not in daily_reboot_helper and '((' not in daily_reboot_helper,
        'Alpine 重启助手仍包含 Bash 专用语法')
require('systemctl reboot --no-wall' in daily_reboot and 'command -v reboot' in daily_reboot,
        '每日重启脚本没有同时覆盖 systemd 与 Alpine reboot')
require('systemctl enable cron.service' in daily_reboot and
        'systemctl restart cron.service' in daily_reboot and
        'systemctl is-active --quiet cron.service' in daily_reboot,
        'Debian cron 服务没有在安装完成后启用、刷新和验证')

require('systemctl daemon-reload' not in create_xray and 'systemctl enable xray' not in create_xray,
        'Xray 服务定义阶段仍然操作 systemd 运行状态')
require('systemctl daemon-reload' not in create_sing and 'systemctl enable sing-box' not in create_sing,
        'sing-box 服务定义阶段仍然操作 systemd 运行状态')
require('systemctl daemon-reload' not in hop_install and 'systemctl enable' not in hop_install,
        '端口跳跃服务定义阶段仍然操作 systemd 运行状态')
require('systemctl enable xray.service' in initial,
        'Xray 没有在配置验证阶段启用')
require('systemctl enable vvv-hy2-port-hop.service sing-box.service' in initial,
        'HY2 服务没有在配置验证阶段统一启用')

timezone_call = HOST.index('configure_timezone', HOST.index('CURRENT_STEP='))
core_install = HOST.index('CURRENT_STEP="安装 Xray 最新稳定版"')
client_generation = HOST.index('CURRENT_STEP="生成日本直连节点"')
cron_install = HOST.index('install_daily_reboot_cron', client_generation)
require(timezone_call < core_install < client_generation < cron_install,
        '时区、代理、客户端配置和 cron 的安装顺序不安全')
require('每天北京时间 06:00 自动重启：cron' in HOST,
        '安装结果没有显示每日重启状态')

print('Safe daily 06:00 cron reboot tests passed.')

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
        '缺少 VVV 专用 cron 文件')
require('0 6 * * * root /usr/local/lib/vvv/daily-reboot.sh' in daily_reboot,
        'cron 不是每天北京时间 06:00 执行')
require("date -d 'tomorrow 00:00:00'" in daily_reboot,
        '没有限制首次重启最早为安装次日')
require('daily-reboot-not-before' in daily_reboot and 'now < not_before' in daily_reboot,
        '每日重启脚本缺少次日门槛')
require("date '+%H:%M'" in daily_reboot and '06:00' in daily_reboot,
        '每日重启脚本缺少执行时刻二次校验')
require('flock -n 9' in daily_reboot,
        '每日重启脚本缺少并发锁')
require('exec /usr/bin/systemctl reboot --no-wall' in daily_reboot,
        '每日重启脚本没有使用明确的 systemctl reboot')
require('systemctl enable cron.service' in daily_reboot and 'systemctl restart cron.service' in daily_reboot,
        'cron 服务没有在安装完成后启用并刷新')
require('systemctl is-active --quiet cron.service' in daily_reboot,
        '安装后没有验证 cron 服务状态')

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

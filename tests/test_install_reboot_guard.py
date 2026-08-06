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


configure_timezone = between(HOST, 'configure_timezone() {', 'prompt_initial_mode_and_port() {')
create_xray = between(HOST, 'create_xray_service() {', 'create_sing_box_service() {')
create_sing = between(HOST, 'create_sing_box_service() {', 'parse_x25519_keys() {')
initial = between(HOST, 'activate_initial_state() {', 'update_state_core_versions() {')
hop_install = between(HOP, 'install_service(){', 'show_status(){')

for forbidden in (
    'daily-reboot.timer', 'daily-reboot.service', 'daily-reboot-guard.sh',
    'daily-reboot-not-before', 'systemctl reboot', '/sbin/reboot',
    'shutdown -r', 'poweroff',
):
    require(forbidden not in HOST, f'全新安装仍包含整机重启路径：{forbidden}')

require('timedatectl set-timezone Asia/Shanghai' in configure_timezone,
        '全新安装没有设置 Asia/Shanghai 时区')
require('systemctl enable' not in configure_timezone and 'systemctl start' not in configure_timezone,
        '时区设置阶段不应启用任何 systemd 服务')
require('自动重启：未安装' in HOST,
        '安装结果没有明确说明自动重启未安装')

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
require(timezone_call < core_install < client_generation,
        '时区设置与代理安装顺序异常')
require('CURRENT_STEP="启用每天 06:00 自动重启"' not in HOST,
        '安装末尾仍然启用整机重启定时器')

print('Clean-install no-reboot tests passed.')

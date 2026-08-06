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


prepare = between(HOST, 'prepare_timezone_and_daily_reboot() {', 'activate_daily_reboot_timer() {')
activate = between(HOST, 'activate_daily_reboot_timer() {', 'prompt_initial_mode_and_port() {')
create_xray = between(HOST, 'create_xray_service() {', 'create_sing_box_service() {')
create_sing = between(HOST, 'create_sing_box_service() {', 'parse_x25519_keys() {')
initial = between(HOST, 'activate_initial_state() {', 'update_state_core_versions() {')
hop_install = between(HOP, 'install_service(){', 'show_status(){')

require('disable --now daily-reboot.timer daily-reboot.service' in prepare,
        '安装开始前没有停止并禁用重启任务')
require("date -d 'tomorrow 00:00:00'" in prepare,
        '每日重启没有设置明天才允许执行的硬保护')
require('daily-reboot-guard.sh' in prepare and 'now < not_before' in prepare,
        '每日重启服务缺少执行时保护')
require('enable --now daily-reboot.timer' not in prepare,
        '安装中段仍然启用了每日重启定时器')
require('enable --now daily-reboot.timer' in activate,
        '安装完成后没有启用每日重启定时器')
require('daily-reboot.service' in activate and '异常运行' in activate,
        '启用定时器后没有检查重启服务是否异常启动')

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

prepare_call = HOST.index('prepare_timezone_and_daily_reboot', HOST.index('CURRENT_STEP='))
core_install = HOST.index('CURRENT_STEP="安装 Xray 最新稳定版"')
client_generation = HOST.index('CURRENT_STEP="生成日本直连节点"')
activate_call = HOST.index('activate_daily_reboot_timer', client_generation)
require(prepare_call < core_install < client_generation < activate_call,
        '每日重启准备/启用顺序不安全')

print('Install-time reboot guard tests passed.')

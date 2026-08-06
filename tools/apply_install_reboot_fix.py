#!/usr/bin/env python3
from pathlib import Path
import re

host_path = Path('core-src/host.sh')
host = host_path.read_text(encoding='utf-8')

replacement = r'''prepare_timezone_and_daily_reboot() {
  [[ -f /usr/share/zoneinfo/Asia/Shanghai ]] || fail "Asia/Shanghai 时区文件不存在。"
  ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime
  echo 'Asia/Shanghai' > /etc/timezone
  timedatectl set-timezone Asia/Shanghai >/dev/null 2>&1 || true

  # 安装尚未完成时，禁止任何旧定时器或新定时器触发整机重启。
  systemctl disable --now daily-reboot.timer daily-reboot.service >/dev/null 2>&1 || true
  install -d -m700 /var/lib/vvv /usr/local/lib/vvv
  date -d 'tomorrow 00:00:00' +%s > /var/lib/vvv/daily-reboot-not-before
  chmod 600 /var/lib/vvv/daily-reboot-not-before

  cat > /usr/local/lib/vvv/daily-reboot-guard.sh <<'EOF_REBOOT_GUARD'
#!/usr/bin/env bash
set -Eeuo pipefail
marker=/var/lib/vvv/daily-reboot-not-before
[[ -r "$marker" ]] || exit 0
read -r not_before < "$marker"
[[ "$not_before" =~ ^[0-9]+$ ]] || exit 0
now="$(date +%s)"
if (( now < not_before )); then
  logger -t vvv-daily-reboot "忽略安装当天或异常提前触发的重启任务；最早允许时间：${not_before}。"
  exit 0
fi
exec /usr/bin/systemctl reboot
EOF_REBOOT_GUARD
  chmod 700 /usr/local/lib/vvv/daily-reboot-guard.sh

  cat > /etc/systemd/system/daily-reboot.service <<'EOF_REBOOT_SERVICE'
[Unit]
Description=Daily reboot at 06:00 Asia/Shanghai

[Service]
Type=oneshot
ExecStart=/usr/local/lib/vvv/daily-reboot-guard.sh
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
  systemctl daemon-reload
  echo "时区：Asia/Shanghai"
  echo "安装期间自动重启：已禁止"
  echo "当前时间：$(date '+%F %T %Z %z')"
}

activate_daily_reboot_timer() {
  # 只有代理、客户端配置和管理命令全部安装成功后，才允许启用定时器。
  systemctl daemon-reload
  systemctl enable --now daily-reboot.timer >/dev/null
  systemctl is-active --quiet daily-reboot.timer || fail "每天 06:00 自动重启定时器未运行。"
  systemctl is-active --quiet daily-reboot.service && fail "检测到重启服务在安装完成时异常运行。"
  echo "每日自动重启：北京时间 06:00（首次最早为明天）"
}

prompt_initial_mode_and_port() {'''
pattern = r'configure_timezone_and_daily_reboot\(\) \{\n.*?\n\}\n\nprompt_initial_mode_and_port\(\) \{'
host, count = re.subn(pattern, replacement, host, count=1, flags=re.S)
if count != 1:
    raise SystemExit('timezone function replacement failed')

replacements = [
    (
        "EOF_XRAY_SERVICE\n  systemctl daemon-reload\n  systemctl enable xray >/dev/null\n}",
        "EOF_XRAY_SERVICE\n}",
    ),
    (
        "  systemctl daemon-reload\n  systemctl enable sing-box >/dev/null\n}\n\nparse_x25519_keys()",
        "}\n\nparse_x25519_keys()",
    ),
    (
        "    systemctl daemon-reload\n    systemctl restart xray || return 1",
        "    systemctl daemon-reload\n    systemctl enable xray.service >/dev/null || return 1\n    systemctl restart xray || return 1",
    ),
    (
        "    systemctl daemon-reload\n    systemctl restart vvv-hy2-port-hop.service || return 1",
        "    systemctl daemon-reload\n    systemctl enable vvv-hy2-port-hop.service sing-box.service >/dev/null || return 1\n    systemctl restart vvv-hy2-port-hop.service || return 1",
    ),
    (
        '  CURRENT_STEP="设置上海时区和每天 06:00 自动重启"; log "$CURRENT_STEP"; configure_timezone_and_daily_reboot',
        '  CURRENT_STEP="设置上海时区并锁定安装期间禁止重启"; log "$CURRENT_STEP"; prepare_timezone_and_daily_reboot',
    ),
    (
        '  CURRENT_STEP="生成日本直连节点"; log "$CURRENT_STEP"; generate_direct_client_files\n\n  apt-get clean',
        '  CURRENT_STEP="生成日本直连节点"; log "$CURRENT_STEP"; generate_direct_client_files\n  CURRENT_STEP="启用每天 06:00 自动重启"; log "$CURRENT_STEP"; activate_daily_reboot_timer\n\n  apt-get clean',
    ),
]
for old, new in replacements:
    if old not in host:
        raise SystemExit('host marker not found: ' + old[:80])
    host = host.replace(old, new, 1)
host_path.write_text(host, encoding='utf-8')

hop_path = Path('core-src/hy2_port_hop.sh')
hop = hop_path.read_text(encoding='utf-8')
old = "  systemctl daemon-reload\n  systemctl enable vvv-hy2-port-hop.service >/dev/null\n}\n\nshow_status()"
new = "}\n\nshow_status()"
if old not in hop:
    raise SystemExit('hy2 install_service activation marker not found')
hop_path.write_text(hop.replace(old, new, 1), encoding='utf-8')

test = '''#!/usr/bin/env python3
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
'''
Path('tests/test_install_reboot_guard.py').write_text(test, encoding='utf-8')

validate_path = Path('.github/workflows/validate.yml')
validate = validate_path.read_text(encoding='utf-8')
marker = '          python3 tests/test_hy2_port_hopping.py\n'
addition = marker + '          python3 tests/test_install_reboot_guard.py\n'
if addition not in validate:
    if marker not in validate:
        raise SystemExit('validate workflow marker not found')
    validate = validate.replace(marker, addition, 1)
validate_path.write_text(validate, encoding='utf-8')

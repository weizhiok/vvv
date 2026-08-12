#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path, old, new, label):
    text = path.read_text(encoding='utf-8')
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{path}: expected exactly one {label} anchor, found {count}')
    path.write_text(text.replace(old, new, 1), encoding='utf-8')


def replace_all_checked(path, old, new, expected, label):
    text = path.read_text(encoding='utf-8')
    count = text.count(old)
    if count != expected:
        raise SystemExit(f'{path}: expected {expected} {label} anchors, found {count}')
    path.write_text(text.replace(old, new), encoding='utf-8')


installer = ROOT / 'vvv-install.sh'
text = installer.read_text(encoding='utf-8')
text = text.replace('curl -fsSL ', 'curl -4fsSL ')
old_files = 'files=(host.sh landing.sh center_install.sh register_sync.sh vvv_manager.sh sub_center.py sync_agent.py backup_manager.py rclone_manager.sh client_adapters.py client_package_renderer.py name_guard_runtime.py name_guard_installer.py debian_compat.py adapter_manager.py client_upgrade_engine.py client_local_renderer.py hy2_port_hop.py hy2_port_hop.sh center_transport.sh center_manager.sh restore_manager.py diagnostic_report.py node_probe.py)'
new_files = 'files=(host.sh landing.sh ipv4_only.sh center_install.sh register_sync.sh vvv_manager.sh sub_center.py sync_agent.py backup_manager.py rclone_manager.sh client_adapters.py client_package_renderer.py name_guard_runtime.py name_guard_installer.py debian_compat.py adapter_manager.py client_upgrade_engine.py client_local_renderer.py hy2_port_hop.py hy2_port_hop.sh center_transport.sh center_manager.sh restore_manager.py diagnostic_report.py node_probe.py)'
if text.count(old_files) != 1:
    raise SystemExit('vvv-install.sh: source list anchor mismatch')
text = text.replace(old_files, new_files, 1)
old = 'sh -n "$TMP/app/landing.sh" || fail "landing.sh 语法检查失败。"\npython3 -m py_compile'
new = 'sh -n "$TMP/app/landing.sh" || fail "landing.sh 语法检查失败。"\nsh -n "$TMP/app/ipv4_only.sh" || fail "ipv4_only.sh 语法检查失败。"\n# 在任何角色安装前先从服务器源头关闭 IPv6；之后 bootstrap/各角色还会幂等复核。\n. "$TMP/app/ipv4_only.sh"\nvvv_enforce_ipv4_only || fail "无法强制启用 IPv4-only。"\npython3 -m py_compile'
if text.count(old) != 1:
    raise SystemExit('vvv-install.sh: IPv4 enforcement insertion anchor mismatch')
text = text.replace(old, new, 1)
installer.write_text(text, encoding='utf-8')

bootstrap = ROOT / 'core-src/bootstrap.sh'
replace_once(
    bootstrap,
    'fail(){ echo "错误：$*" >&2; exit 1; }\n\njson_value() {',
    'fail(){ echo "错误：$*" >&2; exit 1; }\n\nIPV4_ONLY_MODULE="$BASE_DIR/ipv4_only.sh"\n[[ -r "$IPV4_ONLY_MODULE" ]] || fail "缺少 IPv4-only 系统模块。"\n# shellcheck disable=SC1090\nsource "$IPV4_ONLY_MODULE"\nvvv_enforce_ipv4_only || fail "无法强制启用 IPv4-only。"\n\njson_value() {',
    'bootstrap IPv4 source',
)

host = ROOT / 'core-src/host.sh'
replace_once(
    host,
    'for module in client_adapters.py client_package_renderer.py hy2_port_hop.py hy2_port_hop.sh; do',
    'for module in client_adapters.py client_package_renderer.py hy2_port_hop.py hy2_port_hop.sh ipv4_only.sh; do',
    'host module list',
)
replace_once(
    host,
    'done\n\nmkdir -p /usr/local/sbin\ncat > /usr/local/sbin/jp-relay-manager',
    'done\n# shellcheck disable=SC1090\nsource "$HOST_SOURCE_DIR/ipv4_only.sh"\n\nmkdir -p /usr/local/sbin\ncat > /usr/local/sbin/jp-relay-manager',
    'host IPv4 module source',
)
replace_once(
    host,
    '  CURRENT_STEP="检查 Debian 系统"; log "$CURRENT_STEP"; check_debian\n\n  if [[ ! -f "$STATE_FILE" ]]; then',
    '  CURRENT_STEP="检查 Debian 系统"; log "$CURRENT_STEP"; check_debian\n  CURRENT_STEP="强制 IPv4-only"; log "$CURRENT_STEP"; vvv_enforce_ipv4_only || fail "无法强制启用 IPv4-only。"\n\n  if [[ ! -f "$STATE_FILE" ]]; then',
    'host bootstrap IPv4 call',
)

landing = ROOT / 'core-src/landing.sh'
replace_once(
    landing,
    'umask 077\n\n# ============================================================\nPAIRING_KEY=',
    'umask 077\nLANDING_SOURCE_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"\n[[ -f "$LANDING_SOURCE_DIR/ipv4_only.sh" ]] || { echo "错误：缺少 IPv4-only 系统模块。" >&2; exit 1; }\ninstall -d -m700 /usr/local/lib/vvv\ninstall -m755 "$LANDING_SOURCE_DIR/ipv4_only.sh" /usr/local/lib/vvv/ipv4_only.sh\n\n# ============================================================\nPAIRING_KEY=',
    'landing outer IPv4 module install',
)
replace_once(
    landing,
    '#!/bin/sh\nset -eu\numask 077\n\nPAIRING_KEY=',
    '#!/bin/sh\nset -eu\numask 077\n. /usr/local/lib/vvv/ipv4_only.sh\n\nPAIRING_KEY=',
    'landing inner IPv4 source',
)
replace_once(
    landing,
    'CURRENT_STEP="检查操作系统"\nlog "$CURRENT_STEP"\ndetect_os\n\nCURRENT_STEP="解析并验证 JPR3 对接密钥"',
    'CURRENT_STEP="检查操作系统"\nlog "$CURRENT_STEP"\ndetect_os\n\nCURRENT_STEP="强制 IPv4-only"\nlog "$CURRENT_STEP"\nvvv_enforce_ipv4_only || fail "无法强制启用 IPv4-only。"\n\nCURRENT_STEP="解析并验证 JPR3 对接密钥"',
    'landing IPv4 call',
)

center = ROOT / 'core-src/center_install.sh'
replace_once(
    center,
    'fail(){ echo "错误：$*" >&2; exit 1; }\nsection(){',
    'fail(){ echo "错误：$*" >&2; exit 1; }\nIPV4_ONLY_MODULE="$BASE_DIR/ipv4_only.sh"\n[[ -r "$IPV4_ONLY_MODULE" ]] || fail "缺少 IPv4-only 系统模块。"\n# shellcheck disable=SC1090\nsource "$IPV4_ONLY_MODULE"\nsection(){',
    'center IPv4 source',
)
replace_once(
    center,
    '[[ $(id -u) -eq 0 ]] || fail "请使用 root 用户运行。"\npublic_ip=',
    '[[ $(id -u) -eq 0 ]] || fail "请使用 root 用户运行。"\nvvv_enforce_ipv4_only || fail "无法强制启用 IPv4-only。"\npublic_ip=',
    'center IPv4 call',
)
replace_once(
    center,
    'for file in sub_center.py sync_agent.py backup_manager.py rclone_manager.sh client_adapters.py adapter_manager.py center_transport.sh center_manager.sh restore_manager.py diagnostic_report.py node_probe.py; do',
    'for file in sub_center.py sync_agent.py backup_manager.py rclone_manager.sh client_adapters.py adapter_manager.py center_transport.sh center_manager.sh restore_manager.py diagnostic_report.py node_probe.py ipv4_only.sh; do',
    'center runtime module copy',
)

transport = ROOT / 'core-src/center_transport.sh'
replace_all_checked(
    transport,
    ':${port} {\n  log {',
    ':${port} {\n  bind 0.0.0.0\n  log {',
    2,
    'HTTP Caddy IPv4 bind',
)
replace_once(
    transport,
    '${domain}:${port} {\n  tls {',
    '${domain}:${port} {\n  bind 0.0.0.0\n  tls {',
    'domain HTTPS Caddy IPv4 bind',
)
replace_all_checked(
    transport,
    ':80 {\n  root * ${ACME_WEBROOT}',
    ':80 {\n  bind 0.0.0.0\n  root * ${ACME_WEBROOT}',
    2,
    'port 80 Caddy IPv4 bind',
)
replace_once(
    transport,
    'https://${ip}:${port} {\n  tls ',
    'https://${ip}:${port} {\n  bind 0.0.0.0\n  tls ',
    'IP HTTPS Caddy IPv4 bind',
)
replace_once(
    transport,
    'exec /usr/local/bin/cloudflared tunnel --no-autoupdate run --token "$token"',
    'exec /usr/local/bin/cloudflared tunnel --no-autoupdate --edge-ip-version 4 run --token "$token"',
    'cloudflared IPv4 edge',
)

hop = ROOT / 'core-src/hy2_port_hop.py'
replace_once(hop, 'TABLE_FAMILY = "inet"', 'TABLE_FAMILY = "ip"', 'HY2 nft family')

print('IPv4-only source patch applied')

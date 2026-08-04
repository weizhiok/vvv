#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path, old, new):
    target = ROOT / path
    text = target.read_text(encoding='utf-8')
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{path}: expected one match, found {count}: {old[:120]!r}')
    target.write_text(text.replace(old, new, 1), encoding='utf-8')


replace_once(
    'vvv-install.sh',
    'files=(host.sh landing.sh center_install.sh register_sync.sh vvv_manager.sh sub_center.py sync_agent.py backup_manager.py rclone_manager.sh client_adapters.py adapter_manager.py client_upgrade_engine.py client_local_renderer.py center_transport.sh center_manager.sh restore_manager.py diagnostic_report.py node_probe.py)',
    'files=(host.sh landing.sh center_install.sh register_sync.sh vvv_manager.sh sub_center.py sync_agent.py backup_manager.py rclone_manager.sh client_adapters.py client_package_renderer.py adapter_manager.py client_upgrade_engine.py client_local_renderer.py hy2_port_hop.py hy2_port_hop.sh center_transport.sh center_manager.sh restore_manager.py diagnostic_report.py node_probe.py)',
)
replace_once(
    'vvv-install.sh',
    'for file in bootstrap.sh center_install.sh register_sync.sh vvv_manager.sh rclone_manager.sh center_transport.sh center_manager.sh host.sh; do',
    'for file in bootstrap.sh center_install.sh register_sync.sh vvv_manager.sh rclone_manager.sh center_transport.sh center_manager.sh host.sh hy2_port_hop.sh; do',
)
replace_once(
    'vvv-install.sh',
    'python3 -m py_compile "$TMP/app/sub_center.py" "$TMP/app/sync_agent.py" "$TMP/app/backup_manager.py" "$TMP/app/client_adapters.py" "$TMP/app/adapter_manager.py" "$TMP/app/client_upgrade_engine.py" "$TMP/app/client_local_renderer.py" "$TMP/app/restore_manager.py" "$TMP/app/diagnostic_report.py" "$TMP/app/node_probe.py" || fail "Python 模块语法检查失败。"',
    'python3 -m py_compile "$TMP/app/sub_center.py" "$TMP/app/sync_agent.py" "$TMP/app/backup_manager.py" "$TMP/app/client_adapters.py" "$TMP/app/client_package_renderer.py" "$TMP/app/adapter_manager.py" "$TMP/app/client_upgrade_engine.py" "$TMP/app/client_local_renderer.py" "$TMP/app/hy2_port_hop.py" "$TMP/app/restore_manager.py" "$TMP/app/diagnostic_report.py" "$TMP/app/node_probe.py" || fail "Python 模块语法检查失败。"',
)

replace_once(
    'core-src/bootstrap.sh',
    '        VVV_HY2_PORTS="$(jq -r \'.ports\' <<<"$result")"',
    '        VVV_HY2_PORTS="$(python3 -c \'import json,sys; print(json.load(sys.stdin)["ports"])\' <<<"$result")"',
)

replace_once(
    'core-src/host.sh',
    '''    HY2_PORTS="$(jq -r '.ports' <<<"$validated")"
    HY2_HOP_INTERVAL="$(jq -r '.hop_interval_seconds' <<<"$validated")"''',
    '''    read -r HY2_PORTS HY2_HOP_INTERVAL < <(
      python3 -c 'import json,sys; value=json.load(sys.stdin); print(value["ports"], value["hop_interval_seconds"])' <<<"$validated"
    )''',
)

replace_once(
    'core-src/hy2_port_hop.py',
    '''    merged = merge_intervals(intervals)
    if listen_port is not None:''',
    '''    merged = merge_intervals(intervals)
    if sum(end - start + 1 for start, end in merged) < 2:
        raise PortSpecError("端口跳跃范围必须至少包含两个不同的 UDP 端口。")
    if listen_port is not None:''',
)

replace_once(
    'tests/test_hy2_port_hopping.py',
    '''    expect_error('20000-50000', 443, '包含实际监听端口')
    expect_error('0,443', 443, '1–65535')''',
    '''    expect_error('20000-50000', 443, '包含实际监听端口')
    expect_error('443', 443, '至少包含两个')
    expect_error('0,443', 443, '1–65535')''',
)

replace_once(
    'tests/test_quick_upstream_commands.sh',
    '''grep -Fq -- "英国动态IP代理|gw.dataimpulse.com:10000:用户名:密码" "$HOST"

echo "Quick upstream command contract tests passed."''',
    '''grep -Fq -- "英国动态IP代理|gw.dataimpulse.com:10000:用户名:密码" "$HOST"

INSTALLER="$ROOT/vvv-install.sh"
for module in client_package_renderer.py hy2_port_hop.py hy2_port_hop.sh; do
  grep -Fq -- "$module" "$INSTALLER"
done
if grep -Fq -- 'jq -r \".ports\"' "$ROOT/core-src/bootstrap.sh"; then
  echo "bootstrap must not require jq before dependencies are installed" >&2
  exit 1
fi

echo "Quick upstream command and installer contract tests passed."''',
)

print('Final review fixes applied.')

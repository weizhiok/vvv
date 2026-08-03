#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path):
    return (ROOT / path).read_text(encoding='utf-8')


def write(path, value):
    (ROOT / path).write_text(value, encoding='utf-8')


def replace_once(path, old, new):
    value = text(path)
    count = value.count(old)
    if count != 1:
        raise SystemExit(f'{path}: expected one anchor, found {count}: {old[:100]!r}')
    write(path, value.replace(old, new, 1))


def append_once(path, marker, addition):
    value = text(path)
    if addition.strip() in value:
        return
    if marker not in value:
        raise SystemExit(f'{path}: marker missing: {marker!r}')
    write(path, value.replace(marker, marker + addition, 1))


# ---------------------------------------------------------------------------
# Baseline client adapter contract. Future client-only releases are distributed
# from the isolated client-support branch, but fresh installs need the same API.
# ---------------------------------------------------------------------------
replace_once('core-src/client_adapters.py', 'VERSION = 2\n', 'VERSION = 3\n')
replace_once(
    'core-src/client_adapters.py',
    '''def render_shadowrocket(nodes):
    text = '\\n'.join(
        vless_uri(node) if node['protocol'] == 'vless' else hy2_uri_shadowrocket(node)
        for node in nodes
    )
    return b64std(text + ('\\n' if text else '')) + '\\n'
''',
    '''def render_share(nodes):
    text = '\\n'.join(
        vless_uri(node) if node['protocol'] == 'vless' else hy2_uri_shadowrocket(node)
        for node in nodes
    )
    return text + ('\\n' if text else '')


def render_shadowrocket(nodes):
    return b64std(render_share(nodes)) + '\\n'
''',
)
replace_once(
    'core-src/client_adapters.py',
    '''    'shadowrocket': {
        'render': render_shadowrocket,
        'content_type': 'text/plain; charset=utf-8',
    },
}

# Rules are ordered from most specific to broadest. Adding support for a client
''',
    '''    'shadowrocket': {
        'render': render_shadowrocket,
        'content_type': 'text/plain; charset=utf-8',
    },
    'share': {
        'render': render_share,
        'content_type': 'text/plain; charset=utf-8',
    },
}

LOCAL_OUTPUTS = [
    {'filename': 'Quantumult-X.conf', 'format': 'quantumultx', 'display_name': 'Quantumult X'},
    {'filename': 'Loon.conf', 'format': 'loon', 'display_name': 'Loon'},
    {'filename': 'Loon-Shadowrocket.txt', 'format': 'share', 'display_name': 'Loon / Shadowrocket 分享链接'},
    {'filename': 'Shadowrocket.txt', 'format': 'share', 'display_name': 'Shadowrocket 分享链接'},
    {'filename': 'Clash-Verge-Rev.yaml', 'format': 'clash', 'display_name': 'Clash Verge Rev / Mihomo'},
    {'filename': 'NekoBoxForAndroid.yaml', 'format': 'nekobox', 'display_name': 'NekoBoxForAndroid（Clash Meta）'},
]

# Rules are ordered from most specific to broadest. Adding support for a client
''',
)
replace_once(
    'core-src/client_adapters.py',
    '''def available_formats():
    return sorted(RENDERERS)


def smoke_test():
''',
    '''def available_formats():
    return sorted(RENDERERS)


def local_outputs():
    return [dict(item) for item in LOCAL_OUTPUTS]


def smoke_test():
''',
)
replace_once(
    'core-src/client_adapters.py',
    '''    if render('nekobox', sample) != render('clash', sample):
        raise RuntimeError('NekoBox renderer must remain Clash Meta compatible')
    for rule in CLIENT_RULES:
''',
    '''    if render('nekobox', sample) != render('clash', sample):
        raise RuntimeError('NekoBox renderer must remain Clash Meta compatible')
    names = [item['filename'] for item in local_outputs()]
    if len(names) != len(set(names)) or 'NekoBoxForAndroid.yaml' not in names:
        raise RuntimeError('local output manifest is invalid')
    for rule in CLIENT_RULES:
''',
)
replace_once(
    'core-src/client_adapters.py',
    "    print(json.dumps({'version': VERSION, 'formats': available_formats()}, ensure_ascii=False))\n",
    "    print(json.dumps({'version': VERSION, 'formats': available_formats(), 'local_outputs': local_outputs()}, ensure_ascii=False))\n",
)

# Compatibility wrapper: there is only one client-upgrade implementation.
write('core-src/adapter_manager.py', '''#!/usr/bin/env python3
import subprocess
import sys

ENGINE = '/usr/local/lib/vvv/client_upgrade_engine.py'


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else 'status'
    mapping = {'update': 'menu', 'status': 'status'}
    if command not in mapping:
        raise SystemExit('用法：adapter_manager.py [update|status]')
    raise SystemExit(subprocess.run(['python3', ENGINE, mapping[command]], check=False).returncode)


if __name__ == '__main__':
    main()
''')

# ---------------------------------------------------------------------------
# Installer and role bootstrap install the fixed engine + local renderer on all
# roles. This does not automatically run a client update during installation.
# ---------------------------------------------------------------------------
replace_once(
    'vvv-install.sh',
    'client_adapters.py adapter_manager.py center_transport.sh center_manager.sh restore_manager.py diagnostic_report.py node_probe.py)',
    'client_adapters.py adapter_manager.py client_upgrade_engine.py client_local_renderer.py center_transport.sh center_manager.sh restore_manager.py diagnostic_report.py node_probe.py)',
)
replace_once(
    'vvv-install.sh',
    '"$TMP/app/client_adapters.py" "$TMP/app/adapter_manager.py" "$TMP/app/restore_manager.py"',
    '"$TMP/app/client_adapters.py" "$TMP/app/adapter_manager.py" "$TMP/app/client_upgrade_engine.py" "$TMP/app/client_local_renderer.py" "$TMP/app/restore_manager.py"',
)
replace_once(
    'core-src/bootstrap.sh',
    'sub_center.py backup_manager.py rclone_manager.sh client_adapters.py adapter_manager.py center_transport.sh restore_manager.py diagnostic_report.py node_probe.py',
    'sub_center.py backup_manager.py rclone_manager.sh client_adapters.py adapter_manager.py client_upgrade_engine.py client_local_renderer.py center_transport.sh restore_manager.py diagnostic_report.py node_probe.py',
)
replace_once(
    'core-src/bootstrap.sh',
    '''  install -m755 "$BASE_DIR/node_probe.py" /usr/local/lib/vvv/node_probe.py
  cat > /usr/local/sbin/vps <<'EOF_VPS'
''',
    '''  install -m755 "$BASE_DIR/node_probe.py" /usr/local/lib/vvv/node_probe.py
  install -m755 "$BASE_DIR/client_adapters.py" /usr/local/lib/vvv/client_adapters.py
  install -m755 "$BASE_DIR/client_upgrade_engine.py" /usr/local/lib/vvv/client_upgrade_engine.py
  install -m755 "$BASE_DIR/client_local_renderer.py" /usr/local/lib/vvv/client_local_renderer.py
  cat > /usr/local/sbin/vps <<'EOF_VPS'
''',
)
replace_once(
    'core-src/center_install.sh',
    'client_adapters.py adapter_manager.py center_transport.sh center_manager.sh restore_manager.py diagnostic_report.py node_probe.py',
    'client_adapters.py adapter_manager.py client_upgrade_engine.py client_local_renderer.py center_transport.sh center_manager.sh restore_manager.py diagnostic_report.py node_probe.py',
)
replace_once(
    'core-src/center_install.sh',
    'python3 /usr/local/lib/vvv/client_adapters.py >/dev/null || fail "客户端适配器自检失败。"',
    'python3 -m py_compile /usr/local/lib/vvv/client_upgrade_engine.py /usr/local/lib/vvv/client_local_renderer.py\npython3 /usr/local/lib/vvv/client_adapters.py >/dev/null || fail "客户端适配器自检失败。"',
)

# ---------------------------------------------------------------------------
# Unified vps menu: the upgrade item is always the final item above 0.
# ---------------------------------------------------------------------------
replace_once(
    'core-src/vvv_manager.sh',
    'DIAG=/usr/local/lib/vvv/diagnostic_report.py\n',
    'DIAG=/usr/local/lib/vvv/diagnostic_report.py\nCLIENT_UPGRADE=/usr/local/lib/vvv/client_upgrade_engine.py\n',
)
replace_once(
    'core-src/vvv_manager.sh',
    '''  echo "$n. 生成故障诊断报告"; act[$n]=diagnostic; ((n++))
  echo "0. 退出"
''',
    '''  echo "$n. 生成故障诊断报告"; act[$n]=diagnostic; ((n++))
  echo "$n. 升级客户端支持"; act[$n]=client_upgrade; ((n++))
  echo "0. 退出"
''',
)
replace_once(
    'core-src/vvv_manager.sh',
    '''    diagnostic) python3 "$DIAG"; pause;;
    *) echo "请输入有效编号。";;
''',
    '''    diagnostic) python3 "$DIAG"; pause;;
    client_upgrade) python3 "$CLIENT_UPGRADE" menu; pause;;
    *) echo "请输入有效编号。";;
''',
)

# ---------------------------------------------------------------------------
# Subscription center menu and cross-chat debugging handoff.
# ---------------------------------------------------------------------------
replace_once(
    'core-src/center_manager.sh',
    'SUB=/usr/local/lib/vvv/sub_center.py\n',
    'SUB=/usr/local/lib/vvv/sub_center.py\nCLIENT_UPGRADE=/usr/local/lib/vvv/client_upgrade_engine.py\nCLIENT_UPGRADE_URL=https://raw.githubusercontent.com/weizhiok/vvv/client-support/client_upgrade.py\n',
)
replace_once(
    'core-src/center_manager.sh',
    '''debug_headers(){
  local flag=/run/vvv-sub-header-debug.enabled log=/run/vvv-sub-header-debug.jsonl
  rm -f "$log"; : > "$log"; touch "$flag"
  echo "请在客户端中立即刷新统一订阅地址。监听 5 分钟，Ctrl+C 可提前结束。"
''',
    '''debug_headers(){
  local flag=/run/vvv-sub-header-debug.enabled log=/run/vvv-sub-header-debug.jsonl
  rm -f "$log"; : > "$log"; touch "$flag"
  echo "客户端支持仓库：weizhiok/vvv"
  echo "客户端支持分支：client-support"
  echo "目标文件：client_upgrade.py"
  echo "默认升级地址：$CLIENT_UPGRADE_URL"
  echo
  echo "把输出的完整 JSON 发到全新的 ChatGPT 对话后，对方即可知道应只修改 client-support/client_upgrade.py。"
  echo "服务器升级方法：每台 VPS 输入 vps，选择最后一项“升级客户端支持”，直接回车使用默认地址。"
  echo
  echo "请在客户端中立即刷新统一订阅地址。监听 5 分钟，Ctrl+C 可提前结束。"
''',
)
replace_once(
    'core-src/center_manager.sh',
    '''  echo "6. 更新客户端适配器"
  echo "7. 查看客户端适配器状态"
''',
    '''  echo "6. 升级客户端支持（与 vps 菜单相同）"
  echo "7. 查看客户端支持状态"
''',
)
replace_once(
    'core-src/center_manager.sh',
    '''    5) debug_headers;; 6) python3 "$ADAPTERS" update; pause;; 7) python3 "$ADAPTERS" status; pause;;
''',
    '''    5) debug_headers;; 6) python3 "$CLIENT_UPGRADE" menu; pause;; 7) python3 "$CLIENT_UPGRADE" status; pause;;
''',
)
replace_once(
    'core-src/sub_center.py',
    "SENSITIVE_HEADERS = {'authorization', 'proxy-authorization', 'cookie', 'set-cookie'}\n",
    "SENSITIVE_HEADERS = {'authorization', 'proxy-authorization', 'cookie', 'set-cookie'}\nCLIENT_UPGRADE_URL = 'https://raw.githubusercontent.com/weizhiok/vvv/client-support/client_upgrade.py'\n",
)
replace_once(
    'core-src/sub_center.py',
    '''    event = {'time': now(), 'source_ip': handler.client_address[0], 'method': handler.command,
             'path': redacted_path(urlparse(handler.path).path, suffix), 'headers': headers,
             'recognized_client': (recognition or {}).get('name', '未识别'),
             'response_format': (recognition or {}).get('format', '无')}
''',
    '''    event = {'time': now(), 'source_ip': handler.client_address[0], 'method': handler.command,
             'path': redacted_path(urlparse(handler.path).path, suffix), 'headers': headers,
             'recognized_client': (recognition or {}).get('name', '未识别'),
             'response_format': (recognition or {}).get('format', '无'),
             'client_support_handoff': {
                 'purpose': '为 VVV 新增客户端订阅识别和每台 VPS 的本机客户端配置支持',
                 'repository': 'weizhiok/vvv',
                 'branch': 'client-support',
                 'target_file': 'client_upgrade.py',
                 'default_upgrade_url': CLIENT_UPGRADE_URL,
                 'installed_version': int(getattr(client_adapters, 'VERSION', 0)),
                 'required_action': '根据本条 User-Agent 只更新 client-support 分支中的 client_upgrade.py；增加识别规则或渲染器、提高 VERSION 并运行客户端支持测试。',
                 'safety_boundary': '不得修改 main 安装器、Xray、sing-box、节点状态、服务端代理配置、系统设置或其他 VVV 功能。',
                 'server_upgrade_method': '每台 VPS 输入 vps，选择最后一项“升级客户端支持”，直接回车使用默认地址。',
                 'new_chat_instruction': '这是 VVV 客户端支持扩展请求。请只修改 weizhiok/vvv 的 client-support 分支中 client_upgrade.py，并保持客户端升级与代理核心、节点和系统完全隔离。',
             }}
''',
)

# ---------------------------------------------------------------------------
# Existing local view commands always redraw from current adapter, so future
# client-only upgrades cannot be overwritten by legacy shell templates.
# ---------------------------------------------------------------------------
replace_once(
    'core-src/host.sh',
    '''show_client_config() {
  local relay_id="$1" dir="${PACKAGE_ROOT}/${relay_id}"
  generate_client_files "$STATE_FILE" "$relay_id" "$dir" relay >/dev/null
''',
    '''show_client_config() {
  local relay_id="$1" dir="${PACKAGE_ROOT}/${relay_id}"
  if [[ -x /usr/local/lib/vvv/client_local_renderer.py ]]; then
    python3 /usr/local/lib/vvv/client_local_renderer.py regenerate >/dev/null
  else
    generate_client_files "$STATE_FILE" "$relay_id" "$dir" relay >/dev/null
  fi
''',
)
replace_once(
    'core-src/host.sh',
    '''show_upstream_client_config() {
  local upstream_id="$1" dir="${PACKAGE_ROOT}/${upstream_id}"
  generate_client_files "$STATE_FILE" "$upstream_id" "$dir" upstream >/dev/null
''',
    '''show_upstream_client_config() {
  local upstream_id="$1" dir="${PACKAGE_ROOT}/${upstream_id}"
  if [[ -x /usr/local/lib/vvv/client_local_renderer.py ]]; then
    python3 /usr/local/lib/vvv/client_local_renderer.py regenerate >/dev/null
  else
    generate_client_files "$STATE_FILE" "$upstream_id" "$dir" upstream >/dev/null
  fi
''',
)
replace_once(
    'core-src/host.sh',
    '''cat > /usr/local/sbin/jp-show-nodes <<'EOF_SHOW'
#!/usr/bin/env bash
cat /root/日本VPS-客户端节点.txt
EOF_SHOW
''',
    '''cat > /usr/local/sbin/jp-show-nodes <<'EOF_SHOW'
#!/usr/bin/env bash
set -e
if [[ -x /usr/local/lib/vvv/client_local_renderer.py ]]; then
  python3 /usr/local/lib/vvv/client_local_renderer.py regenerate >/dev/null
fi
cat /root/日本VPS-客户端节点.txt
EOF_SHOW
''',
)

# Landing menu is separate because the unified manager delegates to it.
replace_once(
    'core-src/landing.sh',
    '''state=/etc/jp-relay/landing-state.json
nodes=/root/中转客户端节点.txt
updater=/usr/local/lib/vvv/update_landing_ip.py
''',
    '''state=/etc/jp-relay/landing-state.json
nodes=/root/中转客户端节点.txt
updater=/usr/local/lib/vvv/update_landing_ip.py
client_upgrade=/usr/local/lib/vvv/client_upgrade_engine.py
client_renderer=/usr/local/lib/vvv/client_local_renderer.py
''',
)
replace_once(
    'core-src/landing.sh',
    '''show_status() {
  [ ! -f "$nodes" ] || cat "$nodes"
''',
    '''show_status() {
  if [ -x "$client_renderer" ]; then
    python3 "$client_renderer" regenerate >/dev/null || echo "警告：本机客户端配置重新生成失败。" >&2
  fi
  [ ! -f "$nodes" ] || cat "$nodes"
''',
)
replace_once(
    'core-src/landing.sh',
    '''  echo "1. 查看节点与线路状态"
  echo "2. 修改主机 IP 地址"
  echo "0. 退出"
''',
    '''  echo "1. 查看节点与线路状态"
  echo "2. 修改主机 IP 地址"
  echo "3. 升级客户端支持"
  echo "0. 退出"
''',
)
replace_once(
    'core-src/landing.sh',
    '''    1) show_status; pause;;
    2) change_main_ip; pause;;
    0) exit 0;;
''',
    '''    1) show_status; pause;;
    2) change_main_ip; pause;;
    3) python3 "$client_upgrade" menu; pause;;
    0) exit 0;;
''',
)

# ---------------------------------------------------------------------------
# Documentation: complete installers are never the client-support upgrade path.
# ---------------------------------------------------------------------------
append_once(
    'README.md',
    '\n## 订阅与客户端\n',
    '''

> **客户端支持升级与完整安装严格分离。** 新增或修复客户端识别时，不得重新运行完整 VVV 安装器。完整安装器会维护角色和代理核心；客户端支持只能在每台 VPS 输入 `vps`，选择退出上方最后一项“升级客户端支持”。该流程不会运行 APT、不会更新内核、不会下载或重启 Xray/sing-box、不会修改节点、端口、UUID、密码、证书或系统设置。

客户端支持由独立 `client-support` 分支发布，默认地址：

```text
https://raw.githubusercontent.com/weizhiok/vvv/client-support/client_upgrade.py
```

本地固定升级引擎会自动识别订阅中心主机、直连副机和中转副机，只重新生成客户端配置；包含订阅中心时最多只重启 `vvv-sub.service`。升级前后会比较代理二进制、代理配置、节点状态、systemd 单元、内核版本以及 Xray/sing-box 进程身份，任何意外变化都会判定失败并恢复旧客户端支持。
''',
)

# ---------------------------------------------------------------------------
# Tests and final validation.
# ---------------------------------------------------------------------------
replace_once(
    'tests/final_runtime_validation.sh',
    '''  "$ROOT/core-src/client_adapters.py" \\
  "$ROOT/core-src/adapter_manager.py" \\
''',
    '''  "$ROOT/core-src/client_adapters.py" \\
  "$ROOT/core-src/adapter_manager.py" \\
  "$ROOT/core-src/client_upgrade_engine.py" \\
  "$ROOT/core-src/client_local_renderer.py" \\
''',
)
replace_once(
    'tests/final_runtime_validation.sh',
    '''  "$ROOT/tests/build_slot_fixture.py"
python3 "$ROOT/tests/conformance.py"
''',
    '''  "$ROOT/tests/build_slot_fixture.py" \\
  "$ROOT/tests/client_upgrade_isolation_validation.py"
python3 "$ROOT/tests/conformance.py"
python3 "$ROOT/tests/client_upgrade_isolation_validation.py"
''',
)

conformance_addition = r'''

def test_isolated_client_support_upgrade():
    engine = read('core-src/client_upgrade_engine.py')
    renderer = read('core-src/client_local_renderer.py')
    manager = read('core-src/vvv_manager.sh')
    landing = read('core-src/landing.sh')
    center = read('core-src/center_manager.sh')
    sub = read('core-src/sub_center.py')
    installer = read('vvv-install.sh')
    default_url = 'https://raw.githubusercontent.com/weizhiok/vvv/client-support/client_upgrade.py'
    for token in ('PROTECTED_FILES', 'compare_protected', 'proxy_processes_unchanged', '受保护的代理配置：未改动'):
        require(token in engine, f'客户端升级保护缺少：{token}')
    for forbidden in ('apt-get', "restart', 'xray.service", "restart', 'sing-box.service"):
        require(forbidden not in engine, f'客户端升级引擎包含越界操作：{forbidden}')
    require('local_outputs' in renderer and 'landing-state.json' in renderer and 'state.json' in renderer,
            '本机客户端生成器没有覆盖三种角色')
    require('升级客户端支持' in manager and manager.index('升级客户端支持') < manager.index('echo "0. 退出"'),
            '统一菜单没有把客户端升级放在退出上方')
    require('3. 升级客户端支持' in landing, '中转副机菜单缺少客户端升级')
    require(default_url in center and 'client_support_handoff' in sub and 'new_chat_instruction' in sub,
            '请求头调试缺少客户端支持交接信息')
    for name in ('client_upgrade_engine.py', 'client_local_renderer.py'):
        require(name in installer, f'安装器缺少客户端升级文件：{name}')
'''
value = text('tests/conformance.py')
if 'def test_isolated_client_support_upgrade()' not in value:
    value = value.replace('\ndef main():\n', conformance_addition + '\n\ndef main():\n', 1)
    value = value.replace(
        '        test_node_names_and_clients, test_landing_and_direct_ip_change,\n',
        '        test_node_names_and_clients, test_landing_and_direct_ip_change,\n        test_isolated_client_support_upgrade,\n',
        1,
    )
    write('tests/conformance.py', value)

print('client support isolation transformation applied')

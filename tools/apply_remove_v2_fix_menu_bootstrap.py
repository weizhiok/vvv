#!/usr/bin/env python3
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding='utf-8')


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding='utf-8')


def replace_required(text: str, old: str, new: str, label: str, count: int | None = None) -> str:
    actual = text.count(old)
    if actual == 0:
        raise SystemExit(f'{label}: target not found')
    if count is not None and actual != count:
        raise SystemExit(f'{label}: expected {count}, found {actual}')
    return text.replace(old, new)


def regex_required(text: str, pattern: str, replacement: str, label: str, count: int = 1) -> str:
    out, actual = re.subn(pattern, replacement, text, count=count, flags=re.S)
    if actual != count:
        raise SystemExit(f'{label}: expected {count}, found {actual}')
    return out


# 1. Correct the APT index target so deb-src indexes are actually disabled.
apt_files = [
    'vvv-install.sh',
    'core-src/host.sh',
    'core-src/landing.sh',
    'core-src/center_install.sh',
    'core-src/rclone_manager.sh',
]
for path in apt_files:
    text = read(path)
    text = replace_required(
        text,
        'Acquire::IndexTargets::deb::Sources::DefaultEnabled=false',
        'Acquire::IndexTargets::deb-src::Sources::DefaultEnabled=false',
        f'{path} deb-src target',
    )
    write(path, text)

# 2. The unified manager is the sole owner of /usr/local/sbin/vps.
host_path = 'core-src/host.sh'
host = read(host_path)
host = regex_required(
    host,
    r'''install_shortcuts\(\) \{\n  mkdir -p /usr/local/sbin\n  cat > /usr/local/sbin/vps <<'EOF_VPS_CMD'\n#!/usr/bin/env bash\n# JP_RELAY_JPR3_MANAGER\n/usr/local/sbin/jp-relay-manager --manage\nEOF_VPS_CMD\n  chmod 700 /usr/local/sbin/vps\n  cat > /usr/local/sbin/jp-show-nodes <<'EOF_SHOW'\n#!/usr/bin/env bash\ncat /root/日本VPS-客户端节点.txt\nEOF_SHOW\n  chmod 700 /usr/local/sbin/jp-show-nodes\n\}''',
    '''install_shortcuts() {
  mkdir -p /usr/local/sbin
  # /usr/local/sbin/vps 只能由统一 VVV 管理器创建。
  # 中转管理器每次启动时仅维护自己的专用快捷命令，不能覆盖首页入口。
  cat > /usr/local/sbin/jp-show-nodes <<'EOF_SHOW'
#!/usr/bin/env bash
cat /root/日本VPS-客户端节点.txt
EOF_SHOW
  chmod 700 /usr/local/sbin/jp-show-nodes
}''',
    'host unified vps ownership',
)

# 3. Remove v2rayNG local node/config generation from the host installer.
host = replace_required(
    host,
    'qx_lines=[]; share_links=[]; v2rayng_links=[]; loon_lines=[]; clash_entries=[]',
    'qx_lines=[]; share_links=[]; loon_lines=[]; clash_entries=[]',
    'host client list variables',
    1,
)
host = replace_required(
    host,
    '    qx_lines.append(qx); share_links.append((name,uri)); v2rayng_links.append((name,uri)); loon_lines.append(loon); clash_entries.append(clash)',
    '    qx_lines.append(qx); share_links.append((name,uri)); loon_lines.append(loon); clash_entries.append(clash)',
    'host VLESS client append',
    1,
)
host = regex_required(
    host,
    r'''\n    v2_params=\[\("obfs","salamander"\),\("obfs-password",h\["obfs_password"\]\),\("sni",h\["server_name"\]\),\("pinSHA256",h\["certificate_pin_hex"\]\)\]\n    v2_uri=f"hysteria2://\{quote\(password,safe=''\)\}@\{ip\}:\{port\}/\?\{urlencode\(v2_params\)\}#\{quote\(name,safe=''\)\}"''',
    '',
    'host HY2 v2 URI',
)
host = replace_required(
    host,
    '    share_links.append((name,uri)); v2rayng_links.append((name,v2_uri)); loon_lines.append(loon); clash_entries.append(clash)',
    '    share_links.append((name,uri)); loon_lines.append(loon); clash_entries.append(clash)',
    'host HY2 client append',
    1,
)
host = replace_required(
    host,
    'v2rayng_text="\\n".join(uri for _,uri in v2rayng_links)\n',
    '',
    'host v2 text',
    1,
)
host = regex_required(
    host,
    r'''\nif v2rayng_links:\n    lines \+= \["","【v2rayNG 2\.2\.6\+】"\]\n    for name,uri in v2rayng_links: lines \+= \[f"\[\{name\}\]",uri\]''',
    '',
    'host v2 summary block',
)
host = replace_required(
    host,
    '(out/"v2rayNG.txt").write_text((v2rayng_text+"\\n") if v2rayng_text else "",encoding="utf-8")\n',
    '',
    'host v2 file',
    1,
)
if 'v2rayNG' in host or 'v2rayng' in host:
    raise SystemExit('host.sh still contains v2rayNG support')
write(host_path, host)

# 4. Remove v2rayNG files and display blocks from the landing installer.
landing_path = 'core-src/landing.sh'
landing = read(landing_path)
for old, label in [
    ('  : > "$CLIENT_DIR/v2rayNG.txt"\n', 'landing v2 empty file'),
    ('    printf \'%s\\n\' "$vless_uri" >> "$CLIENT_DIR/v2rayNG.txt"\n', 'landing v2 VLESS file'),
    ('    v2rayng_hy2_uri="hysteria2://$(urlencode "$JAPAN_HY2_PASSWORD")@${JAPAN_PUBLIC_IP}:${JAPAN_PORT}/?obfs=salamander&obfs-password=$(urlencode "$JAPAN_HY2_OBFS")&sni=$(urlencode "$JAPAN_HY2_SERVER_NAME")&pinSHA256=$(urlencode "$JAPAN_HY2_PIN_HEX")#${encoded_hy2_name}"\n', 'landing v2 HY2 URI'),
    ('    printf \'%s\\n\' "$v2rayng_hy2_uri" >> "$CLIENT_DIR/v2rayNG.txt"\n', 'landing v2 HY2 file'),
    ('      echo\n      echo "【v2rayNG 2.2.6+：${hy2_name}】"\n      echo "$v2rayng_hy2_uri"\n', 'landing v2 display'),
]:
    landing = replace_required(landing, old, '', label, 1)
if 'v2rayNG' in landing or 'v2rayng' in landing:
    raise SystemExit('landing.sh still contains v2rayNG support')
write(landing_path, landing)

# 5. Remove the v2rayNG subscription renderer and /v2 route.
sub_path = 'core-src/sub_center.py'
sub = read(sub_path)
sub = replace_required(
    sub,
    "SHORT_PATHS = {'c': 'clash', 'qx': 'quantumultx', 'ln': 'loon', 'sr': 'shadowrocket', 'v2': 'v2rayng'}",
    "SHORT_PATHS = {'c': 'clash', 'qx': 'quantumultx', 'ln': 'loon', 'sr': 'shadowrocket'}",
    'subscription short paths',
    1,
)
sub = regex_required(
    sub,
    r'''\n\ndef hy2_uri_v2rayng\(node\):\n.*?(?=\n\ndef render_qx\(nodes\):)''',
    '',
    'v2rayNG HY2 renderer',
)
sub = regex_required(
    sub,
    r'''\n\ndef render_v2rayng\(nodes\):\n.*?(?=\n\ndef render_clash\(nodes\):)''',
    '',
    'v2rayNG subscription renderer',
)
sub = replace_required(
    sub,
    "files={'clash':render_clash(nodes),'quantumultx':render_qx(nodes),'loon':render_loon(nodes),'shadowrocket':render_shadowrocket(nodes),'v2rayng':render_v2rayng(nodes)}",
    "files={'clash':render_clash(nodes),'quantumultx':render_qx(nodes),'loon':render_loon(nodes),'shadowrocket':render_shadowrocket(nodes)}",
    'subscription output files',
    1,
)
if 'v2rayNG' in sub or 'v2rayng' in sub or "'v2':" in sub:
    raise SystemExit('sub_center.py still contains v2rayNG support')
write(sub_path, sub)

# 6. Remove v2rayNG from the subscription center output and format Caddyfiles.
center_path = 'core-src/center_install.sh'
center = read(center_path)
center = replace_required(
    center,
    '''validate_caddy(){
  chown root:caddy /etc/caddy/Caddyfile
  chmod 640 /etc/caddy/Caddyfile
  runuser -u caddy -- /usr/local/bin/caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile || fail "Caddy 配置验证失败。"
}''',
    '''validate_caddy(){
  /usr/local/bin/caddy fmt --overwrite /etc/caddy/Caddyfile >/dev/null || fail "Caddy 配置格式化失败。"
  chown root:caddy /etc/caddy/Caddyfile
  chmod 640 /etc/caddy/Caddyfile
  runuser -u caddy -- /usr/local/bin/caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile || fail "Caddy 配置验证失败。"
}''',
    'Caddy formatting',
    1,
)
center = replace_required(center, '  echo "v2rayNG：${base}/r/${token}/v2"\n', '', 'center v2 URL', 1)
if 'v2rayNG' in center or '/v2"' in center:
    raise SystemExit('center_install.sh still exposes v2rayNG')
write(center_path, center)

# 7. Do not preserve the relay module's temporary vps wrapper during unified install.
bootstrap_path = 'core-src/bootstrap.sh'
bootstrap = read(bootstrap_path)
bootstrap = replace_required(
    bootstrap,
    '''install_host(){
  bash "$BASE_DIR/host.sh"
  [[ -x /usr/local/sbin/vps ]] && cp -f /usr/local/sbin/vps /usr/local/sbin/vvv-host-original
}''',
    '''install_host(){
  bash "$BASE_DIR/host.sh"
}''',
    'bootstrap host wrapper copy',
    1,
)
write(bootstrap_path, bootstrap)

# 8. Update documentation without introducing the future unified subscription URL.
readme_path = 'README.md'
readme = read(readme_path)
old_command = 'curl -fsSL --retry 5 "https://raw.githubusercontent.com/weizhiok/vvv/install/vvv-install.sh?$(date +%s)" | bash'
new_command = '{ command -v curl >/dev/null 2>&1 || { apt-get -o DPkg::Lock::Timeout=10 -o Acquire::Retries=2 -o Acquire::IndexTargets::deb-src::Sources::DefaultEnabled=false update && DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=10 -o Acquire::Retries=2 install -y curl ca-certificates; }; } && curl -fsSL --retry 5 "https://raw.githubusercontent.com/weizhiok/vvv/install/vvv-install.sh?$(date +%s)" | bash'
readme = replace_required(readme, old_command, new_command, 'README bootstrap command', 1)
readme = regex_required(
    readme,
    r'''## 客户端订阅\n.*?(?=\n## 订阅中心与 HTTPS)''',
    '''## 客户端订阅

每个订阅令牌提供四个独立短路径：

```text
/c   Clash Verge Rev / Mihomo
/qx  Quantumult X
/ln  Loon
/sr  Shadowrocket
```

- 四种客户端均只显示订阅地址或文本配置，不生成二维码；
- Quantumult X 只输出 VLESS；
- Loon 使用无多余引号的 Salamander 混淆密码；
- Shadowrocket 使用 Base64 编码的 VLESS/Hysteria 2 分享链接；
- 已移除 v2rayNG 的节点配置、文件和订阅入口。
''',
    'README client section',
)
readme = readme.replace(
    '- 使用 root 用户执行；\n',
    '- 使用 root 用户执行；\n- 固定安装命令会在缺少 curl 时先通过 APT 安装 curl 和 CA 证书；\n',
    1,
)
write(readme_path, readme)

# 9. Replace and extend conformance coverage.
test_path = 'tests/conformance.py'
tests = read(test_path)
tests = regex_required(
    tests,
    r'''\n\ndef decoded_v2rayng\(module, nodes\):\n.*?(?=\n\ndef test_backup_policy\(\):)''',
    '''

def test_subscription_renderers():
    module = load_sub_center()
    nodes = module.nodes_from_host({'host_id': 'audit-host-001', 'role': 'center-relay', 'state': sample_host_state()})
    require({n['protocol'] for n in nodes} == {'vless', 'hysteria2'}, '双协议直连节点没有同时进入订阅')
    clash = module.render_clash(nodes)
    qx = module.render_qx(nodes)
    loon = module.render_loon(nodes)
    shadowrocket = base64.b64decode(module.render_shadowrocket(nodes)).decode('utf-8')
    require('type: vless' in clash and 'type: hysteria2' in clash, 'Clash 订阅缺少双协议节点')
    require('vless=' in qx and 'hysteria' not in qx.lower(), 'Quantumult X 应只输出 VLESS')
    require('salamander-password=salamander-secret' in loon, 'Loon HY2 混淆密码格式错误')
    require('salamander-password="' not in loon, 'Loon HY2 混淆密码仍带双引号')
    require('vless://' in shadowrocket and 'hysteria2://' in shadowrocket, 'Shadowrocket 缺少双协议链接')
    source = read('core-src/sub_center.py')
    short_paths = "{'c': 'clash', 'qx': 'quantumultx', 'ln': 'loon', 'sr': 'shadowrocket'}"
    require(short_paths in source, '订阅短路径集合不正确')
    for token in ('v2rayNG', 'v2rayng', "'v2':"):
        require(token not in source, f'订阅中心仍保留已弃用客户端：{token}')
''',
    'subscription renderer tests',
)
tests = replace_required(
    tests,
    "Acquire::IndexTargets::deb::Sources::DefaultEnabled=false",
    "Acquire::IndexTargets::deb-src::Sources::DefaultEnabled=false",
    'APT conformance target',
)
tests = replace_required(
    tests,
    "    require('跳过重复 apt update' in center, '订阅中心仍可能静默重复刷新软件源')\n",
    "    require('跳过重复 apt update' in center, '订阅中心仍可能静默重复刷新软件源')\n    require('caddy fmt --overwrite /etc/caddy/Caddyfile' in center, 'Caddyfile 没有在验证前自动格式化')\n",
    'Caddy format conformance',
    1,
)
manager_test = '''

def test_manager_entrypoint_and_bootstrap_command():
    host = read('core-src/host.sh')
    bootstrap = read('core-src/bootstrap.sh')
    readme = read('README.md')
    production = '\n'.join((host, read('core-src/landing.sh'), read('core-src/center_install.sh'), read('core-src/sub_center.py')))
    require('cat > /usr/local/sbin/vps' not in host, '中转管理器仍会覆盖统一 vps 首页入口')
    require('exec /usr/local/lib/vvv/vvv_manager.sh "$@"' in bootstrap, '统一 vps 首页入口没有指向 vvv_manager.sh')
    require('vvv-host-original' not in bootstrap, '统一安装仍保存会误导的中转 vps 包装器')
    require('command -v curl >/dev/null 2>&1 || {' in readme, '固定安装命令没有处理 curl 缺失')
    require('DPkg::Lock::Timeout=10' in readme, 'curl 自举安装没有 10 秒 APT 锁上限')
    for token in ('v2rayNG', 'v2rayng'):
        require(token not in production, f'生产脚本仍保留已弃用客户端：{token}')
'''
tests = replace_required(
    tests,
    '\n\ndef test_hy2_leaf_certificate():',
    manager_test + '\n\ndef test_hy2_leaf_certificate():',
    'manager regression test insertion',
    1,
)
tests = replace_required(
    tests,
    '        test_apt_lock_policy,\n        test_hy2_leaf_certificate,',
    '        test_apt_lock_policy,\n        test_manager_entrypoint_and_bootstrap_command,\n        test_hy2_leaf_certificate,',
    'manager test registration',
    1,
)
write(test_path, tests)

# Final source-level guardrails.
for path in ['core-src/host.sh', 'core-src/landing.sh', 'core-src/center_install.sh', 'core-src/sub_center.py']:
    text = read(path)
    if 'v2rayNG' in text or 'v2rayng' in text:
        raise SystemExit(f'{path}: v2rayNG token remains')
if 'cat > /usr/local/sbin/vps' in read('core-src/host.sh'):
    raise SystemExit('host manager still owns the unified vps command')
print('PATCH APPLIED')

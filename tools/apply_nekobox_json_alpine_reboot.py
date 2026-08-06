#!/usr/bin/env python3
from pathlib import Path


def replace_once(path, old, new, label):
    file_path = Path(path)
    text = file_path.read_text(encoding='utf-8')
    if old not in text:
        raise SystemExit(f'{label}: marker not found in {path}')
    file_path.write_text(text.replace(old, new, 1), encoding='utf-8')


def replace_section(path, start, end, replacement, label):
    file_path = Path(path)
    text = file_path.read_text(encoding='utf-8')
    left = text.find(start)
    right = text.find(end, left + len(start)) if left >= 0 else -1
    if left < 0 or right < 0:
        raise SystemExit(f'{label}: section marker not found in {path}')
    file_path.write_text(text[:left] + replacement + text[right:], encoding='utf-8')


# 1. NekoBox subscription: sing-box outbound JSON; local SN/YAML outputs remain separate.
adapter = 'core-src/client_adapters.py'
replace_once(adapter, 'VERSION = 7', 'VERSION = 8', 'adapter version')

insert_marker = '''def render_clash(nodes):
'''
json_renderer = '''def _sing_box_server_ports(node):
    values = []
    for item in hy2_ports(node).split(','):
        item = item.strip()
        if not item:
            continue
        if '-' in item:
            start, end = item.split('-', 1)
            values.append(f'{start}:{end}')
        else:
            values.append(item)
    return values or [str(node['port'])]


def render_nekobox_subscription(nodes):
    outbounds = []
    for node in nodes:
        if node['protocol'] == 'vless':
            outbounds.append({
                'type': 'vless',
                'tag': node['name'],
                'server': node['server'],
                'server_port': int(node['port']),
                'uuid': node['uuid'],
                'flow': 'xtls-rprx-vision',
                'network': 'tcp',
                'packet_encoding': 'xudp',
                'tls': {
                    'enabled': True,
                    'server_name': node['sni'],
                    'insecure': True,
                    'utls': {'enabled': True, 'fingerprint': 'chrome'},
                    'reality': {
                        'enabled': True,
                        'public_key': node['public_key'],
                        'short_id': node['short_id'],
                    },
                },
            })
        else:
            outbounds.append({
                'type': 'hysteria2',
                'tag': node['name'],
                'server': node['server'],
                'server_ports': _sing_box_server_ports(node),
                'hop_interval': f'{fixed_hop_interval(node)}s',
                'up_mbps': client_up_mbps(node),
                'down_mbps': client_down_mbps(node),
                'obfs': {
                    'type': 'salamander',
                    'password': node['obfs_password'],
                },
                'password': node['password'],
                'tls': {
                    'enabled': True,
                    'server_name': node['sni'],
                    'insecure': True,
                    'alpn': ['h3'],
                },
            })
    return json.dumps({'outbounds': outbounds}, ensure_ascii=False, indent=2) + '\n'


'''
replace_once(adapter, insert_marker, json_renderer + insert_marker, 'NekoBox JSON renderer insertion')

replace_once(
    adapter,
    "    'nekobox': {'render': render_nekobox, 'content_type': 'text/yaml; charset=utf-8'},\n",
    "    'nekobox': {'render': render_nekobox_subscription, 'content_type': 'application/json; charset=utf-8'},\n"
    "    'nekobox-yaml': {'render': render_nekobox, 'content_type': 'text/yaml; charset=utf-8'},\n",
    'NekoBox renderer registry',
)
replace_once(
    adapter,
    "    {'filename': 'NekoBoxForAndroid.yaml', 'format': 'nekobox',\n",
    "    {'filename': 'NekoBoxForAndroid.yaml', 'format': 'nekobox-yaml',\n",
    'local NekoBox YAML format isolation',
)

old_smoke = '''    neko = render('nekobox', sample)
    if 'hop-interval: 30' not in neko or 'hop-interval: "20-30"' in neko:
        raise RuntimeError('NekoBox subscription YAML must use fixed 30-second hopping')
'''
new_smoke = '''    neko = json.loads(render('nekobox', sample))
    if not isinstance(neko.get('outbounds'), list) or len(neko['outbounds']) != 2:
        raise RuntimeError('NekoBox sing-box subscription outbounds are missing')
    neko_vless = next((item for item in neko['outbounds'] if item.get('type') == 'vless'), None)
    neko_hy2 = next((item for item in neko['outbounds'] if item.get('type') == 'hysteria2'), None)
    if not neko_vless or not neko_hy2:
        raise RuntimeError('NekoBox sing-box subscription protocol output is incomplete')
    if neko_hy2.get('server_ports') != ['443', '20000:50000'] or neko_hy2.get('hop_interval') != '30s':
        raise RuntimeError('NekoBox sing-box subscription lost HY2 port hopping')
    if neko_hy2.get('up_mbps') != 30 or neko_hy2.get('down_mbps') != 50:
        raise RuntimeError('NekoBox sing-box subscription lost client bandwidth')
    if ((neko_vless.get('tls') or {}).get('reality') or {}).get('short_id') != '0123456789abcdef':
        raise RuntimeError('NekoBox sing-box subscription lost VLESS Reality')
    neko_yaml = render('nekobox-yaml', sample)
    if 'hop-interval: 30' not in neko_yaml or 'hop-interval: "20-30"' in neko_yaml:
        raise RuntimeError('NekoBox hidden local YAML must keep fixed 30-second hopping')
'''
replace_once(adapter, old_smoke, new_smoke, 'NekoBox smoke contract')

# 2. Simplify first-install subscription transport wording only.
bootstrap = 'core-src/bootstrap.sh'
old_menu = '''  echo "请选择订阅传输方式："
  echo "1. 直接 HTTPS【默认】"
  echo "   域名由 Caddy 自动申请公共证书；IP 由 Certbot 申请 Let's Encrypt IP 证书。"
  echo "2. 直接 HTTP"
  echo "   不申请证书，仅限临时调试；请勿长期使用。"
  echo "3. 固定 HTTPS 域名（Cloudflare Tunnel）"
  echo "   公共地址使用标准 443，VPS 只运行本地 HTTP；需提前创建 Tunnel 公共主机名。"
'''
new_menu = '''  echo "请选择订阅传输方式："
  echo "1. 使用 HTTPS【默认】"
  echo "2. 使用 HTTP"
  echo "3. 使用 Cloudflare Tunnel"
'''
replace_once(bootstrap, old_menu, new_menu, 'subscription transport menu')

# 3. Keep Debian behavior and add Alpine BusyBox crond/OpenRC support.
host = 'core-src/host.sh'
new_daily_reboot = r'''install_daily_reboot_cron() {
  local install_day backend root_crontab temporary

  install -d -m700 /usr/local/lib/vvv /var/lib/vvv
  install_day="$(date '+%Y%m%d')"
  [[ "$install_day" =~ ^[0-9]{8}$ ]] || fail "无法读取当前日期，不能安全配置每天自动重启。"
  printf '%s\n' "$install_day" > /var/lib/vvv/daily-reboot-install-day
  chmod 600 /var/lib/vvv/daily-reboot-install-day

  cat > /usr/local/lib/vvv/daily-reboot.sh <<'EOF_DAILY_REBOOT'
#!/usr/bin/env bash
set -Eeuo pipefail

marker=/var/lib/vvv/daily-reboot-install-day
[[ -r "$marker" ]] || exit 0
read -r install_day < "$marker"
current_day="$(date '+%Y%m%d')"
[[ "$install_day" =~ ^[0-9]{8}$ && "$current_day" =~ ^[0-9]{8}$ ]] || exit 0

if (( 10#$current_day <= 10#$install_day )); then
  command -v logger >/dev/null 2>&1 && logger -t vvv-daily-reboot "忽略安装当天的重启任务；首次最早从次日 06:00 执行。"
  exit 0
fi

if [[ "$(date '+%H:%M')" != "06:00" ]]; then
  command -v logger >/dev/null 2>&1 && logger -t vvv-daily-reboot "忽略非 06:00 触发的重启请求。"
  exit 0
fi

install -d -m755 /run/lock
lock_dir=/run/lock/vvv-daily-reboot.lock
mkdir "$lock_dir" 2>/dev/null || exit 0
trap 'rmdir "$lock_dir" 2>/dev/null || true' EXIT

command -v logger >/dev/null 2>&1 && logger -t vvv-daily-reboot "开始执行每天北京时间 06:00 自动重启。"
sync
sleep 2

if command -v systemctl >/dev/null 2>&1 && [[ "$(cat /proc/1/comm 2>/dev/null | tr -d '[:space:]')" == systemd ]]; then
  systemctl reboot --no-wall
elif command -v reboot >/dev/null 2>&1; then
  reboot
else
  command -v logger >/dev/null 2>&1 && logger -t vvv-daily-reboot "找不到可用的系统重启命令。"
  exit 1
fi
EOF_DAILY_REBOOT
  chmod 700 /usr/local/lib/vvv/daily-reboot.sh

  if [[ -f /etc/alpine-release ]]; then
    command -v crond >/dev/null 2>&1 || fail "Alpine 缺少 BusyBox crond，无法配置每天 06:00 自动重启。"
    command -v rc-update >/dev/null 2>&1 || fail "Alpine 缺少 OpenRC rc-update。"
    command -v rc-service >/dev/null 2>&1 || fail "Alpine 缺少 OpenRC rc-service。"
    install -d -m755 /etc/crontabs
    root_crontab=/etc/crontabs/root
    temporary="$(mktemp /tmp/vvv-root-crontab.XXXXXX)"
    if [[ -f "$root_crontab" ]]; then
      grep -vF '/usr/local/lib/vvv/daily-reboot.sh' "$root_crontab" > "$temporary" || true
    fi
    printf '%s\n' '0 6 * * * /usr/local/lib/vvv/daily-reboot.sh' >> "$temporary"
    install -m600 "$temporary" "$root_crontab"
    rm -f "$temporary"
    rc-update add crond default >/dev/null
    rc-service crond restart >/dev/null
    rc-service crond status >/dev/null 2>&1 || fail "Alpine crond 服务未运行。"
    backend='Alpine BusyBox crond / OpenRC'
  else
    [[ -x /usr/sbin/cron || -x /usr/bin/cron ]] || fail "cron 未安装，无法配置每天 06:00 自动重启。"
    cat > /etc/cron.d/vvv-daily-reboot <<'EOF_DAILY_REBOOT_CRON'
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 6 * * * root /usr/local/lib/vvv/daily-reboot.sh
EOF_DAILY_REBOOT_CRON
    chmod 644 /etc/cron.d/vvv-daily-reboot
    systemctl enable cron.service >/dev/null
    systemctl restart cron.service
    systemctl is-active --quiet cron.service || fail "cron 服务未运行，无法保证每天 06:00 自动重启。"
    backend='Debian cron / systemd'
  fi
  echo "每天北京时间 06:00 自动重启：已启用（${backend}，首次最早为明天）"
}


'''
replace_section(host, 'install_daily_reboot_cron() {', 'prompt_initial_mode_and_port() {', new_daily_reboot, 'daily reboot function')

# Permanent tests: NekoBox sing-box JSON and local YAML separation.
test_hop = 'tests/test_client_port_hopping.py'
replace_once(
    test_hop,
    "    assert adapters.render('nekobox', [node]).startswith('proxies:\\n')\n",
    "    neko_subscription = json.loads(adapters.render('nekobox', [node]))\n"
    "    assert list(neko_subscription) == ['outbounds']\n"
    "    assert len(neko_subscription['outbounds']) == 1\n"
    "    hy2_outbound = neko_subscription['outbounds'][0]\n"
    "    assert hy2_outbound['type'] == 'hysteria2'\n"
    "    assert hy2_outbound['server_ports'] == ['443', '20000:50000']\n"
    "    assert hy2_outbound['hop_interval'] == '30s'\n"
    "    assert hy2_outbound['up_mbps'] == 30 and hy2_outbound['down_mbps'] == 50\n"
    "    assert hy2_outbound['obfs'] == {'type': 'salamander', 'password': 'test-obfs'}\n"
    "    assert hy2_outbound['tls']['server_name'] == 'jp-hy2.jp-relay.local'\n",
    'NekoBox JSON unit contract',
)
replace_once(
    test_hop,
    "    neko_yaml = adapters.render('nekobox', [node])\n",
    "    neko_yaml = adapters.render('nekobox-yaml', [node])\n",
    'NekoBox local YAML unit contract',
)

single = 'tests/test_client_single_node_subscription.py'
replace_once(single, 'import importlib.util\nimport sys\n', 'import importlib.util\nimport json\nimport sys\n', 'single-node json import')
replace_once(
    single,
    "    assert recognition['format'] == 'nekobox' and selected == [item]\n",
    "    assert recognition['format'] == 'nekobox' and selected == [item]\n"
    "    assert recognition['content_type'] == 'application/json; charset=utf-8'\n"
    "    payload = json.loads(adapters.render('nekobox', selected))\n"
    "    outbound = payload['outbounds'][0]\n"
    "    assert outbound['type'] == 'hysteria2'\n"
    "    assert outbound['server_ports'] == ['443', '20000:50000']\n"
    "    assert outbound['hop_interval'] == '30s'\n"
    "    assert outbound['up_mbps'] == 30 and outbound['down_mbps'] == 50\n",
    'single-node NekoBox JSON assertions',
)

conformance = 'tests/conformance.py'
replace_once(
    conformance,
    "                  '参数已收集完毕，开始全自动安装'):\n",
    "                  '参数已收集完毕，开始全自动安装',\n"
    "                  '1. 使用 HTTPS【默认】', '2. 使用 HTTP', '3. 使用 Cloudflare Tunnel'):\n",
    'simplified menu conformance tokens',
)
replace_once(
    conformance,
    "    require('schema == 2 || \"$schema\" == 3' in text or '[[ \"$schema\" == 2 || \"$schema\" == 3 ]]' in text, '现有 schema 3 订阅中心不会无损迁移')\n",
    "    require('schema == 2 || \"$schema\" == 3' in text or '[[ \"$schema\" == 2 || \"$schema\" == 3 ]]' in text, '现有 schema 3 订阅中心不会无损迁移')\n"
    "    for obsolete in ('1. 直接 HTTPS【默认】', '域名由 Caddy 自动申请公共证书',\n"
    "                     '2. 直接 HTTP', '固定 HTTPS 域名（Cloudflare Tunnel）'):\n"
    "        require(obsolete not in text, f'订阅传输菜单仍包含旧说明：{obsolete}')\n",
    'obsolete menu conformance',
)
old_conformance_neko = '''        rendered = adapters.render('clash', center.all_nodes())
        nekobox = adapters.render('nekobox', center.all_nodes())
        require(rendered.startswith('proxies:\n') and nekobox.startswith('proxies:\n'),
                'Clash/NekoBox 没有使用节点型 Clash Meta 格式')
        require('proxy-groups:' not in rendered and 'rules:' not in rendered,
                'Clash 节点订阅仍包含策略组或规则')
        require('up: "30 Mbps"' in rendered and 'down: "50 Mbps"' in rendered,
                'Clash 客户端带宽不是 30/50 Mbps')
        require('ports: "443,20000-50000"' in rendered and 'hop-interval: "20-30"' in rendered,
                'Mihomo 客户端模板缺少随机 HY2 端口跳跃')
        require('up: "30 Mbps"' in nekobox and 'down: "50 Mbps"' in nekobox and 'hop-interval: 30' in nekobox,
                'NekoBox 客户端模板缺少固定 30 秒和 30/50 Mbps')
        require(nekobox != rendered, 'NekoBox 与 Clash 的跳跃间隔仍被错误共用')
'''
new_conformance_neko = '''        rendered = adapters.render('clash', center.all_nodes())
        nekobox = json.loads(adapters.render('nekobox', center.all_nodes()))
        require(rendered.startswith('proxies:\n') and isinstance(nekobox.get('outbounds'), list),
                'Clash YAML 或 NekoBox sing-box JSON 格式错误')
        require('proxy-groups:' not in rendered and 'rules:' not in rendered,
                'Clash 节点订阅仍包含策略组或规则')
        require('up: "30 Mbps"' in rendered and 'down: "50 Mbps"' in rendered,
                'Clash 客户端带宽不是 30/50 Mbps')
        require('ports: "443,20000-50000"' in rendered and 'hop-interval: "20-30"' in rendered,
                'Mihomo 客户端模板缺少随机 HY2 端口跳跃')
        hy2_outbound = next(item for item in nekobox['outbounds'] if item['type'] == 'hysteria2')
        require(hy2_outbound['server_ports'] == ['443', '20000:50000'] and
                hy2_outbound['hop_interval'] == '30s' and hy2_outbound['up_mbps'] == 30 and
                hy2_outbound['down_mbps'] == 50,
                'NekoBox sing-box 出站缺少固定 30 秒、端口跳跃或 30/50 Mbps')
        vless_outbound = next(item for item in nekobox['outbounds'] if item['type'] == 'vless')
        require(vless_outbound['flow'] == 'xtls-rprx-vision' and
                vless_outbound['tls']['reality']['enabled'] is True,
                'NekoBox sing-box 出站缺少 VLESS Reality')
        require(adapters.render('nekobox-yaml', center.all_nodes()).startswith('proxies:\n'),
                '本机隐藏 NekoBox YAML 输出丢失')
'''
replace_once(conformance, old_conformance_neko, new_conformance_neko, 'NekoBox conformance renderer')
replace_once(
    conformance,
    "    require(\"'filename': 'NekoBoxForAndroid.yaml'\" in adapter and \"'format': 'nekobox'\" in adapter,\n"
    "            'NekoBox YAML 渲染器缺失')\n"
    "    require(\"'name': 'NekoBoxForAndroid', 'format': 'nekobox'\" in adapter,\n"
    "            '订阅中心 NekoBox 下发不再是 YAML')\n",
    "    require(\"'filename': 'NekoBoxForAndroid.yaml'\" in adapter and \"'format': 'nekobox-yaml'\" in adapter,\n"
    "            '本机隐藏 NekoBox YAML 渲染器缺失')\n"
    "    require(\"'name': 'NekoBoxForAndroid', 'format': 'nekobox'\" in adapter and\n"
    "            'application/json; charset=utf-8' in adapter and 'render_nekobox_subscription' in adapter,\n"
    "            '订阅中心 NekoBox 没有下发 sing-box JSON')\n",
    'NekoBox conformance source assertions',
)

# Reboot contract now covers Debian cron and Alpine BusyBox/OpenRC without GNU date/flock.
reboot_test = 'tests/test_install_reboot_guard.py'
replace_section(
    reboot_test,
    "require('/etc/cron.d/vvv-daily-reboot' in daily_reboot,\n",
    "\nrequire('systemctl daemon-reload' not in create_xray",
    '''require('/etc/cron.d/vvv-daily-reboot' in daily_reboot,
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
        '每日重启仍依赖 Alpine BusyBox 不稳定的 GNU date -d tomorrow')
require('daily-reboot-install-day' in daily_reboot and
        '10#$current_day <= 10#$install_day' in daily_reboot,
        '每日重启脚本缺少跨 Debian/Alpine 的次日门槛')
require("date '+%H:%M'" in daily_reboot and '06:00' in daily_reboot,
        '每日重启脚本缺少执行时刻二次校验')
require('mkdir "$lock_dir"' in daily_reboot and 'flock' not in daily_reboot,
        '每日重启没有使用 Debian/Alpine 通用的原子目录锁')
require('systemctl reboot --no-wall' in daily_reboot and
        'command -v reboot' in daily_reboot,
        '每日重启脚本没有同时覆盖 systemd 与 Alpine reboot')
require('systemctl enable cron.service' in daily_reboot and
        'systemctl restart cron.service' in daily_reboot and
        'systemctl is-active --quiet cron.service' in daily_reboot,
        'Debian cron 服务没有在安装完成后启用、刷新和验证')

require('systemctl daemon-reload' not in create_xray''',
    'daily reboot test block',
)

# Final runtime keeps local YAML/SN checks and additionally validates actual subscription JSON.
final_runtime = 'tests/final_runtime_validation.sh'
insert_after = '''grep -q 'mport=24443,30000-30031' "$WORK/client-files/NekoBoxForAndroid-基础URI.txt"
'''
json_runtime = '''grep -q 'mport=24443,30000-30031' "$WORK/client-files/NekoBoxForAndroid-基础URI.txt"
python3 - "$CLIENT_ADAPTER" "$CLIENT_PACKAGE_RENDERER" "$WORK/state-active.json" "$WORK/nekobox-subscription.json" <<'PY_NEKOBOX_SUBSCRIPTION'
import importlib.util,json,sys
from pathlib import Path
adapter_path,package_path,state_path,out_path=sys.argv[1:]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
adapters=load('runtime_adapters',adapter_path)
packages=load('runtime_packages',package_path)
state=json.loads(Path(state_path).read_text(encoding='utf-8'))
_,_,nodes=packages.main_nodes(state,'direct','')
payload=adapters.render('nekobox',nodes)
obj=json.loads(payload)
assert isinstance(obj.get('outbounds'),list) and len(obj['outbounds'])==2
hy2=next(row for row in obj['outbounds'] if row['type']=='hysteria2')
assert hy2['server_ports']==['24443','30000:30031']
assert hy2['hop_interval']=='30s'
assert hy2['up_mbps']==30 and hy2['down_mbps']==50
assert hy2['obfs']['type']=='salamander'
vless=next(row for row in obj['outbounds'] if row['type']=='vless')
assert vless['flow']=='xtls-rprx-vision'
assert vless['tls']['reality']['enabled'] is True
Path(out_path).write_text(payload,encoding='utf-8')
PY_NEKOBOX_SUBSCRIPTION
jq -e '.outbounds | length == 2' "$WORK/nekobox-subscription.json" >/dev/null
jq -e '.outbounds[] | select(.type == "hysteria2") | .hop_interval == "30s"' "$WORK/nekobox-subscription.json" >/dev/null
'''
replace_once(final_runtime, insert_after, json_runtime, 'runtime NekoBox subscription JSON')

print('Confirmed NekoBox JSON, menu, and Alpine reboot changes applied.')

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


conformance = 'tests/conformance.py'
replace_section(
    conformance,
    "        rendered = adapters.render('clash', center.all_nodes())\n",
    "        loon = adapters.render('loon', center.all_nodes())\n",
    '''        rendered = adapters.render('clash', center.all_nodes())
        nekobox = json.loads(adapters.render('nekobox', center.all_nodes()))
        require(rendered.startswith('proxies:\\n') and isinstance(nekobox.get('outbounds'), list),
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
        require(adapters.render('nekobox-yaml', center.all_nodes()).startswith('proxies:\\n'),
                '本机隐藏 NekoBox YAML 输出丢失')
''',
    'NekoBox conformance renderer section',
)
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

reboot_test = 'tests/test_install_reboot_guard.py'
replace_section(
    reboot_test,
    "require('/etc/cron.d/vvv-daily-reboot' in daily_reboot,\n",
    "require('systemctl daemon-reload' not in create_xray",
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
        '每日重启仍依赖 GNU date -d tomorrow')
require('daily-reboot-install-day' in daily_reboot and
        '10#$current_day <= 10#$install_day' in daily_reboot,
        '每日重启脚本缺少跨 Debian/Alpine 的次日门槛')
require("date '+%H:%M'" in daily_reboot and '06:00' in daily_reboot,
        '每日重启脚本缺少执行时刻二次校验')
require('mkdir "$lock_dir"' in daily_reboot and 'flock' not in daily_reboot,
        '每日重启没有使用 Debian/Alpine 通用的原子目录锁')
require('systemctl reboot --no-wall' in daily_reboot and 'command -v reboot' in daily_reboot,
        '每日重启脚本没有同时覆盖 systemd 与 Alpine reboot')
require('systemctl enable cron.service' in daily_reboot and
        'systemctl restart cron.service' in daily_reboot and
        'systemctl is-active --quiet cron.service' in daily_reboot,
        'Debian cron 服务没有在安装完成后启用、刷新和验证')

''',
    'daily reboot test section',
)

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

print('Continuation patch applied.')

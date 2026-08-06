#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


def write(path, text):
    (ROOT / path).write_text(text, encoding='utf-8')


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected 1 match, found {count}')
    return text.replace(old, new, 1)


# Restore NekoBox subscription-center output to ClashMeta YAML.
path = 'core-src/client_adapters.py'
text = read(path)
text = replace_once(text, 'VERSION = 8\n', 'VERSION = 9\n', 'adapter version')
start = text.index('\ndef _sing_box_server_ports(')
end = text.index('\ndef render_clash(', start)
text = text[:start] + '\n' + text[end:]
text = replace_once(
    text,
    "    'nekobox': {'render': render_nekobox_subscription, 'content_type': 'application/json; charset=utf-8'},\n"
    "    'nekobox-yaml': {'render': render_nekobox, 'content_type': 'text/yaml; charset=utf-8'},\n",
    "    'nekobox': {'render': render_nekobox, 'content_type': 'text/yaml; charset=utf-8'},\n",
    'NekoBox renderer mapping',
)
text = replace_once(
    text,
    "    {'filename': 'NekoBoxForAndroid.yaml', 'format': 'nekobox-yaml',\n",
    "    {'filename': 'NekoBoxForAndroid.yaml', 'format': 'nekobox',\n",
    'local NekoBox YAML format',
)
smoke_start = text.index("    neko = json.loads(render('nekobox', sample))")
smoke_end = text.index("    neko_sn = render('nekobox-sn', sample).splitlines()", smoke_start)
smoke = """    neko = render('nekobox', sample)
    if not neko.startswith('proxies:\\n'):
        raise RuntimeError('NekoBox subscription is not ClashMeta YAML')
    if 'type: vless' not in neko or 'type: hysteria2' not in neko:
        raise RuntimeError('NekoBox ClashMeta subscription protocol output is incomplete')
    if 'ports: \"443,20000-50000\"' not in neko or 'hop-interval: 30' not in neko:
        raise RuntimeError('NekoBox ClashMeta subscription lost HY2 port hopping')
    if 'hop-interval: \"20-30\"' in neko:
        raise RuntimeError('NekoBox ClashMeta subscription reused Mihomo random hopping')
    if 'up: \"30 Mbps\"' not in neko or 'down: \"50 Mbps\"' not in neko:
        raise RuntimeError('NekoBox ClashMeta subscription lost client bandwidth')
    if 'flow: xtls-rprx-vision' not in neko or 'reality-opts:' not in neko:
        raise RuntimeError('NekoBox ClashMeta subscription lost VLESS Reality')
"""
text = text[:smoke_start] + smoke + text[smoke_end:]
for forbidden in ('render_nekobox_subscription', '_sing_box_server_ports', "'nekobox-yaml'"):
    if forbidden in text:
        raise SystemExit(f'client adapter still contains obsolete JSON token: {forbidden}')
write(path, text)

# Client format tests.
path = 'tests/test_client_port_hopping.py'
text = read(path)
text = replace_once(text, 'import json\n', '', 'remove JSON import from port hopping test')
start = text.index("    neko_subscription = json.loads(adapters.render('nekobox', [node]))")
end = text.index('    loon = adapters.render', start)
block = """    neko_subscription = adapters.render('nekobox', [node])
    assert neko_subscription.startswith('proxies:\\n')
    assert 'type: hysteria2' in neko_subscription
    assert 'ports: \"443,20000-50000\"' in neko_subscription
    assert 'hop-interval: 30' in neko_subscription
    assert 'hop-interval: \"20-30\"' not in neko_subscription
    assert 'up: \"30 Mbps\"' in neko_subscription and 'down: \"50 Mbps\"' in neko_subscription
    assert 'obfs: salamander' in neko_subscription
    assert 'obfs-password: \"test-obfs\"' in neko_subscription
    assert 'sni: jp-hy2.jp-relay.local' in neko_subscription
"""
text = text[:start] + block + text[end:]
text = replace_once(
    text,
    "    neko_yaml = adapters.render('nekobox-yaml', [node])\n",
    "    neko_yaml = adapters.render('nekobox', [node])\n",
    'port hopping local YAML renderer',
)
write(path, text)

path = 'tests/test_client_single_node_subscription.py'
text = read(path)
text = replace_once(text, 'import json\n', '', 'remove JSON import from single-node test')
text = replace_once(
    text,
    "    assert recognition['content_type'] == 'application/json; charset=utf-8'\n"
    "    payload = json.loads(adapters.render('nekobox', selected))\n"
    "    outbound = payload['outbounds'][0]\n"
    "    assert outbound['type'] == 'hysteria2'\n"
    "    assert outbound['server_ports'] == ['443', '20000:50000']\n"
    "    assert outbound['hop_interval'] == '30s'\n"
    "    assert outbound['up_mbps'] == 30 and outbound['down_mbps'] == 50\n",
    "    assert recognition['content_type'] == 'text/yaml; charset=utf-8'\n"
    "    payload = adapters.render('nekobox', selected)\n"
    "    assert payload.startswith('proxies:\\n')\n"
    "    assert 'type: hysteria2' in payload\n"
    "    assert 'ports: \\\"443,20000-50000\\\"' in payload\n"
    "    assert 'hop-interval: 30' in payload\n"
    "    assert 'hop-interval: \\\"20-30\\\"' not in payload\n"
    "    assert 'up: \\\"30 Mbps\\\"' in payload and 'down: \\\"50 Mbps\\\"' in payload\n",
    'single-node NekoBox YAML assertions',
)
write(path, text)

path = 'tests/conformance.py'
text = read(path)
start = text.index("        rendered = adapters.render('clash', center.all_nodes())")
end = text.index("        loon = adapters.render('loon', center.all_nodes())", start)
block = """        rendered = adapters.render('clash', center.all_nodes())
        nekobox = adapters.render('nekobox', center.all_nodes())
        require(rendered.startswith('proxies:\\n') and nekobox.startswith('proxies:\\n'),
                'Clash 或 NekoBox ClashMeta YAML 格式错误')
        require('proxy-groups:' not in rendered and 'rules:' not in rendered,
                'Clash 节点订阅仍包含策略组或规则')
        require('up: \"30 Mbps\"' in rendered and 'down: \"50 Mbps\"' in rendered,
                'Clash 客户端带宽不是 30/50 Mbps')
        require('ports: \"443,20000-50000\"' in rendered and 'hop-interval: \"20-30\"' in rendered,
                'Mihomo 客户端模板缺少随机 HY2 端口跳跃')
        require('type: vless' in nekobox and 'type: hysteria2' in nekobox,
                'NekoBox ClashMeta YAML 协议输出不完整')
        require('ports: \"443,20000-50000\"' in nekobox and 'hop-interval: 30' in nekobox and
                'hop-interval: \"20-30\"' not in nekobox and
                'up: \"30 Mbps\"' in nekobox and 'down: \"50 Mbps\"' in nekobox,
                'NekoBox ClashMeta YAML 缺少固定 30 秒、端口跳跃或 30/50 Mbps')
        require('flow: xtls-rprx-vision' in nekobox and 'reality-opts:' in nekobox,
                'NekoBox ClashMeta YAML 缺少 VLESS Reality')
"""
text = text[:start] + block + text[end:]
text = replace_once(
    text,
    "    require(\"'filename': 'NekoBoxForAndroid.yaml'\" in adapter and \"'format': 'nekobox-yaml'\" in adapter,\n"
    "            '本机隐藏 NekoBox YAML 渲染器缺失')\n"
    "    require(\"'name': 'NekoBoxForAndroid', 'format': 'nekobox'\" in adapter and\n"
    "            'application/json; charset=utf-8' in adapter and 'render_nekobox_subscription' in adapter,\n"
    "            '订阅中心 NekoBox 没有下发 sing-box JSON')\n",
    "    require(\"'filename': 'NekoBoxForAndroid.yaml'\" in adapter and \"'format': 'nekobox'\" in adapter,\n"
    "            '本机 NekoBox YAML 渲染器缺失')\n"
    "    require(\"'name': 'NekoBoxForAndroid', 'format': 'nekobox'\" in adapter and\n"
    "            \"'nekobox': {'render': render_nekobox, 'content_type': 'text/yaml; charset=utf-8'}\" in adapter,\n"
    "            '订阅中心 NekoBox 没有下发 ClashMeta YAML')\n",
    'conformance source assertions',
)
write(path, text)

path = 'tests/final_runtime_validation.sh'
text = read(path)
start = text.index('python3 - "$CLIENT_ADAPTER" "$CLIENT_PACKAGE_RENDERER" "$WORK/state-active.json" "$WORK/nekobox-subscription.json"')
end_marker = 'jq -e \'.outbounds[] | select(.type == "hysteria2") | .hop_interval == "30s"\' "$WORK/nekobox-subscription.json" >/dev/null\n'
end = text.index(end_marker, start) + len(end_marker)
replacement = """python3 - \"$CLIENT_ADAPTER\" \"$CLIENT_PACKAGE_RENDERER\" \"$WORK/state-active.json\" \"$WORK/nekobox-subscription.yaml\" <<'PY_NEKOBOX_SUBSCRIPTION'
import importlib.util,sys
from pathlib import Path
adapter_path,package_path,state_path,out_path=sys.argv[1:]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
adapters=load('runtime_adapters',adapter_path)
packages=load('runtime_packages',package_path)
import json
state=json.loads(Path(state_path).read_text(encoding='utf-8'))
_,_,nodes=packages.main_nodes(state,'direct','')
payload=adapters.render('nekobox',nodes)
assert payload.startswith('proxies:\\n')
assert 'type: vless' in payload and 'type: hysteria2' in payload
assert 'ports: \"24443,30000-30031\"' in payload
assert 'hop-interval: 30' in payload
assert 'hop-interval: \"20-30\"' not in payload
assert 'up: \"30 Mbps\"' in payload and 'down: \"50 Mbps\"' in payload
assert 'flow: xtls-rprx-vision' in payload and 'reality-opts:' in payload
Path(out_path).write_text(payload,encoding='utf-8')
PY_NEKOBOX_SUBSCRIPTION
grep -q '^proxies:$' \"$WORK/nekobox-subscription.yaml\"
grep -q 'hop-interval: 30' \"$WORK/nekobox-subscription.yaml\"
! grep -q 'hop-interval: \"20-30\"' \"$WORK/nekobox-subscription.yaml\"
"""
text = text[:start] + replacement + text[end:]
write(path, text)

# Final guard: the JSON subscription implementation must be gone, while local SN Link stays.
targets = [
    read('core-src/client_adapters.py'),
    read('tests/test_client_port_hopping.py'),
    read('tests/test_client_single_node_subscription.py'),
    read('tests/conformance.py'),
    read('tests/final_runtime_validation.sh'),
]
joined = '\n'.join(targets)
for forbidden in ('render_nekobox_subscription', "'nekobox-yaml'", 'application/json; charset=utf-8',
                  'NekoBox sing-box', "payload['outbounds']"):
    if forbidden in joined:
        raise SystemExit(f'obsolete NekoBox JSON expectation remains: {forbidden}')
adapter = read('core-src/client_adapters.py')
for required in ("'nekobox': {'render': render_nekobox, 'content_type': 'text/yaml; charset=utf-8'}",
                 "'nekobox-sn'", 'NekoBoxForAndroid-SN.txt'):
    if required not in adapter:
        raise SystemExit(f'required NekoBox behavior missing: {required}')

print('Restored NekoBox subscription-center output to ClashMeta YAML only.')

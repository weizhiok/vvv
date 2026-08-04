#!/usr/bin/env python3
from pathlib import Path

path = Path('tests/conformance.py')
text = path.read_text(encoding='utf-8')

replacements = [
    (
        "        'schema': 3, 'role': 'japan-hub', 'protocol_mode': 'dual',\n"
        "        'public_ip': '198.51.100.10', 'listen_port': 443, 'sni': 'www.softbank.jp',\n"
        "        'hy2_limit_mbps': 65, 'direct_base_name': 'JP-198.51.100.10:443',\n",
        "        'schema': 4, 'role': 'japan-hub', 'protocol_mode': 'dual',\n"
        "        'public_ip': '198.51.100.10', 'listen_port': 443, 'sni': 'www.softbank.jp',\n"
        "        'hy2_limit_mbps': 65, 'direct_base_name': 'JP-198.51.100.10:443',\n"
        "        'port_hopping': {'enabled': True, 'ports': '443,20000-50000', 'hop_interval_seconds': 30},\n",
    ),
    (
        "        center.REGISTRY = root / 'registry.json'; center.OVERRIDES = root / 'overrides.json'; center.BACKUP = root / 'missing.py'\n",
        "        center.REGISTRY = root / 'registry.json'; center.OVERRIDES = root / 'overrides.json'; center.ORDER = root / 'node-order.json'; center.BACKUP = root / 'missing.py'\n",
    ),
    (
        "        require('65 Mbps' in rendered, 'HY2 客户端模板未使用节点限速')\n"
        "        shadow = base64.b64decode(adapters.render('shadowrocket', center.all_nodes())).decode()\n",
        "        require('65 Mbps' in rendered, 'HY2 客户端模板未使用节点限速')\n"
        "        require('ports: \"443,20000-50000\"' in rendered and 'hop-interval: 30' in rendered,\n"
        "                'Mihomo 客户端模板缺少 HY2 端口跳跃')\n"
        "        loon = adapters.render('loon', center.all_nodes())\n"
        "        require('server-ports=\"443,20000-50000\"' in loon and 'block-quic=true' in loon,\n"
        "                'Loon 客户端模板缺少 HY2 端口跳跃')\n"
        "        shadow = base64.b64decode(adapters.render('shadowrocket', center.all_nodes())).decode()\n",
    ),
    (
        "    host = read('core-src/host.sh')\n"
        "    landing = read('core-src/landing.sh')\n"
        "    bootstrap = read('core-src/bootstrap.sh')\n"
        "    require('NekoBoxForAndroid.yaml' in host and '【NekoBoxForAndroid（Clash Meta）】' in host,\n"
        "            '主机本地配置缺少 NekoBox')\n"
        "    require('NekoBoxForAndroid.yaml' in landing and '【NekoBoxForAndroid（Clash Meta）】' in landing,\n"
        "            '中转副机本地配置缺少 NekoBox')\n",
        "    host = read('core-src/host.sh')\n"
        "    landing = read('core-src/landing.sh')\n"
        "    adapter = read('core-src/client_adapters.py')\n"
        "    package = read('core-src/client_package_renderer.py')\n"
        "    bootstrap = read('core-src/bootstrap.sh')\n"
        "    require('NekoBoxForAndroid.txt' in adapter and \"'nekobox-uri'\" in adapter,\n"
        "            '本地配置缺少 NekoBox 独立分享链接')\n"
        "    require('Loon-Shadowrocket.txt' in package and 'NekoBoxForAndroid.yaml' in package,\n"
        "            '统一渲染器没有清理旧客户端输出')\n"
        "    require('client_package_renderer.py' in host and 'client_package_renderer.py' in bootstrap,\n"
        "            '主机或安装器没有接入统一客户端渲染器')\n"
        "    require('generate_client_files' in landing and 'CLIENT_PACKAGE_RENDERER' in landing,\n"
        "            '中转副机没有接入统一客户端渲染器')\n",
    ),
]

for old, new in replacements:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'expected one conformance match, found {count}: {old[:100]!r}')
    text = text.replace(old, new, 1)

path.write_text(text, encoding='utf-8')
print('HY2 conformance isolation and output assertions updated.')

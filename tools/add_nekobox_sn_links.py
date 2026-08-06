#!/usr/bin/env python3
from pathlib import Path


def replace_once(path, old, new, label):
    file_path = Path(path)
    text = file_path.read_text(encoding='utf-8')
    if old not in text:
        raise SystemExit(f'{label}: marker not found in {path}')
    file_path.write_text(text.replace(old, new, 1), encoding='utf-8')


replace_once(
    'core-src/client_adapters.py',
    "import base64\nimport json\nimport re\n",
    "import base64\nimport json\nimport re\nimport struct\nimport zlib\n",
    'adapter imports',
)
replace_once('core-src/client_adapters.py', 'VERSION = 6', 'VERSION = 7', 'adapter version')

insert_marker = '''def endpoint_authority(server, port):
'''
sn_code = r'''class _KryoSnWriter:
    """Minimal NekoBox/Kryo ByteBufferOutput-compatible writer for SN links."""

    def __init__(self):
        self.data = bytearray()

    def write_int(self, value):
        self.data.extend(struct.pack('<i', int(value)))

    def write_bool(self, value):
        self.data.append(1 if value else 0)

    def write_string(self, value):
        if value is None:
            self.data.append(0x80)
            return
        value = str(value)
        if not value:
            self.data.append(0x81)
            return

        # Kryo's short-ASCII path writes the final character with bit 7 set.
        if 1 < len(value) < 32 and all(ord(char) <= 0x7F for char in value):
            encoded = bytearray(value.encode('ascii'))
            encoded[-1] |= 0x80
            self.data.extend(encoded)
            return

        # Java String.length() counts UTF-16 code units.
        char_count = len(value.encode('utf-16-le')) // 2
        self._write_utf8_length(char_count + 1)
        self.data.extend(value.encode('utf-8'))

    def _write_utf8_length(self, value):
        if value >> 6 == 0:
            self.data.append(0x80 | value)
        elif value >> 13 == 0:
            self.data.append(0xC0 | (value & 0x3F))
            self.data.append(value >> 6)
        elif value >> 20 == 0:
            self.data.append(0xC0 | (value & 0x3F))
            self.data.append(0x80 | ((value >> 6) & 0x7F))
            self.data.append(value >> 13)
        elif value >> 27 == 0:
            self.data.append(0xC0 | (value & 0x3F))
            self.data.append(0x80 | ((value >> 6) & 0x7F))
            self.data.append(0x80 | ((value >> 13) & 0x7F))
            self.data.append(value >> 20)
        else:
            self.data.append(0xC0 | (value & 0x3F))
            self.data.append(0x80 | ((value >> 6) & 0x7F))
            self.data.append(0x80 | ((value >> 13) & 0x7F))
            self.data.append(0x80 | ((value >> 20) & 0x7F))
            self.data.append(value >> 27)

    def bytes(self):
        return bytes(self.data)


def _sn_link(type_name, payload):
    encoded = base64.urlsafe_b64encode(zlib.compress(payload, 9)).decode().rstrip('=')
    return f'sn://{type_name}?{encoded}'


def _nekobox_vless_sn(node):
    writer = _KryoSnWriter()
    writer.write_int(4)  # StandardV2RayBean serialization version.
    writer.write_string(node['server'])
    writer.write_int(node['port'])
    writer.write_string(node['uuid'])
    writer.write_string('xtls-rprx-vision')
    writer.write_int(-1)  # VMessBean alterId=-1 means VLESS.
    writer.write_string('tcp')
    writer.write_string('tls')
    writer.write_string(node['sni'])
    writer.write_string('')  # ALPN
    writer.write_string('')  # Certificates
    writer.write_bool(True)  # allowInsecure
    writer.write_string('chrome')
    writer.write_string(node['public_key'])
    writer.write_string(node['short_id'])
    writer.write_bool(False)  # ECH
    writer.write_string('')
    writer.write_int(2)  # packetEncoding=xudp
    writer.write_bool(False)  # mux
    writer.write_bool(False)  # mux padding
    writer.write_int(0)  # mux type
    writer.write_int(1)  # mux concurrency
    writer.write_int(1)  # AbstractBean extra version
    writer.write_string(node['name'])
    writer.write_string('')  # custom outbound JSON
    writer.write_string('')  # custom config JSON
    return _sn_link('vmess', writer.bytes())


def _nekobox_hy2_sn(node):
    writer = _KryoSnWriter()
    writer.write_int(7)  # HysteriaBean serialization version.
    writer.write_string(node['server'])
    writer.write_int(1080)  # HysteriaBean uses serverPorts; exported serverPort stays default.
    writer.write_int(2)  # Hysteria 2
    writer.write_int(0)  # auth payload type used by NekoBox exports
    writer.write_string(node['password'])
    writer.write_int(0)  # UDP
    writer.write_string(node['obfs_password'])
    writer.write_string(node['sni'])
    writer.write_string('')  # ALPN
    writer.write_int(client_up_mbps(node))
    writer.write_int(client_down_mbps(node))
    writer.write_bool(True)  # allowInsecure
    writer.write_string('')  # CA text
    writer.write_int(0)  # stream receive window
    writer.write_int(0)  # connection receive window
    writer.write_bool(False)  # disable MTU discovery
    writer.write_int(fixed_hop_interval(node))
    writer.write_string(hy2_ports(node))
    writer.write_int(1)  # AbstractBean extra version
    writer.write_string(node['name'])
    writer.write_string('')
    writer.write_string('')
    return _sn_link('hysteria', writer.bytes())


def render_nekobox_sn_links(nodes):
    lines = [
        _nekobox_vless_sn(node) if node['protocol'] == 'vless' else _nekobox_hy2_sn(node)
        for node in nodes
    ]
    return '\n'.join(lines) + ('\n' if lines else '')


'''
replace_once(
    'core-src/client_adapters.py',
    insert_marker,
    sn_code + insert_marker,
    'SN writer insertion',
)

replace_once(
    'core-src/client_adapters.py',
    "    'nekobox': {'render': render_nekobox, 'content_type': 'text/yaml; charset=utf-8'},\n",
    "    'nekobox': {'render': render_nekobox, 'content_type': 'text/yaml; charset=utf-8'},\n"
    "    'nekobox-sn': {'render': render_nekobox_sn_links, 'content_type': 'text/plain; charset=utf-8'},\n",
    'SN renderer registry',
)

old_outputs = '''LOCAL_OUTPUTS = [
    {'filename': 'Quantumult-X.conf', 'format': 'quantumultx', 'display_name': 'Quantumult X'},
    {'filename': 'Loon.conf', 'format': 'loon', 'display_name': 'Loon'},
    {'filename': 'Loon-Import.txt', 'format': 'loon-import', 'display_name': 'Loon 正式导入链接'},
    {'filename': 'Shadowrocket.txt', 'format': 'shadowrocket-uri', 'display_name': 'Shadowrocket 分享链接'},
    {'filename': 'Clash-Verge-Rev.yaml', 'format': 'clash', 'display_name': 'Clash Verge Rev / Mihomo'},
    {'filename': 'NekoBoxForAndroid.yaml', 'format': 'nekobox', 'display_name': 'NekoBoxForAndroid'},
    {'filename': 'NekoBoxForAndroid.txt', 'format': 'nekobox-import', 'display_name': 'NekoBoxForAndroid 单节点订阅'},
    {'filename': 'NekoBoxForAndroid-基础URI.txt', 'format': 'nekobox-uri',
     'display_name': 'NekoBoxForAndroid 基础分享链接', 'display': False},
]
'''
new_outputs = '''LOCAL_OUTPUTS = [
    {'filename': 'Quantumult-X.conf', 'format': 'quantumultx', 'display_name': 'Quantumult X'},
    {'filename': 'Loon.conf', 'format': 'loon', 'display_name': 'Loon'},
    {'filename': 'Loon-Import.txt', 'format': 'loon-import',
     'display_name': 'Loon 正式导入链接', 'display': False},
    {'filename': 'Shadowrocket.txt', 'format': 'shadowrocket-uri', 'display_name': 'Shadowrocket 分享链接'},
    {'filename': 'NekoBoxForAndroid-SN.txt', 'format': 'nekobox-sn', 'display_name': 'NekoBox For Android'},
    {'filename': 'Clash-Verge-Rev.yaml', 'format': 'clash', 'display_name': 'Clash Verge Rev / Mihomo'},
    {'filename': 'NekoBoxForAndroid.yaml', 'format': 'nekobox',
     'display_name': 'NekoBoxForAndroid YAML', 'display': False},
    {'filename': 'NekoBoxForAndroid.txt', 'format': 'nekobox-import',
     'display_name': 'NekoBoxForAndroid 单节点订阅', 'display': False},
    {'filename': 'NekoBoxForAndroid-基础URI.txt', 'format': 'nekobox-uri',
     'display_name': 'NekoBoxForAndroid 基础分享链接', 'display': False},
]
'''
replace_once('core-src/client_adapters.py', old_outputs, new_outputs, 'local output order')

replace_once(
    'core-src/client_adapters.py',
    "    neko = render('nekobox', sample)\n"
    "    if 'hop-interval: 30' not in neko or 'hop-interval: \"20-30\"' in neko:\n"
    "        raise RuntimeError('NekoBox must use fixed 30-second hopping')\n",
    "    neko = render('nekobox', sample)\n"
    "    if 'hop-interval: 30' not in neko or 'hop-interval: \"20-30\"' in neko:\n"
    "        raise RuntimeError('NekoBox subscription YAML must use fixed 30-second hopping')\n"
    "    neko_sn = render('nekobox-sn', sample).splitlines()\n"
    "    if len(neko_sn) != 2 or not neko_sn[0].startswith('sn://vmess?') or not neko_sn[1].startswith('sn://hysteria?'):\n"
    "        raise RuntimeError('NekoBox local SN links are missing')\n",
    'SN smoke rendering',
)

replace_once(
    'core-src/client_adapters.py',
    "    if 'NekoBoxForAndroid.yaml' not in names:\n"
    "        raise RuntimeError('NekoBox full local YAML output is missing')\n"
    "    return True\n",
    "    if 'NekoBoxForAndroid.yaml' not in names or 'NekoBoxForAndroid-SN.txt' not in names:\n"
    "        raise RuntimeError('NekoBox local YAML or SN output is missing')\n"
    "    display_order = [item['display_name'] for item in local_outputs() if item.get('display', True)]\n"
    "    if display_order != ['Quantumult X', 'Loon', 'Shadowrocket 分享链接',\n"
    "                         'NekoBox For Android', 'Clash Verge Rev / Mihomo']:\n"
    "        raise RuntimeError('local client display order changed')\n"
    "    return True\n",
    'local display order smoke test',
)

replace_once(
    'tests/test_client_port_hopping.py',
    "    node = sample_node()\n",
    "    node = sample_node()\n"
    "    vless_sn_node = {\n"
    "        'name': 'JP-VLESS-203.0.113.1:443', 'protocol': 'vless',\n"
    "        'server': '203.0.113.1', 'port': 443,\n"
    "        'uuid': '11111111-1111-4111-8111-111111111111',\n"
    "        'sni': 'www.softbank.jp',\n"
    "        'public_key': 'PublicKeyAudit-1234567890123456789012345678',\n"
    "        'short_id': '0123456789abcdef', 'udp': True,\n"
    "    }\n"
    "    hy2_sn_node = dict(node, obfs_password='test-obfs-password')\n"
    "    sn_links = adapters.render('nekobox-sn', [vless_sn_node, hy2_sn_node]).splitlines()\n"
    "    assert sn_links == [\n"
    "        'sn://vmess?eNpjYWBgMDIw1jPQMzQ01tu4m5GBYakhFOiCCRMQYQHjwkBFSU6xblFBUYVuWWZxZv67_0BQkvyhJOdzeXm5XnF-WklSYl62XtaHxkbG5Iyi_NynawJKk3Iyk71TKx1LUzJLdA2NjE1MzcwtLA0wWQihxKTklNRnDI1MDDDACMVeAbphPq7BwboI5xtamZhsbmwEAE3gPFY',\n"
    "        'sn://hysteria?eNpjZ2BgMDIw1jPQMzQ01ttowcLAwMQAASWpxSW6BYnFxeX5RU_gAvlJacVw0awC3YxKIz0gVZSak1ipl5OfnPimUQ5kJhAzNjLAAEjIxMRYx8gACHRNgcQGRqCQV4CuR6SRLsIBhlYmJpsbGwGtkyOX',\n"
    "    ], sn_links\n"
    "    assert adapters.render('nekobox', [node]).startswith('proxies:\\n')\n",
    'exact SN fixture test',
)

replace_once(
    'tests/test_client_port_hopping.py',
    "        assert '【NekoBoxForAndroid】' in summary\n"
    "        assert (out / 'NekoBoxForAndroid-基础URI.txt').read_text(encoding='utf-8').startswith('hy2://')\n",
    "        assert '【NekoBox For Android】' in summary\n"
    "        assert (out / 'NekoBoxForAndroid-SN.txt').read_text(encoding='utf-8').startswith('sn://hysteria?')\n"
    "        labels = ['【Loon】', '【Shadowrocket 分享链接】', '【NekoBox For Android】',\n"
    "                  '【Clash Verge Rev / Mihomo】']\n"
    "        assert [summary.index(label) for label in labels] == sorted(summary.index(label) for label in labels)\n"
    "        assert (out / 'NekoBoxForAndroid-基础URI.txt').read_text(encoding='utf-8').startswith('hy2://')\n",
    'visible SN summary test',
)

replace_once(
    'tests/client_upgrade_isolation_validation.py',
    "    neko_yaml = directory / 'NekoBoxForAndroid.yaml'\n"
    "    neko = directory / 'NekoBoxForAndroid.txt'\n",
    "    neko_yaml = directory / 'NekoBoxForAndroid.yaml'\n"
    "    neko_sn = directory / 'NekoBoxForAndroid-SN.txt'\n"
    "    neko = directory / 'NekoBoxForAndroid.txt'\n",
    'isolation SN file variable',
)
replace_once(
    'tests/client_upgrade_isolation_validation.py',
    "    require('hop-interval: \"20-30\"' not in neko_yaml_text,\n"
    "            f'{role} NekoBox YAML 错误复用了 Mihomo 随机跳跃')\n"
    "    require(neko.is_file(), f'{role}缺少 NekoBox 单节点订阅输出')\n",
    "    require('hop-interval: \"20-30\"' not in neko_yaml_text,\n"
    "            f'{role} NekoBox YAML 错误复用了 Mihomo 随机跳跃')\n"
    "    require(neko_sn.is_file(), f'{role}缺少 NekoBox SN LINK 输出')\n"
    "    sn_lines = neko_sn.read_text(encoding='utf-8').splitlines()\n"
    "    require(any(line.startswith('sn://vmess?') for line in sn_lines),\n"
    "            f'{role} NekoBox SN LINK 缺少 VLESS')\n"
    "    require(any(line.startswith('sn://hysteria?') for line in sn_lines),\n"
    "            f'{role} NekoBox SN LINK 缺少 Hysteria 2')\n"
    "    require(neko.is_file(), f'{role}缺少 NekoBox 单节点订阅输出')\n",
    'isolation SN verification',
)
replace_once(
    'tests/client_upgrade_isolation_validation.py',
    "    require('proxy-groups:' not in clash and 'rules:' not in clash,\n"
    "            f'{role} Mihomo 输出仍包含策略组或规则')\n",
    "    require('proxy-groups:' not in clash and 'rules:' not in clash,\n"
    "            f'{role} Mihomo 输出仍包含策略组或规则')\n"
    "    summary = (directory / '客户端节点.txt').read_text(encoding='utf-8')\n"
    "    labels = ['【Quantumult X】', '【Loon】', '【Shadowrocket 分享链接】',\n"
    "              '【NekoBox For Android】', '【Clash Verge Rev / Mihomo】']\n"
    "    positions = [summary.index(label) for label in labels]\n"
    "    require(positions == sorted(positions), f'{role}本机客户端显示顺序错误')\n",
    'isolation display order',
)
replace_once(
    'tests/client_upgrade_isolation_validation.py',
    "        require('NekoBoxForAndroid' in text and 'Shadowrocket' in text and 'Quantumult X' in text, '主机汇总缺少客户端')\n",
    "        require('NekoBox For Android' in text and 'Shadowrocket' in text and 'Quantumult X' in text, '主机汇总缺少客户端')\n",
    'main summary label',
)
replace_once(
    'tests/client_upgrade_isolation_validation.py',
    "        require('Shadowrocket' in landing_summary and 'NekoBoxForAndroid' in landing_summary,\n"
    "                '中转副机汇总缺少 Shadowrocket 或 NekoBoxForAndroid')\n",
    "        require('Shadowrocket' in landing_summary and 'NekoBox For Android' in landing_summary,\n"
    "                '中转副机汇总缺少 Shadowrocket 或 NekoBox For Android')\n",
    'landing summary label',
)

old_conf = '''    require('NekoBoxForAndroid.txt' in adapter and "'nekobox-uri'" in adapter,
            '本地配置缺少 NekoBox 独立分享链接')
    require("'filename': 'NekoBoxForAndroid.yaml'" in adapter and "'format': 'nekobox'" in adapter,
            '本机汇总缺少 NekoBox 完整 YAML 输出')
    require('Loon-Shadowrocket.txt' in package and 'NekoBoxForAndroid.yaml' not in package,
            '统一渲染器仍把 NekoBox YAML 当作旧文件清理')
'''
new_conf = '''    require('NekoBoxForAndroid-SN.txt' in adapter and "'nekobox-sn'" in adapter,
            '本地配置缺少 NekoBox SN LINK')
    require("'filename': 'NekoBoxForAndroid.yaml'" in adapter and "'format': 'nekobox'" in adapter,
            'NekoBox YAML 渲染器缺失')
    require("'name': 'NekoBoxForAndroid', 'format': 'nekobox'" in adapter,
            '订阅中心 NekoBox 下发不再是 YAML')
    display_tokens = [
        "'display_name': 'Quantumult X'", "'display_name': 'Loon'",
        "'display_name': 'Shadowrocket 分享链接'", "'display_name': 'NekoBox For Android'",
        "'display_name': 'Clash Verge Rev / Mihomo'",
    ]
    display_positions = [adapter.index(token) for token in display_tokens]
    require(display_positions == sorted(display_positions), '本机客户端显示顺序错误')
    require('Loon-Shadowrocket.txt' in package and 'NekoBoxForAndroid.yaml' not in package,
            '统一渲染器错误清理 NekoBox YAML')
'''
replace_once('tests/conformance.py', old_conf, new_conf, 'conformance SN contract')

replace_once(
    'tests/final_runtime_validation.sh',
    '[[ -s "$WORK/client-files/NekoBoxForAndroid.yaml" ]]\n',
    '[[ -s "$WORK/client-files/NekoBoxForAndroid.yaml" ]]\n'
    '[[ -s "$WORK/client-files/NekoBoxForAndroid-SN.txt" ]]\n',
    'runtime SN file existence',
)
replace_once(
    'tests/final_runtime_validation.sh',
    "grep -q '【NekoBoxForAndroid】' \"$WORK/client-files/客户端节点.txt\"\n",
    "grep -q '【NekoBox For Android】' \"$WORK/client-files/客户端节点.txt\"\n"
    "grep -q '^sn://vmess?' \"$WORK/client-files/NekoBoxForAndroid-SN.txt\"\n"
    "grep -q '^sn://hysteria?' \"$WORK/client-files/NekoBoxForAndroid-SN.txt\"\n",
    'runtime SN summary and schemes',
)

# Final static contract checks.
adapter = Path('core-src/client_adapters.py').read_text(encoding='utf-8')
assert 'VERSION = 7' in adapter
assert "'nekobox-sn'" in adapter
assert 'sn://{type_name}?' in adapter
assert "'format': 'nekobox'" in adapter  # Subscription YAML remains intact.
assert adapter.index("'display_name': 'NekoBox For Android'") < adapter.index("'display_name': 'Clash Verge Rev / Mihomo'")

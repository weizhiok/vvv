#!/usr/bin/env python3
"""Pure VVV client-recognition and rendering module."""

import base64
import json
import re
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

VERSION = 5
DEFAULT_UPGRADE_URL = (
    "https://raw.githubusercontent.com/weizhiok/vvv/client-support/client_upgrade.py"
)

CLIENT_UP_MBPS = 30
CLIENT_DOWN_MBPS = 50
FIXED_HOP_INTERVAL_SECONDS = 30
MIHOMO_HOP_INTERVAL = "20-30"


def b64std(text):
    return base64.b64encode(text.encode()).decode()


def loon_q(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def loon_name(value):
    return str(value).replace('=', '-').replace('\n', ' ').replace('\r', ' ')


def vless_uri(node):
    params = [
        ('encryption', 'none'),
        ('flow', 'xtls-rprx-vision'),
        ('security', 'reality'),
        ('sni', node['sni']),
        ('fp', 'chrome'),
        ('pbk', node['public_key']),
        ('sid', node['short_id']),
        ('type', 'tcp'),
        ('headerType', 'none'),
    ]
    return (
        f"vless://{node['uuid']}@{node['server']}:{node['port']}?"
        f"{urlencode(params)}#{quote(node['name'], safe='')}"
    )


def hy2_ports(node):
    value = str(node.get('ports') or node.get('port_hopping_ports') or node['port']).strip()
    return value or str(node['port'])


def fixed_hop_interval(node):
    try:
        value = int(node.get('hop_interval_seconds') or FIXED_HOP_INTERVAL_SECONDS)
    except (TypeError, ValueError):
        value = FIXED_HOP_INTERVAL_SECONDS
    return max(5, value)


def client_up_mbps(node):
    try:
        return max(1, int(node.get('client_up_mbps') or CLIENT_UP_MBPS))
    except (TypeError, ValueError):
        return CLIENT_UP_MBPS


def client_down_mbps(node):
    try:
        return max(1, int(node.get('client_down_mbps') or CLIENT_DOWN_MBPS))
    except (TypeError, ValueError):
        return CLIENT_DOWN_MBPS


def endpoint_authority(server, port):
    host = str(server)
    if ':' in host and not host.startswith('['):
        host = f'[{host}]'
    return f'{host}:{port}'


def generic_hy2_uri(node, scheme='hysteria2'):
    params = [
        ('obfs', 'salamander'),
        ('obfs-password', node['obfs_password']),
        ('sni', node['sni']),
        ('insecure', '1'),
        ('mport', hy2_ports(node)),
    ]
    if node.get('pin'):
        params.append(('pinSHA256', node['pin']))
    return (
        f"{scheme}://{quote(node['password'], safe='')}@"
        f"{endpoint_authority(node['server'], node['port'])}/?"
        f"{urlencode(params, safe=',-')}#{quote(node['name'], safe='')}"
    )


def shadowrocket_hy2_uri(node):
    params = [
        ('peer', node['sni']),
        ('insecure', '1'),
        ('obfs', 'salamander'),
        ('obfs-password', node['obfs_password']),
        ('fastopen', '1'),
        ('upmbps', str(client_up_mbps(node))),
        ('downmbps', str(client_down_mbps(node))),
    ]
    if node.get('pin'):
        params.append(('hpkp', str(node['pin']).replace(':', '').lower()))
    params.append(('mport', hy2_ports(node)))
    return (
        f"hysteria2://{quote(node['password'], safe='')}@"
        f"{endpoint_authority(node['server'], node['port'])}?"
        f"{urlencode(params, safe=',-')}#{quote(node['name'], safe='')}"
    )


def nekobox_hy2_uri(node):
    params = [
        ('sni', node['sni']),
        ('insecure', '1'),
        ('obfs', 'salamander'),
        ('obfs-password', node['obfs_password']),
        ('mport', hy2_ports(node)),
    ]
    return (
        f"hy2://{quote(node['password'], safe='')}@"
        f"{endpoint_authority(node['server'], node['port'])}/?"
        f"{urlencode(params, safe=',-')}#{quote(node['name'], safe='')}"
    )


def single_node_subscription_url(node, format_name):
    base = str(node.get('subscription_url') or '').strip()
    node_id = str(node.get('id') or '').strip()
    if not base or not re.fullmatch(r'[0-9a-f]{24}', node_id):
        return ''
    parsed = urlsplit(base)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({'format': format_name, 'node': node_id})
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def render_share(nodes):
    text = '\n'.join(
        vless_uri(node) if node['protocol'] == 'vless' else generic_hy2_uri(node)
        for node in nodes
    )
    return text + ('\n' if text else '')


def render_shadowrocket_uris(nodes):
    lines = [
        vless_uri(node) if node['protocol'] == 'vless' else shadowrocket_hy2_uri(node)
        for node in nodes
    ]
    return '\n'.join(lines) + ('\n' if lines else '')


def render_nekobox_uris(nodes):
    lines = [
        vless_uri(node) if node['protocol'] == 'vless' else nekobox_hy2_uri(node)
        for node in nodes
    ]
    return '\n'.join(lines) + ('\n' if lines else '')


def render_loon_import(nodes):
    lines = []
    for node in nodes:
        url = single_node_subscription_url(node, 'loon')
        if url:
            lines.append('loon://import?nodelist=' + quote(url, safe=''))
    return '\n'.join(lines) + ('\n' if lines else '')


def render_nekobox_import(nodes):
    lines = []
    for node in nodes:
        url = single_node_subscription_url(node, 'nekobox')
        if url:
            lines.append(url)
    return '\n'.join(lines) + ('\n' if lines else '')


def render_quantumultx(nodes):
    lines = []
    for node in nodes:
        if node['protocol'] != 'vless':
            continue
        lines.append(
            f"vless={node['server']}:{node['port']}, method=none, password={node['uuid']}, "
            f"obfs=over-tls, obfs-host={node['sni']}, reality-base64-pubkey={node['public_key']}, "
            f"reality-hex-shortid={node['short_id']}, vless-flow=xtls-rprx-vision, fast-open=false, "
            f"udp-relay={'true' if node.get('udp', True) else 'false'}, tag={node['name']}"
        )
    return '\n'.join(lines) + ('\n' if lines else '')


def render_loon(nodes):
    lines = []
    for node in nodes:
        if node['protocol'] == 'vless':
            lines.append(
                f"{loon_name(node['name'])} = VLESS,{node['server']},{node['port']},{loon_q(node['uuid'])},"
                f"transport=tcp,flow=xtls-rprx-vision,public-key={loon_q(node['public_key'])},"
                f"short-id={node['short_id']},udp={'true' if node.get('udp', True) else 'false'},"
                f"over-tls=true,sni={node['sni']},skip-cert-verify=true"
            )
        else:
            lines.append(
                f"{loon_name(node['name'])} = Hysteria2,{node['server']},{node['port']},"
                f"{node['password']},sni={node['sni']},skip-cert-verify=true,fast-open=true,"
                f"salamander-password={node['obfs_password']},server-ports={loon_q(hy2_ports(node))},"
                f"hop-interval={fixed_hop_interval(node)},udp=true,block-quic=true,"
                f"download-bandwidth={client_down_mbps(node)}"
            )
    return '\n'.join(lines) + ('\n' if lines else '')


def render_shadowrocket(nodes):
    return b64std(render_shadowrocket_uris(nodes)) + '\n'


def render_mihomo_proxies(nodes, hop_interval, include_fast_open=False):
    lines = ['proxies:']
    for node in nodes:
        if node['protocol'] == 'vless':
            lines += [
                f'  - name: {json.dumps(node["name"], ensure_ascii=False)}',
                '    type: vless',
                f'    server: {node["server"]}',
                f'    port: {node["port"]}',
                f'    uuid: {node["uuid"]}',
                '    network: tcp',
                f'    udp: {str(node.get("udp", True)).lower()}',
                '    tls: true',
                '    flow: xtls-rprx-vision',
                '    encryption: ""',
                f'    servername: {node["sni"]}',
                '    client-fingerprint: chrome',
                '    skip-cert-verify: true',
                '    reality-opts:',
                f'      public-key: {node["public_key"]}',
                f'      short-id: "{node["short_id"]}"',
            ]
        else:
            lines += [
                f'  - name: {json.dumps(node["name"], ensure_ascii=False)}',
                '    type: hysteria2',
                f'    server: {node["server"]}',
                f'    port: {node["port"]}',
                f'    ports: {json.dumps(hy2_ports(node))}',
                f'    hop-interval: {json.dumps(str(hop_interval)) if isinstance(hop_interval, str) else int(hop_interval)}',
                f'    password: {json.dumps(node["password"])}',
                f'    up: "{client_up_mbps(node)} Mbps"',
                f'    down: "{client_down_mbps(node)} Mbps"',
                '    obfs: salamander',
                f'    obfs-password: {json.dumps(node["obfs_password"])}',
                f'    sni: {node["sni"]}',
                '    skip-cert-verify: true',
                '    alpn: [h3]',
                '    udp: true',
            ]
            if include_fast_open:
                lines.append('    fast-open: true')
    lines.append('')
    return '\n'.join(lines)


def render_clash(nodes):
    return render_mihomo_proxies(nodes, MIHOMO_HOP_INTERVAL)


def render_nekobox(nodes):
    return render_mihomo_proxies(nodes, FIXED_HOP_INTERVAL_SECONDS)


RENDERERS = {
    'clash': {'render': render_clash, 'content_type': 'text/yaml; charset=utf-8'},
    'nekobox': {'render': render_nekobox, 'content_type': 'text/yaml; charset=utf-8'},
    'nekobox-uri': {'render': render_nekobox_uris, 'content_type': 'text/plain; charset=utf-8'},
    'nekobox-import': {'render': render_nekobox_import, 'content_type': 'text/plain; charset=utf-8'},
    'quantumultx': {'render': render_quantumultx, 'content_type': 'text/plain; charset=utf-8'},
    'loon': {'render': render_loon, 'content_type': 'text/plain; charset=utf-8'},
    'loon-import': {'render': render_loon_import, 'content_type': 'text/plain; charset=utf-8'},
    'shadowrocket': {'render': render_shadowrocket, 'content_type': 'text/plain; charset=utf-8'},
    'shadowrocket-uri': {'render': render_shadowrocket_uris, 'content_type': 'text/plain; charset=utf-8'},
    'share': {'render': render_share, 'content_type': 'text/plain; charset=utf-8'},
}

LOCAL_OUTPUTS = [
    {'filename': 'Quantumult-X.conf', 'format': 'quantumultx', 'display_name': 'Quantumult X'},
    {'filename': 'Loon.conf', 'format': 'loon', 'display_name': 'Loon'},
    {'filename': 'Loon-Import.txt', 'format': 'loon-import', 'display_name': 'Loon 正式导入链接'},
    {'filename': 'Shadowrocket.txt', 'format': 'shadowrocket-uri', 'display_name': 'Shadowrocket 分享链接'},
    {'filename': 'Clash-Verge-Rev.yaml', 'format': 'clash', 'display_name': 'Clash Verge Rev / Mihomo'},
    {'filename': 'NekoBoxForAndroid.txt', 'format': 'nekobox-import', 'display_name': 'NekoBoxForAndroid 单节点订阅'},
    {'filename': 'NekoBoxForAndroid-基础URI.txt', 'format': 'nekobox-uri',
     'display_name': 'NekoBoxForAndroid 基础分享链接', 'display': False},
]

CLIENT_RULES = [
    {'name': 'NekoBoxForAndroid', 'format': 'nekobox', 'user_agent': [r'nekobox/android(?:/[0-9.]+)?']},
    {'name': 'Clash Verge Rev', 'format': 'clash', 'user_agent': [r'clash[- ]?verge', r'clash-verge-rev']},
    {'name': 'Mihomo', 'format': 'clash', 'user_agent': [r'\bmihomo\b', r'\bclash\b']},
    {'name': 'Quantumult X', 'format': 'quantumultx', 'user_agent': [r'quantumult[ -]?x', r'quantumult%20x']},
    {'name': 'Loon', 'format': 'loon', 'user_agent': [r'\bloon\b']},
    {'name': 'Shadowrocket', 'format': 'shadowrocket', 'user_agent': [r'shadowrocket']},
]


def normalize_headers(headers):
    return {str(key).lower(): str(value) for key, value in dict(headers).items()}


def detect_client(headers):
    values = normalize_headers(headers)
    combined = values.get('user-agent', '') + '\n' + values.get('accept', '')
    for rule in CLIENT_RULES:
        if any(re.search(pattern, combined, re.IGNORECASE) for pattern in rule.get('user_agent', [])):
            return {
                'name': rule['name'],
                'format': rule['format'],
                'content_type': RENDERERS[rule['format']]['content_type'],
            }
    return None


def render(format_name, nodes):
    renderer = RENDERERS.get(format_name)
    if not renderer:
        raise ValueError(f'unsupported subscription format: {format_name}')
    return renderer['render'](nodes)


def available_formats():
    return sorted(RENDERERS)


def local_outputs():
    return [dict(item) for item in LOCAL_OUTPUTS]


def smoke_test():
    sample = [
        {
            'name': 'JP-VLESS-127.0.0.1:443', 'protocol': 'vless', 'server': '127.0.0.1',
            'port': 443, 'uuid': '11111111-1111-4111-8111-111111111111',
            'sni': 'www.softbank.jp', 'public_key': 'PublicKeyAudit',
            'short_id': '0123456789abcdef', 'udp': True,
            'id': '1' * 24, 'subscription_url': 'https://sub.example.test/secret',
        },
        {
            'name': 'JP-HY2-127.0.0.1:443', 'protocol': 'hysteria2', 'server': '127.0.0.1',
            'port': 443, 'ports': '443,20000-50000', 'hop_interval_seconds': 30,
            'password': 'password', 'sni': 'jp-hy2.jp-relay.local',
            'obfs_password': 'salamander', 'pin': 'aa' * 32, 'limit_mbps': 50,
            'client_up_mbps': 30, 'client_down_mbps': 50, 'udp': True,
            'id': '2' * 24, 'subscription_url': 'https://sub.example.test/secret',
        },
    ]
    for format_name in available_formats():
        output = render(format_name, sample)
        if not isinstance(output, str):
            raise RuntimeError(f'{format_name} renderer returned non-text output')
    detected = detect_client({'User-Agent': 'NekoBox/Android/1.4.2 (Prefer ClashMeta Format)'})
    if not detected or detected.get('name') != 'NekoBoxForAndroid' or detected.get('format') != 'nekobox':
        raise RuntimeError('NekoBoxForAndroid 1.4.2 user agent was not recognized')
    expected_loon = (
        'JP-HY2-127.0.0.1:443 = Hysteria2,127.0.0.1,443,password,'
        'sni=jp-hy2.jp-relay.local,skip-cert-verify=true,fast-open=true,'
        'salamander-password=salamander,server-ports="443,20000-50000",'
        'hop-interval=30,udp=true,block-quic=true,download-bandwidth=50'
    )
    if expected_loon not in render('loon', sample).splitlines():
        raise RuntimeError('Loon Hysteria 2 contract changed')
    clash = render('clash', sample)
    if 'hop-interval: "20-30"' not in clash or 'up: "30 Mbps"' not in clash:
        raise RuntimeError('Mihomo Hysteria 2 client tuning fields are missing')
    if 'proxy-groups:' in clash or 'rules:' in clash or 'mixed-port:' in clash:
        raise RuntimeError('Clash node-only output contains full-profile fields')
    neko = render('nekobox', sample)
    if 'hop-interval: 30' not in neko or 'hop-interval: "20-30"' in neko:
        raise RuntimeError('NekoBox must use fixed 30-second hopping')
    shadow = render('shadowrocket-uri', sample)
    for value in ('peer=', 'fastopen=1', 'upmbps=30', 'downmbps=50', 'hpkp=', 'mport='):
        if value not in shadow:
            raise RuntimeError(f'Shadowrocket parameter missing: {value}')
    basic_neko = render('nekobox-uri', sample)
    if ':443,20000-50000/' in basic_neko or 'mport=443,20000-50000' not in basic_neko:
        raise RuntimeError('NekoBox URI must keep one authority port and use mport')
    if not render('loon-import', sample).startswith('loon://import?nodelist='):
        raise RuntimeError('Loon import scheme is missing')
    if 'format=nekobox' not in render('nekobox-import', sample):
        raise RuntimeError('NekoBox single-node subscription link is missing')
    names = [item['filename'] for item in local_outputs()]
    if len(names) != len(set(names)) or 'Shadowrocket.txt' not in names:
        raise RuntimeError('local output manifest is invalid')
    if 'Loon-Shadowrocket.txt' in names or 'NekoBoxForAndroid.yaml' in names:
        raise RuntimeError('obsolete duplicated local outputs are still present')
    return True


if __name__ == '__main__':
    smoke_test()
    print(json.dumps({'ok': True, 'version': VERSION, 'formats': available_formats()}, ensure_ascii=False))

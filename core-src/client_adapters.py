#!/usr/bin/env python3
"""Pure VVV client-recognition and rendering module."""

import base64
import json
import re
from urllib.parse import quote, urlencode

VERSION = 4
DEFAULT_UPGRADE_URL = (
    "https://raw.githubusercontent.com/weizhiok/vvv/client-support/client_upgrade.py"
)


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


def hy2_hop_interval(node):
    try:
        value = int(node.get('hop_interval_seconds') or 30)
    except (TypeError, ValueError):
        value = 30
    return max(5, value)


def endpoint_authority(server, ports):
    host = str(server)
    if ':' in host and not host.startswith('['):
        host = f'[{host}]'
    return f'{host}:{ports}'


def hy2_uri(node, scheme='hysteria2'):
    params = [
        ('obfs', 'salamander'),
        ('obfs-password', node['obfs_password']),
        ('sni', node['sni']),
        ('insecure', '1'),
    ]
    if node.get('pin'):
        params.append(('pinSHA256', node['pin']))
    return (
        f"{scheme}://{quote(node['password'], safe='')}@"
        f"{endpoint_authority(node['server'], hy2_ports(node))}/?{urlencode(params)}#"
        f"{quote(node['name'], safe='')}"
    )


def render_share(nodes):
    text = '\n'.join(
        vless_uri(node) if node['protocol'] == 'vless' else hy2_uri(node)
        for node in nodes
    )
    return text + ('\n' if text else '')


def render_shadowrocket_uris(nodes):
    lines = [
        vless_uri(node) if node['protocol'] == 'vless' else hy2_uri(node, 'hysteria2')
        for node in nodes
    ]
    return '\n'.join(lines) + ('\n' if lines else '')


def render_nekobox_uris(nodes):
    lines = [
        vless_uri(node) if node['protocol'] == 'vless' else hy2_uri(node, 'hy2')
        for node in nodes
    ]
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
                f"udp=true,block-quic=true"
            )
    return '\n'.join(lines) + ('\n' if lines else '')


def render_shadowrocket(nodes):
    return b64std(render_shadowrocket_uris(nodes)) + '\n'


def render_mihomo_yaml(nodes):
    lines = ['mixed-port: 7890', 'allow-lan: false', 'mode: rule', 'log-level: info', 'proxies:']
    names = []
    for node in nodes:
        names.append(node['name'])
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
            limit = int(node.get('limit_mbps') or 50)
            lines += [
                f'  - name: {json.dumps(node["name"], ensure_ascii=False)}',
                '    type: hysteria2',
                f'    server: {node["server"]}',
                f'    port: {node["port"]}',
                f'    ports: {json.dumps(hy2_ports(node))}',
                f'    hop-interval: {hy2_hop_interval(node)}',
                f'    password: {json.dumps(node["password"])}',
                f'    up: "{limit} Mbps"',
                f'    down: "{limit} Mbps"',
                '    obfs: salamander',
                f'    obfs-password: {json.dumps(node["obfs_password"])}',
                f'    sni: {node["sni"]}',
                '    skip-cert-verify: true',
                '    alpn: [h3]',
                '    udp: true',
            ]
    proxy_list = ', '.join(json.dumps(name, ensure_ascii=False) for name in names) if names else 'DIRECT'
    lines += [
        'proxy-groups:',
        '  - name: 全部节点',
        '    type: select',
        f'    proxies: [{proxy_list}]',
        '  - name: 自动测速',
        '    type: url-test',
        f'    proxies: [{proxy_list}]',
        '    url: https://www.gstatic.com/generate_204',
        '    interval: 86400',
        'rules:',
        '  - MATCH,全部节点',
        '',
    ]
    return '\n'.join(lines)


def render_clash(nodes):
    return render_mihomo_yaml(nodes)


def render_nekobox(nodes):
    # NekoBox subscription detection explicitly requests Clash Meta format, but it
    # has an independent renderer contract so future app-specific changes do not
    # silently alter Clash Verge Rev output.
    return render_mihomo_yaml(nodes)


RENDERERS = {
    'clash': {'render': render_clash, 'content_type': 'text/yaml; charset=utf-8'},
    'nekobox': {'render': render_nekobox, 'content_type': 'text/yaml; charset=utf-8'},
    'nekobox-uri': {'render': render_nekobox_uris, 'content_type': 'text/plain; charset=utf-8'},
    'quantumultx': {'render': render_quantumultx, 'content_type': 'text/plain; charset=utf-8'},
    'loon': {'render': render_loon, 'content_type': 'text/plain; charset=utf-8'},
    'shadowrocket': {'render': render_shadowrocket, 'content_type': 'text/plain; charset=utf-8'},
    'shadowrocket-uri': {'render': render_shadowrocket_uris, 'content_type': 'text/plain; charset=utf-8'},
    'share': {'render': render_share, 'content_type': 'text/plain; charset=utf-8'},
}

LOCAL_OUTPUTS = [
    {'filename': 'Quantumult-X.conf', 'format': 'quantumultx', 'display_name': 'Quantumult X'},
    {'filename': 'Loon.conf', 'format': 'loon', 'display_name': 'Loon'},
    {'filename': 'Shadowrocket.txt', 'format': 'shadowrocket-uri', 'display_name': 'Shadowrocket 分享链接'},
    {'filename': 'Clash-Verge-Rev.yaml', 'format': 'clash', 'display_name': 'Clash Verge Rev / Mihomo'},
    {'filename': 'NekoBoxForAndroid.txt', 'format': 'nekobox-uri', 'display_name': 'NekoBoxForAndroid 分享链接'},
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
        },
        {
            'name': 'JP-HY2-127.0.0.1:443', 'protocol': 'hysteria2', 'server': '127.0.0.1',
            'port': 443, 'ports': '443,20000-50000', 'hop_interval_seconds': 30,
            'password': 'password', 'sni': 'jp-hy2.jp-relay.local',
            'obfs_password': 'salamander', 'pin': 'aa' * 32, 'limit_mbps': 50, 'udp': True,
        },
    ]
    for format_name in available_formats():
        output = render(format_name, sample)
        if not isinstance(output, str) or not output.strip():
            raise RuntimeError(f'{format_name} renderer returned empty output')
    detected = detect_client({'User-Agent': 'NekoBox/Android/1.4.2 (Prefer ClashMeta Format)'})
    if not detected or detected.get('name') != 'NekoBoxForAndroid' or detected.get('format') != 'nekobox':
        raise RuntimeError('NekoBoxForAndroid 1.4.2 user agent was not recognized')
    expected_loon = (
        'JP-HY2-127.0.0.1:443 = Hysteria2,127.0.0.1,443,password,'
        'sni=jp-hy2.jp-relay.local,skip-cert-verify=true,fast-open=true,'
        'salamander-password=salamander,server-ports="443,20000-50000",udp=true,block-quic=true'
    )
    if expected_loon not in render('loon', sample).splitlines():
        raise RuntimeError('Loon Hysteria 2 port hopping contract changed')
    clash = render('clash', sample)
    if 'ports: "443,20000-50000"' not in clash or 'hop-interval: 30' not in clash:
        raise RuntimeError('Mihomo Hysteria 2 port hopping fields are missing')
    if not any(line.startswith('hy2://') for line in render('nekobox-uri', sample).splitlines()):
        raise RuntimeError('NekoBox local Hysteria 2 link must use hy2://')
    names = [item['filename'] for item in local_outputs()]
    if len(names) != len(set(names)) or 'NekoBoxForAndroid.txt' not in names:
        raise RuntimeError('local output manifest is invalid')
    if 'Loon-Shadowrocket.txt' in names or 'NekoBoxForAndroid.yaml' in names:
        raise RuntimeError('obsolete duplicated local outputs are still present')
    return True


if __name__ == '__main__':
    smoke_test()
    print(json.dumps({
        'version': VERSION,
        'formats': available_formats(),
        'local_outputs': local_outputs(),
        'default_upgrade_url': DEFAULT_UPGRADE_URL,
    }, ensure_ascii=False))

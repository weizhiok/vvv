#!/usr/bin/env python3
"""Pure VVV client-recognition and rendering module."""

import base64
import json
import re
from urllib.parse import quote, urlencode

VERSION = 3
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


def hy2_uri(node):
    params = [
        ('obfs', 'salamander'),
        ('obfs-password', node['obfs_password']),
        ('sni', node['sni']),
        ('insecure', '1'),
    ]
    if node.get('pin'):
        params.append(('pinSHA256', node['pin']))
    return (
        f"hysteria2://{quote(node['password'], safe='')}@"
        f"{node['server']}:{node['port']}/?{urlencode(params)}#"
        f"{quote(node['name'], safe='')}"
    )


def render_share(nodes):
    text = '\n'.join(
        vless_uri(node) if node['protocol'] == 'vless' else hy2_uri(node)
        for node in nodes
    )
    return text + ('\n' if text else '')


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
                f"{loon_q(node['password'])},skip-cert-verify=true,sni={node['sni']},"
                f"udp=true,fast-open=true,salamander-password={loon_q(node['obfs_password'])}"
            )
    return '\n'.join(lines) + ('\n' if lines else '')


def render_shadowrocket(nodes):
    return b64std(render_share(nodes)) + '\n'


def render_clash(nodes):
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


RENDERERS = {
    'clash': {'render': render_clash, 'content_type': 'text/yaml; charset=utf-8'},
    'nekobox': {'render': render_clash, 'content_type': 'text/yaml; charset=utf-8'},
    'quantumultx': {'render': render_quantumultx, 'content_type': 'text/plain; charset=utf-8'},
    'loon': {'render': render_loon, 'content_type': 'text/plain; charset=utf-8'},
    'shadowrocket': {'render': render_shadowrocket, 'content_type': 'text/plain; charset=utf-8'},
    'share': {'render': render_share, 'content_type': 'text/plain; charset=utf-8'},
}

LOCAL_OUTPUTS = [
    {'filename': 'Quantumult-X.conf', 'format': 'quantumultx', 'display_name': 'Quantumult X'},
    {'filename': 'Loon.conf', 'format': 'loon', 'display_name': 'Loon'},
    {'filename': 'Loon-Shadowrocket.txt', 'format': 'share', 'display_name': 'Loon / Shadowrocket 分享链接'},
    {'filename': 'Shadowrocket.txt', 'format': 'share', 'display_name': 'Shadowrocket 分享链接'},
    {'filename': 'Clash-Verge-Rev.yaml', 'format': 'clash', 'display_name': 'Clash Verge Rev / Mihomo'},
    {'filename': 'NekoBoxForAndroid.yaml', 'format': 'nekobox', 'display_name': 'NekoBoxForAndroid（Clash Meta）'},
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
            'port': 443, 'password': 'password', 'sni': 'jp-hy2.jp-relay.local',
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
    if render('nekobox', sample) != render('clash', sample):
        raise RuntimeError('NekoBox renderer must remain Clash Meta compatible')
    names = [item['filename'] for item in local_outputs()]
    if len(names) != len(set(names)) or 'NekoBoxForAndroid.yaml' not in names:
        raise RuntimeError('local output manifest is invalid')
    return True


if __name__ == '__main__':
    smoke_test()
    print(json.dumps({
        'version': VERSION,
        'formats': available_formats(),
        'local_outputs': local_outputs(),
        'default_upgrade_url': DEFAULT_UPGRADE_URL,
    }, ensure_ascii=False))

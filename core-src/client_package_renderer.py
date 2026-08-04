#!/usr/bin/env python3
"""Render one VVV client package from a candidate or installed state."""

import argparse
import importlib.util
import json
import os
import re
import tempfile
from pathlib import Path

DEFAULT_ADAPTER = Path('/usr/local/lib/vvv/client_adapters.py')
OBSOLETE_OUTPUTS = ('Loon-Shadowrocket.txt', 'NekoBoxForAndroid.yaml')


def load_adapter(path):
    spec = importlib.util.spec_from_file_location('vvv_package_adapter', str(path))
    module = importlib.util.module_from_spec(spec)
    if not spec.loader:
        raise RuntimeError('无法加载客户端渲染模块。')
    spec.loader.exec_module(module)
    module.smoke_test()
    return module


def read_state(path):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise RuntimeError('状态文件不是 JSON 对象。')
    return value


def protocol_name(base, proto):
    match = re.match(r'^([A-Z]{2})-(.+)$', str(base or ''))
    if match:
        return f'{match.group(1)}-{proto}-{match.group(2)}'
    return f'{proto}-{base}' if re.fullmatch(r'[^:]+:\d+', str(base or '')) else f'{base}-{proto}'


def hopping(state):
    item = state.get('port_hopping') or state.get('japan_port_hopping') or {}
    port = int(state.get('listen_port') or state.get('japan_port') or 0)
    return str(item.get('ports') or port), int(item.get('hop_interval_seconds') or 30)


def vless_node(base, state, uuid, udp=True):
    vless = state.get('vless') or {}
    reality = vless.get('reality') or {}
    return {
        'name': protocol_name(base, 'VLESS'), 'protocol': 'vless',
        'server': state['public_ip'], 'port': int(state['listen_port']), 'uuid': uuid,
        'sni': state['sni'], 'public_key': reality['public_key'],
        'short_id': reality['short_id'], 'udp': bool(udp),
    }


def hy2_node(base, state, password):
    hy2 = state.get('hy2') or {}
    ports, interval = hopping(state)
    return {
        'name': protocol_name(base, 'HY2'), 'protocol': 'hysteria2',
        'server': state['public_ip'], 'port': int(state['listen_port']),
        'ports': ports, 'hop_interval_seconds': interval, 'password': password,
        'sni': hy2['server_name'], 'obfs_password': hy2['obfs_password'],
        'pin': hy2.get('certificate_pin_hex', ''),
        'fingerprint': hy2.get('certificate_fingerprint', ''),
        'limit_mbps': int(state.get('hy2_limit_mbps') or 50), 'udp': True,
    }


def main_nodes(state, kind, item_id):
    mode = state.get('protocol_mode')
    relay = None
    upstream = None
    if kind == 'direct':
        base = state.get('direct_base_name') or f"{state['public_ip']}:{state['listen_port']}"
        v_uuid = ((state.get('vless') or {}).get('direct_user') or {}).get('uuid') if mode in ('dual', 'vless') else None
        h_password = ((state.get('hy2') or {}).get('direct_user') or {}).get('password') if mode in ('dual', 'hy2') else None
        title = '日本 VPS 直连节点'
        metadata = [f"日本入口：{state['public_ip']}:{state['listen_port']}", f'安装模式：{mode}']
        udp = True
    elif kind == 'relay':
        relay = next(row for row in state.get('relays', []) if row.get('id') == item_id)
        raw_name = str(relay.get('name') or '')
        country = raw_name[:2].upper() if len(raw_name) >= 3 and raw_name[:2].isalpha() and raw_name[2] == '-' else ''
        base = (country + '-' if country else '') + f"中转-{state['public_ip']}:{state['listen_port']}"
        v_uuid = (relay.get('vless') or {}).get('client_uuid')
        h_password = (relay.get('hy2') or {}).get('client_password')
        title = f"中转节点：{relay.get('name') or item_id}"
        metadata = [
            f"日本入口：{state['public_ip']}:{state['listen_port']}",
            f"最终落地：{relay.get('remote_ip')}:{relay.get('remote_port')}",
            f'安装模式：{mode}',
        ]
        udp = True
    elif kind == 'upstream':
        upstream = next(row for row in state.get('upstream_relays', []) if row.get('id') == item_id)
        base = upstream.get('name') or item_id
        v_uuid = upstream.get('client_uuid')
        h_password = None
        title = f'动态代理中转节点：{base}'
        metadata = [
            f"日本入口：{state['public_ip']}:{state['listen_port']}",
            f"上游代理：{upstream.get('protocol_label')} {upstream.get('host')}:{upstream.get('port')}",
            'UDP：服务器端拒绝，防止绕过上游出口',
        ]
        udp = False
    else:
        raise RuntimeError(f'未知客户端配置类型：{kind}')
    nodes = []
    if v_uuid:
        nodes.append(vless_node(base, state, v_uuid, udp))
    if h_password:
        nodes.append(hy2_node(base, state, h_password))
    if h_password:
        ports, interval = hopping(state)
        metadata.append(f'Hysteria 2 端口跳跃：{ports}（每 {interval} 秒切换）')
        metadata.append(f"Hysteria 2 服务端硬上限：上行 {int(state.get('hy2_limit_mbps') or 50)} Mbps / 下行 {int(state.get('hy2_limit_mbps') or 50)} Mbps")
    return title, metadata, nodes


def landing_nodes(state):
    mode = state.get('protocol_mode')
    raw_name = str(state.get('node_name') or '')
    country = raw_name[:2].upper() if len(raw_name) >= 3 and raw_name[:2].isalpha() and raw_name[2] == '-' else ''
    base = (country + '-' if country else '') + f"中转-{state['japan_public_ip']}:{state['japan_port']}"
    fake = {
        'public_ip': state['japan_public_ip'], 'listen_port': int(state['japan_port']),
        'sni': state['sni'], 'hy2_limit_mbps': int(state.get('hy2_limit_mbps') or 50),
        'port_hopping': state.get('japan_port_hopping') or {},
        'vless': {'reality': {
            'public_key': (state.get('vless') or {}).get('japan_reality_public_key'),
            'short_id': (state.get('vless') or {}).get('japan_reality_short_id'),
        }},
        'hy2': {
            'server_name': (state.get('hy2') or {}).get('japan_server_name'),
            'obfs_password': (state.get('hy2') or {}).get('japan_obfs_password'),
            'certificate_pin_hex': (state.get('hy2') or {}).get('japan_certificate_pin_hex', ''),
            'certificate_fingerprint': (state.get('hy2') or {}).get('japan_certificate_fingerprint', ''),
        },
    }
    nodes = []
    if mode in ('dual', 'vless'):
        nodes.append(vless_node(base, fake, (state.get('vless') or {}).get('japan_client_uuid'), True))
    if mode in ('dual', 'hy2'):
        nodes.append(hy2_node(base, fake, (state.get('hy2') or {}).get('japan_client_password')))
    ports, interval = hopping(fake)
    metadata = [
        f'线路：{base}',
        f"日本入口：{state['japan_public_ip']}:{state['japan_port']}",
        f"最终落地：{state.get('remote_public_ip')}:{state.get('remote_public_port')}",
        f'协议模式：{mode}',
    ]
    if mode in ('dual', 'hy2'):
        metadata.append(f'Hysteria 2 端口跳跃：{ports}（每 {interval} 秒切换）')
    return '中转客户端节点', metadata, nodes


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def render_package(adapter, title, metadata, nodes, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    outputs = adapter.local_outputs()
    rendered = {}
    for row in outputs:
        rendered[row['filename']] = adapter.render(row['format'], nodes)
        atomic_write(out / row['filename'], rendered[row['filename']])
    for name in OBSOLETE_OUTPUTS:
        (out / name).unlink(missing_ok=True)
    lines = [title, '=' * 36, *metadata]
    for row in outputs:
        content = rendered[row['filename']]
        if content.strip():
            lines += ['', f"【{row.get('display_name') or row['filename']}】", content.rstrip()]
    summary = '\n'.join(lines).rstrip() + '\n'
    atomic_write(out / '客户端节点.txt', summary)
    print(summary, end='')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', required=True)
    parser.add_argument('--kind', choices=('direct', 'relay', 'upstream', 'landing'), required=True)
    parser.add_argument('--id', default='')
    parser.add_argument('--out', required=True)
    parser.add_argument('--adapter', default=str(DEFAULT_ADAPTER))
    args = parser.parse_args()
    state = read_state(args.state)
    adapter = load_adapter(Path(args.adapter))
    if args.kind == 'landing':
        title, metadata, nodes = landing_nodes(state)
    else:
        title, metadata, nodes = main_nodes(state, args.kind, args.id)
    render_package(adapter, title, metadata, nodes, args.out)


if __name__ == '__main__':
    main()

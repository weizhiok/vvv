#!/usr/bin/env python3
"""Regenerate local client configuration from existing read-only VVV state.

This module never changes proxy state, credentials, core configuration, services,
or system settings. It only writes client-facing output files under /root and the
subscription output cache when explicitly called by the fixed upgrade engine.
"""

import argparse
import importlib.util
import json
import os
import shutil
import tempfile
from pathlib import Path

DEFAULT_ADAPTER = Path('/usr/local/lib/vvv/client_adapters.py')


def rooted(root, absolute):
    root = Path(root)
    path = Path(absolute)
    return path if root == Path('/') else root / str(path).lstrip('/')


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default


def load_adapter(path=DEFAULT_ADAPTER):
    path = Path(path)
    spec = importlib.util.spec_from_file_location('vvv_local_client_adapter', path)
    module = importlib.util.module_from_spec(spec)
    if not spec.loader:
        raise RuntimeError('无法加载客户端支持模块。')
    spec.loader.exec_module(module)
    for name in ('render', 'local_outputs', 'smoke_test'):
        if not callable(getattr(module, name, None)):
            raise RuntimeError(f'客户端支持模块缺少 {name}。')
    module.smoke_test()
    return module


def protocol_name(base, protocol):
    base = str(base)
    if len(base) >= 3 and base[:2].isalpha() and base[2] == '-':
        return f'{base[:2].upper()}-{protocol}-{base[3:]}'
    return f'{protocol}-{base}' if _looks_like_endpoint(base) else f'{base}-{protocol}'


def _looks_like_endpoint(value):
    if ':' not in value:
        return False
    host, port = value.rsplit(':', 1)
    return bool(host) and port.isdigit()


def vless_node(name, server, port, uuid, sni, public_key, short_id, udp=True):
    return {
        'name': protocol_name(name, 'VLESS'), 'protocol': 'vless',
        'server': str(server), 'port': int(port), 'uuid': str(uuid),
        'sni': str(sni), 'public_key': str(public_key),
        'short_id': str(short_id), 'udp': bool(udp),
    }


def hy2_node(name, server, port, password, sni, obfs, pin='', fingerprint='', limit=50):
    return {
        'name': protocol_name(name, 'HY2'), 'protocol': 'hysteria2',
        'server': str(server), 'port': int(port), 'password': str(password),
        'sni': str(sni), 'obfs_password': str(obfs), 'pin': str(pin),
        'fingerprint': str(fingerprint), 'limit_mbps': int(limit), 'udp': True,
    }


def slot_value(items, slot, key):
    for item in items or []:
        if item.get('slot') == slot:
            return item.get(key)
    return None


def main_contexts(state, root='/'):
    contexts = []
    mode = state.get('protocol_mode')
    server = state.get('public_ip')
    port = int(state.get('listen_port') or 0)
    sni = state.get('sni')
    limit = int(state.get('hy2_limit_mbps') or 50)
    vless = state.get('vless') or {}
    hy2 = state.get('hy2') or {}
    reality = vless.get('reality') or {}

    def build(base, v_uuid=None, h_password=None, udp=True):
        nodes = []
        if v_uuid:
            nodes.append(vless_node(base, server, port, v_uuid, sni,
                                    reality.get('public_key'), reality.get('short_id'), udp))
        if h_password:
            nodes.append(hy2_node(base, server, port, h_password,
                                  hy2.get('server_name'), hy2.get('obfs_password'),
                                  hy2.get('certificate_pin_hex', ''),
                                  hy2.get('certificate_fingerprint', ''), limit))
        return nodes

    direct_nodes = build(
        state.get('direct_base_name') or f'{server}:{port}',
        (vless.get('direct_user') or {}).get('uuid') if mode in ('dual', 'vless') else None,
        (hy2.get('direct_user') or {}).get('password') if mode in ('dual', 'hy2') else None,
    )
    contexts.append({
        'id': 'direct', 'title': '日本 VPS 直连节点',
        'metadata': [f'日本入口：{server}:{port}', f'安装模式：{mode}'],
        'directory': rooted(root, '/root/日本VPS-直连客户端配置'),
        'summary_copy': rooted(root, '/root/日本VPS-客户端节点.txt'),
        'nodes': direct_nodes,
    })

    package_root = rooted(root, '/root/relay-packages')
    for relay in state.get('relays', []):
        rv = relay.get('vless') or {}
        rh = relay.get('hy2') or {}
        raw_name = str(relay.get('name') or '')
        country = raw_name[:2].upper() if len(raw_name) >= 3 and raw_name[:2].isalpha() and raw_name[2] == '-' else ''
        relay_base = (country + '-' if country else '') + f'中转-{server}:{port}'
        nodes = build(relay_base, rv.get('client_uuid'), rh.get('client_password'))
        contexts.append({
            'id': relay.get('id'), 'title': f"中转节点：{relay.get('name') or relay.get('id')}",
            'metadata': [f'日本入口：{server}:{port}',
                         f"最终落地：{relay.get('remote_ip')}:{relay.get('remote_port')}",
                         f'安装模式：{mode}'],
            'directory': package_root / str(relay.get('id')), 'summary_copy': None, 'nodes': nodes,
        })

    for upstream in state.get('upstream_relays', []):
        nodes = build(upstream.get('name') or upstream.get('id'), upstream.get('client_uuid'), None, False)
        contexts.append({
            'id': upstream.get('id'), 'title': f"动态代理中转节点：{upstream.get('name') or upstream.get('id')}",
            'metadata': [f'日本入口：{server}:{port}',
                         f"上游代理：{upstream.get('protocol_label')} {upstream.get('host')}:{upstream.get('port')}",
                         'UDP：服务器端拒绝，防止绕过上游出口'],
            'directory': package_root / str(upstream.get('id')), 'summary_copy': None, 'nodes': nodes,
        })

    vslots = vless.get('reserve_users') or []
    hslots = hy2.get('reserve_users') or []
    for temp in state.get('temporary_nodes', []):
        tv = temp.get('vless') or {}
        th = temp.get('hy2') or {}
        vuuid = tv.get('client_uuid') or slot_value(vslots, tv.get('reserve_slot'), 'uuid')
        hpass = th.get('client_password') or slot_value(hslots, th.get('reserve_slot'), 'password')
        is_upstream = temp.get('source_type') == 'upstream'
        nodes = build(temp.get('name') or temp.get('id'), vuuid, None if is_upstream else hpass, not is_upstream)
        contexts.append({
            'id': temp.get('id'), 'title': f"临时节点：{temp.get('name') or temp.get('id')}",
            'metadata': [f'日本入口：{server}:{port}',
                         f"复制来源：{temp.get('source_name') or temp.get('source_id')}",
                         f"到期时间：{temp.get('expires_at', '未知')}"],
            'directory': package_root / str(temp.get('id')), 'summary_copy': None, 'nodes': nodes,
        })
    return contexts


def landing_contexts(state, root='/'):
    mode = state.get('protocol_mode')
    raw_name = str(state.get('node_name') or '')
    server = state.get('japan_public_ip')
    port = int(state.get('japan_port') or 0)
    country = raw_name[:2].upper() if len(raw_name) >= 3 and raw_name[:2].isalpha() and raw_name[2] == '-' else ''
    name = (country + '-' if country else '') + f'中转-{server}:{port}'
    limit = int(state.get('hy2_limit_mbps') or 50)
    nodes = []
    vless = state.get('vless') or {}
    hy2 = state.get('hy2') or {}
    if mode in ('dual', 'vless') and vless:
        nodes.append(vless_node(name, server, port, vless.get('japan_client_uuid'),
                                state.get('sni'), vless.get('japan_reality_public_key'),
                                vless.get('japan_reality_short_id'), True))
    if mode in ('dual', 'hy2') and hy2:
        nodes.append(hy2_node(name, server, port, hy2.get('japan_client_password'),
                              hy2.get('japan_server_name'), hy2.get('japan_obfs_password'),
                              hy2.get('japan_certificate_pin_hex', ''),
                              hy2.get('japan_certificate_fingerprint', ''), limit))
    return [{
        'id': 'landing', 'title': '中转客户端节点',
        'metadata': [f'线路：{name}', f'日本入口：{server}:{port}',
                     f"最终落地：{state.get('remote_public_ip')}:{state.get('remote_public_port')}",
                     f'协议模式：{mode}'],
        'directory': rooted(root, '/root/中转客户端配置'),
        'summary_copy': rooted(root, '/root/中转客户端节点.txt'),
        'nodes': nodes,
    }]


def detect_contexts(root='/'):
    main_state = rooted(root, '/etc/jp-relay/state.json')
    landing_state = rooted(root, '/etc/jp-relay/landing-state.json')
    has_main = main_state.is_file()
    has_landing = landing_state.is_file()
    if has_main and has_landing:
        direct = read_json(main_state)
        landing = read_json(landing_state)
        if not isinstance(direct, dict) or not isinstance(landing, dict):
            raise RuntimeError('组合角色状态文件无效。')
        return 'landing-direct', main_contexts(direct, root) + landing_contexts(landing, root)
    if has_landing:
        state = read_json(landing_state)
        if not isinstance(state, dict):
            raise RuntimeError('中转副机状态文件无效。')
        return 'landing', landing_contexts(state, root)
    if has_main:
        state = read_json(main_state)
        if not isinstance(state, dict):
            raise RuntimeError('主机状态文件无效。')
        return 'main', main_contexts(state, root)
    return 'center-only', []


def output_filenames(adapter):
    rows = adapter.local_outputs()
    if not isinstance(rows, list) or not rows:
        raise RuntimeError('客户端本机输出清单为空。')
    names = []
    for row in rows:
        filename = str(row.get('filename') or '')
        format_name = str(row.get('format') or '')
        if not filename or Path(filename).name != filename or not format_name:
            raise RuntimeError('客户端本机输出清单包含非法项目。')
        names.append(filename)
    if len(names) != len(set(names)):
        raise RuntimeError('客户端本机输出文件名重复。')
    return rows


def atomic_write(path, data, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def render_context(context, adapter, obsolete=()):
    directory = Path(context['directory'])
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    outputs = output_filenames(adapter)
    rendered = {}
    for row in outputs:
        rendered[row['filename']] = adapter.render(row['format'], context['nodes'])
    for filename, content in rendered.items():
        if not isinstance(content, str):
            raise RuntimeError(f'{filename} 渲染结果不是文本。')
        atomic_write(directory / filename, content)
    for filename in obsolete:
        if filename not in rendered:
            (directory / filename).unlink(missing_ok=True)

    lines = [context['title'], '=' * 36, *context.get('metadata', [])]
    for row in outputs:
        filename = row['filename']
        if filename == 'Shadowrocket.txt':
            continue
        content = rendered.get(filename, '')
        if not content.strip():
            continue
        lines += ['', f"【{row.get('display_name') or filename}】", content.rstrip()]
    summary = '\n'.join(lines).rstrip() + '\n'
    atomic_write(directory / '客户端节点.txt', summary)
    if context.get('summary_copy'):
        atomic_write(context['summary_copy'], summary)
    return summary


def managed_paths(root='/', adapter_path=DEFAULT_ADAPTER, extra_filenames=()):
    adapter = load_adapter(adapter_path)
    filenames = {row['filename'] for row in output_filenames(adapter)} | set(extra_filenames) | {'客户端节点.txt'}
    _role, contexts = detect_contexts(root)
    paths = []
    for context in contexts:
        directory = Path(context['directory'])
        paths.extend(directory / name for name in filenames)
        if context.get('summary_copy'):
            paths.append(Path(context['summary_copy']))
    return sorted(set(paths), key=str)


def regenerate_all(root='/', adapter_path=DEFAULT_ADAPTER, obsolete=()):
    adapter = load_adapter(adapter_path)
    role, contexts = detect_contexts(root)
    summaries = []
    for context in contexts:
        summaries.append(render_context(context, adapter, obsolete))
    return role, summaries


def show_all(root='/'):
    role, contexts = detect_contexts(root)
    shown = 0
    for context in contexts:
        path = Path(context['directory']) / '客户端节点.txt'
        if path.is_file():
            if shown:
                print()
            print(path.read_text(encoding='utf-8'), end='')
            shown += 1
    if role == 'center-only':
        cfg = read_json(rooted(root, '/etc/vvv-sub/config.json'), {}) or {}
        print('本机仅包含订阅中心，没有本地代理节点。')
        if cfg.get('subscription_url'):
            print(f"统一订阅地址：{cfg['subscription_url']}")
    return shown


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['regenerate', 'show', 'role'])
    parser.add_argument('--root', default=os.environ.get('VVV_TEST_ROOT', '/'))
    parser.add_argument('--adapter', default=str(DEFAULT_ADAPTER))
    parser.add_argument('--obsolete', action='append', default=[])
    args = parser.parse_args()
    if args.command == 'regenerate':
        role, summaries = regenerate_all(args.root, args.adapter, args.obsolete)
        print(json.dumps({'role': role, 'contexts': len(summaries)}, ensure_ascii=False))
    elif args.command == 'show':
        show_all(args.root)
    else:
        role, contexts = detect_contexts(args.root)
        print(json.dumps({'role': role, 'contexts': len(contexts)}, ensure_ascii=False))


if __name__ == '__main__':
    main()

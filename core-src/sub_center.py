#!/usr/bin/env python3
import argparse
import hashlib
import ipaddress
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import client_adapters

CFG = Path('/etc/vvv-sub/config.json')
DATA = Path('/var/lib/vvv-sub')
HOSTS = DATA / 'hosts'
OUT = DATA / 'output'
REGISTRY = DATA / 'registry.json'
OVERRIDES = DATA / 'node-overrides.json'
BACKUP = Path('/usr/local/lib/vvv/backup_manager.py')
DEBUG_FLAG = Path('/run/vvv-sub-header-debug.enabled')
DEBUG_LOG = Path('/run/vvv-sub-header-debug.jsonl')
LOCK = threading.RLock()
SENSITIVE_HEADERS = {'authorization', 'proxy-authorization', 'cookie', 'set-cookie'}


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default


def atomic_json(path, obj, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(obj, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def backup(reason, force=False):
    if not BACKUP.exists() or not CFG.exists():
        return
    command = ['python3', str(BACKUP), 'create', reason]
    if force:
        command.append('--force')
    subprocess.run(command, check=False, stdout=subprocess.DEVNULL)


def protocol_name(base, proto):
    match = re.match(r'^([A-Z]{2})-(.+)$', base or '')
    if match:
        return f'{match.group(1)}-{proto}-{match.group(2)}'
    return f'{base}-{proto}' if base else proto


def node_id(host_id, kind, key):
    return hashlib.sha256(f'{host_id}|{kind}|{key}'.encode()).hexdigest()[:24]


def nodes_from_host(doc):
    state = doc.get('state') or {}
    role = doc.get('role') or ''
    mode = state.get('protocol_mode')
    ip = state.get('public_ip') or state.get('japan_public_ip')
    port = state.get('listen_port') or state.get('japan_port')
    sni = state.get('sni')
    if not (mode and ip and port and sni):
        return []
    vless = state.get('vless') or {}
    hy2 = state.get('hy2') or {}
    nodes = []

    def add_vless(base, uuid, udp=True, category='直连', stable_key=None, temporary=False, expires_at=None, expected_exit_ip=None):
        if not uuid or not vless:
            return
        key = stable_key or base
        nodes.append({
            'id': node_id(doc['host_id'], 'vless', key),
            'name': protocol_name(base, 'VLESS'),
            'protocol': 'vless', 'server': ip, 'port': int(port), 'uuid': uuid,
            'sni': sni, 'public_key': ((vless.get('reality') or {}).get('public_key')),
            'short_id': ((vless.get('reality') or {}).get('short_id')),
            'udp': bool(udp), 'category': category, 'temporary': temporary,
            'expires_at': expires_at, 'expected_exit_ip': expected_exit_ip,
        })

    def add_hy2(base, password, category='直连', stable_key=None, temporary=False, expires_at=None, expected_exit_ip=None):
        if not password or not hy2:
            return
        key = stable_key or base
        nodes.append({
            'id': node_id(doc['host_id'], 'hy2', key),
            'name': protocol_name(base, 'HY2'),
            'protocol': 'hysteria2', 'server': ip, 'port': int(port), 'password': password,
            'sni': hy2.get('server_name'), 'obfs_password': hy2.get('obfs_password'),
            'pin': hy2.get('certificate_pin_hex'), 'udp': True, 'category': category,
            'temporary': temporary, 'expires_at': expires_at, 'expected_exit_ip': expected_exit_ip,
            'limit_mbps': int(state.get('hy2_limit_mbps') or 50),
            'pin_b64': hy2.get('certificate_public_key_sha256'),
        })

    base = state.get('direct_base_name') or f'{ip}:{port}'
    if mode in ('dual', 'vless'):
        add_vless(base, ((vless.get('direct_user') or {}).get('uuid')), True, '直连', 'direct', expected_exit_ip=ip)
    if mode in ('dual', 'hy2'):
        add_hy2(base, ((hy2.get('direct_user') or {}).get('password')), '直连', 'direct', expected_exit_ip=ip)

    if role in ('center-relay', 'relay'):
        for relay in state.get('relays') or []:
            rv, rh = relay.get('vless'), relay.get('hy2')
            if rv:
                add_vless(relay.get('name') or relay.get('id'), rv.get('client_uuid'), True, 'VPS中转', relay.get('id'), expected_exit_ip=relay.get('remote_ip'))
            if rh:
                add_hy2(relay.get('name') or relay.get('id'), rh.get('client_password'), 'VPS中转', relay.get('id'), expected_exit_ip=relay.get('remote_ip'))
        for upstream in state.get('upstream_relays') or []:
            add_vless(upstream.get('name') or upstream.get('id'), upstream.get('client_uuid'), False, '动态代理', upstream.get('id'), expected_exit_ip=upstream.get('last_exit_ip'))
        current = time.time()
        for temp in state.get('temporary_nodes') or []:
            expires = float(temp.get('expires_ts') or 0)
            if expires and expires <= current:
                continue
            base_name = temp.get('name') or f"临时-{temp.get('source_name') or temp.get('id')}"
            tv, th = temp.get('vless') or {}, temp.get('hy2') or {}
            expected = None
            if temp.get('source_type') == 'vps':
                source = next((item for item in state.get('relays') or [] if item.get('id') == temp.get('source_id')), {})
                expected = source.get('remote_ip')
            else:
                source = next((item for item in state.get('upstream_relays') or [] if item.get('id') == temp.get('source_id')), {})
                expected = source.get('last_exit_ip')
            if tv.get('client_uuid'):
                add_vless(base_name, tv['client_uuid'], temp.get('source_type') != 'upstream', '临时节点', temp.get('id'), True, temp.get('expires_at'), expected)
            if th.get('client_password'):
                add_hy2(base_name, th['client_password'], '临时节点', temp.get('id'), True, temp.get('expires_at'), expected)
    return nodes


def active_hosts():
    docs = []
    current = time.time()
    for path in HOSTS.glob('*.json'):
        doc = read_json(path, {}) or {}
        last = float(doc.get('last_seen_ts') or 0)
        if last and current - last > 72 * 3600 and doc.get('role') in ('direct', 'landing'):
            continue
        docs.append(doc)
    return docs


def all_nodes():
    nodes, seen = [], set()
    overrides = read_json(OVERRIDES, {}) or {}
    for host in active_hosts():
        for node in nodes_from_host(host):
            if node['id'] in seen:
                continue
            seen.add(node['id'])
            custom = (overrides.get(node['id']) or {}).get('display_name')
            if custom:
                node['default_name'] = node['name']
                node['name'] = custom
            nodes.append(node)
    return nodes


def regenerate():
    OUT.mkdir(parents=True, exist_ok=True)
    nodes = all_nodes()
    for format_name in client_adapters.available_formats():
        content = client_adapters.render(format_name, nodes)
        target = OUT / format_name
        temporary = target.with_suffix('.tmp')
        temporary.write_text(content, encoding='utf-8')
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    atomic_json(OUT / 'nodes.json', {'generated_at': now(), 'count': len(nodes), 'nodes': nodes})
    return len(nodes)


def public_metadata():
    cfg = read_json(CFG, {}) or {}
    return {'subscription_url': cfg.get('subscription_url', ''), 'transport_mode': cfg.get('transport_mode', '')}


def finalize_registration(entry, body):
    doc = {
        'host_id': entry['host_id'], 'role': entry.get('role', 'direct'),
        'state': body.get('state') or {}, 'meta': body.get('meta') or {},
        'last_seen': now(), 'last_seen_ts': time.time(),
    }
    atomic_json(HOSTS / f"{entry['host_id']}.json", doc)
    count = regenerate()
    result = {'ok': True, 'registered': True, 'subscription_refreshed': True,
              'node_count': count, 'host_id': entry['host_id'], 'host_token': entry['token']}
    result.update(public_metadata())
    return result


def auth_token(handler):
    value = handler.headers.get('Authorization', '')
    return value[7:] if value.startswith('Bearer ') else ''


def redacted_path(path, suffix):
    if path == '/' + suffix and suffix:
        return '/' + suffix[:2] + '*' * max(4, len(suffix) - 4) + suffix[-2:]
    return path[:256]


def capture_debug(handler, recognition, suffix):
    if not DEBUG_FLAG.exists():
        return
    headers = {}
    for index, (key, value) in enumerate(handler.headers.items()):
        if index >= 64:
            break
        headers[key] = '[已隐藏]' if key.lower() in SENSITIVE_HEADERS else str(value)[:512]
    event = {'time': now(), 'source_ip': handler.client_address[0], 'method': handler.command,
             'path': redacted_path(urlparse(handler.path).path, suffix), 'headers': headers,
             'recognized_client': (recognition or {}).get('name', '未识别'),
             'response_format': (recognition or {}).get('format', '无')}
    with DEBUG_LOG.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + '\n')


class Handler(BaseHTTPRequestHandler):
    server_version = 'StaticResource/4.0'

    def log_message(self, *_):
        pass

    def send_bytes(self, status, data, content_type='text/plain; charset=utf-8', extra_headers=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'private, no-store')
        self.send_header('X-Robots-Tag', 'noindex, nofollow')
        self.send_header('Profile-Update-Interval', '24')
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def json_body(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 0 or length > 16 * 1024 * 1024:
                return None
            return json.loads(self.rfile.read(length).decode())
        except Exception:
            return None

    def do_GET(self):
        cfg = read_json(CFG, {}) or {}
        path = urlparse(self.path).path
        suffix = str(cfg.get('subscription_suffix') or '')
        if suffix and path == '/' + suffix:
            recognition = client_adapters.detect_client(self.headers)
            capture_debug(self, recognition, suffix)
            if not recognition:
                return self.send_bytes(415, '未识别订阅客户端。\n'.encode(), extra_headers={'X-VVV-Client': 'unknown'})
            return self.send_bytes(200, client_adapters.render(recognition['format'], all_nodes()).encode(),
                                   recognition['content_type'], {'X-VVV-Client': recognition['name'], 'X-VVV-Format': recognition['format']})
        if path == '/health':
            return self.send_bytes(200, b'ok\n')
        if path == '/api/v1/hosts':
            if not secrets.compare_digest(auth_token(self), str(cfg.get('master_token') or '')):
                return self.send_bytes(403, b'Forbidden\n')
            return self.send_bytes(200, json.dumps({'hosts': active_hosts()}, ensure_ascii=False).encode(), 'application/json')
        if path == '/api/v1/nodes':
            if not secrets.compare_digest(auth_token(self), str(cfg.get('master_token') or '')):
                return self.send_bytes(403, b'Forbidden\n')
            return self.send_bytes(200, json.dumps({'nodes': all_nodes()}, ensure_ascii=False).encode(), 'application/json')
        return self.send_bytes(404, b'Not Found\n')

    def do_POST(self):
        cfg = read_json(CFG, {}) or {}
        path = urlparse(self.path).path
        body = self.json_body()
        if body is None:
            return self.send_bytes(400, b'Bad Request\n')
        with LOCK:
            registry = read_json(REGISTRY, {'hosts': []}) or {'hosts': []}
            registry.setdefault('hosts', [])
            if path == '/api/v1/register':
                if not secrets.compare_digest(auth_token(self), str(cfg.get('master_token') or '')):
                    return self.send_bytes(403, b'Forbidden\n')
                host_id = str(body.get('host_id') or '').strip()
                role = str(body.get('role') or '').strip()
                if not re.fullmatch(r'[A-Za-z0-9._-]{8,128}', host_id) or role not in ('center-relay', 'center', 'relay', 'direct', 'landing'):
                    return self.send_bytes(400, b'Bad registration\n')
                backup('before-host-register')
                entry = next((item for item in registry['hosts'] if item.get('host_id') == host_id), None)
                if entry is None:
                    entry = {'host_id': host_id, 'token': secrets.token_urlsafe(32), 'created_at': now()}
                    registry['hosts'].append(entry)
                entry.update(role=role, hostname=str(body.get('hostname') or ''), updated_at=now())
                atomic_json(REGISTRY, registry)
                result = finalize_registration(entry, body)
                backup('after-host-register')
                return self.send_bytes(200, json.dumps(result, ensure_ascii=False).encode(), 'application/json')
            if path == '/api/v1/sync':
                host_id = str(body.get('host_id') or '').strip()
                entry = next((item for item in registry['hosts'] if item.get('host_id') == host_id), None)
                if entry is None or not secrets.compare_digest(auth_token(self), str(entry.get('token') or '')):
                    return self.send_bytes(403, b'Forbidden\n')
                backup('before-node-sync')
                doc = {'host_id': host_id, 'role': entry.get('role', 'direct'), 'state': body.get('state') or {},
                       'meta': body.get('meta') or {}, 'last_seen': now(), 'last_seen_ts': time.time()}
                atomic_json(HOSTS / f'{host_id}.json', doc)
                entry['updated_at'] = now()
                atomic_json(REGISTRY, registry)
                count = regenerate()
                backup('after-node-sync')
                result = {'ok': True, 'subscription_refreshed': True, 'node_count': count}
                result.update(public_metadata())
                return self.send_bytes(200, json.dumps(result, ensure_ascii=False).encode(), 'application/json')
        return self.send_bytes(404, b'Not Found\n')


def rename_node(node_id_value, name):
    name = str(name).strip()
    if not (1 <= len(name) <= 64) or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise SystemExit('名称必须是 1-64 个字符，且不能包含换行或控制字符。')
    nodes = all_nodes()
    target = next((node for node in nodes if node['id'] == node_id_value), None)
    if not target:
        raise SystemExit('节点不存在。')
    if any(node['id'] != node_id_value and node['name'] == name for node in nodes):
        raise SystemExit('已有其他节点使用相同名称。')
    backup('before-node-rename', True)
    overrides = read_json(OVERRIDES, {}) or {}
    overrides[node_id_value] = {'display_name': name, 'updated_at': now()}
    atomic_json(OVERRIDES, overrides)
    regenerate()
    backup('after-node-rename', True)


def reset_name(node_id_value):
    backup('before-node-name-reset', True)
    overrides = read_json(OVERRIDES, {}) or {}
    overrides.pop(node_id_value, None)
    atomic_json(OVERRIDES, overrides)
    regenerate()
    backup('after-node-name-reset', True)


def delete_host(host_id):
    registry = read_json(REGISTRY, {'hosts': []}) or {'hosts': []}
    if not any(item.get('host_id') == host_id for item in registry.get('hosts', [])):
        raise SystemExit('已注册副机不存在。')
    backup('before-host-delete', True)
    registry['hosts'] = [item for item in registry['hosts'] if item.get('host_id') != host_id]
    atomic_json(REGISTRY, registry)
    (HOSTS / f'{host_id}.json').unlink(missing_ok=True)
    valid_ids = {node['id'] for node in all_nodes()}
    overrides = read_json(OVERRIDES, {}) or {}
    atomic_json(OVERRIDES, {key: value for key, value in overrides.items() if key in valid_ids})
    regenerate()
    backup('after-host-delete', True)


def serve():
    cfg = read_json(CFG, {}) or {}
    HOSTS.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    if not REGISTRY.exists():
        atomic_json(REGISTRY, {'hosts': []})
    if not OVERRIDES.exists():
        atomic_json(OVERRIDES, {})
    regenerate()
    ThreadingHTTPServer((str(cfg.get('listen_host') or '0.0.0.0'), int(cfg.get('listen_port') or 18081)), Handler).serve_forever()


def list_hosts():
    registry = read_json(REGISTRY, {'hosts': []}) or {'hosts': []}
    return registry.get('hosts', [])


def show_host(host_id):
    entry = next((item for item in list_hosts() if item.get('host_id') == host_id), None)
    if not entry:
        raise SystemExit('主机不存在。')
    doc = read_json(HOSTS / f'{host_id}.json', {}) or {}
    return {'registry': entry, 'snapshot': doc, 'nodes': nodes_from_host(doc)}


def show_node(node_id_value):
    node = next((item for item in all_nodes() if item.get('id') == node_id_value), None)
    if not node:
        raise SystemExit('节点不存在。')
    return node


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('serve')
    commands.add_parser('regenerate')
    listing = commands.add_parser('list-nodes'); listing.add_argument('--tsv', action='store_true')
    shown = commands.add_parser('show-node'); shown.add_argument('node_id')
    hosts = commands.add_parser('list-hosts'); hosts.add_argument('--tsv', action='store_true')
    showh = commands.add_parser('show-host'); showh.add_argument('host_id')
    rename = commands.add_parser('rename-node'); rename.add_argument('node_id'); rename.add_argument('name')
    reset = commands.add_parser('reset-name'); reset.add_argument('node_id')
    delete = commands.add_parser('delete-host'); delete.add_argument('host_id')
    args = parser.parse_args()
    if args.command == 'serve':
        serve()
    elif args.command == 'regenerate':
        print(regenerate())
    elif args.command == 'list-nodes':
        nodes = all_nodes()
        if args.tsv:
            for node in nodes:
                print(f"{node.get('id','')}\t{node.get('name','')}\t{node.get('protocol','')}")
        else:
            print(json.dumps({'nodes': nodes}, ensure_ascii=False, indent=2))
    elif args.command == 'show-node':
        print(json.dumps(show_node(args.node_id), ensure_ascii=False, indent=2))
    elif args.command == 'list-hosts':
        rows = list_hosts()
        if args.tsv:
            for entry in rows:
                print(f"{entry.get('host_id','')}\t{entry.get('role','')}\t{entry.get('hostname','')}\t{entry.get('display_name','')}\t{entry.get('updated_at','')}")
        else:
            print(json.dumps({'hosts': rows}, ensure_ascii=False, indent=2))
    elif args.command == 'show-host':
        print(json.dumps(show_host(args.host_id), ensure_ascii=False, indent=2))
    elif args.command == 'rename-node':
        rename_node(args.node_id, args.name)
    elif args.command == 'reset-name':
        reset_name(args.node_id)
    elif args.command == 'delete-host':
        delete_host(args.host_id)

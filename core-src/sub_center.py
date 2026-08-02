#!/usr/bin/env python3
import argparse
import hashlib
import ipaddress
import json
import os
import re
import secrets
import subprocess
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


def protocol_name(base, proto):
    match = re.match(r'^([A-Z]{2})-(.+)$', base or '')
    if match:
        return f'{match.group(1)}-{proto}-{match.group(2)}'
    if re.match(r'^\d+\.\d+\.\d+\.\d+:\d+$', base or ''):
        return f'{proto}-{base}'
    return f'{base}-{proto}'


def backup(reason, force=False):
    if not BACKUP.exists():
        return
    command = ['python3', str(BACKUP), 'create', reason]
    if force:
        command.append('--force')
    subprocess.run(command, check=False, stdout=subprocess.DEVNULL)


def nodes_from_host(doc):
    role = doc.get('role', 'direct')
    if role == 'landing':
        return []
    state = doc.get('state') or {}
    mode = state.get('protocol_mode')
    ip = state.get('public_ip')
    port = int(state.get('listen_port') or 0)
    sni = state.get('sni')
    if not (mode and ip and port and sni):
        return []
    vless = state.get('vless') or {}
    hy2 = state.get('hy2') or {}
    nodes = []

    def add_vless(base, uuid, udp=True, category='直连'):
        if not uuid or not vless:
            return
        nodes.append({
            'id': hashlib.sha256((doc['host_id'] + '|vless|' + base).encode()).hexdigest()[:24],
            'name': protocol_name(base, 'VLESS'),
            'protocol': 'vless',
            'server': ip,
            'port': port,
            'uuid': uuid,
            'sni': sni,
            'public_key': ((vless.get('reality') or {}).get('public_key')),
            'short_id': ((vless.get('reality') or {}).get('short_id')),
            'udp': bool(udp),
            'category': category,
        })

    def add_hy2(base, password, category='直连'):
        if not password or not hy2:
            return
        nodes.append({
            'id': hashlib.sha256((doc['host_id'] + '|hy2|' + base).encode()).hexdigest()[:24],
            'name': protocol_name(base, 'HY2'),
            'protocol': 'hysteria2',
            'server': ip,
            'port': port,
            'password': password,
            'sni': hy2.get('server_name'),
            'obfs_password': hy2.get('obfs_password'),
            'pin': hy2.get('certificate_pin_hex'),
            'udp': True,
            'category': category,
        })

    base = state.get('direct_base_name') or f'{ip}:{port}'
    if mode in ('dual', 'vless'):
        add_vless(base, ((vless.get('direct_user') or {}).get('uuid')), True, '直连')
    if mode in ('dual', 'hy2'):
        add_hy2(base, ((hy2.get('direct_user') or {}).get('password')), '直连')
    if role in ('center-relay', 'relay'):
        for relay in state.get('relays') or []:
            rv = relay.get('vless')
            rh = relay.get('hy2')
            if rv:
                add_vless(relay.get('name') or relay.get('id'), rv.get('client_uuid'), True, 'VPS中转')
            if rh:
                add_hy2(relay.get('name') or relay.get('id'), rh.get('client_password'), 'VPS中转')
        for upstream in state.get('upstream_relays') or []:
            add_vless(upstream.get('name') or upstream.get('id'), upstream.get('client_uuid'), False, '动态代理')
    return nodes


def active_hosts():
    docs = []
    current = time.time()
    for path in HOSTS.glob('*.json'):
        doc = read_json(path, {}) or {}
        if doc.get('disabled'):
            continue
        last = float(doc.get('last_seen_ts') or 0)
        if last and current - last > 72 * 3600 and doc.get('role') in ('direct', 'landing'):
            continue
        docs.append(doc)
    return docs


def all_nodes():
    nodes = []
    seen = set()
    for host in active_hosts():
        for node in nodes_from_host(host):
            if node['id'] in seen:
                continue
            seen.add(node['id'])
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


def auth_token(handler):
    value = handler.headers.get('Authorization', '')
    return value[7:] if value.startswith('Bearer ') else ''


def request_ip(handler):
    cfg = read_json(CFG, {}) or {}
    candidates = []
    if cfg.get('transport_mode') == 'tunnel':
        candidates.append(handler.headers.get('CF-Connecting-IP', '').strip())
    candidates.extend([
        handler.headers.get('X-Forwarded-For', '').split(',')[0].strip(),
        handler.client_address[0],
    ])
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return ipaddress.ip_address(candidate)
        except ValueError:
            continue
    return None


def public_metadata():
    cfg = read_json(CFG, {}) or {}
    return {
        'canonical_base_url': cfg.get('base_url', ''),
        'subscription_url': cfg.get('subscription_url', ''),
        'transport_mode': cfg.get('transport_mode', ''),
    }


def finalize_registration(entry, body):
    doc = {
        'host_id': entry['host_id'],
        'role': entry.get('role', 'direct'),
        'state': body.get('state') or {},
        'meta': body.get('meta') or {},
        'last_seen': now(),
        'last_seen_ts': time.time(),
    }
    atomic_json(HOSTS / f"{entry['host_id']}.json", doc)
    count = regenerate()
    result = {
        'ok': True,
        'registered': True,
        'subscription_refreshed': True,
        'node_count': count,
        'host_id': entry['host_id'],
        'host_token': entry['token'],
    }
    result.update(public_metadata())
    return result


def redacted_path(path, suffix):
    if path == '/' + suffix and suffix:
        if len(suffix) <= 4:
            return '/****'
        return '/' + suffix[:2] + '*' * max(4, len(suffix) - 4) + suffix[-2:]
    return path[:256]


def capture_debug(handler, recognition, suffix):
    if not DEBUG_FLAG.exists():
        return
    headers = {}
    for index, (key, value) in enumerate(handler.headers.items()):
        if index >= 64:
            break
        lower = key.lower()
        headers[key] = '[已隐藏]' if lower in SENSITIVE_HEADERS else str(value)[:512]
    source = request_ip(handler)
    event = {
        'time': now(),
        'source_ip': str(source) if source else '未知',
        'method': handler.command,
        'path': redacted_path(urlparse(handler.path).path, suffix),
        'query_present': bool(urlparse(handler.path).query),
        'backend_http_version': handler.request_version,
        'forwarded_proto': handler.headers.get('X-Forwarded-Proto', ''),
        'host': handler.headers.get('Host', ''),
        'headers': headers,
        'recognized_client': (recognition or {}).get('name', '未识别'),
        'response_format': (recognition or {}).get('format', '无'),
    }
    DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
    with DEBUG_LOG.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + '\n')


class Handler(BaseHTTPRequestHandler):
    server_version = 'StaticResource/3.0'

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
        parsed = urlparse(self.path)
        path = parsed.path
        suffix = str(cfg.get('subscription_suffix') or '')
        subscription_path = '/' + suffix if suffix else ''
        if subscription_path and path == subscription_path:
            recognition = client_adapters.detect_client(self.headers)
            capture_debug(self, recognition, suffix)
            if not recognition:
                return self.send_bytes(
                    415,
                    '未识别订阅客户端，请在服务器运行“客户端请求头识别调试”后重新刷新。\n'.encode(),
                    extra_headers={'X-VVV-Client': 'unknown', 'X-VVV-Format': 'unsupported'},
                )
            nodes = all_nodes()
            content = client_adapters.render(recognition['format'], nodes).encode()
            return self.send_bytes(
                200,
                content,
                recognition['content_type'],
                {
                    'X-VVV-Client': recognition['name'],
                    'X-VVV-Format': recognition['format'],
                },
            )
        if path == '/health':
            return self.send_bytes(200, b'ok\n')
        if path == '/api/v1/hosts':
            if not secrets.compare_digest(auth_token(self), str(cfg.get('master_token') or '')):
                return self.send_bytes(403, b'Forbidden\n')
            return self.send_bytes(
                200,
                json.dumps({'hosts': active_hosts()}, ensure_ascii=False, default=str).encode(),
                'application/json',
            )
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
            if path == '/api/v1/register-direct':
                host_id = str(body.get('host_id') or '').strip()
                role = str(body.get('role') or '')
                if role != 'direct':
                    return self.send_bytes(400, b'Direct role required\n')
                if not re.fullmatch(r'[A-Za-z0-9._-]{8,128}', host_id):
                    return self.send_bytes(400, b'Bad host id\n')
                try:
                    declared_ip = ipaddress.ip_address(str(body.get('public_ip') or '').strip())
                except ValueError:
                    return self.send_bytes(400, b'Bad public ip\n')
                source_ip = request_ip(self)
                if source_ip is None or not declared_ip.is_global:
                    return self.send_bytes(403, b'Public source required\n')
                if source_ip.version == declared_ip.version and source_ip != declared_ip:
                    return self.send_bytes(403, b'Source IP mismatch\n')
                backup('before-host-register')
                entry = next((item for item in registry['hosts'] if item.get('host_id') == host_id), None)
                if entry is None:
                    entry = {'host_id': host_id, 'token': secrets.token_urlsafe(32), 'created_at': now()}
                    registry['hosts'].append(entry)
                entry.update(
                    role='direct',
                    hostname=str(body.get('hostname') or ''),
                    auto_registered=True,
                    source_ip=str(source_ip),
                    updated_at=now(),
                )
                atomic_json(REGISTRY, registry)
                result = finalize_registration(entry, body)
                backup('after-host-register')
                return self.send_bytes(200, json.dumps(result, ensure_ascii=False).encode(), 'application/json')

            if path == '/api/v1/register':
                if not secrets.compare_digest(auth_token(self), str(cfg.get('master_token') or '')):
                    return self.send_bytes(403, b'Forbidden\n')
                host_id = str(body.get('host_id') or '').strip()
                role = str(body.get('role') or '').strip()
                if not re.fullmatch(r'[A-Za-z0-9._-]{8,128}', host_id):
                    return self.send_bytes(400, b'Bad host id\n')
                if role not in ('center-relay', 'center', 'relay', 'direct', 'landing'):
                    return self.send_bytes(400, b'Bad role\n')
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
                doc = {
                    'host_id': host_id,
                    'role': entry.get('role', 'direct'),
                    'state': body.get('state') or {},
                    'meta': body.get('meta') or {},
                    'last_seen': now(),
                    'last_seen_ts': time.time(),
                }
                atomic_json(HOSTS / f'{host_id}.json', doc)
                entry['updated_at'] = now()
                atomic_json(REGISTRY, registry)
                count = regenerate()
                backup('after-node-sync')
                result = {'ok': True, 'subscription_refreshed': True, 'node_count': count}
                result.update(public_metadata())
                return self.send_bytes(200, json.dumps(result, ensure_ascii=False).encode(), 'application/json')

        return self.send_bytes(404, b'Not Found\n')


def serve():
    cfg = read_json(CFG, {}) or {}
    host = str(cfg.get('listen_host') or '127.0.0.1')
    port = int(cfg.get('listen_port') or 18081)
    HOSTS.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    regenerate()
    server = ThreadingHTTPServer((host, port), Handler)
    server.serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('serve')
    commands.add_parser('regenerate')
    args = parser.parse_args()
    if args.command == 'serve':
        serve()
    else:
        print(regenerate())

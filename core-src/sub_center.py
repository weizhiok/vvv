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
from urllib.parse import parse_qs, urlparse

import client_adapters

CFG = Path('/etc/vvv-sub/config.json')
DATA = Path('/var/lib/vvv-sub')
HOSTS = DATA / 'hosts'
OUT = DATA / 'output'
REGISTRY = DATA / 'registry.json'
OVERRIDES = DATA / 'node-overrides.json'
ORDER = DATA / 'node-order.json'
TICKETS = DATA / 'relay-tickets.json'
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
    role = doc.get('role') or ''
    if role == 'landing':
        return []
    states = doc.get('states') or {}
    state = (states.get('direct') or doc.get('state') or {}) if role == 'landing-direct' else (doc.get('state') or {})
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
            'client_up_mbps': 30, 'client_down_mbps': 50,
            'ports': str(((state.get('port_hopping') or {}).get('ports')) or port),
            'hop_interval_seconds': int(((state.get('port_hopping') or {}).get('hop_interval_seconds')) or 30),
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
            raw_name = str(relay.get('name') or '')
            country = raw_name[:2].upper() if len(raw_name) >= 3 and raw_name[:2].isalpha() and raw_name[2] == '-' else ''
            relay_base = (country + '-' if country else '') + f'中转-{ip}:{port}'
            if rv:
                add_vless(relay_base, rv.get('client_uuid'), True, 'VPS中转', relay.get('id'), expected_exit_ip=relay.get('remote_ip'))
            if rh:
                add_hy2(relay_base, rh.get('client_password'), 'VPS中转', relay.get('id'), expected_exit_ip=relay.get('remote_ip'))
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
        if last and current - last > 72 * 3600 and doc.get('role') in ('direct', 'landing', 'landing-direct'):
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
    active_ids = [node['id'] for node in nodes]
    stored = read_json(ORDER, {'schema': 1, 'ids': []}) or {'schema': 1, 'ids': []}
    existing = [str(value) for value in stored.get('ids', []) if str(value) in seen]
    ordered_ids = existing + [value for value in active_ids if value not in existing]
    if stored.get('schema') != 1 or stored.get('ids') != ordered_ids:
        atomic_json(ORDER, {'schema': 1, 'ids': ordered_ids, 'updated_at': now()})
    positions = {value: index for index, value in enumerate(ordered_ids)}
    nodes.sort(key=lambda node: positions.get(node['id'], len(positions)))
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
        'state': body.get('state') or {}, 'states': body.get('states') or {}, 'meta': body.get('meta') or {},
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


def source_relay_active(source_host_id, relay_id):
    doc = read_json(HOSTS / f'{source_host_id}.json', {}) or {}
    state = doc.get('state') or {}
    return any(str(item.get('id')) == str(relay_id) for item in state.get('relays') or [])


def relay_ticket_record(source_host_id, relay_id):
    store = read_json(TICKETS, {'tickets': []}) or {'tickets': []}
    rows = store.setdefault('tickets', [])
    current = next((row for row in rows if row.get('source_host_id') == source_host_id and row.get('relay_id') == relay_id), None)
    if current is None:
        current = {'source_host_id': source_host_id, 'relay_id': relay_id,
                   'registration_token': secrets.token_urlsafe(32), 'created_at': now()}
        rows.append(current)
    current['updated_at'] = now()
    atomic_json(TICKETS, store)
    return current



SINGLE_NODE_FORMATS = {'loon', 'nekobox'}


def resolve_subscription_request(headers, query_string, nodes):
    query = parse_qs(str(query_string or ''), keep_blank_values=True)
    requested_format = str((query.get('format') or [''])[0]).strip()
    requested_node = str((query.get('node') or [''])[0]).strip()
    if requested_format or requested_node:
        if requested_format not in SINGLE_NODE_FORMATS:
            raise ValueError('单节点订阅格式无效。')
        if not re.fullmatch(r'[0-9a-f]{24}', requested_node):
            raise ValueError('单节点订阅 ID 无效。')
        selected = [node for node in nodes if str(node.get('id')) == requested_node]
        if not selected:
            raise LookupError('单节点订阅不存在。')
        return {
            'name': 'Loon 单节点导入' if requested_format == 'loon' else 'NekoBoxForAndroid 单节点订阅',
            'format': requested_format,
            'content_type': client_adapters.RENDERERS[requested_format]['content_type'],
        }, selected
    recognition = client_adapters.detect_client(headers)
    if not recognition:
        return None, nodes
    return recognition, nodes


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
        parsed = urlparse(self.path)
        path = parsed.path
        suffix = str(cfg.get('subscription_suffix') or '')
        if suffix and path == '/' + suffix:
            try:
                recognition, nodes = resolve_subscription_request(self.headers, parsed.query, all_nodes())
            except ValueError as exc:
                return self.send_bytes(400, (str(exc) + '\n').encode())
            except LookupError as exc:
                return self.send_bytes(404, (str(exc) + '\n').encode())
            capture_debug(self, recognition, suffix)
            if not recognition:
                return self.send_bytes(415, '未识别订阅客户端。\n'.encode(), extra_headers={'X-VVV-Client': 'unknown'})
            return self.send_bytes(200, client_adapters.render(recognition['format'], nodes).encode(),
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
            if path == '/api/v1/relay-ticket':
                host_id = str(body.get('host_id') or '').strip()
                relay_id = str(body.get('relay_id') or '').strip()
                entry = next((item for item in registry['hosts'] if item.get('host_id') == host_id), None)
                if entry is None or not secrets.compare_digest(auth_token(self), str(entry.get('token') or '')):
                    return self.send_bytes(403, b'Forbidden\n')
                if entry.get('role') not in ('center-relay', 'relay') or not re.fullmatch(r'[A-Za-z0-9._-]{1,128}', relay_id):
                    return self.send_bytes(400, b'Bad relay ticket request\n')
                if not source_relay_active(host_id, relay_id):
                    return self.send_bytes(409, b'Relay is not synchronized\n')
                ticket = relay_ticket_record(host_id, relay_id)
                bootstrap = {'api_base_url': str(cfg.get('api_base_url') or ''), 'relay_id': relay_id,
                             'registration_token': ticket['registration_token']}
                return self.send_bytes(200, json.dumps({'ok': True, 'subscription_bootstrap': bootstrap}, ensure_ascii=False).encode(), 'application/json')
            if path == '/api/v1/register-ticket':
                relay_id = str(body.get('relay_id') or '').strip()
                supplied = str(body.get('registration_token') or '')
                role = str(body.get('role') or '').strip()
                host_id = str(body.get('host_id') or '').strip()
                store = read_json(TICKETS, {'tickets': []}) or {'tickets': []}
                ticket = next((row for row in store.get('tickets', []) if row.get('relay_id') == relay_id and
                               secrets.compare_digest(str(row.get('registration_token') or ''), supplied)), None)
                if ticket is None or not source_relay_active(ticket.get('source_host_id'), relay_id):
                    return self.send_bytes(403, b'Invalid or revoked relay ticket\n')
                if role not in ('landing', 'landing-direct', 'direct') or not re.fullmatch(r'[A-Za-z0-9._-]{8,128}', host_id):
                    return self.send_bytes(400, b'Bad ticket registration\n')
                entry = next((item for item in registry['hosts'] if item.get('host_id') == host_id), None)
                if entry is None:
                    entry = {'host_id': host_id, 'token': secrets.token_urlsafe(32), 'created_at': now()}
                    registry['hosts'].append(entry)
                entry.update(role=role, hostname=str(body.get('hostname') or ''), relay_id=relay_id, updated_at=now())
                atomic_json(REGISTRY, registry)
                result = finalize_registration(entry, body)
                result['registration_method'] = 'JPR3-ticket'
                return self.send_bytes(200, json.dumps(result, ensure_ascii=False).encode(), 'application/json')
            if path == '/api/v1/register':
                if not secrets.compare_digest(auth_token(self), str(cfg.get('master_token') or '')):
                    return self.send_bytes(403, b'Forbidden\n')
                host_id = str(body.get('host_id') or '').strip()
                role = str(body.get('role') or '').strip()
                if not re.fullmatch(r'[A-Za-z0-9._-]{8,128}', host_id) or role not in ('center-relay', 'center', 'relay', 'direct', 'landing', 'landing-direct'):
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
                       'states': body.get('states') or {}, 'meta': body.get('meta') or {}, 'last_seen': now(), 'last_seen_ts': time.time()}
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
    if not (1 <= len(name) <= 64) or '|' in name or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise SystemExit('名称必须是 1-64 个字符，且不能包含 |、换行或控制字符。')
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



def parse_pipe_values(value):
    text = str(value or '').strip()
    text = text.strip('|').strip()
    if not text:
        return []
    return [part.strip() for part in re.split(r'\|+', text) if part.strip()]


def validate_display_names(names, expected):
    if len(names) != expected:
        raise SystemExit(f'数量不一致：当前共有 {expected} 个节点，但输入了 {len(names)} 个名称。')
    for name in names:
        if not (1 <= len(name) <= 64) or '|' in name or any(ord(c) < 32 or ord(c) == 127 for c in name):
            raise SystemExit('每个名称必须是 1-64 个字符，且不能包含 |、换行或控制字符。')
    if len(names) != len(set(names)):
        raise SystemExit('名称不能重复。')


def bulk_rename(value):
    nodes = all_nodes()
    names = parse_pipe_values(value)
    validate_display_names(names, len(nodes))
    previous = read_json(OVERRIDES, {}) or {}
    updated = dict(previous)
    timestamp = now()
    for node, name in zip(nodes, names):
        updated[node['id']] = {'display_name': name, 'updated_at': timestamp}
    backup('before-node-bulk-rename', True)
    try:
        atomic_json(OVERRIDES, updated)
        count = regenerate()
    except Exception:
        atomic_json(OVERRIDES, previous)
        regenerate()
        raise
    backup('after-node-bulk-rename', True)
    return count


def reorder_nodes(value):
    nodes = all_nodes()
    names = parse_pipe_values(value)
    if len(names) != len(nodes):
        raise SystemExit(f'数量不一致：当前共有 {len(nodes)} 个节点，但输入了 {len(names)} 个名称。')
    if len(names) != len(set(names)):
        raise SystemExit('排序列表中不能出现重复名称。')
    by_name = {node['name']: node['id'] for node in nodes}
    if len(by_name) != len(nodes):
        raise SystemExit('当前订阅存在重名节点，请先批量重命名后再排序。')
    missing = [name for name in names if name not in by_name]
    extra = [node['name'] for node in nodes if node['name'] not in set(names)]
    if missing or extra:
        details = []
        if missing:
            details.append('不存在：' + '、'.join(missing))
        if extra:
            details.append('缺少：' + '、'.join(extra))
        raise SystemExit('排序列表必须完整使用当前节点名称；' + '；'.join(details))
    previous = read_json(ORDER, {'schema': 1, 'ids': []}) or {'schema': 1, 'ids': []}
    updated = {'schema': 1, 'ids': [by_name[name] for name in names], 'updated_at': now()}
    backup('before-node-reorder', True)
    try:
        atomic_json(ORDER, updated)
        count = regenerate()
    except Exception:
        atomic_json(ORDER, previous)
        regenerate()
        raise
    backup('after-node-reorder', True)
    return count


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
    if not ORDER.exists():
        atomic_json(ORDER, {'schema': 1, 'ids': []})
    regenerate()
    ThreadingHTTPServer((str(cfg.get('listen_host') or '0.0.0.0'), int(cfg.get('listen_port') or 18081)), Handler).serve_forever()


def list_hosts():
    registry = read_json(REGISTRY, {'hosts': []}) or {'hosts': []}
    return registry.get('hosts', [])


def host_summary(entry):
    host_id = str(entry.get('host_id') or '')
    doc = read_json(HOSTS / f'{host_id}.json', {}) or {}
    role = str(entry.get('role') or doc.get('role') or 'unknown')
    states = doc.get('states') or {}
    state = (states.get('direct') or doc.get('state') or {}) if role == 'landing-direct' else (doc.get('state') or states.get('direct') or {})
    raw_ip = (state.get('public_ip') or state.get('japan_public_ip') or
              state.get('remote_public_ip') or (doc.get('meta') or {}).get('public_ip') or '')
    try:
        public_ip = str(ipaddress.ip_address(str(raw_ip).strip()))
    except ValueError:
        public_ip = '未知IP'
    timestamp = str(entry.get('updated_at') or doc.get('last_seen') or entry.get('created_at') or '')
    matched = re.match(r'^(\d{4}-\d{2}-\d{2})', timestamp)
    sync_date = matched.group(1) if matched else '未知日期'
    return {'host_id': host_id, 'role': role, 'public_ip': public_ip, 'sync_date': sync_date}


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
    hosts = commands.add_parser('list-hosts'); hosts.add_argument('--tsv', action='store_true'); hosts.add_argument('--summary-tsv', action='store_true')
    showh = commands.add_parser('show-host'); showh.add_argument('host_id')
    rename = commands.add_parser('rename-node'); rename.add_argument('node_id'); rename.add_argument('name')
    bulk = commands.add_parser('bulk-rename'); bulk.add_argument('names')
    reorder = commands.add_parser('reorder-nodes'); reorder.add_argument('names')
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
        if args.summary_tsv:
            for entry in rows:
                summary = host_summary(entry)
                print(f"{summary['host_id']}\t{summary['role']}\t{summary['public_ip']}\t{summary['sync_date']}")
        elif args.tsv:
            for entry in rows:
                print(f"{entry.get('host_id','')}\t{entry.get('role','')}\t{entry.get('hostname','')}\t{entry.get('display_name','')}\t{entry.get('updated_at','')}")
        else:
            print(json.dumps({'hosts': rows}, ensure_ascii=False, indent=2))
    elif args.command == 'show-host':
        print(json.dumps(show_host(args.host_id), ensure_ascii=False, indent=2))
    elif args.command == 'rename-node':
        rename_node(args.node_id, args.name)
    elif args.command == 'bulk-rename':
        print(bulk_rename(args.names))
    elif args.command == 'reorder-nodes':
        print(reorder_nodes(args.names))
    elif args.command == 'reset-name':
        reset_name(args.node_id)
    elif args.command == 'delete-host':
        delete_host(args.host_id)

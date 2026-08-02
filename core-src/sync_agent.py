#!/usr/bin/env python3
import argparse
import base64
import hashlib
import json
import os
import platform
import socket
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

CFG = Path('/etc/vvv/client.json')
CENTER_CFG = Path('/etc/vvv-sub/config.json')
MAIN_STATE = Path('/etc/jp-relay/state.json')
LANDING_STATE = Path('/etc/jp-relay/landing-state.json')


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default


def atomic(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def post(url, token, obj):
    data = json.dumps(obj, ensure_ascii=False).encode()
    headers = {'Content-Type': 'application/json', 'User-Agent': 'VVV-Sync/3.0'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = Request(url, data=data, method='POST', headers=headers)
    with urlopen(request, timeout=30) as response:
        payload = response.read().decode()
        return json.loads(payload)


def decode_code(code):
    code = code.strip()
    raw = code.split('.', 1)[1] if code.startswith('VVV1.') else code
    raw += '=' * ((4 - len(raw) % 4) % 4)
    return json.loads(base64.urlsafe_b64decode(raw).decode())


def format_base(scheme, host, port=None):
    if ':' in host and not host.startswith('['):
        host = '[' + host + ']'
    if port is None or (scheme == 'https' and port == 443) or (scheme == 'http' and port == 80):
        return f'{scheme}://{host}'
    return f'{scheme}://{host}:{port}'


def explicit_base(value):
    parsed = urlparse(value)
    if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname:
        raise ValueError('invalid center address')
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError('invalid center port') from exc
    return format_base(parsed.scheme.lower(), parsed.hostname, port)


def center_candidates(value):
    value = str(value or '').strip().rstrip('/')
    if not value:
        raise SystemExit('订阅中心地址不能为空。')
    if '://' in value:
        try:
            return [explicit_base(value)]
        except ValueError as exc:
            raise SystemExit('订阅中心地址格式错误。') from exc
    parsed = urlparse('//' + value)
    if not parsed.hostname:
        raise SystemExit('订阅中心地址格式错误。')
    try:
        explicit_port = parsed.port
    except ValueError as exc:
        raise SystemExit('订阅中心端口格式错误。') from exc
    host = parsed.hostname
    if explicit_port is not None:
        if not 1 <= explicit_port <= 65535:
            raise SystemExit('订阅中心端口必须是 1-65535。')
        return [format_base('https', host, explicit_port), format_base('http', host, explicit_port)]
    # Direct VVV defaults to 8443. Tunnel mode uses standard HTTPS/443.
    return [format_base('https', host, 8443), format_base('https', host, 443), format_base('http', host, 8443)]


def stable_id():
    seed = '|'.join([socket.gethostname(), platform.machine()])
    try:
        seed += '|' + Path('/etc/machine-id').read_text().strip()
    except Exception:
        pass
    return hashlib.sha256(seed.encode()).hexdigest()[:32]


def state_path(role):
    return LANDING_STATE if role == 'landing' else MAIN_STATE


def local_api_for(role, public_base):
    if role not in ('center', 'center-relay') or not CENTER_CFG.exists():
        return public_base
    center = read(CENTER_CFG, {}) or {}
    port = int(center.get('listen_port') or 18081)
    return f'http://127.0.0.1:{port}'


def api_base(cfg):
    return (cfg.get('api_base_url') or cfg['base_url']).rstrip('/')


def snapshot_payload(role):
    state = read(state_path(role), {}) or {}
    return {
        'state': state,
        'meta': {
            'hostname': socket.gethostname(),
            'role': role,
            'timestamp': time.time(),
            'xray_active': os.system('systemctl is-active --quiet xray') == 0,
            'sing_box_active': os.system('systemctl is-active --quiet sing-box') == 0,
        },
    }


def require_registration_success(response):
    if not isinstance(response, dict):
        raise SystemExit('订阅中心返回了无效的注册结果。')
    required = ('ok', 'registered', 'subscription_refreshed')
    if any(response.get(key) is not True for key in required):
        raise SystemExit('订阅中心未返回完整的注册成功标识，未确认订阅刷新。')
    if not response.get('host_token'):
        raise SystemExit('订阅中心注册响应缺少主机令牌。')
    return response


def canonicalize_cfg(cfg, response, internal=False):
    canonical = str((response or {}).get('canonical_base_url') or '').rstrip('/')
    if canonical.startswith(('http://', 'https://')):
        cfg['base_url'] = canonical
        if not internal:
            cfg['api_base_url'] = canonical
        if canonical.startswith('https://'):
            cfg['https_pinned'] = True
    subscription_url = str((response or {}).get('subscription_url') or '')
    if subscription_url:
        cfg['subscription_url'] = subscription_url
    return cfg


def register(code, role):
    decoded = decode_code(code)
    public_base = str(decoded['base_url']).rstrip('/')
    internal_base = local_api_for(role, public_base)
    master = decoded['master_token']
    host_id = stable_id()
    payload = {'host_id': host_id, 'role': role, 'hostname': socket.gethostname()}
    payload.update(snapshot_payload(role))
    response = require_registration_success(post(internal_base + '/api/v1/register', master, payload))
    current = time.time()
    cfg = {
        'schema': 3,
        'base_url': public_base,
        'api_base_url': internal_base,
        'host_id': host_id,
        'host_token': response['host_token'],
        'role': role,
        'registered_at': current,
        'last_sync': current,
        'last_result': response,
        'https_pinned': public_base.startswith('https://'),
    }
    canonicalize_cfg(cfg, response, internal=internal_base.startswith('http://127.0.0.1:'))
    atomic(CFG, cfg)
    return response


def register_direct(center_address):
    state = read(MAIN_STATE, {}) or {}
    public_ip = str(state.get('public_ip') or '').strip()
    if not public_ip:
        raise SystemExit('本机代理状态缺少公网 IP，无法自动注册。')
    host_id = stable_id()
    payload = {
        'host_id': host_id,
        'role': 'direct',
        'hostname': socket.gethostname(),
        'public_ip': public_ip,
    }
    payload.update(snapshot_payload('direct'))
    errors = []
    response = None
    public_base = ''
    for candidate in center_candidates(center_address):
        try:
            response = require_registration_success(post(candidate + '/api/v1/register-direct', '', payload))
            public_base = candidate
            break
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, SystemExit) as exc:
            errors.append(f'{candidate}: {exc}')
    if response is None:
        raise SystemExit('无法连接订阅中心：' + '；'.join(errors[-3:]))
    current = time.time()
    cfg = {
        'schema': 3,
        'base_url': public_base,
        'api_base_url': public_base,
        'host_id': host_id,
        'host_token': response['host_token'],
        'role': 'direct',
        'registered_at': current,
        'last_sync': current,
        'last_result': response,
        'registration_method': 'center-address',
        'https_pinned': public_base.startswith('https://'),
    }
    canonicalize_cfg(cfg, response)
    atomic(CFG, cfg)
    return response


def https_upgrade_base(base):
    parsed = urlparse(base)
    if parsed.scheme != 'http' or not parsed.hostname:
        return None
    return format_base('https', parsed.hostname, parsed.port)


def sync():
    cfg = read(CFG)
    if not cfg:
        raise SystemExit('尚未配置订阅同步。')
    payload = {'host_id': cfg['host_id']}
    payload.update(snapshot_payload(cfg.get('role', 'direct')))
    bases = []
    internal = str(cfg.get('api_base_url') or '').startswith('http://127.0.0.1:')
    current_base = api_base(cfg)
    if not internal and not cfg.get('https_pinned'):
        upgraded = https_upgrade_base(current_base)
        if upgraded:
            bases.append(upgraded)
    bases.append(current_base)
    response = None
    used_base = ''
    errors = []
    for base in dict.fromkeys(bases):
        try:
            response = post(base + '/api/v1/sync', cfg['host_token'], payload)
            used_base = base
            break
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            errors.append(f'{base}: {exc}')
    if response is None:
        raise SystemExit('订阅同步失败：' + '；'.join(errors[-2:]))
    if used_base.startswith('https://') and not internal:
        cfg['base_url'] = used_base
        cfg['api_base_url'] = used_base
        cfg['https_pinned'] = True
        cfg['https_upgraded_at'] = time.time()
    canonicalize_cfg(cfg, response, internal=internal)
    cfg['last_sync'] = time.time()
    cfg['last_result'] = response
    atomic(CFG, cfg)
    print(json.dumps(response, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='cmd', required=True)
    register_cmd = commands.add_parser('register')
    register_cmd.add_argument('code')
    register_cmd.add_argument('role', choices=['center-relay', 'center', 'relay', 'direct', 'landing'])
    direct_cmd = commands.add_parser('register-direct')
    direct_cmd.add_argument('center_address')
    commands.add_parser('sync')
    args = parser.parse_args()
    if args.cmd == 'register':
        print(json.dumps(register(args.code, args.role), ensure_ascii=False))
    elif args.cmd == 'register-direct':
        print(json.dumps(register_direct(args.center_address), ensure_ascii=False))
    else:
        sync()

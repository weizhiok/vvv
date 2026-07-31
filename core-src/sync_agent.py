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
    req = Request(url, data=data, method='POST', headers={
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + token,
        'User-Agent': 'VVV-Sync/2.0',
    })
    with urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode())


def decode_code(code):
    code = code.strip()
    raw = code.split('.', 1)[1] if code.startswith('VVV1.') else code
    raw += '=' * ((4 - len(raw) % 4) % 4)
    return json.loads(base64.urlsafe_b64decode(raw).decode())


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


def register(code, role):
    decoded = decode_code(code)
    public_base = decoded['base_url'].rstrip('/')
    internal_base = local_api_for(role, public_base)
    master = decoded['master_token']
    host_id = stable_id()
    response = post(internal_base + '/api/v1/register', master, {
        'host_id': host_id,
        'role': role,
        'hostname': socket.gethostname(),
    })
    cfg = {
        'schema': 2,
        'base_url': public_base,
        'api_base_url': internal_base,
        'host_id': host_id,
        'host_token': response['host_token'],
        'role': role,
        'registered_at': time.time(),
    }
    atomic(CFG, cfg)
    return cfg


def sync():
    cfg = read(CFG)
    if not cfg:
        raise SystemExit('尚未配置订阅同步。')
    path = state_path(cfg.get('role', 'direct'))
    state = read(path, {})
    response = post(api_base(cfg) + '/api/v1/sync', cfg['host_token'], {
        'host_id': cfg['host_id'],
        'state': state,
        'meta': {
            'hostname': socket.gethostname(),
            'role': cfg['role'],
            'timestamp': time.time(),
            'xray_active': os.system('systemctl is-active --quiet xray') == 0,
            'sing_box_active': os.system('systemctl is-active --quiet sing-box') == 0,
        },
    })
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
    commands.add_parser('sync')
    args = parser.parse_args()
    if args.cmd == 'register':
        register(args.code, args.role)
        sync()
    else:
        sync()

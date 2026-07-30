#!/usr/bin/env python3
import argparse, base64, hashlib, json, os, platform, socket, time
from pathlib import Path
from urllib.request import Request, urlopen

CFG = Path('/etc/vvv/client.json')
STATE = Path('/etc/jp-relay/state.json')
CENTER_CFG = Path('/etc/vvv-sub/config.json')

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
        'User-Agent': 'VVV-Sync/1.0',
    })
    with urlopen(req, timeout=25) as response:
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

def local_api_for(role, public_base):
    if role not in ('center', 'all') or not CENTER_CFG.exists():
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
    response = post(internal_base + '/api/v1/register', master, {'host_id': host_id, 'role': role})
    cfg = {
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
    state = read(STATE, {})
    if not cfg:
        raise SystemExit('尚未配置订阅同步。')
    response = post(api_base(cfg) + '/api/v1/sync', cfg['host_token'], {
        'host_id': cfg['host_id'],
        'state': state,
        'meta': {'hostname': socket.gethostname(), 'role': cfg['role'], 'timestamp': time.time()},
    })
    cfg['last_sync'] = time.time()
    cfg['last_result'] = response
    atomic(CFG, cfg)
    print(json.dumps(response, ensure_ascii=False))

def pull_backup(dest):
    cfg = read(CFG)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    req = Request(api_base(cfg) + '/api/v1/backup', headers={
        'Authorization': 'Bearer ' + cfg['host_token'],
        'User-Agent': 'VVV-Backup/1.0',
    })
    with urlopen(req, timeout=60) as response:
        data = response.read()
    path = dest / time.strftime('vvv-center-%Y%m%d-%H%M%S.enc')
    path.write_bytes(data)
    os.chmod(path, 0o600)
    latest = dest / 'latest.enc'
    tmp = dest / '.latest.tmp'
    tmp.write_bytes(data)
    os.chmod(tmp, 0o600)
    os.replace(tmp, latest)
    files = sorted(dest.glob('vvv-center-*.enc'))
    for old in files[:-30]:
        old.unlink()
    print(path)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='cmd', required=True)
    register_cmd = commands.add_parser('register')
    register_cmd.add_argument('code')
    register_cmd.add_argument('role')
    commands.add_parser('sync')
    backup_cmd = commands.add_parser('pull-backup')
    backup_cmd.add_argument('dest')
    args = parser.parse_args()
    if args.cmd == 'register':
        register(args.code, args.role)
        sync()
    elif args.cmd == 'sync':
        sync()
    else:
        pull_backup(args.dest)

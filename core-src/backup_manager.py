#!/usr/bin/env python3
import argparse
import configparser
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

CFG = Path('/etc/vvv-sub/config.json')
DATA = Path('/var/lib/vvv-sub')
BACKUPS = DATA / 'backups'
CLOUD_CFG = Path('/etc/vvv-sub/cloud.json')
RCLONE_CFG = Path('/etc/vvv-sub/rclone.conf')
REMOTE_NAME = 'vvvcloud'
REMOTE_ROOT = 'vvv'
REMOTE_BACKUPS = f'{REMOTE_ROOT}/backups'
REMOTE_RECOVER = f'{REMOTE_ROOT}/RecoverKey.ini'
REMOTE_INDEX = f'{REMOTE_ROOT}/BackupIndex.json'
MAX_COUNT = 100
MAX_BYTES = 1024 ** 3
BACKUP_RE = re.compile(r'^VVV_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})_([A-Za-z0-9_-]{1,64})_([A-F0-9]{8})\.enc$')

CONFIG_FILES = [
    Path('/etc/vvv-sub/config.json'),
    Path('/var/lib/vvv-sub/registry.json'),
    Path('/var/lib/vvv-sub/node-overrides.json'),
    Path('/var/lib/vvv-sub/hosts'),
    Path('/etc/jp-relay/state.json'),
    Path('/etc/jp-relay/landing-state.json'),
    Path('/etc/jp-relay/pairing-key.txt'),
    Path('/etc/vvv-landing'),
    Path('/var/lib/vvv-sub/relay-tickets.json'),
    Path('/etc/sing-box/tls'),
    Path('/etc/vvv/client.json'),
    Path('/etc/vvv/roles.json'),
    Path('/etc/vvv-sub/cloud.json'),
    Path('/etc/vvv-sub/cloudflared.token'),
]


def now_iso():
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
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump(obj, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
        handle.flush(); os.fsync(handle.fileno())
    os.chmod(tmp, mode); os.replace(tmp, path)


def safe_reason(reason):
    value = re.sub(r'[^A-Za-z0-9_-]+', '-', str(reason).strip())
    return value.strip('-')[:64] or 'change'


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def temporary_node_ids():
    result = set()
    for host_path in Path('/var/lib/vvv-sub/hosts').glob('*.json'):
        doc = read_json(host_path, {}) or {}
        host_id = str(doc.get('host_id') or '')
        state = doc.get('state') or {}
        for item in state.get('temporary_nodes', []):
            temp_id = str(item.get('id') or '')
            if not host_id or not temp_id:
                continue
            if (item.get('vless') or {}).get('client_uuid'):
                result.add(hashlib.sha256(f'{host_id}|vless|{temp_id}'.encode()).hexdigest()[:24])
            if (item.get('hy2') or {}).get('client_password'):
                result.add(hashlib.sha256(f'{host_id}|hy2|{temp_id}'.encode()).hexdigest()[:24])
    return result


def sanitized_json(path):
    obj = read_json(path)
    if not isinstance(obj, dict):
        return None
    path = Path(path)
    if path in (Path('/etc/jp-relay/state.json'), Path('/etc/jp-relay/landing-state.json')):
        obj['temporary_nodes'] = []
        obj.pop('temporary_relays', None)
    if isinstance(obj.get('state'), dict):
        obj['state']['temporary_nodes'] = []
        obj['state'].pop('temporary_relays', None)
    if path == Path('/var/lib/vvv-sub/node-overrides.json'):
        temp_ids = temporary_node_ids()
        obj = {key: value for key, value in obj.items() if key not in temp_ids}
    return (json.dumps(obj, ensure_ascii=False, indent=2) + '\n').encode()


def snapshot_digest():
    digest = hashlib.sha256()
    for path in CONFIG_FILES:
        if path.is_file():
            data = sanitized_json(path) if path.suffix == '.json' else path.read_bytes()
            if data is not None:
                digest.update(str(path).encode() + b'\0' + data)
        elif path.is_dir():
            for item in sorted(x for x in path.rglob('*') if x.is_file()):
                data = sanitized_json(item) if item.suffix == '.json' else item.read_bytes()
                if data is not None:
                    digest.update(str(item).encode() + b'\0' + data)
    return digest.hexdigest()


def add_source(tar, path, temp_root):
    if not path.exists():
        return
    if path.is_file():
        data = sanitized_json(path) if path.suffix == '.json' else path.read_bytes()
        if data is None:
            return
        staged = temp_root / str(path).lstrip('/')
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes(data); os.chmod(staged, path.stat().st_mode & 0o777)
        tar.add(staged, arcname=str(path).lstrip('/'), recursive=False)
        return
    for item in sorted(x for x in path.rglob('*') if x.is_file()):
        data = sanitized_json(item) if item.suffix == '.json' else item.read_bytes()
        if data is None:
            continue
        staged = temp_root / str(item).lstrip('/')
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes(data); os.chmod(staged, item.stat().st_mode & 0o777)
        tar.add(staged, arcname=str(item).lstrip('/'), recursive=False)


def local_entries():
    entries = []
    for meta_path in BACKUPS.glob('VVV_*.json'):
        meta = read_json(meta_path, {}) or {}
        enc = BACKUPS / str(meta.get('filename') or meta_path.with_suffix('.enc').name)
        if enc.exists() and BACKUP_RE.match(enc.name):
            entries.append((float(meta.get('created_ts') or enc.stat().st_mtime), enc, meta_path, meta))
    return sorted(entries, key=lambda row: row[0], reverse=True)


def enforce_local_limits():
    entries = local_entries()
    total = sum(row[1].stat().st_size for row in entries)
    while len(entries) > MAX_COUNT or (total > MAX_BYTES and len(entries) > 1):
        _, enc, meta, _ = entries.pop()
        total -= enc.stat().st_size
        enc.unlink(missing_ok=True); meta.unlink(missing_ok=True)
    atomic_json(BACKUPS / 'BackupIndex.json', {'schema': 1, 'updated_at': now_iso(), 'backups': [row[3] for row in entries]})


def cloud_enabled():
    return (read_json(CLOUD_CFG, {}) or {}).get('enabled') is True


def rclone(*args, check=True, capture=False):
    command = ['rclone', '--config', str(RCLONE_CFG), *args]
    return subprocess.run(command, check=check, text=True, capture_output=capture)


def remote(path):
    return f'{REMOTE_NAME}:{path}'


def write_recover_key(target):
    cfg = read_json(CFG, {}) or {}
    cloud = read_json(CLOUD_CFG, {}) or {}
    parser = configparser.ConfigParser()
    parser['VVV'] = {
        'format': 'VVV_RECOVERY_1',
        'center_id': hashlib.sha256(str(cfg.get('master_token', '')).encode()).hexdigest()[:24],
        'provider': str(cloud.get('provider', '')),
        'backup_directory': REMOTE_BACKUPS,
        'backup_index': REMOTE_INDEX,
        'recovery_password': str(cfg.get('recovery_password', '')),
        'created_at': str(cloud.get('created_at') or now_iso()),
        'updated_at': now_iso(),
    }
    with Path(target).open('w', encoding='utf-8') as handle:
        parser.write(handle)
    os.chmod(target, 0o600)


def upload_control_files():
    if not cloud_enabled():
        return
    with tempfile.TemporaryDirectory(prefix='vvv-cloud-control.') as td:
        root = Path(td)
        recover = root / 'RecoverKey.ini'
        index = root / 'BackupIndex.json'
        write_recover_key(recover)
        remote_names = {str(row.get('Name') or '') for row in remote_inventory()}
        rows = [row[3] for row in local_entries() if row[1].name in remote_names]
        atomic_json(index, {'schema': 1, 'updated_at': now_iso(), 'backups': rows})
        rclone('mkdir', remote(REMOTE_ROOT))
        rclone('mkdir', remote(REMOTE_BACKUPS))
        rclone('copyto', str(recover), remote(REMOTE_RECOVER), '--retries', '5')
        rclone('copyto', str(index), remote(REMOTE_INDEX), '--retries', '5')


def remote_inventory():
    result = rclone('lsjson', remote(REMOTE_BACKUPS), '--files-only', capture=True)
    rows = json.loads(result.stdout or '[]')
    return sorted([row for row in rows if BACKUP_RE.match(str(row.get('Name', '')))], key=lambda row: row.get('ModTime', ''), reverse=True)


def enforce_cloud_limits():
    if not cloud_enabled():
        return
    rows = remote_inventory()
    total = sum(int(row.get('Size') or 0) for row in rows)
    while len(rows) > MAX_COUNT or (total > MAX_BYTES and len(rows) > 1):
        row = rows.pop()
        total -= int(row.get('Size') or 0)
        name = row['Name']
        rclone('deletefile', remote(f'{REMOTE_BACKUPS}/{name}'), check=False)
        rclone('deletefile', remote(f'{REMOTE_BACKUPS}/{Path(name).with_suffix(".json").name}'), check=False)


def cloud_upload(enc, meta_path):
    if not cloud_enabled():
        return False
    if not shutil.which('rclone') or not RCLONE_CFG.exists():
        raise RuntimeError('云备份已开启，但 rclone 或授权配置不存在')
    rclone('mkdir', remote(REMOTE_BACKUPS))
    rclone('copyto', str(enc), remote(f'{REMOTE_BACKUPS}/{enc.name}'), '--retries', '5')
    rclone('copyto', str(meta_path), remote(f'{REMOTE_BACKUPS}/{meta_path.name}'), '--retries', '5')
    enforce_cloud_limits()
    return True


def create_backup(reason, force=False):
    cfg = read_json(CFG)
    if not cfg:
        raise SystemExit('订阅中心尚未安装。')
    BACKUPS.mkdir(parents=True, exist_ok=True); os.chmod(BACKUPS, 0o700)
    digest = snapshot_digest()
    latest_meta = read_json(BACKUPS / 'latest.json', {}) or {}
    if not force and latest_meta.get('source_sha256') == digest:
        return {'skipped': True, 'reason': 'unchanged', 'file': latest_meta.get('filename')}
    stamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    random_id = os.urandom(4).hex().upper()
    base = f'VVV_{stamp}_{safe_reason(reason)}_{random_id}'
    enc = BACKUPS / f'{base}.enc'; meta_path = BACKUPS / f'{base}.json'
    with tempfile.TemporaryDirectory(prefix='vvv-backup.') as td:
        root = Path(td); staged = root / 'files'; tar_path = root / 'vvv-config.tar.gz'
        with tarfile.open(tar_path, 'w:gz') as tar:
            for source in CONFIG_FILES:
                add_source(tar, source, staged)
            manifest_data = {'schema': 2, 'created_at': now_iso(), 'reason': reason, 'source_sha256': digest,
                             'config_only': True, 'temporary_nodes_included': False}
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            tar.add(manifest, arcname='manifest.json')
        tmp_enc = BACKUPS / f'.{base}.tmp'
        env = os.environ.copy(); env['VVV_BACKUP_PASSWORD'] = str(cfg['recovery_password'])
        subprocess.run(['openssl', 'enc', '-aes-256-cbc', '-salt', '-pbkdf2', '-pass', 'env:VVV_BACKUP_PASSWORD',
                        '-in', str(tar_path), '-out', str(tmp_enc)], check=True, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.chmod(tmp_enc, 0o600); os.replace(tmp_enc, enc)
    if enc.stat().st_size > MAX_BYTES:
        enc.unlink(missing_ok=True)
        raise RuntimeError('单份纯配置备份异常超过 1 GiB，已拒绝保留。')
    meta = {'schema': 2, 'created_at': now_iso(), 'created_ts': time.time(), 'reason': reason,
            'filename': enc.name, 'size': enc.stat().st_size, 'sha256': sha256_file(enc),
            'source_sha256': digest, 'config_only': True, 'temporary_nodes_included': False,
            'role': (read_json('/etc/vvv/roles.json', {}) or {}).get('primary_role', ''), 'cloud_uploaded': False}
    atomic_json(meta_path, meta); atomic_json(BACKUPS / 'latest.json', meta)
    enforce_local_limits()
    try:
        if cloud_upload(enc, meta_path):
            meta['cloud_uploaded'] = True; meta['cloud_uploaded_at'] = now_iso()
            atomic_json(meta_path, meta); atomic_json(BACKUPS / 'latest.json', meta)
            enforce_local_limits(); upload_control_files()
    except Exception as exc:
        meta['cloud_error'] = str(exc); atomic_json(meta_path, meta); atomic_json(BACKUPS / 'latest.json', meta)
        print(f'警告：本地备份成功，但云上传失败：{exc}', file=os.sys.stderr)
    return meta


def list_backups():
    rows = local_entries()
    if not rows:
        print('暂无本地备份。'); return
    print('编号\t创建时间\t触发原因\t大小\t云端\t文件名')
    for index, (_, _, _, meta) in enumerate(rows, 1):
        print(f"{index}\t{meta.get('created_at','-')}\t{meta.get('reason','-')}\t{meta.get('size',0)}\t{'已上传' if meta.get('cloud_uploaded') else '未上传'}\t{meta.get('filename','-')}")
    print(f'总计：{len(rows)} 个，{sum(row[1].stat().st_size for row in rows)} 字节；上限 {MAX_COUNT} 个 / {MAX_BYTES} 字节。')


def cloud_test():
    if not cloud_enabled():
        raise SystemExit('云备份尚未开启。')
    if not local_entries():
        create_backup('cloud-test-initial', force=True)
    enc = local_entries()[0][1]
    test_remote = remote(f'{REMOTE_ROOT}/cloud-test.enc')
    rclone('copyto', str(enc), test_remote, '--retries', '5')
    with tempfile.TemporaryDirectory() as td:
        downloaded = Path(td) / 'cloud-test.enc'
        rclone('copyto', test_remote, str(downloaded), '--retries', '5')
        if sha256_file(downloaded) != sha256_file(enc):
            raise SystemExit('云端下载校验失败。')
    rclone('deletefile', test_remote, check=False)
    upload_control_files()
    print('云备份固定目录、上传、下载、RecoverKey.ini、索引和 SHA-256 校验通过。')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='cmd', required=True)
    create = sub.add_parser('create'); create.add_argument('reason'); create.add_argument('--force', action='store_true')
    sub.add_parser('list'); sub.add_parser('cloud-test'); sub.add_parser('refresh-control')
    args = parser.parse_args()
    if args.cmd == 'create': print(json.dumps(create_backup(args.reason, args.force), ensure_ascii=False))
    elif args.cmd == 'list': list_backups()
    elif args.cmd == 'cloud-test': cloud_test()
    else: enforce_local_limits(); enforce_cloud_limits(); upload_control_files()

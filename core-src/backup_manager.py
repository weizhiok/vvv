#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
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

SOURCES = [
    Path('/etc/vvv-sub/config.json'),
    Path('/var/lib/vvv-sub/registry.json'),
    Path('/var/lib/vvv-sub/hosts'),
    Path('/etc/jp-relay/state.json'),
    Path('/etc/jp-relay/landing-state.json'),
    Path('/etc/vvv/client.json'),
    Path('/etc/vvv/roles.json'),
    Path('/root/VVV-订阅中心恢复信息.txt'),
    Path('/etc/vvv-sub/cloud.json'),
    Path('/etc/vvv-sub/rclone.conf'),
]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def safe_reason(reason):
    out = ''.join(c if c.isalnum() or c in '-_' else '-' for c in reason.strip())
    return out.strip('-')[:64] or 'change'


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default


def atomic_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def add_source(tar, path):
    if not path.exists():
        return
    tar.add(path, arcname=str(path).lstrip('/'), recursive=True)


def snapshot_digest():
    h = hashlib.sha256()
    for path in SOURCES:
        if path.is_file():
            h.update(str(path).encode() + b'\0' + path.read_bytes())
        elif path.is_dir():
            for item in sorted(x for x in path.rglob('*') if x.is_file()):
                h.update(str(item).encode() + b'\0' + item.read_bytes())
    return h.hexdigest()


def cleanup():
    entries = []
    for meta_path in BACKUPS.glob('*.json'):
        if meta_path.name == 'latest.json':
            continue
        meta = read_json(meta_path, {}) or {}
        enc = meta_path.with_suffix('.enc')
        if enc.exists():
            entries.append((float(meta.get('created_ts') or enc.stat().st_mtime), enc, meta_path))
    entries.sort(reverse=True)
    cutoff = time.time() - 30 * 86400
    keep = {enc for index, (ts, enc, _) in enumerate(entries) if index < 20 or ts >= cutoff}
    for _, enc, meta in entries:
        if enc not in keep:
            enc.unlink(missing_ok=True)
            meta.unlink(missing_ok=True)


def cloud_upload(enc, meta):
    cfg = read_json(CLOUD_CFG, {}) or {}
    if not cfg.get('enabled'):
        return False
    if not shutil.which('rclone') or not RCLONE_CFG.exists():
        raise RuntimeError('云备份已开启，但 rclone 或配置文件不存在')
    remote = cfg.get('remote', 'vvvcloud')
    folder = cfg.get('folder') or ('VVV-Backup/' + os.uname().nodename)
    target = f'{remote}:{folder}'
    subprocess.run(['rclone', '--config', str(RCLONE_CFG), 'copyto', str(enc), f'{target}/{enc.name}', '--retries', '5'], check=True)
    subprocess.run(['rclone', '--config', str(RCLONE_CFG), 'copyto', str(meta), f'{target}/{meta.name}', '--retries', '5'], check=True)
    return True


def create_backup(reason, force=False):
    cfg = read_json(CFG)
    if not cfg:
        raise SystemExit('订阅中心尚未安装。')
    BACKUPS.mkdir(parents=True, exist_ok=True)
    os.chmod(BACKUPS, 0o700)
    digest = snapshot_digest()
    latest_meta = read_json(BACKUPS / 'latest.json', {}) or {}
    if not force and latest_meta.get('source_sha256') == digest:
        return {'skipped': True, 'reason': 'unchanged', 'path': latest_meta.get('file')}
    stamp = time.strftime('%Y%m%d-%H%M%S')
    base = f'{stamp}-{safe_reason(reason)}'
    enc = BACKUPS / f'{base}.enc'
    meta_path = BACKUPS / f'{base}.json'
    with tempfile.TemporaryDirectory() as td:
        tar_path = Path(td) / 'vvv-backup.tar.gz'
        with tarfile.open(tar_path, 'w:gz') as tar:
            for src in SOURCES:
                add_source(tar, src)
            manifest = Path(td) / 'manifest.json'
            manifest.write_text(json.dumps({'created_at': now_iso(), 'reason': reason, 'source_sha256': digest}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            tar.add(manifest, arcname='manifest.json')
        tmp_enc = BACKUPS / f'.{base}.tmp'
        subprocess.run([
            'openssl', 'enc', '-aes-256-cbc', '-salt', '-pbkdf2',
            '-pass', f"pass:{cfg['recovery_password']}",
            '-in', str(tar_path), '-out', str(tmp_enc),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.chmod(tmp_enc, 0o600)
        os.replace(tmp_enc, enc)
    meta = {
        'schema': 1,
        'created_at': now_iso(),
        'created_ts': time.time(),
        'reason': reason,
        'file': str(enc),
        'size': enc.stat().st_size,
        'sha256': hashlib.sha256(enc.read_bytes()).hexdigest(),
        'source_sha256': digest,
        'cloud_uploaded': False,
    }
    atomic_json(meta_path, meta)
    shutil.copy2(enc, BACKUPS / 'latest.enc')
    atomic_json(BACKUPS / 'latest.json', meta)
    try:
        if cloud_upload(enc, meta_path):
            meta['cloud_uploaded'] = True
            meta['cloud_uploaded_at'] = now_iso()
            atomic_json(meta_path, meta)
            atomic_json(BACKUPS / 'latest.json', meta)
    except Exception as exc:
        meta['cloud_error'] = str(exc)
        atomic_json(meta_path, meta)
        atomic_json(BACKUPS / 'latest.json', meta)
        print(f'警告：本地备份成功，但云上传失败：{exc}', file=os.sys.stderr)
    cleanup()
    return meta


def list_backups():
    rows = []
    for meta_path in sorted(BACKUPS.glob('*.json'), reverse=True):
        if meta_path.name == 'latest.json':
            continue
        rows.append(read_json(meta_path, {}) or {})
    if not rows:
        print('暂无本地备份。')
        return
    print('创建时间\t触发原因\t大小\t云端\tSHA-256')
    for meta in rows:
        print(f"{meta.get('created_at','-')}\t{meta.get('reason','-')}\t{meta.get('size',0)}\t{'已上传' if meta.get('cloud_uploaded') else '未上传'}\t{meta.get('sha256','-')}")


def cloud_test():
    cfg = read_json(CLOUD_CFG, {}) or {}
    if not cfg.get('enabled'):
        raise SystemExit('云备份尚未开启。')
    latest = BACKUPS / 'latest.enc'
    if not latest.exists():
        create_backup('cloud-test-initial', force=True)
    remote = cfg.get('remote', 'vvvcloud')
    folder = cfg.get('folder') or ('VVV-Backup/' + os.uname().nodename)
    target = f'{remote}:{folder}'
    subprocess.run(['rclone', '--config', str(RCLONE_CFG), 'copyto', str(latest), f'{target}/cloud-test.enc', '--retries', '5'], check=True)
    with tempfile.TemporaryDirectory() as td:
        downloaded = Path(td) / 'cloud-test.enc'
        subprocess.run(['rclone', '--config', str(RCLONE_CFG), 'copyto', f'{target}/cloud-test.enc', str(downloaded), '--retries', '5'], check=True)
        if hashlib.sha256(downloaded.read_bytes()).hexdigest() != hashlib.sha256(latest.read_bytes()).hexdigest():
            raise SystemExit('云端下载校验失败。')
    subprocess.run(['rclone', '--config', str(RCLONE_CFG), 'deletefile', f'{target}/cloud-test.enc'], check=False)
    print('云备份上传、下载和 SHA-256 校验通过。')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='cmd', required=True)
    create = sub.add_parser('create')
    create.add_argument('reason')
    create.add_argument('--force', action='store_true')
    sub.add_parser('list')
    sub.add_parser('cloud-test')
    args = parser.parse_args()
    if args.cmd == 'create':
        print(json.dumps(create_backup(args.reason, args.force), ensure_ascii=False))
    elif args.cmd == 'list':
        list_backups()
    else:
        cloud_test()

#!/usr/bin/env python3
import configparser
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.request import Request, urlopen

RCLONE_CFG = Path('/etc/vvv-sub/rclone.conf')
REMOTE = 'vvvcloud'
ROOT = 'vvv'
RECOVER = f'{ROOT}/RecoverKey.ini'
INDEX = f'{ROOT}/BackupIndex.json'
BACKUPS = f'{ROOT}/backups'
LOG = Path('/root') / f"VVV-恢复日志-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
BACKUP_RE = re.compile(r'^VVV_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_[A-Za-z0-9_-]{1,64}_[A-F0-9]{8}\.enc$')
ALLOWED_EXACT = {
    'manifest.json',
    'etc/vvv-sub/config.json', 'etc/vvv-sub/cloud.json', 'etc/vvv-sub/cloudflared.token',
    'var/lib/vvv-sub/registry.json', 'var/lib/vvv-sub/node-overrides.json',
    'etc/jp-relay/state.json', 'etc/jp-relay/landing-state.json',
    'etc/vvv/client.json', 'etc/vvv/roles.json',
}
ALLOWED_PREFIX = ('var/lib/vvv-sub/hosts/', 'etc/sing-box/tls/')


def log(message):
    text = str(message)
    print(text)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open('a', encoding='utf-8') as handle:
        handle.write(text + '\n')


def run(command, **kwargs):
    log('执行：' + ' '.join(map(str, command[:4])) + (' …' if len(command) > 4 else ''))
    return subprocess.run(command, check=True, **kwargs)


def install_rclone():
    if shutil.which('rclone'):
        return
    run(['apt-get', '-o', 'DPkg::Lock::Timeout=10', '-o', 'Acquire::Retries=2', 'update'], stdout=subprocess.DEVNULL)
    run(['apt-get', '-o', 'DPkg::Lock::Timeout=10', '-o', 'Acquire::Retries=2', 'install', '-y', 'curl', 'unzip'], stdout=subprocess.DEVNULL)
    subprocess.run('curl -fsSL --retry 5 --retry-all-errors https://rclone.org/install.sh | bash', shell=True, check=True)


def configure_cloud():
    install_rclone()
    print('1. Google Drive【默认】\n2. Microsoft OneDrive')
    choice = input('请选择云盘：').strip() or '1'
    provider = {'1': 'drive', '2': 'onedrive'}.get(choice)
    if not provider:
        raise SystemExit('请输入 1 或 2。')
    RCLONE_CFG.parent.mkdir(parents=True, exist_ok=True)
    RCLONE_CFG.unlink(missing_ok=True)
    print('\n接下来只需完成云盘 OAuth 授权。')
    print(f'请在 rclone 配置中创建名称为 {REMOTE} 的 remote，类型选择 {provider}。')
    env = os.environ.copy(); env['RCLONE_CONFIG'] = str(RCLONE_CFG)
    subprocess.run(['rclone', 'config'], check=True, env=env)
    remotes = subprocess.check_output(['rclone', '--config', str(RCLONE_CFG), 'listremotes'], text=True)
    if f'{REMOTE}:' not in remotes.splitlines():
        raise SystemExit(f'没有检测到名为 {REMOTE} 的云盘授权。')
    os.chmod(RCLONE_CFG, 0o600)
    return provider


def rclone(*args, capture=False, check=True):
    return subprocess.run(['rclone', '--config', str(RCLONE_CFG), *args], check=check, text=True, capture_output=capture)


def remote(path):
    return f'{REMOTE}:{path}'


def download(remote_path, local_path):
    rclone('copyto', remote(remote_path), str(local_path), '--retries', '5')


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def load_recover_key(work):
    target = work / 'RecoverKey.ini'
    download(RECOVER, target)
    parser = configparser.ConfigParser(); parser.read(target, encoding='utf-8')
    if parser.get('VVV', 'format', fallback='') != 'VVV_RECOVERY_1':
        raise SystemExit('RecoverKey.ini 格式不正确。')
    password = parser.get('VVV', 'recovery_password', fallback='')
    if not password:
        raise SystemExit('RecoverKey.ini 缺少恢复密码。')
    return password


def load_index(work):
    target = work / 'BackupIndex.json'
    try:
        download(INDEX, target)
        obj = json.loads(target.read_text(encoding='utf-8'))
        rows = [row for row in obj.get('backups', []) if BACKUP_RE.match(str(row.get('filename', '')))]
        if rows:
            return sorted(rows, key=lambda x: float(x.get('created_ts') or 0), reverse=True)[:100]
    except Exception as exc:
        log(f'云端索引不可用，改为扫描固定备份目录：{exc}')
    listing = rclone('lsjson', remote(BACKUPS), '--files-only', capture=True)
    files = json.loads(listing.stdout or '[]')
    rows = []
    for item in files:
        name = str(item.get('Name', ''))
        if not BACKUP_RE.match(name):
            continue
        meta_local = work / (Path(name).stem + '.json')
        try:
            download(f'{BACKUPS}/{meta_local.name}', meta_local)
            meta = json.loads(meta_local.read_text(encoding='utf-8'))
        except Exception:
            meta = {'filename': name, 'size': item.get('Size', 0), 'created_at': item.get('ModTime', ''), 'reason': '未知'}
        rows.append(meta)
    return sorted(rows, key=lambda x: str(x.get('created_at', '')), reverse=True)[:100]


def choose_backup(rows):
    if not rows:
        raise SystemExit('云盘 vvv/backups/ 中没有可恢复备份。')
    print('\n========== 可恢复的云备份 ==========')
    for index, row in enumerate(rows, 1):
        created = str(row.get('created_at', '-')).replace('T', ' ')[:19]
        print(f"{index}. {created}  {row.get('reason','-')}  {row.get('size',0)} 字节")
    while True:
        value = input('请输入编号 [默认 1，恢复最新备份]：').strip()
        if not value:
            return 0, True
        if value.isdigit() and 1 <= int(value) <= len(rows):
            return int(value) - 1, False
        print('请输入列表中的有效编号。')


def download_verified(rows, selected, default_choice, work):
    candidates = range(selected, len(rows)) if default_choice else [selected]
    errors = []
    for index in candidates:
        meta = rows[index]
        name = str(meta.get('filename', ''))
        if not BACKUP_RE.match(name):
            continue
        target = work / name
        try:
            log(f'下载备份：{name}')
            download(f'{BACKUPS}/{name}', target)
            expected = str(meta.get('sha256') or '')
            if expected and sha256(target) != expected:
                raise RuntimeError('SHA-256 不一致')
            return target, meta
        except Exception as exc:
            errors.append(f'{name}: {exc}')
            if not default_choice:
                break
            log(f'该备份不可用，自动尝试上一份：{exc}')
    raise SystemExit('所选备份无法使用：' + '；'.join(errors[-3:]))


def decrypt_and_validate(enc, password, work):
    tar_path = work / 'restore.tar.gz'
    env = os.environ.copy(); env['VVV_BACKUP_PASSWORD'] = password
    run(['openssl', 'enc', '-d', '-aes-256-cbc', '-pbkdf2', '-pass', 'env:VVV_BACKUP_PASSWORD', '-in', str(enc), '-out', str(tar_path)], env=env)
    extract = work / 'extract'; extract.mkdir()
    with tarfile.open(tar_path, 'r:gz') as archive:
        members = archive.getmembers()
        for member in members:
            name = str(PurePosixPath(member.name))
            if member.issym() or member.islnk() or member.isdev():
                raise SystemExit(f'备份包含不允许的特殊文件：{name}')
            if name.startswith('/') or '..' in PurePosixPath(name).parts:
                raise SystemExit(f'备份包含不安全路径：{name}')
            if name not in ALLOWED_EXACT and not name.startswith(ALLOWED_PREFIX):
                raise SystemExit(f'备份包含非配置文件，拒绝恢复：{name}')
        archive.extractall(extract, filter='data')
    manifest = json.loads((extract / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema') != 2 or manifest.get('config_only') is not True:
        raise SystemExit('该备份不是新版纯配置备份。')
    return extract


def detect_public_ipv4():
    for url in ('https://api.ipify.org', 'https://ipv4.icanhazip.com', 'https://ifconfig.co/ip'):
        try:
            request = Request(url, headers={'User-Agent': 'VVV-Recovery/1.0'})
            value = urlopen(request, timeout=12).read().decode().strip()
            parts = value.split('.')
            if len(parts) == 4 and all(part.isdigit() and 0 <= int(part) <= 255 for part in parts):
                return value
        except Exception:
            pass
    raise SystemExit('无法检测当前 VPS 公网 IPv4。')


def copy_tree(extract):
    residual = Path('/root') / f"VVV-恢复前残留-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    residual.mkdir(mode=0o700)
    for root in ('/etc/vvv-sub', '/var/lib/vvv-sub', '/etc/jp-relay', '/etc/vvv'):
        source = Path(root)
        if source.exists():
            shutil.copytree(source, residual / source.name, dirs_exist_ok=True)
    for relative in ALLOWED_EXACT | set(ALLOWED_PREFIX):
        if relative == 'manifest.json' or relative.endswith('/'):
            continue
        source = extract / relative
        if source.exists():
            target = Path('/') / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    for prefix in ALLOWED_PREFIX:
        source = extract / prefix
        if source.exists():
            target = Path('/') / prefix
            shutil.copytree(source, target, dirs_exist_ok=True)
    return residual


def patch_restored_state(password, provider):
    current_ip = detect_public_ipv4()
    main = Path('/etc/jp-relay/state.json')
    old_ip = ''
    if main.exists():
        obj = json.loads(main.read_text(encoding='utf-8'))
        old_ip = str(obj.get('public_ip') or '')
        obj['public_ip'] = current_ip
        obj['temporary_nodes'] = []
        obj.pop('temporary_relays', None)
        if old_ip and old_ip in str(obj.get('direct_base_name', '')):
            obj['direct_base_name'] = str(obj['direct_base_name']).replace(old_ip, current_ip)
        obj['restored_at'] = datetime.now(timezone.utc).isoformat()
        main.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'); os.chmod(main, 0o600)
    center = Path('/etc/vvv-sub/config.json')
    if center.exists():
        obj = json.loads(center.read_text(encoding='utf-8'))
        obj.update(schema=4, public_ip=current_ip, api_base_url=f'http://{current_ip}:18081', listen_host='0.0.0.0', listen_port=18081, recovery_password=password)
        center.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'); os.chmod(center, 0o600)
        cloud = Path('/etc/vvv-sub/cloud.json')
        cloud.write_text(json.dumps({'schema': 2, 'enabled': True, 'provider': provider, 'remote': REMOTE, 'folder': ROOT, 'restored_at': datetime.now(timezone.utc).isoformat()}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.chmod(cloud, 0o600)
    client = Path('/etc/vvv/client.json')
    if client.exists():
        try:
            obj = json.loads(client.read_text(encoding='utf-8'))
            base = str(obj.get('api_base_url') or obj.get('base_url') or '')
            if old_ip and old_ip in base:
                obj['api_base_url'] = base.replace(old_ip, current_ip)
                obj['base_url'] = obj['api_base_url']
                obj['center_ip'] = current_ip
            client.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        except Exception:
            client.unlink(missing_ok=True)
    return old_ip, current_ip


def restore():
    LOG.write_text('VVV 云恢复日志\n', encoding='utf-8'); os.chmod(LOG, 0o600)
    provider = configure_cloud()
    with tempfile.TemporaryDirectory(prefix='vvv-restore.') as td:
        work = Path(td)
        password = load_recover_key(work)
        rows = load_index(work)
        selected, default_choice = choose_backup(rows)
        enc, meta = download_verified(rows, selected, default_choice, work)
        extract = decrypt_and_validate(enc, password, work)
        residual = copy_tree(extract)
        # 新 OAuth 授权必须保留，不能被备份覆盖。
        RCLONE_CFG.parent.mkdir(parents=True, exist_ok=True)
        old_ip, current_ip = patch_restored_state(password, provider)
    result = {'backup': meta.get('filename'), 'created_at': meta.get('created_at'), 'reason': meta.get('reason'),
              'old_ip': old_ip, 'current_ip': current_ip, 'residual': str(residual), 'log': str(LOG)}
    state = Path('/run/vvv-restore-result.json'); state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'); os.chmod(state, 0o600)
    log('配置文件恢复完成；即将使用当前最新版程序重建全部服务。')
    log(f'原公网 IP：{old_ip or "未知"}；当前公网 IP：{current_ip}')
    log(f'恢复日志：{LOG}')


if __name__ == '__main__':
    restore()

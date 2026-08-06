#!/usr/bin/env python3
"""Fixed local engine for isolated VVV client-support upgrades.

Only the client adapter and generated client-facing files may change. Proxy
binaries, configurations, state, credentials, systemd units and proxy process
identity are hashed before and after every update. Any unexpected change aborts
and restores the previous client adapter/output set.
"""

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

DEFAULT_UPGRADE_URL = 'https://raw.githubusercontent.com/weizhiok/vvv/client-support/client_upgrade.py'
MAX_DOWNLOAD = 512 * 1024
TARGET = '/usr/local/lib/vvv/client_adapters.py'
RENDERER = '/usr/local/lib/vvv/client_local_renderer.py'
SUB_CENTER = '/usr/local/lib/vvv/sub_center.py'
STATUS = '/var/lib/vvv/client-support/status.json'

PROTECTED_FILES = [
    '/usr/local/bin/xray',
    '/usr/local/bin/sing-box',
    '/usr/local/etc/xray/config.json',
    '/etc/sing-box/config.json',
    '/etc/jp-relay/state.json',
    '/etc/jp-relay/landing-state.json',
    '/etc/vvv-sub/config.json',
    '/etc/vvv/client.json',
    '/etc/systemd/system/xray.service',
    '/etc/systemd/system/sing-box.service',
    '/etc/vvv-landing/xray/config.json',
    '/etc/vvv-landing/sing-box/config.json',
    '/etc/vvv-landing/sing-box/tls/landing-hy2.crt',
    '/etc/vvv-landing/sing-box/tls/landing-hy2.key',
    '/etc/systemd/system/vvv-landing-xray.service',
    '/etc/systemd/system/vvv-landing-sing-box.service',
]
ALLOWED_IMPORTS = {'base64', 'json', 're', 'urllib.parse', 'zlib'}
FORBIDDEN_CALLS = {
    'open', 'exec', 'eval', 'compile', '__import__', 'input', 'breakpoint',
    'getattr', 'setattr', 'delattr', 'globals', 'locals', 'vars',
}
FORBIDDEN_NAMES = {'__builtins__', '__loader__', '__spec__', '__package__'}


def rooted(root, absolute):
    root = Path(root)
    path = Path(absolute)
    return path if root == Path('/') else root / str(path).lstrip('/')


def sha256(path):
    path = Path(path)
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(obj, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    if not spec.loader:
        raise RuntimeError(f'无法加载 {path}。')
    spec.loader.exec_module(module)
    return module


def module_contract(path):
    module = load_module(path, 'vvv_client_support_candidate')
    for name in ('detect_client', 'render', 'available_formats', 'local_outputs', 'smoke_test'):
        if not callable(getattr(module, name, None)):
            raise RuntimeError(f'候选客户端支持缺少 {name}。')
    module.smoke_test()
    version = getattr(module, 'VERSION', None)
    if not isinstance(version, int) or version < 1:
        raise RuntimeError('候选客户端支持 VERSION 必须是正整数。')
    formats = list(module.available_formats())
    outputs = list(module.local_outputs())
    if not formats or not outputs:
        raise RuntimeError('候选客户端支持格式或本机输出清单为空。')
    return module, version, formats, outputs


def validate_restricted_source(path):
    source = Path(path).read_text(encoding='utf-8')
    tree = ast.parse(source, filename=str(path))
    for index, node in enumerate(tree.body):
        if isinstance(node, ast.Expr) and index == 0 and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.FunctionDef)):
            continue
        if isinstance(node, ast.If):
            test = node.test
            if not (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and
                    test.left.id == '__name__'):
                raise RuntimeError('客户端支持文件只允许 __main__ 顶层条件。')
            continue
        raise RuntimeError(f'客户端支持文件包含不允许的顶层语句：{type(node).__name__}')
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in ALLOWED_IMPORTS:
                    raise RuntimeError(f'客户端支持文件禁止导入：{alias.name}')
        elif isinstance(node, ast.ImportFrom):
            if (node.module or '') not in ALLOWED_IMPORTS:
                raise RuntimeError(f'客户端支持文件禁止导入：{node.module}')
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            raise RuntimeError(f'客户端支持文件禁止访问：{node.id}')
        elif isinstance(node, ast.Attribute) and node.attr.startswith('__'):
            raise RuntimeError(f'客户端支持文件禁止访问双下划线属性：{node.attr}')
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
            raise RuntimeError(f'客户端支持文件禁止调用：{node.func.id}')
    py_compile.compile(str(path), doraise=True)


def current_version(target):
    if not Path(target).is_file():
        return 0
    try:
        _module, version, _formats, _outputs = module_contract(target)
        return version
    except Exception:
        return 0


def process_identity(service):
    try:
        pid = subprocess.check_output(
            ['systemctl', 'show', service, '--property=MainPID', '--value'],
            text=True, timeout=10,
        ).strip()
        if not pid.isdigit() or int(pid) <= 0:
            return None
        stat = Path('/proc') / pid / 'stat'
        fields = stat.read_text(encoding='utf-8').split()
        return {'pid': int(pid), 'start_time': fields[21]}
    except Exception:
        return None


def protected_snapshot(root='/'):
    snapshot = {
        'files': {name: sha256(rooted(root, name)) for name in PROTECTED_FILES},
        'processes': {},
        'kernel': os.uname().release,
    }
    if Path(root) == Path('/'):
        snapshot['processes'] = {
            'xray.service': process_identity('xray.service'),
            'sing-box.service': process_identity('sing-box.service'),
            'vvv-landing-xray.service': process_identity('vvv-landing-xray.service'),
            'vvv-landing-sing-box.service': process_identity('vvv-landing-sing-box.service'),
        }
    return snapshot


def compare_protected(before, after):
    changes = []
    for name, old in before['files'].items():
        new = after['files'].get(name)
        if old != new:
            changes.append(f'受保护文件发生变化：{name}')
    if before.get('kernel') != after.get('kernel'):
        changes.append('内核版本发生变化')
    for service, old in before.get('processes', {}).items():
        new = after.get('processes', {}).get(service)
        if old != new:
            changes.append(f'代理进程发生变化：{service}（{old} → {new}）')
    return changes


def add_nonce(url):
    parts = urlsplit(url)
    query = parts.query + ('&' if parts.query else '') + f'vvv={int(time.time())}'
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def download(url, destination):
    parts = urlsplit(url)
    if parts.scheme.lower() != 'https' or not parts.netloc:
        raise RuntimeError('升级地址必须是完整 HTTPS URL。')
    if len(url) > 2048:
        raise RuntimeError('升级地址过长。')
    request = Request(add_nonce(url), headers={'User-Agent': 'VVV-Client-Support-Upgrader/2.0'})
    with urlopen(request, timeout=90) as response:
        length = response.headers.get('Content-Length')
        if length and int(length) > MAX_DOWNLOAD:
            raise RuntimeError('下载的客户端支持文件超过 512 KiB。')
        data = response.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise RuntimeError('下载的客户端支持文件超过 512 KiB。')
    if len(data) < 1000:
        raise RuntimeError('下载的客户端支持文件异常过小。')
    Path(destination).write_bytes(data)


def install_atomic(source, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_suffix(target.suffix + '.new')
    shutil.copy2(source, staged)
    os.chmod(staged, 0o755)
    os.replace(staged, target)


def load_renderer(root='/'):
    path = rooted(root, RENDERER)
    if not path.is_file():
        raise RuntimeError('本地客户端配置生成器不存在。')
    return load_module(path, 'vvv_client_local_renderer')


def backup_paths(paths, directory):
    directory = Path(directory)
    records = []
    for index, path in enumerate(sorted(set(map(Path, paths)), key=str)):
        record = {'path': str(path), 'exists': path.is_file()}
        if path.is_file():
            backup = directory / f'file-{index}'
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
            record['backup'] = str(backup)
        records.append(record)
    return records


def restore_paths(records):
    for record in records:
        path = Path(record['path'])
        if record['exists']:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(record['backup'], path)
            os.chmod(path, 0o600)
        else:
            path.unlink(missing_ok=True)


def center_present(root='/'):
    return rooted(root, '/etc/vvv-sub/config.json').is_file() and rooted(root, SUB_CENTER).is_file()


def center_output_path(root='/'):
    return rooted(root, '/var/lib/vvv-sub/output')


def backup_center_output(root, directory):
    source = center_output_path(root)
    destination = Path(directory) / 'center-output'
    if source.is_dir():
        shutil.copytree(source, destination)
        return {'exists': True, 'backup': str(destination)}
    return {'exists': False, 'backup': ''}


def restore_center_output(root, record):
    target = center_output_path(root)
    if target.exists():
        shutil.rmtree(target)
    if record['exists']:
        shutil.copytree(record['backup'], target)


def restart_center(root='/'):
    if Path(root) != Path('/'):
        return
    subprocess.run(['systemctl', 'restart', 'vvv-sub.service'], check=True, timeout=75)
    subprocess.run(['systemctl', 'is-active', '--quiet', 'vvv-sub.service'], check=True, timeout=20)


def regenerate_center(root='/'):
    if not center_present(root):
        return
    subprocess.run(['python3', str(rooted(root, SUB_CENTER)), 'regenerate'], check=True, timeout=90)


def status_payload(root='/', source_url=DEFAULT_UPGRADE_URL):
    target = rooted(root, TARGET)
    version = current_version(target)
    role = 'unknown'
    renderer = rooted(root, RENDERER)
    if renderer.is_file():
        try:
            role = load_renderer(root).detect_contexts(root)[0]
        except Exception:
            role = 'unknown'
    if center_present(root):
        role = f'{role}+center' if role not in ('center-only', 'unknown') else 'center-only'
    return {
        'version': version,
        'role': role,
        'default_upgrade_url': source_url,
        'adapter_sha256': sha256(target),
    }


def apply_candidate(candidate, source_url=DEFAULT_UPGRADE_URL, root='/', allow_downgrade=False):
    root = str(Path(root))
    target = rooted(root, TARGET)
    renderer = load_renderer(root)
    validate_restricted_source(candidate)
    _candidate_module, new_version, formats, new_outputs = module_contract(candidate)
    old_version = current_version(target)
    if new_version < old_version and not allow_downgrade:
        raise RuntimeError(f'拒绝降级客户端支持：v{old_version} → v{new_version}')

    old_names = []
    if target.is_file():
        try:
            old_module, _v, _f, old_outputs = module_contract(target)
            old_names = [row['filename'] for row in old_outputs]
        except Exception:
            old_names = []
    new_names = [row['filename'] for row in new_outputs]
    managed = renderer.managed_paths(root, candidate, set(old_names) | set(new_names))
    before = protected_snapshot(root)

    with tempfile.TemporaryDirectory(prefix='vvv-client-upgrade.') as work:
        work = Path(work)
        adapter_backup = work / 'client_adapters.py.previous'
        adapter_existed = target.is_file()
        if adapter_existed:
            shutil.copy2(target, adapter_backup)
        output_records = backup_paths(managed, work / 'local-output-backup')
        center_record = backup_center_output(root, work)
        changed = sha256(candidate) != sha256(target)
        try:
            install_atomic(candidate, target)
            obsolete = sorted(set(old_names) - set(new_names))
            renderer.regenerate_all(root, target, obsolete)
            regenerate_center(root)
            if changed and center_present(root):
                restart_center(root)
            after = protected_snapshot(root)
            unexpected = compare_protected(before, after)
            if unexpected:
                raise RuntimeError('\n'.join(unexpected))
        except Exception:
            if adapter_existed:
                install_atomic(adapter_backup, target)
            else:
                target.unlink(missing_ok=True)
            restore_paths(output_records)
            restore_center_output(root, center_record)
            if center_present(root):
                try:
                    restart_center(root)
                except Exception:
                    pass
            raise

    payload = status_payload(root, source_url)
    payload.update({
        'previous_version': old_version,
        'updated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'formats': formats,
        'protected_proxy_files_unchanged': True,
        'proxy_processes_unchanged': True,
        'source_url': source_url,
    })
    atomic_json(rooted(root, STATUS), payload)
    return payload


def upgrade(url=DEFAULT_UPGRADE_URL, root='/', allow_downgrade=False):
    with tempfile.TemporaryDirectory(prefix='vvv-client-download.') as work:
        candidate = Path(work) / 'client_upgrade.py'
        download(url, candidate)
        return apply_candidate(candidate, url, root, allow_downgrade)


def print_result(payload):
    previous = payload.get('previous_version', payload.get('version', 0))
    current = payload.get('version', 0)
    print()
    print(f'客户端支持升级成功：v{previous} → v{current}')
    print('受保护的代理配置：未改动')
    print('Xray 进程：未重启')
    print('sing-box 进程：未重启')
    print('节点状态文件：未改动')
    print('系统软件包、内核和系统设置：未改动')
    print(f"支持格式：{', '.join(payload.get('formats', []))}")


def show_after(root='/'):
    renderer = load_renderer(root)
    print()
    print('========== 当前客户端配置 ==========')
    renderer.show_all(root)
    cfg = rooted(root, '/etc/vvv-sub/config.json')
    if cfg.is_file():
        data = json.loads(cfg.read_text(encoding='utf-8'))
        print()
        print(f"统一订阅地址：{data.get('subscription_url', '')}")


def menu(root='/'):
    current = status_payload(root)
    print()
    print('========== 升级客户端支持 ==========')
    print(f"当前服务器角色：{current['role']}")
    print(f"当前客户端支持版本：v{current['version']}")
    print('默认升级地址：')
    print(DEFAULT_UPGRADE_URL)
    value = input('请输入完整升级地址 [按回车使用默认地址]：').strip()
    url = value or DEFAULT_UPGRADE_URL
    payload = upgrade(url, root)
    print_result(payload)
    show_after(root)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['menu', 'upgrade', 'status', 'default-url', 'show'])
    parser.add_argument('url', nargs='?')
    parser.add_argument('--root', default=os.environ.get('VVV_TEST_ROOT', '/'))
    parser.add_argument('--allow-downgrade', action='store_true')
    args = parser.parse_args()
    if args.command == 'menu':
        menu(args.root)
    elif args.command == 'upgrade':
        payload = upgrade(args.url or DEFAULT_UPGRADE_URL, args.root, args.allow_downgrade)
        print_result(payload)
        show_after(args.root)
    elif args.command == 'status':
        print(json.dumps(status_payload(args.root), ensure_ascii=False, indent=2))
    elif args.command == 'default-url':
        print(DEFAULT_UPGRADE_URL)
    else:
        show_after(args.root)


if __name__ == '__main__':
    main()

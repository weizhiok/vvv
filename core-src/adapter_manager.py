#!/usr/bin/env python3
import argparse
import hashlib
import importlib.util
import os
import py_compile
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen

TARGET = Path('/usr/local/lib/vvv/client_adapters.py')
RAW_URL = 'https://raw.githubusercontent.com/weizhiok/vvv/main/core-src/client_adapters.py'
SERVICE = 'vvv-sub.service'


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_and_smoke(path):
    spec = importlib.util.spec_from_file_location('vvv_client_adapters_candidate', path)
    module = importlib.util.module_from_spec(spec)
    if not spec.loader:
        raise RuntimeError('无法加载客户端适配器。')
    spec.loader.exec_module(module)
    if not callable(getattr(module, 'detect_client', None)):
        raise RuntimeError('客户端适配器缺少 detect_client。')
    if not callable(getattr(module, 'render', None)):
        raise RuntimeError('客户端适配器缺少 render。')
    if not callable(getattr(module, 'smoke_test', None)):
        raise RuntimeError('客户端适配器缺少 smoke_test。')
    module.smoke_test()
    return int(getattr(module, 'VERSION', 0)), list(module.available_formats())


def service_health():
    subprocess.run(['systemctl', 'restart', SERVICE], check=True, timeout=75)
    for _ in range(20):
        if subprocess.run(['systemctl', 'is-active', '--quiet', SERVICE]).returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError('订阅中心服务更新适配器后没有进入 active 状态。')


def update():
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='vvv-adapter-update.') as tmpdir:
        candidate = Path(tmpdir) / 'client_adapters.py'
        request = Request(f'{RAW_URL}?v={int(time.time())}', headers={'User-Agent': 'VVV-Adapter-Updater/1.0'})
        with urlopen(request, timeout=90) as response:
            data = response.read()
        if len(data) < 1000:
            raise SystemExit('下载的客户端适配器文件异常过小。')
        candidate.write_bytes(data)
        py_compile.compile(str(candidate), doraise=True)
        version, formats = load_and_smoke(candidate)
        old_digest = sha256(TARGET) if TARGET.exists() else ''
        new_digest = sha256(candidate)
        if old_digest == new_digest:
            print(f'客户端适配器已是最新版本：v{version}，格式：{", ".join(formats)}')
            return
        backup = TARGET.with_suffix('.py.previous')
        if TARGET.exists():
            shutil.copy2(TARGET, backup)
        staged = TARGET.with_suffix('.py.new')
        shutil.copy2(candidate, staged)
        os.chmod(staged, 0o755)
        os.replace(staged, TARGET)
        try:
            service_health()
        except Exception:
            if backup.exists():
                os.replace(backup, TARGET)
                subprocess.run(['systemctl', 'restart', SERVICE], check=False, timeout=75)
            raise
        backup.unlink(missing_ok=True)
        print(f'客户端适配器更新成功：v{version}，SHA-256={new_digest}')
        print(f'支持格式：{", ".join(formats)}')


def status():
    if not TARGET.exists():
        raise SystemExit('客户端适配器尚未安装。')
    version, formats = load_and_smoke(TARGET)
    print(f'客户端适配器版本：v{version}')
    print(f'SHA-256：{sha256(TARGET)}')
    print(f'支持格式：{", ".join(formats)}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['update', 'status'])
    args = parser.parse_args()
    if args.command == 'update':
        update()
    else:
        status()

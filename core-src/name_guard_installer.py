#!/usr/bin/env python3
"""Patch the staged subscription-center installer before role installation."""

import argparse
import os
import subprocess
import tempfile
from pathlib import Path

MARKER = '# VVV_GLOBAL_NAME_GUARD_INSTALL_V1'


def patched_text(text):
    if MARKER in text:
        return text
    anchor = 'ensure_service vvv-sub.service restart 60\n'
    if text.count(anchor) != 1:
        raise RuntimeError(f'订阅中心启动锚点预期 1 次，实际 {text.count(anchor)} 次。')
    injected = (
        MARKER + '\n'
        'python3 /usr/local/lib/vvv/name_guard_runtime.py '
        '--manager /usr/local/sbin/jp-relay-manager '
        '--sub-center /usr/local/lib/vvv/sub_center.py '
        '--no-restart-center || fail "安装全局节点名称保护失败。"\n'
        + anchor
    )
    return text.replace(anchor, injected, 1)


def patch_file(path):
    path = Path(path)
    original = path.read_text(encoding='utf-8')
    updated = patched_text(original)
    if updated == original:
        return False
    stat = path.stat()
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, stat.st_mode & 0o777 or 0o700)
        subprocess.run(['bash', '-n', temporary], check=True)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('center_installer')
    args = parser.parse_args()
    print('patched' if patch_file(args.center_installer) else 'already-patched')


if __name__ == '__main__':
    main()

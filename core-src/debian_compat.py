#!/usr/bin/env python3
"""Make staged VVV installer sources accept Debian 12 and Debian 13.

The transformer is intentionally strict: it patches only the known OS gates and
then verifies that the important system-management features remain present.
This keeps fresh-install behavior identical across Debian 12/13 while avoiding
silent edits to proxy, subscription, credential, or routing logic.
"""

import argparse
import os
import re
import subprocess
import tempfile
from pathlib import Path

MARKER = '# VVV_DEBIAN_12_13_COMPAT_V1'
SUPPORTED_VERSIONS = ('12', '13')


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}：预期匹配 1 次，实际 {count} 次。')
    return text.replace(old, new, 1)


def add_marker(text: str) -> str:
    if MARKER in text:
        return text
    lines = text.splitlines(True)
    if not lines or not lines[0].startswith('#!'):
        raise RuntimeError('脚本缺少 shebang，不能安全加入兼容标记。')
    return lines[0] + MARKER + '\n' + ''.join(lines[1:])


def patch_bootstrap(text: str) -> str:
    if MARKER in text:
        return text
    old = '''[[ "${ID:-}" == debian && "${VERSION_ID:-}" == 13 ]] || { echo "错误：VVV 仅支持 Debian 13。当前系统：${PRETTY_NAME:-未知}" >&2; exit 1; }
command -v systemctl >/dev/null 2>&1 || { echo "错误：Debian 13 缺少 systemd。" >&2; exit 1; }'''
    new = '''[[ "${ID:-}" == debian && "${VERSION_ID:-}" =~ ^(12|13)$ ]] || { echo "错误：VVV 仅支持 Debian 12/13。当前系统：${PRETTY_NAME:-未知}" >&2; exit 1; }
command -v systemctl >/dev/null 2>&1 || { echo "错误：当前 Debian 缺少 systemd。" >&2; exit 1; }'''
    text = replace_once(text, old, new, '安装菜单 Debian 版本门槛')
    return add_marker(text)


def patch_host(text: str) -> str:
    if MARKER in text:
        return text
    old = '''  [[ "${ID:-}" == "debian" && "${VERSION_ID:-}" == "13" ]] || fail "主机脚本仅支持 Debian 13。当前系统：${PRETTY_NAME:-未知}"'''
    new = '''  [[ "${ID:-}" == "debian" && "${VERSION_ID:-}" =~ ^(12|13)$ ]] || fail "主机脚本仅支持 Debian 12/13。当前系统：${PRETTY_NAME:-未知}"'''
    text = replace_once(text, old, new, '主机 Debian 版本门槛')
    return add_marker(text)


def patch_landing(text: str) -> str:
    if MARKER in text:
        return text
    old = '''  [ "${ID:-}" = "debian" ] && [ "${VERSION_ID:-}" = "13" ] || fail "落地脚本仅支持 Debian 13。当前系统：${PRETTY_NAME:-未知}"
  command -v apt-get >/dev/null 2>&1 || fail "当前 Debian 13 找不到 apt-get。"
  command -v systemctl >/dev/null 2>&1 || fail "当前 Debian 13 找不到 systemd。"'''
    new = '''  [ "${ID:-}" = "debian" ] || fail "落地脚本仅支持 Debian 12/13。当前系统：${PRETTY_NAME:-未知}"
  case "${VERSION_ID:-}" in
    12|13) ;;
    *) fail "落地脚本仅支持 Debian 12/13。当前系统：${PRETTY_NAME:-未知}" ;;
  esac
  command -v apt-get >/dev/null 2>&1 || fail "当前 Debian 找不到 apt-get。"
  command -v systemctl >/dev/null 2>&1 || fail "当前 Debian 找不到 systemd。"'''
    text = replace_once(text, old, new, '中转副机 Debian 版本门槛')
    text = replace_once(
        text,
        '  echo "Debian 13 核心组件保持 VPS 镜像原版本，仅安装代理所需依赖。"',
        '  echo "Debian 12/13 核心组件保持 VPS 镜像原版本，仅安装代理所需依赖。"',
        '中转副机系统提示',
    )
    return add_marker(text)


PATCHERS = {
    'bootstrap.sh': patch_bootstrap,
    'host.sh': patch_host,
    'landing.sh': patch_landing,
}


def validate_features(app_dir: Path) -> None:
    bootstrap = (app_dir / 'bootstrap.sh').read_text(encoding='utf-8')
    host = (app_dir / 'host.sh').read_text(encoding='utf-8')
    landing = (app_dir / 'landing.sh').read_text(encoding='utf-8')

    for name, text in [('bootstrap.sh', bootstrap), ('host.sh', host), ('landing.sh', landing)]:
        if MARKER not in text:
            raise RuntimeError(f'{name} 没有安装 Debian 12/13 兼容标记。')
        if '仅支持 Debian 13' in text or '只支持 Debian 13' in text:
            raise RuntimeError(f'{name} 仍残留 Debian 13 单版本限制。')

    required_bootstrap = (
        '1. 安装订阅中心 + 中转主机 + 自身代理',
        '2. 安装订阅中心 + 自身代理',
        '3. 安装中转主机 + 自身代理',
        '4. 安装中转副机 + 自身代理',
        '5. 安装中转副机',
        '6. 安装直连代理',
        '7. 从云备份恢复',
    )
    for token in required_bootstrap:
        if token not in bootstrap:
            raise RuntimeError('安装菜单缺少角色入口：' + token)

    host_features = (
        'net.ipv4.tcp_congestion_control = bbr',
        'net.core.default_qdisc = fq',
        '/usr/share/zoneinfo/Asia/Shanghai',
        "0 6 * * * root /usr/local/lib/vvv/daily-reboot.sh",
        'tzdata kmod util-linux',
        'nftables cron',
        'python3-venv',
    )
    for token in host_features:
        if token not in host:
            raise RuntimeError('主机 Debian 12/13 功能检查缺少：' + token)

    landing_features = (
        'net.ipv4.tcp_congestion_control = bbr',
        'net.core.default_qdisc = fq',
        '/usr/share/zoneinfo/Asia/Shanghai',
        'OnCalendar=*-*-* 06:00:00',
        'tzdata kmod util-linux python3',
        'daily-reboot.timer',
    )
    for token in landing_features:
        if token not in landing:
            raise RuntimeError('中转副机 Debian 12/13 功能检查缺少：' + token)

    if not re.search(r'VERSION_ID:-.*12\|13', bootstrap):
        raise RuntimeError('安装菜单没有同时接受 Debian 12/13。')
    if not re.search(r'VERSION_ID:-.*12\|13', host):
        raise RuntimeError('主机脚本没有同时接受 Debian 12/13。')
    if '12|13)' not in landing:
        raise RuntimeError('中转副机脚本没有同时接受 Debian 12/13。')


def atomic_write(path: Path, text: str) -> None:
    stat = path.stat()
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, stat.st_mode & 0o777 or 0o700)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def patch_tree(app_dir: Path, syntax_check: bool = True) -> None:
    app_dir = Path(app_dir)
    for filename, patcher in PATCHERS.items():
        path = app_dir / filename
        if not path.is_file():
            raise RuntimeError(f'缺少安装源码：{path}')
        original = path.read_text(encoding='utf-8')
        updated = patcher(original)
        if updated != original:
            atomic_write(path, updated)

    validate_features(app_dir)

    if syntax_check:
        subprocess.run(['bash', '-n', str(app_dir / 'bootstrap.sh')], check=True)
        subprocess.run(['bash', '-n', str(app_dir / 'host.sh')], check=True)
        subprocess.run(['sh', '-n', str(app_dir / 'landing.sh')], check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('app_dir')
    parser.add_argument('--no-syntax-check', action='store_true')
    args = parser.parse_args()
    patch_tree(Path(args.app_dir), syntax_check=not args.no_syntax_check)
    print('Debian 12/13 compatibility: OK')


if __name__ == '__main__':
    main()

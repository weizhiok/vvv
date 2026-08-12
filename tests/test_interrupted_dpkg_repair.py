#!/usr/bin/env python3
import os
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    ROOT / 'vvv-install.sh',
    ROOT / 'core-src' / 'host.sh',
    ROOT / 'core-src' / 'landing.sh',
    ROOT / 'core-src' / 'center_install.sh',
)


def read(path):
    return path.read_text(encoding='utf-8')


def extract_installer_function(text):
    match = re.search(r'(repair_dpkg_state\(\) \{\n.*?\n\})\n\nrepair_dpkg_state\n', text, re.S)
    if not match:
        raise AssertionError('cannot extract installer repair_dpkg_state function')
    return match.group(1)


def make_fake_tools(root: Path, mode: str):
    bindir = root / 'bin'
    bindir.mkdir()
    admin = root / 'var' / 'lib' / 'dpkg'
    updates = admin / 'updates'
    updates.mkdir(parents=True)
    (admin / 'status').write_text('Package: dpkg\nStatus: install ok installed\n', encoding='utf-8')
    (admin / 'status-old').write_text('Package: dpkg\nStatus: install ok installed\n', encoding='utf-8')
    if mode == 'corrupt-update':
        (updates / '0000').write_text('Status', encoding='utf-8')

    state = root / 'dpkg-state'
    state.write_text('0' if mode == 'dependency-broken' else '1', encoding='utf-8')
    apt_log = root / 'apt.log'

    dpkg = bindir / 'dpkg'
    dpkg.write_text(
        '#!/bin/sh\n'
        'set -eu\n'
        f'admin={str(admin)!r}\n'
        f'state={str(state)!r}\n'
        f'mode={mode!r}\n'
        'case "$*" in\n'
        '  *"--force-confold --configure -a"*)\n'
        '    if [ "$mode" = corrupt-update ] && [ -f "$admin/updates/0000" ]; then\n'
        '      echo "dpkg: error: parsing file '\''$admin/updates/0000'\'' near line 0:" >&2\n'
        '      echo "end of file after field name '\'''\''" >&2\n'
        '      exit 2\n'
        '    fi\n'
        '    if [ "$mode" = dependency-broken ] && [ "$(cat "$state")" = 0 ]; then\n'
        '      echo "simulated dependency problem" >&2\n'
        '      exit 1\n'
        '    fi\n'
        '    exit 0;;\n'
        '  *"--audit"*) exit 0;;\n'
        '  *) echo "unexpected dpkg args: $*" >&2; exit 9;;\n'
        'esac\n',
        encoding='utf-8',
    )
    dpkg.chmod(0o755)

    apt = bindir / 'apt-get'
    apt.write_text(
        '#!/bin/sh\n'
        'set -eu\n'
        f'printf "%s\\n" "$*" >> {str(apt_log)!r}\n'
        f'state={str(state)!r}\n'
        'case "$*" in *"--fix-broken"*) echo 1 > "$state";; esac\n'
        'exit 0\n',
        encoding='utf-8',
    )
    apt.chmod(0o755)
    return bindir, apt_log, admin


def run_simulation(function_text, mode: str):
    with tempfile.TemporaryDirectory(prefix='vvv-dpkg-repair.') as td:
        root = Path(td)
        bindir, apt_log, admin = make_fake_tools(root, mode)
        backup_root = root / 'backups'
        harness = root / 'run.sh'
        harness.write_text(
            '#!/usr/bin/env bash\n'
            'set -Eeuo pipefail\n'
            'fail(){ echo "ERROR:$*" >&2; exit 1; }\n'
            + function_text + '\nrepair_dpkg_state\n',
            encoding='utf-8',
        )
        harness.chmod(0o755)
        env = os.environ.copy()
        env['PATH'] = str(bindir) + os.pathsep + env['PATH']
        env['VVV_DPKG_ADMIN_DIR'] = str(admin)
        env['VVV_DPKG_BACKUP_ROOT'] = str(backup_root)
        proc = subprocess.run([str(harness)], env=env, text=True, capture_output=True)
        if proc.returncode != 0:
            raise AssertionError(proc.stdout + proc.stderr)
        apt_calls = apt_log.read_text(encoding='utf-8') if apt_log.exists() else ''
        backup_dirs = sorted(backup_root.glob('vvv-dpkg-recovery-*')) if backup_root.exists() else []
        snapshot = {
            'update_exists': (admin / 'updates' / '0000').exists(),
            'backup_count': len(backup_dirs),
            'backup_has_update': False,
            'backup_update_content': '',
            'backup_has_status': False,
            'backup_has_status_old': False,
        }
        if backup_dirs:
            backup = backup_dirs[0]
            saved = backup / 'updates' / '0000'
            snapshot['backup_has_update'] = saved.is_file()
            snapshot['backup_update_content'] = saved.read_text(encoding='utf-8') if saved.is_file() else ''
            snapshot['backup_has_status'] = (backup / 'status').is_file()
            snapshot['backup_has_status_old'] = (backup / 'status-old').is_file()
        return proc.stdout + proc.stderr, apt_calls, snapshot


def main():
    texts = {path.name: read(path) for path in FILES}
    for path, text in zip(FILES, texts.values()):
        for token in (
            'repair_dpkg_state() {',
            'VVV_DPKG_ADMIN_DIR',
            'VVV_DPKG_BACKUP_ROOT',
            'LC_ALL=C dpkg --force-confold --configure -a',
            'updates/[0-9][0-9][0-9][0-9]',
            '已隔离备份到',
            'dpkg --audit',
            '--fix-broken --no-remove install -y --no-install-recommends',
            'DPkg::Lock::Timeout=10',
            '未删除任何锁文件或软件包',
        ):
            assert token in text, f'{path}: missing {token}'
        for forbidden in (
            'rm -f /var/lib/dpkg/lock',
            'rm -f /var/lib/dpkg/lock-frontend',
            'rm -f /var/lib/dpkg/updates/',
            'rm -rf /var/lib/dpkg/updates',
            'killall apt',
            'pkill apt',
            'killall dpkg',
            'pkill dpkg',
        ):
            assert forbidden not in text, f'{path}: unsafe repair behavior {forbidden}'

    installer = texts['vvv-install.sh']
    host = texts['host.sh']
    landing = texts['landing.sh']
    center = texts['center_install.sh']
    assert installer.index('repair_dpkg_state\n\nif ! command -v curl') > installer.index('VERSION_ID')
    assert 'upgrade_system_once() {\n  export DEBIAN_FRONTEND=noninteractive\n  export NEEDRESTART_MODE=a\n  repair_dpkg_state' in host
    assert 'upgrade_system_once() {\n  mkdir -p "$(dirname "$UPGRADE_MARKER")"\n  export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a\n  repair_dpkg_state' in landing
    assert 'section "准备订阅中心依赖"\nrepair_dpkg_state\nrequired=' in center

    subprocess.run(['bash', '-n', str(ROOT / 'vvv-install.sh')], check=True)
    subprocess.run(['bash', '-n', str(ROOT / 'core-src' / 'host.sh')], check=True)
    subprocess.run(['sh', '-n', str(ROOT / 'core-src' / 'landing.sh')], check=True)
    subprocess.run(['bash', '-n', str(ROOT / 'core-src' / 'center_install.sh')], check=True)

    fn = extract_installer_function(installer)

    output, apt_calls, snapshot = run_simulation(fn, 'corrupt-update')
    assert "parsing file" in output and 'updates/0000' in output
    assert '检测到损坏的 dpkg 临时更新文件' in output
    assert '已隔离备份到' in output
    assert 'dpkg 状态：正常' in output
    assert apt_calls == '', 'corrupt update fragment must be quarantined before apt fix-broken'
    assert not snapshot['update_exists'], 'corrupt update fragment must leave dpkg updates directory'
    assert snapshot['backup_count'] == 1
    assert snapshot['backup_has_update'] and snapshot['backup_update_content'] == 'Status'
    assert snapshot['backup_has_status'] and snapshot['backup_has_status_old']

    output, apt_calls, snapshot = run_simulation(fn, 'dependency-broken')
    assert 'simulated dependency problem' in output
    assert 'dpkg 状态：正常' in output
    assert 'update' in apt_calls
    assert '--fix-broken' in apt_calls and '--no-remove' in apt_calls
    assert snapshot['backup_count'] == 0, 'dependency-only repair must not create update quarantine backups'

    output, apt_calls, snapshot = run_simulation(fn, 'clean')
    assert 'dpkg 状态：正常' in output
    assert apt_calls == '', 'clean dpkg state must not invoke apt repair path'
    assert snapshot['backup_count'] == 0

    print('PASS dpkg recovery handles clean, dependency-broken, and corrupt updates/NNNN states safely')


if __name__ == '__main__':
    main()

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


def make_fake_tools(root: Path, initially_broken: bool):
    bindir = root / 'bin'
    bindir.mkdir()
    state = root / 'dpkg-state'
    state.write_text('0' if initially_broken else '1', encoding='utf-8')
    apt_log = root / 'apt.log'

    dpkg = bindir / 'dpkg'
    dpkg.write_text(
        '#!/bin/sh\n'
        'set -eu\n'
        f'state={state!s}\n'
        'case "$*" in\n'
        '  *"--force-confold --configure -a"*)\n'
        '    n="$(cat "$state")"\n'
        '    if [ "$n" = 0 ]; then echo 1 > "$state"; echo "simulated interrupted dpkg" >&2; exit 1; fi\n'
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
        f'printf "%s\\n" "$*" >> {apt_log!s}\n'
        'exit 0\n',
        encoding='utf-8',
    )
    apt.chmod(0o755)
    return bindir, apt_log


def run_simulation(function_text, initially_broken: bool):
    with tempfile.TemporaryDirectory(prefix='vvv-dpkg-repair.') as td:
        root = Path(td)
        bindir, apt_log = make_fake_tools(root, initially_broken)
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
        proc = subprocess.run([str(harness)], env=env, text=True, capture_output=True)
        if proc.returncode != 0:
            raise AssertionError(proc.stdout + proc.stderr)
        apt_calls = apt_log.read_text(encoding='utf-8') if apt_log.exists() else ''
        return proc.stdout + proc.stderr, apt_calls


def main():
    texts = {path.name: read(path) for path in FILES}
    for path, text in zip(FILES, texts.values()):
        for token in (
            'repair_dpkg_state() {',
            'dpkg --force-confold --configure -a',
            'dpkg --audit',
            '--fix-broken --no-remove install -y --no-install-recommends',
            'DPkg::Lock::Timeout=10',
            '未删除任何锁文件或软件包',
        ):
            assert token in text, f'{path}: missing {token}'
        for forbidden in (
            'rm -f /var/lib/dpkg/lock',
            'rm -f /var/lib/dpkg/lock-frontend',
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
    output, apt_calls = run_simulation(fn, initially_broken=True)
    assert 'simulated interrupted dpkg' in output
    assert 'dpkg 状态：正常' in output
    assert 'update' in apt_calls
    assert '--fix-broken' in apt_calls and '--no-remove' in apt_calls

    output, apt_calls = run_simulation(fn, initially_broken=False)
    assert 'dpkg 状态：正常' in output
    assert apt_calls == '', 'clean dpkg state must not invoke apt repair path'

    print('PASS interrupted dpkg state is repaired safely before every fresh-install apt path')


if __name__ == '__main__':
    main()

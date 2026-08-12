#!/usr/bin/env python3
import importlib.util
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPAT = ROOT / 'core-src/debian_compat.py'
INSTALLER = ROOT / 'vvv-install.sh'


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    if not spec.loader:
        raise RuntimeError(f'cannot load {path}')
    spec.loader.exec_module(module)
    return module


def main():
    compat = load_module(COMPAT, 'vvv_debian_compat_test')
    assert compat.SUPPORTED_VERSIONS == ('12', '13')

    installer = INSTALLER.read_text(encoding='utf-8')
    assert '"${VERSION_ID:-}" =~ ^(12|13)$' in installer
    assert 'VVV 仅支持 Debian 12/13' in installer
    assert 'debian_compat.py' in installer
    assert 'python3 "$TMP/app/debian_compat.py" "$TMP/app"' in installer
    assert installer.index('python3 "$TMP/app/debian_compat.py" "$TMP/app"') < installer.index('python3 "$TMP/prepare.py"')
    subprocess.run(['bash', '-n', str(INSTALLER)], check=True)

    with tempfile.TemporaryDirectory(prefix='vvv-debian-compat-test.') as td:
        app = Path(td)
        for name in ('bootstrap.sh', 'host.sh', 'landing.sh'):
            shutil.copy2(ROOT / 'core-src' / name, app / name)

        compat.patch_tree(app, syntax_check=True)
        first = {name: (app / name).read_bytes() for name in ('bootstrap.sh', 'host.sh', 'landing.sh')}
        compat.patch_tree(app, syntax_check=True)
        second = {name: (app / name).read_bytes() for name in ('bootstrap.sh', 'host.sh', 'landing.sh')}
        assert first == second, 'Debian compatibility transformation must be idempotent'

        bootstrap = (app / 'bootstrap.sh').read_text(encoding='utf-8')
        host = (app / 'host.sh').read_text(encoding='utf-8')
        landing = (app / 'landing.sh').read_text(encoding='utf-8')

        # Every installation role must remain available after compatibility transformation.
        for label in (
            '安装订阅中心 + 中转主机 + 自身代理',
            '安装订阅中心 + 自身代理',
            '安装中转主机 + 自身代理',
            '安装中转副机 + 自身代理',
            '安装中转副机',
            '安装直连代理',
            '从云备份恢复',
        ):
            assert label in bootstrap

        # Main-host system features required by the user.
        for token in (
            'net.ipv4.tcp_congestion_control = bbr',
            'net.core.default_qdisc = fq',
            'Asia/Shanghai',
            '0 6 * * * root /usr/local/lib/vvv/daily-reboot.sh',
            'cron.service',
            'nftables cron',
            'tzdata kmod util-linux',
            'python3-venv',
        ):
            assert token in host, token

        # Landing/relay-host system features required by the user.
        for token in (
            'net.ipv4.tcp_congestion_control = bbr',
            'net.core.default_qdisc = fq',
            'Asia/Shanghai',
            'OnCalendar=*-*-* 06:00:00',
            'daily-reboot.timer',
            'tzdata kmod util-linux python3',
        ):
            assert token in landing, token

        assert '仅支持 Debian 13' not in bootstrap
        assert '仅支持 Debian 13' not in host
        assert '仅支持 Debian 13' not in landing
        assert '只支持 Debian 13' not in bootstrap + host + landing
        assert '^(12|13)$' in bootstrap
        assert '^(12|13)$' in host
        assert '12|13)' in landing

    print('PASS Debian 12/13 fresh-install compatibility')


if __name__ == '__main__':
    main()

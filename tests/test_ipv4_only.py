#!/usr/bin/env python3
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'core-src' / 'ipv4_only.sh'


def read(path):
    return Path(path).read_text(encoding='utf-8')


def simulate_system_enforcement():
    with tempfile.TemporaryDirectory(prefix='vvv-ipv4-only.') as td:
        root = Path(td)
        etc = root / 'etc'
        proc = root / 'proc'
        boot = root / 'boot'
        for iface in ('all', 'default', 'lo', 'eth0'):
            knob = proc / 'sys' / 'net' / 'ipv6' / 'conf' / iface / 'disable_ipv6'
            knob.parent.mkdir(parents=True, exist_ok=True)
            knob.write_text('0\n', encoding='ascii')
        (proc / '1').mkdir(parents=True, exist_ok=True)
        (proc / '1' / 'cgroup').write_text('0::/\n', encoding='ascii')
        (proc / 'mounts').write_text('', encoding='ascii')
        boot.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env.update({
            'VVV_IPV4_ONLY_ETC_ROOT': str(etc),
            'VVV_IPV4_ONLY_PROC_ROOT': str(proc),
            'VVV_IPV4_ONLY_BOOT_ROOT': str(boot),
            'VVV_IPV4_ONLY_CONTAINER_MODE': '0',
            'VVV_IPV4_ONLY_SKIP_GRUB_UPDATE': '1',
        })
        command = f'. {MODULE!s}; vvv_enforce_ipv4_only; vvv_enforce_ipv4_only'
        proc_run = subprocess.run(['sh', '-c', command], env=env, text=True, capture_output=True)
        assert proc_run.returncode == 0, proc_run.stdout + proc_run.stderr
        assert proc_run.stdout.count('IPv4-only：已强制启用') == 2

        for iface in ('all', 'default', 'lo', 'eth0'):
            knob = proc / 'sys' / 'net' / 'ipv6' / 'conf' / iface / 'disable_ipv6'
            assert knob.read_text(encoding='ascii').strip() == '1', iface

        sysctl = read(etc / 'sysctl.d' / '99-vvv-ipv4-only.conf')
        for token in (
            'net.ipv6.conf.all.disable_ipv6 = 1',
            'net.ipv6.conf.default.disable_ipv6 = 1',
            'net.ipv6.conf.lo.disable_ipv6 = 1',
        ):
            assert token in sysctl

        modprobe = read(etc / 'modprobe.d' / '99-vvv-ipv4-only.conf')
        assert 'options ipv6 disable=1' in modprobe

        grub = etc / 'default' / 'grub.d' / '99-vvv-ipv4-only.cfg'
        grub_text = read(grub)
        assert 'ipv6.disable=1' in grub_text
        shell = (
            'set -eu; GRUB_CMDLINE_LINUX="console=ttyS0"; '
            f'. {grub}; . {grub}; '
            'printf "%s\\n" "$GRUB_CMDLINE_LINUX"'
        )
        rendered = subprocess.check_output(['sh', '-c', shell], text=True).strip()
        assert rendered.startswith('console=ttyS0')
        assert rendered.split().count('ipv6.disable=1') == 1, rendered


def generated_manager_contract(host):
    marker = "cat > /usr/local/sbin/jp-relay-manager <<'JP_RELAY_JPR3_MANAGER_EOF'\n"
    assert host.count(marker) == 1, 'jp-relay-manager heredoc must exist exactly once'
    tail = host.split(marker, 1)[1]
    assert '\nJP_RELAY_JPR3_MANAGER_EOF' in tail, 'jp-relay-manager heredoc terminator missing'
    manager = tail.split('\nJP_RELAY_JPR3_MANAGER_EOF', 1)[0]

    module_decl = 'IPV4_ONLY_MODULE="${VVV_IPV4_ONLY_MODULE:-/usr/local/lib/vvv/ipv4_only.sh}"'
    source_line = 'source "$IPV4_ONLY_MODULE"'
    assert module_decl in manager
    assert '[[ -r "$IPV4_ONLY_MODULE" ]]' in manager
    assert source_line in manager
    assert 'vvv_enforce_ipv4_only' in manager
    assert manager.index(source_line) < manager.index('vvv_enforce_ipv4_only')

    with tempfile.NamedTemporaryFile('w', encoding='utf-8', delete=False) as handle:
        handle.write(manager)
        manager_path = Path(handle.name)
    try:
        subprocess.run(['bash', '-n', str(manager_path)], check=True)
    finally:
        manager_path.unlink(missing_ok=True)


def static_contracts():
    installer = read(ROOT / 'vvv-install.sh')
    bootstrap = read(ROOT / 'core-src' / 'bootstrap.sh')
    host = read(ROOT / 'core-src' / 'host.sh')
    landing = read(ROOT / 'core-src' / 'landing.sh')
    center = read(ROOT / 'core-src' / 'center_install.sh')
    transport = read(ROOT / 'core-src' / 'center_transport.sh')
    hopping = read(ROOT / 'core-src' / 'hy2_port_hop.py')

    assert 'ipv4_only.sh' in installer
    assert '. "$TMP/app/ipv4_only.sh"' in installer
    assert 'vvv_enforce_ipv4_only' in installer
    assert 'curl -4fsSL' in installer

    for label, text in (
        ('bootstrap', bootstrap),
        ('host', host),
        ('landing', landing),
        ('center', center),
    ):
        assert 'ipv4_only.sh' in text, label
        assert 'vvv_enforce_ipv4_only' in text, label

    generated_manager_contract(host)

    assert '"listen":"0.0.0.0"' in host
    assert '"listen":"::"' not in host
    assert '"listen":"[::]"' not in host
    assert '"domainStrategy":"UseIPv4"' in host
    assert '"type":"hysteria2","tag":"hy2-in","listen":"0.0.0.0"' in host

    assert '"listen": "0.0.0.0"' in landing
    assert '"listen": "::"' not in landing
    assert '"listen": "[::]"' not in landing
    assert '"domainStrategy": "UseIPv4"' in landing

    assert "'listen_host':'0.0.0.0'" in center
    assert "'listen_host':'::'" not in center

    assert transport.count('bind 0.0.0.0') >= 4
    assert 'http://127.0.0.1:${port} {\n  bind 127.0.0.1' in transport
    assert '--edge-ip-version 4' in transport
    assert 'bind ::' not in transport and 'bind [::]' not in transport

    assert 'TABLE_FAMILY = "ip"' in hopping
    assert 'TABLE_FAMILY = "inet"' not in hopping

    subprocess.run(['sh', '-n', str(MODULE)], check=True)
    subprocess.run(['bash', '-n', str(ROOT / 'vvv-install.sh')], check=True)
    subprocess.run(['bash', '-n', str(ROOT / 'core-src' / 'bootstrap.sh')], check=True)
    subprocess.run(['bash', '-n', str(ROOT / 'core-src' / 'host.sh')], check=True)
    subprocess.run(['sh', '-n', str(ROOT / 'core-src' / 'landing.sh')], check=True)
    subprocess.run(['bash', '-n', str(ROOT / 'core-src' / 'center_install.sh')], check=True)
    subprocess.run(['bash', '-n', str(ROOT / 'core-src' / 'center_transport.sh')], check=True)


def main():
    static_contracts()
    simulate_system_enforcement()
    print('PASS VVV enforces permanent server-side IPv4-only policy and generated relay manager runtime loading')


if __name__ == '__main__':
    main()

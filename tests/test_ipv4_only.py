#!/usr/bin/env python3
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'core-src' / 'ipv4_only.sh'


def read(path):
    return Path(path).read_text(encoding='utf-8')


def make_fake_proc(proc):
    for iface in ('all', 'default', 'lo', 'eth0'):
        iface_dir = proc / 'sys' / 'net' / 'ipv6' / 'conf' / iface
        iface_dir.mkdir(parents=True, exist_ok=True)
        (iface_dir / 'disable_ipv6').write_text('0\n', encoding='ascii')
        (iface_dir / 'accept_ra').write_text('1\n', encoding='ascii')
        (iface_dir / 'autoconf').write_text('1\n', encoding='ascii')
    (proc / '1').mkdir(parents=True, exist_ok=True)
    (proc / '1' / 'cgroup').write_text('0::/\n', encoding='ascii')
    (proc / 'mounts').write_text('', encoding='ascii')


def base_env(etc, proc, boot):
    env = os.environ.copy()
    env.update({
        'VVV_IPV4_ONLY_ETC_ROOT': str(etc),
        'VVV_IPV4_ONLY_PROC_ROOT': str(proc),
        'VVV_IPV4_ONLY_BOOT_ROOT': str(boot),
        'VVV_IPV4_ONLY_CONTAINER_MODE': '0',
        'VVV_IPV4_ONLY_SKIP_GRUB_UPDATE': '1',
    })
    return env


def simulate_system_enforcement():
    with tempfile.TemporaryDirectory(prefix='vvv-ipv4-only.') as td:
        root = Path(td)
        etc = root / 'etc'
        proc = root / 'proc'
        boot = root / 'boot'
        make_fake_proc(proc)
        boot.mkdir(parents=True, exist_ok=True)

        env = base_env(etc, proc, boot)
        command = f'. {MODULE!s}; vvv_enforce_ipv4_only; vvv_enforce_ipv4_only'
        proc_run = subprocess.run(['sh', '-c', command], env=env, text=True, capture_output=True)
        assert proc_run.returncode == 0, proc_run.stdout + proc_run.stderr
        assert proc_run.stdout.count('IPv4-only：已强制启用') == 2

        for iface in ('all', 'default', 'lo', 'eth0'):
            iface_dir = proc / 'sys' / 'net' / 'ipv6' / 'conf' / iface
            assert (iface_dir / 'disable_ipv6').read_text(encoding='ascii').strip() == '1', iface
            assert (iface_dir / 'accept_ra').read_text(encoding='ascii').strip() == '0', iface
            assert (iface_dir / 'autoconf').read_text(encoding='ascii').strip() == '0', iface

        sysctl = read(etc / 'sysctl.d' / '99-vvv-ipv4-only.conf')
        for token in (
            'net.ipv6.conf.all.disable_ipv6 = 1',
            'net.ipv6.conf.default.disable_ipv6 = 1',
            'net.ipv6.conf.lo.disable_ipv6 = 1',
            'net.ipv6.conf.all.accept_ra = 0',
            'net.ipv6.conf.default.accept_ra = 0',
            'net.ipv6.conf.all.autoconf = 0',
            'net.ipv6.conf.default.autoconf = 0',
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


def write_fake_ip(path):
    path.write_text(
        r'''#!/bin/sh
set -eu
state="${VVV_TEST_IP_STATE:?}"
mode="${VVV_TEST_IP_MODE:?}"

if [ "$#" -ge 3 ] && [ "$1" = "-6" ] && [ "$2" = "addr" ] && [ "$3" = "show" ]; then
  if [ "$mode" = "addr-present" ]; then
    printf '%s\n' '2: eth0    inet6 2001:db8::2/64 scope global'
  fi
  exit 0
fi

if [ "$*" = "-6 route flush table all" ]; then
  count=0
  [ ! -f "$state" ] || count="$(cat "$state")"
  count=$((count + 1))
  printf '%s\n' "$count" > "$state"
  exit 0
fi

case "$*" in
  "-6 route flush table all proto ra"|"-6 route flush cache")
    exit 0
    ;;
esac

if [ "$*" = "-6 route show table all" ]; then
  count=0
  [ ! -f "$state" ] || count="$(cat "$state")"
  case "$mode" in
    reinject-once)
      if [ "$count" -lt 2 ]; then
        printf '%s\n' 'default nhid 3025548617 via fe80::ce16:7eff:fe24:30c0 dev eth0 proto ra metric 1024 expires 1621sec pref medium'
      fi
      ;;
    persistent-global)
      printf '%s\n' 'default via fe80::1 dev eth0 proto ra metric 1024 pref medium'
      ;;
    blocked-residual)
      printf '%s\n' 'unreachable default dev lo metric 1024 error -101 pref medium'
      ;;
  esac
  exit 0
fi

exit 0
''',
        encoding='utf-8',
    )
    path.chmod(0o755)


def run_network_case(mode, command='vvv_enforce_ipv4_only'):
    with tempfile.TemporaryDirectory(prefix=f'vvv-ipv4-route-{mode}.') as td:
        root = Path(td)
        etc = root / 'etc'
        proc = root / 'proc'
        boot = root / 'boot'
        state = root / 'ip-state'
        fake_ip = root / 'ip'
        make_fake_proc(proc)
        boot.mkdir(parents=True, exist_ok=True)
        write_fake_ip(fake_ip)

        env = base_env(etc, proc, boot)
        env.update({
            'VVV_IPV4_ONLY_FORCE_NETWORK_CHECK': '1',
            'VVV_IPV4_ONLY_IP_BIN': str(fake_ip),
            'VVV_IPV4_ONLY_ROUTE_SETTLE_SECONDS': '0',
            'VVV_TEST_IP_MODE': mode,
            'VVV_TEST_IP_STATE': str(state),
        })
        shell = f'. {MODULE!s}; {command}'
        result = subprocess.run(['sh', '-c', shell], env=env, text=True, capture_output=True)
        count = int(state.read_text(encoding='ascii').strip()) if state.exists() else 0
        return result, count


def simulate_ra_route_reinjection():
    first, first_count = run_network_case(
        'reinject-once',
        'vvv_ipv4_only_apply_runtime; vvv_ipv4_only_cleanup_routes_once; vvv_ipv4_only_verify_runtime',
    )
    assert first_count == 1, first_count
    assert first.returncode != 0, first.stdout + first.stderr
    assert '可用的全局 IPv6 路由' in first.stderr

    recovered, flush_count = run_network_case('reinject-once')
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert flush_count == 2, flush_count
    assert 'IPv4-only：已强制启用' in recovered.stdout


def simulate_route_safety_guards():
    blocked, _ = run_network_case('blocked-residual')
    assert blocked.returncode == 0, blocked.stdout + blocked.stderr

    persistent, _ = run_network_case('persistent-global')
    assert persistent.returncode != 0, persistent.stdout + persistent.stderr
    assert '可用的全局 IPv6 路由' in persistent.stderr

    addr, _ = run_network_case('addr-present')
    assert addr.returncode != 0, addr.stdout + addr.stderr
    assert '仍检测到 IPv6 地址' in addr.stderr


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
    simulate_ra_route_reinjection()
    simulate_route_safety_guards()
    print('PASS VVV enforces permanent IPv4-only policy including delayed RA-route cleanup')


if __name__ == '__main__':
    main()

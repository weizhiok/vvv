#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

NODES = Path('/var/lib/vvv-sub/output/nodes.json')
MAIN_STATE = Path('/etc/jp-relay/state.json')
SUB = Path('/usr/local/lib/vvv/sub_center.py')
XRAY = Path('/usr/local/bin/xray')
SING = Path('/usr/local/bin/sing-box')
GREEN = '\033[1;32m'
RED = '\033[1;31m'
RESET = '\033[0m'


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default


def free_port():
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]; sock.close(); return port


def wait_port(port, process):
    for _ in range(30):
        if process.poll() is not None:
            return False
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.25):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def curl_socks(port):
    errors = []
    for url in ('https://api.ipify.org', 'https://ipv4.icanhazip.com'):
        result = subprocess.run(
            ['curl', '-4fsS', '--socks5-hostname', f'127.0.0.1:{port}', '--connect-timeout', '6', '--max-time', '15', url],
            text=True, capture_output=True,
        )
        value = result.stdout.strip()
        try:
            socket.inet_aton(value)
            return value, ''
        except OSError:
            errors.append(result.stderr.strip() or '无有效出口 IP')
    return '', '；'.join(errors[-2:])


def local_test_map():
    state = read(MAIN_STATE, {}) or {}
    mapping = {}
    for relay in state.get('relays') or []:
        if relay.get('vless'):
            mapping[('vless', relay['vless'].get('client_uuid'))] = (relay['vless'].get('test_socks_port'), relay.get('remote_ip'))
        if relay.get('hy2'):
            mapping[('hysteria2', relay['hy2'].get('client_password'))] = (relay['hy2'].get('test_socks_port'), relay.get('remote_ip'))
    for upstream in state.get('upstream_relays') or []:
        mapping[('vless', upstream.get('client_uuid'))] = (upstream.get('test_socks_port'), upstream.get('last_exit_ip'))
    return state, mapping


def probe_local_direct(node, state):
    if str(node.get('server')) != str(state.get('public_ip')):
        return None
    if node.get('category') != '直连':
        return None
    service = 'xray' if node.get('protocol') == 'vless' else 'sing-box'
    active = subprocess.run(['systemctl', 'is-active', '--quiet', service], check=False).returncode == 0
    protocol = 'tcp' if node.get('protocol') == 'vless' else 'udp'
    command = ['ss', '-H', '-lnt'] if protocol == 'tcp' else ['ss', '-H', '-lnu']
    listening = f":{node.get('port')}" in subprocess.run(command, text=True, capture_output=True).stdout
    return (active and listening, '本机服务与监听端口正常' if active and listening else '本机服务或监听端口异常')


def build_xray(node, port, target):
    cfg = {
        'log': {'loglevel': 'warning'},
        'inbounds': [{'listen': '127.0.0.1', 'port': port, 'protocol': 'socks', 'settings': {'udp': False}}],
        'outbounds': [{
            'tag': 'proxy', 'protocol': 'vless',
            'settings': {'address': node['server'], 'port': int(node['port']), 'id': node['uuid'], 'encryption': 'none', 'flow': 'xtls-rprx-vision'},
            'streamSettings': {'method': 'raw', 'security': 'reality', 'realitySettings': {
                'serverName': node['sni'], 'fingerprint': 'chrome', 'password': node['public_key'],
                'shortId': node['short_id'], 'spiderX': ''}},
        }],
    }
    target.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def build_sing(node, port, target):
    cfg = {
        'log': {'level': 'warn'},
        'inbounds': [{'type': 'mixed', 'listen': '127.0.0.1', 'listen_port': port}],
        'outbounds': [{
            'type': 'hysteria2', 'tag': 'proxy', 'server': node['server'], 'server_port': int(node['port']),
            'up_mbps': int(node.get('limit_mbps') or 50), 'down_mbps': int(node.get('limit_mbps') or 50),
            'password': node['password'], 'obfs': {'type': 'salamander', 'password': node['obfs_password']},
            'tls': {'enabled': True, 'server_name': node['sni'], 'insecure': True, 'alpn': ['h3'], 'min_version': '1.3'},
        }],
        'route': {'final': 'proxy', 'auto_detect_interface': True},
    }
    target.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def generic_probe(node):
    binary = XRAY if node.get('protocol') == 'vless' else SING
    if not binary.exists():
        return False, f'{binary.name} 不存在'
    port = free_port()
    with tempfile.TemporaryDirectory(prefix='vvv-node-probe.') as td:
        cfg = Path(td) / 'config.json'; log = Path(td) / 'process.log'
        if node.get('protocol') == 'vless':
            build_xray(node, port, cfg); command = [str(binary), 'run', '-format=json', '-config', str(cfg)]
            check = [str(binary), 'run', '-test', '-format=json', '-config', str(cfg)]
        else:
            build_sing(node, port, cfg); command = [str(binary), 'run', '-c', str(cfg)]
            check = [str(binary), 'check', '-c', str(cfg)]
        verified = subprocess.run(check, text=True, capture_output=True, timeout=30)
        if verified.returncode != 0:
            return False, '临时客户端配置校验失败'
        with log.open('w') as handle:
            process = subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT)
        try:
            if not wait_port(port, process):
                return False, '临时客户端没有启动'
            exit_ip, error = curl_socks(port)
            if not exit_ip:
                return False, error
            expected = str(node.get('expected_exit_ip') or '')
            if expected and exit_ip != expected:
                return False, f'出口 {exit_ip}，预期 {expected}'
            return True, f'真实连接成功，出口 {exit_ip}'
        finally:
            process.terminate()
            try: process.wait(timeout=3)
            except subprocess.TimeoutExpired: process.kill()


def probe_all():
    if SUB.exists():
        subprocess.run(['python3', str(SUB), 'regenerate'], check=False, stdout=subprocess.DEVNULL)
    data = read(NODES, {}) or {}
    nodes = data.get('nodes') or []
    state, local_map = local_test_map()
    results = []
    for node in nodes:
        name = str(node.get('name') or node.get('id') or '未命名节点')
        secret = node.get('uuid') if node.get('protocol') == 'vless' else node.get('password')
        local = local_map.get((node.get('protocol'), secret))
        if local and local[0]:
            exit_ip, error = curl_socks(int(local[0]))
            ok = bool(exit_ip) and (not local[1] or exit_ip == local[1])
            detail = f'真实连接成功，出口 {exit_ip}' if ok else (f'出口 {exit_ip}，预期 {local[1]}' if exit_ip else error)
        else:
            direct = probe_local_direct(node, state)
            if direct is not None:
                ok, detail = direct
            else:
                ok, detail = generic_probe(node)
        results.append({'id': node.get('id'), 'name': name, 'online': ok, 'detail': detail})
        prefix, color = ('✓', GREEN) if ok else ('✗', RED)
        print(f'{color}{prefix} {name}：{"在线" if ok else "离线"}，{detail}{RESET}')
    online = sum(1 for row in results if row['online'])
    print(f'节点检测：{online} 个在线，{len(results)-online} 个离线。')
    return {'count': len(results), 'online': online, 'offline': len(results)-online, 'results': results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--json', action='store_true'); args = parser.parse_args()
    result = probe_all()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))

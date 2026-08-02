#!/usr/bin/env python3
import argparse
import base64
import hashlib
import ipaddress
import json
import os
import platform
import re
import socket
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

CFG = Path('/etc/vvv/client.json')
CENTER_CFG = Path('/etc/vvv-sub/config.json')
MAIN_STATE = Path('/etc/jp-relay/state.json')
LANDING_STATE = Path('/etc/jp-relay/landing-state.json')


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default


def atomic(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd_path = path.with_suffix('.tmp')
    fd_path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.chmod(fd_path, 0o600)
    os.replace(fd_path, path)


def post(url, token, obj):
    data = json.dumps(obj, ensure_ascii=False).encode()
    headers = {'Content-Type': 'application/json', 'User-Agent': 'VVV-Sync/4.0'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = Request(url, data=data, method='POST', headers=headers)
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def encode_vvc1(payload):
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip('=')
    digest = hashlib.sha256(b'VVV-VVC1\0' + raw).hexdigest()[:20]
    return f'VVC1.{encoded}.{digest}'


def decode_vvc1(code):
    value = ''.join(str(code or '').split())
    if value.startswith('JPR3.'):
        raise ValueError('这是中转副机 JPR3 密钥，不能用于订阅中心注册。')
    if value.startswith('VVVR1.'):
        raise ValueError('这是旧云恢复码，不能用于订阅中心注册。')
    parts = value.split('.')
    if len(parts) != 3 or parts[0] != 'VVC1' or not re.fullmatch(r'[A-Za-z0-9_-]+', parts[1]) or not re.fullmatch(r'[0-9a-f]{20}', parts[2]):
        raise ValueError('订阅中心对接码格式错误，必须以 VVC1. 开头。')
    try:
        raw = base64.urlsafe_b64decode(parts[1] + '=' * ((4 - len(parts[1]) % 4) % 4))
        actual = hashlib.sha256(b'VVV-VVC1\0' + raw).hexdigest()[:20]
        if actual != parts[2]:
            raise ValueError('订阅中心对接码校验失败，内容可能复制不完整。')
        obj = json.loads(raw.decode())
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f'订阅中心对接码无法解析：{exc}') from exc
    if obj.get('schema') != 1 or obj.get('type') != 'vvv-subscription-center':
        raise ValueError('对接码用途错误，不是 VVV 订阅中心对接码。')
    parsed = urlparse(str(obj.get('api_base_url') or ''))
    if parsed.scheme != 'http' or not parsed.hostname:
        raise ValueError('对接码中的订阅中心 API 地址无效。')
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError as exc:
        raise ValueError('对接码中的订阅中心必须使用 IP 地址，不能使用域名。') from exc
    if not str(obj.get('master_token') or ''):
        raise ValueError('对接码缺少注册授权信息。')
    return obj


def stable_id():
    seed = '|'.join([socket.gethostname(), platform.machine()])
    try:
        seed += '|' + Path('/etc/machine-id').read_text().strip()
    except Exception:
        pass
    return hashlib.sha256(seed.encode()).hexdigest()[:32]


def state_path(role):
    return LANDING_STATE if role == 'landing' else MAIN_STATE


def snapshot_payload(role):
    state = read(state_path(role), {}) or {}
    return {
        'state': state,
        'meta': {
            'hostname': socket.gethostname(),
            'role': role,
            'timestamp': time.time(),
            'xray_active': os.system('systemctl is-active --quiet xray') == 0,
            'sing_box_active': os.system('systemctl is-active --quiet sing-box') == 0,
        },
    }


def require_registration_success(response):
    if not isinstance(response, dict):
        raise SystemExit('订阅中心返回了无效的注册结果。')
    if any(response.get(key) is not True for key in ('ok', 'registered', 'subscription_refreshed')):
        raise SystemExit('订阅中心未确认注册和订阅刷新成功。')
    if not response.get('host_token'):
        raise SystemExit('订阅中心注册响应缺少主机令牌。')
    return response


def local_api_for(role, api_base):
    if role in ('center', 'center-relay') and CENTER_CFG.exists():
        center = read(CENTER_CFG, {}) or {}
        return f"http://127.0.0.1:{int(center.get('listen_port') or 18081)}"
    return api_base.rstrip('/')


def register(code, role):
    try:
        decoded = decode_vvc1(code)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    public_api = str(decoded['api_base_url']).rstrip('/')
    api_base = local_api_for(role, public_api)
    host_id = stable_id()
    payload = {'host_id': host_id, 'role': role, 'hostname': socket.gethostname()}
    payload.update(snapshot_payload(role))
    response = require_registration_success(post(api_base + '/api/v1/register', decoded['master_token'], payload))
    current = time.time()
    cfg = {
        'schema': 4,
        'api_base_url': public_api,
        'effective_api_base_url': api_base,
        'center_ip': urlparse(public_api).hostname,
        'host_id': host_id,
        'host_token': response['host_token'],
        'role': role,
        'registered_at': current,
        'last_sync': current,
        'last_result': response,
        'subscription_url': response.get('subscription_url', ''),
        'registration_method': 'VVC1',
    }
    atomic(CFG, cfg)
    return response


def sync():
    cfg = read(CFG)
    if not cfg:
        raise SystemExit('尚未配置订阅同步。')
    payload = {'host_id': cfg['host_id']}
    payload.update(snapshot_payload(cfg.get('role', 'direct')))
    api_base = str(cfg.get('effective_api_base_url') or cfg.get('api_base_url') or '').rstrip('/')
    if not api_base:
        raise SystemExit('订阅中心 API 地址缺失。')
    response = post(api_base + '/api/v1/sync', cfg['host_token'], payload)
    cfg['last_sync'] = time.time()
    cfg['last_result'] = response
    if response.get('subscription_url'):
        cfg['subscription_url'] = response['subscription_url']
    atomic(CFG, cfg)
    print(json.dumps(response, ensure_ascii=False))


def update_center_ip(new_ip):
    try:
        ip = ipaddress.ip_address(new_ip)
    except ValueError as exc:
        raise SystemExit('请输入有效的订阅中心 IP 地址。') from exc
    if ip.version != 4 or ip.is_unspecified:
        raise SystemExit('当前只支持有效的 IPv4 地址。')
    cfg = read(CFG)
    if not cfg:
        raise SystemExit('尚未注册订阅中心。')
    old = json.loads(json.dumps(cfg))
    parsed = urlparse(str(cfg.get('api_base_url') or ''))
    port = parsed.port or 18081
    cfg['center_ip'] = str(ip)
    cfg['api_base_url'] = f'http://{ip}:{port}'
    if str(cfg.get('effective_api_base_url') or '').startswith('http://127.0.0.1:'):
        pass
    else:
        cfg['effective_api_base_url'] = cfg['api_base_url']
    atomic(CFG, cfg)
    try:
        sync()
    except Exception:
        atomic(CFG, old)
        raise
    return cfg


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='cmd', required=True)
    register_cmd = commands.add_parser('register')
    register_cmd.add_argument('code')
    register_cmd.add_argument('role', choices=['center-relay', 'center', 'relay', 'direct', 'landing'])
    commands.add_parser('sync')
    update_cmd = commands.add_parser('update-center-ip')
    update_cmd.add_argument('ip')
    validate_cmd = commands.add_parser('validate-code')
    validate_cmd.add_argument('code')
    args = parser.parse_args()
    if args.cmd == 'register':
        print(json.dumps(register(args.code, args.role), ensure_ascii=False))
    elif args.cmd == 'sync':
        sync()
    elif args.cmd == 'update-center-ip':
        print(json.dumps(update_center_ip(args.ip), ensure_ascii=False))
    else:
        print(json.dumps(decode_vvc1(args.code), ensure_ascii=False))

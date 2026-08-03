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
import zlib
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
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def post(url, token, obj):
    data = json.dumps(obj, ensure_ascii=False).encode()
    headers = {'Content-Type': 'application/json', 'User-Agent': 'VVV-Sync/5.0'}
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
    parts = value.split('.')
    if len(parts) != 3 or parts[0] != 'VVC1' or not re.fullmatch(r'[A-Za-z0-9_-]+', parts[1]) or not re.fullmatch(r'[0-9a-f]{20}', parts[2]):
        raise ValueError('订阅中心对接码格式错误，必须是完整 VVC1 或含注册票据的 JPR3。')
    try:
        raw = base64.urlsafe_b64decode(parts[1] + '=' * ((4 - len(parts[1]) % 4) % 4))
        actual = hashlib.sha256(b'VVV-VVC1\0' + raw).hexdigest()[:20]
        if actual != parts[2]:
            raise ValueError('VVC1 校验失败，内容可能复制不完整。')
        obj = json.loads(raw.decode())
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f'VVC1 无法解析：{exc}') from exc
    if obj.get('schema') != 1 or obj.get('type') != 'vvv-subscription-center':
        raise ValueError('VVC1 用途错误。')
    _validate_api(obj.get('api_base_url'))
    if not str(obj.get('master_token') or ''):
        raise ValueError('VVC1 缺少注册授权。')
    return obj


def decode_jpr3(code):
    value = ''.join(str(code or '').split())
    parts = value.split('.')
    if len(parts) != 3 or parts[0] != 'JPR3' or not re.fullmatch(r'[A-Za-z0-9_-]+', parts[1]) or not re.fullmatch(r'[0-9a-f]{20}', parts[2]):
        raise ValueError('JPR3 格式错误或复制不完整。')
    try:
        transferred = base64.urlsafe_b64decode(parts[1] + '=' * ((4 - len(parts[1]) % 4) % 4))
        if hashlib.sha256(transferred).hexdigest()[:20] != parts[2]:
            raise ValueError('JPR3 校验失败。')
        raw = transferred if transferred.startswith(b'{') else zlib.decompress(transferred)
        obj = json.loads(raw.decode())
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f'JPR3 无法解析：{exc}') from exc
    if obj.get('schema') != 4 or obj.get('type') != 'jp-relay-landing':
        raise ValueError('JPR3 版本或用途错误；请从最新版中转主机重新生成。')
    bootstrap = obj.get('subscription_bootstrap')
    if not isinstance(bootstrap, dict):
        raise ValueError('该 JPR3 没有订阅中心注册票据。请先把中转主机注册到订阅中心，再重新生成 JPR3。')
    _validate_api(bootstrap.get('api_base_url'))
    if not str(bootstrap.get('relay_id') or '') or not str(bootstrap.get('registration_token') or ''):
        raise ValueError('JPR3 中的订阅注册票据不完整。')
    if str(bootstrap.get('relay_id')) != str(obj.get('relay_id')):
        raise ValueError('JPR3 线路与订阅注册票据不匹配。')
    return obj, bootstrap


def _validate_api(value):
    parsed = urlparse(str(value or ''))
    if parsed.scheme != 'http' or not parsed.hostname:
        raise ValueError('订阅中心 API 地址无效。')
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError as exc:
        raise ValueError('订阅中心 API 必须使用 IP 地址，不能使用域名。') from exc


def stable_id():
    seed = '|'.join([socket.gethostname(), platform.machine()])
    try:
        seed += '|' + Path('/etc/machine-id').read_text().strip()
    except Exception:
        pass
    return hashlib.sha256(seed.encode()).hexdigest()[:32]


def service_active(name):
    return os.system(f'systemctl is-active --quiet {name}') == 0


def snapshot_payload(role):
    direct = read(MAIN_STATE, {}) or {}
    landing = read(LANDING_STATE, {}) or {}
    meta = {
        'hostname': socket.gethostname(),
        'role': role,
        'timestamp': time.time(),
        'services': {
            'xray': service_active('xray.service'),
            'sing_box': service_active('sing-box.service'),
            'landing_xray': service_active('vvv-landing-xray.service'),
            'landing_sing_box': service_active('vvv-landing-sing-box.service'),
        },
    }
    if role == 'landing-direct':
        return {'state': direct, 'states': {'direct': direct, 'landing': landing}, 'meta': meta}
    if role == 'landing':
        return {'state': landing, 'states': {'landing': landing}, 'meta': meta}
    return {'state': direct, 'states': {'direct': direct}, 'meta': meta}


def require_registration_success(response):
    if not isinstance(response, dict):
        raise SystemExit('订阅中心返回了无效结果。')
    if any(response.get(key) is not True for key in ('ok', 'registered', 'subscription_refreshed')):
        raise SystemExit('订阅中心没有确认注册及订阅刷新。')
    if not response.get('host_token'):
        raise SystemExit('订阅中心响应缺少主机令牌。')
    return response


def local_api_for(role, api_base):
    if role in ('center', 'center-relay') and CENTER_CFG.exists():
        center = read(CENTER_CFG, {}) or {}
        return f"http://127.0.0.1:{int(center.get('listen_port') or 18081)}"
    return api_base.rstrip('/')


def save_registration(role, public_api, api_base, response, method):
    current = time.time()
    obj = {
        'schema': 5,
        'api_base_url': public_api,
        'effective_api_base_url': api_base,
        'center_ip': urlparse(public_api).hostname,
        'host_id': stable_id(),
        'host_token': response['host_token'],
        'role': role,
        'registered_at': current,
        'last_sync': current,
        'last_result': response,
        'subscription_url': response.get('subscription_url', ''),
        'registration_method': method,
    }
    atomic(CFG, obj)
    return response


def register(code, role):
    value = ''.join(str(code or '').split())
    host_id = stable_id()
    payload = {'host_id': host_id, 'role': role, 'hostname': socket.gethostname()}
    payload.update(snapshot_payload(role))
    if value.startswith('JPR3.'):
        _jpr, bootstrap = decode_jpr3(value)
        public_api = str(bootstrap['api_base_url']).rstrip('/')
        api_base = local_api_for(role, public_api)
        payload.update(relay_id=bootstrap['relay_id'], registration_token=bootstrap['registration_token'])
        response = require_registration_success(post(api_base + '/api/v1/register-ticket', '', payload))
        return save_registration(role, public_api, api_base, response, 'JPR3-ticket')
    decoded = decode_vvc1(value)
    public_api = str(decoded['api_base_url']).rstrip('/')
    api_base = local_api_for(role, public_api)
    response = require_registration_success(post(api_base + '/api/v1/register', decoded['master_token'], payload))
    return save_registration(role, public_api, api_base, response, 'VVC1')


def sync():
    cfg = read(CFG)
    if not cfg:
        raise SystemExit('尚未配置订阅同步。')
    role = str(cfg.get('role') or 'direct')
    payload = {'host_id': cfg['host_id']}
    payload.update(snapshot_payload(role))
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


def request_relay_ticket(relay_id):
    relay_id = str(relay_id or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,128}', relay_id):
        raise SystemExit('线路 ID 无效。')
    cfg = read(CFG)
    if not cfg:
        raise SystemExit('中转主机尚未注册订阅中心，无法生成一码注册票据。')
    try:
        sync()
    except Exception as exc:
        raise SystemExit(f'生成 JPR3 前同步中转线路失败：{exc}') from exc
    cfg = read(CFG) or cfg
    api_base = str(cfg.get('effective_api_base_url') or cfg.get('api_base_url') or '').rstrip('/')
    response = post(api_base + '/api/v1/relay-ticket', cfg['host_token'], {
        'host_id': cfg['host_id'], 'relay_id': relay_id,
    })
    bootstrap = response.get('subscription_bootstrap') if isinstance(response, dict) else None
    if not isinstance(bootstrap, dict):
        raise SystemExit('订阅中心没有返回副机注册票据。')
    print(json.dumps(bootstrap, ensure_ascii=False, separators=(',', ':')))


def update_center_ip(new_ip):
    try:
        ip = ipaddress.ip_address(new_ip)
    except ValueError as exc:
        raise SystemExit('请输入有效的订阅中心 IP 地址。') from exc
    if ip.version != 4 or ip.is_unspecified:
        raise SystemExit('当前只支持有效 IPv4。')
    cfg = read(CFG)
    if not cfg:
        raise SystemExit('尚未注册订阅中心。')
    old = json.loads(json.dumps(cfg))
    parsed = urlparse(str(cfg.get('api_base_url') or ''))
    port = parsed.port or 18081
    cfg['center_ip'] = str(ip)
    cfg['api_base_url'] = f'http://{ip}:{port}'
    if not str(cfg.get('effective_api_base_url') or '').startswith('http://127.0.0.1:'):
        cfg['effective_api_base_url'] = cfg['api_base_url']
    atomic(CFG, cfg)
    try:
        sync()
    except Exception:
        atomic(CFG, old)
        raise
    return cfg


def validate_code(code):
    value = ''.join(str(code or '').split())
    if value.startswith('JPR3.'):
        obj, bootstrap = decode_jpr3(value)
        return {'type': 'JPR3', 'relay_id': obj['relay_id'], 'subscription_bootstrap': bootstrap}
    return {'type': 'VVC1', 'subscription_center': decode_vvc1(value)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='cmd', required=True)
    register_cmd = commands.add_parser('register')
    register_cmd.add_argument('code')
    register_cmd.add_argument('role', choices=['center-relay', 'center', 'relay', 'direct', 'landing', 'landing-direct'])
    commands.add_parser('sync')
    update_cmd = commands.add_parser('update-center-ip'); update_cmd.add_argument('ip')
    validate_cmd = commands.add_parser('validate-code'); validate_cmd.add_argument('code')
    ticket_cmd = commands.add_parser('relay-ticket'); ticket_cmd.add_argument('relay_id')
    args = parser.parse_args()
    if args.cmd == 'register':
        print(json.dumps(register(args.code, args.role), ensure_ascii=False))
    elif args.cmd == 'sync':
        sync()
    elif args.cmd == 'update-center-ip':
        print(json.dumps(update_center_ip(args.ip), ensure_ascii=False))
    elif args.cmd == 'relay-ticket':
        request_relay_ticket(args.relay_id)
    else:
        print(json.dumps(validate_code(args.code), ensure_ascii=False))

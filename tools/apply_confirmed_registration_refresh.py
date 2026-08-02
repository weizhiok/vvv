#!/usr/bin/env python3
from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding='utf-8')
    if text.count(old) != 1:
        raise SystemExit(f'{path}: expected one target, found {text.count(old)}')
    file.write_text(text.replace(old, new, 1), encoding='utf-8')


# Client: registration request includes the current node state. A green success
# message is allowed only after the center confirms registration + regeneration.
replace_once(
    'core-src/sync_agent.py',
    '''def api_base(cfg):
    return (cfg.get('api_base_url') or cfg['base_url']).rstrip('/')


def register(code, role):
    decoded = decode_code(code)
    public_base = decoded['base_url'].rstrip('/')
    internal_base = local_api_for(role, public_base)
    master = decoded['master_token']
    host_id = stable_id()
    response = post(internal_base + '/api/v1/register', master, {
        'host_id': host_id,
        'role': role,
        'hostname': socket.gethostname(),
    })
    cfg = {
        'schema': 2,
        'base_url': public_base,
        'api_base_url': internal_base,
        'host_id': host_id,
        'host_token': response['host_token'],
        'role': role,
        'registered_at': time.time(),
    }
    atomic(CFG, cfg)
    return cfg


def register_direct(center_address):
    public_base = normalize_center_address(center_address)
    state = read(MAIN_STATE, {}) or {}
    public_ip = str(state.get('public_ip') or '').strip()
    if not public_ip:
        raise SystemExit('本机代理状态缺少公网 IP，无法自动注册。')
    host_id = stable_id()
    response = post(public_base + '/api/v1/register-direct', '', {
        'host_id': host_id,
        'role': 'direct',
        'hostname': socket.gethostname(),
        'public_ip': public_ip,
    })
    cfg = {
        'schema': 2,
        'base_url': public_base,
        'api_base_url': public_base,
        'host_id': host_id,
        'host_token': response['host_token'],
        'role': 'direct',
        'registered_at': time.time(),
        'registration_method': 'center-address',
    }
    atomic(CFG, cfg)
    return cfg
''',
    '''def api_base(cfg):
    return (cfg.get('api_base_url') or cfg['base_url']).rstrip('/')


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
    required = ('ok', 'registered', 'subscription_refreshed')
    if any(response.get(key) is not True for key in required):
        raise SystemExit('订阅中心未返回完整的注册成功标识，未确认订阅刷新。')
    if not response.get('host_token'):
        raise SystemExit('订阅中心注册响应缺少主机令牌。')
    return response


def register(code, role):
    decoded = decode_code(code)
    public_base = decoded['base_url'].rstrip('/')
    internal_base = local_api_for(role, public_base)
    master = decoded['master_token']
    host_id = stable_id()
    payload = {
        'host_id': host_id,
        'role': role,
        'hostname': socket.gethostname(),
    }
    payload.update(snapshot_payload(role))
    response = require_registration_success(
        post(internal_base + '/api/v1/register', master, payload)
    )
    now = time.time()
    cfg = {
        'schema': 2,
        'base_url': public_base,
        'api_base_url': internal_base,
        'host_id': host_id,
        'host_token': response['host_token'],
        'role': role,
        'registered_at': now,
        'last_sync': now,
        'last_result': response,
    }
    atomic(CFG, cfg)
    return response


def register_direct(center_address):
    public_base = normalize_center_address(center_address)
    state = read(MAIN_STATE, {}) or {}
    public_ip = str(state.get('public_ip') or '').strip()
    if not public_ip:
        raise SystemExit('本机代理状态缺少公网 IP，无法自动注册。')
    host_id = stable_id()
    payload = {
        'host_id': host_id,
        'role': 'direct',
        'hostname': socket.gethostname(),
        'public_ip': public_ip,
    }
    payload.update(snapshot_payload('direct'))
    response = require_registration_success(
        post(public_base + '/api/v1/register-direct', '', payload)
    )
    now = time.time()
    cfg = {
        'schema': 2,
        'base_url': public_base,
        'api_base_url': public_base,
        'host_id': host_id,
        'host_token': response['host_token'],
        'role': 'direct',
        'registered_at': now,
        'last_sync': now,
        'last_result': response,
        'registration_method': 'center-address',
    }
    atomic(CFG, cfg)
    return response
''',
)

replace_once(
    'core-src/sync_agent.py',
    '''    path = state_path(cfg.get('role', 'direct'))
    state = read(path, {})
    response = post(api_base(cfg) + '/api/v1/sync', cfg['host_token'], {
        'host_id': cfg['host_id'],
        'state': state,
        'meta': {
            'hostname': socket.gethostname(),
            'role': cfg['role'],
            'timestamp': time.time(),
            'xray_active': os.system('systemctl is-active --quiet xray') == 0,
            'sing_box_active': os.system('systemctl is-active --quiet sing-box') == 0,
        },
    })
''',
    '''    payload = {'host_id': cfg['host_id']}
    payload.update(snapshot_payload(cfg.get('role', 'direct')))
    response = post(api_base(cfg) + '/api/v1/sync', cfg['host_token'], payload)
''',
)

replace_once(
    'core-src/sync_agent.py',
    '''    if args.cmd == 'register':
        register(args.code, args.role)
        sync()
    elif args.cmd == 'register-direct':
        register_direct(args.center_address)
        sync()
    else:
        sync()
''',
    '''    if args.cmd == 'register':
        print(json.dumps(register(args.code, args.role), ensure_ascii=False))
    elif args.cmd == 'register-direct':
        print(json.dumps(register_direct(args.center_address), ensure_ascii=False))
    else:
        sync()
''',
)

# Center: write the first snapshot and regenerate every subscription before it
# returns the success contract to either direct or pairing-code registration.
replace_once(
    'core-src/sub_center.py',
    '''def request_ip(handler):
    forwarded=handler.headers.get('X-Forwarded-For','').split(',')[0].strip()
    candidate=forwarded or handler.client_address[0]
    try: return ipaddress.ip_address(candidate)
    except ValueError: return None


class Handler(BaseHTTPRequestHandler):
''',
    '''def request_ip(handler):
    forwarded=handler.headers.get('X-Forwarded-For','').split(',')[0].strip()
    candidate=forwarded or handler.client_address[0]
    try: return ipaddress.ip_address(candidate)
    except ValueError: return None


def finalize_registration(entry, body):
    doc={
        'host_id':entry['host_id'],
        'role':entry.get('role','direct'),
        'state':body.get('state') or {},
        'meta':body.get('meta') or {},
        'last_seen':now(),
        'last_seen_ts':time.time(),
    }
    atomic_json(HOSTS/f"{entry['host_id']}.json",doc)
    count=regenerate()
    return {
        'ok':True,
        'registered':True,
        'subscription_refreshed':True,
        'node_count':count,
        'host_id':entry['host_id'],
        'host_token':entry['token'],
    }


class Handler(BaseHTTPRequestHandler):
''',
)

replace_once(
    'core-src/sub_center.py',
    '''                entry.update(role='direct',hostname=str(body.get('hostname') or ''),auto_registered=True,source_ip=str(source_ip),updated_at=now()); atomic_json(REGISTRY,registry); backup('after-host-register')
                return self.send_bytes(200,json.dumps({'host_id':host_id,'host_token':entry['token']},ensure_ascii=False).encode(),'application/json')
''',
    '''                entry.update(role='direct',hostname=str(body.get('hostname') or ''),auto_registered=True,source_ip=str(source_ip),updated_at=now()); atomic_json(REGISTRY,registry)
                result=finalize_registration(entry,body); backup('after-host-register')
                return self.send_bytes(200,json.dumps(result,ensure_ascii=False).encode(),'application/json')
''',
)

replace_once(
    'core-src/sub_center.py',
    '''                entry.update(role=role,hostname=str(body.get('hostname') or ''),updated_at=now()); atomic_json(REGISTRY,registry); backup('after-host-register')
                return self.send_bytes(200,json.dumps({'host_id':host_id,'host_token':entry['token']},ensure_ascii=False).encode(),'application/json')
''',
    '''                entry.update(role=role,hostname=str(body.get('hostname') or ''),updated_at=now()); atomic_json(REGISTRY,registry)
                result=finalize_registration(entry,body); backup('after-host-register')
                return self.send_bytes(200,json.dumps(result,ensure_ascii=False).encode(),'application/json')
''',
)

# Shell: only print green after the Python client has verified the full server
# success contract. Blank optional registration still prints no false success.
replace_once(
    'core-src/register_sync.sh',
    '''if [[ -n "$code" ]]; then
  python3 /usr/local/lib/vvv/sync_agent.py register "$code" "$role"
elif [[ "$role" == direct && -n "$center_address" ]]; then
  python3 /usr/local/lib/vvv/sync_agent.py register-direct "$center_address"
fi
''',
    '''registered=0
registration_result=""
if [[ -n "$code" ]]; then
  registration_result="$(python3 /usr/local/lib/vvv/sync_agent.py register "$code" "$role")"
  registered=1
elif [[ "$role" == direct && -n "$center_address" ]]; then
  registration_result="$(python3 /usr/local/lib/vvv/sync_agent.py register-direct "$center_address")"
  registered=1
fi
if (( registered == 1 )); then
  printf '\033[32m订阅中心注册成功\033[0m\n'
fi
''',
)

# Permanent contract and dynamic regeneration test.
replace_once(
    'tests/conformance.py',
    '''import importlib.util
from pathlib import Path
''',
    '''import importlib.util
import tempfile
from pathlib import Path
''',
)

replace_once(
    'tests/conformance.py',
    '''    require(manager.count('注册或更换订阅中心') == 1 and "act[$n]=register" in manager, '已注册后不能更换订阅中心')


def test_subscription_renderers():
''',
    '''    require(manager.count('注册或更换订阅中心') == 1 and "act[$n]=register" in manager, '已注册后不能更换订阅中心')
    require("printf '\\\\033[32m订阅中心注册成功\\\\033[0m\\\\n'" in register, 'SSH 没有绿色订阅中心注册成功提示')
    for token in ('require_registration_success', "'registered'", "'subscription_refreshed'", 'snapshot_payload'):
        require(token in sync, f'客户端没有验证注册刷新成功标识：{token}')
    for token in ('def finalize_registration', "'registered':True", "'subscription_refreshed':True", 'count=regenerate()'):
        require(token in center, f'订阅中心没有在注册响应前刷新订阅：{token}')


def test_registration_refresh_contract():
    module = load_sub_center()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        module.HOSTS = root / 'hosts'
        module.OUT = root / 'output'
        module.HOSTS.mkdir(parents=True)
        module.OUT.mkdir(parents=True)
        calls = []
        module.regenerate = lambda: calls.append('refresh') or 2
        entry = {'host_id': 'confirmed-host-001', 'token': 'host-token', 'role': 'direct'}
        result = module.finalize_registration(entry, {
            'state': sample_host_state(),
            'meta': {'hostname': 'direct-node', 'role': 'direct'},
        })
        require(calls == ['refresh'], '注册没有在返回前刷新订阅文件')
        require(result.get('ok') is True and result.get('registered') is True, '注册响应缺少成功标识')
        require(result.get('subscription_refreshed') is True, '注册响应没有确认订阅已刷新')
        require(result.get('node_count') == 2 and result.get('host_token') == 'host-token', '注册响应内容不完整')
        saved = module.read_json(module.HOSTS / 'confirmed-host-001.json', {})
        require(saved.get('state', {}).get('public_ip') == '198.51.100.10', '注册时没有保存首份节点状态')


def test_subscription_renderers():
''',
)

replace_once(
    'tests/conformance.py',
    '''        test_direct_address_registration,
        test_subscription_renderers,
''',
    '''        test_direct_address_registration,
        test_registration_refresh_contract,
        test_subscription_renderers,
''',
)

replace_once(
    'README.md',
    '''- 直连副机注册时只需输入订阅中心 IP 地址或域名，默认使用 HTTPS 8443；首次留空后，可随时输入 `vps` 补注册；
''',
    '''- 直连副机注册时只需输入订阅中心 IP 地址或域名，默认使用 HTTPS 8443；首次留空后，可随时输入 `vps` 补注册；
- 直连或中转注册只有在订阅中心接收首份节点状态并重新生成订阅后才算成功，SSH 会以绿色显示“订阅中心注册成功”；
''',
)

print('CONFIRMED REGISTRATION REFRESH PATCH APPLIED')

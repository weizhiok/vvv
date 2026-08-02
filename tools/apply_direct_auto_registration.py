#!/usr/bin/env python3
from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding='utf-8')
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{path}: expected one match, found {count}')
    file.write_text(text.replace(old, new, 1), encoding='utf-8')


# 1. Fix the set -u crash and change direct installation to address-only enrollment.
replace_once(
    'core-src/bootstrap.sh',
    '''ask_code(){
  local __var=$1 prompt=$2 value
  read -r -p "$prompt（直接回车表示暂不注册）：" value
  printf -v "$__var" '%s' "$value"
}

ask_proxy_parameters(){
''',
    '''ask_code(){
  local __var=$1 prompt=$2 value
  read -r -p "$prompt（直接回车表示暂不注册）：" value
  printf -v "$__var" '%s' "$value"
}

ask_center_address(){
  local __var=$1 value
  read -r -p "请输入订阅中心 IP 地址或域名（直接回车暂不注册，默认 HTTPS 端口 8443）：" value
  value="${value//[[:space:]]/}"
  printf -v "$__var" '%s' "$value"
}

ask_required_jpr3(){
  while true; do
    read -r -p "请输入完整 JPR3 对接密钥（中转模式必填）：" key
    key="${key//[[:space:]]/}"
    if [[ -z "$key" ]]; then
      echo "中转模式必须输入 JPR3 对接密钥，不能跳过。"
      continue
    fi
    if [[ "$key" != JPR3.* ]]; then
      echo "对接密钥格式错误，必须以 JPR3. 开头。"
      continue
    fi
    break
  done
}

ask_proxy_parameters(){
''',
)

replace_once(
    'core-src/bootstrap.sh',
    '''register_current_main_role(){
  local supplied_code="${1:-}" role code="$supplied_code"
  role="$(primary_role)"
''',
    '''register_current_main_role(){
  local supplied_code role code
  supplied_code="${1:-}"
  role="$(primary_role)"
  code="$supplied_code"
''',
)

replace_once(
    'core-src/bootstrap.sh',
    '''    elif [[ "$choice" == 3 || "$choice" == 5 ]]; then
      [[ -n "$code" ]] && echo "订阅中心接入码：已填写或将使用本机订阅中心" || echo "订阅中心接入码：未填写（独立使用）"
    fi
''',
    '''    elif [[ "$choice" == 3 ]]; then
      [[ -n "$code" ]] && echo "订阅中心接入码：已填写或将使用本机订阅中心" || echo "订阅中心接入码：未填写（独立使用）"
    elif [[ "$choice" == 5 ]]; then
      [[ -n "$center_address" ]] && echo "订阅中心地址：$center_address（自动注册直连节点）" || echo "订阅中心地址：未填写（本次暂不注册）"
    fi
''',
)

replace_once(
    'core-src/bootstrap.sh',
    '''REUSE_PROXY=0
REUSE_CENTER=0
code=""
key=""
''',
    '''REUSE_PROXY=0
REUSE_CENTER=0
code=""
key=""
center_address=""
''',
)

replace_once(
    'core-src/bootstrap.sh',
    '''  3|5)
    if center_complete; then
      code="$(cat /etc/vvv-sub/registration.code)"
    else
      ask_code code "请输入订阅中心接入码"
    fi
    ;;
  4)
    read -r -p "请输入完整 JPR3 对接密钥：" key
    [[ "$key" == JPR3.* ]] || fail "对接密钥必须以 JPR3. 开头。"
    ;;
''',
    '''  3)
    if center_complete; then
      code="$(cat /etc/vvv-sub/registration.code)"
    else
      ask_code code "请输入订阅中心接入码"
    fi
    ;;
  4)
    ask_required_jpr3
    ;;
  5)
    if center_complete; then
      center_address="本机订阅中心"
    else
      ask_center_address center_address
    fi
    ;;
''',
)

replace_once(
    'core-src/bootstrap.sh',
    '''  5)
    ensure_host
    rebuild_roles_from_system
    register_current_main_role "$code"
    ;;
''',
    '''  5)
    ensure_host
    rebuild_roles_from_system
    if center_complete; then
      register_current_main_role
    else
      bash "$BASE_DIR/register_sync.sh" direct "" "$center_address"
    fi
    ;;
''',
)

# 2. Let register_sync select secure-code registration or direct address enrollment.
replace_once(
    'core-src/register_sync.sh',
    '''role="${1:?role}"; code="${2:-}"
install -d -m700 /etc/vvv /usr/local/lib/vvv
''',
    '''role="${1:?role}"
code="${2:-}"
center_address="${3:-}"
install -d -m700 /etc/vvv /usr/local/lib/vvv
''',
)

replace_once(
    'core-src/register_sync.sh',
    '''if [[ -n "$code" ]]; then
  python3 /usr/local/lib/vvv/sync_agent.py register "$code" "$role"
fi
''',
    '''if [[ -n "$code" ]]; then
  python3 /usr/local/lib/vvv/sync_agent.py register "$code" "$role"
elif [[ "$role" == direct && -n "$center_address" ]]; then
  python3 /usr/local/lib/vvv/sync_agent.py register-direct "$center_address"
fi
''',
)

replace_once(
    'core-src/register_sync.sh',
    '''else
  systemctl disable --now vvv-sync.timer vvv-sync.path >/dev/null 2>&1 || true
  echo "未提供订阅中心接入码；以后可在 vps 菜单中注册。"
fi
''',
    '''else
  systemctl disable --now vvv-sync.timer vvv-sync.path >/dev/null 2>&1 || true
  if [[ "$role" == direct ]]; then
    echo "本次未填写订阅中心地址；以后输入 vps，可只填写 IP 地址或域名完成注册。"
  else
    echo "未提供订阅中心接入码；以后可在 vps 菜单中注册。"
  fi
fi
''',
)

# 3. Add address normalization and the direct-only public enrollment client.
replace_once(
    'core-src/sync_agent.py',
    '''from urllib.request import Request, urlopen
''',
    '''from urllib.parse import urlparse
from urllib.request import Request, urlopen
''',
)

replace_once(
    'core-src/sync_agent.py',
    '''def post(url, token, obj):
    data = json.dumps(obj, ensure_ascii=False).encode()
    req = Request(url, data=data, method='POST', headers={
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + token,
        'User-Agent': 'VVV-Sync/2.0',
    })
''',
    '''def post(url, token, obj):
    data = json.dumps(obj, ensure_ascii=False).encode()
    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'VVV-Sync/2.0',
    }
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = Request(url, data=data, method='POST', headers=headers)
''',
)

replace_once(
    'core-src/sync_agent.py',
    '''def stable_id():
''',
    '''def normalize_center_address(value):
    value = str(value or '').strip()
    if not value:
        raise SystemExit('订阅中心地址不能为空。')
    if '://' not in value:
        value = 'https://' + value
    parsed = urlparse(value)
    if parsed.scheme.lower() != 'https' or not parsed.hostname:
        raise SystemExit('订阅中心地址格式错误，请输入 IP、域名、IP:端口或域名:端口。')
    try:
        port = parsed.port or 8443
    except ValueError as exc:
        raise SystemExit('订阅中心端口格式错误。') from exc
    if not (1 <= port <= 65535):
        raise SystemExit('订阅中心端口必须是 1-65535。')
    host = parsed.hostname
    if ':' in host and not host.startswith('['):
        host = '[' + host + ']'
    return f'https://{host}:{port}'


def stable_id():
''',
)

replace_once(
    'core-src/sync_agent.py',
    '''def sync():
''',
    '''def register_direct(center_address):
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


def sync():
''',
)

replace_once(
    'core-src/sync_agent.py',
    '''    register_cmd = commands.add_parser('register')
    register_cmd.add_argument('code')
    register_cmd.add_argument('role', choices=['center-relay', 'center', 'relay', 'direct', 'landing'])
    commands.add_parser('sync')
    args = parser.parse_args()
    if args.cmd == 'register':
        register(args.code, args.role)
        sync()
    else:
        sync()
''',
    '''    register_cmd = commands.add_parser('register')
    register_cmd.add_argument('code')
    register_cmd.add_argument('role', choices=['center-relay', 'center', 'relay', 'direct', 'landing'])
    direct_cmd = commands.add_parser('register-direct')
    direct_cmd.add_argument('center_address')
    commands.add_parser('sync')
    args = parser.parse_args()
    if args.cmd == 'register':
        register(args.code, args.role)
        sync()
    elif args.cmd == 'register-direct':
        register_direct(args.center_address)
        sync()
    else:
        sync()
''',
)

# 4. Add the center-side direct-only endpoint. It binds IPv4 clients to the public IP in their local state.
replace_once(
    'core-src/sub_center.py',
    '''import hashlib
import json
''',
    '''import hashlib
import ipaddress
import json
''',
)

replace_once(
    'core-src/sub_center.py',
    '''def auth_token(handler):
    value=handler.headers.get('Authorization','')
    return value[7:] if value.startswith('Bearer ') else ''


class Handler(BaseHTTPRequestHandler):
''',
    '''def auth_token(handler):
    value=handler.headers.get('Authorization','')
    return value[7:] if value.startswith('Bearer ') else ''


def request_ip(handler):
    forwarded=handler.headers.get('X-Forwarded-For','').split(',')[0].strip()
    candidate=forwarded or handler.client_address[0]
    try: return ipaddress.ip_address(candidate)
    except ValueError: return None


class Handler(BaseHTTPRequestHandler):
''',
)

replace_once(
    'core-src/sub_center.py',
    '''            registry=read_json(REGISTRY,{'hosts':[]}) or {'hosts':[]}
            if path=='/api/v1/register':
''',
    '''            registry=read_json(REGISTRY,{'hosts':[]}) or {'hosts':[]}
            if path=='/api/v1/register-direct':
                host_id=str(body.get('host_id') or '').strip(); role=str(body.get('role') or '')
                if role!='direct': return self.send_bytes(400,b'Direct role required\\n')
                if not re.fullmatch(r'[A-Za-z0-9._-]{8,128}',host_id): return self.send_bytes(400,b'Bad host id\\n')
                try: declared_ip=ipaddress.ip_address(str(body.get('public_ip') or '').strip())
                except ValueError: return self.send_bytes(400,b'Bad public ip\\n')
                source_ip=request_ip(self)
                if source_ip is None or not declared_ip.is_global: return self.send_bytes(403,b'Public source required\\n')
                if source_ip.version==declared_ip.version and source_ip!=declared_ip: return self.send_bytes(403,b'Source IP mismatch\\n')
                backup('before-host-register'); entry=next((x for x in registry['hosts'] if x['host_id']==host_id),None)
                if entry is None: entry={'host_id':host_id,'token':secrets.token_urlsafe(32),'created_at':now()}; registry['hosts'].append(entry)
                entry.update(role='direct',hostname=str(body.get('hostname') or ''),auto_registered=True,source_ip=str(source_ip),updated_at=now()); atomic_json(REGISTRY,registry); backup('after-host-register')
                return self.send_bytes(200,json.dumps({'host_id':host_id,'host_token':entry['token']},ensure_ascii=False).encode(),'application/json')
            if path=='/api/v1/register':
''',
)

# 5. Let the vps menu register direct hosts by address and always retain the re-register action.
replace_once(
    'core-src/vvv_manager.sh',
    '''register_center(){
  read -r -p "请输入 VVV 主机接入码：" code
  [[ -n $code ]] || { echo "接入码不能为空。"; return; }
  /usr/local/lib/vvv/register_sync.sh "$(primary)" "$code"
}
''',
    '''register_center(){
  local current address code
  current="$(primary)"
  if [[ "$current" == direct ]]; then
    read -r -p "请输入订阅中心 IP 地址或域名（默认 HTTPS 端口 8443）：" address
    address="${address//[[:space:]]/}"
    [[ -n "$address" ]] || { echo "订阅中心地址不能为空。"; return; }
    /usr/local/lib/vvv/register_sync.sh direct "" "$address"
  else
    read -r -p "请输入 VVV 主机接入码：" code
    [[ -n "$code" ]] || { echo "接入码不能为空。"; return; }
    /usr/local/lib/vvv/register_sync.sh "$current" "$code"
  fi
}
''',
)

replace_once(
    'core-src/vvv_manager.sh',
    '''  if [[ -f /etc/vvv/client.json ]]; then
    echo "$n. 立即同步订阅"; act[$n]=sync; ((n++))
    echo "$n. 查看订阅同步状态"; act[$n]=status; ((n++))
  else
    echo "$n. 注册或更换订阅中心"; act[$n]=register; ((n++))
  fi
''',
    '''  if [[ -f /etc/vvv/client.json ]]; then
    echo "$n. 立即同步订阅"; act[$n]=sync; ((n++))
    echo "$n. 查看订阅同步状态"; act[$n]=status; ((n++))
  fi
  echo "$n. 注册或更换订阅中心"; act[$n]=register; ((n++))
''',
)

# 6. Permanent regression coverage.
replace_once(
    'tests/conformance.py',
    '''def test_subscription_renderers():
''',
    '''def test_direct_address_registration():
    bootstrap = read('core-src/bootstrap.sh')
    register = read('core-src/register_sync.sh')
    sync = read('core-src/sync_agent.py')
    center = read('core-src/sub_center.py')
    manager = read('core-src/vvv_manager.sh')
    require('local supplied_code role code' in bootstrap, '最终注册仍可能在 local 同一行提前展开未赋值变量')
    require('local supplied_code="${1:-}" role code="$supplied_code"' not in bootstrap, '仍保留 supplied_code 未绑定崩溃写法')
    require('ask_center_address' in bootstrap and '默认 HTTPS 端口 8443' in bootstrap, '直连安装没有只询问订阅中心地址')
    require('ask_required_jpr3' in bootstrap and '中转模式必须输入 JPR3 对接密钥' in bootstrap, '中转副机仍可跳过对接码')
    require('register-direct "$center_address"' in register, '直连地址没有传给自动注册客户端')
    for token in ('normalize_center_address', "'/api/v1/register-direct'", "registration_method': 'center-address'", "commands.add_parser('register-direct')"):
        require(token in sync, f'直连地址注册客户端缺少：{token}')
    for token in ("path=='/api/v1/register-direct'", "role!='direct'", 'Source IP mismatch', 'auto_registered=True'):
        require(token in center, f'订阅中心直连自动注册缺少：{token}')
    require('if [[ "$current" == direct ]]' in manager and '请输入订阅中心 IP 地址或域名' in manager, 'vps 菜单不能按地址补注册直连副机')
    require(manager.count('注册或更换订阅中心') == 1 and "act[$n]=register" in manager, '已注册后不能更换订阅中心')


def test_subscription_renderers():
''',
)

replace_once(
    'tests/conformance.py',
    '''    tests = [
        test_menu_and_front_loaded_parameters,
        test_subscription_renderers,
''',
    '''    tests = [
        test_menu_and_front_loaded_parameters,
        test_direct_address_registration,
        test_subscription_renderers,
''',
)

# 7. Document the two distinct registration rules.
replace_once(
    'README.md',
    '''- 后续选择新的角色时，只追加缺少的模块，并自动合并最终角色。例如先安装菜单 2，再运行菜单 3，最终会成为“订阅中心 + 中转主机 + 自身代理”；
''',
    '''- 后续选择新的角色时，只追加缺少的模块，并自动合并最终角色。例如先安装菜单 2，再运行菜单 3，最终会成为“订阅中心 + 中转主机 + 自身代理”；
- 直连副机注册时只需输入订阅中心 IP 地址或域名，默认使用 HTTPS 8443；首次留空后，可随时输入 `vps` 补注册；
- 中转副机必须输入完整 JPR3 对接密钥，留空或格式错误都不会开始安装；
''',
)

print('DIRECT ADDRESS REGISTRATION PATCH APPLIED')

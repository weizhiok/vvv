#!/usr/bin/env python3
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


def write(path, content):
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected exactly one anchor, found {count}')
    return text.replace(old, new, 1)


def regex_once(text, pattern, replacement, label):
    result, count = re.subn(pattern, replacement, text, count=1, flags=re.M | re.S)
    if count != 1:
        raise SystemExit(f'{label}: expected exactly one regex match, found {count}')
    return result


SYNC_AGENT = r'''#!/usr/bin/env python3
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
        raise ValueError('订阅中心 API 必须使用 IP 地址。') from exc


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
'''


REGISTER_SYNC = r'''#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
role="${1:?role}"
code="${2:-}"
install -d -m700 /etc/vvv /usr/local/lib/vvv
install -m755 "$BASE_DIR/sync_agent.py" /usr/local/lib/vvv/sync_agent.py

if [[ -n "$code" ]]; then
  python3 /usr/local/lib/vvv/sync_agent.py register "$code" "$role" >/dev/null
  printf '\033[32m订阅中心注册成功\033[0m\n'
fi

cat > /etc/systemd/system/vvv-sync.service <<'UNIT'
[Unit]
Description=VVV Node Snapshot Sync
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/lib/vvv/sync_agent.py sync
UNIT

cat > /etc/systemd/system/vvv-sync.timer <<'UNIT'
[Unit]
Description=VVV node heartbeat
[Timer]
OnBootSec=3min
OnUnitActiveSec=30min
RandomizedDelaySec=60
Persistent=true
[Install]
WantedBy=timers.target
UNIT

{
  echo '[Unit]'
  echo 'Description=Watch VVV node state changes'
  echo '[Path]'
  case "$role" in
    landing-direct)
      echo 'PathChanged=/etc/jp-relay/state.json'
      echo 'PathChanged=/etc/jp-relay/landing-state.json'
      ;;
    landing) echo 'PathChanged=/etc/jp-relay/landing-state.json' ;;
    *) echo 'PathChanged=/etc/jp-relay/state.json' ;;
  esac
  echo 'Unit=vvv-sync.service'
  echo '[Install]'
  echo 'WantedBy=multi-user.target'
} > /etc/systemd/system/vvv-sync.path

systemctl daemon-reload
if [[ -f /etc/vvv/client.json ]]; then
  systemctl enable --now vvv-sync.timer vvv-sync.path
  systemctl start vvv-sync.service || true
else
  systemctl disable --now vvv-sync.timer vvv-sync.path >/dev/null 2>&1 || true
  echo "本次未注册订阅中心；以后输入 vps，可粘贴 VVC1 或含注册票据的 JPR3 完成注册。"
fi
'''


VVV_MANAGER = r'''#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ROLE_FILE=/etc/vvv/roles.json
SYNC=/usr/local/lib/vvv/sync_agent.py
DIAG=/usr/local/lib/vvv/diagnostic_report.py
CLIENT_UPGRADE=/usr/local/lib/vvv/client_upgrade_engine.py
CLIENT_RENDERER=/usr/local/lib/vvv/client_local_renderer.py
role_has(){ jq -e --arg k "$1" '.roles[$k]==true' "$ROLE_FILE" >/dev/null 2>&1; }
pause(){ read -r -p "按回车返回……" _; }
show_roles(){
  echo "已安装模块："
  role_has proxy && echo "✓ 本机直连代理" || echo "✗ 本机直连代理"
  role_has center && echo "✓ 订阅中心" || echo "✗ 订阅中心"
  role_has relay && echo "✓ 中转管理" || echo "✗ 中转管理"
  role_has landing && echo "✓ 中转副机" || echo "✗ 中转副机"
}
primary(){ jq -r .primary_role "$ROLE_FILE"; }
register_center(){
  local code
  while true; do
    read -r -p "请输入订阅中心对接码（支持 VVC1 或含注册票据的 JPR3，按回车取消）：" code
    code="${code//[[:space:]]/}"
    [[ -n "$code" ]] || return
    if python3 "$SYNC" validate-code "$code" >/dev/null 2>&1; then break; fi
    echo "对接码无效，请重新输入完整 VVC1 或 JPR3。"
  done
  /usr/local/lib/vvv/register_sync.sh "$(primary)" "$code"
}
show_sync(){
  [[ -f /etc/vvv/client.json ]] && jq '{api_base_url,center_ip,host_id,role,registration_method,registered_at,last_sync,last_result}' /etc/vvv/client.json || echo "尚未注册订阅中心。"
}
update_center_ip(){ local ip; read -r -p "请输入新的订阅中心公网 IPv4：" ip; python3 "$SYNC" update-center-ip "$ip"; }
upgrade_client_support(){
  [[ -x "$CLIENT_UPGRADE" ]] || { echo "客户端支持升级引擎不存在。"; return 1; }
  python3 "$CLIENT_UPGRADE" menu
}
show_local_clients(){
  [[ -x "$CLIENT_RENDERER" ]] || { echo "本机客户端配置生成器不存在。"; return 1; }
  python3 "$CLIENT_RENDERER" regenerate >/dev/null
  python3 "$CLIENT_RENDERER" show
}
landing_manage(){
  if [[ -x /usr/local/sbin/vvv-landing-original ]]; then
    /usr/local/sbin/vvv-landing-original
  elif [[ -x /usr/local/sbin/landing-vps ]]; then
    /usr/local/sbin/landing-vps
  else
    echo "中转副机管理命令不存在。"
  fi
}
[[ -f $ROLE_FILE ]] || { echo "VVV 角色配置不存在。"; exit 1; }
while true; do
  echo; echo "========== VVV 管理 =========="; show_roles; echo
  n=1; declare -A act=()
  if role_has proxy || role_has landing; then echo "$n. 查看本机客户端配置"; act[$n]=local; ((n++)); fi
  if role_has relay; then echo "$n. 中转线路管理"; act[$n]=relay; ((n++)); fi
  if role_has landing; then echo "$n. 中转副机管理"; act[$n]=landing; ((n++)); fi
  if role_has center; then echo "$n. 订阅中心管理"; act[$n]=center; ((n++)); fi
  if [[ -f /etc/vvv/client.json ]]; then
    echo "$n. 立即同步订阅"; act[$n]=sync; ((n++))
    echo "$n. 查看订阅同步状态"; act[$n]=status; ((n++))
    if ! role_has center; then echo "$n. 修改订阅中心 IP 地址"; act[$n]=update_ip; ((n++)); fi
  fi
  echo "$n. 注册或更换订阅中心"; act[$n]=register; ((n++))
  echo "$n. 生成故障诊断报告"; act[$n]=diagnostic; ((n++))
  echo "$n. 升级客户端支持"; act[$n]=client_upgrade; ((n++))
  echo "0. 退出"
  read -r -p "请输入编号：" x
  [[ $x == 0 ]] && exit 0
  case "${act[$x]:-}" in
    local) show_local_clients; pause;;
    relay) /usr/local/sbin/jp-relay-manager --manage;;
    landing) landing_manage;;
    center) /usr/local/sbin/vvv-center;;
    sync) systemctl start vvv-sync.service; show_sync; pause;;
    status) show_sync; systemctl --no-pager status vvv-sync.timer vvv-sync.path 2>/dev/null || true; pause;;
    update_ip) update_center_ip; pause;;
    register) register_center; pause;;
    diagnostic) python3 "$DIAG"; pause;;
    client_upgrade) upgrade_client_support; pause;;
    *) echo "请输入有效编号。";;
  esac
done
'''


STATIC_TEST = r'''#!/usr/bin/env python3
import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / 'core-src'


def require(value, message):
    if not value:
        raise AssertionError(message)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_menu_and_ports():
    text = (CORE / 'bootstrap.sh').read_text(encoding='utf-8')
    expected = [
        '1. 安装订阅中心 + 中转主机 + 自身代理',
        '2. 安装订阅中心 + 自身代理',
        '3. 安装中转主机 + 自身代理',
        '4. 安装中转副机 + 自身代理',
        '5. 安装中转副机',
        '6. 安装直连代理',
        '7. 从云备份恢复',
    ]
    positions = [text.index(item) for item in expected]
    require(positions == sorted(positions), '首次菜单编号或顺序错误')
    host = (CORE / 'host.sh').read_text(encoding='utf-8')
    require('请输入落地统一端口 [默认 ${default_port}]' in host and 'default_port="553"' in host,
            '新建副机线路默认端口不是 553')
    require('"schema":4,"type":"jp-relay-landing"' in host, 'JPR3 没有升级到 schema 4')
    require('subscription_bootstrap' in host and 'relay-ticket' in host, 'JPR3 没有受限订阅注册票据')


def test_landing_isolation():
    landing = (CORE / 'landing.sh').read_text(encoding='utf-8')
    for token in (
        '/etc/vvv-landing/xray/config.json',
        '/etc/vvv-landing/sing-box/config.json',
        '/etc/vvv-landing/sing-box/tls',
        'vvv-landing-xray.service',
        'vvv-landing-sing-box.service',
        'VVV_COMBINED_INSTALL',
    ):
        require(token in landing, f'中转副机隔离缺少 {token}')
    require('/etc/systemd/system/xray.service <<' not in landing, '中转副机仍覆盖直连 Xray 服务')
    require('/etc/systemd/system/sing-box.service <<' not in landing, '中转副机仍覆盖直连 sing-box 服务')


def test_sync_and_names():
    sync = (CORE / 'sync_agent.py').read_text(encoding='utf-8')
    center = (CORE / 'sub_center.py').read_text(encoding='utf-8')
    renderer = (CORE / 'client_local_renderer.py').read_text(encoding='utf-8')
    require('landing-direct' in sync and "'states': {'direct': direct, 'landing': landing}" in sync,
            '组合角色没有同步两套状态')
    require('/api/v1/register-ticket' in center and '/api/v1/relay-ticket' in center,
            '订阅中心缺少受限注册票据端点')
    require('中转-' in center and '中转-' in renderer, '中转节点命名缺少“中转”')
    require("return 'landing-direct', main_contexts" in renderer,
            '本机客户端生成器没有聚合直连和中转配置')


def test_backup_and_protection():
    backup = (CORE / 'backup_manager.py').read_text(encoding='utf-8')
    restore = (CORE / 'restore_manager.py').read_text(encoding='utf-8')
    engine = (CORE / 'client_upgrade_engine.py').read_text(encoding='utf-8')
    require("Path('/etc/vvv-landing')" in backup, '云备份没有包含中转独立配置')
    require("'etc/vvv-landing/'" in restore, '云恢复没有允许中转独立配置')
    for token in ('vvv-landing-xray.service', 'vvv-landing-sing-box.service', '/etc/vvv-landing/xray/config.json'):
        require(token in engine, f'客户端升级保护缺少 {token}')


def test_renderer_aggregation():
    module = load(CORE / 'client_local_renderer.py', 'renderer_test')
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / 'etc/jp-relay').mkdir(parents=True)
        direct = {
            'protocol_mode': 'vless', 'public_ip': '203.0.113.10', 'listen_port': 443,
            'sni': 'www.softbank.jp', 'direct_base_name': 'SG-203.0.113.10:443',
            'vless': {'direct_user': {'uuid': '11111111-1111-4111-8111-111111111111'},
                      'reality': {'public_key': 'pk', 'short_id': '0123456789abcdef'}},
        }
        landing = {
            'protocol_mode': 'vless', 'node_name': 'SG-198.51.100.20:553',
            'japan_public_ip': '192.0.2.10', 'japan_port': 443,
            'remote_public_ip': '198.51.100.20', 'remote_public_port': 553,
            'sni': 'www.softbank.jp',
            'vless': {'japan_client_uuid': '22222222-2222-4222-8222-222222222222',
                      'japan_reality_public_key': 'pk2', 'japan_reality_short_id': 'abcdef0123456789'},
        }
        (root / 'etc/jp-relay/state.json').write_text(json.dumps(direct), encoding='utf-8')
        (root / 'etc/jp-relay/landing-state.json').write_text(json.dumps(landing), encoding='utf-8')
        role, contexts = module.detect_contexts(root)
        require(role == 'landing-direct' and len(contexts) == 2, '组合角色没有生成两组本机客户端配置')
        names = [node['name'] for ctx in contexts for node in ctx['nodes']]
        require(any('VLESS-中转-192.0.2.10:443' in name for name in names), '中转节点名称不符合要求')


def main():
    for test in (test_menu_and_ports, test_landing_isolation, test_sync_and_names,
                 test_backup_and_protection, test_renderer_aggregation):
        test(); print('PASS', test.__name__)
    print('LANDING DIRECT ROLE VALIDATION PASSED')


if __name__ == '__main__':
    main()
'''


def patch_host():
    text = read('core-src/host.sh')
    text = replace_once(text, 'default_port="443"\n  while true; do\n    read -r -p "请输入落地统一端口 [默认 ${default_port}]：" input',
                        'default_port="553"\n  while true; do\n    read -r -p "请输入落地统一端口 [默认 ${default_port}]：" input',
                        'landing default port')
    old = '''make_pairing_key() {
  local state_path="$1" relay_id="$2" registration_code=""
  [[ ! -r /etc/vvv-sub/registration.code ]] || registration_code="$(cat /etc/vvv-sub/registration.code)"
  python3 - "$state_path" "$relay_id" "$registration_code" <<'PY_JPR3' '''.rstrip()
    new = '''make_pairing_key() {
  local state_path="$1" relay_id="$2" subscription_bootstrap="null"
  if [[ -s /etc/vvv/client.json && -x /usr/local/lib/vvv/sync_agent.py ]]; then
    subscription_bootstrap="$(python3 /usr/local/lib/vvv/sync_agent.py relay-ticket "$relay_id" 2>/dev/null || printf 'null')"
  fi
  python3 - "$state_path" "$relay_id" "$subscription_bootstrap" <<'PY_JPR3' '''.rstrip()
    text = replace_once(text, old, new, 'JPR3 ticket producer')
    text = replace_once(text,
                        's=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))\nr=next(x for x in s["relays"] if x["id"]==sys.argv[2])',
                        's=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))\nr=next(x for x in s["relays"] if x["id"]==sys.argv[2])\ntry:\n    subscription_bootstrap=json.loads(sys.argv[3]) if sys.argv[3] else None\nexcept Exception:\n    subscription_bootstrap=None',
                        'JPR3 ticket decode')
    text = replace_once(text,
                        '"schema":3,"type":"jp-relay-landing","protocol_mode":s["protocol_mode"],',
                        '"schema":4,"type":"jp-relay-landing","protocol_mode":s["protocol_mode"],',
                        'JPR3 schema')
    text = replace_once(text,
                        '"vless":None,"hy2":None,"subscription_registration_code":sys.argv[3] or None,',
                        '"vless":None,"hy2":None,"subscription_bootstrap":subscription_bootstrap,',
                        'JPR3 bootstrap field')
    text = replace_once(text,
                        '''elif kind=="relay":
    relay=next(x for x in state.get("relays",[]) if x["id"]==rid)
    base=relay["name"]
    enabled_vless=relay.get("vless") is not None
    enabled_hy2=relay.get("hy2") is not None''',
                        '''elif kind=="relay":
    relay=next(x for x in state.get("relays",[]) if x["id"]==rid)
    raw_name=str(relay.get("name") or "")
    country=raw_name[:2].upper() if len(raw_name)>=3 and raw_name[:2].isalpha() and raw_name[2]=="-" else ""
    base=(country+"-" if country else "")+f"中转-{ip}:{port}"
    enabled_vless=relay.get("vless") is not None
    enabled_hy2=relay.get("hy2") is not None''',
                        'host relay client names')
    write('core-src/host.sh', text)


def patch_landing():
    text = read('core-src/landing.sh')
    text = replace_once(text, 'PAIRING_KEY="${PAIRING_KEY:-}"\nCURRENT_STEP="启动"',
                        'PAIRING_KEY="${PAIRING_KEY:-}"\nCOMBINED_INSTALL="${VVV_COMBINED_INSTALL:-0}"\nCURRENT_STEP="启动"',
                        'combined flag')
    replacements = {
        'XRAY_CFG="/usr/local/etc/xray/config.json"': 'XRAY_CFG="/etc/vvv-landing/xray/config.json"',
        'SING_CFG="/etc/sing-box/config.json"': 'SING_CFG="/etc/vvv-landing/sing-box/config.json"',
        'TLS_DIR="/etc/sing-box/tls"': 'TLS_DIR="/etc/vvv-landing/sing-box/tls"',
        '/usr/local/etc/xray/config.json': '/etc/vvv-landing/xray/config.json',
        '/etc/sing-box/config.json': '/etc/vvv-landing/sing-box/config.json',
        '/etc/systemd/system/xray.service': '/etc/systemd/system/vvv-landing-xray.service',
        '/etc/systemd/system/sing-box.service': '/etc/systemd/system/vvv-landing-sing-box.service',
        'xray.service': 'vvv-landing-xray.service',
        'sing-box.service': 'vvv-landing-sing-box.service',
        'service_stop xray': 'service_stop vvv-landing-xray',
        'service_stop sing-box': 'service_stop vvv-landing-sing-box',
        'service_restart xray': 'service_restart vvv-landing-xray',
        'service_restart sing-box': 'service_restart vvv-landing-sing-box',
        'service_active xray': 'service_active vvv-landing-xray',
        'service_active sing-box': 'service_active vvv-landing-sing-box',
        'systemctl enable xray': 'systemctl enable vvv-landing-xray',
        'systemctl enable sing-box': 'systemctl enable vvv-landing-sing-box',
        'journalctl -u xray -u sing-box': 'journalctl -u vvv-landing-xray -u vvv-landing-sing-box',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = text.replace('install -d -o root -g xray -m 750 /usr/local/etc/xray',
                        'install -d -o root -g xray -m 750 /etc/vvv-landing/xray')
    text = text.replace('install -d -o root -g sing-box -m 750 /etc/sing-box "$TLS_DIR"',
                        'install -d -o root -g sing-box -m 750 /etc/vvv-landing/sing-box "$TLS_DIR"')
    text = text.replace('mkdir -p /usr/local/etc/xray', 'mkdir -p /etc/vvv-landing/xray')
    text = text.replace('mkdir -p /etc/sing-box', 'mkdir -p /etc/vvv-landing/sing-box')
    text = replace_once(text, '.schema==3 and .type=="jp-relay-landing" and',
                        '.schema==4 and .type=="jp-relay-landing" and', 'landing JPR3 schema')
    helper_anchor = '''protocol_name() {
  base="$1"; proto="$2"
  if printf '%s' "$base" | grep -Eq '^[A-Z]{2}-'; then
    country="${base%%-*}"; rest="${base#*-}"
    printf '%s-%s-%s' "$country" "$proto" "$rest"
  elif printf '%s' "$base" | grep -Eq '^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+:[0-9]+$'; then
    printf '%s-%s' "$proto" "$base"
  else
    printf '%s-%s' "$base" "$proto"
  fi
}
'''
    helper_new = helper_anchor + '''
relay_client_base() {
  raw="$1"
  if printf '%s' "$raw" | grep -Eq '^[A-Za-z]{2}-'; then
    country="$(printf '%s' "${raw%%-*}" | tr '[:lower:]' '[:upper:]')"
    printf '%s-中转-%s:%s' "$country" "$JAPAN_PUBLIC_IP" "$JAPAN_PORT"
  else
    printf '中转-%s:%s' "$JAPAN_PUBLIC_IP" "$JAPAN_PORT"
  fi
}
'''
    text = replace_once(text, helper_anchor, helper_new, 'landing relay naming helper')
    text = replace_once(text, 'vless_name="$(protocol_name "$NODE_NAME" VLESS)"',
                        'vless_name="$(protocol_name "$(relay_client_base "$NODE_NAME")" VLESS)"',
                        'landing vless name')
    text = replace_once(text, 'hy2_name="$(protocol_name "$NODE_NAME" HY2)"',
                        'hy2_name="$(protocol_name "$(relay_client_base "$NODE_NAME")" HY2)"',
                        'landing hy2 name')
    reuse_helper = r'''
reuse_installed_cores() {
  mode_has_vless && [ -x "$XRAY" ] || { mode_has_vless && fail "组合安装未找到已安装的 Xray。"; }
  mode_has_hy2 && [ -x "$SING_BOX" ] || { mode_has_hy2 && fail "组合安装未找到已安装的 sing-box。"; }
  if mode_has_vless; then
    XRAY_VERSION="$("$XRAY" version 2>/dev/null | awk 'NR==1{print $2}')"
    XRAY_VERSION_SOURCE="与本机直连代理共享"
  fi
  if mode_has_hy2; then
    SING_BOX_VERSION="$("$SING_BOX" version 2>/dev/null | awk '/sing-box version/{print $3; exit}')"
    SING_BOX_VERSION_SOURCE="与本机直连代理共享"
  fi
}
'''
    text = replace_once(text, '\nCURRENT_STEP="检查 root 权限"', reuse_helper + '\nCURRENT_STEP="检查 root 权限"',
                        'reuse core helper')
    old_system = '''CURRENT_STEP="刷新软件源并安装依赖"
log "$CURRENT_STEP"
upgrade_system_once

CURRENT_STEP="解析并验证 JPR3 对接密钥"
log "$CURRENT_STEP"
normalize_pairing_key
parse_pairing_key

CURRENT_STEP="检测官方最新稳定版"
log "$CURRENT_STEP"
resolve_core_versions

CURRENT_STEP="检查磁盘和内存"
log "$CURRENT_STEP"
check_disk_space
choose_memory_limit

CURRENT_STEP="配置 Swap"
log "$CURRENT_STEP"
configure_swap_if_suitable

CURRENT_STEP="配置 BBR、fq 和 UDP 缓冲区"
log "$CURRENT_STEP"
configure_network_tuning

CURRENT_STEP="设置上海时区和每天 06:00 自动重启"
log "$CURRENT_STEP"
configure_timezone_and_daily_reboot
'''
    new_system = '''CURRENT_STEP="解析并验证 JPR3 对接密钥"
log "$CURRENT_STEP"
normalize_pairing_key
parse_pairing_key

if [ "$COMBINED_INSTALL" = 1 ]; then
  CURRENT_STEP="复用自身直连代理的系统环境和代理核心"
  log "$CURRENT_STEP"
  reuse_installed_cores
  check_disk_space
  choose_memory_limit
  echo "组合安装不重复执行 APT、Swap、BBR、时区、定时重启或代理核心安装。"
else
  CURRENT_STEP="刷新软件源并安装依赖"
  log "$CURRENT_STEP"
  upgrade_system_once
  CURRENT_STEP="检测官方最新稳定版"
  log "$CURRENT_STEP"
  resolve_core_versions
  CURRENT_STEP="检查磁盘和内存"
  log "$CURRENT_STEP"
  check_disk_space
  choose_memory_limit
  CURRENT_STEP="配置 Swap"
  log "$CURRENT_STEP"
  configure_swap_if_suitable
  CURRENT_STEP="配置 BBR、fq 和 UDP 缓冲区"
  log "$CURRENT_STEP"
  configure_network_tuning
  CURRENT_STEP="设置上海时区和每天 06:00 自动重启"
  log "$CURRENT_STEP"
  configure_timezone_and_daily_reboot
fi
'''
    text = replace_once(text, old_system, new_system, 'combined system setup')
    text = replace_once(text,
                        '''  CURRENT_STEP="安装 Xray 最新稳定版"
  log "$CURRENT_STEP"
  install_xray_binary''',
                        '''  if [ "$COMBINED_INSTALL" != 1 ]; then
    CURRENT_STEP="安装 Xray 最新稳定版"
    log "$CURRENT_STEP"
    install_xray_binary
  fi''',
                        'skip xray install')
    text = replace_once(text,
                        '''  CURRENT_STEP="安装 sing-box 最新稳定版"
  log "$CURRENT_STEP"
  install_sing_box_binary''',
                        '''  if [ "$COMBINED_INSTALL" != 1 ]; then
    CURRENT_STEP="安装 sing-box 最新稳定版"
    log "$CURRENT_STEP"
    install_sing_box_binary
  fi''',
                        'skip sing install')
    text = text.replace('if [ "$XRAY_VERSION" != "$XRAY_FALLBACK_VERSION" ]; then',
                        'if [ "$COMBINED_INSTALL" != 1 ] && [ "$XRAY_VERSION" != "$XRAY_FALLBACK_VERSION" ]; then', 1)
    text = text.replace('if [ "$SING_BOX_VERSION" != "$SING_BOX_FALLBACK_VERSION" ]; then',
                        'if [ "$COMBINED_INSTALL" != 1 ] && [ "$SING_BOX_VERSION" != "$SING_BOX_FALLBACK_VERSION" ]; then', 1)
    text = replace_once(text, '''apt-get clean
rm -rf /var/lib/apt/lists/*''',
                        '''if [ "$COMBINED_INSTALL" != 1 ]; then
  apt-get clean
  rm -rf /var/lib/apt/lists/*
fi''', 'skip apt cleanup')
    text = replace_once(text, 'echo "本次没有立即重启服务器，只重启了启用的代理服务。"',
                        'echo "本次没有立即重启服务器；中转服务使用独立进程，不会重启自身直连代理。"\necho "请在 VPS 服务商安全组放行 TCP/UDP ${REMOTE_PUBLIC_PORT}；推荐仅允许日本主机 ${JAPAN_PUBLIC_IP}/32 访问。"',
                        'landing final firewall note')
    write('core-src/landing.sh', text)


def patch_bootstrap():
    text = read('core-src/bootstrap.sh')
    text = regex_once(text, r'^show_install_menu\(\) \{.*?^\}\n', r'''show_install_menu() {
  detect_installed_modules
  echo
  echo "========== VVV 一体化安装管理 =========="
  echo "当前检测到的模块："
  print_mark "$INST_PROXY" "本机直连代理"
  print_mark "$INST_CENTER" "订阅中心"
  print_mark "$INST_RELAY" "中转管理"
  print_mark "$INST_LANDING" "中转副机"
  echo
  echo "1. 安装订阅中心 + 中转主机 + 自身代理"
  echo "2. 安装订阅中心 + 自身代理"
  echo "3. 安装中转主机 + 自身代理"
  echo "4. 安装中转副机 + 自身代理"
  echo "5. 安装中转副机"
  echo "6. 安装直连代理"
  echo "7. 从云备份恢复"
  echo "0. 退出"
}
''', 'install menu')
    text = regex_once(text, r'^ask_optional_vvc1\(\) \{.*?^\}\n', r'''ask_optional_vvc1() {
  local __var="$1" value
  while true; do
    read -r -p "请输入订阅中心对接码（支持 VVC1 或含注册票据的 JPR3；按回车跳过）：" value
    value="${value//[[:space:]]/}"
    if [[ -z "$value" ]]; then printf -v "$__var" '%s' ''; return; fi
    if python3 "$BASE_DIR/sync_agent.py" validate-code "$value" >/dev/null 2>&1; then
      printf -v "$__var" '%s' "$value"; return
    fi
    echo "对接码错误：请输入完整 VVC1、含订阅注册票据的 JPR3，或直接回车跳过。"
  done
}
''', 'registration prompt')
    text = regex_once(text, r'^jpr_registration_code\(\) \{.*?^\}\n', r'''jpr_field() {
  local value="$1" field="$2"
  python3 - "$value" "$field" <<'PY_JPR_FIELD'
import base64,hashlib,json,sys,zlib
parts=''.join(sys.argv[1].split()).split('.')
if len(parts)!=3 or parts[0]!='JPR3': raise SystemExit(1)
data=base64.urlsafe_b64decode(parts[1]+'='*((4-len(parts[1])%4)%4))
if hashlib.sha256(data).hexdigest()[:20] != parts[2]: raise SystemExit(1)
raw=data if data.startswith(b'{') else zlib.decompress(data)
obj=json.loads(raw.decode())
if obj.get('schema') != 4: raise SystemExit(1)
value=obj
for part in sys.argv[2].split('.'):
    value=value[part]
if isinstance(value,(dict,list)): print(json.dumps(value,ensure_ascii=False,separators=(',',':')))
else: print(value)
PY_JPR_FIELD
}
''', 'JPR field helper')
    text = regex_once(text, r'^install_landing\(\) \{.*?^\}\n', r'''install_landing() {
  local key="$1" combined="${2:-0}" tmp
  tmp="$(mktemp /tmp/vvv-landing.XXXXXX.sh)"
  awk -v key="$key" 'BEGIN{done=0} !done && /^PAIRING_KEY=/ {print "PAIRING_KEY=\047" key "\047"; done=1; next} {print}' "$BASE_DIR/landing.sh" > "$tmp"
  chmod 700 "$tmp"
  local landing_rc
  if [[ "$combined" == 1 ]]; then
    VVV_COMBINED_INSTALL=1 sh "$tmp" && landing_rc=0 || landing_rc=$?
  else
    sh "$tmp" && landing_rc=0 || landing_rc=$?
  fi
  rm -f "$tmp"
  (( landing_rc == 0 )) || fail "中转副机安装程序失败（退出码 ${landing_rc}）。"
  [[ -x /usr/local/sbin/landing-vps ]] || fail "中转副机管理命令不存在。"
  cat > /usr/local/sbin/vvv-landing-original <<'EOF_LANDING_ORIGINAL'
#!/usr/bin/env bash
exec /usr/local/sbin/landing-vps "$@"
EOF_LANDING_ORIGINAL
  chmod 700 /usr/local/sbin/vvv-landing-original
}
''', 'landing installer wrapper')
    text = regex_once(text, r'^rebuild_roles_from_system\(\) \{.*?^\}\n', r'''rebuild_roles_from_system() {
  detect_installed_modules
  local primary
  if [[ "$INST_LANDING" == true && "$INST_PROXY" == true ]]; then
    primary=landing-direct
  elif [[ "$INST_LANDING" == true ]]; then
    primary=landing
  elif [[ "$INST_CENTER" == true && "$INST_RELAY" == true ]]; then
    primary=center-relay
  elif [[ "$INST_CENTER" == true ]]; then
    primary=center
  elif [[ "$INST_RELAY" == true ]]; then
    primary=relay
  elif [[ "$INST_PROXY" == true ]]; then
    primary=direct
  else
    fail "没有检测到可写入的已安装模块。"
  fi
  install -d -m700 /etc/vvv
  python3 - "$ROLE_FILE" "$primary" "$INST_CENTER" "$INST_RELAY" "$INST_LANDING" "$INST_PROXY" <<'PY_ROLES'
import json,os,sys,tempfile
path,primary,center,relay,landing,proxy=sys.argv[1:]
obj={'schema':2,'primary_role':primary,'roles':{
 'center':center=='true','relay':relay=='true','landing':landing=='true','proxy':proxy=='true'}}
os.makedirs(os.path.dirname(path),exist_ok=True)
fd,tmp=tempfile.mkstemp(prefix='.roles.',dir=os.path.dirname(path))
with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump(obj,f,ensure_ascii=False,indent=2); f.write('\n')
os.chmod(tmp,0o600); os.replace(tmp,path)
PY_ROLES
}
''', 'role rebuild')
    text = regex_once(text, r'^show_parameter_summary\(\) \{.*?^\}\n\nREUSE_PROXY=0', r'''show_parameter_summary() {
  local role_name protocol_name endpoint scheme transport_label landing_port
  case "$choice" in
    1) role_name="安装订阅中心 + 中转主机 + 自身代理";;
    2) role_name="安装订阅中心 + 自身代理";;
    3) role_name="安装中转主机 + 自身代理";;
    4) role_name="安装中转副机 + 自身代理";;
    5) role_name="安装中转副机";;
    6) role_name="安装直连代理";;
    7) role_name="从云备份恢复";;
  esac
  echo; echo "========== 安装参数总览 =========="; echo "安装角色：$role_name"
  if [[ "$choice" == 7 ]]; then
    echo "云盘目录：vvv/（重新授权后选择恢复日期）"
  elif [[ "$choice" == 5 ]]; then
    echo "JPR3 密钥：已填写（${#key} 个字符）"
    echo "中转副机统一端口：$(jpr_field "$key" remote_public_port)"
  else
    case "$VVV_PROTOCOL_MODE" in dual) protocol_name="VLESS + Hysteria 2";; vless) protocol_name="仅 VLESS";; hy2) protocol_name="仅 Hysteria 2";; esac
    echo "自身直连协议：$protocol_name$([[ "$REUSE_PROXY" == 1 ]] && echo '（复用现有）')"
    echo "自身直连端口：$VVV_PROXY_PORT"
    [[ "$VVV_PROTOCOL_MODE" == hy2 ]] || echo "REALITY 伪装域名：$VVV_REALITY_SNI"
    [[ "$VVV_PROTOCOL_MODE" == vless ]] || echo "Hysteria 2 限速：${VVV_HY2_LIMIT_MBPS}M"
    if [[ "$choice" == 4 ]]; then
      landing_port="$(jpr_field "$key" remote_public_port)"
      echo "中转副机端口：${landing_port}（TCP/UDP）"
      echo "订阅注册：使用 JPR3 内置受限票据，不再重复询问"
    elif [[ "$choice" == 1 || "$choice" == 2 ]]; then
      case "$VVV_SUB_TRANSPORT" in direct-http) transport_label="直接 HTTP"; scheme=http;; direct-https) transport_label="直接 HTTPS"; scheme=https;; tunnel) transport_label="Cloudflare Tunnel"; scheme=https;; esac
      [[ "$VVV_SUB_TRANSPORT" == tunnel ]] && endpoint="https://${VVV_SUB_DOMAIN}/${VVV_SUB_SUFFIX}" || endpoint="${scheme}://${VVV_SUB_DOMAIN:-本机公网IP}:${VVV_SUB_PORT}/${VVV_SUB_SUFFIX}"
      echo "订阅传输：${transport_label}"; echo "统一订阅地址：${endpoint}"
    elif [[ "$choice" == 3 || "$choice" == 6 ]]; then
      [[ -n "$code" ]] && echo "订阅中心：已填写对接码" || echo "订阅中心：本次暂不注册"
    fi
  fi
  echo "=================================="; echo "参数已收集完毕，开始全自动安装。"
}

REUSE_PROXY=0''', 'parameter summary')
    bottom = r'''REUSE_PROXY=0
REUSE_CENTER=0
code=""
key=""
VVV_CF_TUNNEL_TOKEN=""
LANDING_REMOTE_PORT=""

migrate_center_config_if_needed
if [[ -f /etc/vvv-sub/.schema4-migrated ]]; then
  refresh_center_runtime_code
  ensure_center_runtime || fail "旧订阅中心迁移后服务无法启动。"
fi
show_install_menu
while true; do
  read -r -p "请输入编号：" choice
  case "$choice" in 0|1|2|3|4|5|6|7) break;; *) echo "请输入 0-7。";; esac
done
[[ "$choice" == 0 ]] && exit 0

case "$choice" in
  1|2|3|6)
    landing_state_valid && fail "当前 VPS 已安装中转副机；本版本只面向全新安装，请重装系统后按目标角色重新安装。"
    if main_state_valid; then load_existing_proxy_parameters; else ask_proxy_parameters; fi
    ;;
  4)
    (main_state_valid || landing_state_valid || center_partial) && fail "组合角色只允许在全新系统安装。"
    ask_required_jpr3
    LANDING_REMOTE_PORT="$(jpr_field "$key" remote_public_port)" || fail "无法读取 JPR3 中转端口。"
    ask_proxy_parameters
    [[ "$LANDING_REMOTE_PORT" != "$VVV_PROXY_PORT" ]] || fail "JPR3 中转端口 ${LANDING_REMOTE_PORT} 与自身直连端口冲突；请在中转主机使用默认 553 重新生成 JPR3。"
    ;;
  5)
    (main_state_valid || landing_state_valid || center_partial) && fail "中转副机只允许在全新系统安装。"
    ask_required_jpr3
    ;;
  7)
    (main_state_valid || center_complete || landing_state_valid) && fail "云恢复只允许在干净系统执行。"
    ;;
esac

case "$choice" in
  1|2)
    if center_complete; then load_existing_center_parameters; else backup_and_reset_partial_center; ask_center_parameters; fi
    ;;
  3) center_complete && code="$(cat /etc/vvv-sub/registration.code)" || ask_optional_vvc1 code;;
  6) center_complete && code="$(cat /etc/vvv-sub/registration.code)" || ask_optional_vvc1 code;;
  4|5|7) ;;
esac

show_parameter_summary

case "$choice" in
  1) ensure_host; enable_relay; ensure_center; rebuild_roles_from_system; register_current_main_role;;
  2) ensure_host; ensure_center; rebuild_roles_from_system; register_current_main_role;;
  3) ensure_host; enable_relay; rebuild_roles_from_system; register_current_main_role "$code";;
  4)
    ensure_host
    install_landing "$key" 1
    rebuild_roles_from_system
    bash "$BASE_DIR/register_sync.sh" landing-direct "$key"
    ;;
  5)
    install_landing "$key" 0
    rebuild_roles_from_system
    bash "$BASE_DIR/register_sync.sh" landing "$key"
    ;;
  6) ensure_host; rebuild_roles_from_system; register_current_main_role "$code";;
  7)
    python3 "$BASE_DIR/restore_manager.py"
    if main_state_valid; then
      VVV_PROTOCOL_MODE="$(json_value "$MAIN_STATE" protocol_mode dual)"
      VVV_PROXY_PORT="$(json_value "$MAIN_STATE" listen_port 443)"
      VVV_REALITY_SNI="$(json_value "$MAIN_STATE" sni www.softbank.jp)"
      VVV_HY2_LIMIT_MBPS="$(json_value "$MAIN_STATE" hy2_limit_mbps 50)"
      export VVV_PROTOCOL_MODE VVV_PROXY_PORT VVV_REALITY_SNI VVV_HY2_LIMIT_MBPS
      bash "$BASE_DIR/host.sh"
    fi
    if landing_state_valid; then
      [[ -s /etc/jp-relay/pairing-key.txt ]] || fail "恢复包缺少中转副机 JPR3。"
      key="$(tr -d '[:space:]' </etc/jp-relay/pairing-key.txt)"
      if main_state_valid; then install_landing "$key" 1; else install_landing "$key" 0; fi
    fi
    [[ ! -s "$CENTER_CFG" ]] || VVV_RESTORE_MODE=1 bash "$BASE_DIR/center_install.sh"
    rebuild_roles_from_system
    if center_complete; then register_current_main_role; elif [[ -s /etc/vvv/client.json ]]; then systemctl start vvv-sync.service || true; fi
    echo; echo "========== 恢复后逐节点检测 =========="
    python3 "$BASE_DIR/node_probe.py" || true
    [[ ! -x "$BASE_DIR/backup_manager.py" ]] || python3 "$BASE_DIR/backup_manager.py" create restore-completed --force >/dev/null || true
    ;;
esac

install_unified_manager
printf '\nVVV 安装完成。以后统一输入：vps\n'
/usr/local/sbin/vps
'''
    text = regex_once(text, r'REUSE_PROXY=0\nREUSE_CENTER=0.*\Z', bottom, 'bootstrap main flow')
    write('core-src/bootstrap.sh', text)


def patch_sub_center():
    text = read('core-src/sub_center.py')
    text = replace_once(text, "OVERRIDES = DATA / 'node-overrides.json'", "OVERRIDES = DATA / 'node-overrides.json'\nTICKETS = DATA / 'relay-tickets.json'", 'ticket path')
    text = replace_once(text,
                        '''def nodes_from_host(doc):
    state = doc.get('state') or {}
    role = doc.get('role') or ''
    mode = state.get('protocol_mode')''',
                        '''def nodes_from_host(doc):
    role = doc.get('role') or ''
    if role == 'landing':
        return []
    states = doc.get('states') or {}
    state = (states.get('direct') or doc.get('state') or {}) if role == 'landing-direct' else (doc.get('state') or {})
    mode = state.get('protocol_mode')''',
                        'combined node source')
    text = replace_once(text,
                        '''        for relay in state.get('relays') or []:
            rv, rh = relay.get('vless'), relay.get('hy2')
            if rv:
                add_vless(relay.get('name') or relay.get('id'), rv.get('client_uuid'), True, 'VPS中转', relay.get('id'), expected_exit_ip=relay.get('remote_ip'))
            if rh:
                add_hy2(relay.get('name') or relay.get('id'), rh.get('client_password'), 'VPS中转', relay.get('id'), expected_exit_ip=relay.get('remote_ip'))''',
                        '''        for relay in state.get('relays') or []:
            rv, rh = relay.get('vless'), relay.get('hy2')
            raw_name = str(relay.get('name') or '')
            country = raw_name[:2].upper() if len(raw_name) >= 3 and raw_name[:2].isalpha() and raw_name[2] == '-' else ''
            relay_base = (country + '-' if country else '') + f'中转-{ip}:{port}'
            if rv:
                add_vless(relay_base, rv.get('client_uuid'), True, 'VPS中转', relay.get('id'), expected_exit_ip=relay.get('remote_ip'))
            if rh:
                add_hy2(relay_base, rh.get('client_password'), 'VPS中转', relay.get('id'), expected_exit_ip=relay.get('remote_ip'))''',
                        'subscription relay names')
    text = text.replace("doc.get('role') in ('direct', 'landing')", "doc.get('role') in ('direct', 'landing', 'landing-direct')")
    text = replace_once(text,
                        "'state': body.get('state') or {}, 'meta': body.get('meta') or {},",
                        "'state': body.get('state') or {}, 'states': body.get('states') or {}, 'meta': body.get('meta') or {},",
                        'final registration states')
    text = replace_once(text,
                        "if not re.fullmatch(r'[A-Za-z0-9._-]{8,128}', host_id) or role not in ('center-relay', 'center', 'relay', 'direct', 'landing'):",
                        "if not re.fullmatch(r'[A-Za-z0-9._-]{8,128}', host_id) or role not in ('center-relay', 'center', 'relay', 'direct', 'landing', 'landing-direct'):",
                        'accepted roles')
    text = replace_once(text,
                        "doc = {'host_id': host_id, 'role': entry.get('role', 'direct'), 'state': body.get('state') or {},\n                       'meta': body.get('meta') or {}, 'last_seen': now(), 'last_seen_ts': time.time()}",
                        "doc = {'host_id': host_id, 'role': entry.get('role', 'direct'), 'state': body.get('state') or {},\n                       'states': body.get('states') or {}, 'meta': body.get('meta') or {}, 'last_seen': now(), 'last_seen_ts': time.time()}",
                        'sync states')
    helper = r'''
def source_relay_active(source_host_id, relay_id):
    doc = read_json(HOSTS / f'{source_host_id}.json', {}) or {}
    state = doc.get('state') or {}
    return any(str(item.get('id')) == str(relay_id) for item in state.get('relays') or [])


def relay_ticket_record(source_host_id, relay_id):
    store = read_json(TICKETS, {'tickets': []}) or {'tickets': []}
    rows = store.setdefault('tickets', [])
    current = next((row for row in rows if row.get('source_host_id') == source_host_id and row.get('relay_id') == relay_id), None)
    if current is None:
        current = {'source_host_id': source_host_id, 'relay_id': relay_id,
                   'registration_token': secrets.token_urlsafe(32), 'created_at': now()}
        rows.append(current)
    current['updated_at'] = now()
    atomic_json(TICKETS, store)
    return current

'''
    text = replace_once(text, '\nclass Handler(BaseHTTPRequestHandler):', helper + '\nclass Handler(BaseHTTPRequestHandler):', 'ticket helpers')
    endpoint = r'''            if path == '/api/v1/relay-ticket':
                host_id = str(body.get('host_id') or '').strip()
                relay_id = str(body.get('relay_id') or '').strip()
                entry = next((item for item in registry['hosts'] if item.get('host_id') == host_id), None)
                if entry is None or not secrets.compare_digest(auth_token(self), str(entry.get('token') or '')):
                    return self.send_bytes(403, b'Forbidden\n')
                if entry.get('role') not in ('center-relay', 'relay') or not re.fullmatch(r'[A-Za-z0-9._-]{1,128}', relay_id):
                    return self.send_bytes(400, b'Bad relay ticket request\n')
                if not source_relay_active(host_id, relay_id):
                    return self.send_bytes(409, b'Relay is not synchronized\n')
                ticket = relay_ticket_record(host_id, relay_id)
                bootstrap = {'api_base_url': str(cfg.get('api_base_url') or ''), 'relay_id': relay_id,
                             'registration_token': ticket['registration_token']}
                return self.send_bytes(200, json.dumps({'ok': True, 'subscription_bootstrap': bootstrap}, ensure_ascii=False).encode(), 'application/json')
            if path == '/api/v1/register-ticket':
                relay_id = str(body.get('relay_id') or '').strip()
                supplied = str(body.get('registration_token') or '')
                role = str(body.get('role') or '').strip()
                host_id = str(body.get('host_id') or '').strip()
                store = read_json(TICKETS, {'tickets': []}) or {'tickets': []}
                ticket = next((row for row in store.get('tickets', []) if row.get('relay_id') == relay_id and
                               secrets.compare_digest(str(row.get('registration_token') or ''), supplied)), None)
                if ticket is None or not source_relay_active(ticket.get('source_host_id'), relay_id):
                    return self.send_bytes(403, b'Invalid or revoked relay ticket\n')
                if role not in ('landing', 'landing-direct', 'direct') or not re.fullmatch(r'[A-Za-z0-9._-]{8,128}', host_id):
                    return self.send_bytes(400, b'Bad ticket registration\n')
                entry = next((item for item in registry['hosts'] if item.get('host_id') == host_id), None)
                if entry is None:
                    entry = {'host_id': host_id, 'token': secrets.token_urlsafe(32), 'created_at': now()}
                    registry['hosts'].append(entry)
                entry.update(role=role, hostname=str(body.get('hostname') or ''), relay_id=relay_id, updated_at=now())
                atomic_json(REGISTRY, registry)
                result = finalize_registration(entry, body)
                result['registration_method'] = 'JPR3-ticket'
                return self.send_bytes(200, json.dumps(result, ensure_ascii=False).encode(), 'application/json')
'''
    text = replace_once(text, "            if path == '/api/v1/register':", endpoint + "            if path == '/api/v1/register':", 'ticket endpoints')
    write('core-src/sub_center.py', text)


def patch_renderer():
    text = read('core-src/client_local_renderer.py')
    text = replace_once(text,
                        "nodes = build(relay.get('name') or relay.get('id'), rv.get('client_uuid'), rh.get('client_password'))",
                        "raw_name = str(relay.get('name') or '')\n        country = raw_name[:2].upper() if len(raw_name) >= 3 and raw_name[:2].isalpha() and raw_name[2] == '-' else ''\n        relay_base = (country + '-' if country else '') + f'中转-{server}:{port}'\n        nodes = build(relay_base, rv.get('client_uuid'), rh.get('client_password'))",
                        'local relay names')
    old = '''def landing_contexts(state, root='/'):
    mode = state.get('protocol_mode')
    name = state.get('node_name') or f"{state.get('remote_public_ip')}:{state.get('remote_public_port')}"
    server = state.get('japan_public_ip')
    port = int(state.get('japan_port') or 0)'''
    new = '''def landing_contexts(state, root='/'):
    mode = state.get('protocol_mode')
    raw_name = str(state.get('node_name') or '')
    server = state.get('japan_public_ip')
    port = int(state.get('japan_port') or 0)
    country = raw_name[:2].upper() if len(raw_name) >= 3 and raw_name[:2].isalpha() and raw_name[2] == '-' else ''
    name = (country + '-' if country else '') + f'中转-{server}:{port}' '''.rstrip()
    text = replace_once(text, old, new, 'landing local names')
    text = regex_once(text, r'^def detect_contexts\(root=.*?^    return .center-only., \[\]\n', r'''def detect_contexts(root='/'):
    main_state = rooted(root, '/etc/jp-relay/state.json')
    landing_state = rooted(root, '/etc/jp-relay/landing-state.json')
    has_main = main_state.is_file()
    has_landing = landing_state.is_file()
    if has_main and has_landing:
        direct = read_json(main_state)
        landing = read_json(landing_state)
        if not isinstance(direct, dict) or not isinstance(landing, dict):
            raise RuntimeError('组合角色状态文件无效。')
        return 'landing-direct', main_contexts(direct, root) + landing_contexts(landing, root)
    if has_landing:
        state = read_json(landing_state)
        if not isinstance(state, dict):
            raise RuntimeError('中转副机状态文件无效。')
        return 'landing', landing_contexts(state, root)
    if has_main:
        state = read_json(main_state)
        if not isinstance(state, dict):
            raise RuntimeError('主机状态文件无效。')
        return 'main', main_contexts(state, root)
    return 'center-only', []
''', 'context aggregation')
    write('core-src/client_local_renderer.py', text)


def patch_client_protection():
    text = read('core-src/client_upgrade_engine.py')
    text = replace_once(text, "    '/etc/systemd/system/sing-box.service',\n]", "    '/etc/systemd/system/sing-box.service',\n    '/etc/vvv-landing/xray/config.json',\n    '/etc/vvv-landing/sing-box/config.json',\n    '/etc/vvv-landing/sing-box/tls/landing-hy2.crt',\n    '/etc/vvv-landing/sing-box/tls/landing-hy2.key',\n    '/etc/systemd/system/vvv-landing-xray.service',\n    '/etc/systemd/system/vvv-landing-sing-box.service',\n]", 'protected landing files')
    text = replace_once(text,
                        "            'sing-box.service': process_identity('sing-box.service'),\n        }",
                        "            'sing-box.service': process_identity('sing-box.service'),\n            'vvv-landing-xray.service': process_identity('vvv-landing-xray.service'),\n            'vvv-landing-sing-box.service': process_identity('vvv-landing-sing-box.service'),\n        }",
                        'protected landing processes')
    write('core-src/client_upgrade_engine.py', text)


def patch_backup_restore():
    text = read('core-src/backup_manager.py')
    text = replace_once(text, "    Path('/etc/jp-relay/landing-state.json'),", "    Path('/etc/jp-relay/landing-state.json'),\n    Path('/etc/jp-relay/pairing-key.txt'),\n    Path('/etc/vvv-landing'),\n    Path('/var/lib/vvv-sub/relay-tickets.json'),", 'backup landing config')
    write('core-src/backup_manager.py', text)
    text = read('core-src/restore_manager.py')
    text = replace_once(text,
                        "    'etc/jp-relay/state.json', 'etc/jp-relay/landing-state.json',",
                        "    'etc/jp-relay/state.json', 'etc/jp-relay/landing-state.json', 'etc/jp-relay/pairing-key.txt',\n    'var/lib/vvv-sub/relay-tickets.json',",
                        'restore exact paths')
    text = replace_once(text, "ALLOWED_PREFIX = ('var/lib/vvv-sub/hosts/', 'etc/sing-box/tls/')",
                        "ALLOWED_PREFIX = ('var/lib/vvv-sub/hosts/', 'etc/sing-box/tls/', 'etc/vvv-landing/')",
                        'restore landing prefix')
    text = replace_once(text, "for root in ('/etc/vvv-sub', '/var/lib/vvv-sub', '/etc/jp-relay', '/etc/vvv'):",
                        "for root in ('/etc/vvv-sub', '/var/lib/vvv-sub', '/etc/jp-relay', '/etc/vvv', '/etc/vvv-landing'):",
                        'restore residual backup')
    write('core-src/restore_manager.py', text)


def patch_register_and_manager():
    write('core-src/sync_agent.py', SYNC_AGENT)
    write('core-src/register_sync.sh', REGISTER_SYNC)
    write('core-src/vvv_manager.sh', VVV_MANAGER)


def patch_tests_and_workflow():
    write('tests/landing_direct_role_validation.py', STATIC_TEST)
    final = read('tests/final_runtime_validation.sh')
    final = replace_once(final, '  "$ROOT/core-src/node_probe.py" \\\n  "$ROOT/tests/conformance.py"',
                         '  "$ROOT/core-src/node_probe.py" \\\n  "$ROOT/core-src/client_local_renderer.py" \\\n  "$ROOT/core-src/client_upgrade_engine.py" \\\n  "$ROOT/tests/landing_direct_role_validation.py" \\\n  "$ROOT/tests/conformance.py"', 'compile new validation')
    final = replace_once(final, 'python3 "$ROOT/tests/conformance.py"',
                         'python3 "$ROOT/tests/conformance.py"\npython3 "$ROOT/tests/landing_direct_role_validation.py"',
                         'run new validation')
    write('tests/final_runtime_validation.sh', final)


def patch_docs():
    write('LANDING_DIRECT_ROLE.md', '''# 中转副机 + 自身代理\n\n- 自身直连代理：TCP/UDP 443，使用 `xray.service` 与 `sing-box.service`。\n- 中转副机：TCP/UDP 553，使用 `vvv-landing-xray.service` 与 `vvv-landing-sing-box.service`。\n- 组合安装只输入一次 JPR3；其中包含与线路绑定的订阅中心受限注册票据。\n- 订阅中的中转节点统一命名为 `国家-协议-中转-日本入口IP:端口`。\n- 副机自身直连节点继续使用 `国家-协议-副机IP:443`。\n- 客户端支持升级会保护四个代理服务及两套配置，不能重启或改写它们。\n''')


def main():
    patch_register_and_manager()
    patch_host()
    patch_landing()
    patch_bootstrap()
    patch_sub_center()
    patch_renderer()
    patch_client_protection()
    patch_backup_restore()
    patch_tests_and_workflow()
    patch_docs()
    print('landing-direct role transformation complete')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    file = Path(path)
    text = file.read_text(encoding='utf-8')
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected exactly one target, found {count}')
    file.write_text(text.replace(old, new, 1), encoding='utf-8')


def replace_all(path: str, old: str, new: str, minimum: int, label: str) -> None:
    file = Path(path)
    text = file.read_text(encoding='utf-8')
    count = text.count(old)
    if count < minimum:
        raise SystemExit(f'{label}: expected at least {minimum} targets, found {count}')
    file.write_text(text.replace(old, new), encoding='utf-8')


# 1) Generate a compact, terminal-safe JPR3 payload. Keep the same JPR3 prefix
# and checksum shape, but checksum the compressed transfer bytes.
replace_once(
    'core-src/host.sh',
    'import base64,hashlib,json,sys\n',
    'import base64,hashlib,json,sys,zlib\n',
    'host JPR3 imports',
)
replace_once(
    'core-src/host.sh',
    '''raw=json.dumps(payload,ensure_ascii=False,separators=(",",":")).encode()
enc=base64.urlsafe_b64encode(raw).decode().rstrip("=")
chk=hashlib.sha256(raw).hexdigest()[:20]
print(f"JPR3.{enc}.{chk}")
''',
    '''raw=json.dumps(payload,ensure_ascii=False,separators=(",",":")).encode()
packed=zlib.compress(raw,9)
enc=base64.urlsafe_b64encode(packed).decode().rstrip("=")
chk=hashlib.sha256(packed).hexdigest()[:20]
key=f"JPR3.{enc}.{chk}"
if len(key) >= 3500:
    raise SystemExit(f"压缩后的 JPR3 对接密钥仍过长（{len(key)} 字符），拒绝生成可能被终端截断的密钥。")
print(key)
''',
    'compact JPR3 generator',
)

# Existing installations must receive the refreshed embedded manager when the
# repeatable installer is run; do not rerun the full proxy installation.
replace_once(
    'core-src/host.sh',
    '''chmod 700 /usr/local/sbin/jp-relay-manager
/usr/local/sbin/jp-relay-manager
''',
    '''chmod 700 /usr/local/sbin/jp-relay-manager
if [[ "${VVV_REFRESH_MANAGER_ONLY:-0}" == 1 ]]; then
  exit 0
fi
/usr/local/sbin/jp-relay-manager
''',
    'manager-only refresh entrypoint',
)

# Requested relay-management menu wording.
replace_once(
    'core-src/host.sh',
    'echo "${new_vps_index}. 新建中转线路"',
    'echo "${new_vps_index}. 新建 VPS 副机中转线路"',
    'relay menu label',
)

# 2) Decode both legacy uncompressed JPR3 and new zlib-compressed JPR3 on the
# landing host. Binary compressed bytes never enter a shell variable.
replace_once(
    'core-src/landing.sh',
    '''parse_pairing_key() {
  old_ifs="$IFS"; IFS=.; set -- $PAIRING_KEY; IFS="$old_ifs"
  [ "$#" -eq 3 ] || fail "JPR3 对接密钥格式错误。"
  [ "$1" = "JPR3" ] || fail "本脚本只接受以 JPR3. 开头的全新对接密钥。"
  encoded="$2"; expected_checksum="$3"
  PAIR_JSON="$(base64url_decode "$encoded")" || fail "JPR3 Base64 解码失败。"
  actual_checksum="$(printf '%s' "$PAIR_JSON" | sha256sum | awk '{print substr($1,1,20)}')"
  [ "$actual_checksum" = "$expected_checksum" ] || fail "JPR3 校验失败，密钥可能复制不完整。"

''',
    '''parse_pairing_key() {
  old_ifs="$IFS"; IFS=.; set -- $PAIRING_KEY; IFS="$old_ifs"
  [ "$#" -eq 3 ] || fail "JPR3 对接密钥格式错误；密钥可能被终端单行输入上限截断。"
  [ "$1" = "JPR3" ] || fail "本脚本只接受以 JPR3. 开头的对接密钥。"
  encoded="$2"; expected_checksum="$3"
  PAIR_JSON="$(python3 - "$encoded" "$expected_checksum" <<'PY_JPR3_DECODE'
import base64
import hashlib
import json
import sys
import zlib

encoded, expected = sys.argv[1:]
try:
    transferred = base64.urlsafe_b64decode(encoded + '=' * ((4 - len(encoded) % 4) % 4))
except Exception as exc:
    raise SystemExit(f'Base64 解码失败：{exc}')
if len(transferred) > 65536:
    raise SystemExit('JPR3 传输数据异常过大。')
actual = hashlib.sha256(transferred).hexdigest()[:20]
if actual != expected:
    raise SystemExit('JPR3 校验失败，密钥可能复制不完整。')
if transferred.startswith(b'{'):
    raw = transferred
else:
    try:
        raw = zlib.decompress(transferred)
    except Exception as exc:
        raise SystemExit(f'JPR3 解压失败：{exc}')
if len(raw) > 131072:
    raise SystemExit('JPR3 解压后数据异常过大。')
try:
    obj = json.loads(raw.decode('utf-8'))
except Exception as exc:
    raise SystemExit(f'JPR3 JSON 无效：{exc}')
sys.stdout.write(json.dumps(obj, ensure_ascii=False, separators=(',', ':')))
PY_JPR3_DECODE
)" || fail "JPR3 解码或校验失败，密钥可能复制不完整。"

''',
    'landing dual-format JPR3 decoder',
)

# 3) Installation menu wording, input truncation guard, compact registration
# code extraction, and safe manager refresh on already-installed main hosts.
replace_all(
    'core-src/bootstrap.sh',
    '安装中转副机（通过主机代理）',
    '安装中转副机',
    2,
    'bootstrap landing labels',
)
replace_once(
    'core-src/bootstrap.sh',
    '''    if [[ "$key" != JPR3.* ]]; then
      echo "对接密钥格式错误，必须以 JPR3. 开头。"
      continue
    fi
    break
''',
    '''    if ((${#key} >= 4095)); then
      echo "对接密钥已达到终端单行输入上限，内容很可能被截断。"
      echo "请先在中转主机重新运行统一安装命令刷新程序，再重新查看并复制新的压缩 JPR3 密钥。"
      continue
    fi
    if [[ ! "$key" =~ ^JPR3\.[A-Za-z0-9_-]+\.[0-9a-f]{20}$ ]]; then
      echo "对接密钥格式错误或复制不完整，必须是完整的 JPR3.数据.校验值。"
      continue
    fi
    break
''',
    'JPR3 prompt truncation guard',
)
replace_once(
    'core-src/bootstrap.sh',
    '''jpr_registration_code(){
  local value="$1" rest encoded mod padded
  rest="${value#JPR3.}"; encoded="${rest%%.*}"
  mod=$((${#encoded} % 4))
  case "$mod" in 0) padded="$encoded";; 2) padded="${encoded}==";; 3) padded="${encoded}=";; *) return 1;; esac
  printf '%s' "$padded" | tr '_-' '/+' | base64 -d 2>/dev/null | jq -r '.subscription_registration_code // empty'
}
''',
    '''jpr_registration_code(){
  local value="$1"
  python3 - "$value" <<'PY_JPR_REGISTRATION_CODE'
import base64
import json
import sys
import zlib

parts = ''.join(sys.argv[1].split()).split('.')
if len(parts) != 3 or parts[0] != 'JPR3':
    raise SystemExit(1)
try:
    transferred = base64.urlsafe_b64decode(parts[1] + '=' * ((4 - len(parts[1]) % 4) % 4))
    raw = transferred if transferred.startswith(b'{') else zlib.decompress(transferred)
    value = json.loads(raw.decode('utf-8')).get('subscription_registration_code') or ''
except Exception:
    raise SystemExit(1)
print(value)
PY_JPR_REGISTRATION_CODE
}
''',
    'compressed JPR3 registration-code extraction',
)
replace_once(
    'core-src/bootstrap.sh',
    '''  if host_ready && ensure_host_runtime; then
    echo "本机代理已完整安装，复用现有协议、端口和永久凭证，跳过重复安装。"
    return 0
  fi
''',
    '''  if host_ready && ensure_host_runtime; then
    VVV_REFRESH_MANAGER_ONLY=1 bash "$BASE_DIR/host.sh"
    echo "本机代理已完整安装，已刷新中转管理程序并复用现有协议、端口和永久凭证。"
    return 0
  fi
''',
    'refresh existing host manager',
)

# Keep documentation and permanent tests aligned.
replace_all(
    'README.md',
    '安装中转副机（通过主机代理）',
    '安装中转副机',
    1,
    'README landing label',
)

path = Path('tests/conformance.py')
text = path.read_text(encoding='utf-8')
text = text.replace("        '4. 安装中转副机（通过主机代理）',", "        '4. 安装中转副机',")
old = '''def test_jpr3_and_slot_architecture():
    prepare = read('src/prepare.py')
    landing = read('core-src/landing.sh')
'''
new = '''def test_jpr3_and_slot_architecture():
    prepare = read('src/prepare.py')
    host = read('core-src/host.sh')
    landing = read('core-src/landing.sh')
    bootstrap = read('core-src/bootstrap.sh')
'''
if text.count(old) != 1:
    raise SystemExit('conformance JPR3 test header target not found exactly once')
text = text.replace(old, new, 1)
old = '''    require('python3' in landing, '落地脚本没有显式安装 Python 运行时')
    require('.schema==3' in landing and '.type=="jp-relay-landing"' in landing, '落地脚本没有严格校验 JPR3')
    require('actual_checksum' in landing and 'expected_checksum' in landing, '落地脚本没有校验 JPR3 摘要')
'''
new = '''    require('python3' in landing, '落地脚本没有显式安装 Python 运行时')
    require('.schema==3' in landing and '.type=="jp-relay-landing"' in landing, '落地脚本没有严格校验 JPR3')
    require('zlib.compress(raw,9)' in host and 'len(key) >= 3500' in host, '主机没有生成终端安全的压缩 JPR3')
    require('zlib.decompress(transferred)' in landing and "transferred.startswith(b'{')" in landing, '落地端没有同时兼容新旧 JPR3')
    require("hashlib.sha256(transferred).hexdigest()[:20]" in landing and 'expected_checksum' in landing, '落地脚本没有校验 JPR3 传输摘要')
    require('对接密钥已达到终端单行输入上限' in bootstrap, '安装器没有识别 4095 字符截断风险')
    require('zlib.decompress(transferred)' in bootstrap and 'subscription_registration_code' in bootstrap, '压缩 JPR3 无法提取订阅接入码')
    require('VVV_REFRESH_MANAGER_ONLY=1 bash "$BASE_DIR/host.sh"' in bootstrap, '重复安装不会刷新现有中转管理程序')
    require('新建 VPS 副机中转线路' in host and '新建中转线路' not in host, '中转线路菜单名称未按要求修改')
    require('安装中转副机（通过主机代理）' not in bootstrap, '安装菜单仍保留多余说明文字')
'''
if text.count(old) != 1:
    raise SystemExit('conformance JPR3 assertions target not found exactly once')
text = text.replace(old, new, 1)
path.write_text(text, encoding='utf-8')

print('COMPACT JPR3 AND MENU PATCH APPLIED')

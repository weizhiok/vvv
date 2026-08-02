#!/usr/bin/env python3
from pathlib import Path


def replace_once(path, old, new, label):
    file = Path(path)
    text = file.read_text(encoding='utf-8')
    if text.count(old) != 1:
        raise SystemExit(f'{label}: expected exactly one target, found {text.count(old)}')
    file.write_text(text.replace(old, new, 1), encoding='utf-8')

replace_once(
    'core-src/bootstrap.sh',
    '''  chmod 700 "$tmp"
  sh "$tmp"
  rm -f "$tmp"
  [[ -x /usr/local/sbin/landing-vps ]] || fail "中转副机安装后管理命令不存在。"
''',
    '''  chmod 700 "$tmp"
  local landing_rc
  if sh "$tmp"; then
    landing_rc=0
  else
    landing_rc=$?
  fi
  rm -f "$tmp"
  if (( landing_rc != 0 )); then
    fail "中转副机安装程序失败（退出码 ${landing_rc}）；已停止后续步骤，请以上方首次失败信息为准。"
  fi
  [[ -x /usr/local/sbin/landing-vps ]] || fail "中转副机安装程序返回成功，但管理命令不存在。"
''',
    'landing failure propagation',
)

path = Path('tests/conformance.py')
text = path.read_text(encoding='utf-8')
needle = '''    require('bash "$BASE_DIR/register_sync.sh" landing "$code"' in text, '中转副机没有自动注册')
'''
insert = '''    require('bash "$BASE_DIR/register_sync.sh" landing "$code"' in text, '中转副机没有自动注册')
    require('landing_rc=$?' in text and '已停止后续步骤' in text, '落地安装失败后仍会继续执行并覆盖首次错误')
'''
if text.count(needle) != 1:
    raise SystemExit('conformance landing failure target not found exactly once')
path.write_text(text.replace(needle, insert, 1), encoding='utf-8')

print('LANDING FAILURE PROPAGATION PATCH APPLIED')

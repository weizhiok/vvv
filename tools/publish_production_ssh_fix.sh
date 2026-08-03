#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

git config user.name github-actions[bot]
git config user.email 41898282+github-actions[bot]@users.noreply.github.com

python3 - <<'PY_PATCH'
from pathlib import Path

sync=Path('core-src/sync_agent.py')
text=sync.read_text(encoding='utf-8')
anchor='\ndef decode_vvc1(code):\n'
insert='''\ndef encode_vvc1(payload):
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip('=')
    digest = hashlib.sha256(b'VVV-VVC1\\0' + raw).hexdigest()[:20]
    return f'VVC1.{encoded}.{digest}'


def decode_vvc1(code):
'''
if text.count(anchor) != 1:
    raise SystemExit('sync_agent encode_vvc1 anchor mismatch')
text=text.replace(anchor,insert,1)
text=text.replace('订阅中心 API 必须使用 IP 地址。','订阅中心 API 必须使用 IP 地址，不能使用域名。')
sync.write_text(text,encoding='utf-8')

path=Path('tests/conformance.py')
text=path.read_text(encoding='utf-8')
old="""    labels = [
        '1. 安装订阅中心 + 中转主机 + 自身代理', '2. 安装订阅中心 + 自身代理',
        '3. 安装中转主机 + 自身代理', '4. 安装中转副机', '5. 安装直连代理',
        '6. 从云备份恢复', '0. 退出',
    ]"""
new="""    labels = [
        '1. 安装订阅中心 + 中转主机 + 自身代理', '2. 安装订阅中心 + 自身代理',
        '3. 安装中转主机 + 自身代理', '4. 安装中转副机 + 自身代理',
        '5. 安装中转副机', '6. 安装直连代理', '7. 从云备份恢复', '0. 退出',
    ]"""
if text.count(old) != 1:
    raise SystemExit('conformance menu anchor mismatch')
text=text.replace(old,new,1)
text=text.replace("'输入订阅中心对接码（按回车跳过）'", "'请输入订阅中心对接码（支持 VVC1 或含注册票据的 JPR3；按回车跳过）'")
text=text.replace("'参数已收集完毕，直接开始全自动安装'", "'参数已收集完毕，开始全自动安装'")
path.write_text(text,encoding='utf-8')
PY_PATCH

python3 -m py_compile core-src/sync_agent.py tests/conformance.py
python3 tests/conformance.py

git fetch --no-tags --depth=1 origin main
git checkout FETCH_HEAD -- \
  tools/publish_production_ssh_fix.sh \
  .github/workflows/publish-production-ssh-fix.yml

git add -A
git diff --cached --check
git commit -m 'Update conformance for combined role'
git push origin HEAD

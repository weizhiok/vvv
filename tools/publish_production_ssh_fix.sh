#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1

git config user.name github-actions[bot]
git config user.email 41898282+github-actions[bot]@users.noreply.github.com

python3 - <<'PY_PATCH'
from pathlib import Path

landing=Path('core-src/landing.sh')
text=landing.read_text(encoding='utf-8')
replacements={
    '/etc/systemd/system/vvv-landing-vvv-landing-xray.service':'/etc/systemd/system/vvv-landing-xray.service',
    '/etc/systemd/system/vvv-landing-vvv-landing-sing-box.service':'/etc/systemd/system/vvv-landing-sing-box.service',
}
for old,new in replacements.items():
    if text.count(old) != 1:
        raise SystemExit(f'landing service path anchor mismatch: {old}')
    text=text.replace(old,new,1)
if 'vvv-landing-vvv-landing-' in text:
    raise SystemExit('landing service still contains a doubled prefix')
landing.write_text(text,encoding='utf-8')

path=Path('tests/landing_direct_role_validation.py')
test=path.read_text(encoding='utf-8')
anchor="""    require('/etc/systemd/system/xray.service <<' not in landing, '中转副机仍覆盖直连 Xray 服务')
    require('/etc/systemd/system/sing-box.service <<' not in landing, '中转副机仍覆盖直连 sing-box 服务')"""
replacement="""    require('/etc/systemd/system/xray.service <<' not in landing, '中转副机仍覆盖直连 Xray 服务')
    require('/etc/systemd/system/sing-box.service <<' not in landing, '中转副机仍覆盖直连 sing-box 服务')
    require('cat > /etc/systemd/system/vvv-landing-xray.service <<' in landing,
            '中转 Xray 服务单元路径不正确')
    require('cat > /etc/systemd/system/vvv-landing-sing-box.service <<' in landing,
            '中转 sing-box 服务单元路径不正确')
    require('vvv-landing-vvv-landing-' not in landing, '中转服务单元出现重复前缀')"""
if test.count(anchor) != 1:
    raise SystemExit('landing validation anchor mismatch')
path.write_text(test.replace(anchor,replacement,1),encoding='utf-8')
PY_PATCH

sh -n core-src/landing.sh
python3 tests/landing_direct_role_validation.py

git fetch --no-tags --depth=1 origin main
git checkout FETCH_HEAD -- \
  tools/publish_production_ssh_fix.sh \
  .github/workflows/publish-production-ssh-fix.yml

git add -A
git diff --cached --check
git commit -m 'Fix isolated landing service unit paths'
git push origin HEAD

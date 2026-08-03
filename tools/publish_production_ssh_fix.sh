#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1

git config user.name github-actions[bot]
git config user.email 41898282+github-actions[bot]@users.noreply.github.com

python3 - <<'PY_PATCH'
from pathlib import Path
path=Path('tests/conformance.py')
text=path.read_text(encoding='utf-8')
old="    require('复用已保存的 JPR3 对接密钥' in bootstrap, '中转副机无损升级仍要求重新粘贴 JPR3')"
new="    require('组合角色只允许在全新系统安装' in bootstrap and '中转副机只允许在全新系统安装' in bootstrap,\n            '全新安装角色边界不完整')"
if text.count(old) != 1:
    raise SystemExit('fresh-install conformance anchor mismatch')
path.write_text(text.replace(old,new,1),encoding='utf-8')
PY_PATCH

python3 -m py_compile tests/conformance.py
git fetch --no-tags --depth=1 origin main
git checkout FETCH_HEAD -- \
  tools/publish_production_ssh_fix.sh \
  .github/workflows/publish-production-ssh-fix.yml

git add -A
git diff --cached --check
git commit -m 'Align conformance with fresh installs'
git push origin HEAD

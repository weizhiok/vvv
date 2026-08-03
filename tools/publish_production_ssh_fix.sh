#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1

git config user.name github-actions[bot]
git config user.email 41898282+github-actions[bot]@users.noreply.github.com

python3 - <<'PY_PATCH'
from pathlib import Path

path=Path('core-src/bootstrap.sh')
text=path.read_text(encoding='utf-8')
old='''install_landing() {
  local key="$1" combined="${2:-0}" tmp
  tmp="$(mktemp /tmp/vvv-landing.XXXXXX.sh)"
  awk -v key="$key" 'BEGIN{done=0} !done && /^PAIRING_KEY=/ {print "PAIRING_KEY='" key "'"; done=1; next} {print}' "$BASE_DIR/landing.sh" > "$tmp"
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
'''
new='''install_landing() {
  local key="$1" combined="${2:-0}" landing_rc
  if [[ "$combined" == 1 ]]; then
    VVV_PAIRING_KEY="$key" VVV_COMBINED_INSTALL=1 sh "$BASE_DIR/landing.sh" && landing_rc=0 || landing_rc=$?
  else
    VVV_PAIRING_KEY="$key" sh "$BASE_DIR/landing.sh" && landing_rc=0 || landing_rc=$?
  fi
  (( landing_rc == 0 )) || fail "中转副机安装程序失败（退出码 ${landing_rc}）。"
  [[ -x /usr/local/sbin/landing-vps ]] || fail "中转副机管理命令不存在。"
'''
if text.count(old) != 1:
    raise SystemExit('install_landing unsafe injection anchor mismatch')
path.write_text(text.replace(old,new,1),encoding='utf-8')

path=Path('tests/landing_direct_role_validation.py')
test=path.read_text(encoding='utf-8')
anchor="""    require('subscription_bootstrap' in host and 'relay-ticket' in host, 'JPR3 没有受限订阅注册票据')"""
replacement="""    require('subscription_bootstrap' in host and 'relay-ticket' in host, 'JPR3 没有受限订阅注册票据')
    bootstrap = (CORE / 'bootstrap.sh').read_text(encoding='utf-8')
    require('VVV_PAIRING_KEY=\"$key\"' in bootstrap, 'JPR3 没有通过环境变量完整传给中转安装器')
    require('awk -v key=\"$key\"' not in bootstrap, 'JPR3 仍使用会破坏密钥的 awk 字符串拼接')"""
if test.count(anchor) != 1:
    raise SystemExit('JPR3 environment regression-test anchor mismatch')
path.write_text(test.replace(anchor,replacement,1),encoding='utf-8')
PY_PATCH

bash -n core-src/bootstrap.sh
python3 tests/landing_direct_role_validation.py

git rm -rf --ignore-unmatch core-src/__pycache__ tests/__pycache__
git fetch --no-tags --depth=1 origin main
git checkout FETCH_HEAD -- \
  tools/publish_production_ssh_fix.sh \
  .github/workflows/publish-production-ssh-fix.yml

git add -A
git diff --cached --check
git commit -m 'Pass JPR3 through the environment safely'
git push origin HEAD

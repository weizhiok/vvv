#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
LOG=/tmp/direct-publish-ssh-fix.log

git config user.name github-actions[bot]
git config user.email 41898282+github-actions[bot]@users.noreply.github.com

set +e
(
  set -Eeuxo pipefail
  python3 -m py_compile tools/fix_ssh_log_transformer_anchor.py tools/apply_ssh_log_fixes.py
  python3 tools/fix_ssh_log_transformer_anchor.py
  python3 -m py_compile tools/apply_ssh_log_fixes.py
  python3 tools/apply_ssh_log_fixes.py
  git rm core-src/qr_helper.sh

  python3 -m py_compile \
    src/prepare.py core-src/sub_center.py core-src/sync_agent.py \
    core-src/backup_manager.py tests/conformance.py \
    tests/extract_manager_library.py tests/build_slot_fixture.py
  bash -n vvv-install.sh
  bash -n core-src/bootstrap.sh
  bash -n core-src/host.sh
  bash -n core-src/center_install.sh
  bash -n core-src/register_sync.sh
  bash -n core-src/vvv_manager.sh
  bash -n core-src/rclone_manager.sh
  sh -n core-src/landing.sh

  ! grep -RniE 'qrencode|qr_helper|二维码' vvv-install.sh core-src src/prepare.py
  grep -q '1. 安装订阅中心+中转主机（含自身代理）' core-src/bootstrap.sh
  grep -q '2. 仅安装订阅中心（含自身代理）' core-src/bootstrap.sh
  grep -q '3. 仅安装中转主机（含自身代理）' core-src/bootstrap.sh
  grep -q '4. 仅安装中转副机（通过主机代理）' core-src/bootstrap.sh
  grep -q '5. 仅安装直连代理' core-src/bootstrap.sh
  grep -q 'base_url="https://${domain}:${public_port}"' core-src/center_install.sh
  ! grep -q 'http://${public_ip}' core-src/center_install.sh
  ! grep -q 'mode=ip' core-src/center_install.sh
  grep -q 'pinSHA256' core-src/sub_center.py
  grep -q 'basicConstraints=critical,CA:FALSE' core-src/host.sh
  grep -q '当前版本只支持全新安装' vvv-install.sh
) >"$LOG" 2>&1
rc=$?
set -e

if [[ $rc -eq 0 ]]; then
  git add README.md vvv-install.sh core-src src tests .github/workflows/validate.yml
  git commit -m 'Require HTTPS, fix v2rayNG HY2, remove QR, and reorder roles'
  git push
else
  git reset --hard HEAD
  mkdir -p validation
  cp "$LOG" validation/ssh-log-fix-validation.log
  printf '\nstatus=failure\n' >> validation/ssh-log-fix-validation.log
  git add validation/ssh-log-fix-validation.log
  git commit -m 'Record direct SSH fix publish failure'
  git push
  exit "$rc"
fi

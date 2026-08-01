#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
LOG=/tmp/quick-ssh-log-fix-validation.log

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
  python3 tests/conformance.py

  work="$(mktemp -d /tmp/vvv-quick-render.XXXXXX)"
  cp core-src/host.sh "$work/host.sh"
  cp core-src/landing.sh "$work/landing.sh"
  cp core-src/center_install.sh "$work/center.sh"
  python3 src/prepare.py "$work/host.sh" "$work/landing.sh" "$work/center.sh"
  bash -n "$work/host.sh"
  sh -n "$work/landing.sh"
  bash -n "$work/center.sh"
  ! grep -RniE 'qrencode|qr_helper|二维码' "$work"
  grep -q 'v2rayNG.txt' "$work/host.sh"
  grep -q 'pinSHA256' "$work/host.sh"
  grep -q 'basicConstraints=critical,CA:FALSE' "$work/host.sh"
  grep -q 'base_url="https://${domain}:${public_port}"' "$work/center.sh"
  ! grep -q 'http://${public_ip}' "$work/center.sh"
  ! grep -q 'mode=ip' "$work/center.sh"
  rm -rf "$work"
) >"$LOG" 2>&1
rc=$?
set -e

if [[ $rc -eq 0 ]]; then
  git rm -f --ignore-unmatch \
    tools/apply_ssh_log_fixes.py \
    tools/fix_ssh_log_transformer_anchor.py \
    tools/run_final_ssh_fix.sh \
    tools/run_quick_ssh_fix.sh \
    .github/workflows/apply-ssh-log-fixes.yml \
    .github/workflows/apply-ssh-log-fixes-final.yml \
    .github/workflows/run-final-ssh-fix.yml \
    .github/workflows/publish-quick-ssh-fix.yml \
    .github/workflows/ssh-fix-actions-probe.yml \
    ssh-fix-actions-probe.txt \
    ssh-fix-publisher-started.txt \
    validation/ssh-log-fix-validation.log
  git add -A
  git commit -m 'Require HTTPS, fix v2rayNG HY2, remove QR, and reorder roles'
  git push
else
  git reset --hard HEAD
  mkdir -p validation
  cp "$LOG" validation/ssh-log-fix-validation.log
  printf '\nstatus=failure\n' >> validation/ssh-log-fix-validation.log
  git add validation/ssh-log-fix-validation.log
  git commit -m 'Record source-level SSH log fix validation failure'
  git push
  exit "$rc"
fi

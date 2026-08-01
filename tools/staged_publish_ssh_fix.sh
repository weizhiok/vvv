#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

git config user.name github-actions[bot]
git config user.email 41898282+github-actions[bot]@users.noreply.github.com
mkdir -p validation

stage_commit(){
  local stage="$1"
  printf '%s\n' "$stage" > validation/ssh-fix-publisher-stage.txt
  git add validation/ssh-fix-publisher-stage.txt
  git commit -m "SSH fix publisher: ${stage}"
  git push
}

fail_stage(){
  local rc=$? line=${BASH_LINENO[0]:-unknown}
  set +e
  printf 'failure line=%s rc=%s\n' "$line" "$rc" > validation/ssh-fix-publisher-stage.txt
  git add validation/ssh-fix-publisher-stage.txt
  git commit -m 'Record staged SSH fix publisher failure'
  git push
  exit "$rc"
}
trap fail_stage ERR

python3 -m py_compile tools/fix_ssh_log_transformer_anchor.py tools/apply_ssh_log_fixes.py
python3 tools/fix_ssh_log_transformer_anchor.py
python3 -m py_compile tools/apply_ssh_log_fixes.py
python3 tools/apply_ssh_log_fixes.py
git rm core-src/qr_helper.sh
stage_commit 'stage-1-transform-applied'

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
stage_commit 'stage-2-source-syntax-passed'

work="$(mktemp -d /tmp/vvv-staged-render.XXXXXX)"
cp core-src/host.sh "$work/host.sh"
cp core-src/landing.sh "$work/landing.sh"
cp core-src/center_install.sh "$work/center.sh"
python3 src/prepare.py "$work/host.sh" "$work/landing.sh" "$work/center.sh"
bash -n "$work/host.sh"
sh -n "$work/landing.sh"
bash -n "$work/center.sh"
! grep -RniE 'qrencode|qr_helper|二维码' "$work"
grep -q 'base_url="https://${domain}:${public_port}"' "$work/center.sh"
! grep -q 'http://${public_ip}' "$work/center.sh"
! grep -q 'mode=ip' "$work/center.sh"
grep -q 'v2rayNG.txt' "$work/host.sh"
grep -q 'pinSHA256' "$work/host.sh"
grep -q 'basicConstraints=critical,CA:FALSE' "$work/host.sh"
rm -rf "$work"
stage_commit 'stage-3-final-render-passed'

git rm -f --ignore-unmatch \
  tools/apply_ssh_log_fixes.py \
  tools/fix_ssh_log_transformer_anchor.py \
  tools/run_final_ssh_fix.sh \
  tools/run_quick_ssh_fix.sh \
  tools/publish_production_ssh_fix.sh \
  tools/direct_publish_ssh_fix.sh \
  tools/staged_publish_ssh_fix.sh \
  .github/workflows/apply-ssh-log-fixes.yml \
  .github/workflows/apply-ssh-log-fixes-final.yml \
  .github/workflows/run-final-ssh-fix.yml \
  .github/workflows/publish-quick-ssh-fix.yml \
  .github/workflows/publish-production-ssh-fix.yml \
  .github/workflows/ssh-fix-actions-probe.yml \
  ssh-fix-actions-probe.txt \
  ssh-fix-publisher-started.txt \
  direct-publisher-started.txt \
  validation/ssh-log-fix-validation.log \
  validation/ssh-fix-publisher-stage.txt

git add -A
git commit -m 'Require HTTPS, fix v2rayNG HY2, remove QR, and reorder roles'
git push
trap - ERR

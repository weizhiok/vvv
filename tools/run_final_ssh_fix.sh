#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
LOG=/tmp/final-ssh-log-fix-validation.log

git config user.name github-actions[bot]
git config user.email 41898282+github-actions[bot]@users.noreply.github.com

set +e
(
  set -Eeuxo pipefail
  echo 'final-ssh-log-fix-runner=2'
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

  sudo apt-get update -qq
  sudo apt-get install -y -qq curl unzip jq openssl iproute2
  XRAY_VERSION="$(sed -n 's/^XRAY_FALLBACK_VERSION="\([^"]*\)"/\1/p' core-src/host.sh | head -n1)"
  SING_VERSION="$(sed -n 's/^SING_BOX_FALLBACK_VERSION="\([^"]*\)"/\1/p' core-src/host.sh | head -n1)"
  test -n "$XRAY_VERSION"
  test -n "$SING_VERSION"
  curl -fL --retry 5 --retry-all-errors "https://github.com/XTLS/Xray-core/releases/download/v${XRAY_VERSION}/Xray-linux-64.zip" -o /tmp/xray.zip
  unzip -q /tmp/xray.zip xray -d /tmp/xray-unpack
  install -m755 /tmp/xray-unpack/xray /tmp/xray
  curl -fL --retry 5 --retry-all-errors "https://github.com/SagerNet/sing-box/releases/download/v${SING_VERSION}/sing-box-${SING_VERSION}-linux-amd64.tar.gz" -o /tmp/sing-box.tgz
  tar -xzf /tmp/sing-box.tgz -C /tmp
  install -m755 "/tmp/sing-box-${SING_VERSION}-linux-amd64/sing-box" /tmp/sing-box

  chmod +x tests/final_runtime_validation.sh
  tests/final_runtime_validation.sh /tmp/xray /tmp/sing-box
  ! git grep -nE 'qrencode|qr_helper' -- \
    ':!tools/apply_ssh_log_fixes.py' \
    ':!tools/fix_ssh_log_transformer_anchor.py' \
    ':!tools/run_final_ssh_fix.sh' \
    ':!.github/workflows/apply-ssh-log-fixes.yml' \
    ':!.github/workflows/apply-ssh-log-fixes-final.yml' \
    ':!.github/workflows/run-final-ssh-fix.yml'
  ! git grep -n '二维码' -- vvv-install.sh core-src src/prepare.py
  grep -q 'base_url="https://${domain}:${public_port}"' core-src/center_install.sh
  ! grep -q 'http://${public_ip}' core-src/center_install.sh
  grep -q 'pinSHA256' core-src/sub_center.py
  grep -q 'basicConstraints=critical,CA:FALSE' core-src/host.sh
) >"$LOG" 2>&1
rc=$?
set -e

if [[ $rc -eq 0 ]]; then
  git rm -f --ignore-unmatch \
    tools/apply_ssh_log_fixes.py \
    tools/fix_ssh_log_transformer_anchor.py \
    tools/run_final_ssh_fix.sh \
    .github/workflows/apply-ssh-log-fixes.yml \
    .github/workflows/apply-ssh-log-fixes-final.yml \
    .github/workflows/run-final-ssh-fix.yml \
    .github/workflows/ssh-fix-actions-probe.yml \
    ssh-fix-actions-probe.txt \
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
  git commit -m 'Record final SSH log fix validation failure'
  git push
  exit "$rc"
fi

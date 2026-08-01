#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

git config user.name github-actions[bot]
git config user.email 41898282+github-actions[bot]@users.noreply.github.com
mkdir -p validation

python3 -m py_compile tools/fix_ssh_log_transformer_anchor.py tools/apply_ssh_log_fixes.py
python3 tools/fix_ssh_log_transformer_anchor.py
python3 -m py_compile tools/apply_ssh_log_fixes.py
python3 tools/apply_ssh_log_fixes.py
rm -f core-src/qr_helper.sh

printf '%s\n' transform-applied > validation/final-production-stage.txt
git add validation/final-production-stage.txt
git commit validation/final-production-stage.txt -m 'Final SSH fix: transform applied'
git push origin HEAD:main

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

work="$(mktemp -d /tmp/vvv-second-commit-render.XXXXXX)"
trap 'rm -rf "$work"' EXIT
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

printf '%s\n' validated-production > validation/final-production-stage.txt
git add validation/final-production-stage.txt README.md vvv-install.sh core-src src tests .github/workflows/validate.yml
git commit -m 'Require HTTPS, fix v2rayNG HY2, remove QR, and reorder roles'
git push origin HEAD:main

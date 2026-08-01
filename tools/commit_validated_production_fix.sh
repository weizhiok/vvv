#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

git config user.name github-actions[bot]
git config user.email 41898282+github-actions[bot]@users.noreply.github.com

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

git add README.md vvv-install.sh core-src src tests .github/workflows/validate.yml
git commit -m 'Require HTTPS, fix v2rayNG HY2, remove QR, and reorder roles'
git push

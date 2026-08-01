#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

git config user.name github-actions[bot]
git config user.email 41898282+github-actions[bot]@users.noreply.github.com
mkdir -p validation

mark(){
  printf '%s\n' "$1" > validation/final-production-stage.txt
  git add validation/final-production-stage.txt
  git commit validation/final-production-stage.txt -m "Final SSH fix trace: $1"
  git push origin HEAD:main
}

python3 -m py_compile tools/fix_ssh_log_transformer_anchor.py tools/apply_ssh_log_fixes.py
python3 tools/fix_ssh_log_transformer_anchor.py
python3 -m py_compile tools/apply_ssh_log_fixes.py
python3 tools/apply_ssh_log_fixes.py
rm -f core-src/qr_helper.sh
mark transform-applied

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
mark source-syntax-passed

work="$(mktemp -d /tmp/vvv-traced-render.XXXXXX)"
trap 'rm -rf "$work"' EXIT
cp core-src/host.sh "$work/host.sh"
cp core-src/landing.sh "$work/landing.sh"
cp core-src/center_install.sh "$work/center.sh"
python3 src/prepare.py "$work/host.sh" "$work/landing.sh" "$work/center.sh"
mark prepare-rendered

bash -n "$work/host.sh"
sh -n "$work/landing.sh"
bash -n "$work/center.sh"
mark rendered-syntax-passed

! grep -RniE 'qrencode|qr_helper|二维码' "$work"
mark no-qr-passed

grep -q 'base_url="https://${domain}:${public_port}"' "$work/center.sh"
mark https-base-passed

! grep -q 'http://${public_ip}' "$work/center.sh"
mark no-http-passed

! grep -q 'mode=ip' "$work/center.sh"
mark no-ip-mode-passed

grep -q 'v2rayNG.txt' "$work/host.sh"
mark v2rayng-file-passed

grep -q 'pinSHA256' "$work/host.sh"
mark pin-passed

grep -q 'basicConstraints=critical,CA:FALSE' "$work/host.sh"
mark leaf-cert-passed

printf '%s\n' validated-production > validation/final-production-stage.txt
git add validation/final-production-stage.txt README.md vvv-install.sh core-src src tests .github/workflows/validate.yml
git commit -m 'Require HTTPS, fix v2rayNG HY2, remove QR, and reorder roles'
git push origin HEAD:main

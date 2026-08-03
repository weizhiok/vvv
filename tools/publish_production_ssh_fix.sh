#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

git config user.name github-actions[bot]
git config user.email 41898282+github-actions[bot]@users.noreply.github.com

git rm -rf --ignore-unmatch \
  core-src/__pycache__ \
  src/__pycache__ \
  tests/__pycache__ \
  tools/__pycache__

git fetch --no-tags --depth=1 origin main
git checkout FETCH_HEAD -- \
  tools/publish_production_ssh_fix.sh \
  .github/workflows/publish-production-ssh-fix.yml

git add -A
git diff --cached --check
git commit -m 'Remove generated Python bytecode'
git push origin HEAD

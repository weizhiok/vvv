#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

git config user.name github-actions[bot]
git config user.email 41898282+github-actions[bot]@users.noreply.github.com

git rm -f --ignore-unmatch \
  core-src/__pycache__/client_adapters.cpython-312.pyc \
  core-src/__pycache__/sub_center.cpython-312.pyc \
  core-src/__pycache__/sync_agent.cpython-312.pyc \
  src/__pycache__/validate_embedded_python.cpython-312.pyc \
  tests/__pycache__/conformance.cpython-312.pyc

git fetch --no-tags --depth=1 origin main
git checkout FETCH_HEAD -- \
  tools/publish_production_ssh_fix.sh \
  .github/workflows/publish-production-ssh-fix.yml

git add -A
git diff --cached --check
git commit -m 'Remove generated Python bytecode'
git push origin HEAD

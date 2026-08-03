#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
LOG=/tmp/vvv-landing-direct-publish.log

git config user.name github-actions[bot]
git config user.email 41898282+github-actions[bot]@users.noreply.github.com

set +e
(
  set -Eeuxo pipefail
  python3 -m py_compile tools/apply_landing_direct_role.py
  python3 tools/apply_landing_direct_role.py

  python3 -m py_compile \
    src/prepare.py \
    core-src/sub_center.py \
    core-src/sync_agent.py \
    core-src/backup_manager.py \
    core-src/client_adapters.py \
    core-src/client_upgrade_engine.py \
    core-src/client_local_renderer.py \
    core-src/restore_manager.py \
    core-src/node_probe.py \
    tests/landing_direct_role_validation.py
  bash -n core-src/bootstrap.sh
  bash -n core-src/host.sh
  sh -n core-src/landing.sh
  bash -n core-src/register_sync.sh
  bash -n core-src/vvv_manager.sh
  bash -n tests/final_runtime_validation.sh
  python3 core-src/client_adapters.py >/dev/null
  python3 tests/landing_direct_role_validation.py

  rm -f tools/apply_landing_direct_role.py tools/publish_landing_direct_role.sh
  cat > .github/workflows/publish-quick-ssh-fix.yml <<'EOF_WORKFLOW'
name: Validate role and client isolation
on:
  pull_request:
    paths:
      - 'vvv-install.sh'
      - 'core-src/**'
      - 'src/**'
      - 'tests/**'
      - '.github/workflows/publish-quick-ssh-fix.yml'
  push:
    branches: [main]
    paths:
      - 'vvv-install.sh'
      - 'core-src/**'
      - 'src/**'
      - 'tests/**'
      - '.github/workflows/publish-quick-ssh-fix.yml'
permissions:
  contents: read
jobs:
  validate-role-and-client-isolation:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - name: Validate syntax and role contracts
        run: |
          python3 -m py_compile \
            core-src/sub_center.py \
            core-src/sync_agent.py \
            core-src/client_adapters.py \
            core-src/client_upgrade_engine.py \
            core-src/client_local_renderer.py \
            core-src/backup_manager.py \
            core-src/restore_manager.py \
            tests/landing_direct_role_validation.py \
            tests/client_upgrade_isolation_validation.py
          bash -n core-src/bootstrap.sh
          bash -n core-src/host.sh
          sh -n core-src/landing.sh
          bash -n core-src/register_sync.sh
          bash -n core-src/vvv_manager.sh
          python3 core-src/client_adapters.py >/dev/null
          python3 tests/landing_direct_role_validation.py
          python3 tests/client_upgrade_isolation_validation.py
EOF_WORKFLOW

  git add -A
  git diff --cached --check
  git commit -m 'Add combined landing and direct proxy role'
  git push origin HEAD
) >"$LOG" 2>&1
rc=$?
set -e
cat "$LOG"
exit "$rc"

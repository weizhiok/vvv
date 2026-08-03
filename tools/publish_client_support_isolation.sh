#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

LOG=/tmp/vvv-client-support-isolation.log

git config user.name github-actions[bot]
git config user.email 41898282+github-actions[bot]@users.noreply.github.com

set +e
(
  set -Eeuxo pipefail
  python3 -m py_compile tools/apply_client_support_isolation.py
  python3 tools/apply_client_support_isolation.py

  python3 -m py_compile \
    src/prepare.py \
    core-src/sub_center.py \
    core-src/sync_agent.py \
    core-src/backup_manager.py \
    core-src/client_adapters.py \
    core-src/adapter_manager.py \
    core-src/client_upgrade_engine.py \
    core-src/client_local_renderer.py \
    core-src/restore_manager.py \
    core-src/diagnostic_report.py \
    core-src/node_probe.py \
    tests/conformance.py \
    tests/client_upgrade_isolation_validation.py \
    tests/extract_manager_library.py \
    tests/build_slot_fixture.py

  bash -n vvv-install.sh
  bash -n core-src/bootstrap.sh
  bash -n core-src/host.sh
  bash -n core-src/center_install.sh
  bash -n core-src/register_sync.sh
  bash -n core-src/vvv_manager.sh
  bash -n core-src/rclone_manager.sh
  bash -n core-src/center_transport.sh
  bash -n core-src/center_manager.sh
  sh -n core-src/landing.sh

  python3 core-src/client_adapters.py >/dev/null
  python3 tests/conformance.py
  python3 tests/client_upgrade_isolation_validation.py

  work="$(mktemp -d /tmp/vvv-client-render.XXXXXX)"
  cp core-src/host.sh "$work/host.sh"
  cp core-src/landing.sh "$work/landing.sh"
  cp core-src/center_install.sh "$work/center.sh"
  python3 src/prepare.py "$work/host.sh" "$work/landing.sh" "$work/center.sh"
  bash -n "$work/host.sh"
  sh -n "$work/landing.sh"
  bash -n "$work/center.sh"
  grep -q '升级客户端支持' "$work/host.sh"
  grep -q '升级客户端支持' "$work/landing.sh"
  grep -q 'client_upgrade_engine.py' vvv-install.sh
  grep -q 'client_support_handoff' core-src/sub_center.py
  rm -rf "$work"
) >"$LOG" 2>&1
rc=$?
set -e

if [[ $rc -ne 0 ]]; then
  cat "$LOG"
  exit "$rc"
fi

cat "$LOG"
git rm -f tools/apply_client_support_isolation.py tools/publish_client_support_isolation.sh
git add -A
git commit -m 'Isolate client support upgrades from proxy maintenance'
git push

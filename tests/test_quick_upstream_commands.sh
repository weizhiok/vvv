#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="$ROOT/core-src/host.sh"

for file in \
  "$ROOT/core-src/bootstrap.sh" \
  "$ROOT/core-src/host.sh" \
  "$ROOT/core-src/center_manager.sh" \
  "$ROOT/core-src/hy2_port_hop.sh"; do
  bash -n "$file"
done
sh -n "$ROOT/core-src/landing.sh"

for command in addhttp addhttps addsocks addsocks5; do
  grep -Fq -- "/usr/local/sbin/${command}" "$HOST"
  grep -Fq -- "--add-upstream ${command}" "$HOST"
done

if grep -Fq -- "apphttps" "$HOST"; then
  echo "obsolete apphttps command still exists" >&2
  exit 1
fi

grep -Fq -- "必须使用英文单引号包住完整参数" "$HOST"
grep -Fq -- "导致 | 被 Bash 当成管道符" "$HOST"
grep -Fq -- "pipe_count" "$HOST"
grep -Fq -- "prepare_add_or_overwrite_upstream" "$HOST"
grep -Fq -- "英国动态IP代理|gw.dataimpulse.com:10000:用户名:密码" "$HOST"

INSTALLER="$ROOT/vvv-install.sh"
for module in client_package_renderer.py hy2_port_hop.py hy2_port_hop.sh; do
  grep -Fq -- "$module" "$INSTALLER"
done
if grep -Fq -- 'jq -r ".ports"' "$ROOT/core-src/bootstrap.sh"; then
  echo "bootstrap must not require jq before dependencies are installed" >&2
  exit 1
fi
grep -Fq -- "var/lib/vvv-sub/node-order.json" "$ROOT/core-src/restore_manager.py"
grep -Fq -- "云厂商安全组及外部防火墙放行 UDP" "$HOST"

echo "Quick upstream, installer, restore, and firewall contract tests passed."

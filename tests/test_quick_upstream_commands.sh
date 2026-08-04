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
  grep -Fq "/usr/local/sbin/${command}" "$HOST"
  grep -Fq "--add-upstream ${command}" "$HOST"
  grep -Fq "${command} '线路名称|主机:端口:用户名:密码'" <(
    sed "s/\${command_name}/${command}/g" "$HOST"
  ) || true
  if grep -Fq "apphttps" "$HOST"; then
    echo "obsolete apphttps command still exists" >&2
    exit 1
  fi
done

grep -Fq "必须使用英文单引号包住完整参数" "$HOST"
grep -Fq "导致 | 被 Bash 当成管道符" "$HOST"
grep -Fq "pipe_count" "$HOST"
grep -Fq "prepare_add_or_overwrite_upstream" "$HOST"
grep -Fq "英国动态IP代理|gw.dataimpulse.com:10000:用户名:密码" "$HOST"

echo "Quick upstream command contract tests passed."

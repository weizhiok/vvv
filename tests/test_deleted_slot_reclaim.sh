#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="$ROOT/core-src/host.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

python3 - "$HOST" "$TMP/functions.sh" <<'PY'
import re,sys
from pathlib import Path
src=Path(sys.argv[1]).read_text(encoding='utf-8')
out=[]
for name,next_name in (
    ('allocate_vless_slot','release_orphaned_vless_slots'),
    ('allocate_hy2_slot','release_orphaned_hy2_slots'),
):
    m=re.search(rf'{name}\(\) \{{.*?\n\}}\n\n(?={next_name}\(\))',src,re.S)
    if not m:
        raise SystemExit(f'cannot extract {name}')
    out.append(m.group(0))
Path(sys.argv[2]).write_text('\n'.join(out),encoding='utf-8')

required=(
    'allocate_vless_slot "$relay_id"',
    'allocate_hy2_slot "$relay_id"',
    'allocate_vless_slot "$upstream_id"',
)
for token in required:
    if token not in src:
        raise SystemExit(f'missing explicit reclaim target: {token}')
PY

STATE_FILE="$TMP/state.json"
cat > "$STATE_FILE" <<'JSON'
{
  "vless": {"reserve_users": [
    {"slot":"v01","uuid":"uuid-free","email":"free@example","local_port":11001,"assigned_id":null,"retired":false},
    {"slot":"v02","uuid":"uuid-old","email":"old@example","local_port":11002,"assigned_id":"upstream-old","retired":false}
  ]},
  "hy2": {"reserve_users": [
    {"slot":"h01","name":"hy-free","password":"pass-free","local_port":12001,"assigned_id":null,"retired":false},
    {"slot":"h02","name":"hy-old","password":"pass-old","local_port":12002,"assigned_id":"relay-old","retired":false}
  ]}
}
JSON

fail(){ echo "FAIL: $*" >&2; return 1; }
# shellcheck source=/dev/null
source "$TMP/functions.sh"

allocate_vless_slot upstream-old
[[ "$ALLOC_VLESS_SLOT" == v02 && "$ALLOC_VLESS_UUID" == uuid-old ]] || {
  echo "did not reclaim deleted VLESS slot" >&2; exit 1;
}
allocate_vless_slot upstream-new
[[ "$ALLOC_VLESS_SLOT" == v01 && "$ALLOC_VLESS_UUID" == uuid-free ]] || {
  echo "new VLESS line did not use genuinely free slot" >&2; exit 1;
}
allocate_hy2_slot relay-old
[[ "$ALLOC_HY2_SLOT" == h02 && "$ALLOC_HY2_PASSWORD" == pass-old ]] || {
  echo "did not reclaim deleted HY2 slot" >&2; exit 1;
}
allocate_hy2_slot relay-new
[[ "$ALLOC_HY2_SLOT" == h01 && "$ALLOC_HY2_PASSWORD" == pass-free ]] || {
  echo "new HY2 line did not use genuinely free slot" >&2; exit 1;
}

# 重加同一线路时，把原槽位再次标记为相同 ID，不得产生重复占用。
REBUILT="$TMP/rebuilt.json"
jq '(.vless.reserve_users[]|select(.slot=="v02")).assigned_id="upstream-old" |
    (.hy2.reserve_users[]|select(.slot=="h02")).assigned_id="relay-old"' \
   "$STATE_FILE" > "$REBUILT"
jq -e '[.vless.reserve_users[]?.assigned_id|select(.!=null)] as $ids | ($ids|length)==($ids|unique|length)' "$REBUILT" >/dev/null
jq -e '[.hy2.reserve_users[]?.assigned_id|select(.!=null)] as $ids | ($ids|length)==($ids|unique|length)' "$REBUILT" >/dev/null

echo 'PASS deleted relay slots can be safely reclaimed by the same logical line'

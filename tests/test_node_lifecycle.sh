#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="$ROOT/core-src/host.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

python3 - "$HOST" "$TMP/functions.sh" <<'PY'
import re
import sys
from pathlib import Path

src = Path(sys.argv[1]).read_text(encoding='utf-8')
out = []
for name, next_name in (
    ('allocate_vless_slot', 'release_orphaned_vless_slots'),
    ('allocate_hy2_slot', 'release_orphaned_hy2_slots'),
    ('validate_slot_references', 'prepare_add_or_overwrite'),
    ('build_vless_slot_configs', 'install_vless_slot_service'),
    ('build_hy2_slot_configs', 'sync_hy2_slot_services'),
    ('build_delete_candidate', 'perform_delete'),
):
    match = re.search(rf'(?ms)^{name}\(\) \{{.*?(?=^{next_name}\(\) \{{)', src)
    if not match:
        raise SystemExit(f'cannot extract {name}')
    out.append(match.group(0).rstrip())
Path(sys.argv[2]).write_text('\n\n'.join(out) + '\n', encoding='utf-8')

for name, next_name, required, forbidden in (
    ('verify_xray_runtime', 'verify_sing_runtime', 'build_vless_slot_configs "$STATE_FILE"', '.relays[]?.vless.test_socks_port'),
    ('verify_sing_runtime', 'activate_initial_state', 'build_hy2_slot_configs "$STATE_FILE"', 'assigned_id!=null'),
):
    match = re.search(rf'(?ms)^{name}\(\) \{{.*?(?=^{next_name}\(\) \{{)', src)
    if not match:
        raise SystemExit(f'cannot extract {name}')
    body = match.group(0)
    if required not in body:
        raise SystemExit(f'{name} does not use the active slot builder')
    if forbidden in body:
        raise SystemExit(f'{name} still validates stale slot markers: {forbidden}')

required_tokens = (
    'validate_slot_references "$candidate_state"',
    'build_delete_candidate vps "$relay_id" "$candidate"',
    'build_delete_candidate upstream "$upstream_id" "$candidate"',
    '/etc/systemd/system/multi-user.target.wants/vvv-vless-slot@*.service',
    '/etc/systemd/system/multi-user.target.wants/vvv-hy2-slot@*.service',
)
for token in required_tokens:
    if token not in src:
        raise SystemExit(f'missing lifecycle transaction token: {token}')
PY

STATE_FILE="$TMP/state.json"
HY2_LIMIT_MBPS=50
TMP_FILES=()
fail(){ echo "FAIL: $*" >&2; return 1; }
# shellcheck source=/dev/null
source "$TMP/functions.sh"

cat > "$STATE_FILE" <<'JSON'
{
  "schema": 3,
  "role": "japan-hub",
  "protocol_mode": "dual",
  "public_ip": "198.51.100.10",
  "listen_port": 443,
  "sni": "www.softbank.jp",
  "hy2_limit_mbps": 50,
  "vless": {
    "reserve_users": [
      {"slot":"v01","uuid":"uuid-relay","email":"relay@example","local_port":22001,"assigned_id":"relay-a"},
      {"slot":"v02","uuid":"uuid-temp-vps","email":"temp-vps@example","local_port":22002,"assigned_id":"temp-vps"},
      {"slot":"v03","uuid":"uuid-upstream","email":"upstream@example","local_port":22003,"assigned_id":"upstream-a"},
      {"slot":"v04","uuid":"uuid-temp-up","email":"temp-up@example","local_port":22004,"assigned_id":"temp-up"},
      {"slot":"v05","uuid":"uuid-free","email":"free@example","local_port":22005,"assigned_id":null,"retired":false}
    ]
  },
  "hy2": {
    "reserve_users": [
      {"slot":"h01","name":"hy-relay","password":"pass-relay","local_port":21001,"assigned_id":"relay-a"},
      {"slot":"h02","name":"hy-temp","password":"pass-temp","local_port":21002,"assigned_id":"temp-vps"},
      {"slot":"h03","name":"hy-free","password":"pass-free","local_port":21003,"assigned_id":null,"retired":false}
    ]
  },
  "relays": [
    {
      "id":"relay-a","name":"SG-A","remote_ip":"203.0.113.20","remote_port":553,
      "vless":{"reserve_slot":"v01","outbound_uuid":"remote-uuid","remote_reality":{"public_key":"pub","short_id":"abcd"}},
      "hy2":{"reserve_slot":"h01","outbound_password":"remote-pass","outbound_obfs_password":"remote-obfs","outbound_server_name":"remote.example","remote_certificate_public_key_sha256":"pin"}
    }
  ],
  "upstream_relays": [
    {
      "id":"upstream-a","name":"Dynamic-A","reserve_slot":"v03","proxy_protocol":"http",
      "host":"proxy.example","port":8080,"username":"user","password":"password"
    }
  ],
  "temporary_nodes": [
    {
      "id":"temp-vps","name":"Temp VPS","source_type":"vps","source_id":"relay-a",
      "vless":{"reserve_slot":"v02"},"hy2":{"reserve_slot":"h02"}
    },
    {
      "id":"temp-up","name":"Temp Upstream","source_type":"upstream","source_id":"upstream-a",
      "vless":{"reserve_slot":"v04"},"hy2":null
    }
  ]
}
JSON

validate_slot_references "$STATE_FILE" >/dev/null

BEFORE_V="$TMP/before-v"; BEFORE_H="$TMP/before-h"
build_vless_slot_configs "$STATE_FILE" "$BEFORE_V"
build_hy2_slot_configs "$STATE_FILE" "$BEFORE_H"
for slot in v01 v02 v03 v04; do [[ -f "$BEFORE_V/$slot.json" ]] || { echo "missing initial $slot" >&2; exit 1; }; done
for slot in h01 h02; do [[ -f "$BEFORE_H/$slot.json" ]] || { echo "missing initial $slot" >&2; exit 1; }; done

DELETE_VPS="$TMP/delete-vps.json"
DEPENDENT_VPS="$(build_delete_candidate vps relay-a "$DELETE_VPS")"
[[ "$DEPENDENT_VPS" == "Temp VPS" ]] || { echo "dependent VPS temp was not reported" >&2; exit 1; }
validate_slot_references "$DELETE_VPS" >/dev/null
jq -e '.relays|length==0' "$DELETE_VPS" >/dev/null
jq -e '[.temporary_nodes[].id] == ["temp-up"]' "$DELETE_VPS" >/dev/null
jq -e '.vless.reserve_users[]|select(.slot=="v01")|.assigned_id=="relay-a"' "$DELETE_VPS" >/dev/null
jq -e '.hy2.reserve_users[]|select(.slot=="h01")|.assigned_id=="relay-a"' "$DELETE_VPS" >/dev/null
jq -e '.vless.reserve_users[]|select(.slot=="v02")|(.assigned_id==null and .retired==true and .retired_id=="temp-vps")' "$DELETE_VPS" >/dev/null
jq -e '.hy2.reserve_users[]|select(.slot=="h02")|(.assigned_id==null and .retired==true and .retired_id=="temp-vps")' "$DELETE_VPS" >/dev/null

AFTER_V="$TMP/after-v"; AFTER_H="$TMP/after-h"
build_vless_slot_configs "$DELETE_VPS" "$AFTER_V"
build_hy2_slot_configs "$DELETE_VPS" "$AFTER_H"
[[ -f "$AFTER_V/v03.json" && -f "$AFTER_V/v04.json" ]] || { echo "remaining upstream slots disappeared" >&2; exit 1; }
[[ ! -e "$AFTER_V/v01.json" && ! -e "$AFTER_V/v02.json" ]] || { echo "deleted VPS slots still active" >&2; exit 1; }
[[ -z "$(find "$AFTER_H" -name '*.json' -print -quit)" ]] || { echo "deleted HY2 slots still active" >&2; exit 1; }

cp "$DELETE_VPS" "$STATE_FILE"
allocate_vless_slot relay-a
[[ "$ALLOC_VLESS_SLOT" == v01 && "$ALLOC_VLESS_UUID" == uuid-relay ]] || { echo "VPS VLESS tombstone was not reclaimable" >&2; exit 1; }
allocate_hy2_slot relay-a
[[ "$ALLOC_HY2_SLOT" == h01 && "$ALLOC_HY2_PASSWORD" == pass-relay ]] || { echo "VPS HY2 tombstone was not reclaimable" >&2; exit 1; }

DELETE_UP="$TMP/delete-upstream.json"
DEPENDENT_UP="$(build_delete_candidate upstream upstream-a "$DELETE_UP")"
[[ "$DEPENDENT_UP" == "Temp Upstream" ]] || { echo "dependent upstream temp was not reported" >&2; exit 1; }
validate_slot_references "$DELETE_UP" >/dev/null
jq -e '.upstream_relays|length==0' "$DELETE_UP" >/dev/null
jq -e '.temporary_nodes|length==0' "$DELETE_UP" >/dev/null
jq -e '.vless.reserve_users[]|select(.slot=="v03")|.assigned_id=="upstream-a"' "$DELETE_UP" >/dev/null
jq -e '.vless.reserve_users[]|select(.slot=="v04")|(.assigned_id==null and .retired==true and .retired_id=="temp-up")' "$DELETE_UP" >/dev/null

cp "$DELETE_UP" "$STATE_FILE"
allocate_vless_slot upstream-a
[[ "$ALLOC_VLESS_SLOT" == v03 && "$ALLOC_VLESS_UUID" == uuid-upstream ]] || { echo "upstream tombstone was not reclaimable" >&2; exit 1; }
allocate_vless_slot brand-new
[[ "$ALLOC_VLESS_SLOT" == v05 && "$ALLOC_VLESS_UUID" == uuid-free ]] || { echo "new line did not use a genuinely free slot" >&2; exit 1; }

echo 'PASS complete create-delete-readd lifecycle for VPS, upstream, temporary, VLESS and HY2 nodes'

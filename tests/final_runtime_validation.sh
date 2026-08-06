#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_XRAY="${1:?usage: final_runtime_validation.sh XRAY SING_BOX}"
TEST_SING_BOX="${2:?usage: final_runtime_validation.sh XRAY SING_BOX}"
WORK="$(mktemp -d /tmp/vvv-final-test.XXXXXX)"
PIDS=()
cleanup(){
  local pid
  for pid in "${PIDS[@]:-}"; do [[ -z "$pid" ]] || kill "$pid" >/dev/null 2>&1 || true; done
  wait >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT
log(){ printf '\n===== %s =====\n' "$*"; }

log 'Source conformance and syntax'
python3 -m py_compile \
  "$ROOT/src/prepare.py" \
  "$ROOT/core-src/sub_center.py" \
  "$ROOT/core-src/sync_agent.py" \
  "$ROOT/core-src/backup_manager.py" \
  "$ROOT/core-src/client_adapters.py" \
  "$ROOT/core-src/client_package_renderer.py" \
  "$ROOT/core-src/hy2_port_hop.py" \
  "$ROOT/core-src/adapter_manager.py" \
  "$ROOT/core-src/restore_manager.py" \
  "$ROOT/core-src/diagnostic_report.py" \
  "$ROOT/core-src/node_probe.py" \
  "$ROOT/core-src/client_local_renderer.py" \
  "$ROOT/core-src/client_upgrade_engine.py" \
  "$ROOT/tests/landing_direct_role_validation.py" \
  "$ROOT/tests/conformance.py" \
  "$ROOT/tests/extract_manager_library.py" \
  "$ROOT/tests/build_slot_fixture.py"
python3 "$ROOT/tests/conformance.py"
python3 "$ROOT/tests/landing_direct_role_validation.py"
bash -n "$ROOT/core-src/bootstrap.sh"
bash -n "$ROOT/core-src/host.sh"
bash -n "$ROOT/core-src/center_install.sh"
bash -n "$ROOT/core-src/register_sync.sh"
bash -n "$ROOT/core-src/vvv_manager.sh"
bash -n "$ROOT/core-src/rclone_manager.sh"
bash -n "$ROOT/core-src/center_transport.sh"
bash -n "$ROOT/core-src/center_manager.sh"
sh -n "$ROOT/core-src/landing.sh"
bash -n "$ROOT/tests/hy2_bandwidth_compat_validation.sh"
python3 "$ROOT/core-src/client_adapters.py" >/dev/null

log 'Render final installers'
cp "$ROOT/core-src/host.sh" "$WORK/host.sh"
cp "$ROOT/core-src/landing.sh" "$WORK/landing.sh"
cp "$ROOT/core-src/center_install.sh" "$WORK/center.sh"
python3 "$ROOT/src/prepare.py" "$WORK/host.sh" "$WORK/landing.sh" "$WORK/center.sh"
bash -n "$WORK/host.sh"
sh -n "$WORK/landing.sh"
bash -n "$WORK/center.sh"
! grep -q '^xray_dynamic_parts()' "$WORK/host.sh"
! grep -q '^xray_hot_apply()' "$WORK/host.sh"
! grep -q '^xray_dynamic_parts()' "$WORK/host.sh"
! grep -q '^xray_hot_apply()' "$WORK/host.sh"
! grep -q '^xray_dynamic_parts()' "$WORK/host.sh"
! grep -q '^xray_hot_apply()' "$WORK/host.sh"
! grep -q '^xray_dynamic_parts()' "$WORK/host.sh"
! grep -q '^xray_hot_apply()' "$WORK/host.sh"
python3 "$ROOT/tests/extract_manager_library.py" "$WORK/host.sh" "$WORK/manager-lib.sh"
bash -n "$WORK/manager-lib.sh"
source "$WORK/manager-lib.sh"
XRAY="$TEST_XRAY"
SING_BOX="$TEST_SING_BOX"
HY2_LIMIT_MBPS=50
install -m755 "$ROOT/core-src/client_package_renderer.py" "$WORK/client_package_renderer.py"
install -m755 "$ROOT/core-src/client_adapters.py" "$WORK/client_adapters.py"
CLIENT_PACKAGE_RENDERER="$WORK/client_package_renderer.py"
CLIENT_ADAPTER="$WORK/client_adapters.py"

log 'Build fixed main and isolated slot fixtures'
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -sha256 -nodes -days 7 \
  -subj '/CN=jp-hy2.jp-relay.local' -addext 'subjectAltName=DNS:jp-hy2.jp-relay.local' \
  -addext 'basicConstraints=critical,CA:FALSE' -addext 'keyUsage=critical,digitalSignature' \
  -addext 'extendedKeyUsage=serverAuth' \
  -keyout "$WORK/server.key" -out "$WORK/server.crt" >/dev/null 2>&1
openssl x509 -in "$WORK/server.crt" -noout -text | grep -q 'CA:FALSE'
parse_x25519_keys "$($XRAY x25519)"
export VVV_AUDIT_REALITY_PRIVATE="$GENERATED_PRIVATE_KEY"
export VVV_AUDIT_REALITY_PUBLIC="$GENERATED_PUBLIC_KEY"
parse_x25519_keys "$($XRAY x25519)"
export VVV_AUDIT_REMOTE_PUBLIC="$GENERATED_PUBLIC_KEY"
export VVV_AUDIT_CERT_PATH="$WORK/server.crt"
export VVV_AUDIT_KEY_PATH="$WORK/server.key"
export AUDIT_DIR="$WORK"
python3 "$ROOT/tests/build_slot_fixture.py"

build_xray_config "$WORK/state-empty.json" "$WORK/xray-empty.json"
build_xray_config "$WORK/state-active.json" "$WORK/xray-active.json"
cmp "$WORK/xray-empty.json" "$WORK/xray-active.json"
build_sing_config "$WORK/state-empty.json" "$WORK/sing-empty.json"
build_sing_config "$WORK/state-active.json" "$WORK/sing-active.json"
cmp "$WORK/sing-empty.json" "$WORK/sing-active.json"
build_xray_config "$WORK/state-temp.json" "$WORK/xray-temp.json"
cmp "$WORK/xray-active.json" "$WORK/xray-temp.json"
build_sing_config "$WORK/state-temp.json" "$WORK/sing-temp.json"
cmp "$WORK/sing-active.json" "$WORK/sing-temp.json"
mkdir -p "$WORK/vless-empty" "$WORK/vless-active" "$WORK/vless-temp" "$WORK/hy2-empty" "$WORK/hy2-active" "$WORK/hy2-temp"
build_vless_slot_configs "$WORK/state-empty.json" "$WORK/vless-empty"
build_vless_slot_configs "$WORK/state-active.json" "$WORK/vless-active"
build_hy2_slot_configs "$WORK/state-empty.json" "$WORK/hy2-empty"
build_hy2_slot_configs "$WORK/state-active.json" "$WORK/hy2-active"
build_vless_slot_configs "$WORK/state-temp.json" "$WORK/vless-temp"
build_hy2_slot_configs "$WORK/state-temp.json" "$WORK/hy2-temp"
mkdir -p "$WORK/client-files"
generate_client_files "$WORK/state-active.json" "" "$WORK/client-files" direct >/dev/null
[[ -s "$WORK/client-files/Quantumult-X.conf" ]]
[[ -s "$WORK/client-files/Loon.conf" ]]
[[ -s "$WORK/client-files/Shadowrocket.txt" ]]
[[ -s "$WORK/client-files/Clash-Verge-Rev.yaml" ]]
[[ -e "$WORK/client-files/NekoBoxForAndroid.txt" ]]
[[ ! -s "$WORK/client-files/NekoBoxForAndroid.txt" ]]
[[ -s "$WORK/client-files/NekoBoxForAndroid-基础URI.txt" ]]
[[ -e "$WORK/client-files/Loon-Import.txt" && ! -s "$WORK/client-files/Loon-Import.txt" ]]
[[ -s "$WORK/client-files/NekoBoxForAndroid.yaml" ]]
[[ -s "$WORK/client-files/NekoBoxForAndroid-SN.txt" ]]
[[ ! -e "$WORK/client-files/Loon-Shadowrocket.txt" ]]
! find "$WORK/client-files" -maxdepth 1 -type f -iname '*v2*' | grep -q .
grep -q '^vless=' "$WORK/client-files/Quantumult-X.conf"
grep -q 'Hysteria2' "$WORK/client-files/Loon.conf"
grep -q 'server-ports="24443,30000-30031"' "$WORK/client-files/Loon.conf"
grep -q 'hop-interval=30' "$WORK/client-files/Loon.conf"
grep -q 'download-bandwidth=50' "$WORK/client-files/Loon.conf"
grep -q '^hysteria2://' "$WORK/client-files/Shadowrocket.txt"
grep -q 'fastopen=1' "$WORK/client-files/Shadowrocket.txt"
grep -q 'upmbps=30' "$WORK/client-files/Shadowrocket.txt"
grep -q 'downmbps=50' "$WORK/client-files/Shadowrocket.txt"
grep -q 'mport=24443,30000-30031' "$WORK/client-files/Shadowrocket.txt"
grep -q 'type: hysteria2' "$WORK/client-files/Clash-Verge-Rev.yaml"
grep -q 'ports: "24443,30000-30031"' "$WORK/client-files/Clash-Verge-Rev.yaml"
grep -q 'hop-interval: "20-30"' "$WORK/client-files/Clash-Verge-Rev.yaml"
grep -q 'up: "30 Mbps"' "$WORK/client-files/Clash-Verge-Rev.yaml"
grep -q 'down: "50 Mbps"' "$WORK/client-files/Clash-Verge-Rev.yaml"
! grep -q '^proxy-groups:' "$WORK/client-files/Clash-Verge-Rev.yaml"
! grep -q '^rules:' "$WORK/client-files/Clash-Verge-Rev.yaml"
grep -q '【NekoBox For Android】' "$WORK/client-files/客户端节点.txt"
grep -q '^sn://vmess?' "$WORK/client-files/NekoBoxForAndroid-SN.txt"
grep -q '^sn://hysteria?' "$WORK/client-files/NekoBoxForAndroid-SN.txt"
grep -q 'type: hysteria2' "$WORK/client-files/NekoBoxForAndroid.yaml"
grep -q 'ports: "24443,30000-30031"' "$WORK/client-files/NekoBoxForAndroid.yaml"
grep -q 'hop-interval: 30' "$WORK/client-files/NekoBoxForAndroid.yaml"
! grep -q 'hop-interval: "20-30"' "$WORK/client-files/NekoBoxForAndroid.yaml"
grep -q 'up: "30 Mbps"' "$WORK/client-files/NekoBoxForAndroid.yaml"
grep -q 'down: "50 Mbps"' "$WORK/client-files/NekoBoxForAndroid.yaml"
grep -q '^hy2://' "$WORK/client-files/NekoBoxForAndroid-基础URI.txt"
grep -q 'mport=24443,30000-30031' "$WORK/client-files/NekoBoxForAndroid-基础URI.txt"
! find "$WORK/client-files" -type f -name '*二维码*' | grep -q .
[[ "$(find "$WORK/vless-empty" -type f | wc -l)" -eq 0 ]]
[[ "$(find "$WORK/hy2-empty" -type f | wc -l)" -eq 0 ]]
[[ "$(find "$WORK/vless-active" -name '*.json' | wc -l)" -eq 2 ]]
[[ "$(find "$WORK/hy2-active" -name '*.json' | wc -l)" -eq 1 ]]
[[ -f "$WORK/vless-active/v01.json" && -f "$WORK/vless-active/v02.json" && -f "$WORK/hy2-active/h01.json" ]]
[[ "$(find "$WORK/vless-temp" -name '*.json' | wc -l)" -eq 4 ]]
[[ "$(find "$WORK/hy2-temp" -name '*.json' | wc -l)" -eq 2 ]]
grep -q 'proxy.example.com' "$WORK/vless-temp/v04.json"
grep -q '203.0.113.20' "$WORK/vless-temp/v03.json"
grep -q '203.0.113.20' "$WORK/hy2-temp/h02.json"
grep -q '"ignore_client_bandwidth": false' "$WORK/sing-active.json"

log 'Run actual core configuration checks'
"$XRAY" run -test -format=json -config "$WORK/xray-active.json"
for cfg in "$WORK"/vless-active/*.json; do "$XRAY" run -test -format=json -config "$cfg"; done
"$SING_BOX" check -c "$WORK/sing-active.json"
for cfg in "$WORK"/hy2-active/*.json; do "$SING_BOX" check -c "$cfg"; done
bash "$ROOT/tests/hy2_bandwidth_compat_validation.sh" "$SING_BOX"
sha256sum "$WORK/xray-empty.json" "$WORK/xray-active.json"
sha256sum "$WORK/sing-empty.json" "$WORK/sing-active.json"

log 'Verify deleted credentials are retired'
jq '.relays=[] | .upstream_relays=[]' "$WORK/state-active.json" > "$WORK/state-deleted.json"
release_orphaned_vless_slots "$WORK/state-deleted.json"
release_orphaned_hy2_slots "$WORK/state-deleted.json"
[[ "$(jq -r '.vless.reserve_users[0].assigned_id' "$WORK/state-deleted.json")" == relay-audit ]]
[[ "$(jq -r '.vless.reserve_users[1].assigned_id' "$WORK/state-deleted.json")" == upstream-audit ]]
[[ "$(jq -r '.hy2.reserve_users[0].assigned_id' "$WORK/state-deleted.json")" == relay-audit ]]
STATE_FILE="$WORK/state-deleted.json"
allocate_vless_slot; [[ "$ALLOC_VLESS_SLOT" == v03 ]]
allocate_hy2_slot; [[ "$ALLOC_HY2_SLOT" == h02 ]]
mkdir -p "$WORK/vless-deleted" "$WORK/hy2-deleted"
build_vless_slot_configs "$STATE_FILE" "$WORK/vless-deleted"
build_hy2_slot_configs "$STATE_FILE" "$WORK/hy2-deleted"
[[ "$(find "$WORK/vless-deleted" -type f | wc -l)" -eq 0 ]]
[[ "$(find "$WORK/hy2-deleted" -type f | wc -l)" -eq 0 ]]

log 'Start live main processes'
"$XRAY" run -format=json -config "$WORK/xray-active.json" > "$WORK/xray-main.log" 2>&1 &
XRAY_MAIN_PID=$!; PIDS+=("$XRAY_MAIN_PID")
"$SING_BOX" run -c "$WORK/sing-active.json" > "$WORK/sing-main.log" 2>&1 &
SING_MAIN_PID=$!; PIDS+=("$SING_MAIN_PID")
sleep 3
kill -0 "$XRAY_MAIN_PID"; kill -0 "$SING_MAIN_PID"
XRAY_START="$(awk '{print $22}' "/proc/${XRAY_MAIN_PID}/stat")"
SING_START="$(awk '{print $22}' "/proc/${SING_MAIN_PID}/stat")"
ss -H -lntp 'sport = :24443' | grep -q "pid=${XRAY_MAIN_PID}"
ss -H -lnup 'sport = :24443' | grep -q "pid=${SING_MAIN_PID}"

log 'Start, stop, and replace isolated slots'
"$XRAY" run -format=json -config "$WORK/vless-active/v01.json" > "$WORK/v01.log" 2>&1 & V1_PID=$!; PIDS+=("$V1_PID")
"$XRAY" run -format=json -config "$WORK/vless-active/v02.json" > "$WORK/v02.log" 2>&1 & V2_PID=$!; PIDS+=("$V2_PID")
"$SING_BOX" run -c "$WORK/hy2-active/h01.json" > "$WORK/h01.log" 2>&1 & H1_PID=$!; PIDS+=("$H1_PID")
sleep 3
kill -0 "$V1_PID"; kill -0 "$V2_PID"; kill -0 "$H1_PID"
ss -H -lntp 'sport = :22001' | grep -q "pid=${V1_PID}"
ss -H -lntp 'sport = :22002' | grep -q "pid=${V2_PID}"
ss -H -lntp 'sport = :21001' | grep -q "pid=${H1_PID}"
[[ "$(awk '{print $22}' "/proc/${XRAY_MAIN_PID}/stat")" == "$XRAY_START" ]]
[[ "$(awk '{print $22}' "/proc/${SING_MAIN_PID}/stat")" == "$SING_START" ]]
kill "$V1_PID" "$H1_PID"; wait "$V1_PID" "$H1_PID" 2>/dev/null || true
sleep 2
kill -0 "$XRAY_MAIN_PID"; kill -0 "$SING_MAIN_PID"
[[ "$(awk '{print $22}' "/proc/${XRAY_MAIN_PID}/stat")" == "$XRAY_START" ]]
[[ "$(awk '{print $22}' "/proc/${SING_MAIN_PID}/stat")" == "$SING_START" ]]
"$XRAY" run -format=json -config "$WORK/vless-active/v01.json" > "$WORK/v01-new.log" 2>&1 & V1_NEW_PID=$!; PIDS+=("$V1_NEW_PID")
"$SING_BOX" run -c "$WORK/hy2-active/h01.json" > "$WORK/h01-new.log" 2>&1 & H1_NEW_PID=$!; PIDS+=("$H1_NEW_PID")
sleep 3
kill -0 "$V1_NEW_PID"; kill -0 "$H1_NEW_PID"
[[ "$(awk '{print $22}' "/proc/${XRAY_MAIN_PID}/stat")" == "$XRAY_START" ]]
[[ "$(awk '{print $22}' "/proc/${SING_MAIN_PID}/stat")" == "$SING_START" ]]

log 'Final result'
echo "main_xray_pid=${XRAY_MAIN_PID} start=${XRAY_START}"
echo "main_sing_box_pid=${SING_MAIN_PID} start=${SING_START}"
echo 'Main Xray and sing-box remained unchanged across slot start, stop, and replacement.'
echo 'FINAL RUNTIME VALIDATION PASSED'

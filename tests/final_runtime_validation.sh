#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
export USER="${USER:-runner}"
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d /tmp/vvv-final-test.XXXXXX)"
cleanup(){
  [[ -z "${XRAY_PID:-}" ]] || kill "$XRAY_PID" >/dev/null 2>&1 || true
  [[ -z "${SUB_PID:-}" ]] || sudo kill "$SUB_PID" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT
log(){ printf '\n===== %s =====\n' "$*"; }

log 'Transform sources and check syntax'
cp "$ROOT/core-src/host.sh" "$WORK/host.sh"
cp "$ROOT/core-src/landing.sh" "$WORK/landing.sh"
cp "$ROOT/core-src/center_install.sh" "$WORK/center.sh"
python3 "$ROOT/src/prepare.py" "$WORK/host.sh" "$WORK/landing.sh" "$WORK/center.sh"
bash -n "$WORK/host.sh"
sh -n "$WORK/landing.sh"
for f in \
  "$ROOT/core-src/center_install.sh" \
  "$ROOT/core-src/register_sync.sh" \
  "$ROOT/core-src/vvv_manager.sh" \
  "$ROOT/core-src/rclone_manager.sh" \
  "$ROOT/core-src/qr_helper.sh" \
  "$ROOT/src/bootstrap.sh" \
  "$ROOT/vvv-install.sh"; do
  bash -n "$f"
done
python3 -m py_compile \
  "$ROOT/src/prepare.py" \
  "$ROOT/core-src/sub_center.py" \
  "$ROOT/core-src/sync_agent.py" \
  "$ROOT/core-src/backup_manager.py"
awk "/<<'PY_BUILD_XRAY'/{f=1;next}/^PY_BUILD_XRAY$/{f=0}f" "$WORK/host.sh" > "$WORK/build_xray.py"
awk "/<<'PY_BUILD_SING'/{f=1;next}/^PY_BUILD_SING$/{f=0}f" "$WORK/host.sh" > "$WORK/build_sing.py"
awk "/<<'PY_HY2_SLOTS'/{f=1;next}/^PY_HY2_SLOTS$/{f=0}f" "$WORK/host.sh" > "$WORK/build_slots.py"
python3 -m py_compile "$WORK/build_xray.py" "$WORK/build_sing.py" "$WORK/build_slots.py"
grep -q 'reserve_users' "$WORK/host.sh"
grep -q 'api-in' "$WORK/host.sh"
grep -q 'api adrules' "$WORK/host.sh"
grep -q 'Xray/sing-box 主进程均未重启' "$WORK/host.sh"
! grep -q 'salamander-password={loon_q' "$WORK/host.sh"
! grep -q 'salamander-password={loon_q' "$WORK/landing.sh"

log 'Download current Xray and sing-box'
xapi="$(curl -fsSL https://api.github.com/repos/XTLS/Xray-core/releases/latest)"
xurl="$(jq -r '.assets[]|select(.name=="Xray-linux-64.zip")|.browser_download_url' <<<"$xapi")"
curl -fsSL "$xurl" -o "$WORK/xray.zip"
unzip -q "$WORK/xray.zip" xray -d "$WORK"
chmod +x "$WORK/xray"
sapi="$(curl -fsSL https://api.github.com/repos/SagerNet/sing-box/releases/latest)"
surl="$(jq -r '.assets[]|select(.name|test("linux-amd64.tar.gz$"))|.browser_download_url' <<<"$sapi" | head -n1)"
curl -fsSL "$surl" -o "$WORK/sing.tgz"
sing_member="$(tar -tzf "$WORK/sing.tgz" | awk '/\/sing-box$/{print; exit}')"
[[ -n "$sing_member" ]] || { echo 'sing-box archive has no executable' >&2; exit 1; }
tar -xzf "$WORK/sing.tgz" -C "$WORK" "$sing_member"
cp "$WORK/$sing_member" "$WORK/sing-box"
chmod +x "$WORK/sing-box"
"$WORK/xray" version | head -n2
"$WORK/sing-box" version | head -n3

log 'Build sample state and real proxy configurations'
mkdir -p "$WORK/tls" "$WORK/slots"
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
  -nodes -days 2 -subj /CN=hy2.local -addext subjectAltName=DNS:hy2.local \
  -keyout "$WORK/tls/key.pem" -out "$WORK/tls/cert.pem" >/dev/null 2>&1
kout="$("$WORK/xray" x25519)"
priv="$(sed -n 's/^PrivateKey:[[:space:]]*//p' <<<"$kout" | head -n1)"
pub="$(sed -n -E -e 's/^Password( \(PublicKey\))?:[[:space:]]*//p' -e 's/^PublicKey:[[:space:]]*//p' <<<"$kout" | head -n1)"
pin="$(openssl x509 -in "$WORK/tls/cert.pem" -pubkey -noout | openssl pkey -pubin -outform der | openssl dgst -sha256 -binary | base64 -w0)"
python3 - "$WORK/state.json" "$WORK/old-state.json" "$WORK/tls/cert.pem" "$WORK/tls/key.pem" "$priv" "$pub" "$pin" <<'PY'
import copy,json,sys
state_path,old_path,cert,key,priv,pub,pin=sys.argv[1:]
state={
 'schema':3,'role':'japan-hub','protocol_mode':'dual','public_ip':'127.0.0.1','listen_port':18443,
 'sni':'www.softbank.jp','direct_base_name':'JP-127.0.0.1:18443','xray_version':'test','sing_box_version':'test','relay_manager_enabled':True,
 'vless':{'reality':{'private_key':priv,'public_key':pub,'short_id':'0123456789abcdef'},
          'direct_user':{'uuid':'11111111-1111-4111-8111-111111111111','email':'direct@test'},
          'reserve_users':[
           {'slot':'v01','uuid':'22222222-2222-4222-8222-222222222222','email':'reserve-01@test','assigned_id':'relay1'},
           {'slot':'v02','uuid':'33333333-3333-4333-8333-333333333333','email':'reserve-02@test','assigned_id':'up1'},
           {'slot':'v03','uuid':'44444444-4444-4444-8444-444444444444','email':'reserve-03@test','assigned_id':None}]},
 'hy2':{'server_name':'hy2.local','certificate_path':cert,'key_path':key,'certificate_fingerprint':'AA','certificate_pin_hex':'aa',
        'certificate_public_key_sha256':pin,'obfs_password':'obfs_main','direct_user':{'name':'direct-hy2','password':'direct_pass'},
        'reserve_users':[
         {'slot':'h01','name':'reserve-h01','password':'slot_pass','local_port':21101,'assigned_id':'relay1'},
         {'slot':'h02','name':'reserve-h02','password':'unused_pass','local_port':21102,'assigned_id':None}]},
 'relays':[{'id':'relay1','name':'SG-Relay','remote_ip':'203.0.113.10','remote_port':2443,
   'vless':{'client_uuid':'22222222-2222-4222-8222-222222222222','client_email':'reserve-01@test','reserve_slot':'v01',
            'outbound_uuid':'55555555-5555-4555-8555-555555555555','remote_reality':{'private_key':priv,'public_key':pub,'short_id':'abcdef0123456789'},
            'outbound_tag':'vless-out-relay1','test_inbound_tag':'vless-test-relay1','test_socks_port':18081},
   'hy2':{'client_user':'reserve-h01','client_password':'slot_pass','reserve_slot':'h01','outbound_password':'remote_pass',
          'outbound_obfs_password':'remote_obfs','outbound_tag':'hy2-out-relay1','test_inbound_tag':'hy2-test-relay1','test_socks_port':21101,
          'outbound_server_name':'landing.local','remote_certificate_public_key_sha256':pin,'remote_certificate_fingerprint':'AA',
          'remote_certificate_pin_hex':'aa','remote_certificate_pem':'','remote_key_pem':''}}],
 'upstream_relays':[{'id':'up1','name':'HTTP-Upstream','kind':'upstream','proxy_protocol':'http','protocol_label':'HTTP/HTTPS',
                     'host':'127.0.0.1','port':18090,'username':'user','password':'pass',
                     'client_uuid':'33333333-3333-4333-8333-333333333333','client_email':'reserve-02@test','reserve_slot':'v02',
                     'outbound_tag':'upstream-out-up1','test_inbound_tag':'upstream-test-up1','test_socks_port':20081,'last_exit_ip':''}]
}
with open(state_path,'w') as f: json.dump(state,f,indent=2); f.write('\n')
old=copy.deepcopy(state); old['relays']=[]; old['upstream_relays']=[]
for u in old['vless']['reserve_users']: u['assigned_id']=None
for u in old['hy2']['reserve_users']: u['assigned_id']=None
with open(old_path,'w') as f: json.dump(old,f,indent=2); f.write('\n')
PY
python3 "$WORK/build_xray.py" "$WORK/state.json" "$WORK/xray.json"
python3 "$WORK/build_xray.py" "$WORK/old-state.json" "$WORK/xray-old.json"
"$WORK/xray" run -test -format=json -config "$WORK/xray.json"
python3 "$WORK/build_sing.py" "$WORK/state.json" "$WORK/sing.json" 50
"$WORK/sing-box" check -c "$WORK/sing.json"
python3 "$WORK/build_slots.py" "$WORK/state.json" "$WORK/slots" 50
[[ -s "$WORK/slots/h01.json" ]]
"$WORK/sing-box" check -c "$WORK/slots/h01.json"
jq -e '.outbounds[]|select(.tag=="hy2-slot-h01" and .type=="socks")' "$WORK/sing.json" >/dev/null
jq -e '.routing.rules[]|select(.ruleTag=="block-unused-vless")' "$WORK/xray.json" >/dev/null

log 'Exercise Xray API hot loading without process restart'
"$WORK/xray" run -format=json -config "$WORK/xray-old.json" >"$WORK/xray.log" 2>&1 & XRAY_PID=$!
for _ in $(seq 1 30); do
  "$WORK/xray" api inbounduser --server=127.0.0.1:10085 -tag=in-vless-reality >/dev/null 2>&1 && break
  sleep .2
done
kill -0 "$XRAY_PID"
python3 - "$WORK/xray.json" "$WORK/in.json" "$WORK/out.json" <<'PY'
import json,sys
cfg=json.load(open(sys.argv[1]))
ins=[x for x in cfg['inbounds'] if x.get('tag') not in ('in-vless-reality','api-in')]
outs=[x for x in cfg['outbounds'] if x.get('tag') not in ('direct','blocked')]
json.dump({'inbounds':ins},open(sys.argv[2],'w')); json.dump({'outbounds':outs},open(sys.argv[3],'w'))
PY
"$WORK/xray" api adi --server=127.0.0.1:10085 "$WORK/in.json"
"$WORK/xray" api ado --server=127.0.0.1:10085 "$WORK/out.json"
"$WORK/xray" api adrules --server=127.0.0.1:10085 "$WORK/xray.json"
kill -0 "$XRAY_PID"
ss -lnt | grep -q ':18081 '
ss -lnt | grep -q ':20081 '

log 'Validate subscriptions, short paths and event backups'
sudo install -d -m700 /etc/vvv-sub /etc/vvv /etc/jp-relay /var/lib/vvv-sub/hosts /var/lib/vvv-sub/output /var/lib/vvv-sub/backups /usr/local/lib/vvv
sudo install -m755 "$ROOT/core-src/backup_manager.py" /usr/local/lib/vvv/backup_manager.py
sudo install -m755 "$ROOT/core-src/qr_helper.sh" /usr/local/lib/vvv/qr_helper.sh
sudo install -m600 "$WORK/state.json" /etc/jp-relay/state.json
sudo python3 - "$WORK/state.json" <<'PY'
import json,time,sys,os
state=json.load(open(sys.argv[1]))
json.dump({'host_id':'test','role':'center-relay','state':state,'meta':{},'last_seen_ts':time.time()},open('/var/lib/vvv-sub/hosts/test.json','w'))
json.dump({'schema':2,'mode':'ip','base_url':'http://127.0.0.1:18091','listen_host':'127.0.0.1','listen_port':18091,
           'subscription_token':'token123','master_token':'master123','recovery_password':'test-password'},open('/etc/vvv-sub/config.json','w'))
json.dump({'hosts':[]},open('/var/lib/vvv-sub/registry.json','w'))
os.chmod('/etc/vvv-sub/config.json',0o600)
PY
sudo python3 "$ROOT/core-src/sub_center.py" regenerate
sudo grep -q 'salamander-password=obfs_main' /var/lib/vvv-sub/output/loon
! sudo grep -q 'salamander-password="' /var/lib/vvv-sub/output/loon
sudo base64 -d /var/lib/vvv-sub/output/v2rayng > "$WORK/v2.txt"
grep -q '^hy2://' "$WORK/v2.txt"
! grep -q 'pinSHA256' "$WORK/v2.txt"
sudo python3 "$ROOT/core-src/sub_center.py" serve >"$WORK/sub.log" 2>&1 & SUB_PID=$!
for _ in $(seq 1 30); do curl -fsS http://127.0.0.1:18091/health >/dev/null 2>&1 && break; sleep .2; done
for p in c qx ln sr v2; do curl -fsS "http://127.0.0.1:18091/r/token123/$p" >/dev/null; done
for p in clash quantumultx loon shadowrocket v2rayng; do
  [[ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:18091/r/token123/$p")" == 404 ]]
done
source "$ROOT/core-src/qr_helper.sh"
vvv_print_qr 'https://example.test/r/token/sr' > "$WORK/qr.txt"
head -n1 "$WORK/qr.txt" | grep -q $'\033[47m'
sudo python3 "$ROOT/core-src/backup_manager.py" create validation --force >/dev/null
sudo test -s /var/lib/vvv-sub/backups/latest.enc
sudo openssl enc -d -aes-256-cbc -pbkdf2 -pass pass:test-password -in /var/lib/vvv-sub/backups/latest.enc | tar -tzf - > "$WORK/backup-list"
grep -q 'etc/vvv-sub/config.json' "$WORK/backup-list"
! grep -R -qE 'OnCalendar=.*backup|vvv-backup-pull|pull-backup|/api/v1/backup' "$ROOT/core-src" "$ROOT/src" "$ROOT/vvv-install.sh"

log 'All runtime validations passed'
cat <<EOF
status=ok
xray_config=ok
xray_api_hot_load=ok
xray_pid_preserved=ok
sing_box_main_config=ok
sing_box_slot_config=ok
subscription_renderers=ok
short_paths=ok
old_paths_404=ok
qr_top_border=ok
encrypted_event_backup=ok
cross_host_backup=absent
EOF

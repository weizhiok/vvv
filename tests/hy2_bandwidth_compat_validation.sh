#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SING_BOX="${1:?用法：hy2_bandwidth_compat_validation.sh SING_BOX}"
WORK="$(mktemp -d /tmp/vvv-hy2-compat.XXXXXX)"
PIDS=()

cleanup() {
  local pid
  for pid in "${PIDS[@]:-}"; do
    [[ -z "$pid" ]] || kill "$pid" >/dev/null 2>&1 || true
  done
  wait >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

free_tcp_port() {
  python3 - <<'PY_PORT'
import socket
sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
sock.bind(('127.0.0.1',0))
print(sock.getsockname()[1])
sock.close()
PY_PORT
}

free_udp_port() {
  python3 - <<'PY_PORT'
import socket
sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
sock.bind(('127.0.0.1',0))
print(sock.getsockname()[1])
sock.close()
PY_PORT
}

wait_tcp() {
  python3 - "$1" "$2" <<'PY_WAIT'
import socket,sys,time
port=int(sys.argv[1]); timeout=float(sys.argv[2]); end=time.time()+timeout
while time.time()<end:
    try:
        with socket.create_connection(('127.0.0.1',port),timeout=.2):
            raise SystemExit(0)
    except OSError:
        time.sleep(.1)
raise SystemExit(1)
PY_WAIT
}

HY2_PORT="$(free_udp_port)"
HTTP_PORT="$(free_tcp_port)"
mkdir -p "$WORK/web"
printf '%s\n' 'VVV-HY2-COMPAT-OK' > "$WORK/web/probe.txt"

openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -sha256 -nodes -days 2 \
  -subj '/CN=hy2.test.local' -addext 'subjectAltName=DNS:hy2.test.local' \
  -keyout "$WORK/server.key" -out "$WORK/server.crt" >/dev/null 2>&1

cat > "$WORK/server.json" <<EOF_SERVER
{
  "log": {"level": "debug", "timestamp": true},
  "inbounds": [{
    "type": "hysteria2",
    "tag": "hy2-in",
    "listen": "127.0.0.1",
    "listen_port": ${HY2_PORT},
    "up_mbps": 50,
    "down_mbps": 50,
    "ignore_client_bandwidth": false,
    "users": [{"name": "compat-user", "password": "compat-password"}],
    "obfs": {"type": "salamander", "password": "compat-obfs"},
    "tls": {
      "enabled": true,
      "server_name": "hy2.test.local",
      "alpn": ["h3"],
      "min_version": "1.3",
      "certificate_path": "$WORK/server.crt",
      "key_path": "$WORK/server.key"
    }
  }],
  "outbounds": [{"type": "direct", "tag": "direct"}],
  "route": {"final": "direct", "auto_detect_interface": true}
}
EOF_SERVER

"$SING_BOX" check -c "$WORK/server.json"
python3 -m http.server "$HTTP_PORT" --bind 127.0.0.1 --directory "$WORK/web" >"$WORK/http.log" 2>&1 &
HTTP_PID=$!; PIDS+=("$HTTP_PID")
wait_tcp "$HTTP_PORT" 5
"$SING_BOX" run -c "$WORK/server.json" >"$WORK/server.log" 2>&1 &
SERVER_PID=$!; PIDS+=("$SERVER_PID")
sleep 1
kill -0 "$SERVER_PID"

run_client() {
  local mode="$1" local_port client_cfg client_log bandwidth_json=''
  local_port="$(free_tcp_port)"
  client_cfg="$WORK/client-${mode}.json"
  client_log="$WORK/client-${mode}.log"
  if [[ "$mode" == with-bandwidth ]]; then
    bandwidth_json=', "up_mbps": 50, "down_mbps": 50'
  fi
  cat > "$client_cfg" <<EOF_CLIENT
{
  "log": {"level": "debug", "timestamp": true},
  "inbounds": [{"type": "mixed", "tag": "local", "listen": "127.0.0.1", "listen_port": ${local_port}}],
  "outbounds": [{
    "type": "hysteria2",
    "tag": "proxy",
    "server": "127.0.0.1",
    "server_port": ${HY2_PORT},
    "password": "compat-password"${bandwidth_json},
    "obfs": {"type": "salamander", "password": "compat-obfs"},
    "tls": {"enabled": true, "server_name": "hy2.test.local", "insecure": true, "alpn": ["h3"], "min_version": "1.3"}
  }],
  "route": {"final": "proxy", "auto_detect_interface": true}
}
EOF_CLIENT
  "$SING_BOX" check -c "$client_cfg"
  "$SING_BOX" run -c "$client_cfg" >"$client_log" 2>&1 &
  local client_pid=$!
  PIDS+=("$client_pid")
  wait_tcp "$local_port" 5 || {
    cat "$client_log" >&2
    return 1
  }
  local result
  result="$(curl -fsS --socks5-hostname "127.0.0.1:${local_port}" --connect-timeout 5 --max-time 20 "http://127.0.0.1:${HTTP_PORT}/probe.txt" || true)"
  if [[ "$result" != VVV-HY2-COMPAT-OK ]]; then
    echo "HY2 ${mode} 真实握手失败。" >&2
    tail -n 100 "$WORK/server.log" >&2 || true
    tail -n 100 "$client_log" >&2 || true
    return 1
  fi
  kill "$client_pid" >/dev/null 2>&1 || true
  wait "$client_pid" >/dev/null 2>&1 || true
  echo "PASS HY2 ${mode} real handshake"
}

run_client without-bandwidth
run_client with-bandwidth
echo 'HY2 BANDWIDTH COMPATIBILITY VALIDATION PASSED'

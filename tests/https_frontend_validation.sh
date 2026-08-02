#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
CADDY="${1:?usage: https_frontend_validation.sh CADDY CERTBOT}"
CERTBOT="${2:?usage: https_frontend_validation.sh CADDY CERTBOT}"
WORK="$(mktemp -d /tmp/vvv-frontend-validation.XXXXXX)"
PIDS=()
cleanup(){
  local pid
  for pid in "${PIDS[@]:-}"; do [[ -z "$pid" ]] || kill "$pid" >/dev/null 2>&1 || true; done
  wait >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

"$CADDY" version
"$CERTBOT" --version
"$CERTBOT" certonly --help all | grep -q -- '--ip-address'
"$CERTBOT" certonly --help all | grep -q -- '--preferred-profile'

cat > "$WORK/http.Caddyfile" <<'EOF'
{
  admin off
  auto_https off
}

:18081 {
  @allowed path /Abc12345 /health
  handle @allowed {
    respond "http-ok" 200
  }

  respond 404
}
EOF
"$CADDY" validate --config "$WORK/http.Caddyfile" --adapter caddyfile

cat > "$WORK/tunnel.Caddyfile" <<'EOF'
{
  admin off
  auto_https off
}

http://127.0.0.1:18082 {
  @allowed path /Abc12345 /health
  handle @allowed {
    respond "tunnel-ok" 200
  }

  respond 404
}
EOF
"$CADDY" validate --config "$WORK/tunnel.Caddyfile" --adapter caddyfile

cat > "$WORK/domain.Caddyfile" <<'EOF'
{
  admin off
  auto_https disable_redirects
}

sub.example.com:18443 {
  tls {
    issuer acme {
      disable_tlsalpn_challenge
    }
  }

  @allowed path /Abc12345 /health
  handle @allowed {
    respond "domain-ok" 200
  }

  respond 404
}
EOF
"$CADDY" validate --config "$WORK/domain.Caddyfile" --adapter caddyfile

openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
  -sha256 -nodes -days 2 -subj '/CN=127.0.0.1' \
  -addext 'subjectAltName=IP:127.0.0.1' \
  -addext 'basicConstraints=critical,CA:FALSE' \
  -addext 'keyUsage=critical,digitalSignature' \
  -addext 'extendedKeyUsage=serverAuth' \
  -keyout "$WORK/ip.key" -out "$WORK/ip.crt" >/dev/null 2>&1

cat > "$WORK/ip.Caddyfile" <<EOF
{
  admin off
  auto_https off
  default_sni 127.0.0.1
}

http://127.0.0.1:18080 {
  respond /acme-ready "ready" 200
  respond 404
}

https://127.0.0.1:18444 {
  tls $WORK/ip.crt $WORK/ip.key

  @allowed path /Abc12345 /health
  handle @allowed {
    respond "ip-ok" 200
  }

  respond 404
}
EOF
"$CADDY" validate --config "$WORK/ip.Caddyfile" --adapter caddyfile
"$CADDY" run --config "$WORK/ip.Caddyfile" --adapter caddyfile >"$WORK/ip-caddy.log" 2>&1 &
PIDS+=("$!")
for _ in $(seq 1 30); do
  curl -fsS --connect-timeout 1 --max-time 2 http://127.0.0.1:18080/acme-ready | grep -qx ready && \
  curl -fsS --connect-timeout 1 --max-time 2 --cacert "$WORK/ip.crt" https://127.0.0.1:18444/Abc12345 | grep -qx ip-ok && break
  sleep 1
done
curl -fsS --connect-timeout 2 --max-time 4 http://127.0.0.1:18080/acme-ready | grep -qx ready
curl -fsS --connect-timeout 2 --max-time 4 --cacert "$WORK/ip.crt" https://127.0.0.1:18444/Abc12345 | grep -qx ip-ok

"$CADDY" run --config "$WORK/http.Caddyfile" --adapter caddyfile >"$WORK/http-caddy.log" 2>&1 &
PIDS+=("$!")
"$CADDY" run --config "$WORK/tunnel.Caddyfile" --adapter caddyfile >"$WORK/tunnel-caddy.log" 2>&1 &
PIDS+=("$!")
for _ in $(seq 1 30); do
  curl -fsS http://127.0.0.1:18081/Abc12345 | grep -qx http-ok && \
  curl -fsS http://127.0.0.1:18082/Abc12345 | grep -qx tunnel-ok && break
  sleep 1
done
curl -fsS http://127.0.0.1:18081/Abc12345 | grep -qx http-ok
curl -fsS http://127.0.0.1:18082/Abc12345 | grep -qx tunnel-ok
! curl -fsS http://127.0.0.1:18081/r/legacy/c >/dev/null 2>&1
! curl -fsS http://127.0.0.1:18081/api/v1/register >/dev/null 2>&1
! curl -fsS http://127.0.0.1:18082/api/v1/sync >/dev/null 2>&1

echo 'UNIFIED HTTP HTTPS AND TUNNEL FRONTEND VALIDATION PASSED'
echo 'CERTBOT IP CERTIFICATE FLAGS VALIDATION PASSED'

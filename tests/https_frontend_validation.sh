#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
CADDY="${1:?usage: https_frontend_validation.sh CADDY CERTBOT}"
CERTBOT="${2:?usage: https_frontend_validation.sh CADDY CERTBOT}"
WORK="$(mktemp -d /tmp/vvv-https-validation.XXXXXX)"
CADDY_PID=""
cleanup(){
  [[ -z "$CADDY_PID" ]] || kill "$CADDY_PID" >/dev/null 2>&1 || true
  [[ -z "$CADDY_PID" ]] || wait "$CADDY_PID" 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT

"$CADDY" version
"$CERTBOT" --version
"$CERTBOT" certonly --help all | grep -q -- '--ip-address'
"$CERTBOT" certonly --help all | grep -q -- '--preferred-profile'

cat > "$WORK/domain.Caddyfile" <<'EOF'
{
  admin off
  auto_https disable_redirects
}

sub.example.com:8443 {
  tls {
    issuer acme {
      disable_tlsalpn_challenge
    }
  }

  log {
    output discard
  }

  @allowed path /r/* /api/v1/* /health
  handle @allowed {
    reverse_proxy 127.0.0.1:18081
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

https://127.0.0.1:18443 {
  tls $WORK/ip.crt $WORK/ip.key
  respond /health "ok" 200
  respond 404
}
EOF
"$CADDY" validate --config "$WORK/ip.Caddyfile" --adapter caddyfile
"$CADDY" run --config "$WORK/ip.Caddyfile" --adapter caddyfile >"$WORK/caddy.log" 2>&1 &
CADDY_PID=$!
for _ in $(seq 1 30); do
  kill -0 "$CADDY_PID" 2>/dev/null || { cat "$WORK/caddy.log"; exit 1; }
  curl -fsS --connect-timeout 1 --max-time 2 http://127.0.0.1:18080/acme-ready | grep -qx ready && \
  curl -fsS --connect-timeout 1 --max-time 2 --cacert "$WORK/ip.crt" https://127.0.0.1:18443/health | grep -qx ok && break
  sleep 1
done
curl -fsS --connect-timeout 2 --max-time 4 http://127.0.0.1:18080/acme-ready | grep -qx ready
curl -fsS --connect-timeout 2 --max-time 4 --cacert "$WORK/ip.crt" https://127.0.0.1:18443/health | grep -qx ok
kill "$CADDY_PID"
wait "$CADDY_PID" 2>/dev/null || true
CADDY_PID=""

echo 'CADDY DOMAIN/IP CONFIGURATION AND RUNTIME VALIDATION PASSED'
echo 'CERTBOT IP CERTIFICATE FLAGS VALIDATION PASSED'

#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
CADDY="${1:?usage: https_frontend_validation.sh CADDY CERTBOT}"
CERTBOT="${2:?usage: https_frontend_validation.sh CADDY CERTBOT}"
WORK="$(mktemp -d /tmp/vvv-https-validation.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

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
  -sha256 -nodes -days 2 -subj '/CN=198.51.100.10' \
  -addext 'subjectAltName=IP:198.51.100.10' \
  -addext 'basicConstraints=critical,CA:FALSE' \
  -addext 'keyUsage=critical,digitalSignature' \
  -addext 'extendedKeyUsage=serverAuth' \
  -keyout "$WORK/ip.key" -out "$WORK/ip.crt" >/dev/null 2>&1

cat > "$WORK/ip.Caddyfile" <<EOF
{
  admin off
  auto_https off
  default_sni 198.51.100.10
}

:80 {
  root * $WORK/webroot

  @acme_challenge path /.well-known/acme-challenge/*
  handle @acme_challenge {
    file_server
  }

  respond 404

  log {
    output discard
  }
}

https://198.51.100.10:8443 {
  tls $WORK/ip.crt $WORK/ip.key

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
mkdir -p "$WORK/webroot/.well-known/acme-challenge"
"$CADDY" validate --config "$WORK/ip.Caddyfile" --adapter caddyfile

echo 'CADDY DOMAIN/IP CONFIGURATION VALIDATION PASSED'
echo 'CERTBOT IP CERTIFICATE FLAGS VALIDATION PASSED'

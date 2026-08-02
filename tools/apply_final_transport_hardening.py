#!/usr/bin/env python3
from pathlib import Path


def replace_once(path, old, new, label):
    file=Path(path); text=file.read_text(encoding='utf-8'); count=text.count(old)
    if count!=1:
        raise SystemExit(f'{label}: expected one target, found {count}')
    file.write_text(text.replace(old,new,1),encoding='utf-8')

# Trust Cloudflare's client IP header only while actually using Tunnel mode.
replace_once(
    'core-src/sub_center.py',
    '''def request_ip(handler):
    candidates = [
        handler.headers.get('CF-Connecting-IP', '').strip(),
        handler.headers.get('X-Forwarded-For', '').split(',')[0].strip(),
        handler.client_address[0],
    ]
''',
    '''def request_ip(handler):
    cfg = read_json(CFG, {}) or {}
    candidates = []
    if cfg.get('transport_mode') == 'tunnel':
        candidates.append(handler.headers.get('CF-Connecting-IP', '').strip())
    candidates.extend([
        handler.headers.get('X-Forwarded-For', '').split(',')[0].strip(),
        handler.client_address[0],
    ])
''',
    'conditional Cloudflare source IP trust',
)

# Do not migrate interrupted schema-2 centers before the existing recovery path can run.
replace_once(
    'core-src/bootstrap.sh',
    '''  [[ -s "$CENTER_CFG" ]] || return 0
  [[ "$(json_value "$CENTER_CFG" schema 0)" == 2 ]] || return 0
  local suffix
''',
    '''  [[ -s "$CENTER_CFG" ]] || return 0
  [[ "$(json_value "$CENTER_CFG" schema 0)" == 2 ]] || return 0
  if [[ ! -s /etc/vvv-sub/registration.code || ! -x /usr/local/sbin/vvv-center ||
        ! -x /usr/local/lib/vvv/sub_center.py || ! -f /etc/systemd/system/vvv-sub.service ||
        ! -f /etc/systemd/system/caddy.service || ! -s /etc/caddy/Caddyfile ]]; then
    echo "检测到旧版订阅中心残留不完整，暂不迁移；选择带订阅中心的角色后将先备份并按中断恢复流程处理。"
    return 0
  fi
  local suffix
''',
    'legacy center migration completeness guard',
)

# Debug data is ephemeral and removed both on normal exit and interruption.
replace_once(
    'core-src/center_manager.sh',
    '''  trap 'rm -f "$flag"' EXIT INT TERM
''',
    '''  trap 'rm -f "$flag" "$log"' EXIT INT TERM
''',
    'debug cleanup trap',
)
replace_once(
    'core-src/center_manager.sh',
    '''  rm -f "$flag"
  trap - EXIT INT TERM
''',
    '''  rm -f "$flag" "$log"
  trap - EXIT INT TERM
''',
    'debug cleanup normal exit',
)

transport='core-src/center_transport.sh'
# Verify certificate/key pairing and support both deployed and Certbot lineage files.
replace_once(
    transport,
    '''valid_ip_certificate(){
  local ip cert
  ip="$(value '.public_ip')"
  cert="${CADDY_CERT_DIR}/ip-fullchain.pem"
  [[ -s "$cert" && -s "${CADDY_CERT_DIR}/ip-privkey.pem" ]] || return 1
  openssl x509 -checkend 43200 -noout -in "$cert" >/dev/null 2>&1 || return 1
  openssl x509 -in "$cert" -noout -ext subjectAltName 2>/dev/null | grep -Fq "IP Address:${ip}"
}
''',
    '''valid_ip_cert_files(){
  local cert="$1" key="$2" ip cert_pub key_pub
  ip="$(value '.public_ip')"
  [[ -s "$cert" && -s "$key" ]] || return 1
  openssl x509 -checkend 43200 -noout -in "$cert" >/dev/null 2>&1 || return 1
  openssl x509 -in "$cert" -noout -ext subjectAltName 2>/dev/null | grep -Fq "IP Address:${ip}" || return 1
  cert_pub="$(openssl x509 -in "$cert" -pubkey -noout 2>/dev/null | openssl pkey -pubin -outform DER 2>/dev/null | sha256sum | awk '{print $1}')"
  key_pub="$(openssl pkey -in "$key" -pubout -outform DER 2>/dev/null | sha256sum | awk '{print $1}')"
  [[ -n "$cert_pub" && "$cert_pub" == "$key_pub" ]]
}
valid_ip_certificate(){
  valid_ip_cert_files "${CADDY_CERT_DIR}/ip-fullchain.pem" "${CADDY_CERT_DIR}/ip-privkey.pem"
}
''',
    'certificate pair validation',
)
replace_once(
    transport,
    '''  if valid_ip_certificate; then
    echo "检测到仍有效且匹配当前 IP 的证书，直接复用。"
    return 0
  fi
  install_certbot
  install -d -m755 "$ACME_WEBROOT/.well-known/acme-challenge"
''',
    '''  if valid_ip_certificate; then
    echo "检测到仍有效且匹配当前 IP 的已部署证书，直接复用。"
    install_certbot
    write_ip_deploy_hook
    write_ip_renew_units
    return 0
  fi
  if valid_ip_cert_files "$live_dir/fullchain.pem" "$live_dir/privkey.pem"; then
    echo "检测到仍有效的 Certbot IP 证书 lineage，正在恢复部署并重建续期服务。"
    install_certbot
    write_ip_deploy_hook
    /usr/local/lib/vvv/deploy-ip-cert.sh
    write_ip_renew_units
    return 0
  fi
  install_certbot
  install -d -m755 "$ACME_WEBROOT/.well-known/acme-challenge"
''',
    'reuse restored certificate lineage',
)

# Wait for Caddy ACME/Tunnel propagation instead of failing on the first request.
old='''check_public(){
  local mode base suffix
  mode="$(value '.transport_mode')"; base="$(value '.base_url')"; suffix="$(value '.subscription_suffix')"
  if [[ "$mode" == direct-http ]]; then
    curl -fsS --connect-timeout 3 --max-time 8 -H 'User-Agent: Clash-Verge-Rev' "${base}/${suffix}" >/dev/null
  elif [[ "$mode" == direct-https && -z "$(value '.domain')" ]]; then
    curl -fsS --connect-timeout 3 --max-time 8 --connect-to "$(value '.public_ip'):$(value '.public_port'):127.0.0.1:$(value '.public_port')" \\
      -H 'User-Agent: Clash-Verge-Rev' "${base}/${suffix}" >/dev/null
  else
    curl -fsS --connect-timeout 5 --max-time 15 -H 'User-Agent: Clash-Verge-Rev' "${base}/${suffix}" >/dev/null
  fi
}
'''
new='''check_public_once(){
  local mode base suffix
  mode="$(value '.transport_mode')"; base="$(value '.base_url')"; suffix="$(value '.subscription_suffix')"
  if [[ "$mode" == direct-http ]]; then
    curl -fsS --connect-timeout 3 --max-time 8 -H 'User-Agent: Clash-Verge-Rev' "${base}/${suffix}" >/dev/null
  elif [[ "$mode" == direct-https && -z "$(value '.domain')" ]]; then
    curl -fsS --connect-timeout 3 --max-time 8 --connect-to "$(value '.public_ip'):$(value '.public_port'):127.0.0.1:$(value '.public_port')" \\
      -H 'User-Agent: Clash-Verge-Rev' "${base}/${suffix}" >/dev/null
  else
    curl -fsS --connect-timeout 5 --max-time 15 -H 'User-Agent: Clash-Verge-Rev' "${base}/${suffix}" >/dev/null
  fi
}
check_public(){
  local attempt
  for attempt in $(seq 1 120); do
    check_public_once && return 0
    (( attempt % 10 != 0 )) || echo "统一订阅入口仍在准备：已等待 $((attempt*2)) 秒……"
    sleep 2
  done
  echo "统一订阅入口在 240 秒内未通过健康检查。" >&2
  return 1
}
'''
replace_once(transport,old,new,'public frontend retry')

# Cloud backup must contain everything needed to resume IP renewal.
replace_once(
    'core-src/backup_manager.py',
    '''    Path('/etc/systemd/system/vvv-cloudflared.service'),
    Path('/usr/local/lib/vvv/run-cloudflared.sh'),
''',
    '''    Path('/etc/systemd/system/vvv-cloudflared.service'),
    Path('/etc/systemd/system/vvv-ip-cert-renew.service'),
    Path('/etc/systemd/system/vvv-ip-cert-renew.timer'),
    Path('/usr/local/lib/vvv/run-cloudflared.sh'),
    Path('/usr/local/lib/vvv/deploy-ip-cert.sh'),
''',
    'cloud certificate renewal backup sources',
)

# Permanent checks for the hardening behavior.
test=Path('tests/conformance.py')
s=test.read_text(encoding='utf-8')
s=s.replace(
    "    for token in ('/etc/letsencrypt', '/var/lib/caddy/.local/share/caddy', '/etc/caddy', 'cloudflared.token', 'vvv-cloudflared.service'):\n",
    "    for token in ('/etc/letsencrypt', '/var/lib/caddy/.local/share/caddy', '/etc/caddy', 'cloudflared.token', 'vvv-cloudflared.service', 'vvv-ip-cert-renew.timer', 'deploy-ip-cert.sh'):\n",
    1,
)
anchor="    require('Cloudflare Tunnel模式必须输入 Tunnel Token' in center, 'Tunnel缺少Token校验')\n"
addition=anchor+"    require(\"cfg.get('transport_mode') == 'tunnel'\" in read('core-src/sub_center.py'), '直连模式仍会信任可伪造的Cloudflare来源头')\n    require('valid_ip_cert_files' in transport and 'write_ip_renew_units' in transport and 'seq 1 120' in transport, '证书恢复或公网入口等待机制不完整')\n    require('旧版订阅中心残留不完整' in bootstrap, '不完整schema2中心仍会被提前迁移')\n    require('rm -f \"$flag\" \"$log\"' in manager, '请求头调试结束后没有删除临时日志')\n"
if s.count(anchor)!=1:
    raise SystemExit('hardening test anchor missing')
s=s.replace(anchor,addition,1)
test.write_text(s,encoding='utf-8')

print('FINAL TRANSPORT HARDENING PATCH APPLIED')

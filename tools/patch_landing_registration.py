#!/usr/bin/env python3
from pathlib import Path

landing = Path('core-src/landing.sh')
text = landing.read_text(encoding='utf-8')
replacements = {
    '      tzdata kmod qrencode util-linux\n': '      tzdata kmod qrencode util-linux python3\n',
    '      tzdata kmod libqrencode-tools util-linux\n': '      tzdata kmod libqrencode-tools util-linux python3\n',
}
for old, new in replacements.items():
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'landing dependency anchor count={count}: {old!r}')
    text = text.replace(old, new, 1)
landing.write_text(text, encoding='utf-8')

bootstrap = Path('core-src/bootstrap.sh')
text = bootstrap.read_text(encoding='utf-8')
anchor = "show_parameter_summary(){\n"
helper = r'''jpr_registration_code(){
  local value="$1" rest encoded mod padded
  rest="${value#JPR3.}"; encoded="${rest%%.*}"
  mod=$((${#encoded} % 4))
  case "$mod" in 0) padded="$encoded";; 2) padded="${encoded}==";; 3) padded="${encoded}=";; *) return 1;; esac
  printf '%s' "$padded" | tr '_-' '/+' | base64 -d 2>/dev/null | jq -r '.subscription_registration_code // empty'
}
'''
if text.count(anchor) != 1:
    raise SystemExit(f'bootstrap helper anchor count={text.count(anchor)}')
text = text.replace(anchor, helper + anchor, 1)
old = '''    [[ -x /usr/local/sbin/vps ]] && cp -f /usr/local/sbin/vps /usr/local/sbin/vvv-landing-original
    write_roles false false true false landing
    ;;'''
new = '''    [[ -x /usr/local/sbin/vps ]] && cp -f /usr/local/sbin/vps /usr/local/sbin/vvv-landing-original
    write_roles false false true false landing
    code="$(jpr_registration_code "$key" || true)"
    bash "$BASE_DIR/register_sync.sh" landing "$code"
    ;;'''
if text.count(old) != 1:
    raise SystemExit(f'landing registration block count={text.count(old)}')
text = text.replace(old, new, 1)
bootstrap.write_text(text, encoding='utf-8')

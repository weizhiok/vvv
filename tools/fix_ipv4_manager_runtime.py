#!/usr/bin/env python3
from pathlib import Path

path = Path('core-src/host.sh')
text = path.read_text(encoding='utf-8')
old = """cat > /usr/local/sbin/jp-relay-manager <<'JP_RELAY_JPR3_MANAGER_EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

RUN_MODE=\"${1:-}\"
"""
new = """cat > /usr/local/sbin/jp-relay-manager <<'JP_RELAY_JPR3_MANAGER_EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

IPV4_ONLY_MODULE=/usr/local/lib/vvv/ipv4_only.sh
[[ -r \"$IPV4_ONLY_MODULE\" ]] || { echo \"错误：缺少 IPv4-only 系统模块。\" >&2; exit 1; }
# shellcheck disable=SC1090
source \"$IPV4_ONLY_MODULE\"

RUN_MODE=\"${1:-}\"
"""
count = text.count(old)
if count != 1:
    raise SystemExit(f'expected one jp-relay-manager header anchor, found {count}')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
print('patched generated jp-relay-manager IPv4-only runtime dependency')

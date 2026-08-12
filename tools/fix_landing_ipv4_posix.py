#!/usr/bin/env python3
from pathlib import Path
p=Path('core-src/landing.sh')
s=p.read_text(encoding='utf-8')
old='LANDING_SOURCE_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"\n[[ -f "$LANDING_SOURCE_DIR/ipv4_only.sh" ]] || { echo "错误：缺少 IPv4-only 系统模块。" >&2; exit 1; }'
new='LANDING_SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"\n[ -f "$LANDING_SOURCE_DIR/ipv4_only.sh" ] || { echo "错误：缺少 IPv4-only 系统模块。" >&2; exit 1; }'
if s.count(old)!=1:
    raise SystemExit(f'POSIX anchor mismatch: {s.count(old)}')
p.write_text(s.replace(old,new,1),encoding='utf-8')

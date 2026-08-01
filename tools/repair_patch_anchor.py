#!/usr/bin/env python3
from pathlib import Path

path = Path("tools/patch_vless_slots.py")
text = path.read_text(encoding="utf-8")

if text.count("post=r'''") != 1:
    raise SystemExit("outer opening delimiter not found once")
text = text.replace("post=r'''", 'post=r"""', 1)

closing = "\n'''\ns=s.replace(anchor,post+"
if text.count(closing) != 1:
    raise SystemExit("outer closing delimiter not found once")
text = text.replace(closing, '\n"""\ns=s.replace(anchor,post+', 1)

old = """old = '''  ALLOC_VLESS_EMAIL=\"$(jq -r '.email' <<<\"$slot_json\")\"
  [[ -n \"$ALLOC_VLESS_SLOT\" && -n \"$ALLOC_VLESS_UUID\" && -n \"$ALLOC_VLESS_EMAIL\" ]] || fail \"VLESS 预分配用户池损坏。\"'''
new = '''  ALLOC_VLESS_EMAIL=\"$(jq -r '.email' <<<\"$slot_json\")\"
  ALLOC_VLESS_PORT=\"$(jq -r '.local_port' <<<\"$slot_json\")\"
  [[ -n \"$ALLOC_VLESS_SLOT\" && -n \"$ALLOC_VLESS_UUID\" && -n \"$ALLOC_VLESS_EMAIL\" && \"$ALLOC_VLESS_PORT\" =~ ^[0-9]+$ ]] || fail \"VLESS 预分配用户池损坏。\"'''"""
new = """old = '''  ALLOC_VLESS_EMAIL=\"$(jq -r '.email' <<<\"$slot_json\")\"'''
new = '''  ALLOC_VLESS_EMAIL=\"$(jq -r '.email' <<<\"$slot_json\")\"
  ALLOC_VLESS_PORT=\"$(jq -r '.local_port' <<<\"$slot_json\")\"
  [[ -n \"$ALLOC_VLESS_SLOT\" && -n \"$ALLOC_VLESS_UUID\" && -n \"$ALLOC_VLESS_EMAIL\" && \"$ALLOC_VLESS_PORT\" =~ ^[0-9]+$ ]] || fail \"VLESS 预分配用户池损坏。\"'''"""

if text.count(old) != 1:
    raise SystemExit(f"VLESS allocation anchor count={text.count(old)}")
text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")

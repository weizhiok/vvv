#!/usr/bin/env bash
set -Eeuo pipefail

vvv_qr_visible_width() {
  python3 - "$1" <<'PY'
import re, sys, unicodedata
ansi = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]')
width = 0
for raw in open(sys.argv[1], encoding='utf-8', errors='ignore'):
    text = ansi.sub('', raw.rstrip('\n'))
    n = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        n += 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
    width = max(width, n)
print(width)
PY
}

vvv_print_qr() {
  local value="${1:-}" tmp width cols
  [[ -n "$value" ]] || return 0
  command -v qrencode >/dev/null 2>&1 || { echo "错误：未安装 qrencode。" >&2; return 1; }
  tmp="$(mktemp /tmp/vvv-qr.XXXXXX)"
  qrencode -t ANSIUTF8 -m 1 "$value" > "$tmp"
  width="$(vvv_qr_visible_width "$tmp")"
  cols="$(tput cols 2>/dev/null || echo 120)"
  if [[ "$width" =~ ^[0-9]+$ ]] && (( width > cols )); then
    echo "提示：当前终端宽度 ${cols} 列，二维码需要至少 ${width} 列。请放大窗口后重新显示。" >&2
    rm -f "$tmp"
    return 1
  fi
  printf '\033[47m%*s\033[0m\n' "$width" ''
  cat "$tmp"
  printf '\n'
  rm -f "$tmp"
}

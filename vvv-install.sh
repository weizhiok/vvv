#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
TMP="$(mktemp /tmp/vvv-fixed-entry.XXXXXX.sh)"
trap 'rm -f "$TMP"' EXIT
URL="https://raw.githubusercontent.com/weizhiok/vvv/main/vvv-install.sh?v=$(date +%s)-$$"
if command -v curl >/dev/null 2>&1; then
  curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 "$URL" -o "$TMP"
elif command -v wget >/dev/null 2>&1; then
  wget -qO "$TMP" "$URL"
else
  echo "错误：系统同时缺少 curl 和 wget。请先使用 APT 安装 curl。" >&2
  exit 1
fi
bash -n "$TMP"
if [[ -r /dev/tty ]]; then
  exec bash "$TMP" </dev/tty
else
  exec bash "$TMP"
fi

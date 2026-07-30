#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
TMP="$(mktemp /tmp/vvv-fixed-entry.XXXXXX.sh)"
trap 'rm -f "$TMP"' EXIT
URL="https://raw.githubusercontent.com/weizhiok/vvv/main/vvv-install.sh?v=$(date +%s)-$$"
curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 "$URL" -o "$TMP"
bash -n "$TMP"
if [[ -r /dev/tty ]]; then
  exec bash "$TMP" </dev/tty
else
  exec bash "$TMP"
fi

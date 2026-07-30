#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

TMP="$(mktemp /tmp/vvv-entry.XXXXXX.sh)"
trap 'rm -f "$TMP"' EXIT
URL="https://raw.githubusercontent.com/weizhiok/vvv/main/vvv-install.sh?v=$(date +%s)-$$"

command -v curl >/dev/null 2>&1 || {
  echo "错误：系统缺少 curl。" >&2
  exit 1
}

curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 "$URL" -o "$TMP" || {
  echo "错误：下载 VVV 可靠安装器失败。" >&2
  exit 1
}

bash -n "$TMP" || {
  echo "错误：下载到的 VVV 安装器语法检查失败。" >&2
  exit 1
}

if [[ -r /dev/tty ]]; then
  exec bash "$TMP" </dev/tty
else
  exec bash "$TMP"
fi

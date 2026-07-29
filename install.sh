#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

REPO_RAW="https://raw.githubusercontent.com/weizhiok/vvv/main"
PARTS=16
TMP_DIR="$(mktemp -d /tmp/vvv-bootstrap.XXXXXX)"
trap 'rm -rf "$TMP_DIR"' EXIT

[[ "$(id -u)" -eq 0 ]] || { echo "错误：请使用 root 用户运行。" >&2; exit 1; }
command -v curl >/dev/null 2>&1 || {
  if command -v apt-get >/dev/null 2>&1; then apt-get update && apt-get install -y curl ca-certificates;
  elif command -v apk >/dev/null 2>&1; then apk add --no-cache curl ca-certificates;
  else echo "错误：系统缺少 curl，且无法自动安装。" >&2; exit 1; fi
}
command -v sha256sum >/dev/null 2>&1 || { echo "错误：系统缺少 sha256sum。" >&2; exit 1; }
command -v base64 >/dev/null 2>&1 || { echo "错误：系统缺少 base64。" >&2; exit 1; }
command -v gzip >/dev/null 2>&1 || { echo "错误：系统缺少 gzip。" >&2; exit 1; }

: > "$TMP_DIR/vvv.b64"
for i in $(seq -w 1 "$PARTS"); do
  curl -fsSL --retry 3 --connect-timeout 10 "$REPO_RAW/payload/vvv.part${i}" >> "$TMP_DIR/vvv.b64"
done
base64 -d "$TMP_DIR/vvv.b64" | gzip -dc > "$TMP_DIR/vvv.sh"
curl -fsSL --retry 3 --connect-timeout 10 "$REPO_RAW/sha256.txt" -o "$TMP_DIR/sha256.txt"
expected="$(awk '$2=="vvv.sh"{print $1}' "$TMP_DIR/sha256.txt")"
actual="$(sha256sum "$TMP_DIR/vvv.sh" | awk '{print $1}')"
[[ -n "$expected" && "$expected" == "$actual" ]] || { echo "错误：VVV 主程序完整性校验失败。" >&2; exit 1; }
bash -n "$TMP_DIR/vvv.sh"
chmod 700 "$TMP_DIR/vvv.sh"
if [[ -r /dev/tty ]]; then
  exec bash "$TMP_DIR/vvv.sh" </dev/tty
else
  exec bash "$TMP_DIR/vvv.sh"
fi

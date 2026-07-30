#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

RAW="https://raw.githubusercontent.com/weizhiok/vvv/main"
PARTS=9
EXPECTED_B64_SHA256="e815be04da67410556a8627f273c8605ecc2af999de99ca4622469b87a0470b3"
EXPECTED_ARCHIVE_SHA256="757f36a7248d2cca66c8cb2a8a3b2ccb4c5225d7591dcf95787358134140e306"
TMP="$(mktemp -d /tmp/vvv-install.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

fail() {
  echo "错误：$*" >&2
  exit 1
}

[[ "$(id -u)" -eq 0 ]] || fail "请使用 root 用户运行。"

if ! command -v curl >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y curl ca-certificates coreutils tar gzip bash
  elif command -v apk >/dev/null 2>&1; then
    apk add --no-cache curl ca-certificates coreutils tar gzip bash
  else
    fail "系统缺少 curl，且无法自动安装。"
  fi
fi

for cmd in base64 sha256sum tar gzip bash awk tr; do
  command -v "$cmd" >/dev/null 2>&1 || fail "系统缺少命令：$cmd"
done

nonce="$(date +%s)-$$"
: > "$TMP/bundle.b64"

echo "正在下载 VVV 安装包（1/$PARTS）……"
for ((i=0; i<PARTS; i++)); do
  part="$(printf 'part%02d' "$i")"
  printf '\r正在下载 VVV 安装包（%d/%d）……' "$((i + 1))" "$PARTS"
  curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 \
    "$RAW/bundle/parts/$part?v=$nonce-$i" \
    -o "$TMP/$part" || fail "下载 $part 失败。"
  cat "$TMP/$part" >> "$TMP/bundle.b64"
done
printf '\n'

LC_ALL=C tr -d '\r\n\t ' < "$TMP/bundle.b64" > "$TMP/bundle.clean.b64"
actual_b64_sha256="$(sha256sum "$TMP/bundle.clean.b64" | awk '{print $1}')"
[[ "$actual_b64_sha256" == "$EXPECTED_B64_SHA256" ]] || {
  echo "期望运输层 SHA-256：$EXPECTED_B64_SHA256" >&2
  echo "实际运输层 SHA-256：$actual_b64_sha256" >&2
  fail "安装包下载不完整或 GitHub 缓存内容不一致。"
}

base64 -d "$TMP/bundle.clean.b64" > "$TMP/vvv-bundle.tar.gz" 2>/dev/null \
  || fail "安装包 Base64 解码失败。"

gzip -t "$TMP/vvv-bundle.tar.gz" 2>/dev/null \
  || fail "安装包 gzip 完整性检查失败。"

actual_archive_sha256="$(sha256sum "$TMP/vvv-bundle.tar.gz" | awk '{print $1}')"
[[ "$actual_archive_sha256" == "$EXPECTED_ARCHIVE_SHA256" ]] || {
  echo "期望安装包 SHA-256：$EXPECTED_ARCHIVE_SHA256" >&2
  echo "实际安装包 SHA-256：$actual_archive_sha256" >&2
  fail "安装包 SHA-256 校验失败。"
}

mkdir -p "$TMP/app"
tar -xzf "$TMP/vvv-bundle.tar.gz" -C "$TMP/app" \
  || fail "解压安装包失败。"

for file in bootstrap.sh center_install.sh register_sync.sh vvv_manager.sh host.sh; do
  [[ -f "$TMP/app/$file" ]] || fail "安装包缺少 $file。"
  bash -n "$TMP/app/$file" || fail "$file 语法检查失败。"
done

[[ -f "$TMP/app/landing.sh" ]] || fail "安装包缺少 landing.sh。"
sh -n "$TMP/app/landing.sh" || fail "landing.sh 语法检查失败。"

if command -v python3 >/dev/null 2>&1; then
  [[ -f "$TMP/app/sub_center.py" && -f "$TMP/app/sync_agent.py" ]] \
    || fail "安装包缺少订阅中心 Python 模块。"
  python3 -m py_compile "$TMP/app/sub_center.py" "$TMP/app/sync_agent.py" \
    || fail "订阅中心 Python 模块语法检查失败。"
fi

install -d -m 700 /usr/local/lib/vvv-source
rm -rf /usr/local/lib/vvv-source/*
cp -a "$TMP/app/." /usr/local/lib/vvv-source/
chmod 700 /usr/local/lib/vvv-source/*.sh 2>/dev/null || true
chmod 700 /usr/local/lib/vvv-source/*.py 2>/dev/null || true

echo "VVV 安装包下载、解码、校验和语法检查全部通过。"

if [[ -r /dev/tty ]]; then
  exec bash /usr/local/lib/vvv-source/bootstrap.sh </dev/tty
else
  exec bash /usr/local/lib/vvv-source/bootstrap.sh
fi

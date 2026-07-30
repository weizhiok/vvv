#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

RAW="https://raw.githubusercontent.com/weizhiok/vvv/main"
PARTS=9
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

for cmd in base64 tar gzip bash awk tr wc grep; do
  command -v "$cmd" >/dev/null 2>&1 || fail "系统缺少命令：$cmd"
done

nonce="$(date +%s)-$$"
: > "$TMP/bundle.b64"

echo "正在下载 VVV 安装包……"
for ((i=0; i<PARTS; i++)); do
  part="$(printf 'part%02d' "$i")"
  printf '\r正在下载 VVV 安装包（%d/%d）……' "$((i + 1))" "$PARTS"
  curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 \
    "$RAW/bundle/parts/$part?v=$nonce-$i" \
    -o "$TMP/$part" || fail "下载 $part 失败。"
  [[ -s "$TMP/$part" ]] || fail "$part 是空文件。"
  cat "$TMP/$part" >> "$TMP/bundle.b64"
done
printf '\n'

LC_ALL=C tr -d '\r\n\t ' < "$TMP/bundle.b64" > "$TMP/bundle.clean.b64"
[[ -s "$TMP/bundle.clean.b64" ]] || fail "安装包内容为空。"
LC_ALL=C grep -Eq '^[A-Za-z0-9+/=]+$' "$TMP/bundle.clean.b64" \
  || fail "安装包包含非法 Base64 字符。"

b64_size="$(wc -c < "$TMP/bundle.clean.b64" | tr -d ' ')"
(( b64_size % 4 == 0 )) || fail "安装包 Base64 长度异常。"

base64 -d "$TMP/bundle.clean.b64" > "$TMP/vvv-bundle.tar.gz" 2>/dev/null \
  || fail "安装包 Base64 解码失败。"

gzip -t "$TMP/vvv-bundle.tar.gz" 2>/dev/null \
  || fail "安装包 gzip 完整性检查失败。"

tar -tzf "$TMP/vvv-bundle.tar.gz" > "$TMP/file-list.txt" \
  || fail "安装包 tar 目录检查失败。"

awk '
  /^\// { bad=1 }
  /(^|\/)\.\.($|\/)/ { bad=1 }
  END { exit bad ? 1 : 0 }
' "$TMP/file-list.txt" || fail "安装包包含不安全路径。"

mkdir -p "$TMP/app"
tar -xzf "$TMP/vvv-bundle.tar.gz" -C "$TMP/app" \
  || fail "解压安装包失败。"

for file in bootstrap.sh center_install.sh register_sync.sh vvv_manager.sh host.sh; do
  [[ -f "$TMP/app/$file" ]] || fail "安装包缺少 $file。"
  bash -n "$TMP/app/$file" || fail "$file 语法检查失败。"
done

[[ -f "$TMP/app/landing.sh" ]] || fail "安装包缺少 landing.sh。"
sh -n "$TMP/app/landing.sh" || fail "landing.sh 语法检查失败。"

for file in sub_center.py sync_agent.py; do
  [[ -f "$TMP/app/$file" ]] || fail "安装包缺少 $file。"
done

if command -v python3 >/dev/null 2>&1; then
  python3 -m py_compile "$TMP/app/sub_center.py" "$TMP/app/sync_agent.py" \
    || fail "订阅中心 Python 模块语法检查失败。"
fi

install -d -m 700 /usr/local/lib/vvv-source
rm -rf /usr/local/lib/vvv-source/*
cp -a "$TMP/app/." /usr/local/lib/vvv-source/
chmod 700 /usr/local/lib/vvv-source/*.sh 2>/dev/null || true
chmod 700 /usr/local/lib/vvv-source/*.py 2>/dev/null || true

echo "VVV 安装包下载、解码、完整性和语法检查全部通过。"

if [[ -r /dev/tty ]]; then
  exec bash /usr/local/lib/vvv-source/bootstrap.sh </dev/tty
else
  exec bash /usr/local/lib/vvv-source/bootstrap.sh
fi

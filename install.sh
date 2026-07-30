#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

REPO_RAW="https://raw.githubusercontent.com/weizhiok/vvv/main"
PARTS=16
TMP_DIR="$(mktemp -d /tmp/vvv-bootstrap.XXXXXX)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() {
  echo "错误：$*" >&2
  exit 1
}

[[ "$(id -u)" -eq 0 ]] || fail "请使用 root 用户运行。"

if ! command -v curl >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y curl ca-certificates
  elif command -v apk >/dev/null 2>&1; then
    apk add --no-cache curl ca-certificates
  else
    fail "系统缺少 curl，且无法自动安装。"
  fi
fi

for cmd in sha256sum base64 gzip grep tr awk head tail od; do
  command -v "$cmd" >/dev/null 2>&1 || fail "系统缺少命令：$cmd"
done

normalize_part() {
  local src="$1"
  local dst="$2"
  local tmp="${dst}.tmp"
  local prefix

  # 删除换行、空格、Tab 和 CR。部分系统的 base64 对 CR/BOM 非常严格。
  LC_ALL=C tr -d '\r\n\t ' < "$src" > "$tmp"

  # 去掉可能存在的 UTF-8 BOM。
  prefix="$(head -c 3 "$tmp" | od -An -tx1 | tr -d ' \n')"
  if [[ "$prefix" == "efbbbf" ]]; then
    tail -c +4 "$tmp" > "$dst"
    rm -f "$tmp"
  else
    mv -f "$tmp" "$dst"
  fi

  [[ -s "$dst" ]] || fail "下载到空分片：$(basename "$src")"
  LC_ALL=C grep -Eq '^[A-Za-z0-9+/]*={0,2}$' "$dst" \
    || fail "分片包含非法 Base64 字符：$(basename "$src")"
}

echo "正在下载 VVV 主程序……"

i=1
while (( i <= PARTS )); do
  printf -v n '%02d' "$i"
  src="$TMP_DIR/vvv.part${n}.download"
  dst="$TMP_DIR/vvv.part${n}.b64"

  # 时间戳用于规避中间缓存返回不同版本的旧分片。
  curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 \
    "${REPO_RAW}/payload/vvv.part${n}?t=$(date +%s)-${n}" -o "$src" \
    || fail "下载分片 vvv.part${n} 失败。"

  normalize_part "$src" "$dst"
  i=$((i + 1))
done

archive="$TMP_DIR/vvv.gz"
combined="$TMP_DIR/vvv.b64"
: > "$combined"

i=1
while (( i <= PARTS )); do
  printf -v n '%02d' "$i"
  cat "$TMP_DIR/vvv.part${n}.b64" >> "$combined"
  i=$((i + 1))
done

# 模式一：分片本来就是同一条 Base64 字符串的切片。
decode_ok=0
if base64 -d "$combined" > "$archive" 2>/dev/null \
   && gzip -t "$archive" 2>/dev/null; then
  decode_ok=1
fi

# 模式二：每个分片分别做过 Base64 编码，需要逐个解码后拼接二进制。
if (( decode_ok == 0 )); then
  echo "标准拼接解码未通过，正在尝试兼容模式……"
  : > "$archive"
  part_decode_ok=1
  i=1
  while (( i <= PARTS )); do
    printf -v n '%02d' "$i"
    if ! base64 -d "$TMP_DIR/vvv.part${n}.b64" >> "$archive" 2>/dev/null; then
      part_decode_ok=0
      break
    fi
    i=$((i + 1))
  done

  if (( part_decode_ok == 1 )) && gzip -t "$archive" 2>/dev/null; then
    decode_ok=1
  fi
fi

(( decode_ok == 1 )) || fail "主程序分片无法解码或 gzip 数据已损坏，请稍后重试。"

gzip -dc "$archive" > "$TMP_DIR/vvv.sh" \
  || fail "解压 VVV 主程序失败。"

curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 \
  "${REPO_RAW}/sha256.txt?t=$(date +%s)" -o "$TMP_DIR/sha256.txt" \
  || fail "下载完整性校验文件失败。"

expected="$(awk '$2=="vvv.sh"{print $1}' "$TMP_DIR/sha256.txt" | head -n1)"
actual="$(sha256sum "$TMP_DIR/vvv.sh" | awk '{print $1}')"

[[ -n "$expected" ]] || fail "校验文件中没有 vvv.sh 的摘要。"
[[ "$expected" == "$actual" ]] \
  || fail "VVV 主程序完整性校验失败。期望：$expected，实际：$actual"

bash -n "$TMP_DIR/vvv.sh" || fail "VVV 主程序 Bash 语法检查失败。"
chmod 700 "$TMP_DIR/vvv.sh"

echo "VVV 主程序下载和校验成功。"

if [[ -r /dev/tty ]]; then
  exec bash "$TMP_DIR/vvv.sh" </dev/tty
else
  exec bash "$TMP_DIR/vvv.sh"
fi

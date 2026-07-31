#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
RAW="https://raw.githubusercontent.com/weizhiok/vvv/main"
TMP="$(mktemp -d /tmp/vvv-install.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
fail(){ echo "错误：$*" >&2; exit 1; }
[[ $(id -u) -eq 0 ]] || fail "请使用 root 用户运行。"
if ! command -v curl >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
  apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 update
  DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 install -y curl ca-certificates bash python3
fi
nonce="$(date +%s)-$$"; mkdir -p "$TMP/app"
echo "正在下载 VVV 普通源码……"
curl -fsSL --retry 5 --retry-all-errors "$RAW/src/bootstrap.sh?v=$nonce" -o "$TMP/app/bootstrap.sh" || fail "下载 bootstrap.sh 失败。"
curl -fsSL --retry 5 --retry-all-errors "$RAW/src/prepare.py?v=$nonce" -o "$TMP/prepare.py" || fail "下载 prepare.py 失败。"
files=(host.sh landing.sh center_install.sh register_sync.sh vvv_manager.sh sub_center.py sync_agent.py backup_manager.py rclone_manager.sh qr_helper.sh)
for file in "${files[@]}"; do
  printf '  下载 %s\n' "$file"
  curl -fsSL --retry 5 --retry-all-errors "$RAW/core-src/$file?v=$nonce-$file" -o "$TMP/app/$file" || fail "下载 $file 失败。"
  [[ -s "$TMP/app/$file" ]] || fail "$file 是空文件。"
done
python3 -m py_compile "$TMP/prepare.py"
python3 "$TMP/prepare.py" "$TMP/app/host.sh" "$TMP/app/landing.sh" "$TMP/app/center_install.sh" || fail "源码参数化处理失败。"
for file in bootstrap.sh center_install.sh register_sync.sh vvv_manager.sh rclone_manager.sh qr_helper.sh host.sh; do bash -n "$TMP/app/$file" || fail "$file 语法检查失败。"; done
sh -n "$TMP/app/landing.sh" || fail "landing.sh 语法检查失败。"
python3 -m py_compile "$TMP/app/sub_center.py" "$TMP/app/sync_agent.py" "$TMP/app/backup_manager.py" || fail "Python 模块语法检查失败。"
install -d -m700 /usr/local/lib/vvv-source
rm -rf /usr/local/lib/vvv-source/*
cp -a "$TMP/app/." /usr/local/lib/vvv-source/
chmod 700 /usr/local/lib/vvv-source/*
echo "VVV 普通源码下载和语法检查全部通过。"
if [[ -r /dev/tty ]]; then exec bash /usr/local/lib/vvv-source/bootstrap.sh </dev/tty; else exec bash /usr/local/lib/vvv-source/bootstrap.sh; fi

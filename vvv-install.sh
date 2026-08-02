#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

RAW="https://raw.githubusercontent.com/weizhiok/vvv/main"
TMP="$(mktemp -d /tmp/vvv-install.XXXXXX)"
SOURCE_TARGET=/usr/local/lib/vvv-source
SOURCE_STAGING=""
SOURCE_BACKUP=""
SOURCE_SWAP_COMMITTED=0

cleanup(){
  local rc=$?
  if (( SOURCE_SWAP_COMMITTED == 0 )) && [[ -n "$SOURCE_BACKUP" && -e "$SOURCE_BACKUP" ]]; then
    if [[ ! -e "$SOURCE_TARGET" ]]; then
      mv "$SOURCE_BACKUP" "$SOURCE_TARGET" 2>/dev/null || true
    else
      rm -rf "$SOURCE_BACKUP"
    fi
  fi
  [[ -z "$SOURCE_STAGING" ]] || rm -rf "$SOURCE_STAGING"
  (( SOURCE_SWAP_COMMITTED == 0 )) || { [[ -z "$SOURCE_BACKUP" ]] || rm -rf "$SOURCE_BACKUP"; }
  rm -rf "$TMP"
  return "$rc"
}
trap cleanup EXIT
fail(){ echo "错误：$*" >&2; exit 1; }

[[ $(id -u) -eq 0 ]] || fail "请使用 root 用户运行。"
[[ -r /etc/os-release ]] || fail "无法读取 /etc/os-release。"
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == debian && "${VERSION_ID:-}" == 13 ]] || fail "VVV 仅支持 Debian 13。当前系统：${PRETTY_NAME:-未知}"

if ! command -v curl >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
  echo "APT/dpkg 锁最多等待 10 秒；超时将立即报错。"
  apt-get \
    -o DPkg::Lock::Timeout=10 \
    -o Acquire::Retries=2 \
    -o Acquire::PDiffs=false \
    -o Acquire::IndexTargets::deb-src::Sources::DefaultEnabled=false \
    update || fail "APT 更新失败。若提示锁被占用，脚本已等待最多 10 秒，请稍后重新运行。"
  DEBIAN_FRONTEND=noninteractive apt-get \
    -o DPkg::Lock::Timeout=10 \
    -o Acquire::Retries=2 \
    install -y curl ca-certificates bash python3 || fail "基础依赖安装失败。若提示锁被占用，脚本已等待最多 10 秒，请稍后重新运行。"
fi

if [[ -e /etc/vvv || -e /etc/jp-relay || -e /etc/vvv-sub || -e "$SOURCE_TARGET" ]]; then
  echo "检测到已有或上次中断留下的 VVV 状态。"
  echo "本次不会拒绝运行：将刷新安装源码并始终进入安装菜单，可续装、修复或追加角色。"
fi

nonce="$(date +%s)-$$"
mkdir -p "$TMP/app"
echo "正在下载 VVV 普通源码……"
curl -fsSL --retry 5 --retry-all-errors "$RAW/core-src/bootstrap.sh?v=$nonce" -o "$TMP/app/bootstrap.sh" || fail "下载 bootstrap.sh 失败。"
curl -fsSL --retry 5 --retry-all-errors "$RAW/src/prepare.py?v=$nonce" -o "$TMP/prepare.py" || fail "下载 prepare.py 失败。"
files=(host.sh landing.sh center_install.sh register_sync.sh vvv_manager.sh sub_center.py sync_agent.py backup_manager.py rclone_manager.sh client_adapters.py adapter_manager.py center_transport.sh center_manager.sh)
for file in "${files[@]}"; do
  printf '  下载 %s\n' "$file"
  curl -fsSL --retry 5 --retry-all-errors "$RAW/core-src/$file?v=$nonce-$file" -o "$TMP/app/$file" || fail "下载 $file 失败。"
  [[ -s "$TMP/app/$file" ]] || fail "$file 是空文件。"
done

python3 -m py_compile "$TMP/prepare.py"
python3 "$TMP/prepare.py" "$TMP/app/host.sh" "$TMP/app/landing.sh" "$TMP/app/center_install.sh" || fail "源码参数化处理失败。"
for file in bootstrap.sh center_install.sh register_sync.sh vvv_manager.sh rclone_manager.sh center_transport.sh center_manager.sh host.sh; do
  bash -n "$TMP/app/$file" || fail "$file 语法检查失败。"
done
sh -n "$TMP/app/landing.sh" || fail "landing.sh 语法检查失败。"
python3 -m py_compile "$TMP/app/sub_center.py" "$TMP/app/sync_agent.py" "$TMP/app/backup_manager.py" "$TMP/app/client_adapters.py" "$TMP/app/adapter_manager.py" || fail "Python 模块语法检查失败。"
python3 "$TMP/app/client_adapters.py" >/dev/null || fail "客户端适配器自检失败。"

# 新源码先复制到 /usr/local/lib 同一文件系统的暂存目录。
# 只有暂存副本完整后才切换；切换失败或进程被中断时，EXIT 清理会恢复旧源码。
install -d -m700 /usr/local/lib
SOURCE_STAGING="/usr/local/lib/.vvv-source.staging.$$"
SOURCE_BACKUP="/usr/local/lib/.vvv-source.previous.$$"
rm -rf "$SOURCE_STAGING" "$SOURCE_BACKUP"
cp -a "$TMP/app" "$SOURCE_STAGING" || fail "无法创建同盘源码暂存目录。"
chmod 700 "$SOURCE_STAGING"/*

if [[ -e "$SOURCE_TARGET" ]]; then
  mv "$SOURCE_TARGET" "$SOURCE_BACKUP" || fail "无法备份现有安装源码。"
fi
if ! mv "$SOURCE_STAGING" "$SOURCE_TARGET"; then
  [[ ! -e "$SOURCE_BACKUP" ]] || mv "$SOURCE_BACKUP" "$SOURCE_TARGET" 2>/dev/null || true
  fail "切换新安装源码失败，已尝试恢复旧源码。"
fi
SOURCE_SWAP_COMMITTED=1
rm -rf "$SOURCE_BACKUP"
SOURCE_BACKUP=""
SOURCE_STAGING=""

echo "VVV 普通源码下载和语法检查全部通过。"
if [[ -r /dev/tty ]]; then
  exec bash "$SOURCE_TARGET/bootstrap.sh" </dev/tty
else
  exec bash "$SOURCE_TARGET/bootstrap.sh"
fi

#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

RAW="https://raw.githubusercontent.com/weizhiok/vvv/main"
TMP="$(mktemp -d /tmp/vvv-install.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
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

if [[ -e /etc/vvv || -e /etc/jp-relay || -e /etc/vvv-sub || -e /usr/local/lib/vvv-source ]]; then
  echo "检测到已有或上次中断留下的 VVV 状态。"
  echo "本次不会拒绝运行：将刷新安装源码并始终进入安装菜单，可续装、修复或追加角色。"
fi

nonce="$(date +%s)-$$"
mkdir -p "$TMP/app"
echo "正在下载 VVV 普通源码……"
curl -fsSL --retry 5 --retry-all-errors "$RAW/core-src/bootstrap.sh?v=$nonce" -o "$TMP/app/bootstrap.sh" || fail "下载 bootstrap.sh 失败。"
curl -fsSL --retry 5 --retry-all-errors "$RAW/src/prepare.py?v=$nonce" -o "$TMP/prepare.py" || fail "下载 prepare.py 失败。"
files=(host.sh landing.sh center_install.sh register_sync.sh vvv_manager.sh sub_center.py sync_agent.py backup_manager.py rclone_manager.sh)
for file in "${files[@]}"; do
  printf '  下载 %s\n' "$file"
  curl -fsSL --retry 5 --retry-all-errors "$RAW/core-src/$file?v=$nonce-$file" -o "$TMP/app/$file" || fail "下载 $file 失败。"
  [[ -s "$TMP/app/$file" ]] || fail "$file 是空文件。"
done

python3 -m py_compile "$TMP/prepare.py"
python3 "$TMP/prepare.py" "$TMP/app/host.sh" "$TMP/app/landing.sh" "$TMP/app/center_install.sh" || fail "源码参数化处理失败。"
for file in bootstrap.sh center_install.sh register_sync.sh vvv_manager.sh rclone_manager.sh host.sh; do
  bash -n "$TMP/app/$file" || fail "$file 语法检查失败。"
done
sh -n "$TMP/app/landing.sh" || fail "landing.sh 语法检查失败。"
python3 -m py_compile "$TMP/app/sub_center.py" "$TMP/app/sync_agent.py" "$TMP/app/backup_manager.py" || fail "Python 模块语法检查失败。"

# 只有在新源码全部下载并通过语法检查后才原子替换本地副本。
# SSH 中断不会留下“半套源码”，下次运行仍会刷新源码并进入安装菜单。
target=/usr/local/lib/vvv-source
backup="/usr/local/lib/.vvv-source.previous.$$"
install -d -m700 /usr/local/lib
rm -rf "$backup"
if [[ -e "$target" ]]; then
  mv "$target" "$backup"
fi
mv "$TMP/app" "$target"
chmod 700 "$target"/*
rm -rf "$backup"

echo "VVV 普通源码下载和语法检查全部通过。"
if [[ -r /dev/tty ]]; then
  exec bash "$target/bootstrap.sh" </dev/tty
else
  exec bash "$target/bootstrap.sh"
fi

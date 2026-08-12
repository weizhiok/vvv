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
[[ "${ID:-}" == debian && "${VERSION_ID:-}" =~ ^(12|13)$ ]] || fail "VVV 仅支持 Debian 12/13。当前系统：${PRETTY_NAME:-未知}"

repair_dpkg_state() {
  local audit log bad_file backup_dir="" attempt configured=0 fix_broken_attempted=0
  local dpkg_admin_dir backup_root status_file file_name
  dpkg_admin_dir="${VVV_DPKG_ADMIN_DIR:-/var/lib/dpkg}"
  backup_root="${VVV_DPKG_BACKUP_ROOT:-/var/backups}"
  command -v dpkg >/dev/null 2>&1 || fail "当前 Debian 找不到 dpkg，无法继续安装。"
  export DEBIAN_FRONTEND=noninteractive
  export NEEDRESTART_MODE=a

  echo "检查并修复 dpkg 配置状态……"
  for attempt in 1 2 3 4 5 6 7 8; do
    log="$(mktemp /tmp/vvv-dpkg-configure.XXXXXX)"
    if LC_ALL=C dpkg --force-confold --configure -a >"$log" 2>&1; then
      cat "$log"
      rm -f "$log"
      configured=1
      break
    fi
    cat "$log" >&2

    bad_file="$(sed -n "s#^dpkg: error: parsing file '\([^']*\)'.*#\1#p" "$log" | head -n1)"
    case "$bad_file" in
      "$dpkg_admin_dir"/updates/[0-9][0-9][0-9][0-9]) ;;
      *) bad_file="" ;;
    esac

    if [[ -n "$bad_file" ]]; then
      [[ -f "$bad_file" && ! -L "$bad_file" ]] || {
        rm -f "$log"
        fail "dpkg 报告的 updates 临时文件不是普通文件，拒绝自动处理：$bad_file"
      }
      if [[ -z "$backup_dir" ]]; then
        backup_dir="${backup_root}/vvv-dpkg-recovery-$(date +%Y%m%d-%H%M%S)-$$"
        mkdir -p "$backup_dir/updates" || { rm -f "$log"; fail "无法创建 dpkg 修复备份目录。"; }
        chmod 700 "$backup_dir" "$backup_dir/updates" || { rm -f "$log"; fail "无法保护 dpkg 修复备份目录权限。"; }
        for status_file in "$dpkg_admin_dir/status" "$dpkg_admin_dir/status-old"; do
          if [[ -f "$status_file" ]]; then
            cp -a -- "$status_file" "$backup_dir/" || { rm -f "$log"; fail "备份 dpkg 主状态文件失败，拒绝继续。"; }
          fi
        done
      fi
      file_name="${bad_file##*/}"
      mv -- "$bad_file" "$backup_dir/updates/$file_name" || { rm -f "$log"; fail "隔离损坏的 dpkg 临时更新文件失败。"; }
      echo "检测到损坏的 dpkg 临时更新文件：$bad_file"
      echo "已隔离备份到：$backup_dir/updates/$file_name"
      rm -f "$log"
      continue
    fi

    rm -f "$log"
    if (( fix_broken_attempted == 1 )); then
      fail "dpkg 在依赖修复后仍无法完成配置；已停止安装，请检查上方具体软件包错误。"
    fi
    fix_broken_attempted=1
    echo "dpkg 配置未完成，但不是可安全隔离的 updates/NNNN 解析损坏；尝试修复依赖（禁止自动删除软件包）……"
    apt-get \
      -o DPkg::Lock::Timeout=10 \
      -o Acquire::Retries=2 \
      -o Acquire::PDiffs=false \
      -o Acquire::IndexTargets::deb-src::Sources::DefaultEnabled=false \
      update || fail "修复 dpkg 前刷新 APT 索引失败。若提示锁被占用，请等待系统自动更新结束后重试。"
    apt-get \
      -o DPkg::Lock::Timeout=10 \
      -o Acquire::Retries=2 \
      -o Dpkg::Options::=--force-confold \
      --fix-broken --no-remove install -y --no-install-recommends \
      || fail "自动修复损坏依赖失败；为避免误删系统软件包，脚本已停止。"
  done

  (( configured == 1 )) || fail "dpkg 连续修复后仍无法完成配置；已停止安装。"
  audit="$(LC_ALL=C dpkg --audit 2>/dev/null || true)"
  if [[ -n "$audit" ]]; then
    echo "dpkg 审计仍发现异常：" >&2
    printf '%s
' "$audit" >&2
    fail "dpkg 状态仍不完整，已停止安装，未删除任何锁文件或软件包。"
  fi
  echo "dpkg 状态：正常。"
  [[ -z "$backup_dir" ]] || echo "dpkg 修复备份：$backup_dir"
}

repair_dpkg_state

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
curl -fsSL --retry 5 --retry-all-errors "$RAW/src/validate_embedded_python.py?v=$nonce" -o "$TMP/validate_embedded_python.py" || fail "下载内嵌 Python 检查器失败。"
files=(host.sh landing.sh center_install.sh register_sync.sh vvv_manager.sh sub_center.py sync_agent.py backup_manager.py rclone_manager.sh client_adapters.py client_package_renderer.py name_guard_runtime.py name_guard_installer.py debian_compat.py adapter_manager.py client_upgrade_engine.py client_local_renderer.py hy2_port_hop.py hy2_port_hop.sh center_transport.sh center_manager.sh restore_manager.py diagnostic_report.py node_probe.py)
for file in "${files[@]}"; do
  printf '  下载 %s\n' "$file"
  curl -fsSL --retry 5 --retry-all-errors "$RAW/core-src/$file?v=$nonce-$file" -o "$TMP/app/$file" || fail "下载 $file 失败。"
  [[ -s "$TMP/app/$file" ]] || fail "$file 是空文件。"
done

python3 -m py_compile "$TMP/prepare.py" "$TMP/validate_embedded_python.py" "$TMP/app/debian_compat.py"
python3 "$TMP/app/debian_compat.py" "$TMP/app" >/dev/null || fail "Debian 12/13 兼容性准备失败。"
python3 "$TMP/prepare.py" "$TMP/app/host.sh" "$TMP/app/landing.sh" "$TMP/app/center_install.sh" || fail "源码参数化处理失败。"
python3 "$TMP/validate_embedded_python.py"   "$TMP/app/bootstrap.sh" "$TMP/app/host.sh" "$TMP/app/landing.sh"   "$TMP/app/center_install.sh" "$TMP/app/register_sync.sh" "$TMP/app/vvv_manager.sh"   "$TMP/app/rclone_manager.sh" "$TMP/app/center_transport.sh" "$TMP/app/center_manager.sh"   || fail "Shell 内嵌 Python 语法检查失败。"
for file in bootstrap.sh center_install.sh register_sync.sh vvv_manager.sh rclone_manager.sh center_transport.sh center_manager.sh host.sh hy2_port_hop.sh; do
  bash -n "$TMP/app/$file" || fail "$file 语法检查失败。"
done
sh -n "$TMP/app/landing.sh" || fail "landing.sh 语法检查失败。"
python3 -m py_compile "$TMP/app/sub_center.py" "$TMP/app/sync_agent.py" "$TMP/app/backup_manager.py" "$TMP/app/client_adapters.py" "$TMP/app/client_package_renderer.py" "$TMP/app/name_guard_runtime.py" "$TMP/app/name_guard_installer.py" "$TMP/app/debian_compat.py" "$TMP/app/adapter_manager.py" "$TMP/app/client_upgrade_engine.py" "$TMP/app/client_local_renderer.py" "$TMP/app/hy2_port_hop.py" "$TMP/app/restore_manager.py" "$TMP/app/diagnostic_report.py" "$TMP/app/node_probe.py" || fail "Python 模块语法检查失败。"
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

# 客户端支持运行时在角色安装前单独落盘。后期“升级客户端支持”只会
# 替换客户端适配器并重绘客户端文件，不会重新进入完整安装器。
install -d -m700 /usr/local/lib/vvv
install -m755 "$SOURCE_TARGET/client_upgrade_engine.py" /usr/local/lib/vvv/client_upgrade_engine.py
install -m755 "$SOURCE_TARGET/client_local_renderer.py" /usr/local/lib/vvv/client_local_renderer.py
install -m755 "$SOURCE_TARGET/name_guard_runtime.py" /usr/local/lib/vvv/name_guard_runtime.py
install -m755 "$SOURCE_TARGET/name_guard_installer.py" /usr/local/lib/vvv/name_guard_installer.py
python3 /usr/local/lib/vvv/name_guard_installer.py "$SOURCE_TARGET/center_install.sh" >/dev/null || fail "准备订阅中心全局名称保护失败。"
if [[ ! -f /usr/local/lib/vvv/client_adapters.py ]]; then
  install -m755 "$SOURCE_TARGET/client_adapters.py" /usr/local/lib/vvv/client_adapters.py
fi

echo "VVV 普通源码下载和语法检查全部通过（Debian ${VERSION_ID}）。"
if [[ -r /dev/tty ]]; then
  exec bash "$SOURCE_TARGET/bootstrap.sh" </dev/tty
else
  exec bash "$SOURCE_TARGET/bootstrap.sh"
fi

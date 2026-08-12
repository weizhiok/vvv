#!/usr/bin/env python3
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]

BASH_FN = r'''repair_dpkg_state() {
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

    bad_file="$(sed -n "s#^dpkg: error: parsing file '\\([^']*\\)'.*#\\1#p" "$log" | head -n1)"
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
    printf '%s\n' "$audit" >&2
    fail "dpkg 状态仍不完整，已停止安装，未删除任何锁文件或软件包。"
  fi
  echo "dpkg 状态：正常。"
  [[ -z "$backup_dir" ]] || echo "dpkg 修复备份：$backup_dir"
}
'''

SH_FN = r'''repair_dpkg_state() {
  DPKG_REPAIR_ADMIN_DIR="${VVV_DPKG_ADMIN_DIR:-/var/lib/dpkg}"
  DPKG_REPAIR_BACKUP_ROOT="${VVV_DPKG_BACKUP_ROOT:-/var/backups}"
  DPKG_REPAIR_BACKUP_DIR=""
  DPKG_REPAIR_CONFIGURED=0
  DPKG_REPAIR_FIX_BROKEN=0
  DPKG_REPAIR_ATTEMPT=1
  command -v dpkg >/dev/null 2>&1 || fail "当前 Debian 找不到 dpkg，无法继续安装。"
  export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a

  echo "检查并修复 dpkg 配置状态……"
  while [ "$DPKG_REPAIR_ATTEMPT" -le 8 ]; do
    DPKG_REPAIR_LOG="$(mktemp /tmp/vvv-dpkg-configure.XXXXXX)"
    if LC_ALL=C dpkg --force-confold --configure -a >"$DPKG_REPAIR_LOG" 2>&1; then
      cat "$DPKG_REPAIR_LOG"
      rm -f "$DPKG_REPAIR_LOG"
      DPKG_REPAIR_CONFIGURED=1
      break
    fi
    cat "$DPKG_REPAIR_LOG" >&2

    DPKG_REPAIR_BAD_FILE="$(sed -n "s#^dpkg: error: parsing file '\\([^']*\\)'.*#\\1#p" "$DPKG_REPAIR_LOG" | head -n1)"
    case "$DPKG_REPAIR_BAD_FILE" in
      "$DPKG_REPAIR_ADMIN_DIR"/updates/[0-9][0-9][0-9][0-9]) ;;
      *) DPKG_REPAIR_BAD_FILE="" ;;
    esac

    if [ -n "$DPKG_REPAIR_BAD_FILE" ]; then
      if [ ! -f "$DPKG_REPAIR_BAD_FILE" ] || [ -L "$DPKG_REPAIR_BAD_FILE" ]; then
        rm -f "$DPKG_REPAIR_LOG"
        fail "dpkg 报告的 updates 临时文件不是普通文件，拒绝自动处理：$DPKG_REPAIR_BAD_FILE"
      fi
      if [ -z "$DPKG_REPAIR_BACKUP_DIR" ]; then
        DPKG_REPAIR_BACKUP_DIR="${DPKG_REPAIR_BACKUP_ROOT}/vvv-dpkg-recovery-$(date +%Y%m%d-%H%M%S)-$$"
        mkdir -p "$DPKG_REPAIR_BACKUP_DIR/updates" || { rm -f "$DPKG_REPAIR_LOG"; fail "无法创建 dpkg 修复备份目录。"; }
        chmod 700 "$DPKG_REPAIR_BACKUP_DIR" "$DPKG_REPAIR_BACKUP_DIR/updates" || { rm -f "$DPKG_REPAIR_LOG"; fail "无法保护 dpkg 修复备份目录权限。"; }
        for DPKG_REPAIR_STATUS_FILE in "$DPKG_REPAIR_ADMIN_DIR/status" "$DPKG_REPAIR_ADMIN_DIR/status-old"; do
          if [ -f "$DPKG_REPAIR_STATUS_FILE" ]; then
            cp -a -- "$DPKG_REPAIR_STATUS_FILE" "$DPKG_REPAIR_BACKUP_DIR/" || { rm -f "$DPKG_REPAIR_LOG"; fail "备份 dpkg 主状态文件失败，拒绝继续。"; }
          fi
        done
      fi
      DPKG_REPAIR_FILE_NAME="${DPKG_REPAIR_BAD_FILE##*/}"
      mv -- "$DPKG_REPAIR_BAD_FILE" "$DPKG_REPAIR_BACKUP_DIR/updates/$DPKG_REPAIR_FILE_NAME" || { rm -f "$DPKG_REPAIR_LOG"; fail "隔离损坏的 dpkg 临时更新文件失败。"; }
      echo "检测到损坏的 dpkg 临时更新文件：$DPKG_REPAIR_BAD_FILE"
      echo "已隔离备份到：$DPKG_REPAIR_BACKUP_DIR/updates/$DPKG_REPAIR_FILE_NAME"
      rm -f "$DPKG_REPAIR_LOG"
      DPKG_REPAIR_ATTEMPT=$((DPKG_REPAIR_ATTEMPT + 1))
      continue
    fi

    rm -f "$DPKG_REPAIR_LOG"
    if [ "$DPKG_REPAIR_FIX_BROKEN" -eq 1 ]; then
      fail "dpkg 在依赖修复后仍无法完成配置；已停止安装，请检查上方具体软件包错误。"
    fi
    DPKG_REPAIR_FIX_BROKEN=1
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
    DPKG_REPAIR_ATTEMPT=$((DPKG_REPAIR_ATTEMPT + 1))
  done

  [ "$DPKG_REPAIR_CONFIGURED" -eq 1 ] || fail "dpkg 连续修复后仍无法完成配置；已停止安装。"
  DPKG_REPAIR_AUDIT="$(LC_ALL=C dpkg --audit 2>/dev/null || true)"
  if [ -n "$DPKG_REPAIR_AUDIT" ]; then
    echo "dpkg 审计仍发现异常：" >&2
    printf '%s\n' "$DPKG_REPAIR_AUDIT" >&2
    fail "dpkg 状态仍不完整，已停止安装，未删除任何锁文件或软件包。"
  fi
  echo "dpkg 状态：正常。"
  [ -z "$DPKG_REPAIR_BACKUP_DIR" ] || echo "dpkg 修复备份：$DPKG_REPAIR_BACKUP_DIR"
}
'''


def replace_function(path: Path, replacement: str) -> None:
    text = path.read_text(encoding='utf-8')
    pattern = re.compile(r'^repair_dpkg_state\(\) \{\n.*?^\}\n', re.M | re.S)
    updated, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise SystemExit(f'{path}: expected one repair_dpkg_state function, found {count}')
    path.write_text(updated, encoding='utf-8')


for rel in ('vvv-install.sh', 'core-src/host.sh', 'core-src/center_install.sh'):
    replace_function(ROOT / rel, BASH_FN)
replace_function(ROOT / 'core-src/landing.sh', SH_FN)

subprocess.run(['bash', '-n', str(ROOT / 'vvv-install.sh')], check=True)
subprocess.run(['bash', '-n', str(ROOT / 'core-src/host.sh')], check=True)
subprocess.run(['bash', '-n', str(ROOT / 'core-src/center_install.sh')], check=True)
subprocess.run(['sh', '-n', str(ROOT / 'core-src/landing.sh')], check=True)
print('patched corrupt dpkg updates recovery')

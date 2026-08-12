#!/usr/bin/env python3
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]

BASH_FN = r'''repair_dpkg_state() {
  local audit log bad_file backup_dir="" attempt configured=0 fix_broken_attempted=0
  local dpkg_admin_dir backup_root status_file file_name update_candidate_count max_attempts quarantined_count=0
  dpkg_admin_dir="${VVV_DPKG_ADMIN_DIR:-/var/lib/dpkg}"
  backup_root="${VVV_DPKG_BACKUP_ROOT:-/var/backups}"
  command -v dpkg >/dev/null 2>&1 || fail "当前 Debian 找不到 dpkg，无法继续安装。"
  export DEBIAN_FRONTEND=noninteractive
  export NEEDRESTART_MODE=a

  update_candidate_count=0
  if [[ -d "$dpkg_admin_dir/updates" ]]; then
    update_candidate_count="$(find "$dpkg_admin_dir/updates" -maxdepth 1 -mindepth 1 -name '[0-9][0-9][0-9][0-9]' -printf '.' 2>/dev/null | wc -c | tr -d '[:space:]')"
  fi
  [[ "$update_candidate_count" =~ ^[0-9]+$ ]] || update_candidate_count=0
  (( update_candidate_count <= 10000 )) || fail "dpkg updates/NNNN 条目数量异常（${update_candidate_count}），拒绝自动处理。"
  max_attempts=$((update_candidate_count + 3))

  echo "检查并修复 dpkg 配置状态……"
  for ((attempt=1; attempt<=max_attempts; attempt++)); do
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
      [[ ! -e "$backup_dir/updates/$file_name" ]] || { rm -f "$log"; fail "dpkg 修复备份目录已存在同名文件，拒绝覆盖：$file_name"; }
      mv -- "$bad_file" "$backup_dir/updates/$file_name" || { rm -f "$log"; fail "隔离损坏的 dpkg 临时更新文件失败。"; }
      quarantined_count=$((quarantined_count + 1))
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

  (( configured == 1 )) || fail "dpkg 在按 updates/NNNN 实际数量计算的安全重试范围内仍无法完成配置；已停止安装。"
  audit="$(LC_ALL=C dpkg --audit 2>/dev/null || true)"
  if [[ -n "$audit" ]]; then
    echo "dpkg 审计仍发现异常：" >&2
    printf '%s\n' "$audit" >&2
    fail "dpkg 状态仍不完整，已停止安装，未删除任何锁文件或软件包。"
  fi
  echo "dpkg 状态：正常。"
  if [[ -n "$backup_dir" ]]; then
    echo "本次共隔离损坏的 dpkg 临时更新文件：${quarantined_count} 个"
    echo "dpkg 修复备份：$backup_dir"
  fi
}
'''

SH_FN = r'''repair_dpkg_state() {
  DPKG_REPAIR_ADMIN_DIR="${VVV_DPKG_ADMIN_DIR:-/var/lib/dpkg}"
  DPKG_REPAIR_BACKUP_ROOT="${VVV_DPKG_BACKUP_ROOT:-/var/backups}"
  DPKG_REPAIR_BACKUP_DIR=""
  DPKG_REPAIR_CONFIGURED=0
  DPKG_REPAIR_FIX_BROKEN=0
  DPKG_REPAIR_QUARANTINED=0
  command -v dpkg >/dev/null 2>&1 || fail "当前 Debian 找不到 dpkg，无法继续安装。"
  export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a

  DPKG_REPAIR_UPDATE_COUNT=0
  if [ -d "$DPKG_REPAIR_ADMIN_DIR/updates" ]; then
    DPKG_REPAIR_UPDATE_COUNT="$(find "$DPKG_REPAIR_ADMIN_DIR/updates" -maxdepth 1 -mindepth 1 -name '[0-9][0-9][0-9][0-9]' -printf '.' 2>/dev/null | wc -c | tr -d '[:space:]')"
  fi
  case "$DPKG_REPAIR_UPDATE_COUNT" in ''|*[!0-9]*) DPKG_REPAIR_UPDATE_COUNT=0;; esac
  [ "$DPKG_REPAIR_UPDATE_COUNT" -le 10000 ] || fail "dpkg updates/NNNN 条目数量异常（${DPKG_REPAIR_UPDATE_COUNT}），拒绝自动处理。"
  DPKG_REPAIR_MAX_ATTEMPTS=$((DPKG_REPAIR_UPDATE_COUNT + 3))
  DPKG_REPAIR_ATTEMPT=1

  echo "检查并修复 dpkg 配置状态……"
  while [ "$DPKG_REPAIR_ATTEMPT" -le "$DPKG_REPAIR_MAX_ATTEMPTS" ]; do
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
      if [ -e "$DPKG_REPAIR_BACKUP_DIR/updates/$DPKG_REPAIR_FILE_NAME" ]; then
        rm -f "$DPKG_REPAIR_LOG"
        fail "dpkg 修复备份目录已存在同名文件，拒绝覆盖：$DPKG_REPAIR_FILE_NAME"
      fi
      mv -- "$DPKG_REPAIR_BAD_FILE" "$DPKG_REPAIR_BACKUP_DIR/updates/$DPKG_REPAIR_FILE_NAME" || { rm -f "$DPKG_REPAIR_LOG"; fail "隔离损坏的 dpkg 临时更新文件失败。"; }
      DPKG_REPAIR_QUARANTINED=$((DPKG_REPAIR_QUARANTINED + 1))
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

  [ "$DPKG_REPAIR_CONFIGURED" -eq 1 ] || fail "dpkg 在按 updates/NNNN 实际数量计算的安全重试范围内仍无法完成配置；已停止安装。"
  DPKG_REPAIR_AUDIT="$(LC_ALL=C dpkg --audit 2>/dev/null || true)"
  if [ -n "$DPKG_REPAIR_AUDIT" ]; then
    echo "dpkg 审计仍发现异常：" >&2
    printf '%s\n' "$DPKG_REPAIR_AUDIT" >&2
    fail "dpkg 状态仍不完整，已停止安装，未删除任何锁文件或软件包。"
  fi
  echo "dpkg 状态：正常。"
  if [ -n "$DPKG_REPAIR_BACKUP_DIR" ]; then
    echo "本次共隔离损坏的 dpkg 临时更新文件：${DPKG_REPAIR_QUARANTINED} 个"
    echo "dpkg 修复备份：$DPKG_REPAIR_BACKUP_DIR"
  fi
}
'''


def replace_function(path: Path, replacement: str) -> None:
    text = path.read_text(encoding='utf-8')
    pattern = re.compile(r'^repair_dpkg_state\(\) \{\n.*?^\}\n', re.M | re.S)
    updated, count = pattern.subn(lambda _: replacement, text, count=1)
    if count != 1:
        raise SystemExit(f'{path}: expected one repair_dpkg_state function, found {count}')
    path.write_text(updated, encoding='utf-8')


for rel in ('vvv-install.sh', 'core-src/host.sh', 'core-src/center_install.sh'):
    replace_function(ROOT / rel, BASH_FN)
replace_function(ROOT / 'core-src/landing.sh', SH_FN)

# Upgrade the regression test so it proves recovery of more than eight corrupt fragments.
test_path = ROOT / 'tests' / 'test_interrupted_dpkg_repair.py'
t = test_path.read_text(encoding='utf-8')
t = t.replace(
    "    if mode == 'corrupt-update':\n        (updates / '0000').write_text('Status', encoding='utf-8')\n",
    "    if mode == 'corrupt-update':\n        for i in range(12):\n            (updates / f'{i:04d}').write_text(f'Status-{i:04d}', encoding='utf-8')\n",
)
t = t.replace(
    "        '    if [ \"$mode\" = corrupt-update ] && [ -f \"$admin/updates/0000\" ]; then\\n'\n        \"      echo \\\"dpkg: error: parsing file '$admin/updates/0000' near line 0:\\\" >&2\\n\"\n        \"      echo \\\"end of file after field name ''\\\" >&2\\n\"\n        '      exit 2\\n'\n        '    fi\\n'\n",
    "        '    if [ \"$mode\" = corrupt-update ]; then\\n'\n        '      bad=\"$(find \"$admin/updates\" -maxdepth 1 -type f -name \'[0-9][0-9][0-9][0-9]\' | sort | head -n1)\"\\n'\n        '      if [ -n \"$bad\" ]; then\\n'\n        \"        echo \\\"dpkg: error: parsing file '$bad' near line 0:\\\" >&2\\n\"\n        \"        echo \\\"end of file after field name ''\\\" >&2\\n\"\n        '        exit 2\\n'\n        '      fi\\n'\n        '    fi\\n'\n",
)
t = t.replace(
    "            'update_exists': (admin / 'updates' / '0000').exists(),\n            'backup_count': len(backup_dirs),\n            'backup_has_update': False,\n            'backup_update_content': '',\n",
    "            'remaining_updates': sorted(p.name for p in (admin / 'updates').glob('[0-9][0-9][0-9][0-9]')),\n            'backup_count': len(backup_dirs),\n            'backup_updates': [],\n            'backup_update_contents': {},\n",
)
t = t.replace(
    "            saved = backup / 'updates' / '0000'\n            snapshot['backup_has_update'] = saved.is_file()\n            snapshot['backup_update_content'] = saved.read_text(encoding='utf-8') if saved.is_file() else ''\n",
    "            saved_dir = backup / 'updates'\n            saved = sorted(saved_dir.glob('[0-9][0-9][0-9][0-9]')) if saved_dir.exists() else []\n            snapshot['backup_updates'] = [p.name for p in saved]\n            snapshot['backup_update_contents'] = {p.name: p.read_text(encoding='utf-8') for p in saved}\n",
)
t = t.replace(
    "            'updates/[0-9][0-9][0-9][0-9]',\n            '已隔离备份到',\n",
    "            'updates/[0-9][0-9][0-9][0-9]',\n            'update_candidate_count',\n            'max_attempts',\n            '已隔离备份到',\n",
)
t = t.replace(
    "    assert 'parsing file' in output and 'updates/0000' in output\n",
    "    assert 'parsing file' in output and 'updates/0000' in output and 'updates/0011' in output\n",
)
t = t.replace(
    "    assert not snapshot['update_exists'], 'corrupt update fragment must leave dpkg updates directory'\n    assert snapshot['backup_count'] == 1\n    assert snapshot['backup_has_update'] and snapshot['backup_update_content'] == 'Status'\n",
    "    assert snapshot['remaining_updates'] == [], 'all twelve corrupt fragments must leave the dpkg updates directory'\n    assert snapshot['backup_count'] == 1\n    expected = [f'{i:04d}' for i in range(12)]\n    assert snapshot['backup_updates'] == expected\n    assert snapshot['backup_update_contents'] == {name: f'Status-{name}' for name in expected}\n    assert '本次共隔离损坏的 dpkg 临时更新文件：12 个' in output\n",
)
t = t.replace(
    "    print('PASS dpkg recovery handles clean, dependency-broken, and corrupt updates/NNNN states safely')\n",
    "    print('PASS dpkg recovery safely handles clean, dependency-broken, and more-than-eight corrupt updates/NNNN fragments')\n",
)
if "for attempt in 1 2 3 4 5 6 7 8" in t:
    raise SystemExit('test file unexpectedly contains the legacy fixed retry loop')
test_path.write_text(t, encoding='utf-8')

subprocess.run(['bash', '-n', str(ROOT / 'vvv-install.sh')], check=True)
subprocess.run(['bash', '-n', str(ROOT / 'core-src/host.sh')], check=True)
subprocess.run(['bash', '-n', str(ROOT / 'core-src/center_install.sh')], check=True)
subprocess.run(['sh', '-n', str(ROOT / 'core-src/landing.sh')], check=True)
subprocess.run(['python3', '-m', 'py_compile', str(test_path)], check=True)
print('patched dynamic dpkg update recovery and >8-fragment regression')

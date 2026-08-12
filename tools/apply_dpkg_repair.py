#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BASH_REPAIR = r'''repair_dpkg_state() {
  local audit
  command -v dpkg >/dev/null 2>&1 || fail "当前 Debian 找不到 dpkg，无法继续安装。"
  export DEBIAN_FRONTEND=noninteractive
  export NEEDRESTART_MODE=a

  echo "检查并修复 dpkg 配置状态……"
  if ! dpkg --force-confold --configure -a; then
    echo "检测到未完成或依赖异常的 dpkg 状态，尝试安全修复（禁止自动删除软件包）……"
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
    dpkg --force-confold --configure -a \
      || fail "dpkg 仍有未完成配置，请检查上方具体软件包错误后重试。"
  fi

  audit="$(dpkg --audit 2>/dev/null || true)"
  if [[ -n "$audit" ]]; then
    echo "dpkg 审计仍发现异常：" >&2
    printf '%s\n' "$audit" >&2
    fail "dpkg 状态仍不完整，已停止安装，未删除任何锁文件或软件包。"
  fi
  echo "dpkg 状态：正常。"
}'''

SH_REPAIR = r'''repair_dpkg_state() {
  command -v dpkg >/dev/null 2>&1 || fail "当前 Debian 找不到 dpkg，无法继续安装。"
  export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a

  echo "检查并修复 dpkg 配置状态……"
  if ! dpkg --force-confold --configure -a; then
    echo "检测到未完成或依赖异常的 dpkg 状态，尝试安全修复（禁止自动删除软件包）……"
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
    dpkg --force-confold --configure -a \
      || fail "dpkg 仍有未完成配置，请检查上方具体软件包错误后重试。"
  fi

  audit="$(dpkg --audit 2>/dev/null || true)"
  if [ -n "$audit" ]; then
    echo "dpkg 审计仍发现异常：" >&2
    printf '%s\n' "$audit" >&2
    fail "dpkg 状态仍不完整，已停止安装，未删除任何锁文件或软件包。"
  fi
  echo "dpkg 状态：正常。"
}'''


def replace_once(path: Path, old: str, new: str, label: str):
    text = path.read_text(encoding='utf-8')
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected 1 match, got {count}')
    path.write_text(text.replace(old, new, 1), encoding='utf-8')


def patch_installer():
    path = ROOT / 'vvv-install.sh'
    old = '''[[ "${ID:-}" == debian && "${VERSION_ID:-}" =~ ^(12|13)$ ]] || fail "VVV 仅支持 Debian 12/13。当前系统：${PRETTY_NAME:-未知}"

if ! command -v curl >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then'''
    new = '''[[ "${ID:-}" == debian && "${VERSION_ID:-}" =~ ^(12|13)$ ]] || fail "VVV 仅支持 Debian 12/13。当前系统：${PRETTY_NAME:-未知}"

''' + BASH_REPAIR + '''

repair_dpkg_state

if ! command -v curl >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then'''
    replace_once(path, old, new, 'vvv-install')


def patch_host():
    path = ROOT / 'core-src/host.sh'
    old = '''  (( IS_CONTAINER == 0 )) || echo "提示：检测到受限容器，Swap、BBR 和定时重启将按环境能力尽力配置。"
}

upgrade_system_once() {
  export DEBIAN_FRONTEND=noninteractive
  export NEEDRESTART_MODE=a
'''
    new = '''  (( IS_CONTAINER == 0 )) || echo "提示：检测到受限容器，Swap、BBR 和定时重启将按环境能力尽力配置。"
}

''' + BASH_REPAIR + '''

upgrade_system_once() {
  export DEBIAN_FRONTEND=noninteractive
  export NEEDRESTART_MODE=a
  repair_dpkg_state
'''
    replace_once(path, old, new, 'host')


def patch_landing():
    path = ROOT / 'core-src/landing.sh'
    old = '''  [ "$IS_CONTAINER" -eq 0 ] || echo "虚拟化环境：受限容器（内核参数由宿主机控制）"
}

upgrade_system_once() {
  mkdir -p "$(dirname "$UPGRADE_MARKER")"
  export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
'''
    new = '''  [ "$IS_CONTAINER" -eq 0 ] || echo "虚拟化环境：受限容器（内核参数由宿主机控制）"
}

''' + SH_REPAIR + '''

upgrade_system_once() {
  mkdir -p "$(dirname "$UPGRADE_MARKER")"
  export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
  repair_dpkg_state
'''
    replace_once(path, old, new, 'landing')


def patch_center():
    path = ROOT / 'core-src/center_install.sh'
    old = '''  echo "${label}失败。" >&2; rm -f "$log"; return 1
}
install_caddy(){'''
    new = '''  echo "${label}失败。" >&2; rm -f "$log"; return 1
}

''' + BASH_REPAIR + '''

install_caddy(){'''
    replace_once(path, old, new, 'center function')
    old2 = '''section "准备订阅中心依赖"
required=(ca-certificates curl jq openssl python3 tar gzip)'''
    new2 = '''section "准备订阅中心依赖"
repair_dpkg_state
required=(ca-certificates curl jq openssl python3 tar gzip)'''
    replace_once(path, old2, new2, 'center call')


for fn in (patch_installer, patch_host, patch_landing, patch_center):
    fn()
print('patched interrupted dpkg recovery into all fresh-install apt paths')

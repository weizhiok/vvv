#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing anchor: {label}")
    return text.replace(old, new, 1)


# Network installer: never wait more than 10 seconds for APT/dpkg locks.
path = ROOT / "vvv-install.sh"
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    '''if ! command -v curl >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
  apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 update
  DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 install -y curl ca-certificates bash python3
fi''',
    '''if ! command -v curl >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
  echo "APT/dpkg 锁最多等待 10 秒；超时将立即报错。"
  apt-get \\
    -o DPkg::Lock::Timeout=10 \\
    -o Acquire::Retries=2 \\
    -o Acquire::PDiffs=false \\
    -o Acquire::IndexTargets::deb::Sources::DefaultEnabled=false \\
    update || fail "APT 更新失败。若提示锁被占用，脚本已等待最多 10 秒，请稍后重新运行。"
  DEBIAN_FRONTEND=noninteractive apt-get \\
    -o DPkg::Lock::Timeout=10 \\
    -o Acquire::Retries=2 \\
    install -y curl ca-certificates bash python3 || fail "基础依赖安装失败。若提示锁被占用，脚本已等待最多 10 秒，请稍后重新运行。"
fi''',
    "network installer apt block",
)
path.write_text(text, encoding="utf-8")


# Main host installer: install python3-venv in the first package transaction so
# the subscription center does not immediately invoke APT again.
path = ROOT / "core-src/host.sh"
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    '''  retry 5 10 apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 -o DPkg::Lock::Timeout=120 -o Acquire::PDiffs=false update
  dpkg --configure -a >/dev/null 2>&1 || true
  retry 3 10 apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 -o DPkg::Lock::Timeout=120 install -y --no-install-recommends \\
    ca-certificates curl unzip tar gzip openssl jq python3 iproute2 procps \\
    tzdata kmod util-linux''',
    '''  echo "APT/dpkg 锁最多等待 10 秒；超时立即报错，不删除锁，也不终止系统自动更新。"
  apt-get \\
    -o DPkg::Lock::Timeout=10 \\
    -o Acquire::Retries=2 \\
    -o Acquire::PDiffs=false \\
    -o Acquire::IndexTargets::deb::Sources::DefaultEnabled=false \\
    update || fail "APT 更新失败。若提示锁被占用，已等待最多 10 秒，请稍后重新运行。"
  apt-get \\
    -o DPkg::Lock::Timeout=10 \\
    -o Acquire::Retries=2 \\
    install -y --no-install-recommends \\
    ca-certificates curl unzip tar gzip openssl jq python3 python3-venv iproute2 procps \\
    tzdata kmod util-linux || fail "代理依赖安装失败。若提示锁被占用，已等待最多 10 秒，请稍后重新运行。"''',
    "host apt block",
)
path.write_text(text, encoding="utf-8")


# Landing installer follows the same 10-second lock policy.
path = ROOT / "core-src/landing.sh"
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    '''  retry 5 10 apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 -o DPkg::Lock::Timeout=120 -o Acquire::PDiffs=false update
  dpkg --configure -a >/dev/null 2>&1 || true
  retry 3 10 apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 -o DPkg::Lock::Timeout=120 install -y --no-install-recommends \\
    ca-certificates curl unzip tar gzip openssl jq iproute2 procps \\
    tzdata kmod util-linux python3''',
    '''  echo "APT/dpkg 锁最多等待 10 秒；超时立即报错，不删除锁，也不终止系统自动更新。"
  apt-get \\
    -o DPkg::Lock::Timeout=10 \\
    -o Acquire::Retries=2 \\
    -o Acquire::PDiffs=false \\
    -o Acquire::IndexTargets::deb::Sources::DefaultEnabled=false \\
    update || fail "APT 更新失败。若提示锁被占用，已等待最多 10 秒，请稍后重新运行。"
  apt-get \\
    -o DPkg::Lock::Timeout=10 \\
    -o Acquire::Retries=2 \\
    install -y --no-install-recommends \\
    ca-certificates curl unzip tar gzip openssl jq iproute2 procps \\
    tzdata kmod util-linux python3 || fail "落地端依赖安装失败。若提示锁被占用，已等待最多 10 秒，请稍后重新运行。"''',
    "landing apt block",
)
path.write_text(text, encoding="utf-8")


# Subscription center: distinguish an APT lock timeout from other package
# errors. Lock errors stop immediately after the single 10-second attempt.
path = ROOT / "core-src/center_install.sh"
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    '''  service_diagnostics "$service"
  fail "${service} 未进入 active 状态。"
}
valid_port(){''',
    '''  service_diagnostics "$service"
  fail "${service} 未进入 active 状态。"
}
apt_run(){
  local label="$1" log
  shift
  log="$(mktemp /tmp/vvv-apt.XXXXXX)"
  if "$@" 2>&1 | tee "$log"; then
    rm -f "$log"
    return 0
  fi
  if grep -Eqi 'Could not get lock|Unable to acquire.*lock|Waiting for cache lock' "$log"; then
    rm -f "$log"
    fail "APT/dpkg 锁等待超过 10 秒。请等待系统自动更新结束后重新运行；脚本不会删除锁文件，也不会强行终止系统更新。"
  fi
  echo "${label}失败。" >&2
  rm -f "$log"
  return 1
}
valid_port(){''',
    "center apt helper",
)
text = replace_once(
    text,
    '''if ((${#missing_packages[@]})); then
  echo "正在安装缺少的依赖：${missing_packages[*]}"
  if ! timeout 600 env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 install -y "${missing_packages[@]}"; then
    echo "首次安装依赖失败，正在刷新软件索引后重试……"
    timeout 600 apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 update
    timeout 600 env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 install -y "${missing_packages[@]}" || fail "订阅中心依赖安装失败。"
  fi
else''',
    '''if ((${#missing_packages[@]})); then
  echo "正在安装缺少的依赖：${missing_packages[*]}"
  echo "APT/dpkg 锁最多等待 10 秒；超时立即报错。"
  if ! apt_run "订阅中心依赖安装" \\
    env DEBIAN_FRONTEND=noninteractive apt-get \\
      -o DPkg::Lock::Timeout=10 \\
      -o Acquire::Retries=2 \\
      install -y "${missing_packages[@]}"; then
    echo "依赖安装失败，刷新软件索引后只再尝试一次……"
    apt_run "APT 索引刷新" \\
      apt-get \\
        -o DPkg::Lock::Timeout=10 \\
        -o Acquire::Retries=2 \\
        -o Acquire::PDiffs=false \\
        -o Acquire::IndexTargets::deb::Sources::DefaultEnabled=false \\
        update || fail "APT 索引刷新失败。"
    apt_run "订阅中心依赖安装" \\
      env DEBIAN_FRONTEND=noninteractive apt-get \\
        -o DPkg::Lock::Timeout=10 \\
        -o Acquire::Retries=2 \\
        install -y "${missing_packages[@]}" || fail "订阅中心依赖安装失败。"
  fi
else''',
    "center apt block",
)
path.write_text(text, encoding="utf-8")


# Optional cloud-backup setup uses the same policy.
path = ROOT / "core-src/rclone_manager.sh"
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    '''  apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 update >/dev/null
  DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 install -y curl unzip >/dev/null''',
    '''  echo "APT/dpkg 锁最多等待 10 秒；超时立即报错。"
  apt-get \\
    -o DPkg::Lock::Timeout=10 \\
    -o Acquire::Retries=2 \\
    -o Acquire::PDiffs=false \\
    -o Acquire::IndexTargets::deb::Sources::DefaultEnabled=false \\
    update >/dev/null || { echo "错误：APT 更新失败；若锁被占用，已等待最多 10 秒。" >&2; return 1; }
  DEBIAN_FRONTEND=noninteractive apt-get \\
    -o DPkg::Lock::Timeout=10 \\
    -o Acquire::Retries=2 \\
    install -y curl unzip >/dev/null || { echo "错误：rclone 依赖安装失败；若锁被占用，已等待最多 10 秒。" >&2; return 1; }''',
    "rclone apt block",
)
path.write_text(text, encoding="utf-8")


# Permanent regression coverage.
path = ROOT / "tests/conformance.py"
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    '''def test_hy2_leaf_certificate():
    host = read('core-src/host.sh')''',
    '''def test_apt_lock_policy():
    sources = {
        'network installer': read('vvv-install.sh'),
        'host installer': read('core-src/host.sh'),
        'landing installer': read('core-src/landing.sh'),
        'subscription center': read('core-src/center_install.sh'),
        'rclone manager': read('core-src/rclone_manager.sh'),
    }
    for label, source in sources.items():
        require('DPkg::Lock::Timeout=600' not in source, f'{label} 仍会等待 APT 锁 600 秒')
        require('DPkg::Lock::Timeout=120' not in source, f'{label} 仍会等待 APT 锁 120 秒')
        require('DPkg::Lock::Timeout=10' in source, f'{label} 没有使用 10 秒 APT 锁上限')
    require('python3 python3-venv iproute2' in sources['host installer'], '主安装阶段没有一次性安装 python3-venv')
    for label in ('network installer', 'host installer', 'landing installer', 'subscription center', 'rclone manager'):
        require('Acquire::IndexTargets::deb::Sources::DefaultEnabled=false' in sources[label], f'{label} 没有关闭无用的 deb-src 索引下载')
    require('APT/dpkg 锁等待超过 10 秒' in sources['subscription center'], '订阅中心没有明确的 10 秒锁超时错误')


def test_hy2_leaf_certificate():
    host = read('core-src/host.sh')''',
    "apt policy test function",
)
text = replace_once(
    text,
    '''        test_https_and_fresh_install_only,
        test_hy2_leaf_certificate,''',
    '''        test_https_and_fresh_install_only,
        test_apt_lock_policy,
        test_hy2_leaf_certificate,''',
    "apt policy test registration",
)
path.write_text(text, encoding="utf-8")


path = ROOT / "README.md"
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    '''- 使用 root 用户执行；
- 不包含 Debian 12、Alpine、OpenRC 或旧版本迁移兼容逻辑。''',
    '''- 使用 root 用户执行；
- APT/dpkg 锁最多等待 10 秒，超过后立即显示错误，不删除锁文件、不强行终止系统更新；
- 主安装阶段一次性安装订阅中心所需的 `python3-venv`，避免代理完成后再次调用 APT；
- 安装时关闭无用的 `deb-src` 索引下载，减少软件源警告和等待；
- 不包含 Debian 12、Alpine、OpenRC 或旧版本迁移兼容逻辑。''',
    "README apt policy",
)
path.write_text(text, encoding="utf-8")


# Final source audit: active installers may not contain the old long lock waits.
active_paths = [
    ROOT / "vvv-install.sh",
    ROOT / "core-src/host.sh",
    ROOT / "core-src/landing.sh",
    ROOT / "core-src/center_install.sh",
    ROOT / "core-src/rclone_manager.sh",
]
for active in active_paths:
    source = active.read_text(encoding="utf-8")
    for forbidden in ("DPkg::Lock::Timeout=600", "DPkg::Lock::Timeout=120"):
        if forbidden in source:
            raise SystemExit(f"forbidden lock timeout remains in {active}: {forbidden}")
    if "DPkg::Lock::Timeout=10" not in source:
        raise SystemExit(f"10-second lock timeout missing in {active}")

print("APT LOCK 10S PATCH APPLIED")

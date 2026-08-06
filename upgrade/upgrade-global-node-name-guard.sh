#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

[[ "$(id -u)" -eq 0 ]] || { echo "错误：请使用 root 用户执行。" >&2; exit 1; }
for command_name in curl python3 sha256sum systemctl grep; do
  command -v "$command_name" >/dev/null 2>&1 || { echo "错误：缺少命令 ${command_name}。" >&2; exit 1; }
done

MANAGER=/usr/local/sbin/jp-relay-manager
RENDERER=/usr/local/lib/vvv/client_package_renderer.py
NAME_GUARD=/usr/local/lib/vvv/name_guard_runtime.py
SUB_CENTER=/usr/local/lib/vvv/sub_center.py
STATE=/etc/jp-relay/state.json
TICKETS=/var/lib/vvv-sub/relay-tickets.json
OVERRIDES=/var/lib/vvv-sub/node-overrides.json
MANAGER_MARKER='# VVV_GLOBAL_NAME_GUARD_V1'
CENTER_MARKER='# VVV_GLOBAL_NAME_GUARD_V1'
SOURCE_REF="${VVV_SOURCE_REF:-main}"
RAW_BASE="https://raw.githubusercontent.com/weizhiok/vvv/${SOURCE_REF}"
EXPECTED_RENDERER_BLOB='407a56f00c1d5c0f2e036d3070e5de3aa52afb2c'
EXPECTED_GUARD_BLOB='b60520ef81eaa3635c6269d2f91a5a9b19ee13cb'
WORK="$(mktemp -d /tmp/vvv-name-guard-upgrade.XXXXXX)"
BACKUP="/root/vvv-name-guard-backup-$(date +%Y%m%d-%H%M%S)"
cleanup() { rm -rf -- "$WORK"; }
trap cleanup EXIT

[[ -x "$MANAGER" ]] || { echo "错误：未找到现有 VVV 中转管理器：$MANAGER" >&2; exit 1; }
[[ -f "$RENDERER" ]] || { echo "错误：未找到现有客户端渲染器：$RENDERER" >&2; exit 1; }
[[ -f "$STATE" ]] || { echo "错误：未找到现有中转状态文件：$STATE" >&2; exit 1; }

blob_sha() {
  python3 - "$1" <<'PY_BLOB_SHA'
import hashlib
import sys
from pathlib import Path
content = Path(sys.argv[1]).read_bytes()
print(hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest())
PY_BLOB_SHA
}

download_checked() {
  local relative="$1" target="$2" expected="$3" actual
  curl -fL --retry 5 --retry-all-errors --connect-timeout 10 --max-time 120 \
    "$RAW_BASE/$relative" -o "$target"
  actual="$(blob_sha "$target")"
  [[ "$actual" == "$expected" ]] || {
    echo "错误：${relative} 校验失败，未修改现有 VPS。" >&2
    echo "期望 Git blob：$expected" >&2
    echo "实际 Git blob：$actual" >&2
    exit 1
  }
}

download_checked core-src/client_package_renderer.py "$WORK/client_package_renderer.py" "$EXPECTED_RENDERER_BLOB"
download_checked core-src/name_guard_runtime.py "$WORK/name_guard_runtime.py" "$EXPECTED_GUARD_BLOB"
python3 -m py_compile "$WORK/client_package_renderer.py" "$WORK/name_guard_runtime.py"
python3 "$WORK/client_package_renderer.py" --help | grep -q -- '--upgrade-name-guard-only'
python3 "$WORK/name_guard_runtime.py" --help | grep -q -- '--sub-center'

center_installed=0
[[ -f "$SUB_CENTER" ]] && center_installed=1
if cmp -s "$WORK/client_package_renderer.py" "$RENDERER" \
  && [[ -f "$NAME_GUARD" ]] && cmp -s "$WORK/name_guard_runtime.py" "$NAME_GUARD" \
  && grep -Fqx "$MANAGER_MARKER" "$MANAGER" \
  && { (( center_installed == 0 )) || grep -Fqx "$CENTER_MARKER" "$SUB_CENTER"; }; then
  echo "当前 VPS 已具备全局节点名称保护，无需重复升级。"
  exit 0
fi

mkdir -p "$BACKUP"
cp -a "$MANAGER" "$BACKUP/jp-relay-manager"
cp -a "$RENDERER" "$BACKUP/client_package_renderer.py"
[[ ! -f "$NAME_GUARD" ]] || cp -a "$NAME_GUARD" "$BACKUP/name_guard_runtime.py"
[[ ! -f "$SUB_CENTER" ]] || cp -a "$SUB_CENTER" "$BACKUP/sub_center.py"
[[ ! -f "$TICKETS" ]] || cp -a "$TICKETS" "$BACKUP/relay-tickets.json"
[[ ! -f "$OVERRIDES" ]] || cp -a "$OVERRIDES" "$BACKUP/node-overrides.json"
STATE_BEFORE="$(sha256sum "$STATE" | awk '{print $1}')"
XRAY_PID_BEFORE="$(systemctl show -p MainPID --value xray 2>/dev/null || true)"
SING_PID_BEFORE="$(systemctl show -p MainPID --value sing-box 2>/dev/null || true)"
CENTER_WAS_ACTIVE=0
systemctl is-active --quiet vvv-sub.service 2>/dev/null && CENTER_WAS_ACTIVE=1

rollback() {
  cp -a "$BACKUP/jp-relay-manager" "$MANAGER" 2>/dev/null || true
  cp -a "$BACKUP/client_package_renderer.py" "$RENDERER" 2>/dev/null || true
  if [[ -f "$BACKUP/name_guard_runtime.py" ]]; then
    cp -a "$BACKUP/name_guard_runtime.py" "$NAME_GUARD" 2>/dev/null || true
  else
    rm -f "$NAME_GUARD"
  fi
  [[ ! -f "$BACKUP/sub_center.py" ]] || cp -a "$BACKUP/sub_center.py" "$SUB_CENTER" 2>/dev/null || true
  [[ ! -f "$BACKUP/relay-tickets.json" ]] || cp -a "$BACKUP/relay-tickets.json" "$TICKETS" 2>/dev/null || true
  [[ ! -f "$BACKUP/node-overrides.json" ]] || cp -a "$BACKUP/node-overrides.json" "$OVERRIDES" 2>/dev/null || true
  (( CENTER_WAS_ACTIVE == 0 )) || systemctl restart vvv-sub.service >/dev/null 2>&1 || true
  echo "升级失败，已恢复升级前文件。备份目录：$BACKUP" >&2
}
trap rollback ERR

install -o root -g root -m 755 "$WORK/client_package_renderer.py" "$RENDERER"
install -o root -g root -m 755 "$WORK/name_guard_runtime.py" "$NAME_GUARD"
python3 "$RENDERER" --upgrade-manager-only --manager-path "$MANAGER"
python3 "$RENDERER" --upgrade-name-guard-only \
  --manager-path "$MANAGER" --name-guard-path "$NAME_GUARD"

bash -n "$MANAGER"
grep -Fqx "$MANAGER_MARKER" "$MANAGER"
grep -Fq '(( count == 0 )) || fail "唯一名称保护失败：拒绝覆盖同名 VPS 中转线路。"' "$MANAGER"
grep -Fq '(( count == 0 )) || fail "唯一名称保护失败：拒绝覆盖同名动态代理线路。"' "$MANAGER"
python3 -m py_compile "$RENDERER" "$NAME_GUARD"
if (( center_installed == 1 )); then
  grep -Fqx "$CENTER_MARKER" "$SUB_CENTER"
  python3 -m py_compile "$SUB_CENTER"
  systemctl is-active --quiet vvv-sub.service
fi

[[ "$(sha256sum "$STATE" | awk '{print $1}')" == "$STATE_BEFORE" ]]
[[ "$(systemctl show -p MainPID --value xray 2>/dev/null || true)" == "$XRAY_PID_BEFORE" ]]
[[ "$(systemctl show -p MainPID --value sing-box 2>/dev/null || true)" == "$SING_PID_BEFORE" ]]
trap - ERR

echo "升级成功。"
echo "以后新建 VPS 中转、HTTP/HTTPS/SOCKS5 中转和两类临时节点时，都会先向订阅中心申请全局唯一名称。"
echo "同名时自动使用【2】、【3】……，新建操作不会再覆盖已有线路。"
echo "未尝试恢复已经丢失的旧节点，也没有修改现有中转状态、UUID、密码、Xray、sing-box 或 SSH。"
if (( center_installed == 1 )); then
  echo "订阅中心服务已短暂重启以加载名称保护；代理服务未重启。"
fi
echo "备份目录：$BACKUP"

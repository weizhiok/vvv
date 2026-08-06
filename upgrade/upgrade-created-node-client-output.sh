#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

[[ "$(id -u)" -eq 0 ]] || { echo "错误：请使用 root 用户执行。" >&2; exit 1; }
for command_name in curl python3 sha256sum systemctl; do
  command -v "$command_name" >/dev/null 2>&1 || { echo "错误：缺少命令 ${command_name}。" >&2; exit 1; }
done

MANAGER=/usr/local/sbin/jp-relay-manager
RENDERER=/usr/local/lib/vvv/client_package_renderer.py
STATE=/etc/jp-relay/state.json
MARKER='# VVV_CREATED_NODE_OUTPUT_V1'
SOURCE_REF="${VVV_SOURCE_REF:-main}"
RAW_BASE="https://raw.githubusercontent.com/weizhiok/vvv/${SOURCE_REF}"
EXPECTED_RENDERER_BLOB='bcfc867137ce856ad3d8ecb83947c0447106ce19'
WORK="$(mktemp -d /tmp/vvv-created-output-upgrade.XXXXXX)"
BACKUP="/root/vvv-created-output-backup-$(date +%Y%m%d-%H%M%S)"
cleanup() { rm -rf -- "$WORK"; }
trap cleanup EXIT

[[ -x "$MANAGER" ]] || { echo "错误：未找到现有 VVV 中转管理器：$MANAGER" >&2; exit 1; }
[[ -f "$RENDERER" ]] || { echo "错误：未找到现有客户端渲染器：$RENDERER" >&2; exit 1; }
[[ -f "$STATE" ]] || { echo "错误：未找到现有中转状态文件：$STATE" >&2; exit 1; }

curl -fL --retry 5 --retry-all-errors --connect-timeout 10 --max-time 120 \
  "$RAW_BASE/core-src/client_package_renderer.py" \
  -o "$WORK/client_package_renderer.py"

actual_blob="$(python3 - "$WORK/client_package_renderer.py" <<'PY_BLOB_SHA'
import hashlib
import sys
from pathlib import Path
content = Path(sys.argv[1]).read_bytes()
header = f'blob {len(content)}\0'.encode()
print(hashlib.sha1(header + content).hexdigest())
PY_BLOB_SHA
)"
[[ "$actual_blob" == "$EXPECTED_RENDERER_BLOB" ]] || {
  echo "错误：升级文件校验失败，未修改现有 VPS。" >&2
  echo "期望 Git blob：$EXPECTED_RENDERER_BLOB" >&2
  echo "实际 Git blob：$actual_blob" >&2
  exit 1
}
python3 -m py_compile "$WORK/client_package_renderer.py"
python3 "$WORK/client_package_renderer.py" --help | grep -q -- '--upgrade-manager-only'
python3 "$WORK/client_package_renderer.py" --help | grep -q temporary

if cmp -s "$WORK/client_package_renderer.py" "$RENDERER" && grep -Fqx "$MARKER" "$MANAGER"; then
  echo "当前 VPS 已具备创建后打印全部客户端配置的功能，无需重复升级。"
  exit 0
fi

mkdir -p "$BACKUP"
cp -a "$MANAGER" "$BACKUP/jp-relay-manager"
cp -a "$RENDERER" "$BACKUP/client_package_renderer.py"
STATE_BEFORE="$(sha256sum "$STATE" | awk '{print $1}')"
XRAY_PID_BEFORE="$(systemctl show -p MainPID --value xray 2>/dev/null || true)"
SING_PID_BEFORE="$(systemctl show -p MainPID --value sing-box 2>/dev/null || true)"

rollback() {
  cp -a "$BACKUP/jp-relay-manager" "$MANAGER" 2>/dev/null || true
  cp -a "$BACKUP/client_package_renderer.py" "$RENDERER" 2>/dev/null || true
  echo "升级失败，已恢复升级前文件。备份目录：$BACKUP" >&2
}
trap rollback ERR

install -o root -g root -m 755 "$WORK/client_package_renderer.py" "$RENDERER"
python3 "$RENDERER" --upgrade-manager-only --manager-path "$MANAGER"
bash -n "$MANAGER"
grep -Fqx "$MARKER" "$MANAGER"
python3 -m py_compile "$RENDERER"

[[ "$(sha256sum "$STATE" | awk '{print $1}')" == "$STATE_BEFORE" ]]
[[ "$(systemctl show -p MainPID --value xray 2>/dev/null || true)" == "$XRAY_PID_BEFORE" ]]
[[ "$(systemctl show -p MainPID --value sing-box 2>/dev/null || true)" == "$SING_PID_BEFORE" ]]
trap - ERR

echo "升级成功。"
echo "创建正式 VPS、正式动态代理、临时 VPS、临时动态代理后，SSH 都会打印全部受支持客户端配置。"
echo "状态文件、节点凭据、Xray、sing-box、SSH 和服务器均未重启或修改。"
echo "备份目录：$BACKUP"

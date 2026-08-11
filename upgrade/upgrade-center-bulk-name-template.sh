#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

[[ "$(id -u)" -eq 0 ]] || { echo "错误：请使用 root 用户执行。" >&2; exit 1; }
for command_name in curl python3 bash grep install; do
  command -v "$command_name" >/dev/null 2>&1 || { echo "错误：缺少命令 ${command_name}。" >&2; exit 1; }
done

TARGET=/usr/local/sbin/vvv-center
SOURCE_COPY=/usr/local/lib/vvv-source/center_manager.sh
SOURCE_REF="${VVV_SOURCE_REF:-main}"
RAW_URL="https://raw.githubusercontent.com/weizhiok/vvv/${SOURCE_REF}/core-src/center_manager.sh"
EXPECTED_BLOB='c2be6a884b0cba84c10133e54561dd633885c7ed'
WORK="$(mktemp -d /tmp/vvv-center-bulk-template.XXXXXX)"
BACKUP="/root/vvv-center-bulk-template-backup-$(date +%Y%m%d-%H%M%S)"
cleanup(){ rm -rf -- "$WORK"; }
trap cleanup EXIT

[[ -f "$TARGET" ]] || { echo "错误：未找到订阅中心管理器：$TARGET" >&2; exit 1; }

blob_sha(){
  python3 - "$1" <<'PY_BLOB_SHA'
import hashlib
import sys
from pathlib import Path
content = Path(sys.argv[1]).read_bytes()
print(hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest())
PY_BLOB_SHA
}

NEW="$WORK/center_manager.sh"
curl -fL --retry 5 --retry-all-errors --connect-timeout 10 --max-time 120 "$RAW_URL" -o "$NEW"
ACTUAL_BLOB="$(blob_sha "$NEW")"
[[ "$ACTUAL_BLOB" == "$EXPECTED_BLOB" ]] || {
  echo "错误：下载文件校验失败，未修改现有 VPS。" >&2
  echo "期望 Git blob：$EXPECTED_BLOB" >&2
  echo "实际 Git blob：$ACTUAL_BLOB" >&2
  exit 1
}

bash -n "$NEW"
grep -Fq 'echo "${order_index}. 批量重新排序"' "$NEW"
grep -Fq 'echo "原始名称：${original_names}"' "$NEW"
grep -Fq 'read -r -p "批量新名称：" input' "$NEW"
grep -Fq 'echo "原始顺序：${original_names}"' "$NEW"
grep -Fq 'read -r -p "批量新顺序：" input' "$NEW"
grep -Fq 'original_names+="${name}|"' "$NEW"

if cmp -s "$NEW" "$TARGET" && { [[ ! -f "$SOURCE_COPY" ]] || cmp -s "$NEW" "$SOURCE_COPY"; }; then
  echo "当前 VPS 已具备批量重命名/排序原始模板，无需重复升级。"
  exit 0
fi

mkdir -p "$BACKUP"
cp -a "$TARGET" "$BACKUP/vvv-center"
[[ ! -f "$SOURCE_COPY" ]] || cp -a "$SOURCE_COPY" "$BACKUP/center_manager.sh"

rollback(){
  cp -a "$BACKUP/vvv-center" "$TARGET" 2>/dev/null || true
  if [[ -f "$BACKUP/center_manager.sh" ]]; then
    cp -a "$BACKUP/center_manager.sh" "$SOURCE_COPY" 2>/dev/null || true
  fi
  echo "升级失败，已恢复升级前文件。备份目录：$BACKUP" >&2
}
trap rollback ERR

install -o root -g root -m 700 "$NEW" "$TARGET"
if [[ -d "$(dirname "$SOURCE_COPY")" ]]; then
  install -o root -g root -m 700 "$NEW" "$SOURCE_COPY"
fi

bash -n "$TARGET"
grep -Fq 'echo "${order_index}. 批量重新排序"' "$TARGET"
grep -Fq 'echo "原始名称：${original_names}"' "$TARGET"
grep -Fq 'read -r -p "批量新名称：" input' "$TARGET"
grep -Fq 'echo "原始顺序：${original_names}"' "$TARGET"
grep -Fq 'read -r -p "批量新顺序：" input' "$TARGET"

trap - ERR

echo "升级成功。"
echo "批量重命名现在会先显示：原始名称：|名称1|名称2|...|"
echo "批量重新排序现在会先显示：原始顺序：|名称1|名称2|...|"
echo "输入提示已改为“批量新名称”和“批量新顺序”。"
echo "未修改节点、订阅数据、代理配置，也未重启 Xray、sing-box、vvv-sub、Caddy 或 VPS。"
echo "备份目录：$BACKUP"

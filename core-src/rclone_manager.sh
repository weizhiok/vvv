#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
CFG_DIR=/etc/vvv-sub
CLOUD_CFG=$CFG_DIR/cloud.json
RCLONE_CFG=$CFG_DIR/rclone.conf
BACKUP=/usr/local/lib/vvv/backup_manager.py
REMOTE=vvvcloud
ROOT=vvv

backup_event(){
  local reason="$1"
  [[ -x "$BACKUP" && -f /etc/vvv-sub/config.json ]] || return 0
  python3 "$BACKUP" create "$reason" --force >/dev/null || echo "警告：自动备份失败。" >&2
}
install_rclone(){
  command -v rclone >/dev/null 2>&1 && return 0
  apt-get -o DPkg::Lock::Timeout=10 -o Acquire::Retries=2 -o Acquire::PDiffs=false -o Acquire::IndexTargets::deb-src::Sources::DefaultEnabled=false update >/dev/null || return 1
  DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=10 -o Acquire::Retries=2 install -y curl unzip >/dev/null || return 1
  curl -fsSL --retry 5 --retry-all-errors https://rclone.org/install.sh | bash
  command -v rclone >/dev/null 2>&1
}
configure_remote(){
  local expected="$1"
  install -d -m700 "$CFG_DIR"
  rm -f "$RCLONE_CFG"
  echo
  echo "接下来只需要完成云盘官方 OAuth 授权。"
  echo "请在 rclone 界面创建名称为 ${REMOTE} 的 remote，并选择：${expected}。"
  echo "授权完成并退出配置界面后，VVV 会自动创建固定目录 ${ROOT}/。"
  echo
  RCLONE_CONFIG="$RCLONE_CFG" rclone config
  RCLONE_CONFIG="$RCLONE_CFG" rclone listremotes | grep -Fxq "${REMOTE}:" || { echo "错误：没有检测到名为 ${REMOTE} 的 remote。" >&2; return 1; }
  local actual
  actual="$(RCLONE_CONFIG="$RCLONE_CFG" rclone config show "$REMOTE" | awk -F'=' '/^[[:space:]]*type[[:space:]]*=/{gsub(/[[:space:]]/,"",$2);print $2;exit}')"
  [[ "$actual" == "$expected" ]] || { echo "错误：remote 类型为 ${actual:-未知}，应为 ${expected}。" >&2; return 1; }
  chmod 600 "$RCLONE_CFG"
}
enable_cloud(){
  local choice provider
  echo "1. Google Drive"; echo "2. Microsoft OneDrive"; echo "0. 返回"
  read -r -p "请选择 [默认 1]：" choice; [[ -n "$choice" ]] || choice=1
  case "$choice" in 1) provider=drive;; 2) provider=onedrive;; 0) return;; *) echo "请输入 0、1 或 2。"; return 1;; esac
  backup_event before-cloud-backup-enabled
  install_rclone || { echo "错误：rclone 安装失败。" >&2; return 1; }
  configure_remote "$provider"
  RCLONE_CONFIG="$RCLONE_CFG" rclone mkdir "${REMOTE}:${ROOT}/backups"
  python3 - "$CLOUD_CFG" "$provider" <<'PY'
import json,os,sys,tempfile,datetime
path,provider=sys.argv[1:]
obj={'schema':2,'enabled':True,'provider':provider,'remote':'vvvcloud','folder':'vvv','created_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
fd,tmp=tempfile.mkstemp(prefix='.cloud.',dir=os.path.dirname(path))
with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump(obj,f,ensure_ascii=False,indent=2); f.write('\n')
os.chmod(tmp,0o600); os.replace(tmp,path)
PY
  backup_event after-cloud-backup-enabled
  python3 "$BACKUP" create cloud-backup-enabled --force >/dev/null
  python3 "$BACKUP" cloud-test
  echo "云备份已开启。固定目录：${ROOT}/；后续配置变化会自动备份。"
}
disable_cloud(){
  [[ -f "$CLOUD_CFG" ]] || { echo "云备份本来就是关闭状态。"; return; }
  backup_event before-cloud-backup-disabled
  rm -f "$CLOUD_CFG" "$RCLONE_CFG"
  echo "云备份已关闭；云盘 ${ROOT}/ 中的历史备份不会删除。"
}
status_cloud(){
  [[ -f "$CLOUD_CFG" ]] || { echo "云备份：关闭"; return; }
  jq . "$CLOUD_CFG"
  [[ -s "$RCLONE_CFG" ]] || { echo "错误：本机云盘授权不存在，请重新授权。"; return 1; }
  RCLONE_CONFIG="$RCLONE_CFG" rclone lsf "${REMOTE}:${ROOT}" || true
  python3 "$BACKUP" cloud-test
}
reconfigure(){
  local provider
  provider="$(jq -r '.provider // "drive"' "$CLOUD_CFG" 2>/dev/null || echo drive)"
  install_rclone
  configure_remote "$provider"
  python3 "$BACKUP" refresh-control
  python3 "$BACKUP" cloud-test
  echo "云盘重新授权完成。"
}
case "${1:-menu}" in
  enable) enable_cloud;; disable) disable_cloud;; status) status_cloud;; reconfigure) reconfigure;;
  *) echo "用法：$0 enable|disable|status|reconfigure" >&2; exit 2;;
esac

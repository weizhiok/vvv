#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
CFG_DIR=/etc/vvv-sub
CLOUD_CFG=$CFG_DIR/cloud.json
RCLONE_CFG=$CFG_DIR/rclone.conf
BACKUP=/usr/local/lib/vvv/backup_manager.py

install_rclone(){
  if command -v rclone >/dev/null 2>&1; then return 0; fi
  apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 update >/dev/null
  DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 install -y curl unzip >/dev/null
  curl -fsSL --retry 5 --retry-all-errors https://rclone.org/install.sh | bash
  command -v rclone >/dev/null 2>&1 || { echo "错误：rclone 安装失败。" >&2; return 1; }
}

enable_cloud(){
  local choice provider
  echo "1. Google Drive"
  echo "2. Microsoft OneDrive"
  echo "0. 返回"
  read -r -p "请选择：" choice
  case "$choice" in
    1) provider=drive ;;
    2) provider=onedrive ;;
    0) return 0 ;;
    *) echo "请输入 0、1 或 2。"; return 1 ;;
  esac
  install_rclone
  install -d -m700 "$CFG_DIR"
  echo
  echo "接下来进入 rclone 官方配置界面。"
  echo "请新建一个名称为 vvvcloud 的 remote，并选择：$provider"
  echo "无浏览器 VPS 会提示在电脑端运行 rclone authorize，然后粘贴授权结果。"
  echo
  RCLONE_CONFIG="$RCLONE_CFG" rclone config
  RCLONE_CONFIG="$RCLONE_CFG" rclone listremotes | grep -Fxq 'vvvcloud:' || {
    echo "错误：没有检测到名为 vvvcloud 的 remote。" >&2
    return 1
  }
  local folder="VVV-Backup/$(hostname)"
  RCLONE_CONFIG="$RCLONE_CFG" rclone mkdir "vvvcloud:${folder}"
  python3 - "$CLOUD_CFG" "$provider" "$folder" <<'PY'
import json,os,sys,tempfile
path,provider,folder=sys.argv[1:]
obj={'schema':1,'enabled':True,'provider':provider,'remote':'vvvcloud','folder':folder}
fd,tmp=tempfile.mkstemp(prefix='.cloud.',dir=os.path.dirname(path))
with os.fdopen(fd,'w',encoding='utf-8') as f:
    json.dump(obj,f,ensure_ascii=False,indent=2); f.write('\n')
os.chmod(tmp,0o600); os.replace(tmp,path)
PY
  chmod 600 "$RCLONE_CFG" "$CLOUD_CFG"
  python3 "$BACKUP" create cloud-backup-enabled --force >/dev/null
  python3 "$BACKUP" cloud-test
  echo "云备份已开启。以后只在数据发生变化并生成本地备份后自动上传。"
}

disable_cloud(){
  [[ -f "$CLOUD_CFG" ]] || { echo "云备份本来就是关闭状态。"; return 0; }
  python3 "$BACKUP" create before-cloud-backup-disabled --force >/dev/null || true
  rm -f "$CLOUD_CFG" "$RCLONE_CFG"
  echo "云备份已关闭。本地加密备份不受影响。"
}

status_cloud(){
  if [[ ! -f "$CLOUD_CFG" ]]; then echo "云备份：关闭"; return 0; fi
  jq . "$CLOUD_CFG"
  rclone --config "$RCLONE_CFG" listremotes || true
  python3 "$BACKUP" cloud-test
}

case "${1:-menu}" in
  enable) enable_cloud ;;
  disable) disable_cloud ;;
  status) status_cloud ;;
  reconfigure) disable_cloud; enable_cloud ;;
  *) echo "用法：$0 enable|disable|status|reconfigure" >&2; exit 2 ;;
esac

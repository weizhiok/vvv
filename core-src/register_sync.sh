#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
role="${1:?role}"
code="${2:-}"
install -d -m700 /etc/vvv /usr/local/lib/vvv
install -m755 "$BASE_DIR/sync_agent.py" /usr/local/lib/vvv/sync_agent.py

if [[ -n "$code" ]]; then
  python3 /usr/local/lib/vvv/sync_agent.py register "$code" "$role" >/dev/null
  printf '\033[32m订阅中心注册成功\033[0m\n'
fi

cat > /etc/systemd/system/vvv-sync.service <<'UNIT'
[Unit]
Description=VVV Node Snapshot Sync
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/lib/vvv/sync_agent.py sync
UNIT

cat > /etc/systemd/system/vvv-sync.timer <<'UNIT'
[Unit]
Description=VVV node heartbeat
[Timer]
OnBootSec=3min
OnUnitActiveSec=30min
RandomizedDelaySec=60
Persistent=true
[Install]
WantedBy=timers.target
UNIT

{
  echo '[Unit]'
  echo 'Description=Watch VVV node state changes'
  echo '[Path]'
  case "$role" in
    landing-direct)
      echo 'PathChanged=/etc/jp-relay/state.json'
      echo 'PathChanged=/etc/jp-relay/landing-state.json'
      ;;
    landing) echo 'PathChanged=/etc/jp-relay/landing-state.json' ;;
    *) echo 'PathChanged=/etc/jp-relay/state.json' ;;
  esac
  echo 'Unit=vvv-sync.service'
  echo '[Install]'
  echo 'WantedBy=multi-user.target'
} > /etc/systemd/system/vvv-sync.path

systemctl daemon-reload
if [[ -f /etc/vvv/client.json ]]; then
  systemctl enable --now vvv-sync.timer vvv-sync.path
  systemctl start vvv-sync.service || true
else
  systemctl disable --now vvv-sync.timer vvv-sync.path >/dev/null 2>&1 || true
  echo "本次未注册订阅中心；以后输入 vps，可粘贴 VVC1 或含注册票据的 JPR3 完成注册。"
fi

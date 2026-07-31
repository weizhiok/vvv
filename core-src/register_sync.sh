#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
role="${1:?role}"; code="${2:-}"
install -d -m700 /etc/vvv /usr/local/lib/vvv
install -m755 "$BASE_DIR/sync_agent.py" /usr/local/lib/vvv/sync_agent.py

systemctl disable --now vvv-backup-pull.timer vvv-backup-pull.service >/dev/null 2>&1 || true
rm -f /etc/systemd/system/vvv-backup-pull.timer /etc/systemd/system/vvv-backup-pull.service
rm -rf /var/backups/vvv-remote

if [[ -n "$code" ]]; then
  python3 /usr/local/lib/vvv/sync_agent.py register "$code" "$role"
fi

state_path=/etc/jp-relay/state.json
[[ "$role" != landing ]] || state_path=/etc/jp-relay/landing-state.json
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
cat > /etc/systemd/system/vvv-sync.path <<UNIT
[Unit]
Description=Watch VVV node state changes
[Path]
PathChanged=$state_path
Unit=vvv-sync.service
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
if [[ -f /etc/vvv/client.json ]]; then
  systemctl enable --now vvv-sync.timer vvv-sync.path
  systemctl start vvv-sync.service || true
else
  systemctl disable --now vvv-sync.timer vvv-sync.path >/dev/null 2>&1 || true
  echo "未提供订阅中心接入码；以后可在 vps 菜单中注册。"
fi

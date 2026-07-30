#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
role="${1:?role}"; code="${2:-}"
install -d -m 700 /etc/vvv /usr/local/lib/vvv /var/backups/vvv-remote
install -m 755 "$BASE_DIR/sync_agent.py" /usr/local/lib/vvv/sync_agent.py

if [[ -n "$code" ]]; then
  python3 /usr/local/lib/vvv/sync_agent.py register "$code" "$role"
fi

cat > /etc/systemd/system/vvv-sync.service <<'EOF'
[Unit]
Description=VVV Node Snapshot Sync
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/lib/vvv/sync_agent.py sync
EOF
cat > /etc/systemd/system/vvv-sync.timer <<'EOF'
[Unit]
Description=VVV Node Sync Timer
[Timer]
OnBootSec=3min
OnUnitActiveSec=30min
RandomizedDelaySec=60
Persistent=true
[Install]
WantedBy=timers.target
EOF
cat > /etc/systemd/system/vvv-sync.path <<'EOF'
[Unit]
Description=Watch VVV node state changes
[Path]
PathChanged=/etc/jp-relay/state.json
Unit=vvv-sync.service
[Install]
WantedBy=multi-user.target
EOF

if [[ "$role" == relay ]]; then
  cat > /etc/systemd/system/vvv-backup-pull.service <<'EOF'
[Unit]
Description=Pull encrypted VVV center backup
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/lib/vvv/sync_agent.py pull-backup /var/backups/vvv-remote
EOF
  cat > /etc/systemd/system/vvv-backup-pull.timer <<'EOF'
[Unit]
Description=VVV center remote backup timer
[Timer]
OnBootSec=10min
OnUnitActiveSec=1h
RandomizedDelaySec=5min
Persistent=true
[Install]
WantedBy=timers.target
EOF
fi
systemctl daemon-reload
if [[ -f /etc/vvv/client.json ]]; then
  systemctl enable --now vvv-sync.timer vvv-sync.path
  systemctl start vvv-sync.service || true
  if [[ "$role" == relay ]]; then systemctl enable --now vvv-backup-pull.timer; systemctl start vvv-backup-pull.service || true; fi
else
  echo "未提供订阅中心接入码；以后可在 vps 菜单中注册。"
fi

#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path, old, new):
    target = ROOT / path
    text = target.read_text(encoding='utf-8')
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{path}: expected one match, found {count}: {old[:120]!r}')
    target.write_text(text.replace(old, new, 1), encoding='utf-8')


replace_once(
    'core-src/restore_manager.py',
    "    'var/lib/vvv-sub/registry.json', 'var/lib/vvv-sub/node-overrides.json',\n",
    "    'var/lib/vvv-sub/registry.json', 'var/lib/vvv-sub/node-overrides.json', 'var/lib/vvv-sub/node-order.json',\n",
)

replace_once(
    'core-src/host.sh',
    '''  mode_has_hy2 && echo "Hysteria 2：UDP/$(jq -r '.port_hopping.ports' "$STATE_FILE") → $(jq -r '.listen_port' "$STATE_FILE")，每 $(jq -r '.port_hopping.hop_interval_seconds' "$STATE_FILE") 秒切换，sing-box=$(systemctl is-active sing-box)"
  echo "时区：Asia/Shanghai"''',
    '''  mode_has_hy2 && echo "Hysteria 2：UDP/$(jq -r '.port_hopping.ports' "$STATE_FILE") → $(jq -r '.listen_port' "$STATE_FILE")，每 $(jq -r '.port_hopping.hop_interval_seconds' "$STATE_FILE") 秒切换，sing-box=$(systemctl is-active sing-box)"
  mode_has_hy2 && echo "重要：请在云厂商安全组及外部防火墙放行 UDP $(jq -r '.port_hopping.ports' "$STATE_FILE")。"
  echo "时区：Asia/Shanghai"''',
)

replace_once(
    'core-src/bootstrap.sh',
    '''      echo "Hysteria 2 端口跳跃：${VVV_HY2_PORTS}（每 ${VVV_HY2_HOP_INTERVAL} 秒切换）"
    fi''',
    '''      echo "Hysteria 2 端口跳跃：${VVV_HY2_PORTS}（每 ${VVV_HY2_HOP_INTERVAL} 秒切换）"
      echo "云安全组：安装完成后需放行 UDP ${VVV_HY2_PORTS}"
    fi''',
)

replace_once(
    'tests/test_quick_upstream_commands.sh',
    '''if grep -Fq -- 'jq -r \".ports\"' "$ROOT/core-src/bootstrap.sh"; then
  echo "bootstrap must not require jq before dependencies are installed" >&2
  exit 1
fi

echo "Quick upstream command and installer contract tests passed."''',
    '''if grep -Fq -- 'jq -r \".ports\"' "$ROOT/core-src/bootstrap.sh"; then
  echo "bootstrap must not require jq before dependencies are installed" >&2
  exit 1
fi
grep -Fq -- "var/lib/vvv-sub/node-order.json" "$ROOT/core-src/restore_manager.py"
grep -Fq -- "云厂商安全组及外部防火墙放行 UDP" "$HOST"

echo "Quick upstream, installer, restore, and firewall contract tests passed."''',
)

print('Operational review fixes applied.')

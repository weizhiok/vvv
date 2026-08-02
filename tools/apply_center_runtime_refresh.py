#!/usr/bin/env python3
from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding='utf-8')
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{path}: expected one match, found {count}')
    file.write_text(text.replace(old, new, 1), encoding='utf-8')


replace_once(
    'core-src/bootstrap.sh',
    '''ensure_center_runtime() {
  systemctl daemon-reload
  systemctl enable vvv-sub.service caddy.service >/dev/null 2>&1 || true
  systemctl is-active --quiet vvv-sub.service || timeout 75 systemctl restart vvv-sub.service
  systemctl is-active --quiet caddy.service || timeout 75 systemctl restart caddy.service
  systemctl is-active --quiet vvv-sub.service &&
  systemctl is-active --quiet caddy.service
}

ensure_center(){
  if center_complete; then
    echo "订阅中心已完整安装，保留现有订阅密钥、已注册主机和备份数据。"
    ensure_center_runtime || fail "现有订阅中心文件完整，但服务无法启动；为保护数据，脚本没有自动删除它。"
    return 0
  fi
''',
    '''refresh_center_runtime_code() {
  local changed=0
  install -d -m700 /usr/local/lib/vvv
  for file in sub_center.py backup_manager.py; do
    if [[ ! -f "/usr/local/lib/vvv/$file" ]] || ! cmp -s "$BASE_DIR/$file" "/usr/local/lib/vvv/$file"; then
      install -m755 "$BASE_DIR/$file" "/usr/local/lib/vvv/$file"
      changed=1
    fi
  done
  if (( changed == 1 )); then
    echo "检测到订阅中心程序更新，保留全部数据并重新启动内部服务。"
    timeout 75 systemctl restart vvv-sub.service
  fi
}

ensure_center_runtime() {
  systemctl daemon-reload
  systemctl enable vvv-sub.service caddy.service >/dev/null 2>&1 || true
  systemctl is-active --quiet vvv-sub.service || timeout 75 systemctl restart vvv-sub.service
  systemctl is-active --quiet caddy.service || timeout 75 systemctl restart caddy.service
  systemctl is-active --quiet vvv-sub.service &&
  systemctl is-active --quiet caddy.service
}

ensure_center(){
  if center_complete; then
    echo "订阅中心已完整安装，保留现有订阅密钥、已注册主机和备份数据。"
    refresh_center_runtime_code
    ensure_center_runtime || fail "现有订阅中心文件完整，但服务无法启动；为保护数据，脚本没有自动删除它。"
    return 0
  fi
''',
)

replace_once(
    'tests/conformance.py',
    '''    require('ask_required_jpr3' in bootstrap and '中转模式必须输入 JPR3 对接密钥' in bootstrap, '中转副机仍可跳过对接码')
''',
    '''    require('ask_required_jpr3' in bootstrap and '中转模式必须输入 JPR3 对接密钥' in bootstrap, '中转副机仍可跳过对接码')
    require('refresh_center_runtime_code' in bootstrap and 'cmp -s "$BASE_DIR/$file"' in bootstrap, '已有订阅中心不会刷新自动注册接口')
    require('timeout 75 systemctl restart vvv-sub.service' in bootstrap, '订阅中心程序更新后没有有界重启服务')
''',
)

replace_once(
    'README.md',
    '''- 直连副机注册时只需输入订阅中心 IP 地址或域名，默认使用 HTTPS 8443；首次留空后，可随时输入 `vps` 补注册；
''',
    '''- 直连副机注册时只需输入订阅中心 IP 地址或域名，默认使用 HTTPS 8443；首次留空后，可随时输入 `vps` 补注册；
- 已安装的订阅中心在重复运行安装器时会自动刷新服务程序，但保留订阅密钥、节点数据和备份；
''',
)

print('CENTER RUNTIME REFRESH PATCH APPLIED')

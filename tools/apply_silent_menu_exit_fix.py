#!/usr/bin/env python3
from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding='utf-8')
    if text.count(old) != 1:
        raise SystemExit(f'{path}: target not found exactly once')
    file.write_text(text.replace(old, new, 1), encoding='utf-8')


replace_once(
    'core-src/bootstrap.sh',
    '''detect_installed_modules() {
  INST_PROXY=false
  INST_CENTER=false
  INST_RELAY=false
  INST_LANDING=false
  main_state_valid && INST_PROXY=true
  center_complete && INST_CENTER=true
  relay_enabled && INST_RELAY=true
  landing_state_valid && INST_LANDING=true
}
''',
    '''detect_installed_modules() {
  INST_PROXY=false
  INST_CENTER=false
  INST_RELAY=false
  INST_LANDING=false
  main_state_valid && INST_PROXY=true
  center_complete && INST_CENTER=true
  relay_enabled && INST_RELAY=true
  landing_state_valid && INST_LANDING=true
  # “未安装某模块”是正常检测结果，不能让 set -e 静默退出安装器。
  return 0
}
''',
)

replace_once(
    'tests/conformance.py',
    "    require(positions == sorted(positions), '初始菜单顺序不符合最终要求')\n",
    "    require(positions == sorted(positions), '初始菜单顺序不符合最终要求')\n"
    "    detect_body = text.split('detect_installed_modules() {', 1)[1].split('\\n}', 1)[0]\n"
    "    require('return 0' in detect_body, '模块未安装时检测函数会触发 set -e 静默退出，导致菜单不显示')\n",
)

print('SILENT MENU EXIT FIX APPLIED')

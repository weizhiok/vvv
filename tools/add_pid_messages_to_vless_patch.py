#!/usr/bin/env python3
from pathlib import Path

path = Path('tools/patch_vless_slots.py')
text = path.read_text(encoding='utf-8')

old = '''  [[ -z "$xray_pid" || "$(systemctl show -p MainPID --value xray)" == "$xray_pid" ]] || ok=0
  [[ -z "$sing_pid" || "$(systemctl show -p MainPID --value sing-box)" == "$sing_pid" ]] || ok=0
  verify_xray_runtime || ok=0; verify_sing_runtime || ok=0'''
new = '''  if [[ -n "$xray_pid" ]]; then
    if [[ "$(systemctl show -p MainPID --value xray)" == "$xray_pid" ]]; then
      echo "主 Xray PID 已保持不变：${xray_pid}"
    else
      echo "错误：主 Xray PID 发生变化。" >&2
      ok=0
    fi
  fi
  if [[ -n "$sing_pid" ]]; then
    if [[ "$(systemctl show -p MainPID --value sing-box)" == "$sing_pid" ]]; then
      echo "主 sing-box PID 已保持不变：${sing_pid}"
    else
      echo "错误：主 sing-box PID 发生变化。" >&2
      ok=0
    fi
  fi
  verify_xray_runtime || ok=0; verify_sing_runtime || ok=0'''

if text.count(old) != 1:
    raise SystemExit(f'PID comparison anchor count={text.count(old)}')
path.write_text(text.replace(old, new, 1), encoding='utf-8')

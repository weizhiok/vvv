#!/usr/bin/env python3
import re
from pathlib import Path

path = Path('core-src/host.sh')
text = path.read_text(encoding='utf-8')
new = r'''prompt_initial_mode_and_port() {
  local choice input preset_mode="${VVV_PROXY_MODE:-}" preset_port="${VVV_PROXY_PORT:-}"

  if [[ -n "$preset_mode" ]]; then
    case "$preset_mode" in
      dual|vless|hy2) INSTALL_MODE="$preset_mode" ;;
      *) fail "VVV_PROXY_MODE 必须是 dual、vless 或 hy2。" ;;
    esac
  else
    echo
    echo "请选择要安装的代理协议："
    echo
    echo "1. 同时安装双协议（TCP/443 + UDP/443）【默认】"
    echo "2. 只安装 VLESS + XTLS Vision + REALITY（TCP/443）"
    echo "3. 只安装 Hysteria 2（QUIC/UDP/443）"
    echo "0. 退出"
    echo
    while true; do
      read -r -p "请输入编号 [默认 1]：" choice
      [[ -n "$choice" ]] || choice="1"
      case "$choice" in
        1) INSTALL_MODE="dual"; break ;;
        2) INSTALL_MODE="vless"; break ;;
        3) INSTALL_MODE="hy2"; break ;;
        0) INSTALL_CANCELLED=1; return 0 ;;
        *) echo "请输入 0、1、2 或 3。" ;;
      esac
    done
  fi

  if [[ -n "$preset_port" ]]; then
    preset_port="${preset_port//[[:space:]]/}"
    valid_port "$preset_port" || fail "VVV_PROXY_PORT 必须是 1–65535 之间的数字。"
    INSTALL_PORT="$((10#$preset_port))"
  else
    while true; do
      read -r -p "请输入代理监听端口 [默认 443]：" input
      input="${input//[[:space:]]/}"
      [[ -n "$input" ]] || input="443"
      if valid_port "$input"; then
        INSTALL_PORT="$((10#$input))"
        break
      fi
      echo "端口必须是 1–65535 之间的数字。"
    done
  fi
  echo "已选择模式：$INSTALL_MODE"
  echo "统一监听端口：TCP/UDP ${INSTALL_PORT}（仅启用所选协议）"
}
'''
pattern = r'(?ms)^prompt_initial_mode_and_port\(\) \{.*?^\}\n\nmode_has_vless\(\) \{'
match = re.search(pattern, text)
if not match:
    raise SystemExit('prompt_initial_mode_and_port block not found')
text = re.sub(pattern, lambda _: new + '\nmode_has_vless() {', text, count=1)
path.write_text(text, encoding='utf-8')

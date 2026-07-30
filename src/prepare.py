#!/usr/bin/env python3
from pathlib import Path
import re
import sys

if len(sys.argv) != 4:
    raise SystemExit("usage: prepare.py HOST LANDING CENTER")

host, landing, center = map(Path, sys.argv[1:])

host_text = host.read_text(encoding="utf-8")
host_text, count = re.subn(
    r'^DEFAULT_SNI="www\.softbank\.jp"$',
    'DEFAULT_SNI="${VVV_REALITY_SNI:-www.softbank.jp}"',
    host_text,
    count=1,
    flags=re.M,
)
if count != 1:
    raise SystemExit("无法设置 REALITY 伪装域名变量")

new_func = r'''prompt_initial_mode_and_port() {
  local preset_mode="${VVV_PROTOCOL_MODE:-dual}"
  local preset_port="${VVV_PROXY_PORT:-443}"
  case "$preset_mode" in
    dual|vless|hy2) INSTALL_MODE="$preset_mode" ;;
    *) fail "预设协议模式无效：$preset_mode"; return 1 ;;
  esac
  valid_port "$preset_port" || { fail "预设代理端口无效：$preset_port"; return 1; }
  INSTALL_PORT="$((10#$preset_port))"
  if [[ "$INSTALL_MODE" == "dual" || "$INSTALL_MODE" == "vless" ]]; then
    [[ "$DEFAULT_SNI" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ ]] || {
      fail "REALITY 伪装域名格式无效：$DEFAULT_SNI"; return 1;
    }
  fi
  echo "已选择模式：$INSTALL_MODE"
  echo "统一监听端口：TCP/UDP ${INSTALL_PORT}（仅启用所选协议）"
  [[ "$INSTALL_MODE" == "hy2" ]] || echo "REALITY 伪装域名：$DEFAULT_SNI"
}
'''
host_text, count = re.subn(
    r'(?ms)^prompt_initial_mode_and_port\(\) \{\n.*?^\}\n',
    new_func,
    host_text,
    count=1,
)
if count != 1:
    raise SystemExit("无法替换代理参数函数")
host.write_text(host_text, encoding="utf-8")

landing_text = landing.read_text(encoding="utf-8")
landing_text, count = re.subn(
    r'^PAIRING_KEY=.*$',
    'PAIRING_KEY="${VVV_PAIRING_KEY:-请粘贴以JPR3.开头的完整对接密钥}"',
    landing_text,
    count=1,
    flags=re.M,
)
if count != 1:
    raise SystemExit("无法设置副机对接密钥变量")
landing.write_text(landing_text, encoding="utf-8")

center_text = center.read_text(encoding="utf-8")
required = (
    "VVV_SUB_DOMAIN",
    "VVV_SUB_PORT",
    "--adapter caddyfile",
    "disable_tlsalpn_challenge",
)
missing = [item for item in required if item not in center_text]
if missing:
    raise SystemExit("订阅中心源码缺少必要字段：" + ", ".join(missing))

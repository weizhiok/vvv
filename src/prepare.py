#!/usr/bin/env python3
from pathlib import Path
import re
import sys

if len(sys.argv) != 4:
    raise SystemExit('usage: prepare.py HOST LANDING CENTER')

host, landing, center = map(Path, sys.argv[1:])

s = host.read_text(encoding='utf-8')
s, count = re.subn(
    r'^DEFAULT_SNI="www\.softbank\.jp"$',
    'DEFAULT_SNI="${VVV_REALITY_SNI:-www.softbank.jp}"',
    s,
    count=1,
    flags=re.M,
)
if count != 1:
    raise SystemExit('无法设置 REALITY 伪装域名变量')

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
s, count = re.subn(
    r'(?ms)^prompt_initial_mode_and_port\(\) \{\n.*?^\}\n',
    new_func,
    s,
    count=1,
)
if count != 1:
    raise SystemExit('无法替换代理参数函数')
host.write_text(s, encoding='utf-8')

ls = landing.read_text(encoding='utf-8')
ls, count = re.subn(
    r'^PAIRING_KEY=.*$',
    'PAIRING_KEY="${VVV_PAIRING_KEY:-请粘贴以JPR3.开头的完整对接密钥}"',
    ls,
    count=1,
    flags=re.M,
)
if count != 1:
    raise SystemExit('无法设置副机对接密钥变量')
landing.write_text(ls, encoding='utf-8')

cs = center.read_text(encoding='utf-8')
old = '''read -r -p "请输入订阅访问域名（可直接回车使用 IP 模式）：" domain
domain="${domain,,}"; domain="${domain%.}"
read -r -p "请输入订阅服务端口 [默认 8443]：" public_port
public_port="${public_port:-8443}"; valid_port "$public_port" || fail "端口必须在 1-65535。"'''
new = '''domain="${VVV_SUB_DOMAIN:-}"
domain="${domain,,}"; domain="${domain%.}"
public_port="${VVV_SUB_PORT:-8443}"
valid_port "$public_port" || fail "端口必须在 1-65535。"'''
if old not in cs:
    raise SystemExit('无法替换订阅中心前置参数')
cs = cs.replace(old, new, 1)

needle = r'''public_ip="$(jq -r '.public_ip // empty' /etc/jp-relay/state.json 2>/dev/null || true)"
[[ "$public_ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || fail "无法从代理状态读取本机公网 IPv4。"
'''
replacement = needle + '''systemctl disable --now vvv-sub.service caddy.service >/dev/null 2>&1 || true
rm -rf /etc/vvv-sub /var/lib/vvv-sub
rm -f /etc/systemd/system/vvv-sub.service
systemctl daemon-reload
'''
if needle not in cs:
    raise SystemExit('无法加入订阅中心全新安装清理')
cs = cs.replace(needle, replacement, 1)

show_old = '''show(){
  echo "Clash Verge Rev：${base}/r/${token}/clash"
  echo "Quantumult X：${base}/r/${token}/quantumultx"
  echo "Loon：${base}/r/${token}/loon"
  echo "Shadowrocket：${base}/r/${token}/shadowrocket"
  echo "v2rayNG：${base}/r/${token}/v2rayng"
}'''
show_new = show_old + '''
show_mobile(){
  echo "Quantumult X：${base}/r/${token}/quantumultx"
  echo "Loon：${base}/r/${token}/loon"
  echo "Shadowrocket：${base}/r/${token}/shadowrocket"
  echo "v2rayNG：${base}/r/${token}/v2rayng"
}
show_qr(){
  while IFS= read -r line; do
    name="${line%%：*}"
    url="${line#*：}"
    echo
    echo "【${name}】"
    echo "$url"
    qrencode -t ANSIUTF8 -m1 "$url"
  done < <(show_mobile)
}'''
if show_old not in cs:
    raise SystemExit('无法加入订阅二维码函数')
cs = cs.replace(show_old, show_new, 1)
cs = cs.replace(' urls) show;;', ' urls) show;;\n  qr) show; show_qr;;', 1)

old_qr = '''      2) while IFS= read -r u; do echo; echo "$u"; qrencode -t ANSIUTF8 -m1 "$u"; done < <(show | sed 's/^[^：]*：//');;'''
new_qr = '''      2) show; show_qr;;'''
if old_qr not in cs:
    raise SystemExit('无法修改订阅二维码菜单')
cs = cs.replace(old_qr, new_qr, 1)
cs = cs.replace('/usr/local/sbin/vvv-center urls\nprintf', '/usr/local/sbin/vvv-center qr\nprintf', 1)
center.write_text(cs, encoding='utf-8')

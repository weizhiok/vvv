#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
RAW="https://raw.githubusercontent.com/weizhiok/vvv/main"
TMP="$(mktemp -d /tmp/vvv-install.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
fail(){ echo "错误：$*" >&2; exit 1; }
[[ "$(id -u)" -eq 0 ]] || fail "请使用 root 用户运行。"

if ! command -v curl >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y curl ca-certificates bash python3
  elif command -v apk >/dev/null 2>&1; then
    apk add --no-cache curl ca-certificates bash python3
  else
    fail "系统缺少 curl 或 python3，且无法自动安装。"
  fi
fi

nonce="$(date +%s)-$$"
mkdir -p "$TMP/app"
echo "正在下载 VVV 普通源码……"

curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 \
  "$RAW/src/bootstrap.sh?v=$nonce" -o "$TMP/app/bootstrap.sh" || fail "下载 bootstrap.sh 失败。"

files=(host.sh landing.sh center_install.sh register_sync.sh vvv_manager.sh sub_center.py sync_agent.py)
for file in "${files[@]}"; do
  echo "  下载 $file"
  curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 \
    "$RAW/core-src/$file?v=$nonce-$file" -o "$TMP/app/$file" || fail "下载 $file 失败。"
  [[ -s "$TMP/app/$file" ]] || fail "$file 是空文件。"
done

python3 - "$TMP/app/host.sh" "$TMP/app/landing.sh" "$TMP/app/center_install.sh" <<'PY'
from pathlib import Path
import re, sys
host, landing, center = map(Path, sys.argv[1:])

s = host.read_text(encoding='utf-8')
s, n = re.subn(r'^DEFAULT_SNI="www\.softbank\.jp"$', 'DEFAULT_SNI="${VVV_REALITY_SNI:-www.softbank.jp}"', s, count=1, flags=re.M)
if n != 1:
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
s, n = re.subn(r'(?ms)^prompt_initial_mode_and_port\(\) \{\n.*?^\}\n', new_func, s, count=1)
if n != 1:
    raise SystemExit('无法替换代理参数函数')
host.write_text(s, encoding='utf-8')

ls = landing.read_text(encoding='utf-8')
ls, n = re.subn(r"^PAIRING_KEY=.*$", 'PAIRING_KEY="${VVV_PAIRING_KEY:-请粘贴以JPR3.开头的完整对接密钥}"', ls, count=1, flags=re.M)
if n != 1:
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

needle = 'public_ip="$(jq -r \' .public_ip // empty \' /etc/jp-relay/state.json 2>/dev/null || true)"'
needle = needle.replace("' .public_ip // empty '", "'.public_ip // empty'") + '\n[[ "$public_ip" =~ ^([0-9]{1,3}\\.){3}[0-9]{1,3}$ ]] || fail "无法从代理状态读取本机公网 IPv4。"\n'
replace = needle + '''systemctl disable --now vvv-sub.service caddy.service >/dev/null 2>&1 || true
rm -rf /etc/vvv-sub /var/lib/vvv-sub
rm -f /etc/systemd/system/vvv-sub.service
systemctl daemon-reload
'''
if needle not in cs:
    raise SystemExit('无法加入订阅中心全新安装清理')
cs = cs.replace(needle, replace, 1)

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
PY

for file in bootstrap.sh center_install.sh register_sync.sh vvv_manager.sh host.sh; do
  bash -n "$TMP/app/$file" || fail "$file 语法检查失败。"
done
sh -n "$TMP/app/landing.sh" || fail "landing.sh 语法检查失败。"
python3 -m py_compile "$TMP/app/sub_center.py" "$TMP/app/sync_agent.py" || fail "Python 模块语法检查失败。"

install -d -m700 /usr/local/lib/vvv-source
rm -rf /usr/local/lib/vvv-source/*
cp -a "$TMP/app/." /usr/local/lib/vvv-source/
chmod 700 /usr/local/lib/vvv-source/*.sh /usr/local/lib/vvv-source/*.py 2>/dev/null || true

echo "VVV 普通源码下载和语法检查全部通过。"
if [[ -r /dev/tty ]]; then
  exec bash /usr/local/lib/vvv-source/bootstrap.sh </dev/tty
else
  exec bash /usr/local/lib/vvv-source/bootstrap.sh
fi

#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
RAW="https://raw.githubusercontent.com/weizhiok/vvv/main"
PARTS=9
MODULES_SHA256="79935215bc2f59a463fccbd54b2ad94bd306330f3fcd5fccd52ec33f854d73a5"
TMP="$(mktemp -d /tmp/vvv-install.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
fail(){ echo "错误：$*" >&2; exit 1; }
[[ "$(id -u)" -eq 0 ]] || fail "请使用 root 用户运行。"
if ! command -v curl >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y curl ca-certificates coreutils tar gzip bash python3
  elif command -v apk >/dev/null 2>&1; then
    apk add --no-cache curl ca-certificates coreutils tar gzip bash python3
  else
    fail "系统缺少必要命令，且无法自动安装。"
  fi
fi
for cmd in base64 sha256sum tar gzip bash awk tr wc grep python3; do command -v "$cmd" >/dev/null 2>&1 || fail "系统缺少命令：$cmd"; done
nonce="$(date +%s)-$$"
: > "$TMP/bundle.b64"
echo "正在下载 VVV 核心安装包……"
for ((i=0;i<PARTS;i++)); do
  part="$(printf 'part%02d' "$i")"
  printf '\r正在下载 VVV 核心安装包（%d/%d）……' "$((i+1))" "$PARTS"
  curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 "$RAW/bundle/parts/$part?v=$nonce-$i" -o "$TMP/$part" || fail "下载 $part 失败。"
  [[ -s "$TMP/$part" ]] || fail "$part 是空文件。"
  cat "$TMP/$part" >> "$TMP/bundle.b64"
done
printf '\n'
tr -d '\r\n\t ' < "$TMP/bundle.b64" > "$TMP/bundle.clean.b64"
grep -Eq '^[A-Za-z0-9+/=]+$' "$TMP/bundle.clean.b64" || fail "核心安装包包含非法 Base64 字符。"
(( $(wc -c < "$TMP/bundle.clean.b64") % 4 == 0 )) || fail "核心安装包 Base64 长度异常。"
base64 -d "$TMP/bundle.clean.b64" > "$TMP/vvv-bundle.tar.gz" 2>/dev/null || fail "核心安装包解码失败。"
gzip -t "$TMP/vvv-bundle.tar.gz" 2>/dev/null || fail "核心安装包 gzip 检查失败。"
mkdir -p "$TMP/app"
tar -xzf "$TMP/vvv-bundle.tar.gz" -C "$TMP/app" || fail "解压核心安装包失败。"

echo "正在下载 VVV v2 更新模块……"
curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 "$RAW/v2/modules.b64?v=$nonce" -o "$TMP/modules.b64" || fail "下载 v2 更新模块失败。"
actual="$(sha256sum "$TMP/modules.b64" | awk '{print $1}')"
[[ "$actual" == "$MODULES_SHA256" ]] || fail "v2 更新模块校验失败：$actual"
base64 -d "$TMP/modules.b64" > "$TMP/modules.tar.gz" 2>/dev/null || fail "v2 更新模块解码失败。"
gzip -t "$TMP/modules.tar.gz" || fail "v2 更新模块损坏。"
tar -xzf "$TMP/modules.tar.gz" -C "$TMP/app" || fail "v2 更新模块解压失败。"

python3 - "$TMP/app/host.sh" "$TMP/app/landing.sh" <<'PY'
from pathlib import Path
host,landing=map(Path,__import__('sys').argv[1:])
s=host.read_text(encoding='utf-8')
s=s.replace('DEFAULT_SNI="www.softbank.jp"','DEFAULT_SNI="${VVV_REALITY_SNI:-www.softbank.jp}"',1)
old='''prompt_initial_mode_and_port() {
  local choice input
  echo
  echo "请选择要安装的代理协议："'''
new='''prompt_initial_mode_and_port() {
  local choice input preset_mode="${VVV_PROTOCOL_MODE:-}" preset_port="${VVV_PROXY_PORT:-}"
  if [[ -n "$preset_mode" ]]; then
    case "$preset_mode" in
      dual|vless|hy2) INSTALL_MODE="$preset_mode" ;;
      *) fail "预设协议模式无效：$preset_mode"; return 1 ;;
    esac
  else
    echo
    echo "请选择要安装的代理协议："'''
if old not in s: raise SystemExit('无法定位 host 协议提示代码')
s=s.replace(old,new,1)
old='''  while true; do
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
  echo "已选择模式：$INSTALL_MODE"'''
new='''    while true; do
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
    valid_port "$preset_port" || { fail "预设代理端口无效：$preset_port"; return 1; }
    INSTALL_PORT="$((10#$preset_port))"
  else
    while true; do
      read -r -p "请输入代理监听端口 [默认 443]：" input
      input="${input//[[:space:]]/}"
      [[ -n "$input" ]] || input="443"
      if valid_port "$input"; then INSTALL_PORT="$((10#$input))"; break; fi
      echo "端口必须是 1–65535 之间的数字。"
    done
  fi
  echo "已选择模式：$INSTALL_MODE"'''
if old not in s: raise SystemExit('无法定位 host 端口提示代码')
s=s.replace(old,new,1)
needle='''  echo "已选择模式：$INSTALL_MODE"
  echo "统一监听端口：TCP/UDP ${INSTALL_PORT}（仅启用所选协议）"
}'''
repl='''  if [[ "$INSTALL_MODE" == "dual" || "$INSTALL_MODE" == "vless" ]]; then
    [[ "$DEFAULT_SNI" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\\.)+[A-Za-z]{2,63}$ ]] || { fail "REALITY 伪装域名格式无效：$DEFAULT_SNI"; return 1; }
  fi
  echo "已选择模式：$INSTALL_MODE"
  echo "统一监听端口：TCP/UDP ${INSTALL_PORT}（仅启用所选协议）"
  [[ "$INSTALL_MODE" == "hy2" ]] || echo "REALITY 伪装域名：$DEFAULT_SNI"
}'''
if needle not in s: raise SystemExit('无法定位 host 参数结尾代码')
s=s.replace(needle,repl,1)
host.write_text(s,encoding='utf-8')
ls=landing.read_text(encoding='utf-8')
old="PAIRING_KEY='请粘贴以JPR3.开头的完整对接密钥'"
if old not in ls: raise SystemExit('无法定位 landing 对接密钥代码')
ls=ls.replace(old,'PAIRING_KEY="${VVV_PAIRING_KEY:-请粘贴以JPR3.开头的完整对接密钥}"',1)
landing.write_text(ls,encoding='utf-8')
PY

for f in bootstrap.sh center_install.sh register_sync.sh vvv_manager.sh host.sh; do [[ -f "$TMP/app/$f" ]] || fail "缺少 $f"; bash -n "$TMP/app/$f" || fail "$f 语法错误"; done
sh -n "$TMP/app/landing.sh" || fail "landing.sh 语法错误"
python3 -m py_compile "$TMP/app/sub_center.py" "$TMP/app/sync_agent.py" || fail "Python 模块语法错误"
install -d -m 700 /usr/local/lib/vvv-source
rm -rf /usr/local/lib/vvv-source/*
cp -a "$TMP/app/." /usr/local/lib/vvv-source/
chmod 700 /usr/local/lib/vvv-source/*.sh /usr/local/lib/vvv-source/*.py 2>/dev/null || true
echo "VVV v2 安装文件校验全部通过。"
if [[ -r /dev/tty ]]; then exec bash /usr/local/lib/vvv-source/bootstrap.sh </dev/tty; else exec bash /usr/local/lib/vvv-source/bootstrap.sh; fi

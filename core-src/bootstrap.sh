#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ "$(id -u)" -eq 0 ]] || { echo "错误：请使用 root 用户运行。" >&2; exit 1; }

install_host(){
  bash "$BASE_DIR/host.sh"
  [[ -x /usr/local/sbin/vps ]] && cp -f /usr/local/sbin/vps /usr/local/sbin/vvv-host-original
}
enable_relay(){
  [[ -f /etc/jp-relay/state.json ]] || return 1
  tmp=$(mktemp); jq '.relay_manager_enabled=true | .updated_at=(now|todate)' /etc/jp-relay/state.json > "$tmp"; install -m600 "$tmp" /etc/jp-relay/state.json; rm -f "$tmp"
}
install_unified_manager(){
  install -d -m700 /etc/vvv /usr/local/lib/vvv
  install -m755 "$BASE_DIR/vvv_manager.sh" /usr/local/lib/vvv/vvv_manager.sh
  install -m755 "$BASE_DIR/register_sync.sh" /usr/local/lib/vvv/register_sync.sh
  install -m755 "$BASE_DIR/sync_agent.py" /usr/local/lib/vvv/sync_agent.py
  cat > /usr/local/sbin/vps <<'EOF'
#!/usr/bin/env bash
exec /usr/local/lib/vvv/vvv_manager.sh "$@"
EOF
  chmod 700 /usr/local/sbin/vps
}
write_roles(){
  local center=$1 relay=$2 landing=$3 proxy=$4 role=$5
  install -d -m700 /etc/vvv
  jq -n --arg role "$role" --argjson c "$center" --argjson r "$relay" --argjson l "$landing" --argjson p "$proxy" '{schema:1,primary_role:$role,roles:{center:$c,relay:$r,landing:$l,proxy:$p}}' > /etc/vvv/roles.json
  chmod 600 /etc/vvv/roles.json
}
ask_code(){
  local __var=$1 prompt=$2 v
  read -r -p "$prompt（可直接回车稍后配置）：" v
  printf -v "$__var" '%s' "$v"
}

cat <<'EOF'
========== VVV 一体化安装管理 ==========

1. 安装订阅中心（含自身代理）
   订阅中心 + 本机直连代理

2. 安装中转主机（含自身代理）
   本机直连代理 + VPS中转 + HTTP/HTTPS/SOCKS5中转

3. 安装中转副机
   使用对接密钥自动安装

4. 安装直连代理
   不安装订阅中心，不安装中转管理，只安装本机直连代理

5. 以上全部安装（不含副机）
   订阅中心 + 本机直连代理 + 全部中转管理

0. 退出
EOF
while true; do read -r -p "请输入编号：" choice; case "$choice" in 0|1|2|3|4|5) break;; *) echo "请输入 0-5。";; esac; done
[[ "$choice" == 0 ]] && exit 0

case "$choice" in
  1)
    install_host
    write_roles true false false true center
    bash "$BASE_DIR/center_install.sh"
    code=$(cat /etc/vvv-sub/registration.code)
    bash "$BASE_DIR/register_sync.sh" center "$code"
    ;;
  2)
    install_host; enable_relay
    write_roles false true false true relay
    ask_code code "请输入订阅中心接入码"
    bash "$BASE_DIR/register_sync.sh" relay "$code"
    ;;
  3)
    read -r -p "请输入完整 JPR3 对接密钥：" key
    [[ "$key" == JPR3.* ]] || { echo "错误：对接密钥必须以 JPR3. 开头。" >&2; exit 1; }
    tmp=$(mktemp /tmp/vvv-landing.XXXXXX.sh)
    awk -v key="$key" 'BEGIN{done=0} !done && /^PAIRING_KEY=/ {print "PAIRING_KEY=\047" key "\047"; done=1; next} {print}' "$BASE_DIR/landing.sh" > "$tmp"
    chmod 700 "$tmp"; sh "$tmp"; rm -f "$tmp"
    [[ -x /usr/local/sbin/vps ]] && cp -f /usr/local/sbin/vps /usr/local/sbin/vvv-landing-original
    write_roles false false true false landing
    ;;
  4)
    install_host
    write_roles false false false true direct
    ask_code code "请输入订阅中心接入码"
    bash "$BASE_DIR/register_sync.sh" direct "$code"
    ;;
  5)
    install_host; enable_relay
    write_roles true true false true all
    bash "$BASE_DIR/center_install.sh"
    code=$(cat /etc/vvv-sub/registration.code)
    bash "$BASE_DIR/register_sync.sh" all "$code"
    ;;
esac
install_unified_manager
printf '\nVVV 安装完成。以后统一输入：vps\n'
/usr/local/sbin/vps

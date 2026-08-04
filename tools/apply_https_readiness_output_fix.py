#!/usr/bin/env python3
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
transport_path = ROOT / "core-src" / "center_transport.sh"
conformance_path = ROOT / "tests" / "conformance.py"

old_block = r'''check_public(){
  local attempt
  for attempt in $(seq 1 120); do
    check_public_once && return 0
    (( attempt % 10 != 0 )) || echo "统一订阅入口仍在准备：已等待 $((attempt*2)) 秒……"
    sleep 2
  done
  echo "统一订阅入口在 240 秒内未通过健康检查。" >&2
  return 1
}
'''

new_block = r'''check_public(){
  local mode attempt started elapsed next_progress error_log
  mode="$(value '.transport_mode')"
  started=$SECONDS
  next_progress=10
  error_log="$(mktemp /tmp/vvv-public-check.XXXXXX)"

  for attempt in $(seq 1 121); do
    : > "$error_log"
    if check_public_once 2>"$error_log"; then
      elapsed=$((SECONDS-started))
      case "$mode" in
        direct-https) echo "HTTPS 证书和统一订阅入口已就绪，共等待 ${elapsed} 秒。";;
        tunnel) echo "Cloudflare Tunnel 和统一订阅入口已就绪，共等待 ${elapsed} 秒。";;
        *) echo "统一订阅入口已就绪，共等待 ${elapsed} 秒。";;
      esac
      rm -f "$error_log"
      return 0
    fi

    elapsed=$((SECONDS-started))
    if (( attempt == 1 )); then
      case "$mode" in
        direct-https) echo "正在等待 HTTPS 证书和统一订阅入口就绪……";;
        tunnel) echo "正在等待 Cloudflare Tunnel 和统一订阅入口就绪……";;
        *) echo "正在等待统一订阅入口就绪……";;
      esac
    elif (( elapsed >= next_progress )); then
      echo "统一订阅入口仍在准备：已等待 ${elapsed} 秒……"
      next_progress=$((((elapsed/10)+1)*10))
    fi

    (( elapsed < 240 )) || break
    sleep 2
  done

  elapsed=$((SECONDS-started))
  echo "统一订阅入口在 ${elapsed} 秒内未通过健康检查。" >&2
  if [[ -s "$error_log" ]]; then
    echo "最近一次 curl 错误：" >&2
    sed 's/^/  /' "$error_log" >&2
  fi
  echo "Caddy 当前状态和最近日志：" >&2
  systemctl --no-pager --full status caddy.service >&2 || true
  journalctl -u caddy.service -n 80 --no-pager >&2 || true
  echo "订阅中心内部服务当前状态：" >&2
  systemctl --no-pager --full status vvv-sub.service >&2 || true
  rm -f "$error_log"
  return 1
}
'''

transport = transport_path.read_text(encoding="utf-8")
if old_block not in transport:
    raise SystemExit("center_transport.sh 的旧健康检查锚点不存在，停止修改。")
transport = transport.replace(old_block, new_block, 1)
transport_path.write_text(transport, encoding="utf-8")

anchor = "    require(\"api=\\\"http://$(value '.public_ip'):$(value '.listen_port')\\\"\" in transport, '副机 API 不是固定 IP 地址')\n"
addition = anchor + """    for token in (\n        '2>\"$error_log\"', 'next_progress=10', 'elapsed >= next_progress',\n        '正在等待 HTTPS 证书和统一订阅入口就绪',\n        'HTTPS 证书和统一订阅入口已就绪，共等待',\n        '最近一次 curl 错误', 'journalctl -u caddy.service -n 80 --no-pager',\n    ):\n        require(token in transport, f'HTTPS 就绪等待输出缺少：{token}')\n    require('check_public_once && return 0' not in transport, '健康检查仍直接显示中间 curl 错误')\n    require('attempt % 10' not in transport, '健康检查仍使用旧的 20 秒进度输出')\n"""
conformance = conformance_path.read_text(encoding="utf-8")
if anchor not in conformance:
    raise SystemExit("tests/conformance.py 的插入锚点不存在，停止修改。")
conformance = conformance.replace(anchor, addition, 1)
conformance_path.write_text(conformance, encoding="utf-8")

subprocess.run(["bash", "-n", str(transport_path)], check=True)
subprocess.run(["python3", "-B", "-m", "py_compile", str(conformance_path)], check=True)
subprocess.run(["python3", "-B", str(conformance_path)], cwd=ROOT, check=True)

subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=ROOT, check=True)
subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], cwd=ROOT, check=True)
subprocess.run([
    "git", "rm", "-f",
    "tools/apply_https_readiness_output_fix.py",
    ".github/workflows/apply-https-readiness-output-fix.yml",
], cwd=ROOT, check=True)
subprocess.run(["git", "add", "core-src/center_transport.sh", "tests/conformance.py"], cwd=ROOT, check=True)
subprocess.run([
    "git", "commit", "-m",
    "Clarify HTTPS readiness progress and errors",
], cwd=ROOT, check=True)
subprocess.run(["git", "push"], cwd=ROOT, check=True)

#!/usr/bin/env python3
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

OUT = Path('/root') / f"VVV-诊断报告-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
SENSITIVE_KEYS = re.compile(r'(password|token|secret|private|uuid|master|recovery|credential|authorization|cookie)', re.I)
NODE_PROBE = Path('/usr/local/lib/vvv/node_probe.py')
SERVICES = ['xray','sing-box','caddy','vvv-sub','vvv-cloudflared','vvv-sync.timer','vvv-sync.path','vvv-temp-cleanup.timer','daily-reboot.timer']


def run(command, timeout=20):
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
        return (result.stdout + result.stderr).strip()
    except Exception as exc:
        return f'执行失败：{exc}'


def redact_text(text):
    text = re.sub(r'(?i)(VVC1|JPR3)\.[A-Za-z0-9._-]+', r'\1.[已隐藏]', str(text))
    text = re.sub(r'(?i)(password|token|secret|private_key|master_token|recovery_password)(\s*[:=]\s*)[^\s,}\]]+', r'\1\2[已隐藏]', text)
    text = re.sub(r'\b[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}\b', '[UUID已隐藏]', text)
    return text


def redact_json(value):
    if isinstance(value, dict):
        return {key: ('[已隐藏]' if SENSITIVE_KEYS.search(str(key)) else redact_json(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_json(item) for item in value]
    return value


def add(lines, title, body):
    lines += ['', f'========== {title} ==========', redact_text(body or '-')]


def file_digest(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except Exception:
        return '-'


def main():
    lines = ['VVV 故障诊断报告', f'生成时间：{datetime.now().isoformat()}']
    add(lines, '系统', run(['bash','-lc','cat /etc/os-release; uname -a; systemd-detect-virt || true']))
    add(lines, '资源', run(['bash','-lc','lscpu | sed -n "1,25p"; free -h; swapon --show; df -hT / /var /etc 2>/dev/null']))
    add(lines, '网络与端口', run(['bash','-lc','ip -brief address; ip route; ss -lntup']))
    add(lines, 'BBR 与时区', run(['bash','-lc','sysctl net.ipv4.tcp_congestion_control net.core.default_qdisc 2>/dev/null; timedatectl']))
    add(lines, '程序版本', run(['bash','-lc','/usr/local/bin/xray version 2>/dev/null | head -1; /usr/local/bin/sing-box version 2>/dev/null | head -2; /usr/local/bin/caddy version 2>/dev/null; /usr/local/bin/cloudflared --version 2>/dev/null; rclone version 2>/dev/null | head -1']))
    for service in SERVICES:
        add(lines, f'服务 {service}', run(['systemctl','--no-pager','--full','status',service], timeout=10))
    for path in ('/etc/vvv/roles.json','/etc/jp-relay/state.json','/etc/jp-relay/landing-state.json','/etc/vvv-sub/config.json','/etc/vvv/client.json'):
        p = Path(path)
        if p.exists():
            try:
                value = redact_json(json.loads(p.read_text(encoding='utf-8')))
                add(lines, f'配置 {path}', json.dumps(value, ensure_ascii=False, indent=2))
            except Exception:
                add(lines, f'配置 {path}', f'无法解析；SHA-256={file_digest(path)}')
    if Path('/var/lib/vvv-sub/backups').exists():
        add(lines, '本地备份', run(['bash','-lc', "find /var/lib/vvv-sub/backups -maxdepth 1 -type f -name 'VVV_*.enc' -printf '%TY-%Tm-%Td %TH:%TM:%TS %s %f\\n' | sort -r | head -100; du -sh /var/lib/vvv-sub/backups"]))
    if Path('/etc/vvv-sub/rclone.conf').exists() and shutil.which('rclone'):
        add(lines, '云备份目录', run(['rclone','--config','/etc/vvv-sub/rclone.conf','lsf','vvvcloud:vvv','--max-depth','2'], timeout=40))
    if NODE_PROBE.exists():
        add(lines, '逐节点连接检测', run(['python3', str(NODE_PROBE)], timeout=300))
    add(lines, '最近错误日志', run(['bash','-lc',"journalctl -p warning..alert --since '-2 days' --no-pager -u xray -u sing-box -u caddy -u vvv-sub -u vvv-cloudflared -u vvv-sync -u vvv-temp-cleanup | tail -300"], timeout=30))
    add(lines, '关键文件摘要', '\n'.join(f'{path}\t{file_digest(path)}' for path in ('/etc/vvv/roles.json','/etc/jp-relay/state.json','/etc/vvv-sub/config.json','/etc/vvv/client.json')))
    OUT.write_text('\n'.join(lines) + '\n', encoding='utf-8'); os.chmod(OUT, 0o600)
    print(f'诊断报告已生成：{OUT}')


if __name__ == '__main__':
    main()

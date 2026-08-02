#!/usr/bin/env python3
from pathlib import Path
import base64, gzip, re

ROOT = Path(__file__).resolve().parents[1]

bootstrap = gzip.decompress(base64.b64decode('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')).decode('utf-8')
(ROOT / 'core-src/bootstrap.sh').write_text(bootstrap, encoding='utf-8')

test_path = ROOT / 'tests/conformance.py'
tests = test_path.read_text(encoding='utf-8')

new_menu_test = r"""
def test_menu_and_front_loaded_parameters():
    text = read('core-src/bootstrap.sh')
    labels = [
        '1. 安装订阅中心 + 中转主机 + 自身代理',
        '2. 安装订阅中心 + 自身代理',
        '3. 安装中转主机 + 自身代理',
        '4. 安装中转副机（通过主机代理）',
        '5. 安装直连代理',
        '0. 退出',
    ]
    positions = [text.index(label) for label in labels]
    require(positions == sorted(positions), '初始菜单顺序不符合最终要求')
    for token in (
        '========== 安装参数（全部前置设置） ==========',
        '请输入代理监听端口 [默认 443]',
        '请输入 VLESS + REALITY 伪装域名 [默认 www.softbank.jp]',
        '请输入订阅 HTTPS 域名（直接回车使用本机公网 IP）',
        '请输入订阅 HTTPS 端口 [默认 8443]',
        '请输入订阅中心接入码',
        '请输入完整 JPR3 对接密钥',
        '========== 安装参数总览 ==========',
        '直接开始全自动安装',
        'export VVV_PROTOCOL_MODE VVV_PROXY_PORT VVV_REALITY_SNI',
        'export VVV_SUB_DOMAIN VVV_SUB_PORT',
    ):
        require(token in text, f'缺少前置参数功能：{token}')
    require(text.index('show_install_menu') < text.index('landing_state_valid && fail'), '兼容性判断发生在菜单显示之前')
    summary_call = text.rindex('show_parameter_summary')
    execute = text.index('case "$choice" in', summary_call)
    require('read -r' not in text[summary_call:execute], '参数总览后仍存在确认输入')
    require('rebuild_roles_from_system' in text, '角色状态没有根据实际模块合并重建')
    require('primary=center-relay' in text, '订阅中心与中转主机不能合并为 center-relay')
    require('register_current_main_role' in text, '追加角色后没有按最终角色重新注册')
    require('bash "$BASE_DIR/register_sync.sh" landing "$code"' in text, '中转副机没有自动注册')
    require('write_roles true true false true all' not in text, '仍保留旧 all 主角色')
"""

tests, count = re.subn(
    r"\ndef test_menu_and_front_loaded_parameters\(\):\n.*?(?=\n\ndef sample_host_state\(\):)",
    "\n" + new_menu_test.strip() + "\n",
    tests,
    count=1,
    flags=re.S,
)
if count != 1:
    raise SystemExit('failed to replace menu test')

new_reentrant_test = r"""
def test_https_and_reentrant_installation():
    installer = read('vvv-install.sh')
    bootstrap = read('core-src/bootstrap.sh')
    center = read('core-src/center_install.sh')
    manager = read('core-src/vvv_manager.sh')
    require('当前版本只支持全新安装' not in installer, '网络入口仍拒绝已有或中断状态')
    require('始终进入安装菜单' in installer, '网络入口没有承诺重复运行仍进入菜单')
    require('mv "$TMP/app" "$target"' in installer and '.vvv-source.previous' in installer, '下载源码没有通过原子替换防止中断残留')
    for token in (
        'show_install_menu',
        'center_complete',
        'center_partial',
        'backup_and_reset_partial_center',
        'ensure_host',
        'ensure_center',
        'rebuild_roles_from_system',
        'register_current_main_role',
        '复用现有协议、端口和永久凭证',
        '保留现有订阅密钥、已注册主机和备份数据',
    ):
        require(token in bootstrap, f'重复安装或断点续装缺少：{token}')
    require('rm -rf /etc/vvv /etc/jp-relay' not in installer, '网络入口仍会删除已有代理或角色状态')
    require('直接回车使用本机公网 IP' in bootstrap and 'VVV_SUB_DOMAIN=""' in bootstrap, '订阅域名不能留空使用公网 IP')
    require('域名不能为空' not in bootstrap, '仍强制要求输入订阅域名')
    require('mode=domain' in center and 'mode=ip' in center, '没有同时实现域名与 IP HTTPS 模式')
    require('base_url="https://${site_host}:${public_port}"' in center, '订阅中心基础地址不是统一 HTTPS')
    require('http://${public_ip}' not in center, '仍保留明文 IP 订阅地址')
    require("'certbot>=5.4,<6'" in center, 'IP 模式没有安装 Certbot 5.4+')
    for token in ('--preferred-profile shortlived', '--ip-address "$public_ip"', 'vvv-ip-cert-renew.timer', 'deploy-ip-cert.sh'):
        require(token in center, f'IP 证书申请或续期缺少：{token}')
    require('log { output discard }' not in center, 'Caddy log 块仍使用无效单行语法')
    require('log {\n    output discard\n  }' in center, 'Caddy log 块没有使用规范多行语法')
    require('systemctl reload caddy.service' not in center, 'admin off 模式仍错误调用 Caddy reload')
    require('ExecReload=/usr/local/bin/caddy reload' not in center, 'Caddy 服务仍配置依赖 admin API 的 reload')
    require('.vvv-ip-final-active' in center, 'IP 证书首次部署和续期部署没有使用状态标记分流')
    require('timeout 75 systemctl restart caddy.service' in center, 'IP 证书续期没有使用有界 Caddy 重启')
    require('跳过重复 apt update' in center, '订阅中心仍可能静默重复刷新软件源')
    require('caddy fmt --overwrite /etc/caddy/Caddyfile' in center, 'Caddyfile 没有在验证前自动格式化')
    require('继续安装订阅中心' in bootstrap and '当前 SSH 不受影响' in bootstrap, '代理安装后没有明确显示订阅中心进度')
    require('检查并升级 VVV' not in manager and 'update_vvv' not in manager, '仍保留原地升级兼容入口')
    require('sync_role' not in manager, '仍保留旧 all 角色兼容映射')
"""

tests, count = re.subn(
    r"\ndef test_https_and_fresh_install_only\(\):\n.*?(?=\n\ndef test_apt_lock_policy\(\):)",
    "\n" + new_reentrant_test.strip() + "\n",
    tests,
    count=1,
    flags=re.S,
)
if count != 1:
    raise SystemExit('failed to replace fresh install test')

tests = tests.replace(
    "        test_https_and_fresh_install_only,",
    "        test_https_and_reentrant_installation,",
)
tests = tests.replace(
    "require('仅支持全新 Debian 13' in readme, 'README 没有说明仅支持全新 Debian 13')",
    "require('仅支持 Debian 13' in readme, 'README 没有说明仅支持 Debian 13')",
)
test_path.write_text(tests, encoding='utf-8')

readme_path = ROOT / 'README.md'
readme = readme_path.read_text(encoding='utf-8')
readme = readme.replace(
    'VVV 使用一个固定安装入口，在全新的 **Debian 13 + systemd** VPS 上安装和管理代理、订阅与中转线路。',
    'VVV 使用一个固定安装入口，在 **Debian 13 + systemd** VPS 上安装和管理代理、订阅与中转线路。安装命令可以重复运行：每次都会进入安装菜单，并按当前状态续装、修复或追加角色。',
    1,
)
readme = readme.replace(
    '`install` 是固定入口分支，会自动取得 `main` 分支经过验证的最新安装程序。安装完成后统一输入：',
    '`install` 是固定入口分支，会自动取得 `main` 分支经过验证的最新安装程序。无论首次安装、SSH 中断后续装，还是已经安装后追加其他角色，重新运行同一条安装命令都会先显示安装菜单。安装完成后统一输入：',
    1,
)
needle = '订阅域名可以直接按回车留空。留空时自动使用本机公网 IPv4，并申请 Let’s Encrypt 短期公网 IP 证书。参数总览显示后直接开始安装，不再要求输入 `Y`，安装过程中也不会穿插新的问题。'
extra = "\n\n重复安装规则：\n\n- 已安装的本机代理会复用原协议、端口和永久凭证，不重新生成节点；\n- 已安装的订阅中心会保留订阅密钥、已注册主机和备份数据；\n- 后续选择新的角色时，只追加缺少的模块，并自动合并最终角色。例如先安装菜单 2，再运行菜单 3，最终会成为“订阅中心 + 中转主机 + 自身代理”；\n- SSH 在参数输入或源码下载期间中断，不会再把 VPS 判定为“必须重装系统”；\n- 订阅中心安装中途断开时，下次选择带订阅中心的角色会先备份残留，再清理不完整组件并续装；\n- 中转副机与本机代理/订阅中心/中转主机不能安装在同一台 VPS，但安装菜单仍会正常显示并给出明确提示。"
if needle not in readme:
    raise SystemExit('README parameter paragraph not found')
readme = readme.replace(needle, needle + extra, 1)
readme = readme.replace('- 仅支持全新 Debian 13；', '- 仅支持 Debian 13；', 1)
old_strategy = "## 安装策略\n\n当前版本只按全新 Debian 13 首次安装设计。检测到旧 VVV 状态时会停止，不提供原地升级、迁移或旧版本兼容。"
new_strategy = "## 安装策略\n\n安装入口支持重复执行和角色追加，但仍不迁移 Debian 12、Alpine、OpenRC 或其他旧系统方案。每次运行都会刷新并验证安装源码，然后进入安装菜单；现有完整模块会被保留，只安装所选角色缺少的部分。"
if old_strategy not in readme:
    raise SystemExit('README install strategy not found')
readme = readme.replace(old_strategy, new_strategy, 1)
readme_path.write_text(readme, encoding='utf-8')

print('REENTRANT INSTALLER PATCH APPLIED')

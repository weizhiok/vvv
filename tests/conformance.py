#!/usr/bin/env python3
import base64
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / 'core-src'


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def load_sub_center():
    path = CORE / 'sub_center.py'
    spec = importlib.util.spec_from_file_location('vvv_sub_center', path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


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


def sample_host_state():
    return {
        'protocol_mode': 'dual',
        'public_ip': '198.51.100.10',
        'listen_port': 443,
        'sni': 'www.softbank.jp',
        'direct_base_name': 'JP-198.51.100.10:443',
        'vless': {
            'direct_user': {'uuid': '11111111-1111-4111-8111-111111111111'},
            'reality': {'public_key': 'PublicKeyAudit', 'short_id': '0123456789abcdef'},
        },
        'hy2': {
            'direct_user': {'password': 'hy2-password'},
            'server_name': 'jp-hy2.jp-relay.local',
            'certificate_pin_hex': 'aa' * 32,
            'certificate_public_key_sha256': base64.b64encode(bytes(range(32))).decode(),
            'obfs_password': 'salamander-secret',
        },
        'relays': [],
        'upstream_relays': [],
    }


def test_subscription_renderers():
    module = load_sub_center()
    nodes = module.nodes_from_host({'host_id': 'audit-host-001', 'role': 'center-relay', 'state': sample_host_state()})
    require({n['protocol'] for n in nodes} == {'vless', 'hysteria2'}, '双协议直连节点没有同时进入订阅')
    clash = module.render_clash(nodes)
    qx = module.render_qx(nodes)
    loon = module.render_loon(nodes)
    shadowrocket = base64.b64decode(module.render_shadowrocket(nodes)).decode('utf-8')
    require('type: vless' in clash and 'type: hysteria2' in clash, 'Clash 订阅缺少双协议节点')
    require('vless=' in qx and 'hysteria' not in qx.lower(), 'Quantumult X 应只输出 VLESS')
    require('salamander-password=salamander-secret' in loon, 'Loon HY2 混淆密码格式错误')
    require('salamander-password="' not in loon, 'Loon HY2 混淆密码仍带双引号')
    require('vless://' in shadowrocket and 'hysteria2://' in shadowrocket, 'Shadowrocket 缺少双协议链接')
    source = read('core-src/sub_center.py')
    short_paths = "{'c': 'clash', 'qx': 'quantumultx', 'ln': 'loon', 'sr': 'shadowrocket'}"
    require(short_paths in source, '订阅短路径集合不正确')
    for token in ('v2rayNG', 'v2rayng', "'v2':"):
        require(token not in source, f'订阅中心仍保留已弃用客户端：{token}')


def test_backup_policy():
    center = read('core-src/center_install.sh')
    backup = read('core-src/backup_manager.py')
    rclone = read('core-src/rclone_manager.sh')
    register = read('core-src/register_sync.sh')
    sub = read('core-src/sub_center.py')
    prepare = read('src/prepare.py')
    production = '\n'.join((center, backup, rclone, register, sub, prepare))
    for token in ('vvv-backup-pull', '/api/v1/backup', '/var/backups/vvv-remote'):
        require(token not in production, f'仍保留旧远程备份逻辑：{token}')
    require('backup.timer' not in production and 'backup-pull.timer' not in production, '仍存在备份定时器')
    require('立即生成本地备份' not in center and '手动本地备份' not in center, '仍存在手动备份菜单')
    require('rm -f "$CFG_DIR/cloud.json" "$CFG_DIR/rclone.conf"' in center, '云备份默认状态不是关闭')
    require('rclone.org/install.sh' not in center, '订阅中心首次安装不应安装 rclone')
    require('first-install' in center, '首次安装没有自动备份')
    for token in ('before-line-change', 'after-line-change'):
        require(token in prepare, f'线路事务缺少 {token} 备份')
    for token in ('before-host-register', 'after-host-register', 'before-node-sync', 'after-node-sync'):
        require(token in sub, f'订阅数据写入缺少 {token} 备份')
    for token in ('before-cloud-backup-enabled', 'after-cloud-backup-enabled', 'before-cloud-backup-disabled', 'after-cloud-backup-disabled'):
        require(token in rclone, f'云备份配置缺少事务事件 {token}')
    require("'copyto'" in backup and "'sync'" not in backup, '云上传必须使用 copyto 而不是 sync')
    require('-aes-256-cbc' in backup and '-pbkdf2' in backup and '.enc' in backup, '本地备份加密格式错误')


def test_jpr3_and_slot_architecture():
    prepare = read('src/prepare.py')
    landing = read('core-src/landing.sh')
    for token in (
        'subscription_registration_code', 'build_vless_slot_configs', 'sync_vless_slot_services',
        'vvv-vless-slot@', 'build_hy2_slot_configs', 'sync_hy2_slot_services', 'vvv-hy2-slot@',
        '主 Xray PID 已保持不变', '主 sing-box PID 已保持不变',
    ):
        require(token in prepare, f'最终槽位/JPR3 转换器缺少 {token}')
    require('python3' in landing, '落地脚本没有显式安装 Python 运行时')
    require('.schema==3' in landing and '.type=="jp-relay-landing"' in landing, '落地脚本没有严格校验 JPR3')
    require('actual_checksum' in landing and 'expected_checksum' in landing, '落地脚本没有校验 JPR3 摘要')


def test_no_qr_output():
    files = [
        'vvv-install.sh', 'core-src/bootstrap.sh', 'core-src/host.sh', 'core-src/landing.sh',
        'core-src/center_install.sh', 'core-src/vvv_manager.sh', 'src/prepare.py',
        'tests/final_runtime_validation.sh', '.github/workflows/validate.yml', 'README.md',
    ]
    text = '\n'.join(read(path) for path in files)
    for token in ('qrencode', 'qr_helper'):
        require(token not in text, f'仍保留二维码实现：{token}')
    implementation = '\n'.join(read(path) for path in files[:7])
    require('二维码' not in implementation, '生产脚本仍保留二维码菜单、文件或提示')
    require(not (ROOT / 'core-src/qr_helper.sh').exists(), '二维码辅助文件仍存在')


def test_https_and_reentrant_installation():
    installer = read('vvv-install.sh')
    bootstrap = read('core-src/bootstrap.sh')
    center = read('core-src/center_install.sh')
    manager = read('core-src/vvv_manager.sh')
    require('当前版本只支持全新安装' not in installer, '网络入口仍拒绝已有或中断状态')
    require('始终进入安装菜单' in installer, '网络入口没有承诺重复运行仍进入菜单')
    for token in (
        'SOURCE_STAGING="/usr/local/lib/.vvv-source.staging.$$"',
        'SOURCE_BACKUP="/usr/local/lib/.vvv-source.previous.$$"',
        'cp -a "$TMP/app" "$SOURCE_STAGING"',
        'mv "$SOURCE_STAGING" "$SOURCE_TARGET"',
        'mv "$SOURCE_BACKUP" "$SOURCE_TARGET"',
        'SOURCE_SWAP_COMMITTED=1',
    ):
        require(token in installer, f'源码安全替换缺少：{token}')
    require('mv "$TMP/app" "$SOURCE_TARGET"' not in installer, '仍从 /tmp 跨文件系统直接替换正式源码')
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


def test_apt_lock_policy():
    sources = {
        'network installer': read('vvv-install.sh'),
        'host installer': read('core-src/host.sh'),
        'landing installer': read('core-src/landing.sh'),
        'subscription center': read('core-src/center_install.sh'),
        'rclone manager': read('core-src/rclone_manager.sh'),
    }
    for label, source in sources.items():
        require('DPkg::Lock::Timeout=600' not in source, f'{label} 仍会等待 APT 锁 600 秒')
        require('DPkg::Lock::Timeout=120' not in source, f'{label} 仍会等待 APT 锁 120 秒')
        require('DPkg::Lock::Timeout=10' in source, f'{label} 没有使用 10 秒 APT 锁上限')
    require('python3 python3-venv iproute2' in sources['host installer'], '主安装阶段没有一次性安装 python3-venv')
    for label in ('network installer', 'host installer', 'landing installer', 'subscription center', 'rclone manager'):
        require('Acquire::IndexTargets::deb-src::Sources::DefaultEnabled=false' in sources[label], f'{label} 没有关闭无用的 deb-src 索引下载')
    require('APT/dpkg 锁等待超过 10 秒' in sources['subscription center'], '订阅中心没有明确的 10 秒锁超时错误')


def test_manager_entrypoint_and_bootstrap_command():
    host = read('core-src/host.sh')
    bootstrap = read('core-src/bootstrap.sh')
    readme = read('README.md')
    production = '\n'.join((host, read('core-src/landing.sh'), read('core-src/center_install.sh'), read('core-src/sub_center.py')))
    require('cat > /usr/local/sbin/vps' not in host, '中转管理器仍会覆盖统一 vps 首页入口')
    require('exec /usr/local/lib/vvv/vvv_manager.sh "$@"' in bootstrap, '统一 vps 首页入口没有指向 vvv_manager.sh')
    require('vvv-host-original' not in bootstrap, '统一安装仍保存会误导的中转 vps 包装器')
    require('command -v curl >/dev/null 2>&1 || {' in readme, '固定安装命令没有处理 curl 缺失')
    require('DPkg::Lock::Timeout=10' in readme, 'curl 自举安装没有 10 秒 APT 锁上限')
    for token in ('v2rayNG', 'v2rayng'):
        require(token not in production, f'生产脚本仍保留已弃用客户端：{token}')


def test_hy2_leaf_certificate():
    host = read('core-src/host.sh')
    for token in ('basicConstraints=critical,CA:FALSE', 'keyUsage=critical,digitalSignature', 'extendedKeyUsage=serverAuth'):
        require(token in host, f'HY2 证书缺少叶子证书约束：{token}')


def test_debian13_only():
    sources = {
        'network installer': read('vvv-install.sh'),
        'unified bootstrap': read('core-src/bootstrap.sh'),
        'host installer': read('core-src/host.sh'),
        'landing installer': read('core-src/landing.sh'),
    }
    for label, source in sources.items():
        require('Debian 13' in source, f'{label} 没有明确限制 Debian 13')
    require("${ID:-}" in sources['network installer'] and "${VERSION_ID:-}" in sources['network installer'], '网络入口没有读取系统版本')
    require("${ID:-}" in sources['unified bootstrap'] and "${VERSION_ID:-}" in sources['unified bootstrap'], '统一入口没有再次验证系统版本')
    landing = sources['landing installer']
    for token in ('Debian 12', 'Alpine', 'alpine', 'OpenRC', 'openrc', 'rc-service', 'rc-update', '/etc/init.d', 'apk add', 'apk update', 'apk upgrade'):
        require(token not in landing, f'落地脚本仍保留旧系统兼容逻辑：{token}')
    readme = read('README.md')
    require('仅支持 Debian 13' in readme, 'README 没有说明仅支持 Debian 13')
    require('Debian 12' in readme and '不包含' in readme, 'README 没有说明移除 Debian 12 兼容')


def test_no_obsolete_role_terms():
    files = [
        'core-src/bootstrap.sh', 'core-src/center_install.sh', 'core-src/register_sync.sh',
        'core-src/sync_agent.py', 'core-src/vvv_manager.sh', 'core-src/sub_center.py',
        'core-src/backup_manager.py', 'core-src/rclone_manager.sh', 'src/prepare.py',
    ]
    text = '\n'.join(read(path) for path in files)
    for token in ('日本A', '日本B', '备用订阅中心', '双主机互备'):
        require(token not in text, f'生产源码仍含旧角色概念：{token}')


def main():
    tests = [
        test_menu_and_front_loaded_parameters,
        test_subscription_renderers,
        test_backup_policy,
        test_jpr3_and_slot_architecture,
        test_no_qr_output,
        test_https_and_reentrant_installation,
        test_apt_lock_policy,
        test_manager_entrypoint_and_bootstrap_command,
        test_hy2_leaf_certificate,
        test_debian13_only,
        test_no_obsolete_role_terms,
    ]
    for test in tests:
        test()
        print(f'PASS {test.__name__}')
    print('ALL CONFORMANCE TESTS PASSED')


if __name__ == '__main__':
    main()

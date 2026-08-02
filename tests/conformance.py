#!/usr/bin/env python3
import base64
import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / 'core-src'
sys.path.insert(0, str(CORE))


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def load_sub_center():
    return load_module('vvv_sub_center', CORE / 'sub_center.py')


def load_adapters():
    return load_module('vvv_client_adapters', CORE / 'client_adapters.py')


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


def test_menu_and_front_loaded_parameters():
    text = read('core-src/bootstrap.sh')
    labels = [
        '1. 安装订阅中心 + 中转主机 + 自身代理',
        '2. 安装订阅中心 + 自身代理',
        '3. 安装中转主机 + 自身代理',
        '4. 安装中转副机',
        '5. 安装直连代理',
        '0. 退出',
    ]
    positions = [text.index(label) for label in labels]
    require(positions == sorted(positions), '初始菜单顺序不正确')
    for token in (
        '请选择订阅传输方式',
        '1. 直接 HTTPS',
        '2. 直接 HTTP',
        '3. 固定 HTTPS 域名（Cloudflare Tunnel）',
        '请输入订阅地址后缀',
        '随机生成 8 位',
        '6-32 位',
        'VVV_SUB_TRANSPORT',
        'VVV_SUB_SUFFIX',
        'VVV_CF_TUNNEL_TOKEN',
        '========== 安装参数总览 ==========',
        '直接开始全自动安装',
    ):
        require(token in text, f'统一前置参数缺少：{token}')
    require('安装中转副机（通过主机代理）' not in text, '菜单仍保留多余说明')
    require('read -r' not in text[text.rindex('show_parameter_summary'):text.index('case "$choice" in', text.rindex('show_parameter_summary'))], '参数总览后仍询问输入')


def test_direct_address_registration():
    bootstrap = read('core-src/bootstrap.sh')
    register = read('core-src/register_sync.sh')
    sync = read('core-src/sync_agent.py')
    center = read('core-src/sub_center.py')
    require('ask_center_address' in bootstrap, '直连安装缺少订阅中心地址询问')
    require('register-direct "$center_address"' in register, '直连地址没有传给客户端')
    for token in ('center_candidates', "format_base('https'", "format_base('http'", 'https_upgrade_base', "'https_pinned'"):
        require(token in sync, f'HTTP/HTTPS注册或自动升级缺少：{token}')
    for token in ("path == '/api/v1/register-direct'", "role != 'direct'", 'Source IP mismatch', 'CF-Connecting-IP'):
        require(token in center, f'直连自动注册缺少：{token}')
    require('订阅中心注册成功' in register and '\x1b[32m' in register, 'SSH没有绿色注册成功提示')
    for token in ('require_registration_success', "'registered'", "'subscription_refreshed'", 'snapshot_payload'):
        require(token in sync, f'客户端没有验证注册刷新：{token}')


def test_registration_refresh_contract():
    module = load_sub_center()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        module.HOSTS = root / 'hosts'
        module.OUT = root / 'output'
        module.CFG = root / 'config.json'
        module.HOSTS.mkdir(parents=True)
        module.OUT.mkdir(parents=True)
        module.CFG.write_text('{"base_url":"http://127.0.0.1:8443","subscription_url":"http://127.0.0.1:8443/Abc12345","transport_mode":"direct-http"}', encoding='utf-8')
        calls = []
        module.regenerate = lambda: calls.append('refresh') or 2
        entry = {'host_id': 'confirmed-host-001', 'token': 'host-token', 'role': 'direct'}
        result = module.finalize_registration(entry, {'state': sample_host_state(), 'meta': {'hostname': 'direct-node'}})
        require(calls == ['refresh'], '注册没有先刷新订阅')
        require(result.get('registered') is True and result.get('subscription_refreshed') is True, '注册响应缺少成功标识')
        require(result.get('canonical_base_url') == 'http://127.0.0.1:8443', '注册响应缺少规范中心地址')
        saved = module.read_json(module.HOSTS / 'confirmed-host-001.json', {})
        require(saved.get('state', {}).get('public_ip') == '198.51.100.10', '注册未保存首份状态')


def test_subscription_renderers():
    center = load_sub_center()
    adapters = load_adapters()
    adapters.smoke_test()
    nodes = center.nodes_from_host({'host_id': 'audit-host-001', 'role': 'center-relay', 'state': sample_host_state()})
    require({node['protocol'] for node in nodes} == {'vless', 'hysteria2'}, '双协议节点不完整')
    clash = adapters.render('clash', nodes)
    qx = adapters.render('quantumultx', nodes)
    loon = adapters.render('loon', nodes)
    shadowrocket = base64.b64decode(adapters.render('shadowrocket', nodes)).decode('utf-8')
    require('type: vless' in clash and 'type: hysteria2' in clash, 'Clash输出缺少协议')
    require('vless=' in qx and 'hysteria' not in qx.lower(), 'Quantumult X应只输出VLESS')
    require('salamander-password=salamander-secret' in loon, 'Loon Salamander格式错误')
    require('vless://' in shadowrocket and 'hysteria2://' in shadowrocket, 'Shadowrocket输出不完整')
    require(adapters.detect_client({'User-Agent': 'Clash-Verge-Rev/2'})['format'] == 'clash', '无法识别Clash Verge Rev')
    require(adapters.detect_client({'User-Agent': 'Quantumult X/1.5'})['format'] == 'quantumultx', '无法识别Quantumult X')
    require(adapters.detect_client({'User-Agent': 'Loon/3.2'})['format'] == 'loon', '无法识别Loon')
    require(adapters.detect_client({'User-Agent': 'Shadowrocket/2.2'})['format'] == 'shadowrocket', '无法识别Shadowrocket')
    source = read('core-src/sub_center.py')
    require('SHORT_PATHS' not in source and "'/r/'" not in source, '仍保留旧四路径订阅入口')
    require('format=' not in source and "query.get('format')" not in source, '仍保留格式诊断参数')
    for token in ('subscription_suffix', 'detect_client', '415', 'X-VVV-Client', 'X-VVV-Format', 'DEBUG_FLAG', 'DEBUG_LOG'):
        require(token in source, f'统一入口或请求头调试缺少：{token}')


def test_backup_policy():
    backup = read('core-src/backup_manager.py')
    rclone = read('core-src/rclone_manager.sh')
    for token in ('/etc/letsencrypt', '/var/lib/caddy/.local/share/caddy', '/etc/caddy', 'cloudflared.token', 'vvv-cloudflared.service'):
        require(token in backup, f'云备份未包含证书或Tunnel数据：{token}')
    require('cloud_backup_enabled' in backup and 'CLOUD_ONLY_SOURCES' in backup, '证书/Tunnel数据没有只随云备份启用')
    require("'copyto'" in backup and "'sync'" not in backup, '云上传必须使用copyto')
    require('-aes-256-cbc' in backup and '-pbkdf2' in backup, '备份未使用强加密')
    for token in ('before-cloud-backup-enabled', 'after-cloud-backup-enabled'):
        require(token in rclone, f'云备份事务事件缺少：{token}')


def test_jpr3_and_slot_architecture():
    prepare = read('src/prepare.py')
    host = read('core-src/host.sh')
    landing = read('core-src/landing.sh')
    bootstrap = read('core-src/bootstrap.sh')
    for token in ('build_vless_slot_configs', 'vvv-vless-slot@', 'build_hy2_slot_configs', 'vvv-hy2-slot@'):
        require(token in prepare, f'槽位架构缺少：{token}')
    require('zlib.compress(raw,9)' in host and 'len(key) >= 3500' in host, '主机没有生成压缩JPR3')
    require('zlib.decompress(transferred)' in landing and "transferred.startswith(b'{')" in landing, '落地端没有兼容新旧JPR3')
    require('对接密钥已达到终端单行输入上限' in bootstrap, '安装器未识别终端截断')
    require('新建 VPS 副机中转线路' in host, '中转线路菜单名称不正确')


def test_no_qr_output():
    files = ['vvv-install.sh', 'core-src/bootstrap.sh', 'core-src/host.sh', 'core-src/landing.sh', 'core-src/center_install.sh', 'core-src/center_manager.sh']
    text = '\n'.join(read(path) for path in files)
    require('qrencode' not in text and 'qr_helper' not in text, '仍保留二维码实现')
    require('二维码' not in text, '生产脚本仍保留二维码提示')


def test_transports_and_reentrant_installation():
    installer = read('vvv-install.sh')
    bootstrap = read('core-src/bootstrap.sh')
    center = read('core-src/center_install.sh')
    transport = read('core-src/center_transport.sh')
    manager = read('core-src/center_manager.sh')
    require('当前版本只支持全新安装' not in installer, '网络入口仍拒绝重复运行')
    require('始终进入安装菜单' in installer, '网络入口没有承诺重复运行')
    for file in ('client_adapters.py', 'adapter_manager.py', 'center_transport.sh', 'center_manager.sh'):
        require(file in installer, f'安装入口没有下载新模块：{file}')
    for token in ('direct-http', 'direct-https', 'tunnel', 'subscription_suffix', 'subscription_url'):
        require(token in center and token in transport, f'传输架构缺少：{token}')
    require('Cloudflare Tunnel模式必须输入 Tunnel Token' in center, 'Tunnel缺少Token校验')
    require('http://127.0.0.1:${port}' in transport, 'Tunnel源站不是本地HTTP')
    require('HTTPS 已开启；原 HTTP 订阅入口已失效' in transport, 'HTTP升级HTTPS没有强制失效旧入口')
    require('原 HTTP 配置已恢复并继续可用' in transport, 'HTTPS失败没有事务回滚')
    require('客户端请求头识别调试' in manager and '更新客户端适配器' in manager, '订阅中心菜单缺少调试或适配器更新')
    require('修改订阅地址后缀' in manager and '开启 HTTPS 传输' in manager, '订阅中心菜单缺少后期管理')
    require('6-32位大小写字母或数字' in manager, '自定义后缀长度规则错误')
    require('随机生成 8 位' in bootstrap, '默认随机后缀不是8位')
    require('refresh_center_runtime_code' in bootstrap and 'center_manager.sh' in bootstrap, '重复安装不会刷新中心管理器')
    require('migrate_center_config_if_needed' in bootstrap and 'config.schema2-backup.json' in bootstrap, '旧schema2订阅中心不会原地迁移')


def test_apt_lock_policy():
    sources = {
        'network installer': read('vvv-install.sh'),
        'host installer': read('core-src/host.sh'),
        'landing installer': read('core-src/landing.sh'),
        'subscription center': read('core-src/center_install.sh'),
        'rclone manager': read('core-src/rclone_manager.sh'),
    }
    for label, source in sources.items():
        require('DPkg::Lock::Timeout=600' not in source, f'{label}仍等待APT锁600秒')
        require('DPkg::Lock::Timeout=10' in source, f'{label}没有10秒APT锁上限')
        require('Acquire::IndexTargets::deb-src::Sources::DefaultEnabled=false' in source, f'{label}没有关闭deb-src索引')


def test_manager_entrypoint_and_bootstrap_command():
    host = read('core-src/host.sh')
    bootstrap = read('core-src/bootstrap.sh')
    readme = read('README.md')
    require('cat > /usr/local/sbin/vps' not in host, '中转管理器仍覆盖统一vps入口')
    require('exec /usr/local/lib/vvv/vvv_manager.sh "$@"' in bootstrap, '统一vps入口错误')
    require('command -v curl >/dev/null 2>&1 || {' in readme, '固定安装命令未处理curl缺失')
    require('DPkg::Lock::Timeout=10' in readme, '固定安装命令缺少APT锁上限')


def test_hy2_leaf_certificate():
    host = read('core-src/host.sh')
    for token in ('basicConstraints=critical,CA:FALSE', 'keyUsage=critical,digitalSignature', 'extendedKeyUsage=serverAuth'):
        require(token in host, f'HY2证书缺少约束：{token}')


def test_debian13_only():
    sources = [read('vvv-install.sh'), read('core-src/bootstrap.sh'), read('core-src/host.sh'), read('core-src/landing.sh')]
    require(all('Debian 13' in source for source in sources), '有安装入口没有限制Debian 13')
    readme = read('README.md')
    require('仅支持 Debian 13' in readme and 'Debian 12' in readme, 'README系统要求不完整')


def test_no_obsolete_role_terms():
    files = ['core-src/bootstrap.sh', 'core-src/center_install.sh', 'core-src/register_sync.sh', 'core-src/sync_agent.py', 'core-src/vvv_manager.sh', 'core-src/sub_center.py', 'core-src/backup_manager.py', 'core-src/rclone_manager.sh', 'src/prepare.py']
    text = '\n'.join(read(path) for path in files)
    for token in ('日本A', '日本B', '备用订阅中心', '双主机互备', 'v2rayNG', 'v2rayng'):
        require(token not in text, f'生产源码仍含废弃概念：{token}')


def main():
    tests = [
        test_menu_and_front_loaded_parameters,
        test_direct_address_registration,
        test_registration_refresh_contract,
        test_subscription_renderers,
        test_backup_policy,
        test_jpr3_and_slot_architecture,
        test_no_qr_output,
        test_transports_and_reentrant_installation,
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

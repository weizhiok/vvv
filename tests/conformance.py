#!/usr/bin/env python3
import base64
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / 'core-src'
sys.path.insert(0, str(CORE))


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


def require(value, message):
    if not value:
        raise AssertionError(message)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def sample_state():
    return {
        'schema': 4, 'role': 'japan-hub', 'protocol_mode': 'dual',
        'public_ip': '198.51.100.10', 'listen_port': 443, 'sni': 'www.softbank.jp',
        'hy2_limit_mbps': 65, 'direct_base_name': 'JP-198.51.100.10:443',
        'port_hopping': {'enabled': True, 'ports': '443,20000-50000', 'hop_interval_seconds': 30},
        'vless': {'reality': {'public_key': 'pub', 'short_id': '0123456789abcdef'},
                  'direct_user': {'uuid': '11111111-1111-4111-8111-111111111111'}},
        'hy2': {'server_name': 'jp-hy2.local', 'obfs_password': 'obfs', 'certificate_pin_hex': 'aa' * 32,
                'direct_user': {'password': 'password'}},
        'relays': [], 'upstream_relays': [], 'temporary_nodes': [],
    }


def test_install_menu_and_upfront_parameters():
    text = read('core-src/bootstrap.sh')
    labels = [
        '1. 安装订阅中心 + 中转主机 + 自身代理', '2. 安装订阅中心 + 自身代理',
        '3. 安装中转主机 + 自身代理', '4. 安装中转副机 + 自身代理',
        '5. 安装中转副机', '6. 安装直连代理', '7. 从云备份恢复', '0. 退出',
    ]
    positions = [text.index(label) for label in labels]
    require(positions == sorted(positions), '初始菜单顺序错误')
    for token in ('安装参数（全部前置设置）', 'Hysteria 2 每连接服务器强制限速',
                  '10#$input>=30', '10#$input<=100', '请输入订阅中心对接码（支持 VVC1 或含注册票据的 JPR3；按回车跳过）',
                  '参数已收集完毕，开始全自动安装',
                  '1. 使用 HTTPS【默认】', '2. 使用 HTTP', '3. 使用 Cloudflare Tunnel'):
        require(token in text, f'缺少前置参数功能：{token}')
    summary = text.index('show_parameter_summary')
    execute = text.index('case "$choice" in', summary)
    require('read -r' not in text[summary:execute], '参数总览后仍有输入')
    require('schema == 2 || "$schema" == 3' in text or '[[ "$schema" == 2 || "$schema" == 3 ]]' in text, '现有 schema 3 订阅中心不会无损迁移')
    for obsolete in ('1. 直接 HTTPS【默认】', '域名由 Caddy 自动申请公共证书',
                     '2. 直接 HTTP', '固定 HTTPS 域名（Cloudflare Tunnel）'):
        require(obsolete not in text, f'订阅传输菜单仍包含旧说明：{obsolete}')


def test_vvc1_ip_only_contract():
    sync = load('sync_agent_test', CORE / 'sync_agent.py')
    payload = {'schema': 1, 'type': 'vvv-subscription-center',
               'api_base_url': 'http://198.51.100.10:18081', 'master_token': 'master'}
    code = sync.encode_vvc1(payload)
    require(sync.decode_vvc1(code) == payload, 'VVC1 编解码不一致')
    try:
        sync.decode_vvc1(sync.encode_vvc1({**payload, 'api_base_url': 'http://sub.example.com:18081'}))
    except ValueError as exc:
        require('不能使用域名' in str(exc), 'VVC1 域名错误提示不明确')
    else:
        raise AssertionError('VVC1 仍允许域名 API')
    try:
        sync.decode_vvc1('JPR3.abc.01234567890123456789')
    except ValueError as exc:
        require('JPR3' in str(exc), 'JPR3/VVC1 类型隔离失败')
    source = read('core-src/sub_center.py') + read('core-src/sync_agent.py')
    require('/api/v1/register-direct' not in source, '仍保留无鉴权直连注册')
    for token in ('center_candidates', 'https_pinned', 'https_upgrade_base'):
        require(token not in source, f'仍保留 HTTP/HTTPS 猜测逻辑：{token}')


def test_transports_and_management():
    transport = read('core-src/center_transport.sh')
    manager = read('core-src/center_manager.sh')
    for token in ('change-suffix', 'change-domain', 'change-port', 'change-tunnel-token', 'switch-secure'):
        require(token in transport, f'传输管理缺少：{token}')
    for label in ('修改订阅后缀', '修改订阅域名', '修改订阅端口', '修改 Tunnel Token', '切换 HTTPS/Tunnel 模式'):
        require(label in manager, f'订阅中心菜单缺少：{label}')
    require('enable-https' not in transport and '开启 HTTPS 传输' not in manager, '仍保留 HTTP 自动升级 HTTPS')
    require('只有直接 HTTPS 可以切换到 Tunnel' in transport and '只有 Tunnel 可以切换到直接 HTTPS' in transport,
            '安全模式切换边界不完整')
    require("api=\"http://$(value '.public_ip'):$(value '.listen_port')\"" in transport, '副机 API 不是固定 IP 地址')
    for token in (
        '2>"$error_log"', 'next_progress=10', 'elapsed >= next_progress',
        '正在等待 HTTPS 证书和统一订阅入口就绪',
        'HTTPS 证书和统一订阅入口已就绪，共等待',
        '最近一次 curl 错误', 'journalctl -u caddy.service -n 80 --no-pager',
    ):
        require(token in transport, f'HTTPS 就绪等待输出缺少：{token}')
    require('check_public_once && return 0' not in transport, '健康检查仍直接显示中间 curl 错误')
    require('attempt % 10' not in transport, '健康检查仍使用旧的 20 秒进度输出')


def test_hy2_server_hard_limit():
    host = read('core-src/host.sh')
    landing = read('core-src/landing.sh')
    prepare = read('src/prepare.py')
    for source, label in ((host, '主机'), (landing, '副机'), (prepare, '最终生成器')):
        require(('ignore_client_bandwidth":False' in source or 'ignore_client_bandwidth": false' in source), f'{label} HY2 未保持无带宽客户端兼容')
        require('up_mbps' in source and 'down_mbps' in source, f'{label} HY2 缺少服务端限速')
    require('hy2_limit_mbps' in host and 'hy2_limit_mbps' in landing, 'HY2 限速没有进入状态/JPR3')
    adapter = read('core-src/client_adapters.py')
    require('CLIENT_UP_MBPS = 30' in adapter and 'CLIENT_DOWN_MBPS = 50' in adapter, '客户端上下行带宽没有独立设置为 30/50 Mbps')
    require("node.get('limit_mbps')" not in adapter, '客户端模板仍错误复用服务器硬限速')


def test_temporary_nodes_are_local_copies_only():
    host = read('core-src/host.sh')
    prepare = read('src/prepare.py')
    for label in ('创建临时 VPS 中转线路（从已有线路复制）', '创建临时 HTTP/HTTPS/SOCKS5 中转线路（从已有线路复制）'):
        require(label in host, f'临时菜单缺少：{label}')
    require('从已有正式线路复制' in host, '临时线路没有只允许复制')
    require('临时线路全新创建' not in host and '全新创建临时' not in host, '仍保留临时线路全新创建')
    require('副机和原正式线路均未修改' in host, '临时线路未明确隔离副机')
    for token in ('temporary_nodes', 'source_type', 'source_id', 'retired=True', 'vvv-temp-cleanup.timer'):
        require(token in host, f'临时线路生命周期缺少：{token}')
    require("relay=relays.get(temp.get('source_id'))" in prepare, '临时 VPS 没有复用正式线路出口')
    require("source_id=temps[assigned].get('source_id')" in prepare and 'source_id in upstreams' in prepare, '临时上游没有复用正式线路出口')


def test_config_only_backup_and_restore():
    backup = read('core-src/backup_manager.py')
    restore = read('core-src/restore_manager.py')
    for token in ('MAX_COUNT = 100', 'MAX_BYTES = 1024 ** 3', "REMOTE_ROOT = 'vvv'", 'RecoverKey.ini', 'BackupIndex.json'):
        require(token in backup, f'备份策略缺少：{token}')
    for forbidden in ('/usr/local/bin/xray', '/usr/local/bin/sing-box', '/usr/local/bin/caddy', '/usr/local/bin/cloudflared'):
        require(forbidden not in backup, f'备份错误包含二进制：{forbidden}')
    require("'config_only': True" in backup and "'temporary_nodes_included': False" in backup, '纯配置/排除临时节点标记缺失')
    require("obj['state']['temporary_nodes'] = []" in backup, '订阅中心主机快照中的临时节点未剔除')
    require("obj['temporary_nodes'] = []" in restore, '恢复时没有再次清除临时节点')
    require('请输入编号 [默认 1，恢复最新备份]' in restore, '恢复日期选择和默认最新缺失')
    require('自动尝试上一份' in restore, '最新备份损坏没有回退')


def test_node_names_and_clients():
    center = load('sub_center_test', CORE / 'sub_center.py')
    adapters = load('adapters_test', CORE / 'client_adapters.py')
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        center.CFG = root / 'config.json'; center.DATA = root; center.HOSTS = root / 'hosts'; center.OUT = root / 'out'
        center.REGISTRY = root / 'registry.json'; center.OVERRIDES = root / 'overrides.json'; center.ORDER = root / 'node-order.json'; center.BACKUP = root / 'missing.py'
        center.HOSTS.mkdir(); center.CFG.write_text('{}'); center.REGISTRY.write_text('{"hosts":[]}'); center.OVERRIDES.write_text('{}')
        doc = {'host_id': 'host-00000001', 'role': 'center-relay', 'state': sample_state()}
        center.atomic_json(center.HOSTS / 'host-00000001.json', doc)
        nodes = center.all_nodes(); node = nodes[0]
        center.rename_node(node['id'], '我的主节点')
        require(center.all_nodes()[0]['name'] == '我的主节点', '节点改名未持久化')
        center.reset_name(node['id'])
        require(center.all_nodes()[0]['name'] != '我的主节点', '恢复默认名称失败')
        recognition = adapters.detect_client({'User-Agent': 'NekoBox/Android/1.4.2 (Prefer ClashMeta Format)'})
        require(recognition and recognition['name'] == 'NekoBoxForAndroid' and recognition['format'] == 'nekobox',
                'NekoBoxForAndroid 1.4.2 请求头未被识别')
        rendered = adapters.render('clash', center.all_nodes())
        nekobox = json.loads(adapters.render('nekobox', center.all_nodes()))
        require(rendered.startswith('proxies:\n') and isinstance(nekobox.get('outbounds'), list),
                'Clash YAML 或 NekoBox sing-box JSON 格式错误')
        require('proxy-groups:' not in rendered and 'rules:' not in rendered,
                'Clash 节点订阅仍包含策略组或规则')
        require('up: "30 Mbps"' in rendered and 'down: "50 Mbps"' in rendered,
                'Clash 客户端带宽不是 30/50 Mbps')
        require('ports: "443,20000-50000"' in rendered and 'hop-interval: "20-30"' in rendered,
                'Mihomo 客户端模板缺少随机 HY2 端口跳跃')
        hy2_outbound = next(item for item in nekobox['outbounds'] if item['type'] == 'hysteria2')
        require(hy2_outbound['server_ports'] == ['443', '20000:50000'] and
                hy2_outbound['hop_interval'] == '30s' and hy2_outbound['up_mbps'] == 30 and
                hy2_outbound['down_mbps'] == 50,
                'NekoBox sing-box 出站缺少固定 30 秒、端口跳跃或 30/50 Mbps')
        vless_outbound = next(item for item in nekobox['outbounds'] if item['type'] == 'vless')
        require(vless_outbound['flow'] == 'xtls-rprx-vision' and
                vless_outbound['tls']['reality']['enabled'] is True,
                'NekoBox sing-box 出站缺少 VLESS Reality')
        require(adapters.render('nekobox-yaml', center.all_nodes()).startswith('proxies:\n'),
                '本机隐藏 NekoBox YAML 输出丢失')
        loon = adapters.render('loon', center.all_nodes())
        require('server-ports="443,20000-50000"' in loon and 'hop-interval=30' in loon and
                'block-quic=true' in loon and 'download-bandwidth=50' in loon,
                'Loon 客户端模板缺少 HY2 端口跳跃或下载带宽')
        shadow = base64.b64decode(adapters.render('shadowrocket', center.all_nodes())).decode()
        require('vless://' in shadow and 'hysteria2://' in shadow, '客户端订阅渲染不完整')
        for field in ('peer=', 'fastopen=1', 'upmbps=30', 'downmbps=50', 'mport=443,20000-50000'):
            require(field in shadow, f'Shadowrocket HY2 缺少参数：{field}')
    host = read('core-src/host.sh')
    landing = read('core-src/landing.sh')
    adapter = read('core-src/client_adapters.py')
    package = read('core-src/client_package_renderer.py')
    bootstrap = read('core-src/bootstrap.sh')
    require('NekoBoxForAndroid-SN.txt' in adapter and "'nekobox-sn'" in adapter,
            '本地配置缺少 NekoBox SN LINK')
    require("'filename': 'NekoBoxForAndroid.yaml'" in adapter and "'format': 'nekobox-yaml'" in adapter,
            '本机隐藏 NekoBox YAML 渲染器缺失')
    require("'name': 'NekoBoxForAndroid', 'format': 'nekobox'" in adapter and
            'application/json; charset=utf-8' in adapter and 'render_nekobox_subscription' in adapter,
            '订阅中心 NekoBox 没有下发 sing-box JSON')
    display_tokens = [
        "'display_name': 'Quantumult X'", "'display_name': 'Loon'",
        "'display_name': 'Shadowrocket 分享链接'", "'display_name': 'NekoBox For Android'",
        "'display_name': 'Clash Verge Rev / Mihomo'",
    ]
    display_positions = [adapter.index(token) for token in display_tokens]
    require(display_positions == sorted(display_positions), '本机客户端显示顺序错误')
    require('Loon-Shadowrocket.txt' in package and 'NekoBoxForAndroid.yaml' not in package,
            '统一渲染器错误清理 NekoBox YAML')
    require('client_package_renderer.py' in host and 'client_package_renderer.py' in bootstrap,
            '主机或安装器没有接入统一客户端渲染器')
    require('generate_client_files' in landing and 'CLIENT_PACKAGE_RENDERER' in landing,
            '中转副机没有接入统一客户端渲染器')
    require('组合角色只允许在全新系统安装' in bootstrap and '中转副机只允许在全新系统安装' in bootstrap,
            '全新安装角色边界不完整')


def test_landing_and_direct_ip_change():
    landing = read('core-src/landing.sh')
    manager = read('core-src/vvv_manager.sh')
    sync = read('core-src/sync_agent.py')
    for token in ('修改主机 IP 地址', 'update_landing_ip.py', "outbound.get('tag') == 'back-to-japan'", 'update-center-ip'):
        require(token in landing, f'中转副机修改主机 IP 缺少：{token}')
    require('修改订阅中心 IP 地址' in manager and 'update-center-ip' in sync, '直连副机缺少修改中心 IP')
    probe = read('core-src/node_probe.py')
    require('generic_probe' in probe and 'curl_socks' in probe and '真实连接成功' in probe, '节点检测器不完整')


def test_embedded_python_heredocs():
    validator = load('embedded_python_validator_test', ROOT / 'src' / 'validate_embedded_python.py')
    shell_files = [
        ROOT / 'core-src' / name for name in (
            'bootstrap.sh', 'host.sh', 'landing.sh', 'center_install.sh',
            'register_sync.sh', 'vvv_manager.sh', 'rclone_manager.sh',
            'center_transport.sh', 'center_manager.sh',
        )
    ]
    count = validator.validate_paths(shell_files)
    require(count >= 1, '没有验证任何 Shell 内嵌 Python')
    bootstrap = read('core-src/bootstrap.sh')
    require('print(file=f)' in bootstrap, '角色 JSON 写入仍依赖易损坏的反斜杠换行')


def test_installer_and_diagnostics():
    installer = read('vvv-install.sh')
    validation = read('tests/final_runtime_validation.sh')
    for name in ('restore_manager.py', 'diagnostic_report.py', 'node_probe.py', 'validate_embedded_python.py'):
        require(name in installer, f'安装器没有下载：{name}')
    require('Shell 内嵌 Python 语法检查失败' in installer, '安装器没有在执行前检查 heredoc Python')
    diag = read('core-src/diagnostic_report.py')
    for token in ('VVV-诊断报告', 'SENSITIVE_KEYS', '最近错误日志', '云备份目录', 'vvv-temp-cleanup.timer'):
        require(token in diag, f'诊断报告缺少：{token}')
    require(all(name in validation for name in ('restore_manager.py','diagnostic_report.py','node_probe.py')), '最终验证没有覆盖新增 Python 模块')


def test_no_qr_and_debian13():
    files = ['vvv-install.sh','core-src/bootstrap.sh','core-src/host.sh','core-src/landing.sh','core-src/center_install.sh','core-src/center_manager.sh']
    text = '\n'.join(read(path) for path in files)
    require('qrencode' not in text and 'qr_helper' not in text, '仍保留二维码实现')
    require(all('Debian 13' in read(path) for path in ('vvv-install.sh','core-src/bootstrap.sh','core-src/host.sh','core-src/landing.sh')), '系统限制不是 Debian 13')


def main():
    tests = [
        test_install_menu_and_upfront_parameters, test_vvc1_ip_only_contract,
        test_transports_and_management, test_hy2_server_hard_limit,
        test_temporary_nodes_are_local_copies_only, test_config_only_backup_and_restore,
        test_node_names_and_clients, test_landing_and_direct_ip_change,
        test_embedded_python_heredocs, test_installer_and_diagnostics, test_no_qr_and_debian13,
    ]
    for test in tests:
        test(); print('PASS', test.__name__)
    print('ALL CONFORMANCE TESTS PASSED')


if __name__ == '__main__':
    main()

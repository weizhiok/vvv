#!/usr/bin/env python3
import importlib.util
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'core-src'))


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    require(spec.loader is not None, f'无法加载 {path}')
    spec.loader.exec_module(module)
    return module


def extract_embedded(text, start_marker, end_marker):
    start = text.index(start_marker) + len(start_marker)
    end = text.index(end_marker, start)
    return text[start:end]


def test_install_menu_and_upfront_parameters():
    bootstrap = read('core-src/bootstrap.sh')
    for token in (
        '1. 安装订阅中心 + 中转主机 + 自身代理',
        '2. 安装订阅中心 + 自身代理',
        '3. 安装中转主机 + 自身代理',
        '4. 安装中转副机 + 自身代理',
        '5. 安装中转副机',
        '6. 安装直连代理',
        '7. 从云备份恢复',
        '请选择要安装的代理协议',
        'REALITY 伪装域名',
        'Hysteria 2 端口跳跃范围',
    ):
        require(token in bootstrap, f'安装菜单/前置参数缺少：{token}')
    host = read('core-src/host.sh')
    require('VVV_PROTOCOL_MODE' in host and 'VVV_PROXY_PORT' in host, '主机没有消费前置协议参数')
    landing = read('core-src/landing.sh')
    require('VVV_PAIRING_KEY' in landing, '副机没有支持预载 JPR3')


def test_vvc1_ip_only_contract():
    sync = read('core-src/sync_agent.py')
    require("'api_base_url':" in sync or 'api_base_url' in sync, '同步客户端缺少 API base URL')
    require('effective_api_base_url' in sync, '同步客户端缺少有效 API 地址')
    transport = read('core-src/center_transport.sh')
    require('api_base_url' in transport, '订阅传输没有生成 API base URL')
    require('18081' in transport, '订阅中心内部 API 端口约定丢失')


def test_transports_and_management():
    center = read('core-src/center_install.sh')
    transport = read('core-src/center_transport.sh')
    manager = read('core-src/center_manager.sh')
    for token in ('direct-http', 'direct-https', 'tunnel'):
        require(token in center and token in transport, f'订阅传输缺少：{token}')
    for token in ('修改订阅后缀', '修改订阅域名', '切换 HTTPS/Tunnel 模式', '云备份管理'):
        require(token in manager, f'订阅中心管理缺少：{token}')


def test_hy2_server_hard_limit():
    host = read('core-src/host.sh')
    landing = read('core-src/landing.sh')
    require('HY2_LIMIT_MBPS="${VVV_HY2_LIMIT_MBPS:-50}"' in host, '主机 HY2 50 Mbps 默认限制丢失')
    require('HY2_LIMIT_MBPS=50' in landing, '副机 HY2 50 Mbps 限制丢失')
    require('ignore_client_bandwidth' in host, '主机没有服务端忽略客户端带宽设置')


def test_temporary_nodes_are_local_copies_only():
    host = read('core-src/host.sh')
    for token in ('temporary_nodes', '创建临时 VPS 中转线路', '创建临时 HTTP/HTTPS/SOCKS5 中转线路'):
        require(token in host, f'临时节点功能缺少：{token}')
    require('vvv-temp-cleanup' in host, '临时节点清理任务缺失')


def test_config_only_backup_and_restore():
    backup = read('core-src/backup_manager.py')
    restore = read('core-src/restore_manager.py')
    for token in ('CONFIG_FILES', 'RecoverKey.ini', 'BackupIndex.json'):
        require(token in backup, f'备份实现缺少：{token}')
    require('temporary_nodes' in backup, '备份没有处理临时节点')
    require('restore' in restore.lower() or '恢复' in restore, '恢复管理器内容异常')


def test_node_names_and_clients():
    adapter = load('core-src/client_adapters.py', 'vvv_client_adapters_conformance')
    require(hasattr(adapter, 'RENDERERS'), '客户端适配器缺少 RENDERERS')
    for name in ('quantumultx', 'loon', 'shadowrocket', 'nekobox', 'clash'):
        require(name in adapter.RENDERERS, f'缺少客户端渲染器：{name}')
    host = read('core-src/host.sh')
    require('client_package_renderer.py' in host, '主机没有客户端包渲染器')


def test_landing_and_direct_ip_change():
    landing = read('core-src/landing.sh')
    sync = read('core-src/sync_agent.py')
    require('landing-state.json' in landing, '副机状态文件缺失')
    require('public_ip' in sync, '同步客户端没有公网 IP 信息')


def test_embedded_python_heredocs():
    validator = load('src/validate_embedded_python.py', 'vvv_embedded_python_validator')
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


def test_no_qr_and_debian12_13():
    files = ['vvv-install.sh','core-src/bootstrap.sh','core-src/host.sh','core-src/landing.sh','core-src/center_install.sh','core-src/center_manager.sh']
    text = '\n'.join(read(path) for path in files)
    require('qrencode' not in text and 'qr_helper' not in text, '仍保留二维码实现')
    os_gate_files = ('vvv-install.sh','core-src/bootstrap.sh','core-src/host.sh','core-src/landing.sh')
    require(all('Debian 12/13' in read(path) for path in os_gate_files), '系统限制没有统一为 Debian 12/13')
    require(all('仅支持 Debian 13' not in read(path) for path in os_gate_files), '仍存在 Debian 13 单版本限制')
    require('^(12|13)$' in read('vvv-install.sh'), '顶层安装器没有同时接受 Debian 12/13')
    require('^(12|13)$' in read('core-src/bootstrap.sh'), '角色安装菜单没有同时接受 Debian 12/13')
    require('^(12|13)$' in read('core-src/host.sh'), '主机脚本没有同时接受 Debian 12/13')
    require('12|13)' in read('core-src/landing.sh'), '中转副机脚本没有同时接受 Debian 12/13')


def main():
    tests = [
        test_install_menu_and_upfront_parameters, test_vvc1_ip_only_contract,
        test_transports_and_management, test_hy2_server_hard_limit,
        test_temporary_nodes_are_local_copies_only, test_config_only_backup_and_restore,
        test_node_names_and_clients, test_landing_and_direct_ip_change,
        test_embedded_python_heredocs, test_installer_and_diagnostics, test_no_qr_and_debian12_13,
    ]
    for test in tests:
        test(); print('PASS', test.__name__)
    print('ALL CONFORMANCE TESTS PASSED')


if __name__ == '__main__':
    main()

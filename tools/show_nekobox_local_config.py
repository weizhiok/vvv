#!/usr/bin/env python3
from pathlib import Path


def replace_once(path, old, new, label):
    file_path = Path(path)
    text = file_path.read_text(encoding='utf-8')
    if old not in text:
        raise SystemExit(f'{label}: marker not found in {path}')
    file_path.write_text(text.replace(old, new, 1), encoding='utf-8')


replace_once('core-src/client_adapters.py', 'VERSION = 5', 'VERSION = 6', 'adapter version')

replace_once(
    'core-src/client_adapters.py',
    """    {'filename': 'Clash-Verge-Rev.yaml', 'format': 'clash', 'display_name': 'Clash Verge Rev / Mihomo'},
    {'filename': 'NekoBoxForAndroid.txt', 'format': 'nekobox-import', 'display_name': 'NekoBoxForAndroid 单节点订阅'},
""",
    """    {'filename': 'Clash-Verge-Rev.yaml', 'format': 'clash', 'display_name': 'Clash Verge Rev / Mihomo'},
    {'filename': 'NekoBoxForAndroid.yaml', 'format': 'nekobox', 'display_name': 'NekoBoxForAndroid'},
    {'filename': 'NekoBoxForAndroid.txt', 'format': 'nekobox-import', 'display_name': 'NekoBoxForAndroid 单节点订阅'},
""",
    'NekoBox local YAML output',
)

replace_once(
    'core-src/client_package_renderer.py',
    "OBSOLETE_OUTPUTS = ('Loon-Shadowrocket.txt', 'NekoBoxForAndroid.yaml')",
    "OBSOLETE_OUTPUTS = ('Loon-Shadowrocket.txt',)",
    'package obsolete outputs',
)

replace_once(
    'core-src/client_local_renderer.py',
    "    obsolete = tuple(set(obsolete) | {'Loon-Shadowrocket.txt', 'NekoBoxForAndroid.yaml'})",
    "    obsolete = tuple(set(obsolete) | {'Loon-Shadowrocket.txt'})",
    'local obsolete outputs',
)

replace_once(
    'tests/test_client_port_hopping.py',
    """        assert 'hop-interval: "20-30"' in (out / 'Clash-Verge-Rev.yaml').read_text(encoding='utf-8')
        assert (out / 'NekoBoxForAndroid-基础URI.txt').read_text(encoding='utf-8').startswith('hy2://')
""",
    """        assert 'hop-interval: "20-30"' in (out / 'Clash-Verge-Rev.yaml').read_text(encoding='utf-8')
        neko_yaml_file = (out / 'NekoBoxForAndroid.yaml').read_text(encoding='utf-8')
        assert 'hop-interval: 30' in neko_yaml_file
        assert 'hop-interval: "20-30"' not in neko_yaml_file
        assert '【NekoBoxForAndroid】' in summary
        assert (out / 'NekoBoxForAndroid-基础URI.txt').read_text(encoding='utf-8').startswith('hy2://')
""",
    'port hopping NekoBox output assertions',
)

replace_once(
    'tests/client_upgrade_isolation_validation.py',
    """    neko = directory / 'NekoBoxForAndroid.txt'
    basic = directory / 'NekoBoxForAndroid-基础URI.txt'
    require(neko.is_file(), f'{role}缺少 NekoBox 单节点订阅输出')
""",
    """    neko_yaml = directory / 'NekoBoxForAndroid.yaml'
    neko = directory / 'NekoBoxForAndroid.txt'
    basic = directory / 'NekoBoxForAndroid-基础URI.txt'
    require(neko_yaml.is_file(), f'{role}缺少 NekoBox 完整 YAML 输出')
    neko_yaml_text = neko_yaml.read_text(encoding='utf-8')
    require('proxies:' in neko_yaml_text and 'hop-interval: 30' in neko_yaml_text,
            f'{role} NekoBox YAML 缺少完整节点或固定 30 秒跳跃')
    require('hop-interval: "20-30"' not in neko_yaml_text,
            f'{role} NekoBox YAML 错误复用了 Mihomo 随机跳跃')
    require(neko.is_file(), f'{role}缺少 NekoBox 单节点订阅输出')
""",
    'upgrade NekoBox YAML verification',
)

replace_once(
    'tests/client_upgrade_isolation_validation.py',
    """    require(not (directory / 'NekoBoxForAndroid.yaml').exists(), f'{role}未清理旧 NekoBox YAML')
    require(not (directory / 'Loon-Shadowrocket.txt').exists(), f'{role}未清理旧混合分享文件')
""",
    """    require(not (directory / 'Loon-Shadowrocket.txt').exists(), f'{role}未清理旧混合分享文件')
""",
    'upgrade obsolete NekoBox assertion',
)

replace_once(
    'tests/client_upgrade_isolation_validation.py',
    """        require('Shadowrocket' in (root / 'root/中转客户端节点.txt').read_text(encoding='utf-8'),
                '中转副机汇总缺少 Shadowrocket')
""",
    """        landing_summary = (root / 'root/中转客户端节点.txt').read_text(encoding='utf-8')
        require('Shadowrocket' in landing_summary and 'NekoBoxForAndroid' in landing_summary,
                '中转副机汇总缺少 Shadowrocket 或 NekoBoxForAndroid')
""",
    'landing summary NekoBox assertion',
)

replace_once(
    'tests/conformance.py',
    """    require('NekoBoxForAndroid.txt' in adapter and "'nekobox-uri'" in adapter,
            '本地配置缺少 NekoBox 独立分享链接')
    require('Loon-Shadowrocket.txt' in package and 'NekoBoxForAndroid.yaml' in package,
            '统一渲染器没有清理旧客户端输出')
""",
    """    require('NekoBoxForAndroid.txt' in adapter and "'nekobox-uri'" in adapter,
            '本地配置缺少 NekoBox 独立分享链接')
    require("'filename': 'NekoBoxForAndroid.yaml'" in adapter and "'format': 'nekobox'" in adapter,
            '本机汇总缺少 NekoBox 完整 YAML 输出')
    require('Loon-Shadowrocket.txt' in package and 'NekoBoxForAndroid.yaml' not in package,
            '统一渲染器仍把 NekoBox YAML 当作旧文件清理')
""",
    'conformance NekoBox local output contract',
)

adapter = Path('core-src/client_adapters.py').read_text(encoding='utf-8')
package = Path('core-src/client_package_renderer.py').read_text(encoding='utf-8')
local = Path('core-src/client_local_renderer.py').read_text(encoding='utf-8')
assert "'filename': 'NekoBoxForAndroid.yaml'" in adapter
assert "'format': 'nekobox'" in adapter
assert 'NekoBoxForAndroid.yaml' not in package
assert "{'Loon-Shadowrocket.txt'}" in local

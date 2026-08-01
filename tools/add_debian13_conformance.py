#!/usr/bin/env python3
from pathlib import Path

path = Path('tests/conformance.py')
text = path.read_text(encoding='utf-8')

function = r'''
def test_debian13_only():
    sources = {
        'network installer': read('vvv-install.sh'),
        'unified bootstrap': read('core-src/bootstrap.sh'),
        'host installer': read('core-src/host.sh'),
        'landing installer': read('core-src/landing.sh'),
    }
    for label, source in sources.items():
        require('Debian 13' in source, f'{label} 没有明确限制 Debian 13')
    require("${ID:-}" in sources['network installer'] and "${VERSION_ID:-}" in sources['network installer'], '网络安装入口没有读取系统版本')
    require("${ID:-}" in sources['unified bootstrap'] and "${VERSION_ID:-}" in sources['unified bootstrap'], '统一入口没有再次验证系统版本')
    landing = sources['landing installer']
    for token in ('Debian 12', 'Alpine', 'alpine', 'OpenRC', 'openrc', 'rc-service', 'rc-update', '/etc/init.d', 'apk add', 'apk update', 'apk upgrade'):
        require(token not in landing, f'落地脚本仍保留旧系统兼容逻辑：{token}')
    readme = read('README.md')
    require('仅支持全新 Debian 13' in readme, 'README 没有说明仅支持全新 Debian 13')
    require('Debian 12' in readme and '不包含' in readme, 'README 没有明确说明移除 Debian 12 兼容')
'''

anchor = '\ndef test_no_obsolete_role_terms():\n'
if function.strip() not in text:
    if text.count(anchor) != 1:
        raise SystemExit(f'Debian 13 test insertion anchor count={text.count(anchor)}')
    text = text.replace(anchor, function + anchor, 1)

list_anchor = '''        test_qr_helper,
        test_no_obsolete_role_terms,
'''
list_replacement = '''        test_qr_helper,
        test_debian13_only,
        test_no_obsolete_role_terms,
'''
if list_replacement not in text:
    if text.count(list_anchor) != 1:
        raise SystemExit(f'Debian 13 test list anchor count={text.count(list_anchor)}')
    text = text.replace(list_anchor, list_replacement, 1)

path.write_text(text, encoding='utf-8')

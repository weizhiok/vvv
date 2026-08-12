#!/usr/bin/env python3
from pathlib import Path

path = Path('core-src/client_package_renderer.py')
text = path.read_text(encoding='utf-8')
old = """    text = replace_once(
        text,
        'umask 077\\n\\nRUN_MODE=',
        f'umask 077\\n{MANAGER_PATCH_MARKER}\\n\\nRUN_MODE=',
        '管理器版本标记',
    )
"""
new = """    legacy_header = 'umask 077\\n\\nRUN_MODE='
    ipv4_header = 'source \"$IPV4_ONLY_MODULE\"\\n\\nRUN_MODE='
    if legacy_header in text:
        text = replace_once(
            text,
            legacy_header,
            f'umask 077\\n{MANAGER_PATCH_MARKER}\\n\\nRUN_MODE=',
            '管理器版本标记（旧版）',
        )
    elif ipv4_header in text:
        text = replace_once(
            text,
            ipv4_header,
            f'source \"$IPV4_ONLY_MODULE\"\\n{MANAGER_PATCH_MARKER}\\n\\nRUN_MODE=',
            '管理器版本标记（IPv4-only）',
        )
    else:
        raise RuntimeError('管理器版本标记：未找到兼容的旧版或 IPv4-only 头部。')
"""
count = text.count(old)
if count != 1:
    raise SystemExit(f'expected one manager marker patch block, found {count}')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
print('patched client renderer to support both legacy and IPv4-only manager headers')

#!/usr/bin/env python3
from pathlib import Path

path = Path('tests/conformance.py')
text = path.read_text(encoding='utf-8')
old = '''    require('mv "$TMP/app" "$target"' in installer and '.vvv-source.previous' in installer, '下载源码没有通过原子替换防止中断残留')
'''
new = '''    for token in (
        'SOURCE_STAGING="/usr/local/lib/.vvv-source.staging.$$"',
        'SOURCE_BACKUP="/usr/local/lib/.vvv-source.previous.$$"',
        'cp -a "$TMP/app" "$SOURCE_STAGING"',
        'mv "$SOURCE_STAGING" "$SOURCE_TARGET"',
        'mv "$SOURCE_BACKUP" "$SOURCE_TARGET"',
        'SOURCE_SWAP_COMMITTED=1',
    ):
        require(token in installer, f'源码安全替换缺少：{token}')
    require('mv "$TMP/app" "$SOURCE_TARGET"' not in installer, '仍从 /tmp 跨文件系统直接替换正式源码')
'''
if text.count(old) != 1:
    raise SystemExit('rollback test target not found exactly once')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
print('ROLLBACK TEST UPDATED')

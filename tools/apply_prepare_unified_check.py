#!/usr/bin/env python3
from pathlib import Path
path=Path('src/prepare.py')
text=path.read_text(encoding='utf-8')
old="for required in ('VVV_SUB_DOMAIN', 'VVV_SUB_PORT', '--adapter caddyfile', 'backup_manager.py', '/r/${token}/c'):"
new="for required in ('VVV_SUB_DOMAIN', 'VVV_SUB_PORT', 'VVV_SUB_TRANSPORT', 'VVV_SUB_SUFFIX', 'client_adapters.py', 'center_transport.sh'):"
if text.count(old)!=1:
    raise SystemExit(f'prepare required-field anchor count={text.count(old)}')
path.write_text(text.replace(old,new,1),encoding='utf-8')

test=Path('tests/conformance.py')
s=test.read_text(encoding='utf-8')
anchor="    require('random 8' not in bootstrap, 'placeholder')\n"
# Insert into the transport test without depending on a placeholder.
needle="    require('refresh_center_runtime_code' in bootstrap and 'center_manager.sh' in bootstrap, '重复安装不会刷新中心管理器')\n"
addition=needle+"    prepare = read('src/prepare.py')\n    require('/r/${token}/c' not in prepare and 'VVV_SUB_TRANSPORT' in prepare and 'client_adapters.py' in prepare, '最终安装器构建器仍要求旧四路径或未校验新模块')\n"
if s.count(needle)!=1:
    raise SystemExit('conformance prepare anchor missing')
test.write_text(s.replace(needle,addition,1),encoding='utf-8')
print('PREPARE UNIFIED CHECK PATCH APPLIED')

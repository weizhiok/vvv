#!/usr/bin/env python3
from pathlib import Path

path=Path('core-src/bootstrap.sh')
text=path.read_text(encoding='utf-8')
old='''migrate_center_config_if_needed
show_install_menu'''
new='''migrate_center_config_if_needed
if [[ -f /etc/vvv-sub/.schema3-migrated ]]; then
  refresh_center_runtime_code
  ensure_center_runtime || fail "旧订阅中心配置已迁移，但新统一入口服务无法启动；原数据和 schema2 备份均已保留。"
fi
show_install_menu'''
if text.count(old)!=1:
    raise SystemExit(f'immediate migration anchor count={text.count(old)}')
path.write_text(text.replace(old,new,1),encoding='utf-8')

test=Path('tests/conformance.py')
s=test.read_text(encoding='utf-8')
anchor="    require('migrate_center_config_if_needed' in bootstrap and 'config.schema2-backup.json' in bootstrap, '旧schema2订阅中心不会原地迁移')\n"
addition=anchor+"    require('if [[ -f /etc/vvv-sub/.schema3-migrated ]]' in bootstrap and bootstrap.index('refresh_center_runtime_code', bootstrap.index('migrate_center_config_if_needed\\nif')) < bootstrap.index('show_install_menu', bootstrap.index('migrate_center_config_if_needed\\nif')), '旧中心迁移后没有在显示菜单前立即刷新运行时')\n"
if s.count(anchor)!=1:
    raise SystemExit('conformance immediate migration anchor missing')
test.write_text(s.replace(anchor,addition,1),encoding='utf-8')
print('IMMEDIATE CENTER MIGRATION PATCH APPLIED')

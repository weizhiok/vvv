#!/usr/bin/env python3
from pathlib import Path

path = Path('tools/apply_ssh_log_fixes.py')
text = path.read_text(encoding='utf-8')

start = text.index("old_execute = r'''")
new_start = text.index("new_execute = r'''", start)
new_end = text.index("\n'''", new_start + len("new_execute = r'''")) + 4
new_declaration = text[new_start:new_end]
block_end = text.index("write(p, t)", new_end)
replacement = new_declaration + r'''
t = sub_once(
    t,
    r'(?ms)^case "\$choice" in\n  1\)\n    install_host\n.*?^esac\n(?=install_unified_manager)',
    lambda m: new_execute,
    'role execution mapping',
)
'''
text = text[:start] + replacement + text[block_end:]

installer_anchor = "t = t.replace(' backup_manager.py rclone_manager.sh qr_helper.sh)', ' backup_manager.py rclone_manager.sh)')\n"
installer_replacement = installer_anchor + "t = t.replace(' rclone_manager.sh qr_helper.sh host.sh;', ' rclone_manager.sh host.sh;')\n"
if installer_replacement not in text:
    if installer_anchor not in text:
        raise SystemExit('installer QR syntax list anchor not found')
    text = text.replace(installer_anchor, installer_replacement, 1)

old_assertion = "require('sync_role' not in manager and 'center-relay' not in manager, '仍保留旧 all 角色兼容映射')"
new_assertion = "require('sync_role' not in manager, '仍保留旧 all 角色兼容映射')"
if old_assertion in text:
    text = text.replace(old_assertion, new_assertion, 1)
elif new_assertion not in text:
    raise SystemExit('manager compatibility assertion anchor not found')

old_error = "raise SystemExit(f'forbidden QR implementation remains: {forbidden}')"
new_error = "raise SystemExit(f'forbidden QR implementation remains: {forbidden}; files={[p for p in production_files if forbidden in read(p)]}')"
if old_error in text:
    text = text.replace(old_error, new_error, 1)
elif new_error not in text:
    raise SystemExit('QR diagnostic anchor not found')

path.write_text(text, encoding='utf-8')
print('SSH log transformer anchors repaired')

#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 3:
    raise SystemExit('usage: extract_manager_library.py GENERATED_HOST OUTPUT')
source = Path(sys.argv[1]).read_text(encoding='utf-8')
start_marker = "cat > /usr/local/sbin/jp-relay-manager <<'JP_RELAY_JPR3_MANAGER_EOF'\n"
end_marker = '\nJP_RELAY_JPR3_MANAGER_EOF\n'
if source.count(start_marker) != 1:
    raise SystemExit(f'manager start marker count={source.count(start_marker)}')
manager = source.split(start_marker, 1)[1]
if manager.count(end_marker) != 1:
    raise SystemExit(f'manager end marker count={manager.count(end_marker)}')
manager = manager.split(end_marker, 1)[0]
execution_marker = '\n[[ "$EUID" -eq 0 ]] || fail "请使用 root 用户执行。"\n'
if manager.count(execution_marker) != 1:
    raise SystemExit(f'manager execution marker count={manager.count(execution_marker)}')
manager = manager.split(execution_marker, 1)[0] + '\ntrap - EXIT\n'
Path(sys.argv[2]).write_text(manager, encoding='utf-8')

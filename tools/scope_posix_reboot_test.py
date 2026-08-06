#!/usr/bin/env python3
from pathlib import Path

path = Path('tests/test_install_reboot_guard.py')
text = path.read_text(encoding='utf-8')
marker = "daily_reboot = between(HOST, 'install_daily_reboot_cron() {', 'prompt_initial_mode_and_port() {')\n"
addition = marker + "daily_reboot_helper = daily_reboot.split(\"cat > /usr/local/lib/vvv/daily-reboot.sh <<'EOF_DAILY_REBOOT'\", 1)[1].split('EOF_DAILY_REBOOT', 1)[0]\n"
if marker not in text:
    raise SystemExit('daily_reboot assignment marker not found')
text = text.replace(marker, addition, 1)
text = text.replace(
    "require('#!/bin/sh' in daily_reboot and '#!/usr/bin/env bash' not in daily_reboot,\n",
    "require('#!/bin/sh' in daily_reboot_helper and '#!/usr/bin/env bash' not in daily_reboot_helper,\n",
    1,
)
text = text.replace(
    "require('[[' not in daily_reboot and '((' not in daily_reboot,\n",
    "require('[[' not in daily_reboot_helper and '((' not in daily_reboot_helper,\n",
    1,
)
path.write_text(text, encoding='utf-8')
print('POSIX assertions now inspect only the generated reboot helper.')

#!/usr/bin/env python3
from pathlib import Path

path = Path('src/prepare.py')
text = path.read_text(encoding='utf-8')

replacements = {
    "    hy2_main + '\\nverify_xray_runtime() {',": "    lambda _match: hy2_main + '\\nverify_xray_runtime() {',",
    "    verify_hy2 + '\\nactivate_initial_state() {',": "    lambda _match: verify_hy2 + '\\nactivate_initial_state() {',",
    "h, n = re.subn(r'(?ms)^apply_candidate_with_rollback\\(\\) \\{.*?^\\}\\n\\ngenerate_client_files\\(\\) \\{', new_apply + 'generate_client_files() {', h, count=1)": "h, n = re.subn(r'(?ms)^apply_candidate_with_rollback\\(\\) \\{.*?^\\}\\n\\ngenerate_client_files\\(\\) \\{', lambda _match: new_apply + 'generate_client_files() {', h, count=1)",
}

for old, new in replacements.items():
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'replacement anchor count={count}: {old[:80]!r}')
    text = text.replace(old, new, 1)

path.write_text(text, encoding='utf-8')

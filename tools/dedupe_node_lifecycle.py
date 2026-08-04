#!/usr/bin/env python3
from pathlib import Path
import re

path = Path('core-src/host.sh')
text = path.read_text(encoding='utf-8')

# Shell 顶层函数均从行首开始；按下一个顶层函数边界删除同名后续定义。
def remove_later_definitions(source: str, name: str) -> str:
    starts = list(re.finditer(rf'(?m)^{re.escape(name)}\(\) \{{\n', source))
    if len(starts) < 2:
        return source
    ranges = []
    for match in starts[1:]:
        next_match = re.search(r'(?m)^[A-Za-z_][A-Za-z0-9_]*\(\) \{\n', source[match.end():])
        end = len(source) if next_match is None else match.end() + next_match.start()
        ranges.append((match.start(), end))
    for start, end in reversed(ranges):
        source = source[:start] + source[end:]
    return source

for function_name in ('validate_slot_references', 'build_delete_candidate'):
    text = remove_later_definitions(text, function_name)

validation_block = '''  if ! validate_slot_references "$candidate_state"; then
    fail "候选线路状态引用校验失败，未修改任何运行配置。"
    return 1
  fi
'''
while validation_block + validation_block in text:
    text = text.replace(validation_block + validation_block, validation_block, 1)

expected_once = (
    'verify_xray_runtime',
    'verify_sing_runtime',
    'sync_vless_slot_services',
    'sync_hy2_slot_services',
    'validate_slot_references',
    'build_delete_candidate',
    'perform_delete',
    'perform_delete_upstream',
)
for name in expected_once:
    count = len(re.findall(rf'(?m)^{re.escape(name)}\(\) \{{$', text))
    if count != 1:
        raise SystemExit(f'{name} 顶层定义数量异常：{count}')

call_count = text.count('  if ! validate_slot_references "$candidate_state"; then\n')
if call_count != 1:
    raise SystemExit(f'事务引用校验调用数量异常：{call_count}')

path.write_text(text, encoding='utf-8')
print('deduplicated lifecycle source definitions and guards')

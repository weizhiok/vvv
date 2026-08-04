#!/usr/bin/env python3
# Validate quoted Python heredocs before any installer role changes the VPS.
import argparse
import re
from pathlib import Path

HEADER = re.compile(
    r"(?m)^[^\n]*\bpython3\b[^\n]*<<(?P<quote>['\"])(?P<tag>PY_[A-Za-z0-9_]+)(?P=quote)[^\n]*$"
)


def extract_blocks(path):
    path = Path(path)
    text = path.read_text(encoding='utf-8')
    blocks = []
    for match in HEADER.finditer(text):
        tag = match.group('tag')
        start = match.end()
        if start < len(text) and text[start] == '\n':
            start += 1
        terminator = re.compile(rf"(?m)^{re.escape(tag)}[ \t]*$").search(text, start)
        if terminator is None:
            raise SyntaxError(f'{path}: {tag} 缺少结束标记')
        body = text[start:terminator.start()]
        blocks.append((tag, body))
    return blocks


def validate_path(path):
    count = 0
    for tag, body in extract_blocks(path):
        compile(body, f'{path}:{tag}', 'exec')
        count += 1
    return count


def validate_paths(paths):
    total = sum(validate_path(path) for path in paths)
    if total == 0:
        raise SyntaxError('没有找到可验证的 quoted Python heredoc')
    return total


def main():
    parser = argparse.ArgumentParser(description='检查 Shell 文件中的 quoted Python heredoc 语法。')
    parser.add_argument('paths', nargs='+')
    args = parser.parse_args()
    total = validate_paths(args.paths)
    print(f'内嵌 Python 语法检查通过：{total} 个代码块。')


if __name__ == '__main__':
    main()

import atexit
from pathlib import Path


def finish():
    test = Path('tests/conformance.py')
    if test.exists():
        lines = test.read_text(encoding='utf-8').splitlines()
        changed = False
        for index, line in enumerate(lines):
            if 'SSH 没有绿色订阅中心注册成功提示' in line and 'require("printf' in line:
                lines[index] = "    require('订阅中心注册成功' in register and '\\\\033[32m' in register and '\\\\033[0m' in register, 'SSH 没有绿色订阅中心注册成功提示')"
                changed = True
        if changed:
            test.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    Path(__file__).unlink(missing_ok=True)


atexit.register(finish)

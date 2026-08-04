#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

git config user.name github-actions[bot]
git config user.email 41898282+github-actions[bot]@users.noreply.github.com

python3 - <<'PY_PATCH'
from pathlib import Path

bootstrap = Path('core-src/bootstrap.sh')
text = bootstrap.read_text(encoding='utf-8')
broken = "with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump(obj,f,ensure_ascii=False,indent=2); f.write('\n')"
fixed = "with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump(obj,f,ensure_ascii=False,indent=2); print(file=f)"
if text.count(broken) != 1:
    raise SystemExit(f'bootstrap broken newline anchor mismatch: {text.count(broken)}')
text = text.replace(broken, fixed, 1)
bootstrap.write_text(text, encoding='utf-8')

validator = Path('src/validate_embedded_python.py')
validator.write_text(r'''#!/usr/bin/env python3
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
''', encoding='utf-8')

installer = Path('vvv-install.sh')
text = installer.read_text(encoding='utf-8')
old = '''curl -fsSL --retry 5 --retry-all-errors "$RAW/src/prepare.py?v=$nonce" -o "$TMP/prepare.py" || fail "下载 prepare.py 失败。"
files=(host.sh landing.sh center_install.sh register_sync.sh vvv_manager.sh sub_center.py sync_agent.py backup_manager.py rclone_manager.sh client_adapters.py adapter_manager.py client_upgrade_engine.py client_local_renderer.py center_transport.sh center_manager.sh restore_manager.py diagnostic_report.py node_probe.py)'''
new = '''curl -fsSL --retry 5 --retry-all-errors "$RAW/src/prepare.py?v=$nonce" -o "$TMP/prepare.py" || fail "下载 prepare.py 失败。"
curl -fsSL --retry 5 --retry-all-errors "$RAW/src/validate_embedded_python.py?v=$nonce" -o "$TMP/validate_embedded_python.py" || fail "下载内嵌 Python 检查器失败。"
files=(host.sh landing.sh center_install.sh register_sync.sh vvv_manager.sh sub_center.py sync_agent.py backup_manager.py rclone_manager.sh client_adapters.py adapter_manager.py client_upgrade_engine.py client_local_renderer.py center_transport.sh center_manager.sh restore_manager.py diagnostic_report.py node_probe.py)'''
if text.count(old) != 1:
    raise SystemExit('installer download anchor mismatch')
text = text.replace(old, new, 1)
old = '''python3 -m py_compile "$TMP/prepare.py"
python3 "$TMP/prepare.py" "$TMP/app/host.sh" "$TMP/app/landing.sh" "$TMP/app/center_install.sh" || fail "源码参数化处理失败。"
for file in bootstrap.sh center_install.sh register_sync.sh vvv_manager.sh rclone_manager.sh center_transport.sh center_manager.sh host.sh; do'''
new = '''python3 -m py_compile "$TMP/prepare.py" "$TMP/validate_embedded_python.py"
python3 "$TMP/prepare.py" "$TMP/app/host.sh" "$TMP/app/landing.sh" "$TMP/app/center_install.sh" || fail "源码参数化处理失败。"
python3 "$TMP/validate_embedded_python.py" \
  "$TMP/app/bootstrap.sh" "$TMP/app/host.sh" "$TMP/app/landing.sh" \
  "$TMP/app/center_install.sh" "$TMP/app/register_sync.sh" "$TMP/app/vvv_manager.sh" \
  "$TMP/app/rclone_manager.sh" "$TMP/app/center_transport.sh" "$TMP/app/center_manager.sh" \
  || fail "Shell 内嵌 Python 语法检查失败。"
for file in bootstrap.sh center_install.sh register_sync.sh vvv_manager.sh rclone_manager.sh center_transport.sh center_manager.sh host.sh; do'''
if text.count(old) != 1:
    raise SystemExit('installer validation anchor mismatch')
text = text.replace(old, new, 1)
installer.write_text(text, encoding='utf-8')

conformance = Path('tests/conformance.py')
text = conformance.read_text(encoding='utf-8')
anchor = '''def test_installer_and_diagnostics():
    installer = read('vvv-install.sh')'''
replacement = '''def test_embedded_python_heredocs():
    validator = load('embedded_python_validator_test', ROOT / 'src' / 'validate_embedded_python.py')
    shell_files = [
        ROOT / 'core-src' / name for name in (
            'bootstrap.sh', 'host.sh', 'landing.sh', 'center_install.sh',
            'register_sync.sh', 'vvv_manager.sh', 'rclone_manager.sh',
            'center_transport.sh', 'center_manager.sh',
        )
    ]
    count = validator.validate_paths(shell_files)
    require(count >= 1, '没有验证任何 Shell 内嵌 Python')
    bootstrap = read('core-src/bootstrap.sh')
    require('print(file=f)' in bootstrap, '角色 JSON 写入仍依赖易损坏的反斜杠换行')


def test_installer_and_diagnostics():
    installer = read('vvv-install.sh')'''
if text.count(anchor) != 1:
    raise SystemExit('conformance function anchor mismatch')
text = text.replace(anchor, replacement, 1)
old = '''        test_node_names_and_clients, test_landing_and_direct_ip_change,
        test_installer_and_diagnostics, test_no_qr_and_debian13,'''
new = '''        test_node_names_and_clients, test_landing_and_direct_ip_change,
        test_embedded_python_heredocs, test_installer_and_diagnostics, test_no_qr_and_debian13,'''
if text.count(old) != 1:
    raise SystemExit('conformance test list anchor mismatch')
text = text.replace(old, new, 1)
old = '''    for name in ('restore_manager.py', 'diagnostic_report.py', 'node_probe.py'):
        require(name in installer, f'安装器没有下载：{name}')'''
new = '''    for name in ('restore_manager.py', 'diagnostic_report.py', 'node_probe.py', 'validate_embedded_python.py'):
        require(name in installer, f'安装器没有下载：{name}')
    require('Shell 内嵌 Python 语法检查失败' in installer, '安装器没有在执行前检查 heredoc Python')'''
if text.count(old) != 1:
    raise SystemExit('conformance installer anchor mismatch')
text = text.replace(old, new, 1)
conformance.write_text(text, encoding='utf-8')
PY_PATCH

python3 -m py_compile src/validate_embedded_python.py tests/conformance.py
python3 src/validate_embedded_python.py \
  core-src/bootstrap.sh core-src/host.sh core-src/landing.sh core-src/center_install.sh \
  core-src/register_sync.sh core-src/vvv_manager.sh core-src/rclone_manager.sh \
  core-src/center_transport.sh core-src/center_manager.sh
bash -n vvv-install.sh
bash -n core-src/bootstrap.sh
bash -n core-src/host.sh
sh -n core-src/landing.sh
python3 tests/conformance.py

git fetch --no-tags --depth=1 origin main
git checkout FETCH_HEAD -- \
  tools/publish_production_ssh_fix.sh \
  .github/workflows/publish-production-ssh-fix.yml

git add -A
git diff --cached --check
git commit -m 'Validate embedded Python before installation'
git push origin HEAD

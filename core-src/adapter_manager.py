#!/usr/bin/env python3
import subprocess
import sys

ENGINE = '/usr/local/lib/vvv/client_upgrade_engine.py'


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else 'status'
    mapping = {'update': 'menu', 'status': 'status'}
    if command not in mapping:
        raise SystemExit('用法：adapter_manager.py [update|status]')
    raise SystemExit(subprocess.run(['python3', ENGINE, mapping[command]], check=False).returncode)


if __name__ == '__main__':
    main()

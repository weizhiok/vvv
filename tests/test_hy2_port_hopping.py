#!/usr/bin/env python3
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'core-src' / 'hy2_port_hop.py'
spec = importlib.util.spec_from_file_location('hy2_port_hop', MODULE)
hop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hop)


def expect_error(value, listener, fragment):
    try:
        hop.parse_port_spec(value, listener)
    except hop.PortSpecError as exc:
        assert fragment in str(exc), (fragment, str(exc))
    else:
        raise AssertionError(f'expected PortSpecError for {value!r}')


def main():
    parsed = hop.parse_port_spec(' 443, 20000-30000,30001-50000,443 ', 443)
    assert parsed == [(443, 443), (20000, 50000)], parsed
    assert hop.format_intervals(parsed) == '443,20000-50000'
    assert hop.parse_port_spec('20000-50000', 30000) == [(20000, 50000)]
    expect_error('443，20000-50000', 443, '英文逗号')
    expect_error('443,50000-20000', 443, '起始端口')
    expect_error('443,,20000-50000', 443, '空项目')
    expect_error('20000-50000', 443, '包含实际监听端口')
    expect_error('443', 443, '至少包含两个')
    expect_error('0,443', 443, '1–65535')

    rules = hop.build_ruleset('443,20000-50000', 443)
    assert 'table inet vvv_hy2_hop' in rules
    assert 'elements = { 20000-50000 }' in rules
    assert 'redirect to :443' in rules
    assert len(rules) < 1024, 'range must not be expanded port-by-port'

    original_rows = hop.udp_socket_rows
    original_owners = hop.inode_owners
    try:
        hop.udp_socket_rows = lambda: [
            {'port': 21234, 'inode': '11', 'family': 2, 'state': '07'},
            {'port': 443, 'inode': '12', 'family': 2, 'state': '07'},
            {'port': 53000, 'inode': '13', 'family': 2, 'state': '07'},
        ]
        hop.inode_owners = lambda _inodes: {
            '11': [{'pid': 1234, 'process': 'occupied-daemon', 'command': '/usr/bin/occupied-daemon'}],
            '12': [{'pid': 5678, 'process': 'sing-box', 'command': '/usr/local/bin/sing-box run'}],
            '13': [{'pid': 9, 'process': 'outside', 'command': 'outside'}],
        }
        conflicts = hop.find_udp_conflicts(
            hop.parse_port_spec('443,20000-50000', 443),
            allow_listen_port=443,
            allow_process='sing-box',
        )
        assert [row['port'] for row in conflicts] == [21234], conflicts
        message = hop.conflict_text(conflicts)
        assert '21234' in message and 'occupied-daemon' in message and 'PID：1234' in message
    finally:
        hop.udp_socket_rows = original_rows
        hop.inode_owners = original_owners

    print('Hysteria 2 port hopping engine tests passed.')


if __name__ == '__main__':
    main()

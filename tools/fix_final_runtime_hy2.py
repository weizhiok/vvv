#!/usr/bin/env python3
from pathlib import Path


def replace_once(path, old, new):
    target = Path(path)
    text = target.read_text(encoding='utf-8')
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{path}: expected one match, found {count}: {old[:120]!r}')
    target.write_text(text.replace(old, new, 1), encoding='utf-8')


replace_once(
    'tests/build_slot_fixture.py',
    "    'schema': 3, 'role': 'japan-hub', 'protocol_mode': 'dual',\n"
    "    'public_ip': '198.51.100.10', 'listen_port': 24443, 'sni': 'www.softbank.jp',\n"
    "    'direct_base_name': 'JP-198.51.100.10:24443', 'xray_version': 'audit', 'sing_box_version': 'audit',\n",
    "    'schema': 4, 'role': 'japan-hub', 'protocol_mode': 'dual',\n"
    "    'public_ip': '198.51.100.10', 'listen_port': 24443, 'sni': 'www.softbank.jp',\n"
    "    'direct_base_name': 'JP-198.51.100.10:24443', 'xray_version': 'audit', 'sing_box_version': 'audit',\n"
    "    'hy2_limit_mbps': 50,\n"
    "    'port_hopping': {'enabled': True, 'ports': '24443,30000-30031', 'hop_interval_seconds': 30},\n",
)

replace_once(
    'tests/final_runtime_validation.sh',
    '''  "$ROOT/core-src/client_adapters.py" \\
  "$ROOT/core-src/adapter_manager.py" \\
''',
    '''  "$ROOT/core-src/client_adapters.py" \\
  "$ROOT/core-src/client_package_renderer.py" \\
  "$ROOT/core-src/hy2_port_hop.py" \\
  "$ROOT/core-src/adapter_manager.py" \\
''',
)

replace_once(
    'tests/final_runtime_validation.sh',
    '''source "$WORK/manager-lib.sh"
XRAY="$TEST_XRAY"
SING_BOX="$TEST_SING_BOX"
HY2_LIMIT_MBPS=50
''',
    '''source "$WORK/manager-lib.sh"
XRAY="$TEST_XRAY"
SING_BOX="$TEST_SING_BOX"
HY2_LIMIT_MBPS=50
install -m755 "$ROOT/core-src/client_package_renderer.py" "$WORK/client_package_renderer.py"
install -m755 "$ROOT/core-src/client_adapters.py" "$WORK/client_adapters.py"
CLIENT_PACKAGE_RENDERER="$WORK/client_package_renderer.py"
CLIENT_ADAPTER="$WORK/client_adapters.py"
''',
)

replace_once(
    'tests/final_runtime_validation.sh',
    '''[[ -s "$WORK/client-files/Clash-Verge-Rev.yaml" ]]
[[ -s "$WORK/client-files/NekoBoxForAndroid.yaml" ]]
cmp "$WORK/client-files/Clash-Verge-Rev.yaml" "$WORK/client-files/NekoBoxForAndroid.yaml"
! find "$WORK/client-files" -maxdepth 1 -type f -iname '*v2*' | grep -q .
grep -q '^vless=' "$WORK/client-files/Quantumult-X.conf"
grep -q 'Hysteria2' "$WORK/client-files/Loon.conf"
grep -q '^hysteria2://' "$WORK/client-files/Shadowrocket.txt"
grep -q 'type: hysteria2' "$WORK/client-files/Clash-Verge-Rev.yaml"
grep -q 'type: hysteria2' "$WORK/client-files/NekoBoxForAndroid.yaml"
''',
    '''[[ -s "$WORK/client-files/Clash-Verge-Rev.yaml" ]]
[[ -s "$WORK/client-files/NekoBoxForAndroid.txt" ]]
[[ ! -e "$WORK/client-files/NekoBoxForAndroid.yaml" ]]
[[ ! -e "$WORK/client-files/Loon-Shadowrocket.txt" ]]
! find "$WORK/client-files" -maxdepth 1 -type f -iname '*v2*' | grep -q .
grep -q '^vless=' "$WORK/client-files/Quantumult-X.conf"
grep -q 'Hysteria2' "$WORK/client-files/Loon.conf"
grep -q 'server-ports="24443,30000-30031"' "$WORK/client-files/Loon.conf"
grep -q '^hysteria2://' "$WORK/client-files/Shadowrocket.txt"
grep -q '24443,30000-30031' "$WORK/client-files/Shadowrocket.txt"
grep -q 'type: hysteria2' "$WORK/client-files/Clash-Verge-Rev.yaml"
grep -q 'ports: "24443,30000-30031"' "$WORK/client-files/Clash-Verge-Rev.yaml"
grep -q 'hop-interval: 30' "$WORK/client-files/Clash-Verge-Rev.yaml"
grep -q '^hy2://' "$WORK/client-files/NekoBoxForAndroid.txt"
grep -q '24443,30000-30031' "$WORK/client-files/NekoBoxForAndroid.txt"
''',
)

print('Final runtime fixtures updated for unified HY2 client rendering.')

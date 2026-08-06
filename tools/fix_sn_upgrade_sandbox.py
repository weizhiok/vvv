#!/usr/bin/env python3
from pathlib import Path

adapter_path = Path('core-src/client_adapters.py')
text = adapter_path.read_text(encoding='utf-8')
text = text.replace('import struct\n', '')

start = text.index('class _KryoSnWriter:')
end = text.index('def _sn_link(type_name, payload):', start)
functional_writer = r'''def _sn_write_int(buffer, value):
    buffer.extend(int(value).to_bytes(4, 'little', signed=True))


def _sn_write_bool(buffer, value):
    buffer.append(1 if value else 0)


def _sn_write_utf8_length(buffer, value):
    if value >> 6 == 0:
        buffer.append(0x80 | value)
    elif value >> 13 == 0:
        buffer.append(0xC0 | (value & 0x3F))
        buffer.append(value >> 6)
    elif value >> 20 == 0:
        buffer.append(0xC0 | (value & 0x3F))
        buffer.append(0x80 | ((value >> 6) & 0x7F))
        buffer.append(value >> 13)
    elif value >> 27 == 0:
        buffer.append(0xC0 | (value & 0x3F))
        buffer.append(0x80 | ((value >> 6) & 0x7F))
        buffer.append(0x80 | ((value >> 13) & 0x7F))
        buffer.append(value >> 20)
    else:
        buffer.append(0xC0 | (value & 0x3F))
        buffer.append(0x80 | ((value >> 6) & 0x7F))
        buffer.append(0x80 | ((value >> 13) & 0x7F))
        buffer.append(0x80 | ((value >> 20) & 0x7F))
        buffer.append(value >> 27)


def _sn_write_string(buffer, value):
    if value is None:
        buffer.append(0x80)
        return
    value = str(value)
    if not value:
        buffer.append(0x81)
        return
    if 1 < len(value) < 32 and all(ord(char) <= 0x7F for char in value):
        encoded = bytearray(value.encode('ascii'))
        encoded[-1] |= 0x80
        buffer.extend(encoded)
        return
    char_count = len(value.encode('utf-16-le')) // 2
    _sn_write_utf8_length(buffer, char_count + 1)
    buffer.extend(value.encode('utf-8'))


'''
text = text[:start] + functional_writer + text[end:]

start = text.index('def _nekobox_vless_sn(node):')
end = text.index('def _nekobox_hy2_sn(node):', start)
vless = r'''def _nekobox_vless_sn(node):
    data = bytearray()
    _sn_write_int(data, 4)  # StandardV2RayBean serialization version.
    _sn_write_string(data, node['server'])
    _sn_write_int(data, node['port'])
    _sn_write_string(data, node['uuid'])
    _sn_write_string(data, 'xtls-rprx-vision')
    _sn_write_int(data, -1)  # VMessBean alterId=-1 means VLESS.
    _sn_write_string(data, 'tcp')
    _sn_write_string(data, 'tls')
    _sn_write_string(data, node['sni'])
    _sn_write_string(data, '')  # ALPN
    _sn_write_string(data, '')  # Certificates
    _sn_write_bool(data, True)  # allowInsecure
    _sn_write_string(data, 'chrome')
    _sn_write_string(data, node['public_key'])
    _sn_write_string(data, node['short_id'])
    _sn_write_bool(data, False)  # ECH
    _sn_write_string(data, '')
    _sn_write_int(data, 2)  # packetEncoding=xudp
    _sn_write_bool(data, False)  # mux
    _sn_write_bool(data, False)  # mux padding
    _sn_write_int(data, 0)  # mux type
    _sn_write_int(data, 1)  # mux concurrency
    _sn_write_int(data, 1)  # AbstractBean extra version
    _sn_write_string(data, node['name'])
    _sn_write_string(data, '')  # custom outbound JSON
    _sn_write_string(data, '')  # custom config JSON
    return _sn_link('vmess', bytes(data))


'''
text = text[:start] + vless + text[end:]

start = text.index('def _nekobox_hy2_sn(node):')
end = text.index('def render_nekobox_sn_links(nodes):', start)
hy2 = r'''def _nekobox_hy2_sn(node):
    data = bytearray()
    _sn_write_int(data, 7)  # HysteriaBean serialization version.
    _sn_write_string(data, node['server'])
    _sn_write_int(data, 1080)  # HysteriaBean uses serverPorts; exported serverPort stays default.
    _sn_write_int(data, 2)  # Hysteria 2
    _sn_write_int(data, 0)  # auth payload type used by NekoBox exports
    _sn_write_string(data, node['password'])
    _sn_write_int(data, 0)  # UDP
    _sn_write_string(data, node['obfs_password'])
    _sn_write_string(data, node['sni'])
    _sn_write_string(data, '')  # ALPN
    _sn_write_int(data, client_up_mbps(node))
    _sn_write_int(data, client_down_mbps(node))
    _sn_write_bool(data, True)  # allowInsecure
    _sn_write_string(data, '')  # CA text
    _sn_write_int(data, 0)  # stream receive window
    _sn_write_int(data, 0)  # connection receive window
    _sn_write_bool(data, False)  # disable MTU discovery
    _sn_write_int(data, fixed_hop_interval(node))
    _sn_write_string(data, hy2_ports(node))
    _sn_write_int(data, 1)  # AbstractBean extra version
    _sn_write_string(data, node['name'])
    _sn_write_string(data, '')
    _sn_write_string(data, '')
    return _sn_link('hysteria', bytes(data))


'''
text = text[:start] + hy2 + text[end:]
adapter_path.write_text(text, encoding='utf-8')

engine_path = Path('core-src/client_upgrade_engine.py')
engine = engine_path.read_text(encoding='utf-8')
old = "ALLOWED_IMPORTS = {'base64', 'json', 're', 'urllib.parse'}"
new = "ALLOWED_IMPORTS = {'base64', 'json', 're', 'urllib.parse', 'zlib'}"
if old not in engine:
    raise SystemExit('client upgrade allowed-import marker not found')
engine_path.write_text(engine.replace(old, new, 1), encoding='utf-8')

assert 'class _KryoSnWriter' not in text
assert 'import struct' not in text
assert 'import zlib' in text

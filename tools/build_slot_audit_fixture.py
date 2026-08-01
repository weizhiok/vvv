#!/usr/bin/env python3
import base64
import json
import os
from pathlib import Path

out = Path(os.environ.get('AUDIT_DIR', '/tmp/vvv-slot-audit'))
out.mkdir(parents=True, exist_ok=True)

private_key = os.environ['VVV_AUDIT_REALITY_PRIVATE']
public_key = os.environ['VVV_AUDIT_REALITY_PUBLIC']
remote_public_key = os.environ['VVV_AUDIT_REMOTE_PUBLIC']
cert_path = os.environ['VVV_AUDIT_CERT_PATH']
key_path = os.environ['VVV_AUDIT_KEY_PATH']
remote_pin = base64.b64encode(bytes(range(32))).decode()

vless_slots = []
for index in range(1, 65):
    vless_slots.append({
        'slot': f'v{index:02d}',
        'uuid': f'00000000-0000-4000-8000-{index:012d}',
        'email': f'reserve-{index:02d}@relay.local',
        'local_port': 22000 + index,
        'assigned_id': None,
    })

hy2_slots = []
for index in range(1, 65):
    hy2_slots.append({
        'slot': f'h{index:02d}',
        'name': f'reserve-h{index:02d}',
        'password': f'hy2-slot-password-{index:02d}',
        'local_port': 21000 + index,
        'assigned_id': None,
    })

base_state = {
    'schema': 3,
    'role': 'japan-hub',
    'protocol_mode': 'dual',
    'public_ip': '198.51.100.10',
    'listen_port': 24443,
    'sni': 'www.softbank.jp',
    'direct_base_name': 'JP-198.51.100.10:24443',
    'xray_version': 'audit',
    'sing_box_version': 'audit',
    'vless': {
        'reality': {
            'private_key': private_key,
            'public_key': public_key,
            'short_id': '0123456789abcdef',
        },
        'direct_user': {
            'uuid': '11111111-1111-4111-8111-111111111111',
            'email': 'jp-direct@relay.local',
        },
        'reserve_users': vless_slots,
    },
    'hy2': {
        'server_name': 'jp-hy2.jp-relay.local',
        'certificate_path': cert_path,
        'key_path': key_path,
        'certificate_fingerprint': '00:' * 31 + '00',
        'certificate_pin_hex': '00' * 32,
        'certificate_public_key_sha256': remote_pin,
        'obfs_password': 'main-salamander-password',
        'direct_user': {
            'name': 'jp-direct-hy2',
            'password': 'direct-hy2-password',
        },
        'reserve_users': hy2_slots,
    },
    'relays': [],
    'upstream_relays': [],
    'relay_manager_enabled': True,
    'created_at': '2026-08-01T00:00:00+00:00',
    'updated_at': '2026-08-01T00:00:00+00:00',
}

empty_state = json.loads(json.dumps(base_state))
active_state = json.loads(json.dumps(base_state))
active_state['vless']['reserve_users'][0]['assigned_id'] = 'relay-audit'
active_state['vless']['reserve_users'][1]['assigned_id'] = 'upstream-audit'
active_state['hy2']['reserve_users'][0]['assigned_id'] = 'relay-audit'
active_state['relays'].append({
    'id': 'relay-audit',
    'name': 'SG-AUDIT',
    'remote_ip': '203.0.113.20',
    'remote_port': 24443,
    'vless': {
        'client_uuid': active_state['vless']['reserve_users'][0]['uuid'],
        'client_email': active_state['vless']['reserve_users'][0]['email'],
        'reserve_slot': 'v01',
        'outbound_uuid': '22222222-2222-4222-8222-222222222222',
        'remote_reality': {
            'private_key': private_key,
            'public_key': remote_public_key,
            'short_id': 'fedcba9876543210',
        },
        'outbound_tag': 'vless-out-relay-audit',
        'test_inbound_tag': 'vless-test-relay-audit',
        'test_socks_port': 22001,
    },
    'hy2': {
        'client_user': active_state['hy2']['reserve_users'][0]['name'],
        'client_password': active_state['hy2']['reserve_users'][0]['password'],
        'reserve_slot': 'h01',
        'outbound_password': 'remote-hy2-password',
        'outbound_obfs_password': 'remote-hy2-obfs',
        'outbound_tag': 'hy2-out-relay-audit',
        'test_inbound_tag': 'hy2-test-relay-audit',
        'test_socks_port': 21001,
        'outbound_server_name': 'landing-relay-audit.jp-relay.local',
        'remote_certificate_pem': 'unused-in-japan-config',
        'remote_key_pem': 'unused-in-japan-config',
        'remote_certificate_fingerprint': '11:' * 31 + '11',
        'remote_certificate_pin_hex': '11' * 32,
        'remote_certificate_public_key_sha256': remote_pin,
    },
    'created_at': '2026-08-01T00:00:01+00:00',
    'updated_at': '2026-08-01T00:00:01+00:00',
})
active_state['upstream_relays'].append({
    'id': 'upstream-audit',
    'name': 'HTTP-AUDIT',
    'kind': 'upstream',
    'proxy_protocol': 'http',
    'protocol_label': 'HTTP/HTTPS',
    'host': 'proxy.example.com',
    'port': 18080,
    'username': 'audit-user',
    'password': 'audit-password',
    'client_uuid': active_state['vless']['reserve_users'][1]['uuid'],
    'client_email': active_state['vless']['reserve_users'][1]['email'],
    'reserve_slot': 'v02',
    'outbound_tag': 'upstream-out-audit',
    'test_inbound_tag': 'upstream-test-audit',
    'test_socks_port': 22002,
    'last_exit_ip': '192.0.2.50',
    'created_at': '2026-08-01T00:00:02+00:00',
    'updated_at': '2026-08-01T00:00:02+00:00',
})
active_state['updated_at'] = '2026-08-01T00:00:02+00:00'

(out / 'state-empty.json').write_text(json.dumps(empty_state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
(out / 'state-active.json').write_text(json.dumps(active_state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(out)

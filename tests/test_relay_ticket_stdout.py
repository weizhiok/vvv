#!/usr/bin/env python3
import contextlib
import importlib.util
import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / 'core-src' / 'sync_agent.py'

spec = importlib.util.spec_from_file_location('sync_agent_ticket_test', MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)

cfg = {
    'role': 'center-relay',
    'host_id': 'host-test',
    'host_token': 'host-token',
    'api_base_url': 'http://198.51.100.10:18081',
    'effective_api_base_url': 'http://127.0.0.1:18081',
}
bootstrap = {
    'api_base_url': 'http://198.51.100.10:18081',
    'relay_id': 'relay-1',
    'registration_token': 'ticket-token',
}

sync_calls = []


def fake_read(_path, default=None):
    return dict(cfg)


def fake_sync(emit=True):
    sync_calls.append(emit)
    return {'ok': True, 'subscription_url': 'https://example.test/sub'}


def fake_post(url, token, obj):
    assert url == 'http://127.0.0.1:18081/api/v1/relay-ticket'
    assert token == 'host-token'
    assert obj == {'host_id': 'host-test', 'relay_id': 'relay-1'}
    return {'subscription_bootstrap': dict(bootstrap)}


module.read = fake_read
module.sync = fake_sync
module.post = fake_post

output = io.StringIO()
with contextlib.redirect_stdout(output):
    module.request_relay_ticket('relay-1')

assert sync_calls == [False], f'内部同步没有使用静默模式：{sync_calls!r}'
text = output.getvalue()
lines = text.splitlines()
assert len(lines) == 1, f'relay-ticket stdout 必须只有一行 JSON，实际为：{text!r}'
assert json.loads(lines[0]) == bootstrap

print('PASS relay-ticket emits exactly one JSON document')

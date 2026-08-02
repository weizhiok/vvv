#!/usr/bin/env python3
import argparse
import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse

CFG = Path('/etc/vvv-sub/config.json')
DATA = Path('/var/lib/vvv-sub')
HOSTS = DATA / 'hosts'
OUT = DATA / 'output'
REGISTRY = DATA / 'registry.json'
BACKUP = Path('/usr/local/lib/vvv/backup_manager.py')
LOCK = threading.RLock()
SHORT_PATHS = {'c': 'clash', 'qx': 'quantumultx', 'ln': 'loon', 'sr': 'shadowrocket'}


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default


def atomic_json(path, obj, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def b64std(text):
    return base64.b64encode(text.encode()).decode()


def protocol_name(base, proto):
    m = re.match(r'^([A-Z]{2})-(.+)$', base or '')
    if m:
        return f'{m.group(1)}-{proto}-{m.group(2)}'
    if re.match(r'^\d+\.\d+\.\d+\.\d+:\d+$', base or ''):
        return f'{proto}-{base}'
    return f'{base}-{proto}'


def loon_q(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def loon_name(value):
    return str(value).replace('=', '-').replace('\n', ' ').replace('\r', ' ')


def backup(reason, force=False):
    if not BACKUP.exists():
        return
    cmd = ['python3', str(BACKUP), 'create', reason]
    if force:
        cmd.append('--force')
    subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL)


def nodes_from_host(doc):
    role = doc.get('role', 'direct')
    if role == 'landing':
        return []
    state = doc.get('state') or {}
    mode = state.get('protocol_mode')
    ip = state.get('public_ip')
    port = int(state.get('listen_port') or 0)
    sni = state.get('sni')
    if not (mode and ip and port and sni):
        return []
    v = state.get('vless') or {}
    h = state.get('hy2') or {}
    nodes = []
    def add_vless(base, uuid, udp=True, category='直连'):
        if not uuid or not v:
            return
        nodes.append({'id': hashlib.sha256((doc['host_id']+'|vless|'+base).encode()).hexdigest()[:24], 'name': protocol_name(base,'VLESS'), 'protocol':'vless', 'server':ip, 'port':port, 'uuid':uuid, 'sni':sni, 'public_key':((v.get('reality') or {}).get('public_key')), 'short_id':((v.get('reality') or {}).get('short_id')), 'udp':bool(udp), 'category':category})
    def add_hy2(base, password, category='直连'):
        if not password or not h:
            return
        nodes.append({'id': hashlib.sha256((doc['host_id']+'|hy2|'+base).encode()).hexdigest()[:24], 'name':protocol_name(base,'HY2'), 'protocol':'hysteria2', 'server':ip, 'port':port, 'password':password, 'sni':h.get('server_name'), 'obfs_password':h.get('obfs_password'), 'pin':h.get('certificate_pin_hex'), 'udp':True, 'category':category})
    base = state.get('direct_base_name') or f'{ip}:{port}'
    if mode in ('dual','vless'): add_vless(base, ((v.get('direct_user') or {}).get('uuid')), True, '直连')
    if mode in ('dual','hy2'): add_hy2(base, ((h.get('direct_user') or {}).get('password')), '直连')
    if role in ('center-relay','relay'):
        for relay in state.get('relays') or []:
            rv=relay.get('vless'); rh=relay.get('hy2')
            if rv: add_vless(relay.get('name') or relay.get('id'), rv.get('client_uuid'), True, 'VPS中转')
            if rh: add_hy2(relay.get('name') or relay.get('id'), rh.get('client_password'), 'VPS中转')
        for upstream in state.get('upstream_relays') or []:
            add_vless(upstream.get('name') or upstream.get('id'), upstream.get('client_uuid'), False, '动态代理')
    return nodes


def active_hosts():
    docs=[]; now_ts=time.time()
    for path in HOSTS.glob('*.json'):
        doc=read_json(path,{}) or {}
        if doc.get('disabled'): continue
        last=float(doc.get('last_seen_ts') or 0)
        if last and now_ts-last > 72*3600 and doc.get('role') in ('direct','landing'): continue
        docs.append(doc)
    return docs


def all_nodes():
    nodes=[]; seen=set()
    for host in active_hosts():
        for node in nodes_from_host(host):
            if node['id'] in seen: continue
            seen.add(node['id']); nodes.append(node)
    return nodes


def vless_uri(node):
    params=[('encryption','none'),('flow','xtls-rprx-vision'),('security','reality'),('sni',node['sni']),('fp','chrome'),('pbk',node['public_key']),('sid',node['short_id']),('type','tcp'),('headerType','none')]
    return f"vless://{node['uuid']}@{node['server']}:{node['port']}?{urlencode(params)}#{quote(node['name'],safe='')}"


def hy2_uri_shadowrocket(node):
    params=[('obfs','salamander'),('obfs-password',node['obfs_password']),('sni',node['sni']),('insecure','1')]
    if node.get('pin'): params.append(('pinSHA256',node['pin']))
    return f"hysteria2://{quote(node['password'],safe='')}@{node['server']}:{node['port']}/?{urlencode(params)}#{quote(node['name'],safe='')}"


def render_qx(nodes):
    lines=[]
    for node in nodes:
        if node['protocol']!='vless': continue
        lines.append(f"vless={node['server']}:{node['port']}, method=none, password={node['uuid']}, obfs=over-tls, obfs-host={node['sni']}, reality-base64-pubkey={node['public_key']}, reality-hex-shortid={node['short_id']}, vless-flow=xtls-rprx-vision, fast-open=false, udp-relay={'true' if node['udp'] else 'false'}, tag={node['name']}")
    return '\n'.join(lines)+('\n' if lines else '')


def render_loon(nodes):
    lines=[]
    for node in nodes:
        if node['protocol']=='vless':
            lines.append(f"{loon_name(node['name'])} = VLESS,{node['server']},{node['port']},{loon_q(node['uuid'])},transport=tcp,flow=xtls-rprx-vision,public-key={loon_q(node['public_key'])},short-id={node['short_id']},udp={'true' if node['udp'] else 'false'},over-tls=true,sni={node['sni']},skip-cert-verify=true")
        else:
            lines.append(f"{loon_name(node['name'])} = Hysteria2,{node['server']},{node['port']},{loon_q(node['password'])},skip-cert-verify=true,sni={node['sni']},udp=true,fast-open=true,salamander-password={node['obfs_password']}")
    return '\n'.join(lines)+('\n' if lines else '')


def render_shadowrocket(nodes):
    text='\n'.join(vless_uri(n) if n['protocol']=='vless' else hy2_uri_shadowrocket(n) for n in nodes)
    return b64std(text+('\n' if text else ''))+'\n'


def render_clash(nodes):
    lines=['mixed-port: 7890','allow-lan: false','mode: rule','log-level: info','proxies:']; names=[]
    for node in nodes:
        names.append(node['name'])
        if node['protocol']=='vless':
            lines += [f'  - name: {json.dumps(node["name"],ensure_ascii=False)}','    type: vless',f'    server: {node["server"]}',f'    port: {node["port"]}',f'    uuid: {node["uuid"]}','    network: tcp',f'    udp: {str(node["udp"]).lower()}','    tls: true','    flow: xtls-rprx-vision','    encryption: ""',f'    servername: {node["sni"]}','    client-fingerprint: chrome','    skip-cert-verify: true','    reality-opts:',f'      public-key: {node["public_key"]}',f'      short-id: "{node["short_id"]}"']
        else:
            lines += [f'  - name: {json.dumps(node["name"],ensure_ascii=False)}','    type: hysteria2',f'    server: {node["server"]}',f'    port: {node["port"]}',f'    password: {json.dumps(node["password"])}','    up: "50 Mbps"','    down: "50 Mbps"','    obfs: salamander',f'    obfs-password: {json.dumps(node["obfs_password"])}',f'    sni: {node["sni"]}','    skip-cert-verify: true','    alpn: [h3]','    udp: true']
    proxy_list=', '.join(json.dumps(x,ensure_ascii=False) for x in names) if names else 'DIRECT'
    lines += ['proxy-groups:','  - name: 全部节点','    type: select',f'    proxies: [{proxy_list}]','  - name: 自动测速','    type: url-test',f'    proxies: [{proxy_list}]','    url: https://www.gstatic.com/generate_204','    interval: 86400','rules:','  - MATCH,全部节点','']
    return '\n'.join(lines)


def regenerate():
    OUT.mkdir(parents=True,exist_ok=True); nodes=all_nodes()
    files={'clash':render_clash(nodes),'quantumultx':render_qx(nodes),'loon':render_loon(nodes),'shadowrocket':render_shadowrocket(nodes)}
    for name,content in files.items():
        path=OUT/name; tmp=path.with_suffix('.tmp'); tmp.write_text(content,encoding='utf-8'); os.chmod(tmp,0o600); os.replace(tmp,path)
    atomic_json(OUT/'nodes.json',{'generated_at':now(),'count':len(nodes),'nodes':nodes})
    return len(nodes)


def auth_token(handler):
    value=handler.headers.get('Authorization','')
    return value[7:] if value.startswith('Bearer ') else ''


def request_ip(handler):
    forwarded=handler.headers.get('X-Forwarded-For','').split(',')[0].strip()
    candidate=forwarded or handler.client_address[0]
    try: return ipaddress.ip_address(candidate)
    except ValueError: return None


def finalize_registration(entry, body):
    doc={
        'host_id':entry['host_id'],
        'role':entry.get('role','direct'),
        'state':body.get('state') or {},
        'meta':body.get('meta') or {},
        'last_seen':now(),
        'last_seen_ts':time.time(),
    }
    atomic_json(HOSTS/f"{entry['host_id']}.json",doc)
    count=regenerate()
    return {
        'ok':True,
        'registered':True,
        'subscription_refreshed':True,
        'node_count':count,
        'host_id':entry['host_id'],
        'host_token':entry['token'],
    }


class Handler(BaseHTTPRequestHandler):
    server_version='StaticResource/2.0'
    def log_message(self,*_): pass
    def send_bytes(self,status,data,ctype='text/plain; charset=utf-8'):
        self.send_response(status); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(len(data))); self.send_header('Cache-Control','private, no-store'); self.send_header('X-Robots-Tag','noindex, nofollow'); self.send_header('Profile-Update-Interval','24'); self.end_headers(); self.wfile.write(data)
    def json_body(self):
        try: return json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))).decode())
        except Exception: return None
    def do_GET(self):
        cfg=read_json(CFG,{}) or {}; path=urlparse(self.path).path; prefix=f"/r/{cfg.get('subscription_token','')}/"
        if path.startswith(prefix):
            kind=SHORT_PATHS.get(path[len(prefix):])
            if not kind: return self.send_bytes(404,b'Not Found\n')
            file=OUT/kind
            if not file.exists(): regenerate()
            return self.send_bytes(200,file.read_bytes(),'text/yaml; charset=utf-8' if kind=='clash' else 'text/plain; charset=utf-8')
        if path=='/health': return self.send_bytes(200,b'ok\n')
        if path=='/api/v1/hosts':
            if not secrets.compare_digest(auth_token(self),cfg.get('master_token','')): return self.send_bytes(403,b'Forbidden\n')
            return self.send_bytes(200,json.dumps({'hosts':active_hosts()},ensure_ascii=False,default=str).encode(),'application/json')
        return self.send_bytes(404,b'Not Found\n')
    def do_POST(self):
        cfg=read_json(CFG,{}) or {}; path=urlparse(self.path).path; body=self.json_body()
        if body is None: return self.send_bytes(400,b'Bad Request\n')
        with LOCK:
            registry=read_json(REGISTRY,{'hosts':[]}) or {'hosts':[]}
            if path=='/api/v1/register-direct':
                host_id=str(body.get('host_id') or '').strip(); role=str(body.get('role') or '')
                if role!='direct': return self.send_bytes(400,b'Direct role required\n')
                if not re.fullmatch(r'[A-Za-z0-9._-]{8,128}',host_id): return self.send_bytes(400,b'Bad host id\n')
                try: declared_ip=ipaddress.ip_address(str(body.get('public_ip') or '').strip())
                except ValueError: return self.send_bytes(400,b'Bad public ip\n')
                source_ip=request_ip(self)
                if source_ip is None or not declared_ip.is_global: return self.send_bytes(403,b'Public source required\n')
                if source_ip.version==declared_ip.version and source_ip!=declared_ip: return self.send_bytes(403,b'Source IP mismatch\n')
                backup('before-host-register'); entry=next((x for x in registry['hosts'] if x['host_id']==host_id),None)
                if entry is None: entry={'host_id':host_id,'token':secrets.token_urlsafe(32),'created_at':now()}; registry['hosts'].append(entry)
                entry.update(role='direct',hostname=str(body.get('hostname') or ''),auto_registered=True,source_ip=str(source_ip),updated_at=now()); atomic_json(REGISTRY,registry)
                result=finalize_registration(entry,body); backup('after-host-register')
                return self.send_bytes(200,json.dumps(result,ensure_ascii=False).encode(),'application/json')
            if path=='/api/v1/register':
                if not secrets.compare_digest(auth_token(self),cfg.get('master_token','')): return self.send_bytes(403,b'Forbidden\n')
                host_id=str(body.get('host_id') or '').strip(); role=str(body.get('role') or 'direct')
                if role not in ('center-relay','center','relay','direct','landing'): return self.send_bytes(400,b'Bad role\n')
                if not re.fullmatch(r'[A-Za-z0-9._-]{8,128}',host_id): return self.send_bytes(400,b'Bad host id\n')
                backup('before-host-register'); entry=next((x for x in registry['hosts'] if x['host_id']==host_id),None)
                if entry is None: entry={'host_id':host_id,'token':secrets.token_urlsafe(32),'created_at':now()}; registry['hosts'].append(entry)
                entry.update(role=role,hostname=str(body.get('hostname') or ''),updated_at=now()); atomic_json(REGISTRY,registry)
                result=finalize_registration(entry,body); backup('after-host-register')
                return self.send_bytes(200,json.dumps(result,ensure_ascii=False).encode(),'application/json')
            if path=='/api/v1/sync':
                token=auth_token(self); entry=next((x for x in registry.get('hosts',[]) if secrets.compare_digest(x.get('token',''),token)),None)
                if not entry or entry.get('host_id')!=body.get('host_id'): return self.send_bytes(403,b'Forbidden\n')
                backup('before-node-sync'); doc={'host_id':entry['host_id'],'role':entry.get('role','direct'),'state':body.get('state') or {},'meta':body.get('meta') or {},'last_seen':now(),'last_seen_ts':time.time()}; atomic_json(HOSTS/f"{entry['host_id']}.json",doc); count=regenerate(); backup('after-node-sync')
                return self.send_bytes(200,json.dumps({'ok':True,'node_count':count},ensure_ascii=False).encode(),'application/json')
        return self.send_bytes(404,b'Not Found\n')


def serve():
    cfg=read_json(CFG,{}) or {}; HOSTS.mkdir(parents=True,exist_ok=True); OUT.mkdir(parents=True,exist_ok=True)
    if not REGISTRY.exists(): atomic_json(REGISTRY,{'hosts':[]})
    regenerate(); ThreadingHTTPServer((cfg.get('listen_host','127.0.0.1'),int(cfg.get('listen_port',18081))),Handler).serve_forever()


if __name__=='__main__':
    parser=argparse.ArgumentParser(); sub=parser.add_subparsers(dest='cmd',required=True); sub.add_parser('serve'); sub.add_parser('regenerate'); args=parser.parse_args()
    if args.cmd=='serve': serve()
    else: print(regenerate())

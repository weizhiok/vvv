#!/usr/bin/env python3
import argparse, base64, hashlib, json, os, re, secrets, shutil, subprocess, sys, tarfile, tempfile, threading, time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse

CFG = Path('/etc/vvv-sub/config.json')
DATA = Path('/var/lib/vvv-sub')
HOSTS = DATA / 'hosts'
OUT = DATA / 'output'
BACKUPS = DATA / 'backups'
LOCK = threading.RLock()


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default


def atomic_json(path, obj, mode=0o600):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.chmod(tmp, mode); os.replace(tmp, path)
    finally:
        try: os.unlink(tmp)
        except FileNotFoundError: pass


def b64url(data: bytes):
    return base64.urlsafe_b64encode(data).decode().rstrip('=')


def b64std(text: str):
    return base64.b64encode(text.encode()).decode()


def protocol_name(base, proto):
    m = re.match(r'^([A-Z]{2})-(.+)$', base or '')
    if m: return f'{m.group(1)}-{proto}-{m.group(2)}'
    if re.match(r'^\d+\.\d+\.\d+\.\d+:\d+$', base or ''): return f'{proto}-{base}'
    return f'{base}-{proto}'


def loon_q(v): return '"' + str(v).replace('\\','\\\\').replace('"','\\"') + '"'
def loon_name(v): return str(v).replace('=','-').replace('\n',' ').replace('\r',' ')


def nodes_from_host(doc):
    state = doc.get('state') or {}
    role = doc.get('role','direct')
    mode = state.get('protocol_mode')
    ip = state.get('public_ip')
    port = int(state.get('listen_port') or 0)
    sni = state.get('sni')
    if not (mode and ip and port and sni): return []
    v = state.get('vless') or {}
    h = state.get('hy2') or {}
    nodes=[]
    def add_vless(base, uuid, udp=True, category='直连'):
        if not uuid or not v: return
        nodes.append({'id':hashlib.sha256((doc['host_id']+'|vless|'+base).encode()).hexdigest()[:24],
          'name':protocol_name(base,'VLESS'),'protocol':'vless','server':ip,'port':port,'uuid':uuid,
          'sni':sni,'public_key':((v.get('reality') or {}).get('public_key')),
          'short_id':((v.get('reality') or {}).get('short_id')),'udp':bool(udp),'category':category})
    def add_hy2(base, password, category='直连'):
        if not password or not h: return
        nodes.append({'id':hashlib.sha256((doc['host_id']+'|hy2|'+base).encode()).hexdigest()[:24],
          'name':protocol_name(base,'HY2'),'protocol':'hysteria2','server':ip,'port':port,
          'password':password,'sni':h.get('server_name'),'obfs_password':h.get('obfs_password'),
          'pin':h.get('certificate_pin_hex'),'udp':True,'category':category,'up_mbps':50,'down_mbps':50})
    base=state.get('direct_base_name') or f'{ip}:{port}'
    if mode in ('dual','vless'):
        add_vless(base, ((v.get('direct_user') or {}).get('uuid')), True, '本机直连')
    if mode in ('dual','hy2'):
        add_hy2(base, ((h.get('direct_user') or {}).get('password')), '本机直连')
    for r in state.get('relays') or []:
        rv=r.get('vless')
        rh=r.get('hy2')
        if rv: add_vless(r.get('name') or r.get('id'), rv.get('client_uuid'), True, 'VPS中转')
        if rh: add_hy2(r.get('name') or r.get('id'), rh.get('client_password'), 'VPS中转')
    for u in state.get('upstream_relays') or []:
        add_vless(u.get('name') or u.get('id'), u.get('client_uuid'), False, '动态代理')
    return nodes


def active_hosts(cfg):
    docs=[]; now_ts=time.time()
    for p in HOSTS.glob('*.json'):
        d=read_json(p,{})
        last=float(d.get('last_seen_ts') or 0)
        permanent=d.get('role') in ('center','relay','all')
        if d.get('disabled'): continue
        if not permanent and last and now_ts-last > 72*3600: continue
        docs.append(d)
    return docs


def all_nodes(cfg):
    nodes=[]; seen=set()
    for h in active_hosts(cfg):
        for n in nodes_from_host(h):
            if n['id'] in seen: continue
            seen.add(n['id']); nodes.append(n)
    return nodes


def vless_uri(n):
    params=[('encryption','none'),('flow','xtls-rprx-vision'),('security','reality'),('sni',n['sni']),('fp','chrome'),('pbk',n['public_key']),('sid',n['short_id']),('type','tcp'),('headerType','none')]
    return f"vless://{n['uuid']}@{n['server']}:{n['port']}?{urlencode(params)}#{quote(n['name'],safe='')}"


def hy2_uri(n):
    params=[('obfs','salamander'),('obfs-password',n['obfs_password']),('sni',n['sni']),('insecure','1')]
    if n.get('pin'): params.append(('pinSHA256',n['pin']))
    return f"hysteria2://{quote(n['password'],safe='')}@{n['server']}:{n['port']}/?{urlencode(params)}#{quote(n['name'],safe='')}"


def render_qx(nodes):
    lines=[]
    for n in nodes:
        if n['protocol']!='vless': continue
        lines.append(f"vless={n['server']}:{n['port']}, method=none, password={n['uuid']}, obfs=over-tls, obfs-host={n['sni']}, reality-base64-pubkey={n['public_key']}, reality-hex-shortid={n['short_id']}, vless-flow=xtls-rprx-vision, fast-open=false, udp-relay={'true' if n['udp'] else 'false'}, tag={n['name']}")
    return '\n'.join(lines)+'\n'


def render_loon(nodes):
    lines=[]
    for n in nodes:
        if n['protocol']=='vless':
            lines.append(f"{loon_name(n['name'])} = VLESS,{n['server']},{n['port']},{loon_q(n['uuid'])},transport=tcp,flow=xtls-rprx-vision,public-key={loon_q(n['public_key'])},short-id={n['short_id']},udp={'true' if n['udp'] else 'false'},over-tls=true,sni={n['sni']},skip-cert-verify=true")
        else:
            lines.append(f"{loon_name(n['name'])} = Hysteria2,{n['server']},{n['port']},{loon_q(n['password'])},skip-cert-verify=true,sni={n['sni']},udp=true,fast-open=true,salamander-password={loon_q(n['obfs_password'])}")
    return '\n'.join(lines)+'\n'


def render_uris(nodes):
    return '\n'.join(vless_uri(n) if n['protocol']=='vless' else hy2_uri(n) for n in nodes)+'\n'


def render_clash(nodes):
    lines=['mixed-port: 7890','allow-lan: false','mode: rule','log-level: info','proxies:']
    names=[]
    for n in nodes:
        names.append(n['name'])
        if n['protocol']=='vless':
            lines += [f'  - name: {json.dumps(n["name"],ensure_ascii=False)}','    type: vless',f'    server: {n["server"]}',f'    port: {n["port"]}',f'    uuid: {n["uuid"]}','    network: tcp',f'    udp: {str(n["udp"]).lower()}','    tls: true','    flow: xtls-rprx-vision','    encryption: ""',f'    servername: {n["sni"]}','    client-fingerprint: chrome','    skip-cert-verify: true','    reality-opts:',f'      public-key: {n["public_key"]}',f'      short-id: "{n["short_id"]}"']
        else:
            lines += [f'  - name: {json.dumps(n["name"],ensure_ascii=False)}','    type: hysteria2',f'    server: {n["server"]}',f'    port: {n["port"]}',f'    password: {json.dumps(n["password"])}','    up: "50 Mbps"','    down: "50 Mbps"','    obfs: salamander',f'    obfs-password: {json.dumps(n["obfs_password"])}',f'    sni: {n["sni"]}','    skip-cert-verify: true','    alpn: [h3]','    udp: true']
    lines += ['proxy-groups:','  - name: 全部节点','    type: select']
    if names:
        lines += [f'    proxies: [{", ".join(json.dumps(x,ensure_ascii=False) for x in names)}]']
    else: lines += ['    proxies: [DIRECT]']
    lines += ['  - name: 自动测速','    type: url-test',f'    proxies: [{", ".join(json.dumps(x,ensure_ascii=False) for x in names) if names else "DIRECT"}]','    url: https://www.gstatic.com/generate_204','    interval: 86400','rules:','  - MATCH,全部节点','']
    return '\n'.join(lines)


def regenerate(cfg):
    OUT.mkdir(parents=True,exist_ok=True)
    nodes=all_nodes(cfg)
    files={'clash':render_clash(nodes),'quantumultx':render_qx(nodes),'loon':render_loon(nodes),
           'shadowrocket':b64std(render_uris(nodes))+'\n','v2rayng':b64std(render_uris(nodes))+'\n'}
    for k,v in files.items():
        p=OUT/k; tmp=p.with_suffix('.tmp'); tmp.write_text(v,encoding='utf-8'); os.chmod(tmp,0o600); os.replace(tmp,p)
    atomic_json(OUT/'nodes.json',{'generated_at':now(),'count':len(nodes),'nodes':nodes})
    return len(nodes)


def make_backup(cfg):
    BACKUPS.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tar=Path(td)/'vvv-sub-backup.tar.gz'
        with tarfile.open(tar,'w:gz') as t:
            if CFG.exists(): t.add(CFG,arcname='etc/vvv-sub/config.json')
            if DATA.exists():
                for name in ('hosts','output'):
                    p=DATA/name
                    if p.exists(): t.add(p,arcname=f'var/lib/vvv-sub/{name}')
        enc=BACKUPS/'latest.enc'; tmp=BACKUPS/'.latest.enc.tmp'
        subprocess.run(['openssl','enc','-aes-256-cbc','-salt','-pbkdf2','-pass',f"pass:{cfg['recovery_password']}",'-in',str(tar),'-out',str(tmp)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        os.chmod(tmp,0o600); os.replace(tmp,enc)
        meta={'created_at':now(),'sha256':hashlib.sha256(enc.read_bytes()).hexdigest(),'size':enc.stat().st_size}
        atomic_json(BACKUPS/'latest.json',meta)


def auth_token(handler):
    h=handler.headers.get('Authorization','')
    return h[7:] if h.startswith('Bearer ') else ''


class Handler(BaseHTTPRequestHandler):
    server_version='StaticResource/1.0'
    def log_message(self,*a): pass
    def send_bytes(self,status,data,ctype='text/plain; charset=utf-8',headers=None):
        self.send_response(status); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(len(data))); self.send_header('Cache-Control','private, no-store'); self.send_header('X-Robots-Tag','noindex, nofollow'); self.send_header('Profile-Update-Interval','24')
        for k,v in (headers or {}).items(): self.send_header(k,v)
        self.end_headers(); self.wfile.write(data)
    def json_body(self):
        try:
            n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n).decode())
        except Exception: return None
    def do_GET(self):
        cfg=read_json(CFG,{})
        p=urlparse(self.path).path
        prefix=f"/r/{cfg.get('subscription_token','')}/"
        if p.startswith(prefix):
            kind=p[len(prefix):]
            if kind not in ('clash','quantumultx','loon','shadowrocket','v2rayng'):
                return self.send_bytes(404,b'Not Found\n')
            f=OUT/kind
            if not f.exists(): regenerate(cfg)
            ctype='text/yaml; charset=utf-8' if kind=='clash' else 'text/plain; charset=utf-8'
            return self.send_bytes(200,f.read_bytes(),ctype)
        if p=='/api/v1/backup':
            tok=auth_token(self); reg=read_json(DATA/'registry.json',{})
            host=next((x for x in reg.get('hosts',[]) if secrets.compare_digest(x.get('token',''),tok)),None)
            if not host or host.get('role') not in ('relay','all'): return self.send_bytes(403,b'Forbidden\n')
            f=BACKUPS/'latest.enc'
            if not f.exists(): make_backup(cfg)
            return self.send_bytes(200,f.read_bytes(),'application/octet-stream')
        if p=='/health': return self.send_bytes(200,b'ok\n')
        return self.send_bytes(404,b'Not Found\n')
    def do_POST(self):
        cfg=read_json(CFG,{})
        p=urlparse(self.path).path; body=self.json_body()
        if body is None: return self.send_bytes(400,b'Bad Request\n')
        with LOCK:
            reg=read_json(DATA/'registry.json',{'hosts':[]})
            if p=='/api/v1/register':
                if not secrets.compare_digest(auth_token(self),cfg.get('master_token','')): return self.send_bytes(403,b'Forbidden\n')
                host_id=str(body.get('host_id') or '').strip(); role=str(body.get('role') or 'direct')
                if not re.fullmatch(r'[a-zA-Z0-9._-]{8,128}',host_id): return self.send_bytes(400,b'Bad host id\n')
                ent=next((x for x in reg['hosts'] if x['host_id']==host_id),None)
                if ent is None:
                    ent={'host_id':host_id,'token':secrets.token_urlsafe(32),'role':role,'created_at':now()}; reg['hosts'].append(ent)
                else: ent['role']=role
                ent['updated_at']=now(); atomic_json(DATA/'registry.json',reg)
                payload=json.dumps({'host_id':host_id,'host_token':ent['token']},ensure_ascii=False).encode()
                return self.send_bytes(200,payload,'application/json')
            if p=='/api/v1/sync':
                tok=auth_token(self); ent=next((x for x in reg.get('hosts',[]) if secrets.compare_digest(x.get('token',''),tok)),None)
                if ent is None: return self.send_bytes(403,b'Forbidden\n')
                if body.get('host_id')!=ent['host_id']: return self.send_bytes(403,b'Forbidden\n')
                doc={'host_id':ent['host_id'],'role':ent.get('role','direct'),'last_seen':now(),'last_seen_ts':time.time(),'state':body.get('state') or {},'meta':body.get('meta') or {}}
                atomic_json(HOSTS/f"{ent['host_id']}.json",doc); ent['updated_at']=now(); atomic_json(DATA/'registry.json',reg)
                count=regenerate(cfg); make_backup(cfg)
                payload=json.dumps({'ok':True,'node_count':count,'updated_at':now()},ensure_ascii=False).encode()
                return self.send_bytes(200,payload,'application/json')
        return self.send_bytes(404,b'Not Found\n')


def serve():
    cfg=read_json(CFG,{})
    host=cfg.get('listen_host','127.0.0.1'); port=int(cfg.get('listen_port',18081))
    DATA.mkdir(parents=True,exist_ok=True); HOSTS.mkdir(parents=True,exist_ok=True); OUT.mkdir(parents=True,exist_ok=True); BACKUPS.mkdir(parents=True,exist_ok=True)
    regenerate(cfg)
    ThreadingHTTPServer((host,port),Handler).serve_forever()


def restore(path,password):
    path=Path(path)
    with tempfile.TemporaryDirectory() as td:
        tar=Path(td)/'restore.tar.gz'
        subprocess.run(['openssl','enc','-d','-aes-256-cbc','-pbkdf2','-pass',f'pass:{password}','-in',str(path),'-out',str(tar)],check=True)
        with tarfile.open(tar,'r:gz') as t: t.extractall('/')
    print('恢复完成。')

if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('command',choices=['serve','regenerate','backup','restore']); ap.add_argument('arg1',nargs='?'); ap.add_argument('arg2',nargs='?'); a=ap.parse_args()
    cfg=read_json(CFG,{})
    if a.command=='serve': serve()
    elif a.command=='regenerate': print(regenerate(cfg))
    elif a.command=='backup': make_backup(cfg)
    elif a.command=='restore': restore(a.arg1,a.arg2)

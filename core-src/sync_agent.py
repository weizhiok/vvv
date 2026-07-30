#!/usr/bin/env python3
import argparse, base64, hashlib, json, os, platform, socket, sys, time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

CFG=Path('/etc/vvv/client.json'); STATE=Path('/etc/jp-relay/state.json')

def read(p,d=None):
    try:return json.loads(Path(p).read_text(encoding='utf-8'))
    except:return d

def atomic(p,o):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);q=p.with_suffix('.tmp');q.write_text(json.dumps(o,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');os.chmod(q,0o600);os.replace(q,p)

def post(url,token,obj):
    data=json.dumps(obj,ensure_ascii=False).encode();r=Request(url,data=data,method='POST',headers={'Content-Type':'application/json','Authorization':'Bearer '+token,'User-Agent':'VVV-Sync/1.0'})
    with urlopen(r,timeout=25) as x:return json.loads(x.read().decode())

def decode_code(code):
    code=code.strip(); raw=code.split('.',1)[1] if code.startswith('VVV1.') else code
    raw += '='*((4-len(raw)%4)%4); return json.loads(base64.urlsafe_b64decode(raw).decode())

def stable_id():
    seed='|'.join([socket.gethostname(),read('/etc/machine-id','') if False else '',platform.machine()])
    try: seed+='|'+Path('/etc/machine-id').read_text().strip()
    except: pass
    return hashlib.sha256(seed.encode()).hexdigest()[:32]

def register(code,role):
    c=decode_code(code); base=c['base_url'].rstrip('/'); master=c['master_token']; host_id=stable_id()
    resp=post(base+'/api/v1/register',master,{'host_id':host_id,'role':role})
    cfg={'base_url':base,'host_id':host_id,'host_token':resp['host_token'],'role':role,'registered_at':time.time()};atomic(CFG,cfg);return cfg

def sync():
    cfg=read(CFG); state=read(STATE,{})
    if not cfg: raise SystemExit('尚未配置订阅同步。')
    resp=post(cfg['base_url'].rstrip('/')+'/api/v1/sync',cfg['host_token'],{'host_id':cfg['host_id'],'state':state,'meta':{'hostname':socket.gethostname(),'role':cfg['role'],'timestamp':time.time()}})
    cfg['last_sync']=time.time();cfg['last_result']=resp;atomic(CFG,cfg);print(json.dumps(resp,ensure_ascii=False))

def pull_backup(dest):
    cfg=read(CFG); dest=Path(dest);dest.mkdir(parents=True,exist_ok=True)
    req=Request(cfg['base_url'].rstrip('/')+'/api/v1/backup',headers={'Authorization':'Bearer '+cfg['host_token'],'User-Agent':'VVV-Backup/1.0'})
    with urlopen(req,timeout=60) as r:data=r.read()
    name=time.strftime('vvv-center-%Y%m%d-%H%M%S.enc'); p=dest/name;p.write_bytes(data);os.chmod(p,0o600)
    latest=dest/'latest.enc';tmp=dest/'.latest.tmp';tmp.write_bytes(data);os.chmod(tmp,0o600);os.replace(tmp,latest)
    files=sorted(dest.glob('vvv-center-*.enc'));[x.unlink() for x in files[:-30]]
    print(p)

if __name__=='__main__':
    ap=argparse.ArgumentParser();sp=ap.add_subparsers(dest='cmd',required=True)
    r=sp.add_parser('register');r.add_argument('code');r.add_argument('role')
    sp.add_parser('sync');b=sp.add_parser('pull-backup');b.add_argument('dest')
    a=ap.parse_args()
    if a.cmd=='register': register(a.code,a.role);sync()
    elif a.cmd=='sync':sync()
    else:pull_backup(a.dest)

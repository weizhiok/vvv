#!/usr/bin/env python3
from pathlib import Path
import re

path=Path('core-src/bootstrap.sh')
text=path.read_text(encoding='utf-8')

needle='center_config_valid() {\n'
if text.count(needle)!=1:
    raise SystemExit('center_config_valid anchor missing')
migration=r'''migrate_center_config_if_needed() {
  [[ -s "$CENTER_CFG" ]] || return 0
  [[ "$(json_value "$CENTER_CFG" schema 0)" == 2 ]] || return 0
  local suffix
  suffix="$(python3 - <<'PY_SUFFIX'
import secrets,string
alphabet=string.ascii_letters+string.digits
print(''.join(secrets.choice(alphabet) for _ in range(8)))
PY_SUFFIX
)"
  cp -a "$CENTER_CFG" /etc/vvv-sub/config.schema2-backup.json
  python3 - "$CENTER_CFG" "$suffix" <<'PY_MIGRATE_CENTER'
import json,os,sys,tempfile
path,suffix=sys.argv[1:]
with open(path,encoding='utf-8') as f:
    obj=json.load(f)
base=str(obj.get('base_url','')).rstrip('/')
mode=obj.get('mode') if obj.get('mode') in ('domain','ip') else ('domain' if obj.get('domain') else 'ip')
obj['schema']=3
obj['address_mode']=mode
obj['transport_mode']='direct-https'
obj['subscription_suffix']=suffix
obj['subscription_url']=base+'/'+suffix
obj.pop('mode',None)
obj.pop('subscription_token',None)
fd,tmp=tempfile.mkstemp(prefix='.config-migrate.',dir=os.path.dirname(path))
with os.fdopen(fd,'w',encoding='utf-8') as f:
    json.dump(obj,f,ensure_ascii=False,indent=2); f.write('\n')
os.chmod(tmp,0o600); os.replace(tmp,path)
PY_MIGRATE_CENTER
  touch /etc/vvv-sub/.schema3-migrated
  echo "检测到旧版订阅中心配置，已原地升级为统一订阅地址；令牌、注册主机、节点、备份和证书均保留。"
  echo "新的8位随机订阅后缀：$suffix"
}

'''
text=text.replace(needle,migration+needle,1)

pattern=r'^center_complete\(\) \{.*?^\}\n'
replacement=r'''center_complete() {
  center_config_valid &&
  [[ -s /etc/vvv-sub/registration.code ]] &&
  [[ -x /usr/local/sbin/vvv-center ]] &&
  [[ -x /usr/local/lib/vvv/sub_center.py ]] &&
  [[ -f /etc/systemd/system/vvv-sub.service ]] &&
  [[ -f /etc/systemd/system/caddy.service ]] &&
  [[ -s /etc/caddy/Caddyfile ]]
}
'''
text,count=re.subn(pattern,replacement,text,count=1,flags=re.M|re.S)
if count!=1:
    raise SystemExit('center_complete replacement failed')

old='''  if (( changed == 1 )); then
    echo "检测到订阅中心程序更新，保留全部数据并重新启动内部服务。"
    python3 /usr/local/lib/vvv/client_adapters.py >/dev/null
    timeout 75 systemctl restart vvv-sub.service
  fi
}'''
new='''  if (( changed == 1 )); then
    echo "检测到订阅中心程序更新，保留全部数据并重新启动内部服务。"
    python3 /usr/local/lib/vvv/client_adapters.py >/dev/null
    timeout 75 systemctl restart vvv-sub.service
  fi
  if [[ -f /etc/vvv-sub/.schema3-migrated ]]; then
    echo "正在将旧四路径入口无损切换为新的统一订阅地址。"
    bash /usr/local/lib/vvv/center_transport.sh reapply || fail "旧订阅中心配置已迁移，但统一入口切换失败；原数据和schema2备份均已保留。"
    rm -f /etc/vvv-sub/.schema3-migrated
  fi
}'''
if text.count(old)!=1:
    raise SystemExit('refresh migration hook anchor missing')
text=text.replace(old,new,1)

old='''VVV_CF_TUNNEL_TOKEN=""

show_install_menu'''
new='''VVV_CF_TUNNEL_TOKEN=""

migrate_center_config_if_needed
show_install_menu'''
if text.count(old)!=1:
    raise SystemExit('migration call anchor missing')
text=text.replace(old,new,1)
path.write_text(text,encoding='utf-8')

# Permanent regression assertion.
test=Path('tests/conformance.py')
s=test.read_text(encoding='utf-8')
anchor="    require('refresh_center_runtime_code' in bootstrap and 'center_manager.sh' in bootstrap, '重复安装不会刷新中心管理器')\n"
insert=anchor+"    require('migrate_center_config_if_needed' in bootstrap and 'config.schema2-backup.json' in bootstrap, '旧schema2订阅中心不会原地迁移')\n"
if s.count(anchor)!=1:
    raise SystemExit('conformance migration anchor missing')
test.write_text(s.replace(anchor,insert,1),encoding='utf-8')

print('CENTER SCHEMA MIGRATION PATCH APPLIED')

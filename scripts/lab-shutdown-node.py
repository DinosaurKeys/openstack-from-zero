#!/usr/bin/env python3
"""Fixed observations and network helper stops; no OS poweroff capability."""
import argparse
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tarfile
import re
import time

NODES={'node1':'192.168.0.200','node2':'192.168.0.201','node3':'192.168.0.202'}
UUID_PATTERN=r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'

def network_child(name):
    return re.fullmatch(r'(?:neutron_l3_agent_(?:haproxy|keepalived)_qrouter-|neutron_dhcp_agent_(?:dnsmasq|haproxy)_qdhcp-)'+UUID_PATTERN,name) is not None

def run(args, binary=False):
    p=subprocess.run(args,capture_output=True,text=not binary,timeout=90)
    if p.returncode:
        reason='object no longer exists' if b'No such' in (p.stderr if binary else p.stderr.encode()) else 'command returned nonzero'
        raise RuntimeError(' '.join(args[:2])+': '+reason+'; no retry')
    return p.stdout if binary else p.stdout.strip()

def guard(node):
    if os.geteuid()!=0 or os.environ.get('SUDO_USER')!='joe' or socket.gethostname().split('.')[0]!=node:
        raise RuntimeError('Expected node identity and sudo from joe')
    addresses=json.loads(run(['ip','-j','address','show','dev','enp0s31f6']))
    if not any(a.get('local')==NODES[node] and a.get('prefixlen')==24 for r in addresses for a in r.get('addr_info',[])):
        raise RuntimeError('Management identity mismatch')

def running():return run(['docker','ps','--format','{{.Names}}']).splitlines()

def stopped(name):
    state=json.loads(run(['docker','inspect','--format','{{json .State}}',name]))
    if state.get('Running') is not False or state.get('Status')!='exited' or state.get('ExitCode')!=0 or state.get('OOMKilled'):
        raise RuntimeError(name+': clean exit not confirmed; no poweroff')
    return {'exit_code':0,'finished':state.get('FinishedAt')}

def database():
    result=stopped('mariadb')
    archive=run(['docker','cp','mariadb:/var/lib/mysql/grastate.dat','-'],binary=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        members=tar.getmembers()
        if len(members)!=1 or not members[0].isfile() or members[0].size>4096:
            raise RuntimeError('Unexpected Galera state file')
        raw=tar.extractfile(members[0]).read().decode()
    values={}
    for line in raw.splitlines():
        if ':' in line and not line.lstrip().startswith('#'):
            key,value=line.split(':',1);values[key.strip()]=value.strip()
    import uuid
    result.update(uuid=str(uuid.UUID(values['uuid'])),seqno=int(values['seqno']),safe_to_bootstrap=int(values['safe_to_bootstrap']))
    if result['seqno']<0 or result['safe_to_bootstrap'] not in (0,1):raise RuntimeError('Galera shutdown position unavailable')
    return result

def stop_child(name):
    fmt='{"id":{{json .Id}},"name":{{json .Name}},"auto_remove":{{json .HostConfig.AutoRemove}}}'
    before=json.loads(run(['docker','inspect','--format',fmt,name]))
    cid=before.get('id','')
    if before.get('name')!='/'+name or not re.fullmatch('[0-9a-f]{64}',cid) or type(before.get('auto_remove')) is not bool:
        raise RuntimeError('Network helper identity unavailable; no stop')
    since=str(int(time.time())-2)
    run(['docker','stop','--time','60',cid])
    if before['auto_remove']:
        # AutoRemove containers cannot be inspected after a successful stop.
        raw=run(['docker','events','--since',since,'--until',str(int(time.time())+1),
          '--filter','type=container','--filter','container='+cid,'--format','{{json .}}'])
        events=[json.loads(line) for line in raw.splitlines() if line.strip()]
        if any(e.get('Actor',{}).get('ID')!=cid or e.get('Actor',{}).get('Attributes',{}).get('name')!=name for e in events):
            raise RuntimeError('Network helper event identity mismatch')
        deaths=[e for e in events if e.get('Action')=='die']
        if len(deaths)!=1 or deaths[0]['Actor']['Attributes'].get('exitCode') not in ('0','143') or not any(e.get('Action')=='destroy' for e in events) or any(e.get('Action')=='oom' for e in events):
            raise RuntimeError('Removed helper clean exit not confirmed; no further shutdown')
    else:
        state=json.loads(run(['docker','inspect','--format','{{json .State}}',cid]))
        if state.get('Running') is not False or state.get('Status')!='exited' or state.get('ExitCode') not in (0,143) or state.get('OOMKilled'):
            raise RuntimeError('Network helper clean stop unavailable; no further shutdown')

def action(node,mode):
    guard(node)
    if mode=='inspect':
        scope={};exec(__import__('base64').b64decode(HEALTH_SOURCE),scope)
        host=scope['collect'](node)
        domains=run(['docker','exec','nova_libvirt','virsh','--readonly','list','--uuid']).splitlines()
        return {'host':host,'active_domains':domains,'running':running()}
    if mode=='running':return {'running':running()}
    if mode=='partial':
        scope={};exec(__import__('base64').b64decode(HEALTH_SOURCE),scope)
        current=running();support={}
        for name in ('mariadb','rabbitmq','proxysql','haproxy','keepalived'):
            fmt='{"running":{{json .State.Running}},"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}"none"{{end}}}'
            support[name]=json.loads(run(['docker','inspect','--format',fmt,name]))
        processes=run(['ps','-e','-o','comm=']).splitlines()
        return {'running':current,'support':support,'database':scope['galera'](NODES[node]),
          'broker':scope['broker'](),'vm_processes':sum(p.strip().startswith(('qemu-system','qemu-kvm')) or p.strip()=='kvm' for p in processes)}
    if mode=='network-children':
        # Kolla must first stop BOTH L3 and DHCP agents/other clients, so their
        # process monitors cannot respawn these separately managed children.
        current=running()
        names=[n for n in current if network_child(n)]
        if set(current)-set(names)-{'mariadb','rabbitmq','proxysql','haproxy','keepalived'}:
            raise RuntimeError('Cloud clients/agents or unknown containers still running; no network helper stop')
        for name in names:
            stop_child(name)
        return {'network_children_stopped':len(names)}
    if mode=='broker':return stopped('rabbitmq')
    if mode=='database':return database()
    raise RuntimeError('Unapproved action')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--node',choices=NODES,required=True)
    parser.add_argument('--action',choices=('inspect','partial','running','network-children','broker','database'),required=True)
    args=parser.parse_args()
    try:
        print(json.dumps({'node':args.node,'ok':True,'value':action(args.node,args.action)}))
    except Exception as error:
        print(json.dumps({'node':args.node,'ok':False,'error':str(error) if isinstance(error,RuntimeError) else 'Shutdown observation unavailable'}))
        raise SystemExit(1)

#!/usr/bin/env python3
"""Narrow node helper: inspect API VIP or stop/start one identified API Keepalived container."""
import argparse
import base64
import configparser
import json
import os
from pathlib import Path
import stat
import re
import socket
import subprocess
import time

NODES={'node1':'192.168.0.200','node2':'192.168.0.201','node3':'192.168.0.202'}
VIP='192.168.0.151'
IMAGE='quay.io/openstack.kolla/keepalived:2026.2-ubuntu-noble'
UNIT='kolla-keepalived-container.service'


def run(args,timeout=20):
    p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if p.returncode:raise RuntimeError('Node command failed: '+args[0]+' (exit '+str(p.returncode)+')')
    return p.stdout.strip()


def inspect(name):
    fmt='{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Config.Image}},"state":{{json .State}}}'
    value=json.loads(run(['docker','inspect','--format',fmt,name]))
    state=value.pop('state')
    value.update(running=state.get('Running') is True,status=state.get('Status'),health=state.get('Health',{}).get('Status','not configured'))
    return value


def validate_unit(text):
    parser=configparser.ConfigParser(interpolation=None);parser.read_string(text)
    service=parser['Service']
    if set(service)!={'execstart','execstop','restart','restartsec','successexitstatus'}:
        raise RuntimeError('Unreviewed service hooks/options')
    stop=re.fullmatch(r'/usr/bin/docker stop keepalived -t ([0-9]+)',service.get('ExecStop',''))
    if service.get('ExecStart')!='/usr/bin/docker start -a keepalived':
        raise RuntimeError('Keepalived ExecStart differs from the pinned service command')
    if not stop:raise RuntimeError('Keepalived ExecStop structure differs from the pinned service command')
    if int(stop.group(1)) not in (10,60):
        raise RuntimeError('Unreviewed Keepalived stop timeout: '+stop.group(1)+' seconds; expected pinned 60 or module fallback 10')
    return True


def unit_state():
    properties=dict(line.split('=',1) for line in run(['systemctl','show',UNIT,'--property=LoadState,ActiveState,SubState,FragmentPath,DropInPaths','--no-pager']).splitlines() if '=' in line)
    expected='/etc/systemd/system/'+UNIT
    if properties.get('LoadState')!='loaded' or properties.get('FragmentPath')!=expected or properties.get('DropInPaths'):
        raise RuntimeError('Keepalived service definition/overrides require review')
    path=Path(expected);info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022:
        raise RuntimeError('Unsafe Keepalived service file')
    validate_unit(path.read_text())
    return {'name':UNIT,'active':properties.get('ActiveState'),'substate':properties.get('SubState')}


def collect(node):
    addresses=json.loads(run(['ip','-j','address','show']))
    interface=next((a for a in addresses if a.get('ifname')=='enp0s31f6'),{})
    if not any(a.get('local')==NODES[node] and a.get('prefixlen')==24 for a in interface.get('addr_info',[])):
        raise RuntimeError('Management address does not match the approved node')
    locations=[a['ifname'] for a in addresses if any(v.get('local')==VIP for v in a.get('addr_info',[]))]
    if locations not in ([],['enp0s31f6']):raise RuntimeError('Unexpected VIP location or duplicate address')
    keep=inspect('keepalived');haproxy=inspect('haproxy')
    if keep.get('name')!='/keepalived' or keep.get('image')!=IMAGE or not re.fullmatch(r'[a-f0-9]{64}',keep.get('id','')):
        raise RuntimeError('Container is not the approved API Keepalived image/identity')
    return {'node':node,'vip_owner':bool(locations),'keepalived':keep,'haproxy':haproxy,'unit':unit_state()}


def execute(node,action,container_id=None):
    if node not in NODES or os.geteuid()!=0 or os.environ.get('SUDO_USER')!='joe' or socket.gethostname().split('.')[0]!=node:
        raise RuntimeError('Expected approved node identity and sudo from joe')
    if action not in ('observe','full','stop','start'):raise RuntimeError('Unknown node operation')
    before=collect(node)
    if action in ('stop','start'):
        if not re.fullmatch(r'[a-f0-9]{64}',container_id or '') or before['keepalived']['id']!=container_id:
            raise RuntimeError('Keepalived container identity changed; no mutation')
        if action=='stop':
            if not before['vip_owner'] or not before['keepalived']['running'] or not before['haproxy']['running'] or before['unit']['active']!='active':
                raise RuntimeError('Stop requires this healthy current VIP owner')
            run(['systemctl','stop',UNIT],timeout=90)
        elif not before['keepalived']['running'] or before['unit']['active']!='active':
            run(['systemctl','start',UNIT],timeout=90)
        deadline=time.monotonic()+15
        while True:
            after=collect(node)
            if after['keepalived']['id']!=container_id:raise RuntimeError('Container identity changed during service operation')
            if after['keepalived']['running']==(action=='start') and after['unit']['active']==('active' if action=='start' else 'inactive'):return after
            if time.monotonic()>=deadline:raise RuntimeError('Requested Keepalived service/container state not confirmed')
            time.sleep(0.5)
    if action=='full':
        encoded=globals().get('BASELINE_SOURCE')
        if not encoded:raise RuntimeError('Read-only baseline collector not supplied')
        namespace={'__name__':'baseline_collector'}
        exec(compile(base64.b64decode(encoded),'phase3-deploy-node.py','exec'),namespace)
        baseline=namespace['collect'](node)
        return dict(before,baseline=baseline)
    return before


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--node',choices=NODES,required=True)
    parser.add_argument('--action',choices=['observe','full','stop','start'],required=True);parser.add_argument('--container-id')
    args=parser.parse_args()
    try:result=dict(execute(args.node,args.action,args.container_id),passed=True)
    except Exception as error:result={'passed':False,'error':str(error) if isinstance(error,RuntimeError) else type(error).__name__+'; node operation incomplete'}
    print(json.dumps(result));return 0 if result['passed'] else 1

if __name__=='__main__':raise SystemExit(main())

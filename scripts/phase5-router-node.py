#!/usr/bin/env python3
"""Inspect or lower/restore exactly one router HA virtual interface, never a host NIC."""
import argparse
import base64
import hashlib
import ipaddress
import json
import os
import re
import socket
import subprocess
import uuid

NODES={'node1':'192.168.0.200','node2':'192.168.0.201','node3':'192.168.0.202'}
IMAGE='quay.io/openstack.kolla/neutron-l3-agent:2026.2-ubuntu-noble'


def run(args):
    p=subprocess.run(args,capture_output=True,text=True,timeout=20)
    if p.returncode:raise RuntimeError('Router node command failed: '+args[0]+' (exit '+str(p.returncode)+')')
    return p.stdout.strip()


# Runs inside the existing L3 container; emit selected facts, never configuration/password text.
READER='''import configparser,hashlib,importlib.metadata,json,re,sys
from pathlib import Path
router,interface=sys.argv[1:]
cfg=configparser.ConfigParser(interpolation=None)
if len(cfg.read(['/etc/neutron/neutron.conf','/etc/neutron/l3_agent.ini']))!=2:raise RuntimeError('Expected Neutron configuration unavailable')
state=cfg.defaults().get('state_path')
if state!='/var/lib/neutron/kolla':raise RuntimeError('Unreviewed Neutron state directory')
ha=cfg.defaults().get('ha_confs_path',state+'/ha_confs').replace('${state_path}',state).replace('$state_path',state)
if ha!=state+'/ha_confs':raise RuntimeError('Unreviewed HA configuration directory')
path=Path(ha)/router/'keepalived.conf'
if path.is_symlink() or not path.is_file():raise RuntimeError('Expected regular per-router Keepalived configuration')
raw=path.read_bytes();text=re.sub(r'#[^\\n]*','',raw.decode())
blocks=re.findall(r'\\btrack_interface\\s*\\{([^{}]*)\\}',text,re.S)
tracked=len(blocks)==1 and [line.strip() for line in blocks[0].splitlines() if line.strip()]==[interface]
instances=len(re.findall(r'\\bvrrp_instance\\s+',text))
print(json.dumps({'neutron_version':importlib.metadata.version('neutron'),'config_sha256':hashlib.sha256(raw).hexdigest(),'tracks_HA_interface':tracked,'vrrp_instances':instances,'ha_virtual_ips':[line.split()[0].split('/')[0] for block in re.findall(r'\\bvirtual_ipaddress\\s*\\{([^{}]*)\\}',text,re.S) for line in block.splitlines() if len(line.split())>=3 and line.split()[1:3]==['dev',interface]]}))
'''


def valid_context(context):
    router=context.get('router_id','')
    if str(uuid.UUID(router))!=router:raise RuntimeError('Invalid router UUID')
    ports=context.get('ha_ports',[])
    if len(ports)!=3 or len({p['id'] for p in ports})!=3 or len({p['mac'].lower() for p in ports})!=3:raise RuntimeError('Expected three unique HA ports')
    for port in ports:
        if str(uuid.UUID(port['id']))!=port['id'] or not re.fullmatch(r'(?:[a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}',port['mac']):raise RuntimeError('Invalid HA port identity')
        if not port.get('ips') or not all(ipaddress.IPv4Address(ip).is_link_local for ip in port['ips']):raise RuntimeError('HA port must use observed link-local addresses')
    for key in ('gateway','external_ip'):ipaddress.IPv4Address(context[key])


def select_link(links,context):
    matches=[(link,port) for link in links for port in context['ha_ports'] if link.get('address','').lower()==port['mac'].lower()]
    if len(matches)!=1:raise RuntimeError('Cannot identify exactly one HA link by Neutron port MAC')
    link,port=matches[0]
    if link.get('ifname')!=('ha-'+port['id'])[:14] or type(link.get('ifindex')) is not int:raise RuntimeError('HA interface name/index does not match the observed port')
    ips={a.get('local') for a in link.get('addr_info',[]) if a.get('family')=='inet'}
    if not set(port['ips']).issubset(ips) or not all(ipaddress.IPv4Address(ip).is_link_local for ip in ips):raise RuntimeError('HA link addresses differ from the observed link-local network')
    return link,port


def collect(node,context):
    valid_context(context)
    host=json.loads(run(['ip','-j','address','show','dev','enp0s31f6']))
    if len(host)!=1 or not any(a.get('local')==NODES[node] for a in host[0].get('addr_info',[])):raise RuntimeError('Management identity differs')
    fmt='{"id":{{json .Id}},"image":{{json .Config.Image}},"running":{{json .State.Running}},"network_mode":{{json .HostConfig.NetworkMode}}}'
    container=json.loads(run(['docker','inspect','--format',fmt,'neutron_l3_agent']))
    if container.get('image')!=IMAGE or container.get('running') is not True or container.get('network_mode')!='host' or not re.fullmatch(r'[a-f0-9]{64}',container.get('id','')):
        raise RuntimeError('Existing L3 container identity/state differs')
    namespace='qrouter-'+context['router_id']
    info=os.stat('/run/netns/'+namespace)
    links=json.loads(run(['ip','netns','exec',namespace,'ip','-j','address','show']))
    link,port=select_link(links,context)
    data=json.loads(run(['docker','exec','--user','root','neutron_l3_agent','python3','-c',READER,context['router_id'],link['ifname']]))
    ha_ips={a.get('local') for a in link.get('addr_info',[]) if a.get('family')=='inet'}
    if not ha_ips.issubset(set(port['ips'])|set(data.get('ha_virtual_ips',[]))):raise RuntimeError('HA interface has an address not declared by Neutron or its Keepalived configuration')
    if data.get('tracks_HA_interface') is not True or data.get('vrrp_instances')!=1:raise RuntimeError('Router Keepalived does not uniquely track the selected HA interface')
    addresses={a.get('local') for l in links for a in l.get('addr_info',[]) if a.get('family')=='inet'}
    return {'node':node,'namespace':namespace,'namespace_identity':[info.st_dev,info.st_ino],'container_id':container['id'],
        'interface':link['ifname'],'ifindex':link['ifindex'],'mac':link['address'].lower(),'ha_port_id':port['id'],
        'ha_up':'UP' in link.get('flags',[]),'owns_gateway':context['gateway'] in addresses,'owns_external':context['external_ip'] in addresses,
        'keepalived':data}


def identity(observation):
    return {key:observation[key] for key in ('node','namespace','namespace_identity','container_id','interface','ifindex','mac','ha_port_id','keepalived')}


def execute(node,action,context,expected=None):
    if node not in NODES or os.geteuid()!=0 or os.environ.get('SUDO_USER')!='joe' or socket.gethostname().split('.')[0]!=node:raise RuntimeError('Expected node identity and sudo from joe')
    if action not in ('observe','down','up'):raise RuntimeError('Unknown router action')
    before=collect(node,context)
    if action=='observe':return before
    if not expected or identity(before)!=expected:raise RuntimeError('Prepared router/namespace/interface/config identity changed; no link mutation')
    if action=='down':
        if not before['ha_up'] or not before['owns_gateway'] or not before['owns_external']:raise RuntimeError('Down requires the current active router forwarding addresses and an UP HA link')
    if before['ha_up']!=(action=='up'):
        run(['ip','netns','exec',before['namespace'],'ip','link','set','dev',before['interface'],action])
    after=collect(node,context)
    if identity(after)!=expected or after['ha_up']!=(action=='up'):raise RuntimeError('Requested HA interface state not confirmed')
    return after


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--node',required=True,choices=NODES);parser.add_argument('--action',required=True,choices=['observe','down','up']);parser.add_argument('--context',required=True);args=parser.parse_args()
    try:
        payload=json.loads(base64.b64decode(args.context,validate=True));result=dict(execute(args.node,args.action,payload['context'],payload.get('expected')),passed=True)
    except Exception as error:result={'passed':False,'error':str(error) if isinstance(error,RuntimeError) else type(error).__name__+'; router observation/action incomplete'}
    print(json.dumps(result));return 0 if result['passed'] else 1

if __name__=='__main__':raise SystemExit(main())

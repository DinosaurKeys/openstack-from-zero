#!/usr/bin/env python3
"""Restart only an explicitly approved, identified API container via its Kolla unit."""
import argparse
import base64
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import time

ALLOWED={'node1':('placement_api','nova_api','nova_metadata','neutron_server','cinder_api'),
         'node2':('nova_api','nova_metadata','neutron_server','cinder_api'),
         'node3':('placement_api','nova_api','nova_metadata','neutron_server','cinder_api')}
CONTROL_ORDER=('placement_api','nova_api','nova_metadata','neutron_server','cinder_api',
               'nova_conductor','nova_scheduler','neutron_rpc_server','neutron_periodic_worker')
CONTROL_ALLOWED={node:CONTROL_ORDER for node in ALLOWED}
PROFILES={'approved-api':ALLOWED,'reboot-control':CONTROL_ALLOWED}


def run(args,timeout=25):
    result=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if result.returncode:raise RuntimeError(args[0]+' query/operation exited '+str(result.returncode))
    return result.stdout.strip()


def inspector():
    code=globals().get('HEALTH_SOURCE')
    if not code:raise RuntimeError('Health collector unavailable')
    namespace={'__name__':'readonly_health'}
    exec(compile(base64.b64decode(code),'lab-health-node','exec'),namespace)
    return namespace


def container(name):
    if name not in CONTROL_ORDER:raise RuntimeError('Unapproved service')
    fmt='{"id":{{json .Id}},"image":{{json .Config.Image}},"running":{{json .State.Running}},"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}"not configured"{{end}}}'
    result=json.loads(run(['docker','inspect','--format',fmt,name]))
    image={'nova_metadata':'nova-api','neutron_rpc_server':'neutron-server',
           'neutron_periodic_worker':'neutron-server'}.get(name,name.replace('_','-'))
    if result.get('image')!='quay.io/openstack.kolla/'+image+':2026.2-ubuntu-noble' or not re.fullmatch(r'[a-f0-9]{64}',result.get('id','')):
        raise RuntimeError('Unreviewed container image/identity: '+name)
    if type(result.get('running')) is not bool or result.get('health') not in ('healthy','unhealthy','starting'):
        raise RuntimeError('Unreviewed container health state: '+name)
    return result


def healthy(value):return value.get('running') is True and value.get('health')=='healthy'


def unit(name):
    if name not in CONTROL_ORDER:raise RuntimeError('Unapproved unit')
    service='kolla-'+name+'-container.service';expected='/etc/systemd/system/'+service
    props=dict(line.split('=',1) for line in run(['systemctl','show',service,'--property=LoadState,ActiveState,FragmentPath,DropInPaths','--no-pager']).splitlines() if '=' in line)
    if props.get('LoadState')!='loaded' or props.get('FragmentPath')!=expected or props.get('DropInPaths'):
        raise RuntimeError('Unit definition/overrides need review: '+name)
    path=Path(expected);info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022:raise RuntimeError('Unsafe unit definition')
    raw=path.read_bytes();parser=configparser.ConfigParser(interpolation=None);parser.read_string(raw.decode())
    values=parser['Service']
    if set(values)!={'execstart','execstop','restart','restartsec','successexitstatus'} or values.get('Restart')!='always':
        raise RuntimeError('Unreviewed service hooks or supervision policy')
    if values.get('ExecStart')!='/usr/bin/docker start -a '+name or not re.fullmatch(r'/usr/bin/docker stop '+re.escape(name)+r' -t (10|60)',values.get('ExecStop','')):
        raise RuntimeError('Unit does not manage only the approved container')
    return {'name':service,'sha256':hashlib.sha256(raw).hexdigest(),'active':props.get('ActiveState')}


def dependencies(record,cluster_uuid):
    if record.get('observed') is not True:raise RuntimeError('Host observation unavailable')
    db=record.get('galera',{}).get('value',{})
    wanted={'wsrep_cluster_status':'Primary','wsrep_cluster_size':'3','wsrep_local_state_comment':'Synced',
      'wsrep_connected':'ON','wsrep_ready':'ON','wsrep_cluster_state_uuid':cluster_uuid}
    if not record.get('galera',{}).get('observed') or any(db.get(k)!=v for k,v in wanted.items()):raise RuntimeError('Galera dependency changed')
    if record.get('rabbitmq',{}).get('value',{}).get('healthy') is not True:raise RuntimeError('RabbitMQ dependency needs review')
    states=record.get('containers',{}).get('value',{}).get('containers',{})
    for name in ('proxysql','haproxy'):
        if not healthy(states.get(name,{})):raise RuntimeError(name+' dependency is not healthy')


def execute(node,action,name=None,expected_id=None,unit_sha=None,cluster_uuid=None,profile='approved-api'):
    if node not in ALLOWED or os.geteuid()!=0 or os.environ.get('SUDO_USER')!='joe' or socket.gethostname().split('.')[0]!=node:
        raise RuntimeError('Expected approved node identity and sudo from joe')
    if action not in ('inspect','restart'):raise RuntimeError('Unknown operation')
    if profile not in PROFILES:raise RuntimeError('Unknown recovery profile')
    names=PROFILES[profile][node]
    health=inspector();record=health['collect'](node)
    if action=='inspect':
        return {'node':node,'host':record,'targets':{n:{'container':container(n),'unit':unit(n)} for n in names}}
    if name not in names:raise RuntimeError('Restart outside the approved node/service scope')
    dependencies(record,cluster_uuid)
    before=container(name);definition=unit(name)
    if before['id']!=expected_id or definition['sha256']!=unit_sha:raise RuntimeError('Container or unit identity changed; no restart')
    if healthy(before):return {'node':node,'service':name,'action':'skipped; already healthy','changed':False,'container':before}
    if before['running'] is True and before['health']=='starting':
        if profile!='reboot-control':raise RuntimeError(name+': still starting; no restart sent')
        return {'node':node,'service':name,'action':'deferred; still starting; no restart sent',
                'changed':False,'deferred':True,'container':before}
    run(['systemctl','restart',definition['name']],timeout=90)
    deadline=time.monotonic()+120
    while True:
        after=container(name)
        if after['id']!=expected_id or unit(name)['sha256']!=unit_sha:raise RuntimeError('Service identity changed during recovery')
        if healthy(after) and unit(name)['active']=='active':
            return {'node':node,'service':name,'action':'restarted once; container health passed','changed':True,'container':after}
        if time.monotonic()>=deadline:raise RuntimeError('Container did not become healthy within 120 seconds; no repeated restart')
        time.sleep(3)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node',choices=ALLOWED,required=True);parser.add_argument('--action',choices=('inspect','restart'),required=True)
    parser.add_argument('--profile',choices=PROFILES,default='approved-api')
    for name in ('service','container-id','unit-sha','cluster-uuid'):parser.add_argument('--'+name)
    args=parser.parse_args()
    try:result=dict(execute(args.node,args.action,args.service,args.container_id,args.unit_sha,args.cluster_uuid,args.profile),passed=True)
    except (Exception,KeyboardInterrupt) as error:
        result={'node':args.node,'passed':False,'error':str(error) if isinstance(error,RuntimeError) else type(error).__name__+'; recovery outcome needs review'}
    print(json.dumps(result));return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())

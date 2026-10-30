#!/usr/bin/env python3
"""Numbered VM/storage migration menu for the three-node lab; confirm every move."""
import sys
sys.dont_write_bytecode=True
import argparse
import copy
import datetime as dt
import fcntl
import getpass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import re
import shlex
import tempfile
import time
import uuid
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

REPO=Path(__file__).resolve().parents[1]
def load(name,file):
    spec=importlib.util.spec_from_file_location(name,REPO/'scripts'/file)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
move=load('menu_move','phase5-move-vm.py');health=load('menu_health','lab-health.py')
inspection=move.inspection;volume=move.volume;prep=move.prep;deploy=move.deploy;inv=move.inv;ENV=move.ENV
NODES=tuple(inv.NODES);PREFIX='menu-migration-'
MODES=('live','cold','volume-live','volume-offline')
MODE_LABELS={'live':'LIVE VM + local OS disk','cold':'COLD VM + local OS disk',
  'volume-live':'ATTACHED data volume','volume-offline':'DETACHED data volume'}
TERMINAL={'completed','confirmed','error','failed','cancelled','canceled','done','reverted'}
LIVE_MICROVERSION='2.30'  # Horizon 2026.2 live_migrate feature; synchronous prechecks.


def clean(value):return re.sub(r'[\x00-\x1f\x7f]','?',str(value if value is not None else '-'))[:140]
def identifier(value):
    if not volume.valid_uuid(value):raise RuntimeError('Resource UUID is unavailable or invalid')
    return value
def node_name(value):
    node=str(value).split('@')[0].split('.')[0]
    if node not in NODES:raise RuntimeError('Resource is outside node1/node2/node3')
    return node
def choose(title,items,label,read=input,log=print):
    if not items:log('No entries available: '+title+'. No action requested.');return None
    log('\n'+title)
    for index,item in enumerate(items,1):log('  '+str(index)+') '+clean(label(item)))
    log('  0) Cancel')
    while True:
        value=read('Choose a number: ').strip()
        if value in ('0','q','Q'):return None
        if value.isdigit() and 1<=int(value)<=len(items):return items[int(value)-1]
        named=[item for item in items if isinstance(item,str) and item.casefold()==value.casefold()]
        if len(named)==1:return named[0]
        log('Enter one of the numbers shown.')
def confirm(text,read=input,log=print):
    log(text)
    return read('Type MIGRATE to approve, or press Enter to cancel: ').strip()=='MIGRATE'


class API(prep.API):
    """GETs plus only exact approved migration/confirmation requests, once each."""
    def __init__(self,original,read_only=False):
        super().__init__(original.client,original.urls,original.token);self.allowed={};self.sent=set();self.read_only=read_only
        self.mode=None;self.last_response_request_id=None;self.last_action_request_id=None;self._sending=None
        self.last_response_http_status=None;self.last_action_http_status=None
    def get(self,service,path):return self.request(service,'GET',path)
    def arm(self,plan):
        if self.read_only:raise RuntimeError('Plan-only client cannot authorize a migration')
        if self.allowed:raise RuntimeError('This client is already bound to a move')
        mode=plan['mode'];dest=plan['destination'];server=plan.get('server_id')
        self.mode=mode
        if mode in ('live','cold'):
            identifier(server)
            if dest not in NODES or dest==plan['source']:raise RuntimeError('Invalid compute destination')
            body={'os-migrateLive':{'host':dest,'block_migration':True}} if mode=='live' else {'migrate':{'host':dest}}
            self.allowed['move']=('compute','POST','servers/'+server+'/action',body,202)
            if mode=='cold':self.allowed['confirm']=('compute','POST','servers/'+server+'/action',{'confirmResize':None},204)
        elif mode in ('volume-live','volume-offline'):
            vid=identifier(plan['volume_id']);node_name(dest)
            if dest==plan['source'] or not re.fullmatch(r'node[123]@[A-Za-z0-9_.-]+#[A-Za-z0-9_.-]+',dest):raise RuntimeError('Invalid storage destination')
            body={'os-migrate_volume':{'host':dest,'force_host_copy':False,'lock_volume':mode=='volume-offline'}}
            self.allowed['move']=('block-storage','POST','volumes/'+vid+'/action',body,202)
        else:raise RuntimeError('Unknown migration mode')
    def mutate(self,step):
        if step not in self.allowed or step in self.sent:raise RuntimeError('No repeat or unapproved action permitted')
        service,method,path,body,expected=self.allowed[step]
        self.last_action_request_id=None;self.last_action_http_status=None
        self.sent.add(step)
        version=LIVE_MICROVERSION if self.mode=='live' else '2.79'
        self._sending=(service,method,path,body,expected,version)
        try:return self._send(service,method,path,body,expected,version)
        finally:
            self.last_action_request_id=self.last_response_request_id;self.last_action_http_status=self.last_response_http_status;self._sending=None
    def request(self,service,method,path,body=None,expected=200):
        if method!='GET' or body is not None:raise RuntimeError('Use the single approved migration action')
        return self._send(service,method,path,None,expected,'2.79')
    def write(self,*args,**kwargs):raise RuntimeError('No create/delete/rename/reset/attach/detach operations permitted')
    def _send(self,service,method,path,body,expected,version):
        if method!='GET' or body is not None:
            if self.read_only or self._sending!=(service,method,path,body,expected,version):raise RuntimeError('Transport refuses an unapproved migration request')
        self.last_response_request_id=None;self.last_response_http_status=None
        headers={'X-Auth-Token':self.token,'Accept':'application/json'}
        if service=='compute':headers['OpenStack-API-Version']='compute '+version
        if body is not None:headers['Content-Type']='application/json'
        request=urllib.request.Request(self.url(service,path),method=method,headers=headers,
          data=json.dumps(body).encode() if body is not None else None)
        def remember(response_headers):
            if response_headers is None:return
            value=response_headers.get('X-Openstack-Request-Id') or response_headers.get('X-Compute-Request-Id')
            if isinstance(value,str) and value.startswith('req-') and volume.valid_uuid(value[4:]):self.last_response_request_id=value
        try:
            with self.client.opener.open(request,timeout=180 if method=='POST' and version==LIVE_MICROVERSION else 45) as response:
                remember(response.headers)
                self.last_response_http_status=response.status
                if response.status!=expected:raise RuntimeError('Unexpected migration HTTP status '+str(response.status)+'; outcome needs review')
                raw=response.read(2_000_001)
                if len(raw)>2_000_000:raise RuntimeError('Migration response exceeded collection limit; outcome needs review')
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            remember(error.headers)
            self.last_response_http_status=error.code
            try:
                raw=error.read(65_537)
                doc=json.loads(raw) if len(raw)<=65_536 else {}
                values=[v.get('message') for v in doc.values() if isinstance(v,dict)] if isinstance(doc,dict) else []
                detail=next((move.safe_message(v) for v in values if v),'')
            except (ValueError,OSError):detail=''
            raise RuntimeError(service+' '+method+': HTTP '+str(error.code)+(' — '+detail if detail else '')+'; no automatic retry') from None
        except (ValueError,urllib.error.URLError,OSError):
            raise RuntimeError(service+' '+method+': response unavailable; outcome uncertain, no automatic retry') from None


def server_value(api,server,project):
    value=api.get('compute','servers/'+identifier(server))['server']
    if value.get('id')!=server or value.get('tenant_id')!=project:raise RuntimeError('VM identity/project changed')
    node_name(value.get('OS-EXT-SRV-ATTR:host'))
    return value
def volume_value(api,vid,project):
    value=api.get('block-storage','volumes/'+identifier(vid))['volume']
    if value.get('id')!=vid or value.get('os-vol-tenant-attr:tenant_id')!=project:raise RuntimeError('Volume identity/project unavailable or changed')
    node_name(value.get('os-vol-host-attr:host'))
    return value
def volume_ids(server):
    rows=server.get('os-extended-volumes:volumes_attached')
    if not isinstance(rows,list):raise RuntimeError('VM volume list unavailable')
    values=[identifier(row.get('id')) for row in rows]
    if len(set(values))!=len(values):raise RuntimeError('Duplicate VM volume identity')
    return sorted(values)
def migration_history(api,server):
    page=api.get('compute','os-migrations?instance_uuid='+identifier(server)+'&limit=100');rows=page.get('migrations')
    if not isinstance(rows,list) or len(rows)>=100 or any(row.get('instance_uuid')!=server for row in rows):raise RuntimeError('Migration history unavailable or exceeds the review limit')
    for row in rows:identifier(row.get('uuid'))
    links=page.get('migrations_links',[])
    if not isinstance(links,list) or any(not isinstance(link,dict) or link.get('rel')=='next' for link in links) or len({r['uuid'] for r in rows})!=len(rows):
        raise RuntimeError('Migration history is incomplete or ambiguous; review required')
    return rows
def volume_migration(value):return value.get('migration_status') or value.get('os-vol-mig-status-attr:migstat')
def pending_guard(folder,server=None,vid=None):
    repair=folder/'nova-messaging-fix.json'
    if (repair.exists() or repair.is_symlink()) and health.read_private(repair).get('pending') is True:
        raise RuntimeError('Nova messaging repair is incomplete; return its report before a migration. Repair and migration records are preserved.')
    paths=list(folder.glob(PREFIX+'*.json'))+[folder/'move-vm-state.json']
    for path in paths:
        if not path.exists() and not path.is_symlink():continue
        value=health.read_private(path)
        if value.get('pending') and ((server and value.get('server_id')==server) or (vid and value.get('volume_id')==vid)):
            action=(' After reviewing the phase5-move-vm.py --observe report, use scripts/phase5-reconcile-migration.py --apply --migration-id FAILED_MIGRATION_UUID for a confirmed failed attempt.' if path.name=='move-vm-state.json' else ' Review with bash scripts/lab-migrate.sh --observe; acknowledge a verified failed menu attempt separately with --reconcile.')
            raise RuntimeError('Existing migration checkpoint requires review: '+path.name+'. It has been preserved; no move requested.'+action)
def checkpoints():
    result={}
    for phase in ('phase4','phase5'):
        for path in sorted((REPO/'private'/phase).glob('*.json')):
            if phase=='phase5' and path.name.startswith(PREFIX):continue
            deploy.validation.private_file(path);result[str(path.relative_to(REPO))]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def cluster(nodes_env,passwords,log):
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs={n:pool.submit(health.host_observe,n,passwords[n],nodes_env) for n in NODES}
        hosts={n:j.result() for n,j in jobs.items()}
    unavailable=[n+': '+clean(v.get('error','host observations unavailable')) for n,v in hosts.items() if v.get('observed') is not True]
    if unavailable:raise RuntimeError('Host checks unavailable: '+'; '.join(unavailable)+'. No migration requested during these checks.')
    rows=[];details={};health.host_rows(hosts,rows,details);health.api_checks(rows,details,nodes_env,include_workload=False)
    bad=[r['check'] for r in rows if r.get('status')!='PASS']
    if bad:raise RuntimeError('Cluster checks need attention: '+', '.join(bad))
    log('PASS  Cluster containers, Galera, APIs, services and agents')
    return {'boots':{n:hosts[n]['host']['value']['boot_id'] for n in NODES},
      'cluster_uuid':hosts[NODES[0]]['galera']['value']['wsrep_cluster_state_uuid']}


def runtime(nodes,server):
    result={n:nodes.call(n,'running',{'server_id':server}) for n in NODES}
    if any(v.get('observed') is not True for v in result.values()):raise RuntimeError('Actual VM runtime could not be read on every node')
    if any(type(v.get('VM_running')) is not bool or v.get('domain_state') not in ('running','not active') for v in result.values()):
        raise RuntimeError('A VM domain is paused or in an unreviewed runtime state')
    return [n for n,v in result.items() if v.get('VM_running') is True]
def attachments(api,server,project):
    return {vid:volume_value(api,vid,project) for vid in volume_ids(server)}
def binding(server,volumes):
    return {'server_id':server['id'],'status':server.get('status'),'source':node_name(server.get('OS-EXT-SRV-ATTR:host')),
      'image_id':server.get('image',{}).get('id'),'flavor':server.get('flavor'),
      'addresses':server.get('addresses',{}),'volumes':{vid:{'backend':v.get('os-vol-host-attr:host'),'size':v.get('size'),
          'status':v.get('status'),'servers':sorted(a.get('server_id','') for a in v.get('attachments',[]))} for vid,v in volumes.items()}}
def vm_eligible(server,mode):
    allowed=('ACTIVE',) if mode in ('live','volume-live') else ('ACTIVE','SHUTOFF')
    if server.get('status') not in allowed:raise RuntimeError('VM is '+str(server.get('status'))+'; this mode requires '+('/'.join(allowed))+'. No start/reset or recovery is performed.')
    if 'OS-EXT-STS:task_state' not in server:raise RuntimeError('Nova task state is unavailable; an idle VM is not verified')
    if server['OS-EXT-STS:task_state'] is not None or server.get('locked') is True:raise RuntimeError('VM has an operation in progress or is locked')


def compute_preflight(api,server,project,dest,mode,nodes,log):
    vm_eligible(server,mode);source=node_name(server['OS-EXT-SRV-ATTR:host'])
    if dest not in NODES or dest==source:raise RuntimeError('Choose a different compute node')
    if not isinstance(server.get('image'),dict) or not server['image'].get('id'):
        raise RuntimeError('This menu supports image-based VMs with a local OS disk; volume-backed roots need a separate plan')
    services=api.list('compute','os-services','services')
    for host in (source,dest):
        rows=[s for s in services if s.get('binary')=='nova-compute' and node_name(s.get('host'))==host]
        if len(rows)!=1 or rows[0].get('state')!='up' or rows[0].get('status')!='enabled':raise RuntimeError(host+': require one up/enabled nova-compute service')
    expected=[source] if server['status']=='ACTIVE' else []
    if runtime(nodes,server['id'])!=expected:raise RuntimeError('Nova power state and actual hypervisor runtime do not agree')
    records=nodes.all({'server_id':server['id'],'source':source})
    if any(v.get('observed') is not True for v in records.values()):raise RuntimeError('Compute inspection unavailable on a node')
    domain=records[source]['domain']
    if not domain or domain.get('uuid')!=server['id'] or domain.get('host_devices')!=0:raise RuntimeError('Source VM identity/CPU/devices need review')
    disks=domain.get('disks',[])
    if not any(d.get('type')=='file' and d.get('device')=='disk' and not d.get('readonly') for d in disks):raise RuntimeError('A local writable OS disk was not observed')
    if mode=='live' and any(d.get('readonly') or d.get('device')!='disk' for d in disks):raise RuntimeError('Read-only/config-drive media need a different live-migration plan')
    cpu=nodes.call(dest,'compare',{'domain':domain})
    if cpu.get('observed') is not True or cpu.get('source_cpu_supported_by_destination_hypervisor') is not True:raise RuntimeError('Destination CPU compatibility not confirmed')
    flavor=server.get('flavor',{})
    if not all(type(flavor.get(k)) is int for k in ('vcpus','ram','disk')):
        fid=flavor.get('id')
        if not isinstance(fid,str) or not re.fullmatch(r'[A-Za-z0-9-]+',fid):raise RuntimeError('VM flavor details unavailable')
        flavor=api.get('compute','flavors/'+fid)['flavor']
    requirements={'vcpus':flavor['vcpus'],'ram_mb':flavor['ram'],'disk_gb':flavor['disk']}
    memory=domain.get('memory',{});units={'B':1,'bytes':1,'KiB':1024,'MiB':1024**2,'GiB':1024**3}
    if flavor['disk']<=0 or memory.get('unit') not in units or int(memory.get('value',0))*units[memory['unit']]!=flavor['ram']*1024**2 or int(domain.get('vcpus',0))!=flavor['vcpus']:
        raise RuntimeError('Actual VM RAM/CPU or zero-size root flavor needs separate capacity review')
    summary=inspection.candidate_summary(records[dest],cpu,{'observed':True},requirements)
    if not summary['ram_covers_guest_plus_1GiB_review_margin'] or not summary['disk_covers_root_plus_2GiB_review_margin']:
        raise RuntimeError('Destination physical RAM or instance disk headroom is insufficient')
    move.placement_room(inspection.capacity(api,dest),requirements)
    if mode=='live':
        peer=nodes.call(source,'connection',{'peer':dest,'uri':records[dest]['nova_config']['connection_uri']})
        if peer.get('observed') is not True or peer.get('authenticated_libvirt_read') is not True:raise RuntimeError('Libvirt peer control connection not confirmed')
        for host in (source,dest):
            result=nodes.call(host,'vnc',{})
            if not result.get('observed') or not result.get('graphics_rewrite_test_passed') or not result.get('graphics_rewrite_wired'):raise RuntimeError('Installed Nova graphics rewrite not confirmed')
    history=migration_history(api,server['id'])
    if any(r.get('status') not in TERMINAL for r in history):raise RuntimeError('An existing VM migration requires review')
    volumes=attachments(api,server,project)
    for v in volumes.values():
        if v.get('status')!='in-use' or sorted(a.get('server_id') for a in v.get('attachments',[]))!=[server['id']] or volume_migration(v) not in (None,'success'):
            raise RuntimeError('Attached volume identity/state needs review')
    log('PASS  VM identity/runtime, CPU, RAM, local disk and Placement observations')
    log('NOTE  Nova still performs scheduling and transfer checks; guest traffic/data are checked separately.')
    return {'mode':mode,'server_id':server['id'],'vm_name':server.get('name'),'project_id':project,'source':source,
      'destination':dest,'before':binding(server,volumes),'before_migrations':[r['uuid'] for r in history],
      'node_identity':{n:move.node_identity(v) for n,v in records.items()}}


def storage_preflight(api,value,project,pool,mode,server=None):
    vid=identifier(value['id']);source=value['os-vol-host-attr:host'];dest=pool['name'];node_name(dest)
    if source==dest:raise RuntimeError('Choose another storage backend')
    wanted='in-use' if mode=='volume-live' else 'available'
    if value.get('status')!=wanted or volume_migration(value) not in (None,'success'):raise RuntimeError('Volume must be '+wanted+' with no pending migration')
    if value.get('multiattach') is not False or value.get('encrypted') is not False:raise RuntimeError('This data-volume menu requires an unencrypted, non-multiattach volume')
    if str(value.get('bootable')).lower()!='false' or value.get('group_id') or value.get('consistencygroup_id'):
        raise RuntimeError('Select an ordinary data volume, outside a consistency group')
    if value.get('replication_status') not in (None,'disabled','not-capable'):raise RuntimeError('Replicated-volume migration needs a separate plan')
    snapshots=api.list('block-storage','snapshots/detail?volume_id='+vid,'snapshots')
    if any(s.get('volume_id')==vid for s in snapshots):raise RuntimeError('Volume snapshots must be reviewed before migration')
    caps=pool.get('capabilities',{});free=caps.get('free_capacity_gb')
    if type(free) not in (int,float) or free<float(value['size'])+1:raise RuntimeError('Destination free storage capacity is unavailable or insufficient')
    if str(caps.get('storage_protocol','')).lower()!='iscsi':raise RuntimeError('Only the observed lab iSCSI storage path is covered by this menu')
    pools=api.get('block-storage','scheduler-stats/get_pools?detail=True').get('pools',[])
    original=[p for p in pools if p.get('name')==source]
    if len(original)!=1 or original[0].get('capabilities',{}).get('volume_backend_name')!=caps.get('volume_backend_name') or not caps.get('volume_backend_name'):
        raise RuntimeError('Source/destination backend compatibility needs review')
    if mode=='volume-live':
        if server is None:raise RuntimeError('A running VM must be selected for an attached-volume move')
        vm_eligible(server,mode)
        if vid not in volume_ids(server) or sorted(a.get('server_id') for a in value.get('attachments',[]))!=[server['id']]:raise RuntimeError('Data volume must be attached only to the selected VM')
    elif value.get('attachments'):raise RuntimeError('Offline data-volume migration requires an already detached volume')
    return {'mode':mode,'volume_id':vid,'volume_name':value.get('name'),'server_id':server.get('id') if server else None,
      'vm_name':server.get('name') if server else None,
      'project_id':project,'source':source,'destination':dest,'before_volume':copy.deepcopy(value),
      'before_server':binding(server,attachments(api,server,project)) if server else None,'pool':copy.deepcopy(pool)}


def plan_lines(plan):
    lines=['\nREVIEW THIS MOVE','Operation: '+MODE_LABELS[plan['mode']]]
    if plan.get('server_id'):lines+=['VM: '+clean(plan.get('vm_name') or plan['server_id']),'VM ID: '+plan['server_id']]
    if plan.get('volume_id'):lines+=['Data volume: '+clean(plan.get('volume_name'))+' ('+plan['volume_id']+')']
    if plan['mode'] in ('live','cold'):
        lines+=['Source: '+clean(plan['source']),'Destination: '+clean(plan['destination']),
          'OS disk: copied by Nova to the destination compute node.',
          'Attached Cinder volumes: remain on their current storage backends.',
          'VM power: '+('keep running; a brief pause is possible.' if plan['mode']=='live' else 'planned shutdown/cold migration; downtime is expected.')]
    else:
        lines+=['Source storage backend: '+clean(plan['source']),
          'Destination storage backend: '+clean(plan['destination'])]
        if plan.get('before_server'):lines+=['VM compute host: '+clean(plan['before_server']['source'])+' (stays here)']
        lines+=['Compute/OS disk: stay where they are.','Data volume: move backend; retain the same volume UUID.']
    lines+=['No rename, formatting, force detach, state reset or automatic retry.']
    return lines


def refresh(api,plan):
    project=plan['project_id'];mode=plan['mode']
    if mode in ('live','cold'):
        server=server_value(api,plan['server_id'],project);vm_eligible(server,mode)
        if binding(server,attachments(api,server,project))!=plan['before']:raise RuntimeError('VM or attached storage changed after review')
        if [r['uuid'] for r in migration_history(api,server['id'])]!=plan['before_migrations']:raise RuntimeError('Migration history changed after review')
    else:
        value=volume_value(api,plan['volume_id'],project)
        fields=('id','status','size','os-vol-host-attr:host','attachments','migration_status','os-vol-mig-status-attr:migstat')
        if any(value.get(k)!=plan['before_volume'].get(k) for k in fields):raise RuntimeError('Volume changed after review')
        server=None
        if plan.get('server_id'):
            server=server_value(api,plan['server_id'],project);vm_eligible(server,'volume-live')
            if binding(server,attachments(api,server,project))!=plan['before_server']:raise RuntimeError('VM changed after review')
        pools=api.get('block-storage','scheduler-stats/get_pools?detail=True').get('pools',[])
        destinations=[p for p in pools if p.get('name')==plan['destination']]
        if len(destinations)!=1:raise RuntimeError('Destination storage pool is no longer unique/available')
        storage_preflight(api,value,project,destinations[0],mode,server)


def compute_snapshot(api,plan):
    server=server_value(api,plan['server_id'],plan['project_id'])
    rows=[r for r in migration_history(api,plan['server_id']) if r['uuid'] not in plan['before_migrations']]
    if len(rows)>1:raise RuntimeError('More than one new migration; review actual state')
    item=rows[0] if rows else None
    if item:
        expected_type='live-migration' if plan['mode']=='live' else 'migration'
        if item.get('migration_type') not in (None,expected_type):raise RuntimeError('Migration type differs from the plan')
        if (item.get('source_compute') and node_name(item['source_compute'])!=plan['source']) or (item.get('dest_compute') and node_name(item['dest_compute'])!=plan['destination']):
            raise RuntimeError('Migration source/destination differs from the plan')
    return server,item
def wait_move(api,plan,log,seconds=1200):
    deadline=time.monotonic()+seconds;last=None
    while True:
        if plan['mode'] in ('live','cold'):
            server,item=compute_snapshot(api,plan);status=item.get('status') if item else 'not observed yet'
            note='VM '+str(server.get('status'))+' on '+node_name(server['OS-EXT-SRV-ATTR:host'])+'; migration '+str(status)
            if note!=last:log(clean(note));last=note
            if item:log_id=item.get('uuid')
            else:log_id=None
            if status in ('error','failed','cancelled','canceled') or server.get('status')=='ERROR':
                fault=server.get('fault') or {}
                if log_id:log('Migration ID: '+log_id)
                if fault:log('Nova fault: '+str(fault.get('code','-'))+' '+str(move.safe_message(fault.get('message'))))
                raise RuntimeError('Nova reports migration/VM error; no retry or automatic recovery. Use --observe for the current state and request details.')
            if 'OS-EXT-STS:task_state' not in server:raise RuntimeError('Nova task state is unavailable; migration completion is not verified. Use --observe.')
            at_dest=node_name(server['OS-EXT-SRV-ATTR:host'])==plan['destination'];idle=server.get('OS-EXT-STS:task_state') is None
            placed=(item and item.get('migration_type')==('live-migration' if plan['mode']=='live' else 'migration')
              and item.get('source_compute') and node_name(item['source_compute'])==plan['source']
              and item.get('dest_compute') and node_name(item['dest_compute'])==plan['destination'])
            if plan['mode']=='live' and placed and status=='completed' and at_dest and idle and server.get('status')=='ACTIVE':return 'done'
            if plan['mode']=='cold' and placed and at_dest and idle:
                if server.get('status')=='VERIFY_RESIZE' and status=='finished':return 'confirm'
                if server.get('status')==plan['before']['status'] and status=='confirmed':return 'done'
        else:
            value=volume_value(api,plan['volume_id'],plan['project_id']);status=volume_migration(value)
            note='Volume '+str(value.get('status'))+'; backend '+str(value.get('os-vol-host-attr:host'))+'; migration '+str(status)
            if status=='error' or str(value.get('status','')).startswith('error'):raise RuntimeError('Cinder reports migration error; no retry')
            if value.get('os-vol-host-attr:host')==plan['destination'] and value.get('status')==plan['before_volume']['status'] and status=='success':return 'done'
        if note!=last:log(clean(note));last=note
        if time.monotonic()>=deadline:raise RuntimeError('Observation window ended; the operation may still run. No retry or cancellation sent.')
        time.sleep(5)


def verify_move(api,plan,nodes,awaiting=False):
    if plan['mode'] in ('live','cold'):
        server=server_value(api,plan['server_id'],plan['project_id']);current=binding(server,attachments(api,server,plan['project_id']))
        if 'OS-EXT-STS:task_state' not in server or server['OS-EXT-STS:task_state'] is not None:raise RuntimeError('An idle VM is not verified after the move')
        if current['source']!=plan['destination'] or any(current[k]!=plan['before'][k] for k in ('addresses','volumes','image_id','flavor')):
            raise RuntimeError('Destination, IPs or attached Cinder backend/identity changed unexpectedly')
        expected=[plan['destination']] if plan['before']['status']=='ACTIVE' else []
        if runtime(nodes,server['id'])!=expected:raise RuntimeError('Actual destination VM runtime differs from expected power state')
        if not awaiting and current['status']!=plan['before']['status']:raise RuntimeError('Final VM power/API state differs')
    else:
        value=volume_value(api,plan['volume_id'],plan['project_id']);before=plan['before_volume']
        if value.get('size')!=before.get('size') or value.get('os-vol-host-attr:host')!=plan['destination'] or value.get('status')!=before.get('status'):
            raise RuntimeError('Final volume UUID/size/status/backend needs review')
        if sorted(a.get('server_id') for a in value.get('attachments',[]))!=sorted(a.get('server_id') for a in before.get('attachments',[])):
            raise RuntimeError('Volume attachment changed unexpectedly')
        if plan.get('server_id'):
            server=server_value(api,plan['server_id'],plan['project_id']);vm_eligible(server,'volume-live')
            current=binding(server,attachments(api,server,plan['project_id']))
            if current['source']!=plan['before_server']['source'] or current['addresses']!=plan['before_server']['addresses'] or volume_ids(server)!=sorted(plan['before_server']['volumes']):
                raise RuntimeError('VM identity/location/IPs/volume IDs changed during storage move')
            if runtime(nodes,server['id'])!=[current['source']]:raise RuntimeError('VM runtime not confirmed after storage move')


def perform(api,plan,nodes,save,log,approve,unchanged,healthy,recheck):
    if not approve('Review the source, destination and downtime above.'):log('Cancelled. No migration requested.');return False
    unchanged();healthy();refresh(api,plan);recheck()
    api.arm(plan)
    state={'pending':True,'server_id':plan.get('server_id'),'volume_id':plan.get('volume_id'),'plan':plan,'attempts':['move'],'stage':'move outcome pending'}
    state['created_at']=dt.datetime.now(dt.timezone.utc).isoformat()
    state['request_ids']={}
    state['http_statuses']={}
    state['live_microversion']=LIVE_MICROVERSION if plan['mode']=='live' else None
    save(copy.deepcopy(state))
    def send(step):
        try:api.mutate(step)
        finally:
            request_id=getattr(api,'last_action_request_id',None)
            http_status=getattr(api,'last_action_http_status',None)
            if request_id:state['request_ids'][step]=request_id
            if http_status is not None:state['http_statuses'][step]=http_status
            if request_id or http_status is not None:save(copy.deepcopy(state))
            if request_id:log('Request ID: '+request_id)
    log('Requesting migration; waiting for Nova checks and its response…')
    send('move')
    log('REQUEST ACCEPTED — observing progress…')
    stage=wait_move(api,plan,log)
    if stage=='confirm':
        verify_move(api,plan,nodes,awaiting=True)
        state['stage']='cold move awaits confirmation';save(copy.deepcopy(state))
        server,item=compute_snapshot(api,plan)
        def confirmed(s,migration):
            return node_name(s['OS-EXT-SRV-ATTR:host'])==plan['destination'] and s.get('status')==plan['before']['status'] and migration and migration.get('status')=='confirmed'
        if not confirmed(server,item):
            approved=approve('Cold move reached the destination. Confirm this placement?')
            server,item=compute_snapshot(api,plan)
            if confirmed(server,item):log('Nova already confirmed the move; no extra confirmation request sent.')
            elif not approved:
                log('Cold move awaits confirmation; no automatic revert. Return this report.');return False
            else:
                if server.get('status')!='VERIFY_RESIZE' or not item or item.get('status')!='finished':raise RuntimeError('Cold confirmation state changed; review before any action')
                state['attempts'].append('confirm');state['stage']='confirmation outcome pending';save(copy.deepcopy(state))
                send('confirm');wait_move(api,plan,log)
    verify_move(api,plan,nodes);healthy();unchanged()
    state.update(pending=False,stage='migration API and host observations passed');save(copy.deepcopy(state))
    log('RESULT: MIGRATION_API_AND_HOST_CHECKS_PASSED')
    log('Guest application, file checksum and uninterrupted traffic are not verified by this menu. Check them through Horizon/SSH before the next exercise.')
    return True


def run_menu(api,project,env,folder,log,save,read=input,plan_only=False,health_check=cluster):
    choice=choose('Choose the operation',list(MODES),lambda m:{'live':'Live VM + local OS disk (keep running)',
      'cold':'Cold VM + local OS disk (planned shutdown/downtime)',
      'volume-live':'Move an attached data volume (VM stays running)',
      'volume-offline':'Move an already detached data volume (offline)'}[m],read,log)
    if choice is None:return 0
    server=None
    if choice!='volume-offline':
        if choice=='volume-live':
            log('First select the VM using the volume. This filter lists VM compute locations; the volume storage source is read separately.')
        servers=api.list('compute','servers/detail?limit=100','servers')
        servers=[v for v in servers if v.get('tenant_id')==project and str(v.get('OS-EXT-SRV-ATTR:host','')).split('.')[0] in NODES]
        if not servers:log('No VMs in the current project on node1/node2/node3. No migration requested.');return 0
        while True:
            def source_label(n):
                count=len(servers) if n=='All nodes' else sum(node_name(v['OS-EXT-SRV-ATTR:host'])==n for v in servers)
                return n+' — '+str(count)+' VM(s)'
            title="Choose the VM's current compute node" if choice=='volume-live' else 'Choose the source node'
            source_filter=choose(title,['All nodes',*NODES],source_label,read,log)
            if source_filter is None:return 0
            filtered=[v for v in servers if source_filter=='All nodes' or node_name(v['OS-EXT-SRV-ATTR:host'])==source_filter]
            if filtered:break
            log('No VM currently lives on '+source_filter+'. Choose its current compute node; '+
              ('the volume storage source is read separately.' if choice=='volume-live' else 'destination is selected later.'))
        selected=choose('Choose the existing VM',filtered,
          lambda v:clean(v.get('name'))+' | '+str(v.get('status'))+' | '+node_name(v['OS-EXT-SRV-ATTR:host'])+' | '+v['id'],read,log)
        if selected is None:return 0
        server=server_value(api,selected['id'],project)
        log(('VM compute host: ' if choice=='volume-live' else 'Detected VM source: ')+node_name(server['OS-EXT-SRV-ATTR:host']))
        log('VM state: '+str(server.get('status')))
        for vid,value in attachments(api,server,project).items():
            log('Data disk: '+clean(value.get('name'))+' | '+str(value.get('status'))+' | '+value['os-vol-host-attr:host']+' | '+vid)
        pending_guard(folder,server=server['id']);vm_eligible(server,choice)
    passwords={}
    try:
        if choice in ('live','cold'):
            dest=choose('Choose the destination compute node',[n for n in NODES if n!=node_name(server['OS-EXT-SRV-ATTR:host'])],str,read,log)
            if dest is None:return 0
        else:
            candidates=([volume_value(api,vid,project) for vid in volume_ids(server)] if server else
              [v for v in api.list('block-storage','volumes/detail?limit=100','volumes') if v.get('status')=='available' and v.get('os-vol-tenant-attr:tenant_id')==project])
            selected=choose('Choose the data volume',candidates,lambda v:clean(v.get('name'))+' | '+str(v.get('size'))+' GiB | '+str(v.get('os-vol-host-attr:host'))+' | '+v['id'],read,log)
            if selected is None:return 0
            value=volume_value(api,selected['id'],project);pending_guard(folder,server=server['id'] if server else None,vid=value['id'])
            log('Data-volume source backend: '+clean(value['os-vol-host-attr:host']))
            pools=api.get('block-storage','scheduler-stats/get_pools?detail=True').get('pools',[])
            choices=[p for p in pools if str(p.get('name','')).split('@')[0] in NODES and p['name']!=value['os-vol-host-attr:host']]
            pool=choose('Choose the destination storage backend',choices,lambda p:p['name']+' | free '+str(p.get('capabilities',{}).get('free_capacity_gb','not reported'))+' GiB',read,log)
            if pool is None:return 0
            plan=storage_preflight(api,value,project,pool,choice,server)
        for n in NODES:passwords[n]=getpass.getpass('Joe sudo password on '+n+' (hidden): ')
        nodes=inspection.Nodes(env,passwords);boots=health_check(env,passwords,log)
        if choice in ('live','cold'):plan=compute_preflight(api,server,project,dest,choice,nodes,log)
        elif choice=='volume-live' and runtime(nodes,server['id'])!=[node_name(server['OS-EXT-SRV-ATTR:host'])]:raise RuntimeError('VM is not confirmed running once on its reported host')
        for line in plan_lines(plan):log(line)
        if plan_only:log('RESULT: MIGRATION_PLAN_ONLY — no migration requested');return 0
        before=checkpoints()
        def unchanged():
            if checkpoints()!=before:raise RuntimeError('Existing saved checkpoints changed')
        def healthy():
            if health_check(env,passwords,lambda _:None)!=boots:raise RuntimeError('A host rebooted after review')
        def recheck():
            if choice in ('live','cold'):
                fresh=compute_preflight(api,server_value(api,plan['server_id'],project),project,plan['destination'],choice,nodes,lambda _:None)
                if fresh['node_identity']!=plan['node_identity']:raise RuntimeError('Compute containers/configuration changed after review')
        return 0 if perform(api,plan,nodes,save,log,lambda text:confirm(text,read,log),unchanged,healthy,recheck) else 1
    finally:passwords.clear()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--plan',action='store_true',help='menus and checks only; never send a migration')
    modes.add_argument('--observe',action='store_true',help='read current VM, volume and migration API state; preserve all records')
    modes.add_argument('--reconcile',action='store_true',help='explicitly acknowledge one verified failed menu record locally; no cloud changes')
    args=parser.parse_args(argv)
    deploy.validation.config_step.load_step('menu_r2d2','phase2-check.py').require_r2d2();deploy.tools.check_paths(ENV)
    if not sys.stdin.isatty():raise RuntimeError('Use joe\'s interactive R2d2 terminal')
    if Path(sys.prefix)!=ENV:os.execve(str(ENV/'bin/python'),[str(ENV/'bin/python'),str(Path(__file__).resolve())]+list(argv if argv is not None else sys.argv[1:]),deploy.tools.tool_environment())
    os.umask(0o077);env=deploy.tools.tool_environment();deploy.source_guard()
    inventory,config=deploy.validation.local_checks(lambda _:None,env)
    inputs=move.access.input_hashes(config,inventory)
    if not(args.observe or args.reconcile):inv.ensure_agent(env,print)
    original,project,_=prep.authenticate(config);api=API(original,read_only=args.plan or args.observe or args.reconcile)
    folder=REPO/'private/phase5';reports=REPO/'reports';inv.private_dir(folder);inv.private_dir(reports)
    handles=[]
    try:
        for path in [folder/name for name in ('migration.lock','api-recovery.lock','shutdown.lock','api-vip.lock','mariadb.lock','router.lock')]+[REPO/'private/phase4'/n for n in ('vm.lock','volume.lock','networks.lock')]:
            if path.is_symlink():raise RuntimeError('Unsafe operation lock')
            handle=path.open('a');handles.append(handle);os.chmod(path,0o600)
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('Another lab operation is running') from None
        for name in ('api-vip-state.json','mariadb-state.json','router-state.json'):
            path=folder/name
            if not(args.observe or args.reconcile) and (path.exists() or path.is_symlink()) and health.read_private(path).get('restore_required'):raise RuntimeError('An HA component requires restoration first')
        fd,name=tempfile.mkstemp(prefix=PREFIX+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),suffix='.txt',dir=reports)
        report=Path(name);receipt=folder/(report.stem+'.json');print('Private report: '+name);status=1
        with os.fdopen(fd,'w') as out:
            def log(line):print(line,flush=True);out.write(line+'\n');out.flush()
            def save(value):
                if move.access.input_hashes(config,inventory)!=inputs:raise RuntimeError('Deployment inputs changed')
                saved=copy.deepcopy(value);saved.setdefault('inputs',inputs)
                health.prep.net.write_state(receipt,saved)
            try:
                if args.observe or args.reconcile:
                    records=load('menu_records','lab-migrate-records.py')
                    before=checkpoints()
                    def unchanged():
                        if move.access.input_hashes(config,inventory)!=inputs or checkpoints()!=before:raise RuntimeError('Deployment inputs or other checkpoints changed')
                    status=records.run(SimpleNamespace(**globals()),api,project,folder,log,reconcile=args.reconcile,unchanged=unchanged,current_inputs=inputs)
                else:status=run_menu(api,project,env,folder,log,save,plan_only=args.plan)
            except (Exception,KeyboardInterrupt) as error:
                log('RESULT: '+('MIGRATION_PLAN_BLOCKED' if args.plan else 'MIGRATION_NEEDS_REVIEW'))
                log(move.safe_message(str(error)) if isinstance(error,RuntimeError) else type(error).__name__+'; return report')
                log('No retry, state reset, forced completion or cleanup. Saved records and original evidence are retained.')
                if not api.sent:log('NO MIGRATION REQUEST SENT BY THIS RUN.')
        print('Return short report: cat '+shlex.quote(name));return status
    finally:
        for handle in reversed(handles):handle.close()


if __name__=='__main__':
    try:sys.exit(main())
    except (RuntimeError,OSError) as error:print('Stopped: '+clean(str(error)),file=sys.stderr);sys.exit(1)

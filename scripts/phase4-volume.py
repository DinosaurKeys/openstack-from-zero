#!/usr/bin/env python3
"""Chapter E: one 1 GiB Cinder data volume, attachment and detach/reattach persistence test. Default: plan."""
import sys
sys.dont_write_bytecode = True
import argparse
import base64
import copy
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('volume_boot', REPO/'scripts/phase4-boot-vm.py')
boot = importlib.util.module_from_spec(spec);spec.loader.exec_module(boot)
prep=boot.prep;net=boot.net;access=boot.access;deploy=boot.deploy;ENV=boot.ENV
PLAN={'version':1,'name':'lab-data-01','size_gib':1,'filesystem':'ext4','mount':'/mnt/lab-data',
      'test':'write, checksum, unmount, detach, reattach, mount, compare checksum','storage_HA':False}
STAGES=['new','created','attached','initialized','written','unmounted','detached','absent','reattached','remounted','complete']


class API(prep.API):
    """Pin attachment API to 2.79; allow only this volume's create/attach/detach."""
    server_id=None
    volume_id=None
    create_body=None
    def request(self, service, method, path, body=None, expected=200):
        headers={'X-Auth-Token':self.token,'Accept':'application/json'}
        if service=='compute':headers['OpenStack-API-Version']='compute 2.79'
        if body is not None:headers['Content-Type']='application/json'
        req=urllib.request.Request(self.url(service,path),method=method,headers=headers,
            data=json.dumps(body).encode() if body is not None else None)
        try:
            with self.client.opener.open(req,timeout=45) as response:
                if response.status!=expected:raise RuntimeError('Unexpected volume HTTP status '+str(response.status))
                raw=response.read(2_000_001)
                if len(raw)>2_000_000:raise RuntimeError('Volume response exceeded collection limit')
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            raise RuntimeError(service+' '+method+': HTTP '+str(error.code)+'; no automatic retry') from None
        except ValueError:
            raise RuntimeError(service+' '+method+': invalid JSON response; outcome uncertain, no automatic retry') from None
        except (urllib.error.URLError,OSError) as error:
            reason=error.reason if isinstance(error,urllib.error.URLError) else error
            kind=type(reason).__name__
            raise RuntimeError(service+' '+method+': transport '+kind+'; outcome uncertain, no automatic retry') from None
    def get(self,service,path):
        if service=='compute' and '/os-volume_attachments' in path:return self.request(service,'GET',path)
        return super().get(service,path)
    def write(self,service,method,path,body,expected):
        base='servers/'+str(self.server_id)+'/os-volume_attachments'
        create=service=='block-storage' and method=='POST' and path=='volumes' and self.create_body is not None and body==self.create_body and expected==202
        attach=service=='compute' and method=='POST' and path==base and self.volume_id is not None and body=={'volumeAttachment':{'volumeId':self.volume_id,'delete_on_termination':False}} and expected==200
        detach=service=='compute' and method=='DELETE' and path==base+'/'+str(self.volume_id) and self.volume_id is not None and body is None and expected==202
        if not (create or attach or detach):raise RuntimeError('Unapproved Chapter E API operation')
        return self.request(service,method,path,body,expected)


class ReadOnlyAPI(API):
    def request(self,service,method,path,body=None,expected=200):
        if method!='GET' or body is not None:raise RuntimeError('Observation permits GET requests only after authentication')
        return super().request(service,method,path,None,expected)
    def write(self,*args,**kwargs):raise RuntimeError('Observation cannot change cloud resources')


def observe(api,state,project):
    """Collect actual state without clearing a pending request or asserting its outcome."""
    volume=api.get('block-storage','volumes/'+state['volume_id'])['volume']
    server=api.get('compute','servers/'+state['server_id'])['server']
    rows=attachments(api,state)
    issues=[]
    try:volume_check(volume,state,project)
    except RuntimeError as error:issues.append(str(error))
    if rows:
        try:check_attachment(rows,state,True)
        except RuntimeError as error:issues.append(str(error))
    return {'scope':'API snapshot only; no guest check, request retry or pending-marker change',
        'checkpoint':{k:state.get(k) for k in ('stage','pending','server_id','volume_id','filesystem_uuid','sha256')},
        'volume':{k:volume.get(k) for k in ('id','name','status','size','os-vol-host-attr:host','attachments')},
        'server':{k:server.get(k) for k in ('id','name','status','OS-EXT-SRV-ATTR:host','OS-EXT-STS:task_state','OS-EXT-STS:vm_state','os-extended-volumes:volumes_attached')},
        'nova_attachments':rows,'identity_issues':issues,
        'persistence':'Not yet verified after reattachment; pending state is preserved regardless of API observations.'}


def execute_observation(log):
    deploy.source_guard();env=deploy.tools.tool_environment()
    inventory,config=deploy.validation.local_checks(lambda line:None,env,deploy.validation.CA_FINGERPRINT)
    hashes=access.input_hashes(config,inventory);folder=REPO/'private/phase4';path=folder/'volume.json'
    deploy.validation.private_file(path);before=path.read_bytes();state=json.loads(before)
    original,project,user=prep.authenticate(config);api=ReadOnlyAPI(original.client,original.urls,original.token)
    if state.get('plan')!=PLAN or state.get('project_id')!=project or state.get('user_id')!=user or state.get('apis')!=api.urls or not all(valid_uuid(state.get(k)) for k in ('volume_id','server_id')):
        raise RuntimeError('Observation state identity differs from this authenticated project')
    vm_path=folder/'vm.json';deploy.validation.private_file(vm_path);vm=json.loads(vm_path.read_text())
    if vm.get('project_id')!=project or vm.get('user_id')!=user or vm.get('apis')!=api.urls or vm.get('ids',{}).get('server')!=state['server_id']:
        raise RuntimeError('Observation VM binding differs')
    log('Current volume/VM observations:\n'+json.dumps(observe(api,state,project),indent=2))
    if path.read_bytes()!=before or hashes!=access.input_hashes(config,inventory):raise RuntimeError('Inputs changed while observing; return report')
    log('RESULT: CINDER_VOLUME_STATE_OBSERVED')
    log('Read-only API snapshot. No format, mount, attach, detach, retry, cleanup or checkpoint update. Return report.')
    return 0


class ReattachAPI(API):
    """Recovery mode can attach only; it cannot create, delete or detach anything."""
    def write(self,service,method,path,body,expected):
        if not (service=='compute' and method=='POST' and path=='servers/'+str(self.server_id)+'/os-volume_attachments'):
            raise RuntimeError('Reattachment mode permits only the existing volume attachment')
        return super().write(service,method,path,body,expected)


def reattach_existing(api,state,project,save,probe,log):
    if state.get('stage')=='complete' and not state.get('pending'):
        return workflow(api,state,project,save,probe,log,True)
    if state.get('stage') in ('reattached','remounted') and not state.get('pending') and state.get('reattach_recovery_attempted') is True:
        return workflow(api,state,project,save,probe,log)
    if state.get('stage')!='absent' or state.get('pending')!='reattach same data volume' or state.get('reattach_recovery_attempted'):
        raise RuntimeError('Reattachment requires the reviewed absent-volume checkpoint and original pending request; no retry')
    if not valid_uuid(state.get('volume_id')) or not valid_uuid(state.get('filesystem_uuid')) or state.get('sha256')!=expected_checksum(state) or not state.get('baseline'):
        raise RuntimeError('Saved filesystem identity/checksum/baseline is incomplete')
    entries=api.list('block-storage','volumes/detail?limit=100','volumes')
    matches=[v for v in entries if v.get('id')==state['volume_id'] or v.get('name')==PLAN['name']]
    if len(matches)!=1 or matches[0].get('id')!=state['volume_id']:raise RuntimeError('Tracked volume is missing or its name is duplicated')
    volume=api.get('block-storage','volumes/'+state['volume_id'])['volume']
    volume_check(volume,state,project)
    if volume.get('status')!='available' or volume.get('attachments')!=[]:
        raise RuntimeError('Volume is no longer confirmed available/unattached; observe current state before any change')
    check_attachment(attachments(api,state),state,False)
    result=probe('absent')
    if result.get('volume_absent') is not True or result.get('ready') is not True:
        raise RuntimeError('Guest does not confirm that the tracked volume is absent')
    api.server_id=state['server_id'];api.volume_id=state['volume_id']
    state['reattach_recovery_attempted']=True
    state['reconciled_request']={'stage':state['stage'],'pending':state['pending'],
        'evidence':'Fresh Cinder available/no attachments, Nova no attachments, guest disk absent; user authorized one new attachment'}
    # Replace the old pending marker only after fresh read-only reconciliation. Persist the new
    # marker and single-attempt flag together before issuing the one authorized request.
    state['pending']='authorized single reattachment';save(state)
    log('Reattaching the existing volume once. No volume creation, formatting or detach is permitted in this mode.')
    api.write('compute','POST','servers/'+state['server_id']+'/os-volume_attachments',
        {'volumeAttachment':{'volumeId':state['volume_id'],'delete_on_termination':False}},200)
    state['stage']='reattached';state.pop('pending');save(state)
    return workflow(api,state,project,save,probe,log)


def valid_uuid(value):
    try:return isinstance(value,str) and str(uuid.UUID(value))==value
    except (ValueError,AttributeError):return False


def body_for(state):
    return {'volume':{'name':PLAN['name'],'size':PLAN['size_gib'],'volume_type':state['volume_type']['id'],
        'metadata':{'ofz_chapter':'phase4-e','ofz_server':state['server_id'],'ofz_nonce':state['nonce']}}}


def expected_checksum(state):
    raw=('OpenStack Chapter E persistence test\nserver='+state['server_id']+'\nvolume='+state['volume_id']+'\nnonce='+state['nonce']+'\n').encode()
    return hashlib.sha256(raw).hexdigest()


def volume_check(volume,state,project):
    wanted=body_for(state)['volume'];wanted.pop('volume_type')
    if not net.subset(wanted,volume) or volume.get('id')!=state.get('volume_id') or volume.get('os-vol-tenant-attr:tenant_id')!=project:
        raise RuntimeError('Tracked volume identity/size/metadata differs')
    if volume.get('volume_type')!=state['volume_type']['name'] or volume.get('bootable') not in ('false',False) or volume.get('multiattach') is not False or volume.get('encrypted') is not False:
        raise RuntimeError('Volume must be the tracked unencrypted, nonbootable, single-attach data volume')
    if any(volume.get(k) for k in ('snapshot_id','source_volid','volume_image_metadata','migration_status')):
        raise RuntimeError('Unexpected volume source/image/migration state')
    if volume.get('replication_status') not in (None,'disabled','not-capable'):
        raise RuntimeError('Unexpected volume replication mode')
    if volume.get('status','').startswith('error'):raise RuntimeError('Cinder volume entered an error state; retained for review')
    attachments=volume.get('attachments')
    if not isinstance(attachments,list) or len(attachments)>1 or any(a.get('server_id')!=state['server_id'] or a.get('volume_id')!=state['volume_id'] for a in attachments):
        raise RuntimeError('Unexpected Cinder attachment owner')
    host=volume.get('os-vol-host-attr:host','')
    if host:
        if host not in state['pools']:raise RuntimeError('Volume backend differs from the inspected LVM pools')
        if state.get('backend') not in (None,host):raise RuntimeError('Volume moved to a different storage backend')
    return host


def attachments(api,state):
    return api.get('compute','servers/'+state['server_id']+'/os-volume_attachments')['volumeAttachments']


def check_attachment(rows,state,attached):
    if not isinstance(rows,list):raise RuntimeError('Invalid Nova attachment response')
    if not attached:
        if rows:raise RuntimeError('Expected no data-volume attachments on this VM')
        return
    if len(rows)!=1 or rows[0].get('volumeId')!=state['volume_id'] or rows[0].get('serverId')!=state['server_id'] or rows[0].get('delete_on_termination') is not False:
        raise RuntimeError('Nova attachment/retention policy differs from plan')


def wait_volume(api,state,project,attached):
    deadline=time.monotonic()+300
    while True:
        volume=api.get('block-storage','volumes/'+state['volume_id'])['volume'];host=volume_check(volume,state,project)
        rows=attachments(api,state)
        # Nova and Cinder can settle at different moments; foreign attachments are never a delay.
        if rows:check_attachment(rows,state,True)
        desired='in-use' if attached else 'available'
        if volume.get('status')==desired and bool(volume['attachments'])==attached and bool(rows)==attached:
            check_attachment(rows,state,attached)
            if not host:raise RuntimeError('Cinder did not expose the actual backend host')
            return volume
        if time.monotonic()>=deadline:raise RuntimeError('Volume/attachment did not settle within five minutes; no force/reset action')
        time.sleep(5)


def mutate(state,save,label,operation,stage):
    if state.get('pending'):raise RuntimeError('Uncertain earlier operation requires review')
    state['pending']=label;save(state)
    value=operation()
    state['stage']=stage;state.pop('pending');save(state)
    return value


def guest(ssh,state,action,log):
    expected={k:state[k] for k in ('server_id','nonce','filesystem_uuid')}
    for k in ('baseline','volume_id'):
        if k in state:expected[k]=state[k]
    encoded=base64.b64encode(json.dumps(expected).encode()).decode()
    command=shlex.join(['sudo','-n','/usr/bin/python3','-',action,encoded])
    proc=subprocess.run(ssh+[command],input=(REPO/'scripts/phase4-volume-guest.py').read_text(),capture_output=True,text=True,timeout=300)
    try:value=json.loads(proc.stdout)
    except ValueError:raise RuntimeError('Guest volume helper returned no structured result; no automatic retry') from None
    log('Guest '+action+':\n'+json.dumps(value,indent=2))
    if proc.returncode or value.get('passed') is not True:raise RuntimeError('Guest '+action+' did not pass; return this report')
    return value


def checks_before_create(api,project):
    services=api.list('block-storage','os-services','services')
    for host in ('node1','node2','node3'):
        rows=[s for s in services if s.get('binary')=='cinder-volume' and s.get('host','').split('@')[0].split('.')[0]==host]
        if len(rows)!=1 or rows[0].get('state')!='up' or rows[0].get('status')!='enabled':raise RuntimeError('Cinder volume service not up/enabled: '+host)
    pools=api.get('block-storage','scheduler-stats/get_pools?detail=true')['pools']
    selected={}
    for pool in pools:
        name=pool.get('name','');cap=pool.get('capabilities',{})
        if name.split('@')[0].split('.')[0] not in ('node1','node2','node3') or cap.get('volume_backend_name')!='lvm-1' or cap.get('storage_protocol','').lower()!='iscsi':
            raise RuntimeError('Storage pool is not the course LVM/iSCSI backend')
        selected[name]={k:cap.get(k) for k in ('volume_backend_name','storage_protocol','free_capacity_gb','total_capacity_gb')}
    if len(selected)!=3 or len({n.split('@')[0].split('.')[0] for n in selected})!=3:raise RuntimeError('Expected one LVM pool per node')
    free=[v['free_capacity_gb'] for v in selected.values()]
    if not any(type(v) in (int,float) and math.isfinite(v) and v>=PLAN['size_gib'] for v in free):raise RuntimeError('No observed LVM pool confirms 1 GiB free')
    vtype=api.get('block-storage','types/default')['volume_type']
    if not valid_uuid(vtype.get('id')) or not isinstance(vtype.get('name'),str):raise RuntimeError('Default Cinder volume type needs review')
    specs=vtype.get('extra_specs',{})
    if specs not in ({},{'volume_backend_name':'lvm-1'}):raise RuntimeError('Default volume type has unreviewed extra specifications')
    quota=api.get('block-storage','os-quota-sets/'+project+'?usage=True')['quota_set']
    for key in ('volumes','gigabytes','volumes_'+vtype['name'],'gigabytes_'+vtype['name']):
        q=quota.get(key)
        if not isinstance(q,dict) or any(type(q.get(k)) is not int for k in ('limit','in_use','reserved')) or q['limit'] < -1 or min(q['in_use'],q['reserved'])<0:
            raise RuntimeError('Cinder quota evidence incomplete: '+key)
        if q['limit']!=-1 and q['limit']-q['in_use']-q['reserved']<1:raise RuntimeError('Insufficient Cinder quota: '+key)
    per=quota.get('per_volume_gigabytes',{}).get('limit')
    if type(per) is not int or per < -1 or (per!=-1 and per<1):raise RuntimeError('Per-volume size quota needs review')
    return {'id':vtype['id'],'name':vtype['name']},selected


def vm_identity(api,vm,project,user,allowed_volume=None):
    server=api.get('compute','servers/'+vm['ids']['server'])['server']
    disks=server.get('os-extended-volumes:volumes_attached')
    if not isinstance(disks,list) or any(d.get('id')!=allowed_volume for d in disks) or len(disks)>1:
        raise RuntimeError('VM has an unexpected volume; no attachment changes')
    check=copy.deepcopy(server);check['os-extended-volumes:volumes_attached']=[]
    boot.check_server(check,project,user,vm['networks'],vm['prepared'])
    if server.get('status')!='ACTIVE' or server.get('OS-EXT-STS:task_state') is not None:raise RuntimeError('Tracked VM must be ACTIVE with no task in progress')
    host=server.get('OS-EXT-SRV-ATTR:host','').split('.')[0]
    if host not in ('node1','node2','node3'):raise RuntimeError('VM compute host is unknown')
    return host


def workflow(api,state,project,save,probe,log,verify=False):
    if state.get('pending'):raise RuntimeError('An earlier request has an uncertain outcome; return report, do not remove state')
    if state.get('stage') not in STAGES:raise RuntimeError('Unknown volume checkpoint')
    volumes=api.list('block-storage','volumes/detail?limit=100','volumes')
    same=[v for v in volumes if v.get('name')==PLAN['name'] or v.get('id')==state.get('volume_id')]
    if any(v.get('id')!=state.get('volume_id') for v in same) or len(same)>1:raise RuntimeError('Untracked/duplicate volume; no adoption')
    if state.get('volume_id'):
        if len(same)!=1:raise RuntimeError('Tracked volume is missing')
        volume_check(same[0],state,project)
    elif state['stage']!='new':raise RuntimeError('Volume ID missing from an existing checkpoint')
    if verify and state['stage']!='complete':raise RuntimeError('--verify requires a completed persistence exercise; it performs no repair')
    api.server_id=state['server_id'];api.volume_id=state.get('volume_id')
    if state['stage']=='new':
        check_attachment(attachments(api,state),state,False)
        if 'baseline' not in state:state['baseline']=probe('baseline')['baseline'];save(state)
        api.create_body=body_for(state)
        def create():
            log('Creating one 1 GiB data volume; Cinder chooses its storage node.')
            obj=api.write('block-storage','POST','volumes',api.create_body,202)['volume']
            if not valid_uuid(obj.get('id')):raise RuntimeError('Create response has no valid volume UUID')
            state['volume_id']=obj['id'];api.volume_id=obj['id']
        mutate(state,save,'create volume',create,'created')
    if state['stage']=='created':
        log('Waiting for the new volume to be available, then attaching it to lab-vm-01.')
        volume=wait_volume(api,state,project,False);state['backend']=volume['os-vol-host-attr:host'];save(state)
        mutate(state,save,'attach volume',lambda:api.write('compute','POST','servers/'+state['server_id']+'/os-volume_attachments',
            {'volumeAttachment':{'volumeId':state['volume_id'],'delete_on_termination':False}},200),'attached')
    if state['stage']=='attached':
        log('Checking the attached disk identity and blank contents before initialization.')
        wait_volume(api,state,project,True)
        wait_guest(probe,'inspect')
        mutate(state,save,'initialize new blank data filesystem',lambda:probe('initialize'),'initialized')
    if state['stage']=='initialized':
        def write():
            result=probe('write')
            if result.get('sha256')!=expected_checksum(state):raise RuntimeError('Guest write checksum differs from intended test bytes')
            state['sha256']=result['sha256']
        mutate(state,save,'write and flush test file',write,'written')
    if state['stage']=='written':
        mutate(state,save,'flush and unmount data filesystem',lambda:probe('unmount'),'unmounted')
    if state['stage']=='unmounted':
        log('Confirming safe unmount, then detaching only the tracked data volume.')
        wait_volume(api,state,project,True)
        # Recheck the guest immediately before asking Nova to detach: no forced/lazy detach.
        result=probe('unmounted')
        if result.get('unmounted') is not True:raise RuntimeError('Guest did not confirm safe unmount')
        mutate(state,save,'detach data volume',lambda:api.write('compute','DELETE','servers/'+state['server_id']+'/os-volume_attachments/'+state['volume_id'],None,202),'detached')
    if state['stage']=='detached':
        wait_volume(api,state,project,False);wait_guest(probe,'absent');state['stage']='absent';save(state)
    if state['stage']=='absent':
        log('Reattaching the same volume; the existing filesystem must retain its UUID and data.')
        mutate(state,save,'reattach same data volume',lambda:api.write('compute','POST','servers/'+state['server_id']+'/os-volume_attachments',
            {'volumeAttachment':{'volumeId':state['volume_id'],'delete_on_termination':False}},200),'reattached')
    if state['stage']=='reattached':
        wait_volume(api,state,project,True);wait_guest(probe,'present')
        mutate(state,save,'remount existing data filesystem',lambda:probe('mount'),'remounted')
    volume=wait_volume(api,state,project,True)
    result=probe('verify')
    if result.get('sha256')!=state.get('sha256') or result.get('sha256')!=expected_checksum(state):raise RuntimeError('File checksum changed across detach/reattach')
    if state['stage']=='remounted':state['stage']='complete';save(state)
    return {'volume_id':state['volume_id'],'size_gib':1,'storage_backend':volume['os-vol-host-attr:host'],
        'storage_host':volume['os-vol-host-attr:host'].split('@')[0],'server_id':state['server_id'],
        'attachment_retained_on_VM_deletion':True,'guest':result,'persistence':'same SHA256 before and after safe detach/reattach',
        'limits':'Local LVM volume, not replicated; no storage HA, VM migration, host failure or reboot test.'}


def wait_guest(probe,action):
    # Poll only explicit read-only visibility probes; never retry a guest mutation or an evidence failure.
    deadline=time.monotonic()+90
    while True:
        result=probe(action)
        if result.get('ready') is not False:return result
        if time.monotonic()>=deadline:raise RuntimeError('Guest disk hotplug visibility timed out')
        time.sleep(3)


def execute(log,verify=False,reattach=False):
    deploy.source_guard();env=deploy.tools.tool_environment()
    inventory,config=deploy.validation.local_checks(lambda line:None,env,deploy.validation.CA_FINGERPRINT)
    hashes=access.input_hashes(config,inventory)
    folder=REPO/'private/phase4';deploy.inv.private_dir(folder)
    # Also take Chapter D's lock so its command cannot race this attachment exercise.
    handles=[]
    try:
        for filename in ('vm.lock','volume.lock'):
            path=folder/filename
            if path.is_symlink():raise RuntimeError('Lock path must not be a symlink')
            handle=path.open('a');handles.append(handle);os.chmod(path,0o600)
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('Another VM/volume command is running') from None
        original,project,user=prep.authenticate(config);api=(ReattachAPI if reattach else API)(original.client,original.urls,original.token)
        vm_path=folder/'vm.json';deploy.validation.private_file(vm_path);vm=json.loads(vm_path.read_text())
        networks,prepared,public=boot.load_inputs(api,project,user,folder)
        binding={'plan':boot.PLAN,'project_id':project,'user_id':user,'apis':api.urls,'networks':networks,'prepared':prepared}
        if any(vm.get(k)!=v for k,v in binding.items()) or vm.get('pending') or set(vm.get('ids',{}))!={'server','port','floating_ip'} or not all(valid_uuid(v) for v in vm['ids'].values()) or not vm.get('host_fingerprint'):
            raise RuntimeError('Chapter D private identity/evidence needs review')
        path=folder/'volume.json'
        if path.is_symlink():raise RuntimeError('Volume state must not be a symlink')
        binding={'plan':PLAN,'project_id':project,'user_id':user,'apis':api.urls,'server_id':vm['ids']['server']}
        save=lambda value:net.write_state(path,value)
        if path.exists():
            deploy.validation.private_file(path);state=json.loads(path.read_text())
            if any(state.get(k)!=v for k,v in binding.items()):raise RuntimeError('Volume state belongs to different inputs')
        else:
            if verify or reattach:raise RuntimeError('Existing volume state is required; this mode creates nothing')
            vtype,pools=checks_before_create(api,project)
            state=dict(binding,stage='new',nonce=secrets.token_hex(16),filesystem_uuid=str(uuid.uuid4()),volume_type=vtype,pools=pools)
        if state.get('pending') and not reattach:raise RuntimeError('Earlier volume operation uncertain; return report without retry')
        if not valid_uuid(state.get('filesystem_uuid')) or not re.fullmatch(r'[0-9a-f]{32}',state.get('nonce','')):raise RuntimeError('Invalid volume state identifiers')
        if state.get('volume_id') and not valid_uuid(state['volume_id']):raise RuntimeError('Invalid tracked volume UUID')
        host=vm_identity(api,vm,project,user,state.get('volume_id'))
        # Validate current port/FIP ownership without the Chapter D no-data-volumes assertion.
        port=api.get('network','v2.0/ports/'+vm['ids']['port'])['port'];private=boot.check_port(port,project,networks,prepared,vm['ids']['server'])
        if port.get('device_id')!=vm['ids']['server'] or port.get('status')!='ACTIVE':raise RuntimeError('VM port not ACTIVE on its tracked server')
        fip=api.get('network','v2.0/floatingips/'+vm['ids']['floating_ip'])['floatingip'];address=boot.check_floating(fip,project,networks,vm['ids']['port'],private)
        if fip.get('status')!='ACTIVE':raise RuntimeError('VM floating IP not ACTIVE')
        known=folder/'lab-vm-01-known-hosts';deploy.validation.private_file(known)
        fields=known.read_text().split()
        if len(fields)!=3 or fields[0]!=address or boot.key_fingerprint(' '.join(fields[1:]))!=vm['host_fingerprint']:raise RuntimeError('Guest pinned SSH identity differs; no trust replacement')
        boot.ensure_agent(public,log);ssh=boot.ssh_command(address,known)
        def probe(action):
            if reattach and action not in ('absent','present','mount','verify'):raise RuntimeError('Reattachment cannot initialize, write a file, unmount or format')
            return guest(ssh,state,action,log)
        result=reattach_existing(api,state,project,save,probe,log) if reattach else workflow(api,state,project,save,probe,log,verify)
        if hashes!=access.input_hashes(config,inventory):raise RuntimeError('Deployment inputs changed during volume exercise')
        if vm_identity(api,vm,project,user,state['volume_id'])!=host:raise RuntimeError('VM placement changed during the exercise')
        result.update(compute_host=host,floating_ip=address)
        log('Volume summary:\n'+json.dumps(result,indent=2));log('RESULT: CINDER_VOLUME_PERSISTENCE_VERIFIED')
        log('Volume remains attached and mounted. No fstab change, deletion, migration or failover test. Return this report.')
    finally:
        for handle in reversed(handles):handle.close()
    return 0


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--apply',action='store_true');mode.add_argument('--verify',action='store_true');mode.add_argument('--observe',action='store_true');mode.add_argument('--reattach',action='store_true');args=parser.parse_args(argv)
    if not args.apply and not args.verify and not args.observe and not args.reattach:
        print('PLAN ONLY — no connections or files changed:\n'+json.dumps(PLAN,indent=2))
        print('--apply creates one volume, formats only its verified blank guest disk, writes a test file, safely detaches/reattaches and verifies persistence. --verify checks the completed mounted volume without cloud or guest mutations. --observe reads API state even with a pending request and preserves that marker. --reattach makes one explicitly authorized existing-volume attachment after fresh reconciliation, then mounts and verifies existing data; never formats.');return 0
    deploy.validation.config_step.load_step('volume_r2d2','phase2-check.py').require_r2d2();deploy.tools.check_paths(ENV)
    if not args.observe and not sys.stdin.isatty():raise RuntimeError('Use an interactive R2d2 terminal for the guest key passphrase')
    if Path(sys.prefix)!=ENV:
        os.execve(str(ENV/'bin/python'),[str(ENV/'bin/python'),str(Path(__file__).resolve()),'--observe' if args.observe else '--reattach' if args.reattach else '--verify' if args.verify else '--apply'],deploy.tools.tool_environment())
    os.umask(0o077);reports=REPO/'reports';deploy.inv.private_dir(reports)
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-');fd,name=tempfile.mkstemp(prefix='phase4-volume-'+stamp,suffix='.txt',dir=reports)
    print('Private report: '+name,flush=True);status=1
    with os.fdopen(fd,'w') as output:
        def log(line):print(line,flush=True);output.write(line+'\n');output.flush()
        log('Phase 4 Chapter E — '+('OBSERVE API state only; preserve pending checkpoint' if args.observe else 'REATTACH existing volume only; no format' if args.reattach else 'VERIFY completed volume only' if args.verify else 'APPLY one data volume and test persistence')+'\nPlan:\n'+json.dumps(PLAN,indent=2))
        try:status=execute_observation(log) if args.observe else execute(log,args.verify,args.reattach)
        except (Exception,KeyboardInterrupt) as error:
            log('RESULT: CINDER_VOLUME_NEEDS_REVIEW')
            log(str(error) if isinstance(error,RuntimeError) else 'Stopped ('+type(error).__name__+'); private details withheld.')
            log('Resources/checkpoints retained. No force detach, deletion, repeated format, automatic retry or cleanup. Return report.')
    print('Read report: cat '+shlex.quote(name));return status

if __name__=='__main__':
    try:sys.exit(main())
    except (RuntimeError,OSError) as error:print('Stopped: '+str(error),file=sys.stderr);sys.exit(1)

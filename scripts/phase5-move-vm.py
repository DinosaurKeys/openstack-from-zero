#!/usr/bin/env python3
"""Prepare one node1-to-node2 block live migration; apply only after plan review."""
import sys
sys.dont_write_bytecode=True
import argparse
import datetime as dt
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import time
import uuid

REPO=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('move_inspection',REPO/'scripts/phase5-migration.py')
inspection=importlib.util.module_from_spec(spec);spec.loader.exec_module(inspection)
volume=inspection.volume;boot=inspection.boot;prep=inspection.prep;net=inspection.net;deploy=inspection.deploy;access=inspection.access;inv=inspection.inv;vip=inspection.vip;db=inspection.db;ENV=inspection.ENV
PLAN={'version':1,'source':'node1','destination':'node2','type':'block live migration','microversion':'2.79',
      'root_disk':'Copy the existing local root disk','data_volume':'Keep the same volume on its original storage backend',
      'limits':'One Nova request; no force, retry, evacuation, reboot, detach, format, rollback, host or configuration change',
      'observation_seconds':1200}
TERMINAL={'completed','error','failed','cancelled','canceled','done','confirmed','reverted'}


class API(volume.API):
    def __init__(self,client,urls,token,server,destination,allow_move=False):
        super().__init__(client,urls,token)
        if not volume.valid_uuid(server) or destination!=PLAN['destination']:raise RuntimeError('Unapproved migration identity')
        self.server_id=server;self.destination=destination;self.allow_move=allow_move;self.attempted=False
    def request(self,service,method,path,body=None,expected=200):
        if method=='GET' and body is None:return super().request(service,method,path,None,expected)
        wanted={'os-migrateLive':{'host':self.destination,'block_migration':True}}
        if not(self.allow_move and not self.attempted and service=='compute' and method=='POST' and path=='servers/'+self.server_id+'/action' and body==wanted and expected==202):raise RuntimeError('Only one exact approved live-migration request is permitted')
        self.attempted=True
        return super().request(service,method,path,body,expected)
    def write(self,*args,**kwargs):raise RuntimeError('Use only the reviewed single migration operation')
    def move(self):return self.request('compute','POST','servers/'+self.server_id+'/action',{'os-migrateLive':{'host':self.destination,'block_migration':True}},202)
    def history(self):
        page=self.request('compute','GET','os-migrations?instance_uuid='+self.server_id+'&limit=100')
        rows=page.get('migrations')
        if not isinstance(rows,list) or any(r.get('instance_uuid')!=self.server_id or not volume.valid_uuid(r.get('uuid')) for r in rows):raise RuntimeError('Unexpected migration-history identities')
        return rows


def read_state(path,required=False):
    if not path.exists() and not path.is_symlink():
        if required:raise RuntimeError('Required prepared migration state is missing')
        return None
    deploy.validation.private_file(path);return json.loads(path.read_text())


def boot_id(ssh):
    result=subprocess.run(ssh+['cat /proc/sys/kernel/random/boot_id'],capture_output=True,text=True,timeout=20)
    value=result.stdout.strip()
    if result.returncode or not volume.valid_uuid(value):raise RuntimeError('Guest boot-ID read not confirmed')
    return value


def node_identity(record):
    return {'containers':record['containers'],'nova_config_sha256':record['nova_config']['config_sha256'],
      'libvirt_version':record['libvirt_version'],'instances_path':record['instances_storage']['container_path']}


def workload_binding(ctx):
    return {'inputs':ctx['hashes'],'project_id':ctx['project'],'user_id':ctx['user'],'apis':ctx['api'].urls,
      'server_id':ctx['vm']['ids']['server'],'private_ip':ctx['workload']['private_ip'],'floating_ip':ctx['workload']['floating_ip'],
      'volume_id':ctx['vstate']['volume_id'],'filesystem_uuid':ctx['vstate']['filesystem_uuid'],
      'checksum':ctx['vstate']['sha256'],'storage_backend':ctx['workload']['storage_backend']}


def validate_plan(plan,ctx,now):
    if not isinstance(plan,dict) or plan.get('plan')!=PLAN or not volume.valid_uuid(plan.get('id')) or any(plan.get(k)!=v for k,v in workload_binding(ctx).items()):raise RuntimeError('Prepared migration identity differs')
    if not 0<=now-plan.get('prepared_unix',0)<=1800:raise RuntimeError('Prepared plan is older than 30 minutes; review a fresh preparation')
    if ctx['source']!=PLAN['source']:raise RuntimeError('VM is no longer on the approved source')
    if plan.get('nodes')!={n:node_identity(r) for n,r in ctx['records'].items()}:raise RuntimeError('Compute container/configuration identity changed since preparation')


def placement_room(value,requirements):
    for resource,needed in [('VCPU',requirements['vcpus']),('MEMORY_MB',requirements['ram_mb']),('DISK_GB',requirements['disk_gb'])]:
        invt=value.get('inventories',{}).get(resource);used=value.get('usages',{}).get(resource)
        if not isinstance(invt,dict) or not isinstance(used,(int,float)):raise RuntimeError('Placement capacity unavailable: '+resource)
        total=(invt['total']-invt.get('reserved',0))*invt.get('allocation_ratio',1)
        if used+needed>total or needed<invt.get('min_unit',1) or needed>invt.get('max_unit',invt['total']) or needed%invt.get('step_size',1):raise RuntimeError('Placement headroom does not cover guest '+resource)


def prerequisite_checks(ctx,api,log):
    if ctx['source']!=PLAN['source']:raise RuntimeError('This lesson permits only node1 to node2; no automatic move back')
    dest=PLAN['destination'];summary=ctx['summaries'][dest]
    for key in ('inspection_complete','ram_covers_guest_plus_1GiB_review_margin','disk_covers_root_plus_2GiB_review_margin','source_guest_cpu_compatible','authenticated_libvirt_read_from_source','nova_compute_up_enabled','placement_capacity_collected'):
        if summary.get(key) is not True:raise RuntimeError('Destination prerequisite not confirmed: '+key)
    records=ctx['records'];domain=records[PLAN['source']]['domain']
    if domain.get('machine')!='pc-i440fx-noble' or domain.get('emulator')!='/usr/bin/qemu-system-x86_64' or domain.get('virt_type')!='kvm' or domain.get('host_devices')!=0:raise RuntimeError('VM machine/emulator/passthrough differs from reviewed lab')
    memory=domain.get('memory',{});units={'B':1,'bytes':1,'KiB':1024,'MiB':1024**2,'GiB':1024**3}
    if memory.get('unit') not in units or int(memory.get('value',0))*units[memory['unit']]!=prep.PLAN['ram_mb']*1024**2 or int(domain.get('vcpus',0))!=prep.PLAN['vcpus']:raise RuntimeError('Actual VM memory/vCPUs differ from reviewed capacity requirements')
    disks=domain.get('disks',[])
    if len(disks)!=2 or {d.get('target') for d in disks}!={'vda','vdb'} or any(d.get('device')!='disk' or d.get('readonly') for d in disks):raise RuntimeError('Expected only writable root vda and data vdb disks')
    root=next(d for d in disks if d['target']=='vda')
    if root.get('type')!='file' or root.get('driver_type') not in ('qcow2','raw'):raise RuntimeError('Expected a local QCOW2 or raw root disk')
    paths={r['instances_storage']['container_path'] for r in records.values()}
    if paths!={'/var/lib/nova/instances'}:raise RuntimeError('Instance paths differ from the reviewed lab')
    if any(r.get('migration_type')=='live-migration' and r.get('status') not in TERMINAL for r in api.history()):raise RuntimeError('A migration is already in progress')
    placement_room(inspection.capacity(api,dest),{'vcpus':prep.PLAN['vcpus'],'ram_mb':prep.PLAN['ram_mb'],'disk_gb':prep.PLAN['disk_gb']})
    nodes=inspection.Nodes(ctx['env'],ctx['passwords']);support={n:nodes.call(n,'vnc',{}) for n in (PLAN['source'],dest)}
    for n,value in support.items():
        if not value.get('observed') or value.get('graphics_rewrite_test_passed') is not True or value.get('graphics_rewrite_wired') is not True:raise RuntimeError(n+': installed Nova VNC-address rewrite not confirmed')
    inspection.detail(log,'installed_nova_graphics_support',support)
    log('Selected destination prerequisites: PASS; installed Nova VNC address handling: PASS')
    return support


def validate_receipt(state):
    if not isinstance(state,dict) or state.get('plan')!=PLAN or state.get('attempted') is not True or not volume.valid_uuid(state.get('server_id')) or not volume.valid_uuid(state.get('plan_id')) or not volume.valid_uuid(state.get('guest_boot_id')):
        raise RuntimeError('Recorded migration scope/identity is invalid')
    ids=state.get('before_migration_ids')
    if not isinstance(ids,list) or any(not volume.valid_uuid(v) for v in ids) or len(set(ids))!=len(ids):raise RuntimeError('Recorded migration history is invalid')


def migration_snapshot(api,state):
    server=api.get('compute','servers/'+state['server_id'])['server']
    if server.get('id')!=state['server_id']:raise RuntimeError('Observed VM identity differs')
    rows=[r for r in api.history() if r['uuid'] not in state['before_migration_ids']]
    if len(rows)>1:raise RuntimeError('More than one new migration observed; do not issue another request')
    item=rows[0] if rows else None
    if item and (item.get('migration_type')!='live-migration' or item.get('source_compute')!=PLAN['source'] or item.get('dest_compute') not in (None,PLAN['destination'])):raise RuntimeError('Observed migration type/source/destination differs')
    return {'server_status':server.get('status'),'host':server.get('OS-EXT-SRV-ATTR:host'),'task':server.get('OS-EXT-STS:task_state'),
      'migration':{k:item.get(k) for k in ('uuid','status','source_compute','dest_compute','migration_type')} if item else None}


def safe_message(value):
    if value is None:return None
    line=str(value).splitlines()[0] if str(value).splitlines() else ''
    if re.search(r'(?i)password|passwd|token|secret|authorization|credential|transport_url',line):return '[sensitive diagnostic text withheld]'
    line=re.sub(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s\"'<>]+",'[URI withheld]',line)
    return ' '.join(line.split())[:500]


def exception_name(traceback):
    # Return a class name only, never stack frames, locals or exception values.
    lines=[line.strip() for line in str(traceback or '').splitlines() if line.strip()]
    if not lines:return None
    if ':' not in lines[-1]:return None
    name=lines[-1].split(':',1)[0]
    if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]{0,150}',name) and name.startswith(('nova.','libvirt.','oslo_')):return name
    return name if re.fullmatch(r'[A-Z][A-Za-z0-9_]{0,100}(?:Error|Exception)',name) else None


def fault_diagnostics(api,state):
    server=api.get('compute','servers/'+state['server_id'])['server']
    if server.get('id')!=state['server_id']:raise RuntimeError('Diagnostic VM identity differs')
    fault=server.get('fault') or {}
    result={'fault_code':fault.get('code'),'fault_message':safe_message(fault.get('message')),'latest_live_migration_action':None,
      'scope':'API fault/action observations only; guest running state not established'}
    try:
        path='servers/'+state['server_id']+'/os-instance-actions'
        actions=api.request('compute','GET',path+'?limit=100').get('instanceActions')
        if not isinstance(actions,list):raise RuntimeError('Action list response unavailable')
        matches=[a for a in actions if a.get('action')=='live-migration' and a.get('instance_uuid')==state['server_id']]
        if not matches:return result
        action=max(matches,key=lambda a:a.get('start_time') or '')
        request=action.get('request_id','')
        if not re.fullmatch(r'req-[a-f0-9-]{36}',request) or not volume.valid_uuid(request[4:]):raise RuntimeError('Action request identity unavailable')
        detail=api.request('compute','GET',path+'/'+request)['instanceAction']
        if detail.get('instance_uuid')!=state['server_id'] or detail.get('request_id')!=request or detail.get('action')!='live-migration':raise RuntimeError('Action detail identity differs')
        events=detail.get('events') or []
        failed=[e for e in events if str(e.get('result','')).lower() not in ('success','', 'none')]
        result['latest_live_migration_action']={'request_id':request,'start_time':safe_message(action.get('start_time')),
          'message':safe_message(detail.get('message')),'failed_events':[{'event':safe_message(e.get('event')),'result':safe_message(e.get('result')),
          'host':safe_message(e.get('host')),'exception_type':exception_name(e.get('traceback'))} for e in failed[:3]],
          'correlation':'Latest live-migration action for this VM; original POST request ID was not saved'}
    except (RuntimeError,KeyError,TypeError,ValueError) as error:
        result['action_details']='Unavailable: '+(safe_message(str(error)) if isinstance(error,RuntimeError) else type(error).__name__)
    return result


def completed(snapshot):
    return snapshot['server_status']=='ACTIVE' and snapshot['host']==PLAN['destination'] and snapshot['task'] is None and snapshot['migration'] is not None and snapshot['migration']['status']=='completed' and snapshot['migration']['dest_compute']==PLAN['destination']


def wait_for_completion(api,state,save,log):
    deadline=time.monotonic()+PLAN['observation_seconds'];last=None
    while True:
        snapshot=migration_snapshot(api,state);state['last_observation']=snapshot;save(state)
        text=json.dumps(snapshot,sort_keys=True)
        if text!=last:log('Nova: host='+str(snapshot['host'])+'; task='+str(snapshot['task'])+'; migration='+str((snapshot['migration'] or {}).get('status','not visible yet')));last=text
        if completed(snapshot):return snapshot
        if snapshot['server_status']=='ERROR' or (snapshot['migration'] or {}).get('status') in ('error','failed','cancelled','canceled'):raise RuntimeError('Nova migration needs review; no retry or automatic rollback')
        if time.monotonic()>=deadline:raise RuntimeError('Observation window ended; migration may still be running. Use --observe, not another migration request')
        time.sleep(3)


def final_checks(ctx,state,log):
    if ctx['source']!=PLAN['destination'] or any(state.get(k)!=v for k,v in workload_binding(ctx).items()):raise RuntimeError('Final VM/network/volume/configuration binding differs')
    if boot_id(ctx['ssh'])!=state['guest_boot_id']:raise RuntimeError('Guest reboot detected; live-migration proof did not pass')
    nodes=inspection.Nodes(ctx['env'],ctx['passwords']);running={n:nodes.call(n,'running',{'server_id':state['server_id']}) for n in inv.NODES}
    if any(not r.get('observed') for r in running.values()) or [n for n,r in running.items() if r.get('VM_running')]!=[PLAN['destination']]:raise RuntimeError('VM must run only on the destination hypervisor')
    graphics=ctx['records'][PLAN['destination']]['domain'].get('vnc_listen')
    expected=ctx['records'][PLAN['destination']]['nova_config']['vnc']['server_listen'].strip('"\'')
    if graphics!=[expected]:raise RuntimeError('Destination VNC listen address does not match its configuration')
    old=state['services'];current=ctx['services']
    if old['cluster_uuid']!=current[1] or old['api_owner']!=current[2]:raise RuntimeError('Database cluster UUID or API VIP owner changed during migration')
    for n,r in current[0].items():
        if old['db_ids'][n]!=r['mariadb']['id'] or old['protected'][n]!=r['baseline']['facts']['protected_config_hashes']:raise RuntimeError('Database identity or protected host configuration changed')
    if state['nodes']!={n:node_identity(r) for n,r in ctx['records'].items()}:raise RuntimeError('Compute configuration/container identity changed during migration')
    log('Final checks: VM only on node2; same guest boot ID, IPs, volume backend/UUID/checksum; host/API/database checks PASS')


class NetworkSampler(vip.Sampler):
    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=22)
        if self.thread and self.thread.is_alive():raise RuntimeError('Bounded guest probe did not finish; migration state retained for review')


def apply_move(api,state,save,check,log,sampler_factory=NetworkSampler):
    if state.get('attempted'):raise RuntimeError('This migration plan was already attempted')
    state.update(attempted=True,pending=True,stage='single live-migration request may be issued');save(state)
    sampler=sampler_factory(check)
    try:
        sampler.start();log('Requesting one node1 -> node2 block live migration through Nova.')
        api.move();state['stage']='Nova accepted the migration request';save(state)
        result=wait_for_completion(api,state,save,log);state['stage']='Nova reports completed; guest/host verification pending';save(state)
        return result
    finally:
        sampler.stop()
        inspection.detail(log,'sampled_network_probes',sampler.records)
        log('Network samples: '+str(len(sampler.records))+'; unsuccessful: '+str(sum(not s['ok'] for s in sampler.records))+'; sampled results are not exact downtime')


def continuation(mode,log):
    folder=REPO/'private/phase5';plan_path=folder/'move-vm-plan.json';receipt=folder/'move-vm-state.json'
    def work(ctx,log):
        old=read_state(receipt);plan=read_state(plan_path)
        if old and old.get('pending') and mode!='verify':raise RuntimeError('An earlier migration request is unresolved; use --observe before another action')
        binding=workload_binding(ctx);api=API(ctx['api'].client,ctx['api'].urls,ctx['api'].token,binding['server_id'],PLAN['destination'],allow_move=mode=='apply')
        save=lambda value:net.write_state(receipt,value)
        if mode=='verify':
            if not old or not old.get('attempted'):raise RuntimeError('No attempted migration to verify')
            validate_receipt(old)
            snapshot=migration_snapshot(api,old)
            if not completed(snapshot):raise RuntimeError('Nova completion is not confirmed; use --observe')
            final_checks(ctx,old,log);old.update(pending=False,stage='migration and final checks verified');save(old)
            log('RESULT: VM_LIVE_MIGRATION_VERIFIED');return
        support=prerequisite_checks(ctx,api,log)
        if mode=='prepare':
            if old and old.get('attempted'):raise RuntimeError('A migration was already attempted; review its report before any new preparation')
            plan=dict(binding,plan=PLAN,id=str(uuid.uuid4()),prepared_unix=time.time(),nodes={n:node_identity(r) for n,r in ctx['records'].items()},nova_graphics_support=support)
            net.write_state(plan_path,plan)
            log('Prepared: existing lab-vm-01, node1 -> node2, copy local root disk; data stays on '+binding['storage_backend'])
            log('No migration requested. Review this report before --apply. Plan expires in 30 minutes.')
            log('RESULT: VM_MIGRATION_PREPARED');return
        validate_plan(plan,ctx,time.time())
        if plan['nova_graphics_support']!=support:raise RuntimeError('Installed Nova graphics code changed since preparation')
        if old and old.get('attempted'):raise RuntimeError('Migration already attempted; never repeat the request automatically')
        services=ctx['services'];state=dict(binding,plan=PLAN,plan_id=plan['id'],nodes=plan['nodes'],guest_boot_id=boot_id(ctx['ssh']),
          before_migration_ids=[r['uuid'] for r in api.history()],services={'cluster_uuid':services[1],'api_owner':services[2],
          'db_ids':{n:r['mariadb']['id'] for n,r in services[0].items()},'protected':{n:r['baseline']['facts']['protected_config_hashes'] for n,r in services[0].items()}})
        check=lambda:inspection.router.guest_probe(ctx['ssh'],binding['floating_ip'],net.PLAN['private_gateway'])
        apply_move(api,state,save,check,log)
        # Re-run the full existing baseline against the now-current compute host.
        # Release no locks and make no second API mutation.
        ctx['source']=volume.vm_identity(ctx['api'],ctx['vm'],ctx['project'],ctx['user'],ctx['vstate']['volume_id'])
        if not check()['ok']:raise RuntimeError('Final guest network probe did not pass')
        data=volume.guest(ctx['ssh'],ctx['vstate'],'verify',lambda line:None)
        if data.get('sha256')!=binding['checksum'] or data.get('filesystem_uuid')!=binding['filesystem_uuid']:raise RuntimeError('Final data verification differs')
        obj=ctx['api'].get('block-storage','volumes/'+binding['volume_id'])['volume'];backend=volume.volume_check(obj,ctx['vstate'],ctx['project'])
        volume.check_attachment(volume.attachments(ctx['api'],ctx['vstate']),ctx['vstate'],True)
        if backend!=binding['storage_backend'] or obj.get('status')!='in-use':raise RuntimeError('Volume backend/attachment changed')
        port=ctx['api'].get('network','v2.0/ports/'+ctx['vm']['ids']['port'])['port'];private=boot.check_port(port,ctx['project'],ctx['vm']['networks'],ctx['vm']['prepared'],ctx['vm']['ids']['server'])
        floating=ctx['api'].get('network','v2.0/floatingips/'+ctx['vm']['ids']['floating_ip'])['floatingip'];address=boot.check_floating(floating,ctx['project'],ctx['vm']['networks'],ctx['vm']['ids']['port'],private)
        if private!=binding['private_ip'] or address!=binding['floating_ip'] or port.get('status')!='ACTIVE' or floating.get('status')!='ACTIVE':raise RuntimeError('Final network identity/state differs')
        ctx['records']=inspection.Nodes(ctx['env'],ctx['passwords']).all({'server_id':binding['server_id'],'source':PLAN['destination']})
        if any(not r.get('observed') for r in ctx['records'].values()):raise RuntimeError('Final compute observations incomplete')
        ctx['services']=db.full_checks(db.Nodes(ctx['env'],ctx['passwords']),ctx['profile'],vip.ProbeClient(ctx['profile']['cacert']))
        if binding['inputs']!=access.input_hashes(ctx['config'],ctx['inventory']):raise RuntimeError('Deployment inputs changed during migration')
        final_checks(ctx,state,log);state.update(pending=False,stage='migration and final checks verified');save(state)
        log('RESULT: VM_LIVE_MIGRATION_VERIFIED')
    work.destination=PLAN['destination']
    return work


def observe(log):
    deploy.source_guard();env=deploy.tools.tool_environment();inventory,config=deploy.validation.local_checks(lambda line:None,env)
    folder=REPO/'private/phase5';path=folder/'migration.lock'
    if path.is_symlink():raise RuntimeError('Unsafe migration lock')
    with path.open('a') as lock:
        os.chmod(path,0o600)
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Migration exercise still running in another terminal') from None
        state=read_state(folder/'move-vm-state.json',True)
        validate_receipt(state)
        original,project,user=prep.authenticate(config)
        if state.get('inputs')!=access.input_hashes(config,inventory):raise RuntimeError('Recorded deployment inputs changed')
        if state.get('project_id')!=project or state.get('user_id')!=user or state.get('apis')!=original.urls:raise RuntimeError('Recorded migration identity differs')
        api=API(original.client,original.urls,original.token,state['server_id'],PLAN['destination'])
        snapshot=migration_snapshot(api,state);inspection.detail(log,'migration_snapshot',snapshot)
        log(json.dumps(snapshot,indent=2))
        diagnostic=fault_diagnostics(api,state);inspection.detail(log,'fault_diagnostics',diagnostic)
        log('Nova fault/action details:\n'+json.dumps(diagnostic,indent=2))
        log('RESULT: VM_MIGRATION_STATE_OBSERVED')
        log('GET snapshot only; pending marker unchanged. No retry or recovery action. Return report.')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);group=parser.add_mutually_exclusive_group()
    for name in ('prepare','apply','observe','verify'):group.add_argument('--'+name,action='store_true')
    args=parser.parse_args(argv);mode=next((n for n in ('prepare','apply','observe','verify') if getattr(args,n)),None)
    if mode is None:print('PLAN ONLY — no connections or changes:\n'+json.dumps(PLAN,indent=2));return 0
    deploy.validation.config_step.load_step('move_r2d2','phase2-check.py').require_r2d2();deploy.tools.check_paths(ENV)
    if not sys.stdin.isatty():raise RuntimeError('Use an interactive R2d2 terminal')
    if Path(sys.prefix)!=ENV:os.execve(str(ENV/'bin/python'),[str(ENV/'bin/python'),str(Path(__file__).resolve()),'--'+mode],deploy.tools.tool_environment())
    if mode=='apply':read_state(REPO/'private/phase5/move-vm-plan.json',True)
    os.umask(0o077);folder=REPO/'reports';inv.private_dir(folder)
    fd,name=tempfile.mkstemp(prefix='phase5-move-vm-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),suffix='.txt',dir=folder);details={}
    print('Private summary: '+name,flush=True);status=1
    with os.fdopen(fd,'w') as output:
        def log(line):print(line,flush=True);output.write(line+'\n');output.flush()
        log.details=details;log('Phase 5 E — '+mode.upper()+' existing VM migration')
        try:
            if mode=='observe':observe(log)
            else:inspection.execute(log,continuation(mode,log))
            status=0
        except (Exception,KeyboardInterrupt) as error:
            log('RESULT: VM_MIGRATION_NEEDS_REVIEW');log(str(error) if isinstance(error,RuntimeError) else type(error).__name__+'; private details withheld')
            log('Preparation requested no migration. Return this report.' if mode=='prepare' else 'If a migration was requested it may still run. Use --observe and return report; do not retry, force-complete, delete source files or move back automatically.')
    with Path(name).with_suffix('.json').open('x') as output:json.dump(details,output,indent=2)
    print('Read short summary: cat '+shlex.quote(name));return status

if __name__=='__main__':
    try:sys.exit(main())
    except (RuntimeError,OSError) as error:print('Stopped: '+str(error),file=sys.stderr);sys.exit(1)

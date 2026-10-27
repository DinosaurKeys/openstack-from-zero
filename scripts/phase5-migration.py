#!/usr/bin/env python3
"""Phase 5 E: inspect planned live-migration prerequisites. No migration command."""
import sys
sys.dont_write_bytecode=True
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import fcntl
import getpass
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

REPO=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('migration_router',REPO/'scripts/phase5-router.py')
router=importlib.util.module_from_spec(spec);spec.loader.exec_module(router)
db=router.db;vip=router.vip;volume=router.volume;boot=router.boot;prep=router.prep;net=router.net;access=router.access;deploy=router.deploy;inv=router.inv;ENV=router.ENV
PLAN={'scope':'Read-only inspection of the existing VM and all three compute nodes',
      'observe':['VM/network/volume identity and data checksum','CPU compatibility using source guest CPU XML','Physical RAM and root-disk headroom','Nova and Placement capacity','Configured libvirt transport and authenticated peer reads'],
      'migration':'Not implemented by this inspection script. Choose/review destination and exact plan after report.',
      'changes':'No migration, VM creation, attach/detach, format, service stop, host configuration or API resource changes'}


class Nodes:
    def __init__(self,env,passwords):self.env=env;self.passwords=passwords
    def call(self,node,action,payload):
        if node not in inv.NODES or action not in ('observe','compare','connection'):raise RuntimeError('Only read-only inspection actions are permitted')
        helper=base64.b64encode((REPO/'scripts/phase5-migration-node.py').read_bytes()).decode()
        data=base64.b64encode(json.dumps(payload).encode()).decode()
        code="import base64;exec(compile(base64.b64decode("+repr(helper)+"),'migration-inspection','exec'),{'__name__':'__main__'})"
        command=shlex.join(['sudo','-S','-p','','/usr/bin/python3','-c',code,'--node',node,'--action',action,'--payload',data])
        try:
            result=subprocess.run(vip.ssh_args(node)+[command],input=self.passwords[node]+'\n',capture_output=True,text=True,env=self.env,timeout=240)
            value=json.loads(result.stdout)
            if not isinstance(value,dict) or value.get('node')!=node or type(value.get('observed')) is not bool:raise ValueError()
            if result.returncode and value.get('observed'):raise ValueError()
            return value
        except (OSError,subprocess.TimeoutExpired,ValueError):return {'node':node,'observed':False,'error':'Inspection response unavailable; no change requested'}
    def all(self,payload):
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs={n:pool.submit(self.call,n,'observe',payload) for n in inv.NODES}
            return {n:j.result() for n,j in jobs.items()}


def capacity(api,node):
    providers=[p for p in api.list('placement','resource_providers','resource_providers') if p.get('name','').split('.')[0]==node]
    if len(providers)!=1 or not volume.valid_uuid(providers[0].get('uuid')):raise RuntimeError('Expected one Placement resource provider for '+node)
    identity=providers[0]['uuid']
    inventories=api.get('placement','resource_providers/'+identity+'/inventories')['inventories']
    usage=api.get('placement','resource_providers/'+identity+'/usages')['usages']
    return {'resource_provider_uuid':identity,'inventories':inventories,'usages':usage}


def candidate_summary(item,comparison,transport,requirements):
    if not item.get('observed'):return {'inspection_complete':False,'reason':item.get('error','Host inspection incomplete')}
    ram=item.get('memory',{}).get('MemAvailable_bytes',0)
    disk=item.get('instances_storage',{}).get('available_bytes',0)
    # A small lab review margin, not a reservation or scheduler guarantee.
    return {'inspection_complete':bool(comparison.get('observed') and transport.get('observed')),
      'physical_available_ram_gib':round(ram/1024**3,2),'instances_disk_available_gib':round(disk/1024**3,2),
      'ram_covers_guest_plus_1GiB_review_margin':ram>=requirements['ram_mb']*1024**2+1024**3,
      'disk_covers_root_plus_2GiB_review_margin':disk>=requirements['disk_gb']*1024**3+2*1024**3,
      'source_guest_cpu_compatible':comparison.get('source_cpu_supported_by_destination_hypervisor') is True,
      'authenticated_libvirt_read_from_source':transport.get('authenticated_libvirt_read') is True,
      'libvirt_control_connection_transport':transport.get('transport'),
      'limits':'Snapshot only; margins are not reservations. Migration-stream ports, complete disk/CPU/device compatibility and Nova scheduling are not proven by an authenticated libvirt read.'}


def detail(log,name,value):
    if hasattr(log,'details'):log.details[name]=value


def gib(value):
    return str(round(value/1024**3,2))+' GiB' if isinstance(value,(int,float)) else 'not collected'


def host_rows(records,source,log):
    log('Node | Role | Available RAM | Available instance disk | Inspection')
    for name,item in records.items():
        log(name+' | '+('VM source' if name==source else 'candidate')+' | '+gib(item.get('memory',{}).get('MemAvailable_bytes'))+' | '+gib(item.get('instances_storage',{}).get('available_bytes'))+' | '+('collected' if item.get('observed') else 'needs review'))
        errors=item.get('errors') or ([{'error':item['error']}] if item.get('error') else [])
        for error in errors:
            log('  '+name+': '+error.get('stage','inspection')+' — '+error.get('error','not completed'))


def checked(value,key):
    if not value.get('observed'):return 'NOT CHECKED'
    return 'PASS' if value.get(key) is True else 'NOT CONFIRMED'


def inspect(nodes,api,vm,source,log):
    records=nodes.all({'server_id':vm['ids']['server'],'source':source})
    detail(log,'compute_hosts',records);host_rows(records,source,log)
    source_record=records[source]
    domain=source_record.get('domain') if source_record.get('observed') else None
    requirements={'ram_mb':prep.PLAN['ram_mb'],'disk_gb':prep.PLAN['disk_gb'],'vcpus':prep.PLAN['vcpus']}
    services=api.list('compute','os-services','services')
    hypervisors=api.list('compute','os-hypervisors/detail','hypervisors')
    detail(log,'nova_compute',{'services':[{k:s.get(k) for k in ('host','binary','state','status')} for s in services if s.get('binary')=='nova-compute'],
      'hypervisors':[{k:h.get(k) for k in ('hypervisor_hostname','state','status','vcpus','vcpus_used','memory_mb','memory_mb_used','local_gb','local_gb_used','disk_available_least')} for h in hypervisors]})
    observed={};summaries={}
    log('Destination | CPU comparison | Libvirt connection | Nova service | Placement data')
    for dest,item in records.items():
        if dest==source:continue
        comparison=nodes.call(dest,'compare',{'domain':domain}) if domain and item.get('observed') else {'observed':False,'reason':'Source CPU or destination observation unavailable'}
        transport=nodes.call(source,'connection',{'peer':dest,'uri':item['nova_config']['connection_uri']}) if source_record.get('observed') and item.get('observed') else {'observed':False,'reason':'Source or destination inspection incomplete'}
        summary=candidate_summary(item,comparison,transport,requirements)
        matches=[s for s in services if s.get('binary')=='nova-compute' and s.get('host','').split('.')[0]==dest]
        summary['nova_compute_up_enabled']=len(matches)==1 and matches[0].get('state')=='up' and matches[0].get('status')=='enabled'
        try:placement=capacity(api,dest)
        except RuntimeError as error:placement={'inspection_error':str(error)}
        summary['placement_capacity_collected']='inspection_error' not in placement
        if not summary['placement_capacity_collected']:summary['inspection_complete']=False
        observed[dest]={'cpu_comparison':comparison,'source_to_destination_libvirt':transport,'placement':placement};summaries[dest]=summary
        log(dest+' | '+checked(comparison,'source_cpu_supported_by_destination_hypervisor')+' | '+checked(transport,'authenticated_libvirt_read')+' | '+('up/enabled' if summary['nova_compute_up_enabled'] else 'needs review')+' | '+('collected' if summary['placement_capacity_collected'] else 'needs review'))
        for label,value in [('CPU',comparison),('connection',transport)]:
            if value.get('error'):log('  '+dest+' '+label+': '+value['error'])
            elif value.get('comparison') and not value.get('source_cpu_supported_by_destination_hypervisor'):log('  '+dest+' CPU: '+value['comparison'])
            elif value.get('reason') and value.get('observed'):log('  '+dest+' '+label+': '+value['reason'])
        if not summary['placement_capacity_collected']:log('  '+dest+': '+placement['inspection_error'])
    detail(log,'candidate_observations',observed)
    detail(log,'summary',{'current_compute_host':source,'VM_requirements':requirements,'candidates':summaries,'destination':'Not selected; review report first'})
    log('Destination: Not selected. No migration requested.')
    if not domain:log('Blocker: source VM CPU/XML inspection is incomplete; CPU comparison was not attempted.')
    return records,summaries


def execute(log):
    deploy.source_guard();env=deploy.tools.tool_environment()
    inventory,config=deploy.validation.local_checks(lambda line:None,env,deploy.validation.CA_FINGERPRINT)
    hashes=access.input_hashes(config,inventory);inv.ensure_agent(env,log)
    folder=REPO/'private/phase5';inv.private_dir(folder);handles=[];passwords={}
    try:
        for path in [folder/name for name in ('api-vip.lock','mariadb.lock','router.lock','migration.lock')]+[REPO/'private/phase4'/name for name in ('vm.lock','volume.lock')]:
            if path.is_symlink():raise RuntimeError('Unsafe exercise lock')
            handle=path.open('a');handles.append(handle);os.chmod(path,0o600)
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('Another lab exercise is running') from None
        for name in ('api-vip-state.json','mariadb-state.json','router-state.json'):
            path=folder/name
            if path.exists() or path.is_symlink():
                deploy.validation.private_file(path)
                if json.loads(path.read_text()).get('restore_required'):raise RuntimeError('An earlier component still needs restoration')
        original,project,user=prep.authenticate(config);api=volume.ReadOnlyAPI(original.client,original.urls,original.token)
        def read(name):
            path=REPO/'private/phase4'/name;deploy.validation.private_file(path);value=json.loads(path.read_text())
            if value.get('pending') or value.get('project_id')!=project or value.get('user_id')!=user or value.get('apis')!=api.urls:raise RuntimeError('Workload identity/checkpoint needs review: '+name)
            return value
        vm=read('vm.json');vstate=read('volume.json');prepared=read('vm-preparation.json')
        if vm.get('plan')!=boot.PLAN or prepared.get('plan')!=prep.PLAN or vstate.get('stage')!='complete' or vstate.get('server_id')!=vm['ids']['server'] or vstate.get('sha256')!=volume.expected_checksum(vstate):raise RuntimeError('Completed, matching VM and volume exercises required')
        source=volume.vm_identity(api,vm,project,user,vstate['volume_id'])
        obj=api.get('block-storage','volumes/'+vstate['volume_id'])['volume'];storage=volume.volume_check(obj,vstate,project)
        if obj.get('status')!='in-use' or len(obj.get('attachments',[]))!=1:raise RuntimeError('Existing volume must remain attached and in-use')
        volume.check_attachment(volume.attachments(api,vstate),vstate,True)
        port=api.get('network','v2.0/ports/'+vm['ids']['port'])['port'];private=boot.check_port(port,project,vm['networks'],vm['prepared'],vm['ids']['server'])
        floating=api.get('network','v2.0/floatingips/'+vm['ids']['floating_ip'])['floatingip'];address=boot.check_floating(floating,project,vm['networks'],vm['ids']['port'],private)
        if port.get('status')!='ACTIVE' or port.get('device_id')!=vm['ids']['server'] or floating.get('status')!='ACTIVE':raise RuntimeError('VM port and floating IP must be ACTIVE')
        known=REPO/'private/phase4/lab-vm-01-known-hosts';deploy.validation.private_file(known);fields=known.read_text().split()
        if len(fields)!=3 or fields[0]!=address or boot.key_fingerprint(' '.join(fields[1:]))!=vm.get('host_fingerprint'):raise RuntimeError('Pinned guest SSH identity differs')
        deploy.validation.private_file(prep.KEY);public=Path(str(prep.KEY)+'.pub')
        if public.is_symlink() or not public.is_file() or ' '.join(public.read_text().split()[:2])!=prepared['public_key']:raise RuntimeError('Guest key differs from preparation')
        boot.ensure_agent(prepared['public_key'],log);ssh=boot.ssh_command(address,known)
        if not router.guest_probe(ssh,address,net.PLAN['private_gateway'])['ok']:raise RuntimeError('Guest baseline network check failed')
        guest=volume.guest(ssh,vstate,'verify',lambda line:None)
        detail(log,'guest_volume',guest)
        log('Guest network and data checksum: PASS')
        if guest.get('sha256')!=vstate['sha256'] or guest.get('filesystem_uuid')!=vstate['filesystem_uuid']:raise RuntimeError('Guest data checksum/UUID differs')
        for name in inv.NODES:passwords[name]=getpass.getpass('Joe sudo password on '+name+' (hidden): ')
        profile=access.load_profile(config)
        db.full_checks(db.Nodes(env,passwords),profile,vip.ProbeClient(profile['cacert']))
        workload={'server_id':vm['ids']['server'],'compute_host':source,'private_ip':private,'floating_ip':address,'volume_id':vstate['volume_id'],'storage_backend':storage,'data_checksum':guest['sha256']}
        detail(log,'workload',workload)
        log('Existing VM: '+vm['ids']['server']+' on '+source+'; floating IP '+address)
        log('Data volume: '+vstate['volume_id']+' on '+storage+'; remains attached')
        records,summaries=inspect(Nodes(env,passwords),api,vm,source,log)
        if hashes!=access.input_hashes(config,inventory):raise RuntimeError('Deployment inputs changed during inspection')
        if any(not v.get('observed') for v in records.values()) or any(not v.get('inspection_complete') for v in summaries.values()):raise RuntimeError('Some migration observations are incomplete; return report for review')
        log('RESULT: VM_MIGRATION_INSPECTION_COLLECTED')
        log('Collection only, not migration approval. No destination selected and no migration/VM/volume/service/configuration change requested. Return this report.')
    finally:
        passwords.clear()
        for handle in reversed(handles):handle.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inspect',action='store_true');args=parser.parse_args(argv)
    if not args.inspect:
        print('PLAN ONLY — no connections or changes:\n'+json.dumps(PLAN,indent=2));print('Run --inspect on R2d2. This script has no --apply mode.');return 0
    deploy.validation.config_step.load_step('migration_r2d2','phase2-check.py').require_r2d2();deploy.tools.check_paths(ENV)
    if not sys.stdin.isatty():raise RuntimeError('Use an interactive R2d2 terminal')
    if Path(sys.prefix)!=ENV:os.execve(str(ENV/'bin/python'),[str(ENV/'bin/python'),str(Path(__file__).resolve()),'--inspect'],deploy.tools.tool_environment())
    os.umask(0o077);folder=REPO/'reports';inv.private_dir(folder)
    fd,name=tempfile.mkstemp(prefix='phase5-migration-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),suffix='.txt',dir=folder)
    print('Private summary: '+name,flush=True);status=1
    details_path=Path(name).with_suffix('.json')
    details={'scope':'Read-only migration inspection; no migration requested'}
    with os.fdopen(fd,'w') as out:
        def log(line):print(line,flush=True);out.write(line+'\n');out.flush()
        log.details=details
        log('Phase 5 E — READ-ONLY planned VM migration inspection')
        try:execute(log);status=0
        except (Exception,KeyboardInterrupt) as error:
            log('RESULT: VM_MIGRATION_INSPECTION_NEEDS_REVIEW');log(str(error) if isinstance(error,RuntimeError) else type(error).__name__+'; private details withheld')
            log('No migration requested. Return report before changing configuration or attempting a move.')
    with details_path.open('x') as output:json.dump(details,output,indent=2)
    print('Technical details (private; only if requested): '+str(details_path))
    print('Read short summary: cat '+shlex.quote(name));return status

if __name__=='__main__':
    try:sys.exit(main())
    except (RuntimeError,OSError) as error:print('Stopped: '+str(error),file=sys.stderr);sys.exit(1)

#!/usr/bin/env python3
"""Phase 5 D: prepare/test one virtual router HA-link interruption and restore it. Default: plan."""
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
import re
import shlex
import signal
import subprocess
import tempfile
import time
import uuid

REPO=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('router_db',REPO/'scripts/phase5-mariadb.py')
db=importlib.util.module_from_spec(spec);spec.loader.exec_module(db)
vip=db.vip;volume=db.volume;boot=volume.boot;prep=db.prep;net=volume.net;access=db.access;deploy=db.deploy;inv=db.inv;ENV=db.ENV
PLAN={'version':1,'fault':'Lower only the active router replica HA virtual interface inside its existing qrouter namespace',
      'restore':'Bring that same HA virtual interface UP over management SSH, then verify router roles and guest traffic',
      'observation_seconds':90,'scope':'No process kill, L3-container restart, physical NIC change, API/database fault, host stop, VM/volume creation or migration'}


class Nodes:
    def __init__(self,env,passwords,context,expected=None):self.env=env;self.passwords=passwords;self.context=context;self.expected=expected or {}
    def call(self,node,action):
        if node not in inv.NODES or action not in ('observe','down','up'):raise RuntimeError('Unapproved router node operation')
        payload=base64.b64encode(json.dumps({'context':self.context,'expected':self.expected.get(node)}).encode()).decode()
        helper=base64.b64encode((REPO/'scripts/phase5-router-node.py').read_bytes()).decode()
        code="import base64;exec(compile(base64.b64decode("+repr(helper)+"),'router-node','exec'),{'__name__':'__main__'})"
        cmd=shlex.join(['sudo','-S','-p','','/usr/bin/python3','-c',code,'--node',node,'--action',action,'--context',payload])
        proc=subprocess.run(vip.ssh_args(node)+[cmd],input=self.passwords[node]+'\n',capture_output=True,text=True,env=self.env,timeout=150)
        try:value=json.loads(proc.stdout)
        except ValueError:raise RuntimeError(node+': no structured router response; return report') from None
        if proc.returncode or value.get('passed') is not True:raise RuntimeError(node+': '+value.get('error','router action incomplete'))
        return value
    def all(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures={n:pool.submit(self.call,n,'observe') for n in inv.NODES}
            return {n:f.result() for n,f in futures.items()}


def identity(item):
    return {key:item[key] for key in ('node','namespace','namespace_identity','container_id','interface','ifindex','mac','ha_port_id','keepalived')}


def restore_command(plan):
    saved=plan['nodes'][plan['target']]
    command=shlex.join(['sudo','ip','netns','exec',saved['namespace'],'ip','link','set','dev',saved['interface'],'up'])
    return shlex.join(['ssh','-t','-o','IdentitiesOnly=yes','-i',str(inv.KEY),'joe@'+inv.NODES[plan['target']],command])


def roles(api,router_id):
    values=api.get('network','v2.0/routers/'+router_id+'/l3-agents')['agents'];result={}
    for item in values:
        name=item.get('host','').split('.')[0]
        if name not in inv.NODES or name in result or item.get('alive') is not True or item.get('admin_state_up') is not True:raise RuntimeError('Router hosting-agent identity/health differs')
        result[name]=item.get('ha_state')
    if set(result)!=set(inv.NODES):raise RuntimeError('Expected three healthy hosting L3 agents')
    return result


def inspect_roles(records,reported,plan=None,down=None):
    if set(records)!=set(inv.NODES) or set(reported)!=set(inv.NODES):raise RuntimeError('Incomplete router observations')
    port_ids=[];gateway=[];external=[]
    for node,item in records.items():
        if item.get('node')!=node or type(item.get('ha_up')) is not bool:raise RuntimeError('Router node/link observation differs')
        if plan and identity(item)!=plan['nodes'][node]:raise RuntimeError('Router namespace/interface/config identity changed on '+node)
        if item['ha_up']!=(node!=down):raise RuntimeError('Unexpected router HA interface state on '+node)
        port_ids.append(item['ha_port_id'])
        if item['owns_gateway']:gateway.append(node)
        if item['owns_external']:external.append(node)
    if len(set(port_ids))!=3:raise RuntimeError('HA interface MAC/port mapping is not unique across nodes')
    active=[n for n,v in reported.items() if v=='active']
    if len(active)>1 or len(gateway)>1 or len(external)>1:raise RuntimeError('Multiple active/forwarding replicas observed; restore and review')
    if len(active)!=1 or gateway!=active or external!=active:return None
    owner=active[0]
    if down:
        if owner==down:return None
        if any(v!='standby' for n,v in reported.items() if n not in (owner,down)):return None
    elif any(v!='standby' for n,v in reported.items() if n!=owner):return None
    return owner


def context_for(api,vm,project):
    networks=vm['networks'];neutron=net.Neutron(api.client,api.urls['network'],api.token)
    net.preflight(neutron,networks,project)
    router=api.get('network','v2.0/routers/'+networks['router'])['router']
    if router.get('ha') is not True or router.get('distributed') is not False or router.get('status')!='ACTIVE':raise RuntimeError('Expected the tracked active non-distributed HA router')
    external=router['external_gateway_info']['external_fixed_ips']
    if len(external)!=1 or external[0].get('subnet_id')!=networks['external_subnet']:raise RuntimeError('Router external subnet identity differs')
    ports=api.list('network','v2.0/ports?device_id='+networks['router'],'ports')
    ha=[p for p in ports if p.get('device_id')==networks['router'] and p.get('device_owner')=='network:router_ha_interface']
    if len(ha)!=3:raise RuntimeError('Expected three actual router HA ports')
    return {'router_id':networks['router'],'gateway':net.PLAN['private_gateway'],'external_ip':external[0]['ip_address'],
        'ha_ports':sorted([{'id':p['id'],'mac':p['mac_address'].lower(),'ips':sorted(f['ip_address'] for f in p['fixed_ips'])} for p in ha],key=lambda p:p['id'])}


def guest_probe(ssh,address,gateway):
    started=time.monotonic();ping=False;inside={}
    try:
        p=subprocess.run(['ping','-n','-c','1','-W','1',address],capture_output=True,text=True,timeout=3);ping=p.returncode==0
        code="""import json,subprocess,urllib.request
result={'gateway_ping':False,'outbound_HTTPS':False}
try:
 result['gateway_ping']=subprocess.run(['ping','-n','-c','1','-W','1',GATEWAY],capture_output=True,timeout=3).returncode==0
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 with opener.open('https://cloud-images.ubuntu.com/',timeout=3) as response:
  response.read(512);result['outbound_HTTPS']=response.status==200
except Exception:pass
print(json.dumps(result))
""".replace('GATEWAY',repr(gateway))
        result=subprocess.run(ssh+['timeout 10 /usr/bin/python3 -'],input=code,capture_output=True,text=True,timeout=15)
        if result.returncode==0:inside=json.loads(result.stdout)
    except (OSError,subprocess.TimeoutExpired,ValueError):pass
    gateway_ok=inside.get('gateway_ping') is True;https_ok=inside.get('outbound_HTTPS') is True
    return {'ok':ping and gateway_ok and https_ok,'floating_ping':ping,'guest_gateway_ping':gateway_ok,'guest_outbound_HTTPS':https_ok,'duration_seconds':round(time.monotonic()-started,3)}


def wait_router(nodes,api,plan,down):
    deadline=time.monotonic()+PLAN['observation_seconds']
    while True:
        records=nodes.all();reported=roles(api,plan['context']['router_id']);owner=inspect_roles(records,reported,plan,down)
        if owner:return records,reported,owner
        if time.monotonic()>=deadline:raise RuntimeError('Router ownership/forwarding addresses did not settle; restore link and review')
        time.sleep(2)


def validate_plan(plan,binding,now):
    if not isinstance(plan,dict) or plan.get('plan')!=PLAN or any(plan.get(k)!=v for k,v in binding.items()) or plan.get('target') not in inv.NODES:
        raise RuntimeError('Prepared router plan changed; review a fresh preparation')
    if not volume.valid_uuid(plan.get('id')) or not 0<=now-plan.get('prepared_unix',0)<=1800:
        raise RuntimeError('Prepared router plan invalid or older than 30 minutes')
    if set(plan.get('nodes',{}))!=set(inv.NODES):raise RuntimeError('Prepared node identities incomplete')
    for node,item in plan['nodes'].items():
        if item.get('node')!=node or item.get('namespace')!='qrouter-'+binding['context']['router_id'] or not re.fullmatch(r'ha-[a-f0-9]{8}-[a-f0-9]{2}',item.get('interface','')):
            raise RuntimeError('Prepared namespace/interface identity differs')
        if not re.fullmatch(r'[a-f0-9]{64}',item.get('container_id','')):raise RuntimeError('Prepared L3 container identity invalid')


def exercise(nodes,api,plan,check,guest_volume_check,service_check,save,log,sampler_factory=vip.Sampler):
    before=nodes.all();current=inspect_roles(before,roles(api,plan['context']['router_id']),plan)
    if current!=plan['target']:raise RuntimeError('Active router changed since preparation; prepare again')
    services_before=service_check()
    if not check()['ok']:raise RuntimeError('Baseline guest network probe did not pass')
    guest_volume_check()
    state={'plan_id':plan['id'],'target':plan['target'],'context':plan['context'],'nodes':plan['nodes'],'restore_required':True,'stage':'HA link down may be issued'}
    sampler=sampler_factory(check);failure=None;restored=False;takeover=None
    save(state);started=time.monotonic()
    try:
        sampler.start();log('Lowering only '+plan['nodes'][current]['interface']+' inside '+plan['nodes'][current]['namespace']+' on '+current)
        nodes.call(current,'down');state['stage']='router HA interface down confirmed';save(state)
        during,reported,new=wait_router(nodes,api,plan,current)
        if not check()['ok']:raise RuntimeError('Guest network did not recover while the original HA interface was down')
        takeover={'active_router':new,'roles':reported,'observed_after_down_request_seconds':round(time.monotonic()-started,3)}
        log('Router takeover evidence:\n'+json.dumps(takeover,indent=2))
    except (Exception,KeyboardInterrupt) as error:failure=str(error) if isinstance(error,RuntimeError) else type(error).__name__+'; router exercise incomplete'
    finally:
        log('Restoring the original router HA interface UP over management SSH.')
        try:
            nodes.call(current,'up');wait_router(nodes,api,plan,None)
            restored=True;state['restore_required']=False;state['stage']='HA link and three replica roles restored';save(state)
        except (Exception,KeyboardInterrupt):failure=(failure+'; ' if failure else '')+'Router HA link/roles restoration not confirmed; use the direct link-UP command.'
        try:sampler.stop()
        except RuntimeError as error:failure=(failure+'; ' if failure else '')+str(error)
        samples=[dict({k:v for k,v in s.items() if k not in ('started','finished')},start_seconds=round(s['started']-started,3),finish_seconds=round(s['finished']-started,3)) for s in sampler.records]
        log('Sampled guest network probes:\n'+json.dumps(samples,indent=2))
    if not restored:raise RuntimeError(failure)
    services_after=service_check()
    if services_before[1:]!=services_after[1:]:raise RuntimeError('Database UUID or API VIP owner changed during router-only test')
    for name in inv.NODES:
        left=services_before[0][name];right=services_after[0][name]
        if left['mariadb']['id']!=right['mariadb']['id'] or left['baseline']['facts']['protected_config_hashes']!=right['baseline']['facts']['protected_config_hashes']:
            raise RuntimeError('Database identity or protected host configuration changed on '+name)
    if not check()['ok']:raise RuntimeError('Final guest network check did not pass')
    guest_volume_check();final=inspect_roles(nodes.all(),roles(api,plan['context']['router_id']),plan)
    if not final:raise RuntimeError('Final router roles incomplete')
    state['stage']='restored and final checks passed';save(state)
    if failure:raise RuntimeError(failure+'; original HA link restored')
    if not takeover:raise RuntimeError('No router takeover evidence')
    summary={'original_active':current,'takeover_active':takeover['active_router'],'final_active':final,'router_id':plan['context']['router_id'],
        'private_gateway':plan['context']['gateway'],'external_router_ip':plan['context']['external_ip'],
        'restoration':'All three HA links UP, one active/two standby, expected forwarding addresses, guest network/data and host/API/database checks passed',
        'probe_count':len(samples),'failed_probe_count':sum(not s['ok'] for s in samples),
        'scope':'Virtual HA-link interruption only. Samples do not prove zero downtime, existing TCP-session continuity, process-crash or whole-node recovery.'}
    log('Router test summary:\n'+json.dumps(summary,indent=2));log('RESULT: ROUTER_HA_FAILOVER_VERIFIED')
    return summary


def execute(mode,log):
    deploy.source_guard();env=deploy.tools.tool_environment()
    inventory,config=deploy.validation.local_checks(lambda line:None,env,deploy.validation.CA_FINGERPRINT)
    hashes=access.input_hashes(config,inventory);inv.ensure_agent(env,log)
    folder=REPO/'private/phase5';inv.private_dir(folder);handles=[]
    try:
        for name in ('api-vip.lock','mariadb.lock','router.lock'):
            path=folder/name
            if path.is_symlink():raise RuntimeError('Unsafe test lock')
            handle=path.open('a');handles.append(handle);os.chmod(path,0o600)
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('Another HA exercise is running') from None
        if mode!='restore':
            for name in ('vm.lock','volume.lock'):
                lock=REPO/'private/phase4'/name
                if lock.is_symlink():raise RuntimeError('Unsafe workload lock')
                handle=lock.open('a');handles.append(handle);os.chmod(lock,0o600)
                try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:raise RuntimeError('Another VM/volume operation is running') from None
        for name in ('api-vip-state.json','mariadb-state.json'):
            path=folder/name
            if path.exists() or path.is_symlink():
                deploy.validation.private_file(path)
                if json.loads(path.read_text()).get('restore_required') and mode!='restore':raise RuntimeError('Earlier service restoration is pending')
        path=folder/'router-plan.json';receipt=folder/'router-state.json'
        for p in (path,receipt):
            if p.is_symlink():raise RuntimeError('Unsafe router state')
            if p.exists():deploy.validation.private_file(p)
        old=json.loads(receipt.read_text()) if receipt.exists() else {};plan=json.loads(path.read_text()) if path.exists() else None
        if old.get('restore_required') and mode!='restore':raise RuntimeError('Restore the recorded HA link before preparing another fault')
        vip.require_preparation(mode,plan)
        passwords={}
        try:
            for node in ([old.get('target')] if mode=='restore' else inv.NODES):
                if node not in inv.NODES:raise RuntimeError('No recorded restoration target')
                passwords[node]=getpass.getpass('Joe sudo password on '+node+' (hidden): ')
            save=lambda v:net.write_state(receipt,v)
            if mode=='restore':
                if not old.get('restore_required'):raise RuntimeError('No recorded router link restoration pending')
                nodes=Nodes(env,passwords,old['context'],old['nodes']);nodes.call(old['target'],'up')
                old['restore_required']=False;old['stage']='link UP confirmed; full router/guest checks required';save(old)
                log('RESULT: ROUTER_HA_LINK_RESTORED');log('Run Chapter A and router role checks; return report before another fault.');return
            original,project,user=prep.authenticate(config);api=volume.ReadOnlyAPI(original.client,original.urls,original.token)
            def read(name):
                p=REPO/'private/phase4'/name;deploy.validation.private_file(p);value=json.loads(p.read_text())
                if value.get('pending') or value.get('project_id')!=project or value.get('user_id')!=user or value.get('apis')!=api.urls:raise RuntimeError('Tracked workload identity differs: '+name)
                return value
            vm=read('vm.json');vstate=read('volume.json');prepared=read('vm-preparation.json')
            if vstate.get('stage')!='complete' or vstate.get('server_id')!=vm.get('ids',{}).get('server') or vstate.get('sha256')!=volume.expected_checksum(vstate):raise RuntimeError('Completed data-volume checkpoint required')
            if vm.get('plan')!=boot.PLAN or not volume.valid_uuid(vm.get('ids',{}).get('server')):raise RuntimeError('Tracked VM plan differs')
            compute=volume.vm_identity(api,vm,project,user,vstate['volume_id'])
            data=api.get('block-storage','volumes/'+vstate['volume_id'])['volume'];volume.volume_check(data,vstate,project)
            if data.get('status')!='in-use' or len(data.get('attachments',[]))!=1:raise RuntimeError('Tracked data volume must remain attached and in-use')
            volume.check_attachment(volume.attachments(api,vstate),vstate,True)
            context=context_for(api,vm,project)
            port=api.get('network','v2.0/ports/'+vm['ids']['port'])['port'];private=boot.check_port(port,project,vm['networks'],vm['prepared'],vm['ids']['server'])
            floating=api.get('network','v2.0/floatingips/'+vm['ids']['floating_ip'])['floatingip'];address=boot.check_floating(floating,project,vm['networks'],vm['ids']['port'],private)
            if port.get('status')!='ACTIVE' or port.get('device_id')!=vm['ids']['server'] or floating.get('status')!='ACTIVE':raise RuntimeError('Tracked VM port and floating IP must be ACTIVE and associated')
            known=REPO/'private/phase4/lab-vm-01-known-hosts';deploy.validation.private_file(known);fields=known.read_text().split()
            if len(fields)!=3 or fields[0]!=address or boot.key_fingerprint(' '.join(fields[1:]))!=vm.get('host_fingerprint'):raise RuntimeError('Pinned guest SSH identity differs')
            deploy.validation.private_file(prep.KEY)
            public=Path(str(prep.KEY)+'.pub')
            if public.is_symlink() or not public.is_file() or ' '.join(public.read_text().split()[:2])!=prepared['public_key']:raise RuntimeError('Local guest public key differs from preparation')
            boot.ensure_agent(prepared['public_key'],log);ssh=boot.ssh_command(address,known)
            # Read-only network/guest data probes use the existing guest, key and mount.
            check=lambda:guest_probe(ssh,address,context['gateway'])
            def data_check():
                result=volume.guest(ssh,vstate,'verify',log)
                if result.get('sha256')!=vstate['sha256'] or result.get('filesystem_uuid')!=vstate['filesystem_uuid']:raise RuntimeError('Guest volume checksum/filesystem UUID differs from completed exercise')
                return result
            profile=access.load_profile(config);client=vip.ProbeClient(profile['cacert']);dbnodes=db.Nodes(env,passwords)
            service_check=lambda:db.full_checks(dbnodes,profile,client)
            nodes=Nodes(env,passwords,context)
            binding={'inputs':hashes,'project_id':project,'user_id':user,'server_id':vm['ids']['server'],'floating_ip':address,'compute_host':compute,'context':context}
            if mode=='prepare':
                service_check();observed=nodes.all();reported=roles(api,context['router_id']);owner=inspect_roles(observed,reported)
                if not owner:raise RuntimeError('Router API roles/forwarding addresses do not agree')
                if not check()['ok']:raise RuntimeError('Baseline guest network probe failed')
                data_check()
                plan=dict(binding,plan=PLAN,id=str(uuid.uuid4()),prepared_unix=time.time(),target=owner,nodes={n:identity(v) for n,v in observed.items()})
                net.write_state(path,plan)
                log('Prepared router HA-link target:\n'+json.dumps({'node':owner,'namespace':observed[owner]['namespace'],'interface':observed[owner]['interface'],'router_id':context['router_id'],'neutron_versions':{n:r['keepalived']['neutron_version'] for n,r in observed.items()}},indent=2))
                log('DIRECT RESTORATION COMMAND:\n'+restore_command(plan));log('No link changed. Return report and approve the HA-link interruption before --apply. Plan expires in 30 minutes.')
                log('RESULT: ROUTER_HA_TEST_PREPARED');return
            validate_plan(plan,binding,time.time())
            if old.get('plan_id')==plan['id']:raise RuntimeError('This test plan was already attempted; review report before another fault')
            nodes.expected=plan['nodes'];log('DIRECT RESTORATION COMMAND:\n'+restore_command(plan))
            exercise(nodes,api,plan,check,data_check,service_check,save,log)
            if hashes!=access.input_hashes(config,inventory):raise RuntimeError('Deployment inputs changed during test')
        finally:passwords.clear()
    finally:
        for handle in reversed(handles):handle.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);group=parser.add_mutually_exclusive_group()
    group.add_argument('--prepare',action='store_true');group.add_argument('--apply',action='store_true');group.add_argument('--restore',action='store_true');args=parser.parse_args(argv)
    mode='prepare' if args.prepare else 'apply' if args.apply else 'restore' if args.restore else None
    if mode is None:
        print('PLAN ONLY — no connections or changes:\n'+json.dumps(PLAN,indent=2));print('--prepare verifies router HA interfaces and existing guest traffic/data. --apply lowers one virtual HA link and restores it. --restore brings only the recorded link UP.');return 0
    def terminate(signum,frame):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,terminate)
    deploy.validation.config_step.load_step('router_r2d2','phase2-check.py').require_r2d2();deploy.tools.check_paths(ENV)
    if not sys.stdin.isatty():raise RuntimeError('Use an interactive R2d2 terminal for node sudo/guest-key prompts')
    if Path(sys.prefix)!=ENV:os.execve(str(ENV/'bin/python'),[str(ENV/'bin/python'),str(Path(__file__).resolve()),'--'+mode],deploy.tools.tool_environment())
    os.umask(0o077);reports=REPO/'reports';inv.private_dir(reports)
    fd,name=tempfile.mkstemp(prefix='phase5-router-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),suffix='.txt',dir=reports)
    print('Private report: '+name,flush=True);status=1
    with os.fdopen(fd,'w') as output:
        def log(line):print(line,flush=True);output.write(line+'\n');output.flush()
        log('Phase 5 D — '+mode.upper()+' router HA-link test')
        try:execute(mode,log);status=0
        except (Exception,KeyboardInterrupt) as error:
            log('RESULT: ROUTER_HA_TEST_NEEDS_REVIEW');log(str(error) if isinstance(error,RuntimeError) else type(error).__name__+'; private details withheld')
            log('Preparation changes no link. If a fault was attempted, restore the recorded HA interface; do not repeat the fault. Return report.')
    print('Read report: cat '+shlex.quote(name));return status

if __name__=='__main__':
    try:sys.exit(main())
    except (RuntimeError,OSError) as error:print('Stopped: '+str(error),file=sys.stderr);sys.exit(1)

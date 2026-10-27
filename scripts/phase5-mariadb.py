#!/usr/bin/env python3
"""Phase 5 C: prepare one MariaDB-member interruption, test surviving cluster/API reads, restore."""
import sys
sys.dont_write_bytecode=True
import argparse
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
import tempfile
import time
import uuid

REPO=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('db_vip',REPO/'scripts/phase5-api-vip.py')
vip=importlib.util.module_from_spec(spec);spec.loader.exec_module(vip)
volume=vip.volume;access=vip.access;prep=vip.prep;deploy=vip.deploy;inv=vip.inv;ENV=vip.ENV
PLAN={'version':1,'component':'One MariaDB member via kolla-mariadb-container.service',
      'survivor_observation_seconds':90,'rejoin_observation_seconds':180,
      'scope':'No second database stop, bootstrap, reset, data deletion, host shutdown, API/router fault or VM/volume mutation',
      'restore':'Start the same service/container over management SSH; wait for all three members to synchronize'}


class Nodes(vip.Nodes):
    helper_name='phase5-mariadb-node.py'


def restore_command(plan):
    return shlex.join(['ssh','-t','-o','IdentitiesOnly=yes','-i',str(inv.KEY),'joe@'+inv.NODES[plan['target']],
        'sudo systemctl start kolla-mariadb-container.service'])


def cluster(records,size,stopped=None,expected_ids=None,expected_uuid=None):
    if set(records)!=set(inv.NODES):raise RuntimeError('Incomplete database observations')
    ready=True;uuids=set();owners=[]
    for node,item in records.items():
        if item.get('node')!=node or type(item.get('vip_owner')) is not bool:raise RuntimeError('Node/VIP observation identity differs')
        if item['vip_owner']:owners.append(node)
        db=item.get('mariadb',{})
        if expected_ids and db.get('id')!=expected_ids[node]:raise RuntimeError('Database container identity changed on '+node)
        if node==stopped:
            if db.get('running') is not False or item.get('unit',{}).get('active')!='inactive':raise RuntimeError('Selected database stop not confirmed')
            continue
        if db.get('running') is not True:raise RuntimeError('Unexpected unavailable database member: '+node)
        if db.get('health')!='healthy' or item.get('unit',{}).get('active')!='active':ready=False
        values=item.get('galera') or {}
        if stopped and values.get('wsrep_cluster_status') not in (None,'Primary'):raise RuntimeError('A surviving member is not Primary; restore the stopped member')
        wanted={'wsrep_cluster_status':'Primary','wsrep_cluster_size':str(size),'wsrep_local_state_comment':'Synced','wsrep_connected':'ON','wsrep_ready':'ON'}
        if any(values.get(k)!=v for k,v in wanted.items()):ready=False
        value=values.get('wsrep_cluster_state_uuid')
        if not volume.valid_uuid(value) or not int(value.replace('-',''),16):ready=False
        else:uuids.add(value)
        if expected_uuid is not None and volume.valid_uuid(value) and int(value.replace('-',''),16) and value!=expected_uuid:raise RuntimeError('Database cluster UUID changed')
    if len(owners)!=1:raise RuntimeError('Expected exactly one API VIP owner')
    if len(uuids)!=1:ready=False
    return ready,next(iter(uuids)) if len(uuids)==1 else None,owners[0]


def full_checks(nodes,profile,client,expected_ids=None,expected_uuid=None):
    records=nodes.all('full');ready,cluster_uuid,owner=cluster(records,3,expected_ids=expected_ids,expected_uuid=expected_uuid)
    if not ready or any(v.get('baseline',{}).get('ready') is not True for v in records.values()):raise RuntimeError('Three-member full healthy baseline did not pass')
    access.check_apis(profile,client)
    horizon=client.horizon()
    if horizon.get('https_verified') is not True or horizon.get('login_form_present') is not True:raise RuntimeError('Horizon HTTPS/form check failed')
    return records,cluster_uuid,owner


def wait_cluster(nodes,size,plan,stopped,seconds):
    deadline=time.monotonic()+seconds
    while True:
        records=nodes.all();ready,cluster_uuid,owner=cluster(records,size,stopped,plan['container_ids'],plan['cluster_uuid'])
        if ready:return records,owner
        if time.monotonic()>=deadline:raise RuntimeError('Database membership/synchronization observation timed out; no bootstrap or repair')
        time.sleep(2)


def validate_plan(plan,binding,now):
    if not plan or plan.get('plan')!=PLAN or any(plan.get(k)!=v for k,v in binding.items()) or plan.get('target') not in inv.NODES:
        raise RuntimeError('No matching prepared MariaDB test; review preparation before applying')
    if not volume.valid_uuid(plan.get('id')) or not volume.valid_uuid(plan.get('cluster_uuid')) or not 0<=now-plan.get('prepared_unix',0)<=1800:
        raise RuntimeError('Prepared MariaDB test invalid or older than 30 minutes; prepare again')
    ids=plan.get('container_ids',{})
    if set(ids)!=set(inv.NODES) or any(not re.fullmatch(r'[a-f0-9]{64}',v) for v in ids.values()) or plan.get('container_id')!=ids[plan['target']]:raise RuntimeError('Prepared database container identities differ')


def exercise(nodes,plan,profile,client,server_id,save,log,sampler_factory=vip.Sampler):
    before,cluster_uuid,owner=full_checks(nodes,profile,client,plan['container_ids'],plan['cluster_uuid'])
    if owner!=plan['api_owner']:raise RuntimeError('API VIP owner changed since preparation; prepare again')
    state={'plan_id':plan['id'],'target':plan['target'],'container_id':plan['container_id'],'restore_required':True,'stage':'database stop may be issued'}
    sampler=sampler_factory(lambda:vip.probe(profile,client,server_id));failure=None;survivors=None;restored=False
    save(state);started=time.monotonic()
    try:
        sampler.start();log('Stopping only MariaDB on '+plan['target']+'; the other two database members stay running.')
        nodes.call(plan['target'],'stop',plan['container_id']);state['stage']='one database member stopped';save(state)
        survivors,survivor_owner=wait_cluster(nodes,2,plan,plan['target'],PLAN['survivor_observation_seconds'])
        if survivor_owner!=owner:raise RuntimeError('API VIP owner changed during the database-only exercise')
        log('Surviving database members:\n'+json.dumps({n:r['galera'] for n,r in survivors.items() if n!=plan['target']},indent=2))
        # Reauthenticate and query all course APIs with the selected database still stopped.
        access.check_apis(profile,client)
        if not vip.probe(profile,client,server_id)['ok']:raise RuntimeError('Authentication/tracked VM API read failed with two database members')
        log('Authenticated API reads passed with the selected member still stopped.')
    except (Exception,KeyboardInterrupt) as error:
        failure=str(error) if isinstance(error,RuntimeError) else type(error).__name__+'; database exercise incomplete'
    finally:
        log('Restoring the original MariaDB service through its management IP.')
        try:
            nodes.call(plan['target'],'start',plan['container_id'])
            state['stage']='database process started; waiting for synchronization';save(state)
            wait_cluster(nodes,3,plan,None,PLAN['rejoin_observation_seconds'])
            restored=True;state['restore_required']=False;state['stage']='three database members synchronized';save(state)
        except (Exception,KeyboardInterrupt):failure=(failure+'; ' if failure else '')+'Database restoration/synchronization not confirmed. Use the printed start command; do not stop another member.'
        try:sampler.stop()
        except RuntimeError as error:failure=(failure+'; ' if failure else '')+str(error)
        samples=[{'start_seconds':round(s['started']-started,3),'finish_seconds':round(s['finished']-started,3),'ok':s['ok'],'duration_seconds':s['duration_seconds']} for s in sampler.records]
        log('Timed authentication + existing VM API probes:\n'+json.dumps(samples,indent=2))
    if not restored:raise RuntimeError(failure)
    after,final_uuid,final_owner=full_checks(nodes,profile,client,plan['container_ids'],cluster_uuid)
    for node in inv.NODES:
        if before[node]['baseline']['facts']['protected_config_hashes']!=after[node]['baseline']['facts']['protected_config_hashes']:raise RuntimeError('Protected host configuration changed on '+node)
    state['stage']='restored; final checks passed';save(state)
    if failure:raise RuntimeError(failure+'; three-member cluster restored and final checks passed')
    if survivors is None:raise RuntimeError('No two-member cluster evidence')
    summary={'stopped_member':plan['target'],'survivors':[n for n in inv.NODES if n!=plan['target']],
        'during_test':'Two Synced, Primary, connected/ready members, size 2, original cluster UUID; authenticated course API reads passed',
        'restoration':'All three Synced, Primary, connected/ready, size 3 and original UUID; final host/API/Horizon checks passed',
        'api_vip_owner_before':owner,'api_vip_owner_after':final_owner,'probe_count':len(samples),'failed_probe_count':sum(not v['ok'] for v in samples),
        'scope':'Controlled one-member service stop, not power loss or a multi-node partition. API/DB read checks only; no application write/replication or zero-downtime claim.'}
    log('MariaDB test summary:\n'+json.dumps(summary,indent=2));log('RESULT: MARIADB_HA_VERIFIED')
    return summary


def execute(mode,target,log):
    deploy.source_guard();env=deploy.tools.tool_environment()
    inventory,config=deploy.validation.local_checks(lambda line:None,env,deploy.validation.CA_FINGERPRINT)
    hashes=access.input_hashes(config,inventory);inv.ensure_agent(env,log)
    folder=REPO/'private/phase5';inv.private_dir(folder);handles=[]
    try:
        # Share B's lock so the two different fault tests cannot run concurrently.
        for name in ('api-vip.lock','mariadb.lock'):
            path=folder/name
            if path.is_symlink():raise RuntimeError('Unsafe lock path')
            handle=path.open('a');handles.append(handle);os.chmod(path,0o600)
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('Another API/database test is running') from None
        bstate=folder/'api-vip-state.json'
        if bstate.exists() or bstate.is_symlink():
            deploy.validation.private_file(bstate)
            if json.loads(bstate.read_text()).get('restore_required') and mode!='restore':raise RuntimeError('Restore the API VIP test before a database exercise')
        path=folder/'mariadb-plan.json';receipt=folder/'mariadb-state.json'
        for p in (path,receipt):
            if p.is_symlink():raise RuntimeError('Unsafe private state path')
            if p.exists():deploy.validation.private_file(p)
        old=json.loads(receipt.read_text()) if receipt.exists() else {};plan=json.loads(path.read_text()) if path.exists() else None
        if old.get('restore_required') and mode!='restore':raise RuntimeError('Database restoration pending; use --restore, do not repeat a fault')
        if mode=='apply' and plan is None:raise RuntimeError('No successful MariaDB preparation; return preparation report before applying')
        if mode=='restore':target=old.get('target')
        elif mode=='apply':target=plan.get('target')
        if target not in inv.NODES:raise RuntimeError('Explicit valid database target required')
        passwords={}
        try:
            for node in inv.NODES:passwords[node]=getpass.getpass('Joe sudo password on '+node+' (hidden): ')
            nodes=Nodes(env,passwords);save=lambda value:volume.net.write_state(receipt,value)
            if mode=='restore':
                if not old.get('restore_required') or not re.fullmatch(r'[a-f0-9]{64}',old.get('container_id','')):raise RuntimeError('No recorded pending MariaDB restoration')
                nodes.call(target,'start',old['container_id'])
                if not plan or plan.get('id')!=old.get('plan_id'):raise RuntimeError('Original plan needed to confirm cluster identity; service started, return report')
                wait_cluster(nodes,3,plan,None,PLAN['rejoin_observation_seconds'])
                old['restore_required']=False;old['stage']='three-member synchronization restored; rerun full readiness';save(old)
                log('RESULT: MARIADB_CLUSTER_RESTORED')
                log('Same three members synchronized with the original cluster UUID. Return report and rerun Chapter A before another fault.');return
            profile=access.load_profile(config);client=vip.ProbeClient(profile['cacert'])
            original,project,user=prep.authenticate(config)
            vm_path=REPO/'private/phase4/vm.json';deploy.validation.private_file(vm_path);vm=json.loads(vm_path.read_text())
            if vm.get('project_id')!=project or vm.get('user_id')!=user or vm.get('apis')!=original.urls or vm.get('pending') or not volume.valid_uuid(vm.get('ids',{}).get('server')):raise RuntimeError('Tracked VM identity requires review')
            binding={'inputs':hashes,'project_id':project,'user_id':user,'server_id':vm['ids']['server']}
            if mode=='prepare':
                records,cluster_uuid,owner=full_checks(nodes,profile,client)
                plan=dict(binding,plan=PLAN,id=str(uuid.uuid4()),prepared_unix=time.time(),target=target,cluster_uuid=cluster_uuid,api_owner=owner,
                    container_id=records[target]['mariadb']['id'],container_ids={n:r['mariadb']['id'] for n,r in records.items()})
                volume.net.write_state(path,plan)
                log('Prepared MariaDB member: '+target+' ('+inv.NODES[target]+'); all three members healthy and synchronized.')
                log('DIRECT RESTORATION COMMAND:\n'+restore_command(plan));log('No database stopped. Review report and approve before --apply. Plan expires in 30 minutes.')
                log('RESULT: MARIADB_TEST_PREPARED');return
            validate_plan(plan,binding,time.time())
            if old.get('plan_id')==plan['id']:raise RuntimeError('This prepared test was already attempted; review it before preparing another')
            log('DIRECT RESTORATION COMMAND:\n'+restore_command(plan))
            exercise(nodes,plan,profile,client,binding['server_id'],save,log)
            if hashes!=access.input_hashes(config,inventory):raise RuntimeError('Deployment inputs changed during the exercise')
        finally:passwords.clear()
    finally:
        for handle in reversed(handles):handle.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);group=parser.add_mutually_exclusive_group()
    group.add_argument('--prepare',action='store_true');group.add_argument('--apply',action='store_true');group.add_argument('--restore',action='store_true')
    parser.add_argument('--node',choices=inv.NODES,help='Explicit member to prepare; required with --prepare only');args=parser.parse_args(argv)
    mode='prepare' if args.prepare else 'apply' if args.apply else 'restore' if args.restore else None
    if args.node and mode!='prepare':parser.error('--node is used only with --prepare')
    if mode=='prepare' and not args.node:parser.error('--prepare requires --node node1/node2/node3')
    if mode is None:
        print('PLAN ONLY — no connections or changes:\n'+json.dumps(PLAN,indent=2));print('--prepare --node NODE checks health and saves an explicit target. --apply stops that one member and restores it. --restore starts only the recorded service.');return 0
    def terminate(signum,frame):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,terminate)
    deploy.validation.config_step.load_step('mariadb_r2d2','phase2-check.py').require_r2d2();deploy.tools.check_paths(ENV)
    if not sys.stdin.isatty():raise RuntimeError('Use an interactive R2d2 terminal for SSH-key/sudo prompts')
    if Path(sys.prefix)!=ENV:
        extra=['--node',args.node] if args.node else []
        os.execve(str(ENV/'bin/python'),[str(ENV/'bin/python'),str(Path(__file__).resolve()),'--'+mode,*extra],deploy.tools.tool_environment())
    os.umask(0o077);reports=REPO/'reports';inv.private_dir(reports)
    fd,name=tempfile.mkstemp(prefix='phase5-mariadb-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),suffix='.txt',dir=reports)
    print('Private report: '+name,flush=True);status=1
    with os.fdopen(fd,'w') as output:
        def log(line):print(line,flush=True);output.write(line+'\n');output.flush()
        log('Phase 5 C — '+mode.upper()+' one MariaDB member test')
        try:execute(mode,args.node,log);status=0
        except (Exception,KeyboardInterrupt) as error:
            log('RESULT: MARIADB_TEST_NEEDS_REVIEW');log(str(error) if isinstance(error,RuntimeError) else type(error).__name__+'; private details withheld')
            log('Preparation stops nothing. Do not repeat a fault; if a stop was attempted, restore the recorded service. Return report.')
    print('Read report: cat '+shlex.quote(name));return status

if __name__=='__main__':
    try:sys.exit(main())
    except (RuntimeError,OSError) as error:print('Stopped: '+str(error),file=sys.stderr);sys.exit(1)

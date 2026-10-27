#!/usr/bin/env python3
"""Phase 5 B: prepare, test API VIP takeover, restore. Default: plan only."""
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
import threading
import time
import urllib.error
import urllib.request
import uuid

REPO=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('vip_volume',REPO/'scripts/phase4-volume.py')
volume=importlib.util.module_from_spec(spec);spec.loader.exec_module(volume)
boot=volume.boot;prep=volume.prep;access=volume.access;deploy=volume.deploy;inv=deploy.inv;ENV=volume.ENV
PLAN={'version':1,'vip':'192.168.0.151','component':'API keepalived container only','takeover_timeout_seconds':90,
      'restore':'Start the same container through its management IP; require final healthy checks',
      'scope':'No host shutdown, HAProxy stop, database/router fault, VM/volume mutation or migration'}


class ProbeClient(access.Client):
    def request(self,url,token=None,body=None,expect=200):
        access.safe_url(url);headers={'Accept':'application/json'}
        if token:headers['X-Auth-Token']=token
        if body is not None:headers['Content-Type']='application/json'
        req=urllib.request.Request(url,headers=headers,data=json.dumps(body).encode() if body is not None else None,method='POST' if body is not None else 'GET')
        try:
            with self.opener.open(req,timeout=3) as response:
                raw=response.read(2_000_001)
                if response.status!=expect or len(raw)>2_000_000:raise RuntimeError('Probe HTTP status/size needs review')
                return json.loads(raw),response.headers.get('X-Subject-Token')
        except urllib.error.HTTPError as error:raise RuntimeError('Probe HTTP '+str(error.code)) from None
        except (urllib.error.URLError,OSError,ValueError):raise RuntimeError('Probe HTTPS/response unavailable') from None


def probe(profile,client,server_id):
    start=time.monotonic()
    try:
        identity=profile['auth']['auth_url'].rstrip('/')
        if not identity.endswith('/v3'):identity+='/v3'
        doc,token=client.request(identity+'/auth/tokens',body=access.auth_body(profile),expect=201)
        data=doc['token'];project=data['project']['id']
        if not token:raise RuntimeError('Probe did not receive a token')
        urls=access.catalog_urls(data['catalog'],profile['region_name'])
        server,_=client.request(access.api_url(urls['compute'],'servers/'+server_id,project),token)
        if server.get('server',{}).get('id')!=server_id or server['server'].get('status')!='ACTIVE':raise RuntimeError('Tracked VM API read differs')
        return {'ok':True,'duration_seconds':round(time.monotonic()-start,3)}
    except (RuntimeError,KeyError,TypeError):
        return {'ok':False,'duration_seconds':round(time.monotonic()-start,3)}


class Sampler:
    def __init__(self,check):self.check=check;self.records=[];self.stop_event=threading.Event();self.thread=None
    def start(self):
        def work():
            while not self.stop_event.is_set():
                started=time.monotonic();result=self.check();self.records.append(dict(result,started=started,finished=time.monotonic()))
                self.stop_event.wait(1)
        self.thread=threading.Thread(target=work,daemon=True);self.thread.start()
    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=15)
        if self.thread and self.thread.is_alive():raise RuntimeError('Probe thread did not finish')


def ssh_args(node):
    return ['ssh','-F','/dev/null','-T','-o','StrictHostKeyChecking=yes','-o','IdentitiesOnly=yes','-o','BatchMode=yes',
        '-o','PasswordAuthentication=no','-o','KbdInteractiveAuthentication=no','-o','ConnectTimeout=5','-o','ConnectionAttempts=1',
        '-o','ForwardAgent=no','-o','ClearAllForwardings=yes','-i',str(inv.KEY),'joe@'+inv.NODES[node]]


def restore_command(plan):
    return shlex.join(['ssh','-t','-o','IdentitiesOnly=yes','-i',str(inv.KEY),'joe@'+inv.NODES[plan['target']],
        shlex.join(['sudo','systemctl','start','kolla-keepalived-container.service'])])


class Nodes:
    helper_name='phase5-api-vip-node.py'
    def __init__(self,env,passwords):self.env=env;self.passwords=passwords
    def call(self,node,action,container_id=None):
        if node not in inv.NODES or action not in ('observe','full','stop','start'):raise RuntimeError('Unapproved node call')
        helper=base64.b64encode((REPO/'scripts'/self.helper_name).read_bytes()).decode()
        baseline=base64.b64encode((REPO/'scripts/phase3-deploy-node.py').read_bytes()).decode()
        code="import base64;exec(compile(base64.b64decode("+repr(helper)+"),'vip-node','exec'),{'__name__':'__main__','BASELINE_SOURCE':"+repr(baseline)+"})"
        args=['sudo','-S','-p','','/usr/bin/python3','-c',code,'--node',node,'--action',action]
        if container_id:args+=['--container-id',container_id]
        proc=subprocess.run(ssh_args(node)+[shlex.join(args)],input=self.passwords[node]+'\n',capture_output=True,text=True,env=self.env,timeout=180 if action in ('full','stop','start') else 60)
        try:result=json.loads(proc.stdout)
        except ValueError:raise RuntimeError(node+': no structured response; SSH/sudo or operation needs review') from None
        if proc.returncode or result.get('passed') is not True:raise RuntimeError(node+': '+result.get('error','node call incomplete'))
        return result
    def all(self,action='observe'):
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures={n:pool.submit(self.call,n,action) for n in inv.NODES}
            return {n:f.result() for n,f in futures.items()}


def owners(records,stopped=None,expected_ids=None,full=False):
    if set(records)!=set(inv.NODES):raise RuntimeError('Incomplete VIP observations')
    found=[]
    for node,item in records.items():
        if item.get('node')!=node or type(item.get('vip_owner')) is not bool:raise RuntimeError('Mismatched node/VIP evidence')
        keep=item.get('keepalived',{});haproxy=item.get('haproxy',{})
        if item.get('unit',{}).get('active')!=('inactive' if node==stopped else 'active'):raise RuntimeError('Unexpected Keepalived systemd service state on '+node)
        if expected_ids and keep.get('id')!=expected_ids[node]:raise RuntimeError('Keepalived container was replaced during the test')
        if keep.get('running')!=(node!=stopped) or keep.get('health') not in ('healthy','not configured'):
            raise RuntimeError('Unexpected Keepalived state on '+node)
        if haproxy.get('running') is not True or haproxy.get('health') not in ('healthy','not configured'):raise RuntimeError('HAProxy is not healthy on '+node)
        if full and item.get('baseline',{}).get('ready') is not True:raise RuntimeError('Full host/service baseline did not pass on '+node)
        if item['vip_owner']:found.append(node)
    if len(found)>1:raise RuntimeError('Multiple VIP owners observed; restore immediately and review')
    return found


def full_checks(nodes,profile,client):
    records=nodes.all('full');current=owners(records,full=True)
    uuids={item['baseline']['facts'].get('galera',{}).get('wsrep_cluster_state_uuid') for item in records.values()}
    if len(uuids)!=1 or not all(volume.valid_uuid(v) and int(v.replace('-',''),16) for v in uuids):raise RuntimeError('MariaDB members do not share one valid cluster UUID')
    if len(current)!=1:raise RuntimeError('Need exactly one current API VIP owner')
    access.check_apis(profile,client)
    horizon=client.horizon()
    if horizon.get('https_verified') is not True or horizon.get('login_form_present') is not True:raise RuntimeError('Horizon HTTPS/form check did not pass')
    return records,current[0]


def validate_plan(plan,binding,now):
    if any(plan.get(k)!=v for k,v in binding.items()) or plan.get('plan')!=PLAN or plan.get('target') not in inv.NODES:
        raise RuntimeError('Prepared plan identity differs; prepare again')
    if not re.fullmatch(r'[a-f0-9]{64}',plan.get('container_id','')) or not volume.valid_uuid(plan.get('id')):
        raise RuntimeError('Prepared target identity is invalid')
    age=now-plan.get('prepared_unix',0)
    if not 0<=age<=1800:raise RuntimeError('Prepared plan is older than 30 minutes; prepare again')
    if set(plan.get('container_ids',{}))!=set(inv.NODES) or plan['container_ids'].get(plan['target'])!=plan['container_id']:
        raise RuntimeError('Prepared container identities are incomplete')


def exercise(nodes,plan,profile,client,server_id,save,log,sampler_factory=Sampler):
    before,current=full_checks(nodes,profile,client)
    owners(before,expected_ids=plan['container_ids'],full=True)
    if current!=plan['target']:raise RuntimeError('VIP owner changed since preparation; prepare a fresh plan')
    state={'plan_id':plan['id'],'target':plan['target'],'container_id':plan['container_id'],'restore_required':True,'stage':'stop request may be issued'}
    sampler=sampler_factory(lambda:probe(profile,client,server_id));samples=[];failure=None;restored=False;takeover=None
    # Receipt is durable before the stop. Restoration uses management SSH, never the API VIP.
    save(state);started=time.monotonic()
    try:
        sampler.start()
        log('Stopping API Keepalived only on '+plan['target']+'. Restoration will be attempted in finally.')
        nodes.call(plan['target'],'stop',plan['container_id']);state['stage']='stop confirmed';save(state)
        deadline=time.monotonic()+PLAN['takeover_timeout_seconds']
        while True:
            snapshot=nodes.all();found=owners(snapshot,stopped=plan['target'],expected_ids=plan['container_ids'])
            fresh=[s for s in sampler.records if s['started']>=started and s.get('ok')]
            if len(found)==1 and found[0]!=plan['target'] and fresh:
                access.check_apis(profile,client)
                takeover={'owner':found[0],'observed_after_stop_request_seconds':round(time.monotonic()-started,3)}
                log('Takeover confirmed: '+json.dumps(takeover));break
            if time.monotonic()>=deadline:raise RuntimeError('Takeover/API checks did not pass within the observation window')
            time.sleep(1)
    except (Exception,KeyboardInterrupt) as error:
        failure=str(error) if isinstance(error,RuntimeError) else type(error).__name__+'; exercise incomplete'
    finally:
        log('Restoring the original API Keepalived through its management address.')
        try:
            nodes.call(plan['target'],'start',plan['container_id'])
            restored=True;state['restore_required']=False;state['stage']='container running; final checks pending';save(state)
        except (Exception,KeyboardInterrupt):
            failure=(failure+'; ' if failure else '')+'Automatic restoration not confirmed. Use the printed direct SSH restoration command.'
        try:sampler.stop()
        except RuntimeError as error:failure=(failure+'; ' if failure else '')+str(error)
        samples=[{'start_seconds':round(s['started']-started,3),'finish_seconds':round(s['finished']-started,3),'ok':s['ok'],'duration_seconds':s['duration_seconds']} for s in sampler.records]
        log('Timed HTTPS authentication + tracked VM API probes:\n'+json.dumps(samples,indent=2))
    if not restored:raise RuntimeError(failure)
    after,last=full_checks(nodes,profile,client);owners(after,expected_ids=plan['container_ids'],full=True)
    for node in inv.NODES:
        if before[node]['baseline']['facts']['protected_config_hashes']!=after[node]['baseline']['facts']['protected_config_hashes']:
            raise RuntimeError('Protected host configuration changed on '+node)
    state['stage']='restored and final checks passed';save(state)
    if failure:raise RuntimeError(failure+'; original container restored and final health checks passed')
    if not takeover:raise RuntimeError('No takeover evidence')
    failed=sum(not s['ok'] for s in samples)
    result={'vip':PLAN['vip'],'original_owner':current,'takeover_owner':takeover['owner'],'final_owner':last,
        'takeover':takeover,'restoration':'Original container running; final host/Galera/API/Horizon checks passed',
        'probe_count':len(samples),'failed_probe_count':failed,
        'measurement_limit':'Sampled authentication + Nova reads, not continuous packet capture. No failed sample does not prove zero downtime. Elapsed takeover time includes SSH and observation delays.',
        'scope':PLAN['scope']}
    log('API VIP test summary:\n'+json.dumps(result,indent=2));log('RESULT: API_VIP_FAILOVER_VERIFIED')
    return result


def require_preparation(mode,plan):
    if mode=='apply' and plan is None:
        raise RuntimeError('No successful preparation plan exists. A failed --prepare cannot be applied; return its report for review')


def execute(mode,log):
    deploy.source_guard();env=deploy.tools.tool_environment()
    inventory,config=deploy.validation.local_checks(lambda line:None,env,deploy.validation.CA_FINGERPRINT)
    hashes=access.input_hashes(config,inventory);inv.ensure_agent(env,log)
    folder=REPO/'private/phase5';inv.private_dir(folder);lock=folder/'api-vip.lock'
    if lock.is_symlink():raise RuntimeError('Unsafe lock path')
    with lock.open('a') as handle:
        os.chmod(lock,0o600)
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Another API VIP command is running') from None
        path=folder/'api-vip-plan.json';receipt=folder/'api-vip-state.json'
        for p in (path,receipt):
            if p.is_symlink():raise RuntimeError('Unsafe private state path')
            if p.exists():deploy.validation.private_file(p)
        old=json.loads(receipt.read_text()) if receipt.exists() else {}
        if old.get('restore_required') and mode!='restore':raise RuntimeError('Earlier test requires restoration; use --restore before preparing another test')
        plan=json.loads(path.read_text()) if path.exists() else None
        require_preparation(mode,plan)
        target=old.get('target') if mode=='restore' else plan.get('target') if plan else None
        passwords={}
        try:
            selected=[target] if mode=='restore' else list(inv.NODES)
            if any(n not in inv.NODES for n in selected):raise RuntimeError('No valid restoration target')
            for node in selected:passwords[node]=getpass.getpass('Joe sudo password on '+node+' (hidden): ')
            nodes=Nodes(env,passwords);save=lambda value:volume.net.write_state(receipt,value)
            if mode=='restore':
                if not old.get('restore_required') or not re.fullmatch(r'[a-f0-9]{64}',old.get('container_id','')):raise RuntimeError('No recorded pending container restoration')
                nodes.call(target,'start',old['container_id']);old['restore_required']=False;old['stage']='manual mode restored container; rerun readiness checks';save(old)
                log('RESULT: API_KEEPALIVED_RESTORED');return
            profile=access.load_profile(config);client=ProbeClient(profile['cacert'])
            original,project,user=prep.authenticate(config)
            vm_path=REPO/'private/phase4/vm.json';deploy.validation.private_file(vm_path);vm=json.loads(vm_path.read_text())
            vpath=REPO/'private/phase4/volume.json';deploy.validation.private_file(vpath);vstate=json.loads(vpath.read_text())
            if vm.get('project_id')!=project or vm.get('user_id')!=user or vm.get('apis')!=original.urls or vm.get('pending') or not volume.valid_uuid(vm.get('ids',{}).get('server')):
                raise RuntimeError('Tracked VM identity requires review')
            if vstate.get('stage')!='complete' or vstate.get('pending') or vstate.get('server_id')!=vm['ids']['server']:raise RuntimeError('Completed volume baseline required')
            binding={'inputs':hashes,'project_id':project,'user_id':user,'server_id':vm['ids']['server']}
            if mode=='prepare':
                records,owner=full_checks(nodes,profile,client)
                if not probe(profile,client,binding['server_id'])['ok']:raise RuntimeError('Baseline timed API probe failed')
                plan=dict(binding,plan=PLAN,id=str(uuid.uuid4()),prepared_unix=time.time(),target=owner,
                    container_id=records[owner]['keepalived']['id'],container_ids={n:v['keepalived']['id'] for n,v in records.items()})
                volume.net.write_state(path,plan)
                log('Prepared target: '+owner+' ('+inv.NODES[owner]+'), API VIP '+PLAN['vip'])
                log('DIRECT RESTORATION COMMAND — keep in a second terminal:\n'+restore_command(plan))
                log('No service stopped. Review this report and obtain approval before --apply. Plan expires in 30 minutes.')
                log('RESULT: API_VIP_TEST_PREPARED');return
            if plan is None:raise RuntimeError('Run --prepare first')
            validate_plan(plan,binding,time.time())
            if old.get('plan_id')==plan['id']:raise RuntimeError('This prepared test was already attempted; prepare a fresh plan after reviewing its report')
            log('DIRECT RESTORATION COMMAND:\n'+restore_command(plan))
            exercise(nodes,plan,profile,client,binding['server_id'],save,log)
            if hashes!=access.input_hashes(config,inventory):raise RuntimeError('Deployment inputs changed during the test')
        finally:passwords.clear()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);group=parser.add_mutually_exclusive_group()
    group.add_argument('--prepare',action='store_true');group.add_argument('--apply',action='store_true');group.add_argument('--restore',action='store_true');args=parser.parse_args(argv)
    mode='prepare' if args.prepare else 'apply' if args.apply else 'restore' if args.restore else None
    if mode is None:
        print('PLAN ONLY — no connections or files changed:\n'+json.dumps(PLAN,indent=2));print('--prepare reads current health and saves target/restoration plan. --apply deliberately stops one API Keepalived and restores it. --restore starts the recorded container only.');return 0
    def terminate(signum,frame):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,terminate)
    deploy.validation.config_step.load_step('vip_r2d2','phase2-check.py').require_r2d2();deploy.tools.check_paths(ENV)
    if not sys.stdin.isatty():raise RuntimeError('Use an interactive R2d2 terminal for SSH-key/sudo prompts')
    if Path(sys.prefix)!=ENV:os.execve(str(ENV/'bin/python'),[str(ENV/'bin/python'),str(Path(__file__).resolve()),'--'+mode],deploy.tools.tool_environment())
    os.umask(0o077);reports=REPO/'reports';inv.private_dir(reports)
    fd,name=tempfile.mkstemp(prefix='phase5-api-vip-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),suffix='.txt',dir=reports)
    print('Private report: '+name,flush=True);status=1
    with os.fdopen(fd,'w') as output:
        def log(line):print(line,flush=True);output.write(line+'\n');output.flush()
        log('Phase 5 B — '+mode.upper()+' API VIP test')
        try:execute(mode,log);status=0
        except (Exception,KeyboardInterrupt) as error:
            log('RESULT: API_VIP_TEST_NEEDS_REVIEW');log(str(error) if isinstance(error,RuntimeError) else type(error).__name__+'; private details withheld')
            if mode=='prepare':log('Preparation stops no service. Do not run --apply unless preparation reports API_VIP_TEST_PREPARED. Return this report.')
            else:log('Do not repeat the test. If a stop was attempted, use the direct restoration command or --restore. Return this report.')
    print('Read report: cat '+shlex.quote(name));return status

if __name__=='__main__':
    try:sys.exit(main())
    except (RuntimeError,OSError) as error:print('Stopped: '+str(error),file=sys.stderr);sys.exit(1)

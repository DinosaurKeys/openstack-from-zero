#!/usr/bin/env python3
"""Approved API recovery only: inspect or restart up to 14 identified API containers."""
import sys
sys.dont_write_bytecode=True
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import fcntl
import getpass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

REPO=Path(__file__).resolve().parents[1]
def load(name,file):
    spec=importlib.util.spec_from_file_location(name,REPO/'scripts'/file)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
health=load('recovery_health','lab-health.py');node_helper=load('recovery_node','lab-recover-api-node.py')
deploy=health.deploy;inv=health.inv;ENV=health.ENV;ALLOWED=node_helper.ALLOWED
ORDER=('placement_api','nova_api','nova_metadata','neutron_server','cinder_api')
RECEIPT='api-recovery-state.json'


class Nodes:
    def __init__(self,env,passwords):self.env=env;self.passwords=passwords
    def call(self,node,action,name=None,target=None,cluster_uuid=None):
        if node not in ALLOWED or action not in ('inspect','restart') or (action=='restart' and name not in ALLOWED[node]):raise RuntimeError('Unapproved recovery call')
        helper=base64.b64encode((REPO/'scripts/lab-recover-api-node.py').read_bytes()).decode()
        reader=base64.b64encode((REPO/'scripts/lab-health-node.py').read_bytes()).decode()
        code='import base64;exec(compile(base64.b64decode('+repr(helper)+'),"api-recovery-node","exec"),{"__name__":"__main__","HEALTH_SOURCE":'+repr(reader)+'})'
        args=['sudo','-S','-p','','/usr/bin/python3','-c',code,'--node',node,'--action',action]
        if action=='restart':args+=['--service',name,'--container-id',target['container']['id'],'--unit-sha',target['unit']['sha256'],'--cluster-uuid',cluster_uuid]
        try:
            result=subprocess.run(health.vip.ssh_args(node)+[shlex.join(args)],input=self.passwords[node]+'\n',capture_output=True,text=True,env=self.env,timeout=360)
            value=json.loads(result.stdout)
            if not isinstance(value,dict) or value.get('node')!=node or value.get('passed') is not True or result.returncode:
                message=value.get('error','Structured response/exit status needs review') if isinstance(value,dict) else 'Invalid response'
                raise RuntimeError(node+': '+message)
            return value
        except (OSError,ValueError,subprocess.TimeoutExpired):raise RuntimeError(node+': SSH/sudo or recovery outcome unavailable; no retry') from None
    def all(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs={n:pool.submit(self.call,n,'inspect') for n in ALLOWED}
            return {n:j.result() for n,j in jobs.items()}


def validate(records,expected_cluster=None):
    if set(records)!=set(ALLOWED):raise RuntimeError('All three host observations are required')
    uuids=set()
    for node,record in records.items():
        host=record['host']
        if host.get('node')!=node or host.get('observed') is not True:raise RuntimeError(node+': host identity unavailable')
        database=host.get('galera',{}).get('value',{})
        if not host.get('galera',{}).get('observed') or not health.member_ok(database):raise RuntimeError(node+': healthy three-member Galera required')
        cluster=database['wsrep_cluster_state_uuid'];uuids.add(cluster)
        node_helper.dependencies(host,cluster)
        containers=host.get('containers',{})
        if not containers.get('observed') or containers['value'].get('missing'):raise RuntimeError(node+': container observation incomplete')
        unexpected=set(containers['value'].get('unhealthy',[]))-set(ALLOWED[node])
        if unexpected:raise RuntimeError(node+': unhealthy services outside the approved scope: '+', '.join(sorted(unexpected)))
        if set(record.get('targets',{}))!=set(ALLOWED[node]):raise RuntimeError(node+': approved target observations incomplete')
        for name,target in record['targets'].items():
            state=target['container']
            if state.get('health')=='starting':raise RuntimeError(node+'/'+name+': already starting; wait and inspect instead of restarting')
    if len(uuids)!=1 or (expected_cluster is not None and uuids!={expected_cluster}):raise RuntimeError('Galera cluster UUID changed or differs between members')
    locations=[r['host'].get('vip_interfaces') for r in records.values()]
    if locations.count(['enp0s31f6'])!=1 or any(v not in ([],['enp0s31f6']) for v in locations):raise RuntimeError('Expected exactly one API VIP on management')
    return next(iter(uuids))


def planned(records):
    return [(node,name,records[node]['targets'][name]) for name in ORDER for node in ALLOWED
      if name in ALLOWED[node] and not node_helper.healthy(records[node]['targets'][name]['container'])]


def checkpoints():
    hashes={}
    for folder in ('phase4','phase5'):
        root=REPO/'private'/folder
        for path in sorted(root.glob('*.json')):
            if folder=='phase5' and path.name==RECEIPT:continue
            deploy.validation.private_file(path)
            hashes[str(path.relative_to(REPO))]=hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def workflow(nodes,records,apply,save,log,verify_inputs):
    cluster=validate(records);actions=planned(records)
    log('Fresh plan: '+str(len(actions))+' of the 14 approved API containers still need recovery.')
    for node,name,_ in actions:log('  '+node+' | '+name+' | restart once, then require healthy')
    if not apply:
        log('RESULT: API_RECOVERY_PLAN_COLLECTED');log('Read-only plan. No restart or checkpoint change.');return
    state={'scope':ALLOWED,'cluster_uuid':cluster,'attempts':[],'pending':False,'stage':'before approved restarts'}
    for node,name,target in actions:
        verify_inputs()
        # Recheck all nodes before each restart. Unknown/new failures block the next action.
        fresh=nodes.all();validate(fresh,cluster)
        current=fresh[node]['targets'][name]
        if current['container']['id']!=target['container']['id'] or current['unit']['sha256']!=target['unit']['sha256']:
            raise RuntimeError(node+'/'+name+': container/unit changed; no restart')
        if node_helper.healthy(current['container']):log('Skip '+node+'/'+name+': now healthy.');continue
        state['attempts'].append({'node':node,'service':name,'container_id':target['container']['id']})
        state.update(pending=True,stage='restart outcome not yet verified',current={'node':node,'service':name})
        save(state)  # Persist responsibility before sending the single mutation.
        log('Restart '+node+'/'+name+'; waiting for its container health check…')
        result=nodes.call(node,'restart',name,target,cluster)
        if not node_helper.healthy(result.get('container',{})):raise RuntimeError('Restart response did not confirm container health')
        verify_inputs()
        state.update(pending=False,stage='last restart verified');save(state)
        log('  PASS: '+result['action'])
    final=nodes.all();validate(final,cluster)
    if planned(final):raise RuntimeError('Approved API containers still need review; no repeated restart')
    verify_inputs()
    if state['attempts']:
        state.update(pending=False,stage='all approved API containers healthy');save(state)
    log('RESULT: APPROVED_API_CONTAINERS_HEALTHY')
    return final


def execute(mode,log,details):
    deploy.source_guard();env=deploy.tools.tool_environment()
    inventory,config=deploy.validation.local_checks(lambda line:None,env,deploy.validation.CA_FINGERPRINT)
    inv.ensure_agent(env,log);folder=REPO/'private/phase5';inv.private_dir(folder);handles=[];passwords={}
    try:
        paths=[folder/n for n in ('api-recovery.lock','api-vip.lock','mariadb.lock','router.lock','migration.lock')]
        paths += [REPO/'private/phase4'/n for n in ('vm.lock','volume.lock','networks.lock')]
        for path in paths:
            if path.is_symlink():raise RuntimeError('Unsafe exercise lock')
            handle=path.open('a');handles.append(handle);os.chmod(path,0o600)
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('Another recovery/HA/migration process is running') from None
        receipt=folder/RECEIPT
        if mode=='apply' and (receipt.exists() or receipt.is_symlink()):raise RuntimeError('An earlier API recovery receipt exists. Return its report before another apply; no automatic retry.')
        for name in ('api-vip-state.json','mariadb-state.json','router-state.json'):
            path=folder/name
            if path.exists() or path.is_symlink():
                if health.read_private(path).get('restore_required'):raise RuntimeError('An HA exercise still requires restoration')
        before=checkpoints();config_hashes=health.access.input_hashes(config,inventory)
        def unchanged():
            if checkpoints()!=before or health.access.input_hashes(config,inventory)!=config_hashes:raise RuntimeError('Existing workload/migration/configuration inputs changed; stop and review')
        for node in ALLOWED:passwords[node]=getpass.getpass('Joe sudo password on '+node+' (hidden): ')
        nodes=Nodes(env,passwords);log('Checking all three nodes and the approved API units…')
        records=nodes.all();details['before']=records
        def save(value):health.prep.net.write_state(receipt,value)
        final=workflow(nodes,records,mode=='apply',save,log,unchanged)
        if mode!='apply':return 0
        details['after']=final;rows=[];hosts={n:r['host'] for n,r in final.items()}
        details['hosts']=hosts;health.host_rows(hosts,rows,details)
        log('Checking HTTPS, service heartbeats and existing workload API state…')
        health.api_checks(rows,details,env);health.checkpoint_checks(rows,details);unchanged()
        details['summary']=rows
        for line in health.summary_lines(rows,details):log(line)
        required=[r for r in rows if r['check'] not in ('existing VM/volume API state','exercise checkpoints')]
        ready=all(r['status']=='PASS' for r in required)
        log('RESULT: '+('API_AND_SERVICE_CHECKS_PASSED' if ready else 'API_RECOVERY_NEEDS_REVIEW'))
        log('Existing migration/workload checkpoints are preserved. VM recovery, guest data/traffic and storage HA are separate; no full-cluster recovery claim.')
        return 0 if ready else 1
    finally:
        passwords.clear()
        for handle in reversed(handles):handle.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);modes=parser.add_mutually_exclusive_group()
    for name in ('check','plan','apply'):modes.add_argument('--'+name,action='store_true')
    args=parser.parse_args(argv);mode=next((n for n in ('check','plan','apply') if getattr(args,n)),None)
    if mode is None:
        print('14-CONTAINER APPROVED SCOPE ONLY: Placement node1/node3; Nova API+metadata, Neutron server and Cinder API on all three nodes. --check reads health; --plan inspects; --apply restarts only still-unhealthy approved containers, once each. No database/broker/compute/agent/VM changes.');return 0
    if mode=='check':return health.main(['--check'])
    deploy.validation.config_step.load_step('recover_r2d2','phase2-check.py').require_r2d2();deploy.tools.check_paths(ENV)
    if not sys.stdin.isatty():raise RuntimeError('Run in joe\'s interactive R2d2 terminal')
    if Path(sys.prefix)!=ENV:os.execve(str(ENV/'bin/python'),[str(ENV/'bin/python'),str(Path(__file__).resolve()),'--'+mode],deploy.tools.tool_environment())
    os.umask(0o077);reports=REPO/'reports';inv.private_dir(reports);details={}
    fd,name=tempfile.mkstemp(prefix='api-recovery-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),suffix='.txt',dir=reports)
    print('Private summary: '+name,flush=True);status=1
    with os.fdopen(fd,'w') as output:
        def log(line):print(line,flush=True);output.write(line+'\n');output.flush()
        log('API recovery — '+mode.upper()+' — approved 14-container scope')
        try:status=execute(mode,log,details)
        except (Exception,KeyboardInterrupt) as error:
            log('RESULT: API_RECOVERY_NEEDS_REVIEW')
            log(str(error) if isinstance(error,RuntimeError) else type(error).__name__+'; outcome needs review')
            log('Stop here and return report. Earlier restarts may have completed. No retry, rollback, database recovery or checkpoint clearing.')
        with Path(name).with_suffix('.json').open('x') as output_json:json.dump(details,output_json,indent=2)
    print('Return summary: cat '+shlex.quote(name));return status


if __name__=='__main__':
    try:sys.exit(main())
    except (RuntimeError,OSError) as error:
        print('Stopped: '+(str(error) if isinstance(error,RuntimeError) else type(error).__name__+'; no automatic retry'),file=sys.stderr);sys.exit(1)

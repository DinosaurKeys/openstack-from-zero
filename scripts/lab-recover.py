#!/usr/bin/env python3
"""Check, show proposed reboot repairs, ask, repair once, then verify services."""
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
import re
import shlex
import subprocess
import tempfile
import time

REPO=Path(__file__).resolve().parents[1]
def load(name,file):
    spec=importlib.util.spec_from_file_location(name,REPO/'scripts'/file)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
base=load('reboot_recovery_base','lab-recover-api.py')
dashboard=load('reboot_recovery_dashboard','lab-reboot-verify.py')
health=base.health;deploy=health.deploy;inv=health.inv;ENV=health.ENV
helper=base.node_helper;ALLOWED=helper.CONTROL_ALLOWED;ORDER=helper.CONTROL_ORDER
PREFIX='reboot-recovery-'


class Nodes(base.Nodes):
    allowed=ALLOWED
    profile='reboot-control'
    def __init__(self,env,passwords,apply=False):
        super().__init__(env,passwords);self.apply=apply
    def call(self,node,action,name=None,target=None,cluster_uuid=None):
        if action!='inspect' and not self.apply:raise RuntimeError('Read-only mode cannot restart a service')
        return super().call(node,action,name,target,cluster_uuid)


def host_baseline(records):
    if set(records)!=set(ALLOWED):raise RuntimeError('All three node observations are required')
    boots={}
    for node,record in records.items():
        host=record.get('host',{})
        if record.get('node')!=node or host.get('node')!=node or host.get('observed') is not True:
            raise RuntimeError(node+': management identity/observation unavailable')
        info=host.get('host',{})
        boot=info.get('value',{}).get('boot_id','')
        if not info.get('observed') or not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}',boot):
            raise RuntimeError(node+': boot identity unavailable')
        boots[node]=boot
        for key in ('clock','provider_network'):
            check=host.get(key,{})
            ok=check.get('value') is True if key=='clock' else check.get('value',{}).get('healthy') is True
            if not check.get('observed') or not ok:raise RuntimeError(node+': '+key+' needs review before repair')
        broker=host.get('rabbitmq',{})
        if not broker.get('observed') or broker.get('value',{}).get('healthy') is not True:
            raise RuntimeError(node+': RabbitMQ must have three running members, no alarms/partitions')
        containers=host.get('containers',{})
        if not containers.get('observed') or containers.get('value',{}).get('missing'):
            raise RuntimeError(node+': complete expected container observations required')
        states=containers.get('value',{}).get('containers',{})
        for name in ('proxysql','haproxy','mariadb','keepalived'):
            value=states.get(name,{})
            if type(value.get('running')) is not bool:raise RuntimeError(node+'/'+name+': state unavailable')
            if name not in ('mariadb','keepalived') and not helper.healthy(value):raise RuntimeError(node+'/'+name+': infrastructure requires separate review')
            if name=='keepalived' and value.get('running') is not True:raise RuntimeError(node+': API Keepalived is not running')
        if set(record.get('targets',{}))!=set(ALLOWED[node]):raise RuntimeError(node+': control-unit inspection incomplete')
    locations=[r['host'].get('vip_interfaces') for r in records.values()]
    if locations.count(['enp0s31f6'])!=1 or any(v not in ([],['enp0s31f6']) for v in locations):
        raise RuntimeError('Require exactly one API VIP owner on management')
    return boots


def database_state(records):
    values=[r['host'].get('galera',{}) for r in records.values()]
    if all(v.get('observed') and health.member_ok(v.get('value',{})) for v in values):
        uuids={v['value']['wsrep_cluster_state_uuid'] for v in values}
        states=[r['host']['containers']['value']['containers']['mariadb'] for r in records.values()]
        if len(uuids)==1 and all(helper.healthy(v) for v in states):return 'healthy',next(iter(uuids))
    states=[r['host']['containers']['value']['containers']['mariadb'] for r in records.values()]
    if all(v.get('running') is False and v.get('status')=='exited' and type(v.get('exit_code')) is int and v['exit_code']==0 for v in states) and not any(v.get('observed') for v in values):
        return 'stopped',None
    raise RuntimeError('MariaDB is partial, starting or unclear. No automatic recovery or API restart; review its state.')


def validate_controls(records,boots,cluster):
    if host_baseline(records)!=boots:raise RuntimeError('A node rebooted during recovery; stop for review')
    state,current=database_state(records)
    if state!='healthy' or current!=cluster:raise RuntimeError('Require the same Synced/Primary three-member Galera cluster')
    for node,record in records.items():
        helper.dependencies(record['host'],cluster)
        bad=set(record['host']['containers']['value'].get('unhealthy',[]))-set(ALLOWED[node])
        if bad:raise RuntimeError(node+': failed containers outside control recovery: '+', '.join(sorted(bad)))


def starting(value):
    return value.get('running') is True and value.get('health')=='starting'


def state_label(value):
    return 'running/'+str(value.get('health')) if value.get('running') is True else 'stopped'


def planned(records):
    return [(node,name,records[node]['targets'][name]) for name in ORDER for node in ALLOWED
            if not helper.healthy(records[node]['targets'][name]['container'])]


def plan_lines(records):
    mode,cluster=database_state(records)
    if mode=='stopped':return ['MariaDB: all three confirmed stopped with exit 0.',
      'Proposed: standard Kolla mariadb-recovery on all three nodes.',
      'Control-service decisions will be made from fresh checks after database recovery.']
    actions=planned(records)
    lines=['PASS  MariaDB: three Synced/Primary members; leave running.',
      'Scope: control-container restarts only; compute and network-agent heartbeats need separate review.']
    if not actions:
        return lines+['Control-container restart targets: 0. No restart is needed in this scope.',
          'If --check still names failed services/agents, use the manual restart section in docs/reboot-and-recovery.md.',
          'Repeating --apply will not restart compute services or network agents.']
    lines.append('Container                | node1              | node2              | node3')
    for name in ORDER:
        if not any(service==name for _,service,_ in actions):continue
        cells=[]
        for node in ALLOWED:
            value=records[node]['targets'][name]['container']
            cells.append('healthy / skip' if helper.healthy(value) else 'starting / wait' if starting(value)
                         else ('unhealthy' if value.get('running') else 'stopped')+' / restart')
        lines.append(name.ljust(24)+' | '+' | '.join(cell.ljust(18) for cell in cells).rstrip())
    lines += ['Recovery targets: '+str(len(actions))+'; healthy containers are skipped.',
      'Restart once per listed failed target. Starting targets are observed first; other approved repairs can proceed.']
    return lines


def service_followup(details):
    """Name heartbeat/admin failures without proposing unreviewed mutations."""
    statuses={row.get('check'):row.get('status') for row in details.get('cluster_summary',[])}
    labels=('Nova services','Cinder services','hypervisors','Neutron agents','router HA (API state)')
    if not any(statuses.get(label) not in (None,'PASS') for label in labels):return []
    lines=['MANUAL SERVICE / AGENT REVIEW',
      'A healthy container does not establish a working service heartbeat. No extra restart or enable action was sent.']
    if any(statuses.get(label)=='UNKNOWN' for label in labels):
        lines.append('  Unavailable API checks do not establish failed services; review the reported API read error first.')
    disabled=False
    for key,binaries in [('Nova services',('nova-scheduler','nova-conductor','nova-compute')),
                         ('Cinder services',('cinder-scheduler','cinder-volume'))]:
        values=details.get(key)
        if statuses.get(key)=='PASS' or not isinstance(values,list):continue
        for node in ALLOWED:
            for binary in binaries:
                entry=dashboard.reported(values,binary,node)
                if not isinstance(entry,dict) or dashboard.service_cell(entry)=='UP/enabled':continue
                disabled=disabled or entry.get('status')=='disabled'
                lines.append('  '+node+'/'+binary+': '+dashboard.service_cell(entry)+
                  ' -> inspect '+binary.replace('-','_')+(' and its disable reason' if entry.get('status')=='disabled' else ''))
    agents=details.get('Neutron agents')
    if statuses.get('Neutron agents')!='PASS' and isinstance(agents,list):
        for node in ALLOWED:
            for binary in ('neutron-openvswitch-agent','neutron-l3-agent','neutron-dhcp-agent','neutron-metadata-agent'):
                entry=dashboard.reported(agents,binary,node)
                if not isinstance(entry,dict) or (entry.get('alive') is True and entry.get('admin_state_up') is True):continue
                alive='UP' if entry.get('alive') is True else 'DOWN' if entry.get('alive') is False else 'UNKNOWN'
                admin='enabled' if entry.get('admin_state_up') is True else 'disabled' if entry.get('admin_state_up') is False else 'unknown'
                lines.append('  '+node+'/'+binary+': '+alive+'/'+admin+' -> inspect '+binary.replace('-','_'))
    if disabled:
        lines+=['  Read the disable reason on R2d2: /usr/bin/python3 scripts/lab-view.py compute-services --table',
          '  DOWN and disabled are different. Recover the heartbeat first; review scheduling enablement separately.']
    if statuses.get('hypervisors') not in (None,'PASS'):
        lines.append('  Hypervisor state: review the matching nova-compute and libvirt connection; a hypervisor is not a separate restart unit.')
    if statuses.get('router HA (API state)') not in (None,'PASS'):
        lines.append('  Router HA: require one active/two standby AND all three hosting L3 agents alive/enabled.')
    lines+=['NEXT: Follow docs/reboot-and-recovery.md#manual-service-and-agent-restarts.',
      'Do not repeat control recovery to repair these checks. Keep the named service logs if a selected restart does not recover its heartbeat.']
    return lines


def checkpoints():
    result={}
    for folder in ('phase4','phase5'):
        for path in sorted((REPO/'private'/folder).glob('*.json')):
            if folder=='phase5' and path.name.startswith(PREFIX):continue
            deploy.validation.private_file(path)
            result[str(path.relative_to(REPO))]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def prior_guard(earlier,boots,actions):
    for path,state in earlier:
        if state.get('complete') is not True:
            raise RuntimeError('Earlier reboot recovery needs review: '+path.name+'; do not delete or repeat it')
        attempted={(v.get('node'),v.get('service')) for v in state.get('attempts',[])
                   if v.get('kind')=='restart' and v.get('outcome')!='not_sent'}
        if state.get('boots')==boots and actions is None and any(v.get('kind')=='mariadb-recovery' for v in state.get('attempts',[])):
            raise RuntimeError('MariaDB recovery was already attempted during this boot; review earlier report')
        if state.get('boots')==boots and any((node,name) in attempted for node,name,_ in (actions or [])):
            raise RuntimeError('A listed service was already restarted during this boot; review the earlier recovery report')


def recovery_args(config,inventory,options):
    return [str(ENV/'bin/kolla-ansible'),'mariadb-recovery','--configdir',str(config),'-i',str(inventory),
      '--limit','baremetal','--become','-e','@'+str(options),'-e','ansible_python_interpreter=/usr/bin/python3']


def check_recap(raw):
    if 'PLAY RECAP' not in raw:raise RuntimeError('MariaDB recovery recap unavailable; no retry')
    recap=raw.rsplit('PLAY RECAP',1)[1]
    for node in ALLOWED:
        match=re.search(r'\b'+node+r'\s*:\s*(ok=\d+[^\n]*)',recap)
        values={k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',match[1])} if match else {}
        if not values.get('ok') or any(values.get(k,1) for k in ('unreachable','failed','rescued','ignored')):
            raise RuntimeError(node+': MariaDB recovery recap needs review; no retry')


def approved_snapshot(records,boots,cluster,actions,node,name,target):
    validate_controls(records,boots,cluster)
    current=records[node]['targets'][name]
    if current['container']['id']!=target['container']['id'] or current['unit']['sha256']!=target['unit']['sha256']:
        raise RuntimeError(node+'/'+name+': identity changed; no restart')
    approved={(n,s) for n,s,_ in actions}
    if any((n,s) not in approved for n,s,_ in planned(records)):
        raise RuntimeError('A new failure is outside the displayed plan; stop for review')
    return current


def settle_target(nodes,records,boots,cluster,actions,node,name,target,log,unchanged,seconds=90):
    """Wait for this target only; independent container reads need not be simultaneous."""
    deadline=time.monotonic()+seconds
    while True:
        unchanged()
        current=approved_snapshot(records,boots,cluster,actions,node,name,target)
        if not starting(current['container']):return records,current
        if time.monotonic()>=deadline:
            log('DEFER  '+node+'/'+name+': still starting; no restart sent. Continue other approved targets.')
            return records,None
        log('WAIT   '+node+'/'+name+': starting; observing this target only…')
        time.sleep(5);records=nodes.all()


def workflow(nodes,records,apply,confirm,save,log,unchanged,recover_db,prior=lambda *_:None):
    boots=host_baseline(records);mode,cluster=database_state(records)
    state={'boots':boots,'complete':False,'pending':False,'attempts':[],'deferred':[],'stage':'review'}
    prior(boots,planned(records) if mode=='healthy' else None)
    if mode=='stopped':
        for line in plan_lines(records):log(line)
        if not apply:return None
        if not confirm('RECOVER MARIADB'):log('Cancelled. No changes made.');return None
        unchanged();fresh=nodes.all()
        if host_baseline(fresh)!=boots or database_state(fresh)[0]!='stopped':raise RuntimeError('Database state changed after approval; no recovery requested')
        state.update(pending=True,stage='MariaDB recovery outcome pending')
        state['attempts'].append({'kind':'mariadb-recovery'});save(copy.deepcopy(state))
        recover_db()
        records=nodes.all();unchanged()
        if host_baseline(records)!=boots:raise RuntimeError('Node boot identity changed during MariaDB recovery')
        mode,cluster=database_state(records)
        if mode!='healthy':raise RuntimeError('MariaDB recovery did not establish a healthy three-member cluster')
        state.update(pending=False,stage='MariaDB verified');save(copy.deepcopy(state))
        log('PASS  MariaDB: all three synchronized and Primary.')
    validate_controls(records,boots,cluster)
    for line in plan_lines(records):log(line)
    actions=planned(records);prior(boots,actions)
    if not apply:return None
    if actions and not confirm('RESTART LISTED SERVICES'):
        if state['attempts']:state.update(complete=True,stage='Database recovered; control restarts declined');save(copy.deepcopy(state))
        log('Control restarts cancelled.');return None
    for node,name,target in actions:
        unchanged();fresh=nodes.all()
        fresh,current=settle_target(nodes,fresh,boots,cluster,actions,node,name,target,log,unchanged)
        if current is None:
            state['deferred'].append({'node':node,'service':name,'reason':'still starting; no restart sent'})
            continue
        if helper.healthy(current['container']):log('SKIP  '+node+'/'+name+': now healthy');continue
        state['attempts'].append({'kind':'restart','node':node,'service':name,'container_id':target['container']['id'],'outcome':'pending'})
        state.update(pending=True,stage='Restart '+node+'/'+name+' outcome pending');save(copy.deepcopy(state))
        log('RESTART  '+node+'/'+name+'; waiting for healthy…')
        result=nodes.call(node,'restart',name,target,cluster)
        if result.get('deferred') is True and result.get('changed') is False and starting(result.get('container',{})):
            # The root helper observed startup after the last SSH snapshot and sent no restart.
            state['attempts'][-1]['outcome']='not_sent'
            state['deferred'].append({'node':node,'service':name,'reason':'starting at final node check; no restart sent'})
            unchanged();state.update(pending=False,stage=node+'/'+name+' deferred without restart');save(copy.deepcopy(state))
            log('DEFER    '+node+'/'+name+': started before the final node check; no restart sent.')
            continue
        if not helper.healthy(result.get('container',{})):raise RuntimeError('Restart did not confirm container health')
        state['attempts'][-1]['outcome']='not_sent' if result.get('changed') is False else 'verified'
        unchanged();state.update(pending=False,stage=node+'/'+name+' healthy');save(copy.deepcopy(state))
        log('PASS     '+node+'/'+name+' healthy')
    final=nodes.all();validate_controls(final,boots,cluster)
    remaining=planned(final)
    for node,name,target in remaining:log('REVIEW  '+node+'/'+name+': '+state_label(target['container'])+'; no further restart sent.')
    unchanged()
    if state['attempts']:
        state.update(complete=True,pending=False,stage='All sent actions verified; '+str(len(remaining))+' control targets still need review')
        save(copy.deepcopy(state))
    return final


def execute(mode,log,details,report):
    deploy.source_guard();env=deploy.tools.tool_environment()
    inventory,config=deploy.validation.local_checks(lambda _:None,env,deploy.validation.CA_FINGERPRINT)
    inv.ensure_agent(env,log);folder=REPO/'private/phase5';inv.private_dir(folder)
    handles=[];passwords={}
    try:
        locks=[folder/name for name in ('reboot-recovery.lock','shutdown.lock','api-recovery.lock','api-vip.lock','mariadb.lock','router.lock','migration.lock')]
        locks += [REPO/'private/phase4'/name for name in ('vm.lock','volume.lock','networks.lock')]
        for path in locks:
            if path.is_symlink():raise RuntimeError('Unsafe exercise lock')
            handle=path.open('a');handles.append(handle);os.chmod(path,0o600)
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('Another lab operation is running') from None
        for path in sorted(folder.glob('shutdown-*.json')):
            if health.read_private(path).get('pending'):raise RuntimeError('A service shutdown is incomplete; review before recovery')
        for name in ('api-vip-state.json','mariadb-state.json','router-state.json'):
            path=folder/name
            if path.exists() or path.is_symlink():
                if health.read_private(path).get('restore_required'):raise RuntimeError('An HA exercise requires restoration first')
        before=checkpoints();inputs=health.access.input_hashes(config,inventory)
        def unchanged():
            if checkpoints()!=before or health.access.input_hashes(config,inventory)!=inputs:raise RuntimeError('Saved checkpoints or deployment inputs changed')
        for node in ALLOWED:passwords[node]=getpass.getpass('Joe sudo password on '+node+' (hidden): ')
        nodes=Nodes(env,passwords,mode=='apply');log('Checking all three nodes and control services…')
        records=nodes.all();details['before']=records
        receipt=folder/(report.stem+'.json')
        def save(state):health.prep.net.write_state(receipt,state)
        def confirm(phrase):
            log('Approval required: '+phrase)
            return input('Type '+phrase+' to continue: ')==phrase
        def recover_db():
            fd,opt=tempfile.mkstemp(prefix='reboot-credentials-',suffix='.json',dir=REPO/'reports');options=Path(opt)
            try:
                with os.fdopen(fd,'w') as secret:
                    json.dump({'recovery_passwords':passwords,'ansible_become_password':'{{ recovery_passwords[inventory_hostname] }}'},secret)
                logfile=report.with_name(report.stem+'-mariadb.log');log('MariaDB recovery transcript: '+str(logfile))
                with logfile.open('x') as out:
                    proc=subprocess.Popen(recovery_args(config,inventory,options),stdout=out,stderr=subprocess.STDOUT,env=env)
                    latest=''
                    while True:
                        try:rc=proc.wait(timeout=10);break
                        except subprocess.TimeoutExpired:
                            tasks=re.findall(r'^TASK \[([^\]\r\n]+)\]',logfile.read_text(errors='replace'),re.M)
                            task=tasks[-1] if tasks else 'starting Kolla recovery'
                            log('Kolla: '+task+(' (working)' if task==latest else ''));latest=task
                if rc:raise RuntimeError('Kolla MariaDB recovery exited '+str(rc)+'; see private transcript; no retry')
                check_recap(logfile.read_text(errors='replace'));unchanged()
            finally:options.unlink(missing_ok=True)
        # Snapshot earlier receipts before the workflow creates its own progress file.
        earlier=[(path,health.read_private(path)) for path in sorted(folder.glob(PREFIX+'*.json'))]
        def prior(boots,actions):
            prior_guard(earlier,boots,actions)
        final=workflow(nodes,records,mode=='apply',confirm,save,log,unchanged,recover_db,prior)
        unchanged()
        if mode=='plan':log('RESULT: RECOVERY_PLAN_COLLECTED');log('Read-only plan. No service changes.');return 0
        if final is None:log('RESULT: RECOVERY_STOPPED');return 1
        log('Final check: containers, database, APIs, services and agents…')
        rows=[];hosts={n:r['host'] for n,r in final.items()};details['hosts']=hosts;health.host_rows(hosts,rows,details)
        health.api_checks(rows,details,env,include_workload=False);details['cluster_summary']=rows;unchanged()
        details['summary']=[{'check':'Existing checkpoints/config preserved','status':'PASS'}]
        rendered=dashboard.render_compact(details);ready='RESULT: REBOOT_SERVICES_HEALTHY' in rendered
        followup=service_followup(details) if not ready else []
        for line in rendered:
            if line.startswith('NEXT:') and followup:continue
            if line.startswith('Read-only snapshot.'):line='Verification snapshot after the approved actions listed above.'
            if line.startswith('RESULT:') and not ready:line='RESULT: RECOVERY_NEEDS_REVIEW'
            log(line)
        for line in followup:log(line)
        log('Cluster service checks only. VM and migration records remain paused and unchanged.')
        return 0 if ready else 1
    finally:
        passwords.clear()
        for handle in reversed(handles):handle.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group()
    for name in ('check','plan','apply'):modes.add_argument('--'+name,action='store_true')
    args=parser.parse_args(argv);mode=next((n for n in ('check','plan','apply') if getattr(args,n)),'check')
    if mode=='check':return dashboard.main(['--check','--compact'])
    deploy.validation.config_step.load_step('recovery_r2d2','phase2-check.py').require_r2d2();deploy.tools.check_paths(ENV)
    if not sys.stdin.isatty():raise RuntimeError('Use joe\'s interactive R2d2 terminal')
    if Path(sys.prefix)!=ENV:os.execve(str(ENV/'bin/python'),[str(ENV/'bin/python'),str(Path(__file__).resolve()),'--'+mode],deploy.tools.tool_environment())
    os.umask(0o077);reports=REPO/'reports';inv.private_dir(reports);details={}
    fd,name=tempfile.mkstemp(prefix=PREFIX+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),suffix='.txt',dir=reports)
    report=Path(name);print('Private summary: '+name,flush=True);status=1
    with os.fdopen(fd,'w') as out:
        def log(line):print(line,flush=True);out.write(line+'\n');out.flush()
        log('OPENSTACK REBOOT RECOVERY — '+mode.upper())
        try:status=execute(mode,log,details,report)
        except (Exception,KeyboardInterrupt) as error:
            log('RESULT: RECOVERY_NEEDS_REVIEW')
            log(str(error) if isinstance(error,RuntimeError) else type(error).__name__+'; outcome needs review')
            log('Stop and return this summary. No repeated restart or automatic rollback.')
        with report.with_suffix('.json').open('x') as output:json.dump(details,output,indent=2)
    print('Return summary: cat '+shlex.quote(name));return status


if __name__=='__main__':
    try:sys.exit(main())
    except (RuntimeError,OSError) as error:
        print('Stopped: '+(str(error) if isinstance(error,RuntimeError) else type(error).__name__),file=sys.stderr);sys.exit(1)

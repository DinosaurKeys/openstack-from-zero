#!/usr/bin/env python3
"""Prepare all lab nodes for manual poweroff. --check reads; --apply stops services only."""
import sys
sys.dont_write_bytecode=True
import argparse
import base64
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

REPO=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('shutdown_health',REPO/'scripts/lab-health.py')
health=importlib.util.module_from_spec(spec);spec.loader.exec_module(health)
NODES=tuple(health.NODES)
EXTRAS={'cron','fluentd','kolla_toolbox','memcached','tgtd','iscsid','nova_ssh','nova_novncproxy','glance_api'}
RETAIN=('mariadb','rabbitmq','proxysql','haproxy','keepalived')
UUID_PATTERN=r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'

def network_child(name):
    return re.fullmatch(r'(?:neutron_l3_agent_(?:haproxy|keepalived)_qrouter-|neutron_dhcp_agent_(?:dnsmasq|haproxy)_qdhcp-)'+UUID_PATTERN,name) is not None

def allowed(name):
    required={'mariadb','proxysql','rabbitmq','haproxy','keepalived','keystone_api','keystone_fernet','keystone_ssh',
      'placement_api','nova_api','nova_metadata','nova_scheduler','nova_conductor','nova_compute','nova_libvirt',
      'neutron_server','neutron_rpc_server','neutron_periodic_worker','neutron_openvswitch_agent','neutron_l3_agent',
      'neutron_dhcp_agent','neutron_metadata_agent','openvswitch_db','openvswitch_vswitchd','cinder_api',
      'cinder_scheduler','cinder_volume','horizon'}
    return name in required|EXTRAS or network_child(name)

def node_baseline(node,r):
    host=r.get('host',{})
    if host.get('node')!=node or not host.get('observed'):raise RuntimeError(node+': identity/inspection unavailable')
    db=host.get('galera',{})
    issues=[]
    if not db.get('observed') or not health.member_ok(db.get('value',{})):issues.append('require Synced/Primary Galera size 3')
    if not host.get('rabbitmq',{}).get('value',{}).get('healthy'):issues.append('RabbitMQ cluster needs review')
    if not host.get('containers',{}).get('value',{}).get('healthy'):issues.append('core containers need review')
    if r.get('active_domains') is None:issues.append('VM runtime unavailable')
    elif r['active_domains']:issues.append('VM/domain still active. Shut guests down separately first.')
    if r.get('running') is None:issues.append('container list unavailable')
    else:
        unknown=sorted(n for n in r['running'] if not allowed(n))
        if unknown:issues.append('container names outside the shutdown list: '+', '.join(unknown))
    if issues:raise RuntimeError(node+': '+'; '.join(issues))
    return db['value']['wsrep_cluster_state_uuid']

def baseline(records):
    if set(records)!=set(NODES):raise RuntimeError('All three nodes must be observed')
    uuids=set();issues=[]
    for node in NODES:
        try:uuids.add(node_baseline(node,records[node]))
        except RuntimeError as error:issues.append(str(error))
    if issues:raise RuntimeError('\n'.join(issues))
    if len(uuids)!=1:raise RuntimeError('Database cluster UUID differs between nodes')
    return next(iter(uuids))

def collect_checks(remote):
    records={}
    for node in NODES:
        try:records[node]=remote(node,'inspect')
        except RuntimeError as error:records[node]={'error':str(error)}
    return records

def check_lines(records):
    lines=['CHECK ONLY — no service-stop or poweroff commands are sent.',
      'Node  | Galera | RabbitMQ | Core containers | Active VMs']
    for node in NODES:
        r=records.get(node,{});host=r.get('host',{})
        db=host.get('galera',{});broker=host.get('rabbitmq',{});containers=host.get('containers',{})
        def status(observation,ok):
            return ('OK' if ok else 'REVIEW') if observation.get('observed') else 'UNKNOWN'
        lines.append(' | '.join([node,status(db,health.member_ok(db.get('value',{}))),
          status(broker,broker.get('value',{}).get('healthy')),
          status(containers,containers.get('value',{}).get('healthy')),
          str(len(r['active_domains'])) if isinstance(r.get('active_domains'),list) else 'UNKNOWN']))
    try:baseline(records);ready=True
    except RuntimeError as error:lines+=['Needs review:',str(error)];ready=False
    lines+=['RESULT: '+('SHUTDOWN_READINESS_CHECKED' if ready else 'SHUTDOWN_CHECK_NEEDS_REVIEW'),
      'Check finished. No services or hosts were stopped by this check.']
    return ready,lines

def request_guard(check_only,action):
    if check_only and action!='inspect':raise RuntimeError('Check mode permits inspection only')

def positions(values,cluster):
    if set(values)!=set(NODES):raise RuntimeError('All database shutdown records are required')
    for n,r in values.items():
        if r['uuid']!=cluster or r['seqno']<0 or r['exit_code']!=0:raise RuntimeError(n+': database shutdown position needs review')
        if r['safe_to_bootstrap']!=(1 if n=='node3' else 0):raise RuntimeError(n+': unexpected safe-to-bootstrap marker; no poweroff')
    if values['node3']['seqno']<max(r['seqno'] for r in values.values()):raise RuntimeError('Last database member has an older position; no poweroff')

def stop_args(config,inventory,options,service=None,node=None):
    args=[str(health.ENV/'bin/kolla-ansible'),'stop','--configdir',str(config),'-i',str(inventory),
      '--limit',node or 'baremetal','--become','-e','@'+str(options),'-e','ansible_python_interpreter=/usr/bin/python3',
      '--yes-i-really-really-mean-it']
    if service:args+=['--tags',service]
    else:args+=['-e',json.dumps({'skip_stop_containers':list(RETAIN)})]
    return args

def continuation(state,records):
    attempts=state.get('attempts',[])
    if state.get('pending') is not True or state.get('database')!={} or not attempts or attempts[0]!='1/3 Stop cloud clients and agents; retain database, broker and proxies' or not re.fullmatch('1/3 Stop remaining DHCP/router helpers on node[123]',state.get('stage','')):
        raise RuntimeError('This receipt is not an interrupted network-helper stage; manual review required')
    if set(records)!=set(NODES):raise RuntimeError('All three nodes must be observed before finishing')
    for node,r in records.items():
        if r.get('vm_processes')!=0:raise RuntimeError(node+': VM runtime not confirmed empty')
        if not health.member_ok(r.get('database',{})) or r['database']['wsrep_cluster_state_uuid']!=state.get('cluster_uuid'):
            raise RuntimeError(node+': require the original healthy three-member database cluster')
        if not r.get('broker',{}).get('healthy'):raise RuntimeError(node+': require healthy three-member RabbitMQ')
        names=set(r.get('running',[]))
        if not set(RETAIN).issubset(names) or any(name not in RETAIN and not network_child(name) for name in names):
            raise RuntimeError(node+': clients or unexpected containers remain running')
        for name in RETAIN:
            support=r.get('support',{}).get(name,{})
            if support.get('running') is not True or (name!='keepalived' and support.get('health')!='healthy'):
                raise RuntimeError(node+'/'+name+': supporting service needs review')

def workflow(remote,stop,save,log,cluster,previous=None):
    state=previous if previous is not None else {'pending':True,'stage':'before first service stop','cluster_uuid':cluster,'database':{},'attempts':[]}
    def attempt(stage,call):
        state['stage']=stage;state['attempts'].append(stage);save(state)
        log(stage);return call()
    if previous is None:
        attempt('1/3 Stop cloud clients and agents; retain database, broker and proxies',lambda:stop(None,None))
    else:log('Continue verified partial shutdown; initial cloud-service stop is not repeated.')
    for node in NODES:
        attempt('1/3 Stop remaining DHCP/router helpers on '+node,lambda n=node:remote(n,'network-children'))
    for node in NODES:
        if set(remote(node,'running')['running'])!=set(RETAIN):
            raise RuntimeError(node+': unexpected containers after cloud stop; no further stop/poweroff')
    for node in NODES:
        attempt('2/3 Stop proxies/VIP services on '+node,lambda n=node:stop('loadbalancer',n))
        if set(remote(node,'running')['running'])!={'mariadb','rabbitmq'}:
            raise RuntimeError(node+': proxy stop not confirmed; no further service stop')
    for node in NODES:
        attempt('2/3 Stop RabbitMQ on '+node,lambda n=node:stop('rabbitmq',n))
        remote(node,'broker')
    for node in NODES:
        attempt('3/3 Stop MariaDB on '+node,lambda n=node:stop('mariadb',n))
        state['database'][node]=remote(node,'database');save(state)
    positions(state['database'],cluster)
    for node in NODES:
        if remote(node,'running')['running']:raise RuntimeError(node+': containers remain running; no poweroff')
    state['stage']='services stopped; database clean exits and positions verified';save(state)
    log('All cloud containers stopped. Last database member: node3. No host powered off yet.')
    return state

def finish(state,save,log):
    state.update(pending=False,stage='services stopped and database verified; manual OS poweroff remains')
    save(state)
    log('RESULT: READY_FOR_MANUAL_POWEROFF')
    log('Services are stopped. All three Ubuntu hosts remain powered on. You may now shut down Ubuntu manually. Last database/broker member stopped: node3.')

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--check',action='store_true');modes.add_argument('--apply',action='store_true');modes.add_argument('--finish',action='store_true');args=parser.parse_args(argv)
    if not args.check and not args.apply and not args.finish:
        print('Prepare node1/node2/node3 for manual poweroff. --check reads readiness only. --apply starts service stops. --finish explicitly continues a reviewed interrupted network-helper stage after fresh all-node checks. Hosts remain powered on.');return 0
    health.deploy.validation.config_step.load_step('shutdown_r2d2','phase2-check.py').require_r2d2()
    health.deploy.tools.check_paths(health.ENV)
    if not sys.stdin.isatty():raise RuntimeError('Use joe\'s interactive R2d2 terminal')
    if Path(sys.prefix)!=health.ENV:
        mode='--finish' if args.finish else '--apply' if args.apply else '--check'
        os.execve(str(health.ENV/'bin/python'),[str(health.ENV/'bin/python'),str(Path(__file__).resolve()),mode],health.deploy.tools.tool_environment())
    os.umask(0o077);env=health.deploy.tools.tool_environment();health.deploy.source_guard()
    for name,digest in json.loads((REPO/'scripts/lab-shutdown-sources.json').read_text()).items():
        path=health.ENV/name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise RuntimeError('Shutdown source differs from reviewed Kolla release: '+name)
    inventory,config=health.deploy.validation.local_checks(lambda _:None,env)
    health.inv.ensure_agent(env,print)
    folder=REPO/'private/phase5';health.inv.private_dir(folder)
    reports=REPO/'reports';health.inv.private_dir(reports)
    handles=[];passwords={};options=None
    try:
        paths=[folder/n for n in ('shutdown.lock','api-recovery.lock','api-vip.lock','mariadb.lock','router.lock','migration.lock')]
        paths += [REPO/'private/phase4'/n for n in ('vm.lock','volume.lock','networks.lock')]
        for path in paths:
            if path.is_symlink():raise RuntimeError('Unsafe exercise lock')
            handle=path.open('a');handles.append(handle);os.chmod(path,0o600)
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('Another lab operation is running') from None
        pending=[];previous=None
        for path in folder.glob('shutdown-*.json'):
            value=health.read_private(path)
            if value.get('pending'):pending.append((path,value))
        if args.finish:
            if len(pending)!=1:raise RuntimeError('Finish requires exactly one interrupted shutdown receipt')
            receipt,previous=pending[0]
        elif pending:raise RuntimeError('Earlier shutdown is incomplete: '+pending[0][0].name+'. Do not repeat --apply.')
        for name in ('api-vip-state.json','mariadb-state.json','router-state.json'):
            path=folder/name
            if path.exists() and health.read_private(path).get('restore_required'):raise RuntimeError('An HA exercise requires restoration first')
        for node in NODES:passwords[node]=getpass.getpass('Joe sudo password on '+node+' (hidden): ')
        protected=health.access.input_hashes(config,inventory)
        def unchanged():
            if health.access.input_hashes(config,inventory)!=protected:
                raise RuntimeError('Deployment inputs changed; stop for review')
        helper=base64.b64encode((REPO/'scripts/lab-shutdown-node.py').read_bytes()).decode()
        reader=base64.b64encode((REPO/'scripts/lab-health-node.py').read_bytes()).decode()
        def remote(node,action):
            request_guard(args.check,action)
            code='import base64;exec(compile(base64.b64decode('+repr(helper)+'),"shutdown-node","exec"),{"__name__":"__main__","HEALTH_SOURCE":'+repr(reader)+'})'
            cmd=['sudo','-S','-p','','/usr/bin/python3','-c',code,'--node',node,'--action',action]
            try:
                p=subprocess.run(health.vip.ssh_args(node)+[shlex.join(cmd)],input=passwords[node]+'\n',capture_output=True,text=True,timeout=240,env=env)
                r=json.loads(p.stdout)
                if p.returncode or r.get('node')!=node or not r.get('ok'):raise RuntimeError(node+': '+r.get('error','action outcome unknown'))
                return r['value']
            except (OSError,ValueError,subprocess.TimeoutExpired):raise RuntimeError(node+': SSH/outcome unavailable; no retry') from None
        print('Checking nodes, database/broker and actual VM runtime…',flush=True)
        records={node:remote(node,'partial') for node in NODES} if args.finish else collect_checks(remote)
        if args.check:
            ready,lines=check_lines(records)
            fd,name=tempfile.mkstemp(prefix='shutdown-check-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),suffix='.txt',dir=reports)
            with os.fdopen(fd,'w') as output:output.write('\n'.join(lines)+'\n')
            with Path(name).with_suffix('.json').open('x') as output:json.dump(records,output,indent=2)
            for line in lines:print(line)
            print('Private report: '+name)
            return 0 if ready else 1
        if args.finish:continuation(previous,records);cluster=previous['cluster_uuid']
        else:cluster=baseline(records)
        print('Node  | Galera | RabbitMQ | Core containers | Active VMs')
        for n in NODES:print(n+' | OK     | OK       | OK              | 0')
        print('This stops ALL cloud services only. Ubuntu hosts and R2d2 stay powered on.\nConfirm no active cloud/storage operations and all guests cleanly shut down.\nThe script does not stop guests.')
        if input('Type STOP ALL CLOUD SERVICES to proceed: ')!='STOP ALL CLOUD SERVICES':raise RuntimeError('Cancelled before service changes')
        # Fresh readiness immediately before the first mutation.
        if args.finish:continuation(previous,{n:remote(n,'partial') for n in NODES})
        else:cluster=baseline({n:remote(n,'inspect') for n in NODES})
        stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-')
        fd,report=tempfile.mkstemp(prefix='shutdown-'+stamp,suffix='.txt',dir=reports)
        if not args.finish:receipt=folder/(Path(report).stem+'.json')
        print('Private report: '+report,flush=True)
        with os.fdopen(fd,'w') as out:
            def log(line):print(line,flush=True);out.write(line+'\n');out.flush()
            def save(value):health.prep.net.write_state(receipt,value)
            fd,opt=tempfile.mkstemp(prefix='shutdown-credentials-',suffix='.json',dir=folder);options=Path(opt)
            with os.fdopen(fd,'w') as secret:
                json.dump({'shutdown_passwords':passwords,'ansible_become_password':'{{ shutdown_passwords[inventory_hostname] }}'},secret)
            def stop(service,node):
                unchanged()
                cmd=stop_args(config,inventory,options,service,node)
                logfile=Path(report).with_name(Path(report).stem+'-'+(service or 'cloud')+'-'+(node or 'all')+'.log')
                with logfile.open('x') as transcript:
                    p=subprocess.Popen(cmd,stdout=transcript,stderr=subprocess.STDOUT,env=env)
                    while True:
                        try:rc=p.wait(timeout=30);break
                        except subprocess.TimeoutExpired:log('  Kolla is still stopping services…')
                if rc:raise RuntimeError('Kolla stop failed; see private log '+logfile.name)
                unchanged()
            try:
                state=workflow(remote,stop,save,log,cluster,previous)
                finish(state,save,log)
                return 0
            except BaseException:
                log('RESULT: SHUTDOWN_NEEDS_REVIEW\nStop here. Some services may already be stopped. No host poweroff is performed. No automatic retry, restart, rollback or bootstrap. Keep the receipt and report.')
                raise
    finally:
        if options is not None:options.unlink(missing_ok=True)
        passwords.clear()
        for handle in reversed(handles):handle.close()

if __name__=='__main__':
    try:sys.exit(main())
    except (Exception,KeyboardInterrupt) as error:
        label='Check could not finish; no service-stop or poweroff commands sent: ' if '--check' in sys.argv else 'Script ended: '
        print(label+(str(error) if isinstance(error,RuntimeError) else type(error).__name__+'; review private report'),file=sys.stderr)
        sys.exit(1)

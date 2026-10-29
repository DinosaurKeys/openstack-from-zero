#!/usr/bin/env python3
"""Read-only cluster health snapshot after startup or before maintenance."""
import sys
sys.dont_write_bytecode=True
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import getpass
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import uuid

REPO=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('health_vip',REPO/'scripts/phase5-api-vip.py')
vip=importlib.util.module_from_spec(spec);spec.loader.exec_module(vip)
prep=vip.prep;access=vip.access;deploy=vip.deploy;inv=vip.inv;ENV=vip.ENV
NODES=inv.NODES


def api_error_reason(error):
    raw=str(error);http=re.search(r'\bHTTP ([1-5][0-9]{2})\b',raw)
    if http:return 'HTTP '+http[1]
    if 'certificate_verify_failed' in raw.lower() or 'certificate verify failed' in raw.lower():return 'TLS certificate verification failed'
    if isinstance(error,TimeoutError) or 'timed out' in raw.lower():return 'Connection timed out'
    if isinstance(error,FileNotFoundError):return 'Private client input file missing'
    return type(error).__name__+'; API or local client profile unavailable'


class ReadAPI(prep.API):
    def write(self,*args,**kwargs):raise RuntimeError('Health checks cannot change cloud resources')


def checked(rows,details,label,function):
    try:
        ok,value=function();details[label]=value
        rows.append({'check':label,'status':'PASS' if ok is True else 'REVIEW'})
    except (Exception,KeyboardInterrupt) as error:
        if isinstance(error,KeyboardInterrupt):raise
        # Keep a safe HTTP code, never arbitrary exception text or credentials.
        rows.append({'check':label,'status':'UNKNOWN'})
        match=re.search(r'\bHTTP ([1-5][0-9]{2})\b',str(error)) if isinstance(error,RuntimeError) else None
        details[label]={'error':'HTTP '+match[1]+'; read unavailable' if match else type(error).__name__+'; read unavailable; no repair requested'}


def transport_reason(code,stderr):
    text=stderr.lower()
    if 'permission denied (publickey' in text:return 'SSH key authentication rejected'
    if 'host key verification failed' in text or 'remote host identification has changed' in text:return 'SSH host identity needs review'
    if any(s in text for s in ('incorrect password','sorry, try again','no password was provided')):return 'Sudo authentication failed'
    if 'a password is required' in text:return 'Sudo requires authentication'
    if 'connection refused' in text:return 'SSH connection refused'
    if 'no route to host' in text:return 'Management network route unavailable'
    return 'SSH/sudo or collector output unavailable (exit '+str(code)+')'


def host_observe(node,password,env):
    encoded=base64.b64encode((REPO/'scripts/lab-health-node.py').read_bytes()).decode()
    code='import base64;exec(compile(base64.b64decode('+repr(encoded)+'),"health-node","exec"),{"__name__":"__main__"})'
    remote=shlex.join(['sudo','-S','-p','','/usr/bin/python3','-c',code,'--node',node])
    try:
        p=subprocess.run(vip.ssh_args(node)+[remote],input=password+'\n',capture_output=True,text=True,env=env,timeout=240)
        try:value=json.loads(p.stdout)
        except ValueError:
            return {'node':node,'observed':False,'error':transport_reason(p.returncode,p.stderr)}
        if not isinstance(value,dict) or value.get('node')!=node or type(value.get('observed')) is not bool or (p.returncode and value['observed']):raise ValueError()
        return value
    except subprocess.TimeoutExpired:
        return {'node':node,'observed':False,'error':'Host collection exceeded 240 seconds; state unknown'}
    except (OSError,ValueError):
        return {'node':node,'observed':False,'error':'SSH/sudo or structured host response unavailable'}


def summary_lines(rows,details):
    status={r['check']:r['status'] for r in rows}
    lines=['Node | Access | Database | RabbitMQ | Containers | USB | Clock']
    suffixes=('management SSH/identity','Galera','RabbitMQ','core containers','USB/br-ex','clock sync')
    for node in NODES:lines.append(node+' | '+' | '.join(status.get(node+' '+s,'UNKNOWN') for s in suffixes))
    lines.append('APIs | '+' | '.join(label+': '+status.get(key,'UNKNOWN') for label,key in
      (('Login','HTTPS authentication'),('Horizon','Horizon HTTPS'),('Images','image HTTPS API read'),
       ('Compute','compute HTTPS API read'),('Network','network HTTPS API read'),
       ('Volumes','block-storage HTTPS API read'),('Placement','placement HTTPS API read'))))
    for row in rows:
        if row['check'].startswith(tuple(NODES)) or row['check'] in ('HTTPS authentication','Horizon HTTPS','public service APIs') or 'HTTPS API read' in row['check']:continue
        lines.append(row['check']+' | '+row['status'])
    lines.append('Needs attention:')
    attention=[]
    primary=False
    for node in NODES:
        record=details.get('hosts',{}).get(node,{})
        if not record.get('observed'):
            # Only generated/classified collector errors, never raw SSH stderr.
            reason=record.get('error','host observation unavailable')
            attention.append(node+': '+reason+'. Check management SSH/sudo; the node is not proven down.')
        value=record.get('containers',{}).get('value',{})
        if value.get('missing'):attention.append(node+' missing containers: '+', '.join(value['missing']))
        if value.get('unhealthy'):attention.append(node+' unhealthy/stopped: '+', '.join(value['unhealthy']))
        db=record.get('galera',{}).get('value',{})
        primary=primary or db.get('wsrep_cluster_status')=='Primary'
    if primary and status.get('Galera: same three-member Primary cluster')!='PASS':
        attention.append('A surviving Galera Primary component was observed. Do not repeat full-cluster database recovery; inspect the missing/unready member first.')
    for label,value in details.items():
        if label in status and status[label]=='UNKNOWN' and isinstance(value,dict) and str(value.get('error','')).startswith('HTTP '):
            attention.append(label+': '+value['error'])
    unresolved=details.get('exercise checkpoints',{}).get('unresolved',[])
    if unresolved:attention.append('Unresolved exercise: '+', '.join(unresolved)+'. Preserved; no automatic retry or reset.')
    lines.extend(attention or ['See REVIEW/UNKNOWN rows; collection does not authorize repair.' if any(r['status']!='PASS' for r in rows) else 'None in this snapshot.'])
    return lines


def member_ok(status):
    expected={'wsrep_cluster_status':'Primary','wsrep_cluster_size':'3',
      'wsrep_local_state_comment':'Synced','wsrep_connected':'ON','wsrep_ready':'ON'}
    try:valid_uuid=str(uuid.UUID(status.get('wsrep_cluster_state_uuid','')))==status.get('wsrep_cluster_state_uuid')
    except (ValueError,TypeError):valid_uuid=False
    return valid_uuid and all(status.get(k)==v for k,v in expected.items())


def host_rows(records,rows,details):
    for node in NODES:
        record=records[node]
        rows.append({'check':node+' management SSH/identity','status':'PASS' if record.get('observed') else 'UNKNOWN'})
        for key,label in (('containers','core containers'),('galera','Galera'),('rabbitmq','RabbitMQ'),('provider_network','USB/br-ex'),('clock','clock sync')):
            item=record.get(key,{})
            if not item.get('observed'):
                rows.append({'check':node+' '+label,'status':'UNKNOWN'});continue
            value=item.get('value')
            ok=member_ok(value) if key=='galera' else (value is True if key=='clock' else value.get('healthy') is True)
            rows.append({'check':node+' '+label,'status':'PASS' if ok else 'REVIEW'})
    def database():
        if not all(r.get('galera',{}).get('observed') for r in records.values()):raise RuntimeError('Incomplete database observations')
        values=[r.get('galera',{}).get('value',{}) for r in records.values()]
        return (all(member_ok(v) for v in values) and len({v.get('wsrep_cluster_state_uuid') for v in values})==1,
          {n:r.get('galera',{}) for n,r in records.items()})
    checked(rows,details,'Galera: same three-member Primary cluster',database)
    def vip_owner():
        if not all(r.get('observed') for r in records.values()):raise RuntimeError('Incomplete VIP observation')
        owners=[n for n,r in records.items() if r.get('vip_interfaces')==['enp0s31f6']]
        return len(owners)==1 and all(r.get('vip_interfaces') in ([],['enp0s31f6']) for r in records.values()),{'owners':owners}
    checked(rows,details,'API VIP: exactly one owner',vip_owner)


def service_health(rows,expected,binary_key='binary',up_key='state'):
    selected=[{k:r.get(k) for k in ('host',binary_key,'status',up_key)} for r in rows if r.get(binary_key) in expected]
    actual={}
    for r in selected:
        key=(r[binary_key],str(r.get('host','')).split('.')[0]);actual.setdefault(key,[]).append(r)
    ok=set(actual)=={(b,n) for b in expected for n in NODES}
    ok=ok and all(len(v)==1 and v[0].get('status')=='enabled' and v[0].get(up_key)=='up' for v in actual.values())
    return ok,selected


def neutron_health(agents):
    expected=('neutron-dhcp-agent','neutron-l3-agent','neutron-openvswitch-agent','neutron-metadata-agent')
    rows=[{k:a.get(k) for k in ('host','binary','alive','admin_state_up')} for a in agents if a.get('binary') in expected]
    identities=[(a['binary'],str(a['host']).split('.')[0]) for a in rows]
    return (len(rows)==12 and set(identities)=={(b,n) for b in expected for n in NODES}
      and all(a['alive'] is True and a['admin_state_up'] is True for a in rows)),rows


def cinder_service_health(rows):
    expected=('cinder-scheduler','cinder-volume')
    # Validate the same node registrations while retaining Cinder's full host
    # (node@backend) for the operator's service table.
    ok,_=service_health([{**row,'host':str(row.get('host','')).split('@')[0]} for row in rows],expected)
    return ok,[{key:row.get(key) for key in ('host','binary','status','state')}
               for row in rows if row.get('binary') in expected]


def read_private(path):
    deploy.validation.private_file(path)
    return json.loads(path.read_text())


def api_checks(rows,details,env,include_workload=True):
    try:
        _,config=deploy.validation.local_checks(lambda line:None,env,deploy.validation.CA_FINGERPRINT)
        original,project,user=prep.authenticate(config)
        api=ReadAPI(original.client,original.urls,original.token)
    except Exception as error:
        rows.append({'check':'HTTPS authentication','status':'UNKNOWN'})
        details['HTTPS authentication']={'error':api_error_reason(error)+'; token withheld'}
        for label in ('Horizon HTTPS','public service APIs',*[s+' HTTPS API read' for s in access.API_PATHS],
                      'Nova services','hypervisors','Neutron agents','router HA (API state)','Cinder services')+(('existing VM/volume API state',) if include_workload else ()):
            rows.append({'check':label,'status':'UNKNOWN'})
            details[label]={'blocked_by':'HTTPS authentication','error':'Not queried because authentication/client preparation was unavailable'}
        return
    rows.append({'check':'HTTPS authentication','status':'PASS'})
    def horizon():return True,api.client.horizon()
    checked(rows,details,'Horizon HTTPS',horizon)
    for service,(path,key) in access.API_PATHS.items():
        def read(s=service,p=path,k=key):
            page=api.get(s,p)
            return isinstance(page.get(k),list),{'list_response':isinstance(page.get(k),list)}
        checked(rows,details,service+' HTTPS API read',read)
    checked(rows,details,'Nova services',lambda:service_health(api.list('compute','os-services','services'),('nova-scheduler','nova-conductor','nova-compute')))
    def hypervisors():
        records=api.list('compute','os-hypervisors/detail','hypervisors')
        selected=[{k:r.get(k) for k in ('hypervisor_hostname','state','status')} for r in records]
        names=[str(r['hypervisor_hostname']).split('.')[0] for r in selected]
        return len(selected)==3 and set(names)==set(NODES) and all(r['state']=='up' and r['status']=='enabled' for r in selected),selected
    checked(rows,details,'hypervisors',hypervisors)
    checked(rows,details,'Neutron agents',lambda:neutron_health(api.list('network','v2.0/agents','agents')))
    checked(rows,details,'Cinder services',lambda:cinder_service_health(api.list('block-storage','os-services','services')))
    def router():
        state=read_private(REPO/'private/phase4/networks.json')
        if state.get('project_id')!=project or state.get('api')!=api.urls['network']:raise RuntimeError('Tracked network identity differs')
        identifier=state['ids']['router']
        if str(uuid.UUID(identifier))!=identifier:raise RuntimeError('Invalid router UUID')
        value=api.get('network','v2.0/routers/'+identifier)['router']
        agents=api.get('network','v2.0/routers/'+identifier+'/l3-agents')['agents']
        selected=[{k:a.get(k) for k in ('host','alive','admin_state_up','ha_state')} for a in agents]
        ok=value.get('id')==identifier and value.get('project_id')==project and value.get('status')=='ACTIVE' and value.get('ha') is True
        ok=ok and len(selected)==3 and {str(a['host']).split('.')[0] for a in selected}==set(NODES)
        ok=ok and all(a['alive'] is True and a['admin_state_up'] is True for a in selected) and sorted(str(a['ha_state']).lower() for a in selected)==['active','standby','standby']
        return ok,{'router_id':identifier,'agents':selected,'limit':'API state only; packet traffic not tested'}
    checked(rows,details,'router HA (API state)',router)
    def workload():
        vm=read_private(REPO/'private/phase4/vm.json');volume=read_private(REPO/'private/phase4/volume.json')
        for state in (vm,volume):
            if state.get('project_id')!=project or state.get('user_id')!=user or state.get('apis')!=api.urls:raise RuntimeError('Tracked workload identity differs')
        server_id=vm['ids']['server'];volume_id=volume['volume_id']
        for identifier in (server_id,volume_id):
            if str(uuid.UUID(identifier))!=identifier:raise RuntimeError('Invalid workload UUID')
        server=api.get('compute','servers/'+server_id)['server'];disk=api.get('block-storage','volumes/'+volume_id)['volume']
        attached=disk.get('attachments',[])
        nova_volumes=server.get('os-extended-volumes:volumes_attached',[])
        ok=server.get('id')==server_id and disk.get('id')==volume_id and server.get('status')=='ACTIVE' and server.get('OS-EXT-STS:task_state') is None
        ok=ok and disk.get('status')=='in-use' and len(attached)==1 and attached[0].get('server_id')==server_id and any(a.get('id')==volume_id for a in nova_volumes)
        return ok,{'server_id':server_id,'status':server.get('status'),'compute_host':server.get('OS-EXT-SRV-ATTR:host'),
          'task':server.get('OS-EXT-STS:task_state'),'volume_id':volume_id,'volume_status':disk.get('status'),
          'storage_backend':disk.get('os-vol-host-attr:host'),'limit':'API snapshot; guest SSH, mount and data not checked'}
    if include_workload:checked(rows,details,'existing VM/volume API state',workload)


def checkpoint_checks(rows,details):
    files=('phase5/api-vip-state.json','phase5/mariadb-state.json','phase5/router-state.json','phase5/api-recovery-state.json',
      'phase5/move-vm-state.json','phase4/vm.json','phase4/volume.json')
    unresolved=[];errors=[]
    for name in files:
        path=REPO/'private'/name
        if not path.exists() and not path.is_symlink():continue
        try:
            value=read_private(path)
            if value.get('pending') or value.get('restore_required'):unresolved.append(name)
        except Exception:errors.append(name)
    rows.append({'check':'exercise checkpoints','status':'UNKNOWN' if errors else ('REVIEW' if unresolved else 'PASS')})
    details['exercise checkpoints']={'unresolved':unresolved,'unreadable':errors,'changed':False}


def execute(log,details):
    deploy.source_guard();env=deploy.tools.tool_environment();inv.ensure_agent(env,log)
    passwords={n:getpass.getpass('Joe sudo password on '+n+' (hidden): ') for n in NODES}
    log('Reading all three hosts; database failures will not stop the other checks…')
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs={n:pool.submit(host_observe,n,passwords[n],env) for n in NODES}
            records={n:j.result() for n,j in jobs.items()}
    finally:passwords.clear()
    details['hosts']=records;rows=[]
    host_rows(records,rows,details)
    log('Host checks collected. Checking HTTPS and OpenStack service state…')
    api_checks(rows,details,env);checkpoint_checks(rows,details)
    for line in summary_lines(rows,details):log(line)
    details['summary']=rows
    healthy=all(r['status']=='PASS' for r in rows)
    log('RESULT: '+('CLUSTER_HEALTH_SNAPSHOT_PASSED' if healthy else 'CLUSTER_HEALTH_NEEDS_REVIEW'))
    log('PASS = check passed; REVIEW = observed mismatch; UNKNOWN = check unavailable. Snapshot only; no repair, reboot, migration, guest command or checkpoint change. Guest data/traffic and failover are not tested.')
    return 0 if healthy else 1


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args(argv)
    if not args.check:
        print('PLAN ONLY: read health on node1/node2/node3, then API/service/router/workload state. Run --check on R2d2. No recovery or reboot mode.');return 0
    deploy.validation.config_step.load_step('health_r2d2','phase2-check.py').require_r2d2();deploy.tools.check_paths(ENV)
    if not sys.stdin.isatty():raise RuntimeError('Use joe\'s interactive R2d2 terminal')
    if Path(sys.prefix)!=ENV:
        os.execve(str(ENV/'bin/python'),[str(ENV/'bin/python'),str(Path(__file__).resolve()),'--check'],deploy.tools.tool_environment())
    os.umask(0o077);folder=REPO/'reports';inv.private_dir(folder);details={}
    fd,name=tempfile.mkstemp(prefix='lab-health-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),suffix='.txt',dir=folder)
    print('Private summary: '+name,flush=True);status=1
    with os.fdopen(fd,'w') as stream:
        def log(line):print(line,flush=True);stream.write(line+'\n');stream.flush()
        log('Cluster health — read-only snapshot from R2d2')
        try:status=execute(log,details)
        except (Exception,KeyboardInterrupt) as error:
            log('RESULT: CLUSTER_HEALTH_NEEDS_REVIEW');log(type(error).__name__+'; collection incomplete; no repair requested')
        with Path(name).with_suffix('.json').open('x') as extra:json.dump(details,extra,indent=2)
    print('Return summary: cat '+shlex.quote(name))
    print('Private details: '+str(Path(name).with_suffix('.json')))
    return status


if __name__=='__main__':
    try:sys.exit(main())
    except (RuntimeError,OSError) as error:
        print('Stopped: '+type(error).__name__+'; required local inputs unavailable; no repair requested',file=sys.stderr);sys.exit(1)

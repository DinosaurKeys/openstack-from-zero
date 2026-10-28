#!/usr/bin/env python3
"""One read-only post-reboot verification of services, existing VM, network and data."""
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
health=load('reboot_health','lab-health.py');move=load('reboot_migration','phase5-move-vm.py')
inspection=move.inspection;volume=inspection.volume;boot=volume.boot;prep=volume.prep
deploy=health.deploy;inv=health.inv;ENV=health.ENV


def snapshot():
    result={}
    for folder in ('phase4','phase5'):
        for path in sorted((REPO/'private'/folder).glob('*.json')):
            deploy.validation.private_file(path)
            result[str(path.relative_to(REPO))]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def bindings(api,project,user):
    folder=REPO/'private/phase4'
    vm=health.read_private(folder/'vm.json');data=health.read_private(folder/'volume.json')
    prepared=health.read_private(folder/'vm-preparation.json')
    for state in (vm,data,prepared):
        if state.get('project_id')!=project or state.get('user_id')!=user or state.get('apis')!=api.urls:
            raise RuntimeError('Saved workload identity/project/API differs; no guest connection')
    ids=vm['ids']
    for identifier in (*ids.values(),data['volume_id'],data['filesystem_uuid']):
        if not volume.valid_uuid(identifier):raise RuntimeError('Invalid tracked resource UUID')
    if data.get('server_id')!=ids['server'] or data.get('sha256')!=volume.expected_checksum(data):raise RuntimeError('Saved data-test identity differs')
    if vm.get('pending') or data.get('pending') or data.get('stage')!='complete':raise RuntimeError('VM/volume operation remains pending; inspect before guest checks')
    if vm.get('prepared')!=prepared.get('ids'):raise RuntimeError('Prepared VM resource identities differ')
    return vm,data,prepared


def api_workload(api,vm,data,project,user):
    server=api.get('compute','servers/'+vm['ids']['server'])['server']
    if server.get('id')!=vm['ids']['server']:raise RuntimeError('Server UUID differs')
    # Check identity even if Nova reports ERROR; the actual running guest is a separate observation.
    boot.check_server(server,project,user,vm['networks'],vm['prepared'],data['volume_id'])
    port=api.get('network','v2.0/ports/'+vm['ids']['port'])['port']
    private=boot.check_port(port,project,vm['networks'],vm['prepared'],vm['ids']['server'])
    floating=api.get('network','v2.0/floatingips/'+vm['ids']['floating_ip'])['floatingip']
    address=boot.check_floating(floating,project,vm['networks'],vm['ids']['port'],private)
    disk=api.get('block-storage','volumes/'+data['volume_id'])['volume']
    backend=volume.volume_check(disk,data,project)
    attachment=volume.attachments(api,data);volume.check_attachment(attachment,data,True)
    attached=disk.get('status')=='in-use' and len(disk.get('attachments',[]))==1
    summary={'server_id':server['id'],'server_status':server.get('status'),'host':str(server.get('OS-EXT-SRV-ATTR:host','')).split('.')[0],
      'task':server.get('OS-EXT-STS:task_state'),'volume_id':disk['id'],'volume_attached':attached,'backend':backend,
      'private_ip':private,'floating_ip':address,'mac':port['mac_address']}
    return summary


def runtime(nodes,server_id):
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs={n:pool.submit(nodes.call,n,'running',{'server_id':server_id}) for n in inv.NODES}
        records={n:j.result() for n,j in jobs.items()}
    complete=all(v.get('node')==n and v.get('observed') is True and type(v.get('VM_running')) is bool for n,v in records.items())
    return {'observed':complete,'running_hosts':[n for n,v in records.items() if v.get('observed') is True and v.get('VM_running') is True],'nodes':records}


def trusted_guest(vm,prepared,address,log):
    known=REPO/'private/phase4/lab-vm-01-known-hosts';deploy.validation.private_file(known)
    fields=known.read_text().split()
    if len(fields)!=3 or fields[0]!=address or fields[1]!='ssh-ed25519' or boot.key_fingerprint(' '.join(fields[1:]))!=vm.get('host_fingerprint'):
        raise RuntimeError('Existing guest SSH trust differs; no key replacement')
    public=Path(str(prep.KEY)+'.pub')
    if public.is_symlink() or not public.is_file() or ' '.join(public.read_text().split()[:2])!=prepared.get('public_key'):
        raise RuntimeError('Existing guest key differs from preparation')
    boot.ensure_agent(prepared['public_key'],log)
    return boot.ssh_command(address,known)


def guest_read(ssh,file,expected,timeout):
    if file not in ('phase4-guest-check.py','lab-reboot-guest.py'):raise RuntimeError('Unapproved guest helper')
    payload=base64.b64encode(json.dumps(expected).encode()).decode()
    remote=shlex.join(['sudo','-n','/usr/bin/python3','-',payload])
    result=subprocess.run(ssh+[remote],input=(REPO/'scripts'/file).read_text(),capture_output=True,text=True,timeout=timeout)
    try:value=json.loads(result.stdout)
    except ValueError:raise RuntimeError('Guest SSH/sudo read unavailable; no guest change requested') from None
    if not isinstance(value,dict) or (type(value.get('passed')) is not bool and type(value.get('observed')) is not bool):raise RuntimeError('Guest helper response unavailable')
    if result.returncode and value.get('passed') is not False and value.get('observed') is not False:raise RuntimeError('Guest helper read unavailable; no repair requested')
    return value


def network_context(api,vm,identity):
    network=api.get('network','v2.0/networks/'+vm['networks']['private_network'])['network']
    ports=api.list('network','v2.0/ports?network_id='+vm['networks']['private_network'],'ports')
    ips=sorted({f['ip_address'] for p in ports if p.get('network_id')==vm['networks']['private_network'] and p.get('device_owner')=='network:dhcp'
      for f in p.get('fixed_ips',[]) if f.get('subnet_id')==vm['networks']['private_subnet']})
    if not ips:raise RuntimeError('No observed DHCP server for the tracked subnet')
    return {**identity,'gateway':prep.net.PLAN['private_gateway'],'dns':prep.net.PLAN['dns'][0],'mtu':network['mtu'],'dhcp_server_ips':ips}


def pending_migration(api,vm,project,user):
    path=REPO/'private/phase5/move-vm-state.json'
    if not path.exists() and not path.is_symlink():return {'present':False}
    state=health.read_private(path);move.validate_receipt(state)
    if state.get('server_id')!=vm['ids']['server'] or state.get('project_id')!=project or state.get('user_id')!=user or state.get('apis')!=api.urls:
        raise RuntimeError('Migration receipt identity differs; preserved')
    observer=move.API(api.client,api.urls,api.token,vm['ids']['server'],move.PLAN['destination'])
    return {'present':True,'pending':bool(state.get('pending')),'snapshot':move.migration_snapshot(observer,state),'checkpoint_changed':False}


def record(rows,details,label,function):
    health.checked(rows,details,label,function)


def workload(nodes,api,project,user,rows,details,log):
    vm,data,prepared=bindings(api,project,user)
    current=api_workload(api,vm,data,project,user);details['VM identity']=current
    rows.append({'check':'VM in Nova','status':'PASS' if current['server_status']=='ACTIVE' and current['task'] is None else 'REVIEW'})
    rows.append({'check':'Volume attachment/backend','status':'PASS' if current['volume_attached'] and current['backend']==data.get('backend') else 'REVIEW'})
    physical=runtime(nodes,vm['ids']['server']);details['VM runtime']=physical
    one=physical['observed'] and len(physical['running_hosts'])==1
    rows.append({'check':'VM actually running','status':('PASS' if one else 'REVIEW') if physical['observed'] else 'UNKNOWN'})
    def migration_check():
        value=pending_migration(api,vm,project,user)
        terminal=not value.get('present') or (value.get('snapshot',{}).get('migration') or {}).get('status') in move.TERMINAL
        return not value.get('pending') and terminal,value
    record(rows,details,'Migration record (preserved)',migration_check)
    if not one:
        details['Guest checks']={'issue':'No single verified running VM; SSH/data checks skipped. Review actual runtime before any start or recovery.'}
        for label in ('Guest SSH/identity','Guest network/DHCP/DNS/HTTPS','Data filesystem/mount','Saved file checksum'):rows.append({'check':label,'status':'UNKNOWN'})
        return
    rows.append({'check':'Nova host matches actual host','status':'PASS' if current['host']==physical['running_hosts'][0] else 'REVIEW'})
    try:ssh=trusted_guest(vm,prepared,current['floating_ip'],log)
    except Exception:
        for label in ('Guest SSH/identity','Guest network/DHCP/DNS/HTTPS','Data filesystem/mount','Saved file checksum'):rows.append({'check':label,'status':'UNKNOWN'})
        details['Guest checks']={'issue':'Saved SSH key/trust unavailable or differs; no replacement or trust bypass'};return
    # Full network probe validates the metadata UUID. Do not require Nova ACTIVE just to observe it.
    def network_check():
        value=guest_read(ssh,'phase4-guest-check.py',network_context(api,vm,current),360)
        checks=value.get('checks',{})
        return value.get('passed') is True and bool(checks) and all(v is True for v in checks.values()) and checks.get('metadata_matches_Nova_instance') is True,value
    record(rows,details,'Guest network/DHCP/DNS/HTTPS',network_check)
    network=details.get('Guest network/DHCP/DNS/HTTPS',{})
    verified=network.get('checks',{}).get('metadata_matches_Nova_instance') is True
    rows.append({'check':'Guest SSH/identity','status':'PASS' if verified else 'UNKNOWN'})
    expected={k:data[k] for k in ('server_id','volume_id','filesystem_uuid','nonce','sha256')}
    try:
        result=guest_read(ssh,'lab-reboot-guest.py',expected,90);details['Guest data']=result
        disk_ok=result.get('observed') is True and result.get('disk_identified') is True and result.get('filesystem_matches') is True and result.get('mounted') is True
        rows.append({'check':'Data filesystem/mount','status':'PASS' if disk_ok else ('REVIEW' if result.get('observed') else 'UNKNOWN')})
        checksum=result.get('checksum_matches')
        rows.append({'check':'Saved file checksum','status':'PASS' if checksum is True else ('REVIEW' if checksum is False else 'UNKNOWN')})
    except Exception:
        details['Guest data']={'issue':'Guest data read unavailable; no mount, format or repair attempted'}
        rows.extend({'check':label,'status':'UNKNOWN'} for label in ('Data filesystem/mount','Saved file checksum'))


def execute(log,details):
    deploy.source_guard();env=deploy.tools.tool_environment()
    inventory,config=deploy.validation.local_checks(lambda _:None,env,deploy.validation.CA_FINGERPRINT)
    before=snapshot();input_hashes=health.access.input_hashes(config,inventory);handles=[];passwords={}
    try:
        for folder,name in [('phase5',n) for n in ('api-recovery.lock','api-vip.lock','mariadb.lock','router.lock','migration.lock')]+[('phase4',n) for n in ('vm.lock','volume.lock','networks.lock')]:
            path=REPO/'private'/folder/name
            if path.is_symlink():raise RuntimeError('Unsafe exercise lock')
            handle=path.open('a');handles.append(handle);os.chmod(path,0o600)
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('Another lab/recovery action is running; no verification during changes') from None
        inv.ensure_agent(env,log)
        for node in inv.NODES:passwords[node]=getpass.getpass('Joe sudo password on '+node+' (hidden): ')
        log('1/3 Checking all three hosts and OpenStack services…')
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs={n:pool.submit(health.host_observe,n,passwords[n],env) for n in inv.NODES}
            hosts={n:j.result() for n,j in jobs.items()}
        details['hosts']=hosts;cluster_rows=[]
        health.host_rows(hosts,cluster_rows,details);health.api_checks(cluster_rows,details,env);health.checkpoint_checks(cluster_rows,details)
        service_rows=[r for r in cluster_rows if r['check'] not in ('existing VM/volume API state','exercise checkpoints')]
        services_ok=all(r['status']=='PASS' for r in service_rows);details['cluster_summary']=cluster_rows
        rows=[{'check':'Cluster services/APIs/agents','status':'PASS' if services_ok else 'REVIEW'}]
        log('2/3 Checking the existing VM, actual hypervisor and migration record…')
        try:
            original,project,user=prep.authenticate(config)
            api=volume.ReadOnlyAPI(original.client,original.urls,original.token)
            workload(inspection.Nodes(env,passwords),api,project,user,rows,details,log)
        except Exception as error:
            rows.append({'check':'VM/network/data verification','status':'UNKNOWN'})
            details['Workload verification']={'issue':move.safe_message(str(error)) if isinstance(error,RuntimeError) else type(error).__name__+'; tracked identity/API unavailable; no repair attempted'}
        log('3/3 Confirming all saved checkpoints and deployment inputs are unchanged…')
        unchanged=snapshot()==before and health.access.input_hashes(config,inventory)==input_hashes
        rows.append({'check':'Existing checkpoints/config preserved','status':'PASS' if unchanged else 'REVIEW'})
        checkpoint_status=next(r['status'] for r in cluster_rows if r['check']=='exercise checkpoints')
        rows.append({'check':'Outstanding exercise checkpoints','status':checkpoint_status})
        log('Check | Result')
        for row in rows:log(row['check']+' | '+row['status'])
        log('Needs attention:')
        attention=[]
        if not services_ok:attention.extend(health.summary_lines(cluster_rows,details)[-20:])
        current=details.get('VM identity',{})
        if current and (current['server_status']!='ACTIVE' or current['task'] is not None):attention.append('Nova VM status='+str(current['server_status'])+', task='+str(current['task'])+'. Actual runtime and guest checks are reported separately; no state reset.')
        physical=details.get('VM runtime',{})
        if physical and (not physical['observed'] or len(physical['running_hosts'])!=1):attention.append('Actual VM runtime needs review: '+json.dumps({'complete':physical['observed'],'running_hosts':physical['running_hosts'],'states':{n:v.get('domain_state','unavailable') for n,v in physical.get('nodes',{}).items()}}))
        for key in ('Guest checks','Guest data','Workload verification'):
            value=details.get(key,{})
            if value.get('issue'):attention.append(value['issue'])
            elif value.get('error'):attention.append(key+': '+str(move.safe_message(value['error'])))
        guest_network=details.get('Guest network/DHCP/DNS/HTTPS',{})
        failed=[k for k,v in guest_network.get('checks',{}).items() if v is not True]
        if failed:attention.append('Guest network checks needing review: '+', '.join(failed))
        if guest_network.get('error'):attention.append('Guest network read: '+str(move.safe_message(guest_network['error'])))
        migration=details.get('Migration record (preserved)',{})
        if migration.get('pending'):attention.append('Earlier migration remains pending locally; latest Nova record: '+json.dumps(migration.get('snapshot',{}))+'. Preserved for a separate approved reconciliation.')
        if any(r['status']!='PASS' for r in rows) and not attention:attention.append('Review the non-PASS rows. No automatic repair was attempted.')
        for line in attention or ['None in these verification checks.']:log(line)
        details['summary']=rows;passed=all(r['status']=='PASS' for r in rows)
        log('RESULT: '+('REBOOT_RECOVERY_VERIFIED' if passed else 'REBOOT_RECOVERY_NEEDS_REVIEW'))
        log('Read-only verification. No restart, VM start/reset/migration, mount, format, attachment change or checkpoint clearing. Browser sign-in/failover/storage HA are not tested.')
        return 0 if passed else 1
    finally:
        passwords.clear()
        for handle in reversed(handles):handle.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args(argv)
    if not args.check:print('PLAN ONLY: one combined cluster/VM/runtime/network/data/migration verification. Use --check on R2d2. No repair mode.');return 0
    deploy.validation.config_step.load_step('reboot_r2d2','phase2-check.py').require_r2d2();deploy.tools.check_paths(ENV)
    if not sys.stdin.isatty():raise RuntimeError('Use joe\'s interactive R2d2 terminal')
    if Path(sys.prefix)!=ENV:os.execve(str(ENV/'bin/python'),[str(ENV/'bin/python'),str(Path(__file__).resolve()),'--check'],deploy.tools.tool_environment())
    os.umask(0o077);reports=REPO/'reports';inv.private_dir(reports);details={}
    fd,name=tempfile.mkstemp(prefix='reboot-verify-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),suffix='.txt',dir=reports)
    print('Private summary: '+name,flush=True);status=1
    with os.fdopen(fd,'w') as output:
        def log(line):print(line,flush=True);output.write(line+'\n');output.flush()
        log('Final reboot verification — existing lab only')
        try:status=execute(log,details)
        except (Exception,KeyboardInterrupt) as error:
            log('RESULT: REBOOT_RECOVERY_NEEDS_REVIEW');log(str(error) if isinstance(error,RuntimeError) else type(error).__name__+'; collection incomplete')
        with Path(name).with_suffix('.json').open('x') as extra:json.dump(details,extra,indent=2)
    print('Return this short summary: cat '+shlex.quote(name));return status


if __name__=='__main__':
    try:sys.exit(main())
    except (RuntimeError,OSError) as error:
        print('Stopped: '+type(error).__name__+'; local inputs unavailable; no repair requested',file=sys.stderr);sys.exit(1)

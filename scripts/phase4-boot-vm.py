#!/usr/bin/env python3
"""Chapter D: create one tracked VM/port/floating IP and verify guest networking. Default: plan."""
import sys
sys.dont_write_bytecode = True
import argparse
import base64
import datetime as dt
import fcntl
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('boot_preparation', REPO/'scripts/phase4-prepare-vm.py')
prep = importlib.util.module_from_spec(spec); spec.loader.exec_module(prep)
net = prep.net; access = prep.access; deploy = prep.deploy; ENV = prep.ENV
PLAN = {'version':1, 'server_name':'lab-vm-01', 'port_name':'lab-vm-01-port', 'guest_user':'ubuntu',
        'floating_ip_description':'openstack-from-zero lab-vm-01', 'instances':1,
        'metadata':'HTTP metadata service; no config drive', 'outbound_test':'https://cloud-images.ubuntu.com/'}
USER_DATA = '#cloud-config\nssh:\n  emit_keys_to_console: true\nssh_fp_console_blacklist: []\nssh_key_console_blacklist: [rsa, ecdsa]\n'
HOST_PREFIX = b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20'


class API(prep.API):
    def write(self, service, method, path, body, expected, upload=None):
        allowed = method == 'POST' and (service,path) in {('compute','servers'),('network','v2.0/ports'),('network','v2.0/floatingips')}
        console = service == 'compute' and method == 'POST' and re.fullmatch(r'servers/[a-zA-Z0-9-]+/action',path) is not None and body == {'os-getConsoleOutput':{'length':1000}}
        if not allowed and not console: raise RuntimeError('Unapproved Chapter D API operation')
        if upload is not None: raise RuntimeError('Chapter D does not upload files to APIs')
        if service=='compute' and path=='servers':
            server=body.get('server',{})
            if server.get('name')!=PLAN['server_name'] or server.get('min_count')!=1 or server.get('max_count')!=1:
                raise RuntimeError('Chapter D permits exactly one named VM')
        request = urllib.request.Request(self.url(service,path), data=json.dumps(body).encode(), method=method,
            headers={'X-Auth-Token':self.token,'Content-Type':'application/json','Accept':'application/json'})
        try:
            with self.client.opener.open(request, timeout=45) as response:
                if response.status != expected: raise RuntimeError('Unexpected Chapter D HTTP status '+str(response.status))
                raw=response.read(2_000_001)
                if len(raw)>2_000_000: raise RuntimeError('Chapter D response too large')
                return json.loads(raw)
        except urllib.error.HTTPError as error:
            raise RuntimeError(service+' '+method+' '+path+': HTTP '+str(error.code)+'; no automatic mutation retry') from None
        except (urllib.error.URLError,OSError,ValueError):
            raise RuntimeError(service+' '+method+' '+path+': response unavailable; return report before retrying a write') from None


def ipv4(value, private=False):
    address=ipaddress.IPv4Address(value)
    start=net.PLAN['private_start' if private else 'external_start'];end=net.PLAN['private_end' if private else 'external_end']
    if not ipaddress.IPv4Address(start)<=address<=ipaddress.IPv4Address(end): raise RuntimeError('Allocated address is outside the tracked pool')
    return str(address)


def port_spec(project, networks, prepared):
    return {'name':PLAN['port_name'],'project_id':project,'network_id':networks['private_network'],'admin_state_up':True,
            'port_security_enabled':True,'security_groups':[prepared['group']], 'fixed_ips':[{'subnet_id':networks['private_subnet']}]}


def server_spec(networks, prepared, port_id):
    return {'name':PLAN['server_name'],'imageRef':prepared['image'],'flavorRef':prepared['flavor'],'key_name':prepared['key'],
            'networks':[{'port':port_id}],'min_count':1,'max_count':1,'config_drive':False,
            'metadata':{'ofz_chapter':'phase4-d','ofz_private_network':networks['private_network']},
            'user_data':base64.b64encode(USER_DATA.encode()).decode()}


def check_port(port, project, networks, prepared, server_id=None):
    wanted=port_spec(project,networks,prepared);fixed=wanted.pop('fixed_ips')
    if not net.subset(wanted,port) or port.get('allowed_address_pairs'):
        raise RuntimeError('Tracked VM port/security settings differ from plan')
    addresses=port.get('fixed_ips',[])
    if len(addresses)!=1 or addresses[0].get('subnet_id')!=fixed[0]['subnet_id']:
        raise RuntimeError('VM port must have one address on the tracked private subnet')
    address=ipv4(addresses[0].get('ip_address'),True)
    if not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}',port.get('mac_address','')):
        raise RuntimeError('VM port MAC address is missing')
    owner=port.get('device_id','')
    if owner and (owner!=server_id or not port.get('device_owner','').startswith('compute:')): raise RuntimeError('VM port is attached to an unexpected server or owner')
    return address


def check_server(server, project, user, networks, prepared):
    wanted={'name':PLAN['server_name'],'tenant_id':project,'user_id':user,'key_name':prepared['key'],
            'metadata':{'ofz_chapter':'phase4-d','ofz_private_network':networks['private_network']},
            'image':{'id':prepared['image']},'flavor':{'id':prepared['flavor']}}
    if not net.subset(wanted,server): raise RuntimeError('Tracked VM differs from the approved image/flavor/key/project')
    if server.get('config_drive') not in ('',False,'False') or server.get('os-extended-volumes:volumes_attached') != []:
        raise RuntimeError('VM must use HTTP metadata and an image-backed local root disk without Cinder attachments')


def check_floating(obj, project, networks, port_id, private_ip):
    wanted={'project_id':project,'floating_network_id':networks['external_network'],'port_id':port_id,
            'fixed_ip_address':private_ip,'router_id':networks['router'],'description':PLAN['floating_ip_description']}
    if not net.subset(wanted,obj): raise RuntimeError('Tracked floating IP association differs from plan')
    return ipv4(obj.get('floating_ip_address'))


def preflight(api, state, project, user, networks, prepared):
    ids=state['ids']
    if state.get('pending'): raise RuntimeError('An earlier request has an uncertain outcome; return report before retrying')
    if set(ids)-{'port','server','floating_ip'} or any(not net.valid_id(v) for v in ids.values()): raise RuntimeError('Unexpected Chapter D tracked IDs')
    if 'server' in ids and 'port' not in ids or 'floating_ip' in ids and 'server' not in ids: raise RuntimeError('Incomplete Chapter D identity chain')
    ports=api.list('network','v2.0/ports','ports');servers=api.list('compute','servers/detail?limit=100','servers')
    floating=api.list('network','v2.0/floatingips','floatingips')
    for kind,objects,name in [('port',ports,PLAN['port_name']),('server',servers,PLAN['server_name'])]:
        selected=[o for o in objects if o.get('name')==name]
        if len(selected)>1 or any(o.get('id')!=ids.get(kind) for o in selected): raise RuntimeError('Untracked or duplicate '+kind+' name; no adoption')
        if kind in ids and len(selected)!=1: raise RuntimeError('Tracked '+kind+' is missing or renamed')
    port=next((p for p in ports if p.get('id')==ids.get('port')),None)
    private=check_port(port,project,networks,prepared,ids.get('server')) if port else None
    if 'server' in ids: check_server(next(s for s in servers if s['id']==ids['server']),project,user,networks,prepared)
    relevant=[f for f in floating if f.get('description')==PLAN['floating_ip_description'] or ('port' in ids and f.get('port_id')==ids['port']) or f.get('id')==ids.get('floating_ip')]
    if len(relevant)>1 or any(f.get('id')!=ids.get('floating_ip') for f in relevant): raise RuntimeError('Untracked or conflicting floating IP; no association changes')
    if 'floating_ip' in ids:
        if len(relevant)!=1: raise RuntimeError('Tracked floating IP is missing')
        check_floating(relevant[0],project,networks,ids['port'],private)
    return port


def create_one(api,state,save,label,service,path,body,expected,wrapper):
    state['pending']=label;save(state)
    response=api.write(service,'POST',path,body,expected);obj=response.get(wrapper,{})
    identifier=obj.get('id')
    if not net.valid_id(identifier): raise RuntimeError('Create response missing a valid '+label+' ID')
    state['ids'][label]=identifier;state.pop('pending',None);save(state)
    # Never log the create-server response: it may contain adminPass.
    return identifier


def wait_server(api,identifier):
    deadline=time.monotonic()+600
    while True:
        server=api.get('compute','servers/'+identifier)['server']
        if server.get('status')=='ACTIVE':return server
        if server.get('status')!='BUILD' or time.monotonic()>=deadline: raise RuntimeError('VM did not reach ACTIVE; resources retained for review')
        time.sleep(5)


def check_quota(api):
    limits=api.get('compute','limits').get('limits',{}).get('absolute',{})
    for maximum,used,need in [('maxTotalInstances','totalInstancesUsed',1),('maxTotalRAMSize','totalRAMUsed',prep.PLAN['ram_mb']),('maxTotalCores','totalCoresUsed',prep.PLAN['vcpus'])]:
        cap,consumed=limits.get(maximum),limits.get(used)
        if type(cap) is not int or type(consumed) is not int or (cap!=-1 and cap-consumed<need):
            raise RuntimeError('Nova project quota does not confirm capacity for one VM: '+maximum)


def provision(api,state,project,user,networks,prepared,save,log,verify_only=False):
    port=preflight(api,state,project,user,networks,prepared);ids=state['ids']
    if verify_only and set(ids)!={'port','server','floating_ip'}: raise RuntimeError('Verification requires all three tracked resources; no creates in --verify')
    if 'server' not in ids: check_quota(api)
    if 'port' not in ids:
        log('Creating one private VM port with the prepared security group.')
        create_one(api,state,save,'port','network','v2.0/ports',{'port':port_spec(project,networks,prepared)},201,'port')
        port=api.get('network','v2.0/ports/'+ids['port'])['port']
    private=check_port(port,project,networks,prepared,ids.get('server'))
    if 'server' not in ids:
        if port.get('device_id') or port.get('status')!='DOWN': raise RuntimeError('The precreated VM port must be unbound/DOWN before boot')
        log('Creating lab-vm-01; Nova selects its compute host. Waiting for ACTIVE.')
        create_one(api,state,save,'server','compute','servers',{'server':server_spec(networks,prepared,ids['port'])},202,'server')
    server=wait_server(api,ids['server']);check_server(server,project,user,networks,prepared)
    host=server.get('OS-EXT-SRV-ATTR:host','').split('.')[0]
    if host not in ('node1','node2','node3'): raise RuntimeError('Nova did not report a known compute host')
    deadline=time.monotonic()+180
    while True:
        port=api.get('network','v2.0/ports/'+ids['port'])['port'];private=check_port(port,project,networks,prepared,ids['server'])
        if port.get('device_id')==ids['server'] and port.get('status')=='ACTIVE':break
        if time.monotonic()>=deadline: raise RuntimeError('VM port is not ACTIVE on its tracked server')
        time.sleep(5)
    if 'floating_ip' not in ids:
        log('Allocating and associating one floating IP from the tracked external network.')
        body={'floatingip':{'project_id':project,'floating_network_id':networks['external_network'],'subnet_id':networks['external_subnet'],
              'port_id':ids['port'],'fixed_ip_address':private,'description':PLAN['floating_ip_description']}}
        create_one(api,state,save,'floating_ip','network','v2.0/floatingips',body,201,'floatingip')
    deadline=time.monotonic()+180
    while True:
        floating=api.get('network','v2.0/floatingips/'+ids['floating_ip'])['floatingip']
        external=check_floating(floating,project,networks,ids['port'],private)
        if floating.get('status')=='ACTIVE':break
        if time.monotonic()>=deadline: raise RuntimeError('Floating IP did not report ACTIVE')
        time.sleep(5)
    preflight(api,state,project,user,networks,prepared)
    return {'server_id':ids['server'],'compute_host':host,'private_ip':private,'floating_ip':external,'mac':port['mac_address']}


def key_fingerprint(public):
    fields=public.split()
    if len(fields)<2 or fields[0]!='ssh-ed25519': raise RuntimeError('Expected an Ed25519 public key')
    try: blob=base64.b64decode(fields[1],validate=True)
    except ValueError: raise RuntimeError('Malformed public key') from None
    if len(blob)!=51 or not blob.startswith(HOST_PREFIX): raise RuntimeError('Malformed Ed25519 key structure')
    return 'SHA256:'+base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')


def console_fingerprints(text):
    found=set()
    for line in text.splitlines():
        if 'ED25519' in line.upper(): found.update(re.findall(r'SHA256:[A-Za-z0-9+/]+',line))
        match=re.search(r'\bssh-ed25519\s+([A-Za-z0-9+/]+={0,2})(?:\s|$)',line)
        if match: found.add(key_fingerprint('ssh-ed25519 '+match.group(1)))
    return found


def pin_guest(api,server_id,address,folder,state,save,log):
    deadline=time.monotonic()+600
    while True:
        console=api.write('compute','POST','servers/'+server_id+'/action',{'os-getConsoleOutput':{'length':1000}},200).get('output','')
        trusted=console_fingerprints(console)
        scan=subprocess.run(['ssh-keyscan','-T','5','-t','ed25519',address],capture_output=True,text=True,timeout=15)
        entries=[line.split() for line in scan.stdout.splitlines() if line and not line.startswith('#')]
        keys={' '.join(parts[1:3]) for parts in entries if len(parts)>=3 and parts[1]=='ssh-ed25519'}
        if len(keys)>1: raise RuntimeError('Multiple guest SSH host keys observed; no trust change')
        if keys and trusted:
            key=keys.pop();fingerprint=key_fingerprint(key)
            if fingerprint not in trusted: raise RuntimeError('Guest SSH host key does not match its authenticated Nova console')
            break
        if time.monotonic()>=deadline: raise RuntimeError('Guest SSH/console host key not ready within ten minutes; resources retained')
        time.sleep(5)
    if state.get('host_fingerprint') not in (None,fingerprint): raise RuntimeError('Tracked guest SSH identity changed; no replacement')
    path=folder/'lab-vm-01-known-hosts';content=address+' '+key+'\n'
    if path.exists() or path.is_symlink():
        deploy.validation.private_file(path)
        if path.read_text()!=content: raise RuntimeError('Private guest known-hosts file differs; preserved')
    else:
        with path.open('x') as out: out.write(content)
        path.chmod(0o600)
    state['host_fingerprint']=fingerprint;save(state)
    log('Guest SSH host key matched its console fingerprint: '+fingerprint)
    return path


def ensure_agent(public,log):
    expected=key_fingerprint(public)
    result=subprocess.run(['ssh-add','-l','-E','sha256'],capture_output=True,text=True,timeout=10)
    if result.returncode==2:
        agent=subprocess.run(['ssh-agent','-s'],capture_output=True,text=True,check=True,timeout=10)
        sock=re.search(r'SSH_AUTH_SOCK=([^;\n]+);',agent.stdout);pid=re.search(r'SSH_AGENT_PID=(\d+);',agent.stdout)
        if not sock or not pid: raise RuntimeError('Could not prepare the local SSH agent')
        os.environ.update(SSH_AUTH_SOCK=sock.group(1),SSH_AGENT_PID=pid.group(1))
    elif result.returncode not in (0,1):raise RuntimeError('Local SSH agent inspection failed')
    if expected not in result.stdout.split():
        log('Unlock the existing guest key in this terminal if asked; its passphrase is never recorded.')
        subprocess.run(['ssh-add','-t','3600',str(prep.KEY)],check=True)
    loaded=subprocess.run(['ssh-add','-l','-E','sha256'],capture_output=True,text=True,check=True,timeout=10)
    if expected not in loaded.stdout.split(): raise RuntimeError('Expected guest key is not available in the SSH agent')


def ssh_command(address,known_hosts):
    return ['ssh','-F','/dev/null','-T','-i',str(prep.KEY),'-o','IdentitiesOnly=yes','-o','BatchMode=yes',
            '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(known_hosts),'-o','GlobalKnownHostsFile=/dev/null',
            '-o','HostKeyAlgorithms=ssh-ed25519','-o','PasswordAuthentication=no','-o','KbdInteractiveAuthentication=no',
            '-o','ConnectTimeout=10','-o','ConnectionAttempts=1','-o','ForwardAgent=no','-o','ClearAllForwardings=yes',
            'ubuntu@'+address]


def guest_checks(api,identity,networks,folder,state,save,log):
    address=identity['floating_ip'];known=pin_guest(api,identity['server_id'],address,folder,state,save,log)
    ssh=ssh_command(address,known);deadline=time.monotonic()+300
    while True:
        ready=subprocess.run(ssh+['/usr/bin/true'],capture_output=True,text=True,timeout=20)
        if ready.returncode==0:break
        if time.monotonic()>=deadline:raise RuntimeError('Guest key login not ready within five minutes; resources retained')
        time.sleep(5)
    log('SSH key login succeeded. Checking guest initialization, DHCP, metadata, DNS and HTTPS.')
    private_network=api.get('network','v2.0/networks/'+networks['private_network'])['network']
    ports=api.list('network','v2.0/ports?network_id='+networks['private_network'],'ports')
    dhcp_ips=sorted({f['ip_address'] for p in ports if p.get('network_id')==networks['private_network'] and p.get('device_owner')=='network:dhcp'
                     for f in p.get('fixed_ips',[]) if f.get('subnet_id')==networks['private_subnet']})
    if not dhcp_ips:raise RuntimeError('No observed private DHCP service IPs')
    context=dict(identity,gateway=net.PLAN['private_gateway'],dns=net.PLAN['dns'][0],mtu=private_network['mtu'],dhcp_server_ips=dhcp_ips)
    encoded=base64.b64encode(json.dumps(context).encode()).decode()
    command=shlex.join(['sudo','-n','/usr/bin/python3','-',encoded])
    helper=(REPO/'scripts/phase4-guest-check.py').read_text()
    response=subprocess.run(ssh+[command],input=helper,capture_output=True,text=True,timeout=360)
    try: observations=json.loads(response.stdout)
    except ValueError:raise RuntimeError('Guest read checks did not return structured observations; sudo or guest runtime needs review') from None
    log('Guest observations:\n'+json.dumps(observations,indent=2))
    if response.returncode or observations.get('passed') is not True or not observations.get('checks') or not all(v is True for v in observations['checks'].values()):
        raise RuntimeError('One or more guest checks did not pass; no guest repair is performed')
    ping=subprocess.run(['ping','-n','-c','3','-W','2',address],capture_output=True,text=True,timeout=15)
    if ping.returncode:raise RuntimeError('R2d2-to-floating-IP ping did not pass; resources retained')
    return {'SSH_key_login':'passed','SSH_host_identity':'matched authenticated Nova console','R2d2_to_floating_IP_ping':'passed','guest':observations}


def load_inputs(api,project,user,folder):
    def read(name):
        path=folder/name;deploy.validation.private_file(path);value=json.loads(path.read_text())
        if value.get('pending'):raise RuntimeError('Earlier chapter has an uncertain request; return report')
        return value
    networks=read('networks.json');prepared=read('vm-preparation.json')
    if networks.get('plan')!=net.PLAN or networks.get('project_id')!=project or networks.get('api')!=api.urls['network'] or set(networks.get('ids',{}))!=set(net.KINDS):
        raise RuntimeError('Chapter B identity/plan requires review')
    if prepared.get('plan')!=prep.PLAN or prepared.get('project_id')!=project or prepared.get('user_id')!=user or prepared.get('apis')!=api.urls or set(prepared.get('ids',{}))!={'image','flavor','key','group'}:
        raise RuntimeError('Chapter C identity/plan requires review')
    ids=prepared['ids']
    if not all(net.valid_id(v) for v in networks['ids'].values()) or not all(isinstance(v,str) and re.fullmatch(r'[a-zA-Z0-9_.-]+',v) for v in ids.values()):
        raise RuntimeError('Earlier chapter contains invalid resource identifiers')
    neutron=net.Neutron(api.client,api.urls['network'],api.token)
    net.preflight(neutron,networks['ids'],project);net.wait_ha(neutron,networks['ids']['router']);net.wait_dhcp(neutron,networks['ids']['private_network'])
    public=prepared.get('public_key','');key_fingerprint(public);deploy.validation.private_file(prep.KEY)
    local=Path(str(prep.KEY)+'.pub')
    if local.is_symlink() or not local.is_file() or ' '.join(local.read_text().split()[:2])!=public:raise RuntimeError('Guest public key differs from tracked preparation')
    prep.preflight(api,prepared,project,public)
    image=api.get('image','v2/images/'+ids['image'])
    if image.get('status')!='active':raise RuntimeError('Prepared image is not active')
    cache=folder/'ubuntu-24.04-20261017.img';metadata=prep.inspect_image(cache)
    if image.get('os_hash_algo') not in ('sha256','sha512') or image.get('os_hash_value')!=metadata[image['os_hash_algo']] or image.get('size')!=metadata['size']:
        raise RuntimeError('Prepared image content differs from the verified download')
    group=api.get('network','v2.0/security-groups/'+ids['group'])['security_group'];prep.check_rules(group,True)
    services=api.list('compute','os-services','services')
    for host in ('node1','node2','node3'):
        match=[s for s in services if s.get('binary')=='nova-compute' and s.get('host','').split('.')[0]==host]
        if len(match)!=1 or match[0].get('state')!='up' or match[0].get('status')!='enabled':raise RuntimeError('Compute service not up/enabled: '+host)
    return networks['ids'],ids,public


def execute(log,verify_only=False):
    deploy.source_guard();env=deploy.tools.tool_environment()
    inventory,config=deploy.validation.local_checks(lambda line:None,env,deploy.validation.CA_FINGERPRINT)
    baseline=access.input_hashes(config,inventory)
    for name in ('ssh','ssh-keyscan','ssh-keygen','ssh-add','ssh-agent','ping'):
        if not shutil.which(name):raise RuntimeError('Required existing client tool unavailable: '+name)
    folder=REPO/'private/phase4';deploy.inv.private_dir(folder)
    lock=folder/'vm.lock'
    if lock.is_symlink():raise RuntimeError('VM lock must not be a symlink')
    with open(lock,'a') as handle:
        os.chmod(lock,0o600)
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Another VM command is running') from None
        original,project,user=prep.authenticate(config);api=API(original.client,original.urls,original.token)
        networks,prepared,public=load_inputs(api,project,user,folder)
        path=folder/'vm.json'
        if path.is_symlink():raise RuntimeError('VM state must not be a symlink')
        binding={'plan':PLAN,'project_id':project,'user_id':user,'apis':api.urls,'networks':networks,'prepared':prepared}
        if path.exists():
            deploy.validation.private_file(path);state=json.loads(path.read_text())
            if any(state.get(k)!=v for k,v in binding.items()):raise RuntimeError('Tracked VM identity/inputs changed')
        else:
            if verify_only:raise RuntimeError('No tracked VM state exists; --verify creates no cloud resources')
            state=dict(binding,ids={})
        save=lambda value:net.write_state(path,value)
        preflight(api,state,project,user,networks,prepared)
        ensure_agent(public,log)
        identity=provision(api,state,project,user,networks,prepared,save,log,verify_only)
        log('Allocated VM identity:\n'+json.dumps(identity,indent=2))
        checks=guest_checks(api,identity,networks,folder,state,save,log)
        neutron=net.Neutron(api.client,api.urls['network'],api.token)
        placement=net.wait_ha(neutron,networks['router']);active=next(a['host'] for a in placement if a['ha_state']=='active')
        if baseline!=access.input_hashes(config,inventory):raise RuntimeError('Deployment inputs changed during the VM exercise')
        log('VM/network summary:\n'+json.dumps({'VM':identity,'router_replicas':placement,'active_router_host':active,
            'external_USB_NIC':deploy.validation.config_step.USB[active.split('.')[0]],'verification':checks,
            'path_evidence':'Guest routes plus successful SSH/ping/metadata/DNS/HTTPS and current API placement. USB/VXLAN path is inferred from this topology; no packet capture or failover test.'},indent=2))
        log('RESULT: FIRST_VM_NETWORKING_VERIFIED')
        log('VM and floating IP remain running/allocated. Return report. Cinder volumes and failover are not tested.')
    return 0


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);mode=p.add_mutually_exclusive_group()
    mode.add_argument('--apply',action='store_true');mode.add_argument('--verify',action='store_true');args=p.parse_args(argv)
    if not args.apply and not args.verify:
        print('PLAN ONLY — no connections or files changed:\n'+json.dumps(PLAN,indent=2))
        print('Read Chapter D. --apply creates one VM port, one VM and one associated floating IP, then tests guest networking. --verify reads/tests the existing tracked VM only.');return 0
    deploy.validation.config_step.load_step('boot_r2d2','phase2-check.py').require_r2d2();deploy.tools.check_paths(ENV)
    if not sys.stdin.isatty():raise RuntimeError('Use an interactive R2d2 terminal for the guest key passphrase')
    if Path(sys.prefix)!=ENV:
        os.execve(str(ENV/'bin/python'),[str(ENV/'bin/python'),str(Path(__file__).resolve()),'--verify' if args.verify else '--apply'],deploy.tools.tool_environment())
    os.umask(0o077);reports=REPO/'reports';deploy.inv.private_dir(reports)
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-')
    fd,name=tempfile.mkstemp(prefix='phase4-boot-vm-'+stamp,suffix='.txt',dir=reports);print('Private report: '+name,flush=True);status=1
    with os.fdopen(fd,'w') as output:
        def log(line):print(line,flush=True);output.write(line+'\n');output.flush()
        log('Phase 4 Chapter D — '+('VERIFY existing tracked VM only' if args.verify else 'APPLY one VM/port/floating IP, then VERIFY')+'\nPlan:\n'+json.dumps(PLAN,indent=2))
        try:status=execute(log,args.verify)
        except (Exception,KeyboardInterrupt) as error:
            log('RESULT: FIRST_VM_NEEDS_REVIEW')
            log(str(error) if isinstance(error,RuntimeError) else 'Stopped ('+type(error).__name__+'); private details withheld.')
            log('Confirmed resources and pending-request state are retained. No automatic deletion, mutation retry, rebuild or repair. Return this report.')
    print('Read report: cat '+shlex.quote(name));return status

if __name__=='__main__':
    try:sys.exit(main())
    except (RuntimeError,OSError) as error:print('Stopped: '+str(error),file=sys.stderr);sys.exit(1)

import base64
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
import urllib.error
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def load(name,file):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/file);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
step=load('boot_test','phase4-boot-vm.py');guest=load('guest_test','phase4-guest-check.py')
NETWORKS={'private_network':'private-net','private_subnet':'private-subnet','external_network':'external-net','external_subnet':'external-subnet','router':'router-id'}
PREPARED={'image':'image-id','flavor':'flavor-id','key':'lab-vm-key','group':'group-id'}


class API:
    def __init__(self):
        self.ports=[];self.servers=[];self.floating=[];self.writes=[];self.failure=None;self.hash_bad=False
        self.limits={'maxTotalInstances':10,'totalInstancesUsed':0,'maxTotalRAMSize':51200,'totalRAMUsed':0,'maxTotalCores':20,'totalCoresUsed':0}
    def list(self,service,path,key):
        return copy.deepcopy({'ports':self.ports,'servers':self.servers,'floatingips':self.floating}[key])
    def get(self,service,path):
        if path=='limits':return {'limits':{'absolute':copy.deepcopy(self.limits)}}
        kind,identifier=path.rsplit('/',1)
        entries,wrapper={'v2.0/ports':(self.ports,'port'),'servers':(self.servers,'server'),'v2.0/floatingips':(self.floating,'floatingip')}[kind]
        return {wrapper:copy.deepcopy(next(o for o in entries if o['id']==identifier))}
    def write(self,service,method,path,body,expected):
        self.writes.append((service,method,path,copy.deepcopy(body),expected))
        if self.failure==path:raise RuntimeError('Fixture unconfirmed request')
        if path=='v2.0/ports':
            obj=copy.deepcopy(body['port']);obj.update(id='port-id',status='DOWN',device_id='',device_owner='',mac_address='fa:16:3e:01:02:03',allowed_address_pairs=[])
            obj['fixed_ips'][0]['ip_address']='10.10.10.8';self.ports.append(obj);return {'port':copy.deepcopy(obj)}
        if path=='servers':
            wanted=body['server'];obj={'id':'server-id','name':wanted['name'],'tenant_id':'project-id','user_id':'user-id','key_name':wanted['key_name'],
                'metadata':copy.deepcopy(wanted['metadata']),'image':{'id':wanted['imageRef']},'flavor':{'id':wanted['flavorRef']},
                'config_drive':'','os-extended-volumes:volumes_attached':[],'status':'ACTIVE','OS-EXT-SRV-ATTR:host':'node2'}
            self.servers.append(obj);self.ports[0].update(device_id='server-id',device_owner='compute:nova',status='ACTIVE')
            return {'server':{'id':'server-id','adminPass':'NEVER-LOG-THIS'}}
        if path=='v2.0/floatingips':
            obj=copy.deepcopy(body['floatingip']);obj.pop('subnet_id');obj.update(id='floating-id',router_id='router-id',floating_ip_address='192.168.0.155',status='ACTIVE')
            self.floating.append(obj);return {'floatingip':copy.deepcopy(obj)}
        raise AssertionError(path)


class ProvisionTests(unittest.TestCase):
    def provision(self,api=None,state=None,verify=False,save=None):
        api=api or API();state=state if state is not None else {'ids':{}};logs=[]
        identity=step.provision(api,state,'project-id','user-id',NETWORKS,PREPARED,save or (lambda s:None),logs.append,verify)
        return api,state,identity,logs
    def test_one_vm_one_port_one_floating_ip_and_safe_rerun(self):
        api,state,identity,logs=self.provision()
        self.assertEqual([w[2] for w in api.writes],['v2.0/ports','servers','v2.0/floatingips'])
        self.assertEqual(identity['compute_host'],'node2');self.assertEqual(identity['private_ip'],'10.10.10.8');self.assertEqual(identity['floating_ip'],'192.168.0.155')
        request=api.writes[1][3]['server'];self.assertEqual(request['networks'],[{'port':'port-id'}]);self.assertEqual(request['min_count'],request['max_count']);self.assertEqual(request['max_count'],1)
        self.assertFalse(request['config_drive']);self.assertNotIn('availability_zone',request);self.assertNotIn('block_device_mapping_v2',request)
        self.assertEqual(api.writes[0][3]['port']['security_groups'],['group-id'])
        self.assertNotIn('NEVER-LOG',json.dumps(state)+'\n'.join(logs))
        count=len(api.writes);self.provision(api,state);self.assertEqual(len(api.writes),count)
    def test_verify_never_creates_and_requires_complete_state(self):
        api=API()
        with self.assertRaisesRegex(RuntimeError,'requires all three'):self.provision(api,verify=True)
        self.assertEqual(api.writes,[])
        api,state,_,_=self.provision();count=len(api.writes);self.provision(api,state,True);self.assertEqual(len(api.writes),count)
    def test_conflicting_vm_or_port_blocks_every_write(self):
        for kind in ('port','server'):
            api=API();items=api.ports if kind=='port' else api.servers
            items.append({'id':'other','name':step.PLAN['port_name' if kind=='port' else 'server_name']})
            with self.subTest(kind=kind),self.assertRaisesRegex(RuntimeError,'Untracked'):self.provision(api)
            self.assertEqual(api.writes,[])
    def test_confirmed_partial_port_is_reused(self):
        api,state,_,_=self.provision();state['ids']={'port':'port-id'};api.servers=[];api.floating=[];api.ports[0].update(device_id='',device_owner='',status='DOWN');api.writes=[]
        self.provision(api,state);self.assertEqual([w[2] for w in api.writes],['servers','v2.0/floatingips'])
    def test_uncertain_request_persists_pending_and_prevents_duplicate(self):
        for path,label in [('v2.0/ports','port'),('servers','server'),('v2.0/floatingips','floating_ip')]:
            api=API();api.failure=path;state={'ids':{}};snapshots=[]
            with self.subTest(path=path),self.assertRaisesRegex(RuntimeError,'unconfirmed'):self.provision(api,state,save=lambda s:snapshots.append(copy.deepcopy(s)))
            self.assertEqual(state['pending'],label);self.assertEqual(snapshots[-1]['pending'],label);count=len(api.writes)
            with self.assertRaisesRegex(RuntimeError,'uncertain'):self.provision(api,state)
            self.assertEqual(len(api.writes),count)
    def test_changed_image_security_group_or_port_ownership_never_rewritten(self):
        for change in ('image','security','owner'):
            api,state,_,_=self.provision();count=len(api.writes)
            if change=='image':api.servers[0]['image']['id']='other'
            if change=='security':api.ports[0]['security_groups']=['default']
            if change=='owner':api.ports[0]['device_id']='other'
            with self.subTest(change=change),self.assertRaises(RuntimeError):self.provision(api,state)
            self.assertEqual(len(api.writes),count)
    def test_foreign_floating_ip_and_wrong_pool_rejected(self):
        api,state,_,_=self.provision();count=len(api.writes);api.floating[0]['floating_ip_address']='192.168.0.201'
        with self.assertRaisesRegex(RuntimeError,'outside'):self.provision(api,state)
        self.assertEqual(len(api.writes),count)
        api.floating[0]['floating_ip_address']='192.168.0.155';api.floating[0]['router_id']='other-router'
        with self.assertRaisesRegex(RuntimeError,'association'):self.provision(api,state)
    def test_build_error_retains_server_and_does_not_allocate_floating_ip(self):
        api,state,_,_=self.provision();state['ids'].pop('floating_ip');api.floating=[];api.servers[0]['status']='ERROR';api.writes=[]
        with self.assertRaisesRegex(RuntimeError,'ACTIVE'):self.provision(api,state)
        self.assertEqual(api.writes,[])
    def test_quota_failure_precedes_any_creation(self):
        for key,value in [('maxTotalInstances',0),('maxTotalRAMSize',1024),('maxTotalCores',0)]:
            api=API();api.limits[key]=value
            with self.subTest(key=key),self.assertRaisesRegex(RuntimeError,'quota'):self.provision(api)
            self.assertEqual(api.writes,[])
        api=API();api.limits={k:-1 if k.startswith('max') else 0 for k in api.limits};self.provision(api)
    def test_default_plan_does_not_authenticate_or_write(self):
        with patch.object(step,'execute',side_effect=AssertionError('no execution')),contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(step.main([]),0)
        self.assertIn('PLAN ONLY',out.getvalue())


class TrustTests(unittest.TestCase):
    def key(self,folder):
        path=Path(folder)/'host';subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(path)],check=True)
        return ' '.join(Path(str(path)+'.pub').read_text().split()[:2]),path
    def test_fingerprint_matches_real_ssh_keygen_and_console_formats(self):
        with tempfile.TemporaryDirectory() as folder:
            public,path=self.key(folder);fingerprint=step.key_fingerprint(public)
            output=subprocess.run(['ssh-keygen','-l','-E','sha256','-f',str(path)+'.pub'],capture_output=True,text=True,check=True).stdout
            self.assertIn(fingerprint,output)
            self.assertEqual(step.console_fingerprints(output),{fingerprint})
            self.assertEqual(step.console_fingerprints('boot line\n'+public+' root@guest'),{fingerprint})
            with self.assertRaises(RuntimeError):step.key_fingerprint('ssh-ed25519 AAAA')
    def test_host_pin_requires_console_match_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);public,_=self.key(folder);fingerprint=step.key_fingerprint(public)
            class Console:
                def write(self,*args):return {'output':'256 '+fingerprint+' root@guest (ED25519)'}
            scan=SimpleNamespace(stdout='192.168.0.155 '+public+'\n',returncode=0)
            state={'ids':{}}
            with patch.object(step.subprocess,'run',return_value=scan):
                path=step.pin_guest(Console(),'server-id','192.168.0.155',root,state,lambda s:None,lambda l:None)
                self.assertEqual(state['host_fingerprint'],fingerprint);self.assertIn(public,path.read_text())
                path.write_text('unrelated entry')
                with self.assertRaisesRegex(RuntimeError,'differs'):step.pin_guest(Console(),'server-id','192.168.0.155',root,state,lambda s:None,lambda l:None)
    def test_mismatched_console_and_scanned_key_block_login(self):
        with tempfile.TemporaryDirectory() as folder:
            public,_=self.key(folder)
            class Console:
                def write(self,*args):return {'output':'256 SHA256:DIFFERENT root@guest (ED25519)'}
            with patch.object(step.subprocess,'run',return_value=SimpleNamespace(stdout='192.168.0.155 '+public+'\n',returncode=0)):
                with self.assertRaisesRegex(RuntimeError,'does not match'):step.pin_guest(Console(),'server-id','192.168.0.155',Path(folder),{'ids':{}},lambda s:None,lambda l:None)
            self.assertFalse((Path(folder)/'lab-vm-01-known-hosts').exists())
    def test_ssh_uses_selected_key_strict_pins_and_no_agent_forwarding(self):
        command=step.ssh_command('192.168.0.155',Path('/private/known'))
        for required in ['BatchMode=yes','StrictHostKeyChecking=yes','ForwardAgent=no','IdentitiesOnly=yes','UserKnownHostsFile=/private/known','HostKeyAlgorithms=ssh-ed25519']:
            self.assertIn(required,command)
        self.assertIn(str(step.prep.KEY),command);self.assertNotIn('StrictHostKeyChecking=no',command)


class AgentAndInputTests(unittest.TestCase):
    def public(self):return 'ssh-ed25519 '+base64.b64encode(step.HOST_PREFIX+bytes(range(32))).decode()
    def test_loaded_selected_key_reused_without_agent_or_key_mutation(self):
        public=self.public();fingerprint=step.key_fingerprint(public);calls=[]
        def command(args,**kwargs):
            calls.append(args);return SimpleNamespace(returncode=0,stdout='256 '+fingerprint+' guest (ED25519)')
        with patch.object(step.subprocess,'run',side_effect=command):step.ensure_agent(public,lambda l:None)
        self.assertEqual(len(calls),2);self.assertTrue(all(a[:2]==['ssh-add','-l'] for a in calls))
    def test_new_agent_and_selected_key_unlock_are_bounded_and_local(self):
        public=self.public();fingerprint=step.key_fingerprint(public);calls=[]
        responses=[SimpleNamespace(returncode=2,stdout=''),SimpleNamespace(returncode=0,stdout='SSH_AUTH_SOCK=/tmp/test-agent; export SSH_AUTH_SOCK;\nSSH_AGENT_PID=123; export SSH_AGENT_PID;'),SimpleNamespace(returncode=0,stdout=''),SimpleNamespace(returncode=0,stdout='256 '+fingerprint+' guest (ED25519)')]
        def command(args,**kwargs):calls.append(args);return responses.pop(0)
        with patch.dict(step.os.environ,{},clear=True),patch.object(step.subprocess,'run',side_effect=command):
            step.ensure_agent(public,lambda l:None);self.assertEqual(step.os.environ['SSH_AUTH_SOCK'],'/tmp/test-agent')
        self.assertEqual(calls[2],['ssh-add','-t','3600',str(step.prep.KEY)])
        self.assertFalse(any('ssh-keygen' in a for a in calls))
    def test_foreign_chapter_identity_rejected_before_any_cloud_operation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            networks={'plan':step.net.PLAN,'project_id':'foreign-project','api':'network-api','ids':NETWORKS}
            prepared={'plan':step.prep.PLAN,'project_id':'project-id','user_id':'user-id','apis':{'network':'network-api'},'ids':PREPARED}
            for name,value in [('networks.json',networks),('vm-preparation.json',prepared)]:
                path=root/name;path.write_text(json.dumps(value));path.chmod(0o600)
            api=SimpleNamespace(urls={'network':'network-api'})
            with self.assertRaisesRegex(RuntimeError,'Chapter B identity'):step.load_inputs(api,'project-id','user-id',root)


class TransportTests(unittest.TestCase):
    def api(self,opener):
        return step.API(SimpleNamespace(opener=opener),{'compute':'https://192.168.0.151:8774/v2.1','network':'https://192.168.0.151:9696'},'SECRET-TOKEN')
    def test_delete_reboot_rebuild_and_multiple_vm_requests_blocked(self):
        api=self.api(None)
        for service,method,path,body in [('compute','DELETE','servers/id',{}),('compute','POST','servers/id/action',{'reboot':{'type':'HARD'}}),
            ('compute','POST','servers/id/action',{'rebuild':{}}),('compute','POST','servers',{'server':{'name':'lab-vm-01','min_count':1,'max_count':2}})]:
            with self.subTest(path=path),self.assertRaises(RuntimeError):api.write(service,method,path,body,202)
    def test_only_console_read_action_allowed_and_errors_hide_secrets(self):
        class Opener:
            def open(self,*a,**k):raise urllib.error.HTTPError('SECRET-TOKEN',403,'SECRET-TOKEN',{},io.BytesIO(b'adminPass=SECRET'))
        with self.assertRaises(RuntimeError) as caught:self.api(Opener()).write('compute','POST','servers/id/action',{'os-getConsoleOutput':{'length':1000}},200)
        self.assertIn('403',str(caught.exception));self.assertNotIn('SECRET',str(caught.exception))


class GuestEvidenceTests(unittest.TestCase):
    def evidence(self):
        expected={'mac':'fa:16:3e:01:02:03','private_ip':'10.10.10.8','mtu':1450,'gateway':'10.10.10.1','dns':'192.168.0.1','dhcp_server_ips':['10.10.10.2','10.10.10.3']}
        links=[{'ifname':'ens3','address':expected['mac'],'mtu':1450,'addr_info':[{'local':'10.10.10.8','prefixlen':24}]}]
        routes=[{'dst':'default','dev':'ens3','gateway':'10.10.10.1'}]
        lease=guest.lease_values('# networkd snapshot\nADDRESS=10.10.10.8\nSERVER_ADDRESS=10.10.10.2\nROUTER=10.10.10.1\nDNS=192.168.0.1\n')
        return expected,links,routes,lease
    def test_ip_presence_alone_does_not_pass_dhcp_or_route_checks(self):
        expected,links,routes,lease=self.evidence();name,checks=guest.address_checks(links,routes,expected,lease)
        self.assertEqual(name,'ens3');self.assertTrue(all(checks.values()))
        _,checks=guest.address_checks(links,[],expected,{})
        self.assertTrue(checks['private_address_matches_Neutron']);self.assertFalse(checks['DHCP_lease_address_matches']);self.assertFalse(checks['default_route_uses_private_gateway'])
    def test_wrong_dhcp_server_and_mtu_detected_without_guessing_interface_name(self):
        expected,links,routes,lease=self.evidence();links[0]['ifname']='enp1s0';routes[0]['dev']='enp1s0';lease['SERVER_ADDRESS']='192.168.0.1';links[0]['mtu']=1500
        name,checks=guest.address_checks(links,routes,expected,lease)
        self.assertEqual(name,'enp1s0');self.assertFalse(checks['DHCP_server_matches_Neutron']);self.assertFalse(checks['interface_MTU_matches_network'])
    def test_ambiguous_mac_and_duplicate_lease_keys_rejected(self):
        expected,links,routes,lease=self.evidence()
        with self.assertRaises(RuntimeError):guest.address_checks(links+links,routes,expected,lease)
        with self.assertRaises(RuntimeError):guest.lease_values('ADDRESS=10.10.10.8\nADDRESS=10.10.10.9')

class FullGuestCheckTests(unittest.TestCase):
    def simulate(self,metadata_id='server-id',https_error=False):
        expected,links,routes,lease=GuestEvidenceTests().evidence();expected['server_id']='server-id'
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            files={'etc/os-release':'ID=ubuntu\nVERSION_ID="24.04"\n','sys/class/net/ens3/ifindex':'2',
                   'run/systemd/netif/leases/2':'\n'.join(k+'='+v for k,v in lease.items())}
            for name,value in files.items():
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value)
            def local_path(*parts):return root/str(Path(*parts)).lstrip('/')
            commands=[];urls=[]
            def read(args,timeout=20):
                commands.append(args)
                if args[:3]==['ip','-j','address']:return json.dumps(links)
                if args[:4]==['ip','-j','-4','route']:return json.dumps(routes)
                if args[0]=='resolvectl':return 'Link 2 (ens3): 192.168.0.1'
                if args[0]=='ping':return '3 packets transmitted, 3 received'
                if args[0]=='timeout':return '203.0.113.1 STREAM cloud-images.ubuntu.com'
                raise AssertionError(args)
            class Response:
                status=200
                def __init__(self,body):self.body=body
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def read(self,size):return self.body[:size]
            class Opener:
                def open(self,url,timeout):
                    urls.append(url)
                    if url.startswith('http://169.254.169.254/'):return Response(json.dumps({'uuid':metadata_id,'public_keys':{'key':'not logged'}}).encode())
                    if https_error:raise OSError('certificate test failure')
                    return Response(b'Ubuntu image index')
            cloud=SimpleNamespace(returncode=0,stdout=json.dumps({'status':'done','extended_status':'done'}))
            with patch.object(guest,'Path',side_effect=local_path),patch.object(guest.os,'geteuid',return_value=0),patch.object(guest,'run',side_effect=read),patch.object(guest.subprocess,'run',return_value=cloud),patch.object(guest.urllib.request,'build_opener',return_value=Opener()):
                result=guest.collect(expected)
            return result,commands,urls
    def test_complete_guest_evidence_is_reported_without_metadata_dump(self):
        result,commands,urls=self.simulate()
        self.assertTrue(result['passed']);self.assertTrue(result['checks']['metadata_matches_Nova_instance'])
        self.assertTrue(result['checks']['outbound_HTTPS_certificate_and_response'])
        self.assertNotIn('not logged',json.dumps(result));self.assertEqual(len(urls),2)
        self.assertTrue(all(c[0] in ('ip','resolvectl','ping','timeout') for c in commands))
    def test_wrong_instance_identity_or_failed_https_cannot_pass(self):
        result,_,_=self.simulate(metadata_id='different-instance');self.assertFalse(result['passed']);self.assertFalse(result['checks']['metadata_matches_Nova_instance'])
        result,_,_=self.simulate(https_error=True);self.assertFalse(result['passed']);self.assertIn('error',result)


if __name__=='__main__':unittest.main()

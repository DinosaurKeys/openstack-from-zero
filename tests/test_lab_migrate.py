import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import urllib.error
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('migration_menu_test',ROOT/'scripts/lab-migrate.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
VM='0ef50722-c056-49fa-8f9e-05e926a8b41d';VOL='eebabffa-2a1b-4ea5-bdd7-62082f66df77'
MIG='79cac35a-6f30-4466-b470-bdcde2cd63fa';PROJECT='1235d69fbfd24c0e9b1c30aaa63d29b0'


def server(host='node1',status='ACTIVE'):
    return {'id':VM,'name':'custom VM name','tenant_id':PROJECT,'status':status,'OS-EXT-SRV-ATTR:host':host,
      'OS-EXT-STS:task_state':None,'image':{'id':'image-id'},'flavor':{'vcpus':1,'ram':2048,'disk':10},
      'addresses':{'private':[{'addr':'10.10.10.101'}]},'os-extended-volumes:volumes_attached':[{'id':VOL}]}
def disk(host='node3@lvm-1#lvm-1',status='in-use'):
    return {'id':VOL,'name':'my data','os-vol-tenant-attr:tenant_id':PROJECT,'os-vol-host-attr:host':host,
      'size':1,'status':status,'multiattach':False,'encrypted':False,'bootable':'false','replication_status':'disabled',
      'attachments':[{'server_id':VM}] if status=='in-use' else [],'migration_status':None}
def pool(host):return {'name':host,'capabilities':{'volume_backend_name':'lvm-1','storage_protocol':'iSCSI','free_capacity_gb':50}}


class API:
    def __init__(self):
        self.server=server();self.volume=disk();self.history=[];self.actions=[];self.arm_plan=None;self.fail=None
        self.pools=[pool('node3@lvm-1#lvm-1'),pool('node1@lvm-1#lvm-1'),pool('node2@lvm-1#lvm-1')]
    def get(self,service,path):
        if path=='servers/'+VM:return {'server':copy.deepcopy(self.server)}
        if path=='volumes/'+VOL:return {'volume':copy.deepcopy(self.volume)}
        if path.startswith('os-migrations?'):return {'migrations':copy.deepcopy(self.history)}
        if path=='scheduler-stats/get_pools?detail=True':return {'pools':copy.deepcopy(self.pools)}
        raise AssertionError((service,path))
    def list(self,service,path,key):
        if key=='snapshots':return []
        if key=='servers':return [copy.deepcopy(self.server)]
        if key=='volumes':return [copy.deepcopy(self.volume)]
        if key=='services':return [{'host':n,'binary':'nova-compute','state':'up','status':'enabled'} for n in m.NODES]
        raise AssertionError((service,path,key))
    def arm(self,plan):self.arm_plan=plan
    def mutate(self,step):
        self.actions.append(step)
        if self.fail:raise self.fail
        mode=self.arm_plan['mode']
        if mode in ('volume-live','volume-offline'):
            self.volume['os-vol-host-attr:host']=self.arm_plan['destination'];self.volume['migration_status']='success'
        elif step=='move':
            self.server['OS-EXT-SRV-ATTR:host']=self.arm_plan['destination']
            self.server['status']='VERIFY_RESIZE' if mode=='cold' else 'ACTIVE'
            self.history=[{'uuid':MIG,'instance_uuid':VM,'source_compute':self.arm_plan['source'],'dest_compute':self.arm_plan['destination'],
              'status':'finished' if mode=='cold' else 'completed','migration_type':'migration' if mode=='cold' else 'live-migration'}]
        else:
            self.server['status']=self.arm_plan['before']['status'];self.history[0]['status']='confirmed'


class Nodes:
    def __init__(self,api):self.api=api;self.paused=False
    def call(self,node,action,payload):
        if action=='running':
            running=node==self.api.server['OS-EXT-SRV-ATTR:host'] and self.api.server['status'] in ('ACTIVE','VERIFY_RESIZE')
            return {'node':node,'observed':True,'VM_running':running,'domain_state':'paused' if self.paused else 'running' if running else 'not active'}
        if action=='compare':return {'observed':True,'source_cpu_supported_by_destination_hypervisor':True}
        if action=='connection':return {'observed':True,'authenticated_libvirt_read':True}
        if action=='vnc':return {'observed':True,'graphics_rewrite_test_passed':True,'graphics_rewrite_wired':True}
        raise AssertionError(action)
    def all(self,payload):
        domain={'uuid':VM,'host_devices':0,'vcpus':'1','memory':{'value':str(2*1024**2),'unit':'KiB'},'disks':[{'type':'file','device':'disk','readonly':False}]}
        return {n:{'observed':True,'domain':domain,'containers':{},'nova_config':{'config_sha256':'a'*64,'connection_uri':'qemu+tcp://'+n+'/system'},
          'libvirt_version':'libvirt 10','instances_storage':{'container_path':'/var/lib/nova/instances','available_bytes':40*1024**3},
          'memory':{'MemAvailable_bytes':6*1024**3}} for n in m.NODES}


def compute_plan(api,mode='live',dest='node2'):
    return {'mode':mode,'server_id':VM,'vm_name':api.server['name'],'project_id':PROJECT,'source':api.server['OS-EXT-SRV-ATTR:host'],
      'destination':dest,'before':m.binding(api.server,{VOL:api.volume}),'before_migrations':[]}


class MenuTests(unittest.TestCase):
    def test_number_selection_cancel_invalid_input_and_terminal_sanitizing(self):
        answers=iter(['bad','9','2']);logs=[]
        self.assertEqual(m.choose('Pick',['a','b'],str,lambda _:next(answers),logs.append),'b')
        self.assertEqual(sum('Enter one' in line for line in logs),2)
        self.assertIsNone(m.choose('Pick',['a'],str,lambda _:'0',lambda _:None))
        self.assertNotIn('\x1b',m.clean('name\x1b[2J\nsecret'))

    def test_generic_api_has_exact_live_cold_and_storage_bodies_and_no_repeat(self):
        plans=[compute_plan(API()),compute_plan(API(),'cold'),
          {'mode':'volume-live','volume_id':VOL,'source':'node3@lvm-1#lvm-1','destination':'node2@lvm-1#lvm-1'}]
        for plan in plans:
            requests=[]
            def opened(request,timeout):
                requests.append((request,timeout))
                return contextlib.nullcontext(SimpleNamespace(status=202,headers={'X-Openstack-Request-Id':'req-865d0c31-bebe-4b1b-a8bd-a46dae90485d'},read=lambda _:b''))
            original=SimpleNamespace(client=SimpleNamespace(opener=SimpleNamespace(open=opened)),
              urls={'compute':'https://192.168.0.151:8774/v2.1','block-storage':'https://192.168.0.151:8776/v3'},token='hidden')
            client=m.API(original);client.arm(plan)
            client.mutate('move')
            request,timeout=requests[0];body=json.loads(request.data)
            self.assertEqual(request.method,'POST')
            self.assertTrue(request.full_url.endswith(('volumes/'+VOL+'/action') if plan['mode']=='volume-live' else 'servers/'+VM+'/action'))
            headers={k.lower():v for k,v in request.header_items()}
            if plan['mode']=='live':
                self.assertEqual(body,{'os-migrateLive':{'host':'node2','block_migration':True}})
                self.assertEqual(headers['openstack-api-version'],'compute 2.30')
                self.assertEqual(timeout,180)
            if plan['mode']=='cold':
                self.assertEqual(body,{'migrate':{'host':'node2'}})
                self.assertEqual(headers['openstack-api-version'],'compute 2.79')
            if plan['mode']=='volume-live':self.assertEqual(body,{'os-migrate_volume':{'host':'node2@lvm-1#lvm-1','force_host_copy':False,'lock_volume':False}})
            self.assertEqual(client.last_action_request_id,'req-865d0c31-bebe-4b1b-a8bd-a46dae90485d')
            with self.assertRaises(RuntimeError):client.mutate('move')
            self.assertEqual(len(requests),1)
            for method in ('DELETE','PUT','POST'):
                with self.assertRaises(RuntimeError):client.request('compute',method,'servers/'+VM+'/action',{'os-resetState':{'state':'active'}})
            with self.assertRaises(RuntimeError):client.write('compute','PUT','servers/'+VM,{'name':'rename'},200)

    def test_live_transport_cannot_bypass_approval_force_destination_or_repeat(self):
        original=SimpleNamespace(client=SimpleNamespace(opener=None),urls={},token='hidden')
        for read_only in (True,False):
            client=m.API(original,read_only=read_only)
            with self.assertRaisesRegex(RuntimeError,'Transport refuses'):
                client._send('compute','POST','servers/'+VM+'/action',{'os-migrateLive':{'host':'node3','force':True}},202,'2.30')

    def test_reads_keep_modern_version_and_lost_post_does_not_reuse_a_get_request_id(self):
        requests=[];request_id='req-865d0c31-bebe-4b1b-a8bd-a46dae90485d'
        def opened(request,timeout):
            requests.append(request)
            if request.method=='POST':raise TimeoutError('fixture transport timeout')
            return contextlib.nullcontext(SimpleNamespace(status=200,headers={'X-Openstack-Request-Id':request_id},read=lambda _:b'{}'))
        original=SimpleNamespace(client=SimpleNamespace(opener=SimpleNamespace(open=opened)),urls={'compute':'https://192.168.0.151:8774/v2.1'},token='hidden')
        client=m.API(original);client.get('compute','servers/'+VM)
        self.assertEqual({k.lower():v for k,v in requests[0].header_items()}['openstack-api-version'],'compute 2.79')
        self.assertEqual(client.last_response_request_id,request_id)
        client.arm(compute_plan(API()))
        with self.assertRaisesRegex(RuntimeError,'outcome uncertain'):client.mutate('move')
        self.assertIsNone(client.last_action_request_id);self.assertIsNone(client.last_action_http_status)
        with self.assertRaises(RuntimeError):client.mutate('move')
        self.assertEqual(len(requests),2)

    def test_http_rejection_retains_request_id_and_redacts_server_message(self):
        request_id='req-865d0c31-bebe-4b1b-a8bd-a46dae90485d'
        def opened(request,timeout):
            raise urllib.error.HTTPError(request.full_url,400,'Bad Request',{'X-Openstack-Request-Id':request_id},io.BytesIO(b'{"badRequest":{"message":"password=private"}}'))
        original=SimpleNamespace(client=SimpleNamespace(opener=SimpleNamespace(open=opened)),urls={'compute':'https://192.168.0.151:8774/v2.1'},token='hidden')
        client=m.API(original);client.arm(compute_plan(API()))
        with self.assertRaisesRegex(RuntimeError,'HTTP 400') as error:client.mutate('move')
        self.assertNotIn('private',str(error.exception));self.assertEqual(client.last_action_request_id,request_id)
        with self.assertRaises(RuntimeError):client.mutate('move')

    def test_source_filters_with_no_vm_return_to_menu_and_accept_node_names(self):
        for empty in ('3','4','node2','node3'):
            api=API();answers=iter(['1',empty,'node1','1','node3']);logs=[]
            with tempfile.TemporaryDirectory() as folder,patch.object(m.getpass,'getpass',return_value='hidden'),patch.object(m.inspection,'Nodes',return_value=Nodes(api)),patch.object(m,'compute_preflight',side_effect=lambda a,s,p,d,mode,n,l:compute_plan(a,mode,d)):
                result=m.run_menu(api,PROJECT,{},Path(folder),logs.append,lambda _:self.fail('preview wrote a record'),lambda _:next(answers),True,lambda *_:{'boots':'unchanged'})
            self.assertEqual(result,0);self.assertTrue(any('No VM currently lives on' in line for line in logs));self.assertIn('Destination: node3',logs)
            self.assertFalse(api.actions)

    def test_all_four_operation_previews_handle_current_node3_vm_or_detached_volume(self):
        for mode,choices in [('live',['1','node3','1','node2']),('cold',['2','node3','1','node2']),
          ('volume-live',['3','node3','1','1','2']),('volume-offline',['4','1','2'])]:
            with self.subTest(mode=mode):
                api=API();api.server=server('node3')
                if mode=='volume-offline':api.volume=disk(status='available')
                answers=iter(choices);logs=[]
                with tempfile.TemporaryDirectory() as folder,patch.object(m.getpass,'getpass',return_value='hidden'),patch.object(m.inspection,'Nodes',return_value=Nodes(api)),patch.object(m,'compute_preflight',side_effect=lambda a,s,p,d,kind,n,l:compute_plan(a,kind,d)):
                    code=m.run_menu(api,PROJECT,{},Path(folder),logs.append,lambda _:self.fail('Preview wrote state'),lambda _:next(answers),True,lambda *_:{'boots':'unchanged'})
                self.assertEqual(code,0);self.assertFalse(api.actions)
                self.assertIn('RESULT: MIGRATION_PLAN_ONLY — no migration requested',logs)

    def test_unavailable_host_reports_sudo_reason_before_api_or_container_claims(self):
        hosts={n:{'observed':True} for n in m.NODES};hosts['node1']={'observed':False,'error':'Sudo authentication failed'}
        with patch.object(m.health,'host_observe',side_effect=lambda n,*_:hosts[n]),patch.object(m.health,'api_checks',side_effect=AssertionError('No API checks needed')):
            with self.assertRaisesRegex(RuntimeError,'node1: Sudo authentication failed'):m.cluster({},dict.fromkeys(m.NODES,'hidden'),lambda _:None)

    def test_node3_to_node2_live_menu_uses_one_wire_action_and_retains_data_backend(self):
        backing=API();backing.server=server('node3');actions=[];states=[];logs=[]
        def location(url):
            service='compute' if '/v2.1/' in url else 'block-storage'
            return service,url.split('/v2.1/' if service=='compute' else '/v3/',1)[1]
        def opened(request,timeout):
            service,path=location(request.full_url)
            if request.method=='POST':
                body=json.loads(request.data);actions.append((request,body))
                backing.arm_plan=compute_plan(backing,dest=body['os-migrateLive']['host']);backing.mutate('move')
                return contextlib.nullcontext(SimpleNamespace(status=202,headers={'X-Openstack-Request-Id':'req-865d0c31-bebe-4b1b-a8bd-a46dae90485d'},read=lambda _:b''))
            value=backing.get(service,path)
            return contextlib.nullcontext(SimpleNamespace(status=200,headers={},read=lambda _:json.dumps(value).encode()))
        def listed(url,token):
            service,path=location(url)
            key='servers' if path.startswith('servers/detail') else 'services'
            return {key:backing.list(service,path,key)},None
        original=SimpleNamespace(client=SimpleNamespace(opener=SimpleNamespace(open=opened),request=listed),
          urls={'compute':'https://192.168.0.151:8774/v2.1','block-storage':'https://192.168.0.151:8776/v3'},token='hidden')
        client=m.API(original);answers=iter(['1','node3','1','node2','MIGRATE'])
        with tempfile.TemporaryDirectory() as folder,patch.object(m,'REPO',Path(folder)),\
             patch.object(m.getpass,'getpass',return_value='hidden'),patch.object(m.inspection,'Nodes',return_value=Nodes(backing)),\
             patch.object(m.inspection,'capacity',return_value={}),patch.object(m.move,'placement_room'):
            result=m.run_menu(client,PROJECT,{},Path(folder),logs.append,lambda s:states.append(copy.deepcopy(s)),
              lambda _:next(answers),health_check=lambda *_:{'boots':'stable'})
        self.assertEqual(result,0);self.assertEqual(len(actions),1);request,body=actions[0]
        self.assertEqual(body,{'os-migrateLive':{'host':'node2','block_migration':True}})
        self.assertEqual({k.lower():v for k,v in request.header_items()}['openstack-api-version'],'compute 2.30')
        self.assertEqual(backing.server['OS-EXT-SRV-ATTR:host'],'node2')
        self.assertEqual(backing.volume['os-vol-host-attr:host'],'node3@lvm-1#lvm-1')
        self.assertEqual(states[0]['plan']['source'],'node3');self.assertTrue(states[0]['pending']);self.assertFalse(states[-1]['pending'])
        self.assertIn('Operation: LIVE VM + local OS disk',logs)
        self.assertIn('RESULT: MIGRATION_API_AND_HOST_CHECKS_PASSED',logs)

    def test_plan_client_cannot_authorize_mutation(self):
        api=m.API(SimpleNamespace(client=None,urls={},token='hidden'),read_only=True)
        with self.assertRaisesRegex(RuntimeError,'Plan-only'):api.arm(compute_plan(API()))
        with self.assertRaises(RuntimeError):api.mutate('move')

    def test_source_and_destination_are_not_hardcoded_node1_to_node2(self):
        api=API();api.server=server('node2');plan=compute_plan(api,dest='node3')
        self.assertEqual(plan['source'],'node2');self.assertIn('Destination: node3',m.plan_lines(plan))
        client=m.API(SimpleNamespace(client=None,urls={},token='hidden'));client.arm(plan)
        self.assertEqual(client.allowed['move'][3]['os-migrateLive']['host'],'node3')

    def test_old_or_new_pending_record_blocks_same_vm_without_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);path=folder/'move-vm-state.json';raw=json.dumps({'pending':True,'server_id':VM}).encode()
            path.write_bytes(raw);path.chmod(0o600)
            with self.assertRaisesRegex(RuntimeError,'preserved'):m.pending_guard(folder,server=VM)
            self.assertEqual(path.read_bytes(),raw)
            m.pending_guard(folder,server='11111111-1111-1111-1111-111111111111')

    def test_read_only_menu_changes_vm_source_and_destination_by_number(self):
        api=API();api.server=server('node2');answers=iter(['1','3','1','2'])
        # Operation live, filter node2, first VM, destination node3 (second remaining host).
        log=[];saves=[]
        with tempfile.TemporaryDirectory() as folder,patch.object(m.getpass,'getpass',return_value='hidden'),patch.object(m.inspection,'Nodes',return_value=Nodes(api)),patch.object(m,'compute_preflight',side_effect=lambda a,s,p,d,mode,n,l:compute_plan(a,mode,d)) as preflight:
            code=m.run_menu(api,PROJECT,{},Path(folder),log.append,saves.append,lambda _:next(answers),True,lambda *_:{'boots':'unchanged'})
        self.assertEqual(code,0);self.assertEqual(preflight.call_args.args[3],'node3')
        self.assertIn('Detected VM source: node2',log);self.assertFalse(api.actions);self.assertFalse(saves)

    def test_vm_error_stops_before_sudo_or_any_move(self):
        api=API();api.server['status']='ERROR';answers=iter(['1','1','1'])
        with tempfile.TemporaryDirectory() as folder,patch.object(m.getpass,'getpass',side_effect=AssertionError('No sudo prompt')):
            with self.assertRaisesRegex(RuntimeError,'VM is ERROR'):m.run_menu(api,PROJECT,{},Path(folder),lambda _:None,lambda _:None,lambda _:next(answers))
        self.assertFalse(api.actions)

    def test_shutdown_mode_is_not_live_and_live_requires_active(self):
        vm=server(status='SHUTOFF');m.vm_eligible(vm,'cold')
        with self.assertRaises(RuntimeError):m.vm_eligible(vm,'live')
        with self.assertRaises(RuntimeError):m.vm_eligible(server(status='ERROR'),'cold')
        self.assertTrue(any('downtime' in line for line in m.plan_lines(compute_plan(API(),'cold'))))

    def test_paused_actual_runtime_cannot_be_treated_as_powered_off(self):
        api=API();nodes=Nodes(api);nodes.paused=True
        with self.assertRaisesRegex(RuntimeError,'paused'):m.runtime(nodes,VM)

    def test_unavailable_nova_task_is_not_idle_or_verified_completion(self):
        api=API();plan=compute_plan(api);del api.server['OS-EXT-STS:task_state']
        for mode in ('live','cold'):
            with self.subTest(mode=mode),self.assertRaisesRegex(RuntimeError,'task state is unavailable'):
                m.vm_eligible(api.server,mode)
        api.server['OS-EXT-SRV-ATTR:host']=plan['destination']
        api.history=[{'uuid':MIG,'instance_uuid':VM,'source_compute':plan['source'],'dest_compute':plan['destination'],
          'status':'completed','migration_type':'live-migration'}]
        with self.assertRaisesRegex(RuntimeError,'completion is not verified'):m.wait_move(api,plan,lambda _:None)
        with self.assertRaisesRegex(RuntimeError,'idle VM is not verified'):m.verify_move(api,plan,Nodes(api))
        self.assertFalse(api.actions)

    def test_api_only_modes_do_not_initialize_node_ssh_or_request_sudo(self):
        for mode in ('--observe','--reconcile'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);(root/'private/phase4').mkdir(parents=True)
                reviewed=[]
                def run_records(menu,api,*args,**kwargs):
                    reviewed.append((api.read_only,kwargs['reconcile']));return 0
                original=SimpleNamespace(client=None,urls={},token='hidden')
                with patch.object(m,'REPO',root),patch.object(m,'ENV',Path(sys.prefix)),\
                     patch.object(m.sys.stdin,'isatty',return_value=True),\
                     patch.object(m.deploy.validation.config_step,'load_step',return_value=SimpleNamespace(require_r2d2=lambda:None)),\
                     patch.object(m.deploy.tools,'check_paths'),patch.object(m.deploy,'source_guard'),\
                     patch.object(m.deploy.tools,'tool_environment',return_value={}),\
                     patch.object(m.deploy.validation,'local_checks',return_value=(root/'inventory',root/'config')),\
                     patch.object(m.move.access,'input_hashes',return_value={}),\
                     patch.object(m.inv,'ensure_agent',side_effect=AssertionError('API-only review must not initialize node SSH')),\
                     patch.object(m.getpass,'getpass',side_effect=AssertionError('API-only review must not ask for sudo')),\
                     patch.object(m.prep,'authenticate',return_value=(original,PROJECT,None)),\
                     patch.object(m,'load',return_value=SimpleNamespace(run=run_records)),contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(m.main([mode]),0)
                self.assertEqual(reviewed,[(True,mode=='--reconcile')])

    def test_preflight_dynamic_flavor_capacity_and_cpu(self):
        api=API();nodes=Nodes(api)
        with patch.object(m.inspection,'capacity',return_value={}),patch.object(m.move,'placement_room') as room:
            plan=m.compute_preflight(api,api.server,PROJECT,'node3','live',nodes,lambda _:None)
        self.assertEqual(plan['destination'],'node3');room.assert_called_once_with({}, {'vcpus':1,'ram_mb':2048,'disk_gb':10})

    def test_actual_guest_capacity_must_match_flavor_snapshot(self):
        api=API();api.server['flavor']['ram']=1024
        with self.assertRaisesRegex(RuntimeError,'Actual VM RAM'):m.compute_preflight(api,api.server,PROJECT,'node3','live',Nodes(api),lambda _:None)

    def test_no_shared_volume_migration_when_grouped_encrypted_multiattach_or_snapshotted(self):
        for key,value in [('encrypted',True),('multiattach',True),('group_id','group'),('replication_status','enabled')]:
            api=API();api.volume[key]=value
            with self.assertRaises(RuntimeError):m.storage_preflight(api,api.volume,PROJECT,api.pools[1],'volume-live',api.server)
        api=API()
        with patch.object(api,'list',return_value=[{'volume_id':VOL}]),self.assertRaisesRegex(RuntimeError,'snapshots'):
            m.storage_preflight(api,api.volume,PROJECT,api.pools[1],'volume-live',api.server)

    def test_storage_live_and_offline_have_distinct_requirements(self):
        api=API();plan=m.storage_preflight(api,api.volume,PROJECT,api.pools[1],'volume-live',api.server)
        self.assertEqual(plan['source'],'node3@lvm-1#lvm-1');self.assertEqual(plan['server_id'],VM)
        api.volume=disk(status='available');plan=m.storage_preflight(api,api.volume,PROJECT,api.pools[1],'volume-offline')
        self.assertIsNone(plan['server_id'])
        with self.assertRaises(RuntimeError):m.storage_preflight(api,api.volume,PROJECT,api.pools[1],'volume-live',api.server)
        api.volume=disk();api.server['status']='SHUTOFF'
        with self.assertRaises(RuntimeError):m.storage_preflight(api,api.volume,PROJECT,api.pools[1],'volume-live',api.server)

    def test_changed_vm_or_volume_prevents_sending_request(self):
        api=API();plan=compute_plan(api);api.server['OS-EXT-SRV-ATTR:host']='node3'
        with self.assertRaises(RuntimeError):m.refresh(api,plan)
        api=API();plan=m.storage_preflight(api,api.volume,PROJECT,api.pools[1],'volume-live',api.server);api.volume['encrypted']=True
        with self.assertRaises(RuntimeError):m.refresh(api,plan)

    def run_move(self,api,plan,answers=None):
        saves=[];logs=[];answers=iter(answers or [True,True]);nodes=Nodes(api)
        original=api.mutate
        def mutate(step):
            self.assertTrue(saves[-1]['pending']);self.assertIn(step,saves[-1]['attempts']);return original(step)
        with patch.object(api,'mutate',side_effect=mutate):
            result=m.perform(api,plan,nodes,lambda v:saves.append(copy.deepcopy(v)),logs.append,lambda _:next(answers),lambda:None,lambda:None,lambda:None)
        return result,saves,logs

    def test_live_move_then_check_preserves_data_backend_and_addresses(self):
        api=API();plan=compute_plan(api);before=copy.deepcopy(api.volume)
        result,saves,logs=self.run_move(api,plan)
        self.assertTrue(result);self.assertEqual(api.actions,['move']);self.assertEqual(api.volume,before)
        self.assertFalse(saves[-1]['pending']);self.assertIn('RESULT: MIGRATION_API_AND_HOST_CHECKS_PASSED',logs)

    def test_cold_move_requires_separate_confirmation(self):
        api=API();result,saves,_=self.run_move(api,compute_plan(api,'cold'))
        self.assertTrue(result);self.assertEqual(api.actions,['move','confirm']);self.assertFalse(saves[-1]['pending'])

    def test_cold_move_of_a_shutoff_vm_does_not_request_a_power_action(self):
        api=API();api.server=server('node3','SHUTOFF');plan=compute_plan(api,'cold','node2');states=[]
        class StoppedNodes(Nodes):
            def call(self,node,action,payload):
                if action=='running':return {'node':node,'observed':True,'VM_running':False,'domain_state':'not active'}
                return super().call(node,action,payload)
        result=m.perform(api,plan,StoppedNodes(api),lambda s:states.append(copy.deepcopy(s)),lambda _:None,lambda _:True,lambda:None,lambda:None,lambda:None)
        self.assertTrue(result);self.assertEqual(api.actions,['move','confirm'])
        self.assertEqual(api.server['status'],'SHUTOFF');self.assertEqual(api.server['OS-EXT-SRV-ATTR:host'],'node2')
        self.assertFalse(states[-1]['pending']);self.assertEqual(api.volume['os-vol-host-attr:host'],'node3@lvm-1#lvm-1')

    def test_cold_move_declined_confirmation_is_preserved_not_reverted(self):
        api=API();result,saves,_=self.run_move(api,compute_plan(api,'cold'),[True,False])
        self.assertFalse(result);self.assertEqual(api.actions,['move']);self.assertEqual(api.server['status'],'VERIFY_RESIZE');self.assertTrue(saves[-1]['pending'])

    def test_live_data_volume_moves_without_changing_compute(self):
        api=API();plan=m.storage_preflight(api,api.volume,PROJECT,api.pools[1],'volume-live',api.server);before=copy.deepcopy(api.server)
        result,saves,_=self.run_move(api,plan)
        self.assertTrue(result);self.assertEqual(api.server,before);self.assertEqual(api.volume['id'],VOL)
        self.assertEqual(api.volume['os-vol-host-attr:host'],api.pools[1]['name']);self.assertFalse(saves[-1]['pending'])

    def test_no_approval_no_write_or_receipt(self):
        api=API();result,saves,_=self.run_move(api,compute_plan(api),[False])
        self.assertFalse(result);self.assertFalse(api.actions);self.assertFalse(saves)

    def test_detached_data_volume_move_preserves_compute_and_available_state(self):
        api=API();api.server=server('node3');api.volume=disk(status='available');states=[]
        plan=m.storage_preflight(api,api.volume,PROJECT,api.pools[1],'volume-offline')
        result=m.perform(api,plan,Nodes(api),lambda s:states.append(copy.deepcopy(s)),lambda _:None,lambda _:True,lambda:None,lambda:None,lambda:None)
        self.assertTrue(result);self.assertEqual(api.actions,['move']);self.assertFalse(states[-1]['pending'])
        self.assertEqual(api.volume['status'],'available');self.assertEqual(api.volume['attachments'],[])
        self.assertEqual(api.server['OS-EXT-SRV-ATTR:host'],'node3');self.assertEqual(api.volume['os-vol-host-attr:host'],plan['destination'])

    def test_lost_response_or_interrupt_keeps_pending_and_never_retries(self):
        for error in (RuntimeError('outcome unavailable'),KeyboardInterrupt()):
            api=API();api.fail=error;saves=[]
            with self.assertRaises(type(error)):
                m.perform(api,compute_plan(api),Nodes(api),lambda s:saves.append(copy.deepcopy(s)),lambda _:None,lambda _:True,lambda:None,lambda:None,lambda:None)
            self.assertEqual(api.actions,['move']);self.assertTrue(saves[-1]['pending'])

    def test_incomplete_async_state_is_not_success_and_timeout_does_not_cancel(self):
        api=API();plan=compute_plan(api)
        with patch.object(m.time,'monotonic',side_effect=[0,1201]),self.assertRaisesRegex(RuntimeError,'may still run'):
            m.wait_move(api,plan,lambda _:None)
        self.assertFalse(api.actions)

    def test_reported_nova_error_logs_snapshot_and_fault_before_stopping(self):
        api=API();plan=compute_plan(api);api.server['status']='ERROR';api.server['fault']={'code':500,'message':'MessagingTimeout'};logs=[]
        with self.assertRaisesRegex(RuntimeError,'Nova reports'):
            m.wait_move(api,plan,logs.append)
        self.assertIn('VM ERROR on node1; migration not observed yet',logs)
        self.assertIn('Nova fault: 500 MessagingTimeout',logs)
        self.assertFalse(api.actions)

    def test_request_identity_is_saved_after_post_and_preserved_on_failed_poll(self):
        api=API();api.last_action_request_id='req-865d0c31-bebe-4b1b-a8bd-a46dae90485d';states=[]
        with patch.object(m,'wait_move',side_effect=RuntimeError('Nova reports error')):
            with self.assertRaises(RuntimeError):m.perform(api,compute_plan(api),Nodes(api),lambda s:states.append(copy.deepcopy(s)),lambda _:None,lambda _:True,lambda:None,lambda:None,lambda:None)
        self.assertTrue(states[-1]['pending']);self.assertEqual(states[-1]['request_ids']['move'],api.last_action_request_id)
        self.assertEqual(states[-1]['live_microversion'],'2.30');self.assertEqual(api.actions,['move'])

    def test_history_pagination_and_duplicate_ids_are_not_silently_accepted(self):
        api=API();api.history=[{'uuid':MIG,'instance_uuid':VM},{'uuid':MIG,'instance_uuid':VM}]
        with self.assertRaisesRegex(RuntimeError,'ambiguous'):m.migration_history(api,VM)
        with patch.object(api,'get',return_value={'migrations':[],'migrations_links':[{'rel':'next','href':'ignored'}]}):
            with self.assertRaisesRegex(RuntimeError,'incomplete'):m.migration_history(api,VM)

    def test_transient_empty_migration_locations_are_not_invented_or_rejected_early(self):
        api=API();plan=compute_plan(api)
        api.history=[{'uuid':MIG,'instance_uuid':VM,'source_compute':None,'dest_compute':None,'status':'accepted','migration_type':'live-migration'}]
        server,item=m.compute_snapshot(api,plan)
        self.assertIsNone(item['source_compute']);self.assertEqual(server['status'],'ACTIVE')
        with patch.object(m.time,'monotonic',side_effect=[0,1201]),self.assertRaisesRegex(RuntimeError,'may still run'):
            m.wait_move(api,plan,lambda _:None)

    def test_cinder_backend_change_without_success_marker_does_not_pass(self):
        api=API();plan=m.storage_preflight(api,api.volume,PROJECT,api.pools[1],'volume-live',api.server)
        api.volume['os-vol-host-attr:host']=plan['destination']
        with patch.object(m.time,'monotonic',side_effect=[0,1201]),self.assertRaisesRegex(RuntimeError,'may still run'):
            m.wait_move(api,plan,lambda _:None)

    def test_nova_auto_confirm_during_prompt_does_not_send_duplicate_confirmation(self):
        api=API();plan=compute_plan(api,'cold');calls=[];saves=[]
        def approve(_):
            calls.append(True)
            if len(calls)==2:
                api.server['status']='ACTIVE';api.history[0]['status']='confirmed'
                return False
            return True
        self.assertTrue(m.perform(api,plan,Nodes(api),lambda s:saves.append(copy.deepcopy(s)),lambda _:None,approve,lambda:None,lambda:None,lambda:None))
        self.assertEqual(api.actions,['move']);self.assertFalse(saves[-1]['pending'])

    def test_no_host_power_state_reset_or_force_commands_in_menu_wrapper(self):
        subprocess.run(['bash','-n',str(ROOT/'scripts/lab-migrate.sh')],check=True)
        with contextlib.redirect_stdout(io.StringIO()),self.assertRaises(SystemExit) as result:m.main(['--help'])
        self.assertEqual(result.exception.code,0)


if __name__=='__main__':unittest.main()

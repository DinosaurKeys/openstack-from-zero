import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def load(name,file):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/file);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
step=load('move_test','phase5-move-vm.py');node=load('move_node_test','phase5-migration-node.py')
SERVER='0ef50722-c056-49fa-8f9e-05e926a8b41d';ID='12345678-abcd-4321-8321-0123456789ab';BOOT='a3c6cb8e-cba3-49d0-964b-cf131e1075c5';MIG='f1568275-f734-4bbc-867e-26a822d2cb0d'


def context(source='node1'):
    records={n:{'containers':{'nova_compute':{'id':str(i)*64}},'nova_config':{'config_sha256':str(i)*64,'vnc':{'server_listen':node.NODES[n]}},'libvirt_version':'10.0.0',
        'instances_storage':{'container_path':'/var/lib/nova/instances'},'domain':None,'observed':True} for i,n in enumerate(node.NODES,1)}
    records[source]['domain']={'machine':'pc-i440fx-noble','emulator':'/usr/bin/qemu-system-x86_64','virt_type':'kvm','host_devices':0,
        'memory':{'value':'2097152','unit':'KiB'},'vcpus':'1','vnc_listen':[node.NODES[source]],'disks':[{'target':'vda','device':'disk','type':'file','driver_type':'qcow2','readonly':False},{'target':'vdb','device':'disk','type':'block','driver_type':'raw','readonly':False}]}
    return {'source':source,'hashes':{'config':'same'},'project':'project','user':'user','api':SimpleNamespace(urls={'compute':'https://192.168.0.151:8774/v2.1'}),
        'vm':{'ids':{'server':SERVER}},'vstate':{'volume_id':ID,'filesystem_uuid':MIG,'sha256':'checksum'},
        'workload':{'private_ip':'10.10.10.101','floating_ip':'192.168.0.194','storage_backend':'node3@lvm-1#lvm-1'},
        'records':records,'summaries':{'node2':{key:True for key in ('inspection_complete','ram_covers_guest_plus_1GiB_review_margin','disk_covers_root_plus_2GiB_review_margin','source_guest_cpu_compatible','authenticated_libvirt_read_from_source','nova_compute_up_enabled','placement_capacity_collected')}},
        'env':{},'passwords':{},'ssh':['ssh','pinned-guest'],
        'services':({n:{'mariadb':{'id':str(i)*64},'baseline':{'facts':{'protected_config_hashes':{'host':'same'}}}} for i,n in enumerate(node.NODES,1)},BOOT,'node3')}


def state(ctx=None):
    ctx=ctx or context();services=ctx['services']
    return dict(step.workload_binding(ctx),plan=step.PLAN,plan_id=ID,guest_boot_id=BOOT,before_migration_ids=[],pending=True,attempted=True,
        nodes={n:step.node_identity(v) for n,v in ctx['records'].items()},services={'cluster_uuid':services[1],'api_owner':services[2],
        'db_ids':{n:v['mariadb']['id'] for n,v in services[0].items()},'protected':{n:v['baseline']['facts']['protected_config_hashes'] for n,v in services[0].items()}})


def row(status='completed'):
    return {'uuid':MIG,'instance_uuid':SERVER,'migration_type':'live-migration','source_compute':'node1','dest_compute':'node2','status':status}


class Response:
    status=202
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,*args):return b''


class ApiTests(unittest.TestCase):
    def api(self,allow=True):
        opener=SimpleNamespace(open=lambda *args,**kw:Response())
        return step.API(SimpleNamespace(opener=opener),{'compute':'https://192.168.0.151:8774/v2.1'},'PRIVATE_TOKEN',SERVER,'node2',allow)
    def test_one_exact_request_uses_microversion_no_force_or_disk_overcommit(self):
        api=self.api();captured=[]
        api.client.opener.open=lambda req,**kw:(captured.append(req) or Response())
        api.move()
        req=captured[0];self.assertEqual(req.get_method(),'POST');self.assertTrue(req.full_url.endswith('/servers/'+SERVER+'/action'))
        self.assertEqual(req.headers['Openstack-api-version'],'compute 2.79')
        self.assertEqual(json.loads(req.data),{'os-migrateLive':{'host':'node2','block_migration':True}})
        with self.assertRaises(RuntimeError):api.move()
        self.assertEqual(len(captured),1)
    def test_readonly_mode_and_mutating_paths_are_refused(self):
        api=self.api(False)
        with self.assertRaises(RuntimeError):api.move()
        api=self.api()
        for method,path,body in [('DELETE','servers/'+SERVER,None),('POST','volumes',{}),('POST','servers/'+SERVER+'/action',{'reboot':{'type':'HARD'}}),('POST','servers/'+ID+'/action',{'os-migrateLive':{'host':'node2','block_migration':True}})]:
            with self.assertRaises(RuntimeError):api.request('compute',method,path,body,202)
        with self.assertRaises(RuntimeError):api.write('compute','POST','anything',{},202)
    def test_uncertain_post_cannot_be_repeated(self):
        api=self.api()
        api.client.opener.open=lambda *a,**kw:(_ for _ in ()).throw(TimeoutError())
        with self.assertRaises(RuntimeError):api.move()
        with self.assertRaisesRegex(RuntimeError,'Only one'):api.move()
    def test_foreign_migration_history_is_rejected(self):
        api=self.api();bad=row();bad['instance_uuid']=ID
        with patch.object(api,'request',return_value={'migrations':[bad]}),self.assertRaises(RuntimeError):api.history()
    def test_incomplete_or_duplicate_history_cannot_reconcile_an_attempt(self):
        api=self.api(False)
        for page in ({'migrations':[row()],'migrations_links':[{'rel':'next','href':'next'}]},
                     {'migrations':[row(),row()]},{'migrations':[row()]*100},
                     {'migrations':[row()],'migrations_links':None}):
            with self.subTest(page=page),patch.object(api,'request',return_value=page),self.assertRaises(RuntimeError):api.history()
    def test_nonapproved_destination_refused(self):
        with self.assertRaises(RuntimeError):step.API(None,{},'token',SERVER,'node3')


class PlanTests(unittest.TestCase):
    def test_binding_identity_expiry_and_host_guard(self):
        ctx=context();p=dict(step.workload_binding(ctx),plan=step.PLAN,id=ID,prepared_unix=1000,nodes={n:step.node_identity(r) for n,r in ctx['records'].items()})
        step.validate_plan(p,ctx,1100)
        for wrong in (dict(p,prepared_unix=-9999),dict(p,id='x'),dict(p,volume_id=MIG),dict(p,nodes={}),dict(p,checksum='changed')):
            with self.assertRaises(RuntimeError):step.validate_plan(wrong,ctx,1100)
        with self.assertRaises(RuntimeError):step.validate_plan(p,context('node2'),1100)
    def test_placement_headroom_reservations_and_step_size(self):
        inv={k:{'total':10000,'reserved':0,'allocation_ratio':1,'min_unit':1,'max_unit':10000,'step_size':1} for k in ('VCPU','MEMORY_MB','DISK_GB')}
        value={'inventories':inv,'usages':{k:0 for k in inv}};requirements={'vcpus':1,'ram_mb':2048,'disk_gb':10}
        step.placement_room(value,requirements)
        for resource in inv:
            bad=copy.deepcopy(value);bad['usages'][resource]=10000
            with self.assertRaises(RuntimeError):step.placement_room(bad,requirements)
        bad=copy.deepcopy(value);bad['inventories']['VCPU']['step_size']=2
        with self.assertRaises(RuntimeError):step.placement_room(bad,requirements)
    def test_invalid_receipt_does_not_allow_reconciliation(self):
        value=state();step.validate_receipt(value)
        for wrong in (dict(value,plan={}),dict(value,attempted=False),dict(value,guest_boot_id='x'),dict(value,before_migration_ids=['x'])):
            with self.assertRaises(RuntimeError):step.validate_receipt(wrong)
    def test_default_does_not_connect(self):
        with patch.object(step.inspection,'execute',side_effect=AssertionError),contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(step.main([]),0)
        self.assertIn('PLAN ONLY',output.getvalue())
    def test_prerequisite_has_no_migration_and_checks_installed_vnc_support(self):
        ctx=context();logs=[];api=SimpleNamespace(history=lambda:[])
        caps={'inventories':{k:{'total':10000} for k in ('VCPU','MEMORY_MB','DISK_GB')},'usages':{k:0 for k in ('VCPU','MEMORY_MB','DISK_GB')}}
        support={'observed':True,'graphics_rewrite_test_passed':True,'graphics_rewrite_wired':True}
        with patch.object(step.inspection,'capacity',return_value=caps),patch.object(step.inspection.Nodes,'call',return_value=support) as calls:
            step.prerequisite_checks(ctx,api,logs.append)
        self.assertEqual([c.args[1] for c in calls.call_args_list],['vnc','vnc'])
        self.assertIn('PASS',logs[-1])
        bad=copy.deepcopy(ctx);bad['records']['node1']['domain']['host_devices']=1
        with self.assertRaises(RuntimeError):step.prerequisite_checks(bad,api,logs.append)
    def test_actual_guest_size_must_match_capacity_requirements(self):
        ctx=context();ctx['records']['node1']['domain']['memory']['value']='8388608'
        with self.assertRaisesRegex(RuntimeError,'Actual VM memory'):step.prerequisite_checks(ctx,SimpleNamespace(history=lambda:[]),lambda s:None)
    def test_incompatible_cpu_or_unconfirmed_capacity_blocks_before_vnc_reads(self):
        for key in context()['summaries']['node2']:
            ctx=context();ctx['summaries']['node2'][key]=False
            with patch.object(step.inspection.Nodes,'call') as calls,self.assertRaises(RuntimeError):step.prerequisite_checks(ctx,SimpleNamespace(history=lambda:[]),lambda s:None)
            calls.assert_not_called()


class PollTests(unittest.TestCase):
    def snapshot(self,host='node2',task=None,status='completed'):
        api=SimpleNamespace(get=lambda *a:{'server':{'id':SERVER,'status':'ACTIVE','OS-EXT-SRV-ATTR:host':host,'OS-EXT-STS:task_state':task}},history=lambda:[row(status)])
        return step.migration_snapshot(api,state())
    def test_host_change_alone_is_not_completion(self):
        self.assertTrue(step.completed(self.snapshot()))
        for snap in (self.snapshot(status='running'),self.snapshot(host='node1'),self.snapshot(task='migrating')):
            self.assertFalse(step.completed(snap))
    def test_foreign_or_multiple_new_migrations_rejected(self):
        for rows in ([dict(row(),dest_compute='node3')],[row(),dict(row(),uuid=ID)],[dict(row(),migration_type='evacuation')]):
            api=SimpleNamespace(get=lambda *a:{'server':{'id':SERVER}},history=lambda:rows)
            with self.assertRaises(RuntimeError):step.migration_snapshot(api,state())
    def test_failed_or_timed_out_poll_preserves_pending_state(self):
        for snap in (self.snapshot(status='error'),self.snapshot(status='running')):
            st=state();saved=[]
            with patch.object(step,'migration_snapshot',return_value=snap),patch.dict(step.PLAN,{'observation_seconds':-1}),self.assertRaises(RuntimeError):
                step.wait_for_completion(None,st,lambda s:saved.append(copy.deepcopy(s)),lambda s:None)
            self.assertTrue(saved[-1]['pending'])


class Sampler:
    def __init__(self,check):self.records=[]
    def start(self):self.records=[{'ok':True}]
    def stop(self):pass


class ApplyTests(unittest.TestCase):
    def test_receipt_before_request_and_single_request(self):
        events=[];st=state();st.pop('attempted');api=SimpleNamespace(move=lambda:events.append('POST'))
        def save(value):events.append('saved-pending' if value['pending'] else 'saved')
        with patch.object(step,'wait_for_completion',return_value={}) as wait:
            step.apply_move(api,st,save,lambda:{'ok':True},lambda s:None,Sampler)
        self.assertEqual(events[0],'saved-pending');self.assertEqual(events.count('POST'),1)
        self.assertTrue(st['pending']);wait.assert_called_once()
    def test_receipt_write_failure_prevents_request(self):
        st=state();st.pop('attempted');calls=[]
        with self.assertRaises(OSError):step.apply_move(SimpleNamespace(move=lambda:calls.append(1)),st,lambda v:(_ for _ in ()).throw(OSError()),lambda:{'ok':True},lambda s:None,Sampler)
        self.assertFalse(calls)
    def test_lost_post_or_interrupt_keeps_pending_and_never_retries(self):
        for error in (RuntimeError('Response lost'),KeyboardInterrupt()):
            st=state();st.pop('attempted');calls=[]
            def move():calls.append(1);raise error
            with self.assertRaises(type(error)):step.apply_move(SimpleNamespace(move=move),st,lambda v:None,lambda:{'ok':True},lambda s:None,Sampler)
            self.assertEqual(calls,[1]);self.assertTrue(st['pending']);self.assertTrue(st['attempted'])
    def test_attempted_receipt_blocks_another_post(self):
        calls=[]
        with self.assertRaises(RuntimeError):step.apply_move(SimpleNamespace(move=lambda:calls.append(1)),state(),lambda v:None,lambda:{'ok':True},lambda s:None,Sampler)
        self.assertFalse(calls)


class FinalTests(unittest.TestCase):
    def final(self,ctx,st,boot_id=BOOT,running=None):
        running=running or {n:{'observed':True,'VM_running':n=='node2'} for n in node.NODES}
        with patch.object(step,'boot_id',return_value=boot_id),patch.object(step.inspection.Nodes,'call',side_effect=lambda n,*a:running[n]):step.final_checks(ctx,st,lambda s:None)
    def test_destination_boot_id_config_and_storage_identity(self):
        st=state();ctx=context('node2');self.final(ctx,st)
        for key,value in [('guest_boot_id',ID),('storage_backend','node2@other'),('checksum','changed')]:
            bad=copy.deepcopy(st);bad[key]=value
            with self.assertRaises(RuntimeError):self.final(ctx,bad)
        bad=context('node2');bad['records']['node2']['domain']['vnc_listen']=['192.168.0.200']
        with self.assertRaises(RuntimeError):self.final(bad,st)
    def test_two_active_copies_or_changed_database_identity_are_not_success(self):
        running={n:{'observed':True,'VM_running':n in ('node1','node2')} for n in node.NODES}
        with self.assertRaises(RuntimeError):self.final(context('node2'),state(),running=running)
        bad=context('node2');bad['services'][0]['node3']['mariadb']['id']='changed'
        with self.assertRaises(RuntimeError):self.final(bad,state())
    def test_guest_boot_id_must_be_uuid(self):
        with patch.object(step.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='bad')),self.assertRaises(RuntimeError):step.boot_id(['ssh'])


class InstalledNovaTests(unittest.TestCase):
    def reader(self,wired=True,updates=True):
        migration='''def graphics_listen_addrs(data):\n return {'vnc':str(data.graphics_listen_addr_vnc)}\ndef _update_graphics_xml(xml,data):\n values=graphics_listen_addrs(data)\n for g in xml.findall('devices/graphics'):\n  g.set('listen',values['vnc'])\n  g.find('listen').set('address',values['vnc'])\n return xml\ndef get_updated_guest_xml(instance,guest,data,get_volume_config):\n return _update_graphics_xml(guest,data)\n'''
        if not updates:migration=migration.replace("g.set('listen',values['vnc'])","g.set('listen','192.0.2.1')")
        if not wired:migration=migration.replace('return _update_graphics_xml(guest,data)','return guest')
        driver='''def prepare(data,CONF,migration,guest):\n data.graphics_listen_addr_vnc=CONF.vnc.server_listen\n return migration.get_updated_guest_xml(None,guest,data,None)'''
        pkg=SimpleNamespace(version='34.0.0',locate_file=lambda path:Path(path))
        output=io.StringIO()
        with patch('importlib.metadata.distribution',return_value=pkg),patch.object(Path,'read_text',lambda p:driver if p.name=='driver.py' else migration),patch('sys.argv',['reader','192.168.0.201']),contextlib.redirect_stdout(output):exec(compile(node.VNC_READER,'reader','exec'),{})
        return json.loads(output.getvalue())
    def test_runtime_graphics_probe_checks_transform_and_wiring(self):
        value=self.reader();self.assertTrue(value['graphics_rewrite_test_passed']);self.assertTrue(value['graphics_rewrite_wired'])
        self.assertFalse(self.reader(wired=False)['graphics_rewrite_wired'])
        self.assertFalse(self.reader(updates=False)['graphics_rewrite_test_passed'])
    def test_domain_summary_reports_host_devices_and_vnc_without_secret_xml(self):
        xml='<domain type="kvm"><uuid>'+SERVER+'</uuid><cpu/><os><type arch="x86_64" machine="pc-i440fx-noble">hvm</type></os><devices><hostdev/><graphics type="vnc" listen="192.168.0.201"/></devices></domain>'
        value=node.domain_summary(xml,SERVER);self.assertEqual(value['host_devices'],1);self.assertEqual(value['vnc_listen'],['192.168.0.201'])

class CallbackTests(unittest.TestCase):
    def test_preparation_saves_plan_but_cannot_issue_migration(self):
        ctx=context();ctx['api'].client=object();ctx['api'].token='PRIVATE_TOKEN'
        api=SimpleNamespace(history=lambda:[],move=lambda:(_ for _ in ()).throw(AssertionError('prepare must not move')))
        saved=[]
        with patch.object(step,'API',return_value=api),patch.object(step,'read_state',return_value=None),patch.object(step,'prerequisite_checks',return_value={'support':'verified'}),patch.object(step.net,'write_state',side_effect=lambda p,v:saved.append(copy.deepcopy(v))):
            callback=step.continuation('prepare',lambda s:None);callback(ctx,lambda s:None)
        self.assertEqual(callback.destination,'node2');self.assertEqual(len(saved),1)
        self.assertEqual(saved[0]['plan'],step.PLAN);self.assertEqual(saved[0]['server_id'],SERVER)
        self.assertNotIn('PRIVATE_TOKEN',json.dumps(saved))
    def test_pending_request_blocks_new_preparation_before_api_setup(self):
        with patch.object(step,'read_state',side_effect=[state(),None]),patch.object(step,'API') as api,self.assertRaises(RuntimeError):
            step.continuation('prepare',lambda s:None)(context(),lambda s:None)
        api.assert_not_called()
    def test_verification_cannot_clear_pending_without_nova_completion(self):
        ctx=context('node2');ctx['api'].client=object();ctx['api'].token='token'
        api=SimpleNamespace()
        with patch.object(step,'read_state',side_effect=[state(),{}]),patch.object(step,'API',return_value=api),patch.object(step,'migration_snapshot',return_value={'server_status':'ACTIVE','host':'node1','task':None,'migration':None}),patch.object(step.net,'write_state') as save,self.assertRaises(RuntimeError):
            step.continuation('verify',lambda s:None)(ctx,lambda s:None)
        save.assert_not_called()


class FaultObservationTests(unittest.TestCase):
    def api(self,detail_override=None,unavailable=False):
        request='req-'+MIG
        action={'action':'live-migration','instance_uuid':SERVER,'request_id':request,'start_time':'2026-10-27T21:19:00'}
        class API:
            def __init__(self):self.calls=[]
            def get(self,service,path):return {'server':{'id':SERVER,'fault':{'code':500,'message':'Example migration precheck failed'}}}
            def request(self,service,method,path):
                self.calls.append((method,path))
                if unavailable:raise RuntimeError('API returned HTTP 403')
                if '?' in path:return {'instanceActions':[dict(action,request_id='req-'+ID,start_time='2026-10-26T00:00:00'),action]}
                return {'instanceAction':detail_override if detail_override is not None else dict(action,message='Error',events=[{'event':'conductor_live_migrate_instance','result':'Error','host':'node1','traceback':'File frame PRIVATE_STACK\nnova.exception.ExampleError: PRIVATE_ERROR_VALUE'}])}
        return API()
    def test_only_gets_and_selected_fault_event_metadata_are_returned(self):
        api=self.api();value=step.fault_diagnostics(api,state())
        self.assertEqual(value['fault_code'],500)
        self.assertEqual(value['latest_live_migration_action']['request_id'],'req-'+MIG)
        self.assertEqual(value['latest_live_migration_action']['failed_events'][0]['exception_type'],'nova.exception.ExampleError')
        self.assertTrue(all(method=='GET' for method,path in api.calls))
        self.assertNotIn('PRIVATE_STACK',json.dumps(value));self.assertNotIn('PRIVATE_ERROR_VALUE',json.dumps(value))
    def test_missing_policy_access_retains_fault_without_repair_or_retry(self):
        api=self.api(unavailable=True);original=copy.deepcopy(state());value=step.fault_diagnostics(api,original)
        self.assertEqual(value['fault_code'],500);self.assertIn('403',value['action_details']);self.assertEqual(original,state())
        self.assertEqual(len(api.calls),1)
    def test_mismatched_action_is_not_used_as_failure_evidence(self):
        value=step.fault_diagnostics(self.api(detail_override={'instance_uuid':ID}),state())
        self.assertIsNone(value['latest_live_migration_action']);self.assertIn('identity differs',value['action_details'])
    def test_sensitive_values_and_raw_traceback_are_never_printed(self):
        self.assertNotIn('PRIVATE',step.safe_message('password=PRIVATE'))
        self.assertNotIn('PRIVATE',step.safe_message('failed qemu+tcp://joe:PRIVATE@host/system'))
        self.assertEqual(step.exception_name('nova.exception.ExampleError: PRIVATE'),'nova.exception.ExampleError')
        self.assertIsNone(step.exception_name('PRIVATE_FREEFORM_VALUE with spaces'))
        self.assertIsNone(step.exception_name('PRIVATE_SECRET'))
        self.assertEqual(step.exception_name('libvirt.libvirtError: PRIVATE'),'libvirt.libvirtError')
        self.assertEqual(len(step.safe_message('x'*1000)),500)
    def test_missing_fault_and_action_are_reported_without_guessing(self):
        api=self.api();api.get=lambda *a:{'server':{'id':SERVER}}
        api.request=lambda *a:{'instanceActions':[]}
        value=step.fault_diagnostics(api,state())
        self.assertIsNone(value['fault_code']);self.assertIsNone(value['fault_message']);self.assertIsNone(value['latest_live_migration_action'])


if __name__=='__main__':unittest.main()

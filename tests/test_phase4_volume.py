import base64
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import urllib.error
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def load(name,file):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/file);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod
step=load('volume_test','phase4-volume.py');guest=load('volume_guest_test','phase4-volume-guest.py')
SERVER='0ef50722-c056-49fa-8f9e-05e926a8b41d';VOLUME='12345678-abcd-4321-8321-0123456789ab';PROJECT='project-id'
FUUID='87654321-abcd-4321-8321-0123456789ab';TYPE='a2345678-abcd-4321-8321-0123456789ab'
HOST='node2@lvm-1#LVM'


def state():
    return {'plan':step.PLAN,'server_id':SERVER,'project_id':PROJECT,'stage':'new','volume_type':{'id':TYPE,'name':'__DEFAULT__'},
        'nonce':'a'*32,'filesystem_uuid':FUUID,'pools':{HOST:{}}}


class API:
    def __init__(self):self.volumes=[];self.rows=[];self.writes=[];self.failure=None;self.create_body=None
    def list(self,service,path,key):return copy.deepcopy(self.volumes)
    def get(self,service,path):
        if 'os-volume_attachments' in path:return {'volumeAttachments':copy.deepcopy(self.rows)}
        return {'volume':copy.deepcopy(self.volumes[0])}
    def write(self,service,method,path,body,expected):
        self.writes.append((service,method,path,copy.deepcopy(body),expected))
        if self.failure==len(self.writes):raise RuntimeError('Uncertain request')
        if service=='block-storage':
            v=copy.deepcopy(body['volume']);v.update(id=VOLUME,volume_type='__DEFAULT__',status='available',attachments=[],
                **{'os-vol-tenant-attr:tenant_id':PROJECT,'bootable':'false','multiattach':False,'encrypted':False,'os-vol-host-attr:host':HOST})
            self.volumes.append(v);return {'volume':copy.deepcopy(v)}
        if method=='POST':
            self.rows=[{'volumeId':VOLUME,'serverId':SERVER,'delete_on_termination':False}]
            self.volumes[0].update(status='in-use',attachments=[{'volume_id':VOLUME,'server_id':SERVER}])
            return {'volumeAttachment':copy.deepcopy(self.rows[0])}
        self.rows=[];self.volumes[0].update(status='available',attachments=[]);return {}


class WorkflowTests(unittest.TestCase):
    def setup_flow(self):
        self.api=API();self.s=state();self.saved=[];self.actions=[];self.fail_action=None
        def save(v):self.saved.append(copy.deepcopy(v))
        def probe(action):
            self.actions.append(action)
            if action==self.fail_action:raise RuntimeError('Guest operation not confirmed')
            if action=='baseline':return {'baseline':[{'name':'/dev/vda'}]}
            return {'passed':True,'ready':True,'unmounted':True,'sha256':step.expected_checksum(self.s)}
        self.save=save;self.probe=probe
    def flow(self,verify=False):return step.workflow(self.api,self.s,PROJECT,self.save,self.probe,lambda x:None,verify)
    def test_full_lifecycle_and_retention(self):
        self.setup_flow();result=self.flow()
        self.assertEqual(self.s['stage'],'complete');self.assertNotIn('pending',self.s)
        self.assertEqual([w[1] for w in self.api.writes],['POST','POST','DELETE','POST'])
        self.assertEqual(self.actions,['baseline','inspect','initialize','write','unmount','unmounted','absent','present','mount','verify'])
        self.assertEqual(result['storage_host'],'node2');self.assertEqual(result['persistence'],'same SHA256 before and after safe detach/reattach')
        self.assertEqual(self.api.writes[0][3]['volume']['size'],1)
        self.assertNotIn('availability_zone',self.api.writes[0][3]['volume'])
        for write in (self.api.writes[1],self.api.writes[3]):self.assertEqual(write[3],{'volumeAttachment':{'volumeId':VOLUME,'delete_on_termination':False}})
        pending=[v['pending'] for v in self.saved if 'pending'in v]
        self.assertEqual(len(pending),8)
    def test_verify_and_apply_completed_do_not_mutate(self):
        self.setup_flow();self.flow();self.api.writes=[];self.actions=[];self.saved=[]
        self.flow(True);self.assertEqual(self.actions,['verify']);self.assertFalse(self.api.writes);self.assertFalse(self.saved)
        self.actions=[];self.flow();self.assertEqual(self.actions,['verify']);self.assertFalse(self.api.writes)
    def test_uncertain_each_api_mutation_blocks_rerun(self):
        for n in range(1,5):
            with self.subTest(n=n):
                self.setup_flow();self.api.failure=n
                with self.assertRaises(RuntimeError):self.flow()
                self.assertIn('pending',self.s);count=len(self.api.writes)
                with self.assertRaises(RuntimeError):self.flow()
                self.assertEqual(len(self.api.writes),count)
    def test_uncertain_guest_mutation_never_reformats(self):
        for action in ('initialize','write','unmount','mount'):
            with self.subTest(action=action):
                self.setup_flow();self.fail_action=action
                with self.assertRaises(RuntimeError):self.flow()
                self.assertIn('pending',self.s);before=list(self.actions)
                with self.assertRaises(RuntimeError):self.flow()
                self.assertEqual(self.actions,before)
    def test_existing_named_volume_is_never_adopted(self):
        self.setup_flow();self.api.volumes=[{'id':VOLUME,'name':step.PLAN['name']}]
        with self.assertRaisesRegex(RuntimeError,'Untracked'):self.flow()
        self.assertFalse(self.api.writes);self.assertFalse(self.actions)
    def test_verify_requires_completed_state(self):
        self.setup_flow()
        with self.assertRaisesRegex(RuntimeError,'completed'):self.flow(True)
        self.assertFalse(self.api.writes);self.assertFalse(self.actions)
    def test_mount_checksum_failure_keeps_checkpoint_for_review(self):
        self.setup_flow();self.flow();self.s['sha256']='wrong';self.api.writes=[]
        with self.assertRaisesRegex(RuntimeError,'checksum'):self.flow(True)
        self.assertFalse(self.api.writes)
    def test_volume_drift_or_foreign_attachment(self):
        self.setup_flow();self.flow()
        for key,bad in [('size',2),('encrypted',True),('multiattach',True),('volume_type','other'),('os-vol-tenant-attr:tenant_id','foreign'),('os-vol-host-attr:host','node3@other#pool'),('attachments',[{'server_id':'foreign','volume_id':VOLUME}])]:
            obj=copy.deepcopy(self.api.volumes[0]);obj[key]=bad
            with self.subTest(key=key),self.assertRaises(RuntimeError):step.volume_check(obj,self.s,PROJECT)
    def test_read_only_visibility_poll_only(self):
        calls=[]
        def probe(action):calls.append(action);return {'ready':len(calls)>1}
        with patch.object(step.time,'sleep'):step.wait_guest(probe,'present')
        self.assertEqual(calls,['present','present'])
        with self.assertRaises(RuntimeError):step.wait_guest(lambda a:(_ for _ in ()).throw(RuntimeError('Wrong disk')),'present')
    def test_wait_for_both_nova_and_cinder_without_accepting_foreign_rows(self):
        self.setup_flow();self.flow()
        self.api.volumes[0].update(status='available',attachments=[])
        readings=[copy.deepcopy(self.api.rows),[]]
        original=self.api.get
        def get(service,path):
            if 'os-volume_attachments' in path:return {'volumeAttachments':readings.pop(0)}
            return original(service,path)
        with patch.object(self.api,'get',side_effect=get),patch.object(step.time,'sleep'):
            step.wait_volume(self.api,self.s,PROJECT,False)
        self.assertFalse(readings)
        self.api.rows=[{'volumeId':'foreign','serverId':SERVER,'delete_on_termination':False}]
        with self.assertRaises(RuntimeError):step.wait_volume(self.api,self.s,PROJECT,False)

    def test_no_detach_without_current_unmount_confirmation(self):
        self.setup_flow();self.flow();self.s['stage']='unmounted';self.api.writes=[]
        with self.assertRaisesRegex(RuntimeError,'safe unmount'):
            step.workflow(self.api,self.s,PROJECT,self.save,lambda a:{'unmounted':False},lambda x:None)
        self.assertFalse(self.api.writes)


class APITests(unittest.TestCase):
    def setUp(self):
        self.api=step.API(SimpleNamespace(),{'compute':'https://192.168.0.151:8774/v2.1','block-storage':'https://192.168.0.151:8776/v3/'+PROJECT},'secret')
        self.api.server_id=SERVER;self.api.volume_id=VOLUME;self.api.create_body=step.body_for(state())
    def test_scope_allowlist(self):
        base='servers/'+SERVER+'/os-volume_attachments'
        with patch.object(self.api,'request',return_value={}) as request:
            self.api.write('block-storage','POST','volumes',self.api.create_body,202)
            self.api.write('compute','POST',base,{'volumeAttachment':{'volumeId':VOLUME,'delete_on_termination':False}},200)
            self.api.write('compute','DELETE',base+'/'+VOLUME,None,202)
            self.assertEqual(request.call_count,3)
            for args in [('block-storage','DELETE','volumes/'+VOLUME,None,202),('compute','POST',base,{'volumeAttachment':{'volumeId':VOLUME,'delete_on_termination':True}},200),('compute','DELETE','servers/foreign/os-volume_attachments/'+VOLUME,None,202),('compute','POST','servers/'+SERVER+'/action',{'reboot':{}},202)]:
                with self.assertRaisesRegex(RuntimeError,'Unapproved'):self.api.write(*args)
            self.assertEqual(request.call_count,3)
    def test_microversion_and_no_secret_response_log(self):
        class Opener:
            def open(inner,req,timeout):
                self.assertEqual(req.headers['Openstack-api-version'],'compute 2.79')
                raise urllib.error.HTTPError(req.full_url,409,'secret body token',{},None)
        self.api.client=SimpleNamespace(opener=Opener())
        with self.assertRaisesRegex(RuntimeError,'HTTP 409') as caught:self.api.get('compute','servers/'+SERVER+'/os-volume_attachments')
        self.assertNotIn('secret',str(caught.exception))
    def test_default_mode_no_environment_or_cloud(self):
        with patch.object(step,'execute',side_effect=AssertionError),patch.object(step.deploy.tools,'check_paths',side_effect=AssertionError),contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(step.main([]),0)
        self.assertIn('PLAN ONLY',output.getvalue())


def root_rows():
    return [dict(name='/dev/vda',kname='/dev/vda',type='disk',size=10*1024**3,serial='',fstype=None,uuid=None,mountpoints=[None],**{'maj:min':'252:0','ro':False}),
            dict(name='/dev/vda1',kname='/dev/vda1',type='part',size=9*1024**3,serial=None,fstype='ext4',uuid='root',mountpoints=['/'],**{'maj:min':'252:1','ro':False})]
def disk():return dict(name='/dev/vdb',kname='/dev/vdb',type='disk',size=1024**3,serial=VOLUME[:20],fstype=None,uuid=None,mountpoints=[None],**{'maj:min':'252:16','ro':False})
def context():return dict(server_id=SERVER,volume_id=VOLUME,filesystem_uuid=FUUID,nonce='a'*32,baseline=guest.baseline(root_rows()))


class GuestTests(unittest.TestCase):
    def test_serial_baseline_size_root_guards(self):
        expected=context();d=disk();self.assertEqual(guest.select(root_rows()+[d],expected),d)
        for change in ({'serial':'foreign'},{'size':10*1024**3},{'ro':True},{'type':'part'},{'name':'/dev/../vda'}):
            bad=copy.deepcopy(d);bad.update(change)
            with self.subTest(change=change),self.assertRaises(RuntimeError):guest.select(root_rows()+[bad],expected)
        rows=root_rows();rows[0]['serial']=VOLUME[:20]
        with self.assertRaises(RuntimeError):guest.select(rows,expected)
    def test_changed_root_duplicate_new_disk_partition_refused(self):
        for rows in (root_rows()+[disk(),dict(disk(),name='/dev/vdc')],root_rows()+[disk(),dict(disk(),name='/dev/vdb1',type='part')],root_rows()[1:]+[disk()]):
            with self.assertRaises(RuntimeError):guest.select(rows,context())
        self.assertIsNone(guest.select(root_rows(),context(),True))
        with self.assertRaises(RuntimeError):guest.select(root_rows()+[dict(disk(),type='lvm',serial=None)],context(),True)
        with self.assertRaises(RuntimeError):guest.select(root_rows()+[disk()],context(),True)
    def test_flatten_and_root_baseline(self):
        rows=root_rows();self.assertEqual(guest.flatten([dict(rows[0],children=[rows[1]])]),rows)
        with self.assertRaises(RuntimeError):guest.baseline([disk()])
    def test_blank_rejects_mounted_or_existing_signature(self):
        for d in (dict(disk(),mountpoints=['/mnt/other']),dict(disk(),fstype='ext4'),dict(disk(),uuid=FUUID)):
            with patch.object(guest,'block_guard'),self.assertRaises(RuntimeError):guest.blank(d)
        with patch.object(guest,'block_guard'),patch.object(guest.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='TYPE=ext4')),self.assertRaises(RuntimeError):guest.blank(disk())
    def test_full_zero_scan_and_nonzero_refusal(self):
        for blocks,ok in (([b'\0'*4,b''],True),([b'\0bad'],False),([b''],False),([b'\0'*4,b'x'],False)):
            with self.subTest(blocks=blocks),patch.object(guest,'SIZE',4),patch.object(guest,'block_guard'),patch.object(guest.subprocess,'run',return_value=SimpleNamespace(returncode=2,stdout='')),patch.object(guest.os,'open',return_value=99),patch.object(guest.os,'fstat',return_value=SimpleNamespace(st_mode=0o60600,st_rdev=guest.os.makedev(252,16))),patch.object(guest.os,'read',side_effect=blocks),patch.object(guest.os,'close'):
                if ok:guest.blank(disk())
                else:
                    with self.assertRaises(RuntimeError):guest.blank(disk())
    def test_filesystem_uuid_guard(self):
        with patch.object(guest,'block_guard'),patch.object(guest,'run',return_value='TYPE=ext4\nUUID='+FUUID):guest.filesystem(disk(),context())
        for value in ('TYPE=ext4\nUUID=wrong','TYPE=xfs\nUUID='+FUUID):
            with patch.object(guest,'block_guard'),patch.object(guest,'run',return_value=value),self.assertRaises(RuntimeError):guest.filesystem(disk(),context())
    def test_initialize_formats_only_selected_volume_and_no_force(self):
        calls=[]
        def run(args,timeout=30):calls.append(args);return ''
        with patch.object(guest,'identity'),patch.object(guest.shutil,'which',return_value='/bin/tool'),patch.object(guest,'snapshot',return_value=root_rows()+[disk()]),patch.object(guest,'blank') as blank,patch.object(guest,'mount_directory'),patch.object(guest,'filesystem'),patch.object(guest,'run',side_effect=run):
            value=guest.execute(context(),'initialize')
        blank.assert_called_once();self.assertTrue(value['initialized'])
        mkfs=calls[0];self.assertEqual(mkfs[0],'mkfs.ext4');self.assertEqual(mkfs[-1],'/dev/vdb');self.assertNotIn('-F',mkfs);self.assertIn(FUUID,mkfs)
        self.assertEqual(calls[1],['mount','-t','ext4','-o','nodev,nosuid,noexec','/dev/vdb','/mnt/lab-data'])
    def test_guest_hotplug_readiness_without_masking_wrong_devices(self):
        with patch.object(guest,'identity'),patch.object(guest.shutil,'which',return_value='/bin/tool'),patch.object(guest,'snapshot',return_value=root_rows()):
            self.assertEqual(guest.execute(context(),'present'),{'ready':False})
            self.assertEqual(guest.execute(context(),'absent'),{'ready':True,'volume_absent':True})
        with patch.object(guest,'identity'),patch.object(guest.shutil,'which',return_value='/bin/tool'),patch.object(guest,'snapshot',return_value=root_rows()+[dict(disk(),serial='foreign')]),self.assertRaises(RuntimeError):guest.execute(context(),'present')
    def test_verify_reads_file_does_not_mount_or_format(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(guest,'MOUNT',Path(folder)),patch.object(guest,'identity'),patch.object(guest.shutil,'which',return_value='/bin/tool'),patch.object(guest,'snapshot',return_value=root_rows()+[disk()]),patch.object(guest,'mounted'),patch.object(guest,'run',side_effect=AssertionError('No modifying command allowed')):
            target=Path(folder)/guest.FILE;target.write_bytes(guest.content(context()))
            result=guest.execute(context(),'verify');self.assertEqual(result['sha256'],step.hashlib.sha256(target.read_bytes()).hexdigest())
            target.write_text('different')
            with self.assertRaisesRegex(RuntimeError,'content differs'):guest.execute(context(),'verify')
    def test_symlink_file_and_duplicate_write_refused(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(guest,'MOUNT',Path(folder)),patch.object(guest,'identity'),patch.object(guest.shutil,'which',return_value='/bin/tool'),patch.object(guest,'snapshot',return_value=root_rows()+[disk()]),patch.object(guest,'mounted'):
            target=Path(folder)/guest.FILE;target.symlink_to('/etc/passwd')
            with self.assertRaises(RuntimeError):guest.execute(context(),'verify')
            target.unlink();target.write_bytes(guest.content(context()))
            with self.assertRaises(FileExistsError):guest.execute(context(),'write')
    def test_unmount_checks_flushes_and_never_forces(self):
        calls=[]
        with patch.object(guest,'identity'),patch.object(guest.shutil,'which',return_value='/bin/tool'),patch.object(guest,'snapshot',return_value=root_rows()+[disk()]),patch.object(guest,'mounted'),patch.object(guest,'check_file',return_value='b'*64),patch.object(guest,'run',side_effect=lambda args,timeout=30:calls.append(args) or ''):
            result=guest.execute(context(),'unmount')
        self.assertTrue(result['unmounted']);self.assertEqual(calls,[['sync','-f','/mnt/lab-data'],['umount','/mnt/lab-data']])
    def test_guest_sudo_identity_action_gates(self):
        with patch.object(guest.os,'geteuid',return_value=1000),self.assertRaises(RuntimeError):guest.execute(context(),'initialize')
        with self.assertRaisesRegex(RuntimeError,'Unknown'):guest.execute(context(),'wipe')
        with patch.object(guest,'identity',side_effect=RuntimeError('Wrong metadata')),patch.object(guest.shutil,'which',return_value='/bin/tool'),patch.object(guest,'snapshot') as snap,self.assertRaises(RuntimeError):guest.execute(context(),'initialize')
        snap.assert_not_called()


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.services=[{'binary':'cinder-volume','host':h+'@lvm-1','state':'up','status':'enabled'} for h in ('node1','node2','node3')]
        self.pools=[{'name':h+'@lvm-1#LVM','capabilities':{'volume_backend_name':'lvm-1','storage_protocol':'iSCSI','free_capacity_gb':100,'total_capacity_gb':200}} for h in ('node1','node2','node3')]
        self.vtype={'id':TYPE,'name':'__DEFAULT__','extra_specs':{}}
        self.quota={k:{'limit':10,'in_use':0,'reserved':0} for k in ('volumes','gigabytes','volumes___DEFAULT__','gigabytes___DEFAULT__','per_volume_gigabytes')}
        self.api=SimpleNamespace(list=lambda *a:copy.deepcopy(self.services),get=self.get)
    def get(self,service,path):
        if path.startswith('scheduler'):return {'pools':copy.deepcopy(self.pools)}
        if path=='types/default':return {'volume_type':copy.deepcopy(self.vtype)}
        return {'quota_set':copy.deepcopy(self.quota)}
    def test_capacity_type_and_pool_evidence(self):
        v,p=step.checks_before_create(self.api,PROJECT);self.assertEqual(v,{'id':TYPE,'name':'__DEFAULT__'});self.assertEqual(len(p),3)
        for q in self.quota.values():q['limit']=-1
        step.checks_before_create(self.api,PROJECT)
    def test_quota_capacity_services_and_type_fail_before_writes(self):
        for key in ('volumes','gigabytes','volumes___DEFAULT__','gigabytes___DEFAULT__','per_volume_gigabytes'):
            self.quota[key]['limit']=0
            with self.subTest(key=key),self.assertRaises(RuntimeError):step.checks_before_create(self.api,PROJECT)
            self.quota[key]['limit']=10
        for pool in self.pools:pool['capabilities']['free_capacity_gb']='unknown'
        with self.assertRaises(RuntimeError):step.checks_before_create(self.api,PROJECT)
        for pool in self.pools:pool['capabilities']['free_capacity_gb']=100
        self.services[0]['state']='down'
        with self.assertRaises(RuntimeError):step.checks_before_create(self.api,PROJECT)
        self.services[0]['state']='up';self.vtype['extra_specs']={'replication_enabled':'true'}
        with self.assertRaises(RuntimeError):step.checks_before_create(self.api,PROJECT)

class ExistingVMIntegrationTests(unittest.TestCase):
    def test_root_disk_assertion_allows_only_explicit_reviewed_data_volume(self):
        netids={'private_network':'net'};prepared={'image':'image','flavor':'flavor','key':'key'}
        server={'name':'lab-vm-01','tenant_id':PROJECT,'user_id':'user','key_name':'key','metadata':{'ofz_chapter':'phase4-d','ofz_private_network':'net'},
            'image':{'id':'image'},'flavor':{'id':'flavor'},'config_drive':'','os-extended-volumes:volumes_attached':[{'id':VOLUME}]}
        with self.assertRaises(RuntimeError):step.boot.check_server(server,PROJECT,'user',netids,prepared)
        step.boot.check_server(server,PROJECT,'user',netids,prepared,VOLUME)
        server['os-extended-volumes:volumes_attached'].append({'id':'unknown'})
        with self.assertRaises(RuntimeError):step.boot.check_server(server,PROJECT,'user',netids,prepared,VOLUME)
    def test_completed_volume_gate_in_boot_verification(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);api=API();api.urls={'compute':'url'}
            self.assertIsNone(step.boot.reviewed_data_volume(api,folder,{'ids':{'server':SERVER}},PROJECT,'user'))
            value=state();value.update(stage='complete',volume_id=VOLUME,user_id='user',apis=api.urls)
            path=folder/'volume.json';path.write_text(json.dumps(value));path.chmod(0o600)
            volume=step.body_for(value)['volume'];volume.update(id=VOLUME,volume_type='__DEFAULT__',status='in-use',attachments=[{'volume_id':VOLUME,'server_id':SERVER}],bootable='false',multiattach=False,encrypted=False,
                **{'os-vol-tenant-attr:tenant_id':PROJECT,'os-vol-host-attr:host':HOST})
            api.volumes=[volume]
            self.assertEqual(step.boot.reviewed_data_volume(api,folder,{'ids':{'server':SERVER}},PROJECT,'user'),VOLUME)
            for change in ({'stage':'unmounted'},{'pending':'attach'},{'server_id':'foreign'}):
                bad=dict(value,**change);path.write_text(json.dumps(bad))
                with self.assertRaises(RuntimeError):step.boot.reviewed_data_volume(api,folder,{'ids':{'server':SERVER}},PROJECT,'user')


class ObservationTests(unittest.TestCase):
    def fixture(self,status='in-use',attached=True):
        value=state();value.update(volume_id=VOLUME,stage='absent',pending='reattach same data volume',sha256='b'*64)
        volume=step.body_for(value)['volume'];volume.update(id=VOLUME,volume_type='__DEFAULT__',status=status,attachments=[{'volume_id':VOLUME,'server_id':SERVER}] if attached else [],bootable='false',multiattach=False,encrypted=False,
            **{'os-vol-tenant-attr:tenant_id':PROJECT,'os-vol-host-attr:host':HOST})
        rows=[{'volumeId':VOLUME,'serverId':SERVER,'delete_on_termination':False}] if attached else []
        def get(service,path):
            if service=='block-storage':return {'volume':copy.deepcopy(volume)}
            if path.endswith('os-volume_attachments'):return {'volumeAttachments':copy.deepcopy(rows)}
            return {'server':{'id':SERVER,'name':'lab-vm-01','status':'ACTIVE','OS-EXT-SRV-ATTR:host':'node1','OS-EXT-STS:task_state':None,
                'os-extended-volumes:volumes_attached':[{'id':VOLUME}] if attached else []}}
        return value,get
    def test_pending_snapshot_does_not_resolve_or_modify_it(self):
        for status,attached in (('in-use',True),('available',False),('attaching',False),('error_attaching',False)):
            with self.subTest(status=status):
                value,get=self.fixture(status,attached);before=copy.deepcopy(value)
                result=step.observe(SimpleNamespace(get=get),value,PROJECT)
                self.assertEqual(value,before);self.assertEqual(result['checkpoint']['pending'],'reattach same data volume')
                self.assertEqual(result['volume']['status'],status);self.assertIn('Not yet verified',result['persistence'])
                if status.startswith('error'):self.assertTrue(result['identity_issues'])
    def test_read_only_api_rejects_every_mutation(self):
        api=step.ReadOnlyAPI(SimpleNamespace(),{},'secret')
        for method in ('POST','PUT','DELETE','PATCH'):
            with self.assertRaises(RuntimeError):api.request('compute',method,'any',{})
        with self.assertRaises(RuntimeError):api.write('compute','POST','anything',{},200)
    def test_local_checkpoint_is_byte_identical_after_observation(self):
        value,get=self.fixture();urls={'compute':'url','block-storage':'other'};value.update(apis=urls,user_id='user')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);private=root/'private/phase4';private.mkdir(parents=True)
            path=private/'volume.json';path.write_text(json.dumps(value));path.chmod(0o600);before=path.read_bytes()
            vm=private/'vm.json';vm.write_text(json.dumps({'project_id':PROJECT,'user_id':'user','apis':urls,'ids':{'server':SERVER}}));vm.chmod(0o600)
            original=SimpleNamespace(client=None,urls=urls,token='secret');lines=[]
            with patch.object(step,'REPO',root),patch.object(step.deploy,'source_guard'),patch.object(step.deploy.tools,'tool_environment',return_value={}),patch.object(step.deploy.validation,'local_checks',return_value=('inventory','config')),patch.object(step.access,'input_hashes',return_value='hash'),patch.object(step.prep,'authenticate',return_value=(original,PROJECT,'user')),patch.object(step.ReadOnlyAPI,'get',side_effect=get),patch.object(step.ReadOnlyAPI,'write',side_effect=AssertionError('No writes')):
                self.assertEqual(step.execute_observation(lines.append),0)
            self.assertEqual(path.read_bytes(),before);self.assertIn('RESULT: CINDER_VOLUME_STATE_OBSERVED',lines)


class ReattachmentTests(unittest.TestCase):
    def setup_recovery(self):
        self.api=API();self.value=state();self.value.update(volume_id=VOLUME,stage='absent',pending='reattach same data volume',baseline=[{'name':'/dev/vda'}],backend=HOST)
        self.value['sha256']=step.expected_checksum(self.value)
        volume=step.body_for(self.value)['volume'];volume.update(id=VOLUME,volume_type='__DEFAULT__',status='available',attachments=[],bootable='false',multiattach=False,encrypted=False,
            **{'os-vol-tenant-attr:tenant_id':PROJECT,'os-vol-host-attr:host':HOST})
        self.api.volumes=[volume];self.saved=[];self.actions=[]
        self.save=lambda v:self.saved.append(copy.deepcopy(v))
        def probe(action):
            self.actions.append(action)
            self.assertIn(action,('absent','present','mount','verify'))
            return {'passed':True,'ready':True,'volume_absent':True,'sha256':step.expected_checksum(self.value)}
        self.probe=probe
    def run_recovery(self):return step.reattach_existing(self.api,self.value,PROJECT,self.save,self.probe,lambda line:None)
    def test_exactly_one_attach_no_format_create_or_detach(self):
        self.setup_recovery();result=self.run_recovery()
        self.assertEqual(len(self.api.writes),1);self.assertEqual(self.api.writes[0][0:2],('compute','POST'))
        self.assertEqual(self.actions,['absent','present','mount','verify']);self.assertEqual(self.value['stage'],'complete')
        self.assertNotIn('pending',self.value);self.assertEqual(self.saved[0]['pending'],'authorized single reattachment')
        self.assertTrue(self.saved[0]['reattach_recovery_attempted']);self.assertEqual(self.value['reconciled_request']['pending'],'reattach same data volume')
        self.assertEqual(result['volume_id'],VOLUME)
        self.api.writes=[];self.actions=[];self.run_recovery();self.assertFalse(self.api.writes);self.assertEqual(self.actions,['verify'])
    def test_uncertain_attach_cannot_be_retried(self):
        self.setup_recovery();self.api.failure=1
        with self.assertRaises(RuntimeError):self.run_recovery()
        self.assertEqual(self.value['pending'],'authorized single reattachment')
        with self.assertRaises(RuntimeError):self.run_recovery()
        self.assertEqual(len(self.api.writes),1)
    def test_changed_state_or_saved_identity_blocks_request(self):
        for change in ({'stage':'written'},{'pending':'other operation'},{'reattach_recovery_attempted':True},{'sha256':'wrong'},{'baseline':[]}):
            with self.subTest(change=change):
                self.setup_recovery();self.value.update(change)
                with self.assertRaises(RuntimeError):self.run_recovery()
                self.assertFalse(self.api.writes);self.assertFalse(self.saved)
    def test_live_attachment_or_guest_presence_blocks_request(self):
        for status in ('in-use','attaching','error_attaching'):
            with self.subTest(status=status):
                self.setup_recovery();self.api.volumes[0]['status']=status
                with self.assertRaises(RuntimeError):self.run_recovery()
                self.assertFalse(self.api.writes)
        self.setup_recovery();self.probe=lambda action:{'ready':False}
        with self.assertRaises(RuntimeError):self.run_recovery()
        self.assertFalse(self.api.writes)
    def test_duplicate_volume_and_foreign_nova_attachment_block_request(self):
        self.setup_recovery();self.api.volumes.append(dict(self.api.volumes[0],id='different'))
        with self.assertRaises(RuntimeError):self.run_recovery()
        self.assertFalse(self.api.writes)
        self.setup_recovery();self.api.rows=[{'volumeId':'foreign'}]
        with self.assertRaises(RuntimeError):self.run_recovery()
        self.assertFalse(self.api.writes)
    def test_recovery_api_rejects_creation_deletion_and_detach(self):
        api=step.ReattachAPI(SimpleNamespace(),{},'secret');api.server_id=SERVER;api.volume_id=VOLUME
        with patch.object(api,'request',return_value={}) as request:
            for args in [('block-storage','POST','volumes',{},202),('compute','DELETE','servers/'+SERVER+'/os-volume_attachments/'+VOLUME,None,202),('compute','POST','servers/foreign/os-volume_attachments',{},200)]:
                with self.assertRaises(RuntimeError):api.write(*args)
            request.assert_not_called()
            api.write('compute','POST','servers/'+SERVER+'/os-volume_attachments',{'volumeAttachment':{'volumeId':VOLUME,'delete_on_termination':False}},200)
            request.assert_called_once()


class RealFilesystemTests(unittest.TestCase):
    def test_ext4_flags_uuid_and_file_roundtrip_in_regular_scratch_image(self):
        # No mount, loop device, host block device or cloud call: only a disposable regular file.
        import os, shutil, subprocess
        if not all(shutil.which(tool) for tool in ('mkfs.ext4','blkid','debugfs','e2fsck')):
            self.skipTest('Local e2fsprogs tools unavailable')
        with tempfile.TemporaryDirectory() as folder:
            image=Path(folder)/'data.img';image.touch();os.truncate(image,1024**3)
            subprocess.run(['mkfs.ext4','-q','-m','0','-U',FUUID,'-L','lab-data','-E','nodiscard,lazy_itable_init=0,lazy_journal_init=0',str(image)],check=True,capture_output=True,timeout=180)
            probe=subprocess.run(['blkid','-p','-o','export',str(image)],check=True,capture_output=True,text=True)
            self.assertIn('UUID='+FUUID,probe.stdout);self.assertIn('TYPE=ext4',probe.stdout)
            original=Path(folder)/'original.txt';original.write_bytes(guest.content(context()))
            subprocess.run(['debugfs','-w','-R','write '+str(original)+' /persistence-test.txt',str(image)],check=True,capture_output=True)
            # Reopen the image in another process; no in-process content cache can satisfy this check.
            result=subprocess.run(['debugfs','-R','cat /persistence-test.txt',str(image)],check=True,capture_output=True)
            self.assertEqual(result.stdout,original.read_bytes())
            subprocess.run(['e2fsck','-fn',str(image)],check=True,capture_output=True,timeout=60)


if __name__=='__main__':unittest.main()

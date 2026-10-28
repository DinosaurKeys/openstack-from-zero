import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def load(name,file):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/file)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
r=load('reboot_verify_test','lab-reboot-verify.py');g=load('reboot_guest_test','lab-reboot-guest.py')
SERVER='0ef50722-c056-49fa-8f9e-05e926a8b41d';VOLUME='eebabffa-2a1b-4ea5-bdd7-62082f66df77';FS='f1568275-f734-4bbc-867e-26a822d2cb0d'
BOOT='c34e6666-3431-4ec9-b3f6-bfd0f2828498';NONCE='a'*32
CONTENT=('OpenStack Chapter E persistence test\nserver='+SERVER+'\nvolume='+VOLUME+'\nnonce='+NONCE+'\n').encode()
EXPECTED={'server_id':SERVER,'volume_id':VOLUME,'filesystem_uuid':FS,'nonce':NONCE,'sha256':hashlib.sha256(CONTENT).hexdigest()}


class RebootVerificationTests(unittest.TestCase):
    def test_paused_domain_is_not_reported_as_running(self):
        helper=load('reboot_runtime_node_test','phase5-migration-node.py')
        for state,expected in [('running',True),('paused',False)]:
            def virsh(*args,**kwargs):return SimpleNamespace(stdout=SERVER+'\n' if args[0]=='list' else state+'\n')
            with patch.object(helper,'identity',return_value={}),patch.object(helper,'virsh',side_effect=virsh):
                value=helper.execute('node1','running',{'server_id':SERVER})
            self.assertEqual(value['VM_running'],expected);self.assertEqual(value['domain_state'],state)

    def test_plan_has_no_commands_and_no_apply_mode(self):
        with patch.object(r.subprocess,'run',side_effect=AssertionError('no command')),contextlib.redirect_stdout(io.StringIO()) as out:self.assertEqual(r.main([]),0)
        self.assertIn('No repair mode',out.getvalue())
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):r.main(['--apply'])

    def test_pending_checkpoints_hashes_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(r,'REPO',Path(directory)):
            path=Path(directory)/'private/phase5/move-vm-state.json';path.parent.mkdir(parents=True)
            raw=b'{"attempted":true,"pending":true}';path.write_bytes(raw);path.chmod(0o600)
            self.assertEqual(r.snapshot(),r.snapshot());self.assertEqual(path.read_bytes(),raw)
            path.write_text('{"pending":false}');self.assertNotEqual(r.snapshot()[str(path.relative_to(directory))],hashlib.sha256(raw).hexdigest())

    def test_runtime_needs_all_three_nodes_and_exactly_one_running(self):
        class Nodes:
            def call(self,node,action,payload):
                self_test.assertEqual(action,'running');self_test.assertEqual(payload,{'server_id':SERVER})
                return {'node':node,'observed':True,'VM_running':node=='node1'}
        self_test=self
        result=r.runtime(Nodes(),SERVER);self.assertTrue(result['observed']);self.assertEqual(result['running_hosts'],['node1'])
        class Partial:
            def call(self,node,*args):return {'node':node,'observed':node!='node3','VM_running':node=='node1'}
        self.assertFalse(r.runtime(Partial(),SERVER)['observed'])

    def test_guest_reader_keeps_partial_failure_observations_and_has_no_mutation(self):
        value={'passed':False,'checks':{'metadata_matches_Nova_instance':True,'guest_DNS_lookup':False}}
        def run(args,**kwargs):
            self.assertTrue(args[-1].startswith('sudo -n /usr/bin/python3 - '))
            self.assertIn('def collect(expected)',kwargs['input'])
            self.assertNotIn('mount -',args[-1])
            return subprocess.CompletedProcess(args,1,json.dumps(value),'')
        with patch.object(r.subprocess,'run',side_effect=run):self.assertEqual(r.guest_read(['ssh','guest'],'phase4-guest-check.py',{},360),value)
        with self.assertRaises(RuntimeError):r.guest_read(['ssh'],'unapproved.py',{},30)

    def test_ssh_failure_does_not_expose_stderr(self):
        with patch.object(r.subprocess,'run',return_value=subprocess.CompletedProcess([],255,'','password=private')):
            with self.assertRaises(RuntimeError) as error:r.guest_read(['ssh'],'lab-reboot-guest.py',EXPECTED,90)
        self.assertNotIn('private',str(error.exception))

    def test_nova_error_does_not_skip_actual_guest_observations(self):
        vm={'ids':{'server':SERVER},'networks':{}};data={**EXPECTED,'backend':'node3@lvm-1#lvm-1'}
        current={'server_status':'ERROR','task':None,'host':'node1','volume_attached':True,'backend':data['backend'],'floating_ip':'192.168.0.194'}
        network={'passed':True,'checks':{'metadata_matches_Nova_instance':True,'guest_DNS_lookup':True}}
        guest={'observed':True,'disk_identified':True,'filesystem_matches':True,'mounted':True,'checksum_matches':True}
        def read(ssh,file,*args):return network if file=='phase4-guest-check.py' else guest
        with patch.object(r,'bindings',return_value=(vm,data,{})),patch.object(r,'api_workload',return_value=current),patch.object(r,'runtime',return_value={'observed':True,'running_hosts':['node1']}),patch.object(r,'pending_migration',return_value={'present':True,'pending':True}),patch.object(r,'trusted_guest',return_value=['ssh']),patch.object(r,'network_context',return_value={}),patch.object(r,'guest_read',side_effect=read):
            rows=[];details={};r.workload(None,None,'project','user',rows,details,lambda _:None)
        status={row['check']:row['status'] for row in rows}
        self.assertEqual(status['VM in Nova'],'REVIEW');self.assertEqual(status['VM actually running'],'PASS')
        self.assertEqual(status['Saved file checksum'],'PASS');self.assertEqual(status['Migration record (preserved)'],'REVIEW')

    def test_no_running_vm_skips_ssh_without_starting_vm(self):
        vm={'ids':{'server':SERVER}};data={**EXPECTED,'backend':'node3'}
        current={'server_status':'SHUTOFF','task':None,'volume_attached':True,'backend':'node3'}
        with patch.object(r,'bindings',return_value=(vm,data,{})),patch.object(r,'api_workload',return_value=current),patch.object(r,'runtime',return_value={'observed':True,'running_hosts':[]}),patch.object(r,'pending_migration',return_value={'present':False}),patch.object(r,'trusted_guest',side_effect=AssertionError('No SSH to an unverified VM')):
            rows=[];details={};r.workload(None,None,'p','u',rows,details,lambda _:None)
        self.assertEqual(next(v['status'] for v in rows if v['check']=='VM actually running'),'REVIEW')
        self.assertIn('skipped',details['Guest checks']['issue'])

    def test_changed_guest_trust_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(r,'REPO',Path(directory)):
            path=Path(directory)/'private/phase4/lab-vm-01-known-hosts';path.parent.mkdir(parents=True)
            raw=b'192.168.0.195 ssh-ed25519 test-key\n';path.write_bytes(raw);path.chmod(0o600)
            with self.assertRaises(RuntimeError):r.trusted_guest({'host_fingerprint':'old'}, {},'192.168.0.194',lambda _:None)
            self.assertEqual(path.read_bytes(),raw)


class GuestDataTests(unittest.TestCase):
    def collect(self,mounted=True,serial=None,filesystem=FS,content=CONTENT):
        self.commands=[]
        disk={'name':'/dev/vdb','type':'disk','size':1024**3,'serial':serial or VOLUME[:20],'maj:min':'253:16'}
        class FakePath:
            def __init__(self,path):self.path=str(path)
            def read_text(self):return BOOT
            def is_symlink(self):return False
            def __truediv__(self,name):return FakePath(self.path+'/'+name)
            def lstat(self):
                if self.path.startswith('/dev/'):
                    return SimpleNamespace(st_mode=stat.S_IFBLK|0o660,st_rdev=os.makedev(253,16))
                return SimpleNamespace(st_mode=stat.S_IFREG|0o600,st_uid=0,st_nlink=1,st_size=len(content))
        def run(args):
            self.commands.append(args)
            if args[0]=='lsblk':return json.dumps({'blockdevices':[disk]})
            if args[0]=='blkid':return 'TYPE=ext4\nUUID='+filesystem+'\n'
            if args[0]=='findmnt':return json.dumps({'filesystems':[{'target':'/mnt/lab-data','fstype':'ext4','maj:min':'253:16'}]})
            raise AssertionError('Unexpected/mutating guest command: '+str(args))
        with patch.object(g.os,'geteuid',return_value=0),patch.object(g,'Path',FakePath),patch.object(g,'run',side_effect=run),patch.object(g.os.path,'ismount',return_value=mounted),patch.object(g.os,'open',return_value=999) as opened,patch.object(g.os,'read',return_value=content),patch.object(g.os,'close'):
            result=g.collect(EXPECTED)
            if opened.called:self.assertEqual(opened.call_args.args[1],os.O_RDONLY|os.O_NOFOLLOW)
        return result

    def test_mounted_volume_and_saved_data_match(self):
        result=self.collect();self.assertTrue(result['checksum_matches']);self.assertEqual(result['sha256'],EXPECTED['sha256'])
        self.assertTrue(result['disk_identified']);self.assertTrue(result['filesystem_matches'])
        self.assertTrue(all(c[0] in ('lsblk','blkid','findmnt') for c in self.commands))

    def test_unmounted_volume_is_not_mounted_or_declared_corrupt(self):
        result=self.collect(mounted=False)
        self.assertTrue(result['filesystem_matches']);self.assertFalse(result['mounted'])
        self.assertIsNone(result['checksum_matches']);self.assertIn('not mounted',result['issue'])
        self.assertFalse(any(c[0] in ('mount','mkfs.ext4','umount') for c in self.commands))

    def test_wrong_disk_and_wrong_filesystem_stop_before_data_read(self):
        result=self.collect(serial='another-disk');self.assertFalse(result['disk_identified'])
        self.assertEqual(len(self.commands),1)
        result=self.collect(filesystem=BOOT);self.assertFalse(result['filesystem_matches']);self.assertFalse(result['mounted'])

    def test_changed_data_fails_without_rewrite(self):
        result=self.collect(content=b'changed');self.assertFalse(result['checksum_matches']);self.assertIn('differs',result['issue'])

    def test_documentation_links_and_single_final_command(self):
        doc=(ROOT/'docs/reboot-and-recovery.md').read_text()
        self.assertIn('scripts/lab-reboot-verify.py --check',doc)
        self.assertTrue((ROOT/'docs/reboot-recovery-reference.md').is_file())
        self.assertIn('Neutron',doc);self.assertIn('compute-services --table',doc)


if __name__=='__main__':unittest.main()

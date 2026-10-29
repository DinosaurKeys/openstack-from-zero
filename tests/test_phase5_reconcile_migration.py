import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/file)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value
step = load('failed_attempt_test', 'phase5-reconcile-migration.py')
menu = load('reconciled_menu_test', 'lab-migrate.py')
SERVER = '0ef50722-c056-49fa-8f9e-05e926a8b41d'
MIGRATION = '79cac35a-6f30-4466-b470-bdcde2cd63fa'
OTHER = '11111111-1111-4111-8111-111111111111'
BOOT = '22222222-2222-4222-8222-222222222222'
URLS = {'compute':'https://192.168.0.151:8774/v2.1'}


def state():
    return {'plan':copy.deepcopy(step.move.PLAN),'attempted':True,'pending':True,
      'stage':'Nova accepted the migration request','server_id':SERVER,'plan_id':OTHER,
      'guest_boot_id':BOOT,'before_migration_ids':[],'project_id':'project','user_id':'user',
      'apis':copy.deepcopy(URLS),'inputs':{'input':'hash'},'storage_backend':'node3@lvm-1#lvm-1',
      'last_observation':{'server_status':'ERROR','host':'node1','task':None,'migration':{'uuid':MIGRATION,'status':'error'}}}


class API:
    def __init__(self):
        self.server={'id':SERVER,'tenant_id':'project','user_id':'user','status':'ACTIVE',
          'OS-EXT-SRV-ATTR:host':'node1','OS-EXT-STS:task_state':None}
        self.rows=[{'uuid':MIGRATION,'instance_uuid':SERVER,'status':'error',
          'source_compute':'node1','dest_compute':None,'migration_type':'live-migration'}]
        self.calls=[]
    def get(self, service, path):
        if (service,path)!=('compute','servers/'+SERVER):raise AssertionError('Unexpected API read')
        self.calls.append(('GET',path));return {'server':copy.deepcopy(self.server)}
    def history(self):self.calls.append(('GET','history'));return copy.deepcopy(self.rows)
    def move(self):raise AssertionError('No migration is allowed')
    def write(self,*args):raise AssertionError('No API write is allowed')


class ReconcileTests(unittest.TestCase):
    def folder(self,root):
        folder=root/'private/phase5';folder.mkdir(parents=True)
        path=folder/'move-vm-state.json';path.write_text(json.dumps(state(),indent=1)+'\n');path.chmod(0o600)
        return path
    def run_core(self,api,path,migration=MIGRATION,unchanged=lambda:None):
        lines=[];step.reconcile(api,path,migration,unchanged,lines.append);return lines
    def test_reported_failure_is_closed_without_changing_original_fields_or_requesting_a_move(self):
        with tempfile.TemporaryDirectory() as d:
            path=self.folder(Path(d));raw=path.read_bytes();before=json.loads(raw);api=API()
            with patch.dict(os.environ,{'SECRET':'PRIVATE_SENTINEL'}):lines=self.run_core(api,path)
            closed=json.loads(path.read_text());self.assertFalse(closed['pending']);self.assertTrue(closed['attempted'])
            self.assertEqual(closed['stage'],step.STAGE)
            for key,value in before.items():
                if key not in ('pending','stage'):self.assertEqual(closed[key],value)
            resolution=closed['failed_migration_resolution'];archive=path.parent/resolution['original_record']
            self.assertEqual(archive.read_bytes(),raw);self.assertEqual(archive.stat().st_mode&0o777,0o600)
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertEqual(resolution['observed_api']['migration_status'],'error')
            self.assertEqual(len(api.calls),4);self.assertTrue(all(c[0]=='GET' for c in api.calls))
            self.assertIn('RESULT: FAILED_MIGRATION_RECONCILED',lines)
            self.assertNotIn('PRIVATE_SENTINEL','\n'.join(lines));self.assertNotIn('VERIFIED','\n'.join(lines))
            menu.pending_guard(path.parent,server=SERVER)
    def test_active_unknown_completed_foreign_or_ambiguous_migrations_leave_checkpoint_unchanged(self):
        cases=[]
        for key,value in [('status','running'),('status','completed'),('status',None),('source_compute','node3'),('dest_compute','node3'),('migration_type','migration'),('uuid',OTHER),('instance_uuid',OTHER)]:
            api=API();api.rows[0][key]=value;cases.append(api)
        api=API();api.rows=[];cases.append(api)
        api=API();api.rows*=2;cases.append(api)
        api=API();api.rows.append(dict(api.rows[0],uuid=OTHER));cases.append(api)
        api=API();api.rows*=100;cases.append(api)
        for api in cases:
            with self.subTest(rows=api.rows[:2]),tempfile.TemporaryDirectory() as d:
                path=self.folder(Path(d));raw=path.read_bytes()
                with self.assertRaises(RuntimeError):self.run_core(api,path)
                self.assertEqual(path.read_bytes(),raw);self.assertFalse((path.parent/'migration-history').exists())
    def test_vm_error_busy_wrong_host_and_identity_do_not_close_a_checkpoint(self):
        for key,value in [('status','ERROR'),('status','SHUTOFF'),('OS-EXT-STS:task_state','migrating'),('OS-EXT-SRV-ATTR:host','node2'),('id',OTHER),('tenant_id','different'),('user_id','different')]:
            with self.subTest(key=key),tempfile.TemporaryDirectory() as d:
                path=self.folder(Path(d));raw=path.read_bytes();api=API();api.server[key]=value
                with self.assertRaises(RuntimeError):self.run_core(api,path)
                self.assertEqual(path.read_bytes(),raw)
    def test_api_failure_and_changed_evidence_do_not_write_an_archive_or_clear_pending(self):
        for change in ('timeout','task'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as d:
                path=self.folder(Path(d));raw=path.read_bytes();api=API();original=api.get
                def query(*args):
                    if api.calls:
                        if change=='timeout':raise RuntimeError('API unavailable')
                        api.server['OS-EXT-STS:task_state']='migrating'
                    return original(*args)
                api.get=query
                with self.assertRaises(RuntimeError):self.run_core(api,path)
                self.assertEqual(path.read_bytes(),raw);self.assertFalse((path.parent/'migration-history').exists())
    def test_changed_inputs_or_checkpoint_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            path=self.folder(Path(d));raw=path.read_bytes()
            def changed():raise RuntimeError('inputs changed')
            with self.assertRaises(RuntimeError):self.run_core(API(),path,unchanged=changed)
            self.assertEqual(path.read_bytes(),raw)
            def edited():path.write_text('concurrent edit')
            with self.assertRaises(RuntimeError):self.run_core(API(),path,unchanged=edited)
            self.assertEqual(path.read_text(),'concurrent edit')
    def test_interrupted_local_write_preserves_pending_and_can_reuse_the_identical_original_copy(self):
        with tempfile.TemporaryDirectory() as d:
            path=self.folder(Path(d));raw=path.read_bytes()
            with patch.object(step.move.net,'write_state',side_effect=OSError('write unavailable')):
                with self.assertRaises(OSError):self.run_core(API(),path)
            self.assertEqual(path.read_bytes(),raw)
            originals=list((path.parent/'migration-history').glob('*.json'));self.assertEqual(len(originals),1)
            self.assertEqual(originals[0].read_bytes(),raw)
            self.run_core(API(),path);self.assertEqual(len(list((path.parent/'migration-history').glob('*.json'))),1)
    def test_repeat_is_idempotent_without_more_api_reads_or_local_changes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path=self.folder(root);self.run_core(API(),path)
            before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()};api=API()
            self.run_core(api,path);self.assertEqual(api.calls,[])
            self.assertEqual(before,{p:p.read_bytes() for p in root.rglob('*') if p.is_file()})
            with self.assertRaises(RuntimeError):self.run_core(api,path,migration=OTHER)
    def test_symlinked_or_corrupted_original_evidence_is_not_accepted(self):
        for kind in ('symlink','corrupted','missing'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as d:
                root=Path(d);path=self.folder(root);self.run_core(API(),path);raw=path.read_bytes()
                archived=next((path.parent/'migration-history').glob('*.json'))
                if kind=='corrupted':archived.write_text('changed')
                else:
                    data=archived.read_bytes();archived.unlink()
                    if kind=='symlink':
                        other=root/'other';other.write_bytes(data);other.chmod(0o600);archived.symlink_to(other)
                with self.assertRaises((RuntimeError,ValueError)):self.run_core(API(),path)
                self.assertEqual(path.read_bytes(),raw)
    def test_original_pending_false_without_resolution_and_uncertain_receipts_are_rejected(self):
        for pending in (False,None,'unknown'):
            with self.subTest(pending=pending),tempfile.TemporaryDirectory() as d:
                path=self.folder(Path(d));value=state();value['pending']=pending;path.write_text(json.dumps(value));raw=path.read_bytes()
                with self.assertRaises(RuntimeError):self.run_core(API(),path)
                self.assertEqual(path.read_bytes(),raw)
    def test_reconciliation_uses_the_existing_transport_with_migration_disabled(self):
        client=SimpleNamespace(opener=SimpleNamespace(open=lambda *a,**k:(_ for _ in ()).throw(AssertionError('No HTTP mutation'))))
        api=step.move.API(client,URLS,'PRIVATE_TOKEN',SERVER,'node2',allow_move=False)
        for method,body in [('POST',{'os-migrateLive':{'host':'node2','block_migration':True}}),('DELETE',None),('PUT',{})]:
            with self.assertRaises(RuntimeError):api.request('compute',method,'servers/'+SERVER+'/action',body,202)
        with self.assertRaises(RuntimeError):api.move()
    def test_execute_binds_current_authentication_and_preserves_other_private_records(self):
        for project in ('project','different'):
            with self.subTest(project=project),tempfile.TemporaryDirectory() as d:
                root=Path(d);path=self.folder(root);raw=path.read_bytes();other=root/'private/phase4/vm.json';other.parent.mkdir();other.write_text('{"pending":false}');other.chmod(0o600)
                original=SimpleNamespace(client=None,urls=URLS,token='PRIVATE_TOKEN');api=API();lines=[]
                with patch.object(step,'REPO',root),patch.object(step.deploy,'source_guard'),patch.object(step.deploy.tools,'tool_environment',return_value={}),patch.object(step.deploy.validation,'local_checks',return_value=(root/'inventory',root/'config')),patch.object(step.move.access,'input_hashes',return_value={'input':'hash'}),patch.object(step.move.prep,'authenticate',return_value=(original,project,'user')),patch.object(step.move,'API',return_value=api) as factory:
                    if project=='project':
                        step.execute(lines.append,MIGRATION);self.assertFalse(json.loads(path.read_text())['pending']);self.assertFalse(factory.call_args.kwargs['allow_move'])
                    else:
                        with self.assertRaises(RuntimeError):step.execute(lines.append,MIGRATION)
                        self.assertEqual(path.read_bytes(),raw);factory.assert_not_called()
                self.assertEqual(other.read_text(),'{"pending":false}');self.assertNotIn('PRIVATE_TOKEN','\n'.join(lines))
    def test_default_plan_is_isolated_and_apply_requires_the_reviewed_migration_uuid(self):
        with patch.object(step,'execute') as execute,patch.object(step.deploy.tools,'check_paths') as paths,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(step.main([]),0);execute.assert_not_called();paths.assert_not_called()
        for args in (['--apply'],['--apply','--migration-id','invalid']):
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):step.main(args)


if __name__=='__main__':unittest.main()

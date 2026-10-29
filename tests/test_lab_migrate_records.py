import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
t=load('record_menu_fixtures',ROOT/'tests/test_lab_migrate.py');m=t.m
r=load('record_review',ROOT/'scripts/lab-migrate-records.py')
VM=t.VM;MIG=t.MIG;PROJECT=t.PROJECT
LATER='b32ad679-ec64-4ed7-b532-0a3cd8b91a48'


def fixture(folder,manual=True):
    api=t.API();api.read_only=True
    plan=t.compute_plan(api,dest='node3')
    state={'pending':True,'server_id':VM,'volume_id':None,'plan':plan,'attempts':['move'],'stage':'move outcome pending'}
    path=folder/(m.PREFIX+'20261029T205154Z-fixture.json')
    path.write_text(json.dumps(state,indent=3)+'\n');path.chmod(0o600)
    api.history=[{'uuid':MIG,'instance_uuid':VM,'source_compute':'node1','dest_compute':None,
      'status':'error','migration_type':'live-migration','created_at':'2026-10-29T20:55:19.000000'}]
    if manual:
        api.server['OS-EXT-SRV-ATTR:host']='node3'
        api.history.append({'uuid':LATER,'instance_uuid':VM,'source_compute':'node1','dest_compute':'node3',
          'status':'completed','migration_type':'live-migration','created_at':'2026-10-29T21:30:00.000000'})
    return api,path,state


class MigrationRecordTests(unittest.TestCase):
    def test_observe_reports_horizon_placement_and_preserves_pending_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            api,path,state=fixture(Path(folder));raw=path.read_bytes();logs=[]
            with patch.object(m.getpass,'getpass',side_effect=AssertionError('Observation must not ask for sudo')):
                self.assertEqual(r.run(m,api,PROJECT,Path(folder),logs.append,read=lambda _:'1'),0)
            self.assertEqual(path.read_bytes(),raw);self.assertFalse(api.actions)
            self.assertIn('VM: custom VM name | Nova ACTIVE | host node3',logs)
            self.assertTrue(any(LATER in line and 'completed' in line for line in logs))
            self.assertTrue(any('PENDING' in line for line in logs))

    def test_explicit_acknowledgement_archives_original_and_keeps_manual_success_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);api,path,state=fixture(root);raw=path.read_bytes();answers=iter(['1','ACKNOWLEDGE FAILED ATTEMPT']);logs=[]
            self.assertEqual(r.run(m,api,PROJECT,root,logs.append,reconcile=True,read=lambda _:next(answers)),0)
            saved=json.loads(path.read_text());resolution=saved['failed_attempt_resolution']
            self.assertFalse(saved['pending']);self.assertEqual(saved['stage'],r.STAGE)
            archive=root/resolution['original_record']
            self.assertEqual(archive.read_bytes(),raw);self.assertEqual(archive.stat().st_mode&0o777,0o600)
            self.assertEqual(resolution['observed_api']['failed_migration_id'],MIG)
            self.assertEqual(resolution['observed_api']['later_completed_migration_id'],LATER)
            for key in state:
                if key not in ('pending','stage'):self.assertEqual(saved[key],state[key])
            self.assertFalse(api.actions);self.assertEqual(api.history[0]['status'],'error')
            self.assertIn('VM: custom VM name | '+VM,logs)
            self.assertIn('Local record: '+path.name,logs)
            self.assertIn('RESULT: FAILED_MENU_ATTEMPT_ACKNOWLEDGED',logs)
            before=path.read_bytes();r.run(m,api,PROJECT,root,logs.append,reconcile=True,read=lambda _:self.fail('Already closed'))
            self.assertEqual(path.read_bytes(),before)

    def test_recovered_original_source_can_acknowledge_terminal_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            api,path,state=fixture(Path(folder),manual=False)
            value=r.evidence(m,api,state,PROJECT)
            self.assertEqual(value['host'],'node1');self.assertIsNone(value['later_completed_migration_id'])

    def test_definite_http_rejection_without_a_migration_can_be_acknowledged(self):
        with tempfile.TemporaryDirectory() as folder:
            api,path,state=fixture(Path(folder),manual=False);api.history=[];state['http_statuses']={'move':409}
            value=r.evidence(m,api,state,PROJECT)
            self.assertIsNone(value['failed_migration_id']);self.assertEqual(value['failed_status'],'HTTP 409 rejected')
            for code in (None,202,500):
                state['http_statuses']['move']=code
                with self.assertRaises(RuntimeError):r.evidence(m,api,state,PROJECT)

    def test_declining_acknowledgement_changes_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);api,path,_=fixture(root);raw=path.read_bytes();answers=iter(['1',''])
            r.run(m,api,PROJECT,root,lambda _:None,reconcile=True,read=lambda _:next(answers))
            self.assertEqual(path.read_bytes(),raw);self.assertFalse((root/'migration-history').exists());self.assertFalse(api.actions)

    def test_ambiguous_active_foreign_or_unexplained_states_cannot_close(self):
        edits=[lambda a,s:a.server.update(status='ERROR'),
          lambda a,s:a.server.update({'OS-EXT-STS:task_state':'migrating'}),
          lambda a,s:a.server.update(tenant_id='another-project'),
          lambda a,s:a.history[1].update(status='running'),
          lambda a,s:a.history.pop(),
          lambda a,s:a.history[1].update(source_compute='node2'),
          lambda a,s:a.history.append(dict(a.history[0],uuid='14867731-6f66-4781-9d05-1d3db6bc7b6a')),
          lambda a,s:a.volume.update({'os-vol-host-attr:host':'node2@lvm-1#lvm-1'}),
          lambda a,s:a.history[0].update(migration_type='migration'),
          lambda a,s:s['plan']['before_migrations'].append('14867731-6f66-4781-9d05-1d3db6bc7b6a')]
        for edit in edits:
            with self.subTest(edit=edit),tempfile.TemporaryDirectory() as folder:
                api,path,state=fixture(Path(folder));raw=path.read_bytes();edit(api,state)
                with self.assertRaises(RuntimeError):r.evidence(m,api,state,PROJECT)
                self.assertEqual(path.read_bytes(),raw);self.assertFalse(api.actions)

    def test_changed_snapshot_or_record_is_not_overwritten(self):
        for change in ('api','record'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);api,path,_=fixture(root);raw=path.read_bytes();count=0
                def read(_):
                    nonlocal count
                    count+=1
                    if count==1:return '1'
                    if change=='api':api.server['OS-EXT-STS:task_state']='migrating'
                    else:path.write_text('{"changed":true}')
                    return 'ACKNOWLEDGE FAILED ATTEMPT'
                with self.assertRaises(RuntimeError):r.run(m,api,PROJECT,root,lambda _:None,reconcile=True,read=read)
                self.assertEqual(path.read_bytes(),raw if change=='api' else b'{"changed":true}')
                self.assertFalse((root/'migration-history').exists());self.assertFalse(api.actions)

    def test_input_hash_mismatch_and_other_record_change_stop_acknowledgement(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);api,path,state=fixture(root);state['inputs']={'globals':'old'}
            path.write_text(json.dumps(state));raw=path.read_bytes()
            with self.assertRaisesRegex(RuntimeError,'inputs differ'):
                r.run(m,api,PROJECT,root,lambda _:None,reconcile=True,current_inputs={'globals':'new'},read=lambda _:'1')
            self.assertEqual(path.read_bytes(),raw)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);api,path,_=fixture(root);raw=path.read_bytes();other=root/(m.PREFIX+'other.json')
            other.write_text('{"pending":false}');other.chmod(0o600);answers=iter(['1','ACKNOWLEDGE FAILED ATTEMPT'])
            def unchanged():other.write_text('{"changed":true}')
            with self.assertRaisesRegex(RuntimeError,'Another menu record changed'):
                r.run(m,api,PROJECT,root,lambda _:None,reconcile=True,unchanged=unchanged,read=lambda _:next(answers))
            self.assertEqual(path.read_bytes(),raw);self.assertFalse(api.actions)

    def test_archive_survives_local_save_failure_and_can_be_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);api,path,_=fixture(root);raw=path.read_bytes()
            with patch.object(m.health.prep.net,'write_state',side_effect=OSError('fixture write failure')):
                answers=iter(['1','ACKNOWLEDGE FAILED ATTEMPT'])
                with self.assertRaises(OSError):r.run(m,api,PROJECT,root,lambda _:None,reconcile=True,read=lambda _:next(answers))
            self.assertEqual(path.read_bytes(),raw)
            self.assertEqual(next((root/'migration-history').glob('*.json')).read_bytes(),raw)
            answers=iter(['1','ACKNOWLEDGE FAILED ATTEMPT'])
            r.run(m,api,PROJECT,root,lambda _:None,reconcile=True,read=lambda _:next(answers))
            self.assertFalse(json.loads(path.read_text())['pending']);self.assertFalse(api.actions)

    def test_symlink_and_non_readonly_client_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);api,path,_=fixture(root);target=root/'original.json';path.rename(target);path.symlink_to(target)
            with self.assertRaises(RuntimeError):r.records(m,root)
            api.read_only=False
            with self.assertRaisesRegex(RuntimeError,'GET-only'):r.run(m,api,PROJECT,root,lambda _:None)


if __name__=='__main__':unittest.main()

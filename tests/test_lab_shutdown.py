import copy
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def load(name,file):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/file)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
s=load('shutdown_test','lab-shutdown.py');n=load('shutdown_node_test','lab-shutdown-node.py')
UUID='f3609644-d1e0-11f1-a749-0ef43e7d82dc'
def records():
    return {node:{'host':{'node':node,'observed':True,
      'galera':{'observed':True,'value':{'wsrep_cluster_state_uuid':UUID,'wsrep_cluster_size':'3',
      'wsrep_cluster_status':'Primary','wsrep_local_state_comment':'Synced','wsrep_connected':'ON','wsrep_ready':'ON'}},
      'rabbitmq':{'observed':True,'value':{'healthy':True}},'containers':{'observed':True,'value':{'healthy':True}}},
      'active_domains':[],'running':['mariadb','rabbitmq','nova_compute']} for node in s.NODES}
def positions():
    return {node:{'uuid':UUID,'seqno':42,'exit_code':0,'safe_to_bootstrap':int(node=='node3')} for node in s.NODES}

class ShutdownTests(unittest.TestCase):
    def test_baseline(self):self.assertEqual(s.baseline(records()),UUID)
    def test_running_or_paused_guest_blocks(self):
        r=records();r['node1']['active_domains']=['guest']
        self.assertRaisesRegex(RuntimeError,'still active',s.baseline,r)
    def test_missing_node_blocks(self):
        r=records();del r['node2'];self.assertRaises(RuntimeError,s.baseline,r)
    def test_unhealthy_db_and_broker_block(self):
        for key in ('galera','rabbitmq','containers'):
            r=records();r['node2']['host'][key]={}
            self.assertRaises(RuntimeError,s.baseline,r)
    def test_different_cluster_blocks(self):
        r=records();r['node2']['host']['galera']['value']['wsrep_cluster_state_uuid']='different'
        self.assertRaises(RuntimeError,s.baseline,r)
    def test_unknown_container_blocks(self):
        r=records();r['node2']['running'].append('postgres')
        self.assertRaises(RuntimeError,s.baseline,r)
    def test_check_reports_exact_names_on_every_node(self):
        r=records();r['node1']['running']+=['actual_extra_1'];r['node3']['running']+=['actual_extra_3']
        ready,lines=s.check_lines(r);text='\n'.join(lines)
        self.assertFalse(ready)
        self.assertIn('node1: container names outside the shutdown list: actual_extra_1',text)
        self.assertIn('node3: container names outside the shutdown list: actual_extra_3',text)
        self.assertIn('node2 | OK | OK | OK | 0',text)
        self.assertIn('No services or hosts were stopped by this check.',text)
        self.assertNotIn('Stopped:',text)
        self.assertIn('SHUTDOWN_CHECK_NEEDS_REVIEW',text)
    def test_inspection_continues_after_node_error_without_mutation(self):
        calls=[];data=records()
        def remote(node,action):
            calls.append((node,action))
            if node=='node1':raise RuntimeError('node1: inspection unavailable')
            return data[node]
        result=s.collect_checks(remote)
        self.assertEqual(calls,[(node,'inspect') for node in s.NODES])
        self.assertEqual(set(result),set(s.NODES))
        ready,lines=s.check_lines(result)
        self.assertFalse(ready);self.assertIn('node1 | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN',lines)
    def test_check_guard_blocks_every_mutating_remote_action(self):
        s.request_guard(True,'inspect')
        for action in ('routers','poweroff','database','broker','running','restart','stop'):
            self.assertRaises(RuntimeError,s.request_guard,True,action)
    def test_unknown_names_are_never_automatically_approved(self):
        self.assertFalse(s.allowed('actual_extra_1'))
        self.assertFalse(s.allowed('arbitrary_kolla_name'))
    def test_check_success_is_explicitly_read_only(self):
        ready,lines=s.check_lines(records())
        self.assertTrue(ready);self.assertIn('RESULT: SHUTDOWN_READINESS_CHECKED',lines)
        self.assertTrue(lines[0].startswith('CHECK ONLY'))
    def test_safe_positions(self):s.positions(positions(),UUID)
    def test_wrong_last_or_stale_or_unclean_blocks(self):
        for field,value in [('safe_to_bootstrap',0),('seqno',-1),('uuid','different'),('exit_code',137)]:
            r=positions();r['node3'][field]=value;self.assertRaises(RuntimeError,s.positions,r,UUID)
        r=positions();r['node1']['seqno']=43;self.assertRaises(RuntimeError,s.positions,r,UUID)
    def test_commands_never_destroy_or_ignore_missing(self):
        args=s.stop_args('/config','/inventory','/private/options')
        self.assertEqual(args[1],'stop');self.assertNotIn('destroy',args);self.assertNotIn('--ignore-missing',args)
        self.assertIn(json.dumps({'skip_stop_containers':['mariadb','rabbitmq']}),args)
        args=s.stop_args('/config','/inventory','/private/options','mariadb','node2')
        self.assertEqual(args[args.index('--limit')+1],'node2');self.assertEqual(args[-2:],['--tags','mariadb'])
    def workflow(self,fail=False,remaining=False,bad_db=False):
        calls=[];saves=[];db=positions()
        if bad_db:db['node3']['safe_to_bootstrap']=0
        def remote(node,action):
            calls.append((node,action))
            if action=='running':return {'running':['mariadb','rabbitmq'] if len([c for c in calls if c[1]=='database'])==0 else []}
            if action=='database':return db[node]
            return {}
        def stop(service,node):
            self.assertTrue(saves[-1]['pending']);self.assertTrue(saves[-1]['attempts'])
            calls.append(('stop',service,node))
            if fail:raise RuntimeError('lost response')
        if remaining:
            original=remote
            def remote(node,action):
                if action=='running':return {'running':['mariadb','rabbitmq','nova_compute']}
                return original(node,action)
        result=s.workflow(remote,stop,lambda v:saves.append(copy.deepcopy(v)),lambda _:None,UUID)
        return result,calls,saves
    def test_dependency_order_and_no_poweroff_in_stop_workflow(self):
        result,calls,saves=self.workflow()
        self.assertEqual([c for c in calls if c[0]=='stop'],[('stop',None,None)]+[('stop','rabbitmq',n) for n in s.NODES]+[('stop','mariadb',n) for n in s.NODES])
        self.assertFalse(any(c[1]=='poweroff' for c in calls));self.assertTrue(result['pending'])
        self.assertEqual(set(result['database']),set(s.NODES))
    def test_lost_outcome_never_retries(self):self.assertRaisesRegex(RuntimeError,'lost response',self.workflow,fail=True)
    def test_leftover_or_bad_database_blocks(self):
        self.assertRaises(RuntimeError,self.workflow,remaining=True)
        self.assertRaises(RuntimeError,self.workflow,bad_db=True)
    def test_stopped_rejects_kill_or_oom_or_running(self):
        good={'Running':False,'Status':'exited','ExitCode':0,'OOMKilled':False}
        for changes in ({'Running':True},{'ExitCode':137},{'OOMKilled':True},{'Status':'dead'}):
            with patch.object(n,'run',return_value=json.dumps(good|changes)):
                self.assertRaises(RuntimeError,n.stopped,'mariadb')
    def test_read_stopped_grastate_without_host_write(self):
        buffer=io.BytesIO();content=f'uuid: {UUID}\nseqno: 42\nsafe_to_bootstrap: 1\n'.encode()
        with tarfile.open(fileobj=buffer,mode='w') as tar:
            info=tarfile.TarInfo('grastate.dat');info.size=len(content);tar.addfile(info,io.BytesIO(content))
        with patch.object(n,'stopped',return_value={'exit_code':0}),patch.object(n,'run',return_value=buffer.getvalue()) as run:
            self.assertEqual(n.database()['seqno'],42)
            self.assertEqual(run.call_args.args[0],['docker','cp','mariadb:/var/lib/mysql/grastate.dat','-'])
    def test_router_stop_blocks_while_l3_running(self):
        with patch.object(n,'guard'),patch.object(n,'running',return_value=['mariadb','rabbitmq','neutron_l3_agent']),patch.object(n,'run') as run:
            self.assertRaises(RuntimeError,n.action,'node1','routers');run.assert_not_called()
    def test_poweroff_requires_no_containers_and_clean_database(self):
        with patch.object(n,'guard'),patch.object(n,'running',return_value=['mariadb']),patch.object(n,'run') as run:
            self.assertRaises(RuntimeError,n.action,'node1','poweroff');run.assert_not_called()
        with patch.object(n,'guard'),patch.object(n,'running',return_value=[]),patch.object(n,'database',side_effect=RuntimeError('bad')),patch.object(n,'run') as run:
            self.assertRaises(RuntimeError,n.action,'node1','poweroff');run.assert_not_called()
    def test_help_default_has_no_lab_access(self):
        with patch.object(s.health.inv,'ensure_agent') as agent,patch('builtins.print'):
            self.assertEqual(s.main([]),0);agent.assert_not_called()

if __name__=='__main__':unittest.main()

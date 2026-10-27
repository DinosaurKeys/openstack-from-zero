import ast
import base64
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
from string import Template
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
step=load('db_test','phase5-mariadb.py');node=load('db_node_test','phase5-mariadb-node.py')
IDS={n:str(i)*64 for i,n in enumerate(step.inv.NODES,1)}
UUID='f3609644-d1e0-11f1-a749-0ef43e7d82dc';SERVER='0ef50722-c056-49fa-8f9e-05e926a8b41d'


def records(stopped=None):
    count=2 if stopped else 3
    return {n:{'node':n,'vip_owner':n=='node3','mariadb':{'id':IDS[n],'running':n!=stopped,'health':'healthy'},
        'unit':{'active':'inactive' if n==stopped else 'active'},'haproxy':{'running':True,'health':'healthy'},
        'galera':None if n==stopped else {'wsrep_cluster_status':'Primary','wsrep_cluster_size':str(count),'wsrep_local_state_comment':'Synced','wsrep_connected':'ON','wsrep_ready':'ON','wsrep_cluster_state_uuid':UUID},
        'baseline':{'ready':True,'facts':{'protected_config_hashes':{'config':'same'}}}} for n in step.inv.NODES}


def plan():
    return {'plan':step.PLAN,'id':'12345678-abcd-4321-8321-0123456789ab','target':'node2','container_id':IDS['node2'],'container_ids':IDS,
        'prepared_unix':1000,'inputs':{'x':'y'},'project_id':'project','user_id':'user','server_id':SERVER,'cluster_uuid':UUID,'api_owner':'node3'}


class Nodes:
    def __init__(self):self.stopped=None;self.calls=[];self.fail_stop=False;self.fail_start=False;self.nonprimary=False;self.extra_loss=False
    def all(self,action='observe'):
        value=records(self.stopped)
        if self.stopped and self.nonprimary:value['node1']['galera']['wsrep_cluster_status']='non-Primary'
        if self.stopped and self.extra_loss:value['node1']['mariadb']['running']=False
        return value
    def call(self,target,action,cid):
        self.calls.append((target,action,cid))
        if action=='stop':
            self.stopped=target
            if self.fail_stop:raise RuntimeError('Stop response lost')
        else:
            if self.fail_start:raise RuntimeError('Start failed')
            self.stopped=None
        return self.all()[target]


class Sampler:
    def __init__(self,check):self.records=[]
    def start(self):
        now=step.time.monotonic();self.records=[{'started':now,'finished':now+0.1,'ok':True,'duration_seconds':0.1}]
    def stop(self):pass


class ExerciseTests(unittest.TestCase):
    def setUp(self):self.nodes=Nodes();self.saved=[];self.logs=[]
    def exercise(self):
        with patch.object(step.access,'check_apis',return_value={}),patch.object(step.vip,'probe',return_value={'ok':True}),patch.object(step.time,'sleep'):
            return step.exercise(self.nodes,plan(),{},SimpleNamespace(horizon=lambda:{'https_verified':True,'login_form_present':True}),SERVER,
                lambda v:self.saved.append(copy.deepcopy(v)),self.logs.append,Sampler)
    def test_success_two_members_then_same_three_restored(self):
        result=self.exercise()
        self.assertEqual(self.nodes.calls,[('node2','stop',IDS['node2']),('node2','start',IDS['node2'])])
        self.assertEqual(result['survivors'],['node1','node3']);self.assertIn('RESULT: MARIADB_HA_VERIFIED',self.logs)
        self.assertTrue(self.saved[0]['restore_required']);self.assertFalse(self.saved[-1]['restore_required'])
        self.assertTrue(any('"wsrep_cluster_size": "2"' in line for line in self.logs))
    def test_uncertain_stop_still_starts_original_member(self):
        self.nodes.fail_stop=True
        with self.assertRaisesRegex(RuntimeError,'Stop response lost'):self.exercise()
        self.assertEqual(self.nodes.calls[-1][1],'start');self.assertFalse(self.saved[-1]['restore_required'])
        self.assertNotIn('RESULT: MARIADB_HA_VERIFIED',self.logs)
    def test_start_failure_preserves_restore_required(self):
        self.nodes.fail_start=True
        with self.assertRaisesRegex(RuntimeError,'restoration/synchronization'):self.exercise()
        self.assertTrue(self.saved[-1]['restore_required'])
    def test_survivor_not_primary_or_second_member_lost_restores_no_extra_stop(self):
        for key in ('nonprimary','extra_loss'):
            self.setUp();setattr(self.nodes,key,True)
            with self.subTest(key=key),self.assertRaises(RuntimeError):self.exercise()
            self.assertEqual([v[1] for v in self.nodes.calls],['stop','start'])
    def test_baseline_failure_has_no_mutation(self):
        bad=records();bad['node1']['galera']['wsrep_cluster_size']='2'
        with patch.object(self.nodes,'all',return_value=bad),self.assertRaises(RuntimeError):self.exercise()
        self.assertFalse(self.nodes.calls);self.assertFalse(self.saved)
    def test_api_failure_with_two_members_still_restores(self):
        calls=[]
        def check(*args):
            calls.append(1)
            if len(calls)==2:raise RuntimeError('API read failed with two members')
        with patch.object(step.access,'check_apis',side_effect=check),self.assertRaisesRegex(RuntimeError,'API read failed'):
            step.exercise(self.nodes,plan(),{},SimpleNamespace(horizon=lambda:{'https_verified':True,'login_form_present':True}),SERVER,
                lambda v:self.saved.append(copy.deepcopy(v)),self.logs.append,Sampler)
        self.assertEqual(self.nodes.calls[-1][1],'start');self.assertFalse(self.saved[-1]['restore_required'])
    def test_interruption_restores(self):
        original=self.nodes.call
        def call(target,action,cid):
            if action=='stop':raise KeyboardInterrupt
            return original(target,action,cid)
        with patch.object(self.nodes,'call',side_effect=call),self.assertRaisesRegex(RuntimeError,'KeyboardInterrupt'):self.exercise()
        self.assertEqual(self.nodes.calls[-1][1],'start')
    def test_no_stop_if_receipt_cannot_be_saved(self):
        with patch.object(step.access,'check_apis',return_value={}),self.assertRaises(OSError):
            step.exercise(self.nodes,plan(),{},SimpleNamespace(horizon=lambda:{'https_verified':True,'login_form_present':True}),SERVER,
                lambda value:(_ for _ in ()).throw(OSError()),self.logs.append,Sampler)
        self.assertFalse(self.nodes.calls)
    def test_wrong_container_blocks_before_stop(self):
        bad=records();bad['node2']['mariadb']['id']='f'*64
        with patch.object(self.nodes,'all',return_value=bad),self.assertRaises(RuntimeError):self.exercise()
        self.assertFalse(self.nodes.calls)


class ClusterTests(unittest.TestCase):
    def test_three_and_two_member_states(self):
        self.assertEqual(step.cluster(records(),3),(True,UUID,'node3'))
        self.assertEqual(step.cluster(records('node2'),2,'node2'),(True,UUID,'node3'))
        self.assertFalse(step.cluster(records(),2)[0])
    def test_uuid_mismatch_duplicates_and_incomplete_observations_rejected(self):
        wrong=records();wrong['node1']['galera']['wsrep_cluster_state_uuid']='12345678-abcd-4321-8321-0123456789ab'
        with self.assertRaises(RuntimeError):step.cluster(wrong,3,expected_uuid=UUID)
        self.assertFalse(step.cluster(wrong,3)[0])
        duplicate=records();duplicate['node1']['vip_owner']=True
        with self.assertRaises(RuntimeError):step.cluster(duplicate,3)
        with self.assertRaises(RuntimeError):step.cluster({},3)
    def test_rejoining_unknown_uuid_is_not_accepted_but_can_be_observed_again(self):
        value=records();value['node2']['galera']['wsrep_cluster_state_uuid']='00000000-0000-0000-0000-000000000000'
        self.assertFalse(step.cluster(value,3,expected_uuid=UUID)[0])
    def test_plan_target_expiry_and_binding(self):
        value=plan();binding={k:value[k] for k in ('inputs','project_id','user_id','server_id')};step.validate_plan(value,binding,1100)
        for bad in (dict(value,target='foreign'),dict(value,prepared_unix=-2000),dict(value,inputs={}),dict(value,container_ids={}),dict(value,container_id='x')):
            with self.assertRaises(RuntimeError):step.validate_plan(bad,binding,1100)
    def test_timeout_does_not_inject_another_fault(self):
        value=records();value['node2']['galera']['wsrep_local_state_comment']='Joining'
        with patch.object(step.time,'sleep'),self.assertRaisesRegex(RuntimeError,'timed out'):
            step.wait_cluster(SimpleNamespace(all=lambda:value),3,plan(),None,-1)
    def test_default_and_node_selection_do_not_connect(self):
        with patch.object(step,'execute',side_effect=AssertionError),contextlib.redirect_stdout(io.StringIO()) as out:self.assertEqual(step.main([]),0)
        self.assertIn('PLAN ONLY',out.getvalue())
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):step.main(['--prepare'])
            with self.assertRaises(SystemExit):step.main(['--apply','--node','node2'])
    def test_restore_command_uses_management_service_only(self):
        cmd=step.restore_command(plan());self.assertIn('192.168.0.201',cmd);self.assertNotIn('192.168.0.151',cmd)
        self.assertIn('sudo systemctl start kolla-mariadb-container.service',cmd);self.assertNotIn('bootstrap',cmd)


class NodeTests(unittest.TestCase):
    def setUp(self):self.item=records()['node2'];self.item['mariadb'].update(name='/mariadb',image=node.IMAGE)
    def invoke(self,action,cid=None):
        with patch.object(node.os,'geteuid',return_value=0),patch.dict(node.os.environ,{'SUDO_USER':'joe'}),patch.object(node.socket,'gethostname',return_value='node2'):
            return node.execute('node2',action,cid)
    def test_exact_target_stop_does_not_require_api_vip_ownership(self):
        after=records('node2')['node2'];after['mariadb'].update(name='/mariadb',image=node.IMAGE)
        with patch.object(node,'collect',side_effect=[self.item,after]),patch.object(node,'run') as run:self.invoke('stop',IDS['node2'])
        run.assert_called_once_with(['systemctl','stop','kolla-mariadb-container.service'],timeout=90)
    def test_unsynchronized_member_wrong_id_and_wrong_user_refused(self):
        for change in ('not-synced','wrong-id'):
            item=copy.deepcopy(self.item)
            if change=='not-synced':item['galera']['wsrep_cluster_size']='2'
            with patch.object(node,'collect',return_value=item),patch.object(node,'run') as run,self.assertRaises(RuntimeError):self.invoke('stop',IDS['node1'] if change=='wrong-id' else IDS['node2'])
            run.assert_not_called()
        with patch.object(node.os,'geteuid',return_value=1000),self.assertRaises(RuntimeError):node.execute('node2','stop',IDS['node2'])
    def test_start_same_container_and_no_bootstrap(self):
        stopped=records('node2')['node2'];stopped['mariadb'].update(name='/mariadb',image=node.IMAGE)
        with patch.object(node,'collect',side_effect=[stopped,self.item]),patch.object(node,'run') as run:self.invoke('start',IDS['node2'])
        run.assert_called_once_with(['systemctl','start','kolla-mariadb-container.service'],timeout=90)
        with patch.object(node,'collect',return_value=self.item),patch.object(node,'run') as run:self.invoke('start',IDS['node2'])
        run.assert_not_called()
    def test_pinned_actual_service_template_default_timeout(self):
        import yaml
        folder=ROOT/'build/real-tools-check/share/kolla-ansible/ansible'
        tree=ast.parse((folder/'module_utils/kolla_systemd_worker.py').read_text())
        template=next(ast.literal_eval(v.value) for v in tree.body if isinstance(v,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TEMPLATE' for t in v.targets))
        defaults=yaml.safe_load((folder/'group_vars/all/common.yml').read_text());self.assertEqual(defaults['docker_graceful_timeout'],60)
        text=Template(template).substitute(service_name=node.UNIT,engine='docker',deps='docker.service',restart_timeout=120,restart_retries=10,name='mariadb',graceful_timeout=defaults['docker_graceful_timeout'],restart_policy='always',restart_duration=11)
        self.assertTrue(node.validate_unit(text))
        for bad in (text.replace('start -a mariadb','start -a keepalived'),text.replace('stop mariadb','stop haproxy'),text.replace(' -t 60',' -t 600'),text.replace('Restart=always','ExecStartPost=/bin/false\nRestart=always')):
            with self.assertRaises(RuntimeError):node.validate_unit(bad)
    def test_collector_keeps_readiness_failure_as_observation_not_restart(self):
        addresses=[{'ifname':'enp0s31f6','addr_info':[{'local':'192.168.0.201','prefixlen':24}]}]
        with patch.object(node,'run',return_value=json.dumps(addresses)),patch.object(node,'inspect',return_value=self.item['mariadb']),patch.object(node,'unit_state',return_value=self.item['unit']),patch.object(node,'baseline_module',return_value={'galera_status':lambda ip:(_ for _ in ()).throw(RuntimeError())}):
            result=node.collect('node2')
        self.assertIsNone(result['galera']);self.assertIn('database_observation',result)
    def test_transport_uses_only_mariadb_helper_and_password_stdin(self):
        transport=step.Nodes({}, {'node2':'private-password'})
        with patch.object(step.vip.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='{"passed":true}')) as call:transport.call('node2','observe')
        args,kwargs=call.call_args;self.assertNotIn('private-password',str(args));self.assertEqual(kwargs['input'],'private-password\n')
        import shlex
        payload=shlex.split(args[0][-1])[6];ast.parse(payload)
        self.assertIn('StrictHostKeyChecking=yes',args[0]);self.assertLess(len(args[0][-1]),100000)

if __name__=='__main__':unittest.main()

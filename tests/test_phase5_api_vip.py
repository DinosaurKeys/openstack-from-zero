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
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
step=load('vip_test','phase5-api-vip.py');node=load('vip_node_test','phase5-api-vip-node.py')
IDS={n:str(i)*64 for i,n in enumerate(step.inv.NODES,1)}
SERVER='0ef50722-c056-49fa-8f9e-05e926a8b41d'


def records(owner='node2',stopped=None):
    return {n:{'node':n,'vip_owner':n==owner,'keepalived':{'id':IDS[n],'running':n!=stopped,'health':'not configured'},
        'unit':{'active':'inactive' if n==stopped else 'active'},'haproxy':{'running':True,'health':'healthy'},'baseline':{'ready':True,'facts':{'protected_config_hashes':{'config':'same'},'galera':{'wsrep_cluster_state_uuid':'f3609644-d1e0-11f1-a749-0ef43e7d82dc'}}}} for n in step.inv.NODES}


def plan():
    return {'plan':step.PLAN,'id':'12345678-abcd-4321-8321-0123456789ab','target':'node2','container_id':IDS['node2'],'container_ids':IDS,
        'prepared_unix':1000,'inputs':{'x':'y'},'project_id':'project','user_id':'user','server_id':SERVER}


class Nodes:
    def __init__(self):self.owner='node2';self.stopped=None;self.calls=[];self.fail_stop=False;self.fail_start=False;self.double=False;self.no_takeover=False
    def all(self,action='observe'):
        value=records(self.owner,self.stopped)
        if self.double and self.stopped:value['node1']['vip_owner']=True;value['node3']['vip_owner']=True
        return value
    def call(self,target,action,cid):
        self.calls.append((target,action,cid))
        if action=='stop':
            self.stopped=target;self.owner=target if self.no_takeover else 'node3'
            if self.fail_stop:raise RuntimeError('Stop response lost')
        else:
            if self.fail_start:raise RuntimeError('Start failed')
            self.stopped=None
        return self.all()[target]


class Sampler:
    def __init__(self,check):self.records=[];self.closed=False
    def start(self):
        now=step.time.monotonic();self.records=[{'started':now,'finished':now+0.1,'ok':True,'duration_seconds':0.1}]
    def stop(self):self.closed=True


class ExerciseTests(unittest.TestCase):
    def setUp(self):self.nodes=Nodes();self.saved=[];self.logs=[]
    def exercise(self):
        with patch.object(step.access,'check_apis',return_value={}),patch.object(step.time,'sleep'):
            return step.exercise(self.nodes,plan(),{},SimpleNamespace(horizon=lambda:{'https_verified':True,'login_form_present':True}),SERVER,
                lambda v:self.saved.append(copy.deepcopy(v)),self.logs.append,Sampler)
    def test_success_one_stop_and_restore_other_owner_final_allowed(self):
        result=self.exercise()
        self.assertEqual(self.nodes.calls,[('node2','stop',IDS['node2']),('node2','start',IDS['node2'])])
        self.assertEqual(result['takeover_owner'],'node3');self.assertEqual(result['final_owner'],'node3')
        self.assertTrue(self.saved[0]['restore_required']);self.assertFalse(self.saved[-1]['restore_required'])
        self.assertIn('RESULT: API_VIP_FAILOVER_VERIFIED',self.logs)
    def test_uncertain_stop_still_restores(self):
        self.nodes.fail_stop=True
        with self.assertRaisesRegex(RuntimeError,'Stop response lost'):self.exercise()
        self.assertEqual(self.nodes.calls[-1][1],'start');self.assertFalse(self.saved[-1]['restore_required'])
        self.assertNotIn('RESULT: API_VIP_FAILOVER_VERIFIED',self.logs)
    def test_restore_failure_keeps_durable_requirement(self):
        self.nodes.fail_start=True
        with self.assertRaisesRegex(RuntimeError,'restoration not confirmed'):self.exercise()
        self.assertTrue(self.saved[-1]['restore_required'])
    def test_duplicate_owners_restore_not_pass(self):
        self.nodes.double=True
        with self.assertRaisesRegex(RuntimeError,'Multiple VIP'):self.exercise()
        self.assertEqual(self.nodes.calls[-1][1],'start')
    def test_no_takeover_times_out_then_restores(self):
        self.nodes.no_takeover=True
        with patch.dict(step.PLAN,{'takeover_timeout_seconds':-1}),self.assertRaisesRegex(RuntimeError,'Takeover/API'):self.exercise()
        self.assertEqual(self.nodes.calls[-1][1],'start')
    def test_changed_owner_or_container_blocks_before_stop(self):
        self.nodes.owner='node1'
        with self.assertRaisesRegex(RuntimeError,'owner changed'):self.exercise()
        self.assertFalse(self.nodes.calls);self.assertFalse(self.saved)
        self.nodes.owner='node2';bad=records();bad['node2']['keepalived']['id']='f'*64
        with patch.object(self.nodes,'all',return_value=bad),self.assertRaises(RuntimeError):self.exercise()
        self.assertFalse(self.nodes.calls)
    def test_baseline_api_failure_does_not_stop(self):
        with patch.object(step.access,'check_apis',side_effect=RuntimeError('Baseline API failure')),self.assertRaises(RuntimeError):
            step.exercise(self.nodes,plan(),{},None,SERVER,lambda v:None,lambda v:None,Sampler)
        self.assertFalse(self.nodes.calls)
    def test_keyboard_interrupt_also_restores(self):
        original=self.nodes.call
        def call(target,action,cid):
            if action=='stop':raise KeyboardInterrupt
            return original(target,action,cid)
        with patch.object(self.nodes,'call',side_effect=call),self.assertRaisesRegex(RuntimeError,'KeyboardInterrupt'):self.exercise()
        self.assertEqual(self.nodes.calls[-1][1],'start')
    def test_takeover_api_failure_still_restores(self):
        calls=[]
        def api(*args):
            calls.append(1)
            if len(calls)==2:raise RuntimeError('Takeover API failed')
        with patch.object(step.access,'check_apis',side_effect=api),self.assertRaisesRegex(RuntimeError,'Takeover API failed'):
            step.exercise(self.nodes,plan(),{},SimpleNamespace(horizon=lambda:{'https_verified':True,'login_form_present':True}),SERVER,
                lambda v:self.saved.append(copy.deepcopy(v)),self.logs.append,Sampler)
        self.assertEqual(self.nodes.calls[-1][1],'start')
    def test_state_save_failure_before_stop_has_no_mutation(self):
        with patch.object(step.access,'check_apis',return_value={}),self.assertRaises(OSError):
            step.exercise(self.nodes,plan(),{},SimpleNamespace(horizon=lambda:{'https_verified':True,'login_form_present':True}),SERVER,
                lambda v:(_ for _ in ()).throw(OSError()),self.logs.append,Sampler)
        self.assertFalse(self.nodes.calls)


class GuardTests(unittest.TestCase):
    def test_plan_expiry_binding_and_ids(self):
        value=plan();binding={k:value[k] for k in ('inputs','project_id','user_id','server_id')}
        step.validate_plan(value,binding,1100)
        for bad in (dict(value,prepared_unix=-2000),dict(value,target='foreign'),dict(value,container_id='x'),dict(value,inputs={}),dict(value,container_ids={})):
            with self.assertRaises(RuntimeError):step.validate_plan(bad,binding,1100)
    def test_complete_unique_ownership_and_service_health_required(self):
        self.assertEqual(step.owners(records(),full=True),['node2'])
        for key,bad in [('vip_owner','true'),('keepalived',{'running':False}),('haproxy',{'running':False}),('baseline',{'ready':False})]:
            value=records();value['node1'][key]=bad
            with self.subTest(key=key),self.assertRaises(RuntimeError):step.owners(value,full=True)
        with self.assertRaises(RuntimeError):step.owners({})
    def test_default_plan_has_no_io(self):
        with patch.object(step,'execute',side_effect=AssertionError),patch.object(step.deploy.tools,'check_paths',side_effect=AssertionError),contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(step.main([]),0)
        self.assertIn('PLAN ONLY',out.getvalue())
    def test_failed_preparation_is_rejected_before_password_collection(self):
        with self.assertRaisesRegex(RuntimeError,'failed --prepare'):
            step.require_preparation('apply',None)
        step.require_preparation('prepare',None)
        step.require_preparation('apply',plan())
        import inspect
        source=inspect.getsource(step.execute)
        self.assertLess(source.index('require_preparation(mode,plan)'),source.index('getpass.getpass'))

    def test_restoration_is_management_ip_not_vip(self):
        command=step.restore_command(plan());self.assertIn('192.168.0.201',command);self.assertNotIn('192.168.0.151',command)
        self.assertIn('sudo systemctl start kolla-keepalived-container.service',command)
    def test_password_stdin_never_in_argv_and_no_tofu(self):
        transport=step.Nodes({}, {'node1':'private-password'})
        with patch.object(step.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='{"passed":true}')) as call:
            transport.call('node1','observe')
        args,kwargs=call.call_args;self.assertNotIn('private-password',str(args));self.assertEqual(kwargs['input'],'private-password\n')
        self.assertIn('StrictHostKeyChecking=yes',args[0]);self.assertIn('BatchMode=yes',args[0]);self.assertIn('ForwardAgent=no',args[0])
    def test_raw_errors_and_credentials_not_exposed(self):
        transport=step.Nodes({}, {'node1':'private-password'})
        with patch.object(step.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout='not JSON',stderr='private-password')):
            with self.assertRaises(RuntimeError) as error:transport.call('node1','observe')
        self.assertNotIn('private-password',str(error.exception))


class NodeTests(unittest.TestCase):
    def setUp(self):
        self.item=records()['node2'];self.item['keepalived'].update(name='/keepalived',image=node.IMAGE)
        self.unit_patch=patch.object(node,'unit_state',return_value={'name':node.UNIT,'active':'active','substate':'running'});self.unit_patch.start();self.addCleanup(self.unit_patch.stop)
    def invoke(self,action,cid=None):
        with patch.object(node.os,'geteuid',return_value=0),patch.dict(node.os.environ,{'SUDO_USER':'joe'}),patch.object(node.socket,'gethostname',return_value='node2'):
            return node.execute('node2',action,cid)
    def test_only_exact_current_owner_container_can_stop(self):
        after=copy.deepcopy(self.item);after['keepalived']['running']=False;after['vip_owner']=False;after['unit']['active']='inactive'
        with patch.object(node,'collect',side_effect=[self.item,after]),patch.object(node,'run') as run:self.invoke('stop',IDS['node2'])
        run.assert_called_once_with(['systemctl','stop',node.UNIT],timeout=90)
        for cid,owner in ((IDS['node1'],True),(IDS['node2'],False)):
            item=copy.deepcopy(self.item);item['vip_owner']=owner
            with patch.object(node,'collect',return_value=item),patch.object(node,'run') as run,self.assertRaises(RuntimeError):self.invoke('stop',cid)
            run.assert_not_called()
    def test_start_is_idempotent_and_only_recorded_container(self):
        with patch.object(node,'collect',return_value=self.item),patch.object(node,'run') as run:self.invoke('start',IDS['node2'])
        run.assert_not_called()
        stopped=copy.deepcopy(self.item);stopped['keepalived']['running']=False;stopped['unit']['active']='inactive'
        with patch.object(node,'collect',side_effect=[stopped,self.item]),patch.object(node,'run') as run:self.invoke('start',IDS['node2'])
        run.assert_called_once_with(['systemctl','start',node.UNIT],timeout=90)
    def test_node_and_sudo_identity_required(self):
        with patch.object(node.os,'geteuid',return_value=1000),self.assertRaises(RuntimeError):node.execute('node2','stop',IDS['node2'])
        with self.assertRaises(RuntimeError):self.invoke('delete',IDS['node2'])
    def test_observe_does_not_change_state(self):
        with patch.object(node,'collect',return_value=self.item),patch.object(node,'run') as run:
            self.assertEqual(self.invoke('observe'),self.item)
        run.assert_not_called()
    def test_image_and_management_guard(self):
        addr=[{'ifname':'enp0s31f6','addr_info':[{'local':'192.168.0.201','prefixlen':24},{'local':node.VIP,'prefixlen':32}]}]
        good={'id':IDS['node2'],'name':'/keepalived','image':node.IMAGE,'running':True}
        with patch.object(node,'run',return_value=json.dumps(addr)),patch.object(node,'inspect',return_value=good):self.assertTrue(node.collect('node2')['vip_owner'])
        for bad in (dict(good,image='foreign'),dict(good,id='x'),dict(good,name='/other')):
            with patch.object(node,'run',return_value=json.dumps(addr)),patch.object(node,'inspect',return_value=bad),self.assertRaises(RuntimeError):node.collect('node2')
    def test_embedded_baseline_is_read_only_and_failure_retained(self):
        code="def collect(node): return {'ready':False, 'node':node}"
        with patch.object(node,'collect',return_value=self.item),patch.dict(node.__dict__,{'BASELINE_SOURCE':base64.b64encode(code.encode()).decode()}):
            result=self.invoke('full');self.assertFalse(result['baseline']['ready'])


class UnitDefinitionTests(unittest.TestCase):
    def test_exact_pinned_kolla_unit_template_and_scope(self):
        import ast
        path=ROOT/'build/real-tools-check/share/kolla-ansible/ansible/module_utils/kolla_systemd_worker.py'
        tree=ast.parse(path.read_text());template=next(ast.literal_eval(v.value) for v in tree.body if isinstance(v,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TEMPLATE' for t in v.targets))
        from string import Template
        import yaml
        defaults=yaml.safe_load((ROOT/'build/real-tools-check/share/kolla-ansible/ansible/group_vars/all/common.yml').read_text())
        self.assertEqual(defaults['docker_common_options']['graceful_timeout'],'{{ docker_graceful_timeout }}')
        self.assertEqual(defaults['docker_graceful_timeout'],60)
        content=Template(template).substitute(service_name=node.UNIT,engine='docker',deps='docker.service',restart_timeout=120,restart_retries=10,name='keepalived',graceful_timeout=defaults['docker_graceful_timeout'],restart_policy='always',restart_duration=11)
        self.assertTrue(node.validate_unit(content))
        self.assertTrue(node.validate_unit(content.replace(' -t 60',' -t 10')))
        with self.assertRaises(RuntimeError):node.validate_unit(content.replace(' -t 60',' -t 600'))
        for bad in (content.replace('start -a keepalived','start -a mariadb'),content.replace('stop keepalived','stop haproxy'),content.replace('Restart=always','ExecStartPost=/bin/false\nRestart=always')):
            with self.assertRaises(RuntimeError):node.validate_unit(bad)



class LocalCommandTests(unittest.TestCase):
    def test_remote_python_payload_compiles_and_ssh_config_parses(self):
        import ast,shlex
        transport=step.Nodes({}, {'node1':'password'})
        with patch.object(step.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='{"passed":true}')) as mocked:
            transport.call('node1','observe')
        command=mocked.call_args.args[0][-1];parts=shlex.split(command)
        self.assertEqual(parts[:6],['sudo','-S','-p','','/usr/bin/python3','-c'])
        ast.parse(parts[6]);self.assertLess(len(command),100000)
        # -G only prints the native OpenSSH configuration; it opens no connection.
        args=step.ssh_args('node1');args.insert(1,'-G')
        result=subprocess.run(args,capture_output=True,text=True,check=True)
        self.assertIn('stricthostkeychecking true',result.stdout);self.assertIn('forwardagent no',result.stdout)
    def test_cluster_uuid_mismatch_blocks_baseline(self):
        value=records();value['node1']['baseline']['facts']['galera']['wsrep_cluster_state_uuid']='12345678-abcd-4321-8321-0123456789ab'
        with patch.object(step.access,'check_apis'),self.assertRaisesRegex(RuntimeError,'cluster UUID'):
            step.full_checks(SimpleNamespace(all=lambda action:value),{},None)



class ProbeTests(unittest.TestCase):
    def test_auth_token_stays_in_memory_and_vm_identity_checked(self):
        profile={'auth':{'auth_url':'https://192.168.0.151:5000/v3','username':'admin','user_domain_name':'Default','password':'private','project_name':'admin','project_domain_name':'Default'},'region_name':'RegionOne'}
        catalog=[{'type':kind,'endpoints':[{'interface':'public','region':'RegionOne','url':'https://192.168.0.151:'+str(port)+('/v2.1' if kind=='compute' else '/v3' if kind in ('identity','block-storage') else '')}]} for kind,port in step.access.SERVICE_PORTS.items()]
        client=SimpleNamespace(request=lambda *a,**k:({'token':{'project':{'id':'project'},'catalog':catalog}},'secret') if 'tokens' in a[0] else ({'server':{'id':SERVER,'status':'ACTIVE'}},None))
        result=step.probe(profile,client,SERVER);self.assertTrue(result['ok']);self.assertNotIn('secret',json.dumps(result));self.assertNotIn('private',json.dumps(result))
        client.request=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('Unavailable'))
        self.assertFalse(step.probe(profile,client,SERVER)['ok'])

if __name__=='__main__':unittest.main()

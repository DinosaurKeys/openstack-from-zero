import base64
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import shlex
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def load(name,file):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/file)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
step=load('router_test','phase5-router.py');node=load('router_node_test','phase5-router-node.py')
ROUTER='a3c6cb8e-cba3-49d0-964b-cf131e1075c5'
PORTS=[('bf281c79-0af8-4a0c-b23a-ff92fbbb1171','fa:16:3e:64:51:7c','169.254.192.208'),
       ('7aa70dea-517f-414b-ae0e-69b604810224','fa:16:3e:21:4a:32','169.254.193.222'),
       ('dcd98f88-c771-4abb-91b1-50f3deffa625','fa:16:3e:65:b9:dd','169.254.194.47')]
CONTEXT={'router_id':ROUTER,'gateway':'10.10.10.1','external_ip':'192.168.0.154','ha_ports':[{'id':p,'mac':m,'ips':[ip]} for p,m,ip in PORTS]}

def records(owner='node3',down=None):
    return {n:{'node':n,'namespace':'qrouter-'+ROUTER,'namespace_identity':[4,100+i],
        'container_id':str(i+1)*64,'interface':('ha-'+PORTS[i][0])[:14],'ifindex':16+i,
        'mac':PORTS[i][1],'ha_port_id':PORTS[i][0], 'ha_up':n!=down,
        'owns_gateway':n==owner,'owns_external':n==owner,
        'keepalived':{'neutron_version':'29.0.1.dev19','config_sha256':'a'*64,'tracks_HA_interface':True,'vrrp_instances':1,'ha_virtual_ips':['169.254.0.72']}}
        for i,n in enumerate(step.inv.NODES)}

def plan():
    return {'id':'12345678-abcd-4321-8321-0123456789ab','plan':step.PLAN,'target':'node3','prepared_unix':1000,
        'context':copy.deepcopy(CONTEXT),'nodes':{n:step.identity(v) for n,v in records().items()}}

class FakeNodes:
    def __init__(self):self.down=None;self.owner='node3';self.calls=[];self.fail_down=False;self.fail_up=False;self.interrupt=False
    def all(self):return records(self.owner,self.down)
    def call(self,n,action):
        self.calls.append((n,action))
        if action=='down':
            self.down=n;self.owner='node1'
            if self.interrupt:raise KeyboardInterrupt
            if self.fail_down:raise RuntimeError('Down response unavailable')
        elif action=='up':
            if self.fail_up:raise RuntimeError('UP response unavailable')
            self.down=None
        return self.all()[n]

class Sampler:
    def __init__(self,check):self.records=[]
    def start(self):
        now=step.time.monotonic();self.records=[{'started':now,'finished':now+.1,'ok':True,'duration_seconds':.1}]
    def stop(self):pass

def service():
    return ({n:{'mariadb':{'id':str(i)*64},'baseline':{'facts':{'protected_config_hashes':{'x':'same'}}}} for i,n in enumerate(step.inv.NODES,1)},'f3609644-d1e0-11f1-a749-0ef43e7d82dc','node3')

class ExerciseTests(unittest.TestCase):
    def setUp(self):self.nodes=FakeNodes();self.saved=[];self.logs=[]
    def roles(self,api,rid):return {n:'active' if n==self.nodes.owner else 'standby' for n in step.inv.NODES}
    def exercise(self,check=lambda:{'ok':True},data=lambda:None,services=service,save=None):
        with patch.object(step,'roles',side_effect=self.roles),patch.object(step.time,'sleep'):
            return step.exercise(self.nodes,None,plan(),check,data,services,save or (lambda v:self.saved.append(copy.deepcopy(v))),self.logs.append,Sampler)
    def test_takeover_restores_link_and_verifies_all_checks(self):
        result=self.exercise()
        self.assertEqual(self.nodes.calls,[('node3','down'),('node3','up')])
        self.assertEqual(result['takeover_active'],'node1');self.assertEqual(result['final_active'],'node1')
        self.assertTrue(self.saved[0]['restore_required']);self.assertFalse(self.saved[-1]['restore_required'])
        self.assertIn('RESULT: ROUTER_HA_FAILOVER_VERIFIED',self.logs)
    def test_uncertain_down_always_attempts_original_up(self):
        self.nodes.fail_down=True
        with self.assertRaisesRegex(RuntimeError,'Down response unavailable'):self.exercise()
        self.assertEqual(self.nodes.calls[-1],('node3','up'));self.assertFalse(self.saved[-1]['restore_required'])
    def test_keyboard_interrupt_restores(self):
        self.nodes.interrupt=True
        with self.assertRaisesRegex(RuntimeError,'KeyboardInterrupt'):self.exercise()
        self.assertEqual(self.nodes.calls[-1],('node3','up'))
    def test_failed_restore_retains_responsibility(self):
        self.nodes.fail_up=True
        with self.assertRaisesRegex(RuntimeError,'restoration not confirmed'):self.exercise()
        self.assertTrue(self.saved[-1]['restore_required'])
        self.assertNotIn('RESULT: ROUTER_HA_FAILOVER_VERIFIED',self.logs)
    def test_no_fault_when_baseline_network_or_volume_fails(self):
        with self.assertRaises(RuntimeError):self.exercise(check=lambda:{'ok':False})
        self.assertFalse(self.nodes.calls)
        with self.assertRaises(RuntimeError):self.exercise(data=lambda:(_ for _ in ()).throw(RuntimeError('Checksum failed')))
        self.assertFalse(self.nodes.calls)
    def test_save_failure_prevents_fault(self):
        with self.assertRaises(OSError):self.exercise(save=lambda v:(_ for _ in ()).throw(OSError()))
        self.assertFalse(self.nodes.calls)
    def test_owner_change_blocks_before_fault(self):
        self.nodes.owner='node1'
        with self.assertRaisesRegex(RuntimeError,'changed since preparation'):self.exercise()
        self.assertFalse(self.nodes.calls)
    def test_guest_recovery_failure_restores(self):
        with self.assertRaisesRegex(RuntimeError,'Guest network did not recover'):self.exercise(check=lambda:{'ok':self.nodes.down is None})
        self.assertEqual(self.nodes.calls[-1],('node3','up'))
    def test_config_change_fails_final_result(self):
        calls=[]
        def services():
            value=service();calls.append(1)
            if len(calls)>1:value[0]['node2']['baseline']['facts']['protected_config_hashes']['x']='changed'
            return value
        with self.assertRaisesRegex(RuntimeError,'protected host configuration'):self.exercise(services=services)
        self.assertEqual(self.nodes.calls[-1],('node3','up'))

class ObservationTests(unittest.TestCase):
    def test_live_report_interface_name_and_active_extra_vip_are_accepted(self):
        link={'ifname':'ha-dcd98f88-c7','ifindex':16,'address':PORTS[2][1],
              'addr_info':[{'family':'inet','local':PORTS[2][2]},{'family':'inet','local':'169.254.0.72'}]}
        selected,port=node.select_link([link],CONTEXT)
        self.assertEqual(selected['ifname'],'ha-dcd98f88-c7');self.assertEqual(port['id'],PORTS[2][0])
    def test_mac_duplicates_wrong_name_and_non_linklocal_address_refused(self):
        good={'ifname':'ha-dcd98f88-c7','ifindex':16,'address':PORTS[2][1],'addr_info':[{'family':'inet','local':PORTS[2][2]}]}
        for values in ([good,copy.deepcopy(good)],[dict(good,ifname='enp0s31f6')],[dict(good,addr_info=good['addr_info']+[{'family':'inet','local':'192.168.0.99'}])]):
            with self.assertRaises(RuntimeError):node.select_link(values,CONTEXT)
    def test_roles_match_actual_addresses_not_api_alone(self):
        reported={'node1':'standby','node2':'standby','node3':'active'}
        self.assertEqual(step.inspect_roles(records(),reported),'node3')
        wrong=records();wrong['node3']['owns_external']=False
        self.assertIsNone(step.inspect_roles(wrong,reported))
        wrong=records();wrong['node1']['owns_gateway']=True
        with self.assertRaisesRegex(RuntimeError,'Multiple'):step.inspect_roles(wrong,reported)
    def test_changed_namespace_or_container_refuses_plan(self):
        reported={'node1':'standby','node2':'standby','node3':'active'}
        for key,value in [('namespace_identity',[4,999]),('container_id','f'*64),('ifindex',999)]:
            wrong=records();wrong['node1'][key]=value
            with self.assertRaises(RuntimeError):step.inspect_roles(wrong,reported,plan())
    def test_default_plan_no_connections(self):
        with patch.object(step,'execute',side_effect=AssertionError),contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(step.main([]),0)
        self.assertIn('PLAN ONLY',out.getvalue())
    def test_plan_identity_age_target_and_binding(self):
        p=plan();binding={'context':CONTEXT};step.validate_plan(p,binding,1100)
        for wrong in [dict(p,target='other'),dict(p,prepared_unix=-1000),dict(p,id='x'),dict(p,nodes={}),dict(p,context={})]:
            with self.assertRaises(RuntimeError):step.validate_plan(wrong,binding,1100)
    def test_restoration_command_uses_management_ssh_namespace_and_exact_interface(self):
        args=shlex.split(step.restore_command(plan()))
        self.assertIn('joe@192.168.0.202',args)
        self.assertEqual(shlex.split(args[-1]),['sudo','ip','netns','exec','qrouter-'+ROUTER,'ip','link','set','dev','ha-dcd98f88-c7','up'])
    def test_missing_prepare_refuses_apply(self):
        with self.assertRaisesRegex(RuntimeError,'prepare'):step.vip.require_preparation('apply',None)
    def test_network_probe_requires_three_successful_observations(self):
        with patch.object(step.subprocess,'run',side_effect=[SimpleNamespace(returncode=0),SimpleNamespace(returncode=0,stdout='{"gateway_ping":true,"outbound_HTTPS":true}')]) as calls:
            self.assertTrue(step.guest_probe(['ssh','guest'],'192.168.0.194','10.10.10.1')['ok'])
            self.assertNotIn('sudo',calls.call_args.args[0][-1])
        with patch.object(step.subprocess,'run',side_effect=[SimpleNamespace(returncode=1),SimpleNamespace(returncode=0,stdout='{"gateway_ping":true,"outbound_HTTPS":true}')]):
            self.assertFalse(step.guest_probe(['ssh','guest'],'192.168.0.194','10.10.10.1')['ok'])
    def test_transport_secret_stdin_and_embedded_helper_syntax(self):
        nodes=step.Nodes({}, {'node3':'test-password'},CONTEXT)
        with patch.object(step.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='{"passed":true}')) as run:
            nodes.call('node3','observe')
        args=run.call_args.args[0];kwargs=run.call_args.kwargs
        self.assertNotIn('test-password',str(args));self.assertEqual(kwargs['input'],'test-password\n')
        remote=shlex.split(args[-1]);compile(remote[6],'ssh-payload','exec')
        self.assertLess(len(args[-1]),100000)

class NodeMutationTests(unittest.TestCase):
    def invoke(self,action,values,expected=None):
        with patch.object(node.os,'geteuid',return_value=0),patch.dict(node.os.environ,{'SUDO_USER':'joe'}),patch.object(node.socket,'gethostname',return_value='node3'),patch.object(node,'collect',side_effect=values),patch.object(node,'run') as run:
            result=node.execute('node3',action,CONTEXT,expected)
            return result,run.call_args_list
    def test_exact_namespace_only_down_and_up(self):
        before=records()['node3'];after=records('node1','node3')['node3'];expected=node.identity(before)
        result,calls=self.invoke('down',[before,after],expected)
        self.assertEqual(calls[0].args[0],['ip','netns','exec','qrouter-'+ROUTER,'ip','link','set','dev','ha-dcd98f88-c7','down'])
        self.assertFalse(result['ha_up'])
        result,calls=self.invoke('up',[after,dict(after,ha_up=True)],expected)
        self.assertEqual(calls[0].args[0][-1],'up');self.assertTrue(result['ha_up'])
    def test_no_mutation_without_matching_identity_or_active_addresses(self):
        before=records()['node3']
        with self.assertRaises(RuntimeError):self.invoke('down',[before],dict(node.identity(before),container_id='f'*64))
        with self.assertRaises(RuntimeError):self.invoke('down',[records('node1')['node3']],node.identity(before))
    def test_observe_and_already_up_never_mutate(self):
        before=records()['node3'];result,calls=self.invoke('observe',[before]);self.assertFalse(calls)
        result,calls=self.invoke('up',[before,before],node.identity(before));self.assertFalse(calls)
    def test_reader_executes_real_config_parsing_without_secrets(self):
        def config(cfg,paths):cfg.read_dict({'DEFAULT':{'state_path':'/var/lib/neutron/kolla'}});return paths
        template='''vrrp_instance VR_72 {
 authentication { auth_pass SECRET }
 track_interface {
 HA_INTERFACE
 }
 virtual_ipaddress {
 169.254.0.72/24 dev ha-dcd98f88-c7
 }
}'''
        for tracking,valid in [('ha-dcd98f88-c7',True),('ha-dcd98f88-c7 weight 10',False)]:
            output=io.StringIO()
            with patch('configparser.ConfigParser.read',config),patch('pathlib.Path.is_symlink',return_value=False),patch('pathlib.Path.is_file',return_value=True),patch('pathlib.Path.read_bytes',return_value=template.replace('HA_INTERFACE',tracking).encode()),patch('importlib.metadata.version',return_value='29.0.1.dev19'),patch('sys.argv',['reader',ROUTER,'ha-dcd98f88-c7']),contextlib.redirect_stdout(output):
                exec(compile(node.READER,'reader','exec'),{})
            value=json.loads(output.getvalue());self.assertEqual(value['tracks_HA_interface'],valid)
            self.assertEqual(value['ha_virtual_ips'],['169.254.0.72']);self.assertNotIn('SECRET',output.getvalue())

class CollectorTests(unittest.TestCase):
    def collect(self,extra=None):
        link={'ifname':'ha-dcd98f88-c7','ifindex':16,'address':PORTS[2][1],'flags':['UP'],
              'addr_info':[{'family':'inet','local':PORTS[2][2]},{'family':'inet','local':'169.254.0.72'}]}
        if extra:link['addr_info'].append({'family':'inet','local':extra})
        links=[link,{'ifname':'qg-e6b8a64d-60','addr_info':[{'family':'inet','local':'192.168.0.154'}]},
                    {'ifname':'qr-bf8731d7-7b','addr_info':[{'family':'inet','local':'10.10.10.1'}]}]
        replies=[json.dumps([{'addr_info':[{'local':'192.168.0.202'}]}]),
          json.dumps({'id':'3'*64,'image':node.IMAGE,'running':True,'network_mode':'host'}),
          json.dumps(links),json.dumps(records()['node3']['keepalived'])]
        with patch.object(node,'run',side_effect=replies) as calls,patch.object(node.os,'stat',return_value=SimpleNamespace(st_dev=4,st_ino=102)):
            result=node.collect('node3',CONTEXT)
        return result,calls.call_args_list
    def test_live_shape_passes_collector_and_only_reads(self):
        value,calls=self.collect()
        self.assertTrue(value['owns_gateway']);self.assertTrue(value['owns_external']);self.assertTrue(value['ha_up'])
        self.assertEqual(value['interface'],'ha-dcd98f88-c7')
        self.assertEqual(len(calls),4)
        self.assertFalse(any('set' in call.args[0] for call in calls))
    def test_undeclared_linklocal_address_is_not_silently_accepted(self):
        with self.assertRaisesRegex(RuntimeError,'not declared'):self.collect('169.254.99.99')
    def test_wait_timeout_does_not_issue_another_fault(self):
        nodes=FakeNodes();nodes.owner='node1'
        partial=records('node1');partial['node1']['owns_external']=False
        with patch.object(nodes,'all',return_value=partial),patch.object(step,'roles',return_value={'node1':'active','node2':'standby','node3':'standby'}),patch.dict(step.PLAN,{'observation_seconds':-1}),self.assertRaisesRegex(RuntimeError,'did not settle'):
            step.wait_router(nodes,None,plan(),None)
        self.assertFalse(nodes.calls)
    def test_api_role_observation_rejects_unhealthy_agent(self):
        api=SimpleNamespace(get=lambda *a:{'agents':[{'host':n,'alive':n!='node2','admin_state_up':True,'ha_state':'standby'} for n in step.inv.NODES]})
        with self.assertRaises(RuntimeError):step.roles(api,ROUTER)


if __name__=='__main__':unittest.main()

import ast
import base64
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import stat
from string import Template
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('recover_api_test',ROOT/'scripts/lab-recover-api.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r);n=r.node_helper
CLUSTER='f3609644-d1e0-11f1-a749-0ef43e7d82dc'


def records():
    result={}
    for i,(node,names) in enumerate(r.ALLOWED.items(),1):
        db={'wsrep_cluster_status':'Primary','wsrep_cluster_size':'3','wsrep_local_state_comment':'Synced',
          'wsrep_connected':'ON','wsrep_ready':'ON','wsrep_cluster_state_uuid':CLUSTER}
        states={name:{'running':True,'health':'healthy'} for name in ('proxysql','haproxy')}
        targets={name:{'container':{'id':str(i)*64,'running':True,'health':'unhealthy'},
          'unit':{'name':'kolla-'+name+'-container.service','sha256':'a'*64,'active':'active'}} for name in names}
        result[node]={'node':node,'passed':True,'host':{'node':node,'observed':True,
          'vip_interfaces':['enp0s31f6'] if node=='node3' else [],
          'galera':{'observed':True,'value':db},'rabbitmq':{'observed':True,'value':{'healthy':True}},
          'containers':{'observed':True,'value':{'containers':states,'unhealthy':list(names),'missing':[]}}},'targets':targets}
    return result


class Nodes:
    def __init__(self):self.data=records();self.calls=[];self.fail=None;self.drift=None;self.observations=0
    def all(self):
        self.observations+=1
        value=copy.deepcopy(self.data)
        if self.drift and self.observations>1:
            value['node1']['targets']['placement_api']['container']['id']='f'*64
        return value
    def call(self,node,action,name,target,cluster):
        if action!='restart' or name not in r.ALLOWED[node] or cluster!=CLUSTER:raise AssertionError('scope exceeded')
        self.calls.append((node,name))
        if self.fail:raise self.fail
        self.data[node]['targets'][name]['container']['health']='healthy'
        self.data[node]['host']['containers']['value']['unhealthy'].remove(name)
        return {'node':node,'container':copy.deepcopy(self.data[node]['targets'][name]['container']),'action':'restarted once; container health passed'}


class RecoveryTests(unittest.TestCase):
    def workflow(self,nodes,apply=True):
        saves=[];logs=[]
        result=r.workflow(nodes,nodes.all(),apply,lambda state:saves.append(copy.deepcopy(state)),logs.append,lambda:None)
        return result,saves,logs

    def test_exact_14_and_order_with_durable_attempt_before_mutation(self):
        nodes=Nodes();saves=[]
        original=nodes.call
        def call(node,action,name,target,cluster):
            self.assertTrue(saves[-1]['pending']);self.assertEqual(saves[-1]['current'],{'node':node,'service':name})
            return original(node,action,name,target,cluster)
        with patch.object(nodes,'call',side_effect=call):
            result=r.workflow(nodes,nodes.all(),True,lambda value:saves.append(copy.deepcopy(value)),lambda _:None,lambda:None)
        expected=[(node,name) for name in r.ORDER for node in r.ALLOWED if name in r.ALLOWED[node]]
        self.assertEqual(nodes.calls,expected);self.assertEqual(len(nodes.calls),14)
        self.assertNotIn(('node2','placement_api'),nodes.calls)
        self.assertFalse(saves[-1]['pending']);self.assertEqual(len(saves[-1]['attempts']),14)
        self.assertFalse(r.planned(result))

    def test_plan_never_mutates_or_saves_receipt(self):
        nodes=Nodes();_,saves,logs=self.workflow(nodes,False)
        self.assertFalse(nodes.calls);self.assertFalse(saves);self.assertIn('RESULT: API_RECOVERY_PLAN_COLLECTED',logs)

    def test_now_healthy_targets_skipped(self):
        nodes=Nodes();nodes.call('node1','restart','placement_api',None,CLUSTER);nodes.calls.clear()
        self.workflow(nodes)
        self.assertEqual(len(nodes.calls),13);self.assertNotIn(('node1','placement_api'),nodes.calls)

    def test_lost_response_stops_and_preserves_pending_no_retry(self):
        nodes=Nodes();nodes.fail=RuntimeError('response lost');saved=[]
        with self.assertRaisesRegex(RuntimeError,'response lost'):
            r.workflow(nodes,nodes.all(),True,lambda value:saved.append(copy.deepcopy(value)),lambda _:None,lambda:None)
        self.assertEqual(len(nodes.calls),1);self.assertTrue(saved[-1]['pending'])

    def test_interrupt_preserves_attempted_receipt(self):
        nodes=Nodes();nodes.fail=KeyboardInterrupt();saved=[]
        with self.assertRaises(KeyboardInterrupt):r.workflow(nodes,nodes.all(),True,lambda v:saved.append(copy.deepcopy(v)),lambda _:None,lambda:None)
        self.assertTrue(saved[-1]['pending']);self.assertEqual(len(nodes.calls),1)

    def test_dependency_ambiguity_and_unapproved_failure_block(self):
        for key in ('galera','rabbitmq','containers'):
            data=records()
            if key=='galera':data['node2']['host'][key]['value']['wsrep_cluster_size']='2'
            if key=='rabbitmq':data['node2']['host'][key]['value']['healthy']=False
            if key=='containers':data['node2']['host'][key]['value']['unhealthy'].append('nova_compute')
            with self.assertRaises(RuntimeError):r.validate(data)
        data=records();data['node2']['host']['galera']['observed']=False
        with self.assertRaises(RuntimeError):r.validate(data)
        data=records();data['node2']['host']['vip_interfaces']=['enp0s31f6']
        with self.assertRaises(RuntimeError):r.validate(data)

    def test_identity_drift_blocks_before_restart(self):
        nodes=Nodes();nodes.drift=True
        with self.assertRaisesRegex(RuntimeError,'changed'):self.workflow(nodes)
        self.assertFalse(nodes.calls)

    def test_starting_service_not_restarted_again(self):
        data=records();data['node1']['targets']['placement_api']['container']['health']='starting'
        with self.assertRaisesRegex(RuntimeError,'already starting'):r.validate(data)

    def test_pinned_systemd_unit_and_nova_metadata_image_mapping(self):
        path=ROOT/'build/real-tools-check/share/kolla-ansible/ansible/module_utils/kolla_systemd_worker.py'
        tree=ast.parse(path.read_text())
        template=next(ast.literal_eval(x.value) for x in tree.body if isinstance(x,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TEMPLATE' for t in x.targets))
        for name in ('placement_api','nova_metadata'):
            raw=Template(template).substitute(service_name=name,engine='docker',deps='docker.service',restart_timeout=120,restart_retries=10,name=name,graceful_timeout=60,restart_policy='always',restart_duration=11).encode()
            fake=SimpleNamespace(lstat=lambda:SimpleNamespace(st_mode=stat.S_IFREG|0o644,st_uid=0),read_bytes=lambda:raw)
            props='LoadState=loaded\nActiveState=active\nFragmentPath=/etc/systemd/system/kolla-'+name+'-container.service\nDropInPaths=\n'
            with patch.object(n,'Path',return_value=fake),patch.object(n,'run',return_value=props):self.assertEqual(n.unit(name)['active'],'active')
            bad=raw.replace(b'[Service]',b'[Service]\nExecStartPre=/bin/false')
            fake.read_bytes=lambda:bad
            with patch.object(n,'Path',return_value=fake),patch.object(n,'run',return_value=props),self.assertRaises(RuntimeError):n.unit(name)
        image={'id':'a'*64,'image':'quay.io/openstack.kolla/nova-api:2026.2-ubuntu-noble','running':True,'health':'unhealthy'}
        with patch.object(n,'run',return_value=json.dumps(image)):self.assertEqual(n.container('nova_metadata')['id'],'a'*64)

    def node_context(self):
        return [patch.object(n.os,'geteuid',return_value=0),patch.dict(os.environ,{'SUDO_USER':'joe'}),patch.object(n.socket,'gethostname',return_value='node1')]

    def test_node_performs_one_systemctl_restart_only(self):
        record=records()['node1']['host'];before={'id':'a'*64,'running':True,'health':'unhealthy'}
        after=dict(before,health='healthy');unit={'name':'kolla-placement_api-container.service','sha256':'b'*64,'active':'active'}
        with contextlib.ExitStack() as stack:
            for p in self.node_context():stack.enter_context(p)
            stack.enter_context(patch.object(n,'inspector',return_value={'collect':lambda _:record}))
            stack.enter_context(patch.object(n,'container',side_effect=[before,after]));stack.enter_context(patch.object(n,'unit',return_value=unit))
            command=stack.enter_context(patch.object(n,'run',return_value=''))
            result=n.execute('node1','restart','placement_api','a'*64,'b'*64,CLUSTER)
        command.assert_called_once_with(['systemctl','restart',unit['name']],timeout=90)
        self.assertTrue(n.healthy(result['container']))

    def test_unapproved_restart_is_rejected(self):
        with contextlib.ExitStack() as stack:
            for p in self.node_context():stack.enter_context(p)
            stack.enter_context(patch.object(n,'inspector',return_value={'collect':lambda _:records()['node1']['host']}))
            command=stack.enter_context(patch.object(n,'run',side_effect=AssertionError('No system command permitted')))
            with self.assertRaisesRegex(RuntimeError,'outside'):n.execute('node1','restart','mariadb','a'*64,'b'*64,CLUSTER)
        command.assert_not_called()

    def test_workload_checkpoints_are_read_not_cleared(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(r,'REPO',Path(directory)):
            path=Path(directory)/'private/phase5/move-vm-state.json';path.parent.mkdir(parents=True)
            raw=b'{"pending":true,"attempted":true}\n';path.write_bytes(raw);path.chmod(0o600)
            before=r.checkpoints();self.assertIn(str(path.relative_to(directory)),before);self.assertEqual(path.read_bytes(),raw)
            (path.parent/r.RECEIPT).write_text('{"pending":true}')
            self.assertEqual(r.checkpoints(),before)

    def test_default_scope_has_no_commands(self):
        with patch.object(r.subprocess,'run',side_effect=AssertionError('no I/O')),contextlib.redirect_stdout(io.StringIO()) as out:self.assertEqual(r.main([]),0)
        self.assertIn('14',out.getvalue())

    def test_prior_receipt_blocks_second_apply_before_passwords(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(r,'REPO',Path(directory)):
            folder=Path(directory)/'private/phase5';folder.mkdir(parents=True)
            (Path(directory)/'private/phase4').mkdir()
            receipt=folder/r.RECEIPT;raw=b'{"pending":true,"attempts":[{"service":"placement_api"}]}'
            receipt.write_bytes(raw);receipt.chmod(0o600)
            with patch.object(r.deploy,'source_guard'),patch.object(r.deploy.tools,'tool_environment',return_value={}),patch.object(r.deploy.validation,'local_checks',return_value=('inventory','config')),patch.object(r.inv,'ensure_agent'),patch.object(r.getpass,'getpass',side_effect=AssertionError('No prompt after blocked receipt')):
                with self.assertRaisesRegex(RuntimeError,'earlier API recovery receipt'):r.execute('apply',lambda _:None,{})
            self.assertEqual(receipt.read_bytes(),raw)

    def test_health_timeout_does_not_repeat_restart(self):
        record=records()['node1']['host'];value={'id':'a'*64,'running':True,'health':'unhealthy'}
        unit={'name':'kolla-placement_api-container.service','sha256':'b'*64,'active':'active'}
        with contextlib.ExitStack() as stack:
            for p in self.node_context():stack.enter_context(p)
            stack.enter_context(patch.object(n,'inspector',return_value={'collect':lambda _:record}))
            stack.enter_context(patch.object(n,'container',return_value=value));stack.enter_context(patch.object(n,'unit',return_value=unit))
            stack.enter_context(patch.object(n.time,'monotonic',side_effect=[0,121]))
            command=stack.enter_context(patch.object(n,'run',return_value=''))
            with self.assertRaisesRegex(RuntimeError,'120 seconds'):n.execute('node1','restart','placement_api','a'*64,'b'*64,CLUSTER)
        command.assert_called_once_with(['systemctl','restart',unit['name']],timeout=90)

    def test_transport_does_not_expose_sudo_password_or_stderr(self):
        def run(argv,**kwargs):
            self.assertNotIn('private-password',' '.join(argv));self.assertEqual(kwargs['input'],'private-password\n')
            remote=shlex.split(argv[-1]);self.assertEqual(remote[:5],['sudo','-S','-p','','/usr/bin/python3'])
            self.assertIn('HEALTH_SOURCE',remote[6])
            return subprocess.CompletedProcess(argv,1,'','password=do-not-print')
        with patch.object(r.subprocess,'run',side_effect=run),self.assertRaises(RuntimeError) as result:r.Nodes({},dict.fromkeys(r.ALLOWED,'private-password')).call('node1','inspect')
        self.assertNotIn('do-not-print',str(result.exception))


if __name__=='__main__':unittest.main()

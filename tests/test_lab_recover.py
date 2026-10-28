import ast
import contextlib
import copy
import importlib.util
import io
import itertools
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
spec=importlib.util.spec_from_file_location('lab_recover_test',ROOT/'scripts/lab-recover.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r);n=r.helper
reader=r.load('recovery_health_fixture','lab-health-node.py')
CLUSTER='f3609644-d1e0-11f1-a749-0ef43e7d82dc'
BOOT={node:f'00000000-0000-0000-0000-00000000000{i}' for i,node in enumerate(r.ALLOWED,1)}


def records(failed=True,stopped=False):
    result={}
    for i,node in enumerate(r.ALLOWED,1):
        states={name:{'running':True,'health':'healthy','status':'running','exit_code':0} for name in reader.REQUIRED}
        if node=='node1':states['glance_api']={'running':True,'health':'healthy','status':'running','exit_code':0}
        targets={}
        for name in r.ORDER:
            state=dict(states[name],id=str(i)*64)
            if failed and name not in ('neutron_rpc_server','neutron_periodic_worker'):state['health']='unhealthy'
            states[name]=dict(state)
            targets[name]={'container':state,'unit':{'name':'kolla-'+name+'-container.service','sha256':'a'*64,'active':'active'}}
        if stopped:states['mariadb'].update(running=False,status='exited',exit_code=0)
        db={'wsrep_cluster_status':'Primary','wsrep_cluster_size':'3','wsrep_local_state_comment':'Synced',
            'wsrep_connected':'ON','wsrep_ready':'ON','wsrep_cluster_state_uuid':CLUSTER}
        host={'node':node,'observed':True,'host':{'observed':True,'value':{'boot_id':BOOT[node]}},
              'clock':{'observed':True,'value':True},'provider_network':{'observed':True,'value':{'healthy':True}},
              'vip_interfaces':['enp0s31f6'] if node=='node1' else [],
              'galera':{'observed':not stopped,'value':db if not stopped else {}},
              'rabbitmq':{'observed':True,'value':{'healthy':True}},
              'containers':{'observed':True,'value':{'containers':states,'missing':[],
                  'unhealthy':[name for name,v in states.items() if not n.healthy(v)],'healthy':not failed and not stopped}}}
        result[node]={'node':node,'passed':True,'host':host,'targets':targets}
    return result


class Nodes:
    def __init__(self,failed=True,stopped=False):self.data=records(failed,stopped);self.calls=[];self.fail=None;self.drift=None;self.reads=0
    def all(self):
        self.reads+=1;value=copy.deepcopy(self.data)
        if self.drift and self.reads>1:self.drift(value)
        return value
    def call(self,node,action,name,target,cluster):
        if action!='restart' or name not in r.ALLOWED[node] or cluster!=CLUSTER:raise AssertionError('Unapproved operation')
        self.calls.append((node,name))
        if self.fail:raise self.fail
        self.data[node]['targets'][name]['container'].update(running=True,health='healthy')
        self.data[node]['host']['containers']['value']['containers'][name].update(running=True,health='healthy')
        self.data[node]['host']['containers']['value']['unhealthy'].remove(name)
        return {'container':copy.deepcopy(self.data[node]['targets'][name]['container'])}


class RecoveryTests(unittest.TestCase):
    def workflow(self,nodes,apply=True,answer=True):
        saves=[];logs=[];questions=[];db_calls=[]
        def confirm(phrase):questions.append(phrase);return answer
        def recover_db():
            db_calls.append(True)
            fresh=records()
            for node in nodes.data:
                nodes.data[node]['host']['galera']=fresh[node]['host']['galera']
                nodes.data[node]['host']['containers']['value']['containers']['mariadb']=fresh[node]['host']['containers']['value']['containers']['mariadb']
                nodes.data[node]['host']['containers']['value']['unhealthy'].remove('mariadb')
        result=r.workflow(nodes,nodes.all(),apply,confirm,lambda x:saves.append(copy.deepcopy(x)),logs.append,lambda:None,recover_db)
        return result,saves,logs,questions,db_calls

    def test_live_shaped_report_has_exact_21_targets_including_cinder_and_nova_workers(self):
        data=records();actions=r.planned(data)
        self.assertEqual(len(actions),21)
        self.assertEqual({name for _,name,_ in actions},{'placement_api','nova_api','nova_metadata','nova_scheduler','nova_conductor','neutron_server','cinder_api'})
        self.assertEqual(len([x for x in actions if x[1]=='placement_api']),3)
        self.assertIn('Recovery targets: 21; healthy containers are skipped.',r.plan_lines(data))

    def test_plan_is_read_only_and_healthy_database_is_not_recovered(self):
        nodes=Nodes();_,saves,logs,questions,db=self.workflow(nodes,False)
        self.assertEqual((nodes.calls,saves,questions,db),([],[],[],[]))
        self.assertIn('PASS  MariaDB: three Synced/Primary members; leave running.',logs)

    def test_cancel_before_any_restart(self):
        nodes=Nodes();result,saves,_,questions,db=self.workflow(nodes,answer=False)
        self.assertIsNone(result);self.assertFalse(nodes.calls);self.assertFalse(saves);self.assertFalse(db)
        self.assertEqual(questions,['RESTART LISTED SERVICES'])

    def test_restart_once_after_approval_with_durable_receipt(self):
        nodes=Nodes();saved=[];original=nodes.call;approved=[]
        def call(node,action,name,target,cluster):
            self.assertEqual(approved,['RESTART LISTED SERVICES'])
            self.assertTrue(saved[-1]['pending']);self.assertFalse(saved[-1]['complete'])
            self.assertEqual(saved[-1]['attempts'][-1]['service'],name)
            return original(node,action,name,target,cluster)
        with patch.object(nodes,'call',side_effect=call):
            result=r.workflow(nodes,nodes.all(),True,lambda p:approved.append(p) or True,lambda s:saved.append(copy.deepcopy(s)),lambda _:None,lambda:None,lambda:self.fail('healthy database must be left running'))
        self.assertEqual(len(nodes.calls),21);self.assertEqual(len(set(nodes.calls)),21)
        self.assertFalse(r.planned(result));self.assertTrue(saved[-1]['complete']);self.assertFalse(saved[-1]['pending'])

    def test_healthy_lab_has_no_confirmation_or_mutation(self):
        nodes=Nodes(False);result,saves,_,questions,db=self.workflow(nodes)
        self.assertIsNotNone(result);self.assertEqual((nodes.calls,saves,questions,db),([],[],[],[]))

    def test_complete_stop_has_separate_database_approval_then_fresh_control_plan(self):
        nodes=Nodes(stopped=True);_,saves,_,questions,db=self.workflow(nodes)
        self.assertEqual(questions,['RECOVER MARIADB','RESTART LISTED SERVICES']);self.assertEqual(db,[True])
        self.assertEqual(saves[0]['attempts'],[{'kind':'mariadb-recovery'}]);self.assertTrue(saves[0]['pending'])
        self.assertEqual(len(nodes.calls),21)

    def test_stopped_database_plan_does_not_guess_dependent_restarts(self):
        nodes=Nodes(stopped=True);_,saves,logs,questions,db=self.workflow(nodes,False)
        self.assertFalse(any('restart once' in v for v in logs));self.assertEqual((nodes.calls,saves,questions,db),([],[],[],[]))

    def test_partial_unknown_or_abnormal_database_is_blocked(self):
        for change in ('running','query','exit','unknown','uuid'):
            with self.subTest(change=change):
                data=records(stopped=True)
                state=data['node1']['host']['containers']['value']['containers']['mariadb']
                if change=='running':state.update(running=True,status='running')
                elif change=='query':data['node1']['host']['galera']={'observed':True,'value':{'wsrep_cluster_status':'Primary'}}
                elif change=='exit':state['exit_code']=137
                elif change=='unknown':state['running']=None
                else:
                    data=records();data['node1']['host']['galera']['value']['wsrep_cluster_state_uuid']='00000000-0000-0000-0000-000000000004'
                with self.assertRaises(RuntimeError):r.database_state(data)

    def test_membership_status_and_uuid_required_not_just_three_green_containers(self):
        for key,value in [('wsrep_cluster_status','Non-Primary'),('wsrep_cluster_size','2'),('wsrep_local_state_comment','Joining'),('wsrep_ready','OFF')]:
            data=records();data['node2']['host']['galera']['value'][key]=value
            with self.assertRaises(RuntimeError):r.database_state(data)

    def test_failed_restart_or_interrupt_stops_without_retry(self):
        for error in (RuntimeError('response unavailable'),KeyboardInterrupt()):
            nodes=Nodes();nodes.fail=error;saved=[]
            with self.assertRaises(type(error)):
                r.workflow(nodes,nodes.all(),True,lambda _:True,lambda s:saved.append(copy.deepcopy(s)),lambda _:None,lambda:None,lambda:None)
            self.assertEqual(len(nodes.calls),1);self.assertTrue(saved[-1]['pending']);self.assertFalse(saved[-1]['complete'])

    def test_boot_container_or_unit_drift_blocks(self):
        def boot(data):data['node1']['host']['host']['value']['boot_id']=BOOT['node2']
        def identity(data):data['node1']['targets']['placement_api']['container']['id']='f'*64
        def unit(data):data['node1']['targets']['placement_api']['unit']['sha256']='b'*64
        for change in (boot,identity,unit):
            nodes=Nodes();nodes.drift=change
            with self.assertRaises(RuntimeError):self.workflow(nodes)
            self.assertFalse(nodes.calls)

    def test_failure_outside_profile_is_not_repaired(self):
        for name in ('nova_compute','proxysql','rabbitmq','neutron_l3_agent','cinder_volume'):
            nodes=Nodes();nodes.data['node1']['host']['containers']['value']['unhealthy'].append(name)
            if name=='proxysql':nodes.data['node1']['host']['containers']['value']['containers'][name]['health']='unhealthy'
            if name=='rabbitmq':nodes.data['node1']['host']['rabbitmq']['value']['healthy']=False
            with self.assertRaises(RuntimeError):self.workflow(nodes)
            self.assertFalse(nodes.calls)

    def test_new_unapproved_failure_cannot_expand_displayed_list(self):
        nodes=Nodes()
        def drift(data):
            data['node1']['targets']['neutron_rpc_server']['container']['health']='unhealthy'
            data['node1']['host']['containers']['value']['unhealthy'].append('neutron_rpc_server')
        nodes.drift=drift
        with self.assertRaisesRegex(RuntimeError,'outside the displayed plan'):self.workflow(nodes)
        self.assertFalse(nodes.calls)

    def test_waiting_on_starting_does_not_restart_it(self):
        nodes=Nodes();nodes.data['node2']['targets']['nova_scheduler']['container']['health']='starting'
        nodes.data['node2']['host']['containers']['value']['containers']['nova_scheduler']['health']='starting'
        with patch.object(r.time,'monotonic',side_effect=itertools.count(0,91)):
            final,saves,logs,questions,db=self.workflow(nodes)
        self.assertEqual(len(nodes.calls),20);self.assertNotIn(('node2','nova_scheduler'),nodes.calls)
        self.assertEqual(len(r.planned(final)),1);self.assertFalse(db)
        self.assertTrue(saves[-1]['complete']);self.assertFalse(saves[-1]['pending'])
        self.assertTrue(any('DEFER' in value and 'node2/nova_scheduler' in value for value in logs))
        self.assertEqual(questions,['RESTART LISTED SERVICES'])

    def test_independent_host_and_target_reads_do_not_block_unrelated_recovery(self):
        nodes=Nodes()
        host=nodes.data['node1']['host']['containers']['value']['containers']['nova_scheduler']
        host.update(running=False,status='exited',exit_code=1,health='unhealthy')
        # The worker started between the host collection and the later target inspection.
        nodes.data['node1']['targets']['nova_scheduler']['container'].update(running=True,health='starting')
        with patch.object(r.time,'monotonic',side_effect=itertools.count(0,91)):
            _,saves,_,questions,_=self.workflow(nodes)
        self.assertEqual(nodes.calls[:3],[(node,'placement_api') for node in r.ALLOWED])
        self.assertNotIn(('node1','nova_scheduler'),nodes.calls);self.assertEqual(len(nodes.calls),20)
        self.assertEqual(questions,['RESTART LISTED SERVICES']);self.assertTrue(saves[-1]['complete'])

    def test_changing_worker_states_across_nodes_do_not_prevent_api_repairs(self):
        class ChangingNodes(Nodes):
            def all(self):
                value=super().all()
                for node in r.ALLOWED:
                    for name in ('nova_scheduler','nova_conductor'):
                        if (node,name) in self.calls:continue
                        state=value[node]['targets'][name]['container']
                        state.update(running=self.reads%2==1,health='starting' if self.reads%2==1 else 'unhealthy')
                        # The earlier general observation can differ from the target read.
                        value[node]['host']['containers']['value']['containers'][name].update(running=False,health='unhealthy',status='exited',exit_code=1)
                return value
        nodes=ChangingNodes()
        with patch.object(r.time,'sleep'),patch.object(r.time,'monotonic',side_effect=itertools.count(0,5)):
            final,saves,_,_,db=self.workflow(nodes)
        self.assertFalse(db);self.assertFalse(r.planned(final));self.assertEqual(len(nodes.calls),21)
        self.assertEqual(nodes.calls[:3],[(node,'placement_api') for node in r.ALLOWED])
        self.assertEqual(len(set(nodes.calls)),21);self.assertFalse(saves[-1]['pending'])

    def test_target_that_becomes_healthy_during_wait_is_skipped(self):
        nodes=Nodes()
        nodes.data['node1']['targets']['nova_scheduler']['container']['health']='starting'
        target=copy.deepcopy(nodes.data['node1']['targets']['nova_scheduler']);initial=nodes.all()
        nodes.data['node1']['targets']['nova_scheduler']['container']['health']='healthy'
        with patch.object(r.time,'sleep'):
            _,current=r.settle_target(nodes,initial,BOOT,CLUSTER,r.planned(initial),'node1','nova_scheduler',target,lambda _:None,lambda:None)
        self.assertTrue(n.healthy(current['container']));self.assertFalse(nodes.calls)

    def test_stopped_target_with_old_starting_health_is_eligible_for_one_restart(self):
        nodes=Nodes();nodes.data['node1']['targets']['nova_scheduler']['container'].update(running=False,health='starting')
        self.workflow(nodes)
        self.assertEqual(nodes.calls.count(('node1','nova_scheduler')),1)

    def test_starting_race_at_root_check_sends_no_systemctl_command(self):
        value={'id':'a'*64,'running':True,'health':'starting'}
        unit={'name':'kolla-nova_scheduler-container.service','sha256':'b'*64,'active':'active'}
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(n.os,'geteuid',return_value=0));stack.enter_context(patch.dict(os.environ,{'SUDO_USER':'joe'}))
            stack.enter_context(patch.object(n.socket,'gethostname',return_value='node1'))
            stack.enter_context(patch.object(n,'inspector',return_value={'collect':lambda _:records()['node1']['host']}))
            stack.enter_context(patch.object(n,'container',return_value=value));stack.enter_context(patch.object(n,'unit',return_value=unit))
            command=stack.enter_context(patch.object(n,'run',side_effect=AssertionError('no restart')))
            result=n.execute('node1','restart','nova_scheduler','a'*64,'b'*64,CLUSTER,'reboot-control')
        self.assertTrue(result['deferred']);self.assertIs(result['changed'],False);command.assert_not_called()

    def test_known_deferred_response_is_not_recorded_as_a_sent_restart(self):
        nodes=Nodes();original=nodes.call
        def call(node,action,name,target,cluster):
            if (node,name)==('node1','nova_scheduler'):
                return {'deferred':True,'changed':False,'container':dict(target['container'],running=True,health='starting')}
            return original(node,action,name,target,cluster)
        with patch.object(nodes,'call',side_effect=call):_,saves,_,_,_=self.workflow(nodes)
        state=saves[-1];attempt=next(a for a in state['attempts'] if a.get('node')=='node1' and a.get('service')=='nova_scheduler')
        self.assertEqual(attempt['outcome'],'not_sent');self.assertFalse(state['pending']);self.assertTrue(state['complete'])
        # A later approved first restart of this target is permitted; prior actual restarts still block repeats.
        r.prior_guard([(Path('receipt.json'),state)],BOOT,[('node1','nova_scheduler',{})])

    def test_plan_with_starting_target_still_reaches_review_without_changes(self):
        nodes=Nodes();nodes.data['node1']['targets']['nova_scheduler']['container']['health']='starting'
        _,saves,logs,questions,db=self.workflow(nodes,False)
        self.assertTrue(any('starting / wait' in value for value in logs))
        self.assertEqual((nodes.calls,saves,questions,db),([],[],[],[]))

    def test_complete_apply_returns_healthy_and_preserves_workload_checkpoint(self):
        class ObservedNodes(Nodes):
            def all(self):
                value=super().all()
                for record in value.values():
                    state=record['host']['containers']['value']
                    state['healthy']=not state['unhealthy']
                return value
        nodes=ObservedNodes();logs=[];details={}
        def api_checks(rows,value,env,include_workload=True):
            self.assertIs(include_workload,False)
            for label in ('HTTPS authentication','Horizon HTTPS','image HTTPS API read','compute HTTPS API read',
              'network HTTPS API read','block-storage HTTPS API read','placement HTTPS API read','Nova services',
              'Cinder services','hypervisors','Neutron agents','router HA (API state)'):
                rows.append({'check':label,'status':'PASS'})
        with tempfile.TemporaryDirectory() as directory,patch.object(r,'REPO',Path(directory)),contextlib.ExitStack() as stack:
            root=Path(directory);(root/'private/phase4').mkdir(parents=True);(root/'private/phase5').mkdir();(root/'reports').mkdir()
            checkpoint=root/'private/phase5/move-vm-state.json';raw=b'{"pending":true,"migration":"preserved"}'
            checkpoint.write_bytes(raw);checkpoint.chmod(0o600)
            stack.enter_context(patch.object(r.deploy,'source_guard'))
            stack.enter_context(patch.object(r.deploy.tools,'tool_environment',return_value={}))
            stack.enter_context(patch.object(r.deploy.validation,'local_checks',return_value=('inventory','config')))
            stack.enter_context(patch.object(r.inv,'ensure_agent'))
            stack.enter_context(patch.object(r.health.access,'input_hashes',return_value={'input':'unchanged'}))
            stack.enter_context(patch.object(r.getpass,'getpass',return_value='hidden'))
            stack.enter_context(patch.object(r,'Nodes',return_value=nodes))
            stack.enter_context(patch('builtins.input',return_value='RESTART LISTED SERVICES'))
            stack.enter_context(patch.object(r.health,'api_checks',side_effect=api_checks))
            with patch.object(r.subprocess,'Popen',side_effect=AssertionError('No database recovery')):
                self.assertEqual(r.execute('apply',logs.append,details,root/'reports/reboot-recovery-test.txt'),0)
            self.assertEqual(len(nodes.calls),21);self.assertEqual(checkpoint.read_bytes(),raw)
            self.assertIn('RESULT: REBOOT_SERVICES_HEALTHY',logs)
            receipt=json.loads((root/'private/phase5/reboot-recovery-test.json').read_text())
            self.assertTrue(receipt['complete']);self.assertFalse(receipt['pending'])

    def test_scope_profiles_keep_old_14_approval_and_reject_infrastructure_or_compute(self):
        self.assertEqual(sum(map(len,n.ALLOWED.values())),14)
        self.assertNotIn('placement_api',n.ALLOWED['node2'])
        for name in ('mariadb','proxysql','rabbitmq','nova_compute','nova_libvirt','cinder_volume','neutron_l3_agent'):
            self.assertNotIn(name,n.CONTROL_ORDER)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(n.os,'geteuid',return_value=0));stack.enter_context(patch.dict(os.environ,{'SUDO_USER':'joe'}))
            stack.enter_context(patch.object(n.socket,'gethostname',return_value='node2'))
            stack.enter_context(patch.object(n,'inspector',return_value={'collect':lambda _:records()['node2']['host']}))
            command=stack.enter_context(patch.object(n,'run',side_effect=AssertionError('no mutation')))
            with self.assertRaisesRegex(RuntimeError,'outside'):n.execute('node2','restart','placement_api','a'*64,'b'*64,CLUSTER)
            with self.assertRaisesRegex(RuntimeError,'outside'):n.execute('node2','restart','nova_compute','a'*64,'b'*64,CLUSTER,'reboot-control')
            command.assert_not_called()

    def test_control_profile_permits_one_identified_worker_restart(self):
        before={'id':'a'*64,'running':False,'health':'unhealthy'};after=dict(before,running=True,health='healthy')
        unit={'name':'kolla-nova_conductor-container.service','sha256':'b'*64,'active':'active'}
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(n.os,'geteuid',return_value=0));stack.enter_context(patch.dict(os.environ,{'SUDO_USER':'joe'}))
            stack.enter_context(patch.object(n.socket,'gethostname',return_value='node1'))
            stack.enter_context(patch.object(n,'inspector',return_value={'collect':lambda _:records()['node1']['host']}))
            stack.enter_context(patch.object(n,'container',side_effect=[before,after]));stack.enter_context(patch.object(n,'unit',return_value=unit))
            command=stack.enter_context(patch.object(n,'run',return_value=''))
            result=n.execute('node1','restart','nova_conductor','a'*64,'b'*64,CLUSTER,'reboot-control')
        command.assert_called_once_with(['systemctl','restart',unit['name']],timeout=90);self.assertTrue(n.healthy(result['container']))

    def test_real_kolla_unit_template_and_shared_image_aliases(self):
        path=ROOT/'build/real-tools-check/share/kolla-ansible/ansible/module_utils/kolla_systemd_worker.py'
        tree=ast.parse(path.read_text())
        template=next(ast.literal_eval(x.value) for x in tree.body if isinstance(x,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TEMPLATE' for t in x.targets))
        for name in r.ORDER:
            raw=Template(template).substitute(service_name=name,engine='docker',deps='docker.service',restart_timeout=120,restart_retries=10,name=name,graceful_timeout=60,restart_policy='always',restart_duration=11).encode()
            fake=SimpleNamespace(lstat=lambda:SimpleNamespace(st_mode=stat.S_IFREG|0o644,st_uid=0),read_bytes=lambda:raw)
            props='LoadState=loaded\nActiveState=active\nFragmentPath=/etc/systemd/system/kolla-'+name+'-container.service\nDropInPaths=\n'
            with patch.object(n,'Path',return_value=fake),patch.object(n,'run',return_value=props):self.assertEqual(n.unit(name)['active'],'active')
        for name in ('neutron_rpc_server','neutron_periodic_worker','nova_metadata'):
            image='nova-api' if name=='nova_metadata' else 'neutron-server'
            value={'id':'a'*64,'image':'quay.io/openstack.kolla/'+image+':2026.2-ubuntu-noble','running':True,'health':'unhealthy'}
            with patch.object(n,'run',return_value=json.dumps(value)):self.assertEqual(n.container(name)['id'],'a'*64)

    def test_prior_pending_or_repeated_same_boot_repair_blocked_completed_old_boot_allowed(self):
        state={'complete':False,'boots':BOOT,'attempts':[]};earlier=[(Path('reboot-recovery-old.json'),state)]
        with self.assertRaises(RuntimeError):r.prior_guard(earlier,BOOT,r.planned(records()))
        state.update(complete=True,attempts=[{'kind':'restart','node':'node1','service':'placement_api'}])
        with self.assertRaises(RuntimeError):r.prior_guard(earlier,BOOT,r.planned(records()))
        r.prior_guard(earlier,dict(BOOT,node1=BOOT['node2']),r.planned(records()))
        r.prior_guard(earlier,BOOT,[])

    def test_workload_and_old_api_receipt_remain_byte_identical(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(r,'REPO',Path(folder)):
            root=Path(folder)/'private/phase5';root.mkdir(parents=True)
            values={'move-vm-state.json':b'{"pending":true}', 'api-recovery-state.json':b'{"pending":false,"attempted":true}'}
            for name,raw in values.items():path=root/name;path.write_bytes(raw);path.chmod(0o600)
            before=r.checkpoints();(root/'reboot-recovery-new.json').write_text('{"pending":true}')
            self.assertEqual(r.checkpoints(),before)
            for name,raw in values.items():self.assertEqual((root/name).read_bytes(),raw)

    def test_read_only_mode_and_transport_credentials(self):
        nodes=r.Nodes({},dict.fromkeys(r.ALLOWED,'hidden'))
        with self.assertRaisesRegex(RuntimeError,'Read-only'):nodes.call('node1','restart','nova_api',{},CLUSTER)
        def remote(argv,**kwargs):
            self.assertNotIn('hidden',' '.join(argv));self.assertEqual(kwargs['input'],'hidden\n')
            args=shlex.split(argv[-1]);self.assertIn('--profile',args);self.assertIn('reboot-control',args)
            return subprocess.CompletedProcess(argv,0,json.dumps({'node':'node1','passed':True}),'')
        with patch.object(r.base.subprocess,'run',side_effect=remote):nodes.call('node1','inspect')

    def test_exact_recovery_command_and_strict_recap(self):
        args=r.recovery_args(Path('/config'),Path('/inventory'),Path('/options'))
        self.assertEqual(args[1:8],['mariadb-recovery','--configdir','/config','-i','/inventory','--limit','baremetal'])
        self.assertIn('--become',args);self.assertIn('@/options',args)
        good='PLAY RECAP\n'+'\n'.join(node+' : ok=29 changed=2 unreachable=0 failed=0 skipped=56 rescued=0 ignored=0' for node in r.ALLOWED)
        r.check_recap(good)
        for bad in (good.replace('failed=0','failed=1',1),good.replace('ignored=0','ignored=1',1),good.replace('node3 :','node4 :'),''):
            with self.assertRaises(RuntimeError):r.check_recap(bad)

    def test_check_uses_existing_dashboard_and_help_has_no_probes(self):
        with patch.object(r.dashboard,'main',return_value=0) as check:self.assertEqual(r.main(['--check']),0)
        check.assert_called_once_with(['--check','--compact'])
        with patch.object(r.subprocess,'run',side_effect=AssertionError('no I/O')),contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as result:r.main(['--help'])
        self.assertEqual(result.exception.code,0)

    def test_bash_wrapper_forwards_only_one_supported_mode(self):
        source=(ROOT/'scripts/lab-recover.sh').read_text().replace('exec /usr/bin/python3','exec "$FAKE_PYTHON"')
        with tempfile.TemporaryDirectory() as folder:
            script=Path(folder)/'wrapper.sh';script.write_text(source)
            fake=Path(folder)/'python';fake.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n');fake.chmod(0o700)
            env=dict(os.environ,FAKE_PYTHON=str(fake))
            for mode in ([],['--check'],['--plan'],['--apply']):
                result=subprocess.run(['bash',str(script),*mode],capture_output=True,text=True,env=env)
                self.assertEqual(result.returncode,0);self.assertIn(mode[0] if mode else '--check',result.stdout)
            for mode in (['--invalid'],['--apply','--check']):
                result=subprocess.run(['bash',str(script),*mode],capture_output=True,text=True,env=env)
                self.assertEqual(result.returncode,2)


if __name__=='__main__':unittest.main()

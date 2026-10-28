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
        for action in ('network-children','routers','poweroff','database','broker','running','restart','stop'):
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
        self.assertIn(json.dumps({'skip_stop_containers':list(s.RETAIN)}),args)
        args=s.stop_args('/config','/inventory','/private/options','mariadb','node2')
        self.assertEqual(args[args.index('--limit')+1],'node2');self.assertEqual(args[-2:],['--tags','mariadb'])
    def workflow(self,fail=False,remaining=False,bad_db=False):
        calls=[];saves=[];db=positions()
        if bad_db:db['node3']['safe_to_bootstrap']=0
        def remote(node,action):
            calls.append((node,action))
            if action=='running':
                if any(c[1]=='database' for c in calls):return {'running':[]}
                if ('stop','loadbalancer',node) in calls:return {'running':['mariadb','rabbitmq']}
                return {'running':list(s.RETAIN)}
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
        self.assertEqual([c for c in calls if c[0]=='stop'],[('stop',None,None)]+[('stop','loadbalancer',n) for n in s.NODES]+[('stop','rabbitmq',n) for n in s.NODES]+[('stop','mariadb',n) for n in s.NODES])
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
            self.assertRaises(RuntimeError,n.action,'node1','network-children');run.assert_not_called()
    def test_live_dhcp_names_on_node1_node3_pass_readiness(self):
        r=records()
        for node in ('node1','node3'):
            r[node]['running'] += ['neutron_dhcp_agent_'+service+'_qdhcp-74c1c96b-caa9-43b2-9498-a4239dda07f1' for service in ('dnsmasq','haproxy')]
        self.assertEqual(s.baseline(r),UUID)
        self.assertTrue(s.check_lines(r)[0])
    def test_network_child_scope_matches_in_controller_and_node(self):
        for prefix in ('neutron_dhcp_agent_dnsmasq_qdhcp-','neutron_dhcp_agent_haproxy_qdhcp-',
                       'neutron_l3_agent_keepalived_qrouter-','neutron_l3_agent_haproxy_qrouter-'):
            name=prefix+UUID
            self.assertTrue(s.allowed(name));self.assertTrue(n.network_child(name))
        for name in ('neutron_dhcp_agent_unknown_qdhcp-'+UUID,'neutron_dhcp_agent_dnsmasq_qrouter-'+UUID,
                     'neutron_dhcp_agent_dnsmasq_qdhcp-'+'-'*36,'arbitrary_container'):
            self.assertFalse(s.allowed(name));self.assertFalse(n.network_child(name))
    def test_no_helper_stop_while_dhcp_agent_or_unknown_container_runs(self):
        for extra in ('neutron_dhcp_agent','unrecognized'):
            with patch.object(n,'guard'),patch.object(n,'running',return_value=['mariadb','rabbitmq',extra]),patch.object(n,'run') as run:
                self.assertRaises(RuntimeError,n.action,'node1','network-children');run.assert_not_called()
    def test_dhcp_and_router_children_stopped_after_parents_without_deletion(self):
        children=['neutron_dhcp_agent_'+service+'_qdhcp-'+UUID for service in ('dnsmasq','haproxy')]
        children+=['neutron_l3_agent_'+service+'_qrouter-'+UUID for service in ('keepalived','haproxy')]
        with patch.object(n,'guard'),patch.object(n,'running',return_value=['mariadb','rabbitmq']+children),patch.object(n,'stop_child') as stop:
            self.assertEqual(n.action('node1','network-children'),{'network_children_stopped':4})
            self.assertEqual([c.args[0] for c in stop.call_args_list],children)
    def test_killed_network_child_blocks_further_shutdown(self):
        child='neutron_dhcp_agent_dnsmasq_qdhcp-'+UUID
        with patch.object(n,'guard'),patch.object(n,'running',return_value=['mariadb','rabbitmq',child]),patch.object(n,'stop_child',side_effect=RuntimeError('exit 137')):
            self.assertRaises(RuntimeError,n.action,'node1','network-children')
    def child_stop(self,auto=True,exit_code='0',destroy=True,oom=False,wrong_id=False):
        name='neutron_dhcp_agent_dnsmasq_qdhcp-'+UUID;cid='a'*64;calls=[]
        def run(args):
            calls.append(args)
            if args[:2]==['docker','stop']:return ''
            if args[:2]==['docker','events']:
                events=[{'Action':'die','Actor':{'ID':'b'*64 if wrong_id else cid,'Attributes':{'name':name,'exitCode':exit_code}}}]
                for action in (['destroy'] if destroy else [])+(['oom'] if oom else []):
                    events.append({'Action':action,'Actor':{'ID':cid,'Attributes':{'name':name}}})
                return '\n'.join(map(json.dumps,events))
            if args[-1]==name:return json.dumps({'id':cid,'name':'/'+name,'auto_remove':auto})
            return json.dumps({'Status':'exited','Running':False,'ExitCode':int(exit_code),'OOMKilled':oom})
        with patch.object(n,'run',side_effect=run):n.stop_child(name)
        return calls
    def test_autoremoved_success_uses_die_destroy_events_not_missing_inspect(self):
        calls=self.child_stop()
        self.assertEqual(sum(c[:2]==['docker','inspect'] for c in calls),1)
        self.assertIn(['docker','stop','--time','60','a'*64],calls)
        self.assertTrue(any(c[:2]==['docker','events'] for c in calls))
    def test_retained_helper_uses_exit_state(self):
        calls=self.child_stop(auto=False)
        self.assertEqual(sum(c[:2]==['docker','inspect'] for c in calls),2)
        self.assertFalse(any(c[:2]==['docker','events'] for c in calls))
    def test_missing_bad_or_foreign_events_block(self):
        for kwargs in ({'exit_code':'137'},{'destroy':False},{'oom':True},{'wrong_id':True}):
            self.assertRaises(RuntimeError,self.child_stop,**kwargs)
    def partial(self):
        state={'pending':True,'database':{},'cluster_uuid':UUID,
          'stage':'1/3 Stop remaining DHCP/router helpers on node1',
          'attempts':['1/3 Stop cloud clients and agents; retain database, broker and proxies','1/3 Stop remaining DHCP/router helpers on node1']}
        r={node:{'vm_processes':0,'database':records()[node]['host']['galera']['value'],
          'broker':{'healthy':True},'running':list(s.RETAIN),
          'support':{name:{'running':True,'health':'healthy'} for name in s.RETAIN}} for node in s.NODES}
        return state,r
    def test_finish_validates_exact_partial_phase_and_all_nodes(self):
        state,r=self.partial();s.continuation(state,r)
        for field,value in [('stage','2/3 Stop RabbitMQ on node1'),('pending',False),('database',{'node1':{}}),('cluster_uuid','different')]:
            bad=copy.deepcopy(state);bad[field]=value;self.assertRaises(RuntimeError,s.continuation,bad,r)
        del r['node3'];self.assertRaises(RuntimeError,s.continuation,state,r)
    def test_finish_blocks_new_clients_vms_db_or_broker_failures(self):
        for key,value in [('vm_processes',1),('database',{}),('broker',{}),('support',{}),('running',list(s.RETAIN)+['nova_api'])]:
            state,r=self.partial();r['node2'][key]=value;self.assertRaises(RuntimeError,s.continuation,state,r)
    def test_finish_workflow_never_repeats_cloud_stop(self):
        state,_=self.partial();calls=[];stages=[]
        def remote(node,action):
            if action=='database':return positions()[node]
            if action=='running':
                if any(c[0]=='mariadb' for c in calls):return {'running':[]}
                return {'running':['mariadb','rabbitmq'] if ('loadbalancer',node) in calls else list(s.RETAIN)}
            return {}
        s.workflow(remote,lambda service,node:calls.append((service,node)),lambda _:None,stages.append,UUID,previous=state)
        self.assertNotIn((None,None),calls)
        self.assertEqual(calls[:3],[('loadbalancer',node) for node in s.NODES])
    def test_poweroff_is_not_an_available_node_action(self):
        with patch.object(n,'guard'),patch.object(n,'run') as run:
            self.assertRaises(RuntimeError,n.action,'node1','poweroff');run.assert_not_called()
        self.assertNotIn('/usr/sbin/shutdown',(ROOT/'scripts/lab-shutdown-node.py').read_text())
    def test_success_finishes_without_host_shutdown(self):
        state,calls,saves=self.workflow();logs=[]
        s.finish(state,lambda v:saves.append(copy.deepcopy(v)),logs.append)
        self.assertFalse(state['pending'])
        self.assertIn('RESULT: READY_FOR_MANUAL_POWEROFF',logs)
        self.assertIn('All three Ubuntu hosts remain powered on.',logs[-1])
        self.assertFalse(any(c[1]=='poweroff' for c in calls))
    def test_help_default_has_no_lab_access(self):
        with patch.object(s.health.inv,'ensure_agent') as agent,patch('builtins.print'):
            self.assertEqual(s.main([]),0);agent.assert_not_called()

if __name__=='__main__':unittest.main()

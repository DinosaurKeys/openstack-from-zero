import base64
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def load(name,file):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/file)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
h=load('health_tests','lab-health.py');n=load('health_node_tests','lab-health-node.py')
CLUSTER='f3609644-d1e0-11f1-a749-0ef43e7d82dc'
PROJECT='a'*32;USER='b'*32
SERVER='0ef50722-c056-49fa-8f9e-05e926a8b41d'
VOLUME='eebabffa-2a1b-4ea5-bdd7-62082f66df77'
ROUTER='a3c6cb8e-cba3-49d0-964b-cf131e1075c5'

def galera():
    return {'wsrep_local_state_comment':'Synced','wsrep_cluster_size':'3','wsrep_cluster_state_uuid':CLUSTER,
      'wsrep_cluster_status':'Primary','wsrep_connected':'ON','wsrep_ready':'ON'}

def host(node):
    return {'node':node,'observed':True,'vip_interfaces':['enp0s31f6'] if node=='node2' else [],
      'galera':{'observed':True,'value':galera()},'clock':{'observed':True,'value':True},
      **{k:{'observed':True,'value':{'healthy':True}} for k in ('containers','rabbitmq','provider_network')}}

def services(binaries):
    return [{'binary':b,'host':node,'status':'enabled','state':'up'} for b in binaries for node in h.NODES]

def agents():
    return [{'binary':b,'host':node,'alive':True,'admin_state_up':True} for b in
      ('neutron-dhcp-agent','neutron-l3-agent','neutron-openvswitch-agent','neutron-metadata-agent') for node in h.NODES]


class HealthTests(unittest.TestCase):
    def test_short_summary_names_actual_failures_and_preserves_uncertainty(self):
        records={node:host(node) for node in h.NODES}
        failed=['placement_api','nova_api','nova_metadata','nova_scheduler','nova_conductor','neutron_server','cinder_api']
        for node in ('node1','node3'):
            records[node]['containers']['value'].update(healthy=False,unhealthy=failed,missing=[])
        records['node2']={'node':'node2','observed':False,'error':'SSH/sudo or structured host response unavailable'}
        rows=[];details={'hosts':records};h.host_rows(records,rows,details)
        details['exercise checkpoints']={'unresolved':['phase5/move-vm-state.json']}
        rows.append({'check':'exercise checkpoints','status':'REVIEW'})
        text='\n'.join(h.summary_lines(rows,details))
        self.assertIn('node1 unhealthy/stopped: '+', '.join(failed),text)
        self.assertIn('node3 unhealthy/stopped: '+', '.join(failed),text)
        self.assertIn('node2: SSH/sudo or structured host response unavailable',text)
        self.assertIn('node is not proven down',text)
        self.assertIn('Do not repeat full-cluster database recovery',text)
        self.assertIn('Preserved; no automatic retry or reset',text)

    def test_transport_reasons_and_api_errors_are_safe_and_specific(self):
        rows=[];details={}
        h.checked(rows,details,'compute',lambda:(_ for _ in ()).throw(RuntimeError('API request returned HTTP 503 password=private')))
        self.assertEqual(details['compute']['error'],'HTTP 503; read unavailable')
        self.assertNotIn('private',json.dumps(details))
        for message,expected in [('Sorry, try again. private-password','Sudo authentication failed'),
          ('Permission denied (publickey).','SSH key authentication rejected'),
          ('Host key verification failed.','SSH host identity needs review')]:
            self.assertEqual(h.transport_reason(1,message),expected)

    def test_default_plan_has_no_network_or_mutations(self):
        with patch.object(h.subprocess,'run',side_effect=AssertionError('no commands')),patch.object(h.os,'execve',side_effect=AssertionError('no reexec')),contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(h.main([]),0)
        self.assertIn('PLAN ONLY',out.getvalue())
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):h.main(['--apply'])

    def test_node_identity_guard_precedes_commands(self):
        with patch.object(n.os,'geteuid',return_value=0),patch.dict(os.environ,{'SUDO_USER':'joe'}),patch.object(n.socket,'gethostname',return_value='wrong-node'),patch.object(n,'run',side_effect=AssertionError('no read')):
            with self.assertRaises(RuntimeError):n.collect('node1')

    def test_stopped_database_does_not_hide_other_node_checks(self):
        addresses=[{'ifname':'enp0s31f6','addr_info':[{'local':'192.168.0.200','prefixlen':24}]}]
        with patch.object(n.os,'geteuid',return_value=0),patch.dict(os.environ,{'SUDO_USER':'joe'}),patch.object(n.socket,'gethostname',return_value='node1'),patch.object(n,'run',return_value=json.dumps(addresses)),patch.object(n,'containers',return_value={'healthy':False}),patch.object(n,'galera',side_effect=RuntimeError('docker read query exited 1')),patch.object(n,'broker',return_value={'healthy':True}) as broker,patch.object(n,'network',return_value={'healthy':True}) as network:
            result=n.collect('node1')
        self.assertFalse(result['galera']['observed'])
        self.assertTrue(result['rabbitmq']['value']['healthy']);self.assertTrue(result['provider_network']['value']['healthy'])
        broker.assert_called_once();network.assert_called_once()

    def test_glance_placement_and_unhealthy_container(self):
        calls=[]
        def run(args,**kwargs):
            calls.append(args)
            if args[:3]==['docker','ps','-a']:return '\n'.join(n.REQUIRED)
            return '\n'.join(json.dumps({'name':'/'+name,'running':True,'status':'running','health':'unhealthy' if name=='proxysql' else 'healthy'}) for name in args[4:])
        with patch.object(n,'run',side_effect=run):
            node1=n.containers('node1');node2=n.containers('node2')
        self.assertEqual(node1['missing'],['glance_api'])
        self.assertEqual(node2['missing'],[])
        self.assertEqual(node2['unhealthy'],['proxysql'])
        self.assertNotIn('.Config.Env',' '.join(calls[1]))

    def test_galera_query_uses_existing_monitor_file_and_show_only(self):
        raw='\n'.join(k+'\t'+v for k,v in galera().items())
        with patch.object(n,'run',return_value=raw) as run:self.assertEqual(n.galera('192.168.0.200'),galera())
        command=run.call_args.args[0]
        self.assertIn('--defaults-file=/etc/mysql/healthcheck.cnf',command)
        self.assertTrue(command[-1].startswith('--execute=SHOW GLOBAL STATUS'))
        self.assertNotIn('--password',' '.join(command))
        with patch.object(n,'run',return_value=raw+'\nwsrep_ready\tON'):
            with self.assertRaises(RuntimeError):n.galera('192.168.0.200')

    def test_galera_split_uuids_and_missing_member_cannot_pass(self):
        records={node:host(node) for node in h.NODES};rows=[];details={}
        h.host_rows(records,rows,details);self.assertTrue(all(r['status']=='PASS' for r in rows))
        records['node3']['galera']['value']['wsrep_cluster_state_uuid']='669c5556-d132-11f1-98ea-2793b69ea273'
        rows=[];h.host_rows(records,rows,{})
        self.assertEqual(next(r['status'] for r in rows if r['check'].startswith('Galera:')),'REVIEW')
        records['node3']={'node':'node3','observed':False};rows=[];h.host_rows(records,rows,{})
        self.assertEqual(next(r['status'] for r in rows if r['check'].startswith('Galera:')),'UNKNOWN')

    def test_non_primary_and_two_member_cluster_require_review(self):
        for key,value in [('wsrep_cluster_status','Non-Primary'),('wsrep_cluster_size','2'),('wsrep_ready','OFF'),('wsrep_cluster_state_uuid','invalid')]:
            status=galera();status[key]=value;self.assertFalse(h.member_ok(status))

    def test_duplicate_or_wrong_interface_vip_does_not_pass(self):
        records={node:host(node) for node in h.NODES}
        for locations in (['enp0s31f6'],['br-ex']):
            records['node1']['vip_interfaces']=locations;rows=[];h.host_rows(records,rows,{})
            self.assertEqual(next(r['status'] for r in rows if r['check'].startswith('API VIP:')),'REVIEW')

    def test_rabbit_cluster_partition_alarm_missing_member_detected(self):
        base={'disk_nodes':['rabbit@'+name for name in h.NODES],'running_nodes':['rabbit@'+name for name in h.NODES],'partitions':{},'alarms':[]}
        def test(value,alarm=False):
            def run(args,**kwargs):
                if args[3]=='rabbitmqctl':return json.dumps(value)
                if alarm and args[-1]=='check_local_alarms':raise RuntimeError('alarm')
                return 'Check passed'
            with patch.object(n,'run',side_effect=run):return n.broker()
        self.assertTrue(test(base)['healthy'])
        self.assertFalse(test(dict(base,partitions={'rabbit@node1':['rabbit@node3']}))['healthy'])
        self.assertFalse(test(dict(base,running_nodes=['rabbit@node1']))['healthy'])
        self.assertFalse(test(base,True)['healthy'])

    def test_service_and_agent_heartbeat_counts_not_container_health(self):
        rows=services(('nova-scheduler','nova-conductor','nova-compute'))
        self.assertTrue(h.service_health(rows,('nova-scheduler','nova-conductor','nova-compute'))[0])
        rows[-1]['state']='down';self.assertFalse(h.service_health(rows,('nova-scheduler','nova-conductor','nova-compute'))[0])
        self.assertFalse(h.service_health(rows+rows[:1],('nova-scheduler','nova-conductor','nova-compute'))[0])
        data=agents();self.assertTrue(h.neutron_health(data)[0]);data[-1]['alive']=False
        self.assertFalse(h.neutron_health(data)[0]);self.assertFalse(h.neutron_health(data[:-1])[0])

    def test_pending_migration_preserved_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(h,'REPO',Path(folder)):
            path=Path(folder)/'private/phase5/move-vm-state.json';path.parent.mkdir(parents=True)
            raw=b'{"pending":true,"attempted":true,"stage":"migration requested"}\n';path.write_bytes(raw);path.chmod(0o600)
            rows=[];details={};h.checkpoint_checks(rows,details)
            self.assertEqual(rows[0]['status'],'REVIEW');self.assertEqual(path.read_bytes(),raw)
            self.assertFalse(details['exercise checkpoints']['changed'])

    def test_auth_failure_still_returns_host_evidence_no_passwords(self):
        rows=[];details={'hosts':{'node1':'collected'}}
        with patch.object(h.deploy.validation,'local_checks',side_effect=RuntimeError('password=private')):
            h.api_checks(rows,details,{})
        self.assertEqual(details['hosts'],{'node1':'collected'})
        self.assertTrue(all(r['status']=='UNKNOWN' for r in rows))
        self.assertNotIn('private',json.dumps(details))

    def test_readonly_api_cannot_write(self):
        api=h.ReadAPI(None,{},None)
        for action in ('POST','PUT','DELETE'):
            with self.assertRaises(RuntimeError):api.write('compute',action,'anything',{},200)

    def test_exact_remote_payload_and_sudo_password_transport(self):
        def run(argv,**kwargs):
            self.assertNotIn('private-password',' '.join(argv));self.assertEqual(kwargs['input'],'private-password\n')
            remote=shlex.split(argv[-1]);self.assertEqual(remote[:5],['sudo','-S','-p','','/usr/bin/python3'])
            import ast
            tree=ast.parse(remote[6]);encoded=tree.body[1].value.args[0].args[0].args[0].value
            source=base64.b64decode(encoded).decode();self.assertIn('def collect(node)',source)
            # Exercise the emitted program's real __main__ and argument parser.
            def child(args,**kw):
                self.assertEqual(args,['ip','-j','address','show'])
                return subprocess.CompletedProcess(args,0,'[]','')
            with patch.object(os,'geteuid',return_value=0),patch.dict(os.environ,{'SUDO_USER':'joe'}),patch.object(n.socket,'gethostname',return_value='node1'),patch.object(subprocess,'run',side_effect=child),patch('sys.argv',['health-node','--node','node1']),contextlib.redirect_stdout(io.StringIO()) as out:
                with self.assertRaises(SystemExit):exec(compile(source,'remote-fixture','exec'),{'__name__':'__main__'})
            value=json.loads(out.getvalue());self.assertEqual(value['node'],'node1');self.assertFalse(value['observed'])
            return subprocess.CompletedProcess(argv,1,out.getvalue(),'')
        with patch.object(h.subprocess,'run',side_effect=run):result=h.host_observe('node1','private-password',{})
        self.assertFalse(result['observed'])

    def test_host_transport_timeout_is_unknown_and_stderr_not_exposed(self):
        for effect in (subprocess.TimeoutExpired(['ssh'],240),):
            with patch.object(h.subprocess,'run',side_effect=effect):self.assertFalse(h.host_observe('node2','x',{})['observed'])
        with patch.object(h.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','password=private')):
            self.assertNotIn('private',json.dumps(h.host_observe('node2','x',{})))

    def test_full_api_snapshot_and_independent_failures(self):
        urls={'network':'https://192.168.0.151:9696','compute':'https://192.168.0.151:8774/v2.1',
          'image':'https://192.168.0.151:9292','block-storage':'https://192.168.0.151:8776/v3',
          'placement':'https://192.168.0.151:8780','identity':'https://192.168.0.151:5000'}
        calls=[];mode=['healthy']
        api_data={urls['compute']+'/os-services':{'services':services(('nova-scheduler','nova-conductor','nova-compute'))},
          urls['compute']+'/os-hypervisors/detail':{'hypervisors':[{'hypervisor_hostname':node,'state':'up','status':'enabled'} for node in h.NODES]},
          urls['network']+'/v2.0/agents':{'agents':agents()},
          urls['block-storage']+'/os-services':{'services':services(('cinder-scheduler','cinder-volume'))},
          urls['network']+'/v2.0/routers/'+ROUTER:{'router':{'id':ROUTER,'project_id':PROJECT,'status':'ACTIVE','ha':True}},
          urls['network']+'/v2.0/routers/'+ROUTER+'/l3-agents':{'agents':[{'host':node,'alive':True,'admin_state_up':True,'ha_state':'active' if node=='node2' else 'standby'} for node in h.NODES]},
          urls['compute']+'/servers/'+SERVER:{'server':{'id':SERVER,'status':'ACTIVE','OS-EXT-STS:task_state':None,'OS-EXT-SRV-ATTR:host':'node1','os-extended-volumes:volumes_attached':[{'id':VOLUME}]}},
          urls['block-storage']+'/volumes/'+VOLUME:{'volume':{'id':VOLUME,'status':'in-use','attachments':[{'server_id':SERVER}],'os-vol-host-attr:host':'node3@lvm-1#lvm-1'}}}
        for service,(path,key) in h.access.API_PATHS.items():api_data[urls[service]+'/'+path]={key:[]}
        class Client:
            def horizon(self):return {'https_verified':True,'login_form_present':True}
            def request(self,url,token=None,body=None,expect=200):
                self_assert=body is None and expect==200
                if not self_assert:raise AssertionError('Unexpected mutation')
                calls.append(url)
                if mode[0]=='image-down' and url.startswith(urls['image']):raise RuntimeError('unavailable')
                return api_data[url],None
        original=SimpleNamespace(client=Client(),urls=urls,token='token-never-printed')
        states={'networks.json':{'project_id':PROJECT,'api':urls['network'],'ids':{'router':ROUTER}},
          'vm.json':{'project_id':PROJECT,'user_id':USER,'apis':urls,'ids':{'server':SERVER}},
          'volume.json':{'project_id':PROJECT,'user_id':USER,'apis':urls,'volume_id':VOLUME}}
        with patch.object(h.deploy.validation,'local_checks',return_value=('inventory','config')),patch.object(h.prep,'authenticate',return_value=(original,PROJECT,USER)),patch.object(h,'read_private',side_effect=lambda p:states[p.name]):
            rows=[];details={};h.api_checks(rows,details,{})
            self.assertTrue(all(r['status']=='PASS' for r in rows),rows)
            self.assertNotIn(original.token,json.dumps(details))
            mode[0]='image-down';api_data[urls['compute']+'/servers/'+SERVER]['server']['status']='ERROR'
            rows=[];details={};h.api_checks(rows,details,{})
            results={r['check']:r['status'] for r in rows}
            self.assertEqual(results['image HTTPS API read'],'UNKNOWN')
            self.assertEqual(results['existing VM/volume API state'],'REVIEW')
            self.assertEqual(results['Neutron agents'],'PASS')
            self.assertEqual(results['Cinder services'],'PASS')
            self.assertEqual(results['router HA (API state)'],'PASS')
        self.assertTrue(calls)

    def test_node_read_commands_match_pinned_service_names(self):
        base=ROOT/'build/real-tools-check/share/kolla-ansible/ansible/roles'
        if not base.is_dir():self.skipTest('Pinned Kolla sources unavailable')
        text='\n'.join(p.read_text() for p in base.glob('*/defaults/main.yml'))
        for name in n.REQUIRED:
            if name=='rabbitmq':
                self.assertIn('container_name: "{{ project_name }}"',(base/'rabbitmq/defaults/main.yml').read_text())
                self.assertIn('project_name: "rabbitmq"',(base/'rabbitmq/vars/main.yml').read_text())
                continue
            self.assertTrue(re.search(r'container_name:\s*["\']?'+re.escape(name)+r'["\']?\s*$',text,re.M),name+' must exist in pinned service definitions')
        inventory=(base.parent/'inventory/multinode').read_text()
        for group in ('neutron-server','neutron-rpc-server','neutron-periodic-worker'):
            self.assertIn('['+group+':children]\ncontrol',inventory)


if __name__=='__main__':unittest.main()

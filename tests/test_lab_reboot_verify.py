import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def load(name,file):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/file)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
r=load('reboot_verify_test','lab-reboot-verify.py');g=load('reboot_guest_test','lab-reboot-guest.py')
SERVER='0ef50722-c056-49fa-8f9e-05e926a8b41d';VOLUME='eebabffa-2a1b-4ea5-bdd7-62082f66df77';FS='f1568275-f734-4bbc-867e-26a822d2cb0d'
BOOT='c34e6666-3431-4ec9-b3f6-bfd0f2828498';NONCE='a'*32
CONTENT=('OpenStack Chapter E persistence test\nserver='+SERVER+'\nvolume='+VOLUME+'\nnonce='+NONCE+'\n').encode()
EXPECTED={'server_id':SERVER,'volume_id':VOLUME,'filesystem_uuid':FS,'nonce':NONCE,'sha256':hashlib.sha256(CONTENT).hexdigest()}


class RebootVerificationTests(unittest.TestCase):
    def dashboard(self):
        details={'hosts':{},'cluster_summary':[],'summary':[{'check':'Existing checkpoints/config preserved','status':'PASS'},
          {'check':'VM in Nova','status':'REVIEW'}],
          'Nova services':[],'Cinder services':[],'hypervisors':[],'Neutron agents':[],
          'router HA (API state)':{'agents':[]},'API VIP: exactly one owner':{'owners':['node3']},
          'VM identity':{'server_status':'ERROR','host':'node1'},'Migration record (preserved)':{'present':True,'pending':True,'snapshot':{'migration':{'status':'error','source_compute':'node1','dest_compute':None}}}}
        required=load('dashboard_node_fixture','lab-health-node.py').REQUIRED
        for node in r.inv.NODES:
            names=required+(('glance_api',) if node=='node1' else ())
            details['hosts'][node]={'galera':{'observed':True,'value':{'wsrep_local_state_comment':'Synced','wsrep_cluster_status':'Primary','wsrep_cluster_size':'3','wsrep_connected':'ON','wsrep_ready':'ON','wsrep_cluster_state_uuid':'f3609644-d1e0-11f1-a749-0ef43e7d82dc'}},
              'containers':{'observed':True,'value':{'containers':{name:{'running':True,'health':'not configured' if name in ('keepalived','neutron_metadata_agent') else 'healthy'} for name in names},'missing':[]}},
              'rabbitmq':{'observed':True,'value':{'checks':{'check_running':True},'healthy':True,'cluster':{'running_nodes':['rabbit@'+n for n in r.inv.NODES],'partitions':{},'alarms':[]}}}}
            details['cluster_summary'] += [{'check':node+' '+name,'status':'PASS'} for name in ('management SSH/identity','clock sync','USB/br-ex')]
            details['Nova services'] += [{'host':node,'binary':name,'state':'up','status':'enabled'} for name in ('nova-scheduler','nova-conductor','nova-compute')]
            details['Cinder services'] += [{'host':node,'binary':name,'state':'up','status':'enabled'} for name in ('cinder-scheduler','cinder-volume')]
            details['hypervisors'].append({'hypervisor_hostname':node,'state':'up','status':'enabled'})
            details['Neutron agents'] += [{'host':node,'binary':name,'alive':True,'admin_state_up':True} for name in ('neutron-dhcp-agent','neutron-l3-agent','neutron-openvswitch-agent','neutron-metadata-agent')]
            details['router HA (API state)']['agents'].append({'host':node,'ha_state':'active' if node=='node3' else 'standby','alive':True,'admin_state_up':True})
        details['cluster_summary'] += [{'check':label,'status':'PASS'} for label in ('Galera: same three-member Primary cluster','API VIP: exactly one owner','HTTPS authentication','Horizon HTTPS','image HTTPS API read','compute HTTPS API read','network HTTPS API read','block-storage HTTPS API read','placement HTTPS API read','Nova services','Neutron agents','Cinder services','hypervisors','router HA (API state)')]
        return details

    def test_compact_healthy_preserves_result_with_shorter_output(self):
        details=self.dashboard();compact=r.render_compact(details);full=r.render_brief(details)
        self.assertLess(len(compact),len(full));self.assertLessEqual(len(compact),32)
        for value in ('PASS    Galera: 3/3 Synced/Primary, size 3, same cluster UUID',
          'PASS    RabbitMQ cluster: 3/3 running members, no alarms or partitions',
          'PASS    HTTPS: login, Horizon and all five service API reads','RESULT: REBOOT_SERVICES_HEALTHY'):
            self.assertIn(value,compact)
        self.assertNotIn('VM in Nova','\n'.join(compact))
        self.assertLess(next(i for i,v in enumerate(compact) if 'PASS    Galera:' in v),
                        next(i for i,v in enumerate(compact) if 'PASS    Placement:' in v))

    def test_compact_keeps_each_named_failure_and_safe_api_error(self):
        details=self.dashboard()
        details['hosts']['node3']['containers']['value']['containers']['nova_conductor'].update(running=False,status='exited',exit_code=1,health='unhealthy')
        next(v for v in details['cluster_summary'] if v['check']=='compute HTTPS API read')['status']='UNKNOWN'
        details['compute HTTPS API read']={'error':'HTTP 503 password=do-not-print'}
        text='\n'.join(r.render_compact(details))
        self.assertIn('node3: nova_conductor=EXITED exit=1',text);self.assertIn('BLOCKED Compute: HTTP 503',text)
        self.assertNotIn('do-not-print',text);self.assertIn('RESULT: REBOOT_SERVICES_NEED_REVIEW',text)

    def test_compact_does_not_hide_database_mismatch_or_missing_host(self):
        details=self.dashboard()
        details['hosts']['node1']['galera']['value']['wsrep_cluster_size']='2'
        details['hosts']['node2']={'observed':False,'error':'SSH key authentication rejected'}
        next(v for v in details['cluster_summary'] if v['check']=='node2 management SSH/identity')['status']='UNKNOWN'
        text='\n'.join(r.render_compact(details))
        self.assertIn('members=2',text);self.assertIn('node2 management SSH/identity: SSH key authentication rejected',text)
        self.assertNotIn('PASS    Nodes: 3/3',text);self.assertNotIn('PASS    Galera: 3/3 Synced',text)
        self.assertIn('RESULT: REBOOT_SERVICES_NEED_REVIEW',text)

    def test_compact_changed_protected_input_cannot_pass(self):
        details=self.dashboard()
        next(v for v in details['summary'] if v['check']=='Existing checkpoints/config preserved')['status']='REVIEW'
        self.assertIn('FAIL    Saved checkpoints/config preserved',r.render_compact(details))
        self.assertNotIn('RESULT: REBOOT_SERVICES_HEALTHY',r.render_compact(details))

    def test_saved_compact_snapshot_has_no_remote_reads_or_file_changes(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(r,'REPO',Path(directory)):
            reports=Path(directory)/'reports';reports.mkdir()
            path=reports/'reboot-verify-example.json';path.write_text(json.dumps(self.dashboard()));path.chmod(0o600)
            raw=path.read_bytes()
            with patch.object(r.health,'host_observe',side_effect=AssertionError('no SSH')),contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(r.saved_report(path,compact=True),0)
            self.assertIn('SAVED SNAPSHOT:',output.getvalue());self.assertIn('RESULT: REBOOT_SERVICES_HEALTHY',output.getvalue())
            self.assertEqual(path.read_bytes(),raw)

    def test_brief_healthy_groups_counts_and_glance_placement(self):
        text='\n'.join(r.render_brief(self.dashboard()))
        for value in ('PASS    MariaDB: 3/3 ready','PASS    Glance: 1/1 ready (node1 only)',
          'PASS    Nova services: 9/9 enabled/up','PASS    Cinder services: 6/6 enabled/up',
          'PASS    Neutron agents: 12/12 alive/enabled','RESULT: REBOOT_SERVICES_HEALTHY'):
            self.assertIn(value,text)
        self.assertNotIn('VM in Nova',text);self.assertNotIn('MISSING',text)

    def test_brief_starting_database_and_failed_auth_do_not_claim_agents_down(self):
        details=self.dashboard()
        for node in r.inv.NODES:
            details['hosts'][node]['galera']={'observed':False,'error':'database connection unavailable (exit 1)'}
            details['hosts'][node]['galera_log']={'observed':True,'value':{'view':'NON_PRIM','log_time':'2026-10-28 21:29:09'}}
            details['hosts'][node]['containers']['value']['containers']['mariadb'].update(health='starting')
            details['hosts'][node]['containers']['value']['containers']['nova_api'].update(health='unhealthy')
        for row in details['cluster_summary']:
            if row['check'] in ('HTTPS authentication','Galera: same three-member Primary cluster'):row['status']='UNKNOWN'
        details['HTTPS authentication']={'error':'HTTP 503 password=do-not-print'}
        text='\n'.join(r.render_brief(details))
        for value in ('FAIL    MariaDB: 0/3 ready','node1: mariadb=running/starting',
          'BLOCKED node1 Galera SQL status: database connection unavailable',
          'Recent log: NON_PRIM','log entry, not live SQL proof',
          'BLOCKED Service/agent checks not queried: authentication/client preparation unavailable',
          'Neutron agent heartbeats',
          'NEXT: Review MariaDB status/log evidence first'):
            self.assertIn(value,text)
        self.assertNotIn('do-not-print',text)
        self.assertNotIn('FAIL    Neutron agent heartbeats',text)
        self.assertNotIn('RESULT: REBOOT_SERVICES_HEALTHY',text)

    def test_brief_lists_stopped_container_exit_and_failed_service_identity(self):
        details=self.dashboard()
        details['hosts']['node3']['containers']['value']['containers']['nova_conductor'].update(running=False,status='exited',exit_code=1,health='unhealthy')
        details['Nova services'][0].update(state='down',status='disabled')
        next(v for v in details['cluster_summary'] if v['check']=='Nova services')['status']='REVIEW'
        text='\n'.join(r.render_brief(details))
        self.assertIn('node3: nova_conductor=EXITED exit=1',text)
        self.assertIn('node1/nova-scheduler=DOWN/disabled',text)
        self.assertIn('FAIL    Nova services: 8/9 enabled/up',text)

    def test_brief_missing_host_is_blocked_not_invented_down(self):
        details=self.dashboard();details['hosts']['node2']={'observed':False,'error':'SSH key authentication rejected'}
        for row in details['cluster_summary']:
            if row['check'].startswith('node2 '):row['status']='UNKNOWN'
        text='\n'.join(r.render_brief(details))
        self.assertIn('BLOCKED node2 management SSH/identity: SSH key authentication rejected',text)
        self.assertIn('node2: mariadb=container observation unavailable',text)
        self.assertNotIn('node2: mariadb=DOWN',text)

    def test_brief_duplicate_service_cannot_pass_by_count(self):
        details=self.dashboard();details['Nova services'].append(dict(details['Nova services'][0]))
        next(v for v in details['cluster_summary'] if v['check']=='Nova services')['status']='REVIEW'
        text='\n'.join(r.render_brief(details));self.assertIn('node1/nova-scheduler=DUPLICATE',text)
        self.assertNotIn('PASS    Nova services',text)

    def test_brief_http_failure_preserves_other_observations(self):
        details=self.dashboard();details['Neutron agents']={'error':'HTTP 503; private=do-not-print'}
        next(v for v in details['cluster_summary'] if v['check']=='Neutron agents')['status']='UNKNOWN'
        text='\n'.join(r.render_brief(details))
        self.assertIn('BLOCKED Neutron agent heartbeats: HTTP 503',text)
        self.assertIn('PASS    MariaDB: 3/3 ready',text);self.assertNotIn('do-not-print',text)

    def test_dashboard_shows_actual_service_agent_and_container_states(self):
        details=self.dashboard();details['Nova services'][2].update(state='down',status='disabled')
        details['Neutron agents'][0].update(alive=False,admin_state_up=True)
        details['hosts']['node2']['containers']['value']['containers']['nova_api']={'running':False,'health':'unhealthy'}
        for row in details['cluster_summary']:
            if row['check'] in ('Nova services','Neutron agents'):row['status']='REVIEW'
        text='\n'.join(r.render_report(details))
        for expected in ('NOVA SERVICES','nova-compute','DOWN/disabled','NEUTRON AGENTS','DHCP','DOWN/enabled','DOWN/unhealthy','CINDER SERVICES','HYPERVISORS','ACTIVE','STANDBY','HTTPS API READS'):
            self.assertIn(expected,text)
        self.assertIn('REBOOT_SERVICES_NEED_REVIEW',text)
        self.assertNotIn('VM / MIGRATION',text)

    def test_cluster_pass_is_separate_from_vm_migration_failure(self):
        details=self.dashboard();text='\n'.join(r.render_report(details))
        self.assertIn('CLUSTER SERVICES: HEALTHY',text);self.assertIn('RESULT: REBOOT_SERVICES_HEALTHY',text)
        self.assertNotIn('Nova VM status',text);self.assertNotIn('PENDING (preserved)',text)
        full='\n'.join(r.render_report(details,True))
        self.assertIn('CLUSTER RESULT: HEALTHY',full);self.assertIn('WORKLOAD RESULT: NEEDS REVIEW',full)
        self.assertIn('ERROR',full);self.assertIn('PENDING (preserved)',full)
        self.assertNotIn('"server_status":',full)

    def test_unknown_is_not_down_and_glance_placement_is_not_missing(self):
        details=self.dashboard();details['Nova services']={'error':'read unavailable'}
        details['hosts']['node2']['containers']={'observed':False}
        text='\n'.join(r.render_report(details))
        line=next(line for line in text.splitlines() if line.startswith('nova-compute '))
        self.assertIn('UNKNOWN',line);self.assertNotIn('DOWN',line)
        glance=next(line for line in text.splitlines() if line.startswith('glance_api '))
        self.assertIn('N/A (placement)',glance);self.assertNotIn('MISSING',glance)

    def test_duplicate_services_not_silently_selected(self):
        details=self.dashboard();details['Nova services'].append(dict(details['Nova services'][0]))
        self.assertIn('DUPLICATE','\n'.join(r.render_report(details)))

    def test_saved_snapshot_renders_without_probes_or_passwords_or_write(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(r,'REPO',Path(folder)):
            reports=Path(folder)/'reports';reports.mkdir();path=reports/'reboot-verify-20261028T114755Z-test.json'
            raw=json.dumps(self.dashboard());path.write_text(raw);path.chmod(0o600)
            with patch.object(r.subprocess,'run',side_effect=AssertionError('No probe')),patch.object(r.getpass,'getpass',side_effect=AssertionError('No password')),contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(r.saved_report(path),0)
            self.assertIn('SAVED SNAPSHOT',out.getvalue());self.assertIn('NEUTRON AGENTS',out.getvalue());self.assertEqual(path.read_text(),raw)

    def test_saved_snapshot_details_keeps_full_tables_without_probes(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(r,'REPO',Path(folder)):
            path=Path(folder)/'reports/reboot-verify-test.json';path.parent.mkdir();path.write_text(json.dumps(self.dashboard()));path.chmod(0o600)
            with patch.object(r.subprocess,'run',side_effect=AssertionError('No probe')),patch.object(r.getpass,'getpass',side_effect=AssertionError('No password')),contextlib.redirect_stdout(io.StringIO()) as out:
                r.saved_report(path,detailed=True)
            self.assertIn('CONTAINERS — process / healthcheck',out.getvalue());self.assertIn('UP/healthy',out.getvalue())

    def test_service_only_api_checks_do_not_query_vm_or_volume_identity(self):
        health_test=load('health_module_tests_for_reboot','lab-health.py')
        rows=[];details={}
        with patch.object(health_test.deploy.validation,'local_checks',side_effect=RuntimeError('unavailable')):
            health_test.api_checks(rows,details,{},include_workload=False)
        self.assertNotIn('existing VM/volume API state',[row['check'] for row in rows])

    def test_default_live_check_skips_vm_guest_migration_and_checkpoint_interpretation(self):
        fixture=self.dashboard()
        with tempfile.TemporaryDirectory() as folder,patch.object(r,'REPO',Path(folder)):
            for name in ('phase4','phase5'):(Path(folder)/'private'/name).mkdir(parents=True)
            def hosts(records,rows,details):
                details.update(fixture)
                rows.extend(fixture['cluster_summary'])
            def apis(rows,details,env,include_workload=True):
                self.assertFalse(include_workload)
            with patch.object(r.deploy,'source_guard'),patch.object(r.deploy.tools,'tool_environment',return_value={}),patch.object(r.deploy.validation,'local_checks',return_value=('inv','config')),patch.object(r.health.access,'input_hashes',return_value={}),patch.object(r.inv,'ensure_agent'),patch.object(r.getpass,'getpass',return_value='sudo'),patch.object(r.health,'host_observe',side_effect=lambda node,*args:fixture['hosts'][node]),patch.object(r.health,'host_rows',side_effect=hosts),patch.object(r.health,'api_checks',side_effect=apis),patch.object(r.health,'checkpoint_checks',side_effect=AssertionError('No migration/checkpoint interpretation')),patch.object(r,'workload',side_effect=AssertionError('No VM/guest checks')),patch.object(r.prep,'authenticate',side_effect=AssertionError('No additional workload authentication')):
                output=[];details={};self.assertEqual(r.execute(output.append,details),0)
            text='\n'.join(output)
            self.assertIn('REBOOT_SERVICES_HEALTHY',text);self.assertNotIn('Checking the existing VM',text)
            self.assertNotIn('WORKLOAD RESULT',text)

    def test_paused_domain_is_not_reported_as_running(self):
        helper=load('reboot_runtime_node_test','phase5-migration-node.py')
        for state,expected in [('running',True),('paused',False)]:
            def virsh(*args,**kwargs):return SimpleNamespace(stdout=SERVER+'\n' if args[0]=='list' else state+'\n')
            with patch.object(helper,'identity',return_value={}),patch.object(helper,'virsh',side_effect=virsh):
                value=helper.execute('node1','running',{'server_id':SERVER})
            self.assertEqual(value['VM_running'],expected);self.assertEqual(value['domain_state'],state)

    def test_plan_has_no_commands_and_no_apply_mode(self):
        with patch.object(r.subprocess,'run',side_effect=AssertionError('no command')),contextlib.redirect_stdout(io.StringIO()) as out:self.assertEqual(r.main([]),0)
        self.assertIn('No repair mode',out.getvalue())
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):r.main(['--apply'])

    def test_pending_checkpoints_hashes_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(r,'REPO',Path(directory)):
            path=Path(directory)/'private/phase5/move-vm-state.json';path.parent.mkdir(parents=True)
            raw=b'{"attempted":true,"pending":true}';path.write_bytes(raw);path.chmod(0o600)
            self.assertEqual(r.snapshot(),r.snapshot());self.assertEqual(path.read_bytes(),raw)
            path.write_text('{"pending":false}');self.assertNotEqual(r.snapshot()[str(path.relative_to(directory))],hashlib.sha256(raw).hexdigest())

    def test_runtime_needs_all_three_nodes_and_exactly_one_running(self):
        class Nodes:
            def call(self,node,action,payload):
                self_test.assertEqual(action,'running');self_test.assertEqual(payload,{'server_id':SERVER})
                return {'node':node,'observed':True,'VM_running':node=='node1'}
        self_test=self
        result=r.runtime(Nodes(),SERVER);self.assertTrue(result['observed']);self.assertEqual(result['running_hosts'],['node1'])
        class Partial:
            def call(self,node,*args):return {'node':node,'observed':node!='node3','VM_running':node=='node1'}
        self.assertFalse(r.runtime(Partial(),SERVER)['observed'])

    def test_guest_reader_keeps_partial_failure_observations_and_has_no_mutation(self):
        value={'passed':False,'checks':{'metadata_matches_Nova_instance':True,'guest_DNS_lookup':False}}
        def run(args,**kwargs):
            self.assertTrue(args[-1].startswith('sudo -n /usr/bin/python3 - '))
            self.assertIn('def collect(expected)',kwargs['input'])
            self.assertNotIn('mount -',args[-1])
            return subprocess.CompletedProcess(args,1,json.dumps(value),'')
        with patch.object(r.subprocess,'run',side_effect=run):self.assertEqual(r.guest_read(['ssh','guest'],'phase4-guest-check.py',{},360),value)
        with self.assertRaises(RuntimeError):r.guest_read(['ssh'],'unapproved.py',{},30)

    def test_ssh_failure_does_not_expose_stderr(self):
        with patch.object(r.subprocess,'run',return_value=subprocess.CompletedProcess([],255,'','password=private')):
            with self.assertRaises(RuntimeError) as error:r.guest_read(['ssh'],'lab-reboot-guest.py',EXPECTED,90)
        self.assertNotIn('private',str(error.exception))

    def test_nova_error_does_not_skip_actual_guest_observations(self):
        vm={'ids':{'server':SERVER},'networks':{}};data={**EXPECTED,'backend':'node3@lvm-1#lvm-1'}
        current={'server_status':'ERROR','task':None,'host':'node1','volume_attached':True,'backend':data['backend'],'floating_ip':'192.168.0.194'}
        network={'passed':True,'checks':{'metadata_matches_Nova_instance':True,'guest_DNS_lookup':True}}
        guest={'observed':True,'disk_identified':True,'filesystem_matches':True,'mounted':True,'checksum_matches':True}
        def read(ssh,file,*args):return network if file=='phase4-guest-check.py' else guest
        with patch.object(r,'bindings',return_value=(vm,data,{})),patch.object(r,'api_workload',return_value=current),patch.object(r,'runtime',return_value={'observed':True,'running_hosts':['node1']}),patch.object(r,'pending_migration',return_value={'present':True,'pending':True}),patch.object(r,'trusted_guest',return_value=['ssh']),patch.object(r,'network_context',return_value={}),patch.object(r,'guest_read',side_effect=read):
            rows=[];details={};r.workload(None,None,'project','user',rows,details,lambda _:None)
        status={row['check']:row['status'] for row in rows}
        self.assertEqual(status['VM in Nova'],'REVIEW');self.assertEqual(status['VM actually running'],'PASS')
        self.assertEqual(status['Saved file checksum'],'PASS');self.assertEqual(status['Migration record (preserved)'],'REVIEW')

    def test_no_running_vm_skips_ssh_without_starting_vm(self):
        vm={'ids':{'server':SERVER}};data={**EXPECTED,'backend':'node3'}
        current={'server_status':'SHUTOFF','task':None,'volume_attached':True,'backend':'node3'}
        with patch.object(r,'bindings',return_value=(vm,data,{})),patch.object(r,'api_workload',return_value=current),patch.object(r,'runtime',return_value={'observed':True,'running_hosts':[]}),patch.object(r,'pending_migration',return_value={'present':False}),patch.object(r,'trusted_guest',side_effect=AssertionError('No SSH to an unverified VM')):
            rows=[];details={};r.workload(None,None,'p','u',rows,details,lambda _:None)
        self.assertEqual(next(v['status'] for v in rows if v['check']=='VM actually running'),'REVIEW')
        self.assertIn('skipped',details['Guest checks']['issue'])

    def test_changed_guest_trust_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(r,'REPO',Path(directory)):
            path=Path(directory)/'private/phase4/lab-vm-01-known-hosts';path.parent.mkdir(parents=True)
            raw=b'192.168.0.195 ssh-ed25519 test-key\n';path.write_bytes(raw);path.chmod(0o600)
            with self.assertRaises(RuntimeError):r.trusted_guest({'host_fingerprint':'old'}, {},'192.168.0.194',lambda _:None)
            self.assertEqual(path.read_bytes(),raw)


class GuestDataTests(unittest.TestCase):
    def collect(self,mounted=True,serial=None,filesystem=FS,content=CONTENT):
        self.commands=[]
        disk={'name':'/dev/vdb','type':'disk','size':1024**3,'serial':serial or VOLUME[:20],'maj:min':'253:16'}
        class FakePath:
            def __init__(self,path):self.path=str(path)
            def read_text(self):return BOOT
            def is_symlink(self):return False
            def __truediv__(self,name):return FakePath(self.path+'/'+name)
            def lstat(self):
                if self.path.startswith('/dev/'):
                    return SimpleNamespace(st_mode=stat.S_IFBLK|0o660,st_rdev=os.makedev(253,16))
                return SimpleNamespace(st_mode=stat.S_IFREG|0o600,st_uid=0,st_nlink=1,st_size=len(content))
        def run(args):
            self.commands.append(args)
            if args[0]=='lsblk':return json.dumps({'blockdevices':[disk]})
            if args[0]=='blkid':return 'TYPE=ext4\nUUID='+filesystem+'\n'
            if args[0]=='findmnt':return json.dumps({'filesystems':[{'target':'/mnt/lab-data','fstype':'ext4','maj:min':'253:16'}]})
            raise AssertionError('Unexpected/mutating guest command: '+str(args))
        with patch.object(g.os,'geteuid',return_value=0),patch.object(g,'Path',FakePath),patch.object(g,'run',side_effect=run),patch.object(g.os.path,'ismount',return_value=mounted),patch.object(g.os,'open',return_value=999) as opened,patch.object(g.os,'read',return_value=content),patch.object(g.os,'close'):
            result=g.collect(EXPECTED)
            if opened.called:self.assertEqual(opened.call_args.args[1],os.O_RDONLY|os.O_NOFOLLOW)
        return result

    def test_mounted_volume_and_saved_data_match(self):
        result=self.collect();self.assertTrue(result['checksum_matches']);self.assertEqual(result['sha256'],EXPECTED['sha256'])
        self.assertTrue(result['disk_identified']);self.assertTrue(result['filesystem_matches'])
        self.assertTrue(all(c[0] in ('lsblk','blkid','findmnt') for c in self.commands))

    def test_unmounted_volume_is_not_mounted_or_declared_corrupt(self):
        result=self.collect(mounted=False)
        self.assertTrue(result['filesystem_matches']);self.assertFalse(result['mounted'])
        self.assertIsNone(result['checksum_matches']);self.assertIn('not mounted',result['issue'])
        self.assertFalse(any(c[0] in ('mount','mkfs.ext4','umount') for c in self.commands))

    def test_wrong_disk_and_wrong_filesystem_stop_before_data_read(self):
        result=self.collect(serial='another-disk');self.assertFalse(result['disk_identified'])
        self.assertEqual(len(self.commands),1)
        result=self.collect(filesystem=BOOT);self.assertFalse(result['filesystem_matches']);self.assertFalse(result['mounted'])

    def test_changed_data_fails_without_rewrite(self):
        result=self.collect(content=b'changed');self.assertFalse(result['checksum_matches']);self.assertIn('differs',result['issue'])

    def test_documentation_links_and_single_final_command(self):
        doc=(ROOT/'docs/reboot-and-recovery.md').read_text()
        self.assertIn('scripts/lab-reboot-verify.py --check',doc)
        self.assertTrue((ROOT/'docs/reboot-recovery-reference.md').is_file())
        self.assertIn('Neutron',doc);self.assertIn('compute-services --table',doc)


if __name__=='__main__':unittest.main()

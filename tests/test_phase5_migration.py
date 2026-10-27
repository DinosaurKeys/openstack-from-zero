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
step=load('migration_test','phase5-migration.py');node=load('migration_node_test','phase5-migration-node.py')
SERVER='0ef50722-c056-49fa-8f9e-05e926a8b41d';RP='12345678-abcd-4321-8321-0123456789ab'
XML='''<domain type="kvm"><uuid>SERVER</uuid><memory unit="KiB">2097152</memory><vcpu>1</vcpu><os><type arch="x86_64" machine="pc-q35-9.0">hvm</type></os><cpu mode="custom" match="exact"><model>Skylake-Client-IBRS</model><feature policy="require" name="vmx"/></cpu><devices><emulator>/usr/bin/qemu-system-x86_64</emulator><disk type="file" device="disk"><driver type="qcow2"/><source file="/private/root/disk"/><target dev="vda"/></disk><disk type="block" device="disk"><source dev="/dev/private"/><target dev="vdb"/><auth username="PRIVATE"><secret uuid="SECRET"/></auth></disk><console><source path="PRIVATE_CONSOLE"/></console></devices></domain>'''.replace('SERVER',SERVER)
DOMAIN=node.domain_summary(XML,SERVER)

def host(n):
    return {'node':n,'observed':True,'domain':DOMAIN if n=='node1' else None,
      'memory':{'MemAvailable_bytes':6*1024**3},'instances_storage':{'available_bytes':40*1024**3},
      'nova_config':{'connection_uri':'qemu+tcp://'+node.NODES[n]+'/system'}}

class SummaryTests(unittest.TestCase):
    def test_real_guest_cpu_and_disks_extracted_without_auth_or_console_xml(self):
        value=node.domain_summary(XML,SERVER)
        self.assertEqual(value['disks'][0]['target'],'vda');self.assertEqual(value['disks'][1]['target'],'vdb')
        self.assertEqual(value['memory']['value'],'2097152')
        for secret in ('SECRET','PRIVATE','/private/root/disk'):
            self.assertNotIn(secret,json.dumps(value))
    def test_wrong_domain_uuid_dtd_and_missing_cpu_rejected(self):
        for raw in (XML.replace(SERVER,RP), '<!DOCTYPE domain>'+XML, XML.replace('<cpu mode="custom" match="exact">','<other>').replace('</cpu>','</other>')):
            with self.assertRaises(RuntimeError):node.domain_summary(raw,SERVER)
    def test_uri_auth_query_nonlab_peer_and_wrong_scheme_rejected_without_echo(self):
        self.assertEqual(node.libvirt_uri('"qemu+tcp://192.168.0.201/system"','node2'),'qemu+tcp://192.168.0.201/system')
        for uri in ('qemu+tcp://nova:SECRET@192.168.0.201/system','qemu+tcp://192.168.0.201/system?authfile=/private','qemu+ssh://node2/system','qemu+tcp://192.168.0.200/system','qemu+tcp://node2:1234/system'):
            with self.assertRaises(RuntimeError) as error:node.libvirt_uri(uri,'node2')
            self.assertNotIn('SECRET',str(error.exception))
    def test_capacity_summary_is_snapshot_not_approval(self):
        good={'observed':True,'source_cpu_supported_by_destination_hypervisor':True};transport={'observed':True,'authenticated_libvirt_read':True,'transport':'qemu+tcp'}
        value=step.candidate_summary(host('node2'),good,transport,{'ram_mb':2048,'disk_gb':10})
        self.assertTrue(value['ram_covers_guest_plus_1GiB_review_margin']);self.assertTrue(value['source_guest_cpu_compatible'])
        small=host('node2');small['memory']['MemAvailable_bytes']=2*1024**3;small['instances_storage']['available_bytes']=10*1024**3
        value=step.candidate_summary(small,good,transport,{'ram_mb':2048,'disk_gb':10})
        self.assertFalse(value['ram_covers_guest_plus_1GiB_review_margin']);self.assertFalse(value['disk_covers_root_plus_2GiB_review_margin'])
        self.assertIn('not reservations',value['limits'])
    def test_failed_or_missing_cpu_comparison_is_never_called_compatible(self):
        for comparison in ({'observed':False},{'observed':True,'source_cpu_supported_by_destination_hypervisor':False}):
            value=step.candidate_summary(host('node2'),comparison,{'observed':False},{'ram_mb':2048,'disk_gb':10})
            self.assertFalse(value['source_guest_cpu_compatible']);self.assertFalse(value['inspection_complete'])
    def test_default_and_apply_do_not_run_or_migrate(self):
        with patch.object(step,'execute',side_effect=AssertionError),contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(step.main([]),0)
        self.assertIn('PLAN ONLY',output.getvalue())
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):step.main(['--apply'])

class NodeTests(unittest.TestCase):
    def test_compare_uses_required_normal_connection_and_cpu_only_stdin(self):
        with patch.object(node,'run',return_value=SimpleNamespace(returncode=0)) as run:
            value=node.compare('node2',{'domain':DOMAIN})
        args=run.call_args.args[0];kw=run.call_args.kwargs
        self.assertNotIn('--readonly',args);self.assertIn('hypervisor-cpu-compare',args)
        self.assertIn('/dev/stdin',args);self.assertIn('--error',args)
        self.assertEqual(kw['input'],DOMAIN['cpu_xml']);self.assertNotIn('SECRET',kw['input'])
        self.assertTrue(value['source_cpu_supported_by_destination_hypervisor'])
    def test_failed_comparison_is_a_negative_observation_not_migration(self):
        with patch.object(node,'run',return_value=SimpleNamespace(returncode=1,stderr='CPU incompatible')):
            self.assertFalse(node.compare('node2',{'domain':DOMAIN})['source_cpu_supported_by_destination_hypervisor'])
    def test_invalid_cpu_emulator_or_machine_prevents_command(self):
        for wrong in (dict(DOMAIN,cpu_xml='<!DOCTYPE cpu><cpu/>'),dict(DOMAIN,emulator='/tmp/unknown'),dict(DOMAIN,machine='--change'),dict(DOMAIN,virt_type='qemu')):
            with patch.object(node,'run') as run,self.assertRaises(RuntimeError):node.compare('node2',{'domain':wrong})
            run.assert_not_called()
    def test_remote_authentication_read_checks_actual_peer_identity(self):
        for result,expected in [(SimpleNamespace(returncode=0,stdout='node2\n'),True),(SimpleNamespace(returncode=0,stdout='node3\n'),False),(SimpleNamespace(returncode=1,stdout='',stderr='authentication failed'),False)]:
            with patch.object(node,'run',return_value=result) as run:
                value=node.connection('node1',{'peer':'node2','uri':'qemu+tcp://192.168.0.201/system'})
            self.assertEqual(value['authenticated_libvirt_read'],expected)
            self.assertFalse(value['encrypted_transport']);self.assertIn('--readonly',run.call_args.args[0]);self.assertEqual(run.call_args.args[0][-1],'hostname')
    def test_same_peer_and_nonlab_identity_refused(self):
        with self.assertRaises(RuntimeError):node.connection('node1',{'peer':'node1'})
        with patch.object(node.os,'geteuid',return_value=1000),self.assertRaises(RuntimeError):node.identity('node2')
    def test_config_whitelist_never_exposes_passwords(self):
        raw=b'''[DEFAULT]\ntransport_url=rabbit://user:SECRET@host\n[libvirt]\nvirt_type=kvm\nconnection_uri=qemu+tcp://192.168.0.200/system\n[database]\nconnection=mysql://SECRET\n'''
        with patch.object(node.Path,'read_bytes',return_value=raw):parser,value=node.configuration()
        self.assertNotIn('SECRET',json.dumps(value));self.assertIsNone(value['DEFAULT']['instances_path'])
        self.assertEqual(len(value['config_sha256']),64)
    def test_actual_pinned_kolla_template_documents_sasl_auth_paths_and_cpu_bypass(self):
        folder=ROOT/'build/real-tools-check/share/kolla-ansible/ansible/roles/nova-cell/templates'
        self.assertIn('/root/.config/libvirt/auth.conf',(folder/'nova-libvirt.json.j2').read_text())
        self.assertIn('skip_cpu_compare_on_dest = true',(folder/'nova.conf.d/libvirt.conf.j2').read_text())
        self.assertIn('live_migration_inbound_addr',(folder/'nova.conf.d/libvirt.conf.j2').read_text())
    def test_only_inspection_actions_are_supported(self):
        with patch.object(node,'identity',return_value={}),self.assertRaises(RuntimeError):node.execute('node1','migrate',{})

class HostCollectionTests(unittest.TestCase):
    def test_observe_maps_nova_mount_and_reads_live_domain_without_changes(self):
        parser=node.configparser.ConfigParser(interpolation=None)
        parser.read_dict({'libvirt':{'connection_uri':'"qemu+tcp://192.168.0.200/system"'}})
        mounts={'nova_compute':{'id':'a'*64,'image':node.IMAGES['nova_compute'],'running':True,'mounts':[{'Destination':'/var/lib/nova','Source':'/var/lib/docker/volumes/nova_compute/_data'}]},
                'nova_libvirt':{'id':'b'*64,'image':node.IMAGES['nova_libvirt'],'running':True}}
        calls=[]
        def virsh(*args,**kwargs):
            calls.append(args)
            if args[0]=='capabilities':return SimpleNamespace(stdout='<capabilities><host><cpu><arch>x86_64</arch><model>Skylake-Client-IBRS</model></cpu></host></capabilities>')
            if args[0]=='dumpxml':return SimpleNamespace(stdout=XML)
            return SimpleNamespace(stdout='libvirt 10.0.0')
        def text(path):
            return 'MemTotal: 16000000 kB\nMemAvailable: 6000000 kB\nSwapTotal: 4000000 kB\nSwapFree: 3999000 kB' if str(path)=='/proc/meminfo' else 'model name : Intel Test CPU\nvendor_id : GenuineIntel\nflags : vmx sse4_2'
        with patch.object(node,'configuration',return_value=(parser,{})),patch.object(node,'virsh',side_effect=virsh),patch.object(node.Path,'read_text',text),patch.object(node.os,'statvfs',return_value=SimpleNamespace(f_bavail=100,f_blocks=200,f_frsize=4096)) as stat,patch.object(node.socket,'getaddrinfo',side_effect=lambda n,*a:[(None,None,None,None,(node.NODES[n],0))]):
            value=node.observe('node1',SERVER,'node1',mounts)
        self.assertEqual(str(stat.call_args.args[0]),'/var/lib/docker/volumes/nova_compute/_data/instances')
        self.assertEqual(value['memory']['MemAvailable_bytes'],6000000*1024)
        self.assertFalse(value['instances_storage']['path_explicit_in_nova_config'])
        self.assertEqual(value['domain']['uuid'],SERVER)
        self.assertEqual([c[0] for c in calls],['capabilities','dumpxml','version'])
        self.assertNotIn('SECRET',json.dumps(value))


class TransportTests(unittest.TestCase):
    def test_password_is_stdin_only_and_payload_parses(self):
        nodes=step.Nodes({}, {'node2':'PRIVATE_PASSWORD'})
        with patch.object(step.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='{"node":"node2","observed":true}')) as run:
            self.assertTrue(nodes.call('node2','observe',{'server_id':SERVER,'source':'node1'})['observed'])
        args=run.call_args.args[0];kw=run.call_args.kwargs
        self.assertNotIn('PRIVATE_PASSWORD',str(args));self.assertEqual(kw['input'],'PRIVATE_PASSWORD\n')
        self.assertIn('StrictHostKeyChecking=yes',args);compile(shlex.split(args[-1])[6],'payload','exec')
    def test_lost_response_does_not_echo_stderr(self):
        with patch.object(step.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout='',stderr='SECRET')):
            value=step.Nodes({}, {'node1':'x'}).call('node1','observe',{})
        self.assertFalse(value['observed']);self.assertNotIn('SECRET',json.dumps(value))
    def test_transport_cannot_send_migration_action(self):
        with patch.object(step.subprocess,'run') as run,self.assertRaises(RuntimeError):step.Nodes({},{}).call('node1','migrate',{})
        run.assert_not_called()

class WorkflowTests(unittest.TestCase):
    def api(self):
        class API:
            def list(self,service,path,key):
                if service=='placement':return [{'name':n+'.lab','uuid':RP} for n in node.NODES]
                if key=='services':return [{'host':n,'binary':'nova-compute','state':'up','status':'enabled'} for n in node.NODES]
                if key=='hypervisors':return []
                raise AssertionError(path)
            def get(self,service,path):return {'inventories':{},'usages':{}}
            def write(self,*args):raise AssertionError('No mutations allowed')
        return API()
    def nodes(self):
        class Nodes:
            def __init__(self):self.calls=[]
            def all(self,payload):return {n:host(n) for n in node.NODES}
            def call(self,n,action,payload):
                self.calls.append((n,action,payload))
                if action=='compare':return {'node':n,'observed':True,'source_cpu_supported_by_destination_hypervisor':True}
                if action=='connection':return {'node':n,'observed':True,'authenticated_libvirt_read':True,'transport':'qemu+tcp'}
                raise AssertionError(action)
        return Nodes()
    def test_both_candidates_inspected_without_selecting_destination(self):
        nodes=self.nodes();logs=[]
        records,summaries=step.inspect(nodes,self.api(),{'ids':{'server':SERVER}},'node1',logs.append)
        self.assertEqual(set(summaries),{'node2','node3'})
        self.assertEqual([v[1] for v in nodes.calls],['compare','connection','compare','connection'])
        self.assertTrue(all(v['placement_capacity_collected'] for v in summaries.values()))
        self.assertIn('Not selected',logs[-1])
    def test_missing_source_domain_prevents_compatibility_actions(self):
        nodes=self.nodes();bad={n:host(n) for n in node.NODES};bad['node1']['domain']=None
        with patch.object(nodes,'all',return_value=bad):
            records,summaries=step.inspect(nodes,self.api(),{'ids':{'server':SERVER}},'node1',lambda s:None)
        self.assertFalse(any(v[1]=='compare' for v in nodes.calls))
        self.assertFalse(summaries['node2']['inspection_complete'])
    def test_missing_placement_inventory_marks_incomplete(self):
        api=self.api();api.list=lambda *args:[]
        records,summaries=step.inspect(self.nodes(),api,{'ids':{'server':SERVER}},'node1',lambda s:None)
        self.assertFalse(summaries['node2']['inspection_complete'])
        self.assertFalse(summaries['node2']['placement_capacity_collected'])

class RegressionTests(unittest.TestCase):
    # The actual libvirt v10.0.0 API contracts are in src/libvirt-domain.c
    # virDomainGetXMLDesc and src/libvirt-host.c virConnectCompareHypervisorCPU.
    # Reproduce those rejection rules rather than treating every virsh call as
    # successful, which allowed the original incompatible flags through tests.
    def test_libvirt10_connection_rules_and_ordinary_xml(self):
        calls=[]
        def libvirt10(args,**kwargs):
            calls.append(args)
            rejected='--readonly' in args and ('--migratable' in args or 'hypervisor-cpu-compare' in args)
            return SimpleNamespace(returncode=1 if rejected else 0,stdout='' if rejected else XML,stderr='operation forbidden for read only access' if rejected else '')
        with patch.object(node.subprocess,'run',side_effect=libvirt10):
            value=node.virsh('dumpxml',SERVER)
            self.assertEqual(value.stdout,XML)
            self.assertTrue(node.compare('node2',{'domain':DOMAIN})['source_cpu_supported_by_destination_hypervisor'])
        self.assertIn('--readonly',calls[0]);self.assertNotIn('--migratable',calls[0])
        self.assertNotIn('--readonly',calls[1]);self.assertIn('hypervisor-cpu-compare',calls[1])
    def test_no_vm_mutation_or_sensitive_xml_flag_is_accepted(self):
        with patch.object(node,'run') as run:
            for args in [('migrate',SERVER),('destroy',SERVER),('start',SERVER),('dumpxml',SERVER,'--security-info'),('dumpxml',SERVER,'--migratable')]:
                with self.assertRaises(RuntimeError):node.virsh(*args)
        run.assert_not_called()
    def test_command_failure_identifies_query_and_classifies_without_raw_secret(self):
        value=SimpleNamespace(returncode=1,stdout='',stderr='operation forbidden for read only access PASSWORD=PRIVATE_SECRET')
        with patch.object(node.subprocess,'run',return_value=value),self.assertRaises(node.CommandFailure) as error:
            node.virsh('dumpxml',SERVER)
        self.assertIn('dumpxml',str(error.exception));self.assertIn(SERVER,str(error.exception))
        self.assertEqual(error.exception.details['exit_status'],1)
        self.assertNotIn('PRIVATE_SECRET',json.dumps(node.failure(error.exception,'source VM XML inspection')))
    def test_query_timeout_is_bounded_and_identifies_operation(self):
        import subprocess
        with patch.object(node.subprocess,'run',side_effect=subprocess.TimeoutExpired(['docker'],35)),self.assertRaisesRegex(node.CommandFailure,'timed out'):
            node.virsh('capabilities')
    def test_source_xml_failure_preserves_ram_disk_config_and_version(self):
        parser=node.configparser.ConfigParser(interpolation=None);parser.read_dict({'libvirt':{'connection_uri':'qemu+tcp://192.168.0.200/system'}})
        containers={'nova_compute':{'id':'a'*64,'image':node.IMAGES['nova_compute'],'running':True,'mounts':[{'Destination':'/var/lib/nova','Source':'/actual/nova'}]},
                    'nova_libvirt':{'id':'b'*64,'image':node.IMAGES['nova_libvirt'],'running':True}}
        def virsh(*args,**kwargs):
            if args[0]=='dumpxml':raise node.CommandFailure(['docker','exec','nova_libvirt','virsh','dumpxml',SERVER],1,'Libvirt could not find the requested VM domain')
            return SimpleNamespace(stdout='<capabilities><host><cpu><model>test</model></cpu></host></capabilities>' if args[0]=='capabilities' else 'libvirt 10.0.0')
        def text(path):return 'MemAvailable: 6000000 kB' if str(path)=='/proc/meminfo' else 'model name : Intel'
        with patch.object(node,'configuration',return_value=(parser,{'config_sha256':'a'*64})),patch.object(node,'virsh',side_effect=virsh),patch.object(node.Path,'read_text',text),patch.object(node.os,'statvfs',return_value=SimpleNamespace(f_bavail=100,f_blocks=200,f_frsize=4096)),patch.object(node.socket,'getaddrinfo',return_value=[]):
            value=node.observe('node1',SERVER,'node1',containers)
        self.assertFalse(value['observed']);self.assertIsNone(value['domain'])
        self.assertEqual(value['memory']['MemAvailable_bytes'],6000000*1024)
        self.assertEqual(value['instances_storage']['available_bytes'],100*4096)
        self.assertEqual(value['libvirt_version'],'libvirt 10.0.0')
        self.assertEqual(value['errors'][0]['stage'],'source VM XML inspection')
        self.assertIn('dumpxml',value['errors'][0]['command'])
    def test_short_output_omits_raw_cpu_xml_and_flags_details_retained(self):
        logs=[]
        def log(line):logs.append(line)
        log.details={}
        workflow=WorkflowTests();nodes=workflow.nodes()
        records,summaries=step.inspect(nodes,workflow.api(),{'ids':{'server':SERVER}},'node1',log)
        output='\n'.join(logs)
        self.assertLess(len(logs),15);self.assertNotIn('<cpu',output);self.assertNotIn('cpu_xml',output)
        self.assertIn('node2 | PASS | PASS',output)
        self.assertEqual(log.details['compute_hosts']['node1']['domain']['cpu_xml'],DOMAIN['cpu_xml'])
    def test_source_failure_still_summarizes_candidates_without_compatibility_attempts(self):
        workflow=WorkflowTests();nodes=workflow.nodes();values={n:host(n) for n in node.NODES}
        values['node1'].update(observed=False,domain=None,error='source VM query failed')
        logs=[]
        with patch.object(nodes,'all',return_value=values):records,summaries=step.inspect(nodes,workflow.api(),{'ids':{'server':SERVER}},'node1',logs.append)
        self.assertFalse(nodes.calls)
        self.assertIn('node2 | NOT CHECKED | NOT CHECKED','\n'.join(logs))
        self.assertIn('source VM CPU/XML inspection is incomplete',logs[-1])
        self.assertEqual(summaries['node2']['physical_available_ram_gib'],6.0)


if __name__=='__main__':unittest.main()

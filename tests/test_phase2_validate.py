"""Chapter F privacy, preservation, packet logic and real certificate checks."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch, MagicMock
ROOT = Path(__file__).resolve().parents[1]
def load(name, file):
 s=importlib.util.spec_from_file_location(name,ROOT/'scripts'/file)
 m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
step=load('validate','phase2-validate.py')
node=load('readiness','phase2-node-readiness.py')

class ValidationTests(unittest.TestCase):
 def test_plan_has_no_checks_connections_or_trust_changes(self):
  with patch.object(step,'execute') as run,contextlib.redirect_stdout(io.StringIO()):
   self.assertEqual(step.main([]),0);run.assert_not_called()
 def test_trust_flag_requires_run(self):
  with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):step.main(['--trust-r2d2'])
 def test_inventory_tree_cannot_claim_wrong_node_or_unreachable(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'node1'
   for value in ({'unreachable':True},{'stdout':json.dumps({'node':'node2','ready':True})}):
    p.write_text(json.dumps(value))
    with self.assertRaises(RuntimeError):step.node_result(Path(d),'node1')
   p.write_text(json.dumps({'rc':0,'stdout':json.dumps({'node':'node1','ready':False})}))
   self.assertFalse(step.node_result(Path(d),'node1')['ready'])
 def test_trust_refuses_existing_different_file_and_symlink(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);source=root/'certificates/ca/lab-root.crt';source.parent.mkdir(parents=True);source.write_text('public')
   dest=root/'trust.crt';dest.write_text('keep')
   with patch.object(step,'TRUST_TARGET',dest),patch.object(step.tools,'Runner') as runner:
    with self.assertRaises(RuntimeError):step.trust_ca(root,lambda x:None,{})
    self.assertEqual(dest.read_text(),'keep');runner.assert_not_called()
    dest.unlink();dest.symlink_to(source)
    with self.assertRaises(RuntimeError):step.trust_ca(root,lambda x:None,{})
 def test_trust_imports_only_public_ca_then_verifies_ip(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);source=root/'certificates/ca/lab-root.crt';source.parent.mkdir(parents=True);source.write_text('public')
   with patch.object(step,'TRUST_TARGET',root/'trust.crt'),patch.object(step.tools,'Runner') as runner:
    step.trust_ca(root,lambda x:None,{})
    calls=[c.args[0] for c in runner.return_value.run.call_args_list]
    self.assertEqual(calls[0],['sudo','install','-o','root','-g','root','-m','0644',source,root/'trust.crt'])
    self.assertEqual(calls[1],['sudo','/usr/sbin/update-ca-certificates'])
    self.assertIn('-verify_ip',calls[2]);self.assertNotIn('--fresh',str(calls))
 def test_real_local_configuration_checks_preserve_all_files_and_reject_wrong_ca(self):
  import yaml
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);config=root/'private/phase2/kolla';config.mkdir(parents=True)
   env=root/'venv';template=env/'share/kolla-ansible/etc_examples/kolla/passwords.yml';template.parent.mkdir(parents=True);template.write_text('keystone_admin_password: null\ndocker_registry_password: null\n')
   files={config/step.config_step.MARKER:json.dumps(step.config_step.MARK),config/'globals.yml':yaml.safe_dump(step.config_step.settings(config)),config/'lab-network-plan.json':json.dumps(step.config_step.network_plan()),config/'passwords.yml':'keystone_admin_password: PRIVATE_SENTINEL\ndocker_registry_password: null\n',config.parent/'inventory.ini':'fixture'}
   for n,nic in step.config_step.USB.items():files[config.parent/'host_vars'/f'{n}.yml']=yaml.safe_dump({'neutron_external_interface':nic})
   for p,v in files.items():p.parent.mkdir(parents=True,exist_ok=True);p.write_text(v);p.chmod(0o600)
   (config/'certificates').mkdir();step.config_step.make_certificates(config/'certificates');fingerprint=step.config_step.verify_certificates(config/'certificates')
   inventory_template=env/'share/kolla-ansible/ansible/inventory/multinode';inventory_template.parent.mkdir(parents=True);inventory_template.write_text('fixture')
   parsed={'_meta':{'hostvars':{n:{'neutron_external_interface':nic} for n,nic in step.config_step.USB.items()}}}
   before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()};lines=[]
   with patch.object(step,'REPO',root),patch.object(step,'ENV',env),patch.object(step.inv,'render_inventory',return_value='fixture'),patch.object(step.inv,'inventory_data',return_value=parsed),patch.object(step.inv,'validate_inventory'),patch.object(step.tools,'Runner') as runner,patch.object(step.importlib.metadata,'version',side_effect=lambda n:'23.0.0' if n=='kolla-ansible' else '2.21.5'):
    step.local_checks(lines.append,{},fingerprint)
    with self.assertRaises(RuntimeError):step.local_checks(lines.append,{},'0'*64)
    self.assertEqual(runner.return_value.run.call_count,2)
   self.assertNotIn('PRIVATE_SENTINEL','\n'.join(lines));self.assertEqual(before,{p:p.read_bytes() for p in before})
   result=subprocess.run(['openssl','verify','-CAfile',str(config/'certificates/ca/lab-root.crt'),'-verify_ip',node.VIP,str(config/'certificates/server.crt')],capture_output=True)
   self.assertEqual(result.returncode,0)
 def test_node_readiness_full_fixture_is_read_only_and_detects_unexpected_lvs(self):
  values={'vgs':{'report':[{'vg':[{'vg_name':'cinder-volumes','pv_count':'1','lv_count':'0'}]}]},'pvs':{'report':[{'pv':[{'pv_name':'/dev/sda','vg_name':'cinder-volumes'}]}]},'lsblk':{'blockdevices':[{'serial':'50026B7766070422','size':240057409536}]}}
  def cmd(args):
   if args[0]=='ip' and 'address' in args:return json.dumps([{'ifname':node.MGMT,'addr_info':[{'local':'192.168.0.200','prefixlen':24}]},{'ifname':'enx00e04c557e58','address':'00:e0:4c:55:7e:58','flags':['UP','LOWER_UP'],'addr_info':[]}])
   if args[0]=='ip':return json.dumps([{'dev':node.MGMT,'gateway':'192.168.0.1'}])
   if args[0]=='systemctl':return 'active'
   if args[0]=='timedatectl':return 'yes'
   return json.dumps(values[args[0]])
  def read(p,*a,**k):
   return {'/etc/os-release':'ID=ubuntu\nVERSION_ID="24.04"','/proc/cpuinfo':'flags : vmx sse','/proc/meminfo':'MemTotal: 1000 kB'}[str(p)]
  with patch.object(node,'command',side_effect=cmd) as commands,patch.object(node.os,'geteuid',return_value=0),patch.dict(os.environ,{'SUDO_USER':'joe'}),patch.object(node.socket,'gethostname',return_value='node1'),patch.object(Path,'read_text',read),patch.object(Path,'exists',return_value=True),patch.object(Path,'resolve',return_value=Path('/dev/sda')),patch.object(node,'vip_conflicts',return_value=[]):
   self.assertTrue(node.collect('node1')['ready'])
   values['vgs']['report'][0]['vg'][0]['lv_count']='1'
   self.assertFalse(node.collect('node1')['ready'])
   self.assertEqual({c.args[0][0] for c in commands.call_args_list},{'ip','systemctl','timedatectl','vgs','pvs','lsblk'})
 def test_wrong_node_stops_before_lvm_or_network_probe(self):
  with patch.object(node.os,'geteuid',return_value=0),patch.dict(os.environ,{'SUDO_USER':'joe'}),patch.object(node.socket,'gethostname',return_value='wrong'),patch.object(Path,'read_text',return_value='ID=ubuntu\nVERSION_ID="24.04"'),patch.object(node,'command',return_value='[]') as command,patch.object(node,'vip_conflicts') as arp:
   self.assertFalse(node.collect('node1')['ready']);self.assertEqual(command.call_count,1);arp.assert_not_called()
 def test_arp_probe_does_not_claim_vip_and_detects_owner_and_competing_probe(self):
  own=bytes.fromhex('001122334455');other=bytes.fromhex('aabbccddeeff')
  packet=node.arp_packet(own,node.VIP)
  self.assertEqual(packet[28:32],b'\0'*4);self.assertEqual(len(packet),60)
  self.assertIsNone(node.conflict_mac(packet,own,node.VIP))
  competing=node.arp_packet(other,node.VIP)
  self.assertEqual(node.conflict_mac(competing,own,node.VIP),'aa:bb:cc:dd:ee:ff')
  owner=bytearray(competing);owner[20:22]=b'\x00\x02';owner[28:32]=socket.inet_aton(node.VIP)
  self.assertEqual(node.conflict_mac(owner,own,node.VIP),'aa:bb:cc:dd:ee:ff')
  owner[28:32]=socket.inet_aton('192.168.0.1')
  self.assertIsNone(node.conflict_mac(owner,own,node.VIP));self.assertIsNone(node.conflict_mac(b'invalid',own,node.VIP))
 def test_private_file_symlink_and_excess_permissions_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'private';p.write_text('x');p.chmod(0o644)
   with self.assertRaises(RuntimeError):step.private_file(p)
   p.chmod(0o600);step.private_file(p)
   link=Path(d)/'link';link.symlink_to(p)
   with self.assertRaises(RuntimeError):step.private_file(link)

if __name__=='__main__':unittest.main()

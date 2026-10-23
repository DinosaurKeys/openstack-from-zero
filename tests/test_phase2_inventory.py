"""Inventory generation preserves unknown files and enforces the agreed roles."""
import importlib.util,json,tempfile,unittest,subprocess
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('inventory_step',ROOT/'scripts/phase2-inventory.py')
step=importlib.util.module_from_spec(spec);spec.loader.exec_module(step)
TEMPLATE=''.join('['+g+']\nplaceholder\n\n' for g in ('control','network','compute','monitoring','storage','deployment'))+'[baremetal:children]\ncontrol\nnetwork\ncompute\nstorage\nmonitoring\n[custom:children]\ncontrol\n'

class InventoryTests(unittest.TestCase):
 def test_template_child_groups_preserved_and_roles_substituted(self):
  result=step.render_inventory(TEMPLATE)
  self.assertIn('[custom:children]\ncontrol',result)
  self.assertIn('[compute]\nnode2\nnode3\n',result)
  self.assertIn('[network]\nnode1\nnode2\nnode3\n',result)
  self.assertNotIn('placeholder',result)
  self.assertIn('StrictHostKeyChecking=yes',result)
  with self.assertRaises(RuntimeError): step.render_inventory('[control]\n')
 def test_unknown_files_and_symlinks_remain_untouched(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'inventory.ini';p.write_text('existing')
   with self.assertRaises(RuntimeError): step.preserve_write(p,'new')
   self.assertEqual(p.read_text(),'existing')
   link=Path(d)/'link';link.symlink_to(p)
   with self.assertRaises(RuntimeError):step.preserve_write(link,'existing')
 def test_private_inventory_is_reusable_and_mode_600(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'inventory.ini'
   step.preserve_write(p,'agreed');step.preserve_write(p,'agreed')
   self.assertEqual(p.stat().st_mode&0o777,0o600)
 def test_playbook_only_observes_and_asserts(self):
  play,=json.loads(step.access_playbook())
  self.assertFalse(play['gather_facts']);self.assertFalse(play['become'])
  commands=[t for t in play['tasks'] if 'ansible.builtin.command' in t]
  self.assertTrue(all(t['changed_when'] is False for t in commands))
  root,=[t for t in commands if t.get('become')]
  self.assertEqual(root['ansible.builtin.command']['argv'],['/usr/bin/id','-u'])
  self.assertTrue(any('ansible_host in' in condition for t in play['tasks'] for condition in t.get('ansible.builtin.assert',{}).get('that',[])))
 def test_inventory_warnings_do_not_corrupt_json(self):
  response=subprocess.CompletedProcess([],0,'{"_meta": {"hostvars": {}}}','[WARNING]: hyphenated groups')
  with patch.object(step.subprocess,'run',return_value=response):
   self.assertIn('_meta',step.inventory_data({},Path('inventory.ini'),lambda s:None))
 def test_plan_never_writes_or_connects(self):
  with patch.object(step.tools,'guard'),patch.object(step,'execute') as execute,patch.object(step,'private_dir') as mkdir:
   self.assertEqual(step.main([]),0);execute.assert_not_called();mkdir.assert_not_called()
 def test_wrong_group_or_connection_is_rejected(self):
  data={'_meta':{'hostvars':{name:{'ansible_host':ip,'ansible_user':'joe','ansible_ssh_private_key_file':str(step.KEY),'ansible_python_interpreter':'/usr/bin/python3'} for name,ip in step.NODES.items()}}}
  for g in ('control','network','storage','baremetal','cinder-volume','cinder-volume-lvm'):
   data[g]={'hosts':list(step.NODES)}
  data.update(compute={'hosts':['node2','node3']},deployment={'hosts':['localhost']},all={'hosts':list(step.NODES)+['localhost']})
  step.validate_inventory(data)
  data['compute']['hosts'].append('node1')
  with self.assertRaises(RuntimeError):step.validate_inventory(data)
  data['compute']['hosts'].remove('node1');data['_meta']['hostvars']['node1']['ansible_host']='192.168.0.99'
  with self.assertRaises(RuntimeError):step.validate_inventory(data)

if __name__=='__main__':unittest.main()

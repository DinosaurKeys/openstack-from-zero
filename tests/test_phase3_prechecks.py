import contextlib,importlib.util,io,json,os,pathlib,tempfile,unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('precheck_tests',ROOT/'scripts/phase3-prechecks.py');step=importlib.util.module_from_spec(s);s.loader.exec_module(step)
def recap(nodes=('node1','node2','node3'),failed=0,ignored=0):
 return 'TASK [prechecks : Checking Ansible version]\nTASK [prechecks : Check if using quay.io/openstack.kolla images]\nPLAY RECAP ***\n'+''.join(n+' : ok=50 changed=0 unreachable=0 failed='+str(failed)+' skipped=20 rescued=0 ignored='+str(ignored)+'\n' for n in nodes)
class PrecheckTests(unittest.TestCase):
 def test_plan_does_not_prompt_connect_or_write(self):
  with patch.object(step,'execute') as execute,patch.object(step.getpass,'getpass') as prompt,contextlib.redirect_stdout(io.StringIO()):
   self.assertEqual(step.main([]),0);execute.assert_not_called();prompt.assert_not_called()
 def test_command_runs_full_prechecks_with_approved_images_and_no_global_sudo(self):
  command=list(map(str,step.precheck_command(pathlib.Path('/inventory'),pathlib.Path('/config'),pathlib.Path('/options'))))
  self.assertEqual(command[1],'prechecks');self.assertIn('--use-test-images',command);self.assertIn('baremetal',command)
  for forbidden in ['--become','--skip-tags','--tags','--check','deploy','bootstrap-servers']:self.assertNotIn(forbidden,command)
  self.assertIn('/config',command);self.assertIn('@/options',command)
 def test_success_requires_every_node_and_actual_role_execution(self):
  self.assertEqual(set(step.check_recap(recap())),set(step.inv.NODES))
  for text in [recap(nodes=('node1','node2')),recap(failed=1),recap(ignored=1),recap().replace('Checking Ansible version','other task'),'']:
   with self.assertRaises(RuntimeError):step.check_recap(text)
 def test_host_compare_catches_packages_or_hosts_file_changes(self):
  b={'node':'node1','facts':{'protected_config_hashes':{},'management_DNS':'DNS','chrony_active':'active','installed_packages':{'docker-ce':'version'},'hosts_file':'names'}}
  step.compare_host(b,b)
  for key in ('installed_packages','hosts_file'):
   a=json.loads(json.dumps(b));a['facts'][key]='different'
   with self.assertRaises(RuntimeError):step.compare_host(b,a)
 def test_input_snapshot_detects_changes_without_logging_values(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);config=root/'kolla';config.mkdir();(root/'host_vars').mkdir();inventory=root/'inventory';inventory.write_text('inventory')
   password=config/'passwords.yml';password.write_text('private-secret')
   before=step.private_inputs(config,inventory);self.assertNotIn('private-secret',str(before))
   password.write_text('changed-secret');self.assertNotEqual(before,step.private_inputs(config,inventory))
 def test_source_drift_stops(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);(root/'scripts').mkdir();(root/'scripts/phase3-precheck-sources.json').write_text(json.dumps({'source.yml':'0'*64}));(root/'source.yml').write_text('different')
   with patch.object(step.bootstrap,'reviewed_sources'),patch.object(step,'REPO',root),patch.object(step,'ENV',root),self.assertRaises(RuntimeError):step.source_guard()
 def test_ordering_and_sudo_secret_never_saved_or_logged(self):
  with tempfile.TemporaryDirectory() as d:
   work=pathlib.Path(d);events=[];lines=[]
   state=lambda node:{'node':node,'ready':True,'facts':{'protected_config_hashes':{},'management_DNS':'DNS','chrony_active':'active','installed_packages':{},'hosts_file':'names'}}
   def run(args,terminal=False):
    if '--syntax-check' in args:return ''
    if 'prechecks' in args:
     events.append('prechecks');(work/'kolla-prechecks.log').write_text(recap());return ''
    name=args[args.index('--limit')+1];stage=pathlib.Path(args[args.index('-i')+2]).stem
    events.append(stage+':'+name);(work/stage/(name+'.json')).write_text(json.dumps(state(name)));return ''
   with patch.object(step,'source_guard'),patch.object(step.validation,'local_checks',return_value=(work/'inventory',work/'kolla')),patch.object(step,'private_inputs',return_value={}),patch.object(step.inv,'ensure_agent'),patch.object(step.getpass,'getpass',return_value='DO_NOT_SAVE_SUDO_SECRET'),patch.object(step.tools,'Runner') as runner:
    runner.return_value.run.side_effect=run;step.execute(lines.append,lines.append,work,'f'*64)
   self.assertEqual(events,['before:node1','before:node2','before:node3','prechecks','after:node1','after:node2','after:node3'])
   self.assertNotIn('DO_NOT_SAVE_SUDO_SECRET','\n'.join(lines));self.assertNotIn('DO_NOT_SAVE_SUDO_SECRET',''.join(p.read_text() for p in work.rglob('*') if p.is_file()))
 def test_failed_host_readiness_blocks_prechecks(self):
  with tempfile.TemporaryDirectory() as d:
   work=pathlib.Path(d);commands=[]
   def run(args,terminal=False):
    commands.append(args)
    if '--syntax-check' not in args:raise RuntimeError('host needs review')
   with patch.object(step,'source_guard'),patch.object(step.validation,'local_checks',return_value=(work/'inventory',work/'kolla')),patch.object(step,'private_inputs',return_value={}),patch.object(step.inv,'ensure_agent'),patch.object(step.getpass,'getpass',return_value='secret'),patch.object(step.tools,'Runner') as runner:
    runner.return_value.run.side_effect=run
    with self.assertRaises(RuntimeError):step.execute(lambda s:None,lambda s:None,work,'f'*64)
   self.assertFalse(any('prechecks' in args for args in commands))
if __name__=='__main__':unittest.main()

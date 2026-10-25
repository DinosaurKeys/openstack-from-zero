import contextlib,importlib.util,io,json,os,pathlib,tempfile,unittest
from unittest.mock import patch,MagicMock
ROOT=pathlib.Path(__file__).resolve().parents[1]
def load(name,file):
 s=importlib.util.spec_from_file_location(name,ROOT/'scripts'/file);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
step=load('bootstrap_test','phase3-bootstrap.py')
node=load('bootstrap_node_test','phase3-node-state.py')
class BootstrapTests(unittest.TestCase):
 def test_default_plan_does_not_connect_prompt_or_apply(self):
  with patch.object(step,'execute') as execute,patch.object(step.getpass,'getpass') as prompt,contextlib.redirect_stdout(io.StringIO()):
   self.assertEqual(step.main([]),0);execute.assert_not_called();prompt.assert_not_called()
 def test_command_uses_explicit_private_paths_and_no_sudo_password_argument(self):
  c=step.bootstrap_command(pathlib.Path('/private/inventory.ini'),pathlib.Path('/private/kolla'),pathlib.Path('/private/options.json'),'node2')
  self.assertEqual(c[1],'bootstrap-servers');self.assertIn('--configdir',c);self.assertIn(pathlib.Path('/private/kolla'),c);self.assertIn('node2',c)
  self.assertNotIn('--ask-become-pass',c);self.assertNotIn('ansible_become_password',' '.join(map(str,c)));self.assertIn('--become',c)
 def test_reviewed_source_hash_guard_stops_on_drift(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);(root/'scripts').mkdir();(root/'scripts/phase3-reviewed-sources.json').write_text(json.dumps({'source.yml':'0'*64}));(root/'source.yml').write_text('different')
   with patch.object(step,'REPO',root),patch.object(step,'ENV',root),self.assertRaises(RuntimeError):step.reviewed_sources()
 def test_compare_preserves_recorded_network_time_ssh_config_and_dns(self):
  b={'node':'node1','facts':{'protected_config_hashes':{'network':'abc'},'management_DNS':'dns','chrony_active':'active'}}
  step.compare(b,b)
  a=json.loads(json.dumps(b));a['facts']['protected_config_hashes']['network']='different'
  with self.assertRaises(RuntimeError):step.compare(b,a)
 def test_playbook_checks_identity_and_readiness_then_saves_only_public_observations(self):
  p=json.loads(step.inspection_playbook(pathlib.Path('/private/run'),True))[0]
  self.assertEqual(p['hosts'],'baremetal');self.assertTrue(p['become']);self.assertFalse(p['gather_facts'])
  self.assertIn('--after',p['tasks'][2]['ansible.builtin.script']);self.assertEqual(p['tasks'][-1]['delegate_to'],'localhost');self.assertFalse(p['tasks'][-1]['become'])
  self.assertNotIn('password',json.dumps(p));self.assertEqual(p['tasks'][-1]['ansible.builtin.copy']['mode'],'0600')
 def test_wrong_node_cannot_reach_package_collection(self):
  with patch.object(node.socket,'gethostname',return_value='wrong'),patch.object(node,'run') as run,self.assertRaises(RuntimeError):node.collect('node1',False)
  run.assert_not_called()
 def test_all_preflights_precede_bootstrap_and_passwords_are_not_logged(self):
  with tempfile.TemporaryDirectory() as d:
   work=pathlib.Path(d);inventory=work/'inventory';config=work/'kolla';events=[];lines=[]
   state=lambda n:{'node':n,'ready':True,'facts':{'protected_config_hashes':{},'management_DNS':'dns','chrony_active':'active'}}
   def run(args,terminal=False):
    if '--syntax-check' in args:return ''
    if 'bootstrap-servers' in args:events.append('apply:'+args[args.index('--limit')+1]);return ''
    name=args[args.index('--limit')+1];stage=pathlib.Path(args[args.index('-i')+2]).stem
    events.append(stage+':'+name);(work/stage/(name+'.json')).write_text(json.dumps(state(name)));return ''
   with patch.object(step,'reviewed_sources'),patch.object(step.validation,'local_checks',return_value=(inventory,config)),patch.object(step.inv,'ensure_agent'),patch.object(step.getpass,'getpass',return_value='NEVER_LOG_SUDO_SECRET'),patch.object(step.tools,'Runner') as runner:
    runner.return_value.run.side_effect=run;step.execute(lines.append,work,'f'*64)
   self.assertEqual(events[:3],['before:node1','before:node2','before:node3'])
   self.assertEqual(events[3:],['apply:baremetal','after:node1','after:node2','after:node3'])
   self.assertNotIn('NEVER_LOG_SUDO_SECRET','\n'.join(lines));self.assertNotIn('NEVER_LOG_SUDO_SECRET',''.join(p.read_text() for p in work.rglob('*') if p.is_file()))
 def test_failed_preflight_stops_before_any_bootstrap(self):
  with tempfile.TemporaryDirectory() as d:
   work=pathlib.Path(d);commands=[]
   def run(args,terminal=False):
    commands.append(args)
    if '--syntax-check' not in args:raise RuntimeError('preflight blocked')
   with patch.object(step,'reviewed_sources'),patch.object(step.validation,'local_checks',return_value=(work/'inventory',work/'kolla')),patch.object(step.inv,'ensure_agent'),patch.object(step.getpass,'getpass',return_value='secret'),patch.object(step.tools,'Runner') as runner:
    runner.return_value.run.side_effect=run
    with self.assertRaises(RuntimeError):step.execute(lambda s:None,work,'a'*64)
   self.assertFalse(any('bootstrap-servers' in args for args in commands))
 def test_postchecks_require_working_docker_sdk_and_expected_packages(self):
  from pathlib import Path
  installed=['git','iputils-ping','tzdata','ca-certificates','gnupg','docker-ce','apparmor','python3-setuptools','python3-pip','python3-docker','python3-dbus','ufw']
  sdk=['True']
  def run(args,allowed=(0,)):
   if args[0]=='dpkg-query':return '\n'.join(p+'\tinstalled\t1.0' for p in installed)
   if args[0]=='resolvectl':return 'DNS 192.168.0.1'
   if args[0]=='systemctl':return 'enabled' if 'is-enabled' in args else 'active'
   if args[0]=='docker':return 'recorded-engine-version'
   if args[0]=='/usr/bin/python3':return sdk[0]
   if args[0]=='ufw':return 'Status: inactive'
   if args[0]=='getent':return node.NODES[args[-1]]+' STREAM '+args[-1]
   raise AssertionError(args)
  def read(path,*a,**k):
   if str(path)=='/etc/hosts':return '127.0.0.1 localhost'
   if str(path)=='/etc/docker/daemon.json':return json.dumps({'iptables':False,'bridge':'none','ip-forward':False})
   raise AssertionError(str(path))
  with patch.object(node.socket,'gethostname',return_value='node1'),patch.object(node.os,'geteuid',return_value=0),patch.dict(os.environ,{'SUDO_USER':'joe'}),patch.object(node,'run',side_effect=run),patch.object(node,'digest_files',return_value={}),patch.object(Path,'is_file',return_value=True),patch.object(Path,'read_text',read):
   self.assertTrue(node.collect('node1',True)['ready'])
   sdk[0]='False';self.assertFalse(node.collect('node1',True)['ready'])
if __name__=='__main__':unittest.main()

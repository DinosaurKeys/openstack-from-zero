import contextlib,importlib.util,io,json,pathlib,tempfile,unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[1]
def load(name,file):
 s=importlib.util.spec_from_file_location(name,ROOT/'scripts'/file);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
step=load('deploy_test','phase3-deploy.py');node_step=load('deploy_node_test','phase3-deploy-node.py')
def recap():
 return ''.join('TASK ['+m+' task]\n' for m in ('mariadb :','keystone :','nova-cell :','neutron :','cinder :','horizon :'))+'PLAY RECAP ***\n'+''.join(n+' : ok=100 changed=20 unreachable=0 failed=0 skipped=40 rescued=0 ignored=0\n' for n in step.inv.NODES)
def state(node,after):
 facts={'protected_config_hashes':{},'management_DNS':'DNS','chrony_active':'active'}
 if after:
  facts['VIP on management interface']=node=='node2'
  facts['galera']={'wsrep_cluster_state_uuid':'12345678-1234-1234-1234-123456789abc'}
 return {'node':node,'ready':True,'facts':facts}
class DeploymentTests(unittest.TestCase):
 def test_plan_has_no_writes_prompts_or_connections(self):
  with patch.object(step,'execute') as execute,patch.object(step.getpass,'getpass') as prompt,contextlib.redirect_stdout(io.StringIO()):
   self.assertEqual(step.main([]),0);execute.assert_not_called();prompt.assert_not_called()
 def test_deploy_command_uses_full_release_action_and_existing_inputs(self):
  c=list(map(str,step.deploy_command(pathlib.Path('/inventory'),pathlib.Path('/config'),pathlib.Path('/options'))))
  self.assertEqual(c[1],'deploy');self.assertIn('/config',c);self.assertIn('/inventory',c);self.assertIn('@/options',c);self.assertIn('baremetal',c)
  for bad in ('--use-test-images','--become','--skip-tags','--tags','--check','destroy','bootstrap-servers','post-deploy'):self.assertNotIn(bad,c)
 def test_recap_checks_every_node_and_service_task_evidence(self):
  self.assertEqual(set(step.deployment_recap(recap())),set(step.inv.NODES))
  for bad in ('',recap().replace('node3 :','other :'),recap().replace('failed=0','failed=1'),recap().replace('rescued=0','rescued=1'),recap().replace('TASK [horizon :','TASK [other :')):
   with self.assertRaises(RuntimeError):step.deployment_recap(bad)
 def test_vip_requires_one_owner_and_three_ready_nodes(self):
  states={n:state(n,True) for n in step.inv.NODES};self.assertEqual(step.verify_vip(states),'node2')
  states['node1']['facts']['VIP on management interface']=True
  with self.assertRaises(RuntimeError):step.verify_vip(states)
  states.pop('node1')
  with self.assertRaises(RuntimeError):step.verify_vip(states)
 def test_container_health_does_not_accept_missing_stopped_or_starting(self):
  states={n:{'running':True,'health':'healthy'} for n in node_step.REQUIRED}
  self.assertTrue(node_step.healthy(states));states['horizon']['health']='not configured';self.assertTrue(node_step.healthy(states))
  states['horizon']['health']='starting';self.assertFalse(node_step.healthy(states))
  states['horizon']['health']='healthy';states['horizon']['running']=False;self.assertFalse(node_step.healthy(states))
  states.pop('horizon');self.assertFalse(node_step.healthy(states))
 def test_node_observations_verify_bridge_health_and_management(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d)
   def paths(path,*parts):return root/str(path).lstrip('/')/pathlib.Path(*parts)
   addresses=[{'ifname':'enp0s31f6','addr_info':[{'local':'192.168.0.201','prefixlen':24},{'local':'192.168.0.151','prefixlen':32}]},{'ifname':'enx00e04c2c1678','address':'00:e0:4c:2c:16:78','addr_info':[],'flags':['UP','LOWER_UP']}]
   def read(args):
    if args[:3]==['ip','-j','address']:return json.dumps(addresses)
    if args[:4]==['ip','-j','-4','route']:return '[{"dev":"enp0s31f6","gateway":"192.168.0.1"}]'
    if args[0]=='systemctl':return 'active'
    if args[0]=='timedatectl':return 'yes'
    if args[0]=='resolvectl':return 'DNS'
    if args[0]=='vgs':return '{"report":[{"vg":[{"vg_name":"cinder-volumes","pv_count":"1","lv_count":"1"}]}]}'
    if 'port-to-br' in args:return 'br-ex'
    if 'list-ports' in args:return 'enx00e04c2c1678'
    if args[:2]==['docker','inspect']:return 'safe image references'
    raise AssertionError(args)
   states={n:{'running':True,'status':'running','health':'healthy'} for n in node_step.REQUIRED}
   with patch.object(node_step.os,'geteuid',return_value=0),patch.dict(node_step.os.environ,{'SUDO_USER':'joe'}),patch.object(node_step.socket,'gethostname',return_value='node2'),patch.object(node_step,'Path',side_effect=paths),patch.object(node_step,'run',side_effect=read),patch.object(node_step,'container_states',return_value=states),patch.object(node_step,'ha_configuration',return_value=({},True)),patch.object(node_step,'galera_status',return_value=({},True)):
    result=node_step.collect('node2');self.assertTrue(result['ready']);self.assertTrue(result['facts']['VIP on management interface'])
    # A management-address mismatch must not be treated as successful deployment.
    addresses[0]['addr_info'][0]['local']='192.168.0.209'
    self.assertFalse(node_step.collect('node2')['ready'])
 def test_identity_guard_precedes_node_reads(self):
  with patch.object(node_step.os,'geteuid',return_value=1000),patch.object(node_step,'run') as read:
   with self.assertRaises(RuntimeError):node_step.collect('node1')
   read.assert_not_called()
 def test_post_playbook_has_no_empty_vg_or_vip_conflict_probe(self):
  value=step.observation_playbook(pathlib.Path('/private/after'));self.assertIn('phase3-deploy-node.py',value)
  self.assertNotIn('phase2-node-readiness.py',value);self.assertNotIn('phase3-node-state.py',value)
  self.assertFalse(json.loads(value)[0]['tasks'][1]['become'])
 def simulate(self,fail=None):
  with tempfile.TemporaryDirectory() as d:
   work=pathlib.Path(d);events=[];lines=[];commands=[];envs=[]
   def runner_factory(log,env):
    envs.append(env.copy())
    class Runner:
     def run(self,args,terminal=False):
      args=list(map(str,args));commands.append(args)
      if '--syntax-check' in args:return ''
      if 'prechecks' in args:
       events.append('prechecks')
       if fail=='prechecks':raise RuntimeError('precheck stopped')
       (work/'kolla-prechecks.log').write_text('precheck transcript');return ''
      if 'deploy' in args:
       events.append('deploy')
       if fail=='deploy':raise RuntimeError('deploy stopped')
       (work/'kolla-deploy.log').write_text(recap());return ''
      node=args[args.index('--limit')+1];play=pathlib.Path(args[args.index('-i')+2]);stage=play.stem;events.append(stage+':'+node)
      if fail=='before' and stage=='before':raise RuntimeError('before stopped')
      (work/stage/(node+'.json')).write_text(json.dumps(state(node,stage=='after')));return ''
    return Runner()
   with patch.object(step,'source_guard'),patch.object(step.validation,'local_checks',return_value=(work/'inventory',work/'kolla')),patch.object(step.pre,'private_inputs',return_value={}),patch.object(step.inv,'ensure_agent'),patch.object(step.getpass,'getpass',side_effect=['sudo-secret-'+n for n in step.inv.NODES]),patch.object(step.tools,'Runner',side_effect=runner_factory),patch.object(step.pre,'check_recap'):
    if fail:
     with self.assertRaises(RuntimeError):step.execute(lines.append,lines.append,work,'f'*64)
    else:step.execute(lines.append,lines.append,work,'f'*64)
   contents=''.join(p.read_text() for p in work.rglob('*') if p.is_file())+'\n'.join(lines)+str(commands)
   self.assertNotIn('sudo-secret-',contents)
   deploy_env=[e for e in envs if str(e.get('ANSIBLE_LOG_PATH','')).endswith('kolla-deploy.log')]
   if deploy_env:
    for n in step.inv.NODES:self.assertEqual(deploy_env[0]['OFZ_SUDO_'+n],'sudo-secret-'+n)
   return events,lines
 def test_ordering_password_selection_and_no_secret_in_files_or_arguments(self):
  events,lines=self.simulate();self.assertEqual(events,['before:node1','before:node2','before:node3','prechecks','deploy','after:node1','after:node2','after:node3'])
  self.assertIn('RESULT: OPENSTACK_DEPLOYED for all',lines)
 def test_failure_before_or_in_prechecks_blocks_deployment(self):
  for where in ('before','prechecks'):
   events,_=self.simulate(where);self.assertNotIn('deploy',events)
 def test_deploy_failure_stops_without_retry_or_postchecks(self):
  events,lines=self.simulate('deploy');self.assertEqual(events.count('deploy'),1);self.assertFalse(any(e.startswith('after:') for e in events));self.assertFalse(any('OPENSTACK_DEPLOYED' in l for l in lines))
 def test_source_drift_blocks_action(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);(root/'scripts').mkdir();(root/'scripts/phase3-deploy-sources.json').write_text('{"changed.py":"wrong"}');(root/'changed.py').write_text('changed')
   with patch.object(step.pre,'source_guard'),patch.object(step,'REPO',root),patch.object(step,'ENV',root),self.assertRaises(RuntimeError):step.source_guard()
if __name__=='__main__':unittest.main()

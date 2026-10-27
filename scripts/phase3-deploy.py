#!/usr/bin/env python3
"""Chapter C: fresh prechecks, then standard Kolla deployment. Default: plan only."""
import sys
sys.dont_write_bytecode=True
import argparse,datetime as dt,getpass,hashlib,importlib.util,json,os,re,shlex,tempfile
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
ENV=Path('/home/joe/venvs/kolla')
spec=importlib.util.spec_from_file_location('deploy_prechecks',Path(__file__).with_name('phase3-prechecks.py'))
pre=importlib.util.module_from_spec(spec);spec.loader.exec_module(pre)
validation=pre.validation;inv=pre.inv;tools=pre.tools
OPTIONS=dict(pre.OPTIONS)

def source_guard():
 pre.source_guard()
 for name,expected in json.loads((REPO/'scripts/phase3-deploy-sources.json').read_text()).items():
  path=ENV/name
  if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
   raise RuntimeError('Deployment source differs from reviewed release: '+name)

def deploy_command(inventory,config,options):
 # --use-test-images is a PRECHECKS-only CLI flag in Kolla 23, not a deploy flag.
 # No global --become: Kolla's node tasks request sudo where needed.
 return [ENV/'bin/kolla-ansible','deploy','--configdir',config,'-i',inventory,'--limit','baremetal',
  '-e','@'+str(options),'-e','ansible_python_interpreter=/usr/bin/python3']

def deployment_recap(text):
 if 'PLAY RECAP' not in text:raise RuntimeError('No deployment recap recorded')
 recap=text.rsplit('PLAY RECAP',1)[1];result={}
 for node in inv.NODES:
  match=re.search(r'\b'+node+r'\s*:\s*(ok=\d+[^\n]*)',recap)
  if not match:raise RuntimeError('Deployment recap missing '+node)
  values={k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',match.group(1))}
  if not values.get('ok') or any(values.get(k,0) for k in ('failed','unreachable','rescued')):
   raise RuntimeError('Deployment recap needs review: '+node)
  result[node]=values
 # Log callbacks may print "...ignoring" without a hostname. Attribute the
 # exact FAILED event itself to its task/node, then reconcile recap counts.
 allowed={n:0 for n in inv.NODES}; task=''
 for line in text.splitlines():
  match=re.search(r'TASK \[([^\]]+)\]',line)
  if match:task=match.group(1)
  failed=re.search(r'fatal: \[([^\]]+)\]: FAILED!\s*=>\s*(.*)',line)
  if not failed:continue
  node,payload=failed.groups()
  if node not in inv.NODES:raise RuntimeError('Unexpected failed deployment host or delegation')
  try:details=json.loads(payload)
  except (ValueError,TypeError):raise RuntimeError('Unparseable failed deployment event: '+node) from None
  expected='Timeout when waiting for search string MariaDB in '+inv.NODES[node]+':3306'
  if task!='mariadb : Check MariaDB service port liveness' or not isinstance(details,dict) or details.get('changed') is not False or details.get('msg')!=expected or allowed[node]:
   raise RuntimeError('Unexpected failed deployment task: '+node)
  allowed[node]+=1
 for node,values in result.items():
  if values.get('ignored',0)!=allowed[node]:raise RuntimeError('Ignored deployment task lacks reviewed discovery evidence: '+node)
 # These tasks establish that service deployment ran, rather than just a syntax check.
 for marker in ('mariadb :','keystone :','nova-cell :','neutron :','cinder :','horizon :'):
  if 'TASK ['+marker not in text:raise RuntimeError('Deployment service execution missing: '+marker)
 return result

def observation_playbook(folder):
 tasks=[{'name':'Read deployment observations','ansible.builtin.script':shlex.join([str(REPO/'scripts/phase3-deploy-node.py'),'--node','{{ inventory_hostname }}'])+' executable=/usr/bin/python3','register':'state','changed_when':False},
  {'name':'Save private deployment observation','ansible.builtin.copy':{'content':'{{ state.stdout }}','dest':str(folder/'{{ inventory_hostname }}.json'),'mode':'0600'},'delegate_to':'localhost','become':False},
  {'name':'Confirm observed deployment readiness','ansible.builtin.assert':{'that':['(state.stdout | from_json).ready','(state.stdout | from_json).node == inventory_hostname']}}]
 return json.dumps([{'name':'Chapter C deployment observations','hosts':'baremetal','become':True,'gather_facts':False,'tasks':tasks}],indent=2)+'\n'

def verify_vip(observations):
 if set(observations)!=set(inv.NODES) or not all(s.get('ready') for s in observations.values()):
  raise RuntimeError('All three deployment observations must pass')
 owners=[n for n,s in observations.items() if s['facts'].get('VIP on management interface') is True]
 if len(owners)!=1:raise RuntimeError('Expected exactly one observed API VIP owner; review Keepalived state')
 uuids={s['facts'].get('galera',{}).get('wsrep_cluster_state_uuid') for s in observations.values()}
 if len(uuids)!=1 or not all(isinstance(u,str) and re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}',u) and int(u.replace('-',''),16) for u in uuids):
  raise RuntimeError('Expected one valid shared MariaDB cluster UUID across three nodes')
 return owners[0]

def execute(log,private_log,work,expected_ca):
 source_guard();env=tools.tool_environment()
 log('Checking existing local inputs; detailed output goes to the private report.')
 inventory,config=validation.local_checks(private_log,env,expected_ca)
 baseline=pre.private_inputs(config,inventory);inv.ensure_agent(env,log)
 options=work/'deploy-options.json';options.write_text(json.dumps(OPTIONS,indent=2)+'\n');options.chmod(0o600)
 before_dir=work/'before';after_dir=work/'after'
 for folder in (before_dir,after_dir):inv.private_dir(folder)
 before_play=work/'before.yml';after_play=work/'after.yml'
 before_play.write_text(pre.bootstrap.inspection_playbook(before_dir,True));before_play.chmod(0o600)
 after_play.write_text(observation_playbook(after_dir));after_play.chmod(0o600)
 for play in (before_play,after_play):tools.Runner(private_log,env).run([ENV/'bin/ansible-playbook','-i',inventory,play,'--syntax-check'])
 log('Enter each named node sudo password; input is hidden. No R2d2 sudo password is requested.')
 passwords={node:getpass.getpass('Joe sudo password on '+node+': ') for node in inv.NODES}
 child=dict(env,ANSIBLE_BECOME_ASK_PASS='False')
 for node,password in passwords.items():child['OFZ_SUDO_'+node]=password
 def inspect(node,play,folder):
  tools.Runner(private_log,dict(child,ANSIBLE_BECOME_PASS=passwords[node])).run([ENV/'bin/ansible-playbook','-i',inventory,play,'--limit',node],terminal=True)
  result=json.loads((folder/(node+'.json')).read_text());private_log(json.dumps(result,indent=2))
  if result.get('node')!=node or not result.get('ready'):raise RuntimeError('Node observations need review: '+node)
  return result
 try:
  before={}
  for node in inv.NODES:
   log('BEFORE checks: '+node);before[node]=inspect(node,before_play,before_dir)
  log('Fresh full Kolla prechecks run before any deployment.')
  pre_env=dict(child,ANSIBLE_LOG_PATH=str(work/'kolla-prechecks.log'))
  tools.Runner(private_log,pre_env).run(pre.precheck_command(inventory,config,options),terminal=True)
  pre.check_recap((work/'kolla-prechecks.log').read_text())
  if pre.private_inputs(config,inventory)!=baseline:raise RuntimeError('Private inputs changed during prechecks; deployment not started')
  log('PASS fresh prechecks. Starting standard Kolla deployment on all three nodes.')
  deploy_env=dict(child,ANSIBLE_LOG_PATH=str(work/'kolla-deploy.log'))
  tools.Runner(private_log,deploy_env).run(deploy_command(inventory,config,options),terminal=True)
  stats=deployment_recap((work/'kolla-deploy.log').read_text())
  observations={}
  for node in inv.NODES:
   log('AFTER deployment observations: '+node)
   observations[node]=inspect(node,after_play,after_dir)
   pre.bootstrap.compare(before[node],observations[node])
   log('DEPLOY_RECAP '+node+': '+json.dumps(stats[node],sort_keys=True))
  log('Observed API VIP owner: '+verify_vip(observations))
  if pre.private_inputs(config,inventory)!=baseline:raise RuntimeError('Private input files changed; review before cloud access')
  log('PASS existing inventory, host variables, globals, credentials and local certificates unchanged.')
  log('RESULT: OPENSTACK_DEPLOYED for all')
  log('Return this report. Chapter D will verify live HTTPS, login and cloud API/service registration. VM and volume tests follow later.')
 finally:
  passwords.clear()
  for node in inv.NODES:child.pop('OFZ_SUDO_'+node,None)

def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--apply',action='store_true');p.add_argument('--expected-ca',default=validation.CA_FINGERPRINT);args=p.parse_args(argv)
 if not re.fullmatch('[0-9a-f]{64}',args.expected_ca):p.error('--expected-ca requires 64 lowercase hexadecimal characters')
 if not args.apply:
  print('PLAN ONLY: verify existing configuration and nodes, run fresh full prechecks, deploy through Kolla, observe core containers/VIP/USB bridges, save one private report.')
  print('Read Phase 3 Chapter C before --apply. No files changed or nodes contacted.');return 0
 validation.config_step.load_step('chapter_c_r2d2','phase2-check.py').require_r2d2();tools.check_paths(ENV)
 if not sys.stdin.isatty():raise RuntimeError('Use an interactive R2d2 terminal for key and sudo prompts')
 if Path(sys.prefix)!=ENV:
  exe=str(ENV/'bin/python');os.execve(exe,[exe,str(Path(__file__).resolve()),*sys.argv[1:]],tools.tool_environment())
 os.umask(0o077);reports=REPO/'reports';inv.private_dir(reports)
 stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-')
 fd,name=tempfile.mkstemp(prefix='phase3-deploy-'+stamp,suffix='.txt',dir=reports)
 work=Path(tempfile.mkdtemp(prefix='phase3-deploy-'+stamp,dir=reports));status=1
 print('Private report (being written): '+name,flush=True)
 with os.fdopen(fd,'w') as output:
  def private_log(line):output.write(line+'\n');output.flush()
  def log(line):print(line,flush=True);private_log(line)
  log('Phase 3 — Chapter C: standard Kolla deployment — APPLY + VERIFY')
  try:execute(log,private_log,work,args.expected_ca);status=0
  except (Exception,KeyboardInterrupt) as e:
   log('RESULT: OPENSTACK_DEPLOYMENT_NEEDS_REVIEW')
   log('Stopped: '+str(e) if isinstance(e,RuntimeError) else 'Stopped ('+type(e).__name__+'); private-input library details withheld.')
   log('Earlier deployment tasks may have changed nodes. No automatic rollback, cleanup, retry or configuration bypass. Return this report.')
  finally:
   for filename in ('kolla-prechecks.log','kolla-deploy.log'):
    transcript=work/filename
    if transcript.is_file():private_log('\nFull '+filename+' transcript:\n'+transcript.read_text())
 print('Private report: '+name);print('Read it with: cat '+shlex.quote(name));return status
if __name__=='__main__':
 try:sys.exit(main())
 except (OSError,RuntimeError) as e:print('Stopped: '+str(e),file=sys.stderr);sys.exit(1)

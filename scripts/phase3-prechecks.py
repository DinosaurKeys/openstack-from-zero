#!/usr/bin/env python3
"""Chapter B: run full Kolla prechecks; default shows the plan only."""
import sys
sys.dont_write_bytecode=True
import argparse,datetime as dt,getpass,hashlib,importlib.util,json,os,re,shlex,tempfile
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
ENV=Path('/home/joe/venvs/kolla')
spec=importlib.util.spec_from_file_location('host_bootstrap',Path(__file__).with_name('phase3-bootstrap.py'))
bootstrap=importlib.util.module_from_spec(spec);spec.loader.exec_module(bootstrap)
validation=bootstrap.validation;inv=bootstrap.inv;tools=bootstrap.tools
OPTIONS={'ansible_become_password':"{{ lookup('ansible.builtin.env', 'OFZ_SUDO_' ~ inventory_hostname) }}",
 'kolla_ansible_delegate_facts_hosts':['node1','node2','node3'], 'kolla_container_engine':'docker',
 'prechecks_enable_host_os_checks':True,'prechecks_enable_host_ntp_checks':True}

def source_guard():
 bootstrap.reviewed_sources()
 for name,expected in json.loads((REPO/'scripts/phase3-precheck-sources.json').read_text()).items():
  path=ENV/name
  if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
   raise RuntimeError('Precheck source differs from reviewed files: '+name)

def private_inputs(config,inventory):
 paths=[inventory,*config.rglob('*'),*(config.parent/'host_vars').glob('*.yml')]
 return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}

def precheck_command(inventory,config,options):
 # No global --become: delegated local certificate/password-file checks run as joe.
 # Individual Kolla tasks request sudo on the nodes where required.
 return [ENV/'bin/kolla-ansible','prechecks','--configdir',config,'-i',inventory,'--limit','baremetal',
         '--use-test-images','-e','@'+str(options),'-e','ansible_python_interpreter=/usr/bin/python3']

def check_recap(text):
 if 'TASK [prechecks : Checking Ansible version]' not in text:
  raise RuntimeError('Full prechecks role execution was not confirmed in the transcript')
 if 'TASK [prechecks : Check if using quay.io/openstack.kolla images]' not in text:
  raise RuntimeError('Registry acceptance check was not observed in the transcript')
 # Use only the final recap, not similarly named earlier log lines.
 if 'PLAY RECAP' not in text:raise RuntimeError('No final Ansible recap in the transcript')
 recap=text.rsplit('PLAY RECAP',1)[1]
 result={}
 for name in inv.NODES:
  match=re.search(r'\b'+name+r'\s*:\s*(ok=\d+[^\n]*)',recap)
  if not match:raise RuntimeError('Missing final precheck recap for '+name)
  values={k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',match.group(1))}
  if not values.get('ok') or any(values.get(k,0) for k in ('failed','unreachable','rescued','ignored')):
   raise RuntimeError('Final precheck recap requires review for '+name)
  result[name]=values
 return result

def compare_host(before,after):
 bootstrap.compare(before,after)
 for key in ('installed_packages','hosts_file'):
  if before['facts'][key]!=after['facts'][key]:raise RuntimeError('Recorded '+key+' changed during prechecks on '+after['node'])

def execute(log,private_log,work,expected_ca):
 source_guard()
 env=tools.tool_environment()
 log('Checking saved versions, inventory, configuration and certificates; detailed output goes to the report.')
 inventory,config=validation.local_checks(private_log,env,expected_ca)
 log('PASS local configuration and existing certificate checks.')
 baseline=private_inputs(config,inventory)
 inv.ensure_agent(env,log)
 options=work/'precheck-options.json';options.write_text(json.dumps(OPTIONS,indent=2)+'\n');options.chmod(0o600)
 folders={};plays={}
 for stage in ('before','after'):
  folders[stage]=work/stage;inv.private_dir(folders[stage]);plays[stage]=work/(stage+'.yml')
  # Reuse Chapter A's post-bootstrap observations before and after this check-only step.
  plays[stage].write_text(bootstrap.inspection_playbook(folders[stage],True));plays[stage].chmod(0o600)
  tools.Runner(private_log,env).run([ENV/'bin/ansible-playbook','-i',inventory,plays[stage],'--syntax-check'])
 log('Next: enter each named node sudo password. Input is hidden; press Enter after typing.')
 passwords={node:getpass.getpass('Joe sudo password on '+node+': ') for node in inv.NODES}
 def inspect(node,stage):
  log(stage.upper()+' host checks: '+node)
  child=dict(env,ANSIBLE_BECOME_PASS=passwords[node],ANSIBLE_BECOME_ASK_PASS='False')
  tools.Runner(private_log,child).run([ENV/'bin/ansible-playbook','-i',inventory,plays[stage],'--limit',node],terminal=True)
  result=json.loads((folders[stage]/(node+'.json')).read_text())
  if result.get('node')!=node or not result.get('ready'):raise RuntimeError('Host observations need review: '+node)
  private_log(stage+' '+node+': '+json.dumps(result,indent=2));return result
 before={node:inspect(node,'before') for node in inv.NODES}
 log('Running FULL Kolla prechecks on node1, node2 and node3. No deploy/bootstrap command will run.')
 child=dict(env,ANSIBLE_BECOME_ASK_PASS='False',ANSIBLE_LOG_PATH=str(work/'kolla-prechecks.log'))
 for node,password in passwords.items():child['OFZ_SUDO_'+node]=password
 tools.Runner(private_log,child).run(precheck_command(inventory,config,options),terminal=True)
 transcript=work/'kolla-prechecks.log'
 stats=check_recap(transcript.read_text() if transcript.is_file() else '')
 for node in inv.NODES:
  compare_host(before[node],inspect(node,'after'))
  log('PRECHECK_RECAP '+node+': '+json.dumps(stats[node],sort_keys=True))
 if private_inputs(config,inventory)!=baseline:raise RuntimeError('Private input files changed during prechecks; review before deployment')
 passwords.clear()
 log('PASS recorded host package/configuration observations and private input files unchanged.')
 log('RESULT: KOLLA_PRECHECKS_PASSED for all')
 log('Return this report for review. OpenStack deployment and live Horizon HTTPS remain pending.')

def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',action='store_true');p.add_argument('--expected-ca',default=validation.CA_FINGERPRINT);args=p.parse_args(argv)
 if not re.fullmatch('[0-9a-f]{64}',args.expected_ca):p.error('--expected-ca needs 64 lowercase hexadecimal characters')
 if not args.run:
  print('PLAN ONLY: verify the prepared hosts; run full Kolla prechecks with the approved lab-image source; collect one private report.')
  print('No nodes contacted or files changed. Read Phase 3 Chapter B before --run.');return 0
 validation.config_step.load_step('chapter_b_r2d2','phase2-check.py').require_r2d2();tools.check_paths(ENV)
 if not sys.stdin.isatty():raise RuntimeError('Use an interactive terminal for SSH-key and node sudo passwords')
 if Path(sys.prefix)!=ENV:
  exe=str(ENV/'bin/python');os.execve(exe,[exe,str(Path(__file__).resolve()),*sys.argv[1:]],tools.tool_environment())
 os.umask(0o077);reports=REPO/'reports';inv.private_dir(reports)
 stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-')
 fd,name=tempfile.mkstemp(prefix='phase3-prechecks-'+stamp,suffix='.txt',dir=reports)
 work=Path(tempfile.mkdtemp(prefix='phase3-prechecks-'+stamp,dir=reports));status=1
 print('Private report (being written): '+name,flush=True)
 with os.fdopen(fd,'w') as output:
  def private_log(line):output.write(line+'\n');output.flush()
  def log(line):print(line,flush=True);private_log(line)
  log('Phase 3 — Chapter B: FULL Kolla prechecks — no bootstrap or deployment')
  log('Approved image source: quay.io/openstack.kolla via --use-test-images. Existing independent-LVM exception retained.')
  try:execute(log,private_log,work,args.expected_ca);status=0
  except (Exception,KeyboardInterrupt) as e:
   log('RESULT: KOLLA_PRECHECKS_NEEDS_REVIEW')
   log('Stopped: '+str(e) if isinstance(e,RuntimeError) else 'Stopped ('+type(e).__name__+'); private-input library details withheld.')
   log('No automatic repair, disabled checks, bootstrap, deployment or retry. Return this report.')
  finally:
   transcript=work/'kolla-prechecks.log'
   if transcript.is_file():private_log('\nFull Kolla precheck transcript:\n'+transcript.read_text())
 print('Private report: '+name);print('Read it with: cat '+shlex.quote(name));return status
if __name__=='__main__':
 try:sys.exit(main())
 except (OSError,RuntimeError) as e:print('Stopped: '+str(e),file=sys.stderr);sys.exit(1)

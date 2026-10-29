#!/usr/bin/env python3
"""Chapter A: one guarded wrapper around Kolla bootstrap; default plan only."""
import sys
sys.dont_write_bytecode=True
import argparse,datetime as dt,getpass,hashlib,importlib.util,json,os,shlex,subprocess,tempfile
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
ENV=Path('/home/joe/venvs/kolla')
spec=importlib.util.spec_from_file_location('phase2_validation',Path(__file__).with_name('phase2-validate.py'))
validation=importlib.util.module_from_spec(spec);spec.loader.exec_module(validation)
inv=validation.inv;tools=validation.tools
OPTIONS={'disable_firewall':True,'create_kolla_user':False,'customize_etc_hosts':True,'kolla_container_engine':'docker',
 'enable_multipathd':False,'enable_nova_libvirt_container':True,'apparmor_remove_libvirt_profile':True,'virtualenv':None,
 'kolla_ansible_delegate_facts_hosts':['node1','node2','node3'],
 'ansible_become_password':"{{ lookup('ansible.builtin.env', 'OFZ_SUDO_' ~ inventory_hostname) }}"}

def reviewed_sources():
 manifest=json.loads((REPO/'scripts/phase3-reviewed-sources.json').read_text())
 for name,expected in manifest.items():
  path=ENV/name
  if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
   raise RuntimeError('Installed bootstrap source differs from reviewed source: '+name+'; no bootstrap started')

def inspection_playbook(work,after):
 # Tasks use Kolla-independent read commands; their output is returned in one private JSON file.
 tasks=[]
 for label,file in [('readiness','phase2-node-readiness.py'),('host_state','phase3-node-state.py')]:
  command=shlex.join([str(REPO/'scripts'/file),'--node','{{ inventory_hostname }}'])+(' --after' if after and label=='host_state' else '')+' executable=/usr/bin/python3'
  tasks.append({'name':'Read '+label,'ansible.builtin.script':command,'register':label,'changed_when':False})
  tasks.append({'name':'Confirm '+label,'ansible.builtin.assert':{'that':[f'({label}.stdout | from_json).ready',f'({label}.stdout | from_json).node == inventory_hostname']}})
 tasks.append({'name':'Save private host observation on R2d2','ansible.builtin.copy':{
  'content':'{{ host_state.stdout }}','dest':str(work/'{{ inventory_hostname }}.json'),'mode':'0600'},'delegate_to':'localhost','become':False})
 return json.dumps([{'name':'Chapter A '+('after' if after else 'before')+' checks','hosts':'baremetal','gather_facts':False,'become':True,'tasks':tasks}],indent=2)+'\n'

def compare(before,after):
 for key in ('protected_config_hashes','management_DNS','chrony_active'):
  if before['facts'][key]!=after['facts'][key]:raise RuntimeError('Existing configuration observation changed: '+key+' on '+after['node'])

def bootstrap_command(inventory,config,options,node):
 return [ENV/'bin/kolla-ansible','bootstrap-servers','--configdir',config,'-i',inventory,'--limit',node,'--become',
         '-e','@'+str(options),'-e','ansible_python_interpreter=/usr/bin/python3']

def execute(log,work,expected_ca):
 env=tools.tool_environment()
 reviewed_sources()
 inventory,config=validation.local_checks(log,env,expected_ca)
 inv.ensure_agent(env,log)
 options=work/'bootstrap-options.json';options.write_text(json.dumps(OPTIONS,indent=2)+'\n');options.chmod(0o600)
 folders={};plays={}
 for stage in ('before','after'):
  folders[stage]=work/stage;inv.private_dir(folders[stage]);plays[stage]=work/(stage+'.yml')
  plays[stage].write_text(inspection_playbook(folders[stage],stage=='after'));plays[stage].chmod(0o600)
 run=tools.Runner(log,env)
 for stage in plays:run.run([ENV/'bin/ansible-playbook','-i',inventory,plays[stage],'--syntax-check'])
 passwords={}
 # Passwords remain in process memory/child environment, never arguments, reports or files.
 for node in inv.NODES:passwords[node]=getpass.getpass('Joe sudo password on '+node+': ')
 def inspect(node,stage):
  child=dict(env,ANSIBLE_BECOME_PASS=passwords[node],ANSIBLE_BECOME_ASK_PASS='False')
  tools.Runner(log,child).run([ENV/'bin/ansible-playbook','-i',inventory,plays[stage],'--limit',node],terminal=True)
  state=json.loads((folders[stage]/(node+'.json')).read_text())
  if state.get('node')!=node or not state.get('ready'):raise RuntimeError('Incomplete host observations for '+node)
  log(stage+' '+node+': '+json.dumps(state,indent=2))
  return state
 before={node:inspect(node,'before') for node in inv.NODES}
 log('PASS all three fresh preflight checks. Standard Kolla host preparation starts next.')
 log('BOOTSTRAP all three baremetal nodes; R2d2 is excluded from host preparation.')
 child=dict(env,ANSIBLE_BECOME_ASK_PASS='False',ANSIBLE_LOG_PATH=str(work/'all-bootstrap.log'))
 for node,password in passwords.items():child['OFZ_SUDO_'+node]=password
 tools.Runner(log,child).run(bootstrap_command(inventory,config,options,'baremetal'),terminal=True)
 for node in inv.NODES:
  after=inspect(node,'after');compare(before[node],after)
  log('NODE_BOOTSTRAP_READY '+node)
 passwords.clear()
 log('RESULT: HOST_BOOTSTRAP_READY for all')
 log('Host preparation only. Return this report before full Kolla prechecks or deployment.')

def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--apply',action='store_true');p.add_argument('--expected-ca',help='optional pin; normally use the saved Phase 2 CA approval');args=p.parse_args(argv)
 if not args.apply:
  print('PLAN ONLY: validate files and all three nodes; run reviewed Kolla host bootstrap; verify and save one private report.')
  print('Approved standard preparation includes firewall/package changes. Read Phase 3 Chapter A before --apply.');return 0
 validation.config_step.load_step('phase3_r2d2','phase2-check.py').require_r2d2();tools.check_paths(ENV)
 if not sys.stdin.isatty():raise RuntimeError('Use an interactive terminal for key-passphrase and node sudo passwords')
 if Path(sys.prefix)!=ENV:
  exe=str(ENV/'bin/python');os.execve(exe,[exe,str(Path(__file__).resolve()),*sys.argv[1:]],tools.tool_environment())
 os.umask(0o077);reports=REPO/'reports';inv.private_dir(reports)
 stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-')
 fd,name=tempfile.mkstemp(prefix='phase3-bootstrap-'+stamp,suffix='.txt',dir=reports)
 work=Path(tempfile.mkdtemp(prefix='phase3-bootstrap-'+stamp,dir=reports))
 status=1
 with os.fdopen(fd,'w') as output:
  def log(line):print(line,flush=True);output.write(line+'\n');output.flush()
  log('Phase 3 — Chapter A: standard Kolla host preparation — APPLY + VERIFY')
  try:execute(log,work,args.expected_ca);status=0
  except (Exception,KeyboardInterrupt) as e:
   log('RESULT: HOST_BOOTSTRAP_NEEDS_REVIEW')
   log('Stopped: '+str(e) if isinstance(e,RuntimeError) else 'Stopped ('+type(e).__name__+'); details withheld to protect private configuration.')
   log('Earlier nodes/tasks may have changed. No automatic rollback, cleanup, retry or deployment. Return this report.')
  finally:
   for transcript in sorted(work.glob('*-bootstrap.log')):
    log('\nKolla task transcript '+transcript.name+':');log(transcript.read_text())
 print('Private report: '+name);print('Read it with: cat '+shlex.quote(name));return status
if __name__=='__main__':
 try:sys.exit(main())
 except (OSError,RuntimeError) as e:print('Stopped: '+str(e),file=sys.stderr);sys.exit(1)

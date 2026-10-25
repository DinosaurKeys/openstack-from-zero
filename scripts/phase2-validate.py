#!/usr/bin/env python3
"""Chapter F: validate the prepared lab; optionally trust its verified public CA on R2d2."""
import sys
sys.dont_write_bytecode = True
import argparse
import datetime as dt
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

spec = importlib.util.spec_from_file_location('chapter_e', Path(__file__).with_name('phase2-configure.py'))
config_step = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config_step)
inv = config_step.inventory_step
tools = config_step.tools
REPO = Path(__file__).resolve().parents[1]
ENV = Path('/home/joe/venvs/kolla')
CA_FINGERPRINT = '278a4e6a84b761f954b9bbf1f8051c3560b63f0eb073efc9e95d8d1c69df3cbb'
TRUST_TARGET = Path('/usr/local/share/ca-certificates/openstack-from-zero-lab.crt')

def private_file(path):
    if any(p.is_symlink() for p in [path,*path.parents]) or not path.is_file():
        raise RuntimeError('Expected regular private file: '+str(path))
    if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise RuntimeError('Private file ownership/permissions require review: '+str(path))

def local_checks(log, env, expected_ca):
    import yaml
    if importlib.metadata.version('kolla-ansible') != '23.0.0' or importlib.metadata.version('ansible-core') != '2.21.5':
        raise RuntimeError('Expected Kolla-Ansible 23.0.0 and Ansible-core 2.21.5')
    folder = REPO/'private/phase2'
    config = folder/'kolla'
    inventory = folder/'inventory.ini'
    paths = [inventory, config/config_step.MARKER, config/'globals.yml', config/'passwords.yml',config/'lab-network-plan.json']
    paths += [folder/'host_vars'/f'{node}.yml' for node in inv.NODES]
    for path in paths:
        private_file(path)
    if json.loads((config/config_step.MARKER).read_text()) != config_step.MARK:
        raise RuntimeError('Chapter E configuration marker differs')
    if yaml.safe_load((config/'globals.yml').read_text()) != config_step.settings(config):
        raise RuntimeError('Globals differ from the agreed Chapter E settings; contents withheld')
    if json.loads((config/'lab-network-plan.json').read_text()) != config_step.network_plan():
        raise RuntimeError('Network plan differs from Chapter E')
    template_inventory = (ENV/'share/kolla-ansible/ansible/inventory/multinode').read_text()
    if inventory.read_text() != inv.render_inventory(template_inventory):
        raise RuntimeError('Inventory differs from the agreed all-three-node layout; preserved')
    if (folder/'group_vars').exists() or {p.name for p in (folder/'host_vars').iterdir()} != {n+'.yml' for n in inv.NODES}:
        raise RuntimeError('Additional inventory variable files require review; preserved')
    parsed = inv.inventory_data(env, inventory, log)
    inv.validate_inventory(parsed)
    for node, nic in config_step.USB.items():
        if yaml.safe_load((folder/'host_vars'/f'{node}.yml').read_text()) != {'neutron_external_interface':nic} or parsed['_meta']['hostvars'][node].get('neutron_external_interface') != nic:
            raise RuntimeError('USB interface settings differ for '+node)
    template = yaml.safe_load((ENV/'share/kolla-ansible/etc_examples/kolla/passwords.yml').read_text())
    passwords = yaml.safe_load((config/'passwords.yml').read_text())
    if not isinstance(passwords,dict) or set(passwords) != set(template) or any(not passwords[k] for k in template if k != 'docker_registry_password'):
        raise RuntimeError('Service credential completeness requires review; values withheld')
    for path in (config/'certificates').rglob('*'):
        if path.is_file():
            private_file(path)
    fingerprint = config_step.verify_certificates(config/'certificates')
    if fingerprint != expected_ca:
        raise RuntimeError('CA fingerprint differs from the reviewed Chapter E fingerprint; no trust changes made')
    config_step.verify_proxysql_certificates(config/'certificates')
    log('PASS tool versions, all-three-node roles, globals, NIC mapping and credential completeness.')
    log('PASS approved independent-LVM exception only; empty cluster name retained.')
    log('PASS certificate keys, validity, chain and VIP SAN. Public CA SHA256: '+fingerprint)
    run = tools.Runner(log,env)
    run.run(['openssl','verify','-CAfile',config/'certificates/ca/lab-root.crt','-verify_ip',config_step.VIP,config/'certificates/server.crt'])
    run.run([ENV/'bin/ansible-playbook','-i',inventory,ENV/'share/kolla-ansible/ansible/site.yml',
        '-e','@'+str(config/'globals.yml'),'-e','@'+str(config/'passwords.yml'),'--syntax-check'])
    log('PASS Kolla playbook syntax only; no bootstrap, Kolla prechecks or deployment executed.')
    return inventory, config

def node_result(tree, node):
    data = json.loads((tree/node).read_text())
    if data.get('failed') or data.get('unreachable') or data.get('rc',0) != 0:
        raise RuntimeError('Ansible node inspection did not complete: '+node)
    result = json.loads(data['stdout'].strip())
    if result.get('node') != node or not isinstance(result.get('ready'),bool):
        raise RuntimeError('Unexpected node result: '+node)
    return result

def trust_ca(config, log, env):
    ca = config/'certificates/ca/lab-root.crt'
    # Do not overwrite an unrelated CA, follow symlinks or import combined private PEMs.
    if any(p.is_symlink() for p in [TRUST_TARGET,*TRUST_TARGET.parents]):
        raise RuntimeError('CA trust destination must not be symlinked')
    if TRUST_TARGET.exists() and (not TRUST_TARGET.is_file() or TRUST_TARGET.read_bytes() != ca.read_bytes()):
        raise RuntimeError('Existing trust destination differs; preserved without overwrite')
    if not Path('/usr/sbin/update-ca-certificates').is_file():
        raise RuntimeError('Ubuntu CA trust tool is unavailable; no package installation performed')
    run = tools.Runner(log, env)
    log('R2d2 only: sudo may request joe password to trust the verified public lab CA.')
    if not TRUST_TARGET.exists():
        run.run(['sudo','install','-o','root','-g','root','-m','0644',ca,TRUST_TARGET],terminal=True)
    run.run(['sudo','/usr/sbin/update-ca-certificates'],terminal=True)
    run.run(['openssl','verify','-CAfile','/etc/ssl/certs/ca-certificates.crt','-verify_ip',config_step.VIP,config/'certificates/server.crt'])
    log('RESULT: R2D2_CA_TRUST_READY (offline certificate verification; browser trust is separate)')

def execute(log, work, expected_ca, trust):
    env = tools.tool_environment()
    inventory, config = local_checks(log, env, expected_ca)
    inv.ensure_agent(env, log)
    run = tools.Runner(log,env)
    all_ready = True
    for node in inv.NODES:
        tree = work/node
        inv.private_dir(tree)
        log('Inspect '+node+': BECOME password is joe sudo password on this node.')
        args = [ENV/'bin/ansible',node,'-i',inventory,'--become','--ask-become-pass',
            '-m','ansible.builtin.script','-a',shlex.join([str(REPO/'scripts/phase2-node-readiness.py'),'--node',node])+ ' executable=/usr/bin/python3',
            '--tree',tree]
        run.run(args,terminal=True)
        result = node_result(tree,node)
        log(json.dumps(result,indent=2))
        all_ready = all_ready and result['ready']
    if not all_ready:
        raise RuntimeError('One or more node checks require review; no automatic configuration repair')
    log('RESULT: PREPARATION_CHECKS_PASSED')
    if trust:
        trust_ca(config,log,env)
    else:
        log('R2d2 CA trust not requested; no trust-store changes.')
    log('Browser import and live Horizon HTTPS remain separate checkpoints.')
    log('No node settings, disk contents or running services changed. Temporary Ansible helper files were used.')
    log('Return this report for review before Phase 3.')

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true',help='run explained local and node checks; write a private report')
    parser.add_argument('--trust-r2d2',action='store_true',help='after checks pass, add only the verified public CA to R2d2 Ubuntu trust')
    parser.add_argument('--expected-ca',default=CA_FINGERPRINT,help='reviewed Chapter E SHA256 fingerprint; change only for an intentional fresh rebuild')
    args = parser.parse_args(argv)
    if not re.fullmatch('[0-9a-f]{64}',args.expected_ca):
        parser.error('--expected-ca must be 64 lowercase hexadecimal characters')
    if args.trust_r2d2 and not args.run:
        parser.error('--trust-r2d2 also needs --run')
    if not args.run:
        print('PLAN ONLY: verify local settings/certificates, inspect all three nodes, observe VIP conflicts.')
        print('Optional --trust-r2d2 adds the verified public CA to R2d2 only. Read Chapter F before --run.')
        return 0
    config_step.load_step('check_r2d2_f','phase2-check.py').require_r2d2()
    tools.check_paths(ENV)
    if not sys.stdin.isatty():
        raise RuntimeError('Run in an interactive terminal for key-passphrase and per-node sudo prompts')
    if Path(sys.prefix) != ENV:
        exe = str(ENV/'bin/python')
        os.execve(exe,[exe,str(Path(__file__).resolve()),*sys.argv[1:]],tools.tool_environment())
    os.umask(0o077)
    reports = REPO/'reports'
    inv.private_dir(reports)
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-')
    fd,filename = tempfile.mkstemp(prefix='phase2-validation-'+stamp,suffix='.txt',dir=reports)
    work = Path(tempfile.mkdtemp(prefix='phase2-validation-'+stamp,dir=reports))
    with os.fdopen(fd,'w') as output:
        def log(line):
            print(line,flush=True);output.write(line+'\n');output.flush()
        log('Phase 2 — Chapter F: preparation checks'+(' + R2d2 public-CA trust' if args.trust_r2d2 else ''))
        try:
            execute(log,work,args.expected_ca,args.trust_r2d2)
            status = 0
        except Exception as error:
            log('RESULT: PREPARATION_NEEDS_REVIEW')
            log('Stopped: '+str(error) if isinstance(error,RuntimeError) else 'Stopped ('+type(error).__name__+'); details withheld to protect private configuration.')
            log('No automatic repair, file deletion, credential rotation or deployment.')
            status = 1
    print('Private report: '+filename)
    print('Read it with: cat '+shlex.quote(filename))
    return status

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError,RuntimeError) as error:
        print('Stopped: '+str(error),file=sys.stderr);sys.exit(1)

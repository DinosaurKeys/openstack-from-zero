#!/usr/bin/env python3
"""Remove this lab's OpenStack deployment and prepare the three hosts for a fresh HA install. Default: plan."""
import sys
sys.dont_write_bytecode = True
import argparse
import datetime as dt
import fcntl
import getpass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import tempfile

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('reset_deploy', REPO/'scripts/phase3-deploy.py')
deploy = importlib.util.module_from_spec(spec); spec.loader.exec_module(deploy)
ENV = deploy.ENV; inv = deploy.inv; tools = deploy.tools; config_step = deploy.validation.config_step
HA_KEYS = {'enable_haproxy', 'enable_keepalived', 'enable_mariadb', 'enable_neutron_agent_ha',
           'enable_neutron_dvr', 'max_l3_agents_per_router', 'dhcp_agents_per_network'}


def source_guard():
    deploy.source_guard()
    for name, expected in json.loads((REPO/'scripts/phase3-reset-sources.json').read_text()).items():
        path = ENV/name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError('Destroy source differs from reviewed Kolla release: '+name)


def validate_config(config):
    import yaml
    validation = deploy.validation
    for name in ('globals.yml', 'passwords.yml', config_step.MARKER, 'lab-network-plan.json'):
        validation.private_file(config/name)
    if json.loads((config/config_step.MARKER).read_text()) != config_step.MARK:
        raise RuntimeError('Unknown configuration marker; no destruction')
    generated = {'clouds.yaml', 'admin-openrc.sh', 'admin-openrc-system.sh', 'public-openrc.sh', 'public-openrc-system.sh'}
    allowed = {'globals.yml', 'passwords.yml', config_step.MARKER, 'lab-network-plan.json', 'certificates'} | generated
    if {p.name for p in config.iterdir()} - allowed: raise RuntimeError('Additional Kolla override files require review before destruction')
    for name in generated:
        path = config/name
        if path.exists() or path.is_symlink(): validation.private_file(path)
    if (config.parent/'group_vars').exists() or (config.parent/'group_vars').is_symlink():
        raise RuntimeError('Additional inventory group variables require review')
    expected = config_step.settings(config); actual = yaml.safe_load((config/'globals.yml').read_text())
    legacy = {k: v for k, v in expected.items() if k not in HA_KEYS}
    if actual not in (expected, legacy): raise RuntimeError('Unexpected globals; no destruction')
    fingerprint = config_step.verify_certificates(config/'certificates')
    if fingerprint != validation.reviewed_ca(config): raise RuntimeError('Existing CA differs; no destruction or rotation')
    config_step.verify_proxysql_certificates(config/'certificates')
    passwords = yaml.safe_load((config/'passwords.yml').read_text())
    template = yaml.safe_load((ENV/'share/kolla-ansible/etc_examples/kolla/passwords.yml').read_text())
    if not isinstance(passwords, dict) or set(passwords) != set(template) or any(not passwords[k] for k in template if k != 'docker_registry_password'):
        raise RuntimeError('Existing credentials incomplete; no destruction')
    for node, usb in config_step.USB.items():
        path = config.parent/'host_vars'/f'{node}.yml'; validation.private_file(path)
        if yaml.safe_load(path.read_text()) != {'neutron_external_interface': usb}: raise RuntimeError('USB host mapping differs')


def destroy_command(inventory, config, options, node):
    if node not in inv.NODES: raise RuntimeError('Unknown destruction target')
    return [ENV/'bin/kolla-ansible', 'destroy', '--configdir', config, '-i', inventory, '--limit', node,
            '--become', '-e', '@'+str(options), '-e', 'ansible_python_interpreter=/usr/bin/python3', '--yes-i-really-really-mean-it']


def helper_playbook(folder, clean=False):
    command = shlex.join([str(REPO/'scripts/phase3-reset-node.py'), '--node', '{{ inventory_hostname }}'])
    if clean: command += ' --clean'
    return json.dumps([{'name': 'Fresh-install host '+('cleanup' if clean else 'inspection'), 'hosts': 'baremetal', 'gather_facts': False,
        'become': True, 'tasks': [
            {'name': 'Check selected host and reviewed SATA', 'ansible.builtin.script': command+' executable=/usr/bin/python3', 'register': 'result', 'changed_when': clean},
            {'name': 'Save private host result', 'delegate_to': 'localhost', 'become': False,
             'ansible.builtin.copy': {'content': '{{ result.stdout }}', 'dest': str(folder/'{{ inventory_hostname }}.json'), 'mode': '0600'}},
            {'name': 'Require selected host ready', 'ansible.builtin.assert': {'that': ['(result.stdout | from_json).ready']}}]}], indent=2)+'\n'


def save_state(path, value):
    if path.is_symlink(): raise RuntimeError('Reset state must not be a symlink')
    fd, temp = tempfile.mkstemp(prefix='.reset-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out: json.dump(value, out, indent=2); out.flush(); os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def finish_local(config, archive):
    # Archive cloud identities only after every node is clean. Reuse the guest key and verified image cache.
    for name in ('networks.json', 'vm-preparation.json'):
        path = REPO/'private/phase4'/name
        if path.is_symlink(): raise RuntimeError('Cloud state must not be a symlink')
        if path.exists():
            deploy.validation.private_file(path); shutil.move(str(path), str(archive/name))
    for name in ('clouds.yaml', 'admin-openrc.sh', 'admin-openrc-system.sh', 'public-openrc.sh', 'public-openrc-system.sh'):
        path = config/name
        if path.is_symlink(): raise RuntimeError('Generated client file must not be a symlink')
        if path.exists():
            deploy.validation.private_file(path); shutil.move(str(path), str(archive/name))
    destination = config/'globals.yml'
    text = config_step.yaml_text(config_step.settings(config))
    fd, temp = tempfile.mkstemp(prefix='.ha-globals-', dir=config)
    try:
        with os.fdopen(fd, 'w') as out: out.write(text); out.flush(); os.fsync(out.fileno())
        os.replace(temp, destination)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def execute(log, work):
    source_guard(); env = tools.tool_environment()
    inventory = REPO/'private/phase2/inventory.ini'; config = inventory.parent/'kolla'
    deploy.validation.private_file(inventory); validate_config(config)
    inv.validate_inventory(inv.inventory_data(env, inventory, log)); inv.ensure_agent(env, log)
    root = REPO/'private'; inv.private_dir(root)
    receipt = root/'ha-reset.json'; lock = root/'ha-reset.lock'
    if lock.is_symlink(): raise RuntimeError('Reset lock must not be a symlink')
    with open(lock, 'a') as handle:
        os.chmod(lock, 0o600)
        try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError('Another reset is running') from None
        if receipt.exists() or receipt.is_symlink():
            raise RuntimeError('Reset receipt already exists; return the earlier report. No automatic repeat destruction.')
        for name in ('networks.json', 'vm-preparation.json'):
            p = REPO/'private/phase4'/name
            if p.exists(): deploy.validation.private_file(p)
            if p.is_symlink(): raise RuntimeError('Cloud state is symlinked')
        options = work/'options.json'; options.write_text(json.dumps(deploy.OPTIONS)); options.chmod(0o600)
        folders = {}; plays = {}
        for stage in ('before', 'after'):
            folders[stage] = work/stage; inv.private_dir(folders[stage])
            plays[stage] = work/(stage+'.yml'); plays[stage].write_text(helper_playbook(folders[stage], stage == 'after')); plays[stage].chmod(0o600)
            tools.Runner(log, env).run([ENV/'bin/ansible-playbook', '-i', inventory, plays[stage], '--syntax-check'])
        log('DESTRUCTIVE: all OpenStack services/data and Cinder logical volumes on node1/node2/node3 will be removed. Ubuntu, reviewed PV/VGs, credentials and CA are retained.')
        passwords = {n: getpass.getpass('Joe sudo password on '+n+': ') for n in inv.NODES}
        child = dict(env, ANSIBLE_BECOME_ASK_PASS='False')
        for n, value in passwords.items(): child['OFZ_SUDO_'+n] = value
        def inspect(n, stage):
            runenv = dict(child, ANSIBLE_BECOME_PASS=passwords[n])
            tools.Runner(log, runenv).run([ENV/'bin/ansible-playbook', '-i', inventory, plays[stage], '--limit', n, '-e', 'ansible_python_interpreter=/usr/bin/python3'], terminal=True)
            value = json.loads((folders[stage]/(n+'.json')).read_text())
            if value.get('node') != n or value.get('ready') is not True: raise RuntimeError('Host reset observation needs review: '+n)
            return value
        try:
            before = {n: inspect(n, 'before') for n in inv.NODES}
            archive = root/'ha-install-backups'/work.name; inv.private_dir(archive)
            shutil.copytree(config, archive/'kolla')
            for name in ('networks.json', 'vm-preparation.json'):
                p = REPO/'private/phase4'/name
                if p.exists(): shutil.copy2(p, archive/('before-'+name))
            state = {'status': 'started', 'nodes_clean': [], 'report': str(work), 'archive': str(archive)}
            save_state(receipt, state)
            for n in inv.NODES:
                state['pending_node'] = n; save_state(receipt, state)
                log('Removing OpenStack from '+n+' through standard Kolla destroy.')
                tools.Runner(log, child).run(destroy_command(inventory, config, options, n), terminal=True)
                after = inspect(n, 'after')
                if before[n]['protected'] != after['protected']: raise RuntimeError('Protected host/OS state changed on '+n)
                state['nodes_clean'].append(n); state.pop('pending_node'); save_state(receipt, state)
                log(n+': no Kolla containers, cloud namespaces, API VIP or Cinder LVs; reviewed PV/VG and OS state retained.')
            secrets = {str(p.relative_to(config)): hashlib.sha256(p.read_bytes()).hexdigest() for p in config.rglob('*') if p.is_file() and ('certificates' in p.parts or p.name == 'passwords.yml')}
            finish_local(config, archive)
            for name, digest in secrets.items():
                if hashlib.sha256((config/name).read_bytes()).hexdigest() != digest: raise RuntimeError('Existing credential or certificate changed')
            validate_config(config)
            state['status'] = 'complete'; save_state(receipt, state)
            log('RESULT: FRESH_HA_INSTALL_READY')
            log('Return this report. Full prechecks and deployment are separate steps; no reinstall has started.')
        finally:
            passwords.clear()
            for n in inv.NODES: child.pop('OFZ_SUDO_'+n, None)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--apply', action='store_true'); args = p.parse_args(argv)
    if not args.apply:
        print('PLAN ONLY: remove this lab OpenStack deployment/data on all three nodes; empty only reviewed Cinder VGs; retain Ubuntu/PV/VG/SSH/NICs/CA/credentials; prepare HA globals. No connections or files changed.'); return 0
    config_step.load_step('reset_r2d2', 'phase2-check.py').require_r2d2(); tools.check_paths(ENV)
    if not sys.stdin.isatty(): raise RuntimeError('Use an interactive R2d2 terminal')
    if Path(sys.prefix) != ENV:
        os.execve(str(ENV/'bin/python'), [str(ENV/'bin/python'), str(Path(__file__).resolve()), '--apply'], tools.tool_environment())
    os.umask(0o077); reports = REPO/'reports'; inv.private_dir(reports)
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-')
    work = Path(tempfile.mkdtemp(prefix='phase3-reset-'+stamp, dir=reports))
    filename = work.with_suffix('.txt'); print('Private report: '+str(filename), flush=True); status = 1
    with filename.open('x') as out:
        def log(line): print(line, flush=True); out.write(line+'\n'); out.flush()
        try: execute(log, work); status = 0
        except (Exception, KeyboardInterrupt) as error:
            log('RESULT: FRESH_HA_INSTALL_NEEDS_REVIEW')
            log(str(error) if isinstance(error, RuntimeError) else 'Stopped ('+type(error).__name__+'); private details withheld.')
            log('Some nodes may have been cleaned. No automatic retry, deployment or rollback. Return this report; retain reset state.')
    print('Read report: cat '+shlex.quote(str(filename))); return status

if __name__ == '__main__':
    try: sys.exit(main())
    except (RuntimeError, OSError) as error: print('Stopped: '+str(error), file=sys.stderr); sys.exit(1)

#!/usr/bin/env python3
"""Verify an existing installation. Default: plan; --run collects read-only observations."""
import sys
sys.dont_write_bytecode = True
import argparse
import datetime as dt
import getpass
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import tempfile

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('verify_deployment', REPO/'scripts/phase3-deploy.py')
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)
inv, tools, ENV = deploy.inv, deploy.tools, deploy.ENV
EXPECTED = {'wsrep_cluster_status': 'Primary', 'wsrep_cluster_size': '3',
            'wsrep_connected': 'ON', 'wsrep_ready': 'ON',
            'wsrep_local_state_comment': 'Synced'}
STATUS_KEYS = (*EXPECTED, 'wsrep_cluster_state_uuid')
SQL = 'SHOW GLOBAL STATUS WHERE Variable_name IN (' + ','.join("'"+k+"'" for k in STATUS_KEYS) + ')'


def database_command(ip):
    # The released Ubuntu template installs this monitor-user option file.
    # The client reads its existing password internally; no password enters argv/output.
    return ['/usr/bin/timeout', '30', 'docker', 'exec', '--user', 'root', 'mariadb',
            'mariadb', '--defaults-file=/etc/mysql/healthcheck.cnf', '--protocol=tcp',
            '--host='+ip, '--port=3306', '--connect-timeout=5', '--batch',
            '--skip-column-names', '--execute='+SQL]


def playbook(folder):
    command = shlex.join([str(REPO/'scripts/phase3-deploy-node.py'),
                          '--node', '{{ inventory_hostname }}'])+' executable=/usr/bin/python3'
    content = "{{ {'node': inventory_hostname, 'observation': state.stdout | from_json, 'database': {'rc': galera.rc, 'stdout': galera.stdout}} | to_json }}"
    return json.dumps([{'name': 'Read existing OpenStack state only', 'hosts': 'baremetal',
        'gather_facts': False, 'become': True, 'tasks': [
            {'name': 'Read container, network, VIP and host observations',
             'ansible.builtin.script': command, 'register': 'state', 'changed_when': False},
            {'name': 'Read MariaDB Galera status through the existing monitor account',
             'ansible.builtin.command': {'argv': database_command('{{ ansible_host }}')},
             'register': 'galera', 'changed_when': False, 'failed_when': False},
            {'name': 'Save private observations on R2d2', 'delegate_to': 'localhost',
             'become': False, 'ansible.builtin.copy': {'content': content,
                 'dest': str(folder/'{{ inventory_hostname }}.json'), 'mode': '0600'}}
        ]}], indent=2)+'\n'


def database_status(record):
    if record.get('rc') != 0:
        raise RuntimeError('MariaDB status query did not complete; exit '+str(record.get('rc')))
    values = {}
    for line in record.get('stdout', '').splitlines():
        fields = line.split('\t')
        if len(fields) != 2 or fields[0] not in STATUS_KEYS or fields[0] in values:
            raise RuntimeError('Unexpected or duplicate MariaDB status output')
        values[fields[0]] = fields[1]
    if set(values) != set(STATUS_KEYS):
        raise RuntimeError('Incomplete MariaDB cluster status')
    return values


def summarize(records):
    issues, databases, observations = [], {}, {}
    for node in inv.NODES:
        record = records.get(node, {})
        if record.get('node') != node:
            issues.append(node+': missing or mismatched observation'); continue
        observation = record.get('observation', {})
        observations[node] = observation
        checks = observation.get('checks', {})
        if observation.get('node') != node or observation.get('ready') is not True or not checks or not all(v is True for v in checks.values()):
            issues.append(node+': host/container/network observations need review')
        try:
            databases[node] = database_status(record.get('database', {}))
            for key, expected in EXPECTED.items():
                if databases[node][key] != expected:
                    issues.append(node+': '+key+'='+databases[node][key]+' (expected '+expected+')')
        except RuntimeError as error:
            issues.append(node+': '+str(error))
    if len(databases) == 3:
        uuids = {s['wsrep_cluster_state_uuid'] for s in databases.values()}
        if len(uuids) != 1 or not all(re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', u) and int(u.replace('-', ''), 16) for u in uuids):
            issues.append('MariaDB nodes do not report one valid shared cluster state UUID')
    owners = [n for n, s in observations.items() if s.get('facts', {}).get('VIP on management interface') is True]
    if len(owners) != 1:
        issues.append('Expected exactly one observed API VIP owner; found '+str(len(owners)))
    return {'passed': not issues, 'issues': issues, 'database_status': databases, 'vip_owners': owners}


def execute(log, work):
    deploy.validation.config_step.load_step('verify_r2d2', 'phase2-check.py').require_r2d2()
    tools.check_paths(ENV)
    deploy.source_guard()
    env = tools.tool_environment()
    inventory = REPO/'private/phase2/inventory.ini'
    deploy.validation.private_file(inventory)
    inv.validate_inventory(inv.inventory_data(env, inventory, log))
    inv.ensure_agent(env, log)
    folder = work/'observations'; inv.private_dir(folder)
    play = work/'verify.yml'; play.write_text(playbook(folder)); play.chmod(0o600)
    tools.Runner(log, env).run([ENV/'bin/ansible-playbook', '-i', inventory, play, '--syntax-check'])
    records = {}
    for node in inv.NODES:
        log('Checking '+node+'; enter joe sudo password for this node. Typing is hidden.')
        password = getpass.getpass('Joe sudo password on '+node+': ')
        child = dict(env, ANSIBLE_BECOME_PASS=password, ANSIBLE_BECOME_ASK_PASS='False')
        try:
            tools.Runner(log, child).run([ENV/'bin/ansible-playbook', '-i', inventory, play,
                '--limit', node, '-e', 'ansible_python_interpreter=/usr/bin/python3'], terminal=True)
            records[node] = json.loads((folder/(node+'.json')).read_text())
            log(node+' observations:\n'+json.dumps(records[node], indent=2))
        except (RuntimeError, OSError, ValueError):
            log(node+': observation could not be completed; continuing to the next node.')
        finally:
            child.pop('ANSIBLE_BECOME_PASS', None); password = None
    summary = summarize(records)
    log('Verification summary:\n'+json.dumps(summary, indent=2))
    log('RESULT: '+('OPENSTACK_OBSERVATIONS_PASSED for all' if summary['passed'] else 'OPENSTACK_VERIFICATION_NEEDS_REVIEW'))
    log('Snapshot checks only. API login, VM/volume operation and failover are not tested.')
    return 0 if summary['passed'] else 1


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', action='store_true'); args = p.parse_args(argv)
    if not args.run:
        print('PLAN ONLY: read container states, MariaDB cluster status, VIP and USB bridges on all three nodes; save one private report.')
        print('No deployment, restart, recovery, cleanup or settings change. Use --run on R2d2.'); return 0
    if not sys.stdin.isatty():
        raise RuntimeError('Use an interactive R2d2 terminal for SSH-key and sudo prompts')
    # System Python loads the management tools via the existing environment.
    if Path(sys.prefix) != ENV:
        deploy.validation.config_step.load_step('verify_r2d2', 'phase2-check.py').require_r2d2()
        tools.check_paths(ENV)
        os.execve(str(ENV/'bin/python'), [str(ENV/'bin/python'), str(Path(__file__).resolve()), '--run'], tools.tool_environment())
    os.umask(0o077)
    reports = REPO/'reports'; inv.private_dir(reports)
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-')
    fd, name = tempfile.mkstemp(prefix='phase3-verify-'+stamp, suffix='.txt', dir=reports)
    work = Path(tempfile.mkdtemp(prefix='phase3-verify-'+stamp, dir=reports))
    print('Private report: '+name, flush=True)
    status = 1
    with os.fdopen(fd, 'w') as output:
        def log(line):
            print(line, flush=True); output.write(line+'\n'); output.flush()
        log('Existing OpenStack installation: observation only; no install or repair actions.')
        try:
            status = execute(log, work)
        except (Exception, KeyboardInterrupt) as error:
            log('RESULT: OPENSTACK_VERIFICATION_NEEDS_REVIEW')
            log(str(error) if isinstance(error, RuntimeError) else 'Verification interrupted; details withheld.')
    print('Read report: cat '+shlex.quote(name))
    print('Short summary: tail -n 65 '+shlex.quote(name))
    return status


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, OSError) as error:
        print('Stopped: '+str(error), file=sys.stderr); sys.exit(1)

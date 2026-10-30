#!/usr/bin/env python3
"""Disable faulty Nova reply-queue name reuse; six reviewed service restarts only."""
import sys
sys.dont_write_bytecode = True
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import fcntl
import getpass
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, REPO/'scripts'/file)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


base = load('messaging_base', 'lab-recover-api.py')
node_helper = load('messaging_node', 'lab-nova-messaging-node.py')
health = base.health
deploy = health.deploy
inv = health.inv
ENV = health.ENV
SERVICES = node_helper.SERVICES
RECEIPT = node_helper.RECEIPT
SCOPE = node_helper.SCOPE
FINISHED = node_helper.FINISHED


class Nodes:
    def __init__(self, env, passwords):
        self.env, self.passwords = env, passwords

    def call(self, node, action='inspect', service=None, expected=None, cluster=None):
        if node not in inv.NODES or action not in ('inspect', 'apply') or (action == 'apply' and service not in SERVICES):
            raise RuntimeError('Unapproved messaging call')
        sources = {key: base64.b64encode((REPO/'scripts'/file).read_bytes()).decode()
                   for key, file in [('HEALTH_SOURCE', 'lab-health-node.py'), ('UNIT_SOURCE', 'lab-recover-api-node.py')]}
        sources['PAYLOAD'] = base64.b64encode(json.dumps(dict(node=node, action=action, service=service, expected=expected, cluster=cluster)).encode()).decode()
        payload = base64.b64encode((REPO/'scripts/lab-nova-messaging-node.py').read_bytes()).decode()
        code = 'import base64;exec(compile(base64.b64decode('+repr(payload)+'),"nova-messaging-node","exec"),'+repr(dict(sources, __name__='__main__'))+')'
        command = shlex.join(['sudo', '-S', '-p', '', '/usr/bin/python3', '-c', code])
        try:
            proc = subprocess.run(health.vip.ssh_args(node)+[command], input=self.passwords[node]+'\n',
                                  capture_output=True, text=True, env=self.env, timeout=300)
            result = json.loads(proc.stdout)
            if proc.returncode or result.get('node') != node or result.get('passed') is not True:
                reason = health.transport_reason(proc.returncode, proc.stderr) if not proc.stdout.strip() else result.get('error', 'Node response incomplete')
                raise RuntimeError(node+': '+reason)
            return result
        except (OSError, ValueError, subprocess.TimeoutExpired):
            raise RuntimeError(node+': SSH/sudo or operation outcome unavailable; no automatic retry') from None

    def all(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs = {n: pool.submit(self.call, n) for n in inv.NODES}
            return {n: job.result() for n, job in jobs.items()}


def local_plan(config):
    """Kolla's pinned default is CONFIG_DIR/config; globals/inventory are validated."""
    if (config/'globals.d').exists() or (config/'globals.d').is_symlink():
        raise RuntimeError('Additional globals.d files require configuration review')
    root = config/'config'
    result = {}
    for service in SERVICES:
        path = root/'nova'/(service.replace('_', '-')+'.conf')
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise RuntimeError('Symlinked Kolla override rejected')
        raw = b''
        if path.exists():
            deploy.validation.private_file(path); raw = path.read_bytes()
        wanted = node_helper.patched(raw, missing=True)
        result[str(path)] = {'before': node_helper.digest(raw) if path.exists() else None,
                             'after': node_helper.digest(wanted), 'content': wanted}
        # Host overrides merge later. Never promise persistence if they conflict.
        for node in inv.NODES:
            for later in (root/'nova'/node/'nova.conf', root/'nova'/node/(service.replace('_', '-')+'.conf')):
                if later.exists() or later.is_symlink():
                    deploy.validation.private_file(later)
                    content = later.read_bytes()
                    if node_helper.patched(content, missing=True) != content:
                        # Unrelated settings are allowed; an absent Boolean is harmless.
                        import configparser
                        cfg = configparser.ConfigParser(interpolation=None)
                        cfg.read_string(content.decode())
                        if cfg.has_option(node_helper.SECTION, node_helper.OPTION):
                            raise RuntimeError('Higher-precedence queue-manager override requires review: '+str(later.relative_to(config)))
    return result


def save_overrides(plan):
    for name, change in plan.items():
        path = Path(name)
        inv.private_dir(path.parent.parent); inv.private_dir(path.parent)
        if change['before'] is None:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as out:
                out.write(change['content']); out.flush(); os.fsync(out.fileno())
        else:
            node_helper.replace(path, change['content'], change['before'], os.getuid())
        deploy.validation.private_file(path)
        if node_helper.digest(path.read_bytes()) != change['after']:
            raise RuntimeError('Persistent Kolla override verification failed')


def validate(records):
    if set(records) != set(inv.NODES):
        raise RuntimeError('All three nodes must be observed')
    rows, details = [], {}
    health.host_rows({n: r['host'] for n, r in records.items()}, rows, details)
    bad = [r['check'] for r in rows if r.get('status') != 'PASS']
    if bad:
        raise RuntimeError('Cluster prerequisites need review: '+', '.join(bad))
    for node, record in records.items():
        if record.get('node') != node or set(record.get('targets', {})) != set(SERVICES):
            raise RuntimeError('Incomplete messaging targets')
    return records['node1']['host']['galera']['value']['wsrep_cluster_state_uuid']


def workload(api):
    """GET snapshots only. ERROR is retained; active/unknown operations block repair."""
    servers = api.list('compute', 'servers/detail?limit=100', 'servers')
    values = []
    for server in servers:
        if 'OS-EXT-STS:task_state' not in server or server['OS-EXT-STS:task_state'] is not None:
            raise RuntimeError('VM operation active or unknown; no Nova restart')
        for row in api.list('compute', 'os-migrations?instance_uuid='+server['id'], 'migrations'):
            if row.get('status') not in ('error', 'failed', 'completed', 'confirmed', 'reverted', 'cancelled'):
                raise RuntimeError('Migration active or unknown; no Nova restart')
        values.append({k: server.get(k) for k in ('id', 'status', 'OS-EXT-SRV-ATTR:host', 'OS-EXT-STS:task_state',
                                                'addresses', 'os-extended-volumes:volumes_attached')})
    volumes = api.list('block-storage', 'volumes/detail?limit=100', 'volumes')
    disks = [{k: v.get(k) for k in ('id', 'status', 'size', 'os-vol-host-attr:host', 'attachments', 'migration_status')} for v in volumes]
    if any(v.get('status') not in ('available', 'in-use') or (v.get('migration_status') or v.get('os-vol-mig-status-attr:migstat')) not in (None, 'success') for v in volumes):
        raise RuntimeError('Volume operation active or unknown; no Nova restart')
    return {'servers': sorted(values, key=lambda v: v['id']), 'volumes': sorted(disks, key=lambda v: v['id'])}


def checkpoint_hashes():
    result = {}
    for chapter in ('phase4', 'phase5'):
        for path in sorted((REPO/'private'/chapter).glob('*.json')):
            if path.name == RECEIPT and chapter == 'phase5':
                continue
            deploy.validation.private_file(path)
            result[str(path.relative_to(REPO))] = node_helper.digest(path.read_bytes())
    return result


def heartbeat(api, node, service, started):
    binary = service.replace('_', '-')
    deadline = time.monotonic()+65
    start = dt.datetime.fromisoformat(started.replace('Z', '+00:00'))
    while True:
        rows = [r for r in api.list('compute', 'os-services', 'services')
                if str(r.get('host', '')).split('.')[0] == node and r.get('binary') == binary]
        if len(rows) == 1 and rows[0].get('state') == 'up' and rows[0].get('status') == 'enabled':
            updated = rows[0].get('updated_at')
            if isinstance(updated, str):
                stamp = dt.datetime.fromisoformat(updated.replace('Z', '+00:00'))
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=dt.timezone.utc)
                if stamp >= start:
                    return True
        if time.monotonic() >= deadline:
            raise RuntimeError(node+'/'+service+': fresh Nova heartbeat not confirmed after restart')
        time.sleep(2)


def input_transition(state, before, after):
    """Authorize only these two exact config additions/changes, never other drift."""
    if state.get('scope') != SCOPE or state.get('pending') is not False or state.get('stage') != FINISHED:
        return False
    if state.get('before_inputs') != before or state.get('after_inputs') != after or not isinstance(before, dict) or not isinstance(after, dict):
        return False
    changes = state.get('overrides', {})
    expected = {str(REPO/'private/phase2/kolla/config/nova'/(s.replace('_', '-')+'.conf')) for s in SERVICES}
    if set(changes) != expected:
        return False
    for path, change in changes.items():
        if change.get('before') != before.get(path) or change.get('after') != after.get(path):
            return False
    if {k: v for k, v in before.items() if k not in changes} != {k: v for k, v in after.items() if k not in changes}:
        return False
    completed = state.get('completed', [])
    return (len(completed) == 6 and {(v.get('node'), v.get('service')) for v in completed} ==
            {(n, s) for n in inv.NODES for s in SERVICES} and all(v.get('heartbeat') is True for v in completed))


def workflow(nodes, records, plan, state, save, log, unchanged, inputs, api, verify_final):
    cluster = validate(records)
    targets = [(n, s) for n in inv.NODES for s in SERVICES if records[n]['targets'][s]['installed']['use_queue_manager'] is True]
    if len(targets) != 6:
        raise RuntimeError('Require all six reviewed settings ON for first apply; partial/already changed state preserved. Use --check.')
    unchanged(); state.update(pending=True, stage='persistent Kolla override update pending'); save(state)
    save_overrides(plan)
    state['after_inputs'] = inputs()
    expected_inputs = dict(state['before_inputs'])
    expected_inputs.update({path: change['after'] for path, change in plan.items()})
    if state['after_inputs'] != expected_inputs:
        raise RuntimeError('Configuration changed outside the two approved Kolla overrides')
    state.update(stage='persistent Kolla overrides saved; service updates pending'); save(state)
    for node, service in targets:
        unchanged()
        if workload(api) != state['workload_before']:
            raise RuntimeError('VM/volume API identity or state changed; stop before next restart')
        state.update(pending=True, stage='one config update/restart outcome pending', current={'node': node, 'service': service}); save(state)
        log('APPLY '+node+'/'+service+' — disable queue manager; restart once…')
        result = nodes.call(node, 'apply', service, records[node]['targets'][service], cluster)
        target = result['target']
        if target['installed']['use_queue_manager'] is not False:
            raise RuntimeError('Container setting is not OFF')
        heartbeat(api, node, service, target['started'])
        state['completed'].append({'node': node, 'service': service, 'target': target, 'heartbeat': True})
        state.update(stage='one service verified; remaining work pending'); save(state)
        log('PASS  '+node+'/'+service+' | queue manager OFF | healthy | fresh heartbeat')
    final = nodes.all()
    if validate(final) != cluster or any(t['installed']['use_queue_manager'] is not False for r in final.values() for t in r['targets'].values()):
        raise RuntimeError('Final cluster/setting observations differ')
    verify_final()
    unchanged()
    state.update(pending=False, stage=FINISHED); state.pop('current', None)
    if not input_transition(state, state['before_inputs'], inputs()):
        state['pending'] = True
        raise RuntimeError('Approved input transition not verified')
    save(state)
    log('PASS  Persistent Kolla overrides saved; VM/volume state and earlier records preserved')
    log('RESULT: NOVA_MESSAGING_WORKAROUND_VERIFIED')
    log('The faulty queue manager is OFF in all six services. Package version remains 18.3.0. A live migration has not been tested by this repair.')


def execute(apply, log, read=input):
    deploy.source_guard(); env = deploy.tools.tool_environment()
    inventory, config = deploy.validation.local_checks(lambda _: None, env)
    folder = REPO/'private/phase5'; inv.private_dir(folder)
    locks = []
    passwords = {}
    try:
        for path in [folder/n for n in ('migration.lock', 'api-recovery.lock', 'shutdown.lock', 'api-vip.lock', 'mariadb.lock', 'router.lock')] + [REPO/'private/phase4'/n for n in ('vm.lock', 'volume.lock', 'networks.lock')]:
            if path.is_symlink():
                raise RuntimeError('Symlinked operation lock')
            handle = path.open('a'); locks.append(handle); os.chmod(path, 0o600)
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('Another lab operation is running') from None
        receipt = folder/RECEIPT
        if apply and (receipt.exists() or receipt.is_symlink()):
            raise RuntimeError('A messaging repair receipt already exists; use --check. No repeated apply or receipt removal.')
        for name in ('api-vip-state.json', 'mariadb-state.json', 'router-state.json'):
            path = folder/name
            if path.exists() and health.read_private(path).get('restore_required'):
                raise RuntimeError('An HA exercise still requires restoration')
        plan = local_plan(config)
        before = health.access.input_hashes(config, inventory)
        checkpoints = checkpoint_hashes()
        inv.ensure_agent(env, log)
        for node in inv.NODES:
            passwords[node] = getpass.getpass('Joe sudo password on '+node+' (hidden): ')
        nodes = Nodes(env, passwords)
        log('Checking three hosts and the six reviewed Nova services…')
        observed = nodes.all(); cluster = validate(observed)
        original, project, _ = health.prep.authenticate(config)
        api = health.ReadAPI(original.client, original.urls, original.token)
        baseline = workload(api)
        def api_check():
            rows, details = [], {}
            health.api_checks(rows, details, env, include_workload=False)
            bad = [r['check'] for r in rows if r.get('status') != 'PASS']
            if bad:
                raise RuntimeError('API/service prerequisite checks need review: '+', '.join(bad))
        api_check()
        log('Node  | Scheduler queue manager | Conductor queue manager')
        for node, record in observed.items():
            values = ['ON' if record['targets'][s]['installed']['use_queue_manager'] else 'OFF' for s in SERVICES]
            log(node+' | '+' | '.join(values))
        log('Scope: two persistent Kolla overrides; restart scheduler/conductor on three nodes, one service at a time.')
        log('Keep other Horizon/CLI cloud changes paused until this repair finishes.')
        log('VMs, volumes, databases, RabbitMQ queues, timeouts and pending migration records are preserved.')
        if not apply:
            off = all(not t['installed']['use_queue_manager'] for r in observed.values() for t in r['targets'].values())
            log('RESULT: '+('NOVA_MESSAGING_SETTING_OFF' if off else 'NOVA_MESSAGING_REPAIR_PLAN'))
            if receipt.exists():
                state = health.read_private(receipt)
                log('Saved repair: '+str(state.get('stage'))+' | pending='+str(state.get('pending')))
            return 0
        if read('Type APPLY NOVA MESSAGING FIX to approve, or Enter to cancel: ').strip() != 'APPLY NOVA MESSAGING FIX':
            log('Cancelled; configuration and services unchanged.'); return 0
        state = {'version': 1, 'scope': SCOPE, 'pending': False, 'stage': 'reviewed', 'cluster_uuid': cluster,
                 'before_inputs': before, 'workload_before': baseline, 'completed': [],
                 'overrides': {p: {k: v for k, v in value.items() if k != 'content'} for p, value in plan.items()}}
        def inputs():
            return health.access.input_hashes(config, inventory)
        def unchanged():
            if checkpoint_hashes() != checkpoints or inputs() != state.get('after_inputs', before):
                raise RuntimeError('Earlier records or other configuration inputs changed')
        def save(value):
            health.prep.net.write_state(receipt, value)
        def final():
            api_check()
            if workload(api) != baseline:
                raise RuntimeError('VM/volume API state changed; review separately')
        # Second complete snapshot before the first mutation catches review-time changes.
        fresh = nodes.all()
        if validate(fresh) != cluster or any(fresh[n]['targets'] != observed[n]['targets'] or
               fresh[n]['host']['host']['value']['boot_id'] != observed[n]['host']['host']['value']['boot_id'] for n in inv.NODES):
            raise RuntimeError('Host/service observations changed during review; no change sent')
        workflow(nodes, observed, plan, state, save, log, unchanged, inputs, api, final)
        return 0
    finally:
        passwords.clear()
        for handle in reversed(locks):
            handle.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--check', action='store_true', help='read settings and health; no repair')
    group.add_argument('--apply', action='store_true', help='review and approve the six-service workaround')
    args = parser.parse_args(argv)
    if not (args.check or args.apply):
        print('PLAN ONLY: --apply checks, explains and asks before disabling faulty Nova queue-name reuse and restarting six services. --check is read-only. No nodes contacted.'); return 0
    deploy.validation.config_step.load_step('messaging_r2d2', 'phase2-check.py').require_r2d2()
    deploy.tools.check_paths(ENV)
    if not sys.stdin.isatty():
        raise RuntimeError('Run in joe\'s interactive R2d2 terminal')
    if Path(sys.prefix) != ENV:
        os.execve(str(ENV/'bin/python'), [str(ENV/'bin/python'), str(Path(__file__).resolve()), '--apply' if args.apply else '--check'], deploy.tools.tool_environment())
    os.umask(0o077); reports = REPO/'reports'; inv.private_dir(reports)
    fd, name = tempfile.mkstemp(prefix='nova-messaging-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), suffix='.txt', dir=reports)
    print('Private summary: '+name, flush=True)
    status = 1
    with os.fdopen(fd, 'w') as output:
        def log(line):
            print(line, flush=True); output.write(line+'\n'); output.flush()
        try:
            status = execute(args.apply, log)
        except (Exception, KeyboardInterrupt) as error:
            log('RESULT: NOVA_MESSAGING_REPAIR_NEEDS_REVIEW')
            log(str(error) if isinstance(error, RuntimeError) else type(error).__name__+'; collection or repair incomplete')
            log('Some approved changes may have completed. Keep the receipt; no retry, rollback, VM reset or migration was requested.')
    print('Read short summary: cat '+shlex.quote(name))
    return status


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, OSError) as error:
        print('Stopped: '+str(error) if isinstance(error, RuntimeError) else 'Stopped: local file/tool unavailable', file=sys.stderr)
        sys.exit(1)

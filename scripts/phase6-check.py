#!/usr/bin/env python3
"""Chapter A: read readiness for lab1/lab2; never install, stop or create."""
import sys
sys.dont_write_bytecode = True
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
import datetime as dt
import fcntl
import getpass
import hashlib
import importlib.util
import ipaddress
import json
import math
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import tempfile
import uuid

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('phase6_health', REPO / 'scripts/lab-health.py')
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)
prep = health.prep
deploy = health.deploy
inv = health.inv
ENV = health.ENV
TARGET = {'names': ['lab1', 'lab2'], 'vcpus_each': 1, 'ram_mb_each': 2048,
          'root_gib_each': 10, 'volume_name': 'lab2-data', 'volume_gib': 5}


class EvidenceError(RuntimeError):
    """A fixed, non-secret explanation of missing or mismatched evidence."""


def reason(error):
    return clean(str(error)) if isinstance(error, EvidenceError) else health.api_error_reason(error)


class ReadAPI(prep.API):
    def write(self, *args, **kwargs):
        raise RuntimeError('Chapter A permits no cloud resource changes')


def clean(value):
    return re.sub(r'[\x00-\x1f\x7f]', '?', str(value))[:180]


def r2d2_info():
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    distro = release.get('ID', '').strip('"')
    version = release.get('VERSION_ID', '').strip('"')
    machine = platform.machine()
    space = shutil.disk_usage(REPO).free
    binary = shutil.which('terraform')
    installed = None
    if binary:
        p = subprocess.run([binary, 'version', '-json'], text=True, capture_output=True, timeout=15)
        if p.returncode:
            raise EvidenceError('Existing Terraform version could not be read; nothing replaced')
        installed = json.loads(p.stdout).get('terraform_version')
        if not isinstance(installed, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][a-zA-Z0-9.-]+)?', installed):
            raise EvidenceError('Existing Terraform version response needs review')
    return distro == 'ubuntu' and version == '24.04' and machine in ('x86_64', 'aarch64') and space >= 1024**3, {
        'os': distro, 'version': version, 'architecture': machine, 'free_disk_gib': round(space / 1024**3, 2),
        'terraform_version': installed,
        'message': distro + ' ' + version + ' / ' + machine + '; free disk ' + str(round(space / 1024**3, 2)) + ' GiB; Terraform ' + (installed or 'not installed yet')}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-fA-F0-9]{32}|[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}', value):
        raise ValueError('Invalid resource identity')
    return value


def protected():
    values = {}
    for phase in ('phase4', 'phase5'):
        for path in sorted((REPO / 'private' / phase).glob('*.json')):
            deploy.validation.private_file(path)
            values[str(path.relative_to(REPO))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return values


def bound_state(name, api, project, user):
    state = health.read_private(REPO / 'private/phase4' / name)
    if state.get('project_id') != project:
        raise EvidenceError('Existing exercise project differs')
    if name == 'networks.json':
        if state.get('api') != api.urls['network'] or state.get('plan') != prep.net.PLAN:
            raise EvidenceError('Existing network identity or plan differs')
    elif state.get('user_id') != user or state.get('apis') != api.urls:
        raise EvidenceError('Existing exercise API identity differs')
    if state.get('pending') or state.get('restore_required'):
        raise EvidenceError('Existing exercise has an unresolved operation; records preserved')
    return state


def quota_row(label, value, needed, used_key='in_use'):
    if not isinstance(value, dict) or any(type(value.get(k)) is not int for k in ('limit', used_key, 'reserved')):
        raise EvidenceError(label + ' quota evidence is incomplete')
    limit, used, reserved = value['limit'], value[used_key], value['reserved']
    if limit < -1 or min(used, reserved) < 0:
        raise EvidenceError(label + ' quota evidence is invalid')
    remaining = None if limit == -1 else limit - used - reserved
    return {'item': label, 'needed': needed, 'remaining': remaining,
            'used': used, 'reserved': reserved, 'limit': limit,
            'ok': remaining is None or remaining >= needed}


def image_info(api, state, project):
    value = api.get('image', 'v2/images/' + identifier(state['ids']['image']))
    if value.get('id') != state['ids']['image'] or value.get('owner') != project or value.get('name') != prep.PLAN['image_name']:
        raise EvidenceError('Tracked image identity differs')
    if value.get('status') != 'active' or value.get('disk_format') != 'qcow2':
        raise EvidenceError('Tracked image is not active QCOW2')
    if any(type(value.get(k)) is not int or value[k] < 0 for k in ('min_ram', 'min_disk')):
        raise EvidenceError('Image boot minimums are unavailable')
    return {k: value[k] for k in ('id', 'name', 'min_ram', 'min_disk')}


def available_addresses(subnet, ports):
    network = ipaddress.ip_network(subnet['cidr'], strict=True)
    if network.version != 4:
        raise EvidenceError('Expected IPv4 subnet')
    addresses = set()
    for pool in subnet['allocation_pools']:
        start, end = ipaddress.ip_address(pool['start']), ipaddress.ip_address(pool['end'])
        if start not in network or end not in network or int(end) < int(start) or int(end) - int(start) > 65535:
            raise EvidenceError('Unreviewed allocation pool')
        addresses.update(range(int(start), int(end) + 1))
    for port in ports:
        for fixed in port.get('fixed_ips', []):
            if fixed.get('subnet_id') == subnet['id']:
                addresses.discard(int(ipaddress.ip_address(fixed['ip_address'])))
    return len(addresses)


def network_info(api, state, project):
    ids = state['ids']
    neutron = prep.net.Neutron(api.client, api.urls['network'], api.token)
    prep.net.preflight(neutron, ids, project)
    counts = {}
    for kind in ('private', 'external'):
        net_id = identifier(ids[kind + '_network'])
        subnet = api.get('network', 'v2.0/subnets/' + identifier(ids[kind + '_subnet']))['subnet']
        ports = api.list('network', 'v2.0/ports?network_id=' + net_id, 'ports')
        if subnet.get('id') != ids[kind + '_subnet'] or subnet.get('network_id') != net_id:
            raise EvidenceError('Subnet identity differs')
        counts[kind] = available_addresses(subnet, ports)
    return {'ids': ids, 'free_private_addresses': counts['private'],
            'free_external_addresses': counts['external']}


def inventory_remaining(inventory, used, request):
    if not isinstance(inventory, dict) or type(used) is not int or used < 0:
        raise EvidenceError('Placement usage is unavailable')
    for key in ('total', 'reserved', 'min_unit', 'max_unit', 'step_size'):
        if type(inventory.get(key)) is not int:
            raise EvidenceError('Placement inventory is incomplete')
    total, reserved = inventory['total'], inventory['reserved']
    ratio = inventory.get('allocation_ratio')
    if total < 0 or not 0 <= reserved <= total or type(ratio) not in (int, float) or not math.isfinite(ratio) or ratio <= 0:
        raise EvidenceError('Placement capacity is invalid')
    if inventory['step_size'] <= 0 or not 0 < inventory['min_unit'] <= inventory['max_unit']:
        raise EvidenceError('Placement allocation units are invalid')
    if not inventory['min_unit'] <= request <= inventory['max_unit'] or request % inventory['step_size']:
        return 0
    return max(0, math.floor(((total - reserved) * ratio - used) / request))


def capacity_info(api, hosts, ram_mb):
    providers = api.list('placement', 'resource_providers', 'resource_providers')
    rows = []
    for node in inv.NODES:
        h = hosts[node]
        if h.get('node') != node or h.get('observed') is not True:
            raise EvidenceError('Physical capacity unavailable on ' + node + ': ' + clean(h.get('error', 'read incomplete')))
        selected = [p for p in providers if p.get('name', '').split('.')[0] == node]
        if len(selected) != 1:
            raise EvidenceError('Expected one Placement provider for ' + node)
        path = 'resource_providers/' + identifier(selected[0]['uuid'])
        stocks = api.get('placement', path + '/inventories')
        usage = api.get('placement', path + '/usages')
        generations = [v.get('resource_provider_generation') for v in (stocks, usage)]
        if any(type(g) is not int for g in generations) or generations[0] != generations[1]:
            raise EvidenceError('Placement changed while reading ' + node)
        slots = []
        for kind, request in (('VCPU', TARGET['vcpus_each']), ('MEMORY_MB', ram_mb), ('DISK_GB', TARGET['root_gib_each'])):
            slots.append(inventory_remaining(stocks['inventories'].get(kind), usage['usages'].get(kind), request))
        ram = h['memory']['MemAvailable']
        disk = h['instance_disk_available_bytes']
        if type(ram) is not int or type(disk) is not int or min(ram, disk) < 0:
            raise EvidenceError('Physical free resources are invalid')
        # Explicit lab review margins, not reservations or a scheduling guarantee.
        slots += [max(0, (ram - 1024**3) // (ram_mb * 1024**2)),
                  max(0, (disk - 2 * 1024**3) // (TARGET['root_gib_each'] * 1024**3))]
        rows.append({'node': node, 'cpu_threads': h['cpu_threads'], 'available_ram_gib': round(ram / 1024**3, 2),
                     'available_instance_disk_gib': round(disk / 1024**3, 2), 'review_slots': min(slots)})
    return rows


def storage_info(api, project):
    vtype = api.get('block-storage', 'types/default')['volume_type']
    identifier(vtype.get('id'))
    if not isinstance(vtype.get('name'), str) or not vtype['name'] or vtype.get('extra_specs', {}) not in ({}, {'volume_backend_name': 'lvm-1'}):
        raise EvidenceError('Default Cinder volume type needs review')
    pools = api.get('block-storage', 'scheduler-stats/get_pools?detail=true')['pools']
    rows = []
    for p in pools:
        name, caps = p.get('name', ''), p.get('capabilities', {})
        if not re.fullmatch(r'node[123]@lvm-1#lvm-1', name) or caps.get('volume_backend_name') != 'lvm-1' or str(caps.get('storage_protocol', '')).lower() != 'iscsi':
            raise EvidenceError('Storage pool differs from the course LVM design')
        free = caps.get('free_capacity_gb')
        if type(free) not in (int, float) or not math.isfinite(free) or free < 0:
            raise EvidenceError('Cinder free space is unavailable')
        rows.append({'backend': name, 'free_gib': free})
    if len(rows) != 3 or {r['backend'].split('@')[0] for r in rows} != set(inv.NODES):
        raise EvidenceError('Expected three different LVM pools')
    quota = api.get('block-storage', 'os-quota-sets/' + project + '?usage=True')['quota_set']
    qrows = [quota_row(label, quota.get(key), count) for label, key, count in
             [('Volumes', 'volumes', 1), ('Volume GiB', 'gigabytes', TARGET['volume_gib']),
              ('Type volumes', 'volumes_' + vtype['name'], 1),
              ('Type GiB', 'gigabytes_' + vtype['name'], TARGET['volume_gib'])]]
    limit = quota.get('per_volume_gigabytes', {}).get('limit')
    if type(limit) is not int or limit < -1:
        raise EvidenceError('Per-volume size quota is unavailable')
    return {'type_id': vtype['id'], 'type_name': vtype['name'], 'pools': rows, 'quotas': qrows,
            'fits': any(p['free_gib'] >= TARGET['volume_gib'] for p in rows)
                    and all(q['ok'] for q in qrows) and (limit == -1 or limit >= TARGET['volume_gib'])}


def capacity_observe(node, password, env):
    encoded = base64.b64encode((REPO / 'scripts/phase6-capacity-node.py').read_bytes()).decode()
    code = 'import base64;exec(compile(base64.b64decode(' + repr(encoded) + '),"phase6-capacity","exec"),{"__name__":"__main__"})'
    command = shlex.join(['sudo', '-S', '-p', '', '/usr/bin/python3', '-c', code, '--node', node])
    try:
        p = subprocess.run(health.vip.ssh_args(node) + [command], input=password + '\n', text=True, capture_output=True, env=env, timeout=150)
        value = json.loads(p.stdout)
        if not isinstance(value, dict) or value.get('node') != node or type(value.get('observed')) is not bool or (p.returncode and value['observed']):
            raise ValueError()
        return value
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {'node': node, 'observed': False, 'error': 'Physical capacity query unavailable; no change requested'}


def collect_cloud(api, project, user, hosts, check, details):
    identifier(project)
    details['project_id'] = project
    prepared = bound_state('vm-preparation.json', api, project, user)
    network = bound_state('networks.json', api, project, user)
    old = bound_state('vm.json', api, project, user)
    image = check('Existing Ubuntu image', lambda: (True, image_info(api, prepared, project)))
    ram = max(TARGET['ram_mb_each'], math.ceil(image['min_ram'] / 1024) * 1024) if image else TARGET['ram_mb_each']
    details['assessed_ram_mb_each'] = ram
    if image:
        check('Approved size meets image minimum', lambda: (image['min_ram'] <= TARGET['ram_mb_each'] and image['min_disk'] <= TARGET['root_gib_each'],
              {'message': 'Approved RAM ' + str(TARGET['ram_mb_each']) + ' MiB; image minimum ' + str(image['min_ram']) + ' MiB; root minimum ' + str(image['min_disk']) + ' GiB'}))
    else:
        check('Approved size meets image minimum', lambda: (_ for _ in ()).throw(ValueError('Image minimum unavailable')))
    def key():
        deploy.validation.private_file(prep.KEY)
        public_path = Path(str(prep.KEY) + '.pub')
        if public_path.is_symlink() or not public_path.is_file() or public_path.stat().st_uid != os.getuid():
            raise EvidenceError('Guest public key file needs review')
        public = ' '.join(public_path.read_text().split()[:2])
        value = api.get('compute', 'os-keypairs/' + prep.PLAN['key_name'])['keypair']
        return value.get('name') == prep.PLAN['key_name'] and public == prepared.get('public_key') == ' '.join(value.get('public_key', '').split()[:2]), {'name': prep.PLAN['key_name']}
    check('Existing guest SSH key', key)
    net = check('Existing networks and free addresses', lambda: (True, network_info(api, network, project)))
    if net:
        check('Address space for two VMs and one floating IP', lambda: (net['free_private_addresses'] >= 2 and net['free_external_addresses'] >= 1,
              {'message': str(net['free_private_addresses']) + ' private / ' + str(net['free_external_addresses']) + ' external addresses available'}))
    def names():
        conflicts = []
        for service, path, field, wanted in [('compute', 'servers/detail?limit=100', 'servers', TARGET['names']),
                ('block-storage', 'volumes/detail?limit=100', 'volumes', [TARGET['volume_name']]),
                ('network', 'v2.0/ports', 'ports', ['lab1-port', 'lab2-port']),
                ('network', 'v2.0/security-groups', 'security_groups', ['lab1-access', 'lab2-access'])]:
            conflicts += [r['name'] for r in api.list(service, path, field) if r.get('name') in wanted]
        return not conflicts, {'conflicts': conflicts, 'message': 'Conflicts: ' + ', '.join(conflicts) if conflicts else 'Planned VM, disk, port and security-group names are available'}
    check('New resource names available', names)
    def old_vm():
        server_id = identifier(old['ids']['server'])
        value = api.get('compute', 'servers/' + server_id)['server']
        if value.get('id') != server_id or value.get('name') != 'lab-vm-01' or value.get('tenant_id') != project or 'OS-EXT-STS:task_state' not in value:
            raise EvidenceError('Existing VM identity/state needs review')
        if str(value.get('OS-EXT-SRV-ATTR:host', '')).split('.')[0] not in inv.NODES:
            raise EvidenceError('Existing VM compute host is outside the reviewed three-node lab')
        if any(h.get('observed') is not True for h in hosts.values()):
            raise EvidenceError('Actual VM runtime could not be verified on every host')
        active = [n for n, h in hosts.items() if server_id in h['active_domain_uuids']]
        return value.get('status') == 'SHUTOFF' and value['OS-EXT-STS:task_state'] is None and not active, {
            'id': server_id, 'status': value.get('status'), 'host': value.get('OS-EXT-SRV-ATTR:host'), 'active_hosts': active,
            'message': 'Existing VM must be SHUTOFF and inactive; no stop request sent'}
    check('Old lab-vm-01 safely off', old_vm)
    def flavor():
        flavors = api.list('compute', 'flavors/detail?limit=100', 'flavors')
        matches = [r for r in flavors if (r.get('vcpus'), r.get('ram'), r.get('disk')) == (1, ram, 10)
                   and r.get('OS-FLV-EXT-DATA:ephemeral', 0) == 0 and r.get('swap') in ('', 0, None)]
        return True, {'message': ('Reusable matching flavor: ' + ', '.join(clean(r['name']) for r in matches)) if matches else 'No matching flavor; a new flavor must appear in the later plan',
                      'ids': [r['id'] for r in matches], 'ram_mb': ram}
    check('Flavor options at assessed RAM', flavor)
    def quotas():
        compute = api.get('compute', 'os-quota-sets/' + project + '/detail')['quota_set']
        neutron = api.get('network', 'v2.0/quotas/' + project + '/details.json')['quota']
        qrows = [quota_row(label, compute.get(key), count) for label, key, count in
                 [('VMs', 'instances', 2), ('vCPUs', 'cores', 2), ('RAM MiB', 'ram', 2 * ram)]]
        # Two IPv4 groups: app SSH/HTTP, DB SSH/PostgreSQL, one egress rule each.
        # The later Terraform definition removes generated rules only in these new groups.
        qrows += [quota_row(label, neutron.get(key), count, 'used') for label, key, count in
                  [('Ports', 'port', 2), ('Floating IPs', 'floatingip', 1), ('Security groups', 'security_group', 2), ('Security rules', 'security_group_rule', 6)]]
        return all(q['ok'] for q in qrows), {'rows': qrows}
    check('Compute and network quotas', quotas)
    def storage():
        value = storage_info(api, project)
        return value['fits'], value
    def capacity():
        values = capacity_info(api, hosts, ram)
        return sum(r['review_slots'] for r in values) >= 2, {'rows': values}
    check('Cinder space and quotas', storage)
    check('Physical and Placement capacity', capacity)


def render(rows, details):
    lines = ['\nPHASE 6 A — READINESS', 'Target: lab1 + lab2; 1 vCPU each; approved RAM ' + str(TARGET['ram_mb_each'] // 1024) + ' GiB each.']
    assessed = details.get('assessed_ram_mb_each')
    if assessed and assessed != TARGET['ram_mb_each']:
        lines.append('RAM assessment against image: ' + str(assessed // 1024) + ' GiB each — needs your review; approved size was not changed.')
    for row in rows:
        value = details.get(row['check'], {})
        message = value.get('message') or value.get('error') or ''
        lines.append(row['status'].ljust(8) + row['check'] + (': ' + clean(message) if message else ''))
    for key in ('Compute and network quotas', 'Cinder space and quotas'):
        qrows = details.get(key, {}).get('rows', details.get(key, {}).get('quotas', []))
        if qrows:
            lines += ['\nQuota | Needed | Remaining | Result']
            lines += [q['item'] + ' | ' + str(q['needed']) + ' | ' + ('unlimited' if q['remaining'] is None else str(q['remaining'])) + ' | ' + ('PASS' if q['ok'] else 'REVIEW') for q in qrows]
    capacity = details.get('Physical and Placement capacity', {}).get('rows', [])
    if capacity:
        lines += ['\nNode | CPU threads | Available RAM GiB | Instance disk GiB | Review slots']
        lines += [' | '.join(str(r[k]) for k in ('node', 'cpu_threads', 'available_ram_gib', 'available_instance_disk_gib', 'review_slots')) for r in capacity]
        lines.append('Review slots include Placement plus 1 GiB host RAM / 2 GiB disk margins; no reservation or scheduling guarantee.')
    old = details.get('Old lab-vm-01 safely off', {})
    if old.get('id'):
        lines.append('\nOld VM: ' + clean(old['status']) + ' on ' + clean(old['host']) + '; active on ' + (', '.join(old['active_hosts']) or 'no host'))
    pools = details.get('Cinder space and quotas', {}).get('pools', [])
    for pool in pools:
        lines.append('Storage: ' + pool['backend'] + ' | free ' + str(pool['free_gib']) + ' GiB')
    passed = bool(rows) and all(r['status'] == 'PASS' for r in rows)
    lines += ['\nRESULT: ' + ('PHASE6_READINESS_PASSED' if passed else 'PHASE6_READINESS_NEEDS_REVIEW'),
              'Snapshot only. No Terraform install, VM stop/create, image edit, formatting or checkpoint update.',
              'Return this report. BLOCKED means evidence is unavailable; it does not mean a service is down.']
    return lines, passed


def execute(log, details):
    details['approved_target'] = dict(TARGET)
    deploy.source_guard()
    env = deploy.tools.tool_environment()
    inventory, config = deploy.validation.local_checks(lambda _: None, env)
    before, inputs = protected(), prep.access.input_hashes(config, inventory)
    rows = []
    def check(label, function):
        try:
            ok, value = function()
            details[label] = value
            rows.append({'check': label, 'status': 'PASS' if ok is True else 'REVIEW'})
            return value
        except Exception as error:
            details[label] = {'error': reason(error) + '; read unavailable'}
            rows.append({'check': label, 'status': 'BLOCKED'})
            return None
    check('R2d2 tools platform and free disk', r2d2_info)
    with ExitStack() as stack:
        for phase, name in [('phase5', n) for n in ('api-recovery.lock', 'api-vip.lock', 'mariadb.lock', 'router.lock', 'migration.lock')] + [('phase4', n) for n in ('vm.lock', 'volume.lock', 'networks.lock', 'vm-preparation.lock')]:
            path = REPO / 'private' / phase / name
            if path.is_symlink():
                raise RuntimeError('Unsafe exercise lock')
            handle = stack.enter_context(path.open('a'))
            os.chmod(path, 0o600)
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        inv.ensure_agent(env, log)
        passwords = {n: getpass.getpass('Joe sudo password on ' + n + ' (hidden): ') for n in inv.NODES}
        hosts, capacity = {}, {}
        log('1/3 Read all three hosts and actual instance storage; no changes…')
        def observe(node):
            return health.host_observe(node, passwords[node], env), capacity_observe(node, passwords[node], env)
        try:
            with ThreadPoolExecutor(max_workers=3) as pool:
                jobs = {pool.submit(observe, n): n for n in inv.NODES}
                for job in as_completed(jobs):
                    node = jobs[job]
                    hosts[node], capacity[node] = job.result()
                    log('Collected ' + node + ': ' + ('host/capacity available' if hosts[node].get('observed') and capacity[node].get('observed') else 'one or more reads unavailable'))
        finally:
            passwords.clear()
        details['hosts'] = hosts
        details['capacity_hosts'] = capacity
        log('2/3 Read APIs, services, image minimums and resource quotas…')
        cluster_rows, cluster_details = [], {}
        health.host_rows(hosts, cluster_rows, cluster_details)
        health.api_checks(cluster_rows, cluster_details, env, include_workload=False)
        bad = [r['check'] for r in cluster_rows if r['status'] != 'PASS']
        details['cluster'] = {'checks': cluster_rows, 'details': cluster_details}
        check('Cluster services and agents', lambda: (not bad, {'message': 'All checked services passed' if not bad else ', '.join(bad)}))
        try:
            original, project, user = prep.authenticate(config)
            api = ReadAPI(original.client, original.urls, original.token)
            collect_cloud(api, project, user, capacity, check, details)
        except Exception as error:
            check('Cloud identity and existing checkpoints', lambda: (_ for _ in ()).throw(error))
        log('3/3 Confirm saved inputs and checkpoints are unchanged…')
        check('Existing inputs/checkpoints preserved', lambda: (before == protected() and inputs == prep.access.input_hashes(config, inventory), {'message': 'Compared before and after this read-only check'}))
    details['summary'] = rows
    lines, passed = render(rows, details)
    for line in lines:
        log(line)
    return 0 if passed else 1


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--check', action='store_true')
    args = p.parse_args(argv)
    if not args.check:
        print('PLAN ONLY: read cluster, capacity, image boot minimums, quotas and old VM state. Use --check on R2d2. No install/create/stop mode.')
        return 0
    deploy.validation.config_step.load_step('phase6_r2d2', 'phase2-check.py').require_r2d2()
    deploy.tools.check_paths(ENV)
    if not sys.stdin.isatty():
        raise RuntimeError('Run from joe\'s interactive R2d2 terminal')
    if Path(sys.prefix) != ENV:
        os.execve(str(ENV / 'bin/python'), [str(ENV / 'bin/python'), str(Path(__file__).resolve()), '--check'], deploy.tools.tool_environment())
    os.umask(0o077)
    folder = REPO / 'reports'
    inv.private_dir(folder)
    fd, name = tempfile.mkstemp(prefix='phase6-check-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), suffix='.txt', dir=folder)
    print('Private summary: ' + name, flush=True)
    details, status = {}, 1
    with os.fdopen(fd, 'w') as out:
        def log(line):
            print(line, flush=True)
            out.write(line + '\n')
            out.flush()
        log('Phase 6 Chapter A — CHECK ONLY; approved starting plan: lab1 + lab2')
        try:
            status = execute(log, details)
        except (Exception, KeyboardInterrupt) as error:
            log('RESULT: PHASE6_READINESS_NEEDS_REVIEW')
            log(health.api_error_reason(error) + '; collection incomplete; no changes requested')
        with Path(name).with_suffix('.json').open('x') as extra:
            json.dump(details, extra, indent=2)
    print('Return summary: cat ' + shlex.quote(name))
    return status


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError) as error:
        print('Stopped: ' + health.api_error_reason(error) + '; required local inputs unavailable', file=sys.stderr)
        raise SystemExit(1)

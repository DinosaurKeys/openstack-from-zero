#!/usr/bin/env python3
"""Chapter B: create the agreed two networks, two subnets and virtual router. Default: plan."""
import sys
sys.dont_write_bytecode = True
import argparse
import datetime as dt
import fcntl
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = Path(__file__).resolve().parents[1]
s = importlib.util.spec_from_file_location('create_access', REPO/'scripts/phase3-access.py')
access = importlib.util.module_from_spec(s); s.loader.exec_module(access)
deploy = access.deploy; ENV = access.ENV
PLAN = {'version': 2, 'external_name': 'lab-external', 'external_subnet': 'lab-external-subnet',
        'external_cidr': '192.168.0.0/24', 'external_gateway': '192.168.0.1',
        'external_start': '192.168.0.152', 'external_end': '192.168.0.199',
        'physnet': 'physnet1', 'private_name': 'lab-private', 'private_subnet': 'lab-private-subnet',
        'private_cidr': '10.10.10.0/24', 'private_gateway': '10.10.10.1',
        'private_start': '10.10.10.2', 'private_end': '10.10.10.254',
        'dns': ['192.168.0.1'], 'router_name': 'lab-router'}
KINDS = {'external_network': ('networks', 'network'), 'private_network': ('networks', 'network'),
         'external_subnet': ('subnets', 'subnet'), 'private_subnet': ('subnets', 'subnet'), 'router': ('routers', 'router')}


def valid_id(value):
    return isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9-]+', value) is not None


def subset(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and subset(v, actual[k]) for k, v in expected.items())
    return expected == actual and type(expected) is type(actual)


def write_state(path, state):
    if path.is_symlink(): raise RuntimeError('State file must not be a symlink')
    fd, tmp = tempfile.mkstemp(prefix='network-state-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(state, out, indent=2); out.write('\n'); out.flush(); os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


class Neutron:
    def __init__(self, client, base, token):
        self.client, self.base, self.token = client, access.safe_url(base).rstrip('/')+'/v2.0', token

    def get(self, path):
        return self.client.request(self.base+'/'+path, self.token)[0]

    def list(self, resource, query=''):
        url = self.base+'/'+resource+('?' + query if query else '')
        values = []; seen = set()
        while url:
            if url in seen or len(seen) >= 100: raise RuntimeError('Neutron pagination needs review')
            seen.add(url); page, _ = self.client.request(url, self.token)
            if not isinstance(page.get(resource), list): raise RuntimeError('Unexpected Neutron list response: '+resource)
            values.extend(page[resource])
            links = [l['href'] for l in page.get(resource+'_links', []) if l.get('rel') == 'next']
            if len(links) > 1: raise RuntimeError('Ambiguous Neutron pagination')
            url = access.safe_url(urllib.parse.urljoin(url, links[0])) if links else None
            if url and urllib.parse.urlsplit(url).path != urllib.parse.urlsplit(self.base+'/'+resource).path:
                raise RuntimeError('Neutron pagination changed resource')
        return values

    def write(self, method, path, body, status):
        allowed = method == 'POST' and path in ('networks', 'subnets', 'routers')
        allowed |= method == 'PUT' and re.fullmatch(r'routers/[a-zA-Z0-9-]+/add_router_interface', path) is not None
        if not allowed: raise RuntimeError('Unapproved network API write operation')
        url = access.safe_url(self.base+'/'+path)
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method=method,
            headers={'Content-Type': 'application/json', 'Accept': 'application/json', 'X-Auth-Token': self.token})
        try:
            with self.client.opener.open(req, timeout=45) as response:
                if response.status != status: raise RuntimeError('Unexpected network write response '+str(response.status))
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000: raise RuntimeError('Network response too large')
                return json.loads(raw)
        except urllib.error.HTTPError as error:
            raise RuntimeError(method+' '+path+': HTTP '+str(error.code)+'; no automatic retry') from None
        except (urllib.error.URLError, OSError, ValueError):
            raise RuntimeError(method+' '+path+': response unavailable; request may have completed. No automatic retry.') from None


def specifications(ids, project):
    common = {'project_id': project, 'admin_state_up': True}
    ext = dict(common, name=PLAN['external_name'], shared=False, **{'router:external': True,
        'provider:network_type': 'flat', 'provider:physical_network': PLAN['physnet']})
    private = dict(common, name=PLAN['private_name'], shared=False, **{'router:external': False, 'provider:network_type': 'vxlan'})
    specs = {'external_network': ext, 'private_network': private}
    if 'external_network' in ids:
        specs['external_subnet'] = {'project_id': project, 'name': PLAN['external_subnet'], 'network_id': ids['external_network'],
            'ip_version': 4, 'cidr': PLAN['external_cidr'], 'gateway_ip': PLAN['external_gateway'], 'enable_dhcp': False,
            'allocation_pools': [{'start': PLAN['external_start'], 'end': PLAN['external_end']}], 'dns_nameservers': []}
    if 'private_network' in ids:
        specs['private_subnet'] = {'project_id': project, 'name': PLAN['private_subnet'], 'network_id': ids['private_network'],
            'ip_version': 4, 'cidr': PLAN['private_cidr'], 'gateway_ip': PLAN['private_gateway'], 'enable_dhcp': True,
            'allocation_pools': [{'start': PLAN['private_start'], 'end': PLAN['private_end']}], 'dns_nameservers': PLAN['dns']}
    if 'external_subnet' in ids:
        specs['router'] = dict(common, name=PLAN['router_name'], ha=True, distributed=False,
            external_gateway_info={'network_id': ids['external_network'], 'enable_snat': True,
                                   'external_fixed_ips': [{'subnet_id': ids['external_subnet']}]})
    return specs


def matches(key, expected, actual):
    if key != 'router': return subset(expected, actual)
    # Neutron adds an allocated address to each external_fixed_ips item.
    e = dict(expected); gateway = dict(e.pop('external_gateway_info'))
    wanted = gateway.pop('external_fixed_ips')
    got = actual.get('external_gateway_info') or {}
    ips = got.get('external_fixed_ips', [])
    return subset(e, actual) and subset(gateway, got) and len(ips) == 1 and all(
        subset(w, a) and ipaddress.ip_address(a.get('ip_address', '0.0.0.0')) in ipaddress.ip_network(PLAN['external_cidr'])
        and ipaddress.ip_address(PLAN['external_start']) <= ipaddress.ip_address(a.get('ip_address', '0.0.0.0')) <= ipaddress.ip_address(PLAN['external_end'])
        for w, a in zip(wanted, ips))


def preflight(api, ids, project):
    aliases = {e.get('alias') for e in api.list('extensions')}
    if not {'router', 'external-net', 'provider', 'agent', 'l3_agent_scheduler', 'l3-ha', 'dvr', 'dhcp_agent_scheduler'}.issubset(aliases):
        raise RuntimeError('Required Neutron network/router extensions are unavailable; no further writes')
    inventory = {r: api.list(r) for r in ('networks', 'subnets', 'routers')}
    names = {'external_network': PLAN['external_name'], 'private_network': PLAN['private_name'],
             'external_subnet': PLAN['external_subnet'], 'private_subnet': PLAN['private_subnet'], 'router': PLAN['router_name']}
    for key, (plural, singular) in KINDS.items():
        found = [o for o in inventory[plural] if o.get('name') == names[key]]
        if len(found) > 1 or any(o['id'] != ids.get(key) for o in found):
            raise RuntimeError('Existing untracked or duplicate object named '+names[key]+'; not adopted or modified')
        if key in ids and not any(o['id'] == ids[key] for o in found):
            raise RuntimeError('Tracked object missing or renamed: '+key)
    for key, ident in ids.items():
        if key not in KINDS or not valid_id(ident): raise RuntimeError('Unexpected ID in local state')
        plural, singular = KINDS[key]; obj = next(o for o in inventory[plural] if o['id'] == ident)
        expected = specifications(ids, project).get(key)
        if expected is None or not matches(key, expected, obj): raise RuntimeError('Tracked object differs from plan: '+key)
    private = ipaddress.ip_network(PLAN['private_cidr'])
    external = ipaddress.ip_network(PLAN['external_cidr'])
    for subnet in inventory['subnets']:
        if subnet['id'] in ids.values(): continue
        net = ipaddress.ip_network(subnet['cidr'])
        if net.version == 4 and (net.overlaps(private) or net.overlaps(external)):
            raise RuntimeError('An existing Neutron subnet overlaps the plan; review before creation')
    for n in inventory['networks']:
        if n.get('provider:network_type') == 'flat' and n.get('provider:physical_network') == PLAN['physnet'] and n['id'] != ids.get('external_network'):
            raise RuntimeError('physnet1 already has another flat network')
    agents = api.list('agents')
    for host in ('node1', 'node2', 'node3'):
        for binary in ('neutron-openvswitch-agent', 'neutron-l3-agent', 'neutron-dhcp-agent', 'neutron-metadata-agent'):
            selected = [a for a in agents if a.get('host', '').split('.')[0] == host and a.get('binary') == binary]
            if len(selected) != 1 or selected[0].get('alive') is not True or selected[0].get('admin_state_up') is not True:
                raise RuntimeError('Required live network agent needs review: '+host+' '+binary)


def create(api, project, state, save, log):
    ids = state['ids']
    if state.get('pending'): raise RuntimeError('Previous request has an unconfirmed outcome; return the report before any retry')
    preflight(api, ids, project)
    for key in ('external_network', 'external_subnet', 'private_network', 'private_subnet', 'router'):
        plural, singular = KINDS[key]; expected = specifications(ids, project)[key]
        if key in ids:
            log('Reusing verified tracked object: '+key); continue
        state['pending'] = key; save(state)
        log('Creating '+key+' ('+expected['name']+').')
        response = api.write('POST', plural, {singular: expected}, 201)
        obj = response.get(singular, {}); ident = obj.get('id')
        if not valid_id(ident): raise RuntimeError('Create response missing a valid ID: '+key)
        ids[key] = ident; state.pop('pending', None); save(state)
        if not matches(key, expected, obj): raise RuntimeError('Created object needs review: '+key)
    ports = api.list('ports', urllib.parse.urlencode({'device_id': ids['router']}))
    attached = [p for p in ports if p.get('device_id') == ids['router'] and p.get('device_owner') in ('network:router_interface', 'network:ha_router_replicated_interface')
                and any(f.get('subnet_id') == ids['private_subnet'] for f in p.get('fixed_ips', []))]
    if not attached:
        state['pending'] = 'router_interface'; save(state)
        api.write('PUT', 'routers/'+ids['router']+'/add_router_interface', {'subnet_id': ids['private_subnet']}, 200)
        state.pop('pending', None); save(state)
        log('Attached the private subnet to the tracked router.')
    elif len(attached) != 1: raise RuntimeError('Duplicate private router interfaces need review')
    deadline = time.monotonic()+180
    while True:
        ports = api.list('ports', urllib.parse.urlencode({'device_id': ids['router']}))
        internal = [p for p in ports if p.get('device_id') == ids['router'] and p.get('device_owner') in ('network:router_interface', 'network:ha_router_replicated_interface')
                    and any(f.get('subnet_id') == ids['private_subnet'] and f.get('ip_address') == PLAN['private_gateway'] for f in p.get('fixed_ips', []))]
        external = [p for p in ports if p.get('device_id') == ids['router'] and p.get('device_owner') == 'network:router_gateway'
                    and any(f.get('subnet_id') == ids['external_subnet'] for f in p.get('fixed_ips', []))]
        if len(internal) == len(external) == 1 and all(p.get('status') == 'ACTIVE' for p in internal+external): break
        if time.monotonic() >= deadline: raise RuntimeError('Router ports have not both reached ACTIVE; return the report without redeploying')
        time.sleep(5)
    preflight(api, ids, project)
    router = api.get('routers/'+ids['router'])['router']
    placement = wait_ha(api, ids['router'])
    dhcp = wait_dhcp(api, ids['private_network'])
    if router.get('status') != 'ACTIVE' or router.get('ha') is not True:
        raise RuntimeError('Router status or HA flag needs review')
    networks = [api.get('networks/'+ids[key])['network'] for key in ('external_network', 'private_network')]
    return {'ids': dict(ids), 'router_hosts': placement, 'dhcp_hosts': [a.get('host') for a in dhcp], 'router_external_ips': router['external_gateway_info']['external_fixed_ips'],
            'networks': [{k: n.get(k) for k in ('id', 'name', 'provider:network_type', 'provider:physical_network', 'provider:segmentation_id', 'mtu')} for n in networks],
            'private_gateway': PLAN['private_gateway'], 'router_ports': [{'id': p['id'], 'device_owner': p['device_owner'], 'fixed_ips': p['fixed_ips'], 'status': p['status']} for p in internal+external],
            'scope': 'Network objects and API/port state verified; VM traffic, DNS operation and failover not tested.'}


def ha_placement(agents):
    hosts = {a.get('host', '').split('.')[0] for a in agents}
    states = [a.get('ha_state') for a in agents]
    if len(agents) != 3 or hosts != {'node1', 'node2', 'node3'} or states.count('active') != 1 or states.count('standby') != 2:
        raise RuntimeError('Expected HA router on all three nodes: one active, two standby')
    if not all(a.get('alive') is True and a.get('admin_state_up') is True for a in agents):
        raise RuntimeError('An HA router agent is not alive/enabled')
    return [{k: a.get(k) for k in ('host', 'alive', 'admin_state_up', 'ha_state')} for a in agents]


def wait_ha(api, router_id):
    deadline = time.monotonic()+180
    while True:
        try: return ha_placement(api.get('routers/'+router_id+'/l3-agents').get('agents', []))
        except RuntimeError:
            if time.monotonic() >= deadline: raise
            time.sleep(5)


def wait_dhcp(api, network_id):
    deadline = time.monotonic()+180
    while True:
        agents = api.get('networks/'+network_id+'/dhcp-agents').get('agents', [])
        hosts = {a.get('host', '').split('.')[0] for a in agents}
        if len(agents) == 2 and len(hosts) == 2 and hosts.issubset({'node1','node2','node3'}) and all(a.get('alive') is True and a.get('admin_state_up') is True for a in agents): return agents
        if time.monotonic() >= deadline: raise RuntimeError('Expected two alive/enabled DHCP agents on known nodes')
        time.sleep(5)


def execute(log):
    deploy.source_guard(); env = deploy.tools.tool_environment()
    inventory, config = deploy.validation.local_checks(lambda line: None, env)
    baseline = access.input_hashes(config, inventory)
    # Inspect non-default routes visible on R2d2; this cannot enumerate Windows/VPN routes.
    p = subprocess.run(['ip', '-j', '-4', 'route', 'show', 'table', 'all'], capture_output=True, text=True, check=True, timeout=15)
    for route in json.loads(p.stdout):
        cidr = route.get('dst', 'default')
        network = ipaddress.ip_network(cidr, strict=False) if cidr != 'default' else None
        if network is not None and network.prefixlen != 0 and network.overlaps(ipaddress.ip_network(PLAN['private_cidr'])):
            raise RuntimeError('A visible R2d2 route overlaps the proposed private subnet')
    profile = access.load_profile(config); client = access.Client(profile['cacert'])
    identity = profile['auth']['auth_url'].rstrip('/')
    if not identity.endswith('/v3'): identity += '/v3'
    token_doc, token = client.request(identity+'/auth/tokens', body=access.auth_body(profile), expect=201)
    project = token_doc['token']['project']['id']
    if not token or not valid_id(project): raise RuntimeError('Project-scoped authentication needs review')
    base = access.catalog_urls(token_doc['token']['catalog'], profile['region_name'])['network']
    api = Neutron(client, base, token)
    folder = REPO/'private/phase4'; deploy.inv.private_dir(folder)
    lock = folder/'networks.lock'
    if lock.is_symlink(): raise RuntimeError('Lock path must not be a symlink')
    with open(lock, 'a') as handle:
        os.chmod(lock, 0o600)
        try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError('Another network creation command is already running') from None
        path = folder/'networks.json'
        if path.exists():
            deploy.validation.private_file(path); state = json.loads(path.read_text())
            if state.get('plan') != PLAN or state.get('project_id') != project or state.get('api') != base:
                raise RuntimeError('Existing private network state differs from this plan/project/API')
        else:
            if path.is_symlink(): raise RuntimeError('State path must not be a symlink')
            state = {'plan': PLAN, 'project_id': project, 'api': base, 'ids': {}}
        summary = create(api, project, state, lambda value: write_state(path, value), log)
        if access.input_hashes(config, inventory) != baseline: raise RuntimeError('Existing deployment input files changed; review required')
        log('Created network summary:\n'+json.dumps(summary, indent=2))
        log('RESULT: LAB_NETWORKS_CREATED_AND_VERIFIED')
        log('Return this report. No VM, floating IP or volume was created; no packet-connectivity claim is made.')
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--apply', action='store_true'); args = p.parse_args(argv)
    if not args.apply:
        print('PLAN ONLY — no connections or files changed:\n'+json.dumps(PLAN, indent=2))
        print('Creates two networks, two subnets and one HA non-distributed router plus its private interface. Read Chapter B before --apply.'); return 0
    deploy.validation.config_step.load_step('create_network_r2d2', 'phase2-check.py').require_r2d2(); deploy.tools.check_paths(ENV)
    if not sys.stdin.isatty(): raise RuntimeError('Use an interactive R2d2 terminal')
    if Path(sys.prefix) != ENV:
        os.execve(str(ENV/'bin/python'), [str(ENV/'bin/python'), str(Path(__file__).resolve()), '--apply'], deploy.tools.tool_environment())
    os.umask(0o077); reports = REPO/'reports'; deploy.inv.private_dir(reports)
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-')
    fd, name = tempfile.mkstemp(prefix='phase4-create-networks-'+stamp, suffix='.txt', dir=reports)
    print('Private report: '+name, flush=True); status = 1
    with os.fdopen(fd, 'w') as output:
        def log(line): print(line, flush=True); output.write(line+'\n'); output.flush()
        log('Phase 4 Chapter B — APPLY network objects only, then read-only verification.')
        log('Approved plan:\n'+json.dumps(PLAN, indent=2))
        try: status = execute(log)
        except (Exception, KeyboardInterrupt) as error:
            log('RESULT: LAB_NETWORKS_NEED_REVIEW')
            log(str(error) if isinstance(error, RuntimeError) else 'Command stopped ('+type(error).__name__+'); private details withheld.')
            log('Earlier API requests may have created resources. No automatic rollback, deletion or request retry. Return the report.')
    print('Read report: cat '+shlex.quote(name)); return status


if __name__ == '__main__':
    try: sys.exit(main())
    except (RuntimeError, OSError) as error: print('Stopped: '+str(error), file=sys.stderr); sys.exit(1)

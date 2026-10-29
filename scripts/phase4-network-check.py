#!/usr/bin/env python3
"""Phase 4 Chapter A: inspect existing networking only; default is plan."""
import sys
sys.dont_write_bytecode = True
import argparse
import datetime as dt
import getpass
import importlib.util
import json
import os
from pathlib import Path
import shlex
import tempfile
import urllib.parse

REPO = Path(__file__).resolve().parents[1]
s = importlib.util.spec_from_file_location('network_access', REPO/'scripts/phase3-access.py')
access = importlib.util.module_from_spec(s); s.loader.exec_module(access)
deploy = access.deploy; inv = deploy.inv; ENV = deploy.ENV
FIELDS = {
    'networks': ['id', 'name', 'status', 'router:external', 'shared', 'provider:network_type', 'provider:physical_network', 'provider:segmentation_id', 'mtu', 'subnets'],
    'subnets': ['id', 'name', 'network_id', 'cidr', 'gateway_ip', 'allocation_pools', 'enable_dhcp', 'dns_nameservers', 'ip_version'],
    'routers': ['id', 'name', 'status', 'external_gateway_info', 'ha', 'distributed'],
    'ports': ['id', 'name', 'network_id', 'device_id', 'device_owner', 'fixed_ips', 'status', 'binding:host_id', 'mac_address'],
    'agents': ['id', 'host', 'binary', 'agent_type', 'alive', 'admin_state_up'],
    'floatingips': ['id', 'floating_ip_address', 'fixed_ip_address', 'router_id', 'port_id', 'status']}


def cloud_state(profile, client):
    auth_url = access.safe_url(profile['auth']['auth_url']).rstrip('/')
    identity = auth_url if auth_url.endswith('/v3') else auth_url+'/v3'
    doc, token = client.request(identity+'/auth/tokens', body=access.auth_body(profile), expect=201)
    if not token: raise RuntimeError('Authentication did not return a token')
    base = access.catalog_urls(doc['token']['catalog'], profile['region_name'])['network'].rstrip('/')
    result = {}
    for resource, fields in FIELDS.items():
        root = base+'/v2.0/'+resource
        url = root+'?'+urllib.parse.urlencode([('limit', '1000'), *(('fields', f) for f in fields)])
        values = []; visited = set()
        while url:
            if url in visited or len(visited) >= 100: raise RuntimeError('Network API pagination needs review: '+resource)
            visited.add(url); page, _ = client.request(url, token)
            if not isinstance(page.get(resource), list): raise RuntimeError('Incomplete network API response: '+resource)
            values += [{k: v for k, v in item.items() if k in fields} for item in page[resource]]
            next_links = [l['href'] for l in page.get(resource+'_links', []) if l.get('rel') == 'next']
            if len(next_links) > 1: raise RuntimeError('Ambiguous pagination: '+resource)
            url = access.safe_url(urllib.parse.urljoin(root, next_links[0])) if next_links else None
            if url and urllib.parse.urlsplit(url).path != urllib.parse.urlsplit(root).path:
                raise RuntimeError('Pagination changed resource path: '+resource)
        result[resource] = values
    return result


def playbook(folder):
    return json.dumps([{'name': 'Read deployed network state only', 'hosts': 'baremetal', 'gather_facts': False, 'become': True,
        'tasks': [{'name': 'Read host networking', 'ansible.builtin.script': shlex.join([
            str(REPO/'scripts/phase4-network-node.py'), '--node', '{{ inventory_hostname }}'])+' executable=/usr/bin/python3',
            'register': 'network_state', 'changed_when': False},
            {'name': 'Save private network observations on R2d2', 'delegate_to': 'localhost', 'become': False,
             'ansible.builtin.copy': {'content': '{{ network_state.stdout }}', 'dest': str(folder/'{{ inventory_hostname }}.json'), 'mode': '0600'}}]}], indent=2)+'\n'


def execute(log, work):
    deploy.source_guard(); env = deploy.tools.tool_environment()
    inventory, config = deploy.validation.local_checks(lambda line: None, env)
    baseline = access.input_hashes(config, inventory)
    profile = access.load_profile(config); client = access.Client(profile['cacert'])
    log('Reading Neutron networks, subnets, routers, ports, agents and floating IPs over verified HTTPS.')
    cloud = cloud_state(profile, client)
    log('Cloud network inventory:\n'+json.dumps(cloud, indent=2))
    inv.ensure_agent(env, log)
    folder = work/'nodes'; inv.private_dir(folder)
    play = work/'network.yml'; play.write_text(playbook(folder)); play.chmod(0o600)
    deploy.tools.Runner(log, env).run([ENV/'bin/ansible-playbook', '-i', inventory, play, '--syntax-check'])
    nodes = {}; issues = []
    for node in inv.NODES:
        password = getpass.getpass('Joe sudo password on '+node+': ')
        child = dict(env, ANSIBLE_BECOME_PASS=password, ANSIBLE_BECOME_ASK_PASS='False')
        try:
            deploy.tools.Runner(log, child).run([ENV/'bin/ansible-playbook', '-i', inventory, play, '--limit', node,
                '-e', 'ansible_python_interpreter=/usr/bin/python3'], terminal=True)
            state = json.loads((folder/(node+'.json')).read_text()); nodes[node] = state
            log(node+' network observations:\n'+json.dumps(state, indent=2))
            if state.get('node') != node or not state.get('collected'): issues.append(node+': incomplete host observations')
            if not state.get('checks') or not all(v is True for v in state['checks'].values()): issues.append(node+': management/provider layout needs review')
        except (RuntimeError, OSError, ValueError): issues.append(node+': host inspection could not complete')
        finally: child.pop('ANSIBLE_BECOME_PASS', None); password = None
    if access.input_hashes(config, inventory) != baseline: issues.append('Existing private deployment inputs changed')
    summary = {'object_counts': {k: len(v) for k, v in cloud.items()},
               'agent_states': cloud['agents'], 'nodes_collected': sorted(nodes), 'issues': issues,
               'meaning': 'Existing state collected for review; no VM traffic or address-availability test.'}
    log('Network inspection summary:\n'+json.dumps(summary, indent=2))
    log('RESULT: '+('NETWORK_INSPECTION_COLLECTED for all' if not issues else 'NETWORK_INSPECTION_NEEDS_REVIEW'))
    log('Return this report before choosing names, tenant CIDR or creating network resources.')
    return 0 if not issues else 1


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--run', action='store_true'); args = p.parse_args(argv)
    if not args.run:
        print('PLAN ONLY: inspect Neutron objects/agents and deployed OVS/host network settings on all three nodes. No network objects or settings changed.'); return 0
    deploy.validation.config_step.load_step('phase4_r2d2', 'phase2-check.py').require_r2d2(); deploy.tools.check_paths(ENV)
    if not sys.stdin.isatty(): raise RuntimeError('Use an interactive R2d2 terminal for SSH-key and node sudo prompts')
    if Path(sys.prefix) != ENV:
        os.execve(str(ENV/'bin/python'), [str(ENV/'bin/python'), str(Path(__file__).resolve()), '--run'], deploy.tools.tool_environment())
    os.umask(0o077); reports = REPO/'reports'; inv.private_dir(reports)
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-')
    fd, name = tempfile.mkstemp(prefix='phase4-network-'+stamp, suffix='.txt', dir=reports)
    work = Path(tempfile.mkdtemp(prefix='phase4-network-'+stamp, dir=reports)); status = 1
    print('Private report: '+name, flush=True)
    with os.fdopen(fd, 'w') as output:
        def log(line): print(line, flush=True); output.write(line+'\n'); output.flush()
        log('Phase 4 Chapter A: existing network inspection only; no resource creation or configuration changes.')
        try: status = execute(log, work)
        except (Exception, KeyboardInterrupt) as error:
            log('RESULT: NETWORK_INSPECTION_NEEDS_REVIEW')
            log(str(error) if isinstance(error, RuntimeError) else 'Inspection did not complete ('+type(error).__name__+'); private details withheld.')
    print('Read report: cat '+shlex.quote(name))
    print('Use the actual report file as an attachment if terminal copying truncates it.')
    return status


if __name__ == '__main__':
    try: sys.exit(main())
    except (RuntimeError, OSError) as error: print('Stopped: '+str(error), file=sys.stderr); sys.exit(1)

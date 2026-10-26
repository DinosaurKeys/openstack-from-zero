#!/usr/bin/env python3
"""Read selected OpenStack objects using the existing private HTTPS profile."""
import sys
sys.dont_write_bytecode = True
import argparse
import importlib.util
import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('view_access', REPO/'scripts/phase4-prepare-vm.py')
step = importlib.util.module_from_spec(spec); spec.loader.exec_module(step)
QUERIES = {
    'networks': ('network', 'v2.0/networks', 'networks', ('id', 'name', 'status', 'router:external', 'provider:network_type', 'provider:physical_network', 'mtu')),
    'subnets': ('network', 'v2.0/subnets', 'subnets', ('id', 'name', 'cidr', 'gateway_ip', 'enable_dhcp', 'allocation_pools', 'dns_nameservers')),
    'agents': ('network', 'v2.0/agents', 'agents', ('id', 'agent_type', 'binary', 'host', 'alive', 'admin_state_up')),
    'images': ('image', 'v2/images?limit=100', 'images', ('id', 'name', 'status', 'size', 'disk_format', 'min_disk', 'min_ram')),
    'flavors': ('compute', 'flavors/detail?limit=100', 'flavors', ('id', 'name', 'vcpus', 'ram', 'disk', 'os-flavor-access:is_public')),
    'security-groups': ('network', 'v2.0/security-groups', 'security_groups', ('id', 'name', 'description', 'security_group_rules')),
    'servers': ('compute', 'servers/detail?limit=100', 'servers', ('id', 'name', 'status', 'addresses', 'OS-EXT-SRV-ATTR:host')),
    'volumes': ('block-storage', 'volumes/detail?limit=100', 'volumes', ('id', 'name', 'status', 'size', 'attachments')),
    'compute-services': ('compute', 'os-services', 'services', ('id', 'binary', 'host', 'zone', 'status', 'state')),
    'floating-ips': ('network', 'v2.0/floatingips', 'floatingips', ('id', 'floating_ip_address', 'fixed_ip_address', 'port_id', 'router_id', 'status')),
}


def collect(api, what):
    if what == 'router':
        routers = api.list('network', 'v2.0/routers?name=lab-router', 'routers')
        routers = [r for r in routers if r.get('name') == 'lab-router']
        if len(routers) != 1 or not step.net.valid_id(routers[0].get('id')):
            raise RuntimeError('Expected exactly one lab-router; no changes made')
        router = routers[0]; identifier = router['id']
        ports = api.list('network', 'v2.0/ports?device_id='+identifier, 'ports')
        agents = api.get('network', 'v2.0/routers/'+identifier+'/l3-agents').get('agents', [])
        return {'router': {k: router.get(k) for k in ('id', 'name', 'status', 'ha', 'distributed', 'external_gateway_info')},
                'ports': [{k: p.get(k) for k in ('id', 'device_owner', 'fixed_ips', 'status')} for p in ports if p.get('device_id') == identifier],
                'hosting_agents': [{k: a.get(k) for k in ('host', 'alive', 'admin_state_up', 'ha_state')} for a in agents]}
    service, path, key, fields = QUERIES[what]
    return {key: [{k: obj.get(k) for k in fields} for obj in api.list(service, path, key)]}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('object', choices=['router', *QUERIES]); args = p.parse_args(argv)
    step.deploy.validation.config_step.load_step('view_r2d2', 'phase2-check.py').require_r2d2()
    step.deploy.tools.check_paths(step.ENV)
    if Path(sys.prefix) != step.ENV:
        os.execve(str(step.ENV/'bin/python'), [str(step.ENV/'bin/python'), str(Path(__file__).resolve()), args.object], step.deploy.tools.tool_environment())
    step.deploy.source_guard(); env = step.deploy.tools.tool_environment()
    _, config = step.deploy.validation.local_checks(lambda line: None, env, step.deploy.validation.CA_FINGERPRINT)
    api, _, _ = step.authenticate(config)
    print(json.dumps(collect(api, args.object), indent=2))
    return 0


if __name__ == '__main__':
    try: sys.exit(main())
    except Exception as error:
        print('Stopped: '+(str(error) if isinstance(error, RuntimeError) else type(error).__name__+'; private details withheld.'), file=sys.stderr)
        sys.exit(1)

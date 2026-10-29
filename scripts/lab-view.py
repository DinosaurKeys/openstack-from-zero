#!/usr/bin/env python3
"""Read selected OpenStack objects using the existing private HTTPS profile."""
import sys
sys.dont_write_bytecode = True
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re

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
    'servers': ('compute', 'servers/detail?limit=100', 'servers', ('id', 'name', 'status', 'addresses', 'OS-EXT-SRV-ATTR:host', 'tenant_id')),
    'volumes': ('block-storage', 'volumes/detail?limit=100', 'volumes', ('id', 'name', 'status', 'size', 'attachments', 'os-vol-host-attr:host', 'os-vol-tenant-attr:tenant_id')),
    'compute-services': ('compute', 'os-services', 'services', ('id', 'binary', 'host', 'zone', 'status', 'state', 'disabled_reason')),
    'hypervisors': ('compute', 'os-hypervisors/detail', 'hypervisors', ('id', 'hypervisor_hostname', 'state', 'status')),
    'cinder-services': ('block-storage', 'os-services', 'services', ('binary', 'host', 'zone', 'status', 'state')),
    'floating-ips': ('network', 'v2.0/floatingips', 'floatingips', ('id', 'floating_ip_address', 'fixed_ip_address', 'port_id', 'router_id', 'status')),
}
TABLES = {
    'servers': ('servers', (('Name','name'),('Status (Nova)','status'),('Compute host','OS-EXT-SRV-ATTR:host'),('VM ID','id'))),
    'volumes': ('volumes', (('Name','name'),('Status','status'),('GiB','size'),('Storage backend','os-vol-host-attr:host'),('Attached VM ID','attachments'),('Volume ID','id'))),
    'compute-services': ('services', (('Host','host'),('Service','binary'),('Enabled state','status'),('Heartbeat','state'),('Disabled reason','disabled_reason'))),
    'cinder-services': ('services', (('Host','host'),('Service','binary'),('Enabled state','status'),('Heartbeat','state'))),
    'hypervisors': ('hypervisors', (('Host','hypervisor_hostname'),('Enabled state','status'),('State','state'))),
    'agents': ('agents', (('Host','host'),('Agent','agent_type'),('Alive','alive'),('Enabled','admin_state_up'))),
}


def table(what, result):
    key, columns = TABLES[what]
    def cell(value):
        if type(value) is bool:return 'yes' if value else 'no'
        return re.sub(r'[\x00-\x1f\x7f]', '?', str(value if value is not None else 'unknown'))[:100]
    def value(row,field):
        if what=='volumes' and field=='attachments':
            items=row.get(field)
            if not isinstance(items,list):return 'unknown'
            if not items:return '-'
            return ', '.join(str(v.get('server_id') or 'unknown') if isinstance(v,dict) else 'unknown' for v in items)
        if field=='disabled_reason' and row.get('status')=='enabled' and row.get(field) is None:return '-'
        return row.get(field)
    rows = [[heading for heading,_ in columns]]
    rows += [[cell(value(row,field)) for _,field in columns] for row in result[key]]
    widths = [max(len(row[i]) for row in rows) for i in range(len(columns))]
    return '\n'.join(' | '.join(value.ljust(widths[i]) for i,value in enumerate(row)).rstrip() for row in rows)


def collect(api, what, all_projects=False):
    if all_projects and what not in ('servers','volumes'):raise RuntimeError('All-projects is supported only for servers and volumes')
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
    if all_projects:path+='&all_tenants=1'
    return {key: [{k: obj.get(k) for k in fields} for obj in api.list(service, path, key)]}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('object', choices=['router', *QUERIES]); p.add_argument('--table', action='store_true', help='compact VM, volume, service, hypervisor or agent table')
    p.add_argument('--all-projects', action='store_true', help='list VMs or volumes across projects; requires admin policy access')
    args = p.parse_args(argv)
    if args.table and args.object not in TABLES:p.error('--table supports servers, volumes, compute-services, hypervisors, cinder-services and agents')
    if args.all_projects and args.object not in ('servers','volumes'):p.error('--all-projects supports servers and volumes only')
    step.deploy.validation.config_step.load_step('view_r2d2', 'phase2-check.py').require_r2d2()
    step.deploy.tools.check_paths(step.ENV)
    if Path(sys.prefix) != step.ENV:
        os.execve(str(step.ENV/'bin/python'), [str(step.ENV/'bin/python'), str(Path(__file__).resolve()), args.object]+(['--table'] if args.table else [])+(['--all-projects'] if args.all_projects else []), step.deploy.tools.tool_environment())
    step.deploy.source_guard(); env = step.deploy.tools.tool_environment()
    _, config = step.deploy.validation.local_checks(lambda line: None, env)
    api, _, _ = step.authenticate(config)
    result=collect(api, args.object, args.all_projects)
    print(table(args.object,result) if args.table else json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    try: sys.exit(main())
    except Exception as error:
        print('Stopped: '+(str(error) if isinstance(error, RuntimeError) else type(error).__name__+'; private details withheld.'), file=sys.stderr)
        sys.exit(1)

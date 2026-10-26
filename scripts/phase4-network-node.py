#!/usr/bin/env python3
"""Read selected host networking facts; never change network configuration."""
import argparse
import configparser
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess

NODES = {'node1': ('192.168.0.200', 'enx00e04c557e58'),
         'node2': ('192.168.0.201', 'enx00e04c2c1678'),
         'node3': ('192.168.0.202', 'enx00e04c304688')}
CONFIGS = {
    '/etc/kolla/neutron-server/ml2_conf.ini': {'ml2': ['type_drivers', 'tenant_network_types', 'mechanism_drivers'],
        'ml2_type_flat': ['flat_networks'], 'ml2_type_vxlan': ['vni_ranges']},
    '/etc/kolla/neutron-openvswitch-agent/openvswitch_agent.ini': {'ovs': ['bridge_mappings', 'local_ip', 'datapath_type'],
        'agent': ['tunnel_types', 'enable_distributed_routing'], 'securitygroup': ['firewall_driver']},
    '/etc/kolla/neutron-l3-agent/l3_agent.ini': {'DEFAULT': ['agent_mode']},
    '/etc/kolla/neutron-server/neutron.conf': {'DEFAULT': ['core_plugin', 'service_plugins', 'l3_ha']}}


def run(argv):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    if p.returncode:
        raise RuntimeError('exit '+str(p.returncode)+': '+shlex.join(argv)+'; '+p.stderr.strip()[:500])
    return p.stdout.strip()


def selected_config(path, schema):
    parser = configparser.RawConfigParser(strict=False)
    if not parser.read(path): raise RuntimeError('Expected deployed network config is unavailable: '+str(path))
    # Explicit allowlist; auth sections and whole files never enter the report.
    return {section: {key: parser.get(section, key, fallback=None) for key in keys}
            for section, keys in schema.items()}


def interfaces(raw):
    table = json.loads(raw); columns = table['headings']; result = []
    for row in table['data']:
        obj = dict(zip(columns, row)); options = obj.get('options', ['map', []])
        if not isinstance(options, list) or len(options) != 2 or options[0] != 'map':
            raise RuntimeError('Unexpected OVS interface options encoding')
        allowed = {'local_ip', 'remote_ip', 'key', 'in_key', 'out_key', 'dst_port', 'peer'}
        result.append({'name': obj['name'], 'type': obj['type'],
                       'options': {k: v for k, v in options[1] if k in allowed}})
    return result


def collect(node):
    if os.geteuid() != 0 or os.environ.get('SUDO_USER') != 'joe' or socket.gethostname().split('.')[0] != node:
        raise RuntimeError('Expected named node and sudo from joe')
    facts = {}; errors = {}
    def read(label, fn):
        try: facts[label] = fn()
        except Exception as error:
            errors[label] = str(error) if isinstance(error, RuntimeError) else type(error).__name__
    ip, usb = NODES[node]
    read('links', lambda: [{k: a.get(k) for k in ('ifname', 'address', 'mtu', 'flags', 'master', 'addr_info')}
                          for a in json.loads(run(['ip', '-j', 'address', 'show']))])
    read('IPv4 routes', lambda: json.loads(run(['ip', '-j', '-4', 'route', 'show'])))
    read('network namespaces', lambda: run(['ip', 'netns', 'list']).splitlines())
    ovs = ['docker', 'exec', 'openvswitch_vswitchd', 'ovs-vsctl']
    read('OVS bridges', lambda: run(ovs+['list-br']).splitlines())
    for bridge in ('br-ex', 'br-int', 'br-tun'):
        read(bridge+' ports', lambda bridge=bridge: run(ovs+['list-ports', bridge]).splitlines())
    read('OVS interfaces', lambda: interfaces(run(ovs+['--format=json', '--columns=name,type,options', 'list', 'Interface'])))
    for path, schema in CONFIGS.items(): read(path, lambda path=path, schema=schema: selected_config(path, schema))
    links = {a['ifname']: a for a in facts.get('links', [])}
    checks = {
        'management IPv4 on built-in NIC': any(a.get('local') == ip and a.get('prefixlen') == 24
            for a in (links.get('enp0s31f6', {}).get('addr_info') or [])),
        'USB present without host addresses': usb in links and links[usb].get('addr_info') == [],
        'USB attached to br-ex': usb in facts.get('br-ex ports', []),
        'management NIC outside br-ex': 'br-ex ports' in facts and 'enp0s31f6' not in facts['br-ex ports']}
    return {'node': node, 'collected': not errors, 'checks': checks, 'facts': facts, 'errors': errors}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--node', choices=NODES, required=True); args = p.parse_args()
    try: value = collect(args.node)
    except Exception as error: value = {'node': args.node, 'collected': False, 'error': str(error) if isinstance(error, RuntimeError) else type(error).__name__}
    print(json.dumps(value))

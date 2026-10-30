#!/usr/bin/env python3
"""Read physical RAM, Nova instance storage and active domain UUIDs only."""
import argparse
import configparser
import json
import os
from pathlib import Path
import socket
import subprocess
import uuid

NODES = {'node1': '192.168.0.200', 'node2': '192.168.0.201', 'node3': '192.168.0.202'}


def run(args):
    p = subprocess.run(args, capture_output=True, text=True, timeout=35)
    if p.returncode:
        raise RuntimeError('Read-only host query failed; no change requested')
    return p.stdout


def collect(node):
    if node not in NODES or os.geteuid() != 0 or os.environ.get('SUDO_USER') != 'joe' or socket.gethostname().split('.')[0] != node:
        raise RuntimeError('Expected node identity and sudo from joe')
    links = json.loads(run(['ip', '-j', 'address', 'show', 'dev', 'enp0s31f6']))
    if len(links) != 1 or not any(a.get('local') == NODES[node] for a in links[0].get('addr_info', [])):
        raise RuntimeError('Management address differs from the selected node')
    fmt = '{"running":{{json .State.Running}},"mounts":{{json .Mounts}}}'
    container = json.loads(run(['docker', 'inspect', '--format', fmt, 'nova_compute']))
    if container.get('running') is not True:
        raise RuntimeError('Nova compute container is not running')
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read_string(Path('/etc/kolla/nova-compute/nova.conf').read_text())
    target = parser.get('DEFAULT', 'instances_path', fallback='/var/lib/nova/instances').strip('"\'')
    if not target.startswith('/var/lib/nova/') or '..' in Path(target).parts:
        raise RuntimeError('Nova instance path needs review')
    matches = [m for m in container['mounts'] if target == m.get('Destination') or target.startswith(m.get('Destination', '!').rstrip('/') + '/')]
    if not matches:
        raise RuntimeError('Nova instance storage mount was not found')
    mount = max(matches, key=lambda m: len(m['Destination']))
    source = Path(mount['Source']) / Path(target).relative_to(mount['Destination'])
    if not source.is_absolute() or '..' in source.parts or not source.is_dir():
        raise RuntimeError('Observed instance storage directory is unavailable')
    space = os.statvfs(source)
    mem = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, _, value = line.partition(':')
        if key in ('MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'):
            mem[key] = int(value.split()[0]) * 1024
    active = run(['docker', 'exec', '--user', 'root', 'nova_libvirt', 'virsh', '--readonly', '--connect', 'qemu:///system', 'list', '--uuid']).split()
    if len(active) > 1000 or len(set(active)) != len(active) or any(str(uuid.UUID(v)) != v for v in active):
        raise RuntimeError('Active domain identity list needs review')
    return {'node': node, 'observed': True, 'cpu_threads': os.cpu_count(), 'memory': mem,
            'instance_disk_available_bytes': space.f_bavail * space.f_frsize,
            'active_domain_uuids': active}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--node', required=True, choices=NODES)
    args = p.parse_args()
    try:
        value = collect(args.node)
    except Exception as error:
        value = {'node': args.node, 'observed': False,
                 'error': str(error) if isinstance(error, RuntimeError) else type(error).__name__ + '; capacity unavailable'}
    print(json.dumps(value))
    return 0 if value['observed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Guard the authorized OpenStack reset; preserve Ubuntu and the reviewed Cinder PV/VG."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess

TARGETS = {
 'node1': ('192.168.0.200', '50026B7766070422', 'ata-KINGSTON_SUV400S37240G_50026B7766070422'),
 'node2': ('192.168.0.201', '4P3521354988', 'ata-ADATA_SU630_4P3521354988'),
 'node3': ('192.168.0.202', '50026B768317883A', 'ata-KINGSTON_SA400S37240G_50026B768317883A')}
SIZE = 240057409536
VG = 'cinder-volumes'
VIP = '192.168.0.151'


def run(args):
    p = subprocess.run(args, capture_output=True, text=True, timeout=90)
    if p.returncode: raise RuntimeError('Reset command failed: '+args[0]+' (exit '+str(p.returncode)+')')
    return p.stdout.strip()


def rows(tool, section, fields):
    return [{k: str(v).strip() for k, v in r.items()} for report in json.loads(run([
        tool, '--readonly', '--reportformat', 'json', '-o', fields]))['report'] for r in report.get(section, [])]


def guard_disk(node, disk, pvs, vgs, device, system_ancestors):
    _, serial, _ = TARGETS[node]
    if disk.get('path') != device or disk.get('type') != 'disk' or disk.get('tran') != 'sata' or disk.get('serial', '').strip() != serial or int(disk.get('size', 0)) != SIZE:
        raise RuntimeError('Reviewed SATA identity does not match')
    if device in system_ancestors: raise RuntimeError('Cinder target backs the operating system')
    members = [p for p in pvs if p['vg_name'] == VG]
    owners = [p for p in pvs if os.path.realpath(p['pv_name']) == device]
    groups = [g for g in vgs if g['vg_name'] == VG]
    if len(members) != 1 or len(owners) != 1 or owners[0]['vg_name'] != VG or os.path.realpath(members[0]['pv_name']) != device or len(groups) != 1 or groups[0]['pv_count'] != '1':
        raise RuntimeError('Cinder VG must have exactly the reviewed single SATA PV')
    def occupied(entry):
        return any(entry.get('mountpoints') or []) or any(occupied(c) for c in entry.get('children', []))
    if occupied(disk): raise RuntimeError('Cinder target or its logical volumes are mounted')


def snapshot(node):
    if os.geteuid() != 0 or os.environ.get('SUDO_USER') != 'joe' or socket.gethostname().split('.')[0] != node:
        raise RuntimeError('Expected root through joe on the selected node')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if release.get('ID', '').strip(chr(34)) != 'ubuntu' or release.get('VERSION_ID', '').strip(chr(34)) != '24.04':
        raise RuntimeError('Expected Ubuntu 24.04')
    ip, _, alias = TARGETS[node]
    addresses = json.loads(run(['ip', '-j', 'address', 'show']))
    management = next((a for a in addresses if a['ifname'] == 'enp0s31f6'), {})
    if not any(a.get('local') == ip and a.get('prefixlen') == 24 for a in management.get('addr_info', [])):
        raise RuntimeError('Management identity differs')
    containers = json.loads('['+','.join(run(['docker', 'ps', '-a', '--format', '{{json .}}']).splitlines())+']')
    kolla = set(run(['docker', 'ps', '-a', '--filter', 'label=kolla_version', '--format', '{{.Names}}']).splitlines())
    if any(c['Names'] not in kolla for c in containers): raise RuntimeError('Non-Kolla containers exist; reset scope needs review')
    processes = run(['ps', '-eo', 'comm=']).splitlines()
    if any(p.strip().startswith(('qemu-system', 'qemu-kvm')) or p.strip() == 'kvm' for p in processes):
        raise RuntimeError('Running VM process detected; stop and review before destruction')
    namespaces = [line.split()[0] for line in run(['ip', 'netns', 'list']).splitlines()]
    if any(not n.startswith(('qrouter-', 'qdhcp-', 'fip-', 'snat-')) for n in namespaces):
        raise RuntimeError('Unrelated network namespace exists; reset scope needs review')
    alias_path = Path('/dev/disk/by-id')/alias
    if not alias_path.is_symlink(): raise RuntimeError('Reviewed SATA by-id alias is missing')
    device = str(alias_path.resolve(strict=True))
    disk = json.loads(run(['lsblk', '-J', '-b', '--tree', '-o', 'PATH,TYPE,TRAN,SIZE,SERIAL,MOUNTPOINTS', device]))['blockdevices'][0]
    pvs = rows('pvs', 'pv', 'pv_name,pv_uuid,vg_name')
    vgs = rows('vgs', 'vg', 'vg_name,vg_uuid,pv_count,lv_count')
    lvs = rows('lvs', 'lv', 'vg_name,lv_name,lv_uuid,lv_attr')
    ancestors = []; system = []
    for point in ('/', '/boot', '/boot/efi'):
        source = run(['findmnt', '-n', '-o', 'SOURCE', '--target', point])
        tree = run(['lsblk', '-s', '-l', '-n', '-p', '-o', 'PATH', source]).splitlines()
        resolved = [os.path.realpath(p) for p in tree]
        ancestors += resolved; system.append([point, source, resolved])
    guard_disk(node, disk, pvs, vgs, device, ancestors)
    # Reject configured mounts and active block swap anywhere on this SATA's tree.
    children = {os.path.realpath(p) for p in run(['lsblk', '-l', '-n', '-p', '-o', 'PATH', device]).splitlines()}
    fstab = json.loads(run(['findmnt', '--fstab', '--evaluate', '--json', '-o', 'SOURCE,TARGET']))
    for item in fstab.get('filesystems', []):
        if item.get('source', '').startswith('/dev/') and os.path.realpath(item['source']) in children:
            raise RuntimeError('Cinder storage is configured in fstab')
    swaps = [line.split()[0] for line in Path('/proc/swaps').read_text().splitlines()[1:]]
    if any(s.startswith('/dev/') and os.path.realpath(s) in children for s in swaps): raise RuntimeError('Cinder storage is active swap')
    files = [*Path('/etc/netplan').glob('*.yaml'), Path('/etc/ssh/sshd_config'), *Path('/etc/ssh/sshd_config.d').glob('*.conf'),
             Path('/etc/chrony/chrony.conf'), *Path('/etc/chrony').rglob('*.sources')]
    protected = {'system_mounts': system,
                 'management_routes': json.loads(run(['ip', '-j', '-4', 'route', 'show', 'default'])),
                 'management_dns': run(['resolvectl', 'dns', 'enp0s31f6']), 'pvs': [p for p in pvs if p['vg_name'] != VG],
                 'vgs': [g for g in vgs if g['vg_name'] != VG], 'lvs': [v for v in lvs if v['vg_name'] != VG],
                 'config': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}}
    return {'node': node, 'device': device, 'protected': protected, 'kolla_containers': sorted(kolla),
            'namespaces': namespaces, 'ovs_bridges': [a['ifname'] for a in addresses if a['ifname'] in ('br-ex','br-int','br-tun')], 'cinder_lvs': [v for v in lvs if v['vg_name'] == VG],
            'vip': any(a.get('local') == VIP for a in management.get('addr_info', [])),
            'vip_cidr': next((VIP+'/'+str(a['prefixlen']) for a in management.get('addr_info', []) if a.get('local') == VIP), None)}


def clean(node, inspect=snapshot, command=run):
    before = inspect(node)
    if before['kolla_containers'] or before['namespaces'] or before.get('ovs_bridges'): raise RuntimeError('Kolla containers/namespaces remain; no LVM cleanup')
    if any(len(lv['lv_attr']) > 5 and lv['lv_attr'][5] == 'o' for lv in before['cinder_lvs']):
        raise RuntimeError('A Cinder logical volume is still open')
    if before['vip']: command(['ip', 'address', 'delete', before['vip_cidr'], 'dev', 'enp0s31f6'])
    # Recheck target ownership immediately before the single, explicitly scoped LVM write.
    current = inspect(node)
    if current['device'] != before['device'] or current['protected'] != before['protected'] or current['cinder_lvs'] != before['cinder_lvs']:
        raise RuntimeError('Storage state changed before cleanup')
    if current['cinder_lvs']: command(['lvremove', '--yes', VG])
    after = inspect(node)
    if after.get('ovs_bridges') or after['cinder_lvs'] or after['kolla_containers'] or after['namespaces'] or after['vip'] or after['protected'] != before['protected']:
        raise RuntimeError('Fresh-install host verification needs review')
    return after


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--node', choices=TARGETS, required=True)
    p.add_argument('--clean', action='store_true'); args = p.parse_args()
    try:
        result = clean(args.node) if args.clean else snapshot(args.node)
        print(json.dumps(dict(result, ready=True)))
    except Exception as error:
        print(json.dumps({'node': args.node, 'ready': False, 'error': str(error) if isinstance(error, RuntimeError) else type(error).__name__}))
        return 1
    return 0

if __name__ == '__main__': raise SystemExit(main())

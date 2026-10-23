#!/usr/bin/env python3
"""Prepare only the explicitly reviewed SATA disk on a selected lab node."""

import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import shlex
import socket
import stat
import subprocess
import sys
import tempfile

TARGETS = {
    'node1': ('192.168.0.200', '50026B7766070422', 'ata-KINGSTON_SUV400S37240G_50026B7766070422'),
    'node2': ('192.168.0.201', '4P3521354988', 'ata-ADATA_SU630_4P3521354988'),
    'node3': ('192.168.0.202', '50026B768317883A', 'ata-KINGSTON_SA400S37240G_50026B768317883A'),
}
SIZE = 240057409536
VG = 'cinder-volumes'


def run(args):
    print('$ ' + shlex.join(args), flush=True)
    result = subprocess.run(args, text=True, capture_output=True, stdin=subprocess.DEVNULL,
                            timeout=60, env={**os.environ, 'LC_ALL': 'C'})
    if result.stderr.strip():
        print(result.stderr.strip(), flush=True)
    if result.returncode:
        print(result.stdout, flush=True)
        raise RuntimeError('Command stopped with exit ' + str(result.returncode))
    return result.stdout


def report_rows(command, section, columns):
    data = json.loads(run([command, '--readonly', '--reportformat', 'json', '--units', 'b',
                           '--nosuffix', '--options', columns]))
    return [{k: v.strip() if isinstance(v, str) else v for k, v in row.items()}
            for report in data['report'] for row in report.get(section, [])]


def canonical(value):
    return str(Path(value).resolve(strict=True))


def classify(device, disk, signatures, pvs, vgs, lvs, serial):
    if (disk.get('path') != device or disk.get('type') != 'disk' or disk.get('tran') != 'sata'
            or (disk.get('serial') or '').strip() != serial or int(disk.get('size', 0)) != SIZE):
        raise RuntimeError('Disk identity does not match the reviewed target')
    if disk.get('children') or any(disk.get('mountpoints') or []) or disk.get('fstype') not in (None, '', 'LVM2_member'):
        raise RuntimeError('Target has partitions, dependent devices, mounts or another filesystem')
    target_pvs = [p for p in pvs if p['pv_name'] == device]
    groups = [g for g in vgs if g['vg_name'] == VG]
    members = [p for p in pvs if p['vg_name'] == VG]
    types = {s.get('type') for s in signatures}
    if len(target_pvs) > 1 or len(groups) > 1:
        raise RuntimeError('Ambiguous PV/VG ownership')
    if not target_pvs:
        if signatures or groups or members or disk.get('fstype'):
            raise RuntimeError('Signatures or an existing target VG need review')
        return 'blank'
    if types - {'LVM2_member'}:
        raise RuntimeError('Unexpected signatures on an existing physical volume')
    owner = target_pvs[0]['vg_name']
    if not owner and not groups and not members:
        return 'pv-only'
    if (owner == VG and len(groups) == 1 and len(members) == 1
            and int(groups[0]['pv_count']) == 1 and int(groups[0]['lv_count']) == 0
            and not any(v['vg_name'] == VG for v in lvs)):
        return 'ready'
    raise RuntimeError('Target belongs to another, nonempty or unexpected volume group')


def inspect(node):
    address, serial, alias = TARGETS[node]
    target = Path('/dev/disk/by-id') / alias
    device = canonical(target)
    if not stat.S_ISBLK(Path(device).stat().st_mode):
        raise RuntimeError('Target is not a block device')
    disks = json.loads(run(['lsblk', '--json', '--bytes', '--tree', '--output',
                           'PATH,KNAME,TYPE,TRAN,SIZE,SERIAL,FSTYPE,MOUNTPOINTS', device]))['blockdevices']
    if len(disks) != 1:
        raise RuntimeError('Expected exactly one target disk')
    disk = disks[0]
    if list((Path('/sys/class/block') / disk['kname'] / 'holders').iterdir()):
        raise RuntimeError('Kernel holders indicate the target is in use')
    protected_mounts = []
    for point in ('/', '/boot', '/boot/efi'):
        source = run(['findmnt', '--noheadings', '--output', 'SOURCE', '--target', point]).strip()
        ancestors = [canonical(p) for p in run(['lsblk', '--inverse', '--list', '--noheadings', '--paths',
                                               '--output', 'PATH', source]).splitlines() if p.strip()]
        if device in ancestors:
            raise RuntimeError('Target backs an Ubuntu system mount: ' + point)
        protected_mounts.append((point, source, ancestors))
    fstab = json.loads(run(['findmnt', '--fstab', '--evaluate', '--json', '--output', 'SOURCE,TARGET']))
    for mount in fstab.get('filesystems', []):
        source = mount['source']
        if source.startswith('/dev/') and canonical(source) == device:
            raise RuntimeError('Target is configured in fstab')
    swaps = [line.split()[0] for line in Path('/proc/swaps').read_text().splitlines()[1:] if line.split()]
    for source in swaps:
        if source.startswith('/dev/') and canonical(source) == device:
            raise RuntimeError('Target is active swap')
    pvs = report_rows('pvs', 'pv', 'pv_name,pv_uuid,vg_name')
    for pv in pvs:
        pv['pv_name'] = canonical(pv['pv_name'])
    vgs = report_rows('vgs', 'vg', 'vg_name,vg_uuid,pv_count,lv_count')
    lvs = report_rows('lvs', 'lv', 'vg_name,lv_name,lv_uuid,lv_size')
    signatures = json.loads(run(['wipefs', '--no-act', '--json', '--output', 'TYPE', device]))['signatures']
    state = classify(device, disk, signatures, pvs, vgs, lvs, serial)
    # Compare all non-target LVM identities and system mounts after the operation.
    protected = {'mounts': protected_mounts, 'fstab': fstab, 'swaps': swaps,
                 'pvs': [p for p in pvs if p['pv_name'] != device],
                 'vgs': [g for g in vgs if g['vg_name'] != VG],
                 'lvs': [v for v in lvs if v['vg_name'] != VG]}
    print('Target: ' + str(target) + '\nObserved: ' + json.dumps(disk), flush=True)
    print('State: ' + state, flush=True)
    return {'device': device, 'alias': str(target), 'serial': serial, 'state': state,
            'protected': protected, 'pvs': pvs, 'vgs': vgs, 'lvs': lvs}


def prepare(node, apply=False, inspect_fn=inspect, command=run, save=None):
    before = inspect_fn(node)
    print('Planned target: ' + node + ' serial ' + before['serial'] + ' -> ' + VG, flush=True)
    if not apply:
        print('RESULT: LVM_PLAN_READY ' + node + ' — no disk change', flush=True)
        return
    if before['state'] == 'ready':
        print('RESULT: CINDER_LVM_READY ' + node + ' — existing empty pool verified', flush=True)
        return
    if save is not None:
        save(before)
    current = inspect_fn(node)
    if current != before:
        raise RuntimeError('State changed after inspection; no write attempted')
    if before['state'] == 'blank':
        command(['pvcreate', before['alias']])
        current = inspect_fn(node)
        if current['protected'] != before['protected'] or current['state'] != 'pv-only':
            raise RuntimeError('Physical-volume verification needs review')
    command(['vgcreate', VG, before['alias']])
    after = inspect_fn(node)
    if after['protected'] != before['protected'] or after['state'] != 'ready':
        raise RuntimeError('Final volume-group verification needs review')
    print('Verified: one SATA PV, zero LVs, Ubuntu/system LVM unchanged.', flush=True)
    print('RESULT: CINDER_LVM_READY ' + node, flush=True)


def save_evidence(before):
    root = Path('/var/lib/openstack-from-zero/lvm')
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise RuntimeError('Evidence path contains a symlink')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.stat().st_uid != 0:
        raise RuntimeError('Unexpected evidence-directory owner')
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S-')
    directory = Path(tempfile.mkdtemp(prefix=stamp, dir=root))
    path = directory / 'before.json'
    with path.open('x') as output:
        json.dump(before, output, indent=2)
    print('Private pre-change evidence: ' + str(path) + ' (not a disk-data backup)', flush=True)


def main(node, apply=False):
    try:
        os.umask(0o077)
        if os.geteuid() != 0 or node not in TARGETS or socket.gethostname().split('.')[0] != node:
            raise RuntimeError('Expected root on the explicitly selected node')
        release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
        if release.get('ID', '').strip('"') != 'ubuntu' or release.get('VERSION_ID', '').strip('"') != '24.04':
            raise RuntimeError('Expected Ubuntu 24.04')
        addresses = json.loads(run(['ip', '-json', '-4', 'address', 'show']))
        if not any(a.get('local') == TARGETS[node][0] for nic in addresses for a in nic.get('addr_info', [])):
            raise RuntimeError('Expected management IP is absent')
        with open('/run/lock/openstack-from-zero-lvm.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            prepare(node, apply, save=save_evidence)
        return 0
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        print('REVIEW: ' + str(error) + '\nNo automatic rollback or force operation is performed.', file=sys.stderr)
        return 1

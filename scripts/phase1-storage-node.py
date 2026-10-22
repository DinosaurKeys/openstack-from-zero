#!/usr/bin/env python3
"""Privileged, read-only evidence for the known node3 SATA disk. No apply mode."""

import datetime as dt
import json
import os
from pathlib import Path
import shlex
import socket
import stat
import subprocess
import sys


TARGET = Path('/dev/disk/by-id/ata-KINGSTON_SA400S37240G_50026B768317883A')
SERIAL = '50026B768317883A'
SIZE = 240057409536


def run(args):
    return subprocess.run(args, text=True, capture_output=True, timeout=45,
                          env={**os.environ, 'LC_ALL': 'C'})


def checked_json(args):
    result = run(args)
    if result.returncode:
        raise RuntimeError(shlex.join(args) + ': ' + (result.stderr.strip() or str(result.returncode)))
    return json.loads(result.stdout)


def verify_identity(device, facts):
    rows = facts.get('blockdevices', [])
    if len(rows) != 1:
        raise RuntimeError('Expected exactly one target disk')
    disk = rows[0]
    if (disk.get('path') != str(device) or disk.get('type') != 'disk'
            or (disk.get('serial') or '').strip() != SERIAL or int(disk.get('size', -1)) != SIZE):
        raise RuntimeError('Disk identity differs from the reviewed node3 SATA candidate: ' + json.dumps(disk))
    return disk


def flatten(rows, parent=None):
    for row in rows:
        yield row, parent
        yield from flatten(row.get('children', []), row.get('path'))


def show_command(label, args):
    """Keep actual output and exit status; unavailable evidence is never a pass."""
    print('\n' + label + '\n$ ' + shlex.join(args), flush=True)
    try:
        result = run(args)
    except (OSError, subprocess.TimeoutExpired) as error:
        print('REVIEW: ' + str(error))
        return False
    print(result.stdout.strip() or '(no output)')
    if result.stderr.strip():
        print('stderr: ' + result.stderr.strip())
    print('exit status: ' + str(result.returncode))
    if result.returncode:
        print('REVIEW: command returned nonzero; inspect the evidence above.')
    return result.returncode == 0


def collect(device):
    complete = True
    print('\n1. Block devices and their parents (loop devices omitted)')
    inventory = checked_json(['lsblk', '--json', '--bytes', '--exclude', '7', '--output',
                              'NAME,KNAME,PATH,MAJ:MIN,TYPE,SIZE,MODEL,SERIAL,FSTYPE,MOUNTPOINTS'])
    rows = list(flatten(inventory['blockdevices']))
    for row, parent in rows:
        print(json.dumps({key: row.get(key) for key in
                          ('path', 'type', 'size', 'fstype', 'mountpoints')} | {'parent': parent}))

    complete &= show_command('2. Ubuntu mounts',
                             ['findmnt', '--raw', '--output', 'SOURCE,TARGET,FSTYPE', '--target', '/'])
    for point in ('/boot', '/boot/efi'):
        complete &= show_command('Mount backing ' + point,
                                 ['findmnt', '--raw', '--output', 'SOURCE,TARGET,FSTYPE', '--target', point])
    complete &= show_command('Configured mounts (fstab)',
                             ['findmnt', '--fstab', '--evaluate', '--raw', '--output', 'SOURCE,TARGET,FSTYPE'])

    print('\n3. Swap and active kernel RAID')
    for filename in ('/proc/swaps', '/proc/mdstat'):
        print(filename + ':\n' + Path(filename).read_text().strip())
    print('\nTarget and its children: kernel holders and signatures')
    target_rows = list(flatten(checked_json(['lsblk', '--json', '--output', 'NAME,KNAME,PATH', str(device)])['blockdevices']))
    for row, _ in target_rows:
        holders = Path('/sys/class/block') / row['kname'] / 'holders'
        print(row['path'] + ' holders: ' + (', '.join(sorted(p.name for p in holders.iterdir())) or '(none)'))
        complete &= show_command('Read signatures on ' + row['path'],
                                 ['wipefs', '--no-act', '--json', '--output', 'DEVICE,OFFSET,TYPE,UUID,LABEL', row['path']])

    for command, columns in (('pvs', 'pv_name,pv_uuid,vg_name,pv_size,pv_free'),
                             ('vgs', 'vg_name,vg_uuid,pv_count,lv_count,vg_size,vg_free'),
                             ('lvs', 'lv_name,vg_name,lv_size,devices')):
        complete &= show_command('4. Existing LVM: ' + command,
                                 [command, '--readonly', '--reportformat', 'json', '--units', 'b', '--nosuffix', '--options', columns])

    complete &= show_command('5. Existing SMART health data (no self-test started)',
                             ['smartctl', '--all', str(device)])
    print('\nNo disk configuration was changed. This report does not authorize erasing data.')
    return 0 if complete else 1


def main():
    try:
        if os.geteuid() != 0:
            raise RuntimeError('Run this helper through the R2d2 wrapper and sudo')
        if socket.gethostname().split('.')[0].lower() != 'node3':
            raise RuntimeError('Expected node3; refusing another machine')
        release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
        if release.get('ID', '').strip('"') != 'ubuntu' or release.get('VERSION_ID', '').strip('"') != '24.04':
            raise RuntimeError('Expected Ubuntu 24.04')
        device = TARGET.resolve(strict=True)
        if not stat.S_ISBLK(device.stat().st_mode):
            raise RuntimeError('Stable disk identifier does not point to a block device')
        identity = verify_identity(device, checked_json(['lsblk', '--json', '--bytes', '--nodeps', '--output',
                                                        'PATH,TYPE,SIZE,MODEL,SERIAL', str(device)]))
        print('UTC: ' + dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'))
        print('Target: ' + str(TARGET) + '\nResolved: ' + str(device))
        print('Identity matched: ' + json.dumps(identity))
        return collect(device)
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        print('REVIEW: inspection stopped: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

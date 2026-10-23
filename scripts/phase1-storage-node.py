#!/usr/bin/env python3
"""Privileged, read-only discovery and inspection of SATA disks. No apply mode."""

import datetime as dt
import json
import os
from pathlib import Path
import shlex
import socket
import stat
import subprocess
import sys


def run(args):
    return subprocess.run(args, text=True, capture_output=True, timeout=45,
                          env={**os.environ, 'LC_ALL': 'C'})


def checked_json(args):
    result = run(args)
    if result.returncode:
        raise RuntimeError(shlex.join(args) + ': ' + (result.stderr.strip() or str(result.returncode)))
    return json.loads(result.stdout)


def verify_identity(device, facts, expected):
    rows = facts.get('blockdevices', [])
    if len(rows) != 1:
        raise RuntimeError('Expected exactly one target disk')
    disk = rows[0]
    if (disk.get('path') != str(device) or disk.get('type') != 'disk'
            or (disk.get('serial') or '').strip() != (expected.get('serial') or '').strip()
            or int(disk.get('size', -1)) != int(expected['size'])
            or disk.get('model') != expected.get('model') or disk.get('tran') != expected.get('tran')):
        raise RuntimeError('Disk identity changed during this inspection: ' + json.dumps(disk))
    return disk


def discover_sata(facts):
    """Use observed transport, not an assumed /dev/sda name or old serial."""
    disks = [row for row in facts.get('blockdevices', [])
             if row.get('type') == 'disk' and row.get('tran') in {'sata', 'ata'}]
    if not disks:
        raise RuntimeError('No SATA disk identified by lsblk transport; review inventory instead of guessing')
    for disk in disks:
        if (not disk.get('path') or not (disk.get('serial') or '').strip()
                or int(disk.get('size', 0)) <= 0):
            raise RuntimeError('SATA identity is incomplete; review inventory')
    return disks


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


def main(expected_node='node3'):
    try:
        if os.geteuid() != 0:
            raise RuntimeError('Run this helper through the R2d2 wrapper and sudo')
        if expected_node not in {'node1', 'node2', 'node3'}:
            raise RuntimeError('Expected an explicitly selected lab node')
        if socket.gethostname().split('.')[0].lower() != expected_node:
            raise RuntimeError('Expected ' + expected_node + '; refusing another machine')
        release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
        if release.get('ID', '').strip('"') != 'ubuntu' or release.get('VERSION_ID', '').strip('"') != '24.04':
            raise RuntimeError('Expected Ubuntu 24.04')
        print('Node identity: ' + expected_node)
        print('UTC: ' + dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'))
        inventory = checked_json(['lsblk', '--json', '--bytes', '--nodeps', '--output',
                                  'PATH,TYPE,TRAN,SIZE,MODEL,SERIAL'])
        print('Current physical-disk inventory: ' + json.dumps(inventory))
        disks = discover_sata(inventory)
        complete = True
        for disk in disks:
            device = Path(disk['path']).resolve(strict=True)
            if not stat.S_ISBLK(device.stat().st_mode):
                raise RuntimeError('Observed SATA path is not a block device')
            identity = verify_identity(device, checked_json(['lsblk', '--json', '--bytes', '--nodeps', '--output',
                                                            'PATH,TYPE,TRAN,SIZE,MODEL,SERIAL', str(device)]), disk)
            aliases = sorted(str(p) for p in Path('/dev/disk/by-id').glob('*') if p.resolve() == device)
            print('\nSATA identity observed: ' + json.dumps(identity))
            print('Stable identifiers: ' + (', '.join(aliases) or '(none found; review)'))
            complete &= bool(aliases)
            complete &= collect(device) == 0
        return 0 if complete else 1
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        print('REVIEW: inspection stopped: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

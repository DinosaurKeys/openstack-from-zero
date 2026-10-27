#!/usr/bin/env python3
"""Chapter E guest helper: operate only on the identified new Cinder data disk."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import urllib.request
import uuid

MOUNT = Path('/mnt/lab-data')
FILE = 'persistence-test.txt'
SIZE = 1024**3


def run(args, timeout=30):
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env={**os.environ, 'LC_ALL':'C'})
    if p.returncode: raise RuntimeError('Guest command failed: '+args[0]+' (exit '+str(p.returncode)+'); no automatic repair')
    return p.stdout.strip()


def flatten(rows):
    result = []
    for row in rows:
        result.append({k:v for k,v in row.items() if k != 'children'})
        result.extend(flatten(row.get('children', [])))
    return result


def snapshot():
    return flatten(json.loads(run(['lsblk','--json','--bytes','--paths','--tree','--output',
        'NAME,KNAME,TYPE,SIZE,SERIAL,FSTYPE,UUID,MOUNTPOINTS,MAJ:MIN,RO']))['blockdevices'])


def stable(row):
    return {k:row.get(k) for k in ('name','kname','type','size','serial','maj:min')}


def baseline(rows):
    rows = [r for r in rows if r.get('type') != 'loop']
    if len([r for r in rows if '/' in (r.get('mountpoints') or [])]) != 1:
        raise RuntimeError('Cannot identify exactly one guest root filesystem')
    if not any(r.get('type') == 'disk' for r in rows): raise RuntimeError('No baseline root disk observed')
    return [stable(r) for r in rows]


def select(rows, expected, absent=False):
    before = expected['baseline']; names = {r['name'] for r in before}
    current = {r['name']:r for r in rows}
    if len(names) != len(before): raise RuntimeError('Duplicate baseline device')
    for old in before:
        if old['name'] not in current or stable(current[old['name']]) != old:
            raise RuntimeError('Original guest device identity changed; no disk operation')
    serials = {expected['volume_id'], expected['volume_id'][:20]}
    matches = [r for r in rows if (r.get('serial') or '').strip() in serials]
    new = [r for r in rows if r.get('type') == 'disk' and r.get('name') not in names]
    if absent:
        unexpected=[r for r in rows if r.get('type')!='loop' and r.get('name') not in names]
        if matches or unexpected: raise RuntimeError('Detached volume or an unexpected new disk remains visible')
        return None
    if len(matches) != 1 or len(new) != 1 or matches[0] != new[0]:
        raise RuntimeError('New disk is not uniquely matched to the Cinder UUID/virtio serial')
    disk = matches[0]
    if disk['name'] in names or disk.get('type') != 'disk' or disk.get('size') != SIZE or disk.get('ro') not in (False,0):
        raise RuntimeError('Data disk identity/type/size/writeability needs review')
    if not re.fullmatch(r'/dev/[a-zA-Z0-9]+', disk['name']): raise RuntimeError('Unexpected kernel device path')
    # A partition/mapper descendant would also be new: this chapter uses a whole, unpartitioned disk.
    extra = [r for r in rows if r.get('type') != 'loop' and r.get('name') not in names and r['name'] != disk['name']]
    if extra: raise RuntimeError('Unexpected new partition or mapped device')
    return disk


def block_guard(disk):
    path = Path(disk['name']); info = path.lstat()
    if not stat.S_ISBLK(info.st_mode) or str(os.major(info.st_rdev))+':'+str(os.minor(info.st_rdev)) != disk['maj:min']:
        raise RuntimeError('Kernel block identity mismatch')
    holders = Path('/sys/class/block', Path(disk['kname']).name, 'holders')
    if list(holders.iterdir()): raise RuntimeError('Data device has active holders')
    swaps = Path('/proc/swaps').read_text().splitlines()[1:]
    if any(line.split() and os.path.realpath(line.split()[0]) == str(path) for line in swaps):
        raise RuntimeError('Data device is used as swap')


def not_mounted(disk):
    if any(disk.get('mountpoints') or []): raise RuntimeError('Data disk is already mounted')


def blank(disk):
    not_mounted(disk); block_guard(disk)
    if disk.get('fstype') or disk.get('uuid'): raise RuntimeError('New data disk already contains a filesystem')
    p = subprocess.run(['blkid','-p','-o','export',disk['name']], capture_output=True, text=True, timeout=20)
    if p.returncode != 2 or p.stdout.strip(): raise RuntimeError('New data disk has a signature or probe needs review')
    fd = os.open(disk['name'], os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISBLK(info.st_mode) or f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}' != disk['maj:min']:
            raise RuntimeError('Data device changed before blank scan')
        remaining = SIZE
        while remaining:
            chunk = os.read(fd, min(1024**2, remaining))
            if not chunk or chunk.strip(b'\0'): raise RuntimeError('New data disk is not entirely zero-filled; no format')
            remaining -= len(chunk)
        if os.read(fd, 1): raise RuntimeError('Data device size changed')
    finally: os.close(fd)


def mount_directory(mounted=False):
    for path in (Path('/mnt'), MOUNT):
        if not path.exists():
            if mounted: raise RuntimeError('Expected mounted directory missing')
            if path != MOUNT: raise RuntimeError('Expected /mnt directory missing')
            path.mkdir(mode=0o755)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError('Unsafe data mount directory')
    if not mounted and (os.path.ismount(MOUNT) or list(MOUNT.iterdir())):
        raise RuntimeError('Mount directory must be empty and unused')


def filesystem(disk, expected):
    block_guard(disk)
    values = dict(line.split('=',1) for line in run(['blkid','-p','-o','export',disk['name']]).splitlines() if '=' in line)
    if values.get('TYPE') != 'ext4' or values.get('UUID') != expected['filesystem_uuid']:
        raise RuntimeError('Data filesystem type/UUID differs; never reformat')


def mounted(disk, expected):
    filesystem(disk, expected); mount_directory(True)
    entries = json.loads(run(['findmnt','--json','--mountpoint',str(MOUNT),'--output','TARGET,SOURCE,FSTYPE,MAJ:MIN,OPTIONS']))['filesystems']
    if len(entries) != 1 or entries[0].get('maj:min') != disk['maj:min'] or entries[0].get('fstype') != 'ext4' or entries[0].get('target') != str(MOUNT):
        raise RuntimeError('Mount does not refer to the identified data volume')
    if 'rw' not in entries[0].get('options','').split(','): raise RuntimeError('Data filesystem is not mounted read/write')
    if disk.get('mountpoints') != [str(MOUNT)]: raise RuntimeError('Unexpected data-volume mount locations')


def content(expected):
    return ('OpenStack Chapter E persistence test\nserver='+expected['server_id']+'\nvolume='+expected['volume_id']+'\nnonce='+expected['nonce']+'\n').encode()


def check_file(expected):
    path = MOUNT/FILE; info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or info.st_size > 1024:
        raise RuntimeError('Unexpected persistence-test file identity')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        raw = os.read(fd, 1025)
    finally: os.close(fd)
    if raw != content(expected): raise RuntimeError('Persistence test content differs')
    return hashlib.sha256(raw).hexdigest()


def identity(expected):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open('http://169.254.169.254/openstack/latest/meta_data.json', timeout=10) as response:
        raw=response.read(65537)
        if response.status != 200 or len(raw)>65536 or json.loads(raw).get('uuid') != expected['server_id']:
            raise RuntimeError('Guest metadata does not match the tracked VM')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


def execute(expected, action):
    if os.geteuid() != 0: raise RuntimeError('Guest helper requires ubuntu sudo -n')
    if action not in {'baseline','inspect','initialize','write','unmount','unmounted','absent','present','mount','verify'}: raise RuntimeError('Unknown guest operation')
    for key in ('server_id','volume_id','filesystem_uuid'):
        if key in expected and str(uuid.UUID(expected[key])) != expected[key]: raise RuntimeError('Invalid UUID input')
    if not re.fullmatch(r'[0-9a-f]{32}',expected['nonce']): raise RuntimeError('Invalid test nonce')
    for tool in ('lsblk','findmnt','blkid','mkfs.ext4','mount','umount','sync'):
        if not shutil.which(tool): raise RuntimeError('Required existing guest tool missing: '+tool)
    identity(expected)
    rows = snapshot()
    if action == 'baseline':
        # No filesystem changes, even mount-directory creation, in this mode.
        if (MOUNT.exists() or MOUNT.is_symlink()) and (MOUNT.is_symlink() or not MOUNT.is_dir() or os.path.ismount(MOUNT) or list(MOUNT.iterdir())):
            raise RuntimeError('Data mount point already in use')
        return {'baseline':baseline(rows)}
    if action in ('inspect','present','absent'):
        # Only a known hotplug delay is retried. Unknown devices/changed root identities still fail.
        serials={expected['volume_id'],expected['volume_id'][:20]}
        matches=[r for r in rows if (r.get('serial') or '').strip() in serials]
        if not matches:
            select(rows,expected,True)
            if action=='absent':return {'volume_absent':True,'ready':True}
            return {'ready':False}
        disk=select(rows,expected)
        if action=='absent':return {'ready':False}
    else:
        disk=select(rows,expected)

    result = {'device':disk['name'],'serial':disk['serial'],'bytes':disk['size'],'major_minor':disk['maj:min']}
    if action == 'inspect':
        blank(disk); return dict(result, blank=True)
    if action in ('present','unmounted'):
        filesystem(disk,expected);not_mounted(disk)
        return dict(result,ready=True,unmounted=True)
    if action == 'initialize':
        blank(disk); mount_directory()
        run(['mkfs.ext4','-q','-m','0','-U',expected['filesystem_uuid'],'-L','lab-data','-E','nodiscard,lazy_itable_init=0,lazy_journal_init=0',disk['name']], timeout=180)
        filesystem(disk, expected)
        run(['mount','-t','ext4','-o','nodev,nosuid,noexec',disk['name'],str(MOUNT)])
        return dict(result, filesystem_uuid=expected['filesystem_uuid'], initialized=True)
    if action == 'mount':
        not_mounted(disk); filesystem(disk, expected); mount_directory()
        run(['mount','-t','ext4','-o','nodev,nosuid,noexec',disk['name'],str(MOUNT)])
        disk = select(snapshot(), expected)
    mounted(disk, expected)
    if action == 'write':
        fd = os.open(MOUNT/FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd,'wb') as out: out.write(content(expected));out.flush();os.fsync(out.fileno())
    checksum = check_file(expected)
    if action == 'unmount':
        run(['sync','-f',str(MOUNT)]); run(['umount',str(MOUNT)])
        disk = select(snapshot(), expected);not_mounted(disk)
        return dict(result, sha256=checksum, unmounted=True)
    return dict(result, sha256=checksum, filesystem_uuid=expected['filesystem_uuid'], mounted_at=str(MOUNT), file=str(MOUNT/FILE))


def main():
    try:
        expected=json.loads(base64.b64decode(sys.argv[2],validate=True))
        result=dict(execute(expected,sys.argv[1]),passed=True)
    except Exception as error:
        result={'passed':False,'error':str(error) if isinstance(error,RuntimeError) else type(error).__name__+'; guest operation incomplete'}
    print(json.dumps(result));return 0 if result['passed'] else 1

if __name__ == '__main__': raise SystemExit(main())

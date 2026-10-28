#!/usr/bin/env python3
"""Read the tracked data disk/mount/file after reboot; never mount or initialize."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid

MOUNT='/mnt/lab-data'
FILE='persistence-test.txt'


def run(args):
    result=subprocess.run(args,capture_output=True,text=True,timeout=20)
    if result.returncode:raise RuntimeError('Guest read failed: '+args[0])
    return result.stdout


def flatten(rows):
    return [item for row in rows for item in [row,*flatten(row.get('children',[]))]]


def collect(expected):
    if os.geteuid()!=0:raise RuntimeError('Read check requires ubuntu sudo -n')
    for key in ('server_id','volume_id','filesystem_uuid'):
        if str(uuid.UUID(expected[key]))!=expected[key]:raise RuntimeError('Invalid expected UUID')
    if not re.fullmatch(r'[a-f0-9]{64}',expected['sha256']) or not re.fullmatch(r'[a-f0-9]{32}',expected['nonce']):raise RuntimeError('Invalid test identity')
    result={'boot_id':str(uuid.UUID(Path('/proc/sys/kernel/random/boot_id').read_text().strip())),
      'disk_identified':False,'filesystem_matches':False,'mounted':False,'checksum_matches':None}
    disks=flatten(json.loads(run(['lsblk','--json','--bytes','--paths','--tree','--output','NAME,TYPE,SIZE,SERIAL,FSTYPE,UUID,MOUNTPOINTS,MAJ:MIN']))['blockdevices'])
    matches=[d for d in disks if str(d.get('serial') or '').strip() in (expected['volume_id'],expected['volume_id'][:20])]
    if len(matches)!=1:result['issue']='Tracked volume serial not uniquely present';return result
    disk=matches[0]
    if disk.get('type')!='disk' or disk.get('size')!=1024**3 or not re.fullmatch(r'/dev/[A-Za-z0-9]+',disk.get('name','')):
        result['issue']='Tracked disk type/size/path differs';return result
    if any(d is not disk and d.get('name')==disk['name'] for d in disks):raise RuntimeError('Duplicate disk identity')
    result.update(disk_identified=True,device=disk['name'])
    identity=Path(disk['name']).lstat()
    if not stat.S_ISBLK(identity.st_mode) or str(os.major(identity.st_rdev))+':'+str(os.minor(identity.st_rdev))!=disk.get('maj:min'):
        raise RuntimeError('Block device identity differs')
    values=dict(line.split('=',1) for line in run(['blkid','-p','-o','export',disk['name']]).splitlines() if '=' in line)
    result['filesystem_uuid']=values.get('UUID')
    result['filesystem_matches']=values.get('TYPE')=='ext4' and values.get('UUID')==expected['filesystem_uuid']
    if not result['filesystem_matches']:result['issue']='Filesystem UUID/type differs; no format requested';return result
    path=Path(MOUNT)
    if path.is_symlink():raise RuntimeError('Unexpected symlinked mount directory')
    if not os.path.ismount(path):
        result['issue']='Expected filesystem is present but not mounted; data has not been checked';return result
    entries=json.loads(run(['findmnt','--json','--mountpoint',MOUNT,'--output','TARGET,FSTYPE,MAJ:MIN']))['filesystems']
    if len(entries)!=1 or entries[0].get('target')!=MOUNT or entries[0].get('fstype')!='ext4' or entries[0].get('maj:min')!=disk['maj:min']:
        result['issue']='Mount points to a different filesystem';return result
    result['mounted']=True
    test=path/FILE;info=test.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1 or info.st_size>1024:raise RuntimeError('Unexpected saved test-file identity')
    fd=os.open(test,os.O_RDONLY|os.O_NOFOLLOW)
    try:raw=os.read(fd,1025)
    finally:os.close(fd)
    expected_bytes=('OpenStack Chapter E persistence test\nserver='+expected['server_id']+'\nvolume='+expected['volume_id']+'\nnonce='+expected['nonce']+'\n').encode()
    checksum=hashlib.sha256(raw).hexdigest()
    result.update(sha256=checksum,checksum_matches=checksum==expected['sha256'] and raw==expected_bytes)
    if not result['checksum_matches']:result['issue']='Saved test-file checksum/content differs'
    return result


def main():
    try:
        expected=json.loads(base64.b64decode(sys.argv[1],validate=True))
        result=dict(collect(expected),observed=True)
    except Exception as error:
        result={'observed':False,'error':str(error) if isinstance(error,RuntimeError) else type(error).__name__+'; guest read unavailable'}
    print(json.dumps(result));return 0 if result['observed'] else 1


if __name__=='__main__':raise SystemExit(main())

#!/usr/bin/env python3
"""R2d2 only: add confirmed node names to /etc/hosts and verify IPv4 lookups."""

import argparse
import datetime as dt
import os
from pathlib import Path
import pwd
import shlex
import socket
import stat
import subprocess
import sys
import tempfile


NODES = {'node1': '192.168.0.200', 'node2': '192.168.0.201', 'node3': '192.168.0.202'}
BEGIN = '# BEGIN OPENSTACK FROM ZERO NODES'
END = '# END OPENSTACK FROM ZERO NODES'
HOSTS = Path('/etc/hosts')
BACKUPS = Path('/var/lib/openstack-from-zero/hosts')
REPORTS = Path(__file__).resolve().parents[1] / 'reports'


def render_hosts(text):
    """Append missing names; preserve existing lines and refuse conflicting names."""
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.strip() == BEGIN]
    ends = [i for i, line in enumerate(lines) if line.strip() == END]
    if starts or ends:
        if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
            raise RuntimeError('Managed hosts markers need review; no change made')
    present = set()
    for line in lines:
        fields = line.split('#', 1)[0].split()
        if len(fields) < 2:
            continue
        for alias in fields[1:]:
            name = alias.lower()
            if name in NODES:
                if fields[0] != NODES[name]:
                    raise RuntimeError(name + ' already maps to ' + fields[0] + '; no entry was overwritten')
                present.add(name)
    missing = [name for name in NODES if name not in present]
    if not missing:
        return text
    newline = '\r\n' if '\r\n' in text else '\n'
    additions = ''.join(NODES[name] + ' ' + name + newline for name in missing)
    if starts:
        lines.insert(ends[0], additions)
        return ''.join(lines)
    separator = '' if not text or text.endswith(('\n', '\r')) else newline
    return text + separator + BEGIN + newline + additions + END + newline


def read_hosts(path):
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode):
        raise RuntimeError('Expected a regular hosts file, not a symlink or other file type')
    return path.read_bytes(), details


def atomic_write(path, content, details):
    descriptor, temporary = tempfile.mkstemp(prefix='.openstack-hosts-', dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            os.fchown(output.fileno(), details.st_uid, details.st_gid)
            os.fchmod(output.fileno(), stat.S_IMODE(details.st_mode))
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def lookup(name):
    result = subprocess.run(['getent', 'ahostsv4', name], text=True, capture_output=True,
                            timeout=12, env={**os.environ, 'LC_ALL': 'C'})
    if result.returncode:
        raise RuntimeError('Name lookup unavailable for ' + name + ': ' + result.stderr.strip())
    return {line.split()[0] for line in result.stdout.splitlines() if line.split()}


def prepare(path, backup_root, resolver=lookup):
    before, details = read_hosts(path)
    if details.st_uid != os.geteuid():
        raise RuntimeError('Unexpected hosts-file owner; no change made')
    candidate = render_hosts(before.decode('utf-8')).encode('utf-8')
    print('Before /etc/hosts:\n' + before.decode('utf-8').rstrip(), flush=True)
    changed = False
    backup = None
    try:
        if candidate != before:
            if any(parent.is_symlink() for parent in (backup_root, *backup_root.parents)):
                raise RuntimeError('Backup path contains a symlink; no change made')
            backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if backup_root.stat().st_uid != os.geteuid():
                raise RuntimeError('Unexpected backup-directory owner; no change made')
            stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ-')
            directory = Path(tempfile.mkdtemp(prefix=stamp, dir=backup_root))
            backup = directory / 'hosts'
            descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as output:
                output.write(before)
                output.flush()
                os.fsync(output.fileno())
            print('Private backup: ' + str(backup), flush=True)
            current, current_stat = read_hosts(path)
            if current != before or current_stat.st_ino != details.st_ino or current_stat.st_dev != details.st_dev:
                raise RuntimeError('Hosts file changed during preparation; no overwrite made')
            atomic_write(path, candidate, details)
            changed = True
        else:
            print('All three mappings already exist; no write or new backup is needed.', flush=True)
        print('After /etc/hosts:\n' + path.read_bytes().decode('utf-8').rstrip(), flush=True)
        for name, address in NODES.items():
            observed = resolver(name)
            print(name + ' -> ' + (', '.join(sorted(observed)) or '(no IPv4 answer)'), flush=True)
            if observed != {address}:
                raise RuntimeError('Expected only ' + address + ' for ' + name)
        if path.read_bytes() != candidate:
            raise RuntimeError('Hosts file changed during verification')
        print('PASS existing lines preserved, including r2d2, localhost and IPv6 entries.', flush=True)
        print('RESULT: R2D2_HOSTS_READY', flush=True)
        return True
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print('REVIEW: ' + str(error), flush=True)
        if changed:
            if path.read_bytes() == candidate:
                atomic_write(path, before, details)
                print('Previous hosts-file contents restored.', flush=True)
            else:
                print('File changed after our write; automatic restore was not attempted. Backup: ' + str(backup), flush=True)
        return False


def require_r2d2():
    if socket.gethostname().split('.')[0].lower() != 'r2d2':
        raise RuntimeError('Run this step on R2d2 only')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if release.get('ID', '').strip('"') != 'ubuntu' or release.get('VERSION_ID', '').strip('"') != '24.04':
        raise RuntimeError('Expected Ubuntu 24.04 on R2d2')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='back up, add missing node mappings and verify lookups')
    parser.add_argument('--root-helper', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    require_r2d2()
    if args.root_helper:
        if not args.apply or os.geteuid() != 0 or os.environ.get('SUDO_USER') != 'joe':
            raise RuntimeError('The helper is called by the script through sudo as joe')
        return 0 if prepare(HOSTS, BACKUPS) else 1
    if os.geteuid() == 0 or pwd.getpwuid(os.getuid()).pw_name != 'joe' or not sys.stdin.isatty():
        raise RuntimeError('Run as joe in an interactive R2d2 terminal, without putting sudo before this script')
    os.umask(0o077)
    if REPORTS.is_symlink():
        raise RuntimeError('reports must be a real directory')
    REPORTS.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    report = REPORTS / ('phase1-hosts-' + stamp + '.txt')
    descriptor = os.open(report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        def log(text):
            print(text, flush=True)
            output.write(text + '\n')
            output.flush()
        log('Phase 1: node-name mappings on R2d2 only')
        log('Mode: ' + ('APPLY + VERIFY' if args.apply else 'PREVIEW'))
        if args.apply:
            log('Sudo may request the R2d2 joe password. No node connections are made.')
            process = subprocess.Popen(['sudo', '/usr/bin/python3', str(Path(__file__).resolve()), '--root-helper', '--apply'],
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in process.stdout:
                log(line.rstrip('\n'))
            code = process.wait()
            if code:
                log('RESULT: R2D2_HOSTS_NEEDS_REVIEW')
        else:
            before, _ = read_hosts(HOSTS)
            log('Proposed /etc/hosts:\n' + render_hosts(before.decode('utf-8')).rstrip())
            log('RESULT: HOSTS_PREVIEW_ONLY — no system file changed')
            code = 0
    print('Private report: ' + str(report))
    print('Read it with: cat ' + shlex.quote(str(report)))
    return code


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\nInterrupted. Review any printed backup and current hosts file before retrying.', file=sys.stderr)
        sys.exit(130)
    except (OSError, RuntimeError, ValueError) as error:
        print('Stopped: ' + str(error), file=sys.stderr)
        sys.exit(1)

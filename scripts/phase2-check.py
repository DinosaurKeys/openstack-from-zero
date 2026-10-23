#!/usr/bin/env python3
"""Collect R2d2 management-tool facts without installing or changing settings."""

import sys
sys.dont_write_bytecode = True

import argparse
import datetime as dt
import importlib.metadata
import importlib.util
import os
from pathlib import Path
import platform
import pwd
import shlex
import shutil
import socket
import subprocess
import tempfile

REPO = Path(__file__).resolve().parents[1]
REPORTS = REPO / 'reports'
PACKAGES = ('git', 'python3', 'python3-venv', 'python3-dev', 'python3-pip',
            'libffi-dev', 'gcc', 'libssl-dev', 'libdbus-glib-1-dev')


def memory_values(text):
    values = {}
    for line in text.splitlines():
        fields = line.split()
        if fields and fields[0] in ('MemTotal:', 'MemAvailable:'):
            if len(fields) != 3 or fields[2] != 'kB':
                raise RuntimeError('Unexpected memory-unit format')
            values[fields[0].rstrip(':')] = int(fields[1]) * 1024
    if set(values) != {'MemTotal', 'MemAvailable'}:
        raise RuntimeError('Memory facts are incomplete')
    return values


def gib(value):
    return f'{value / 1024**3:.2f} GiB'


def require_r2d2():
    if socket.gethostname().split('.')[0].lower() != 'r2d2':
        raise RuntimeError('Run this check on R2d2 only')
    if os.geteuid() == 0 or pwd.getpwuid(os.getuid()).pw_name != 'joe':
        raise RuntimeError('Run as joe, without sudo')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if release.get('ID', '').strip('"') != 'ubuntu' or release.get('VERSION_ID', '').strip('"') != '24.04':
        raise RuntimeError('Expected the confirmed Ubuntu 24.04 installation')
    return release.get('PRETTY_NAME', 'Ubuntu 24.04').strip('"')


def probe(args):
    executable = shutil.which(args[0])
    if not executable:
        return 'NOT FOUND on current PATH', True
    try:
        result = subprocess.run([executable, *args[1:]], text=True, capture_output=True, timeout=15,
                                env={**os.environ, 'LC_ALL': 'C', 'PYTHONDONTWRITEBYTECODE': '1'})
        text = '\n'.join(s.strip() for s in (result.stdout, result.stderr) if s.strip())
        return str(executable) + '\n' + (text or '(no output)') + '\nexit status: ' + str(result.returncode), result.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as error:
        return 'UNAVAILABLE: ' + str(error), False


def collect(log, pretty):
    complete = True
    log('Phase 2 — Chapter A: R2d2 management-environment inspection')
    log('Mode: READ ONLY; no sudo, SSH connections, installations or configuration changes.')
    log('Only a private report is saved.')
    log('UTC: ' + dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'))
    log('\n1. Machine and Python')
    log('Hostname: ' + socket.gethostname())
    log('OS: ' + pretty)
    log('Architecture: ' + platform.machine() + '; kernel: ' + platform.release())
    log('Logical CPUs available to Python: ' + str(os.cpu_count()))
    memory = memory_values(Path('/proc/meminfo').read_text())
    log('RAM total: ' + gib(memory['MemTotal']) + '; currently available: ' + gib(memory['MemAvailable']))
    for label, path in (('Home', Path.home()), ('Repository', REPO)):
        usage = shutil.disk_usage(path)
        log(label + ' filesystem: total ' + gib(usage.total) + '; free ' + gib(usage.free))
    log('Running Python: ' + sys.executable + '; version: ' + platform.python_version())
    log('Active Python virtual environment: ' + ('yes' if sys.prefix != sys.base_prefix else 'no'))
    log('VIRTUAL_ENV path: ' + os.environ.get('VIRTUAL_ENV', '(not set)'))
    for name in ('venv', 'ensurepip', 'pip'):
        log('Python module ' + name + ': ' + ('available in this interpreter' if importlib.util.find_spec(name) else 'not found in this interpreter'))
    for name in ('pip', 'ansible-core', 'ansible', 'kolla-ansible'):
        try:
            version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            version = 'not installed in this interpreter; other environments are not ruled out'
        log('Python distribution ' + name + ': ' + version)

    log('\n2. Available commands and Ubuntu package state')
    # Do not launch Ansible/Kolla: even version commands can load configuration/plugins.
    for args in (['python3', '--version'], ['git', '--version'], ['ssh', '-V']):
        observed, ok = probe(args)
        complete &= ok
        log('$ ' + shlex.join(args) + '\n' + observed)
    for name in ('ansible', 'ansible-playbook', 'ansible-inventory', 'kolla-ansible', 'kolla-genpwd'):
        log(name + ' executable: ' + (shutil.which(name) or 'not found on current PATH') + ' (not executed)')
    observed, ok = probe(['dpkg-query', '-W', '-f=${binary:Package}\t${Status}\t${Version}\n', *PACKAGES])
    # dpkg-query returns 1 for missing packages: preserve that output as inventory, not an install failure.
    log('$ dpkg-query (selected prerequisites only)\n' + observed)
    if (observed.startswith('UNAVAILABLE:') or observed.startswith('NOT FOUND')
            or (not ok and not observed.rstrip().endswith('exit status: 1'))):
        complete = False

    log('\n3. Known environment/configuration locations — presence only')
    candidates = [Path.home() / 'venvs/kolla', Path.home() / '.venvs/kolla', REPO / '.venv']
    if os.environ.get('VIRTUAL_ENV'):
        candidates.append(Path(os.environ['VIRTUAL_ENV']))
    for path in dict.fromkeys(candidates):
        log(str(path) + ': ' + ('exists; pyvenv.cfg=' + str((path / 'pyvenv.cfg').is_file()) if path.exists() else 'not present'))
    for path in (Path('/etc/kolla'), Path('/etc/kolla/globals.yml'), Path('/etc/kolla/passwords.yml')):
        log(str(path) + ': ' + ('exists' if path.exists() else 'not present or inaccessible'))
    log('Configuration contents, credentials and private keys were not read.')
    log('No network reachability or node readiness was tested in this local check.')
    log('\nRESULT: ' + ('R2D2_STATE_COLLECTED' if complete else 'R2D2_STATE_NEEDS_REVIEW'))
    log('Collection is not version selection, tool installation or approval to deploy.')
    return 0 if complete else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    pretty = require_r2d2()
    os.umask(0o077)
    if REPORTS.is_symlink():
        raise RuntimeError('reports must be a real directory')
    REPORTS.mkdir(mode=0o700, parents=True, exist_ok=True)
    if REPORTS.stat().st_uid != os.geteuid():
        raise RuntimeError('Expected joe to own the report directory')
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-')
    descriptor, filename = tempfile.mkstemp(prefix='phase2-r2d2-' + stamp, suffix='.txt', dir=REPORTS)
    path = Path(filename)
    with os.fdopen(descriptor, 'w') as output:
        def log(line):
            print(line, flush=True)
            output.write(line + '\n')
            output.flush()
        try:
            code = collect(log, pretty)
        except (OSError, RuntimeError, ValueError) as error:
            log('REVIEW: ' + str(error))
            log('RESULT: R2D2_STATE_NEEDS_REVIEW')
            code = 1
    print('Private report: ' + str(path))
    print('Read it with: cat ' + shlex.quote(str(path)))
    return code


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print('Stopped: ' + str(error), file=sys.stderr)
        sys.exit(1)

#!/usr/bin/env python3
"""Install and verify Chrony on R2d2 only. Run as joe, without sudo in front."""

import argparse
import codecs
import datetime as dt
import os
from pathlib import Path
import pwd
import shlex
import socket
import subprocess
import sys

REPORTS = Path(__file__).resolve().parents[1] / 'reports'


def require_r2d2():
    if socket.gethostname().split('.')[0].lower() != 'r2d2':
        raise RuntimeError('Run this script on R2d2 only')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if release.get('ID', '').strip('"') != 'ubuntu' or release.get('VERSION_ID', '').strip('"') != '24.04':
        raise RuntimeError('Expected Ubuntu 24.04 on R2d2')


def command(argv, check=True, timeout=60, interactive=False):
    print('Run: ' + shlex.join(argv), flush=True)
    result = subprocess.run(argv, text=True, stdout=None if interactive else subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=timeout, env={**os.environ, 'LC_ALL': 'C'})
    if not interactive:
        print(result.stdout, end='' if result.stdout.endswith('\n') else '\n', flush=True)
    if check and result.returncode:
        raise RuntimeError('Command returned ' + str(result.returncode) + ': ' + shlex.join(argv))
    return result


def check_plan(text):
    """Only the observed Ubuntu time-client replacement may be removed."""
    removals = [line.split()[1].split(':')[0] for line in text.splitlines() if line.startswith('Remv ')]
    unexpected = sorted(set(removals) - {'systemd-timesyncd'})
    if unexpected:
        raise RuntimeError('Package removals need review: ' + ', '.join(unexpected))


def prepare(run=command):
    run(['timedatectl', 'status'])
    package = run(['dpkg-query', '-W', '-f=${Status}', 'chrony'], check=False)
    if package.returncode or package.stdout.strip() != 'install ok installed':
        run(['apt-get', '-o', 'APT::Update::Error-Mode=any', 'update'], timeout=1800, interactive=True)
        options = ['--no-install-recommends', '--no-upgrade']
        plan = run(['apt-get', '--simulate', *options, 'install', 'chrony'])
        check_plan(plan.stdout)
        print('Install Chrony. The package manager may replace systemd-timesyncd. Review its plan; answer Y to continue.', flush=True)
        # No -y: Joe sees and accepts the actual package transaction.
        # Retain any existing local package configuration, including on a rebuild.
        run(['apt-get', '-o', 'Dpkg::Options::=--force-confold', *options, 'install', 'chrony'], timeout=1800, interactive=True)
    else:
        print('Chrony is already installed; no package update or reinstall requested.', flush=True)
    run(['systemctl', 'enable', '--now', 'chrony.service'])
    run(['systemctl', 'is-active', 'chrony.service'])
    enabled = run(['systemctl', 'is-enabled', 'chrony.service'])
    if enabled.stdout.strip() != 'enabled':
        raise RuntimeError('Chrony is not enabled for boot')
    other = run(['systemctl', 'is-active', 'systemd-timesyncd.service'], check=False)
    if other.returncode == 0 or other.stdout.strip() not in {'inactive', 'unknown', 'failed'}:
        raise RuntimeError('Could not confirm systemd-timesyncd is inactive')
    print('Wait up to about 48 seconds for synchronization and less than 0.1 second remaining clock correction.', flush=True)
    sync = run(['chronyc', 'waitsync', '24', '0.1', '0', '2'], check=False, timeout=55)
    tracking = run(['chronyc', 'tracking'])
    sources = run(['chronyc', '-n', 'sources', '-v'])
    run(['timedatectl', 'status'])
    if sync.returncode:
        raise RuntimeError('Chrony did not meet the synchronization check in this waiting period')
    if not any(line.startswith('Leap status') and line.split(':', 1)[1].strip() == 'Normal'
               for line in tracking.stdout.splitlines()):
        raise RuntimeError('Chrony tracking does not report normal synchronization')
    if not any(len(line) > 1 and line[1] == '*' for line in sources.stdout.splitlines()):
        raise RuntimeError('Chrony has no selected time source')
    print('RESULT: R2D2_CHRONY_READY', flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='install, enable and verify Chrony locally')
    parser.add_argument('--root-helper', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    require_r2d2()
    if args.root_helper:
        if not args.apply or os.geteuid() != 0 or os.environ.get('SUDO_USER') != 'joe':
            raise RuntimeError('Helper requires sudo from joe and --apply')
        prepare()
        return 0
    if os.geteuid() == 0 or pwd.getpwuid(os.getuid()).pw_name != 'joe' or not sys.stdin.isatty():
        raise RuntimeError('Run as joe in an interactive terminal on R2d2; do not prefix this script with sudo')
    if not args.apply:
        print('Preview: install Chrony if absent; let APT replace systemd-timesyncd; enable Chrony; verify synchronization.')
        print('No system change made. Read chapter 1.3d, then use --apply.')
        return 0
    os.umask(0o077)
    if REPORTS.is_symlink():
        raise RuntimeError('reports must be a real directory')
    REPORTS.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    report = REPORTS / ('phase1-chrony-' + stamp + '.txt')
    descriptor = os.open(report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        def log(line):
            print(line, flush=True)
            output.write(line + '\n')
            output.flush()
        log('Phase 1: Chrony on R2d2 only — APPLY + VERIFY')
        log('Sudo password: joe on R2d2. APT may ask Y/n. No node connections.')
        # sudo uses the controlling terminal; the report never records passwords.
        process = subprocess.Popen(['sudo', '/usr/bin/python3', '-u', str(Path(__file__).resolve()), '--root-helper', '--apply'],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        while True:
            chunk = os.read(process.stdout.fileno(), 4096)
            text = decoder.decode(chunk, final=not chunk)
            print(text, end='', flush=True)
            output.write(text)
            output.flush()
            if not chunk:
                break
        process.stdout.close()
        code = process.wait()
        if code:
            log('RESULT: R2D2_CHRONY_NEEDS_REVIEW — keep this report; no automatic package rollback attempted')
    print('Private report: ' + str(report))
    print('Read it with: cat ' + shlex.quote(str(report)))
    return code


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print('Stopped: ' + str(error), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('\nInterrupted. Keep the report and review current service/package state before continuing.', file=sys.stderr)
        sys.exit(130)

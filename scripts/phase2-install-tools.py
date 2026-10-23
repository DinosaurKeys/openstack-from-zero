#!/usr/bin/env python3
"""Install the agreed management tools on R2d2; default is plan only."""
import sys
sys.dont_write_bytecode = True
import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import pwd
import shlex
import socket
import subprocess
import tempfile

REPO = Path(__file__).resolve().parents[1]
ENV = Path('/home/joe/venvs/kolla')
COMMIT = '4b493ef36ace2b90257ecf94feb00e2c6e8014c9'
SPEC = {'project': 'openstack-from-zero', 'kolla_commit': COMMIT,
        'kolla_version': '23.0.0', 'ansible_core': '2.21.5'}
PACKAGES = ['git', 'python3-dev', 'libffi-dev', 'gcc', 'libssl-dev',
            'libdbus-glib-1-dev', 'python3-venv']
MARKER = '.openstack-from-zero-tools.json'


def check_paths(env):
    for p in [env, *env.parents]:
        if p.is_symlink():
            raise RuntimeError('Environment path must not contain symlinks: ' + str(p))
    if env.exists():
        marker = env / MARKER
        if (not env.is_dir() or env.stat().st_uid != os.getuid()
                or marker.is_symlink() or not marker.is_file()
                or json.loads(marker.read_text()) != SPEC):
            raise RuntimeError('Existing environment is not this agreed setup; left unchanged')


def guard():
    if os.geteuid() == 0 or pwd.getpwuid(os.getuid()).pw_name != 'joe':
        raise RuntimeError('Run as joe, without sudo')
    if socket.gethostname().split('.')[0].lower() != 'r2d2':
        raise RuntimeError('Run on R2d2 only')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if release.get('ID', '').strip('"') != 'ubuntu' or release.get('VERSION_ID', '').strip('"') != '24.04':
        raise RuntimeError('Expected Ubuntu 24.04')
    if sys.version_info[:2] != (3, 12) or sys.prefix != sys.base_prefix or Path.home() != Path('/home/joe'):
        raise RuntimeError('Use system Python 3.12 as joe with home /home/joe')
    check_paths(ENV)


class Runner:
    def __init__(self, log, env):
        self.log, self.env = log, env

    def run(self, args, terminal=False):
        self.log('$ ' + shlex.join(map(str, args)))
        args = list(map(str, args))
        if terminal:
            # Keep sudo and APT's interactive prompts on the user's terminal.
            result = subprocess.run(args, env=self.env)
            self.log('Exit status: ' + str(result.returncode) + ' (interactive output shown in terminal)')
        else:
            result = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, env=self.env,
                                    cwd=ENV if ENV.is_dir() else REPO)
            self.log(result.stdout.rstrip())
        if result.returncode:
            raise RuntimeError('Command stopped with exit ' + str(result.returncode) + ': ' + args[0])
        return getattr(result, 'stdout', '') or ''


def verify_versions(observed):
    if observed['kolla'] != SPEC['kolla_version'] or observed['ansible'] != SPEC['ansible_core']:
        raise RuntimeError('Installed versions do not match the agreed set')
    if observed['commit'] != COMMIT or observed['prefix'] != str(ENV):
        raise RuntimeError('Installer provenance or environment does not match')


def install(run, log):
    log('1. Refresh Ubuntu package list; sudo may ask for joe\'s R2d2 password.')
    run.run(['sudo', 'apt-get', '-o', 'APT::Update::Error-Mode=any', 'update'], terminal=True)
    log('2. Review the prerequisite package plan.')
    plan = run.run(['apt-get', '--simulate', '--no-install-recommends', '--no-upgrade', 'install', *PACKAGES])
    if any(line.startswith('Remv ') for line in plan.splitlines()):
        raise RuntimeError('APT proposes package removal; stopped before installation')
    log('APT will ask Y/n if packages are needed. Answer Y to install these prerequisites.')
    run.run(['sudo', 'apt-get', '--no-remove', '--no-install-recommends', '--no-upgrade', 'install', *PACKAGES], terminal=True)
    log('3. Create/reuse only this project-marked environment; never delete it.')
    check_paths(ENV)
    if not ENV.exists():
        ENV.mkdir(parents=True, mode=0o700)
        (ENV / MARKER).write_text(json.dumps(SPEC, sort_keys=True) + '\n')
    if not (ENV / 'pyvenv.cfg').is_file() or not (ENV / 'bin/python').exists():
        run.run(['/usr/bin/python3', '-m', 'venv', ENV])
    log('4. Install pip and the agreed tools inside the environment, as joe.')
    pip = [ENV / 'bin/python', '-m', 'pip', '--isolated', '--disable-pip-version-check']
    run.run([*pip, 'install', '--upgrade', 'pip'])
    run.run([*pip, 'install', 'ansible-core==2.21.5',
             'git+https://github.com/openstack/kolla-ansible.git@' + COMMIT])
    log('5. Verify versions, source commit and dependency consistency before downloading collections.')
    code = '''import importlib.metadata as m,json,sys
k=m.distribution('kolla-ansible')
d=json.loads(k.read_text('direct_url.json'))
print(json.dumps({'kolla':k.version,'ansible':m.version('ansible-core'),'prefix':sys.prefix,'commit':d.get('vcs_info',{}).get('commit_id')}))'''
    observed = json.loads(run.run([ENV / 'bin/python', '-c', code]).strip())
    verify_versions(observed)
    run.run([*pip, 'check'])
    log('6. Download Kolla Ansible collections into the dedicated environment (no node tasks).')
    run.run([ENV / 'bin/kolla-ansible', 'install-deps'])
    run.run([ENV / 'bin/ansible', '--version'])
    run.run([ENV / 'bin/kolla-ansible', '--version'])
    collections = json.loads(run.run([ENV / 'bin/ansible-galaxy', 'collection', 'list',
                                     '-p', ENV / 'collections', '--format', 'json']))
    names = {name for group in collections.values() for name in group}
    required = {'openstack.kolla', 'ansible.netcommon', 'ansible.posix', 'ansible.utils',
                'community.crypto', 'community.general', 'community.docker', 'containers.podman'}
    if not required.issubset(names):
        raise RuntimeError('Required collections are missing: ' + ', '.join(sorted(required - names)))
    log('7. Save private dependency records and the activation helper.')
    frozen = run.run([*pip, 'freeze', '--all'])
    (ENV / 'installed-python-packages.txt').write_text(frozen)
    (ENV / 'installed-ansible-collections.json').write_text(json.dumps(collections, indent=2) + '\n')
    helper = ('# Source this file in Bash; it activates the lab tools and collection path.\n'
              'source /home/joe/venvs/kolla/bin/activate\n'
              'export ANSIBLE_COLLECTIONS_PATH=/home/joe/venvs/kolla/collections\n'
              'export ANSIBLE_HOME=/home/joe/venvs/kolla/ansible-home\n'
              'export ANSIBLE_CONFIG=/dev/null\n')
    (ENV / 'activate-lab.sh').write_text(helper)
    log('RESULT: R2D2_TOOLS_READY')
    log('OpenStack series 2026.2 selected; no OpenStack services deployed.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='install the agreed tools on R2d2')
    args = parser.parse_args(argv)
    guard()
    if not args.apply:
        print('PLAN ONLY: Ubuntu prerequisites: ' + ', '.join(PACKAGES))
        print('Environment: ' + str(ENV))
        print('Kolla-Ansible 23.0.0 at ' + COMMIT + '; ansible-core 2.21.5')
        print('Apply with --apply after reading Phase 2 Chapter C. No changes made.')
        return 0
    if not sys.stdin.isatty():
        raise RuntimeError('Apply needs an interactive terminal for sudo/APT prompts')
    os.umask(0o077)
    reports = REPO / 'reports'
    if reports.is_symlink():
        raise RuntimeError('reports must be a real directory')
    reports.mkdir(mode=0o700, exist_ok=True)
    if reports.stat().st_uid != os.getuid():
        raise RuntimeError('Expected joe to own reports')
    with (reports / '.phase2-tools.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-')
        fd, filename = tempfile.mkstemp(prefix='phase2-tools-' + stamp, suffix='.txt', dir=reports)
        with os.fdopen(fd, 'w') as report:
            def log(line):
                print(line, flush=True)
                report.write(line + '\n'); report.flush()
            log('Phase 2 — Chapter C: management tools on R2d2 only — APPLY + VERIFY')
            log('Sudo/APT output stays on the terminal; command/status and tool outputs are recorded.')
            # Avoid unrelated Ansible config/plugins and inherited pip/venv selection.
            env = {k: v for k, v in os.environ.items() if not k.startswith(('ANSIBLE_', 'PIP_', 'PYTHON', 'VIRTUAL_ENV'))}
            env.update(LC_ALL='C', PATH=str(ENV / 'bin') + ':/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
                       ANSIBLE_CONFIG='/dev/null', ANSIBLE_HOME=str(ENV / 'ansible-home'),
                       ANSIBLE_COLLECTIONS_PATH=str(ENV / 'collections'), PYTHONDONTWRITEBYTECODE='1')
            try:
                install(Runner(log, env), log)
                status = 0
            except (OSError, RuntimeError, ValueError, KeyError, KeyboardInterrupt) as error:
                log('Stopped: ' + str(error))
                log('RESULT: R2D2_TOOLS_NEEDS_REVIEW')
                log('No environment or packages were automatically removed. Return this report before another step.')
                status = 1
        print('Private report: ' + filename)
        print('Read it with: cat ' + shlex.quote(filename))
        return status


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print('Stopped: ' + str(error), file=sys.stderr)
        sys.exit(1)

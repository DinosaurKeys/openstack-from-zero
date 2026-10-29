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
CLIENT_ENV = Path('/home/joe/venvs/openstack-client')
CLIENT_VERSION = '10.3.1'
CLIENT_MARKER = '.openstack-from-zero-client.json'
CLIENT_SPEC = {'project': 'openstack-from-zero', 'python_openstackclient': CLIENT_VERSION}
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


def check_client_path():
    for path in [CLIENT_ENV, *CLIENT_ENV.parents]:
        if path.is_symlink():
            raise RuntimeError('Client environment path must not contain symlinks: ' + str(path))
    if not CLIENT_ENV.exists():
        return
    if not CLIENT_ENV.is_dir() or CLIENT_ENV.stat().st_uid != os.getuid():
        raise RuntimeError('Expected joe to own the client environment; left unchanged')
    marker, config = CLIENT_ENV / CLIENT_MARKER, CLIENT_ENV / 'pyvenv.cfg'
    for path in (marker, config, CLIENT_ENV / 'installed-python-packages.txt'):
        if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_uid != os.getuid())):
            raise RuntimeError('Unexpected client environment file; left unchanged: ' + str(path))
    binary_dir = CLIENT_ENV / 'bin'
    if binary_dir.is_symlink() or (binary_dir.exists() and (not binary_dir.is_dir() or binary_dir.stat().st_uid != os.getuid())):
        raise RuntimeError('Unexpected client executable directory; left unchanged')
    if marker.exists():
        if json.loads(marker.read_text()) != CLIENT_SPEC:
            raise RuntimeError('Unexpected client environment marker; left unchanged')
    elif not config.is_file():
        raise RuntimeError('Existing client directory is not a Python environment; left unchanged')
    if config.is_file():
        settings = dict(line.strip().split('=', 1) for line in config.read_text().splitlines() if '=' in line)
        settings = {key.strip(): value.strip().lower() for key, value in settings.items()}
        if settings.get('include-system-site-packages') != 'false':
            raise RuntimeError('Client environment must exclude system packages; left unchanged')


def guard(client_only=False):
    if os.geteuid() == 0 or pwd.getpwuid(os.getuid()).pw_name != 'joe':
        raise RuntimeError('Run as joe, without sudo')
    if socket.gethostname().split('.')[0].lower() != 'r2d2':
        raise RuntimeError('Run on R2d2 only')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if release.get('ID', '').strip('"') != 'ubuntu' or release.get('VERSION_ID', '').strip('"') != '24.04':
        raise RuntimeError('Expected Ubuntu 24.04')
    if sys.version_info[:2] != (3, 12) or sys.prefix != sys.base_prefix or Path.home() != Path('/home/joe'):
        raise RuntimeError('Use system Python 3.12 as joe with home /home/joe')
    if not client_only:
        check_paths(ENV)
    check_client_path()


def tool_environment():
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('ANSIBLE_', 'PIP_', 'PYTHON', 'VIRTUAL_ENV', 'OS_'))}
    # Ansible requires a UTF-8 locale; plain C is ASCII.
    # These variables affect this process's children, not Ubuntu's saved locale.
    env.update(LC_ALL='C.UTF-8', LANG='C.UTF-8',
               PATH=str(ENV / 'bin') + ':/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
               ANSIBLE_CONFIG=str(ENV / 'chapter-c-ansible.cfg'), ANSIBLE_HOME=str(ENV / 'ansible-home'),
               ANSIBLE_COLLECTIONS_PATH=str(ENV / 'collections'),
               PYTHONDONTWRITEBYTECODE='1')
    return env


def prepare_ansible_config():
    path = ENV / 'chapter-c-ansible.cfg'
    content = '# Managed by openstack-from-zero Chapter C\n[defaults]\n'
    if path.is_symlink() or (path.exists() and path.read_text() != content):
        raise RuntimeError('Unexpected Chapter C Ansible config; left unchanged')
    if not path.exists():
        with path.open('x') as output:
            output.write(content)


class Runner:
    def __init__(self, log, env):
        self.log, self.env = log, env

    def run(self, args, terminal=False, quiet=False):
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
            if not quiet or result.returncode:
                self.log(result.stdout.rstrip())
        if result.returncode:
            raise RuntimeError('Command stopped with exit ' + str(result.returncode) + ': ' + args[0])
        return getattr(result, 'stdout', '') or ''


def verify_versions(observed):
    if observed['kolla'] != SPEC['kolla_version'] or observed['ansible'] != SPEC['ansible_core']:
        raise RuntimeError('Installed versions do not match the agreed set')
    if observed['commit'] != COMMIT or observed['prefix'] != str(ENV):
        raise RuntimeError('Installer provenance or environment does not match')


def install_client(run, log):
    """Install the standard CLI locally; no credentials or cloud requests are used."""
    check_client_path()
    log('OpenStack CLI: install/verify ' + CLIENT_VERSION + ' in ' + str(CLIENT_ENV))
    if not CLIENT_ENV.exists():
        CLIENT_ENV.mkdir(parents=True, mode=0o700)
        (CLIENT_ENV / CLIENT_MARKER).write_text(json.dumps(CLIENT_SPEC, sort_keys=True) + '\n')
    if not (CLIENT_ENV / 'pyvenv.cfg').is_file() or not (CLIENT_ENV / 'bin/python').exists():
        run.run(['/usr/bin/python3', '-m', 'venv', CLIENT_ENV])
    code = '''import importlib.metadata as m,json,sys
try: client=m.version('python-openstackclient')
except m.PackageNotFoundError: client=None
print(json.dumps({'client':client,'prefix':sys.prefix,'base_prefix':sys.base_prefix,'python':list(sys.version_info[:2])}))'''
    observed = json.loads(run.run([CLIENT_ENV / 'bin/python', '-I', '-c', code]).strip())
    if (observed['prefix'] != str(CLIENT_ENV) or observed['base_prefix'] == observed['prefix']
            or observed['python'] != [3, 12]):
        raise RuntimeError('Expected an isolated Python 3.12 client environment; left unchanged')
    pip = [CLIENT_ENV / 'bin/python', '-m', 'pip', '--isolated', '--disable-pip-version-check']
    if observed['client'] != CLIENT_VERSION:
        log('Downloading the pinned CLI and its dependencies; this can take several minutes.')
        run.run([*pip, 'install', 'python-openstackclient==' + CLIENT_VERSION])
    else:
        log('Pinned OpenStack CLI already installed; checking it without reinstalling.')
    run.run([*pip, 'check'])
    version = run.run([CLIENT_ENV / 'bin/openstack', '--version']).strip()
    if version != 'openstack ' + CLIENT_VERSION:
        raise RuntimeError('OpenStack CLI executable did not report the pinned version')
    for action in ('list', 'start', 'stop'):
        run.run([CLIENT_ENV / 'bin/openstack', 'help', 'server', action], quiet=True)
    frozen = run.run([*pip, 'freeze', '--all'], quiet=True)
    (CLIENT_ENV / 'installed-python-packages.txt').write_text(frozen)
    (CLIENT_ENV / CLIENT_MARKER).write_text(json.dumps(CLIENT_SPEC, sort_keys=True) + '\n')
    log('PASS: OpenStack CLI version, dependencies and list/start/stop command help.')
    log('Activate in this terminal: source /home/joe/venvs/openstack-client/bin/activate')


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
    log('6. Check the dedicated Ansible config, then download collections (no node tasks).')
    prepare_ansible_config()
    run.run([ENV / 'bin/ansible', '--version'])
    run.run([ENV / 'bin/ansible-config', 'dump', '--only-changed'])
    log('Collection downloads may take several minutes; output appears when the command finishes.')
    run.run([ENV / 'bin/kolla-ansible', 'install-deps'])
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
              'export LC_ALL=C.UTF-8 LANG=C.UTF-8\n'
              'export ANSIBLE_COLLECTIONS_PATH=/home/joe/venvs/kolla/collections\n'
              'export ANSIBLE_HOME=/home/joe/venvs/kolla/ansible-home\n'
              'export ANSIBLE_CONFIG=/home/joe/venvs/kolla/chapter-c-ansible.cfg\n')
    (ENV / 'activate-lab.sh').write_text(helper)
    log('8. Install and verify the OpenStack CLI in its separate client environment.')
    install_client(run, log)
    log('RESULT: R2D2_TOOLS_READY')
    log('OpenStack series 2026.2 selected; no OpenStack services deployed.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='install the agreed tools on R2d2')
    parser.add_argument('--client-only', action='store_true', help='install/verify only the OpenStack CLI on an already-prepared R2d2')
    args = parser.parse_args(argv)
    guard(client_only=args.client_only)
    if not args.apply:
        if not args.client_only:
            print('PLAN ONLY: Ubuntu prerequisites: ' + ', '.join(PACKAGES))
            print('Environment: ' + str(ENV))
            print('Kolla-Ansible 23.0.0 at ' + COMMIT + '; ansible-core 2.21.5')
        else:
            print('PLAN ONLY: OpenStack CLI installation only; requires the existing python3-venv prerequisite.')
        print('OpenStack CLI ' + CLIENT_VERSION + ': ' + str(CLIENT_ENV))
        print('Apply with --apply after reading Phase 2 Chapter C. No changes made.')
        return 0
    if not args.client_only and not sys.stdin.isatty():
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
            log('Phase 2 — Chapter C: ' + ('OpenStack CLI only' if args.client_only else 'management tools') + ' on R2d2 — APPLY + VERIFY')
            if not args.client_only:
                log('Sudo/APT output stays on the terminal; command/status and tool outputs are recorded.')
            # Avoid unrelated Ansible config/plugins and inherited pip/venv selection.
            env = tool_environment()
            try:
                run = Runner(log, env)
                if args.client_only:
                    install_client(run, log)
                    log('RESULT: OPENSTACK_CLI_READY')
                    log('Local client installation verified. API login and VM power actions were not requested.')
                else:
                    install(run, log)
                status = 0
            except (OSError, RuntimeError, ValueError, KeyError, KeyboardInterrupt) as error:
                log('Stopped: ' + str(error))
                log('RESULT: ' + ('OPENSTACK_CLI_NEEDS_REVIEW' if args.client_only else 'R2D2_TOOLS_NEEDS_REVIEW'))
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

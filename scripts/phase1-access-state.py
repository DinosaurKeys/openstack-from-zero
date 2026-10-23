#!/usr/bin/env python3
"""R2d2: read SSH-key, Chrony and hostname state; save one private report."""

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys


NODES = {'node1': '192.168.0.200', 'node2': '192.168.0.201', 'node3': '192.168.0.202'}
REPORT_DIR = Path(__file__).resolve().parents[1] / 'reports'

# Only monitoring commands and readable system files. No sudo or installation.
COLLECTOR = r'''
import json, os, pwd, socket, subprocess
from pathlib import Path

def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=12,
                                env={**os.environ, 'LC_ALL': 'C'})
        return {'exit': result.returncode, 'out': result.stdout.strip(), 'err': result.stderr.strip()}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'exit': None, 'out': '', 'err': str(error)}

def read(path):
    try:
        return {'text': Path(path).read_text().strip(), 'error': None}
    except OSError as error:
        return {'text': '', 'error': str(error)}

release = read('/etc/os-release')
os_info = dict(line.split('=', 1) for line in release['text'].splitlines() if '=' in line)
facts = {
    'hostname': socket.gethostname().split('.')[0].lower(),
    'username': pwd.getpwuid(os.getuid()).pw_name,
    'os': {key: os_info.get(key, '').strip('"') for key in ('ID', 'VERSION_ID')},
    'addresses': command(['ip', '-j', 'address', 'show']),
    'hosts_file': read('/etc/hosts'),
    'lookups': {name: command(['getent', 'ahostsv4', name])
                for name in ('r2d2', 'node1', 'node2', 'node3')},
    'chrony_package': command(['dpkg-query', '-W', '-f=${Status}', 'chrony']),
    'chrony_service': command(['systemctl', 'show', 'chrony.service',
                               '--property=LoadState', '--property=ActiveState', '--property=UnitFileState']),
    'timesyncd_service': command(['systemctl', 'show', 'systemd-timesyncd.service',
                                  '--property=LoadState', '--property=ActiveState']),
    'clock_sync': command(['timedatectl', 'show', '--property=NTPSynchronized', '--value']),
    'chrony_tracking': command(['chronyc', '-n', 'tracking']),
    'chrony_sources': command(['chronyc', '-n', 'sources']),
}
print(json.dumps(facts))
'''


def command(args, timeout=15):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                                env={**os.environ, 'LC_ALL': 'C'})
        return {'exit': result.returncode, 'out': result.stdout.strip(), 'err': result.stderr.strip()}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'exit': None, 'out': '', 'err': str(error)}


def local_keys(home):
    """List public fingerprints and counterpart existence; never read private files."""
    ssh_dir = home / '.ssh'
    keys = []
    if ssh_dir.exists():
        for public in sorted(ssh_dir.glob('*.pub')):
            if public.is_symlink() or not public.is_file():
                continue
            counterpart = public.with_suffix('')
            keys.append({'public_file': str(public),
                         'counterpart_file_exists': counterpart.is_file(),
                         'fingerprint': command(['ssh-keygen', '-lf', str(public)])})
    return {'public_keys': keys, 'agent': command(['ssh-add', '-l'])}


def ssh_arguments(username, address, key_only):
    args = ['ssh', '-T', '-o', 'ConnectTimeout=8', '-o', 'ConnectionAttempts=1',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'UpdateHostKeys=no',
            '-o', 'ControlMaster=no', '-o', 'ControlPath=none', '-o', 'ControlPersist=no',
            '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no', '-o', 'ClearAllForwardings=yes',
            '-o', 'PermitLocalCommand=no', '-o', 'RemoteCommand=none', '-o', 'AddKeysToAgent=no']
    if key_only:
        args += ['-o', 'BatchMode=yes', '-o', 'PreferredAuthentications=publickey',
                 '-o', 'PubkeyAuthentication=yes', '-o', 'PasswordAuthentication=no',
                 '-o', 'KbdInteractiveAuthentication=no']
    else:
        args += ['-o', 'BatchMode=no', '-o', 'PubkeyAuthentication=no',
                 '-o', 'PreferredAuthentications=keyboard-interactive,password',
                 '-o', 'PasswordAuthentication=yes', '-o', 'KbdInteractiveAuthentication=yes',
                 '-o', 'NumberOfPasswordPrompts=1']
    return args + [username + '@' + address, 'python3 -']


def ssh_collect(username, address, key_only):
    try:
        result = subprocess.run(ssh_arguments(username, address, key_only), input=COLLECTOR,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=150 if not key_only else 100)
        facts = None
        if result.returncode == 0:
            try:
                facts = json.loads(result.stdout)
                if not isinstance(facts, dict):
                    facts = None
            except ValueError:
                pass
        return {'exit': result.returncode, 'facts': facts, 'error': result.stderr.strip(),
                'invalid_output': result.returncode == 0 and facts is None}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'exit': None, 'facts': None, 'error': str(error), 'invalid_output': False}


def inspect_node(username, address):
    key = ssh_collect(username, address, True)
    if key['exit'] == 0:
        return {'key_attempt': key, 'used': 'public key', 'facts': key['facts'], 'error': key['error']}
    print('  Key-only check did not complete; password login may prompt to read this node.', flush=True)
    password = ssh_collect(username, address, False)
    return {'key_attempt': key, 'used': 'password/keyboard-interactive',
            'facts': password['facts'], 'error': password['error']}


def identity_issues(facts, expected_name, username, address=None):
    if not isinstance(facts, dict):
        return ['No usable system observations were returned']
    issues = []
    if facts.get('hostname') != expected_name or facts.get('username') != username:
        issues.append('Hostname/account differs from ' + expected_name + '/' + username)
    if facts.get('os') != {'ID': 'ubuntu', 'VERSION_ID': '24.04'}:
        issues.append('Ubuntu 24.04 was not confirmed')
    addresses = facts.get('addresses', {})
    try:
        if addresses.get('exit') != 0:
            raise ValueError('ip command unavailable')
        entries = json.loads(addresses['out'])
        if not isinstance(entries, list):
            raise ValueError('expected an interface list')
        if address:
            matches = [entry for nic in entries for entry in nic.get('addr_info', [])
                       if entry.get('family') == 'inet' and entry.get('local') == address
                       and entry.get('prefixlen') == 24]
            if len(matches) != 1:
                issues.append('Expected management address ' + address + '/24 was not confirmed')
    except (KeyError, TypeError, ValueError):
        issues.append('IPv4 observations are unavailable')
    return issues


def lookup_text(result):
    if result.get('exit') != 0:
        return 'NO ANSWER / unavailable'
    values = sorted({line.split()[0] for line in result.get('out', '').splitlines() if line.split()})
    return ', '.join(values) or 'NO ANSWER'


def result_text(result):
    if result.get('exit') == 0:
        return result.get('out') or '(empty output)'
    return 'UNAVAILABLE (exit ' + str(result.get('exit')) + '): ' + (result.get('err') or result.get('out') or 'no details')


def machine_report(name, facts, issues):
    lines = ['\n=== ' + name + ' ===', 'Identity: ' + ('confirmed' if not issues else 'REVIEW: ' + '; '.join(issues))]
    if not isinstance(facts, dict):
        return lines
    try:
        interfaces = json.loads(facts.get('addresses', {}).get('out', '[]'))
        addresses = [nic['ifname'] + '=' + entry['local'] + '/' + str(entry['prefixlen'])
                     for nic in interfaces for entry in nic.get('addr_info', []) if entry.get('family') == 'inet']
        lines.append('Observed IPv4: ' + ', '.join(addresses))
    except (ValueError, TypeError, KeyError):
        lines.append('Observed IPv4: unavailable')
    for field, label in (('chrony_package', 'Chrony package'), ('chrony_service', 'Chrony service'),
                         ('timesyncd_service', 'Other time service: systemd-timesyncd'),
                         ('clock_sync', 'System reports clock synchronized')):
        lines.append(label + ': ' + result_text(facts.get(field, {})))
    lines.append('Name lookups (reported IPv4 answers; no reachability claim):')
    for target in ('r2d2', *NODES):
        observed = lookup_text(facts.get('lookups', {}).get(target, {}))
        note = ''
        if target in NODES:
            note = ' [matches node address]' if observed == NODES[target] else ' [review observed mapping]'
        lines.append('  ' + target + ' -> ' + observed + note)
    hosts = facts.get('hosts_file', {})
    lines.append('/etc/hosts (as read):\n' + (hosts.get('text', '') if not hosts.get('error') else 'UNAVAILABLE: ' + hosts['error']))
    lines.append('Chrony tracking:\n' + result_text(facts.get('chrony_tracking', {})))
    lines.append('Chrony sources:\n' + result_text(facts.get('chrony_sources', {})))
    return lines


def write_report(lines):
    if REPORT_DIR.is_symlink():
        raise RuntimeError('reports must be a real local directory')
    REPORT_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    path = REPORT_DIR / ('phase1-access-state-' + stamp + '.txt')
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        output.write('\n'.join(lines) + '\n')
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user', default='joe', help='node SSH account (default: joe)')
    args = parser.parse_args(argv)
    if not re.fullmatch(r'[a-z_][a-z0-9_-]*[$]?', args.user):
        parser.error('Use a normal Linux SSH account name')
    if os.geteuid() == 0 or not sys.stdin.isatty():
        parser.error('Run as your normal account in an interactive R2d2 terminal, without sudo')
    local = command([sys.executable, '-c', COLLECTOR], timeout=150)
    if local['exit'] != 0:
        raise RuntimeError('R2d2 observations unavailable: ' + result_text(local))
    facts = json.loads(local['out'])
    issues = identity_issues(facts, 'r2d2', args.user)
    if issues:
        raise RuntimeError('Check execution machine before continuing: ' + '; '.join(issues))
    lines = ['Phase 1: SSH keys, time and names — READ ONLY',
             'UTC: ' + dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
             'No keys created/copied, no packages installed, no host/network/time configuration changed.',
             '\nR2d2 public-key inventory (fingerprints only):']
    inventory = local_keys(Path.home())
    for item in inventory['public_keys']:
        lines += ['  ' + item['public_file'],
                  '    Matching filename without .pub exists: ' + str(item['counterpart_file_exists']),
                  '    Fingerprint: ' + result_text(item['fingerprint'])]
    if not inventory['public_keys']:
        lines.append('  No regular *.pub files found in ~/.ssh; this does not prove no private key exists elsewhere.')
    lines.append('Current SSH-agent key listing: ' + result_text(inventory['agent']))
    lines += machine_report('r2d2', facts, issues)
    partial = False
    for name, address in NODES.items():
        print('Inspecting ' + name + ' (' + address + '): first try a fresh key-only login.', flush=True)
        node = inspect_node(args.user, address)
        issues = identity_issues(node['facts'], name, args.user, address)
        partial |= bool(issues)
        key_confirmed = node['key_attempt']['exit'] == 0 and not issues and node['used'] == 'public key'
        lines += ['\n' + name + ' key-only login: ' + ('CONFIRMED' if key_confirmed else 'NOT CONFIRMED'),
                  'Inventory connection used: ' + node['used']]
        if node['key_attempt']['error']:
            lines.append('Key-check observation: ' + node['key_attempt']['error'])
        if issues and node['error']:
            lines.append('Inventory observation: ' + node['error'])
        lines += machine_report(name, node['facts'], issues)
    marker = 'ACCESS_STATE_PARTIAL' if partial else 'ACCESS_STATE_COLLECTED'
    lines += ['\nRESULT: ' + marker,
              'Collection status is not approval to change settings. Missing or inactive items remain observations.',
              'A key-only check without prompts does not prove that no key file exists.',
              'R2d2 name resolution is observed only; no fixed NAT address or reverse connectivity was assumed.']
    path = write_report(lines)
    print('\nRESULT: ' + marker)
    print('Private combined report: ' + str(path))
    print('Read it with: cat ' + shlex.quote(str(path)))
    return 1 if partial else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\nInspection interrupted; no configuration was applied.', file=sys.stderr)
        sys.exit(130)
    except (OSError, RuntimeError, ValueError) as error:
        print('Inspection stopped: ' + str(error), file=sys.stderr)
        sys.exit(1)

"""Real Bash/key generation; fixture agent and node transport, never lab access."""

import contextlib
import io
import json
import os
from pathlib import Path
import pty
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/phase1-ssh-keys.sh'
KEYGEN = shutil.which('ssh-keygen')

FAKE_SSH = '''#!/usr/bin/env python3
import json, os, pathlib, shlex, sys
destination, remote = sys.argv[-2:]
parts = shlex.split(remote)
node, address, account, marker = parts[-4:]
with pathlib.Path(os.environ['SSH_FIXTURE_CALLS']).open('a') as stream:
    stream.write(json.dumps({'tool':'ssh','node':node,'marker':marker,'args':sys.argv[1:]})+'\\n')
if os.environ.get('SSH_FIXTURE_FAILURE') == node+':'+marker:
    print('fixture: selected transport check did not complete')
    sys.exit(1)
print(marker, node, account, address)
'''

FAKE_COPY = '''#!/usr/bin/env python3
import json, os, pathlib, sys
public = pathlib.Path(sys.argv[sys.argv.index('-i')+1])
assert public.suffix == '.pub', 'Only public files may be copied'
assert public.read_text().startswith('ssh-ed25519 ')
assert '-f' not in sys.argv, 'Do not force duplicate installation'
with pathlib.Path(os.environ['SSH_FIXTURE_CALLS']).open('a') as stream:
    stream.write(json.dumps({'tool':'ssh-copy-id','destination':sys.argv[-1],'args':sys.argv[1:]})+'\\n')
print('fixture: selected public key handled')
'''


class SshKeysTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / 'scripts').mkdir()
        self.key_dir = self.root / 'key-files'
        self.key = self.key_dir / 'openstack-from-zero'
        self.calls = self.root / 'calls.jsonl'
        bindir = self.root / 'bin'
        bindir.mkdir()
        # Relocate only the file path in the isolated script copy; never reassign HOME.
        source = SCRIPT.read_text().replace('ssh_dir="$HOME/.ssh"', 'ssh_dir="${SSH_FIXTURE_KEY_DIRECTORY:?}"')
        self.script = self.root / 'scripts/phase1-ssh-keys.sh'
        self.script.write_text(source)
        commands = {'ssh': FAKE_SSH, 'ssh-copy-id': FAKE_COPY,
                    'hostname': '#!/bin/sh\nprintf "r2d2\\n"\n',
                    'id': '#!/bin/sh\nprintf "joe\\n"\n'}
        commands['ssh-add'] = '''#!/usr/bin/env python3
import json, os, pathlib, sys
state=pathlib.Path(os.environ['SSH_FIXTURE_KEY_DIRECTORY']).parent/'agent-state'
with pathlib.Path(os.environ['SSH_FIXTURE_CALLS']).open('a') as stream:
    stream.write(json.dumps({'tool':'ssh-add','args':sys.argv[1:]})+'\\n')
if sys.argv[1:] == ['-l']:
    if os.environ.get('SSH_FIXTURE_NO_AGENT') == '1' and not state.with_name('agent-available').exists():
        sys.exit(2)
    sys.exit(0 if state.exists() else 1)
if sys.argv[1] == '-t':
    state.write_text(sys.argv[-1])
    sys.exit(0)
if sys.argv[1] == '-T':
    sys.exit(0 if state.exists() else 1)
sys.exit(2)
'''
        commands['ssh-agent'] = '''#!/usr/bin/env python3
import json, os, pathlib, sys
with pathlib.Path(os.environ['SSH_FIXTURE_CALLS']).open('a') as stream:
    stream.write(json.dumps({'tool':'ssh-agent','args':sys.argv[1:]})+'\\n')
available=pathlib.Path(os.environ['SSH_FIXTURE_KEY_DIRECTORY']).parent/'agent-available'
if sys.argv[1:] == ['-s']:
    available.write_text('fixture')
    print('SSH_FIXTURE_AGENT_STARTED=1; export SSH_FIXTURE_AGENT_STARTED;')
elif sys.argv[1:] == ['-k']:
    available.unlink()
else:
    sys.exit(2)
'''
        commands['ssh-keygen'] = '''#!/usr/bin/env python3
import json, os, pathlib, sys
args=sys.argv[1:]
if '-t' in args:
    with pathlib.Path(os.environ['SSH_FIXTURE_CALLS']).open('a') as stream:
        stream.write(json.dumps({'tool':'generate','args':args})+'\\n')
    args += ['-N', '']
os.execv(os.environ['SSH_FIXTURE_REAL_KEYGEN'], ['ssh-keygen', *args])
'''
        for name, text in commands.items():
            target = bindir / name
            target.write_text(text)
            target.chmod(0o700)
        self.env = {**os.environ, 'PATH': str(bindir)+os.pathsep+os.environ['PATH'],
                    'SSH_FIXTURE_KEY_DIRECTORY': str(self.key_dir),
                    'SSH_FIXTURE_CALLS': str(self.calls), 'SSH_FIXTURE_REAL_KEYGEN': KEYGEN}

    def tearDown(self):
        self.directory.cleanup()

    def run_script(self, args=('--user', 'joe', '--apply'), fail=None):
        env = dict(self.env)
        if fail:
            env['SSH_FIXTURE_FAILURE'] = fail
        master, slave = pty.openpty()
        try:
            return subprocess.run(['bash', str(self.script), *args], stdin=slave,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, env=env, timeout=30)
        finally:
            os.close(master)
            os.close(slave)

    def generate(self, name='openstack-from-zero', passphrase=''):
        self.key_dir.mkdir(exist_ok=True)
        target = self.key_dir / name
        subprocess.run([KEYGEN, '-q', '-t', 'ed25519', '-N', passphrase, '-f', str(target)],
                       check=True, capture_output=True)
        return target

    def events(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def test_new_pair_agent_load_public_copy_and_fresh_verification_on_all_nodes(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('RESULT: SSH_KEYS_READY for all', result.stdout)
        events = self.events()
        self.assertEqual(sum(e['tool'] == 'generate' for e in events), 1)
        self.assertEqual(sum(e['tool'] == 'ssh-copy-id' for e in events), 3)
        self.assertIn({'tool': 'ssh-add', 'args': ['-t', '4h', str(self.key)]}, events)
        self.assertIn({'tool': 'ssh-add', 'args': ['-T', str(self.key)+'.pub']}, events)
        verified = [e for e in events if e.get('marker') == 'KEY_LOGIN_OK']
        self.assertEqual([e['node'] for e in verified], ['node1', 'node2', 'node3'])
        for event in verified:
            for option in ('BatchMode=yes', 'PasswordAuthentication=no', 'KbdInteractiveAuthentication=no',
                           'PreferredAuthentications=publickey', 'IdentitiesOnly=yes', 'ControlPath=none',
                           'StrictHostKeyChecking=yes'):
                self.assertIn(option, event['args'])
            self.assertEqual(event['args'][event['args'].index('-i')+1], str(self.key))
            self.assertEqual(event['args'][:2], ['-F', '/dev/null'])
        self.assertEqual(stat.S_IMODE(self.key.stat().st_mode), 0o600)
        report = next((self.root / 'reports').glob('*.txt'))
        self.assertEqual(stat.S_IMODE(report.stat().st_mode), 0o600)
        self.assertNotIn('BEGIN OPENSSH PRIVATE KEY', report.read_text())

    def test_repeat_keeps_pair_and_other_keys_unchanged(self):
        other = self.generate('other-key')
        other_bytes = other.read_bytes()
        self.assertEqual(self.run_script().returncode, 0)
        original = self.key.read_bytes(), Path(str(self.key)+'.pub').read_bytes()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual((self.key.read_bytes(), Path(str(self.key)+'.pub').read_bytes()), original)
        self.assertEqual(other.read_bytes(), other_bytes)
        self.assertEqual(sum(e['tool'] == 'generate' for e in self.events()), 1)

    def test_incomplete_pair_stops_without_overwrite_or_node_connection(self):
        self.generate()
        original = self.key.read_bytes()
        Path(str(self.key)+'.pub').unlink()
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('pair is incomplete', result.stdout)
        self.assertEqual(self.key.read_bytes(), original)
        self.assertEqual(self.events(), [])

    def test_mismatched_encrypted_pair_is_detected_before_agent_or_copy(self):
        self.generate(passphrase='fixture-passphrase')
        other = self.generate('another')
        Path(str(self.key)+'.pub').write_bytes(Path(str(other)+'.pub').read_bytes())
        original = self.key.read_bytes()
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('fingerprints differ', result.stdout)
        self.assertEqual(self.key.read_bytes(), original)
        self.assertEqual(self.events(), [])

    def test_failed_node_identity_prevents_its_copy_and_later_nodes(self):
        result = self.run_script(fail='node2:NODE_IDENTITY_OK')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('RESULT: SSH_KEYS_READY for all', result.stdout)
        copies = [e for e in self.events() if e['tool'] == 'ssh-copy-id']
        self.assertEqual([e['destination'] for e in copies], ['joe@192.168.0.200'])
        self.assertFalse(any(e.get('node') == 'node3' for e in self.events()))

    def test_failed_key_only_verification_never_reports_all_ready(self):
        result = self.run_script(fail='node2:KEY_LOGIN_OK')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('RESULT: SSH_KEYS_READY for all', result.stdout)
        self.assertFalse(any(e.get('node') == 'node3' for e in self.events()))

    def test_verification_mode_does_not_generate_or_copy(self):
        self.generate()
        result = self.run_script(('--user', 'joe'))
        self.assertEqual(result.returncode, 0, result.stdout)
        ssh_events = [e for e in self.events() if e['tool'] == 'ssh']
        self.assertEqual(len(ssh_events), 3)
        self.assertTrue(all(e.get('marker') == 'KEY_LOGIN_OK' for e in ssh_events))
        self.assertFalse(any(e['tool'] in ('generate', 'ssh-copy-id') for e in self.events()))

    def test_unsafe_private_permissions_and_invalid_user_stop_before_copy(self):
        self.generate()
        self.key.chmod(0o644)
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Private key permissions', result.stdout)
        self.assertEqual(self.events(), [])
        result = self.run_script(('--user', 'joe;false', '--apply'))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.events(), [])

    def test_remote_guard_checks_actual_identity_and_does_not_change_files(self):
        source = re.search(r'remote_source="\$\(cat <<\x27PY\x27\n([\s\S]*?)\nPY\n\)"', SCRIPT.read_text()).group(1)
        observed = json.dumps([{'addr_info': [{'family': 'inet', 'local': '192.168.0.200', 'prefixlen': 24}]}])
        actual_read = Path.read_text

        def read(path, *args, **kwargs):
            if str(path) == '/etc/os-release':
                return 'ID=ubuntu\nVERSION_ID="24.04"'
            return actual_read(path, *args, **kwargs)

        self.key_dir.mkdir()
        with patch('socket.gethostname', return_value='node1'), \
                patch('pwd.getpwuid', return_value=types.SimpleNamespace(pw_name='joe')), \
                patch('subprocess.check_output', return_value=observed), \
                patch.object(Path, 'read_text', read), patch.object(Path, 'home', return_value=self.root), \
                patch.object(sys, 'argv', ['check', 'node1', '192.168.0.200', 'joe', 'KEY_LOGIN_OK']), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            exec(compile(source, '<remote fixture>', 'exec'), {})
            self.assertIn('KEY_LOGIN_OK node1 joe 192.168.0.200', output.getvalue())
            with patch('socket.gethostname', return_value='node2'), self.assertRaises(SystemExit):
                exec(compile(source, '<remote fixture>', 'exec'), {})
        self.assertEqual(list(self.key_dir.iterdir()), [])

    def test_only_an_agent_started_by_this_script_is_stopped(self):
        self.env['SSH_FIXTURE_NO_AGENT'] = '1'
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout)
        events = [e for e in self.events() if e['tool'] == 'ssh-agent']
        self.assertEqual([e['args'] for e in events], [['-s'], ['-k']])
        self.assertFalse((self.root / 'agent-available').exists())


if __name__ == '__main__':
    unittest.main()

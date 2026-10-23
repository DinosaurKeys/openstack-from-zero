"""Exercise authentication boundaries and read-only evidence without lab access."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('access_state', ROOT / 'scripts/phase1-access-state.py')
access = importlib.util.module_from_spec(spec)
spec.loader.exec_module(access)


def facts(name, address='192.168.0.200'):
    okay = {'exit': 0, 'out': '', 'err': ''}
    return {'hostname': name, 'username': 'joe', 'os': {'ID': 'ubuntu', 'VERSION_ID': '24.04'},
            'addresses': {'exit': 0, 'out': json.dumps([{'ifname': 'mgmt', 'addr_info': [
                {'family': 'inet', 'local': address, 'prefixlen': 24}]}])},
            'hosts_file': {'text': '127.0.0.1 localhost', 'error': None},
            'lookups': {node: {'exit': 0, 'out': ip + ' STREAM ' + node} for node, ip in access.NODES.items()},
            **{key: okay for key in ('chrony_package', 'chrony_service', 'timesyncd_service',
                                    'clock_sync', 'chrony_tracking', 'chrony_sources')}}


class AccessStateTests(unittest.TestCase):
    def test_collector_only_reads_system_files_and_runs_monitoring_commands(self):
        calls, files = [], []

        def command(args, **kwargs):
            calls.append(args)
            out = '[]' if args[0] == 'ip' else 'fixture observation'
            return subprocess.CompletedProcess(args, 0, out, '')

        def read(path, *args, **kwargs):
            files.append(str(path))
            return 'ID=ubuntu\nVERSION_ID="24.04"' if str(path) == '/etc/os-release' else '127.0.0.1 localhost'

        with patch('subprocess.run', side_effect=command), patch.object(Path, 'read_text', read), \
                patch('socket.gethostname', return_value='node1'), \
                patch('pwd.getpwuid', return_value=types.SimpleNamespace(pw_name='joe')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            exec(compile(access.COLLECTOR, '<fixture collector>', 'exec'), {})
        observed = json.loads(output.getvalue())
        self.assertEqual(observed['hostname'], 'node1')
        self.assertEqual(set(files), {'/etc/os-release', '/etc/hosts'})
        self.assertEqual({call[0] for call in calls}, {'ip', 'getent', 'dpkg-query', 'systemctl', 'timedatectl', 'chronyc'})
        for call in calls:
            if call[0] == 'chronyc':
                self.assertIn(call[-1], ('tracking', 'sources'))
            if call[0] == 'systemctl':
                self.assertEqual(call[1], 'show')
            self.assertNotIn('sudo', call)

    def test_key_probe_cannot_fall_back_to_password_or_reuse_connection(self):
        args = access.ssh_arguments('joe', '192.168.0.200', True)
        for option in ('BatchMode=yes', 'PreferredAuthentications=publickey',
                       'PasswordAuthentication=no', 'KbdInteractiveAuthentication=no',
                       'StrictHostKeyChecking=yes', 'UpdateHostKeys=no', 'ControlPath=none',
                       'AddKeysToAgent=no', 'ForwardAgent=no', 'ClearAllForwardings=yes'):
            self.assertIn(option, args)
        self.assertEqual(args[-2:], ['joe@192.168.0.200', 'python3 -'])
        password_args = access.ssh_arguments('joe', '192.168.0.200', False)
        self.assertIn('PubkeyAuthentication=no', password_args)
        self.assertIn('StrictHostKeyChecking=yes', password_args)

    def test_password_inventory_does_not_turn_into_key_login_success(self):
        key = {'exit': 255, 'facts': None, 'error': 'key not available', 'invalid_output': False}
        password = {'exit': 0, 'facts': facts('node1'), 'error': '', 'invalid_output': False}
        with patch.object(access, 'ssh_collect', side_effect=[key, password]) as collect, \
                contextlib.redirect_stdout(io.StringIO()):
            result = access.inspect_node('joe', '192.168.0.200')
        self.assertEqual(collect.call_args_list[0].args[-1], True)
        self.assertEqual(collect.call_args_list[1].args[-1], False)
        self.assertEqual(result['key_attempt']['exit'], 255)
        self.assertEqual(result['used'], 'password/keyboard-interactive')
        self.assertEqual(result['facts']['hostname'], 'node1')

    def test_key_success_uses_one_connection(self):
        key = {'exit': 0, 'facts': facts('node1'), 'error': '', 'invalid_output': False}
        with patch.object(access, 'ssh_collect', return_value=key) as collect:
            result = access.inspect_node('joe', '192.168.0.200')
        self.assertEqual(result['used'], 'public key')
        collect.assert_called_once_with('joe', '192.168.0.200', True)

    def test_unexpected_node_or_ip_is_not_a_verified_identity(self):
        self.assertEqual(access.identity_issues(facts('node1'), 'node1', 'joe', '192.168.0.200'), [])
        self.assertTrue(access.identity_issues(facts('node2'), 'node1', 'joe', '192.168.0.200'))
        self.assertTrue(access.identity_issues(facts('node1'), 'node1', 'joe', '192.168.0.202'))
        self.assertTrue(access.identity_issues(None, 'node1', 'joe', '192.168.0.200'))

    def test_public_inventory_never_reads_private_contents_and_keeps_files(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            ssh_dir = home / '.ssh'
            ssh_dir.mkdir()
            private = ssh_dir / 'example.key'
            private.write_text('PRIVATE_SECRET_MUST_NOT_APPEAR')
            public = ssh_dir / 'example.key.pub'
            public.write_text('fixture public')
            (ssh_dir / 'linked.pub').symlink_to(private)
            with patch.object(access, 'command', return_value={'exit': 0, 'out': 'SHA256:fixture', 'err': ''}), \
                    patch.object(Path, 'read_text', side_effect=AssertionError('file content must not be read here')):
                result = access.local_keys(home)
            self.assertEqual(len(result['public_keys']), 1)
            self.assertTrue(result['public_keys'][0]['counterpart_file_exists'])
            self.assertNotIn('PRIVATE_SECRET', json.dumps(result))
            self.assertEqual(private.read_text(), 'PRIVATE_SECRET_MUST_NOT_APPEAR')

    def test_duplicate_name_answers_and_missing_time_tools_remain_observations(self):
        observed = facts('node1')
        observed['lookups']['node1'] = {'exit': 0, 'out': '127.0.1.1 STREAM node1\n192.168.0.200 STREAM node1'}
        observed['chrony_tracking'] = {'exit': None, 'out': '', 'err': 'chronyc not installed'}
        text = '\n'.join(access.machine_report('node1', observed, []))
        self.assertIn('node1 -> 127.0.1.1, 192.168.0.200 [review observed mapping]', text)
        self.assertIn('UNAVAILABLE', text)
        self.assertIn('chronyc not installed', text)
        self.assertIn('r2d2 -> NO ANSWER / unavailable', text)

    def test_complete_run_reports_password_and_key_modes_separately_and_saves_privately(self):
        def node(username, address):
            name = next(name for name, ip in access.NODES.items() if ip == address)
            key_only = name != 'node2'
            return {'key_attempt': {'exit': 0 if key_only else 255, 'error': ''},
                    'used': 'public key' if key_only else 'password/keyboard-interactive',
                    'facts': facts(name, address), 'error': ''}

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(access, 'REPORT_DIR', Path(directory) / 'reports'), \
                patch.object(access.os, 'geteuid', return_value=1000), \
                patch.object(access.sys.stdin, 'isatty', return_value=True), \
                patch.object(access, 'command', return_value={'exit': 0, 'out': json.dumps(facts('r2d2', '192.168.162.131')), 'err': ''}), \
                patch.object(access, 'local_keys', return_value={'public_keys': [], 'agent': {'exit': 1, 'out': '', 'err': 'no agent'}}), \
                patch.object(access, 'inspect_node', side_effect=node), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(access.main(['--user', 'joe']), 0)
            report = next((Path(directory) / 'reports').glob('*.txt'))
            self.assertEqual(stat.S_IMODE(report.stat().st_mode), 0o600)
            text = report.read_text()
            self.assertIn('node1 key-only login: CONFIRMED', text)
            self.assertIn('node2 key-only login: NOT CONFIRMED', text)
            self.assertIn('RESULT: ACCESS_STATE_COLLECTED', text)


if __name__ == '__main__':
    unittest.main()

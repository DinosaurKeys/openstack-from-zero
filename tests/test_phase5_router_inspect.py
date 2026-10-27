import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('router_inspect', ROOT/'scripts/phase5-router-inspect.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
ROUTER = 'a3c6cb8e-cba3-49d0-964b-cf131e1075c5'


class InspectionTests(unittest.TestCase):
    def test_default_plan_never_connects_or_reads_private_inputs(self):
        with patch.object(m, 'observe', side_effect=AssertionError), patch.object(m, 'tracked_router', side_effect=AssertionError), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(m.main([]), 0)

    def test_private_file_refuses_public_file_and_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'state'; path.write_text('{}'); path.chmod(0o644)
            with self.assertRaises(RuntimeError): m.private_file(path)
            path.chmod(0o600); m.private_file(path)
            link = Path(folder)/'link'; link.symlink_to(path)
            with self.assertRaises(RuntimeError): m.private_file(link)

    def test_router_uuid_comes_from_tracked_state(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(m, 'REPO', Path(folder)):
            path = Path(folder)/'private/phase4/vm.json'; path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'networks': {'router': ROUTER}})); path.chmod(0o600)
            self.assertEqual(m.tracked_router(), ROUTER)
            path.write_text(json.dumps({'networks': {'router': '../wrong'}}))
            with self.assertRaises(ValueError): m.tracked_router()

    def reader(self, content, version_missing=False):
        def read_config(cfg, paths):
            cfg.read_dict({'DEFAULT': {'state_path': '/var/lib/neutron/kolla'}})
            return paths
        output = io.StringIO()
        with patch('configparser.ConfigParser.read', read_config), patch('pathlib.Path.is_symlink', return_value=False), patch('pathlib.Path.is_file', return_value=True), patch('pathlib.Path.read_bytes', return_value=content.encode()), patch('importlib.metadata.version', return_value='reviewed-version') as version, patch('sys.argv', ['reader', ROUTER]), contextlib.redirect_stdout(output):
            if version_missing:
                import importlib.metadata
                version.side_effect = importlib.metadata.PackageNotFoundError
            exec(compile(m.READER, 'reader', 'exec'), {})
        return json.loads(output.getvalue())

    def test_reader_extracts_tracking_and_primary_vip_without_auth_secret(self):
        result = self.reader('''# track_interface { fake }
vrrp_instance VR_1 {
 authentication { auth_pass SUPER_SECRET }
 track_interface {
 ha-7aa70dea-517
 }
 virtual_ipaddress {
 169.254.0.1/24 dev ha-7aa70dea-517
 }
 virtual_ipaddress_excluded {
 192.168.0.154/24 dev qg-test
 }
}''')
        self.assertEqual(result['tracked_interfaces'], ['ha-7aa70dea-517'])
        self.assertEqual(result['primary_virtual_addresses'], ['169.254.0.1/24'])
        self.assertEqual(result['vrrp_instances'], 1)
        self.assertNotIn('SUPER_SECRET', json.dumps(result))
        self.assertEqual(len(result['config_sha256']), 64)

    def test_missing_python_distribution_metadata_is_not_a_failure(self):
        self.assertEqual(self.reader('vrrp_instance VR_1 {}', True)['neutron_version'], 'not available from this interpreter')

    def test_ssh_sends_password_only_on_stdin_with_strict_host_checks(self):
        result = subprocess.CompletedProcess([], 0, json.dumps({'node': 'node1', 'observed': True}), '')
        with patch.object(m, 'private_file'), patch.object(m.subprocess, 'run', return_value=result) as run:
            self.assertTrue(m.observe('node1', ROUTER, 'TEST_SECRET')['observed'])
        args = run.call_args.args[0]; kwargs = run.call_args.kwargs
        self.assertNotIn('TEST_SECRET', repr(args)); self.assertEqual(kwargs['input'], 'TEST_SECRET\n')
        self.assertIn('StrictHostKeyChecking=yes', args)
        self.assertIn('BatchMode=yes', args)
        self.assertIn('ForwardAgent=no', args)

    def test_ssh_error_does_not_echo_stderr_or_password(self):
        result = subprocess.CompletedProcess([], 1, '', 'PRIVATE_ERROR')
        with patch.object(m, 'private_file'), patch.object(m.subprocess, 'run', return_value=result):
            value = m.observe('node2', ROUTER, 'TEST_SECRET')
        self.assertFalse(value['observed'])
        self.assertNotIn('PRIVATE_ERROR', json.dumps(value))
        self.assertNotIn('TEST_SECRET', json.dumps(value))

    def test_wrong_node_response_rejected(self):
        result = subprocess.CompletedProcess([], 0, json.dumps({'node': 'node3', 'observed': True}), '')
        with patch.object(m, 'private_file'), patch.object(m.subprocess, 'run', return_value=result):
            self.assertFalse(m.observe('node1', ROUTER, 'x')['observed'])

    def test_remote_command_allowlist_has_no_mutations(self):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            if args[:2] == ['ip', '-j']:
                data = [{'addr_info': [{'local': '192.168.0.200'}]}]
            elif args[:2] == ['docker', 'inspect']:
                data = {'id': 'a'*64, 'image': 'quay.io/openstack.kolla/neutron-l3-agent:2026.2-ubuntu-noble', 'running': True, 'network_mode': 'host'}
            elif args[:3] == ['ip', 'netns', 'exec']:
                data = [{'ifname': 'ha-7aa70dea-517', 'ifindex': 3, 'flags': ['UP'], 'addr_info': []}]
            else:
                self.assertEqual(args[:6], ['docker', 'exec', '--user', 'root', 'neutron_l3_agent', 'python3'])
                data = {'tracked_interfaces': ['ha-7aa70dea-517']}
            return subprocess.CompletedProcess(args, 0, json.dumps(data), '')
        output = io.StringIO()
        with patch('os.geteuid', return_value=0), patch.dict(os.environ, {'SUDO_USER': 'joe'}), patch('socket.gethostname', return_value='node1'), patch('os.stat') as stat, patch('subprocess.run', run), patch('sys.argv', ['node', 'node1', ROUTER, '192.168.0.200', m.READER]), contextlib.redirect_stdout(output):
            stat.return_value.st_dev = 1; stat.return_value.st_ino = 2
            with self.assertRaises(SystemExit) as ended:
                exec(compile(m.NODE, 'node', 'exec'), {})
        self.assertEqual(ended.exception.code, 0)
        self.assertTrue(json.loads(output.getvalue())['observed'])
        self.assertEqual(len(calls), 4)
        self.assertFalse(any('set' in call or 'stop' in call or 'start' in call or 'down' in call for call in calls))

    def test_remote_refuses_wrong_sudo_identity_before_commands(self):
        output = io.StringIO()
        with patch('os.geteuid', return_value=0), patch.dict(os.environ, {'SUDO_USER': 'somebody'}), patch('subprocess.run', side_effect=AssertionError), patch('sys.argv', ['node', 'node1', ROUTER, '192.168.0.200', m.READER]), contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as ended:
                exec(compile(m.NODE, 'node', 'exec'), {})
        self.assertEqual(ended.exception.code, 1)
        self.assertFalse(json.loads(output.getvalue())['observed'])


if __name__ == '__main__': unittest.main()

"""Test hosts-file preservation and verification using temporary files only."""

import contextlib
import importlib.util
import io
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('hosts_step', ROOT / 'scripts/phase1-hosts.py')
hosts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hosts)
ORIGINAL = '# existing comment\n127.0.0.1 localhost\n127.0.1.1 r2d2\n::1 ip6-localhost ip6-loopback\n'


class RenderTests(unittest.TestCase):
    def test_addition_preserves_every_original_line_and_repeat_is_unchanged(self):
        rendered = hosts.render_hosts(ORIGINAL)
        self.assertTrue(rendered.startswith(ORIGINAL))
        for name, address in hosts.NODES.items():
            self.assertEqual(rendered.count(address + ' ' + name + '\n'), 1)
        self.assertEqual(hosts.render_hosts(rendered), rendered)

    def test_existing_correct_aliases_comments_and_newlines_are_preserved(self):
        original = ORIGINAL + '192.168.0.200\tNODE1 other-alias # retain this\n'
        rendered = hosts.render_hosts(original)
        self.assertTrue(rendered.startswith(original))
        self.assertNotIn('192.168.0.200 node1\n', rendered)
        crlf = ORIGINAL.replace('\n', '\r\n')
        self.assertTrue(hosts.render_hosts(crlf).startswith(crlf))
        self.assertIn('192.168.0.201 node2\r\n', hosts.render_hosts(crlf))
        self.assertTrue(hosts.render_hosts(ORIGINAL.rstrip('\n')).startswith(ORIGINAL))

    def test_conflicting_addresses_and_malformed_markers_are_refused(self):
        for line in ('127.0.1.1 node1', '192.168.0.99 NODE2', '::1 node3'):
            with self.subTest(line=line), self.assertRaises(RuntimeError):
                hosts.render_hosts(ORIGINAL + line + '\n')
        for suffix in (hosts.BEGIN+'\n', hosts.END+'\n', hosts.END+'\n'+hosts.BEGIN+'\n'):
            with self.subTest(suffix=suffix), self.assertRaises(RuntimeError):
                hosts.render_hosts(ORIGINAL+suffix)

    def test_existing_block_is_extended_without_deleting_content(self):
        original = ORIGINAL + hosts.BEGIN+'\n# preserve inside comment\n192.168.0.200 node1\n'+hosts.END+'\n'
        rendered = hosts.render_hosts(original)
        self.assertIn('# preserve inside comment\n192.168.0.200 node1\n', rendered)
        self.assertEqual(rendered.count(hosts.BEGIN), 1)
        self.assertEqual(hosts.render_hosts(rendered), rendered)


class FileTransactionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.path = self.root / 'hosts'
        self.path.write_bytes(ORIGINAL.encode())
        self.path.chmod(0o640)
        self.backups = self.root / 'backups'

    def tearDown(self):
        self.directory.cleanup()

    def run_prepare(self, resolver=None):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = hosts.prepare(self.path, self.backups,
                                   resolver=resolver or (lambda name: {hosts.NODES[name]}))
        return result, output.getvalue()

    def test_real_file_update_keeps_owner_permissions_and_private_exact_backup(self):
        before = self.path.stat()
        result, output = self.run_prepare()
        self.assertTrue(result, output)
        after = self.path.stat()
        self.assertEqual((after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)),
                         (before.st_uid, before.st_gid, 0o640))
        self.assertTrue(self.path.read_text().startswith(ORIGINAL))
        backup = next(self.backups.glob('*/hosts'))
        self.assertEqual(backup.read_bytes(), ORIGINAL.encode())
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertIn('RESULT: R2D2_HOSTS_READY', output)
        content = self.path.read_bytes()
        result, output = self.run_prepare()
        self.assertTrue(result, output)
        self.assertEqual(self.path.read_bytes(), content)
        self.assertEqual(len(list(self.backups.glob('*/hosts'))), 1)

    def test_failed_lookup_restores_original_contents_and_permissions(self):
        result, output = self.run_prepare(lambda name: {'127.0.1.1'} if name == 'node2' else {hosts.NODES[name]})
        self.assertFalse(result)
        self.assertEqual(self.path.read_bytes(), ORIGINAL.encode())
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)
        self.assertIn('Previous hosts-file contents restored.', output)
        self.assertNotIn('RESULT: R2D2_HOSTS_READY', output)

    def test_conflict_and_symlink_cannot_modify_files(self):
        conflict = ORIGINAL+'192.168.0.99 node1\n'
        self.path.write_text(conflict)
        with self.assertRaises(RuntimeError):
            self.run_prepare()
        self.assertEqual(self.path.read_text(), conflict)
        self.assertFalse(self.backups.exists())
        linked = self.root/'linked'
        linked.symlink_to(self.path)
        with self.assertRaises(RuntimeError):
            hosts.read_hosts(linked)

    def test_external_edit_during_preparation_is_not_overwritten(self):
        read = hosts.read_hosts
        calls = 0
        external = ORIGINAL+'10.0.0.5 unrelated-new-entry\n'

        def changed(path):
            nonlocal calls
            calls += 1
            if calls == 2:
                path.write_text(external)
            return read(path)

        with patch.object(hosts, 'read_hosts', side_effect=changed):
            result, output = self.run_prepare()
        self.assertFalse(result)
        self.assertEqual(self.path.read_text(), external)
        self.assertIn('no overwrite made', output)


class EntrypointTests(unittest.TestCase):
    def test_normal_account_calls_only_local_sudo_helper_and_owns_private_report(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(hosts, 'REPORTS', Path(directory)/'reports'), \
                patch.object(hosts, 'require_r2d2'), patch.object(hosts.os, 'geteuid', return_value=1000), \
                patch.object(hosts.pwd, 'getpwuid', return_value=types.SimpleNamespace(pw_name='joe')), \
                patch.object(hosts.sys.stdin, 'isatty', return_value=True), \
                patch.object(hosts.subprocess, 'Popen', return_value=types.SimpleNamespace(
                    stdout=iter(['RESULT: R2D2_HOSTS_READY\n']), wait=lambda: 0)) as process, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(hosts.main(['--apply']), 0)
            self.assertEqual(process.call_args.args[0], ['sudo', '/usr/bin/python3',
                str((ROOT/'scripts/phase1-hosts.py').resolve()), '--root-helper', '--apply'])
            report = next((Path(directory)/'reports').glob('*.txt'))
            self.assertEqual(stat.S_IMODE(report.stat().st_mode), 0o600)

    def test_wrong_machine_stops_before_privileged_process(self):
        with patch.object(hosts.socket, 'gethostname', return_value='node1'), \
                patch.object(hosts.subprocess, 'Popen') as process:
            with self.assertRaises(RuntimeError):
                hosts.main(['--apply'])
            process.assert_not_called()

    def test_lookup_uses_ipv4_and_deduplicates_socket_types(self):
        response = subprocess.CompletedProcess([], 0,
            '192.168.0.200 STREAM node1\n192.168.0.200 DGRAM\n192.168.0.200 RAW\n', '')
        with patch.object(hosts.subprocess, 'run', return_value=response) as command:
            self.assertEqual(hosts.lookup('node1'), {'192.168.0.200'})
        self.assertEqual(command.call_args.args[0], ['getent', 'ahostsv4', 'node1'])


if __name__ == '__main__':
    unittest.main()

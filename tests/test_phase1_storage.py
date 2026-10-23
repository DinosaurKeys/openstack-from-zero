"""Storage evidence must stay read-only and never turn missing data into success."""

import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import pty
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('storage_node', ROOT / 'scripts/phase1-storage-node.py')
storage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage)
IDENTITY = {'blockdevices': [{'path': '/dev/sda', 'type': 'disk', 'size': storage.SIZE,
                              'model': 'KINGSTON SA400S3', 'serial': storage.SERIAL}]}


class StorageEvidenceTests(unittest.TestCase):
    def test_changed_identity_refused(self):
        storage.verify_identity(Path('/dev/sda'), IDENTITY)
        for field, value in (('serial', 'another-disk'), ('size', 1),
                             ('type', 'part'), ('path', '/dev/nvme0n1')):
            with self.subTest(field=field):
                changed = copy.deepcopy(IDENTITY)
                changed['blockdevices'][0][field] = value
                with self.assertRaises(RuntimeError):
                    storage.verify_identity(Path('/dev/sda'), changed)

    def test_wrong_node_stops_before_disk_commands(self):
        with patch.object(storage.os, 'geteuid', return_value=0), \
                patch.object(storage.socket, 'gethostname', return_value='node2'), \
                patch.object(storage, 'run') as command, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(storage.main(), 1)
            command.assert_not_called()

    def test_collection_uses_inspection_commands_and_preserves_failures(self):
        for smart_status in (0, 8):
            calls = []

            def command(args):
                calls.append(args)
                if args[0] == 'lsblk':
                    value = {'blockdevices': [{'path': '/dev/sda', 'kname': 'sda', 'type': 'disk',
                                              'size': storage.SIZE, 'fstype': None, 'mountpoints': [None]}]}
                    return subprocess.CompletedProcess(args, 0, json.dumps(value), '')
                return subprocess.CompletedProcess(args, smart_status if args[0] == 'smartctl' else 0,
                                                   'fixture evidence', '')

            with self.subTest(smart_status=smart_status), patch.object(storage, 'run', side_effect=command), \
                    patch.object(storage.Path, 'read_text', return_value='fixture proc data'), \
                    patch.object(storage.Path, 'iterdir', return_value=iter([])), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(storage.collect(Path('/dev/sda')), 0 if smart_status == 0 else 1)
            self.assertEqual({call[0] for call in calls}, {'lsblk', 'findmnt', 'wipefs', 'pvs', 'vgs', 'lvs', 'smartctl'})
            for call in calls:
                if call[0] == 'wipefs':
                    self.assertIn('--no-act', call)
                    self.assertNotIn('--all', call)
                if call[0] in ('pvs', 'vgs', 'lvs'):
                    self.assertIn('--readonly', call)
                if call[0] == 'smartctl':
                    self.assertEqual(call, ['smartctl', '--all', '/dev/sda'])
            if smart_status:
                self.assertIn('REVIEW: command returned nonzero', output.getvalue())

    def test_missing_health_tool_is_incomplete_evidence(self):
        with patch.object(storage, 'run', side_effect=FileNotFoundError('smartctl unavailable')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertFalse(storage.show_command('health', ['smartctl', '--all', '/dev/sda']))
        self.assertIn('REVIEW:', output.getvalue())


FAKE_SSH = '''#!/usr/bin/env python3
import os, pathlib, sys
pathlib.Path(os.environ['STORAGE_FIXTURE_CALLS']).write_text(sys.argv[-2]+'\\n'+sys.argv[-1])
import json
pathlib.Path(os.environ['STORAGE_FIXTURE_ARGV']).write_text(json.dumps(sys.argv[1:]))
print('fixture storage evidence')
sys.exit(int(os.environ.get('STORAGE_FIXTURE_STATUS', '0')))
'''


class StorageWrapperTests(unittest.TestCase):
    def exercise(self, root, args=(), status=0, identity=None):
        root = Path(root)
        (root / 'scripts').mkdir()
        for filename in ('phase1-storage-check.sh', 'phase1-storage-node.py'):
            shutil.copyfile(ROOT / 'scripts' / filename, root / 'scripts' / filename)
        bindir = root / 'bin'
        bindir.mkdir()
        (bindir / 'ssh').write_text(FAKE_SSH)
        (bindir / 'ssh').chmod(0o700)
        call_file = root / 'ssh-calls.txt'
        home = root / 'home'
        (home / '.ssh').mkdir(parents=True)
        if identity == 'regular':
            (home / '.ssh/openstack-from-zero').write_text('fixture identity; not a real key')
        elif identity == 'symlink':
            (home / '.ssh/openstack-from-zero').symlink_to(root / 'missing-key')
        env = {**os.environ, 'PATH': str(bindir)+os.pathsep+os.environ['PATH'],
               'HOME': str(home), 'STORAGE_FIXTURE_ARGV': str(root / 'ssh-argv.json'),
               'STORAGE_FIXTURE_CALLS': str(call_file), 'STORAGE_FIXTURE_STATUS': str(status)}
        master, slave = pty.openpty()
        try:
            result = subprocess.run(['bash', str(root / 'scripts/phase1-storage-check.sh'), *args],
                                    stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, env=env, timeout=15)
        finally:
            os.close(master)
            os.close(slave)
        return result, call_file.read_text() if call_file.exists() else ''

    def test_one_node_private_report_and_no_apply_mode(self):
        with tempfile.TemporaryDirectory() as root:
            result, call = self.exercise(root, ['--user', 'joe'])
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertTrue(call.startswith('joe@192.168.0.202\nsudo /usr/bin/python3 '))
            self.assertIn('RESULT: STORAGE_INSPECTION_COLLECTED', result.stdout)
            report = next((Path(root) / 'reports').glob('*.txt'))
            self.assertEqual(stat.S_IMODE(report.stat().st_mode), 0o600)
        with tempfile.TemporaryDirectory() as root:
            result, call = self.exercise(root, ['--apply'])
            self.assertEqual(result.returncode, 2)
            self.assertEqual(call, '')

    def test_ssh_failure_and_invalid_user_cannot_report_success(self):
        with tempfile.TemporaryDirectory() as root:
            result, call = self.exercise(root, status=1)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn('RESULT: STORAGE_INSPECTION_COLLECTED', result.stdout)
            self.assertIn('RESULT: STORAGE_INSPECTION_NEEDS_REVIEW', result.stdout)
        with tempfile.TemporaryDirectory() as root:
            result, call = self.exercise(root, ['--user', 'joe;false'])
            self.assertEqual(result.returncode, 2)
            self.assertEqual(call, '')

    def test_existing_project_key_is_selected_without_copying_it(self):
        with tempfile.TemporaryDirectory() as root:
            result, call = self.exercise(root, identity='regular')
            self.assertEqual(result.returncode, 0, result.stdout)
            args = json.loads((Path(root) / 'ssh-argv.json').read_text())
            self.assertEqual(args[args.index('-i') + 1], str(Path(root) / 'home/.ssh/openstack-from-zero'))
            self.assertIn('IdentitiesOnly=yes', args)
            self.assertNotIn('fixture identity', call)
            self.assertIn('Key-unlock prompt: key passphrase', result.stdout)

    def test_symlinked_project_key_stops_before_connection(self):
        with tempfile.TemporaryDirectory() as root:
            result, call = self.exercise(root, identity='symlink')
            self.assertEqual(result.returncode, 2)
            self.assertEqual(call, '')


if __name__ == '__main__':
    unittest.main()

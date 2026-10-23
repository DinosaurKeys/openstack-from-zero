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
import shlex
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('storage_node', ROOT / 'scripts/phase1-storage-node.py')
storage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage)
IDENTITY = {'blockdevices': [{'path': '/dev/sda', 'type': 'disk', 'size': 240057409536,
                              'model': 'KINGSTON SA400S3', 'serial': '50026B768317883A', 'tran': 'sata'}]}


class StorageEvidenceTests(unittest.TestCase):
    def test_changed_identity_refused(self):
        storage.verify_identity(Path('/dev/sda'), IDENTITY, IDENTITY['blockdevices'][0])
        for field, value in (('serial', 'another-disk'), ('size', 1),
                             ('type', 'part'), ('path', '/dev/nvme0n1')):
            with self.subTest(field=field):
                changed = copy.deepcopy(IDENTITY)
                changed['blockdevices'][0][field] = value
                with self.assertRaises(RuntimeError):
                    storage.verify_identity(Path('/dev/sda'), changed, IDENTITY['blockdevices'][0])

    def test_discovery_reads_all_sata_and_excludes_nvme_and_usb(self):
        rows = [IDENTITY['blockdevices'][0],
                {'path': '/dev/sdb', 'type': 'disk', 'size': 1000, 'serial': 'disk2', 'tran': 'sata'},
                {'path': '/dev/nvme0n1', 'type': 'disk', 'size': 1000, 'serial': 'os', 'tran': 'nvme'},
                {'path': '/dev/sdc', 'type': 'disk', 'size': 1000, 'serial': 'usb', 'tran': 'usb'}]
        self.assertEqual([d['path'] for d in storage.discover_sata({'blockdevices': rows})], ['/dev/sda', '/dev/sdb'])
        for value in ({'blockdevices': rows[2:]}, {'blockdevices': [{**rows[0], 'serial': None}]},
                      {'blockdevices': [{**rows[0], 'size': 0}]}):
            with self.assertRaises(RuntimeError):
                storage.discover_sata(value)

    def test_wrong_node_stops_before_disk_commands(self):
        with patch.object(storage.os, 'geteuid', return_value=0), \
                patch.object(storage.socket, 'gethostname', return_value='node2'), \
                patch.object(storage, 'run') as command, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(storage.main(), 1)
            command.assert_not_called()

    def test_each_expected_node_uses_its_current_discovered_identity(self):
        for index in (1, 2, 3):
            node = 'node' + str(index)
            disk = {**IDENTITY['blockdevices'][0], 'serial': 'current-serial-' + str(index),
                    'size': 1000000 * index, 'model': 'Current observed model'}
            facts = {'blockdevices': [disk]}
            alias = Mock()
            alias.resolve.return_value = Path('/dev/sda')
            with self.subTest(node=node), patch.object(storage.os, 'geteuid', return_value=0), \
                 patch.object(storage.socket, 'gethostname', return_value=node), \
                 patch.object(storage.Path, 'read_text', return_value='ID=ubuntu\nVERSION_ID="24.04"\n'), \
                 patch.object(storage.Path, 'resolve', lambda value, **kwargs: value), \
                 patch.object(storage.Path, 'stat', return_value=SimpleNamespace(st_mode=stat.S_IFBLK)), \
                 patch.object(storage.Path, 'glob', return_value=[alias]), \
                 patch.object(storage, 'checked_json', side_effect=[facts, facts]), \
                 patch.object(storage, 'collect', return_value=0) as collect, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(storage.main(node), 0)
                collect.assert_called_once_with(Path('/dev/sda'))
            self.assertIn('current-serial-' + str(index), output.getvalue())

    def test_collection_uses_inspection_commands_and_preserves_failures(self):
        for smart_status in (0, 8):
            calls = []

            def command(args):
                calls.append(args)
                if args[0] == 'lsblk':
                    value = {'blockdevices': [{'path': '/dev/sda', 'kname': 'sda', 'type': 'disk',
                                              'size': 240057409536, 'fstype': None, 'mountpoints': [None]}]}
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
with pathlib.Path(os.environ['STORAGE_FIXTURE_CALLS']).open('a') as output:
    output.write(sys.argv[-2]+'\\n'+sys.argv[-1]+'\\n')
import json
with pathlib.Path(os.environ['STORAGE_FIXTURE_ARGV']).open('a') as output:
    output.write(json.dumps(sys.argv[1:])+'\\n')
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

    def test_all_nodes_one_private_report_and_no_apply_mode(self):
        with tempfile.TemporaryDirectory() as root:
            result, call = self.exercise(root, ['--user', 'joe'])
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertTrue(call.startswith('joe@192.168.0.200\nsudo /usr/bin/python3 '))
            args = [json.loads(line) for line in (Path(root) / 'ssh-argv.json').read_text().splitlines()]
            self.assertEqual([a[-2] for a in args], ['joe@192.168.0.200', 'joe@192.168.0.201', 'joe@192.168.0.202'])
            for index, entry in enumerate(args, 1):
                bootstrap = shlex.split(entry[-1])[-1]
                self.assertIn('scope["main"](\'node'+str(index)+'\')', bootstrap)
            self.assertIn('RESULT: STORAGE_INSPECTION_COLLECTED', result.stdout)
            report = next((Path(root) / 'reports').glob('*.txt'))
            self.assertEqual(stat.S_IMODE(report.stat().st_mode), 0o600)
            self.assertIn('Node result: node3 COLLECTED', report.read_text())
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
            self.assertIn('Node: node3 (192.168.0.202)', result.stdout)
        with tempfile.TemporaryDirectory() as root:
            result, call = self.exercise(root, ['--user', 'joe;false'])
            self.assertEqual(result.returncode, 2)
            self.assertEqual(call, '')

    def test_existing_project_key_is_selected_without_copying_it(self):
        with tempfile.TemporaryDirectory() as root:
            result, call = self.exercise(root, identity='regular')
            self.assertEqual(result.returncode, 0, result.stdout)
            args = json.loads((Path(root) / 'ssh-argv.json').read_text().splitlines()[0])
            self.assertEqual(args[args.index('-i') + 1], str(Path(root) / 'home/.ssh/openstack-from-zero'))
            self.assertIn('IdentitiesOnly=yes', args)
            self.assertNotIn('fixture identity', call)
            self.assertIn('Key-unlock prompt: key passphrase', result.stdout)

    def test_symlinked_project_key_stops_before_connection(self):
        with tempfile.TemporaryDirectory() as root:
            result, call = self.exercise(root, identity='symlink')
            self.assertEqual(result.returncode, 2)
            self.assertEqual(call, '')

    def test_single_node_selection_and_invalid_node(self):
        with tempfile.TemporaryDirectory() as root:
            result, call = self.exercise(root, ['--node', 'node2'])
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertTrue(call.startswith('joe@192.168.0.201\n'))
            self.assertNotIn('joe@192.168.0.200', call)
            self.assertNotIn('joe@192.168.0.202', call)
        with tempfile.TemporaryDirectory() as root:
            result, call = self.exercise(root, ['--node', 'all;false'])
            self.assertEqual(result.returncode, 2)
            self.assertEqual(call, '')


if __name__ == '__main__':
    unittest.main()

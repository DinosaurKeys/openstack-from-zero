"""No real disk writes: test target rejection, transitions and real wrapper transport."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import pty
import shlex
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('lvm_pool', ROOT / 'scripts/phase1-lvm-node.py')
pool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pool)


class LvmTests(unittest.TestCase):
    def facts(self, state='blank'):
        return {'device': '/dev/sda', 'alias': '/dev/disk/by-id/ata-reviewed', 'serial': 'serial',
                'state': state, 'protected': {'ubuntu': 'unchanged'}, 'pvs': [], 'vgs': [], 'lvs': []}

    def test_plan_never_writes_or_saves_remote_evidence(self):
        command, save = Mock(), Mock()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            pool.prepare('node1', False, lambda _: self.facts(), command, save)
        command.assert_not_called()
        save.assert_not_called()
        self.assertIn('LVM_PLAN_READY node1', output.getvalue())

    def test_blank_disk_pv_vg_sequence_and_final_ubuntu_verification(self):
        command, save = Mock(), Mock()
        states = Mock(side_effect=[self.facts(), self.facts(), self.facts('pv-only'), self.facts('ready')])
        with contextlib.redirect_stdout(io.StringIO()) as output:
            pool.prepare('node2', True, states, command, save)
        self.assertEqual([c.args[0] for c in command.call_args_list],
                         [['pvcreate', '/dev/disk/by-id/ata-reviewed'],
                          ['vgcreate', 'cinder-volumes', '/dev/disk/by-id/ata-reviewed']])
        save.assert_called_once()
        self.assertIn('CINDER_LVM_READY node2', output.getvalue())

    def test_existing_pool_does_not_write_and_partial_pv_is_resumable(self):
        for state in ('ready', 'pv-only'):
            command = Mock()
            snapshots = [self.facts(state)] if state == 'ready' else [self.facts(state), self.facts(state), self.facts('ready')]
            with contextlib.redirect_stdout(io.StringIO()):
                pool.prepare('node3', True, Mock(side_effect=snapshots), command)
            self.assertEqual(command.call_count, 0 if state == 'ready' else 1)
            if command.called:
                self.assertEqual(command.call_args.args[0][0], 'vgcreate')

    def test_changed_state_stops_before_write(self):
        command = Mock()
        different = self.facts(); different['protected'] = {'ubuntu': 'changed'}
        with self.assertRaises(RuntimeError), contextlib.redirect_stdout(io.StringIO()):
            pool.prepare('node1', True, Mock(side_effect=[self.facts(), different]), command)
        command.assert_not_called()

    def test_failure_or_changed_ubuntu_never_reports_ready(self):
        changed = self.facts('ready'); changed['protected'] = {'ubuntu': 'changed'}
        for command, states in [(Mock(side_effect=RuntimeError('pvcreate refused')), [self.facts(), self.facts()]),
                                (Mock(), [self.facts('pv-only'), self.facts('pv-only'), changed])]:
            with self.subTest(), self.assertRaises(RuntimeError), contextlib.redirect_stdout(io.StringIO()) as output:
                pool.prepare('node1', True, Mock(side_effect=states), command)
            self.assertNotIn('CINDER_LVM_READY', output.getvalue())

    def test_classification_refuses_other_owners_signatures_partitions_and_wrong_identity(self):
        disk = {'path': '/dev/sda', 'type': 'disk', 'tran': 'sata', 'serial': 'known',
                'size': pool.SIZE, 'fstype': None, 'mountpoints': [None]}
        self.assertEqual(pool.classify('/dev/sda', disk, [], [], [], [], 'known'), 'blank')
        for key, value in [('serial', 'wrong'), ('tran', 'nvme'), ('size', 1),
                           ('mountpoints', ['/']), ('children', [{'path': '/dev/sda1'}]), ('fstype', 'ext4')]:
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                pool.classify('/dev/sda', {**disk, key: value}, [], [], [], [], 'known')
        for signatures, pvs, vgs, lvs in [([{'type': 'ext4'}], [], [], []),
                ([], [{'pv_name': '/dev/sda', 'vg_name': 'ubuntu-vg'}], [], []),
                ([], [], [{'vg_name': pool.VG, 'pv_count': '1', 'lv_count': '0'}], [])]:
            with self.subTest(), self.assertRaises(RuntimeError):
                pool.classify('/dev/sda', disk, signatures, pvs, vgs, lvs, 'known')
        pv = [{'pv_name': '/dev/sda', 'vg_name': pool.VG}]
        vg = [{'vg_name': pool.VG, 'pv_count': '1', 'lv_count': '0'}]
        self.assertEqual(pool.classify('/dev/sda', disk, [{'type': 'LVM2_member'}], pv, vg, [], 'known'), 'ready')
        with self.assertRaises(RuntimeError):
            pool.classify('/dev/sda', disk, [], pv, [{**vg[0], 'lv_count': '1'}], [{'vg_name': pool.VG}], 'known')

    def test_commands_cannot_answer_overwrite_prompts(self):
        with patch.object(pool.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
            with contextlib.redirect_stdout(io.StringIO()):
                pool.run(['pvcreate', '/dev/disk/by-id/reviewed'])
            self.assertEqual(run.call_args.kwargs['stdin'], subprocess.DEVNULL)

    def test_wrong_node_rejected_before_commands(self):
        with patch.object(pool.os, 'geteuid', return_value=0), patch.object(pool.socket, 'gethostname', return_value='node2'), \
             patch.object(pool, 'run') as run, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(pool.main('node1', True), 1)
            run.assert_not_called()

    def test_full_inspection_rejects_os_backing_fstab_and_swap_use(self):
        for usage in ('none', 'root', 'fstab', 'swap', 'holder'):
            disk = {'path': '/dev/sda', 'kname': 'sda', 'type': 'disk', 'tran': 'sata',
                    'serial': pool.TARGETS['node1'][1], 'size': pool.SIZE, 'fstype': None, 'mountpoints': [None]}
            def command(args):
                if args[0] == 'lsblk' and '--tree' in args:
                    return json.dumps({'blockdevices': [disk]})
                if args[0] == 'lsblk':
                    return '/dev/sda\n' if usage == 'root' else '/dev/mapper/ubuntu--vg-ubuntu--lv\n/dev/nvme0n1p3\n/dev/nvme0n1\n'
                if args[0] == 'findmnt' and '--fstab' in args:
                    return json.dumps({'filesystems': [{'source': '/dev/sda' if usage == 'fstab' else '/dev/mapper/ubuntu--vg-ubuntu--lv', 'target': '/'}]})
                if args[0] == 'findmnt':
                    return '/dev/mapper/ubuntu--vg-ubuntu--lv\n'
                if args[0] in ('pvs', 'vgs', 'lvs'):
                    key = {'pvs': 'pv', 'vgs': 'vg', 'lvs': 'lv'}[args[0]]
                    return json.dumps({'report': [{key: []}]})
                if args[0] == 'wipefs':
                    return '{"signatures": []}'
                raise AssertionError(args)
            def resolve(value):
                return '/dev/sda' if '/dev/disk/by-id/' in str(value) else str(value)
            with self.subTest(usage=usage), patch.object(pool, 'run', side_effect=command), \
                 patch.object(pool, 'canonical', side_effect=resolve), \
                 patch.object(pool.Path, 'stat', return_value=type('Info', (), {'st_mode': stat.S_IFBLK})()), \
                 patch.object(pool.Path, 'iterdir', return_value=['md0'] if usage == 'holder' else []), \
                 patch.object(pool.Path, 'read_text', return_value='Filename Type Size Used Priority\n'+('/dev/sda partition 1 0 -2\n' if usage == 'swap' else '')), \
                 contextlib.redirect_stdout(io.StringIO()):
                if usage == 'none':
                    self.assertEqual(pool.inspect('node1')['state'], 'blank')
                else:
                    with self.assertRaises(RuntimeError):
                        pool.inspect('node1')


FAKE_SSH = '''#!/usr/bin/env python3
import ast,json,os,pathlib,shlex,sys
bootstrap=shlex.split(sys.argv[-1])[-1]
ast.parse(bootstrap)
with pathlib.Path(os.environ['LVM_CALLS']).open('a') as output:
    output.write(json.dumps({'target':sys.argv[-2],'bootstrap':bootstrap})+'\\n')
print('fixture helper result')
sys.exit(1 if os.environ.get('LVM_FAIL')=='1' else 0)
'''


class WrapperTests(unittest.TestCase):
    def exercise(self, args=(), fail=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'scripts').mkdir(); (root / 'bin').mkdir()
            for name in ('phase1-lvm.sh', 'phase1-lvm-node.py'):
                shutil.copyfile(ROOT / 'scripts' / name, root / 'scripts' / name)
            ssh = root / 'bin/ssh'; ssh.write_text(FAKE_SSH); ssh.chmod(0o700)
            calls = root / 'calls.jsonl'
            master, slave = pty.openpty()
            try:
                result = subprocess.run(['bash', str(root / 'scripts/phase1-lvm.sh'), *args], stdin=slave,
                                        text=True, capture_output=True, timeout=15,
                                        env={**os.environ, 'HOME': str(root), 'PATH': str(root / 'bin') + ':' + os.environ['PATH'],
                                             'LVM_CALLS': str(calls), 'LVM_FAIL': str(int(fail))})
            finally:
                os.close(master); os.close(slave)
            entries = [json.loads(s) for s in calls.read_text().splitlines()] if calls.exists() else []
            reports = list((root / 'reports').glob('*.txt'))
            for report in reports:
                self.assertEqual(stat.S_IMODE(report.stat().st_mode), 0o600)
            return result, entries

    def test_default_plan_and_explicit_apply_reach_all_three_with_correct_mode(self):
        for mode in (False, True):
            result, entries = self.exercise(['--apply'] if mode else [])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual([e['target'] for e in entries], ['joe@192.168.0.200', 'joe@192.168.0.201', 'joe@192.168.0.202'])
            for i, entry in enumerate(entries, 1):
                self.assertIn('scope["main"](\'node'+str(i)+'\','+str(mode)+')', entry['bootstrap'])
            self.assertIn('CINDER_LVM_READY for all' if mode else 'LVM_PLAN_READY for all', result.stdout)

    def test_failure_stops_remaining_nodes_and_invalid_node_never_connects(self):
        result, entries = self.exercise(['--apply'], fail=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(entries), 1)
        self.assertNotIn('RESULT: CINDER_LVM_READY', result.stdout)
        result, entries = self.exercise(['--node', 'node4'])
        self.assertEqual(result.returncode, 2)
        self.assertFalse(entries)


if __name__ == '__main__':
    unittest.main()

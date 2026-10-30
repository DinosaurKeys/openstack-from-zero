"""Offline command and classification checks; never contact the lab."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/lab-resource-check.sh'
SOURCE = SCRIPT.read_text()
PARSER = SOURCE.split("| python3 -c '\n", 1)[1].split("\n'\nfi", 1)[0]
UUID = '12345678-1234-1234-1234-123456789abc'


def queue(name, **changes):
    return dict(name=name, type='quorum', messages=0, consumers=0, **changes)


class QueueInventory(unittest.TestCase):
    def parse(self, value):
        return subprocess.run(['/usr/bin/python3', '-c', PARSER], input=json.dumps(value),
                              text=True, capture_output=True)

    def test_counts_only_exact_empty_unused_heat_quorum_names(self):
        records = [queue('engine_worker.' + UUID),
                   queue('heat-engine-listener.' + UUID),
                   queue('nova.' + UUID), queue('engine_worker.not-a-uuid'),
                   dict(queue('engine_worker.' + UUID), messages=1),
                   dict(queue('engine_worker.' + UUID), consumers=1),
                   dict(queue('engine_worker.' + UUID), type='classic')]
        p = self.parse(records)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('Total queues: 7', p.stdout)
        self.assertIn('Heat-named queues: 5', p.stdout)
        self.assertIn('Empty Heat quorum queues with no consumers: 2', p.stdout)
        self.assertIn('nothing deleted', p.stdout)

    def test_incomplete_or_invalid_data_never_becomes_zero_queues(self):
        for value in ({}, [{}], [dict(queue('x'), consumers='0')],
                      [dict(queue('x'), messages=False)], [dict(queue('x'), messages=-1)]):
            with self.subTest(value=value):
                p = self.parse(value)
                self.assertNotEqual(p.returncode, 0)
                self.assertNotIn('Total queues: 0', p.stdout)

    def test_empty_inventory_is_valid_but_not_a_cleanup_approval(self):
        p = self.parse([])
        self.assertEqual(p.returncode, 0)
        self.assertIn('Total queues: 0', p.stdout)
        self.assertIn('Review candidates only', p.stdout)

    def test_invalid_json_reports_unknown_without_a_traceback(self):
        p = subprocess.run(['/usr/bin/python3', '-c', PARSER], input='broker unavailable',
                           text=True, capture_output=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('counts unknown', p.stderr)
        self.assertNotIn('Traceback', p.stderr)


class ReadOnlyCommand(unittest.TestCase):
    def test_unexpected_hostname_blocks_before_sudo_or_docker(self):
        remote = SOURCE.split("<<'CHECK'\n", 1)[1].split('\nCHECK\n', 1)[0]
        with tempfile.TemporaryDirectory() as d:
            hostname = Path(d) / 'hostname'
            hostname.write_text('#!/usr/bin/env bash\necho unexpected\n')
            hostname.chmod(0o700)
            p = subprocess.run(['bash', '-c', remote], text=True, capture_output=True,
                               env=dict(os.environ, lab_expected='node1',
                                        PATH=d + os.pathsep + os.environ['PATH']))
        self.assertEqual(p.returncode, 1)
        self.assertIn('unexpected hostname', p.stderr)
        self.assertNotIn('CPU threads', p.stdout)

    def test_bash_syntax(self):
        subprocess.run(['bash', '-n', str(SCRIPT)], check=True)

    def test_no_mutation_mode(self):
        p = subprocess.run(['bash', str(SCRIPT), '--apply'], text=True, capture_output=True)
        self.assertEqual(p.returncode, 2)
        self.assertIn('no repair or delete mode', p.stderr)

    def run_with_fake_ssh(self, fail_node=False):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            stub = path / 'ssh'
            stub.write_text('#!/usr/bin/env bash\n'
                            'printf "%s\\n" "$@" >> "$LAB_AUDIT_CALLS"\n'
                            'if [[ "${LAB_AUDIT_FAIL:-}" = yes && "$*" = *joe@192.168.0.201* ]]; then exit 1; fi\n'
                            'echo "Simulated host observation"\n')
            stub.chmod(0o700)
            env = dict(os.environ, PATH=d + os.pathsep + os.environ['PATH'],
                       LAB_AUDIT_CALLS=str(path / 'calls'), LAB_AUDIT_FAIL='yes' if fail_node else 'no')
            p = subprocess.run(['bash', str(SCRIPT), '--check'], text=True, capture_output=True, env=env)
            return p, (path / 'calls').read_text()

    def test_checks_all_nodes_with_verified_ssh_host_keys(self):
        p, calls = self.run_with_fake_ssh()
        self.assertEqual(p.returncode, 0, p.stderr)
        for ip in ('200', '201', '202'):
            self.assertIn('joe@192.168.0.' + ip, calls)
        self.assertEqual(calls.count('StrictHostKeyChecking=yes'), 3)
        self.assertIn('RESOURCE_AND_HEAT_CHECK_COLLECTED', p.stdout)
        self.assertNotIn('delete_queue', calls)
        self.assertNotIn('systemctl stop', calls)
        self.assertNotIn('systemctl restart', calls)

    def test_one_unavailable_node_does_not_hide_other_nodes_or_pass(self):
        p, calls = self.run_with_fake_ssh(True)
        self.assertEqual(p.returncode, 1)
        self.assertIn('joe@192.168.0.202', calls)
        self.assertIn('192.168.0.201 check incomplete', p.stderr)
        self.assertIn('RESOURCE_AND_HEAT_CHECK_INCOMPLETE', p.stdout)


if __name__ == '__main__':
    unittest.main()

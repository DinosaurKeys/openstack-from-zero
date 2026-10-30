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


class FullRemoteCollection(unittest.TestCase):
    """Execute the actual remote shell against local command fixtures."""

    def run_collector(self, heat=False, systemd_error=False, rabbit_error=False, script=SCRIPT):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            stubs = {
                'ssh': '''#!/usr/bin/env bash
set -eu
for arg in "$@"; do
  case "$arg" in
    joe@192.168.0.200) export LAB_FIXTURE_NODE=node1 ;;
    joe@192.168.0.201) export LAB_FIXTURE_NODE=node2 ;;
    joe@192.168.0.202) export LAB_FIXTURE_NODE=node3 ;;
  esac
done
exec bash -c "${@: -1}"
''',
                'hostname': '#!/usr/bin/env bash\nprintf "%s\\n" "$LAB_FIXTURE_NODE"\n',
                'sudo': '''#!/usr/bin/env bash
set -eu
if [[ "$1" = -v ]]; then exit 0; fi
[[ "$1" = -n ]] || exit 90
shift
exec "$@"
''',
                'systemctl': '''#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$LAB_FIXTURE_SYSTEMCTL_CALLS"
[[ "${LAB_FIXTURE_SYSTEMD_ERROR:-no}" = no ]] || { echo 'Fixture: unit listing unavailable' >&2; exit 1; }
# Reproduce systemd: a pattern selecting no unit files is an error.
if [[ "$*" = *'kolla-heat_'* && "${LAB_FIXTURE_HEAT:-no}" = no ]]; then exit 1; fi
printf 'ssh.service enabled enabled\\n'
if [[ "${LAB_FIXTURE_HEAT:-no}" = yes ]]; then
  printf 'kolla-heat_engine-container.service enabled enabled\\n'
fi
''',
                'docker': '''#!/usr/bin/env python3
import json,os,sys
args=sys.argv[1:]
if args[0]=='ps':
    if os.environ['LAB_FIXTURE_HEAT']=='yes': print('heat_engine | Up 1 hour')
elif args[0]=='stats': print('CPU=0.2% RAM=100MiB / 15GiB')
elif args[0]=='exec' and args[1:3]==['rabbitmq','rabbitmqctl']:
    if 'version' in args: print('4.2.9')
    elif 'list_vhosts' in args: print('name\\n/')
    elif 'list_queues' in args:
        if os.environ['LAB_FIXTURE_RABBIT_ERROR']=='yes':
            print('Fixture: queue listing unavailable',file=sys.stderr); sys.exit(1)
        print(json.dumps([{'name':'engine_worker.12345678-1234-1234-1234-123456789abc','type':'quorum','messages':0,'consumers':0}]))
    else: sys.exit(90)
else: sys.exit(90)
''',
            }
            for name, content in stubs.items():
                p = root / name
                p.write_text(content)
                p.chmod(0o700)
            calls = root / 'systemctl-calls'
            env = dict(os.environ, PATH=d + os.pathsep + os.environ['PATH'],
                       LAB_FIXTURE_SYSTEMCTL_CALLS=str(calls),
                       LAB_FIXTURE_HEAT='yes' if heat else 'no',
                       LAB_FIXTURE_SYSTEMD_ERROR='yes' if systemd_error else 'no',
                       LAB_FIXTURE_RABBIT_ERROR='yes' if rabbit_error else 'no')
            p = subprocess.run(['bash', str(script), '--check'], env=env, text=True, capture_output=True)
            return p, calls.read_text()

    def test_absent_heat_continues_to_rabbitmq_on_all_nodes(self):
        p, calls = self.run_collector()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.count('Heat startup units:\nNONE found'), 3)
        self.assertEqual(p.stdout.count('RabbitMQ container resource usage:'), 3)
        self.assertIn('Empty Heat quorum queues with no consumers: 1', p.stdout)
        self.assertIn('RESOURCE_AND_HEAT_CHECK_COLLECTED', p.stdout)
        self.assertNotIn('kolla-heat_', calls)

    def test_installed_heat_is_shown_and_unrelated_units_are_filtered(self):
        p, _ = self.run_collector(heat=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.count('kolla-heat_engine-container.service enabled enabled'), 3)
        self.assertNotIn('ssh.service', p.stdout)
        self.assertIn('Total queues: 1', p.stdout)

    def test_actual_systemctl_failure_is_not_hidden_as_absent_heat(self):
        p, _ = self.run_collector(systemd_error=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn('RESOURCE_AND_HEAT_CHECK_INCOMPLETE', p.stdout)
        self.assertNotIn('RESOURCE_AND_HEAT_CHECK_COLLECTED', p.stdout)
        self.assertNotIn('Total queues:', p.stdout)

    def test_rabbitmq_failure_does_not_become_zero_queue_count(self):
        p, _ = self.run_collector(rabbit_error=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn('== node3 ==', p.stdout)
        self.assertIn('RESOURCE_AND_HEAT_CHECK_INCOMPLETE', p.stdout)
        self.assertNotIn('Total queues: 0', p.stdout)


if __name__ == '__main__':
    unittest.main()

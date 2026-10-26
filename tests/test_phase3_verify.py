import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('verify_test', ROOT/'scripts/phase3-verify.py')
step = importlib.util.module_from_spec(spec); spec.loader.exec_module(step)


def records():
    status = dict(step.EXPECTED, wsrep_cluster_state_uuid='12345678-1234-1234-1234-123456789abc')
    return {n: {'node': n, 'observation': {'node': n, 'ready': True,
        'checks': {'fixture': True}, 'facts': {'VIP on management interface': n == 'node2'}},
        'database': {'rc': 0, 'stdout': '\n'.join(k+'\t'+v for k, v in status.items())}}
        for n in step.inv.NODES}


class VerifyTests(unittest.TestCase):
    def test_default_has_no_connections_prompts_or_writes(self):
        with patch.object(step, 'execute') as execute, patch.object(step.getpass, 'getpass') as prompt, patch.object(step.tempfile, 'mkstemp') as write, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(step.main([]), 0)
            execute.assert_not_called(); prompt.assert_not_called(); write.assert_not_called()

    def test_complete_observations_pass(self):
        self.assertTrue(step.summarize(records())['passed'])

    def test_missing_or_wrong_node_cannot_pass(self):
        r = records(); r.pop('node3'); self.assertFalse(step.summarize(r)['passed'])
        r = records(); r['node1']['node'] = 'node2'; self.assertFalse(step.summarize(r)['passed'])

    def test_database_query_failure_missing_and_duplicate_values(self):
        for change in ({'rc': 1}, {'rc': 0, 'stdout': ''}, {'rc': 0, 'stdout': 'wsrep_ready\tON\nwsrep_ready\tON'}):
            r = records(); r['node1']['database'] = change
            self.assertFalse(step.summarize(r)['passed'])

    def test_unsynced_small_nonprimary_or_disconnected_cluster_fails(self):
        for key, value in [('wsrep_cluster_size', '2'), ('wsrep_cluster_status', 'Non-Primary'),
                           ('wsrep_local_state_comment', 'Joining'), ('wsrep_connected', 'OFF'), ('wsrep_ready', 'OFF')]:
            r = records(); db = step.database_status(r['node1']['database']); db[key] = value
            r['node1']['database']['stdout'] = '\n'.join(k+'\t'+v for k, v in db.items())
            self.assertFalse(step.summarize(r)['passed'], key)

    def test_distinct_or_invalid_cluster_identity_fails(self):
        for value in ('12345678-1234-1234-1234-123456789abd', 'invalid', '00000000-0000-0000-0000-000000000000'):
            r = records(); r['node1']['database']['stdout'] = r['node1']['database']['stdout'].replace('12345678-1234-1234-1234-123456789abc', value)
            self.assertFalse(step.summarize(r)['passed'])

    def test_missing_duplicate_vip_and_failed_host_checks_fail(self):
        for owners in ([], ['node1', 'node2']):
            r = records()
            for n, v in r.items(): v['observation']['facts']['VIP on management interface'] = n in owners
            self.assertFalse(step.summarize(r)['passed'])
        r = records(); r['node1']['observation']['checks']['fixture'] = False
        self.assertFalse(step.summarize(r)['passed'])

    def test_playbook_has_only_read_queries_and_private_local_output(self):
        p = json.loads(step.playbook(Path('/private')))[0]
        self.assertEqual(p['hosts'], 'baremetal'); self.assertFalse(p['gather_facts'])
        self.assertEqual(len(p['tasks']), 3)
        self.assertIn('phase3-deploy-node.py', p['tasks'][0]['ansible.builtin.script'])
        command = p['tasks'][1]['ansible.builtin.command']['argv']
        self.assertEqual(command, step.database_command('{{ ansible_host }}'))
        self.assertTrue(command[-1].startswith('--execute=SHOW GLOBAL STATUS'))
        self.assertIn('--defaults-file=/etc/mysql/healthcheck.cnf', command)
        self.assertFalse(any('--password' in arg for arg in command))
        self.assertEqual(p['tasks'][2]['delegate_to'], 'localhost')
        self.assertFalse(p['tasks'][2]['become'])
        self.assertEqual(p['tasks'][2]['ansible.builtin.copy']['mode'], '0600')

    def test_all_nodes_collected_and_passwords_not_saved_even_on_node_failure(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); events = []; logs = []
            def runner(log, env):
                class Runner:
                    def run(self, argv, terminal=False):
                        argv = list(map(str, argv)); events.append(argv)
                        if '--syntax-check' in argv: return
                        n = argv[argv.index('--limit')+1]
                        self_password = env['ANSIBLE_BECOME_PASS']
                        if self_password != 'secret-'+n: raise AssertionError('Wrong node password')
                        if n == 'node1': raise RuntimeError('Fixture connection failure')
                        (root/'observations'/(n+'.json')).write_text(json.dumps(records()[n]))
                return Runner()
            with patch.object(step.deploy.validation.config_step, 'load_step') as local, patch.object(step.tools, 'check_paths'), patch.object(step.deploy, 'source_guard'), patch.object(step.deploy.validation, 'private_file'), patch.object(step.inv, 'inventory_data', return_value={}), patch.object(step.inv, 'validate_inventory'), patch.object(step.inv, 'ensure_agent'), patch.object(step.getpass, 'getpass', side_effect=['secret-'+n for n in step.inv.NODES]), patch.object(step.tools, 'Runner', side_effect=runner):
                self.assertEqual(step.execute(logs.append, root), 1)
            self.assertEqual([e[e.index('--limit')+1] for e in events if '--limit' in e], list(step.inv.NODES))
            self.assertTrue(all(Path(e[0]).name == 'ansible-playbook' for e in events))
            self.assertNotIn('secret-', '\n'.join(logs)+str(events)+''.join(p.read_text() for p in root.rglob('*') if p.is_file()))


if __name__ == '__main__': unittest.main()

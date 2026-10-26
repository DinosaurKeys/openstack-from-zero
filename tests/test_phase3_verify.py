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
node_spec = importlib.util.spec_from_file_location('verify_node_test', ROOT/'scripts/phase3-deploy-node.py')
node_step = importlib.util.module_from_spec(node_spec); node_spec.loader.exec_module(node_step)


def records():
    status = dict(step.EXPECTED, wsrep_cluster_state_uuid='12345678-1234-1234-1234-123456789abc')
    return {n: {'node': n, 'observation': {'node': n, 'ready': True,
        'checks': {'fixture': True}, 'facts': {'VIP on management interface': n == 'node2'}},
        'database': {'rc': 0, 'stdout': '\n'.join(k+'\t'+v for k, v in status.items())}}
        for n in step.inv.NODES}


class VerifyTests(unittest.TestCase):
    def test_file_backend_glance_required_on_node1_only(self):
        states = {n: {'running': True, 'health': 'healthy'} for n in node_step.REQUIRED}
        states.pop('glance_api')
        self.assertFalse(node_step.healthy(states, 'node1'))
        for node in ('node2', 'node3'):
            self.assertNotIn('glance_api', node_step.required_containers(node))
            self.assertTrue(node_step.healthy(states, node))
            missing = dict(states); missing.pop('nova_compute')
            self.assertFalse(node_step.healthy(missing, node))
            unhealthy = {k: dict(v) for k, v in states.items()}
            unhealthy['cinder_volume']['health'] = 'unhealthy'
            self.assertFalse(node_step.healthy(unhealthy, node))
        with self.assertRaises(RuntimeError): node_step.required_containers('unknown')

    def test_glance_present_on_node1_still_requires_health(self):
        states = {n: {'running': True, 'health': 'healthy'} for n in node_step.REQUIRED}
        self.assertTrue(node_step.healthy(states, 'node1'))
        for key, value in [('running', False), ('health', 'starting'), ('health', 'unhealthy')]:
            broken = {k: dict(v) for k, v in states.items()}; broken['glance_api'][key] = value
            self.assertFalse(node_step.healthy(broken, 'node1'))

    def test_unavailable_vip_observations_are_unknown_not_absent(self):
        r = records(); r['node2']['observation'] = {'node': 'node2', 'ready': False, 'error': 'fixture query failed'}
        result = step.summarize(r)
        self.assertFalse(result['passed']); self.assertFalse(result['vip_observations_complete'])
        self.assertIsNone(result['vip_owners'])
        self.assertIn('VIP ownership unverified: address observations are incomplete', result['issues'])
        self.assertIn('node2: fixture query failed', result['issues'])

    def test_docker_failure_retains_vip_and_container_evidence(self):
        states = {n: {'running': True, 'health': 'healthy'} for n in node_step.REQUIRED}
        addresses = [{'ifname': 'enp0s31f6', 'addr_info': [{'local': '192.168.0.201', 'prefixlen': 24}, {'local': '192.168.0.151'}]},
                     {'ifname': 'enx00e04c2c1678', 'address': '00:e0:4c:2c:16:78', 'addr_info': [], 'flags': ['UP', 'LOWER_UP']}]
        def read(args):
            if args[:3] == ['ip', '-j', 'address']: return json.dumps(addresses)
            if args[0] == 'ip': return '[{"dev":"enp0s31f6","gateway":"192.168.0.1"}]'
            if args[0] == 'systemctl': return 'active'
            if args[0] == 'timedatectl': return 'yes'
            raise RuntimeError('Docker OVS fixture failure')
        with patch.object(node_step.os, 'geteuid', return_value=0), patch.dict(node_step.os.environ, {'SUDO_USER': 'joe'}), patch.object(node_step.socket, 'gethostname', return_value='node2'), patch.object(node_step, 'run', side_effect=read), patch.object(node_step, 'container_states', return_value=states):
            result = node_step.collect('node2')
        self.assertFalse(result['ready']); self.assertEqual(result['error'], 'Docker OVS fixture failure')
        self.assertTrue(result['facts']['VIP on management interface'])
        self.assertEqual(result['facts']['containers'], states)

    def test_docker_error_identifies_command_and_exit_code(self):
        from types import SimpleNamespace
        with patch.object(node_step.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='', stderr='fixture: no such port')):
            with self.assertRaisesRegex(RuntimeError, 'exit 1.*docker exec.*no such port'):
                node_step.run(['docker', 'exec', 'openvswitch_vswitchd', 'ovs-vsctl', 'port-to-br', 'fixture0'])

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

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
def load(name, file):
    s = importlib.util.spec_from_file_location(name, ROOT/'scripts'/file)
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
step = load('network_test', 'phase4-network-check.py')
node = load('network_node_test', 'phase4-network-node.py')


class API:
    def __init__(self, next_url=None): self.calls = []; self.next_url = next_url; self.network_pages = 0
    def request(self, url, token=None, body=None, expect=200):
        self.calls.append((url, token, body))
        if body:
            catalog = [{'type': k, 'endpoints': [{'interface': 'public', 'region_id': 'RegionOne',
                'url': 'https://192.168.0.151:'+str(v)}]} for k, v in step.access.SERVICE_PORTS.items()]
            return {'token': {'catalog': catalog}}, 'fixture-token'
        resource = url.split('?')[0].rsplit('/', 1)[1]
        if resource not in step.FIELDS: raise AssertionError(url)
        data = {resource: [{'id': 'fixture', 'name': 'existing', 'password': 'never-save-this', 'configuration': 'not-requested'}]}
        if resource == 'networks':
            self.network_pages += 1
            if self.network_pages == 1 and self.next_url:
                data['networks_links'] = [{'rel': 'next', 'href': self.next_url}]
        return data, None


class NetworkTests(unittest.TestCase):
    def test_wrapper_collects_all_nodes_without_logging_passwords(self):
        for fail_node in (None, 'node2'):
            with tempfile.TemporaryDirectory() as d:
                work = Path(d); events = []; logs = []
                def runner(log, env):
                    class Runner:
                        def run(self, argv, terminal=False):
                            args = list(map(str, argv)); events.append(args)
                            if '--syntax-check' in args: return
                            n = args[args.index('--limit')+1]
                            if env['ANSIBLE_BECOME_PASS'] != 'secret-'+n: raise AssertionError('Wrong password selected')
                            if n == fail_node: raise RuntimeError('Fixture SSH failure')
                            (work/'nodes'/(n+'.json')).write_text(json.dumps({'node': n, 'collected': True, 'checks': {'layout': True}, 'facts': {}}))
                    return Runner()
                with patch.object(step.deploy, 'source_guard'), patch.object(step.deploy.validation, 'local_checks', return_value=(Path('/inventory'), Path('/config'))), patch.object(step.access, 'input_hashes', return_value={}), patch.object(step.access, 'load_profile', return_value={'cacert': '/fixture'}), patch.object(step.access, 'Client'), patch.object(step, 'cloud_state', return_value={k: [] for k in step.FIELDS}), patch.object(step.inv, 'ensure_agent'), patch.object(step.getpass, 'getpass', side_effect=['secret-'+n for n in step.inv.NODES]), patch.object(step.deploy.tools, 'Runner', side_effect=runner):
                    self.assertEqual(step.execute(logs.append, work), 1 if fail_node else 0)
                self.assertEqual([a[a.index('--limit')+1] for a in events if '--limit' in a], list(step.inv.NODES))
                self.assertNotIn('secret-', '\n'.join(logs)+str(events)+''.join(p.read_text() for p in work.rglob('*') if p.is_file()))
                self.assertTrue(all(Path(a[0]).name == 'ansible-playbook' for a in events))

    def test_default_is_noop(self):
        with patch.object(step, 'execute') as execute, patch.object(step.tempfile, 'mkstemp') as write, patch.object(step.getpass, 'getpass') as prompt, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(step.main([]), 0); execute.assert_not_called(); write.assert_not_called(); prompt.assert_not_called()

    def test_api_only_authenticates_then_reads_sanitized_fields(self):
        api = API(); profile = {'auth': {'auth_url': 'https://192.168.0.151:5000', 'username': 'admin', 'password': 'fixture-password',
            'user_domain_name': 'Default', 'project_name': 'admin', 'project_domain_name': 'Default'}, 'region_name': 'RegionOne'}
        data = step.cloud_state(profile, api)
        self.assertEqual(set(data), set(step.FIELDS))
        self.assertEqual(sum(body is not None for _, _, body in api.calls), 1)
        self.assertTrue(all(token == 'fixture-token' for _, token, body in api.calls if body is None))
        self.assertNotIn('never-save-this', json.dumps(data)); self.assertNotIn('fixture-token', json.dumps(data))

    def test_pagination_collects_second_page_and_blocks_wrong_destinations(self):
        profile = {'auth': {'auth_url': 'https://192.168.0.151:5000', 'username': 'admin', 'password': 'fixture-password',
            'user_domain_name': 'Default', 'project_name': 'admin', 'project_domain_name': 'Default'}, 'region_name': 'RegionOne'}
        self.assertEqual(len(step.cloud_state(profile, API('?marker=next'))['networks']), 2)
        for url in ('http://192.168.0.151:9696/v2.0/networks', 'https://other.example/v2.0/networks', 'https://192.168.0.151:9696/v2.0/ports'):
            with self.assertRaises(RuntimeError): step.cloud_state(profile, API(url))

    def test_config_reads_only_allowlisted_network_settings(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'neutron.conf'
            p.write_text('[DEFAULT]\nl3_ha=false\ntransport_url=secret-transport\n[keystone_authtoken]\npassword=secret-password\n')
            result = node.selected_config(p, {'DEFAULT': ['l3_ha', 'agent_mode']})
            self.assertEqual(result, {'DEFAULT': {'l3_ha': 'false', 'agent_mode': None}})
            self.assertNotIn('secret', json.dumps(result))

    def test_ovs_options_filter_secret_keys_and_preserve_tunnel_and_patch_details(self):
        raw = json.dumps({'headings': ['name', 'type', 'options'], 'data': [['vxlan-fixture', 'vxlan',
            ['map', [['remote_ip', '192.168.0.201'], ['local_ip', '192.168.0.200'], ['psk', 'secret'], ['peer', 'patch-peer']]]]]})
        result = node.interfaces(raw)
        self.assertEqual(result[0]['options']['remote_ip'], '192.168.0.201')
        self.assertNotIn('secret', json.dumps(result))
        with self.assertRaises(RuntimeError): node.interfaces('{"headings":["options"],"data":[["invalid"]]}')

    def test_identity_guard_precedes_commands(self):
        with patch.object(node.os, 'geteuid', return_value=1000), patch.object(node, 'run') as read:
            with self.assertRaises(RuntimeError): node.collect('node1')
            read.assert_not_called()

    def test_partial_collection_preserves_other_evidence_and_uses_only_read_commands(self):
        commands = []
        def read(argv):
            commands.append(argv)
            if argv[:3] == ['ip', '-j', 'address']:
                return json.dumps([{'ifname': 'enp0s31f6', 'addr_info': [{'local': '192.168.0.200', 'prefixlen': 24}]},
                    {'ifname': 'enx00e04c557e58', 'addr_info': []}])
            if argv[:4] == ['ip', '-j', '-4', 'route']: return '[]'
            if argv[:2] == ['ip', 'netns']: return ''
            if 'list-br' in argv: return 'br-ex\nbr-int\nbr-tun'
            if 'list-ports' in argv:
                if argv[-1] == 'br-tun': raise RuntimeError('fixture bridge unavailable')
                return 'enx00e04c557e58' if argv[-1] == 'br-ex' else 'patch-fixture'
            if 'Interface' in argv: return '{"headings":["name","type","options"],"data":[]}'
            raise AssertionError(argv)
        with patch.object(node.os, 'geteuid', return_value=0), patch.dict(node.os.environ, {'SUDO_USER': 'joe'}), patch.object(node.socket, 'gethostname', return_value='node1'), patch.object(node, 'run', side_effect=read), patch.object(node, 'selected_config', return_value={}):
            result = node.collect('node1')
        self.assertFalse(result['collected']); self.assertTrue(result['checks']['USB attached to br-ex'])
        self.assertIn('br-tun ports', result['errors']); self.assertIn('OVS interfaces', result['facts'])
        for argv in commands:
            self.assertNotIn('add-br', argv); self.assertNotIn('add-port', argv); self.assertNotIn('set', argv)
            self.assertNotIn('del-br', argv); self.assertNotIn('restart', argv)

    def test_playbook_only_collects_and_saves_locally(self):
        p = json.loads(step.playbook(Path('/private')))[0]
        self.assertEqual(len(p['tasks']), 2); self.assertFalse(p['gather_facts'])
        self.assertIn('phase4-network-node.py', p['tasks'][0]['ansible.builtin.script'])
        self.assertFalse(p['tasks'][1]['become']); self.assertEqual(p['tasks'][1]['delegate_to'], 'localhost')


if __name__ == '__main__': unittest.main()

import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('messaging_repair_test', ROOT/'scripts/lab-nova-messaging.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
h = m.node_helper
RAW = b'[DEFAULT]\ntransport_url = rabbit://user:private-secret@host\n\n[oslo_messaging_rabbit]\nuse_queue_manager = true\nrabbit_transient_queues_ttl = 1800\n'


def targets():
    return {n: {'node': n, 'host': {'host': {'value': {'boot_id': 'boot'}}},
                'targets': {s: {'installed': {'use_queue_manager': True}, 'source_sha256': h.digest(RAW),
                                'desired_sha256': h.digest(h.patched(RAW)), 'started': '2026-10-30T10:00:00Z'}
                            for s in m.SERVICES}} for n in m.inv.NODES}


class MessagingRepairTests(unittest.TestCase):
    def test_only_boolean_changes_secrets_and_other_bytes_preserved(self):
        result = h.patched(RAW)
        self.assertEqual(result, RAW.replace(b'use_queue_manager = true', b'use_queue_manager = false'))
        self.assertEqual(h.patched(result), result)

    def test_ini_formats_comments_crlf_and_section_insertion(self):
        for value in (b'True', b'yes', b'1', b'ON', b'false'):
            raw = b'[oslo_messaging_rabbit]\r\n  use_queue_manager: '+value+b'  # comment\r\n[other]\r\nx = private\r\n'
            result = h.patched(raw)
            self.assertIn(b'  use_queue_manager: false  # comment\r\n', result)
            self.assertTrue(result.endswith(b'[other]\r\nx = private\r\n'))
        self.assertEqual(h.patched(b'', missing=True), h.OVERRIDE)
        raw = b'[oslo_messaging_rabbit]\nx = yes\n[other]\ny = no\n'
        self.assertEqual(h.patched(raw, missing=True), raw.replace(b'[other]', b'use_queue_manager = false\n[other]'))

    def test_ambiguous_templated_and_missing_rendered_value_rejected(self):
        for raw in (b'[oslo_messaging_rabbit]\nuse_queue_manager=unknown\n',
                    b'[oslo_messaging_rabbit]\nuse_queue_manager=true\nuse_queue_manager=false\n',
                    b'[oslo_messaging_rabbit]\nuse_queue_manager={{ dynamic }}\n', b'[DEFAULT]\nx = yes\n'):
            with self.assertRaises(RuntimeError):
                h.patched(raw)

    def test_atomic_update_preserves_mode_and_rejects_drift_or_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'nova.conf'; path.write_bytes(RAW); path.chmod(0o640)
            h.replace(path, h.patched(RAW), h.digest(RAW), os.getuid())
            self.assertEqual(path.read_bytes(), h.patched(RAW))
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            with self.assertRaises(RuntimeError):
                h.replace(path, RAW, h.digest(RAW), os.getuid())
            link = Path(folder)/'link'; link.symlink_to(path)
            with self.assertRaises(RuntimeError):
                h.replace(link, RAW, h.digest(path.read_bytes()), os.getuid())

    def test_persistent_overrides_and_precedence(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder); plan = m.local_plan(config)
            self.assertEqual(len(plan), 2)
            m.save_overrides(plan)
            for name in plan:
                self.assertEqual(Path(name).read_bytes(), h.OVERRIDE)
                self.assertEqual(Path(name).stat().st_mode & 0o777, 0o600)
            later = config/'config/nova/node2'; later.mkdir(mode=0o700)
            source = later/'nova.conf'; source.write_bytes(RAW); source.chmod(0o600)
            with self.assertRaisesRegex(RuntimeError, 'Higher-precedence'):
                m.local_plan(config)
            source.write_bytes(b'[DEFAULT]\nworkers = 2\n')
            self.assertEqual(len(m.local_plan(config)), 2)
            source.write_bytes(h.OVERRIDE)
            self.assertEqual(len(m.local_plan(config)), 2)

    def test_override_drift_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder); plan = m.local_plan(config)
            m.save_overrides(plan)
            with self.assertRaises(FileExistsError):
                m.save_overrides(plan)
            self.assertTrue(all(Path(p).read_bytes() == h.OVERRIDE for p in plan))

    def test_bare_command_sends_nothing(self):
        with patch.object(m.subprocess, 'run', side_effect=AssertionError('command sent')), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(m.main([]), 0)
        self.assertIn('No nodes contacted', out.getvalue())

    def test_node_and_service_allowlist(self):
        with patch.object(m.subprocess, 'run', side_effect=AssertionError('command sent')):
            nodes = m.Nodes({}, {})
            for node, action, service in [('node9', 'inspect', None), ('node1', 'stop', None), ('node1', 'apply', 'mariadb')]:
                with self.assertRaises(RuntimeError):
                    nodes.call(node, action, service)
        with self.assertRaises(RuntimeError):
            h.inspect_service('nova_compute', {})

    def test_heartbeat_requires_fresh_enabled_row(self):
        class API:
            def list(self, *args):
                return [{'host': 'node2', 'binary': 'nova-conductor', 'state': 'up', 'status': 'enabled',
                         'updated_at': '2026-10-30T10:01:00.000000'}]
        self.assertTrue(m.heartbeat(API(), 'node2', 'nova_conductor', '2026-10-30T10:00:59.100000000Z'))
        with patch.object(m.time, 'monotonic', side_effect=[0, 66]):
            with self.assertRaisesRegex(RuntimeError, 'fresh Nova heartbeat'):
                m.heartbeat(API(), 'node2', 'nova_conductor', '2026-10-30T10:02:00Z')

    def test_workload_error_is_preserved_but_active_task_blocks(self):
        class API:
            def __init__(self): self.server = {'id': 'vm', 'status': 'ERROR', 'OS-EXT-STS:task_state': None}
            def list(self, service, path, key):
                return [self.server] if key == 'servers' else []
        api = API(); before = copy.deepcopy(api.server)
        self.assertEqual(m.workload(api)['servers'][0]['status'], 'ERROR')
        self.assertEqual(api.server, before)
        api.server['OS-EXT-STS:task_state'] = 'migrating'
        with self.assertRaisesRegex(RuntimeError, 'operation active'):
            m.workload(api)
        del api.server['OS-EXT-STS:task_state']
        with self.assertRaisesRegex(RuntimeError, 'operation active or unknown'):
            m.workload(api)

    def test_workflow_saves_before_mutation_and_sends_six_once(self):
        original = targets(); current = copy.deepcopy(original); events = []; saved = []; logs = []
        before_inputs = {'protected': 'old'}
        changes = {str(m.REPO/'private/phase2/kolla/config/nova'/(s.replace('_', '-')+'.conf')):
                   {'before': None, 'after': h.digest(h.OVERRIDE), 'content': h.OVERRIDE} for s in m.SERVICES}
        after_inputs = dict(before_inputs, **{p: v['after'] for p, v in changes.items()})
        state = {'scope': m.SCOPE, 'before_inputs': before_inputs, 'workload_before': {'servers': []},
                 'completed': [], 'overrides': {p: {k: v for k, v in c.items() if k != 'content'} for p, c in changes.items()}}
        class Nodes:
            def call(self, node, action, service, expected, cluster):
                self_test.assertTrue(saved[-1]['pending']); events.append((node, service))
                result = current[node]['targets'][service]
                result['installed']['use_queue_manager'] = False
                result['started'] = '2026-10-30T10:01:00Z'
                return {'target': copy.deepcopy(result)}
            def all(self): return copy.deepcopy(current)
        self_test = self
        def save(value): saved.append(copy.deepcopy(value))
        def overrides(plan):
            self.assertTrue(saved[-1]['pending']); events.append('overrides')
        with patch.object(m, 'validate', return_value='cluster'), patch.object(m, 'save_overrides', side_effect=overrides), \
             patch.object(m, 'workload', return_value={'servers': []}), patch.object(m, 'heartbeat', return_value=True):
            m.workflow(Nodes(), original, changes, state, save, logs.append, lambda: None, lambda: after_inputs, object(), lambda: events.append('final'))
        self.assertEqual(events[0], 'overrides')
        self.assertEqual(events[1:-1], [(n, s) for n in m.inv.NODES for s in m.SERVICES])
        self.assertEqual(events[-1], 'final'); self.assertFalse(saved[-1]['pending'])
        self.assertTrue(m.input_transition(saved[-1], before_inputs, after_inputs))
        self.assertIn('RESULT: NOVA_MESSAGING_WORKAROUND_VERIFIED', logs)

    def test_failed_restart_stops_and_preserves_pending_without_retry(self):
        records = targets(); saved = []; calls = []
        changes = {'override': {'before': None, 'after': 'new', 'content': b''}}
        state = {'scope': m.SCOPE, 'before_inputs': {}, 'workload_before': {}, 'completed': [], 'overrides': {}}
        class Nodes:
            def call(self, *args): calls.append(args); raise RuntimeError('SSH outcome unavailable')
        with patch.object(m, 'validate', return_value='cluster'), patch.object(m, 'save_overrides'), patch.object(m, 'workload', return_value={}):
            with self.assertRaisesRegex(RuntimeError, 'outcome unavailable'):
                m.workflow(Nodes(), records, changes, state, lambda v: saved.append(copy.deepcopy(v)), lambda _: None,
                           lambda: None, lambda: {'override': 'new'}, object(), lambda: self.fail('final check'))
        self.assertEqual(len(calls), 1); self.assertTrue(saved[-1]['pending']); self.assertEqual(saved[-1]['completed'], [])

    def test_partial_settings_block_before_any_mutation(self):
        records = targets(); records['node3']['targets']['nova_scheduler']['installed']['use_queue_manager'] = False
        with patch.object(m, 'validate', return_value='cluster'), patch.object(m, 'save_overrides', side_effect=AssertionError('write')):
            with self.assertRaisesRegex(RuntimeError, 'all six'):
                m.workflow(object(), records, {}, {}, lambda _: self.fail('save'), lambda _: None, lambda: None, lambda: {}, object(), lambda: None)

    def test_input_transition_rejects_other_drift_and_incomplete_work(self):
        paths = {str(m.REPO/'private/phase2/kolla/config/nova'/(s.replace('_', '-')+'.conf')) for s in m.SERVICES}
        before = {'protected': 'old'}; after = dict(before, **{p: 'new' for p in paths})
        state = {'scope': m.SCOPE, 'pending': False, 'stage': m.FINISHED, 'before_inputs': before, 'after_inputs': after,
                 'overrides': {p: {'before': None, 'after': 'new'} for p in paths},
                 'completed': [{'node': n, 'service': s, 'heartbeat': True} for n in m.inv.NODES for s in m.SERVICES]}
        self.assertTrue(m.input_transition(state, before, after))
        changed = dict(after, protected='drift'); state['after_inputs'] = changed
        self.assertFalse(m.input_transition(state, before, changed))
        state['after_inputs'] = after; state['completed'].pop()
        self.assertFalse(m.input_transition(state, before, after))

    def test_pinned_kolla_merge_paths_and_defaults(self):
        root = ROOT/'build/real-tools-check/share/kolla-ansible/ansible'
        common = (root/'group_vars/all/common.yml').read_text()
        self.assertIn('node_custom_config: "{{ node_config }}/config"', common)
        for role in ('nova', 'nova-cell'):
            text = (root/'roles'/role/'tasks/config.yml').read_text()
            self.assertIn('{{ node_custom_config }}/nova/{{ item.key }}.conf', text)
            self.assertGreater(text.index('{{ node_custom_config }}/nova/{{ inventory_hostname }}/nova.conf'),
                               text.index('{{ node_custom_config }}/nova/{{ item.key }}.conf'))

    def inspector_fixture(self, directory):
        service = 'nova_scheduler'
        root = directory/'nova-scheduler'; root.mkdir()
        path = root/'nova.conf'; path.write_bytes(RAW); path.chmod(0o660)
        mapping = root/'config.json'
        mapping.write_text(json.dumps({'command': 'nova-scheduler', 'config_files': [
            {'source': '/var/lib/kolla/config_files/nova.conf', 'dest': '/etc/nova/nova.conf'}]}))
        mapping.chmod(0o660)
        metadata = {'mounts': [{'Destination': '/var/lib/kolla/config_files', 'Type': 'bind', 'Source': str(root)}],
                    'env': ['KOLLA_CONFIG_STRATEGY=COPY_ALWAYS', 'private-key=do-not-report'],
                    'started': '2026-10-30T10:00:00Z'}
        installed = {'version': '18.3.0', 'driver_sha256': h.DRIVER_SHA, 'use_queue_manager': True,
                     'config_sha256': h.digest(RAW), 'transient_ttl': 1800}
        commands = []
        def run(args):
            commands.append(args)
            return json.dumps(metadata if args[1] == 'inspect' else installed)
        helpers = {'run': run, 'container': lambda _: {'id': 'a'*64, 'running': True, 'health': 'healthy'},
                   'unit': lambda _: {'sha256': 'unit', 'active': 'active'}, 'healthy': lambda _: True}
        return helpers, metadata, installed, commands, mapping

    def test_actual_node_config_uses_hyphens_container_uses_underscores(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); helper, metadata, installed, commands, mapping = self.inspector_fixture(root)
            real_path = Path
            regular = h.regular
            with patch.object(h, 'Path', side_effect=lambda p: root if str(p) == '/etc/kolla' else real_path(p)), \
                 patch.object(h, 'regular', side_effect=lambda p: regular(p, os.getuid())):
                result = h.inspect_service('nova_scheduler', helper)
            self.assertEqual(result['source_sha256'], h.digest(RAW))
            self.assertEqual(result['desired_sha256'], h.digest(h.patched(RAW)))
            self.assertNotIn('private-key', json.dumps(result))
            self.assertEqual(commands[1][0:3], ['docker', 'exec', 'nova_scheduler'])

    def test_config_strategy_mount_source_and_effective_hash_are_required(self):
        for bad in ('strategy', 'mount', 'mapping', 'version', 'source', 'expiry', 'copied-config'):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as folder:
                root = Path(folder); helper, metadata, installed, commands, mapping = self.inspector_fixture(root)
                if bad == 'strategy': metadata['env'] = ['KOLLA_CONFIG_STRATEGY=COPY_ONCE']
                if bad == 'mount': metadata['mounts'][0]['Source'] = '/etc/kolla/nova_scheduler'
                if bad == 'mapping': mapping.write_text('{"command":"other","config_files":[]}')
                if bad == 'version': installed['version'] = '18.3.1'
                if bad == 'source': installed['driver_sha256'] = 'unreviewed'
                if bad == 'expiry': installed['transient_ttl'] = 0
                if bad == 'copied-config': installed['config_sha256'] = 'different'
                regular = h.regular
                with patch.object(h, 'Path', side_effect=lambda p: root if str(p) == '/etc/kolla' else Path(p)), \
                     patch.object(h, 'regular', side_effect=lambda p: regular(p, os.getuid())):
                    with self.assertRaises(RuntimeError): h.inspect_service('nova_scheduler', helper)

    def test_node_apply_changes_one_config_and_restarts_exact_unit_once(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); directory = root/'nova-conductor'; directory.mkdir()
            conf = directory/'nova.conf'; conf.write_bytes(RAW); conf.chmod(0o660)
            before = {'container': {'id': 'a'*64}, 'unit': {'sha256': 'u', 'name': 'kolla-nova_conductor-container.service'},
                      'installed': {'use_queue_manager': True}, 'source_sha256': h.digest(RAW),
                      'desired_sha256': h.digest(h.patched(RAW)), 'started': 'old', 'mapping_sha256': 'm'}
            after = copy.deepcopy(before); after.update(started='new', source_sha256=before['desired_sha256'])
            after['installed']['use_queue_manager'] = False
            commands = []
            def run(args, **kwargs):
                commands.append(args)
                if args[0] == 'ip': return json.dumps([{'addr_info': [{'local': h.NODES['node1']}]}])
                self.assertEqual(conf.read_bytes(), h.patched(RAW)); return ''
            helpers = {'run': run, 'inspector': lambda: {'collect': lambda _: {}}, 'dependencies': lambda *_: None,
                       'container': lambda _: {'id': 'a'*64}, 'unit': lambda _: {'sha256': 'u'}, 'healthy': lambda _: True}
            regular = h.regular
            with patch.object(h, 'helpers', return_value=helpers), patch.object(h, 'inspect_service', side_effect=[before, after]), \
                 patch.object(h, 'Path', side_effect=lambda p: root if str(p) == '/etc/kolla' else Path(p)), \
                 patch.object(h, 'regular', side_effect=lambda p, owner=0: regular(p, os.getuid())), \
                 patch.object(h.os, 'geteuid', return_value=0), \
                 patch.object(h.socket, 'gethostname', return_value='node1'), patch.dict(os.environ, SUDO_USER='joe'):
                result = h.execute('node1', 'apply', 'nova_conductor', before, 'cluster')
            self.assertEqual([v for v in commands if v[0] == 'systemctl'], [['systemctl', 'restart', 'kolla-nova_conductor-container.service']])
            self.assertFalse(result['target']['installed']['use_queue_manager'])

    def test_incomplete_messaging_repair_blocks_menu_without_touching_records(self):
        menu = m.load('messaging_menu_guard_test', 'lab-migrate.py')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/m.RECEIPT; raw = b'{"pending": true}\n'; path.write_bytes(raw); path.chmod(0o600)
            with self.assertRaisesRegex(RuntimeError, 'messaging repair is incomplete'):
                menu.pending_guard(Path(folder), server='vm')
            self.assertEqual(path.read_bytes(), raw)


if __name__ == '__main__':
    unittest.main()

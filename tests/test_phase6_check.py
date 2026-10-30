"""Readiness simulations; no lab connections or cloud mutations."""
import contextlib
from contextlib import ExitStack as ExitStackForTests
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


m = load('phase6_test', 'phase6-check.py')
n = load('phase6_node_test', 'phase6-capacity-node.py')
PROJECT = 'a' * 32
USER = 'b' * 32
IMAGE = '11111111-1111-4111-8111-111111111111'
SERVER = '22222222-2222-4222-8222-222222222222'
TYPE = '33333333-3333-4333-8333-333333333333'
PROVIDERS = {name: str(i) * 8 + '-1111-4111-8111-' + str(i) * 12 for i, name in enumerate(m.inv.NODES, 4)}
PUB = 'ssh-ed25519 AAAATEST'


def quota(limit=20, used=1, reserved=0, key='in_use'):
    return {'limit': limit, key: used, 'reserved': reserved}


def hosts():
    return {name: {'node': name, 'observed': True, 'cpu_threads': 4,
                  'memory': {'MemAvailable': 5 * 1024**3},
                  'instance_disk_available_bytes': 70 * 1024**3,
                  'active_domain_uuids': []} for name in m.inv.NODES}


class FakeAPI:
    urls = {'compute': 'https://192.168.0.151:8774/v2.1',
            'network': 'https://192.168.0.151:9696', 'block-storage': 'https://192.168.0.151:8776/v3/' + PROJECT}
    client = None
    token = 'NEVER-PRINT-TOKEN'

    def __init__(self, min_ram=1024, old_state='SHUTOFF', task=None):
        self.calls = []
        self.min_ram = min_ram
        self.old_state = old_state
        self.task = task
        self.overrides = {}

    def get(self, service, path):
        self.calls.append(('GET', service, path))
        if (service, path) in self.overrides:
            return copy.deepcopy(self.overrides[service, path])
        if service == 'image':
            assert path == 'v2/images/' + IMAGE
            return {'id': IMAGE, 'name': m.prep.PLAN['image_name'], 'owner': PROJECT,
                    'status': 'active', 'disk_format': 'qcow2', 'min_ram': self.min_ram, 'min_disk': 10}
        if path == 'os-keypairs/lab-vm-key':
            return {'keypair': {'name': 'lab-vm-key', 'public_key': PUB}}
        if path == 'servers/' + SERVER:
            return {'server': {'id': SERVER, 'name': 'lab-vm-01', 'tenant_id': PROJECT, 'status': self.old_state,
                               'OS-EXT-STS:task_state': self.task, 'OS-EXT-SRV-ATTR:host': 'node2'}}
        if path == 'os-quota-sets/' + PROJECT + '/detail':
            return {'quota_set': {'instances': quota(), 'cores': quota(), 'ram': quota(20000, 2048)}}
        if path == 'v2.0/quotas/' + PROJECT + '/details.json':
            return {'quota': {k: quota(100, 1, key='used') for k in
                              ('port', 'floatingip', 'security_group', 'security_group_rule')}}
        if path == 'types/default':
            return {'volume_type': {'id': TYPE, 'name': '__DEFAULT__', 'extra_specs': {}}}
        if path == 'scheduler-stats/get_pools?detail=true':
            return {'pools': [{'name': node + '@lvm-1#lvm-1', 'capabilities': {
                'volume_backend_name': 'lvm-1', 'storage_protocol': 'iSCSI', 'free_capacity_gb': 200.5}}
                for node in m.inv.NODES]}
        if path == 'os-quota-sets/' + PROJECT + '?usage=True':
            return {'quota_set': {**{k: quota(100, 1) for k in
                                    ('volumes', 'gigabytes', 'volumes___DEFAULT__', 'gigabytes___DEFAULT__')},
                                  'per_volume_gigabytes': {'limit': -1}}}
        if service == 'placement':
            if path.endswith('/inventories'):
                return {'resource_provider_generation': 3, 'inventories': {
                    k: {'total': total, 'reserved': 0, 'min_unit': 1, 'max_unit': total,
                        'step_size': 1, 'allocation_ratio': 1.0}
                    for k, total in [('VCPU', 4), ('MEMORY_MB', 15000), ('DISK_GB', 80)]}}
            if path.endswith('/usages'):
                return {'resource_provider_generation': 3, 'usages': {'VCPU': 1, 'MEMORY_MB': 2048, 'DISK_GB': 10}}
        raise AssertionError(('Unexpected GET', service, path))

    def list(self, service, path, key):
        self.calls.append(('GET', service, path))
        if key == 'resource_providers':
            return [{'name': n, 'uuid': u} for n, u in PROVIDERS.items()]
        if key == 'flavors':
            return [{'id': 'flavor-2g', 'name': 'lab.small', 'vcpus': 1, 'ram': 2048, 'disk': 10, 'swap': ''}]
        if key in ('servers', 'volumes', 'ports', 'security_groups'):
            return []
        raise AssertionError(('Unexpected list', service, path, key))

    def write(self, *args, **kwargs):
        raise AssertionError('No cloud changes permitted')


class ReadinessTests(unittest.TestCase):
    def collect(self, api, host_values=None, run_pipeline=False):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            folder = root / 'private/phase4'
            folder.mkdir(parents=True)
            states = {
                'vm-preparation.json': {'project_id': PROJECT, 'user_id': USER, 'apis': api.urls,
                                       'ids': {'image': IMAGE}, 'public_key': PUB},
                # Match the actual Chapter B schema: project and network API, no user_id/apis.
                'networks.json': {'project_id': PROJECT, 'api': api.urls['network'], 'plan': m.prep.net.PLAN, 'ids': {}},
                'vm.json': {'project_id': PROJECT, 'user_id': USER, 'apis': api.urls, 'ids': {'server': SERVER}},
            }
            for name, value in states.items():
                path = folder / name
                path.write_text(json.dumps(value))
                path.chmod(0o600)
            key = root / 'guest-key'
            key.write_text('private placeholder: never read or logged')
            key.chmod(0o600)
            Path(str(key) + '.pub').write_text(PUB + ' joe\n')
            rows, details = [], {}
            def check(label, function):
                try:
                    ok, value = function()
                    rows.append({'check': label, 'status': 'PASS' if ok else 'REVIEW'})
                    details[label] = value
                    return value
                except Exception as error:
                    rows.append({'check': label, 'status': 'BLOCKED'})
                    details[label] = {'error': type(error).__name__}
            with ExitStackForTests() as stack:
                stack.enter_context(patch.object(m, 'REPO', root))
                stack.enter_context(patch.object(m.prep, 'KEY', key))
                stack.enter_context(patch.object(m, 'network_info', return_value={'free_private_addresses': 250, 'free_external_addresses': 45}))
                if not run_pipeline:
                    m.collect_cloud(api, PROJECT, USER, host_values or hosts(), check, details)
                else:
                    (root / 'private/phase5').mkdir()
                    original = {p: p.read_bytes() for p in folder.glob('*.json')}
                    stack.enter_context(patch.object(m.deploy, 'source_guard'))
                    stack.enter_context(patch.object(m, 'r2d2_info', return_value=(True, {'message': 'Ubuntu 24.04 x86_64 fixture'})))
                    stack.enter_context(patch.object(m.deploy.tools, 'tool_environment', return_value={}))
                    stack.enter_context(patch.object(m.deploy.validation, 'local_checks', return_value=(root / 'inventory', root / 'config')))
                    stack.enter_context(patch.object(m.prep.access, 'input_hashes', return_value={'same': 'hash'}))
                    stack.enter_context(patch.object(m.inv, 'ensure_agent'))
                    stack.enter_context(patch.object(m.getpass, 'getpass', return_value='DO-NOT-LOG-PASSWORD'))
                    stack.enter_context(patch.object(m.health, 'host_observe', side_effect=lambda node, password, env: {'node': node, 'observed': True}))
                    stack.enter_context(patch.object(m, 'capacity_observe', side_effect=lambda node, password, env: (host_values or hosts())[node]))
                    stack.enter_context(patch.object(m.health, 'host_rows', side_effect=lambda h, r, d: r.append({'check': 'host fixtures', 'status': 'PASS'})))
                    stack.enter_context(patch.object(m.health, 'api_checks', side_effect=lambda r, d, env, include_workload: r.append({'check': 'API fixtures', 'status': 'PASS'})))
                    stack.enter_context(patch.object(m.prep, 'authenticate', return_value=(api, PROJECT, USER)))
                    stack.enter_context(patch.object(m, 'ReadAPI', return_value=api))
                    stack.enter_context(patch.object(m.subprocess, 'run', side_effect=AssertionError('No external commands from simulation')))
                    output = []
                    status = m.execute(output.append, details)
                    self.assertEqual(original, {p: p.read_bytes() for p in folder.glob('*.json')})
                    self.assertNotIn('DO-NOT-LOG-PASSWORD', '\n'.join(output) + json.dumps(details))
                    self.assertNotIn(api.token, '\n'.join(output) + json.dumps(details))
                    return status, output, details
            return rows, details

    def test_complete_compatible_inputs_pass_without_changes(self):
        api = FakeAPI()
        rows, details = self.collect(api)
        self.assertTrue(all(r['status'] == 'PASS' for r in rows), rows)
        self.assertEqual(details['assessed_ram_mb_each'], 1024)
        self.assertTrue(all(c[0] == 'GET' for c in api.calls))
        self.assertNotIn(api.token, json.dumps(details))
        text, passed = m.render(rows, details)
        self.assertTrue(passed)
        self.assertIn('RESULT: PHASE6_READINESS_PASSED', '\n'.join(text))

    def test_real_course_image_floor_blocks_1g_plan_but_assesses_2g(self):
        api = FakeAPI(min_ram=2048)
        rows, details = self.collect(api)
        self.assertEqual(next(r['status'] for r in rows if r['check'] == 'Approved size meets image minimum'), 'REVIEW')
        self.assertEqual(details['assessed_ram_mb_each'], 2048)
        ram = next(r for r in details['Compute and network quotas']['rows'] if r['item'] == 'RAM MiB')
        self.assertEqual(ram['needed'], 4096)
        text, passed = m.render(rows, details)
        self.assertFalse(passed)
        self.assertTrue(any('needs your review; approved size was not changed' in line for line in text))
        self.assertIn('Reusable matching flavor: lab.small', details['Flavor options at assessed RAM']['message'])

    def test_old_active_vm_does_not_trigger_a_stop(self):
        api = FakeAPI(old_state='ACTIVE')
        h = hosts()
        h['node2']['active_domain_uuids'] = [SERVER]
        rows, details = self.collect(api, h)
        self.assertEqual(next(r['status'] for r in rows if r['check'] == 'Old lab-vm-01 safely off'), 'REVIEW')
        self.assertEqual(details['Old lab-vm-01 safely off']['active_hosts'], ['node2'])
        self.assertTrue(all(c[0] == 'GET' for c in api.calls))

    def test_missing_task_field_cannot_establish_vm_is_idle(self):
        api = FakeAPI()
        api.overrides['compute', 'servers/' + SERVER] = {'server': {'id': SERVER, 'name': 'lab-vm-01', 'tenant_id': PROJECT, 'status': 'SHUTOFF'}}
        rows, _ = self.collect(api)
        self.assertEqual(next(r['status'] for r in rows if r['check'] == 'Old lab-vm-01 safely off'), 'BLOCKED')

    def test_vm_assigned_outside_lab_cannot_pass_runtime_check(self):
        api = FakeAPI()
        value = api.get('compute', 'servers/' + SERVER)
        value['server']['OS-EXT-SRV-ATTR:host'] = 'node4'
        api.overrides['compute', 'servers/' + SERVER] = value
        rows, _ = self.collect(api)
        self.assertEqual(next(r['status'] for r in rows if r['check'] == 'Old lab-vm-01 safely off'), 'BLOCKED')

    def test_absent_quota_is_blocked_without_hiding_storage(self):
        api = FakeAPI()
        api.overrides['network', 'v2.0/quotas/' + PROJECT + '/details.json'] = {'quota': {}}
        rows, details = self.collect(api)
        self.assertEqual(next(r['status'] for r in rows if r['check'] == 'Compute and network quotas'), 'BLOCKED')
        self.assertEqual(next(r['status'] for r in rows if r['check'] == 'Cinder space and quotas'), 'PASS')

    def test_bad_image_status_blocks_instead_of_editing_image(self):
        api = FakeAPI()
        value = api.get('image', 'v2/images/' + IMAGE)
        value['status'] = 'queued'
        api.overrides['image', 'v2/images/' + IMAGE] = value
        rows, _ = self.collect(api)
        self.assertEqual(next(r['status'] for r in rows if r['check'] == 'Existing Ubuntu image'), 'BLOCKED')

    def test_read_client_refuses_create_stop_and_delete(self):
        api = m.ReadAPI(None, {}, 'secret')
        for method in ('POST', 'DELETE', 'PUT'):
            with self.assertRaises(RuntimeError):
                api.write('compute', method, 'servers', {}, 202)

    def test_checkpoint_from_another_project_is_rejected_before_cloud_reads(self):
        api = FakeAPI()
        with patch.object(m.health, 'read_private', return_value={'project_id': 'c' * 32}):
            with self.assertRaises(m.EvidenceError):
                m.bound_state('vm-preparation.json', api, PROJECT, USER)
        self.assertEqual(api.calls, [])

    def test_default_plan_and_unknown_apply_flag_never_connect(self):
        with patch.object(m.subprocess, 'run', side_effect=AssertionError('No commands')), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(m.main([]), 0)
        self.assertIn('PLAN ONLY', out.getvalue())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            m.main(['--apply'])

    def test_full_pipeline_retains_original_checkpoints_and_finishes(self):
        api = FakeAPI()
        status, output, details = self.collect(api, run_pipeline=True)
        self.assertEqual(status, 0, '\n'.join(output))
        self.assertEqual(details['summary'][-1]['status'], 'PASS')
        self.assertTrue(all(call[0] == 'GET' for call in api.calls))

    def test_full_pipeline_reports_image_floor_and_running_old_vm_without_changes(self):
        api = FakeAPI(min_ram=2048, old_state='ACTIVE')
        h = hosts()
        h['node2']['active_domain_uuids'] = [SERVER]
        status, output, details = self.collect(api, h, run_pipeline=True)
        self.assertEqual(status, 1)
        self.assertIn('RESULT: PHASE6_READINESS_NEEDS_REVIEW', '\n'.join(output))
        self.assertEqual(details['assessed_ram_mb_each'], 2048)
        self.assertTrue(all(call[0] == 'GET' for call in api.calls))


class CapacityAndQuotaTests(unittest.TestCase):
    def test_uninstalled_terraform_is_expected_during_readiness(self):
        with patch.object(m.Path, 'read_text', return_value='ID=ubuntu\nVERSION_ID="24.04"\n'), patch.object(m.platform, 'machine', return_value='x86_64'), patch.object(m.shutil, 'disk_usage', return_value=SimpleNamespace(free=3 * 1024**3)), patch.object(m.shutil, 'which', return_value=None), patch.object(m.subprocess, 'run', side_effect=AssertionError('No installation or commands')):
            ok, value = m.r2d2_info()
        self.assertTrue(ok)
        self.assertIsNone(value['terraform_version'])

    def test_existing_terraform_is_queried_for_version_only(self):
        p = subprocess.CompletedProcess([], 0, '{"terraform_version":"1.17.1"}', '')
        with patch.object(m.Path, 'read_text', return_value='ID=ubuntu\nVERSION_ID="24.04"\n'), patch.object(m.platform, 'machine', return_value='x86_64'), patch.object(m.shutil, 'disk_usage', return_value=SimpleNamespace(free=3 * 1024**3)), patch.object(m.shutil, 'which', return_value='/usr/bin/terraform'), patch.object(m.subprocess, 'run', return_value=p) as run:
            ok, value = m.r2d2_info()
        self.assertTrue(ok)
        self.assertEqual(value['terraform_version'], '1.17.1')
        self.assertEqual(run.call_args.args[0], ['/usr/bin/terraform', 'version', '-json'])

    def test_reservations_count_and_minus_one_means_unlimited(self):
        self.assertFalse(m.quota_row('VMs', quota(10, 8, 1), 2)['ok'])
        self.assertTrue(m.quota_row('RAM', quota(-1, 100000, 10), 4096)['ok'])
        self.assertFalse(m.quota_row('Volume GiB', quota(10, 7, 0), 5)['ok'])
        for bad in (None, {}, {'limit': -2, 'in_use': 0, 'reserved': 0}, {'limit': 10, 'in_use': False, 'reserved': 0}):
            with self.assertRaises(m.EvidenceError):
                m.quota_row('VMs', bad, 2)

    def test_free_ips_subtract_allocated_ports_and_preserve_subnet_scope(self):
        subnet = {'id': 's', 'cidr': '192.168.0.0/24', 'allocation_pools': [{'start': '192.168.0.152', 'end': '192.168.0.155'}]}
        ports = [{'fixed_ips': [{'subnet_id': 's', 'ip_address': '192.168.0.154'}, {'subnet_id': 'other', 'ip_address': '192.168.0.152'}]}]
        self.assertEqual(m.available_addresses(subnet, ports), 3)

    def test_placement_uses_allocations_ratio_and_unit_limits(self):
        inv = {'total': 4, 'reserved': 1, 'min_unit': 1, 'max_unit': 4, 'step_size': 1, 'allocation_ratio': 2.0}
        self.assertEqual(m.inventory_remaining(inv, 4, 1), 2)
        self.assertEqual(m.inventory_remaining(inv, 0, 5), 0)
        with self.assertRaises(m.EvidenceError):
            m.inventory_remaining(dict(inv, allocation_ratio=float('nan')), 0, 1)

    def test_physical_margin_limits_slots_even_when_placement_has_space(self):
        api = FakeAPI()
        h = hosts()
        for item in h.values():
            item['memory']['MemAvailable'] = 2 * 1024**3
        rows = m.capacity_info(api, h, 2048)
        self.assertEqual(sum(r['review_slots'] for r in rows), 0)

    def test_changed_placement_generation_is_not_a_pass(self):
        api = FakeAPI()
        api.overrides['placement', 'resource_providers/' + PROVIDERS['node1'] + '/usages'] = {
            'resource_provider_generation': 4, 'usages': {'VCPU': 1, 'MEMORY_MB': 2048, 'DISK_GB': 10}}
        with self.assertRaises(RuntimeError):
            m.capacity_info(api, hosts(), 1024)

    def test_five_gib_volume_needs_five_gib_of_type_quota(self):
        api = FakeAPI()
        value = api.get('block-storage', 'os-quota-sets/' + PROJECT + '?usage=True')
        value['quota_set']['gigabytes___DEFAULT__'] = quota(5, 1)
        api.overrides['block-storage', 'os-quota-sets/' + PROJECT + '?usage=True'] = value
        result = m.storage_info(api, PROJECT)
        self.assertFalse(result['fits'])
        row = next(r for r in result['quotas'] if r['item'] == 'Type GiB')
        self.assertEqual(row['needed'], 5)
        self.assertEqual(row['remaining'], 4)

    def test_host_identity_is_checked_before_any_commands(self):
        with patch.object(n.os, 'geteuid', return_value=0), patch.dict(os.environ, {'SUDO_USER': 'joe'}), patch.object(n.socket, 'gethostname', return_value='wrong'), patch.object(n, 'run', side_effect=AssertionError('No command')):
            with self.assertRaises(RuntimeError):
                n.collect('node1')

    def test_node_collector_reads_actual_nova_mount_and_readonly_domains(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'instances').mkdir()
            calls = []
            def query(args):
                calls.append(args)
                if args[0] == 'ip':
                    return json.dumps([{'addr_info': [{'local': '192.168.0.200'}]}])
                if args[:2] == ['docker', 'inspect']:
                    return json.dumps({'running': True, 'mounts': [{'Destination': '/var/lib/nova', 'Source': d}]})
                if 'virsh' in args:
                    self.assertIn('--readonly', args)
                    return SERVER + '\n'
                raise AssertionError(args)
            def text(path, *args, **kwargs):
                if str(path) == '/etc/kolla/nova-compute/nova.conf':
                    return '[DEFAULT]\n'
                if str(path) == '/proc/meminfo':
                    return 'MemTotal: 16000000 kB\nMemAvailable: 5000000 kB\nSwapTotal: 4000000 kB\nSwapFree: 4000000 kB\n'
                raise AssertionError(path)
            with patch.object(n.os, 'geteuid', return_value=0), patch.dict(os.environ, {'SUDO_USER': 'joe'}), patch.object(n.socket, 'gethostname', return_value='node1'), patch.object(n, 'run', side_effect=query), patch.object(n.Path, 'read_text', text), patch.object(n.os, 'statvfs', return_value=SimpleNamespace(f_bavail=1000, f_frsize=4096)):
                value = n.collect('node1')
            self.assertTrue(value['observed'])
            self.assertEqual(value['instance_disk_available_bytes'], 4096000)
            self.assertEqual(value['active_domain_uuids'], [SERVER])
            self.assertEqual(value['memory']['MemAvailable'], 5000000 * 1024)
            self.assertTrue(all(c[0] in ('ip', 'docker') for c in calls))
            self.assertNotIn('start', json.dumps(calls))
            self.assertNotIn('stop', json.dumps(calls))


if __name__ == '__main__':
    unittest.main()

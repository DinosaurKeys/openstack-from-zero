import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
s = importlib.util.spec_from_file_location('create_network_test', ROOT/'scripts/phase4-create-networks.py')
step = importlib.util.module_from_spec(s); s.loader.exec_module(step)


class API:
    def __init__(self):
        self.objects = {k: [] for k in ('networks', 'subnets', 'routers', 'ports')}; self.writes = []; self.fail = None
        self.agents = [{'host': h, 'binary': b, 'alive': True, 'admin_state_up': True}
            for h in ('node1', 'node2', 'node3') for b in ('neutron-openvswitch-agent', 'neutron-l3-agent', 'neutron-dhcp-agent', 'neutron-metadata-agent')]
    def list(self, kind, query=''):
        if kind == 'extensions': return [{'alias': a} for a in ('router', 'external-net', 'provider', 'agent', 'l3_agent_scheduler', 'l3-ha', 'dvr')]
        return copy.deepcopy(self.agents if kind == 'agents' else self.objects[kind])
    def write(self, method, path, body, status):
        self.writes.append((method, path, copy.deepcopy(body), status))
        if self.fail == path: raise RuntimeError('Fixture ambiguous API timeout')
        if method == 'POST':
            singular = path[:-1]; obj = copy.deepcopy(body[singular]); obj['id'] = path+str(len(self.objects[path])+1)
            obj['status'] = 'ACTIVE'
            if path == 'routers':
                obj['external_gateway_info']['external_fixed_ips'][0]['ip_address'] = '192.168.0.152'
                self.objects['ports'].append({'id': 'gatewayport', 'device_id': obj['id'], 'device_owner': 'network:router_gateway',
                    'fixed_ips': copy.deepcopy(obj['external_gateway_info']['external_fixed_ips']), 'status': 'ACTIVE'})
            self.objects[path].append(obj); return {singular: copy.deepcopy(obj)}
        if method == 'PUT':
            router_id = path.split('/')[1]
            self.objects['ports'].append({'id': 'privateport', 'device_id': router_id, 'device_owner': 'network:router_interface',
                'fixed_ips': [{'subnet_id': body['subnet_id'], 'ip_address': '10.10.10.1'}], 'status': 'ACTIVE'})
            return {'id': router_id, 'subnet_id': body['subnet_id'], 'port_id': 'privateport'}
        raise AssertionError((method, path))
    def get(self, path):
        if path.endswith('/l3-agents'): return {'agents': [{'host': 'node2', 'alive': True, 'admin_state_up': True}]}
        kind, ident = path.split('/')
        return {kind[:-1]: copy.deepcopy(next(o for o in self.objects[kind] if o['id'] == ident))}


def state(): return {'ids': {}}


class CreationTests(unittest.TestCase):
    def test_missing_extension_blocks_writes(self):
        api = API(); original = api.list
        api.list = lambda kind, query='': [] if kind == 'extensions' else original(kind, query)
        with self.assertRaisesRegex(RuntimeError, 'extensions are unavailable'): self.create(api)
        self.assertEqual(api.writes, [])

    def test_down_router_ports_do_not_produce_success_or_repeat_creates(self):
        api = API(); original = api.list
        def down(kind, query=''):
            values = original(kind, query)
            if kind == 'ports':
                for p in values: p['status'] = 'DOWN'
            return values
        api.list = down
        with patch.object(step.time, 'monotonic', side_effect=[0, 181]):
            with self.assertRaisesRegex(RuntimeError, 'not both reached ACTIVE'): self.create(api)
        self.assertEqual(len(api.writes), 6)

    def test_write_transport_preserves_method_and_never_exposes_bearer_token_on_failure(self):
        client = step.access.Client.__new__(step.access.Client)
        class Response:
            status = 201
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self, n): return b'{"network":{"id":"fixture"}}'
        with patch.object(client, 'opener', create=True) as opener:
            opener.open.return_value = Response()
            api = step.Neutron(client, 'https://192.168.0.151:9696', 'private-token')
            result = api.write('POST', 'networks', {'network': {'name': 'fixture'}}, 201)
            req = opener.open.call_args[0][0]
            self.assertEqual(req.get_method(), 'POST'); self.assertEqual(req.headers['X-auth-token'], 'private-token')
            self.assertEqual(json.loads(req.data), {'network': {'name': 'fixture'}})
            self.assertEqual(result['network']['id'], 'fixture')
            opener.open.side_effect = urllib.error.HTTPError(req.full_url, 409, 'private-token', {}, None)
            with self.assertRaisesRegex(RuntimeError, '^POST networks: HTTP 409; no automatic retry$'):
                api.write('POST', 'networks', {}, 201)

    def create(self, api, value=None):
        value = value if value is not None else state(); saved = []; logs = []
        result = step.create(api, 'project0', value, lambda v: saved.append(copy.deepcopy(v)), logs.append)
        return result, value, saved, logs

    def test_default_plan_has_no_prompts_files_or_network_calls(self):
        with patch.object(step, 'execute') as execute, patch.object(step.tempfile, 'mkstemp') as write, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(step.main([]), 0); execute.assert_not_called(); write.assert_not_called()

    def test_first_apply_creates_exact_scope_and_router_attachment(self):
        api = API(); result, value, saved, logs = self.create(api)
        self.assertEqual(len(api.writes), 6)
        self.assertEqual([w[1] for w in api.writes[:5]], ['networks', 'subnets', 'networks', 'subnets', 'routers'])
        self.assertEqual(api.writes[-1][0], 'PUT'); self.assertTrue(api.writes[-1][1].endswith('/add_router_interface'))
        self.assertEqual(len(value['ids']), 5); self.assertNotIn('pending', value)
        self.assertEqual(result['router_host'], 'node2')
        self.assertEqual(result['router_external_ips'][0]['ip_address'], '192.168.0.152')
        self.assertEqual(len(saved), 12)

    def test_external_dhcp_disabled_tenant_dhcp_enabled_and_pool_excludes_hosts(self):
        api = API(); self.create(api)
        ext, private = api.objects['subnets']
        self.assertFalse(ext['enable_dhcp']); self.assertTrue(private['enable_dhcp'])
        self.assertEqual(ext['allocation_pools'], [{'start': '192.168.0.152', 'end': '192.168.0.199'}])
        self.assertEqual(private['gateway_ip'], '10.10.10.1'); self.assertEqual(private['dns_nameservers'], ['192.168.0.1'])
        router = api.objects['routers'][0]
        self.assertFalse(router['ha']); self.assertFalse(router['distributed'])
        self.assertTrue(router['external_gateway_info']['enable_snat'])

    def test_rerun_verifies_tracked_resources_and_makes_no_writes(self):
        api = API(); _, value, _, _ = self.create(api); api.writes.clear()
        self.create(api, value); self.assertEqual(api.writes, [])

    def test_partial_confirmed_state_resumes_only_remaining_objects(self):
        api = API(); api.fail = 'subnets'; value = state()
        with self.assertRaises(RuntimeError): self.create(api, value)
        self.assertEqual(set(value['ids']), {'external_network'})
        self.assertEqual(value['pending'], 'external_subnet')
        # Pending means an ambiguous request; even a caller rerun must stop before writes.
        api.fail = None; api.writes.clear()
        with self.assertRaisesRegex(RuntimeError, 'unconfirmed outcome'): self.create(api, value)
        self.assertEqual(api.writes, [])

    def test_confirmed_partial_state_with_no_pending_can_resume(self):
        api = API(); value = state()
        expected = step.specifications({}, 'project0')['external_network']
        obj = api.write('POST', 'networks', {'network': expected}, 201)['network']
        value['ids']['external_network'] = obj['id']; api.writes.clear()
        self.create(api, value); self.assertEqual(len(api.writes), 5)

    def test_untracked_name_conflicts_stop_before_writing(self):
        api = API(); api.objects['networks'].append({'id': 'foreign', 'name': step.PLAN['external_name']})
        with self.assertRaisesRegex(RuntimeError, 'untracked'): self.create(api)
        self.assertEqual(api.writes, [])

    def test_tracked_modification_or_deletion_is_not_overwritten(self):
        api = API(); _, value, _, _ = self.create(api); api.writes.clear()
        api.objects['subnets'][1]['enable_dhcp'] = False
        with self.assertRaisesRegex(RuntimeError, 'differs from plan'): self.create(api, value)
        self.assertEqual(api.writes, [])
        api = API(); _, value, _, _ = self.create(api); api.writes.clear(); api.objects['routers'].clear()
        with self.assertRaisesRegex(RuntimeError, 'missing or renamed'): self.create(api, value)
        self.assertEqual(api.writes, [])

    def test_overlap_or_existing_flat_physnet_blocks_all_writes(self):
        api = API(); api.objects['subnets'].append({'id': 'other', 'name': 'other', 'cidr': '10.10.0.0/16'})
        with self.assertRaisesRegex(RuntimeError, 'overlaps'): self.create(api)
        self.assertEqual(api.writes, [])
        api = API(); api.objects['networks'].append({'id': 'other', 'name': 'other', 'provider:network_type': 'flat', 'provider:physical_network': 'physnet1'})
        with self.assertRaisesRegex(RuntimeError, 'another flat network'): self.create(api)
        self.assertEqual(api.writes, [])

    def test_unhealthy_or_duplicate_agent_blocks_all_writes(self):
        for mode in ('down', 'duplicate'):
            api = API()
            if mode == 'down': api.agents[0]['alive'] = False
            else: api.agents.append(copy.deepcopy(api.agents[0]))
            with self.assertRaisesRegex(RuntimeError, 'network agent needs review'): self.create(api)
            self.assertEqual(api.writes, [])

    def test_wrong_router_gateway_or_private_interface_cannot_pass(self):
        api = API(); _, value, _, _ = self.create(api); api.writes.clear()
        api.objects['routers'][0]['external_gateway_info']['external_fixed_ips'][0]['ip_address'] = '192.168.0.200'
        with self.assertRaisesRegex(RuntimeError, 'differs from plan'): self.create(api, value)
        self.assertEqual(api.writes, [])

    def test_pending_interface_request_stops_without_repeating_it(self):
        api = API(); api.fail = 'routers/routers1/add_router_interface'; value = state()
        with self.assertRaises(RuntimeError): self.create(api, value)
        self.assertEqual(value['pending'], 'router_interface'); api.writes.clear()
        with self.assertRaisesRegex(RuntimeError, 'unconfirmed'): self.create(api, value)
        self.assertEqual(api.writes, [])

    def test_state_file_is_atomic_private_and_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'state.json'; step.write_state(p, {'ids': {'router': 'fixture'}})
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(p.read_text())['ids']['router'], 'fixture')
            link = Path(d)/'link'; link.symlink_to(p)
            with self.assertRaises(RuntimeError): step.write_state(link, {})

    def test_http_write_scope_rejects_deletion_other_resources_and_foreign_path(self):
        client = step.access.Client.__new__(step.access.Client)
        api = step.Neutron(client, 'https://192.168.0.151:9696', 'private-token')
        for method, path in [('DELETE', 'routers/fixture'), ('POST', 'floatingips'), ('PUT', 'routers/fixture'), ('PUT', 'routers/../../agents/add_router_interface')]:
            with self.assertRaises(RuntimeError): api.write(method, path, {}, 200)


if __name__ == '__main__': unittest.main()

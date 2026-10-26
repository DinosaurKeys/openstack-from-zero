import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('prepare_vm_test', ROOT/'scripts/phase4-prepare-vm.py')
step = importlib.util.module_from_spec(spec); spec.loader.exec_module(step)
spec = importlib.util.spec_from_file_location('view_test', ROOT/'scripts/lab-view.py')
view = importlib.util.module_from_spec(spec); spec.loader.exec_module(view)
PUBLIC = 'ssh-ed25519 AAAATESTPUBLICKEY'
META = {'size': 100, 'sha256': 'test256', 'sha512': 'test512'}


class API:
    def __init__(self):
        self.objects = {k: [] for k in ('image', 'flavor', 'key', 'group')}
        self.writes = []; self.failure = None
    def list(self, service, path, key):
        kind = {'images': 'image', 'flavors': 'flavor', 'keypairs': 'key', 'security_groups': 'group'}[key]
        values = copy.deepcopy(self.objects[kind])
        return [{'keypair': v} for v in values] if kind == 'key' else values
    def get(self, service, path):
        kind = 'image' if service == 'image' else 'key' if path.startswith('os-keypairs/') else 'group'
        obj = copy.deepcopy(next(o for o in self.objects[kind] if o.get('id', o['name']) == path.rsplit('/', 1)[1]))
        return obj if kind == 'image' else {('keypair' if kind == 'key' else 'security_group'): obj}
    def write(self, service, method, path, body, expected, upload=None):
        self.writes.append((service, method, path, copy.deepcopy(body), expected, upload))
        if self.failure == path: raise RuntimeError('Fixture uncertain outcome')
        if method == 'PUT':
            self.objects['image'][0].update(status='active', size=META['size'], os_hash_algo='sha512', os_hash_value=META['sha512'])
            return {}
        if path.endswith('security-group-rules'):
            rule = dict(body['security_group_rule'], id='rule'+str(len(self.writes)))
            self.objects['group'][0]['security_group_rules'].append(rule)
            return {'security_group_rule': copy.deepcopy(rule)}
        kind, wrapper = {'v2/images': ('image', None), 'flavors': ('flavor', 'flavor'), 'os-keypairs': ('key', 'keypair'),
                         'v2.0/security-groups': ('group', 'security_group')}[path]
        obj = copy.deepcopy(body[wrapper] if wrapper else body)
        if kind != 'key': obj['id'] = kind+'-id'
        if kind == 'image': obj.update(owner='project1', status='queued')
        if kind == 'group':
            obj['security_group_rules'] = [{'id': 'out'+eth, 'security_group_id': obj['id'], 'direction': 'egress',
                'ethertype': eth, 'protocol': None, 'port_range_min': None, 'port_range_max': None,
                'remote_ip_prefix': None, 'remote_group_id': None} for eth in ('IPv4', 'IPv6')]
        if kind == 'flavor': obj['swap'] = ''
        self.objects[kind].append(copy.deepcopy(obj))
        return {wrapper: obj} if wrapper else obj


class PreparationTests(unittest.TestCase):
    def run_prepare(self, api=None, state=None, save=None):
        api = api or API(); state = state if state is not None else {'ids': {}}
        result = step.prepare(api, state, 'project1', PUBLIC, Path('/fixture/image'), META, save or (lambda v: None), lambda line: None)
        return api, state, result
    def test_exact_creation_scope_and_successful_rerun_without_writes(self):
        api, state, result = self.run_prepare()
        self.assertEqual([w[2] for w in api.writes], ['v2/images', 'flavors', 'os-keypairs', 'v2.0/security-groups',
            'v2/images/image-id/file', 'v2.0/security-group-rules', 'v2.0/security-group-rules'])
        self.assertEqual(api.writes[2][3]['keypair'], {'name': 'lab-vm-key', 'public_key': PUBLIC})
        self.assertNotIn('private_key', json.dumps(api.writes, default=str))
        self.assertEqual(api.writes[0][3]['visibility'], 'private')
        self.assertEqual(api.writes[1][3]['flavor']['os-flavor-access:is_public'], True)
        self.assertEqual(result['guest_login'], 'ubuntu')
        count = len(api.writes); self.run_prepare(api, state); self.assertEqual(len(api.writes), count)
    def test_rules_restrict_sources_and_ports(self):
        api, _, _ = self.run_prepare()
        rules = [w[3]['security_group_rule'] for w in api.writes if w[2].endswith('security-group-rules')]
        self.assertEqual({r['remote_ip_prefix'] for r in rules}, {'192.168.0.0/24'})
        self.assertEqual({r['protocol'] for r in rules}, {'tcp', 'icmp'})
        self.assertEqual(rules[0]['port_range_min'], 22); self.assertEqual(rules[0]['port_range_max'], 22)
    def test_all_name_conflicts_block_every_write(self):
        for kind, wanted in step.specs('project1', PUBLIC).items():
            with self.subTest(kind=kind):
                api = API(); api.objects[kind] = [dict(wanted, id='foreign')]
                with self.assertRaisesRegex(RuntimeError, 'Untracked'): self.run_prepare(api)
                self.assertEqual(api.writes, [])
    def test_ambiguous_post_records_pending_and_prevents_second_attempt(self):
        api = API(); api.failure = 'flavors'; state = {'ids': {}}; snapshots = []
        with self.assertRaisesRegex(RuntimeError, 'uncertain'): self.run_prepare(api, state, lambda s: snapshots.append(copy.deepcopy(s)))
        self.assertEqual(state['ids'], {'image': 'image-id'}); self.assertEqual(state['pending'], 'flavor')
        self.assertEqual(snapshots[-1]['pending'], 'flavor')
        count = len(api.writes)
        with self.assertRaisesRegex(RuntimeError, 'unconfirmed'): self.run_prepare(api, state)
        self.assertEqual(len(api.writes), count)
    def test_ambiguous_upload_not_repeated(self):
        api = API(); api.failure = 'v2/images/image-id/file'; state = {'ids': {}}
        with self.assertRaises(RuntimeError): self.run_prepare(api, state)
        self.assertEqual(state['pending'], 'image_upload')
        count = len(api.writes)
        with self.assertRaisesRegex(RuntimeError, 'unconfirmed'): self.run_prepare(api, state)
        self.assertEqual(len(api.writes), count)
    def test_tracked_drift_not_overwritten(self):
        api, state, _ = self.run_prepare(); count = len(api.writes)
        api.objects['flavor'][0]['ram'] = 4096
        with self.assertRaisesRegex(RuntimeError, 'differs'): self.run_prepare(api, state)
        self.assertEqual(len(api.writes), count)
    def test_missing_tracked_resource_not_recreated(self):
        api, state, _ = self.run_prepare(); api.objects['image'] = []; count = len(api.writes)
        with self.assertRaisesRegex(RuntimeError, 'missing'): self.run_prepare(api, state)
        self.assertEqual(len(api.writes), count)
    def test_unexpected_ingress_is_rejected_without_removal(self):
        api, state, _ = self.run_prepare(); count = len(api.writes)
        api.objects['group'][0]['security_group_rules'].append({'direction': 'ingress', 'ethertype': 'IPv4', 'remote_ip_prefix': '0.0.0.0/0'})
        with self.assertRaisesRegex(RuntimeError, 'unexpected'): self.run_prepare(api, state)
        self.assertEqual(len(api.writes), count)
    def test_wrong_uploaded_hash_cannot_pass(self):
        api, state, _ = self.run_prepare(); api.objects['image'][0]['os_hash_value'] = 'wrong'
        with self.assertRaisesRegex(RuntimeError, 'hash/size'): self.run_prepare(api, state)
    def test_nonactive_image_cannot_pass(self):
        api, state, _ = self.run_prepare(); api.objects['image'][0]['status'] = 'killed'
        with self.assertRaisesRegex(RuntimeError, 'not reached active'): self.run_prepare(api, state)
    def test_default_plan_makes_no_connections_or_key_files(self):
        with patch.object(step, 'execute', side_effect=AssertionError('must not connect')), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(step.main([]), 0)
        self.assertIn('PLAN ONLY', out.getvalue())


class FileTests(unittest.TestCase):
    def qcow(self, size=1024**3): return b'QFI\xfb'+struct.pack('>I', 3)+bytes(16)+struct.pack('>Q', size)+b'fixture'
    def test_qcow_digest_and_virtual_size_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'image'; payload = self.qcow(); path.write_bytes(payload)
            with patch.dict(step.PLAN, sha256=hashlib.sha256(payload).hexdigest()):
                metadata = step.inspect_image(path); self.assertEqual(metadata['size'], len(payload))
                self.assertEqual(metadata['sha512'], hashlib.sha512(payload).hexdigest())
            with self.assertRaisesRegex(RuntimeError, 'SHA256'): step.inspect_image(path)
            path.write_bytes(self.qcow(11*1024**3))
            with self.assertRaisesRegex(RuntimeError, 'virtual disk'): step.inspect_image(path)
            path.write_bytes(b'<html>download error</html>')
            with self.assertRaisesRegex(RuntimeError, 'QCOW2'): step.inspect_image(path)
    def test_image_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'image'; path.symlink_to('/etc/passwd')
            with self.assertRaisesRegex(RuntimeError, 'regular file'): step.inspect_image(path)
    def test_real_guest_key_pair_checked_and_existing_node_key_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            key = Path(folder)/'guest'; sentinel = Path(folder)/'node'; sentinel.write_text('node key sentinel')
            subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
            state = {'ids': {}}
            with patch.object(step, 'KEY', key):
                public = step.guest_key(state, lambda s: None, lambda l: None)
                self.assertTrue(public.startswith('ssh-ed25519 ')); self.assertNotIn('PRIVATE', public)
                self.assertEqual(step.guest_key(state, lambda s: None, lambda l: None), public)
                Path(str(key)+'.pub').write_text('ssh-ed25519 WRONG')
                with self.assertRaisesRegex(RuntimeError, 'does not match'): step.guest_key(state, lambda s: None, lambda l: None)
            self.assertEqual(sentinel.read_text(), 'node key sentinel')


class TransportTests(unittest.TestCase):
    def api(self, opener):
        class Client: pass
        client = Client(); client.opener = opener
        return step.API(client, {'image': 'https://192.168.0.151:9292', 'compute': 'https://192.168.0.151:8774/v2.1'}, 'SECRET-TOKEN')
    def test_vm_delete_and_other_writes_blocked(self):
        api = self.api(None)
        for service, method, path in [('compute', 'POST', 'servers'), ('image', 'DELETE', 'v2/images/abc'), ('compute', 'POST', 'flavors/x/action')]:
            with self.assertRaisesRegex(RuntimeError, 'Unapproved'): api.write(service, method, path, {}, 200)
    def test_upload_stream_and_content_length(self):
        seen = []
        class Response:
            status = 204
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self, n): return b''
        class Opener:
            def open(self, request, timeout):
                seen.append((request.method, request.headers, request.data.read(), timeout)); return Response()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'image'; path.write_bytes(b'qcow data')
            self.api(Opener()).write('image', 'PUT', 'v2/images/abc/file', None, 204, upload=path)
        self.assertEqual(seen[0][0], 'PUT'); self.assertEqual(seen[0][1]['Content-length'], '9')
        self.assertEqual(seen[0][2], b'qcow data'); self.assertEqual(seen[0][3], 1800)
    def test_http_error_never_prints_token_or_response_body(self):
        class Opener:
            def open(self, *args, **kwargs):
                raise urllib.error.HTTPError('SECRET-TOKEN', 403, 'SECRET-TOKEN', {}, io.BytesIO(b'password=SECRET'))
        with self.assertRaises(RuntimeError) as caught: self.api(Opener()).write('compute', 'POST', 'flavors', {}, 200)
        self.assertNotIn('SECRET', str(caught.exception)); self.assertIn('403', str(caught.exception))
    def test_pagination_cannot_move_token_to_other_service(self):
        api = self.api(None)
        api.client.request = lambda url, token: ({'images': [], 'next': 'https://192.168.0.151:5000/v2/images'}, None)
        with self.assertRaisesRegex(RuntimeError, 'changed service'): api.list('image', 'v2/images', 'images')


class ViewTests(unittest.TestCase):
    def test_agent_view_filters_arbitrary_fields(self):
        class ReadOnly:
            def list(self, *args): return [{'id': 'a', 'host': 'node1', 'alive': True, 'admin_state_up': True, 'password': 'SECRET'}]
        result = view.collect(ReadOnly(), 'agents')
        self.assertNotIn('SECRET', json.dumps(result)); self.assertTrue(result['agents'][0]['alive'])
    def test_router_view_only_uses_reads_and_reports_actual_host(self):
        calls = []
        class ReadOnly:
            def list(self, service, path, key):
                calls.append(path)
                if key == 'routers': return [{'id': 'router1', 'name': 'lab-router', 'status': 'ACTIVE'}]
                return [{'id': 'p', 'device_id': 'router1', 'fixed_ips': [{'ip_address': '192.168.0.192'}], 'status': 'ACTIVE'}]
            def get(self, service, path):
                calls.append(path); return {'agents': [{'host': 'node1', 'alive': True, 'admin_state_up': True, 'secret': 'SECRET'}]}
        result = view.collect(ReadOnly(), 'router')
        self.assertEqual(result['hosting_agents'][0]['host'], 'node1')
        self.assertEqual(result['ports'][0]['fixed_ips'][0]['ip_address'], '192.168.0.192')
        self.assertNotIn('SECRET', json.dumps(result)); self.assertEqual(len(calls), 3)


if __name__ == '__main__': unittest.main()

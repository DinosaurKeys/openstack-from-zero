import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import ssl
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
s = importlib.util.spec_from_file_location('access_test', ROOT/'scripts/phase3-access.py')
step = importlib.util.module_from_spec(s); s.loader.exec_module(step)
PROFILE = {'auth': {'auth_url': 'https://192.168.0.151:5000', 'username': 'admin',
    'password': 'fixture-private-password', 'user_domain_name': 'Default',
    'project_name': 'admin', 'project_domain_name': 'Default'}, 'region_name': 'RegionOne'}
URLS = {'identity': 'https://192.168.0.151:5000', 'image': 'https://192.168.0.151:9292',
        'compute': 'https://192.168.0.151:8774/v2.1', 'network': 'https://192.168.0.151:9696',
        'block-storage': 'https://192.168.0.151:8776/v3', 'placement': 'https://192.168.0.151:8778'}


def catalog():
    return [{'type': k, 'endpoints': [{'interface': 'public', 'region_id': 'RegionOne', 'url': v}]} for k, v in URLS.items()]


class FakeClient:
    def __init__(self, fail=None): self.requests = []; self.fail = fail
    def request(self, url, token=None, body=None, expect=200):
        self.requests.append((url, token, body, expect))
        if self.fail == 'auth' and body: raise RuntimeError('API request returned HTTP 401')
        if body:
            return {'token': {'project': {'id': 'abcdef1234'}, 'catalog': catalog()}}, 'fixture-private-token'
        if url.endswith('/services'):
            return {'services': [{'id': k, 'type': k, 'enabled': k != self.fail} for k in URLS]}, None
        if url.endswith('/endpoints'):
            return {'endpoints': [{'service_id': k, 'interface': 'public', 'region_id': 'RegionOne', 'url': v, 'enabled': True} for k, v in URLS.items()]}, None
        for k, (path, key) in step.API_PATHS.items():
            if url == URLS[k]+'/'+path: return {key: []}, None
        raise AssertionError('Unexpected request: '+url)
    def horizon(self): return {'https_verified': True, 'login_form_present': True, 'browser_login': 'pending user confirmation'}


class AccessTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('openssl'), 'OpenSSL required for local TLS fixture')
    def test_real_tls_handshake_accepts_only_trusted_correct_vip_certificate(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); key = root/'key.pem'; cert = root/'cert.pem'
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                '-subj', '/CN=local-fixture', '-addext', 'subjectAltName=IP:192.168.0.151',
                '-keyout', str(key), '-out', str(cert)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); server_ctx.load_cert_chain(cert, key)
            client = step.Client(cert)
            ctx = next(h._context for h in client.opener.handlers if isinstance(h, step.urllib.request.HTTPSHandler))
            self.assertTrue(ctx.check_hostname); self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
            def handshake(client_ctx, hostname):
                ci, co, si, so = (ssl.MemoryBIO() for _ in range(4))
                peers = [client_ctx.wrap_bio(ci, co, server_hostname=hostname), server_ctx.wrap_bio(si, so, server_side=True)]
                done = set()
                for _ in range(100):
                    for index, peer in enumerate(peers):
                        try: peer.do_handshake(); done.add(index)
                        except ssl.SSLWantReadError: pass
                    for outgoing, incoming in ((co, si), (so, ci)):
                        data = outgoing.read()
                        if data: incoming.write(data)
                    if len(done) == 2: return
                self.fail('TLS handshake did not finish')
            handshake(ctx, step.VIP)
            with self.assertRaises(ssl.SSLCertVerificationError): handshake(ctx, '192.168.0.152')
            with self.assertRaises(ssl.SSLCertVerificationError): handshake(ssl.create_default_context(), step.VIP)

    def test_execution_prepares_local_files_and_keeps_credentials_out_of_report(self):
        logs = []; client = FakeClient()
        with patch.object(step.deploy, 'source_guard'), patch.object(step.deploy.validation, 'local_checks', return_value=(Path('/inventory'), Path('/config'))), patch.object(step, 'input_hashes', return_value={'original': 'unchanged'}), patch.object(step.deploy.validation, 'private_file'), patch.object(step, 'load_profile', return_value=dict(PROFILE, cacert='/ca.crt')), patch.object(step, 'Client', return_value=client), patch.object(step.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='fixture-private-password')) as run:
            self.assertEqual(step.execute(logs.append), 0)
            argv = run.call_args[0][0]
            self.assertEqual(argv[1], 'post-deploy'); self.assertEqual(argv[-2:], ['--limit', 'localhost'])
            self.assertEqual(run.call_args.kwargs['env']['ANSIBLE_NO_LOG'], 'True')
        text = '\n'.join(logs)
        self.assertNotIn('fixture-private-password', text); self.assertNotIn('fixture-private-token', text)
        self.assertIn('OPENSTACK_API_ACCESS_PASSED', text)

    def test_changed_deployment_inputs_block_api_requests(self):
        with patch.object(step.deploy, 'source_guard'), patch.object(step.deploy.validation, 'local_checks', return_value=(Path('/inventory'), Path('/config'))), patch.object(step, 'input_hashes', side_effect=[{'original': 'a'}, {'original': 'b'}]), patch.object(step.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='')), patch.object(step, 'Client') as client:
            with self.assertRaisesRegex(RuntimeError, 'deployment inputs changed'): step.execute(lambda line: None)
            client.assert_not_called()

    def test_plan_performs_no_operations(self):
        with patch.object(step, 'execute') as execute, patch.object(step.subprocess, 'run') as run, patch.object(step.tempfile, 'mkstemp') as write, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(step.main([]), 0)
            execute.assert_not_called(); run.assert_not_called(); write.assert_not_called()

    def test_only_approved_https_destinations_allowed(self):
        for url in ('http://192.168.0.151:5000', 'https://other.example:5000', 'https://user:password@192.168.0.151', 'https://192.168.0.151:8443', 'https://192.168.0.151/#fragment'):
            with self.assertRaises(RuntimeError): step.safe_url(url)
        for url in URLS.values(): self.assertEqual(step.safe_url(url), url)

    def test_postdeploy_is_local_only_and_not_a_deployment(self):
        args = list(map(str, step.post_command(Path('/inventory'), Path('/config'))))
        self.assertEqual(args[1], 'post-deploy'); self.assertEqual(args[-2:], ['--limit', 'localhost'])
        for forbidden in ('--become', 'deploy', 'reconfigure', 'destroy', 'bootstrap-servers'): self.assertNotIn(forbidden, args)

    def test_catalog_requires_each_public_regional_endpoint(self):
        self.assertEqual(step.catalog_urls(catalog(), 'RegionOne'), URLS)
        for bad in (catalog()[:-1], catalog()+[catalog()[0]]):
            with self.assertRaises(RuntimeError): step.catalog_urls(bad, 'RegionOne')
        bad = catalog(); bad[0]['endpoints'][0]['url'] = 'http://192.168.0.151:5000'
        with self.assertRaises(RuntimeError): step.catalog_urls(bad, 'RegionOne')

    def test_service_api_reads_are_authenticated_and_do_not_create_resources(self):
        c = FakeClient(); result = step.check_apis(PROFILE, c)
        self.assertEqual(set(result['service_api_reads']), set(step.API_PATHS))
        self.assertEqual(sum(body is not None for _, _, body, _ in c.requests), 1)
        for url, token, body, status in c.requests:
            if body is None: self.assertEqual(token, 'fixture-private-token')
            else:
                self.assertTrue(url.endswith('/v3/auth/tokens')); self.assertEqual(status, 201)
                self.assertEqual(body, step.auth_body(PROFILE))
        text = json.dumps(result)
        self.assertNotIn('fixture-private-token', text); self.assertNotIn(PROFILE['auth']['password'], text)

    def test_authentication_and_disabled_services_cannot_pass(self):
        for fail in ('auth', 'compute', 'block-storage'):
            with self.assertRaises(RuntimeError): step.check_apis(PROFILE, FakeClient(fail))

    def test_project_substitution_and_invalid_project_rejection(self):
        self.assertEqual(step.api_url(URLS['block-storage']+'/%(project_id)s', 'volumes', 'abc123'), URLS['block-storage']+'/abc123/volumes')
        with self.assertRaises(RuntimeError): step.api_url(URLS['block-storage'], 'volumes', '../other')

    def test_http_errors_do_not_expose_response_secrets(self):
        c = step.Client.__new__(step.Client)
        with patch.object(c, 'opener', create=True) as opener:
            opener.open.side_effect = urllib.error.HTTPError(URLS['identity'], 401, 'fixture-private-token', {}, io.BytesIO(b'fixture-private-password'))
            with self.assertRaisesRegex(RuntimeError, '^API request returned HTTP 401$'): c.request(URLS['identity'])

    def test_tls_errors_remain_failures_without_disabling_verification(self):
        c = step.Client.__new__(step.Client)
        with patch.object(c, 'opener', create=True) as opener:
            opener.open.side_effect = urllib.error.URLError(ssl.SSLCertVerificationError('fixture'))
            with self.assertRaisesRegex(RuntimeError, 'certificate validation'): c.request(URLS['identity'])

    def test_horizon_is_a_page_check_not_a_login(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): return b'<input name="username"><input name="csrfmiddlewaretoken">'
        c = step.Client.__new__(step.Client)
        with patch.object(c, 'opener', create=True) as opener:
            opener.open.return_value = Response()
            result = c.horizon()
            self.assertEqual(result['browser_login'], 'pending user confirmation')
            self.assertEqual(opener.open.call_args[0][0].get_method(), 'GET')

    def test_horizon_will_not_follow_a_redirect_outside_vip(self):
        c = step.Client.__new__(step.Client)
        with patch.object(c, 'opener', create=True) as opener:
            opener.open.side_effect = urllib.error.HTTPError(URLS['identity'], 302, '', {'Location': 'https://other.example/'}, None)
            with self.assertRaises(RuntimeError): c.horizon()
            self.assertEqual(opener.open.call_count, 1)

    def test_client_uses_ca_verification_and_suppresses_proxy_inheritance(self):
        with patch.object(step.ssl, 'create_default_context') as ctx, patch.object(step.urllib.request, 'build_opener') as build:
            step.Client('/approved/ca.crt'); ctx.assert_called_once_with(cafile='/approved/ca.crt')
            self.assertEqual(build.call_args[0][0].proxies, {})


if __name__ == '__main__': unittest.main()

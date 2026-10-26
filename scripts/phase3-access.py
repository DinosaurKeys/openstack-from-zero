#!/usr/bin/env python3
"""Chapter D: prepare local client credentials and check live HTTPS/API access."""
import sys
sys.dont_write_bytecode = True
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('access_deploy', REPO/'scripts/phase3-deploy.py')
deploy = importlib.util.module_from_spec(spec); spec.loader.exec_module(deploy)
ENV = deploy.ENV
VIP = '192.168.0.151'
OUTPUTS = {'clouds.yaml', 'admin-openrc.sh', 'admin-openrc-system.sh',
           'public-openrc.sh', 'public-openrc-system.sh'}
API_PATHS = {'image': ('v2/images?limit=1', 'images'),
             'compute': ('servers/detail?limit=1', 'servers'),
             'network': ('v2.0/networks?limit=1', 'networks'),
             'block-storage': ('volumes/detail?limit=1', 'volumes'),
             'placement': ('resource_providers', 'resource_providers')}
SERVICE_TYPES = {'identity', *API_PATHS}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def safe_url(url, host=VIP):
    p = urllib.parse.urlsplit(url)
    if p.scheme != 'https' or p.hostname != host or p.username or p.password or p.fragment:
        raise RuntimeError('Endpoint must use HTTPS on the approved API VIP')
    if p.port not in (None, 443, 5000, 9292, 8774, 9696, 8776, 8778):
        raise RuntimeError('Unexpected service endpoint port')
    return url


def auth_body(profile):
    a = profile['auth']
    return {'auth': {'identity': {'methods': ['password'], 'password': {'user': {
        'name': a['username'], 'domain': {'name': a['user_domain_name']}, 'password': a['password']}}},
        'scope': {'project': {'name': a['project_name'], 'domain': {'name': a['project_domain_name']}}}}}


def api_url(base, path, project_id):
    if not re_project(project_id): raise RuntimeError('Invalid project identifier in token response')
    base = expand_endpoint(base, project_id)
    return safe_url(base.rstrip('/')+'/'+path)


def expand_endpoint(url, project_id):
    return safe_url(url.replace('%(project_id)s', project_id).replace('$(project_id)s', project_id))


def re_project(value):
    import re
    return isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9-]+', value) is not None


class Client:
    def __init__(self, ca):
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(ca))), NoRedirect())

    def request(self, url, token=None, body=None, expect=200):
        safe_url(url)
        headers = {'Accept': 'application/json'}
        if token: headers['X-Auth-Token'] = token
        if body is not None: headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                     headers=headers, method='POST' if body is not None else 'GET')
        try:
            with self.opener.open(req, timeout=20) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000: raise RuntimeError('API response exceeded the collection limit')
                if response.status != expect: raise RuntimeError('Unexpected HTTP response '+str(response.status))
                return json.loads(raw), response.headers.get('X-Subject-Token')
        except urllib.error.HTTPError as error:
            raise RuntimeError('API request returned HTTP '+str(error.code)) from None
        except (urllib.error.URLError, ssl.SSLError, TimeoutError, OSError):
            raise RuntimeError('HTTPS connection, certificate validation or timeout needs review') from None
        except (ValueError, TypeError):
            raise RuntimeError('API response was not valid JSON') from None

    def horizon(self):
        url = 'https://'+VIP+'/'
        for _ in range(4):
            try:
                req = urllib.request.Request(safe_url(url), headers={'Accept': 'text/html'})
                with self.opener.open(req, timeout=20) as response:
                    page = response.read(1_000_001).decode('utf-8', errors='replace')
                    if response.status != 200 or len(page) > 1_000_000 or 'csrfmiddlewaretoken' not in page or 'name="username"' not in page:
                        raise RuntimeError('Horizon response did not contain the expected login form')
                    return {'url': url, 'https_verified': True, 'login_form_present': True,
                            'browser_login': 'pending user confirmation'}
            except urllib.error.HTTPError as error:
                if error.code not in (301, 302, 303, 307, 308):
                    raise RuntimeError('Horizon returned HTTP '+str(error.code)) from None
                url = urllib.parse.urljoin(url, error.headers.get('Location', ''))
                safe_url(url)
                if urllib.parse.urlsplit(url).port not in (None, 443):
                    raise RuntimeError('Horizon redirect changed the expected HTTPS port')
            except (urllib.error.URLError, ssl.SSLError, TimeoutError, OSError):
                raise RuntimeError('Horizon HTTPS connection or certificate validation needs review') from None
        raise RuntimeError('Too many Horizon redirects')


def catalog_urls(catalog, region):
    urls = {}
    for service in catalog:
        kind = service.get('type')
        if kind not in SERVICE_TYPES: continue
        endpoints = [e for e in service.get('endpoints', []) if e.get('interface') == 'public'
                     and e.get('region_id', e.get('region')) == region]
        if kind in urls or len(endpoints) != 1:
            raise RuntimeError('Expected one public regional endpoint for '+kind)
        urls[kind] = safe_url(endpoints[0]['url'])
    if set(urls) != SERVICE_TYPES: raise RuntimeError('Required service types missing from token catalog')
    return urls


def check_apis(profile, client):
    def request(label, url, *args, **kwargs):
        try: return client.request(url, *args, **kwargs)
        except RuntimeError as error: raise RuntimeError(label+': '+str(error)) from None
    auth = profile['auth']; auth_url = safe_url(auth['auth_url']).rstrip('/')
    identity = auth_url if auth_url.endswith('/v3') else auth_url+'/v3'
    document, token = request('Keystone authentication', identity+'/auth/tokens', body=auth_body(profile), expect=201)
    data = document.get('token', {})
    if not token or not re_project(data.get('project', {}).get('id')):
        raise RuntimeError('Expected a project-scoped authentication token')
    project = data['project']['id']; urls = {k: expand_endpoint(v, project) for k, v in catalog_urls(data.get('catalog', []), profile['region_name']).items()}
    result = {'authentication': 'passed (token value withheld)', 'public_endpoints': urls}
    services, _ = request('Keystone service registration', identity+'/services', token)
    enabled = {s['type'] for s in services.get('services', []) if s.get('enabled') is True}
    if not SERVICE_TYPES.issubset(enabled): raise RuntimeError('Required service registration missing or disabled')
    endpoints, _ = request('Keystone endpoint registration', identity+'/endpoints', token)
    entries = endpoints.get('endpoints', [])
    by_id = {s['id']: s['type'] for s in services['services'] if s.get('enabled') is True}
    for kind in SERVICE_TYPES:
        selected = [e for e in entries if by_id.get(e.get('service_id')) == kind
                    and e.get('interface') == 'public' and e.get('region_id', e.get('region')) == profile['region_name']]
        if len(selected) != 1 or selected[0].get('enabled') is not True:
            raise RuntimeError('Public endpoint registration needs review: '+kind)
        if expand_endpoint(selected[0]['url'], project) != urls[kind]:
            raise RuntimeError('Registered endpoint differs from token catalog: '+kind)
    result['service_registration'] = sorted(SERVICE_TYPES)
    checks = {}
    for kind, (path, key) in API_PATHS.items():
        doc, _ = request(kind+' API', api_url(urls[kind], path, project), token)
        if not isinstance(doc.get(key), list): raise RuntimeError('Unexpected '+kind+' API response')
        checks[kind] = 'authenticated HTTPS list request passed'
    result['service_api_reads'] = checks
    # The bearer token remains only in memory and is never printed or persisted.
    return result


def input_hashes(config, inventory):
    return {p: h for p, h in deploy.pre.private_inputs(config, inventory).items()
            if Path(p).parent != config or Path(p).name not in OUTPUTS}


def post_command(inventory, config):
    return [ENV/'bin/kolla-ansible', 'post-deploy', '--configdir', config, '-i', inventory,
            '--limit', 'localhost']


def load_profile(config):
    import yaml
    path = config/'clouds.yaml'; deploy.validation.private_file(path)
    profile = yaml.safe_load(path.read_text())['clouds']['kolla-admin']
    safe_url(profile['auth']['auth_url'])
    expected_ca = config/'certificates/ca/lab-root.crt'
    if Path(profile.get('cacert', '')) != expected_ca or not profile.get('region_name'):
        raise RuntimeError('Client profile CA or region requires review')
    return profile


def execute(log):
    deploy.source_guard(); env = deploy.tools.tool_environment()
    inventory, config = deploy.validation.local_checks(lambda line: None, env, deploy.validation.CA_FINGERPRINT)
    before = input_hashes(config, inventory)
    for name in OUTPUTS:
        path = config/name
        if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_uid != os.getuid())):
            raise RuntimeError('Unsafe existing client output path: '+name)
    log('Preparing private client files on R2d2 with standard Kolla post-deploy (localhost only).')
    # Post-deploy only renders local credential files. Suppress all Ansible task data.
    child = dict(env, ANSIBLE_NO_LOG='True', ANSIBLE_BECOME_ASK_PASS='False')
    p = subprocess.run(list(map(str, post_command(inventory, config))), env=child,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=180)
    if input_hashes(config, inventory) != before:
        raise RuntimeError('Existing private deployment inputs changed; review required')
    if p.returncode: raise RuntimeError('Local Kolla post-deploy returned exit '+str(p.returncode)+'; credential output withheld')
    for name in OUTPUTS: deploy.validation.private_file(config/name)
    profile = load_profile(config); client = Client(profile['cacert'])
    result = {'api_checks': None, 'horizon': None, 'issues': []}
    try: result['api_checks'] = check_apis(profile, client)
    except RuntimeError as error: result['issues'].append('API checks: '+str(error))
    try: result['horizon'] = client.horizon()
    except RuntimeError as error: result['issues'].append('Horizon page: '+str(error))
    log(json.dumps(result, indent=2))
    log('RESULT: '+('OPENSTACK_API_ACCESS_PASSED' if not result['issues'] else 'OPENSTACK_API_ACCESS_NEEDS_REVIEW'))
    log('Browser sign-in remains pending. No VM, volume, network or failover test was performed.')
    return 0 if not result['issues'] else 1


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group(); group.add_argument('--run', action='store_true'); group.add_argument('--show-login', action='store_true')
    args = p.parse_args(argv)
    if not (args.run or args.show_login):
        print('PLAN ONLY: generate local private client files, verify HTTPS/API authentication, registrations and Horizon login page.')
        print('No service changes or cloud resources created. Browser sign-in is a separate manual step.'); return 0
    deploy.validation.config_step.load_step('access_r2d2', 'phase2-check.py').require_r2d2()
    deploy.tools.check_paths(ENV)
    if not sys.stdin.isatty(): raise RuntimeError('Use an interactive R2d2 terminal')
    if Path(sys.prefix) != ENV:
        flag = '--run' if args.run else '--show-login'
        os.execve(str(ENV/'bin/python'), [str(ENV/'bin/python'), str(Path(__file__).resolve()), flag], deploy.tools.tool_environment())
    config = REPO/'private/phase2/kolla'
    if args.show_login:
        a = load_profile(config)['auth']
        print('For your browser only. Do not paste these credentials into chat or GitHub.')
        print('URL: https://'+VIP+'/\nDomain: '+a['user_domain_name']+'\nUsername: '+a['username']+'\nPassword: '+a['password'])
        return 0
    os.umask(0o077); reports = REPO/'reports'; deploy.inv.private_dir(reports)
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-')
    fd, name = tempfile.mkstemp(prefix='phase3-access-'+stamp, suffix='.txt', dir=reports)
    print('Private report: '+name, flush=True); status = 1
    with os.fdopen(fd, 'w') as output:
        def log(line):
            print(line, flush=True); output.write(line+'\n'); output.flush()
        log('Chapter D: local client preparation and live HTTPS/API checks.')
        try: status = execute(log)
        except (Exception, KeyboardInterrupt) as error:
            log('RESULT: OPENSTACK_API_ACCESS_NEEDS_REVIEW')
            log(str(error) if isinstance(error, RuntimeError) else 'Check could not complete ('+type(error).__name__+'); private details withheld.')
    print('Read report: cat '+shlex.quote(name)); return status


if __name__ == '__main__':
    try: sys.exit(main())
    except (RuntimeError, OSError) as error:
        print('Stopped: '+str(error), file=sys.stderr); sys.exit(1)

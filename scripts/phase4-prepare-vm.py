#!/usr/bin/env python3
"""Chapter C: prepare image, flavor, guest key and security group; never boot a VM."""
import sys
sys.dont_write_bytecode = True
import argparse
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import ssl
import struct
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('vm_networks', REPO/'scripts/phase4-create-networks.py')
net = importlib.util.module_from_spec(spec); spec.loader.exec_module(net)
access = net.access; deploy = access.deploy; ENV = access.ENV
PLAN = {'version': 1, 'image_name': 'lab-ubuntu-24.04-20261017',
        'image_url': 'https://cloud-images.ubuntu.com/releases/noble/release-20261017/ubuntu-24.04-server-cloudimg-amd64.img',
        'sha256': '04e8f2461a16a44e78a370284471f29616cdd9baf1893df25807b71f985b6c94',
        'flavor_name': 'lab.small', 'vcpus': 1, 'ram_mb': 2048, 'disk_gb': 10,
        'key_name': 'lab-vm-key', 'security_group': 'lab-vm-access', 'source_cidr': '192.168.0.0/24'}
KEY = Path.home()/'.ssh/openstack-from-zero-vm'
MAX_IMAGE = 2 * 1024**3


class API:
    """Restricted preparation writes; bearer token stays in memory."""
    def __init__(self, client, urls, token):
        self.client, self.urls, self.token = client, urls, token
    def url(self, service, path):
        return access.safe_url(self.urls[service].rstrip('/')+'/'+path)
    def get(self, service, path):
        return self.client.request(self.url(service, path), self.token)[0]
    def list(self, service, path, key):
        url = self.url(service, path); result = []; seen = set()
        while url:
            if url in seen or len(seen) >= 100: raise RuntimeError('API pagination needs review')
            seen.add(url); page, _ = self.client.request(url, self.token)
            if not isinstance(page.get(key), list): raise RuntimeError('Unexpected list response: '+key)
            result.extend(page[key])
            links = [l['href'] for l in page.get(key+'_links', []) if l.get('rel') == 'next']
            if page.get('next'): links.append(page['next'])
            if len(links) > 1: raise RuntimeError('Ambiguous API pagination')
            following = access.safe_url(urllib.parse.urljoin(url, links[0])) if links else None
            if following and urllib.parse.urlsplit(following)[:3] != urllib.parse.urlsplit(self.url(service, path))[:3]:
                raise RuntimeError('Pagination changed service or resource')
            url = following
        return result
    def write(self, service, method, path, body, expected, upload=None):
        allowed = method == 'POST' and (service, path) in {
            ('image', 'v2/images'), ('compute', 'flavors'), ('compute', 'os-keypairs'),
            ('network', 'v2.0/security-groups'), ('network', 'v2.0/security-group-rules')}
        allowed |= service == 'image' and method == 'PUT' and re.fullmatch(r'v2/images/[a-zA-Z0-9-]+/file', path) is not None
        if not allowed: raise RuntimeError('Unapproved VM preparation write')
        headers = {'X-Auth-Token': self.token, 'Accept': 'application/json', 'Content-Type': 'application/json'}
        handle = None
        try:
            if upload is not None:
                if not (service == 'image' and method == 'PUT'): raise RuntimeError('Unapproved file upload')
                handle = open(upload, 'rb'); data = handle
                headers.update({'Content-Type': 'application/octet-stream', 'Content-Length': str(os.fstat(handle.fileno()).st_size)})
            else: data = json.dumps(body).encode()
            request = urllib.request.Request(self.url(service, path), data=data, method=method, headers=headers)
            with self.client.opener.open(request, timeout=1800 if handle else 45) as response:
                if response.status != expected: raise RuntimeError('Unexpected preparation HTTP status '+str(response.status))
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000: raise RuntimeError('Preparation response too large')
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            raise RuntimeError(service+' '+method+' '+path+': HTTP '+str(error.code)+'; no automatic retry') from None
        except (urllib.error.URLError, OSError, ValueError):
            raise RuntimeError(service+' '+method+' '+path+': outcome uncertain; return report, do not retry') from None
        finally:
            if handle: handle.close()


def authenticate(config):
    profile = access.load_profile(config); client = access.Client(profile['cacert'])
    identity = profile['auth']['auth_url'].rstrip('/')
    if not identity.endswith('/v3'): identity += '/v3'
    document, token = client.request(identity+'/auth/tokens', body=access.auth_body(profile), expect=201)
    project = document['token']['project']['id']; user = document['token']['user']['id']
    if not token or not net.valid_id(project) or not net.valid_id(user): raise RuntimeError('Scoped authentication needs review')
    urls = {k: access.expand_endpoint(v, project) for k, v in access.catalog_urls(document['token']['catalog'], profile['region_name']).items()}
    return API(client, urls, token), project, user


def inspect_image(path):
    if path.is_symlink() or not path.is_file(): raise RuntimeError('Image cache must be a regular file')
    h256 = hashlib.sha256(); h512 = hashlib.sha512(); size = 0
    with path.open('rb') as image:
        head = image.read(32)
        if len(head) != 32 or head[:4] != b'QFI\xfb' or struct.unpack('>I', head[4:8])[0] not in (2, 3):
            raise RuntimeError('Downloaded file is not the expected QCOW2 format')
        virtual_size = struct.unpack('>Q', head[24:32])[0]
        if not 0 < virtual_size <= PLAN['disk_gb'] * 1024**3: raise RuntimeError('QCOW2 virtual disk exceeds the planned flavor')
        image.seek(0)
        while True:
            block = image.read(1024**2)
            if not block: break
            size += len(block)
            if size > MAX_IMAGE: raise RuntimeError('Image exceeds the download limit')
            h256.update(block); h512.update(block)
    if h256.hexdigest() != PLAN['sha256']: raise RuntimeError('Ubuntu image SHA256 does not match the pinned release')
    return {'size': size, 'sha256': h256.hexdigest(), 'sha512': h512.hexdigest(), 'virtual_size': virtual_size}


def download(folder, log):
    path = folder/'ubuntu-24.04-20261017.img'
    if path.exists() or path.is_symlink(): return path, inspect_image(path)
    if shutil.disk_usage(folder).free < 3 * 1024**3: raise RuntimeError('Need at least 3 GiB free on R2d2 for the image cache')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()), access.NoRedirect())
    fd, temp = tempfile.mkstemp(prefix='ubuntu-download-', dir=folder)
    try:
        log('Downloading the pinned Ubuntu cloud image to R2d2; this can take several minutes.')
        with os.fdopen(fd, 'wb') as out, opener.open(PLAN['image_url'], timeout=120) as response:
            if response.status != 200: raise RuntimeError('Ubuntu download HTTP status needs review')
            total = 0; last = time.monotonic()
            while True:
                block = response.read(1024**2)
                if not block: break
                total += len(block)
                if total > MAX_IMAGE: raise RuntimeError('Ubuntu download exceeded 2 GiB')
                out.write(block)
                if time.monotonic()-last >= 20:
                    log('Downloaded '+str(total//1024**2)+' MiB.'); last = time.monotonic()
        metadata = inspect_image(Path(temp)); os.replace(temp, path)
        return path, metadata
    finally:
        if os.path.exists(temp): os.unlink(temp)


def guest_key(state, save, log):
    public = Path(str(KEY)+'.pub')
    if KEY.is_symlink() or public.is_symlink(): raise RuntimeError('Guest key paths must not be symlinks')
    if not KEY.exists() and not public.exists():
        if state.get('public_key'): raise RuntimeError('Tracked guest key is missing; no regeneration')
        deploy.inv.private_dir(KEY.parent)
        log('Creating a separate guest SSH key. Choose its passphrase in the terminal; typing is hidden.')
        subprocess.run(['ssh-keygen', '-t', 'ed25519', '-f', str(KEY), '-C', 'joe OpenStack lab guests'], check=True)
    if not KEY.is_file() or not public.is_file(): raise RuntimeError('Incomplete guest key pair; return report')
    deploy.validation.private_file(KEY)
    # ssh-keygen asks locally for an existing passphrase if needed; no private material is logged.
    result = subprocess.run(['ssh-keygen', '-y', '-f', str(KEY)], stdout=subprocess.PIPE, text=True, check=True)
    derived = result.stdout.strip().split(); saved = public.read_text().strip().split()
    if len(saved) < 2 or len(derived) < 2 or saved[:2] != derived[:2] or saved[0] != 'ssh-ed25519':
        raise RuntimeError('Guest private/public key pair does not match')
    value = ' '.join(saved[:2])
    if state.get('public_key') not in (None, value): raise RuntimeError('Tracked guest public key changed')
    state['public_key'] = value; save(state)
    return value


def rules(group):
    common = {'security_group_id': group, 'direction': 'ingress', 'ethertype': 'IPv4', 'remote_ip_prefix': PLAN['source_cidr']}
    return [dict(common, protocol='tcp', port_range_min=22, port_range_max=22), dict(common, protocol='icmp')]


def canonical_rule(rule):
    return tuple(rule.get(k) for k in ('direction', 'ethertype', 'protocol', 'port_range_min', 'port_range_max',
                                     'remote_ip_prefix', 'remote_group_id', 'remote_address_group_id'))


def check_rules(group, complete):
    expected = {canonical_rule(r) for r in rules(group['id'])}
    defaults = {('egress', eth, None, None, None, None, None, None) for eth in ('IPv4', 'IPv6')}
    actual = [canonical_rule(r) for r in group.get('security_group_rules', [])]
    if len(set(actual)) != len(actual) or not set(actual).issubset(expected | defaults):
        raise RuntimeError('Security group contains unexpected or duplicate rules; no rules removed')
    if not defaults.issubset(actual): raise RuntimeError('Default unrestricted egress rules differ; review before continuing')
    if complete and not expected.issubset(actual): raise RuntimeError('Required guest ingress rules are missing')


def specs(project, public):
    return {
        'image': {'name': PLAN['image_name'], 'visibility': 'private', 'disk_format': 'qcow2', 'container_format': 'bare',
                  'min_disk': PLAN['disk_gb'], 'min_ram': PLAN['ram_mb'], 'architecture': 'x86_64', 'os_distro': 'ubuntu', 'os_version': '24.04'},
        'flavor': {'name': PLAN['flavor_name'], 'vcpus': PLAN['vcpus'], 'ram': PLAN['ram_mb'], 'disk': PLAN['disk_gb'], 'OS-FLV-EXT-DATA:ephemeral': 0, 'swap': 0, 'os-flavor-access:is_public': True},
        'key': {'name': PLAN['key_name'], 'public_key': public},
        'group': {'name': PLAN['security_group'], 'description': 'Lab guests: SSH and ping from home LAN only', 'project_id': project}}


def inventories(api):
    return {'image': api.list('image', 'v2/images?limit=100', 'images'),
            'flavor': api.list('compute', 'flavors/detail?limit=100', 'flavors'),
            'key': [k['keypair'] for k in api.list('compute', 'os-keypairs', 'keypairs')],
            'group': api.list('network', 'v2.0/security-groups', 'security_groups')}


def validate_object(kind, obj, wanted, project):
    # Nova may represent an unset swap as the empty string on flavor reads.
    actual = dict(obj)
    if kind == 'flavor' and actual.get('swap') == '': actual['swap'] = 0
    if not net.subset(wanted, actual): raise RuntimeError('Tracked '+kind+' differs from the plan')
    if kind == 'image' and obj.get('owner') != project: raise RuntimeError('Image belongs to another project')
    if kind == 'group': check_rules(obj, False)


def preflight(api, state, project, public):
    objects = inventories(api); expected = specs(project, public)
    if set(state['ids']) - set(expected): raise RuntimeError('Unexpected resource kind in preparation state')
    for kind, wanted in expected.items():
        selected = [o for o in objects[kind] if o.get('name') == wanted['name']]
        identifier = state['ids'].get(kind)
        if len(selected) > 1 or (selected and (selected[0].get('name') if kind == 'key' else selected[0].get('id')) != identifier):
            raise RuntimeError('Untracked or duplicate '+kind+' name; no adoption or replacement')
        if identifier:
            if len(selected) != 1: raise RuntimeError('Tracked '+kind+' is missing or renamed')
            obj = api.get('compute', 'os-keypairs/'+identifier)['keypair'] if kind == 'key' else selected[0]
            validate_object(kind, obj, wanted, project)
    return objects


def change(api, state, save, label, service, method, path, body, status, upload=None):
    state['pending'] = label; save(state)
    result = api.write(service, method, path, body, status, upload=upload)
    # Caller must persist the returned ID before clearing this marker.
    return result


def prepare(api, state, project, public, image_path, metadata, save, log):
    if state.get('pending'): raise RuntimeError('Previous request outcome is unconfirmed; return report before retrying')
    preflight(api, state, project, public); expected = specs(project, public)
    locations = {'image': ('image', 'v2/images', None, 201), 'flavor': ('compute', 'flavors', 'flavor', 200),
                 'key': ('compute', 'os-keypairs', 'keypair', 200), 'group': ('network', 'v2.0/security-groups', 'security_group', 201)}
    for kind in ('image', 'flavor', 'key', 'group'):
        if kind in state['ids']: continue
        service, path, wrapper, status = locations[kind]
        wanted = expected[kind]; body = {wrapper: wanted} if wrapper else wanted
        log('Creating '+kind+' ('+wanted['name']+').')
        response = change(api, state, save, kind, service, 'POST', path, body, status)
        obj = response[wrapper] if wrapper else response
        identifier = obj.get('name') if kind == 'key' else obj.get('id')
        if not net.valid_id(identifier) and kind != 'flavor': raise RuntimeError('Create response missing identifier: '+kind)
        if kind == 'flavor' and (not isinstance(identifier, str) or not re.fullmatch(r'[a-zA-Z0-9_.-]+', identifier)):
            raise RuntimeError('Flavor response missing identifier')
        state['ids'][kind] = identifier; state.pop('pending', None); save(state)
        validate_object(kind, obj, wanted, project)
    image_id = state['ids']['image']; image = api.get('image', 'v2/images/'+image_id)
    if image.get('status') == 'queued':
        log('Uploading verified image bytes to Glance on node1; waiting for the upload response.')
        change(api, state, save, 'image_upload', 'image', 'PUT', 'v2/images/'+image_id+'/file', None, 204, upload=image_path)
        state.pop('pending', None); save(state)
    deadline = time.monotonic()+180
    while True:
        image = api.get('image', 'v2/images/'+image_id)
        if image.get('status') == 'active': break
        if image.get('status') not in ('queued', 'saving', 'uploading', 'importing') or time.monotonic() >= deadline:
            raise RuntimeError('Glance image has not reached active; return report')
        time.sleep(5)
    algo = image.get('os_hash_algo'); wanted_hash = metadata.get(algo) if algo in ('sha256', 'sha512') else None
    if not wanted_hash or image.get('os_hash_value') != wanted_hash or image.get('size') != metadata['size']:
        raise RuntimeError('Glance image hash/size differs from the verified download')
    group_id = state['ids']['group']; group = api.get('network', 'v2.0/security-groups/'+group_id)['security_group']
    check_rules(group, False)
    for index, rule in enumerate(rules(group_id)):
        if canonical_rule(rule) in {canonical_rule(r) for r in group['security_group_rules']}: continue
        response = change(api, state, save, 'ingress_rule_'+str(index), 'network', 'POST', 'v2.0/security-group-rules', {'security_group_rule': rule}, 201)
        created = response.get('security_group_rule', {})
        if not net.valid_id(created.get('id')) or canonical_rule(created) != canonical_rule(rule) or created.get('security_group_id') != group_id:
            raise RuntimeError('Created security rule differs from the plan')
        state.pop('pending', None); save(state)
        group = api.get('network', 'v2.0/security-groups/'+group_id)['security_group']
    check_rules(group, True); preflight(api, state, project, public)
    return {'ids': state['ids'], 'image': {k: image.get(k) for k in ('name', 'status', 'size', 'os_hash_algo', 'os_hash_value')},
            'flavor': {'vcpus': PLAN['vcpus'], 'ram_mb': PLAN['ram_mb'], 'root_disk_gb': PLAN['disk_gb']},
            'ssh_private_key': str(KEY), 'guest_login': 'ubuntu', 'ingress_source': PLAN['source_cidr'],
            'scope': 'Preparation objects only; no VM, floating IP or volume created. Guest access and boot remain untested.'}


def execute(log):
    deploy.source_guard(); env = deploy.tools.tool_environment()
    inventory, config = deploy.validation.local_checks(lambda line: None, env)
    baseline = access.input_hashes(config, inventory)
    folder = REPO/'private/phase4'; deploy.inv.private_dir(folder)
    lock = folder/'vm-preparation.lock'
    if lock.is_symlink(): raise RuntimeError('Lock must not be a symlink')
    with open(lock, 'a') as handle:
        os.chmod(lock, 0o600)
        try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError('Another VM preparation command is running') from None
        path = folder/'vm-preparation.json'
        if path.is_symlink(): raise RuntimeError('State must not be a symlink')
        if path.exists(): deploy.validation.private_file(path); state = json.loads(path.read_text())
        else: state = {'plan': PLAN, 'ids': {}}
        if state.get('plan') != PLAN or state.get('pending'): raise RuntimeError('Preparation state differs or has an uncertain request; return report')
        save = lambda value: net.write_state(path, value)
        api, project, user = authenticate(config)
        binding = {'project_id': project, 'user_id': user, 'apis': api.urls}
        if any(k in state and state[k] != v for k, v in binding.items()): raise RuntimeError('Preparation identity or API changed')
        state.update(binding); save(state)
        network_state = folder/'networks.json'; deploy.validation.private_file(network_state)
        network_data = json.loads(network_state.read_text())
        if network_data.get('project_id') != project or network_data.get('plan') != net.PLAN or network_data.get('pending'):
            raise RuntimeError('Chapter B network state requires review')
        if set(network_data.get('ids', {})) != set(net.KINDS): raise RuntimeError('Complete Chapter B first')
        net.preflight(net.Neutron(api.client, api.urls['network'], api.token), network_data['ids'], project)
        # Check name conflicts before downloading or creating a key.
        preflight(api, state, project, state.get('public_key', ''))
        image_path, metadata = download(folder, log)
        public = guest_key(state, save, log)
        # Refresh the token after the download and interactive key prompt.
        api, new_project, new_user = authenticate(config)
        if new_project != project or new_user != user or api.urls != state['apis']: raise RuntimeError('Authentication context changed')
        summary = prepare(api, state, project, public, image_path, metadata, save, log)
        if baseline != access.input_hashes(config, inventory): raise RuntimeError('Deployment inputs changed; review required')
        log('VM preparation summary:\n'+json.dumps(summary, indent=2))
        log('RESULT: VM_PREPARATION_VERIFIED')
        log('Return this report. Boot, SSH, DHCP/DNS, VM traffic, volumes and failover remain later tests.')
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--apply', action='store_true'); args = p.parse_args(argv)
    if not args.apply:
        print('PLAN ONLY — no connections or files changed:\n'+json.dumps(PLAN, indent=2))
        print('Creates a private Ubuntu image, public flavor, separate local guest key, Nova public-key registration and limited security group. No VM. Read Chapter C before --apply.'); return 0
    deploy.validation.config_step.load_step('prepare_vm_r2d2', 'phase2-check.py').require_r2d2(); deploy.tools.check_paths(ENV)
    if not sys.stdin.isatty(): raise RuntimeError('Use an interactive R2d2 terminal')
    if Path(sys.prefix) != ENV:
        os.execve(str(ENV/'bin/python'), [str(ENV/'bin/python'), str(Path(__file__).resolve()), '--apply'], deploy.tools.tool_environment())
    os.umask(0o077); reports = REPO/'reports'; deploy.inv.private_dir(reports)
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-')
    fd, name = tempfile.mkstemp(prefix='phase4-prepare-vm-'+stamp, suffix='.txt', dir=reports)
    print('Private report: '+name, flush=True); status = 1
    with os.fdopen(fd, 'w') as output:
        def log(line): print(line, flush=True); output.write(line+'\n'); output.flush()
        log('Phase 4 Chapter C — prepare guest resources only.\nPlan:\n'+json.dumps(PLAN, indent=2))
        try: status = execute(log)
        except (Exception, KeyboardInterrupt) as error:
            log('RESULT: VM_PREPARATION_NEEDS_REVIEW')
            log(str(error) if isinstance(error, RuntimeError) else 'Stopped ('+type(error).__name__+'); private details withheld.')
            log('Earlier steps may have created local files or resources. No automatic deletion, rollback or retry. Return this report.')
    print('Read report: cat '+shlex.quote(name)); return status


if __name__ == '__main__':
    try: sys.exit(main())
    except (RuntimeError, OSError) as error: print('Stopped: '+str(error), file=sys.stderr); sys.exit(1)

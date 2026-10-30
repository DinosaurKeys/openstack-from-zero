#!/usr/bin/env python3
"""Chapter C: write local Terraform settings from Chapter A evidence; no connections."""
import argparse
import json
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import sys

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
LABELS = {
    'R2d2 tools platform and free disk', 'Cluster services and agents',
    'Existing Ubuntu image', 'Approved size meets image minimum',
    'Existing guest SSH key', 'Existing networks and free addresses',
    'Address space for two VMs and one floating IP', 'New resource names available',
    'Old lab-vm-01 safely off', 'Flavor options at assessed RAM',
    'Compute and network quotas', 'Cinder space and quotas',
    'Physical and Placement capacity', 'Existing inputs/checkpoints preserved',
}
FILES = ('versions.tf', 'provider.tf', 'variables.tf', 'existing.tf', 'main.tf',
         'outputs.tf', 'lab.tfvars.example', '.terraform.lock.hcl')
UUID = r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'


class ConfigError(RuntimeError):
    pass


def identity():
    if os.geteuid() == 0 or pwd.getpwuid(os.getuid()).pw_name != 'joe' or socket.gethostname().split('.')[0] != 'r2d2':
        raise ConfigError('Run as joe on R2d2, without sudo')


def safe_path(path, directory=False):
    try:
        relative = path.relative_to(REPO)
    except ValueError:
        raise ConfigError('Input/output must stay inside this repository') from None
    current = REPO
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ConfigError('A local input/output path is symlinked; nothing replaced')
    info = path.stat()
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(info.st_mode) or info.st_uid != os.getuid():
        raise ConfigError('Local input/output ownership or type needs review')
    return info


def private_read(path):
    info = safe_path(path)
    if stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > 4 * 1024**2:
        raise ConfigError('Private input permissions or size need review')
    return path.read_bytes()


def report_path(requested):
    folder = REPO / 'reports'
    safe_path(folder, directory=True)
    if requested:
        path = Path(requested)
        if not path.is_absolute():
            path = REPO / path
    else:
        candidates = sorted(folder.glob('phase6-check-*.json'))
        if not candidates:
            raise ConfigError('Run Chapter A first; no readiness report found')
        path = candidates[-1]
    if path.parent != folder or not re.fullmatch(r'phase6-check-\d{8}T\d{6}Z-[a-zA-Z0-9_-]+\.json', path.name):
        raise ConfigError('Select a Chapter A JSON report inside reports/')
    safe_path(path)
    return path


def resource_id(value, keystone=False):
    pattern = r'(?:[0-9a-fA-F]{32}|' + UUID + ')' if keystone else UUID
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ConfigError('Saved resource identity is missing or malformed')
    return value


def settings(report, prepared, networks, old):
    summary = report.get('summary', [])
    if not isinstance(summary, list) or len(summary) != len(LABELS) or {r.get('check') for r in summary} != LABELS:
        raise ConfigError('Readiness collection is incomplete; use its full JSON report')
    allowed_reviews = {'Approved size meets image minimum', 'Old lab-vm-01 safely off'}
    if any(r.get('status') != 'PASS' and not (r.get('status') == 'REVIEW' and r['check'] in allowed_reviews) for r in summary):
        raise ConfigError('Chapter A has other failed/unavailable checks; review them first')
    project = resource_id(report.get('project_id'), keystone=True)
    user = resource_id(prepared.get('user_id'), keystone=True)
    if any(s.get('project_id') != project or s.get('pending') or s.get('restore_required') for s in (prepared, networks, old)):
        raise ConfigError('Existing project or exercise records need review; preserved')
    apis = prepared.get('apis')
    if not isinstance(apis, dict) or old.get('apis') != apis or old.get('user_id') != user or networks.get('api') != apis.get('network'):
        raise ConfigError('Existing cloud/user binding differs')
    image = report['Existing Ubuntu image']
    if (image.get('id') != prepared.get('ids', {}).get('image') or
            any(type(image.get(k)) is not int for k in ('min_ram', 'min_disk')) or
            not 0 <= image['min_ram'] <= 2048 or not 0 <= image['min_disk'] <= 10 or
            report.get('assessed_ram_mb_each') != 2048):
        raise ConfigError('The report does not fit the approved 2-GiB/10-GiB VMs')
    if not isinstance(image.get('name'), str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', image['name']):
        raise ConfigError('Saved image name needs review')
    net_ids = networks.get('ids', {})
    if report['Existing networks and free addresses'].get('ids') != net_ids:
        raise ConfigError('Saved network references changed after Chapter A')
    if report['Old lab-vm-01 safely off'].get('id') != old.get('ids', {}).get('server'):
        raise ConfigError('The retained VM identity differs')
    flavors = report['Flavor options at assessed RAM']
    flavor = prepared.get('ids', {}).get('flavor')
    if flavors.get('ram_mb') != 2048 or not isinstance(flavors.get('ids'), list) or flavor not in flavors['ids']:
        raise ConfigError('The existing tracked flavor is not a verified 1-vCPU/2-GiB/10-GiB match')
    key = report['Existing guest SSH key'].get('name')
    if key != 'lab-vm-key':
        raise ConfigError('Guest SSH keypair name differs from the course key')
    volume_type = report['Cinder space and quotas'].get('type_name')
    if not isinstance(volume_type, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', volume_type):
        raise ConfigError('The verified Cinder volume type is missing or unrecognized')
    values = {
        'project_id': project, 'user_id': user,
        'image_id': resource_id(image['id']), 'image_name': image['name'],
        'flavor_id': resource_id(flavor), 'keypair_name': key,
        **{kind + '_id': resource_id(net_ids.get(kind)) for kind in
           ('private_network', 'private_subnet', 'external_network', 'external_subnet')},
        'volume_type_name': volume_type,
    }
    return {'existing': values, 'home_lan_cidr': '192.168.0.0/24'}


def private_dir(path):
    if not path.exists() and not path.is_symlink():
        path.mkdir(mode=0o700)
    info = safe_path(path, directory=True)
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ConfigError('Private directory permissions need review')


def prepare(requested=None, check=False):
    source = report_path(requested)
    paths = [source, *[REPO / 'private/phase4' / name for name in
                      ('vm-preparation.json', 'networks.json', 'vm.json')]]
    originals = {p: private_read(p) for p in paths}
    documents = [json.loads(originals[p]) for p in paths]
    variables = settings(*documents)
    public = REPO / 'terraform/phase6'
    for name in FILES:
        safe_path(public / name)
    folder = REPO / 'private/phase6'
    desired = {
        'lab.tfvars.json': json.dumps(variables, indent=2) + '\n',
        'backend.tfbackend': 'path = ' + json.dumps(str(folder / 'terraform.tfstate')) + '\n',
    }
    # Never replace a previous course configuration or touch its Terraform state.
    for name, content in desired.items():
        path = folder / name
        if path.exists() or path.is_symlink():
            if private_read(path) != content.encode():
                raise ConfigError('Existing Phase 6 settings differ; nothing overwritten')
    if any(private_read(p) != raw for p, raw in originals.items()):
        raise ConfigError('Saved inputs changed during preparation; no files written')
    print('LOCAL FILES ONLY — Chapter C; no SSH, cloud request or Terraform execution.')
    print('Readiness snapshot: ' + source.name)
    print('VM     | vCPU | RAM   | OS disk | New data disk')
    print('lab1   | 1    | 2 GiB | 10 GiB  | none')
    print('lab2   | 1    | 2 GiB | 10 GiB  | 5 GiB')
    print('Flavor: verified existing 1-vCPU/2-GiB/10-GiB flavor (' + variables['existing']['flavor_id'] + ')')
    if check:
        complete = all((folder / name).exists() for name in desired)
        print('RESULT: ' + ('TERRAFORM_CONFIGURATION_READY' if complete else 'TERRAFORM_CONFIGURATION_NOT_PREPARED'))
        return 0 if complete else 1
    private_dir(REPO / 'private')
    private_dir(folder)
    for name, content in desired.items():
        path = folder / name
        if path.exists():
            print('KEEP   private/phase6/' + name + ' — matches')
            continue
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as out:
            out.write(content)
        print('WRITE  private/phase6/' + name)
    print('PASS   Existing report/checkpoints retained; no state file created or changed.')
    print('RESULT: TERRAFORM_CONFIGURATION_READY')
    print('NEXT: Chapter D will check fresh readiness and preview the cloud changes.')
    print('Before provisioning: lab-vm-01 must be SHUTOFF; this command does not stop it.')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--prepare', action='store_true', help='Write private settings only; do not run Terraform')
    modes.add_argument('--check', action='store_true', help='Compare the local settings without writing')
    parser.add_argument('--report', help='Chapter A JSON file; default: newest phase6-check report')
    args = parser.parse_args(argv)
    if not (args.prepare or args.check):
        print('PLAN ONLY: --prepare writes private Terraform variables/backend settings from the newest Chapter A report.')
        print('No installation, provider download, SSH, cloud call, VM shutdown or VM creation.')
        return 0
    identity()
    return prepare(args.report, check=args.check)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt) as error:
        print('RESULT: TERRAFORM_CONFIGURATION_NEEDS_REVIEW', file=sys.stderr)
        print(str(error) if isinstance(error, ConfigError) else 'Local preparation incomplete; inputs retained. Return this result for review.', file=sys.stderr)
        raise SystemExit(1)

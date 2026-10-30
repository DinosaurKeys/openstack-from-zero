#!/usr/bin/env python3
"""Install the pinned Terraform CLI on R2d2 only; never contact OpenStack."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import shlex
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

VERSION = '1.17.1'
REPO = Path(__file__).resolve().parents[1]
TARGET = Path('/usr/local/bin/terraform')
ORIGIN = 'https://releases.hashicorp.com/terraform/' + VERSION + '/'
ARCHES = {'x86_64': ('amd64', 62), 'aarch64': ('arm64', 183)}
MAX_ZIP = 256 * 1024**2
MAX_BINARY = 512 * 1024**2


class InstallError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def identity():
    if os.geteuid() == 0 or pwd.getpwuid(os.getuid()).pw_name != 'joe' or socket.gethostname().split('.')[0] != 'r2d2':
        raise InstallError('Run as joe on R2d2; do not run this script with sudo')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if release.get('ID', '').strip('"') != 'ubuntu' or release.get('VERSION_ID', '').strip('"') != '24.04':
        raise InstallError('This chapter targets Ubuntu 24.04 on R2d2')
    machine = platform.machine()
    if machine not in ARCHES:
        raise InstallError('R2d2 CPU architecture is not supported by this installer')
    return ARCHES[machine][0]


def version_info(path):
    env = dict(os.environ, CHECKPOINT_DISABLE='1')
    try:
        p = subprocess.run([str(path), 'version', '-json'], capture_output=True, text=True, timeout=20, env=env)
        if p.returncode:
            raise InstallError('Existing/staged Terraform version query failed')
        value = json.loads(p.stdout)
        if not isinstance(value, dict) or not isinstance(value.get('terraform_version'), str) or not isinstance(value.get('platform'), str):
            raise ValueError()
        return {'terraform_version': value['terraform_version'], 'platform': value['platform']}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise InstallError('Terraform version could not be verified; no automatic replacement') from None


def installation_directory():
    if TARGET.parent.is_symlink() or not TARGET.parent.is_dir():
        raise InstallError('Expected an ordinary /usr/local/bin directory')
    parent = TARGET.parent.stat()
    if parent.st_uid != 0 or stat.S_IMODE(parent.st_mode) & 0o022:
        raise InstallError('/usr/local/bin ownership/permissions need review')


def plan():
    arch = identity()
    installation_directory()
    if TARGET.is_symlink():
        raise InstallError('Terraform installation target is a symlink; nothing replaced')
    expected = {'terraform_version': VERSION, 'platform': 'linux_' + arch}
    if TARGET.exists():
        if not TARGET.is_file() or version_info(TARGET) != expected:
            raise InstallError('A different/unverified Terraform already occupies /usr/local/bin/terraform; nothing replaced')
        return {'arch': arch, 'ready': True, 'binary': str(TARGET), 'version': VERSION}
    existing = shutil.which('terraform')
    if existing:
        value = version_info(Path(existing))
        if value == expected:
            return {'arch': arch, 'ready': True, 'binary': existing, 'version': VERSION}
        raise InstallError('Another Terraform version is already on PATH; return its version before replacing or installing another copy')
    if min(shutil.disk_usage(tempfile.gettempdir()).free, shutil.disk_usage(TARGET.parent).free) < 1024**3:
        raise InstallError('Need 1 GiB free for the download/staging review margin')
    return {'arch': arch, 'ready': False, 'binary': str(TARGET), 'version': VERSION}


def fetch(url, path, maximum, log):
    if not url.startswith(ORIGIN) or '/' in url[len(ORIGIN):]:
        raise InstallError('Unapproved Terraform download URL')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()), NoRedirect())
    total = 0
    try:
        with opener.open(url, timeout=45) as response, path.open('xb') as out:
            if response.status != 200:
                raise InstallError('Terraform download did not return HTTP 200')
            while True:
                chunk = response.read(1024**2)
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum:
                    raise InstallError('Terraform download exceeded its size limit')
                out.write(chunk)
        if not total:
            raise InstallError('Terraform download was empty')
    except urllib.error.HTTPError as error:
        raise InstallError('HashiCorp download returned HTTP ' + str(error.code) + '; nothing installed') from None
    except (urllib.error.URLError, OSError):
        raise InstallError('HashiCorp HTTPS download unavailable; nothing installed') from None


def unpack_verified(archive, manifest, arch, destination):
    wanted = archive.name
    matches = [line.split()[0] for line in manifest.read_text().splitlines()
               if len(line.split()) == 2 and line.split()[1] == wanted]
    if len(matches) != 1 or len(matches[0]) != 64 or any(c not in '0123456789abcdef' for c in matches[0]):
        raise InstallError('Official checksum entry is missing or ambiguous')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != matches[0]:
        raise InstallError('Terraform archive SHA256 mismatch; nothing installed')
    with zipfile.ZipFile(archive) as bundle:
        entries = [v for v in bundle.infolist() if v.filename == 'terraform']
        if len(entries) != 1 or entries[0].is_dir() or not 64 <= entries[0].file_size <= MAX_BINARY:
            raise InstallError('Expected one bounded Terraform executable in the archive')
        # Read only this exact member; never extract archive paths into the filesystem.
        raw = bundle.read(entries[0])
    machine = {'amd64': 62, 'arm64': 183}[arch]
    if raw[:6] != b'\x7fELF\x02\x01' or int.from_bytes(raw[18:20], 'little') != machine:
        raise InstallError('Terraform executable architecture differs from R2d2')
    with destination.open('xb') as out:
        out.write(raw)
    destination.chmod(0o700)
    return hashlib.sha256(raw).hexdigest()


def install(value, log):
    if value['ready']:
        log('PASS  Terraform ' + VERSION + ' / linux_' + value['arch'] + ' already verified at ' + value['binary'])
        return value['binary']
    with tempfile.TemporaryDirectory(prefix='terraform-' + VERSION + '-') as folder:
        work = Path(folder)
        archive = work / ('terraform_' + VERSION + '_linux_' + value['arch'] + '.zip')
        manifest = work / ('terraform_' + VERSION + '_SHA256SUMS')
        log('DOWNLOAD  Terraform ' + VERSION + ' from HashiCorp over verified HTTPS…')
        fetch(ORIGIN + archive.name, archive, MAX_ZIP, log)
        fetch(ORIGIN + manifest.name, manifest, 1024**2, log)
        staged = work / 'terraform'
        binary_hash = unpack_verified(archive, manifest, value['arch'], staged)
        if version_info(staged) != {'terraform_version': VERSION, 'platform': 'linux_' + value['arch']}:
            raise InstallError('Staged Terraform version/platform differs; nothing installed')
        log('PASS  Official archive SHA256, Linux architecture and staged version')
        if TARGET.exists() or TARGET.is_symlink():
            raise InstallError('Installation target appeared during staging; nothing replaced')
        log('INSTALL  ' + str(TARGET) + '; sudo password is for R2d2 only')
        p = subprocess.run(['sudo', '--', '/usr/bin/install', '-m', '0755', '--', str(staged), str(TARGET)])
        if p.returncode:
            raise InstallError('Local install command failed; inspect the target before another install')
        if TARGET.is_symlink() or not TARGET.is_file() or hashlib.sha256(TARGET.read_bytes()).hexdigest() != binary_hash:
            raise InstallError('Installed binary differs; return this report before another action')
        if version_info(TARGET) != {'terraform_version': VERSION, 'platform': 'linux_' + value['arch']}:
            raise InstallError('Installed Terraform version/platform could not be verified')
    log('PASS  Installed binary SHA256 and Terraform ' + VERSION + ' / linux_' + value['arch'])
    return str(TARGET)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--check', action='store_true')
    group.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    if not args.apply:
        value = plan()
        print('CHECK ONLY — Terraform ' + VERSION + ' on R2d2; no download or installation.')
        print(json.dumps(value, indent=2))
        print('RESULT: ' + ('TERRAFORM_READY' if value['ready'] else 'TERRAFORM_INSTALL_PLAN_READY'))
        return 0
    if not sys.stdin.isatty():
        raise InstallError('Use joe\'s interactive R2d2 terminal')
    identity()
    os.umask(0o077)
    reports = REPO / 'reports'
    if reports.is_symlink():
        raise InstallError('Report directory must not be a symlink')
    reports.mkdir(mode=0o700, exist_ok=True)
    if reports.stat().st_uid != os.getuid():
        raise InstallError('Report directory must belong to joe')
    fd, name = tempfile.mkstemp(prefix='terraform-install-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), suffix='.txt', dir=reports)
    print('Private report: ' + name, flush=True)
    status = 1
    with os.fdopen(fd, 'w') as out:
        def log(line):
            print(line, flush=True)
            out.write(line + '\n')
            out.flush()
        log('Chapter B — install Terraform CLI on R2d2 only')
        try:
            value = plan()
            log('Version: ' + VERSION + '; platform: linux_' + value['arch'])
            binary = install(value, log)
            log('Binary: ' + binary)
            log('RESULT: TERRAFORM_READY')
            status = 0
        except (Exception, KeyboardInterrupt) as error:
            log('RESULT: TERRAFORM_INSTALL_NEEDS_REVIEW')
            log(str(error) if isinstance(error, InstallError) else type(error).__name__ + '; return report before another action')
        log('No node SSH, OpenStack request, VM operation or Terraform state/configuration change was performed.')
    print('Return report: cat ' + shlex.quote(name))
    return status


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (InstallError, OSError) as error:
        print('Stopped: ' + (str(error) if isinstance(error, InstallError) else type(error).__name__ + '; local check unavailable'), file=sys.stderr)
        raise SystemExit(1)

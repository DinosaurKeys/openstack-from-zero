#!/usr/bin/env python3
"""Prepare the agreed lab configuration on R2d2. Default: show plan only."""
import sys
sys.dont_write_bytecode = True
import argparse
import datetime as dt
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import tempfile

REPO = Path(__file__).resolve().parents[1]
ENV = Path('/home/joe/venvs/kolla')
VIP = '192.168.0.151'
USB = {'node1': 'enx00e04c557e58', 'node2': 'enx00e04c2c1678', 'node3': 'enx00e04c304688'}
MARKER = '.openstack-from-zero-config.json'
MARK = {'project': 'openstack-from-zero', 'chapter': 'E', 'release': '2026.2', 'vip': VIP}


def load_step(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


inventory_step = load_step('lab_inventory', 'phase2-inventory.py')
tools = inventory_step.tools


def settings(config):
    return {
        'kolla_base_distro': 'ubuntu', 'openstack_release': '2026.2',
        'kolla_internal_vip_address': VIP, 'kolla_external_vip_address': VIP,
        'network_interface': 'enp0s31f6',
        'neutron_plugin_agent': 'openvswitch',
        'enable_cinder': True, 'enable_cinder_backend_lvm': True,
        'cinder_volume_group': 'cinder-volumes',
        'cinder_cluster_name': '', 'cinder_cluster_skip_precheck': True,
        'enable_cinder_backup': False, 'enable_heat': False,
        'enable_horizon': True, 'enable_prometheus': False, 'enable_grafana': False,
        'kolla_enable_tls_internal': True, 'kolla_enable_tls_external': True,
        'kolla_enable_tls_backend': False,
        'kolla_copy_ca_into_containers': True,
        'openstack_cacert': '/etc/ssl/certs/ca-certificates.crt',
        'kolla_certificates_dir': str(config/'certificates'),
        'kolla_internal_fqdn_cert': str(config/'certificates/haproxy-internal.pem'),
        'kolla_external_fqdn_cert': str(config/'certificates/haproxy.pem'),
        'kolla_admin_openrc_cacert': str(config/'certificates/ca/lab-root.crt'),
    }


def network_plan():
    return {'subnet': '192.168.0.0/24', 'api_vip': VIP,
            'future_vm_external_pool': {'start': '192.168.0.152', 'end': '192.168.0.199'},
            'dhcp_pool': {'start': '192.168.0.2', 'end': '192.168.0.150'},
            'status': 'Joe confirmed free; reserved plan only; no cloud network created'}


def yaml_text(value):
    import yaml
    return '# Managed by openstack-from-zero Chapter E\n' + yaml.safe_dump(value, sort_keys=False)


def inspect_destination(folder, files):
    # Unknown existing configuration is never adopted or replaced.
    if folder.is_symlink():
        raise RuntimeError('Configuration folder must not be a symlink')
    if folder.exists():
        marker = folder/MARKER
        if marker.is_symlink() or not marker.is_file() or json.loads(marker.read_text()) != MARK:
            raise RuntimeError('Existing configuration is not this Chapter E setup; preserved')
        allowed = {MARKER, 'globals.yml', 'passwords.yml', 'certificates', 'lab-network-plan.json'}
        if set(p.name for p in folder.iterdir()) - allowed:
            raise RuntimeError('Additional configuration files require review; preserved')
        for name in ('passwords.yml', 'certificates'):
            path = folder/name
            if path.is_symlink():
                raise RuntimeError('Refuse symlinked private configuration')
    for path, content in files.items():
        if path.is_symlink() or (path.exists() and path.read_text() != content):
            raise RuntimeError('Existing file has different contents; preserved: ' + str(path))
    hv = folder.parent/'host_vars'
    if hv.exists() and (hv.is_symlink() or set(p.name for p in hv.iterdir()) - {n+'.yml' for n in USB}):
        raise RuntimeError('Unexpected inventory host variables require review')


def fill_passwords(config, env, log):
    import yaml
    template = ENV/'share/kolla-ansible/etc_examples/kolla/passwords.yml'
    expected = yaml.safe_load(template.read_text())
    path = config/'passwords.yml'
    if not path.exists():
        inventory_step.preserve_write(path, template.read_text())
    values = yaml.safe_load(path.read_text())
    if not isinstance(values, dict) or set(values) != set(expected):
        raise RuntimeError('Password file keys differ from the installed template; contents withheld')
    missing = any(value is None for key, value in values.items() if key != 'docker_registry_password')
    if missing:
        result = subprocess.run([str(ENV/'bin/kolla-genpwd'), '-p', str(path)], env=env,
                                text=True, capture_output=True)
        path.chmod(0o600)
        if result.returncode:
            raise RuntimeError('kolla-genpwd failed; credential contents and command output withheld')
        values = yaml.safe_load(path.read_text())
    for name, value in values.items():
        if name == 'docker_registry_password':
            continue
        if not value or (isinstance(value, dict) and (not value.get('private_key') or not value.get('public_key'))):
            raise RuntimeError('Required credentials are incomplete; contents withheld')
    path.chmod(0o600)
    log('PASS service credentials populated privately; existing values retained; no values logged.')


def make_certificates(destination):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    now = dt.datetime.now(dt.timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'OpenStack home lab CA')])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now-dt.timedelta(minutes=5)).not_valid_after(now+dt.timedelta(days=3650))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
               key_encipherment=False, data_encipherment=False, key_agreement=False,
               key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, VIP)]))
            .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-dt.timedelta(minutes=5)).not_valid_after(now+dt.timedelta(days=365))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(VIP))]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                 key_encipherment=True, data_encipherment=False, key_agreement=False,
                 key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .sign(ca_key, hashes.SHA256()))
    pem = serialization.Encoding.PEM
    key_format = serialization.PrivateFormat.PKCS8
    password = serialization.NoEncryption()
    (destination/'ca').mkdir(mode=0o700)
    (destination/'private').mkdir(mode=0o700)
    (destination/'ca/lab-root.crt').write_bytes(ca.public_bytes(pem))
    (destination/'private/lab-root.key').write_bytes(ca_key.private_bytes(pem, key_format, password))
    (destination/'private/server.key').write_bytes(key.private_bytes(pem, key_format, password))
    (destination/'server.crt').write_bytes(cert.public_bytes(pem))
    combined = cert.public_bytes(pem) + key.private_bytes(pem, key_format, password)
    for name in ('haproxy.pem', 'haproxy-internal.pem'):
        (destination/name).write_bytes(combined)
    for path in destination.rglob('*'):
        if path.is_file():
            path.chmod(0o600)


def verify_certificates(folder):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    required = ('ca/lab-root.crt', 'private/lab-root.key', 'private/server.key',
                'server.crt', 'haproxy.pem', 'haproxy-internal.pem')
    for name in ('ca', 'private'):
        if (folder/name).is_symlink():
            raise RuntimeError('Certificate directories must not be symlinks')
    for name in required:
        path = folder/name
        if path.is_symlink() or not path.is_file():
            raise RuntimeError('Certificate set is incomplete or symlinked; no automatic replacement')
    ca = x509.load_pem_x509_certificate((folder/'ca/lab-root.crt').read_bytes())
    cert = x509.load_pem_x509_certificate((folder/'server.crt').read_bytes())
    ca_key = serialization.load_pem_private_key((folder/'private/lab-root.key').read_bytes(), None)
    key = serialization.load_pem_private_key((folder/'private/server.key').read_bytes(), None)
    if ca_key.public_key().public_numbers() != ca.public_key().public_numbers() or key.public_key().public_numbers() != cert.public_key().public_numbers():
        raise RuntimeError('Certificate/key mismatch; private contents withheld')
    ca.public_key().verify(cert.signature, cert.tbs_certificate_bytes, padding.PKCS1v15(), cert.signature_hash_algorithm)
    now = dt.datetime.now(dt.timezone.utc)
    if not (cert.not_valid_before_utc <= now < cert.not_valid_after_utc and ca.not_valid_before_utc <= now < ca.not_valid_after_utc):
        raise RuntimeError('Certificate is outside its validity dates; no automatic rotation')
    if cert.issuer != ca.subject or not ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
        raise RuntimeError('Certificate authority relationship is invalid')
    if ipaddress.ip_address(VIP) not in cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress):
        raise RuntimeError('API VIP is missing from certificate SAN')
    combined = (folder/'server.crt').read_bytes() + (folder/'private/server.key').read_bytes()
    if any((folder/name).read_bytes() != combined for name in ('haproxy.pem', 'haproxy-internal.pem')):
        raise RuntimeError('HAProxy PEM differs from verified server certificate/key')
    return ca.fingerprint(hashes.SHA256()).hex()


PROXYSQL_FILES = {'proxysql-ca.pem': 'ca/lab-root.crt',
                 'proxysql-cert.pem': 'server.crt',
                 'proxysql-key.pem': 'private/server.key'}


def verify_proxysql_certificates(folder):
    for target, source in PROXYSQL_FILES.items():
        path = folder/target
        if path.is_symlink() or not path.is_file():
            raise RuntimeError('ProxySQL TLS files are incomplete; complete Chapter B certificate preparation first')
        if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
            raise RuntimeError('ProxySQL TLS file ownership/permissions require review; contents withheld')
        if path.read_bytes() != (folder/source).read_bytes():
            raise RuntimeError('ProxySQL TLS files differ from the verified lab certificate set; preserved')


def ensure_proxysql_certificates(folder):
    # Same internal/external/database VIP: mirror the pinned release's own
    # certificates role, without issuing or rotating any certificate/key.
    verify_certificates(folder)
    for parent in [folder, *folder.parents]:
        if parent.is_symlink():
            raise RuntimeError('Refuse symlinked certificate path')
    values = {folder/target: (folder/source).read_bytes()
              for target, source in PROXYSQL_FILES.items()}
    # Inspect every destination before creating any; never replace a conflict.
    for path, content in values.items():
        if path.is_symlink() or (path.exists() and
                (not path.is_file() or path.stat().st_uid != os.getuid() or path.read_bytes() != content)):
            raise RuntimeError('Existing ProxySQL TLS destination differs; preserved: '+path.name)
    for path, content in values.items():
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as output:
                output.write(content)
        path.chmod(0o600)
    verify_proxysql_certificates(folder)


def prepare(log):
    env = tools.tool_environment()
    config = REPO/'private/phase2/kolla'
    inventory = config.parent/'inventory.ini'
    inventory_step.validate_inventory(inventory_step.inventory_data(env, inventory, log))
    files = {config/'globals.yml': yaml_text(settings(config)),
             config/'lab-network-plan.json': json.dumps(network_plan(), indent=2)+'\n'}
    for node, nic in USB.items():
        files[config.parent/'host_vars'/f'{node}.yml'] = yaml_text({'neutron_external_interface': nic})
    inspect_destination(config, files)
    inventory_step.private_dir(config)
    inventory_step.preserve_write(config/MARKER, json.dumps(MARK, sort_keys=True)+'\n')
    for path, content in files.items():
        inventory_step.private_dir(path.parent)
        inventory_step.preserve_write(path, content)
        log('Prepared: '+str(path))
    fill_passwords(config, env, log)
    certs = config/'certificates'
    if not certs.exists():
        staging = Path(tempfile.mkdtemp(prefix='.tls-preparing-', dir=config.parent))
        make_certificates(staging)
        verify_certificates(staging)
        os.rename(staging, certs)
    fingerprint = verify_certificates(certs)
    ensure_proxysql_certificates(certs)
    log('PASS certificate chain, keys and VIP SAN verified. CA SHA256: '+fingerprint)
    log('PASS ProxySQL CA/certificate/key files prepared privately from the same verified set.')
    parsed = inventory_step.inventory_data(env, inventory, log)
    inventory_step.validate_inventory(parsed)
    for node, nic in USB.items():
        if parsed['_meta']['hostvars'][node].get('neutron_external_interface') != nic:
            raise RuntimeError('External interface host variable differs for '+node)
    log('PASS all three roles and per-node external interface variables parsed by Ansible.')
    log('RESULT: LAB_CONFIG_PREPARED')
    log('Local files only. No IP addresses assigned, nodes contacted or services deployed.')
    log('HTTPS files prepared; browser trust and live HTTPS checks are still pending.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='create the explained private configuration and credentials')
    args = parser.parse_args(argv)
    load_step('r2d2_check', 'phase2-check.py').require_r2d2()
    tools.check_paths(ENV)
    if not args.apply:
        print(json.dumps(settings(REPO/'private/phase2/kolla'), indent=2))
        print('PLAN ONLY: no files created. Read Chapter E before --apply.')
        return 0
    if Path(sys.prefix) != ENV:
        executable = str(ENV/'bin/python')
        os.execve(executable, [executable, str(Path(__file__).resolve()), '--apply'], tools.tool_environment())
    import importlib.metadata
    if importlib.metadata.version('kolla-ansible') != '23.0.0' or importlib.metadata.version('ansible-core') != '2.21.5':
        raise RuntimeError('Expected installed Kolla-Ansible 23.0.0')
    os.umask(0o077)
    reports = REPO/'reports'
    inventory_step.private_dir(reports)
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-')
    fd, filename = tempfile.mkstemp(prefix='phase2-config-'+stamp, suffix='.txt', dir=reports)
    with os.fdopen(fd, 'w') as output:
        def log(line):
            print(line, flush=True); output.write(line+'\n'); output.flush()
        log('Phase 2 — Chapter E: private configuration, credentials and HTTPS certificate preparation')
        try:
            prepare(log); status = 0
        except Exception as error:
            # Secret-processing libraries can include input values in exceptions.
            log('RESULT: LAB_CONFIG_NEEDS_REVIEW')
            if isinstance(error, RuntimeError):
                log('Stopped: '+str(error))
            else:
                log('Stopped ('+type(error).__name__+'); library details withheld to protect credentials.')
            log('No automatic deletion or secret rotation.')
            status = 1
    print('Private report: '+filename)
    print('Read it with: cat '+str(filename))
    return status


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print('Stopped: '+str(error), file=sys.stderr); sys.exit(1)

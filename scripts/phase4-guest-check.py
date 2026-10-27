#!/usr/bin/env python3
"""Read-only checks inside the single lab guest; input is supplied by the R2d2 wrapper."""
import base64
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

DOMAIN = 'cloud-images.ubuntu.com'
HTTPS_URL = 'https://cloud-images.ubuntu.com/'


def run(args, timeout=20):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env={**os.environ, 'LC_ALL': 'C'})
    if result.returncode: raise RuntimeError('Guest read/probe failed: '+args[0]+' (exit '+str(result.returncode)+')')
    return result.stdout.strip()


def lease_values(text):
    values = {}
    for line in text.splitlines():
        if not line or line.startswith('#') or '=' not in line: continue
        key, value = line.split('=', 1)
        if key in ('ADDRESS','ROUTER','SERVER_ADDRESS','DNS'):
            if key in values: raise RuntimeError('Duplicate DHCP lease field')
            values[key] = value.strip()
    return values


def address_checks(links, routes, expected, lease):
    interfaces = [a for a in links if a.get('address', '').lower() == expected['mac'].lower()]
    if len(interfaces) != 1: raise RuntimeError('Expected exactly one guest interface matching the Neutron port MAC')
    interface = interfaces[0]; name = interface['ifname']
    return name, {
        'private_address_matches_Neutron': any(a.get('local') == expected['private_ip'] and a.get('prefixlen') == 24 for a in interface.get('addr_info', [])),
        'interface_MTU_matches_network': interface.get('mtu') == expected['mtu'],
        'default_route_uses_private_gateway': any(r.get('dst') == 'default' and r.get('gateway') == expected['gateway'] and r.get('dev') == name for r in routes),
        'DHCP_lease_address_matches': lease.get('ADDRESS') == expected['private_ip'],
        'DHCP_server_matches_Neutron': lease.get('SERVER_ADDRESS') in expected['dhcp_server_ips'],
        'DHCP_gateway_matches': expected['gateway'] in lease.get('ROUTER', '').split(),
        'DHCP_DNS_matches': expected['dns'] in lease.get('DNS', '').split()}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


def collect(expected):
    if os.geteuid() != 0: raise RuntimeError('Guest read checks require sudo from the ubuntu account')
    result = {'passed': False, 'checks': {}, 'observations': {}}; checks = result['checks']; facts = result['observations']
    try:
        release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
        checks['Ubuntu_24_04'] = release.get('ID', '').strip('"') == 'ubuntu' and release.get('VERSION_ID', '').strip('"') == '24.04'
        facts['hostname'] = socket.gethostname()
        deadline = time.monotonic()+180
        while True:
            proc = subprocess.run(['cloud-init','status','--format','json'], capture_output=True, text=True, timeout=15)
            value = json.loads(proc.stdout)
            if value.get('status') != 'running' or time.monotonic() >= deadline: break
            time.sleep(5)
        facts['cloud_init'] = {'status': value.get('status'), 'extended_status': value.get('extended_status'), 'rc': proc.returncode}
        checks['cloud_init_completed_without_errors'] = proc.returncode == 0 and value.get('status') == 'done'
        links = json.loads(run(['ip','-j','address','show']))
        matching = [a for a in links if a.get('address','').lower() == expected['mac'].lower()]
        if len(matching) != 1: raise RuntimeError('Neutron port MAC not uniquely present in the guest')
        interface = matching[0]['ifname']
        index = int(Path('/sys/class/net', interface, 'ifindex').read_text())
        # Ubuntu's networkd lease snapshot is read only; unknown/missing fields do not pass.
        lease = lease_values(Path('/run/systemd/netif/leases', str(index)).read_text())
        routes = json.loads(run(['ip','-j','-4','route','show']))
        interface, ip_checks = address_checks(links, routes, expected, lease); checks.update(ip_checks)
        facts.update(interface=interface, private_ip=expected['private_ip'], mac=expected['mac'], mtu=matching[0].get('mtu'),
                     default_routes=[r for r in routes if r.get('dst') == 'default'], dhcp_lease=lease)
        dns = run(['resolvectl','dns',interface]); facts['resolver'] = dns
        checks['guest_resolver_uses_configured_DNS'] = expected['dns'] in dns.split()
        run(['ping','-n','-c','3','-W','2',expected['gateway']], timeout=15)
        checks['private_gateway_ping'] = True
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open('http://169.254.169.254/openstack/latest/meta_data.json', timeout=10) as response:
            raw = response.read(65537)
            if response.status != 200 or len(raw) > 65536: raise RuntimeError('Metadata response needs review')
            meta = json.loads(raw)
        facts['metadata_instance_id'] = meta.get('uuid'); checks['metadata_matches_Nova_instance'] = meta.get('uuid') == expected['server_id']
        answers = run(['timeout','20','getent','ahostsv4',DOMAIN], timeout=25)
        addresses = sorted({str(ipaddress.IPv4Address(line.split()[0])) for line in answers.splitlines() if line.split()})
        facts['DNS_answers'] = {DOMAIN: addresses}; checks['guest_DNS_lookup'] = bool(addresses)
        with opener.open(HTTPS_URL, timeout=20) as response:
            if response.status != 200: raise RuntimeError('Guest outbound HTTPS status needs review')
            response.read(512)
        facts['outbound_HTTPS_URL'] = HTTPS_URL; checks['outbound_HTTPS_certificate_and_response'] = True
        result['passed'] = all(checks.values())
    except Exception as error:
        result['error'] = str(error) if isinstance(error, RuntimeError) else type(error).__name__+'; guest check incomplete'
    return result


def main():
    try:
        expected = json.loads(base64.b64decode(sys.argv[1], validate=True))
        result = collect(expected)
    except Exception as error: result = {'passed': False, 'error': type(error).__name__+'; guest check input/runtime needs review'}
    print(json.dumps(result)); return 0 if result['passed'] else 1

if __name__ == '__main__': raise SystemExit(main())

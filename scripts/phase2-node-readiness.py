#!/usr/bin/env python3
"""Chapter F: read-only node facts and bounded LAN ARP conflict observation."""
import argparse
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import time

NODES = {
 'node1': ('192.168.0.200', 'enx00e04c557e58', '00:e0:4c:55:7e:58', 'ata-KINGSTON_SUV400S37240G_50026B7766070422'),
 'node2': ('192.168.0.201', 'enx00e04c2c1678', '00:e0:4c:2c:16:78', 'ata-ADATA_SU630_4P3521354988'),
 'node3': ('192.168.0.202', 'enx00e04c304688', '00:e0:4c:30:46:88', 'ata-KINGSTON_SA400S37240G_50026B768317883A'),
}
VIP = '192.168.0.151'
MGMT = 'enp0s31f6'

def command(args):
    p = subprocess.run(args, text=True, capture_output=True, timeout=20)
    if p.returncode:
        raise RuntimeError('Read-only command unavailable or unsuccessful: '+args[0])
    return p.stdout.strip()

def arp_packet(mac, target):
    # RFC 5227 probe: source IPv4 is zero; do not claim or assign the target.
    return (b'\xff'*6 + mac + b'\x08\x06' + struct.pack('!HHBBH6s4s6s4s',
        1, 0x0800, 6, 4, 1, mac, b'\0'*4, b'\0'*6, socket.inet_aton(target))).ljust(60, b'\0')

def conflict_mac(frame, own_mac, target):
    if len(frame) < 42 or frame[12:14] != b'\x08\x06':
        return None
    hw, proto, hlen, plen, op = struct.unpack('!HHBBH', frame[14:22])
    if (hw, proto, hlen, plen) != (1, 0x0800, 6, 4) or op not in (1, 2):
        return None
    sender, source, dest = frame[22:28], frame[28:32], frame[38:42]
    if sender == own_mac:
        return None
    ip = socket.inet_aton(target)
    if source == ip or (op == 1 and source == b'\0'*4 and dest == ip):
        return ':'.join(f'{b:02x}' for b in sender)
    return None

def vip_conflicts():
    mac = bytes.fromhex(Path('/sys/class/net', MGMT, 'address').read_text().strip().replace(':',''))
    conflicts = set()
    with socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0806)) as sock:
        sock.bind((MGMT, 0))
        for _ in range(3):
            sock.send(arp_packet(mac, VIP))
            deadline = time.monotonic()+1
            while time.monotonic() < deadline:
                sock.settimeout(max(0.001, deadline-time.monotonic()))
                try:
                    found = conflict_mac(sock.recv(65535), mac, VIP)
                except socket.timeout:
                    break
                if found:
                    conflicts.add(found)
    return sorted(conflicts)

def collect(node):
    ip, usb, mac, disk_id = NODES[node]
    facts, checks = {}, {}
    def check(label, value):
        checks[label] = bool(value)
    check('sudo root from joe', os.geteuid() == 0 and os.environ.get('SUDO_USER') == 'joe')
    check('hostname', socket.gethostname().split('.')[0] == node)
    os_release = dict(line.split('=',1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    check('Ubuntu 24.04', os_release.get('ID','').strip('"') == 'ubuntu' and os_release.get('VERSION_ID','').strip('"') == '24.04')
    addresses = json.loads(command(['ip','-j','address','show']))
    by_name = {x['ifname']: x for x in addresses}
    management = by_name.get(MGMT,{})
    check('management IPv4 /24', any(a.get('local') == ip and a.get('prefixlen') == 24 for a in management.get('addr_info',[])))
    if not all(checks.values()):
        return {'node':node,'checks':checks,'facts':facts,'ready':False}
    routes = json.loads(command(['ip','-j','-4','route','show','default']))
    check('management default route', any(r.get('dev') == MGMT and r.get('gateway') == '192.168.0.1' for r in routes))
    provider = by_name.get(usb,{})
    check('USB MAC', provider.get('address','').lower() == mac)
    check('USB no host addresses', bool(provider) and provider.get('addr_info') == [])
    check('USB link', 'UP' in provider.get('flags',[]) and 'LOWER_UP' in provider.get('flags',[]))
    facts['USB'] = usb
    flags = [set(line.split(':',1)[1].split()) for line in Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('flags') and ':' in line]
    check('CPU virtualization flags', bool(flags) and all({'vmx','svm'} & f for f in flags))
    facts['/dev/kvm present'] = Path('/dev/kvm').exists()
    # Missing device can mean a module has not loaded yet, so report without changing it.
    facts['CPU threads'] = os.cpu_count()
    facts['MemTotal'] = next((line for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemTotal:')), 'unknown')
    facts['KVM note'] = 'Device presence is observed only; usable acceleration is checked in Phase 3.'
    check('Chrony active', command(['systemctl','is-active','chrony.service']) == 'active')
    check('clock synchronized', command(['timedatectl','show','--property=NTPSynchronized','--value']) == 'yes')
    vgs = json.loads(command(['vgs','--reportformat','json','--units','b','--nosuffix','-o','vg_name,pv_count,lv_count']))['report'][0]['vg']
    groups = [v for v in vgs if v['vg_name'].strip() == 'cinder-volumes']
    check('empty Cinder VG: one PV, zero LVs', len(groups) == 1 and int(groups[0]['pv_count']) == 1 and int(groups[0]['lv_count']) == 0)
    pvs = json.loads(command(['pvs','--reportformat','json','--units','b','--nosuffix','-o','pv_name,vg_name']))['report'][0]['pv']
    selected = [p for p in pvs if p['vg_name'].strip() == 'cinder-volumes']
    stable = Path('/dev/disk/by-id', disk_id)
    check('Cinder PV stable disk identity', stable.exists() and len(selected) == 1 and Path(selected[0]['pv_name'].strip()).resolve() == stable.resolve())
    blocks = json.loads(command(['lsblk','-b','-J','-d','-o','PATH,SERIAL,SIZE',str(stable)]))['blockdevices'] if stable.exists() else []
    check('Cinder disk serial and size', len(blocks) == 1 and str(blocks[0].get('serial','')).strip() == disk_id.rsplit('_',1)[1] and int(blocks[0]['size']) == 240057409536)
    facts['Cinder disk by-id'] = str(stable)
    facts['VIP conflicting MACs'] = vip_conflicts()
    check('VIP: no active conflict observed', not facts['VIP conflicting MACs'])
    facts['VIP limit'] = 'Three ARP probes on the physical LAN; silent/offline devices cannot be excluded. Keep the router reservation/exclusion.'
    return {'node':node,'checks':checks,'facts':facts,'ready':all(checks.values())}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', required=True, choices=NODES)
    args = parser.parse_args()
    try:
        result = collect(args.node)
    except Exception as e:
        result = {'node':args.node,'ready':False,'error': str(e) if isinstance(e,RuntimeError) else type(e).__name__}
    # Ad-hoc Ansible writes this JSON even for a non-ready result; the wrapper decides status.
    print(json.dumps(result))

if __name__ == '__main__':
    main()

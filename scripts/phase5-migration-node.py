#!/usr/bin/env python3
"""Read-only host, libvirt and CPU observations for planned VM migration."""
import argparse
import base64
import configparser
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import urllib.parse
import uuid
import xml.etree.ElementTree as ET

NODES={'node1':'192.168.0.200','node2':'192.168.0.201','node3':'192.168.0.202'}
IMAGES={name:'quay.io/openstack.kolla/'+image+':2026.2-ubuntu-noble' for name,image in [('nova_compute','nova-compute'),('nova_libvirt','nova-libvirt')]}


def run(args,input=None,allow_failure=False):
    value=subprocess.run(args,input=input,capture_output=True,text=True,timeout=35)
    if value.returncode and not allow_failure:raise RuntimeError('Read-only command failed: '+args[0])
    return value


def libvirt_uri(value,node):
    value=value.strip().strip('"\'')
    parsed=urllib.parse.urlsplit(value)
    if parsed.scheme not in ('qemu+tcp','qemu+tls') or parsed.username or parsed.password or parsed.hostname not in (node,NODES[node]) or parsed.path!='/system' or parsed.query or parsed.fragment or parsed.port is not None:
        raise RuntimeError('Unreviewed libvirt URI; credentials/URI withheld')
    return value


def configuration():
    path=Path('/etc/kolla/nova-compute/nova.conf');raw=path.read_bytes()
    parser=configparser.ConfigParser(interpolation=None,strict=False);parser.read_string(raw.decode())
    keys={'DEFAULT':['instances_path','compute_driver','my_ip'],
          'libvirt':['virt_type','cpu_mode','cpu_models','live_migration_scheme','live_migration_inbound_addr','live_migration_tunnelled','live_migration_with_native_tls','live_migration_permit_post_copy','live_migration_permit_auto_converge','live_migration_timeout_action'],
          'vnc':['server_listen'],'workarounds':['skip_cpu_compare_on_dest']}
    values={section:{key:parser.get(section,key,fallback=None) for key in options} for section,options in keys.items()}
    values['config_sha256']=hashlib.sha256(raw).hexdigest()
    return parser,values


def domain_summary(raw,server):
    if len(raw)>2_000_000 or '<!DOCTYPE' in raw or '<!ENTITY' in raw:raise RuntimeError('Unreviewed domain XML')
    root=ET.fromstring(raw)
    if root.tag!='domain' or root.findtext('uuid')!=server:raise RuntimeError('Libvirt domain UUID differs from tracked VM')
    cpu=root.find('cpu');os_type=root.find('os/type')
    if cpu is None or os_type is None:raise RuntimeError('Domain CPU/machine details unavailable')
    disks=[]
    for disk in root.findall('devices/disk'):
        target=disk.find('target');source=disk.find('source');driver=disk.find('driver')
        disks.append({'device':disk.get('device'),'type':disk.get('type'),'target':target.get('dev') if target is not None else None,
          'driver_type':driver.get('type') if driver is not None else None,
          'source_protocol':source.get('protocol') if source is not None else None,'readonly':disk.find('readonly') is not None})
    # Only CPU XML is retained, never disk/authentication/console XML.
    return {'uuid':server,'virt_type':root.get('type'),'arch':os_type.get('arch'),'machine':os_type.get('machine'),
      'emulator':root.findtext('devices/emulator'),'cpu_xml':ET.tostring(cpu,encoding='unicode'),
      'memory':{'value':root.findtext('memory'),'unit':root.find('memory').get('unit') if root.find('memory') is not None else None},
      'vcpus':root.findtext('vcpu'),'disks':disks}


def identity(node):
    if node not in NODES or os.geteuid()!=0 or os.environ.get('SUDO_USER')!='joe' or socket.gethostname().split('.')[0]!=node:raise RuntimeError('Expected node identity and sudo from joe')
    links=json.loads(run(['ip','-j','address','show','dev','enp0s31f6']).stdout)
    if len(links)!=1 or not any(a.get('local')==NODES[node] for a in links[0].get('addr_info',[])):raise RuntimeError('Management IP differs')
    containers={}
    fmt='{"id":{{json .Id}},"image":{{json .Config.Image}},"running":{{json .State.Running}},"network_mode":{{json .HostConfig.NetworkMode}},"mounts":{{json .Mounts}}}'
    for name,image in IMAGES.items():
        item=json.loads(run(['docker','inspect','--format',fmt,name]).stdout)
        if item.get('image')!=image or item.get('running') is not True or item.get('network_mode')!='host' or not re.fullmatch(r'[a-f0-9]{64}',item.get('id','')):raise RuntimeError('Expected running Kolla compute/libvirt identity')
        containers[name]=item
    return containers


def virsh(*args,input=None,allow_failure=False,uri='qemu:///system'):
    return run(['docker','exec','-i','--user','root','nova_libvirt','virsh','--readonly','--connect',uri,*args],input=input,allow_failure=allow_failure)


def observe(node,server,source,containers):
    parser,config=configuration()
    local_uri=libvirt_uri(parser.get('libvirt','connection_uri',fallback=''),node)
    config['connection_uri']=local_uri
    instances=parser.get('DEFAULT','instances_path',fallback=None)
    # A default is not silently treated as a measured runtime setting. The
    # deployed /var/lib/nova mount is still inspected when the option is absent.
    container_path=instances.strip('"\'') if instances else '/var/lib/nova/instances'
    if not container_path.startswith('/var/lib/nova/') or '..' in Path(container_path).parts:raise RuntimeError('Unreviewed instance storage path')
    mounts=containers['nova_compute']['mounts']
    choices=[m for m in mounts if container_path==m.get('Destination') or container_path.startswith(m.get('Destination','!').rstrip('/')+'/')]
    if not choices:raise RuntimeError('No observed Nova mount for instance storage')
    mount=max(choices,key=lambda m:len(m['Destination']));hostpath=Path(mount['Source'])/Path(container_path).relative_to(mount['Destination'])
    usage=os.statvfs(hostpath)
    memory={}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key,_,value=line.partition(':')
        if key in ('MemTotal','MemAvailable','SwapTotal','SwapFree'):memory[key+'_bytes']=int(value.split()[0])*1024
    cpus=Path('/proc/cpuinfo').read_text().split('\n\n')
    first={line.split(':',1)[0].strip():line.split(':',1)[1].strip() for line in cpus[0].splitlines() if ':' in line}
    cap=ET.fromstring(virsh('capabilities').stdout)
    domain=None
    if node==source:
        domain=domain_summary(virsh('dumpxml',server,'--migratable').stdout,server)
    addresses={}
    for n in NODES:
        try:addresses[n]=sorted({a[4][0] for a in socket.getaddrinfo(n,None,socket.AF_INET)})
        except socket.gaierror:addresses[n]=[]
    return {'node':node,'observed':True,'containers':{n:{k:v[k] for k in ('id','image','running')} for n,v in containers.items()},
      'memory':memory,'cpu':{'logical_cpus':os.cpu_count(),'model':first.get('model name'),'vendor':first.get('vendor_id'),'flags':first.get('flags','').split()},
      'instances_storage':{'container_path':container_path,'path_explicit_in_nova_config':instances is not None,'available_bytes':usage.f_bavail*usage.f_frsize,'total_bytes':usage.f_blocks*usage.f_frsize},
      'nova_config':config,'libvirt_version':virsh('version').stdout.strip(),
      'libvirt_host_cpu':{'arch':cap.findtext('host/cpu/arch'),'model':cap.findtext('host/cpu/model'),'vendor':cap.findtext('host/cpu/vendor')},
      'peer_name_resolution':addresses,'domain':domain}


def compare(node,payload):
    domain=payload['domain'];cpu=domain.get('cpu_xml','')
    if len(cpu)>100_000 or '<!DOCTYPE' in cpu or '<!ENTITY' in cpu:raise RuntimeError('Invalid CPU XML')
    root=ET.fromstring(cpu)
    if root.tag!='cpu' or domain.get('virt_type')!='kvm' or domain.get('arch')!='x86_64' or domain.get('emulator')!='/usr/bin/qemu-system-x86_64' or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]*',domain.get('machine','')):raise RuntimeError('Unreviewed source CPU/emulator/machine')
    result=virsh('hypervisor-cpu-compare','/dev/stdin','--virttype','kvm','--emulator',domain['emulator'],'--arch','x86_64','--machine',domain['machine'],'--error',input=cpu,allow_failure=True)
    return {'node':node,'observed':True,'source_cpu_supported_by_destination_hypervisor':result.returncode==0,
      'comparison':'compatible CPU definition' if result.returncode==0 else 'not confirmed; return report before migration'}


def connection(node,payload):
    peer=payload['peer']
    if peer not in NODES or peer==node:raise RuntimeError('Unreviewed peer')
    uri=libvirt_uri(payload['uri'],peer)
    result=virsh('hostname',uri=uri,allow_failure=True)
    name=result.stdout.strip().split('.')[0]
    return {'node':node,'peer':peer,'observed':True,'authenticated_libvirt_read':result.returncode==0 and name==peer,
      'transport':urllib.parse.urlsplit(uri).scheme,'encrypted_transport':urllib.parse.urlsplit(uri).scheme=='qemu+tls'}


def execute(node,action,payload):
    containers=identity(node)
    if action=='observe':
        server=payload['server_id'];source=payload['source']
        if str(uuid.UUID(server))!=server or source not in NODES:raise RuntimeError('Invalid tracked VM/source')
        return observe(node,server,source,containers)
    if action=='compare':return compare(node,payload)
    if action=='connection':return connection(node,payload)
    raise RuntimeError('Only read-only inspection actions are supported')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--node',choices=NODES,required=True);parser.add_argument('--action',choices=['observe','compare','connection'],required=True);parser.add_argument('--payload',required=True);args=parser.parse_args()
    try:
        payload=json.loads(base64.b64decode(args.payload,validate=True));result=execute(args.node,args.action,payload)
    except Exception as error:result={'node':args.node,'observed':False,'error':str(error) if isinstance(error,RuntimeError) else type(error).__name__+'; inspection incomplete'}
    print(json.dumps(result));return 0 if result.get('observed') else 1

if __name__=='__main__':raise SystemExit(main())

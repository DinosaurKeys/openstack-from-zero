#!/usr/bin/env python3
"""Read-only router inspection. No fault injection, service or interface changes."""
import argparse
import base64
import datetime as dt
import getpass
import json
import os
from pathlib import Path
import shlex
import socket
import stat
import subprocess
import sys
import tempfile
import uuid

REPO = Path(__file__).resolve().parent.parent
NODES = {'node1': '192.168.0.200', 'node2': '192.168.0.201', 'node3': '192.168.0.202'}

# Executed inside the existing container. Configuration contents and authentication
# material are never printed; only a digest and selected non-secret facts leave it.
READER = r'''import configparser,hashlib,importlib.metadata,json,re,sys
from pathlib import Path
router=sys.argv[1]
cfg=configparser.ConfigParser(interpolation=None)
if len(cfg.read(['/etc/neutron/neutron.conf','/etc/neutron/l3_agent.ini']))!=2:
 raise RuntimeError('Expected Neutron configuration unavailable')
state=cfg.defaults().get('state_path')
if state!='/var/lib/neutron/kolla':raise RuntimeError('Unreviewed Neutron state directory')
ha=cfg.defaults().get('ha_confs_path',state+'/ha_confs').replace('${state_path}',state).replace('$state_path',state)
if ha!=state+'/ha_confs':raise RuntimeError('Unreviewed HA configuration directory')
path=Path(ha)/router/'keepalived.conf'
if path.is_symlink() or not path.is_file():raise RuntimeError('Expected regular router configuration')
raw=path.read_bytes();clean=re.sub(r'#[^\n]*','',raw.decode())
blocks=re.findall(r'\btrack_interface\s*\{([^{}]*)\}',clean,re.S)
try:version=importlib.metadata.version('neutron')
except importlib.metadata.PackageNotFoundError:version='not available from this interpreter'
print(json.dumps({'neutron_version':version,'config_sha256':hashlib.sha256(raw).hexdigest(),
 'vrrp_instances':len(re.findall(r'\bvrrp_instance\s+',clean)),
 'tracked_interfaces':[line.strip() for block in blocks for line in block.splitlines() if line.strip()],
 'primary_virtual_addresses':[line.split()[0] for block in re.findall(r'\bvirtual_ipaddress\s*\{([^{}]*)\}',clean,re.S) for line in block.splitlines() if line.split()]}))
'''

NODE = r'''import json,os,re,socket,subprocess,sys,uuid
node,router,expected_ip,reader=sys.argv[1:]
def run(args):
 p=subprocess.run(args,capture_output=True,text=True,timeout=25)
 if p.returncode:raise RuntimeError('Read-only inspection command failed: '+args[0])
 return p.stdout.strip()
try:
 if os.geteuid()!=0 or os.environ.get('SUDO_USER')!='joe' or socket.gethostname().split('.')[0]!=node:
  raise RuntimeError('Expected node identity and sudo from joe')
 if str(uuid.UUID(router))!=router:raise RuntimeError('Invalid tracked router UUID')
 host=json.loads(run(['ip','-j','address','show','dev','enp0s31f6']))
 if len(host)!=1 or not any(a.get('local')==expected_ip for a in host[0].get('addr_info',[])):
  raise RuntimeError('Management address does not match node')
 fmt='{"id":{{json .Id}},"image":{{json .Config.Image}},"running":{{json .State.Running}},"network_mode":{{json .HostConfig.NetworkMode}}}'
 container=json.loads(run(['docker','inspect','--format',fmt,'neutron_l3_agent']))
 if container.get('image')!='quay.io/openstack.kolla/neutron-l3-agent:2026.2-ubuntu-noble' or container.get('running') is not True or container.get('network_mode')!='host':
  raise RuntimeError('Expected running Kolla L3 container with host networking')
 namespace='qrouter-'+router;info=os.stat('/run/netns/'+namespace)
 links=json.loads(run(['ip','netns','exec',namespace,'ip','-j','address','show']))
 selected=[]
 for link in links:
  if not re.fullmatch(r'(?:ha|qr|qg)-[a-f0-9-]+',link.get('ifname','')):continue
  selected.append({'name':link['ifname'],'index':link.get('ifindex'),'mac':link.get('address'),
   'admin_up':'UP' in link.get('flags',[]),
   'ipv4':[a['local']+'/'+str(a['prefixlen']) for a in link.get('addr_info',[]) if a.get('family')=='inet']})
 config=json.loads(run(['docker','exec','--user','root','neutron_l3_agent','python3','-c',reader,router]))
 result={'node':node,'observed':True,'namespace':namespace,'namespace_identity':[info.st_dev,info.st_ino],
  'container':container,'interfaces':selected,'keepalived':config}
except Exception as error:
 result={'node':node,'observed':False,'error':str(error) if isinstance(error,RuntimeError) else type(error).__name__+'; inspection incomplete'}
print(json.dumps(result));sys.exit(0 if result['observed'] else 1)
'''


def private_file(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise RuntimeError('Expected private, owned regular file: ' + str(path))


def tracked_router():
    path = REPO / 'private/phase4/vm.json'
    private_file(path)
    router = json.loads(path.read_text())['networks']['router']
    if str(uuid.UUID(router)) != router:
        raise RuntimeError('Invalid tracked router UUID')
    return router


def observe(node, router, password):
    key = Path.home() / '.ssh/openstack-from-zero'
    private_file(key)
    payload = base64.b64encode(NODE.encode()).decode()
    code = "import base64;exec(compile(base64.b64decode(" + repr(payload) + "),'readonly-router-inspection','exec'))"
    remote = shlex.join(['sudo', '-S', '-p', '', '/usr/bin/python3', '-c', code, node, router, NODES[node], READER])
    args = ['ssh', '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'ForwardAgent=no',
            '-i', str(key), 'joe@' + NODES[node], remote]
    try:
        result = subprocess.run(args, input=password + '\n', capture_output=True, text=True, timeout=130)
        value = json.loads(result.stdout)
        if value.get('node') != node or type(value.get('observed')) is not bool:
            raise ValueError('Unexpected response')
        if result.returncode and value.get('observed'):
            raise ValueError('SSH response/exit status differ')
        return value
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {'node': node, 'observed': False, 'error': 'SSH or inspection response unavailable; no changes requested'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='inspect existing router on all three nodes')
    args = parser.parse_args(argv)
    if not args.run:
        print('READ-ONLY PLAN: inspect the tracked router namespace, interface addresses and selected Keepalived facts on node1/node2/node3. Run with --run. No interface/service changes or VM creation.')
        return 0
    if getpass.getuser() != 'joe' or socket.gethostname().split('.')[0].lower() != 'r2d2' or not sys.stdin.isatty():
        raise RuntimeError('Run in joe\'s interactive R2d2 terminal')
    router = tracked_router()
    folder = REPO / 'reports'
    folder.mkdir(mode=0o700, exist_ok=True)
    info = folder.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise RuntimeError('Expected private owned reports directory')
    fd, name = tempfile.mkstemp(prefix='phase5-router-inspect-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), suffix='.txt', dir=folder)
    print('Private report: ' + name, flush=True)
    complete = True
    with os.fdopen(fd, 'w') as report:
        def log(line):
            print(line, flush=True)
            report.write(line + '\n'); report.flush()
        log('Phase 5 D — READ-ONLY router inspection; router ID: ' + router)
        for node in NODES:
            password = getpass.getpass('Joe sudo password on ' + node + ' (hidden): ')
            try:
                value = observe(node, router, password)
            finally:
                password = None
            complete = complete and value['observed']
            log(json.dumps(value, indent=2))
        log('RESULT: ROUTER_INSPECTION_COLLECTED' if complete else 'RESULT: ROUTER_INSPECTION_INCOMPLETE')
        log('Facts only. No failover, service stop, interface change, VM creation or volume operation was requested. Return this report for review.')
    print('Read report: cat ' + shlex.quote(name))
    return 0 if complete else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        print('Stopped: ' + str(error) if isinstance(error, RuntimeError) else 'Stopped: required local input unavailable; no changes requested', file=sys.stderr)
        sys.exit(1)

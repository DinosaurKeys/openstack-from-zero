#!/usr/bin/env python3
"""Read-only host preparation observations; run by the Chapter A wrapper."""
import argparse,hashlib,json,os,socket,subprocess,sysconfig
from pathlib import Path
PACKAGES=['git','iputils-ping','tzdata','ca-certificates','gnupg','docker-ce','docker-ce-cli','containerd.io','apparmor','python3-setuptools','python3-pip','python3-docker','python3-dbus','lxd','lxc','open-iscsi','libvirt-bin','ufw']
NODES={'node1':'192.168.0.200','node2':'192.168.0.201','node3':'192.168.0.202'}
def run(args,allowed=(0,)):
 p=subprocess.run(args,capture_output=True,text=True,timeout=30)
 if p.returncode not in allowed:raise RuntimeError('Read command needs review: '+args[0])
 return p.stdout.strip()
def digest_files(paths):
 return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(paths)) if p.is_file()}
def collect(node,after):
 if socket.gethostname().split('.')[0]!=node or os.geteuid()!=0 or os.environ.get('SUDO_USER')!='joe':
  raise RuntimeError('Expected node identity and sudo from joe')
 text=run(['dpkg-query','-W','-f=${binary:Package}\t${db:Status-Status}\t${Version}\n',*PACKAGES],(0,1))
 packages={}
 for line in text.splitlines():
  fields=line.split('\t')
  if len(fields)==3 and fields[1]=='installed':packages[fields[0].split(':')[0]]=fields[2]
 protected=digest_files([*Path('/etc/netplan').glob('*.yaml'),Path('/etc/chrony/chrony.conf'),*Path('/etc/chrony').rglob('*.sources'),*Path('/etc/chrony').rglob('*.conf'),Path('/etc/ssh/sshd_config'),*Path('/etc/ssh/sshd_config.d').glob('*.conf')])
 checks={'system Python externally managed':(Path(sysconfig.get_path('stdlib'))/'EXTERNALLY-MANAGED').is_file()}
 facts={'installed_packages':packages,'protected_config_hashes':protected,'hosts_file':Path('/etc/hosts').read_text(),
 'management_DNS':run(['resolvectl','dns','enp0s31f6']), 'chrony_active':run(['systemctl','is-active','chrony'],(0,3,4))}
 if not after:
  # Stop rather than prepare an already occupied container/virtualization host.
  occupied=[]
  for directory in ('/var/lib/docker/containers','/var/lib/lxd/containers','/var/snap/lxd/common/lxd/containers','/sys/class/iscsi_session'):
   p=Path(directory)
   if p.exists() and any(p.iterdir()):occupied.append(directory)
  if list(Path('/etc/libvirt/qemu').glob('*.xml')):occupied.append('/etc/libvirt/qemu/*.xml')
  checks['no recorded host workloads']=not occupied
  facts['workload_locations']=occupied
 else:
  for package in ['git','iputils-ping','tzdata','ca-certificates','gnupg','docker-ce','apparmor','python3-setuptools','python3-pip','python3-docker','python3-dbus']:
   checks['installed '+package]=package in packages
  for package in ['lxd','lxc','open-iscsi','libvirt-bin']:checks['host package absent '+package]=package not in packages
  checks['Docker active']=run(['systemctl','is-active','docker'],(0,3,4))=='active'
  checks['Docker enabled']=run(['systemctl','is-enabled','docker'],(0,1,3,4))=='enabled'
  facts['Docker server version']=run(['docker','version','--format','{{.Server.Version}}'])
  checks['Docker SDK can contact engine']=run(['/usr/bin/python3','-c','import docker,dbus; c=docker.from_env(); print(c.ping()); c.close()'])=='True'
  config=json.loads(Path('/etc/docker/daemon.json').read_text())
  checks['Kolla Docker network options']=all(config.get(k)==v for k,v in {'iptables':False,'bridge':'none','ip-forward':False}.items())
  if 'ufw' in packages:checks['UFW inactive']=run(['ufw','status']).startswith('Status: inactive')
  for host,ip in NODES.items():
   answers=run(['getent','ahostsv4',host]).splitlines()
   checks['node name '+host]=bool(answers) and {line.split()[0] for line in answers}=={ip}
  checks['kolla target exists']=Path('/etc/systemd/system/kolla.target').is_file()
 return {'node':node,'ready':all(checks.values()),'checks':checks,'facts':facts}
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--node',choices=NODES,required=True);p.add_argument('--after',action='store_true');args=p.parse_args()
 try:result=collect(args.node,args.after)
 except Exception as e:result={'node':args.node,'ready':False,'error':str(e) if isinstance(e,RuntimeError) else type(e).__name__}
 print(json.dumps(result))
if __name__=='__main__':main()

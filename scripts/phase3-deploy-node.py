#!/usr/bin/env python3
"""Read-only deployment observations; no ARP probes or empty-VG assertion."""
import argparse,configparser,hashlib,json,os,shlex,socket,subprocess,time
from pathlib import Path
# Names from the pinned release's service definitions and the all-three-role inventory.
REQUIRED=('mariadb','proxysql','haproxy','keepalived','rabbitmq','keystone_api','keystone_fernet','keystone_ssh','glance_api',
 'placement_api','nova_api','nova_scheduler','nova_conductor','nova_compute','nova_libvirt',
 'neutron_server','neutron_openvswitch_agent','neutron_l3_agent','neutron_dhcp_agent',
 'openvswitch_db','openvswitch_vswitchd','cinder_api','cinder_scheduler','cinder_volume','horizon')
NODES={'node1':('192.168.0.200','enx00e04c557e58','00:e0:4c:55:7e:58'),
 'node2':('192.168.0.201','enx00e04c2c1678','00:e0:4c:2c:16:78'),
 'node3':('192.168.0.202','enx00e04c304688','00:e0:4c:30:46:88')}
def run(args):
 p=subprocess.run(args,capture_output=True,text=True,timeout=30)
 if p.returncode:raise RuntimeError('Read-only command failed (exit '+str(p.returncode)+'): '+shlex.join(args)+'; '+p.stderr.strip()[:600])
 return p.stdout.strip()
def container_states():
 names=set(run(['docker','ps','-a','--format','{{.Names}}']).splitlines())
 found=[n for n in REQUIRED if n in names]
 states={}
 if found:
  lines=run(['docker','inspect','--format','{{json .State}}',*found]).splitlines()
  if len(lines)!=len(found):raise RuntimeError('Incomplete container state output')
  for name,line in zip(found,lines):
   value=json.loads(line)
   states[name]={'running':value.get('Running') is True,'status':value.get('Status'),
                'health':value.get('Health',{}).get('Status','not configured')}
 return states

def required_containers(node):
 if node not in NODES:raise RuntimeError('Unknown node for container placement')
 # Approved local file backend, default "glance" volume: pinned Kolla
 # group_vars/all/glance.yml selects the first glance-api host (node1).
 return tuple(name for name in REQUIRED if name!='glance_api' or node=='node1')

def healthy(states,node='node1'):
 return set(required_containers(node)).issubset(states) and all(s['running'] and s['health'] in ('healthy','not configured') for s in states.values())

def ha_configuration():
 parser=configparser.ConfigParser(interpolation=None)
 parser.read('/etc/kolla/neutron-server/neutron.conf')
 expected={'l3_ha':'true','max_l3_agents_per_router':'3','dhcp_agents_per_network':'2'}
 actual={k:parser.defaults().get(k,'').lower() for k in expected}
 return actual, actual==expected

def galera_status(ip):
 keys=('wsrep_cluster_status','wsrep_cluster_size','wsrep_connected','wsrep_ready','wsrep_local_state_comment','wsrep_cluster_state_uuid')
 query='SHOW GLOBAL STATUS WHERE Variable_name IN ('+','.join("'"+k+"'" for k in keys)+')'
 output=run(['docker','exec','--user','root','mariadb','mariadb','--defaults-file=/etc/mysql/healthcheck.cnf','--protocol=tcp','--host='+ip,'--port=3306','--connect-timeout=5','--batch','--skip-column-names','--execute='+query])
 values={}
 for line in output.splitlines():
  parts=line.split('\t')
  if len(parts)!=2 or parts[0] in values or parts[0] not in keys:raise RuntimeError('Unexpected MariaDB monitor output')
  values[parts[0]]=parts[1]
 if set(values)!=set(keys):raise RuntimeError('Incomplete MariaDB monitor output')
 expected={'wsrep_cluster_status':'Primary','wsrep_cluster_size':'3','wsrep_connected':'ON','wsrep_ready':'ON','wsrep_local_state_comment':'Synced'}
 return values, all(values[k]==v for k,v in expected.items())

def collect(node):
 if os.geteuid()!=0 or os.environ.get('SUDO_USER')!='joe' or socket.gethostname().split('.')[0]!=node:
  raise RuntimeError('Expected node identity and sudo from joe')
 result={'node':node,'ready':False,'checks':{},'facts':{}}
 try:collect_details(node,result)
 except Exception as e:result['error']=str(e) if isinstance(e,RuntimeError) else type(e).__name__
 return result

def collect_details(node,result):
 ip,usb,mac=NODES[node]
 checks=result['checks'];facts=result['facts']
 addresses=json.loads(run(['ip','-j','address','show']))
 by_name={a['ifname']:a for a in addresses};management=by_name.get('enp0s31f6',{});provider=by_name.get(usb,{})
 facts['VIP on management interface']=any(a.get('local')=='192.168.0.151' for a in management.get('addr_info',[]))
 routes=json.loads(run(['ip','-j','-4','route','show','default']))
 checks.update({'management IPv4 retained':any(a.get('local')==ip and a.get('prefixlen')==24 for a in management.get('addr_info',[])),
  'management default route retained':any(r.get('dev')=='enp0s31f6' and r.get('gateway')=='192.168.0.1' for r in routes),
  'USB MAC retained':provider.get('address','').lower()==mac,
  'USB has no host addresses':bool(provider) and provider.get('addr_info')==[],
  'USB link':{'UP','LOWER_UP'}.issubset(provider.get('flags',[])),
  'Chrony active':run(['systemctl','is-active','chrony'])=='active',
  'clock synchronized':run(['timedatectl','show','-p','NTPSynchronized','--value'])=='yes'})
 # Wait only for existing service healthchecks, never restart a service.
 deadline=time.monotonic()+120
 while True:
  states=container_states()
  if healthy(states,node) or time.monotonic()>=deadline:break
  time.sleep(5)
 checks['selected core containers running and healthchecks passed']=healthy(states,node)
 facts['Neutron HA configuration'],checks['Neutron router HA configured']=ha_configuration()
 facts['galera'],checks['MariaDB three-member Primary Synced cluster']=galera_status(ip)
 facts['containers']=states
 facts['required containers']=list(required_containers(node))
 facts['missing required containers']=sorted(set(required_containers(node))-set(states))
 # Query only OVS bridge membership, not the management NIC configuration.
 bridge=run(['docker','exec','openvswitch_vswitchd','ovs-vsctl','port-to-br',usb])
 checks['USB attached to br-ex']=bridge=='br-ex'
 checks['management NIC not attached to br-ex']='enp0s31f6' not in run(['docker','exec','openvswitch_vswitchd','ovs-vsctl','list-ports','br-ex']).splitlines()
 protected=[*Path('/etc/netplan').glob('*.yaml'),Path('/etc/chrony/chrony.conf'),*Path('/etc/chrony').rglob('*.sources'),*Path('/etc/chrony').rglob('*.conf'),Path('/etc/ssh/sshd_config'),*Path('/etc/ssh/sshd_config.d').glob('*.conf')]
 facts.update({'USB bridge':bridge,
  'protected_config_hashes':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(protected)) if p.is_file()},
  'management_DNS':run(['resolvectl','dns','enp0s31f6']),'chrony_active':run(['systemctl','is-active','chrony']),
  'image references':run(['docker','inspect','--format','{{.Name}} {{.Config.Image}} {{.Image}}',*states]).splitlines() if states else [],
  'Cinder VGs':json.loads(run(['vgs','--reportformat','json','-o','vg_name,pv_count,lv_count']))['report'][0]['vg']})
 result['ready']=all(checks.values())
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--node',required=True,choices=NODES);args=p.parse_args()
 try:result=collect(args.node)
 except Exception as e:result={'node':args.node,'ready':False,'error':str(e) if isinstance(e,RuntimeError) else type(e).__name__}
 print(json.dumps(result))
if __name__=='__main__':main()

#!/usr/bin/env python3
"""Read-only post-reboot host, container, Galera and broker observations."""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import uuid

NODES = {'node1':('192.168.0.200','enx00e04c557e58'),
         'node2':('192.168.0.201','enx00e04c2c1678'),
         'node3':('192.168.0.202','enx00e04c304688')}
REQUIRED = ('mariadb','proxysql','rabbitmq','haproxy','keepalived','keystone_api',
 'keystone_fernet','keystone_ssh','placement_api','nova_api','nova_metadata',
 'nova_scheduler','nova_conductor','nova_compute','nova_libvirt','neutron_server','neutron_rpc_server','neutron_periodic_worker',
 'neutron_openvswitch_agent','neutron_l3_agent','neutron_dhcp_agent','neutron_metadata_agent',
 'openvswitch_db','openvswitch_vswitchd','cinder_api','cinder_scheduler','cinder_volume','horizon')
WSREP = ('wsrep_local_state_comment','wsrep_cluster_size','wsrep_cluster_state_uuid',
         'wsrep_cluster_status','wsrep_connected','wsrep_ready')


def run(args, timeout=20):
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        if args[:2]==['docker','exec'] and '--defaults-file=/etc/mysql/healthcheck.cnf' in args:
            error=p.stderr.lower()
            if 'is not running' in error:reason='database container not running'
            elif '1045' in error or 'access denied' in error:reason='database query authentication rejected'
            elif '1047' in error or 'wsrep has not yet prepared' in error:reason='database not ready for queries'
            elif '2002' in error or '2003' in error:reason='database connection unavailable'
            else:reason='database status query failed'
            raise RuntimeError(reason+' (exit '+str(p.returncode)+')')
        raise RuntimeError(args[0]+' read query exited '+str(p.returncode))
    return p.stdout.strip()


def query(function):
    try: return {'observed':True,'value':function()}
    except (OSError,ValueError,KeyError,TypeError,IndexError,RuntimeError,subprocess.TimeoutExpired) as error:
        return {'observed':False,'error':str(error) if isinstance(error,RuntimeError) else type(error).__name__+'; read unavailable'}


def containers(node):
    required = REQUIRED + (('glance_api',) if node == 'node1' else ())
    names = set(run(['docker','ps','-a','--format','{{.Names}}']).splitlines())
    found = [n for n in required if n in names]
    fmt = '{"name":{{json .Name}},"running":{{json .State.Running}},"status":{{json .State.Status}},"exit_code":{{json .State.ExitCode}},"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}"not configured"{{end}}}'
    rows = [json.loads(line) for line in run(['docker','inspect','--format',fmt,*found]).splitlines()] if found else []
    if len(rows) != len(found): raise RuntimeError('Incomplete container observation')
    values = {r['name'].lstrip('/'):r for r in rows}
    if set(values) != set(found): raise RuntimeError('Container identity mismatch')
    bad = [n for n in found if values[n]['running'] is not True or values[n]['health'] not in ('healthy','not configured')]
    return {'containers':values,'missing':sorted(set(required)-set(found)),'unhealthy':bad,
            'healthy':not bad and set(required).issubset(values)}


def galera(ip):
    sql = 'SHOW GLOBAL STATUS WHERE Variable_name IN ('+','.join("'"+k+"'" for k in WSREP)+')'
    raw = run(['docker','exec','--user','root','mariadb','mariadb',
      '--defaults-file=/etc/mysql/healthcheck.cnf','--protocol=tcp','--host='+ip,
      '--port=3306','--connect-timeout=5','--batch','--skip-column-names','--execute='+sql])
    values = {}
    for line in raw.splitlines():
        pair = line.split('\t')
        if len(pair)!=2 or pair[0] not in WSREP or pair[0] in values: raise RuntimeError('Unreviewed Galera response')
        values[pair[0]] = pair[1]
    if set(values)!=set(WSREP): raise RuntimeError('Incomplete Galera status')
    return values


def broker():
    checks = {name:query(lambda n=name:run(['docker','exec','rabbitmq','rabbitmq-diagnostics','-q',n]))
              for name in ('check_running','check_local_alarms')}
    cluster = query(lambda:json.loads(run(['docker','exec','rabbitmq','rabbitmqctl','cluster_status','--formatter','json'])))
    selected = {}
    if cluster['observed']:
        value = cluster['value']
        selected = {k:value.get(k) for k in ('disk_nodes','running_nodes','partitions','alarms')}
    return {'checks':{k:v['observed'] for k,v in checks.items()},
            'cluster_observed':cluster['observed'],'cluster':selected,
            'healthy':all(c['observed'] for c in checks.values()) and cluster['observed']
              and set(selected.get('running_nodes') or [])=={'rabbit@'+n for n in NODES}
              and set(selected.get('disk_nodes') or [])=={'rabbit@'+n for n in NODES}
              and selected.get('partitions') in ({},[]) and selected.get('alarms')==[]}


def recent_galera_view():
    raw=run(['docker','exec','--user','root','mariadb','tail','-n','60','/var/log/kolla/mariadb/mariadb.log'])
    result={}
    for line in raw.splitlines():
        match=re.search(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d).*WSREP: view\(view_id\((NON_PRIM|PRIM),',line)
        if match:result={'log_time':match[1],'view':match[2],'scope':'recent log entry, not a live SQL result'}
    return result


def network(node, addresses):
    ip,usb = NODES[node]
    by_name = {a['ifname']:a for a in addresses}; provider = by_name.get(usb,{})
    bridge = query(lambda:run(['docker','exec','openvswitch_vswitchd','ovs-vsctl','port-to-br',usb]))
    ports = query(lambda:run(['docker','exec','openvswitch_vswitchd','ovs-vsctl','list-ports','br-ex']).splitlines())
    checks = {'USB_present':bool(provider),'USB_link_up':{'UP','LOWER_UP'}.issubset(provider.get('flags',[])),
      'USB_unnumbered':bool(provider) and provider.get('addr_info')==[],
      'USB_in_br_ex':bridge.get('value')=='br-ex',
      'management_not_in_br_ex':ports['observed'] and 'enp0s31f6' not in ports['value']}
    return {'USB':usb,'checks':checks,'healthy':all(checks.values())}


def collect(node):
    if node not in NODES or os.geteuid()!=0 or os.environ.get('SUDO_USER')!='joe' or socket.gethostname().split('.')[0]!=node:
        raise RuntimeError('Expected node identity and sudo from joe')
    addresses = json.loads(run(['ip','-j','address','show']))
    management = [a for a in addresses if a.get('ifname')=='enp0s31f6']
    if len(management)!=1 or not any(a.get('local')==NODES[node][0] and a.get('prefixlen')==24 for a in management[0].get('addr_info',[])):
        raise RuntimeError('Management address does not match node')
    result = {'node':node,'observed':True,
      'vip_interfaces':[a['ifname'] for a in addresses if any(v.get('local')=='192.168.0.151' for v in a.get('addr_info',[]))]}
    result['host'] = query(lambda:{'boot_id':str(uuid.UUID(Path('/proc/sys/kernel/random/boot_id').read_text().strip())),
      'uptime_seconds':float(Path('/proc/uptime').read_text().split()[0]),
      'MemAvailable_kib':int(re.search(r'^MemAvailable:\s+(\d+)',Path('/proc/meminfo').read_text(),re.M)[1])})
    result['clock'] = query(lambda:run(['timedatectl','show','-p','NTPSynchronized','--value'])=='yes')
    result['containers'] = query(lambda:containers(node))
    # Independent queries: a stopped database must not hide the other checks.
    result['galera'] = query(lambda:galera(NODES[node][0]))
    state=result.get('containers',{}).get('value',{}).get('containers',{}).get('mariadb',{})
    if not result['galera']['observed'] and state.get('running') is True:
        result['galera_log']=query(recent_galera_view)
    result['rabbitmq'] = query(broker)
    result['provider_network'] = query(lambda:network(node,addresses))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--node',required=True,choices=NODES)
    args=parser.parse_args()
    try:result=collect(args.node)
    except (Exception,KeyboardInterrupt) as error:
        result={'node':args.node,'observed':False,'error':str(error) if isinstance(error,RuntimeError) else type(error).__name__+'; host observation unavailable'}
    print(json.dumps(result));return 0 if result['observed'] else 1


if __name__=='__main__':raise SystemExit(main())

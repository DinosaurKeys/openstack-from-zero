#!/usr/bin/env python3
"""Read-only reboot dashboard for services and agents, with optional workload checks."""
import sys
sys.dont_write_bytecode=True
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor,as_completed
import datetime as dt
import fcntl
import getpass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

REPO=Path(__file__).resolve().parents[1]
def load(name,file):
    spec=importlib.util.spec_from_file_location(name,REPO/'scripts'/file)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
health=load('reboot_health','lab-health.py');move=load('reboot_migration','phase5-move-vm.py')
inspection=move.inspection;volume=inspection.volume;boot=volume.boot;prep=volume.prep
deploy=health.deploy;inv=health.inv;ENV=health.ENV


def cell(value):
    return re.sub(r'[\x00-\x1f\x7f]', '?', str(value if value is not None else '-'))[:120]


def table(title,headers,rows):
    values=[[cell(v) for v in headers]]+[[cell(v) for v in row] for row in rows]
    widths=[max(len(row[i]) for row in values) for i in range(len(headers))]
    return ['',title]+[' | '.join(value.ljust(widths[i]) for i,value in enumerate(row)).rstrip() for row in values]


def cluster_result(details):
    rows=[r for r in details.get('cluster_summary',[]) if r.get('check') not in ('existing VM/volume API state','exercise checkpoints')]
    if not rows:return 'UNKNOWN'
    return 'HEALTHY' if all(r.get('status')=='PASS' for r in rows) else 'NEEDS REVIEW'


def reported(value,key,node,identify='binary'):
    if not isinstance(value,list):return None
    found=[r for r in value if r.get(identify)==key and str(r.get('host','')).split('@')[0].split('.')[0]==node]
    if not found:return 'NOT LISTED'
    return found[0] if len(found)==1 else 'DUPLICATE'


def service_cell(value):
    if not isinstance(value,dict):return value or 'UNKNOWN'
    state=value.get('state');status=value.get('status')
    return (str(state).upper() if state in ('up','down') else 'UNKNOWN')+'/'+(status if status in ('enabled','disabled') else 'unknown')


def safe_reason(value):
    """Only selected diagnostic classes; never echo raw errors or credentials."""
    raw=str(value or '')
    http=re.search(r'\bHTTP [1-5][0-9]{2}\b',raw)
    if http:return http[0]
    for label in ('TLS certificate verification failed','Connection timed out','Private client input file missing',
      'database container not running','database query authentication rejected','database not ready for queries',
      'database connection unavailable','database status query failed','SSH key authentication rejected',
      'SSH host identity needs review','Sudo authentication failed','Sudo requires authentication',
      'SSH connection refused','Management network route unavailable'):
        if label in raw:return label
    match=re.match(r'(?:docker read query exited |.*?\(exit )(\d+)',raw)
    if match:return 'read command exited '+match[1]
    match=re.match(r'([A-Za-z]+(?:Error|Expired|Exception))\b',raw)
    return (match[1]+'; read unavailable') if match else 'check unavailable; see private JSON details'


def render_brief(details):
    """Grouped PASS/FAIL/BLOCKED display from the same read-only observations."""
    hosts=details.get('hosts',{});nodes=tuple(inv.NODES)
    statuses={r['check']:r.get('status') for r in details.get('cluster_summary',[])}
    counts={'PASS':0,'FAIL':0,'BLOCKED':0};lines=['OPENSTACK LAB STATUS — R2d2']
    def section(title):lines.extend(['','== '+title+' =='])
    def emit(status,label,notes=()):
        counts[status]+=1;lines.append(status.ljust(7)+' '+label)
        lines.extend('        '+cell(note) for note in notes)
    def status_of(key):return {'PASS':'PASS','REVIEW':'FAIL'}.get(statuses.get(key),'BLOCKED')
    def host_value(node,key):return hosts.get(node,{}).get(key,{})
    def state(node,name):
        record=host_value(node,'containers')
        if record.get('observed') is not True:return 'BLOCKED','container observation unavailable'
        value=record.get('value',{})
        if name in value.get('missing',[]):return 'FAIL','MISSING'
        item=value.get('containers',{}).get(name)
        if not isinstance(item,dict) or type(item.get('running')) is not bool:return 'BLOCKED','container state unavailable'
        health_state=item.get('health')
        if not item['running']:
            code=item.get('exit_code');return 'FAIL',(str(item.get('status') or 'stopped').upper()+((' exit='+str(code)) if isinstance(code,int) else ''))
        if health_state in ('healthy','not configured'):return 'PASS','running/'+('no-check' if health_state=='not configured' else 'healthy')
        if health_state in ('starting','unhealthy'):return 'FAIL','running/'+health_state
        return 'BLOCKED','running; health unavailable'
    section('HOSTS')
    for node in nodes:
        outcome=status_of(node+' management SSH/identity')
        emit(outcome,node+' management SSH/identity'+(' OK' if outcome=='PASS' else ': '+safe_reason(hosts.get(node,{}).get('error'))))
    for suffix in ('clock sync','USB/br-ex'):
        good=sum(statuses.get(node+' '+suffix)=='PASS' for node in nodes)
        outcome='PASS' if good==3 else 'FAIL' if any(statuses.get(node+' '+suffix)=='REVIEW' for node in nodes) else 'BLOCKED'
        emit(outcome,suffix+': '+str(good)+'/3 passed',[node+': check '+('failed' if statuses.get(node+' '+suffix)=='REVIEW' else 'unavailable') for node in nodes if statuses.get(node+' '+suffix)!='PASS'])
    section('CORE CONTAINERS')
    groups=(('MariaDB',('mariadb',)),('ProxySQL',('proxysql',)),('RabbitMQ',('rabbitmq',)),
      ('API front door',('haproxy','keepalived')),('Identity',('keystone_api','keystone_fernet','keystone_ssh')),
      ('Glance',('glance_api',)),('Placement',('placement_api',)),
      ('Nova control',('nova_api','nova_metadata','nova_scheduler','nova_conductor')),
      ('Nova compute/libvirt',('nova_compute','nova_libvirt')),
      ('Neutron control',('neutron_server','neutron_rpc_server','neutron_periodic_worker')),
      ('Neutron agent containers',('neutron_openvswitch_agent','neutron_dhcp_agent','neutron_l3_agent','neutron_metadata_agent')),
      ('Open vSwitch',('openvswitch_db','openvswitch_vswitchd')),
      ('Cinder',('cinder_api','cinder_scheduler','cinder_volume')),('Horizon',('horizon',)))
    for label,names in groups:
        checks=[(node,name,*state(node,name)) for node in nodes for name in names if name!='glance_api' or node=='node1']
        good=sum(row[2]=='PASS' for row in checks)
        outcome='FAIL' if any(row[2]=='FAIL' for row in checks) else 'BLOCKED' if good<len(checks) else 'PASS'
        notes=[]
        for node in nodes:
            bad=[name+'='+value for host,name,status,value in checks if host==node and status!='PASS']
            if bad:notes.append(node+': '+'; '.join(bad))
        # Keep node/group lines readable without silently cutting off a failure.
        emit(outcome,label+': '+str(good)+'/'+str(len(checks))+' ready'+(' (node1 only)' if label=='Glance' else ''))
        for note in notes:lines.append('        '+re.sub(r'[\x00-\x1f\x7f]','?',note))
    lines.append('        Ready = running and healthy where configured; no-check is process-only. Starting means not ready yet.')
    section('DATABASE AND MESSAGING CLUSTERS')
    for node in nodes:
        query=host_value(node,'galera');value=query.get('value',{})
        if query.get('observed'):
            ok=health.member_ok(value)
            emit('PASS' if ok else 'FAIL',node+' Galera: '+cell(value.get('wsrep_local_state_comment'))+'/'+cell(value.get('wsrep_cluster_status'))+
              ', members='+cell(value.get('wsrep_cluster_size'))+', connected='+cell(value.get('wsrep_connected'))+', ready='+cell(value.get('wsrep_ready')))
        else:
            emit('BLOCKED',node+' Galera SQL status: '+safe_reason(query.get('error')))
            log_view=host_value(node,'galera_log').get('value',{})
            if log_view.get('view') in ('PRIM','NON_PRIM'):
                lines.append('        Recent log: '+log_view['view']+' at '+cell(log_view.get('log_time'))+' (log entry, not live SQL proof)')
    emit(status_of('Galera: same three-member Primary cluster'),'Galera: same synchronized three-member Primary cluster')
    for node in nodes:
        observed=host_value(node,'rabbitmq');value=observed.get('value',{});cluster=value.get('cluster',{})
        if not observed.get('observed'):emit('BLOCKED',node+' RabbitMQ cluster: '+safe_reason(observed.get('error')));continue
        members=cluster.get('running_nodes');alarms=cluster.get('alarms');partitions=cluster.get('partitions')
        emit('PASS' if value.get('healthy') is True else 'FAIL',node+' RabbitMQ: running members='+str(len(members) if isinstance(members,list) else 'not obtained')+
          ', alarms='+('none' if alarms==[] else 'present' if alarms else 'not obtained')+', partitions='+('none' if partitions in ({},[]) else 'present' if partitions else 'not obtained'))
    owners=details.get('API VIP: exactly one owner',{}).get('owners',[])
    emit(status_of('API VIP: exactly one owner'),'API VIP 192.168.0.151: '+(', '.join(cell(n) for n in owners) if owners else 'owner not obtained'))
    section('HTTPS API CHECKS')
    authenticated=statuses.get('HTTPS authentication')=='PASS'
    emit(status_of('HTTPS authentication'),'Keystone authentication'+(' works' if authenticated else ': '+safe_reason(details.get('HTTPS authentication',{}).get('error'))))
    def api_status(label,title):
        if not authenticated:return
        outcome=status_of(label);value=details.get(label,{})
        note='' if outcome=='PASS' else ': '+safe_reason(value.get('error')) if isinstance(value,dict) and value.get('error') else ': observed check did not pass'
        emit(outcome,title+note)
    for label,title in [('Horizon HTTPS','Horizon'),('image HTTPS API read','Images'),('compute HTTPS API read','Compute'),
      ('network HTTPS API read','Network'),('block-storage HTTPS API read','Volumes'),('placement HTTPS API read','Placement')]:api_status(label,title)
    if not authenticated:
        emit('BLOCKED','HTTPS reads not queried: authentication/client preparation unavailable',
          ['Horizon, Images, Compute, Network, Volumes, Placement'])
    section('OPENSTACK SERVICES')
    if not authenticated:
        emit('BLOCKED','Service/agent checks not queried: authentication/client preparation unavailable',
          ['Nova services, Cinder services, Hypervisors, Neutron agent heartbeats, Router HA placement'])
    for key,binaries in [('Nova services',('nova-scheduler','nova-conductor','nova-compute')),('Cinder services',('cinder-scheduler','cinder-volume'))]:
        values=details.get(key)
        if not authenticated or not isinstance(values,list):api_status(key,key);continue
        good=0;notes=[]
        for binary in binaries:
            for node in nodes:
                entry=reported(values,binary,node);display=service_cell(entry)
                if display=='UP/enabled':good+=1
                else:notes.append(node+'/'+binary+'='+display)
        outcome='PASS' if good==len(binaries)*3 and statuses.get(key)=='PASS' else 'FAIL'
        if good==len(binaries)*3 and outcome=='FAIL':notes.append('API registration set needs review; expected exactly one service of each type per node')
        emit(outcome,key+': '+str(good)+'/'+str(len(binaries)*3)+' enabled/up',notes)
    values=details.get('hypervisors')
    if authenticated and isinstance(values,list):
        notes=[];good=0
        for node in nodes:
            matches=[v for v in values if str(v.get('hypervisor_hostname','')).split('.')[0]==node]
            entry=matches[0] if len(matches)==1 else 'DUPLICATE' if matches else 'NOT LISTED'
            display=service_cell(entry)
            if display=='UP/enabled':good+=1
            else:notes.append(node+'='+display)
        if good==3 and statuses.get('hypervisors')!='PASS':notes.append('API hypervisor registration set needs review')
        emit('PASS' if good==3 and statuses.get('hypervisors')=='PASS' else 'FAIL','Hypervisors: '+str(good)+'/3 enabled/up',notes)
    else:api_status('hypervisors','Hypervisors')
    if authenticated:section('NEUTRON AGENTS')
    agents=details.get('Neutron agents')
    if authenticated and isinstance(agents,list):
        notes=[];good=0
        for binary in ('neutron-dhcp-agent','neutron-l3-agent','neutron-openvswitch-agent','neutron-metadata-agent'):
            for node in nodes:
                entry=reported(agents,binary,node)
                if isinstance(entry,dict) and entry.get('alive') is True and entry.get('admin_state_up') is True:good+=1
                else:notes.append(node+'/'+binary+'='+('alive='+str(entry.get('alive'))+', enabled='+str(entry.get('admin_state_up')) if isinstance(entry,dict) else str(entry)))
        if good==12 and statuses.get('Neutron agents')!='PASS':notes.append('API agent registration set needs review; expected exactly 12')
        emit('PASS' if good==12 and statuses.get('Neutron agents')=='PASS' else 'FAIL','Neutron agents: '+str(good)+'/12 alive/enabled',notes)
    else:api_status('Neutron agents','Neutron agent heartbeats')
    api_status('router HA (API state)','Router HA API placement')
    router=details.get('router HA (API state)',{})
    if authenticated and isinstance(router,dict) and isinstance(router.get('agents'),list):
        for value in router['agents']:lines.append('        '+cell(value.get('host'))+'='+cell(value.get('ha_state')))
    preserved=next((r.get('status') for r in details.get('summary',[]) if r.get('check')=='Existing checkpoints/config preserved'),None)
    section('RESULT')
    emit({'PASS':'PASS','REVIEW':'FAIL'}.get(preserved,'BLOCKED'),'Saved checkpoints/config preserved')
    passed=cluster_result(details)=='HEALTHY' and preserved=='PASS' and not counts['FAIL'] and not counts['BLOCKED']
    lines.append('PASS checks: '+str(counts['PASS'])+' | FAIL checks: '+str(counts['FAIL'])+' | BLOCKED checks: '+str(counts['BLOCKED']))
    lines.append('FAIL = observed check not passing; BLOCKED = could not verify, not proof the service is down.')
    lines.append('RESULT: '+('REBOOT_SERVICES_HEALTHY' if passed else 'REBOOT_SERVICES_NEED_REVIEW'))
    if not passed:
        database_bad=any(not host_value(n,'galera').get('observed') or not health.member_ok(host_value(n,'galera').get('value',{})) for n in nodes)
        lines.append('NEXT: '+('Review MariaDB status/log evidence first; do not select API restarts yet.' if database_bad else 'Review the named failed/blocked checks before selecting a repair.'))
    else:lines.append('NEXT: No service restart indicated by this snapshot.')
    lines.append('Read-only snapshot. No repair, restart, bootstrap or VM/volume/migration action performed.')
    return lines


def render_compact(details):
    """Short operational view; retain every failed/blocked result and its details."""
    full=render_brief(details);statuses={r['check']:r.get('status') for r in details.get('cluster_summary',[])}
    nodes=tuple(inv.NODES);lines=['OPENSTACK LAB STATUS — R2d2'];section='';db_shown=False;broker_shown=False
    host_ok=all(statuses.get(n+' management SSH/identity')=='PASS' for n in nodes)
    db_values=[details.get('hosts',{}).get(n,{}).get('galera',{}) for n in nodes]
    db_ok=(all(v.get('observed') and health.member_ok(v.get('value',{})) for v in db_values)
      and len({v.get('value',{}).get('wsrep_cluster_state_uuid') for v in db_values})==1
      and statuses.get('Galera: same three-member Primary cluster')=='PASS')
    brokers=[details.get('hosts',{}).get(n,{}).get('rabbitmq',{}) for n in nodes]
    broker_ok=all(v.get('observed') and v.get('value',{}).get('healthy') is True for v in brokers)
    api_labels=('HTTPS authentication','Horizon HTTPS','image HTTPS API read','compute HTTPS API read',
                'network HTTPS API read','block-storage HTTPS API read','placement HTTPS API read')
    api_ok=all(statuses.get(label)=='PASS' for label in api_labels)
    for line in full[1:]:
        if line.startswith('== '):
            section=line.strip('= ')
            if section=='HOSTS' and host_ok:lines.append('PASS    Nodes: 3/3 reachable with expected identities')
            if section=='DATABASE AND MESSAGING CLUSTERS' and db_ok and not db_shown:lines.append('PASS    Galera: 3/3 Synced/Primary, size 3, same cluster UUID')
            if section=='DATABASE AND MESSAGING CLUSTERS' and broker_ok and not broker_shown:lines.append('PASS    RabbitMQ cluster: 3/3 running members, no alarms or partitions')
            if section=='HTTPS API CHECKS' and api_ok:lines.append('PASS    HTTPS: login, Horizon and all five service API reads')
            continue
        if not line:continue
        if section=='HOSTS' and host_ok and ' management SSH/identity OK' in line:continue
        if section=='CORE CONTAINERS':
            if db_ok and line=='PASS    MariaDB: 3/3 ready':
                lines.append('PASS    Galera: 3/3 Synced/Primary, size 3, same cluster UUID');db_shown=True;continue
            if broker_ok and line=='PASS    RabbitMQ: 3/3 ready':
                lines.append('PASS    RabbitMQ cluster: 3/3 running members, no alarms or partitions');broker_shown=True;continue
        if section=='DATABASE AND MESSAGING CLUSTERS':
            if db_ok and (' Galera:' in line or 'Galera: same synchronized' in line):continue
            if broker_ok and ' RabbitMQ:' in line:continue
        if section=='HTTPS API CHECKS' and api_ok:continue
        if line.startswith('        Ready ='):continue
        if line.startswith(('PASS checks:','FAIL =','Read-only snapshot.')):continue
        if line.startswith('NEXT:') and 'No service restart' in line:continue
        # Keep exact failed component names and safe reasons, including indented detail.
        lines.append(line)
    lines.append('BLOCKED = could not verify; it does not mean the service is down.')
    lines.append('Cluster services only; VM and migration checks remain separate.')
    return lines


def render_report(details,include_workload=False):
    """Render collected evidence only. This function performs no probes or writes."""
    nodes=tuple(inv.NODES);hosts=details.get('hosts',{})
    cluster_status={r['check']:r.get('status','UNKNOWN') for r in details.get('cluster_summary',[])}
    rows=details.get('summary',[]);workload_status={r['check']:r.get('status','UNKNOWN') for r in rows}
    lines=['CLUSTER SERVICES: '+cluster_result(details)]
    def check_text(status):return {'PASS':'OK','REVIEW':'NEEDS REVIEW','UNKNOWN':'UNKNOWN'}.get(status,'UNKNOWN')
    lines+=table('HOST ACCESS AND NETWORK',['Node','SSH/identity','Clock','USB / br-ex'],[
      [node,*[check_text(cluster_status.get(node+' '+label)) for label in ('management SSH/identity','clock sync','USB/br-ex')]] for node in nodes])
    database=[]
    for node in nodes:
        value=hosts.get(node,{}).get('galera',{})
        observed=value.get('observed') is True;data=value.get('value',{}) if observed else {}
        database.append([node,data.get('wsrep_local_state_comment','UNKNOWN'),data.get('wsrep_cluster_status','UNKNOWN'),data.get('wsrep_cluster_size','UNKNOWN'),data.get('wsrep_connected','UNKNOWN'),data.get('wsrep_ready','UNKNOWN')])
    lines+=table('MARIADB / GALERA',['Node','State','Component','Members','Connected','Ready'],database)
    lines.append('Same three-member cluster: '+check_text(cluster_status.get('Galera: same three-member Primary cluster')))
    names=('mariadb','proxysql','rabbitmq','haproxy','keepalived','keystone_api','keystone_fernet','keystone_ssh','glance_api',
      'placement_api','nova_api','nova_metadata','nova_scheduler','nova_conductor','nova_compute','nova_libvirt',
      'neutron_server','neutron_rpc_server','neutron_periodic_worker','neutron_openvswitch_agent','neutron_l3_agent','neutron_dhcp_agent','neutron_metadata_agent',
      'openvswitch_db','openvswitch_vswitchd','cinder_api','cinder_scheduler','cinder_volume','horizon')
    def container_state(node,name):
        if name=='glance_api' and node!='node1':return 'N/A (placement)'
        record=hosts.get(node,{})
        value=record.get('containers',{})
        if value.get('observed') is not True:return 'UNKNOWN'
        contents=value.get('value',{})
        if name in contents.get('missing',[]):return 'MISSING'
        state=contents.get('containers',{}).get(name)
        if not isinstance(state,dict):return 'UNKNOWN'
        running=state.get('running')
        up='UP' if running is True else ('DOWN' if running is False else 'UNKNOWN')
        health=state.get('health')
        return up+'/'+('no-check' if health=='not configured' else health if health in ('healthy','unhealthy','starting') else 'unknown')
    lines+=table('CONTAINERS — process / healthcheck',['Container',*nodes],[[name,*[container_state(node,name) for node in nodes]] for name in names])
    rabbit=[]
    for node in nodes:
        value=hosts.get(node,{}).get('rabbitmq',{});data=value.get('value',{})
        cluster=data.get('cluster',{})
        rabbit.append([node,'OK' if data.get('checks',{}).get('check_running') is True else 'UNKNOWN/FAIL',
          len(cluster['running_nodes']) if isinstance(cluster.get('running_nodes'),list) else 'UNKNOWN',
          'NONE' if cluster.get('alarms')==[] else 'UNKNOWN/ALARM',
          'NONE' if cluster.get('partitions') in ({},[]) else 'UNKNOWN/PARTITION',
          'OK' if data.get('healthy') is True else 'NEEDS REVIEW'])
    lines+=table('RABBITMQ CLUSTER',['Node','Application','Running members','Alarms','Partitions','Result'],rabbit)
    owners=details.get('API VIP: exactly one owner',{}).get('owners',[])
    lines.append('API VIP 192.168.0.151: '+(', '.join(owners) if owners else 'owner UNKNOWN')+' | '+check_text(cluster_status.get('API VIP: exactly one owner')))
    for title,key,binaries in [('NOVA SERVICES — heartbeat / admin','Nova services',('nova-scheduler','nova-conductor','nova-compute')),
      ('CINDER SERVICES — heartbeat / admin','Cinder services',('cinder-scheduler','cinder-volume'))]:
        values=details.get(key)
        lines+=table(title,['Service',*nodes],[[binary,*[service_cell(reported(values,binary,node)) for node in nodes]] for binary in binaries])
    hypers=details.get('hypervisors');hyper_rows=[]
    for node in nodes:
        matches=[v for v in hypers if str(v.get('hypervisor_hostname','')).split('.')[0]==node] if isinstance(hypers,list) else None
        selected=matches[0] if matches and len(matches)==1 else ('DUPLICATE' if matches else 'NOT LISTED' if matches==[] else None)
        hyper_rows.append([node,service_cell(selected)])
    lines+=table('HYPERVISORS — API state / admin',['Node','State'],hyper_rows)
    def agent_cell(value):
        if not isinstance(value,dict):return value or 'UNKNOWN'
        alive=value.get('alive');enabled=value.get('admin_state_up')
        return ('UP' if alive is True else 'DOWN' if alive is False else 'UNKNOWN')+'/'+('enabled' if enabled is True else 'disabled' if enabled is False else 'unknown')
    agents=details.get('Neutron agents')
    agent_types=(('DHCP','neutron-dhcp-agent'),('Router (L3)','neutron-l3-agent'),('Open vSwitch','neutron-openvswitch-agent'),('Metadata','neutron-metadata-agent'))
    lines+=table('NEUTRON AGENTS — heartbeat / admin',['Agent',*nodes],[[label,*[agent_cell(reported(agents,binary,node)) for node in nodes]] for label,binary in agent_types])
    router=details.get('router HA (API state)',{});router_agents=router.get('agents') if isinstance(router,dict) else None
    router_rows=[]
    for node in nodes:
        matches=[a for a in router_agents if str(a.get('host','')).split('.')[0]==node] if isinstance(router_agents,list) else None
        value=matches[0] if matches and len(matches)==1 else None
        router_rows.append([node,value.get('ha_state','UNKNOWN').upper() if value and isinstance(value.get('ha_state'),str) else 'UNKNOWN',agent_cell(value)])
    lines+=table('VIRTUAL ROUTER — API placement',['Node','HA role','Agent heartbeat / admin'],router_rows)
    api_rows=[]
    for label,key in [('Login','HTTPS authentication'),('Horizon','Horizon HTTPS'),('Images','image HTTPS API read'),('Compute','compute HTTPS API read'),('Network','network HTTPS API read'),('Volumes','block-storage HTTPS API read'),('Placement','placement HTTPS API read')]:
        result=check_text(cluster_status.get(key));detail=details.get(key,{})
        if result=='UNKNOWN' and isinstance(detail,dict) and str(detail.get('error','')).startswith('HTTP '):result=str(detail['error']).split(';')[0]
        api_rows.append([label,result])
    lines+=table('HTTPS API READS',['API','Result'],api_rows)
    lines.append('CLUSTER RESULT: '+cluster_result(details))
    lines.append('Container UP = process running; service/agent UP = reported heartbeat. UNKNOWN is not proof of DOWN.')
    lines.append('no-check = no container healthcheck configured; N/A (placement) = Glance runs on node1 only.')
    if not include_workload:
        lines.append('Saved checkpoints/config: '+check_text(workload_status.get('Existing checkpoints/config preserved')))
        passed=cluster_result(details)=='HEALTHY' and workload_status.get('Existing checkpoints/config preserved')=='PASS'
        lines.append('ACTION: '+('No service restart indicated by this snapshot.' if passed else 'Review DOWN/unhealthy/UNKNOWN entries; approve exact targets before a restart.'))
        lines.append('Scope: cluster services and agents only. VM/migration checks paused; existing records preserved.')
        lines.append('RESULT: '+('REBOOT_SERVICES_HEALTHY' if passed else 'REBOOT_SERVICES_NEED_REVIEW'))
        return lines
    current=details.get('VM identity',{});physical=details.get('VM runtime',{})
    lines+=table('VM / MIGRATION — SEPARATE FROM CLUSTER',['Item','Observed state'],[
      ['Existing VM',boot.PLAN['server_name']],['Nova VM status',current.get('server_status','UNKNOWN')],['Nova assigned host',current.get('host','UNKNOWN')],
      *[['Actual domain on '+node,str(physical.get('nodes',{}).get(node,{}).get('domain_state') or 'UNKNOWN').upper()] for node in nodes],
      ['Data volume attached','YES' if current.get('volume_attached') is True else 'NO' if current.get('volume_attached') is False else 'UNKNOWN'],
      ['Data backend',current.get('backend','UNKNOWN')],
      *[[label,{'PASS':'OK','REVIEW':'NEEDS REVIEW','UNKNOWN':'NOT VERIFIED'}.get(workload_status.get(label),'NOT VERIFIED')] for label in
         ('Guest SSH/identity','Guest network/DHCP/DNS/HTTPS','Data filesystem/mount','Saved file checksum')]])
    migration=details.get('Migration record (preserved)',{});snap=migration.get('snapshot',{});item=snap.get('migration') or {}
    lines+=table('RECORDED MIGRATION',['Status','Source','Destination','Local checkpoint'],[[item.get('status','UNKNOWN'),item.get('source_compute'),item.get('dest_compute'),'PENDING (preserved)' if migration.get('pending') else 'none pending' if migration.get('present') is False else 'preserved / see result']])
    work_rows=[v for v in rows if v.get('check') not in ('Cluster services/APIs/agents','Existing checkpoints/config preserved')]
    work_ok=bool(work_rows) and all(v.get('status')=='PASS' for v in work_rows)
    lines.append('WORKLOAD RESULT: '+('VERIFIED' if work_ok else 'NEEDS REVIEW'))
    lines.append('Saved checkpoints/config: '+check_text(workload_status.get('Existing checkpoints/config preserved')))
    lines.append('ACTION SUMMARY')
    if cluster_result(details)=='HEALTHY':lines.append('Cluster services/agents passed. No service restart is indicated by this snapshot.')
    else:lines.append('Cluster has failed or unavailable checks. Review the DOWN/unhealthy/UNKNOWN entries before selecting a repair.')
    if not work_ok:lines.append('VM/migration/data are not fully verified. VM recovery is separate and requires approval.')
    if details.get('Guest data',{}).get('issue'):lines.append(cell(details['Guest data']['issue']))
    if details.get('Workload verification',{}).get('issue'):lines.append(cell(details['Workload verification']['issue']))
    passed=bool(rows) and all(row.get('status')=='PASS' for row in rows)
    lines.append('RESULT: '+('REBOOT_RECOVERY_VERIFIED' if passed else 'REBOOT_RECOVERY_NEEDS_REVIEW'))
    return lines


def saved_report(path,include_workload=False,detailed=False,compact=False):
    path=Path(path).absolute()
    if path.suffix!='.json' or not path.name.startswith('reboot-verify-') or path.resolve().parent!=(REPO/'reports').resolve():
        raise RuntimeError('Choose an existing reboot-verify JSON file directly inside this repository reports directory')
    deploy.validation.private_file(path)
    if path.stat().st_size>5_000_000:raise RuntimeError('Saved report exceeds the read limit')
    details=json.loads(path.read_text())
    if not isinstance(details,dict):raise RuntimeError('Saved report must contain an object')
    print('SAVED SNAPSHOT: '+cell(path.name)+' — display only; no new lab checks')
    rendered=render_report(details,include_workload) if detailed or include_workload else render_compact(details) if compact else render_brief(details)
    for line in rendered:print(line)
    return 0


def snapshot():
    result={}
    for folder in ('phase4','phase5'):
        for path in sorted((REPO/'private'/folder).glob('*.json')):
            deploy.validation.private_file(path)
            result[str(path.relative_to(REPO))]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def bindings(api,project,user):
    folder=REPO/'private/phase4'
    vm=health.read_private(folder/'vm.json');data=health.read_private(folder/'volume.json')
    prepared=health.read_private(folder/'vm-preparation.json')
    for state in (vm,data,prepared):
        if state.get('project_id')!=project or state.get('user_id')!=user or state.get('apis')!=api.urls:
            raise RuntimeError('Saved workload identity/project/API differs; no guest connection')
    ids=vm['ids']
    for identifier in (*ids.values(),data['volume_id'],data['filesystem_uuid']):
        if not volume.valid_uuid(identifier):raise RuntimeError('Invalid tracked resource UUID')
    if data.get('server_id')!=ids['server'] or data.get('sha256')!=volume.expected_checksum(data):raise RuntimeError('Saved data-test identity differs')
    if vm.get('pending') or data.get('pending') or data.get('stage')!='complete':raise RuntimeError('VM/volume operation remains pending; inspect before guest checks')
    if vm.get('prepared')!=prepared.get('ids'):raise RuntimeError('Prepared VM resource identities differ')
    return vm,data,prepared


def api_workload(api,vm,data,project,user):
    server=api.get('compute','servers/'+vm['ids']['server'])['server']
    if server.get('id')!=vm['ids']['server']:raise RuntimeError('Server UUID differs')
    # Check identity even if Nova reports ERROR; the actual running guest is a separate observation.
    boot.check_server(server,project,user,vm['networks'],vm['prepared'],data['volume_id'])
    port=api.get('network','v2.0/ports/'+vm['ids']['port'])['port']
    private=boot.check_port(port,project,vm['networks'],vm['prepared'],vm['ids']['server'])
    floating=api.get('network','v2.0/floatingips/'+vm['ids']['floating_ip'])['floatingip']
    address=boot.check_floating(floating,project,vm['networks'],vm['ids']['port'],private)
    disk=api.get('block-storage','volumes/'+data['volume_id'])['volume']
    backend=volume.volume_check(disk,data,project)
    attachment=volume.attachments(api,data);volume.check_attachment(attachment,data,True)
    attached=disk.get('status')=='in-use' and len(disk.get('attachments',[]))==1
    summary={'server_id':server['id'],'server_status':server.get('status'),'host':str(server.get('OS-EXT-SRV-ATTR:host','')).split('.')[0],
      'task':server.get('OS-EXT-STS:task_state'),'volume_id':disk['id'],'volume_attached':attached,'backend':backend,
      'private_ip':private,'floating_ip':address,'mac':port['mac_address']}
    return summary


def runtime(nodes,server_id):
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs={n:pool.submit(nodes.call,n,'running',{'server_id':server_id}) for n in inv.NODES}
        records={n:j.result() for n,j in jobs.items()}
    complete=all(v.get('node')==n and v.get('observed') is True and type(v.get('VM_running')) is bool for n,v in records.items())
    return {'observed':complete,'running_hosts':[n for n,v in records.items() if v.get('observed') is True and v.get('VM_running') is True],'nodes':records}


def trusted_guest(vm,prepared,address,log):
    known=REPO/'private/phase4/lab-vm-01-known-hosts';deploy.validation.private_file(known)
    fields=known.read_text().split()
    if len(fields)!=3 or fields[0]!=address or fields[1]!='ssh-ed25519' or boot.key_fingerprint(' '.join(fields[1:]))!=vm.get('host_fingerprint'):
        raise RuntimeError('Existing guest SSH trust differs; no key replacement')
    public=Path(str(prep.KEY)+'.pub')
    if public.is_symlink() or not public.is_file() or ' '.join(public.read_text().split()[:2])!=prepared.get('public_key'):
        raise RuntimeError('Existing guest key differs from preparation')
    boot.ensure_agent(prepared['public_key'],log)
    return boot.ssh_command(address,known)


def guest_read(ssh,file,expected,timeout):
    if file not in ('phase4-guest-check.py','lab-reboot-guest.py'):raise RuntimeError('Unapproved guest helper')
    payload=base64.b64encode(json.dumps(expected).encode()).decode()
    remote=shlex.join(['sudo','-n','/usr/bin/python3','-',payload])
    result=subprocess.run(ssh+[remote],input=(REPO/'scripts'/file).read_text(),capture_output=True,text=True,timeout=timeout)
    try:value=json.loads(result.stdout)
    except ValueError:raise RuntimeError('Guest SSH/sudo read unavailable; no guest change requested') from None
    if not isinstance(value,dict) or (type(value.get('passed')) is not bool and type(value.get('observed')) is not bool):raise RuntimeError('Guest helper response unavailable')
    if result.returncode and value.get('passed') is not False and value.get('observed') is not False:raise RuntimeError('Guest helper read unavailable; no repair requested')
    return value


def network_context(api,vm,identity):
    network=api.get('network','v2.0/networks/'+vm['networks']['private_network'])['network']
    ports=api.list('network','v2.0/ports?network_id='+vm['networks']['private_network'],'ports')
    ips=sorted({f['ip_address'] for p in ports if p.get('network_id')==vm['networks']['private_network'] and p.get('device_owner')=='network:dhcp'
      for f in p.get('fixed_ips',[]) if f.get('subnet_id')==vm['networks']['private_subnet']})
    if not ips:raise RuntimeError('No observed DHCP server for the tracked subnet')
    return {**identity,'gateway':prep.net.PLAN['private_gateway'],'dns':prep.net.PLAN['dns'][0],'mtu':network['mtu'],'dhcp_server_ips':ips}


def pending_migration(api,vm,project,user):
    path=REPO/'private/phase5/move-vm-state.json'
    if not path.exists() and not path.is_symlink():return {'present':False}
    state=health.read_private(path);move.validate_receipt(state)
    if state.get('server_id')!=vm['ids']['server'] or state.get('project_id')!=project or state.get('user_id')!=user or state.get('apis')!=api.urls:
        raise RuntimeError('Migration receipt identity differs; preserved')
    observer=move.API(api.client,api.urls,api.token,vm['ids']['server'],move.PLAN['destination'])
    return {'present':True,'pending':bool(state.get('pending')),'snapshot':move.migration_snapshot(observer,state),'checkpoint_changed':False}


def record(rows,details,label,function):
    health.checked(rows,details,label,function)


def workload(nodes,api,project,user,rows,details,log):
    vm,data,prepared=bindings(api,project,user)
    current=api_workload(api,vm,data,project,user);details['VM identity']=current
    rows.append({'check':'VM in Nova','status':'PASS' if current['server_status']=='ACTIVE' and current['task'] is None else 'REVIEW'})
    rows.append({'check':'Volume attachment/backend','status':'PASS' if current['volume_attached'] and current['backend']==data.get('backend') else 'REVIEW'})
    physical=runtime(nodes,vm['ids']['server']);details['VM runtime']=physical
    one=physical['observed'] and len(physical['running_hosts'])==1
    rows.append({'check':'VM actually running','status':('PASS' if one else 'REVIEW') if physical['observed'] else 'UNKNOWN'})
    def migration_check():
        value=pending_migration(api,vm,project,user)
        terminal=not value.get('present') or (value.get('snapshot',{}).get('migration') or {}).get('status') in move.TERMINAL
        return not value.get('pending') and terminal,value
    record(rows,details,'Migration record (preserved)',migration_check)
    if not one:
        details['Guest checks']={'issue':'No single verified running VM; SSH/data checks skipped. Review actual runtime before any start or recovery.'}
        for label in ('Guest SSH/identity','Guest network/DHCP/DNS/HTTPS','Data filesystem/mount','Saved file checksum'):rows.append({'check':label,'status':'UNKNOWN'})
        return
    rows.append({'check':'Nova host matches actual host','status':'PASS' if current['host']==physical['running_hosts'][0] else 'REVIEW'})
    try:ssh=trusted_guest(vm,prepared,current['floating_ip'],log)
    except Exception:
        for label in ('Guest SSH/identity','Guest network/DHCP/DNS/HTTPS','Data filesystem/mount','Saved file checksum'):rows.append({'check':label,'status':'UNKNOWN'})
        details['Guest checks']={'issue':'Saved SSH key/trust unavailable or differs; no replacement or trust bypass'};return
    # Full network probe validates the metadata UUID. Do not require Nova ACTIVE just to observe it.
    def network_check():
        value=guest_read(ssh,'phase4-guest-check.py',network_context(api,vm,current),360)
        checks=value.get('checks',{})
        return value.get('passed') is True and bool(checks) and all(v is True for v in checks.values()) and checks.get('metadata_matches_Nova_instance') is True,value
    record(rows,details,'Guest network/DHCP/DNS/HTTPS',network_check)
    network=details.get('Guest network/DHCP/DNS/HTTPS',{})
    verified=network.get('checks',{}).get('metadata_matches_Nova_instance') is True
    rows.append({'check':'Guest SSH/identity','status':'PASS' if verified else 'UNKNOWN'})
    expected={k:data[k] for k in ('server_id','volume_id','filesystem_uuid','nonce','sha256')}
    try:
        result=guest_read(ssh,'lab-reboot-guest.py',expected,90);details['Guest data']=result
        disk_ok=result.get('observed') is True and result.get('disk_identified') is True and result.get('filesystem_matches') is True and result.get('mounted') is True
        rows.append({'check':'Data filesystem/mount','status':'PASS' if disk_ok else ('REVIEW' if result.get('observed') else 'UNKNOWN')})
        checksum=result.get('checksum_matches')
        rows.append({'check':'Saved file checksum','status':'PASS' if checksum is True else ('REVIEW' if checksum is False else 'UNKNOWN')})
    except Exception:
        details['Guest data']={'issue':'Guest data read unavailable; no mount, format or repair attempted'}
        rows.extend({'check':label,'status':'UNKNOWN'} for label in ('Data filesystem/mount','Saved file checksum'))


def execute(log,details,include_workload=False,detailed=False,compact=False):
    deploy.source_guard();env=deploy.tools.tool_environment()
    inventory,config=deploy.validation.local_checks(lambda _:None,env,deploy.validation.CA_FINGERPRINT)
    before=snapshot();input_hashes=health.access.input_hashes(config,inventory);handles=[];passwords={}
    try:
        for folder,name in [('phase5',n) for n in ('api-recovery.lock','api-vip.lock','mariadb.lock','router.lock','migration.lock')]+[('phase4',n) for n in ('vm.lock','volume.lock','networks.lock')]:
            path=REPO/'private'/folder/name
            if path.is_symlink():raise RuntimeError('Unsafe exercise lock')
            handle=path.open('a');handles.append(handle);os.chmod(path,0o600)
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('Another lab/recovery action is running; no verification during changes') from None
        inv.ensure_agent(env,log)
        for node in inv.NODES:passwords[node]=getpass.getpass('Joe sudo password on '+node+' (hidden): ')
        log('1/3 Checking all three hosts and OpenStack services…' if include_workload else '1/2 Checking all three hosts, APIs, services and agents…')
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs={pool.submit(health.host_observe,n,passwords[n],env):n for n in inv.NODES};hosts={}
            for job in as_completed(jobs):
                node=jobs[job];hosts[node]=job.result()
                log('Collected '+node+': '+('host checks available' if hosts[node].get('observed') else safe_reason(hosts[node].get('error'))))
        details['hosts']=hosts;cluster_rows=[]
        log('Checking HTTPS authentication, APIs and service/agent heartbeats…')
        health.host_rows(hosts,cluster_rows,details);health.api_checks(cluster_rows,details,env,include_workload=include_workload)
        if include_workload:health.checkpoint_checks(cluster_rows,details)
        service_rows=[r for r in cluster_rows if r['check'] not in ('existing VM/volume API state','exercise checkpoints')]
        services_ok=all(r['status']=='PASS' for r in service_rows);details['cluster_summary']=cluster_rows
        rows=[{'check':'Cluster services/APIs/agents','status':'PASS' if services_ok else 'REVIEW'}]
        if include_workload:
            log('2/3 Checking the existing VM, actual hypervisor and migration record…')
            try:
                original,project,user=prep.authenticate(config)
                api=volume.ReadOnlyAPI(original.client,original.urls,original.token)
                workload(inspection.Nodes(env,passwords),api,project,user,rows,details,log)
            except Exception as error:
                rows.append({'check':'VM/network/data verification','status':'UNKNOWN'})
                details['Workload verification']={'issue':move.safe_message(str(error)) if isinstance(error,RuntimeError) else type(error).__name__+'; tracked identity/API unavailable; no repair attempted'}
        log('3/3 Confirming saved checkpoints and deployment inputs are unchanged…' if include_workload else '2/2 Confirming saved checkpoints and deployment inputs are unchanged…')
        unchanged=snapshot()==before and health.access.input_hashes(config,inventory)==input_hashes
        rows.append({'check':'Existing checkpoints/config preserved','status':'PASS' if unchanged else 'REVIEW'})
        if include_workload:
            checkpoint_status=next(r['status'] for r in cluster_rows if r['check']=='exercise checkpoints')
            rows.append({'check':'Outstanding exercise checkpoints','status':checkpoint_status})
        details['summary']=rows
        rendered=render_report(details,include_workload) if detailed or include_workload else render_compact(details) if compact else render_brief(details)
        for line in rendered:log(line)
        if not include_workload:return 0 if services_ok and unchanged and 'RESULT: REBOOT_SERVICES_HEALTHY' in rendered else 1
        log('Workload notes:')
        attention=[]
        current=details.get('VM identity',{})
        if current and (current['server_status']!='ACTIVE' or current['task'] is not None):attention.append('Nova VM status='+str(current['server_status'])+', task='+str(current['task'])+'. Actual runtime and guest checks are reported separately; no state reset.')
        for key in ('Guest checks','Guest data','Workload verification'):
            value=details.get(key,{})
            if value.get('issue'):attention.append(value['issue'])
            elif value.get('error'):attention.append(key+': '+str(move.safe_message(value['error'])))
        guest_network=details.get('Guest network/DHCP/DNS/HTTPS',{})
        failed=[k for k,v in guest_network.get('checks',{}).items() if v is not True]
        if failed:attention.append('Guest network checks needing review: '+', '.join(failed))
        if guest_network.get('error'):attention.append('Guest network read: '+str(move.safe_message(guest_network['error'])))
        migration=details.get('Migration record (preserved)',{})
        if migration.get('pending'):attention.append('Earlier migration remains pending locally. The recorded state is shown above; reconciliation requires separate approval.')
        if any(r['status']!='PASS' for r in rows) and not attention:attention.append('Review the non-PASS rows. No automatic repair was attempted.')
        for line in attention or ['None in these verification checks.']:log(line)
        details['summary']=rows;passed=all(r['status']=='PASS' for r in rows)
        log('Read-only verification. No restart, VM start/reset/migration, mount, format, attachment change or checkpoint clearing. Browser sign-in/failover/storage HA are not tested.')
        return 0 if passed else 1
    finally:
        passwords.clear()
        for handle in reversed(handles):handle.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);group=parser.add_mutually_exclusive_group()
    group.add_argument('--check',action='store_true',help='live cluster service/agent dashboard')
    group.add_argument('--report',type=Path,help='display an existing reboot-verify JSON snapshot without live checks')
    parser.add_argument('--with-workload',action='store_true',help='also inspect VM/data/migration; separate from cluster service health')
    display=parser.add_mutually_exclusive_group()
    display.add_argument('--details',action='store_true',help='show the full by-node tables instead of the grouped summary')
    display.add_argument('--compact',action='store_true',help='short status display with failed components still named')
    args=parser.parse_args(argv)
    if not args.check and args.report is None:
        if args.with_workload:parser.error('--with-workload requires --check or --report')
        print('PLAN ONLY: services and agents by node. Use --check on R2d2, or --report with an existing private JSON snapshot. No repair mode.');return 0
    deploy.validation.config_step.load_step('reboot_r2d2','phase2-check.py').require_r2d2()
    if args.report is not None:return saved_report(args.report,args.with_workload,args.details,args.compact)
    deploy.tools.check_paths(ENV)
    if not sys.stdin.isatty():raise RuntimeError('Use joe\'s interactive R2d2 terminal')
    if Path(sys.prefix)!=ENV:os.execve(str(ENV/'bin/python'),[str(ENV/'bin/python'),str(Path(__file__).resolve()),'--check']+(['--with-workload'] if args.with_workload else [])+(['--details'] if args.details else [])+(['--compact'] if args.compact else []),deploy.tools.tool_environment())
    os.umask(0o077);reports=REPO/'reports';inv.private_dir(reports);details={}
    fd,name=tempfile.mkstemp(prefix='reboot-verify-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),suffix='.txt',dir=reports)
    print('Private summary: '+name,flush=True);status=1
    with os.fdopen(fd,'w') as output:
        def log(line):print(line,flush=True);output.write(line+'\n');output.flush()
        log('Reboot service/agent dashboard — existing lab only' if not args.with_workload else 'Reboot service dashboard with separate workload verification')
        try:status=execute(log,details,args.with_workload,args.details,args.compact)
        except (Exception,KeyboardInterrupt) as error:
            log('RESULT: REBOOT_RECOVERY_NEEDS_REVIEW');log(str(error) if isinstance(error,RuntimeError) else type(error).__name__+'; collection incomplete')
        with Path(name).with_suffix('.json').open('x') as extra:json.dump(details,extra,indent=2)
    print('Return this short summary: cat '+shlex.quote(name));return status


if __name__=='__main__':
    try:sys.exit(main())
    except (RuntimeError,OSError,ValueError,TypeError,KeyError) as error:
        print('Stopped: '+type(error).__name__+'; local inputs unavailable; no repair requested',file=sys.stderr);sys.exit(1)

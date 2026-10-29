#!/usr/bin/env python3
"""Collect migration evidence only. No recovery or migration request."""
import sys
sys.dont_write_bytecode = True
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import getpass
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import socket
import stat
import subprocess
import tempfile

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('diagnostic_vip', REPO/'scripts/phase5-api-vip.py')
vip = importlib.util.module_from_spec(spec); spec.loader.exec_module(vip)
NODES = vip.inv.NODES
SERVICES = ('nova_scheduler', 'nova_conductor', 'nova_compute', 'neutron_server', 'rabbitmq')
RPC_READER = r'''import ast,configparser,hashlib,importlib.metadata,json,re
from pathlib import Path
cfg=configparser.ConfigParser(interpolation=None)
if not cfg.read('/etc/nova/nova.conf'):raise RuntimeError('Nova configuration unavailable')
timeouts={}
for name in ('rpc_response_timeout','long_rpc_timeout','executor_thread_pool_size','rpc_conn_pool_size'):
 value=cfg.defaults().get(name)
 timeouts[name]=value if value is not None and re.fullmatch(r'[0-9.]+',value) else 'not explicitly configured as a number'
dist=importlib.metadata.distribution('nova')
path=Path(dist.locate_file('nova/scheduler/rpcapi.py'));raw=path.read_bytes();tree=ast.parse(raw)
methods=[n for c in tree.body if isinstance(c,ast.ClassDef) for n in c.body if isinstance(n,ast.FunctionDef) and n.name=='select_destinations']
if len(methods)!=1:raise RuntimeError('Expected scheduler RPC method unavailable')
method=methods[0]
rpc_calls=[{'method':n.func.attr,'timeout_arguments':{k.arg:ast.unparse(k.value) for k in n.keywords if k.arg in ('timeout','call_monitor_timeout')}} for n in ast.walk(method) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ('prepare','call')]
messaging=importlib.metadata.distribution('oslo.messaging')
driver_raw=Path(messaging.locate_file('oslo_messaging/_drivers/amqpdriver.py')).read_bytes()
driver_tree=ast.parse(driver_raw)
classes=[n for n in driver_tree.body if isinstance(n,ast.ClassDef) and n.name=='QManager']
fix='unrecognized'
if len(classes)==1:
 methods={n.name:n for n in classes[0].body if isinstance(n,ast.FunctionDef)}
 init=methods.get('__init__');get=methods.get('get');identity=methods.get('_service_identity')
 def has_call(node,name):
  return node is not None and any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr==name for n in ast.walk(node))
 if get is not None and identity is not None and has_call(get,'_service_identity') and has_call(identity,'getpgrp') and not has_call(init,'getpgrp'):
  fix='present'
 elif get is not None and has_call(init,'getpgrp') and any(isinstance(n,ast.Attribute) and isinstance(n.value,ast.Name) and n.value.id=='self' and n.attr=='pg' for n in ast.walk(get)):
  fix='legacy construction-time identity'
print(json.dumps({'nova_version':dist.version,'explicit_RPC_settings':timeouts,'scheduler_rpcapi_sha256':hashlib.sha256(raw).hexdigest(),'select_destinations_lines':[method.lineno,method.end_lineno],'RPC_calls':rpc_calls,
 'messaging':{'version':messaging.version,'use_queue_manager':cfg.getboolean('oslo_messaging_rabbit','use_queue_manager',fallback=None),'qmanager_identity_fix':fix,'amqpdriver_sha256':hashlib.sha256(driver_raw).hexdigest(),'debug_logging':cfg.getboolean('DEFAULT','debug',fallback=None),'scope':'Installed source/config only; not proof of a queue collision or root cause.'}}))
'''


def redact(line):
    # Exception messages may include URLs or credentials: omit the whole line.
    if re.search(r'://|password|passwd|authorization|auth_token|transport_url|secret|credential|token\s*[=:]', line, re.I):
        return '[sensitive line withheld]'
    return line[:700]


def excerpt(raw, request, start, end):
    lines = raw.splitlines(); selected = []; remaining = 0
    for line in lines:
        stamp = re.match(r'^(\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d)', line)
        in_window = stamp and start <= stamp[1].replace('T', ' ') <= end
        if request in line or (in_window and re.search(r'ERROR|WARNING|CRITICAL|Traceback|No calling threads waiting|Skipping host:|Binding failed for port', line, re.I)):
            remaining = 8
        elif stamp:
            remaining = 0
        if remaining:
            selected.append(redact(line)); remaining -= 1
    omitted = max(0, len(selected)-99) if len(selected)>100 else 0
    kept = selected if not omitted else selected[:50]+['[... '+str(omitted)+' matching middle lines omitted ...]']+selected[-49:]
    return {'lines': kept, 'matched_lines': len(selected), 'omitted_middle_lines': omitted,
            'limit': 'Last 8 MiB per file; at most 100 lines. Long excerpts retain the beginning and end. Empty output does not establish a cause.'}


def run(args, timeout=25):
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError('Read query failed: '+args[0]+' (exit '+str(p.returncode)+')')
    return p.stdout


def container_query(name):
    if name not in SERVICES: raise RuntimeError('Unapproved container')
    # Formatting avoids returning Config.Env or command arguments containing secrets.
    fmt = '{"id":{{json .Id}},"image":{{json .Config.Image}},"state":{{json .State.Status}},"running":{{json .State.Running}},"started":{{json .State.StartedAt}},"restarts":{{json .RestartCount}},"mounts":{{json .Mounts}}}'
    return json.loads(run(['docker', 'inspect', '--format', fmt, name]))


def log_paths(info, service):
    mounts = [m for m in info['mounts'] if m.get('Destination','').rstrip('/') == '/var/log/kolla' and m.get('Type') == 'volume']
    if len(mounts) != 1: raise RuntimeError('Expected Kolla log volume unavailable')
    root = Path(mounts[0]['Source'])
    if root.is_symlink() or not root.is_absolute() or root.resolve() != root:
        raise RuntimeError('Unexpected log volume path')
    if service == 'rabbitmq':
        paths = sorted((root/'rabbitmq').glob('*.log'))[:6]
    elif service == 'neutron_server':
        paths = [root/'neutron/neutron-server.log']
    else:
        paths = [root/'nova'/(service.replace('_', '-')+'.log')]
    for path in paths:
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise RuntimeError('Expected regular log file unavailable')
    if not paths: raise RuntimeError('No matching log file')
    return paths


def read_tail(path):
    with path.open('rb') as stream:
        size = os.fstat(stream.fileno()).st_size
        offset = max(0, size-8*1024*1024); stream.seek(offset)
        raw = stream.read(8*1024*1024)
    if offset: raw = raw.partition(b'\n')[2]
    return raw.decode('utf-8', 'replace'), bool(offset)


def collect_node(node, request, start, end):
    if node not in NODES or os.geteuid() != 0 or os.environ.get('SUDO_USER') != 'joe' or socket.gethostname().split('.')[0] != node:
        raise RuntimeError('Expected node identity and sudo from joe')
    addresses = json.loads(run(['ip', '-j', 'address', 'show', 'dev', 'enp0s31f6']))
    if not any(a.get('local') == NODES[node] for link in addresses for a in link.get('addr_info', [])):
        raise RuntimeError('Management address mismatch')
    records = {}
    for service in SERVICES:
        item = {}; records[service] = item
        try:
            info = container_query(service)
            expected = 'quay.io/openstack.kolla/'+service.replace('_','-')+':2026.2-ubuntu-noble'
            if info.get('image') != expected: raise RuntimeError('Unreviewed container image')
            item['container'] = {k: info[k] for k in ('id','image','state','running','started','restarts')}
            if service in ('nova_scheduler','nova_conductor') and info.get('running'):
                try:
                    item['installed_RPC'] = json.loads(run(['docker','exec',service,'/var/lib/kolla/venv/bin/python3','-c',RPC_READER]))
                except (RuntimeError,OSError,ValueError,subprocess.TimeoutExpired):
                    item['installed_RPC'] = {'error':'Installed Nova RPC inspection unavailable'}
            item['logs'] = []
            for path in log_paths(info, service):
                raw, truncated = read_tail(path)
                item['logs'].append(dict(excerpt(raw, request, start, end), file=path.name, scan_truncated=truncated))
            item['collected'] = True
        except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired, KeyError) as error:
            item['collected'] = False
            item['error'] = str(error) if isinstance(error, RuntimeError) else type(error).__name__+'; query incomplete'
    return {'node': node, 'services': records,
            'complete': all(r.get('collected') for r in records.values())}


def observe(node, password, request, start, end, env):
    source = Path(__file__).read_text()
    # Execute only the collector definitions on nodes; never import local helpers there.
    source = source[source.index('def redact('):source.index('def observe(')]
    header = 'import json,os,re,socket,subprocess\nfrom pathlib import Path\n'
    header += 'NODES='+repr(NODES)+'\nSERVICES='+repr(SERVICES)+'\nRPC_READER='+repr(RPC_READER)+'\n'
    footer = '\nprint(json.dumps(collect_node('+','.join(map(repr,(node,request,start,end)))+')))\n'
    payload = base64.b64encode((header+source+footer).encode()).decode()
    code = 'import base64;exec(compile(base64.b64decode('+repr(payload)+'),"migration-diagnostic","exec"))'
    remote = shlex.join(['sudo','-S','-p','','/usr/bin/python3','-c',code])
    try:
        p = subprocess.run(vip.ssh_args(node)+[remote], input=password+'\n', capture_output=True, text=True, env=env, timeout=180)
        value = json.loads(p.stdout)
        if p.returncode or value.get('node') != node or type(value.get('complete')) is not bool: raise ValueError()
        return value
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return {'node':node,'complete':False,'error':'SSH/sudo or collector response unavailable; no change requested'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--request-id', required=True)
    parser.add_argument('--start', required=True, help='Incident UTC time: YYYY-MM-DD HH:MM:SS')
    parser.add_argument('--end', required=True, help='Incident UTC time: YYYY-MM-DD HH:MM:SS')
    args = parser.parse_args(argv)
    if not re.fullmatch(r'req-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', args.request_id): parser.error('Invalid request ID')
    for value in (args.start,args.end):
        if dt.datetime.strptime(value,'%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d %H:%M:%S') != value: parser.error('Invalid incident time')
    if args.start > args.end: parser.error('Start must precede end')
    if not args.run:
        print('PLAN: read Nova scheduler/conductor/compute, Neutron API and RabbitMQ status/logs on all three nodes; inspect installed Nova RPC and oslo.messaging queue-manager source/settings. Add --run. No API request, service change, retry or checkpoint update.'); return 0
    vip.deploy.source_guard(); env = vip.deploy.tools.tool_environment(); vip.inv.ensure_agent(env,print)
    folder = REPO/'reports'; vip.inv.private_dir(folder)
    passwords = {n:getpass.getpass('Joe sudo password on '+n+' (hidden): ') for n in NODES}
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs = {n:pool.submit(observe,n,passwords[n],args.request_id,args.start,args.end,env) for n in NODES}
            results = {n:j.result() for n,j in jobs.items()}
    finally: passwords.clear()
    fd, name = tempfile.mkstemp(prefix='phase5-migration-diagnose-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),suffix='.txt',dir=folder)
    complete = all(r['complete'] for r in results.values())
    with os.fdopen(fd,'w') as stream:
        stream.write(json.dumps({'request_id':args.request_id,'UTC_window':[args.start,args.end],'nodes':results},indent=2)+'\n')
        stream.write('RESULT: MIGRATION_EVIDENCE_COLLECTED\n' if complete else 'RESULT: MIGRATION_EVIDENCE_INCOMPLETE\n')
        stream.write('Evidence only; no root cause or recovery conclusion. Live guest, API state and broker queue health are separate checks. No checkpoint changed.\n')
    print('Node | Collection')
    for n,r in results.items(): print(n+' | '+('collected' if r['complete'] else 'incomplete; see report'))
    print('Node | Container | oslo.messaging | Queue manager | Identity fix in source')
    for node,record in results.items():
        for service in ('nova_scheduler','nova_conductor'):
            data=record.get('services',{}).get(service,{}).get('installed_RPC',{}).get('messaging',{})
            enabled={True:'ON',False:'OFF',None:'not explicit'}.get(data.get('use_queue_manager'),'unknown')
            fix={'present':'PRESENT','legacy construction-time identity':'MISSING (legacy code)'}.get(data.get('qmanager_identity_fix'),'UNKNOWN')
            print(node+' | '+service+' | '+str(data.get('version','unknown'))+' | '+enabled+' | '+fix)
    print('PRESENT = recognized upstream fix; MISSING = legacy source pattern; UNKNOWN = source unavailable or unrecognized.')
    print('Source/settings check only. Missing fix plus ON identifies an upstream bug candidate, not a confirmed cause.')
    print('Private report: '+name)
    print('Return report: cat '+shlex.quote(name))
    return 0 if complete else 1


if __name__ == '__main__':
    try: sys.exit(main())
    except (RuntimeError,OSError,ValueError) as error:
        print('Stopped: '+(str(error) if isinstance(error,RuntimeError) else type(error).__name__+'; no change requested'),file=sys.stderr); sys.exit(1)

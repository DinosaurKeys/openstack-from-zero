"""Read menu migration state and explicitly acknowledge a terminal failed attempt.

This helper receives the menu's GET-only API. It sends no cloud mutations.
"""
import copy
import datetime as dt
import hashlib
import json
import os

STAGE='failed menu attempt acknowledged; fresh plan required'
KINDS={'migration':'COLD VM move','live-migration':'LIVE VM move','resize':'RESIZE','evacuation':'EVACUATION'}


def move_name(menu,row):
    kind=row.get('migration_type')
    return KINDS.get(kind,'UNKNOWN move')+' ('+menu.clean(kind)+')'


def records(menu,folder,server=None,pending=False):
    result=[]
    for path in sorted(folder.glob(menu.PREFIX+'*.json')):
        state=menu.health.read_private(path)
        if not isinstance(state,dict):raise RuntimeError('Migration record is not an object: '+path.name)
        if server and state.get('server_id')!=server:continue
        if pending and state.get('pending') is not True:continue
        result.append((path,state))
    return result


def select_vm(menu,api,project,read,log):
    servers=[v for v in api.list('compute','servers/detail?limit=100','servers')
      if v.get('tenant_id')==project and str(v.get('OS-EXT-SRV-ATTR:host','')).split('.')[0] in menu.NODES]
    if not servers:log('No VMs in this project on the lab nodes.');return None
    value=menu.choose('Choose the VM to observe',servers,
      lambda v:menu.clean(v.get('name'))+' | '+str(v.get('status'))+' | '+menu.node_name(v.get('OS-EXT-SRV-ATTR:host'))+' | '+v['id'],read,log)
    return value['id'] if value else None


def observe(menu,api,project,folder,read,log):
    log('CHECK ONLY — read VM/migration state; no move or tracking change.')
    server_id=select_vm(menu,api,project,read,log)
    if server_id is None:return 0
    server=menu.server_value(api,server_id,project)
    volumes=menu.attachments(api,server,project)
    history=menu.migration_history(api,server_id)
    log('VM: '+menu.clean(server.get('name'))+' | Nova '+str(server.get('status'))+' | host '+menu.node_name(server['OS-EXT-SRV-ATTR:host']))
    task=server.get('OS-EXT-STS:task_state')
    log('Current operation: '+('UNKNOWN — Nova task field unavailable' if 'OS-EXT-STS:task_state' not in server else 'NONE — Nova reports no operation' if task is None else menu.clean(task)))
    for vid,value in volumes.items():log('Data volume: '+menu.clean(value.get('name'))+' | '+str(value.get('status'))+' | '+value['os-vol-host-attr:host']+' | '+vid)
    ordered=sorted(history,key=lambda r:(r.get('created_at') or '',r['uuid']),reverse=True)
    completed=[row for row in ordered if row.get('status') in ('completed','confirmed')]
    if completed:
        last=completed[0]
        log('Latest completed move in Nova: '+move_name(menu,last)+' | '+menu.clean(last.get('source_compute'))+' -> '+menu.clean(last.get('dest_compute')))
        if last.get('migration_type')=='migration':log('A completed cold move does not establish live-migration success.')
    log('Recent migrations — UTC | Type | Status | Source | Destination | ID')
    for row in ordered[:5]:
        log(' | '.join([menu.clean(row.get('created_at')),move_name(menu,row)]+[menu.clean(row.get(k)) for k in ('status','source_compute','dest_compute','uuid')]))
    if not history:log('No Nova migration records observed.')
    fault=server.get('fault') or {}
    if fault:log('Current Nova fault: '+str(fault.get('code','-'))+' '+str(menu.move.safe_message(fault.get('message'))))
    saved=records(menu,folder,server_id)
    for path,state in saved:
        log('Local record: '+path.name+' | '+('PENDING' if state.get('pending') else 'closed')+' | '+menu.clean(state.get('stage')))
        request=state.get('request_ids',{}).get('move')
        if request:log('Recorded request ID: '+menu.clean(request))
    if any(state.get('pending') for _,state in saved):log('PENDING is a saved local task awaiting review; current Nova operations are shown separately. Review an old failed task with --reconcile; it does not move the VM.')
    log('RESULT: MIGRATION_STATE_OBSERVED')
    log('Read-only API snapshot. Actual guest runtime, networking and file checks remain separate.')
    return 0


def validate(menu,state,project):
    plan=state.get('plan')
    if not isinstance(plan,dict) or plan.get('mode') not in ('live','cold'):
        raise RuntimeError('Only a failed VM/OS-disk menu attempt can be acknowledged here; record preserved')
    server=menu.identifier(state.get('server_id'))
    before=plan.get('before')
    if (state.get('pending') is not True or state.get('attempts')!=['move'] or state.get('failed_attempt_resolution') is not None
            or plan.get('server_id')!=server or plan.get('project_id')!=project or not isinstance(before,dict)
            or before.get('server_id')!=server or before.get('source')!=plan.get('source')
            or plan.get('source') not in menu.NODES or plan.get('destination') not in menu.NODES
            or plan['source']==plan['destination'] or before.get('status') not in ('ACTIVE','SHUTOFF')):
        raise RuntimeError('Original pending VM attempt identity/scope needs review; record preserved')
    baseline=plan.get('before_migrations')
    if not isinstance(baseline,list) or any(not menu.volume.valid_uuid(v) for v in baseline) or len(set(baseline))!=len(baseline):
        raise RuntimeError('Original migration baseline is invalid; record preserved')
    return plan


def evidence(menu,api,state,project):
    plan=validate(menu,state,project)
    server=menu.server_value(api,state['server_id'],project)
    if server.get('status')!=plan['before']['status'] or 'OS-EXT-STS:task_state' not in server or server['OS-EXT-STS:task_state'] is not None or server.get('locked') is True:
        raise RuntimeError('VM must be idle in its original power state; record preserved')
    current=menu.binding(server,menu.attachments(api,server,project))
    if any(current.get(k)!=plan['before'].get(k) for k in ('server_id','image_id','flavor','addresses','volumes')):
        raise RuntimeError('VM identity, addresses or attached storage changed; record preserved')
    history=menu.migration_history(api,server['id'])
    if not set(plan['before_migrations']).issubset({r['uuid'] for r in history}):raise RuntimeError('Saved migration baseline is no longer complete; record preserved')
    if any(r.get('status') not in menu.TERMINAL for r in history):raise RuntimeError('An active or unknown migration still exists; record preserved')
    fresh=[r for r in history if r['uuid'] not in plan['before_migrations']]
    rejected=state.get('http_statuses',{}).get('move')
    if not fresh and current['source']==plan['source'] and rejected in (400,401,403,404,405,406,409,412,415,422):
        return {'server_id':server['id'],'status':server['status'],'host':current['source'],'failed_migration_id':None,
          'failed_status':'HTTP '+str(rejected)+' rejected','later_completed_migration_id':None,'binding':current,
          'history':sorted([r['uuid'],r.get('status'),r.get('source_compute'),r.get('dest_compute')] for r in history)}
    kind='live-migration' if plan['mode']=='live' else 'migration'
    failures=[r for r in fresh if r.get('status') in ('error','failed') and r.get('migration_type')==kind
      and r.get('source_compute') and menu.node_name(r['source_compute'])==plan['source']
      and (r.get('dest_compute') is None or menu.node_name(r['dest_compute'])==plan['destination'])]
    if len(failures)!=1:raise RuntimeError('Require one unambiguous failed attempt since this plan; record preserved')
    failed=failures[0];later=None
    if current['source']!=plan['source']:
        completed=[r for r in fresh if r.get('status') in ('completed','confirmed') and r.get('migration_type') in ('live-migration','migration')
          and r.get('source_compute') and menu.node_name(r['source_compute'])==plan['source']
          and r.get('dest_compute') and menu.node_name(r['dest_compute'])==current['source']
          and isinstance(r.get('created_at'),str) and isinstance(failed.get('created_at'),str) and r['created_at']>failed['created_at']]
        if len(completed)!=1:raise RuntimeError('Changed host requires one later completed Nova migration matching the current placement; record preserved')
        later=completed[0]['uuid']
    return {'server_id':server['id'],'status':server['status'],'host':current['source'],'failed_migration_id':failed['uuid'],
      'failed_status':failed['status'],'later_completed_migration_id':later,'binding':current,
      'history':sorted([r['uuid'],r.get('status'),r.get('source_compute'),r.get('dest_compute')] for r in history)}


def acknowledge(menu,api,project,folder,read,log,unchanged,current_inputs):
    log('REVIEW OLD FAILED TASK — acknowledge one saved failure; no VM move, power or disk change.')
    pending=records(menu,folder,pending=True)
    choices=[(p,s) for p,s in pending if isinstance(s.get('plan'),dict) and s['plan'].get('mode') in ('live','cold')]
    if not choices:
        if pending:raise RuntimeError('Pending storage or unrecognized records require separate review; preserved')
        log('No pending VM/OS-disk menu attempts.');log('RESULT: MIGRATION_RECORDS_CURRENT');return 0
    selected=menu.choose('Choose the failed menu record to review',choices,
      lambda row:row[0].name+' | '+menu.clean(row[1].get('plan',{}).get('vm_name') or row[1].get('server_id')),read,log)
    if selected is None:return 0
    path,state=selected;menu.deploy.validation.private_file(path);raw=path.read_bytes()
    if json.loads(raw)!=state:raise RuntimeError('Selected record changed; preserved')
    def other_records():
        values={}
        for other,_ in records(menu,folder):
            if other!=path:values[other.name]=hashlib.sha256(other.read_bytes()).hexdigest()
        return values
    others=other_records()
    external_unchanged=unchanged
    def unchanged():
        external_unchanged()
        if other_records()!=others:raise RuntimeError('Another menu record changed; records preserved')
    if state.get('inputs') is not None and state['inputs']!=current_inputs:raise RuntimeError('Deployment inputs differ from the saved attempt; record preserved')
    observed=evidence(menu,api,state,project)
    log('Local record: '+path.name)
    log('VM: '+menu.clean(state['plan'].get('vm_name') or state['server_id'])+' | '+state['server_id'])
    log('Failed attempt: '+menu.clean(observed['failed_migration_id'])+' | '+observed['failed_status'])
    log('Current VM: '+observed['status']+' on '+observed['host'])
    if observed['later_completed_migration_id']:log('Later completed move: '+observed['later_completed_migration_id']+' (separate from the failed script attempt)')
    log('This acknowledges only the failed local record. Its original bytes and Nova history are retained.')
    if read('Type ACKNOWLEDGE FAILED ATTEMPT to close this local pending flag, or Enter to cancel: ').strip()!='ACKNOWLEDGE FAILED ATTEMPT':
        log('Cancelled. Record unchanged.');return 0
    if evidence(menu,api,state,project)!=observed:raise RuntimeError('API evidence changed; record preserved')
    unchanged()
    if path.is_symlink() or path.read_bytes()!=raw:raise RuntimeError('Local record changed; preserved')
    digest=hashlib.sha256(raw).hexdigest();archive=folder/'migration-history'/(path.stem+'-'+digest+'.json')
    menu.inv.private_dir(archive.parent)
    if archive.exists() or archive.is_symlink():
        menu.deploy.validation.private_file(archive)
        if archive.read_bytes()!=raw:raise RuntimeError('Original record archive differs; pending record preserved')
    else:
        descriptor=os.open(archive,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(descriptor,'wb') as output:output.write(raw);output.flush();os.fsync(output.fileno())
    closed=copy.deepcopy(state)
    closed.update(pending=False,stage=STAGE,failed_attempt_resolution={'kind':'acknowledged_failure',
      'original_sha256':digest,'original_record':str(archive.relative_to(folder)),'observed_api':observed,
      'recorded_at':dt.datetime.now(dt.timezone.utc).isoformat()})
    unchanged()
    if path.is_symlink() or path.read_bytes()!=raw:raise RuntimeError('Local record changed before acknowledgement; preserved')
    menu.health.prep.net.write_state(path,closed)
    if menu.health.read_private(path)!=closed or archive.read_bytes()!=raw:raise RuntimeError('Saved local acknowledgement needs review')
    log('Original record retained: '+str(archive))
    log('RESULT: FAILED_MENU_ATTEMPT_ACKNOWLEDGED')
    log('No cloud action performed. A new move still requires a fresh preview and approval; guest/data checks remain separate.')
    return 0


def run(menu,api,project,folder,log,reconcile=False,unchanged=lambda:None,current_inputs=None,read=input):
    if not api.read_only:raise RuntimeError('Record review requires a GET-only API client')
    if reconcile:return acknowledge(menu,api,project,folder,read,log,unchanged,current_inputs)
    return observe(menu,api,project,folder,read,log)

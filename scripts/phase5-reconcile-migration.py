#!/usr/bin/env python3
"""Close one reviewed failed migration checkpoint; never request a cloud change."""
import sys
sys.dont_write_bytecode = True
import argparse
import copy
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import tempfile

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('reconcile_move', REPO/'scripts/phase5-move-vm.py')
move = importlib.util.module_from_spec(spec); spec.loader.exec_module(move)
deploy = move.deploy; inv = move.inv; ENV = move.ENV
STAGE = 'failed migration acknowledged; fresh migration plan required'


def read_receipt(path):
    deploy.validation.private_file(path)
    raw = path.read_bytes(); state = json.loads(raw)
    move.validate_receipt(state)
    return raw, state


def api_evidence(api, state, migration_id):
    server = api.get('compute', 'servers/'+state['server_id'])['server']
    if (server.get('id') != state['server_id'] or server.get('tenant_id') != state['project_id']
            or server.get('user_id') != state['user_id']):
        raise RuntimeError('VM identity/project differs; checkpoint preserved')
    if (server.get('status') != 'ACTIVE' or server.get('OS-EXT-STS:task_state') is not None
            or server.get('OS-EXT-SRV-ATTR:host') != move.PLAN['source']):
        raise RuntimeError('Require the recovered VM ACTIVE and idle on the original source; checkpoint preserved')
    history = api.history()
    if (not isinstance(history, list) or len(history) >= 100
            or any(r.get('instance_uuid') != state['server_id'] or not move.volume.valid_uuid(r.get('uuid')) for r in history)
            or len({r['uuid'] for r in history}) != len(history)):
        raise RuntimeError('Migration history is incomplete or ambiguous; checkpoint preserved')
    if any(r.get('status') not in move.TERMINAL for r in history):
        raise RuntimeError('A migration is still active or unknown; checkpoint preserved')
    rows = [r for r in history if r['uuid'] not in state['before_migration_ids']]
    if len(rows) != 1:
        raise RuntimeError('Require exactly one recorded attempt since preparation; checkpoint preserved')
    row = rows[0]
    if (row['uuid'] != migration_id or row.get('status') != 'error'
            or row.get('migration_type') != 'live-migration'
            or row.get('source_compute') != move.PLAN['source']
            or row.get('dest_compute') not in (None, move.PLAN['destination'])):
        raise RuntimeError('The selected migration is not the recorded failed attempt; checkpoint preserved')
    return {'server_id': state['server_id'], 'server_status': 'ACTIVE', 'host': move.PLAN['source'],
            'task': None, 'migration_id': migration_id, 'migration_status': 'error',
            'history': sorted((r['uuid'], r['status']) for r in history)}


def archive_path(path, migration_id, digest):
    if not move.volume.valid_uuid(migration_id) or not re.fullmatch('[0-9a-f]{64}', digest):
        raise RuntimeError('Invalid reconciliation evidence identity')
    return path.parent/'migration-history'/(migration_id+'-'+digest+'.json')


def verify_resolution(path, state, migration_id):
    resolution = state.get('failed_migration_resolution')
    if (state.get('pending') is not False or state.get('stage') != STAGE or not isinstance(resolution, dict)
            or resolution.get('kind') != 'acknowledged_failure' or resolution.get('migration_id') != migration_id):
        raise RuntimeError('Checkpoint is not this completed reconciliation; preserved')
    digest = resolution.get('original_sha256', '')
    archived = archive_path(path, migration_id, digest)
    if resolution.get('original_record') != str(archived.relative_to(path.parent)):
        raise RuntimeError('Original-record location differs; preserved')
    original_raw, original = read_receipt(archived)
    if hashlib.sha256(original_raw).hexdigest() != digest or original.get('pending') is not True:
        raise RuntimeError('Original reconciliation evidence differs; preserved')
    expected = dict(original, pending=False, stage=STAGE, failed_migration_resolution=resolution)
    if state != expected:
        raise RuntimeError('Reconciled checkpoint differs from its original evidence; preserved')
    return archived


def reconcile(api, path, migration_id, unchanged, log):
    raw, state = read_receipt(path)
    if state.get('pending') is False:
        archived = verify_resolution(path, state, migration_id)
        log('Already reconciled. Original failed record retained: '+str(archived))
        log('RESULT: FAILED_MIGRATION_RECONCILED')
        return
    if state.get('pending') is not True or state.get('failed_migration_resolution') is not None:
        raise RuntimeError('Require the original pending attempt; checkpoint preserved')
    evidence = api_evidence(api, state, migration_id)
    log('Reviewed API evidence:\n'+json.dumps(evidence, indent=2))
    if api_evidence(api, state, migration_id) != evidence:
        raise RuntimeError('API evidence changed during review; checkpoint preserved')
    unchanged()
    if path.read_bytes() != raw or path.is_symlink():
        raise RuntimeError('Saved checkpoint changed during review; preserved')
    digest = hashlib.sha256(raw).hexdigest(); archived = archive_path(path, migration_id, digest)
    inv.private_dir(archived.parent)
    if archived.exists() or archived.is_symlink():
        deploy.validation.private_file(archived)
        if archived.read_bytes() != raw:
            raise RuntimeError('Existing original-record copy differs; checkpoint preserved')
    else:
        descriptor = os.open(archived, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'wb') as output:
            output.write(raw); output.flush(); os.fsync(output.fileno())
    closed = copy.deepcopy(state)
    closed.update(pending=False, stage=STAGE, failed_migration_resolution={
        'kind': 'acknowledged_failure', 'migration_id': migration_id,
        'original_sha256': digest, 'original_record': str(archived.relative_to(path.parent)),
        'observed_api': evidence, 'recorded_at': dt.datetime.now(dt.timezone.utc).isoformat()})
    unchanged()
    if path.read_bytes() != raw or path.is_symlink():
        raise RuntimeError('Saved checkpoint changed before reconciliation; preserved')
    move.net.write_state(path, closed)
    _, saved = read_receipt(path)
    verify_resolution(path, saved, migration_id)
    log('Original failed record retained: '+str(archived))
    log('Only this local pending flag is closed. The Nova migration remains recorded as error.')
    log('RESULT: FAILED_MIGRATION_RECONCILED')
    log('Next: bash scripts/lab-migrate.sh --plan. Fresh readiness and approval are still required.')


def protected_records(excluded):
    result = {}
    for phase in ('phase4', 'phase5'):
        for path in sorted((REPO/'private'/phase).glob('*.json')):
            if path == excluded:
                continue
            deploy.validation.private_file(path)
            result[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def execute(log, migration_id):
    deploy.source_guard(); env = deploy.tools.tool_environment()
    inventory, config = deploy.validation.local_checks(lambda _: None, env)
    folder = REPO/'private/phase5'; inv.private_dir(folder)
    lock_path = folder/'migration.lock'
    if lock_path.is_symlink():
        raise RuntimeError('Unsafe migration lock')
    with lock_path.open('a') as lock:
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another lab/migration action is running; no reconciliation') from None
        path = folder/'move-vm-state.json'; _, state = read_receipt(path)
        hashes = move.access.input_hashes(config, inventory); protected = protected_records(path)
        if state.get('inputs') != hashes:
            raise RuntimeError('Deployment inputs differ from the recorded attempt; preserved')
        original, project, user = move.prep.authenticate(config)
        if state.get('project_id') != project or state.get('user_id') != user or state.get('apis') != original.urls:
            raise RuntimeError('Authenticated project/user/API differs from the recorded attempt; preserved')
        api = move.API(original.client, original.urls, original.token, state['server_id'], move.PLAN['destination'], allow_move=False)
        def unchanged():
            if hashes != move.access.input_hashes(config, inventory) or protected != protected_records(path):
                raise RuntimeError('Protected inputs/checkpoints changed; reconciliation stopped')
        reconcile(api, path, migration_id, unchanged, log)
        unchanged()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='reconcile the one verified failed local attempt')
    parser.add_argument('--migration-id', help='exact failed migration UUID from the reviewed observation')
    args = parser.parse_args(argv)
    if args.migration_id is not None and not move.volume.valid_uuid(args.migration_id):
        parser.error('--migration-id requires the exact migration UUID')
    if args.apply and args.migration_id is None:
        parser.error('--apply requires --migration-id')
    if not args.apply:
        print('PLAN ONLY: verify one Nova migration is error and the VM is ACTIVE/idle on its original source; preserve the original local record and close only its pending flag.')
        print('Use --apply --migration-id UUID after reviewing --observe. No cloud change, VM/storage health claim or migration request.'); return 0
    deploy.validation.config_step.load_step('reconcile_r2d2', 'phase2-check.py').require_r2d2()
    deploy.tools.check_paths(ENV)
    if not sys.stdin.isatty():
        raise RuntimeError('Run in joe\'s interactive R2d2 terminal')
    if Path(sys.prefix) != ENV:
        os.execve(str(ENV/'bin/python'), [str(ENV/'bin/python'), str(Path(__file__).resolve()), '--apply', '--migration-id', args.migration_id], deploy.tools.tool_environment())
    os.umask(0o077); reports = REPO/'reports'; inv.private_dir(reports)
    fd, name = tempfile.mkstemp(prefix='phase5-reconcile-migration-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), suffix='.txt', dir=reports)
    status = 1
    with os.fdopen(fd, 'w') as output:
        def log(line):
            print(line, flush=True); output.write(line+'\n'); output.flush()
        log('Phase 5 E — reconcile one reviewed failed local migration record')
        try:
            execute(log, args.migration_id); status = 0
        except (Exception, KeyboardInterrupt) as error:
            log('RESULT: MIGRATION_RECONCILIATION_NEEDS_REVIEW')
            log(move.safe_message(str(error)) if isinstance(error, RuntimeError) else type(error).__name__+'; return report')
        log('No cloud mutation, VM power action, migration retry, volume operation or configuration change was requested. Guest/runtime health and the original timeout cause are not established by this step.')
    print('Return short report: cat '+shlex.quote(name)); return status


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError) as error:
        print('Stopped: '+move.safe_message(str(error)), file=sys.stderr); sys.exit(1)

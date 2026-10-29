#!/usr/bin/env python3
"""Complete the existing local ProxySQL TLS file layout; no node connections."""
import sys
sys.dont_write_bytecode=True
import argparse,datetime as dt,importlib.metadata,importlib.util,json,os,re,shlex,tempfile
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
ENV=Path('/home/joe/venvs/kolla')
spec=importlib.util.spec_from_file_location('db_tls_validation',Path(__file__).with_name('phase2-validate.py'))
validation=importlib.util.module_from_spec(spec);spec.loader.exec_module(validation)
step=validation.config_step;tools=validation.tools

def prepare_existing(log,expected_ca):
    import yaml
    config=REPO/'private/phase2/kolla';certs=config/'certificates'
    for path in [config/step.MARKER,config/'globals.yml',*(certs/name for name in ('ca/lab-root.crt','private/lab-root.key','private/server.key','server.crt','haproxy.pem','haproxy-internal.pem'))]:
        validation.private_file(path)
    if json.loads((config/step.MARKER).read_text())!=step.MARK:
        raise RuntimeError('Expected the existing Chapter E configuration marker')
    if yaml.safe_load((config/'globals.yml').read_text())!=step.settings(config):
        raise RuntimeError('Globals differ from the reviewed lab configuration; preserved')
    fingerprint=step.verify_certificates(certs)
    if fingerprint!=validation.reviewed_ca(config,expected_ca):
        raise RuntimeError('Existing CA fingerprint differs from the reviewed value; no files added')
    original={p:p.read_bytes() for p in [config/'globals.yml',*(certs/name for name in ('ca/lab-root.crt','private/lab-root.key','private/server.key','server.crt','haproxy.pem','haproxy-internal.pem'))]}
    step.ensure_proxysql_certificates(certs)
    if any(p.read_bytes()!=content for p,content in original.items()):
        raise RuntimeError('Original TLS/configuration contents changed; stop for review')
    log('PASS existing certificate signature, key matches, dates, VIP and reviewed CA fingerprint.')
    for name in step.PROXYSQL_FILES:log('Ready: '+str(certs/name)+' (private, mode 0600)')
    log('PASS original CA, keys, API PEMs and globals unchanged; no certificates regenerated.')
    log('CA SHA256: '+fingerprint)
    log('RESULT: PROXYSQL_TLS_READY')
    log('R2d2 local files only. No passwords read, sudo, packages, node connections or TLS-setting changes.')

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--apply',action='store_true');p.add_argument('--expected-ca',help='optional pin; normally use the saved Phase 2 CA approval');args=p.parse_args(argv)
    if args.expected_ca is not None and not re.fullmatch('[0-9a-f]{64}',args.expected_ca):p.error('--expected-ca needs 64 lowercase hexadecimal characters')
    if not args.apply:
        print('PLAN ONLY: verify existing lab certificates and add/reuse the three private ProxySQL TLS files on R2d2.')
        print('No node connections, file changes, new certificates or changed TLS settings.');return 0
    step.load_step('db_tls_r2d2','phase2-check.py').require_r2d2();tools.check_paths(ENV)
    if Path(sys.prefix)!=ENV:
        exe=str(ENV/'bin/python');os.execve(exe,[exe,str(Path(__file__).resolve()),*sys.argv[1:]],tools.tool_environment())
    if importlib.metadata.version('kolla-ansible')!='23.0.0' or importlib.metadata.version('ansible-core')!='2.21.5':
        raise RuntimeError('Expected the agreed Kolla 23.0.0 / Ansible-core 2.21.5 environment')
    os.umask(0o077);reports=REPO/'reports';validation.inv.private_dir(reports)
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-')
    fd,name=tempfile.mkstemp(prefix='phase3-db-tls-'+stamp,suffix='.txt',dir=reports);status=1
    with os.fdopen(fd,'w') as output:
        def log(line):print(line,flush=True);output.write(line+'\n');output.flush()
        log('Phase 3 — Chapter B: local ProxySQL TLS certificate preparation')
        try:prepare_existing(log,args.expected_ca);status=0
        except Exception as e:
            log('RESULT: PROXYSQL_TLS_NEEDS_REVIEW')
            log('Stopped: '+str(e) if isinstance(e,RuntimeError) else 'Stopped ('+type(e).__name__+'); private-input details withheld.')
            log('No automatic replacement, regeneration or node changes. Return this report.')
    print('Private report: '+name);print('Read it with: cat '+shlex.quote(name));return status
if __name__=='__main__':
    try:sys.exit(main())
    except (OSError,RuntimeError) as e:print('Stopped: '+str(e),file=sys.stderr);sys.exit(1)

#!/usr/bin/env python3
"""Apply one reviewed Nova messaging setting through its existing Kolla unit."""
import base64
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import tempfile
import time

SERVICES = ('nova_scheduler', 'nova_conductor')
CONFIG_DIRS = {'nova_scheduler': 'nova-scheduler', 'nova_conductor': 'nova-conductor'}
NODES = {'node1': '192.168.0.200', 'node2': '192.168.0.201', 'node3': '192.168.0.202'}
DRIVER_SHA = '652d12f2c6b744b19e1b813842d09b4ebcb47282ca2b7a6ecbbec2617cb176a5'
SECTION = 'oslo_messaging_rabbit'
OPTION = 'use_queue_manager'
OVERRIDE = b'[oslo_messaging_rabbit]\nuse_queue_manager = false\n'
SCOPE = 'nova-scheduler-conductor-qmanager-off-v1'
RECEIPT = 'nova-messaging-fix.json'
FINISHED = 'six services verified with queue manager disabled'
READER = r'''import configparser,hashlib,importlib.metadata,json
from pathlib import Path
p=Path('/etc/nova/nova.conf');raw=p.read_bytes()
c=configparser.ConfigParser(interpolation=None);c.read_string(raw.decode())
d=importlib.metadata.distribution('oslo.messaging')
source=Path(d.locate_file('oslo_messaging/_drivers/amqpdriver.py')).read_bytes()
print(json.dumps({'version':d.version,'driver_sha256':hashlib.sha256(source).hexdigest(),
 'use_queue_manager':c.getboolean('oslo_messaging_rabbit','use_queue_manager'),
 'config_sha256':hashlib.sha256(raw).hexdigest(),
 'transient_ttl':c.getint('oslo_messaging_rabbit','rabbit_transient_queues_ttl',fallback=1800)}))
'''


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def patched(raw, missing=False):
    """Change just this Boolean, preserving all other bytes, including secrets."""
    text = raw.decode('utf-8')
    if '{{' in text or '{%' in text:
        raise RuntimeError('Templated override requires review')
    cfg = configparser.ConfigParser(interpolation=None, strict=True, inline_comment_prefixes=('#', ';'))
    try:
        cfg.read_string(text)
    except configparser.Error:
        raise RuntimeError('Ambiguous INI configuration; unchanged') from None
    exists = cfg.has_option(SECTION, OPTION)
    if exists:
        try:
            cfg.getboolean(SECTION, OPTION)
        except ValueError:
            raise RuntimeError('Queue-manager setting is not a Boolean') from None
    elif not missing:
        raise RuntimeError('Expected explicit queue-manager setting unavailable')
    lines = text.splitlines(keepends=True)
    newline = '\r\n' if '\r\n' in text else '\n'
    section = None
    section_end = len(lines)
    found = None
    for i, line in enumerate(lines):
        header = re.fullmatch(r'\s*\[([^\]]+)\]\s*(?:[#;].*)?(?:\r?\n)?', line)
        if header:
            if section == SECTION:
                section_end = i
            section = header[1]
        if section == SECTION and re.match(r'\s*use_queue_manager\s*[:=]', line, re.I):
            if found is not None:
                raise RuntimeError('Duplicate queue-manager setting')
            found = i
    if found is not None:
        line = lines[found]
        match = re.fullmatch(r'(\s*use_queue_manager\s*[:=]\s*)(true|false|yes|no|on|off|1|0)(\s*(?:[#;].*)?)(\r?\n)?', line, re.I)
        if not match:
            raise RuntimeError('Unreviewed queue-manager line; unchanged')
        lines[found] = match[1]+'false'+match[3]+(match[4] or '')
    elif cfg.has_section(SECTION):
        if section_end and not lines[section_end-1].endswith('\n'):
            lines[section_end-1] += newline
        lines.insert(section_end, OPTION+' = false'+newline)
    else:
        if lines and not lines[-1].endswith('\n'):
            lines[-1] += newline
        lines += ['['+SECTION+']'+newline, OPTION+' = false'+newline]
    result = ''.join(lines).encode()
    # Independently compare parsed settings: only one option may change.
    after = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=('#', ';'))
    after.read_string(result.decode())
    def settings(parser):
        values = {s: dict(parser[s]) for s in parser.sections()}
        values.setdefault(SECTION, dict(parser.defaults())).pop(OPTION, None)
        return parser.defaults(), values
    if settings(cfg) != settings(after) or after.getboolean(SECTION, OPTION) is not False:
        raise RuntimeError('Unexpected configuration difference; unchanged')
    return result


def regular(path, owner=0):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise RuntimeError('Symlinked configuration path rejected')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_mode & 0o002:
        raise RuntimeError('Configuration ownership/type/permissions need review')
    return info


def replace(path, raw, expected, owner=0):
    info = regular(path, owner)
    if digest(path.read_bytes()) != expected:
        raise RuntimeError('Configuration changed since review')
    fd, temporary = tempfile.mkstemp(prefix='.nova-messaging-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode))
            if os.geteuid() == 0:
                os.fchown(stream.fileno(), info.st_uid, info.st_gid)
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        regular(path, owner)
        if digest(path.read_bytes()) != expected:
            raise RuntimeError('Configuration changed before commit')
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def helpers():
    namespace = {'__name__': 'messaging_units', 'HEALTH_SOURCE': globals()['HEALTH_SOURCE']}
    exec(compile(base64.b64decode(globals()['UNIT_SOURCE']), 'reviewed-units', 'exec'), namespace)
    return namespace


def inspect_service(service, unit_helpers):
    if service not in SERVICES:
        raise RuntimeError('Service outside messaging repair scope')
    run = unit_helpers['run']
    state = unit_helpers['container'](service)
    definition = unit_helpers['unit'](service)
    if not unit_helpers['healthy'](state) or definition['active'] != 'active':
        raise RuntimeError(service+': require a healthy running service before this setting repair')
    fmt = '{"mounts":{{json .Mounts}},"env":{{json .Config.Env}},"started":{{json .State.StartedAt}}}'
    info = json.loads(run(['docker', 'inspect', '--format', fmt, service]))
    if 'KOLLA_CONFIG_STRATEGY=COPY_ALWAYS' not in info['env']:
        raise RuntimeError(service+': require Kolla COPY_ALWAYS')
    mounts = [m for m in info['mounts'] if m.get('Destination', '').rstrip('/') == '/var/lib/kolla/config_files']
    directory = Path('/etc/kolla')/CONFIG_DIRS[service]
    if len(mounts) != 1 or mounts[0].get('Type') != 'bind' or mounts[0].get('Source', '').rstrip('/') != str(directory):
        raise RuntimeError(service+': configuration mount differs')
    config_json = directory/'config.json'; regular(config_json)
    raw_json = config_json.read_bytes(); doc = json.loads(raw_json)
    selected = [v for v in doc.get('config_files', []) if v.get('dest') == '/etc/nova/nova.conf']
    if doc.get('command') != service.replace('_', '-') or len(selected) != 1 or selected[0].get('source') != '/var/lib/kolla/config_files/nova.conf':
        raise RuntimeError(service+': unreviewed Kolla command/configuration mapping')
    path = directory/'nova.conf'; regular(path)
    raw = path.read_bytes(); wanted = patched(raw)
    installed = json.loads(run(['docker', 'exec', service, '/var/lib/kolla/venv/bin/python3', '-c', READER]))
    if installed.get('version') != '18.3.0' or installed.get('driver_sha256') != DRIVER_SHA:
        raise RuntimeError(service+': installed messaging source differs from reviewed 18.3.0')
    if type(installed.get('use_queue_manager')) is not bool or type(installed.get('transient_ttl')) is not int or installed['transient_ttl'] <= 0:
        raise RuntimeError(service+': queue setting/expiry needs review')
    # A previous incomplete change is not silently adopted.
    if installed['config_sha256'] != digest(raw):
        raise RuntimeError(service+': rendered and container configuration differ')
    return {'container': state, 'unit': definition, 'source_sha256': digest(raw),
            'desired_sha256': digest(wanted), 'mapping_sha256': digest(raw_json),
            'installed': installed, 'started': info['started']}


def execute(node, action, service=None, expected=None, cluster=None):
    if node not in NODES or os.geteuid() != 0 or os.environ.get('SUDO_USER') != 'joe' or socket.gethostname().split('.')[0] != node:
        raise RuntimeError('Expected node identity and sudo from joe')
    h = helpers()
    addresses = json.loads(h['run'](['ip', '-j', 'address', 'show', 'dev', 'enp0s31f6']))
    if not any(a.get('local') == NODES[node] for link in addresses for a in link.get('addr_info', [])):
        raise RuntimeError('Management address mismatch')
    host = h['inspector']()['collect'](node)
    if action == 'inspect':
        return {'node': node, 'host': host, 'targets': {s: inspect_service(s, h) for s in SERVICES}}
    if action != 'apply' or service not in SERVICES:
        raise RuntimeError('Unapproved messaging repair action')
    h['dependencies'](host, cluster)
    before = inspect_service(service, h)
    if before != expected:
        raise RuntimeError('Service identity/configuration/startup changed after review')
    if before['installed']['use_queue_manager'] is False:
        raise RuntimeError('Setting already off; no repeated restart')
    path = Path('/etc/kolla')/CONFIG_DIRS[service]/'nova.conf'
    replace(path, patched(path.read_bytes()), before['source_sha256'])
    # COPY_ALWAYS copies the one changed configuration on this controlled restart.
    h['run'](['systemctl', 'restart', before['unit']['name']], timeout=90)
    deadline = time.monotonic()+120
    while True:
        state = h['container'](service)
        if state['id'] != before['container']['id'] or h['unit'](service)['sha256'] != before['unit']['sha256']:
            raise RuntimeError('Service/container replaced during repair')
        if h['healthy'](state):
            result = inspect_service(service, h)
            if result['started'] == before['started'] or result['installed']['use_queue_manager'] is not False or result['source_sha256'] != before['desired_sha256'] or result['mapping_sha256'] != before['mapping_sha256']:
                raise RuntimeError('Restart/new configuration not confirmed')
            return {'node': node, 'service': service, 'target': result}
        if time.monotonic() >= deadline:
            raise RuntimeError('Restart sent; service not healthy within observation window')
        time.sleep(2)


if __name__ == '__main__':
    try:
        p = json.loads(base64.b64decode(globals()['PAYLOAD']))
        result = execute(**p); result['passed'] = True
        print(json.dumps(result))
    except (Exception, KeyboardInterrupt) as error:
        print(json.dumps({'node': globals().get('p', {}).get('node'), 'passed': False,
                         'error': str(error) if isinstance(error, RuntimeError) else type(error).__name__+'; operation incomplete'}))
        raise SystemExit(1)

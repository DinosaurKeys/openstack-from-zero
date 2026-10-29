import base64
import configparser
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('migration_diagnose_test',ROOT/'scripts/phase5-migration-diagnose.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
REQUEST = 'req-e7c72037-04a3-48da-bbf0-1c1410772681'
START, END = '2026-10-27 21:18:00', '2026-10-27 21:24:00'


class MigrationDiagnosticTests(unittest.TestCase):
    def test_correlated_and_window_messages_with_continuation(self):
        raw = '\n'.join([
            '2026-10-27 21:17:00 ERROR unrelated',
            '2026-10-27 21:19:41 INFO ['+REQUEST+'] Host filter only checking host node2',
            '2026-10-27 21:20:41 ERROR MessagingTimeout',
            '  File "/nova/scheduler/rpcapi.py", line 160',
            '2026-10-27 21:25:00 INFO after window',
            'unrelated continuation',
            '2026-10-27 21:30:00 ERROR ['+REQUEST+'] delayed correlated failure'])
        lines = m.excerpt(raw,REQUEST,START,END)['lines']
        self.assertEqual(len(lines),4)
        self.assertTrue(any('delayed correlated failure' in l for l in lines))
        self.assertFalse(any('unrelated' in l for l in lines))

    def test_credentials_and_urls_omitted_output_bounded(self):
        for text in ('password=private','Authorization: Bearer private','amqp://user:private@broker','auth_token=private'):
            self.assertNotIn('private',m.redact(text))
        raw='\n'.join('2026-10-27 21:20:00 ERROR '+('x'*1000) for _ in range(200))
        result=m.excerpt(raw,REQUEST,START,END)
        self.assertEqual(len(result['lines']),100)
        self.assertEqual(result['matched_lines'],200)
        self.assertLessEqual(max(map(len,result['lines'])),700)

    def test_first_failure_survives_a_later_long_timeout_trace(self):
        raw='\n'.join(['2026-10-27 21:19:00 ERROR earlier binding failure']+
          ['2026-10-27 21:20:00 ERROR traceback line '+str(i) for i in range(142)]+
          ['2026-10-27 21:20:41 ERROR final MessagingTimeout'])
        result=m.excerpt(raw,REQUEST,START,END)
        self.assertIn('earlier binding failure',result['lines'][0])
        self.assertIn('final MessagingTimeout',result['lines'][-1])
        self.assertEqual(result['matched_lines'],144)
        self.assertEqual(len(result['lines']),100)
        self.assertEqual(result['omitted_middle_lines'],45)
        self.assertIn('45 matching middle lines omitted',result['lines'][50])

    def test_reply_and_rejection_messages_without_request_context_are_included(self):
        raw='\n'.join([
          '2026-10-27 21:19:01 INFO No calling threads waiting for msg_id : abc',
          '2026-10-27 21:19:02 DEBUG Skipping host: node3 because: earlier failure',
          '2026-10-27 21:19:03 ERROR Binding failed for port example and host node3',
          '2026-10-27 21:25:00 INFO No calling threads waiting for msg_id : outside'])
        lines=m.excerpt(raw,REQUEST,START,END)['lines']
        self.assertEqual(len(lines),3)
        self.assertFalse(any('outside' in line for line in lines))

    def test_log_tail_is_bounded_and_discards_partial_line(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'log'; p.write_bytes(b'x'*(8*1024*1024)+b'\nlast line\n')
            raw,truncated=m.read_tail(p)
            self.assertTrue(truncated); self.assertEqual(raw,'last line\n')

    def test_missing_logs_and_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'nova').mkdir()
            info={'mounts':[{'Destination':'/var/log/kolla/','Type':'volume','Source':str(root)}]}
            with self.assertRaises(RuntimeError):m.log_paths(info,'nova_scheduler')
            (root/'nova/nova-scheduler.log').symlink_to('/etc/passwd')
            with self.assertRaises(RuntimeError):m.log_paths(info,'nova_scheduler')

    def test_plan_does_not_connect_or_change_checkpoints(self):
        with patch.object(m.subprocess,'run',side_effect=AssertionError('unexpected command')), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(m.main(['--request-id',REQUEST,'--start',START,'--end',END]),0)
        self.assertIn('PLAN:',output.getvalue())

    def test_unapproved_container_and_bad_request_rejected(self):
        with patch.object(m,'run',side_effect=AssertionError('unexpected command')):
            with self.assertRaises(RuntimeError):m.container_query('nova_scheduler; reboot')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            m.main(['--request-id','req-; reboot','--start',START,'--end',END])

    def test_collect_all_services_preserves_partial_failure(self):
        commands=[]
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'nova').mkdir(); (root/'rabbitmq').mkdir(); (root/'neutron').mkdir()
            for service in m.SERVICES:
                p=(root/'rabbitmq/rabbit@node1.log' if service=='rabbitmq' else
                   root/'neutron/neutron-server.log' if service=='neutron_server' else
                   root/'nova'/(service.replace('_','-')+'.log'))
                p.write_text('2026-10-27 21:20:41 ERROR ['+REQUEST+'] MessagingTimeout\n')
            def fake(args,timeout=25):
                commands.append(args)
                if args[0]=='ip':return json.dumps([{'addr_info':[{'local':'192.168.0.200'}]}])
                if args[:2]==['docker','exec']:
                    self.assertEqual(args[2:5],['nova_scheduler','/var/lib/kolla/venv/bin/python3','-c'])
                    return json.dumps({'nova_version':'fixture'})
                self.assertEqual(args[:3],['docker','inspect','--format'])
                name=args[-1]
                if name=='nova_conductor':raise subprocess.TimeoutExpired(args,25)
                return json.dumps({'id':'a'*64,'image':'quay.io/openstack.kolla/'+name.replace('_','-')+':2026.2-ubuntu-noble',
                  'state':'running','running':True,'started':START,'restarts':0,
                  'mounts':[{'Destination':'/var/log/kolla','Type':'volume','Source':str(root)}]})
            with patch.object(m,'run',side_effect=fake),patch.object(m.os,'geteuid',return_value=0),patch.dict(os.environ,{'SUDO_USER':'joe'}),patch.object(m.socket,'gethostname',return_value='node1'):
                result=m.collect_node('node1',REQUEST,START,END)
            self.assertFalse(result['complete'])
            self.assertTrue(result['services']['rabbitmq']['collected'])
            self.assertIn('TimeoutExpired',result['services']['nova_conductor']['error'])
            self.assertEqual(len(commands),7)
            self.assertEqual(result['services']['nova_scheduler']['logs'][0]['matched_lines'],1)
            self.assertEqual(result['services']['neutron_server']['logs'][0]['file'],'neutron-server.log')

    def test_remote_payload_executes_collector_and_keeps_password_off_argv(self):
        def transport(argv,**kw):
            self.assertNotIn('private-password',' '.join(argv))
            self.assertEqual(kw['input'],'private-password\n')
            remote=shlex.split(argv[-1]); self.assertEqual(remote[:5],['sudo','-S','-p','','/usr/bin/python3'])
            code=remote[6]
            # Decode the exact emitted program and execute with a simulated node.
            import ast
            tree=ast.parse(code)
            encoded=tree.body[1].value.args[0].args[0].args[0].value
            source=base64.b64decode(encoded).decode()
            ns={}; definitions=source[:source.rindex('\nprint(')]
            exec(compile(definitions,'remote-fixture','exec'),ns)
            calls=[]
            def fake(args,**kw):
                calls.append(args)
                if args[0]=='ip':return subprocess.CompletedProcess(args,0,json.dumps([{'addr_info':[{'local':'192.168.0.201'}]}]),'')
                return subprocess.CompletedProcess(args,1,'','private error withheld')
            with patch.object(subprocess,'run',side_effect=fake),patch.object(os,'geteuid',return_value=0),patch.dict(os.environ,{'SUDO_USER':'joe'}),patch.object(m.socket,'gethostname',return_value='node2'),contextlib.redirect_stdout(io.StringIO()) as out:
                exec(compile(source[source.rindex('\nprint('):],'remote-main','exec'),ns)
            self.assertEqual(len(calls),6)
            self.assertNotIn('private error',out.getvalue())
            return subprocess.CompletedProcess(argv,0,out.getvalue(),'')
        with patch.object(m.subprocess,'run',side_effect=transport):
            result=m.observe('node2','private-password',REQUEST,START,END,{})
        self.assertEqual(result['node'],'node2'); self.assertFalse(result['complete'])

    def test_remote_failure_does_not_expose_stderr(self):
        with patch.object(m.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','password=private')):
            result=m.observe('node3','private',REQUEST,START,END,{})
        self.assertNotIn('password',json.dumps(result))
        self.assertFalse(result['complete'])

    def test_installed_rpc_reader_returns_only_selected_numbers_and_source_facts(self):
        class Config(configparser.ConfigParser):
            def read(self,path):
                self.read_string('[DEFAULT]\nrpc_response_timeout=60\ntransport_url=amqp://private\nlong_rpc_timeout=private\n')
                return [path]
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'rpcapi.py'
            path.write_text('class SchedulerAPI:\n def select_destinations(self, ctxt):\n  client=self.client.prepare(timeout=CONF.long_rpc_timeout)\n  return client.call(ctxt, "select_destinations")\n')
            class Distribution:
                version='fixture-nova'
                def locate_file(self,name):return path
            with patch('configparser.ConfigParser',Config),patch('importlib.metadata.distribution',return_value=Distribution()),contextlib.redirect_stdout(io.StringIO()) as out:
                exec(compile(m.RPC_READER,'rpc-reader','exec'),{})
            result=json.loads(out.getvalue())
            self.assertEqual(result['explicit_RPC_settings']['rpc_response_timeout'],'60')
            self.assertNotIn('private',out.getvalue())
            self.assertEqual(result['RPC_calls'][0]['timeout_arguments'],{'timeout':'CONF.long_rpc_timeout'})

    def test_queue_manager_check_reads_source_and_does_not_trust_version_alone(self):
        legacy='class QManager:\n def __init__(self):\n  self.pg=os.getpgrp()\n def get(self):\n  return self.pg\n'
        fixed=('class QManager:\n raise AssertionError("source must not run")\n'
          ' def __init__(self):\n  pass\n'
          ' @staticmethod\n def _service_identity():\n  return os.getpgrp(),0\n'
          ' def get(self):\n  return self._service_identity()\n')
        cases=[('18.3.0',fixed,'true','present',True),
          ('18.3.1',legacy,'true','legacy construction-time identity',True),
          ('18.3.1','class QManager:\n pass\n','false','unrecognized',False),
          ('18.3.1',fixed,None,'present',None)]
        with tempfile.TemporaryDirectory() as folder:
            rpc=Path(folder)/'rpc.py';driver=Path(folder)/'driver.py'
            rpc.write_text('class SchedulerAPI:\n def select_destinations(self,ctxt):\n  return self.client.call(ctxt,"select_destinations")\n')
            for version,source,enabled,expected,flag in cases:
                with self.subTest(version=version,expected=expected,enabled=enabled):
                    driver.write_text(source)
                    class Config(configparser.ConfigParser):
                        def read(self,path):
                            self.read_string('[DEFAULT]\ndebug=false\ntransport_url=amqp://private\n'+
                              ('[oslo_messaging_rabbit]\nuse_queue_manager='+enabled+'\n' if enabled is not None else ''))
                            return [path]
                    class Distribution:
                        def __init__(self,name):self.name=name;self.version=version if name=='oslo.messaging' else 'fixture-nova'
                        def locate_file(self,name):return driver if self.name=='oslo.messaging' else rpc
                    with patch('configparser.ConfigParser',Config),patch('importlib.metadata.distribution',side_effect=Distribution),contextlib.redirect_stdout(io.StringIO()) as out:
                        exec(compile(m.RPC_READER,'rpc-reader','exec'),{})
                    data=json.loads(out.getvalue())['messaging']
                    self.assertEqual(data['qmanager_identity_fix'],expected)
                    self.assertIs(data['use_queue_manager'],flag)
                    self.assertFalse(data['debug_logging'])
                    self.assertEqual(data['version'],version)
                    self.assertEqual(len(data['amqpdriver_sha256']),64)
                    self.assertNotIn('private',out.getvalue())

    def test_run_prints_source_summary_and_keeps_checkpoints_unchanged(self):
        def observed(node,*args):
            messaging={'version':'18.3.0','use_queue_manager':True,'qmanager_identity_fix':'legacy construction-time identity'}
            return {'node':node,'complete':True,'services':{s:{'installed_RPC':{'messaging':messaging}} for s in ('nova_scheduler','nova_conductor')}}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);checkpoint=root/'private/phase5/menu-migration-state.json';checkpoint.parent.mkdir(parents=True)
            checkpoint.write_text('{"pending":true}')
            with patch.object(m,'REPO',root),patch.object(m.vip.deploy,'source_guard'),patch.object(m.vip.deploy.tools,'tool_environment',return_value={}),patch.object(m.vip.inv,'ensure_agent'),patch.object(m.getpass,'getpass',return_value='hidden'),patch.object(m,'observe',side_effect=observed),patch.object(m.subprocess,'run',side_effect=AssertionError('unexpected command')),contextlib.redirect_stdout(io.StringIO()) as out:
                code=m.main(['--run','--request-id',REQUEST,'--start',START,'--end',END])
            self.assertEqual(code,0)
            self.assertEqual(checkpoint.read_text(),'{"pending":true}')
            self.assertEqual(out.getvalue().count(' | 18.3.0 | ON | MISSING (legacy code)'),6)
            self.assertIn('not a confirmed cause',out.getvalue())
            self.assertEqual(len(list((root/'reports').glob('*.txt'))),1)


if __name__=='__main__':unittest.main()

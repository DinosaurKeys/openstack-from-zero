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
            root=Path(folder); (root/'nova').mkdir(); (root/'rabbitmq').mkdir()
            for service in m.SERVICES:
                p=root/'rabbitmq/rabbit@node1.log' if service=='rabbitmq' else root/'nova'/(service.replace('_','-')+'.log')
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
            self.assertEqual(len(commands),6)
            self.assertEqual(result['services']['nova_scheduler']['logs'][0]['matched_lines'],1)

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
            self.assertEqual(len(calls),5)
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


if __name__=='__main__':unittest.main()

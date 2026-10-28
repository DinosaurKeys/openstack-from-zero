import json
import os
from pathlib import Path
import pty
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'scripts/lab-poweroff.sh'

class PoweroffTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'scripts').mkdir();(self.root/'private/phase5').mkdir(parents=True)
        self.script=self.root/'scripts/lab-poweroff.sh';shutil.copyfile(SOURCE,self.script)
        self.bin=self.root/'bin';self.bin.mkdir();self.trace=self.root/'trace'
        for name,text in {'id':'#!/bin/sh\necho joe\n','hostname':'#!/bin/sh\necho r2d2\n',
          'ssh':'''#!/usr/bin/python3
import json,os,sys
with open(os.environ['POWEROFF_TEST_TRACE'],'a') as f:f.write(json.dumps(sys.argv[1:])+'\\n')
sys.exit(int(os.environ.get('POWEROFF_TEST_SSH_EXIT','0')))
'''}.items():
            p=self.bin/name;p.write_text(text);p.chmod(0o700)
        self.env=os.environ|{'PATH':str(self.bin)+':'+os.environ['PATH'],'POWEROFF_TEST_TRACE':str(self.trace)}
        self.receipt=self.root/'private/phase5/shutdown-20261028T201006Z-test.json'
        self.state={'pending':False,'stage':'services stopped and database verified; manual OS poweroff remains',
          'cluster_uuid':'f3609644-d1e0-11f1-a749-0ef43e7d82dc','database':{n:{'exit_code':0,'uuid':'f3609644-d1e0-11f1-a749-0ef43e7d82dc','seqno':42,'safe_to_bootstrap':int(n=='node3')} for n in ('node1','node2','node3')}}
        self.save()
    def save(self):self.receipt.write_text(json.dumps(self.state));self.receipt.chmod(0o600)
    def run_script(self,mode,answer=None):
        if answer is None:return subprocess.run(['bash',str(self.script),mode],env=self.env,capture_output=True,text=True,timeout=10)
        master,slave=pty.openpty()
        try:
            p=subprocess.Popen(['bash',str(self.script),mode],env=self.env,stdin=slave,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            os.write(master,(answer+'\n').encode());out,err=p.communicate(timeout=10)
            return subprocess.CompletedProcess(p.args,p.returncode,out,err)
        finally:os.close(master);os.close(slave)
    def calls(self):return [json.loads(x) for x in self.trace.read_text().splitlines()] if self.trace.exists() else []
    def test_bash_syntax(self):subprocess.run(['bash','-n',str(SOURCE)],check=True)
    def test_help_does_not_contact_hosts(self):
        self.assertEqual(self.run_script('--help').returncode,0);self.assertEqual(self.calls(),[])
    def test_check_visits_all_three_without_apply(self):
        p=self.run_script('--check');self.assertEqual(p.returncode,0,p.stderr)
        calls=self.calls();self.assertEqual([c[-2] for c in calls],['joe@192.168.0.200','joe@192.168.0.201','joe@192.168.0.202'])
        self.assertTrue(all(c[-1].endswith('--check') for c in calls))
        self.assertIn('No shutdown requested',p.stdout)
    def test_apply_requires_typed_confirmation(self):
        p=self.run_script('--apply',answer='no');self.assertNotEqual(p.returncode,0);self.assertEqual(self.calls(),[])
    def test_apply_sends_three_requests_only_after_confirmation(self):
        p=self.run_script('--apply',answer='POWER OFF ALL NODES');self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(len(self.calls()),3);self.assertTrue(all(c[-1].endswith('--apply') for c in self.calls()))
    def test_pending_receipt_blocks_before_any_ssh(self):
        self.state['pending']=True;self.save();p=self.run_script('--check')
        self.assertNotEqual(p.returncode,0);self.assertEqual(self.calls(),[])
    def test_missing_receipt_blocks(self):
        self.receipt.unlink();self.assertNotEqual(self.run_script('--check').returncode,0);self.assertEqual(self.calls(),[])
    def test_unclean_database_blocks(self):
        self.state['database']['node2']['exit_code']=137;self.save()
        self.assertNotEqual(self.run_script('--check').returncode,0);self.assertEqual(self.calls(),[])
    def test_ssh_failure_is_not_hidden_and_blocks_next_host(self):
        self.env['POWEROFF_TEST_SSH_EXIT']='255';p=self.run_script('--apply',answer='POWER OFF ALL NODES')
        self.assertNotEqual(p.returncode,0);self.assertEqual(len(self.calls()),1);self.assertIn('outcome unknown',p.stderr)
    @unittest.skipUnless(os.geteuid()==0,'remote sudo guard requires root in isolated test')
    def test_remote_guards_reject_failed_queries_or_remaining_workloads(self):
        remote=SOURCE.read_text().split("remote_code=$(cat <<'REMOTE'\n",1)[1].split('\nREMOTE\n)',1)[0]
        # Replace the real shutdown executable before running ANY remote code.
        remote=remote.replace('/usr/sbin/shutdown -h +1','echo TEST_POWEROFF_CALLED')
        (self.bin/'hostname').write_text('#!/bin/sh\necho node1\n')
        docker=self.bin/'docker';docker.write_text('''#!/usr/bin/python3
import os,sys
case=os.environ['POWEROFF_TEST_CASE']
if sys.argv[1:]==['ps','-q']:
    if case=='query-failed':sys.exit(125)
    if case=='container-running':print('container-id')
else:print('exited 137 false' if case=='bad-db' else 'exited 0 false')
''');docker.chmod(0o700)
        ps=self.bin/'ps';ps.write_text('#!/bin/sh\nif [ "$POWEROFF_TEST_CASE" = vm-running ]; then echo qemu-system-x86; else echo bash; fi\n');ps.chmod(0o700)
        for case in ('query-failed','container-running','bad-db','vm-running'):
            p=subprocess.run(['bash','-c',remote,'--','node1','--apply'],env=self.env|{'SUDO_USER':'joe','POWEROFF_TEST_CASE':case},capture_output=True,text=True,timeout=10)
            self.assertNotEqual(p.returncode,0,case);self.assertNotIn('TEST_POWEROFF_CALLED',p.stdout)

if __name__=='__main__':unittest.main()

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'scripts/lab-containers.sh'


class ContainerViewTests(unittest.TestCase):
    def invoke(self,args=(),failed=None,host='r2d2'):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);calls=folder/'calls.jsonl'
            hostname=folder/'hostname';hostname.write_text('#!/bin/sh\nprintf "%s\\n" "$LAB_CONTAINER_LOCAL_HOST"\n');hostname.chmod(0o700)
            ssh=folder/'ssh';ssh.write_text('''#!/usr/bin/python3
import json,os,sys
with open(os.environ['LAB_CONTAINER_CALLS'],'a') as output:output.write(json.dumps(sys.argv[1:])+'\\n')
print('NAMES       STATUS\\nnova_compute Up 1 minute (healthy)')
sys.exit(7 if os.environ.get('LAB_CONTAINER_FAILED') in sys.argv[1:] else 0)
''');ssh.chmod(0o700)
            env=dict(os.environ,PATH=str(folder)+os.pathsep+os.environ['PATH'],LAB_CONTAINER_CALLS=str(calls),
              LAB_CONTAINER_LOCAL_HOST=host,LAB_CONTAINER_FAILED=failed or '')
            result=subprocess.run(['bash',str(SCRIPT),*args],env=env,text=True,capture_output=True,timeout=5)
            values=[json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
            return result,values

    def test_default_reads_all_nodes_with_strict_identity_and_only_docker_ps(self):
        result,calls=self.invoke();self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(len(calls),3)
        for index,args in enumerate(calls):
            node='node'+str(index+1);ip='192.168.0.'+str(index+200)
            self.assertEqual(args[-2],'joe@'+ip);self.assertIn('StrictHostKeyChecking=yes',args);self.assertIn('IdentitiesOnly=yes',args)
            self.assertIn('$(hostname -s)',args[-1]);self.assertIn('!= '+node,args[-1])
            self.assertIn('sudo docker ps -a --format',args[-1]);self.assertNotIn('systemctl',args[-1]);self.assertNotIn('docker exec',args[-1])
        self.assertIn('READ ONLY',result.stdout);self.assertIn('node3 (192.168.0.202)',result.stdout)

    def test_one_failed_read_does_not_hide_the_other_nodes(self):
        result,calls=self.invoke(failed='joe@192.168.0.201')
        self.assertEqual(result.returncode,1);self.assertEqual(len(calls),3);self.assertIn('FAIL node2',result.stderr)
        self.assertIn('node3 (192.168.0.202)',result.stdout)

    def test_single_node_selection_and_wrong_host_are_read_only(self):
        result,calls=self.invoke(['node2']);self.assertEqual(result.returncode,0);self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][-2],'joe@192.168.0.201')
        result,calls=self.invoke(host='node2');self.assertEqual(result.returncode,2);self.assertFalse(calls)

    def test_invalid_selection_and_help_never_connect(self):
        for args in (['node4'],['node2; sudo reboot'],['node1','node2']):
            result,calls=self.invoke(args);self.assertEqual(result.returncode,2);self.assertFalse(calls)
        result,calls=self.invoke(['--help'],host='other');self.assertEqual(result.returncode,0);self.assertFalse(calls)


if __name__=='__main__':unittest.main()

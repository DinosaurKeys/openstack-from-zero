"""Exercise installation boundaries using temporary folders and simulated commands."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('install_tools', ROOT/'scripts/phase2-install-tools.py')
step = importlib.util.module_from_spec(spec)
spec.loader.exec_module(step)
REQUIRED = ['openstack.kolla', 'ansible.netcommon', 'ansible.posix', 'ansible.utils',
            'community.crypto', 'community.general', 'community.docker', 'containers.podman']

class FakeRunner:
    def __init__(self, removal=False, missing=False, failure=None):
        self.calls=[]; self.removal=removal; self.missing=missing; self.failure=failure
    def run(self, args, terminal=False):
        args=list(map(str,args)); self.calls.append((args,terminal))
        if self.failure and self.failure in args:
            raise RuntimeError('simulated download failure')
        if '--simulate' in args:
            return 'Remv critical-package\n' if self.removal else 'Inst python3-venv\n'
        if args[:3]==['/usr/bin/python3','-m','venv']:
            (step.ENV/'bin').mkdir(exist_ok=True)
            (step.ENV/'bin/python').touch(); (step.ENV/'pyvenv.cfg').write_text('fixture')
        if '-c' in args:
            return json.dumps({'kolla':'23.0.0','ansible':'2.21.5',
                               'commit':step.COMMIT,'prefix':str(step.ENV)})
        if '--format' in args:
            names=REQUIRED[:-1] if self.missing else REQUIRED
            return json.dumps({str(step.ENV/'collections/ansible_collections'):{name:{'version':'fixture'} for name in names}})
        if 'freeze' in args:
            return 'ansible-core==2.21.5\n'
        return ''

class InstallTests(unittest.TestCase):
    def test_unknown_existing_environment_and_symlink_are_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); env=root/'kolla'; env.mkdir(); keep=env/'existing'; keep.write_text('keep')
            with self.assertRaisesRegex(RuntimeError,'left unchanged'): step.check_paths(env)
            self.assertEqual(keep.read_text(),'keep')
            alias=root/'alias'; alias.symlink_to(env)
            with self.assertRaisesRegex(RuntimeError,'symlinks'): step.check_paths(alias)

    def test_wrong_host_stops_before_commands(self):
        with patch.object(step.os,'geteuid',return_value=1000), patch.object(step.pwd,'getpwuid') as user, patch.object(step.socket,'gethostname',return_value='node2'), patch.object(step.subprocess,'run') as run:
            user.return_value.pw_name='joe'
            with self.assertRaisesRegex(RuntimeError,'R2d2 only'): step.main(['--apply'])
            run.assert_not_called()

    def test_default_plan_creates_nothing_and_runs_no_commands(self):
        with patch.object(step,'guard'), patch.object(step.subprocess,'run') as run, patch.object(step.Path,'mkdir') as mkdir:
            self.assertEqual(step.main([]),0)
            run.assert_not_called(); mkdir.assert_not_called()

    def test_apt_removal_plan_stops_before_install_or_environment_creation(self):
        with tempfile.TemporaryDirectory() as d, patch.object(step,'ENV',Path(d)/'kolla'):
            runner=FakeRunner(removal=True)
            with self.assertRaisesRegex(RuntimeError,'package removal'): step.install(runner,lambda s:None)
            self.assertFalse(step.ENV.exists())
            self.assertEqual(len(runner.calls),2)

    def test_success_records_versions_collections_and_activation_without_node_commands(self):
        with tempfile.TemporaryDirectory() as d, patch.object(step,'ENV',Path(d)/'kolla'):
            runner=FakeRunner(); lines=[]
            step.install(runner,lines.append)
            self.assertIn('RESULT: R2D2_TOOLS_READY',lines)
            self.assertTrue((step.ENV/'installed-python-packages.txt').is_file())
            self.assertTrue((step.ENV/'installed-ansible-collections.json').is_file())
            self.assertIn('ANSIBLE_COLLECTIONS_PATH=',(step.ENV/'activate-lab.sh').read_text())
            self.assertIn('export LC_ALL=C.UTF-8 LANG=C.UTF-8', (step.ENV/'activate-lab.sh').read_text())
            for args,_ in runner.calls:
                self.assertNotIn('ssh',args); self.assertNotIn('bootstrap-servers',args); self.assertNotIn('deploy',args)
            # Rerun preserves unrelated files and reuses only our marked environment.
            keep=step.ENV/'keep'; keep.write_text('keep')
            step.install(FakeRunner(),lambda s:None)
            self.assertEqual(keep.read_text(),'keep')

    def test_missing_collections_or_failed_install_never_claim_ready(self):
        for runner in (FakeRunner(missing=True),FakeRunner(failure='ansible-core==2.21.5')):
            with tempfile.TemporaryDirectory() as d, patch.object(step,'ENV',Path(d)/'kolla'):
                lines=[]
                with self.assertRaises(RuntimeError): step.install(runner,lines.append)
                self.assertNotIn('RESULT: R2D2_TOOLS_READY',lines)
                self.assertTrue(step.ENV.exists())

    def test_ansible_child_receives_real_utf8_locale_even_from_ascii_terminal(self):
        with patch.dict(os.environ, {'LC_ALL': 'C', 'LANG': 'C', 'PYTHONUTF8': '0'}):
            env = step.tool_environment()
        self.assertEqual(env['LC_ALL'], 'C.UTF-8')
        self.assertEqual(env['LANG'], 'C.UTF-8')
        # Disable Python's coercion/UTF-8 mode so it cannot mask an ASCII locale.
        env.update(PYTHONUTF8='0', PYTHONCOERCECLOCALE='0')
        result = subprocess.run([sys.executable, '-c',
            'import locale; print(locale.getlocale()[1])'],
            env=env, text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout.strip().lower().replace('-', ''), 'utf8')

    def test_wrong_versions_source_or_environment_are_rejected(self):
        observed={'kolla':'23.0.0','ansible':'2.21.5','commit':step.COMMIT,'prefix':str(step.ENV)}
        step.verify_versions(observed)
        for name in observed:
            wrong={**observed,name:'unexpected'}
            with self.assertRaises(RuntimeError): step.verify_versions(wrong)

if __name__=='__main__': unittest.main()

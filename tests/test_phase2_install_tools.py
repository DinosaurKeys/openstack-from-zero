"""Exercise installation boundaries using temporary folders and simulated commands."""
import importlib.util
import io
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
    def run(self, args, terminal=False, quiet=False):
        args=list(map(str,args)); self.calls.append((args,terminal))
        if self.failure and self.failure in args:
            raise RuntimeError('simulated download failure')
        if '--simulate' in args:
            return 'Remv critical-package\n' if self.removal else 'Inst python3-venv\n'
        if args[:3]==['/usr/bin/python3','-m','venv']:
            env=Path(args[3])
            (env/'bin').mkdir(exist_ok=True)
            (env/'bin/python').touch()
            (env/'pyvenv.cfg').write_text('include-system-site-packages = false\n')
        if '-c' in args:
            if 'python-openstackclient' in args[-1]:
                return json.dumps({'client':step.CLIENT_VERSION if (step.CLIENT_ENV/'bin/openstack').exists() else None,
                                   'prefix':str(step.CLIENT_ENV),'base_prefix':'/usr','python':[3,12]})
            return json.dumps({'kolla':'23.0.0','ansible':'2.21.5',
                               'commit':step.COMMIT,'prefix':str(step.ENV)})
        if 'python-openstackclient=='+step.CLIENT_VERSION in args:
            (step.CLIENT_ENV/'bin/openstack').touch()
        if args==[str(step.CLIENT_ENV/'bin/openstack'),'--version']:
            return 'openstack '+step.CLIENT_VERSION+'\n'
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
        with tempfile.TemporaryDirectory() as d, patch.object(step,'ENV',Path(d)/'kolla'), patch.object(step,'CLIENT_ENV',Path(d)/'client'):
            runner=FakeRunner(removal=True)
            with self.assertRaisesRegex(RuntimeError,'package removal'): step.install(runner,lambda s:None)
            self.assertFalse(step.ENV.exists())
            self.assertEqual(len(runner.calls),2)

    def test_success_records_versions_collections_and_activation_without_node_commands(self):
        with tempfile.TemporaryDirectory() as d, patch.object(step,'ENV',Path(d)/'kolla'), patch.object(step,'CLIENT_ENV',Path(d)/'client'):
            runner=FakeRunner(); lines=[]
            step.install(runner,lines.append)
            self.assertIn('RESULT: R2D2_TOOLS_READY',lines)
            self.assertTrue((step.ENV/'installed-python-packages.txt').is_file())
            self.assertTrue((step.ENV/'installed-ansible-collections.json').is_file())
            self.assertTrue((step.CLIENT_ENV/'bin/openstack').is_file())
            self.assertTrue((step.CLIENT_ENV/'installed-python-packages.txt').is_file())
            self.assertEqual(json.loads((step.CLIENT_ENV/step.CLIENT_MARKER).read_text()),step.CLIENT_SPEC)
            self.assertIn('ANSIBLE_COLLECTIONS_PATH=',(step.ENV/'activate-lab.sh').read_text())
            self.assertIn('export LC_ALL=C.UTF-8 LANG=C.UTF-8', (step.ENV/'activate-lab.sh').read_text())
            self.assertIn('export ANSIBLE_CONFIG=/home/joe/venvs/kolla/chapter-c-ansible.cfg', (step.ENV/'activate-lab.sh').read_text())
            for args,_ in runner.calls:
                self.assertNotIn('ssh',args); self.assertNotIn('bootstrap-servers',args); self.assertNotIn('deploy',args)
                if 'python-openstackclient=='+step.CLIENT_VERSION in args:
                    self.assertEqual(args[0],str(step.CLIENT_ENV/'bin/python'))
            # Rerun preserves unrelated files and reuses only our marked environment.
            keep=step.ENV/'keep'; keep.write_text('keep')
            step.install(FakeRunner(),lambda s:None)
            self.assertEqual(keep.read_text(),'keep')

    def test_missing_collections_or_failed_install_never_claim_ready(self):
        for runner in (FakeRunner(missing=True),FakeRunner(failure='ansible-core==2.21.5')):
            with tempfile.TemporaryDirectory() as d, patch.object(step,'ENV',Path(d)/'kolla'), patch.object(step,'CLIENT_ENV',Path(d)/'client'):
                lines=[]
                with self.assertRaises(RuntimeError): step.install(runner,lines.append)
                self.assertNotIn('RESULT: R2D2_TOOLS_READY',lines)
                self.assertTrue(step.ENV.exists())

    def test_client_only_main_installs_without_kolla_apt_sudo_or_cloud_operations(self):
        with tempfile.TemporaryDirectory() as d, patch.object(step,'REPO',Path(d)), patch.object(step,'ENV',Path(d)/'kolla'), patch.object(step,'CLIENT_ENV',Path(d)/'client'), patch.object(step,'guard') as guard, patch.object(step,'Runner',return_value=FakeRunner()) as runner, patch('sys.stdout',new_callable=io.StringIO) as output:
            step.ENV.mkdir(); keep=step.ENV/'keep'; keep.write_bytes(b'unchanged')
            self.assertEqual(step.main(['--client-only','--apply']),0)
            guard.assert_called_once_with(client_only=True)
            self.assertEqual(list(step.ENV.iterdir()),[keep])
            self.assertEqual(keep.read_bytes(),b'unchanged')
            self.assertIn('RESULT: OPENSTACK_CLI_READY',output.getvalue())
            for args,_ in runner.return_value.calls:
                self.assertFalse(any(item in args for item in ('sudo','apt-get','ssh','deploy','post-deploy')))
                self.assertNotEqual(args[0],str(step.ENV/'bin/python'))
                if args[0]==str(step.CLIENT_ENV/'bin/openstack'):
                    self.assertTrue(args[1:]==['--version'] or args[1:3]==['help','server'])

    def test_client_only_plan_does_not_create_files_or_execute_commands(self):
        with patch.object(step,'guard'), patch.object(step.subprocess,'run') as run, patch.object(step.Path,'mkdir') as mkdir, patch('sys.stdout',new_callable=io.StringIO) as output:
            self.assertEqual(step.main(['--client-only']),0)
            self.assertIn('OpenStack CLI '+step.CLIENT_VERSION,output.getvalue())
            run.assert_not_called(); mkdir.assert_not_called()

    def test_client_reuse_preserves_files_and_skips_pinned_install(self):
        with tempfile.TemporaryDirectory() as d, patch.object(step,'CLIENT_ENV',Path(d)/'client'):
            step.install_client(FakeRunner(),lambda s:None)
            keep=step.CLIENT_ENV/'keep'; keep.write_text('keep')
            runner=FakeRunner(); step.install_client(runner,lambda s:None)
            self.assertEqual(keep.read_text(),'keep')
            self.assertFalse(any('install' in args for args,_ in runner.calls))
            self.assertTrue(any('check' in args for args,_ in runner.calls))

    def test_existing_manual_client_environment_is_reused_without_recreation(self):
        with tempfile.TemporaryDirectory() as d, patch.object(step,'CLIENT_ENV',Path(d)/'client'):
            (step.CLIENT_ENV/'bin').mkdir(parents=True)
            (step.CLIENT_ENV/'bin/python').touch(); (step.CLIENT_ENV/'bin/openstack').touch()
            (step.CLIENT_ENV/'pyvenv.cfg').write_text('include-system-site-packages = false\n')
            runner=FakeRunner(); step.install_client(runner,lambda s:None)
            self.assertFalse(any('venv' in args or 'install' in args for args,_ in runner.calls))

    def test_unknown_client_directory_marker_and_system_packages_are_rejected_before_commands(self):
        for mode in ('unknown','wrong-marker','system-packages','symlink','record-symlink','bin-symlink'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as d, patch.object(step,'CLIENT_ENV',Path(d)/'client'):
                step.CLIENT_ENV.mkdir(); keep=step.CLIENT_ENV/'keep'; keep.write_text('keep')
                if mode=='wrong-marker': (step.CLIENT_ENV/step.CLIENT_MARKER).write_text('{}')
                if mode=='system-packages': (step.CLIENT_ENV/'pyvenv.cfg').write_text('include-system-site-packages = true\n')
                if mode=='symlink': (step.CLIENT_ENV/'pyvenv.cfg').symlink_to(keep)
                if mode in ('record-symlink','bin-symlink'):
                    (step.CLIENT_ENV/'pyvenv.cfg').write_text('include-system-site-packages = false\n')
                    (step.CLIENT_ENV/('installed-python-packages.txt' if mode=='record-symlink' else 'bin')).symlink_to(keep)
                runner=FakeRunner()
                with self.assertRaises(RuntimeError): step.install_client(runner,lambda s:None)
                self.assertEqual(runner.calls,[]); self.assertEqual(keep.read_text(),'keep')

    def test_client_failure_never_claims_full_tool_installation_ready(self):
        with tempfile.TemporaryDirectory() as d, patch.object(step,'ENV',Path(d)/'kolla'), patch.object(step,'CLIENT_ENV',Path(d)/'client'):
            lines=[]
            with self.assertRaises(RuntimeError):
                step.install(FakeRunner(failure='python-openstackclient=='+step.CLIENT_VERSION),lines.append)
            self.assertNotIn('RESULT: R2D2_TOOLS_READY',lines)

    def test_client_version_or_dependency_failure_is_not_ready(self):
        for failing_arg in ('--version','check'):
            with self.subTest(failure=failing_arg), tempfile.TemporaryDirectory() as d, patch.object(step,'REPO',Path(d)), patch.object(step,'CLIENT_ENV',Path(d)/'client'), patch.object(step,'guard'), patch.object(step,'Runner',return_value=FakeRunner(failure=failing_arg)), patch('sys.stdout',new_callable=io.StringIO) as output:
                self.assertEqual(step.main(['--client-only','--apply']),1)
                self.assertIn('RESULT: OPENSTACK_CLI_NEEDS_REVIEW',output.getvalue())
                self.assertNotIn('RESULT: OPENSTACK_CLI_READY',output.getvalue())

    def test_client_installer_commands_ignore_inherited_cloud_credentials(self):
        with patch.dict(os.environ,{'OS_PASSWORD':'private','OS_AUTH_URL':'https://example.invalid','PIP_TARGET':'/unexpected','VIRTUAL_ENV':'/other'}):
            env=step.tool_environment()
        self.assertNotIn('OS_PASSWORD',env); self.assertNotIn('OS_AUTH_URL',env)
        self.assertNotIn('PIP_TARGET',env); self.assertNotIn('VIRTUAL_ENV',env)

    def test_help_output_is_quiet_and_failure_output_remains_visible(self):
        for rc in (0,1):
            with self.subTest(rc=rc), tempfile.TemporaryDirectory() as d, patch.object(step,'ENV',Path(d)), patch.object(step.subprocess,'run',return_value=subprocess.CompletedProcess([],rc,stdout='command details\n')):
                lines=[]; runner=step.Runner(lines.append,{})
                if rc:
                    with self.assertRaises(RuntimeError):runner.run(['openstack','help','server','start'],quiet=True)
                    self.assertIn('command details',lines)
                else:
                    self.assertEqual(runner.run(['openstack','help','server','start'],quiet=True),'command details\n')
                    self.assertNotIn('command details',lines)

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

    def test_dedicated_config_is_minimal_idempotent_and_preserves_unknown_content(self):
        with tempfile.TemporaryDirectory() as d, patch.object(step, 'ENV', Path(d)):
            step.prepare_ansible_config()
            path = Path(d) / 'chapter-c-ansible.cfg'
            self.assertEqual(path.read_text(), '# Managed by openstack-from-zero Chapter C\n[defaults]\n')
            self.assertEqual(step.tool_environment()['ANSIBLE_CONFIG'], str(path))
            step.prepare_ansible_config()
            path.write_text('existing user config')
            with self.assertRaisesRegex(RuntimeError, 'left unchanged'):
                step.prepare_ansible_config()
            self.assertEqual(path.read_text(), 'existing user config')

    def test_wrong_versions_source_or_environment_are_rejected(self):
        observed={'kolla':'23.0.0','ansible':'2.21.5','commit':step.COMMIT,'prefix':str(step.ENV)}
        step.verify_versions(observed)
        for name in observed:
            wrong={**observed,name:'unexpected'}
            with self.assertRaises(RuntimeError): step.verify_versions(wrong)

if __name__=='__main__': unittest.main()

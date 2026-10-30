"""Exercise the launcher with local command fixtures; no installer or model calls."""
import json
import os
from pathlib import Path
import pty
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PIN = 'bf0203a794a07aac3ff764972ab9087baa4e4f4e'


class HermesLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hermes test ')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.workspace = self.home / 'work/openstack-from-zero'
        (self.workspace / 'scripts').mkdir(parents=True)
        (self.workspace / 'AI').mkdir()
        self.script = self.workspace / 'scripts/hermes-iac.sh'
        shutil.copyfile(ROOT / 'scripts/hermes-iac.sh', self.script)
        for name in ('HERMES-IAC-RULES.md', 'HERMES-LEARNING-GUIDE.md', 'HERMES-START.md'):
            (self.workspace / 'AI' / name).write_text('public fixture\n')
        self.source = self.home / '.hermes/hermes-agent'
        (self.source / '.git').mkdir(parents=True)
        self.bin = self.home / '.local/bin'
        self.bin.mkdir(parents=True)
        self.log = self.home / 'calls.json'
        self.fixture('id', '#!/bin/bash\ncase "$1" in -un) echo "${TEST_USER:-hermes-iac}";; -u) echo "${TEST_UID:-1100}";; -nG) echo "${TEST_GROUPS:-hermes-iac}";; *) exit 3;; esac\n')
        self.fixture('git', '#!/bin/bash\n[[ "$*" == *"rev-parse HEAD" ]] || exit 4\nprintf "%s\\n" "${TEST_PIN:-' + PIN + '}"\n')
        self.fixture('hermes', '#!' + shutil.which('python3') + '\nimport json, os, sys\nfrom pathlib import Path\nPath(os.environ["TEST_CALL_LOG"]).write_text(json.dumps({"args":sys.argv[1:],"env":dict(os.environ)}))\nprint("FAKE_HERMES_STARTED")\n')
        self.env = dict(os.environ, HOME=str(self.home), PATH=str(self.bin) + os.pathsep + os.environ['PATH'], TEST_CALL_LOG=str(self.log))
        for key in ('TEST_USER', 'TEST_UID', 'TEST_GROUPS', 'TEST_PIN'):
            self.env.pop(key, None)

    def fixture(self, name, text):
        path = self.bin / name
        path.write_text(text)
        path.chmod(0o755)

    def run_script(self, args=(), tty=False):
        cmd = ['bash', str(self.script), *args]
        if not tty:
            return subprocess.run(cmd, env=self.env, capture_output=True, text=True, timeout=5)
        master, slave = pty.openpty()
        try:
            p = subprocess.run(cmd, env=self.env, stdin=slave, stdout=slave, stderr=subprocess.PIPE, text=True, timeout=5)
            return p
        finally:
            os.close(master)
            os.close(slave)

    def test_local_check_never_starts_hermes_or_reads_a_key(self):
        self.env['OPENROUTER_API_KEY'] = 'DO-NOT-PRINT-KEY'
        p = self.run_script(['--check'])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('HERMES_LAUNCHER_CHECK_PASSED', p.stdout)
        self.assertNotIn('DO-NOT-PRINT-KEY', p.stdout + p.stderr)
        self.assertFalse(self.log.exists())

    def test_chat_has_fixed_provider_toolsets_and_workspace(self):
        self.env.update(OS_PASSWORD='PRIVATE-CLOUD-PASSWORD', OS_TOKEN='PRIVATE-TOKEN', GH_TOKEN='PRIVATE-GIT-TOKEN',
                        SSH_AUTH_SOCK='/unrelated/agent', TERMINAL_ENV='ssh', TERMINAL_SSH_HOST='node1')
        p = self.run_script(tty=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        call = json.loads(self.log.read_text())
        self.assertEqual(call['args'], ['chat', '--cli', '--provider', 'openrouter', '--toolsets', 'file,skills,clarify',
                                       '--in', str(self.workspace), '--no-restore-cwd', '--query-file', str(self.workspace / 'AI/HERMES-START.md')])
        self.assertFalse(any(key.startswith('OS_') for key in call['env']))
        for key in ('GH_TOKEN', 'GITHUB_TOKEN', 'SSH_AUTH_SOCK', 'TERMINAL_SSH_HOST'):
            self.assertNotIn(key, call['env'])
        self.assertEqual(call['env']['HERMES_WRITE_SAFE_ROOT'], ':'.join(str(self.workspace / name) for name in ('terraform', 'ansible', 'docs', 'AI')))
        self.assertEqual(call['env']['HERMES_HOME'], str(self.home / '.hermes'))
        self.assertEqual(call['env']['TERMINAL_ENV'], 'local')
        self.assertEqual(call['env']['TERMINAL_CWD'], str(self.workspace))

    def test_normal_management_user_is_rejected(self):
        self.env['TEST_USER'] = 'joe'
        p = self.run_script(['--check'])
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('separate account', p.stderr)
        self.assertFalse(self.log.exists())

    def test_admin_groups_and_root_uid_are_rejected(self):
        for group in ('sudo', 'docker', 'lxd', 'incus-admin'):
            self.env['TEST_GROUPS'] = 'hermes-iac ' + group
            p = self.run_script(['--check'])
            self.assertNotEqual(p.returncode, 0)
        self.env.pop('TEST_GROUPS')
        self.env['TEST_UID'] = '0'
        self.assertNotEqual(self.run_script(['--check']).returncode, 0)
        self.assertFalse(self.log.exists())

    def test_extra_flags_cannot_expand_execution_scope(self):
        for args in (['--toolsets', 'terminal'], ['--check', '--yolo'], ['--resume', 'old-session']):
            p = self.run_script(args)
            self.assertNotEqual(p.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_wrong_source_revision_is_not_accepted(self):
        self.env['TEST_PIN'] = 'different-version'
        p = self.run_script(['--check'])
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('reviewed v0.22.5', p.stderr)

    def test_private_directory_is_not_a_public_workspace(self):
        for name in ('private', 'reports'):
            path = self.workspace / name
            path.mkdir()
            p = self.run_script(['--check'])
            self.assertNotEqual(p.returncode, 0)
            path.rmdir()
        self.assertFalse(self.log.exists())

    def test_symlinked_or_missing_rules_are_rejected(self):
        path = self.workspace / 'AI/HERMES-IAC-RULES.md'
        path.unlink()
        self.assertNotEqual(self.run_script(['--check']).returncode, 0)
        other = self.home / 'elsewhere'
        other.write_text('preserve')
        path.symlink_to(other)
        self.assertNotEqual(self.run_script(['--check']).returncode, 0)
        self.assertEqual(other.read_text(), 'preserve')

    def test_noninteractive_default_does_not_send_model_requests(self):
        p = self.run_script()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('interactive terminal', p.stderr)
        self.assertFalse(self.log.exists())

    def test_unrelated_clone_and_missing_installation_are_rejected(self):
        original = self.bin / 'hermes'
        original.unlink()
        self.assertNotEqual(self.run_script(['--check']).returncode, 0)
        elsewhere = self.home / 'other/scripts'
        elsewhere.mkdir(parents=True)
        new_script = elsewhere / 'hermes-iac.sh'
        shutil.copyfile(self.script, new_script)
        p = subprocess.run(['bash', str(new_script), '--check'], env=self.env, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('Run the launcher from', p.stderr)

    def test_help_is_available_before_account_creation(self):
        self.env['TEST_USER'] = 'joe'
        p = self.run_script(['--help'])
        self.assertEqual(p.returncode, 0)
        self.assertIn('Usage:', p.stdout)
        self.assertFalse(self.log.exists())


if __name__ == '__main__':
    unittest.main()

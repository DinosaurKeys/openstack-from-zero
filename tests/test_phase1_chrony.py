import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('chrony_step', Path(__file__).resolve().parents[1] / 'scripts/phase1-chrony.py')
step = importlib.util.module_from_spec(spec)
spec.loader.exec_module(step)


class ChronyTests(unittest.TestCase):
    def runner(self, installed=False, plan='Remv systemd-timesyncd [255]\nInst chrony (4.5)\n',
               sync=0, leap='Normal', source='^* 192.0.2.1', fail_install=False, other='inactive'):
        calls = []
        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            result, code = '', 0
            if argv[0] == 'dpkg-query':
                result, code = ('install ok installed', 0) if installed else ('not installed', 1)
            elif '--simulate' in argv:
                result = plan
            elif argv[0] == 'apt-get' and 'install' in argv and fail_install:
                raise RuntimeError('installation cancelled')
            elif argv[:2] == ['systemctl', 'is-enabled']:
                result = 'enabled\n'
            elif argv == ['systemctl', 'is-active', 'systemd-timesyncd.service']:
                result, code = other, (0 if other == 'active' else 3)
            elif argv[:2] == ['chronyc', 'waitsync']:
                code = sync
            elif argv == ['chronyc', 'tracking']:
                result = 'Leap status     : ' + leap + '\n'
            elif argv[:3] == ['chronyc', '-n', 'sources']:
                result = source + '\n'
            return subprocess.CompletedProcess(argv, code, result)
        return run, calls

    def execute(self, **kwargs):
        run, calls = self.runner(**kwargs)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            step.prepare(run)
        return calls, output.getvalue()

    def test_install_replaces_only_timesyncd_and_waits_before_success(self):
        calls, output = self.execute()
        args = [a for a, _ in calls]
        install = next((a, k) for a, k in calls if a[0] == 'apt-get' and 'install' in a and '--simulate' not in a)
        self.assertTrue(install[1]['interactive'])
        self.assertNotIn('-y', install[0])
        self.assertIn('Dpkg::Options::=--force-confold', install[0])
        self.assertIn(['chronyc', 'waitsync', '24', '0.1', '0', '2'], args)
        self.assertIn('RESULT: R2D2_CHRONY_READY', output)
        self.assertFalse(any(a[0] in ('ssh', 'ssh-copy-id', 'timedatectl set-ntp') for a in args))

    def test_existing_install_is_not_upgraded_or_reinstalled(self):
        calls, output = self.execute(installed=True)
        self.assertFalse(any(a[0] == 'apt-get' for a, _ in calls))
        self.assertIn('R2D2_CHRONY_READY', output)

    def test_unexpected_removal_stops_before_install_or_service_change(self):
        run, calls = self.runner(plan='Remv openssh-server [1]\n')
        with self.assertRaisesRegex(RuntimeError, 'openssh-server'), contextlib.redirect_stdout(io.StringIO()):
            step.prepare(run)
        self.assertFalse(any(a[0] == 'systemctl' or (a[0] == 'apt-get' and 'install' in a and '--simulate' not in a) for a, _ in calls))

    def test_install_cancelled_does_not_enable_or_report_success(self):
        run, calls = self.runner(fail_install=True)
        output = io.StringIO()
        with self.assertRaises(RuntimeError), contextlib.redirect_stdout(output):
            step.prepare(run)
        self.assertFalse(any(a[0] == 'systemctl' for a, _ in calls))
        self.assertNotIn('R2D2_CHRONY_READY', output.getvalue())

    def test_unsynchronized_or_missing_source_never_reports_ready(self):
        for options in ({'sync': 1}, {'leap': 'Not synchronised'}, {'source': '^? 192.0.2.1'}, {'other': 'active'}):
            with self.subTest(options=options):
                output = io.StringIO()
                run, _ = self.runner(**options)
                with self.assertRaises(RuntimeError), contextlib.redirect_stdout(output):
                    step.prepare(run)
                self.assertNotIn('R2D2_CHRONY_READY', output.getvalue())

    def test_wrong_machine_stops_without_commands(self):
        with patch.object(step.socket, 'gethostname', return_value='node1'), patch.object(step.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'R2d2 only'):
                step.main(['--apply'])
            run.assert_not_called()

    def test_interactive_command_inherits_output_so_prompt_is_visible(self):
        with patch.object(step.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, None)) as run:
            with contextlib.redirect_stdout(io.StringIO()):
                step.command(['apt-get', 'install', 'chrony'], interactive=True)
            self.assertIsNone(run.call_args.kwargs['stdout'])
            self.assertNotIn('stdin', run.call_args.kwargs)

    def test_wrapper_streams_helper_output_and_keeps_private_result_report(self):
        for code in (0, 1):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                sudo = root / 'sudo'
                sudo.write_text('#!/usr/bin/python3\nimport sys\nprint("APT prompt without newline: ", end="", flush=True)\nprint("\\nRESULT: R2D2_CHRONY_READY" if '+str(code)+' == 0 else "\\nInstallation stopped")\nsys.exit('+str(code)+')\n')
                sudo.chmod(0o700)
                output = io.StringIO()
                with patch.object(step, 'require_r2d2'), patch.object(step.os, 'geteuid', return_value=1000), \
                     patch.object(step.pwd, 'getpwuid', return_value=SimpleNamespace(pw_name='joe')), \
                     patch.object(step.sys.stdin, 'isatty', return_value=True), \
                     patch.object(step, 'REPORTS', root / 'reports'), \
                     patch.dict(os.environ, {'PATH': str(root) + ':' + os.environ['PATH']}), \
                     contextlib.redirect_stdout(output):
                    previous = os.umask(0o077)
                    try:
                        self.assertEqual(step.main(['--apply']), code)
                    finally:
                        os.umask(previous)
                report, = (root / 'reports').iterdir()
                self.assertEqual(report.stat().st_mode & 0o777, 0o600)
                self.assertIn('APT prompt without newline:', report.read_text())
                self.assertIn('Read it with: cat ', output.getvalue())
                self.assertEqual('R2D2_CHRONY_NEEDS_REVIEW' in report.read_text(), code != 0)


if __name__ == '__main__':
    unittest.main()

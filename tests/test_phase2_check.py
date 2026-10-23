"""Local management inspection must stay read-only and distinguish inventory from readiness."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('phase2_check', ROOT / 'scripts/phase2-check.py')
step = importlib.util.module_from_spec(spec)
spec.loader.exec_module(step)
MEM = 'MemTotal: 8388608 kB\nMemAvailable: 4194304 kB\n'


class ManagementCheckTests(unittest.TestCase):
    def test_memory_uses_bytes_and_gib_and_refuses_incomplete_input(self):
        self.assertEqual(step.gib(step.memory_values(MEM)['MemTotal']), '8.00 GiB')
        for text in ('MemTotal: 2 kB\n', 'MemTotal: 2 MB\nMemAvailable: 1 kB\n'):
            with self.assertRaises(RuntimeError):
                step.memory_values(text)

    def test_wrong_host_never_runs_commands_or_creates_report(self):
        with patch.object(step.socket, 'gethostname', return_value='node1'), patch.object(step, 'probe') as probe:
            with self.assertRaisesRegex(RuntimeError, 'R2d2 only'):
                step.main([])
            probe.assert_not_called()

    def test_missing_command_is_observation_without_execution(self):
        with patch.object(step.shutil, 'which', return_value=None), patch.object(step.subprocess, 'run') as run:
            text, ok = step.probe(['git', '--version'])
            self.assertTrue(ok)
            self.assertIn('NOT FOUND', text)
            run.assert_not_called()

    def test_probe_keeps_nonzero_and_timeout_evidence(self):
        with patch.object(step.shutil, 'which', return_value='/usr/bin/git'), \
             patch.object(step.subprocess, 'run', return_value=subprocess.CompletedProcess([], 2, '', 'cannot inspect')):
            text, ok = step.probe(['git', '--version'])
            self.assertFalse(ok)
            self.assertIn('exit status: 2', text)
        with patch.object(step.shutil, 'which', return_value='/usr/bin/git'), \
             patch.object(step.subprocess, 'run', side_effect=subprocess.TimeoutExpired('git', 15)):
            text, ok = step.probe(['git', '--version'])
            self.assertFalse(ok)
            self.assertIn('UNAVAILABLE', text)

    def exercise_collection(self, broken_version=False, package_status=1):
        calls, lines = [], []
        def probe(args):
            calls.append(args)
            return ('fixture version', not broken_version) if args[0] != 'dpkg-query' else ('package evidence; exit status: '+str(package_status), package_status == 0)
        with patch.object(step.Path, 'read_text', return_value=MEM), patch.object(step, 'probe', side_effect=probe), \
             patch.object(step.shutil, 'which', return_value=None), \
             patch.object(step.importlib.util, 'find_spec', return_value=None), \
             patch.object(step.importlib.metadata, 'version', side_effect=step.importlib.metadata.PackageNotFoundError):
            code = step.collect(lines.append, 'Ubuntu 24.04 fixture')
        return code, calls, '\n'.join(lines)

    def test_collection_runs_only_local_version_and_package_queries(self):
        code, calls, output = self.exercise_collection()
        self.assertEqual(code, 0)
        self.assertEqual([a[0] for a in calls], ['python3', 'git', 'ssh', 'dpkg-query'])
        self.assertEqual(calls[2], ['ssh', '-V'])
        self.assertIn('R2D2_STATE_COLLECTED', output)
        self.assertIn('not installed in this interpreter; other environments are not ruled out', output)
        self.assertNotIn('READY', output)

    def test_failed_version_probe_requires_review(self):
        code, _, output = self.exercise_collection(broken_version=True)
        self.assertEqual(code, 1)
        self.assertIn('R2D2_STATE_NEEDS_REVIEW', output)

    def test_package_query_error_is_not_a_successful_collection(self):
        code, _, output = self.exercise_collection(package_status=2)
        self.assertEqual(code, 1)
        self.assertIn('R2D2_STATE_NEEDS_REVIEW', output)

    def test_report_is_private_and_failure_still_returns_report_path(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as directory:
                reports = Path(directory) / 'reports'
                def collect(log, pretty):
                    log('Observed machine state')
                    if fail:
                        raise OSError('incomplete fixture evidence')
                    log('RESULT: R2D2_STATE_COLLECTED')
                    return 0
                previous = os.umask(0o077)
                try:
                    with patch.object(step, 'require_r2d2', return_value='Ubuntu fixture'), \
                         patch.object(step, 'REPORTS', reports), patch.object(step, 'collect', side_effect=collect), \
                         contextlib.redirect_stdout(io.StringIO()) as output:
                        self.assertEqual(step.main([]), int(fail))
                finally:
                    os.umask(previous)
                report, = reports.iterdir()
                self.assertEqual(stat.S_IMODE(report.stat().st_mode), 0o600)
                self.assertIn('Read it with: cat ', output.getvalue())
                self.assertEqual('R2D2_STATE_NEEDS_REVIEW' in report.read_text(), fail)

    def test_symlinked_reports_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'reports').symlink_to(root / 'missing')
            with patch.object(step, 'require_r2d2', return_value='Ubuntu fixture'), \
                 patch.object(step, 'REPORTS', root / 'reports'):
                previous = os.umask(0o077)
                try:
                    with self.assertRaisesRegex(RuntimeError, 'real directory'):
                        step.main([])
                finally:
                    os.umask(previous)


if __name__ == '__main__':
    unittest.main()

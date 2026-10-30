"""Offline installer checks. No real downloads, sudo or cloud calls."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('terraform_install_tests', ROOT / 'scripts/phase6-install-terraform.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
VERSION = {'terraform_version': '1.17.1', 'platform': 'linux_amd64'}


def binary(machine=62):
    raw = bytearray(128)
    raw[:6] = b'\x7fELF\x02\x01'
    raw[18:20] = machine.to_bytes(2, 'little')
    return bytes(raw)


def archive_files(folder, wrong_machine=False):
    archive = folder / 'terraform_1.17.1_linux_amd64.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('terraform', binary(183 if wrong_machine else 62))
        z.writestr('../not-extracted', 'never write this')
        z.writestr('LICENSE.txt', 'fixture')
    manifest = folder / 'terraform_1.17.1_SHA256SUMS'
    manifest.write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + '  ' + archive.name + '\n')
    return archive, manifest


class ArchiveTests(unittest.TestCase):
    def test_verified_archive_extracts_only_selected_executable(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            archive, manifest = archive_files(root)
            target = root / 'terraform'
            digest = m.unpack_verified(archive, manifest, 'amd64', target)
            self.assertEqual(target.read_bytes(), binary())
            self.assertEqual(digest, hashlib.sha256(binary()).hexdigest())
            self.assertFalse((root / 'LICENSE.txt').exists())
            self.assertFalse((root.parent / 'not-extracted').exists())

    def test_checksum_mismatch_does_not_extract(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            archive, manifest = archive_files(root)
            manifest.write_text('0' * 64 + '  ' + archive.name + '\n')
            with self.assertRaises(m.InstallError):
                m.unpack_verified(archive, manifest, 'amd64', root / 'terraform')
            self.assertFalse((root / 'terraform').exists())

    def test_duplicate_checksum_entry_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            archive, manifest = archive_files(root)
            manifest.write_text(manifest.read_text() * 2)
            with self.assertRaises(m.InstallError):
                m.unpack_verified(archive, manifest, 'amd64', root / 'terraform')

    def test_wrong_binary_architecture_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            archive, manifest = archive_files(root, wrong_machine=True)
            with self.assertRaises(m.InstallError):
                m.unpack_verified(archive, manifest, 'amd64', root / 'terraform')
            self.assertFalse((root / 'terraform').exists())


class InstallTests(unittest.TestCase):
    def test_identity_guard_runs_before_other_commands(self):
        with patch.object(m.os, 'geteuid', return_value=0), patch.object(m.subprocess, 'run', side_effect=AssertionError('No commands')):
            with self.assertRaises(m.InstallError):
                m.identity()

    def test_new_install_plan_does_not_download_or_install(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / 'terraform'
            with patch.object(m, 'TARGET', target), patch.object(m, 'identity', return_value='amd64'), patch.object(m, 'installation_directory'), patch.object(m.shutil, 'which', return_value=None), patch.object(m.shutil, 'disk_usage', return_value=SimpleNamespace(free=2 * 1024**3)), patch.object(m, 'fetch', side_effect=AssertionError('No download')), patch.object(m.subprocess, 'run', side_effect=AssertionError('No install')):
                value = m.plan()
            self.assertFalse(value['ready'])
            self.assertFalse(target.exists())

    def test_matching_existing_terraform_is_retained(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / 'terraform'
            target.write_bytes(b'existing')
            with patch.object(m, 'TARGET', target), patch.object(m, 'identity', return_value='amd64'), patch.object(m, 'installation_directory'), patch.object(m, 'version_info', return_value=VERSION), patch.object(m, 'fetch', side_effect=AssertionError('No download')):
                value = m.plan()
                self.assertTrue(value['ready'])
                self.assertEqual(m.install(value, lambda _: None), str(target))
            self.assertEqual(target.read_bytes(), b'existing')

    def test_different_existing_version_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / 'terraform'
            target.write_bytes(b'existing')
            with patch.object(m, 'TARGET', target), patch.object(m, 'identity', return_value='amd64'), patch.object(m, 'installation_directory'), patch.object(m, 'version_info', return_value=dict(VERSION, terraform_version='1.16.0')):
                with self.assertRaises(m.InstallError):
                    m.plan()
            self.assertEqual(target.read_bytes(), b'existing')

    def test_symlink_target_is_not_followed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            original = root / 'other'
            original.write_bytes(b'keep')
            target = root / 'terraform'
            target.symlink_to(original)
            with patch.object(m, 'TARGET', target), patch.object(m, 'identity', return_value='amd64'), patch.object(m, 'installation_directory'):
                with self.assertRaises(m.InstallError):
                    m.plan()
            self.assertEqual(original.read_bytes(), b'keep')

    def test_wrong_version_on_path_is_not_shadowed(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(m, 'TARGET', Path(d) / 'terraform'), patch.object(m, 'identity', return_value='amd64'), patch.object(m, 'installation_directory'), patch.object(m.shutil, 'which', return_value='/some/existing/terraform'), patch.object(m, 'version_info', return_value=dict(VERSION, terraform_version='1.16.0')):
                with self.assertRaises(m.InstallError):
                    m.plan()

    def test_install_pipeline_verifies_before_only_local_install_and_keeps_state(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            originals = root / 'fixtures'
            originals.mkdir()
            archive, manifest = archive_files(originals)
            state = root / 'terraform.tfstate'
            state.write_bytes(b'keep existing private state')
            target = root / 'terraform'
            calls = []
            def fetch(url, path, maximum, log):
                self.assertTrue(url.startswith(m.ORIGIN))
                shutil.copyfile(archive if path.name.endswith('.zip') else manifest, path)
            def run(args):
                calls.append(args)
                self.assertEqual(args[:6], ['sudo', '--', '/usr/bin/install', '-m', '0755', '--'])
                shutil.copyfile(args[6], args[7])
                return subprocess.CompletedProcess(args, 0)
            with patch.object(m, 'TARGET', target), patch.object(m, 'fetch', side_effect=fetch), patch.object(m, 'version_info', return_value=VERSION), patch.object(m.subprocess, 'run', side_effect=run):
                result = m.install({'ready': False, 'arch': 'amd64'}, lambda _: None)
            self.assertEqual(result, str(target))
            self.assertEqual(target.read_bytes(), binary())
            self.assertEqual(state.read_bytes(), b'keep existing private state')
            self.assertEqual(len(calls), 1)
            self.assertNotIn('ssh', json.dumps(calls))
            self.assertNotIn('apply', json.dumps(calls))

    def test_checksum_failure_never_reaches_sudo(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            originals = root / 'fixtures'
            originals.mkdir()
            archive, manifest = archive_files(originals)
            manifest.write_text('0' * 64 + '  ' + archive.name + '\n')
            def fetch(url, path, maximum, log):
                shutil.copyfile(archive if path.name.endswith('.zip') else manifest, path)
            with patch.object(m, 'TARGET', root / 'terraform'), patch.object(m, 'fetch', side_effect=fetch), patch.object(m.subprocess, 'run', side_effect=AssertionError('No sudo or execution')):
                with self.assertRaises(m.InstallError):
                    m.install({'ready': False, 'arch': 'amd64'}, lambda _: None)
            self.assertFalse((root / 'terraform').exists())

    def test_external_download_url_is_rejected_without_network(self):
        with patch.object(m.urllib.request, 'build_opener', side_effect=AssertionError('No network')):
            with self.assertRaises(m.InstallError):
                m.fetch('https://example.com/terraform.zip', Path('/unused'), 10, lambda _: None)

    def test_version_query_is_only_local_version_json_and_disables_checkpoint(self):
        p = subprocess.CompletedProcess([], 0, json.dumps(VERSION), '')
        with patch.object(m.subprocess, 'run', return_value=p) as run:
            self.assertEqual(m.version_info(Path('/terraform')), VERSION)
        self.assertEqual(run.call_args.args[0], ['/terraform', 'version', '-json'])
        self.assertEqual(run.call_args.kwargs['env']['CHECKPOINT_DISABLE'], '1')

    def test_check_mode_does_not_install(self):
        with patch.object(m, 'plan', return_value={'ready': False, 'arch': 'amd64', 'binary': '/usr/local/bin/terraform', 'version': '1.17.1'}), patch.object(m, 'install', side_effect=AssertionError('No install')), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(m.main(['--check']), 0)
        self.assertIn('TERRAFORM_INSTALL_PLAN_READY', out.getvalue())


if __name__ == '__main__':
    unittest.main()

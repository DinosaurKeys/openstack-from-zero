"""Offline Chapter C simulations: original evidence/state remain byte-for-byte intact."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('phase6_configure_test', ROOT / 'scripts/phase6-configure.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def uid(n):
    return str(n) * 8 + '-1111-4111-8111-' + str(n) * 12


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('reports', 'private', 'private/phase4', 'private/phase5', 'terraform', 'terraform/phase6'):
            (self.root / name).mkdir(mode=0o700)
        for name in m.FILES:
            (self.root / 'terraform/phase6' / name).write_text('fixture\n')
        self.report = {
            'project_id': 'a' * 32, 'assessed_ram_mb_each': 2048,
            'summary': [{'check': label, 'status': 'PASS'} for label in sorted(m.LABELS)],
            'Existing Ubuntu image': {'id': uid(1), 'name': 'lab-ubuntu-24.04-20261017', 'min_ram': 2048, 'min_disk': 10},
            'Existing guest SSH key': {'name': 'lab-vm-key'},
            'Old lab-vm-01 safely off': {'id': uid(8), 'status': 'SHUTOFF', 'active_hosts': []},
            'Flavor options at assessed RAM': {'ids': [uid(2)], 'ram_mb': 2048},
            'Cinder space and quotas': {'type_name': '__DEFAULT__'},
            'private_decoy': 'DO-NOT-ECHO-REPORT-CONTENTS',
        }
        apis = {'network': 'https://192.168.0.151:9696'}
        self.prepared = {'project_id': 'a' * 32, 'user_id': 'b' * 32, 'apis': apis,
                         'ids': {'image': uid(1), 'flavor': uid(2)}, 'public_key': 'ssh-ed25519 AAAATEST'}
        self.networks = {'project_id': 'a' * 32, 'api': apis['network'], 'ids': {
            'private_network': uid(3), 'private_subnet': uid(4),
            'external_network': uid(5), 'external_subnet': uid(6), 'router': uid(7)}}
        self.old = {'project_id': 'a' * 32, 'user_id': 'b' * 32, 'apis': apis, 'ids': {'server': uid(8)}}
        self.report['Existing networks and free addresses'] = {'ids': copy.deepcopy(self.networks['ids'])}
        self.path = self.root / 'reports/phase6-check-20261030T193626Z-test.json'
        self.save()
        history = self.root / 'private/phase5/old-migration.json'
        history.write_bytes(b'original evidence, never rewritten\n')
        history.chmod(0o600)
        self.patch = patch.object(m, 'REPO', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def save(self):
        for path, value in [(self.path, self.report), *[(self.root / 'private/phase4' / name, value) for name, value in
                [('vm-preparation.json', self.prepared), ('networks.json', self.networks), ('vm.json', self.old)]]]:
            path.write_text(json.dumps(value))
            path.chmod(0o600)

    def status(self, label, value):
        next(r for r in self.report['summary'] if r['check'] == label)['status'] = value

    def run_prepare(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()) as out, patch.object(m.socket, 'create_connection', side_effect=AssertionError('No network')):
            result = m.prepare(**kwargs)
        return result, out.getvalue()

    def originals(self):
        return {p: p.read_bytes() for folder in ('reports', 'private/phase4', 'private/phase5')
                for p in (self.root / folder).glob('*') if p.is_file()}

    def test_full_local_preparation_retains_evidence_and_is_idempotent(self):
        before = self.originals()
        code, output = self.run_prepare()
        self.assertEqual(code, 0)
        self.assertIn('TERRAFORM_CONFIGURATION_READY', output)
        self.assertNotIn('DO-NOT-ECHO-REPORT-CONTENTS', output)
        self.assertEqual(before, self.originals())
        folder = self.root / 'private/phase6'
        values = json.loads((folder / 'lab.tfvars.json').read_text())
        self.assertEqual(values['existing']['flavor_id'], uid(2))
        self.assertEqual(values['existing']['image_id'], uid(1))
        self.assertEqual(values['existing']['private_network_id'], uid(3))
        self.assertNotIn('password', (folder / 'lab.tfvars.json').read_text())
        self.assertEqual((folder / 'backend.tfbackend').read_text(), 'path = ' + json.dumps(str(folder / 'terraform.tfstate')) + '\n')
        self.assertFalse((folder / 'terraform.tfstate').exists())
        self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
        snapshots = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.iterdir()}
        for path in snapshots:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        _, output = self.run_prepare()
        self.assertIn('KEEP ', output)
        self.assertEqual(snapshots, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.iterdir()})

    def test_original_report_size_review_and_running_vm_are_not_rewritten(self):
        self.status('Approved size meets image minimum', 'REVIEW')
        self.status('Old lab-vm-01 safely off', 'REVIEW')
        self.report['Old lab-vm-01 safely off'].update(status='ACTIVE', active_hosts=['node2'])
        self.save()
        before = self.originals()
        _, output = self.run_prepare()
        self.assertIn('lab-vm-01 must be SHUTOFF', output)
        self.assertEqual(before, self.originals())

    def test_existing_terraform_state_is_never_read_or_changed(self):
        folder = self.root / 'private/phase6'
        folder.mkdir(mode=0o700)
        state = folder / 'terraform.tfstate'
        state.write_bytes(b'opaque original state; never parse or replace\x00')
        self.run_prepare()
        self.assertEqual(state.read_bytes(), b'opaque original state; never parse or replace\x00')

    def test_check_mode_does_not_create_files_and_reports_missing(self):
        before = self.originals()
        code, output = self.run_prepare(check=True)
        self.assertEqual(code, 1)
        self.assertIn('NOT_PREPARED', output)
        self.assertFalse((self.root / 'private/phase6').exists())
        self.assertEqual(before, self.originals())
        self.run_prepare()
        code, _ = self.run_prepare(check=True)
        self.assertEqual(code, 0)

    def test_missing_or_duplicate_rows_do_not_look_complete(self):
        self.report['summary'].pop()
        self.save()
        with self.assertRaises(m.ConfigError):
            self.run_prepare()
        self.assertFalse((self.root / 'private/phase6').exists())

    def test_other_failures_and_unknowns_block_configuration(self):
        for status in ('REVIEW', 'BLOCKED', None):
            with self.subTest(status=status):
                self.status('Cluster services and agents', status)
                self.save()
                with self.assertRaises(m.ConfigError):
                    self.run_prepare()
        self.assertFalse((self.root / 'private/phase6').exists())

    def test_network_drift_does_not_reuse_old_report(self):
        self.networks['ids']['private_network'] = uid(9)
        self.save()
        with self.assertRaises(m.ConfigError):
            self.run_prepare()

    def test_different_project_or_pending_old_record_blocks(self):
        for key, value in [('project_id', 'c' * 32), ('pending', 'outcome unknown')]:
            with self.subTest(key=key):
                original = copy.deepcopy(self.old)
                self.old[key] = value
                self.save()
                with self.assertRaises(m.ConfigError):
                    self.run_prepare()
                self.old = original

    def test_image_must_fit_and_have_matching_uuid(self):
        for changes in ({'min_ram': 4096}, {'min_disk': 20}, {'id': uid(9)}):
            with self.subTest(changes=changes):
                original = copy.deepcopy(self.report['Existing Ubuntu image'])
                self.report['Existing Ubuntu image'].update(changes)
                self.save()
                with self.assertRaises(m.ConfigError):
                    self.run_prepare()
                self.report['Existing Ubuntu image'] = original

    def test_only_the_previously_verified_flavor_is_reused(self):
        self.report['Flavor options at assessed RAM']['ids'] = [uid(9)]
        self.save()
        with self.assertRaises(m.ConfigError):
            self.run_prepare()

    def test_malformed_identifier_or_missing_volume_type_is_not_guessed(self):
        self.report['Cinder space and quotas']['type_name'] = None
        self.save()
        with self.assertRaises(m.ConfigError):
            self.run_prepare()
        with self.assertRaises(m.ConfigError):
            m.resource_id('not-an-id')

    def test_different_existing_settings_are_not_overwritten(self):
        self.run_prepare()
        path = self.root / 'private/phase6/lab.tfvars.json'
        path.write_text('previous settings\n')
        with self.assertRaises(m.ConfigError):
            self.run_prepare()
        self.assertEqual(path.read_text(), 'previous settings\n')

    def test_symlinked_inputs_and_outputs_are_rejected(self):
        target = self.root / 'unrelated'
        target.write_text('preserve me')
        self.path.unlink()
        self.path.symlink_to(target)
        with self.assertRaises(m.ConfigError):
            self.run_prepare()
        self.path.unlink()
        self.save()
        folder = self.root / 'private/phase6'
        folder.mkdir(mode=0o700)
        (folder / 'lab.tfvars.json').symlink_to(target)
        with self.assertRaises(m.ConfigError):
            self.run_prepare()
        self.assertEqual(target.read_text(), 'preserve me')

    def test_shared_private_input_is_rejected(self):
        self.path.chmod(0o644)
        with self.assertRaises(m.ConfigError):
            self.run_prepare()

    def test_latest_report_and_explicit_report_selection(self):
        newer = self.path.with_name('phase6-check-20261030T200000Z-new.json')
        newer.write_bytes(self.path.read_bytes())
        newer.chmod(0o600)
        self.assertEqual(m.report_path(None), newer)
        self.assertEqual(m.report_path(str(self.path.relative_to(self.root))), self.path)
        with self.assertRaises(m.ConfigError):
            m.report_path('../outside.json')

    def test_default_does_not_read_or_write_inputs(self):
        with patch.object(m, 'identity', side_effect=AssertionError('No identity probe')), patch.object(m, 'prepare', side_effect=AssertionError('No preparation')), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(m.main([]), 0)
        self.assertIn('PLAN ONLY', out.getvalue())


if __name__ == '__main__':
    unittest.main()

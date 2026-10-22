"""Local fixtures only: no lab SSH, package changes or disk operations."""

import contextlib
import copy
import importlib.util
import io
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "phase1-check.py"
spec = importlib.util.spec_from_file_location("phase1", SCRIPT)
phase1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phase1)


def facts(name, address):
    return {
        "hostname": name, "os": {"id": "ubuntu", "version": "24.04"},
        "addresses": {"ok": True, "value": [{"ifname": "test0", "addr_info": [
            {"family": "inet", "local": address, "prefixlen": 24}]}]},
        "interfaces": [{"name": "test0", "usb": False}],
        "stable_disk_paths": [{"path": "/dev/disk/by-id/fixture", "target": "/dev/fixture"}],
        **{key: {"ok": True, "value": []} for key in
           ("routes", "dns", "disks", "root_mount", "time_sync")},
    }


class InspectionTests(unittest.TestCase):
    def test_identity_mismatch_is_not_a_success(self):
        data = facts("wrong-node", "192.168.0.200")
        self.assertTrue(phase1.assess(data, "node1", "192.168.0.200"))
        data["hostname"] = "node1"
        data["os"]["version"] = "26.04"
        self.assertTrue(phase1.assess(data, "node1", "192.168.0.200"))

    def test_prefix_and_duplicate_addresses_are_rejected(self):
        data = facts("node1", "192.168.0.200")
        entry = data["addresses"]["value"][0]["addr_info"][0]
        entry["prefixlen"] = 16
        self.assertTrue(phase1.assess(data, "node1", "192.168.0.200"))
        entry["prefixlen"] = 24
        duplicate = copy.deepcopy(data["addresses"]["value"][0])
        duplicate["ifname"] = "test1"
        duplicate["addr_info"][0]["prefixlen"] = 32
        data["addresses"]["value"].append(duplicate)
        self.assertTrue(phase1.assess(data, "node1", "192.168.0.200"))

    def test_local_loopback_only_does_not_pass(self):
        data = facts("r2d2", "127.0.0.1")
        data["addresses"]["value"][0]["ifname"] = "lo"
        self.assertTrue(phase1.assess(data, "r2d2"))

    def test_missing_inventory_is_explicit_review(self):
        data = facts("node3", "192.168.0.202")
        data["disks"] = {"ok": False, "error": "missing command"}
        data["stable_disk_paths"] = []
        self.assertIn("disks", phase1.evidence_gaps(data))
        self.assertIn("stable_disk_paths", phase1.evidence_gaps(data))

    def run_fixture(self, directory, ssh=False, tcp_ok=True, bad_login=False):
        def node_data(user, address):
            if bad_login and address.endswith("201"):
                return {"error": "fixture: login refused"}
            name = next(k for k, v in phase1.NODES.items() if v == address)
            return facts(name, address)

        output = io.StringIO()
        with patch.object(phase1, "REPORT_DIR", Path(directory) / "reports"), \
             patch.object(phase1, "collect_local", return_value=facts("r2d2", "10.0.2.15")), \
             patch.object(phase1, "tcp_check", return_value={"ok": tcp_ok, "error": "fixture: unreachable"}), \
             patch.object(phase1, "collect_ssh", side_effect=node_data) as ssh_call, \
             patch.object(phase1.shutil, "which", return_value="/fixture/ssh"), \
             contextlib.redirect_stdout(output):
            result = phase1.main((["--ssh"] if ssh else []) + ["--user", "joe"])
        return result, output.getvalue(), ssh_call.call_count

    def test_unreachable_nodes_never_login_or_claim_success(self):
        with tempfile.TemporaryDirectory() as folder:
            result, output, calls = self.run_fixture(folder, ssh=True, tcp_ok=False)
            self.assertEqual(result, 1)
            self.assertEqual(calls, 0)
            self.assertIn("CHECKS_NEED_REVIEW", output)
            self.assertNotIn("NODE_IDENTITY_CHECKS_PASSED", output)

    def test_login_failure_continues_and_is_not_hidden(self):
        with tempfile.TemporaryDirectory() as folder:
            result, output, calls = self.run_fixture(folder, ssh=True, bad_login=True)
            self.assertEqual((result, calls), (1, 3))
            self.assertIn("fixture: login refused", output)
            self.assertIn("PASS node3 SSH identity", output)
            self.assertNotIn("NODE_IDENTITY_CHECKS_PASSED", output)

    def test_success_report_is_private_and_limited_in_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            result, output, calls = self.run_fixture(folder, ssh=True)
            self.assertEqual((result, calls), (0, 3))
            report = next((Path(folder) / "reports").glob("*.txt"))
            self.assertEqual(stat.S_IMODE(report.stat().st_mode), 0o600)
            self.assertIn("NODE_IDENTITY_CHECKS_PASSED", output)
            self.assertIn("Sudo, disk health and full OpenStack readiness were not tested", report.read_text())

    def test_tcp_mode_does_not_perform_ssh(self):
        with tempfile.TemporaryDirectory() as folder:
            result, output, calls = self.run_fixture(folder)
            self.assertEqual((result, calls), (0, 0))
            self.assertIn("LOCAL_AND_TCP_CHECKS_PASSED", output)
            self.assertNotIn("NODE_IDENTITY_CHECKS_PASSED", output)

    def test_ssh_retains_host_key_checking_and_uses_no_local_shell(self):
        with patch.object(phase1.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = '{"hostname": "node1"}'
            phase1.collect_ssh("joe", "192.168.0.200")
        args, kwargs = run.call_args
        self.assertIn("StrictHostKeyChecking=ask", args[0])
        self.assertEqual(args[0][-1], "python3 -")
        self.assertNotIn("shell", kwargs)
        self.assertIsNone(kwargs.get("stderr"))


if __name__ == "__main__":
    unittest.main()

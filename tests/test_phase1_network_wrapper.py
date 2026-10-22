"""Exercise the real Bash wrapper with a PTY and fake SSH, never lab access."""

import os
from pathlib import Path
import pty
import shutil
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FAKE_SSH = '''#!/usr/bin/env python3
import os, pathlib, sys
destination, command = sys.argv[-2:]
with pathlib.Path(os.environ['USB_FIXTURE_CALLS']).open('a') as output:
    output.write(destination+' '+('apply' if command.startswith('sudo ') else 'reconnect')+'\\n')
if destination.endswith(os.environ.get('USB_FIXTURE_FAIL', 'never')):
    print('fixture: SSH refused', flush=True)
    sys.exit(1)
if command.startswith('sudo '): print('RESULT: USB_NETWORK_READY', flush=True)
else: print('SSH_RECONNECT_OK fixture-node', flush=True)
'''


class WrapperTests(unittest.TestCase):
    def exercise(self, root, args, fail="never"):
        root = Path(root)
        (root / "scripts").mkdir()
        for name in ("phase1-network.sh", "phase1-network-node.py"):
            shutil.copyfile(ROOT / "scripts" / name, root / "scripts" / name)
        bindir = root / "bin"
        bindir.mkdir()
        (bindir / "ssh").write_text(FAKE_SSH)
        (bindir / "ssh").chmod(0o700)
        calls = root / "ssh-calls.txt"
        env = {**os.environ, "PATH": str(bindir)+os.pathsep+os.environ["PATH"],
               "USB_FIXTURE_CALLS": str(calls), "USB_FIXTURE_FAIL": fail}
        master, slave = pty.openpty()
        try:
            result = subprocess.run(["bash", str(root / "scripts/phase1-network.sh"), *args],
                                    stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, env=env, timeout=15)
        finally:
            os.close(master)
            os.close(slave)
        return result, calls.read_text() if calls.exists() else ""

    def test_real_wrapper_selects_all_nodes_and_checks_new_ssh(self):
        with tempfile.TemporaryDirectory() as root:
            result, calls = self.exercise(root, ["--user", "joe", "--apply"])
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(len(calls.splitlines()), 6)
            self.assertEqual(calls.count("reconnect"), 3)
            self.assertIn("RESULT: USB_NETWORK_READY for all", result.stdout)
            report = next((Path(root) / "reports").glob("*.txt"))
            self.assertEqual(stat.S_IMODE(report.stat().st_mode), 0o600)

    def test_real_wrapper_stops_after_failed_node_and_cannot_report_all_ready(self):
        with tempfile.TemporaryDirectory() as root:
            result, calls = self.exercise(root, ["--apply"], fail=".201")
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertNotIn("192.168.0.202", calls)
            self.assertIn("NETWORK_NEEDS_REVIEW at node2", result.stdout)
            self.assertNotIn("USB_NETWORK_READY for all", result.stdout)

    def test_node_selection_and_account_rejection(self):
        with tempfile.TemporaryDirectory() as root:
            result, calls = self.exercise(root, ["--node", "node3", "--apply"])
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(len(calls.splitlines()), 2)
            self.assertTrue(all("192.168.0.202" in line for line in calls.splitlines()))
        with tempfile.TemporaryDirectory() as root:
            result, calls = self.exercise(root, ["--user", "joe;false", "--apply"])
            self.assertEqual(result.returncode, 2)
            self.assertEqual(calls, "")


if __name__ == "__main__":
    unittest.main()

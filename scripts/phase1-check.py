#!/usr/bin/env python3
"""Inspect R2d2 and node access. No package, network or disk configuration."""

import argparse
import datetime as dt
import getpass
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import sys


NODES = {"node1": "192.168.0.200", "node2": "192.168.0.201", "node3": "192.168.0.202"}
REPORT_DIR = Path(__file__).resolve().parent.parent / "reports"

# The same read-only collector runs locally and, with --ssh, on each node.
# Missing commands and partial observations are reported, not installed/fixed.
COLLECTOR = r'''
import getpass, glob, json, os, platform, socket, subprocess
from pathlib import Path

def text(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None

def command(args, as_json=False):
    try:
        p = subprocess.run(args, text=True, capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "error": str(e)}
    if p.returncode:
        return {"ok": False, "error": p.stderr.strip() or "exit " + str(p.returncode)}
    try:
        return {"ok": True, "value": json.loads(p.stdout) if as_json else p.stdout.strip()}
    except ValueError:
        return {"ok": False, "error": "unreadable command JSON"}

release = {}
for line in (text("/etc/os-release") or "").splitlines():
    if "=" in line:
        k, v = line.split("=", 1)
        release[k] = v.strip('"')
addresses = command(["ip", "-j", "address", "show"], True)
interfaces = []
for item in sorted(Path("/sys/class/net").glob("*")):
    device = item / "device"
    interfaces.append({
        "name": item.name, "mac": text(item / "address"),
        "state": text(item / "operstate"), "carrier": text(item / "carrier"),
        "physical": device.exists(),
        "usb": device.exists() and any(p.startswith("usb") for p in device.resolve().parts),
        "device_path": str(device.resolve()) if device.exists() else None,
    })
disk_ids = []
for path in sorted(glob.glob("/dev/disk/by-id/*")):
    item = Path(path)
    if item.is_symlink():
        disk_ids.append({"path": path, "target": str(item.resolve())})
cpu = text("/proc/cpuinfo") or ""
facts = {
    "hostname": socket.gethostname().split(".")[0].lower(),
    "username": getpass.getuser(), "architecture": platform.machine(),
    "os": {"id": release.get("ID"), "version": release.get("VERSION_ID")},
    "addresses": addresses, "interfaces": interfaces,
    "routes": command(["ip", "-j", "route", "show"], True),
    "dns": command(["resolvectl", "dns"]),
    "disks": command(["lsblk", "--json", "--bytes", "--output",
                         "NAME,PATH,TYPE,SIZE,MODEL,SERIAL,FSTYPE,MOUNTPOINTS"], True),
    "root_mount": command(["findmnt", "--json", "--target", "/"], True),
    "stable_disk_paths": disk_ids,
    "time_sync": command(["timedatectl", "show", "--property=NTPSynchronized", "--property=Timezone"]),
    "cpu_virtualization_flag": any(flag in cpu.split() for flag in ("vmx", "svm")),
    "kvm_device_present": Path("/dev/kvm").exists(),
}
print(json.dumps(facts))
'''


def collect_local():
    result = subprocess.run([sys.executable, "-c", COLLECTOR], text=True,
                            capture_output=True, timeout=100)
    if result.returncode:
        raise RuntimeError("Local inspection failed: " + result.stderr.strip())
    return json.loads(result.stdout)


def tcp_check(address):
    try:
        with socket.create_connection((address, 22), timeout=4):
            return {"ok": True}
    except OSError as error:
        return {"ok": False, "error": str(error)}


def collect_ssh(username, address):
    # No shell=True. SSH prints fingerprints/password prompts directly to the
    # terminal. We never collect, store or echo the user's password.
    args = ["ssh", "-T", "-o", "ConnectTimeout=8", "-o", "ConnectionAttempts=1",
            "-o", "StrictHostKeyChecking=ask", "-o", "NumberOfPasswordPrompts=1",
            username + "@" + address, "python3 -"]
    try:
        result = subprocess.run(args, input=COLLECTOR, text=True,
                                stdout=subprocess.PIPE, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"error": str(error)}
    if result.returncode:
        return {"error": "SSH inspection exited " + str(result.returncode) + "; see the SSH message above"}
    try:
        facts = json.loads(result.stdout)
        if not isinstance(facts, dict):
            raise ValueError("expected an object")
        return facts
    except ValueError:
        return {"error": "SSH returned no usable inspection JSON; Python 3 is required on the node"}


def assess(facts, name, address=None):
    """Checks are limited to OS/name/address, never storage safety/readiness."""
    failures = []
    if facts.get("error"):
        return [facts["error"]]
    if facts.get("hostname") != name:
        failures.append("expected hostname " + name + "; observed " + str(facts.get("hostname")))
    if facts.get("os") != {"id": "ubuntu", "version": "24.04"}:
        failures.append("expected Ubuntu 24.04; observed " + str(facts.get("os")))
    addresses = facts.get("addresses") or {}
    if not addresses.get("ok") or not isinstance(addresses.get("value"), list):
        failures.append("could not read IP addresses")
    elif address:
        matches = [entry for nic in addresses["value"] if nic.get("ifname") != "lo"
                   for entry in nic.get("addr_info", [])
                   if entry.get("family") == "inet" and entry.get("local") == address]
        if len(matches) != 1 or matches[0].get("prefixlen") != 24:
            failures.append("expected exactly one " + address + "/24 management address")
    elif not any(entry.get("family") == "inet" for nic in addresses["value"]
                 if nic.get("ifname") != "lo" for entry in nic.get("addr_info", [])):
        failures.append("no non-loopback IPv4 address observed on R2d2")
    return failures


def evidence_gaps(facts):
    """Missing inventory is a review item, not a passed observation."""
    gaps = [key for key in ("routes", "dns", "disks", "root_mount", "time_sync")
            if not isinstance(facts.get(key), dict) or not facts[key].get("ok")]
    if not facts.get("interfaces"):
        gaps.append("interfaces")
    if not facts.get("stable_disk_paths"):
        gaps.append("stable_disk_paths")
    return gaps


def write_report(summary, evidence):
    if REPORT_DIR.is_symlink():
        raise RuntimeError("reports must be a real local directory")
    REPORT_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    path = REPORT_DIR / ("phase1-" + stamp + ".txt")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write("\n".join(summary) + "\n\nObserved facts (JSON):\n")
        json.dump(evidence, output, indent=2)
        output.write("\n")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh", action="store_true", help="also log in to each node and read its facts")
    parser.add_argument("--user", default=getpass.getuser(), help="node SSH account (default: current local account)")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*[$]?", args.user):
        parser.error("--user must be a normal Linux login name")
    if args.ssh and not shutil.which("ssh"):
        parser.error("SSH client missing; install the openssh-client package on R2d2")
    os.umask(0o077)
    summary = ["Phase 1: inspection only",
               "UTC: " + dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")]
    evidence = {"mode": "ssh" if args.ssh else "local-and-tcp", "nodes": {}}
    failed = False
    try:
        local = collect_local()
        evidence["management"] = local
        errors = assess(local, "r2d2")
        failed = bool(errors)
        summary.append(("FAIL" if errors else "PASS") + " R2d2 OS/name/address inspection")
        summary.extend("  " + item for item in errors)
        # Show enough local network information to review NAT without opening JSON.
        for nic in local.get("addresses", {}).get("value", []) or []:
            for entry in nic.get("addr_info", []):
                if entry.get("family") == "inet" and nic.get("ifname") != "lo":
                    summary.append("  R2d2 " + nic["ifname"] + ": " + entry["local"] + "/" + str(entry["prefixlen"]))
        for name, address in NODES.items():
            reach = tcp_check(address)
            evidence["nodes"][name] = {"address": address, "tcp22": reach}
            if not reach["ok"]:
                failed = True
                summary.append("FAIL " + name + " " + address + " TCP 22: " + reach["error"])
                continue
            summary.append("PASS " + name + " " + address + " TCP 22")
            if args.ssh:
                print("Inspecting " + name + " as " + args.user + " (SSH may prompt)", flush=True)
                facts = collect_ssh(args.user, address)
                evidence["nodes"][name]["facts"] = facts
                errors = assess(facts, name, address)
                failed = failed or bool(errors)
                summary.append(("FAIL" if errors else "PASS") + " " + name + " SSH identity/OS/address")
                summary.extend("  " + item for item in errors)
                if not facts.get("error"):
                    gaps = evidence_gaps(facts)
                    if gaps:
                        summary.append("REVIEW " + name + " incomplete evidence: " + ", ".join(gaps))
        if failed:
            summary.append("RESULT: CHECKS_NEED_REVIEW")
        else:
            summary.append("RESULT: " + ("NODE_IDENTITY_CHECKS_PASSED" if args.ssh else "LOCAL_AND_TCP_CHECKS_PASSED"))
        summary.append("REVIEW: NAT/DHCP details, NIC assignments and disk ownership still require review.")
        summary.append("Sudo, disk health and full OpenStack readiness were not tested.")
        path = write_report(summary, evidence)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print("Inspection stopped: " + str(error), file=sys.stderr)
        return 1
    print("\n".join(summary))
    print("Private combined report: " + str(path))
    print("Read it with: cat " + shlex.quote(str(path)))
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInspection interrupted; no node configuration was applied.", file=sys.stderr)
        sys.exit(130)

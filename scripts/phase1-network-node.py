#!/usr/bin/env python3
"""Node-side USB preparation; invoked by phase1-network.sh through SSH/sudo."""

import argparse
import copy
import datetime as dt
import fnmatch
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

import yaml  # Ubuntu's Netplan dependency on the node, not a pip installation.


NODES = {
    "node1": ("192.168.0.200", "enx00e04c557e58", "00:e0:4c:55:7e:58"),
    "node2": ("192.168.0.201", "enx00e04c2c1678", "00:e0:4c:2c:16:78"),
    "node3": ("192.168.0.202", "enx00e04c304688", "00:e0:4c:30:46:88"),
}
MGMT = "enp0s31f6"
HEADER = "# Managed by openstack-from-zero: Phase 1 USB provider NIC\n"


class PreparationError(RuntimeError):
    pass


class System:
    def __init__(self, etc=Path("/etc/netplan"), backups=Path("/var/lib/openstack-from-zero/netplan")):
        self.etc, self.backups = etc, backups

    def run(self, args):
        p = subprocess.run(args, text=True, capture_output=True, timeout=30,
                           env={**os.environ, "LC_ALL": "C"})
        if p.returncode:
            raise PreparationError(args[0] + " failed: " + p.stderr.strip())
        return p.stdout

    def netplan(self):
        data = yaml.safe_load(self.run(["netplan", "get"]))
        if not isinstance(data, dict) or not isinstance(data.get("network"), dict):
            raise PreparationError("Cannot read the effective Netplan configuration")
        return data

    def snapshot(self, usb):
        interfaces = json.loads(self.run(["ip", "-j", "address", "show"]))
        links = {x["ifname"]: x for x in interfaces}
        if MGMT not in links or usb not in links:
            raise PreparationError("Expected management/USB interface is missing")
        mgmt, provider = links[MGMT], links[usb]
        routes = json.loads(self.run(["ip", "-j", "route", "show", "dev", MGMT]))
        normalized = sorted((x.get("dst", "default"), x.get("gateway", ""),
                             x.get("prefsrc", ""), x.get("metric", 0)) for x in routes)
        dns_line = self.run(["resolvectl", "dns", MGMT]).strip()
        dns = dns_line.split(":", 1)[1].split() if ":" in dns_line else []
        def read(name):
            try:
                return (Path("/sys/class/net") / usb / name).read_text().strip()
            except OSError:
                return None
        device = Path("/sys/class/net") / usb / "device"
        return {
            "management": {"mac": mgmt["address"],
                "ipv4": sorted((a["local"], a["prefixlen"]) for a in mgmt.get("addr_info", []) if a["family"] == "inet"),
                "routes": normalized, "dns": dns},
            "usb_mac": provider["address"].lower(), "usb_up": "UP" in provider.get("flags", []),
            "usb_addresses": provider.get("addr_info", []),
            "usb_master": provider.get("master"),
            "usb_routes": json.loads(self.run(["ip", "-j", "route", "show", "dev", usb])),
            "usb_device": device.exists() and any(p.startswith("usb") for p in device.resolve().parts),
            "carrier": read("carrier"), "speed": read("speed"), "duplex": read("duplex"),
        }

    def generate(self):
        self.run(["netplan", "generate"])

    def trial(self):
        return subprocess.run(["netplan", "try", "--timeout", "120"], check=False).returncode

    def restore_runtime(self, usb, was_up):
        self.generate()
        self.run(["netplan", "apply"])
        if not was_up:
            self.run(["ip", "link", "set", "dev", usb, "down"])

    def post_state(self, usb):
        for _ in range(15):
            state = self.snapshot(usb)
            if state["usb_up"] and state["carrier"] == "1":
                return state
            time.sleep(1)
        return state


def provider_definition(mac):
    return {"match": {"macaddress": mac}, "renderer": "networkd",
            "dhcp4": False, "dhcp6": False, "accept-ra": False,
            "link-local": [], "addresses": [], "optional": True}


def provider_id(config, usb, mac):
    network = config["network"]
    if network.get("renderer", "networkd") != "networkd":
        raise PreparationError("This step expects networkd; no renderer change will be made")
    matches = []
    for name, value in (network.get("ethernets") or {}).items():
        value = value or {}
        match = value.get("match") or {}
        if (name == usb or str(match.get("macaddress", "")).lower() == mac
                or fnmatch.fnmatch(usb, match.get("name", "__no_match__"))):
            matches.append((name, value))
    if len(matches) > 1:
        raise PreparationError("Several Netplan definitions match the USB NIC; review required")
    key, current = matches[0] if matches else ("lab_provider_usb", {})
    if not matches and key in (network.get("ethernets") or {}):
        raise PreparationError("lab_provider_usb already belongs to a different device")
    allowed = {"match", "renderer", "dhcp4", "dhcp6", "accept-ra", "link-local", "addresses", "optional"}
    if set(current) - allowed or current.get("addresses") or current.get("dhcp4") or current.get("dhcp6"):
        raise PreparationError("USB already has other network settings; review before modifying")
    if current.get("renderer", "networkd") != "networkd":
        raise PreparationError("USB is not managed by networkd")
    current_match = current.get("match") or {}
    if current_match.get("macaddress") and str(current_match["macaddress"]).lower() != mac:
        raise PreparationError("Existing USB definition matches a different MAC")
    if set(current_match) - {"macaddress", "name"}:
        raise PreparationError("Existing USB match has other conditions; review required")
    if current_match.get("name") not in (None, usb):
        raise PreparationError("A broad interface-name match must be reviewed before modification")
    for section in ("bridges", "bonds", "vlans", "vrfs"):
        for definition in (network.get(section) or {}).values():
            refs = ((definition or {}).get("interfaces") or []) + [(definition or {}).get("link")]
            if key in refs or usb in refs:
                raise PreparationError("USB is already used by " + section)
    return key


def without_provider(config, key):
    result = copy.deepcopy(config)
    ethernet = result["network"].get("ethernets") or {}
    ethernet.pop(key, None)
    if ethernet:
        result["network"]["ethernets"] = ethernet
    else:
        result["network"].pop("ethernets", None)
    return result


def validate_effective_provider(effective, mac):
    # Netplan may omit empty sequences or default false values when serializing.
    # The written fragment is explicit; reject any effective contrary setting.
    if str((effective.get("match") or {}).get("macaddress", "")).lower() != mac:
        raise PreparationError("Merged provider MAC differs from the candidate")
    if effective.get("renderer", "networkd") != "networkd":
        raise PreparationError("Merged provider renderer differs from networkd")
    if any(effective.get(k, False) is not False for k in ("dhcp4", "dhcp6", "accept-ra")):
        raise PreparationError("Merged provider enables host address autoconfiguration")
    if effective.get("addresses") or effective.get("link-local"):
        raise PreparationError("Merged provider enables host addresses")
    if effective.get("optional", False) is not True:
        raise PreparationError("Merged provider must not delay host boot")


def validate_state(state, address, mac, after=False):
    mgmt = state["management"]
    if mgmt["ipv4"] != [(address, 24)]:
        raise PreparationError("Management IP does not match the reviewed node address")
    defaults = [r for r in mgmt["routes"] if r[0] == "default"]
    if len(defaults) != 1 or defaults[0][1] != "192.168.0.1" or mgmt["dns"] != ["192.168.0.1"]:
        raise PreparationError("Management gateway/DNS differ from the reviewed settings")
    if state["usb_mac"] != mac or not state["usb_device"]:
        raise PreparationError("USB MAC/device identity mismatch")
    if state["usb_addresses"]:
        raise PreparationError("USB NIC has host addresses; review required")
    if state.get("usb_master") or state.get("usb_routes"):
        raise PreparationError("USB already has a master device or routes; review required")
    if after and (not state["usb_up"] or state["carrier"] != "1"):
        raise PreparationError("USB interface is not UP with a detected physical link")


def write_private(path, content):
    fd, name = tempfile.mkstemp(prefix=".provider-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            output.write(content)
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def configure(name, apply=False, system=None):
    system = system or System()
    address, usb, mac = NODES[name]
    before = system.snapshot(usb)
    validate_state(before, address, mac)
    config_before = system.netplan()
    key = provider_id(config_before, usb, mac)
    desired = provider_definition(mac)
    existing = (config_before["network"].get("ethernets") or {}).get(key) or {}
    if (existing.get("match") or {}).get("name"):
        desired["match"]["name"] = usb
    config = {"network": {"version": 2, "ethernets": {key: desired}}}
    content = HEADER + yaml.safe_dump(config, sort_keys=False)
    target = system.etc / "90-openstack-provider.yaml"
    if system.etc.is_symlink() or target.is_symlink():
        raise PreparationError("Netplan path must not be a symlink")
    for path in (Path("/run/netplan") / target.name, Path("/lib/netplan") / target.name):
        if path.exists():
            raise PreparationError("A second file shadows " + str(target))
    previous = target.read_text() if target.exists() else None
    if previous is not None and previous != content:
        raise PreparationError("The target file already differs; it will not be overwritten")
    print("Node: " + name + " | management: " + MGMT + " " + address + "/24", flush=True)
    print("USB: " + usb + " | MAC: " + mac + " | keeps its current name", flush=True)
    print("USB-only candidate: " + str(target) + "\n" + content, flush=True)
    if not apply:
        print("RESULT: PREVIEW_ONLY — node files and runtime unchanged", flush=True)
        return
    if previous == content and before["usb_up"] and before["carrier"] == "1":
        validate_state(before, address, mac, after=True)
        print("RESULT: USB_NETWORK_READY (already configured)", flush=True)
        return
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup = system.backups / stamp
    backup.mkdir(mode=0o700, parents=True)
    shutil.copytree(system.etc, backup / "etc-netplan", symlinks=True)
    print("Private backup: " + str(backup / "etc-netplan"), flush=True)
    trial_started = False
    try:
        write_private(target, content)
        system.generate()
        merged = system.netplan()
        if without_provider(merged, key) != without_provider(config_before, key):
            raise PreparationError("A non-USB Netplan definition changed; refusing activation")
        effective = merged["network"].get("ethernets", {}).get(key, {})
        validate_effective_provider(effective, mac)
        print("Netplan trial: when asked, press Enter to keep the USB settings.", flush=True)
        print("If the session becomes unresponsive, let the 120-second trial expire.", flush=True)
        trial_started = True
        if system.trial() != 0:
            raise PreparationError("Netplan trial was not accepted")
        after = system.post_state(usb)
        validate_state(after, address, mac, after=True)
        if after["management"] != before["management"]:
            raise PreparationError("Management address/routes/DNS changed")
        print("PASS management IP, routes and DNS preserved", flush=True)
        print("PASS USB UP, physical link detected, no host addresses", flush=True)
        print("Observed USB speed: " + str(after["speed"]) + " Mb/s; duplex: " + str(after["duplex"]), flush=True)
        print("RESULT: USB_NETWORK_READY", flush=True)
    except BaseException:
        # Restore only our fragment. Other existing Netplan files were never edited.
        if previous is None:
            target.unlink(missing_ok=True)
        else:
            write_private(target, previous)
        try:
            if trial_started:
                system.restore_runtime(usb, before["usb_up"])
            else:
                system.generate()
            restored = system.snapshot(usb)
            if restored["management"] != before["management"]:
                raise PreparationError("Management restoration needs console review")
            print("RESTORED previous provider file and management settings", flush=True)
        except BaseException as error:
            print("RESTORE NEEDS CONSOLE REVIEW: " + str(error), flush=True)
        print("Backup remains at: " + str(backup / "etc-netplan"), flush=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("node", choices=NODES)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    if os.geteuid() != 0 or socket.gethostname().split(".")[0] != args.node:
        parser.error("Requires sudo on the exact intended node")
    if release.get("ID", "").strip('"') != "ubuntu" or release.get("VERSION_ID", "").strip('"') != "24.04":
        parser.error("Expected Ubuntu 24.04")
    if args.apply and not sys.stdin.isatty():
        parser.error("Apply requires an interactive SSH terminal for the Netplan trial")
    system = System()
    def interrupted(signum, frame):
        raise PreparationError("Interrupted by signal " + str(signum))
    signal.signal(signal.SIGHUP, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    try:
        system.run(["systemctl", "is-active", "systemd-networkd"])
        configure(args.node, args.apply, system)
        return 0
    except (Exception, KeyboardInterrupt) as error:
        print("RESULT: USB_NETWORK_NEEDS_REVIEW — " + str(error), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

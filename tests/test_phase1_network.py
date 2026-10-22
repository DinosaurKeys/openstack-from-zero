"""Temporary local files and fake network commands only; never lab SSH/apply."""

import contextlib
import copy
import importlib.util
import io
from pathlib import Path
import stat
import tempfile
import unittest

import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "phase1-network-node.py"
spec = importlib.util.spec_from_file_location("phase1_network", SCRIPT)
network = importlib.util.module_from_spec(spec)
spec.loader.exec_module(network)


def config_fixture():
    return {"network": {"version": 2, "ethernets": {
        "enp0s31f6": {"addresses": ["192.168.0.200/24"],
                       "nameservers": {"addresses": ["192.168.0.1"]},
                       "routes": [{"to": "default", "via": "192.168.0.1"}]}}}}


def state_fixture(name="node1"):
    address, usb, mac = network.NODES[name]
    return {"management": {"mac": "00:23:24:eb:e0:0b", "ipv4": [(address, 24)],
            "routes": [("default", "192.168.0.1", "", 0), ("192.168.0.0/24", "", address, 0)],
            "dns": ["192.168.0.1"]},
            "usb_mac": mac, "usb_up": False, "usb_addresses": [], "usb_device": True,
            "carrier": None, "speed": None, "duplex": None}


class FakeSystem(network.System):
    def __init__(self, root, name="node1"):
        super().__init__(Path(root) / "etc-netplan", Path(root) / "backups")
        self.etc.mkdir()
        self.base = config_fixture()
        if name != "node1":
            self.base["network"]["ethernets"][network.MGMT]["addresses"] = [network.NODES[name][0]+"/24"]
        (self.etc / "50-installer.yaml").write_text(yaml.safe_dump(self.base))
        self.original_file = (self.etc / "50-installer.yaml").read_bytes()
        self.state = state_fixture(name)
        self.saved = copy.deepcopy(self.state)
        self.generations = self.trials = self.restores = 0
        self.trial_exit = 0
        self.generate_error = False
        self.post_failure = None

    def snapshot(self, usb):
        return copy.deepcopy(self.state)

    def netplan(self):
        result = copy.deepcopy(self.base)
        path = self.etc / "90-openstack-provider.yaml"
        if path.exists():
            fragment = yaml.safe_load(path.read_text())
            if isinstance(fragment, dict):
                for key, value in fragment["network"]["ethernets"].items():
                    result["network"]["ethernets"][key] = {
                        **result["network"]["ethernets"].get(key, {}), **value}
        return result

    def generate(self):
        self.generations += 1
        if self.generate_error:
            self.generate_error = False
            raise network.PreparationError("fixture invalid Netplan")

    def trial(self):
        self.trials += 1
        if self.trial_exit == 0:
            self.state.update(usb_up=True, carrier="1", speed="1000", duplex="full")
        return self.trial_exit

    def post_state(self, usb):
        if self.post_failure == "gateway":
            self.state["management"]["routes"][0] = ("default", "192.168.0.99", "", 0)
        elif self.post_failure == "address":
            self.state["usb_addresses"] = [{"family": "inet6", "local": "fe80::1"}]
        elif self.post_failure == "carrier":
            self.state["carrier"] = "0"
        return self.snapshot(usb)

    def restore_runtime(self, usb, was_up):
        self.restores += 1
        self.state = copy.deepcopy(self.saved)


class UsbPreparationTests(unittest.TestCase):
    def configure(self, system, apply=True, name="node1"):
        with contextlib.redirect_stdout(io.StringIO()):
            network.configure(name, apply, system)

    def test_all_three_candidates_keep_management_files_and_disable_addressing(self):
        for name, (address, usb, mac) in network.NODES.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as root:
                system = FakeSystem(root, name)
                self.configure(system, name=name)
                self.assertEqual((system.etc / "50-installer.yaml").read_bytes(), system.original_file)
                target = system.etc / "90-openstack-provider.yaml"
                fragment = yaml.safe_load(target.read_text())
                config = fragment["network"]["ethernets"]["lab_provider_usb"]
                self.assertEqual(config["match"], {"macaddress": mac})
                self.assertFalse(config["dhcp4"])
                self.assertFalse(config["dhcp6"])
                self.assertFalse(config["accept-ra"])
                self.assertEqual(config["link-local"], [])
                self.assertEqual(config["addresses"], [])
                self.assertNotIn("set-name", config)
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
                self.assertEqual(system.trials, 1)

    def test_preview_has_no_file_or_runtime_changes(self):
        with tempfile.TemporaryDirectory() as root:
            system = FakeSystem(root)
            self.configure(system, apply=False)
            self.assertFalse((system.etc / "90-openstack-provider.yaml").exists())
            self.assertFalse(system.backups.exists())
            self.assertEqual((system.trials, system.generations, system.restores), (0, 0, 0))

    def test_identity_and_address_guards_run_before_writes(self):
        for field, value in (("usb_mac", "00:00:00:00:00:00"), ("usb_device", False),
                             ("usb_addresses", [{"local": "192.168.0.9"}])):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as root:
                system = FakeSystem(root)
                system.state[field] = value
                with self.assertRaises(network.PreparationError): self.configure(system)
                self.assertFalse(system.backups.exists())

    def test_management_gateway_and_dns_mismatch_refuse_before_writes(self):
        with tempfile.TemporaryDirectory() as root:
            system = FakeSystem(root)
            system.state["management"]["dns"] = ["8.8.8.8"]
            with self.assertRaises(network.PreparationError): self.configure(system)
            self.assertFalse(system.backups.exists())

    def test_runtime_usb_master_or_routes_are_refused_before_writes(self):
        for field, value in (("usb_master", "br-ex"), ("usb_routes", [{"dst": "default"}])):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as root:
                system = FakeSystem(root)
                system.state[field] = value
                with self.assertRaises(network.PreparationError): self.configure(system)
                self.assertFalse(system.backups.exists())

    def test_usb_in_bridge_or_vlan_is_refused(self):
        for section, cfg in (("bridges", {"interfaces": ["enx00e04c557e58"]}),
                             ("vlans", {"link": "enx00e04c557e58", "id": 42})):
            with self.subTest(section=section), tempfile.TemporaryDirectory() as root:
                system = FakeSystem(root)
                system.base["network"][section] = {"existing": cfg}
                with self.assertRaises(network.PreparationError): self.configure(system)
                self.assertFalse(system.backups.exists())

    def test_duplicate_and_broad_matches_are_refused(self):
        for ethernet in ({"a": {"match": {"name": "en*"}}},
                         {"a": {"match": {"macaddress": network.NODES["node1"][2]}},
                          "b": {"match": {"name": network.NODES["node1"][1]}}}):
            with self.subTest(ethernet=ethernet), tempfile.TemporaryDirectory() as root:
                system = FakeSystem(root)
                system.base["network"]["ethernets"].update(ethernet)
                with self.assertRaises(network.PreparationError): self.configure(system)
                self.assertFalse(system.backups.exists())

    def test_existing_empty_usb_definition_is_reused_without_rename(self):
        with tempfile.TemporaryDirectory() as root:
            system = FakeSystem(root)
            usb = network.NODES["node1"][1]
            system.base["network"]["ethernets"][usb] = {"dhcp4": False, "dhcp6": False}
            self.configure(system)
            definition = yaml.safe_load((system.etc / "90-openstack-provider.yaml").read_text())
            self.assertIn(usb, definition["network"]["ethernets"])

    def test_invalid_generation_restores_file_without_activating_network(self):
        with tempfile.TemporaryDirectory() as root:
            system = FakeSystem(root)
            system.generate_error = True
            with self.assertRaises(network.PreparationError): self.configure(system)
            self.assertFalse((system.etc / "90-openstack-provider.yaml").exists())
            self.assertEqual((system.trials, system.restores), (0, 0))

    def test_rejected_trial_restores_previous_file_and_runtime(self):
        with tempfile.TemporaryDirectory() as root:
            system = FakeSystem(root)
            system.trial_exit = 1
            with self.assertRaises(network.PreparationError): self.configure(system)
            self.assertFalse((system.etc / "90-openstack-provider.yaml").exists())
            self.assertEqual(system.restores, 1)
            self.assertEqual(system.state, system.saved)

    def test_post_trial_failures_restore_runtime_and_are_not_success(self):
        for failure in ("gateway", "address", "carrier"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as root:
                system = FakeSystem(root)
                system.post_failure = failure
                with self.assertRaises(network.PreparationError): self.configure(system)
                self.assertEqual(system.restores, 1)
                self.assertFalse((system.etc / "90-openstack-provider.yaml").exists())

    def test_repeat_does_not_run_another_trial(self):
        with tempfile.TemporaryDirectory() as root:
            system = FakeSystem(root)
            self.configure(system)
            self.configure(system)
            self.assertEqual(system.trials, 1)

    def test_netplan_serialization_defaults_are_accepted_but_dhcp_is_not(self):
        mac = network.NODES["node1"][2]
        config = {"match": {"macaddress": mac}, "optional": True}
        network.validate_effective_provider(config, mac)
        for key, value in (("dhcp4", True), ("accept-ra", True), ("link-local", ["ipv6"]),
                           ("addresses", ["192.168.0.151/24"])):
            with self.subTest(key=key), self.assertRaises(network.PreparationError):
                network.validate_effective_provider({**config, key: value}, mac)

    def test_existing_unowned_or_symlinked_file_is_never_overwritten(self):
        for symlink in (False, True):
            with self.subTest(symlink=symlink), tempfile.TemporaryDirectory() as root:
                system = FakeSystem(root)
                target = system.etc / "90-openstack-provider.yaml"
                if symlink: target.symlink_to(system.etc / "50-installer.yaml")
                else: target.write_text("# somebody else's file\n")
                with self.assertRaises(network.PreparationError): self.configure(system)
                self.assertFalse(system.backups.exists())


if __name__ == "__main__":
    unittest.main()

# Chapter H — Prepare the USB NICs

[Phase 1](README.md) · [Previous: SSH access](C-access-check.md) · [Next: Cinder disk](I-cinder-storage.md)

**Status: complete.** The full procedure remains visible for study or a rebuild. The reviewed result and lesson follow the steps.

**Purpose:** prepare each USB NIC for external VM traffic while preserving the built-in management connection.

## 1. The confirmed network

All six Ethernet connections share the ordinary mini switch and TP-Link home router. There is **no VLAN tagging**; the external provider network will use the **flat** type.

The built-in management NIC is `enp0s31f6` on all nodes, with `.200/24`, `.201/24` and `.202/24`. Their configured gateway and DNS are `192.168.0.1`.

| Node | USB NIC — name stays unchanged | MAC used to identify it |
|---|---|---|
| node1 | `enx00e04c557e58` | `00:e0:4c:55:7e:58` |
| node2 | `enx00e04c2c1678` | `00:e0:4c:2c:16:78` |
| node3 | `enx00e04c304688` | `00:e0:4c:30:46:88` |

All three USB NICs are now UP with physical link and no host addresses. Their names stay unchanged.

### Address plan for the later cloud installation

Your offered `.151–.250` range includes the nodes. Use this smaller initial plan:

| Purpose | Address/range | Status |
|---|---|---|
| Home DHCP | `192.168.0.2–150` | Confirmed |
| Shared OpenStack API address | `192.168.0.151` | Proposed |
| External router gateways and VM floating IPs | `192.168.0.152–199` | Proposed |
| Node management | `192.168.0.200–202` | Existing — excluded from cloud allocation |
| `.203–.250` | Outside the initial cloud pool | No assignment made here |

The API VIP is one shared API address for the controllers. These cloud addresses are planning values; this NIC step did not assign them.

## 2. What the script does

Run the [phase1-network.sh](../../scripts/phase1-network.sh) on R2d2. It sends the [phase1-network-node.py helper](../../scripts/phase1-network-node.py) through SSH and works on one node at a time.

| Action | Effect |
|---|---|
| Check node, NIC/MAC and management settings | Refuse an unexpected target or conflicting configuration |
| Back up existing Netplan files | Private copy under `/var/lib/openstack-from-zero/netplan/` on that node |
| Add `/etc/netplan/90-openstack-provider.yaml` | USB-only settings: MAC match, DHCP off, IPv6 autoconfiguration/link-local off, no host addresses |
| Validate, then run `netplan try --timeout 120` | Trial the settings with a timed confirmation |
| Verify the result | USB UP with physical link; management IP/routes/DNS preserved; a fresh SSH connection works |
| Save the combined report | `reports/` on R2d2 |

The USB name stays unchanged. Later Kolla inventory selects that actual name on each node. This step creates no bridge or VLAN.

The wrapper uses tools already on R2d2: Bash, Python 3 and OpenSSH. The helper uses the node's Netplan and its YAML library. It installs no packages.

## 3. Run on R2d2

Have node console access available during network changes.

The commands enter the course folder, download the latest files, and run the script. `--user joe` selects the node SSH account; `--apply` saves and trials the USB configuration.

```bash
cd ~/openstack-from-zero
git pull --ff-only
bash scripts/phase1-network.sh --user joe --apply
```

Expect these prompts:

1. SSH may request the node login password.
2. Sudo may request that node account's password.
3. **When Netplan asks whether to keep the settings, press Enter while the SSH session is responsive.** If it becomes unresponsive, let the trial expire.
4. A fresh SSH check may request the login password again. This proves access through a new connection.

The script stops on the first failed node. Completed nodes retain their settings.

## 4. Success check

The final line must say **`RESULT: USB_NETWORK_READY for all`**.

Each node must show:

- management IP, routes and DNS preserved;
- USB UP, physical link detected, no host addresses;
- `SSH_RECONNECT_OK`.

The script prints the combined report path and a `cat` command to read it. Reports stay out of Git. VM traffic forwarding is tested later with the first VM.

## Recorded result from this lab

Your report ends with **`RESULT: USB_NETWORK_READY for all`**.

| Node | USB link | Host addresses on USB | Management and fresh SSH |
|---|---|---|---|
| node1 | 1 Gb/s, full duplex | None | Passed |
| node2 | 1 Gb/s, full duplex | None | Passed |
| node3 | 1 Gb/s, full duplex | None | Passed |

All three Netplan trials were accepted. Management IPs, routes and DNS stayed intact. The settings are saved on disk; a reboot check and VM traffic forwarding remain later checks.

## 5. Short lecture — what your result means

A NIC can carry Ethernet frames without an IP assigned to the host. The built-in NIC is your host's management connection; the USB NIC will carry Neutron's external VM traffic.

Both cables still use the same LAN and switch. Two NICs give the traffic separate interfaces; they do not create separate physical networks.

Netplan stores the USB settings for future boots. Kolla later connects that NIC to OpenStack's external bridge. The reported 1 Gb/s is the physical link speed, not a measured VM throughput.

## 6. Preview, repeat runs and restoration

Without `--apply`, the script shows the candidate without changing node configuration. `--node node2` selects one node. A matching, working configuration skips another Netplan trial.

If generation fails, the script restores its fragment without activating the network. If the trial or post-check fails, it restores the prior fragment and attempts to restore runtime settings. The backup path is printed.

Netplan documents rollback caveats. If SSH is lost or restoration needs review, inspect the actual state from the node console and use its printed backup path before retrying.

Local fixture checks passed for configuration preservation, rejection, restoration, repeat runs and wrapper behavior. They do not substitute for the real Netplan and hardware checks in your run.

**Next:** [Chapter I — Inspect the Cinder disk](I-cinder-storage.md).

References: [Netplan configuration](https://netplan.readthedocs.io/en/stable/netplan-yaml/) · [Netplan timed trial and caveats](https://netplan.readthedocs.io/en/stable/netplan-try/) · [Kolla external NICs](https://docs.openstack.org/kolla-ansible/2026.1/reference/networking/neutron.html#network-interfaces).

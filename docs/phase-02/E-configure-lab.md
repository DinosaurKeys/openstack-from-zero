# Chapter E — Configure the lab

[Phase 2](README.md) · [Previous: create the inventory](D-create-inventory.md)

**Goal:** prepare the reviewed settings that Kolla will use during installation.

**Status: draft.** No configuration generator or apply command is ready yet.

## 1. Purpose

The inventory says **which machines** to use. Kolla's settings say **how to configure the cloud**: network interfaces, services, storage and access addresses.

## 2. Confirmed inputs

| Setting | Confirmed design |
|---|---|
| Host OS | Ubuntu 24.04 on the three nodes |
| Management interface | `enp0s31f6` on each node |
| node1 external interface | `enx00e04c557e58` |
| node2 external interface | `enx00e04c2c1678` |
| node3 external interface | `enx00e04c304688` |
| Physical external network | Same untagged LAN through the mini switch and TP-Link router |
| Cinder storage | Separate existing `cinder-volumes` VG on each SATA disk |
| Cinder clustering | Independent backends, empty cluster name; the specific cluster-check exception is approved |

The API VIP `.151` and future VM external allocation range `.152–.199` remain **proposals**. They must be confirmed and checked before assigning addresses. The home DHCP pool is `.2–.150`; nodes use `.200–.202`. We do not assume an address is unused simply because it is outside DHCP.

## 3. Planned steps

1. Review the remaining address and service choices with Joe.
2. Prepare release-compatible inventory host variables and Kolla settings using the confirmed inputs.
3. Explain each setting and any private files that will be created.
4. Preserve existing configuration; show changes before applying them on R2d2.
5. Generate required service credentials privately, following the installed release's procedure.
6. Review the configuration report. Keep passwords, private keys and cloud credentials out of GitHub and shared reports.

## 4. Script and commands

The configuration script will follow the agreed values and installed release. No VIP, NIC, bridge or OpenStack service is configured by this draft.

## 5. Finish check and lesson

Configuration matches the agreed lab and passes local review. The short lesson follows the actual result. Three independent LVM backends do not provide automatic replication.

[Next planned chapter: validate preparation](F-validate-preparation.md).

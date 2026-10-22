# Phase 1 — Prerequisites

[Home](../../README.md) · [Goal and plan](../00-goal-and-plan.md)

**Goal:** prepare Ubuntu and reliable management access, then prepare the correct NICs and Cinder disk.

![R2d2 management path and planned node roles](../images/lab-layout.svg)

## Read in this order

| Chapter | You will achieve |
|---|---|
| **[1.1 — Install R2d2](01-r2d2.md)** | Management server available through its VM console |
| [1.2 — Install the Ubuntu nodes](02-ubuntu-nodes.md) | Correct hostnames, management IPs and SSH service |
| [1.3 — Check access from R2d2](03-access-check.md) | One combined report with connectivity and node facts |
| [1.4 — Prepare the two NICs and Cinder storage](04-nics-and-storage.md) | Review the actual devices before their final configuration |

**Current action: Chapter 1.1.** Complete one chapter before moving to the next.

The first script inspects the machines and writes a private report. Permanent NIC configuration and Cinder volume-group creation follow the device review in Chapter 1.4. Those changes are not yet supplied as runnable commands because the new device/network details have not been observed.

## Phase 1 finish check

- [ ] R2d2 runs the selected Ubuntu version and reaches the nodes.
- [ ] node1–node3 have their intended Ubuntu version, names and management IPs.
- [ ] SSH login works from R2d2 to all three nodes.
- [ ] NIC identities and the LAN gateway, DNS and DHCP service are established.
- [ ] Permanent two-NIC settings work and preserve management access.
- [ ] The reviewed node3 SATA disk backs the Cinder volume group, separately from Ubuntu storage.

The inspection script covers connectivity and recorded facts. Its success does not complete the last three checkboxes.

**Next:** [1.1 — Install R2d2](01-r2d2.md).

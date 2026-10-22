# Phase 1 — Prerequisites

[Home](../../README.md) · [Goal and plan](../00-goal-and-plan.md)

**Goal:** prepare Ubuntu and reliable management access, then prepare the correct NICs and Cinder disk.

![Verified management access and planned OpenStack services](../images/lab-layout.svg)

## Chapters and progress

| Chapter | Status |
|---|---|
| [1.1 — Install R2d2](01-r2d2.md) | Complete |
| [1.2 — Install the Ubuntu nodes](02-ubuntu-nodes.md) | Ubuntu, hostnames and management IPs verified |
| [1.3 — Check access from R2d2](03-access-check.md) | Complete — SSH inspection passed on all three |
| **[1.4 — Prepare the two NICs and Cinder storage](04-nics-and-storage.md)** | **Current — review network inputs, then configure** |

Your inspection ends with **`NODE_IDENTITY_CHECKS_PASSED`**. The observed NIC map, gateway/DNS and node3 storage candidate are now recorded in Chapter 1.4.

## Phase 1 finish check

- [x] R2d2 runs Ubuntu 24.04 and reaches the nodes.
- [x] node1–node3 match Ubuntu 24.04, their hostnames and management IPs.
- [x] SSH login works from R2d2 as `joe` on all three.
- [x] NIC identities and configured gateway/DNS are recorded.
- [ ] USB cabling and the LAN DHCP device/pool are confirmed.
- [ ] Permanent provider-NIC configuration works and preserves management access.
- [ ] The reviewed node3 SATA disk backs the Cinder volume group, separately from Ubuntu.

**Next:** [1.4 — Prepare the two NICs and Cinder storage](04-nics-and-storage.md).

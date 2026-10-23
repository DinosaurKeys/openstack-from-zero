# Phase 1 — Prerequisites

[Home](../../README.md) · [Goal and plan](../00-goal-and-plan.md)

**Goal:** prepare Ubuntu and reliable management access, then prepare the correct NICs and Cinder disk.

![Verified management access and planned OpenStack services](../images/lab-layout.svg)

## Chapters and progress

Each chapter includes its purpose, script link, commands to run, expected result and short lesson. Completed procedures stay visible for study. For a rebuild, follow the chapters in order; for this existing lab, use the **Current** status.

| Chapter | Status | Steps and commands |
|---|---|---|
| [1.1 — Install R2d2](01-r2d2.md) | Complete | [Manual installation](01-r2d2.md#installation-steps) |
| [1.2 — Install the Ubuntu nodes](02-ubuntu-nodes.md) | Ubuntu, hostnames and management IPs verified | [Manual installation](02-ubuntu-nodes.md#steps) |
| [1.3 — Check access from R2d2](03-access-check.md) | Complete — SSH inspection passed on all three | [Run access check](03-access-check.md#2-run-the-access-check-on-r2d2) |
| [1.4 — Prepare the USB NICs](04-nics-and-storage.md) | Complete — all three links and fresh SSH passed | [Run NIC script](04-nics-and-storage.md#3-run-on-r2d2) |
| **[1.5 — Prepare Cinder storage](05-cinder-storage.md)** | **Current — inspect node3's SATA disk** | [Run storage inspection](05-cinder-storage.md#3-run-on-r2d2) |

Your network report ends with **`USB_NETWORK_READY for all`**. Read the short lesson in Chapter 1.4, then continue to the storage inspection in Chapter 1.5.

## Phase 1 finish check

- [x] R2d2 runs Ubuntu 24.04 and reaches the nodes.
- [x] node1–node3 match Ubuntu 24.04, their hostnames and management IPs.
- [x] SSH login works from R2d2 as `joe` on all three.
- [x] NIC identities and configured gateway/DNS are recorded.
- [x] Same untagged switch/LAN confirmed; TP-Link home DHCP pool is `.2–.150`.
- [x] Saved provider-NIC configuration works and preserves management access on all three nodes.
- [ ] The reviewed node3 SATA disk backs the Cinder volume group, separately from Ubuntu.

**Next:** [1.5 — Inspect the Cinder disk](05-cinder-storage.md).

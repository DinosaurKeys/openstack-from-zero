# Phase 1 — Prerequisites

[Home](../../README.md) · [Goal and plan](../00-goal-and-plan.md)

**Goal:** prepare Ubuntu and reliable management access, then prepare the correct NICs and Cinder disk.

![Verified management access and planned OpenStack services](../images/lab-layout.svg)

## Chapters and progress

Each chapter includes its purpose, script link, commands to run, expected result and short lesson. Completed procedures stay visible for study. For a rebuild, follow the chapters in order. For the existing lab, use the [current learning checkpoint](../../README.md#current-learning-step).

| Chapter | Status | Steps and commands |
|---|---|---|
| [Chapter A — Install R2d2](A-r2d2.md) | Complete | [Manual installation](A-r2d2.md#installation-steps) |
| [Chapter B — Install the Ubuntu nodes](B-ubuntu-nodes.md) | Ubuntu, hostnames and management IPs verified | [Manual installation](B-ubuntu-nodes.md#steps) |
| [Chapter C — Check access from R2d2](C-access-check.md) | Complete — SSH inspection passed on all three | [Run access check](C-access-check.md#2-run-the-access-check-on-r2d2) |
| [Chapter D — Check keys, time and host names](D-access-state.md) | Complete — actual state reviewed | [Read inspection and result](D-access-state.md) |
| [Chapter E — Create SSH keys and copy the public key](E-ssh-keys.md) | Complete — fresh key-only login passed on all three | [Setup, result and lesson](E-ssh-keys.md) |
| [Chapter F — Resolve node names on R2d2](F-r2d2-hosts.md) | Complete — all three node names resolve correctly | [Explain and run hosts script](F-r2d2-hosts.md) |
| [Chapter G — Prepare time synchronization](G-r2d2-chrony.md) | Reviewed — R2d2 and node clocks synchronized | [Explain and run Chrony script](G-r2d2-chrony.md) |
| [Chapter H — Prepare the USB NICs](H-nics-and-storage.md) | Complete — all three links and fresh SSH passed | [Run NIC script](H-nics-and-storage.md#3-run-on-r2d2) |
| [Chapter I — Prepare Cinder storage](I-cinder-storage.md) | Inspection complete — all three reviewed | [Run storage inspection](I-cinder-storage.md#3-run-on-r2d2) |
| [Chapter J — Create Cinder LVM pools](J-cinder-lvm.md) | Complete — three local pools created and verified | [Purpose, script and commands](J-cinder-lvm.md) |

The table records reviewed results for the existing lab. A rebuild must supply fresh access, clock, NIC and storage evidence before continuing.

## Phase 1 finish check

- [x] R2d2 runs Ubuntu 24.04 and reaches the nodes.
- [x] node1–node3 match Ubuntu 24.04, their hostnames and management IPs.
- [x] SSH login works from R2d2 as `joe` on all three.
- [x] Existing SSH-key, Chrony and hostname-resolution settings reviewed.
- [x] Dedicated R2d2 public key installed and fresh key-only login verified on all three nodes.
- [x] NIC identities and configured gateway/DNS are recorded.
- [x] Same untagged switch/LAN confirmed; TP-Link home DHCP pool is `.2–.150`.
- [x] Saved provider-NIC configuration works and preserves management access on all three nodes.
- [x] R2d2 resolves node1, node2 and node3 to their confirmed management IPs.
- [x] Chrony is installed, enabled and synchronized on R2d2 and all three nodes.
- [x] Current SATA identities, existing use and health evidence reviewed on all three nodes.
- [x] Approved SATA disks back the intended Cinder LVM pools, separately from Ubuntu.

**Next phase:** [Phase 2 — Management environment](../phase-02/README.md). The [main README](../../README.md#current-learning-step) holds the current learning checkpoint. For a rebuild, complete A–J and review the results before continuing.

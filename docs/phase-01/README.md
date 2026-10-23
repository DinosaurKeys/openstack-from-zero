# Phase 1 — Prerequisites

[Home](../../README.md) · [Goal and plan](../00-goal-and-plan.md)

**Goal:** prepare Ubuntu and reliable management access, then prepare the correct NICs and Cinder disk.

![Verified management access and planned OpenStack services](../images/lab-layout.svg)

## Chapters and progress

Each chapter includes its purpose, script link, commands to run, expected result and short lesson. Completed procedures stay visible for study. For a rebuild, follow the chapters in order; for this existing lab, use the **Current** status.

| Chapter | Status | Steps and commands |
|---|---|---|
| [Chapter A — Install R2d2](A-r2d2.md) | Complete | [Manual installation](A-r2d2.md#installation-steps) |
| [Chapter B — Install the Ubuntu nodes](B-ubuntu-nodes.md) | Ubuntu, hostnames and management IPs verified | [Manual installation](B-ubuntu-nodes.md#steps) |
| [Chapter C — Check access from R2d2](C-access-check.md) | Complete — SSH inspection passed on all three | [Run access check](C-access-check.md#2-run-the-access-check-on-r2d2) |
| [Chapter D — Check keys, time and host names](D-access-state.md) | Complete — actual state reviewed | [Read inspection and result](D-access-state.md) |
| [Chapter E — Create SSH keys and copy the public key](E-ssh-keys.md) | Complete — fresh key-only login passed on all three | [Setup, result and lesson](E-ssh-keys.md) |
| [Chapter F — Resolve node names on R2d2](F-r2d2-hosts.md) | Complete — all three node names resolve correctly | [Explain and run hosts script](F-r2d2-hosts.md) |
| [Chapter G — Install Chrony on R2d2](G-r2d2-chrony.md) | Complete — active, enabled and initially synchronized | [Explain and run Chrony script](G-r2d2-chrony.md) |
| [Chapter H — Prepare the USB NICs](H-nics-and-storage.md) | Complete — all three links and fresh SSH passed | [Run NIC script](H-nics-and-storage.md#3-run-on-r2d2) |
| [Chapter I — Prepare Cinder storage](I-cinder-storage.md) | Inspection complete — all three reviewed | [Run storage inspection](I-cinder-storage.md#3-run-on-r2d2) |
| [Chapter J — Create Cinder LVM pools](J-cinder-lvm.md) | Complete — three local pools created and verified | [Purpose, script and commands](J-cinder-lvm.md) |

Your network report ends with **`USB_NETWORK_READY for all`**. That checkpoint remains complete. The SSH/time/name inspection is also reviewed. SSH-key setup now ends with **`SSH_KEYS_READY for all`**. Its reviewed result and short lesson remain available. **Node-name setup is also complete:** [Chapter F — reviewed result and lesson](F-r2d2-hosts.md#5-reviewed-result). **Chrony on R2d2 is also complete:** [Chapter G — reviewed result and lesson](G-r2d2-chrony.md#6-reviewed-result). **SATA inspection is complete:** [Chapter I — reviewed result and lesson](I-cinder-storage.md#5-reviewed-inspection-result). **LVM pools are also complete:** [Chapter J — creation result and lesson](J-cinder-lvm.md#6-reviewed-creation-result).

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
- [x] Chrony is installed, enabled and synchronized on R2d2.
- [x] Current SATA identities, existing use and health evidence reviewed on all three nodes.
- [x] Approved SATA disks back the intended Cinder LVM pools, separately from Ubuntu.

**Latest completed checkpoint:** [Chapter J — three LVM pools created and verified](J-cinder-lvm.md#6-reviewed-creation-result). Read the lesson and use the optional read-only command to view a pool. Joe has approved starting **[Phase 2 — Management environment](../phase-02/README.md)**. Its **[Chapter A — Check R2d2](../phase-02/A-check-r2d2.md)** is the current read-only step; every Phase 1 procedure remains available above.

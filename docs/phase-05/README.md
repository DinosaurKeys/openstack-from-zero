# Phase 5 — HA validation and VM migration

[Home](../../README.md) · [Previous: Phase 4](../phase-04/README.md)

**Goal:** check that another service takes over when one stops, then move a running VM for planned maintenance.

Phase 4's VM networking and Cinder persistence reports passed. Reviewed live reports passed B (API VIP), C (MariaDB) and D (virtual-router HA), including restoration. Reboot service recovery also passed. **E covers live/cold VM moves and separate data-volume moves through one numbered menu.** Reviewed evidence records a confirmed cold move on 29 October and a completed live VM/OS-disk move from node3 to node2 on 30 October 2026. Guest SSH, the original filesystem UUID and saved test-file checksum were then verified. A subsequent attached-volume move from storage node3 to node2 passed API/host and final cluster checks, with the VM remaining on node2. The guest/file check after that storage move remains pending; uninterrupted application/network traffic is not established. Read current state and review each exact move before execution. Repeating a completed fault test requires fresh preparation and review.

| Chapter | Simple purpose | Status |
|---|---|---|
| [A — Readiness](A-readiness.md) | Check health, resources and where things run | Existing check commands ready |
| [B — API VIP](B-api-vip.md) | Keep the cloud's front-door address working | Complete — reviewed API VIP takeover and restoration passed |
| [C — MariaDB](C-mariadb.md) | Keep the database working with one member stopped | Complete — one-member stop, surviving cluster/API reads and three-member restoration passed |
| [D — Virtual router](D-router.md) | Keep the VM's network path working after takeover | Complete — router takeover, link restoration and final checks passed |
| [E — VM and storage migration](E-vm-migration.md) | Select → check → review → approve → verify | Cold move confirmed; live compute and attached-volume node3 → node2 API/host checks passed. Guest/file check after the storage move pending |

The reviewed 30 October report verifies the [Nova messaging workaround](nova-messaging-repair.md) on all six scheduler/conductor services. The subsequent live move passed. Keep this verified configuration; any later repair or migration requires its own checks and approval.

For each exercise:

1. Check the current healthy state.
2. Explain the exact target, action and restoration command; ask before interruption or migration.
3. Perform one exercise and measure any interruption.
4. Restore the stopped component and check health again.
5. Review the report before continuing.

**Restoration** means starting the component we stopped and checking it rejoins normally. It does not mean reinstalling OpenStack. Migration has its own completion/failure checks; moving the VM back is a separate decision.

No whole-node shutdowns in these first exercises. Ceph, storage HA and automatic VM recovery remain deferred. Local Cinder storage is not replicated, and planned migration does not prove recovery after a host failure.

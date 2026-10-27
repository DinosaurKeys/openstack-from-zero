# Phase 5 — HA validation and VM migration

[Home](../../README.md) · [Previous: Phase 4](../phase-04/README.md)

**Goal:** check that another service takes over when one stops, then move a running VM for planned maintenance.

Phase 4's VM networking and Cinder persistence reports passed. Reviewed live reports passed B (API VIP), C (MariaDB) and D (virtual-router HA), including restoration. D confirmed node3-to-node2 router takeover and healthy final guest traffic/data and host/API/database checks; one sampled network probe failed, so uninterrupted traffic is not claimed. E's live inspection passed and node2 was selected. Its migration script is locally tested: prepare and return the node1 → node2 plan before approving the move. Repeating a completed fault test requires fresh preparation and review.

| Chapter | Simple purpose | Status |
|---|---|---|
| [A — Readiness](A-readiness.md) | Check health, resources and where things run | Existing check commands ready |
| [B — API VIP](B-api-vip.md) | Keep the cloud's front-door address working | Complete — reviewed API VIP takeover and restoration passed |
| [C — MariaDB](C-mariadb.md) | Keep the database working with one member stopped | Complete — one-member stop, surviving cluster/API reads and three-member restoration passed |
| [D — Virtual router](D-router.md) | Keep the VM's network path working after takeover | Complete — router takeover, link restoration and final checks passed |
| [E — VM migration](E-vm-migration.md) | Move a VM between healthy compute nodes | Inspection passed; node2 selected; prepare/apply script ready; live migration pending |

For each exercise:

1. Check the current healthy state.
2. Explain the exact target, action and restoration command; ask before interruption or migration.
3. Perform one exercise and measure any interruption.
4. Restore the stopped component and check health again.
5. Review the report before continuing.

**Restoration** means starting the component we stopped and checking it rejoins normally. It does not mean reinstalling OpenStack. Migration has its own completion/failure checks; moving the VM back is a separate decision.

No whole-node shutdowns in these first exercises. Ceph, storage HA and automatic VM recovery remain deferred. Local Cinder storage is not replicated, and planned migration does not prove recovery after a host failure.

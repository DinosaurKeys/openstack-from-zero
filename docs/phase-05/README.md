# Phase 5 — HA validation and VM migration

[Home](../../README.md) · [Previous: Phase 4](../phase-04/README.md)

**Goal:** check that another service takes over when one stops, then move a running VM for planned maintenance.

Phase 4's VM networking and Cinder persistence reports passed. Phase 5 tests have **not** run. Start with A. B has a locally tested prepare/apply/restore script; prepare and review its current target before approving a stop. C–E remain short test plans; their exact commands follow the preceding reviewed reports.

| Chapter | Simple purpose | Status |
|---|---|---|
| [A — Readiness](A-readiness.md) | Check health, resources and where things run | Existing check commands ready |
| [B — API VIP](B-api-vip.md) | Keep the cloud's front-door address working | Script ready; live result pending |
| [C — MariaDB](C-mariadb.md) | Keep the database working with one member stopped | Test plan |
| [D — Virtual router](D-router.md) | Keep the VM's network path working after takeover | Test plan |
| [E — VM migration](E-vm-migration.md) | Move a VM between healthy compute nodes | Test plan |

For each exercise:

1. Check the current healthy state.
2. Explain the exact target, action and restoration command; ask before interruption or migration.
3. Perform one exercise and measure any interruption.
4. Restore the stopped component and check health again.
5. Review the report before continuing.

**Restoration** means starting the component we stopped and checking it rejoins normally. It does not mean reinstalling OpenStack. Migration has its own completion/failure checks; moving the VM back is a separate decision.

No whole-node shutdowns in these first exercises. Ceph, storage HA and automatic VM recovery remain deferred. Local Cinder storage is not replicated, and planned migration does not prove recovery after a host failure.

# Phase 3 — Install OpenStack

[Home](../../README.md) · [Phase 2](../phase-02/README.md)

**Status: draft plan for review. No Phase 3 commands are ready to run.**

**Goal:** prepare the three hosts, check deployment requirements, install OpenStack and verify access.

| Chapter | Purpose | Status |
|---|---|---|
| [A — Prepare the nodes](A-prepare-nodes.md) | Review and then prepare the host dependencies Kolla needs | Draft for review |
| B — Full Kolla prechecks | Check the prepared hosts before deploying services | Planned |
| C — Deploy OpenStack | Deploy the agreed services using the existing private configuration | Planned |
| D — Verify cloud access | Check services, live HTTPS and the Horizon login page | Planned |

Each chapter follows **purpose → explain the script → publish → Joe pulls and runs → review one report → short lecture**. Letters restart at A in this phase.

The selected versions, three-node roles, NIC mappings, independent Cinder LVM pools and HTTPS plan stay as agreed in Phase 2. We do not recreate the disks, passwords or certificates.

**Current action:** Phase 2 and the chosen Windows Chrome/Linux Firefox imports are complete (browser imports user-confirmed). Review Chapter A's scope before its runnable implementation. Publishing this draft does not start bootstrap or deployment.

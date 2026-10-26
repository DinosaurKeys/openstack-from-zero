# Phase 3 — Install OpenStack

[Home](../../README.md) · [Phase 2](../phase-02/README.md)

**Current: Chapters A and B are complete and their live reports are reviewed.** [Chapter C](C-deploy-openstack.md) is prepared for review: one wrapper runs fresh prechecks, standard Kolla deployment and deployment observations. Joe's live deployment result is pending; Chapter D remains planned.

**Goal:** prepare the three hosts, check deployment requirements, install OpenStack and verify access.

| Chapter | Purpose | Status |
|---|---|---|
| [A — Prepare the nodes](A-prepare-nodes.md) | Review and then prepare the host dependencies Kolla needs | Complete — all three nodes passed |
| [B — Full Kolla prechecks](B-full-prechecks.md) | Check the prepared hosts before deploying services | Complete — all three nodes passed |
| [C — Deploy OpenStack](C-deploy-openstack.md) | Deploy the agreed services using the existing private configuration | Prepared for review — live deployment pending |
| D — Verify cloud access | Check services, live HTTPS and the Horizon login page | Planned |

Each chapter follows **purpose → explain the script → publish → Joe pulls and runs → review one report → short lecture**. Letters restart at A in this phase.

The selected versions, three-node roles, NIC mappings, independent Cinder LVM pools and HTTPS plan stay as agreed in Phase 2. We do not recreate the disks, passwords or certificates.

**Current action:** read [Chapter C's scope, script explanation and run procedure](C-deploy-openstack.md). Bootstrap and full prechecks passed on all three nodes; the [Chapter A](A-prepare-nodes.md) and [Chapter B](B-full-prechecks.md) instructions and lessons remain available for rebuilding. No OpenStack deployment or live Horizon HTTPS result is claimed yet.

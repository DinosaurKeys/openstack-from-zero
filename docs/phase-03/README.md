# Phase 3 — Install OpenStack

[Home](../../README.md) · [Phase 2](../phase-02/README.md)

**Current: Chapters A, B and C are complete.** Joe's live deployment observations passed on all three nodes. [Chapter D](D-client-access.md) is prepared for local client files, HTTPS/API verification and a separate browser login checkpoint.

**Goal:** prepare the three hosts, check deployment requirements, install OpenStack and verify access.

| Chapter | Purpose | Status |
|---|---|---|
| [A — Prepare the nodes](A-prepare-nodes.md) | Review and then prepare the host dependencies Kolla needs | Complete — all three nodes passed |
| [B — Full Kolla prechecks](B-full-prechecks.md) | Check the prepared hosts before deploying services | Complete — all three nodes passed |
| [C — Deploy OpenStack](C-deploy-openstack.md) | Deploy the agreed services using the existing private configuration | Complete — live observations passed on all three nodes |
| [D — Client access and API verification](D-client-access.md) | Prepare local credentials, check HTTPS/APIs and sign in to Horizon | Published — live API result and browser login pending |

Each chapter follows **purpose → explain the script → publish → Joe pulls and runs → review one report → short lecture**. Letters restart at A in this phase.

The selected versions, three-node roles, NIC mappings, independent Cinder LVM pools and HTTPS plan stay as agreed in Phase 2. We do not recreate the disks, passwords or certificates.

**Current action:** follow [Chapter D](D-client-access.md). All earlier procedures remain available for rebuilding. Live API access and Horizon login are not yet confirmed.

**Next phase:** document and test the virtual networks that use `br-ex` and the USB NICs: external/provider network, tenant network/subnet, virtual router, floating IP and the actual VM-to-LAN packet path. VM, volume and failover tests remain pending.
